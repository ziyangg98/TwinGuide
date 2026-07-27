"""连接梁策略分派以及 TwinGuideMerge 独立 Bézier 算法。"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from twin_guide.config import ConnectorMode
from twin_guide.geometry import Vec3
from twin_guide.point_linking import (
    PointLink,
    PointLinkingConfig,
    PointLinkingPlan,
    link_selected_points,
)
from twin_guide.template_link_points import TemplateLinkPointPlan
from twin_guide.types import ConnectorEndpointSource

if TYPE_CHECKING:
    from twin_guide.guide_component_bridge import GuideComponentBridgePlan
    from twin_guide.guide_terminal_u_extension import GuideTerminalUExtensionPlan
    from twin_guide.press_beam_points import PressBeamPointPlan

LEGACY_HANDLE_FACTOR = 3.0
LEGACY_CURVE_RESOLUTION = 24


def _bezier_centerline(
    start: Vec3,
    end: Vec3,
    start_normal: Vec3,
    end_normal: Vec3,
    radius_mm: float,
) -> tuple[Vec3, ...]:
    """按 TwinGuideMerge 控制柄规则采样一条三次 Bézier 中心线。"""

    distance = start.distance_to(end)
    handle = min(distance / 3.0, LEGACY_HANDLE_FACTOR * radius_mm)
    first_control = start + start_normal.normalized() * handle
    template_normal = end_normal.normalized()
    if template_normal.dot(start - end) < 0.0:
        template_normal = -template_normal
    second_control = end + template_normal * handle
    sample_count = max(16, int(distance / max(0.35 * radius_mm, 0.15)) + 1)
    return tuple(
        start * ((1.0 - fraction) ** 3)
        + first_control * (3.0 * ((1.0 - fraction) ** 2) * fraction)
        + second_control * (3.0 * (1.0 - fraction) * (fraction**2))
        + end * (fraction**3)
        for fraction in (
            index / (sample_count - 1) for index in range(sample_count)
        )
    )


def _independent_bezier_links(
    points: TemplateLinkPointPlan,
    radius_mm: float,
) -> tuple[PointLink, ...]:
    """将每个导管上下真实外壁 Q 点分别连接到左右导板锚点。"""

    if points.template_points.multi_site_paths:
        raise ValueError(
            "independent_bezier 不支持跨多种植位连续路径；"
            "请使用 continuous_frame"
        )
    links = []
    for sleeve, template in zip(
        points.sleeve_anchors.selections,
        points.template_points.selections,
        strict=True,
    ):
        if not template.feasible:
            raise ValueError(f"导管 {sleeve.guide_index} 的导板侧锚点不可行")
        for sleeve_label, sleeve_point in (
            ("lower", sleeve.lower),
            ("upper", sleeve.upper),
        ):
            for template_label, template_point in (
                ("left", template.left),
                ("right", template.right),
            ):
                centerline = _bezier_centerline(
                    sleeve_point.surface_contact,
                    template_point.position,
                    sleeve.radial_direction,
                    template_point.normal,
                    radius_mm,
                )
                links.append(
                    PointLink(
                        guide_index=sleeve.guide_index,
                        sleeve_label=sleeve_label,
                        left_surface_anchor=sleeve_point.surface_contact,
                        right_surface_anchor=template_point.position,
                        left_surface_normal=sleeve_point.surface_normal,
                        right_surface_normal=template_point.normal,
                        start=sleeve_point.surface_contact,
                        tube_contact=sleeve_point.surface_contact,
                        end=template_point.position,
                        centerline=centerline,
                        contact_index=0,
                        left_source=ConnectorEndpointSource.SLEEVE,
                        right_source=ConnectorEndpointSource.TEMPLATE,
                        guide_indices=(sleeve.guide_index,),
                        tube_contacts=(sleeve_point.surface_contact,),
                        contact_indices=(0,),
                        link_label=f"{sleeve_label}_{template_label}",
                    )
                )
    return tuple(links)


def build_point_linking_plan(
    mode: ConnectorMode,
    points: TemplateLinkPointPlan,
    config: PointLinkingConfig,
    press_beam_points: PressBeamPointPlan | None = None,
    guide_component_bridge: GuideComponentBridgePlan | None = None,
    guide_terminal_u_extension: GuideTerminalUExtensionPlan | None = None,
) -> PointLinkingPlan:
    """运行当前连续梁算法，或用 Merge 独立 Bézier 主梁替换其主梁部分。"""

    plan = link_selected_points(
        points,
        config,
        press_beam_points,
        guide_component_bridge,
        guide_terminal_u_extension,
    )
    if mode is ConnectorMode.CONTINUOUS_FRAME:
        return plan
    return replace(
        plan,
        links=_independent_bezier_links(points, config.radius_mm),
        curve_resolution=LEGACY_CURVE_RESOLUTION,
        connection_type=ConnectorMode.INDEPENDENT_BEZIER.value,
        connector_guide_endpoint=None,
        terminal_distal_common_node=None,
        trim_against_dentition=False,
    )

"""第 6 步：以连续五次 Hermite 曲线生成导套—导板梁架中心线。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from twin_guide.config import PressBeamGuideEndpointParameters
from twin_guide.geometry import Vec3
from twin_guide.template_link_points import TemplateLinkPointPlan
from twin_guide.types import ConnectorEndpointSource

if TYPE_CHECKING:
    from twin_guide.guide_component_bridge import GuideComponentBridgePlan
    from twin_guide.guide_terminal_u_extension import GuideTerminalUExtensionPlan
    from twin_guide.press_beam_points import PressBeamPointPlan
    from twin_guide.terminal_distal_common_node import TerminalDistalCommonNodePlan


@dataclass(frozen=True, slots=True)
class PointLinkingConfig:
    """第 6 步连续梁中心线与实体化参数。"""

    radius_mm: float
    curve_resolution: int = 64
    recut_sleeve_bore: bool = True
    endpoint_tension: float = 0.45
    contact_tension: float = 0.90
    lower_approach_overlap_mm: float = 1.45
    lower_dive_merge_arc_mm: float = 5.0
    centerline_spacing_mm: float = 0.30
    connector_guide_endpoint: PressBeamGuideEndpointParameters = (
        PressBeamGuideEndpointParameters()
    )

    def __post_init__(self) -> None:
        """校验梁半径、嵌入量、张力和离散参数。"""

        if self.radius_mm <= 0.0:
            raise ValueError("连接梁半径必须为正")
        if self.curve_resolution < 8:
            raise ValueError("连接梁截面细分数不得小于 8")
        if min(self.endpoint_tension, self.contact_tension) <= 0.0:
            raise ValueError("Hermite 曲线张力必须为正")
        if not 0.0 <= self.lower_approach_overlap_mm < 2.0 * self.radius_mm:
            raise ValueError("低梁外层接近嵌入量必须位于 [0, 梁直径) 内")
        if min(self.lower_dive_merge_arc_mm, self.centerline_spacing_mm) <= 0.0:
            raise ValueError("低梁下潜合并弧长和中心线采样间距必须为正")


@dataclass(frozen=True, slots=True)
class PointLink:
    """一根从导板左锚点经导管 P 点到右锚点的连续梁。"""

    guide_index: int
    sleeve_label: str
    left_surface_anchor: Vec3
    right_surface_anchor: Vec3
    left_surface_normal: Vec3
    right_surface_normal: Vec3
    start: Vec3
    tube_contact: Vec3
    end: Vec3
    centerline: tuple[Vec3, ...]
    contact_index: int
    left_source: ConnectorEndpointSource = ConnectorEndpointSource.TEMPLATE
    right_source: ConnectorEndpointSource = ConnectorEndpointSource.TEMPLATE
    guide_indices: tuple[int, ...] = ()
    tube_contacts: tuple[Vec3, ...] = ()
    contact_indices: tuple[int, ...] = ()
    link_label: str = ""


@dataclass(frozen=True, slots=True)
class PressBeamLink:
    """一根从 Y 汇合点通向导管或导板锚点的直线按压梁。"""

    label: str
    source: str
    surface_anchor: Vec3
    surface_normal: Vec3
    start: Vec3
    end: Vec3
    centerline: tuple[Vec3, Vec3]


@dataclass(frozen=True, slots=True)
class PointLinkingPlan:
    """第 6 步输出的四根连续梁及其扫掠参数。"""

    links: tuple[PointLink, ...]
    radius_mm: float
    curve_resolution: int
    recut_sleeve_bore: bool
    anchor_trajectories: tuple[tuple[Vec3, ...], ...] = ()
    connection_type: str = "continuous_sleeve_frame"
    press_beam_links_included: bool = False
    press_beam_links: tuple[PressBeamLink, ...] = ()
    press_beam_junction: Vec3 | None = None
    press_beam_radius_mm: float | None = None
    press_beam_junction_radius_factor: float = 1.12
    press_beam_trajectories: tuple[tuple[Vec3, ...], ...] = ()
    press_beam_guide_endpoint: PressBeamGuideEndpointParameters | None = None
    connector_guide_endpoint: PressBeamGuideEndpointParameters | None = None
    guide_component_bridge: GuideComponentBridgePlan | None = None
    guide_terminal_u_extension: GuideTerminalUExtensionPlan | None = None
    terminal_distal_common_node: TerminalDistalCommonNodePlan | None = None
    trim_against_dentition: bool = True


def _projected_direction(vector: Vec3, normal: Vec3) -> Vec3:
    """返回向量在给定切平面内的稳定单位投影。"""

    unit_normal = normal.normalized()
    projected = vector - unit_normal * vector.dot(unit_normal)
    if projected.length > 1e-8:
        return projected.normalized()
    return vector.normalized()


def _quintic_segment(
    start: Vec3,
    end: Vec3,
    start_tangent: Vec3,
    end_tangent: Vec3,
    sample_count: int,
) -> tuple[Vec3, ...]:
    """以零端点二阶导数的五次 Hermite 基函数采样一段曲线。"""

    samples = []
    for index in range(sample_count):
        u = index / (sample_count - 1)
        u2 = u * u
        u3 = u2 * u
        u4 = u3 * u
        u5 = u4 * u
        h_start = 1.0 - 10.0 * u3 + 15.0 * u4 - 6.0 * u5
        h_start_tangent = u - 6.0 * u3 + 8.0 * u4 - 3.0 * u5
        h_end = 10.0 * u3 - 15.0 * u4 + 6.0 * u5
        h_end_tangent = -4.0 * u3 + 7.0 * u4 - 3.0 * u5
        samples.append(
            start * h_start
            + start_tangent * h_start_tangent
            + end * h_end
            + end_tangent * h_end_tangent
        )
    return tuple(samples)


def _segment_sample_count(start: Vec3, end: Vec3, spacing_mm: float) -> int:
    """按弦长返回不小于 17 的奇数采样数量。"""

    count = max(17, int(start.distance_to(end) / spacing_mm) + 1)
    return count if count % 2 else count + 1


def _curve_through_contact(
    left: Vec3,
    contact: Vec3,
    right: Vec3,
    left_normal: Vec3,
    contact_normal: Vec3,
    right_normal: Vec3,
    config: PointLinkingConfig,
) -> tuple[tuple[Vec3, ...], int]:
    """构造两段在接触点共享一阶导数的五次 Hermite 曲线。"""

    left_length = left.distance_to(contact)
    right_length = contact.distance_to(right)
    left_tangent = (
        _projected_direction(contact - left, left_normal)
        * left_length
        * config.endpoint_tension
    )
    contact_tangent = (
        _projected_direction(right - left, contact_normal)
        * (left_length + right_length)
        * 0.5
        * config.contact_tension
    )
    right_tangent = (
        _projected_direction(right - contact, right_normal)
        * right_length
        * config.endpoint_tension
    )
    left_curve = _quintic_segment(
        left,
        contact,
        left_tangent,
        contact_tangent,
        _segment_sample_count(left, contact, config.centerline_spacing_mm),
    )
    right_curve = _quintic_segment(
        contact,
        right,
        contact_tangent,
        right_tangent,
        _segment_sample_count(contact, right, config.centerline_spacing_mm),
    )
    return left_curve + right_curve[1:], len(left_curve) - 1


def _curve_through_multiple_contacts(
    start: Vec3,
    contacts: tuple[Vec3, ...],
    end: Vec3,
    start_normal: Vec3,
    contact_normals: tuple[Vec3, ...],
    end_normal: Vec3,
    config: PointLinkingConfig,
) -> tuple[tuple[Vec3, ...], tuple[int, ...]]:
    """构建一条依次穿过两处或更多导管锚点的光顺路径。"""

    points = (start, *contacts, end)
    normals = (start_normal, *contact_normals, end_normal)
    tangents = []
    for index, (point, normal) in enumerate(zip(points, normals, strict=True)):
        if index == 0:
            direction = points[1] - point
            scale = direction.length * config.endpoint_tension
        elif index == len(points) - 1:
            direction = point - points[index - 1]
            scale = direction.length * config.endpoint_tension
        else:
            direction = points[index + 1] - points[index - 1]
            scale = (
                min(
                    point.distance_to(points[index - 1]),
                    point.distance_to(points[index + 1]),
                )
                * config.contact_tension
            )
        tangents.append(_projected_direction(direction, normal) * scale)

    centerline: tuple[Vec3, ...] = ()
    waypoint_indices = [0]
    for index in range(len(points) - 1):
        segment = _quintic_segment(
            points[index],
            points[index + 1],
            tangents[index],
            tangents[index + 1],
            _segment_sample_count(
                points[index], points[index + 1], config.centerline_spacing_mm
            ),
        )
        centerline = centerline + (segment if not centerline else segment[1:])
        waypoint_indices.append(len(centerline) - 1)
    return centerline, tuple(waypoint_indices[1:-1])


def _cumulative_lengths(points: tuple[Vec3, ...]) -> tuple[float, ...]:
    """返回折线各采样点的累计弧长。"""

    lengths = [0.0]
    for previous, point in zip(points[:-1], points[1:], strict=True):
        lengths.append(lengths[-1] + previous.distance_to(point))
    return tuple(lengths)


def _lower_curve_with_local_dive(
    left: Vec3,
    deep_contact: Vec3,
    surface_contact: Vec3,
    contact_normal: Vec3,
    right: Vec3,
    left_normal: Vec3,
    right_normal: Vec3,
    config: PointLinkingConfig,
) -> tuple[tuple[Vec3, ...], int]:
    """先建立外层路线，再在导管附近局部下潜到低端深埋 P 点。"""

    outer_contact = surface_contact + contact_normal.normalized() * (
        config.radius_mm - config.lower_approach_overlap_mm
    )
    base_curve, outer_index = _curve_through_contact(
        left,
        outer_contact,
        right,
        left_normal,
        contact_normal,
        right_normal,
        config,
    )
    arc_lengths = _cumulative_lengths(base_curve)
    middle_arc = arc_lengths[outer_index]
    merge_arc = min(
        config.lower_dive_merge_arc_mm,
        middle_arc * 0.8,
        (arc_lengths[-1] - middle_arc) * 0.8,
    )
    left_index = max(
        index for index, arc in enumerate(arc_lengths[: outer_index + 1])
        if arc <= middle_arc - merge_arc
    )
    right_index = next(
        index for index, arc in enumerate(arc_lengths[outer_index:], outer_index)
        if arc >= middle_arc + merge_arc
    )
    left_merge = base_curve[left_index]
    right_merge = base_curve[right_index]
    left_approach_direction = _projected_direction(
        left_merge - base_curve[max(left_index - 1, 0)],
        contact_normal,
    )
    right_approach_direction = _projected_direction(
        base_curve[min(right_index + 1, len(base_curve) - 1)] - right_merge,
        contact_normal,
    )
    deep_contact_direction = _projected_direction(
        right_merge - left_merge, contact_normal
    )
    left_tangent = left_approach_direction * (
        left_merge.distance_to(deep_contact) * config.endpoint_tension
    )
    contact_tangent = deep_contact_direction * (
        (left_merge.distance_to(deep_contact) + deep_contact.distance_to(right_merge))
        * 0.5
        * config.contact_tension
    )
    right_tangent = right_approach_direction * (
        deep_contact.distance_to(right_merge) * config.endpoint_tension
    )
    dive_left = _quintic_segment(
        left_merge,
        deep_contact,
        left_tangent,
        contact_tangent,
        _segment_sample_count(left_merge, deep_contact, config.centerline_spacing_mm),
    )
    dive_right = _quintic_segment(
        deep_contact,
        right_merge,
        contact_tangent,
        right_tangent,
        _segment_sample_count(deep_contact, right_merge, config.centerline_spacing_mm),
    )
    prefix = base_curve[:left_index]
    suffix = base_curve[right_index + 1 :]
    centerline = prefix + dive_left + dive_right[1:] + suffix
    contact_index = len(prefix) + len(dive_left) - 1
    return centerline, contact_index


def link_selected_points(
    points: TemplateLinkPointPlan,
    config: PointLinkingConfig,
    press_beam_points: PressBeamPointPlan | None = None,
    guide_component_bridge: GuideComponentBridgePlan | None = None,
    guide_terminal_u_extension: GuideTerminalUExtensionPlan | None = None,
) -> PointLinkingPlan:
    """按当前 Q/P 与导板 A 点生成每导管上下两根连续梁。

    参数:
        points: 当前 TwinGuide 已选择的导管 Q/P 与导板左右 A 点。
        config: 连续曲线、导板端嵌入量和实体化配置。
        press_beam_points: 可选的内侧导管高端三锚点 Y 型按压梁计划。

    返回:
        每个导管含上下两根连续中心线的梁架计划。

    导板表面点 A 同时作为扫掠中心线端点，不再沿法向外移到 S。
    梁体进入牙体保护空间的部分由实体化阶段在与导板融合前裁掉。
    高梁直接以两段
    五次 Hermite 曲线经过当前高端 P；低梁先经过外层代理点，再在 P
    附近用短 Hermite 段完成局部下潜，允许梁柱埋入闭合孔端内部。
    """

    links: list[PointLink] = []
    if points.template_points.multi_site_paths:
        sleeve_by_index = {
            selection.guide_index: selection
            for selection in points.sleeve_anchors.selections
        }
        for path in points.template_points.multi_site_paths:
            ordered_sleeves = tuple(
                sleeve_by_index[guide_index] for guide_index in path.guide_indices
            )
            start_centerline = path.start_centerline_anchor or path.start.position
            end_centerline = path.end_centerline_anchor or path.end.position
            for sleeve_label in ("lower", "upper"):
                sleeve_points = tuple(
                    getattr(selection, sleeve_label) for selection in ordered_sleeves
                )
                centerline, contact_indices = _curve_through_multiple_contacts(
                    start_centerline,
                    tuple(anchor.position for anchor in sleeve_points),
                    end_centerline,
                    path.start.normal,
                    tuple(anchor.surface_normal for anchor in sleeve_points),
                    path.end.normal,
                    config,
                )
                links.append(
                    PointLink(
                        path.guide_indices[0],
                        sleeve_label,
                        path.start.position,
                        path.end.position,
                        path.start.normal,
                        path.end.normal,
                        start_centerline,
                        sleeve_points[0].position,
                        end_centerline,
                        centerline,
                        contact_indices[0],
                        path.start_source,
                        path.end_source,
                        guide_indices=path.guide_indices,
                        tube_contacts=tuple(
                            anchor.position for anchor in sleeve_points
                        ),
                        contact_indices=contact_indices,
                    )
                )
    else:
        links = []
    for sleeve, template in (() if points.template_points.multi_site_paths else zip(
        points.sleeve_anchors.selections,
        points.template_points.selections,
        strict=True,
    )):
        if not template.feasible:
            raise ValueError(f"导套 {sleeve.guide_index} 的牙科导板侧锚点不可行")
        left_surface = template.left.position
        right_surface = template.right.position
        left_start = template.left_centerline_anchor or left_surface
        right_end = template.right_centerline_anchor or right_surface
        for sleeve_label, sleeve_point in (
            ("lower", sleeve.lower),
            ("upper", sleeve.upper),
        ):
            if sleeve_label == "lower":
                centerline, contact_index = _lower_curve_with_local_dive(
                    left_start,
                    sleeve_point.position,
                    sleeve_point.surface_contact,
                    sleeve_point.surface_normal,
                    right_end,
                    template.left.normal,
                    template.right.normal,
                    config,
                )
            else:
                centerline, contact_index = _curve_through_contact(
                    left_start,
                    sleeve_point.position,
                    right_end,
                    template.left.normal,
                    sleeve_point.surface_normal,
                    template.right.normal,
                    config,
                )
            links.append(
                PointLink(
                    sleeve.guide_index,
                    sleeve_label,
                    left_surface,
                    right_surface,
                    template.left.normal,
                    template.right.normal,
                    left_start,
                    sleeve_point.position,
                    right_end,
                    centerline,
                    contact_index,
                    template.left_source,
                    template.right_source,
                )
            )
    press_links: tuple[PressBeamLink, ...] = ()
    press_junction = None
    press_radius = None
    press_junction_factor = 1.12
    press_trajectories: tuple[tuple[Vec3, ...], ...] = ()
    press_guide_endpoint = None
    if press_beam_points is not None:
        press_junction = press_beam_points.junction
        press_radius = press_beam_points.radius_mm
        press_junction_factor = press_beam_points.junction_radius_factor
        press_trajectories = press_beam_points.trajectories
        press_guide_endpoint = press_beam_points.guide_endpoint
        sleeve = press_beam_points.sleeve_anchor
        sleeve_links = () if sleeve is None else (
            PressBeamLink(
                f"inner_sleeve_{sleeve.guide_index}_upper",
                "inner_sleeve_upper",
                sleeve.surface_contact,
                sleeve.surface_normal,
                sleeve.centerline_anchor,
                press_junction,
                (sleeve.centerline_anchor, press_junction),
            ),
        )
        extension = press_beam_points.extension_anchor
        extension_links = () if extension is None else (
            PressBeamLink(
                f"terminal_u_{extension.segment}_farthest",
                "guide_terminal_u_extension",
                extension.surface_contact,
                extension.surface_normal,
                extension.centerline_anchor,
                press_junction,
                (extension.centerline_anchor, press_junction),
            ),
        )
        press_links = (
            *sleeve_links,
            *extension_links,
            *(
                PressBeamLink(
                    "tooth_" + "_".join(str(fdi) for fdi in anchor.station_fdis),
                    "tooth_section_trajectory",
                    anchor.surface_anchor,
                    anchor.surface_normal,
                    anchor.centerline_anchor,
                    press_junction,
                    (anchor.centerline_anchor, press_junction),
                )
                for anchor in press_beam_points.guide_anchors
            ),
        )
    return PointLinkingPlan(
        tuple(links),
        config.radius_mm,
        config.curve_resolution,
        config.recut_sleeve_bore,
        points.template_points.trajectories,
        press_beam_links_included=bool(press_links),
        press_beam_links=press_links,
        press_beam_junction=press_junction,
        press_beam_radius_mm=press_radius,
        press_beam_junction_radius_factor=press_junction_factor,
        press_beam_trajectories=press_trajectories,
        press_beam_guide_endpoint=press_guide_endpoint,
        connector_guide_endpoint=config.connector_guide_endpoint,
        guide_component_bridge=guide_component_bridge,
        guide_terminal_u_extension=guide_terminal_u_extension,
        terminal_distal_common_node=points.template_points.terminal_distal_common_node,
    )

"""为断裂成两个主体分量的导板规划同侧双梁预连接。"""

from __future__ import annotations

import math
from dataclasses import dataclass

import trimesh

from twin_guide.config import PressBeamGuideEndpointParameters
from twin_guide.errors import GeometryError
from twin_guide.geometry import Vec3
from twin_guide.tooth_section_anchors import select_tooth_section_local_anchor_pairs
from twin_guide.types import GenerationContext


@dataclass(frozen=True, slots=True)
class GuideComponentBridgeLink:
    """一根连接两个导板主体分量同一牙弓侧锚点的直梁。"""

    label: str
    side: str
    start_surface_anchor: Vec3
    end_surface_anchor: Vec3
    start_surface_normal: Vec3
    end_surface_normal: Vec3
    centerline: tuple[Vec3, ...]
    start_component_rank: int
    end_component_rank: int


@dataclass(frozen=True, slots=True)
class GuideComponentBridgePlan:
    """断裂导板预连接的两根梁、射线轨迹与实体化参数。"""

    links: tuple[GuideComponentBridgeLink, GuideComponentBridgeLink]
    radius_mm: float
    dental_clearance_mm: float
    endpoint_reinforcement: PressBeamGuideEndpointParameters | None
    trajectories: tuple[tuple[Vec3, ...], ...]
    meaningful_component_areas_mm2: tuple[float, ...]


def _load_meaningful_components(
    path: object,
) -> tuple[tuple[trimesh.Trimesh, ...], tuple[float, ...]]:
    """返回按面积排序的导板主体分量；极小退化碎片不计入。"""

    loaded = trimesh.load(path, force="mesh", process=False)
    if isinstance(loaded, trimesh.Scene):
        loaded = loaded.dump(concatenate=True)
    if not isinstance(loaded, trimesh.Trimesh) or loaded.is_empty:
        raise GeometryError(f"导板预连接输入网格为空：{path}")
    mesh = loaded.copy()
    mesh.merge_vertices()
    mesh.remove_unreferenced_vertices()
    mesh.fix_normals()
    ranked = sorted(
        ((component, float(component.area)) for component in mesh.split(only_watertight=False)),
        key=lambda item: item[1],
        reverse=True,
    )
    if not ranked:
        raise GeometryError("导板预连接无法识别连通分量")
    minimum_area = max(1.0, ranked[0][1] * 0.01)
    meaningful = tuple(item for item in ranked if item[1] >= minimum_area)
    return (
        tuple(component for component, _ in meaningful),
        tuple(area for _, area in meaningful),
    )


def _straight_centerline(start: Vec3, end: Vec3, spacing_mm: float = 0.30) -> tuple[Vec3, ...]:
    """以不大于给定间距的均匀采样生成直梁中心线。"""

    sample_count = max(2, math.ceil(start.distance_to(end) / spacing_mm) + 1)
    return tuple(
        start + (end - start) * (index / (sample_count - 1)) for index in range(sample_count)
    )


def select_guide_component_bridge(
    context: GenerationContext,
) -> GuideComponentBridgePlan:
    """按病例 YAML 的两个牙位站位生成 U 侧和背 U 侧跨分量双梁。"""

    config = context.config.guide_component_bridge
    if not config.enabled:
        raise GeometryError("当前病例未启用断裂导板预连接")
    if (
        context.case is None
        or context.sleeve_generation is None
        or context.window_cutouts is None
        or context.tooth_identification is None
    ):
        raise GeometryError("断裂导板预连接缺少病例、导管、切窗或牙位结果")
    tooth_stations = tuple(item.tooth_station for item in config.stations)
    angle_pairs = tuple(
        (
            item.u_side_ray_angle_degrees,
            item.back_u_side_ray_angle_degrees,
        )
        for item in config.stations
    )
    component_meshes, component_areas = _load_meaningful_components(
        context.case.config.inputs.template
    )
    if len(component_areas) != config.required_guide_component_count:
        raise GeometryError(
            "断裂导板主体分量数量不符："
            f"要求 {config.required_guide_component_count}，实际 {len(component_areas)}"
        )
    assignments = ((1, 2), (2, 1)) if config.require_different_guide_components else ((None, None),)
    candidates = []
    failures = []
    for assignment in assignments:
        try:
            selection = select_tooth_section_local_anchor_pairs(
                context.case,
                context.tooth_identification,
                tooth_stations,
                angle_pairs,
                station_meshes=(
                    None
                    if assignment[0] is None
                    else tuple(component_meshes[rank - 1] for rank in assignment)
                ),
            )
        except GeometryError as error:
            failures.append(f"{assignment}: {error}")
            continue
        score = sum(
            item.plane_origin.distance_to(anchor.position)
            for item in selection
            for anchor in (item.first, item.second)
        )
        candidates.append((score, selection, assignment))
    if not candidates:
        raise GeometryError(
            "两个牙位站位无法按不同导板分量完成同侧双射线选点：" + "; ".join(failures)
        )
    _, selections, selected_assignment = min(candidates, key=lambda item: item[0])
    station_component_ranks = list(selected_assignment)
    if (
        config.require_different_guide_components
        and station_component_ranks[0] == station_component_ranks[1]
    ):
        raise GeometryError("两个预连接牙位站位落在同一导板分量，不能跨接断裂导板")
    links = []
    for side, attribute in (("u_side", "first"), ("back_u_side", "second")):
        start_anchor = getattr(selections[0], attribute)
        end_anchor = getattr(selections[1], attribute)
        links.append(
            GuideComponentBridgeLink(
                f"{config.stations[0].station_id}_{config.stations[1].station_id}_{side}",
                side,
                start_anchor.position,
                end_anchor.position,
                start_anchor.normal,
                end_anchor.normal,
                _straight_centerline(start_anchor.position, end_anchor.position),
                station_component_ranks[0],
                station_component_ranks[1],
            )
        )
    return GuideComponentBridgePlan(
        (links[0], links[1]),
        config.radius_mm,
        config.dental_clearance_mm,
        config.endpoint_reinforcement,
        tuple(
            trajectory for selection in selections for trajectory in selection.support_trajectories
        ),
        component_areas,
    )

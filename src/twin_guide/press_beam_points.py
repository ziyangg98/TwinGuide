"""第 5 步：选择牙弓内侧导管高端与两个牙位导板锚点。"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, replace
from enum import StrEnum

from twin_guide.config import (
    PressBeamGuideEndpointParameters,
    PressBeamMode,
    PressBeamSleeveAnchorSelectionParameters,
)
from twin_guide.errors import GeometryError
from twin_guide.geometry import Vec3, mean_point
from twin_guide.tooth_section_anchors import (
    ToothSectionSingleRayAnchorSelection,
    ToothSectionSurfaceAnchor,
    select_tooth_section_u_side_ray_anchors,
)
from twin_guide.types import GenerationContext

PRESS_BEAM_U_SIDE_RAY_DEGREES = 45.0


@dataclass(frozen=True, slots=True)
class InnerSleeveScore:
    """一根导管相对最近牙位的牙弓外向坐标。"""

    guide_index: int
    nearest_fdi: int
    outward_coordinate_mm: float


@dataclass(frozen=True, slots=True)
class PressBeamGuideAnchor:
    """一个按压梁导板端 A/S 锚点。"""

    station_fdis: tuple[int, ...]
    trajectory_fraction: float | None
    surface_anchor: Vec3
    surface_normal: Vec3
    centerline_anchor: Vec3
    arch_outward_coordinate_mm: float = 0.0
    ray_angle_degrees: float | None = None


@dataclass(frozen=True, slots=True)
class PressBeamSleeveAnchor:
    """牙弓内侧导管 C 口高端的 Q/P 锚点。"""

    guide_index: int
    label: str
    surface_contact: Vec3
    surface_normal: Vec3
    centerline_anchor: Vec3
    sleeve_axis: Vec3


@dataclass(frozen=True, slots=True)
class PressBeamExtensionAnchor:
    """末端 U 型延伸梁圆管上的 Q/S 锚点。"""

    segment: str
    centerline_point: Vec3
    centerline_tangent: Vec3
    surface_contact: Vec3
    surface_normal: Vec3
    centerline_anchor: Vec3
    guide_anchor_distances_mm: tuple[float, float]
    minimum_guide_anchor_distance_mm: float
    segment_fraction: float


class PressBeamJunctionHeightMode(StrEnum):
    """Y 汇合点相对三锚点中心或导管锚点的高度规则。"""

    SLEEVE_ALIGNED = "sleeve_aligned"
    ANCHOR_CENTER_LIFTED = "anchor_center_lifted"
    OCCLUSAL_LIFTED = "occlusal_lifted"


@dataclass(frozen=True, slots=True)
class PressBeamPointPlan:
    """三锚点 Y 型按压梁的选点与显式汇合点计划。"""

    sleeve_anchor: PressBeamSleeveAnchor | None
    guide_anchors: tuple[PressBeamGuideAnchor, ...]
    junction: Vec3
    radius_mm: float
    guide_overlap_mm: float
    junction_radius_factor: float
    trajectories: tuple[tuple[Vec3, ...], ...]
    inner_sleeve_scores: tuple[InnerSleeveScore, ...]
    junction_axial_error_mm: float
    junction_minimum_angle_degrees: float
    junction_sleeve_distance_mm: float
    junction_sleeve_distance_error_mm: float
    extension_anchor: PressBeamExtensionAnchor | None = None
    connection_type: str = "inner_sleeve_upper_y_tripod"
    guide_endpoint: PressBeamGuideEndpointParameters | None = None
    junction_axis: Vec3 | None = None
    junction_height_mode: PressBeamJunctionHeightMode = PressBeamJunctionHeightMode.OCCLUSAL_LIFTED
    junction_center_axial_offset_mm: float = 0.0
    anchor_center_to_sleeve_axial_mm: float | None = None


def _inner_sleeve_scores(context: GenerationContext) -> tuple[InnerSleeveScore, ...]:
    """以最近牙冠的局部外向坐标排序两根导管。"""

    assert context.sleeve_generation is not None
    assert context.tooth_identification is not None
    teeth = context.tooth_identification.positions
    scores = []
    for guide in context.sleeve_generation.sleeves:
        nearest = min(teeth, key=lambda tooth: tooth.crown_point.distance_to(guide.center))
        coordinate = (guide.center - nearest.crown_point).dot(nearest.local_outward)
        scores.append(InnerSleeveScore(guide.guide_index, nearest.fdi, coordinate))
    return tuple(sorted(scores, key=lambda score: score.outward_coordinate_mm))


def _centerline_anchor(
    anchor: ToothSectionSurfaceAnchor,
    radius_mm: float,
    overlap_mm: float,
) -> Vec3:
    """将导板表面 A 沿外法向偏移为扫掠中心线端点 S。"""

    return anchor.position + anchor.normal.normalized() * (radius_mm - overlap_mm)


def _triangle_area_twice(first: Vec3, second: Vec3, third: Vec3) -> float:
    """返回三维三角形面积的两倍。"""

    return (second - first).cross(third - first).length


def _project_to_axial_plane(point: Vec3, origin: Vec3, axis: Vec3) -> Vec3:
    """将点正交投影到经过 origin 且垂直于导管轴线的平面。"""

    unit_axis = axis.normalized()
    return point - unit_axis * (point - origin).dot(unit_axis)


def _positive_sleeve_axis(axis: Vec3, lower: Vec3, upper: Vec3) -> Vec3:
    """将导管轴符号统一为从低端 P 指向高端 P。"""

    positive = axis.normalized()
    return positive if positive.dot(upper - lower) >= 0.0 else positive * -1.0


def _geometric_median(points: tuple[Vec3, Vec3, Vec3]) -> Vec3:
    """以 Weiszfeld 迭代返回三个中心线锚点的三维几何中位点。"""

    current = mean_point(points)
    for _ in range(128):
        distances = tuple(max(current.distance_to(point), 1e-8) for point in points)
        updated = sum(
            (point * (1.0 / distance) for point, distance in zip(points, distances, strict=True)),
            Vec3(0.0, 0.0, 0.0),
        ) / sum(1.0 / distance for distance in distances)
        if current.distance_to(updated) <= 1e-7:
            return updated
        current = updated
    return current


def _conditional_inner_sleeve_junction(
    points: tuple[Vec3, Vec3, Vec3],
    plane_origin: Vec3,
    sleeve_axis: Vec3,
    minimum_sleeve_distance_mm: float,
    axial_lift_mm: float = 2.0,
) -> Vec3:
    """按导管锚点相对高度选择等高或从三锚点中心继续抬高。"""

    unit_axis = sleeve_axis.normalized()
    original_center = _geometric_median(points)
    center_above_sleeve_mm = (original_center - plane_origin).dot(unit_axis)
    if center_above_sleeve_mm > 1e-8:
        candidate = original_center + unit_axis * axial_lift_mm
    else:
        candidate = _project_to_axial_plane(
            original_center,
            plane_origin,
            unit_axis,
        )

    axial_delta_mm = (candidate - plane_origin).dot(unit_axis)
    radial = candidate - plane_origin - unit_axis * axial_delta_mm
    if radial.length <= 1e-8:
        projected_guide_center = _project_to_axial_plane(
            mean_point(points[1:]),
            plane_origin,
            unit_axis,
        )
        radial = projected_guide_center - plane_origin
    if radial.length <= 1e-8:
        raise GeometryError("Y 汇合点在导管轴向垂直平面内没有稳定的径向方向")
    if candidate.distance_to(plane_origin) >= minimum_sleeve_distance_mm:
        return candidate
    required_radial_mm = math.sqrt(max(0.0, minimum_sleeve_distance_mm**2 - axial_delta_mm**2))
    return plane_origin + unit_axis * axial_delta_mm + radial.normalized() * required_radial_mm


def _minimum_junction_angle_degrees(
    junction: Vec3,
    anchors: tuple[Vec3, Vec3, Vec3],
) -> float:
    """返回三根 Y 臂在汇合点处的最小夹角。"""

    directions = tuple((anchor - junction).normalized() for anchor in anchors)
    return min(
        math.degrees(math.acos(max(-1.0, min(1.0, first.dot(second)))))
        for index, first in enumerate(directions)
        for second in directions[index + 1 :]
    )


def _farthest_point_from_two_anchors(
    points: tuple[Vec3, ...],
    first_anchor: Vec3,
    second_anchor: Vec3,
    start_margin_mm: float,
    end_margin_mm: float,
) -> tuple[Vec3, Vec3, tuple[float, float], float]:
    """返回使到两个锚点的较小距离最大的连续折线点。"""

    if len(points) < 2:
        raise GeometryError("末端 U 型延伸梁候选段不足两个中心线点")
    lengths = tuple(first.distance_to(second) for first, second in itertools.pairwise(points))
    total_length = sum(lengths)
    if total_length <= start_margin_mm + end_margin_mm:
        raise GeometryError(
            f"末端 U 型延伸梁候选段长度 {total_length:.3f} mm "
            f"不足以保留起点 {start_margin_mm:.3f} mm、终点 "
            f"{end_margin_mm:.3f} mm 余量"
        )
    best: tuple[float, float, Vec3, Vec3, tuple[float, float], float] | None = None
    cumulative = 0.0
    for first, second, length in zip(points[:-1], points[1:], lengths, strict=True):
        if length <= 1e-9:
            continue
        vector = second - first
        direction = vector / length
        lower = max(0.0, (start_margin_mm - cumulative) / length)
        upper = min(1.0, (total_length - end_margin_mm - cumulative) / length)
        if lower > upper + 1e-12:
            cumulative += length
            continue
        fractions = [lower, upper]
        # 两个平方距离之差沿直线段为一次函数；其零点是 maximin
        # 目标可能出现内部折点的唯一位置。其余最大值只可能位于端点。
        anchor_delta = second_anchor - first_anchor
        slope = 2.0 * vector.dot(anchor_delta)
        intercept = (first - first_anchor).dot(first - first_anchor) - (first - second_anchor).dot(
            first - second_anchor
        )
        if abs(slope) > 1e-12:
            equal_fraction = -intercept / slope
            if lower <= equal_fraction <= upper:
                fractions.append(equal_fraction)
        for fraction in fractions:
            point = first + vector * fraction
            distances = (
                point.distance_to(first_anchor),
                point.distance_to(second_anchor),
            )
            minimum_distance = min(distances)
            distance_sum = sum(distances)
            along_fraction = (cumulative + fraction * length) / total_length
            candidate = (
                minimum_distance,
                distance_sum,
                point,
                direction,
                distances,
                along_fraction,
            )
            if best is None or candidate[:2] > best[:2]:
                best = candidate
        cumulative += length
    if best is None:
        raise GeometryError("末端 U 型延伸梁候选段没有满足端部余量的最远点")
    return best[2], best[3], best[4], best[5]


def _extension_segment_centerline(
    context: GenerationContext,
    segment: str,
) -> tuple[Vec3, ...]:
    """按配置名称取得末端 U 型延伸梁的候选中心线段。"""

    extension = context.guide_terminal_u_extension
    if extension is None:
        raise GeometryError("末端 U 型延伸梁锚点 Y 梁缺少延伸梁计划")
    if segment == "u_side":
        return extension.u_side_centerline
    if segment == "back_u_side":
        return extension.back_u_side_centerline
    if segment == "turnaround":
        return extension.turnaround_centerline
    if segment == "full":
        return extension.centerline
    raise GeometryError(f"不支持的末端 U 型延伸梁候选段：{segment}")


def _lifted_three_anchor_junction(
    points: tuple[Vec3, Vec3, Vec3],
    radius_mm: float,
    lift_axis: Vec3,
    axial_lift_mm: float,
    minimum_junction_angle_degrees: float,
) -> tuple[Vec3, float]:
    """检查三个锚点展开度，并沿指定轴抬高其算术中心。"""

    center = mean_point(points)
    junction = center + lift_axis * axial_lift_mm
    area_twice = _triangle_area_twice(*points)
    minimum_edge = min(
        first.distance_to(second)
        for index, first in enumerate(points)
        for second in points[index + 1 :]
    )
    minimum_angle = _minimum_junction_angle_degrees(junction, points)
    if area_twice <= 2.0 * radius_mm * radius_mm or minimum_edge <= 2.0 * radius_mm:
        raise GeometryError("三个锚点无法形成稳定展开的 Y 型三角形")
    if minimum_angle < minimum_junction_angle_degrees:
        raise GeometryError(
            f"Y 汇合点最小三臂夹角 {minimum_angle:.2f}° 小于 {minimum_junction_angle_degrees:.2f}°"
        )
    return junction, minimum_angle


def _fixed_ray_guide_anchors(
    selections: tuple[ToothSectionSingleRayAnchorSelection, ...],
    radius_mm: float,
    overlap_mm: float,
) -> tuple[PressBeamGuideAnchor, ...]:
    """将显式角度的 U 侧射线出口转换为按压梁导板锚点。"""

    return tuple(
        PressBeamGuideAnchor(
            selection.station_fdis,
            None,
            selection.anchor.position,
            selection.anchor.normal,
            _centerline_anchor(selection.anchor, radius_mm, overlap_mm),
            selection.arch_outward_coordinate_mm,
            selection.ray_angle_degrees,
        )
        for selection in selections
    )


def _case_occlusal_axis(context: GenerationContext) -> Vec3:
    """返回由病例 YAML 确认并写入牙位映射的牙合方向。"""

    assert context.tooth_identification is not None
    coordinate_system = context.tooth_identification.mapping_report.get("coordinate_system")
    if not isinstance(coordinate_system, dict):
        raise GeometryError("牙位映射缺少病例 YAML 的 coordinate_system")
    raw_axis = coordinate_system.get("e_occ")
    if (
        not isinstance(raw_axis, list | tuple)
        or len(raw_axis) != 3
        or any(isinstance(value, bool) or not isinstance(value, int | float) for value in raw_axis)
    ):
        raise GeometryError("牙位映射 coordinate_system.e_occ 必须为三元素数值向量")
    axis = Vec3(*(float(value) for value in raw_axis))
    if axis.length <= 1e-8:
        raise GeometryError("牙位映射 coordinate_system.e_occ 长度为零")
    return axis.normalized()


def _select_three_guide_candidates(
    selections: tuple[ToothSectionSingleRayAnchorSelection, ...],
    radius_mm: float,
    overlap_mm: float,
    lift_axis: Vec3,
    axial_lift_mm: float,
    minimum_junction_angle_degrees: float,
) -> tuple[tuple[PressBeamGuideAnchor, ...], Vec3, float]:
    """转换三个固定的 U 侧射线锚点，并检查抬高后的 Y 三臂。"""

    if len(selections) != 3:
        raise GeometryError("全牙位锚点 Y 型按压梁必须提供三个牙位射线站位")
    anchors = _fixed_ray_guide_anchors(selections, radius_mm, overlap_mm)
    points = tuple(anchor.centerline_anchor for anchor in anchors)
    junction, minimum_angle = _lifted_three_anchor_junction(
        points,
        radius_mm,
        lift_axis,
        axial_lift_mm,
        minimum_junction_angle_degrees,
    )
    return tuple(anchors), junction, minimum_angle


def _select_three_tooth_anchor_points(
    context: GenerationContext,
) -> PressBeamPointPlan:
    """选择三个牙位导板锚点，并将汇合点沿病例牙合方向抬高。"""

    assert context.case is not None
    assert context.sleeve_generation is not None
    assert context.tooth_identification is not None
    assert context.window_cutouts is not None
    config = context.config.press_beam
    occlusal_axis = _case_occlusal_axis(context)
    selections = select_tooth_section_u_side_ray_anchors(
        context.case,
        context.tooth_identification,
        config.stations,
        PRESS_BEAM_U_SIDE_RAY_DEGREES,
    )
    anchors, junction, minimum_angle = _select_three_guide_candidates(
        selections,
        config.radius_mm,
        config.guide_overlap_mm,
        occlusal_axis,
        config.junction_axial_lift_mm,
        config.minimum_junction_angle_degrees,
    )
    anchor_center = mean_point(tuple(anchor.centerline_anchor for anchor in anchors))
    axial_lift = (junction - anchor_center).dot(occlusal_axis)
    axial_error = abs(axial_lift - config.junction_axial_lift_mm)
    if axial_error > 1e-6:
        raise GeometryError(f"全牙位 Y 汇合点牙合方向抬高误差 {axial_error:.6g} mm 超限")
    return PressBeamPointPlan(
        sleeve_anchor=None,
        extension_anchor=None,
        guide_anchors=anchors,
        junction=junction,
        radius_mm=config.radius_mm,
        guide_overlap_mm=config.guide_overlap_mm,
        junction_radius_factor=1.12,
        trajectories=tuple(selection.trajectory for selection in selections),
        inner_sleeve_scores=(),
        junction_axial_error_mm=axial_error,
        junction_minimum_angle_degrees=minimum_angle,
        junction_sleeve_distance_mm=0.0,
        junction_sleeve_distance_error_mm=0.0,
        connection_type="three_tooth_anchors_y_tripod",
        guide_endpoint=config.guide_endpoint,
        junction_axis=occlusal_axis,
        junction_height_mode=PressBeamJunctionHeightMode.OCCLUSAL_LIFTED,
        junction_center_axial_offset_mm=axial_lift,
    )


def _select_terminal_u_extension_anchor_points(
    context: GenerationContext,
) -> PressBeamPointPlan:
    """选择一处延伸梁双锚点 maximin 最远点组成 Y 型按压梁。"""

    assert context.case is not None
    assert context.tooth_identification is not None
    config = context.config.press_beam
    anchor_config = config.extension_anchor
    if anchor_config is None:
        raise GeometryError("末端 U 型延伸梁锚点 Y 梁缺少 extension_anchor 配置")
    selections = select_tooth_section_u_side_ray_anchors(
        context.case,
        context.tooth_identification,
        config.stations,
        PRESS_BEAM_U_SIDE_RAY_DEGREES,
    )
    guide_anchors = _fixed_ray_guide_anchors(selections, config.radius_mm, config.guide_overlap_mm)
    if len(guide_anchors) != 2:
        raise GeometryError("末端 U 型延伸梁锚点 Y 梁必须产生两个牙位锚点")
    segment_centerline = _extension_segment_centerline(context, anchor_config.segment)
    center, tangent, anchor_distances, segment_fraction = _farthest_point_from_two_anchors(
        segment_centerline,
        guide_anchors[0].centerline_anchor,
        guide_anchors[1].centerline_anchor,
        anchor_config.start_margin_mm,
        anchor_config.end_margin_mm,
    )
    guide_midpoint = mean_point(tuple(anchor.centerline_anchor for anchor in guide_anchors))
    toward_guides = guide_midpoint - center
    radial = toward_guides - tangent * toward_guides.dot(tangent)
    if radial.length <= 1e-8:
        raise GeometryError("延伸梁最远点朝 Y 梁区域没有稳定的圆管径向")
    normal = radial.normalized()
    extension = context.guide_terminal_u_extension
    assert extension is not None
    surface = center + normal * extension.radius_mm
    embedded = surface - normal * anchor_config.overlap_mm
    extension_anchor = PressBeamExtensionAnchor(
        segment=anchor_config.segment,
        centerline_point=center,
        centerline_tangent=tangent,
        surface_contact=surface,
        surface_normal=normal,
        centerline_anchor=embedded,
        guide_anchor_distances_mm=anchor_distances,
        minimum_guide_anchor_distance_mm=min(anchor_distances),
        segment_fraction=segment_fraction,
    )
    points = (
        embedded,
        guide_anchors[0].centerline_anchor,
        guide_anchors[1].centerline_anchor,
    )
    occlusal_axis = _case_occlusal_axis(context)
    junction, minimum_angle = _lifted_three_anchor_junction(
        points,
        config.radius_mm,
        occlusal_axis,
        config.junction_axial_lift_mm,
        config.minimum_junction_angle_degrees,
    )
    anchor_center = mean_point(points)
    axial_lift = (junction - anchor_center).dot(occlusal_axis)
    return PressBeamPointPlan(
        sleeve_anchor=None,
        extension_anchor=extension_anchor,
        guide_anchors=guide_anchors,
        junction=junction,
        radius_mm=config.radius_mm,
        guide_overlap_mm=config.guide_overlap_mm,
        junction_radius_factor=1.12,
        trajectories=(
            tuple(segment_centerline),
            *(selection.trajectory for selection in selections),
        ),
        inner_sleeve_scores=(),
        junction_axial_error_mm=abs(axial_lift - config.junction_axial_lift_mm),
        junction_minimum_angle_degrees=minimum_angle,
        junction_sleeve_distance_mm=0.0,
        junction_sleeve_distance_error_mm=0.0,
        connection_type="terminal_u_extension_anchor_y_tripod",
        guide_endpoint=config.guide_endpoint,
        junction_axis=occlusal_axis,
        junction_height_mode=PressBeamJunctionHeightMode.OCCLUSAL_LIFTED,
        junction_center_axial_offset_mm=axial_lift,
    )


def _select_press_beam_points_base(context: GenerationContext) -> PressBeamPointPlan:
    """选择牙弓内侧导管高端 P 和两个牙位导板 A/S 点。

    参数:
        context: 已完成病例分析、牙位识别、切窗和第 4 步联建选点的上下文。

    返回:
        包含三个端点锚点、Y 汇合点和射线或轨迹参考线的计划。

    算法说明:
        对每根导管查找最近牙位，以导管中心相对牙冠沿该牙位局部外向
        方向的有符号坐标排序；数值较小者是 U 型牙弓内侧导管。导管端
        只复用第 4 步已经验证的 ``upper`` 高端 Q/P，不允许配置或选中
        唇颊侧外导管。两种 Y 模式的每个导板端均采用配置的单牙中心或
        双牙中点和显式 ``ray_angle_degrees``，沿牙合轴向 U 侧旋转并取导板外壁
        出口，不再生成轨迹候选或自动评分选点。
        若三锚点几何中位点沿导管正轴高于 ``upper P``，汇合点从该中心
        继续抬高配置距离；否则才与 ``upper P`` 轴向等高。距离不足时只
        沿对应等高平面径向外推，并按配置检查三臂最小夹角。
        全牙位模式不使用上述轨迹候选，而为三个牙位分别从牙冠最高点
        沿牙合方向下移 2 mm 后按各站位显式角度发射 U 侧射线，直接采用
        局部外壁出口。
    """

    config = context.config.press_beam
    if config.mode is PressBeamMode.DISABLED:
        raise ValueError("当前病例未启用 Y 型按压梁")
    if (
        context.case is None
        or context.sleeve_generation is None
        or context.tooth_identification is None
        or context.window_cutouts is None
        or context.template_link_points is None
    ):
        raise GeometryError("按压梁选点缺少病例、牙位、切窗或联建锚点上游结果")

    if config.mode is PressBeamMode.THREE_TOOTH_ANCHORS_Y:
        return _select_three_tooth_anchor_points(context)
    if config.mode is PressBeamMode.TERMINAL_U_EXTENSION_ANCHOR_Y:
        return _select_terminal_u_extension_anchor_points(context)
    if config.mode is not PressBeamMode.INNER_SLEEVE_UPPER_Y:
        raise ValueError(f"不支持的 Y 型按压梁模式：{config.mode.value}")
    selection_policy = config.sleeve_anchor_selection
    if selection_policy is None:
        raise GeometryError("内侧导管高端 Y 型按压梁缺少导管锚点筛选策略")
    if not isinstance(
        selection_policy,
        PressBeamSleeveAnchorSelectionParameters,
    ):
        raise GeometryError("导管锚点筛选策略接口类型错误")

    tooth_selections = select_tooth_section_u_side_ray_anchors(
        context.case,
        context.tooth_identification,
        config.stations,
        PRESS_BEAM_U_SIDE_RAY_DEGREES,
    )
    guide_anchors = _fixed_ray_guide_anchors(
        tooth_selections,
        config.radius_mm,
        config.guide_overlap_mm,
    )
    if len(guide_anchors) != 2:
        raise GeometryError("内侧导管高端 Y 型按压梁必须产生两个固定射线锚点")
    scores = _inner_sleeve_scores(context)
    score_by_guide = {score.guide_index: score for score in scores}
    guides = context.sleeve_generation.sleeves
    if len(guides) not in {2, 4} or len(guides) % 2:
        raise GeometryError("按压梁内外侧判定要求一个或两个双导管种植位")
    candidates = []
    for pair_start in range(0, len(guides), 2):
        pair_scores = tuple(
            sorted(
                (
                    score_by_guide[guide.guide_index]
                    for guide in guides[pair_start : pair_start + 2]
                ),
                key=lambda score: score.outward_coordinate_mm,
            )
        )
        if pair_scores[1].outward_coordinate_mm - pair_scores[0].outward_coordinate_mm < 0.50:
            raise GeometryError(f"种植位 {pair_start // 2 + 1} 的双导管牙弓内外侧差异不足 0.50 mm")
        candidate = pair_scores[0]
        sleeve_selection = next(
            selection
            for selection in context.template_link_points.sleeve_anchors.selections
            if selection.guide_index == candidate.guide_index
        )
        distances = tuple(
            sleeve_selection.upper.position.distance_to(anchor.centerline_anchor)
            for anchor in guide_anchors
        )
        candidates.append((min(distances), sum(distances), candidate, sleeve_selection))
    if selection_policy.distance_score != "maximin_to_two_guide_anchors":
        raise GeometryError(f"不支持的 Y 梁导管距离评分：{selection_policy.distance_score}")
    if selection_policy.tie_breaker != "larger_sum_distance":
        raise GeometryError(f"不支持的 Y 梁导管平局规则：{selection_policy.tie_breaker}")
    if selection_policy.candidate_scope != "inner_sleeve_upper_per_implant_site":
        raise GeometryError(f"不支持的 Y 梁导管候选范围：{selection_policy.candidate_scope}")
    _, _, inner_score, sleeve_selection = max(
        candidates,
        key=lambda item: (item[0], item[1], -item[2].guide_index),
    )
    inner_guide = next(guide for guide in guides if guide.guide_index == inner_score.guide_index)
    upper = sleeve_selection.upper
    centerline_anchors = (
        upper.position,
        guide_anchors[0].centerline_anchor,
        guide_anchors[1].centerline_anchor,
    )
    sleeve_axis = _positive_sleeve_axis(
        inner_guide.axis,
        sleeve_selection.lower.position,
        upper.position,
    )
    original_center = _geometric_median(centerline_anchors)
    center_above_sleeve_mm = (original_center - upper.position).dot(sleeve_axis)
    expected_axial_delta_mm = (
        center_above_sleeve_mm + config.junction_axial_lift_mm
        if center_above_sleeve_mm > 1e-8
        else 0.0
    )
    junction = _conditional_inner_sleeve_junction(
        centerline_anchors,
        upper.position,
        sleeve_axis,
        config.junction_sleeve_distance_mm,
        config.junction_axial_lift_mm,
    )
    axial_error = abs((junction - upper.position).dot(sleeve_axis) - expected_axial_delta_mm)
    if axial_error > 1e-6:
        raise GeometryError(f"Y 汇合点条件轴向高度误差 {axial_error:.6g} mm 超限")
    sleeve_distance = junction.distance_to(upper.position)
    sleeve_distance_error = max(
        0.0,
        config.junction_sleeve_distance_mm - sleeve_distance,
    )
    if sleeve_distance_error > 1e-6:
        raise GeometryError(
            f"Y 汇合点到导管上锚点的最小距离缺口 {sleeve_distance_error:.6g} mm 超限"
        )
    minimum_angle = _minimum_junction_angle_degrees(junction, centerline_anchors)
    if minimum_angle < config.minimum_junction_angle_degrees:
        raise GeometryError(
            f"Y 汇合点最小三臂夹角 {minimum_angle:.2f}° 小于 "
            f"{config.minimum_junction_angle_degrees:.2f}°"
        )
    height_mode = (
        PressBeamJunctionHeightMode.ANCHOR_CENTER_LIFTED
        if center_above_sleeve_mm > 1e-8
        else PressBeamJunctionHeightMode.SLEEVE_ALIGNED
    )
    return PressBeamPointPlan(
        sleeve_anchor=PressBeamSleeveAnchor(
            inner_score.guide_index,
            "upper",
            upper.surface_contact,
            upper.surface_normal,
            upper.position,
            sleeve_axis,
        ),
        extension_anchor=None,
        guide_anchors=guide_anchors,
        junction=junction,
        radius_mm=config.radius_mm,
        guide_overlap_mm=config.guide_overlap_mm,
        junction_radius_factor=1.12,
        trajectories=(
            tooth_selections[0].trajectory,
            tooth_selections[1].trajectory,
        ),
        inner_sleeve_scores=scores,
        junction_axial_error_mm=axial_error,
        junction_minimum_angle_degrees=minimum_angle,
        junction_sleeve_distance_mm=sleeve_distance,
        junction_sleeve_distance_error_mm=sleeve_distance_error,
        guide_endpoint=config.guide_endpoint,
        junction_axis=sleeve_axis,
        junction_height_mode=height_mode,
        junction_center_axial_offset_mm=(junction - original_center).dot(sleeve_axis),
        anchor_center_to_sleeve_axial_mm=center_above_sleeve_mm,
    )


def _apply_editor_overrides(
    context: GenerationContext,
    plan: PressBeamPointPlan,
) -> PressBeamPointPlan:
    """把显式表面锚点和工作平面汇合点交给原有梁生成流程。"""

    overrides = getattr(context.config, "editor_overrides", None)
    if overrides is None:
        return plan
    anchors = []
    for index, anchor in enumerate(plan.guide_anchors, start=1):
        override = overrides.surface_anchor_for(f"press_anchor_{index}")
        if override is None:
            anchors.append(anchor)
            continue
        position = Vec3(*override.position_mm)
        normal = Vec3(*override.normal).normalized()
        anchors.append(
            replace(
                anchor,
                surface_anchor=position,
                surface_normal=normal,
                centerline_anchor=(position + normal * (plan.radius_mm - plan.guide_overlap_mm)),
            )
        )
    junction = (
        plan.junction if overrides.press_junction_mm is None else Vec3(*overrides.press_junction_mm)
    )
    centerline_anchors = tuple(anchor.centerline_anchor for anchor in anchors)
    if plan.sleeve_anchor is not None:
        angle_anchors = (plan.sleeve_anchor.centerline_anchor, *centerline_anchors)
    elif plan.extension_anchor is not None:
        angle_anchors = (plan.extension_anchor.centerline_anchor, *centerline_anchors)
    else:
        angle_anchors = centerline_anchors
    minimum_angle = (
        _minimum_junction_angle_degrees(junction, angle_anchors)
        if len(angle_anchors) == 3
        else plan.junction_minimum_angle_degrees
    )
    return replace(
        plan,
        guide_anchors=tuple(anchors),
        junction=junction,
        junction_minimum_angle_degrees=minimum_angle,
    )


def select_press_beam_points(context: GenerationContext) -> PressBeamPointPlan:
    """选择按压梁点，并应用图形编辑器的显式覆盖值。

    参数:
        context: 已完成上游阶段的生成上下文。

    返回:
        继续交给现有梁生成逻辑的按压点计划。
    """

    return _apply_editor_overrides(context, _select_press_beam_points_base(context))

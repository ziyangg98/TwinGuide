"""第 4 步的导套侧上下锚点选择。"""

from __future__ import annotations

import math
from dataclasses import dataclass

from twin_guide.geometry import Vec3, project_to_plane
from twin_guide.models import CaseAnalysis, GuideSleeve
from twin_guide.types import SleeveGenerationResult

LOWER_ANCHOR_FRACTION = 0.25
UPPER_ANCHOR_FRACTION = 0.75
_DIRECTION_TOLERANCE = 1e-10
_SURFACE_TOLERANCE = 1e-9


@dataclass(frozen=True, slots=True)
class SleeveAnchorPoint:
    """一个导套侧锚点及其可行性诊断。

    属性:
        label: 锚点标签，取 ``lower`` 或 ``upper``。
        axial_fraction: 锚点高度占导套总高的比例。
        axial_position_mm: 锚点的轴向坐标，单位为毫米。
        section_center: 该高度处的导套轴线中心。
        position: 外壁锚点世界坐标；不可行时为 ``None``。
        feasible: 是否找到符合规则的外壁点。
        reason: 不可行原因；可行时为 ``None``。
    """

    label: str
    axial_fraction: float
    axial_position_mm: float
    section_center: Vec3
    position: Vec3 | None
    feasible: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class SleeveAnchorSelection:
    """一个导套的牙科导板朝向及上下锚点。

    属性:
        guide_index: 导套编号。
        radial_direction: 从导套轴线指向牙科导板侧外壁的单位向量。
        lower: 四分之一高度的锚点。
        upper: 四分之三高度的锚点。
    """

    guide_index: int
    radial_direction: Vec3 | None
    lower: SleeveAnchorPoint
    upper: SleeveAnchorPoint

    @property
    def feasible(self) -> bool:
        """返回上下两个导套锚点是否同时可行。"""

        return self.lower.feasible and self.upper.feasible


@dataclass(frozen=True, slots=True)
class SleeveAnchorPlan:
    """所有导套的导套侧锚点计划。

    属性:
        selections: 按导套顺序排列的锚点选择结果。
    """

    selections: tuple[SleeveAnchorSelection, ...]


def _body_wall_direction(guide: GuideSleeve) -> Vec3 | None:
    """计算牙科导板侧径向。

    参数:
        guide: 待处理导套。

    返回:
        平台方向在轴线法平面上投影的反向单位向量；投影退化时为 ``None``。
    """

    radial = -project_to_plane(guide.parameters.platform_direction, guide.axis)
    if radial.length <= _DIRECTION_TOLERANCE:
        return None
    return radial.normalized()


def _is_exposed_body_wall(guide: GuideSleeve, direction: Vec3, axial_position_mm: float) -> bool:
    """判断径向交点是否位于主体外圆弧。

    参数:
        guide: 待处理导套。
        direction: 从轴线指向候选点的单位径向。
        axial_position_mm: 候选点轴向坐标。

    返回:
        候选点在主体外圆弧上时为 ``True``。
    """

    parameters = guide.parameters
    platform = project_to_plane(parameters.platform_direction, guide.axis).normalized()
    axial_from_top = axial_position_mm - guide.axial_min_mm
    platform_start = parameters.height - parameters.platform_height
    if axial_from_top < platform_start - _SURFACE_TOLERANCE:
        gap_half_angle = 0.5 * (2.0 * math.pi - parameters.outer_arc_angle)
        return direction.dot(platform) <= math.cos(gap_half_angle) + _SURFACE_TOLERANCE
    return direction.dot(platform) <= _SURFACE_TOLERANCE


def _anchor_at_fraction(
    guide: GuideSleeve, direction: Vec3 | None, label: str, fraction: float
) -> SleeveAnchorPoint:
    """在指定高度比例上构造锚点。

    参数:
        guide: 待处理导套。
        direction: 牙科导板侧径向；退化时为 ``None``。
        label: 锚点标签。
        fraction: 锚点高度占导套长度的比例。

    返回:
        包含位置或失败诊断的导套锚点。
    """

    axial_position = guide.axial_min_mm + fraction * guide.length_mm
    center = guide.center + guide.axis * axial_position
    if direction is None:
        return SleeveAnchorPoint(
            label,
            fraction,
            axial_position,
            center,
            None,
            False,
            "平台径向无法确定",
        )
    position = center + direction * guide.body_radius_mm
    if not _is_exposed_body_wall(guide, direction, axial_position):
        return SleeveAnchorPoint(
            label,
            fraction,
            axial_position,
            center,
            None,
            False,
            "径向射线未与暴露的主体外圆弧相交",
        )
    return SleeveAnchorPoint(label, fraction, axial_position, center, position, True)


def select_sleeve_anchors(case: CaseAnalysis, sleeves: SleeveGenerationResult) -> SleeveAnchorPlan:
    """在每个导套主体外壁的四分之一和四分之三高度选点。

    参数:
        case: 包含牙科导板坐标系和导套分析结果的病例对象。
        sleeves: 第 1 步输出的导套结果。

    返回:
        每个导套的上下锚点及可行性。

    异常:
        ValueError: ``case`` 与 ``sleeves`` 中的导套不一致。

    算法说明:
        算法按以下顺序执行：

        1. 将平台方向投影到导套轴线法平面，取反向并归一化，
           得到牙科导板侧径向 ``radial_direction``。
        2. 用 ``axial_min_mm + fraction * length_mm`` 计算四分之一
           和四分之三高度。
        3. 在每个高度上，从轴线中心沿径向移动 ``body_radius_mm``，
           得到候选外壁点。
        4. 根据开口角和平台高度判断候选点是否位于主体外圆弧。
        5. 候选点落入开口或平台非圆弧区时，返回
           ``feasible=False`` 和失败原因，不尝试旋转或备用选点。

    """

    if case.guide_sleeves != sleeves.sleeves:
        raise ValueError("病例分析与导套生成结果不一致")
    selections = tuple(
        SleeveAnchorSelection(
            guide.guide_index,
            direction,
            _anchor_at_fraction(guide, direction, "lower", LOWER_ANCHOR_FRACTION),
            _anchor_at_fraction(guide, direction, "upper", UPPER_ANCHOR_FRACTION),
        )
        for guide in sleeves.sleeves
        for direction in (_body_wall_direction(guide),)
    )
    return SleeveAnchorPlan(selections)

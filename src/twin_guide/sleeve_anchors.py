"""第 4 步的导套侧上下锚点选择。"""

from __future__ import annotations

from dataclasses import dataclass

from twin_guide.geometry import Vec3, project_to_plane
from twin_guide.models import CaseAnalysis, GuideSleeve
from twin_guide.types import SleeveGenerationResult

LOWER_ANCHOR_FRACTION = 0.25
UPPER_ANCHOR_FRACTION = 0.75


@dataclass(frozen=True, slots=True)
class SleeveAnchorPoint:
    """一个导套侧锚点。

    属性:
        label: 锚点标签，取 ``lower`` 或 ``upper``。
        axial_fraction: 锚点高度占导套总高的比例。
        axial_position_mm: 锚点的轴向坐标，单位为毫米。
        section_center: 该高度处的导套轴线中心。
        position: 外壁锚点世界坐标。
    """

    label: str
    axial_fraction: float
    axial_position_mm: float
    section_center: Vec3
    position: Vec3


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
    radial_direction: Vec3
    lower: SleeveAnchorPoint
    upper: SleeveAnchorPoint

@dataclass(frozen=True, slots=True)
class SleeveAnchorPlan:
    """所有导套的导套侧锚点计划。

    属性:
        selections: 按导套顺序排列的锚点选择结果。
    """

    selections: tuple[SleeveAnchorSelection, ...]


def _body_wall_direction(guide: GuideSleeve) -> Vec3:
    """计算牙科导板侧径向。

    参数:
        guide: 待处理导套。

    返回:
        C 口方向在轴线法平面上投影的反向单位向量。
    """

    radial = -project_to_plane(guide.parameters.c_opening_direction, guide.axis)
    return radial.normalized()


def _anchor_at_fraction(
    guide: GuideSleeve, direction: Vec3, label: str, fraction: float
) -> SleeveAnchorPoint:
    """在指定高度比例上构造锚点。

    参数:
        guide: 待处理导套。
        direction: C 口反向的主体圆弧径向。
        label: 锚点标签。
        fraction: 锚点高度占导套长度的比例。

    返回:
        指定高度的导套锚点。
    """

    axial_position = guide.axial_min_mm + fraction * guide.length_mm
    center = guide.center + guide.axis * axial_position
    position = center + direction * guide.body_radius_mm
    return SleeveAnchorPoint(label, fraction, axial_position, center, position)


def select_sleeve_anchors(case: CaseAnalysis, sleeves: SleeveGenerationResult) -> SleeveAnchorPlan:
    """在每个导套主体外壁的四分之一和四分之三高度选点。

    参数:
        case: 包含牙科导板坐标系和导套分析结果的病例对象。
        sleeves: 第 1 步输出的导套结果。

    返回:
        每个导套的上下锚点。

    异常:
        ValueError: ``case`` 与 ``sleeves`` 中的导套不一致。

    算法说明:
        算法按以下顺序执行：

        1. 将 C 口方向投影到导套轴线法平面，取反向并归一化，
           得到牙科导板侧径向 ``radial_direction``。
        2. 用 ``axial_min_mm + fraction * length_mm`` 计算四分之一
           和四分之三高度。
        3. 在每个高度上，从轴线中心沿径向移动 ``body_radius_mm``，
           得到外壁锚点。

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

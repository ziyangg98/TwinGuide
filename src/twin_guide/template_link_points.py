"""导套—牙科导板联建选点。"""

from __future__ import annotations

from dataclasses import dataclass

from twin_guide.models import CaseAnalysis, CutoutPlan
from twin_guide.sleeve_anchors import SleeveAnchorPlan, select_sleeve_anchors
from twin_guide.template_anchors import (
    TemplatePointPlan,
    TemplatePointSelectionConfig,
    select_template_points,
)
from twin_guide.types import SleeveGenerationResult


@dataclass(frozen=True, slots=True)
class TemplateLinkPointContext:
    """联建选点的输入。

    属性:
        case: 牙科导板表面采样和局部坐标系。
        sleeves: 第 1 步导套输出。
        cutouts: 第 3 步通道与窗口计划。
    """

    case: CaseAnalysis
    sleeves: SleeveGenerationResult
    cutouts: CutoutPlan


@dataclass(frozen=True, slots=True)
class TemplateLinkPointPlan:
    """导套侧锚点和牙科导板侧锚点。

    属性:
        sleeve_anchors: 导套侧上下锚点。
        template_points: 牙科导板侧左右锚点。
    """

    sleeve_anchors: SleeveAnchorPlan
    template_points: TemplatePointPlan


def select_template_link_points(
    context: TemplateLinkPointContext,
    config: TemplatePointSelectionConfig,
) -> TemplateLinkPointPlan:
    """执行第 4 步选点。

    参数:
        context: 第 4 步显式声明的上游输入。
        config: 牙科导板侧选点净距、间距和搜索数量配置。

    返回:
        导套侧锚点和牙科导板侧锚点。

    算法说明:
        依次计算导套侧锚点和牙科导板侧锚点。
    """

    sleeve_anchors = select_sleeve_anchors(context.case, context.sleeves)
    template_points = select_template_points(
        context.case, context.sleeves, context.cutouts, sleeve_anchors, config
    )
    return TemplateLinkPointPlan(sleeve_anchors, template_points)

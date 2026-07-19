"""第 4 步公开接口：组合导套侧和牙科导板侧选点。"""

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
    """第 4 步允许读取的全部上游数据。

    属性:
        case: 牙科导板表面采样和局部坐标系。
        sleeves: 第 1 步导套输出。
        cutouts: 第 3 步通道与窗口计划。
    """
    case: CaseAnalysis
    sleeves: SleeveGenerationResult
    cutouts: CutoutPlan


@dataclass(frozen=True, slots=True)
class LinkPointDiagnostic:
    """一个导套的两侧选点可行性诊断。"""
    guide_index: int
    sleeve_anchors_feasible: bool
    template_points_feasible: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class TemplateLinkPointPlan:
    """第 4 步输出：只含选点和诊断。

    属性:
        sleeve_anchors: 导套侧上下锚点。
        template_points: 牙科导板侧左右锚点。
        diagnostics: 按导套记录的选点可行性。

    算法说明:
        该类不得包含中心线或 Blender 网格；它们属于第 6 步。
    """
    sleeve_anchors: SleeveAnchorPlan
    template_points: TemplatePointPlan
    diagnostics: tuple[LinkPointDiagnostic, ...]


def select_template_link_points(
    context: TemplateLinkPointContext,
    config: TemplatePointSelectionConfig | None = None,
) -> TemplateLinkPointPlan:
    """执行第 4 步选点。

    参数:
        context: 第 4 步显式声明的上游输入。
        config: 牙科导板侧选点净距、间距和搜索数量配置。

    返回:
        导套侧锚点、牙科导板侧锚点和可行性诊断。

    异常:
        ValueError: 病例分析与第 1 步的导套结果不一致。

    算法说明:
        该函数只负责阶段组合：先检查病例和导套结果一致，
        再依次调用 ``select_sleeve_anchors()`` 和 ``select_template_points()``，
        最后按导套汇总两侧可行性与第一个失败原因。
        本函数不构造第 6 步中心线或实体。
    """

    if config is None:
        config = TemplatePointSelectionConfig()
    if context.case.guide_sleeves != context.sleeves.sleeves:
        raise ValueError("病例分析与导套生成结果不一致")
    sleeve_anchors = select_sleeve_anchors(context.case, context.sleeves)
    template_points = select_template_points(
        context.case, context.sleeves, context.cutouts, sleeve_anchors, config
    )
    diagnostics = tuple(
        LinkPointDiagnostic(
            sleeve.guide_index,
            sleeve.feasible,
            template.feasible,
            sleeve.lower.reason or sleeve.upper.reason or template.reason,
        )
        for sleeve, template in zip(
            sleeve_anchors.selections, template_points.selections, strict=True
        )
    )
    return TemplateLinkPointPlan(sleeve_anchors, template_points, diagnostics)

"""按顺序执行各生成阶段。"""

from __future__ import annotations

from twin_guide.case_analysis import analyze_case
from twin_guide.config import CaseConfig
from twin_guide.point_linking import PointLinkingConfig, link_selected_points
from twin_guide.template_anchors import TemplatePointSelectionConfig
from twin_guide.template_link_points import (
    TemplateLinkPointContext,
    select_template_link_points,
)
from twin_guide.types import (
    GenerationContext,
    GenerationProcessResult,
    SleeveGenerationResult,
    StageDefinition,
    StageMaturity,
    StageResult,
    StageRunStatus,
)
from twin_guide.window_cutouts import plan_window_cutouts

STAGES = (
    StageDefinition(
        1,
        "sleeve_generation",
        "导套识别与重建",
        StageMaturity.STABLE,
        "1.0",
        ("source_meshes",),
        "sleeve_generation",
    ),
    StageDefinition(
        2,
        "tooth_identification",
        "牙位识别",
        StageMaturity.PENDING,
        None,
        ("source_meshes",),
        "tooth_identification",
    ),
    StageDefinition(
        3,
        "window_cutouts",
        "操作窗与观察窗规划",
        StageMaturity.EXPERIMENTAL,
        "0.1",
        ("template_analysis", "sleeve_generation"),
        "window_cutouts",
    ),
    StageDefinition(
        4,
        "template_link_points",
        "导套—牙科导板联建锚点选择",
        StageMaturity.EXPERIMENTAL,
        "0.1",
        ("template_analysis", "sleeve_generation", "window_cutouts"),
        "template_link_points",
    ),
    StageDefinition(
        5,
        "press_beam_points",
        "按压梁柱锚点选择",
        StageMaturity.PENDING,
        None,
        ("tooth_identification", "window_cutouts"),
        "press_beam_points",
    ),
    StageDefinition(
        6,
        "point_linking",
        "锚点连接与结构生成",
        StageMaturity.EXPERIMENTAL,
        "0.1",
        ("template_link_points",),
        "linked_structure",
    ),
    StageDefinition(
        7,
        "clearance_adjustment",
        "器械避让与净距优化",
        StageMaturity.PENDING,
        None,
        ("linked_structure",),
        "final_geometry_plan",
    ),
)


def _skipped(definition: StageDefinition) -> StageResult:
    """构造阶段跳过记录。

    参数:
        definition: 被跳过阶段的静态定义。

    返回:
        原因中包含阶段成熟度的 ``StageResult``。
    """

    return StageResult(
        definition,
        StageRunStatus.SKIPPED,
        reason=f"阶段状态为 {definition.maturity.value}",
    )


def run_generation_process(config: CaseConfig) -> GenerationProcessResult:
    """按顺序执行稳定和实验阶段。

    参数:
        config: 生成过程共享的病例配置。

    返回:
        包含七个阶段状态和已完成输出的 ``GenerationProcessResult``。

    算法说明:
        执行顺序为：第 1 步完成、第 2 步跳过、第 3 步完成、
        第 4 步完成、第 5 步跳过、第 6 步完成、第 7 步跳过。
        跳过阶段只写入 ``StageResult.reason``，对应上下文字段保持 ``None``。
    """

    context = GenerationContext(config=config)
    results: list[StageResult] = []

    case = analyze_case(config)
    context.case = case
    context.sleeve_generation = SleeveGenerationResult(
        case.guide_sleeves,
        case.template_frame,
    )
    results.append(StageResult(STAGES[0], StageRunStatus.COMPLETED, context.sleeve_generation))
    results.append(_skipped(STAGES[1]))

    context.window_cutouts = plan_window_cutouts(case, context.sleeve_generation)
    results.append(StageResult(STAGES[2], StageRunStatus.COMPLETED, context.window_cutouts))

    context.template_link_points = select_template_link_points(
        TemplateLinkPointContext(
            case,
            context.sleeve_generation,
            context.window_cutouts,
        ),
        TemplatePointSelectionConfig(connector_radius_mm=config.geometry.connector_radius_mm),
    )
    results.append(StageResult(STAGES[3], StageRunStatus.COMPLETED, context.template_link_points))

    results.append(_skipped(STAGES[4]))
    context.point_linking = link_selected_points(
        context.template_link_points,
        PointLinkingConfig(radius_mm=config.geometry.connector_radius_mm),
    )
    results.append(StageResult(STAGES[5], StageRunStatus.COMPLETED, context.point_linking))
    results.append(_skipped(STAGES[6]))
    return GenerationProcessResult(context, tuple(results))

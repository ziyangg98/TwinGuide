"""按顺序执行各生成阶段。"""

from __future__ import annotations

from twin_guide.case_analysis import analyze_case
from twin_guide.clearance_adjustment import adjust_clearance
from twin_guide.config import CaseConfig, PressBeamMode
from twin_guide.guide_component_bridge import select_guide_component_bridge
from twin_guide.guide_terminal_u_extension import select_guide_terminal_u_extension
from twin_guide.point_linking import PointLinkingConfig, link_selected_points
from twin_guide.press_beam_points import select_press_beam_points
from twin_guide.template_anchors import TemplatePointSelectionConfig
from twin_guide.template_link_points import (
    TemplateLinkPointContext,
    select_template_link_points,
)
from twin_guide.tooth_identification import identify_tooth_positions
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
        "导管识别与标准重建",
        StageMaturity.STABLE,
        "1.0",
        ("source_meshes",),
        "sleeve_generation",
    ),
    StageDefinition(
        2,
        "tooth_identification",
        "牙位识别",
        StageMaturity.EXPERIMENTAL,
        "0.4",
        ("source_meshes", "case_yaml_tooth_constraints"),
        "tooth_identification",
    ),
    StageDefinition(
        3,
        "window_cutouts",
        "操作窗与观察窗规划",
        StageMaturity.EXPERIMENTAL,
        "0.2",
        ("template_analysis", "sleeve_generation"),
        "window_cutouts",
    ),
    StageDefinition(
        4,
        "template_link_points",
        "导管—牙科导板联建锚点选择",
        StageMaturity.EXPERIMENTAL,
        "0.1",
        ("template_analysis", "sleeve_generation", "window_cutouts"),
        "template_link_points",
    ),
    StageDefinition(
        5,
        "press_beam_points",
        "按压梁锚点选择",
        StageMaturity.EXPERIMENTAL,
        "0.1",
        ("tooth_identification", "window_cutouts", "template_link_points"),
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
        StageMaturity.EXPERIMENTAL,
        "0.1",
        ("linked_structure",),
        "final_geometry_plan",
    ),
)


def _skipped(definition: StageDefinition, reason: str | None = None) -> StageResult:
    """构造阶段跳过记录。

    参数:
        definition: 被跳过阶段的静态定义。

    返回:
        原因中包含阶段成熟度的 ``StageResult``。
    """

    return StageResult(
        definition,
        StageRunStatus.SKIPPED,
        reason=reason or f"阶段状态为 {definition.maturity.value}",
    )


def run_generation_process(config: CaseConfig) -> GenerationProcessResult:
    """按顺序执行稳定和实验阶段。

    参数:
        config: 生成过程共享的病例配置。

    返回:
        包含七个阶段状态和已完成输出的 ``GenerationProcessResult``。

    算法说明:
        执行顺序为：第 1 步完成、第 2 步按配置执行或跳过、第 3 步完成、
        第 4 步完成、第 5 步按配置执行或跳过、第 6 步完成、第 7 步按
        手机避障配置执行或跳过。
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
    if config.tooth_identification is None:
        results.append(
            _skipped(STAGES[1], "病例未配置牙位工作流 case.yaml；不生成 FDI 观察窗")
        )
    else:
        context.tooth_identification = identify_tooth_positions(
            config,
        )
        results.append(
            StageResult(STAGES[1], StageRunStatus.COMPLETED, context.tooth_identification)
        )

    context.window_cutouts = plan_window_cutouts(
        case,
        context.sleeve_generation,
        context.tooth_identification,
    )
    results.append(StageResult(STAGES[2], StageRunStatus.COMPLETED, context.window_cutouts))

    if config.guide_component_bridge.enabled:
        context.guide_component_bridge = select_guide_component_bridge(context)

    if config.guide_terminal_u_extension.enabled:
        context.guide_terminal_u_extension = select_guide_terminal_u_extension(context)

    context.template_link_points = select_template_link_points(
        TemplateLinkPointContext(
            case,
            context.sleeve_generation,
            context.window_cutouts,
            context.tooth_identification,
        ),
        TemplatePointSelectionConfig(
            template_clearance_mm=(
                config.geometry.connector_radius_mm
                + config.geometry.fusion_voxel_size_mm
            ),
            connector_radius_mm=config.geometry.connector_radius_mm,
        ),
    )
    context.terminal_distal_common_node = (
        context.template_link_points.template_points.terminal_distal_common_node
    )
    results.append(StageResult(STAGES[3], StageRunStatus.COMPLETED, context.template_link_points))

    if config.press_beam.mode is PressBeamMode.DISABLED:
        results.append(_skipped(STAGES[4], "病例未启用 Y 型按压梁"))
    else:
        context.press_beam_points = select_press_beam_points(context)
        results.append(
            StageResult(STAGES[4], StageRunStatus.COMPLETED, context.press_beam_points)
        )
    context.point_linking = link_selected_points(
        context.template_link_points,
        PointLinkingConfig(
            radius_mm=config.geometry.connector_radius_mm,
            include_lower_main=config.geometry.connection_blocks.lower_main,
            include_upper_main=config.geometry.connection_blocks.upper_main,
            include_press_beam=config.geometry.connection_blocks.press_beam,
            stop_platform_front_avoidance_mm=(
                config.geometry.sleeve_stop_front_avoidance_mm
            ),
            connector_guide_endpoint=config.geometry.connector_guide_endpoint,
        ),
        context.press_beam_points,
        context.guide_component_bridge,
        context.guide_terminal_u_extension,
        context.sleeve_generation.template_frame.normal * -1.0,
    )
    results.append(StageResult(STAGES[5], StageRunStatus.COMPLETED, context.point_linking))
    if not config.handpiece_avoidance:
        results.append(_skipped(STAGES[6], "病例未配置牙科手机避障"))
    else:
        context.clearance_adjustment = adjust_clearance(context)
        results.append(
            StageResult(STAGES[6], StageRunStatus.COMPLETED, context.clearance_adjustment)
        )
    process = GenerationProcessResult(context, tuple(results))
    from twin_guide.stage_artifacts import write_stage_result_documents

    write_stage_result_documents(process)
    return process

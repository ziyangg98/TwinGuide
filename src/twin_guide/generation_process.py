"""按顺序执行各生成阶段。"""

from __future__ import annotations

from dataclasses import replace
from time import perf_counter

from twin_guide.case_analysis import analyze_case
from twin_guide.clearance_adjustment import adjust_clearance
from twin_guide.config import CaseConfig, PressBeamMode
from twin_guide.guide_component_bridge import select_guide_component_bridge
from twin_guide.guide_terminal_u_extension import select_guide_terminal_u_extension
from twin_guide.point_linking import (
    PointLinkingConfig,
    PointLinkingPlan,
    link_selected_points,
)
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

_LAST_PROCESS: GenerationProcessResult | None = None
_LAST_PROCESS_KEY: tuple[str, bool, bool, bool] | None = None


def _reuse_numerically_unchanged_links(
    current: PointLinkingPlan,
    previous: PointLinkingPlan | None,
) -> PointLinkingPlan:
    """稳定复用仅受浮点选点噪声影响的单根连接梁。"""

    if previous is None:
        return current
    old_links = {
        (link.guide_index, link.sleeve_label, link.link_label): link
        for link in previous.links
    }
    links = []
    for link in current.links:
        old = old_links.get((link.guide_index, link.sleeve_label, link.link_label))
        if (
            old is not None
            and len(old.centerline) == len(link.centerline)
            and max(
                (first.distance_to(second) for first, second in zip(
                    old.centerline,
                    link.centerline,
                    strict=True,
                )),
                default=0.0,
            )
            <= 5e-5
        ):
            links.append(old)
        else:
            links.append(link)
    return replace(current, links=tuple(links))


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


def run_generation_process(
    config: CaseConfig,
    *,
    require_observation_qa: bool = True,
    write_stage_documents: bool = True,
    include_clearance_adjustment: bool = True,
    include_observation_window_geometry: bool = True,
    validate_cached_geometry: bool = True,
    force_rebuild: bool = False,
    changed_feature_ids: tuple[str, ...] = (),
) -> GenerationProcessResult:
    """按顺序执行稳定和实验阶段。

    参数:
        config: 生成过程共享的病例配置。
        require_observation_qa: 是否要求观察窗规划通过轴向余隙 QA。
        write_stage_documents: 是否写出阶段 JSON/文档，供报告和 UI 复用。
        include_clearance_adjustment: 是否执行第 7 阶段牙科手机余隙调整。
        include_observation_window_geometry: 是否生成观察窗实体切割网格。

    返回:
        包含七个阶段状态和已完成输出的 ``GenerationProcessResult``。

    算法说明:
        执行顺序为：第 1 步完成、第 2 步按配置执行或跳过、第 3 步完成、
        第 4 步完成、第 5 步按配置执行或跳过、第 6 步完成、第 7 步按
        手机避障配置执行或跳过。
        跳过阶段只写入 ``StageResult.reason``，对应上下文字段保持 ``None``。
    """

    global _LAST_PROCESS, _LAST_PROCESS_KEY
    from twin_guide.editor_plan import editor_plan_fingerprint

    structure_fingerprint = editor_plan_fingerprint(config)
    process_key = (
        structure_fingerprint,
        require_observation_qa,
        include_clearance_adjustment,
        include_observation_window_geometry,
    )
    previous = (
        _LAST_PROCESS
        if changed_feature_ids
        and not force_rebuild
        and process_key == _LAST_PROCESS_KEY
        else None
    )
    previous_context = None if previous is None else previous.context
    changed = set(changed_feature_ids)
    cache_hits: list[str] = []
    context = GenerationContext(config=config)
    results: list[StageResult] = []
    timings: dict[str, float] = {}

    started = perf_counter()
    case = analyze_case(config, force_rebuild=force_rebuild)
    context.case = case
    context.sleeve_generation = SleeveGenerationResult(
        case.guide_sleeves,
        case.template_frame,
    )
    results.append(
        StageResult(STAGES[0], StageRunStatus.COMPLETED, context.sleeve_generation)
    )
    timings[STAGES[0].key] = perf_counter() - started
    started = perf_counter()
    if config.tooth_identification is None:
        results.append(
            _skipped(STAGES[1], "病例未配置牙位工作流 case.yaml；不生成 FDI 观察窗")
        )
    else:
        if previous_context is not None and previous_context.tooth_identification is not None:
            context.tooth_identification = previous_context.tooth_identification
            cache_hits.append(STAGES[1].key)
        else:
            context.tooth_identification = identify_tooth_positions(
                config,
                regenerate=force_rebuild,
                write_overview=write_stage_documents,
            )
        results.append(
            StageResult(STAGES[1], StageRunStatus.COMPLETED, context.tooth_identification)
        )
    timings[STAGES[1].key] = perf_counter() - started

    started = perf_counter()
    windows_changed = any(
        feature_id.startswith(("sleeve:", "operation_window:", "observation_window:"))
        for feature_id in changed
    )
    if (
        previous_context is not None
        and not windows_changed
        and previous_context.window_cutouts is not None
    ):
        context.window_cutouts = previous_context.window_cutouts
        cache_hits.append(STAGES[2].key)
    else:
        context.window_cutouts = plan_window_cutouts(
            case,
            context.sleeve_generation,
            context.tooth_identification,
            require_observation_qa=require_observation_qa,
            include_observation_window_geometry=include_observation_window_geometry,
            force_rebuild=force_rebuild,
        )
    results.append(StageResult(STAGES[2], StageRunStatus.COMPLETED, context.window_cutouts))
    timings[STAGES[2].key] = perf_counter() - started

    started = perf_counter()
    template_points_reusable = (
        previous_context is not None
        and not windows_changed
        and previous_context.template_link_points is not None
    )
    if template_points_reusable:
        context.guide_component_bridge = previous_context.guide_component_bridge
        context.guide_terminal_u_extension = (
            previous_context.guide_terminal_u_extension
        )
        context.template_link_points = previous_context.template_link_points
        cache_hits.append(STAGES[3].key)
    else:
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
    timings[STAGES[3].key] = perf_counter() - started

    started = perf_counter()
    press_changed = any(
        feature_id.startswith("press_anchor:") or feature_id == "press_junction"
        for feature_id in changed
    )
    changed_sleeves = {
        int(feature_id.rsplit("_", 1)[1])
        for feature_id in changed
        if feature_id.startswith("sleeve:guide_")
    }
    previous_press = (
        None if previous_context is None else previous_context.press_beam_points
    )
    sleeve_edit_keeps_press_plan = bool(
        changed_sleeves
        and all(feature_id.startswith("sleeve:guide_") for feature_id in changed)
        and previous_press is not None
        and (
            previous_press.sleeve_anchor is None
            or previous_press.sleeve_anchor.guide_index not in changed_sleeves
        )
    )
    if config.press_beam.mode is PressBeamMode.DISABLED:
        results.append(_skipped(STAGES[4], "病例未启用 Y 型按压梁"))
    elif (
        (template_points_reusable or sleeve_edit_keeps_press_plan)
        and not press_changed
        and previous_press is not None
    ):
        context.press_beam_points = previous_press
        cache_hits.append(STAGES[4].key)
        results.append(
            StageResult(STAGES[4], StageRunStatus.COMPLETED, context.press_beam_points)
        )
    else:
        context.press_beam_points = select_press_beam_points(context)
        results.append(
            StageResult(STAGES[4], StageRunStatus.COMPLETED, context.press_beam_points)
        )
    timings[STAGES[4].key] = perf_counter() - started
    started = perf_counter()
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
            stop_platform_overrides=config.editor_overrides.connector_avoidance,
            connector_guide_endpoint=config.geometry.connector_guide_endpoint,
        ),
        context.press_beam_points,
        context.guide_component_bridge,
        context.guide_terminal_u_extension,
        context.sleeve_generation.template_frame.normal * -1.0,
    )
    context.point_linking = _reuse_numerically_unchanged_links(
        context.point_linking,
        None if previous_context is None else previous_context.point_linking,
    )
    results.append(StageResult(STAGES[5], StageRunStatus.COMPLETED, context.point_linking))
    timings[STAGES[5].key] = perf_counter() - started
    started = perf_counter()
    if not include_clearance_adjustment:
        results.append(_skipped(STAGES[6], "当前任务不需要牙科手机避障"))
    elif not config.handpiece_avoidance:
        results.append(_skipped(STAGES[6], "病例未配置牙科手机避障"))
    else:
        context.clearance_adjustment = adjust_clearance(
            context,
            validate_cached_geometry=validate_cached_geometry,
            force_rebuild=force_rebuild,
        )
        results.append(
            StageResult(STAGES[6], StageRunStatus.COMPLETED, context.clearance_adjustment)
        )
    timings[STAGES[6].key] = perf_counter() - started
    process = GenerationProcessResult(
        context,
        tuple(results),
        timings,
        tuple(cache_hits),
    )
    if write_stage_documents:
        from twin_guide.stage_artifacts import write_stage_result_documents

        write_stage_result_documents(process)
    _LAST_PROCESS = process
    _LAST_PROCESS_KEY = process_key
    return process

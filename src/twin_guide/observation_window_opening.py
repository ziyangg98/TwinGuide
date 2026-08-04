"""TwinGuide 第 3 阶段的 FDI 轴扫掠观察窗适配层。"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

from twin_guide.config import CaseConfig
from twin_guide.errors import GeometryError
from twin_guide.geometry import Vec3
from twin_guide.models import ProfileWindowCutout
from twin_guide.tooth_identification import ToothIdentificationResult

INTEGRATION_REPORT_NAME = "manifest.json"
OBSERVATION_OPENING_ALGORITHM_VERSION = (
    "axis_sweep_dental_constrained_direct_v4"
)


def _validate_axis_sweep_contract(
    mapping: dict[str, object], config: CaseConfig
) -> None:
    """要求映射只提供当前统一的 0.2 mm/90° 轴扫掠定义。"""

    windows = mapping.get("observation_windows")
    if not isinstance(windows, list) or not windows:
        raise GeometryError("牙位映射报告中没有观察窗")
    for index, value in enumerate(windows):
        if not isinstance(value, dict):
            raise GeometryError(f"observation_windows[{index}] 必须为对象")
        window_id = str(value.get("id", index))
        if value.get("opening_geometry") != "axis_sweep":
            raise GeometryError(
                f"观察窗 {window_id!r} 不是 axis_sweep；请使用当前牙位映射方法重新映射"
            )
        definition = value.get("axis_sweep")
        if not isinstance(definition, dict):
            raise GeometryError(f"观察窗 {window_id!r} 缺少 axis_sweep 定义")
        editor_overrides = getattr(config, "editor_overrides", None)
        override = (
            None
            if editor_overrides is None
            else editor_overrides.observation_window_for(window_id)
        )
        expected_drop = (
            config.windows.observation_axis_drop_mm
            if override is None
            else override.axis_drop_mm
        )
        expected_angle = (
            config.windows.observation_sweep_angle_degrees
            if override is None
            else override.sweep_angle_degrees
        )
        drop = float(definition.get("axis_drop_mm", -1.0))
        angle = float(definition.get("sweep_angle_deg", -1.0))
        if abs(drop - expected_drop) > 1e-6:
            raise GeometryError(
                f"观察窗 {window_id!r} 映射高度为 {drop:g} mm，"
                f"但 TwinGuide 配置要求 {expected_drop:g} mm"
            )
        if abs(angle - expected_angle) > 1e-6:
            raise GeometryError(
                f"观察窗 {window_id!r} 扫掠角为 {angle:g}°，"
                f"但 TwinGuide 配置要求 {expected_angle:g}°"
            )


def _mapping_with_editor_overrides(
    config: CaseConfig,
    tooth_identification: ToothIdentificationResult,
    output_root: Path,
) -> tuple[dict[str, object], Path]:
    """生成只供第 3 阶段使用的观察窗映射副本。"""

    editor_overrides = getattr(config, "editor_overrides", None)
    if editor_overrides is None or not editor_overrides.observation_windows:
        return (
            tooth_identification.mapping_report,
            tooth_identification.mapping_report_path.resolve(),
        )
    mapping = deepcopy(tooth_identification.mapping_report)
    raw_windows = mapping.get("observation_windows")
    if not isinstance(raw_windows, list):
        raise GeometryError("牙位映射报告中没有可覆盖的观察窗")
    positions = {item.fdi: item for item in tooth_identification.positions}
    for raw_window in raw_windows:
        if not isinstance(raw_window, dict):
            continue
        window_id = str(raw_window.get("id", ""))
        override = editor_overrides.observation_window_for(window_id)
        if override is None:
            continue
        try:
            start_position = positions[override.start_fdi]
            end_position = positions[override.end_fdi]
        except KeyError as error:
            raise GeometryError(
                f"观察窗 {window_id!r} 端点必须吸附到当前病例的有效 FDI"
            ) from error
        definition = raw_window.get("axis_sweep")
        if not isinstance(definition, dict):
            raise GeometryError(f"观察窗 {window_id!r} 缺少 axis_sweep 定义")
        direction_values = definition.get("zero_degree_occlusal_direction_global")
        if not isinstance(direction_values, list) or len(direction_values) != 3:
            raise GeometryError(f"观察窗 {window_id!r} 缺少牙合方向")
        occlusal = Vec3(*(float(value) for value in direction_values)).normalized()
        start = start_position.guide_top or start_position.crown_point
        end = end_position.guide_top or end_position.crown_point
        start = start - occlusal * override.axis_drop_mm
        end = end - occlusal * override.axis_drop_mm
        raw_window.update(
            start_fdi=override.start_fdi,
            end_fdi=override.end_fdi,
            height_mm=override.height_mm,
        )
        definition.update(
            axis_start_global_mm=[start.x, start.y, start.z],
            axis_end_global_mm=[end.x, end.y, end.z],
            axis_drop_mm=override.axis_drop_mm,
            sweep_angle_deg=override.sweep_angle_degrees,
        )
    derived_path = output_root / "editor-observation-mapping.json"
    derived_path.write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return mapping, derived_path


def _mapping_digest(mapping: dict[str, object]) -> str:
    """散列观察窗语义，忽略每次运行都会变化的报告元数据。"""

    semantic = deepcopy(mapping)
    for key in ("created_at", "sources", "outputs"):
        semantic.pop(key, None)
    encoded = json.dumps(
        semantic,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _fingerprint(config: CaseConfig, mapping: dict[str, object]) -> dict[str, object]:
    """生成用于安全复用既有开口产物的输入指纹。"""

    template_stat = config.inputs.template.stat()
    dental_stat = config.inputs.patient_dentition.stat()
    return {
        "algorithm_version": OBSERVATION_OPENING_ALGORITHM_VERSION,
        "mapping_sha256": _mapping_digest(mapping),
        "template": str(config.inputs.template),
        "template_size": template_stat.st_size,
        "template_mtime_ns": template_stat.st_mtime_ns,
        "dental": str(config.inputs.patient_dentition),
        "dental_size": dental_stat.st_size,
        "dental_mtime_ns": dental_stat.st_mtime_ns,
        "axis_drop_mm": config.windows.observation_axis_drop_mm,
        "sweep_angle_degrees": config.windows.observation_sweep_angle_degrees,
    }


def _axis_points(definition: dict[str, object]) -> tuple[Vec3, ...]:
    """从最终轴定义恢复包含局部高度修正的轴采样点。"""

    try:
        start_values = definition["axis_start_global_mm"]
        end_values = definition["axis_end_global_mm"]
        count = int(definition["axis_section_count"])
    except (KeyError, TypeError, ValueError) as error:
        raise GeometryError("开口报告的 axis_sweep 定义不完整") from error
    if (
        not isinstance(start_values, list)
        or not isinstance(end_values, list)
        or len(start_values) != 3
        or len(end_values) != 3
        or count < 2
    ):
        raise GeometryError("开口报告的 axis_sweep 坐标或截面数无效")
    start = Vec3(*(float(value) for value in start_values))
    end = Vec3(*(float(value) for value in end_values))
    return tuple(
        start
        + (end - start) * (index / (count - 1))
        for index in range(count)
    )


def _profile_from_report(report_path: Path) -> ProfileWindowCutout:
    """从最终开口报告构造 TwinGuide 变截面切割计划。"""

    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GeometryError(f"无法读取观察窗开口报告 {report_path}：{error}") from error
    outputs = report.get("outputs")
    windows = report.get("windows")
    if not isinstance(outputs, dict) or not isinstance(windows, list) or not windows:
        raise GeometryError("观察窗开口报告缺少 outputs 或 windows")
    cutter = Path(str(outputs.get("combined_cutter_ply", ""))).resolve()
    if cutter.suffix.lower() != ".ply" or not cutter.is_file():
        raise GeometryError(f"观察窗组合 cutter 不存在：{cutter}")
    window_ids = []
    crest_points = []
    window_crest_points = []
    for value in windows:
        if not isinstance(value, dict):
            raise GeometryError("观察窗开口报告的 windows 项必须为对象")
        window_ids.append(str(value.get("id", "")))
        definition = value.get("axis_sweep")
        if not isinstance(definition, dict):
            raise GeometryError("观察窗开口报告缺少最终 axis_sweep 定义")
        points = _axis_points(definition)
        window_crest_points.append(points)
        crest_points.extend(points)
    return ProfileWindowCutout(
        name="fdi_axis_sweep_observation_windows",
        cutter_mesh_path=cutter,
        report_path=report_path,
        window_ids=tuple(window_ids),
        crest_points=tuple(crest_points),
        window_crest_points=tuple(window_crest_points),
    )


def build_observation_window_opening(
    config: CaseConfig,
    tooth_identification: ToothIdentificationResult,
    *,
    require_qa: bool = True,
    regenerate: bool = False,
    fast_preview: bool = False,
) -> ProfileWindowCutout:
    """使用现有牙位映射直接求解并返回轴扫掠 cutter。

    参数:
        config: 包含统一开口参数、导板和口扫路径的病例配置。
        tooth_identification: 第 2 阶段现场生成的内存牙位映射结果。

    返回:
        指向 cutter、约束报告和轴采样点的切割计划。

    本函数不调用牙位识别或牙位编号逻辑，映射报告是只读输入。
    """

    output_root = (
        config.output_directory / ".cache" / "stage-03-cutout-planning"
    )
    output_root.mkdir(parents=True, exist_ok=True)
    mapping, mapping_path = _mapping_with_editor_overrides(
        config,
        tooth_identification,
        output_root,
    )
    _validate_axis_sweep_contract(mapping, config)
    integration_path = output_root / INTEGRATION_REPORT_NAME
    fingerprint = _fingerprint(config, mapping)
    if not regenerate and integration_path.is_file():
        try:
            cached = json.loads(integration_path.read_text(encoding="utf-8"))
            cached_report = Path(str(cached["final_report"])).resolve()
            if cached.get("fingerprint") == fingerprint and cached_report.is_file():
                cached_qa = cached.get("QA")
                qa_passed = bool(
                    isinstance(cached_qa, dict)
                    and cached_qa
                    and all(cached_qa.values())
                )
                if not require_qa or qa_passed:
                    return _profile_from_report(cached_report)
        except (KeyError, OSError, json.JSONDecodeError):
            pass

    # 延迟导入项目内观察窗引擎，使纯配置测试不必加载完整网格依赖。
    try:
        from twin_guide.observation_window_engine import (
            ObservationWindowRequest,
            build_preview,
            run,
        )
    except ImportError as error:
        raise GeometryError(
            "无法加载 TwinGuide 内部观察窗算法；请检查项目依赖安装"
        ) from error

    request = ObservationWindowRequest(
        case=mapping_path,
        mapping_report=mapping_path,
        source=config.inputs.template,
        output_dir=output_root,
        side_extension_mm=0.4,
        wall_overcut_mm=0.4,
        following_wall_safety_mm=0.10,
        axis_core_overcut_mm=0.30,
        minimum_axis_visibility_row_fraction=0.50,
        minimum_axis_clear_corridor_fraction=0.95,
        union_batch_size=16,
        fragment_volume_tolerance_mm3=2.0,
        minimum_removed_volume_mm3=1.0,
        residual_volume_tolerance_mm3=1e-4,
        # difference 与 intersection 是两次独立的浮点网格布尔运算。
        # 0.05 mm³ 是 12 病例回归覆盖的绝对数值底线；大切割仍受 0.01% 限制。
        volume_identity_tolerance_mm3=5e-2,
        volume_identity_relative_tolerance=1e-4,
    )
    try:
        report = (
            build_preview(request, force_rebuild=regenerate)
            if fast_preview
            else run(request)
        )
    except Exception as error:
        raise GeometryError(f"轴扫掠观察窗生成失败：{error}") from error
    qa_passed = bool(report["QA"]) and all(report["QA"].values())
    if require_qa and not qa_passed:
        failed_checks = [name for name, passed in report["QA"].items() if not passed]
        raise GeometryError(
            "轴扫观察窗未通过最终 QA：" + "、".join(failed_checks)
        )
    final_report = Path(str(report["outputs"]["report_json"])).resolve()
    integration = {
        "status": "complete" if qa_passed else "preview_qa_failed",
        "fingerprint": fingerprint,
        "mapping_report": str(mapping_path),
        "final_report": str(final_report),
        "final_cutter": str(report["outputs"]["combined_cutter_ply"]),
        "QA": report["QA"],
        "constraint_solution": report.get("constraint_solution"),
    }
    integration_path.write_text(
        json.dumps(integration, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return _profile_from_report(final_report)


__all__ = ["build_observation_window_opening"]

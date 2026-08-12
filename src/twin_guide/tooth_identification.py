"""第 2 步：现场执行并校验统一的 FDI 牙位与导板映射。"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from twin_guide.config import CaseConfig, Jaw
from twin_guide.config.loading import load_case_yaml
from twin_guide.errors import GeometryError
from twin_guide.geometry import Vec3


@dataclass(frozen=True, slots=True)
class ToothPosition:
    """一个已识别现存牙位的世界坐标结果。"""

    fdi: int
    crown_point: Vec3
    guide_top: Vec3 | None
    arch_s_mm: float
    local_tangent: Vec3
    local_outward: Vec3


@dataclass(frozen=True, slots=True)
class ObservationWindowMapping:
    """一个 FDI 观察窗及其顶部脊线采样。"""

    window_id: str
    start_fdi: int
    end_fdi: int
    height_mm: float
    top_open: bool
    crest_points: tuple[Vec3, ...]


@dataclass(frozen=True, slots=True)
class ToothIdentificationResult:
    """本次运行中已通过全部安全门的牙位与观察窗语义映射。"""

    mapping_report_path: Path
    mapping_report: dict[str, object]
    jaw: str
    fdi_order: tuple[int, ...]
    present_teeth: tuple[int, ...]
    missing_teeth: tuple[int, ...]
    excluded_teeth: tuple[int, ...]
    positions: tuple[ToothPosition, ...]
    windows: tuple[ObservationWindowMapping, ...]


def _read_report(path: Path, name: str) -> dict[str, object]:
    """读取一个 JSON 报告并校验根对象。"""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GeometryError(f"无法读取{name} {path}：{error}") from error
    if not isinstance(value, dict):
        raise GeometryError(f"{name}根值必须为对象：{path}")
    return value


def _require_complete_qa(report: dict[str, object], expected_status: str, name: str) -> None:
    """要求报告状态正确且每个 QA 项均通过。"""

    if report.get("status") != expected_status:
        raise GeometryError(f"{name}状态不是 {expected_status}")
    qa = report.get("QA")
    if not isinstance(qa, dict) or not qa or not all(value is True for value in qa.values()):
        raise GeometryError(f"{name}没有通过全部 QA 安全门")


def _mapping(value: object, name: str) -> dict[str, object]:
    """要求报告字段为字符串键对象。"""

    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise GeometryError(f"{name}必须为对象")
    return value


def _sequence(value: object, name: str) -> list[object]:
    """要求报告字段为数组。"""

    if not isinstance(value, list):
        raise GeometryError(f"{name}必须为数组")
    return value


def _vec3(value: object, name: str) -> Vec3:
    """将三元素数值数组转换为世界坐标向量。"""

    values = _sequence(value, name)
    invalid_value = any(
        isinstance(item, bool) or not isinstance(item, int | float) for item in values
    )
    if len(values) != 3 or invalid_value:
        raise GeometryError(f"{name}必须为三元素数值数组")
    return Vec3(*(float(item) for item in values))


def _resolved_report_path(value: object, report_path: Path, name: str) -> Path:
    """解析报告中绝对或相对于报告目录的文件路径。"""

    if not isinstance(value, str) or not value:
        raise GeometryError(f"{name}必须为非空路径")
    raw_path = Path(value)
    path = (
        raw_path.resolve() if raw_path.is_absolute() else (report_path.parent / raw_path).resolve()
    )
    if not path.is_file():
        raise GeometryError(f"{name}文件不存在：{path}")
    return path


def _expected_mapping_jaw(jaw: Jaw) -> str:
    """将 TwinGuide 上下颌枚举转换为映射报告术语。"""

    return "maxillary" if jaw is Jaw.UPPER else "mandibular"


def _tooth_positions(mapping_report: dict[str, object]) -> tuple[ToothPosition, ...]:
    """从映射报告读取全部现存牙三维位置。"""

    positions = []
    for index, raw_slot in enumerate(_sequence(mapping_report.get("tooth_slots"), "tooth_slots")):
        slot = _mapping(raw_slot, f"tooth_slots[{index}]")
        if slot.get("status") != "present":
            continue
        guide_top_value = slot.get("guide_top_global_mm")
        positions.append(
            ToothPosition(
                fdi=int(slot["FDI"]),
                crown_point=_vec3(
                    slot.get("dental_crown_point_global_mm"),
                    f"tooth_slots[{index}].dental_crown_point_global_mm",
                ),
                guide_top=None
                if guide_top_value is None
                else _vec3(guide_top_value, f"tooth_slots[{index}].guide_top_global_mm"),
                arch_s_mm=float(slot["arch_s_mm"]),
                local_tangent=_vec3(
                    slot.get("local_tangent_global"),
                    f"tooth_slots[{index}].local_tangent_global",
                ).normalized(),
                local_outward=_vec3(
                    slot.get("local_outward_global"),
                    f"tooth_slots[{index}].local_outward_global",
                ).normalized(),
            )
        )
    if not positions:
        raise GeometryError("牙位映射报告中没有现存牙三维位置")
    return tuple(positions)


def _window_mappings(
    mapping_report: dict[str, object],
) -> tuple[ObservationWindowMapping, ...]:
    """从牙位映射读取观察窗端点和语义轴/顶部脊线。"""

    windows = []
    for index, raw_window in enumerate(
        _sequence(mapping_report.get("observation_windows"), "observation_windows")
    ):
        window = _mapping(raw_window, f"observation_windows[{index}]")
        window_id = str(window.get("id", ""))
        if not window_id:
            raise GeometryError(f"observation_windows[{index}].id 不得为空")
        axis_definition = window.get("axis_sweep")
        if isinstance(axis_definition, dict):
            axis = _mapping(axis_definition, f"observation_windows[{index}].axis_sweep")
            start = _vec3(
                axis.get("axis_start_global_mm"),
                f"observation_windows[{index}].axis_sweep.axis_start_global_mm",
            )
            end = _vec3(
                axis.get("axis_end_global_mm"),
                f"observation_windows[{index}].axis_sweep.axis_end_global_mm",
            )
            section_count = int(axis.get("axis_section_count", 0))
            if section_count < 2:
                raise GeometryError(f"观察窗 {window_id!r} 的语义轴截面数不足")
            crest_points = tuple(
                start + (end - start) * (section / (section_count - 1))
                for section in range(section_count)
            )
        else:
            samples = _sequence(window.get("samples"), f"observation_windows[{index}].samples")
            crest_points = tuple(
                _vec3(
                    _mapping(sample, f"observation_windows[{index}].samples[]").get(
                        "true_top_global_mm"
                    ),
                    f"observation_windows[{index}].samples[].true_top_global_mm",
                )
                for sample in samples
            )
            if len(crest_points) < 2:
                raise GeometryError(f"观察窗 {window_id!r} 的映射截面数不足")
        windows.append(
            ObservationWindowMapping(
                window_id=window_id,
                start_fdi=int(window["start_fdi"]),
                end_fdi=int(window["end_fdi"]),
                height_mm=float(window["height_mm"]),
                top_open=bool(window["top_open"]),
                crest_points=crest_points,
            )
        )
    if not windows:
        raise GeometryError("牙位映射报告中没有观察窗")
    return tuple(windows)


WORKFLOW_SCHEMA_VERSION = "1.6-standard-stage-output"
STAGE_RESULT_SCHEMA = "twin-guide.stage-result/1.0"
WORKFLOW_RESULT_NAME = "stage-02-tooth-mapping.json"
WORKFLOW_OVERVIEW_NAME = "stage-02-tooth-mapping.png"
WORKFLOW_CACHE_NAME = "stage-02-tooth-mapping"
WORKFLOW_CACHE_RESULT_NAME = "result.json"


def _sha256(path: Path) -> str:
    """计算一个输入或报告文件的稳定摘要。"""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _input_fingerprint(config: CaseConfig) -> dict[str, object]:
    """记录足以拒绝陈旧牙位结果的输入指纹。"""

    inputs = config.tooth_identification
    if inputs is None:
        raise GeometryError("病例未配置牙位识别 case.yaml")
    case_value = load_case_yaml(inputs.case_yaml)
    if not isinstance(case_value, dict):
        raise GeometryError("牙位识别病例 YAML 根值必须为对象")
    case_value.pop("editor_overrides", None)
    case_value.pop("review", None)
    case_semantic_sha256 = hashlib.sha256(
        json.dumps(
            case_value,
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return {
        "workflow_schema": WORKFLOW_SCHEMA_VERSION,
        "case_id": config.case_id,
        "jaw": config.jaw.value,
        "case_semantic_sha256": case_semantic_sha256,
        "dental": str(config.inputs.patient_dentition.resolve()),
        "dental_sha256": _sha256(config.inputs.patient_dentition),
        "guide": str(config.inputs.template.resolve()),
        "guide_sha256": _sha256(config.inputs.template),
    }


def stage_2_mapping_payload(document: dict[str, object]) -> dict[str, object]:
    """将统一阶段结果转换为几何算法使用的牙位映射。"""

    if document.get("schema_version") != STAGE_RESULT_SCHEMA:
        return document
    stage = _mapping(document.get("stage"), "stage")
    inputs = _mapping(document.get("inputs"), "inputs")
    result = _mapping(document.get("result"), "result")
    quality = _mapping(document.get("quality"), "quality")
    artifacts = _mapping(document.get("artifacts"), "artifacts")
    return {
        "schema_version": WORKFLOW_SCHEMA_VERSION,
        "created_at": stage.get("completed_at"),
        "status": (
            "tooth_guide_mapping_complete"
            if stage.get("status") == "completed"
            else "tooth_guide_mapping_needs_review"
        ),
        "case": document.get("case"),
        "sources": inputs.get("sources"),
        "semantics": result.get("semantics"),
        "coordinate_system": result.get("coordinate_system"),
        "mapping_parameters": document.get("parameters"),
        "tooth_slots": result.get("teeth"),
        "observation_windows": result.get("observation_windows"),
        "diagnostics": quality.get("diagnostics"),
        "QA": quality.get("checks"),
        "outputs": {
            "report_json": artifacts.get("result_json"),
            "overview_png": artifacts.get("overview_png"),
        },
    }


def _validated_result(
    config: CaseConfig,
    mapping_report_path: Path,
    mapping_report: dict[str, object],
) -> ToothIdentificationResult:
    """把本次生成的报告转换成 TwinGuide 的内存阶段结果。"""

    mapping_report = stage_2_mapping_payload(mapping_report)
    _require_complete_qa(mapping_report, "tooth_guide_mapping_complete", "牙位映射报告")

    semantics = _mapping(mapping_report.get("semantics"), "semantics")
    jaw = str(semantics.get("jaw", ""))
    if jaw != _expected_mapping_jaw(config.jaw):
        raise GeometryError(f"牙位报告上下颌 {jaw!r} 与 TwinGuide 病例 {config.jaw.value!r} 不一致")

    mapping_sources = _mapping(mapping_report.get("sources"), "mapping.sources")
    mapped_guide = _resolved_report_path(
        mapping_sources.get("guide"), mapping_report_path, "mapping.sources.guide"
    )
    if mapped_guide != config.inputs.template.resolve():
        raise GeometryError("牙位报告使用的导板 STL 与 TwinGuide 输入不一致")
    mapped_dental = _resolved_report_path(
        mapping_sources.get("dental"), mapping_report_path, "mapping.sources.dental"
    )
    if mapped_dental != config.inputs.patient_dentition.resolve():
        raise GeometryError("牙位报告使用的口扫 STL 与 TwinGuide 输入不一致")
    return ToothIdentificationResult(
        mapping_report_path=mapping_report_path,
        mapping_report=mapping_report,
        jaw=jaw,
        fdi_order=tuple(int(value) for value in _sequence(semantics.get("FDI_order"), "FDI_order")),
        present_teeth=tuple(
            int(value) for value in _sequence(semantics.get("present_teeth"), "present_teeth")
        ),
        missing_teeth=tuple(
            int(value)
            for value in _sequence(
                semantics.get("missing_teeth_without_geometric_centres"),
                "missing_teeth_without_geometric_centres",
            )
        ),
        excluded_teeth=tuple(
            int(value)
            for value in _sequence(
                semantics.get("excluded_teeth_without_geometric_centres"),
                "excluded_teeth_without_geometric_centres",
            )
        ),
        positions=_tooth_positions(mapping_report),
        windows=_window_mappings(mapping_report),
    )


def _ensure_overview(config: CaseConfig, cache_root: Path) -> None:
    """使用已缓存的牙位识别结果补充正式阶段总览图。"""

    raw_overview_path = cache_root / "raw-overview.png"
    overview_path = config.output_directory / WORKFLOW_OVERVIEW_NAME
    if not raw_overview_path.is_file():
        from twin_guide.tooth_mapping.guide_mapping import (
            GuideMappingRequest,
            map_recognized_teeth_to_guide,
        )
        from twin_guide.tooth_mapping.tooth_recognition import (
            load_tooth_recognition_result,
        )

        recognition = load_tooth_recognition_result(cache_root / "tooth-recognition")
        mapping = map_recognized_teeth_to_guide(
            GuideMappingRequest(
                recognition=recognition,
                output_dir=cache_root / "guide-mapping",
                case_yaml=config.tooth_identification.case_yaml,
                overview_path=raw_overview_path,
            )
        )
        if not mapping.complete:
            raise GeometryError("缓存牙位结果无法生成正式阶段总览图")
    shutil.copy2(raw_overview_path, overview_path)


def _load_current_result(
    config: CaseConfig,
    *,
    write_overview: bool,
) -> ToothIdentificationResult | None:
    """仅在输入指纹完全一致时复用 TwinGuide 自己生成的当前结果。"""

    cache_root = config.output_directory / ".cache" / WORKFLOW_CACHE_NAME
    result_path = cache_root / WORKFLOW_CACHE_RESULT_NAME
    recognition_root = cache_root / "tooth-recognition"
    required_cache_paths = (
        recognition_root / "guide-surface-mapping",
        recognition_root / "crown-projection",
        recognition_root / "contact-contours",
        cache_root / "guide-mapping",
    )
    if not result_path.is_file() or not all(path.exists() for path in required_cache_paths):
        return None
    try:
        report = _read_report(result_path, "TwinGuide 第二阶段结果")
        if report.get("schema_version") != STAGE_RESULT_SCHEMA:
            return None
        inputs = _mapping(report.get("inputs"), "inputs")
        if inputs.get("fingerprint") != _input_fingerprint(config):
            return None
        result = _validated_result(config, result_path, report)
        if write_overview:
            _ensure_overview(config, cache_root)
        return result
    except (KeyError, OSError, GeometryError, ValueError):
        return None


def _run_unified_workflow(
    config: CaseConfig,
    *,
    write_overview: bool,
) -> ToothIdentificationResult:
    """调用统一识别与导板映射库，生成本次 TwinGuide 运行结果。"""

    inputs = config.tooth_identification
    if inputs is None:
        raise GeometryError("病例未配置牙位识别 case.yaml")
    workflow_root = config.output_directory
    cache_root = workflow_root / ".cache" / WORKFLOW_CACHE_NAME
    recognition_directory = cache_root / "tooth-recognition"
    mapping_directory = cache_root / "guide-mapping"
    raw_overview_path = cache_root / "raw-overview.png"
    for directory in (recognition_directory, mapping_directory):
        if directory.is_dir():
            shutil.rmtree(directory)
    try:
        from twin_guide.tooth_mapping.guide_mapping import (
            GuideMappingRequest,
            map_recognized_teeth_to_guide,
        )
        from twin_guide.tooth_mapping.tooth_recognition import (
            ToothRecognitionRequest,
            recognize_teeth,
        )

        recognition = recognize_teeth(
            ToothRecognitionRequest(
                case_yaml=inputs.case_yaml,
                output_dir=recognition_directory,
            )
        )
        if not recognition.safe_for_guide_mapping:
            raise GeometryError("本次牙位识别未通过导板映射安全门")
        guide_mapping = map_recognized_teeth_to_guide(
            GuideMappingRequest(
                recognition=recognition,
                output_dir=mapping_directory,
                case_yaml=inputs.case_yaml,
                overview_path=raw_overview_path if write_overview else None,
            )
        )
        if not guide_mapping.complete:
            raise GeometryError("本次牙位到导板映射未通过全部 QA")
    except GeometryError:
        raise
    except Exception as error:
        raise GeometryError(f"统一牙位映射执行失败：{error}") from error

    result_path = cache_root / WORKFLOW_CACHE_RESULT_NAME
    mapping = guide_mapping.mapping_report
    overview_path = workflow_root / WORKFLOW_OVERVIEW_NAME
    if write_overview:
        shutil.copy2(raw_overview_path, overview_path)
    report = {
        "schema_version": STAGE_RESULT_SCHEMA,
        "stage": {
            "number": 2,
            "key": "tooth_identification",
            "title": "牙位识别",
            "status": "completed",
            "maturity": "experimental",
            "implementation_version": WORKFLOW_SCHEMA_VERSION,
            "completed_at": mapping.get("created_at"),
        },
        "case": mapping.get("case"),
        "inputs": {
            "sources": mapping.get("sources"),
            "fingerprint": _input_fingerprint(config),
        },
        "parameters": {
            **_mapping(mapping.get("mapping_parameters"), "mapping_parameters"),
            "recognition_profile_id": recognition.profile.profile_id,
            "core_grouping_policy": recognition.profile.core_grouping_policy,
        },
        "result": {
            "semantics": mapping.get("semantics"),
            "coordinate_system": mapping.get("coordinate_system"),
            "teeth": mapping.get("tooth_slots"),
            "observation_windows": mapping.get("observation_windows"),
        },
        "quality": {
            "passed": True,
            "checks": mapping.get("QA"),
            "diagnostics": mapping.get("diagnostics"),
        },
        "artifacts": {
            "result_json": str(result_path.resolve()),
            "overview_png": str(overview_path.resolve()),
        },
    }
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return _validated_result(config, result_path, report)


def identify_tooth_positions(
    config: CaseConfig,
    *,
    regenerate: bool = False,
    write_overview: bool = True,
) -> ToothIdentificationResult:
    """执行或安全复用 TwinGuide 自己生成的统一牙位映射。

    参数:
        config: 包含口扫、导板和病例 YAML 的配置。
        regenerate: 为真时强制现场重新执行第 2 阶段。

    返回:
        已通过状态、QA、病例和路径一致性检查的本次第 2 步内存结果；
        本阶段不生成观察窗 cutter。

    异常:
        GeometryError: 识别/映射失败，或结果与当前病例不一致。
    """

    if not regenerate:
        current = _load_current_result(config, write_overview=write_overview)
        if current is not None:
            return current
    return _run_unified_workflow(config, write_overview=write_overview)


__all__ = [
    "ObservationWindowMapping",
    "ToothIdentificationResult",
    "ToothPosition",
    "identify_tooth_positions",
    "stage_2_mapping_payload",
]

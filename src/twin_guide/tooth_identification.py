"""第 2 步：现场执行并校验统一的 FDI 牙位与导板映射。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from twin_guide.config import CaseConfig, Jaw
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
    workflow_manifest_path: Path
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


def _require_complete_qa(
    report: dict[str, object], expected_status: str, name: str
) -> None:
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
        raw_path.resolve()
        if raw_path.is_absolute()
        else (report_path.parent / raw_path).resolve()
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
            samples = _sequence(
                window.get("samples"), f"observation_windows[{index}].samples"
            )
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


WORKFLOW_SCHEMA_VERSION = "1.4-twinguide-physical-guide-coverage"
WORKFLOW_DIRECTORY_NAME = "tooth_mapping"
WORKFLOW_MANIFEST_NAME = "twin_guide_tooth_mapping.json"


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
    return {
        "workflow_schema": WORKFLOW_SCHEMA_VERSION,
        "case_id": config.case_id,
        "jaw": config.jaw.value,
        "case_yaml": str(inputs.case_yaml.resolve()),
        "case_yaml_sha256": _sha256(inputs.case_yaml),
        "dental": str(config.inputs.patient_dentition.resolve()),
        "dental_sha256": _sha256(config.inputs.patient_dentition),
        "guide": str(config.inputs.template.resolve()),
        "guide_sha256": _sha256(config.inputs.template),
        "surgical_references": [
            str(path.resolve()) for path in config.inputs.guide_sleeve_assemblies
        ],
        "surgical_reference_sha256": [
            _sha256(path) for path in config.inputs.guide_sleeve_assemblies
        ],
    }


def _validated_result(
    config: CaseConfig,
    mapping_report_path: Path,
    mapping_report: dict[str, object],
    workflow_manifest_path: Path,
) -> ToothIdentificationResult:
    """把本次生成的报告转换成 TwinGuide 的内存阶段结果。"""

    _require_complete_qa(mapping_report, "tooth_guide_mapping_complete", "牙位映射报告")

    semantics = _mapping(mapping_report.get("semantics"), "semantics")
    jaw = str(semantics.get("jaw", ""))
    if jaw != _expected_mapping_jaw(config.jaw):
        raise GeometryError(
            f"牙位报告上下颌 {jaw!r} 与 TwinGuide 病例 "
            f"{config.jaw.value!r} 不一致"
        )

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
    mapped_surgical_references = {
        _resolved_report_path(value, mapping_report_path, "mapping.sources.surgical_reference")
        for value in _sequence(
            mapping_sources.get("surgical_reference"),
            "mapping.sources.surgical_reference",
        )
    }
    configured_surgical_references = {
        path.resolve() for path in config.inputs.guide_sleeve_assemblies
    }
    if not configured_surgical_references.issubset(mapped_surgical_references):
        missing = configured_surgical_references - mapped_surgical_references
        raise GeometryError(
            "牙位方向检测缺少 TwinGuide 配置的导管 STL："
            + ", ".join(str(path) for path in sorted(missing))
        )

    return ToothIdentificationResult(
        mapping_report_path=mapping_report_path,
        workflow_manifest_path=workflow_manifest_path,
        mapping_report=mapping_report,
        jaw=jaw,
        fdi_order=tuple(
            int(value) for value in _sequence(semantics.get("FDI_order"), "FDI_order")
        ),
        present_teeth=tuple(
            int(value)
            for value in _sequence(semantics.get("present_teeth"), "present_teeth")
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


def _load_current_result(config: CaseConfig) -> ToothIdentificationResult | None:
    """仅在输入指纹完全一致时复用 TwinGuide 自己生成的当前结果。"""

    workflow_root = config.output_directory / WORKFLOW_DIRECTORY_NAME
    manifest_path = workflow_root / WORKFLOW_MANIFEST_NAME
    if not manifest_path.is_file():
        return None
    try:
        manifest = _read_report(manifest_path, "TwinGuide 牙位工作流清单")
        if manifest.get("schema_version") != WORKFLOW_SCHEMA_VERSION:
            return None
        if manifest.get("input_fingerprint") != _input_fingerprint(config):
            return None
        report_path = Path(str(manifest["mapping_report"])).resolve()
        if not report_path.is_file():
            return None
        if manifest.get("mapping_report_sha256") != _sha256(report_path):
            return None
        report = _read_report(report_path, "本次牙位映射报告")
        return _validated_result(config, report_path, report, manifest_path)
    except (KeyError, OSError, GeometryError, ValueError):
        return None


def _run_unified_workflow(config: CaseConfig) -> ToothIdentificationResult:
    """调用统一识别与导板映射库，生成本次 TwinGuide 运行结果。"""

    inputs = config.tooth_identification
    if inputs is None:
        raise GeometryError("病例未配置牙位识别 case.yaml")
    workflow_root = config.output_directory / WORKFLOW_DIRECTORY_NAME
    recognition_directory = workflow_root / "01_tooth_recognition"
    mapping_directory = workflow_root / "02_guide_mapping"
    try:
        from twin_guide.tooth_mapping.guide_mapping import (
            GuideMappingRequest,
            map_recognized_teeth_to_guide,
        )
        from twin_guide.tooth_mapping.tooth_recognition import (
            ToothRecognitionRequest,
            recognize_teeth,
        )

        recognition = recognize_teeth(ToothRecognitionRequest(
            case_yaml=inputs.case_yaml,
            output_dir=recognition_directory,
        ))
        if not recognition.safe_for_guide_mapping:
            raise GeometryError("本次牙位识别未通过导板映射安全门")
        guide_mapping = map_recognized_teeth_to_guide(GuideMappingRequest(
            recognition=recognition,
            output_dir=mapping_directory,
            case_yaml=inputs.case_yaml,
        ))
        if not guide_mapping.complete:
            raise GeometryError("本次牙位到导板映射未通过全部 QA")
    except GeometryError:
        raise
    except Exception as error:
        raise GeometryError(f"统一牙位映射执行失败：{error}") from error

    report_path = guide_mapping.report_path.resolve()
    report = guide_mapping.mapping_report
    manifest_path = workflow_root / WORKFLOW_MANIFEST_NAME
    manifest = {
        "schema_version": WORKFLOW_SCHEMA_VERSION,
        "status": "complete",
        "input_fingerprint": _input_fingerprint(config),
        "recognition_profile_id": recognition.profile.profile_id,
        "core_grouping_policy": recognition.profile.core_grouping_policy,
        "recognition_manifest": str(recognition.manifest_path.resolve()),
        "guide_mapping_manifest": str(guide_mapping.manifest_path.resolve()),
        "mapping_report": str(report_path),
        "mapping_report_sha256": _sha256(report_path),
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return _validated_result(config, report_path, report, manifest_path)


def identify_tooth_positions(
    config: CaseConfig,
    *,
    regenerate: bool = False,
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
        current = _load_current_result(config)
        if current is not None:
            return current
    return _run_unified_workflow(config)


__all__ = [
    "ObservationWindowMapping",
    "ToothIdentificationResult",
    "ToothPosition",
    "identify_tooth_positions",
]

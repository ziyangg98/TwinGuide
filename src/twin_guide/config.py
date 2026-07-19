"""牙科导板构建与独立检查的配置解析。"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

from twin_guide.errors import ConfigurationError

CASE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


@dataclass(frozen=True, slots=True)
class InputMeshPaths:
    """病例的牙科导板、导套装配体和患者牙列网格路径。"""

    template: Path
    guide_sleeve_assembly: Path
    patient_dentition: Path


@dataclass(frozen=True, slots=True)
class GeometryParameters:
    """导孔切除、连接管和网格融合所需的几何参数。"""

    template_channel_radius_mm: float
    channel_axial_margin_mm: float
    connector_radius_mm: float
    fusion_voxel_size_mm: float


@dataclass(frozen=True, slots=True)
class WindowParameters:
    """操作窗在切向和次切向上的扩展余量。"""

    operation_tangent_margin_mm: float
    operation_bitangent_margin_mm: float


@dataclass(frozen=True, slots=True)
class RenderParameters:
    """诊断图和结果图的像素尺寸。"""

    width_px: int
    height_px: int


@dataclass(frozen=True, slots=True)
class HandpieceValidationParameters:
    """仅用于检查的牙科手机网格和运动采样参数。"""

    mesh_path: Path
    head_crop_radius_mm: float
    minimum_clearance_mm: float
    maximum_tilt_degrees: float
    withdrawal_distances_mm: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class ValidationParameters:
    """独立检查所需的输入。"""

    handpiece: HandpieceValidationParameters


@dataclass(frozen=True, slots=True)
class CaseConfig:
    """构建与检查命令共用的已校验配置。"""

    case_id: str
    inputs: InputMeshPaths
    geometry: GeometryParameters
    windows: WindowParameters
    render: RenderParameters
    validation: ValidationParameters | None
    output_directory: Path

    @classmethod
    def from_json(cls, config_file: str | Path) -> CaseConfig:
        """读取配置文件，并拒绝未知字段或无效取值。"""

        path = Path(config_file).resolve()
        try:
            raw_value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ConfigurationError(f"无法读取配置 {path}：{error}") from error
        root = _mapping(raw_value, "configuration")
        _reject_unknown(
            root,
            {
                "case_id",
                "inputs",
                "geometry",
                "windows",
                "render",
                "validation",
                "output_directory",
            },
            "configuration",
        )
        base_directory = path.parent
        return cls(
            case_id=_case_id(_required(root, "case_id")),
            inputs=_parse_inputs(_section(root, "inputs"), base_directory),
            geometry=_parse_geometry(_section(root, "geometry")),
            windows=_parse_windows(_section(root, "windows")),
            render=_parse_render(_section(root, "render")),
            validation=(
                _parse_validation(_mapping(root["validation"], "validation"), base_directory)
                if "validation" in root
                else None
            ),
            output_directory=_path(
                _required(root, "output_directory"), base_directory, "output_directory"
            ),
        )


def _mapping(value: object, name: str) -> dict[str, object]:
    """校验配置值为字符串键映射并返回该映射。"""

    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ConfigurationError(f"{name} 必须为对象")
    return value


def _required(values: dict[str, object], name: str) -> object:
    """返回必填字段，字段缺失时抛出配置异常。"""

    if name not in values:
        raise ConfigurationError(f"缺少必填字段：{name}")
    return values[name]


def _section(values: dict[str, object], name: str) -> dict[str, object]:
    """读取必填配置分组并校验其映射类型。"""

    return _mapping(_required(values, name), name)


def _reject_unknown(values: dict[str, object], allowed: set[str], section: str) -> None:
    """拒绝配置分组中不在允许集合内的字段。"""

    unknown = sorted(values.keys() - allowed)
    if unknown:
        raise ConfigurationError(f"{section} 包含未知字段：{', '.join(unknown)}")


def _number(value: object, name: str, *, positive: bool = False) -> float:
    """将配置值校验为有限非负数或正数。"""

    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigurationError(f"{name} 必须为数值")
    number = float(value)
    if not math.isfinite(number):
        raise ConfigurationError(f"{name} 必须为有限数")
    if positive and number <= 0:
        raise ConfigurationError(f"{name} 必须为正数")
    if not positive and number < 0:
        raise ConfigurationError(f"{name} 不得为负数")
    return number


def _positive_integer(value: object, name: str) -> int:
    """将配置值校验为正整数。"""

    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigurationError(f"{name} 必须为正整数")
    return value


def _case_id(value: object) -> str:
    """校验病例标识符只含允许的小写字符。"""

    if not isinstance(value, str) or not CASE_ID_PATTERN.fullmatch(value):
        raise ConfigurationError("case_id 只能包含小写字母、数字、'-' 或 '_'")
    return value


def _path(value: object, base_directory: Path, name: str) -> Path:
    """解析绝对路径或相对配置文件的路径。"""

    if not isinstance(value, str) or not value:
        raise ConfigurationError(f"{name} 必须为非空路径字符串")
    path = Path(value)
    return path.resolve() if path.is_absolute() else (base_directory / path).resolve()


def _stl_reference(value: object, base_directory: Path, name: str) -> Path:
    """解析路径并校验其扩展名为 STL。"""

    path = _path(value, base_directory, name)
    if path.suffix.lower() != ".stl":
        raise ConfigurationError(f"{name} 必须指向 STL 文件：{path}")
    return path


def _stl_path(value: object, base_directory: Path, name: str) -> Path:
    """解析 STL 路径并校验文件实际存在。"""

    path = _stl_reference(value, base_directory, name)
    if not path.is_file():
        raise ConfigurationError(f"{name} 必须指向已存在的 STL 文件：{path}")
    return path


def _parse_inputs(raw: dict[str, object], base_directory: Path) -> InputMeshPaths:
    """解析牙科导板、导套装配体和患者牙列 STL 路径。"""

    fields = {"template", "guide_sleeve_assembly", "patient_dentition"}
    _reject_unknown(raw, fields, "inputs")
    return InputMeshPaths(
        template=_stl_path(_required(raw, "template"), base_directory, "inputs.template"),
        guide_sleeve_assembly=_stl_path(
            _required(raw, "guide_sleeve_assembly"),
            base_directory,
            "inputs.guide_sleeve_assembly",
        ),
        patient_dentition=_stl_path(
            _required(raw, "patient_dentition"),
            base_directory,
            "inputs.patient_dentition",
        ),
    )


def _parse_geometry(raw: dict[str, object]) -> GeometryParameters:
    """解析并校验通道、连接和融合几何参数。"""

    fields = {
        "template_channel_radius_mm",
        "channel_axial_margin_mm",
        "connector_radius_mm",
        "fusion_voxel_size_mm",
    }
    _reject_unknown(raw, fields, "geometry")
    return GeometryParameters(
        template_channel_radius_mm=_number(
            _required(raw, "template_channel_radius_mm"),
            "geometry.template_channel_radius_mm",
            positive=True,
        ),
        channel_axial_margin_mm=_number(
            _required(raw, "channel_axial_margin_mm"), "geometry.channel_axial_margin_mm"
        ),
        connector_radius_mm=_number(
            _required(raw, "connector_radius_mm"),
            "geometry.connector_radius_mm",
            positive=True,
        ),
        fusion_voxel_size_mm=_number(
            _required(raw, "fusion_voxel_size_mm"),
            "geometry.fusion_voxel_size_mm",
            positive=True,
        ),
    )


def _parse_windows(raw: dict[str, object]) -> WindowParameters:
    """解析操作窗在切向和副切向的外扩参数。"""

    fields = {"operation_tangent_margin_mm", "operation_bitangent_margin_mm"}
    _reject_unknown(raw, fields, "windows")
    return WindowParameters(
        operation_tangent_margin_mm=_number(
            _required(raw, "operation_tangent_margin_mm"),
            "windows.operation_tangent_margin_mm",
        ),
        operation_bitangent_margin_mm=_number(
            _required(raw, "operation_bitangent_margin_mm"),
            "windows.operation_bitangent_margin_mm",
        ),
    )


def _parse_render(raw: dict[str, object]) -> RenderParameters:
    """解析并校验渲染图像的像素尺寸。"""

    fields = {"width_px", "height_px"}
    _reject_unknown(raw, fields, "render")
    return RenderParameters(
        width_px=_positive_integer(_required(raw, "width_px"), "render.width_px"),
        height_px=_positive_integer(_required(raw, "height_px"), "render.height_px"),
    )


def _parse_validation(raw: dict[str, object], base_directory: Path) -> ValidationParameters:
    """解析手机网格、净距、倾斜角和撤离距离的独立验证参数。"""

    _reject_unknown(raw, {"handpiece"}, "validation")
    handpiece = _section(raw, "handpiece")
    fields = {
        "mesh",
        "head_crop_radius_mm",
        "minimum_clearance_mm",
        "maximum_tilt_degrees",
        "withdrawal_distances_mm",
    }
    _reject_unknown(handpiece, fields, "validation.handpiece")
    withdrawal_value = _required(handpiece, "withdrawal_distances_mm")
    if not isinstance(withdrawal_value, list) or not withdrawal_value:
        raise ConfigurationError("validation.handpiece.withdrawal_distances_mm 必须为非空数组")
    withdrawal_distances = tuple(
        _number(value, f"validation.handpiece.withdrawal_distances_mm[{index}]")
        for index, value in enumerate(withdrawal_value)
    )
    if not any(abs(distance) < 1e-9 for distance in withdrawal_distances):
        raise ConfigurationError("validation.handpiece.withdrawal_distances_mm 必须包含 0")
    maximum_tilt = _number(
        _required(handpiece, "maximum_tilt_degrees"),
        "validation.handpiece.maximum_tilt_degrees",
    )
    if maximum_tilt >= 90:
        raise ConfigurationError("validation.handpiece.maximum_tilt_degrees 必须小于 90")
    return ValidationParameters(
        handpiece=HandpieceValidationParameters(
            mesh_path=_stl_reference(
                _required(handpiece, "mesh"), base_directory, "validation.handpiece.mesh"
            ),
            head_crop_radius_mm=_number(
                _required(handpiece, "head_crop_radius_mm"),
                "validation.handpiece.head_crop_radius_mm",
                positive=True,
            ),
            minimum_clearance_mm=_number(
                _required(handpiece, "minimum_clearance_mm"),
                "validation.handpiece.minimum_clearance_mm",
            ),
            maximum_tilt_degrees=maximum_tilt,
            withdrawal_distances_mm=withdrawal_distances,
        )
    )

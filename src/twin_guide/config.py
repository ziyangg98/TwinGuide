"""牙科导板构建与独立检查的配置解析。"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

from twin_guide.errors import ConfigurationError

CASE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
DEFAULT_CONNECTOR_DIAMETER_MM = 4.60
DEFAULT_PRESS_BEAM_DIAMETER_MM = 4.60
DEFAULT_OPERATION_BITANGENT_MARGIN_MM = 3.0
DEFAULT_GUIDE_ANCHOR_U_SIDE_RAY_ANGLE_DEGREES = 70.0
DEFAULT_GUIDE_ANCHOR_BACK_U_SIDE_RAY_ANGLE_DEGREES = 90.0


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """拒绝重复映射键的安全 YAML 加载器。"""


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader,
    node: MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    """构造 YAML 映射，并在同层键重复时立即报错。"""

    loader.flatten_mapping(node)
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicated = key in mapping
        except TypeError as error:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            ) from error
        if duplicated:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _load_case_yaml(path: Path) -> object:
    """以严格重复键策略读取一份病例 YAML。"""

    try:
        return yaml.load(
            path.read_text(encoding="utf-8"),
            Loader=_UniqueKeySafeLoader,
        )
    except (OSError, yaml.YAMLError) as error:
        raise ConfigurationError(f"无法读取病例 YAML {path}：{error}") from error


class Jaw(StrEnum):
    """病例的上下颌标记。"""

    UPPER = "upper"
    LOWER = "lower"

    @property
    def occlusal_axis_sign(self) -> float:
        """返回观察窗牙合侧开放方向在世界 Z 轴上的符号。"""

        return -1.0 if self is Jaw.UPPER else 1.0


class SleeveGeometryMode(StrEnum):
    """最终导管实体的来源策略。"""

    GENERATED = "generated"
    INPUT = "input"


@dataclass(frozen=True, slots=True)
class InputMeshPaths:
    """病例的牙科导板、导套装配体和患者牙列网格路径。"""

    template: Path
    guide_sleeve_assemblies: tuple[Path, ...]
    patient_dentition: Path

    @property
    def guide_sleeve_assembly(self) -> Path:
        """为旧版单种植调用方返回唯一导管装配体。"""

        if len(self.guide_sleeve_assemblies) != 1:
            raise ConfigurationError(
                "多种植位病例包含多个导管装配体，必须逐装配体处理"
            )
        return self.guide_sleeve_assemblies[0]


@dataclass(frozen=True, slots=True)
class SleeveParameters:
    """控制导柱形状的八个几何参数。"""

    inner_diameter_mm: float
    outer_diameter_mm: float
    height_mm: float
    platform_width_mm: float
    platform_height_mm: float
    closed_bore_height_mm: float
    inner_arc_angle_degrees: float
    outer_arc_angle_degrees: float

    @property
    def inner_radius_mm(self) -> float:
        """返回导柱内半径。"""

        return self.inner_diameter_mm / 2.0

    @property
    def outer_radius_mm(self) -> float:
        """返回导柱主体外半径。"""

        return self.outer_diameter_mm / 2.0


@dataclass(frozen=True, slots=True)
class PressBeamGuideEndpointParameters:
    """梁架导板端的渐粗、根部球和贴合脚参数。"""

    root_radius_factor: float = 1.08
    transition_length_mm: float = 3.0
    bulb_radius_factor: float = 1.08
    bulb_forward_offset_mm: float = 0.08
    foot_major_radius_mm: float = 3.0
    foot_minor_radius_mm: float = 2.2
    foot_peak_height_mm: float = 2.55
    foot_embed_depth_mm: float = 0.25


@dataclass(frozen=True, slots=True)
class GeometryParameters:
    """导孔切除、连接管和网格融合所需的几何参数。"""

    channel_axial_margin_mm: float
    connector_diameter_mm: float
    fusion_voxel_size_mm: float
    connector_dental_clearance_mm: float = 0.20
    connector_guide_endpoint: PressBeamGuideEndpointParameters = (
        PressBeamGuideEndpointParameters()
    )

    @property
    def connector_radius_mm(self) -> float:
        """返回连接柱半径。"""

        return self.connector_diameter_mm / 2.0


@dataclass(frozen=True, slots=True)
class WindowParameters:
    """操作窗尺寸以及 FDI 轴扫掠观察窗的统一参数。"""

    operation_tangent_margin_mm: float
    operation_bitangent_margin_mm: float
    operation_axial_margin_mm: float
    operation_corner_radius_mm: float
    observation_axis_drop_mm: float
    observation_sweep_angle_degrees: float
    observation_local_failure_drop_targets_mm: tuple[float, ...]
    observation_local_failure_transition_rows: int


@dataclass(frozen=True, slots=True)
class ToothIdentificationInputs:
    """统一牙位识别与导板映射所需的病例定义。"""

    case_yaml: Path


@dataclass(frozen=True, slots=True)
class HandpieceAvoidanceParameters:
    """当前装配深度下牙科手机左右摆动避障参数。"""

    avoidance_id: str
    handpiece: Path
    stop_report: Path
    maximum_angle_degrees: float = 5.0
    pose_samples: int = 41
    union_batch_size: int = 7
    extra_clearance_mm: float = 0.0


class GuideAnchorMode(StrEnum):
    """牙科导板侧锚点选择模式。"""

    NEAREST = "nearest"
    TOOTH_SECTION_TRAJECTORY = "tooth_section_trajectory"
    ADJACENT_TWO_IMPLANT_CONTINUOUS_PATHS = (
        "adjacent_two_implant_continuous_paths"
    )
    TERMINAL_DISTAL_COMMON_NODE = "terminal_distal_common_node"
    ADJACENT_TWO_IMPLANT_TERMINAL_DISTAL_NODE_PATHS = (
        "adjacent_two_implant_terminal_distal_node_paths"
    )


class GuideAnchorSide(StrEnum):
    """独立导板锚点所在的牙弓 U 侧或背 U 侧。"""

    U_SIDE = "u_side"
    BACK_U_SIDE = "back_u_side"


@dataclass(frozen=True, slots=True)
class ToothAnchorStation:
    """一个由单牙中心或相邻双牙中点定义的锚点切面站位。"""

    fdis: tuple[int, ...]
    ray_angle_degrees: float | None = None
    u_side_ray_angle_degrees: float | None = None
    back_u_side_ray_angle_degrees: float | None = None


@dataclass(frozen=True, slots=True)
class GuideAnchorLocation:
    """一个可独立配置牙位轨迹、侧别和射线角度的导板锚点。"""

    anchor_id: str
    endpoint_id: str
    side: GuideAnchorSide
    tooth_station: ToothAnchorStation
    ray_angle_degrees: float


@dataclass(frozen=True, slots=True)
class TerminalDistalCommonNodeParameters:
    """末端缺牙种植位的远中公共梁中心节点参数。"""

    missing_fdi: int
    reference_neighbor_fdi: int
    implant_fdis: tuple[int, ...] = ()
    node_radius_factor: float = 1.12
    distal_offset_sleeve_diameters: float = 2.0


@dataclass(frozen=True, slots=True)
class GuideAnchorParameters:
    """导板侧锚点模式及可独立设置的牙位射线锚点。"""

    mode: GuideAnchorMode = GuideAnchorMode.NEAREST
    anchors: tuple[GuideAnchorLocation, ...] = ()
    # 旧版站位字段仅用于兼容解析；几何阶段统一消费 anchors。
    stations: tuple[ToothAnchorStation, ...] = ()
    u_side_ray_angle_degrees: float = (
        DEFAULT_GUIDE_ANCHOR_U_SIDE_RAY_ANGLE_DEGREES
    )
    back_u_side_ray_angle_degrees: float = (
        DEFAULT_GUIDE_ANCHOR_BACK_U_SIDE_RAY_ANGLE_DEGREES
    )
    terminal_distal_common_node: TerminalDistalCommonNodeParameters | None = None


class PressBeamMode(StrEnum):
    """按压梁锚点与拓扑模式。"""

    DISABLED = "disabled"
    INNER_SLEEVE_UPPER_Y = "inner_sleeve_upper_y"
    THREE_TOOTH_ANCHORS_Y = "three_tooth_anchors_y"
    TERMINAL_U_EXTENSION_ANCHOR_Y = "terminal_u_extension_anchor_y"


class GuideComponentBridgeMode(StrEnum):
    """断裂导板分量之间的预连接拓扑。"""

    DISABLED = "disabled"
    SAME_SIDE_DUAL_BEAM = "same_side_dual_beam"


class GuideTerminalUExtensionMode(StrEnum):
    """导板末端绕牙 U 型延伸梁拓扑。"""

    DISABLED = "disabled"
    TOOTH_WRAPPING_U_BEAM = "tooth_wrapping_u_beam"


@dataclass(frozen=True, slots=True)
class GuideComponentBridgeStation:
    """断裂导板预连接的一个牙位站及其两侧固定射线角度。"""

    station_id: str
    tooth_station: ToothAnchorStation
    u_side_ray_angle_degrees: float
    back_u_side_ray_angle_degrees: float


@dataclass(frozen=True, slots=True)
class GuideComponentBridgeParameters:
    """两块断裂导板之间按 U/背 U 同侧连接的双梁参数。"""

    mode: GuideComponentBridgeMode = GuideComponentBridgeMode.DISABLED
    required_guide_component_count: int = 2
    stations: tuple[GuideComponentBridgeStation, ...] = ()
    require_different_guide_components: bool = True
    diameter_mm: float = DEFAULT_CONNECTOR_DIAMETER_MM
    dental_clearance_mm: float = 0.20
    endpoint_reinforcement: PressBeamGuideEndpointParameters | None = None

    @property
    def enabled(self) -> bool:
        """返回是否启用断裂导板分量桥接。"""

        return self.mode is not GuideComponentBridgeMode.DISABLED

    @property
    def radius_mm(self) -> float:
        """返回导板分量桥接梁半径。"""

        return self.diameter_mm / 2.0


@dataclass(frozen=True, slots=True)
class GuideTerminalUExtensionParameters:
    """从导板末端双锚点绕末端牙回转的 U 型延伸梁参数。"""

    mode: GuideTerminalUExtensionMode = GuideTerminalUExtensionMode.DISABLED
    anchor_station: ToothAnchorStation | None = None
    u_side_ray_angle_degrees: float = (
        DEFAULT_GUIDE_ANCHOR_U_SIDE_RAY_ANGLE_DEGREES
    )
    back_u_side_ray_angle_degrees: float = (
        DEFAULT_GUIDE_ANCHOR_BACK_U_SIDE_RAY_ANGLE_DEGREES
    )
    terminal_fdi: int | None = None
    reference_neighbor_fdi: int | None = None
    diameter_mm: float = DEFAULT_CONNECTOR_DIAMETER_MM
    dental_clearance_mm: float = 0.20
    safety_margin_mm: float = 0.30
    turnaround_depth_mm: float = 3.00
    endpoint_reinforcement: PressBeamGuideEndpointParameters | None = None

    @property
    def enabled(self) -> bool:
        """返回是否启用末端绕牙 U 型延伸梁。"""

        return self.mode is not GuideTerminalUExtensionMode.DISABLED

    @property
    def radius_mm(self) -> float:
        """返回末端绕牙 U 型延伸梁半径。"""

        return self.diameter_mm / 2.0


@dataclass(frozen=True, slots=True)
class PressBeamExtensionAnchorParameters:
    """Y 梁在末端 U 型延伸梁上的双导板锚点最远点参数。"""

    segment: str
    selection: str = "farthest_from_guide_anchors"
    start_margin_mm: float = DEFAULT_CONNECTOR_DIAMETER_MM
    end_margin_mm: float = 0.0
    overlap_mm: float = 0.30


@dataclass(frozen=True, slots=True)
class PressBeamSleeveAnchorSelectionParameters:
    """多种植位 Y 梁导管候选的固定筛选策略。"""

    candidate_scope: str = "inner_sleeve_upper_per_implant_site"
    distance_score: str = "maximin_to_two_guide_anchors"
    tie_breaker: str = "larger_sum_distance"


@dataclass(frozen=True, slots=True)
class PressBeamParameters:
    """定义混合锚点或全牙位锚点 Y 型按压梁。"""

    mode: PressBeamMode = PressBeamMode.DISABLED
    stations: tuple[ToothAnchorStation, ...] = ()
    extension_anchor: PressBeamExtensionAnchorParameters | None = None
    diameter_mm: float = DEFAULT_PRESS_BEAM_DIAMETER_MM
    guide_overlap_mm: float = 0.30
    junction_sleeve_distance_mm: float = 6.0
    junction_axial_lift_mm: float = 2.0
    minimum_junction_angle_degrees: float = 25.0
    sleeve_anchor_selection: PressBeamSleeveAnchorSelectionParameters | None = None
    guide_endpoint: PressBeamGuideEndpointParameters = (
        PressBeamGuideEndpointParameters()
    )

    @property
    def radius_mm(self) -> float:
        """返回按压梁半径。"""

        return self.diameter_mm / 2.0


@dataclass(frozen=True, slots=True)
class RenderParameters:
    """诊断图和结果图的像素尺寸。"""

    width_px: int
    height_px: int


@dataclass(frozen=True, slots=True)
class CaseConfig:
    """构建与检查命令共用的已校验配置。"""

    case_id: str
    jaw: Jaw
    inputs: InputMeshPaths
    sleeve: SleeveParameters
    sleeve_geometry_mode: SleeveGeometryMode
    geometry: GeometryParameters
    windows: WindowParameters
    tooth_identification: ToothIdentificationInputs | None
    handpiece_avoidance: tuple[HandpieceAvoidanceParameters, ...]
    guide_anchors: GuideAnchorParameters
    guide_component_bridge: GuideComponentBridgeParameters
    guide_terminal_u_extension: GuideTerminalUExtensionParameters
    press_beam: PressBeamParameters
    render: RenderParameters
    output_directory: Path

    @classmethod
    def from_json(cls, config_file: str | Path) -> CaseConfig:
        """读取并校验配置文件。"""

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
                "jaw",
                "inputs",
                "sleeve",
                "geometry",
                "windows",
                "tooth_identification",
                "handpiece_avoidance",
                "guide_anchors",
                "guide_component_bridge",
                "guide_terminal_u_extension",
                "press_beam",
                "render",
                "output_directory",
            },
            "configuration",
        )
        base_directory = path.parent
        tooth_identification = _parse_optional_tooth_identification(
            root.get("tooth_identification"), base_directory
        )
        yaml_design = _load_case_yaml_design(tooth_identification)
        yaml_planning = _load_case_yaml_planning(tooth_identification)
        guide_anchor_raw = _merge_case_design_section(
            root.get("guide_anchors"),
            yaml_design.get("guide_anchors"),
            "guide_anchors",
        )
        press_beam_raw = _merge_case_design_section(
            root.get("press_beam"),
            yaml_design.get("press_beam"),
            "press_beam",
        )
        bridge_raw = _merge_case_design_section(
            root.get("guide_component_bridge"),
            yaml_design.get("guide_component_bridge"),
            "guide_component_bridge",
        )
        terminal_u_raw = _merge_case_design_section(
            root.get("guide_terminal_u_extension"),
            yaml_design.get("guide_terminal_u_extension"),
            "guide_terminal_u_extension",
        )
        geometry = _parse_geometry(_section(root, "geometry"))
        window_raw = _merge_operation_window_parameters(
            _section(root, "windows"),
            yaml_planning.get("operation_windows"),
        )
        config = cls(
            case_id=_case_id(_required(root, "case_id")),
            jaw=_jaw(_required(root, "jaw")),
            inputs=_parse_inputs(_section(root, "inputs"), base_directory),
            sleeve=_parse_sleeve(_section(root, "sleeve")),
            sleeve_geometry_mode=_parse_sleeve_geometry_mode(
                yaml_design.get("sleeve_geometry")
            ),
            geometry=geometry,
            windows=_parse_windows(
                window_raw,
                default_operation_axial_margin_mm=geometry.channel_axial_margin_mm,
            ),
            tooth_identification=tooth_identification,
            handpiece_avoidance=_parse_handpiece_avoidances(
                root.get("handpiece_avoidance"), base_directory
            ),
            guide_anchors=_parse_guide_anchors(guide_anchor_raw),
            guide_component_bridge=_parse_guide_component_bridge(bridge_raw),
            guide_terminal_u_extension=_parse_guide_terminal_u_extension(
                terminal_u_raw
            ),
            press_beam=_parse_press_beam(press_beam_raw),
            render=_parse_render(_section(root, "render")),
            output_directory=_path(
                _required(root, "output_directory"), base_directory, "output_directory"
            ),
        )
        requires_tooth_identification = (
            config.guide_anchors.mode is not GuideAnchorMode.NEAREST
            or config.guide_component_bridge.enabled
            or config.guide_terminal_u_extension.enabled
            or config.press_beam.mode is not PressBeamMode.DISABLED
        )
        if requires_tooth_identification and config.tooth_identification is None:
            raise ConfigurationError(
                "启用牙位锚点、断裂导板桥接、末端 U 型延伸梁或 "
                "Y 型按压梁时必须配置 tooth_identification"
            )
        if (
            config.press_beam.mode
            is PressBeamMode.TERMINAL_U_EXTENSION_ANCHOR_Y
            and not config.guide_terminal_u_extension.enabled
        ):
            raise ConfigurationError(
                "terminal_u_extension_anchor_y 必须同时启用 guide_terminal_u_extension"
            )
        if (
            config.guide_anchors.terminal_distal_common_node is not None
            and config.guide_terminal_u_extension.enabled
        ):
            raise ConfigurationError(
                "terminal_distal_common_node 与 guide_terminal_u_extension "
                "不得在同一病例中同时启用"
            )
        if config.tooth_identification is not None:
            _validate_special_case_anatomy(config)
        return config


def _mapping(value: object, name: str) -> dict[str, object]:
    """校验配置值为字符串键映射并返回该映射。"""

    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ConfigurationError(f"{name} 必须为对象")
    return value


def _required(values: dict[str, object], name: str) -> object:
    """读取配置中必须提供的字段。"""

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


def _boolean(value: object, name: str) -> bool:
    """校验严格布尔值，拒绝用 0/1 或字符串代替。"""

    if not isinstance(value, bool):
        raise ConfigurationError(f"{name} 必须为布尔值")
    return value


def _case_id(value: object) -> str:
    """校验病例标识符只含允许的小写字符。"""

    if not isinstance(value, str) or not CASE_ID_PATTERN.fullmatch(value):
        raise ConfigurationError("case_id 只能包含小写字母、数字、'-' 或 '_'")
    return value


def _jaw(value: object) -> Jaw:
    """校验上下颌标记。"""

    try:
        return Jaw(value)
    except (TypeError, ValueError) as error:
        raise ConfigurationError("jaw 必须为 'upper' 或 'lower'") from error


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


def _json_path(value: object, base_directory: Path, name: str) -> Path:
    """解析并校验实际存在的 JSON 报告路径。"""

    path = _path(value, base_directory, name)
    if path.suffix.lower() != ".json" or not path.is_file():
        raise ConfigurationError(f"{name} 必须指向已存在的 JSON 文件：{path}")
    return path


def _parse_inputs(raw: dict[str, object], base_directory: Path) -> InputMeshPaths:
    """解析牙科导板、导套装配体和患者牙列 STL 路径。"""

    fields = {
        "template",
        "guide_sleeve_assembly",
        "guide_sleeve_assemblies",
        "patient_dentition",
    }
    _reject_unknown(raw, fields, "inputs")
    has_single = "guide_sleeve_assembly" in raw
    has_multiple = "guide_sleeve_assemblies" in raw
    if has_single == has_multiple:
        raise ConfigurationError(
            "inputs 必须且只能配置 guide_sleeve_assembly 或 "
            "guide_sleeve_assemblies"
        )
    if has_single:
        assemblies = (
            _stl_path(
                _required(raw, "guide_sleeve_assembly"),
                base_directory,
                "inputs.guide_sleeve_assembly",
            ),
        )
    else:
        raw_assemblies = _required(raw, "guide_sleeve_assemblies")
        if not isinstance(raw_assemblies, list) or len(raw_assemblies) < 2:
            raise ConfigurationError(
                "inputs.guide_sleeve_assemblies 必须包含至少两个 STL"
            )
        assemblies = tuple(
            _stl_path(
                value,
                base_directory,
                f"inputs.guide_sleeve_assemblies[{index}]",
            )
            for index, value in enumerate(raw_assemblies)
        )
    return InputMeshPaths(
        template=_stl_path(_required(raw, "template"), base_directory, "inputs.template"),
        guide_sleeve_assemblies=assemblies,
        patient_dentition=_stl_path(
            _required(raw, "patient_dentition"),
            base_directory,
            "inputs.patient_dentition",
        ),
    )


def _parse_sleeve(raw: dict[str, object]) -> SleeveParameters:
    """解析并校验导柱的八个几何参数。"""

    fields = {
        "inner_diameter_mm",
        "outer_diameter_mm",
        "height_mm",
        "platform_width_mm",
        "platform_height_mm",
        "closed_bore_height_mm",
        "inner_arc_angle_degrees",
        "outer_arc_angle_degrees",
    }
    _reject_unknown(raw, fields, "sleeve")
    parameters = SleeveParameters(
        inner_diameter_mm=_number(
            _required(raw, "inner_diameter_mm"),
            "sleeve.inner_diameter_mm",
            positive=True,
        ),
        outer_diameter_mm=_number(
            _required(raw, "outer_diameter_mm"),
            "sleeve.outer_diameter_mm",
            positive=True,
        ),
        height_mm=_number(_required(raw, "height_mm"), "sleeve.height_mm", positive=True),
        platform_width_mm=_number(
            _required(raw, "platform_width_mm"),
            "sleeve.platform_width_mm",
            positive=True,
        ),
        platform_height_mm=_number(
            _required(raw, "platform_height_mm"),
            "sleeve.platform_height_mm",
            positive=True,
        ),
        closed_bore_height_mm=_number(
            _required(raw, "closed_bore_height_mm"),
            "sleeve.closed_bore_height_mm",
            positive=True,
        ),
        inner_arc_angle_degrees=_number(
            _required(raw, "inner_arc_angle_degrees"),
            "sleeve.inner_arc_angle_degrees",
            positive=True,
        ),
        outer_arc_angle_degrees=_number(
            _required(raw, "outer_arc_angle_degrees"),
            "sleeve.outer_arc_angle_degrees",
            positive=True,
        ),
    )
    if parameters.outer_diameter_mm <= parameters.inner_diameter_mm:
        raise ConfigurationError("sleeve.outer_diameter_mm 必须大于 sleeve.inner_diameter_mm")
    for name, angle in (
        ("inner_arc_angle_degrees", parameters.inner_arc_angle_degrees),
        ("outer_arc_angle_degrees", parameters.outer_arc_angle_degrees),
    ):
        if angle >= 360.0:
            raise ConfigurationError(f"sleeve.{name} 必须小于 360")
    if not (
        0.0
        < parameters.closed_bore_height_mm
        < parameters.platform_height_mm
        < parameters.height_mm
    ):
        raise ConfigurationError(
            "sleeve 高度必须满足 0 < closed_bore_height_mm < platform_height_mm < height_mm"
        )
    return parameters


def _parse_guide_endpoint(
    raw_value: object,
    section: str,
) -> PressBeamGuideEndpointParameters:
    """解析一组梁架导板端渐粗、根部球和贴合脚参数。"""

    raw = _mapping(raw_value, section)
    _reject_unknown(
        raw,
        {
            "root_radius_factor",
            "transition_length_mm",
            "bulb_radius_factor",
            "bulb_forward_offset_mm",
            "foot_major_radius_mm",
            "foot_minor_radius_mm",
            "foot_peak_height_mm",
            "foot_embed_depth_mm",
        },
        section,
    )
    parameters = PressBeamGuideEndpointParameters(
        root_radius_factor=_number(
            raw.get("root_radius_factor", 1.08),
            f"{section}.root_radius_factor",
            positive=True,
        ),
        transition_length_mm=_number(
            raw.get("transition_length_mm", 3.0),
            f"{section}.transition_length_mm",
            positive=True,
        ),
        bulb_radius_factor=_number(
            raw.get("bulb_radius_factor", 1.08),
            f"{section}.bulb_radius_factor",
            positive=True,
        ),
        bulb_forward_offset_mm=_number(
            raw.get("bulb_forward_offset_mm", 0.08),
            f"{section}.bulb_forward_offset_mm",
        ),
        foot_major_radius_mm=_number(
            raw.get("foot_major_radius_mm", 3.0),
            f"{section}.foot_major_radius_mm",
            positive=True,
        ),
        foot_minor_radius_mm=_number(
            raw.get("foot_minor_radius_mm", 2.2),
            f"{section}.foot_minor_radius_mm",
            positive=True,
        ),
        foot_peak_height_mm=_number(
            raw.get("foot_peak_height_mm", 2.55),
            f"{section}.foot_peak_height_mm",
            positive=True,
        ),
        foot_embed_depth_mm=_number(
            raw.get("foot_embed_depth_mm", 0.25),
            f"{section}.foot_embed_depth_mm",
            positive=True,
        ),
    )
    if parameters.root_radius_factor < 1.0:
        raise ConfigurationError(f"{section}.root_radius_factor 不得小于 1")
    if parameters.bulb_radius_factor < 1.0:
        raise ConfigurationError(f"{section}.bulb_radius_factor 不得小于 1")
    if parameters.foot_minor_radius_mm > parameters.foot_major_radius_mm:
        raise ConfigurationError(f"{section}.foot_minor_radius_mm 不得大于主半径")
    return parameters


def _parse_geometry(raw: dict[str, object]) -> GeometryParameters:
    """解析并校验通道、连接和融合几何参数。"""

    fields = {
        "channel_axial_margin_mm",
        "connector_diameter_mm",
        "fusion_voxel_size_mm",
        "connector_dental_clearance_mm",
        "connector_guide_endpoint",
    }
    _reject_unknown(raw, fields, "geometry")
    return GeometryParameters(
        channel_axial_margin_mm=_number(
            _required(raw, "channel_axial_margin_mm"), "geometry.channel_axial_margin_mm"
        ),
        connector_diameter_mm=_number(
            raw.get("connector_diameter_mm", DEFAULT_CONNECTOR_DIAMETER_MM),
            "geometry.connector_diameter_mm",
            positive=True,
        ),
        fusion_voxel_size_mm=_number(
            _required(raw, "fusion_voxel_size_mm"),
            "geometry.fusion_voxel_size_mm",
            positive=True,
        ),
        connector_dental_clearance_mm=_number(
            raw.get("connector_dental_clearance_mm", 0.20),
            "geometry.connector_dental_clearance_mm",
        ),
        connector_guide_endpoint=_parse_guide_endpoint(
            raw.get("connector_guide_endpoint", {}),
            "geometry.connector_guide_endpoint",
        ),
    )


def _parse_windows(
    raw: dict[str, object],
    *,
    default_operation_axial_margin_mm: float,
) -> WindowParameters:
    """解析操作窗和轴扫掠观察窗参数。"""

    fields = {
        "operation_tangent_margin_mm",
        "operation_bitangent_margin_mm",
        "operation_axial_margin_mm",
        "operation_corner_radius_mm",
        "observation_axis_drop_mm",
        "observation_sweep_angle_degrees",
        "observation_local_failure_drop_targets_mm",
        "observation_local_failure_transition_rows",
    }
    _reject_unknown(raw, fields, "windows")
    axis_drop_mm = _number(
        raw.get("observation_axis_drop_mm", 0.2),
        "windows.observation_axis_drop_mm",
        positive=True,
    )
    sweep_angle_degrees = _number(
        raw.get("observation_sweep_angle_degrees", 90.0),
        "windows.observation_sweep_angle_degrees",
        positive=True,
    )
    if sweep_angle_degrees > 180.0:
        raise ConfigurationError(
            "windows.observation_sweep_angle_degrees 必须小于或等于 180"
        )
    raw_targets = raw.get(
        "observation_local_failure_drop_targets_mm", [0.5, 1.0, 2.0]
    )
    if not isinstance(raw_targets, list) or not raw_targets:
        raise ConfigurationError(
            "windows.observation_local_failure_drop_targets_mm 必须为非空数组"
        )
    targets = tuple(
        _number(
            value,
            f"windows.observation_local_failure_drop_targets_mm[{index}]",
            positive=True,
        )
        for index, value in enumerate(raw_targets)
    )
    if any(target <= axis_drop_mm for target in targets):
        raise ConfigurationError(
            "观察窗局部失败高度目标必须全部大于全局高度"
        )
    if any(later <= earlier for earlier, later in zip(targets, targets[1:])):
        raise ConfigurationError("观察窗局部失败高度目标必须严格递增")
    transition_rows_value = raw.get("observation_local_failure_transition_rows", 1)
    if (
        isinstance(transition_rows_value, bool)
        or not isinstance(transition_rows_value, int)
        or transition_rows_value < 0
    ):
        raise ConfigurationError(
            "windows.observation_local_failure_transition_rows 必须为非负整数"
        )
    operation_bitangent_margin_mm = _number(
        raw.get(
            "operation_bitangent_margin_mm",
            DEFAULT_OPERATION_BITANGENT_MARGIN_MM,
        ),
        "windows.operation_bitangent_margin_mm",
    )
    return WindowParameters(
        operation_tangent_margin_mm=_number(
            _required(raw, "operation_tangent_margin_mm"),
            "windows.operation_tangent_margin_mm",
        ),
        operation_bitangent_margin_mm=operation_bitangent_margin_mm,
        operation_axial_margin_mm=_number(
            raw.get(
                "operation_axial_margin_mm",
                default_operation_axial_margin_mm,
            ),
            "windows.operation_axial_margin_mm",
        ),
        operation_corner_radius_mm=_number(
            raw.get(
                "operation_corner_radius_mm",
                min(
                    1.0,
                    max(0.2, operation_bitangent_margin_mm),
                ),
            ),
            "windows.operation_corner_radius_mm",
        ),
        observation_axis_drop_mm=axis_drop_mm,
        observation_sweep_angle_degrees=sweep_angle_degrees,
        observation_local_failure_drop_targets_mm=targets,
        observation_local_failure_transition_rows=transition_rows_value,
    )


def _parse_optional_tooth_identification(
    raw_value: object, base_directory: Path
) -> ToothIdentificationInputs | None:
    """解析可选的统一牙位工作流病例定义。"""

    if raw_value is None:
        return None
    raw = _mapping(raw_value, "tooth_identification")
    fields = {"case_yaml"}
    _reject_unknown(raw, fields, "tooth_identification")
    case_yaml = _path(
        _required(raw, "case_yaml"),
        base_directory,
        "tooth_identification.case_yaml",
    )
    if case_yaml.suffix.lower() not in {".yaml", ".yml"} or not case_yaml.is_file():
        raise ConfigurationError(
            "tooth_identification.case_yaml 必须指向已存在的 YAML 文件："
            f"{case_yaml}"
        )
    return ToothIdentificationInputs(
        case_yaml=case_yaml,
    )


def _parse_single_handpiece_avoidance(
    raw_value: object,
    base_directory: Path,
    index: int,
) -> HandpieceAvoidanceParameters:
    """解析一个当前深度牙科手机左右摆动避障项。"""

    section = f"handpiece_avoidance[{index}]"
    raw = _mapping(raw_value, section)
    _reject_unknown(
        raw,
        {
            "id",
            "handpiece",
            "stop_report",
            "maximum_angle_degrees",
            "pose_samples",
            "union_batch_size",
            "extra_clearance_mm",
        },
        section,
    )
    raw_id = raw.get("id", f"handpiece_{index + 1}")
    avoidance_id = _case_id(raw_id)
    maximum_angle_degrees = _number(
        raw.get("maximum_angle_degrees", 5.0),
        "handpiece_avoidance.maximum_angle_degrees",
        positive=True,
    )
    if maximum_angle_degrees > 45.0:
        raise ConfigurationError(
            "handpiece_avoidance.maximum_angle_degrees 必须小于或等于 45"
        )
    pose_samples = _positive_integer(
        raw.get("pose_samples", 41),
        "handpiece_avoidance.pose_samples",
    )
    if pose_samples < 3 or pose_samples % 2 == 0:
        raise ConfigurationError(
            "handpiece_avoidance.pose_samples 必须为不小于 3 的奇数，以包含 0° 姿态"
        )
    union_batch_size = _positive_integer(
        raw.get("union_batch_size", 7),
        "handpiece_avoidance.union_batch_size",
    )
    if union_batch_size < 2:
        raise ConfigurationError(
            "handpiece_avoidance.union_batch_size 必须不小于 2"
        )
    return HandpieceAvoidanceParameters(
        avoidance_id=avoidance_id,
        handpiece=_stl_path(
            _required(raw, "handpiece"),
            base_directory,
            "handpiece_avoidance.handpiece",
        ),
        stop_report=_json_path(
            _required(raw, "stop_report"),
            base_directory,
            "handpiece_avoidance.stop_report",
        ),
        maximum_angle_degrees=maximum_angle_degrees,
        pose_samples=pose_samples,
        union_batch_size=union_batch_size,
        extra_clearance_mm=_number(
            raw.get("extra_clearance_mm", 0.0),
            "handpiece_avoidance.extra_clearance_mm",
        ),
    )


def _parse_handpiece_avoidances(
    raw_value: object,
    base_directory: Path,
) -> tuple[HandpieceAvoidanceParameters, ...]:
    """解析零个、单个或多个牙科手机避障项并校验编号唯一。"""

    if raw_value is None:
        return ()
    raw_items = raw_value if isinstance(raw_value, list) else [raw_value]
    if not raw_items:
        raise ConfigurationError("handpiece_avoidance 数组不得为空")
    items = tuple(
        _parse_single_handpiece_avoidance(item, base_directory, index)
        for index, item in enumerate(raw_items)
    )
    identifiers = tuple(item.avoidance_id for item in items)
    if len(set(identifiers)) != len(identifiers):
        raise ConfigurationError("handpiece_avoidance.id 不得重复")
    return items


def _load_case_yaml_design(
    inputs: ToothIdentificationInputs | None,
) -> dict[str, object]:
    """读取病例 YAML 中供 TwinGuide 使用的设计语义分组。"""

    if inputs is None:
        return {}
    raw_value = _load_case_yaml(inputs.case_yaml)
    root = _mapping(raw_value, "case.yaml")
    raw_design = root.get("design")
    if raw_design is None:
        return {}
    return _mapping(raw_design, "case.yaml design")


def _load_case_yaml_planning(
    inputs: ToothIdentificationInputs | None,
) -> dict[str, object]:
    """读取病例 YAML 中供 TwinGuide 使用的规划语义分组。"""

    if inputs is None:
        return {}
    raw_value = _load_case_yaml(inputs.case_yaml)
    root = _mapping(raw_value, "case.yaml")
    raw_planning = root.get("planning")
    if raw_planning is None:
        return {}
    return _mapping(raw_planning, "case.yaml planning")


def _parse_sleeve_geometry_mode(raw_value: object) -> SleeveGeometryMode:
    """解析 YAML 中导管实体来源；未配置时保持旧版生成模式。"""

    if raw_value is None:
        return SleeveGeometryMode.GENERATED
    raw = _mapping(raw_value, "case.yaml design.sleeve_geometry")
    _reject_unknown(raw, {"mode"}, "case.yaml design.sleeve_geometry")
    mode_value = str(raw.get("mode", SleeveGeometryMode.GENERATED.value))
    try:
        return SleeveGeometryMode(mode_value)
    except ValueError as error:
        raise ConfigurationError(
            "case.yaml design.sleeve_geometry.mode 必须为 generated 或 input"
        ) from error


def _merge_operation_window_parameters(
    json_windows: dict[str, object],
    yaml_value: object,
) -> dict[str, object]:
    """让病例 YAML 的操作窗规划覆盖 JSON 中的兼容默认值。"""

    if yaml_value is None:
        return json_windows
    yaml_windows = _mapping(
        yaml_value,
        "case.yaml planning.operation_windows",
    )
    _reject_unknown(
        yaml_windows,
        {
            "mode",
            "center_mode",
            "axis_mode",
            "tangent_margin_mm",
            "bitangent_margin_mm",
            "axial_margin_mm",
            "corner_radius_mm",
            "overlap_rule",
            "cut_target",
            "sites",
        },
        "case.yaml planning.operation_windows",
    )
    supported_values = {
        "mode": "per_implant_site",
        "center_mode": "paired_sleeve_operation_feature",
        "axis_mode": "paired_sleeve_average_axis",
        "overlap_rule": "union_cutters",
        "cut_target": "guide_template_only",
    }
    for field, expected in supported_values.items():
        value = yaml_windows.get(field, expected)
        if value != expected:
            raise ConfigurationError(
                "case.yaml planning.operation_windows."
                f"{field} 当前仅支持 {expected}"
            )
    if "sites" in yaml_windows and not isinstance(yaml_windows["sites"], list):
        raise ConfigurationError(
            "case.yaml planning.operation_windows.sites 必须为数组"
        )
    key_map = {
        "tangent_margin_mm": "operation_tangent_margin_mm",
        "bitangent_margin_mm": "operation_bitangent_margin_mm",
        "axial_margin_mm": "operation_axial_margin_mm",
        "corner_radius_mm": "operation_corner_radius_mm",
    }
    overrides = {
        target: yaml_windows[source]
        for source, target in key_map.items()
        if source in yaml_windows
    }
    return {**json_windows, **overrides}


def _load_case_yaml_anatomy(inputs: ToothIdentificationInputs) -> dict[str, object]:
    """读取特殊拓扑配置所需的病例牙位语义。"""

    raw_value = _load_case_yaml(inputs.case_yaml)
    root = _mapping(raw_value, "case.yaml")
    return _mapping(_required(root, "anatomy"), "case.yaml anatomy")


def case_occlusal_axis(config: CaseConfig) -> tuple[float, float, float] | None:
    """返回病例 YAML 中确认的世界坐标牙合轴。

    未配置牙位病例或 YAML 未显式提供 ``anatomy.orientation`` 时返回
    ``None``，调用方可继续采用与旧病例兼容的上下颌世界 Z 轴规则。
    """

    inputs = config.tooth_identification
    if inputs is None:
        return None
    anatomy = _load_case_yaml_anatomy(inputs)
    raw_orientation = anatomy.get("orientation")
    if raw_orientation is None:
        return None
    orientation = _mapping(raw_orientation, "case.yaml anatomy.orientation")
    raw_axis = _required(orientation, "occlusal_axis")
    named_axes = {
        "+X": (1.0, 0.0, 0.0),
        "-X": (-1.0, 0.0, 0.0),
        "+Y": (0.0, 1.0, 0.0),
        "-Y": (0.0, -1.0, 0.0),
        "+Z": (0.0, 0.0, 1.0),
        "-Z": (0.0, 0.0, -1.0),
    }
    if isinstance(raw_axis, str):
        values = named_axes.get(raw_axis.strip().upper())
        if values is None:
            raise ConfigurationError(
                "case.yaml anatomy.orientation.occlusal_axis 必须为 "
                "+X/-X/+Y/-Y/+Z/-Z 或三元素数值数组"
            )
    elif (
        isinstance(raw_axis, list | tuple)
        and len(raw_axis) == 3
        and all(
            not isinstance(value, bool) and isinstance(value, int | float)
            for value in raw_axis
        )
    ):
        values = tuple(float(value) for value in raw_axis)
    else:
        raise ConfigurationError(
            "case.yaml anatomy.orientation.occlusal_axis 必须为 "
            "+X/-X/+Y/-Y/+Z/-Z 或三元素数值数组"
        )
    length = math.sqrt(sum(value * value for value in values))
    if not math.isfinite(length) or length <= 1e-8:
        raise ConfigurationError(
            "case.yaml anatomy.orientation.occlusal_axis 必须为有限非零向量"
        )
    return tuple(value / length for value in values)


def require_production_review(config: CaseConfig) -> None:
    """拒绝使用明确标记为待人工审核的病例执行生产生成。"""

    inputs = config.tooth_identification
    if inputs is None:
        return
    raw_value = _load_case_yaml(inputs.case_yaml)
    root = _mapping(raw_value, "case.yaml")
    pending_values = {"pending", "pending_user_input", "unreviewed"}
    pending_fields = []
    raw_anatomy = root.get("anatomy")
    if isinstance(raw_anatomy, dict):
        value = raw_anatomy.get("review_status")
        if isinstance(value, str) and value.strip().lower() in pending_values:
            pending_fields.append("anatomy.review_status")
    raw_review = root.get("review")
    if isinstance(raw_review, dict):
        pending_fields.extend(
            f"review.{key}"
            for key, value in raw_review.items()
            if key.endswith("_status")
            and isinstance(value, str)
            and value.strip().lower() in pending_values
        )
    if pending_fields:
        raise ConfigurationError(
            "生产生成被 case.yaml 待审核状态阻止："
            + ", ".join(pending_fields)
            + "；请完成人工确认，或在明确承担风险时使用 "
            "--allow-unreviewed"
        )


def _anatomy_fdis(anatomy: dict[str, object], key: str) -> tuple[int, ...]:
    """读取并校验病例牙列语义中的 FDI 数组。"""

    raw = _required(anatomy, key)
    if not isinstance(raw, list):
        raise ConfigurationError(f"case.yaml anatomy.{key} 必须为 FDI 数组")
    return tuple(
        _fdi(value, f"case.yaml anatomy.{key}[{index}]")
        for index, value in enumerate(raw)
    )


def _validate_distal_pair(
    terminal_fdi: int,
    reference_fdi: int,
    present_fdis: set[int],
    *,
    terminal_must_be_present: bool,
    section: str,
) -> None:
    """要求参考牙为终末牙的直接近中邻牙，且远中无更后现存牙。"""

    terminal_quadrant, terminal_position = divmod(terminal_fdi, 10)
    reference_quadrant, reference_position = divmod(reference_fdi, 10)
    if (
        terminal_quadrant != reference_quadrant
        or terminal_position != reference_position + 1
    ):
        raise ConfigurationError(
            f"{section} 必须满足参考牙→直接远中终末牙的相邻关系"
        )
    if reference_fdi not in present_fdis:
        raise ConfigurationError(f"{section} 的参考邻牙必须为现存牙")
    if terminal_must_be_present and terminal_fdi not in present_fdis:
        raise ConfigurationError(f"{section} 的终末牙必须为现存牙")
    if any(
        divmod(fdi, 10)[0] == terminal_quadrant
        and divmod(fdi, 10)[1] > terminal_position
        for fdi in present_fdis
    ):
        raise ConfigurationError(f"{section} 的 terminal_fdi 不是当前牙列末端")


def _validate_special_case_anatomy(config: CaseConfig) -> None:
    """在进入几何阶段前校验 #14/#17 类特殊病例的牙位语义。"""

    assert config.tooth_identification is not None
    terminal = config.guide_anchors.terminal_distal_common_node
    extension = config.guide_terminal_u_extension
    if terminal is None and not extension.enabled:
        return
    anatomy = _load_case_yaml_anatomy(config.tooth_identification)
    present = set(_anatomy_fdis(anatomy, "present_teeth"))
    missing = set(_anatomy_fdis(anatomy, "missing_teeth"))
    if terminal is not None:
        if terminal.missing_fdi not in missing:
            raise ConfigurationError(
                "guide_anchors.terminal_distal_common_node.missing_fdi "
                "必须在 anatomy.missing_teeth 中"
            )
        if (
            config.guide_anchors.mode
            is GuideAnchorMode.ADJACENT_TWO_IMPLANT_TERMINAL_DISTAL_NODE_PATHS
        ):
            implant_fdis = terminal.implant_fdis
            if len(implant_fdis) != 2:
                raise ConfigurationError(
                    "双种植位末端牙龈模式必须配置两个 implant_fdis"
                )
            if terminal.missing_fdi != implant_fdis[-1]:
                raise ConfigurationError(
                    "terminal_distal_common_node.missing_fdi 必须是 implant_fdis 的远中末项"
                )
            quadrant, reference_position = divmod(
                terminal.reference_neighbor_fdi, 10
            )
            expected = tuple(
                quadrant * 10 + reference_position + offset
                for offset in range(1, len(implant_fdis) + 1)
            )
            if implant_fdis != expected:
                raise ConfigurationError(
                    "双种植位末端牙龈模式必须满足"
                    "参考邻牙→两个连续远中种植位"
                )
            if terminal.reference_neighbor_fdi not in present:
                raise ConfigurationError("末端远中公共节点参考邻牙必须为现存牙")
            if any(fdi not in missing for fdi in implant_fdis):
                raise ConfigurationError(
                    "双种植位末端牙龈模式的 implant_fdis 必须均在 missing_teeth 中"
                )
            if any(
                divmod(fdi, 10)[0] == quadrant
                and divmod(fdi, 10)[1] > divmod(implant_fdis[-1], 10)[1]
                for fdi in present
            ):
                raise ConfigurationError("末端种植位不是当前牙列远中末端")
        else:
            _validate_distal_pair(
                terminal.missing_fdi,
                terminal.reference_neighbor_fdi,
                present,
                terminal_must_be_present=False,
                section="guide_anchors.terminal_distal_common_node",
            )
    if extension.enabled:
        assert extension.terminal_fdi is not None
        assert extension.reference_neighbor_fdi is not None
        _validate_distal_pair(
            extension.terminal_fdi,
            extension.reference_neighbor_fdi,
            present,
            terminal_must_be_present=True,
            section="guide_terminal_u_extension",
        )


def _merge_case_design_section(
    json_value: object,
    yaml_value: object,
    section: str,
) -> dict[str, object] | None:
    """按字段合并 JSON 工程参数与 YAML 病例设计，拒绝重复来源。"""

    if json_value is None and yaml_value is None:
        return None
    json_section = {} if json_value is None else _mapping(json_value, section)
    yaml_section = (
        {} if yaml_value is None else _mapping(yaml_value, f"case.yaml design.{section}")
    )
    duplicates = sorted(json_section.keys() & yaml_section.keys())
    if duplicates:
        raise ConfigurationError(
            f"{section} 在 JSON 与 case.yaml 中重复配置字段："
            f"{', '.join(duplicates)}"
        )
    return {**json_section, **yaml_section}


def _fdi(value: object, name: str) -> int:
    """校验一个恒牙 FDI 编码。"""

    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigurationError(f"{name} 必须为整数 FDI 编码")
    quadrant, position = divmod(value, 10)
    if quadrant not in {1, 2, 3, 4} or position not in set(range(1, 9)):
        raise ConfigurationError(f"{name} 不是有效恒牙 FDI 编码")
    return value


def _parse_guide_anchors(raw_value: object) -> GuideAnchorParameters:
    """解析可选的导板侧牙位截面轨迹锚点配置。"""

    if raw_value is None:
        return GuideAnchorParameters()
    raw = _mapping(raw_value, "guide_anchors")
    _reject_unknown(
        raw,
        {
            "mode",
            "anchors",
            "stations",
            "u_side_ray_angle_degrees",
            "back_u_side_ray_angle_degrees",
            "terminal_distal_common_node",
        },
        "guide_anchors",
    )
    try:
        mode = GuideAnchorMode(str(raw.get("mode", GuideAnchorMode.NEAREST.value)))
    except ValueError as error:
        raise ConfigurationError(
            "guide_anchors.mode 必须为 nearest、tooth_section_trajectory "
            "、adjacent_two_implant_continuous_paths、"
            "terminal_distal_common_node 或 "
            "adjacent_two_implant_terminal_distal_node_paths"
        ) from error
    if "anchors" in raw and "stations" in raw:
        raise ConfigurationError("guide_anchors 不得同时配置 anchors 和旧版 stations")
    terminal_distal_common_node = None
    raw_terminal_anchor = raw.get("terminal_distal_common_node")
    if raw_terminal_anchor is not None:
        terminal = _mapping(
            raw_terminal_anchor,
            "guide_anchors.terminal_distal_common_node",
        )
        _reject_unknown(
            terminal,
            {
                "missing_fdi",
                "reference_neighbor_fdi",
                "implant_fdis",
                "node_radius_factor",
                "distal_offset_sleeve_diameters",
            },
            "guide_anchors.terminal_distal_common_node",
        )
        missing_fdi = _fdi(
            _required(terminal, "missing_fdi"),
            "guide_anchors.terminal_distal_common_node.missing_fdi",
        )
        neighbor_fdi = _fdi(
            _required(terminal, "reference_neighbor_fdi"),
            "guide_anchors.terminal_distal_common_node.reference_neighbor_fdi",
        )
        if missing_fdi == neighbor_fdi:
            raise ConfigurationError("末端缺牙牙位与参考邻牙不得相同")
        raw_implant_fdis = terminal.get("implant_fdis", [])
        if not isinstance(raw_implant_fdis, list):
            raise ConfigurationError(
                "guide_anchors.terminal_distal_common_node.implant_fdis 必须为 FDI 数组"
            )
        implant_fdis = tuple(
            _fdi(
                value,
                "guide_anchors.terminal_distal_common_node."
                f"implant_fdis[{index}]",
            )
            for index, value in enumerate(raw_implant_fdis)
        )
        node_radius_factor = _number(
            terminal.get("node_radius_factor", 1.12),
            "guide_anchors.terminal_distal_common_node.node_radius_factor",
            positive=True,
        )
        if node_radius_factor < 1.0:
            raise ConfigurationError("远中公共节点半径系数不得小于 1.0")
        distal_offset_sleeve_diameters = _number(
            terminal.get("distal_offset_sleeve_diameters", 2.0),
            "guide_anchors.terminal_distal_common_node."
            "distal_offset_sleeve_diameters",
            positive=True,
        )
        if abs(distal_offset_sleeve_diameters - 2.0) > 1e-9:
            raise ConfigurationError(
                "远中公共节点必须固定沿远中方向移动 2 个平均导管外径"
            )
        terminal_distal_common_node = TerminalDistalCommonNodeParameters(
            missing_fdi=missing_fdi,
            reference_neighbor_fdi=neighbor_fdi,
            implant_fdis=implant_fdis,
            node_radius_factor=node_radius_factor,
            distal_offset_sleeve_diameters=distal_offset_sleeve_diameters,
        )
    if (
        mode in {
            GuideAnchorMode.TERMINAL_DISTAL_COMMON_NODE,
            GuideAnchorMode.ADJACENT_TWO_IMPLANT_TERMINAL_DISTAL_NODE_PATHS,
        }
        and terminal_distal_common_node is None
    ):
        raise ConfigurationError("末端远中公共节点模式必须配置 terminal_distal_common_node")
    if (
        mode not in {
            GuideAnchorMode.TERMINAL_DISTAL_COMMON_NODE,
            GuideAnchorMode.ADJACENT_TWO_IMPLANT_TERMINAL_DISTAL_NODE_PATHS,
        }
        and terminal_distal_common_node is not None
    ):
        raise ConfigurationError(
            "只有 terminal_distal_common_node 可以配置 terminal_distal_common_node"
        )
    u_side_angle = _ray_angle_degrees(
        raw.get(
            "u_side_ray_angle_degrees",
            DEFAULT_GUIDE_ANCHOR_U_SIDE_RAY_ANGLE_DEGREES,
        ),
        "guide_anchors.u_side_ray_angle_degrees",
    )
    back_u_side_angle = _ray_angle_degrees(
        raw.get(
            "back_u_side_ray_angle_degrees",
            DEFAULT_GUIDE_ANCHOR_BACK_U_SIDE_RAY_ANGLE_DEGREES,
        ),
        "guide_anchors.back_u_side_ray_angle_degrees",
    )
    if mode is GuideAnchorMode.NEAREST and (
        "u_side_ray_angle_degrees" in raw
        or "back_u_side_ray_angle_degrees" in raw
    ):
        raise ConfigurationError("nearest 锚点模式不得配置旋转射线角度")
    stations = _parse_tooth_anchor_stations(
        raw.get("stations", []),
        "guide_anchors.stations",
    )
    if "anchors" in raw:
        if (
            "u_side_ray_angle_degrees" in raw
            or "back_u_side_ray_angle_degrees" in raw
        ):
            raise ConfigurationError(
                "独立 anchors 模式的角度必须配置在每个锚点内"
            )
        anchors = _parse_guide_anchor_locations(raw["anchors"])
    else:
        anchors = _expand_legacy_guide_anchor_stations(
            raw.get("stations", []),
            stations,
            u_side_angle,
            back_u_side_angle,
            require_station_angles=mode in {
                GuideAnchorMode.ADJACENT_TWO_IMPLANT_CONTINUOUS_PATHS,
                GuideAnchorMode.ADJACENT_TWO_IMPLANT_TERMINAL_DISTAL_NODE_PATHS,
            },
        )
    _validate_guide_anchor_locations(mode, anchors)
    return GuideAnchorParameters(
        mode=mode,
        anchors=anchors,
        stations=stations,
        u_side_ray_angle_degrees=u_side_angle,
        back_u_side_ray_angle_degrees=back_u_side_angle,
        terminal_distal_common_node=terminal_distal_common_node,
    )


def _parse_guide_anchor_locations(
    raw_anchors: object,
) -> tuple[GuideAnchorLocation, ...]:
    """解析逐锚点牙位轨迹、侧别与角度参数。"""

    if not isinstance(raw_anchors, list):
        raise ConfigurationError("guide_anchors.anchors 必须为数组")
    anchors = []
    for index, raw_anchor in enumerate(raw_anchors):
        section = f"guide_anchors.anchors[{index}]"
        anchor = _mapping(raw_anchor, section)
        _reject_unknown(
            anchor,
            {"id", "endpoint", "side", "station", "ray_angle_degrees"},
            section,
        )
        anchor_id = str(_required(anchor, "id")).strip()
        endpoint_id = str(_required(anchor, "endpoint")).strip()
        if not CASE_ID_PATTERN.fullmatch(anchor_id):
            raise ConfigurationError(f"{section}.id 必须为小写字母数字标识")
        if not CASE_ID_PATTERN.fullmatch(endpoint_id):
            raise ConfigurationError(f"{section}.endpoint 必须为小写字母数字标识")
        try:
            side = GuideAnchorSide(str(_required(anchor, "side")))
        except ValueError as error:
            raise ConfigurationError(
                f"{section}.side 必须为 u_side 或 back_u_side"
            ) from error
        tooth_station = _parse_tooth_anchor_stations(
            [_mapping(_required(anchor, "station"), f"{section}.station")],
            f"{section}.station",
        )[0]
        anchors.append(
            GuideAnchorLocation(
                anchor_id,
                endpoint_id,
                side,
                tooth_station,
                _ray_angle_degrees(
                    _required(anchor, "ray_angle_degrees"),
                    f"{section}.ray_angle_degrees",
                ),
            )
        )
    identifiers = tuple(anchor.anchor_id for anchor in anchors)
    if len(set(identifiers)) != len(identifiers):
        raise ConfigurationError("guide_anchors.anchors.id 不得重复")
    return tuple(anchors)


def _expand_legacy_guide_anchor_stations(
    raw_stations: object,
    stations: tuple[ToothAnchorStation, ...],
    default_u_angle: float,
    default_back_u_angle: float,
    *,
    require_station_angles: bool,
) -> tuple[GuideAnchorLocation, ...]:
    """把旧版成对站位等价展开为逐锚点配置。"""

    if not isinstance(raw_stations, list):
        raise ConfigurationError("guide_anchors.stations 必须为数组")
    anchors = []
    endpoint_ids = []
    for index, (raw_station, station) in enumerate(
        zip(raw_stations, stations, strict=True)
    ):
        mapping = _mapping(raw_station, f"guide_anchors.stations[{index}]")
        endpoint_id = str(mapping.get("id", f"station_{index + 1}")).strip()
        if not CASE_ID_PATTERN.fullmatch(endpoint_id):
            raise ConfigurationError("guide_anchors.stations.id 必须为小写字母数字标识")
        endpoint_ids.append(endpoint_id)
        if require_station_angles and (
            station.u_side_ray_angle_degrees is None
            or station.back_u_side_ray_angle_degrees is None
        ):
            raise ConfigurationError("当前连续路径模式的每个站位必须配置 U/背 U 角度")
        u_angle = station.u_side_ray_angle_degrees or default_u_angle
        back_u_angle = station.back_u_side_ray_angle_degrees or default_back_u_angle
        anchors.extend(
            (
                GuideAnchorLocation(
                    f"{endpoint_id}_u",
                    endpoint_id,
                    GuideAnchorSide.U_SIDE,
                    station,
                    u_angle,
                ),
                GuideAnchorLocation(
                    f"{endpoint_id}_back_u",
                    endpoint_id,
                    GuideAnchorSide.BACK_U_SIDE,
                    station,
                    back_u_angle,
                ),
            )
        )
    if len(set(endpoint_ids)) != len(endpoint_ids):
        raise ConfigurationError("guide_anchors.stations.id 不得重复")
    return tuple(anchors)


def _validate_guide_anchor_locations(
    mode: GuideAnchorMode,
    anchors: tuple[GuideAnchorLocation, ...],
) -> None:
    """校验各拓扑所需端部数量及每端 U/背 U 锚点完整性。"""

    expected_endpoint_count = {
        GuideAnchorMode.NEAREST: 0,
        GuideAnchorMode.TOOTH_SECTION_TRAJECTORY: 2,
        GuideAnchorMode.ADJACENT_TWO_IMPLANT_CONTINUOUS_PATHS: 2,
        GuideAnchorMode.TERMINAL_DISTAL_COMMON_NODE: 1,
        GuideAnchorMode.ADJACENT_TWO_IMPLANT_TERMINAL_DISTAL_NODE_PATHS: 1,
    }[mode]
    endpoint_ids = tuple(dict.fromkeys(anchor.endpoint_id for anchor in anchors))
    if len(endpoint_ids) != expected_endpoint_count:
        raise ConfigurationError(
            f"guide_anchors.{mode.value} 必须配置 "
            f"{expected_endpoint_count} 个端部的独立锚点"
        )
    for endpoint_id in endpoint_ids:
        endpoint_anchors = tuple(
            anchor for anchor in anchors if anchor.endpoint_id == endpoint_id
        )
        sides = tuple(anchor.side for anchor in endpoint_anchors)
        if len(endpoint_anchors) != 2 or set(sides) != set(GuideAnchorSide):
            raise ConfigurationError(
                f"guide_anchors 端部 {endpoint_id} 必须各配置一个 U 侧和背 U 侧锚点"
            )


def _ray_angle_degrees(value: object, name: str) -> float:
    """校验从正参考轴起算的射线旋转角。"""

    angle = _number(value, name, positive=True)
    if angle > 180.0:
        raise ConfigurationError(f"{name} 必须小于或等于 180")
    return angle


def _parse_tooth_anchor_stations(
    raw_stations: object,
    section: str,
    *,
    allow_ray_angle: bool = False,
) -> tuple[ToothAnchorStation, ...]:
    """解析由单牙中心或双牙中点定义的牙位站位数组。"""

    if not isinstance(raw_stations, list):
        raise ConfigurationError(f"{section} 必须为数组")
    stations = []
    for index, raw_station in enumerate(raw_stations):
        station_name = f"{section}[{index}]"
        station = _mapping(raw_station, station_name)
        _reject_unknown(
            station,
            {
                "id",
                "type",
                "fdi",
                "fdis",
                "u_side_ray_angle_degrees",
                "back_u_side_ray_angle_degrees",
            }
            | ({"ray_angle_degrees"} if allow_ray_angle else set()),
            station_name,
        )
        station_type = str(_required(station, "type"))
        if station_type == "tooth_center":
            fdis = (_fdi(_required(station, "fdi"), f"{station_name}.fdi"),)
            if station.get("fdis") is not None:
                raise ConfigurationError("tooth_center 站位不得同时配置 fdis")
        elif station_type == "tooth_pair_midpoint":
            raw_fdis = _required(station, "fdis")
            if not isinstance(raw_fdis, list) or len(raw_fdis) != 2:
                raise ConfigurationError("tooth_pair_midpoint.fdis 必须包含两个 FDI")
            fdis = tuple(
                _fdi(value, f"{station_name}.fdis[{fdi_index}]")
                for fdi_index, value in enumerate(raw_fdis)
            )
            if fdis[0] == fdis[1]:
                raise ConfigurationError("tooth_pair_midpoint 的两个 FDI 不得相同")
            if station.get("fdi") is not None:
                raise ConfigurationError("tooth_pair_midpoint 站位不得同时配置 fdi")
        else:
            raise ConfigurationError(
                f"{section} station.type 必须为 tooth_center 或 tooth_pair_midpoint"
            )
        ray_angle_degrees = None
        if "ray_angle_degrees" in station:
            ray_angle_degrees = _ray_angle_degrees(
                station["ray_angle_degrees"],
                f"{station_name}.ray_angle_degrees",
            )
        u_side_angle = (
            _ray_angle_degrees(
                station["u_side_ray_angle_degrees"],
                f"{station_name}.u_side_ray_angle_degrees",
            )
            if station.get("u_side_ray_angle_degrees") is not None
            else None
        )
        back_u_side_angle = (
            _ray_angle_degrees(
                station["back_u_side_ray_angle_degrees"],
                f"{station_name}.back_u_side_ray_angle_degrees",
            )
            if station.get("back_u_side_ray_angle_degrees") is not None
            else None
        )
        stations.append(
            ToothAnchorStation(
                fdis,
                ray_angle_degrees,
                u_side_angle,
                back_u_side_angle,
            )
        )
    return tuple(stations)


def _parse_guide_component_bridge(
    raw_value: object,
) -> GuideComponentBridgeParameters:
    """解析断裂导板两分量之间的同侧双梁预连接。"""

    if raw_value is None:
        return GuideComponentBridgeParameters()
    raw = _mapping(raw_value, "guide_component_bridge")
    _reject_unknown(
        raw,
        {
            "enabled",
            "mode",
            "required_guide_component_count",
            "stations",
            "connection_rule",
            "require_different_guide_components",
            "diameter_mm",
            "dental_clearance_mm",
            "endpoint_reinforcement",
        },
        "guide_component_bridge",
    )
    enabled = _boolean(raw.get("enabled", False), "guide_component_bridge.enabled")
    mode_value = str(
        raw.get(
            "mode",
            (
                GuideComponentBridgeMode.SAME_SIDE_DUAL_BEAM.value
                if enabled
                else GuideComponentBridgeMode.DISABLED.value
            ),
        )
    )
    try:
        mode = GuideComponentBridgeMode(mode_value)
    except ValueError as error:
        raise ConfigurationError(
            "guide_component_bridge.mode 必须为 disabled 或 same_side_dual_beam"
        ) from error
    if enabled != (mode is not GuideComponentBridgeMode.DISABLED):
        raise ConfigurationError(
            "guide_component_bridge.enabled 必须与 mode 是否为 disabled 一致"
        )
    required_count = _positive_integer(
        raw.get("required_guide_component_count", 2),
        "guide_component_bridge.required_guide_component_count",
    )
    if enabled and required_count != 2:
        raise ConfigurationError("same_side_dual_beam 当前要求恰好两个导板分量")
    if str(raw.get("connection_rule", "same_side")) != "same_side":
        raise ConfigurationError("guide_component_bridge.connection_rule 必须为 same_side")
    require_different = _boolean(
        raw.get("require_different_guide_components", True),
        "guide_component_bridge.require_different_guide_components",
    )
    raw_stations = raw.get("stations", [])
    if not isinstance(raw_stations, list):
        raise ConfigurationError("guide_component_bridge.stations 必须为数组")
    stations = []
    for index, raw_station_value in enumerate(raw_stations):
        section = f"guide_component_bridge.stations[{index}]"
        raw_station = _mapping(raw_station_value, section)
        _reject_unknown(
            raw_station,
            {
                "id",
                "type",
                "fdi",
                "fdis",
                "u_side_ray_angle_degrees",
                "back_u_side_ray_angle_degrees",
            },
            section,
        )
        station_id = str(_required(raw_station, "id"))
        if not CASE_ID_PATTERN.fullmatch(station_id):
            raise ConfigurationError(f"{section}.id 含有无效字符")
        tooth_raw = {
            key: value
            for key, value in raw_station.items()
            if key in {"type", "fdi", "fdis"}
        }
        tooth_station = _parse_tooth_anchor_stations([tooth_raw], section)[0]
        stations.append(
            GuideComponentBridgeStation(
                station_id,
                tooth_station,
                _ray_angle_degrees(
                    _required(raw_station, "u_side_ray_angle_degrees"),
                    f"{section}.u_side_ray_angle_degrees",
                ),
                _ray_angle_degrees(
                    _required(raw_station, "back_u_side_ray_angle_degrees"),
                    f"{section}.back_u_side_ray_angle_degrees",
                ),
            )
        )
    if enabled and len(stations) != 2:
        raise ConfigurationError("same_side_dual_beam 必须配置两个牙位站位")
    if not enabled and stations:
        raise ConfigurationError("disabled 导板预连接不得配置牙位站位")
    if len({station.station_id for station in stations}) != len(stations):
        raise ConfigurationError("guide_component_bridge station.id 不得重复")
    endpoint_raw = _mapping(
        raw.get("endpoint_reinforcement", {"enabled": False}),
        "guide_component_bridge.endpoint_reinforcement",
    )
    _reject_unknown(
        endpoint_raw,
        {"enabled", "method"},
        "guide_component_bridge.endpoint_reinforcement",
    )
    endpoint_enabled = _boolean(
        endpoint_raw.get("enabled", False),
        "guide_component_bridge.endpoint_reinforcement.enabled",
    )
    method = str(endpoint_raw.get("method", "bulb_and_conformal_foot"))
    if endpoint_enabled and method != "bulb_and_conformal_foot":
        raise ConfigurationError(
            "guide_component_bridge.endpoint_reinforcement.method "
            "必须为 bulb_and_conformal_foot"
        )
    return GuideComponentBridgeParameters(
        mode=mode,
        required_guide_component_count=required_count,
        stations=tuple(stations),
        require_different_guide_components=require_different,
        diameter_mm=_number(
            raw.get("diameter_mm", DEFAULT_CONNECTOR_DIAMETER_MM),
            "guide_component_bridge.diameter_mm",
            positive=True,
        ),
        dental_clearance_mm=_number(
            raw.get("dental_clearance_mm", 0.20),
            "guide_component_bridge.dental_clearance_mm",
        ),
        endpoint_reinforcement=(
            PressBeamGuideEndpointParameters() if endpoint_enabled else None
        ),
    )


def _parse_guide_terminal_u_extension(
    raw_value: object,
) -> GuideTerminalUExtensionParameters:
    """解析从导板末端双锚点绕末端牙回转的 U 型延伸梁。"""

    if raw_value is None:
        return GuideTerminalUExtensionParameters()
    raw = _mapping(raw_value, "guide_terminal_u_extension")
    _reject_unknown(
        raw,
        {
            "enabled",
            "mode",
            "anchor_station",
            "u_side_ray_angle_degrees",
            "back_u_side_ray_angle_degrees",
            "terminal_fdi",
            "reference_neighbor_fdi",
            "diameter_mm",
            "dental_clearance_mm",
            "safety_margin_mm",
            "turnaround_depth_mm",
            "endpoint_reinforcement",
        },
        "guide_terminal_u_extension",
    )
    enabled = _boolean(
        raw.get("enabled", False), "guide_terminal_u_extension.enabled"
    )
    mode_value = str(
        raw.get(
            "mode",
            (
                GuideTerminalUExtensionMode.TOOTH_WRAPPING_U_BEAM.value
                if enabled
                else GuideTerminalUExtensionMode.DISABLED.value
            ),
        )
    )
    try:
        mode = GuideTerminalUExtensionMode(mode_value)
    except ValueError as error:
        raise ConfigurationError(
            "guide_terminal_u_extension.mode 必须为 disabled 或 "
            "tooth_wrapping_u_beam"
        ) from error
    if enabled != (mode is not GuideTerminalUExtensionMode.DISABLED):
        raise ConfigurationError(
            "guide_terminal_u_extension.enabled 必须与 mode 是否为 disabled 一致"
        )

    anchor_station = None
    raw_anchor = raw.get("anchor_station")
    if raw_anchor is not None:
        anchor_station = _parse_tooth_anchor_stations(
            [_mapping(raw_anchor, "guide_terminal_u_extension.anchor_station")],
            "guide_terminal_u_extension.anchor_station",
        )[0]
    if enabled and anchor_station is None:
        raise ConfigurationError(
            "tooth_wrapping_u_beam 必须配置 anchor_station"
        )
    if not enabled and anchor_station is not None:
        raise ConfigurationError(
            "disabled 末端 U 型延伸梁不得配置 anchor_station"
        )

    terminal_fdi = (
        _fdi(
            _required(raw, "terminal_fdi"),
            "guide_terminal_u_extension.terminal_fdi",
        )
        if enabled
        else None
    )
    neighbor_fdi = (
        _fdi(
            _required(raw, "reference_neighbor_fdi"),
            "guide_terminal_u_extension.reference_neighbor_fdi",
        )
        if enabled
        else None
    )
    if enabled and terminal_fdi == neighbor_fdi:
        raise ConfigurationError("末端牙与参考邻牙不得相同")

    diameter_mm = _number(
        raw.get("diameter_mm", DEFAULT_CONNECTOR_DIAMETER_MM),
        "guide_terminal_u_extension.diameter_mm",
        positive=True,
    )
    turnaround_depth_mm = _number(
        raw.get("turnaround_depth_mm", 3.0),
        "guide_terminal_u_extension.turnaround_depth_mm",
        positive=True,
    )
    if enabled and turnaround_depth_mm < diameter_mm / 2.0:
        raise ConfigurationError(
            "guide_terminal_u_extension.turnaround_depth_mm 不得小于梁半径"
        )

    endpoint_raw = _mapping(
        raw.get("endpoint_reinforcement", {"enabled": False}),
        "guide_terminal_u_extension.endpoint_reinforcement",
    )
    _reject_unknown(
        endpoint_raw,
        {"enabled", "method"},
        "guide_terminal_u_extension.endpoint_reinforcement",
    )
    endpoint_enabled = _boolean(
        endpoint_raw.get("enabled", False),
        "guide_terminal_u_extension.endpoint_reinforcement.enabled",
    )
    method = str(endpoint_raw.get("method", "bulb_and_conformal_foot"))
    if endpoint_enabled and method != "bulb_and_conformal_foot":
        raise ConfigurationError(
            "guide_terminal_u_extension.endpoint_reinforcement.method "
            "必须为 bulb_and_conformal_foot"
        )
    return GuideTerminalUExtensionParameters(
        mode=mode,
        anchor_station=anchor_station,
        u_side_ray_angle_degrees=_ray_angle_degrees(
            raw.get(
                "u_side_ray_angle_degrees",
                DEFAULT_GUIDE_ANCHOR_U_SIDE_RAY_ANGLE_DEGREES,
            ),
            "guide_terminal_u_extension.u_side_ray_angle_degrees",
        ),
        back_u_side_ray_angle_degrees=_ray_angle_degrees(
            raw.get(
                "back_u_side_ray_angle_degrees",
                DEFAULT_GUIDE_ANCHOR_BACK_U_SIDE_RAY_ANGLE_DEGREES,
            ),
            "guide_terminal_u_extension.back_u_side_ray_angle_degrees",
        ),
        terminal_fdi=terminal_fdi,
        reference_neighbor_fdi=neighbor_fdi,
        diameter_mm=diameter_mm,
        dental_clearance_mm=_number(
            raw.get("dental_clearance_mm", 0.20),
            "guide_terminal_u_extension.dental_clearance_mm",
        ),
        safety_margin_mm=_number(
            raw.get("safety_margin_mm", 0.30),
            "guide_terminal_u_extension.safety_margin_mm",
        ),
        turnaround_depth_mm=turnaround_depth_mm,
        endpoint_reinforcement=(
            PressBeamGuideEndpointParameters() if endpoint_enabled else None
        ),
    )


def _parse_press_beam(raw_value: object) -> PressBeamParameters:
    """解析可选的牙弓内侧导管高端 Y 型按压梁配置。"""

    if raw_value is None:
        return PressBeamParameters()
    raw = _mapping(raw_value, "press_beam")
    _reject_unknown(
        raw,
        {
            "mode",
            "stations",
            "extension_anchor",
            "diameter_mm",
            "guide_overlap_mm",
            "junction_sleeve_distance_mm",
            "junction_axial_lift_mm",
            "sleeve_anchor_selection",
            "minimum_junction_angle_degrees",
            "guide_endpoint",
        },
        "press_beam",
    )
    try:
        mode = PressBeamMode(str(raw.get("mode", PressBeamMode.DISABLED.value)))
    except ValueError as error:
        raise ConfigurationError(
            "press_beam.mode 必须为 disabled、inner_sleeve_upper_y "
            "、three_tooth_anchors_y 或 terminal_u_extension_anchor_y"
        ) from error
    stations = _parse_tooth_anchor_stations(
        raw.get("stations", []),
        "press_beam.stations",
        allow_ray_angle=True,
    )
    diameter_mm = _number(
        raw.get("diameter_mm", DEFAULT_PRESS_BEAM_DIAMETER_MM),
        "press_beam.diameter_mm",
        positive=True,
    )
    overlap_mm = _number(
        raw.get("guide_overlap_mm", 0.30),
        "press_beam.guide_overlap_mm",
    )
    junction_distance_mm = _number(
        raw.get("junction_sleeve_distance_mm", 6.0),
        "press_beam.junction_sleeve_distance_mm",
        positive=True,
    )
    junction_axial_lift_mm = _number(
        raw.get("junction_axial_lift_mm", 2.0),
        "press_beam.junction_axial_lift_mm",
        positive=True,
    )
    guide_endpoint = _parse_guide_endpoint(
        raw.get("guide_endpoint", {}),
        "press_beam.guide_endpoint",
    )
    raw_sleeve_selection = raw.get("sleeve_anchor_selection")
    sleeve_anchor_selection = None
    if raw_sleeve_selection is not None:
        selection = _mapping(
            raw_sleeve_selection,
            "press_beam.sleeve_anchor_selection",
        )
        _reject_unknown(
            selection,
            {"candidate_scope", "distance_score", "tie_breaker"},
            "press_beam.sleeve_anchor_selection",
        )
        if str(_required(selection, "candidate_scope")) != (
            "inner_sleeve_upper_per_implant_site"
        ):
            raise ConfigurationError("不支持的多种植位 Y 梁导管候选范围")
        if str(_required(selection, "distance_score")) != "maximin_to_two_guide_anchors":
            raise ConfigurationError("多种植位 Y 梁距离评分必须为 maximin_to_two_guide_anchors")
        if str(_required(selection, "tie_breaker")) != "larger_sum_distance":
            raise ConfigurationError("多种植位 Y 梁平局规则必须为 larger_sum_distance")
        sleeve_anchor_selection = PressBeamSleeveAnchorSelectionParameters(
            candidate_scope=str(_required(selection, "candidate_scope")),
            distance_score=str(_required(selection, "distance_score")),
            tie_breaker=str(_required(selection, "tie_breaker")),
        )
    minimum_junction_angle = _number(
        raw.get("minimum_junction_angle_degrees", 25.0),
        "press_beam.minimum_junction_angle_degrees",
        positive=True,
    )
    if minimum_junction_angle > 180.0:
        raise ConfigurationError("press_beam.minimum_junction_angle_degrees 必须不大于 180")
    extension_anchor = None
    raw_extension_anchor = raw.get("extension_anchor")
    if raw_extension_anchor is not None:
        anchor = _mapping(raw_extension_anchor, "press_beam.extension_anchor")
        _reject_unknown(
            anchor,
            {
                "segment",
                "selection",
                "start_margin_mm",
                "end_margin_mm",
                "overlap_mm",
            },
            "press_beam.extension_anchor",
        )
        segment = str(_required(anchor, "segment"))
        if segment not in {"u_side", "back_u_side", "turnaround", "full"}:
            raise ConfigurationError(
                "press_beam.extension_anchor.segment 必须为 u_side、"
                "back_u_side、turnaround 或 full"
            )
        selection = str(anchor.get("selection", "farthest_from_guide_anchors"))
        if selection != "farthest_from_guide_anchors":
            raise ConfigurationError(
                "press_beam.extension_anchor.selection 必须为 "
                "farthest_from_guide_anchors"
            )
        anchor_overlap_mm = _number(
            anchor.get("overlap_mm", 0.30),
            "press_beam.extension_anchor.overlap_mm",
        )
        if anchor_overlap_mm >= diameter_mm / 2.0:
            raise ConfigurationError(
                "press_beam.extension_anchor.overlap_mm 必须小于按压梁半径"
            )
        extension_anchor = PressBeamExtensionAnchorParameters(
            segment=segment,
            selection=selection,
            start_margin_mm=_number(
                anchor.get("start_margin_mm", DEFAULT_CONNECTOR_DIAMETER_MM),
                "press_beam.extension_anchor.start_margin_mm",
            ),
            end_margin_mm=_number(
                anchor.get("end_margin_mm", 0.0),
                "press_beam.extension_anchor.end_margin_mm",
            ),
            overlap_mm=anchor_overlap_mm,
        )
    if mode is PressBeamMode.DISABLED and stations:
        raise ConfigurationError("disabled 按压梁模式不得配置牙位站位")
    if mode is PressBeamMode.INNER_SLEEVE_UPPER_Y and len(stations) != 2:
        raise ConfigurationError("内侧导管高端 Y 型按压梁必须配置两个牙位站位")
    if mode is PressBeamMode.INNER_SLEEVE_UPPER_Y and sleeve_anchor_selection is None:
        sleeve_anchor_selection = PressBeamSleeveAnchorSelectionParameters()
    if (
        mode is not PressBeamMode.INNER_SLEEVE_UPPER_Y
        and sleeve_anchor_selection is not None
    ):
        raise ConfigurationError(
            "只有 inner_sleeve_upper_y 可以配置 sleeve_anchor_selection"
        )
    if mode is PressBeamMode.THREE_TOOTH_ANCHORS_Y and len(stations) != 3:
        raise ConfigurationError("全牙位锚点 Y 型按压梁必须配置三个牙位站位")
    if (
        mode is PressBeamMode.TERMINAL_U_EXTENSION_ANCHOR_Y
        and len(stations) != 2
    ):
        raise ConfigurationError("末端 U 型延伸梁锚点 Y 型按压梁必须配置两个牙位站位")
    if (
        mode is PressBeamMode.TERMINAL_U_EXTENSION_ANCHOR_Y
        and extension_anchor is None
    ):
        raise ConfigurationError("末端 U 型延伸梁锚点 Y 型按压梁必须配置 extension_anchor")
    if (
        mode is not PressBeamMode.TERMINAL_U_EXTENSION_ANCHOR_Y
        and extension_anchor is not None
    ):
        raise ConfigurationError("只有 terminal_u_extension_anchor_y 可以配置 extension_anchor")
    if mode is not PressBeamMode.DISABLED and any(
        station.ray_angle_degrees is None for station in stations
    ):
        raise ConfigurationError(
            "Y 型按压梁的每个导板锚点必须显式配置 ray_angle_degrees"
        )
    if overlap_mm >= diameter_mm / 2.0:
        raise ConfigurationError("press_beam.guide_overlap_mm 必须小于按压梁半径")
    return PressBeamParameters(
        mode=mode,
        stations=stations,
        extension_anchor=extension_anchor,
        diameter_mm=diameter_mm,
        guide_overlap_mm=overlap_mm,
        junction_sleeve_distance_mm=junction_distance_mm,
        junction_axial_lift_mm=junction_axial_lift_mm,
        minimum_junction_angle_degrees=minimum_junction_angle,
        sleeve_anchor_selection=sleeve_anchor_selection,
        guide_endpoint=guide_endpoint,
    )


def _parse_render(raw: dict[str, object]) -> RenderParameters:
    """解析并校验渲染图像的像素尺寸。"""

    fields = {"width_px", "height_px"}
    _reject_unknown(raw, fields, "render")
    return RenderParameters(
        width_px=_positive_integer(_required(raw, "width_px"), "render.width_px"),
        height_px=_positive_integer(_required(raw, "height_px"), "render.height_px"),
    )

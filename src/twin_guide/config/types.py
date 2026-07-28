"""病例配置使用的不可变数据类型。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from twin_guide.errors import ConfigurationError

DEFAULT_CONNECTOR_DIAMETER_MM = 4.60
DEFAULT_PRESS_BEAM_DIAMETER_MM = 4.60
DEFAULT_OPERATION_BITANGENT_MARGIN_MM = 3.0
DEFAULT_GUIDE_ANCHOR_U_SIDE_RAY_ANGLE_DEGREES = 70.0
DEFAULT_GUIDE_ANCHOR_BACK_U_SIDE_RAY_ANGLE_DEGREES = 90.0

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
    """病例的牙科导板、导管装配体和患者牙列网格路径。"""

    template: Path
    guide_sleeve_assemblies: tuple[Path, ...]
    patient_dentition: Path

    @property
    def guide_sleeve_assembly(self) -> Path:
        """返回单颗病例的唯一导管装配体。"""

        if len(self.guide_sleeve_assemblies) != 1:
            raise ConfigurationError(
                "多种植位病例包含多个导管装配体，必须逐装配体处理"
            )
        return self.guide_sleeve_assemblies[0]


@dataclass(frozen=True, slots=True)
class SleeveParameters:
    """控制导管形状的八个几何参数。"""

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
        """返回导管内半径。"""

        return self.inner_diameter_mm / 2.0

    @property
    def outer_radius_mm(self) -> float:
        """返回导管主体外半径。"""

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


__all__ = [
    "DEFAULT_CONNECTOR_DIAMETER_MM",
    "DEFAULT_GUIDE_ANCHOR_BACK_U_SIDE_RAY_ANGLE_DEGREES",
    "DEFAULT_GUIDE_ANCHOR_U_SIDE_RAY_ANGLE_DEGREES",
    "DEFAULT_OPERATION_BITANGENT_MARGIN_MM",
    "DEFAULT_PRESS_BEAM_DIAMETER_MM",
    "GeometryParameters",
    "GuideAnchorLocation",
    "GuideAnchorMode",
    "GuideAnchorParameters",
    "GuideAnchorSide",
    "GuideComponentBridgeMode",
    "GuideComponentBridgeParameters",
    "GuideComponentBridgeStation",
    "GuideTerminalUExtensionMode",
    "GuideTerminalUExtensionParameters",
    "HandpieceAvoidanceParameters",
    "InputMeshPaths",
    "Jaw",
    "PressBeamExtensionAnchorParameters",
    "PressBeamGuideEndpointParameters",
    "PressBeamMode",
    "PressBeamParameters",
    "PressBeamSleeveAnchorSelectionParameters",
    "RenderParameters",
    "SleeveGeometryMode",
    "SleeveParameters",
    "TerminalDistalCommonNodeParameters",
    "ToothAnchorStation",
    "ToothIdentificationInputs",
    "WindowParameters",
]

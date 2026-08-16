"""病例配置使用的不可变数据类型。"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path

from twin_guide.guide_post_positioning import calculate_twin_guide_extension_mm

DEFAULT_CONNECTOR_DIAMETER_MM = 4.60
DEFAULT_PRESS_BEAM_DIAMETER_MM = 4.60
DEFAULT_OPERATION_BITANGENT_MARGIN_MM = 3.0
DEFAULT_GUIDE_ANCHOR_U_SIDE_RAY_ANGLE_DEGREES = 70.0
DEFAULT_GUIDE_ANCHOR_BACK_U_SIDE_RAY_ANGLE_DEGREES = 90.0
DEFAULT_GUIDE_SPACING_MM = 11.50


class Jaw(StrEnum):
    """病例的上下颌标记。"""

    UPPER = "upper"
    LOWER = "lower"

    @property
    def occlusal_axis_sign(self) -> float:
        """返回观察窗牙合侧开放方向在世界 Z 轴上的符号。"""

        return -1.0 if self is Jaw.UPPER else 1.0


class ToothIdentificationBackend(StrEnum):
    """第 2 阶段可选的牙位识别实现。"""

    STANDARD = "standard"
    FDI_NEW = "fdi_new"


@dataclass(frozen=True, slots=True)
class InputMeshPaths:
    """病例的牙科导板和患者牙列网格路径。"""

    template: Path
    patient_dentition: Path


@dataclass(frozen=True, slots=True)
class SleeveParameters:
    """控制双导导柱主体、间距和顶部环形凹陷的几何参数。"""

    inner_diameter_mm: float
    outer_diameter_mm: float
    height_mm: float
    platform_slot_width_mm: float
    platform_height_mm: float
    closed_bore_height_mm: float
    inner_arc_angle_degrees: float
    outer_arc_angle_degrees: float
    guide_spacing_mm: float = DEFAULT_GUIDE_SPACING_MM
    platform_overhang_mm: float = 0.20
    top_recess_diameter_mm: float | None = None
    top_recess_depth_mm: float = 0.0

    @property
    def inner_radius_mm(self) -> float:
        """返回导管内半径。"""

        return self.inner_diameter_mm / 2.0

    @property
    def outer_radius_mm(self) -> float:
        """返回导管主体外半径。"""

        return self.outer_diameter_mm / 2.0

    @property
    def outer_d_face_offset_mm(self) -> float:
        """返回轴心沿 C 口方向到外侧 D 面的距离。"""

        opening_angle = math.radians(360.0 - self.outer_arc_angle_degrees)
        return self.outer_radius_mm * math.cos(0.5 * opening_angle)

    @property
    def platform_inner_face_offset_mm(self) -> float:
        """返回轴心到相向内侧平台端面的距离。"""

        return self.outer_radius_mm + self.platform_overhang_mm

    @property
    def guide_axis_spacing_mm(self) -> float:
        """由两个相向内侧平台端面净距返回双导轴心距。"""

        return self.guide_spacing_mm + 2.0 * self.platform_inner_face_offset_mm

    @property
    def guide_c_opening_spacing_mm(self) -> float:
        """返回下部 C 口截面两个相向 D 面的净距。"""

        return self.guide_axis_spacing_mm - 2.0 * self.outer_d_face_offset_mm

    @property
    def guide_pair_outer_span_mm(self) -> float:
        """返回两根导柱沿中心连线方向的最外侧总宽。"""

        return self.guide_axis_spacing_mm + self.outer_diameter_mm

    @property
    def top_recess_radius_mm(self) -> float | None:
        """返回顶部环形凹陷半径；未启用时返回 ``None``。"""

        if self.top_recess_diameter_mm is None:
            return None
        return self.top_recess_diameter_mm / 2.0


@dataclass(frozen=True, slots=True)
class SleeveParameterOverrides:
    """一个种植位相对全局标准导柱参数的三项轴向高度覆盖值。"""

    height_mm: float | None = None
    platform_height_mm: float | None = None
    closed_bore_height_mm: float | None = None

    def resolve(self, defaults: SleeveParameters) -> SleeveParameters:
        """把已填写字段覆盖到全局标准值，返回一组完整参数。"""

        updates = {
            name: value
            for name, value in (
                ("height_mm", self.height_mm),
                ("platform_height_mm", self.platform_height_mm),
                ("closed_bore_height_mm", self.closed_bore_height_mm),
            )
            if value is not None
        }
        return replace(defaults, **updates)


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
class ConnectionBlockParameters:
    """控制第 6 阶段各类连接块是否进入最终模型。"""

    lower_main: bool = True
    upper_main: bool = True
    press_beam: bool = True


@dataclass(frozen=True, slots=True)
class GeometryParameters:
    """导孔切除、连接管和网格融合所需的几何参数。"""

    channel_axial_margin_mm: float
    connector_diameter_mm: float
    fusion_voxel_size_mm: float
    connector_dental_clearance_mm: float = 0.20
    sleeve_stop_clearance_mm: float = 2.0
    sleeve_stop_front_avoidance_mm: float = 4.0
    connection_blocks: ConnectionBlockParameters = field(default_factory=ConnectionBlockParameters)
    connector_guide_endpoint: PressBeamGuideEndpointParameters = PressBeamGuideEndpointParameters()

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
    operation_front_axial_margin_mm: float
    operation_rear_axial_margin_mm: float
    operation_corner_radius_mm: float
    observation_axis_drop_mm: float
    observation_sweep_angle_degrees: float
    observation_local_failure_drop_targets_mm: tuple[float, ...]
    observation_local_failure_transition_rows: int


@dataclass(frozen=True, slots=True)
class ClinicalPlanningParameters:
    """等待临床定义或外部格式确认的病例级参数接口。"""

    implant_coordinates_path: Path | None = None
    implant_coordinates_format: str | None = None
    extension_mm: float | None = None
    extension_definition: str | None = None
    mouth_opening_mm: float | None = None
    adapter_length_mm: float | None = None
    height_formula_id: str | None = None

    @property
    def effective_extension_mm(self) -> float:
        """返回中性延长量；未定义时不改变现有几何。"""

        return 0.0 if self.extension_mm is None else self.extension_mm


@dataclass(frozen=True, slots=True)
class GuidePostParameters:
    """一个已识别圆环对应的种植位轴向规划参数。"""

    ring_index: int
    drill_length_mm: float
    implant_length_mm: float
    sleeve_template_extension_mm: float
    sleeve: SleeveParameterOverrides = SleeveParameterOverrides()

    @property
    def twin_guide_extension_mm(self) -> float:
        """返回该圆环对应的双导导板延长量。"""

        return calculate_twin_guide_extension_mm(
            self.drill_length_mm,
            self.implant_length_mm,
        )

    def resolved_sleeve(self, defaults: SleeveParameters) -> SleeveParameters:
        """返回该种植位继承并覆盖后的完整双导导柱参数。"""

        return self.sleeve.resolve(defaults)


@dataclass(frozen=True, slots=True)
class SleeveSiteOverride:
    """一个种植位左右两根导柱共用的三项轴向高度。"""

    ring_index: int
    height_mm: float
    platform_height_mm: float
    closed_bore_height_mm: float

    def __post_init__(self) -> None:
        """校验圆环编号及三项严格高度关系。"""
        if self.ring_index <= 0:
            raise ValueError("导柱种植位圆环编号必须为正")
        if not 0.0 < self.closed_bore_height_mm < self.platform_height_mm < self.height_mm:
            raise ValueError("导柱高度必须满足：底部高度 < 平台高度 < 总高度")


@dataclass(frozen=True, slots=True)
class OperationWindowOverride:
    """一个种植位操作窗口的局部几何覆盖值。"""

    site_index: int
    tangent_margin_mm: float
    bitangent_margin_mm: float
    front_axial_margin_mm: float
    rear_axial_margin_mm: float
    center_offset_mm: tuple[float, float, float] = (0.0, 0.0, 0.0)

    def __post_init__(self) -> None:
        """校验种植位编号和非负窗口边距。"""
        if self.site_index <= 0:
            raise ValueError("操作窗口种植位编号必须为正")
        if (
            min(
                self.tangent_margin_mm,
                self.bitangent_margin_mm,
                self.front_axial_margin_mm,
                self.rear_axial_margin_mm,
            )
            < 0.0
        ):
            raise ValueError("操作窗口边距不得为负")


@dataclass(frozen=True, slots=True)
class ObservationWindowOverride:
    """一个 FDI 观察窗口的图形化覆盖值。"""

    window_id: str
    start_fdi: int
    end_fdi: int
    axis_drop_mm: float
    height_mm: float
    sweep_angle_degrees: float

    def __post_init__(self) -> None:
        """校验 FDI、下沉量、高度和扫掠角。"""
        if not self.window_id:
            raise ValueError("观察窗口编号不得为空")
        if min(self.start_fdi, self.end_fdi) <= 0:
            raise ValueError("观察窗口 FDI 编号必须为正")
        if self.axis_drop_mm < 0.0 or self.height_mm <= 0.0:
            raise ValueError("观察窗口下沉量不得为负且高度必须为正")
        if not 0.0 < self.sweep_angle_degrees <= 180.0:
            raise ValueError("观察窗口扫掠角必须位于 (0, 180]")


@dataclass(frozen=True, slots=True)
class ConnectorAvoidanceOverride:
    """一根导柱高位连接线单侧的止停台避让节点。"""

    guide_index: int
    path_fraction: float
    downward_offset_mm: float
    side: str

    def __post_init__(self) -> None:
        """校验导柱编号、沿线比例和下移量。"""
        if self.guide_index <= 0:
            raise ValueError("导柱编号必须为正")
        if not 0.0 <= self.path_fraction <= 1.0:
            raise ValueError("连接节点沿线比例必须位于 [0, 1]")
        if self.downward_offset_mm < 0.0:
            raise ValueError("连接节点下移量不得为负")
        if self.side not in {"left", "right"}:
            raise ValueError("连接节点 side 必须为 left 或 right")


@dataclass(frozen=True, slots=True)
class SurfaceAnchorOverride:
    """导板或牙面上的显式按压/支撑锚点。"""

    anchor_id: str
    surface_role: str
    position_mm: tuple[float, float, float]
    normal: tuple[float, float, float]

    def __post_init__(self) -> None:
        """校验表面类型及保存法向。"""
        if not self.anchor_id:
            raise ValueError("表面锚点编号不得为空")
        if self.surface_role not in {"template", "dentition"}:
            raise ValueError("表面锚点类型必须为 template 或 dentition")
        if sum(value * value for value in self.normal) <= 1e-12:
            raise ValueError("表面锚点法向不得为零向量")


@dataclass(frozen=True, slots=True)
class EditorOverrides:
    """Blender 图形化编辑器写回病例的显式几何覆盖值。"""

    sleeve_sites: tuple[SleeveSiteOverride, ...] = ()
    operation_windows: tuple[OperationWindowOverride, ...] = ()
    observation_windows: tuple[ObservationWindowOverride, ...] = ()
    connector_avoidance: tuple[ConnectorAvoidanceOverride, ...] = ()
    surface_anchors: tuple[SurfaceAnchorOverride, ...] = ()
    press_junction_mm: tuple[float, float, float] | None = None

    def sleeve_for(self, ring_index: int) -> SleeveSiteOverride | None:
        """按种植位圆环编号查找成对高度覆盖值。"""
        return next(
            (item for item in self.sleeve_sites if item.ring_index == ring_index),
            None,
        )

    def operation_window_for(self, site_index: int) -> OperationWindowOverride | None:
        """按种植位查找操作窗覆盖值。"""
        return next(
            (item for item in self.operation_windows if item.site_index == site_index),
            None,
        )

    def connector_for(
        self,
        guide_index: int,
        side: str,
    ) -> ConnectorAvoidanceOverride | None:
        """按导柱和侧别查找避让节点。"""

        return next(
            (
                item
                for item in self.connector_avoidance
                if item.guide_index == guide_index and item.side == side
            ),
            None,
        )

    def observation_window_for(self, window_id: str) -> ObservationWindowOverride | None:
        """按观察窗编号查找拖动覆盖值。"""
        return next(
            (item for item in self.observation_windows if item.window_id == window_id),
            None,
        )

    def surface_anchor_for(self, anchor_id: str) -> SurfaceAnchorOverride | None:
        """按锚点编号查找表面坐标覆盖值。"""
        return next(
            (item for item in self.surface_anchors if item.anchor_id == anchor_id),
            None,
        )


@dataclass(frozen=True, slots=True)
class ToothIdentificationInputs:
    """统一牙位识别与导板映射所需的病例定义。"""

    case_yaml: Path
    backend: ToothIdentificationBackend = ToothIdentificationBackend.FDI_NEW


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
    ADJACENT_TWO_IMPLANT_CONTINUOUS_PATHS = "adjacent_two_implant_continuous_paths"
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
    u_side_ray_angle_degrees: float = DEFAULT_GUIDE_ANCHOR_U_SIDE_RAY_ANGLE_DEGREES
    back_u_side_ray_angle_degrees: float = DEFAULT_GUIDE_ANCHOR_BACK_U_SIDE_RAY_ANGLE_DEGREES
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
    u_side_ray_angle_degrees: float = DEFAULT_GUIDE_ANCHOR_U_SIDE_RAY_ANGLE_DEGREES
    back_u_side_ray_angle_degrees: float = DEFAULT_GUIDE_ANCHOR_BACK_U_SIDE_RAY_ANGLE_DEGREES
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
    guide_endpoint: PressBeamGuideEndpointParameters = PressBeamGuideEndpointParameters()

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
    "ClinicalPlanningParameters",
    "ConnectionBlockParameters",
    "ConnectorAvoidanceOverride",
    "EditorOverrides",
    "GeometryParameters",
    "GuideAnchorLocation",
    "GuideAnchorMode",
    "GuideAnchorParameters",
    "GuideAnchorSide",
    "GuideComponentBridgeMode",
    "GuideComponentBridgeParameters",
    "GuideComponentBridgeStation",
    "GuidePostParameters",
    "GuideTerminalUExtensionMode",
    "GuideTerminalUExtensionParameters",
    "HandpieceAvoidanceParameters",
    "InputMeshPaths",
    "Jaw",
    "ObservationWindowOverride",
    "OperationWindowOverride",
    "PressBeamExtensionAnchorParameters",
    "PressBeamGuideEndpointParameters",
    "PressBeamMode",
    "PressBeamParameters",
    "PressBeamSleeveAnchorSelectionParameters",
    "RenderParameters",
    "SleeveParameterOverrides",
    "SleeveParameters",
    "SleeveSiteOverride",
    "SurfaceAnchorOverride",
    "TerminalDistalCommonNodeParameters",
    "ToothAnchorStation",
    "ToothIdentificationBackend",
    "ToothIdentificationInputs",
    "WindowParameters",
]

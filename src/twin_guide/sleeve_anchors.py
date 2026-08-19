"""第 4 步的导管侧表面接触点 Q 和梁中心线点 P 选择。"""

from __future__ import annotations

from dataclasses import dataclass

from twin_guide.config import DEFAULT_CONNECTOR_DIAMETER_MM, GeometryParameters
from twin_guide.geometry import Vec3, project_to_plane
from twin_guide.models import CaseAnalysis, GuideSleeve
from twin_guide.types import SleeveGenerationResult


@dataclass(frozen=True, slots=True)
class SleeveAnchorSelectionConfig:
    """导管接触点和梁中心线点的几何参数。

    属性:
        connector_radius_mm: 连接梁半径，单位毫米。
        upper_edge_clearance_mm: 上梁外缘到稳定外壁上边缘的净距。
        lower_edge_clearance_mm: 下梁外缘到稳定外壁下边缘的净距。
        axial_margin_mm: 接触截面到稳定区间端部的最低安全余量。
        upper_cutter_clearance_mm: 上梁嵌入后到固定孔边界的最低余量。
    """

    connector_radius_mm: float = DEFAULT_CONNECTOR_DIAMETER_MM / 2.0
    upper_edge_clearance_mm: float = 2.0
    lower_edge_clearance_mm: float = 1.0
    axial_margin_mm: float = 0.8
    upper_cutter_clearance_mm: float = 0.01

    @classmethod
    def from_geometry(cls, geometry: GeometryParameters) -> SleeveAnchorSelectionConfig:
        """从病例几何配置生成算法输入，避免调用方重复搬运字段。"""

        anchors = geometry.anchor_selection
        return cls(
            connector_radius_mm=geometry.connector_radius_mm,
            upper_edge_clearance_mm=geometry.sleeve_stop_clearance_mm,
            lower_edge_clearance_mm=anchors.lower_edge_clearance_mm,
            axial_margin_mm=anchors.axial_margin_mm,
            upper_cutter_clearance_mm=anchors.upper_cutter_clearance_mm,
        )

    def __post_init__(self) -> None:
        """校验半径、净距和安全余量。"""

        if self.connector_radius_mm <= 0.0:
            raise ValueError("连接梁半径必须为正")
        if (
            min(
                self.upper_edge_clearance_mm,
                self.lower_edge_clearance_mm,
                self.axial_margin_mm,
                self.upper_cutter_clearance_mm,
            )
            < 0.0
        ):
            raise ValueError("导管锚点净距和安全余量不得为负")


@dataclass(frozen=True, slots=True)
class SleeveAnchorPoint:
    """一组导管表面接触点 Q 和梁中心线点 P。

    ``surface_contact`` 是标准导管主体外壁上的 Q；``position`` 是后续
    连续梁使用的中心线接触点 P。两者满足
    ``P = Q + (r_connector - tube_overlap) * surface_normal``。
    """

    label: str
    axial_fraction: float
    axial_position_mm: float
    section_center: Vec3
    surface_contact: Vec3
    surface_normal: Vec3
    position: Vec3
    connector_radius_mm: float
    tube_overlap_mm: float
    local_wall_thickness_mm: float

    @property
    def centerline_offset_mm(self) -> float:
        """返回 Q 到 P 沿外法向的有符号距离。"""

        return self.connector_radius_mm - self.tube_overlap_mm


@dataclass(frozen=True, slots=True)
class SleevePlatformEnvelope:
    """导柱平台用于正视投影避让的参数化包络。"""

    origin: Vec3
    axis: Vec3
    opening_direction: Vec3
    across_direction: Vec3
    axial_min_mm: float
    axial_max_mm: float
    opening_min_mm: float
    opening_max_mm: float
    across_min_mm: float
    across_max_mm: float


@dataclass(frozen=True, slots=True)
class SleeveAnchorSelection:
    """一个导管的成对向外方向及上下 Q/P 点。"""

    guide_index: int
    radial_direction: Vec3
    lower: SleeveAnchorPoint
    upper: SleeveAnchorPoint
    platform: SleevePlatformEnvelope


@dataclass(frozen=True, slots=True)
class SleeveAnchorPlan:
    """所有导管的 Q/P 锚点计划。"""

    selections: tuple[SleeveAnchorSelection, ...]


def _body_wall_direction(guide: GuideSleeve) -> Vec3:
    """返回 C 口反方向在导管轴线法平面上的单位向量。"""

    radial = -project_to_plane(guide.parameters.c_opening_direction, guide.axis)
    return radial.normalized()


def _stable_outer_wall_interval(guide: GuideSleeve) -> tuple[float, float]:
    """返回标准重建导管的轴向范围。"""

    return guide.axial_min_mm, guide.axial_max_mm


def _contact_position(
    guide: GuideSleeve,
    label: str,
    axial_position_mm: float,
    direction: Vec3,
    config: SleeveAnchorSelectionConfig,
) -> SleeveAnchorPoint:
    """在标准导管外壁上求 Q，并按上下嵌入规则生成 P。"""

    # 导管轴线以 C 口高端为 axis_origin，+axis 指向闭合低端。
    t_min, t_max = _stable_outer_wall_interval(guide)
    closed_low_end = guide.center + guide.axis * t_max
    section_center = closed_low_end - guide.axis * (axial_position_mm - t_min)
    surface_contact = section_center + direction * guide.body_radius_mm
    surface_normal = direction
    contact_radius = (surface_contact - section_center).dot(direction)
    wall_thickness = contact_radius - guide.bore_radius_mm
    if wall_thickness <= config.upper_cutter_clearance_mm:
        raise ValueError(f"导管 {guide.guide_index} 主体壁厚不足以保留固定孔安全余量")

    if label == "upper":
        # 上梁尽量嵌入已知参数壁厚，但在固定孔边界前保留安全余量。
        overlap = min(
            2.0 * config.connector_radius_mm,
            wall_thickness - config.upper_cutter_clearance_mm,
        )
    elif label == "lower":
        # 下梁全直径预埋，最终统一复切导孔。
        overlap = 2.0 * config.connector_radius_mm
    else:
        raise ValueError(f"未知导管锚点标签：{label}")

    position = surface_contact + surface_normal * (config.connector_radius_mm - overlap)
    fraction = (axial_position_mm - t_min) / guide.length_mm
    return SleeveAnchorPoint(
        label=label,
        axial_fraction=fraction,
        axial_position_mm=axial_position_mm,
        section_center=section_center,
        surface_contact=surface_contact,
        surface_normal=surface_normal,
        position=position,
        connector_radius_mm=config.connector_radius_mm,
        tube_overlap_mm=overlap,
        local_wall_thickness_mm=wall_thickness,
    )


def _select_for_guide(
    guide: GuideSleeve,
    direction: Vec3,
    config: SleeveAnchorSelectionConfig,
) -> SleeveAnchorSelection:
    """按闭合低端和 C 口高端选择一个导管的低、高锚点。"""

    t_min, t_max = _stable_outer_wall_interval(guide)
    t_upper = t_max - config.upper_edge_clearance_mm - config.connector_radius_mm
    t_lower = t_min + config.lower_edge_clearance_mm + config.connector_radius_mm
    if t_upper <= t_lower:
        raise ValueError(
            f"导管 {guide.guide_index} 的稳定外壁区间无法容纳直径 "
            f"{2.0 * config.connector_radius_mm:g} mm 的上下连接梁"
        )
    if t_upper > t_max - config.axial_margin_mm or t_lower < t_min + config.axial_margin_mm:
        raise ValueError(f"导管 {guide.guide_index} 的 Q 点违反轴向安全余量")
    return SleeveAnchorSelection(
        guide_index=guide.guide_index,
        radial_direction=direction,
        lower=_contact_position(guide, "lower", t_lower, direction, config),
        upper=_contact_position(guide, "upper", t_upper, direction, config),
        platform=SleevePlatformEnvelope(
            origin=guide.center,
            axis=guide.axis.normalized(),
            opening_direction=(-direction).normalized(),
            across_direction=(guide.axis.normalized().cross(-direction)).normalized(),
            axial_min_mm=guide.parameters.height - guide.parameters.platform_height,
            axial_max_mm=guide.parameters.height,
            opening_min_mm=0.0,
            opening_max_mm=(guide.parameters.outer_radius + guide.parameters.platform_overhang),
            across_min_mm=-guide.parameters.outer_radius,
            across_max_mm=guide.parameters.outer_radius,
        ),
    )


def select_sleeve_anchors(
    case: CaseAnalysis,
    sleeves: SleeveGenerationResult,
    config: SleeveAnchorSelectionConfig | None = None,
) -> SleeveAnchorPlan:
    """在每根标准导管的同一外侧母线上选择上下 Q/P 点。

    参数:
        case: 包含本次病例导管的分析结果。
        sleeves: 第 1 步从输入装配体识别出的导管及轴向信息。
        config: 梁半径、上下端缘净距和嵌入安全余量；省略时使用
        直径 4.60 mm 的默认连接梁。

    返回:
        每根导管成对向外方向上的上下表面点 Q 和中心线点 P。

    算法:
        使用第 1 步识别的导管轴线和标准高度。C 口反方向作为
        成对向外方向；锚点参数
        从闭合低端向 C 口高端递增，高端位置为
        ``t_max - 2 mm - r``，低端位置为 ``t_min + 1 mm + r``。
        Q 位于参数化主体外壁，P 按嵌入规则沿 Q 的法向偏移；低端梁
        允许全直径预埋并在最终模型中复切导孔。
    """

    if case.guide_sleeves != sleeves.sleeves:
        raise ValueError("病例分析与导管生成结果不一致")
    selection_config = config or SleeveAnchorSelectionConfig()
    selections = []
    if len(sleeves.sleeves) % 2:
        raise ValueError("导管数量必须为偶数并按种植位成对输入")
    for pair_start in range(0, len(sleeves.sleeves), 2):
        pair = sleeves.sleeves[pair_start : pair_start + 2]
        pair_midpoint = (pair[0].center + pair[1].center) * 0.5
        for guide in pair:
            direction = _body_wall_direction(guide)
            if (guide.center - pair_midpoint).dot(direction) <= 0.0:
                raise ValueError(f"导管 {guide.guide_index} 的 C 口反方向没有远离所属双导管中点")
            selections.append(
                _select_for_guide(
                    guide,
                    direction,
                    selection_config,
                )
            )
    return SleeveAnchorPlan(tuple(selections))

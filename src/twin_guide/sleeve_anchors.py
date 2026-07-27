"""第 4 步的导管侧表面接触点 Q 和梁中心线点 P 选择。"""

from __future__ import annotations

from dataclasses import dataclass

from twin_guide.blender.mesh_queries import build_bvh, ray_cast_mesh
from twin_guide.config import DEFAULT_CONNECTOR_DIAMETER_MM, SleeveGeometryMode
from twin_guide.errors import GeometryError
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
        input_lower_wall_overlap_mm: 输入导管模式下低梁嵌入真实外壁的深度。
    """

    connector_radius_mm: float = DEFAULT_CONNECTOR_DIAMETER_MM / 2.0
    upper_edge_clearance_mm: float = 2.0
    lower_edge_clearance_mm: float = 1.0
    axial_margin_mm: float = 0.8
    upper_cutter_clearance_mm: float = 0.01
    input_lower_wall_overlap_mm: float = 1.45

    def __post_init__(self) -> None:
        """校验半径、净距和安全余量。"""

        if self.connector_radius_mm <= 0.0:
            raise ValueError("连接梁半径必须为正")
        if min(
            self.upper_edge_clearance_mm,
            self.lower_edge_clearance_mm,
            self.axial_margin_mm,
            self.upper_cutter_clearance_mm,
            self.input_lower_wall_overlap_mm,
        ) < 0.0:
            raise ValueError("导管锚点净距和安全余量不得为负")


@dataclass(frozen=True, slots=True)
class SleeveAnchorPoint:
    """一组导管表面接触点 Q 和梁中心线点 P。

    ``surface_contact`` 是输入导柱真实外壁上的 Q；``position`` 是后续
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
class SleeveAnchorSelection:
    """一个导管的成对向外方向及上下 Q/P 点。"""

    guide_index: int
    radial_direction: Vec3
    lower: SleeveAnchorPoint
    upper: SleeveAnchorPoint


@dataclass(frozen=True, slots=True)
class SleeveAnchorPlan:
    """所有导管的 Q/P 锚点计划。"""

    selections: tuple[SleeveAnchorSelection, ...]


def _body_wall_direction(guide: GuideSleeve) -> Vec3:
    """返回 C 口反方向在导管轴线法平面上的单位向量。"""

    radial = -project_to_plane(guide.parameters.c_opening_direction, guide.axis)
    return radial.normalized()


def _stable_outer_wall_interval(guide: GuideSleeve) -> tuple[float, float]:
    """返回输入导柱实际轴向范围。"""

    return guide.axial_min_mm, guide.axial_max_mm


def _contact_position(
    guide: GuideSleeve,
    label: str,
    axial_position_mm: float,
    direction: Vec3,
    config: SleeveAnchorSelectionConfig,
    use_input_surface: bool,
) -> SleeveAnchorPoint:
    """射线求输入导柱真实外壁 Q，并按上下嵌入规则生成 P。"""

    # 输入导柱轴线已规范化为以 C 口高端为 axis_origin，+axis 指向
    # 闭合低端。锚点参数按相反方向递增，因此用闭合端作为原点映射。
    t_min, t_max = _stable_outer_wall_interval(guide)
    closed_low_end = guide.center + guide.axis * t_max
    section_center = closed_low_end - guide.axis * (axial_position_mm - t_min)
    if not use_input_surface or guide.guide_mesh is None:
        # 纯几何单元测试和外部规划调用可以不携带 Blender 网格；生产病例
        # 始终包含输入导柱网格并进入下方的真实表面射线分支。
        surface_contact = section_center + direction * guide.body_radius_mm
    else:
        guide_tree = build_bvh(guide.guide_mesh)
        ray_start_distance = max(
            10.0,
            4.0 * guide.outer_radius_mm,
            4.0 * config.connector_radius_mm,
        )
        surface_contact = ray_cast_mesh(
            guide_tree,
            section_center + direction * ray_start_distance,
            -direction,
        )
        if surface_contact is None:
            raise GeometryError(
                f"导管 {guide.guide_index} 在 {label} 锚点截面无法命中输入外壁"
            )
    surface_normal = direction
    contact_radius = (surface_contact - section_center).dot(direction)
    wall_thickness = contact_radius - guide.bore_radius_mm
    if wall_thickness <= config.upper_cutter_clearance_mm:
        raise ValueError(
            f"导管 {guide.guide_index} 主体壁厚不足以保留固定孔安全余量"
        )

    if label == "upper":
        # 上梁尽量嵌入已知参数壁厚，但在固定孔边界前保留安全余量。
        overlap = min(
            2.0 * config.connector_radius_mm,
            wall_thickness - config.upper_cutter_clearance_mm,
        )
    elif label == "lower":
        if use_input_surface:
            # 输入导管不会再做全局导孔复切，因此低梁只嵌入真实外壁，
            # 并在配置内孔前保留安全余量，避免连接梁填塞现有导孔。
            overlap = min(
                config.input_lower_wall_overlap_mm,
                wall_thickness - config.upper_cutter_clearance_mm,
            )
        else:
            # 生成模式保持旧规则：下梁全直径预埋，最终权威复切导孔。
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
    use_input_surface: bool,
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
    if (
        t_upper > t_max - config.axial_margin_mm
        or t_lower < t_min + config.axial_margin_mm
    ):
        raise ValueError(f"导管 {guide.guide_index} 的 Q 点违反轴向安全余量")
    return SleeveAnchorSelection(
        guide_index=guide.guide_index,
        radial_direction=direction,
        lower=_contact_position(
            guide, "lower", t_lower, direction, config, use_input_surface
        ),
        upper=_contact_position(
            guide, "upper", t_upper, direction, config, use_input_surface
        ),
    )


def select_sleeve_anchors(
    case: CaseAnalysis,
    sleeves: SleeveGenerationResult,
    config: SleeveAnchorSelectionConfig | None = None,
) -> SleeveAnchorPlan:
    """在每根输入导柱的同一外侧母线上选择上下 Q/P 点。

    参数:
        case: 包含本次病例导管的分析结果。
        sleeves: 第 1 步从输入装配体识别出的导柱及轴向信息。
        config: 梁半径、上下端缘净距和嵌入安全余量；省略时使用
            直径 4.60 mm 的默认连接梁。

    返回:
        每根导管成对向外方向上的上下表面点 Q 和中心线点 P。

    算法使用第 1 步识别的导管轴线和输入导柱实际高度，并从轴线向外
    反向射线命中真实输入外壁。C 口反方向作为成对向外方向；锚点参数
    从闭合低端向 C 口高端
    递增，高端位置为 ``t_max - 2 mm - r``，低端位置为
    ``t_min + 1 mm + r``。Q 位于主体外壁，P 按嵌入规则沿 Q 的法向
    偏移；生成模式低端梁允许全直径预埋，输入模式低端梁只嵌入真实外壁，
    高端梁始终受局部壁厚限制。
    """

    if case.guide_sleeves != sleeves.sleeves:
        raise ValueError("病例分析与导套生成结果不一致")
    selection_config = config or SleeveAnchorSelectionConfig()
    use_input_surface = (
        getattr(getattr(case, "config", None), "sleeve_geometry_mode", None)
        is SleeveGeometryMode.INPUT
    )
    selections = []
    if len(sleeves.sleeves) % 2:
        raise ValueError("导管数量必须为偶数并按种植位成对输入")
    for pair_start in range(0, len(sleeves.sleeves), 2):
        pair = sleeves.sleeves[pair_start : pair_start + 2]
        pair_midpoint = (pair[0].center + pair[1].center) * 0.5
        for guide in pair:
            direction = _body_wall_direction(guide)
            if (guide.center - pair_midpoint).dot(direction) <= 0.0:
                raise ValueError(
                    f"导管 {guide.guide_index} 的 C 口反方向没有远离所属双导管中点"
                )
            selections.append(
                _select_for_guide(
                    guide,
                    direction,
                    selection_config,
                    use_input_surface,
                )
            )
    return SleeveAnchorPlan(tuple(selections))

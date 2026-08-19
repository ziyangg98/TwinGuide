"""末端缺牙病例的远中公共梁中心节点选择。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from twin_guide.errors import GeometryError
from twin_guide.geometry import Vec3
from twin_guide.models import GuideSleeve
from twin_guide.types import GenerationContext


@dataclass(frozen=True, slots=True)
class TerminalDistalCommonNodePlan:
    """四根导管梁共享的远中自由节点。"""

    missing_fdi: int
    reference_neighbor_fdi: int
    centerline_node: Vec3
    node_radius_mm: float
    distal_direction: Vec3
    distal_offset_mm: float
    projection_base: Vec3


def _vec3(value: np.ndarray) -> Vec3:
    """将 NumPy 三维向量转换为框架 Vec3。"""

    return Vec3(*(float(item) for item in value))


def _terminal_arch_direction(
    fdi_order: tuple[int, ...],
    missing_fdi: int,
    reference_neighbor_fdi: int,
    neighbor_tangent: Vec3,
    implant_fdis: tuple[int, ...] = (),
) -> np.ndarray:
    """由统一牙位顺序和邻牙切向返回指向末端缺牙的方向。"""

    try:
        missing_index = fdi_order.index(missing_fdi)
        neighbor_index = fdi_order.index(reference_neighbor_fdi)
    except ValueError as error:
        raise GeometryError("末端节点引用的牙位不在统一 FDI 顺序中") from error
    expected_index_gap = len(implant_fdis) if implant_fdis else 1
    if abs(missing_index - neighbor_index) != expected_index_gap:
        raise GeometryError("远中公共节点参考牙与末端缺牙的牙位跨度不符合配置")
    if implant_fdis:
        ordered_indices = tuple(fdi_order.index(fdi) for fdi in implant_fdis)
        step = 1 if missing_index > neighbor_index else -1
        expected_indices = tuple(
            neighbor_index + step * offset for offset in range(1, len(implant_fdis) + 1)
        )
        if ordered_indices != expected_indices:
            raise GeometryError("双种植位远中公共节点的种植牙位顺序不连续")
    if missing_index not in {0, len(fdi_order) - 1}:
        raise GeometryError("远中公共节点只适用于牙弓末端缺牙")
    order_step = 1.0 if missing_index > neighbor_index else -1.0
    direction = np.asarray(neighbor_tangent.as_tuple(), dtype=float) * order_step
    length = float(np.linalg.norm(direction))
    if length <= 1e-8:
        raise GeometryError("末端参考邻牙缺少有效的局部牙弓切向")
    return direction / length


def select_terminal_distal_common_node(
    context: GenerationContext,
    projection_base: Vec3,
    *,
    terminal_guides: tuple[GuideSleeve, GuideSleeve] | None = None,
) -> TerminalDistalCommonNodePlan:
    """由 B 沿远中方向平移两个平均导管外径，直接得到 G。"""

    if context.case is None or context.tooth_identification is None:
        raise GeometryError("远中公共节点缺少病例或牙位识别结果")
    parameters = context.config.guide_anchors.terminal_distal_common_node
    if parameters is None:
        raise GeometryError("病例未配置 terminal_distal_common_node")
    if parameters.missing_fdi not in context.tooth_identification.missing_teeth:
        raise GeometryError(f"远中公共节点 FDI {parameters.missing_fdi} 不是配置的缺失牙")
    positions = {position.fdi: position for position in context.tooth_identification.positions}
    neighbor = positions.get(parameters.reference_neighbor_fdi)
    if neighbor is None:
        raise GeometryError(f"远中公共节点参考邻牙 {parameters.reference_neighbor_fdi} 不是现存牙")
    expected_distal = _terminal_arch_direction(
        context.tooth_identification.fdi_order,
        parameters.missing_fdi,
        parameters.reference_neighbor_fdi,
        neighbor.local_tangent,
        parameters.implant_fdis,
    )
    if context.sleeve_generation is None:
        raise GeometryError("远中公共节点缺少两导管位姿")
    guides = context.sleeve_generation.sleeves if terminal_guides is None else terminal_guides
    if len(guides) != 2:
        raise GeometryError("远中公共节点必须由末端种植位的两根导管确定")

    centerline = np.asarray((guides[1].center - guides[0].center).as_tuple())
    centerline /= max(float(np.linalg.norm(centerline)), 1e-12)
    first_axis = np.asarray(guides[0].axis.as_tuple())
    second_axis = np.asarray(guides[1].axis.as_tuple())
    if float(np.dot(first_axis, second_axis)) < 0.0:
        second_axis = -second_axis
    common_axis = first_axis + second_axis
    common_axis /= max(float(np.linalg.norm(common_axis)), 1e-12)
    distal_direction = np.cross(centerline, common_axis)
    distal_length = float(np.linalg.norm(distal_direction))
    if distal_length <= 1e-8:
        raise GeometryError("两导管中心连线与公共轴线无法确定远中方向")
    distal_direction /= distal_length
    if float(np.dot(distal_direction, expected_distal)) < 0.0:
        distal_direction = -distal_direction

    average_sleeve_diameter_mm = sum(2.0 * guide.body_radius_mm for guide in guides) / 2.0
    distal_offset_mm = average_sleeve_diameter_mm * parameters.distal_offset_sleeve_diameters
    base = np.asarray(projection_base.as_tuple())
    center = base + distal_direction * distal_offset_mm
    node_radius = context.config.geometry.connector_radius_mm * parameters.node_radius_factor
    return TerminalDistalCommonNodePlan(
        missing_fdi=parameters.missing_fdi,
        reference_neighbor_fdi=parameters.reference_neighbor_fdi,
        centerline_node=_vec3(center),
        node_radius_mm=node_radius,
        distal_direction=_vec3(distal_direction),
        distal_offset_mm=distal_offset_mm,
        projection_base=_vec3(base),
    )

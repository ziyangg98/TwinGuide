"""末端缺牙病例的远中公共梁中心节点选择。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

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


def _base_mapping_report(mapping_report: dict[str, object]) -> dict[str, object]:
    """读取牙位映射引用的基础坐标报告。"""

    sources = mapping_report.get("sources")
    if not isinstance(sources, dict):
        raise GeometryError("牙位报告缺少 sources")
    raw_path = sources.get("base_coordinate_report")
    if not isinstance(raw_path, str) or not raw_path:
        raise GeometryError("牙位报告缺少 base_coordinate_report")
    path = Path(raw_path)
    if not path.is_file():
        raise GeometryError(f"基础牙位映射不存在：{path}")
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GeometryError(f"无法读取基础牙位映射：{path}") from error
    if not isinstance(report, dict):
        raise GeometryError("基础牙位映射必须为对象")
    return report


def _slot(report: dict[str, object], fdi: int) -> dict[str, object]:
    """从基础映射报告中取得指定 FDI 的牙位槽记录。"""

    slots = report.get("tooth_slots")
    if not isinstance(slots, list):
        raise GeometryError("基础牙位映射缺少 tooth_slots")
    for value in slots:
        if isinstance(value, dict) and value.get("FDI") == fdi:
            return value
    raise GeometryError(f"基础牙位映射中不存在 FDI {fdi}")


def _numeric_vec3(value: object, name: str) -> np.ndarray:
    """校验并返回三元素数值数组。"""

    if not (
        isinstance(value, list)
        and len(value) == 3
        and all(isinstance(item, int | float) for item in value)
    ):
        raise GeometryError(f"{name} 必须为三元素数值数组")
    return np.asarray(value, dtype=float)


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
        raise GeometryError(
            f"远中公共节点 FDI {parameters.missing_fdi} 不是配置的缺失牙"
        )
    present_fdis = {position.fdi for position in context.tooth_identification.positions}
    if parameters.reference_neighbor_fdi not in present_fdis:
        raise GeometryError(
            f"远中公共节点参考邻牙 {parameters.reference_neighbor_fdi} 不是现存牙"
        )

    base_report = _base_mapping_report(context.tooth_identification.mapping_report)
    missing_slot = _slot(base_report, parameters.missing_fdi)
    neighbor_slot = _slot(base_report, parameters.reference_neighbor_fdi)
    if missing_slot.get("status") != "missing_slot":
        raise GeometryError("远中公共节点目标牙位在基础映射中不是 missing_slot")
    fdi_order = tuple(int(value) for value in base_report["semantics"]["FDI_order"])
    missing_index = fdi_order.index(parameters.missing_fdi)
    neighbor_index = fdi_order.index(parameters.reference_neighbor_fdi)
    implant_fdis = parameters.implant_fdis
    expected_index_gap = len(implant_fdis) if implant_fdis else 1
    if abs(missing_index - neighbor_index) != expected_index_gap:
        raise GeometryError("远中公共节点参考牙与末端缺牙的牙位跨度不符合配置")
    if implant_fdis:
        ordered_indices = tuple(fdi_order.index(fdi) for fdi in implant_fdis)
        step = 1 if missing_index > neighbor_index else -1
        expected_indices = tuple(
            neighbor_index + step * offset
            for offset in range(1, len(implant_fdis) + 1)
        )
        if ordered_indices != expected_indices:
            raise GeometryError("双种植位远中公共节点的种植牙位顺序不连续")
    if missing_index not in {0, len(fdi_order) - 1}:
        raise GeometryError("远中公共节点只适用于牙弓末端缺牙")

    missing_point = _numeric_vec3(
        missing_slot.get("dental_crown_point_global_mm"),
        "missing dental crown point",
    )
    neighbor_point = _numeric_vec3(
        neighbor_slot.get("dental_crown_point_global_mm"),
        "neighbor dental crown point",
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
    if float(np.dot(distal_direction, missing_point - neighbor_point)) < 0.0:
        distal_direction = -distal_direction

    average_sleeve_diameter_mm = sum(
        2.0 * guide.body_radius_mm for guide in guides
    ) / 2.0
    distal_offset_mm = (
        average_sleeve_diameter_mm
        * parameters.distal_offset_sleeve_diameters
    )
    base = np.asarray(projection_base.as_tuple())
    center = base + distal_direction * distal_offset_mm
    node_radius = (
        context.config.geometry.connector_radius_mm
        * parameters.node_radius_factor
    )
    return TerminalDistalCommonNodePlan(
        missing_fdi=parameters.missing_fdi,
        reference_neighbor_fdi=parameters.reference_neighbor_fdi,
        centerline_node=_vec3(center),
        node_radius_mm=node_radius,
        distal_direction=_vec3(distal_direction),
        distal_offset_mm=distal_offset_mm,
        projection_base=_vec3(base),
    )

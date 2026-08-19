"""规划从导板末端双锚点绕末端牙回转的 U 型延伸梁。"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import trimesh

from twin_guide.config import PressBeamGuideEndpointParameters
from twin_guide.errors import GeometryError
from twin_guide.geometry import Vec3
from twin_guide.tooth_section_anchors import select_tooth_section_local_anchor_pairs
from twin_guide.types import GenerationContext

EPS = 1e-9
DEFAULT_TOOTH_HALF_WIDTH_MM = 4.5
MAXIMUM_TOOTH_HALF_WIDTH_MM = 7.0


@dataclass(frozen=True, slots=True)
class GuideTerminalUExtensionPlan:
    """一个连续 U 型中心线及其双根部、牙体净距和诊断数据。"""

    centerline: tuple[Vec3, ...]
    radius_mm: float
    dental_clearance_mm: float
    u_surface_anchor: Vec3
    back_u_surface_anchor: Vec3
    u_surface_normal: Vec3
    back_u_surface_normal: Vec3
    endpoint_reinforcement: PressBeamGuideEndpointParameters | None
    trajectories: tuple[tuple[Vec3, ...], ...]
    u_side_centerline: tuple[Vec3, ...]
    turnaround_centerline: tuple[Vec3, ...]
    back_u_side_centerline: tuple[Vec3, ...]
    terminal_fdi: int
    reference_neighbor_fdi: int
    distal_direction: Vec3
    arch_outward_direction: Vec3
    turnaround_apex: Vec3
    distal_surface_extent_mm: float
    turnaround_entry_distal_mm: float
    turnaround_apex_distal_mm: float
    turnaround_surface_clearance_mm: float
    u_centerline_offset_mm: float
    back_u_centerline_offset_mm: float


def _unit(vector: np.ndarray, name: str) -> np.ndarray:
    """单位化方向向量并拒绝零向量。"""

    length = float(np.linalg.norm(vector))
    if length <= EPS:
        raise GeometryError(f"{name}方向为零向量")
    return np.asarray(vector, dtype=float) / length


def _vec3(vector: np.ndarray) -> Vec3:
    """将 NumPy 三维向量转换为 TwinGuide 向量。"""

    return Vec3(*(float(value) for value in vector))


def _load_mesh(path: object) -> trimesh.Trimesh:
    """读取并清理末端 U 型梁规划使用的牙列网格。"""

    loaded = trimesh.load(path, force="mesh", process=False)
    if isinstance(loaded, trimesh.Scene):
        loaded = loaded.dump(concatenate=True)
    if not isinstance(loaded, trimesh.Trimesh) or loaded.is_empty:
        raise GeometryError(f"末端 U 型延伸梁牙列输入为空：{path}")
    mesh = loaded.copy()
    mesh.merge_vertices()
    mesh.remove_unreferenced_vertices()
    return mesh


def _quintic_segment(
    start: np.ndarray,
    end: np.ndarray,
    start_tangent: np.ndarray,
    end_tangent: np.ndarray,
    maximum_step_mm: float = 0.30,
) -> tuple[np.ndarray, ...]:
    """以零端点二阶导数的五次 Hermite 段连接两个空间点。"""

    chord = float(np.linalg.norm(end - start))
    sample_count = max(17, math.ceil(chord / maximum_step_mm) + 1)
    if sample_count % 2 == 0:
        sample_count += 1
    samples = []
    for index in range(sample_count):
        u = index / (sample_count - 1)
        u2 = u * u
        u3 = u2 * u
        u4 = u3 * u
        u5 = u4 * u
        h_start = 1.0 - 10.0 * u3 + 15.0 * u4 - 6.0 * u5
        h_start_tangent = u - 6.0 * u3 + 8.0 * u4 - 3.0 * u5
        h_end = 10.0 * u3 - 15.0 * u4 + 6.0 * u5
        h_end_tangent = -4.0 * u3 + 7.0 * u4 - 3.0 * u5
        samples.append(
            start * h_start
            + start_tangent * h_start_tangent
            + end * h_end
            + end_tangent * h_end_tangent
        )
    return tuple(samples)


def _line_samples(
    start: np.ndarray,
    end: np.ndarray,
    maximum_step_mm: float = 0.30,
) -> tuple[np.ndarray, ...]:
    """按最大步长在线段上均匀采样。"""

    count = max(2, math.ceil(float(np.linalg.norm(end - start)) / maximum_step_mm) + 1)
    return tuple(start + (end - start) * (index / (count - 1)) for index in range(count))


def _turnaround_distal_positions(
    distal_surface_extent_mm: float,
    centerline_clearance_mm: float,
    turnaround_depth_mm: float,
) -> tuple[float, float]:
    """计算不重复叠加回转深度的入口和远中顶点位置。

    ``centerline_clearance_mm`` 已包含梁半径、牙体净距和安全余量。
    回转深度只控制两侧入口到远中顶点的曲率，不应再加到顶点净距上。
    """

    apex_distal_mm = distal_surface_extent_mm + centerline_clearance_mm
    entry_distal_mm = apex_distal_mm - turnaround_depth_mm
    if entry_distal_mm <= 0.0:
        raise GeometryError("末端 U 型梁 turnaround_depth_mm 过大：回转入口会越过末端牙中心")
    return entry_distal_mm, apex_distal_mm


def _slot(mapping_report: dict[str, object], fdi: int) -> dict[str, object]:
    """从牙位映射报告中取得指定 FDI 的牙槽位。"""

    slots = mapping_report.get("tooth_slots")
    if not isinstance(slots, list):
        raise GeometryError("牙位报告缺少 tooth_slots")
    for value in slots:
        if isinstance(value, dict) and value.get("FDI") == fdi:
            return value
    raise GeometryError(f"牙位报告中不存在 FDI {fdi}")


def _distal_extent_from_slot(
    slot: dict[str, object],
    terminal_arch_s_mm: float,
    neighbor_arch_s_mm: float,
) -> float:
    """从牙槽弧长区间估计末端牙的远中表面范围。"""

    interval = slot.get("arch_interval_s_mm")
    if not (
        isinstance(interval, list)
        and len(interval) == 2
        and all(isinstance(value, int | float) for value in interval)
    ):
        return 4.0
    distal_positive = terminal_arch_s_mm > neighbor_arch_s_mm
    boundary = float(interval[1] if distal_positive else interval[0])
    return max(2.5, abs(boundary - terminal_arch_s_mm))


def _terminal_lateral_extents(
    dentition: trimesh.Trimesh,
    terminal_center: np.ndarray,
    distal: np.ndarray,
    outward: np.ndarray,
    occlusal: np.ndarray,
    distal_extent_mm: float,
) -> tuple[float, float]:
    """从末端牙冠邻域估算 U/背 U 两侧的实际表面外包络。"""

    relative = np.asarray(dentition.vertices, dtype=float) - terminal_center
    distal_coordinates = relative @ distal
    outward_coordinates = relative @ outward
    axial_coordinates = relative @ occlusal
    local_mask = (
        (distal_coordinates >= -(distal_extent_mm + 1.5))
        & (distal_coordinates <= distal_extent_mm + 1.5)
        & (np.abs(outward_coordinates) <= 9.0)
        & (axial_coordinates >= -8.0)
        & (axial_coordinates <= 1.0)
    )
    lateral = outward_coordinates[local_mask]
    if len(lateral) < 30:
        return -DEFAULT_TOOTH_HALF_WIDTH_MM, DEFAULT_TOOTH_HALF_WIDTH_MM
    u_surface = float(np.quantile(lateral, 0.02))
    back_u_surface = float(np.quantile(lateral, 0.98))
    u_surface = max(-MAXIMUM_TOOTH_HALF_WIDTH_MM, min(u_surface, -2.5))
    back_u_surface = min(MAXIMUM_TOOTH_HALF_WIDTH_MM, max(back_u_surface, 2.5))
    if back_u_surface - u_surface < 5.0:
        return -DEFAULT_TOOTH_HALF_WIDTH_MM, DEFAULT_TOOTH_HALF_WIDTH_MM
    return u_surface, back_u_surface


def _terminal_contour_extents(
    mapping_report: dict[str, object],
    terminal_fdi: int,
    terminal_center: np.ndarray,
    distal: np.ndarray,
    outward: np.ndarray,
) -> tuple[float, float, float] | None:
    """读取牙位识别的独立闭合牙冠轮廓，返回远中和两侧真实外包络。"""

    sources = mapping_report.get("sources")
    if not isinstance(sources, dict):
        return None
    report_value = sources.get("contact_chord_report")
    if not isinstance(report_value, str) or not report_value:
        return None
    report_path = Path(report_value)
    if not report_path.is_file():
        return None
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    coordinate_system = report.get("coordinate_system")
    contours = report.get("contours")
    if not isinstance(coordinate_system, dict) or not isinstance(contours, list):
        return None
    raw_origin = coordinate_system.get("origin_global_mm")
    raw_lr = coordinate_system.get("e_patient_right_to_left")
    raw_ap = coordinate_system.get("e_anterior_to_posterior")
    if not all(
        isinstance(value, list)
        and len(value) == 3
        and all(isinstance(item, int | float) for item in value)
        for value in (raw_origin, raw_lr, raw_ap)
    ):
        return None
    contour_value = next(
        (
            item.get("contour_LR_AP_mm")
            for item in contours
            if isinstance(item, dict) and item.get("FDI") == terminal_fdi
        ),
        None,
    )
    if not (
        isinstance(contour_value, list)
        and len(contour_value) >= 3
        and all(
            isinstance(point, list)
            and len(point) == 2
            and all(isinstance(value, int | float) for value in point)
            for point in contour_value
        )
    ):
        return None
    origin = np.asarray(raw_origin, dtype=float)
    e_lr = _unit(np.asarray(raw_lr, dtype=float), "牙冠轮廓左右轴")
    e_ap = _unit(np.asarray(raw_ap, dtype=float), "牙冠轮廓前后轴")
    contour = np.asarray(
        [origin + e_lr * float(point[0]) + e_ap * float(point[1]) for point in contour_value],
        dtype=float,
    )
    relative = contour - terminal_center
    distal_coordinates = relative @ distal
    outward_coordinates = relative @ outward
    return (
        max(2.5, float(np.max(distal_coordinates))),
        float(np.min(outward_coordinates)),
        float(np.max(outward_coordinates)),
    )


def _station_center(
    station_fdis: tuple[int, ...],
    positions: dict[int, object],
) -> np.ndarray:
    """计算牙位站所有牙冠中心的平均位置。"""

    values = [
        np.asarray(positions[fdi].crown_point.as_tuple(), dtype=float) for fdi in station_fdis
    ]
    return np.mean(values, axis=0)


def select_guide_terminal_u_extension(
    context: GenerationContext,
) -> GuideTerminalUExtensionPlan:
    """按牙位双射线锚点和末端牙外包络生成连续 U 型中心线。"""

    config = context.config.guide_terminal_u_extension
    if not config.enabled:
        raise GeometryError("当前病例未启用末端 U 型延伸梁")
    if context.case is None or context.tooth_identification is None:
        raise GeometryError("末端 U 型延伸梁缺少病例或牙位识别结果")
    if (
        config.anchor_station is None
        or config.terminal_fdi is None
        or config.reference_neighbor_fdi is None
    ):
        raise GeometryError("末端 U 型延伸梁配置不完整")

    positions = {position.fdi: position for position in context.tooth_identification.positions}
    required_fdis = {
        *config.anchor_station.fdis,
        config.terminal_fdi,
        config.reference_neighbor_fdi,
    }
    missing = sorted(required_fdis - positions.keys())
    if missing:
        raise GeometryError(f"末端 U 型延伸梁引用了未识别牙位：{missing}")
    terminal_quadrant, terminal_position = divmod(config.terminal_fdi, 10)
    neighbor_quadrant, neighbor_position = divmod(config.reference_neighbor_fdi, 10)
    if terminal_quadrant != neighbor_quadrant or terminal_position != neighbor_position + 1:
        raise GeometryError("末端 U 型延伸梁参考牙必须是终末牙的直接近中邻牙")
    if any(
        divmod(fdi, 10)[0] == terminal_quadrant and divmod(fdi, 10)[1] > terminal_position
        for fdi in positions
    ):
        raise GeometryError("末端 U 型延伸梁 terminal_fdi 不是当前牙列末端")

    selection = select_tooth_section_local_anchor_pairs(
        context.case,
        context.tooth_identification,
        (config.anchor_station,),
        (
            (
                config.u_side_ray_angle_degrees,
                config.back_u_side_ray_angle_degrees,
            ),
        ),
    )[0]
    terminal = positions[config.terminal_fdi]
    neighbor = positions[config.reference_neighbor_fdi]
    coordinate_system = context.tooth_identification.mapping_report.get("coordinate_system")
    if not isinstance(coordinate_system, dict):
        raise GeometryError("牙位报告缺少 coordinate_system")
    occlusal = _unit(np.asarray(coordinate_system.get("e_occ"), dtype=float), "牙合轴")

    terminal_center = np.asarray(terminal.crown_point.as_tuple(), dtype=float)
    neighbor_center = np.asarray(neighbor.crown_point.as_tuple(), dtype=float)
    neighbor_to_terminal = terminal_center - neighbor_center
    neighbor_to_terminal -= occlusal * float(np.dot(neighbor_to_terminal, occlusal))
    terminal_tangent = np.asarray(terminal.local_tangent.as_tuple(), dtype=float)
    terminal_tangent -= occlusal * float(np.dot(terminal_tangent, occlusal))
    if float(np.dot(terminal_tangent, neighbor_to_terminal)) < 0.0:
        terminal_tangent = -terminal_tangent
    distal = _unit(terminal_tangent, "末端牙远中")
    outward = np.asarray(terminal.local_outward.as_tuple(), dtype=float)
    outward -= occlusal * float(np.dot(outward, occlusal))
    outward -= distal * float(np.dot(outward, distal))
    outward = _unit(outward, "末端牙局部外向")
    if float(np.dot(outward, np.asarray(terminal.local_outward.as_tuple()))) < 0.0:
        outward = -outward

    contour_extents = _terminal_contour_extents(
        context.tooth_identification.mapping_report,
        config.terminal_fdi,
        terminal_center,
        distal,
        outward,
    )
    if contour_extents is None:
        dentition = _load_mesh(context.case.config.inputs.patient_dentition)
        terminal_slot = _slot(context.tooth_identification.mapping_report, config.terminal_fdi)
        distal_extent = _distal_extent_from_slot(
            terminal_slot,
            terminal.arch_s_mm,
            neighbor.arch_s_mm,
        )
        u_surface, back_u_surface = _terminal_lateral_extents(
            dentition,
            terminal_center,
            distal,
            outward,
            occlusal,
            distal_extent,
        )
    else:
        distal_extent, u_surface, back_u_surface = contour_extents
    centerline_clearance = config.radius_mm + config.dental_clearance_mm + config.safety_margin_mm
    u_offset = u_surface - centerline_clearance
    back_u_offset = back_u_surface + centerline_clearance

    u_anchor = np.asarray(selection.first.position.as_tuple(), dtype=float)
    back_anchor = np.asarray(selection.second.position.as_tuple(), dtype=float)
    anchor_height = 0.5 * (float(np.dot(u_anchor, occlusal)) + float(np.dot(back_anchor, occlusal)))
    base = terminal_center + occlusal * (anchor_height - float(np.dot(terminal_center, occlusal)))
    u_terminal = base + outward * u_offset
    back_terminal = base + outward * back_u_offset
    entry_s, apex_distal_s = _turnaround_distal_positions(
        distal_extent,
        centerline_clearance,
        config.turnaround_depth_mm,
    )
    anchor_station_center = _station_center(config.anchor_station.fdis, positions)
    route_direction = terminal_center - anchor_station_center
    route_direction -= occlusal * float(np.dot(route_direction, occlusal))
    route_direction = _unit(route_direction, "锚点到末端牙")
    u_span = float(np.linalg.norm(u_terminal - u_anchor))
    back_span = float(np.linalg.norm(back_terminal - back_anchor))
    u_side = _quintic_segment(
        u_anchor,
        u_terminal,
        route_direction * (u_span * 0.45),
        distal * (u_span * 0.45),
    )
    back_side = _quintic_segment(
        back_terminal,
        back_anchor,
        -distal * (back_span * 0.45),
        -route_direction * (back_span * 0.45),
    )
    u_arc_start = u_terminal + distal * entry_s
    back_arc_end = back_terminal + distal * entry_s
    u_run = _line_samples(u_terminal, u_arc_start)
    back_run = _line_samples(back_arc_end, back_terminal)
    lateral_mid = 0.5 * (u_offset + back_u_offset)
    lateral_radius = 0.5 * (back_u_offset - u_offset)
    arc_sample_count = max(
        33,
        math.ceil(math.pi * max(config.turnaround_depth_mm, lateral_radius) / 0.30) + 1,
    )
    arc = tuple(
        base
        + distal
        * (
            entry_s
            + config.turnaround_depth_mm
            * math.cos(-math.pi / 2.0 + math.pi * index / (arc_sample_count - 1))
        )
        + outward
        * (
            lateral_mid
            + lateral_radius * math.sin(-math.pi / 2.0 + math.pi * index / (arc_sample_count - 1))
        )
        for index in range(arc_sample_count)
    )
    centerline_arrays = (
        *u_side,
        *u_run[1:],
        *arc[1:],
        *back_run[1:],
        *back_side[1:],
    )
    centerline = tuple(_vec3(point) for point in centerline_arrays)
    u_side_centerline = tuple(_vec3(point) for point in (*u_side, *u_run[1:]))
    turnaround_centerline = tuple(_vec3(point) for point in arc)
    back_u_side_centerline = tuple(_vec3(point) for point in (*back_run, *back_side[1:]))
    apex = base + distal * apex_distal_s + outward * lateral_mid
    return GuideTerminalUExtensionPlan(
        centerline=centerline,
        radius_mm=config.radius_mm,
        dental_clearance_mm=config.dental_clearance_mm,
        u_surface_anchor=selection.first.position,
        back_u_surface_anchor=selection.second.position,
        u_surface_normal=selection.first.normal,
        back_u_surface_normal=selection.second.normal,
        endpoint_reinforcement=config.endpoint_reinforcement,
        trajectories=selection.support_trajectories,
        u_side_centerline=u_side_centerline,
        turnaround_centerline=turnaround_centerline,
        back_u_side_centerline=back_u_side_centerline,
        terminal_fdi=config.terminal_fdi,
        reference_neighbor_fdi=config.reference_neighbor_fdi,
        distal_direction=_vec3(distal),
        arch_outward_direction=_vec3(outward),
        turnaround_apex=_vec3(apex),
        distal_surface_extent_mm=distal_extent,
        turnaround_entry_distal_mm=entry_s,
        turnaround_apex_distal_mm=apex_distal_s,
        turnaround_surface_clearance_mm=(apex_distal_s - config.radius_mm - distal_extent),
        u_centerline_offset_mm=u_offset,
        back_u_centerline_offset_mm=back_u_offset,
    )

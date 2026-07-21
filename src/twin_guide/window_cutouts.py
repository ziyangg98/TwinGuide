"""规划牙科导板上的导孔、操作窗和观察窗。"""

from __future__ import annotations

from twin_guide.geometry import Vec3, orthonormal_tangent
from twin_guide.models import (
    CaseAnalysis,
    CutoutPlan,
    CylinderCutout,
    SurfaceSample,
    WindowCutout,
    WindowPurpose,
)
from twin_guide.types import SleeveGenerationResult

WindowCutoutPlan = CutoutPlan

OBSERVATION_WIDTH_MM = 7.0
OBSERVATION_OUTSIDE_MARGIN_MM = 0.8
OBSERVATION_EDGE_MARGIN_MM = 2.0


def _plan_channels(case: CaseAnalysis) -> tuple[CylinderCutout, CylinderCutout]:
    """沿两个导套轴线构造带轴向余量的牙科导板通道。"""

    axial_margin_mm = case.config.geometry.channel_axial_margin_mm
    guide_inner_radius_mm = case.config.sleeve.inner_radius_mm
    channels = tuple(
        CylinderCutout(
            name=f"guide_{guide.guide_index}_channel",
            start=guide.center + guide.axis * (guide.axial_min_mm - axial_margin_mm),
            end=guide.center + guide.axis * (guide.axial_max_mm + axial_margin_mm),
            radius_mm=guide_inner_radius_mm,
        )
        for guide in case.guide_sleeves
    )
    return channels[0], channels[1]


def _plan_operation_window(case: CaseAnalysis) -> WindowCutout:
    """根据两导套间距、操作特征尺寸和局部牙科导板厚度规划操作窗。"""

    first_guide, second_guide = case.guide_sleeves
    center = case.operation_feature.center
    second_axis = (
        second_guide.axis if first_guide.axis.dot(second_guide.axis) >= 0 else -second_guide.axis
    )
    average_guide_axis = (first_guide.axis + second_axis).normalized()
    guide_offset = second_guide.center - first_guide.center
    tangent = (
        guide_offset - average_guide_axis * guide_offset.dot(average_guide_axis)
    ).normalized()
    normal = average_guide_axis
    short_direction = normal.cross(tangent).normalized()
    guide_spacing_mm = abs(guide_offset.dot(tangent))
    short_edge_mm = case.operation_feature.diameter_mm + 2.0 * (
        case.config.windows.operation_bitangent_margin_mm
    )
    long_edge_mm = (
        guide_spacing_mm
        + first_guide.body_radius_mm
        + second_guide.body_radius_mm
        + 2.0 * case.config.windows.operation_tangent_margin_mm
    )
    local_samples = tuple(
        sample
        for sample in case.template_samples
        if abs((sample.position - center).dot(tangent)) <= long_edge_mm * 0.5 + 2.0
        and abs((sample.position - center).dot(short_direction)) <= short_edge_mm * 0.5 + 2.0
    )
    depth_coordinates = tuple(
        (sample.position - center).dot(normal)
        for sample in local_samples
    )
    local_depth_mm = max(depth_coordinates) - min(depth_coordinates)
    depth_mm = (
        max(
            local_depth_mm,
            first_guide.length_mm,
            second_guide.length_mm,
        )
        + 2.0 * case.config.geometry.channel_axial_margin_mm
    )
    return WindowCutout(
        name="operation_window",
        purpose=WindowPurpose.OPERATION,
        center=center,
        normal=normal,
        tangent=tangent,
        width_mm=long_edge_mm,
        height_mm=short_edge_mm,
        depth_mm=depth_mm,
        corner_radius_mm=min(
            1.0,
            max(0.2, case.config.windows.operation_bitangent_margin_mm),
        ),
    )


def _nearest_surface_sample(
    case: CaseAnalysis,
    lateral_mm: float,
    depth_mm: float,
) -> SurfaceSample:
    """在牙科导板局部横向—深度平面中返回距离目标最近的表面样本。"""

    return min(
        case.template_samples,
        key=lambda sample: (
            (case.template_frame.coordinates(sample.position)[0] - lateral_mm) ** 2
            + (case.template_frame.coordinates(sample.position)[1] - depth_mm) ** 2,
            sample.polygon_index,
        ),
    )


def _observation_notch(
    case: CaseAnalysis,
    name: str,
    lateral_mm: float,
    depth_mm: float,
    width_mm: float,
    tooth_position: Vec3,
) -> WindowCutout:
    """从导板下缘向上构造带水平止线的开放式观察缺口。"""

    frame = case.template_frame
    target_sample = _nearest_surface_sample(case, lateral_mm, depth_mm)
    target_lateral, target_depth, _ = frame.coordinates(target_sample.position)
    surface_normal = target_sample.normal.normalized()
    if surface_normal.dot(frame.depth) < 0.0:
        surface_normal = -surface_normal
    tangent = orthonormal_tangent(surface_normal, frame.lateral)
    bitangent = surface_normal.cross(tangent).normalized()
    # frame.normal 在建立局部标架时已按 jaw 定向到牙合侧。
    if bitangent.dot(frame.normal) < 0.0:
        tangent = -tangent
        bitangent = -bitangent

    local_samples = tuple(
        candidate
        for candidate in case.template_samples
        if abs(frame.coordinates(candidate.position)[0] - target_lateral) <= width_mm * 0.5 + 1.0
        and abs(frame.coordinates(candidate.position)[1] - target_depth) <= 5.0
    )
    height_offsets = tuple(
        (candidate.position - target_sample.position).dot(bitangent)
        for candidate in local_samples
    )
    normal_offsets = tuple(
        (candidate.position - target_sample.position).dot(surface_normal)
        for candidate in local_samples
    )
    upper_height = max(height_offsets)
    horizontal_cut_height = (tooth_position - target_sample.position).dot(bitangent)
    open_edge_height = upper_height + OBSERVATION_EDGE_MARGIN_MM
    opening_height = open_edge_height - horizontal_cut_height
    center_height = (horizontal_cut_height + open_edge_height) * 0.5
    lower_normal, upper_normal = min(normal_offsets), max(normal_offsets)
    cutter_depth = upper_normal - lower_normal + 2.0 * OBSERVATION_OUTSIDE_MARGIN_MM
    center_normal = 0.5 * (lower_normal + upper_normal)
    center = (
        target_sample.position
        + bitangent * center_height
        + surface_normal * center_normal
    )
    return WindowCutout(
        name=f"observation_window_{name}",
        purpose=WindowPurpose.OBSERVATION,
        center=center,
        normal=surface_normal,
        tangent=tangent,
        width_mm=width_mm,
        height_mm=opening_height,
        depth_mm=cutter_depth,
        corner_radius_mm=0.35,
    )


def _plan_observation_windows(case: CaseAnalysis) -> tuple[WindowCutout, ...]:
    """在前牙中线附近规划刚好露出牙面的观察缺口。"""

    anterior_depth = max(
        case.template_frame.coordinates(sample.position)[1]
        for sample in case.template_samples
    )
    anterior_sample = _nearest_surface_sample(case, 0.0, anterior_depth)
    tooth_sample = min(
        case.dentition_samples,
        key=lambda sample: sample.position.distance_to(anterior_sample.position),
    )
    tooth_lateral, tooth_depth, _ = case.template_frame.coordinates(tooth_sample.position)

    notch = _observation_notch(
        case,
        "anterior",
        tooth_lateral,
        tooth_depth,
        OBSERVATION_WIDTH_MM,
        tooth_sample.position,
    )
    return (notch,)


def plan_window_cutouts(
    case: CaseAnalysis,
    sleeves: SleeveGenerationResult,
) -> CutoutPlan:
    """生成导孔、操作窗和观察缺口计划。

    参数:
        case: 包含牙科导板、导套和窗口配置的病例分析。
        sleeves: 第 1 步输出的导套结果。

    返回:
        不含 Blender 对象的导孔与窗口几何计划。

    异常:
        ValueError: 病例分析与第 1 步导套结果不一致。

    算法说明:
        依次生成两个导孔、一个操作窗和一个观察缺口。
    """

    if case.guide_sleeves != sleeves.sleeves:
        raise ValueError("病例分析与导套生成结果不一致")

    operation_window = _plan_operation_window(case)
    return CutoutPlan(
        channels=_plan_channels(case),
        windows=(operation_window, *_plan_observation_windows(case)),
    )

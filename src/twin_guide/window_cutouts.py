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

OBSERVATION_FORWARD_FRACTION = 0.05


def _plan_channels(case: CaseAnalysis) -> tuple[CylinderCutout, CylinderCutout]:
    """沿两个导套轴线构造带轴向余量的牙科导板通道。"""

    axial_margin_mm = case.config.geometry.channel_axial_margin_mm
    template_channel_radius_mm = case.config.geometry.template_channel_radius_mm
    channels = tuple(
        CylinderCutout(
            name=f"guide_{guide.guide_index}_channel",
            start=guide.center + guide.axis * (guide.axial_min_mm - axial_margin_mm),
            end=guide.center + guide.axis * (guide.axial_max_mm + axial_margin_mm),
            radius_mm=template_channel_radius_mm,
        )
        for guide in case.guide_sleeves
    )
    return channels[0], channels[1]


def _plan_operation_window(case: CaseAnalysis) -> WindowCutout:
    """根据两导套间距、操作特征尺寸和局部牙科导板厚度规划操作窗。"""

    first_guide, second_guide = case.guide_sleeves
    center = case.operation_feature.center
    second_axis = (
        second_guide.axis
        if first_guide.axis.dot(second_guide.axis) >= 0
        else -second_guide.axis
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
        and abs((sample.position - center).dot(short_direction))
        <= short_edge_mm * 0.5 + 2.0
    )
    depth_coordinates = tuple(
        (sample.position - center).dot(normal)
        for sample in (local_samples or case.template_samples)
    )
    local_depth_mm = max(depth_coordinates) - min(depth_coordinates)
    depth_mm = max(
        local_depth_mm,
        first_guide.length_mm,
        second_guide.length_mm,
    ) + 2.0 * case.config.geometry.channel_axial_margin_mm
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


def _center_observation_window(
    case: CaseAnalysis,
    sample: SurfaceSample,
    vertical: Vec3,
) -> tuple[Vec3, float]:
    """根据样本附近的竖直高度范围对观察窗居中并计算深度。"""

    local_samples = tuple(
        candidate
        for candidate in case.template_samples
        if (
            candidate.position
            - sample.position
            - vertical * (candidate.position - sample.position).dot(vertical)
        ).length
        <= 4.0
    )
    heights = tuple(
        (candidate.position - sample.position).dot(vertical)
        for candidate in (local_samples or case.template_samples)
    )
    lower_height, upper_height = min(heights), max(heights)
    center = sample.position + vertical * ((lower_height + upper_height) * 0.5)
    return center, upper_height - lower_height + 2.0


def _plan_observation_windows(
    case: CaseAnalysis, operation_window: WindowCutout
) -> tuple[WindowCutout, ...]:
    """在牙科导板左右外侧规划观察窗，并跳过与操作窗过近的候选。"""

    coordinates = tuple(
        case.template_frame.coordinates(sample.position) for sample in case.template_samples
    )
    lateral_values = tuple(value[0] for value in coordinates)
    depth_values = tuple(value[1] for value in coordinates)
    lateral_min, lateral_max = min(lateral_values), max(lateral_values)
    depth_midpoint = (min(depth_values) + max(depth_values)) * 0.5
    depth_span = max(depth_values) - min(depth_values)
    forward_shift_mm = depth_span * OBSERVATION_FORWARD_FRACTION
    lateral_span = lateral_max - lateral_min
    targets = (
        ("left", lateral_min + lateral_span * 0.10),
        ("right", lateral_max - lateral_span * 0.10),
    )
    windows = []
    vertical = operation_window.normal.normalized()
    for name, lateral_mm in targets:
        baseline_sample = _nearest_surface_sample(case, lateral_mm, depth_midpoint)
        baseline_center, _ = _center_observation_window(case, baseline_sample, vertical)
        observation_depth = (
            case.template_frame.coordinates(baseline_sample.position)[1] + forward_shift_mm
        )
        shifted_sample = _nearest_surface_sample(case, lateral_mm, observation_depth)
        _, depth_mm = _center_observation_window(case, shifted_sample, vertical)
        center = baseline_center + case.template_frame.depth * forward_shift_mm
        if center.distance_to(operation_window.center) <= operation_window.width_mm * 0.6:
            continue
        tangent = orthonormal_tangent(vertical, case.template_frame.lateral)
        windows.append(
            WindowCutout(
                name=f"observation_window_{name}",
                purpose=WindowPurpose.OBSERVATION,
                center=center,
                normal=vertical,
                tangent=tangent,
                width_mm=3.0,
                height_mm=3.0,
                depth_mm=depth_mm,
                corner_radius_mm=0.45,
            )
        )
    return tuple(windows)


def plan_window_cutouts(
    case: CaseAnalysis,
    sleeves: SleeveGenerationResult,
) -> CutoutPlan:
    """生成导孔、操作窗和互不重叠的观察窗计划。

    参数:
        case: 包含牙科导板、导套和窗口配置的病例分析。
        sleeves: 第 1 步输出的导套结果。

    返回:
        不含 Blender 对象的导孔与窗口几何计划。

    异常:
        ValueError: 病例分析与第 1 步导套结果不一致。

    算法说明:
        函数先检查两项输入中的导套顺序与参数完全一致，然后依次生成
        两个导孔、一个操作窗和可行的观察窗。计算过程不修改输入网格。
    """

    if case.guide_sleeves != sleeves.sleeves:
        raise ValueError("病例分析与导套生成结果不一致")

    operation_window = _plan_operation_window(case)
    return CutoutPlan(
        channels=_plan_channels(case),
        windows=(operation_window, *_plan_observation_windows(case, operation_window)),
    )

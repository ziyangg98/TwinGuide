"""规划牙科导板上的导孔、操作窗和观察窗。"""

from __future__ import annotations

from twin_guide.models import (
    CaseAnalysis,
    CutoutPlan,
    CylinderCutout,
    GuideSleeve,
    OperationFeature,
    WindowCutout,
    WindowPurpose,
)
from twin_guide.observation_window_opening import build_observation_window_opening
from twin_guide.tooth_identification import ToothIdentificationResult
from twin_guide.types import SleeveGenerationResult


def _plan_channels(case: CaseAnalysis) -> tuple[CylinderCutout, ...]:
    """沿全部导管轴线构造带轴向余量的牙科导板通道。"""

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
    return channels


def _plan_operation_window(
    case: CaseAnalysis,
    guides: tuple[GuideSleeve, GuideSleeve],
    operation_feature: OperationFeature,
    site_index: int,
) -> WindowCutout:
    """根据两导管间距、操作特征尺寸和局部牙科导板厚度规划操作窗。"""

    first_guide, second_guide = guides
    center = operation_feature.center
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
    short_edge_mm = operation_feature.diameter_mm + 2.0 * (
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
    if not local_samples:
        raise ValueError(
            f"种植位 {site_index} 的操作窗范围内没有导板表面采样点"
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
        + 2.0 * case.config.windows.operation_axial_margin_mm
    )
    return WindowCutout(
        name=f"operation_window_{site_index:02d}",
        purpose=WindowPurpose.OPERATION,
        center=center,
        normal=normal,
        tangent=tangent,
        width_mm=long_edge_mm,
        height_mm=short_edge_mm,
        depth_mm=depth_mm,
        corner_radius_mm=case.config.windows.operation_corner_radius_mm,
    )


def plan_window_cutouts(
    case: CaseAnalysis,
    sleeves: SleeveGenerationResult,
    tooth_identification: ToothIdentificationResult | None = None,
) -> CutoutPlan:
    """生成导孔、操作窗和观察缺口计划。

    参数:
        case: 包含牙科导板、导管和窗口配置的病例分析。
        sleeves: 第 1 步输出的导管结果。
        tooth_identification: 可选的第 2 步现场牙位与观察窗映射结果。

    返回:
        不含 Blender 对象的导孔与窗口几何计划。

    异常:
        ValueError: 病例分析与第 1 步导管结果不一致。

    算法说明:
        按种植位生成导孔和操作窗。已配置观察窗时，必须使用
        第 2 步的 FDI 牙位映射生成轴扫掠组合 cutter；缺少所需
        牙位映射时明确报错。
    """

    if case.guide_sleeves != sleeves.sleeves:
        raise ValueError("病例分析与导管生成结果不一致")

    operation_windows = tuple(
        _plan_operation_window(case, pair, feature, index)
        for index, (pair, feature) in enumerate(
            zip(case.guide_sleeve_pairs, case.operation_features, strict=True),
            1,
        )
    )
    profile_windows = (
        ()
        if tooth_identification is None
        else (build_observation_window_opening(case.config, tooth_identification),)
    )
    return CutoutPlan(
        channels=_plan_channels(case),
        windows=operation_windows,
        profile_windows=profile_windows,
    )

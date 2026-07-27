"""观察窗策略分派以及 TwinGuideMerge 表面缺口算法。"""

from __future__ import annotations

from twin_guide.config import ObservationWindowMode
from twin_guide.geometry import Vec3, orthonormal_tangent
from twin_guide.models import (
    CaseAnalysis,
    ProfileWindowCutout,
    SurfaceSample,
    WindowCutout,
    WindowPurpose,
)
from twin_guide.observation_window_opening import build_observation_window_opening
from twin_guide.tooth_identification import ToothIdentificationResult

SURFACE_NOTCH_WIDTH_MM = 7.0
SURFACE_NOTCH_OUTSIDE_MARGIN_MM = 0.8
SURFACE_NOTCH_EDGE_MARGIN_MM = 2.0
SURFACE_NOTCH_CORNER_RADIUS_MM = 0.35


def _nearest_surface_sample(
    case: CaseAnalysis,
    lateral_mm: float,
    depth_mm: float,
) -> SurfaceSample:
    """返回局部横向—深度平面中距离目标最近的导板表面样本。"""

    return min(
        case.template_samples,
        key=lambda sample: (
            (case.template_frame.coordinates(sample.position)[0] - lateral_mm) ** 2
            + (case.template_frame.coordinates(sample.position)[1] - depth_mm) ** 2,
            sample.polygon_index,
        ),
    )


def _surface_notch(
    case: CaseAnalysis,
    name: str,
    lateral_mm: float,
    depth_mm: float,
    tooth_position: Vec3,
) -> WindowCutout:
    """按 TwinGuideMerge 规则从导板下缘构造固定宽度开放缺口。"""

    frame = case.template_frame
    target_sample = _nearest_surface_sample(case, lateral_mm, depth_mm)
    target_lateral, target_depth, _ = frame.coordinates(target_sample.position)
    surface_normal = target_sample.normal.normalized()
    if surface_normal.dot(frame.depth) < 0.0:
        surface_normal = -surface_normal
    tangent = orthonormal_tangent(surface_normal, frame.lateral)
    bitangent = surface_normal.cross(tangent).normalized()
    if bitangent.dot(frame.normal) < 0.0:
        tangent = -tangent
        bitangent = -bitangent

    local_samples = tuple(
        candidate
        for candidate in case.template_samples
        if abs(frame.coordinates(candidate.position)[0] - target_lateral)
        <= SURFACE_NOTCH_WIDTH_MM * 0.5 + 1.0
        and abs(frame.coordinates(candidate.position)[1] - target_depth) <= 5.0
    )
    if not local_samples:
        raise ValueError("表面缺口附近没有可用的导板表面样本")
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
    open_edge_height = upper_height + SURFACE_NOTCH_EDGE_MARGIN_MM
    opening_height = open_edge_height - horizontal_cut_height
    if opening_height <= 0.0:
        raise ValueError("表面缺口牙面止线位于导板开放边缘之外")
    center_height = (horizontal_cut_height + open_edge_height) * 0.5
    lower_normal, upper_normal = min(normal_offsets), max(normal_offsets)
    cutter_depth = (
        upper_normal - lower_normal + 2.0 * SURFACE_NOTCH_OUTSIDE_MARGIN_MM
    )
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
        width_mm=SURFACE_NOTCH_WIDTH_MM,
        height_mm=opening_height,
        depth_mm=cutter_depth,
        corner_radius_mm=SURFACE_NOTCH_CORNER_RADIUS_MM,
    )


def _plan_surface_notch_windows(case: CaseAnalysis) -> tuple[WindowCutout, ...]:
    """在牙弓前方最近牙面生成 TwinGuideMerge 单一观察缺口。"""

    anterior_depth = max(
        case.template_frame.coordinates(sample.position)[1]
        for sample in case.template_samples
    )
    anterior_sample = _nearest_surface_sample(case, 0.0, anterior_depth)
    tooth_sample = min(
        case.dentition_samples,
        key=lambda sample: sample.position.distance_to(anterior_sample.position),
    )
    tooth_lateral, tooth_depth, _ = case.template_frame.coordinates(
        tooth_sample.position
    )
    return (
        _surface_notch(
            case,
            "anterior",
            tooth_lateral,
            tooth_depth,
            tooth_sample.position,
        ),
    )


def plan_observation_windows(
    case: CaseAnalysis,
    tooth_identification: ToothIdentificationResult | None,
) -> tuple[tuple[WindowCutout, ...], tuple[ProfileWindowCutout, ...]]:
    """按病例配置返回解析型窗口和网格型窗口，保持两类 cutter 分离。"""

    mode = case.config.algorithms.observation_window
    if mode is ObservationWindowMode.SURFACE_NOTCH:
        return _plan_surface_notch_windows(case), ()
    if tooth_identification is None:
        return (), ()
    return (), (build_observation_window_opening(case.config, tooth_identification),)

"""第 1 步：从装配体识别两个导管并重建标准实体。"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from twin_guide.config import Jaw, SleeveParameters
from twin_guide.errors import GeometryError
from twin_guide.geometry import (
    Vec3,
    mean_point,
    point_axis_coordinates,
    principal_axis,
    principal_plane_normal,
    project_to_plane,
    quantile,
)
from twin_guide.models import GuideSleeve, SurfaceSample, TemplateFrame
from twin_guide.sleeve_estimation import c_opening_toward, estimate_sleeve_axis
from twin_guide.sleeve_estimation.types import SleeveAxis, SleeveEstimate
from twin_guide.types import SleeveGenerationResult

if TYPE_CHECKING:
    import bpy

BORE_PROBE_FRACTIONS = (0.15, 0.25, 0.35, 0.50, 0.65, 0.75, 0.85)
MINIMUM_CLEAR_BORE_PROBES = 5
CANDIDATE_CACHE_VERSION = 1


@dataclass(frozen=True, slots=True)
class SleeveGenerationInputs:
    """导管识别、定向和排序所需的最小输入集。"""

    components: tuple[bpy.types.Object, ...]
    template_samples: tuple[SurfaceSample, ...]
    template_center: Vec3
    sleeve_parameters: SleeveParameters
    jaw: Jaw
    occlusal_axis: Vec3 | None = None
    candidate_cache_path: Path | None = None
    candidate_cache_key: str | None = None


@dataclass(frozen=True, slots=True)
class _GuideCandidate:
    """从导管装配体分离出的导管候选分量。"""

    component_index: int
    guide_mesh: bpy.types.Object
    center: Vec3
    axis: Vec3
    axial_min_mm: float
    axial_max_mm: float
    outer_radius_mm: float
    fitted_pose: SleeveAxis
    fitted_axial_min_mm: float
    fitted_axial_max_mm: float
    clear_bore_probe_count: int

    @property
    def length_mm(self) -> float:
        """返回候选分量的轴向长度，单位毫米。"""

        return self.axial_max_mm - self.axial_min_mm

    @property
    def has_axial_bore(self) -> bool:
        """返回候选分量是否具有连续轴向导孔。"""

        return self.clear_bore_probe_count >= MINIMUM_CLEAR_BORE_PROBES


def _candidate_values(candidate: _GuideCandidate) -> dict[str, object]:
    """把耗时的导柱候选分析转换为可复用数值。"""

    return {
        "component_index": candidate.component_index,
        "center": candidate.center.as_tuple(),
        "axis": candidate.axis.as_tuple(),
        "axial_min_mm": candidate.axial_min_mm,
        "axial_max_mm": candidate.axial_max_mm,
        "outer_radius_mm": candidate.outer_radius_mm,
        "fitted_axis_origin": candidate.fitted_pose.axis_origin.as_tuple(),
        "fitted_axis": candidate.fitted_pose.axis.as_tuple(),
        "fitted_axial_min_mm": candidate.fitted_axial_min_mm,
        "fitted_axial_max_mm": candidate.fitted_axial_max_mm,
        "clear_bore_probe_count": candidate.clear_bore_probe_count,
    }


def _cached_candidates(
    inputs: SleeveGenerationInputs,
) -> tuple[_GuideCandidate, ...] | None:
    """读取与当前导柱装配体匹配的候选分析。"""

    path = inputs.candidate_cache_path
    key = inputs.candidate_cache_key
    if path is None or key is None or not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        if document.get("version") != CANDIDATE_CACHE_VERSION:
            return None
        if document.get("key") != key:
            return None
        candidates = []
        for value in document["candidates"]:
            index = int(value["component_index"])
            candidates.append(
                _GuideCandidate(
                    component_index=index,
                    guide_mesh=inputs.components[index],
                    center=Vec3(*map(float, value["center"])),
                    axis=Vec3(*map(float, value["axis"])),
                    axial_min_mm=float(value["axial_min_mm"]),
                    axial_max_mm=float(value["axial_max_mm"]),
                    outer_radius_mm=float(value["outer_radius_mm"]),
                    fitted_pose=SleeveAxis(
                        Vec3(*map(float, value["fitted_axis_origin"])),
                        Vec3(*map(float, value["fitted_axis"])),
                    ),
                    fitted_axial_min_mm=float(value["fitted_axial_min_mm"]),
                    fitted_axial_max_mm=float(value["fitted_axial_max_mm"]),
                    clear_bore_probe_count=int(value["clear_bore_probe_count"]),
                )
            )
        return tuple(candidates)
    except (IndexError, KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _write_candidate_cache(
    inputs: SleeveGenerationInputs,
    candidates: tuple[_GuideCandidate, ...],
) -> None:
    """保存只依赖输入装配体的导柱候选分析。"""

    path = inputs.candidate_cache_path
    key = inputs.candidate_cache_key
    if path is None or key is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": CANDIDATE_CACHE_VERSION,
                "key": key,
                "candidates": [_candidate_values(item) for item in candidates],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _orient_axis_against_occlusal(
    pose: SleeveAxis,
    vertices: tuple[Vec3, ...],
    occlusal_outward: Vec3,
) -> SleeveAxis:
    """按病例牙合外向统一轴向，并把原点放在外向一端。"""

    axis = pose.axis if pose.axis.dot(occlusal_outward) <= 0.0 else -pose.axis
    coordinates = tuple((point - pose.axis_origin).dot(axis) for point in vertices)
    return SleeveAxis(pose.axis_origin + axis * min(coordinates), axis)


def _analyze_component(
    mesh: bpy.types.Object,
    component_index: int,
    occlusal_outward: Vec3,
) -> _GuideCandidate:
    """将连通分量转为导管候选。

    参数:
        mesh: 连通分量网格。
        component_index: 分量索引。

    返回:
        包含 PCA 包络、精确轴线、轴向范围和导孔检查的候选。
    """

    from twin_guide.blender.mesh_queries import (
        build_bvh,
        point_inside_mesh,
        sample_mesh_surface,
    )
    from twin_guide.blender.sleeve_estimation_adapter import mesh_object_to_triangle_data

    samples = sample_mesh_surface(mesh)
    if len(samples) < 20:
        raise GeometryError(f"导管候选分量 {component_index} 的表面积过小")
    points = tuple(sample.position for sample in samples)
    center = mean_point(points)
    axis = principal_axis(points)
    coordinates = tuple(point_axis_coordinates(point, center, axis) for point in points)
    source = mesh_object_to_triangle_data(mesh)
    try:
        fitted_axis = _orient_axis_against_occlusal(
            estimate_sleeve_axis(source),
            source.vertices,
            occlusal_outward,
        )
    except ValueError as error:
        raise GeometryError(
            f"导管候选分量 {component_index} 无法估计轴向孔道"
        ) from error
    fitted_coordinates = tuple(
        (point - fitted_axis.axis_origin).dot(fitted_axis.axis)
        for point in source.vertices
    )
    fitted_minimum = min(fitted_coordinates)
    fitted_maximum = max(fitted_coordinates)
    fitted_span = fitted_maximum - fitted_minimum
    if fitted_span <= 1e-6:
        raise GeometryError(f"导管候选分量 {component_index} 的轴向范围退化")
    mesh_tree = build_bvh(mesh)
    bore_probe_points = tuple(
        fitted_axis.axis_origin
        + fitted_axis.axis
        * (fitted_minimum + fitted_span * fraction)
        for fraction in BORE_PROBE_FRACTIONS
    )
    clear_bore_probe_count = sum(
        not point_inside_mesh(mesh_tree, point) for point in bore_probe_points
    )
    return _GuideCandidate(
        component_index,
        mesh,
        center,
        axis,
        min(axial for _, axial in coordinates),
        max(axial for _, axial in coordinates),
        quantile([radial for radial, _ in coordinates], 0.90),
        fitted_axis,
        fitted_minimum,
        fitted_maximum,
        clear_bore_probe_count,
    )


def _filter_bore_candidates(
    candidates: tuple[_GuideCandidate, ...],
) -> tuple[_GuideCandidate, ...]:
    """仅保留七个轴向探测点中至少五个位于导孔的分量。"""

    return tuple(candidate for candidate in candidates if candidate.has_axial_bore)


def _pair_key(
    pair: tuple[_GuideCandidate, _GuideCandidate],
    configured: SleeveParameters,
) -> tuple[float, float, float, int, int]:
    """按与已知导管尺寸的差异和轴线平行度排序。

    参数:
        pair: 两个导管候选。

    返回:
        配置尺寸差、轴线差、分量间距和两个分量索引。
    """

    first, second = pair
    return (
        abs(first.length_mm - configured.height_mm) / configured.height_mm
        + abs(second.length_mm - configured.height_mm) / configured.height_mm
        + abs(first.outer_radius_mm - configured.outer_radius_mm)
        / configured.outer_radius_mm
        + abs(second.outer_radius_mm - configured.outer_radius_mm)
        / configured.outer_radius_mm,
        1.0 - abs(first.axis.dot(second.axis)),
        -first.center.distance_to(second.center),
        first.component_index,
        second.component_index,
    )


def _select_pair(
    candidates: tuple[_GuideCandidate, ...],
    configured: SleeveParameters,
    rejected_components: tuple[str, ...] = (),
) -> tuple[_GuideCandidate, _GuideCandidate]:
    """选择最接近已知尺寸且轴线最平行的导管对。

    参数:
        candidates: 全部连通分量候选。

    返回:
        最优的两个导管候选。

    异常:
        GeometryError: 可用连通分量少于两个。
    """

    bore_candidates = _filter_bore_candidates(candidates)
    pairs = tuple(
        (first, second)
        for index, first in enumerate(bore_candidates)
        for second in bore_candidates[index + 1 :]
    )
    if not pairs:
        diagnostic = ", ".join(
            f"{candidate.component_index}:"
            f"{candidate.clear_bore_probe_count}/{len(BORE_PROBE_FRACTIONS)}"
            for candidate in candidates
        )
        rejected = "; ".join(rejected_components)
        raise GeometryError(
            "导管装配体中至少需要两个具有轴向孔道的连通分量；"
            f"候选导孔探测 [{diagnostic}]"
            + (f"；拒绝原因 [{rejected}]" if rejected else "")
        )
    return min(pairs, key=lambda pair: _pair_key(pair, configured))


def _template_frame(
    inputs: SleeveGenerationInputs,
    first: _GuideCandidate,
    second: _GuideCandidate,
) -> TemplateFrame:
    """构造牙科导板局部标架。

    参数:
        inputs: 第 1 步输入几何。
        first: 第一个已定向候选。
        second: 第二个已定向候选。

    返回:
        包含横向、深度和法向的正交局部标架。
    """

    normal = principal_plane_normal([sample.position for sample in inputs.template_samples])
    occlusal_outward = inputs.occlusal_axis or Vec3(
        0.0, 0.0, inputs.jaw.occlusal_axis_sign
    )
    if normal.dot(occlusal_outward) < 0:
        normal = -normal
    midpoint = (first.center + second.center) / 2.0
    depth = project_to_plane(inputs.template_center - midpoint, normal)
    if depth.length < 1e-6:
        raise GeometryError("无法根据导管位置确定牙科导板深度方向")
    depth = depth.normalized()
    return TemplateFrame(inputs.template_center, depth.cross(normal).normalized(), depth, normal)


def _build_sleeve(
    candidate: _GuideCandidate,
    other: _GuideCandidate,
    index: int,
    configured: SleeveParameters,
) -> GuideSleeve:
    """复用已估计位姿并装入配置尺寸。

    参数:
        candidate: 已定向导管候选。
        other: 另一个导管候选。
        index: 输出导管编号。
        configured: 病例配置中的导管几何参数。

    返回:
        包含 STL 位姿和配置尺寸的导管。
    """

    pose = candidate.fitted_pose
    c_opening_direction = c_opening_toward(pose.axis, candidate.center, other.center)
    parameters = SleeveEstimate(
        axis_origin=pose.axis_origin,
        axis=pose.axis,
        c_opening_direction=c_opening_direction,
        height=configured.height_mm,
        platform_height=configured.platform_height_mm,
        closed_bore_height=configured.closed_bore_height_mm,
        inner_radius=configured.inner_radius_mm,
        outer_radius=configured.outer_radius_mm,
        inner_arc_angle=math.radians(configured.inner_arc_angle_degrees),
        outer_arc_angle=math.radians(configured.outer_arc_angle_degrees),
        top_recess_radius=configured.top_recess_radius_mm,
        top_recess_depth=configured.top_recess_depth_mm,
        platform_slot_width=configured.platform_slot_width_mm,
    )
    return GuideSleeve(
        guide_index=index,
        guide_mesh=candidate.guide_mesh,
        parameters=parameters,
        axial_min_mm=0.0,
        axial_max_mm=configured.height_mm,
    )


def recognize_and_build_sleeves(inputs: SleeveGenerationInputs) -> SleeveGenerationResult:
    """识别并重建两个导管。

    参数:
        inputs: 装配体连通分量、牙科导板表面样本和中心。

    返回:
        按牙科导板横向排序的两个导管及牙科导板局部坐标系。

    异常:
        GeometryError: 可用连通分量不足、牙科导板标架退化，
            或配置尺寸无法构成闭合导管。

    算法说明:
        算法对每个连通分量只分析一次，保存 PCA 包络、精确内孔轴线和
        七点导孔检查。通过导孔资格的分量按已知高度、外径、平行度和间距
        选对，然后直接复用已拟合位姿，将两个 C 口定向到对侧导管并装入标准尺寸。
        本函数不依赖牙位、切窗或连建结果。
    """

    cached = _cached_candidates(inputs)
    candidates = [] if cached is None else list(cached)
    rejected_components = []
    occlusal_outward = inputs.occlusal_axis or Vec3(
        0.0,
        0.0,
        inputs.jaw.occlusal_axis_sign,
    )
    if cached is None:
        for index, mesh in enumerate(inputs.components):
            try:
                candidates.append(_analyze_component(mesh, index, occlusal_outward))
            except GeometryError as error:
                rejected_components.append(f"{index}:{error}")
        _write_candidate_cache(inputs, tuple(candidates))
    first_raw, second_raw = _select_pair(
        tuple(candidates),
        inputs.sleeve_parameters,
        tuple(rejected_components),
    )
    selected = (first_raw, second_raw)
    template_frame = _template_frame(inputs, selected[0], selected[1])
    ordered = sorted(
        selected,
        key=lambda candidate: (candidate.center - inputs.template_center).dot(
            template_frame.lateral
        ),
    )
    sleeves = (
        _build_sleeve(
            ordered[0],
            ordered[1],
            1,
            inputs.sleeve_parameters,
        ),
        _build_sleeve(
            ordered[1],
            ordered[0],
            2,
            inputs.sleeve_parameters,
        ),
    )
    return SleeveGenerationResult(sleeves, template_frame)

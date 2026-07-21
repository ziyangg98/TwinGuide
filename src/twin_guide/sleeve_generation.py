"""第 1 步：识别两个导套并重建清洁闭合实体。"""

from __future__ import annotations

import math
from dataclasses import dataclass

import bpy

from twin_guide.blender.mesh_queries import sample_mesh_surface
from twin_guide.blender.sleeve_estimation_adapter import mesh_object_to_triangle_data
from twin_guide.blender.sleeve_reconstruction import (
    validate_sleeve_boolean_parameters,
)
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
from twin_guide.sleeve_estimation.types import SleeveEstimate
from twin_guide.types import SleeveGenerationResult


@dataclass(frozen=True, slots=True)
class SleeveGenerationInputs:
    """导套识别、定向和排序所需的最小输入集。"""

    components: tuple[bpy.types.Object, ...]
    template_samples: tuple[SurfaceSample, ...]
    template_center: Vec3
    sleeve_parameters: SleeveParameters
    jaw: Jaw


@dataclass(frozen=True, slots=True)
class _GuideCandidate:
    """从导套装配体分离出的导套候选分量。"""

    component_index: int
    guide_mesh: bpy.types.Object
    samples: tuple[SurfaceSample, ...]
    center: Vec3
    axis: Vec3
    axial_min_mm: float
    axial_max_mm: float
    outer_radius_mm: float

    @property
    def length_mm(self) -> float:
        """返回候选分量的轴向长度，单位毫米。"""

        return self.axial_max_mm - self.axial_min_mm


def _analyze_component(mesh: bpy.types.Object, component_index: int) -> _GuideCandidate:
    """将连通分量转为导套候选。

    参数:
        mesh: 连通分量网格。
        component_index: 分量索引。

    返回:
        包含表面样本、主轴、轴向范围和外径估计的候选。
    """

    samples = sample_mesh_surface(mesh)
    if len(samples) < 20:
        raise GeometryError(f"导套候选分量 {component_index} 的表面积过小")
    points = tuple(sample.position for sample in samples)
    center = mean_point(points)
    axis = principal_axis(points)
    coordinates = tuple(point_axis_coordinates(point, center, axis) for point in points)
    return _GuideCandidate(
        component_index,
        mesh,
        samples,
        center,
        axis,
        min(axial for _, axial in coordinates),
        max(axial for _, axial in coordinates),
        quantile([radial for radial, _ in coordinates], 0.90),
    )


def _pair_key(
    pair: tuple[_GuideCandidate, _GuideCandidate],
    configured: SleeveParameters,
) -> tuple[float, float, float, int, int]:
    """按与已知导柱尺寸的差异和轴线平行度排序。

    参数:
        pair: 两个导套候选。

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
) -> tuple[_GuideCandidate, _GuideCandidate]:
    """选择最接近已知尺寸且轴线最平行的导柱对。

    参数:
        candidates: 全部连通分量候选。

    返回:
        最优的两个导套候选。

    异常:
        GeometryError: 可用连通分量少于两个。
    """

    pairs = tuple(
        (first, second)
        for index, first in enumerate(candidates)
        for second in candidates[index + 1 :]
    )
    if not pairs:
        raise GeometryError("导柱装配体中至少需要两个可用连通分量")
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
    jaw_outward = Vec3(0.0, 0.0, inputs.jaw.occlusal_axis_sign)
    if normal.dot(jaw_outward) < 0:
        normal = -normal
    midpoint = (first.center + second.center) / 2.0
    depth = project_to_plane(inputs.template_center - midpoint, normal)
    if depth.length < 1e-6:
        raise GeometryError("无法根据导套位置确定牙科导板深度方向")
    depth = depth.normalized()
    return TemplateFrame(inputs.template_center, depth.cross(normal).normalized(), depth, normal)


def _build_sleeve(
    candidate: _GuideCandidate,
    other: _GuideCandidate,
    index: int,
    configured: SleeveParameters,
) -> GuideSleeve:
    """估计一个导柱的位姿并装入配置尺寸。

    参数:
        candidate: 已定向导套候选。
        other: 另一个导套候选。
        index: 输出导套编号。
        configured: 病例配置中的导柱几何参数。

    返回:
        包含 STL 位姿和配置尺寸的导柱。
    """

    source = mesh_object_to_triangle_data(candidate.guide_mesh)
    try:
        pose = estimate_sleeve_axis(source)
    except ValueError as error:
        raise GeometryError(f"无法估计导柱分量 {candidate.component_index}：{error}") from error
    c_opening_direction = c_opening_toward(pose.axis, candidate.center, other.center)
    parameters = SleeveEstimate(
        axis_origin=pose.axis_origin,
        axis=pose.axis,
        c_opening_direction=c_opening_direction,
        height=configured.height_mm,
        platform_height=configured.platform_height_mm,
        closed_bore_height=configured.closed_bore_height_mm,
        platform_width=configured.platform_width_mm,
        inner_radius=configured.inner_radius_mm,
        outer_radius=configured.outer_radius_mm,
        inner_arc_angle=math.radians(configured.inner_arc_angle_degrees),
        outer_arc_angle=math.radians(configured.outer_arc_angle_degrees),
    )
    validate_sleeve_boolean_parameters(parameters)
    return GuideSleeve(
        guide_index=index,
        guide_mesh=candidate.guide_mesh,
        parameters=parameters,
        axial_min_mm=0.0,
        axial_max_mm=parameters.height,
    )


def recognize_and_build_sleeves(inputs: SleeveGenerationInputs) -> SleeveGenerationResult:
    """识别并重建两个导套。

    参数:
        inputs: 装配体连通分量、牙科导板表面样本和中心。

    返回:
        按牙科导板横向排序的两个导套及牙科导板局部坐标系。

    异常:
        GeometryError: 可用连通分量不足、牙科导板标架退化，
            或配置尺寸无法构成闭合导柱。

    算法说明:
        算法先对每个连通分量做表面采样，然后枚举分量对，
        按已知导柱高度、外径和轴线平行程度排序。对选中的两个候选，
        从 STL 估计轴线，再将两个 C 口定向到对侧导柱并与配置尺寸组合。
        本函数不依赖牙位、切窗或连建结果。
    """

    candidates = []
    for index, mesh in enumerate(inputs.components):
        try:
            candidates.append(_analyze_component(mesh, index))
        except GeometryError:
            continue
    first_raw, second_raw = _select_pair(tuple(candidates), inputs.sleeve_parameters)
    selected = (first_raw, second_raw)
    template_frame = _template_frame(inputs, selected[0], selected[1])
    ordered = sorted(
        selected,
        key=lambda candidate: (candidate.center - inputs.template_center).dot(
            template_frame.lateral
        ),
    )
    sleeves = (
        _build_sleeve(ordered[0], ordered[1], 1, inputs.sleeve_parameters),
        _build_sleeve(ordered[1], ordered[0], 2, inputs.sleeve_parameters),
    )
    return SleeveGenerationResult(sleeves, template_frame)

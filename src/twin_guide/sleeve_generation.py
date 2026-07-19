"""第 1 步：识别两个导套并重建清洁闭合实体。"""

from __future__ import annotations

from dataclasses import dataclass, replace

import bpy
from mathutils.bvhtree import BVHTree

from twin_guide.blender.mesh_queries import ray_cast_mesh, sample_mesh_surface
from twin_guide.blender.sleeve_estimation_adapter import mesh_object_to_triangle_data
from twin_guide.blender.sleeve_reconstruction import (
    create_closed_sleeve_object,
    validate_sleeve_boolean_parameters,
)
from twin_guide.errors import GeometryError
from twin_guide.geometry import (
    Vec3,
    mean_point,
    point_axis_coordinates,
    principal_axis,
    principal_plane_normal,
    project_to_plane,
    quantile,
    relative_difference,
)
from twin_guide.models import GuideSleeve, SurfaceSample, TemplateFrame
from twin_guide.sleeve_estimation import estimate_sleeve_parameters, validate_reconstruction
from twin_guide.types import SleeveGenerationResult

MINIMUM_GUIDE_ALIGNMENT = 0.95
MAXIMUM_GUIDE_SIZE_DIFFERENCE = 0.25
MINIMUM_GUIDE_ASPECT_RATIO = 4.0


@dataclass(frozen=True, slots=True)
class SleeveGenerationInputs:
    """导套识别、定向和排序所需的最小输入集。"""

    components: tuple[bpy.types.Object, ...]
    template_bvh: BVHTree
    template_samples: tuple[SurfaceSample, ...]
    template_center: Vec3


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


def _pair_is_plausible(first: _GuideCandidate, second: _GuideCandidate) -> bool:
    """检查候选对是否符合双导套约束。

    参数:
        first: 第一个候选。
        second: 第二个候选。

    返回:
        轴线、尺寸、长径比和中心间距均达标时为 ``True``。
    """

    return (
        abs(first.axis.dot(second.axis)) >= MINIMUM_GUIDE_ALIGNMENT
        and relative_difference(first.length_mm, second.length_mm)
        <= MAXIMUM_GUIDE_SIZE_DIFFERENCE
        and relative_difference(first.outer_radius_mm, second.outer_radius_mm)
        <= MAXIMUM_GUIDE_SIZE_DIFFERENCE
        and min(
            first.length_mm / max(first.outer_radius_mm, 1e-9),
            second.length_mm / max(second.outer_radius_mm, 1e-9),
        )
        >= MINIMUM_GUIDE_ASPECT_RATIO
        and first.center.distance_to(second.center)
        > 2.0 * max(first.outer_radius_mm, second.outer_radius_mm)
    )


def _pair_key(
    pair: tuple[_GuideCandidate, _GuideCandidate],
) -> tuple[float, float, float, int, int]:
    """生成候选对的确定性排序键。

    参数:
        pair: 两个导套候选。

    返回:
        尺寸差、轴线差、负体量近似量和两个分量索引。
    """

    first, second = pair
    return (
        relative_difference(first.length_mm, second.length_mm)
        + relative_difference(first.outer_radius_mm, second.outer_radius_mm),
        1.0 - abs(first.axis.dot(second.axis)),
        -min(
            first.length_mm * first.outer_radius_mm**2,
            second.length_mm * second.outer_radius_mm**2,
        ),
        first.component_index,
        second.component_index,
    )


def _select_pair(
    candidates: tuple[_GuideCandidate, ...],
) -> tuple[_GuideCandidate, _GuideCandidate]:
    """选择排序键最小的合理导套对。

    参数:
        candidates: 全部连通分量候选。

    返回:
        最优的两个导套候选。

    异常:
        GeometryError: 没有候选对满足几何约束。
    """

    pairs = tuple(
        (first, second)
        for index, first in enumerate(candidates)
        for second in candidates[index + 1 :]
        if _pair_is_plausible(first, second)
    )
    if not pairs:
        raise GeometryError("没有导套候选对满足几何约束")
    return min(pairs, key=_pair_key)


def _template_intersection(inputs: SleeveGenerationInputs, candidate: _GuideCandidate) -> Vec3:
    """定位导套轴线与牙科导板的参考交点。

    参数:
        inputs: 第 1 步输入几何。
        candidate: 导套候选。

    返回:
        双向射线最近交点；无交点时为轴线加权最近牙科导板样本。
    """

    intersections = tuple(
        hit
        for direction in (candidate.axis, -candidate.axis)
        if (hit := ray_cast_mesh(inputs.template_bvh, candidate.center, direction)) is not None
    )
    if intersections:
        return min(intersections, key=lambda point: point.distance_to(candidate.center))
    return min(
        inputs.template_samples,
        key=lambda sample: (
            (lambda radial, axial: radial + max(
                0.0, axial - candidate.axial_max_mm, candidate.axial_min_mm - axial
            ) * 1.8)(*point_axis_coordinates(sample.position, candidate.center, candidate.axis)),
            sample.polygon_index,
        ),
    ).position


def _orient(candidate: _GuideCandidate, intersection: Vec3) -> _GuideCandidate:
    """统一导套候选的轴向。

    参数:
        candidate: 导套候选。
        intersection: 牙科导板参考交点。

    返回:
        正轴向指向远离牙科导板一侧的候选。
    """

    if (candidate.center - intersection).dot(candidate.axis) >= 0:
        return candidate
    return replace(
        candidate,
        axis=-candidate.axis,
        axial_min_mm=-candidate.axial_max_mm,
        axial_max_mm=-candidate.axial_min_mm,
    )


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

    second_axis = second.axis if first.axis.dot(second.axis) >= 0 else -second.axis
    average_axis = (first.axis + second_axis).normalized()
    normal = principal_plane_normal([sample.position for sample in inputs.template_samples])
    if normal.dot(average_axis) < 0:
        normal = -normal
    midpoint = (first.center + second.center) / 2.0
    depth = project_to_plane(inputs.template_center - midpoint, normal)
    if depth.length < 1e-6:
        raise GeometryError("无法根据导套位置确定牙科导板深度方向")
    depth = depth.normalized()
    return TemplateFrame(inputs.template_center, depth.cross(normal).normalized(), depth, normal)


def _build_sleeve(candidate: _GuideCandidate, intersection: Vec3, index: int) -> GuideSleeve:
    """估计、重建并验证一个导套。

    参数:
        candidate: 已定向导套候选。
        intersection: 牙科导板参考交点。
        index: 输出导套编号。

    返回:
        包含估计参数和重建验证指标的导套。
    """

    source = mesh_object_to_triangle_data(candidate.guide_mesh)
    try:
        parameters = estimate_sleeve_parameters(source, preferred_axis=candidate.axis)
    except ValueError as error:
        raise GeometryError(
            f"无法估计导套候选分量 {candidate.component_index}：{error}"
        ) from error
    soft_diagnostics = {"hp", "Wp", "phi_out"}
    blocking = tuple(
        item for item in parameters.diagnostics
        if not item.valid and item.parameter not in soft_diagnostics
    )
    if blocking:
        details = "; ".join(
            f"{item.parameter}: {item.message or '估计值无效'}" for item in blocking
        )
        raise GeometryError(
            f"导套候选分量 {candidate.component_index} 的参数无效：{details}"
        )
    validate_sleeve_boolean_parameters(parameters)
    reconstructed = create_closed_sleeve_object(
        parameters, f"sleeve_validation_{candidate.component_index}"
    )
    try:
        reconstructed_data = mesh_object_to_triangle_data(reconstructed)
    finally:
        bpy.data.objects.remove(reconstructed, do_unlink=True)
    validation = validate_reconstruction(
        source, parameters, maximum_samples=128, reconstructed=reconstructed_data
    )
    diagnostic_type = type(parameters.diagnostics[0])
    parameters = replace(
        parameters,
        diagnostics=(
            *parameters.diagnostics,
            diagnostic_type(
                "reconstruction",
                validation.symmetric_rms <= 0.20 * parameters.outer_radius,
                validation.sample_count,
                validation.symmetric_rms,
                validation.hausdorff_approximation,
                "双向表面误差比较",
            ),
        ),
    )
    return GuideSleeve(
        guide_index=index,
        guide_mesh=candidate.guide_mesh,
        parameters=parameters,
        axial_min_mm=0.0,
        axial_max_mm=parameters.height,
        template_intersection=intersection,
        reconstruction_validation=validation,
    )


def recognize_and_build_sleeves(inputs: SleeveGenerationInputs) -> SleeveGenerationResult:
    """识别并重建两个导套。

    参数:
        inputs: 装配体连通分量、牙科导板 BVH、表面样本和牙科导板中心。

    返回:
        按牙科导板横向排序的两个导套及牙科导板局部坐标系。

    异常:
        GeometryError: 连通分量样本不足、找不到合理导套对、牙科导板朝向退化，
            或参数估计和闭合重建验证失败。

    算法说明:
        算法先对每个连通分量做表面采样，通过主轴、轴向范围和径向分位数
        得到导套候选。然后枚举候选对，按轴线平行度、高度和半径相对差、
        长径比和中心间距筛选，再用尺寸差、轴线差和体量近似量排序。
        对选中的两个候选，通过牙科导板射线交点统一轴向，构造牙科导板局部坐标系，
        按横向坐标排序后分别做参数估计、闭合重建和误差验证。
        本函数不依赖牙位、切窗或连建结果。
    """

    candidates = tuple(_analyze_component(mesh, i) for i, mesh in enumerate(inputs.components))
    first_raw, second_raw = _select_pair(candidates)
    selected = tuple(
        (_orient(candidate, intersection), intersection)
        for candidate in (first_raw, second_raw)
        for intersection in (_template_intersection(inputs, candidate),)
    )
    template_frame = _template_frame(inputs, selected[0][0], selected[1][0])
    ordered = sorted(
        selected,
        key=lambda item: (item[0].center - inputs.template_center).dot(
            template_frame.lateral
        ),
    )
    sleeves = tuple(
        _build_sleeve(candidate, intersection, index)
        for index, (candidate, intersection) in enumerate(ordered, 1)
    )
    return SleeveGenerationResult((sleeves[0], sleeves[1]), template_frame)

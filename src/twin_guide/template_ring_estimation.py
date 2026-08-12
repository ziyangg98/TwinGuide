"""从参考牙科导板网格估计种植圆环的中心轴线。"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt
import trimesh

from twin_guide.errors import GeometryError, MeshIOError
from twin_guide.geometry import Vec3

FloatArray = npt.NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class TemplateRingEstimationConfig:
    """圆环截面搜索的尺度和质量阈值。"""

    slice_count: int = 40
    candidate_axis_count: int = 12
    normal_round_decimals: int = 3
    minimum_planar_face_count: int = 12
    minimum_radius_mm: float = 0.75
    maximum_radius_mm: float = 6.0
    maximum_relative_circle_rms: float = 0.01
    center_cluster_tolerance_mm: float = 0.15
    minimum_slice_support: int = 4
    minimum_top_plane_face_count: int = 50
    minimum_top_plane_area_mm2: float = 10.0

    def __post_init__(self) -> None:
        """拒绝无法形成有效搜索空间的配置。"""

        if self.slice_count < 5:
            raise ValueError("圆环估计至少需要五个截面")
        if self.candidate_axis_count < 1:
            raise ValueError("候选轴数量必须为正")
        if self.minimum_planar_face_count < 3:
            raise ValueError("平面法向至少需要三个三角面支持")
        if not 0.0 < self.minimum_radius_mm < self.maximum_radius_mm:
            raise ValueError("圆环半径范围无效")
        if self.maximum_relative_circle_rms <= 0.0:
            raise ValueError("圆拟合残差阈值必须为正")
        if self.center_cluster_tolerance_mm <= 0.0:
            raise ValueError("圆心聚类容差必须为正")
        if self.minimum_slice_support < 2:
            raise ValueError("圆环至少需要两个截面支持")
        if self.minimum_top_plane_face_count < 1:
            raise ValueError("圆环上平面支持面数必须为正")
        if self.minimum_top_plane_area_mm2 <= 0.0:
            raise ValueError("圆环上平面面积阈值必须为正")


@dataclass(frozen=True, slots=True)
class TemplateRingEstimate:
    """参考模板圆环的代表性中心、轴向和拟合质量。"""

    center: Vec3
    axis: Vec3
    radius_mm: float
    axial_span_mm: float
    circle_rms_mm: float
    axis_rms_mm: float
    supporting_slice_count: int
    supporting_circle_count: int


@dataclass(frozen=True, slots=True)
class TemplateRingTopPlaneEstimate:
    """圆环外侧上平面及其与中心轴线的交点。"""

    center: Vec3
    normal: Vec3
    offset_from_ring_center_mm: float
    supporting_face_count: int
    supporting_area_mm2: float


@dataclass(frozen=True, slots=True)
class _CircleObservation:
    """一个切片中通过质量筛选的圆拟合结果。"""

    center: FloatArray
    radius_mm: float
    rms_mm: float
    slice_index: int


def _canonical_direction(direction: FloatArray) -> FloatArray:
    """返回符号固定且归一化的三维方向。"""

    result = np.array(direction, dtype=float, copy=True)
    result /= np.linalg.norm(result)
    largest_index = int(np.argmax(np.abs(result)))
    return -result if result[largest_index] < 0.0 else result


def _candidate_axes(
    mesh: trimesh.Trimesh,
    config: TemplateRingEstimationConfig,
) -> tuple[FloatArray, ...]:
    """从重复平面法向中提取少量圆环轴候选。"""

    normals = np.asarray(mesh.face_normals, dtype=float)
    canonical = np.asarray([_canonical_direction(normal) for normal in normals])
    rounded = np.round(canonical, config.normal_round_decimals)
    keys = tuple(map(tuple, rounded))
    counts = Counter(keys)
    candidates: list[FloatArray] = []
    for key, count in counts.most_common(config.candidate_axis_count * 4):
        if count < config.minimum_planar_face_count:
            break
        matching = np.all(rounded == np.asarray(key), axis=1)
        axis = _canonical_direction(np.average(canonical[matching], axis=0))
        if any(abs(float(np.dot(axis, existing))) > 0.99999 for existing in candidates):
            continue
        candidates.append(axis)
        if len(candidates) >= config.candidate_axis_count:
            break
    return tuple(candidates)


def _plane_basis(axis: FloatArray) -> tuple[FloatArray, FloatArray]:
    """构造垂直于给定轴的确定性二维正交基。"""

    reference = np.eye(3)[int(np.argmin(np.abs(axis)))]
    tangent = reference - axis * float(np.dot(reference, axis))
    tangent /= np.linalg.norm(tangent)
    return tangent, np.cross(axis, tangent)


def _fit_circle(points: FloatArray) -> tuple[FloatArray, float, float] | None:
    """对二维点集执行代数圆拟合并返回径向残差。"""

    matrix = np.column_stack((points[:, 0], points[:, 1], np.ones(len(points))))
    values = -(points[:, 0] ** 2 + points[:, 1] ** 2)
    coefficients, *_ = np.linalg.lstsq(matrix, values, rcond=None)
    center = -0.5 * coefficients[:2]
    squared_radius = float(np.dot(center, center) - coefficients[2])
    if squared_radius <= 0.0:
        return None
    radius = math.sqrt(squared_radius)
    residuals = np.linalg.norm(points - center, axis=1) - radius
    rms = math.sqrt(float(np.mean(residuals * residuals)))
    return center, radius, rms


def _circle_observations(
    mesh: trimesh.Trimesh,
    axis: FloatArray,
    config: TemplateRingEstimationConfig,
) -> tuple[_CircleObservation, ...]:
    """沿候选轴切片并保留高精度闭合圆截面。"""

    origin = np.asarray(mesh.vertices, dtype=float).mean(axis=0)
    tangent, bitangent = _plane_basis(axis)
    axial_coordinates = (np.asarray(mesh.vertices, dtype=float) - origin) @ axis
    margin = max(float(np.ptp(axial_coordinates)) * 0.01, 1e-4)
    offsets = np.linspace(
        float(axial_coordinates.min()) + margin,
        float(axial_coordinates.max()) - margin,
        config.slice_count,
    )
    observations: list[_CircleObservation] = []
    for slice_index, offset in enumerate(offsets):
        section = mesh.section(plane_origin=origin + axis * offset, plane_normal=axis)
        if section is None:
            continue
        for raw_points in section.discrete:
            points = np.asarray(raw_points, dtype=float)
            if len(points) < 8:
                continue
            closure_tolerance = max(float(np.linalg.norm(mesh.extents)) * 1e-7, 1e-5)
            if np.linalg.norm(points[0] - points[-1]) > closure_tolerance:
                continue
            points = points[:-1]
            relative = points - (origin + axis * offset)
            coordinates = np.column_stack((relative @ tangent, relative @ bitangent))
            fit = _fit_circle(coordinates)
            if fit is None:
                continue
            center_2d, radius, rms = fit
            if not config.minimum_radius_mm <= radius <= config.maximum_radius_mm:
                continue
            if rms / radius > config.maximum_relative_circle_rms:
                continue
            center_3d = origin + axis * offset + tangent * center_2d[0] + bitangent * center_2d[1]
            observations.append(_CircleObservation(center_3d, radius, rms, slice_index))
    return tuple(observations)


def _center_clusters(
    observations: tuple[_CircleObservation, ...],
    axis: FloatArray,
    tolerance_mm: float,
) -> tuple[tuple[_CircleObservation, ...], ...]:
    """按横截面圆心位置聚合不同高度的圆观测。"""

    tangent, bitangent = _plane_basis(axis)
    centers = np.asarray(
        [[float(item.center @ tangent), float(item.center @ bitangent)] for item in observations]
    )
    unvisited = set(range(len(observations)))
    clusters: list[tuple[_CircleObservation, ...]] = []
    while unvisited:
        pending = [unvisited.pop()]
        indices = set(pending)
        while pending:
            current = pending.pop()
            neighbors = {
                index
                for index in unvisited
                if np.linalg.norm(centers[current] - centers[index]) <= tolerance_mm
            }
            unvisited.difference_update(neighbors)
            indices.update(neighbors)
            pending.extend(neighbors)
        clusters.append(tuple(observations[index] for index in sorted(indices)))
    return tuple(clusters)


def _estimate_from_cluster(
    cluster: tuple[_CircleObservation, ...],
    candidate_axis: FloatArray,
) -> TemplateRingEstimate:
    """用同一圆环的多层圆心细化其中心轴线。"""

    centers = np.asarray([observation.center for observation in cluster])
    center = centers.mean(axis=0)
    centered = centers - center
    covariance = centered.T @ centered / len(centers)
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    axis = eigenvectors[:, int(np.argmax(eigenvalues))]
    if float(np.dot(axis, candidate_axis)) < 0.0:
        axis = -axis
    axis = _canonical_direction(axis)
    axial = centered @ axis
    radial = centered - np.outer(axial, axis)
    axis_rms = math.sqrt(float(np.mean(np.sum(radial * radial, axis=1))))
    radii = np.asarray([observation.radius_mm for observation in cluster])
    residuals = np.asarray([observation.rms_mm for observation in cluster])
    return TemplateRingEstimate(
        center=Vec3(*map(float, center)),
        axis=Vec3(*map(float, axis)),
        radius_mm=float(np.median(radii)),
        axial_span_mm=float(np.ptp(axial)),
        circle_rms_mm=float(np.median(residuals)),
        axis_rms_mm=axis_rms,
        supporting_slice_count=len({observation.slice_index for observation in cluster}),
        supporting_circle_count=len(cluster),
    )


def _estimate_candidates(
    mesh: trimesh.Trimesh,
    settings: TemplateRingEstimationConfig,
) -> tuple[TemplateRingEstimate, ...]:
    """收集全部达到截面支持阈值的原始圆环候选。"""

    candidates: list[TemplateRingEstimate] = []
    for axis in _candidate_axes(mesh, settings):
        observations = _circle_observations(mesh, axis, settings)
        for cluster in _center_clusters(
            observations,
            axis,
            settings.center_cluster_tolerance_mm,
        ):
            slice_support = len({observation.slice_index for observation in cluster})
            if slice_support >= settings.minimum_slice_support:
                candidates.append(_estimate_from_cluster(cluster, axis))
    return tuple(candidates)


def _axis_line_distance(
    first: TemplateRingEstimate,
    second: TemplateRingEstimate,
) -> float:
    """返回两条近似平行圆环轴线的对称横向距离。"""

    first_center = np.asarray(first.center.as_tuple(), dtype=float)
    second_center = np.asarray(second.center.as_tuple(), dtype=float)
    first_axis = np.asarray(first.axis.as_tuple(), dtype=float)
    second_axis = np.asarray(second.axis.as_tuple(), dtype=float)
    delta = second_center - first_center
    first_distance = np.linalg.norm(delta - first_axis * float(np.dot(delta, first_axis)))
    second_distance = np.linalg.norm(delta - second_axis * float(np.dot(delta, second_axis)))
    return float(max(first_distance, second_distance))


def _same_ring(first: TemplateRingEstimate, second: TemplateRingEstimate) -> bool:
    """判断两个候选是否表示同一条物理圆环轴线。"""

    first_axis = np.asarray(first.axis.as_tuple(), dtype=float)
    second_axis = np.asarray(second.axis.as_tuple(), dtype=float)
    return (
        abs(float(np.dot(first_axis, second_axis))) >= math.cos(math.radians(1.0))
        and _axis_line_distance(first, second) <= 0.30
    )


def _estimate_quality_key(
    estimate: TemplateRingEstimate,
) -> tuple[int, int, float, float]:
    """返回用于选择同一圆环最佳候选的确定性质量键。"""

    return (
        estimate.supporting_slice_count,
        estimate.supporting_circle_count,
        -estimate.circle_rms_mm,
        -estimate.axis_rms_mm,
    )


def _deduplicate_estimates(
    candidates: tuple[TemplateRingEstimate, ...],
) -> tuple[TemplateRingEstimate, ...]:
    """合并由相近候选轴重复产生的同一圆环估计。"""

    remaining = set(range(len(candidates)))
    groups: list[list[TemplateRingEstimate]] = []
    while remaining:
        pending = [remaining.pop()]
        group_indices = set(pending)
        while pending:
            current = pending.pop()
            duplicates = {
                index for index in remaining if _same_ring(candidates[current], candidates[index])
            }
            remaining.difference_update(duplicates)
            group_indices.update(duplicates)
            pending.extend(duplicates)
        groups.append([candidates[index] for index in sorted(group_indices)])
    selected = [max(group, key=_estimate_quality_key) for group in groups]
    return tuple(sorted(selected, key=_estimate_quality_key, reverse=True))


def estimate_template_rings(
    mesh: trimesh.Trimesh,
    config: TemplateRingEstimationConfig | None = None,
) -> tuple[TemplateRingEstimate, ...]:
    """只使用参考模板网格，估计全部稳定圆环的中心轴线。"""

    settings = config or TemplateRingEstimationConfig()
    if len(mesh.vertices) < 3 or len(mesh.faces) < 1:
        raise GeometryError("参考模板网格为空")
    estimates = tuple(
        estimate
        for estimate in _deduplicate_estimates(_estimate_candidates(mesh, settings))
        if (
            (
                top_plane := estimate_template_ring_top_plane(mesh, estimate, settings)
            ).supporting_face_count
            >= settings.minimum_top_plane_face_count
            and top_plane.supporting_area_mm2 >= settings.minimum_top_plane_area_mm2
        )
    )
    if not estimates:
        raise GeometryError("参考模板中未找到具有稳定多截面支持的圆环")
    return estimates


def estimate_template_ring(
    mesh: trimesh.Trimesh,
    config: TemplateRingEstimationConfig | None = None,
) -> TemplateRingEstimate:
    """兼容旧调用，返回参考模板中支持最稳定的一个圆环。"""

    return estimate_template_rings(mesh, config)[0]


def estimate_template_ring_top_plane(
    mesh: trimesh.Trimesh,
    ring: TemplateRingEstimate | None = None,
    config: TemplateRingEstimationConfig | None = None,
) -> TemplateRingTopPlaneEstimate:
    """识别圆环外侧大平面，并返回中心轴线与该平面的交点。"""

    ring_estimate = ring or estimate_template_ring(mesh, config)
    center = np.asarray(ring_estimate.center.as_tuple(), dtype=float)
    axis = np.asarray(ring_estimate.axis.as_tuple(), dtype=float)
    face_centers = np.asarray(mesh.triangles_center, dtype=float)
    face_normals = np.asarray(mesh.face_normals, dtype=float)
    face_areas = np.asarray(mesh.area_faces, dtype=float)
    relative = face_centers - center
    offsets = relative @ axis
    radial_vectors = relative - np.outer(offsets, axis)
    radial_distances = np.linalg.norm(radial_vectors, axis=1)
    candidate_mask = (
        (np.abs(face_normals @ axis) >= 0.9999)
        & (radial_distances >= ring_estimate.radius_mm * 1.1)
        & (radial_distances <= ring_estimate.radius_mm * 2.5)
    )
    candidate_indices = np.flatnonzero(candidate_mask)
    if not len(candidate_indices):
        raise GeometryError("圆环附近未找到与中心轴垂直的外侧平面")
    groups: dict[float, list[int]] = {}
    for face_index in candidate_indices:
        key = round(float(offsets[face_index]), 2)
        groups.setdefault(key, []).append(int(face_index))
    plane_faces = max(
        groups.values(),
        key=lambda indices: (
            float(face_areas[indices].sum()),
            len(indices),
            float(offsets[indices].mean()),
        ),
    )
    plane_area = float(face_areas[plane_faces].sum())
    plane_offset = float(np.average(offsets[plane_faces], weights=face_areas[plane_faces]))
    plane_center = center + axis * plane_offset
    outward_normal = np.average(
        face_normals[plane_faces],
        axis=0,
        weights=face_areas[plane_faces],
    )
    outward_normal /= np.linalg.norm(outward_normal)
    return TemplateRingTopPlaneEstimate(
        center=Vec3(*map(float, plane_center)),
        normal=Vec3(*map(float, outward_normal)),
        offset_from_ring_center_mm=float(np.dot(plane_center - center, outward_normal)),
        supporting_face_count=len(plane_faces),
        supporting_area_mm2=plane_area,
    )


def estimate_template_ring_pair_direction(
    mesh: trimesh.Trimesh,
    ring: TemplateRingEstimate,
    inward_axis: Vec3,
    sign_reference: Vec3 | None = None,
) -> Vec3:
    """由圆环内侧局部模板截面估计双导连线方向。"""

    centers = np.asarray(mesh.triangles_center, dtype=float)
    center = np.asarray(ring.center.as_tuple(), dtype=float)
    axis = np.asarray(inward_axis.as_tuple(), dtype=float)
    relative = centers - center
    axial = relative @ axis
    planar = relative - np.outer(axial, axis)
    radial = np.linalg.norm(planar, axis=1)
    target_axial_mm = 0.5 * ring.axial_span_mm + 0.5
    selected = (
        (np.abs(axial - target_axial_mm) <= 0.3)
        & (radial >= ring.radius_mm + 0.7)
        & (radial <= ring.radius_mm + 5.7)
    )
    if int(selected.sum()) < 50:
        raise GeometryError("圆环内侧局部截面支持不足，无法确定导柱旋转方向")
    directions = planar[selected] / radial[selected, None]
    mean_direction = directions.mean(axis=0)
    if float(np.linalg.norm(mean_direction)) < 0.05:
        raise GeometryError("圆环内侧局部截面近似对称，无法确定导柱旋转方向")
    connection = Vec3(*map(float, mean_direction)).normalized()
    pair_direction = inward_axis.cross(connection).normalized()
    if sign_reference is not None and pair_direction.dot(sign_reference) < 0.0:
        pair_direction = -pair_direction
    elif sign_reference is None:
        coordinates = pair_direction.as_tuple()
        largest_index = max(range(3), key=lambda index: abs(coordinates[index]))
        if coordinates[largest_index] < 0.0:
            pair_direction = -pair_direction
    return pair_direction


def estimate_template_ring_from_stl(
    path: str | Path,
    config: TemplateRingEstimationConfig | None = None,
) -> TemplateRingEstimate:
    """读取 STL，并在不使用 sleeve 数据的情况下估计模板圆环。"""

    mesh_path = Path(path)
    if not mesh_path.is_file():
        raise MeshIOError(f"STL 文件不存在：{mesh_path}")
    try:
        loaded = trimesh.load_mesh(mesh_path, process=True)
    except Exception as error:
        raise MeshIOError(f"无法读取 STL {mesh_path}：{error}") from error
    if not isinstance(loaded, trimesh.Trimesh):
        raise MeshIOError(f"{mesh_path} 未解析为单个三角网格")
    return estimate_template_ring(loaded, config)


def estimate_template_rings_from_stl(
    path: str | Path,
    config: TemplateRingEstimationConfig | None = None,
) -> tuple[TemplateRingEstimate, ...]:
    """读取 STL，并在不使用 sleeve 数据的情况下估计全部模板圆环。"""

    mesh_path = Path(path)
    if not mesh_path.is_file():
        raise MeshIOError(f"STL 文件不存在：{mesh_path}")
    try:
        loaded = trimesh.load_mesh(mesh_path, process=True)
    except Exception as error:
        raise MeshIOError(f"无法读取 STL {mesh_path}：{error}") from error
    if not isinstance(loaded, trimesh.Trimesh):
        raise MeshIOError(f"{mesh_path} 未解析为单个三角网格")
    return estimate_template_rings(loaded, config)


def estimate_template_ring_top_plane_from_stl(
    path: str | Path,
    config: TemplateRingEstimationConfig | None = None,
) -> TemplateRingTopPlaneEstimate:
    """读取 STL，并估计圆环上平面中心。"""

    mesh_path = Path(path)
    if not mesh_path.is_file():
        raise MeshIOError(f"STL 文件不存在：{mesh_path}")
    try:
        loaded = trimesh.load_mesh(mesh_path, process=True)
    except Exception as error:
        raise MeshIOError(f"无法读取 STL {mesh_path}：{error}") from error
    if not isinstance(loaded, trimesh.Trimesh):
        raise MeshIOError(f"{mesh_path} 未解析为单个三角网格")
    ring = estimate_template_ring(loaded, config)
    return estimate_template_ring_top_plane(loaded, ring, config)


__all__ = [
    "TemplateRingEstimate",
    "TemplateRingEstimationConfig",
    "TemplateRingTopPlaneEstimate",
    "estimate_template_ring",
    "estimate_template_ring_from_stl",
    "estimate_template_ring_pair_direction",
    "estimate_template_ring_top_plane",
    "estimate_template_ring_top_plane_from_stl",
    "estimate_template_rings",
    "estimate_template_rings_from_stl",
]

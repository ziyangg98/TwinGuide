"""从输入导管 STL 估计有向轴线，并定义成对的 C 口方向。"""

from __future__ import annotations

from twin_guide.geometry import Vec3, mean_point, principal_axis, quantile

from .fitting import fit_axis, fit_circle
from .slicing import Section, section_offsets, slice_mesh
from .types import SleeveAxis, TriangleMeshData


def _axial_extent(mesh: TriangleMeshData, origin: Vec3, axis: Vec3) -> tuple[float, float]:
    """返回排除网格端点毛刺的轴向范围。"""

    coordinates = tuple((point - origin).dot(axis) for point in mesh.vertices)
    return quantile(coordinates, 0.005), quantile(coordinates, 0.995)


def _bore_points(section: Section, axis_point: Vec3) -> tuple[Vec3, ...]:
    """根据截面法向选出内孔圆弧点。"""

    result = []
    for sample in section.samples:
        radial = sample.point - axis_point
        radial -= section.plane_normal * radial.dot(section.plane_normal)
        if radial.length > 1e-10 and sample.normal.dot(radial.normalized()) < -0.25:
            result.append(sample.point)
    return tuple(result)


def _refined_axis(mesh: TriangleMeshData, center: Vec3, initial_axis: Vec3) -> tuple[Vec3, Vec3]:
    """用五个内孔截面圆心对 PCA 轴线做一次校正。"""

    minimum, maximum = _axial_extent(mesh, center, initial_axis)
    circle_centers = []
    for offset in section_offsets(minimum, maximum, 5, 0.15, 0.85):
        section = slice_mesh(mesh, center, initial_axis, offset)
        axis_point = center + initial_axis * offset
        points = _bore_points(section, axis_point)
        if len(points) < 3:
            continue
        try:
            circle_centers.append(fit_circle(points, axis_point, initial_axis).center)
        except ValueError:
            continue
    if len(circle_centers) < 2:
        return center, initial_axis
    line = fit_axis(tuple(circle_centers))
    direction = line.direction if line.direction.dot(initial_axis) >= 0.0 else -line.direction
    return line.origin, direction


def estimate_sleeve_axis(mesh: TriangleMeshData) -> SleeveAxis:
    """拟合导管的无符号轴线；病例层负责按上下颌统一其方向。"""

    center = mean_point(mesh.vertices)
    axis_center, axis = _refined_axis(mesh, center, principal_axis(mesh.vertices))
    minimum, _ = _axial_extent(mesh, axis_center, axis)
    return SleeveAxis(axis_center + axis * minimum, axis)


def c_opening_toward(axis: Vec3, center: Vec3, other_center: Vec3) -> Vec3:
    """返回轴线法平面内指向另一导管的 C 口方向。"""

    toward_other = other_center - center
    return (toward_other - axis * toward_other.dot(axis)).normalized()

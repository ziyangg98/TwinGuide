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


def _orient_toward_closed_end(
    mesh: TriangleMeshData, axis_origin: Vec3, axis: Vec3
) -> tuple[Vec3, Vec3]:
    """以带闭合内孔的一端作为轴向终点。"""

    minimum, maximum = _axial_extent(mesh, axis_origin, axis)
    span = maximum - minimum
    low = slice_mesh(mesh, axis_origin, axis, minimum + 0.10 * span)
    high = slice_mesh(mesh, axis_origin, axis, maximum - 0.10 * span)
    low_closed = sum(polyline.closed for polyline in low.polylines)
    high_closed = sum(polyline.closed for polyline in high.polylines)
    if high_closed > low_closed:
        return axis_origin + axis * minimum, axis
    return axis_origin + axis * maximum, -axis


def estimate_sleeve_axis(mesh: TriangleMeshData) -> SleeveAxis:
    """估计导管轴线并将正向指向平台端。"""

    center = mean_point(mesh.vertices)
    axis_center, axis = _refined_axis(mesh, center, principal_axis(mesh.vertices))
    axis_origin, axis = _orient_toward_closed_end(mesh, axis_center, axis)
    return SleeveAxis(axis_origin, axis)


def c_opening_toward(axis: Vec3, center: Vec3, other_center: Vec3) -> Vec3:
    """返回轴线法平面内指向另一导管的 C 口方向。"""

    toward_other = other_center - center
    return (toward_other - axis * toward_other.dot(axis)).normalized()

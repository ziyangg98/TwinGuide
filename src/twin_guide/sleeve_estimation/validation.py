"""计算输入网格与实际重建网格之间的表面误差。"""

from __future__ import annotations

import math
from collections import defaultdict
from statistics import median

from twin_guide.geometry import Vec3

from .types import ReconstructionValidation, SleeveEstimate, TriangleMeshData


def _closest_point_triangle(point: Vec3, first: Vec3, second: Vec3, third: Vec3) -> Vec3:
    """返回空间点在三角形上的最近点。"""

    edge_ab = second - first
    edge_ac = third - first
    offset = point - first
    d1, d2 = edge_ab.dot(offset), edge_ac.dot(offset)
    if d1 <= 0.0 and d2 <= 0.0:
        return first
    offset_b = point - second
    d3, d4 = edge_ab.dot(offset_b), edge_ac.dot(offset_b)
    if d3 >= 0.0 and d4 <= d3:
        return second
    vc = d1 * d4 - d3 * d2
    if vc <= 0.0 and d1 >= 0.0 and d3 <= 0.0:
        return first + edge_ab * (d1 / (d1 - d3))
    offset_c = point - third
    d5, d6 = edge_ab.dot(offset_c), edge_ac.dot(offset_c)
    if d6 >= 0.0 and d5 <= d6:
        return third
    vb = d5 * d2 - d1 * d6
    if vb <= 0.0 and d2 >= 0.0 and d6 <= 0.0:
        return first + edge_ac * (d2 / (d2 - d6))
    va = d3 * d6 - d5 * d4
    if va <= 0.0 and d4 - d3 >= 0.0 and d5 - d6 >= 0.0:
        return second + (third - second) * ((d4 - d3) / ((d4 - d3) + (d5 - d6)))
    denominator = 1.0 / (va + vb + vc)
    return first + edge_ab * (vb * denominator) + edge_ac * (vc * denominator)


def _surface_samples(mesh: TriangleMeshData, maximum: int) -> tuple[Vec3, ...]:
    """从顶点和面心生成确定性表面样本。"""

    points = list(mesh.vertices)
    points.extend(
        (mesh.vertices[first] + mesh.vertices[second] + mesh.vertices[third]) / 3.0
        for first, second, third in mesh.faces
    )
    if len(points) <= maximum:
        return tuple(points)
    step = len(points) / maximum
    return tuple(points[min(int(index * step), len(points) - 1)] for index in range(maximum))


def _distances(source: tuple[Vec3, ...], target: TriangleMeshData) -> tuple[float, ...]:
    """计算每个源样本到目标三角网格的最近距离。"""

    triangles = tuple(tuple(target.vertices[index] for index in face) for face in target.faces)
    return tuple(
        min(point.distance_to(_closest_point_triangle(point, *triangle)) for triangle in triangles)
        for point in source
    )


def _region(point: Vec3, estimate: SleeveEstimate) -> str:
    """按轴向位置和半径标记导柱表面区域。"""

    offset = point - estimate.axis_origin
    axial = offset.dot(estimate.axis)
    radial_vector = offset - estimate.axis * axial
    radial = radial_vector.length
    if min(abs(axial), abs(axial - estimate.height)) <= 0.04 * estimate.height:
        return "end_faces"
    if radial_vector.dot(estimate.c_opening_direction) > estimate.outer_radius * 1.02:
        return "platform"
    if abs(radial - estimate.inner_radius) <= abs(radial - estimate.outer_radius):
        return "inner_arc"
    return "outer_arc"


def validate_reconstruction(
    original: TriangleMeshData,
    estimate: SleeveEstimate,
    reconstructed: TriangleMeshData,
    maximum_samples: int = 2500,
) -> ReconstructionValidation:
    """比较输入网格与实际重建网格的双向表面误差。"""

    original_samples = _surface_samples(original, maximum_samples)
    reconstructed_samples = _surface_samples(reconstructed, maximum_samples)
    forward = _distances(original_samples, reconstructed)
    backward = _distances(reconstructed_samples, original)
    rms_forward = math.sqrt(sum(value * value for value in forward) / len(forward))
    rms_backward = math.sqrt(sum(value * value for value in backward) / len(backward))
    sum_squared = sum(value * value for value in forward) + sum(value * value for value in backward)
    combined = tuple(sorted((*forward, *backward)))
    grouped: dict[str, list[float]] = defaultdict(list)
    for point, distance in zip(reconstructed_samples, backward, strict=True):
        grouped[_region(point, estimate)].append(distance)
    return ReconstructionValidation(
        rms_forward,
        rms_backward,
        math.sqrt(sum_squared / (len(forward) + len(backward))),
        median(combined),
        combined[round(0.95 * (len(combined) - 1))],
        combined[-1],
        tuple(
            (name, math.sqrt(sum(value * value for value in values) / len(values)))
            for name, values in sorted(grouped.items())
        ),
        len(combined),
    )

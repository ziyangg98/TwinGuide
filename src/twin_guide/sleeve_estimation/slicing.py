"""不依赖 Blender 的三角网格与平面求交。"""

from __future__ import annotations

from dataclasses import dataclass

from twin_guide.geometry import Vec3, mean_point

from .types import TriangleMeshData


@dataclass(frozen=True, slots=True)
class SectionSample:
    """截面交点及其所属三角面法向。"""

    point: Vec3
    normal: Vec3


@dataclass(frozen=True, slots=True)
class SectionPolyline:
    """按拓扑顺序排列的截面折线分量。"""

    samples: tuple[SectionSample, ...]
    closed: bool


@dataclass(frozen=True, slots=True)
class Section:
    """一个定向平面与三角网格的截交结果。"""

    plane_origin: Vec3
    plane_normal: Vec3
    offset: float
    samples: tuple[SectionSample, ...]
    polylines: tuple[SectionPolyline, ...] = ()


def _unique(points: list[Vec3], tolerance: float) -> list[Vec3]:
    """按给定空间容差去除截面交点中的近重复点。"""

    result: list[Vec3] = []
    for point in points:
        if all(point.distance_to(existing) > tolerance for existing in result):
            result.append(point)
    return result


def slice_mesh(
    mesh: TriangleMeshData,
    origin: Vec3,
    normal: Vec3,
    offset: float,
    tolerance: float = 1e-7,
) -> Section:
    """计算三角网格与定向偏移平面的交线。

    每个被平面穿过的三角形保留一条交线段和对应面法向，
    供后续区分内壁与外壁。
    """

    unit_normal = normal.normalized()
    plane_point = origin + unit_normal * offset
    segments: list[tuple[SectionSample, SectionSample]] = []
    for first_index, second_index, third_index in mesh.faces:
        vertices = (
            mesh.vertices[first_index],
            mesh.vertices[second_index],
            mesh.vertices[third_index],
        )
        face_cross = (vertices[1] - vertices[0]).cross(vertices[2] - vertices[0])
        if face_cross.length <= tolerance:
            continue
        face_normal = face_cross.normalized()
        distances = tuple((vertex - plane_point).dot(unit_normal) for vertex in vertices)
        if all(value > tolerance for value in distances) or all(
            value < -tolerance for value in distances
        ):
            continue
        intersections: list[Vec3] = []
        for edge_index in range(3):
            start = vertices[edge_index]
            end = vertices[(edge_index + 1) % 3]
            start_distance = distances[edge_index]
            end_distance = distances[(edge_index + 1) % 3]
            if abs(start_distance) <= tolerance:
                intersections.append(start)
            if start_distance * end_distance < -(tolerance * tolerance):
                fraction = start_distance / (start_distance - end_distance)
                intersections.append(start + (end - start) * fraction)
        intersections = _unique(intersections, tolerance * 4.0)
        if len(intersections) >= 2:
            segments.append(
                (
                    SectionSample(intersections[0], face_normal),
                    SectionSample(intersections[1], face_normal),
                )
            )
    polylines = _order_segments(segments, max(tolerance * 12.0, 1e-8))
    samples = tuple(
        SectionSample(mean_point((first.point, second.point)), first.normal)
        for first, second in segments
    )
    return Section(origin, unit_normal, offset, samples, polylines)


def _order_segments(
    segments: list[tuple[SectionSample, SectionSample]], tolerance: float
) -> tuple[SectionPolyline, ...]:
    """将无向交线段连接为开放或闭合折线。"""

    unused = list(segments)
    result: list[SectionPolyline] = []
    while unused:
        first, second = unused.pop()
        chain = [first, second]
        changed = True
        while changed and unused:
            changed = False
            for index, (start, end) in enumerate(unused):
                if chain[-1].point.distance_to(start.point) <= tolerance:
                    chain.append(end)
                elif chain[-1].point.distance_to(end.point) <= tolerance:
                    chain.append(start)
                elif chain[0].point.distance_to(end.point) <= tolerance:
                    chain.insert(0, start)
                elif chain[0].point.distance_to(start.point) <= tolerance:
                    chain.insert(0, end)
                else:
                    continue
                unused.pop(index)
                changed = True
                break
        closed = len(chain) > 2 and chain[0].point.distance_to(chain[-1].point) <= tolerance
        if closed:
            chain.pop()
        result.append(SectionPolyline(tuple(chain), closed))
    return tuple(sorted(result, key=lambda item: len(item.samples), reverse=True))


def section_offsets(
    minimum: float,
    maximum: float,
    count: int,
    low_fraction: float,
    high_fraction: float,
) -> tuple[float, ...]:
    """在轴向范围内返回等间距内部偏移量。"""

    span = maximum - minimum
    low = minimum + low_fraction * span
    high = minimum + high_fraction * span
    return tuple(low + (high - low) * index / (count - 1) for index in range(count))

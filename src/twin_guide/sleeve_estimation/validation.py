"""根据估计参数重建导管，并计算表面误差。"""

from __future__ import annotations

import math
from collections import defaultdict
from statistics import median

from twin_guide.geometry import Vec3

from .types import ReconstructionValidation, SleeveEstimate, TriangleMeshData


def _append_quad(
    faces: list[tuple[int, int, int]],
    first: int,
    second: int,
    third: int,
    fourth: int,
) -> None:
    """向重建网格追加一个四边形的四个顶点和两个三角面。"""

    faces.extend(((first, second, third), (first, third, fourth)))


def _weld_mesh(
    vertices: list[Vec3],
    faces: list[tuple[int, int, int]],
    tolerance: float = 1e-8,
) -> TriangleMeshData:
    """合并重合构造顶点，形成单一 STL 外壳。"""

    welded: list[Vec3] = []
    lookup: dict[tuple[int, int, int], int] = {}
    remap: list[int] = []
    for vertex in vertices:
        key = tuple(round(value / tolerance) for value in vertex.as_tuple())
        index = lookup.get(key)
        if index is None:
            index = len(welded)
            lookup[key] = index
            welded.append(vertex)
        remap.append(index)

    welded_faces: list[tuple[int, int, int]] = []
    seen: set[tuple[int, int, int]] = set()
    for face in faces:
        candidate = tuple(remap[index] for index in face)
        if len(set(candidate)) < 3:
            continue
        unoriented = tuple(sorted(candidate))
        if unoriented in seen:
            continue
        seen.add(unoriented)
        welded_faces.append(candidate)
    return TriangleMeshData(tuple(welded), tuple(welded_faces))


def reconstruct_sleeve(estimate: SleeveEstimate, angular_segments: int = 72) -> TriangleMeshData:
    """根据核心参数重建导管的三个轴向区域。

    上段为 C 形环状截面；中段由 D 形外轮廓、内圆弧和平行槽壁组成；
    下段保留 D 形外轮廓，并在完整圆形固定孔周围封闭侧槽。
    """

    if angular_segments < 8:
        raise ValueError("角向分段数不得小于 8")
    axis = estimate.axis.normalized()
    platform = estimate.c_opening_direction - axis * estimate.c_opening_direction.dot(axis)
    platform = platform.normalized()
    across = axis.cross(platform).normalized()
    inner_gap = 2.0 * math.pi - estimate.inner_arc_angle
    outer_gap = 2.0 * math.pi - estimate.outer_arc_angle
    inner_start = 0.5 * inner_gap
    inner_end = 2.0 * math.pi - 0.5 * inner_gap
    outer_start = 0.5 * outer_gap
    outer_end = 2.0 * math.pi - 0.5 * outer_gap
    inner_cut = estimate.inner_radius * math.cos(inner_start)
    outer_cut = estimate.outer_radius * math.cos(outer_start)
    common_cut = 0.5 * (inner_cut + outer_cut)
    platform_edge = common_cut + estimate.platform_width
    half_width = estimate.outer_radius
    z_top = 0.0
    z_platform = estimate.height - estimate.platform_height
    z_transition = estimate.height - estimate.closed_bore_height
    z_bottom = estimate.height
    if not z_top < z_platform < z_transition < z_bottom:
        raise ValueError("导管高度必须满足 0 < H-hp < H-hs < H")
    vertices: list[Vec3] = []
    faces: list[tuple[int, int, int]] = []

    def point(x_value: float, y_value: float, axial: float) -> Vec3:
        """将导管局部坐标转换为世界坐标。"""

        return estimate.axis_origin + platform * x_value + across * y_value + axis * axial

    def arc(radius: float, start: float, end: float, count: int) -> tuple[tuple[float, float], ...]:
        """按等角度间隔采样局部平面圆弧。"""

        while end <= start:
            end += 2.0 * math.pi
        return tuple(
            (
                radius * math.cos(start + (end - start) * index / count),
                radius * math.sin(start + (end - start) * index / count),
            )
            for index in range(count + 1)
        )

    def wall(
        polyline: tuple[tuple[float, float], ...],
        low: float,
        high: float,
        reverse: bool = False,
    ) -> None:
        """在两个轴向高度之间封闭指定轮廓侧壁。"""

        low_indices = []
        high_indices = []
        for x_value, y_value in polyline:
            low_indices.append(len(vertices))
            vertices.append(point(x_value, y_value, low))
            high_indices.append(len(vertices))
            vertices.append(point(x_value, y_value, high))
        for index in range(len(polyline) - 1):
            if polyline[index] == polyline[index + 1]:
                continue
            quad = (
                low_indices[index],
                low_indices[index + 1],
                high_indices[index + 1],
                high_indices[index],
            )
            _append_quad(faces, *reversed(quad) if reverse else quad)

    upper_outer = arc(estimate.outer_radius, outer_start, outer_end, angular_segments)
    upper_inner = arc(estimate.inner_radius, inner_start, inner_end, angular_segments)
    wall(upper_outer, z_top, z_platform)
    wall(upper_inner, z_top, z_platform, reverse=True)
    wall((upper_outer[0], upper_inner[0]), z_top, z_platform, reverse=True)
    wall((upper_inner[-1], upper_outer[-1]), z_top, z_platform, reverse=True)

    slot_y_low = estimate.inner_radius * math.sin(inner_end)
    slot_y_high = estimate.inner_radius * math.sin(inner_start)
    if slot_y_low > slot_y_high:
        slot_y_low, slot_y_high = slot_y_high, slot_y_low
    d_boundary = (
        (platform_edge, -half_width),
        (platform_edge, slot_y_low),
        (platform_edge, slot_y_high),
        (platform_edge, half_width),
        (0.0, half_width),
        *arc(half_width, 0.5 * math.pi, 1.5 * math.pi, angular_segments // 2)[1:],
        (platform_edge, -half_width),
    )
    # 中段和下段共用 D 形外轮廓；中段仅在矩形孔槽穿出平台处缺少外边。
    wall(d_boundary[:2], z_platform, z_transition)
    wall(d_boundary[2:], z_platform, z_transition)
    middle_inner = arc(estimate.inner_radius, inner_start, inner_end, angular_segments)
    wall(middle_inner, z_platform, z_transition, reverse=True)
    wall((middle_inner[0], (platform_edge, slot_y_high)), z_platform, z_transition, reverse=True)
    wall(((platform_edge, slot_y_low), middle_inner[-1]), z_platform, z_transition, reverse=True)

    # 下段外轮廓和圆形内孔均为闭合轮廓。
    wall(d_boundary, z_transition, z_bottom)
    full_inner = arc(estimate.inner_radius, 0.0, 2.0 * math.pi, angular_segments)
    wall(full_inner, z_transition, z_bottom, reverse=True)

    # 端面在成对极坐标样本间构造三角形环带。顶部为 C 形环，
    # 下部 D 形轮廓相对内孔为星形域。
    def cap_strip(
        inner: tuple[tuple[float, float], ...],
        outer: tuple[tuple[float, float], ...],
        axial: float,
        reverse: bool,
    ) -> None:
        """在内外轮廓之间构造三角化环带端面。"""

        inner_indices = []
        outer_indices = []
        for inner_point, outer_point in zip(inner, outer, strict=True):
            inner_indices.append(len(vertices))
            vertices.append(point(*inner_point, axial))
            outer_indices.append(len(vertices))
            vertices.append(point(*outer_point, axial))
        for index in range(len(inner) - 1):
            quad = (
                inner_indices[index],
                outer_indices[index],
                outer_indices[index + 1],
                inner_indices[index + 1],
            )
            if reverse:
                quad = tuple(reversed(quad))
            candidates = ((quad[0], quad[1], quad[2]), (quad[0], quad[2], quad[3]))
            for candidate in candidates:
                first, second, third = (vertices[value] for value in candidate)
                if (second - first).cross(third - first).length > 1e-12:
                    faces.append(candidate)

    cap_strip(upper_inner, upper_outer, z_top, reverse=True)

    def d_radius(angle: float) -> float:
        """返回 D 形外轮廓在指定极角方向上的半径。"""

        cosine, sine = math.cos(angle), math.sin(angle)
        if cosine < 0.0:
            return half_width
        x_hit = platform_edge / max(cosine, 1e-12)
        y_hit = half_width / max(abs(sine), 1e-12)
        return min(x_hit, y_hit)

    # 第一台阶只连接上部 C 形外轮廓与公共 D 形外轮廓。
    shoulder_angles = tuple(
        outer_start + (outer_end + 2.0 * math.pi - outer_start) * index / angular_segments
        for index in range(angular_segments + 1)
    )
    shoulder_d = tuple(
        (d_radius(value) * math.cos(value), d_radius(value) * math.sin(value))
        for value in shoulder_angles
    )
    cap_strip(upper_outer, shoulder_d, z_platform, reverse=False)

    # 第二过渡面只封闭圆形内孔之外的矩形槽。缺失内孔圆弧上的点
    # 与同一横向坐标的平台边配对，避免径向扇面穿过内孔并堵塞中心空腔。
    gap_angles = tuple(
        inner_end + (inner_start - inner_end) * index / angular_segments
        for index in range(angular_segments + 1)
    )
    gap_inner = tuple(
        (estimate.inner_radius * math.cos(value), estimate.inner_radius * math.sin(value))
        for value in gap_angles
    )
    gap_outer = tuple((platform_edge, value[1]) for value in gap_inner)
    cap_strip(gap_inner, gap_outer, z_transition, reverse=False)

    angles = tuple(
        2.0 * math.pi * index / angular_segments for index in range(angular_segments + 1)
    )
    lower_inner = tuple(
        (estimate.inner_radius * math.cos(value), estimate.inner_radius * math.sin(value))
        for value in angles
    )
    lower_outer = tuple(
        (d_radius(value) * math.cos(value), d_radius(value) * math.sin(value)) for value in angles
    )
    cap_strip(lower_inner, lower_outer, z_bottom, reverse=False)
    return _weld_mesh(vertices, faces)


def _closest_point_triangle(point: Vec3, first: Vec3, second: Vec3, third: Vec3) -> Vec3:
    """返回空间点在指定三角形上的最近点。"""

    # 使用 Ericson 区域判定，并仅通过 Vec3 运算实现。
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
    """按三角面面积分层从网格生成有确定性的表面样本。"""

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
    """计算源样本到目标三角网格的最近距离。"""

    triangles = tuple(tuple(target.vertices[index] for index in face) for face in target.faces)
    return tuple(
        min(point.distance_to(_closest_point_triangle(point, *triangle)) for triangle in triangles)
        for point in source
    )


def _region(point: Vec3, estimate: SleeveEstimate) -> str:
    """按轴向高度和径向位置将重建点归入导管几何区域。"""

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
    maximum_samples: int = 2500,
    *,
    reconstructed: TriangleMeshData | None = None,
) -> ReconstructionValidation:
    """比较输入网格与参数化重建网格的表面误差。

    调用方可通过 ``reconstructed`` 传入下游实体构造器的求值网格；
    未传入时使用无副作用的纯 Python 多边形重建。
    """

    if reconstructed is None:
        reconstructed = reconstruct_sleeve(estimate)
    original_samples = _surface_samples(original, maximum_samples)
    reconstructed_samples = _surface_samples(reconstructed, maximum_samples)
    forward = _distances(original_samples, reconstructed)
    backward = _distances(reconstructed_samples, original)
    rms_forward = math.sqrt(sum(value * value for value in forward) / len(forward))
    rms_backward = math.sqrt(sum(value * value for value in backward) / len(backward))
    sum_squared = sum(value * value for value in forward) + sum(value * value for value in backward)
    symmetric = math.sqrt(sum_squared / (len(forward) + len(backward)))
    combined = tuple(sorted((*forward, *backward)))
    percentile_95 = combined[round(0.95 * (len(combined) - 1))]
    grouped: dict[str, list[float]] = defaultdict(list)
    for point, distance in zip(reconstructed_samples, backward, strict=True):
        grouped[_region(point, estimate)].append(distance)
    region_rms = tuple(
        (name, math.sqrt(sum(value * value for value in values) / len(values)))
        for name, values in sorted(grouped.items())
    )
    return ReconstructionValidation(
        rms_forward,
        rms_backward,
        symmetric,
        median(combined),
        percentile_95,
        combined[-1],
        region_rms,
        len(combined),
    )

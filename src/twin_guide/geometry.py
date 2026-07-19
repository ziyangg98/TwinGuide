"""纯 Python 三维几何与线性代数算法。"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from twin_guide.errors import GeometryError

Matrix3 = tuple[
    tuple[float, float, float],
    tuple[float, float, float],
    tuple[float, float, float],
]


@dataclass(frozen=True, slots=True)
class Vec3:
    """不可变三维向量。"""

    x: float
    y: float
    z: float

    def __add__(self, other: Vec3) -> Vec3:
        """返回两个向量的和。"""

        return Vec3(self.x + other.x, self.y + other.y, self.z + other.z)

    def __sub__(self, other: Vec3) -> Vec3:
        """返回两个向量的差。"""

        return Vec3(self.x - other.x, self.y - other.y, self.z - other.z)

    def __neg__(self) -> Vec3:
        """返回反向向量。"""

        return Vec3(-self.x, -self.y, -self.z)

    def __mul__(self, scalar: float) -> Vec3:
        """返回向量与标量的乘积。"""

        return Vec3(self.x * scalar, self.y * scalar, self.z * scalar)

    __rmul__ = __mul__

    def __truediv__(self, scalar: float) -> Vec3:
        """返回向量除以标量的结果。"""

        return Vec3(self.x / scalar, self.y / scalar, self.z / scalar)

    def dot(self, other: Vec3) -> float:
        """返回与另一向量的点积。"""

        return self.x * other.x + self.y * other.y + self.z * other.z

    def cross(self, other: Vec3) -> Vec3:
        """返回与另一向量的叉积。"""

        return Vec3(
            self.y * other.z - self.z * other.y,
            self.z * other.x - self.x * other.z,
            self.x * other.y - self.y * other.x,
        )

    @property
    def length(self) -> float:
        """返回欧氏范数。"""

        return math.sqrt(self.dot(self))

    def normalized(self) -> Vec3:
        """返回单位向量，并拒绝零向量。"""

        if self.length < 1e-12:
            raise GeometryError("零向量无法归一化")
        return self / self.length

    def distance_to(self, other: Vec3) -> float:
        """返回到另一点的欧氏距离。"""

        return (self - other).length

    def as_tuple(self) -> tuple[float, float, float]:
        """以元组形式返回坐标。"""

        return self.x, self.y, self.z


def mean_point(points: Sequence[Vec3]) -> Vec3:
    """返回非空点序列的算术平均点。"""

    if not points:
        raise GeometryError("至少需要一个点")
    total = Vec3(0.0, 0.0, 0.0)
    for point in points:
        total += point
    return total / len(points)


def covariance_matrix(points: Sequence[Vec3], center: Vec3 | None = None) -> Matrix3:
    """返回点序列的总体协方差矩阵。"""

    if not points:
        raise GeometryError("协方差计算至少需要一个点")
    origin = mean_point(points) if center is None else center
    values = [[0.0, 0.0, 0.0] for _ in range(3)]
    for point in points:
        coordinates = (point - origin).as_tuple()
        for row in range(3):
            for column in range(3):
                values[row][column] += coordinates[row] * coordinates[column]
    scale = 1.0 / len(points)
    return (
        tuple(values[0][column] * scale for column in range(3)),
        tuple(values[1][column] * scale for column in range(3)),
        tuple(values[2][column] * scale for column in range(3)),
    )


def symmetric_eigenvectors(matrix: Matrix3) -> tuple[tuple[float, Vec3], ...]:
    """使用稳定的 Jacobi 旋转对称三阶矩阵进行特征分解。"""

    values = [list(row) for row in matrix]
    vectors = [[1.0 if row == column else 0.0 for column in range(3)] for row in range(3)]
    for _ in range(48):
        row, column = max(((0, 1), (0, 2), (1, 2)), key=lambda pair: abs(values[pair[0]][pair[1]]))
        off_diagonal = values[row][column]
        if abs(off_diagonal) < 1e-13:
            break
        tau = (values[column][column] - values[row][row]) / (2.0 * off_diagonal)
        tangent = math.copysign(1.0, tau) / (abs(tau) + math.sqrt(1.0 + tau * tau))
        cosine = 1.0 / math.sqrt(1.0 + tangent * tangent)
        sine = tangent * cosine
        row_value = values[row][row]
        column_value = values[column][column]
        for index in range(3):
            if index in (row, column):
                continue
            index_row = values[index][row]
            index_column = values[index][column]
            values[index][row] = values[row][index] = cosine * index_row - sine * index_column
            values[index][column] = values[column][index] = sine * index_row + cosine * index_column
        values[row][row] = (
            cosine * cosine * row_value
            - 2.0 * sine * cosine * off_diagonal
            + sine * sine * column_value
        )
        values[column][column] = (
            sine * sine * row_value
            + 2.0 * sine * cosine * off_diagonal
            + cosine * cosine * column_value
        )
        values[row][column] = values[column][row] = 0.0
        for index in range(3):
            vector_row = vectors[index][row]
            vector_column = vectors[index][column]
            vectors[index][row] = cosine * vector_row - sine * vector_column
            vectors[index][column] = sine * vector_row + cosine * vector_column
    pairs = (
        (values[index][index], Vec3(*(vectors[row][index] for row in range(3))).normalized())
        for index in range(3)
    )
    return tuple(sorted(pairs, key=lambda pair: pair[0], reverse=True))


def principal_axis(points: Sequence[Vec3]) -> Vec3:
    """返回点云的唯一主方向。"""

    eigenpairs = symmetric_eigenvectors(covariance_matrix(points))
    largest, second = eigenpairs[0][0], eigenpairs[1][0]
    tolerance = max(abs(largest), 1.0) * 1e-8
    if largest <= tolerance or largest - second <= tolerance:
        raise GeometryError("点云不存在唯一主轴")
    return eigenpairs[0][1]


def principal_plane_normal(points: Sequence[Vec3]) -> Vec3:
    """返回近似平面点云中方差最小的唯一方向。"""

    eigenpairs = symmetric_eigenvectors(covariance_matrix(points))
    second, smallest = eigenpairs[1][0], eigenpairs[2][0]
    tolerance = max(abs(second), 1.0) * 1e-8
    if second <= tolerance or second - smallest <= tolerance:
        raise GeometryError("点云不存在唯一主平面")
    return eigenpairs[2][1]


def point_axis_coordinates(point: Vec3, axis_origin: Vec3, axis: Vec3) -> tuple[float, float]:
    """返回点相对指定轴线的径向距离和有符号轴向坐标。"""

    unit_axis = axis.normalized()
    axial = (point - axis_origin).dot(unit_axis)
    radial = point.distance_to(axis_origin + unit_axis * axial)
    return radial, axial


def project_to_plane(vector: Vec3, normal: Vec3) -> Vec3:
    """移除向量在平面法向上的分量。"""

    unit_normal = normal.normalized()
    return vector - unit_normal * vector.dot(unit_normal)


def orthonormal_tangent(normal: Vec3, preferred: Vec3) -> Vec3:
    """将首选方向投影到平面上并归一化。"""

    tangent = project_to_plane(preferred, normal)
    if tangent.length < 1e-10:
        candidates = (Vec3(1.0, 0.0, 0.0), Vec3(0.0, 1.0, 0.0), Vec3(0.0, 0.0, 1.0))
        tangent = max(candidates, key=lambda candidate: project_to_plane(candidate, normal).length)
        tangent = project_to_plane(tangent, normal)
    return tangent.normalized()


def quantile(values: Sequence[float], fraction: float) -> float:
    """使用最近秩方法返回非空序列的分位数。"""

    if not values:
        raise GeometryError("分位数计算至少需要一个数值")
    if not 0.0 <= fraction <= 1.0:
        raise ValueError("分位数分数必须位于 [0, 1] 内")
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * fraction)]


def relative_difference(first: float, second: float) -> float:
    """返回与尺度无关的绝对差异。"""

    return abs(first - second) / max(abs(first), abs(second), 1e-12)


def periodic_catmull_rom(
    control_points: tuple[Vec3, ...], maximum_step_mm: float
) -> tuple[Vec3, ...]:
    """按有界弦长步长采样周期向心 Catmull–Rom 曲线。"""

    if len(control_points) < 4:
        raise GeometryError("周期曲线至少需要四个控制点")
    if maximum_step_mm <= 0:
        raise ValueError("maximum_step_mm 必须为正数")
    samples: list[Vec3] = []
    point_count = len(control_points)

    def interpolate(
        first: Vec3,
        second: Vec3,
        start: float,
        end: float,
        value: float,
    ) -> Vec3:
        """在两个控制点之间按节点参数线性插值。"""

        if end - start < 1e-12:
            return first
        fraction = (value - start) / (end - start)
        return first * (1.0 - fraction) + second * fraction

    for segment in range(point_count):
        previous = control_points[(segment - 1) % point_count]
        start = control_points[segment]
        end = control_points[(segment + 1) % point_count]
        following = control_points[(segment + 2) % point_count]
        divisions = max(4, math.ceil(start.distance_to(end) / maximum_step_mm) * 2)
        previous_knot = 0.0
        start_knot = previous_knot + math.sqrt(max(previous.distance_to(start), 1e-12))
        end_knot = start_knot + math.sqrt(max(start.distance_to(end), 1e-12))
        following_knot = end_knot + math.sqrt(max(end.distance_to(following), 1e-12))
        for index in range(divisions):
            parameter = start_knot + (end_knot - start_knot) * index / divisions
            previous_start = interpolate(previous, start, previous_knot, start_knot, parameter)
            start_end = interpolate(start, end, start_knot, end_knot, parameter)
            end_following = interpolate(end, following, end_knot, following_knot, parameter)
            first_blend = interpolate(previous_start, start_end, previous_knot, end_knot, parameter)
            second_blend = interpolate(
                start_end, end_following, start_knot, following_knot, parameter
            )
            samples.append(interpolate(first_blend, second_blend, start_knot, end_knot, parameter))
    return tuple(samples)


def closed_curve_length(points: Sequence[Vec3]) -> float:
    """返回闭合离散曲线的长度。"""

    return (
        sum(
            points[index].distance_to(points[(index + 1) % len(points)])
            for index in range(len(points))
        )
        if len(points) >= 2
        else 0.0
    )


def surface_area_centroid(triangles: Sequence[tuple[Vec3, Vec3, Vec3]]) -> Vec3:
    """返回三角网格的面积加权重心。"""

    weighted = Vec3(0.0, 0.0, 0.0)
    total_area = 0.0
    for first, second, third in triangles:
        area = (second - first).cross(third - first).length * 0.5
        weighted += (first + second + third) * (area / 3.0)
        total_area += area
    if total_area < 1e-12:
        raise GeometryError("网格表面积为零")
    return weighted / total_area


def volume_centroid(triangles: Sequence[tuple[Vec3, Vec3, Vec3]]) -> Vec3:
    """返回封闭网格的体积重心；体积失效时使用面积重心。"""

    weighted = Vec3(0.0, 0.0, 0.0)
    signed_volume_six = 0.0
    for first, second, third in triangles:
        tetrahedron_volume_six = first.dot(second.cross(third))
        weighted += (first + second + third) * tetrahedron_volume_six
        signed_volume_six += tetrahedron_volume_six
    if abs(signed_volume_six) < 1e-9:
        return surface_area_centroid(triangles)
    return weighted / (4.0 * signed_volume_six)

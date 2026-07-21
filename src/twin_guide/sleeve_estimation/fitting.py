"""导套参数估计所需的稳健拟合函数。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import median

from twin_guide.geometry import Vec3, mean_point, orthonormal_tangent, principal_axis


@dataclass(frozen=True, slots=True)
class CircleFit:
    """圆拟合的圆心、半径和残差。"""

    center: Vec3
    radius: float
    rms_residual: float
    point_count: int
    plane_normal: Vec3


@dataclass(frozen=True, slots=True)
class LineFit:
    """直线拟合的原点、方向和残差。"""

    origin: Vec3
    direction: Vec3
    rms_residual: float
    point_count: int


def _solve_3x3(matrix: list[list[float]], values: list[float]) -> tuple[float, float, float]:
    """使用带主元的高斯消元求解三阶线性方程组。"""

    augmented = [[*row, value] for row, value in zip(matrix, values, strict=True)]
    for column in range(3):
        pivot = max(range(column, 3), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            raise ValueError("圆拟合的几何构型奇异")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        scale = augmented[column][column]
        augmented[column] = [value / scale for value in augmented[column]]
        for row in range(3):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [
                value - factor * pivot_value
                for value, pivot_value in zip(augmented[row], augmented[column], strict=True)
            ]
    return augmented[0][3], augmented[1][3], augmented[2][3]


def _algebraic_circle(
    points: tuple[Vec3, ...], plane_origin: Vec3, plane_normal: Vec3
) -> CircleFit:
    """在二维投影平面上用代数最小二乘初始化圆心和半径。"""

    tangent = orthonormal_tangent(plane_normal, Vec3(1.0, 0.0, 0.0))
    bitangent = plane_normal.normalized().cross(tangent).normalized()
    coordinates = (
        ((point - plane_origin).dot(tangent), (point - plane_origin).dot(bitangent))
        for point in points
    )
    xy = tuple(coordinates)
    matrix = [[0.0] * 3 for _ in range(3)]
    values = [0.0] * 3
    for x_value, y_value in xy:
        row = (x_value, y_value, 1.0)
        target = -(x_value * x_value + y_value * y_value)
        for row_index in range(3):
            values[row_index] += row[row_index] * target
            for column_index in range(3):
                matrix[row_index][column_index] += row[row_index] * row[column_index]
    coefficient_x, coefficient_y, constant = _solve_3x3(matrix, values)
    center_x = -0.5 * coefficient_x
    center_y = -0.5 * coefficient_y
    squared_radius = center_x * center_x + center_y * center_y - constant
    if squared_radius <= 0.0:
        raise ValueError("圆拟合得到非正半径")
    center = plane_origin + tangent * center_x + bitangent * center_y
    radius = math.sqrt(squared_radius)
    residuals = [abs(point.distance_to(center) - radius) for point in points]
    rms = math.sqrt(sum(value * value for value in residuals) / len(residuals))
    return CircleFit(center, radius, rms, len(points), plane_normal.normalized())


def fit_circle(
    points: tuple[Vec3, ...],
    plane_origin: Vec3,
    plane_normal: Vec3,
    trim_sigma: float = 3.5,
) -> CircleFit:
    """拟合圆，并根据径向残差执行一次离群点剔除。"""

    if len(points) < 3:
        raise ValueError("圆拟合至少需要三个点")
    fit = _algebraic_circle(points, plane_origin, plane_normal)
    residuals = tuple(abs(point.distance_to(fit.center) - fit.radius) for point in points)
    middle = median(residuals)
    mad = median(abs(value - middle) for value in residuals)
    scale = max(1.4826 * mad, fit.radius * 1e-5, 1e-9)
    retained = tuple(
        point
        for point, value in zip(points, residuals, strict=True)
        if value <= middle + trim_sigma * scale
    )
    return _algebraic_circle(retained, plane_origin, plane_normal) if len(retained) >= 3 else fit


def fit_axis(points: tuple[Vec3, ...]) -> LineFit:
    """对截面中心拟合最小二乘直线。"""

    if len(points) < 2:
        raise ValueError("轴线拟合至少需要两个截面中心")
    origin = mean_point(points)
    direction = principal_axis(points)
    residuals = tuple(
        ((point - origin) - direction * (point - origin).dot(direction)).length for point in points
    )
    rms = math.sqrt(sum(value * value for value in residuals) / len(residuals))
    return LineFit(origin, direction, rms, len(points))

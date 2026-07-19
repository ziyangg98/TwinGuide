"""窗口、通道和表面的净距计算函数。"""

from __future__ import annotations

import math

from twin_guide.geometry import Vec3
from twin_guide.models import WindowCutout

_DIRECTION_TOLERANCE = 1e-10


def window_distance(point: Vec3, window: WindowCutout) -> float:
    """计算点到有向窗口长方体的有符号距离。

    参数:
        point: 待检查点。
        window: 包含中心、法向、切向和三边尺寸的窗口。

    返回:
        点到有向长方体的有符号距离；外部为正，内部为负，边界为零。

    算法说明:
        函数先将点转换到窗口的切向、副切向和法向坐标，
        再使用轴对齐长方体的标准有符号距离计算。
    """

    normal = window.normal.normalized()
    tangent = window.tangent.normalized()
    bitangent = normal.cross(tangent).normalized()
    offset = point - window.center
    coordinates = offset.dot(tangent), offset.dot(bitangent), offset.dot(normal)
    half_extents = window.width_mm * 0.5, window.height_mm * 0.5, window.depth_mm * 0.5
    outside = tuple(
        max(abs(coordinate) - half_extent, 0.0)
        for coordinate, half_extent in zip(coordinates, half_extents, strict=True)
    )
    if any(outside):
        return math.sqrt(sum(value * value for value in outside))
    return -min(
        half_extent - abs(coordinate)
        for coordinate, half_extent in zip(coordinates, half_extents, strict=True)
    )


def channel_distance(point: Vec3, start: Vec3, end: Vec3, radius_mm: float) -> float:
    """计算点到有限圆柱通道的有符号径向距离。

    参数:
        point: 待检查点。
        start: 通道轴线起点。
        end: 通道轴线终点。
        radius_mm: 通道半径。

    返回:
        点到通道轴段最近点的距离减去半径。
    """

    segment = end - start
    denominator = segment.dot(segment)
    if denominator <= _DIRECTION_TOLERANCE:
        return point.distance_to(start) - radius_mm
    fraction = max(0.0, min(1.0, (point - start).dot(segment) / denominator))
    return point.distance_to(start + segment * fraction) - radius_mm

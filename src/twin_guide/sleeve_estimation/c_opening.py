"""C 形导柱开口的二维轮廓约束。"""

from __future__ import annotations

import math


def rounded_c_opening_slot_profile(
    inner_radius: float,
    inner_opening_y: float,
    outer_cut: float,
    cutter_end: float,
    *,
    segments: int = 64,
    radial_overlap: float = 0.0,
) -> list[tuple[float, float]]:
    """返回由中心圆孔经相切圆角连接到直槽的开口轮廓。"""

    nominal_inner_cut = math.sqrt(max(0.0, inner_radius**2 - inner_opening_y**2))
    available_span = outer_cut - nominal_inner_cut
    if available_span <= 1e-9 or inner_opening_y <= 1e-9:
        return [
            (nominal_inner_cut, -inner_opening_y),
            (cutter_end, -inner_opening_y),
            (cutter_end, inner_opening_y),
            (nominal_inner_cut, inner_opening_y),
        ]

    length_segments = min(
        max(2, segments),
        max(16, math.ceil(available_span / 0.012)),
    )

    # 三次贝塞尔圆角在圆孔交点采用圆周切线，在直槽端采用水平切线。
    # 两个控制柄各占可用跨度的 22%，保持与真实参考相近的浅圆角外形。
    tangent_slope = -nominal_inner_cut / inner_opening_y
    handle = min(
        0.22 * available_span,
        0.60 * inner_opening_y / max(abs(tangent_slope), 1e-12),
    )
    tangent_segments = math.ceil(8.00 * available_span / handle)
    effective_segments = min(512, max(length_segments, tangent_segments))
    start = (nominal_inner_cut, inner_opening_y)
    control_1 = (start[0] + handle, start[1] + tangent_slope * handle)
    control_2 = (outer_cut - handle, inner_opening_y)
    end = (outer_cut, inner_opening_y)
    upper_round = [
        (
            (1.0 - t) ** 3 * start[0]
            + 3.0 * (1.0 - t) ** 2 * t * control_1[0]
            + 3.0 * (1.0 - t) * t**2 * control_2[0]
            + t**3 * end[0],
            (1.0 - t) ** 3 * start[1]
            + 3.0 * (1.0 - t) ** 2 * t * control_1[1]
            + 3.0 * (1.0 - t) * t**2 * control_2[1]
            + t**3 * end[1],
        )
        for t in (index / effective_segments for index in range(effective_segments + 1))
    ]
    lower_round = [(x, -y) for x, y in upper_round]
    overlap_scale = max(0.0, 1.0 - radial_overlap / inner_radius)
    lower_overlap = (
        lower_round[0][0] * overlap_scale,
        lower_round[0][1] * overlap_scale,
    )
    upper_overlap = (
        upper_round[0][0] * overlap_scale,
        upper_round[0][1] * overlap_scale,
    )
    return [
        lower_overlap,
        *lower_round,
        (cutter_end, -inner_opening_y),
        (cutter_end, inner_opening_y),
        *reversed(upper_round),
        upper_overlap,
    ]

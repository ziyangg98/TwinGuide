"""C 形导柱开口的二维轮廓约束。"""

from __future__ import annotations

import math


def rounded_c_opening_slot_profile(
    inner_radius: float,
    inner_opening_y: float,
    outer_cut: float,
    cutter_end: float,
    *,
    segments: int = 16,
) -> list[tuple[float, float]]:
    """返回先平直、再以浅圆弧收敛到共同截平面的 C 口槽轮廓。"""

    nominal_inner_cut = math.sqrt(max(0.0, inner_radius**2 - inner_opening_y**2))
    round_start_x = nominal_inner_cut + 0.10 * inner_radius
    round_span = outer_cut - round_start_x
    if round_span <= 1e-9:
        return [
            (nominal_inner_cut, -inner_opening_y),
            (cutter_end, -inner_opening_y),
            (cutter_end, inner_opening_y),
            (nominal_inner_cut, inner_opening_y),
        ]

    edge_radius = 1.66 * round_span
    center_x = 0.5 * (round_start_x + outer_cut)
    center_y = inner_opening_y + math.sqrt(edge_radius**2 - (0.5 * round_span) ** 2)
    upper_round = [
        (
            x,
            center_y - math.sqrt(max(0.0, edge_radius**2 - (x - center_x) ** 2)),
        )
        for x in (round_start_x + round_span * index / segments for index in range(segments + 1))
    ]
    lower_round = [(x, -y) for x, y in upper_round]
    return [
        (nominal_inner_cut, -inner_opening_y),
        *lower_round,
        (cutter_end, -inner_opening_y),
        (cutter_end, inner_opening_y),
        *reversed(upper_round),
        (nominal_inner_cut, inner_opening_y),
    ]

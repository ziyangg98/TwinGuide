import math
from itertools import pairwise

import pytest

from twin_guide.sleeve_estimation.c_opening import rounded_c_opening_slot_profile


def _rounds(profile):
    round_point_count = (len(profile) - 4) // 2
    lower_round = profile[1 : 1 + round_point_count]
    upper_start = 1 + round_point_count + 2
    upper_round = list(reversed(profile[upper_start : upper_start + round_point_count]))
    return lower_round, upper_round


def test_c_opening_slot_has_common_plane_and_shallow_rounded_edges():
    inner_radius = 1.025
    inner_angle = math.radians(252.90)
    inner_opening_y = inner_radius * math.sin(0.5 * (2.0 * math.pi - inner_angle))

    profile = rounded_c_opening_slot_profile(
        inner_radius,
        inner_opening_y,
        1.3998221888,
        7.65,
        radial_overlap=0.005,
    )

    assert profile[0][0] == pytest.approx(profile[-1][0])
    assert profile[0][1] == pytest.approx(-profile[-1][1])
    assert math.hypot(*profile[0]) < inner_radius

    lower_round, upper_round = _rounds(profile)
    assert math.hypot(*lower_round[0]) == pytest.approx(inner_radius)
    assert lower_round[0][1] == pytest.approx(-inner_opening_y)
    assert lower_round[-1][1] == pytest.approx(-inner_opening_y)
    assert max(y for _, y in lower_round) > -inner_opening_y + 0.04
    assert upper_round == pytest.approx([(x, -y) for x, y in lower_round])
    assert all(first[0] <= second[0] for first, second in pairwise(lower_round))

    # 圆角起点与中心孔相切，圆角终点与水平直槽相切。
    start_secant = (
        lower_round[1][0] - lower_round[0][0],
        lower_round[1][1] - lower_round[0][1],
    )
    circle_tangent = (-lower_round[0][1], lower_round[0][0])
    start_cross = start_secant[0] * circle_tangent[1] - start_secant[1] * circle_tangent[0]
    assert abs(start_cross) / math.hypot(*start_secant) < 0.03
    end_slope = (lower_round[-1][1] - lower_round[-2][1]) / (
        lower_round[-1][0] - lower_round[-2][0]
    )
    assert abs(end_slope) < 0.02


@pytest.mark.parametrize("inner_arc_angle_degrees", range(180, 351))
def test_c_opening_round_stays_on_its_side_for_extreme_valid_arcs(
    inner_arc_angle_degrees,
):
    inner_radius = 1.025
    inner_opening_y = inner_radius * math.sin(0.5 * math.radians(360.0 - inner_arc_angle_degrees))
    profile = rounded_c_opening_slot_profile(
        inner_radius,
        inner_opening_y,
        1.40,
        7.65,
        radial_overlap=0.005,
    )

    lower_round, upper_round = _rounds(profile)
    assert all(y <= 1e-12 for _, y in lower_round)
    assert all(y >= -1e-12 for _, y in upper_round)

    start_secant = (
        upper_round[1][0] - upper_round[0][0],
        upper_round[1][1] - upper_round[0][1],
    )
    circle_tangent = (upper_round[0][1], -upper_round[0][0])
    cosine = sum(a * b for a, b in zip(start_secant, circle_tangent, strict=True)) / (
        math.hypot(*start_secant) * math.hypot(*circle_tangent)
    )
    assert math.degrees(math.acos(max(-1.0, min(1.0, cosine)))) < 3.5

    end_secant = (
        upper_round[-1][0] - upper_round[-2][0],
        upper_round[-1][1] - upper_round[-2][1],
    )
    assert abs(math.degrees(math.atan2(end_secant[1], end_secant[0]))) < 3.5

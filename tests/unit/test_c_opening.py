import math
from itertools import pairwise

import pytest

from twin_guide.sleeve_estimation.c_opening import rounded_c_opening_slot_profile


def test_c_opening_slot_has_common_plane_and_shallow_rounded_edges():
    inner_radius = 1.025
    inner_angle = math.radians(252.90)
    inner_opening_y = inner_radius * math.sin(0.5 * (2.0 * math.pi - inner_angle))

    profile = rounded_c_opening_slot_profile(
        inner_radius,
        inner_opening_y,
        1.3998221888,
        7.65,
    )

    assert profile[0][0] == pytest.approx(profile[-1][0])
    assert profile[0][1] == pytest.approx(-profile[-1][1])
    assert math.hypot(*profile[0]) == pytest.approx(inner_radius)

    lower_round = profile[1:18]
    upper_round = list(reversed(profile[20:37]))
    assert lower_round[0][0] == pytest.approx(profile[0][0] + 0.10 * inner_radius)
    assert lower_round[-1][0] == pytest.approx(1.3998221888)
    assert lower_round[0][1] == pytest.approx(-inner_opening_y)
    assert lower_round[-1][1] == pytest.approx(-inner_opening_y)
    assert max(y for _, y in lower_round) > -inner_opening_y + 0.04
    assert upper_round == pytest.approx([(x, -y) for x, y in lower_round])
    assert all(first[0] <= second[0] for first, second in pairwise(lower_round))

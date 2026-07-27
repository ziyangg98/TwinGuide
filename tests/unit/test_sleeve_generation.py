import unittest

from twin_guide.config import SleeveParameters
from twin_guide.errors import GeometryError
from twin_guide.geometry import Vec3
from twin_guide.sleeve_generation import _GuideCandidate, _select_pair


def _candidate(
    index: int,
    *,
    length_mm: float,
    radius_mm: float,
    bore_clear_fraction: float,
) -> _GuideCandidate:
    return _GuideCandidate(
        component_index=index,
        guide_mesh=None,
        samples=(),
        center=Vec3(float(index), 0.0, 0.0),
        axis=Vec3(0.0, 0.0, 1.0),
        axial_min_mm=0.0,
        axial_max_mm=length_mm,
        outer_radius_mm=radius_mm,
        axial_bore_clear_fraction=bore_clear_fraction,
    )


class SleeveCandidateSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parameters = SleeveParameters(
            inner_diameter_mm=2.1,
            outer_diameter_mm=4.3,
            height_mm=16.373,
            platform_width_mm=2.036,
            platform_height_mm=9.875,
            closed_bore_height_mm=4.777,
            inner_arc_angle_degrees=264.934,
            outer_arc_angle_degrees=211.684,
        )

    def test_solid_cutters_cannot_outscore_bore_bearing_guides(self) -> None:
        candidates = (
            _candidate(
                2,
                length_mm=13.0,
                radius_mm=2.95,
                bore_clear_fraction=1.0,
            ),
            _candidate(
                3,
                length_mm=12.6,
                radius_mm=2.87,
                bore_clear_fraction=1.0,
            ),
            _candidate(
                4,
                length_mm=16.373,
                radius_mm=2.15,
                bore_clear_fraction=0.0,
            ),
            _candidate(
                5,
                length_mm=16.373,
                radius_mm=2.15,
                bore_clear_fraction=0.0,
            ),
        )

        selected = _select_pair(candidates, self.parameters)

        self.assertEqual(
            tuple(candidate.component_index for candidate in selected),
            (2, 3),
        )

    def test_fails_closed_when_two_bore_candidates_are_unavailable(self) -> None:
        candidates = (
            _candidate(
                2,
                length_mm=13.0,
                radius_mm=2.95,
                bore_clear_fraction=1.0,
            ),
            _candidate(
                4,
                length_mm=16.373,
                radius_mm=2.15,
                bore_clear_fraction=0.0,
            ),
        )

        with self.assertRaisesRegex(GeometryError, "轴向孔道"):
            _select_pair(candidates, self.parameters)


if __name__ == "__main__":
    unittest.main()

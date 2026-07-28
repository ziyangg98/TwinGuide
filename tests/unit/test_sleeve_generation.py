import unittest

from twin_guide.config import SleeveParameters
from twin_guide.errors import GeometryError
from twin_guide.geometry import Vec3
from twin_guide.sleeve_estimation.types import SleeveAxis
from twin_guide.sleeve_generation import (
    _filter_bore_candidates,
    _GuideCandidate,
    _orient_axis_against_occlusal,
    _select_pair,
)


def _candidate(
    index: int,
    *,
    length_mm: float,
    radius_mm: float,
    clear_probe_count: int,
) -> _GuideCandidate:
    return _GuideCandidate(
        component_index=index,
        guide_mesh=None,
        center=Vec3(float(index), 0.0, 0.0),
        axis=Vec3(0.0, 0.0, 1.0),
        axial_min_mm=0.0,
        axial_max_mm=length_mm,
        outer_radius_mm=radius_mm,
        fitted_pose=SleeveAxis(Vec3(float(index), 0.0, 0.0), Vec3(0.0, 0.0, 1.0)),
        fitted_axial_min_mm=0.0,
        fitted_axial_max_mm=length_mm,
        clear_bore_probe_count=clear_probe_count,
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
                clear_probe_count=7,
            ),
            _candidate(
                3,
                length_mm=12.6,
                radius_mm=2.87,
                clear_probe_count=7,
            ),
            _candidate(
                4,
                length_mm=16.373,
                radius_mm=2.15,
                clear_probe_count=0,
            ),
            _candidate(
                5,
                length_mm=16.373,
                radius_mm=2.15,
                clear_probe_count=0,
            ),
        )

        selected = _select_pair(candidates, self.parameters)

        self.assertEqual(
            tuple(candidate.component_index for candidate in selected),
            (2, 3),
        )

    def test_five_of_seven_bore_probes_is_the_exact_boundary(self) -> None:
        candidates = tuple(
            _candidate(
                index,
                length_mm=16.373,
                radius_mm=2.15,
                clear_probe_count=clear_probe_count,
            )
            for index, clear_probe_count in ((4, 4), (5, 5), (6, 7))
        )

        eligible = _filter_bore_candidates(candidates)

        self.assertEqual(
            tuple(candidate.component_index for candidate in eligible),
            (5, 6),
        )

    def test_jaw_direction_is_the_only_axis_sign_rule(self) -> None:
        vertices = (Vec3(0.0, 0.0, -2.0), Vec3(0.0, 0.0, 3.0))
        raw = SleeveAxis(Vec3(0.0, 0.0, -2.0), Vec3(0.0, 0.0, 1.0))

        maxillary = _orient_axis_against_occlusal(
            raw,
            vertices,
            Vec3(0.0, 0.0, -1.0),
        )
        mandibular = _orient_axis_against_occlusal(
            raw,
            vertices,
            Vec3(0.0, 0.0, 1.0),
        )

        self.assertEqual(maxillary.axis, Vec3(0.0, 0.0, 1.0))
        self.assertEqual(maxillary.axis_origin, Vec3(0.0, 0.0, -2.0))
        self.assertEqual(mandibular.axis, Vec3(0.0, 0.0, -1.0))
        self.assertEqual(mandibular.axis_origin, Vec3(0.0, 0.0, 3.0))

    def test_fails_closed_when_two_bore_candidates_are_unavailable(self) -> None:
        candidates = (
            _candidate(
                2,
                length_mm=13.0,
                radius_mm=2.95,
                clear_probe_count=7,
            ),
            _candidate(
                4,
                length_mm=16.373,
                radius_mm=2.15,
                clear_probe_count=0,
            ),
        )

        with self.assertRaisesRegex(GeometryError, "轴向孔道"):
            _select_pair(candidates, self.parameters)

    def test_final_failure_lists_probe_counts_and_analysis_rejections(self) -> None:
        candidates = (
            _candidate(
                8,
                length_mm=16.373,
                radius_mm=2.15,
                clear_probe_count=4,
            ),
        )

        with self.assertRaisesRegex(
            GeometryError,
            r"8:4/7.*9:分量表面积过小",
        ):
            _select_pair(
                candidates,
                self.parameters,
                ("9:分量表面积过小",),
            )


if __name__ == "__main__":
    unittest.main()

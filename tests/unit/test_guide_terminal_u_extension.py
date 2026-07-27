from __future__ import annotations

import unittest

from twin_guide.errors import GeometryError
from twin_guide.guide_terminal_u_extension import _turnaround_distal_positions
from twin_guide.geometry import Vec3
from twin_guide.press_beam_points import _farthest_point_from_two_anchors


class GuideTerminalUExtensionTests(unittest.TestCase):
    def test_y_anchor_maximizes_the_smaller_distance_to_two_anchors(self):
        point, tangent, distances, fraction = _farthest_point_from_two_anchors(
            (Vec3(0, 0, 0), Vec3(10, 0, 0), Vec3(20, 0, 0)),
            Vec3(0, 3, 0),
            Vec3(0, -3, 0),
            start_margin_mm=4.6,
            end_margin_mm=4.6,
        )

        self.assertEqual(point, Vec3(15.4, 0, 0))
        self.assertEqual(tangent, Vec3(1, 0, 0))
        self.assertAlmostEqual(distances[0], distances[1])
        self.assertAlmostEqual(fraction, 0.77)

    def test_y_anchor_respects_configured_end_margin(self):
        point, _, distances, _ = _farthest_point_from_two_anchors(
            (Vec3(0, 0, 0), Vec3(20, 0, 0)),
            Vec3(0, 1, 0),
            Vec3(0, -1, 0),
            start_margin_mm=4.6,
            end_margin_mm=4.6,
        )

        self.assertEqual(point, Vec3(15.4, 0, 0))
        self.assertAlmostEqual(distances[0], distances[1])

    def test_y_anchor_can_use_distal_segment_endpoint_when_end_margin_is_zero(self):
        point, _, _, fraction = _farthest_point_from_two_anchors(
            (Vec3(0, 0, 0), Vec3(20, 0, 0)),
            Vec3(0, 1, 0),
            Vec3(0, -1, 0),
            start_margin_mm=4.6,
            end_margin_mm=0.0,
        )

        self.assertEqual(point, Vec3(20, 0, 0))
        self.assertAlmostEqual(fraction, 1.0)

    def test_turnaround_depth_is_contained_inside_target_apex_clearance(self):
        entry, apex = _turnaround_distal_positions(
            distal_surface_extent_mm=4.0,
            centerline_clearance_mm=2.8,
            turnaround_depth_mm=3.0,
        )

        self.assertAlmostEqual(entry, 3.8)
        self.assertAlmostEqual(apex, 6.8)
        # With a 2.3 mm beam radius, the planned surface gap is 0.5 mm,
        # rather than the former 3.5 mm produced by double-counting depth.
        self.assertAlmostEqual(apex - 2.3 - 4.0, 0.5)

    def test_rejects_turnaround_depth_that_crosses_terminal_center(self):
        with self.assertRaisesRegex(GeometryError, "turnaround_depth_mm 过大"):
            _turnaround_distal_positions(
                distal_surface_extent_mm=2.5,
                centerline_clearance_mm=2.8,
                turnaround_depth_mm=5.3,
            )

if __name__ == "__main__":
    unittest.main()

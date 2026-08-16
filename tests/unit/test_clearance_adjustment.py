import unittest

import numpy as np

from twin_guide.clearance_adjustment import (
    _adaptive_signed_safe_boundary,
    _handle_direction,
    _one_way_envelope_angles,
    _signed_release_then_safe_boundary,
    _signed_buccal_target_angle,
)


class _Mesh:
    def __init__(self, vertices):
        self.vertices = np.asarray(vertices, dtype=float)


class BuccalDirectionTests(unittest.TestCase):
    def test_signed_angle_rotates_handle_toward_positive_buccal_side(self):
        angle, projected = _signed_buccal_target_angle(
            np.asarray([0.0, 0.0, 1.0]),
            np.asarray([1.0, 0.0, 0.0]),
            np.asarray([0.0, 1.0, 0.0]),
        )

        self.assertAlmostEqual(angle, 90.0)
        np.testing.assert_allclose(projected, [0.0, 1.0, 0.0])

    def test_signed_angle_rotates_handle_toward_negative_buccal_side(self):
        angle, _ = _signed_buccal_target_angle(
            np.asarray([0.0, 0.0, 1.0]),
            np.asarray([1.0, 0.0, 0.0]),
            np.asarray([0.0, -1.0, 0.0]),
        )

        self.assertAlmostEqual(angle, -90.0)

    def test_handle_direction_uses_farthest_coherent_radial_region(self):
        mesh = _Mesh(
            [
                [10.0, 0.0, 0.0],
                [9.8, 0.2, 0.0],
                [9.7, -0.2, 0.0],
                [1.0, 1.0, 0.0],
                [-1.0, 1.0, 0.0],
                [0.0, 0.0, 2.0],
            ]
        )

        direction, count, radius = _handle_direction(
            mesh,
            np.zeros(3),
            np.asarray([0.0, 0.0, 1.0]),
        )

        self.assertGreater(count, 0)
        self.assertAlmostEqual(radius, 10.0)
        self.assertGreater(direction[0], 0.99)

    def test_adaptive_search_refines_first_positive_collision(self):
        safe, failure, evaluated = _adaptive_signed_safe_boundary(
            90.0,
            10.0,
            0.1,
            lambda angle: (
                {
                    "angle_degrees": angle,
                    "tooth_intrusion": False,
                    "back_u_connector_contact": True,
                }
                if angle >= 47.25
                else None
            ),
        )

        self.assertIsNotNone(safe)
        assert safe is not None and failure is not None
        self.assertLess(safe, 47.25)
        self.assertLessEqual(47.25 - safe, 0.1)
        self.assertGreaterEqual(failure["angle_degrees"], 47.25)
        self.assertLess(len(evaluated), 16)

    def test_adaptive_search_preserves_negative_rotation_sign(self):
        safe, failure, _ = _adaptive_signed_safe_boundary(
            -90.0,
            10.0,
            0.1,
            lambda angle: (
                {
                    "angle_degrees": angle,
                    "tooth_intrusion": True,
                    "back_u_connector_contact": False,
                }
                if angle <= -32.5
                else None
            ),
        )

        self.assertIsNotNone(safe)
        assert safe is not None and failure is not None
        self.assertGreater(safe, -32.5)
        self.assertLessEqual(safe + 32.5, 0.1)
        self.assertLessEqual(failure["angle_degrees"], -32.5)

    def test_envelope_angles_use_independent_step_and_keep_endpoints(self):
        angles = _one_way_envelope_angles(-2.3, 0.5)

        self.assertEqual(len(angles), 6)
        self.assertAlmostEqual(float(angles[0]), 0.0)
        self.assertAlmostEqual(float(angles[-1]), -2.3)
        self.assertLessEqual(float(np.max(np.abs(np.diff(angles)))), 0.5)

    def test_initial_tooth_and_connector_contact_can_clear_during_rotation(self):
        safe, release, reentry, initial, records = (
            _signed_release_then_safe_boundary(
                90.0,
                10.0,
                0.1,
                lambda angle: {
                    "tooth_intrusion": angle < 22.25,
                    "back_u_connector_contact": angle < 70.0,
                },
            )
        )

        self.assertEqual(safe, 90.0)
        assert release is not None
        self.assertGreaterEqual(release, 22.25)
        self.assertLessEqual(release - 22.25, 0.1)
        self.assertIsNone(reentry)
        self.assertTrue(initial["tooth_intrusion"])
        self.assertTrue(initial["back_u_connector_contact"])
        self.assertTrue(any(not item["tooth_intrusion"] for item in records))

    def test_rejects_path_when_tooth_intrusion_never_clears(self):
        safe, release, reentry, initial, _ = _signed_release_then_safe_boundary(
            -60.0,
            5.0,
            0.1,
            lambda angle: {
                "tooth_intrusion": True,
                "back_u_connector_contact": angle > -20.0,
            },
        )

        self.assertIsNone(safe)
        self.assertIsNone(release)
        self.assertIsNone(reentry)
        self.assertTrue(initial["tooth_intrusion"])

    def test_stops_at_first_tooth_reentry_after_initial_release(self):
        safe, release, reentry, _, _ = _signed_release_then_safe_boundary(
            80.0,
            10.0,
            0.1,
            lambda angle: {
                "tooth_intrusion": angle < 12.0 or angle >= 53.4,
                "back_u_connector_contact": False,
            },
        )

        assert safe is not None and release is not None and reentry is not None
        self.assertGreaterEqual(release, 12.0)
        self.assertLess(safe, 53.4)
        self.assertLessEqual(53.4 - safe, 0.1)
        self.assertGreaterEqual(reentry["angle_degrees"], 53.4)


if __name__ == "__main__":
    unittest.main()

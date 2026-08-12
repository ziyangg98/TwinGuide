import unittest

import numpy as np
import trimesh

from twin_guide.errors import GeometryError
from twin_guide.template_ring_estimation import (
    estimate_template_ring,
    estimate_template_ring_top_plane,
    estimate_template_rings,
)


class TemplateRingEstimationTests(unittest.TestCase):
    def test_estimates_rotated_annular_center_without_sleeve_input(self):
        ring = trimesh.creation.annulus(r_min=1.3, r_max=3.2, height=5.0, sections=96)
        rotation = trimesh.transformations.rotation_matrix(
            np.deg2rad(31.0),
            [0.4, -0.2, 1.0],
        )
        translation = np.array([7.5, -3.25, 4.75])
        transform = rotation.copy()
        transform[:3, 3] = translation
        ring.apply_transform(transform)

        estimate = estimate_template_ring(ring)

        self.assertLess(np.linalg.norm(np.asarray(estimate.center.as_tuple()) - translation), 0.05)
        expected_axis = rotation[:3, :3] @ np.array([0.0, 0.0, 1.0])
        self.assertGreater(abs(np.dot(estimate.axis.as_tuple(), expected_axis)), 0.999)
        self.assertGreaterEqual(estimate.supporting_slice_count, 20)
        self.assertLess(estimate.circle_rms_mm, 0.01)

        top_plane = estimate_template_ring_top_plane(ring, estimate)
        expected_top_center = translation + expected_axis * 2.5
        self.assertLess(
            np.linalg.norm(np.asarray(top_plane.center.as_tuple()) - expected_top_center),
            0.05,
        )
        self.assertGreater(np.dot(top_plane.normal.as_tuple(), expected_axis), 0.999)
        self.assertGreater(top_plane.offset_from_ring_center_mm, 0.0)
        self.assertGreater(top_plane.supporting_area_mm2, 15.0)

    def test_rejects_mesh_without_a_stable_circular_section(self):
        box = trimesh.creation.box(extents=(8.0, 5.0, 3.0))

        with self.assertRaises(GeometryError):
            estimate_template_ring(box)

    def test_estimates_all_rings_and_merges_duplicate_axis_candidates(self):
        rings = []
        expected_centers = (
            np.array([-9.0, 2.0, 1.5]),
            np.array([0.0, -3.0, 1.5]),
            np.array([8.0, 4.0, 1.5]),
        )
        for center in expected_centers:
            ring = trimesh.creation.annulus(
                r_min=1.6,
                r_max=3.4,
                height=4.0,
                sections=96,
            )
            ring.apply_translation(center)
            rings.append(ring)
        mesh = trimesh.util.concatenate(rings)

        estimates = estimate_template_rings(mesh)

        self.assertEqual(len(estimates), 3)
        actual_centers = [np.asarray(estimate.center.as_tuple()) for estimate in estimates]
        for expected in expected_centers:
            self.assertLess(
                min(np.linalg.norm(actual - expected) for actual in actual_centers), 0.05
            )


if __name__ == "__main__":
    unittest.main()

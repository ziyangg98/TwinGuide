"""Synthetic invariants for the isolated ``fdi_new`` package."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import unittest

import numpy as np

from twin_guide.tooth_fdi_mapping_new.component_segmentation import (
    SegmentationDiagnostics,
    _finite_chord_3d_boundary_support,
    _finite_separator_core_clearance,
    _independent_crown_basin_support,
    _remove_endpoint_collisions,
    _surface_valley_barrier,
    choose_partition_map,
    segment_component_local_regions,
)
from twin_guide.tooth_fdi_mapping_new.arch_coordinates import (
    _pca_axis_permutation_candidates,
)
from twin_guide.tooth_fdi_mapping_new.models import (
    AlignmentAssignment,
    AlignmentPath,
    ArchFrame,
    CoreTrack,
    CrownHypothesis,
    LabeledMissingSlotAnchor,
    ToothFdiMappingNewRequest,
    ToothRegion,
)
from twin_guide.tooth_fdi_mapping_new.missing_slot_anchors import (
    evaluate_anchor_alignment,
    evaluate_anchor_frame,
)
from twin_guide.tooth_fdi_mapping_new.multi_view_boundary import (
    assignment_pair_boundary_evidence,
    build_multiview_boundary_evidence,
    build_multiview_frames,
)
from twin_guide.tooth_fdi_mapping_new.multiscale_candidates import (
    _raw_core_observations,
    add_relative_relief_fields,
)
from twin_guide.tooth_fdi_mapping_new.sequence_alignment import (
    _physical_crownness,
    build_crown_hypotheses,
    solve_monotone_fdi_alignment,
)
from twin_guide.tooth_fdi_mapping_new.recognition import (
    _prioritize_complete_present_paths,
    _structural_evidence_diagnostics,
)
from twin_guide.tooth_fdi_mapping_new.surface_valleys import (
    build_surface_valley_evidence,
    estimate_minimum_curvature,
)
from twin_guide.tooth_mapping.fdi import CANONICAL_ORDER, signed_midline_distance_prior_mm
from twin_guide.tooth_mapping.enhanced_projection import rasterise_crown_triangles
from twin_guide.tooth_mapping.contact_chords import _paired_concavity_metrics
from twin_guide.tooth_mapping.contact_guide_mapping import (
    fit_measured_contour_arch,
    locate_reported_teeth,
)


def _frame(name: str = "confirmed") -> ArchFrame:
    curve_s = np.linspace(-50.0, 50.0, 501)
    return ArchFrame(
        origin=np.zeros(3),
        e_lr=np.asarray([1.0, 0.0, 0.0]),
        e_ap=np.asarray([0.0, 1.0, 0.0]),
        e_occ=np.asarray([0.0, 0.0, 1.0]),
        curve_lr=curve_s.copy(),
        curve_ap=np.zeros_like(curve_s),
        curve_s=curve_s,
        local_scale_mm=np.full_like(curve_s, 8.0),
        orientation_name=name,
    )


def _track(track_id: int, s_mm: float, crownness: float = 0.95) -> CoreTrack:
    return CoreTrack(
        track_id=track_id,
        observations=(),
        center_lr_ap_mm=(s_mm, 0.0),
        s_mm=s_mm,
        u_mm=0.0,
        local_scale_mm=8.0,
        persistence=1.0,
        crownness=crownness,
        support_scale_indices=(0, 1, 2, 3, 4),
    )


def _quadratic_patch(
    curvature_x: float, curvature_y: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    axis = np.linspace(-2.4, 2.4, 17)
    x, y = np.meshgrid(axis, axis, indexing="ij")
    z = 0.5 * curvature_x * x**2 + 0.5 * curvature_y * y**2
    vertices = np.column_stack([x.ravel(), y.ravel(), z.ravel()])
    normal = np.column_stack([
        -curvature_x * x.ravel(),
        -curvature_y * y.ravel(),
        np.ones(x.size),
    ])
    normal /= np.linalg.norm(normal, axis=1, keepdims=True)
    faces = []
    width = x.shape[1]
    for row in range(x.shape[0] - 1):
        for column in range(x.shape[1] - 1):
            first = row * width + column
            faces.extend([
                (first, first + width, first + width + 1),
                (first, first + width + 1, first + 1),
            ])
    center = (x.shape[0] // 2) * width + x.shape[1] // 2
    return vertices, np.asarray(faces, dtype=np.int64), normal, center


class MultiViewBoundaryV27Tests(unittest.TestCase):
    @staticmethod
    def _two_quad_surface(folded: bool) -> SimpleNamespace:
        slope = 0.55 if folded else 0.0
        vertices = np.asarray([
            [-2.0, -2.0, 0.0], [0.0, -2.0, 0.0],
            [0.0, 2.0, 0.0], [-2.0, 2.0, 0.0],
            [0.0, -2.0, 0.0], [2.0, -2.0, 2.0 * slope],
            [2.0, 2.0, 2.0 * slope], [0.0, 2.0, 0.0],
        ])
        faces = np.asarray([
            [0, 1, 2], [0, 2, 3],
            [4, 5, 6], [4, 6, 7],
        ], dtype=np.int64)
        left = np.asarray([0.0, 0.0, 1.0])
        right = np.asarray([-slope, 0.0, 1.0])
        right /= np.linalg.norm(right)
        normals = np.vstack([
            np.tile(left, (4, 1)), np.tile(right, (4, 1))
        ])
        return SimpleNamespace(
            vertices=vertices,
            faces=faces,
            vertex_normals=normals,
        )

    def test_view_ring_is_arch_frame_upper_hemisphere(self) -> None:
        frame = _frame()
        views = build_multiview_frames(
            frame, azimuth_count=8, obliquity_degrees=45.0
        )
        self.assertEqual(len(views), 9)
        self.assertEqual(views[0].view_id, "occlusal")
        for view in views:
            self.assertAlmostEqual(np.linalg.norm(view.e_depth), 1.0)
            self.assertGreater(float(view.e_depth @ frame.e_occ), 0.0)
            self.assertAlmostEqual(float(view.e_x @ view.e_depth), 0.0, places=7)
            self.assertAlmostEqual(float(view.e_y @ view.e_depth), 0.0, places=7)

    def test_silhouette_is_not_promoted_to_internal_boundary(self) -> None:
        evidence = build_multiview_boundary_evidence(
            self._two_quad_surface(folded=False),
            _frame(),
            azimuth_count=4,
            obliquity_degrees=45.0,
            resolution_mm=0.12,
        )
        occlusal = evidence.rasters[0]
        self.assertLess(float(np.max(occlusal.boundary_score)), 0.05)
        self.assertEqual(
            evidence.summary()["integration_mode"],
            "component_local_watershed_cost_only",
        )

    def test_fold_is_detected_and_backprojected_to_original_faces(self) -> None:
        evidence = build_multiview_boundary_evidence(
            self._two_quad_surface(folded=True),
            _frame(),
            azimuth_count=4,
            obliquity_degrees=45.0,
            resolution_mm=0.12,
        )
        self.assertEqual(evidence.face_boundary_score.shape, (4,))
        self.assertTrue(np.all(evidence.face_boundary_score >= 0.0))
        self.assertTrue(np.all(evidence.face_boundary_score <= 1.0))
        self.assertGreater(
            float(np.max(evidence.rasters[0].boundary_score)), 0.25
        )
        self.assertTrue(np.any(evidence.face_visible_view_count >= 2))
        self.assertTrue(np.any(evidence.face_supporting_view_count >= 1))

    def test_adjacent_assignment_pair_receives_local_boundary_evidence(self) -> None:
        frame = _frame()
        alignment = AlignmentPath(
            "confirmed", 1.0, 0.0, 0.2,
            (
                AlignmentAssignment(
                    11, "single:1", "single", (1,), (-1.0, 0.0), -1.0, 1.0, 0.1
                ),
                AlignmentAssignment(
                    21, "single:2", "single", (2,), (1.0, 0.0), 1.0, 1.0, 0.1
                ),
            ),
            (), (1, 2), (), (),
        )
        axis = np.linspace(-2.2, 2.2, 45)
        mask = np.ones((len(axis), len(axis)), dtype=bool)
        maps = {
            "lr_centres": axis,
            "ap_centres": axis,
            "resolution_mm": float(axis[1] - axis[0]),
            "silhouette": mask,
            "relative_crown_relief_score": np.ones(mask.shape),
        }

        def pair_score(folded: bool) -> float:
            evidence = build_multiview_boundary_evidence(
                self._two_quad_surface(folded=folded),
                frame,
                azimuth_count=4,
                obliquity_degrees=45.0,
                resolution_mm=0.10,
            )
            records, boundary, consistency = assignment_pair_boundary_evidence(
                evidence, alignment, frame, maps
            )
            self.assertEqual(len(records), 1)
            self.assertEqual(boundary.shape, mask.shape)
            self.assertEqual(consistency.shape, mask.shape)
            return float(records[0]["tooth_tooth_boundary_score"])

        self.assertGreater(pair_score(True), pair_score(False))


class SurfaceValleyEvidenceV24Tests(unittest.TestCase):
    def test_vertex_valley_field_follows_highest_surface_rasterization(self) -> None:
        vertices = np.asarray([
            [-2.0, -2.0, 1.0],
            [2.0, -2.0, 1.0],
            [2.0, 2.0, 1.0],
            [-2.0, 2.0, 1.0],
        ])
        faces = np.asarray([(0, 1, 2), (0, 2, 3)], dtype=np.int64)
        normals = np.tile(np.asarray([0.0, 0.0, 1.0]), (4, 1))
        maps = rasterise_crown_triangles(
            vertices_lr_ap_height=vertices,
            faces=faces,
            vertex_normals_lr_ap_occ=normals,
            height_floor_mm=0.5,
            resolution_mm=0.20,
            vertex_scalar_fields={
                "surface_valley_score": np.asarray([0.0, 1.0, 1.0, 0.0])
            },
        )
        mask = np.asarray(maps["silhouette"], dtype=bool)
        score = np.asarray(maps["surface_valley_score"], dtype=float)
        lr = np.asarray(maps["lr_centres"], dtype=float)
        self.assertTrue(np.all(np.isfinite(score[mask])))
        self.assertGreater(
            float(np.mean(score[mask & (lr[:, None] > 0.8)])),
            float(np.mean(score[mask & (lr[:, None] < -0.8)])),
        )

    def test_minimum_curvature_sign_separates_crown_and_valley(self) -> None:
        crown = _quadratic_patch(-0.18, -0.14)
        valley = _quadratic_patch(0.18, 0.14)
        crown_minimum, crown_valid = estimate_minimum_curvature(*crown[:3])
        valley_minimum, valley_valid = estimate_minimum_curvature(*valley[:3])
        self.assertTrue(crown_valid[crown[3]])
        self.assertTrue(valley_valid[valley[3]])
        self.assertGreater(crown_minimum[crown[3]], 0.08)
        self.assertLess(valley_minimum[valley[3]], -0.08)

    def test_read_only_mesh_normals_are_supported(self) -> None:
        vertices, faces, normals, center = _quadratic_patch(0.18, 0.14)
        normals.setflags(write=False)
        minimum, valid = estimate_minimum_curvature(vertices, faces, normals)
        self.assertTrue(valid[center])
        self.assertLess(minimum[center], -0.08)

    def test_scale_normalized_valley_score_is_bounded(self) -> None:
        vertices, faces, normals, center = _quadratic_patch(0.20, 0.12)
        evidence = build_surface_valley_evidence(
            vertices,
            faces,
            normals,
            normalization_scale_mm=8.0,
            smoothing_iterations=(0, 1, 2),
        )
        self.assertGreater(evidence.valley_strength[center], 0.5)
        self.assertTrue(np.all(evidence.valley_score >= 0.0))
        self.assertTrue(np.all(evidence.valley_score <= 1.0))
        self.assertGreater(evidence.valid_vertex_fraction, 0.90)


class PairedConcavityEvidenceV26Tests(unittest.TestCase):
    def test_opposing_notches_score_above_non_facing_notches(self) -> None:
        common = {
            "endpoint_1": np.asarray([0.0, 1.0]),
            "endpoint_2": np.asarray([0.0, -1.0]),
            "concavity_1": 0.40,
            "concavity_2": 0.40,
            "inter_seed_axis": np.asarray([1.0, 0.0]),
            "centre_distance": 7.0,
            "endpoint_crown_support": 0.80,
        }
        opposing = _paired_concavity_metrics(
            **common,
            notch_direction_1=np.asarray([0.0, -1.0]),
            notch_direction_2=np.asarray([0.0, 1.0]),
        )
        outward = _paired_concavity_metrics(
            **common,
            notch_direction_1=np.asarray([0.0, 1.0]),
            notch_direction_2=np.asarray([0.0, -1.0]),
        )
        self.assertAlmostEqual(float(opposing["facing"]), 1.0)
        self.assertAlmostEqual(float(outward["facing"]), 0.0)
        self.assertGreater(float(opposing["score"]), float(outward["score"]))
        self.assertEqual(opposing["level"], "strong")

    def test_axially_misaligned_notches_remain_soft_evidence(self) -> None:
        common = {
            "notch_direction_1": np.asarray([0.0, -1.0]),
            "notch_direction_2": np.asarray([0.0, 1.0]),
            "concavity_1": 0.30,
            "concavity_2": 0.30,
            "inter_seed_axis": np.asarray([1.0, 0.0]),
            "centre_distance": 6.0,
            "endpoint_crown_support": 0.70,
        }
        aligned = _paired_concavity_metrics(
            **common,
            endpoint_1=np.asarray([0.0, 1.0]),
            endpoint_2=np.asarray([0.0, -1.0]),
        )
        offset = _paired_concavity_metrics(
            **common,
            endpoint_1=np.asarray([-2.0, 1.0]),
            endpoint_2=np.asarray([2.0, -1.0]),
        )
        self.assertGreater(
            float(aligned["axial_alignment"]),
            float(offset["axial_alignment"]),
        )
        self.assertGreater(float(aligned["score"]), float(offset["score"]))


class V2GuideMappingCompatibilityTests(unittest.TestCase):
    def test_reported_crown_points_remain_authoritative(self) -> None:
        regions = []
        contours = []
        for index, (fdi, lr, ap) in enumerate(
            ((13, -5.0, -1.0), (12, 0.0, -3.0), (11, 5.0, -1.0))
        ):
            contour = [
                [lr - 1.5, ap],
                [lr, ap - 1.0],
                [lr + 1.5, ap],
                [lr, ap + 1.0],
            ]
            contours.append({
                "FDI": fdi,
                "area_centroid_LR_AP_mm": [lr, ap],
                "contour_LR_AP_mm": contour,
            })
            regions.append({
                "fdi": fdi,
                "area_centroid_lr_ap_mm": [lr, ap],
                "contour_lr_ap_mm": contour,
                "crown_height_mm": 4.0 + index,
                "crown_point_global_mm": [lr, ap, 4.0 + index],
            })
        frame = {"curve": fit_measured_contour_arch(contours)}

        locations = locate_reported_teeth(
            region_records=regions,
            frame=frame,
        )

        self.assertEqual([item.fdi for item in locations], [13, 12, 11])
        self.assertEqual(
            locations[1].crown_point_global_mm,
            (0.0, -3.0, 5.0),
        )
        self.assertEqual(
            locations[1].lift_method,
            "authoritative_v2_component_crown_point",
        )
        self.assertEqual(locations[1].lift_distance_mm, 0.0)


class SequenceAlignmentV2Tests(unittest.TestCase):
    def test_case_report_is_not_written_by_default(self) -> None:
        request = ToothFdiMappingNewRequest(Path("case.yaml"), Path("output"))
        self.assertFalse(request.write_report_json)

    def test_foreground_occupancy_cannot_promote_weak_track_to_single_tooth(self) -> None:
        weak = replace(
            _track(1, 0.0),
            persistence=0.4,
            support_scale_indices=(0, 1),
        )
        axis = np.linspace(-6.0, 6.0, 61)
        maps = {
            quantile: {
                "silhouette": np.ones((len(axis), len(axis)), dtype=bool),
                "resolution_mm": float(axis[1] - axis[0]),
                "lr_centres": axis,
                "ap_centres": axis,
            }
            for quantile in (0.35, 0.40, 0.45, 0.50, 0.55)
        }
        hypotheses = build_crown_hypotheses(
            [weak], _frame(), maps_by_quantile=maps
        )
        self.assertNotIn("single:1", {
            item.hypothesis_id for item in hypotheses
        })

    def test_foreground_occupancy_cannot_promote_weak_tracks_to_merge_tooth(self) -> None:
        weak_tracks = [
            replace(
                _track(1, -1.0),
                persistence=0.4,
                support_scale_indices=(0, 1),
            ),
            replace(
                _track(2, 1.0),
                persistence=0.4,
                support_scale_indices=(0, 1),
            ),
        ]
        axis = np.linspace(-6.0, 6.0, 61)
        maps = {
            quantile: {
                "silhouette": np.ones((len(axis), len(axis)), dtype=bool),
                "resolution_mm": float(axis[1] - axis[0]),
                "lr_centres": axis,
                "ap_centres": axis,
            }
            for quantile in (0.35, 0.40, 0.45, 0.50, 0.55)
        }
        hypotheses = build_crown_hypotheses(
            weak_tracks, _frame(), maps_by_quantile=maps
        )
        self.assertFalse(any(item.kind == "merge" for item in hypotheses))

    def test_finite_chord_requires_3d_ridge_stronger_than_both_crown_sides(self) -> None:
        axis = np.linspace(-4.0, 4.0, 81)
        component = np.ones((len(axis), len(axis)), dtype=bool)
        ridge = np.zeros_like(component, dtype=float)
        ridge[np.abs(axis) <= 0.15, :] = 1.0
        maps = {
            "lr_centres": axis,
            "ap_centres": axis,
            "resolution_mm": float(axis[1] - axis[0]),
            "surface_valley_score": ridge,
            "multi_view_boundary_score": ridge,
            "multi_view_consistency": np.ones_like(ridge),
            "fused_edge": ridge,
        }
        supported = _finite_chord_3d_boundary_support(
            first_endpoint_lr_ap_mm=(0.0, -3.5),
            second_endpoint_lr_ap_mm=(0.0, 3.5),
            first_center_lr_ap_mm=np.asarray([-2.0, 0.0]),
            second_center_lr_ap_mm=np.asarray([2.0, 0.0]),
            maps=maps,
            component=component,
            local_scale_mm=8.0,
        )
        self.assertTrue(supported["accepted"])

        uniform_maps = dict(maps)
        uniform_maps["surface_valley_score"] = np.ones_like(ridge)
        uniform_maps["multi_view_boundary_score"] = np.ones_like(ridge)
        rejected = _finite_chord_3d_boundary_support(
            first_endpoint_lr_ap_mm=(0.0, -3.5),
            second_endpoint_lr_ap_mm=(0.0, 3.5),
            first_center_lr_ap_mm=np.asarray([-2.0, 0.0]),
            second_center_lr_ap_mm=np.asarray([2.0, 0.0]),
            maps=uniform_maps,
            component=component,
            local_scale_mm=8.0,
        )
        self.assertFalse(rejected["accepted"])

        one_channel = dict(maps)
        one_channel["multi_view_boundary_score"] = np.zeros_like(ridge)
        rejected_one_channel = _finite_chord_3d_boundary_support(
            first_endpoint_lr_ap_mm=(0.0, -3.5),
            second_endpoint_lr_ap_mm=(0.0, 3.5),
            first_center_lr_ap_mm=np.asarray([-2.0, 0.0]),
            second_center_lr_ap_mm=np.asarray([2.0, 0.0]),
            maps=one_channel,
            component=component,
            local_scale_mm=8.0,
        )
        self.assertFalse(rejected_one_channel["accepted"])

    def test_two_crown_basins_require_topological_prominence(self) -> None:
        axis = np.linspace(-6.0, 6.0, 121)
        x, y = np.meshgrid(axis, axis, indexing="ij")
        component = x**2 / 5.5**2 + y**2 / 3.5**2 <= 1.0
        separate_relief = np.maximum(
            np.exp(-0.5 * (((x + 2.0) / 0.9) ** 2 + (y / 1.2) ** 2)),
            np.exp(-0.5 * (((x - 2.0) / 0.9) ** 2 + (y / 1.2) ** 2)),
        )
        maps = {
            "lr_centres": axis,
            "ap_centres": axis,
            "resolution_mm": float(axis[1] - axis[0]),
            "relative_crown_relief_score": separate_relief,
        }
        separate = _independent_crown_basin_support(
            first_center_lr_ap_mm=np.asarray([-2.0, 0.0]),
            second_center_lr_ap_mm=np.asarray([2.0, 0.0]),
            maps=maps,
            component=component,
            local_scale_mm=8.0,
        )
        self.assertTrue(separate["accepted"])

        single_relief = np.exp(-0.5 * ((x / 2.2) ** 2 + (y / 1.8) ** 2))
        single_maps = dict(maps)
        single_maps["relative_crown_relief_score"] = single_relief
        single = _independent_crown_basin_support(
            first_center_lr_ap_mm=np.asarray([-2.0, 0.0]),
            second_center_lr_ap_mm=np.asarray([2.0, 0.0]),
            maps=single_maps,
            component=component,
            local_scale_mm=8.0,
        )
        self.assertFalse(single["accepted"])

    def test_foreground_support_alone_cannot_propose_a_split(self) -> None:
        axis = np.linspace(-8.0, 8.0, 81)
        maps = {
            quantile: {
                "silhouette": np.ones((len(axis), len(axis)), dtype=bool),
                "resolution_mm": float(axis[1] - axis[0]),
                "lr_centres": axis,
                "ap_centres": axis,
            }
            for quantile in (0.35, 0.40, 0.45, 0.50, 0.55)
        }
        hypotheses = build_crown_hypotheses(
            [_track(1, 0.0)], _frame(), maps_by_quantile=maps
        )
        self.assertFalse(any(item.kind == "split" for item in hypotheses))

    def test_complete_present_path_outranks_cheaper_undetected_path(self) -> None:
        complete = AlignmentPath(
            "confirmed", 1.0, 0.0, 9.0,
            (
                AlignmentAssignment(
                    46, "single:1", "single", (1,), (-5.0, 0.0),
                    -5.0, 1.0, 1.0,
                ),
                AlignmentAssignment(
                    44, "single:2", "single", (2,), (5.0, 0.0),
                    5.0, 1.0, 8.0,
                ),
            ),
            (), (1, 2), (), ("46:single:1", "44:single:2"),
        )
        cheaper_incomplete = AlignmentPath(
            "confirmed", 1.0, 0.0, 2.0,
            (
                complete.assignments[0],
                AlignmentAssignment(
                    44, None, "undetected", (), None, None, 0.0, 1.0
                ),
            ),
            (), (1,), (44,), ("46:single:1", "44:undetected:"),
        )
        primary, diagnostic, applied = _prioritize_complete_present_paths(
            [cheaper_incomplete, complete]
        )
        self.assertTrue(applied)
        self.assertEqual(primary, [complete])
        self.assertEqual(diagnostic, [cheaper_incomplete])

    def test_undetected_path_remains_available_only_when_no_complete_path_exists(self) -> None:
        incomplete = AlignmentPath(
            "confirmed", 1.0, 0.0, 2.0,
            (
                AlignmentAssignment(
                    44, None, "undetected", (), None, None, 0.0, 2.0
                ),
            ),
            (), (), (44,), ("44:undetected:",),
        )
        primary, diagnostic, applied = _prioritize_complete_present_paths(
            [incomplete]
        )
        self.assertFalse(applied)
        self.assertEqual(primary, [incomplete])
        self.assertEqual(diagnostic, [])

    def test_both_pca_minor_axes_are_retained_as_occlusal_hypotheses(self) -> None:
        # Unique diagonal covariance keeps the expected eigenvectors stable;
        # the guide deliberately points along the second-minor axis.
        points = np.asarray([
            [sx * 1.0, sy * 2.0, sz * 4.0]
            for sx in (-1.0, 1.0)
            for sy in (-1.0, 1.0)
            for sz in (-1.0, 1.0)
        ])
        candidates = _pca_axis_permutation_candidates(
            points,
            np.zeros(3),
            np.asarray([0.0, 10.0, 0.0]),
        )
        self.assertEqual(
            [item["occlusal_axis_index"] for item in candidates], [0, 1]
        )
        self.assertLess(
            float(candidates[0]["guide_occlusal_alignment"]), 1.0e-6
        )
        self.assertGreater(
            float(candidates[1]["guide_occlusal_alignment"]), 0.999
        )

    def test_labeled_missing_slot_rejects_lr_mirror(self) -> None:
        anchor = LabeledMissingSlotAnchor(
            fdi=45,
            sleeve_id="sleeve_45",
            label_source="planning.implant_sites",
            mesh_path="sleeve45.stl",
            point_global_mm=(-20.0, 0.0, 0.0),
            point_method="synthetic",
        )
        correct = _frame("lr_positive")
        mirrored = _frame("lr_negative")
        mirrored.e_lr = np.asarray([-1.0, 0.0, 0.0])
        canonical = CANONICAL_ORDER["mandibular"]
        self.assertTrue(
            evaluate_anchor_frame(correct, [anchor], canonical)["compatible"]
        )
        mirrored_result = evaluate_anchor_frame(
            mirrored, [anchor], canonical
        )
        self.assertFalse(mirrored_result["compatible"])
        self.assertEqual(
            mirrored_result["violations"][0]["reason"],
            "sleeve_anchor_is_on_wrong_patient_side",
        )

    def test_multiple_sleeves_must_follow_canonical_rank_order(self) -> None:
        anchors = [
            LabeledMissingSlotAnchor(
                47, "s47", "planning.implant_sites", "47.stl",
                (-30.0, 0.0, 0.0), "synthetic",
            ),
            LabeledMissingSlotAnchor(
                45, "s45", "planning.implant_sites", "45.stl",
                (-20.0, 0.0, 0.0), "synthetic",
            ),
        ]
        canonical = CANONICAL_ORDER["mandibular"]
        self.assertTrue(
            evaluate_anchor_frame(_frame(), anchors, canonical)["compatible"]
        )
        reversed_positions = [
            replace(anchors[0], point_global_mm=(-15.0, 0.0, 0.0)),
            replace(anchors[1], point_global_mm=(-25.0, 0.0, 0.0)),
        ]
        result = evaluate_anchor_frame(
            _frame(), reversed_positions, canonical
        )
        self.assertFalse(result["compatible"])
        self.assertTrue(any(
            item["reason"] == "sleeve_anchors_violate_canonical_rank_order"
            for item in result["violations"]
        ))

    def test_present_crowns_must_bracket_labeled_missing_slot(self) -> None:
        anchor = LabeledMissingSlotAnchor(
            45, "s45", "planning.implant_sites", "45.stl",
            (-20.0, 0.0, 0.0), "synthetic",
        )
        correct = AlignmentPath(
            "confirmed", 1.0, 0.0, 0.0,
            (
                AlignmentAssignment(
                    46, "single:1", "single", (1,), (-25.0, 0.0),
                    -25.0, 1.0, 0.0,
                ),
                AlignmentAssignment(
                    44, "single:2", "single", (2,), (-15.0, 0.0),
                    -15.0, 1.0, 0.0,
                ),
            ),
            (), (1, 2), (), (),
        )
        canonical = CANONICAL_ORDER["mandibular"]
        self.assertTrue(evaluate_anchor_alignment(
            correct, _frame(), [anchor], canonical
        )["compatible"])
        wrong = replace(correct, assignments=(
            correct.assignments[0],
            replace(correct.assignments[1], s_mm=-24.0),
        ))
        result = evaluate_anchor_alignment(
            wrong, _frame(), [anchor], canonical
        )
        self.assertFalse(result["compatible"])
        self.assertEqual(
            result["violations"][0]["reason"],
            "present_crown_crosses_labeled_missing_slot_rank",
        )

    def test_canonical_rank_is_not_numeric_order(self) -> None:
        maxillary = CANONICAL_ORDER["maxillary"]
        self.assertLess(maxillary.index(17), maxillary.index(16))
        self.assertGreater(17, 16)
        mandibular = CANONICAL_ORDER["mandibular"]
        self.assertLess(mandibular.index(47), mandibular.index(46))

    def test_monotone_alignment_consumes_each_core_once(self) -> None:
        fdis = (17, 16, 15)
        tracks = [
            _track(index + 1, signed_midline_distance_prior_mm(fdi, "maxillary"))
            for index, fdi in enumerate(fdis)
        ]
        frame = _frame()
        hypotheses = build_crown_hypotheses(tracks, frame)
        best, second, margin = solve_monotone_fdi_alignment(
            [(frame, tracks, hypotheses)], fdis, "maxillary"
        )
        self.assertEqual(tuple(item.fdi for item in best.assignments), fdis)
        self.assertFalse(best.undetected_fdi)
        self.assertEqual(len(best.consumed_core_ids), len(set(best.consumed_core_ids)))
        self.assertIsNotNone(second)
        self.assertGreaterEqual(margin, 0.0)

    def test_artifact_operation_does_not_shift_fdi_order(self) -> None:
        fdis = (17, 16)
        tracks = [
            _track(1, -48.0, 0.05),
            _track(2, signed_midline_distance_prior_mm(17, "maxillary")),
            _track(3, signed_midline_distance_prior_mm(16, "maxillary")),
        ]
        frame = _frame()
        best, _, _ = solve_monotone_fdi_alignment(
            [(frame, tracks, build_crown_hypotheses(tracks, frame))],
            fdis,
            "maxillary",
        )
        self.assertEqual(tuple(item.fdi for item in best.assignments), fdis)
        self.assertIn(1, best.artifact_core_ids)

    def test_lr_mirror_is_compared_by_global_alignment(self) -> None:
        fdis = (17, 16, 15)
        expected = [
            signed_midline_distance_prior_mm(fdi, "maxillary") for fdi in fdis
        ]
        correct_frame = _frame("lr_positive")
        mirrored_frame = _frame("lr_negative")
        correct_tracks = [_track(index + 1, value) for index, value in enumerate(expected)]
        mirrored_tracks = [
            _track(index + 1, value)
            for index, value in enumerate(sorted(-value for value in expected))
        ]
        best, _, _ = solve_monotone_fdi_alignment(
            [
                (
                    correct_frame,
                    correct_tracks,
                    build_crown_hypotheses(correct_tracks, correct_frame),
                ),
                (
                    mirrored_frame,
                    mirrored_tracks,
                    build_crown_hypotheses(mirrored_tracks, mirrored_frame),
                ),
            ],
            fdis,
            "maxillary",
        )
        self.assertEqual(best.orientation_name, "lr_positive")

    def test_midline_offset_search_scales_with_case_crown_size(self) -> None:
        fdis = (12, 11, 21, 22)
        shift = 9.0
        tracks = [
            _track(
                index + 1,
                signed_midline_distance_prior_mm(fdi, "maxillary") + shift,
            )
            for index, fdi in enumerate(fdis)
        ]
        frame = _frame()
        best, _, _ = solve_monotone_fdi_alignment(
            [(frame, tracks, build_crown_hypotheses(tracks, frame))],
            fdis,
            "maxillary",
        )
        self.assertEqual(tuple(item.fdi for item in best.assignments), fdis)
        self.assertGreater(abs(best.midline_offset_mm), 3.0)

    def test_split_and_merge_hypotheses_consume_intervals_once(self) -> None:
        frame = _frame()
        split_track = _track(1, -5.0)
        split = CrownHypothesis(
            "split:1", "split", 0, 0, (1,), 2,
            ((-8.0, 0.0), (-2.0, 0.0)), (-8.0, -2.0),
            13.0, 0.99, 1.0, 0.99,
        )
        best, _, _ = solve_monotone_fdi_alignment(
            [(frame, [split_track], [split])], (12, 11), "maxillary"
        )
        self.assertTrue(all(item.kind == "split" for item in best.assignments))
        self.assertEqual(best.consumed_core_ids, (1,))

        merge_tracks = [_track(1, -7.0), _track(2, -5.5)]
        merge = CrownHypothesis(
            "merge:1+2", "merge", 0, 1, (1, 2), 1,
            ((-6.0, 0.0),), (-6.0,), 8.0, 0.99, 1.0, 0.99,
        )
        merged, _, _ = solve_monotone_fdi_alignment(
            [(frame, merge_tracks, [merge])], (11,), "maxillary"
        )
        self.assertEqual(merged.assignments[0].kind, "merge")
        self.assertEqual(merged.consumed_core_ids, (1, 2))

    def test_equal_candidate_count_can_reject_wrong_candidate(self) -> None:
        fdis = (17, 16)
        tracks = [
            _track(1, signed_midline_distance_prior_mm(17, "maxillary")),
            _track(2, 45.0, 0.01),
        ]
        frame = _frame()
        best, _, _ = solve_monotone_fdi_alignment(
            [(frame, tracks, build_crown_hypotheses(tracks, frame))],
            fdis,
            "maxillary",
        )
        self.assertIn(2, best.artifact_core_ids)
        self.assertIn(16, best.undetected_fdi)

    def test_missing_rank_changes_expected_spacing_without_physical_slot(self) -> None:
        fdis = (14, 11)
        tracks = [
            _track(index + 1, signed_midline_distance_prior_mm(fdi, "maxillary"))
            for index, fdi in enumerate(fdis)
        ]
        frame = _frame()
        best, _, _ = solve_monotone_fdi_alignment(
            [(frame, tracks, build_crown_hypotheses(tracks, frame))],
            fdis,
            "maxillary",
        )
        self.assertEqual(tuple(item.fdi for item in best.assignments), fdis)
        self.assertEqual(len(best.assignments), 2)
        self.assertFalse(best.undetected_fdi)

    def test_missing_semantics_cannot_turn_crownlike_core_into_cheap_artifact(self) -> None:
        frame = _frame()
        missing_site = signed_midline_distance_prior_mm(17, "maxillary")
        tracks = [
            _track(1, missing_site, 0.999),
            _track(2, signed_midline_distance_prior_mm(16, "maxillary"), 0.999),
        ]
        hypotheses = build_crown_hypotheses(tracks, frame)
        without_missing, _, _ = solve_monotone_fdi_alignment(
            [(frame, tracks, hypotheses)], (16,), "maxillary"
        )
        with_missing, _, _ = solve_monotone_fdi_alignment(
            [(frame, tracks, hypotheses)], (16,), "maxillary", missing_fdis=(17,)
        )
        self.assertAlmostEqual(
            without_missing.total_cost, with_missing.total_cost, places=9
        )

    def test_merge_cannot_erase_two_independently_persistent_cores(self) -> None:
        frame = _frame()
        tracks = [_track(1, -2.0), _track(2, 2.0)]
        hypotheses = build_crown_hypotheses(tracks, frame)
        self.assertFalse(any(item.kind == "merge" for item in hypotheses))

    def test_merge_can_absorb_close_fragmentary_duplicate(self) -> None:
        frame = _frame()
        stable = _track(1, -1.0)
        fragment = CoreTrack(
            **{
                **stable.__dict__,
                "track_id": 2,
                "s_mm": -0.6,
                "center_lr_ap_mm": (-0.6, 0.0),
                "persistence": 0.4,
                "support_scale_indices": (0, 1),
            }
        )
        hypotheses = build_crown_hypotheses([stable, fragment], frame)
        self.assertTrue(any(item.kind == "merge" for item in hypotheses))

    def test_relative_3d_support_only_reduces_flat_core_crownness(self) -> None:
        ordinary = _track(1, 0.0)
        flat = CoreTrack(
            **{
                **ordinary.__dict__,
                "relative_3d_tooth_support": 0.40,
            }
        )
        self.assertAlmostEqual(_physical_crownness(ordinary), ordinary.crownness)
        self.assertAlmostEqual(
            _physical_crownness(flat), 0.40 * ordinary.crownness
        )
        hypotheses = build_crown_hypotheses([flat], _frame())
        self.assertTrue(any(item.kind == "single" for item in hypotheses))
        self.assertFalse(any(item.kind == "split" for item in hypotheses))


class RelativeReliefV21Tests(unittest.TestCase):
    def test_local_relief_suppresses_tilt_and_preserves_crown_bump(self) -> None:
        resolution = 0.20
        lr = np.arange(-10.0, 10.0 + resolution, resolution)
        ap = np.arange(-8.0, 8.0 + resolution, resolution)
        x, y = np.meshgrid(lr, ap, indexing="ij")
        mask = np.ones(x.shape, dtype=bool)
        tilt = 0.08 * x + 0.04 * y
        bump = 3.0 * np.exp(-0.5 * ((x / 1.8) ** 2 + (y / 1.4) ** 2))
        maps = {
            "resolution_mm": resolution,
            "silhouette": mask,
            "top_height_mm": tilt + bump,
        }
        enhanced = add_relative_relief_fields(maps, (5.0, 7.0, 9.0))
        relief = np.asarray(enhanced["relative_crown_relief_mm"])
        center = relief[len(lr) // 2, len(ap) // 2]
        distant = relief[(np.abs(x) > 7.0) & (np.abs(y) > 5.0)]
        self.assertGreater(center, 1.0)
        self.assertLess(float(np.median(distant)), 0.35 * center)

    def test_core_records_anisotropic_arch_dimensions(self) -> None:
        lr = np.linspace(-8.0, 8.0, 161)
        ap = np.linspace(-6.0, 6.0, 121)
        x, y = np.meshgrid(lr, ap, indexing="ij")
        mask = (x / 4.5) ** 2 + (y / 2.5) ** 2 <= 1.0
        maps = {
            "resolution_mm": float(lr[1] - lr[0]),
            "lr_centres": lr,
            "ap_centres": ap,
            "silhouette": mask,
            "relative_crown_relief_mm": np.where(mask, 2.0, np.nan),
            "relative_crown_relief_score": np.where(mask, 0.8, 0.0),
        }
        observations = _raw_core_observations(maps, _frame(), 0, 0.35)
        self.assertEqual(len(observations), 1)
        observation = observations[0]
        self.assertGreater(
            observation.mesiodistal_width_mm,
            observation.buccolingual_width_mm,
        )
        self.assertGreater(observation.relative_crown_height_mm, 0.0)
        self.assertAlmostEqual(observation.projection_component_area_ratio, 1.0)


class StructuralSafetyV22Tests(unittest.TestCase):
    @staticmethod
    def _region(fdi: int, area: float) -> ToothRegion:
        return ToothRegion(
            fdi=fdi,
            region_id=fdi,
            pixel_count=100,
            area_mm2=area,
            area_centroid_lr_ap_mm=(0.0, 0.0),
            interior_center_lr_ap_mm=(0.0, 0.0),
            maximum_interior_radius_mm=2.0,
            contour_lr_ap_mm=((0.0, 0.0), (1.0, 0.0), (0.0, 1.0)),
            component_ids=(1,),
            boundary_method="test",
            boundary_confidence=1.0,
            crown_height_mm=2.0,
            crown_point_global_mm=(0.0, 0.0, 0.0),
        )

    @staticmethod
    def _diagnostics(*records: dict[str, object]) -> SegmentationDiagnostics:
        return SegmentationDiagnostics(
            component_count=1,
            seeded_component_count=1,
            artifact_component_ids=(),
            finite_separator_count=0,
            fallback_component_count=0,
            separator_component_local=True,
            assigned_pixel_fraction=1.0,
            separator_records=(),
            separator_candidate_records=records,
        )

    def test_weak_shared_split_is_unresolved_not_forced(self) -> None:
        assignments = (
            AlignmentAssignment(12, "split:1", "split", (1,), (-1.0, 0.0), -1.0, 1.0, 0.1, 1.0, 2),
            AlignmentAssignment(11, "split:1", "split", (1,), (1.0, 0.0), 1.0, 1.0, 0.1, 1.0, 2),
        )
        path = AlignmentPath(
            "confirmed", 1.0, 0.0, 0.2, assignments, (), (1,), (), ()
        )
        diagnostic = _structural_evidence_diagnostics(
            path,
            [self._region(12, 40.0), self._region(11, 42.0)],
            self._diagnostics({
                "rejection_reason": "low_evidence_shared_split_uses_level_2",
                "component_id": 1,
                "pair_index": 0,
                "evidence_score": 0.4,
                "component_evidence_median": 0.7,
            }),
            [_track(1, 0.0)],
        )
        self.assertFalse(diagnostic["safe_physical_hypothesis_path"])
        self.assertIn(
            "unresolved_single_or_multiple",
            {item["kind"] for item in diagnostic["conflicts"]},
        )

    def test_surface_valley_path_resolves_weak_shared_split(self) -> None:
        assignments = (
            AlignmentAssignment(12, "split:1", "split", (1,), (-1.0, 0.0), -1.0, 1.0, 0.1, 1.0, 2),
            AlignmentAssignment(11, "split:1", "split", (1,), (1.0, 0.0), 1.0, 1.0, 0.1, 1.0, 2),
        )
        path = AlignmentPath(
            "confirmed", 1.0, 0.0, 0.2, assignments, (), (1,), (), ()
        )
        diagnostics = SegmentationDiagnostics(
            component_count=1,
            seeded_component_count=1,
            artifact_component_ids=(),
            finite_separator_count=1,
            fallback_component_count=0,
            separator_component_local=True,
            assigned_pixel_fraction=1.0,
            separator_records=(),
            separator_candidate_records=({
                "rejection_reason": "low_evidence_shared_split_uses_level_2",
                "component_id": 1,
                "pair_index": 0,
                "evidence_score": 0.4,
                "component_evidence_median": 0.7,
            },),
            surface_valley_evidence_available=True,
            surface_valley_separator_count=1,
            surface_valley_separator_records=({
                "component_id": 1,
                "pair_index": 0,
                "accepted": True,
                "mean_support": 0.72,
                "coverage": 0.80,
            },),
        )
        diagnostic = _structural_evidence_diagnostics(
            path,
            [self._region(12, 40.0), self._region(11, 42.0)],
            diagnostics,
            [_track(1, 0.0)],
        )
        self.assertTrue(diagnostic["safe_physical_hypothesis_path"])
        self.assertEqual(diagnostic["weak_split_boundary_records"], [])
        self.assertEqual(
            diagnostic["surface_valley_resolved_pairs"],
            [{"component_id": 1, "pair_index": 0}],
        )

    def test_boundary_curve_cannot_create_split_without_two_crown_basins(self) -> None:
        assignments = (
            AlignmentAssignment(
                12, "split:1", "split", (1,), (-1.0, 0.0),
                -1.0, 1.0, 0.1,
            ),
            AlignmentAssignment(
                11, "split:1", "split", (1,), (1.0, 0.0),
                1.0, 1.0, 0.1,
            ),
        )
        path = AlignmentPath(
            "confirmed", 1.0, 0.0, 0.2, assignments, (), (1,), (), ()
        )
        diagnostics = replace(
            self._diagnostics(),
            surface_valley_separator_records=({
                "component_id": 1,
                "pair_index": 0,
                "first_FDI": 12,
                "second_FDI": 11,
                "accepted": True,
                "accepted_by": "multiview_normal_discontinuity",
            },),
        )
        diagnostic = _structural_evidence_diagnostics(
            path,
            [self._region(12, 40.0), self._region(11, 42.0)],
            diagnostics,
            [_track(1, 0.0)],
        )
        self.assertFalse(diagnostic["safe_physical_hypothesis_path"])
        self.assertEqual(diagnostic["unproven_split_FDI"], [12, 11])

    def test_verified_split_boundary_overrides_only_its_semantic_cost_outlier(self) -> None:
        assignments = (
            AlignmentAssignment(17, "split:1", "split", (1,), (-4.0, 0.0), -4.0, 1.0, 0.1, 1.0, 2),
            AlignmentAssignment(16, "split:1", "split", (1,), (-2.0, 0.0), -2.0, 1.0, 8.0, 1.0, 2),
            AlignmentAssignment(15, "single:2", "single", (2,), (0.0, 0.0), 0.0, 1.0, 0.1),
            AlignmentAssignment(14, "single:3", "single", (3,), (2.0, 0.0), 2.0, 1.0, 0.1),
            AlignmentAssignment(13, "single:4", "single", (4,), (4.0, 0.0), 4.0, 1.0, 0.1),
        )
        path = AlignmentPath(
            "confirmed", 1.0, 0.0, 8.4, assignments, (), (1, 2, 3, 4), (), ()
        )
        diagnostics = replace(
            self._diagnostics(),
            surface_valley_separator_records=({
            "component_id": 1,
            "pair_index": 0,
            "first_FDI": 17,
            "second_FDI": 16,
            "accepted": True,
            "accepted_by": "multiview_normal_discontinuity",
            },),
        )
        diagnostic = _structural_evidence_diagnostics(
            path,
            [self._region(fdi, 40.0) for fdi in (17, 16, 15, 14, 13)],
            diagnostics,
            [_track(1, -3.0), _track(2, 0.0), _track(3, 2.0), _track(4, 4.0)],
        )
        self.assertTrue(diagnostic["safe_physical_hypothesis_path"])
        self.assertIn(
            16,
            diagnostic["diagnostic_only_single_match_cost_outlier_FDI"],
        )

    def test_bilateral_double_area_is_semantic_geometry_conflict(self) -> None:
        assignments = (
            AlignmentAssignment(15, "single:1", "single", (1,), (-1.0, 0.0), -1.0, 1.0, 0.1),
            AlignmentAssignment(25, "single:2", "single", (2,), (1.0, 0.0), 1.0, 1.0, 0.1),
        )
        path = AlignmentPath(
            "confirmed", 1.0, 0.0, 0.2, assignments, (), (1, 2), (), ()
        )
        diagnostic = _structural_evidence_diagnostics(
            path,
            [self._region(15, 100.0), self._region(25, 45.0)],
            self._diagnostics(),
            [_track(1, -1.0), _track(2, 1.0)],
        )
        self.assertFalse(diagnostic["safe_physical_hypothesis_path"])
        self.assertTrue(any(
            item["reason"] == "region_is_bilateral_area_outlier"
            for item in diagnostic["conflicts"]
        ))

    def test_flat_persistent_core_is_reported_but_global_fdi_can_resolve_it(self) -> None:
        flat = CoreTrack(
            **{
                **_track(1, -5.0).__dict__,
                "relative_crown_height_mm": 1.0,
                "relief_quality": 0.30,
                "relative_3d_tooth_support": 0.40,
                "projection_component_area_ratio": 0.10,
            }
        )
        peers = [
            CoreTrack(
                **{
                    **_track(index, float(index)).__dict__,
                    "relative_crown_height_mm": 2.7,
                    "relief_quality": 0.53,
                }
            )
            for index in range(2, 7)
        ]
        assignment = AlignmentAssignment(
            16, "single:1", "single", (1,), (-5.0, 0.0), -5.0, 1.0, 0.1
        )
        path = AlignmentPath(
            "confirmed", 1.0, 0.0, 0.1, (assignment,), (), (1,), (), ()
        )
        diagnostic = _structural_evidence_diagnostics(
            path,
            [self._region(16, 40.0)],
            self._diagnostics(),
            [flat, *peers],
        )
        self.assertIn(16, diagnostic["low_relative_3d_crown_support_FDI"])
        self.assertTrue(diagnostic["safe_physical_hypothesis_path"])

    def test_crowded_singles_cannot_compensate_for_absorbed_crown_core(self) -> None:
        assignments = (
            AlignmentAssignment(
                13, "single:1", "single", (1,), (0.0, 0.0), 0.0, 1.0, 0.1
            ),
            AlignmentAssignment(
                12, "single:2", "single", (2,), (1.0, 0.0), 1.0, 1.0, 0.1
            ),
            AlignmentAssignment(
                27, "single:3", "single", (3,), (10.0, 0.0), 10.0, 1.0, 0.1
            ),
        )
        path = AlignmentPath(
            "confirmed",
            1.0,
            0.0,
            0.3,
            assignments,
            (4,),
            (1, 2, 3),
            (),
            ("13:single:1", "12:single:2", "27:single:3", "artifact:4"),
        )
        lr = np.linspace(-2.0, 20.0, 111)
        ap = np.linspace(-1.0, 1.0, 11)
        labels = np.zeros((len(lr), len(ap)), dtype=np.int32)
        labels[np.argmin(np.abs(lr - 18.0)), :] = 27
        diagnostic = _structural_evidence_diagnostics(
            path,
            [
                self._region(13, 35.0),
                self._region(12, 30.0),
                self._region(27, 120.0),
            ],
            self._diagnostics(),
            [_track(1, 0.0), _track(2, 1.0), _track(3, 10.0), _track(4, 18.0)],
            label_grid=labels,
            partition_maps={"lr_centres": lr, "ap_centres": ap},
        )
        self.assertFalse(diagnostic["safe_physical_hypothesis_path"])
        self.assertTrue(diagnostic["implicit_compensatory_single_topology"])
        self.assertEqual(
            diagnostic["absorbed_independent_crown_core_records"][0]["FDI"],
            27,
        )


class ComponentSegmentationV2Tests(unittest.TestCase):
    def test_separator_through_core_is_rejected_even_with_valid_midpoint(self) -> None:
        evidence = _finite_separator_core_clearance(
            first_endpoint_lr_ap_mm=(-6.0, 0.0),
            second_endpoint_lr_ap_mm=(6.0, 0.0),
            first_center_lr_ap_mm=(-5.0, 0.0),
            second_center_lr_ap_mm=(5.0, 0.0),
            maximum_centre_offset_mm=3.5,
        )
        self.assertTrue(evidence["within_midpoint_corridor"])
        self.assertFalse(evidence["clears_both_cores"])
        self.assertFalse(evidence["accepted"])

    def test_intercore_separator_clears_both_protected_cores(self) -> None:
        evidence = _finite_separator_core_clearance(
            first_endpoint_lr_ap_mm=(0.0, -4.0),
            second_endpoint_lr_ap_mm=(0.0, 4.0),
            first_center_lr_ap_mm=(-5.0, 0.0),
            second_center_lr_ap_mm=(5.0, 0.0),
            maximum_centre_offset_mm=3.5,
        )
        self.assertTrue(evidence["within_midpoint_corridor"])
        self.assertTrue(evidence["clears_both_cores"])
        self.assertTrue(evidence["accepted"])
    def _maps(self, connected: bool = False) -> dict[str, object]:
        lr = np.linspace(-10.0, 10.0, 101)
        ap = np.linspace(-6.0, 6.0, 61)
        x, y = np.meshgrid(lr, ap, indexing="ij")
        mask = ((x + 4.0) ** 2 + y**2 <= 3.0**2) | (
            (x - 4.0) ** 2 + y**2 <= 3.0**2
        )
        if connected:
            mask |= (np.abs(x) <= 4.0) & (np.abs(y) <= 0.5)
        height = np.where(mask, 2.0 + 0.1 * x, np.nan)
        normals = np.zeros(mask.shape + (3,))
        normals[..., 2] = 1.0
        edge = np.zeros(mask.shape)
        edge[np.abs(y) <= 0.5] = 0.9
        return {
            "resolution_mm": float(lr[1] - lr[0]),
            "lr_centres": lr,
            "ap_centres": ap,
            "silhouette": mask,
            "top_height_mm": height,
            "top_normal_lr_ap_occ": normals,
            "normal_rgb": 0.5 * (normals + 1.0),
            "fused_edge": edge,
        }

    def _alignment(self) -> AlignmentPath:
        assignments = (
            AlignmentAssignment(12, "single:1", "single", (1,), (-4.0, 0.0), -4.0, 1.0, 0.0),
            AlignmentAssignment(21, "single:2", "single", (2,), (4.0, 0.0), 4.0, 1.0, 0.0),
        )
        return AlignmentPath(
            "confirmed", 1.0, 0.0, 0.0, assignments, (), (1, 2), (),
            ("12:single:1", "21:single:2"),
        )

    def test_disconnected_components_are_assigned_without_global_separator(self) -> None:
        regions, diagnostics, labels = segment_component_local_regions(
            alignment=self._alignment(), frame=_frame(), maps=self._maps(False)
        )
        self.assertEqual([item.fdi for item in regions], [12, 21])
        self.assertEqual(diagnostics.finite_separator_count, 0)
        self.assertTrue(diagnostics.separator_component_local)
        self.assertTrue(all(
            item.boundary_method == "connected_component_membership"
            for item in regions
        ))
        self.assertGreater(np.count_nonzero(labels), 0)

    def test_connected_component_is_partitioned_locally(self) -> None:
        regions, diagnostics, _ = segment_component_local_regions(
            alignment=self._alignment(), frame=_frame(), maps=self._maps(True)
        )
        self.assertEqual(len(regions), 2)
        self.assertTrue(diagnostics.separator_component_local)
        self.assertTrue(all(item.pixel_count > 0 for item in regions))

    def test_boundary_first_mode_does_not_promote_midpoint_to_anatomy(self) -> None:
        lr = np.linspace(-5.0, 5.0, 101)
        ap = np.linspace(-3.0, 3.0, 61)
        mask = np.ones((len(lr), len(ap)), dtype=bool)
        normals = np.zeros(mask.shape + (3,))
        normals[..., 2] = 1.0
        maps = {
            "resolution_mm": float(lr[1] - lr[0]),
            "lr_centres": lr,
            "ap_centres": ap,
            "silhouette": mask,
            "top_height_mm": np.ones(mask.shape),
            "top_normal_lr_ap_occ": normals,
            "normal_rgb": 0.5 * (normals + 1.0),
            "fused_edge": np.zeros(mask.shape),
        }
        regions, diagnostics, _ = segment_component_local_regions(
            alignment=self._alignment(),
            frame=_frame(),
            maps=maps,
            boundary_first_segmentation=True,
        )
        self.assertEqual(len(regions), 2)
        self.assertTrue(diagnostics.midpoint_fallback_disabled)
        self.assertEqual(len(diagnostics.unsupported_separator_records), 1)
        self.assertFalse(
            diagnostics.unsupported_separator_records[0][
                "formal_midpoint_barrier_created"
            ]
        )
        self.assertTrue(all(
            item.boundary_confidence == 0.0 for item in regions
        ))

    def test_adjacent_separator_endpoint_reuse_is_topology_conflict(self) -> None:
        def chord(pair_index: int, first, second, crown_support: float):
            return SimpleNamespace(
                pair_index=pair_index,
                first_endpoint_lr_ap_mm=first,
                second_endpoint_lr_ap_mm=second,
                paired_concavity_crown_support=crown_support,
                paired_concavity_score=0.6,
                evidence_score=0.6,
            )

        first = chord(0, (-2.0, -2.0), (-2.0, 2.0), 0.8)
        second = chord(1, (-2.0, 2.0), (2.0, 2.0), 0.2)
        accepted, records = _remove_endpoint_collisions(
            [first, second], resolution=0.1, component_id=1
        )
        self.assertEqual([item.pair_index for item in accepted], [0])
        self.assertEqual(len(records), 1)
        self.assertEqual(
            records[0]["reason"],
            "adjacent_separators_reuse_component_boundary_endpoint",
        )

    def test_multiview_evidence_refines_boundaries_without_changing_FDI_set(self) -> None:
        maps = self._maps(True)
        lr = np.asarray(maps["lr_centres"])
        ap = np.asarray(maps["ap_centres"])
        x, _ = np.meshgrid(lr, ap, indexing="ij")
        maps["multi_view_boundary_score"] = np.exp(
            -0.5 * ((x - 0.35) / 0.25) ** 2
        )
        maps["multi_view_consistency"] = np.ones_like(x)
        regions, diagnostics, labels = segment_component_local_regions(
            alignment=self._alignment(),
            frame=_frame(),
            maps=maps,
            multi_view_watershed_weight=0.12,
        )
        self.assertEqual([item.fdi for item in regions], [12, 21])
        self.assertEqual(set(np.unique(labels)) - {0}, {1, 2})
        self.assertTrue(diagnostics.multi_view_boundary_evidence_available)
        self.assertTrue(diagnostics.multi_view_boundary_fused_into_watershed)

    def test_locally_dominant_multiview_boundary_can_replace_weak_valley(self) -> None:
        maps = self._maps(True)
        lr = np.asarray(maps["lr_centres"])
        ap = np.asarray(maps["ap_centres"])
        x, _ = np.meshgrid(lr, ap, indexing="ij")
        maps["surface_valley_score"] = np.where(
            np.asarray(maps["silhouette"]), 0.10, 0.0
        )
        maps["multi_view_boundary_score"] = np.exp(
            -0.5 * (x / 0.25) ** 2
        )
        maps["multi_view_consistency"] = np.ones_like(x)
        records = [
            {
                "component_id": 1,
                "assignment": self._alignment().assignments[0],
            },
            {
                "component_id": 1,
                "assignment": self._alignment().assignments[1],
            },
        ]
        barrier, record = _surface_valley_barrier(
            records=records,
            pair_index=0,
            maps=maps,
            frame=_frame(),
            component=np.asarray(maps["silhouette"], dtype=bool),
            minimum_mean_support=0.36,
            minimum_coverage=0.32,
        )
        self.assertTrue(record["accepted"])
        self.assertGreater(np.count_nonzero(barrier), 0)
        self.assertEqual(record["accepted_by"], "multiview_normal_discontinuity")
        self.assertTrue(
            record["multi_view_used_as_independent_boundary_hypothesis"]
        )
        self.assertGreater(record["multi_view_mean_support"], 0.0)

    def test_uniform_multiview_response_is_not_an_anatomical_boundary(self) -> None:
        maps = self._maps(True)
        maps["surface_valley_score"] = np.where(
            np.asarray(maps["silhouette"]), 0.10, 0.0
        )
        maps["multi_view_boundary_score"] = np.where(
            np.asarray(maps["silhouette"]), 0.75, 0.0
        )
        maps["multi_view_consistency"] = np.ones_like(
            maps["multi_view_boundary_score"]
        )
        records = [
            {
                "component_id": 1,
                "assignment": self._alignment().assignments[0],
            },
            {
                "component_id": 1,
                "assignment": self._alignment().assignments[1],
            },
        ]
        barrier, record = _surface_valley_barrier(
            records=records,
            pair_index=0,
            maps=maps,
            frame=_frame(),
            component=np.asarray(maps["silhouette"], dtype=bool),
            minimum_mean_support=0.36,
            minimum_coverage=0.32,
        )
        self.assertFalse(record["accepted"])
        self.assertEqual(np.count_nonzero(barrier), 0)

    def test_projected_surface_valley_can_form_finite_local_separator(self) -> None:
        maps = self._maps(True)
        lr = np.asarray(maps["lr_centres"])
        ap = np.asarray(maps["ap_centres"])
        x, _ = np.meshgrid(lr, ap, indexing="ij")
        maps["surface_valley_score"] = np.exp(-0.5 * (x / 0.35) ** 2)
        records = [
            {
                "component_id": 1,
                "assignment": self._alignment().assignments[0],
            },
            {
                "component_id": 1,
                "assignment": self._alignment().assignments[1],
            },
        ]
        barrier, record = _surface_valley_barrier(
            records=records,
            pair_index=0,
            maps=maps,
            frame=_frame(),
            component=np.asarray(maps["silhouette"], dtype=bool),
            minimum_mean_support=0.36,
            minimum_coverage=0.32,
        )
        self.assertTrue(record["accepted"])
        self.assertGreater(record["mean_support"], 0.50)
        self.assertGreater(np.count_nonzero(barrier), 10)

    def test_partition_scale_rejects_tiny_seed_component(self) -> None:
        lower = self._maps(False)
        higher = self._maps(False)
        lr = np.asarray(higher["lr_centres"])
        ap = np.asarray(higher["ap_centres"])
        x, y = np.meshgrid(lr, ap, indexing="ij")
        higher_mask = ((x + 4.0) ** 2 + y**2 <= 3.0**2) | (
            (x - 4.0) ** 2 + y**2 <= 0.45**2
        )
        higher["silhouette"] = higher_mask
        quantile, _ = choose_partition_map(
            {0.50: lower, 0.55: higher}, self._alignment().assignments
        )
        self.assertEqual(quantile, 0.50)

    def test_low_relief_boundary_tail_can_remain_unassigned(self) -> None:
        maps = self._maps(True)
        lr = np.asarray(maps["lr_centres"])
        ap = np.asarray(maps["ap_centres"])
        x, y = np.meshgrid(lr, ap, indexing="ij")
        tail = ((np.abs(x) <= 0.65) & (y >= 0.0) & (y <= 5.0)) | (
            x**2 + (y - 5.0) ** 2 <= 1.4**2
        )
        maps["silhouette"] = np.asarray(maps["silhouette"]) | tail
        relief = np.where(maps["silhouette"], 2.0, np.nan)
        score = np.where(maps["silhouette"], 0.8, 0.0)
        relief[tail] = 0.05
        score[tail] = 0.0
        maps["relative_crown_relief_mm"] = relief
        maps["relative_crown_relief_score"] = score
        regions, diagnostics, labels = segment_component_local_regions(
            alignment=self._alignment(),
            frame=_frame(),
            maps=maps,
            unassigned_seed_protection_scale=0.55,
            minimum_unassigned_area_mm2=0.50,
        )
        self.assertEqual([item.fdi for item in regions], [12, 21])
        self.assertGreater(diagnostics.unassigned_pixel_count, 0)
        self.assertGreater(np.count_nonzero(maps["silhouette"] & (labels == 0)), 0)
        for center in ((-4.0, 0.0), (4.0, 0.0)):
            row = int(np.argmin(np.abs(lr - center[0])))
            column = int(np.argmin(np.abs(ap - center[1])))
            self.assertGreater(int(labels[row, column]), 0)

    def test_default_protection_does_not_blanket_near_seed_gingiva(self) -> None:
        maps = self._maps(True)
        lr = np.asarray(maps["lr_centres"])
        ap = np.asarray(maps["ap_centres"])
        x, y = np.meshgrid(lr, ap, indexing="ij")
        gingival_tail = (
            (np.abs(x + 4.0) <= 0.60)
            & (y >= 2.80)
            & (y <= 5.60)
        )
        maps["silhouette"] = np.asarray(maps["silhouette"]) | gingival_tail
        relief = np.where(maps["silhouette"], 2.0, np.nan)
        score = np.where(maps["silhouette"], 0.8, 0.0)
        relief[gingival_tail] = 0.03
        score[gingival_tail] = 0.0
        maps["relative_crown_relief_mm"] = relief
        maps["relative_crown_relief_score"] = score

        _, diagnostics, labels = segment_component_local_regions(
            alignment=self._alignment(),
            frame=_frame(),
            maps=maps,
            minimum_unassigned_area_mm2=0.50,
        )

        self.assertGreater(diagnostics.unassigned_pixel_count, 0)
        self.assertTrue(np.any(gingival_tail & (labels == 0)))
        for center in ((-4.0, 0.0), (4.0, 0.0)):
            row = int(np.argmin(np.abs(lr - center[0])))
            column = int(np.argmin(np.abs(ap - center[1])))
            self.assertGreater(int(labels[row, column]), 0)


if __name__ == "__main__":
    unittest.main()

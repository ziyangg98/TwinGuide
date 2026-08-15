import math
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import bpy

from twin_guide.blender.booleans import (
    apply_boolean,
    apply_manifold3d_difference,
    apply_manifold3d_differences,
)
from twin_guide.blender.mesh_builders import (
    create_axis_cylinder,
    create_centerline_tube,
    voxel_union,
)
from twin_guide.blender.mesh_queries import (
    build_bvh,
    duplicate_triangle_count,
    mesh_component_vertex_counts,
    nearest_mesh_distance,
    nearest_mesh_surface_side,
    point_inside_mesh,
    remove_excess_components,
    topology_edge_counts,
)
from twin_guide.blender.scene import clear_scene, set_active_object
from twin_guide.blender.sleeve_estimation_adapter import mesh_object_to_triangle_data
from twin_guide.blender.sleeve_reconstruction import (
    create_closed_sleeve_object,
    validate_sleeve_boolean_parameters,
)
from twin_guide.errors import GeometryError
from twin_guide.geometry import Vec3
from twin_guide.guide_validation import (
    _point_is_outside_dental_trim,
    _point_is_outside_guide_bores,
    _point_is_retained,
)
from twin_guide.models import GuideSleeve, TemplateFrame
from twin_guide.sleeve_anchors import SleeveAnchorSelectionConfig, select_sleeve_anchors
from twin_guide.sleeve_estimation.mesh_integrity import inspect_triangle_mesh
from twin_guide.sleeve_estimation.types import SleeveEstimate
from twin_guide.types import SleeveGenerationResult


class BlenderBackendTests(unittest.TestCase):
    def setUp(self):
        clear_scene()

    def test_closed_primitive_has_valid_topology(self):
        cylinder_mesh = create_axis_cylinder(
            "test_cylinder", Vec3(0.0, 0.0, 0.0), Vec3(0.0, 0.0, 5.0), 1.0
        )

        self.assertEqual(topology_edge_counts(cylinder_mesh), (0, 0))

    def test_continuous_centerline_sweep_is_closed_and_manifold(self):
        """平行输运扫掠的弯曲梁应保持封闭、流形且无重复三角形。"""

        beam = create_centerline_tube(
            "continuous_beam",
            (
                Vec3(-5.0, 0.0, 0.0),
                Vec3(-2.5, 1.0, 1.0),
                Vec3(0.0, 1.5, -1.0),
                Vec3(2.5, 1.0, 1.0),
                Vec3(5.0, 0.0, 0.0),
            ),
            2.3,
            ring_segments=32,
        )

        self.assertEqual(topology_edge_counts(beam), (0, 0))
        self.assertEqual(len(mesh_component_vertex_counts(beam)), 1)
        self.assertEqual(duplicate_triangle_count(beam), 0)

    def test_sleeve_reconstruction_rejects_zero_width_slot_inputs(self):
        estimate = SleeveEstimate(
            axis_origin=Vec3(0.0, 0.0, 0.0),
            axis=Vec3(0.0, 0.0, 1.0),
            c_opening_direction=Vec3(1.0, 0.0, 0.0),
            height=8.0,
            platform_height=2.8,
            closed_bore_height=1.4,
            platform_slot_width=0.0,
            inner_radius=1.2,
            outer_radius=2.0,
            inner_arc_angle=1.5 * math.pi,
            outer_arc_angle=1.5 * math.pi,
        )
        with self.assertRaises(GeometryError):
            validate_sleeve_boolean_parameters(estimate)

    def test_sleeve_reconstruction_rejects_unsupported_inner_arc(self):
        for angle_degrees in (179.99, 350.01):
            estimate = SleeveEstimate(
                axis_origin=Vec3(0.0, 0.0, 0.0),
                axis=Vec3(0.0, 0.0, 1.0),
                c_opening_direction=Vec3(1.0, 0.0, 0.0),
                height=8.0,
                platform_height=2.8,
                closed_bore_height=1.4,
                platform_slot_width=1.6,
                inner_radius=1.025,
                outer_radius=2.55,
                inner_arc_angle=math.radians(angle_degrees),
                outer_arc_angle=math.radians(246.59),
            )
            with (
                self.subTest(angle_degrees=angle_degrees),
                self.assertRaisesRegex(GeometryError, "180 与 350"),
            ):
                validate_sleeve_boolean_parameters(estimate)

    def test_reconstructed_sleeve_is_valid_at_maximum_inner_arc(self):
        estimate = SleeveEstimate(
            axis_origin=Vec3(0.0, 0.0, 0.0),
            axis=Vec3(0.0, 0.0, 1.0),
            c_opening_direction=Vec3(1.0, 0.0, 0.0),
            height=15.5,
            platform_height=10.0,
            closed_bore_height=4.9,
            platform_slot_width=1.6,
            platform_overhang=0.2,
            inner_radius=1.025,
            outer_radius=2.55,
            inner_arc_angle=math.radians(350.0),
            outer_arc_angle=math.radians(246.59),
        )

        sleeve = create_closed_sleeve_object(estimate, "maximum_inner_arc_sleeve")
        integrity = inspect_triangle_mesh(mesh_object_to_triangle_data(sleeve))

        self.assertTrue(integrity.valid, integrity)

    def test_reconstructed_sleeve_is_closed_and_manifold(self):
        estimate = SleeveEstimate(
            axis_origin=Vec3(0.0, 0.0, 0.0),
            axis=Vec3(0.0, 0.0, 1.0),
            c_opening_direction=Vec3(1.0, 0.0, 0.0),
            height=8.0,
            platform_height=2.8,
            closed_bore_height=1.4,
            platform_slot_width=2.4,
            inner_radius=1.2,
            outer_radius=2.0,
            inner_arc_angle=1.5 * math.pi,
            outer_arc_angle=4.0,
        )
        sleeve = create_closed_sleeve_object(estimate, "validated_sleeve")
        self.assertEqual(topology_edge_counts(sleeve), (0, 0))
        self.assertEqual(len(mesh_component_vertex_counts(sleeve)), 1)
        self.assertEqual(duplicate_triangle_count(sleeve), 0)
        triangle_data = mesh_object_to_triangle_data(sleeve)
        axial = tuple(
            (point - estimate.axis_origin).dot(estimate.axis) for point in triangle_data.vertices
        )
        self.assertAlmostEqual(min(axial), 0.0, delta=1e-6)
        self.assertAlmostEqual(max(axial), estimate.height, delta=1e-6)

    def test_reconstructed_sleeve_accepts_an_edited_total_height(self):
        estimate = SleeveEstimate(
            axis_origin=Vec3(-6.3477875194, 14.5231135498, 5.7193765152),
            axis=Vec3(-0.4426520149, 0.0039038047, -0.8966849804),
            c_opening_direction=Vec3(-0.8929016718, -0.09378444, 0.4403760703),
            height=16.5430000408,
            platform_height=9.8750001702,
            closed_bore_height=4.7770001507,
            platform_slot_width=0.85,
            inner_radius=1.05,
            outer_radius=2.15,
            inner_arc_angle=4.6239706005,
            outer_arc_angle=3.6945827738,
        )

        sleeve = create_closed_sleeve_object(estimate, "edited_height_sleeve")
        integrity = inspect_triangle_mesh(mesh_object_to_triangle_data(sleeve))

        self.assertTrue(integrity.valid, integrity)

    def test_reconstructed_sleeve_has_configured_top_annular_recess(self):
        estimate = SleeveEstimate(
            axis_origin=Vec3(0.0, 0.0, 0.0),
            axis=Vec3(0.0, 0.0, 1.0),
            c_opening_direction=Vec3(1.0, 0.0, 0.0),
            height=15.5,
            platform_height=10.0,
            closed_bore_height=4.9,
            inner_radius=1.025,
            outer_radius=2.55,
            inner_arc_angle=math.radians(257.83),
            outer_arc_angle=math.radians(246.59),
            top_recess_radius=1.305,
            top_recess_depth=0.30,
            platform_slot_width=1.60,
            platform_overhang=0.20,
        )

        sleeve = create_closed_sleeve_object(estimate, "top_recess_sleeve")
        tree = build_bvh(sleeve)

        self.assertFalse(point_inside_mesh(tree, Vec3(-1.13, 0.0, 0.15)))
        self.assertTrue(point_inside_mesh(tree, Vec3(-1.20, 0.0, 0.15)))
        self.assertTrue(point_inside_mesh(tree, Vec3(-1.16, 0.0, 0.45)))
        self.assertFalse(point_inside_mesh(tree, Vec3(-0.80, 0.0, 0.45)))
        self.assertFalse(point_inside_mesh(tree, Vec3(1.10, 0.0, 0.45)))
        self.assertFalse(point_inside_mesh(tree, Vec3(0.90, 0.70, 0.45)))
        self.assertFalse(point_inside_mesh(tree, Vec3(1.45, 1.50, 0.45)))
        self.assertTrue(point_inside_mesh(tree, Vec3(1.35, 1.50, 0.45)))
        self.assertFalse(point_inside_mesh(tree, Vec3(1.07, 0.70, 8.00)))
        self.assertTrue(point_inside_mesh(tree, Vec3(1.07, 0.77, 8.00)))
        self.assertTrue(point_inside_mesh(tree, Vec3(2.50, 2.40, 15.30)))
        self.assertFalse(point_inside_mesh(tree, Vec3(2.80, 2.40, 15.30)))
        self.assertTrue(point_inside_mesh(tree, Vec3(-2.45, 0.0, 15.30)))
        self.assertEqual(topology_edge_counts(sleeve), (0, 0))
        self.assertEqual(len(mesh_component_vertex_counts(sleeve)), 1)
        triangle_data = mesh_object_to_triangle_data(sleeve)
        self.assertAlmostEqual(max(point.x for point in triangle_data.vertices), 2.75, delta=1e-6)

    def test_generated_sleeve_parameters_drive_q_and_wall_thickness(self):
        estimates = tuple(
            SleeveEstimate(
                axis_origin=Vec3(x, 0.0, 0.0),
                axis=Vec3(0.0, 0.0, 1.0),
                c_opening_direction=Vec3(1.0 if x < 0.0 else -1.0, 0.0, 0.0),
                height=16.0,
                platform_height=6.0,
                closed_bore_height=4.0,
                platform_slot_width=1.0,
                inner_radius=1.0,
                outer_radius=2.5,
                inner_arc_angle=math.radians(264.934),
                outer_arc_angle=math.radians(211.684),
            )
            for x in (-5.0, 5.0)
        )
        sleeves = tuple(
            GuideSleeve(
                index,
                create_closed_sleeve_object(estimate, f"generated_sleeve_{index}"),
                estimate,
                0.0,
                estimate.height,
            )
            for index, estimate in enumerate(estimates, 1)
        )
        frame = TemplateFrame(
            Vec3(0.0, 0.0, 0.0),
            Vec3(1.0, 0.0, 0.0),
            Vec3(0.0, 1.0, 0.0),
            Vec3(0.0, 0.0, 1.0),
        )

        plan = select_sleeve_anchors(
            SimpleNamespace(guide_sleeves=sleeves),
            SleeveGenerationResult(sleeves, frame),
            SleeveAnchorSelectionConfig(connector_radius_mm=2.3),
        )

        for sleeve, selection in zip(sleeves, plan.selections, strict=True):
            sleeve_bvh = build_bvh(sleeve.guide_mesh)
            for anchor in (selection.lower, selection.upper):
                self.assertLess(nearest_mesh_distance(sleeve_bvh, anchor.surface_contact), 1e-5)
                self.assertGreater(anchor.surface_normal.dot(selection.radial_direction), 0.99)
                self.assertAlmostEqual(anchor.local_wall_thickness_mm, 1.5, delta=0.02)

    def test_point_inside_uses_stable_multi_direction_vote(self):
        cylinder_mesh = create_axis_cylinder(
            "solid", Vec3(0.0, 0.0, -2.0), Vec3(0.0, 0.0, 2.0), 1.0
        )
        cylinder_bvh = build_bvh(cylinder_mesh)

        self.assertTrue(point_inside_mesh(cylinder_bvh, Vec3(0.0, 0.0, 0.0)))
        self.assertFalse(point_inside_mesh(cylinder_bvh, Vec3(3.0, 0.0, 0.0)))
        self.assertLess(nearest_mesh_surface_side(cylinder_bvh, Vec3(0.0, 0.0, 0.0)), 0.0)
        self.assertGreater(nearest_mesh_surface_side(cylinder_bvh, Vec3(3.0, 0.0, 0.0)), 0.0)

    def test_retention_accepts_contained_and_near_surface_points(self):
        model = create_axis_cylinder(
            "retention_model", Vec3(0.0, 0.0, -2.0), Vec3(0.0, 0.0, 2.0), 2.0
        )
        model_bvh = build_bvh(model)

        self.assertTrue(_point_is_retained(model_bvh, Vec3(0.0, 0.0, 0.0), 0.4))
        self.assertTrue(_point_is_retained(model_bvh, Vec3(2.2, 0.0, 0.0), 0.4))
        self.assertFalse(_point_is_retained(model_bvh, Vec3(3.0, 0.0, 0.0), 0.4))

    def test_connector_validation_excludes_every_associated_guide_bore(self):
        parameters = SleeveEstimate(
            axis_origin=Vec3(0.0, 0.0, 0.0),
            axis=Vec3(0.0, 0.0, 1.0),
            c_opening_direction=Vec3(1.0, 0.0, 0.0),
            height=8.0,
            platform_height=2.8,
            closed_bore_height=1.4,
            platform_slot_width=2.4,
            inner_radius=1.2,
            outer_radius=2.0,
            inner_arc_angle=1.5 * math.pi,
            outer_arc_angle=4.0,
        )
        guides = tuple(
            GuideSleeve(
                index,
                None,
                SleeveEstimate(
                    axis_origin=Vec3(x, 0.0, 0.0),
                    axis=parameters.axis,
                    c_opening_direction=parameters.c_opening_direction,
                    height=parameters.height,
                    platform_height=parameters.platform_height,
                    closed_bore_height=parameters.closed_bore_height,
                    platform_slot_width=parameters.platform_slot_width,
                    inner_radius=parameters.inner_radius,
                    outer_radius=parameters.outer_radius,
                    inner_arc_angle=parameters.inner_arc_angle,
                    outer_arc_angle=parameters.outer_arc_angle,
                ),
                0.0,
                parameters.height,
            )
            for index, x in enumerate((-5.0, 5.0), 1)
        )

        self.assertFalse(_point_is_outside_guide_bores(Vec3(-5.0, 0.0, 4.0), guides))
        self.assertFalse(_point_is_outside_guide_bores(Vec3(5.0, 0.0, 4.0), guides))
        self.assertTrue(_point_is_outside_guide_bores(Vec3(0.0, 0.0, 4.0), guides))

    def test_connector_validation_excludes_planned_dental_trim(self):
        dentition = create_axis_cylinder(
            "dentition",
            Vec3(0.0, 0.0, -2.0),
            Vec3(0.0, 0.0, 2.0),
            1.0,
        )
        dentition_bvh = build_bvh(dentition)

        self.assertFalse(
            _point_is_outside_dental_trim(
                dentition_bvh,
                Vec3(0.0, 0.0, 0.0),
                0.2,
            )
        )
        self.assertFalse(
            _point_is_outside_dental_trim(
                dentition_bvh,
                Vec3(1.1, 0.0, 0.0),
                0.2,
            )
        )
        self.assertTrue(
            _point_is_outside_dental_trim(
                dentition_bvh,
                Vec3(1.3, 0.0, 0.0),
                0.2,
            )
        )

    def test_voxel_union_preserves_input_objects(self):
        first_cylinder_mesh = create_axis_cylinder(
            "first", Vec3(0.0, 0.0, -1.0), Vec3(0.0, 0.0, 1.0), 1.0
        )
        second_cylinder_mesh = create_axis_cylinder(
            "second", Vec3(0.5, 0.0, -1.0), Vec3(0.5, 0.0, 1.0), 1.0
        )
        source_vertex_counts = (
            len(first_cylinder_mesh.data.vertices),
            len(second_cylinder_mesh.data.vertices),
        )

        union_mesh = voxel_union((first_cylinder_mesh, second_cylinder_mesh), "union", 0.2)

        self.assertIn(first_cylinder_mesh, bpy.data.objects.values())
        self.assertIn(second_cylinder_mesh, bpy.data.objects.values())
        self.assertEqual(
            source_vertex_counts,
            (
                len(first_cylinder_mesh.data.vertices),
                len(second_cylinder_mesh.data.vertices),
            ),
        )
        self.assertGreater(len(union_mesh.data.vertices), 0)

    def test_voxel_union_accepts_one_mesh_without_joining(self):
        source = create_axis_cylinder("single", Vec3(0.0, 0.0, -1.0), Vec3(0.0, 0.0, 1.0), 1.0)

        with patch("bpy.ops.object.join") as join:
            union_mesh = voxel_union((source,), "single_union", 0.2)

        join.assert_not_called()
        self.assertGreater(len(union_mesh.data.vertices), 0)

    def test_excess_component_cleanup_preserves_largest_mesh(self):
        main_mesh = create_axis_cylinder("main", Vec3(0.0, 0.0, -2.0), Vec3(0.0, 0.0, 2.0), 1.0)
        bpy.ops.mesh.primitive_cube_add(size=0.1, location=(5.0, 0.0, 0.0))
        artifact_mesh = bpy.context.object
        set_active_object(main_mesh)
        artifact_mesh.select_set(True)
        bpy.ops.object.join()

        self.assertEqual(len(mesh_component_vertex_counts(main_mesh)), 2)

        remove_excess_components(main_mesh, 1)

        self.assertEqual(len(mesh_component_vertex_counts(main_mesh)), 1)

    def test_boolean_preserves_cutter(self):
        target_mesh = create_axis_cylinder("target", Vec3(0.0, 0.0, -2.0), Vec3(0.0, 0.0, 2.0), 2.0)
        cutter_mesh = create_axis_cylinder("cutter", Vec3(0.0, 0.0, -3.0), Vec3(0.0, 0.0, 3.0), 0.5)

        difference_mesh = apply_boolean(target_mesh, cutter_mesh, "DIFFERENCE")

        self.assertIn(cutter_mesh, bpy.data.objects.values())
        self.assertEqual(topology_edge_counts(difference_mesh), (0, 0))

    def test_manifold3d_difference_returns_closed_mesh_and_preserves_cutter(self):
        target_mesh = create_axis_cylinder("target", Vec3(0.0, 0.0, -2.0), Vec3(0.0, 0.0, 2.0), 2.0)
        cutter_mesh = create_axis_cylinder("cutter", Vec3(0.0, 0.0, -3.0), Vec3(0.0, 0.0, 3.0), 0.5)

        difference_mesh = apply_manifold3d_difference(target_mesh, cutter_mesh)

        self.assertIn(cutter_mesh, bpy.data.objects.values())
        self.assertEqual(topology_edge_counts(difference_mesh), (0, 0))
        self.assertEqual(len(mesh_component_vertex_counts(difference_mesh)), 1)
        self.assertEqual(duplicate_triangle_count(difference_mesh), 0)

    def test_manifold3d_multiple_differences_convert_only_final_mesh(self):
        target_mesh = create_axis_cylinder("target", Vec3(0.0, 0.0, -2.0), Vec3(0.0, 0.0, 2.0), 3.0)
        first_cutter = create_axis_cylinder(
            "first_cutter", Vec3(-1.0, 0.0, -3.0), Vec3(-1.0, 0.0, 3.0), 0.5
        )
        second_cutter = create_axis_cylinder(
            "second_cutter", Vec3(1.0, 0.0, -3.0), Vec3(1.0, 0.0, 3.0), 0.5
        )

        difference_mesh = apply_manifold3d_differences(target_mesh, (first_cutter, second_cutter))

        self.assertIn(first_cutter, bpy.data.objects.values())
        self.assertIn(second_cutter, bpy.data.objects.values())
        self.assertEqual(topology_edge_counts(difference_mesh), (0, 0))
        self.assertEqual(len(mesh_component_vertex_counts(difference_mesh)), 1)

    def test_detects_geometrically_duplicate_triangles(self):
        mesh = bpy.data.meshes.new("duplicates")
        mesh.from_pydata(
            [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 0), (1, 0, 0), (0, 1, 0)],
            [],
            [(0, 1, 2), (3, 4, 5)],
        )
        mesh_object = bpy.data.objects.new("duplicates", mesh)
        bpy.context.collection.objects.link(mesh_object)

        self.assertEqual(duplicate_triangle_count(mesh_object), 1)


if __name__ == "__main__":
    unittest.main()

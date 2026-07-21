import math
import unittest

import bpy

from twin_guide.blender.booleans import apply_boolean
from twin_guide.blender.mesh_builders import (
    create_axis_cylinder,
    voxel_union,
)
from twin_guide.blender.mesh_queries import (
    build_bvh,
    duplicate_triangle_count,
    mesh_component_vertex_counts,
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
from twin_guide.guide_validation import _point_is_retained
from twin_guide.sleeve_estimation.types import SleeveEstimate


class BlenderBackendTests(unittest.TestCase):
    def setUp(self):
        clear_scene()

    def test_closed_primitive_has_valid_topology(self):
        cylinder_mesh = create_axis_cylinder(
            "test_cylinder", Vec3(0.0, 0.0, 0.0), Vec3(0.0, 0.0, 5.0), 1.0
        )

        self.assertEqual(topology_edge_counts(cylinder_mesh), (0, 0))

    def test_sleeve_reconstruction_rejects_zero_width_slot_inputs(self):
        estimate = SleeveEstimate(
            axis_origin=Vec3(0.0, 0.0, 0.0),
            axis=Vec3(0.0, 0.0, 1.0),
            c_opening_direction=Vec3(1.0, 0.0, 0.0),
            height=8.0,
            platform_height=2.8,
            closed_bore_height=1.4,
            platform_width=0.0,
            inner_radius=1.2,
            outer_radius=2.0,
            inner_arc_angle=1.5 * math.pi,
            outer_arc_angle=1.5 * math.pi,
        )
        with self.assertRaises(GeometryError):
            validate_sleeve_boolean_parameters(estimate)

    def test_reconstructed_sleeve_is_closed_and_manifold(self):
        estimate = SleeveEstimate(
            axis_origin=Vec3(0.0, 0.0, 0.0),
            axis=Vec3(0.0, 0.0, 1.0),
            c_opening_direction=Vec3(1.0, 0.0, 0.0),
            height=8.0,
            platform_height=2.8,
            closed_bore_height=1.4,
            platform_width=0.8,
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

    def test_point_inside_uses_stable_multi_direction_vote(self):
        cylinder_mesh = create_axis_cylinder(
            "solid", Vec3(0.0, 0.0, -2.0), Vec3(0.0, 0.0, 2.0), 1.0
        )
        cylinder_bvh = build_bvh(cylinder_mesh)

        self.assertTrue(point_inside_mesh(cylinder_bvh, Vec3(0.0, 0.0, 0.0)))
        self.assertFalse(point_inside_mesh(cylinder_bvh, Vec3(3.0, 0.0, 0.0)))

    def test_retention_accepts_contained_and_near_surface_points(self):
        model = create_axis_cylinder(
            "retention_model", Vec3(0.0, 0.0, -2.0), Vec3(0.0, 0.0, 2.0), 2.0
        )
        model_bvh = build_bvh(model)

        self.assertTrue(_point_is_retained(model_bvh, Vec3(0.0, 0.0, 0.0), 0.4))
        self.assertTrue(_point_is_retained(model_bvh, Vec3(2.2, 0.0, 0.0), 0.4))
        self.assertFalse(_point_is_retained(model_bvh, Vec3(3.0, 0.0, 0.0), 0.4))

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

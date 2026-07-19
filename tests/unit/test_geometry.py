import math
import unittest

from twin_guide.errors import GeometryError
from twin_guide.geometry import (
    Vec3,
    periodic_catmull_rom,
    point_axis_coordinates,
    principal_axis,
    principal_plane_normal,
    volume_centroid,
)
from twin_guide.models import TemplateFrame


class GeometryTests(unittest.TestCase):
    def test_principal_directions_follow_rotated_geometry(self):
        line_direction = Vec3(1.0, 2.0, 3.0).normalized()
        line_points = tuple(line_direction * float(index) for index in range(-8, 9))
        self.assertAlmostEqual(abs(principal_axis(line_points).dot(line_direction)), 1.0)

        normal = Vec3(1.0, -2.0, 2.0).normalized()
        tangent = normal.cross(Vec3(0.0, 0.0, 1.0)).normalized()
        bitangent = normal.cross(tangent).normalized()
        plane_points = tuple(
            tangent * float(row) + bitangent * float(column)
            for row in range(-4, 5)
            for column in range(-3, 4)
        )
        self.assertAlmostEqual(abs(principal_plane_normal(plane_points).dot(normal)), 1.0)

    def test_principal_direction_rejects_degenerate_cloud(self):
        with self.assertRaises(GeometryError):
            principal_axis((Vec3(0.0, 0.0, 0.0),) * 4)

    def test_template_coordinates_are_rotation_equivariant(self):
        rotation_angle_rad = math.radians(37.0)

        def rotate_about_z(vector: Vec3) -> Vec3:
            return Vec3(
                vector.x * math.cos(rotation_angle_rad) - vector.y * math.sin(rotation_angle_rad),
                vector.x * math.sin(rotation_angle_rad) + vector.y * math.cos(rotation_angle_rad),
                vector.z,
            )

        frame = TemplateFrame(
            origin=Vec3(2.0, -1.0, 3.0),
            lateral=Vec3(1.0, 0.0, 0.0),
            depth=Vec3(0.0, 1.0, 0.0),
            normal=Vec3(0.0, 0.0, 1.0),
        )
        point = Vec3(5.0, 3.0, 1.0)
        rotated_frame = TemplateFrame(
            origin=rotate_about_z(frame.origin),
            lateral=rotate_about_z(frame.lateral),
            depth=rotate_about_z(frame.depth),
            normal=rotate_about_z(frame.normal),
        )

        actual_coordinates = rotated_frame.coordinates(rotate_about_z(point))
        expected_coordinates = frame.coordinates(point)
        for actual_coordinate, expected_coordinate in zip(
            actual_coordinates,
            expected_coordinates,
            strict=True,
        ):
            self.assertAlmostEqual(actual_coordinate, expected_coordinate)

    def test_axis_coordinates(self):
        radial_distance_mm, axial_distance_mm = point_axis_coordinates(
            Vec3(2.0, 0.0, 3.0), Vec3(0.0, 0.0, 0.0), Vec3(0.0, 0.0, 2.0)
        )
        self.assertAlmostEqual(radial_distance_mm, 2.0)
        self.assertAlmostEqual(axial_distance_mm, 3.0)

    def test_periodic_curve_is_rotation_equivariant(self):
        controls = (
            Vec3(-2.0, -1.0, 0.0),
            Vec3(2.0, -1.0, 0.5),
            Vec3(2.0, 1.0, 0.0),
            Vec3(-2.0, 1.0, -0.5),
        )

        def rotate(vector: Vec3) -> Vec3:
            return Vec3(-vector.y, vector.x, vector.z)

        curve = periodic_catmull_rom(controls, 0.4)
        rotated_curve = periodic_catmull_rom(tuple(rotate(point) for point in controls), 0.4)

        self.assertEqual(len(curve), len(rotated_curve))
        for point, rotated_point in zip(curve, rotated_curve, strict=True):
            self.assertLess(rotate(point).distance_to(rotated_point), 1e-9)

    def test_tetrahedron_volume_centroid(self):
        origin = Vec3(0.0, 0.0, 0.0)
        x_vertex = Vec3(1.0, 0.0, 0.0)
        y_vertex = Vec3(0.0, 1.0, 0.0)
        z_vertex = Vec3(0.0, 0.0, 1.0)
        triangles = (
            (origin, y_vertex, x_vertex),
            (origin, x_vertex, z_vertex),
            (origin, z_vertex, y_vertex),
            (x_vertex, y_vertex, z_vertex),
        )
        self.assertEqual(volume_centroid(triangles), Vec3(0.25, 0.25, 0.25))


if __name__ == "__main__":
    unittest.main()

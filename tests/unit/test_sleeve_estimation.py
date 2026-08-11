import math
import unittest

from twin_guide.geometry import Vec3
from twin_guide.sleeve_estimation.fitting import fit_circle
from twin_guide.sleeve_estimation.mesh_integrity import inspect_triangle_mesh
from twin_guide.sleeve_estimation.sleeve import c_opening_toward, estimate_sleeve_axis
from twin_guide.sleeve_estimation.slicing import slice_mesh
from twin_guide.sleeve_estimation.types import SleeveEstimate, TriangleMeshData
from twin_guide.sleeve_estimation.validation import validate_reconstruction


def _add_quad(faces, first, second, third, fourth, *, reverse=False):
    triangles = ((first, second, third), (first, third, fourth))
    if reverse:
        faces.extend(tuple(reversed(face)) for face in triangles)
    else:
        faces.extend(triangles)


def _synthetic_sleeve(
    *,
    inner_radius=1.2,
    outer_radius=2.0,
    height=8.0,
    platform_height=2.0,
    platform_extension=0.8,
    arc_angle=1.5 * math.pi,
    segments=48,
):
    """创建独立的开孔单侧平台导管测试网格。"""

    cut_coordinate = inner_radius * math.cos(math.pi - 0.5 * arc_angle)
    outer_arc_angle = 2.0 * math.pi - 2.0 * math.acos(cut_coordinate / outer_radius)
    angles_by_kind = {
        "inner": tuple(
            math.pi - 0.5 * arc_angle + arc_angle * index / segments
            for index in range(segments + 1)
        ),
        "outer": tuple(
            math.pi - 0.5 * outer_arc_angle + outer_arc_angle * index / segments
            for index in range(segments + 1)
        ),
    }
    levels = (-0.5 * height, -0.5 * height + platform_height, 0.5 * height)
    vertices = []
    rings = {}
    for level_index, z_value in enumerate(levels):
        for kind in ("inner", "outer"):
            ring = []
            for angle in angles_by_kind[kind]:
                extension = 0.0
                if kind == "outer" and level_index == 0:
                    alignment = max(0.0, math.cos(angle))
                    extension = platform_extension * alignment**8
                radius = inner_radius if kind == "inner" else outer_radius + extension
                ring.append(len(vertices))
                vertices.append(Vec3(radius * math.cos(angle), radius * math.sin(angle), z_value))
            rings[level_index, kind] = tuple(ring)

    faces = []
    for level_index in range(2):
        for angle_index in range(segments):
            inner_low = rings[level_index, "inner"][angle_index]
            inner_high = rings[level_index + 1, "inner"][angle_index]
            inner_high_next = rings[level_index + 1, "inner"][angle_index + 1]
            inner_low_next = rings[level_index, "inner"][angle_index + 1]
            _add_quad(faces, inner_low, inner_high, inner_high_next, inner_low_next)

            outer_low = rings[level_index, "outer"][angle_index]
            outer_low_next = rings[level_index, "outer"][angle_index + 1]
            outer_high_next = rings[level_index + 1, "outer"][angle_index + 1]
            outer_high = rings[level_index + 1, "outer"][angle_index]
            _add_quad(faces, outer_low, outer_low_next, outer_high_next, outer_high)

    for level_index in (0, 2):
        reverse = level_index == 0
        for angle_index in range(segments):
            _add_quad(
                faces,
                rings[level_index, "inner"][angle_index],
                rings[level_index, "inner"][angle_index + 1],
                rings[level_index, "outer"][angle_index + 1],
                rings[level_index, "outer"][angle_index],
                reverse=reverse,
            )
    for angle_index in range(segments):
        _add_quad(
            faces,
            rings[1, "outer"][angle_index],
            rings[1, "outer"][angle_index + 1],
            rings[0, "outer"][angle_index + 1],
            rings[0, "outer"][angle_index],
        )
    for endpoint in (0, segments):
        for level_index in range(2):
            _add_quad(
                faces,
                rings[level_index, "inner"][endpoint],
                rings[level_index, "outer"][endpoint],
                rings[level_index + 1, "outer"][endpoint],
                rings[level_index + 1, "inner"][endpoint],
                reverse=endpoint == segments,
            )
    truth = (
        inner_radius,
        outer_radius,
        height,
        platform_height,
        platform_extension,
        arc_angle,
        outer_arc_angle,
    )
    return TriangleMeshData(tuple(vertices), tuple(faces)), truth


def _rigid_transform(mesh, translation=None, angle=0.63):
    if translation is None:
        translation = Vec3(3.0, -4.0, 2.0)
    cosine, sine = math.cos(angle), math.sin(angle)

    def rotate(point):
        # 构造第三列不与世界坐标轴重合的旋转矩阵。
        first = Vec3(cosine, sine, 0.0)
        third = Vec3(-0.35 * sine, 0.35 * cosine, math.sqrt(1.0 - 0.35**2))
        second = third.cross(first).normalized()
        third = first.cross(second).normalized()
        return translation + first * point.x + second * point.y + third * point.z

    return TriangleMeshData(tuple(rotate(point) for point in mesh.vertices), mesh.faces)


def _bidirectional_vertex_distance(first, second):
    def directed(source, target):
        return max(
            min(point.distance_to(other) for other in target.vertices) for point in source.vertices
        )

    return max(directed(first, second), directed(second, first))


class SleeveEstimationTests(unittest.TestCase):
    def test_mesh_integrity_detects_open_and_duplicate_surfaces(self):
        vertices = (
            Vec3(0.0, 0.0, 0.0),
            Vec3(1.0, 0.0, 0.0),
            Vec3(0.0, 1.0, 0.0),
            Vec3(0.0, 0.0, 1.0),
        )
        faces = ((0, 2, 1), (0, 1, 3), (1, 2, 3), (2, 0, 3))
        closed = inspect_triangle_mesh(TriangleMeshData(vertices, faces))
        self.assertTrue(closed.valid)
        self.assertEqual(closed.component_count, 1)

        opened = inspect_triangle_mesh(TriangleMeshData(vertices, faces[:-1]))
        self.assertFalse(opened.valid)
        self.assertEqual(opened.boundary_edge_count, 3)

        duplicated = inspect_triangle_mesh(TriangleMeshData(vertices, (*faces, faces[0])))
        self.assertFalse(duplicated.valid)
        self.assertEqual(duplicated.duplicate_face_count, 1)
        self.assertEqual(duplicated.non_manifold_edge_count, 3)

    def test_slice_and_circle_fit_recover_open_bore(self):
        mesh, truth = _synthetic_sleeve()
        section = slice_mesh(mesh, Vec3(0.0, 0.0, 0.0), Vec3(0.0, 0.0, 1.0), 1.0)
        bore_points = tuple(
            sample.point
            for sample in section.samples
            if sample.point.x * sample.normal.x + sample.point.y * sample.normal.y < 0.0
        )
        fit = fit_circle(bore_points, Vec3(0.0, 0.0, 1.0), Vec3(0.0, 0.0, 1.0))
        self.assertAlmostEqual(fit.radius, truth[0], delta=0.02)

    def test_estimate_axis_and_validate_actual_mesh(self):
        mesh, truth = _synthetic_sleeve()
        transformed = _rigid_transform(mesh)
        pose = estimate_sleeve_axis(transformed)
        estimate = SleeveEstimate(
            axis_origin=pose.axis_origin,
            axis=pose.axis,
            c_opening_direction=Vec3(1.0, 0.0, 0.0),
            height=truth[2],
            platform_height=truth[3],
            closed_bore_height=1.4,
            platform_slot_width=2.4,
            inner_radius=truth[0],
            outer_radius=truth[1],
            inner_arc_angle=truth[5],
            outer_arc_angle=truth[6],
        )
        expected_axis = Vec3(
            -0.35 * math.sin(0.63),
            0.35 * math.cos(0.63),
            math.sqrt(1.0 - 0.35**2),
        )
        self.assertGreater(estimate.axis.dot(expected_axis), 0.99)
        axial_coordinates = tuple(
            (point - estimate.axis_origin).dot(estimate.axis) for point in transformed.vertices
        )
        self.assertAlmostEqual(min(axial_coordinates), 0.0, delta=0.12)
        self.assertAlmostEqual(max(axial_coordinates), estimate.height, delta=0.12)

        validation = validate_reconstruction(
            transformed,
            estimate,
            transformed,
            maximum_samples=500,
        )
        self.assertAlmostEqual(validation.symmetric_rms, 0.0, delta=1e-10)

    def test_axis_line_is_recovered_from_upside_down_input(self):
        source, _ = _synthetic_sleeve()
        upside_down = TriangleMeshData(
            tuple(Vec3(point.x, -point.y, -point.z) for point in source.vertices),
            source.faces,
        )

        estimate = estimate_sleeve_axis(upside_down)

        self.assertGreater(abs(estimate.axis.dot(Vec3(0.0, 0.0, -1.0))), 0.99)

    def test_c_openings_face_each_other(self):
        first_axis = Vec3(0.0, 0.0, 1.0)
        second_axis = Vec3(0.2, 0.0, 0.98).normalized()
        first_center = Vec3(-5.0, 0.0, 1.0)
        second_center = Vec3(5.0, 0.0, -1.0)

        first = c_opening_toward(first_axis, first_center, second_center)
        second = c_opening_toward(second_axis, second_center, first_center)

        self.assertGreater(first.dot(second_center - first_center), 0.0)
        self.assertGreater(second.dot(first_center - second_center), 0.0)
        self.assertAlmostEqual(first.dot(first_axis), 0.0, delta=1e-12)
        self.assertAlmostEqual(second.dot(second_axis), 0.0, delta=1e-12)


if __name__ == "__main__":
    unittest.main()

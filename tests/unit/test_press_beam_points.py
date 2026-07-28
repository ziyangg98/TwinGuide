import unittest
from types import SimpleNamespace

import numpy as np
import trimesh

from twin_guide.config import ToothAnchorStation
from twin_guide.errors import GeometryError
from twin_guide.geometry import Vec3
from twin_guide.models import GuideSleeve
from twin_guide.press_beam_points import (
    _case_occlusal_axis,
    _conditional_inner_sleeve_junction,
    _geometric_median,
    _inner_sleeve_scores,
    _lifted_three_anchor_junction,
    _minimum_junction_angle_degrees,
    _positive_sleeve_axis,
)
from twin_guide.sleeve_estimation.types import SleeveEstimate
from twin_guide.tooth_identification import ToothPosition
from twin_guide.tooth_section_anchors._core import (
    _arch_outward_coordinate,
    _bridge_short_visibility_gaps,
    _common_positive_sleeve_axis,
    _covered_neighbour_reference_tangent,
    _interpolate_missing_tooth,
    _ray_outer_exit_anchor,
    _rotation_lateral_direction,
    _side_rotation_direction,
    _u_and_back_u_directions,
)


def _guide(index: int, y: float) -> GuideSleeve:
    return GuideSleeve(
        guide_index=index,
        guide_mesh=None,
        parameters=SleeveEstimate(
            axis_origin=Vec3(0.0, y, 0.0),
            axis=Vec3(0.0, 0.0, 1.0),
            c_opening_direction=Vec3(1.0, 0.0, 0.0),
            height=16.0,
            platform_height=6.0,
            closed_bore_height=4.0,
            platform_width=2.0,
            inner_radius=1.0,
            outer_radius=2.5,
            inner_arc_angle=4.5,
            outer_arc_angle=5.0,
        ),
        axial_min_mm=0.0,
        axial_max_mm=16.0,
    )


class PressBeamPointTests(unittest.TestCase):
    def test_only_short_external_visibility_gaps_are_bridged(self):
        """网格小断点应闭合，长内表面段必须保持排除。"""

        points = np.asarray(
            [
                [0.0, 0.0, 0.0],
                [0.1, 0.0, 0.0],
                [0.2, 0.0, 0.0],
                [2.0, 0.0, 0.0],
                [3.0, 0.0, 0.0],
            ]
        )
        mask = np.asarray([True, False, True, False, True])

        bridged = _bridge_short_visibility_gaps(points, mask, 0.25)

        np.testing.assert_array_equal(
            bridged,
            np.asarray([True, True, True, False, True]),
        )

    def test_missing_tooth_station_is_interpolated_along_fdi_order(self):
        """缺牙 47 应由牙弓顺序两侧的现存牙 48、46 插值。"""

        positions = {
            48: ToothPosition(
                48,
                Vec3(-4.0, 2.0, 0.0),
                Vec3(-4.0, 2.0, 4.0),
                -6.0,
                Vec3(1.0, 0.0, 0.0),
                Vec3(0.0, 1.0, 0.0),
            ),
            46: ToothPosition(
                46,
                Vec3(0.0, -2.0, 2.0),
                Vec3(0.0, -2.0, 6.0),
                -2.0,
                Vec3(0.0, 1.0, 0.0),
                Vec3(1.0, 0.0, 0.0),
            ),
        }

        virtual = _interpolate_missing_tooth(
            47,
            positions,
            (48, 47, 46),
            (47,),
        )

        self.assertEqual(virtual.crown_point, Vec3(-2.0, 0.0, 1.0))
        self.assertEqual(virtual.guide_top, Vec3(-2.0, 0.0, 5.0))
        self.assertEqual(virtual.arch_s_mm, -4.0)
        self.assertEqual(
            virtual.local_tangent,
            Vec3(1.0, 1.0, 0.0).normalized(),
        )

    def test_u_side_has_negative_arch_outward_coordinate(self):
        """U 型牙弓凹侧必须为负坐标，背 U 侧必须为正坐标。"""

        center = np.asarray([0.0, 0.0, 0.0])
        outward = np.asarray([0.0, 1.0, 0.0])

        self.assertEqual(
            _arch_outward_coordinate(np.asarray([0.0, -2.0, 0.0]), center, outward),
            -2.0,
        )
        self.assertEqual(
            _arch_outward_coordinate(np.asarray([0.0, 2.0, 0.0]), center, outward),
            2.0,
        )

    def test_rotation_plane_uses_positive_axis_and_two_guide_line(self):
        """正导管轴应朝外，两导管连线投影应定义旋转面的左右方向。"""

        sleeves = SimpleNamespace(
            sleeves=(_guide(1, -2.0), _guide(2, 2.0)),
            template_frame=SimpleNamespace(normal=Vec3(0.0, 0.0, 1.0)),
        )

        positive_axis = _common_positive_sleeve_axis(sleeves)
        lateral = _rotation_lateral_direction(sleeves, positive_axis)

        np.testing.assert_allclose(positive_axis, np.asarray([0.0, 0.0, 1.0]))
        np.testing.assert_allclose(lateral, np.asarray([0.0, 1.0, 0.0]))

    def test_ray_selects_local_outer_wall_exit(self):
        """从两壁之间发出的射线必须跳过内壁入口并选择外壁出口。"""

        negative_wall = trimesh.creation.box(extents=(4.0, 2.0, 4.0))
        negative_wall.apply_translation((0.0, -3.0, 0.0))
        positive_wall = trimesh.creation.box(extents=(4.0, 2.0, 4.0))
        positive_wall.apply_translation((0.0, 3.0, 0.0))
        mesh = trimesh.util.concatenate((negative_wall, positive_wall))
        mesh.fix_normals()

        negative = _ray_outer_exit_anchor(
            mesh,
            np.asarray([0.0, 0.0, 0.0]),
            np.asarray([0.0, -1.0, 0.0]),
        )
        positive = _ray_outer_exit_anchor(
            mesh,
            np.asarray([0.0, 0.0, 0.0]),
            np.asarray([0.0, 1.0, 0.0]),
        )

        self.assertAlmostEqual(negative.position.y, -4.0)
        self.assertAlmostEqual(positive.position.y, 4.0)
        self.assertLess(negative.normal.y, -0.9)
        self.assertGreater(positive.normal.y, 0.9)

    def test_u_side_rotates_70_and_back_u_side_rotates_90_degrees(self):
        """局部外向应把旋转面两侧映射为 U 侧 70° 和背 U 侧 90°。"""

        axis = np.asarray([0.0, 0.0, 1.0])
        lateral = np.asarray([0.0, 1.0, 0.0])
        outward = np.asarray([0.0, 1.0, 0.0])

        u_direction, back_u_direction = _u_and_back_u_directions(
            axis,
            lateral,
            outward,
        )

        self.assertAlmostEqual(float(u_direction @ axis), np.cos(np.deg2rad(70.0)))
        self.assertAlmostEqual(float(u_direction @ -lateral), np.sin(np.deg2rad(70.0)))
        self.assertAlmostEqual(
            float(back_u_direction @ axis),
            np.cos(np.deg2rad(90.0)),
        )
        self.assertAlmostEqual(
            float(back_u_direction @ lateral),
            np.sin(np.deg2rad(90.0)),
        )

    def test_press_beam_u_side_ray_rotates_45_degrees(self):
        """三牙位按压梁锚点应统一使用靠 U 侧 45° 射线。"""

        axis = np.asarray([0.0, 0.0, 1.0])
        lateral = np.asarray([0.0, 1.0, 0.0])
        outward = np.asarray([0.0, 1.0, 0.0])

        direction = _side_rotation_direction(
            axis,
            lateral,
            outward,
            45.0,
            u_side=True,
        )

        self.assertAlmostEqual(float(direction @ axis), np.cos(np.deg2rad(45.0)))
        self.assertAlmostEqual(float(direction @ -lateral), np.sin(np.deg2rad(45.0)))

    def test_single_station_skips_uncovered_neighbour(self):
        """单牙旋转面必须使用最近且被导板覆盖的邻牙。"""

        def tooth(fdi: int, x: float, covered: bool) -> ToothPosition:
            return ToothPosition(
                fdi,
                Vec3(x, 0.0, 0.0),
                Vec3(x, 0.0, 2.0) if covered else None,
                x,
                Vec3(1.0, 0.0, 0.0),
                Vec3(0.0, 1.0, 0.0),
            )

        positions = {
            34: tooth(34, -2.0, True),
            35: tooth(35, 0.0, True),
            36: tooth(36, 1.0, False),
        }

        tangent, reference_fdis = _covered_neighbour_reference_tangent(
            ToothAnchorStation((35,)),
            positions,
            (34, 35, 36),
            (),
        )

        self.assertEqual(reference_fdis, (35, 34))
        np.testing.assert_allclose(tangent, np.asarray([-1.0, 0.0, 0.0]))

    def test_pair_station_uses_its_two_covered_teeth(self):
        """双牙中点旋转面应直接采用这两颗覆盖牙的中心连线。"""

        first = ToothPosition(
            45,
            Vec3(0.0, 0.0, 0.0),
            Vec3(0.0, 0.0, 2.0),
            0.0,
            Vec3(1.0, 0.0, 0.0),
            Vec3(0.0, 1.0, 0.0),
        )
        second = ToothPosition(
            44,
            Vec3(3.0, 4.0, 0.0),
            Vec3(3.0, 4.0, 2.0),
            5.0,
            Vec3(1.0, 0.0, 0.0),
            Vec3(0.0, 1.0, 0.0),
        )

        tangent, reference_fdis = _covered_neighbour_reference_tangent(
            ToothAnchorStation((45, 44)),
            {45: first, 44: second},
            (45, 44),
            (),
        )

        self.assertEqual(reference_fdis, (45, 44))
        np.testing.assert_allclose(tangent, np.asarray([0.6, 0.8, 0.0]))

    def test_low_sleeve_anchor_uses_center_plus_axial_lift(self):
        """导管锚点低于三点中心时，汇合点应从原中心继续抬高 2 mm。"""

        upper = Vec3(0.0, 0.0, 0.0)
        axis = Vec3(0.0, 0.0, 1.0)
        anchors = (
            upper,
            Vec3(4.0, -3.0, 3.0),
            Vec3(4.0, 3.0, 3.0),
        )
        original_center = _geometric_median(anchors)
        self.assertGreater((original_center - upper).dot(axis), 0.0)

        junction = _conditional_inner_sleeve_junction(
            anchors,
            upper,
            axis,
            6.0,
            2.0,
        )

        self.assertAlmostEqual((junction - original_center).dot(axis), 2.0, places=7)
        self.assertAlmostEqual(junction.distance_to(upper), 6.0, places=7)
        self.assertGreater(_minimum_junction_angle_degrees(junction, anchors), 0.0)

    def test_positive_sleeve_axis_points_from_lower_to_upper(self):
        """输入导管轴反向时也必须恢复几何高端正方向。"""

        lower = Vec3(1.0, 2.0, 3.0)
        upper = Vec3(1.0, 2.0, 8.0)

        positive = _positive_sleeve_axis(
            Vec3(0.0, 0.0, -1.0),
            lower,
            upper,
        )

        self.assertEqual(positive, Vec3(0.0, 0.0, 1.0))

    def test_high_sleeve_anchor_keeps_existing_axial_alignment(self):
        """导管锚点不低于三点中心时，继续使用导管锚点等高平面。"""

        upper = Vec3(0.0, 0.0, 5.0)
        axis = Vec3(0.0, 0.0, 1.0)
        anchors = (
            upper,
            Vec3(18.0, -2.0, 0.0),
            Vec3(18.0, 2.0, 0.0),
        )
        original_center = _geometric_median(anchors)
        self.assertLessEqual((original_center - upper).dot(axis), 0.0)
        expected = original_center - axis * (original_center - upper).dot(axis)

        junction = _conditional_inner_sleeve_junction(
            anchors,
            upper,
            axis,
            6.0,
            2.0,
        )

        self.assertGreater(expected.distance_to(upper), 6.0)
        self.assertAlmostEqual((junction - upper).dot(axis), 0.0, places=7)
        self.assertAlmostEqual(junction.distance_to(expected), 0.0, places=7)
        self.assertGreater(junction.distance_to(upper), 6.0)

    def test_three_anchor_junction_uses_configured_minimum_angle(self):
        """三牙位 Y 汇合点必须采用配置阈值而不是固定 25°。"""

        anchors = (
            Vec3(-8.0, 0.0, 0.0),
            Vec3(4.0, -6.0, 0.0),
            Vec3(4.0, 6.0, 0.0),
        )

        with self.assertRaisesRegex(GeometryError, "小于 179.00°"):
            _lifted_three_anchor_junction(
                anchors,
                2.3,
                Vec3(0.0, 0.0, 1.0),
                2.0,
                179.0,
            )

    def test_inner_sleeve_is_the_smaller_local_outward_coordinate(self):
        """自动导管选择必须排除更靠唇颊侧的外导管。"""

        tooth = ToothPosition(
            11,
            Vec3(0.0, 0.0, 0.0),
            Vec3(0.0, 0.0, 1.0),
            0.0,
            Vec3(1.0, 0.0, 0.0),
            Vec3(0.0, 1.0, 0.0),
        )
        context = SimpleNamespace(
            sleeve_generation=SimpleNamespace(sleeves=(_guide(1, 2.0), _guide(2, -2.0))),
            tooth_identification=SimpleNamespace(positions=(tooth,)),
        )

        scores = _inner_sleeve_scores(context)

        self.assertEqual(tuple(score.guide_index for score in scores), (2, 1))
        self.assertEqual(tuple(score.outward_coordinate_mm for score in scores), (-2.0, 2.0))

    def test_case_occlusal_axis_uses_confirmed_mapping_direction(self):
        """全牙位 Y 汇合点必须使用病例 YAML 确认的牙合方向。"""

        context = SimpleNamespace(
            tooth_identification=SimpleNamespace(
                mapping_report={
                    "coordinate_system": {"e_occ": [0.0, 0.0, -2.0]}
                }
            )
        )

        occlusal_axis = _case_occlusal_axis(context)

        self.assertEqual(occlusal_axis, Vec3(0.0, 0.0, -1.0))


if __name__ == "__main__":
    unittest.main()

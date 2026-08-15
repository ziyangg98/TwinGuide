import unittest
from types import SimpleNamespace

from twin_guide.config import EditorOverrides, OperationWindowOverride
from twin_guide.geometry import Vec3
from twin_guide.models import GuideSleeve, OperationFeature, SurfaceSample
from twin_guide.sleeve_estimation.types import SleeveEstimate
from twin_guide.window_cutouts import _plan_channels, _plan_operation_window


def _guide(index: int, x: float, inner_radius: float = 1.0) -> GuideSleeve:
    return GuideSleeve(
        guide_index=index,
        guide_mesh=None,
        parameters=SleeveEstimate(
            axis_origin=Vec3(x, 0.0, 0.0),
            axis=Vec3(0.0, 0.0, 1.0),
            c_opening_direction=Vec3(0.0, 1.0, 0.0),
            height=10.0,
            platform_height=6.0,
            closed_bore_height=4.0,
            platform_slot_width=2.0,
            inner_radius=inner_radius,
            outer_radius=2.0,
            inner_arc_angle=4.0,
            outer_arc_angle=4.0,
        ),
        axial_min_mm=0.0,
        axial_max_mm=10.0,
    )


class OperationWindowTests(unittest.TestCase):
    def test_channels_use_each_generated_guides_inner_radius(self):
        case = SimpleNamespace(
            config=SimpleNamespace(geometry=SimpleNamespace(channel_axial_margin_mm=2.0)),
            guide_sleeves=(_guide(1, -3.0, 1.0), _guide(2, 3.0, 1.25)),
        )

        channels = _plan_channels(case)

        self.assertEqual(tuple(item.radius_mm for item in channels), (1.0, 1.25))

    def test_front_and_rear_margins_shift_planar_window(self):
        windows = SimpleNamespace(
            operation_tangent_margin_mm=1.0,
            operation_bitangent_margin_mm=2.0,
            operation_front_axial_margin_mm=6.0,
            operation_rear_axial_margin_mm=2.0,
            operation_corner_radius_mm=1.0,
        )
        case = SimpleNamespace(
            config=SimpleNamespace(windows=windows),
            template_samples=(
                SurfaceSample(Vec3(0.0, 0.0, -2.0), Vec3(0.0, 0.0, 1.0), 1),
                SurfaceSample(Vec3(0.0, 0.0, 3.0), Vec3(0.0, 0.0, 1.0), 2),
            ),
        )

        result = _plan_operation_window(
            case,
            (_guide(1, -3.0), _guide(2, 3.0)),
            OperationFeature(Vec3(0.0, 0.0, 0.0), 2.0),
            1,
        )

        self.assertEqual(result.depth_mm, 20.0)
        self.assertEqual(result.center, Vec3(0.0, 0.0, 6.0))
        self.assertEqual(result.normal, Vec3(0.0, 0.0, 1.0))

    def test_site_override_changes_only_requested_window_geometry(self):
        windows = SimpleNamespace(
            operation_tangent_margin_mm=1.0,
            operation_bitangent_margin_mm=2.0,
            operation_front_axial_margin_mm=6.0,
            operation_rear_axial_margin_mm=2.0,
            operation_corner_radius_mm=1.0,
        )
        overrides = EditorOverrides(
            operation_windows=(OperationWindowOverride(1, 2.0, 3.0, 4.0, 1.0, (1.0, 0.0, 0.0)),)
        )
        case = SimpleNamespace(
            config=SimpleNamespace(windows=windows, editor_overrides=overrides),
            template_samples=(
                SurfaceSample(Vec3(0.0, 0.0, -2.0), Vec3(0.0, 0.0, 1.0), 1),
                SurfaceSample(Vec3(0.0, 0.0, 3.0), Vec3(0.0, 0.0, 1.0), 2),
            ),
        )

        result = _plan_operation_window(
            case,
            (_guide(1, -3.0), _guide(2, 3.0)),
            OperationFeature(Vec3(0.0, 0.0, 0.0), 2.0),
            1,
        )

        self.assertEqual(result.depth_mm, 17.0)
        self.assertEqual(result.center, Vec3(1.0, 0.0, 5.5))
        self.assertEqual(result.width_mm, 14.0)
        self.assertEqual(result.height_mm, 8.0)


if __name__ == "__main__":
    unittest.main()

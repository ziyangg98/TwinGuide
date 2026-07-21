import unittest

from twin_guide.geometry import Vec3
from twin_guide.models import GuideSleeve, TemplateFrame
from twin_guide.point_linking import PointLinkingConfig, link_selected_points
from twin_guide.sleeve_anchors import select_sleeve_anchors
from twin_guide.sleeve_estimation.types import SleeveEstimate
from twin_guide.template_anchors import (
    TemplateAnchorPoint,
    TemplatePointPlan,
    TemplatePointSelection,
    TemplatePointSelectionConfig,
)
from twin_guide.template_link_points import TemplateLinkPointPlan
from twin_guide.types import SleeveGenerationResult


def _sleeve(index: int, x: float) -> GuideSleeve:
    return GuideSleeve(
        guide_index=index,
        guide_mesh=None,
        parameters=SleeveEstimate(
            axis_origin=Vec3(x, 0.0, 0.0),
            axis=Vec3(0.0, 0.0, 1.0),
            c_opening_direction=Vec3(1.0, 0.0, 0.0),
            height=16.0,
            platform_height=6.0,
            closed_bore_height=4.0,
            platform_width=2.0,
            inner_radius=1.0,
            outer_radius=2.5,
            inner_arc_angle=1.5 * 3.141592653589793,
            outer_arc_angle=1.75 * 3.141592653589793,
        ),
        axial_min_mm=0.0,
        axial_max_mm=16.0,
    )


class PointLinkingTests(unittest.TestCase):
    def test_template_span_follows_connector_radius(self):
        """牙科导板左右点跨度应使用论文给出的半径公式。"""

        config = TemplatePointSelectionConfig(connector_radius_mm=1.6)

        self.assertEqual(config.minimum_span_mm(2.5), 4.0)
        self.assertEqual(config.minimum_span_mm(5.0), 5.0)

    def test_builds_four_smooth_links_per_sleeve(self):
        from types import SimpleNamespace

        sleeves = (_sleeve(1, -5.0), _sleeve(2, 5.0))
        frame = TemplateFrame(
            Vec3(0.0, 0.0, 0.0),
            Vec3(1.0, 0.0, 0.0),
            Vec3(0.0, 1.0, 0.0),
            Vec3(0.0, 0.0, 1.0),
        )
        sleeve_plan = select_sleeve_anchors(
            SimpleNamespace(guide_sleeves=sleeves),
            SleeveGenerationResult(sleeves, frame),
        )
        template_selections = tuple(
            TemplatePointSelection(
                sleeve.guide_index,
                Vec3(sleeve.center.x, 0.0, 8.0),
                Vec3(0.0, 1.0, 0.0),
                TemplateAnchorPoint(Vec3(sleeve.center.x, -4.0, 0.0), Vec3(0, 0, 1), 1),
                TemplateAnchorPoint(Vec3(sleeve.center.x, 4.0, 0.0), Vec3(0, 0, 1), 2),
                3.0,
            )
            for sleeve in sleeves
        )
        points = TemplateLinkPointPlan(
            sleeve_plan,
            TemplatePointPlan(template_selections),
        )
        config = PointLinkingConfig(radius_mm=1.2, curve_resolution=30)

        plan = link_selected_points(points, config)

        self.assertEqual(len(plan.links), 8)
        self.assertEqual(plan.radius_mm, 1.2)
        self.assertEqual(plan.curve_resolution, 30)
        self.assertFalse(plan.press_beam_links_included)
        self.assertEqual(plan.connection_type, "sleeve_template")
        for link in plan.links:
            self.assertEqual(link.centerline[0], link.start)
            self.assertEqual(link.centerline[-1], link.end)
            self.assertGreater((link.control_points[1] - link.start).length, 0.0)
            self.assertGreater((link.control_points[2] - link.end).length, 0.0)


if __name__ == "__main__":
    unittest.main()

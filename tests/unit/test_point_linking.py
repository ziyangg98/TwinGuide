import unittest
from dataclasses import replace

from twin_guide.config import ConnectorAvoidanceOverride, Jaw
from twin_guide.generation_process import (
    _jaw_downward_direction,
    _reuse_numerically_unchanged_links,
)
from twin_guide.geometry import Vec3
from twin_guide.models import GuideSleeve, TemplateFrame
from twin_guide.point_linking import (
    PointLink,
    PointLinkingConfig,
    PointLinkingPlan,
    link_selected_points,
)
from twin_guide.press_beam_points import (
    InnerSleeveScore,
    PressBeamGuideAnchor,
    PressBeamPointPlan,
    PressBeamSleeveAnchor,
)
from twin_guide.sleeve_anchors import SleeveAnchorSelectionConfig, select_sleeve_anchors
from twin_guide.sleeve_estimation.types import SleeveEstimate
from twin_guide.template_anchors import (
    MultiSiteTemplatePath,
    TemplateAnchorPoint,
    TemplatePointPlan,
    TemplatePointSelection,
    TemplatePointSelectionConfig,
)
from twin_guide.template_link_points import TemplateLinkPointPlan
from twin_guide.terminal_distal_common_node import TerminalDistalCommonNodePlan
from twin_guide.types import ConnectorEndpointSource, SleeveGenerationResult


def _sleeve(index: int, x: float) -> GuideSleeve:
    return GuideSleeve(
        guide_index=index,
        guide_mesh=None,
        parameters=SleeveEstimate(
            axis_origin=Vec3(x, 0.0, 0.0),
            axis=Vec3(0.0, 0.0, 1.0),
            c_opening_direction=Vec3(1.0 if x < 0.0 else -1.0, 0.0, 0.0),
            height=16.0,
            platform_height=6.0,
            closed_bore_height=4.0,
            platform_slot_width=1.0,
            inner_radius=1.0,
            outer_radius=2.5,
            inner_arc_angle=1.5 * 3.141592653589793,
            outer_arc_angle=1.75 * 3.141592653589793,
        ),
        axial_min_mm=0.0,
        axial_max_mm=16.0,
    )


class PointLinkingTests(unittest.TestCase):
    def test_fixed_platform_shift_follows_upper_and_lower_gingival_direction(self):
        """同一模板法向应按上下颌统一到各自的龈向下移侧。"""

        tilted_up = Vec3(0.2, 0.0, 0.98).normalized()
        self.assertEqual(_jaw_downward_direction(Jaw.LOWER, tilted_up), tilted_up * -1.0)
        self.assertEqual(_jaw_downward_direction(Jaw.UPPER, tilted_up), tilted_up)

    def test_template_span_follows_connector_radius(self):
        """牙科导板左右点跨度应使用论文给出的半径公式。"""

        config = TemplatePointSelectionConfig(connector_radius_mm=1.6)

        self.assertEqual(config.minimum_span_mm(2.5), 4.0)
        self.assertEqual(config.minimum_span_mm(5.0), 5.0)

    def test_builds_two_continuous_links_per_sleeve(self):
        """每个导管应生成经过 P 的上下两根连续 Hermite 梁。"""
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
            SleeveAnchorSelectionConfig(connector_radius_mm=1.2),
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

        self.assertEqual(len(plan.links), 4)
        self.assertEqual(plan.radius_mm, 1.2)
        self.assertEqual(plan.curve_resolution, 30)
        self.assertFalse(plan.press_beam_links_included)
        self.assertEqual(plan.connection_type, "continuous_sleeve_frame")
        default_routes = tuple(
            route for link in plan.links for route in link.platform_avoidance_routes
        )
        self.assertEqual(
            {(route.guide_index, route.side) for route in default_routes},
            {(1, "left"), (1, "right"), (2, "left"), (2, "right")},
        )
        self.assertTrue(all(route.actual_offset_mm == 4.0 for route in default_routes))
        for route in default_routes:
            self.assertAlmostEqual(
                (route.routing_point - route.tube_contact).dot(route.avoidance_direction),
                4.0,
            )
        crowded_sleeves = tuple(
            replace(
                sleeve,
                parameters=replace(sleeve.parameters, platform_height=14.0),
            )
            for sleeve in sleeves
        )
        crowded_sleeve_plan = select_sleeve_anchors(
            SimpleNamespace(guide_sleeves=crowded_sleeves),
            SleeveGenerationResult(crowded_sleeves, frame),
            SleeveAnchorSelectionConfig(connector_radius_mm=1.2),
        )
        crowded_points = TemplateLinkPointPlan(
            crowded_sleeve_plan,
            TemplatePointPlan(template_selections),
        )
        crowded_default = link_selected_points(crowded_points, config)
        self.assertTrue(
            all(
                route.actual_offset_mm == 4.0
                for link in crowded_default.links
                for route in link.platform_avoidance_routes
            )
        )
        crowded_config = replace(
            config,
            stop_platform_overrides=tuple(
                ConnectorAvoidanceOverride(guide_index, 0.35, 2.7, side)
                for guide_index in (1, 2)
                for side in ("left", "right")
            ),
        )
        crowded = link_selected_points(
            crowded_points,
            crowded_config,
        )
        crowded_routes = tuple(
            route for link in crowded.links for route in link.platform_avoidance_routes
        )
        self.assertEqual(len(crowded_routes), 4)
        self.assertTrue(all(route.actual_offset_mm > 0.0 for route in crowded_routes))
        for link in plan.links:
            self.assertEqual(link.centerline[0], link.start)
            self.assertEqual(link.centerline[-1], link.end)
            self.assertEqual(link.centerline[link.contact_index], link.tube_contact)
            self.assertAlmostEqual(
                link.start.distance_to(link.left_surface_anchor),
                0.0,
            )
            self.assertAlmostEqual(
                link.end.distance_to(link.right_surface_anchor),
                0.0,
            )
        for guide_index in (1, 2):
            guide_links = [link for link in plan.links if link.guide_index == guide_index]
            self.assertEqual(len(guide_links), 2)
            self.assertEqual(guide_links[0].start, guide_links[1].start)
            self.assertEqual(guide_links[0].end, guide_links[1].end)

        upper_only = link_selected_points(
            points,
            PointLinkingConfig(
                radius_mm=1.2,
                curve_resolution=30,
                include_lower_main=False,
            ),
        )
        self.assertEqual(len(upper_only.links), 2)
        self.assertTrue(all(link.sleeve_label == "upper" for link in upper_only.links))
        with self.assertRaisesRegex(ValueError, "至少保留"):
            PointLinkingConfig(
                radius_mm=1.2,
                include_lower_main=False,
                include_upper_main=False,
            )

        avoided = link_selected_points(
            points,
            PointLinkingConfig(
                radius_mm=1.2,
                curve_resolution=30,
                stop_platform_front_avoidance_mm=2.0,
            ),
            stop_platform_avoidance_direction=Vec3(0.0, 0.0, -1.0),
        )
        for selection in sleeve_plan.selections:
            upper_link = next(
                link
                for link in avoided.links
                if link.guide_index == selection.guide_index and link.sleeve_label == "upper"
            )
            candidate_routing_points = (
                Vec3(
                    selection.upper.position.x
                    + (upper_link.start.x - selection.upper.position.x) * 0.35,
                    selection.upper.position.y
                    + (upper_link.start.y - selection.upper.position.y) * 0.35,
                    selection.upper.position.z - 2.0,
                ),
                Vec3(
                    selection.upper.position.x
                    + (upper_link.end.x - selection.upper.position.x) * 0.35,
                    selection.upper.position.y
                    + (upper_link.end.y - selection.upper.position.y) * 0.35,
                    selection.upper.position.z - 2.0,
                ),
            )
            self.assertTrue(
                any(point in upper_link.centerline for point in candidate_routing_points)
            )
            self.assertEqual(
                {route.side for route in upper_link.platform_avoidance_routes},
                {"left", "right"},
            )
            self.assertEqual(
                upper_link.centerline[upper_link.contact_index],
                selection.upper.position,
            )

        independent = link_selected_points(
            points,
            PointLinkingConfig(
                radius_mm=1.2,
                curve_resolution=30,
                stop_platform_overrides=(ConnectorAvoidanceOverride(1, 0.6, 3.0, "left"),),
            ),
            stop_platform_avoidance_direction=Vec3(0.0, 0.0, -1.0),
        )
        first_upper = next(
            link
            for link in independent.links
            if link.guide_index == 1 and link.sleeve_label == "upper"
        )
        first_routes = {route.side: route for route in first_upper.platform_avoidance_routes}
        self.assertAlmostEqual(first_routes["left"].path_fraction, 0.6)
        self.assertGreaterEqual(first_routes["left"].actual_offset_mm, 3.0)
        self.assertEqual(
            first_routes["left"].avoidance_direction,
            Vec3(0.0, 0.0, -1.0),
        )
        self.assertAlmostEqual(first_routes["right"].path_fraction, 0.35)

    def test_terminal_missing_tooth_links_share_one_distal_node(self):
        """末端缺牙模式应使两导管的上下四梁共享远中节点。"""
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
            SleeveAnchorSelectionConfig(connector_radius_mm=1.2),
        )
        surface = Vec3(0.0, 10.0, 0.0)
        common_node = Vec3(0.0, 10.0, 1.5)
        terminal = TerminalDistalCommonNodePlan(
            17,
            16,
            common_node,
            1.5,
            Vec3(0.0, 1.0, 0.0),
            5.0,
            surface,
        )
        selections = tuple(
            TemplatePointSelection(
                sleeve.guide_index,
                Vec3(sleeve.center.x, 0.0, 8.0),
                Vec3(0.0, 1.0, 0.0),
                TemplateAnchorPoint(
                    Vec3(sleeve.center.x, -4.0, 0.0),
                    Vec3(0.0, 0.0, 1.0),
                    sleeve.guide_index,
                ),
                TemplateAnchorPoint(surface, Vec3(0.0, 0.0, 1.0), -1),
                3.0,
                left_source=ConnectorEndpointSource.TEMPLATE,
                right_source=ConnectorEndpointSource.DISTAL_COMMON_NODE,
                right_centerline_anchor=common_node,
            )
            for sleeve in sleeves
        )
        points = TemplateLinkPointPlan(
            sleeve_plan,
            TemplatePointPlan(selections, terminal_distal_common_node=terminal),
        )

        plan = link_selected_points(
            points,
            PointLinkingConfig(radius_mm=1.2, curve_resolution=30),
        )

        self.assertEqual(len(plan.links), 4)
        self.assertIs(plan.terminal_distal_common_node, terminal)
        self.assertTrue(all(link.end == common_node for link in plan.links))
        self.assertTrue(
            all(
                link.right_source is ConnectorEndpointSource.DISTAL_COMMON_NODE
                for link in plan.links
            )
        )

    def test_two_implant_terminal_paths_share_distal_common_node(self):
        """四导管应形成两条跨种植位路径，并在上下层共享远中节点。"""
        from dataclasses import replace
        from types import SimpleNamespace

        raw_sleeves = tuple(_sleeve(index, x) for index, x in enumerate((-9.0, -6.0, 2.0, 5.0), 1))
        c_opening_directions = (
            Vec3(1.0, 0.0, 0.0),
            Vec3(-1.0, 0.0, 0.0),
            Vec3(1.0, 0.0, 0.0),
            Vec3(-1.0, 0.0, 0.0),
        )
        sleeves = tuple(
            replace(
                sleeve,
                parameters=replace(
                    sleeve.parameters,
                    c_opening_direction=direction,
                ),
            )
            for sleeve, direction in zip(raw_sleeves, c_opening_directions, strict=True)
        )
        frame = TemplateFrame(
            Vec3(0.0, 0.0, 0.0),
            Vec3(1.0, 0.0, 0.0),
            Vec3(0.0, 1.0, 0.0),
            Vec3(0.0, 0.0, 1.0),
        )
        sleeve_plan = select_sleeve_anchors(
            SimpleNamespace(guide_sleeves=sleeves),
            SleeveGenerationResult(sleeves, frame),
            SleeveAnchorSelectionConfig(connector_radius_mm=1.2),
        )
        surface = Vec3(10.0, 8.0, 0.0)
        common_node = Vec3(10.0, 8.0, 1.5)
        terminal = TerminalDistalCommonNodePlan(
            17,
            15,
            common_node,
            1.5,
            Vec3(0.0, 1.0, 0.0),
            5.0,
            surface,
        )
        distal_node = TemplateAnchorPoint(common_node, Vec3(0.0, 1.0, 0.0), None)
        paths = (
            MultiSiteTemplatePath(
                "u_side",
                TemplateAnchorPoint(Vec3(-12.0, -3.0, 0.0), Vec3(0, 0, 1), 1),
                distal_node,
                (1, 3),
                (15, 14),
                (17,),
                (70.0, 0.0),
                end_source=ConnectorEndpointSource.DISTAL_COMMON_NODE,
                end_centerline_anchor=common_node,
            ),
            MultiSiteTemplatePath(
                "back_u_side",
                TemplateAnchorPoint(Vec3(-12.0, 3.0, 0.0), Vec3(0, 0, 1), 2),
                distal_node,
                (2, 4),
                (15, 14),
                (17,),
                (90.0, 0.0),
                end_source=ConnectorEndpointSource.DISTAL_COMMON_NODE,
                end_centerline_anchor=common_node,
            ),
        )
        points = TemplateLinkPointPlan(
            sleeve_plan,
            TemplatePointPlan(
                (),
                terminal_distal_common_node=terminal,
                multi_site_paths=paths,
            ),
        )

        plan = link_selected_points(
            points,
            PointLinkingConfig(radius_mm=1.2, curve_resolution=30),
        )

        self.assertEqual(len(plan.links), 4)
        self.assertTrue(all(link.end == common_node for link in plan.links))
        self.assertTrue(all(len(link.tube_contacts) == 2 for link in plan.links))
        self.assertEqual(
            {link.guide_indices for link in plan.links},
            {(1, 3), (2, 4)},
        )
        self.assertTrue(
            all(
                link.right_source is ConnectorEndpointSource.DISTAL_COMMON_NODE
                for link in plan.links
            )
        )
        multi_routes = tuple(
            route for link in plan.links for route in link.platform_avoidance_routes
        )
        self.assertEqual(len(multi_routes), 8)
        self.assertEqual(
            {(route.guide_index, route.side) for route in multi_routes},
            {
                (1, "left"),
                (1, "right"),
                (2, "left"),
                (2, "right"),
                (3, "left"),
                (3, "right"),
                (4, "left"),
                (4, "right"),
            },
        )

    def test_sleeve_contacts_use_edge_clearance_and_q_to_p_offsets(self):
        """上下 Q 应位于同一外侧母线，P 应遵循对应的嵌入规则。"""

        from types import SimpleNamespace

        sleeves = (_sleeve(1, -5.0), _sleeve(2, 5.0))
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

        left = plan.selections[0]
        self.assertAlmostEqual(left.lower.axial_position_mm, 3.3)
        self.assertAlmostEqual(left.upper.axial_position_mm, 11.7)
        self.assertEqual(left.lower.surface_contact, Vec3(-7.5, 0.0, 12.7))
        self.assertAlmostEqual(left.upper.surface_contact.x, -7.5)
        self.assertAlmostEqual(left.upper.surface_contact.y, 0.0)
        self.assertAlmostEqual(left.upper.surface_contact.z, 4.3)
        self.assertAlmostEqual(left.upper.local_wall_thickness_mm, 1.5)
        self.assertAlmostEqual(left.upper.tube_overlap_mm, 1.49)
        self.assertAlmostEqual(left.upper.centerline_offset_mm, 0.81)
        self.assertAlmostEqual(left.upper.position.x, -8.31)
        self.assertAlmostEqual(left.lower.tube_overlap_mm, 4.6)
        self.assertAlmostEqual(left.lower.centerline_offset_mm, -2.3)
        self.assertAlmostEqual(left.lower.position.x, -5.2)

        right = plan.selections[1]
        self.assertEqual(right.radial_direction, Vec3(1.0, 0.0, 0.0))
        self.assertEqual(right.lower.surface_contact, Vec3(7.5, 0.0, 12.7))
        self.assertAlmostEqual(right.lower.position.x, 5.2)

    def test_adds_three_y_spokes_from_optional_press_beam_plan(self):
        """启用混合锚点后应在原四梁之外加入三根共节点 Y 臂。"""
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
            SleeveAnchorSelectionConfig(connector_radius_mm=1.2),
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
        junction = Vec3(0.0, 0.0, 4.0)
        press = PressBeamPointPlan(
            PressBeamSleeveAnchor(
                1,
                "upper",
                sleeve_plan.selections[0].upper.surface_contact,
                sleeve_plan.selections[0].upper.surface_normal,
                sleeve_plan.selections[0].upper.position,
                Vec3(0.0, 0.0, 1.0),
            ),
            (
                PressBeamGuideAnchor((15,), 0.2, Vec3(-8, -3, 0), Vec3(0, 0, 1), Vec3(-8, -3, 0.9)),
                PressBeamGuideAnchor((25,), 0.8, Vec3(8, -3, 0), Vec3(0, 0, 1), Vec3(8, -3, 0.9)),
            ),
            junction,
            1.2,
            0.3,
            1.12,
            ((Vec3(-8, -3, 0), Vec3(-8, 3, 0)), (Vec3(8, -3, 0), Vec3(8, 3, 0))),
            (InnerSleeveScore(1, 15, -2.0), InnerSleeveScore(2, 15, 2.0)),
            0.0,
            60.0,
            junction.distance_to(sleeve_plan.selections[0].upper.position),
            0.0,
        )

        plan = link_selected_points(
            points,
            PointLinkingConfig(radius_mm=1.2),
            press,
        )

        self.assertTrue(plan.press_beam_links_included)
        self.assertEqual(len(plan.press_beam_links), 3)
        self.assertEqual(plan.press_beam_junction, junction)
        self.assertEqual(plan.press_beam_radius_mm, 1.2)
        self.assertTrue(all(link.end == junction for link in plan.press_beam_links))

    def test_sub_resolution_link_noise_reuses_the_existing_feature(self):
        zero = Vec3(0.0, 0.0, 0.0)
        old_link = PointLink(
            1,
            "lower",
            zero,
            zero,
            Vec3(0.0, 0.0, 1.0),
            Vec3(0.0, 0.0, 1.0),
            zero,
            Vec3(1.0, 0.0, 0.0),
            Vec3(2.0, 0.0, 0.0),
            (zero, Vec3(1.0, 0.0, 0.0), Vec3(2.0, 0.0, 0.0)),
            1,
        )
        old_plan = PointLinkingPlan((old_link,), 2.3, 32, True)
        noisy_link = replace(
            old_link,
            centerline=tuple(
                Vec3(point.x + 1e-5, point.y, point.z) for point in old_link.centerline
            ),
        )
        current = replace(old_plan, links=(noisy_link,))

        stabilized = _reuse_numerically_unchanged_links(current, old_plan)

        self.assertIs(stabilized.links[0], old_link)


if __name__ == "__main__":
    unittest.main()

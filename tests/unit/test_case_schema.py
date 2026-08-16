"""紧凑病例设计语义的展开回归。"""

from __future__ import annotations

import unittest

from twin_guide.case_schema import normalize_case_definition
from twin_guide.errors import ConfigurationError


def _design(**values: object) -> dict[str, object]:
    return {"design": values}


class CompactCaseSchemaTests(unittest.TestCase):
    def test_observation_window_defaults_and_overrides_expand(self) -> None:
        compact = _design(
            observation_windows=[
                {
                    "id": "first",
                    "fdi": [11, 21],
                    "extent_mode": "center_to_center",
                    "axis_drop_mm": 0.2,
                    "sweep_angle_deg": 90.0,
                },
                {
                    "id": "second",
                    "fdi": [24, 25],
                    "extent_mode": "full_teeth",
                    "axis_drop_mm": 0.5,
                    "sweep_angle_deg": 80.0,
                    "axis_sections": 31,
                    "wall_overcut_mm": 0.4,
                },
            ]
        )

        windows = normalize_case_definition(compact)["design"][
            "observation_windows"
        ]

        self.assertEqual((windows[0]["start_fdi"], windows[0]["end_fdi"]), (11, 21))
        self.assertEqual(windows[0]["extent_mode"], "center_to_center")
        self.assertEqual(windows[0]["opening_geometry"], "axis_sweep")
        self.assertEqual(windows[1]["extent_mode"], "full_teeth")
        self.assertEqual(windows[1]["axis_drop_mm"], 0.5)
        self.assertEqual(windows[1]["sweep_angle_deg"], 80.0)
        self.assertEqual(windows[1]["axis_sections"], 31)
        self.assertEqual(windows[1]["wall_overcut_mm"], 0.4)

    def test_observation_window_rejects_unknown_override(self) -> None:
        compact = _design(
            observation_windows=[
                {"fdi": [11, 21], "unknown_mm": 1.0}
            ]
        )
        with self.assertRaisesRegex(ConfigurationError, "未知字段"):
            normalize_case_definition(compact)

    def test_common_guide_endpoint_expands_to_two_sides(self) -> None:
        compact = _design(
            guide_anchors={
                "mode": "tooth_section_trajectory",
                "anchors": [
                    {"side": "u_side", "fdi": 13, "angle": 70.0},
                    {"side": "back_u_side", "fdi": 13, "angle": 90.0},
                    {"side": "u_side", "fdi": [22, 23], "angle": 65.0},
                    {"side": "back_u_side", "fdi": [22, 23], "angle": 85.0},
                ],
            }
        )

        guide = normalize_case_definition(compact)["design"]["guide_anchors"]

        self.assertEqual(len(guide["anchors"]), 4)
        self.assertEqual(
            [item["id"] for item in guide["anchors"]],
            [
                "station_1_u",
                "station_1_back_u",
                "station_2_u",
                "station_2_back_u",
            ],
        )
        self.assertEqual(
            guide["anchors"][0]["station"],
            {"type": "tooth_center", "fdi": 13},
        )
        self.assertEqual(
            guide["anchors"][2]["station"],
            {"type": "tooth_pair_midpoint", "fdis": [22, 23]},
        )

    def test_independent_guide_sides_preserve_distinct_stations(self) -> None:
        compact = _design(
            guide_anchors={
                "mode": "adjacent_two_implant_continuous_paths",
                "anchors": [
                    {"side": "u_side", "fdi": 16, "angle": 70.0},
                    {"side": "back_u_side", "fdi": 16, "angle": 80.0},
                    {"side": "u_side", "fdi": [12, 11], "angle": 50.0},
                    {"side": "back_u_side", "fdi": 13, "angle": 80.0},
                ],
            }
        )

        anchors = normalize_case_definition(compact)["design"]["guide_anchors"][
            "anchors"
        ]

        self.assertEqual(
            anchors[2]["station"],
            {"type": "tooth_pair_midpoint", "fdis": [12, 11]},
        )
        self.assertEqual(
            anchors[3]["station"],
            {"type": "tooth_center", "fdi": 13},
        )

    def test_terminal_node_expands_and_keeps_overrides(self) -> None:
        compact = _design(
            guide_anchors={
                "mode": "adjacent_two_implant_terminal_distal_node_paths",
                "anchors": [
                    {"side": "u_side", "fdi": [15, 14], "angle": 70.0},
                    {"side": "back_u_side", "fdi": [15, 14], "angle": 90.0},
                ],
                "terminal": {
                    "missing_fdi": 17,
                    "reference_fdi": 15,
                    "implant_fdis": [16, 17],
                    "overrides": {"node_radius_factor": 1.2},
                },
            }
        )

        terminal = normalize_case_definition(compact)["design"]["guide_anchors"][
            "terminal_distal_common_node"
        ]

        self.assertEqual(terminal["reference_neighbor_fdi"], 15)
        self.assertEqual(terminal["implant_fdis"], [16, 17])
        self.assertEqual(terminal["node_radius_factor"], 1.2)

    def test_guide_mode_controls_endpoint_count(self) -> None:
        compact = _design(
            guide_anchors={
                "mode": "terminal_distal_common_node",
                "anchors": [],
            }
        )
        with self.assertRaisesRegex(ConfigurationError, "2 个 anchors"):
            normalize_case_definition(compact)

    def test_press_stations_expand_scalar_and_pair_teeth(self) -> None:
        compact = _design(
            press_beam={
                "mode": "inner_sleeve_upper_y",
                "anchors": [
                    {"fdi": 12, "angle": 45.0},
                    {"fdi": [24, 25], "angle": 50.0},
                ],
            }
        )

        press = normalize_case_definition(compact)["design"]["press_beam"]

        self.assertEqual(
            press["stations"][0],
            {"type": "tooth_center", "fdi": 12, "ray_angle_degrees": 45.0},
        )
        self.assertEqual(
            press["stations"][1],
            {
                "type": "tooth_pair_midpoint",
                "fdis": [24, 25],
                "ray_angle_degrees": 50.0,
            },
        )

    def test_press_common_and_endpoint_overrides_are_preserved(self) -> None:
        compact = _design(
            press_beam={
                "mode": "three_tooth_anchors_y",
                "anchors": [
                    {"fdi": 13, "angle": 45.0},
                    {"fdi": 23, "angle": 45.0},
                    {"fdi": [11, 21], "angle": 45.0},
                ],
                "overrides": {
                    "diameter_mm": 5.0,
                    "guide_endpoint": {
                        "foot_major_radius_mm": 2.4,
                        "foot_minor_radius_mm": 1.7,
                    },
                },
            }
        )

        press = normalize_case_definition(compact)["design"]["press_beam"]

        self.assertEqual(press["diameter_mm"], 5.0)
        self.assertEqual(press["guide_endpoint"]["foot_major_radius_mm"], 2.4)
        self.assertEqual(press["guide_endpoint"]["foot_minor_radius_mm"], 1.7)

    def test_terminal_press_extension_expands(self) -> None:
        compact = _design(
            press_beam={
                "mode": "terminal_u_extension_anchor_y",
                "anchors": [
                    {"fdi": [11, 21], "angle": 45.0},
                    {"fdi": [16, 15], "angle": 45.0},
                ],
                "extension": {
                    "segment": "u_side",
                    "overrides": {"overlap_mm": 1.0},
                },
            }
        )

        extension = normalize_case_definition(compact)["design"]["press_beam"][
            "extension_anchor"
        ]

        self.assertEqual(extension, {"segment": "u_side", "overlap_mm": 1.0})

    def test_normalization_does_not_mutate_input(self) -> None:
        compact = _design(
            observation_windows=[{"id": "first", "fdi": [11, 21]}]
        )

        normalize_case_definition(compact)

        self.assertEqual(
            compact,
            _design(observation_windows=[{"id": "first", "fdi": [11, 21]}]),
        )


if __name__ == "__main__":
    unittest.main()

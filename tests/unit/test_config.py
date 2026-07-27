import json
import math
import tempfile
import unittest
from pathlib import Path

from twin_guide.config import (
    AlgorithmProfile,
    CaseConfig,
    ConnectorMode,
    Jaw,
    ObservationWindowMode,
    SleeveGeometryMode,
    case_occlusal_axis,
    require_production_review,
)
from twin_guide.errors import ConfigurationError


class CaseConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.case_directory = Path(self.temporary_directory.name)
        for filename in (
            "template.stl",
            "guide_sleeve_assembly.stl",
            "patient_dentition.stl",
            "handpiece.stl",
        ):
            (self.case_directory / filename).touch()
        (self.case_directory / "stop_report.json").write_text("{}", encoding="utf-8")

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _valid_config_data(self) -> dict[str, object]:
        return {
            "case_id": "case_01",
            "jaw": "upper",
            "inputs": {
                "template": "template.stl",
                "guide_sleeve_assembly": "guide_sleeve_assembly.stl",
                "patient_dentition": "patient_dentition.stl",
            },
            "sleeve": {
                "inner_diameter_mm": 2.10,
                "outer_diameter_mm": 4.3,
                "height_mm": 16.373,
                "platform_width_mm": 2.036,
                "platform_height_mm": 9.875,
                "closed_bore_height_mm": 4.777,
                "inner_arc_angle_degrees": 264.934,
                "outer_arc_angle_degrees": 211.684,
            },
            "geometry": {
                "channel_axial_margin_mm": 5.0,
                "connector_diameter_mm": 2.3,
                "fusion_voxel_size_mm": 0.2,
            },
            "windows": {
                "operation_tangent_margin_mm": 1.0,
                "operation_bitangent_margin_mm": 3.0,
            },
            "render": {"width_px": 640, "height_px": 480},
            "output_directory": "output",
        }

    def _write_config(self, config_data: dict[str, object]) -> Path:
        config_path = self.case_directory / "case.json"
        config_path.write_text(json.dumps(config_data), encoding="utf-8")
        return config_path

    def test_loads_and_resolves_valid_configuration(self):
        config = CaseConfig.from_json(self._write_config(self._valid_config_data()))

        self.assertEqual(config.case_id, "case_01")
        self.assertEqual(config.jaw.value, "upper")
        self.assertEqual(config.jaw.occlusal_axis_sign, -1.0)
        self.assertEqual(Jaw.LOWER.occlusal_axis_sign, 1.0)
        self.assertEqual(config.sleeve.inner_diameter_mm, 2.10)
        self.assertEqual(config.sleeve.inner_radius_mm, 1.05)
        self.assertEqual(config.sleeve.outer_diameter_mm, 4.3)
        self.assertEqual(config.sleeve.outer_radius_mm, 2.15)
        self.assertIs(config.sleeve_geometry_mode, SleeveGeometryMode.GENERATED)
        self.assertIs(config.algorithms.profile, AlgorithmProfile.CURRENT)
        self.assertIs(
            config.algorithms.observation_window,
            ObservationWindowMode.FDI_AXIS_SWEEP,
        )
        self.assertIs(config.algorithms.connector, ConnectorMode.CONTINUOUS_FRAME)
        self.assertEqual(config.sleeve.height_mm, 16.373)
        self.assertEqual(config.sleeve.platform_width_mm, 2.036)
        self.assertEqual(config.sleeve.platform_height_mm, 9.875)
        self.assertEqual(config.sleeve.closed_bore_height_mm, 4.777)
        self.assertEqual(config.sleeve.inner_arc_angle_degrees, 264.934)
        self.assertEqual(config.sleeve.outer_arc_angle_degrees, 211.684)
        self.assertEqual(config.geometry.connector_diameter_mm, 2.3)
        self.assertEqual(config.geometry.connector_radius_mm, 1.15)
        self.assertEqual(config.geometry.connector_dental_clearance_mm, 0.20)
        self.assertEqual(
            config.geometry.connector_guide_endpoint.root_radius_factor,
            1.08,
        )
        self.assertEqual(
            config.geometry.connector_guide_endpoint.foot_major_radius_mm,
            3.0,
        )
        self.assertEqual(config.windows.operation_bitangent_margin_mm, 3.0)
        self.assertEqual(config.windows.operation_axial_margin_mm, 5.0)
        self.assertEqual(config.windows.operation_corner_radius_mm, 1.0)
        self.assertEqual(config.inputs.template, (self.case_directory / "template.stl").resolve())
        self.assertEqual(
            config.inputs.patient_dentition,
            (self.case_directory / "patient_dentition.stl").resolve(),
        )
        self.assertEqual(config.output_directory, (self.case_directory / "output").resolve())
        self.assertIsNone(config.tooth_identification)
        self.assertEqual(config.handpiece_avoidance, ())
        self.assertEqual(config.guide_anchors.mode.value, "nearest")
        self.assertEqual(config.guide_anchors.stations, ())
        self.assertFalse(config.guide_terminal_u_extension.enabled)
        self.assertEqual(config.press_beam.mode.value, "disabled")
        self.assertEqual(config.press_beam.stations, ())
        self.assertEqual(config.press_beam.diameter_mm, 4.60)
        self.assertEqual(config.press_beam.junction_sleeve_distance_mm, 6.0)
        self.assertEqual(config.press_beam.junction_axial_lift_mm, 2.0)
        self.assertEqual(config.press_beam.minimum_junction_angle_degrees, 25.0)
        self.assertIsNone(config.press_beam.sleeve_anchor_selection)
        self.assertEqual(config.press_beam.guide_endpoint.root_radius_factor, 1.08)
        self.assertEqual(config.press_beam.guide_endpoint.foot_major_radius_mm, 3.0)
        self.assertEqual(config.windows.observation_axis_drop_mm, 0.2)
        self.assertEqual(config.windows.observation_sweep_angle_degrees, 90.0)
        self.assertEqual(
            config.windows.observation_local_failure_drop_targets_mm,
            (0.5, 1.0, 2.0),
        )

    def test_loads_input_sleeve_geometry_mode_from_case_yaml(self):
        (self.case_directory / "case.yaml").write_text(
            """
design:
  sleeve_geometry:
    mode: input
""",
            encoding="utf-8",
        )
        config_data = self._valid_config_data()
        config_data["tooth_identification"] = {"case_yaml": "case.yaml"}

        config = CaseConfig.from_json(self._write_config(config_data))

        self.assertIs(config.sleeve_geometry_mode, SleeveGeometryMode.INPUT)

    def test_rejects_unknown_sleeve_geometry_mode(self):
        (self.case_directory / "case.yaml").write_text(
            """
design:
  sleeve_geometry:
    mode: legacy
""",
            encoding="utf-8",
        )
        config_data = self._valid_config_data()
        config_data["tooth_identification"] = {"case_yaml": "case.yaml"}

        with self.assertRaisesRegex(ConfigurationError, "generated 或 input"):
            CaseConfig.from_json(self._write_config(config_data))

    def test_loads_legacy_merge_algorithm_profile_from_case_yaml(self):
        (self.case_directory / "case.yaml").write_text(
            """
design:
  algorithms:
    profile: legacy_merge
""",
            encoding="utf-8",
        )
        config_data = self._valid_config_data()
        config_data["tooth_identification"] = {"case_yaml": "case.yaml"}

        config = CaseConfig.from_json(self._write_config(config_data))

        self.assertIs(config.algorithms.profile, AlgorithmProfile.LEGACY_MERGE)
        self.assertIs(
            config.algorithms.observation_window,
            ObservationWindowMode.SURFACE_NOTCH,
        )
        self.assertIs(config.algorithms.connector, ConnectorMode.INDEPENDENT_BEZIER)

    def test_algorithm_profile_allows_explicit_stage_override(self):
        (self.case_directory / "case.yaml").write_text(
            """
design:
  algorithms:
    profile: legacy_merge
    observation_window: fdi_axis_sweep
    connector: continuous_frame
""",
            encoding="utf-8",
        )
        config_data = self._valid_config_data()
        config_data["tooth_identification"] = {"case_yaml": "case.yaml"}

        config = CaseConfig.from_json(self._write_config(config_data))

        self.assertIs(
            config.algorithms.observation_window,
            ObservationWindowMode.FDI_AXIS_SWEEP,
        )
        self.assertIs(config.algorithms.connector, ConnectorMode.CONTINUOUS_FRAME)

    def test_rejects_unknown_algorithm_profile(self):
        (self.case_directory / "case.yaml").write_text(
            """
design:
  algorithms:
    profile: experimental_unknown
""",
            encoding="utf-8",
        )
        config_data = self._valid_config_data()
        config_data["tooth_identification"] = {"case_yaml": "case.yaml"}

        with self.assertRaisesRegex(ConfigurationError, "current 或 legacy_merge"):
            CaseConfig.from_json(self._write_config(config_data))

    def test_connector_diameter_defaults_to_4_60_mm(self):
        config_data = self._valid_config_data()
        geometry_data = config_data["geometry"]
        self.assertIsInstance(geometry_data, dict)
        del geometry_data["connector_diameter_mm"]

        config = CaseConfig.from_json(self._write_config(config_data))

        self.assertEqual(config.geometry.connector_diameter_mm, 4.60)
        self.assertEqual(config.geometry.connector_radius_mm, 2.30)

    def test_operation_bitangent_margin_defaults_to_3_mm(self):
        config_data = self._valid_config_data()
        windows_data = config_data["windows"]
        self.assertIsInstance(windows_data, dict)
        del windows_data["operation_bitangent_margin_mm"]

        config = CaseConfig.from_json(self._write_config(config_data))

        self.assertEqual(config.windows.operation_bitangent_margin_mm, 3.0)

    def test_case_yaml_operation_window_parameters_override_json_defaults(self):
        """病例 YAML 是操作窗范围参数的正式病例级来源。"""

        (self.case_directory / "case.yaml").write_text(
            """
planning:
  operation_windows:
    mode: per_implant_site
    center_mode: paired_sleeve_operation_feature
    axis_mode: paired_sleeve_average_axis
    tangent_margin_mm: 0.75
    bitangent_margin_mm: 1.50
    axial_margin_mm: 4.25
    corner_radius_mm: 0.60
    overlap_rule: union_cutters
    cut_target: guide_template_only
    sites: []
""",
            encoding="utf-8",
        )
        config_data = self._valid_config_data()
        config_data["tooth_identification"] = {"case_yaml": "case.yaml"}

        config = CaseConfig.from_json(self._write_config(config_data))

        self.assertEqual(config.windows.operation_tangent_margin_mm, 0.75)
        self.assertEqual(config.windows.operation_bitangent_margin_mm, 1.50)
        self.assertEqual(config.windows.operation_axial_margin_mm, 4.25)
        self.assertEqual(config.windows.operation_corner_radius_mm, 0.60)

    def test_rejects_unsupported_case_yaml_operation_window_mode(self):
        (self.case_directory / "case.yaml").write_text(
            """
planning:
  operation_windows:
    mode: custom_window_mode
    tangent_margin_mm: 1.0
""",
            encoding="utf-8",
        )
        config_data = self._valid_config_data()
        config_data["tooth_identification"] = {"case_yaml": "case.yaml"}

        with self.assertRaisesRegex(ConfigurationError, "mode 当前仅支持"):
            CaseConfig.from_json(self._write_config(config_data))

    def test_loads_optional_handpiece_avoidance(self):
        config_data = self._valid_config_data()
        config_data["handpiece_avoidance"] = {
            "handpiece": "handpiece.stl",
            "stop_report": "stop_report.json",
        }

        config = CaseConfig.from_json(self._write_config(config_data))

        self.assertEqual(len(config.handpiece_avoidance), 1)
        parameters = config.handpiece_avoidance[0]
        self.assertEqual(parameters.avoidance_id, "handpiece_1")
        self.assertEqual(parameters.maximum_angle_degrees, 5.0)
        self.assertEqual(parameters.pose_samples, 41)
        self.assertEqual(parameters.union_batch_size, 7)
        self.assertEqual(parameters.extra_clearance_mm, 0.0)

    def test_loads_multiple_handpiece_avoidances(self):
        config_data = self._valid_config_data()
        config_data["handpiece_avoidance"] = [
            {
                "id": "region_1",
                "handpiece": "handpiece.stl",
                "stop_report": "stop_report.json",
            },
            {
                "id": "region_2",
                "handpiece": "handpiece.stl",
                "stop_report": "stop_report.json",
            },
        ]

        config = CaseConfig.from_json(self._write_config(config_data))

        self.assertEqual(
            tuple(item.avoidance_id for item in config.handpiece_avoidance),
            ("region_1", "region_2"),
        )

    def test_rejects_even_handpiece_pose_count(self):
        config_data = self._valid_config_data()
        config_data["handpiece_avoidance"] = {
            "handpiece": "handpiece.stl",
            "stop_report": "stop_report.json",
            "pose_samples": 40,
        }

        with self.assertRaisesRegex(ConfigurationError, "奇数"):
            CaseConfig.from_json(self._write_config(config_data))

    def test_loads_optional_unified_tooth_identification_case(self):
        """解析统一牙位识别与导板映射使用的病例 YAML。"""

        (self.case_directory / "case.yaml").write_text("case: {}\n", encoding="utf-8")
        config_data = self._valid_config_data()
        config_data["tooth_identification"] = {
            "case_yaml": "case.yaml",
        }

        config = CaseConfig.from_json(self._write_config(config_data))

        self.assertIsNotNone(config.tooth_identification)
        assert config.tooth_identification is not None
        self.assertEqual(
            config.tooth_identification.case_yaml,
            (self.case_directory / "case.yaml").resolve(),
        )

    def test_loads_normalized_case_occlusal_axis(self):
        """导管阶段应采用病例确认的真实牙合方向，而非仅依赖上下颌。"""

        (self.case_directory / "case.yaml").write_text(
            """
anatomy:
  orientation:
    occlusal_axis: [0.0, 0.0, -2.0]
""",
            encoding="utf-8",
        )
        config_data = self._valid_config_data()
        config_data["tooth_identification"] = {"case_yaml": "case.yaml"}

        config = CaseConfig.from_json(self._write_config(config_data))

        self.assertEqual(case_occlusal_axis(config), (0.0, 0.0, -1.0))

    def test_loads_named_case_occlusal_axis(self):
        (self.case_directory / "case.yaml").write_text(
            """
anatomy:
  orientation:
    occlusal_axis: +Z
""",
            encoding="utf-8",
        )
        config_data = self._valid_config_data()
        config_data["tooth_identification"] = {"case_yaml": "case.yaml"}

        config = CaseConfig.from_json(self._write_config(config_data))

        self.assertEqual(case_occlusal_axis(config), (0.0, 0.0, 1.0))

    def test_production_review_rejects_explicit_pending_statuses(self):
        (self.case_directory / "case.yaml").write_text(
            """
anatomy:
  review_status: pending_user_input
review:
  anatomy_status: pending_user_input
  guide_anchor_parameters_status: configured
""",
            encoding="utf-8",
        )
        config_data = self._valid_config_data()
        config_data["tooth_identification"] = {"case_yaml": "case.yaml"}
        config = CaseConfig.from_json(self._write_config(config_data))

        with self.assertRaisesRegex(ConfigurationError, "anatomy.review_status"):
            require_production_review(config)

    def test_production_review_accepts_confirmed_or_configured_statuses(self):
        (self.case_directory / "case.yaml").write_text(
            """
anatomy:
  review_status: confirmed_after_user_correction
review:
  anatomy_status: validated
  guide_anchor_parameters_status: configured
""",
            encoding="utf-8",
        )
        config_data = self._valid_config_data()
        config_data["tooth_identification"] = {"case_yaml": "case.yaml"}
        config = CaseConfig.from_json(self._write_config(config_data))

        require_production_review(config)

    def test_loads_tooth_section_trajectory_anchor_stations(self):
        """解析单牙中心与相邻双牙中点的导板锚点站位。"""

        (self.case_directory / "case.yaml").write_text(
            "case: {}\n", encoding="utf-8"
        )
        config_data = self._valid_config_data()
        config_data["tooth_identification"] = {"case_yaml": "case.yaml"}
        config_data["guide_anchors"] = {
            "mode": "tooth_section_trajectory",
            "stations": [
                {"type": "tooth_center", "fdi": 13},
                {"type": "tooth_pair_midpoint", "fdis": [22, 23]},
            ],
        }

        config = CaseConfig.from_json(self._write_config(config_data))

        self.assertEqual(config.guide_anchors.mode.value, "tooth_section_trajectory")
        self.assertEqual(
            tuple(station.fdis for station in config.guide_anchors.stations),
            ((13,), (22, 23)),
        )
        self.assertEqual(config.guide_anchors.u_side_ray_angle_degrees, 70.0)
        self.assertEqual(config.guide_anchors.back_u_side_ray_angle_degrees, 90.0)
        self.assertEqual(len(config.guide_anchors.anchors), 4)
        self.assertEqual(
            tuple(anchor.tooth_station.fdis for anchor in config.guide_anchors.anchors),
            ((13,), (13,), (22, 23), (22, 23)),
        )

    def test_loads_independent_guide_anchor_tooth_trajectories(self):
        """每个导板锚点可独立选择牙位轨迹、侧别和射线角度。"""

        (self.case_directory / "case.yaml").write_text(
            """
design:
  guide_anchors:
    mode: tooth_section_trajectory
    anchors:
      - {id: left_u, endpoint: left, side: u_side, station: {type: tooth_center, fdi: 16}, ray_angle_degrees: 65.0}
      - {id: left_back, endpoint: left, side: back_u_side, station: {type: tooth_pair_midpoint, fdis: [15, 14]}, ray_angle_degrees: 92.0}
      - {id: right_u, endpoint: right, side: u_side, station: {type: tooth_center, fdi: 12}, ray_angle_degrees: 70.0}
      - {id: right_back, endpoint: right, side: back_u_side, station: {type: tooth_center, fdi: 11}, ray_angle_degrees: 88.0}
""",
            encoding="utf-8",
        )
        config_data = self._valid_config_data()
        config_data["tooth_identification"] = {"case_yaml": "case.yaml"}

        config = CaseConfig.from_json(self._write_config(config_data))

        self.assertEqual(config.guide_anchors.stations, ())
        self.assertEqual(
            tuple(anchor.anchor_id for anchor in config.guide_anchors.anchors),
            ("left_u", "left_back", "right_u", "right_back"),
        )
        self.assertEqual(
            tuple(anchor.tooth_station.fdis for anchor in config.guide_anchors.anchors),
            ((16,), (15, 14), (12,), (11,)),
        )
        self.assertEqual(
            tuple(anchor.ray_angle_degrees for anchor in config.guide_anchors.anchors),
            (65.0, 92.0, 70.0, 88.0),
        )

    def test_rejects_independent_endpoint_without_both_sides(self):
        (self.case_directory / "case.yaml").write_text("case: {}\n", encoding="utf-8")
        config_data = self._valid_config_data()
        config_data["tooth_identification"] = {"case_yaml": "case.yaml"}
        config_data["guide_anchors"] = {
            "mode": "tooth_section_trajectory",
            "anchors": [
                {
                    "id": "left_u",
                    "endpoint": "left",
                    "side": "u_side",
                    "station": {"type": "tooth_center", "fdi": 16},
                    "ray_angle_degrees": 70.0,
                },
                {
                    "id": "left_u_2",
                    "endpoint": "left",
                    "side": "u_side",
                    "station": {"type": "tooth_center", "fdi": 15},
                    "ray_angle_degrees": 75.0,
                },
                {
                    "id": "right_u",
                    "endpoint": "right",
                    "side": "u_side",
                    "station": {"type": "tooth_center", "fdi": 12},
                    "ray_angle_degrees": 70.0,
                },
                {
                    "id": "right_back",
                    "endpoint": "right",
                    "side": "back_u_side",
                    "station": {"type": "tooth_center", "fdi": 12},
                    "ray_angle_degrees": 90.0,
                },
            ],
        }

        with self.assertRaisesRegex(ConfigurationError, "各配置一个 U 侧和背 U 侧"):
            CaseConfig.from_json(self._write_config(config_data))

    def test_loads_anchor_design_from_case_yaml_and_merges_engineering_fields(self):
        """YAML 提供锚点语义，JSON 保留按压梁工程参数。"""

        (self.case_directory / "case.yaml").write_text(
            """
design:
  guide_anchors:
    mode: tooth_section_trajectory
    u_side_ray_angle_degrees: 65.0
    back_u_side_ray_angle_degrees: 95.0
    stations:
      - {type: tooth_center, fdi: 48}
      - {type: tooth_pair_midpoint, fdis: [46, 45]}
  press_beam:
    mode: three_tooth_anchors_y
    stations:
      - {type: tooth_pair_midpoint, fdis: [45, 44], ray_angle_degrees: 75.0}
      - {type: tooth_pair_midpoint, fdis: [31, 32], ray_angle_degrees: 45.0}
      - {type: tooth_pair_midpoint, fdis: [34, 35], ray_angle_degrees: 75.0}
""",
            encoding="utf-8",
        )
        config_data = self._valid_config_data()
        config_data["tooth_identification"] = {"case_yaml": "case.yaml"}
        config_data["press_beam"] = {
            "diameter_mm": 4.6,
            "junction_axial_lift_mm": 2.0,
        }

        config = CaseConfig.from_json(self._write_config(config_data))

        self.assertEqual(config.guide_anchors.mode.value, "tooth_section_trajectory")
        self.assertEqual(
            tuple(station.fdis for station in config.guide_anchors.stations),
            ((48,), (46, 45)),
        )
        self.assertEqual(config.guide_anchors.u_side_ray_angle_degrees, 65.0)
        self.assertEqual(config.guide_anchors.back_u_side_ray_angle_degrees, 95.0)
        self.assertEqual(config.press_beam.mode.value, "three_tooth_anchors_y")
        self.assertEqual(config.press_beam.diameter_mm, 4.6)
        self.assertEqual(
            tuple(station.ray_angle_degrees for station in config.press_beam.stations),
            (75.0, 45.0, 75.0),
        )

    def test_rejects_duplicate_anchor_fields_in_json_and_case_yaml(self):
        """同一设计字段不允许存在两个配置来源。"""

        (self.case_directory / "case.yaml").write_text(
            """
design:
  guide_anchors:
    mode: tooth_section_trajectory
""",
            encoding="utf-8",
        )
        config_data = self._valid_config_data()
        config_data["tooth_identification"] = {"case_yaml": "case.yaml"}
        config_data["guide_anchors"] = {"mode": "nearest"}

        with self.assertRaisesRegex(ConfigurationError, "JSON.*case.yaml.*mode"):
            CaseConfig.from_json(self._write_config(config_data))

    def test_rejects_duplicate_keys_inside_case_yaml(self):
        """严格 YAML 加载器不得静默采用同名键的最后一个值。"""

        (self.case_directory / "case.yaml").write_text(
            """
design:
  press_beam:
    mode: disabled
    mode: three_tooth_anchors_y
""",
            encoding="utf-8",
        )
        config_data = self._valid_config_data()
        config_data["tooth_identification"] = {"case_yaml": "case.yaml"}

        with self.assertRaisesRegex(ConfigurationError, "duplicate key.*mode"):
            CaseConfig.from_json(self._write_config(config_data))

    def test_rejects_tooth_section_mode_without_two_stations(self):
        config_data = self._valid_config_data()
        config_data["guide_anchors"] = {
            "mode": "tooth_section_trajectory",
            "stations": [{"type": "tooth_center", "fdi": 13}],
        }

        with self.assertRaisesRegex(ConfigurationError, "必须配置 2 个端部"):
            CaseConfig.from_json(self._write_config(config_data))

    def test_loads_inner_sleeve_upper_y_press_beam(self):
        """按压梁只配置两个牙位，导管由运行时自动选择牙弓内侧者。"""

        (self.case_directory / "case.yaml").write_text("case: {}\n", encoding="utf-8")
        config_data = self._valid_config_data()
        config_data["tooth_identification"] = {"case_yaml": "case.yaml"}
        config_data["press_beam"] = {
            "mode": "inner_sleeve_upper_y",
            "diameter_mm": 4.6,
            "guide_overlap_mm": 0.3,
            "junction_sleeve_distance_mm": 6.0,
            "minimum_junction_angle_degrees": 32.0,
            "sleeve_anchor_selection": {
                "candidate_scope": "inner_sleeve_upper_per_implant_site",
                "distance_score": "maximin_to_two_guide_anchors",
                "tie_breaker": "larger_sum_distance",
            },
            "stations": [
                {
                    "type": "tooth_pair_midpoint",
                    "fdis": [13, 12],
                    "ray_angle_degrees": 75.0,
                },
                {
                    "type": "tooth_pair_midpoint",
                    "fdis": [24, 25],
                    "ray_angle_degrees": 45.0,
                },
            ],
            "guide_endpoint": {
                "root_radius_factor": 1.08,
                "foot_major_radius_mm": 3.0,
                "foot_minor_radius_mm": 2.2,
            },
        }

        config = CaseConfig.from_json(self._write_config(config_data))

        self.assertEqual(config.press_beam.mode.value, "inner_sleeve_upper_y")
        self.assertEqual(
            tuple(station.fdis for station in config.press_beam.stations),
            ((13, 12), (24, 25)),
        )
        self.assertEqual(config.press_beam.radius_mm, 2.3)
        self.assertEqual(config.press_beam.junction_sleeve_distance_mm, 6.0)
        self.assertEqual(config.press_beam.minimum_junction_angle_degrees, 32.0)
        self.assertIsNotNone(config.press_beam.sleeve_anchor_selection)
        assert config.press_beam.sleeve_anchor_selection is not None
        self.assertEqual(
            config.press_beam.sleeve_anchor_selection.distance_score,
            "maximin_to_two_guide_anchors",
        )
        self.assertEqual(
            tuple(station.ray_angle_degrees for station in config.press_beam.stations),
            (75.0, 45.0),
        )
        self.assertEqual(config.press_beam.guide_endpoint.root_radius_factor, 1.08)
        self.assertEqual(config.press_beam.guide_endpoint.foot_minor_radius_mm, 2.2)

    def test_rejects_inner_sleeve_y_without_two_tooth_stations(self):
        config_data = self._valid_config_data()
        config_data["press_beam"] = {
            "mode": "inner_sleeve_upper_y",
            "stations": [{"type": "tooth_center", "fdi": 15}],
        }

        with self.assertRaisesRegex(ConfigurationError, "必须配置两个牙位站位"):
            CaseConfig.from_json(self._write_config(config_data))

    def test_loads_three_tooth_anchor_y_press_beam(self):
        """全牙位 Y 模式必须解析三个站位及牙合方向抬高距离。"""

        (self.case_directory / "case.yaml").write_text("case: {}\n", encoding="utf-8")
        config_data = self._valid_config_data()
        config_data["tooth_identification"] = {"case_yaml": "case.yaml"}
        config_data["press_beam"] = {
            "mode": "three_tooth_anchors_y",
            "junction_axial_lift_mm": 2.5,
            "stations": [
                {
                    "type": "tooth_center",
                    "fdi": 45,
                    "ray_angle_degrees": 75.0,
                },
                {
                    "type": "tooth_pair_midpoint",
                    "fdis": [31, 32],
                    "ray_angle_degrees": 45.0,
                },
                {
                    "type": "tooth_pair_midpoint",
                    "fdis": [36, 37],
                    "ray_angle_degrees": 120.0,
                },
            ],
        }

        config = CaseConfig.from_json(self._write_config(config_data))

        self.assertEqual(config.press_beam.mode.value, "three_tooth_anchors_y")
        self.assertEqual(
            tuple(station.fdis for station in config.press_beam.stations),
            ((45,), (31, 32), (36, 37)),
        )
        self.assertEqual(config.press_beam.junction_axial_lift_mm, 2.5)
        self.assertIsNone(config.press_beam.sleeve_anchor_selection)
        self.assertEqual(
            tuple(
                station.ray_angle_degrees
                for station in config.press_beam.stations
            ),
            (75.0, 45.0, 120.0),
        )

    def test_rejects_enabled_press_beam_without_explicit_angles(self):
        config_data = self._valid_config_data()
        config_data["press_beam"] = {
            "mode": "inner_sleeve_upper_y",
            "stations": [
                {"type": "tooth_pair_midpoint", "fdis": [13, 12]},
                {"type": "tooth_pair_midpoint", "fdis": [24, 25]},
            ],
        }

        with self.assertRaisesRegex(ConfigurationError, "显式配置 ray_angle_degrees"):
            CaseConfig.from_json(self._write_config(config_data))

    def test_accepts_single_tooth_press_beam_anchor(self):
        (self.case_directory / "case.yaml").write_text("case: {}\n", encoding="utf-8")
        config_data = self._valid_config_data()
        config_data["tooth_identification"] = {"case_yaml": "case.yaml"}
        config_data["press_beam"] = {
            "mode": "inner_sleeve_upper_y",
            "stations": [
                {
                    "type": "tooth_center",
                    "fdi": 13,
                    "ray_angle_degrees": 75.0,
                },
                {
                    "type": "tooth_pair_midpoint",
                    "fdis": [24, 25],
                    "ray_angle_degrees": 45.0,
                },
            ],
        }

        config = CaseConfig.from_json(self._write_config(config_data))

        self.assertEqual(
            tuple(station.fdis for station in config.press_beam.stations),
            ((13,), (24, 25)),
        )

    def test_accepts_terminal_distal_common_node(self):
        (self.case_directory / "case.yaml").write_text(
            """
anatomy:
  present_teeth: [16, 15]
  missing_teeth: [17]
""",
            encoding="utf-8",
        )
        config_data = self._valid_config_data()
        config_data["tooth_identification"] = {"case_yaml": "case.yaml"}
        config_data["guide_anchors"] = {
            "mode": "terminal_distal_common_node",
            "stations": [
                {"type": "tooth_pair_midpoint", "fdis": [16, 15]},
            ],
            "u_side_ray_angle_degrees": 70.0,
            "back_u_side_ray_angle_degrees": 90.0,
            "terminal_distal_common_node": {
                "missing_fdi": 17,
                "reference_neighbor_fdi": 16,
                "node_radius_factor": 1.12,
                "distal_offset_sleeve_diameters": 2.0,
            },
        }

        config = CaseConfig.from_json(self._write_config(config_data))

        self.assertEqual(
            config.guide_anchors.mode.value,
            "terminal_distal_common_node",
        )
        self.assertEqual(
            config.guide_anchors.stations[0].fdis,
            (16, 15),
        )
        self.assertEqual(
            config.guide_anchors.terminal_distal_common_node.missing_fdi,
            17,
        )
        self.assertEqual(
            config.guide_anchors.terminal_distal_common_node.
            distal_offset_sleeve_diameters,
            2.0,
        )

    def test_accepts_adjacent_two_implant_terminal_distal_node_paths(self):
        (self.case_directory / "case.yaml").write_text(
            """
anatomy:
  present_teeth: [15, 14]
  missing_teeth: [16, 17]
""",
            encoding="utf-8",
        )
        config_data = self._valid_config_data()
        config_data["tooth_identification"] = {"case_yaml": "case.yaml"}
        config_data["guide_anchors"] = {
            "mode": "adjacent_two_implant_terminal_distal_node_paths",
            "stations": [
                {
                    "type": "tooth_pair_midpoint",
                    "fdis": [15, 14],
                    "u_side_ray_angle_degrees": 70.0,
                    "back_u_side_ray_angle_degrees": 90.0,
                }
            ],
            "terminal_distal_common_node": {
                "missing_fdi": 17,
                "reference_neighbor_fdi": 15,
                "implant_fdis": [16, 17],
            },
        }

        config = CaseConfig.from_json(self._write_config(config_data))

        self.assertEqual(
            config.guide_anchors.mode.value,
            "adjacent_two_implant_terminal_distal_node_paths",
        )
        self.assertEqual(
            config.guide_anchors.terminal_distal_common_node.implant_fdis,
            (16, 17),
        )

    def test_rejects_tooth_guide_anchors_without_tooth_identification(self):
        config_data = self._valid_config_data()
        config_data["guide_anchors"] = {
            "mode": "tooth_section_trajectory",
            "stations": [
                {"type": "tooth_pair_midpoint", "fdis": [16, 15]},
                {"type": "tooth_pair_midpoint", "fdis": [12, 11]},
            ],
        }

        with self.assertRaisesRegex(ConfigurationError, "tooth_identification"):
            CaseConfig.from_json(self._write_config(config_data))

    def test_rejects_nonadjacent_terminal_distal_reference(self):
        (self.case_directory / "case.yaml").write_text(
            """
anatomy:
  present_teeth: [16, 15]
  missing_teeth: [18]
""",
            encoding="utf-8",
        )
        config_data = self._valid_config_data()
        config_data["tooth_identification"] = {"case_yaml": "case.yaml"}
        config_data["guide_anchors"] = {
            "mode": "terminal_distal_common_node",
            "stations": [
                {"type": "tooth_pair_midpoint", "fdis": [16, 15]},
            ],
            "terminal_distal_common_node": {
                "missing_fdi": 18,
                "reference_neighbor_fdi": 16,
            },
        }

        with self.assertRaisesRegex(ConfigurationError, "相邻关系"):
            CaseConfig.from_json(self._write_config(config_data))

    def test_rejects_terminal_distal_common_node_with_terminal_u_extension(self):
        (self.case_directory / "case.yaml").write_text(
            """
anatomy:
  present_teeth: [16, 15, 23, 22, 21]
  missing_teeth: [17]
""",
            encoding="utf-8",
        )
        config_data = self._valid_config_data()
        config_data["tooth_identification"] = {"case_yaml": "case.yaml"}
        config_data["guide_anchors"] = {
            "mode": "terminal_distal_common_node",
            "stations": [
                {"type": "tooth_pair_midpoint", "fdis": [16, 15]},
            ],
            "terminal_distal_common_node": {
                "missing_fdi": 17,
                "reference_neighbor_fdi": 16,
            },
        }
        config_data["guide_terminal_u_extension"] = {
            "enabled": True,
            "mode": "tooth_wrapping_u_beam",
            "anchor_station": {"type": "tooth_center", "fdi": 21},
            "terminal_fdi": 23,
            "reference_neighbor_fdi": 22,
            "turnaround_depth_mm": 3.0,
        }

        with self.assertRaisesRegex(ConfigurationError, "不得在同一病例"):
            CaseConfig.from_json(self._write_config(config_data))

    def test_rejects_terminal_distal_mode_without_terminal_parameters(self):
        config_data = self._valid_config_data()
        config_data["guide_anchors"] = {
            "mode": "terminal_distal_common_node",
            "stations": [
                {"type": "tooth_pair_midpoint", "fdis": [16, 15]},
            ],
        }

        with self.assertRaisesRegex(ConfigurationError, "terminal_distal_common_node"):
            CaseConfig.from_json(self._write_config(config_data))

    def test_rejects_three_tooth_y_without_three_stations(self):
        config_data = self._valid_config_data()
        config_data["press_beam"] = {
            "mode": "three_tooth_anchors_y",
            "stations": [
                {"type": "tooth_pair_midpoint", "fdis": [46, 45]},
                {"type": "tooth_pair_midpoint", "fdis": [31, 32]},
            ],
        }

        with self.assertRaisesRegex(ConfigurationError, "必须配置三个牙位站位"):
            CaseConfig.from_json(self._write_config(config_data))

    def test_loads_terminal_tooth_wrapping_u_extension_from_case_yaml(self):
        """病例 YAML 可完整定义导板末端绕牙 U 型延伸梁。"""

        (self.case_directory / "case.yaml").write_text(
            """
anatomy:
  present_teeth: [23, 22, 21]
  missing_teeth: [14]
design:
  guide_terminal_u_extension:
    enabled: true
    mode: tooth_wrapping_u_beam
    anchor_station: {type: tooth_center, fdi: 21}
    u_side_ray_angle_degrees: 70.0
    back_u_side_ray_angle_degrees: 90.0
    terminal_fdi: 23
    reference_neighbor_fdi: 22
    diameter_mm: 4.6
    dental_clearance_mm: 0.2
    safety_margin_mm: 0.3
    turnaround_depth_mm: 3.0
    endpoint_reinforcement:
      enabled: true
      method: bulb_and_conformal_foot
""",
            encoding="utf-8",
        )
        config_data = self._valid_config_data()
        config_data["tooth_identification"] = {"case_yaml": "case.yaml"}

        config = CaseConfig.from_json(self._write_config(config_data))

        extension = config.guide_terminal_u_extension
        self.assertTrue(extension.enabled)
        self.assertEqual(extension.anchor_station.fdis, (21,))
        self.assertEqual(extension.terminal_fdi, 23)
        self.assertEqual(extension.reference_neighbor_fdi, 22)
        self.assertEqual(extension.radius_mm, 2.3)
        self.assertEqual(extension.turnaround_depth_mm, 3.0)
        self.assertIsNotNone(extension.endpoint_reinforcement)

    def test_rejects_u_extension_when_terminal_tooth_is_not_distal_most(self):
        (self.case_directory / "case.yaml").write_text(
            """
anatomy:
  present_teeth: [24, 23, 22, 21]
  missing_teeth: [14]
""",
            encoding="utf-8",
        )
        config_data = self._valid_config_data()
        config_data["tooth_identification"] = {"case_yaml": "case.yaml"}
        config_data["guide_terminal_u_extension"] = {
            "enabled": True,
            "mode": "tooth_wrapping_u_beam",
            "anchor_station": {"type": "tooth_center", "fdi": 21},
            "terminal_fdi": 23,
            "reference_neighbor_fdi": 22,
            "turnaround_depth_mm": 3.0,
        }

        with self.assertRaisesRegex(ConfigurationError, "不是当前牙列末端"):
            CaseConfig.from_json(self._write_config(config_data))

    def test_rejects_terminal_u_turn_depth_smaller_than_radius(self):
        config_data = self._valid_config_data()
        config_data["guide_terminal_u_extension"] = {
            "enabled": True,
            "mode": "tooth_wrapping_u_beam",
            "anchor_station": {"type": "tooth_center", "fdi": 21},
            "terminal_fdi": 23,
            "reference_neighbor_fdi": 22,
            "diameter_mm": 4.6,
            "turnaround_depth_mm": 2.0,
        }

        with self.assertRaisesRegex(ConfigurationError, "不得小于梁半径"):
            CaseConfig.from_json(self._write_config(config_data))

    def test_loads_terminal_u_extension_anchor_y_from_case_yaml(self):
        (self.case_directory / "case.yaml").write_text(
            """
anatomy:
  present_teeth: [23, 22, 21, 16, 15, 12, 11]
  missing_teeth: [14]
design:
  guide_terminal_u_extension:
    enabled: true
    mode: tooth_wrapping_u_beam
    anchor_station: {type: tooth_center, fdi: 21}
    terminal_fdi: 23
    reference_neighbor_fdi: 22
    diameter_mm: 4.6
    turnaround_depth_mm: 3.0
  press_beam:
    mode: terminal_u_extension_anchor_y
    extension_anchor:
      segment: u_side
      selection: farthest_from_guide_anchors
      start_margin_mm: 4.6
      end_margin_mm: 0.0
      overlap_mm: 0.3
    stations:
      - {type: tooth_pair_midpoint, fdis: [12, 11], ray_angle_degrees: 45.0}
      - {type: tooth_pair_midpoint, fdis: [16, 15], ray_angle_degrees: 45.0}
    junction_axial_lift_mm: 2.0
""",
            encoding="utf-8",
        )
        config_data = self._valid_config_data()
        config_data["tooth_identification"] = {"case_yaml": "case.yaml"}

        config = CaseConfig.from_json(self._write_config(config_data))

        self.assertEqual(
            config.press_beam.mode.value, "terminal_u_extension_anchor_y"
        )
        self.assertEqual(len(config.press_beam.stations), 2)
        self.assertIsNotNone(config.press_beam.extension_anchor)
        assert config.press_beam.extension_anchor is not None
        self.assertEqual(config.press_beam.extension_anchor.segment, "u_side")
        self.assertEqual(
            config.press_beam.extension_anchor.selection,
            "farthest_from_guide_anchors",
        )
        self.assertEqual(config.press_beam.extension_anchor.start_margin_mm, 4.6)
        self.assertEqual(config.press_beam.extension_anchor.end_margin_mm, 0.0)

    def test_rejects_non_increasing_local_observation_targets(self):
        config_data = self._valid_config_data()
        windows = config_data["windows"]
        self.assertIsInstance(windows, dict)
        windows["observation_local_failure_drop_targets_mm"] = [0.5, 0.5, 2.0]

        with self.assertRaisesRegex(ConfigurationError, "必须严格递增"):
            CaseConfig.from_json(self._write_config(config_data))

    def test_rejects_unknown_fields(self):
        config_data = self._valid_config_data()
        geometry_data = config_data["geometry"]
        self.assertIsInstance(geometry_data, dict)
        geometry_data["platform_height_mm"] = 4.0

        with self.assertRaisesRegex(ConfigurationError, "geometry 包含未知字段"):
            CaseConfig.from_json(self._write_config(config_data))

    def test_rejects_missing_or_invalid_jaw(self):
        config_data = self._valid_config_data()
        del config_data["jaw"]
        with self.assertRaisesRegex(ConfigurationError, "缺少必填字段：jaw"):
            CaseConfig.from_json(self._write_config(config_data))

        config_data = self._valid_config_data()
        config_data["jaw"] = "maxilla"
        with self.assertRaisesRegex(ConfigurationError, "upper.*lower"):
            CaseConfig.from_json(self._write_config(config_data))

    def test_rejects_invalid_numbers(self):
        config_data = self._valid_config_data()
        geometry_data = config_data["geometry"]
        self.assertIsInstance(geometry_data, dict)
        geometry_data["fusion_voxel_size_mm"] = math.inf
        with self.assertRaisesRegex(ConfigurationError, "必须为有限数"):
            CaseConfig.from_json(self._write_config(config_data))

    def test_rejects_outer_guide_diameter_not_larger_than_inner(self):
        config_data = self._valid_config_data()
        sleeve_data = config_data["sleeve"]
        self.assertIsInstance(sleeve_data, dict)
        sleeve_data["outer_diameter_mm"] = 2.10

        with self.assertRaisesRegex(ConfigurationError, "必须大于"):
            CaseConfig.from_json(self._write_config(config_data))

    def test_rejects_missing_sleeve_parameter(self):
        config_data = self._valid_config_data()
        sleeve_data = config_data["sleeve"]
        self.assertIsInstance(sleeve_data, dict)
        del sleeve_data["height_mm"]

        with self.assertRaisesRegex(ConfigurationError, "缺少必填字段：height_mm"):
            CaseConfig.from_json(self._write_config(config_data))


if __name__ == "__main__":
    unittest.main()

import math
import tempfile
import unittest
from pathlib import Path

import yaml

from twin_guide.config import (
    CaseConfig,
    Jaw,
    ToothIdentificationBackend,
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
                "patient_dentition": "patient_dentition.stl",
            },
            "sleeve": {
                "inner_diameter_mm": 2.10,
                "outer_diameter_mm": 4.3,
                "top_recess_diameter_mm": 2.61,
                "top_recess_depth_mm": 0.30,
                "height_mm": 16.373,
                "platform_slot_width_mm": 1.65,
                "platform_overhang_mm": 0.20,
                "platform_height_mm": 9.875,
                "closed_bore_height_mm": 4.777,
                "inner_arc_angle_degrees": 264.934,
                "outer_arc_angle_degrees": 211.684,
                "guide_spacing_mm": 11.5,
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
        config_path = self.case_directory / "case.yaml"
        existing: dict[str, object] = {}
        if config_path.exists():
            loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing = loaded
        inputs = config_data["inputs"]
        assert isinstance(inputs, dict)
        runtime = {
            key: config_data[key]
            for key in (
                "sleeve",
                "geometry",
                "windows",
                "handpiece_avoidance",
                "press_beam",
                "render",
                "tooth_identification",
            )
            if key in config_data and config_data[key] is not None
        }
        design = dict(existing.get("design", {}))
        for key in (
            "guide_anchors",
            "guide_component_bridge",
            "guide_terminal_u_extension",
        ):
            if config_data.get(key) is not None:
                design[key] = config_data[key]
        jaw = config_data.get("jaw")
        anatomy_jaw = {
            "upper": "maxillary",
            "lower": "mandibular",
        }.get(jaw, jaw)
        anatomy = {
            "present_teeth": [],
            "missing_teeth": [],
            "excluded_teeth": [],
            **dict(existing.get("anatomy", {})),
        }
        if anatomy_jaw is not None:
            anatomy["jaw"] = anatomy_jaw
        planning = dict(existing.get("planning", {}))
        planning.setdefault(
            "guide_posts",
            [
                {
                    "ring_index": 1,
                    "drill_length_mm": 33.0,
                    "implant_length_mm": 12.0,
                    "sleeve_template_extension_mm": 9.0,
                }
            ],
        )
        content = {
            "schema_version": "1.0",
            "case": {"id": config_data.get("case_id", "case_01")},
            "objects": {
                "dental": {"path": inputs["patient_dentition"]},
                "guide": {"path": inputs["template"]},
            },
            "runtime": runtime,
            "anatomy": anatomy,
            "design": design,
            "planning": planning,
            "review": existing.get("review", {}),
        }
        config_path.write_text(
            yaml.safe_dump(content, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        return config_path

    def test_loads_and_resolves_valid_configuration(self):
        config = CaseConfig.from_yaml(self._write_config(self._valid_config_data()))

        self.assertEqual(config.case_id, "case_01")
        self.assertEqual(config.jaw.value, "upper")
        self.assertEqual(config.jaw.occlusal_axis_sign, -1.0)
        self.assertEqual(Jaw.LOWER.occlusal_axis_sign, 1.0)
        self.assertEqual(config.sleeve.inner_diameter_mm, 2.10)
        self.assertEqual(config.sleeve.inner_radius_mm, 1.05)
        self.assertEqual(config.sleeve.outer_diameter_mm, 4.3)
        self.assertEqual(config.sleeve.outer_radius_mm, 2.15)
        self.assertEqual(config.sleeve.top_recess_diameter_mm, 2.61)
        self.assertEqual(config.sleeve.top_recess_radius_mm, 1.305)
        self.assertEqual(config.sleeve.top_recess_depth_mm, 0.30)
        self.assertEqual(config.sleeve.height_mm, 16.373)
        self.assertEqual(config.sleeve.platform_slot_width_mm, 1.65)
        self.assertEqual(config.sleeve.platform_overhang_mm, 0.20)
        self.assertEqual(config.sleeve.platform_height_mm, 9.875)
        self.assertEqual(config.sleeve.closed_bore_height_mm, 4.777)
        self.assertEqual(config.sleeve.inner_arc_angle_degrees, 264.934)
        self.assertEqual(config.sleeve.outer_arc_angle_degrees, 211.684)
        self.assertEqual(config.sleeve.guide_spacing_mm, 11.5)
        expected_d_face_offset = config.sleeve.outer_radius_mm * math.cos(
            math.radians(0.5 * (360.0 - config.sleeve.outer_arc_angle_degrees))
        )
        self.assertAlmostEqual(
            config.sleeve.outer_d_face_offset_mm,
            expected_d_face_offset,
        )
        self.assertAlmostEqual(
            config.sleeve.guide_axis_spacing_mm,
            11.5 + 2.0 * (config.sleeve.outer_radius_mm + 0.20),
        )
        self.assertAlmostEqual(
            config.sleeve.guide_c_opening_spacing_mm,
            config.sleeve.guide_axis_spacing_mm - 2.0 * expected_d_face_offset,
        )
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
        self.assertEqual(config.windows.operation_front_axial_margin_mm, 5.0)
        self.assertEqual(config.windows.operation_rear_axial_margin_mm, 5.0)
        self.assertEqual(config.geometry.sleeve_stop_clearance_mm, 2.0)
        self.assertEqual(config.geometry.sleeve_stop_front_avoidance_mm, 4.0)
        self.assertTrue(config.geometry.connection_blocks.lower_main)
        self.assertEqual(config.inputs.template, (self.case_directory / "template.stl").resolve())
        self.assertEqual(
            config.inputs.patient_dentition,
            (self.case_directory / "patient_dentition.stl").resolve(),
        )
        self.assertEqual(
            config.output_directory,
            Path(__file__).resolve().parents[2] / "output" / "case_01",
        )
        self.assertEqual(
            config.tooth_identification.case_yaml,
            (self.case_directory / "case.yaml").resolve(),
        )
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
        self.assertFalse(config.windows.observation_adaptive_fallback_enabled)
        self.assertIs(
            config.tooth_identification.backend,
            ToothIdentificationBackend.FDI_NEW,
        )

    def test_loads_explicit_standard_tooth_identification_fallback(self):
        """旧病例仍可显式选择标准牙位识别后端。"""

        config_data = self._valid_config_data()
        config_data["tooth_identification"] = {"backend": "standard"}

        config = CaseConfig.from_yaml(self._write_config(config_data))

        self.assertIs(
            config.tooth_identification.backend,
            ToothIdentificationBackend.STANDARD,
        )

    def test_loads_explicit_buccal_outward_handpiece_v5(self):
        """v5 单向颊侧避让不强制依赖止挡报告。"""

        config_data = self._valid_config_data()
        config_data["handpiece_avoidance"] = {
            "id": "phone_v5",
            "handpiece": "handpiece.stl",
            "motion_mode": "buccal_outward",
            "sampling_mode": "adaptive",
            "maximum_angle_degrees": 120.0,
            "collision_refinement_degrees": 0.1,
            "envelope_step_degrees": 0.5,
        }

        config = CaseConfig.from_yaml(self._write_config(config_data))
        parameters = config.handpiece_avoidance[0]

        self.assertEqual(parameters.motion_mode.value, "buccal_outward")
        self.assertEqual(parameters.sampling_mode.value, "adaptive")
        self.assertIsNone(parameters.stop_report)
        self.assertEqual(parameters.maximum_angle_degrees, 120.0)

    def test_loads_meeting_adjustment_interfaces(self):
        config_data = self._valid_config_data()
        windows = config_data["windows"]
        geometry = config_data["geometry"]
        self.assertIsInstance(windows, dict)
        self.assertIsInstance(geometry, dict)
        windows["operation_front_axial_margin_mm"] = 6.25
        windows["operation_rear_axial_margin_mm"] = 2.75
        geometry["sleeve_stop_clearance_mm"] = 3.1
        geometry["sleeve_stop_front_avoidance_mm"] = 2.4
        geometry["connection_blocks"] = {
            "lower_main": False,
            "upper_main": True,
            "press_beam": False,
        }
        (self.case_directory / "implant-coordinates.dat").write_text(
            "format supplied by clinician",
            encoding="utf-8",
        )
        config_path = self._write_config(config_data)
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        raw["planning"]["clinical_parameters"] = {
            "implant_coordinates_path": "implant-coordinates.dat",
            "implant_coordinates_format": "vendor-format-v1",
            "extension_mm": 1.234,
            "extension_definition": "clinician-confirmed-axis-extension",
            "mouth_opening_mm": 36.0,
            "adapter_length_mm": 12.0,
            "height_formula_id": "clinician-formula-v1",
        }
        config_path.write_text(
            yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

        config = CaseConfig.from_yaml(config_path)

        self.assertEqual(config.windows.operation_front_axial_margin_mm, 6.25)
        self.assertEqual(config.windows.operation_rear_axial_margin_mm, 2.75)
        self.assertEqual(config.geometry.sleeve_stop_clearance_mm, 3.1)
        self.assertEqual(config.geometry.sleeve_stop_front_avoidance_mm, 2.4)
        self.assertFalse(config.geometry.connection_blocks.lower_main)
        self.assertFalse(config.geometry.connection_blocks.press_beam)
        self.assertEqual(config.clinical_planning.extension_mm, 1.234)
        self.assertEqual(config.clinical_planning.height_formula_id, "clinician-formula-v1")

    def test_rejects_partial_clinical_parameter_pairs(self):
        config_path = self._write_config(self._valid_config_data())
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        raw["planning"]["clinical_parameters"] = {"extension_mm": 1.0}
        config_path.write_text(
            yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ConfigurationError, "延长量数值和定义"):
            CaseConfig.from_yaml(config_path)

    def test_loads_per_ring_lengths_and_calculates_extension(self):
        config_path = self._write_config(self._valid_config_data())
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        raw["planning"]["guide_posts"] = [
            {
                "ring_index": 1,
                "drill_length_mm": 33.0,
                "implant_length_mm": 12.0,
                "sleeve_template_extension_mm": 9.0,
            }
        ]
        config_path.write_text(
            yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

        config = CaseConfig.from_yaml(config_path)

        self.assertEqual(len(config.guide_posts), 1)
        self.assertEqual(config.guide_posts[0].ring_index, 1)
        self.assertEqual(config.guide_posts[0].twin_guide_extension_mm, 9.0)
        self.assertEqual(config.guide_posts[0].sleeve_template_extension_mm, 9.0)
        self.assertEqual(config.sleeve.guide_spacing_mm, 11.5)

    def test_requires_both_guide_post_lengths(self):
        config_path = self._write_config(self._valid_config_data())
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        raw["planning"]["guide_posts"] = [
            {
                "ring_index": 1,
                "drill_length_mm": 35.0,
                "sleeve_template_extension_mm": 13.0,
            }
        ]
        config_path.write_text(
            yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ConfigurationError, "implant_length_mm"):
            CaseConfig.from_yaml(config_path)

    def test_rejects_nonpositive_extension_in_guide_post(self):
        config_path = self._write_config(self._valid_config_data())
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        raw["planning"]["guide_posts"] = [
            {
                "ring_index": 1,
                "drill_length_mm": 22.0,
                "implant_length_mm": 10.0,
                "sleeve_template_extension_mm": 13.0,
            }
        ]
        config_path.write_text(
            yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ConfigurationError, "延长量必须大于 0"):
            CaseConfig.from_yaml(config_path)

    def test_rejects_input_path_outside_case_directory(self):
        """病例输入不得通过上级目录跳出病例边界。"""

        config_data = self._valid_config_data()
        inputs = config_data["inputs"]
        self.assertIsInstance(inputs, dict)
        inputs["template"] = "../outside.stl"

        with self.assertRaisesRegex(ConfigurationError, "必须位于病例目录内"):
            CaseConfig.from_yaml(self._write_config(config_data))

    def test_rejects_absolute_input_path(self):
        """病例 YAML 的输入路径必须保持可移植的相对形式。"""

        config_data = self._valid_config_data()
        inputs = config_data["inputs"]
        self.assertIsInstance(inputs, dict)
        inputs["template"] = str((self.case_directory / "template.stl").resolve())

        with self.assertRaisesRegex(ConfigurationError, "必须使用相对病例目录"):
            CaseConfig.from_yaml(self._write_config(config_data))

    def test_rejects_removed_sleeve_geometry_field(self):
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

        with self.assertRaisesRegex(ConfigurationError, "sleeve_geometry"):
            CaseConfig.from_yaml(self._write_config(config_data))

    def test_connector_diameter_defaults_to_4_60_mm(self):
        config_data = self._valid_config_data()
        geometry_data = config_data["geometry"]
        self.assertIsInstance(geometry_data, dict)
        del geometry_data["connector_diameter_mm"]

        config = CaseConfig.from_yaml(self._write_config(config_data))

        self.assertEqual(config.geometry.connector_diameter_mm, 4.60)
        self.assertEqual(config.geometry.connector_radius_mm, 2.30)

    def test_operation_bitangent_margin_defaults_to_3_mm(self):
        config_data = self._valid_config_data()
        windows_data = config_data["windows"]
        self.assertIsInstance(windows_data, dict)
        del windows_data["operation_bitangent_margin_mm"]

        config = CaseConfig.from_yaml(self._write_config(config_data))

        self.assertEqual(config.windows.operation_bitangent_margin_mm, 3.0)

    def test_maps_planning_operation_window_parameters(self):
        """同一 YAML 的规划字段应映射为操作窗参数。"""

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

        config = CaseConfig.from_yaml(self._write_config(config_data))

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
            CaseConfig.from_yaml(self._write_config(config_data))

    def test_loads_optional_handpiece_avoidance(self):
        config_data = self._valid_config_data()
        config_data["handpiece_avoidance"] = {
            "handpiece": "handpiece.stl",
            "stop_report": "stop_report.json",
        }

        config = CaseConfig.from_yaml(self._write_config(config_data))

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

        config = CaseConfig.from_yaml(self._write_config(config_data))

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
            CaseConfig.from_yaml(self._write_config(config_data))

    def test_loads_optional_unified_tooth_identification_case(self):
        """解析统一牙位识别与导板映射使用的病例 YAML。"""

        (self.case_directory / "case.yaml").write_text("case: {}\n", encoding="utf-8")
        config_data = self._valid_config_data()
        config_data["tooth_identification"] = {
            "case_yaml": "case.yaml",
        }

        config = CaseConfig.from_yaml(self._write_config(config_data))

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

        config = CaseConfig.from_yaml(self._write_config(config_data))

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

        config = CaseConfig.from_yaml(self._write_config(config_data))

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
        config = CaseConfig.from_yaml(self._write_config(config_data))

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
        config = CaseConfig.from_yaml(self._write_config(config_data))

        require_production_review(config)

    def test_loads_tooth_section_trajectory_anchor_stations(self):
        """解析单牙中心与相邻双牙中点的导板锚点站位。"""

        (self.case_directory / "case.yaml").write_text("case: {}\n", encoding="utf-8")
        config_data = self._valid_config_data()
        config_data["tooth_identification"] = {"case_yaml": "case.yaml"}
        config_data["guide_anchors"] = {
            "mode": "tooth_section_trajectory",
            "stations": [
                {"type": "tooth_center", "fdi": 13},
                {"type": "tooth_pair_midpoint", "fdis": [22, 23]},
            ],
        }

        config = CaseConfig.from_yaml(self._write_config(config_data))

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

        config = CaseConfig.from_yaml(self._write_config(config_data))

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
            CaseConfig.from_yaml(self._write_config(config_data))

    def test_loads_design_semantics_and_runtime_engineering_fields(self):
        """同一 YAML 可分组表达锚点语义与按压梁工程参数。"""

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

        config = CaseConfig.from_yaml(self._write_config(config_data))

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

    def test_rejects_duplicate_keys_inside_case_yaml(self):
        """严格 YAML 加载器不得静默采用同名键的最后一个值。"""

        path = self._write_config(self._valid_config_data())
        text = path.read_text(encoding="utf-8").replace(
            "  id: case_01\n",
            "  id: case_01\n  id: duplicate\n",
        )
        path.write_text(text, encoding="utf-8")

        with self.assertRaisesRegex(ConfigurationError, "duplicate key.*id"):
            CaseConfig.from_yaml(path)

    def test_rejects_tooth_section_mode_without_two_stations(self):
        config_data = self._valid_config_data()
        config_data["guide_anchors"] = {
            "mode": "tooth_section_trajectory",
            "stations": [{"type": "tooth_center", "fdi": 13}],
        }

        with self.assertRaisesRegex(ConfigurationError, "必须配置 2 个端部"):
            CaseConfig.from_yaml(self._write_config(config_data))

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

        config = CaseConfig.from_yaml(self._write_config(config_data))

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
            CaseConfig.from_yaml(self._write_config(config_data))

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

        config = CaseConfig.from_yaml(self._write_config(config_data))

        self.assertEqual(config.press_beam.mode.value, "three_tooth_anchors_y")
        self.assertEqual(
            tuple(station.fdis for station in config.press_beam.stations),
            ((45,), (31, 32), (36, 37)),
        )
        self.assertEqual(config.press_beam.junction_axial_lift_mm, 2.5)
        self.assertIsNone(config.press_beam.sleeve_anchor_selection)
        self.assertEqual(
            tuple(station.ray_angle_degrees for station in config.press_beam.stations),
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
            CaseConfig.from_yaml(self._write_config(config_data))

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

        config = CaseConfig.from_yaml(self._write_config(config_data))

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

        config = CaseConfig.from_yaml(self._write_config(config_data))

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
            config.guide_anchors.terminal_distal_common_node.distal_offset_sleeve_diameters,
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

        config = CaseConfig.from_yaml(self._write_config(config_data))

        self.assertEqual(
            config.guide_anchors.mode.value,
            "adjacent_two_implant_terminal_distal_node_paths",
        )
        self.assertEqual(
            config.guide_anchors.terminal_distal_common_node.implant_fdis,
            (16, 17),
        )

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
            CaseConfig.from_yaml(self._write_config(config_data))

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
            CaseConfig.from_yaml(self._write_config(config_data))

    def test_rejects_terminal_distal_mode_without_terminal_parameters(self):
        config_data = self._valid_config_data()
        config_data["guide_anchors"] = {
            "mode": "terminal_distal_common_node",
            "stations": [
                {"type": "tooth_pair_midpoint", "fdis": [16, 15]},
            ],
        }

        with self.assertRaisesRegex(ConfigurationError, "terminal_distal_common_node"):
            CaseConfig.from_yaml(self._write_config(config_data))

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
            CaseConfig.from_yaml(self._write_config(config_data))

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

        config = CaseConfig.from_yaml(self._write_config(config_data))

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
            CaseConfig.from_yaml(self._write_config(config_data))

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
            CaseConfig.from_yaml(self._write_config(config_data))

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

        config = CaseConfig.from_yaml(self._write_config(config_data))

        self.assertEqual(config.press_beam.mode.value, "terminal_u_extension_anchor_y")
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
            CaseConfig.from_yaml(self._write_config(config_data))

    def test_rejects_unknown_fields(self):
        config_data = self._valid_config_data()
        geometry_data = config_data["geometry"]
        self.assertIsInstance(geometry_data, dict)
        geometry_data["platform_height_mm"] = 4.0

        with self.assertRaisesRegex(ConfigurationError, "geometry 包含未知字段"):
            CaseConfig.from_yaml(self._write_config(config_data))

    def test_rejects_missing_or_invalid_jaw(self):
        config_data = self._valid_config_data()
        del config_data["jaw"]
        with self.assertRaisesRegex(ConfigurationError, "缺少必填字段：jaw"):
            CaseConfig.from_yaml(self._write_config(config_data))

        config_data = self._valid_config_data()
        config_data["jaw"] = "maxilla"
        with self.assertRaisesRegex(ConfigurationError, "maxillary.*mandibular"):
            CaseConfig.from_yaml(self._write_config(config_data))

    def test_rejects_invalid_numbers(self):
        config_data = self._valid_config_data()
        geometry_data = config_data["geometry"]
        self.assertIsInstance(geometry_data, dict)
        geometry_data["fusion_voxel_size_mm"] = math.inf
        with self.assertRaisesRegex(ConfigurationError, "必须为有限数"):
            CaseConfig.from_yaml(self._write_config(config_data))

    def test_rejects_outer_guide_diameter_not_larger_than_inner(self):
        config_data = self._valid_config_data()
        sleeve_data = config_data["sleeve"]
        self.assertIsInstance(sleeve_data, dict)
        sleeve_data["outer_diameter_mm"] = 2.10

        with self.assertRaisesRegex(ConfigurationError, "必须大于"):
            CaseConfig.from_yaml(self._write_config(config_data))

    def test_requires_valid_platform_slot_width(self):
        config_data = self._valid_config_data()
        sleeve_data = config_data["sleeve"]
        self.assertIsInstance(sleeve_data, dict)
        del sleeve_data["platform_slot_width_mm"]
        with self.assertRaisesRegex(ConfigurationError, "platform_slot_width_mm"):
            CaseConfig.from_yaml(self._write_config(config_data))

        config_data = self._valid_config_data()
        sleeve_data = config_data["sleeve"]
        self.assertIsInstance(sleeve_data, dict)
        sleeve_data["platform_slot_width_mm"] = 4.3
        with self.assertRaisesRegex(ConfigurationError, "必须小于"):
            CaseConfig.from_yaml(self._write_config(config_data))

    def test_rejects_negative_platform_overhang(self):
        config_data = self._valid_config_data()
        sleeve_data = config_data["sleeve"]
        self.assertIsInstance(sleeve_data, dict)
        sleeve_data["platform_overhang_mm"] = -0.01

        with self.assertRaisesRegex(ConfigurationError, "platform_overhang_mm"):
            CaseConfig.from_yaml(self._write_config(config_data))

    def test_rejects_inner_arc_too_close_to_closed_circle(self):
        for invalid_angle in (179.99, 350.01):
            with self.subTest(invalid_angle=invalid_angle):
                config_data = self._valid_config_data()
                sleeve_data = config_data["sleeve"]
                self.assertIsInstance(sleeve_data, dict)
                sleeve_data["inner_arc_angle_degrees"] = invalid_angle

                with self.assertRaisesRegex(ConfigurationError, "180 至 350"):
                    CaseConfig.from_yaml(self._write_config(config_data))

    def test_defaults_platform_overhang_to_standard_value(self):
        config_data = self._valid_config_data()
        sleeve_data = config_data["sleeve"]
        self.assertIsInstance(sleeve_data, dict)
        del sleeve_data["platform_overhang_mm"]

        config = CaseConfig.from_yaml(self._write_config(config_data))

        self.assertEqual(config.sleeve.platform_overhang_mm, 0.20)

    def test_rejects_incomplete_or_invalid_top_recess(self):
        config_data = self._valid_config_data()
        sleeve_data = config_data["sleeve"]
        self.assertIsInstance(sleeve_data, dict)
        del sleeve_data["top_recess_depth_mm"]
        with self.assertRaisesRegex(ConfigurationError, "必须同时提供"):
            CaseConfig.from_yaml(self._write_config(config_data))

        config_data = self._valid_config_data()
        sleeve_data = config_data["sleeve"]
        self.assertIsInstance(sleeve_data, dict)
        sleeve_data["top_recess_diameter_mm"] = 4.3
        with self.assertRaisesRegex(ConfigurationError, "顶部凹陷直径"):
            CaseConfig.from_yaml(self._write_config(config_data))

        config_data = self._valid_config_data()
        sleeve_data = config_data["sleeve"]
        self.assertIsInstance(sleeve_data, dict)
        sleeve_data["top_recess_depth_mm"] = 7.0
        with self.assertRaisesRegex(ConfigurationError, "顶部 C 口段高度"):
            CaseConfig.from_yaml(self._write_config(config_data))

    def test_top_recess_is_optional_for_existing_cases(self):
        config_data = self._valid_config_data()
        sleeve_data = config_data["sleeve"]
        self.assertIsInstance(sleeve_data, dict)
        del sleeve_data["top_recess_diameter_mm"]
        del sleeve_data["top_recess_depth_mm"]

        config = CaseConfig.from_yaml(self._write_config(config_data))

        self.assertIsNone(config.sleeve.top_recess_diameter_mm)
        self.assertIsNone(config.sleeve.top_recess_radius_mm)
        self.assertEqual(config.sleeve.top_recess_depth_mm, 0.0)

    def test_rejects_missing_sleeve_parameter(self):
        config_data = self._valid_config_data()
        sleeve_data = config_data["sleeve"]
        self.assertIsInstance(sleeve_data, dict)
        del sleeve_data["height_mm"]

        with self.assertRaisesRegex(ConfigurationError, "缺少必填字段：height_mm"):
            CaseConfig.from_yaml(self._write_config(config_data))

    def test_loads_site_editor_overrides(self):
        path = self._write_config(self._valid_config_data())
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        raw["editor_overrides"] = {
            "sleeve_sites": [
                {
                    "ring_index": 1,
                    "height_mm": 16.0,
                    "platform_height_mm": 9.0,
                    "closed_bore_height_mm": 4.0,
                },
            ],
            "connector_avoidance": [
                {
                    "guide_index": 1,
                    "side": "left",
                    "path_fraction": 0.35,
                    "downward_offset_mm": 2.0,
                },
                {
                    "guide_index": 2,
                    "side": "right",
                    "path_fraction": 0.60,
                    "downward_offset_mm": 3.0,
                },
            ],
        }
        path.write_text(
            yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

        config = CaseConfig.from_yaml(path)

        self.assertEqual(config.editor_overrides.sleeve_for(1).height_mm, 16.0)
        self.assertEqual(
            config.editor_overrides.connector_for(1, "left").downward_offset_mm,
            2.0,
        )

    def test_connector_avoidance_requires_explicit_side(self):
        path = self._write_config(self._valid_config_data())
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        raw["editor_overrides"] = {
            "connector_avoidance": [
                {
                    "guide_index": 1,
                    "path_fraction": 0.35,
                    "downward_offset_mm": 2.0,
                }
            ]
        }
        path.write_text(
            yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ConfigurationError, "side"):
            CaseConfig.from_yaml(path)

    def test_guide_post_sleeve_parameters_inherit_and_override_defaults(self):
        path = self._write_config(self._valid_config_data())
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        raw["planning"]["guide_posts"] = [
            {
                "ring_index": 1,
                "drill_length_mm": 33.0,
                "implant_length_mm": 12.0,
                "sleeve_template_extension_mm": 9.0,
                "sleeve": {
                    "height_mm": 17.0,
                    "platform_height_mm": 10.5,
                    "closed_bore_height_mm": 5.0,
                },
            },
            {
                "ring_index": 2,
                "drill_length_mm": 31.0,
                "implant_length_mm": 10.0,
                "sleeve_template_extension_mm": 8.0,
                "sleeve": {
                    "height_mm": 16.0,
                    "platform_height_mm": 10.2,
                    "closed_bore_height_mm": 4.8,
                },
            },
        ]
        path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

        config = CaseConfig.from_yaml(path)
        first = config.guide_posts[0].resolved_sleeve(config.sleeve)
        second = config.guide_posts[1].resolved_sleeve(config.sleeve)

        self.assertEqual(first.height_mm, 17.0)
        self.assertEqual(first.platform_height_mm, 10.5)
        self.assertEqual(first.closed_bore_height_mm, 5.0)
        self.assertEqual(first.guide_spacing_mm, 11.5)
        self.assertEqual(first.inner_diameter_mm, config.sleeve.inner_diameter_mm)
        self.assertEqual(second.height_mm, 16.0)
        self.assertEqual(second.platform_height_mm, 10.2)
        self.assertEqual(second.closed_bore_height_mm, 4.8)
        self.assertEqual(second.guide_spacing_mm, 11.5)
        self.assertEqual(second.outer_diameter_mm, config.sleeve.outer_diameter_mm)
        self.assertNotEqual(first, second)

    def test_guide_post_accepts_all_three_height_overrides(self):
        path = self._write_config(self._valid_config_data())
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        expected = {
            "height_mm": 16.50,
            "platform_height_mm": 10.20,
            "closed_bore_height_mm": 5.10,
        }
        raw["planning"]["guide_posts"][0]["sleeve"] = expected
        path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

        config = CaseConfig.from_yaml(path)
        effective = config.guide_posts[0].resolved_sleeve(config.sleeve)

        for name, value in expected.items():
            self.assertEqual(getattr(effective, name), value)

        fixed_names = {
            "inner_diameter_mm",
            "outer_diameter_mm",
            "top_recess_diameter_mm",
            "top_recess_depth_mm",
            "platform_slot_width_mm",
            "platform_overhang_mm",
            "inner_arc_angle_degrees",
            "outer_arc_angle_degrees",
            "guide_spacing_mm",
        }
        for name in fixed_names:
            self.assertEqual(getattr(effective, name), getattr(config.sleeve, name))

    def test_rejects_fixed_guide_post_sleeve_overrides(self):
        fixed_names = (
            "inner_diameter_mm",
            "outer_diameter_mm",
            "top_recess_diameter_mm",
            "top_recess_depth_mm",
            "platform_slot_width_mm",
            "platform_overhang_mm",
            "inner_arc_angle_degrees",
            "outer_arc_angle_degrees",
            "guide_spacing_mm",
        )
        for field_name in fixed_names:
            with self.subTest(field_name=field_name):
                path = self._write_config(self._valid_config_data())
                raw = yaml.safe_load(path.read_text(encoding="utf-8"))
                raw["planning"]["guide_posts"][0]["sleeve"] = {field_name: 12.0}
                path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

                with self.assertRaisesRegex(ConfigurationError, field_name):
                    CaseConfig.from_yaml(path)

    def test_accepts_global_guide_spacing_for_all_sites(self):
        path = self._write_config(self._valid_config_data())
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        raw["runtime"]["sleeve"]["guide_spacing_mm"] = 12.0
        raw["planning"]["guide_posts"].append(
            {
                "ring_index": 2,
                "drill_length_mm": 31.0,
                "implant_length_mm": 10.0,
                "sleeve_template_extension_mm": 8.0,
                "sleeve": {
                    "height_mm": 16.0,
                    "platform_height_mm": 10.2,
                    "closed_bore_height_mm": 4.8,
                },
            }
        )
        path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

        config = CaseConfig.from_yaml(path)

        self.assertEqual(config.sleeve.guide_spacing_mm, 12.0)
        self.assertEqual(len(config.guide_posts), 2)
        for post in config.guide_posts:
            self.assertEqual(post.resolved_sleeve(config.sleeve).guide_spacing_mm, 12.0)

    def test_rejects_invalid_or_unknown_guide_post_sleeve_override(self):
        path = self._write_config(self._valid_config_data())
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        post = raw["planning"]["guide_posts"][0]
        post["sleeve"] = {"height_mm": 8.0, "unknown_mm": 1.0}
        path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
        with self.assertRaisesRegex(ConfigurationError, "unknown_mm"):
            CaseConfig.from_yaml(path)

        del post["sleeve"]["unknown_mm"]
        path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
        with self.assertRaisesRegex(ConfigurationError, "高度必须满足"):
            CaseConfig.from_yaml(path)

    def test_rejects_recess_geometry_as_site_override(self):
        path = self._write_config(self._valid_config_data())
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        raw["planning"]["guide_posts"][0]["sleeve"] = {"top_recess_diameter_mm": 2.7}
        path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

        with self.assertRaisesRegex(ConfigurationError, "top_recess_diameter_mm"):
            CaseConfig.from_yaml(path)

    def test_migrates_matching_legacy_pair_and_rejects_divergence(self):
        path = self._write_config(self._valid_config_data())
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        matching = {
            "height_mm": 16.0,
            "platform_height_mm": 9.0,
            "closed_bore_height_mm": 4.0,
        }
        raw["editor_overrides"] = {
            "sleeve_guides": [
                {"guide_index": 1, **matching},
                {
                    "guide_index": 2,
                    "height_mm": 15.999999,
                    "platform_height_mm": 9.000001,
                    "closed_bore_height_mm": 3.999999,
                },
            ]
        }
        path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
        config = CaseConfig.from_yaml(path)
        self.assertEqual(config.editor_overrides.sleeve_for(1).height_mm, 16.0)

        raw["editor_overrides"]["sleeve_guides"][0]["height_mm"] = 16.004
        raw["editor_overrides"]["sleeve_guides"][1]["height_mm"] = 16.006
        path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
        with self.assertRaisesRegex(ConfigurationError, "完全一致"):
            CaseConfig.from_yaml(path)

        raw["editor_overrides"]["sleeve_guides"][0]["height_mm"] = 16.0
        raw["editor_overrides"]["sleeve_guides"][1]["height_mm"] = 17.0
        path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
        with self.assertRaisesRegex(ConfigurationError, "完全一致"):
            CaseConfig.from_yaml(path)

        for guide in raw["editor_overrides"]["sleeve_guides"]:
            guide.update(
                height_mm=10.004,
                platform_height_mm=9.996,
                closed_bore_height_mm=4.0,
            )
        path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
        with self.assertRaisesRegex(ConfigurationError, "保留两位小数后的高度无效"):
            CaseConfig.from_yaml(path)

    def test_rejects_invalid_canonical_editor_height_as_configuration_error(self):
        path = self._write_config(self._valid_config_data())
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        raw["editor_overrides"] = {
            "sleeve_sites": [
                {
                    "ring_index": 1,
                    "height_mm": 9.0,
                    "platform_height_mm": 10.0,
                    "closed_bore_height_mm": 4.0,
                }
            ]
        }
        path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

        with self.assertRaisesRegex(ConfigurationError, "高度必须满足"):
            CaseConfig.from_yaml(path)

    def test_rejects_editor_height_for_unknown_ring(self):
        path = self._write_config(self._valid_config_data())
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        raw["editor_overrides"] = {
            "sleeve_sites": [
                {
                    "ring_index": 9,
                    "height_mm": 16.0,
                    "platform_height_mm": 10.0,
                    "closed_bore_height_mm": 4.9,
                }
            ]
        }
        path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

        with self.assertRaisesRegex(ConfigurationError, "未知 ring_index"):
            CaseConfig.from_yaml(path)


if __name__ == "__main__":
    unittest.main()

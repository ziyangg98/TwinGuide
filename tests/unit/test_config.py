import json
import math
import tempfile
import unittest
from pathlib import Path

from twin_guide.config import CaseConfig, Jaw
from twin_guide.errors import ConfigurationError


class CaseConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.case_directory = Path(self.temporary_directory.name)
        for filename in (
            "template.stl",
            "guide_sleeve_assembly.stl",
            "patient_dentition.stl",
        ):
            (self.case_directory / filename).touch()

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
                "operation_bitangent_margin_mm": 0.5,
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
        self.assertEqual(config.sleeve.height_mm, 16.373)
        self.assertEqual(config.sleeve.platform_width_mm, 2.036)
        self.assertEqual(config.sleeve.platform_height_mm, 9.875)
        self.assertEqual(config.sleeve.closed_bore_height_mm, 4.777)
        self.assertEqual(config.sleeve.inner_arc_angle_degrees, 264.934)
        self.assertEqual(config.sleeve.outer_arc_angle_degrees, 211.684)
        self.assertEqual(config.geometry.connector_diameter_mm, 2.3)
        self.assertEqual(config.geometry.connector_radius_mm, 1.15)
        self.assertEqual(config.inputs.template, (self.case_directory / "template.stl").resolve())
        self.assertEqual(
            config.inputs.patient_dentition,
            (self.case_directory / "patient_dentition.stl").resolve(),
        )
        self.assertEqual(config.output_directory, (self.case_directory / "output").resolve())

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

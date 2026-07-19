import json
import math
import tempfile
import unittest
from pathlib import Path

from twin_guide.config import CaseConfig
from twin_guide.errors import ConfigurationError


class CaseConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.case_directory = Path(self.temporary_directory.name)
        for filename in ("template.stl", "guide_sleeve_assembly.stl", "handpiece.stl"):
            (self.case_directory / filename).touch()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _valid_config_data(self) -> dict[str, object]:
        return {
            "case_id": "case_01",
            "inputs": {
                "template": "template.stl",
                "guide_sleeve_assembly": "guide_sleeve_assembly.stl",
            },
            "geometry": {
                "template_channel_radius_mm": 3.05,
                "channel_axial_margin_mm": 5.0,
                "connector_radius_mm": 1.2,
                "fusion_voxel_size_mm": 0.2,
            },
            "windows": {
                "operation_tangent_margin_mm": 1.0,
                "operation_bitangent_margin_mm": 0.5,
            },
            "render": {"width_px": 640, "height_px": 480},
            "validation": {
                "handpiece": {
                    "mesh": "handpiece.stl",
                    "head_crop_radius_mm": 10.0,
                    "minimum_clearance_mm": 1.0,
                    "maximum_tilt_degrees": 5.0,
                    "withdrawal_distances_mm": [0.0, 4.0, 8.0],
                }
            },
            "output_directory": "output",
        }

    def _write_config(self, config_data: dict[str, object]) -> Path:
        config_path = self.case_directory / "case.json"
        config_path.write_text(json.dumps(config_data), encoding="utf-8")
        return config_path

    def test_loads_and_resolves_valid_configuration(self):
        config = CaseConfig.from_json(self._write_config(self._valid_config_data()))

        self.assertEqual(config.case_id, "case_01")
        self.assertEqual(config.geometry.template_channel_radius_mm, 3.05)
        self.assertEqual(config.geometry.connector_radius_mm, 1.2)
        self.assertIsNotNone(config.validation)
        if config.validation is None:
            self.fail("Expected validation configuration")
        self.assertEqual(
            config.validation.handpiece.withdrawal_distances_mm,
            (0.0, 4.0, 8.0),
        )
        self.assertEqual(config.inputs.template, (self.case_directory / "template.stl").resolve())
        self.assertEqual(config.output_directory, (self.case_directory / "output").resolve())

    def test_loads_generation_config_without_validation(self):
        config_data = self._valid_config_data()
        del config_data["validation"]

        config = CaseConfig.from_json(self._write_config(config_data))

        self.assertIsNone(config.validation)

    def test_rejects_unknown_fields(self):
        config_data = self._valid_config_data()
        geometry_data = config_data["geometry"]
        self.assertIsInstance(geometry_data, dict)
        geometry_data["platform_height_mm"] = 4.0

        with self.assertRaisesRegex(ConfigurationError, "geometry 包含未知字段"):
            CaseConfig.from_json(self._write_config(config_data))

    def test_rejects_invalid_numbers_and_motion_samples(self):
        config_data = self._valid_config_data()
        geometry_data = config_data["geometry"]
        self.assertIsInstance(geometry_data, dict)
        geometry_data["fusion_voxel_size_mm"] = math.inf
        with self.assertRaisesRegex(ConfigurationError, "必须为有限数"):
            CaseConfig.from_json(self._write_config(config_data))

        config_data = self._valid_config_data()
        validation_data = config_data["validation"]
        self.assertIsInstance(validation_data, dict)
        handpiece_data = validation_data["handpiece"]
        self.assertIsInstance(handpiece_data, dict)
        handpiece_data["withdrawal_distances_mm"] = [4.0, 8.0]
        with self.assertRaisesRegex(ConfigurationError, "必须包含 0"):
            CaseConfig.from_json(self._write_config(config_data))


if __name__ == "__main__":
    unittest.main()

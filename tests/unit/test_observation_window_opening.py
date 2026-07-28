import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from twin_guide.errors import GeometryError
from twin_guide.observation_window_engine import ObservationWindowRequest
from twin_guide.observation_window_opening import build_observation_window_opening


class ObservationWindowOpeningTests(unittest.TestCase):
    def test_failed_final_qa_never_returns_a_cutter(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            template = root / "template.stl"
            dental = root / "dental.stl"
            mapping_path = root / "stage-02-tooth-mapping.json"
            template.write_bytes(b"template")
            dental.write_bytes(b"dental")
            mapping = {
                "observation_windows": [{
                    "id": "right",
                    "opening_geometry": "axis_sweep",
                    "axis_sweep": {
                        "axis_drop_mm": 0.2,
                        "sweep_angle_deg": 90.0,
                    },
                }],
            }
            mapping_path.write_text(json.dumps(mapping), encoding="utf-8")
            config = SimpleNamespace(
                output_directory=root / "output",
                inputs=SimpleNamespace(
                    template=template,
                    patient_dentition=dental,
                ),
                windows=SimpleNamespace(
                    observation_axis_drop_mm=0.2,
                    observation_sweep_angle_degrees=90.0,
                    observation_local_failure_drop_targets_mm=(0.5, 1.0, 2.0),
                    observation_local_failure_transition_rows=1,
                ),
            )
            identification = SimpleNamespace(
                mapping_report_path=mapping_path,
                mapping_report=mapping,
            )
            failed_report = {
                "QA": {"axis_sweep_semantic_axis_is_fully_open": False},
                "outputs": {},
            }

            with patch(
                "twin_guide.observation_window_engine.run",
                return_value=failed_report,
            ) as mocked_run, self.assertRaisesRegex(GeometryError, "未通过最终 QA"):
                build_observation_window_opening(config, identification)

            request = mocked_run.call_args.args[0]
            self.assertIsInstance(request, ObservationWindowRequest)
            self.assertEqual(
                request.output_dir,
                root / "output" / ".cache" / "stage-03-cutout-planning",
            )
            self.assertEqual(request.volume_identity_tolerance_mm3, 0.05)
            self.assertEqual(request.volume_identity_relative_tolerance, 1e-4)


if __name__ == "__main__":
    unittest.main()

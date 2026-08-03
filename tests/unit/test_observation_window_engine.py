import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from twin_guide.observation_window_engine._core import (
    ObservationWindowRequest,
    _run_with_local_failure_target_sequence,
)


class ObservationWindowRetryTests(unittest.TestCase):
    def test_retry_skips_targets_not_deeper_than_editor_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mapping_path = root / "mapping.json"
            mapping_path.write_text(
                json.dumps(
                    {
                        "observation_windows": [
                            {
                                "id": "anterior_axis_sweep",
                                "axis_sweep": {
                                    "axis_drop_mm": 1.958,
                                    "axis_section_count": 3,
                                },
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            report_path = root / "report.json"
            failed = {
                "QA": {"axis_clearance": False},
                "windows": [
                    {
                        "id": "anterior_axis_sweep",
                        "opening_geometry": "axis_sweep",
                        "minimum_removed_axis_clearance_mm": 0.0,
                        "axis_clearance_threshold_mm": 0.15,
                        "axis_rows_below_clearance_threshold": [1],
                    }
                ],
                "outputs": {"report_json": str(report_path)},
            }
            passed = {
                "QA": {"axis_clearance": True},
                "windows": failed["windows"],
                "outputs": {"report_json": str(report_path)},
            }
            request = ObservationWindowRequest(
                case=mapping_path,
                mapping_report=mapping_path,
                source=root / "template.stl",
                output_dir=root,
                local_failure_drop_targets_mm=(1.0, 2.0, 3.0),
            )

            with patch(
                "twin_guide.observation_window_engine._core._run_once",
                side_effect=(failed, passed),
            ) as run_once:
                result = _run_with_local_failure_target_sequence(request)

            self.assertTrue(result["QA"]["axis_clearance"])
            adaptation = result["local_failure_adaptation"]
            self.assertEqual(adaptation["selected_effective_drop_target_mm"], 2.0)
            self.assertEqual(run_once.call_count, 2)
            corrected_mapping = json.loads(
                run_once.call_args.args[0].mapping_report.read_text(encoding="utf-8")
            )
            additions = corrected_mapping["observation_windows"][0]["axis_sweep"][
                "local_axis_drop_additions_mm"
            ]
            self.assertAlmostEqual(additions[1], 0.042)


if __name__ == "__main__":
    unittest.main()

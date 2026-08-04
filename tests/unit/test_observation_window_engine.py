import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from twin_guide.observation_window_engine._core import (
    ObservationWindowRequest,
    _structured_sweep_volume,
    run,
)


class ObservationWindowConstraintSolverTests(unittest.TestCase):
    def test_structured_sweep_contains_axis_without_cell_booleans(self) -> None:
        angles = np.deg2rad((0.0, 45.0, 90.0))
        directions = np.column_stack(
            (np.cos(angles), np.sin(angles), np.zeros(len(angles)))
        )
        outer = np.stack(
            (2.0 * directions, 2.0 * directions + (0.0, 0.0, 1.0))
        )
        inner_directions = directions[::-1]
        inner = np.stack(
            (-inner_directions, -inner_directions + (0.0, 0.0, 1.0))
        )

        cutter = _structured_sweep_volume(outer, inner)

        self.assertTrue(cutter.is_volume)
        self.assertTrue(cutter.contains([[0.0, 0.0, 0.5]])[0])
        _, distance, _ = cutter.nearest.on_surface([[0.0, 0.0, 0.5]])
        self.assertGreater(float(distance[0]), 0.2)

    def test_run_builds_and_validates_exactly_once(self) -> None:
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
            passed = {
                "QA": {"axis_clearance": True},
                "windows": [],
                "outputs": {"report_json": str(report_path)},
            }
            request = ObservationWindowRequest(
                case=mapping_path,
                mapping_report=mapping_path,
                source=root / "template.stl",
                output_dir=root,
            )

            with patch(
                "twin_guide.observation_window_engine._core._run_once",
                return_value=passed,
            ) as run_once:
                result = run(request)

            self.assertTrue(result["QA"]["axis_clearance"])
            self.assertEqual(result["constraint_solution"]["attempt_count"], 1)
            run_once.assert_called_once_with(request)


if __name__ == "__main__":
    unittest.main()

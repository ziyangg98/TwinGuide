import tempfile
import unittest
from pathlib import Path

from twin_guide.config import (
    ConnectorAvoidanceOverride,
    EditorOverrides,
    ObservationWindowOverride,
    OperationWindowOverride,
    SleeveSiteOverride,
    SurfaceAnchorOverride,
)
from twin_guide.config.editor_storage import save_editor_overrides


class EditorStorageTests(unittest.TestCase):
    def test_preserves_comments_and_creates_only_first_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "case.yaml"
            original = "# clinician note\ncase: {id: demo}\nreview: {}\n"
            path.write_text(original, encoding="utf-8")
            overrides = EditorOverrides(
                sleeve_sites=(SleeveSiteOverride(1, 16.0, 9.0, 4.0, -12.5),),
                operation_windows=(
                    OperationWindowOverride(1, 1.0, 2.0, 3.0, 4.0, (0.1, 0.2, 0.3)),
                ),
                observation_windows=(
                    ObservationWindowOverride("anterior", 11, 21, 0.2, 5.0, 90.0),
                ),
                connector_avoidance=(ConnectorAvoidanceOverride(1, 0.4, 2.5, "left"),),
                surface_anchors=(
                    SurfaceAnchorOverride(
                        "press_anchor_1",
                        "template",
                        (1.0, 2.0, 3.0),
                        (0.0, 0.0, 1.0),
                    ),
                ),
                press_junction_mm=(4.0, 5.0, 6.0),
            )

            backup = save_editor_overrides(path, overrides)
            first = path.read_text(encoding="utf-8")
            save_editor_overrides(
                path,
                EditorOverrides(sleeve_sites=(SleeveSiteOverride(1, 17.0, 10.0, 5.0),)),
            )

            self.assertEqual(backup.read_text(encoding="utf-8"), original)
            self.assertIn("# clinician note", first)
            self.assertIn("sleeve_sites:", first)
            self.assertIn("ring_index: 1", first)
            self.assertIn("rotation_degrees: -12.5", first)
            self.assertIn("operation_windows:", first)
            self.assertIn("center_offset_mm:", first)
            self.assertIn("observation_windows:", first)
            self.assertIn("sweep_angle_degrees: 90.0", first)
            self.assertIn("path_fraction: 0.4", first)
            self.assertIn("side: left", first)
            self.assertIn("surface_anchors:", first)
            self.assertIn("surface_role: template", first)
            self.assertIn("press_junction_mm:", first)
            self.assertEqual(backup.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()

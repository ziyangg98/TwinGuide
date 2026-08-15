import tempfile
import unittest
from pathlib import Path

from twin_guide.config import (
    ConnectorAvoidanceOverride,
    EditorOverrides,
    SleeveSiteOverride,
)
from twin_guide.config.editor_storage import save_editor_overrides


class EditorStorageTests(unittest.TestCase):
    def test_preserves_comments_and_creates_only_first_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "case.yaml"
            original = "# clinician note\ncase: {id: demo}\nreview: {}\n"
            path.write_text(original, encoding="utf-8")
            overrides = EditorOverrides(
                sleeve_sites=(SleeveSiteOverride(1, 16.0, 9.0, 4.0),),
                connector_avoidance=(
                    ConnectorAvoidanceOverride(1, 0.4, 2.5, "left"),
                ),
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
            self.assertIn("path_fraction: 0.4", first)
            self.assertIn("side: left", first)
            self.assertEqual(backup.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()

import unittest

from twin_guide.config import (
    ConnectorAvoidanceOverride,
    EditorOverrides,
    OperationWindowOverride,
)
from twin_guide.editor_session import EditorSession, changed_feature_ids


def _overrides(offset: float) -> EditorOverrides:
    return EditorOverrides(
        connector_avoidance=(ConnectorAvoidanceOverride(1, 0.4, offset),)
    )


class EditorSessionTests(unittest.TestCase):
    def test_drag_commit_cancel_undo_redo_and_revision(self):
        original = _overrides(1.0)
        changed = _overrides(2.0)
        session = EditorSession.create(original)

        session.begin_edit()
        session.preview_edit(changed)
        self.assertEqual(session.revision, 0)
        session.cancel_edit()
        self.assertEqual(session.working_overrides, original)

        session.begin_edit()
        session.preview_edit(changed)
        self.assertTrue(session.commit_edit())
        self.assertTrue(session.dirty)
        self.assertEqual(session.revision, 1)
        self.assertTrue(session.undo())
        self.assertEqual(session.working_overrides, original)
        self.assertTrue(session.redo())
        self.assertEqual(session.working_overrides, changed)
        self.assertFalse(session.preview_is_current(1))

        session.mark_saved()
        self.assertFalse(session.dirty)

    def test_locked_session_rejects_mutation(self):
        session = EditorSession.create(EditorOverrides())
        session.locked = True

        with self.assertRaisesRegex(RuntimeError, "最终导出"):
            session.begin_edit()

    def test_changed_feature_ids_uses_stable_semantic_ids(self):
        previous = _overrides(1.0)
        current = EditorOverrides(
            connector_avoidance=(ConnectorAvoidanceOverride(1, 0.4, 2.0),),
            operation_windows=(
                OperationWindowOverride(1, 1.0, 1.0, 1.0, 1.0),
            ),
        )

        self.assertEqual(
            changed_feature_ids(previous, current),
            ("connector:guide_1", "operation_window:1"),
        )


if __name__ == "__main__":
    unittest.main()

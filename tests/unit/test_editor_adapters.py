import unittest

from twin_guide.config import (
    ConnectorAvoidanceOverride,
    EditorOverrides,
    ObservationWindowOverride,
    OperationWindowOverride,
    SleeveSiteOverride,
    SurfaceAnchorOverride,
)
from twin_guide.editor_adapters import (
    with_connector,
    with_observation_window,
    with_operation_window,
    with_press_junction,
    with_sleeve,
    with_surface_anchor,
)


class EditorAdapterTests(unittest.TestCase):
    def test_single_feature_update_preserves_other_semantic_values(self):
        original = EditorOverrides(
            sleeve_sites=(SleeveSiteOverride(1, 12.0, 6.0, 4.0),),
            operation_windows=(OperationWindowOverride(1, 0.8, 0.6, 1.0, 0.5),),
            connector_avoidance=(
                ConnectorAvoidanceOverride(1, 0.35, 1.0, "left"),
                ConnectorAvoidanceOverride(2, 0.40, 1.5, "left"),
            ),
        )

        changed = with_connector(
            original,
            ConnectorAvoidanceOverride(1, 0.6, 3.0, "left"),
        )

        self.assertEqual(
            changed.connector_for(1, "left"),
            ConnectorAvoidanceOverride(1, 0.6, 3.0, "left"),
        )
        self.assertEqual(
            changed.connector_for(2, "left"), original.connector_for(2, "left")
        )
        self.assertEqual(changed.operation_windows, original.operation_windows)
        self.assertEqual(changed.sleeve_sites, original.sleeve_sites)

    def test_upsert_orders_new_features_by_stable_identifier(self):
        overrides = EditorOverrides()
        overrides = with_sleeve(overrides, SleeveSiteOverride(2, 13.0, 7.0, 4.0))
        overrides = with_sleeve(overrides, SleeveSiteOverride(1, 12.0, 6.0, 3.0))
        overrides = with_operation_window(
            overrides,
            OperationWindowOverride(2, 1.0, 1.0, 1.0, 1.0),
        )
        overrides = with_operation_window(
            overrides,
            OperationWindowOverride(1, 0.5, 0.5, 0.5, 0.5),
        )

        self.assertEqual(
            [item.ring_index for item in overrides.sleeve_sites],
            [1, 2],
        )
        self.assertEqual(
            [item.site_index for item in overrides.operation_windows],
            [1, 2],
        )

    def test_other_feature_edits_cannot_modify_operation_window(self):
        operation = OperationWindowOverride(
            1,
            1.0,
            3.0,
            5.0,
            5.0,
            (0.0, 0.0, 0.0),
        )
        original = EditorOverrides(operation_windows=(operation,))
        updates = (
            (
                "sleeve:site_1",
                with_sleeve(original, SleeveSiteOverride(1, 12.0, 6.0, 4.0)),
            ),
            (
                "observation_window:anterior",
                with_observation_window(
                    original,
                    ObservationWindowOverride("anterior", 42, 32, 0.5, 5.0, 90.0),
                ),
            ),
            (
                "connector:guide_1",
                with_connector(
                    original,
                    ConnectorAvoidanceOverride(1, 0.5, 1.0, "left"),
                ),
            ),
            (
                "press_anchor:1",
                with_surface_anchor(
                    original,
                    SurfaceAnchorOverride(
                        "press_anchor_1",
                        "template",
                        (1.0, 2.0, 3.0),
                        (0.0, 0.0, 1.0),
                    ),
                ),
            ),
            (
                "press_junction",
                with_press_junction(original, (1.0, 2.0, 3.0)),
            ),
        )

        for feature_id, updated in updates:
            with self.subTest(feature_id=feature_id):
                self.assertEqual(updated.operation_windows, (operation,))


if __name__ == "__main__":
    unittest.main()

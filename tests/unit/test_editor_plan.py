import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from twin_guide.config import ConnectorAvoidanceOverride, EditorOverrides
from twin_guide.editor_plan import (
    EDITOR_PLAN_SCHEMA,
    build_editor_plan,
    editor_geometry_fingerprint,
    editor_plan_fingerprint,
    editor_snapshot_matches,
)
from twin_guide.geometry import Vec3


class EditorPlanTests(unittest.TestCase):
    def test_snapshot_requires_matching_schema_revision_and_geometry(self):
        value = {
            "schema_version": "twin-guide.ui-editor-snapshot/2.0",
            "revision": 4,
            "geometry_fingerprint": "geometry-4",
        }

        self.assertTrue(
            editor_snapshot_matches(
                value,
                revision=4,
                geometry_fingerprint="geometry-4",
            )
        )
        self.assertFalse(
            editor_snapshot_matches(
                value,
                revision=5,
                geometry_fingerprint="geometry-4",
            )
        )

    def test_fingerprint_uses_semantic_config_and_ignores_output_directory(self):
        @dataclass(frozen=True)
        class Inputs:
            template: Path
            guide_sleeve_assemblies: tuple[Path, ...]
            patient_dentition: Path

        @dataclass(frozen=True)
        class Config:
            case_id: str
            inputs: Inputs
            output_directory: Path
            value: float
            editor_overrides: EditorOverrides = field(default_factory=EditorOverrides)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "case.yaml"
            config_path.write_text("first formatting", encoding="utf-8")
            paths = [root / name for name in ("template.stl", "sleeve.stl", "teeth.stl")]
            for path in paths:
                path.write_bytes(b"mesh")
            inputs = Inputs(paths[0], (paths[1],), paths[2])
            first = Config("demo", inputs, root / "formal", 1.0)
            second = Config("demo", inputs, root / "ui-plan", 1.0)
            adjusted = Config(
                "demo",
                inputs,
                root / "ui-plan",
                1.0,
                EditorOverrides(
                    connector_avoidance=(
                        ConnectorAvoidanceOverride(1, 0.6, 2.0),
                    )
                ),
            )

            first_value = editor_plan_fingerprint(first, config_path)
            config_path.write_text("different formatting", encoding="utf-8")

            self.assertEqual(
                first_value,
                editor_plan_fingerprint(second, config_path),
            )
            self.assertEqual(first_value, editor_plan_fingerprint(adjusted, config_path))
            self.assertNotEqual(
                editor_geometry_fingerprint(first, config_path),
                editor_geometry_fingerprint(adjusted, config_path),
            )

    def test_builds_stable_feature_ids_directly_from_generation_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "case.yaml"
            config_path.write_text("case: demo", encoding="utf-8")
            paths = [root / name for name in ("template.stl", "sleeve.stl", "teeth.stl")]
            for path in paths:
                path.write_bytes(b"mesh")
            config = SimpleNamespace(
                case_id="demo",
                editor_overrides=EditorOverrides(),
                inputs=SimpleNamespace(
                    template=paths[0],
                    guide_sleeve_assemblies=(paths[1],),
                    patient_dentition=paths[2],
                ),
                tooth_identification=None,
                windows=SimpleNamespace(
                    operation_front_axial_margin_mm=1.0,
                    operation_rear_axial_margin_mm=0.5,
                    operation_tangent_margin_mm=0.8,
                    operation_bitangent_margin_mm=0.6,
                ),
            )

            @dataclass(frozen=True)
            class Sleeve:
                guide_index: int
                parameters: dict[str, object]

            @dataclass(frozen=True)
            class Window:
                center: Vec3
                normal: Vec3
                tangent: Vec3
                width_mm: float
                height_mm: float
                depth_mm: float
                purpose: str = "operation"

            @dataclass(frozen=True)
            class Link:
                guide_index: int
                sleeve_label: str
                start: Vec3
                tube_contact: Vec3
                end: Vec3
                centerline: tuple[Vec3, ...]

            @dataclass(frozen=True)
            class Anchor:
                surface_anchor: Vec3
                surface_normal: Vec3

            context = SimpleNamespace(
                config=config,
                sleeve_generation=SimpleNamespace(
                    sleeves=(Sleeve(1, {"axis": [0, 0, 1]}),)
                ),
                tooth_identification=None,
                case=SimpleNamespace(
                    operation_features=(
                        SimpleNamespace(center=Vec3(0.0, 0.0, 0.0)),
                    )
                ),
                window_cutouts=SimpleNamespace(
                    windows=(
                        Window(
                            Vec3(0.25, 0.0, 0.0),
                            Vec3(1.0, 0.0, 0.0),
                            Vec3(0.0, 1.0, 0.0),
                            5.6,
                            4.2,
                            4.5,
                        ),
                    )
                ),
                point_linking=SimpleNamespace(
                    links=(
                        Link(
                            1,
                            "upper",
                            Vec3(-1.0, 0.0, 0.0),
                            Vec3(0.0, 0.0, 0.0),
                            Vec3(1.0, 0.0, 0.0),
                            (Vec3(-1.0, 0.0, 0.0), Vec3(1.0, 0.0, 0.0)),
                        ),
                    )
                ),
                press_beam_points=SimpleNamespace(
                    guide_anchors=(
                        Anchor(Vec3(0.0, 1.0, 0.0), Vec3(0.0, 0.0, 1.0)),
                    ),
                    junction=Vec3(0.0, 0.0, 1.0),
                    junction_axis=Vec3(0.0, 0.0, 1.0),
                ),
            )

            plan = build_editor_plan(context, config_path)
            identifiers = {item["id"] for item in plan["features"]}
            operation = next(
                item for item in plan["features"] if item["id"] == "operation_window:1"
            )["geometry"]

            self.assertEqual(plan["schema_version"], EDITOR_PLAN_SCHEMA)
            self.assertAlmostEqual(operation["base_depth_mm"], 3.0)
            self.assertAlmostEqual(operation["base_width_mm"], 4.0)
            self.assertAlmostEqual(operation["base_height_mm"], 3.0)
            self.assertAlmostEqual(operation["front_axial_margin_mm"], 1.0)
            self.assertAlmostEqual(operation["rear_axial_margin_mm"], 0.5)
            self.assertEqual(
                identifiers,
                {
                    "sleeve:guide_1",
                    "operation_window:1",
                    "connector:guide_1",
                    "press_anchor:1",
                    "press_junction",
                },
            )


if __name__ == "__main__":
    unittest.main()

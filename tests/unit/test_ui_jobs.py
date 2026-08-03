import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from twin_guide.blender_ui_worker import _remove_stage_documents, run_job
from twin_guide.config import EditorOverrides
from twin_guide.ui_jobs import promote_candidate, read_manifest, write_manifest


class UiJobTests(unittest.TestCase):
    def test_ui_job_cleanup_removes_only_stage_documents(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            stage_json = output / "stage-02-tooth-detection.json"
            stage_png = output / "stage-03-window.png"
            plan = output / "ui-editor-plan.json"
            model = output / "twin_guide.stl"
            for path in (stage_json, stage_png, plan, model):
                path.write_text(path.name, encoding="utf-8")

            _remove_stage_documents(output)

            self.assertFalse(stage_json.exists())
            self.assertFalse(stage_png.exists())
            self.assertTrue(plan.exists())
            self.assertTrue(model.exists())

    def test_manifest_round_trip_and_candidate_promotion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / "candidate"
            formal = root / "formal"
            candidate.mkdir()
            formal.mkdir()
            (candidate / "twin_guide.stl").write_text("new", encoding="utf-8")
            (candidate / "ui-task.json").write_text("transient", encoding="utf-8")
            (formal / "twin_guide.stl").write_text("old", encoding="utf-8")
            manifest = candidate / "ui-task.json"

            write_manifest(manifest, {"status": "completed"})
            promoted = promote_candidate(candidate, formal)

            self.assertEqual(read_manifest(manifest), {"status": "completed"})
            self.assertEqual((formal / "twin_guide.stl").read_text(), "new")
            self.assertEqual(promoted, (formal / "twin_guide.stl",))
            self.assertFalse((formal / "ui-task.json").exists())
            json.loads(manifest.read_text(encoding="utf-8"))

    def test_preview_skips_validation_and_failed_final_keeps_formal_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            formal = root / "formal"
            preview = root / "preview"
            candidate = root / "candidate"
            formal.mkdir()
            (formal / "twin_guide.stl").write_text("old", encoding="utf-8")
            config = SimpleNamespace(output_directory=formal)

            def generate(job_config, *, preview=False):
                job_config.output_directory.mkdir(parents=True, exist_ok=True)
                model = job_config.output_directory / "twin_guide.stl"
                model.write_text(
                    "preview" if preview else "candidate",
                    encoding="utf-8",
                )
                return SimpleNamespace(model_path=model)

            def generate_preview(job_config):
                return generate(job_config, preview=True), SimpleNamespace(
                    context=object()
                )

            def write_snapshot(_context, _config_path, output, **_values):
                path = output / "ui-editor-snapshot.json"
                path.write_text("{}", encoding="utf-8")
                return path

            with patch(
                "twin_guide.blender_ui_worker.CaseConfig.from_yaml",
                return_value=config,
            ), patch(
                "twin_guide.blender_ui_worker.replace",
                side_effect=lambda _config, **values: SimpleNamespace(**values),
            ), patch(
                "twin_guide.blender_ui_worker._generate_with_process",
                side_effect=generate_preview,
            ), patch(
                "twin_guide.editor_plan.write_editor_plan",
                side_effect=write_snapshot,
            ), patch(
                "twin_guide.editor_plan.editor_geometry_fingerprint",
                return_value="geometry-12",
            ):
                run_job(
                    "preview",
                    root / "case.yaml",
                    preview,
                    preview / "task.json",
                    revision=12,
                )

            self.assertEqual(
                read_manifest(preview / "task.json")["validation"],
                "not_run",
            )
            self.assertEqual(read_manifest(preview / "task.json")["revision"], 12)
            self.assertEqual(
                Path(read_manifest(preview / "task.json")["editor_snapshot_path"]).name,
                "ui-editor-snapshot.json",
            )
            self.assertEqual(
                (preview / "twin_guide.stl").read_text(encoding="utf-8"),
                "preview",
            )

            failed = SimpleNamespace(name="topology", passed=False, metrics={})
            def generate_final(job_config, *, preview=False):
                return generate(job_config, preview=preview), SimpleNamespace(
                    context=object()
                )

            with patch(
                "twin_guide.blender_ui_worker.CaseConfig.from_yaml",
                return_value=config,
            ), patch(
                "twin_guide.blender_ui_worker.replace",
                side_effect=lambda _config, **values: SimpleNamespace(**values),
            ), patch(
                "twin_guide.blender_ui_worker._generate_with_process",
                side_effect=generate_final,
            ), patch(
                "twin_guide.editor_plan.write_editor_plan",
                side_effect=write_snapshot,
            ), patch(
                "twin_guide.editor_plan.editor_geometry_fingerprint",
                return_value="geometry-final",
            ), patch(
                "twin_guide.blender_ui_worker._validate",
                return_value=(failed,),
            ):
                run_job("final", root / "case.yaml", candidate, candidate / "task.json")

            self.assertEqual((formal / "twin_guide.stl").read_text(), "old")
            self.assertEqual(
                read_manifest(candidate / "task.json")["status"],
                "validation_failed",
            )

    def test_successful_final_promotes_model_and_matching_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            formal = root / "formal"
            candidate = root / "candidate"
            formal.mkdir()
            (formal / "twin_guide.stl").write_text("old", encoding="utf-8")
            config = SimpleNamespace(output_directory=formal)

            def generate(job_config, *, preview=False):
                self.assertFalse(preview)
                job_config.output_directory.mkdir(parents=True, exist_ok=True)
                model = job_config.output_directory / "twin_guide.stl"
                model.write_text("verified", encoding="utf-8")
                return SimpleNamespace(model_path=model), SimpleNamespace(
                    context=object()
                )

            def write_snapshot(_context, _config_path, output, **values):
                self.assertTrue(values["snapshot"])
                self.assertEqual(values["revision"], 8)
                path = output / "ui-editor-snapshot.json"
                path.write_text("snapshot", encoding="utf-8")
                return path

            passed = SimpleNamespace(name="topology", passed=True, metrics={})
            with patch(
                "twin_guide.blender_ui_worker.CaseConfig.from_yaml",
                return_value=config,
            ), patch(
                "twin_guide.blender_ui_worker.replace",
                side_effect=lambda _config, **values: SimpleNamespace(**values),
            ), patch(
                "twin_guide.blender_ui_worker._generate_with_process",
                side_effect=generate,
            ), patch(
                "twin_guide.editor_plan.write_editor_plan",
                side_effect=write_snapshot,
            ), patch(
                "twin_guide.editor_plan.editor_geometry_fingerprint",
                return_value="geometry-8",
            ), patch(
                "twin_guide.blender_ui_worker._validate",
                return_value=(passed,),
            ):
                run_job(
                    "final",
                    root / "case.yaml",
                    candidate,
                    candidate / "task.json",
                    revision=8,
                )

            manifest = read_manifest(candidate / "task.json")
            assert manifest is not None
            self.assertEqual(manifest["status"], "completed")
            self.assertEqual(manifest["validation"], "passed")
            self.assertEqual(manifest["revision"], 8)
            self.assertEqual(
                (formal / "twin_guide.stl").read_text(encoding="utf-8"),
                "verified",
            )
            self.assertEqual(
                (formal / "ui-editor-snapshot.json").read_text(encoding="utf-8"),
                "snapshot",
            )

    def test_plan_mode_runs_planning_without_entity_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "plan"
            manifest = output / "task.json"
            plan_path = output / "ui-editor-plan.json"
            config = SimpleNamespace(output_directory=root / "formal")
            process = Mock()
            process.return_value = SimpleNamespace(context="context")
            write_plan = Mock(return_value=plan_path)
            fake_modules = {
                "twin_guide.generation_process": SimpleNamespace(
                    run_generation_process=process
                ),
                "twin_guide.editor_plan": SimpleNamespace(
                    write_editor_plan=write_plan,
                    editor_geometry_fingerprint=Mock(return_value="geometry-7"),
                ),
            }

            with patch.dict(sys.modules, fake_modules), patch(
                "twin_guide.blender_ui_worker.CaseConfig.from_yaml",
                return_value=config,
            ), patch(
                "twin_guide.blender_ui_worker.replace",
                side_effect=lambda source, **values: SimpleNamespace(
                    **(vars(source) | values)
                ),
            ), patch(
                "twin_guide.blender_ui_worker._generate_with_process"
            ) as generate:
                run_job(
                    "plan",
                    root / "case.yaml",
                    output,
                    manifest,
                    revision=7,
                )

            process.assert_called_once()
            self.assertEqual(
                process.call_args.args[0].output_directory,
                output.resolve(),
            )
            self.assertEqual(
                process.call_args.args[0].editor_overrides,
                EditorOverrides(),
            )
            self.assertEqual(
                process.call_args.kwargs,
                {
                    "require_observation_qa": False,
                    "write_stage_documents": False,
                    "include_clearance_adjustment": False,
                    "include_observation_window_geometry": False,
                },
            )
            write_plan.assert_called_once()
            self.assertEqual(write_plan.call_args.args[0], "context")
            generate.assert_not_called()
            self.assertEqual(
                read_manifest(manifest),
                {
                    "status": "completed",
                    "mode": "plan",
                    "revision": 7,
                    "plan_path": str(plan_path),
                    "geometry_fingerprint": "geometry-7",
                },
            )


if __name__ == "__main__":
    unittest.main()

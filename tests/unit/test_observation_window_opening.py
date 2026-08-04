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
    @staticmethod
    def _inputs(root: Path):
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
        return config, identification

    def test_preview_can_return_failed_qa_cutter_without_approving_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config, identification = self._inputs(root)
            final_report = root / "failed-report.json"
            failed_report = {
                "QA": {"axis_sweep_exposes_dental_surface": False},
                "outputs": {
                    "report_json": str(final_report),
                    "combined_cutter_ply": str(root / "failed-cutter.ply"),
                },
            }
            sentinel = object()

            with patch(
                "twin_guide.observation_window_engine.run",
                return_value=failed_report,
            ), patch(
                "twin_guide.observation_window_opening._profile_from_report",
                return_value=sentinel,
            ):
                result = build_observation_window_opening(
                    config,
                    identification,
                    require_qa=False,
                )

            manifest = json.loads(
                (
                    config.output_directory
                    / ".cache/stage-03-cutout-planning/manifest.json"
                ).read_text(encoding="utf-8")
            )
            self.assertIs(result, sentinel)
            self.assertEqual(manifest["status"], "preview_qa_failed")
            self.assertFalse(manifest["QA"]["axis_sweep_exposes_dental_surface"])

    def test_failed_final_qa_never_returns_a_cutter(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config, identification = self._inputs(root)
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

    def test_fast_preview_skips_full_observation_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config, identification = self._inputs(root)
            preview_report = {
                "QA": {},
                "outputs": {
                    "report_json": str(root / "preview-report.json"),
                    "combined_cutter_ply": str(root / "preview-cutter.ply"),
                },
            }
            sentinel = object()

            with patch(
                "twin_guide.observation_window_engine.build_preview",
                return_value=preview_report,
            ) as build_preview, patch(
                "twin_guide.observation_window_engine.run"
            ) as full_run, patch(
                "twin_guide.observation_window_opening._profile_from_report",
                return_value=sentinel,
            ):
                result = build_observation_window_opening(
                    config,
                    identification,
                    require_qa=False,
                    fast_preview=True,
                )

            self.assertIs(result, sentinel)
            build_preview.assert_called_once()
            full_run.assert_not_called()

    def test_preview_cache_ignores_regenerated_report_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config, identification = self._inputs(root)
            identification.mapping_report.update(
                created_at="first",
                sources={"report": "/tmp/first"},
                outputs={"report": "/tmp/first-output"},
            )
            identification.mapping_report_path.write_text(
                json.dumps(identification.mapping_report),
                encoding="utf-8",
            )
            final_report = root / "failed-report.json"
            final_report.write_text("{}", encoding="utf-8")
            failed_report = {
                "QA": {"axis_sweep_exposes_dental_surface": False},
                "outputs": {
                    "report_json": str(final_report),
                    "combined_cutter_ply": str(root / "failed-cutter.ply"),
                },
            }
            sentinel = object()

            with patch(
                "twin_guide.observation_window_engine.run",
                return_value=failed_report,
            ) as mocked_run, patch(
                "twin_guide.observation_window_opening._profile_from_report",
                return_value=sentinel,
            ):
                first = build_observation_window_opening(
                    config,
                    identification,
                    require_qa=False,
                )
                identification.mapping_report.update(
                    created_at="second",
                    sources={"report": "/tmp/second"},
                    outputs={"report": "/tmp/second-output"},
                )
                identification.mapping_report_path.write_text(
                    json.dumps(identification.mapping_report),
                    encoding="utf-8",
                )
                second = build_observation_window_opening(
                    config,
                    identification,
                    require_qa=False,
                )

            self.assertIs(first, sentinel)
            self.assertIs(second, sentinel)
            mocked_run.assert_called_once()

    def test_formal_run_rebuilds_a_preview_only_cached_cutter(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config, identification = self._inputs(root)
            preview_report = root / "preview-report.json"
            preview_report.write_text("{}", encoding="utf-8")
            preview_result = {
                "QA": {"axis_sweep_exposes_dental_surface": False},
                "outputs": {
                    "report_json": str(preview_report),
                    "combined_cutter_ply": str(root / "preview-cutter.ply"),
                },
            }
            formal_report = root / "formal-report.json"
            formal_report.write_text("{}", encoding="utf-8")
            formal_result = {
                "QA": {"axis_sweep_exposes_dental_surface": True},
                "outputs": {
                    "report_json": str(formal_report),
                    "combined_cutter_ply": str(root / "formal-cutter.ply"),
                },
            }
            with patch(
                "twin_guide.observation_window_engine.run",
                side_effect=(preview_result, formal_result),
            ) as mocked_run, patch(
                "twin_guide.observation_window_opening._profile_from_report",
                return_value=object(),
            ):
                build_observation_window_opening(
                    config,
                    identification,
                    require_qa=False,
                )
                build_observation_window_opening(
                    config,
                    identification,
                    require_qa=True,
                )

            self.assertEqual(mocked_run.call_count, 2)


if __name__ == "__main__":
    unittest.main()

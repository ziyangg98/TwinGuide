import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from twin_guide import CaseConfig, generate_guide, run_generation_process, validate_guide
from twin_guide.case_analysis import analyze_case
from twin_guide.models import WindowPurpose
from twin_guide.types import SleeveGenerationResult, StageRunStatus
from twin_guide.window_cutouts import (
    OBSERVATION_DEPTH_MM,
    OBSERVATION_TARGET_DEPTH_MM,
    OBSERVATION_TARGET_LATERAL_MM,
    OBSERVATION_WIDTH_MM,
    plan_window_cutouts,
)


class EndToEndTests(unittest.TestCase):
    def test_current_case(self):
        code_directory = Path(__file__).resolve().parents[2]
        source_config = CaseConfig.from_json(code_directory / "examples" / "case.json")
        with tempfile.TemporaryDirectory() as temporary_output_directory:
            case_config = replace(
                source_config,
                output_directory=Path(temporary_output_directory),
            )
            case_analysis = analyze_case(case_config)
            sleeves = SleeveGenerationResult(
                case_analysis.guide_sleeves,
                case_analysis.template_frame,
            )
            cutout_plan = plan_window_cutouts(case_analysis, sleeves)
            self.assertEqual(plan_window_cutouts(case_analysis, sleeves), cutout_plan)

            observation_windows = tuple(
                window
                for window in cutout_plan.windows
                if window.purpose is WindowPurpose.OBSERVATION
            )
            self.assertEqual(len(observation_windows), 1)
            for window in observation_windows:
                bitangent = window.normal.normalized().cross(window.tangent.normalized())
                self.assertGreater(window.normal.dot(case_analysis.template_frame.depth), 0.0)
                self.assertGreater(bitangent.dot(case_analysis.template_frame.normal), 0.0)
                self.assertEqual(window.width_mm, OBSERVATION_WIDTH_MM)
                self.assertGreater(window.height_mm, 3.0)
                self.assertEqual(window.depth_mm, OBSERVATION_DEPTH_MM)
            self.assertEqual(OBSERVATION_TARGET_LATERAL_MM, 0.0)
            self.assertEqual(OBSERVATION_TARGET_DEPTH_MM, 22.4)
            build_artifacts = generate_guide(case_config)
            validation_results = validate_guide(build_artifacts.model_path, case_config)

            self.assertEqual(build_artifacts.model_path.name, "twin_guide.stl")
            self.assertTrue(build_artifacts.model_path.is_file())
            self.assertEqual(
                {image_path.name for image_path in build_artifacts.image_paths},
                {
                    "guide_iso.png",
                    "guide_top.png",
                    "guide_bottom.png",
                    "guide_side.png",
                    "guide_assembly.png",
                    "guide_connectors.png",
                    "cutouts.png",
                    "input_template.png",
                    "input_sleeves.png",
                    "input_patient_dentition.png",
                    "reconstructed_sleeves.png",
                    "link_points.png",
                },
            )
            self.assertEqual(
                {result.name for result in validation_results},
                {
                    "topology",
                    "guide_retention",
                    "guide_connectors",
                    "channels",
                    "windows",
                    "handpiece_clearance",
                },
            )
            validation_by_name = {result.name: result for result in validation_results}
            required_checks = (
                "topology",
                "guide_retention",
                "guide_connectors",
                "channels",
                "windows",
            )
            failed_checks = {
                name: validation_by_name[name].metrics
                for name in required_checks
                if not validation_by_name[name].passed
            }
            self.assertFalse(failed_checks, failed_checks)
            handpiece_result = validation_by_name["handpiece_clearance"]
            self.assertIsInstance(handpiece_result.passed, bool)
            self.assertEqual(
                set(handpiece_result.metrics),
                {
                    "triangle_overlap_count",
                    "sweep_inside_model_count",
                    "model_inside_sweep_count",
                    "minimum_distance_mm",
                    "required_clearance_mm",
                },
            )
            self.assertTrue(
                all(
                    isinstance(metric_value, int | float)
                    for metric_value in handpiece_result.metrics.values()
                )
            )

            process_result = run_generation_process(case_config)
            self.assertEqual(
                tuple(stage.status for stage in process_result.stages),
                (
                    StageRunStatus.COMPLETED,
                    StageRunStatus.SKIPPED,
                    StageRunStatus.COMPLETED,
                    StageRunStatus.COMPLETED,
                    StageRunStatus.SKIPPED,
                    StageRunStatus.COMPLETED,
                    StageRunStatus.SKIPPED,
                ),
            )
            self.assertIsNotNone(process_result.context.sleeve_generation)
            self.assertIsNotNone(process_result.context.window_cutouts)
            self.assertIsNotNone(process_result.context.template_link_points)
            self.assertIsNotNone(process_result.context.point_linking)
            self.assertFalse(process_result.context.point_linking.press_beam_links_included)


if __name__ == "__main__":
    unittest.main()

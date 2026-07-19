import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from twin_guide import CaseConfig, generate_guide, run_generation_process, validate_guide
from twin_guide.case_analysis import analyze_case
from twin_guide.models import WindowPurpose
from twin_guide.types import SleeveGenerationResult, StageRunStatus
from twin_guide.window_cutouts import (
    OBSERVATION_FORWARD_FRACTION,
    _center_observation_window,
    _nearest_surface_sample,
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
            self.assertEqual(len(observation_windows), 2)
            template_coordinates = tuple(
                case_analysis.template_frame.coordinates(sample.position)
                for sample in case_analysis.template_samples
            )
            lateral_values = tuple(value[0] for value in template_coordinates)
            depth_values = tuple(value[1] for value in template_coordinates)
            lateral_min, lateral_max = min(lateral_values), max(lateral_values)
            depth_midpoint = (min(depth_values) + max(depth_values)) * 0.5
            forward_shift_mm = (
                max(depth_values) - min(depth_values)
            ) * OBSERVATION_FORWARD_FRACTION
            baseline_targets = {
                "observation_window_left": lateral_min + (lateral_max - lateral_min) * 0.10,
                "observation_window_right": lateral_max - (lateral_max - lateral_min) * 0.10,
            }
            vertical = cutout_plan.windows[0].normal.normalized()
            for window in observation_windows:
                baseline_sample = _nearest_surface_sample(
                    case_analysis,
                    baseline_targets[window.name],
                    depth_midpoint,
                )
                baseline_center, _ = _center_observation_window(
                    case_analysis,
                    baseline_sample,
                    vertical,
                )
                baseline_lateral, baseline_depth, _ = case_analysis.template_frame.coordinates(
                    baseline_center
                )
                window_lateral, window_depth, _ = case_analysis.template_frame.coordinates(
                    window.center
                )
                self.assertAlmostEqual(window_lateral, baseline_lateral)
                self.assertAlmostEqual(window_depth, baseline_depth + forward_shift_mm)
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

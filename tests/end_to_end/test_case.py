import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from twin_guide import CaseConfig, generate_guide, run_generation_process, validate_guide
from twin_guide.case_analysis import analyze_case
from twin_guide.tooth_identification import identify_tooth_positions
from twin_guide.types import SleeveGenerationResult, StageRunStatus
from twin_guide.window_cutouts import plan_window_cutouts


class EndToEndTests(unittest.TestCase):
    def test_current_case(self):
        code_directory = Path(__file__).resolve().parents[2]
        case_path = code_directory.parent / "data/cases/single/tooth-17/case.yaml"
        if not case_path.is_file():
            self.skipTest("未提供 tooth-17 仓库外病例数据")
        source_config = CaseConfig.from_yaml(case_path)
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
            identification = identify_tooth_positions(case_config)
            cutout_plan = plan_window_cutouts(case_analysis, sleeves, identification)
            self.assertEqual(
                plan_window_cutouts(case_analysis, sleeves, identification),
                cutout_plan,
            )

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
                    "stage-06-structure-linking.png",
                    "stage-03-cutout-planning.png",
                    "stage-01-sleeve-reconstruction.png",
                    "stage-02-tooth-mapping.png",
                    "stage-04-anchor-selection.png",
                    "stage-05-press-beam.png",
                    "stage-07-clearance-adjustment.png",
                },
            )
            for stage_number, stage_stem in {
                1: "stage-01-sleeve-reconstruction",
                2: "stage-02-tooth-mapping",
                3: "stage-03-cutout-planning",
                4: "stage-04-anchor-selection",
                5: "stage-05-press-beam",
                6: "stage-06-structure-linking",
                7: "stage-07-clearance-adjustment",
            }.items():
                result_path = case_config.output_directory / f"{stage_stem}.json"
                overview_path = case_config.output_directory / f"{stage_stem}.png"
                self.assertTrue(result_path.is_file(), stage_number)
                self.assertTrue(overview_path.is_file(), stage_number)
            self.assertEqual(
                {result.name for result in validation_results},
                {
                    "topology",
                    "guide_retention",
                    "guide_connectors",
                    "connector_endpoint_reinforcement",
                    "press_beam",
                    "channels",
                    "observation_windows",
                    "terminal_distal_common_node",
                },
            )
            validation_by_name = {result.name: result for result in validation_results}
            required_checks = (
                "topology",
                "guide_retention",
                "guide_connectors",
                "connector_endpoint_reinforcement",
                "press_beam",
                "channels",
                "observation_windows",
                "terminal_distal_common_node",
            )
            failed_checks = {
                name: validation_by_name[name].metrics
                for name in required_checks
                if not validation_by_name[name].passed
            }
            self.assertFalse(failed_checks, failed_checks)
            process_result = run_generation_process(case_config)
            self.assertEqual(
                tuple(stage.status for stage in process_result.stages),
                (
                    StageRunStatus.COMPLETED,
                    StageRunStatus.COMPLETED,
                    StageRunStatus.COMPLETED,
                    StageRunStatus.COMPLETED,
                    StageRunStatus.COMPLETED,
                    StageRunStatus.COMPLETED,
                    StageRunStatus.COMPLETED,
                ),
            )
            self.assertIsNotNone(process_result.context.sleeve_generation)
            self.assertIsNotNone(process_result.context.window_cutouts)
            self.assertIsNotNone(process_result.context.template_link_points)
            self.assertIsNotNone(process_result.context.press_beam_points)
            self.assertIsNotNone(process_result.context.point_linking)


if __name__ == "__main__":
    unittest.main()

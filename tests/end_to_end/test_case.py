import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from twin_guide import CaseConfig, generate_guide, run_generation_process, validate_guide
from twin_guide.case_analysis import analyze_case
from twin_guide.config import SleeveGeometryMode
from twin_guide.models import WindowPurpose
from twin_guide.tooth_identification import identify_tooth_positions
from twin_guide.types import SleeveGenerationResult, StageRunStatus
from twin_guide.window_cutouts import plan_window_cutouts


class EndToEndTests(unittest.TestCase):
    def _assert_fdi_axis_sweep_window(self, config_path: Path) -> None:
        """已配置牙位映射时只生成第 3 步轴扫掠观察窗。"""

        case_config = CaseConfig.from_yaml(config_path)
        case_analysis = analyze_case(case_config)
        sleeves = SleeveGenerationResult(
            case_analysis.guide_sleeves,
            case_analysis.template_frame,
        )
        identification = identify_tooth_positions(case_config)
        cutout_plan = plan_window_cutouts(case_analysis, sleeves, identification)
        observation_windows = tuple(
            window
            for window in cutout_plan.windows
            if window.purpose is WindowPurpose.OBSERVATION
        )
        self.assertEqual(observation_windows, ())
        self.assertEqual(len(cutout_plan.profile_windows), 1)
        self.assertEqual(
            cutout_plan.profile_windows[0].window_ids,
            tuple(window.window_id for window in identification.windows),
        )

    def test_current_case(self):
        code_directory = Path(__file__).resolve().parents[2]
        case_path = (
            code_directory.parent / "data/cases/single/tooth-11/case.yaml"
        )
        if not case_path.is_file():
            self.skipTest("未提供 tooth-11 仓库外病例数据")
        for tooth_number in (11,):
            with self.subTest(tooth_number=tooth_number):
                self._assert_fdi_axis_sweep_window(case_path)
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
            sleeve_process_image = (
                "selected_input_sleeves.png"
                if case_config.sleeve_geometry_mode is SleeveGeometryMode.INPUT
                else "generated_sleeves.png"
            )
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
                    sleeve_process_image,
                    "link_points.png",
                    "press_beam.png",
                    "handpiece_avoidance.png",
                },
            )
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
            self.assertTrue(process_result.context.point_linking.press_beam_links_included)
            self.assertEqual(len(process_result.context.point_linking.press_beam_links), 3)
            press_plan = process_result.context.press_beam_points
            self.assertEqual(
                tuple(anchor.station_fdis for anchor in press_plan.guide_anchors),
                ((15, 14), (24, 25)),
            )
            self.assertEqual(
                tuple(anchor.ray_angle_degrees for anchor in press_plan.guide_anchors),
                (45.0, 45.0),
            )
            self.assertTrue(
                all(
                    anchor.arch_outward_coordinate_mm <= -0.5
                    for anchor in press_plan.guide_anchors
                )
            )
            self.assertEqual(press_plan.sleeve_anchor.label, "upper")
            self.assertEqual(
                press_plan.sleeve_anchor.guide_index,
                press_plan.inner_sleeve_scores[0].guide_index,
            )
            self.assertLess(
                press_plan.inner_sleeve_scores[0].outward_coordinate_mm,
                press_plan.inner_sleeve_scores[1].outward_coordinate_mm,
            )
            self.assertLessEqual(press_plan.junction_axial_error_mm, 1e-6)
            self.assertGreaterEqual(
                press_plan.junction_minimum_angle_degrees,
                25.0,
            )
            self.assertGreaterEqual(
                press_plan.junction_sleeve_distance_mm,
                6.0 - 1e-9,
            )
            self.assertLessEqual(
                press_plan.junction_sleeve_distance_error_mm,
                1e-6,
            )
            template_plan = process_result.context.template_link_points.template_points
            self.assertEqual(len(template_plan.trajectories), 4)
            for fdi in (13, 22):
                station_fdis = (fdi,)
                station_anchors = []
                station_angles = []
                for selection in template_plan.selections:
                    self.assertIsNotNone(selection.left)
                    self.assertIsNotNone(selection.right)
                    self.assertIsNotNone(selection.chosen_ray_angles_degrees)
                    if selection.left_station_fdis == station_fdis:
                        station_anchors.append(selection.left)
                        station_angles.append(selection.chosen_ray_angles_degrees[0])
                    if selection.right_station_fdis == station_fdis:
                        station_anchors.append(selection.right)
                        station_angles.append(selection.chosen_ray_angles_degrees[1])
                self.assertEqual(sorted(station_angles), [70.0, 90.0])
                self.assertEqual(len(station_anchors), 2)
                self.assertGreater(
                    station_anchors[0].position.distance_to(station_anchors[1].position),
                    1.0,
                )


if __name__ == "__main__":
    unittest.main()

"""仓库外规范病例数据的可选集成检查。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

from twin_guide.config import CaseConfig


class CaseDataLayoutTests(unittest.TestCase):
    """检查仓库外病例数据中的规范入口与来源文件映射。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.project = Path(__file__).resolve().parents[2]
        cls.data_root = cls.project.parent / "data"
        if not cls.data_root.is_dir():
            raise unittest.SkipTest("未提供仓库外 TwinGuide 病例数据")
        cls.dataset = cls.data_root / "cases/single"
        cls.case_names = tuple(
            f"tooth-{value}" for value in (11, 12, 13, 14, 15, 16, 17, 47)
        )
        cls.multiple_dataset = cls.data_root / "cases/multiple"
        cls.multiple_case_names = (
            "teeth-12-13",
            "teeth-14-15",
            "teeth-15-16",
            "teeth-16-17",
        )

    def test_case_yaml_object_paths_resolve_inside_case_directory(self) -> None:
        """每个 YAML 的启用对象必须指向本病例的规范输入目录。"""

        datasets = (
            (self.dataset, self.case_names),
            (self.multiple_dataset, self.multiple_case_names),
        )
        for dataset, case_names in datasets:
            for case_name in case_names:
                with self.subTest(case=case_name):
                    case_directory = dataset / case_name
                    self._assert_case_object_paths(case_directory)

    def test_blender_wrapper_returns_nonzero_for_python_failures(self) -> None:
        """Blender 启动器必须把未捕获 Python 异常传播为失败退出码。"""

        wrapper = (self.project / "scripts/blender.sh").read_text(encoding="utf-8")
        self.assertNotIn("--gpu-backend", wrapper)
        self.assertIn("--python-exit-code 1", wrapper)

    def _assert_case_object_paths(self, case_directory: Path) -> None:
        """检查一份病例 YAML 的全部对象路径。"""
        case_yaml = case_directory / "case.yaml"
        self.assertTrue(case_yaml.is_file())
        content = yaml.safe_load(case_yaml.read_text(encoding="utf-8"))
        objects = content["objects"]
        paths: list[str] = []
        for key in ("dental", "guide", "cutter"):
            value = objects.get(key, {}).get("path")
            if value:
                paths.append(value)
        for key in ("sleeve", "handpiece"):
            records = objects.get(key, {}).get("files", [])
            paths.extend(record["path"] for record in records)
        for value in paths:
            resolved = (case_directory / value).resolve()
            self.assertTrue(resolved.is_relative_to(case_directory.resolve()))
            self.assertTrue(resolved.is_file(), resolved)

    def test_multiple_cases_keep_two_sleeve_assemblies_separate(self) -> None:
        """多颗病例在输入层保留两个种植位的独立导管装配体。"""

        for case_name in self.multiple_case_names:
            with self.subTest(case=case_name):
                case_yaml = self.multiple_dataset / case_name / "case.yaml"
                content = yaml.safe_load(case_yaml.read_text(encoding="utf-8"))
                sleeves = content["objects"]["sleeve"]
                self.assertEqual(len(sleeves["files"]), 2)
                self.assertEqual(len(sleeves["active_ids"]), 2)

    def test_all_cases_have_no_legacy_algorithm_selection(self) -> None:
        """唯一生产算法不再需要病例级策略开关。"""

        datasets = (
            (self.dataset, self.case_names),
            (self.multiple_dataset, self.multiple_case_names),
        )
        for dataset, case_names in datasets:
            for case_name in case_names:
                with self.subTest(case=case_name):
                    case_yaml = dataset / case_name / "case.yaml"
                    content = yaml.safe_load(case_yaml.read_text(encoding="utf-8"))
                    self.assertNotIn("algorithms", content["design"])

    def test_multiple_cases_define_continuous_frame_parameters(self) -> None:
        """多颗 YAML 完整定义跨两个种植位的连接梁和按压梁。"""

        for case_name in self.multiple_case_names:
            with self.subTest(case=case_name):
                case_yaml = self.multiple_dataset / case_name / "case.yaml"
                content = yaml.safe_load(case_yaml.read_text(encoding="utf-8"))
                design = content["design"]
                planning = content["planning"]
                self.assertEqual(len(planning["implant_sites"]), 2)
                frame = planning["connector_frame"]
                guide_anchor_records = design["guide_anchors"]["anchors"]
                endpoint_ids = list(
                    dict.fromkeys(record["endpoint"] for record in guide_anchor_records)
                )
                if case_name == "teeth-16-17":
                    self.assertEqual(
                        endpoint_ids,
                        ["s_mesial"],
                    )
                    self.assertEqual(
                        frame["path_sequence"],
                        [
                            "s_mesial",
                            "implant_site_1",
                            "implant_site_2",
                            "terminal_distal_common_node",
                        ],
                    )
                else:
                    self.assertEqual(
                        endpoint_ids,
                        ["s_minus", "s_plus"],
                    )
                    self.assertEqual(
                        frame["path_sequence"],
                        ["s_minus", "implant_site_1", "implant_site_2", "s_plus"],
                    )
                for endpoint_id in endpoint_ids:
                    endpoint_records = [
                        record
                        for record in guide_anchor_records
                        if record["endpoint"] == endpoint_id
                    ]
                    self.assertEqual(len(endpoint_records), 2)
                    self.assertEqual(
                        {record["side"] for record in endpoint_records},
                        {"u_side", "back_u_side"},
                    )
                    for record in endpoint_records:
                        station = record["station"]
                        self.assertIn(
                            station["type"],
                            {"tooth_center", "tooth_pair_midpoint"},
                        )
                        if station["type"] == "tooth_center":
                            self.assertIsInstance(station["fdi"], int)
                        else:
                            self.assertEqual(len(station["fdis"]), 2)
                self.assertEqual(frame["levels"], ["upper", "lower"])
                press_beam = design["press_beam"]
                expected_press_station_count = (
                    3 if press_beam["mode"] == "three_tooth_anchors_y" else 2
                )
                self.assertEqual(
                    len(press_beam["stations"]), expected_press_station_count
                )
                if press_beam["mode"] == "inner_sleeve_upper_y":
                    self.assertEqual(
                        press_beam["sleeve_anchor_selection"]["distance_score"],
                        "maximin_to_two_guide_anchors",
                    )
                else:
                    self.assertNotIn("sleeve_anchor_selection", press_beam)
                operation = planning["operation_windows"]
                self.assertEqual(operation["mode"], "per_implant_site")
                self.assertEqual(operation["overlap_rule"], "union_cutters")
                self.assertEqual(operation["cut_target"], "guide_template_only")
                self.assertEqual(len(operation["sites"]), 2)
                sleeve_by_fdi = {
                    site["fdi"]: site["sleeve_id"]
                    for site in planning["implant_sites"]
                }
                for site in operation["sites"]:
                    self.assertEqual(
                        site["sleeve_assembly_id"], sleeve_by_fdi[site["fdi"]]
                    )

    def test_all_cases_use_independent_guide_anchor_records(self) -> None:
        """全部正式病例逐锚点声明牙位、侧别和角度，不再依赖成对站位。"""

        datasets = (
            (self.dataset, self.case_names),
            (self.multiple_dataset, self.multiple_case_names),
        )
        terminal_cases = {"tooth-17", "teeth-16-17"}
        for dataset, case_names in datasets:
            for case_name in case_names:
                with self.subTest(case=case_name):
                    case_yaml = dataset / case_name / "case.yaml"
                    content = yaml.safe_load(case_yaml.read_text(encoding="utf-8"))
                    guide_anchors = content["design"]["guide_anchors"]
                    self.assertNotIn("stations", guide_anchors)
                    self.assertNotIn("u_side_ray_angle_degrees", guide_anchors)
                    self.assertNotIn("back_u_side_ray_angle_degrees", guide_anchors)
                    records = guide_anchors["anchors"]
                    expected_count = 2 if case_name in terminal_cases else 4
                    self.assertEqual(len(records), expected_count)
                    for record in records:
                        self.assertIn(record["side"], {"u_side", "back_u_side"})
                        self.assertIn("station", record)
                        self.assertGreater(record["ray_angle_degrees"], 0.0)

    def test_all_cases_explicitly_select_input_sleeve_geometry(self) -> None:
        """当前正式病例直接保留输入导管，模式选择必须显式可审计。"""

        datasets = (
            (self.dataset, self.case_names),
            (self.multiple_dataset, self.multiple_case_names),
        )
        for dataset, case_names in datasets:
            for case_name in case_names:
                with self.subTest(case=case_name):
                    case_yaml = dataset / case_name / "case.yaml"
                    content = yaml.safe_load(case_yaml.read_text(encoding="utf-8"))
                    self.assertEqual(
                        content["design"]["sleeve_geometry"],
                        {"mode": "input"},
                    )

    def test_single_case_yaml_is_complete_and_loadable(self) -> None:
        """每个单颗病例只用自身 YAML 提供完整运行配置。"""

        for case_name in self.case_names:
            path = self.dataset / case_name / "case.yaml"
            with self.subTest(config=path):
                config = CaseConfig.from_yaml(path)
                self.assertTrue(config.inputs.template.is_file())
                self.assertTrue(config.inputs.guide_sleeve_assembly.is_file())
                self.assertTrue(config.inputs.patient_dentition.is_file())
                self.assertTrue(config.tooth_identification.case_yaml.is_file())
                self.assertGreaterEqual(len(config.handpiece_avoidance), 1)
                for avoidance in config.handpiece_avoidance:
                    self.assertTrue(avoidance.handpiece.is_file())
                    self.assertTrue(avoidance.stop_report.is_file())

    def test_multiple_case_yaml_is_complete_and_loadable(self) -> None:
        """每个多颗病例只用自身 YAML 提供完整运行配置。"""

        for case_name in self.multiple_case_names:
            path = self.multiple_dataset / case_name / "case.yaml"
            with self.subTest(config=path.name):
                config = CaseConfig.from_yaml(path)
                self.assertEqual(
                    config.tooth_identification.case_yaml,
                    path.resolve(),
                )
                self.assertEqual(len(config.inputs.guide_sleeve_assemblies), 2)

    def test_input_files_are_nonempty(self) -> None:
        """复制后的 STL 和止挡报告不得为空文件。"""

        patterns = (
            self.dataset.glob("tooth-*/input/*"),
            self.multiple_dataset.glob("teeth-*/input/*"),
        )
        for paths in patterns:
            for path in paths:
                with self.subTest(path=path):
                    self.assertGreater(path.stat().st_size, 0)
                    if path.suffix == ".json":
                        json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

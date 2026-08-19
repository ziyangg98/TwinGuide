"""仓库外规范病例数据的可选集成检查。"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

import yaml

from twin_guide.config import CaseConfig


class CaseDataLayoutTests(unittest.TestCase):
    """检查扁平匿名病例目录、索引与正式运行配置。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.project = Path(__file__).resolve().parents[2]
        cls.data_root = cls.project.parent / "data"
        cls.dataset = cls.data_root / "cases"
        if not (cls.dataset / "index.yaml").is_file():
            raise unittest.SkipTest("未提供仓库外 TwinGuide 病例数据")
        cls.index = yaml.safe_load((cls.dataset / "index.yaml").read_text(encoding="utf-8"))
        cls.case_records = tuple(cls.index["cases"])
        cls.configured_records = tuple(
            record for record in cls.case_records if record["status"] == "configured"
        )

    def test_index_and_directories_are_consistent(self) -> None:
        """索引必须逐一覆盖匿名病例目录，且不得使用旧分类路径。"""

        indexed = {record["path"] for record in self.case_records}
        observed = {path.name for path in self.dataset.glob("case-*") if path.is_dir()}
        self.assertEqual(indexed, observed)
        self.assertEqual(self.index["case_count"], len(observed))
        for name in observed:
            self.assertRegex(name, r"^case-[0-9a-f]{12}$")
            self.assertIsNone(re.search(r"AI[_-]|tooth|teeth|副本", name, re.I))

    def test_case_yaml_presence_matches_status(self) -> None:
        """只有 configured 病例可以在根目录提供正式 case.yaml。"""

        for record in self.case_records:
            with self.subTest(case=record["id"]):
                case_directory = self.dataset / record["path"]
                metadata = yaml.safe_load(
                    (case_directory / "metadata.yaml").read_text(encoding="utf-8")
                )
                self.assertEqual(metadata["id"], record["id"])
                self.assertEqual(metadata["patient_id"], record["patient_id"])
                self.assertEqual(metadata["status"], record["status"])
                self.assertEqual(
                    (case_directory / "case.yaml").is_file(),
                    record["status"] == "configured",
                )

    def test_case_yaml_object_paths_resolve_inside_case_directory(self) -> None:
        """每个正式 YAML 的启用对象必须指向本病例目录。"""

        for record in self.configured_records:
            with self.subTest(case=record["id"]):
                self._assert_case_object_paths(self.dataset / record["path"])

    def _assert_case_object_paths(self, case_directory: Path) -> None:
        case_yaml = case_directory / "case.yaml"
        content = yaml.safe_load(case_yaml.read_text(encoding="utf-8"))
        self.assertEqual(content["case"]["id"], case_directory.name)
        objects = content["objects"]
        paths: list[str] = []
        for key in ("dental", "guide", "cutter"):
            value = objects.get(key, {}).get("path")
            if value:
                paths.append(value)
        paths.extend(record["path"] for record in objects.get("handpiece", {}).get("files", []))
        for value in paths:
            resolved = (case_directory / value).resolve()
            self.assertTrue(resolved.is_relative_to(case_directory.resolve()))
            self.assertTrue(resolved.is_file(), resolved)

    def test_single_tooth_14_cases_use_standard_y_beam_without_terminal_u_extension(
        self,
    ) -> None:
        """全部单 14 病例均不应生成临床不需要的尾部 U 型梁。"""

        records = tuple(
            record for record in self.configured_records if tuple(record["implant_fdis"]) == (14,)
        )
        self.assertTrue(records)
        for record in records:
            with self.subTest(case=record["id"]):
                content = yaml.safe_load(
                    (self.dataset / record["path"] / "case.yaml").read_text(encoding="utf-8")
                )
                design = content["design"]
                self.assertNotIn("guide_terminal_u_extension", design)
                self.assertEqual(design["press_beam"]["mode"], "inner_sleeve_upper_y")

    def test_configured_cases_use_current_design_schema(self) -> None:
        """正式病例不再配置旧算法或导管实体来源分支。"""

        for record in self.configured_records:
            case_yaml = self.dataset / record["path"] / "case.yaml"
            content = yaml.safe_load(case_yaml.read_text(encoding="utf-8"))
            with self.subTest(case=record["id"]):
                self.assertNotIn("algorithms", content["design"])
                self.assertNotIn("sleeve_geometry", content["design"])
                self.assertNotIn("sleeve", content["objects"])

    def test_configured_cases_use_independent_guide_anchor_records(self) -> None:
        """正式病例逐锚点声明牙位、侧别和角度。"""

        for record in self.configured_records:
            case_yaml = self.dataset / record["path"] / "case.yaml"
            content = yaml.safe_load(case_yaml.read_text(encoding="utf-8"))
            guide_anchors = content["design"]["guide_anchors"]
            with self.subTest(case=record["id"]):
                self.assertNotIn("stations", guide_anchors)
                self.assertNotIn("u_side_ray_angle_degrees", guide_anchors)
                self.assertNotIn("back_u_side_ray_angle_degrees", guide_anchors)
                records = guide_anchors["anchors"]
                expected_count = 2 if record["implant_fdis"] == [17] else 4
                self.assertEqual(len(records), expected_count)
                for anchor in records:
                    self.assertIn(anchor["side"], {"u_side", "back_u_side"})
                    self.assertIn("station", anchor)
                    self.assertGreater(anchor["ray_angle_degrees"], 0.0)

    def test_press_beam_station_records_use_only_applicable_fields(self) -> None:
        """按压梁站位不保留模式无关字段。"""

        for record in self.configured_records:
            case_yaml = self.dataset / record["path"] / "case.yaml"
            content = yaml.safe_load(case_yaml.read_text(encoding="utf-8"))
            for station in content["design"]["press_beam"]["stations"]:
                with self.subTest(case=record["id"], station=station):
                    expected = {"type", "ray_angle_degrees"}
                    if station["type"] == "tooth_center":
                        expected.add("fdi")
                    else:
                        self.assertEqual(station["type"], "tooth_pair_midpoint")
                        expected.add("fdis")
                    self.assertEqual(set(station), expected)

    def test_migrated_case_yaml_is_complete_and_loadable(self) -> None:
        """每份正式配置均可加载，且内部ID与匿名目录一致。"""

        for record in self.configured_records:
            path = self.dataset / record["path"] / "case.yaml"
            config = CaseConfig.from_yaml(path)
            with self.subTest(case=record["id"]):
                self.assertEqual(config.case_id, record["id"])
                self.assertTrue(config.inputs.template.is_file())
                self.assertTrue(config.inputs.patient_dentition.is_file())
                self.assertGreaterEqual(len(config.guide_posts), 1)

    def test_input_files_are_nonempty(self) -> None:
        """规范化后的输入文件不得为空。"""

        for case_directory in self.dataset.glob("case-*"):
            for path in (case_directory / "input").glob("*"):
                if not path.is_file():
                    continue
                with self.subTest(path=path):
                    self.assertGreater(path.stat().st_size, 0)
                    if path.suffix == ".json":
                        json.loads(path.read_text(encoding="utf-8"))

    def test_blender_wrapper_returns_nonzero_for_python_failures(self) -> None:
        """Blender启动器必须把未捕获Python异常传播为失败退出码。"""

        wrapper = (self.project / "scripts/blender.sh").read_text(encoding="utf-8")
        self.assertNotIn("--gpu-backend", wrapper)
        self.assertIn("--python-exit-code 1", wrapper)


if __name__ == "__main__":
    unittest.main()

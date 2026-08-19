"""检查源码文档的完整性和语言。"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

SOURCE_ROOT = Path(__file__).parents[2] / "src" / "twin_guide"
PROJECT_ROOT = Path(__file__).parents[2]
CHINESE_TEXT = re.compile(r"[\u3400-\u9fff]")
PUBLIC_FUNCTIONS = {
    "__init__.py": {"generate_guide", "validate_guide", "run_generation_process"},
    "clearance_adjustment.py": {"adjust_clearance"},
    "point_linking.py": {"link_selected_points"},
    "press_beam_points.py": {"select_press_beam_points"},
    "sleeve_anchors.py": {"select_sleeve_anchors"},
    "template_link_points.py": {"select_template_link_points"},
    "template_anchors.py": {"select_template_points"},
    "tooth_identification.py": {"identify_tooth_positions"},
    "window_cutouts.py": {"plan_window_cutouts"},
}


class DocumentationTest(unittest.TestCase):
    """验证模块、类和函数的中文文档。"""

    def test_every_module_class_and_function_has_chinese_docstring(self) -> None:
        """检查所有 Python 模块、类和函数均有中文文档字符串。"""

        problems: list[str] = []
        for path in sorted(SOURCE_ROOT.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            nodes = [tree, *ast.walk(tree)]
            for node in nodes:
                if not isinstance(
                    node,
                    (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
                ):
                    continue
                docstring = ast.get_docstring(node, clean=False)
                if docstring and CHINESE_TEXT.search(docstring):
                    continue
                name = getattr(node, "name", "<module>")
                line = getattr(node, "lineno", 1)
                problems.append(f"{path.relative_to(SOURCE_ROOT)}:{line}:{name}")
        self.assertEqual([], problems, "缺少中文文档字符串:\n" + "\n".join(problems))

    def test_public_functions_describe_parameters_and_returns(self) -> None:
        """检查公开函数文档是否说明参数和返回值。"""

        problems: list[str] = []
        for relative_path, function_names in PUBLIC_FUNCTIONS.items():
            path = SOURCE_ROOT / relative_path
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            functions = {
                node.name: node
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            for name in sorted(function_names):
                node = functions.get(name)
                if node is None:
                    problems.append(f"{relative_path}:{name}: 函数不存在")
                    continue
                docstring = ast.get_docstring(node) or ""
                missing = [title for title in ("参数:", "返回:") if title not in docstring]
                if missing:
                    problems.append(f"{relative_path}:{name}: 缺少 {', '.join(missing)}")
        self.assertEqual([], problems, "公开函数文档不完整:\n" + "\n".join(problems))

    def test_regression_cases_have_documented_results_and_images(self) -> None:
        """检查回归脚本中的十二个规范病例均有结果条目和最终图。"""

        single_cases = ("11", "12", "13", "14", "15", "16", "17", "47")
        multiple_cases = ("12-13", "14-15", "15-16", "16-17")
        document = (PROJECT_ROOT / "docs/guide/case-results.md").read_text(encoding="utf-8")
        for case in single_cases:
            with self.subTest(case=f"single_{case}"):
                self.assertIn(f"`single_{case}`", document)
                self.assertTrue(
                    (PROJECT_ROOT / f"docs/images/case-results/single-{case}.png").is_file()
                )
        for case in multiple_cases:
            case_id = case.replace("-", "_")
            with self.subTest(case=f"multiple_{case_id}"):
                self.assertIn(f"`multiple_{case_id}`", document)
                self.assertTrue(
                    (PROJECT_ROOT / f"docs/images/case-results/multiple-{case}.png").is_file()
                )

    def test_process_pages_identify_current_code_branches(self) -> None:
        """检查七阶段算法页标出源码入口和关键拓扑分支。"""

        process_root = PROJECT_ROOT / "docs/process"
        for stage in range(1, 8):
            path = next(process_root.glob(f"stage-{stage}-*.md"))
            with self.subTest(stage=stage):
                self.assertIn("## 代码对应", path.read_text(encoding="utf-8"))

        tooth_page = (process_root / "stage-2-teeth.md").read_text(encoding="utf-8")
        self.assertIn("find_shortest_concavity_chords", tooth_page)
        self.assertIn("legacy_midline_fallback", tooth_page)
        self.assertNotIn("代码对 $\\delta$ 等间取 25 个值", tooth_page)

        linking_page = (process_root / "stage-6-linking.md").read_text(encoding="utf-8")
        self.assertIn("多种植位连续路径不走这段代理点算法", linking_page)

        topology_page = (process_root / "special-topologies.md").read_text(encoding="utf-8")
        self.assertIn("末端牙的局部牙弓切向", topology_page)
        self.assertIn("## 代码对应", topology_page)


if __name__ == "__main__":
    unittest.main()

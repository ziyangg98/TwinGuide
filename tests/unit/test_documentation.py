"""检查源码文档的完整性和语言。"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

SOURCE_ROOT = Path(__file__).parents[2] / "src" / "twin_guide"
CHINESE_TEXT = re.compile(r"[\u3400-\u9fff]")
PUBLIC_FUNCTIONS = {
    "__init__.py": {"generate_guide", "validate_guide", "run_generation_process"},
    "point_linking.py": {"link_selected_points"},
    "sleeve_anchors.py": {"select_sleeve_anchors"},
    "sleeve_generation.py": {"recognize_and_build_sleeves"},
    "template_link_points.py": {"select_template_link_points"},
    "template_anchors.py": {"select_template_points"},
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


if __name__ == "__main__":
    unittest.main()

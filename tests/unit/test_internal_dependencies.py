"""检查 TwinGuide 不再依赖项目外部业务模块。"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src" / "twin_guide"
FORBIDDEN_BUSINESS_PACKAGES = {"scripts", "tooth_guide_mapping"}


class InternalDependencyTests(unittest.TestCase):
    """验证业务算法和启动路径完全位于 TwinGuide 内部。"""

    def test_source_has_no_external_business_imports(self) -> None:
        """禁止源码重新导入旧工作区包或脚本目录。"""

        violations: list[str] = []
        for path in sorted(SOURCE_ROOT.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules = [alias.name.split(".", 1)[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules = [node.module.split(".", 1)[0]]
                else:
                    continue
                forbidden = FORBIDDEN_BUSINESS_PACKAGES.intersection(modules)
                if forbidden:
                    violations.append(
                        f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}:"
                        f"{','.join(sorted(forbidden))}"
                    )
        self.assertEqual([], violations)

    def test_blender_environment_does_not_inherit_external_pythonpath(self) -> None:
        """启动脚本只声明项目内部 Python 搜索路径。"""

        content = (PROJECT_ROOT / "scripts/blender.sh").read_text(encoding="utf-8")
        pythonpath_line = next(
            line for line in content.splitlines() if line.startswith("export PYTHONPATH=")
        )
        self.assertNotIn("workspace_directory", content)
        self.assertNotIn("${PYTHONPATH", pythonpath_line)
        self.assertIn("$project_directory/src", pythonpath_line)
        self.assertNotIn("--gpu-backend", content)


if __name__ == "__main__":
    unittest.main()

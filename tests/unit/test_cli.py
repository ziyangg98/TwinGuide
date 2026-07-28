"""检查 TwinGuide 标准命令行入口。"""

from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

from twin_guide.cli import _command_arguments, _parser


class CommandLineTests(unittest.TestCase):
    """验证直接运行和 Blender 转发使用同一参数协议。"""

    def test_reads_standard_process_arguments(self) -> None:
        """普通 Python 入口应读取程序名之后的参数。"""

        with patch.object(sys, "argv", ["twinguide", "process", "--config", "case.yaml"]):
            self.assertEqual(
                _command_arguments(),
                ["process", "--config", "case.yaml"],
            )

    def test_reads_only_arguments_after_blender_separator(self) -> None:
        """Blender 入口应忽略 ``--`` 前的宿主参数。"""

        with patch.object(
            sys,
            "argv",
            [
                "Blender",
                "--background",
                "--python-expr",
                "entry",
                "--",
                "validate",
                "--config",
                "case.yaml",
                "--model",
                "guide.stl",
            ],
        ):
            self.assertEqual(
                _command_arguments(),
                [
                    "validate",
                    "--config",
                    "case.yaml",
                    "--model",
                    "guide.stl",
                ],
            )

    def test_exposes_three_commands_under_one_program_name(self) -> None:
        """唯一解析器应使用统一程序名并声明三个子命令。"""

        parser = _parser()

        self.assertEqual(parser.prog, "twinguide")
        subparsers_action = next(
            action
            for action in parser._actions
            if hasattr(action, "choices") and action.choices
        )
        self.assertEqual(set(subparsers_action.choices), {"generate", "process", "validate"})

if __name__ == "__main__":
    unittest.main()

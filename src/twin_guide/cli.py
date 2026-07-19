"""可在 Blender 中运行的命令行入口。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from twin_guide.config import CaseConfig
from twin_guide.guide_generation import generate_guide


def _blender_arguments() -> list[str]:
    """返回 Blender ``--`` 之后传给 Twinguide 的命令行参数。"""

    return sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []


def _parser() -> argparse.ArgumentParser:
    """构造 generate、process 和 validate 子命令的参数解析器。"""

    parser = argparse.ArgumentParser(prog="twin-guide")
    commands = parser.add_subparsers(dest="command", required=True)
    generate_command = commands.add_parser("generate", help="构建牙科导板并输出诊断图")
    generate_command.add_argument("--config", required=True, type=Path)
    process_command = commands.add_parser(
        "process", help="运行已实现的生成阶段"
    )
    process_command.add_argument("--config", required=True, type=Path)
    validate_command = commands.add_parser("validate", help="检查已导出的牙科导板 STL")
    validate_command.add_argument("--config", required=True, type=Path)
    validate_command.add_argument("--model", required=True, type=Path)
    return parser


def main() -> None:
    """执行用户选择的构建、阶段运行或检查命令。"""

    arguments = _parser().parse_args(_blender_arguments())
    config = CaseConfig.from_json(arguments.config)
    if arguments.command == "generate":
        artifacts = generate_guide(config)
        print(f"MODEL {artifacts.model_path}")
        for image_path in artifacts.image_paths:
            print(f"IMAGE {image_path}")
        return
    if arguments.command == "process":
        from twin_guide import run_generation_process

        result = run_generation_process(config)
        for stage in result.stages:
            print(
                f"STAGE {stage.definition.number} {stage.status.value} "
                f"{stage.definition.key} {stage.definition.maturity.value} "
                f"{stage.reason or ''}".rstrip()
            )
        return
    from twin_guide.guide_validation import validate_guide

    results = validate_guide(arguments.model, config)
    for result in results:
        status = "通过" if result.passed else "失败"
        print(f"{status} {result.name} {result.metrics}")
    if not all(result.passed for result in results):
        raise SystemExit(1)

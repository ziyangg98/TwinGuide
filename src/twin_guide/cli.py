"""可在 Blender 中运行的命令行入口。"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from twin_guide.config import CaseConfig, require_production_review


def _command_arguments() -> list[str]:
    """返回标准命令行或 Blender ``--`` 之后的参数。"""

    return (
        sys.argv[sys.argv.index("--") + 1 :]
        if "--" in sys.argv
        else sys.argv[1:]
    )


def _parser() -> argparse.ArgumentParser:
    """构造 generate、process 和 validate 子命令的参数解析器。"""

    parser = argparse.ArgumentParser(prog="twinguide")
    commands = parser.add_subparsers(dest="command", required=True)
    generate_command = commands.add_parser("generate", help="构建牙科导板并输出诊断图")
    generate_command.add_argument("--config", required=True, type=Path)
    generate_command.add_argument(
        "--output",
        type=Path,
        help="覆盖默认的 output/<case_id> 运行输出目录",
    )
    generate_command.add_argument(
        "--validate",
        action="store_true",
        help="生成后立即运行同一配置的最终 STL 验证",
    )
    generate_command.add_argument(
        "--force",
        action="store_true",
        help="忽略计算缓存并完整重建",
    )
    generate_command.add_argument(
        "--allow-unreviewed",
        action="store_true",
        help="允许生成 case.yaml 中仍明确标记为待审核的病例",
    )
    process_command = commands.add_parser("process", help="运行已实现的生成阶段")
    process_command.add_argument("--config", required=True, type=Path)
    process_command.add_argument(
        "--force",
        action="store_true",
        help="忽略计算缓存并完整重建",
    )
    validate_command = commands.add_parser("validate", help="检查已导出的牙科导板 STL")
    validate_command.add_argument("--config", required=True, type=Path)
    validate_command.add_argument("--model", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """执行用户选择的构建、阶段运行或检查命令。

    参数:
        argv: 待解析的 TwinGuide 参数；省略时从当前进程读取。

    返回:
        命令成功时返回 ``None``；参数或验证失败时以非零状态退出。
    """

    arguments = _parser().parse_args(
        _command_arguments() if argv is None else list(argv)
    )
    config = CaseConfig.from_yaml(arguments.config)
    if arguments.command == "generate":
        from twin_guide.guide_generation import generate_guide

        if arguments.output is not None:
            config = replace(config, output_directory=arguments.output.resolve())
        if not arguments.allow_unreviewed:
            require_production_review(config)
        artifacts = generate_guide(config, force_rebuild=arguments.force)
        print(f"MODEL {artifacts.model_path}")
        for image_path in artifacts.image_paths:
            print(f"IMAGE {image_path}")
        if arguments.validate:
            from twin_guide.guide_validation import validate_guide

            results = validate_guide(artifacts.model_path, config)
            for result in results:
                status = "通过" if result.passed else "失败"
                print(f"{status} {result.name} {result.metrics}")
            if not all(result.passed for result in results):
                raise SystemExit(1)
        return
    if arguments.command == "process":
        from twin_guide import run_generation_process

        result = run_generation_process(config, force_rebuild=arguments.force)
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

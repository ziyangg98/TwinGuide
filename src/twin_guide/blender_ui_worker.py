"""TwinGuide 图形编辑器使用的独立 Blender 后台工作进程。"""

from __future__ import annotations

import argparse
import shutil
import sys
import traceback
from dataclasses import replace
from pathlib import Path

from twin_guide.config import CaseConfig, EditorOverrides
from twin_guide.models import BuildArtifacts, ValidationResult
from twin_guide.ui_jobs import promote_candidate, read_manifest, write_manifest


def _seed_ui_cache(formal_directory: Path, output_directory: Path) -> None:
    """把正式输出或另一 UI 任务已有的阶段缓存补入当前任务。"""

    destination = output_directory / ".cache"
    sources = (
        formal_directory / ".cache",
        formal_directory / "ui-plan" / ".cache",
        formal_directory / "ui-preview" / ".cache",
    )
    for source in sources:
        if source == destination or not source.is_dir():
            continue
        for stage in source.iterdir():
            target = destination / stage.name
            if stage.is_dir() and not target.exists():
                destination.mkdir(parents=True, exist_ok=True)
                shutil.copytree(stage, target)


def _remove_stage_documents(output_directory: Path) -> None:
    """移除 UI 临时任务中由牙位流程顺带生成的阶段文档。"""

    for artifact in output_directory.glob("stage-*"):
        if artifact.is_file():
            artifact.unlink()


def _generate_with_process(
    config: CaseConfig,
    *,
    preview: bool = True,
) -> tuple[BuildArtifacts, object]:
    """运行一次实体生成并保留同一次规划上下文。"""

    from twin_guide.guide_generation import _generate_guide_with_process

    return _generate_guide_with_process(config, preview=preview)


def _validate(
    model_path: Path,
    config: CaseConfig,
) -> tuple[ValidationResult, ...]:
    """延迟加载并运行现有完整几何检验。"""

    from twin_guide.guide_validation import validate_guide

    return validate_guide(model_path, config)


def run_job(
    mode: str,
    config_path: Path,
    output_directory: Path,
    manifest_path: Path,
    revision: int = 0,
) -> None:
    """运行不检验的预览，或运行候选生成、检验和正式提升。"""

    config = CaseConfig.from_yaml(config_path)
    formal_directory = config.output_directory
    if mode in {"plan", "preview"}:
        _seed_ui_cache(formal_directory, output_directory)
    job_config = replace(config, output_directory=output_directory.resolve())
    from twin_guide.editor_plan import editor_geometry_fingerprint

    geometry_fingerprint = editor_geometry_fingerprint(job_config, config_path)
    write_manifest(
        manifest_path,
        {
            "status": "running",
            "mode": mode,
            "revision": revision,
            "geometry_fingerprint": geometry_fingerprint,
        },
    )
    if mode == "plan":
        from twin_guide.editor_plan import write_editor_plan
        from twin_guide.generation_process import run_generation_process

        baseline_config = replace(job_config, editor_overrides=EditorOverrides())
        process = run_generation_process(
            baseline_config,
            require_observation_qa=False,
            write_stage_documents=False,
            include_clearance_adjustment=False,
            include_observation_window_geometry=False,
        )
        plan_path = write_editor_plan(
            process.context,
            config_path,
            output_directory,
            revision=revision,
        )
        _remove_stage_documents(output_directory)
        write_manifest(
            manifest_path,
            {
                "status": "completed",
                "mode": mode,
                "revision": revision,
                "plan_path": str(plan_path),
                "geometry_fingerprint": geometry_fingerprint,
            },
        )
        return
    if mode == "preview":
        from twin_guide.editor_plan import write_editor_plan

        artifacts, process = _generate_with_process(job_config)
        snapshot_path = write_editor_plan(
            process.context,
            config_path,
            output_directory,
            revision=revision,
            snapshot=True,
        )
        _remove_stage_documents(output_directory)
        write_manifest(
            manifest_path,
            {
                "status": "completed",
                "mode": mode,
                "model_path": str(artifacts.model_path),
                "validation": "not_run",
                "revision": revision,
                "geometry_fingerprint": geometry_fingerprint,
                "editor_snapshot_path": str(snapshot_path),
            },
        )
        return
    if mode != "final":
        raise ValueError(f"不支持的 UI 后台任务：{mode}")
    from twin_guide.editor_plan import write_editor_plan

    artifacts, process = _generate_with_process(job_config, preview=False)
    snapshot_path = write_editor_plan(
        process.context,
        config_path,
        output_directory,
        revision=revision,
        snapshot=True,
    )
    write_manifest(
        manifest_path,
        {
            "status": "validating",
            "mode": mode,
            "revision": revision,
            "geometry_fingerprint": geometry_fingerprint,
        },
    )
    results = _validate(artifacts.model_path, job_config)
    result_values = [
        {"name": item.name, "passed": item.passed, "metrics": item.metrics}
        for item in results
    ]
    passed = all(item.passed for item in results)
    promoted = ()
    if passed:
        current = read_manifest(manifest_path) or {}
        if current.get("status") == "cancel_requested":
            write_manifest(
                manifest_path,
                {
                    "status": "cancelled",
                    "mode": mode,
                    "validation": "passed",
                    "validation_results": result_values,
                    "revision": revision,
                    "geometry_fingerprint": geometry_fingerprint,
                },
            )
            return
        write_manifest(
            manifest_path,
            {
                "status": "promoting",
                "mode": mode,
                "revision": revision,
                "geometry_fingerprint": geometry_fingerprint,
            },
        )
        promoted = promote_candidate(output_directory, formal_directory)
    write_manifest(
        manifest_path,
        {
            "status": "completed" if passed else "validation_failed",
            "mode": mode,
            "model_path": str(artifacts.model_path),
            "validation": "passed" if passed else "failed",
            "validation_results": result_values,
            "promoted_paths": [str(path) for path in promoted],
            "revision": revision,
            "geometry_fingerprint": geometry_fingerprint,
            "editor_snapshot_path": str(snapshot_path),
        },
    )


def launch_from_argv() -> None:
    """解析 Blender 参数并报告后台任务失败详情。"""
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(prog="twinguide-ui-worker")
    parser.add_argument("--mode", choices=("plan", "preview", "final"), required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--revision", type=int, default=0)
    parsed = parser.parse_args(arguments)
    try:
        run_job(
            parsed.mode,
            parsed.config,
            parsed.output,
            parsed.manifest,
            parsed.revision,
        )
    except Exception as error:
        write_manifest(
            parsed.manifest,
            {
                "status": "failed",
                "mode": parsed.mode,
                "revision": parsed.revision,
                "error": str(error),
                "traceback": traceback.format_exc(),
            },
        )
        raise


__all__ = ["launch_from_argv", "run_job"]

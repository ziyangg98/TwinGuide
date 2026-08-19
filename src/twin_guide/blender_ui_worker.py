"""TwinGuide 图形编辑器使用的独立 Blender 后台工作进程。"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import traceback
from dataclasses import replace
from pathlib import Path
from time import perf_counter

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
    changed_feature_ids: tuple[str, ...] = (),
) -> tuple[BuildArtifacts, object]:
    """运行一次实体生成并保留同一次规划上下文。"""

    from twin_guide.guide_generation import _generate_guide_with_process

    return _generate_guide_with_process(
        config,
        preview=preview,
        changed_feature_ids=changed_feature_ids,
    )


def _validate(
    model_path: Path,
    config: CaseConfig,
) -> tuple[ValidationResult, ...]:
    """延迟加载并运行现有完整几何检验。"""

    from twin_guide.guide_validation import validate_guide

    return validate_guide(model_path, config)


def _process_timings(process: object) -> dict[str, float]:
    """返回七阶段耗时；测试替身和旧调用结果没有该字段时为空。"""

    return dict(getattr(process, "timings_seconds", {}))


def _handpiece_cache_hits(process: object) -> list[str]:
    """返回本次规划直接复用的手机包络编号。"""

    plans = getattr(getattr(process, "context", None), "clearance_adjustment", ())
    return [plan.avoidance_id for plan in plans or () if plan.cache_reused]


def _planning_cache_hits(process: object) -> list[str]:
    """返回本次按编辑依赖直接复用的规划阶段。"""

    return list(getattr(process, "cache_hits", ()))


def _entity_cache_hits(output_directory: Path) -> dict[str, bool]:
    """读取本次实体构建写出的检查点命中记录。"""

    path = output_directory / ".cache" / "entity-preview" / "last-build.json"
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return dict(value.get("cache_hits", {}))


def run_job(
    mode: str,
    config_path: Path,
    output_directory: Path,
    manifest_path: Path,
    revision: int = 0,
    job_id: str = "",
    changed_feature_ids: tuple[str, ...] = (),
    formal_output_directory: Path | None = None,
) -> None:
    """运行不检验的预览，或运行候选生成、检验和正式提升。"""

    config = CaseConfig.from_yaml(config_path)
    if formal_output_directory is not None:
        config = replace(config, output_directory=formal_output_directory.resolve())
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
            "detail": (
                "正在分析病例并识别牙位，首次运行可能需要数分钟"
                if mode == "plan"
                else (
                    "正在生成快速预览，最终融合留到确认导出"
                    if mode == "preview"
                    else "正在生成并检验最终模型"
                )
            ),
            "revision": revision,
            "job_id": job_id,
            "changed_feature_ids": list(changed_feature_ids),
            "geometry_fingerprint": geometry_fingerprint,
        },
    )
    timings: dict[str, float] = {}
    if mode == "plan":
        from twin_guide.editor_plan import write_editor_plan
        from twin_guide.generation_process import run_generation_process

        baseline_config = replace(job_config, editor_overrides=EditorOverrides())
        started = perf_counter()
        process = run_generation_process(
            baseline_config,
            require_observation_qa=False,
            write_stage_documents=False,
            include_clearance_adjustment=False,
            include_observation_window_geometry=False,
        )
        process_timings = _process_timings(process)
        timings.update(process_timings)
        timings["planning_total"] = perf_counter() - started
        started = perf_counter()
        plan_path = write_editor_plan(
            process.context,
            config_path,
            output_directory,
            revision=revision,
        )
        timings["editor_plan"] = perf_counter() - started
        _remove_stage_documents(output_directory)
        write_manifest(
            manifest_path,
            {
                "status": "completed",
                "mode": mode,
                "revision": revision,
                "job_id": job_id,
                "changed_feature_ids": list(changed_feature_ids),
                "plan_path": str(plan_path),
                "geometry_fingerprint": geometry_fingerprint,
                "timings_seconds": timings,
            },
        )
        return
    if mode == "preview":
        from twin_guide.editor_plan import write_editor_plan

        started = perf_counter()
        if changed_feature_ids:
            artifacts, process = _generate_with_process(
                job_config,
                changed_feature_ids=changed_feature_ids,
            )
        else:
            artifacts, process = _generate_with_process(job_config)
        generation_elapsed = perf_counter() - started
        process_timings = _process_timings(process)
        timings.update(process_timings)
        timings["planning_total"] = sum(process_timings.values())
        timings["entity_build"] = max(
            0.0,
            generation_elapsed - timings["planning_total"],
        )
        started = perf_counter()
        snapshot_path = write_editor_plan(
            process.context,
            config_path,
            output_directory,
            revision=revision,
            snapshot=True,
        )
        timings["editor_snapshot"] = perf_counter() - started
        _remove_stage_documents(output_directory)
        write_manifest(
            manifest_path,
            {
                "status": "completed",
                "mode": mode,
                "model_path": str(artifacts.model_path),
                "validation": "not_run",
                "revision": revision,
                "job_id": job_id,
                "changed_feature_ids": list(changed_feature_ids),
                "geometry_fingerprint": geometry_fingerprint,
                "editor_snapshot_path": str(snapshot_path),
                "timings_seconds": timings,
                "cache_hits": {
                    "planning": _planning_cache_hits(process),
                    "handpiece_avoidance": _handpiece_cache_hits(process),
                    "entities": _entity_cache_hits(output_directory),
                },
            },
        )
        return
    if mode != "final":
        raise ValueError(f"不支持的 UI 后台任务：{mode}")
    from twin_guide.editor_plan import write_editor_plan

    started = perf_counter()
    artifacts, process = _generate_with_process(job_config, preview=False)
    generation_elapsed = perf_counter() - started
    process_timings = _process_timings(process)
    timings.update(process_timings)
    timings["planning_total"] = sum(process_timings.values())
    timings["entity_build"] = max(
        0.0,
        generation_elapsed - timings["planning_total"],
    )
    started = perf_counter()
    snapshot_path = write_editor_plan(
        process.context,
        config_path,
        output_directory,
        revision=revision,
        snapshot=True,
    )
    timings["editor_snapshot"] = perf_counter() - started
    write_manifest(
        manifest_path,
        {
            "status": "validating",
            "mode": mode,
            "revision": revision,
            "job_id": job_id,
            "changed_feature_ids": list(changed_feature_ids),
            "geometry_fingerprint": geometry_fingerprint,
            "timings_seconds": timings,
        },
    )
    started = perf_counter()
    results = _validate(artifacts.model_path, job_config)
    timings["validation"] = perf_counter() - started
    result_values = [
        {"name": item.name, "passed": item.passed, "metrics": item.metrics} for item in results
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
                    "job_id": job_id,
                    "geometry_fingerprint": geometry_fingerprint,
                    "timings_seconds": timings,
                },
            )
            return
        write_manifest(
            manifest_path,
            {
                "status": "promoting",
                "mode": mode,
                "revision": revision,
                "job_id": job_id,
                "geometry_fingerprint": geometry_fingerprint,
                "timings_seconds": timings,
            },
        )
        started = perf_counter()
        promoted = promote_candidate(output_directory, formal_directory)
        timings["promotion"] = perf_counter() - started
        from twin_guide.guide_generation import _write_formal_artifacts_cache

        formal_artifacts = BuildArtifacts(
            formal_directory / artifacts.model_path.name,
            tuple(formal_directory / path.name for path in artifacts.image_paths),
        )
        _write_formal_artifacts_cache(config, formal_artifacts)
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
            "job_id": job_id,
            "changed_feature_ids": list(changed_feature_ids),
            "geometry_fingerprint": geometry_fingerprint,
            "editor_snapshot_path": str(snapshot_path),
            "timings_seconds": timings,
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
    parser.add_argument("--formal-output", type=Path)
    parser.add_argument("--revision", type=int, default=0)
    parser.add_argument("--job-id", default="")
    parser.add_argument("--changed-feature-id", action="append", default=[])
    parsed = parser.parse_args(arguments)
    try:
        run_job(
            parsed.mode,
            parsed.config,
            parsed.output,
            parsed.manifest,
            parsed.revision,
            parsed.job_id,
            tuple(parsed.changed_feature_id),
            parsed.formal_output,
        )
    except Exception as error:
        write_manifest(
            parsed.manifest,
            {
                "status": "failed",
                "mode": parsed.mode,
                "revision": parsed.revision,
                "job_id": parsed.job_id,
                "error": str(error),
                "traceback": traceback.format_exc(),
            },
        )
        raise


def _parent_is_running(parent_pid: int) -> bool:
    """检查启动 UI 是否仍在运行。"""

    try:
        os.kill(parent_pid, 0)
    except OSError:
        return False
    return True


def serve(request_path: Path, parent_pid: int) -> None:
    """循环消费文件式任务请求，任务之间保留 Blender 进程。"""

    while _parent_is_running(parent_pid):
        request = read_manifest(request_path)
        if request is None:
            time.sleep(0.1)
            continue
        request_path.unlink(missing_ok=True)
        manifest_path = Path(str(request["manifest_path"]))
        try:
            run_job(
                str(request["mode"]),
                Path(str(request["config_path"])),
                Path(str(request["output_directory"])),
                manifest_path,
                int(request.get("revision", 0)),
                str(request.get("job_id", "")),
                tuple(str(item) for item in request.get("changed_feature_ids", [])),
                Path(str(request["formal_output_directory"]))
                if request.get("formal_output_directory")
                else None,
            )
        except Exception as error:
            write_manifest(
                manifest_path,
                {
                    "status": "failed",
                    "mode": request.get("mode", ""),
                    "revision": request.get("revision", 0),
                    "job_id": request.get("job_id", ""),
                    "error": str(error),
                    "traceback": traceback.format_exc(),
                },
            )


def launch_worker_from_argv() -> None:
    """启动病例专用常驻 worker。"""

    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(prog="twinguide-ui-worker-server")
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--parent-pid", type=int, required=True)
    parsed = parser.parse_args(arguments)
    serve(parsed.request, parsed.parent_pid)


__all__ = ["launch_from_argv", "launch_worker_from_argv", "run_job", "serve"]

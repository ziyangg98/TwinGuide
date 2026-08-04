"""Blender 编辑器后台生成任务及安全产物提升。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from twin_guide.config import CaseConfig

_WORKER_PROCESS: subprocess.Popen[str] | None = None
_WORKER_REQUEST_PATH: Path | None = None


def write_manifest(path: Path, value: dict[str, object]) -> None:
    """原子写入供前台轮询的任务状态。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as stream:
        temporary = Path(stream.name)
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def read_manifest(path: Path) -> dict[str, object] | None:
    """读取状态；后台刚好替换文件时返回下一轮再读。"""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def promote_candidate(candidate: Path, destination: Path) -> tuple[Path, ...]:
    """整批提升已验证候选；任一替换失败时恢复原有正式文件。"""

    destination.mkdir(parents=True, exist_ok=True)
    transaction = uuid.uuid4().hex
    entries: list[tuple[Path, Path, Path]] = []
    sources = sorted(
        candidate.iterdir(),
        key=lambda path: (path.name == "twin_guide.stl", path.name),
    )
    for source in sources:
        if source.is_file() and source.name != "ui-task.json":
            target = destination / source.name
            staged = destination / f".{source.name}.{transaction}.staged"
            shutil.copy2(source, staged)
            backup = destination / f".{source.name}.{transaction}.backup"
            entries.append((target, staged, backup))
    touched: list[tuple[Path, Path, bool]] = []
    try:
        for target, staged, backup in entries:
            if target.name == "twin_guide.stl":
                os.replace(staged, target)
                continue
            had_target = target.exists()
            touched.append((target, backup, had_target))
            if had_target:
                os.replace(target, backup)
            os.replace(staged, target)
    except OSError:
        for target, backup, had_target in reversed(touched):
            if had_target and backup.exists():
                os.replace(backup, target)
            elif not had_target:
                target.unlink(missing_ok=True)
        raise
    finally:
        for _target, staged, _backup in entries:
            staged.unlink(missing_ok=True)
    for _target, _staged, backup in entries:
        backup.unlink(missing_ok=True)
    return tuple(target for target, _staged, _backup in entries)


@dataclass(slots=True)
class BackgroundJob:
    """Blender 主界面持有的单个可取消子进程。"""

    mode: str
    process: subprocess.Popen[str]
    manifest_path: Path
    revision: int = 0

    def cancel(self) -> bool:
        """取消生成；正式文件提升开始后不再中断进程。"""

        manifest = read_manifest(self.manifest_path) or {}
        status = str(manifest.get("status", ""))
        if self.mode == "final" and status == "promoting":
            return False
        if self.mode == "final" and status in {"validating", "cancel_requested"}:
            write_manifest(
                self.manifest_path,
                {
                    **manifest,
                    "status": "cancel_requested",
                    "mode": self.mode,
                    "revision": self.revision,
                },
            )
            return True
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        write_manifest(
            self.manifest_path,
            {
                **manifest,
                "status": "cancelled",
                "mode": self.mode,
                "revision": self.revision,
            },
        )
        return True


def start_background_job(
    *,
    blender_binary: Path,
    mode: str,
    config_path: Path,
    output_directory: Path,
    manifest_path: Path,
    revision: int = 0,
    changed_feature_ids: tuple[str, ...] = (),
) -> BackgroundJob:
    """把任务交给病例专用的常驻 Blender worker。"""

    global _WORKER_PROCESS, _WORKER_REQUEST_PATH
    expression = (
        "from twin_guide.blender_ui_worker import launch_worker_from_argv; "
        "launch_worker_from_argv()"
    )
    worker_root = (
        CaseConfig.from_yaml(config_path).output_directory / ".cache" / "ui-worker"
    )
    request_path = worker_root / "request.json"
    if (
        _WORKER_PROCESS is None
        or _WORKER_PROCESS.poll() is not None
        or request_path != _WORKER_REQUEST_PATH
    ):
        worker_root.mkdir(parents=True, exist_ok=True)
        request_path.unlink(missing_ok=True)
        command = [
            str(blender_binary),
            "--background",
            "--factory-startup",
            "--python-use-system-env",
            "--python-expr",
            expression,
            "--",
            "--request",
            str(request_path),
            "--parent-pid",
            str(os.getpid()),
        ]
        _WORKER_PROCESS = subprocess.Popen(
            command,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _WORKER_REQUEST_PATH = request_path
    job_id = uuid.uuid4().hex
    write_manifest(
        manifest_path,
        {
            "status": "starting",
            "mode": mode,
            "revision": revision,
            "job_id": job_id,
            "changed_feature_ids": list(changed_feature_ids),
        },
    )
    write_manifest(
        request_path,
        {
            "job_id": job_id,
            "mode": mode,
            "config_path": str(config_path),
            "output_directory": str(output_directory),
            "manifest_path": str(manifest_path),
            "revision": revision,
            "changed_feature_ids": list(changed_feature_ids),
        },
    )
    return BackgroundJob(mode, _WORKER_PROCESS, manifest_path, revision)


def preview_directory(config: CaseConfig) -> Path:
    """返回不会覆盖正式模型的实体预览目录。"""
    return config.output_directory / "ui-preview"


def candidate_directory(config: CaseConfig) -> Path:
    """返回本次最终检验的唯一候选目录。"""
    return config.output_directory / "ui-candidates" / uuid.uuid4().hex


def plan_directory(config: CaseConfig) -> Path:
    """返回编辑结构规划的固定缓存目录。"""

    return config.output_directory / "ui-plan"


__all__ = [
    "BackgroundJob",
    "candidate_directory",
    "plan_directory",
    "preview_directory",
    "promote_candidate",
    "read_manifest",
    "start_background_job",
    "write_manifest",
]

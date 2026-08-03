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
    """逐文件原子提升已验证候选，并保留任何未被候选替换的缓存。"""

    destination.mkdir(parents=True, exist_ok=True)
    promoted = []
    for source in sorted(candidate.iterdir()):
        if not source.is_file() or source.name == "ui-task.json":
            continue
        target = destination / source.name
        temporary = destination / f".{source.name}.{uuid.uuid4().hex}.promoting"
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
        promoted.append(target)
    return tuple(promoted)


@dataclass(slots=True)
class BackgroundJob:
    """Blender 主界面持有的单个可取消子进程。"""

    mode: str
    process: subprocess.Popen[str]
    manifest_path: Path
    revision: int = 0

    def cancel(self) -> None:
        """终止后台进程并写入可见的取消状态。"""
        if self.process.poll() is None:
            self.process.terminate()
        write_manifest(
            self.manifest_path,
            {
                "status": "cancelled",
                "mode": self.mode,
                "revision": self.revision,
            },
        )


def start_background_job(
    *,
    blender_binary: Path,
    mode: str,
    config_path: Path,
    output_directory: Path,
    manifest_path: Path,
    revision: int = 0,
) -> BackgroundJob:
    """在独立 Blender 中启动实体生成，主界面不承担布尔运算。"""

    expression = (
        "from twin_guide.blender_ui_worker import launch_from_argv; "
        "launch_from_argv()"
    )
    command = [
        str(blender_binary),
        "--background",
        "--factory-startup",
        "--python-use-system-env",
        "--python-expr",
        expression,
        "--",
        "--mode",
        mode,
        "--config",
        str(config_path),
        "--output",
        str(output_directory),
        "--manifest",
        str(manifest_path),
        "--revision",
        str(revision),
    ]
    write_manifest(
        manifest_path,
        {"status": "starting", "mode": mode, "revision": revision},
    )
    process = subprocess.Popen(
        command,
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return BackgroundJob(mode, process, manifest_path, revision)


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

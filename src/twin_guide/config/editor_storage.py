"""保持病例原文的图形编辑器覆盖值写回。"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import fields, is_dataclass
from pathlib import Path

import yaml

from twin_guide.config.types import EditorOverrides
from twin_guide.errors import ConfigurationError

BEGIN_MARKER = "# BEGIN TWIN_GUIDE_EDITOR_OVERRIDES"
END_MARKER = "# END TWIN_GUIDE_EDITOR_OVERRIDES"
_TOP_LEVEL_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*:\s*(?:#.*)?$")


def _plain_yaml_value(value: object) -> object:
    """把微调数据类型转成 safe_dump 可直接写入的纯 YAML 值。"""

    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _plain_yaml_value(getattr(value, field.name)) for field in fields(value)
        }
    if isinstance(value, tuple | list):
        return [_plain_yaml_value(item) for item in value]
    return value


def editor_overrides_data(overrides: EditorOverrides) -> dict[str, object]:
    """把类型化覆盖值转换为稳定、无 Python 标签的 YAML 数据。"""

    result: dict[str, object] = {}
    for group in (
        "sleeve_sites",
        "operation_windows",
        "observation_windows",
        "connector_avoidance",
        "surface_anchors",
    ):
        values = getattr(overrides, group)
        if values:
            result[group] = _plain_yaml_value(values)
    if overrides.press_junction_mm is not None:
        result["press_junction_mm"] = _plain_yaml_value(overrides.press_junction_mm)
    return result


def _managed_block(overrides: EditorOverrides) -> str:
    """渲染带边界标记的编辑器覆盖块。"""
    body = yaml.safe_dump(
        {"editor_overrides": editor_overrides_data(overrides)},
        allow_unicode=True,
        sort_keys=False,
    ).rstrip()
    return f"{BEGIN_MARKER}\n{body}\n{END_MARKER}\n"


def _replacement_span(text: str) -> tuple[int, int] | None:
    """定位已有覆盖块或旧式顶层配置范围。"""
    marker_start = text.find(BEGIN_MARKER)
    if marker_start >= 0:
        marker_end = text.find(END_MARKER, marker_start)
        if marker_end < 0:
            raise ConfigurationError("case.yaml 的编辑器覆盖块缺少结束标记")
        line_end = text.find("\n", marker_end)
        return marker_start, len(text) if line_end < 0 else line_end + 1
    lines = text.splitlines(keepends=True)
    offset = 0
    start = None
    for index, line in enumerate(lines):
        if line.startswith("editor_overrides:"):
            start = offset
            end = len(text)
            inner_offset = offset + len(line)
            for candidate in lines[index + 1 :]:
                stripped = candidate.strip()
                if stripped and not candidate[0].isspace() and _TOP_LEVEL_KEY.match(stripped):
                    end = inner_offset
                    break
                inner_offset += len(candidate)
            return start, end
        offset += len(line)
    return None


def save_editor_overrides(
    case_path: Path,
    overrides: EditorOverrides,
    *,
    create_backup: bool = True,
) -> Path:
    """原子写回编辑器覆盖块，并保持病例其余原文不变。"""

    path = case_path.resolve()
    try:
        original = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ConfigurationError(f"无法读取病例配置 {path}：{error}") from error
    block = _managed_block(overrides)
    span = _replacement_span(original)
    if span is None:
        separator = "" if not original or original.endswith("\n") else "\n"
        updated = f"{original}{separator}\n{block}"
    else:
        updated = f"{original[: span[0]]}{block}{original[span[1] :]}"
    backup_path = path.with_suffix(path.suffix + ".bak")
    try:
        if create_backup and not backup_path.exists():
            shutil.copy2(path, backup_path)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    except OSError as error:
        if "temporary_path" in locals():
            temporary_path.unlink(missing_ok=True)
        raise ConfigurationError(f"无法原子写回病例配置 {path}：{error}") from error
    return backup_path


__all__ = ["editor_overrides_data", "save_editor_overrides"]

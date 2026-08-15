"""保持病例原文的图形编辑器覆盖值写回。"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path

import yaml

from twin_guide.config.types import EditorOverrides
from twin_guide.errors import ConfigurationError

BEGIN_MARKER = "# BEGIN TWIN_GUIDE_EDITOR_OVERRIDES"
END_MARKER = "# END TWIN_GUIDE_EDITOR_OVERRIDES"
_TOP_LEVEL_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*:\s*(?:#.*)?$")


def editor_overrides_data(overrides: EditorOverrides) -> dict[str, object]:
    """把类型化覆盖值转换为稳定、无 Python 标签的 YAML 数据。"""

    result: dict[str, object] = {}
    if overrides.sleeve_sites:
        result["sleeve_sites"] = [
            {
                "ring_index": item.ring_index,
                "height_mm": item.height_mm,
                "platform_height_mm": item.platform_height_mm,
                "closed_bore_height_mm": item.closed_bore_height_mm,
            }
            for item in overrides.sleeve_sites
        ]
    if overrides.operation_windows:
        result["operation_windows"] = [
            {
                "site_index": item.site_index,
                "tangent_margin_mm": item.tangent_margin_mm,
                "bitangent_margin_mm": item.bitangent_margin_mm,
                "front_axial_margin_mm": item.front_axial_margin_mm,
                "rear_axial_margin_mm": item.rear_axial_margin_mm,
                "center_offset_mm": list(item.center_offset_mm),
            }
            for item in overrides.operation_windows
        ]
    if overrides.observation_windows:
        result["observation_windows"] = [
            {
                "window_id": item.window_id,
                "start_fdi": item.start_fdi,
                "end_fdi": item.end_fdi,
                "axis_drop_mm": item.axis_drop_mm,
                "height_mm": item.height_mm,
                "sweep_angle_degrees": item.sweep_angle_degrees,
            }
            for item in overrides.observation_windows
        ]
    if overrides.connector_avoidance:
        result["connector_avoidance"] = [
            {
                "guide_index": item.guide_index,
                "path_fraction": item.path_fraction,
                "downward_offset_mm": item.downward_offset_mm,
            }
            for item in overrides.connector_avoidance
        ]
    if overrides.surface_anchors:
        result["surface_anchors"] = [
            {
                "anchor_id": item.anchor_id,
                "surface_role": item.surface_role,
                "position_mm": list(item.position_mm),
                "normal": list(item.normal),
            }
            for item in overrides.surface_anchors
        ]
    if overrides.press_junction_mm is not None:
        result["press_junction_mm"] = list(overrides.press_junction_mm)
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

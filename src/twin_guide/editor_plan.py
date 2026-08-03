"""生成 Blender 编辑器唯一消费的语义结构计划。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from twin_guide.config import CaseConfig
from twin_guide.ui_jobs import write_manifest

if TYPE_CHECKING:
    from twin_guide.types import GenerationContext

EDITOR_PLAN_SCHEMA = "twin-guide.ui-editor-plan/2.0"
EDITOR_SNAPSHOT_SCHEMA = "twin-guide.ui-editor-snapshot/2.0"


def _json_value(value: object) -> object:
    """把规划上下文转换为不含 Blender 运行时对象的 JSON 值。"""

    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Path):
        return str(value.resolve())
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_value(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _json_value(getattr(value, field.name))
            for field in fields(value)
            if field.name not in {"guide_mesh", "mapping_report"}
        }
    raise TypeError(f"编辑计划包含不可序列化类型：{type(value).__name__}")


def _semantic_config(config: CaseConfig, *, include_overrides: bool) -> bytes:
    """返回用于结构或当前几何指纹的稳定配置表示。"""

    if is_dataclass(config):
        value = asdict(config)
        value.pop("output_directory", None)
        if not include_overrides:
            value.pop("editor_overrides", None)
        return json.dumps(
            value,
            default=str,
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    return repr(config).encode()


def _dependency_paths(config: CaseConfig) -> tuple[Path, ...]:
    """返回影响编辑结构的输入网格和牙位病例文件。"""

    inputs = getattr(config, "inputs", None)
    if inputs is None:
        return ()
    paths = [
        inputs.template,
        *inputs.guide_sleeve_assemblies,
        inputs.patient_dentition,
    ]
    tooth_inputs = getattr(config, "tooth_identification", None)
    tooth_case = None if tooth_inputs is None else tooth_inputs.case_yaml
    if tooth_case is not None:
        paths.append(tooth_case)
    return tuple(paths)


def _fingerprint(config: CaseConfig, *, include_overrides: bool) -> str:
    """计算结构或当前工作几何的依赖指纹。"""

    digest = hashlib.sha256(_semantic_config(config, include_overrides=include_overrides))
    for path in _dependency_paths(config):
        stat = path.stat()
        digest.update(str(path.resolve()).encode())
        digest.update(f"{stat.st_size}:{stat.st_mtime_ns}".encode())
    return digest.hexdigest()


def editor_plan_fingerprint(config: CaseConfig, _config_path: Path | None = None) -> str:
    """计算不受图形微调值影响的编辑结构指纹。"""

    return _fingerprint(config, include_overrides=False)


def editor_geometry_fingerprint(
    config: CaseConfig,
    _config_path: Path | None = None,
) -> str:
    """计算包含当前微调值的显示几何指纹。"""

    return _fingerprint(config, include_overrides=True)


def _operation_features(context: GenerationContext) -> list[dict[str, object]]:
    """从上下文提取带独立前后深度语义的操作窗。"""

    assert context.case is not None
    assert context.window_cutouts is not None
    result = []
    for site_index, (window, operation) in enumerate(
        zip(
            context.window_cutouts.windows,
            context.case.operation_features,
            strict=True,
        ),
        start=1,
    ):
        override = context.config.editor_overrides.operation_window_for(site_index)
        front = (
            context.config.windows.operation_front_axial_margin_mm
            if override is None
            else override.front_axial_margin_mm
        )
        rear = (
            context.config.windows.operation_rear_axial_margin_mm
            if override is None
            else override.rear_axial_margin_mm
        )
        tangent_margin = (
            context.config.windows.operation_tangent_margin_mm
            if override is None
            else override.tangent_margin_mm
        )
        bitangent_margin = (
            context.config.windows.operation_bitangent_margin_mm
            if override is None
            else override.bitangent_margin_mm
        )
        geometry = _json_value(window)
        assert isinstance(geometry, dict)
        geometry.update(
            feature_center=_json_value(operation.center),
            base_depth_mm=max(0.0, window.depth_mm - front - rear),
            base_width_mm=max(0.0, window.width_mm - 2.0 * tangent_margin),
            base_height_mm=max(0.0, window.height_mm - 2.0 * bitangent_margin),
            front_axial_margin_mm=front,
            rear_axial_margin_mm=rear,
            tangent_margin_mm=tangent_margin,
            bitangent_margin_mm=bitangent_margin,
        )
        result.append(
            {
                "id": f"operation_window:{site_index}",
                "kind": "operation_window",
                "group": "OPERATION",
                "label": f"操作窗 {site_index}",
                "geometry": geometry,
            }
        )
    return result


def _observation_features(context: GenerationContext) -> list[dict[str, object]]:
    """从上下文提取观察窗和有序有效牙弓轨迹。"""

    identification = context.tooth_identification
    if identification is None:
        return []
    raw_windows = identification.mapping_report.get("observation_windows", [])
    raw_by_id = {
        str(item.get("id", "")): item
        for item in raw_windows
        if isinstance(item, dict)
    }
    valid_teeth = [
        {
            "fdi": tooth.fdi,
            "point": _json_value(tooth.guide_top or tooth.crown_point),
            "tangent": _json_value(tooth.local_tangent),
            "outward": _json_value(tooth.local_outward),
            "arch_s_mm": tooth.arch_s_mm,
        }
        for tooth in identification.positions
        if tooth.fdi in identification.present_teeth
        and tooth.fdi not in identification.excluded_teeth
    ]
    result = []
    for mapping in identification.windows:
        geometry = dict(raw_by_id.get(mapping.window_id, {}))
        geometry.setdefault("id", mapping.window_id)
        geometry.setdefault("start_fdi", mapping.start_fdi)
        geometry.setdefault("end_fdi", mapping.end_fdi)
        geometry.setdefault("height_mm", mapping.height_mm)
        geometry["valid_teeth"] = valid_teeth
        result.append(
            {
                "id": f"observation_window:{mapping.window_id}",
                "kind": "observation_window",
                "group": "OBSERVATION",
                "label": f"观察窗 {mapping.window_id}",
                "geometry": geometry,
            }
        )
    return result


def build_editor_plan(
    context: GenerationContext,
    config_path: Path,
    *,
    revision: int = 0,
    snapshot: bool = False,
) -> dict[str, object]:
    """直接从一次规划上下文建立统一编辑结构，不读取阶段文件。"""

    config = context.config
    features: list[dict[str, object]] = []
    if context.sleeve_generation is not None:
        for sleeve in context.sleeve_generation.sleeves:
            features.append(
                {
                    "id": f"sleeve:guide_{sleeve.guide_index}",
                    "kind": "sleeve",
                    "group": "SLEEVE",
                    "label": f"导柱 {sleeve.guide_index}",
                    "geometry": _json_value(sleeve),
                }
            )
    features.extend(_observation_features(context))
    if context.case is not None and context.window_cutouts is not None:
        features.extend(_operation_features(context))
    if context.point_linking is not None:
        for link in context.point_linking.links:
            if link.sleeve_label != "upper":
                continue
            features.append(
                {
                    "id": f"connector:guide_{link.guide_index}",
                    "kind": "connector",
                    "group": "CONNECTOR",
                    "label": f"连接线 {link.guide_index}",
                    "geometry": _json_value(link),
                }
            )
    if context.press_beam_points is not None:
        plan = context.press_beam_points
        for index, anchor in enumerate(plan.guide_anchors, start=1):
            features.append(
                {
                    "id": f"press_anchor:{index}",
                    "kind": "press_anchor",
                    "group": "PRESS",
                    "label": f"支撑点 {index}",
                    "geometry": _json_value(anchor),
                }
            )
        features.append(
            {
                "id": "press_junction",
                "kind": "press_junction",
                "group": "PRESS",
                "label": "Y 型汇合点",
                "geometry": {
                    "position": _json_value(plan.junction),
                    "axis": _json_value(plan.junction_axis),
                },
            }
        )
    return {
        "schema_version": EDITOR_SNAPSHOT_SCHEMA if snapshot else EDITOR_PLAN_SCHEMA,
        "case_id": config.case_id,
        "structure_fingerprint": editor_plan_fingerprint(config, config_path),
        "geometry_fingerprint": editor_geometry_fingerprint(config, config_path),
        "revision": revision,
        "features": features,
    }


def write_editor_plan(
    context: GenerationContext,
    config_path: Path,
    output_directory: Path,
    *,
    revision: int = 0,
    snapshot: bool = False,
) -> Path:
    """原子写入统一编辑计划或同版本实体快照。"""

    name = "ui-editor-snapshot.json" if snapshot else "ui-editor-plan.json"
    path = output_directory / name
    write_manifest(
        path,
        build_editor_plan(
            context,
            config_path,
            revision=revision,
            snapshot=snapshot,
        ),
    )
    return path


def editor_snapshot_matches(
    value: dict[str, object],
    *,
    revision: int,
    geometry_fingerprint: str,
) -> bool:
    """判断实体预览快照能否与同一任务的 STL 一起加载。"""

    return bool(
        value.get("schema_version") == EDITOR_SNAPSHOT_SCHEMA
        and int(value.get("revision", -1)) == revision
        and value.get("geometry_fingerprint") == geometry_fingerprint
    )


__all__ = [
    "EDITOR_PLAN_SCHEMA",
    "EDITOR_SNAPSHOT_SCHEMA",
    "build_editor_plan",
    "editor_geometry_fingerprint",
    "editor_plan_fingerprint",
    "editor_snapshot_matches",
    "write_editor_plan",
]

"""生成 Blender 编辑器唯一消费的语义结构计划。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from twin_guide.config import CaseConfig
from twin_guide.config.loading import load_case_yaml
from twin_guide.ui_jobs import write_manifest

if TYPE_CHECKING:
    from twin_guide.types import GenerationContext

EDITOR_PLAN_SCHEMA = "twin-guide.ui-editor-plan/4.0"
EDITOR_SNAPSHOT_SCHEMA = "twin-guide.ui-editor-snapshot/4.0"


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


def _coordinate_values(value: object) -> tuple[float, float, float]:
    """读取 JSON 坐标对象或三元素数组。"""

    if isinstance(value, dict):
        try:
            return tuple(float(value[axis]) for axis in ("x", "y", "z"))
        except (KeyError, TypeError, ValueError) as error:
            raise TypeError("三维坐标对象必须包含数值 x、y、z") from error
    if isinstance(value, list) and len(value) == 3:
        try:
            return tuple(float(item) for item in value)
        except (TypeError, ValueError) as error:
            raise TypeError("三维坐标数组必须包含三个数值") from error
    raise TypeError("三维坐标必须是包含 x、y、z 的对象或三元素数组")


def _semantic_config(config: CaseConfig, *, include_overrides: bool) -> bytes:
    """返回用于结构或当前几何指纹的稳定配置表示。"""

    if is_dataclass(config):
        value = asdict(config)
        value.pop("output_directory", None)
        value["tooth_identification"] = {
            "enabled": getattr(config, "tooth_identification", None) is not None
        }
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
        inputs.patient_dentition,
    ]
    return tuple(paths)


def _case_semantics(config_path: Path | None) -> bytes:
    """读取病例 YAML 中不含审核和编辑覆盖值的稳定语义。"""

    if config_path is None:
        return b""
    value = load_case_yaml(config_path)
    if not isinstance(value, dict):
        raise ValueError("病例 YAML 根值必须为对象")
    value.pop("editor_overrides", None)
    value.pop("review", None)
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
    ).encode()


def _fingerprint(
    config: CaseConfig,
    config_path: Path | None,
    *,
    include_overrides: bool,
) -> str:
    """计算结构或当前工作几何的依赖指纹。"""

    digest = hashlib.sha256(_semantic_config(config, include_overrides=include_overrides))
    digest.update(_case_semantics(config_path))
    for path in _dependency_paths(config):
        stat = path.stat()
        digest.update(str(path.resolve()).encode())
        digest.update(f"{stat.st_size}:{stat.st_mtime_ns}".encode())
    return digest.hexdigest()


def editor_plan_fingerprint(config: CaseConfig, _config_path: Path | None = None) -> str:
    """计算不受图形微调值影响的编辑结构指纹。"""

    return _fingerprint(config, _config_path, include_overrides=False)


def editor_geometry_fingerprint(
    config: CaseConfig,
    _config_path: Path | None = None,
) -> str:
    """计算包含当前微调值的显示几何指纹。"""

    return _fingerprint(config, _config_path, include_overrides=True)


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
    raw_by_id = {str(item.get("id", "")): item for item in raw_windows if isinstance(item, dict)}
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
        sleeves = context.sleeve_generation.sleeves
        for site_index, guide_post in enumerate(config.guide_posts):
            pair = sleeves[2 * site_index : 2 * site_index + 2]
            if len(pair) != 2:
                raise ValueError("每个 planning.guide_posts 必须对应两根导柱")
            geometry = _json_value(pair[0])
            if not isinstance(geometry, dict):
                raise TypeError("导柱编辑几何必须为对象")
            parameters = geometry.get("parameters")
            if not isinstance(parameters, dict):
                raise TypeError("导柱编辑几何缺少 parameters")
            second = _json_value(pair[1])
            if not isinstance(second, dict) or not isinstance(second.get("parameters"), dict):
                raise TypeError("第二根导柱编辑几何缺少 parameters")
            first_origin = _coordinate_values(parameters["axis_origin"])
            second_origin = _coordinate_values(second["parameters"]["axis_origin"])
            parameters["axis_origin"] = [
                0.5 * (float(left) + float(right))
                for left, right in zip(first_origin, second_origin, strict=True)
            ]
            geometry["ring_index"] = guide_post.ring_index
            geometry["guide_indices"] = [item.guide_index for item in pair]
            features.append(
                {
                    "id": f"sleeve:site_{guide_post.ring_index}",
                    "kind": "sleeve",
                    "group": "SLEEVE",
                    "label": f"种植位圆环 {guide_post.ring_index} 双导柱",
                    "geometry": geometry,
                }
            )
    features.extend(_observation_features(context))
    if context.case is not None and context.window_cutouts is not None:
        features.extend(_operation_features(context))
    if context.point_linking is not None:
        for link in context.point_linking.links:
            if link.sleeve_label != "upper":
                continue
            link_geometry = _json_value(link)
            for route in link.platform_avoidance_routes:
                features.append(
                    {
                        "id": f"connector:guide_{route.guide_index}:{route.side}",
                        "kind": "connector",
                        "group": "CONNECTOR",
                        "label": (
                            f"连接线 {route.guide_index}"
                            f" {'左侧' if route.side == 'left' else '右侧'}"
                        ),
                        "geometry": {
                            **link_geometry,
                            "guide_index": route.guide_index,
                            "avoidance_route": _json_value(route),
                        },
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

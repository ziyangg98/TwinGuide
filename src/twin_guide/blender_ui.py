"""TwinGuide 的“模型—控制点—预览—最终导出”Blender 编辑器。"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
import uuid
from dataclasses import replace
from itertools import pairwise
from pathlib import Path
from typing import ClassVar

import bpy
from mathutils import Vector

from twin_guide.blender_ui_gizmos import TwinGuideFeatureGizmoGroup
from twin_guide.blender_ui_panel import (
    TWINGUIDE_UL_feature_list,
    TwinGuideFeatureItem,
    TwinGuidePanel,
    TwinGuideStructurePanel,
)
from twin_guide.blender_ui_proxies import (
    CONTROL_PREFIX,
    LABEL_PREFIX,
    OVERLAY_PREFIX,
    SURFACE_PREFIX,
    create_control,
    create_curve,
    create_fdi_label,
    create_hint_label,
)
from twin_guide.blender_ui_workspace import configure_workspace
from twin_guide.config import (
    CaseConfig,
    ConnectorAvoidanceOverride,
    EditorOverrides,
    ObservationWindowOverride,
    OperationWindowOverride,
    SleeveGuideOverride,
    SurfaceAnchorOverride,
    production_review_status,
)
from twin_guide.config.editor_storage import save_editor_overrides
from twin_guide.editor_adapters import (
    with_connector,
    with_observation_window,
    with_operation_window,
    with_press_junction,
    with_sleeve,
    with_surface_anchor,
)
from twin_guide.editor_plan import (
    EDITOR_PLAN_SCHEMA,
    EDITOR_SNAPSHOT_SCHEMA,
    editor_geometry_fingerprint,
    editor_plan_fingerprint,
    editor_snapshot_matches,
)
from twin_guide.editor_session import EditorSession, changed_feature_ids
from twin_guide.ui_jobs import (
    BackgroundJob,
    candidate_directory,
    plan_directory,
    preview_directory,
    read_manifest,
    start_background_job,
)

PREVIEW_OBJECT_NAME = "TwinGuide_Model"

_CONFIG: CaseConfig | None = None
_JOB: BackgroundJob | None = None
_JOB_CONFIG_PATH: Path | None = None
_UPDATING = False
_SESSION: EditorSession | None = None
_EDITOR_PLAN_PATH: Path | None = None
_EDITOR_PLAN_VALUE: dict[str, object] | None = None
_SELECTION_SYNC = False
_LAST_ACTIVE_NAME = ""
_FEATURE_VALUE_SYNC = False
_EDITOR_PLAN_STALE = False
_LAST_PREVIEW_OVERRIDES = EditorOverrides()


def _working_overrides() -> EditorOverrides:
    """返回当前编辑会话工作值，未初始化时使用病例值。"""

    assert _CONFIG is not None
    return (
        _CONFIG.editor_overrides
        if _SESSION is None
        else _SESSION.working_overrides
    )


def _load_editor_plan(path: Path) -> dict[str, object]:
    """读取并校验 UI 唯一消费的 v2 编辑计划或预览快照。"""

    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") not in {
        EDITOR_PLAN_SCHEMA,
        EDITOR_SNAPSHOT_SCHEMA,
    }:
        raise ValueError(f"不支持的编辑计划：{path}")
    if not isinstance(value.get("features"), list):
        raise ValueError(f"编辑计划缺少 features：{path}")
    return value


def _plan_features(kind: str) -> tuple[dict[str, object], ...]:
    """按结构类型读取统一编辑计划；UI 不再读取阶段 JSON。"""

    if _EDITOR_PLAN_VALUE is None:
        return ()
    features = _EDITOR_PLAN_VALUE.get("features", [])
    return tuple(
        item
        for item in features
        if isinstance(item, dict) and item.get("kind") == kind
    )


def _vec(value: dict[str, object] | list[object]) -> Vector:
    """把阶段 JSON 坐标转换为 Blender 向量。"""
    if isinstance(value, dict):
        return Vector((float(value["x"]), float(value["y"]), float(value["z"])))
    return Vector(tuple(float(item) for item in value))


def _project_to_polyline(
    point: Vector,
    points: list[Vector],
) -> tuple[Vector, Vector, float]:
    """把任意点投影到折线最近位置，返回点、切向和弧长。"""

    best_point = points[0]
    best_tangent = (points[1] - points[0]).normalized()
    best_distance = 0.0
    best_error = float("inf")
    travelled = 0.0
    for start, end in pairwise(points):
        segment = end - start
        length = segment.length
        if length <= 1e-9:
            continue
        tangent = segment / length
        t = min(length, max(0.0, (point - start).dot(tangent)))
        projected = start + tangent * t
        error = (point - projected).length_squared
        if error < best_error:
            best_point = projected
            best_tangent = tangent
            best_distance = travelled + t
            best_error = error
        travelled += length
    return best_point, best_tangent, best_distance


def _connector_base(object_: bpy.types.Object) -> tuple[Vector, Vector, float]:
    """返回生成器语义中的接触点—选定侧端点基点。"""

    start = Vector(object_["tg_route_start"])
    end = Vector(object_["tg_route_end"])
    segment = end - start
    length = max(1e-9, segment.length)
    distance = min(length, max(0.0, float(object_.get("tg_path_distance", 0.0))))
    tangent = segment.normalized()
    base = start + tangent * distance
    return base, tangent, length


def _control(
    name: str,
    location: Vector,
    kind: str,
    **properties: object,
) -> bpy.types.Object:
    """建立可点击但不能用 Blender 普通变换的结构代理。"""

    return create_control(name, location, kind, properties)


def _curve(name: str, points: list[Vector], cyclic: bool = False) -> bpy.types.Object:
    """建立不参与布尔运算的轻量曲线预览。"""
    return create_curve(name, points, cyclic)


def _fdi_label(
    window_id: str,
    role: str,
    fdi: int,
    location: Vector,
) -> bpy.types.Object:
    """建立跟随观察窗端点的可见 FDI 标签。"""

    return create_fdi_label(window_id, role, fdi, location)


def _hint_label(
    name: str,
    text: str,
    location: Vector,
    group: str,
    feature_id: str,
    color: tuple[float, float, float, float],
) -> bpy.types.Object:
    """建立可点击的三维结构提示标签。"""

    return create_hint_label(
        name,
        text,
        location,
        group,
        feature_id,
        color,
    )


def _remove_editor_objects() -> None:
    """移除旧控制点和轻量预览对象。"""
    for object_ in tuple(bpy.data.objects):
        if object_.name.startswith((CONTROL_PREFIX, OVERLAY_PREFIX, LABEL_PREFIX)):
            bpy.data.objects.remove(object_, do_unlink=True)


def _load_reference_surfaces(*, visible: bool = False) -> None:
    """加载不可选中的吸附表面；它们不进入正式生成逻辑。"""

    assert _CONFIG is not None
    from twin_guide.blender.stl_io import import_stl_mesh

    for role, path in (
        ("template", _CONFIG.inputs.template),
        ("dentition", _CONFIG.inputs.patient_dentition),
    ):
        name = f"{SURFACE_PREFIX}{role}"
        if bpy.data.objects.get(name) is not None:
            continue
        target = import_stl_mesh(path, name)
        target.display_type = "WIRE"
        target.hide_render = True
        target.hide_select = True
        target.hide_set(not visible)


def _load_input_fallback() -> None:
    """没有正式或预览模型时显示传统模板与牙列线框。"""

    _load_reference_surfaces(visible=True)


def _snap_to_surface(object_: bpy.types.Object) -> None:
    """将锚点投影到指定导板或牙列表面。"""
    target = bpy.data.objects.get(
        f"{SURFACE_PREFIX}{object_['tg_surface_role']}"
    )
    if target is None:
        return
    local = target.matrix_world.inverted() @ object_.location
    hit, location, normal, _index = target.closest_point_on_mesh(local)
    if not hit:
        object_["tg_resnap_required"] = True
        object_.color = (1.0, 0.1, 0.1, 1.0)
        return
    world_location = target.matrix_world @ location
    distance = (world_location - object_.location).length
    object_["tg_resnap_required"] = distance > 0.75
    if distance > 0.75:
        object_.color = (1.0, 0.1, 0.1, 1.0)
        return
    object_.location = world_location
    object_.color = (0.25, 0.9, 0.35, 1.0)
    world_normal = (target.matrix_world.to_3x3() @ normal).normalized()
    object_["tg_normal"] = list(world_normal)
    object_.rotation_quaternion = world_normal.to_track_quat("Z", "Y")
    anchor_index = str(object_["tg_anchor_id"]).rsplit("_", 1)[-1]
    _move_hint_label(f"PressAnchor_{anchor_index}", object_.location + world_normal * 1.4)
    _update_press_overlay()


def _surface_point(role: str, point: Vector) -> Vector:
    """返回指定参考表面上离目标最近的位置。"""

    target = bpy.data.objects.get(f"{SURFACE_PREFIX}{role}")
    if target is None:
        return point
    inverse = target.matrix_world.inverted()
    hit, location, _normal, _index = target.closest_point_on_mesh(inverse @ point)
    return target.matrix_world @ location if hit else point


def _create_sleeve_controls() -> None:
    """为每根导柱建立三项独立高度手柄。"""
    for feature in _plan_features("sleeve"):
        raw = feature.get("geometry")
        if not isinstance(raw, dict):
            continue
        index = int(raw["guide_index"])
        parameters = raw.get("parameters")
        if not isinstance(parameters, dict):
            continue
        origin = _vec(parameters["axis_origin"])
        axis = _vec(parameters["axis"]).normalized()
        override = _working_overrides().sleeve_for(index)
        values = {
            "closed": float(parameters["closed_bore_height"]),
            "platform": float(parameters["platform_height"]),
            "total": float(parameters["height"]),
        }
        if override is not None:
            values.update(
                closed=override.closed_bore_height_mm,
                platform=override.platform_height_mm,
                total=override.height_mm,
            )
        for role, value in values.items():
            _control(
                f"Sleeve_{index}_{role}",
                origin + axis * value,
                "sleeve_height",
                guide_index=index,
                role=role,
                origin=list(origin),
                axis=list(axis),
            )
        _hint_label(
            f"Sleeve_{index}",
            f"导柱 {index}",
            origin + axis * values["platform"] + Vector((0.0, 0.0, 1.8)),
            "SLEEVE",
            f"sleeve:guide_{index}",
            (0.1, 0.65, 1.0, 1.0),
        )


def _create_operation_controls() -> None:
    """为每个操作窗建立局部坐标手柄。"""
    for feature in _plan_features("operation_window"):
        raw = feature.get("geometry")
        if not isinstance(raw, dict):
            continue
        site_index = int(str(feature["id"]).rsplit(":", 1)[-1])
        feature_center = _vec(raw["feature_center"])
        normal = _vec(raw["normal"]).normalized()
        tangent = _vec(raw["tangent"]).normalized()
        bitangent = normal.cross(tangent).normalized()
        override = _working_overrides().operation_window_for(site_index)
        working_tangent = (
            float(raw["tangent_margin_mm"])
            if override is None
            else override.tangent_margin_mm
        )
        working_bitangent = (
            float(raw["bitangent_margin_mm"])
            if override is None
            else override.bitangent_margin_mm
        )
        width = float(raw["base_width_mm"]) + 2.0 * working_tangent
        height = float(raw["base_height_mm"]) + 2.0 * working_bitangent
        working_offset = (
            Vector((0.0, 0.0, 0.0))
            if override is None
            else Vector(override.center_offset_mm)
        )
        front_margin = (
            float(raw["front_axial_margin_mm"])
            if override is None
            else override.front_axial_margin_mm
        )
        rear_margin = (
            float(raw["rear_axial_margin_mm"])
            if override is None
            else override.rear_axial_margin_mm
        )
        base_depth = float(raw["base_depth_mm"])
        cutter_front_center = (
            feature_center + normal * (base_depth / 2.0 + front_margin)
        )
        visible_base_center = _surface_point("template", cutter_front_center)
        local_x = working_offset.dot(tangent)
        local_y = working_offset.dot(bitangent)
        fixed_offset = working_offset - tangent * local_x - bitangent * local_y
        visible_center = _surface_point(
            "template",
            visible_base_center + tangent * local_x + bitangent * local_y,
        )
        common = {
            "site_index": site_index,
            "center": list(visible_base_center),
            "normal": list(normal),
            "tangent": list(tangent),
            "bitangent": list(bitangent),
            "base_width": width,
            "base_height": height,
            "base_width_without_margin": float(raw["base_width_mm"]),
            "base_height_without_margin": float(raw["base_height_mm"]),
            "corner_radius": float(raw["corner_radius_mm"]),
            "tangent_margin_base": working_tangent,
            "bitangent_margin_base": working_bitangent,
            "local_x": local_x,
            "local_y": local_y,
            "fixed_offset": list(fixed_offset),
        }
        _control(
            f"Window_{site_index}_center",
            visible_center,
            "window_center",
            **common,
        )
        _control(
            f"Window_{site_index}_width",
            visible_center + tangent * width / 2.0,
            "window_size",
            role="width",
            axis=list(tangent),
            origin=list(visible_center),
            value=width / 2.0,
            **common,
        )
        _control(
            f"Window_{site_index}_width_opposite",
            visible_center - tangent * width / 2.0,
            "window_size",
            role="width_opposite",
            axis=list(-tangent),
            origin=list(visible_center),
            value=width / 2.0,
            **common,
        )
        _control(
            f"Window_{site_index}_height",
            visible_center + bitangent * height / 2.0,
            "window_size",
            role="height",
            axis=list(bitangent),
            origin=list(visible_center),
            value=height / 2.0,
            **common,
        )
        _control(
            f"Window_{site_index}_height_opposite",
            visible_center - bitangent * height / 2.0,
            "window_size",
            role="height_opposite",
            axis=list(-bitangent),
            origin=list(visible_center),
            value=height / 2.0,
            **common,
        )
        lateral_gap = width / 2.0 + 1.4
        for role, sign, margin, lateral_sign in (
            ("front", 1.0, front_margin, 1.0),
            ("rear", -1.0, rear_margin, -1.0),
        ):
            visual_offset = tangent * lateral_gap * lateral_sign
            margin_origin = visible_center + visual_offset
            _control(
                f"Window_{site_index}_{role}",
                margin_origin + normal * sign * margin,
                "window_margin",
                role=role,
                axis=list(normal * sign),
                origin=list(margin_origin),
                visual_offset=list(visual_offset),
                **common,
            )
        _update_window_overlay(site_index)
        _hint_label(
            f"Window_{site_index}",
            f"操作窗 {site_index}",
            visible_center + normal * 2.0,
            "OPERATION",
            f"operation_window:{site_index}",
            (0.15, 0.9, 0.45, 1.0),
        )


def _upper_link_by_guide() -> dict[int, dict[str, object]]:
    """按导柱编号取得统一编辑计划中的高位连接线。"""

    result = {}
    for feature in _plan_features("connector"):
        geometry = feature.get("geometry")
        if isinstance(geometry, dict):
            result[int(geometry["guide_index"])] = geometry
    return result


def _create_connector_controls() -> None:
    """为每根导柱建立独立正视避让节点。"""
    assert _CONFIG is not None
    for index, raw in _upper_link_by_guide().items():
        raw_centerline = raw.get("centerline")
        centerline = (
            [_vec(point) for point in raw_centerline]
            if isinstance(raw_centerline, list) and len(raw_centerline) >= 2
            else [_vec(raw["start"]), _vec(raw["end"])]
        )
        start = centerline[0]
        end = centerline[-1]
        route_start = _vec(raw["tube_contact"])
        route_end_value = raw.get("avoidance_route_endpoint")
        route_end = end if route_end_value is None else _vec(route_end_value)
        route = route_end - route_start
        path_length = max(1e-9, route.length)
        tangent = route.normalized()
        down_value = raw.get("avoidance_direction")
        local_down = (
            Vector((0.0, 0.0, 1.0 if _CONFIG.jaw.value == "upper" else -1.0))
            if down_value is None
            else _vec(down_value)
        ).normalized()
        override = _working_overrides().connector_for(index)
        fraction = 0.35 if override is None else override.path_fraction
        offset = (
            _CONFIG.geometry.sleeve_stop_front_avoidance_mm
            if override is None
            else override.downward_offset_mm
        )
        distance = path_length * fraction
        base = route_start + tangent * distance
        local_down = (local_down - tangent * local_down.dot(tangent)).normalized()
        location = base + local_down * offset
        _control(
            f"Connector_{index}",
            location,
            "connector_node",
            guide_index=index,
            start=list(start),
            end=list(end),
            route_start=list(route_start),
            route_end=list(route_end),
            route_side=str(raw.get("avoidance_route_side", "right")),
            tangent=list(tangent),
            down=list(local_down),
            path_distance=distance,
        )
        _update_connector_overlay(index)
        _hint_label(
            f"Connector_{index}",
            f"连接线 {index}",
            location + local_down * 1.4,
            "CONNECTOR",
            f"connector:guide_{index}",
            (1.0, 0.68, 0.05, 1.0),
        )


def _create_press_controls() -> None:
    """建立按压锚点和工作平面汇合点。"""
    anchor_features = _plan_features("press_anchor")
    for feature in anchor_features:
        raw = feature.get("geometry")
        if isinstance(raw, dict):
            index = int(str(feature["id"]).rsplit(":", 1)[-1])
            surface_anchor = _vec(raw["surface_anchor"])
            centerline_anchor = _vec(raw["centerline_anchor"])
            override = _working_overrides().surface_anchor_for(
                f"press_anchor_{index}"
            )
            normal = (
                _vec(raw["surface_normal"]).normalized()
                if override is None
                else Vector(override.normal).normalized()
            )
            control = _control(
                f"PressAnchor_{index}",
                (
                    _vec(raw["surface_anchor"])
                    if override is None
                    else Vector(override.position_mm)
                ),
                "surface_anchor",
                anchor_id=f"press_anchor_{index}",
                surface_role=(
                    "template" if override is None else override.surface_role
                ),
                normal=list(normal),
                centerline_depth=(centerline_anchor - surface_anchor).length,
            )
            _snap_to_surface(control)
            _hint_label(
                f"PressAnchor_{index}",
                f"支撑点 {index}",
                control.location + normal * 1.4,
                "PRESS",
                f"press_anchor:{index}",
                (0.25, 0.9, 0.35, 1.0),
            )
    junction_features = _plan_features("press_junction")
    if junction_features:
        geometry = junction_features[0].get("geometry")
        junction = None if not isinstance(geometry, dict) else geometry.get("position")
    else:
        junction = None
    if isinstance(junction, dict | list):
        assert _CONFIG is not None
        raw_axis = None if not isinstance(geometry, dict) else geometry.get("axis")
        axis = (
            Vector((0.0, 0.0, -1.0 if _CONFIG.jaw.value == "upper" else 1.0))
            if raw_axis is None
            else _vec(raw_axis).normalized()
        )
        position = (
            _vec(junction)
            if _working_overrides().press_junction_mm is None
            else Vector(_working_overrides().press_junction_mm)
        )
        _control(
            "PressJunction",
            position,
            "junction",
            plane_origin=list(_vec(junction)),
            plane_normal=list(axis),
        )
        _hint_label(
            "PressJunction",
            "Y 型汇合点",
            position + Vector((0.0, 0.0, 1.4)),
            "PRESS",
            "press_junction",
            (0.15, 0.8, 0.95, 1.0),
        )
    _update_press_overlay()


def _create_observation_controls() -> None:
    """建立可吸附 FDI 的观察窗手柄。"""
    for feature in _plan_features("observation_window"):
        raw = feature.get("geometry")
        if not isinstance(raw, dict):
            continue
        candidates = raw.get("valid_teeth", [])
        if not isinstance(candidates, list) or not candidates:
            continue
        encoded = json.dumps(candidates)
        definition = raw.get("axis_sweep")
        if not isinstance(definition, dict):
            continue
        window_id = str(raw["id"])
        start = _vec(definition["axis_start_global_mm"])
        end = _vec(definition["axis_end_global_mm"])
        override = _working_overrides().observation_window_for(window_id)
        start_fdi = int(raw["start_fdi"] if override is None else override.start_fdi)
        end_fdi = int(raw["end_fdi"] if override is None else override.end_fdi)
        drop = float(definition["axis_drop_mm"] if override is None else override.axis_drop_mm)
        height = float(raw["height_mm"] if override is None else override.height_mm)
        angle = float(definition["sweep_angle_deg"] if override is None else override.sweep_angle_degrees)
        occlusal = _vec(definition["zero_degree_occlusal_direction_global"]).normalized()
        exterior = _vec(definition["positive_90_degree_exterior_direction_global"]).normalized()
        start_candidate = next(
            (item for item in candidates if item["fdi"] == start_fdi),
            None,
        )
        end_candidate = next(
            (item for item in candidates if item["fdi"] == end_fdi),
            None,
        )
        if start_candidate is not None and end_candidate is not None:
            start = _vec(start_candidate["point"]) - occlusal * drop
            end = _vec(end_candidate["point"]) - occlusal * drop
        midpoint = (start + end) / 2.0
        _control(
            f"Observation_{window_id}_start",
            start,
            "observation_endpoint",
            window_id=window_id,
            role="start",
            fdi=start_fdi,
            candidates=encoded,
            axis_origin=list(start),
        )
        _fdi_label(window_id, "start", start_fdi, start)
        _control(
            f"Observation_{window_id}_drop",
            midpoint,
            "observation_scalar",
            window_id=window_id,
            role="drop",
            origin=list(midpoint + occlusal * drop),
            axis=list(-occlusal),
        )
        _control(
            f"Observation_{window_id}_height",
            midpoint + exterior * height,
            "observation_scalar",
            window_id=window_id,
            role="height",
            origin=list(midpoint),
            axis=list(exterior),
        )
        _control(
            f"Observation_{window_id}_sweep",
            midpoint + exterior * (angle / 18.0),
            "observation_scalar",
            window_id=window_id,
            role="sweep",
            origin=list(midpoint),
            axis=list(exterior),
            scale=18.0,
        )
        _update_curve(f"Observation_{window_id}", [start, end])
        _control(
            f"Observation_{window_id}_end",
            end,
            "observation_endpoint",
            window_id=window_id,
            role="end",
            fdi=end_fdi,
            candidates=encoded,
            axis_origin=list(end),
        )
        _fdi_label(window_id, "end", end_fdi, end)
        _hint_label(
            f"Observation_{window_id}",
            _observation_display_name(window_id),
            midpoint + Vector((0.0, 0.0, 1.6)),
            "OBSERVATION",
            f"observation_window:{window_id}",
            (0.7, 0.3, 1.0, 1.0),
        )


def _create_controls() -> None:
    """从统一编辑计划重建全部图形控制点。"""
    _remove_editor_objects()
    _create_sleeve_controls()
    _create_operation_controls()
    _create_connector_controls()
    _create_press_controls()
    _create_observation_controls()
    _populate_feature_list()


def _find_control(kind: str, **values: object) -> bpy.types.Object | None:
    """按类型和标识查找一个控制点。"""
    for object_ in bpy.data.objects:
        if object_.get("tg_kind") != kind:
            continue
        if all(object_.get(f"tg_{key}") == value for key, value in values.items()):
            return object_
    return None


def _update_sleeve_hint_label(index: int) -> None:
    """让导柱入口标签跟随平台高度圆环。"""

    platform = _find_control("sleeve_height", guide_index=index, role="platform")
    if platform is None:
        return
    _move_hint_label(f"Sleeve_{index}", platform.location + Vector((0.0, 0.0, 1.8)))


def _update_curve(name: str, points: list[Vector], cyclic: bool = False) -> None:
    """用新折线替换同名轻量预览。"""
    existing = bpy.data.objects.get(f"{OVERLAY_PREFIX}{name}")
    if existing is None:
        _curve(name, points, cyclic)
        return
    spline = existing.data.splines[0]
    if len(spline.points) != len(points):
        bpy.data.objects.remove(existing, do_unlink=True)
        _curve(name, points, cyclic)
        return
    for point, value in zip(spline.points, points, strict=True):
        point.co = (*value, 1.0)
    spline.use_cyclic_u = cyclic


def _move_hint_label(name: str, location: Vector) -> None:
    """让三维提示标签跟随当前结构。"""

    label = bpy.data.objects.get(f"{LABEL_PREFIX}{name}")
    if label is not None:
        label.location = location


def _show_model_view() -> None:
    """回到模型查看，只显示各结构的概览热点。"""

    for object_ in bpy.data.objects:
        if object_.get("tg_group") is None:
            continue
        visible = bool(object_.get("tg_overview_visible"))
        object_.hide_set(not visible)
        object_.hide_select = not visible
        if object_.type == "CURVE" and object_.data.splines:
            object_.data.bevel_depth = 0.14
        if object_.get("tg_hint_label") and hasattr(object_.data, "size"):
            object_.data.size = 1.15
    if _SESSION is not None:
        _SESSION.select(None)
    model = bpy.data.objects.get(PREVIEW_OBJECT_NAME)
    if model is not None:
        for selected in bpy.context.selected_objects:
            selected.select_set(False)
        model.select_set(True)
        bpy.context.view_layer.objects.active = model


def _show_feature(feature_id: str) -> None:
    """展开当前结构手柄，同时保留其他结构的可点击概览。"""

    active = bpy.context.active_object
    selected = (
        active
        if active is not None
        and active.name.startswith(CONTROL_PREFIX)
        and active.get("tg_feature_id") == feature_id
        else None
    )
    for object_ in bpy.data.objects:
        object_feature = object_.get("tg_feature_id")
        if object_feature is None:
            continue
        current = str(object_feature) == feature_id
        visible = current or bool(object_.get("tg_overview_visible"))
        object_.hide_set(not visible)
        object_.hide_select = not visible
        if object_.type == "CURVE" and object_.data.splines:
            object_.data.bevel_depth = 0.28 if current else 0.11
        if object_.get("tg_hint_label") and hasattr(object_.data, "size"):
            object_.data.size = 1.45 if current else 1.0
        if current and object_.name.startswith(CONTROL_PREFIX) and selected is None:
            selected = object_
    if selected is not None:
        for object_ in bpy.context.selected_objects:
            object_.select_set(False)
        selected.select_set(True)
        bpy.context.view_layer.objects.active = selected
    if _SESSION is not None:
        _SESSION.select(feature_id)


def _feature_label(feature_id: str) -> tuple[str, str]:
    """返回结构列表的分组和中文名称。"""

    if feature_id.startswith("operation_window:"):
        return "操作窗", f"操作窗 {feature_id.rsplit(':', 1)[-1]}"
    if feature_id.startswith("observation_window:"):
        return "观察窗", _observation_display_name(feature_id.split(":", 1)[1])
    if feature_id.startswith("connector:guide_"):
        return "连接线", f"连接线 {feature_id.rsplit('_', 1)[-1]}"
    if feature_id.startswith("press_anchor:"):
        return "支撑结构", f"支撑点 {feature_id.rsplit(':', 1)[-1]}"
    if feature_id == "press_junction":
        return "支撑结构", "Y 型汇合点"
    return "导柱", f"导柱 {feature_id.rsplit('_', 1)[-1]}"


def _observation_display_name(identifier: str) -> str:
    """把内部观察窗编号转换为界面显示名称。"""

    return {
        "anterior_axis_sweep": "前方观察窗",
        "left_premolar": "左侧观察窗",
        "right_premolar": "右侧观察窗",
    }.get(identifier, f"观察窗 {identifier.replace('_', ' ')}")


def _populate_feature_list() -> None:
    """从当前代理建立按结构分组的稳定选择列表。"""

    scene = bpy.context.scene
    if not hasattr(scene, "twin_guide_features"):
        return
    scene.twin_guide_features.clear()
    identifiers = sorted(
        {
            str(object_["tg_feature_id"])
            for object_ in bpy.data.objects
            if object_.name.startswith(CONTROL_PREFIX)
        },
        key=lambda value: (_feature_label(value)[0], value),
    )
    for feature_id in identifiers:
        group, label = _feature_label(feature_id)
        item = scene.twin_guide_features.add()
        item.feature_id = feature_id
        item.group_label = group
        item.label = label


def _rebuild_working_proxies() -> None:
    """按会话工作值重建代理并保持当前结构选择。"""

    selected_feature = None if _SESSION is None else _SESSION.selected_feature_id
    _create_controls()
    if selected_feature is None:
        _show_model_view()
    else:
        _show_feature(selected_feature)
    if _SESSION is not None:
        bpy.context.scene.twin_guide_state.dirty = _SESSION.dirty


def _feature_index_updated(state: TwinGuideState, context: bpy.types.Context) -> None:
    """把结构列表选择同步到三维代理。"""

    if _SELECTION_SYNC:
        return
    features = context.scene.twin_guide_features
    if 0 <= state.active_feature_index < len(features):
        feature_id = features[state.active_feature_index].feature_id
        _show_feature(feature_id)
        _sync_feature_values(feature_id)


def _controls_for_feature(feature_id: str) -> dict[str, bpy.types.Object]:
    """按角色返回一个结构的全部三维代理。"""

    controls = {}
    for object_ in bpy.data.objects:
        if (
            object_.name.startswith(CONTROL_PREFIX)
            and object_.get("tg_feature_id") == feature_id
        ):
            controls[str(object_.get("tg_role", object_.get("tg_kind", "main")))] = object_
    return controls


def _axis_distance(object_: bpy.types.Object) -> float:
    """返回代理沿其第一允许轴的标量。"""

    if object_.get("tg_kind") == "window_size":
        return float(object_["tg_value"])
    axes = _gizmo_axes(object_)
    return 0.0 if not axes else (object_.location - axes[0][0]).dot(axes[0][1])


def _translate_operation_handles(
    center: bpy.types.Object,
    previous_location: Vector,
) -> None:
    """移动操作窗中心时保持其尺寸和切除量手柄相对位置不变。"""

    delta = center.location - previous_location
    if delta.length <= 1e-9:
        return
    site_index = int(center["tg_site_index"])
    for candidate in bpy.data.objects:
        if (
            candidate is center
            or not candidate.name.startswith(CONTROL_PREFIX)
            or candidate.get("tg_site_index") != site_index
        ):
            continue
        candidate.location += delta
        if "tg_origin" in candidate:
            candidate["tg_origin"] = list(Vector(candidate["tg_origin"]) + delta)
        if candidate.get("tg_kind") == "window_size":
            origin = Vector(candidate["tg_origin"])
            axis = Vector(candidate["tg_axis"])
            candidate.location = origin + axis * float(candidate["tg_value"])


def _mirror_operation_size_handle(object_: bpy.types.Object) -> None:
    """让操作窗相对边缘方块保持关于中心对称。"""

    role = str(object_.get("tg_role", ""))
    partners = {
        "width": "width_opposite",
        "width_opposite": "width",
        "height": "height_opposite",
        "height_opposite": "height",
    }
    partner_role = partners.get(role)
    if partner_role is None:
        return
    site_index = int(object_["tg_site_index"])
    center = _find_control("window_center", site_index=site_index)
    partner = _find_control(
        "window_size",
        site_index=site_index,
        role=partner_role,
    )
    if center is None or partner is None:
        return
    distance = _axis_distance(object_)
    partner["tg_value"] = distance
    partner.location = center.location + Vector(partner["tg_axis"]) * distance


def _sync_feature_values(feature_id: str) -> None:
    """把当前结构的完整参数同步到右侧精确输入区。"""

    global _FEATURE_VALUE_SYNC
    controls = _controls_for_feature(feature_id)
    state = bpy.context.scene.twin_guide_state
    _FEATURE_VALUE_SYNC = True
    if feature_id.startswith("operation_window:"):
        state.feature_value_1 = _axis_distance(controls["width"]) * 2.0
        state.feature_value_2 = _axis_distance(controls["height"]) * 2.0
        state.feature_value_3 = _axis_distance(controls["front"])
        state.feature_value_4 = _axis_distance(controls["rear"])
        center_values = _semantic_values(controls["window_center"])
        state.feature_value_5 = center_values[0][1]
        state.feature_value_6 = center_values[1][1]
    elif feature_id.startswith("connector:"):
        values = _semantic_values(controls["connector_node"])
        state.feature_value_1 = values[0][1]
        state.feature_value_2 = values[1][1]
    elif feature_id.startswith("sleeve:"):
        state.feature_value_1 = _axis_distance(controls["closed"])
        state.feature_value_2 = _axis_distance(controls["platform"])
        state.feature_value_3 = _axis_distance(controls["total"])
    elif feature_id.startswith("observation_window:"):
        state.feature_fdi_start = int(controls["start"]["tg_fdi"])
        state.feature_fdi_end = int(controls["end"]["tg_fdi"])
        state.feature_value_1 = _axis_distance(controls["drop"])
        state.feature_value_2 = _axis_distance(controls["height"])
        state.feature_value_3 = (
            _axis_distance(controls["sweep"]) * float(controls["sweep"]["tg_scale"])
        )
    elif feature_id == "press_junction":
        values = _semantic_values(controls["junction"])
        state.feature_value_1 = values[0][1]
        state.feature_value_2 = values[1][1]
    elif feature_id.startswith("press_anchor:"):
        control = next(iter(controls.values()))
        state.surface_role = str(control["tg_surface_role"])
    _FEATURE_VALUE_SYNC = False


def _move_on_axes(object_: bpy.types.Object, values: tuple[float, ...]) -> None:
    """按一个或两个局部轴直接设置代理位置。"""

    axes = _gizmo_axes(object_)
    if not axes:
        return
    previous_location = object_.location.copy()
    origin = axes[0][0]
    object_.location = origin + sum(
        (axis * value for (_axis_origin, axis), value in zip(axes, values, strict=False)),
        Vector((0.0, 0.0, 0.0)),
    )
    _constrain_control(object_)
    if object_.get("tg_kind") == "window_center":
        _translate_operation_handles(object_, previous_location)
    elif object_.get("tg_kind") == "window_size":
        _mirror_operation_size_handle(object_)


def _feature_values_updated(
    state: TwinGuideState,
    _context: bpy.types.Context,
) -> None:
    """把右侧完整参数组写回语义会话和结构代理。"""

    if _FEATURE_VALUE_SYNC or _SESSION is None or _SESSION.locked:
        return
    feature_id = _SESSION.selected_feature_id
    if feature_id is None:
        return
    controls = _controls_for_feature(feature_id)
    _SESSION.begin_edit()
    if feature_id.startswith("operation_window:"):
        _move_on_axes(controls["width"], (state.feature_value_1 / 2.0,))
        _move_on_axes(controls["height"], (state.feature_value_2 / 2.0,))
        _move_on_axes(controls["front"], (state.feature_value_3,))
        _move_on_axes(controls["rear"], (state.feature_value_4,))
        _move_on_axes(
            controls["window_center"],
            (state.feature_value_5, state.feature_value_6),
        )
        site_index = int(controls["width"]["tg_site_index"])
        _update_window_overlay(site_index)
    elif feature_id.startswith("connector:"):
        control = controls["connector_node"]
        route_start = Vector(control["tg_route_start"])
        route_end = Vector(control["tg_route_end"])
        route = route_end - route_start
        length = max(1e-9, route.length)
        distance = min(length, max(0.0, state.feature_value_1 * length))
        tangent = route.normalized()
        base = route_start + tangent * distance
        control["tg_path_distance"] = distance
        control["tg_tangent"] = list(tangent)
        control.location = base + Vector(control["tg_down"]) * max(
            0.0, state.feature_value_2
        )
        _update_connector_overlay(int(control["tg_guide_index"]))
    elif feature_id.startswith("sleeve:"):
        _move_on_axes(controls["closed"], (state.feature_value_1,))
        _move_on_axes(controls["platform"], (state.feature_value_2,))
        _move_on_axes(controls["total"], (state.feature_value_3,))
        _update_sleeve_hint_label(int(controls["platform"]["tg_guide_index"]))
    elif feature_id.startswith("observation_window:"):
        _move_on_axes(controls["drop"], (state.feature_value_1,))
        _update_observation_overlay(
            feature_id.split(":", 1)[1],
            drop_changed=True,
        )
        _move_on_axes(controls["height"], (state.feature_value_2,))
        _move_on_axes(
            controls["sweep"],
            (state.feature_value_3 / float(controls["sweep"]["tg_scale"]),),
        )
    elif feature_id == "press_junction":
        _move_on_axes(
            controls["junction"],
            (state.feature_value_1, state.feature_value_2),
        )
    _preview_feature_edit(feature_id)
    _SESSION.commit_edit()
    state.dirty = _SESSION.dirty
    _sync_feature_values(feature_id)


def _feature_fdi_updated(
    state: TwinGuideState,
    _context: bpy.types.Context,
) -> None:
    """把观察窗端点精确输入吸附到有效 FDI。"""

    global _FEATURE_VALUE_SYNC
    if _FEATURE_VALUE_SYNC or _SESSION is None or _SESSION.locked:
        return
    feature_id = _SESSION.selected_feature_id
    if feature_id is None or not feature_id.startswith("observation_window:"):
        return
    controls = _controls_for_feature(feature_id)
    _SESSION.begin_edit()
    invalid = False
    for role, fdi in (
        ("start", state.feature_fdi_start),
        ("end", state.feature_fdi_end),
    ):
        control = controls[role]
        candidates = json.loads(str(control["tg_candidates"]))
        candidate = next(
            (item for item in candidates if int(item["fdi"]) == fdi),
            None,
        )
        if candidate is None:
            invalid = True
            continue
        control.location = _vec(candidate["point"])
        control["tg_fdi"] = fdi
        _constrain_control(control)
    if invalid:
        _SESSION.cancel_edit()
        _FEATURE_VALUE_SYNC = True
        _sync_feature_values(feature_id)
        _FEATURE_VALUE_SYNC = False
        return
    _update_observation_overlay(feature_id.split(":", 1)[1])
    _preview_feature_edit(feature_id)
    _SESSION.commit_edit()
    state.dirty = _SESSION.dirty


def _surface_role_updated(
    state: TwinGuideState,
    _context: bpy.types.Context,
) -> None:
    """切换支撑点目标表面并要求有效重新吸附。"""

    if _FEATURE_VALUE_SYNC or _SESSION is None or _SESSION.locked:
        return
    feature_id = _SESSION.selected_feature_id
    if feature_id is None or not feature_id.startswith("press_anchor:"):
        return
    control = next(iter(_controls_for_feature(feature_id).values()))
    _SESSION.begin_edit()
    control["tg_surface_role"] = state.surface_role
    _snap_to_surface(control)
    if control.get("tg_resnap_required"):
        _SESSION.cancel_edit()
        return
    _preview_feature_edit(feature_id)
    _SESSION.commit_edit()
    bpy.context.scene.twin_guide_state.dirty = _SESSION.dirty


def _update_connector_overlay(index: int) -> None:
    """按生成器的局部 route_fraction 语义刷新单根连接线。"""
    node = _find_control("connector_node", guide_index=index)
    if node is None:
        return
    start = Vector(node["tg_start"])
    contact = Vector(node["tg_route_start"])
    end = Vector(node["tg_end"])
    path = (
        [start, node.location.copy(), contact, end]
        if str(node.get("tg_route_side", "right")) == "left"
        else [start, contact, node.location.copy(), end]
    )
    _update_curve(f"Connector_{index}", path)
    _move_hint_label(
        f"Connector_{index}",
        node.location + Vector(node["tg_down"]) * 1.4,
    )


def _update_press_overlay() -> None:
    """按当前锚点和汇合点即时刷新 Y 型按压梁。"""

    junction = _find_control("junction")
    if junction is None:
        return
    anchors = sorted(
        (
            item
            for item in bpy.data.objects
            if item.get("tg_kind") == "surface_anchor"
        ),
        key=lambda item: str(item.get("tg_anchor_id", "")),
    )
    for index, anchor in enumerate(anchors, start=1):
        normal = Vector(anchor["tg_normal"]).normalized()
        centerline_anchor = (
            anchor.location
            + normal * float(anchor.get("tg_centerline_depth", 0.0))
        )
        _update_curve(
            f"Press_{index}",
            [centerline_anchor, junction.location.copy()],
        )


def _update_observation_overlay(
    window_id: str,
    *,
    drop_changed: bool = False,
) -> None:
    """刷新观察窗端点、标量手柄和牙弓折线。"""

    start = _find_control(
        "observation_endpoint",
        window_id=window_id,
        role="start",
    )
    end = _find_control(
        "observation_endpoint",
        window_id=window_id,
        role="end",
    )
    if start is None or end is None:
        return
    if drop_changed:
        drop = _find_control(
            "observation_scalar",
            window_id=window_id,
            role="drop",
        )
        if drop is not None:
            drop_vector = Vector(drop["tg_axis"]) * _axis_distance(drop)
            for endpoint in (start, end):
                candidates = json.loads(str(endpoint["tg_candidates"]))
                current = next(
                    item
                    for item in candidates
                    if int(item["fdi"]) == int(endpoint["tg_fdi"])
                )
                endpoint.location = _vec(current["point"]) + drop_vector
                endpoint["tg_axis_origin"] = list(endpoint.location)
            midpoint = (start.location + end.location) / 2.0
            for role in ("height", "sweep"):
                scalar = _find_control(
                    "observation_scalar",
                    window_id=window_id,
                    role=role,
                )
                if scalar is None:
                    continue
                value = _axis_distance(scalar)
                scalar["tg_origin"] = list(midpoint)
                scalar.location = midpoint + Vector(scalar["tg_axis"]) * value
    for endpoint in (start, end):
        label = bpy.data.objects.get(
            f"{OVERLAY_PREFIX}FDI_{window_id}_{endpoint['tg_role']}"
        )
        if label is not None:
            label.location = endpoint.location + Vector((0.0, 0.0, 1.0))
            label.data.body = f"FDI {endpoint['tg_fdi']}"
    _update_curve(
        f"Observation_{window_id}",
        [start.location.copy(), end.location.copy()],
    )
    _move_hint_label(
        f"Observation_{window_id}",
        (start.location + end.location) / 2.0 + Vector((0.0, 0.0, 1.6)),
    )


def _update_window_overlay(index: int) -> None:
    """刷新投影到当前模型表面的轻量操作窗轮廓。"""
    center = _find_control("window_center", site_index=index)
    width = _find_control("window_size", site_index=index, role="width")
    height = _find_control("window_size", site_index=index, role="height")
    if center is None or width is None or height is None:
        return
    tangent = Vector(center["tg_tangent"])
    bitangent = Vector(center["tg_bitangent"])
    half_width = _axis_distance(width)
    half_height = _axis_distance(height)
    for role, sign in (("front", 1.0), ("rear", -1.0)):
        margin = _find_control("window_margin", site_index=index, role=role)
        if margin is None:
            continue
        desired = tangent * (half_width + 1.4) * sign
        previous = Vector(margin.get("tg_visual_offset", (0.0, 0.0, 0.0)))
        delta = desired - previous
        margin.location += delta
        margin["tg_origin"] = list(Vector(margin["tg_origin"]) + delta)
        margin["tg_visual_offset"] = list(desired)
    radius = min(
        float(center.get("tg_corner_radius", 0.0)),
        half_width,
        half_height,
    )
    points = []
    for tangent_sign, bitangent_sign, angle_start in (
        (1.0, 1.0, 0.0),
        (-1.0, 1.0, 90.0),
        (-1.0, -1.0, 180.0),
        (1.0, -1.0, 270.0),
    ):
        arc_center = (
            center.location
            + tangent * tangent_sign * (half_width - radius)
            + bitangent * bitangent_sign * (half_height - radius)
        )
        for step in range(5):
            angle = math.radians(angle_start + step * 22.5)
            points.append(
                arc_center
                + tangent * radius * math.cos(angle)
                + bitangent * radius * math.sin(angle)
            )
    projected = [_surface_point("template", point) for point in points]
    projection_failed = bpy.data.objects.get(f"{SURFACE_PREFIX}template") is None
    _update_curve(f"Window_{index}", projected, True)
    overlay = bpy.data.objects.get(f"{OVERLAY_PREFIX}Window_{index}")
    if overlay is not None:
        overlay.color = (
            (1.0, 0.15, 0.08, 1.0)
            if projection_failed
            else (0.15, 0.9, 0.45, 1.0)
        )
    center["tg_projection_failed"] = projection_failed
    _move_hint_label(
        f"Window_{index}",
        center.location + Vector(center["tg_normal"]) * 2.0,
    )


def _constrain_control(object_: bpy.types.Object) -> None:
    """把拖动位置投影回控制点允许的几何空间。"""
    kind = object_.get("tg_kind")
    if kind in {
        "sleeve_height",
        "window_size",
        "window_margin",
        "observation_scalar",
    }:
        origin = Vector(object_["tg_origin"])
        axis = Vector(object_["tg_axis"]).normalized()
        distance = max(0.05, (object_.location - origin).dot(axis))
        if kind == "window_size":
            object_["tg_value"] = distance
            object_.location = origin + axis * distance
            return
        if kind == "sleeve_height":
            guide_index = int(object_["tg_guide_index"])
            role = str(object_["tg_role"])
            values = {}
            for candidate_role in ("closed", "platform", "total"):
                candidate = _find_control(
                    "sleeve_height",
                    guide_index=guide_index,
                    role=candidate_role,
                )
                if candidate is not None:
                    candidate_origin = Vector(candidate["tg_origin"])
                    candidate_axis = Vector(candidate["tg_axis"])
                    values[candidate_role] = (
                        candidate.location - candidate_origin
                    ).dot(candidate_axis)
            if role == "closed" and "platform" in values:
                distance = min(distance, values["platform"] - 0.05)
            elif role == "platform":
                if "closed" in values:
                    distance = max(distance, values["closed"] + 0.05)
                if "total" in values:
                    distance = min(distance, values["total"] - 0.05)
            elif role == "total" and "platform" in values:
                distance = max(distance, values["platform"] + 0.05)
        object_.location = origin + axis * distance
    elif kind == "window_center":
        origin = Vector(object_["tg_center"])
        tangent = Vector(object_["tg_tangent"]).normalized()
        bitangent = Vector(object_["tg_bitangent"]).normalized()
        delta = object_.location - origin
        object_["tg_local_x"] = delta.dot(tangent)
        object_["tg_local_y"] = delta.dot(bitangent)
        object_.location = _surface_point(
            "template",
            origin
            + tangent * float(object_["tg_local_x"])
            + bitangent * float(object_["tg_local_y"]),
        )
    elif kind == "connector_node":
        route_start = Vector(object_["tg_route_start"])
        route_end = Vector(object_["tg_route_end"])
        route = route_end - route_start
        length = max(1e-9, route.length)
        tangent = route.normalized()
        distance = min(length, max(0.0, (object_.location - route_start).dot(tangent)))
        base = route_start + tangent * distance
        down = Vector(object_["tg_down"])
        lowered = max(0.0, (object_.location - base).dot(down))
        object_["tg_path_distance"] = distance
        object_["tg_tangent"] = list(tangent)
        object_.location = base + down * lowered
    elif kind == "junction":
        origin = Vector(object_["tg_plane_origin"])
        normal = Vector(object_["tg_plane_normal"]).normalized()
        object_.location -= normal * (object_.location - origin).dot(normal)
    elif kind == "observation_endpoint":
        candidates = sorted(
            json.loads(str(object_["tg_candidates"])),
            key=lambda item: float(item.get("arch_s_mm", 0.0)),
        )
        drop_control = _find_control(
            "observation_scalar",
            window_id=str(object_["tg_window_id"]),
            role="drop",
        )
        drop_vector = Vector((0.0, 0.0, 0.0))
        if drop_control is not None:
            drop_axis = Vector(drop_control["tg_axis"])
            drop_origin = Vector(drop_control["tg_origin"])
            drop_value = (drop_control.location - drop_origin).dot(drop_axis)
            drop_vector = drop_axis * drop_value
        arch_points = [_vec(item["point"]) for item in candidates]
        target = object_.location - drop_vector
        projected, _tangent, _distance = _project_to_polyline(target, arch_points)
        nearest = min(candidates, key=lambda item: (projected - _vec(item["point"])).length)
        object_.location = projected + drop_vector
        object_["tg_fdi"] = int(nearest["fdi"])
        label = bpy.data.objects.get(
            f"{OVERLAY_PREFIX}FDI_{object_['tg_window_id']}_{object_['tg_role']}"
        )
        if label is not None:
            label.location = object_.location + Vector((0.0, 0.0, 1.0))
            label.data.body = f"FDI {object_['tg_fdi']}"
    elif kind == "surface_anchor":
        _snap_to_surface(object_)


def _snap_observation_endpoint(object_: bpy.types.Object) -> None:
    """拖动结束时把连续牙弓位置确认到最近有效 FDI。"""

    if object_.get("tg_kind") != "observation_endpoint":
        return
    candidates = json.loads(str(object_["tg_candidates"]))
    drop = _find_control(
        "observation_scalar",
        window_id=str(object_["tg_window_id"]),
        role="drop",
    )
    drop_vector = Vector((0.0, 0.0, 0.0))
    if drop is not None:
        drop_vector = Vector(drop["tg_axis"]) * _axis_distance(drop)
    undropped_location = object_.location - drop_vector
    nearest = min(
        candidates,
        key=lambda item: (undropped_location - _vec(item["point"])).length,
    )
    object_["tg_fdi"] = int(nearest["fdi"])
    object_.location = _vec(nearest["point"]) + drop_vector
    _update_observation_overlay(str(object_["tg_window_id"]))
    _preview_feature_edit(str(object_["tg_feature_id"]))


def _editor_depsgraph_update(_scene: bpy.types.Scene, _depsgraph: object) -> None:
    """把三维对象选择同步到结构列表和参数面板。"""

    global _LAST_ACTIVE_NAME, _SELECTION_SYNC, _UPDATING
    if _UPDATING:
        return
    _UPDATING = True
    try:
        active = bpy.context.active_object
        active_name = "" if active is None else active.name
        if active is not None and active_name != _LAST_ACTIVE_NAME:
            feature_id = active.get("tg_feature_id")
            if feature_id is not None:
                feature_id = str(feature_id)
                if not active.name.startswith(CONTROL_PREFIX):
                    _show_feature(feature_id)
                    active = bpy.context.active_object
                    active_name = "" if active is None else active.name
                if _SESSION is not None:
                    _SESSION.select(feature_id)
                _sync_feature_values(feature_id)
                features = bpy.context.scene.twin_guide_features
                matching = next(
                    (
                        index
                        for index, item in enumerate(features)
                        if item.feature_id == feature_id
                    ),
                    None,
                )
                if matching is not None:
                    _SELECTION_SYNC = True
                    bpy.context.scene.twin_guide_state.active_feature_index = matching
                    _SELECTION_SYNC = False
            _LAST_ACTIVE_NAME = active_name
    finally:
        _UPDATING = False


def _feature_adapter_value(feature_id: str) -> EditorOverrides:
    """只把当前结构的语义值合入工作状态，其他结构保持不变。"""

    assert _CONFIG is not None
    current = _working_overrides()
    controls = _controls_for_feature(feature_id)
    if feature_id.startswith("connector:"):
        control = controls["connector_node"]
        base, _tangent, length = _connector_base(control)
        value = ConnectorAvoidanceOverride(
            int(control["tg_guide_index"]),
            min(1.0, max(0.0, float(control["tg_path_distance"]) / length)),
            max(0.0, (control.location - base).dot(Vector(control["tg_down"]))),
        )
        return with_connector(current, value)
    if feature_id.startswith("operation_window:"):
        center = controls["window_center"]
        width = controls["width"]
        height = controls["height"]
        tangent = Vector(center["tg_tangent"])
        bitangent = Vector(center["tg_bitangent"])
        center_offset = (
            Vector(center["tg_fixed_offset"])
            + tangent * float(center["tg_local_x"])
            + bitangent * float(center["tg_local_y"])
        )
        value = OperationWindowOverride(
            int(center["tg_site_index"]),
            max(
                0.0,
                float(center["tg_tangent_margin_base"])
                + _axis_distance(width)
                - float(center["tg_base_width"]) / 2.0,
            ),
            max(
                0.0,
                float(center["tg_bitangent_margin_base"])
                + _axis_distance(height)
                - float(center["tg_base_height"]) / 2.0,
            ),
            max(0.0, _axis_distance(controls["front"])),
            max(0.0, _axis_distance(controls["rear"])),
            tuple(center_offset),
        )
        return with_operation_window(current, value)
    if feature_id.startswith("sleeve:"):
        index = int(controls["platform"]["tg_guide_index"])
        value = SleeveGuideOverride(
            index,
            _axis_distance(controls["total"]),
            _axis_distance(controls["platform"]),
            _axis_distance(controls["closed"]),
        )
        return with_sleeve(current, value)
    if feature_id.startswith("observation_window:"):
        window_id = feature_id.split(":", 1)[1]
        source = next(
            (
                item.get("geometry")
                for item in _plan_features("observation_window")
                if item.get("id") == feature_id
            ),
            {},
        )
        definition = source.get("axis_sweep", {}) if isinstance(source, dict) else {}
        previous = current.observation_window_for(window_id)
        value = ObservationWindowOverride(
            window_id,
            int(controls["start"]["tg_fdi"]),
            int(controls["end"]["tg_fdi"]),
            _axis_distance(controls["drop"]),
            _axis_distance(controls["height"]),
            min(
                180.0,
                _axis_distance(controls["sweep"])
                * float(controls["sweep"]["tg_scale"]),
            ),
        )
        if not definition and previous is not None:
            value = previous
        return with_observation_window(current, value)
    if feature_id.startswith("press_anchor:"):
        control = next(iter(controls.values()))
        if control.get("tg_resnap_required"):
            raise ValueError("支撑点超过表面吸附容差")
        return with_surface_anchor(
            current,
            SurfaceAnchorOverride(
                str(control["tg_anchor_id"]),
                str(control["tg_surface_role"]),
                tuple(control.location),
                tuple(Vector(control["tg_normal"]).normalized()),
            ),
        )
    if feature_id == "press_junction":
        return with_press_junction(current, tuple(controls["junction"].location))
    return current


def _preview_feature_edit(feature_id: str) -> None:
    """通过对应 Adapter 更新语义工作值，不扫描其他 Blender 对象。"""

    if _SESSION is not None:
        _SESSION.preview_edit(_feature_adapter_value(feature_id))


def _load_model(path: Path) -> None:
    """替换场景中当前显示的正式或预览 STL。"""
    from twin_guide.blender.stl_io import import_stl_mesh

    previous = bpy.data.objects.get(PREVIEW_OBJECT_NAME)
    incoming_name = f"{PREVIEW_OBJECT_NAME}_Incoming"
    stale_incoming = bpy.data.objects.get(incoming_name)
    if stale_incoming is not None:
        bpy.data.objects.remove(stale_incoming, do_unlink=True)
    model = import_stl_mesh(path, incoming_name)
    if previous is not None:
        bpy.data.objects.remove(previous, do_unlink=True)
    model.name = PREVIEW_OBJECT_NAME
    model.display_type = "SOLID"


def _matching_model_snapshot(
    directory: Path,
    geometry_fingerprint: str,
) -> tuple[Path, Path, dict[str, object]] | None:
    """返回与当前病例参数对应的模型和编辑快照。"""

    model = directory / "twin_guide.stl"
    snapshot_path = directory / "ui-editor-snapshot.json"
    if not model.is_file() or not snapshot_path.is_file():
        return None
    snapshot = _load_editor_plan(snapshot_path)
    revision = int(snapshot.get("revision", -1))
    if not editor_snapshot_matches(
        snapshot,
        revision=revision,
        geometry_fingerprint=geometry_fingerprint,
    ):
        return None
    return model, snapshot_path, snapshot


def _initial_model(
    config_path: Path,
) -> tuple[Path | None, Path | None, dict[str, object] | None]:
    """优先加载与当前参数一致的实体和控制点快照。"""

    assert _CONFIG is not None
    fingerprint = editor_geometry_fingerprint(_CONFIG, config_path)
    for directory in (_CONFIG.output_directory, preview_directory(_CONFIG)):
        matched = _matching_model_snapshot(directory, fingerprint)
        if matched is not None:
            return matched
    formal = _CONFIG.output_directory / "twin_guide.stl"
    if formal.is_file():
        return formal, None, None
    preview = preview_directory(_CONFIG) / "twin_guide.stl"
    if preview.is_file():
        return preview, None, None
    return None, None, None


def _temporary_job_config(overrides: EditorOverrides) -> Path:
    """为未保存调整建立不影响病例文件的后台配置。"""
    assert _CONFIG is not None
    original = Path(bpy.context.scene.twin_guide_state.config_path)
    temporary = original.parent / f".twinguide-ui-{uuid.uuid4().hex}.yaml"
    shutil.copy2(original, temporary)
    save_editor_overrides(temporary, overrides, create_backup=False)
    return temporary


def _commit_pending_edit() -> None:
    """在保存、预览或导出前确认当前拖动并更新版本号。"""

    if _SESSION is not None and _SESSION.editing:
        _SESSION.commit_edit()
        bpy.context.scene.twin_guide_state.dirty = _SESSION.dirty


def _reuse_matching_preview() -> bool:
    """当前参数已有实体预览时直接加载，不重复启动生成进程。"""

    global _EDITOR_PLAN_PATH, _EDITOR_PLAN_VALUE, _LAST_PREVIEW_OVERRIDES
    assert _CONFIG is not None
    assert _SESSION is not None
    working_config = replace(
        _CONFIG,
        editor_overrides=_SESSION.working_overrides,
    )
    fingerprint = editor_geometry_fingerprint(
        working_config,
        Path(bpy.context.scene.twin_guide_state.config_path),
    )
    matched = _matching_model_snapshot(preview_directory(_CONFIG), fingerprint)
    if matched is None:
        return False
    model_path, snapshot_path, snapshot = matched
    if (
        bpy.data.objects.get(PREVIEW_OBJECT_NAME) is None
        or _EDITOR_PLAN_VALUE is None
        or _EDITOR_PLAN_VALUE.get("geometry_fingerprint") != fingerprint
    ):
        _load_model(model_path)
        _EDITOR_PLAN_VALUE = snapshot
        _EDITOR_PLAN_PATH = snapshot_path
        _create_controls()
        _show_model_view()
    state = bpy.context.scene.twin_guide_state
    state.task_status = "已复用现有预览"
    state.preview_status = f"预览已是当前版本（版本 {_SESSION.revision}）"
    _LAST_PREVIEW_OVERRIDES = _SESSION.working_overrides
    return True


def _start_job(mode: str) -> None:
    """启动预览或最终检验后台任务。"""
    global _JOB, _JOB_CONFIG_PATH
    assert _CONFIG is not None
    if _JOB is not None and _JOB.process.poll() is None:
        raise RuntimeError("已有后台任务正在运行")
    if _SESSION is None:
        raise RuntimeError("编辑会话尚未初始化")
    if mode != "plan":
        _commit_pending_edit()
    if mode == "preview" and _reuse_matching_preview():
        return
    overrides = _SESSION.working_overrides
    changed_ids = (
        changed_feature_ids(_LAST_PREVIEW_OVERRIDES, overrides)
        if mode == "preview"
        else ()
    )
    _JOB_CONFIG_PATH = _temporary_job_config(overrides)
    if mode == "plan":
        output = plan_directory(_CONFIG)
    elif mode == "preview":
        output = preview_directory(_CONFIG)
    else:
        output = candidate_directory(_CONFIG)
    manifest = output / "ui-task.json"
    _JOB = start_background_job(
        blender_binary=Path(bpy.app.binary_path),
        mode=mode,
        config_path=_JOB_CONFIG_PATH,
        output_directory=output,
        manifest_path=manifest,
        revision=0 if _SESSION is None else _SESSION.revision,
        changed_feature_ids=changed_ids,
    )
    state = bpy.context.scene.twin_guide_state
    state.task_status = "正在刷新编辑数据" if mode == "plan" else "生成中"
    if mode == "preview":
        state.preview_status = "预览生成中"
    elif mode == "final":
        state.validation_status = "运行中"


def _poll_job() -> float:
    """轮询后台状态并自动替换已完成预览。"""
    global _EDITOR_PLAN_PATH, _EDITOR_PLAN_STALE, _EDITOR_PLAN_VALUE
    global _LAST_PREVIEW_OVERRIDES
    global _JOB, _JOB_CONFIG_PATH
    if _JOB is None:
        return 0.5
    manifest = read_manifest(_JOB.manifest_path)
    if manifest is None:
        return 0.5
    status = str(manifest.get("status", ""))
    state = bpy.context.scene.twin_guide_state
    state.task_status = {
        "starting": "正在启动",
        "running": "运行中",
        "validating": "正在检验",
        "cancel_requested": "正在取消",
        "promoting": "正在更新正式模型",
        "completed": "已完成",
        "validation_failed": "检验失败",
        "failed": "任务失败",
        "cancelled": "已取消",
    }.get(status, status)
    if status not in {"completed", "validation_failed", "failed", "cancelled"}:
        if _JOB.process.poll() is not None:
            status = "failed"
            state.task_status = "任务失败：后台进程已退出"
            if _JOB.mode == "plan":
                state.task_status = "编辑数据刷新失败，仍可使用旧热点"
            elif _JOB.mode == "preview":
                state.preview_status = "更新失败，仍显示上一版"
            elif _JOB.mode == "final":
                state.validation_status = "失败"
                if _SESSION is not None:
                    _SESSION.locked = False
                state.editing_locked = False
        else:
            return 0.5
    if status not in {"completed", "validation_failed", "failed", "cancelled"}:
        return 0.5
    if _JOB.mode == "plan" and status == "completed":
        plan_path = Path(str(manifest.get("plan_path", "")))
        if plan_path.is_file():
            _EDITOR_PLAN_VALUE = _load_editor_plan(plan_path)
            _EDITOR_PLAN_PATH = plan_path
            _EDITOR_PLAN_STALE = False
            _create_controls()
            _show_model_view()
            state.task_status = "编辑数据已准备"
    elif _JOB.mode == "preview" and status == "completed":
        model_path = Path(str(manifest["model_path"]))
        snapshot_path = Path(str(manifest.get("editor_snapshot_path", "")))
        revision = int(manifest.get("revision", -1))
        if _SESSION is not None and not _SESSION.preview_is_current(revision):
            state.preview_status = "预览已过期，可重新更新"
        elif model_path.is_file() and snapshot_path.is_file():
            snapshot = _load_editor_plan(snapshot_path)
            if not editor_snapshot_matches(
                snapshot,
                revision=revision,
                geometry_fingerprint=str(manifest.get("geometry_fingerprint", "")),
            ):
                state.preview_status = "预览快照版本不一致，仍显示上一版"
            else:
                _EDITOR_PLAN_VALUE = snapshot
                _EDITOR_PLAN_PATH = snapshot_path
                _load_model(model_path)
                _create_controls()
                _show_model_view()
                state.preview_status = f"预览已更新（版本 {revision}）"
                _LAST_PREVIEW_OVERRIDES = _SESSION.working_overrides
        elif model_path.is_file():
            state.preview_status = "预览缺少编辑快照，仍显示上一版"
    if _JOB.mode == "final":
        if _SESSION is not None:
            _SESSION.locked = False
        state.editing_locked = False
        state.validation_status = {
            "passed": "通过",
            "failed": "失败",
            "not_run": "未运行",
        }.get(str(manifest.get("validation", "failed")), "失败")
        if status == "completed":
            formal = _CONFIG.output_directory / "twin_guide.stl"  # type: ignore[union-attr]
            snapshot_path = Path(str(manifest.get("editor_snapshot_path", "")))
            revision = int(manifest.get("revision", -1))
            snapshot = (
                _load_editor_plan(snapshot_path)
                if snapshot_path.is_file()
                else None
            )
            if (
                formal.is_file()
                and snapshot is not None
                and editor_snapshot_matches(
                    snapshot,
                    revision=revision,
                    geometry_fingerprint=str(
                        manifest.get("geometry_fingerprint", "")
                    ),
                )
            ):
                _EDITOR_PLAN_VALUE = snapshot
                _EDITOR_PLAN_PATH = snapshot_path
                _load_model(formal)
                _create_controls()
                _show_model_view()
            elif formal.is_file():
                _load_model(formal)
                _EDITOR_PLAN_STALE = True
                state.task_status = "正式模型已更新，编辑数据需要刷新"
    if _JOB.mode == "preview" and status == "failed":
        state.preview_status = "更新失败，仍显示上一版"
    elif _JOB.mode == "preview" and status == "cancelled":
        state.preview_status = "已取消，仍显示上一版"
    if _JOB.mode == "plan" and status in {"failed", "cancelled"}:
        _EDITOR_PLAN_VALUE = None
        _EDITOR_PLAN_PATH = None
        _EDITOR_PLAN_STALE = True
    if status == "failed":
        error = str(manifest.get("error", "后台任务失败"))
        state.task_status = f"任务失败：{error}"
    if _JOB_CONFIG_PATH is not None:
        _JOB_CONFIG_PATH.unlink(missing_ok=True)
    _JOB = None
    _JOB_CONFIG_PATH = None
    return 0.5


def _plane_axes(normal: Vector) -> tuple[Vector, Vector]:
    """为任意工作平面返回稳定的两个正交方向。"""

    unit = normal.normalized()
    seed = Vector((1.0, 0.0, 0.0))
    if abs(unit.dot(seed)) > 0.9:
        seed = Vector((0.0, 1.0, 0.0))
    first = (seed - unit * seed.dot(unit)).normalized()
    return first, unit.cross(first).normalized()


def _gizmo_axes(object_: bpy.types.Object) -> tuple[tuple[Vector, Vector], ...]:
    """返回当前结构代理允许拖动的原点和方向。"""

    kind = str(object_.get("tg_kind", ""))
    if kind == "connector_node":
        base, tangent, _length = _connector_base(object_)
        return (
            (base, tangent.normalized()),
            (base, Vector(object_["tg_down"]).normalized()),
        )
    if kind == "window_center":
        origin = Vector(object_["tg_center"])
        return (
            (origin, Vector(object_["tg_tangent"]).normalized()),
            (origin, Vector(object_["tg_bitangent"]).normalized()),
        )
    if kind == "junction":
        origin = Vector(object_["tg_plane_origin"])
        first, second = _plane_axes(Vector(object_["tg_plane_normal"]))
        return ((origin, first), (origin, second))
    if kind == "observation_endpoint":
        candidates = json.loads(str(object_["tg_candidates"]))
        current_fdi = int(object_["tg_fdi"])
        current = next(
            (item for item in candidates if int(item["fdi"]) == current_fdi),
            candidates[0],
        )
        tangent = _vec(current["tangent"]).normalized()
        return ((Vector(object_["tg_axis_origin"]), tangent),)
    if kind == "surface_anchor":
        return ()
    if "tg_origin" in object_ and "tg_axis" in object_:
        return (
            (
                Vector(object_["tg_origin"]),
                Vector(object_["tg_axis"]).normalized(),
            ),
        )
    return ()


def _gizmo_value(axis_index: int) -> float:
    """返回当前结构代理沿指定 Gizmo 轴的参数。"""

    object_ = bpy.context.active_object
    if object_ is None:
        return 0.0
    if object_.get("tg_kind") == "connector_node":
        base, _tangent, _length = _connector_base(object_)
        if axis_index == 0:
            return float(object_.get("tg_path_distance", 0.0))
        if axis_index == 1:
            return (object_.location - base).dot(Vector(object_["tg_down"]))
        return 0.0
    if object_.get("tg_kind") == "window_size":
        return _axis_distance(object_)
    if object_.get("tg_kind") == "window_center":
        return float(object_[f"tg_local_{'x' if axis_index == 0 else 'y'}"])
    axes = _gizmo_axes(object_)
    if axis_index >= len(axes):
        return 0.0
    origin, axis = axes[axis_index]
    return (object_.location - origin).dot(axis)


def _gizmo_set_value(axis_index: int, value: float) -> None:
    """由专用 Gizmo 更新代理、工作值和轻量预览。"""

    object_ = bpy.context.active_object
    if object_ is None or _SESSION is None or _SESSION.locked:
        return
    axes = _gizmo_axes(object_)
    if axis_index >= len(axes):
        return
    _SESSION.begin_edit()
    previous_location = object_.location.copy()
    if object_.get("tg_kind") == "connector_node":
        route_start = Vector(object_["tg_route_start"])
        route_end = Vector(object_["tg_route_end"])
        route = route_end - route_start
        path_length = max(1e-9, route.length)
        tangent = route.normalized()
        current_base, _current_tangent, _current_length = _connector_base(object_)
        down = Vector(object_["tg_down"]).normalized()
        current_offset = max(0.0, (object_.location - current_base).dot(down))
        if axis_index == 0:
            distance = min(path_length, max(0.0, value))
            offset = current_offset
        else:
            distance = float(object_.get("tg_path_distance", 0.0))
            offset = max(0.0, value)
        base = route_start + tangent * distance
        object_["tg_path_distance"] = distance
        object_["tg_tangent"] = list(tangent)
        object_.location = base + down * offset
        _update_connector_overlay(int(object_["tg_guide_index"]))
        _preview_feature_edit(str(object_["tg_feature_id"]))
        bpy.context.scene.twin_guide_state.dirty = _SESSION.dirty
        return
    if object_.get("tg_kind") == "window_center":
        local_key = f"tg_local_{'x' if axis_index == 0 else 'y'}"
        object_[local_key] = value
        origin = Vector(object_["tg_center"])
        tangent = Vector(object_["tg_tangent"])
        bitangent = Vector(object_["tg_bitangent"])
        object_.location = _surface_point(
            "template",
            origin
            + tangent * float(object_["tg_local_x"])
            + bitangent * float(object_["tg_local_y"]),
        )
        _translate_operation_handles(object_, previous_location)
        site_index = int(object_["tg_site_index"])
        _update_window_overlay(site_index)
        _preview_feature_edit(str(object_["tg_feature_id"]))
        bpy.context.scene.twin_guide_state.dirty = _SESSION.dirty
        return
    origin, axis = axes[axis_index]
    if len(axes) == 1:
        object_.location = origin + axis * value
    else:
        other_index = 1 - axis_index
        other_origin, other_axis = axes[other_index]
        other_value = (object_.location - other_origin).dot(other_axis)
        object_.location = origin + axis * value + other_axis * other_value
    _constrain_control(object_)
    kind = str(object_.get("tg_kind", ""))
    if kind == "window_center":
        _translate_operation_handles(object_, previous_location)
    elif kind == "window_size":
        _mirror_operation_size_handle(object_)
    if kind.startswith("window_"):
        site_index = int(object_["tg_site_index"])
        _update_window_overlay(site_index)
    elif kind == "sleeve_height":
        _update_sleeve_hint_label(int(object_["tg_guide_index"]))
    elif kind == "connector_node":
        _update_connector_overlay(int(object_["tg_guide_index"]))
    elif kind == "observation_endpoint":
        _update_observation_overlay(str(object_["tg_window_id"]))
    elif kind == "junction":
        _update_press_overlay()
    elif kind == "observation_scalar" and object_.get("tg_role") == "drop":
        _update_observation_overlay(
            str(object_["tg_window_id"]),
            drop_changed=True,
        )
    _preview_feature_edit(str(object_["tg_feature_id"]))
    bpy.context.scene.twin_guide_state.dirty = _SESSION.dirty


def _semantic_values(
    object_: bpy.types.Object,
) -> tuple[tuple[str, float], ...]:
    """返回右侧面板使用的临床参数名称和值。"""

    kind = str(object_.get("tg_kind", ""))
    role = str(object_.get("tg_role", ""))
    if kind == "connector_node":
        base, _tangent, length = _connector_base(object_)
        return (
            (
                "沿线位置",
                float(object_.get("tg_path_distance", 0.0)) / length,
            ),
            (
                "向下偏移 (mm)",
                (object_.location - base).dot(Vector(object_["tg_down"])),
            ),
        )
    values = _gizmo_axes(object_)
    if not values:
        return ()
    raw = tuple((object_.location - origin).dot(axis) for origin, axis in values)
    if kind == "window_size":
        label = "窗口宽度 (mm)" if role.startswith("width") else "窗口高度 (mm)"
        return ((label, raw[0] * 2.0),)
    if kind == "window_margin":
        return (("前部切除量 (mm)" if role == "front" else "后部切除量 (mm)", raw[0]),)
    if kind == "sleeve_height":
        labels = {"closed": "底部高度 (mm)", "platform": "平台高度 (mm)", "total": "总高度 (mm)"}
        return ((labels[role], raw[0]),)
    if kind == "observation_scalar":
        labels = {"drop": "轴向下沉 (mm)", "height": "窗口高度 (mm)", "sweep": "扫掠角 (°)"}
        value = raw[0] * float(object_.get("tg_scale", 1.0))
        return ((labels[role], value),)
    if kind == "window_center":
        return (
            ("局部横向 (mm)", float(object_["tg_local_x"])),
            ("局部纵向 (mm)", float(object_["tg_local_y"])),
        )
    if kind == "junction":
        return (("工作平面 X (mm)", raw[0]), ("工作平面 Y (mm)", raw[1]))
    return ()


class TwinGuideState(bpy.types.PropertyGroup):
    """保存彼此独立的病例、修改、任务和检验状态。"""
    config_path: bpy.props.StringProperty(name="病例配置", subtype="FILE_PATH")
    case_label: bpy.props.StringProperty(name="当前病例")
    review_status: bpy.props.StringProperty(name="病例审核")
    validation_status: bpy.props.StringProperty(name="几何检验", default="未运行")
    preview_status: bpy.props.StringProperty(name="实体预览", default="未更新")
    task_status: bpy.props.StringProperty(name="后台任务", default="空闲")
    dirty: bpy.props.BoolProperty(name="未保存修改", default=False)
    editing_locked: bpy.props.BoolProperty(name="编辑锁定", default=False)
    active_feature_index: bpy.props.IntProperty(
        name="当前结构",
        default=-1,
        update=_feature_index_updated,
    )
    feature_value_1: bpy.props.FloatProperty(precision=3, update=_feature_values_updated)
    feature_value_2: bpy.props.FloatProperty(precision=3, update=_feature_values_updated)
    feature_value_3: bpy.props.FloatProperty(precision=3, update=_feature_values_updated)
    feature_value_4: bpy.props.FloatProperty(precision=3, update=_feature_values_updated)
    feature_value_5: bpy.props.FloatProperty(precision=3, update=_feature_values_updated)
    feature_value_6: bpy.props.FloatProperty(precision=3, update=_feature_values_updated)
    feature_fdi_start: bpy.props.IntProperty(min=11, max=48, update=_feature_fdi_updated)
    feature_fdi_end: bpy.props.IntProperty(min=11, max=48, update=_feature_fdi_updated)
    surface_role: bpy.props.EnumProperty(
        name="吸附表面",
        items=(
            ("template", "导板", "吸附到导板表面"),
            ("dentition", "牙面", "吸附到患者牙列表面"),
        ),
        update=_surface_role_updated,
    )
    show_advanced: bpy.props.BoolProperty(name="高级信息", default=False)


class TwinGuideModelViewOperator(bpy.types.Operator):
    """回到只显示结构概览的模型查看状态。"""

    bl_idname = "twinguide.model_view"
    bl_label = "模型查看"

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        """编辑数据准备完成且未锁定时允许切换结构。"""

        return bool(
            _EDITOR_PLAN_PATH is not None
            and not context.scene.twin_guide_state.editing_locked
        )

    def execute(self, context: bpy.types.Context) -> set[str]:
        """显示指定结构的控制点。"""

        global _SELECTION_SYNC
        _SELECTION_SYNC = True
        context.scene.twin_guide_state.active_feature_index = -1
        _SELECTION_SYNC = False
        _show_model_view()
        return {"FINISHED"}


class TwinGuideHandleDragOperator(bpy.types.Operator):
    """用明确的按下、移动、松开和取消生命周期拖动语义手柄。"""

    bl_idname = "twinguide.drag_feature_handle"
    bl_label = "调整 TwinGuide 参数"
    bl_options: ClassVar[set[str]] = {"BLOCKING"}

    axis_index: bpy.props.IntProperty(default=0, min=0, max=1)
    _object_name: str = ""
    _initial_value: float = 0.0
    _mouse_origin: Vector | None = None
    _screen_axis: Vector | None = None
    _pixels_per_unit: float = 1.0

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        """仅在可编辑语义手柄处于活动状态时允许拖动。"""

        object_ = context.active_object
        return bool(
            object_ is not None
            and object_.name.startswith(CONTROL_PREFIX)
            and _SESSION is not None
            and not _SESSION.locked
        )

    def invoke(
        self,
        context: bpy.types.Context,
        event: bpy.types.Event,
    ) -> set[str]:
        """记录语义快照和当前局部轴的屏幕投影。"""

        object_ = context.active_object
        if object_ is None or _SESSION is None:
            return {"CANCELLED"}
        axes = _gizmo_axes(object_)
        if self.axis_index >= len(axes):
            return {"CANCELLED"}
        from bpy_extras import view3d_utils

        _origin, axis = axes[self.axis_index]
        start = view3d_utils.location_3d_to_region_2d(
            context.region,
            context.region_data,
            object_.location,
        )
        end = view3d_utils.location_3d_to_region_2d(
            context.region,
            context.region_data,
            object_.location + axis,
        )
        if start is None or end is None or (end - start).length < 1e-6:
            screen_axis = Vector((1.0, 0.0))
            pixels_per_unit = 20.0
        else:
            delta = end - start
            pixels_per_unit = delta.length
            screen_axis = delta.normalized()
        self._object_name = object_.name
        self._initial_value = _gizmo_value(self.axis_index)
        self._mouse_origin = Vector((event.mouse_region_x, event.mouse_region_y))
        self._screen_axis = screen_axis
        self._pixels_per_unit = pixels_per_unit
        _SESSION.begin_edit()
        context.window_manager.modal_handler_add(self)
        context.area.header_text_set("拖动调整；Shift 精调；Esc 取消")
        return {"RUNNING_MODAL"}

    def modal(
        self,
        context: bpy.types.Context,
        event: bpy.types.Event,
    ) -> set[str]:
        """拖动时只更新语义值和轻量几何，松开时提交一个 revision。"""

        if _SESSION is None or bpy.data.objects.get(self._object_name) is None:
            return {"CANCELLED"}
        if event.type == "ESC":
            _SESSION.cancel_edit()
            context.area.header_text_set(None)
            _rebuild_working_proxies()
            return {"CANCELLED"}
        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            object_ = bpy.data.objects.get(self._object_name)
            if object_ is not None:
                _snap_observation_endpoint(object_)
            _SESSION.commit_edit()
            context.scene.twin_guide_state.dirty = _SESSION.dirty
            context.area.header_text_set(None)
            return {"FINISHED"}
        if event.type != "MOUSEMOVE":
            return {"RUNNING_MODAL"}
        assert self._mouse_origin is not None and self._screen_axis is not None
        current = Vector((event.mouse_region_x, event.mouse_region_y))
        delta = (current - self._mouse_origin).dot(self._screen_axis)
        value = self._initial_value + delta / max(self._pixels_per_unit, 1e-6)
        if event.shift:
            object_ = bpy.data.objects[self._object_name]
            step = (
                1.0 / float(object_.get("tg_scale", 1.0))
                if object_.get("tg_kind") == "observation_scalar"
                and object_.get("tg_role") == "sweep"
                else 0.1
            )
            value = round(value / step) * step
        _gizmo_set_value(self.axis_index, value)
        return {"RUNNING_MODAL"}


class TwinGuideSurfaceDragOperator(bpy.types.Operator):
    """使用视图射线把支撑点直接滑动到指定表面。"""

    bl_idname = "twinguide.drag_surface_anchor"
    bl_label = "在表面重新定位"

    _object_name: str = ""
    _start_location: Vector | None = None
    _start_normal: Vector | None = None
    _start_color: tuple[float, float, float, float] | None = None
    _start_resnap_required: bool = False

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        """只允许当前表面图钉进入射线拖动。"""

        object_ = context.active_object
        return bool(
            object_ is not None
            and object_.get("tg_kind") == "surface_anchor"
            and _SESSION is not None
            and not _SESSION.locked
        )

    def invoke(
        self,
        context: bpy.types.Context,
        _event: bpy.types.Event,
    ) -> set[str]:
        """进入只接受表面命中的模态拖动。"""

        object_ = context.active_object
        if (
            object_ is None
            or object_.get("tg_kind") != "surface_anchor"
            or _SESSION is None
            or _SESSION.locked
        ):
            return {"CANCELLED"}
        self._object_name = object_.name
        self._start_location = object_.location.copy()
        self._start_normal = Vector(object_["tg_normal"])
        self._start_color = tuple(object_.color)
        self._start_resnap_required = bool(object_.get("tg_resnap_required", False))
        _SESSION.begin_edit()
        context.window_manager.modal_handler_add(self)
        context.area.header_text_set("在导板或牙面上移动鼠标；左键确认，Esc 取消")
        return {"RUNNING_MODAL"}

    def modal(
        self,
        context: bpy.types.Context,
        event: bpy.types.Event,
    ) -> set[str]:
        """射线命中时更新图钉，未命中时保留最后有效位置。"""

        object_ = bpy.data.objects.get(self._object_name)
        if object_ is None or _SESSION is None:
            return {"CANCELLED"}
        if event.type == "ESC":
            _SESSION.cancel_edit()
            if self._start_location is not None:
                object_.location = self._start_location
            if self._start_normal is not None:
                object_["tg_normal"] = list(self._start_normal)
                object_.rotation_quaternion = self._start_normal.to_track_quat(
                    "Z", "Y"
                )
                anchor_index = str(object_["tg_anchor_id"]).rsplit("_", 1)[-1]
                _move_hint_label(
                    f"PressAnchor_{anchor_index}",
                    object_.location + self._start_normal * 1.4,
                )
            if self._start_color is not None:
                object_.color = self._start_color
            object_["tg_resnap_required"] = self._start_resnap_required
            _update_press_overlay()
            context.scene.twin_guide_state.dirty = _SESSION.dirty
            context.area.header_text_set(None)
            return {"CANCELLED"}
        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            _SESSION.commit_edit()
            context.scene.twin_guide_state.dirty = _SESSION.dirty
            context.area.header_text_set(None)
            return {"FINISHED"}
        if event.type != "MOUSEMOVE":
            return {"RUNNING_MODAL"}
        area = next(
            (item for item in context.screen.areas if item.type == "VIEW_3D"),
            None,
        )
        if area is None:
            return {"RUNNING_MODAL"}
        region = next((item for item in area.regions if item.type == "WINDOW"), None)
        space = area.spaces.active
        if region is None or space.region_3d is None:
            return {"RUNNING_MODAL"}
        from bpy_extras import view3d_utils

        coordinate = (event.mouse_x - region.x, event.mouse_y - region.y)
        origin = view3d_utils.region_2d_to_origin_3d(
            region, space.region_3d, coordinate
        )
        direction = view3d_utils.region_2d_to_vector_3d(
            region, space.region_3d, coordinate
        )
        target = bpy.data.objects.get(
            f"{SURFACE_PREFIX}{object_['tg_surface_role']}"
        )
        if target is None:
            return {"RUNNING_MODAL"}
        inverse = target.matrix_world.inverted()
        local_origin = inverse @ origin
        local_direction = (inverse.to_3x3() @ direction).normalized()
        hit, location, normal, _index = target.ray_cast(
            local_origin,
            local_direction,
        )
        if hit:
            object_.location = target.matrix_world @ location
            world_normal = (target.matrix_world.to_3x3() @ normal).normalized()
            object_["tg_normal"] = list(world_normal)
            object_.rotation_quaternion = world_normal.to_track_quat("Z", "Y")
            object_["tg_resnap_required"] = False
            object_.color = (0.25, 0.9, 0.35, 1.0)
            anchor_index = str(object_["tg_anchor_id"]).rsplit("_", 1)[-1]
            _move_hint_label(
                f"PressAnchor_{anchor_index}",
                object_.location + world_normal * 1.4,
            )
            _update_press_overlay()
            _preview_feature_edit(str(object_["tg_feature_id"]))
            context.scene.twin_guide_state.dirty = _SESSION.dirty
        return {"RUNNING_MODAL"}


class TwinGuideSaveOperator(bpy.types.Operator):
    """仅在明确点击时原子写回病例覆盖值。"""
    bl_idname = "twinguide.save_adjustments"
    bl_label = "保存调整"

    @classmethod
    def poll(cls, _context: bpy.types.Context) -> bool:
        """最终任务锁定期间不允许写回病例。"""

        return bool(_SESSION is not None and not _SESSION.locked)

    def execute(self, context: bpy.types.Context) -> set[str]:
        """保存当前控制点并刷新保存基线。"""
        global _CONFIG
        try:
            if _SESSION is not None and _SESSION.editing:
                _SESSION.commit_edit()
            if _SESSION is None:
                raise RuntimeError("编辑会话尚未初始化")
            overrides = _SESSION.working_overrides
            save_editor_overrides(Path(context.scene.twin_guide_state.config_path), overrides)
            _CONFIG = CaseConfig.from_yaml(Path(context.scene.twin_guide_state.config_path))
            _SESSION.mark_saved()
            context.scene.twin_guide_state.dirty = False
        except Exception as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class TwinGuideResetSelectedOperator(bpy.types.Operator):
    """撤销最近一次已确认的语义编辑。"""
    bl_idname = "twinguide.reset_selected"
    bl_label = "撤销"

    @classmethod
    def poll(cls, _context: bpy.types.Context) -> bool:
        """存在可撤销语义快照时启用。"""

        return bool(_SESSION is not None and not _SESSION.locked and _SESSION.undo_stack)

    def execute(self, context: bpy.types.Context) -> set[str]:
        """撤销会话快照并重建结构代理。"""

        if _SESSION is None or not _SESSION.undo():
            return {"CANCELLED"}
        _rebuild_working_proxies()
        return {"FINISHED"}


class TwinGuideRedoOperator(bpy.types.Operator):
    """重做最近一次已撤销的语义编辑。"""

    bl_idname = "twinguide.redo_adjustment"
    bl_label = "重做"

    @classmethod
    def poll(cls, _context: bpy.types.Context) -> bool:
        """存在可重做语义快照时启用。"""

        return bool(_SESSION is not None and not _SESSION.locked and _SESSION.redo_stack)

    def execute(self, _context: bpy.types.Context) -> set[str]:
        """重做会话快照并重建结构代理。"""

        if _SESSION is None or not _SESSION.redo():
            return {"CANCELLED"}
        _rebuild_working_proxies()
        return {"FINISHED"}


class TwinGuideRestoreOperator(bpy.types.Operator):
    """从已保存阶段结果恢复全部控制点。"""
    bl_idname = "twinguide.restore_saved"
    bl_label = "恢复已保存值"

    @classmethod
    def poll(cls, _context: bpy.types.Context) -> bool:
        """工作值偏离保存值且未锁定时启用。"""

        return bool(_SESSION is not None and not _SESSION.locked and _SESSION.dirty)

    def execute(self, _context: bpy.types.Context) -> set[str]:
        """恢复最近保存值并建立一个可撤销步骤。"""

        if _SESSION is None:
            return {"CANCELLED"}
        _SESSION.replace(_SESSION.saved_overrides)
        _rebuild_working_proxies()
        return {"FINISHED"}


class TwinGuidePreviewOperator(bpy.types.Operator):
    """启动不运行几何检验的实体预览。"""
    bl_idname = "twinguide.update_preview"
    bl_label = "更新预览"

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        """编辑计划就绪且没有其他后台任务时启用。"""

        return bool(
            _EDITOR_PLAN_PATH is not None
            and not _EDITOR_PLAN_STALE
            and _JOB is None
            and not context.scene.twin_guide_state.editing_locked
        )

    def execute(self, context: bpy.types.Context) -> set[str]:
        """提交独立后台实体预览任务。"""
        try:
            _start_job("preview")
        except Exception as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class TwinGuideFinalOperator(bpy.types.Operator):
    """保存、生成候选、检验并安全提升正式输出。"""
    bl_idname = "twinguide.final_export"
    bl_label = "确认导出并检验"

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        """编辑计划就绪且没有其他后台任务时启用。"""

        return bool(
            _EDITOR_PLAN_PATH is not None
            and _JOB is None
            and not context.scene.twin_guide_state.editing_locked
        )

    def execute(self, context: bpy.types.Context) -> set[str]:
        """提交最终候选生成和完整检验任务。"""
        result = bpy.ops.twinguide.save_adjustments()
        if "FINISHED" not in result:
            return {"CANCELLED"}
        try:
            if _SESSION is not None:
                _SESSION.locked = True
            context.scene.twin_guide_state.editing_locked = True
            _start_job("final")
        except Exception as error:
            if _SESSION is not None:
                _SESSION.locked = False
            context.scene.twin_guide_state.editing_locked = False
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class TwinGuideCancelOperator(bpy.types.Operator):
    """取消当前后台生成任务。"""
    bl_idname = "twinguide.cancel_job"
    bl_label = "取消后台任务"

    @classmethod
    def poll(cls, _context: bpy.types.Context) -> bool:
        """只在后台任务存在时启用。"""

        return _JOB is not None

    def execute(self, _context: bpy.types.Context) -> set[str]:
        """终止子进程并保留既有正式文件。"""
        if _JOB is not None and not _JOB.cancel():
            self.report({"INFO"}, "正式模型正在更新，当前阶段不能取消")
        return {"FINISHED"}


class _TwinGuidePanelRenderer:
    """显示简化操作按钮和相互独立的状态。"""

    layout: bpy.types.UILayout

    @staticmethod
    def _status_icon(value: str) -> str:
        """为短状态选择稳定的 Blender 图标。"""

        if any(token in value for token in ("失败", "上一版")):
            return "ERROR"
        if any(token in value for token in ("通过", "已", "完成", "空闲")):
            return "CHECKMARK"
        return "INFO"

    def _draw_status_card(
        self,
        layout: bpy.types.UILayout,
        state: TwinGuideState,
    ) -> None:
        """绘制病例、审核、预览和任务状态。"""

        box = layout.box()
        header = box.row(align=True)
        header.label(text=state.case_label, icon="FILE_TICK")
        if state.dirty:
            header.label(text="未保存", icon="ERROR")
        else:
            header.label(text="已保存", icon="CHECKMARK")
        status = box.grid_flow(columns=2, even_columns=True, align=True)
        status.label(text=f"审核 {state.review_status}")
        status.label(text=f"检验 {state.validation_status}")
        if state.task_status not in {"", "空闲", "已完成"}:
            box.label(text=state.task_status, icon=self._status_icon(state.task_status))

    @staticmethod
    def _feature_instruction(feature_id: str) -> str:
        """返回当前结构的一句话操作提示。"""

        if feature_id.startswith("operation_window:"):
            return "拖中心移动窗口；拖边缘调宽高；拖前/后点调切除量。"
        if feature_id.startswith("observation_window:"):
            return "拖起止点换牙位；拖轴向手柄调下沉、高度和扫掠角。"
        if feature_id.startswith("connector:"):
            return "拖沿线箭头改避让节点位置，拖向下箭头改正视避让量。"
        if feature_id.startswith("press_anchor:"):
            return "选择吸附表面后，按“在表面重新定位”让图钉贴面滑动。"
        if feature_id == "press_junction":
            return "拖二维十字，只在当前工作平面内移动汇合点。"
        return "拖三个圆环分别调整底部、平台和总高度。"

    @staticmethod
    def _draw_feature_values(
        box: bpy.types.UILayout,
        state: TwinGuideState,
        feature_id: str,
    ) -> None:
        """绘制当前完整结构的临床参数输入框。"""

        if feature_id.startswith("operation_window:"):
            labels = (
                "宽度 (mm)",
                "高度 (mm)",
                "前部切除量 (mm)",
                "后部切除量 (mm)",
                "局部横向 (mm)",
                "局部纵向 (mm)",
            )
        elif feature_id.startswith("connector:"):
            labels = ("沿线位置", "向下偏移 (mm)")
            if _CONFIG is not None:
                box.label(
                    text=f"梁直径：{_CONFIG.geometry.connector_diameter_mm:.3f} mm"
                )
                blocks = _CONFIG.geometry.connection_blocks
                enabled = [
                    label
                    for label, value in (
                        ("下层", blocks.lower_main),
                        ("上层", blocks.upper_main),
                        ("按压梁", blocks.press_beam),
                    )
                    if value
                ]
                box.label(text=f"连接分块：{'、'.join(enabled)}")
        elif feature_id.startswith("sleeve:"):
            labels = ("底部高度 (mm)", "平台高度 (mm)", "总高度 (mm)")
        elif feature_id.startswith("observation_window:"):
            row = box.row(align=True)
            row.prop(state, "feature_fdi_start", text="起点 FDI")
            row.prop(state, "feature_fdi_end", text="终点 FDI")
            labels = ("轴向下沉 (mm)", "窗口高度 (mm)", "扫掠角 (°)")
        elif feature_id == "press_junction":
            labels = ("工作平面 X (mm)", "工作平面 Y (mm)")
        elif feature_id.startswith("press_anchor:"):
            box.prop(state, "surface_role")
            box.operator("twinguide.drag_surface_anchor")
            labels = ()
        else:
            labels = ()
        for index, label in enumerate(labels, start=1):
            box.prop(state, f"feature_value_{index}", text=label)

    @staticmethod
    def _draw_advanced(
        box: bpy.types.UILayout,
        state: TwinGuideState,
        selected: bpy.types.Object,
    ) -> None:
        """在折叠区域显示不用于常规调整的世界信息。"""

        row = box.row()
        icon = "TRIA_DOWN" if state.show_advanced else "TRIA_RIGHT"
        row.prop(state, "show_advanced", text="高级信息", icon=icon, emboss=False)
        if not state.show_advanced:
            return
        location = selected.location
        box.label(
            text=(
                f"世界坐标：({location.x:.3f}, {location.y:.3f}, {location.z:.3f}) mm"
            )
        )
        if "tg_normal" in selected:
            normal = Vector(selected["tg_normal"])
            box.label(
                text=f"法向：({normal.x:.4f}, {normal.y:.4f}, {normal.z:.4f})"
            )

    def draw(self, context: bpy.types.Context) -> None:
        """绘制当前病例编辑工作流。"""
        layout = self.layout
        state = context.scene.twin_guide_state
        self._draw_status_card(layout, state)
        if _EDITOR_PLAN_PATH is None:
            box = layout.box()
            box.label(text="正在准备编辑数据", icon="TIME")
            hint = box.column(align=True)
            hint.enabled = False
            hint.label(text="模型可以查看，几何编辑和预览稍后可用")
            hint.label(text="准备完成后会自动显示控制点")
            cancel = layout.column()
            cancel.enabled = _JOB is not None
            cancel.operator("twinguide.cancel_job")
            return
        editor = layout.column()
        editor.enabled = not state.editing_locked
        selected = context.active_object
        if (
            selected is not None
            and selected.name.startswith(CONTROL_PREFIX)
            and not selected.hide_get()
        ):
            box = editor.box()
            feature_id = str(selected.get("tg_feature_id", ""))
            _group, feature_name = _feature_label(feature_id)
            box.label(text=feature_name, icon="EDITMODE_HLT")
            handle_hint = str(selected.get("tg_hint", ""))
            if handle_hint:
                box.label(text=f"手柄：{handle_hint}")
            hint = box.column(align=True)
            hint.enabled = False
            hint.label(text=self._feature_instruction(feature_id))
            hint.label(text="Shift 精调，Esc 取消")
            box.separator()
            self._draw_feature_values(box, state, feature_id)
            self._draw_advanced(box, state, selected)
            if feature_id.startswith("operation_window:"):
                center = _find_control(
                    "window_center",
                    site_index=int(feature_id.rsplit(":", 1)[-1]),
                )
                if center is not None and center.get("tg_projection_failed"):
                    box.label(text="部分轮廓未能投影到当前模型", icon="ERROR")
            if selected.get("tg_resnap_required"):
                box.label(text="距已保存表面过远，请重新吸附", icon="ERROR")
        else:
            hint = editor.box()
            hint.label(text="选择一个结构开始调整", icon="INFO")
            hint.label(text="可点三维标签、彩色热点或左侧列表")
        edit_box = editor.box()
        edit_box.label(text="编辑", icon="EDITMODE_HLT")
        row = edit_box.row(align=True)
        row.operator("twinguide.reset_selected", text="撤销", icon="LOOP_BACK")
        row.operator("twinguide.redo_adjustment", text="重做", icon="LOOP_FORWARDS")
        edit_box.operator("twinguide.restore_saved", text="恢复已保存值")
        edit_box.operator("twinguide.save_adjustments", text="保存调整")
        output_box = editor.box()
        output_box.label(text="预览与输出", icon="INFO")
        note = output_box.column(align=True)
        note.enabled = False
        note.label(text="预览用于快速检查形态")
        note.label(text="最终结果以导出并检验为准")
        output_box.operator("twinguide.update_preview", text="更新预览")
        output_box.operator("twinguide.final_export", text="确认导出并检验")
        cancel = layout.column()
        cancel.enabled = _JOB is not None
        cancel.operator("twinguide.cancel_job")


CLASSES = (
    TwinGuideFeatureItem,
    TWINGUIDE_UL_feature_list,
    TwinGuideState,
    TwinGuideHandleDragOperator,
    TwinGuideFeatureGizmoGroup,
    TwinGuideModelViewOperator,
    TwinGuideSurfaceDragOperator,
    TwinGuideSaveOperator,
    TwinGuideResetSelectedOperator,
    TwinGuideRedoOperator,
    TwinGuideRestoreOperator,
    TwinGuidePreviewOperator,
    TwinGuideFinalOperator,
    TwinGuideCancelOperator,
    TwinGuideStructurePanel,
    TwinGuidePanel,
)


def _draw_twinguide_header(
    self: bpy.types.Header,
    context: bpy.types.Context,
) -> None:
    """在三维视图顶部显示状态和主要操作。"""

    if not context.scene.get("twinguide_ready"):
        return
    state = context.scene.twin_guide_state
    layout = self.layout
    layout.separator_spacer()
    layout.label(text=state.review_status, icon="CHECKMARK" if state.review_status == "已确认" else "INFO")
    layout.label(text="未保存" if state.dirty else "已保存")
    layout.label(text=f"预览：{state.preview_status}")
    layout.label(text=f"检验：{state.validation_status}")
    layout.operator("twinguide.reset_selected", text="", icon="LOOP_BACK")
    layout.operator("twinguide.redo_adjustment", text="", icon="LOOP_FORWARDS")
    layout.operator("twinguide.save_adjustments", text="保存")
    layout.operator("twinguide.update_preview", text="预览")
    layout.operator("twinguide.final_export", text="导出并检验")
    if _JOB is not None:
        layout.operator("twinguide.cancel_job", text="取消", icon="CANCEL")


def register() -> None:
    """注册 Blender 面板、操作器和拖动监听。"""
    for item in CLASSES:
        bpy.utils.register_class(item)
    bpy.types.Scene.twin_guide_state = bpy.props.PointerProperty(type=TwinGuideState)
    bpy.types.Scene.twin_guide_features = bpy.props.CollectionProperty(
        type=TwinGuideFeatureItem
    )
    if _editor_depsgraph_update not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_editor_depsgraph_update)
    if not bpy.app.timers.is_registered(_poll_job):
        bpy.app.timers.register(_poll_job, first_interval=0.5, persistent=True)
    bpy.types.VIEW3D_HT_header.append(_draw_twinguide_header)


def unregister() -> None:
    """注销 Blender 面板及其监听器。"""
    if _editor_depsgraph_update in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_editor_depsgraph_update)
    bpy.types.VIEW3D_HT_header.remove(_draw_twinguide_header)
    del bpy.types.Scene.twin_guide_features
    del bpy.types.Scene.twin_guide_state
    for item in reversed(CLASSES):
        bpy.utils.unregister_class(item)


def launch_from_argv() -> None:
    """从命令行自动加载病例、模型和控制点。"""
    global _CONFIG, _EDITOR_PLAN_PATH, _EDITOR_PLAN_STALE, _EDITOR_PLAN_VALUE
    global _LAST_PREVIEW_OVERRIDES, _SESSION
    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(prog="twinguide ui")
    parser.add_argument("--config", required=True, type=Path)
    parsed = parser.parse_args(arguments)
    for object_ in tuple(bpy.data.objects):
        bpy.data.objects.remove(object_, do_unlink=True)
    _CONFIG = CaseConfig.from_yaml(parsed.config.resolve())
    _SESSION = EditorSession.create(_CONFIG.editor_overrides)
    _LAST_PREVIEW_OVERRIDES = _CONFIG.editor_overrides
    _EDITOR_PLAN_STALE = False
    register()
    configure_workspace()
    state = bpy.context.scene.twin_guide_state
    state.config_path = str(parsed.config.resolve())
    state.case_label = f"当前病例：{_CONFIG.case_id}"
    review = production_review_status(_CONFIG)
    state.review_status = "已确认" if review.confirmed else "待确认"
    model_path, model_snapshot_path, model_snapshot = _initial_model(
        parsed.config.resolve()
    )
    if model_path is not None:
        _load_model(model_path)
        if model_snapshot is not None:
            state.preview_status = "模型与当前参数一致"
        else:
            state.preview_status = "模型尚未按当前参数更新"
    _load_reference_surfaces(visible=model_path is None)
    if model_path is None:
        _load_input_fallback()
    cached_plan = plan_directory(_CONFIG) / "ui-editor-plan.json"
    valid_plan = model_snapshot is not None
    plan_value = model_snapshot
    if not valid_plan and cached_plan.is_file():
        try:
            plan_value = _load_editor_plan(cached_plan)
            valid_plan = (
                plan_value.get("schema_version") == EDITOR_PLAN_SCHEMA
                and plan_value.get("structure_fingerprint")
                == editor_plan_fingerprint(_CONFIG, parsed.config.resolve())
            )
        except (OSError, ValueError, json.JSONDecodeError):
            valid_plan = False
    if valid_plan:
        assert plan_value is not None
        _EDITOR_PLAN_VALUE = plan_value
        _EDITOR_PLAN_PATH = model_snapshot_path or cached_plan
        _create_controls()
        _show_model_view()
    else:
        _EDITOR_PLAN_VALUE = None
        _EDITOR_PLAN_PATH = None
        _EDITOR_PLAN_STALE = False
        state.preview_status = "等待编辑数据"
        _start_job("plan")
    bpy.context.scene["twinguide_ready"] = True


__all__ = ["launch_from_argv", "register", "unregister"]

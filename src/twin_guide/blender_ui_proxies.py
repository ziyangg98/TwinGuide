"""TwinGuide 可点击结构代理和轻量轮廓对象。"""

from __future__ import annotations

import math

import bpy
from mathutils import Vector

CONTROL_PREFIX = "TG_Control_"
OVERLAY_PREFIX = "TG_Overlay_"
SURFACE_PREFIX = "TG_Surface_"
LABEL_PREFIX = "TG_Label_"

GROUP_BY_KIND = {
    "sleeve_height": "SLEEVE",
    "window_center": "OPERATION",
    "window_size": "OPERATION",
    "window_margin": "OPERATION",
    "connector_node": "CONNECTOR",
    "surface_anchor": "PRESS",
    "junction": "PRESS",
    "observation_endpoint": "OBSERVATION",
    "observation_scalar": "OBSERVATION",
}

_COLORS = {
    "window_center": (0.18, 0.88, 0.68, 1.0),
    "window_size": (0.24, 0.78, 0.52, 1.0),
    "window_margin": (1.0, 0.46, 0.30, 1.0),
    "connector_node": (1.0, 0.66, 0.16, 1.0),
    "surface_anchor": (0.22, 0.82, 0.88, 1.0),
    "junction": (0.20, 0.72, 0.96, 1.0),
    "observation_endpoint": (0.76, 0.48, 1.0, 1.0),
    "observation_scalar": (0.62, 0.42, 0.94, 1.0),
    "sleeve_height": (0.30, 0.64, 1.0, 1.0),
}

_GROUP_COLORS = {
    "OPERATION": (0.18, 0.88, 0.68, 1.0),
    "CONNECTOR": (1.0, 0.66, 0.16, 1.0),
    "PRESS": (0.20, 0.76, 0.94, 1.0),
    "OBSERVATION": (0.72, 0.46, 1.0, 1.0),
    "SLEEVE": (0.30, 0.64, 1.0, 1.0),
}

_HANDLE_HINTS = {
    "window_center": "移动窗口",
    "window_size": {
        "width": "调宽度",
        "width_opposite": "调宽度",
        "height": "调高度",
        "height_opposite": "调高度",
    },
    "window_margin": {"front": "前部切除", "rear": "后部切除"},
    "connector_node": "避让节点",
    "surface_anchor": "支撑点",
    "junction": "汇合点",
    "observation_endpoint": {"start": "起点牙位", "end": "终点牙位"},
    "observation_scalar": {
        "drop": "下沉量",
        "height": "窗口高度",
        "sweep": "扫掠角",
    },
    "sleeve_height": {
        "closed": "底部高度",
        "platform": "平台高度",
        "total": "总高度",
    },
}


def _handle_hint(kind: str, properties: dict[str, object]) -> str:
    """返回当前手柄在界面中显示的动作名称。"""

    hint = _HANDLE_HINTS.get(kind, "")
    if isinstance(hint, dict):
        return hint.get(str(properties.get("role", "")), "")
    return str(hint)


def _feature_id(kind: str, properties: dict[str, object]) -> str:
    """由语义属性生成稳定结构编号。"""

    if kind == "sleeve_height":
        return f"sleeve:site_{properties['ring_index']}"
    if kind.startswith("window_"):
        return f"operation_window:{properties['site_index']}"
    if kind == "connector_node":
        return f"connector:guide_{properties['guide_index']}:{properties['side']}"
    if kind == "surface_anchor":
        index = str(properties["anchor_id"]).rsplit("_", 1)[-1]
        return f"press_anchor:{index}"
    if kind == "junction":
        return "press_junction"
    return f"observation_window:{properties['window_id']}"


def _mesh_data(
    kind: str,
) -> tuple[list[tuple[float, float, float]], list[tuple[int, int]], list[tuple[int, ...]]]:
    """返回一个结构手柄的本地网格。"""

    if kind == "sleeve_height":
        major_segments = 32
        minor_segments = 8
        major_radius = 0.80
        minor_radius = 0.22
        vertices = [
            (
                (major_radius + minor_radius * math.cos(minor * math.tau / minor_segments))
                * math.cos(major * math.tau / major_segments),
                (major_radius + minor_radius * math.cos(minor * math.tau / minor_segments))
                * math.sin(major * math.tau / major_segments),
                minor_radius * math.sin(minor * math.tau / minor_segments),
            )
            for major in range(major_segments)
            for minor in range(minor_segments)
        ]
        faces = [
            (
                major * minor_segments + minor,
                ((major + 1) % major_segments) * minor_segments + minor,
                ((major + 1) % major_segments) * minor_segments
                + (minor + 1) % minor_segments,
                major * minor_segments + (minor + 1) % minor_segments,
            )
            for major in range(major_segments)
            for minor in range(minor_segments)
        ]
        return vertices, [], faces
    if kind == "surface_anchor":
        vertices = [(0.0, 0.0, -0.9), (0.0, 0.0, 0.35)] + [
            (
                0.48 * math.cos(index * math.tau / 8),
                0.48 * math.sin(index * math.tau / 8),
                0.05,
            )
            for index in range(8)
        ]
        faces = [(0, 2 + index, 2 + (index + 1) % 8) for index in range(8)] + [
            (1, 2 + (index + 1) % 8, 2 + index) for index in range(8)
        ]
        return vertices, [], faces
    if kind == "window_size":
        size = 0.58
        vertices = [
            (x * size, y * size, z * size)
            for x, y, z in (
                (-1, -1, -1),
                (1, -1, -1),
                (1, 1, -1),
                (-1, 1, -1),
                (-1, -1, 1),
                (1, -1, 1),
                (1, 1, 1),
                (-1, 1, 1),
            )
        ]
        faces = [
            (0, 1, 2, 3),
            (4, 7, 6, 5),
            (0, 4, 5, 1),
            (1, 5, 6, 2),
            (2, 6, 7, 3),
            (4, 0, 3, 7),
        ]
        return vertices, [], faces
    if kind in {"window_center", "junction"}:
        size = 1.0 if kind == "window_center" else 0.85
        vertices = [
            (-size, 0.0, 0.0),
            (size, 0.0, 0.0),
            (0.0, -size, 0.0),
            (0.0, size, 0.0),
        ]
        return vertices, [(0, 1), (2, 3)], []
    if kind == "window_margin":
        vertices = [
            (0.0, 0.0, 0.95),
            (-0.55, -0.42, -0.55),
            (0.55, -0.42, -0.55),
            (0.0, 0.62, -0.55),
        ]
        return vertices, [], [(0, 1, 2), (0, 2, 3), (0, 3, 1), (1, 3, 2)]
    if kind == "observation_endpoint":
        vertices = [
            (0.0, 0.0, 0.18),
            (0.75, 0.0, 0.0),
            (0.0, 0.75, 0.0),
            (-0.75, 0.0, 0.0),
            (0.0, -0.75, 0.0),
            (0.0, 0.0, -0.18),
        ]
        return (
            vertices,
            [],
            [
                (0, 1, 2),
                (0, 2, 3),
                (0, 3, 4),
                (0, 4, 1),
                (5, 2, 1),
                (5, 3, 2),
                (5, 4, 3),
                (5, 1, 4),
            ],
        )
    vertices = [
        (0.0, 0.0, 0.86),
        (0.0, 0.0, -0.86),
        (0.74, 0.0, 0.0),
        (-0.74, 0.0, 0.0),
        (0.0, 0.74, 0.0),
        (0.0, -0.74, 0.0),
    ]
    faces = [
        (0, 2, 4),
        (0, 4, 3),
        (0, 3, 5),
        (0, 5, 2),
        (1, 4, 2),
        (1, 3, 4),
        (1, 5, 3),
        (1, 2, 5),
    ]
    return vertices, [], faces


def create_control(
    name: str,
    location: Vector,
    kind: str,
    properties: dict[str, object],
) -> bpy.types.Object:
    """建立可点击但不能接受普通 Blender 变换的结构代理。"""

    vertices, edges, faces = _mesh_data(kind)
    mesh = bpy.data.meshes.new(f"{CONTROL_PREFIX}{name}_Mesh")
    mesh.from_pydata(vertices, edges, faces)
    mesh.update()
    object_ = bpy.data.objects.new(f"{CONTROL_PREFIX}{name}", mesh)
    bpy.context.collection.objects.link(object_)
    object_.location = location
    object_.show_in_front = True
    object_.color = _COLORS[kind]
    for polygon in mesh.polygons:
        polygon.use_smooth = True
    object_["tg_kind"] = kind
    object_["tg_group"] = GROUP_BY_KIND[kind]
    object_["tg_feature_id"] = _feature_id(kind, properties)
    object_["tg_hint"] = _handle_hint(kind, properties)
    object_["tg_overview_visible"] = bool(
        kind
        in {
            "window_center",
            "connector_node",
            "surface_anchor",
            "junction",
            "observation_endpoint",
        }
        or (kind == "sleeve_height" and properties.get("role") == "platform")
    )
    for key, value in properties.items():
        object_[f"tg_{key}"] = value
    if kind == "sleeve_height":
        object_.rotation_mode = "QUATERNION"
        object_.rotation_quaternion = Vector(properties["axis"]).to_track_quat("Z", "Y")
        object_.color = {
            "closed": (0.20, 0.82, 0.64, 1.0),
            "platform": (1.0, 0.62, 0.18, 1.0),
            "total": (0.28, 0.58, 1.0, 1.0),
        }[str(properties["role"])]
    elif kind == "surface_anchor":
        object_.rotation_mode = "QUATERNION"
        object_.rotation_quaternion = Vector(properties["normal"]).to_track_quat("Z", "Y")
    object_["tg_base_color"] = list(object_.color)
    object_.hide_set(True)
    object_.lock_location = (True, True, True)
    object_.lock_rotation = (True, True, True)
    object_.lock_scale = (True, True, True)
    return object_


def create_curve(
    name: str,
    points: list[Vector],
    cyclic: bool,
) -> bpy.types.Object:
    """建立带稳定结构编号的轻量折线。"""

    data = bpy.data.curves.new(f"{OVERLAY_PREFIX}{name}", "CURVE")
    data.dimensions = "3D"
    data.bevel_depth = 0.12
    data.bevel_resolution = 4
    spline = data.splines.new("POLY")
    spline.points.add(len(points) - 1)
    for point, value in zip(spline.points, points, strict=True):
        point.co = (*value, 1.0)
    spline.use_cyclic_u = cyclic
    object_ = bpy.data.objects.new(f"{OVERLAY_PREFIX}{name}", data)
    bpy.context.collection.objects.link(object_)
    object_.show_in_front = True
    object_.color = _GROUP_COLORS["SLEEVE"]
    object_.lock_location = (True, True, True)
    object_.lock_rotation = (True, True, True)
    object_.lock_scale = (True, True, True)
    if name.startswith("Window_"):
        object_["tg_group"] = "OPERATION"
        object_["tg_feature_id"] = f"operation_window:{name.rsplit('_', 1)[-1]}"
        object_.color = _GROUP_COLORS["OPERATION"]
    elif name.startswith("Connector_"):
        object_["tg_group"] = "CONNECTOR"
        parts = name.removeprefix("Connector_").split("_")
        object_["tg_feature_id"] = f"connector:guide_{parts[0]}"
        object_.color = _GROUP_COLORS["CONNECTOR"]
    elif name.startswith("Observation_"):
        object_["tg_group"] = "OBSERVATION"
        identifier = name.removeprefix("Observation_")
        object_["tg_feature_id"] = f"observation_window:{identifier}"
        object_.color = _GROUP_COLORS["OBSERVATION"]
    elif name.startswith("Press_"):
        object_["tg_group"] = "PRESS"
        object_["tg_feature_id"] = "press_junction"
        object_.color = _GROUP_COLORS["PRESS"]
    if "tg_group" in object_:
        object_["tg_overview_visible"] = True
        object_["tg_base_color"] = list(object_.color)
        object_.hide_set(True)
    return object_


def create_fdi_label(
    window_id: str,
    role: str,
    fdi: int,
    location: Vector,
) -> bpy.types.Object:
    """建立观察窗端点的 FDI 标签代理。"""

    data = bpy.data.curves.new(f"{OVERLAY_PREFIX}FDI_{window_id}_{role}", "FONT")
    data.body = f"FDI {fdi}"
    data.size = 1.2
    data.align_x = "CENTER"
    object_ = bpy.data.objects.new(f"{OVERLAY_PREFIX}FDI_{window_id}_{role}", data)
    bpy.context.collection.objects.link(object_)
    object_.location = location + Vector((0.0, 0.0, 1.0))
    object_.show_in_front = True
    object_.color = _GROUP_COLORS["OBSERVATION"]
    object_["tg_group"] = "OBSERVATION"
    object_["tg_feature_id"] = f"observation_window:{window_id}"
    object_["tg_endpoint_role"] = role
    object_.hide_set(True)
    object_.lock_location = (True, True, True)
    object_.lock_rotation = (True, True, True)
    object_.lock_scale = (True, True, True)
    return object_


def create_hint_label(
    name: str,
    text: str,
    location: Vector,
    group: str,
    feature_id: str,
    color: tuple[float, float, float, float],
) -> bpy.types.Object:
    """建立可点击的三维文字提示。"""

    data = bpy.data.curves.new(f"{LABEL_PREFIX}{name}", "FONT")
    data.body = text
    data.size = 1.15
    data.extrude = 0.018
    data.bevel_depth = 0.008
    data.bevel_resolution = 2
    data.space_character = 1.08
    data.align_x = "CENTER"
    data.align_y = "CENTER"
    object_ = bpy.data.objects.new(f"{LABEL_PREFIX}{name}", data)
    bpy.context.collection.objects.link(object_)
    object_.location = location
    object_.show_in_front = True
    object_.color = color
    object_["tg_group"] = group
    object_["tg_feature_id"] = feature_id
    object_["tg_overview_visible"] = True
    object_["tg_hint_label"] = True
    object_["tg_base_color"] = list(color)
    object_.hide_set(False)
    object_.lock_location = (True, True, True)
    object_.lock_rotation = (True, True, True)
    object_.lock_scale = (True, True, True)
    return object_


__all__ = [
    "CONTROL_PREFIX",
    "LABEL_PREFIX",
    "OVERLAY_PREFIX",
    "SURFACE_PREFIX",
    "create_control",
    "create_curve",
    "create_fdi_label",
    "create_hint_label",
]

"""Blender 场景对象的创建、选择、复制和删除。"""

from __future__ import annotations

import bpy


def clear_scene() -> None:
    """删除当前 Blender 场景中的全部对象。"""

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def set_active_object(mesh_object: bpy.types.Object) -> None:
    """选中指定对象并将其设为活动对象。"""

    bpy.ops.object.select_all(action="DESELECT")
    mesh_object.select_set(True)
    bpy.context.view_layer.objects.active = mesh_object


def apply_object_transform(mesh_object: bpy.types.Object) -> None:
    """将对象的旋转和缩放应用到网格坐标。"""

    set_active_object(mesh_object)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)


def duplicate_mesh_object(mesh_object: bpy.types.Object, name: str) -> bpy.types.Object:
    """复制对象及其网格数据。"""

    duplicate = mesh_object.copy()
    duplicate.data = mesh_object.data.copy()
    duplicate.name = name
    bpy.context.collection.objects.link(duplicate)
    return duplicate


def remove_object(mesh_object: bpy.types.Object) -> None:
    """删除对象并解除它与所有集合的关联。"""

    bpy.data.objects.remove(mesh_object, do_unlink=True)

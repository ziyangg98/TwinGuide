"""导入和导出 STL 网格文件。"""

from __future__ import annotations

from pathlib import Path

import bpy

from twin_guide.blender.scene import set_active_object
from twin_guide.errors import MeshIOError


def import_stl_mesh(path: Path, name: str) -> bpy.types.Object:
    """导入且仅导入一个 STL 对象，并赋予固定名称。"""

    if not path.is_file():
        raise MeshIOError(f"STL 文件不存在：{path}")
    objects_before = set(bpy.context.scene.objects)
    try:
        bpy.ops.wm.stl_import(filepath=str(path))
    except Exception as error:
        raise MeshIOError(f"无法导入 STL {path}：{error}") from error
    created = [
        mesh_object
        for mesh_object in bpy.context.scene.objects
        if mesh_object not in objects_before
    ]
    if len(created) != 1:
        for mesh_object in created:
            bpy.data.objects.remove(mesh_object, do_unlink=True)
        raise MeshIOError(f"{path} 应只包含一个对象，实际导入 {len(created)} 个")
    created[0].name = name
    return created[0]


def export_stl_mesh(path: Path, mesh_object: bpy.types.Object) -> None:
    """将指定网格对象导出为 STL。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        set_active_object(mesh_object)
        bpy.ops.wm.stl_export(filepath=str(path), export_selected_objects=True)
    except Exception as error:
        raise MeshIOError(f"无法导出 STL {path}：{error}") from error

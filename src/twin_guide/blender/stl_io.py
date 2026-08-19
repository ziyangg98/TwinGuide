"""导入和导出 STL 网格文件。"""

from __future__ import annotations

from pathlib import Path

import bpy

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


def import_polygon_mesh(path: Path, name: str) -> bpy.types.Object:
    """按扩展名导入一个 STL 或保留索引拓扑的 PLY 网格对象。"""

    if path.suffix.lower() == ".stl":
        return import_stl_mesh(path, name)
    if path.suffix.lower() != ".ply":
        raise MeshIOError(f"只支持 STL 或 PLY 网格：{path}")
    if not path.is_file():
        raise MeshIOError(f"PLY 文件不存在：{path}")
    objects_before = set(bpy.context.scene.objects)
    try:
        bpy.ops.wm.ply_import(filepath=str(path))
    except Exception as error:
        raise MeshIOError(f"无法导入 PLY {path}：{error}") from error
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

    export_stl_meshes(path, (mesh_object,))


def export_stl_meshes(path: Path, mesh_objects: tuple[bpy.types.Object, ...]) -> None:
    """将多个独立网格合并写入一个 STL，不执行昂贵的布尔融合。"""

    if not mesh_objects:
        raise MeshIOError("导出 STL 至少需要一个网格对象")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        bpy.ops.object.select_all(action="DESELECT")
        for mesh_object in mesh_objects:
            mesh_object.select_set(True)
        bpy.context.view_layer.objects.active = mesh_objects[0]
        bpy.ops.wm.stl_export(filepath=str(path), export_selected_objects=True)
    except Exception as error:
        raise MeshIOError(f"无法导出 STL {path}：{error}") from error

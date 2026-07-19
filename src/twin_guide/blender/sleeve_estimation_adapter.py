"""将 Blender 网格对象转换为无副作用的参数估计数据。"""

from __future__ import annotations

import bpy

from twin_guide.geometry import Vec3
from twin_guide.sleeve_estimation.types import TriangleMeshData


def mesh_object_to_triangle_data(
    mesh_object: bpy.types.Object,
    *,
    evaluated: bool = True,
    world_space: bool = True,
) -> TriangleMeshData:
    """返回三角化网格快照，不改变对象或场景状态。

    ``evaluated=True`` 时包含修改器结果；``world_space=True`` 时使用
    对象的世界矩阵转换坐标。函数返回前始终释放 Blender 临时求值网格。
    """

    if mesh_object.type != "MESH":
        raise TypeError(f"需要网格对象，实际类型为 {mesh_object.type!r}")

    source = mesh_object
    owns_temporary_mesh = False
    if evaluated:
        source = mesh_object.evaluated_get(bpy.context.evaluated_depsgraph_get())
        blender_mesh = source.to_mesh(preserve_all_data_layers=False, depsgraph=None)
        owns_temporary_mesh = True
    else:
        blender_mesh = source.data

    try:
        blender_mesh.calc_loop_triangles()
        transform = source.matrix_world if world_space else None
        vertices = tuple(
            Vec3(float(point.x), float(point.y), float(point.z))
            for vertex in blender_mesh.vertices
            for point in ((transform @ vertex.co) if transform is not None else vertex.co,)
        )
        faces = tuple(
            tuple(int(index) for index in triangle.vertices)
            for triangle in blender_mesh.loop_triangles
        )
        return TriangleMeshData(vertices=vertices, faces=faces)
    finally:
        if owns_temporary_mesh:
            source.to_mesh_clear()

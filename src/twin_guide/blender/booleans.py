"""不改变输入对象的 Blender 布尔运算，并支持求解器回退。"""

from __future__ import annotations

from typing import Literal

import bpy

from twin_guide.blender.scene import duplicate_mesh_object, remove_object, set_active_object
from twin_guide.errors import BooleanOperationError

BooleanOperation = Literal["UNION", "DIFFERENCE"]
BOOLEAN_SOLVERS = ("EXACT", "MANIFOLD", "FLOAT")


def _apply_modifier(
    target_mesh: bpy.types.Object,
    cutter_mesh: bpy.types.Object,
    operation: BooleanOperation,
    solver: str,
) -> None:
    """用指定求解器将一次 Blender 布尔修改器应用到目标网格。"""

    modifier = target_mesh.modifiers.new(f"{operation.lower()}_{cutter_mesh.name}", "BOOLEAN")
    modifier.operation = operation
    modifier.solver = solver
    if hasattr(modifier, "use_hole_tolerant"):
        modifier.use_hole_tolerant = True
    modifier.object = cutter_mesh
    set_active_object(target_mesh)
    bpy.ops.object.modifier_apply(modifier=modifier.name)


def apply_boolean(
    target_mesh: bpy.types.Object,
    cutter_mesh: bpy.types.Object,
    operation: BooleanOperation,
) -> bpy.types.Object:
    """在副本上尝试布尔运算，并保留最后一个求解器异常。"""

    last_error: Exception | None = None
    for solver in BOOLEAN_SOLVERS:
        candidate = duplicate_mesh_object(
            target_mesh, f"{target_mesh.name}_{operation.lower()}_{solver.lower()}"
        )
        try:
            _apply_modifier(candidate, cutter_mesh, operation, solver)
            if candidate.data.vertices and candidate.data.polygons:
                remove_object(target_mesh)
                return candidate
            last_error = BooleanOperationError(f"求解器 {solver} 返回了空网格")
        except Exception as error:
            last_error = error
        remove_object(candidate)
    message = f"对 {target_mesh.name} 和 {cutter_mesh.name} 执行 {operation} 布尔运算失败"
    if last_error is None:
        raise BooleanOperationError(message)
    raise BooleanOperationError(message) from last_error


def subtract_cutters(
    target_mesh: bpy.types.Object,
    cutter_meshes: tuple[bpy.types.Object, ...],
) -> bpy.types.Object:
    """按给定顺序从目标网格中逐个扣除切割体。"""

    result = target_mesh
    for cutter_mesh in cutter_meshes:
        result = apply_boolean(result, cutter_mesh, "DIFFERENCE")
    return result

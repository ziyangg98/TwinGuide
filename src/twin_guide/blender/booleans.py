"""不改变输入对象的确定性 Blender 布尔运算。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

import bpy

if TYPE_CHECKING:
    import trimesh

from twin_guide.blender.scene import duplicate_mesh_object, remove_object, set_active_object
from twin_guide.errors import BooleanOperationError

BooleanOperation = Literal["UNION", "DIFFERENCE"]
BOOLEAN_SOLVER = "MANIFOLD"
MANIFOLD_OUTPUT_WELD_TOLERANCE_MM = 1e-6
MANIFOLD_OUTPUT_COLLAPSE_LIMIT_MM = 0.02
MANIFOLD_SIMPLIFY_TOLERANCE_MM = 0.005
MANIFOLD_CUTTER_CLEARANCE_MM = 0.020
MANIFOLD_CUTTER_LOCALIZATION_MARGIN_MM = 0.10


def _to_trimesh(
    mesh_object: bpy.types.Object,
    *,
    process: bool = True,
) -> trimesh.Trimesh:
    """将 Blender 对象转换为 Trimesh，并可为 cutter 保留索引拓扑。"""

    import numpy as np
    import trimesh

    mesh_object.data.calc_loop_triangles()
    vertices = np.empty(len(mesh_object.data.vertices) * 3, dtype=np.float32)
    mesh_object.data.vertices.foreach_get("co", vertices)
    vertices = vertices.reshape((-1, 3)).astype(np.float64, copy=False)
    world_matrix = np.asarray(mesh_object.matrix_world, dtype=np.float64)
    vertices = vertices @ world_matrix[:3, :3].T + world_matrix[:3, 3]
    faces = np.empty(len(mesh_object.data.loop_triangles) * 3, dtype=np.int32)
    mesh_object.data.loop_triangles.foreach_get("vertices", faces)
    faces = faces.reshape((-1, 3))
    return trimesh.Trimesh(vertices=vertices, faces=faces, process=process)


def _remove_invalid_faces(mesh: trimesh.Trimesh) -> None:
    """删除坐标量化后塌缩或重复的三角面。"""

    mesh.update_faces(mesh.unique_faces() & mesh.nondegenerate_faces())
    mesh.remove_unreferenced_vertices()


def _weld_manifold_output(
    mesh: trimesh.Trimesh,
    collapse_limit_mm: float = MANIFOLD_OUTPUT_COLLAPSE_LIMIT_MM,
) -> trimesh.Trimesh:
    """焊接 manifold3d 输出中的亚微米重合点并封闭少量短坏边。"""

    import numpy as np
    from scipy.spatial import cKDTree

    mesh.process(validate=True)
    vertices = mesh.vertices.copy()
    parents = np.arange(len(vertices))

    def find(index: int) -> int:
        """返回并压缩指定顶点所在的并查集根节点。"""

        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = int(parents[index])
        return index

    for first, second in cKDTree(vertices).query_pairs(MANIFOLD_OUTPUT_WELD_TOLERANCE_MM):
        first_root = find(first)
        second_root = find(second)
        if first_root != second_root:
            parents[second_root] = first_root
    roots = np.array([find(index) for index in range(len(vertices))])
    _, inverse = np.unique(roots, return_inverse=True)
    welded_vertices = np.zeros((int(inverse.max()) + 1, 3), dtype=np.float64)
    np.add.at(welded_vertices, inverse, vertices)
    welded_vertices /= np.bincount(inverse)[:, None]
    mesh.vertices = welded_vertices
    mesh.faces = inverse[mesh.faces]
    _remove_invalid_faces(mesh)

    for _ in range(64):
        edge_counts = np.bincount(
            mesh.edges_unique_inverse,
            minlength=len(mesh.edges_unique),
        )
        boundary_edges = mesh.edges_unique[edge_counts == 1]
        overused_edges = mesh.edges_unique[edge_counts > 2]
        if len(boundary_edges) == 0 and len(overused_edges) == 0 and mesh.is_volume:
            return mesh
        bad_edges = np.vstack((boundary_edges, overused_edges))
        if len(bad_edges) == 0:
            break
        lengths = np.linalg.norm(
            mesh.vertices[bad_edges[:, 0]] - mesh.vertices[bad_edges[:, 1]],
            axis=1,
        )
        shortest_index = int(np.argmin(lengths))
        if float(lengths[shortest_index]) > collapse_limit_mm:
            break
        first, second = (int(index) for index in bad_edges[shortest_index])
        mesh.vertices[first] = (mesh.vertices[first] + mesh.vertices[second]) / 2.0
        mesh.faces[mesh.faces == second] = first
        _remove_invalid_faces(mesh)

    edge_counts = np.bincount(
        mesh.edges_unique_inverse,
        minlength=len(mesh.edges_unique),
    )
    boundary_edges = mesh.edges_unique[edge_counts == 1]
    overused_edges = mesh.edges_unique[edge_counts > 2]
    bad_edges = np.vstack((boundary_edges, overused_edges))
    if len(boundary_edges) == 0 and 0 < len(overused_edges) <= 8:
        # STL 不保留共享顶点索引；差集交线附近偶尔会序列化出一个附着在
        # 正常封闭表面上的冗余薄片面。逐面试删，只接受删除后恢复为体积
        # 的候选，避免为较长坏边放宽几何折叠阈值。
        incident_faces = sorted(
            {
                int(face_index)
                for edge in overused_edges
                for face_index in np.flatnonzero(np.sum(np.isin(mesh.faces, edge), axis=1) == 2)
            }
        )
        valid_candidates = []
        face_areas = mesh.area_faces
        for face_index in incident_faces:
            candidate = mesh.copy()
            keep_faces = np.ones(len(candidate.faces), dtype=bool)
            keep_faces[face_index] = False
            candidate.update_faces(keep_faces)
            candidate.remove_unreferenced_vertices()
            if candidate.is_volume:
                valid_candidates.append((float(face_areas[face_index]), face_index, candidate))
        if valid_candidates:
            return min(valid_candidates, key=lambda item: (item[0], item[1]))[2]

    if 0 < len(bad_edges) <= 8:
        incident_face_sets = []
        for edge in bad_edges:
            incident_face_sets.append(
                set(np.flatnonzero(np.sum(np.isin(mesh.faces, edge), axis=1) == 2).tolist())
            )
        common_faces = set.intersection(*incident_face_sets)
        if len(common_faces) == 1:
            candidate = mesh.copy()
            keep_faces = np.ones(len(candidate.faces), dtype=bool)
            keep_faces[next(iter(common_faces))] = False
            candidate.update_faces(keep_faces)
            candidate.remove_unreferenced_vertices()
            if candidate.is_volume:
                return candidate

    edge_counts = np.bincount(
        mesh.edges_unique_inverse,
        minlength=len(mesh.edges_unique),
    )
    boundary_count = int(np.count_nonzero(edge_counts == 1))
    overused_edges = mesh.edges_unique[edge_counts > 2]
    shortest_overused_length = (
        float(
            np.min(
                np.linalg.norm(
                    mesh.vertices[overused_edges[:, 0]] - mesh.vertices[overused_edges[:, 1]],
                    axis=1,
                )
            )
        )
        if len(overused_edges)
        else None
    )
    raise BooleanOperationError(
        "manifold3d 差集输出无法在局部偏差阈值内恢复为封闭体："
        f"boundary={boundary_count}, overused={len(overused_edges)}, "
        f"shortest_overused_mm={shortest_overused_length}"
    )


def apply_manifold3d_difference(
    target_mesh: bpy.types.Object,
    cutter_mesh: bpy.types.Object,
    *,
    cutter_clearance_mm: float = MANIFOLD_CUTTER_CLEARANCE_MM,
    simplify_tolerance_mm: float = MANIFOLD_SIMPLIFY_TOLERANCE_MM,
    conservative_clearance_kernel: bool = False,
) -> bpy.types.Object:
    """用 manifold3d 后端执行一次差集并返回封闭的 Blender 三角网格。"""

    return apply_manifold3d_differences(
        target_mesh,
        (cutter_mesh,),
        cutter_clearance_mm=cutter_clearance_mm,
        simplify_tolerance_mm=simplify_tolerance_mm,
        conservative_clearance_kernel=conservative_clearance_kernel,
    )


def apply_manifold3d_differences(
    target_mesh: bpy.types.Object,
    cutter_meshes: tuple[bpy.types.Object, ...],
    *,
    cutter_clearance_mm: float = MANIFOLD_CUTTER_CLEARANCE_MM,
    simplify_tolerance_mm: float = MANIFOLD_SIMPLIFY_TOLERANCE_MM,
    conservative_clearance_kernel: bool = False,
    validate_inputs: bool = True,
    validate_result: bool = True,
) -> bpy.types.Object:
    """在一次 manifold 会话中连续扣除多个 cutter。

    中间差集不转换回 Blender 浮点网格，避免微米级序列化坏边使下一次
    manifold 差集误判输入不是封闭体。
    """

    import numpy as np
    import trimesh
    from manifold3d import Manifold, Mesh

    if not cutter_meshes:
        raise ValueError("manifold3d 多切割体差集至少需要一个 cutter")
    if cutter_clearance_mm < 0.0:
        raise ValueError("manifold3d 切割体外扩量不得为负")
    if simplify_tolerance_mm < 0.0:
        raise ValueError("manifold3d 简化公差不得为负")
    try:
        # manifold3d 的输出在 Blender 中保留了有效的共享顶点索引。优先
        # 原样复用这些索引；process=True 可能把仅在边界接触的闭合壳层
        # 合并成非流形公共边，反而使下一次连续差集误判目标无效。
        target = _to_trimesh(target_mesh, process=False)
        if validate_inputs and not target.is_volume:
            # 对来自普通 Blender/体素对象且尚未焊接的输入保留旧的整理回退。
            target = _to_trimesh(target_mesh, process=True)
        if validate_inputs and not target.is_volume:
            raise ValueError("参与差集的目标网格不是封闭有效体")
        result_manifold = Manifold(
            mesh=Mesh(
                vert_properties=np.asarray(target.vertices, dtype=np.float32),
                tri_verts=np.asarray(target.faces, dtype=np.uint32),
            )
        )
        for cutter_mesh in cutter_meshes:
            # 轴扫掠 cutter 可能由多个仅在边界接触的闭合体组成。PLY 中的
            # 独立索引使其整体有效；合并同坐标顶点反而会制造非流形公共边。
            cutter = _to_trimesh(cutter_mesh, process=False)
            if validate_inputs and not cutter.is_volume:
                raise ValueError(f"切割体 {cutter_mesh.name} 不是封闭有效体")
            cutter_manifold = Manifold(
                mesh=Mesh(
                    vert_properties=np.asarray(cutter.vertices, dtype=np.float32),
                    tri_verts=np.asarray(cutter.faces, dtype=np.uint32),
                )
            )
            expanded_cutter = cutter_manifold
            if cutter_clearance_mm > 0.0:
                # Minkowski sum 的成本由 cutter 全部面数决定，而差集只受
                # 目标包围盒附近的 cutter 影响。先用封闭包围盒局部化 cutter；
                # 裁剪边界与目标相距至少一个外扩半径和固定余量，
                # 因此人工封口的外扩不可能触及目标。
                bounds = result_manifold.bounding_box()
                padding = 2.0 * cutter_clearance_mm + MANIFOLD_CUTTER_LOCALIZATION_MARGIN_MM
                minimum = tuple(float(bounds[index]) - padding for index in range(3))
                size = tuple(
                    float(bounds[index + 3] - bounds[index]) + 2.0 * padding for index in range(3)
                )
                local_box = Manifold.cube(size).translate(minimum)
                localized_cutter = cutter_manifold ^ local_box
                if conservative_clearance_kernel:
                    # 轴对齐立方体是半径 r 欧氏球的外包络：任意方向的
                    # 支撑距离都不小于 r，不会像低面数内接球那样欠切。
                    kernel = Manifold.cube((2.0 * cutter_clearance_mm,) * 3).translate(
                        (-cutter_clearance_mm,) * 3
                    )
                else:
                    kernel = Manifold.sphere(cutter_clearance_mm, circular_segments=4)
                expanded_cutter = localized_cutter.minkowski_sum(kernel)
            result_manifold -= expanded_cutter
        output_manifold = (
            result_manifold.simplify(simplify_tolerance_mm)
            if simplify_tolerance_mm > 0.0
            else result_manifold
        )
        result_mesh = output_manifold.to_mesh()
        result = trimesh.Trimesh(
            vertices=result_mesh.vert_properties,
            faces=result_mesh.tri_verts,
            process=False,
        )
    except Exception as error:
        cutter_names = ", ".join(cutter.name for cutter in cutter_meshes)
        raise BooleanOperationError(
            f"对 {target_mesh.name} 和 [{cutter_names}] 执行 manifold3d 差集失败"
        ) from error
    if result is None or result.is_empty or (validate_result and not result.is_volume):
        raise BooleanOperationError(
            f"对 {target_mesh.name} 执行 manifold3d 多切割体差集未返回封闭体"
        )

    result_data = bpy.data.meshes.new(f"{target_mesh.name}_differences_manifold3d_data")
    result_data.from_pydata(result.vertices.tolist(), [], result.faces.tolist())
    result_data.validate(clean_customdata=True)
    result_data.update()
    result_object = bpy.data.objects.new(
        f"{target_mesh.name}_differences_manifold3d",
        result_data,
    )
    bpy.context.collection.objects.link(result_object)
    for material in target_mesh.data.materials:
        result_data.materials.append(material)
    remove_object(target_mesh)
    return result_object


def repair_manifold3d_stl(
    path: Path,
    collapse_limit_mm: float = MANIFOLD_OUTPUT_COLLAPSE_LIMIT_MM,
) -> None:
    """在指定局部偏差阈值内修复 STL 序列化坏边并原位验收。"""

    import trimesh

    if collapse_limit_mm <= 0.0:
        raise ValueError("STL 局部坏边折叠阈值必须为正")
    repaired = _weld_manifold_output(
        trimesh.load_mesh(path, process=True),
        collapse_limit_mm,
    )
    repaired.export(path)
    reloaded = trimesh.load_mesh(path, process=True)
    if not reloaded.is_volume or reloaded.body_count != 1:
        raise BooleanOperationError(f"manifold3d STL 修复后验收失败：{path}")


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
    modifier_name = modifier.name
    bpy.ops.object.modifier_apply(modifier=modifier_name)
    if target_mesh.modifiers.get(modifier_name) is not None:
        target_mesh.modifiers.remove(target_mesh.modifiers[modifier_name])
        raise BooleanOperationError(f"求解器 {solver} 未应用布尔修改器")


def apply_boolean(
    target_mesh: bpy.types.Object,
    cutter_mesh: bpy.types.Object,
    operation: BooleanOperation,
    solver: str = BOOLEAN_SOLVER,
) -> bpy.types.Object:
    """在副本上用指定求解器执行一次布尔运算。"""

    candidate = duplicate_mesh_object(
        target_mesh, f"{target_mesh.name}_{operation.lower()}_{solver.lower()}"
    )
    try:
        _apply_modifier(candidate, cutter_mesh, operation, solver)
        if not candidate.data.vertices or not candidate.data.polygons:
            raise BooleanOperationError(f"求解器 {solver} 返回了空网格")
    except Exception as error:
        remove_object(candidate)
        raise BooleanOperationError(
            f"对 {target_mesh.name} 和 {cutter_mesh.name} 执行 "
            f"{operation} 布尔运算失败（求解器：{solver}）"
        ) from error
    remove_object(target_mesh)
    return candidate


def subtract_cutters(
    target_mesh: bpy.types.Object,
    cutter_meshes: tuple[bpy.types.Object, ...],
    solver: str = BOOLEAN_SOLVER,
) -> bpy.types.Object:
    """按给定顺序从目标网格中逐个扣除切割体。"""

    result = target_mesh
    for cutter_mesh in cutter_meshes:
        result = apply_boolean(result, cutter_mesh, "DIFFERENCE", solver)
    return result

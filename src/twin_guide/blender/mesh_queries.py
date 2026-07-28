"""采样网格表面，并计算空间与拓扑指标。"""

from __future__ import annotations

import bisect
import math

import bmesh
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from twin_guide.blender.scene import set_active_object
from twin_guide.errors import GeometryError
from twin_guide.geometry import Vec3
from twin_guide.models import SurfaceSample

INSIDE_TEST_DIRECTIONS = (
    Vec3(1.0, 0.173, 0.347),
    Vec3(0.241, 1.0, 0.419),
    Vec3(0.311, 0.257, 1.0),
)


def to_vec3(vector: Vector) -> Vec3:
    """将 Blender 向量转换为包内部向量。"""

    return Vec3(float(vector.x), float(vector.y), float(vector.z))


def to_blender_vector(vector: Vec3) -> Vector:
    """将包内部向量转换为 Blender 向量。"""

    return Vector(vector.as_tuple())


def sample_mesh_surface(
    mesh_object: bpy.types.Object, limit: int = 180_000
) -> tuple[SurfaceSample, ...]:
    """按三角形面积分层，返回确定性表面样本。"""

    if limit <= 0:
        raise ValueError("limit 必须为正数")
    world_matrix = mesh_object.matrix_world
    normal_matrix = world_matrix.to_3x3().inverted_safe().transposed()
    weighted_samples: list[tuple[float, SurfaceSample]] = []
    for polygon in mesh_object.data.polygons:
        vertex_indices = tuple(polygon.vertices)
        if len(vertex_indices) < 3:
            continue
        first = world_matrix @ mesh_object.data.vertices[vertex_indices[0]].co
        transformed_normal = (normal_matrix @ polygon.normal).normalized()
        for offset in range(1, len(vertex_indices) - 1):
            second = world_matrix @ mesh_object.data.vertices[vertex_indices[offset]].co
            third = world_matrix @ mesh_object.data.vertices[vertex_indices[offset + 1]].co
            area = (second - first).cross(third - first).length * 0.5
            if area <= 1e-12:
                continue
            centroid = (first + second + third) / 3.0
            weighted_samples.append(
                (
                    area,
                    SurfaceSample(to_vec3(centroid), to_vec3(transformed_normal), polygon.index),
                )
            )
    if not weighted_samples:
        return ()
    cumulative: list[float] = []
    total_area = 0.0
    for area, _ in weighted_samples:
        total_area += area
        cumulative.append(total_area)
    sample_count = min(limit, len(weighted_samples))
    targets = ((index + 0.5) * total_area / sample_count for index in range(sample_count))
    return tuple(
        weighted_samples[min(bisect.bisect_left(cumulative, target), len(weighted_samples) - 1)][1]
        for target in targets
    )


def mesh_points(mesh_object: bpy.types.Object, limit: int = 100_000) -> tuple[Vec3, ...]:
    """返回按面积分层采样的表面位置。"""

    return tuple(sample.position for sample in sample_mesh_surface(mesh_object, limit))


def mesh_triangles(mesh_object: bpy.types.Object) -> tuple[tuple[Vec3, Vec3, Vec3], ...]:
    """返回多边形网格在世界坐标中的三角形。"""

    world_matrix = mesh_object.matrix_world
    triangles: list[tuple[Vec3, Vec3, Vec3]] = []
    for polygon in mesh_object.data.polygons:
        indices = tuple(polygon.vertices)
        if len(indices) < 3:
            continue
        first = to_vec3(world_matrix @ mesh_object.data.vertices[indices[0]].co)
        for index in range(1, len(indices) - 1):
            second = to_vec3(world_matrix @ mesh_object.data.vertices[indices[index]].co)
            third = to_vec3(world_matrix @ mesh_object.data.vertices[indices[index + 1]].co)
            triangles.append((first, second, third))
    return tuple(triangles)


def separate_connected_components(mesh_object: bpy.types.Object) -> tuple[bpy.types.Object, ...]:
    """按连通性分离网格，并返回分离后的连通分量。"""

    objects_before = set(bpy.context.scene.objects)
    set_active_object(mesh_object)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.separate(type="LOOSE")
    bpy.ops.object.mode_set(mode="OBJECT")
    created = [
        candidate
        for candidate in bpy.context.scene.objects
        if candidate is mesh_object or candidate not in objects_before
    ]
    components = sorted(
        (candidate for candidate in created if candidate.type == "MESH"),
        key=lambda candidate: (-len(candidate.data.vertices), candidate.name),
    )
    for index, component in enumerate(components):
        component.name = f"guide_sleeve_component_{index:02d}"
    return tuple(components)


def mesh_bounds(mesh_object: bpy.types.Object) -> tuple[Vec3, Vec3]:
    """返回对象在世界坐标中的轴对齐包围盒。"""

    corners = [mesh_object.matrix_world @ Vector(corner) for corner in mesh_object.bound_box]
    lower = Vec3(
        min(corner.x for corner in corners),
        min(corner.y for corner in corners),
        min(corner.z for corner in corners),
    )
    upper = Vec3(
        max(corner.x for corner in corners),
        max(corner.y for corner in corners),
        max(corner.z for corner in corners),
    )
    return lower, upper


def build_bvh(mesh_object: bpy.types.Object) -> BVHTree:
    """根据求值后网格构建世界坐标 BVH。"""

    dependency_graph = bpy.context.evaluated_depsgraph_get()
    evaluated = mesh_object.evaluated_get(dependency_graph)
    mesh = evaluated.to_mesh()
    world_matrix = evaluated.matrix_world
    vertices = [world_matrix @ vertex.co for vertex in mesh.vertices]
    polygons = [tuple(polygon.vertices) for polygon in mesh.polygons]
    tree = BVHTree.FromPolygons(vertices, polygons, all_triangles=False)
    evaluated.to_mesh_clear()
    return tree


def build_local_aligned_bvh(
    mesh_object: bpy.types.Object,
    anchor: Vec3,
    normal: Vec3,
    tangent: Vec3,
    bitangent: Vec3,
    major_radius_mm: float,
    minor_radius_mm: float,
) -> BVHTree:
    """构建锚点附近与目标外法向同向的椭圆局部表面 BVH。"""

    dependency_graph = bpy.context.evaluated_depsgraph_get()
    evaluated = mesh_object.evaluated_get(dependency_graph)
    mesh = evaluated.to_mesh()
    world_matrix = evaluated.matrix_world
    normal_matrix = world_matrix.to_3x3().inverted_safe().transposed()
    vertices = [world_matrix @ vertex.co for vertex in mesh.vertices]
    polygons = []
    unit_normal = normal.normalized()
    unit_tangent = tangent.normalized()
    unit_bitangent = bitangent.normalized()
    for polygon in mesh.polygons:
        indices = tuple(polygon.vertices)
        if len(indices) < 3:
            continue
        center = sum(
            (vertices[index] for index in indices),
            Vector((0.0, 0.0, 0.0)),
        ) / len(indices)
        relative = to_vec3(center) - anchor
        u = relative.dot(unit_tangent)
        v = relative.dot(unit_bitangent)
        w = abs(relative.dot(unit_normal))
        ellipse = (
            (u / (major_radius_mm + 1.5)) ** 2
            + (v / (minor_radius_mm + 1.5)) ** 2
        )
        polygon_normal = to_vec3(normal_matrix @ polygon.normal).normalized()
        if ellipse <= 1.0 and w <= 3.0 and polygon_normal.dot(unit_normal) >= 0.15:
            polygons.append(indices)
    evaluated.to_mesh_clear()
    if len(polygons) < 20:
        raise GeometryError("按压梁贴合脚附近同向导板面数量不足 20")
    return BVHTree.FromPolygons(vertices, polygons, all_triangles=False)


def ray_cast_mesh(mesh_tree: BVHTree, ray_origin: Vec3, ray_direction: Vec3) -> Vec3 | None:
    """返回射线与网格的第一个交点。"""

    location, _, _, _ = mesh_tree.ray_cast(
        to_blender_vector(ray_origin), to_blender_vector(ray_direction.normalized())
    )
    if location is None:
        return None
    return to_vec3(location)


def nearest_mesh_distance(mesh_tree: BVHTree, point: Vec3) -> float:
    """返回指定点到网格表面的最短距离。"""

    location, _, _, distance = mesh_tree.find_nearest(to_blender_vector(point))
    return math.inf if location is None else float(distance)


def nearest_mesh_surface_side(mesh_tree: BVHTree, point: Vec3) -> float | None:
    """返回点相对最近有向表面的法向侧别。

    返回值为正表示点位于最近表面的外法向一侧，为负表示位于实体侧；
    找不到最近表面时返回 ``None``。该局部判据可辅助识别空腔中射线奇偶
    检查因三角交线产生的少量误投票。
    """

    query = to_blender_vector(point)
    location, normal, _, _ = mesh_tree.find_nearest(query)
    if location is None or normal is None:
        return None
    return float((query - location).dot(normal))


def point_inside_mesh(mesh_tree: BVHTree, point: Vec3) -> bool:
    """通过三条非共面射线的奇偶多数决判点是否位于网格内。"""

    nearest_location, _, _, nearest_distance = mesh_tree.find_nearest(to_blender_vector(point))
    if nearest_location is not None and nearest_distance is not None and nearest_distance <= 1e-6:
        return True
    votes = 0
    for direction_value in INSIDE_TEST_DIRECTIONS:
        direction = to_blender_vector(direction_value.normalized())
        origin = to_blender_vector(point)
        intersections = 0
        for _ in range(256):
            location, _, _, _ = mesh_tree.ray_cast(origin, direction)
            if location is None:
                break
            intersections += 1
            origin = location + direction * 1e-5
        votes += intersections % 2
    return votes >= 2


def mesh_overlap_pairs(
    first_mesh: bpy.types.Object, second_mesh: bpy.types.Object
) -> tuple[tuple[int, int], ...]:
    """返回两个对象之间相交的多边形对。"""

    return tuple(build_bvh(first_mesh).overlap(build_bvh(second_mesh)))


def _vertex_components(
    editable_mesh: bmesh.types.BMesh,
) -> tuple[set[bmesh.types.BMVert], ...]:
    """根据 BMesh 边邻接关系返回顶点连通分量。"""

    remaining_vertices = set(editable_mesh.verts)
    components = []
    while remaining_vertices:
        seed = remaining_vertices.pop()
        component = {seed}
        pending = [seed]
        while pending:
            current = pending.pop()
            for edge in current.link_edges:
                neighbor = edge.other_vert(current)
                if neighbor in remaining_vertices:
                    remaining_vertices.remove(neighbor)
                    component.add(neighbor)
                    pending.append(neighbor)
        components.append(component)
    return tuple(components)


def mesh_component_vertex_counts(mesh_object: bpy.types.Object) -> tuple[int, ...]:
    """按降序返回各连通分量的顶点数。"""

    editable_mesh = bmesh.new()
    editable_mesh.from_mesh(mesh_object.data)
    counts = tuple(len(component) for component in _vertex_components(editable_mesh))
    editable_mesh.free()
    return tuple(sorted(counts, reverse=True))


def remove_excess_components(
    mesh_object: bpy.types.Object, maximum_components: int
) -> bpy.types.Object:
    """至多保留指定数量的最大连通分量。"""

    if maximum_components < 1:
        raise ValueError("maximum_components 必须为正数")
    editable_mesh = bmesh.new()
    editable_mesh.from_mesh(mesh_object.data)
    components = sorted(_vertex_components(editable_mesh), key=len, reverse=True)
    discarded_vertices = [
        vertex for component in components[maximum_components:] for vertex in component
    ]
    if discarded_vertices and len(discarded_vertices) < len(editable_mesh.verts):
        bmesh.ops.delete(editable_mesh, geom=discarded_vertices, context="VERTS")
    editable_mesh.to_mesh(mesh_object.data)
    editable_mesh.free()
    mesh_object.data.update()
    return mesh_object


def topology_edge_counts(mesh_object: bpy.types.Object) -> tuple[int, int]:
    """返回边界边数和非流形边数。"""

    editable = bmesh.new()
    editable.from_mesh(mesh_object.data)
    boundary_edges = sum(len(edge.link_faces) == 1 for edge in editable.edges)
    non_manifold_edges = sum(not edge.is_manifold for edge in editable.edges)
    editable.free()
    return boundary_edges, non_manifold_edges


def duplicate_triangle_count(mesh_object: bpy.types.Object, precision: int = 7) -> int:
    """统计经坐标舍入后完全重合的三角形，避免误判微米级封闭折边。"""

    mesh_object.data.calc_loop_triangles()
    world_matrix = mesh_object.matrix_world
    seen: set[tuple[tuple[float, float, float], ...]] = set()
    duplicates = 0
    for triangle in mesh_object.data.loop_triangles:
        coordinates = tuple(
            sorted(
                tuple(
                    round(float(value), precision)
                    for value in world_matrix @ mesh_object.data.vertices[index].co
                )
                for index in triangle.vertices
            )
        )
        if coordinates in seen:
            duplicates += 1
        else:
            seen.add(coordinates)
    return duplicates


def clean_mesh(mesh_object: bpy.types.Object) -> bpy.types.Object:
    """清除退化几何、三角化面并重新计算法向。"""

    editable = bmesh.new()
    editable.from_mesh(mesh_object.data)
    bmesh.ops.remove_doubles(editable, verts=editable.verts, dist=1e-5)
    bmesh.ops.dissolve_degenerate(editable, dist=1e-6, edges=editable.edges)
    bmesh.ops.triangulate(editable, faces=editable.faces)
    bmesh.ops.recalc_face_normals(editable, faces=editable.faces)
    editable.to_mesh(mesh_object.data)
    editable.free()
    mesh_object.data.validate(clean_customdata=True)
    mesh_object.data.update()
    return mesh_object

"""将纯几何规格转换为 Blender 网格。"""

from __future__ import annotations

import bpy
from mathutils import Matrix

from twin_guide.blender.mesh_queries import clean_mesh, to_blender_vector
from twin_guide.blender.scene import (
    apply_object_transform,
    duplicate_mesh_object,
    set_active_object,
)
from twin_guide.errors import GeometryError
from twin_guide.geometry import Vec3
from twin_guide.models import WindowCutout


def assign_material(
    mesh_object: bpy.types.Object, material: bpy.types.Material | None
) -> bpy.types.Object:
    """指定材质时，清空原材质槽并设置新材质。"""

    if material is None:
        return mesh_object
    mesh_object.data.materials.clear()
    mesh_object.data.materials.append(material)
    return mesh_object


def create_axis_cylinder(
    name: str,
    start: Vec3,
    end: Vec3,
    radius_mm: float,
    material: bpy.types.Material | None = None,
    vertices: int = 96,
) -> bpy.types.Object:
    """在两个世界坐标点之间创建封闭圆柱体。"""

    direction = to_blender_vector(end - start)
    if direction.length < 1e-8:
        raise GeometryError(f"无法创建零长度圆柱体：{name}")
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=vertices,
        radius=radius_mm,
        depth=direction.length,
        location=to_blender_vector((start + end) / 2.0),
    )
    cylinder = bpy.context.object
    cylinder.name = name
    cylinder.rotation_euler = direction.to_track_quat("Z", "Y").to_euler()
    apply_object_transform(cylinder)
    return assign_material(cylinder, material)


def create_window_cutter(
    specification: WindowCutout, material: bpy.types.Material | None = None
) -> bpy.types.Object:
    """根据窗口几何规格创建定向圆角切割体。"""

    bpy.ops.mesh.primitive_cube_add(size=1.0, location=to_blender_vector(specification.center))
    cutter = bpy.context.object
    cutter.name = specification.name
    normal = to_blender_vector(specification.normal).normalized()
    tangent = to_blender_vector(specification.tangent).normalized()
    bitangent = normal.cross(tangent).normalized()
    cutter.matrix_world = (
        Matrix.Translation(to_blender_vector(specification.center))
        @ Matrix((tangent, bitangent, normal)).transposed().to_4x4()
    )
    cutter.dimensions = (
        specification.width_mm,
        specification.height_mm,
        specification.depth_mm,
    )
    apply_object_transform(cutter)
    bevel = cutter.modifiers.new("window_edge_rounding", "BEVEL")
    bevel.width = min(
        specification.corner_radius_mm,
        specification.width_mm * 0.2,
        specification.height_mm * 0.2,
    )
    bevel.segments = 6
    set_active_object(cutter)
    bpy.ops.object.modifier_apply(modifier=bevel.name)
    return assign_material(cutter, material)


def create_bezier_tube(
    name: str,
    controls: tuple[Vec3, Vec3, Vec3, Vec3],
    radius_mm: float,
    material: bpy.types.Material | None = None,
    resolution: int = 24,
) -> bpy.types.Object:
    """创建带圆形端盖的光滑三次 Bézier 连接管。"""

    start, first_control, second_control, end = controls
    curve = bpy.data.curves.new(name, "CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = resolution
    curve.bevel_depth = radius_mm
    curve.bevel_resolution = 10
    curve.fill_mode = "FULL"
    curve.use_fill_caps = True
    spline = curve.splines.new("BEZIER")
    spline.bezier_points.add(1)
    first, second = spline.bezier_points
    first.co = start.as_tuple()
    first.handle_left_type = "FREE"
    first.handle_right_type = "FREE"
    first.handle_left = start.as_tuple()
    first.handle_right = first_control.as_tuple()
    second.co = end.as_tuple()
    second.handle_left_type = "FREE"
    second.handle_right_type = "FREE"
    second.handle_left = second_control.as_tuple()
    second.handle_right = end.as_tuple()
    tube = bpy.data.objects.new(name, curve)
    bpy.context.collection.objects.link(tube)
    set_active_object(tube)
    bpy.ops.object.convert(target="MESH")
    mesh_object = bpy.context.object
    mesh_object.name = name
    clean_mesh(mesh_object)
    return assign_material(mesh_object, material)


def voxel_union(
    mesh_objects: tuple[bpy.types.Object, ...],
    name: str,
    voxel_size_mm: float,
    material: bpy.types.Material | None = None,
) -> bpy.types.Object:
    """在副本上执行体素融合，不修改输入对象。"""

    if not mesh_objects:
        raise GeometryError("体素融合至少需要一个网格")
    duplicates = tuple(
        duplicate_mesh_object(mesh_object, f"{name}_source_{index}")
        for index, mesh_object in enumerate(mesh_objects)
    )
    bpy.ops.object.select_all(action="DESELECT")
    for duplicate in duplicates:
        duplicate.hide_set(False)
        duplicate.select_set(True)
    bpy.context.view_layer.objects.active = duplicates[0]
    bpy.ops.object.join()
    result = bpy.context.object
    result.name = name
    result.data.remesh_voxel_size = voxel_size_mm
    result.data.remesh_voxel_adaptivity = 0.0
    set_active_object(result)
    bpy.ops.object.voxel_remesh()
    return assign_material(result, material)

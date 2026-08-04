"""在 Blender 中根据几何参数重建封闭导管。"""

from __future__ import annotations

import math

import bpy
from mathutils import Matrix

from twin_guide.blender.sleeve_estimation_adapter import mesh_object_to_triangle_data
from twin_guide.errors import GeometryError
from twin_guide.sleeve_estimation.mesh_integrity import inspect_triangle_mesh
from twin_guide.sleeve_estimation.types import SleeveEstimate


def validate_sleeve_boolean_parameters(estimate: SleeveEstimate) -> None:
    """拒绝会使布尔切割体为空或方向翻转的导管尺寸。"""

    numeric = {
        "height": estimate.height,
        "platform_height": estimate.platform_height,
        "closed_bore_height": estimate.closed_bore_height,
        "platform_width": estimate.platform_width,
        "inner_radius": estimate.inner_radius,
        "outer_radius": estimate.outer_radius,
        "inner_arc_angle": estimate.inner_arc_angle,
        "outer_arc_angle": estimate.outer_arc_angle,
    }
    non_finite = [name for name, value in numeric.items() if not math.isfinite(value)]
    if non_finite:
        raise GeometryError(f"导管参数中存在非有限值：{', '.join(non_finite)}")
    if not 0.0 < estimate.inner_radius < estimate.outer_radius:
        raise GeometryError("导管半径必须满足 0 < inner_radius < outer_radius")
    if not 0.0 < estimate.closed_bore_height < estimate.platform_height < estimate.height:
        raise GeometryError("导管高度必须满足 0 < closed_bore_height < platform_height < height")
    if estimate.platform_width <= 0.0:
        raise GeometryError("platform_width 必须为正数")
    for name, angle in (
        ("inner_arc_angle", estimate.inner_arc_angle),
        ("outer_arc_angle", estimate.outer_arc_angle),
    ):
        if not 0.0 < angle < 2.0 * math.pi:
            raise GeometryError(f"{name} 必须严格位于 0 与 2*pi 之间")
    if estimate.axis.length <= 1e-10:
        raise GeometryError("导管轴向不得为零向量")
    radial_direction = estimate.c_opening_direction - estimate.axis.normalized() * (
        estimate.c_opening_direction.dot(estimate.axis.normalized())
    )
    if radial_direction.length <= 1e-10:
        raise GeometryError("c_opening_direction 不得与导管轴向平行")

    inner_gap = 2.0 * math.pi - estimate.inner_arc_angle
    outer_gap = 2.0 * math.pi - estimate.outer_arc_angle
    inner_cut = estimate.inner_radius * math.cos(0.5 * inner_gap)
    outer_cut = estimate.outer_radius * math.cos(0.5 * outer_gap)
    common_cut = 0.5 * (inner_cut + outer_cut)
    if abs(common_cut) >= estimate.inner_radius:
        raise GeometryError("拟合圆弧端点无法形成正宽度固定孔侧槽")
    slot_half_width = math.sqrt(estimate.inner_radius**2 - common_cut**2)
    tolerance = max(1e-9, estimate.inner_radius * 1e-6)
    if slot_half_width <= tolerance:
        raise GeometryError("中段固定孔侧槽的数值宽度为零")
    if common_cut + estimate.platform_width <= common_cut + tolerance:
        raise GeometryError("平台外缘未超出固定孔侧槽起点")


def create_closed_sleeve_object(
    estimate: SleeveEstimate,
    name: str,
) -> bpy.types.Object:
    """根据导管参数创建单一连通的封闭实体。

    外形由圆弧主体和肩部下方的单侧平台组成；贯穿圆柱形成固定孔。
    上段和中段分别使用切割体形成 C 形开口和矩形侧槽，
    两个切割体均不进入下段封闭区域。
    """

    validate_sleeve_boolean_parameters(estimate)
    inner_gap = 2.0 * math.pi - estimate.inner_arc_angle
    outer_gap = 2.0 * math.pi - estimate.outer_arc_angle
    inner_cut = estimate.inner_radius * math.cos(0.5 * inner_gap)
    outer_cut = estimate.outer_radius * math.cos(0.5 * outer_gap)
    common_cut = 0.5 * (inner_cut + outer_cut)
    platform_edge = common_cut + estimate.platform_width
    z_platform = estimate.height - estimate.platform_height
    z_transition = estimate.height - estimate.closed_bore_height
    epsilon = max(1e-3, 0.002 * estimate.outer_radius)

    from manifold3d import Manifold

    body = Manifold.cylinder(
        estimate.height,
        estimate.outer_radius,
        circular_segments=128,
    )
    platform = Manifold.cube(
        (
            platform_edge,
            2.0 * estimate.outer_radius,
            estimate.height - z_platform,
        )
    ).translate((0.0, -estimate.outer_radius, z_platform))
    body += platform

    bore = Manifold.cylinder(
        estimate.height + 4.0 * epsilon,
        estimate.inner_radius,
        circular_segments=128,
    ).translate((0.0, 0.0, -2.0 * epsilon))
    body -= bore

    cutter_end = platform_edge + 2.0 * estimate.outer_radius
    upper_opening = Manifold.cube(
        (
            cutter_end - common_cut,
            4.0 * estimate.outer_radius,
            z_platform + 2.0 * epsilon,
        )
    ).translate((common_cut, -2.0 * estimate.outer_radius, -epsilon))
    body -= upper_opening

    slot_half_width = math.sqrt(max(0.0, estimate.inner_radius**2 - common_cut**2))
    middle_slot = Manifold.cube(
        (
            cutter_end - common_cut,
            2.0 * slot_half_width,
            z_transition - z_platform + 2.0 * epsilon,
        )
    ).translate((common_cut, -slot_half_width, z_platform - epsilon))
    body -= middle_slot
    if str(body.status()) != "Error.NoError":
        raise GeometryError(f"重建导管 {name!r} 的封闭体运算失败：{body.status()}")
    output = body.to_mesh()
    mesh = bpy.data.meshes.new(f"{name}_Mesh")
    mesh.from_pydata(output.vert_properties.tolist(), [], output.tri_verts.tolist())
    mesh.update()
    body_object = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(body_object)

    axis = estimate.axis.normalized()
    c_opening_direction = (
        estimate.c_opening_direction - axis * estimate.c_opening_direction.dot(axis)
    ).normalized()
    across = axis.cross(c_opening_direction).normalized()
    origin = estimate.axis_origin
    body_object.matrix_world = Matrix(
        (
            (c_opening_direction.x, across.x, axis.x, origin.x),
            (c_opening_direction.y, across.y, axis.y, origin.y),
            (c_opening_direction.z, across.z, axis.z, origin.z),
            (0.0, 0.0, 0.0, 1.0),
        )
    )
    integrity = inspect_triangle_mesh(mesh_object_to_triangle_data(body_object))
    if not integrity.valid:
        raise GeometryError(f"重建导管 {name!r} 未通过网格完整性检查：{integrity}")
    return body_object

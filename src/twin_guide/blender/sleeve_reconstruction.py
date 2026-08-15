"""在 Blender 中根据几何参数重建封闭导管。"""

from __future__ import annotations

import math

import bpy
from mathutils import Matrix

from twin_guide.blender.mesh_queries import clean_mesh
from twin_guide.blender.sleeve_estimation_adapter import mesh_object_to_triangle_data
from twin_guide.errors import GeometryError
from twin_guide.sleeve_estimation.c_opening import rounded_c_opening_slot_profile
from twin_guide.sleeve_estimation.mesh_integrity import inspect_triangle_mesh
from twin_guide.sleeve_estimation.types import SleeveEstimate


def validate_sleeve_boolean_parameters(estimate: SleeveEstimate) -> None:
    """校验导柱实体构造所需参数。"""

    numeric = {
        "height": estimate.height,
        "platform_height": estimate.platform_height,
        "closed_bore_height": estimate.closed_bore_height,
        "platform_slot_width": estimate.platform_slot_width,
        "platform_overhang": estimate.platform_overhang,
        "inner_radius": estimate.inner_radius,
        "outer_radius": estimate.outer_radius,
        "inner_arc_angle": estimate.inner_arc_angle,
        "outer_arc_angle": estimate.outer_arc_angle,
        "top_recess_depth": estimate.top_recess_depth,
    }
    if estimate.top_recess_radius is not None:
        numeric["top_recess_radius"] = estimate.top_recess_radius
    non_finite = [name for name, value in numeric.items() if not math.isfinite(value)]
    if non_finite:
        raise GeometryError(f"导管参数中存在非有限值：{', '.join(non_finite)}")
    if not 0.0 < estimate.inner_radius < estimate.outer_radius:
        raise GeometryError("导管半径必须满足 0 < inner_radius < outer_radius")
    if not 0.0 < estimate.closed_bore_height < estimate.platform_height < estimate.height:
        raise GeometryError("导管高度必须满足 0 < closed_bore_height < platform_height < height")
    if not 0.0 < estimate.platform_slot_width < 2.0 * estimate.outer_radius:
        raise GeometryError("platform_slot_width 必须小于导柱外径")
    if estimate.platform_overhang < 0.0:
        raise GeometryError("platform_overhang 不得小于 0")
    if estimate.top_recess_radius is None:
        if estimate.top_recess_depth != 0.0:
            raise GeometryError("top_recess_radius 与 top_recess_depth 必须同时启用")
    else:
        if not estimate.inner_radius < estimate.top_recess_radius < estimate.outer_radius:
            raise GeometryError(
                "顶部凹陷半径必须满足 inner_radius < top_recess_radius < outer_radius"
            )
        if not 0.0 < estimate.top_recess_depth < (estimate.height - estimate.platform_height):
            raise GeometryError("顶部凹陷深度必须小于顶部 C 口段高度")
    for name, angle in (
        ("inner_arc_angle", estimate.inner_arc_angle),
        ("outer_arc_angle", estimate.outer_arc_angle),
    ):
        if not 0.0 < angle < 2.0 * math.pi:
            raise GeometryError(f"{name} 必须严格位于 0 与 2*pi 之间")
    if not math.pi <= estimate.inner_arc_angle <= math.radians(350.0):
        raise GeometryError("inner_arc_angle 必须位于 180 与 350 度之间")
    if estimate.axis.length <= 1e-10:
        raise GeometryError("导管轴向不得为零向量")
    axis = estimate.axis.normalized()
    radial_direction = estimate.c_opening_direction - axis * (
        estimate.c_opening_direction.dot(axis)
    )
    if radial_direction.length <= 1e-10:
        raise GeometryError("c_opening_direction 不得与导管轴向平行")


def create_closed_sleeve_object(
    estimate: SleeveEstimate,
    name: str,
) -> bpy.types.Object:
    """创建带 C 口、平底段和可选顶部凹陷的封闭导柱。"""

    validate_sleeve_boolean_parameters(estimate)
    inner_gap = 2.0 * math.pi - estimate.inner_arc_angle
    outer_gap = 2.0 * math.pi - estimate.outer_arc_angle
    outer_cut = estimate.outer_radius * math.cos(0.5 * outer_gap)
    z_platform = estimate.height - estimate.platform_height
    z_transition = estimate.height - estimate.closed_bore_height
    epsilon = max(1e-3, 0.002 * estimate.outer_radius)

    from manifold3d import CrossSection, Manifold

    body = Manifold.cylinder(
        estimate.height,
        estimate.outer_radius,
        circular_segments=128,
    )
    platform = Manifold.cube(
        (
            estimate.outer_radius + estimate.platform_overhang,
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
    cutters = bore

    if estimate.top_recess_radius is not None:
        recess_lead = Manifold.cylinder(
            epsilon,
            estimate.top_recess_radius,
            circular_segments=128,
        ).translate((0.0, 0.0, -epsilon))
        recess_taper = Manifold.cylinder(
            estimate.top_recess_depth,
            estimate.top_recess_radius,
            estimate.inner_radius,
            circular_segments=128,
        )
        cutters += recess_lead + recess_taper

    cutter_end = 3.0 * estimate.outer_radius
    inner_opening_y = estimate.inner_radius * math.sin(0.5 * inner_gap)
    outer_clearance = Manifold.cube(
        (
            cutter_end - outer_cut,
            4.0 * estimate.outer_radius,
            z_platform + 2.0 * epsilon,
        )
    ).translate(
        (
            outer_cut,
            -2.0 * estimate.outer_radius,
            -epsilon,
        )
    )
    rounded_slot_points = rounded_c_opening_slot_profile(
        estimate.inner_radius,
        inner_opening_y,
        outer_cut,
        cutter_end,
        radial_overlap=2.0 * epsilon,
    )
    rounded_slot_profile = CrossSection([rounded_slot_points])
    rounded_slot = rounded_slot_profile.extrude(z_platform + 2.0 * epsilon).translate(
        (0.0, 0.0, -epsilon)
    )
    cutters += outer_clearance + rounded_slot

    slot_half_width = 0.5 * estimate.platform_slot_width
    middle_slot_height = z_transition - z_platform + 2.0 * epsilon
    if slot_half_width >= estimate.inner_radius:
        middle_slot = Manifold.cube(
            (
                cutter_end + 2.0 * epsilon,
                2.0 * slot_half_width,
                middle_slot_height,
            )
        ).translate((-2.0 * epsilon, -slot_half_width, z_platform - epsilon))
    else:
        middle_slot_points = rounded_c_opening_slot_profile(
            estimate.inner_radius,
            slot_half_width,
            outer_cut + epsilon,
            cutter_end,
            radial_overlap=2.0 * epsilon,
        )
        middle_slot_profile = CrossSection([middle_slot_points])
        middle_slot = middle_slot_profile.extrude(middle_slot_height).translate(
            (0.0, 0.0, z_platform - epsilon)
        )
    cutters += middle_slot
    body -= cutters
    if str(body.status()) != "Error.NoError":
        raise GeometryError(f"重建导管 {name!r} 的封闭体运算失败：{body.status()}")
    output = body.to_mesh()
    mesh = bpy.data.meshes.new(f"{name}_Mesh")
    mesh.from_pydata(output.vert_properties.tolist(), [], output.tri_verts.tolist())
    mesh.update()
    body_object = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(body_object)
    clean_mesh(body_object)

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

"""在 Blender 中根据几何参数重建封闭导套。"""

from __future__ import annotations

import math

import bpy
from mathutils import Matrix

from twin_guide.blender.sleeve_estimation_adapter import mesh_object_to_triangle_data
from twin_guide.errors import GeometryError
from twin_guide.sleeve_estimation.mesh_integrity import inspect_triangle_mesh
from twin_guide.sleeve_estimation.types import SleeveEstimate


def _activate(mesh_object: bpy.types.Object) -> None:
    """将网格设为 Blender 当前唯一选中的活动对象。"""

    bpy.ops.object.select_all(action="DESELECT")
    mesh_object.select_set(True)
    bpy.context.view_layer.objects.active = mesh_object


def _cube(
    name: str,
    center: tuple[float, float, float],
    size: tuple[float, float, float],
) -> bpy.types.Object:
    """创建指定中心和尺寸的立方体布尔辅助体。"""

    bpy.ops.mesh.primitive_cube_add(location=center)
    result = bpy.context.object
    result.name = name
    result.dimensions = size
    _activate(result)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return result


def _cylinder(
    name: str,
    radius: float,
    depth: float,
    axial_center: float,
) -> bpy.types.Object:
    """创建沿导套局部 Z 轴排列的圆柱布尔辅助体。"""

    bpy.ops.mesh.primitive_cylinder_add(
        vertices=128,
        radius=radius,
        depth=depth,
        location=(0.0, 0.0, axial_center),
    )
    result = bpy.context.object
    result.name = name
    return result


def _boolean(target: bpy.types.Object, operand: bpy.types.Object, operation: str) -> None:
    """对导套重建中间体执行布尔运算，并移除操作体。"""

    _activate(target)
    modifier = target.modifiers.new(f"{operation.lower()}_{operand.name}", "BOOLEAN")
    modifier.operation = operation
    modifier.solver = "EXACT"
    modifier.object = operand
    bpy.ops.object.modifier_apply(modifier=modifier.name)
    bpy.data.objects.remove(operand, do_unlink=True)


def validate_sleeve_boolean_parameters(estimate: SleeveEstimate) -> None:
    """拒绝会使布尔切割体为空或方向翻转的导套尺寸。"""

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
        raise GeometryError(f"导套参数中存在非有限值：{', '.join(non_finite)}")
    if not 0.0 < estimate.inner_radius < estimate.outer_radius:
        raise GeometryError("导套半径必须满足 0 < inner_radius < outer_radius")
    if not 0.0 < estimate.closed_bore_height < estimate.platform_height < estimate.height:
        raise GeometryError("导套高度必须满足 0 < closed_bore_height < platform_height < height")
    if estimate.platform_width <= 0.0:
        raise GeometryError("platform_width 必须为正数")
    for name, angle in (
        ("inner_arc_angle", estimate.inner_arc_angle),
        ("outer_arc_angle", estimate.outer_arc_angle),
    ):
        if not 0.0 < angle < 2.0 * math.pi:
            raise GeometryError(f"{name} 必须严格位于 0 与 2*pi 之间")
    if estimate.axis.length <= 1e-10:
        raise GeometryError("导套轴向不得为零向量")
    radial_direction = estimate.c_opening_direction - estimate.axis.normalized() * (
        estimate.c_opening_direction.dot(estimate.axis.normalized())
    )
    if radial_direction.length <= 1e-10:
        raise GeometryError("c_opening_direction 不得与导套轴向平行")

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
    """根据导套参数创建单一连通的封闭实体。

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

    body = _cylinder(name, estimate.outer_radius, estimate.height, 0.5 * estimate.height)
    platform = _cube(
        f"{name}_platform",
        (0.5 * platform_edge, 0.0, 0.5 * (z_platform + estimate.height)),
        (platform_edge, 2.0 * estimate.outer_radius, estimate.height - z_platform),
    )
    _boolean(body, platform, "UNION")

    bore = _cylinder(
        f"{name}_bore",
        estimate.inner_radius,
        estimate.height + 4.0 * epsilon,
        0.5 * estimate.height,
    )
    _boolean(body, bore, "DIFFERENCE")

    cutter_end = platform_edge + 2.0 * estimate.outer_radius
    upper_opening = _cube(
        f"{name}_upper_opening",
        (0.5 * (common_cut + cutter_end), 0.0, 0.5 * z_platform),
        (cutter_end - common_cut, 4.0 * estimate.outer_radius, z_platform + 2.0 * epsilon),
    )
    _boolean(body, upper_opening, "DIFFERENCE")

    slot_half_width = math.sqrt(max(0.0, estimate.inner_radius**2 - common_cut**2))
    middle_slot = _cube(
        f"{name}_middle_slot",
        (
            0.5 * (common_cut + cutter_end),
            0.0,
            0.5 * (z_platform + z_transition),
        ),
        (
            cutter_end - common_cut,
            2.0 * slot_half_width,
            z_transition - z_platform + 2.0 * epsilon,
        ),
    )
    _boolean(body, middle_slot, "DIFFERENCE")

    axis = estimate.axis.normalized()
    c_opening_direction = (
        estimate.c_opening_direction - axis * estimate.c_opening_direction.dot(axis)
    ).normalized()
    across = axis.cross(c_opening_direction).normalized()
    origin = estimate.axis_origin
    # 布尔基本体的轴向中心位于 H/2。替换 matrix_world 前先应用局部平移，
    # 否则导套将以 axis_origin 为中心跨越 [-H/2, H/2]。
    _activate(body)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    body.matrix_world = Matrix(
        (
            (c_opening_direction.x, across.x, axis.x, origin.x),
            (c_opening_direction.y, across.y, axis.y, origin.y),
            (c_opening_direction.z, across.z, axis.z, origin.z),
            (0.0, 0.0, 0.0, 1.0),
        )
    )
    body.name = name
    integrity = inspect_triangle_mesh(mesh_object_to_triangle_data(body))
    if not integrity.valid:
        raise GeometryError(f"重建导套 {name!r} 未通过网格完整性检查：{integrity}")
    return body

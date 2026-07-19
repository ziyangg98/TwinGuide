"""渲染诊断图和最终结果图。"""

from __future__ import annotations

from pathlib import Path

import bpy
from mathutils import Vector

from twin_guide.blender.mesh_queries import mesh_bounds
from twin_guide.config import RenderParameters

COLORS = {
    "final": (0.72, 0.74, 0.78, 1.0),
    "template": (0.30, 0.62, 0.86, 1.0),
    "sleeve": (0.58, 0.61, 0.66, 1.0),
    "sleeve_point": (0.90, 0.22, 0.12, 1.0),
    "template_point": (1.0, 0.76, 0.08, 1.0),
    "connector": (0.95, 0.55, 0.10, 1.0),
    "channel": (0.88, 0.38, 0.12, 0.42),
    "operation": (1.0, 0.30, 0.02, 0.45),
    "observation": (0.0, 0.85, 0.95, 0.48),
}

VIEW_DIRECTIONS = {
    "iso": Vector((0.6, -0.8, 0.55)),
    "top": Vector((0.0, 0.0, 1.0)),
    "bottom": Vector((0.0, 0.0, -1.0)),
    "side": Vector((1.0, 0.0, 0.1)),
}


def create_materials() -> dict[str, bpy.types.Material]:
    """创建诊断渲染所需的语义材质。"""

    materials: dict[str, bpy.types.Material] = {}
    for name, color in COLORS.items():
        material = bpy.data.materials.get(f"twin_guide_{name}")
        if material is None:
            material = bpy.data.materials.new(f"twin_guide_{name}")
        material.diffuse_color = color
        materials[name] = material
    return materials


def render_objects(
    path: Path,
    mesh_objects: tuple[bpy.types.Object, ...],
    parameters: RenderParameters,
    view: str = "iso",
) -> None:
    """使用自适应正交相机渲染指定对象。"""

    if not mesh_objects:
        raise ValueError("渲染至少需要一个对象")
    visibility = {mesh_object: mesh_object.hide_render for mesh_object in bpy.context.scene.objects}
    visible = set(mesh_objects)
    for mesh_object in bpy.context.scene.objects:
        mesh_object.hide_render = mesh_object not in visible
    bounds = tuple(mesh_bounds(mesh_object) for mesh_object in mesh_objects)
    lower = Vector(tuple(min(pair[0].as_tuple()[axis] for pair in bounds) for axis in range(3)))
    upper = Vector(tuple(max(pair[1].as_tuple()[axis] for pair in bounds) for axis in range(3)))
    center = (lower + upper) * 0.5
    scale = max(upper - lower)
    direction = VIEW_DIRECTIONS[view].normalized()
    bpy.ops.object.camera_add(location=center + direction * max(80.0, scale * 2.5))
    camera = bpy.context.object
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = max(scale * 1.28, 1.0)
    camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
    scene = bpy.context.scene
    scene.camera = camera
    scene.render.resolution_x = parameters.width_px
    scene.render.resolution_y = parameters.height_px
    scene.render.resolution_percentage = 100
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "MATERIAL"
    scene.display.shading.show_shadows = True
    scene.display.shading.show_cavity = True
    scene.display.shading.cavity_type = "WORLD"
    scene.display.shading.curvature_ridge_factor = 1.25
    scene.display.shading.curvature_valley_factor = 1.0
    scene.display.shading.background_type = "VIEWPORT"
    scene.display.shading.background_color = (0.94, 0.94, 0.94)
    scene.view_settings.view_transform = "Standard"
    path.parent.mkdir(parents=True, exist_ok=True)
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    bpy.data.objects.remove(camera, do_unlink=True)
    for mesh_object, was_hidden in visibility.items():
        if mesh_object.name in bpy.data.objects:
            mesh_object.hide_render = was_hidden

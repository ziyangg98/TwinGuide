"""在 Blender 中渲染参考模板圆环上平面和中心交点。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import bpy
import trimesh
from mathutils import Vector

from twin_guide.blender.rendering import render_objects
from twin_guide.blender.scene import clear_scene
from twin_guide.blender.stl_io import import_stl_mesh
from twin_guide.config import RenderParameters
from twin_guide.template_ring_estimation import (
    TemplateRingEstimate,
    TemplateRingTopPlaneEstimate,
    estimate_template_ring_top_plane,
    estimate_template_rings,
)


def _material(name: str, color: tuple[float, float, float, float]) -> bpy.types.Material:
    """创建用于检查图的纯色材质。"""

    material = bpy.data.materials.new(name)
    material.diffuse_color = color
    return material


def _add_cylinder(
    location: Vector,
    direction: Vector,
    depth: float,
    radius: float,
    material: bpy.types.Material,
) -> bpy.types.Object:
    """创建沿指定方向放置的标记圆柱。"""

    bpy.ops.mesh.primitive_cylinder_add(
        vertices=32,
        radius=radius,
        depth=depth,
        location=location,
    )
    cylinder = bpy.context.object
    cylinder.rotation_euler = direction.to_track_quat("Z", "Y").to_euler()
    cylinder.data.materials.append(material)
    return cylinder


def _highlight_top_plane(
    mesh_object: bpy.types.Object,
    ring: TemplateRingEstimate,
    top_plane: TemplateRingTopPlaneEstimate,
) -> Vector:
    """给识别平面赋红色材质，并返回其外向法向。"""

    ring_center = Vector(ring.center.as_tuple())
    axis = Vector(ring.axis.as_tuple()).normalized()
    selected_normals = []
    for polygon in mesh_object.data.polygons:
        centroid = sum(
            (mesh_object.data.vertices[index].co for index in polygon.vertices),
            Vector(),
        ) / len(polygon.vertices)
        relative = centroid - ring_center
        offset = relative.dot(axis)
        radial_distance = (relative - axis * offset).length
        if (
            abs(offset - top_plane.offset_from_ring_center_mm) < 0.006
            and 1.1 * ring.radius_mm <= radial_distance <= 2.5 * ring.radius_mm
            and abs(polygon.normal.dot(axis)) > 0.9999
        ):
            polygon.material_index = 1
            selected_normals.append(polygon.normal.copy())
    if not selected_normals:
        raise RuntimeError("没有可高亮的圆环上平面三角面")
    outward = sum(selected_normals, Vector()).normalized()
    return outward


def render_check(
    template_path: Path,
    output_path: Path,
    *,
    overview: bool = False,
    view: str = "iso",
) -> None:
    """估计一个参考模板，并输出圆环局部检查图。"""

    source_mesh = trimesh.load_mesh(template_path, process=True)
    rings = estimate_template_rings(source_mesh)
    top_planes = tuple(estimate_template_ring_top_plane(source_mesh, ring) for ring in rings)
    clear_scene()
    template = import_stl_mesh(template_path, "reference_template")
    base_material = _material("template_blue", (0.12, 0.40, 0.64, 1.0))
    plane_material = _material("identified_top_plane_red", (1.0, 0.03, 0.02, 1.0))
    marker_material = _material("center_cross_yellow", (1.0, 0.85, 0.0, 1.0))
    template.data.materials.append(base_material)
    template.data.materials.append(plane_material)
    marker_objects = []
    outward_directions = []
    for ring, top_plane in zip(rings, top_planes, strict=True):
        outward = _highlight_top_plane(template, ring, top_plane)
        outward_directions.append(outward)
        center = Vector(top_plane.center.as_tuple())
        axis = Vector(ring.axis.as_tuple()).normalized()
        tangent = Vector((1.0, 0.0, 0.0))
        if abs(tangent.dot(axis)) > 0.95:
            tangent = Vector((0.0, 1.0, 0.0))
        tangent = (tangent - axis * tangent.dot(axis)).normalized()
        bitangent = axis.cross(tangent).normalized()
        display_center = center + outward * 0.45
        bpy.ops.mesh.primitive_uv_sphere_add(
            segments=48,
            ring_count=24,
            radius=0.48,
            location=display_center,
        )
        sphere = bpy.context.object
        sphere.data.materials.append(marker_material)
        marker_objects.append(sphere)
        for direction in (tangent, bitangent):
            marker_objects.append(
                _add_cylinder(display_center, direction, 4.0, 0.14, marker_material)
            )
        if overview:
            marker_objects.append(_add_cylinder(center, axis, 10.0, 0.18, marker_material))

    if overview:
        render_objects(
            output_path,
            (template, *marker_objects),
            RenderParameters(width_px=1200, height_px=1000),
            view,
        )
        return

    center = Vector(top_planes[0].center.as_tuple())
    outward = outward_directions[0]

    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "MATERIAL"
    scene.display.shading.show_shadows = True
    scene.display.shading.show_cavity = True
    scene.display.shading.background_type = "VIEWPORT"
    scene.display.shading.background_color = (0.96, 0.96, 0.96)
    scene.view_settings.view_transform = "Standard"
    scene.render.resolution_x = 900
    scene.render.resolution_y = 760
    scene.render.resolution_percentage = 100
    bpy.ops.object.camera_add(location=center + outward * 35.0)
    camera = bpy.context.object
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = 13.0
    camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
    scene.camera = camera
    output_path.parent.mkdir(parents=True, exist_ok=True)
    scene.render.filepath = str(output_path)
    bpy.ops.render.render(write_still=True)


def main() -> None:
    """解析输入输出路径并渲染检查图。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--overview", action="store_true")
    parser.add_argument("--view", choices=("iso", "top", "bottom", "side"), default="iso")
    arguments = parser.parse_args(
        sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else None
    )
    render_check(
        arguments.template.resolve(),
        arguments.output.resolve(),
        overview=arguments.overview,
        view=arguments.view,
    )


if __name__ == "__main__":
    main()

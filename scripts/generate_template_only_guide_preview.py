"""只用传统模板和病例参数试生成一对双导导柱。"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import bpy
import numpy as np
import trimesh
import yaml

from twin_guide.blender.rendering import render_objects
from twin_guide.blender.scene import clear_scene, set_active_object
from twin_guide.blender.sleeve_reconstruction import create_closed_sleeve_object
from twin_guide.blender.stl_io import export_stl_mesh, import_stl_mesh
from twin_guide.config import CaseConfig, RenderParameters
from twin_guide.geometry import Vec3
from twin_guide.sleeve_estimation.types import SleeveEstimate
from twin_guide.template_ring_estimation import (
    estimate_template_ring_top_plane,
    estimate_template_rings,
)

GUIDE_D_FACE_SPACING_TOLERANCE_MM = 0.001


def _projected_direction(direction: Vec3, axis: Vec3) -> Vec3:
    """把病例左右方向投影到圆环横截面。"""

    projected = direction - axis * direction.dot(axis)
    return projected.normalized()


def _estimate_tooth_section_lateral(
    mesh: trimesh.Trimesh,
    ring_center: Vec3,
    ring_axis: Vec3,
    ring_radius_mm: float,
    ring_axial_span_mm: float,
    sign_reference: Vec3,
) -> tuple[Vec3, Vec3, int, float]:
    """由圆环内侧局部模板截面估计垂直于牙齿切面的方向。"""

    centers = np.asarray(mesh.triangles_center, dtype=float)
    center = np.asarray(ring_center.as_tuple(), dtype=float)
    axis = np.asarray(ring_axis.as_tuple(), dtype=float)
    relative = centers - center
    axial = relative @ axis
    planar = relative - np.outer(axial, axis)
    radial = np.linalg.norm(planar, axis=1)
    target_axial_mm = 0.5 * ring_axial_span_mm + 0.5
    selected = (
        (np.abs(axial - target_axial_mm) <= 0.3)
        & (radial >= ring_radius_mm + 0.7)
        & (radial <= ring_radius_mm + 5.7)
    )
    if int(selected.sum()) < 50:
        raise ValueError("圆环内侧局部截面支持不足，无法确定导柱旋转方向")
    directions = planar[selected] / radial[selected, None]
    mean_direction = directions.mean(axis=0)
    concentration = float(np.linalg.norm(mean_direction))
    if concentration < 0.05:
        raise ValueError("圆环内侧局部截面近似对称，无法确定导柱旋转方向")
    connection = Vec3(*map(float, mean_direction)).normalized()
    lateral = ring_axis.cross(connection).normalized()
    if lateral.dot(sign_reference) < 0.0:
        lateral = -lateral
    return lateral, connection, int(selected.sum()), concentration


def _join_objects(objects: tuple[bpy.types.Object, ...]) -> bpy.types.Object:
    """把两个独立导柱合并为一个可导出的多壳网格。"""

    set_active_object(objects[0])
    for item in objects:
        item.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.object.join()
    objects[0].name = "template_only_twin_guide_posts"
    return objects[0]


def _measure_exported_d_face_spacing_mm(
    stl_path: Path,
    pair_direction: Vec3,
    axis: Vec3,
    axis_origin_center: Vec3,
    d_face_section_height_mm: float,
) -> float:
    """在最终 STL 的 C 口截面反测两个相向 D 面的净距。"""

    mesh = trimesh.load_mesh(stl_path, process=True)
    components = list(mesh.split(only_watertight=False))
    if len(components) != 2:
        raise ValueError(f"导柱 STL 应包含两个连通分量，实际为 {len(components)} 个")
    direction = np.asarray(pair_direction.as_tuple(), dtype=float)
    components.sort(key=lambda item: float(np.asarray(item.centroid) @ direction))
    plane_origin = axis_origin_center + axis * d_face_section_height_mm
    plane_normal = np.asarray(axis.as_tuple(), dtype=float)
    ranges: list[tuple[float, float]] = []
    for component in components:
        section = component.section(
            plane_origin=np.asarray(plane_origin.as_tuple(), dtype=float),
            plane_normal=plane_normal,
        )
        if section is None:
            raise ValueError("最终 STL 未能在 D 面高度形成导柱截面")
        section_points = [np.asarray(points, dtype=float) for points in section.discrete]
        if not section_points:
            raise ValueError("最终 STL 的 D 面截面没有可测量轮廓")
        coordinates = np.vstack(section_points) @ direction
        ranges.append((float(coordinates.min()), float(coordinates.max())))
    return ranges[1][0] - ranges[0][1]


def generate_preview(
    config_path: Path,
    output_directory: Path,
    reference_guide_stl: Path | None = None,
) -> None:
    """识别传统模板圆环，并生成独立的双导柱试件。"""

    config = CaseConfig.from_yaml(config_path)
    if len(config.guide_posts) != 1:
        raise ValueError("当前试生成脚本要求病例恰好配置一个 guide_posts 项")
    guide_post = config.guide_posts[0]
    sleeve_template_extension_mm = guide_post.sleeve_template_extension_mm
    source = trimesh.load_mesh(config.inputs.template, process=True)
    rings = estimate_template_rings(source)
    if guide_post.ring_index > len(rings):
        raise ValueError("guide_posts.ring_index 超出传统模板识别圆环数量")
    ring = rings[guide_post.ring_index - 1]
    top_plane = estimate_template_ring_top_plane(source, ring)

    outward = top_plane.normal.normalized()
    inward_axis = -outward
    extension_mm = guide_post.twin_guide_extension_mm
    implant_top = top_plane.center + inward_axis * sleeve_template_extension_mm
    stop_center = implant_top + outward * extension_mm

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    right_to_left = Vec3(*map(float, raw["anatomy"]["orientation"]["patient_right_to_left_axis"]))
    previous_lateral = _projected_direction(right_to_left, inward_axis)
    lateral, section_direction, section_support, section_concentration = (
        _estimate_tooth_section_lateral(
            source,
            ring.center,
            inward_axis,
            ring.radius_mm,
            ring.axial_span_mm,
            previous_lateral,
        )
    )
    rotation_from_previous_degrees = math.degrees(
        math.acos(max(-1.0, min(1.0, lateral.dot(previous_lateral))))
    )

    configured = config.sleeve
    d_face_offset = configured.outer_d_face_offset_mm
    axis_spacing = configured.guide_axis_spacing_mm
    half_spacing = 0.5 * axis_spacing
    z_platform = configured.height_mm - configured.platform_height_mm
    axis_origin_center = stop_center - inward_axis * z_platform

    clear_scene()
    template = import_stl_mesh(config.inputs.template, "traditional_template")
    template_material = bpy.data.materials.new("template_blue")
    template_material.diffuse_color = (0.10, 0.36, 0.68, 1.0)
    guide_material = bpy.data.materials.new("guide_orange")
    guide_material.diffuse_color = (1.0, 0.30, 0.03, 1.0)
    template.data.materials.append(template_material)

    guides = []
    for index, side in enumerate((-1.0, 1.0), 1):
        axis_origin = axis_origin_center + lateral * (side * half_spacing)
        opening_direction = lateral * (-side)
        estimate = SleeveEstimate(
            axis_origin=axis_origin,
            axis=inward_axis,
            c_opening_direction=opening_direction,
            height=configured.height_mm,
            platform_height=configured.platform_height_mm,
            closed_bore_height=configured.closed_bore_height_mm,
            inner_radius=configured.inner_radius_mm,
            outer_radius=configured.outer_radius_mm,
            inner_arc_angle=math.radians(configured.inner_arc_angle_degrees),
            outer_arc_angle=math.radians(configured.outer_arc_angle_degrees),
            platform_slot_width=configured.platform_slot_width_mm,
            top_recess_radius=configured.top_recess_radius_mm,
            top_recess_depth=configured.top_recess_depth_mm,
        )
        guide = create_closed_sleeve_object(estimate, f"template_only_guide_{index}")
        guide.data.materials.append(guide_material)
        guides.append(guide)

    joined = _join_objects(tuple(guides))
    output_directory.mkdir(parents=True, exist_ok=True)
    stl_path = output_directory / "template-only-twin-guide-posts.stl"
    image_path = output_directory / "template-only-twin-guide-posts.png"
    comparison_path = output_directory / "comparison-with-given-sleeve.png"
    guide_only_comparison_path = output_directory / "guide-post-only-comparison.png"
    report_path = output_directory / "template-only-twin-guide-posts.json"
    export_stl_mesh(stl_path, joined)
    measured_d_face_spacing_mm = _measure_exported_d_face_spacing_mm(
        stl_path,
        lateral,
        inward_axis,
        axis_origin_center,
        0.5 * z_platform,
    )
    d_face_spacing_error_mm = measured_d_face_spacing_mm - configured.guide_spacing_mm
    d_face_spacing_passed = abs(d_face_spacing_error_mm) <= GUIDE_D_FACE_SPACING_TOLERANCE_MM
    render_objects(
        image_path,
        (template, joined),
        RenderParameters(width_px=1200, height_px=1000),
        "iso",
    )
    given_sleeve = import_stl_mesh(
        (
            config.inputs.guide_sleeve_assemblies[0]
            if reference_guide_stl is None
            else reference_guide_stl
        ),
        "given_sleeve_for_comparison_only",
    )
    comparison_material = bpy.data.materials.new("given_sleeve_green")
    comparison_material.diffuse_color = (0.08, 0.72, 0.22, 1.0)
    given_sleeve.data.materials.append(comparison_material)
    render_objects(
        comparison_path,
        (template, given_sleeve, joined),
        RenderParameters(width_px=1200, height_px=1000),
        "iso",
    )
    render_objects(
        guide_only_comparison_path,
        (given_sleeve, joined),
        RenderParameters(width_px=1200, height_px=1000),
        "iso",
    )
    report = {
        "template": str(config.inputs.template),
        "used_sleeve_stl": False,
        "ring_index": guide_post.ring_index,
        "ring_top_plane_center": top_plane.center.as_tuple(),
        "ring_outward_axis": outward.as_tuple(),
        "sleeve_template_extension_mm": sleeve_template_extension_mm,
        "drill_length_mm": guide_post.drill_length_mm,
        "implant_length_mm": guide_post.implant_length_mm,
        "twin_guide_extension_mm": extension_mm,
        "guide_spacing_mm": configured.guide_spacing_mm,
        "guide_spacing_definition": "clearance_between_opposing_inner_d_faces",
        "measured_exported_d_face_spacing_mm": measured_d_face_spacing_mm,
        "d_face_spacing_error_mm": d_face_spacing_error_mm,
        "d_face_spacing_tolerance_mm": GUIDE_D_FACE_SPACING_TOLERANCE_MM,
        "d_face_spacing_validation_passed": d_face_spacing_passed,
        "outer_d_face_offset_from_axis_mm": d_face_offset,
        "rotation_source": "template_local_tooth_section_normal",
        "guide_pair_direction": lateral.as_tuple(),
        "local_section_direction": section_direction.as_tuple(),
        "local_section_support_face_count": section_support,
        "local_section_direction_concentration": section_concentration,
        "rotation_from_previous_global_axis_degrees": rotation_from_previous_degrees,
        "constructed_d_face_spacing_mm": axis_spacing - 2.0 * d_face_offset,
        "guide_axis_spacing_internal_mm": axis_spacing,
        "constructed_pair_outer_span_mm": configured.guide_pair_outer_span_mm,
        "implant_top_center": implant_top.as_tuple(),
        "twin_guide_stop_center": stop_center.as_tuple(),
        "output_stl": str(stl_path.resolve()),
        "output_image": str(image_path.resolve()),
        "comparison_image": str(comparison_path.resolve()),
        "guide_only_comparison_image": str(guide_only_comparison_path.resolve()),
        "comparison_reference_stl": str(
            (
                config.inputs.guide_sleeve_assemblies[0]
                if reference_guide_stl is None
                else reference_guide_stl
            ).resolve()
        ),
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if not d_face_spacing_passed:
        raise ValueError(
            "最终 STL 的 D 面净距超出公差："
            f"目标 {configured.guide_spacing_mm:.6f} mm，"
            f"实测 {measured_d_face_spacing_mm:.6f} mm，"
            f"允许误差 ±{GUIDE_D_FACE_SPACING_TOLERANCE_MM:.6f} mm"
        )


def main() -> None:
    """读取命令行参数。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_config", type=Path)
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("--reference-guide-stl", type=Path)
    arguments = parser.parse_args(
        sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else None
    )
    generate_preview(
        arguments.case_config.resolve(),
        arguments.output_directory.resolve(),
        (
            None
            if arguments.reference_guide_stl is None
            else arguments.reference_guide_stl.resolve()
        ),
    )


if __name__ == "__main__":
    main()

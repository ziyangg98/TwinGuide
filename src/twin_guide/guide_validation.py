"""对已导出牙科导板进行当前已实现的结构检查。"""

from __future__ import annotations

import math
from pathlib import Path

import bpy
from mathutils.bvhtree import BVHTree

from twin_guide.blender.mesh_queries import (
    build_bvh,
    duplicate_triangle_count,
    mesh_component_vertex_counts,
    nearest_mesh_distance,
    point_inside_mesh,
    sample_mesh_surface,
    topology_edge_counts,
)
from twin_guide.blender.sleeve_reconstruction import create_closed_sleeve_object
from twin_guide.blender.stl_io import import_stl_mesh
from twin_guide.case_analysis import analyze_case
from twin_guide.config import CaseConfig
from twin_guide.geometry import Vec3, point_axis_coordinates
from twin_guide.models import (
    CaseAnalysis,
    CylinderCutout,
    ValidationResult,
)
from twin_guide.point_linking import PointLinkingConfig, PointLinkingPlan, link_selected_points
from twin_guide.template_anchors import TemplatePointSelectionConfig
from twin_guide.template_link_points import (
    TemplateLinkPointContext,
    select_template_link_points,
)
from twin_guide.types import SleeveGenerationResult
from twin_guide.window_cutouts import plan_window_cutouts

REFERENCE_SAMPLE_LIMIT = 3_000
RETAINED_FRACTION_MINIMUM = 0.90
BORE_VOXEL_MARGIN_FACTOR = 3.0
CONNECTOR_INSIDE_FRACTION_MINIMUM = 0.95


def _point_is_retained(model_bvh: BVHTree, point: Vec3, distance_tolerance_mm: float) -> bool:
    """返回导套表面点是否仍由最终实体保留。"""

    return point_inside_mesh(model_bvh, point) or (
        nearest_mesh_distance(model_bvh, point) <= distance_tolerance_mm
    )


def _channel_probe_points(
    channel: CylinderCutout,
    usable_bore_radius_mm: float,
) -> tuple[Vec3, ...]:
    """按轴向自适应截面和三个径向层生成通道畅通性探针点。"""

    axis_vector = channel.end - channel.start
    length_mm = axis_vector.length
    axis = axis_vector.normalized()
    reference = Vec3(1.0, 0.0, 0.0) if abs(axis.x) < 0.8 else Vec3(0.0, 1.0, 0.0)
    tangent = axis.cross(reference).normalized()
    bitangent = axis.cross(tangent).normalized()
    axial_interval_count = max(
        3,
        math.ceil(length_mm / max(0.5, usable_bore_radius_mm * 0.5)),
    )
    points = []
    for axial_index in range(axial_interval_count + 1):
        center = channel.start + axis_vector * (axial_index / axial_interval_count)
        for radius_fraction in (0.0, 0.5, 0.9):
            angle_count = 1 if radius_fraction == 0.0 else 12
            for angle_index in range(angle_count):
                angle = math.tau * angle_index / angle_count
                radial_direction = tangent * math.cos(angle) + bitangent * math.sin(angle)
                points.append(center + radial_direction * (usable_bore_radius_mm * radius_fraction))
    return tuple(points)


def _guide_retention_result(
    model_bvh: BVHTree,
    case: CaseAnalysis,
    reference_sleeves: tuple[bpy.types.Object, bpy.types.Object],
) -> ValidationResult:
    """以重建导套表面点的实体包含率验证导套保留。"""

    voxel_size_mm = case.config.geometry.fusion_voxel_size_mm
    distance_tolerance_mm = voxel_size_mm * 2.0
    metrics: dict[str, int | float] = {
        "minimum_retained_fraction": RETAINED_FRACTION_MINIMUM,
        "distance_tolerance_mm": distance_tolerance_mm,
    }
    passed = True
    for guide, reference_sleeve in zip(case.guide_sleeves, reference_sleeves, strict=True):
        reference_points = tuple(
            sample.position
            for sample in sample_mesh_surface(reference_sleeve, REFERENCE_SAMPLE_LIMIT)
        )
        retained_count = sum(
            _point_is_retained(model_bvh, point, distance_tolerance_mm)
            for point in reference_points
        )
        retained_fraction = retained_count / len(reference_points) if reference_points else 0.0
        metrics[f"guide_{guide.guide_index}_reference_count"] = len(reference_points)
        metrics[f"guide_{guide.guide_index}_retained_fraction"] = retained_fraction
        passed &= retained_fraction >= RETAINED_FRACTION_MINIMUM
    return ValidationResult("guide_retention", passed, metrics)


def _connector_result(
    model_bvh: BVHTree,
    template_bvh: BVHTree,
    sleeve_bvhs: tuple[BVHTree, ...],
    case: CaseAnalysis,
    connector_plan: PointLinkingPlan,
    connector_radius_mm: float,
) -> ValidationResult:
    """检查每条曲线中心线的实体包含率和两端表面距离。"""

    metrics: dict[str, int | float] = {"minimum_inside_fraction": CONNECTOR_INSIDE_FRACTION_MINIMUM}
    passed = True
    for connector in connector_plan.links:
        guide = case.guide_sleeves[connector.guide_index - 1]
        retained_centerline = tuple(
            point
            for point in connector.centerline
            if point_axis_coordinates(point, guide.center, guide.axis)[0] >= guide.bore_radius_mm
        )
        inside_count = sum(point_inside_mesh(model_bvh, point) for point in retained_centerline)
        inside_fraction = inside_count / len(retained_centerline)
        sleeve_bvh = sleeve_bvhs[connector.guide_index - 1]
        guide_end_distance = nearest_mesh_distance(sleeve_bvh, connector.start)
        template_end_distance = nearest_mesh_distance(template_bvh, connector.end)
        metric_prefix = (
            f"guide_{connector.guide_index}_{connector.sleeve_label}_{connector.template_label}"
        )
        metrics[f"{metric_prefix}_sample_count"] = len(connector.centerline)
        metrics[f"{metric_prefix}_retained_sample_count"] = len(retained_centerline)
        metrics[f"{metric_prefix}_inside_fraction"] = inside_fraction
        metrics[f"{metric_prefix}_sleeve_end_distance_mm"] = guide_end_distance
        metrics[f"{metric_prefix}_template_end_distance_mm"] = template_end_distance
        passed &= (
            inside_fraction >= CONNECTOR_INSIDE_FRACTION_MINIMUM
            and guide_end_distance <= connector_radius_mm + 0.4
            and template_end_distance <= 0.5
        )
    return ValidationResult("guide_connectors", passed, metrics)


def validate_guide(
    model_path: Path,
    config: CaseConfig,
) -> tuple[ValidationResult, ...]:
    """对已导出 STL 执行独立检查，不修改输入文件。"""

    case = analyze_case(config)
    sleeves = SleeveGenerationResult(case.guide_sleeves, case.template_frame)
    cutout_plan = plan_window_cutouts(case, sleeves)
    link_points = select_template_link_points(
        TemplateLinkPointContext(case, sleeves, cutout_plan),
        TemplatePointSelectionConfig(connector_radius_mm=config.geometry.connector_radius_mm),
    )
    connector_plan = link_selected_points(
        link_points,
        PointLinkingConfig(radius_mm=config.geometry.connector_radius_mm),
    )
    reference_sleeves = tuple(
        create_closed_sleeve_object(
            guide.parameters,
            f"guide_{guide.guide_index}_validation_sleeve",
        )
        for guide in case.guide_sleeves
    )
    clean_sleeves = (reference_sleeves[0], reference_sleeves[1])
    model_mesh = import_stl_mesh(model_path.resolve(), "validated_twin_guide_mesh")
    model_bvh = build_bvh(model_mesh)
    template_bvh = build_bvh(case.input_meshes.template_mesh)
    sleeve_bvhs = tuple(build_bvh(sleeve) for sleeve in clean_sleeves)
    boundary_edge_count, non_manifold_edge_count = topology_edge_counts(model_mesh)
    duplicate_face_count = duplicate_triangle_count(model_mesh)
    component_vertex_counts = mesh_component_vertex_counts(model_mesh)
    results = [
        ValidationResult(
            "topology",
            boundary_edge_count == 0
            and non_manifold_edge_count == 0
            and duplicate_face_count == 0
            and len(component_vertex_counts) == 1,
            {
                "boundary_edge_count": boundary_edge_count,
                "non_manifold_edge_count": non_manifold_edge_count,
                "duplicate_triangle_count": duplicate_face_count,
                "connected_component_count": len(component_vertex_counts),
                "smallest_component_vertex_count": min(component_vertex_counts, default=0),
            },
        ),
        _guide_retention_result(model_bvh, case, clean_sleeves),
        _connector_result(
            model_bvh,
            template_bvh,
            sleeve_bvhs,
            case,
            connector_plan,
            config.geometry.connector_radius_mm,
        ),
    ]
    usable_bore_radii = tuple(
        max(
            guide.bore_radius_mm - BORE_VOXEL_MARGIN_FACTOR * config.geometry.fusion_voxel_size_mm,
            guide.bore_radius_mm * 0.25,
        )
        for guide in case.guide_sleeves
    )
    channel_probe_points = tuple(
        point
        for usable_bore_radius_mm, channel in zip(
            usable_bore_radii,
            cutout_plan.channels,
            strict=True,
        )
        for point in _channel_probe_points(channel, usable_bore_radius_mm)
    )
    blocked_channel_count = sum(
        point_inside_mesh(model_bvh, point) for point in channel_probe_points
    )
    results.append(
        ValidationResult(
            "channels",
            blocked_channel_count == 0,
            {
                "sample_count": len(channel_probe_points),
                "blocked_sample_count": blocked_channel_count,
                "guide_1_usable_bore_radius_mm": usable_bore_radii[0],
                "guide_2_usable_bore_radius_mm": usable_bore_radii[1],
            },
        )
    )
    return tuple(results)

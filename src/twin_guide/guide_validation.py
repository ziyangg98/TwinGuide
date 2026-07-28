"""对已导出牙科导板进行当前已实现的结构检查。"""

from __future__ import annotations

import json
import math
from pathlib import Path

import bpy
from mathutils.bvhtree import BVHTree

from twin_guide.blender.mesh_builders import MINIMUM_CONFORMAL_FOOTPRINT_SCALE
from twin_guide.blender.mesh_queries import (
    build_bvh,
    build_local_aligned_bvh,
    duplicate_triangle_count,
    mesh_component_vertex_counts,
    nearest_mesh_distance,
    nearest_mesh_surface_side,
    point_inside_mesh,
    sample_mesh_surface,
    to_blender_vector,
    to_vec3,
    topology_edge_counts,
)
from twin_guide.blender.sleeve_reconstruction import create_closed_sleeve_object
from twin_guide.blender.stl_io import import_stl_mesh
from twin_guide.config import (
    CaseConfig,
    PressBeamMode,
    SleeveGeometryMode,
)
from twin_guide.generation_process import run_generation_process
from twin_guide.geometry import Vec3, point_axis_coordinates
from twin_guide.models import (
    CaseAnalysis,
    CylinderCutout,
    ProfileWindowCutout,
    ValidationResult,
)
from twin_guide.point_linking import PointLinkingPlan
from twin_guide.types import ConnectorEndpointSource

REFERENCE_SAMPLE_LIMIT = 3_000
RETAINED_FRACTION_MINIMUM = 0.90
BORE_VOXEL_MARGIN_FACTOR = 3.0
CONNECTOR_INSIDE_FRACTION_MINIMUM = 0.95
OBSERVATION_CREST_CLEARANCE_MINIMUM_MM = 0.2


def _point_is_retained(model_bvh: BVHTree, point: Vec3, distance_tolerance_mm: float) -> bool:
    """返回导管表面点是否仍由最终实体保留。"""

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
    reference_sleeves: tuple[bpy.types.Object, ...],
) -> ValidationResult:
    """按所选模式以输入或参数化导管表面验证最终实体保留率。"""

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
        if guide.source_component_index is not None:
            metrics[f"guide_{guide.guide_index}_source_component_index"] = (
                guide.source_component_index
            )
        if guide.axial_bore_clear_fraction is not None:
            metrics[f"guide_{guide.guide_index}_axial_bore_clear_fraction"] = (
                guide.axial_bore_clear_fraction
            )
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
    """检查每根主梁中心线及其导管和导板端点的保留情况。"""

    voxel_tolerance_mm = case.config.geometry.fusion_voxel_size_mm
    metrics: dict[str, int | float] = {
        "minimum_inside_fraction": CONNECTOR_INSIDE_FRACTION_MINIMUM,
        "voxel_surface_tolerance_mm": voxel_tolerance_mm,
    }
    passed = True
    for connector in connector_plan.links:
        guide = case.guide_sleeves[connector.guide_index - 1]
        retained_centerline = tuple(
            point
            for point in connector.centerline
            if point_axis_coordinates(point, guide.center, guide.axis)[0] >= guide.bore_radius_mm
        )
        inside_count = sum(
            _point_is_retained(model_bvh, point, voxel_tolerance_mm)
            for point in retained_centerline
        )
        inside_fraction = (
            inside_count / len(retained_centerline) if retained_centerline else 0.0
        )
        guide_indices = connector.guide_indices or (connector.guide_index,)
        tube_contacts = connector.tube_contacts or (connector.tube_contact,)
        tube_contact_distances = tuple(
            nearest_mesh_distance(sleeve_bvhs[guide_index - 1], contact)
            for guide_index, contact in zip(
                guide_indices, tube_contacts, strict=True
            )
        )
        tube_contact_distance = max(tube_contact_distances)
        left_anchor_distance = (
            nearest_mesh_distance(template_bvh, connector.left_surface_anchor)
            if connector.left_source is ConnectorEndpointSource.TEMPLATE
            else 0.0
        )
        right_anchor_distance = (
            nearest_mesh_distance(template_bvh, connector.right_surface_anchor)
            if connector.right_source is ConnectorEndpointSource.TEMPLATE
            else 0.0
        )
        link_label = connector.link_label or connector.sleeve_label
        metric_prefix = f"guide_{connector.guide_index}_{link_label}"
        metrics[f"{metric_prefix}_sample_count"] = len(connector.centerline)
        metrics[f"{metric_prefix}_retained_sample_count"] = len(retained_centerline)
        metrics[f"{metric_prefix}_inside_fraction"] = inside_fraction
        metrics[f"{metric_prefix}_tube_contact_distance_mm"] = tube_contact_distance
        metrics[f"{metric_prefix}_left_anchor_distance_mm"] = left_anchor_distance
        metrics[f"{metric_prefix}_right_anchor_distance_mm"] = right_anchor_distance
        passed &= (
            inside_fraction >= CONNECTOR_INSIDE_FRACTION_MINIMUM
            and tube_contact_distance <= connector_radius_mm + 0.4
            and (
                connector.left_source is not ConnectorEndpointSource.TEMPLATE
                or left_anchor_distance <= 0.5
            )
            and (
                connector.right_source is not ConnectorEndpointSource.TEMPLATE
                or right_anchor_distance <= 0.5
            )
        )
    return ValidationResult("guide_connectors", passed, metrics)


def _terminal_distal_common_node_result(
    model_bvh: BVHTree,
    case: CaseAnalysis,
    connector_plan: PointLinkingPlan,
    tooth_identification: object,
) -> ValidationResult:
    """检查四根主连接梁是否共享 B + 2D·远中方向得到的节点。"""

    terminal = connector_plan.terminal_distal_common_node
    if terminal is None:
        return ValidationResult(
            "terminal_distal_common_node",
            False,
            {"missing_terminal_anchor_plan": 1},
        )
    distal_node_links = tuple(
        link
        for link in connector_plan.links
        if (
            link.left_source is ConnectorEndpointSource.DISTAL_COMMON_NODE
            or link.right_source is ConnectorEndpointSource.DISTAL_COMMON_NODE
        )
    )
    endpoint_errors = []
    for link in distal_node_links:
        endpoint = (
            link.start
            if link.left_source is ConnectorEndpointSource.DISTAL_COMMON_NODE
            else link.end
        )
        endpoint_errors.append(endpoint.distance_to(terminal.centerline_node))
    node_retained = _point_is_retained(
        model_bvh,
        terminal.centerline_node,
        case.config.geometry.fusion_voxel_size_mm,
    )
    maximum_endpoint_error = max(endpoint_errors, default=float("inf"))
    lower_links = tuple(
        link
        for link in connector_plan.links
        if link.sleeve_label == "lower"
    )
    lower_contacts = tuple(
        (link.tube_contacts or (link.tube_contact,))[-1]
        for link in lower_links
    )
    expected_projection_base = (
        (lower_contacts[0] + lower_contacts[1]) * 0.5
        if len(lower_contacts) == 2
        else None
    )
    projection_base_error = (
        terminal.projection_base.distance_to(expected_projection_base)
        if terminal.projection_base is not None
        and expected_projection_base is not None
        else float("inf")
    )
    terminal_guide_indices = tuple(
        (link.guide_indices or (link.guide_index,))[-1]
        for link in lower_links
    )
    terminal_guides = tuple(
        case.guide_sleeves[index - 1] for index in terminal_guide_indices
    )
    expected_offset = (
        sum(2.0 * guide.body_radius_mm for guide in terminal_guides)
        / len(terminal_guides)
        * case.config.guide_anchors.terminal_distal_common_node.
        distal_offset_sleeve_diameters
    )
    offset_error = abs(terminal.distal_offset_mm - expected_offset)
    expected_node = (
        terminal.projection_base
        + terminal.distal_direction * expected_offset
    )
    node_formula_error = terminal.centerline_node.distance_to(expected_node)
    centers = tuple(guide.center for guide in terminal_guides)
    sleeve_line = (centers[1] - centers[0]).normalized()
    axes = [guide.axis.normalized() for guide in terminal_guides]
    if axes[0].dot(axes[1]) < 0.0:
        axes[1] = axes[1] * -1.0
    common_axis = (axes[0] + axes[1]).normalized()
    perpendicular_error = (
        max(
            abs(terminal.distal_direction.dot(sleeve_line)),
            abs(terminal.distal_direction.dot(common_axis)),
        )
    )
    distal_alignment = -1.0
    mapping_report = getattr(tooth_identification, "mapping_report", {})
    sources = mapping_report.get("sources") if isinstance(mapping_report, dict) else None
    base_path = sources.get("base_coordinate_report") if isinstance(sources, dict) else None
    if isinstance(base_path, str):
        try:
            base_report = json.loads(Path(base_path).read_text(encoding="utf-8"))
            slots = base_report.get("tooth_slots", [])
            missing_slot = next(
                slot for slot in slots if slot.get("FDI") == terminal.missing_fdi
            )
            neighbor_slot = next(
                slot
                for slot in slots
                if slot.get("FDI") == terminal.reference_neighbor_fdi
            )
            missing_point = Vec3(*missing_slot["dental_crown_point_global_mm"])
            neighbor_point = Vec3(*neighbor_slot["dental_crown_point_global_mm"])
            distal_alignment = terminal.distal_direction.dot(
                (missing_point - neighbor_point).normalized()
            )
        except (OSError, ValueError, KeyError, StopIteration, json.JSONDecodeError):
            distal_alignment = -1.0
    passed = (
        len(distal_node_links) == 4
        and maximum_endpoint_error <= 1e-6
        and node_retained
        and projection_base_error <= 1e-6
        and offset_error <= 1e-6
        and node_formula_error <= 1e-6
        and perpendicular_error <= 1e-6
        and distal_alignment > 0.0
    )
    return ValidationResult(
        "terminal_distal_common_node",
        passed,
        {
            "distal_common_node_link_count": len(distal_node_links),
            "maximum_common_endpoint_error_mm": maximum_endpoint_error,
            "node_radius_mm": terminal.node_radius_mm,
            "distal_offset_mm": terminal.distal_offset_mm,
            "projection_base_error_mm": projection_base_error,
            "distal_offset_error_mm": offset_error,
            "node_formula_error_mm": node_formula_error,
            "distal_perpendicular_error": perpendicular_error,
            "distal_alignment": distal_alignment,
            "node_retained": int(node_retained),
            "missing_fdi": terminal.missing_fdi,
            "reference_neighbor_fdi": terminal.reference_neighbor_fdi,
        },
    )


def _guide_component_bridge_result(
    model_bvh: BVHTree,
    template_bvh: BVHTree,
    case: CaseAnalysis,
    connector_plan: PointLinkingPlan,
) -> ValidationResult:
    """检查两根断裂导板预连接梁的中心线和跨分量端点。"""

    bridge = connector_plan.guide_component_bridge
    if bridge is None:
        return ValidationResult(
            "guide_component_bridge", False, {"missing_bridge_plan": 1}
        )
    tolerance = case.config.geometry.fusion_voxel_size_mm
    metrics: dict[str, int | float] = {
        "link_count": len(bridge.links),
        "meaningful_component_count": len(bridge.meaningful_component_areas_mm2),
        "minimum_inside_fraction": CONNECTOR_INSIDE_FRACTION_MINIMUM,
    }
    passed = len(bridge.links) == 2
    for index, link in enumerate(bridge.links, 1):
        inside_count = sum(
            _point_is_retained(model_bvh, point, tolerance)
            for point in link.centerline
        )
        inside_fraction = inside_count / len(link.centerline)
        start_distance = nearest_mesh_distance(
            template_bvh, link.start_surface_anchor
        )
        end_distance = nearest_mesh_distance(template_bvh, link.end_surface_anchor)
        prefix = f"link_{index}_{link.side}"
        metrics[f"{prefix}_sample_count"] = len(link.centerline)
        metrics[f"{prefix}_inside_fraction"] = inside_fraction
        metrics[f"{prefix}_start_anchor_distance_mm"] = start_distance
        metrics[f"{prefix}_end_anchor_distance_mm"] = end_distance
        metrics[f"{prefix}_start_component_rank"] = link.start_component_rank
        metrics[f"{prefix}_end_component_rank"] = link.end_component_rank
        passed &= (
            inside_fraction >= CONNECTOR_INSIDE_FRACTION_MINIMUM
            and start_distance <= 0.5
            and end_distance <= 0.5
            and link.start_component_rank != link.end_component_rank
        )
    return ValidationResult("guide_component_bridge", passed, metrics)


def _guide_terminal_u_extension_result(
    model_bvh: BVHTree,
    template_bvh: BVHTree,
    case: CaseAnalysis,
    connector_plan: PointLinkingPlan,
    tooth_identification: object,
) -> ValidationResult:
    """检查末端绕牙 U 型梁中心线、回转顶点和两个导板根部。"""

    extension = connector_plan.guide_terminal_u_extension
    if extension is None:
        return ValidationResult(
            "guide_terminal_u_extension", False, {"missing_extension_plan": 1}
        )
    tolerance = case.config.geometry.fusion_voxel_size_mm
    inside_count = sum(
        _point_is_retained(model_bvh, point, tolerance)
        for point in extension.centerline
    )
    inside_fraction = inside_count / len(extension.centerline)
    u_anchor_distance = nearest_mesh_distance(
        template_bvh, extension.u_surface_anchor
    )
    back_u_anchor_distance = nearest_mesh_distance(
        template_bvh, extension.back_u_surface_anchor
    )
    apex_retained = _point_is_retained(
        model_bvh, extension.turnaround_apex, tolerance
    )
    positions = {
        position.fdi: position
        for position in getattr(tooth_identification, "positions", ())
    }
    terminal_position = positions.get(extension.terminal_fdi)
    neighbor_position = positions.get(extension.reference_neighbor_fdi)
    distal_alignment = (
        extension.distal_direction.dot(
            (
                terminal_position.crown_point
                - neighbor_position.crown_point
            ).normalized()
        )
        if terminal_position is not None and neighbor_position is not None
        else -1.0
    )
    required_clearance = (
        case.config.guide_terminal_u_extension.dental_clearance_mm
        + case.config.guide_terminal_u_extension.safety_margin_mm
    )
    passed = (
        inside_fraction >= CONNECTOR_INSIDE_FRACTION_MINIMUM
        and u_anchor_distance <= 0.5
        and back_u_anchor_distance <= 0.5
        and apex_retained
        and distal_alignment > 0.0
        and extension.turnaround_surface_clearance_mm
        >= required_clearance - tolerance
    )
    return ValidationResult(
        "guide_terminal_u_extension",
        passed,
        {
            "sample_count": len(extension.centerline),
            "inside_fraction": inside_fraction,
            "minimum_inside_fraction": CONNECTOR_INSIDE_FRACTION_MINIMUM,
            "u_anchor_distance_mm": u_anchor_distance,
            "back_u_anchor_distance_mm": back_u_anchor_distance,
            "turnaround_apex_retained": int(apex_retained),
            "terminal_fdi": extension.terminal_fdi,
            "reference_neighbor_fdi": extension.reference_neighbor_fdi,
            "distal_surface_extent_mm": extension.distal_surface_extent_mm,
            "turnaround_entry_distal_mm": extension.turnaround_entry_distal_mm,
            "turnaround_apex_distal_mm": extension.turnaround_apex_distal_mm,
            "turnaround_surface_clearance_mm": (
                extension.turnaround_surface_clearance_mm
            ),
            "required_turnaround_clearance_mm": required_clearance,
            "distal_alignment": distal_alignment,
        },
    )


def _press_beam_result(
    model_bvh: BVHTree,
    template_mesh: bpy.types.Object,
    case: CaseAnalysis,
    connector_plan: PointLinkingPlan,
) -> ValidationResult:
    """独立检查 Y 三臂中心线以及两个导板端根部球和贴合脚的保留率。"""

    endpoint = connector_plan.press_beam_guide_endpoint
    radius_mm = connector_plan.press_beam_radius_mm
    if endpoint is None or radius_mm is None:
        return ValidationResult("press_beam", False, {"missing_endpoint_plan": 1})
    tolerance = case.config.geometry.fusion_voxel_size_mm
    metrics: dict[str, int | float] = {
        "minimum_centerline_inside_fraction": CONNECTOR_INSIDE_FRACTION_MINIMUM,
        "root_radius_mm": radius_mm * endpoint.root_radius_factor,
        "bulb_radius_mm": radius_mm * endpoint.bulb_radius_factor,
        "configured_foot_planar_area_mm2": (
            math.pi * endpoint.foot_major_radius_mm * endpoint.foot_minor_radius_mm
        ),
        "minimum_adaptive_foot_planar_area_mm2": (
            math.pi
            * endpoint.foot_major_radius_mm
            * endpoint.foot_minor_radius_mm
            * MINIMUM_CONFORMAL_FOOTPRINT_SCALE
            * MINIMUM_CONFORMAL_FOOTPRINT_SCALE
        ),
    }
    passed = True
    guide_endpoint_count = 0
    for index, link in enumerate(connector_plan.press_beam_links, 1):
        length = link.start.distance_to(link.end)
        sample_count = max(2, math.ceil(length / 0.30) + 1)
        centerline_samples = tuple(
            link.start + (link.end - link.start) * (sample / (sample_count - 1))
            for sample in range(sample_count)
        )
        inside_count = sum(
            _point_is_retained(model_bvh, point, tolerance)
            for point in centerline_samples
        )
        inside_fraction = inside_count / sample_count
        prefix = f"arm_{index}_{link.label}"
        metrics[f"{prefix}_sample_count"] = sample_count
        metrics[f"{prefix}_inside_fraction"] = inside_fraction
        passed &= inside_fraction >= CONNECTOR_INSIDE_FRACTION_MINIMUM
        if link.source != "tooth_section_trajectory":
            continue
        guide_endpoint_count += 1
        outward_tangent = (link.start - link.end).normalized()
        bulb_center = (
            link.start + outward_tangent * endpoint.bulb_forward_offset_mm
        )
        bulb_probe_radius = radius_mm * endpoint.bulb_radius_factor * 0.65
        bulb_probes = (bulb_center, *tuple(bulb_center + axis * bulb_probe_radius for axis in (Vec3(1.0, 0.0, 0.0), Vec3(-1.0, 0.0, 0.0), Vec3(0.0, 1.0, 0.0), Vec3(0.0, -1.0, 0.0), Vec3(0.0, 0.0, 1.0), Vec3(0.0, 0.0, -1.0))))
        bulb_inside = sum(
            _point_is_retained(model_bvh, point, tolerance)
            for point in bulb_probes
        )
        bulb_fraction = bulb_inside / len(bulb_probes)
        metrics[f"{prefix}_bulb_inside_fraction"] = bulb_fraction

        normal = link.surface_normal.normalized()
        tangent = link.end - link.start
        tangent = (tangent - normal * tangent.dot(normal)).normalized()
        bitangent = normal.cross(tangent).normalized()
        local_surface_bvh = build_local_aligned_bvh(
            template_mesh,
            link.surface_anchor,
            normal,
            tangent,
            bitangent,
            endpoint.foot_major_radius_mm,
            endpoint.foot_minor_radius_mm,
        )
        foot_probes = []
        # 生成器允许足印按局部曲率最多缩到 77%；以配置足印的 44% 采样，
        # 即使达到最小尺寸也只对应实际足印的 57%。
        rho = 0.44
        maximum_relative_rho = rho / MINIMUM_CONFORMAL_FOOTPRINT_SCALE
        for angle_index in range(8):
            angle = math.tau * angle_index / 8
            query = (
                link.surface_anchor
                + tangent * (rho * endpoint.foot_major_radius_mm * math.cos(angle))
                + bitangent * (rho * endpoint.foot_minor_radius_mm * math.sin(angle))
            )
            location, local_normal, _, _ = local_surface_bvh.find_nearest(
                to_blender_vector(query)
            )
            if location is None or local_normal is None:
                continue
            projected_normal = to_vec3(local_normal).normalized()
            if projected_normal.dot(normal) < 0.0:
                projected_normal = projected_normal * -1.0
            height = (
                endpoint.foot_peak_height_mm
                * (1.0 - maximum_relative_rho * maximum_relative_rho) ** 2
            )
            foot_probes.append(
                to_vec3(location) + projected_normal * (height * 0.60)
            )
        foot_inside = sum(
            _point_is_retained(model_bvh, point, tolerance)
            for point in foot_probes
        )
        foot_fraction = foot_inside / len(foot_probes) if foot_probes else 0.0
        metrics[f"{prefix}_foot_probe_count"] = len(foot_probes)
        metrics[f"{prefix}_foot_inside_fraction"] = foot_fraction
        passed &= bulb_fraction >= 0.85 and foot_fraction >= 0.75
    metrics["guide_endpoint_count"] = guide_endpoint_count
    expected_guide_endpoints = (
        3
        if case.config.press_beam.mode is PressBeamMode.THREE_TOOTH_ANCHORS_Y
        else 2
    )
    metrics["expected_guide_endpoint_count"] = expected_guide_endpoints
    passed &= guide_endpoint_count == expected_guide_endpoints
    return ValidationResult("press_beam", passed, metrics)


def _connector_endpoint_reinforcement_result(
    model_bvh: BVHTree,
    template_mesh: bpy.types.Object,
    case: CaseAnalysis,
    connector_plan: PointLinkingPlan,
) -> ValidationResult:
    """检查四个唯一连接梁导板端的根部球和自适应贴合脚。"""

    endpoint = connector_plan.connector_guide_endpoint
    if endpoint is None:
        return ValidationResult(
            "connector_endpoint_reinforcement",
            False,
            {"missing_endpoint_plan": 1},
        )
    groups: dict[
        tuple[float, float, float],
        tuple[str, Vec3, Vec3, Vec3, list[Vec3]],
    ] = {}
    for link in connector_plan.links:
        endpoints = (
            (
                "left",
                link.left_source,
                link.left_surface_anchor,
                link.left_surface_normal,
                link.start,
                link.centerline[1] - link.start,
            ),
            (
                "right",
                link.right_source,
                link.right_surface_anchor,
                link.right_surface_normal,
                link.end,
                link.centerline[-2] - link.end,
            ),
        )
        for side, source, surface_anchor, surface_normal, center, incident in endpoints:
            if source is not ConnectorEndpointSource.TEMPLATE:
                continue
            key = tuple(round(value, 5) for value in center.as_tuple())
            if key not in groups:
                groups[key] = (
                    f"guide_{link.guide_index}_{side}",
                    surface_anchor,
                    surface_normal,
                    center,
                    [],
                )
            groups[key][4].append(incident)

    radius_mm = connector_plan.radius_mm
    tolerance = case.config.geometry.fusion_voxel_size_mm
    metrics: dict[str, int | float] = {
        "endpoint_count": len(groups),
        "root_radius_mm": radius_mm * endpoint.root_radius_factor,
        "bulb_radius_mm": radius_mm * endpoint.bulb_radius_factor,
    }
    expected_endpoint_count = (
        2 if connector_plan.terminal_distal_common_node is not None else 4
    )
    metrics["expected_endpoint_count"] = expected_endpoint_count
    passed = len(groups) == expected_endpoint_count
    for label, surface_anchor, surface_normal, center, incidents in groups.values():
        incident = sum(incidents, Vec3(0.0, 0.0, 0.0)).normalized()
        bulb_center = center - incident * endpoint.bulb_forward_offset_mm
        bulb_probe_radius = radius_mm * endpoint.bulb_radius_factor * 0.65
        bulb_probes = (bulb_center, *tuple(bulb_center + axis * bulb_probe_radius for axis in (Vec3(1.0, 0.0, 0.0), Vec3(-1.0, 0.0, 0.0), Vec3(0.0, 1.0, 0.0), Vec3(0.0, -1.0, 0.0), Vec3(0.0, 0.0, 1.0), Vec3(0.0, 0.0, -1.0))))
        bulb_fraction = sum(
            _point_is_retained(model_bvh, point, tolerance)
            for point in bulb_probes
        ) / len(bulb_probes)

        normal = surface_normal.normalized()
        tangent = incident - normal * incident.dot(normal)
        if tangent.length <= 1e-8:
            tangent = incident
        tangent = tangent.normalized()
        bitangent = normal.cross(tangent).normalized()
        local_surface_bvh = build_local_aligned_bvh(
            template_mesh,
            surface_anchor,
            normal,
            tangent,
            bitangent,
            endpoint.foot_major_radius_mm,
            endpoint.foot_minor_radius_mm,
        )
        # 与生成器允许的最小足印比例使用同一常量。此前这里按 80%
        # 采样，而生成器可缩至 77%，会把真实足印内的边界点误判为缺失。
        rho = 0.44
        maximum_relative_rho = rho / MINIMUM_CONFORMAL_FOOTPRINT_SCALE
        height = (
            endpoint.foot_peak_height_mm
            * (1.0 - maximum_relative_rho * maximum_relative_rho) ** 2
        )
        foot_probes = []
        for angle_index in range(8):
            angle = math.tau * angle_index / 8
            query = (
                surface_anchor
                + tangent * (rho * endpoint.foot_major_radius_mm * math.cos(angle))
                + bitangent * (rho * endpoint.foot_minor_radius_mm * math.sin(angle))
            )
            location, local_normal, _, _ = local_surface_bvh.find_nearest(
                to_blender_vector(query)
            )
            if location is None or local_normal is None:
                continue
            projected_normal = to_vec3(local_normal).normalized()
            if projected_normal.dot(normal) < 0.0:
                projected_normal = projected_normal * -1.0
            foot_probes.append(
                to_vec3(location) + projected_normal * (height * 0.60)
            )
        foot_fraction = (
            sum(
                _point_is_retained(model_bvh, point, tolerance)
                for point in foot_probes
            )
            / len(foot_probes)
            if foot_probes
            else 0.0
        )
        metrics[f"{label}_bulb_inside_fraction"] = bulb_fraction
        metrics[f"{label}_foot_inside_fraction"] = foot_fraction
        passed &= bulb_fraction >= 0.85 and foot_fraction >= 0.75
    return ValidationResult("connector_endpoint_reinforcement", passed, metrics)


def _observation_window_result(
    model_bvh: BVHTree,
    profile_windows: tuple[ProfileWindowCutout, ...],
) -> ValidationResult:
    """检查第 3 步最终局部修正轴在最终实体中保持开放。"""

    metrics: dict[str, int | float] = {
        "minimum_clearance_mm": OBSERVATION_CREST_CLEARANCE_MINIMUM_MM,
        "window_count": sum(len(profile.window_ids) for profile in profile_windows),
    }
    passed = True
    total_samples = 0
    blocked_samples = 0
    for profile in profile_windows:
        if len(profile.window_ids) != len(profile.window_crest_points):
            raise ValueError("观察窗 ID 与最终修正轴点组数量不一致")
        for window_id, points in zip(
            profile.window_ids, profile.window_crest_points, strict=True
        ):
            clearances = tuple(
                nearest_mesh_distance(model_bvh, point) for point in points
            )
            blocked = sum(
                point_inside_mesh(model_bvh, point)
                or clearance < OBSERVATION_CREST_CLEARANCE_MINIMUM_MM
                for point, clearance in zip(points, clearances, strict=True)
            )
            total_samples += len(clearances)
            blocked_samples += blocked
            metrics[f"{window_id}_sample_count"] = len(clearances)
            metrics[f"{window_id}_blocked_sample_count"] = blocked
            metrics[f"{window_id}_minimum_clearance_mm"] = min(clearances)
            passed &= blocked == 0
    metrics["sample_count"] = total_samples
    metrics["blocked_sample_count"] = blocked_samples
    return ValidationResult("observation_windows", passed, metrics)


def validate_guide(
    model_path: Path,
    config: CaseConfig,
) -> tuple[ValidationResult, ...]:
    """对已导出 STL 执行独立检查，不修改输入文件。"""

    process = run_generation_process(config)
    context = process.context
    if (
        context.case is None
        or context.window_cutouts is None
        or context.point_linking is None
    ):
        raise RuntimeError("验证未获得完整的公开几何计划")
    case = context.case
    cutout_plan = context.window_cutouts
    connector_plan = context.point_linking
    tooth_identification = context.tooth_identification
    reference_sleeves = (
        tuple(guide.guide_mesh for guide in case.guide_sleeves)
        if config.sleeve_geometry_mode is SleeveGeometryMode.INPUT
        else tuple(
            create_closed_sleeve_object(
                guide.parameters,
                f"validation_generated_sleeve_{guide.guide_index}",
            )
            for guide in case.guide_sleeves
        )
    )
    model_mesh = import_stl_mesh(model_path.resolve(), "validated_twin_guide_mesh")
    model_bvh = build_bvh(model_mesh)
    template_bvh = build_bvh(case.input_meshes.template_mesh)
    sleeve_bvhs = tuple(build_bvh(sleeve) for sleeve in reference_sleeves)
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
        _guide_retention_result(model_bvh, case, reference_sleeves),
        _connector_result(
            model_bvh,
            template_bvh,
            sleeve_bvhs,
            case,
            connector_plan,
            config.geometry.connector_radius_mm,
        ),
    ]
    if connector_plan.connector_guide_endpoint is not None:
        results.append(
            _connector_endpoint_reinforcement_result(
                model_bvh,
                case.input_meshes.template_mesh,
                case,
                connector_plan,
            )
        )
    if connector_plan.press_beam_links:
        results.append(
            _press_beam_result(
                model_bvh,
                case.input_meshes.template_mesh,
                case,
                connector_plan,
            )
        )
    if connector_plan.terminal_distal_common_node is not None:
        results.append(
            _terminal_distal_common_node_result(
                model_bvh,
                case,
                connector_plan,
                tooth_identification,
            )
        )
    if connector_plan.guide_component_bridge is not None:
        results.append(
            _guide_component_bridge_result(
                model_bvh,
                template_bvh,
                case,
                connector_plan,
            )
        )
    if connector_plan.guide_terminal_u_extension is not None:
        results.append(
            _guide_terminal_u_extension_result(
                model_bvh,
                template_bvh,
                case,
                connector_plan,
                tooth_identification,
            )
        )
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
        point_inside_mesh(model_bvh, point)
        and not (
            (side := nearest_mesh_surface_side(model_bvh, point)) is not None
            and side > 1e-6
        )
        for point in channel_probe_points
    )
    input_geometry_mode = config.sleeve_geometry_mode is SleeveGeometryMode.INPUT
    channel_metrics: dict[str, int | float] = {
        "sample_count": len(channel_probe_points),
        "blocked_sample_count": blocked_channel_count,
        "input_geometry_preserved": int(input_geometry_mode),
        "global_bore_recut_applied": int(
            not input_geometry_mode and connector_plan.recut_sleeve_bore
        ),
        "connector_bore_recut_before_input_sleeves": int(
            input_geometry_mode and connector_plan.recut_sleeve_bore
        ),
        "blocked_input_geometry_requires_source_bore_review": int(
            input_geometry_mode and blocked_channel_count > 0
        ),
    }
    for guide, radius in zip(case.guide_sleeves, usable_bore_radii, strict=True):
        channel_metrics[f"guide_{guide.guide_index}_usable_bore_radius_mm"] = radius
    results.append(
        ValidationResult(
            "channels",
            blocked_channel_count == 0,
            channel_metrics,
        )
    )
    if tooth_identification is not None:
        results.append(
            _observation_window_result(model_bvh, cutout_plan.profile_windows)
        )
    return tuple(results)

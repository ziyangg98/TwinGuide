"""将纯几何阶段计划实体化、布尔运算并导出 STL。"""

from __future__ import annotations

import shutil
from pathlib import Path

import bpy

from twin_guide.blender.booleans import (
    apply_manifold3d_differences,
    repair_manifold3d_stl,
    subtract_cutters,
)
from twin_guide.blender.mesh_builders import (
    assign_material,
    create_axis_cylinder,
    create_centerline_tube,
    create_conformal_fusion_foot,
    create_dual_root_tapered_centerline_tube,
    create_root_tapered_centerline_tube,
    create_window_cutter,
    voxel_union,
)
from twin_guide.blender.mesh_queries import (
    clean_mesh,
    remove_excess_components,
)
from twin_guide.blender.rendering import create_materials, render_objects
from twin_guide.blender.scene import duplicate_mesh_object, remove_object
from twin_guide.blender.sleeve_reconstruction import create_closed_sleeve_object
from twin_guide.blender.stl_io import (
    export_stl_mesh,
    import_polygon_mesh,
    import_stl_mesh,
)
from twin_guide.clearance_adjustment import HandpieceAvoidancePlan
from twin_guide.geometry import Vec3
from twin_guide.models import (
    BuildArtifacts,
    CaseAnalysis,
    CutoutPlan,
    WindowPurpose,
)
from twin_guide.point_linking import PointLinkingPlan
from twin_guide.types import ConnectorEndpointSource

GENERATED_SUFFIXES = {".png", ".stl"}
MAIN_CONNECTOR_PREFIXES = (
    "point_link_",
    "connector_root_bulb_",
    "connector_conformal_foot_",
    "guide_component_bridge_",
    "guide_terminal_u_extension_",
)


def _clear_generated_artifacts(output_directory: Path) -> None:
    """删除上一次公开产物和已废弃的缓存布局。"""

    for artifact_path in output_directory.iterdir():
        if (
            artifact_path.is_file()
            and artifact_path.suffix.lower() in GENERATED_SUFFIXES
            and artifact_path.name != "stage-02-tooth-mapping.png"
            and artifact_path.name != "ui-task.json"
        ):
            artifact_path.unlink()

    cache_root = output_directory / ".cache"
    legacy_directories = (
        output_directory / "observation_window_opening",
        cache_root / "stage-overviews",
        cache_root / "stage-02-tooth-mapping" / "recognition",
        cache_root / "stage-02-tooth-mapping" / "guide_mapping",
    )
    for directory in legacy_directories:
        if directory.is_dir():
            shutil.rmtree(directory)
    legacy_stage_2_overview = (
        cache_root / "stage-02-tooth-mapping" / "overview.png"
    )
    if legacy_stage_2_overview.is_file():
        legacy_stage_2_overview.unlink()
    stage_7_cache = cache_root / "stage-07-clearance-adjustment"
    if stage_7_cache.is_dir():
        for child in stage_7_cache.iterdir():
            if child.is_dir() and child.name != "handpieces":
                shutil.rmtree(child)


def _create_channel_cutters(
    cutout_plan: CutoutPlan,
    material: bpy.types.Material,
) -> tuple[bpy.types.Object, ...]:
    """将第 3 步圆柱通道计划转换为 Blender 切割体。"""

    return tuple(
        create_axis_cylinder(
            channel.name,
            channel.start,
            channel.end,
            channel.radius_mm,
            material,
        )
        for channel in cutout_plan.channels
    )


def _create_generated_sleeves(
    case: CaseAnalysis,
    material: bpy.types.Material,
) -> tuple[bpy.types.Object, ...]:
    """按输入位姿和病例固定参数重建标准导管。"""

    return tuple(
        assign_material(
            create_closed_sleeve_object(
                guide.parameters,
                f"generated_sleeve_{guide.guide_index}",
            ),
            material,
        )
        for guide in case.guide_sleeves
    )


def _trim_main_connectors_against_dentition(
    link_meshes: tuple[bpy.types.Object, ...],
    dentition_mesh: bpy.types.Object,
    clearance_mm: float,
    voxel_size_mm: float,
    material: bpy.types.Material,
) -> tuple[bpy.types.Object, ...]:
    """在导板融合前裁掉主连接梁进入牙体保护空间的部分。"""

    connector_meshes = tuple(
        mesh
        for mesh in link_meshes
        if mesh.name.startswith(MAIN_CONNECTOR_PREFIXES)
    )
    if not connector_meshes:
        return link_meshes
    other_meshes = tuple(mesh for mesh in link_meshes if mesh not in connector_meshes)
    connector_union = voxel_union(
        connector_meshes,
        "main_connectors_before_dental_trim",
        voxel_size_mm,
        material,
    )
    for mesh in connector_meshes:
        remove_object(mesh)
    trimmed = apply_manifold3d_differences(
        connector_union,
        (dentition_mesh,),
        cutter_clearance_mm=clearance_mm,
    )
    trimmed.name = "main_connectors_dental_trimmed"
    assign_material(trimmed, material)
    return (trimmed, *other_meshes)


def _create_window_cutters(
    cutout_plan: CutoutPlan,
    materials: dict[str, bpy.types.Material],
) -> tuple[bpy.types.Object, ...]:
    """将操作窗和观察窗计划转换为带材质的 Blender 切割体。"""

    return tuple(
        create_window_cutter(
            window,
            materials["operation" if window.purpose is WindowPurpose.OPERATION else "observation"],
        )
        for window in cutout_plan.windows
    )


def _create_profile_window_cutters(
    cutout_plan: CutoutPlan,
    material: bpy.types.Material,
) -> tuple[bpy.types.Object, ...]:
    """导入第 3 步按本次牙位映射生成的观察窗组合切割体。"""

    return tuple(
        assign_material(
            import_polygon_mesh(profile.cutter_mesh_path, profile.name),
            material,
        )
        for profile in cutout_plan.profile_windows
    )


def create_point_link_meshes(
    plan: PointLinkingPlan,
    material: bpy.types.Material,
    template_mesh: bpy.types.Object | None = None,
) -> tuple[bpy.types.Object, ...]:
    """将第 6 步计划实体化为尚未复切固定孔的扫掠梁。

    参数:
        plan: 第 6 步纯几何连接计划。
        material: 赋给连接管的 Blender 材质。

    返回:
        连续连接梁、可选的三根 Y 型按压梁及汇合球网格。

    算法说明:
        对每条已规划中心线使用平行输运标架扫掠圆形截面并封闭两端。
        固定孔不能在单条梁上提前复切，否则全直径预埋
        的下梁可能在融合前被切断；``build_guide_from_links`` 在全部正向
        融合完成后统一复切最终整体。
    """

    meshes = []
    connector_endpoint = plan.connector_guide_endpoint
    connector_groups: dict[
        tuple[float, float, float],
        tuple[str, Vec3, Vec3, Vec3, list[Vec3]],
    ] = {}
    for index, link in enumerate(plan.links, 1):
        if connector_endpoint is None:
            meshes.append(
                create_centerline_tube(
                    f"point_link_{index}",
                    link.centerline,
                    plan.radius_mm,
                    material,
                    plan.curve_resolution,
                )
            )
            continue
        if template_mesh is None:
            raise ValueError("连接梁导板端增强需要原始导板网格")
        tube_builder = (
            create_root_tapered_centerline_tube
            if plan.terminal_distal_common_node is not None
            else create_dual_root_tapered_centerline_tube
        )
        meshes.append(
            tube_builder(
                f"point_link_{index}",
                link.centerline,
                plan.radius_mm,
                plan.radius_mm * connector_endpoint.root_radius_factor,
                connector_endpoint.transition_length_mm,
                material,
                plan.curve_resolution,
            )
        )
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
            if key not in connector_groups:
                connector_groups[key] = (
                    f"guide_{link.guide_index}_{side}",
                    surface_anchor,
                    surface_normal,
                    center,
                    [],
                )
            connector_groups[key][4].append(incident)
    if plan.terminal_distal_common_node is not None:
        # G 是远中自由节点，不是牙龈表面锚点。主梁仍需对牙列执行避让，
        # 但末端导管 P -> G 的闭合段位于无牙导板区，必须显式保留。
        for index, link in enumerate(plan.links, 1):
            contact_indices = link.contact_indices or (link.contact_index,)
            if link.right_source is ConnectorEndpointSource.DISTAL_COMMON_NODE:
                closure_centerline = link.centerline[contact_indices[-1] :]
            elif link.left_source is ConnectorEndpointSource.DISTAL_COMMON_NODE:
                closure_centerline = link.centerline[: contact_indices[0] + 1]
            else:
                continue
            if len(closure_centerline) < 2:
                raise ValueError("远中公共节点闭合段至少需要两个中心线采样点")
            meshes.append(
                create_centerline_tube(
                    f"terminal_distal_closure_{index}",
                    closure_centerline,
                    plan.radius_mm,
                    material,
                    plan.curve_resolution,
                )
            )
    if connector_endpoint is not None:
        assert template_mesh is not None
        for label, surface_anchor, surface_normal, center, incidents in (
            connector_groups.values()
        ):
            incident = sum(incidents, Vec3(0.0, 0.0, 0.0)).normalized()
            bulb_center = (
                center
                - incident * connector_endpoint.bulb_forward_offset_mm
            )
            bpy.ops.mesh.primitive_ico_sphere_add(
                subdivisions=3,
                radius=plan.radius_mm * connector_endpoint.bulb_radius_factor,
                location=bulb_center.as_tuple(),
            )
            root_bulb = bpy.context.object
            root_bulb.name = f"connector_root_bulb_{label}"
            meshes.append(assign_material(root_bulb, material))
            meshes.append(
                create_conformal_fusion_foot(
                    f"connector_conformal_foot_{label}",
                    template_mesh,
                    surface_anchor,
                    surface_normal,
                    incident,
                    connector_endpoint.foot_major_radius_mm,
                    connector_endpoint.foot_minor_radius_mm,
                    connector_endpoint.foot_peak_height_mm,
                    connector_endpoint.foot_embed_depth_mm,
                    material,
                )
            )
    if plan.terminal_distal_common_node is not None:
        terminal = plan.terminal_distal_common_node
        bpy.ops.mesh.primitive_ico_sphere_add(
            subdivisions=3,
            radius=terminal.node_radius_mm,
            location=terminal.centerline_node.as_tuple(),
        )
        node = bpy.context.object
        node.name = "terminal_distal_common_node"
        meshes.append(assign_material(node, material))
    bridge = plan.guide_component_bridge
    if bridge is not None:
        endpoint = bridge.endpoint_reinforcement
        for index, link in enumerate(bridge.links, 1):
            link_name = f"guide_component_bridge_link_{index}_{link.side}"
            if endpoint is None:
                meshes.append(
                    create_centerline_tube(
                        link_name,
                        link.centerline,
                        bridge.radius_mm,
                        material,
                        plan.curve_resolution,
                    )
                )
            else:
                if template_mesh is None:
                    raise ValueError("断裂导板预连接端强化需要原始导板网格")
                meshes.append(
                    create_dual_root_tapered_centerline_tube(
                        link_name,
                        link.centerline,
                        bridge.radius_mm,
                        bridge.radius_mm * endpoint.root_radius_factor,
                        endpoint.transition_length_mm,
                        material,
                        plan.curve_resolution,
                    )
                )
                endpoints = (
                    (
                        "start",
                        link.start_surface_anchor,
                        link.start_surface_normal,
                        link.centerline[0],
                        link.centerline[1] - link.centerline[0],
                    ),
                    (
                        "end",
                        link.end_surface_anchor,
                        link.end_surface_normal,
                        link.centerline[-1],
                        link.centerline[-2] - link.centerline[-1],
                    ),
                )
                for side, surface_anchor, surface_normal, center, incident in endpoints:
                    incident = incident.normalized()
                    bulb_center = center - incident * endpoint.bulb_forward_offset_mm
                    bpy.ops.mesh.primitive_ico_sphere_add(
                        subdivisions=3,
                        radius=bridge.radius_mm * endpoint.bulb_radius_factor,
                        location=bulb_center.as_tuple(),
                    )
                    bulb = bpy.context.object
                    bulb.name = f"guide_component_bridge_bulb_{index}_{side}"
                    meshes.append(assign_material(bulb, material))
                    meshes.append(
                        create_conformal_fusion_foot(
                            f"guide_component_bridge_foot_{index}_{side}",
                            template_mesh,
                            surface_anchor,
                            surface_normal,
                            incident,
                            endpoint.foot_major_radius_mm,
                            endpoint.foot_minor_radius_mm,
                            endpoint.foot_peak_height_mm,
                            endpoint.foot_embed_depth_mm,
                            material,
                        )
                    )
    terminal_u = plan.guide_terminal_u_extension
    if terminal_u is not None:
        endpoint = terminal_u.endpoint_reinforcement
        link_name = "guide_terminal_u_extension_link"
        if endpoint is None:
            meshes.append(
                create_centerline_tube(
                    link_name,
                    terminal_u.centerline,
                    terminal_u.radius_mm,
                    material,
                    plan.curve_resolution,
                )
            )
        else:
            if template_mesh is None:
                raise ValueError("末端 U 型延伸梁根部强化需要原始导板网格")
            meshes.append(
                create_dual_root_tapered_centerline_tube(
                    link_name,
                    terminal_u.centerline,
                    terminal_u.radius_mm,
                    terminal_u.radius_mm * endpoint.root_radius_factor,
                    endpoint.transition_length_mm,
                    material,
                    plan.curve_resolution,
                )
            )
            terminal_endpoints = (
                (
                    "u_side",
                    terminal_u.u_surface_anchor,
                    terminal_u.u_surface_normal,
                    terminal_u.centerline[0],
                    terminal_u.centerline[1] - terminal_u.centerline[0],
                ),
                (
                    "back_u_side",
                    terminal_u.back_u_surface_anchor,
                    terminal_u.back_u_surface_normal,
                    terminal_u.centerline[-1],
                    terminal_u.centerline[-2] - terminal_u.centerline[-1],
                ),
            )
            for side, surface_anchor, surface_normal, center, incident in terminal_endpoints:
                incident = incident.normalized()
                bulb_center = center - incident * endpoint.bulb_forward_offset_mm
                bpy.ops.mesh.primitive_ico_sphere_add(
                    subdivisions=3,
                    radius=terminal_u.radius_mm * endpoint.bulb_radius_factor,
                    location=bulb_center.as_tuple(),
                )
                bulb = bpy.context.object
                bulb.name = f"guide_terminal_u_extension_bulb_{side}"
                meshes.append(assign_material(bulb, material))
                meshes.append(
                    create_conformal_fusion_foot(
                        f"guide_terminal_u_extension_foot_{side}",
                        template_mesh,
                        surface_anchor,
                        surface_normal,
                        incident,
                        endpoint.foot_major_radius_mm,
                        endpoint.foot_minor_radius_mm,
                        endpoint.foot_peak_height_mm,
                        endpoint.foot_embed_depth_mm,
                        material,
                    )
                )
    if plan.press_beam_links:
        if plan.press_beam_radius_mm is None or plan.press_beam_junction is None:
            raise ValueError("按压梁计划缺少半径或汇合点")
        endpoint = plan.press_beam_guide_endpoint
        for index, link in enumerate(plan.press_beam_links, 1):
            link_name = f"press_beam_link_{index}_{link.label}"
            if link.source != "tooth_section_trajectory" or endpoint is None:
                meshes.append(
                    create_centerline_tube(
                        link_name,
                        link.centerline,
                        plan.press_beam_radius_mm,
                        material,
                        plan.curve_resolution,
                    )
                )
                continue
            if template_mesh is None:
                raise ValueError("按压梁导板端增强需要原始导板网格")
            meshes.append(
                create_root_tapered_centerline_tube(
                    link_name,
                    link.centerline,
                    plan.press_beam_radius_mm,
                    plan.press_beam_radius_mm * endpoint.root_radius_factor,
                    endpoint.transition_length_mm,
                    material,
                    plan.curve_resolution,
                )
            )
            outward_tangent = (link.start - link.centerline[1]).normalized()
            bulb_center = (
                link.start
                + outward_tangent * endpoint.bulb_forward_offset_mm
            )
            bpy.ops.mesh.primitive_ico_sphere_add(
                subdivisions=3,
                radius=plan.press_beam_radius_mm * endpoint.bulb_radius_factor,
                location=bulb_center.as_tuple(),
            )
            root_bulb = bpy.context.object
            root_bulb.name = f"press_beam_root_bulb_{link.label}"
            meshes.append(assign_material(root_bulb, material))
            meshes.append(
                create_conformal_fusion_foot(
                    f"press_beam_conformal_foot_{link.label}",
                    template_mesh,
                    link.surface_anchor,
                    link.surface_normal,
                    link.end - link.start,
                    endpoint.foot_major_radius_mm,
                    endpoint.foot_minor_radius_mm,
                    endpoint.foot_peak_height_mm,
                    endpoint.foot_embed_depth_mm,
                    material,
                )
            )
        bpy.ops.mesh.primitive_ico_sphere_add(
            subdivisions=3,
            radius=(
                plan.press_beam_radius_mm
                * plan.press_beam_junction_radius_factor
            ),
            location=plan.press_beam_junction.as_tuple(),
        )
        junction = bpy.context.object
        junction.name = "press_beam_y_junction"
        meshes.append(assign_material(junction, material))
    return tuple(meshes)


def _create_link_point_markers(
    plan: PointLinkingPlan,
    materials: dict[str, bpy.types.Material],
) -> tuple[tuple[bpy.types.Object, ...], tuple[bpy.types.Object, ...]]:
    """为联建选点图创建导管侧和导板侧标记球。"""

    marker_radius = min(0.80, plan.radius_mm * 0.45)

    def markers(kind: str, points: tuple[object, ...]) -> tuple[bpy.types.Object, ...]:
        """将去重后的一类选点转换为标记球。"""

        unique: dict[tuple[float, float, float], object] = {}
        for point in points:
            unique[point.as_tuple()] = point
        result = []
        for index, point in enumerate(unique.values(), 1):
            bpy.ops.mesh.primitive_uv_sphere_add(
                segments=32,
                ring_count=16,
                radius=marker_radius,
                location=point.as_tuple(),
            )
            marker = bpy.context.object
            marker.name = f"{kind}_point_{index}"
            result.append(assign_material(marker, materials[f"{kind}_point"]))
        return tuple(result)

    sleeve_markers = markers(
        "sleeve",
        (
            *(
                point
                for link in plan.links
                for point in (link.tube_contacts or (link.tube_contact,))
            ),
            *(
                link.surface_anchor
                for link in plan.press_beam_links
                if link.source == "inner_sleeve_upper"
            ),
        ),
    )
    template_markers = markers(
        "template",
        (
            point
            for link in plan.links
            for point, source in (
                (link.left_surface_anchor, link.left_source),
                (link.right_surface_anchor, link.right_source),
            )
            if source is ConnectorEndpointSource.TEMPLATE
        ),
    )
    if plan.press_beam_links:
        template_markers += markers(
            "template",
            tuple(
                link.surface_anchor
                for link in plan.press_beam_links
                if link.source == "tooth_section_trajectory"
            ),
        )
    if plan.guide_component_bridge is not None:
        bridge = plan.guide_component_bridge
        template_markers += markers(
            "template",
            tuple(
                anchor
                for link in bridge.links
                for anchor in (
                    link.start_surface_anchor,
                    link.end_surface_anchor,
                )
            ),
        )
    if plan.guide_terminal_u_extension is not None:
        terminal_u = plan.guide_terminal_u_extension
        template_markers += markers(
            "template",
            (
                terminal_u.u_surface_anchor,
                terminal_u.back_u_surface_anchor,
            ),
        )
    return sleeve_markers, template_markers


def _create_stage_trajectory_meshes(
    plan: PointLinkingPlan,
    material: bpy.types.Material,
) -> tuple[tuple[bpy.types.Object, ...], tuple[bpy.types.Object, ...]]:
    """分别实体化锚点选择和按压梁规划轨迹。"""

    def build(
        prefix: str,
        trajectories: tuple[tuple[Vec3, ...], ...],
    ) -> tuple[bpy.types.Object, ...]:
        """将一组轨迹构造为带阶段前缀的诊断细管。"""

        return tuple(
            create_centerline_tube(
                f"{prefix}_{index}",
                trajectory,
                0.24,
                material,
                16,
            )
            for index, trajectory in enumerate(trajectories, 1)
            if len(trajectory) >= 2
        )

    anchor_trajectories = (
        *plan.anchor_trajectories,
        *(
            plan.guide_component_bridge.trajectories
            if plan.guide_component_bridge is not None
            else ()
        ),
        *(
            plan.guide_terminal_u_extension.trajectories
            if plan.guide_terminal_u_extension is not None
            else ()
        ),
    )
    return (
        build("anchor_trajectory", anchor_trajectories),
        build("press_beam_trajectory", plan.press_beam_trajectories),
    )


def _render_process_images(
    output_directory: Path,
    case: CaseAnalysis,
    cut_template_mesh: bpy.types.Object,
    sleeve_meshes: tuple[bpy.types.Object, ...],
    connector_meshes: tuple[bpy.types.Object, ...],
    sleeve_point_markers: tuple[bpy.types.Object, ...],
    template_point_markers: tuple[bpy.types.Object, ...],
    anchor_trajectory_meshes: tuple[bpy.types.Object, ...],
    press_beam_trajectory_meshes: tuple[bpy.types.Object, ...],
) -> tuple[Path, ...]:
    """渲染输入、标准重建导管、切口、选点和连接结果。"""

    source_assemblies = case.input_meshes.guide_sleeve_assembly_meshes
    accessory_meshes = case.retained_accessory_meshes
    cut_template_preview = duplicate_mesh_object(
        cut_template_mesh,
        "cut_template_preview",
    )
    assign_material(cut_template_preview, cut_template_mesh.data.materials[0])
    press_beam_meshes = tuple(
        mesh for mesh in connector_meshes if mesh.name.startswith("press_beam_")
    )
    image_specs = [
        (
            "stage-01-sleeve-reconstruction.png",
            (*source_assemblies, *sleeve_meshes),
        ),
        (
            "stage-04-anchor-selection.png",
            (
                cut_template_preview,
                *sleeve_meshes,
                *anchor_trajectory_meshes,
                *sleeve_point_markers,
                *template_point_markers,
            ),
        ),
        (
            "stage-06-structure-linking.png",
            (
                cut_template_preview,
                *sleeve_meshes,
                *accessory_meshes,
                *connector_meshes,
            ),
        ),
        (
            "stage-03-cutout-planning.png",
            (cut_template_preview, *sleeve_meshes),
        ),
    ]
    image_specs.append(
        (
            "stage-05-press-beam.png",
            (
                cut_template_preview,
                *sleeve_meshes,
                *press_beam_meshes,
                *press_beam_trajectory_meshes,
                *sleeve_point_markers,
                *template_point_markers,
            ),
        )
    )
    image_paths = []
    for filename, visible_meshes in image_specs:
        image_path = output_directory / filename
        render_objects(image_path, tuple(visible_meshes), case.config.render)
        image_paths.append(image_path)
    remove_object(cut_template_preview)
    return tuple(image_paths)


def _render_handpiece_avoidance(
    output_directory: Path,
    case: CaseAnalysis,
    visible_meshes: tuple[bpy.types.Object, ...],
    plans: tuple[HandpieceAvoidancePlan, ...],
    materials: dict[str, bpy.types.Material],
) -> Path | None:
    """渲染手机摆动包络、旋转轴、枢轴和当前导板结构。"""

    if not plans:
        return None
    envelopes = tuple(
        assign_material(
            import_polygon_mesh(
                plan.envelope_mesh_path,
                f"handpiece_{plan.avoidance_id}_preview",
            ),
            materials["avoidance_envelope"],
        )
        for plan in plans
    )
    axes = []
    pivots = []
    for plan in plans:
        pivot = Vec3(*plan.pivot)
        axis = Vec3(*plan.rotation_axis).normalized()
        axes.append(create_axis_cylinder(
            f"handpiece_{plan.avoidance_id}_rotation_axis",
            pivot - axis * 24.0,
            pivot + axis * 24.0,
            0.35,
            materials["avoidance_axis"],
            48,
        ))
        bpy.ops.mesh.primitive_uv_sphere_add(
            segments=48,
            ring_count=24,
            radius=0.9,
            location=plan.pivot,
        )
        marker = bpy.context.object
        marker.name = f"handpiece_{plan.avoidance_id}_pivot"
        pivots.append(assign_material(marker, materials["avoidance_pivot"]))
    image_path = output_directory / "stage-07-clearance-adjustment.png"
    render_objects(
        image_path,
        (*visible_meshes, *envelopes, *axes, *pivots),
        case.config.render,
    )
    for temporary_object in (*envelopes, *axes, *pivots):
        remove_object(temporary_object)
    return image_path


def build_guide_from_links(
    case: CaseAnalysis,
    cutout_plan: CutoutPlan,
    point_links: PointLinkingPlan,
    handpiece_avoidance: tuple[HandpieceAvoidancePlan, ...] | None = None,
    *,
    preview: bool = False,
) -> BuildArtifacts:
    """使用第 6 步光滑连接计划构造并导出牙科导板。

    参数:
        case: 包含输入网格、导管参数和输出配置的病例分析。
        cutout_plan: 第 3 步通道与窗口计划。
        point_links: 第 6 步光滑连接计划。
        handpiece_avoidance: 可选的第 7 步一个或多个手机左右摆动包络计划。
        preview: 是否省略过程图和最终视图；不改变实体几何。

    返回:
        最终 STL 路径和全部渲染图路径。

    算法说明:
        重建标准导管，将其与导板、附件和连接梁整体融合后
        统一复切导孔和观察窗，再执行手机避让与最终修复。
    """

    output_directory = case.config.output_directory
    fusion_voxel_size_mm = case.config.geometry.fusion_voxel_size_mm
    output_directory.mkdir(parents=True, exist_ok=True)
    _clear_generated_artifacts(output_directory)
    materials = create_materials()
    template_mesh = case.input_meshes.template_mesh
    accessory_meshes = case.retained_accessory_meshes
    assign_material(template_mesh, materials["template"])
    for source_assembly in case.input_meshes.guide_sleeve_assembly_meshes:
        assign_material(source_assembly, materials["source"])
    for guide in case.guide_sleeves:
        assign_material(guide.guide_mesh, materials["sleeve"])
    for accessory_mesh in accessory_meshes:
        assign_material(accessory_mesh, materials["sleeve"])
    sleeve_meshes = _create_generated_sleeves(case, materials["sleeve"])
    channel_cutters = _create_channel_cutters(cutout_plan, materials["channel"])
    analytic_window_cutters = _create_window_cutters(cutout_plan, materials)
    profile_window_cutters = _create_profile_window_cutters(
        cutout_plan,
        materials["observation"],
    )
    window_cutters = (*analytic_window_cutters, *profile_window_cutters)
    link_meshes = create_point_link_meshes(
        point_links,
        materials["connector"],
        template_mesh,
    )
    if point_links.trim_against_dentition:
        link_meshes = _trim_main_connectors_against_dentition(
            link_meshes,
            case.input_meshes.patient_dentition_mesh,
            case.config.geometry.connector_dental_clearance_mm,
            fusion_voxel_size_mm,
            materials["connector"],
        )
    process_image_paths = ()
    if not preview:
        sleeve_point_markers, template_point_markers = _create_link_point_markers(
            point_links, materials
        )
        anchor_trajectory_meshes, press_beam_trajectory_meshes = (
            _create_stage_trajectory_meshes(
                point_links, materials["template_point"]
            )
        )
    cut_template_mesh = subtract_cutters(
        duplicate_mesh_object(template_mesh, "cut_template_for_build"),
        (*channel_cutters, *window_cutters),
    )
    if not preview:
        process_image_paths = _render_process_images(
            output_directory,
            case,
            cut_template_mesh,
            sleeve_meshes,
            link_meshes,
            sleeve_point_markers,
            template_point_markers,
            anchor_trajectory_meshes,
            press_beam_trajectory_meshes,
        )
        handpiece_image = _render_handpiece_avoidance(
            output_directory,
            case,
            (template_mesh, *sleeve_meshes, *link_meshes),
            handpiece_avoidance or (),
            materials,
        )
        if handpiece_image is not None:
            process_image_paths = (*process_image_paths, handpiece_image)
        tooth_mapping_image = output_directory / "stage-02-tooth-mapping.png"
        if tooth_mapping_image.is_file():
            process_image_paths = (*process_image_paths, tooth_mapping_image)
    recut_cutters = (
        (*channel_cutters, *profile_window_cutters)
        if point_links.recut_sleeve_bore
        else profile_window_cutters
    )
    final_mesh = voxel_union(
        (cut_template_mesh, *sleeve_meshes, *accessory_meshes, *link_meshes),
        "twin_guide_mesh",
        fusion_voxel_size_mm,
        materials["final"],
    )
    if recut_cutters:
        final_mesh = voxel_union(
            (final_mesh,),
            "twin_guide_pre_recut_mesh",
            fusion_voxel_size_mm,
            materials["final"],
        )
        final_mesh = apply_manifold3d_differences(
            final_mesh,
            recut_cutters,
        )
        remove_excess_components(final_mesh, 1)
    else:
        remove_excess_components(final_mesh, 1)
        clean_mesh(final_mesh)
    for avoidance in handpiece_avoidance or ():
        handpiece_cutter = import_polygon_mesh(
            avoidance.envelope_mesh_path,
            f"handpiece_{avoidance.avoidance_id}_current_depth_lr_sweep_cutter",
        )
        final_mesh = apply_manifold3d_differences(
            final_mesh,
            (handpiece_cutter,),
            cutter_clearance_mm=avoidance.extra_clearance_mm,
            simplify_tolerance_mm=0.0,
        )
        remove_excess_components(final_mesh, 1)
        remove_object(handpiece_cutter)
    requires_serialized_repair = bool(recut_cutters or handpiece_avoidance)
    assign_material(final_mesh, materials["final"])
    model_path = output_directory / "twin_guide.stl"
    export_stl_mesh(model_path, final_mesh)
    if requires_serialized_repair:
        # 最终网格来自体素尺寸控制的规则化；STL 序列化偶发坏边只允许
        # 在一个体素以内局部折叠，修复尺度不超过上游离散精度。
        repair_manifold3d_stl(
            model_path,
            fusion_voxel_size_mm,
        )
        remove_object(final_mesh)
        final_mesh = import_stl_mesh(model_path, "twin_guide_repaired_mesh")
        assign_material(final_mesh, materials["final"])
    final_image_paths = []
    if not preview:
        for view_name in ("iso", "top", "bottom", "side"):
            image_path = output_directory / f"guide_{view_name}.png"
            render_objects(image_path, (final_mesh,), case.config.render, view_name)
            final_image_paths.append(image_path)
    return BuildArtifacts(model_path, (*final_image_paths, *process_image_paths))

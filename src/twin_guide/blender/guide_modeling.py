"""将纯几何阶段计划实体化、布尔运算并导出 STL。"""

from __future__ import annotations

from pathlib import Path

import bpy

from twin_guide.blender.booleans import subtract_cutters
from twin_guide.blender.mesh_builders import (
    assign_material,
    create_axis_cylinder,
    create_bezier_tube,
    create_window_cutter,
    voxel_union,
)
from twin_guide.blender.mesh_queries import (
    clean_mesh,
    remove_excess_components,
)
from twin_guide.blender.rendering import create_materials, render_objects
from twin_guide.blender.scene import remove_object
from twin_guide.blender.sleeve_reconstruction import create_closed_sleeve_object
from twin_guide.blender.stl_io import export_stl_mesh
from twin_guide.models import (
    BuildArtifacts,
    CaseAnalysis,
    CutoutPlan,
    WindowPurpose,
)
from twin_guide.point_linking import PointLinkingPlan

GENERATED_SUFFIXES = {".png", ".stl"}


def _clear_generated_artifacts(output_directory: Path) -> None:
    """删除输出目录中上一次生成的 PNG 和 STL 产物。"""

    for artifact_path in output_directory.iterdir():
        if artifact_path.is_file() and artifact_path.suffix.lower() in GENERATED_SUFFIXES:
            artifact_path.unlink()


def _create_channel_cutters(cutout_plan: CutoutPlan) -> tuple[bpy.types.Object, ...]:
    """将第 3 步圆柱通道计划转换为 Blender 切割体。"""

    return tuple(
        create_axis_cylinder(channel.name, channel.start, channel.end, channel.radius_mm)
        for channel in cutout_plan.channels
    )


def _create_bore_cutters(case: CaseAnalysis) -> tuple[bpy.types.Object, ...]:
    """按导套轴线和固定孔半径构造带轴向余量的复切圆柱。"""

    margin_mm = case.config.geometry.channel_axial_margin_mm
    return tuple(
        create_axis_cylinder(
            f"guide_{guide.guide_index}_bore_cutter",
            guide.center + guide.axis * (guide.axial_min_mm - margin_mm),
            guide.center + guide.axis * (guide.axial_max_mm + margin_mm),
            guide.bore_radius_mm,
        )
        for guide in case.guide_sleeves
    )


def _create_reconstructed_sleeves(
    case: CaseAnalysis,
    material: bpy.types.Material,
) -> tuple[bpy.types.Object, bpy.types.Object]:
    """按第 1 步参数重建后续统一使用的完整导套实体。"""

    sleeves = tuple(
        assign_material(
            create_closed_sleeve_object(
                guide.parameters,
                f"guide_{guide.guide_index}_reconstructed_sleeve",
            ),
            material,
        )
        for guide in case.guide_sleeves
    )
    return sleeves[0], sleeves[1]


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


def create_point_link_meshes(
    case: CaseAnalysis,
    plan: PointLinkingPlan,
    material: bpy.types.Material,
) -> tuple[bpy.types.Object, ...]:
    """将第 6 步计划实体化为光滑曲线管，并复切导套固定孔。

    参数:
        case: 用于构造固定孔切割体的病例分析。
        plan: 第 6 步纯几何连接计划。
        material: 赋给连接管的 Blender 材质。

    返回:
        与 ``plan.links`` 顺序一致的曲线管网格。

    算法说明:
        对每条连接，函数先用四个控制点创建 Blender 贝塞尔曲线，
        设置倒角半径、曲线分辨率和圆形端盖，然后转换为网格。
        ``recut_sleeve_bore=True`` 时，再用该连接所属导套的固定孔圆柱
        对曲线管做差集。复切在每条曲线管上单独完成，
        不改写输入计划。
    """

    bore_cutters = _create_bore_cutters(case)
    cutters_by_guide = {
        guide.guide_index: cutter
        for guide, cutter in zip(case.guide_sleeves, bore_cutters, strict=True)
    }
    links = []
    for index, link in enumerate(plan.links, 1):
        raw_link = create_bezier_tube(
            f"point_link_{index}",
            link.control_points,
            plan.radius_mm,
            material,
            plan.curve_resolution,
        )
        cutters = (cutters_by_guide[link.guide_index],) if plan.recut_sleeve_bore else ()
        links.append(subtract_cutters(raw_link, cutters) if cutters else raw_link)
    for cutter in bore_cutters:
        remove_object(cutter)
    return tuple(links)


def _render_process_images(
    output_directory: Path,
    case: CaseAnalysis,
    sleeve_meshes: tuple[bpy.types.Object, ...],
    connector_meshes: tuple[bpy.types.Object, ...],
    channel_cutters: tuple[bpy.types.Object, ...],
    window_cutters: tuple[bpy.types.Object, ...],
) -> tuple[Path, ...]:
    """渲染导套装配、连接结构和切口三张过程图。"""

    template_mesh = case.input_meshes.template_mesh
    accessory_meshes = case.retained_accessory_meshes
    image_specs = (
        ("guide_assembly.png", (template_mesh, *sleeve_meshes, *accessory_meshes)),
        (
            "guide_connectors.png",
            (template_mesh, *sleeve_meshes, *accessory_meshes, *connector_meshes),
        ),
        ("cutouts.png", (template_mesh, *channel_cutters, *window_cutters)),
    )
    image_paths = []
    for filename, visible_meshes in image_specs:
        image_path = output_directory / filename
        render_objects(image_path, tuple(visible_meshes), case.config.render)
        image_paths.append(image_path)
    return tuple(image_paths)


def build_guide_from_links(
    case: CaseAnalysis,
    cutout_plan: CutoutPlan,
    point_links: PointLinkingPlan,
) -> BuildArtifacts:
    """使用第 6 步光滑连接计划构造并导出正式牙科导板。

    参数:
        case: 包含输入网格、导套参数和输出配置的病例分析。
        cutout_plan: 第 3 步通道与窗口计划。
        point_links: 第 6 步光滑连接计划。

    返回:
        最终 STL 路径和全部渲染图路径。

    算法说明:
        函数依次重建导套、创建通道和窗口切割体、实体化曲线并复切
        固定孔、从牙科导板扣除切口，再将已切牙科导板、导套、保留附件和连接管
        做一次体素融合。最后保留主连通分量、清理网格并导出 STL 和四视图。
    """

    output_directory = case.config.output_directory
    output_directory.mkdir(parents=True, exist_ok=True)
    _clear_generated_artifacts(output_directory)
    materials = create_materials()
    template_mesh = case.input_meshes.template_mesh
    accessory_meshes = case.retained_accessory_meshes
    assign_material(template_mesh, materials["final"])
    for accessory_mesh in accessory_meshes:
        assign_material(accessory_mesh, materials["final"])
    sleeve_meshes = _create_reconstructed_sleeves(case, materials["final"])
    channel_cutters = _create_channel_cutters(cutout_plan)
    window_cutters = _create_window_cutters(cutout_plan, materials)
    link_meshes = create_point_link_meshes(case, point_links, materials["connector"])
    process_image_paths = _render_process_images(
        output_directory,
        case,
        sleeve_meshes,
        link_meshes,
        channel_cutters,
        window_cutters,
    )
    cut_template_mesh = subtract_cutters(template_mesh, (*channel_cutters, *window_cutters))
    final_mesh = voxel_union(
        (cut_template_mesh, *sleeve_meshes, *accessory_meshes, *link_meshes),
        "twin_guide_mesh",
        case.config.geometry.fusion_voxel_size_mm,
        materials["final"],
    )
    remove_excess_components(final_mesh, 1)
    clean_mesh(final_mesh)
    assign_material(final_mesh, materials["final"])
    model_path = output_directory / "twin_guide.stl"
    export_stl_mesh(model_path, final_mesh)
    final_image_paths = []
    for view_name in ("iso", "top", "bottom", "side"):
        image_path = output_directory / f"guide_{view_name}.png"
        render_objects(image_path, (final_mesh,), case.config.render, view_name)
        final_image_paths.append(image_path)
    return BuildArtifacts(model_path, (*final_image_paths, *process_image_paths))

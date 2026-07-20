"""读取输入网格，并分析各阶段共用的病例几何。"""

from __future__ import annotations

import bpy

from twin_guide.blender.mesh_queries import (
    build_bvh,
    mesh_bounds,
    mesh_triangles,
    sample_mesh_surface,
    separate_connected_components,
)
from twin_guide.blender.scene import clear_scene, duplicate_mesh_object
from twin_guide.blender.stl_io import import_stl_mesh
from twin_guide.config import CaseConfig
from twin_guide.errors import GeometryError
from twin_guide.geometry import (
    Vec3,
    mean_point,
    volume_centroid,
)
from twin_guide.models import (
    CaseAnalysis,
    GenerationMeshes,
    GuideSleeve,
    OperationFeature,
    SurfaceSample,
)
from twin_guide.sleeve_generation import (
    SleeveGenerationInputs,
    recognize_and_build_sleeves,
)


def _load_generation_meshes(config: CaseConfig) -> GenerationMeshes:
    """导入病例配置中的三个 STL 网格。"""

    return GenerationMeshes(
        template_mesh=import_stl_mesh(config.inputs.template, "template_mesh"),
        guide_sleeve_assembly_mesh=import_stl_mesh(
            config.inputs.guide_sleeve_assembly, "guide_sleeve_assembly_mesh"
        ),
        patient_dentition_mesh=import_stl_mesh(
            config.inputs.patient_dentition, "patient_dentition_mesh"
        ),
    )


def _template_center(
    template_mesh: bpy.types.Object, template_samples: tuple[SurfaceSample, ...]
) -> Vec3:
    """优先使用体积重心，重心异常时退化为表面样本均值。"""

    center = volume_centroid(mesh_triangles(template_mesh))
    lower, upper = mesh_bounds(template_mesh)
    inside_bounds = all(
        minimum - 1e-5 <= coordinate <= maximum + 1e-5
        for coordinate, minimum, maximum in zip(
            center.as_tuple(), lower.as_tuple(), upper.as_tuple(), strict=True
        )
    )
    return center if inside_bounds else mean_point([sample.position for sample in template_samples])


def _select_accessory_meshes(
    components: tuple[bpy.types.Object, ...],
    guide_sleeves: tuple[GuideSleeve, GuideSleeve],
) -> tuple[bpy.types.Object, ...]:
    """只保留后续构建需要的装配体分量。"""
    del components, guide_sleeves
    return ()


def _measure_operation_feature(
    components: tuple[bpy.types.Object, ...],
    guide_sleeves: tuple[GuideSleeve, GuideSleeve],
) -> OperationFeature:
    """测量两个导套附近用于操作窗定位的紧凑圆形分量。"""

    guide_meshes = {guide.guide_mesh for guide in guide_sleeves}
    guide_midpoint = (guide_sleeves[0].center + guide_sleeves[1].center) * 0.5
    candidates = []
    for component in components:
        if component in guide_meshes:
            continue
        lower, upper = mesh_bounds(component)
        spans = sorted((upper - lower).as_tuple())
        if spans[0] <= 1e-6 or spans[2] / spans[0] > 2.0:
            continue
        center = (lower + upper) * 0.5
        candidates.append((center.distance_to(guide_midpoint), center, spans[2]))
    if not candidates:
        raise GeometryError("无法识别操作窗对应的圆形结构")
    _, center, diameter_mm = min(candidates, key=lambda candidate: candidate[0])
    return OperationFeature(center, diameter_mm)


def analyze_case(config: CaseConfig) -> CaseAnalysis:
    """读取病例网格，并计算后续阶段共用的几何数据。"""

    clear_scene()
    input_meshes = _load_generation_meshes(config)
    template_samples = sample_mesh_surface(input_meshes.template_mesh)
    if not template_samples:
        raise GeometryError("牙科导板网格不存在可采样表面")
    template_bvh = build_bvh(input_meshes.template_mesh)
    assembly_working_mesh = duplicate_mesh_object(
        input_meshes.guide_sleeve_assembly_mesh,
        "guide_sleeve_assembly_components",
    )
    components = separate_connected_components(assembly_working_mesh)
    center = _template_center(input_meshes.template_mesh, template_samples)
    sleeve_generation = recognize_and_build_sleeves(
        SleeveGenerationInputs(
            components=components,
            template_bvh=template_bvh,
            template_samples=template_samples,
            template_center=center,
            sleeve_parameters=config.sleeve,
        )
    )
    selected_guides = sleeve_generation.sleeves
    if sleeve_generation.template_frame is None:
        raise GeometryError("导套识别未生成牙科导板局部坐标系")
    template_frame = sleeve_generation.template_frame
    operation_feature = _measure_operation_feature(components, selected_guides)
    return CaseAnalysis(
        config=config,
        input_meshes=input_meshes,
        guide_sleeves=selected_guides,
        retained_accessory_meshes=_select_accessory_meshes(components, selected_guides),
        operation_feature=operation_feature,
        template_frame=template_frame,
        template_samples=template_samples,
    )

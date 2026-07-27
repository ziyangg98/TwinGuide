"""读取输入网格，并分析各阶段共用的病例几何。"""

from __future__ import annotations

from dataclasses import replace

import bpy

from twin_guide.blender.mesh_queries import (
    mesh_bounds,
    mesh_triangles,
    sample_mesh_surface,
    separate_connected_components,
)
from twin_guide.blender.scene import clear_scene, duplicate_mesh_object
from twin_guide.blender.sleeve_estimation_adapter import mesh_object_to_triangle_data
from twin_guide.blender.stl_io import import_stl_mesh
from twin_guide.config import CaseConfig, case_occlusal_axis
from twin_guide.errors import GeometryError
from twin_guide.geometry import (
    Vec3,
    covariance_matrix,
    mean_point,
    symmetric_eigenvectors,
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
        guide_sleeve_assembly_meshes=tuple(
            import_stl_mesh(path, f"guide_sleeve_assembly_mesh_{index:02d}")
            for index, path in enumerate(config.inputs.guide_sleeve_assemblies, 1)
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
    guide_sleeves: tuple[GuideSleeve, ...],
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
    guide_centers = tuple(
        guide.center + guide.axis * (0.5 * guide.length_mm) for guide in guide_sleeves
    )
    guide_midpoint = (guide_centers[0] + guide_centers[1]) * 0.5
    candidates = []
    for component in components:
        if component in guide_meshes:
            continue
        points = mesh_object_to_triangle_data(component).vertices
        origin = mean_point(points)
        axes = tuple(pair[1] for pair in symmetric_eigenvectors(covariance_matrix(points, origin)))
        ranges = []
        center = origin
        for axis in axes:
            coordinates = tuple((point - origin).dot(axis) for point in points)
            lower, upper = min(coordinates), max(coordinates)
            ranges.append(upper - lower)
            center += axis * (0.5 * (lower + upper))
        plane_ratio = ranges[0] / max(ranges[1], 1e-9)
        candidates.append(
            (center.distance_to(guide_midpoint), plane_ratio, center, max(ranges[:2]))
        )
    circular = tuple(candidate for candidate in candidates if candidate[1] <= 1.3)
    _, _, center, diameter_mm = min(circular, key=lambda candidate: candidate[0])
    return OperationFeature(center, diameter_mm)


def _orient_sleeve_into_guide(
    guide: GuideSleeve,
    template_frame: object,
) -> GuideSleeve:
    """统一拟合轴符号，使每个种植位均指向导板内部。

    圆柱拟合轴存在正负二义性。TwinGuide 将 ``axis_origin`` 定义在 C 口
    几何高端，并将正 ``axis`` 定义为指向闭合孔几何低端；若输入装配体
    相反，除反转轴外还需将原点移到另一个物理端面。
    """

    inward = -template_frame.normal
    if guide.axis.dot(inward) >= 0.0:
        return guide
    parameters = replace(
        guide.parameters,
        axis_origin=guide.center + guide.axis * guide.length_mm,
        axis=-guide.axis,
    )
    return replace(guide, parameters=parameters)


def analyze_case(config: CaseConfig) -> CaseAnalysis:
    """读取病例网格，并计算后续阶段共用的几何数据。"""

    clear_scene()
    input_meshes = _load_generation_meshes(config)
    template_samples = sample_mesh_surface(input_meshes.template_mesh)
    if not template_samples:
        raise GeometryError("牙科导板网格不存在可采样表面")
    dentition_samples = sample_mesh_surface(input_meshes.patient_dentition_mesh)
    if not dentition_samples:
        raise GeometryError("患者牙列网格不存在可采样表面")
    center = _template_center(input_meshes.template_mesh, template_samples)
    raw_occlusal_axis = case_occlusal_axis(config)
    occlusal_axis = (
        None if raw_occlusal_axis is None else Vec3(*raw_occlusal_axis).normalized()
    )
    all_components = []
    all_guides = []
    operation_features = []
    template_frame = None
    for assembly_index, source_mesh in enumerate(
        input_meshes.guide_sleeve_assembly_meshes,
        1,
    ):
        assembly_working_mesh = duplicate_mesh_object(
            source_mesh,
            f"guide_sleeve_assembly_components_{assembly_index:02d}",
        )
        components = separate_connected_components(assembly_working_mesh)
        all_components.extend(components)
        sleeve_generation = recognize_and_build_sleeves(
            SleeveGenerationInputs(
                components=components,
                template_samples=template_samples,
                template_center=center,
                sleeve_parameters=config.sleeve,
                sleeve_geometry_mode=config.sleeve_geometry_mode,
                jaw=config.jaw,
                occlusal_axis=occlusal_axis,
            )
        )
        offset = len(all_guides)
        pair = tuple(
            replace(guide, guide_index=offset + local_index)
            for local_index, guide in enumerate(sleeve_generation.sleeves, 1)
        )
        if template_frame is None:
            template_frame = sleeve_generation.template_frame
        pair = tuple(
            _orient_sleeve_into_guide(guide, template_frame) for guide in pair
        )
        if occlusal_axis is not None and any(
            guide.axis.dot(occlusal_axis) >= 0.0 for guide in pair
        ):
            raise GeometryError(
                "导管轴没有指向病例牙合轴的反方向；请检查 case.yaml "
                "anatomy.orientation.occlusal_axis"
            )
        all_guides.extend(pair)
        operation_features.append(_measure_operation_feature(components, pair))
    if template_frame is None:
        raise GeometryError("病例没有可识别的导管装配体")
    selected_guides = tuple(all_guides)
    return CaseAnalysis(
        config=config,
        input_meshes=input_meshes,
        guide_sleeves=selected_guides,
        retained_accessory_meshes=_select_accessory_meshes(tuple(all_components), selected_guides),
        operation_features=tuple(operation_features),
        template_frame=template_frame,
        template_samples=template_samples,
        dentition_samples=dentition_samples,
    )

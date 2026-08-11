"""读取输入网格，并分析各阶段共用的病例几何。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace

import bpy

from twin_guide.blender.mesh_queries import (
    mesh_bounds,
    sample_mesh_surface,
    sample_mesh_surface_and_triangles,
    separate_connected_components,
)
from twin_guide.blender.scene import clear_scene, duplicate_mesh_object, remove_object
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


@dataclass(slots=True)
class _CaseCache:
    """常驻 worker 内复用的只读病例网格与识别结果。"""

    fingerprint: str
    case: CaseAnalysis
    assembly_components: tuple[tuple[bpy.types.Object, ...], ...]


_CASE_CACHE: _CaseCache | None = None


def _case_fingerprint(config: CaseConfig) -> str:
    """计算不包含图形微调值的病例分析指纹。"""

    digest = hashlib.sha256(
        json.dumps(
            {
                "jaw": config.jaw.value,
                "sleeve": asdict(config.sleeve),
                "occlusal_axis": case_occlusal_axis(config),
            },
            sort_keys=True,
        ).encode()
    )
    for path in (
        config.inputs.template,
        *config.inputs.guide_sleeve_assemblies,
        config.inputs.patient_dentition,
    ):
        stat = path.stat()
        digest.update(str(path.resolve()).encode())
        digest.update(f"{stat.st_size}:{stat.st_mtime_ns}".encode())
    return digest.hexdigest()


def _case_with_overrides(
    cached: _CaseCache,
    config: CaseConfig,
) -> CaseAnalysis:
    """复用静态病例分析，并应用当前导柱高度覆盖值。"""

    base = cached.case
    guides = tuple(
        _apply_sleeve_editor_override(config, guide)
        for guide in base.guide_sleeves
    )
    operation_features = []
    offset = 0
    for components in cached.assembly_components:
        pair = guides[offset : offset + 2]
        operation_features.append(_measure_operation_feature(components, pair))
        offset += len(pair)
    return replace(
        base,
        config=config,
        guide_sleeves=guides,
        operation_features=tuple(operation_features),
    )


def _clear_generated_scene_objects(cached: _CaseCache) -> None:
    """保留病例源网格和分量，删除上一次实体任务的临时对象。"""

    case = cached.case
    preserved = {
        case.input_meshes.template_mesh,
        *case.input_meshes.guide_sleeve_assembly_meshes,
        case.input_meshes.patient_dentition_mesh,
        *(item for group in cached.assembly_components for item in group),
    }
    for mesh_object in tuple(bpy.context.scene.objects):
        if mesh_object not in preserved:
            remove_object(mesh_object)


def _apply_sleeve_editor_override(
    config: CaseConfig,
    guide: GuideSleeve,
) -> GuideSleeve:
    """把图形编辑器的单导柱高度覆盖值应用到已识别导柱。"""

    override = config.editor_overrides.sleeve_for(guide.guide_index)
    if override is None:
        return guide
    return replace(
        guide,
        parameters=replace(
            guide.parameters,
            height=override.height_mm,
            platform_height=override.platform_height_mm,
            closed_bore_height=override.closed_bore_height_mm,
        ),
        axial_max_mm=override.height_mm,
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
    template_mesh: bpy.types.Object,
    template_samples: tuple[SurfaceSample, ...],
    template_triangles: tuple[tuple[Vec3, Vec3, Vec3], ...],
) -> Vec3:
    """优先使用体积重心，重心异常时退化为表面样本均值。"""

    center = volume_centroid(template_triangles)
    lower, upper = mesh_bounds(template_mesh)
    inside_bounds = all(
        minimum - 1e-5 <= coordinate <= maximum + 1e-5
        for coordinate, minimum, maximum in zip(
            center.as_tuple(), lower.as_tuple(), upper.as_tuple(), strict=True
        )
    )
    return center if inside_bounds else mean_point([sample.position for sample in template_samples])


def _measure_operation_feature(
    components: tuple[bpy.types.Object, ...],
    guide_sleeves: tuple[GuideSleeve, GuideSleeve],
) -> OperationFeature:
    """测量两个导管附近用于操作窗定位的紧凑圆形分量。"""

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


def analyze_case(config: CaseConfig, *, force_rebuild: bool = False) -> CaseAnalysis:
    """读取病例网格，并计算后续阶段共用的几何数据。"""

    global _CASE_CACHE
    fingerprint = _case_fingerprint(config)
    if (
        not force_rebuild
        and _CASE_CACHE is not None
        and _CASE_CACHE.fingerprint == fingerprint
    ):
        _clear_generated_scene_objects(_CASE_CACHE)
        return _case_with_overrides(_CASE_CACHE, config)
    clear_scene()
    input_meshes = _load_generation_meshes(config)
    template_samples, template_triangles = sample_mesh_surface_and_triangles(
        input_meshes.template_mesh
    )
    if not template_samples:
        raise GeometryError("牙科导板网格不存在可采样表面")
    dentition_samples = sample_mesh_surface(input_meshes.patient_dentition_mesh)
    if not dentition_samples:
        raise GeometryError("患者牙列网格不存在可采样表面")
    center = _template_center(
        input_meshes.template_mesh,
        template_samples,
        template_triangles,
    )
    raw_occlusal_axis = case_occlusal_axis(config)
    occlusal_axis = (
        None if raw_occlusal_axis is None else Vec3(*raw_occlusal_axis).normalized()
    )
    assembly_components = []
    all_guides = []
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
        assembly_components.append(components)
        source_path = config.inputs.guide_sleeve_assemblies[assembly_index - 1]
        source_stat = source_path.stat()
        candidate_cache_key = json.dumps(
            {
                "path": str(source_path.resolve()),
                "size": source_stat.st_size,
                "mtime_ns": source_stat.st_mtime_ns,
                "occlusal_axis": (
                    None if occlusal_axis is None else occlusal_axis.as_tuple()
                ),
            },
            sort_keys=True,
        )
        sleeve_generation = recognize_and_build_sleeves(
            SleeveGenerationInputs(
                components=components,
                template_samples=template_samples,
                template_center=center,
                sleeve_parameters=config.sleeve,
                jaw=config.jaw,
                occlusal_axis=occlusal_axis,
                candidate_cache_path=(
                    None
                    if force_rebuild
                    else config.output_directory
                    / ".cache"
                    / "stage-01-sleeve-reconstruction"
                    / f"assembly-{assembly_index:02d}-candidates.json"
                ),
                candidate_cache_key=candidate_cache_key,
            )
        )
        offset = len(all_guides)
        base_pair = tuple(
            replace(guide, guide_index=offset + local_index)
            for local_index, guide in enumerate(sleeve_generation.sleeves, 1)
        )
        if template_frame is None:
            template_frame = sleeve_generation.template_frame
        all_guides.extend(base_pair)
    if template_frame is None:
        raise GeometryError("病例没有可识别的导管装配体")
    base_case = CaseAnalysis(
        config=config,
        input_meshes=input_meshes,
        guide_sleeves=tuple(all_guides),
        retained_accessory_meshes=(),
        operation_features=(),
        template_frame=template_frame,
        template_samples=template_samples,
        dentition_samples=dentition_samples,
    )
    _CASE_CACHE = _CaseCache(
        fingerprint,
        base_case,
        tuple(assembly_components),
    )
    return _case_with_overrides(_CASE_CACHE, config)

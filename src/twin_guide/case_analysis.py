"""读取输入网格，并分析各阶段共用的病例几何。"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, replace

import bpy
import numpy as np
import trimesh

from twin_guide.blender.mesh_queries import (
    mesh_bounds,
    sample_mesh_surface,
    sample_mesh_surface_and_triangles,
)
from twin_guide.blender.scene import clear_scene, remove_object
from twin_guide.blender.sleeve_reconstruction import create_closed_sleeve_object
from twin_guide.blender.stl_io import import_stl_mesh
from twin_guide.config import CaseConfig, case_occlusal_axis
from twin_guide.config.loading import load_case_yaml
from twin_guide.errors import GeometryError
from twin_guide.geometry import (
    Vec3,
    mean_point,
    principal_plane_normal,
    project_to_plane,
    volume_centroid,
)
from twin_guide.models import (
    CaseAnalysis,
    GenerationMeshes,
    GuideSleeve,
    OperationFeature,
    SurfaceSample,
    TemplateFrame,
)
from twin_guide.sleeve_estimation.types import SleeveEstimate
from twin_guide.template_ring_estimation import (
    estimate_template_ring_top_plane,
    estimate_template_rings,
)
from twin_guide.tooth_mapping.fdi import validate_anatomy
from twin_guide.tooth_mapping.pipeline._core import estimate_frame_and_arch, local_arch_frame


@dataclass(slots=True)
class _CaseCache:
    """常驻 worker 内复用的只读病例网格与识别结果。"""

    fingerprint: str
    case: CaseAnalysis


_CASE_CACHE: _CaseCache | None = None


def _case_fingerprint(config: CaseConfig) -> str:
    """计算不包含图形微调值的病例分析指纹。"""

    digest = hashlib.sha256(
        json.dumps(
            {
                "jaw": config.jaw.value,
                "sleeve": asdict(config.sleeve),
                "guide_posts": [asdict(item) for item in config.guide_posts],
                "occlusal_axis": case_occlusal_axis(config),
            },
            sort_keys=True,
        ).encode()
    )
    source_paths = (
        config.inputs.template,
        config.inputs.patient_dentition,
    )
    for path in source_paths:
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
    guides = tuple(_apply_sleeve_editor_override(config, guide) for guide in base.guide_sleeves)
    return replace(
        base,
        config=config,
        guide_sleeves=guides,
    )


def _clear_generated_scene_objects(cached: _CaseCache) -> None:
    """保留病例源网格和分量，删除上一次实体任务的临时对象。"""

    case = cached.case
    preserved = {
        case.input_meshes.template_mesh,
        case.input_meshes.patient_dentition_mesh,
        *(guide.guide_mesh for guide in case.guide_sleeves),
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
    """导入传统模板和患者牙列两个源 STL 网格。"""

    return GenerationMeshes(
        template_mesh=import_stl_mesh(config.inputs.template, "template_mesh"),
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


def _template_frame_from_guides(
    config: CaseConfig,
    template_samples: tuple[SurfaceSample, ...],
    template_center: Vec3,
    guides: tuple[GuideSleeve, ...],
    occlusal_axis: Vec3 | None,
) -> TemplateFrame:
    """只由模板表面和已生成导柱构造后续阶段局部标架。"""

    normal = principal_plane_normal([sample.position for sample in template_samples])
    occlusal_outward = occlusal_axis or Vec3(0.0, 0.0, config.jaw.occlusal_axis_sign)
    if normal.dot(occlusal_outward) < 0.0:
        normal = -normal
    midpoint = (guides[0].center + guides[1].center) * 0.5
    depth = project_to_plane(template_center - midpoint, normal)
    if depth.length < 1e-6:
        raise GeometryError("无法根据模板圆环位置确定牙科导板深度方向")
    depth = depth.normalized()
    return TemplateFrame(
        template_center,
        depth.cross(normal).normalized(),
        depth,
        normal,
    )


def _dental_arch_frame(
    config: CaseConfig,
    template_mesh: trimesh.Trimesh,
) -> dict[str, object]:
    """根据牙列和病例语义拟合用于导柱旋转的牙弓曲线。"""

    if config.tooth_identification is None:
        raise GeometryError("牙弓局部切线定位要求病例配置牙位识别输入")
    raw = load_case_yaml(config.tooth_identification.case_yaml)
    if not isinstance(raw, dict) or not isinstance(raw.get("anatomy"), dict):
        raise GeometryError("病例 YAML 缺少 anatomy，无法拟合牙弓局部切线")
    dentition = trimesh.load_mesh(config.inputs.patient_dentition, process=True)
    if not isinstance(dentition, trimesh.Trimesh):
        raise GeometryError("患者牙列 STL 未解析为单个三角网格")
    anatomy = raw["anatomy"]
    return estimate_frame_and_arch(
        dentition,
        template_mesh,
        anatomy,
        validate_anatomy(anatomy),
        0.55,
        0.05,
        surgical_reference_point=None,
    )


def _ring_arch_normal(
    frame: dict[str, object],
    ring_center: Vec3,
    guide_axis: Vec3,
) -> Vec3:
    """返回离圆环中心最近且垂直于牙弓局部切线的方向。"""

    origin = np.asarray(frame["origin"], dtype=float)
    e_lr = np.asarray(frame["e_lr"], dtype=float)
    e_ap = np.asarray(frame["e_ap"], dtype=float)
    relative = np.asarray(ring_center.as_tuple(), dtype=float) - origin
    ring_point = np.asarray([float(relative @ e_lr), float(relative @ e_ap)])
    curve = frame["curve"]
    curve_points = curve.at_s(curve.s)
    nearest_index = int(np.argmin(np.linalg.norm(curve_points - ring_point, axis=1)))
    _, outward, _ = local_arch_frame(frame, float(curve.s[nearest_index]))
    direction = Vec3(*map(float, outward))
    projected = direction - guide_axis * direction.dot(guide_axis)
    if projected.length < 1e-6:
        raise GeometryError("牙弓局部法向与导柱轴线近似平行，无法确定双柱方向")
    return projected.normalized()


def _build_template_only_guides(
    config: CaseConfig,
    template_samples: tuple[SurfaceSample, ...],
    template_center: Vec3,
    occlusal_axis: Vec3 | None,
) -> tuple[tuple[GuideSleeve, ...], tuple[OperationFeature, ...], TemplateFrame]:
    """只使用传统模板圆环和病例参数生成正式第 1 阶段导柱。"""

    source = trimesh.load_mesh(config.inputs.template, process=True)
    if not isinstance(source, trimesh.Trimesh):
        raise GeometryError("传统模板 STL 未解析为单个三角网格")
    rings = estimate_template_rings(source)
    arch_frame = _dental_arch_frame(config, source)
    configured = config.sleeve
    z_platform = configured.height_mm - configured.platform_height_mm
    guides: list[GuideSleeve] = []
    operation_features: list[OperationFeature] = []
    for site_index, guide_post in enumerate(config.guide_posts, 1):
        if guide_post.ring_index > len(rings):
            raise GeometryError(f"guide_posts[{site_index - 1}].ring_index 超出传统模板圆环数量")
        ring = rings[guide_post.ring_index - 1]
        top_plane = estimate_template_ring_top_plane(source, ring)
        outward = top_plane.normal.normalized()
        if occlusal_axis is not None and outward.dot(occlusal_axis) < 0.0:
            outward = -outward
        inward = -outward
        implant_top = top_plane.center + inward * guide_post.sleeve_template_extension_mm
        stop_center = implant_top + outward * guide_post.twin_guide_extension_mm
        pair_direction = _ring_arch_normal(arch_frame, ring.center, inward)
        axis_origin_center = stop_center - inward * z_platform
        half_axis_spacing = 0.5 * configured.guide_axis_spacing_mm
        for side in (-1.0, 1.0):
            parameters = SleeveEstimate(
                axis_origin=axis_origin_center + pair_direction * (side * half_axis_spacing),
                axis=inward,
                c_opening_direction=pair_direction * (-side),
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
            guide_index = len(guides) + 1
            guide_mesh = create_closed_sleeve_object(
                parameters,
                f"template_only_source_guide_{guide_index}",
            )
            guides.append(
                GuideSleeve(
                    guide_index,
                    guide_mesh,
                    parameters,
                    0.0,
                    configured.height_mm,
                )
            )
        operation_features.append(OperationFeature(top_plane.center, 2.0 * ring.radius_mm))
    if not guides:
        raise GeometryError("无 sleeve 模式至少需要一个 planning.guide_posts 配置")
    guide_tuple = tuple(guides)
    return (
        guide_tuple,
        tuple(operation_features),
        _template_frame_from_guides(
            config,
            template_samples,
            template_center,
            guide_tuple,
            occlusal_axis,
        ),
    )


def analyze_case(config: CaseConfig, *, force_rebuild: bool = False) -> CaseAnalysis:
    """读取病例网格，并计算后续阶段共用的几何数据。"""

    global _CASE_CACHE
    fingerprint = _case_fingerprint(config)
    if not force_rebuild and _CASE_CACHE is not None and _CASE_CACHE.fingerprint == fingerprint:
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
    occlusal_axis = None if raw_occlusal_axis is None else Vec3(*raw_occlusal_axis).normalized()
    all_guides, operation_features, template_frame = _build_template_only_guides(
        config,
        template_samples,
        center,
        occlusal_axis,
    )
    base_case = CaseAnalysis(
        config=config,
        input_meshes=input_meshes,
        guide_sleeves=tuple(all_guides),
        retained_accessory_meshes=(),
        operation_features=operation_features,
        template_frame=template_frame,
        template_samples=template_samples,
        dentition_samples=dentition_samples,
    )
    _CASE_CACHE = _CaseCache(fingerprint, base_case)
    return _case_with_overrides(_CASE_CACHE, config)

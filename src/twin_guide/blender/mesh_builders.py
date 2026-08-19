"""将纯几何规格转换为 Blender 网格。"""

from __future__ import annotations

import itertools
import warnings
from math import cos, pi, sin

import bpy
from mathutils import Matrix

from twin_guide.blender.mesh_queries import (
    LocalAlignedSurfaceData,
    build_local_aligned_bvh,
    clean_mesh,
    to_blender_vector,
)
from twin_guide.blender.scene import (
    apply_object_transform,
    duplicate_mesh_object,
    set_active_object,
)
from twin_guide.errors import GeometryError
from twin_guide.geometry import Vec3
from twin_guide.models import WindowCutout

# TwinGuide 内部 trimesh 算法阈值为 1.50 mm；Blender 将原多边形重新三角化后，
# 同一足印的最近点最大偏差约增加 0.05 mm，预留 0.06 mm 离散容差。
FOOT_PROJECTION_LIMIT_MM = 1.560
FOOT_PROJECTION_NUMERICAL_TOLERANCE_MM = 0.010
MINIMUM_CONFORMAL_FOOTPRINT_SCALE = 0.77


def assign_material(
    mesh_object: bpy.types.Object, material: bpy.types.Material | None
) -> bpy.types.Object:
    """指定材质时，清空原材质槽并设置新材质。"""

    if material is None:
        return mesh_object
    mesh_object.data.materials.clear()
    mesh_object.data.materials.append(material)
    return mesh_object


def create_axis_cylinder(
    name: str,
    start: Vec3,
    end: Vec3,
    radius_mm: float,
    material: bpy.types.Material | None = None,
    vertices: int = 96,
) -> bpy.types.Object:
    """在两个世界坐标点之间创建封闭圆柱体。"""

    direction = to_blender_vector(end - start)
    if direction.length < 1e-8:
        raise GeometryError(f"无法创建零长度圆柱体：{name}")
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=vertices,
        radius=radius_mm,
        depth=direction.length,
        location=to_blender_vector((start + end) / 2.0),
    )
    cylinder = bpy.context.object
    cylinder.name = name
    cylinder.rotation_euler = direction.to_track_quat("Z", "Y").to_euler()
    apply_object_transform(cylinder)
    return assign_material(cylinder, material)


def create_window_cutter(
    specification: WindowCutout, material: bpy.types.Material | None = None
) -> bpy.types.Object:
    """根据窗口几何规格创建定向圆角切割体。"""

    bpy.ops.mesh.primitive_cube_add(size=1.0, location=to_blender_vector(specification.center))
    cutter = bpy.context.object
    cutter.name = specification.name
    normal = to_blender_vector(specification.normal).normalized()
    tangent = to_blender_vector(specification.tangent).normalized()
    bitangent = normal.cross(tangent).normalized()
    cutter.matrix_world = (
        Matrix.Translation(to_blender_vector(specification.center))
        @ Matrix((tangent, bitangent, normal)).transposed().to_4x4()
    )
    cutter.dimensions = (
        specification.width_mm,
        specification.height_mm,
        specification.depth_mm,
    )
    apply_object_transform(cutter)
    bevel = cutter.modifiers.new("window_edge_rounding", "BEVEL")
    bevel.width = min(
        specification.corner_radius_mm,
        specification.width_mm * 0.2,
        specification.height_mm * 0.2,
    )
    bevel.segments = 6
    set_active_object(cutter)
    bpy.ops.object.modifier_apply(modifier=bevel.name)
    return assign_material(cutter, material)


def _transported_frames(
    centerline: tuple[Vec3, ...],
) -> tuple[tuple[Vec3, Vec3, Vec3], ...]:
    """为离散中心线生成稳定的平行输运标架。"""

    tangents = []
    for index, point in enumerate(centerline):
        if index == 0:
            tangent = centerline[1] - point
        elif index == len(centerline) - 1:
            tangent = point - centerline[index - 1]
        else:
            tangent = centerline[index + 1] - centerline[index - 1]
        if tangent.length <= 1e-8:
            raise GeometryError("连续梁中心线包含重复或零切向采样点")
        tangents.append(tangent.normalized())

    first_tangent = tangents[0]
    reference = min(
        (Vec3(1.0, 0.0, 0.0), Vec3(0.0, 1.0, 0.0), Vec3(0.0, 0.0, 1.0)),
        key=lambda axis: abs(first_tangent.dot(axis)),
    )
    normal = first_tangent.cross(reference).normalized()
    frames = [(first_tangent, normal, first_tangent.cross(normal).normalized())]
    previous_tangent = first_tangent
    for tangent in tangents[1:]:
        rotation_axis = previous_tangent.cross(tangent)
        sine = rotation_axis.length
        cosine = max(-1.0, min(1.0, previous_tangent.dot(tangent)))
        if sine > 1e-8:
            unit_axis = rotation_axis / sine
            normal = (
                normal * cosine
                + unit_axis.cross(normal) * sine
                + unit_axis * unit_axis.dot(normal) * (1.0 - cosine)
            )
        normal = (normal - tangent * normal.dot(tangent)).normalized()
        binormal = tangent.cross(normal).normalized()
        frames.append((tangent, normal, binormal))
        previous_tangent = tangent
    return tuple(frames)


def _create_centerline_tube_with_radii(
    name: str,
    centerline: tuple[Vec3, ...],
    radii_mm: tuple[float, ...],
    material: bpy.types.Material | None = None,
    ring_segments: int = 64,
) -> bpy.types.Object:
    """按逐点半径沿中心线扫掠带端盖的圆截面梁。"""

    if len(centerline) < 2:
        raise GeometryError(f"连续梁中心线至少需要两个点：{name}")
    if len(radii_mm) != len(centerline):
        raise GeometryError("连续梁逐点半径数量必须与中心线采样数一致")
    if min(radii_mm) <= 0.0 or ring_segments < 8:
        raise GeometryError("连续梁半径必须为正且截面细分数不得小于 8")
    frames = _transported_frames(centerline)
    vertices = []
    for point, radius_mm, (_, normal, binormal) in zip(centerline, radii_mm, frames, strict=True):
        for segment in range(ring_segments):
            angle = 2.0 * pi * segment / ring_segments
            vertices.append(
                (
                    point + normal * (radius_mm * cos(angle)) + binormal * (radius_mm * sin(angle))
                ).as_tuple()
            )
    faces = []
    for ring in range(len(centerline) - 1):
        current = ring * ring_segments
        following = (ring + 1) * ring_segments
        for segment in range(ring_segments):
            next_segment = (segment + 1) % ring_segments
            faces.append(
                (
                    current + segment,
                    current + next_segment,
                    following + next_segment,
                    following + segment,
                )
            )
    faces.append(tuple(reversed(range(ring_segments))))
    final_ring = (len(centerline) - 1) * ring_segments
    faces.append(tuple(final_ring + segment for segment in range(ring_segments)))
    mesh = bpy.data.meshes.new(f"{name}_mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    mesh_object = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(mesh_object)
    for polygon in mesh.polygons:
        polygon.use_smooth = True
    clean_mesh(mesh_object)
    return assign_material(mesh_object, material)


def create_centerline_tube(
    name: str,
    centerline: tuple[Vec3, ...],
    radius_mm: float,
    material: bpy.types.Material | None = None,
    ring_segments: int = 64,
) -> bpy.types.Object:
    """沿离散中心线以恒定圆截面扫掠封闭梁。"""

    return _create_centerline_tube_with_radii(
        name,
        centerline,
        (radius_mm,) * len(centerline),
        material,
        ring_segments,
    )


def create_root_tapered_centerline_tube(
    name: str,
    centerline: tuple[Vec3, ...],
    beam_radius_mm: float,
    root_radius_mm: float,
    transition_length_mm: float,
    material: bpy.types.Material | None = None,
    ring_segments: int = 64,
) -> bpy.types.Object:
    """从中心线首端根部以 smoothstep 在限定弧长内过渡到标准梁半径。"""

    if root_radius_mm < beam_radius_mm:
        raise GeometryError("根部半径不得小于按压梁半径")
    cumulative = [0.0]
    for previous, point in itertools.pairwise(centerline):
        cumulative.append(cumulative[-1] + previous.distance_to(point))
    effective = min(transition_length_mm, 0.45 * cumulative[-1])
    if effective <= 1e-8:
        raise GeometryError("按压梁根部渐粗段长度不足")
    radii = []
    for distance in cumulative:
        u = max(0.0, min(1.0, distance / effective))
        smoothstep = 3.0 * u * u - 2.0 * u * u * u
        radii.append(root_radius_mm + (beam_radius_mm - root_radius_mm) * smoothstep)
    return _create_centerline_tube_with_radii(
        name,
        centerline,
        tuple(radii),
        material,
        ring_segments,
    )


def create_dual_root_tapered_centerline_tube(
    name: str,
    centerline: tuple[Vec3, ...],
    beam_radius_mm: float,
    root_radius_mm: float,
    transition_length_mm: float,
    material: bpy.types.Material | None = None,
    ring_segments: int = 64,
) -> bpy.types.Object:
    """从中心线两端以 smoothstep 渐粗，并保持中段为标准梁半径。"""

    if root_radius_mm < beam_radius_mm:
        raise GeometryError("根部半径不得小于连接梁半径")
    cumulative = [0.0]
    for previous, point in itertools.pairwise(centerline):
        cumulative.append(cumulative[-1] + previous.distance_to(point))
    total_length = cumulative[-1]
    effective = min(transition_length_mm, 0.45 * total_length)
    if effective <= 1e-8:
        raise GeometryError("连接梁双端渐粗段长度不足")

    def tapered_radius(distance_from_root: float) -> float:
        """返回距离一个根部指定弧长处的 smoothstep 半径。"""

        u = max(0.0, min(1.0, distance_from_root / effective))
        smoothstep = 3.0 * u * u - 2.0 * u * u * u
        return root_radius_mm + (beam_radius_mm - root_radius_mm) * smoothstep

    radii = tuple(
        max(
            tapered_radius(distance),
            tapered_radius(total_length - distance),
        )
        for distance in cumulative
    )
    return _create_centerline_tube_with_radii(
        name,
        centerline,
        radii,
        material,
        ring_segments,
    )


def create_conformal_fusion_foot(
    name: str,
    surface_mesh: bpy.types.Object,
    anchor: Vec3,
    normal: Vec3,
    incident_direction: Vec3,
    major_radius_mm: float,
    minor_radius_mm: float,
    peak_height_mm: float,
    embed_depth_mm: float,
    material: bpy.types.Material | None = None,
    radial_rings: int = 10,
    angular_segments: int = 72,
    surface_data: LocalAlignedSurfaceData | None = None,
) -> bpy.types.Object:
    """在导板外表面生成带亚表面预埋量的封闭椭圆贴合脚。"""

    unit_normal = normal.normalized()
    tangent = incident_direction - unit_normal * incident_direction.dot(unit_normal)
    if tangent.length <= 1e-8:
        raise GeometryError(f"按压梁贴合脚缺少稳定切向：{name}")
    tangent = tangent.normalized()
    bitangent = unit_normal.cross(tangent).normalized()
    surface_tree = build_local_aligned_bvh(
        surface_mesh,
        anchor,
        unit_normal,
        tangent,
        bitangent,
        major_radius_mm,
        minor_radius_mm,
        surface_data,
    )
    uv = [(0.0, 0.0)]
    ring_indices: list[list[int]] = []
    for ring_index in range(1, radial_rings + 1):
        rho = ring_index / radial_rings
        indices = []
        for segment in range(angular_segments):
            angle = 2.0 * pi * segment / angular_segments
            uv.append((rho * cos(angle), rho * sin(angle)))
            indices.append(len(uv) - 1)
        ring_indices.append(indices)

    top_vertices = []
    bottom_vertices = []
    maximum_projection = float("inf")
    footprint_scale = 1.0
    projection_limit_exceeded = False
    for candidate_scale in (
        1.0,
        0.98,
        0.96,
        0.94,
        0.92,
        0.90,
        0.88,
        0.85,
        0.80,
        0.79,
        0.78,
        MINIMUM_CONFORMAL_FOOTPRINT_SCALE,
    ):
        candidate_top = []
        candidate_bottom = []
        candidate_maximum_projection = 0.0
        for u, v in uv:
            query = (
                anchor
                + tangent * (u * major_radius_mm * candidate_scale)
                + bitangent * (v * minor_radius_mm * candidate_scale)
            )
            location, local_normal, _, distance = surface_tree.find_nearest(
                to_blender_vector(query)
            )
            if location is None or local_normal is None or distance is None:
                raise GeometryError(f"按压梁贴合脚无法投影到导板：{name}")
            projection_distance = float(distance)
            candidate_maximum_projection = max(
                candidate_maximum_projection,
                projection_distance,
            )
            projected_normal = Vec3(
                float(local_normal.x), float(local_normal.y), float(local_normal.z)
            ).normalized()
            if projected_normal.dot(unit_normal) < 0.0:
                projected_normal = projected_normal * -1.0
            surface = Vec3(float(location.x), float(location.y), float(location.z))
            rho_squared = u * u + v * v
            height = peak_height_mm * max(0.0, 1.0 - rho_squared) ** 2
            candidate_top.append((surface + projected_normal * height).as_tuple())
            candidate_bottom.append((surface - projected_normal * embed_depth_mm).as_tuple())
        maximum_projection = candidate_maximum_projection
        if maximum_projection <= (
            FOOT_PROJECTION_LIMIT_MM + FOOT_PROJECTION_NUMERICAL_TOLERANCE_MM
        ):
            footprint_scale = candidate_scale
            top_vertices = candidate_top
            bottom_vertices = candidate_bottom
            break
    if not top_vertices:
        # 局部曲率过大时仍保留最小的 77% 足印，使病例能够导出 STL；
        # 超限状态同时写入日志和对象属性，供人工复核，而不再阻断建模。
        projection_limit_exceeded = True
        footprint_scale = candidate_scale
        top_vertices = candidate_top
        bottom_vertices = candidate_bottom
        warnings.warn(
            f"按压梁贴合脚超出局部同向导板表面：{name}，"
            f"最大投影距离 {maximum_projection:.6f} mm，"
            f"上限 {FOOT_PROJECTION_LIMIT_MM:.3f} mm，"
            "采用已缩放至 77% 的足印继续生成 STL",
            RuntimeWarning,
            stacklevel=2,
        )

    vertex_count = len(top_vertices)
    faces: list[tuple[int, ...]] = []
    first_ring = ring_indices[0]
    for segment in range(angular_segments):
        following = (segment + 1) % angular_segments
        faces.append((0, first_ring[segment], first_ring[following]))
    for ring_index in range(1, radial_rings):
        inner = ring_indices[ring_index - 1]
        outer = ring_indices[ring_index]
        for segment in range(angular_segments):
            following = (segment + 1) % angular_segments
            faces.append((inner[segment], outer[segment], outer[following], inner[following]))
    top_faces = tuple(faces)
    faces.extend(
        tuple(reversed(tuple(vertex + vertex_count for vertex in face))) for face in top_faces
    )
    outer = ring_indices[-1]
    for segment in range(angular_segments):
        following = (segment + 1) % angular_segments
        faces.append(
            (
                outer[segment],
                outer[segment] + vertex_count,
                outer[following] + vertex_count,
                outer[following],
            )
        )
    mesh = bpy.data.meshes.new(f"{name}_mesh")
    mesh.from_pydata((*top_vertices, *bottom_vertices), [], faces)
    mesh.update()
    mesh_object = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(mesh_object)
    for polygon in mesh.polygons:
        polygon.use_smooth = True
    clean_mesh(mesh_object)
    mesh_object["maximum_surface_projection_distance_mm"] = maximum_projection
    mesh_object["surface_projection_limit_exceeded"] = projection_limit_exceeded
    mesh_object["footprint_scale"] = footprint_scale
    mesh_object["effective_major_radius_mm"] = major_radius_mm * footprint_scale
    mesh_object["effective_minor_radius_mm"] = minor_radius_mm * footprint_scale
    return assign_material(mesh_object, material)


def voxel_union(
    mesh_objects: tuple[bpy.types.Object, ...],
    name: str,
    voxel_size_mm: float,
    material: bpy.types.Material | None = None,
) -> bpy.types.Object:
    """在副本上执行体素融合，不修改输入对象。"""

    if not mesh_objects:
        raise GeometryError("体素融合至少需要一个网格")
    duplicates = tuple(
        duplicate_mesh_object(mesh_object, f"{name}_source_{index}")
        for index, mesh_object in enumerate(mesh_objects)
    )
    bpy.ops.object.select_all(action="DESELECT")
    for duplicate in duplicates:
        duplicate.hide_set(False)
        duplicate.select_set(True)
    bpy.context.view_layer.objects.active = duplicates[0]
    if len(duplicates) > 1:
        bpy.ops.object.join()
    result = duplicates[0]
    result.name = name
    result.data.remesh_voxel_size = voxel_size_mm
    result.data.remesh_voxel_adaptivity = 0.0
    set_active_object(result)
    bpy.ops.object.voxel_remesh()
    return assign_material(result, material)

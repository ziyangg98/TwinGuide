"""识别牙科手机参考姿态，并构造保守扫掠体。"""

from __future__ import annotations

import math

import bmesh
import bpy
from mathutils import Matrix, Vector

from twin_guide.blender.mesh_builders import voxel_union
from twin_guide.blender.mesh_queries import mesh_points, to_blender_vector
from twin_guide.blender.scene import duplicate_mesh_object, remove_object, set_active_object
from twin_guide.config import HandpieceValidationParameters
from twin_guide.errors import GeometryError
from twin_guide.geometry import Vec3, mean_point, principal_axis
from twin_guide.models import GuideSleeve, HandpieceReference

HANDPIECE_SWEEP_VOXEL_MM = 0.5


def locate_handpiece_reference(
    handpiece_mesh: bpy.types.Object,
    guide_sleeves: tuple[GuideSleeve, GuideSleeve],
) -> HandpieceReference:
    """识别牙科手机机头及与其对齐的导套。"""

    points = mesh_points(handpiece_mesh, 40_000)
    center = mean_point(points)
    long_axis = principal_axis(points)
    ordered = sorted(((point - center).dot(long_axis), point) for point in points)
    end_count = max(1, len(ordered) // 5)
    end_centers = (
        mean_point([point for _, point in ordered[:end_count]]),
        mean_point([point for _, point in ordered[-end_count:]]),
    )
    guide_center = mean_point([guide.center for guide in guide_sleeves])
    head_center = min(end_centers, key=lambda point: point.distance_to(guide_center))
    source_guide = min(guide_sleeves, key=lambda guide: guide.center.distance_to(head_center))
    return HandpieceReference(source_guide.guide_index, head_center)


def _crop_to_head(
    handpiece_mesh: bpy.types.Object, head_center: Vec3, radius_mm: float
) -> bpy.types.Object:
    """保留手机网格中位于机头中心半径范围内的面。"""

    inverse_world = handpiece_mesh.matrix_world.inverted()
    local_center = inverse_world @ to_blender_vector(head_center)
    editable = bmesh.new()
    editable.from_mesh(handpiece_mesh.data)
    outside = [vertex for vertex in editable.verts if (vertex.co - local_center).length > radius_mm]
    bmesh.ops.delete(editable, geom=outside, context="VERTS")
    if not editable.verts:
        editable.free()
        raise GeometryError("牙科手机机头裁剪后未保留任何网格")
    editable.to_mesh(handpiece_mesh.data)
    editable.free()
    handpiece_mesh.data.update()
    return handpiece_mesh


def _decimate_handpiece_head(
    handpiece_head: bpy.types.Object, target_faces: int = 4_500
) -> bpy.types.Object:
    """在保留机头形状的前提下降低网格面数，以加速扫掠融合。"""

    if len(handpiece_head.data.polygons) <= target_faces:
        return handpiece_head
    modifier = handpiece_head.modifiers.new("handpiece_sweep_decimation", "DECIMATE")
    modifier.ratio = max(0.01, target_faces / len(handpiece_head.data.polygons))
    set_active_object(handpiece_head)
    bpy.ops.object.modifier_apply(modifier=modifier.name)
    return handpiece_head


def _pose_matrix(
    source_guide: GuideSleeve,
    target_guide: GuideSleeve,
    withdrawal_mm: float,
    tilt_axis: Vector | None,
    tilt_radians: float,
) -> Matrix:
    """构造手机绕指定支点旋转并沿撤离方向平移的姿态矩阵。"""

    alignment = to_blender_vector(source_guide.axis).rotation_difference(
        to_blender_vector(target_guide.axis)
    )
    tilt = Matrix.Identity(4) if tilt_axis is None else Matrix.Rotation(tilt_radians, 4, tilt_axis)
    target_center = to_blender_vector(target_guide.center + target_guide.axis * withdrawal_mm)
    return (
        Matrix.Translation(target_center)
        @ tilt
        @ alignment.to_matrix().to_4x4()
        @ Matrix.Translation(-to_blender_vector(source_guide.center))
    )


def build_handpiece_sweep(
    handpiece_mesh: bpy.types.Object,
    guide_sleeves: tuple[GuideSleeve, GuideSleeve],
    reference: HandpieceReference,
    parameters: HandpieceValidationParameters,
) -> bpy.types.Object:
    """将沿导套轴线采样的机头姿态融合为未外扩的扫掠体。"""

    source_guide = next(
        guide for guide in guide_sleeves if guide.guide_index == reference.source_guide_index
    )
    local_head = duplicate_mesh_object(handpiece_mesh, "handpiece_local_head")
    _crop_to_head(local_head, reference.head_center, parameters.head_crop_radius_mm)
    _decimate_handpiece_head(local_head)
    local_head.hide_render = True
    local_head.hide_set(True)
    tilt_radians = math.radians(parameters.maximum_tilt_degrees)
    poses: list[bpy.types.Object] = []
    for target_guide in guide_sleeves:
        axis = to_blender_vector(target_guide.axis).normalized()
        first_tangent = axis.cross(Vector((0.0, 1.0, 0.0)))
        if first_tangent.length < 1e-6:
            first_tangent = axis.cross(Vector((1.0, 0.0, 0.0)))
        first_tangent.normalize()
        second_tangent = axis.cross(first_tangent).normalized()
        tilts = (
            (None, 0.0),
            (first_tangent, tilt_radians),
            (first_tangent, -tilt_radians),
            (second_tangent, tilt_radians),
            (second_tangent, -tilt_radians),
        )
        for withdrawal_mm in parameters.withdrawal_distances_mm:
            for pose_index, (tilt_axis, tilt_angle) in enumerate(tilts):
                pose = duplicate_mesh_object(
                    local_head,
                    f"handpiece_pose_{target_guide.guide_index}_{withdrawal_mm:g}_{pose_index}",
                )
                pose.hide_set(False)
                pose.hide_render = False
                pose.matrix_world = _pose_matrix(
                    source_guide,
                    target_guide,
                    withdrawal_mm,
                    tilt_axis,
                    tilt_angle,
                )
                poses.append(pose)
    sweep_mesh = voxel_union(
        tuple(poses),
        "handpiece_sweep_mesh",
        HANDPIECE_SWEEP_VOXEL_MM,
    )
    for pose in poses:
        remove_object(pose)
    remove_object(local_head)
    return sweep_mesh

"""生成当前装配深度下的牙科手机左右摆动避障包络。"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path

from twin_guide.config import (
    GuideAnchorSide,
    HandpieceAvoidanceParameters,
    HandpieceMotionMode,
    HandpieceSamplingMode,
)
from twin_guide.errors import GeometryError
from twin_guide.types import GenerationContext

ALGORITHM_VERSION = "current-depth-configurable-sweep-v5-release-then-validate"
FRAGMENT_VOLUME_TOLERANCE_MM3 = 1.0e-4


@dataclass(frozen=True, slots=True)
class HandpieceAvoidancePlan:
    """第 7 步输出：供最终整体直接差集使用的封闭扫掠包络。"""

    avoidance_id: str
    envelope_mesh_path: Path
    report_path: Path
    rotation_axis: tuple[float, float, float]
    pivot: tuple[float, float, float]
    matched_stop_patch_ids: tuple[str, str]
    angle_samples_degrees: tuple[float, ...]
    extra_clearance_mm: float
    cache_reused: bool


def _sha256(path: Path) -> str:
    """分块计算文件的 SHA-256。"""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unit(vector):
    """返回三维向量的单位方向。"""

    import numpy as np

    length = float(np.linalg.norm(vector))
    if length <= 1.0e-9:
        raise GeometryError("手机止挡报告中的 pair_axis 长度为零")
    return vector / length


def _load_mesh(
    path: Path,
    *,
    require_volume: bool = True,
    process: bool = False,
):
    """读取单个或场景网格，并按需要求其为封闭体。"""

    import trimesh

    loaded = trimesh.load(path, force="mesh", process=process)
    if isinstance(loaded, trimesh.Scene):
        loaded = trimesh.util.concatenate(tuple(loaded.geometry.values()))
    if not isinstance(loaded, trimesh.Trimesh) or loaded.is_empty:
        raise GeometryError(f"无法读取手机避障网格：{path}")
    if require_volume and not loaded.is_volume:
        raise GeometryError(f"手机避障网格不是封闭有效体：{path}")
    return loaded


def _boolean_union(
    meshes: list,
    batch_size: int,
    simplify_tolerance_mm: float = 0.0,
):
    """用 manifold3d 分批合并封闭网格，并可在每层受控简化。"""

    import trimesh

    current = meshes
    while len(current) > 1:
        next_level = []
        for start in range(0, len(current), batch_size):
            batch = current[start : start + batch_size]
            if len(batch) == 1:
                next_level.append(batch[0])
                continue
            result = trimesh.boolean.union(
                batch,
                engine="manifold",
                check_volume=True,
            )
            if not isinstance(result, trimesh.Trimesh) or result.is_empty:
                raise GeometryError("手机姿态包络的 manifold3d 并集失败")
            if simplify_tolerance_mm > 0.0:
                from manifold3d import Manifold, Mesh

                manifold = Manifold(
                    mesh=Mesh(
                        vert_properties=result.vertices.astype("float32"),
                        tri_verts=result.faces.astype("uint32"),
                    )
                ).simplify(simplify_tolerance_mm)
                simplified = manifold.to_mesh()
                result = trimesh.Trimesh(
                    vertices=simplified.vert_properties,
                    faces=simplified.tri_verts,
                    process=False,
                )
                if result.is_empty or not result.is_volume:
                    raise GeometryError("手机姿态包络受控简化后不是封闭有效体")
            next_level.append(result)
        current = next_level
    return current[0]


def _stop_geometry(stop_report: Path):
    """从止挡报告提取单位轴、枢轴、匹配编号和中心坐标。"""

    import numpy as np

    try:
        report = json.loads(stop_report.read_text(encoding="utf-8"))
        axis = _unit(np.asarray(report["pair_axis"], dtype=float))
        patches = {
            item["patch_id"]: item
            for item in report["phone_downward_planar_patch_candidates"]
        }
        matched_ids = tuple(
            str(report["provisional_paired_stop_match"][side]["patch_id"])
            for side in ("left_tube", "right_tube")
        )
        centroids = np.asarray(
            [patches[patch_id]["centroid_global_mm"] for patch_id in matched_ids],
            dtype=float,
        )
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise GeometryError(
            f"手机止挡报告缺少 pair_axis 或成对止挡面信息：{stop_report}"
        ) from error
    if axis.shape != (3,) or centroids.shape != (2, 3) or not np.isfinite(centroids).all():
        raise GeometryError(f"手机止挡报告的轴或止挡面坐标无效：{stop_report}")
    return axis, np.mean(centroids, axis=0), matched_ids, centroids


def _guide_midpoint(guide):
    """返回一个已识别导管轴向范围的中点。"""

    import numpy as np

    return np.asarray(guide.center.as_tuple(), dtype=float) + (
        0.5 * (guide.axial_min_mm + guide.axial_max_mm)
        * np.asarray(guide.axis.as_tuple(), dtype=float)
    )

def _derived_pair_geometry(context: GenerationContext, body):
    """从已识别导管对自动匹配手机并计算公共轴线。"""

    import numpy as np

    sleeves = context.sleeve_generation
    if sleeves is None or len(sleeves.sleeves) < 2 or len(sleeves.sleeves) % 2:
        raise GeometryError("无法从当前病例构造成对导管旋转轴")
    candidates = []
    for start in range(0, len(sleeves.sleeves), 2):
        first, second = sleeves.sleeves[start : start + 2]
        first_axis = np.asarray(first.axis.as_tuple(), dtype=float)
        second_axis = np.asarray(second.axis.as_tuple(), dtype=float)
        if float(first_axis @ second_axis) < 0.0:
            second_axis = -second_axis
        axis = _unit(first_axis + second_axis)
        centroids = np.asarray(
            [_guide_midpoint(first), _guide_midpoint(second)], dtype=float
        )
        pivot = np.mean(centroids, axis=0)
        nearest_phone_distance = float(
            np.min(np.linalg.norm(body.vertices - pivot, axis=1))
        )
        candidates.append(
            (
                nearest_phone_distance,
                axis,
                pivot,
                (f"guide_{first.guide_index}", f"guide_{second.guide_index}"),
                centroids,
            )
        )
    return min(candidates, key=lambda item: item[0])


def _fingerprint(
    parameters: HandpieceAvoidanceParameters,
    constraint_inputs: dict[str, object] | None = None,
) -> dict[str, object]:
    """构造与包络几何一一对应的缓存指纹。"""

    return {
        "algorithm_version": ALGORITHM_VERSION,
        "handpiece_sha256": _sha256(parameters.handpiece),
        "stop_report_sha256": (
            _sha256(parameters.stop_report)
            if parameters.stop_report is not None
            else None
        ),
        "motion_mode": parameters.motion_mode.value,
        "sampling_mode": parameters.sampling_mode.value,
        "maximum_angle_degrees": parameters.maximum_angle_degrees,
        "pose_samples": parameters.pose_samples,
        "union_batch_size": parameters.union_batch_size,
        "collision_coarse_step_degrees": parameters.collision_coarse_step_degrees,
        "collision_refinement_degrees": parameters.collision_refinement_degrees,
        "envelope_step_degrees": parameters.envelope_step_degrees,
        "envelope_simplify_tolerance_mm": (
            parameters.envelope_simplify_tolerance_mm
        ),
        "tooth_clearance_mm": parameters.tooth_clearance_mm,
        "connector_clearance_mm": parameters.connector_clearance_mm,
        "constraint_inputs": constraint_inputs,
    }


def _mesh_bvh(mesh):
    """从 trimesh 网格构造 Blender 世界坐标三角形 BVH。"""

    from mathutils.bvhtree import BVHTree

    return BVHTree.FromPolygons(
        mesh.vertices.astype(float).tolist(),
        mesh.faces.astype(int).tolist(),
        all_triangles=True,
    )


def _connector_obstacle(context: GenerationContext, clearance_mm: float):
    """把背 U 侧主梁中心线转为受保护的分段圆柱。"""

    import numpy as np
    import trimesh

    plan = context.point_linking
    if plan is None:
        raise GeometryError("颊侧手机避障缺少连接梁计划")
    radius = float(plan.radius_mm + clearance_mm)
    pieces = []
    labels = []
    for link in plan.links:
        if link.arch_side is not GuideAnchorSide.BACK_U_SIDE:
            continue
        labels.append(link.link_label or f"guide_{link.guide_index}_{link.sleeve_label}")
        points = np.asarray([point.as_tuple() for point in link.centerline], dtype=float)
        for first, second in zip(points[:-1], points[1:], strict=True):
            if float(np.linalg.norm(second - first)) <= 1.0e-8:
                continue
            pieces.append(
                trimesh.creation.cylinder(
                    radius=radius,
                    segment=np.asarray([first, second]),
                    sections=12,
                )
            )
    if not pieces:
        raise GeometryError("颊侧手机避障没有找到背 U 侧连接梁")
    obstacle = trimesh.util.concatenate(pieces)
    obstacle.remove_unreferenced_vertices()
    return obstacle, tuple(labels)


def _tooth_crown_obstacle(context: GenerationContext, dentition):
    """按当前牙位映射半径提取现存牙冠保护面，排除牙龈和模型基座。"""

    import numpy as np
    from scipy.spatial import cKDTree

    result = context.tooth_identification
    if result is None or not result.positions:
        raise GeometryError("颊侧手机避障缺少现存牙冠位置")
    mapping_parameters = result.mapping_report.get("mapping_parameters", {})
    radius = float(mapping_parameters.get("maximum_crown_label_radius_mm", 6.5))
    if radius <= 0.0:
        raise GeometryError("牙位映射的牙冠标签半径无效")
    crown_points = np.asarray(
        [position.crown_point.as_tuple() for position in result.positions], dtype=float
    )
    distances, _ = cKDTree(crown_points).query(dentition.triangles_center, k=1)
    face_indices = np.flatnonzero(distances <= radius)
    if len(face_indices) == 0:
        raise GeometryError("无法从口扫网格提取牙冠保护面")
    crown_mesh = dentition.submesh([face_indices], append=True, repair=False)
    crown_mesh.remove_unreferenced_vertices()
    return crown_mesh, radius, int(len(face_indices))


def _missing_site_outward(context: GenerationContext, pivot):
    """用缺牙位两侧的现存牙映射插值得到局部颊侧方向。"""

    import numpy as np

    result = context.tooth_identification
    if result is None or not result.missing_teeth:
        raise GeometryError("buccal_outward 手机避障需要现场牙位映射和缺牙位")
    order_indices = {fdi: index for index, fdi in enumerate(result.fdi_order)}
    positions = {position.fdi: position for position in result.positions}
    candidates = []
    for missing_fdi in result.missing_teeth:
        target_index = order_indices.get(missing_fdi)
        if target_index is None:
            continue
        before = sorted(
            (
                (target_index - order_indices[fdi], position)
                for fdi, position in positions.items()
                if order_indices.get(fdi, target_index + 1) < target_index
            ),
            key=lambda item: item[0],
        )
        after = sorted(
            (
                (order_indices[fdi] - target_index, position)
                for fdi, position in positions.items()
                if order_indices.get(fdi, target_index - 1) > target_index
            ),
            key=lambda item: item[0],
        )
        neighbor_records = (*before[:1], *after[:1])
        neighbors = tuple(item[1] for item in neighbor_records)
        if not neighbors:
            continue
        centre = np.mean(
            np.asarray([position.crown_point.as_tuple() for position in neighbors]),
            axis=0,
        )
        outward = np.sum(
            np.asarray([position.local_outward.as_tuple() for position in neighbors]),
            axis=0,
        )
        outward = _unit(outward)
        candidates.append(
            (
                float(np.linalg.norm(centre - pivot)),
                int(missing_fdi),
                centre,
                outward,
                tuple(position.fdi for position in neighbors),
            )
        )
    if not candidates:
        raise GeometryError("无法从现存牙位为缺牙区插值得到颊侧方向")
    _, missing_fdi, centre, outward, neighbor_fdis = min(
        candidates, key=lambda item: item[0]
    )
    return missing_fdi, centre, outward, neighbor_fdis


def _handle_direction(body, pivot, axis):
    """以离旋转轴最远的手机手柄末端区域估计有符号手柄方向。"""

    import numpy as np

    delta = body.vertices - pivot
    radial = delta - np.outer(delta @ axis, axis)
    distances = np.linalg.norm(radial, axis=1)
    maximum = float(np.max(distances))
    if maximum <= 1.0e-6:
        raise GeometryError("手机网格无法确定远离 pair_axis 的手柄方向")
    selected = radial[distances >= max(np.quantile(distances, 0.95), 0.85 * maximum)]
    seed = radial[int(np.argmax(distances))] / maximum
    coherent = selected[(selected @ seed) > 0.0]
    if len(coherent) >= 3:
        selected = coherent
    direction = np.mean(selected, axis=0)
    direction = direction - float(direction @ axis) * axis
    return _unit(direction), int(len(selected)), maximum


def _signed_buccal_target_angle(axis, handle_direction, outward):
    """返回手柄投影从输入姿态最短转向颊侧投影的有符号角。"""

    import numpy as np

    outward_projected = outward - float(outward @ axis) * axis
    outward_projected = _unit(outward_projected)
    angle = float(
        np.rad2deg(
            np.arctan2(
                float(axis @ np.cross(handle_direction, outward_projected)),
                float(handle_direction @ outward_projected),
            )
        )
    )
    return angle, outward_projected


def _adaptive_signed_safe_boundary(
    requested_angle: float,
    coarse_step_degrees: float,
    refinement_degrees: float,
    collision_test,
):
    """沿单一符号方向粗搜索首碰，并在安全/碰撞区间内二分。"""

    import numpy as np

    evaluated: list[float] = []

    def evaluate(angle: float):
        """记录一次候选角并返回其硬约束碰撞结果。"""

        value = float(angle)
        evaluated.append(value)
        return collision_test(value)

    initial_failure = evaluate(0.0)
    if initial_failure is not None:
        return None, initial_failure, tuple(evaluated)
    requested_magnitude = abs(float(requested_angle))
    if requested_magnitude <= 1.0e-9:
        return 0.0, None, tuple(evaluated)
    sign = float(np.sign(requested_angle))
    safe_magnitude = 0.0
    failure_magnitude = None
    failure = None
    while safe_magnitude < requested_magnitude - 1.0e-12:
        candidate_magnitude = min(
            safe_magnitude + coarse_step_degrees,
            requested_magnitude,
        )
        candidate_failure = evaluate(sign * candidate_magnitude)
        if candidate_failure is None:
            safe_magnitude = candidate_magnitude
            continue
        failure_magnitude = candidate_magnitude
        failure = candidate_failure
        break
    if failure_magnitude is None:
        return sign * safe_magnitude, None, tuple(evaluated)
    while failure_magnitude - safe_magnitude > refinement_degrees:
        middle_magnitude = 0.5 * (safe_magnitude + failure_magnitude)
        middle_failure = evaluate(sign * middle_magnitude)
        if middle_failure is None:
            safe_magnitude = middle_magnitude
        else:
            failure_magnitude = middle_magnitude
            failure = middle_failure
    assert failure is not None
    failure = dict(failure)
    failure["angle_degrees"] = sign * failure_magnitude
    return sign * safe_magnitude, failure, tuple(evaluated)


def _signed_release_then_safe_boundary(
    requested_angle: float,
    coarse_step_degrees: float,
    refinement_degrees: float,
    pose_test,
):
    """允许初始侵入，寻找牙体释放及其后的首个再次侵入边界。

    背 U 梁接触在这里仅作为诊断信号；手机包络切除后的梁连续性由最终
    STL 验证负责。若整个请求角度范围内牙体侵入从未解除，则没有可接受
    的颊侧避障轨迹。
    """

    import numpy as np

    evaluated: list[dict[str, object]] = []

    def evaluate(angle: float) -> dict[str, object]:
        """内部算法说明。"""
        value = float(angle)
        result = dict(pose_test(value))
        result["angle_degrees"] = value
        evaluated.append(result)
        return result

    initial = evaluate(0.0)
    requested_magnitude = abs(float(requested_angle))
    if requested_magnitude <= 1.0e-9:
        return (
            0.0 if not bool(initial["tooth_intrusion"]) else None,
            0.0 if not bool(initial["tooth_intrusion"]) else None,
            None,
            initial,
            tuple(evaluated),
        )
    sign = float(np.sign(requested_angle))
    coarse_step = min(float(coarse_step_degrees), requested_magnitude)
    magnitudes = list(np.arange(coarse_step, requested_magnitude, coarse_step))
    magnitudes.append(requested_magnitude)
    release_magnitude = 0.0 if not bool(initial["tooth_intrusion"]) else None
    last_intruding_magnitude = 0.0 if release_magnitude is None else None
    last_clear_magnitude = 0.0 if release_magnitude is not None else None
    first_reentry = None

    for magnitude in magnitudes:
        status = evaluate(sign * magnitude)
        intruding = bool(status["tooth_intrusion"])
        if release_magnitude is None:
            if intruding:
                last_intruding_magnitude = magnitude
                continue
            low = float(last_intruding_magnitude or 0.0)
            high = float(magnitude)
            if refinement_degrees > 0.0:
                while high - low > refinement_degrees:
                    middle = 0.5 * (low + high)
                    if bool(evaluate(sign * middle)["tooth_intrusion"]):
                        low = middle
                    else:
                        high = middle
            release_magnitude = high
            last_clear_magnitude = magnitude
            continue
        if not intruding:
            last_clear_magnitude = magnitude
            continue
        low = float(last_clear_magnitude or release_magnitude)
        high = float(magnitude)
        failure = status
        if refinement_degrees > 0.0:
            while high - low > refinement_degrees:
                middle = 0.5 * (low + high)
                middle_status = evaluate(sign * middle)
                if bool(middle_status["tooth_intrusion"]):
                    high = middle
                    failure = middle_status
                else:
                    low = middle
        first_reentry = dict(failure)
        first_reentry["angle_degrees"] = sign * high
        return (
            sign * low,
            sign * release_magnitude,
            first_reentry,
            initial,
            tuple(evaluated),
        )

    if release_magnitude is None:
        return None, None, None, initial, tuple(evaluated)
    return (
        sign * requested_magnitude,
        sign * release_magnitude,
        None,
        initial,
        tuple(evaluated),
    )


def _one_way_envelope_angles(safe_angle: float, step_degrees: float):
    """按独立包络步长生成含两个端点的单向姿态角。"""

    import numpy as np

    if abs(safe_angle) <= 1.0e-9:
        return np.asarray([0.0])
    interval_count = max(1, int(np.ceil(abs(safe_angle) / step_degrees)))
    return np.linspace(0.0, safe_angle, interval_count + 1)


def _buccal_angles_and_constraints(
    context: GenerationContext,
    parameters: HandpieceAvoidanceParameters,
    body,
    axis,
    pivot,
):
    """允许初始接触并搜索牙体释放后的连续颊侧旋转区间。"""

    import numpy as np
    import trimesh

    missing_fdi, site_centre, outward, neighbor_fdis = _missing_site_outward(
        context, pivot
    )
    handle_direction, handle_vertex_count, maximum_radius = _handle_direction(
        body, pivot, axis
    )
    target_angle, outward_projected = _signed_buccal_target_angle(
        axis, handle_direction, outward
    )
    requested_angle = float(
        np.sign(target_angle)
        * min(abs(target_angle), parameters.maximum_angle_degrees)
    )
    dentition = _load_mesh(
        context.config.inputs.patient_dentition,
        require_volume=False,
        process=True,
    )
    connector, connector_labels = _connector_obstacle(
        context, parameters.connector_clearance_mm
    )
    tooth_crowns, crown_radius, crown_face_count = _tooth_crown_obstacle(
        context, dentition
    )
    dentition_bvh = _mesh_bvh(tooth_crowns)
    connector_bvh = _mesh_bvh(connector)

    def pose_test(angle: float):
        """返回一个旋转姿态的牙体侵入和背 U 梁接触状态。"""

        pose = body.copy()
        pose.apply_transform(
            trimesh.transformations.rotation_matrix(
                np.deg2rad(float(angle)), axis, point=pivot
            )
        )
        pose_bvh = _mesh_bvh(pose)
        tooth_collision = bool(pose_bvh.overlap(dentition_bvh))
        if not tooth_collision and parameters.tooth_clearance_mm > 0.0:
            sample_step = max(1, len(pose.vertices) // 10000)
            _, distances, _ = trimesh.proximity.closest_point(
                tooth_crowns,
                pose.vertices[::sample_step],
            )
            tooth_collision = bool(
                np.min(distances, initial=np.inf) < parameters.tooth_clearance_mm
            )
        connector_collision = bool(pose_bvh.overlap(connector_bvh))
        return {
            "angle_degrees": float(angle),
            "tooth_intrusion": tooth_collision,
            "back_u_connector_contact": connector_collision,
        }

    if parameters.sampling_mode is HandpieceSamplingMode.ADAPTIVE:
        (
            safe_angle,
            first_clear_angle,
            first_failure,
            initial_constraints,
            search_records,
        ) = _signed_release_then_safe_boundary(
            requested_angle,
            parameters.collision_coarse_step_degrees,
            parameters.collision_refinement_degrees,
            pose_test,
        )
    else:
        exact_step = (
            abs(requested_angle) / max(parameters.pose_samples - 1, 1)
            if abs(requested_angle) > 1.0e-9
            else 1.0
        )
        (
            safe_angle,
            first_clear_angle,
            first_failure,
            initial_constraints,
            search_records,
        ) = _signed_release_then_safe_boundary(
            requested_angle,
            exact_step,
            0.0,
            pose_test,
        )
    if safe_angle is None:
        raise GeometryError(
            "手机从输入姿态沿颊侧旋转后牙体侵入始终未解除："
            f"请求角度={requested_angle:.3f}°，"
            f"初始背 U 梁接触={initial_constraints['back_u_connector_contact']}"
        )
    envelope_angles = _one_way_envelope_angles(
        safe_angle,
        parameters.envelope_step_degrees,
    )
    connector_contact_records = tuple(
        item for item in search_records if bool(item["back_u_connector_contact"])
    )
    return envelope_angles, {
        "missing_fdi": missing_fdi,
        "interpolation_neighbor_fdis": list(neighbor_fdis),
        "estimated_site_centre_global_mm": site_centre.astype(float).tolist(),
        "local_outward_global": outward.astype(float).tolist(),
        "local_outward_axis_plane_global": outward_projected.astype(float).tolist(),
        "handle_direction_axis_plane_global": handle_direction.astype(float).tolist(),
        "handle_direction_vertex_count": handle_vertex_count,
        "maximum_rotation_radius_mm": maximum_radius,
        "signed_target_buccal_angle_degrees": target_angle,
        "signed_requested_angle_degrees": requested_angle,
        "signed_safe_angle_degrees": float(safe_angle),
        "initial_pose_constraints": initial_constraints,
        "first_tooth_clearance_angle_degrees": float(first_clear_angle),
        "tooth_intrusion_cleared_during_rotation": True,
        "back_u_connector_contact_is_diagnostic_only": True,
        "back_u_connector_contact_observed": bool(connector_contact_records),
        "back_u_connector_contact_sample_count": len(connector_contact_records),
        "rotation_sign": 0 if requested_angle == 0.0 else int(np.sign(requested_angle)),
        "first_hard_constraint_failure": first_failure,
        "sampling_mode": parameters.sampling_mode.value,
        "collision_search_evaluation_angles_degrees": [
            float(item["angle_degrees"]) for item in search_records
        ],
        "collision_search_evaluation_count": len(search_records),
        "collision_coarse_step_degrees": parameters.collision_coarse_step_degrees,
        "collision_refinement_degrees": parameters.collision_refinement_degrees,
        "envelope_step_degrees": parameters.envelope_step_degrees,
        "envelope_pose_count": int(len(envelope_angles)),
        "protected_back_u_connector_labels": list(connector_labels),
        "tooth_clearance_mm": parameters.tooth_clearance_mm,
        "protected_tooth_crown_radius_mm": crown_radius,
        "protected_tooth_crown_face_count": crown_face_count,
        "connector_clearance_mm": parameters.connector_clearance_mm,
        "constraint_semantics": {
            "dentition": (
                "initial intrusion is allowed, but mapped tooth-crown intrusion must "
                "clear during the requested buccal rotation"
            ),
            "back_u_connector": (
                "contact is diagnostic during sweep planning; post-cut connector "
                "retention and topology QA determine acceptance"
            ),
            "guide": (
                "locally removable; final topology, connector retention, and terminal "
                "node QA are mandatory"
            ),
        },
    }


def _cached_plan(
    envelope_path: Path,
    report_path: Path,
    fingerprint: dict[str, object],
    avoidance_id: str,
    extra_clearance_mm: float,
    *,
    validate_mesh: bool = True,
) -> HandpieceAvoidancePlan | None:
    """校验缓存报告与包络并返回可复用计划。"""

    if not envelope_path.is_file() or not report_path.is_file():
        return None
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if report.get("fingerprint") != fingerprint:
            return None
        motion = report["motion_model"]
        envelope_info = report.get("envelope", {})
        if not isinstance(envelope_info, dict) or not envelope_info.get("is_closed_volume"):
            return None
        if validate_mesh:
            envelope = _load_mesh(envelope_path)
            if not envelope.is_volume:
                return None
        return HandpieceAvoidancePlan(
            avoidance_id=avoidance_id,
            envelope_mesh_path=envelope_path,
            report_path=report_path,
            rotation_axis=tuple(float(value) for value in motion["rotation_axis"]),
            pivot=tuple(float(value) for value in motion["pivot_global_mm"]),
            matched_stop_patch_ids=tuple(motion["matched_stop_patch_ids"]),
            angle_samples_degrees=tuple(
                float(value) for value in motion["angle_samples_degrees"]
            ),
            extra_clearance_mm=extra_clearance_mm,
            cache_reused=True,
        )
    except (OSError, ValueError, TypeError, KeyError, GeometryError):
        return None


def _adjust_single_handpiece(
    context: GenerationContext,
    parameters: HandpieceAvoidanceParameters,
    output_directory: Path,
    *,
    validate_cached_geometry: bool,
    force_rebuild: bool,
) -> HandpieceAvoidancePlan:
    """为一个已配置手机生成或复用当前深度旋转包络。"""

    import numpy as np
    import trimesh

    output_directory.mkdir(parents=True, exist_ok=True)
    envelope_filename = (
        "handpiece_current_depth_buccal_sweep_envelope.ply"
        if parameters.motion_mode is HandpieceMotionMode.BUCCAL_OUTWARD
        else "handpiece_current_depth_lr_sweep_envelope.ply"
    )
    envelope_path = output_directory / envelope_filename
    report_path = output_directory / "handpiece_avoidance.json"

    all_components = _load_mesh(
        parameters.handpiece,
        require_volume=False,
        process=True,
    ).split(only_watertight=False)
    if not all_components:
        raise GeometryError("手机 STL 不包含可用连通分量")
    body = max(all_components, key=lambda mesh: float(mesh.area))
    if not body.is_volume:
        raise GeometryError("手机 STL 最大面积连通分量不是封闭有效体")

    if parameters.stop_report is not None:
        axis, pivot, matched_ids, centroids = _stop_geometry(parameters.stop_report)
        rotation_axis_definition = "pair_axis from handpiece stop report"
        pivot_definition = "midpoint of matched left/right phone stop-face centroids"
        pair_match_distance_mm = None
    elif parameters.motion_mode is HandpieceMotionMode.BUCCAL_OUTWARD:
        (
            pair_match_distance_mm,
            axis,
            pivot,
            matched_ids,
            centroids,
        ) = _derived_pair_geometry(context, body)
        rotation_axis_definition = "mean axis of automatically matched guide pair"
        pivot_definition = "midpoint of matched guide axial midpoints"
    else:
        raise GeometryError("当前手机旋转模式需要 handpiece stop report")

    constraint_report = None
    constraint_started = time.perf_counter()
    if parameters.motion_mode is HandpieceMotionMode.BUCCAL_OUTWARD:
        try:
            angles, constraint_report = _buccal_angles_and_constraints(
                context, parameters, body, axis, pivot
            )
        except GeometryError as error:
            raise GeometryError(
                f"手机 {parameters.avoidance_id}：{error}"
            ) from error
        constraint_inputs = {
            "dentition_sha256": _sha256(context.config.inputs.patient_dentition),
            # 牙位报告包含 created_at 和输出路径，整文件哈希会导致相同
            # 映射每次重跑都误判缓存失效。这里只指纹化本阶段实际消费的
            # 稳定几何语义。
            "tooth_mapping_geometry": [
                {
                    "fdi": position.fdi,
                    "crown_point_global_mm": list(position.crown_point.as_tuple()),
                    "local_outward_global": list(position.local_outward.as_tuple()),
                }
                for position in (
                    context.tooth_identification.positions
                    if context.tooth_identification is not None
                    else ()
                )
            ],
            "protected_tooth_crown_radius_mm": constraint_report[
                "protected_tooth_crown_radius_mm"
            ],
            "missing_fdi": constraint_report["missing_fdi"],
            "local_outward_global": constraint_report["local_outward_global"],
            "protected_back_u_connector_labels": constraint_report[
                "protected_back_u_connector_labels"
            ],
            "connector_centerlines": [
                [list(point.as_tuple()) for point in link.centerline]
                for link in (context.point_linking.links if context.point_linking else ())
                if link.arch_side is GuideAnchorSide.BACK_U_SIDE
            ],
            "rotation_axis": axis.astype(float).tolist(),
            "pivot_global_mm": pivot.astype(float).tolist(),
            "matched_pair_ids": list(matched_ids),
        }
    else:
        angles = np.linspace(
            -parameters.maximum_angle_degrees,
            parameters.maximum_angle_degrees,
            parameters.pose_samples,
        )
        constraint_inputs = None
    constraint_elapsed_seconds = time.perf_counter() - constraint_started
    if not np.any(np.isclose(angles, 0.0)):
        raise GeometryError("手机姿态采样未包含 0° 当前装配姿态")
    fingerprint = _fingerprint(parameters, constraint_inputs)
    cached = (
        None
        if force_rebuild
        else _cached_plan(
            envelope_path,
            report_path,
            fingerprint,
            parameters.avoidance_id,
            parameters.extra_clearance_mm,
            validate_mesh=validate_cached_geometry,
        )
    )
    if cached is not None:
        return cached

    poses = []
    for angle in angles:
        pose = body.copy()
        pose.apply_transform(
            trimesh.transformations.rotation_matrix(
                np.deg2rad(float(angle)),
                axis,
                point=pivot,
            )
        )
        poses.append(pose)
    union_started = time.perf_counter()
    simplify_tolerance_mm = (
        parameters.envelope_simplify_tolerance_mm
        if parameters.sampling_mode is HandpieceSamplingMode.ADAPTIVE
        else 0.0
    )
    envelope_raw = _boolean_union(
        poses,
        parameters.union_batch_size,
        simplify_tolerance_mm=simplify_tolerance_mm,
    )
    union_elapsed_seconds = time.perf_counter() - union_started
    components = sorted(
        envelope_raw.split(only_watertight=False),
        key=lambda mesh: abs(float(mesh.volume)),
        reverse=True,
    )
    if not components:
        raise GeometryError("手机姿态包络为空")
    discarded_volumes = [abs(float(mesh.volume)) for mesh in components[1:]]
    significant = [
        volume
        for volume in discarded_volumes
        if volume > FRAGMENT_VOLUME_TOLERANCE_MM3
    ]
    if significant:
        raise GeometryError(f"手机姿态并集产生独立有效分量：{significant}")
    envelope = components[0]
    envelope.remove_unreferenced_vertices()
    if not envelope.is_volume:
        raise GeometryError("手机左右摆动包络不是封闭有效体")
    envelope.export(envelope_path)

    radial_delta = body.vertices - pivot
    radial_distance = np.linalg.norm(
        radial_delta - np.outer(radial_delta @ axis, axis),
        axis=1,
    )
    maximum_angle_step = (
        float(np.max(np.abs(np.diff(angles)))) if len(angles) > 1 else 0.0
    )
    maximum_rotation_radius = float(np.max(radial_distance))
    maximum_half_step_displacement = float(
        2.0
        * maximum_rotation_radius
        * np.sin(np.deg2rad(maximum_angle_step / 2.0) / 2.0)
    )
    report = {
        "status": "completed",
        "avoidance_id": parameters.avoidance_id,
        "fingerprint": fingerprint,
        "inputs": {
            "handpiece": str(parameters.handpiece),
            "stop_report": (
                str(parameters.stop_report)
                if parameters.stop_report is not None
                else None
            ),
        },
        "motion_model": {
            "moving_component": "largest-area connected component of handpiece STL",
            "axial_depth_range_mm": [0.0, 0.0],
            "rotation_axis_definition": rotation_axis_definition,
            "rotation_axis": axis.astype(float).tolist(),
            "pivot_definition": pivot_definition,
            "matched_stop_patch_ids": list(matched_ids),
            "matched_stop_centroids_mm": centroids.astype(float).tolist(),
            "automatic_pair_match_surface_distance_mm": pair_match_distance_mm,
            "pivot_global_mm": pivot.astype(float).tolist(),
            "angle_range_degrees": [float(angles[0]), float(angles[-1])],
            "angle_samples_degrees": angles.astype(float).tolist(),
            "pose_count": len(poses),
            "maximum_angle_step_degrees": maximum_angle_step,
            "maximum_rotation_radius_mm": maximum_rotation_radius,
            "maximum_half_step_unsampled_displacement_mm": maximum_half_step_displacement,
            "extra_clearance_mm": parameters.extra_clearance_mm,
            "motion_mode": parameters.motion_mode.value,
            "sampling_mode": parameters.sampling_mode.value,
            "envelope_simplify_tolerance_mm": simplify_tolerance_mm,
            "sweep_semantics": (
                "one-way rotation from input pose toward local buccal/back-U direction"
                if parameters.motion_mode is HandpieceMotionMode.BUCCAL_OUTWARD
                else "signed one-axis left/right rotation at current depth"
            ),
        },
        "buccal_outward_constraints": constraint_report,
        "performance": {
            "constraint_search_seconds": constraint_elapsed_seconds,
            "envelope_union_seconds": union_elapsed_seconds,
        },
        "envelope": {
            "mesh": str(envelope_path),
            "is_closed_volume": bool(envelope.is_volume),
            "volume_mm3": abs(float(envelope.volume)),
            "vertex_count": len(envelope.vertices),
            "face_count": len(envelope.faces),
            "discarded_fragment_volumes_mm3": discarded_volumes,
        },
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return HandpieceAvoidancePlan(
        avoidance_id=parameters.avoidance_id,
        envelope_mesh_path=envelope_path,
        report_path=report_path,
        rotation_axis=tuple(float(value) for value in axis),
        pivot=tuple(float(value) for value in pivot),
        matched_stop_patch_ids=matched_ids,
        angle_samples_degrees=tuple(float(value) for value in angles),
        extra_clearance_mm=parameters.extra_clearance_mm,
        cache_reused=False,
    )


def adjust_clearance(
    context: GenerationContext,
    *,
    validate_cached_geometry: bool = True,
    force_rebuild: bool = False,
) -> tuple[HandpieceAvoidancePlan, ...]:
    """按配置生成或复用所有手机当前深度旋转包络。

    两种策略都使用止挡报告的双导管公共轴及两片匹配手机止挡面中点，
    且不做轴向位移。``symmetric_lr`` 保留旧版对称扫掠；
    ``buccal_outward`` 从输入姿态只向牙位局部背 U 侧旋转，并在首次
    牙体侵入或背 U 连接梁接触前停止。本函数只规划 cutter，最终整体
    差集在 Blender 实体化末尾执行。

    参数:
        context: 已完成联建且包含病例配置的生成上下文。

    返回:
        按配置顺序排列的封闭包络计划；每个手机对应一个独立计划。
    """

    parameters = context.config.handpiece_avoidance
    if not parameters:
        raise GeometryError("病例未配置 handpiece_avoidance")
    root = context.config.output_directory / "handpiece_avoidance"
    multiple = len(parameters) > 1
    return tuple(
        _adjust_single_handpiece(
            context,
            item,
            root / item.avoidance_id if multiple else root,
            validate_cached_geometry=validate_cached_geometry,
            force_rebuild=force_rebuild,
        )
        for item in parameters
    )

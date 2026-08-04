"""生成当前装配深度下的牙科手机左右摆动避障包络。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from twin_guide.config import HandpieceAvoidanceParameters
from twin_guide.errors import GeometryError
from twin_guide.types import GenerationContext

if TYPE_CHECKING:
    import numpy as np
    import trimesh

ALGORITHM_VERSION = "current-depth-signed-lr-sweep-v1"
FRAGMENT_VOLUME_TOLERANCE_MM3 = 1.0e-4
FRAGMENT_SURFACE_AREA_TOLERANCE_MM2 = 1.0e-4


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


def _unit(vector: np.ndarray) -> np.ndarray:
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
) -> trimesh.Trimesh:
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
    meshes: list[trimesh.Trimesh],
    batch_size: int,
) -> trimesh.Trimesh:
    """用 manifold3d 分批精确合并一组封闭网格。"""

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
            next_level.append(result)
        current = next_level
    return current[0]


def _signed_volume(mesh: trimesh.Trimesh) -> float:
    """不计算质心，直接返回三角网格有向体积。"""

    import numpy as np

    triangles = np.asarray(mesh.triangles, dtype=float)
    if len(triangles) == 0:
        return 0.0
    return float(
        np.einsum(
            "ij,ij->i",
            triangles[:, 0],
            np.cross(triangles[:, 1], triangles[:, 2]),
        ).sum()
        / 6.0
    )


def _stop_geometry(
    stop_report: Path,
) -> tuple[np.ndarray, np.ndarray, tuple[str, str], np.ndarray]:
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


def _fingerprint(parameters: HandpieceAvoidanceParameters) -> dict[str, object]:
    """构造与包络几何一一对应的缓存指纹。"""

    return {
        "algorithm_version": ALGORITHM_VERSION,
        "handpiece_sha256": _sha256(parameters.handpiece),
        "stop_report_sha256": _sha256(parameters.stop_report),
        "maximum_angle_degrees": parameters.maximum_angle_degrees,
        "pose_samples": parameters.pose_samples,
        "union_batch_size": parameters.union_batch_size,
    }


def _cached_plan(
    envelope_path: Path,
    report_path: Path,
    fingerprint: dict[str, object],
    avoidance_id: str,
    extra_clearance_mm: float,
    *,
    validate_mesh: bool,
) -> HandpieceAvoidancePlan | None:
    """校验缓存报告与包络并返回可复用计划。"""

    if not envelope_path.is_file() or not report_path.is_file():
        return None
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if report.get("fingerprint") != fingerprint:
            return None
        motion = report["motion_model"]
        envelope = report["envelope"]
        if not envelope.get("is_closed_volume"):
            return None
        if validate_mesh and not _load_mesh(envelope_path).is_volume:
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
    """为一个已配置手机生成或复用当前深度左右摆动包络。"""

    import numpy as np
    import trimesh

    output_directory.mkdir(parents=True, exist_ok=True)
    envelope_path = output_directory / "handpiece_current_depth_lr_sweep_envelope.ply"
    report_path = output_directory / "handpiece_avoidance.json"
    fingerprint = _fingerprint(parameters)
    cached = None
    if not force_rebuild:
        cached = _cached_plan(
            envelope_path,
            report_path,
            fingerprint,
            parameters.avoidance_id,
            parameters.extra_clearance_mm,
            validate_mesh=validate_cached_geometry,
        )
    if cached is not None:
        return cached

    axis, pivot, matched_ids, centroids = _stop_geometry(parameters.stop_report)
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

    angles = np.linspace(
        -parameters.maximum_angle_degrees,
        parameters.maximum_angle_degrees,
        parameters.pose_samples,
    )
    if not np.any(np.isclose(angles, 0.0)):
        raise GeometryError("手机姿态采样未包含 0° 当前装配姿态")
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
    envelope_raw = _boolean_union(poses, parameters.union_batch_size)
    raw_components = envelope_raw.split(only_watertight=False)
    valid_components = []
    invalid_component_areas = []
    for component in raw_components:
        signed_volume = _signed_volume(component)
        if (
            component.is_watertight
            and component.is_winding_consistent
            and signed_volume > 0.0
        ):
            valid_components.append((component, signed_volume))
        elif float(component.area) > FRAGMENT_SURFACE_AREA_TOLERANCE_MM2:
            invalid_component_areas.append(float(component.area))
    if invalid_component_areas:
        raise GeometryError(
            "手机姿态并集产生非封闭有效分量，表面积 mm²："
            f"{invalid_component_areas}"
        )
    components = sorted(valid_components, key=lambda item: item[1], reverse=True)
    if not components:
        raise GeometryError("手机姿态包络为空")
    discarded_volumes = [volume for _, volume in components[1:]]
    significant = [
        volume
        for volume in discarded_volumes
        if volume > FRAGMENT_VOLUME_TOLERANCE_MM3
    ]
    if significant:
        raise GeometryError(f"手机姿态并集产生独立有效分量：{significant}")
    envelope = components[0][0]
    envelope.remove_unreferenced_vertices()
    if not envelope.is_volume:
        raise GeometryError("手机左右摆动包络不是封闭有效体")
    envelope.export(envelope_path)

    radial_delta = body.vertices - pivot
    radial_distance = np.linalg.norm(
        radial_delta - np.outer(radial_delta @ axis, axis),
        axis=1,
    )
    maximum_angle_step = float(np.max(np.diff(angles)))
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
            "stop_report": str(parameters.stop_report),
        },
        "motion_model": {
            "moving_component": "largest-area connected component of handpiece STL",
            "axial_depth_range_mm": [0.0, 0.0],
            "rotation_axis_definition": "pair_axis from handpiece stop report",
            "rotation_axis": axis.astype(float).tolist(),
            "pivot_definition": "midpoint of matched left/right phone stop-face centroids",
            "matched_stop_patch_ids": list(matched_ids),
            "matched_stop_centroids_mm": centroids.astype(float).tolist(),
            "pivot_global_mm": pivot.astype(float).tolist(),
            "angle_range_degrees": [float(angles[0]), float(angles[-1])],
            "angle_samples_degrees": angles.astype(float).tolist(),
            "pose_count": len(poses),
            "maximum_angle_step_degrees": maximum_angle_step,
            "maximum_rotation_radius_mm": maximum_rotation_radius,
            "maximum_half_step_unsampled_displacement_mm": maximum_half_step_displacement,
            "extra_clearance_mm": parameters.extra_clearance_mm,
            "sweep_semantics": "signed one-axis left/right rotation at current depth",
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
    """生成或复用所有手机当前深度 ``-θ`` 至 ``+θ`` 左右摆动包络。

    旋转轴和枢轴分别取止挡报告的双导管轴与两片匹配手机止挡面的
    中点。手机不做任何轴向位移；只使用输入手机最大面积连通分量。
    本函数只规划 cutter，最终整体差集在 Blender 实体化末尾执行。

    参数:
        context: 已完成联建且包含病例配置的生成上下文。

    返回:
        按配置顺序排列的封闭包络计划；每个手机对应一个独立计划。
    """

    parameters = context.config.handpiece_avoidance
    if not parameters:
        raise GeometryError("病例未配置 handpiece_avoidance")
    root = (
        context.config.output_directory
        / ".cache"
        / "stage-07-clearance-adjustment"
    )
    return tuple(
        _adjust_single_handpiece(
            context,
            item,
            root / "handpieces" / item.avoidance_id,
            validate_cached_geometry=validate_cached_geometry,
            force_rebuild=force_rebuild,
        )
        for item in parameters
    )

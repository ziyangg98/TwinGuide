"""牙位截面锚点内部实现。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import trimesh

from twin_guide.clearance import channel_distance, window_distance
from twin_guide.config import (
    DEFAULT_GUIDE_ANCHOR_BACK_U_SIDE_RAY_ANGLE_DEGREES,
    DEFAULT_GUIDE_ANCHOR_U_SIDE_RAY_ANGLE_DEGREES,
    GuideAnchorLocation,
    GuideAnchorSide,
    ToothAnchorStation,
)
from twin_guide.errors import GeometryError
from twin_guide.geometry import Vec3
from twin_guide.models import CaseAnalysis, CutoutPlan, GuideSleeve
from twin_guide.tooth_identification import ToothIdentificationResult, ToothPosition
from twin_guide.types import SleeveGenerationResult

EPS = 1e-9
U_SIDE_MARGIN_MM = 0.5
VISIBILITY_GAP_BRIDGE_MM = 0.75
OUTER_WALL_ALIGNMENT_COSINE = 0.80
ANCHOR_AXIS_DROP_MM = 2.0
RAY_DUPLICATE_HIT_TOLERANCE_MM = 0.02
RAY_EXIT_NORMAL_ALIGNMENT_MIN = 0.05


@dataclass(frozen=True, slots=True)
class ToothSectionSurfaceAnchor:
    """牙位几何方法确定的一个导板表面锚点。"""

    position: Vec3
    normal: Vec3
    polygon_index: int


@dataclass(frozen=True, slots=True)
class ToothSectionAnchorSelection:
    """一个牙位站位对应的旋转面、双向射线和双锚点。"""

    station_fdis: tuple[int, ...]
    plane_origin: Vec3
    plane_normal: Vec3
    outward_direction: Vec3
    trajectory: tuple[Vec3, ...]
    support_trajectories: tuple[tuple[Vec3, ...], tuple[Vec3, ...]]
    first: ToothSectionSurfaceAnchor
    second: ToothSectionSurfaceAnchor
    chosen_ray_angles_degrees: tuple[float, float]


@dataclass(frozen=True, slots=True)
class ToothSectionAnchorCandidateSet:
    """一个牙位完整外表面轨迹上的可行单锚点候选。"""

    station_fdis: tuple[int, ...]
    plane_origin: Vec3
    plane_normal: Vec3
    outward_direction: Vec3
    trajectory: tuple[Vec3, ...]
    candidates: tuple[ToothSectionSurfaceAnchor, ...]
    candidate_fractions: tuple[float, ...]
    candidate_arch_outward_coordinates_mm: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class ToothSectionSingleRayAnchorSelection:
    """一个牙位站位对应的单侧旋转射线及其导板外壁锚点。"""

    station_fdis: tuple[int, ...]
    ray_origin: Vec3
    plane_normal: Vec3
    outward_direction: Vec3
    trajectory: tuple[Vec3, ...]
    anchor: ToothSectionSurfaceAnchor
    ray_angle_degrees: float
    arch_outward_coordinate_mm: float
    reference_fdis: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class IndependentGuideAnchorSelection:
    """一个独立配置的牙位射线及其导板外壁锚点。"""

    configuration: GuideAnchorLocation
    station_fdis: tuple[int, ...]
    ray_origin: Vec3
    plane_normal: Vec3
    outward_direction: Vec3
    support_trajectory: tuple[Vec3, ...]
    anchor: ToothSectionSurfaceAnchor
    arch_outward_coordinate_mm: float


def _unit(vector: np.ndarray) -> np.ndarray:
    """返回非零数组向量的单位方向。"""

    length = float(np.linalg.norm(vector))
    if length <= EPS:
        raise GeometryError("牙位截面方向为零向量")
    return np.asarray(vector, dtype=float) / length


def _vec3(vector: np.ndarray) -> Vec3:
    """将三维数组转换为不可变世界坐标向量。"""

    return Vec3(*(float(value) for value in vector))


def _load_welded(path: object) -> trimesh.Trimesh:
    """读取、焊接并统一法向的三角网格。"""

    loaded = trimesh.load(path, force="mesh", process=False)
    if isinstance(loaded, trimesh.Scene):
        loaded = loaded.dump(concatenate=True)
    if not isinstance(loaded, trimesh.Trimesh) or loaded.is_empty:
        raise GeometryError(f"牙位锚点输入导板为空：{path}")
    mesh = loaded.copy()
    mesh.merge_vertices()
    mesh.remove_unreferenced_vertices()
    mesh.fix_normals()
    return mesh


def _polyline_length(points: np.ndarray) -> float:
    """返回折线总弧长。"""

    return float(np.sum(np.linalg.norm(np.diff(points, axis=0), axis=1)))


def _split_valid_runs(points: np.ndarray, mask: np.ndarray) -> tuple[np.ndarray, ...]:
    """将闭合或开放截面按有效掩码分割为连续轨迹。"""

    closed = len(points) > 2 and float(np.linalg.norm(points[0] - points[-1])) <= 1e-5
    if closed:
        points = points[:-1]
        mask = mask[:-1]
    if len(points) < 2 or not np.any(mask):
        return ()
    if closed and np.all(mask):
        return (np.vstack((points, points[0])),)
    if closed:
        false_index = int(np.flatnonzero(~mask)[0])
        order = np.r_[
            np.arange(false_index + 1, len(points)),
            np.arange(0, false_index + 1),
        ]
        points = points[order]
        mask = mask[order]
    runs = []
    start = None
    for index, valid in enumerate(np.r_[mask, False]):
        if valid and start is None:
            start = index
        elif not valid and start is not None:
            if index - start >= 2:
                runs.append(points[start:index])
            start = None
    return tuple(runs)


def _bridge_short_visibility_gaps(
    points: np.ndarray,
    mask: np.ndarray,
    maximum_gap_mm: float = VISIBILITY_GAP_BRIDGE_MM,
) -> np.ndarray:
    """桥接外表面轨迹中由网格边缘造成的短无效间断。"""

    result = np.asarray(mask, dtype=bool).copy()
    closed = len(points) > 2 and float(np.linalg.norm(points[0] - points[-1])) <= 1e-5
    core_points = points[:-1] if closed else points
    core_mask = result[:-1].copy() if closed else result
    count = len(core_points)
    if count < 3 or not np.any(core_mask):
        return result
    false_indices = {int(index) for index in np.flatnonzero(~core_mask)}
    while false_indices:
        seed = false_indices.pop()
        component = {seed}
        pending = [seed]
        while pending:
            index = pending.pop()
            neighbours = (index - 1, index + 1)
            for neighbour in neighbours:
                if closed:
                    neighbour %= count
                elif neighbour < 0 or neighbour >= count:
                    continue
                if neighbour in false_indices:
                    false_indices.remove(neighbour)
                    component.add(neighbour)
                    pending.append(neighbour)
        start = next(
            (
                index
                for index in component
                if (closed or index > 0)
                and core_mask[(index - 1) % count]
            ),
            None,
        )
        if start is None:
            continue
        previous = (start - 1) % count
        path_indices = [previous, start]
        index = start
        while True:
            index = (index + 1) % count if closed else index + 1
            if index >= count or index == previous:
                path_indices = []
                break
            path_indices.append(index)
            if core_mask[index]:
                break
        if not path_indices:
            continue
        gap_length = float(
            np.sum(
                np.linalg.norm(
                    np.diff(core_points[np.asarray(path_indices)], axis=0),
                    axis=1,
                )
            )
        )
        if gap_length <= maximum_gap_mm:
            core_mask[list(component)] = True
    if closed:
        result[:-1] = core_mask
        result[-1] = core_mask[0]
    else:
        result = core_mask
    return result


def _visible_from_directions(
    mesh: trimesh.Trimesh,
    points: np.ndarray,
    candidate_mask: np.ndarray,
    outward_directions: np.ndarray,
) -> np.ndarray:
    """沿每个样本各自的背牙方向，以射线首交点判定可见外表面。"""

    result = np.zeros(len(points), dtype=bool)
    candidates = np.flatnonzero(candidate_mask)
    if not len(candidates):
        return result
    directions_from_surface = outward_directions[candidates]
    origins = points[candidates] + 100.0 * directions_from_surface
    directions = -directions_from_surface
    locations, ray_indices, _ = mesh.ray.intersects_location(
        ray_origins=origins,
        ray_directions=directions,
        multiple_hits=True,
    )
    first_hits: dict[int, tuple[float, np.ndarray]] = {}
    for location, ray_index in zip(locations, ray_indices, strict=True):
        distance = float(np.dot(location - origins[ray_index], directions[ray_index]))
        if distance < 0.0:
            continue
        if ray_index not in first_hits or distance < first_hits[ray_index][0]:
            first_hits[int(ray_index)] = distance, np.asarray(location, dtype=float)
    for local_index, point_index in enumerate(candidates):
        hit = first_hits.get(local_index)
        if hit is not None and float(np.linalg.norm(hit[1] - points[point_index])) <= 0.25:
            result[point_index] = True
    return result


def _directions_away_from_teeth(
    dentition: trimesh.Trimesh,
    points: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """返回每个导板点从最近牙面指向该点的单位方向及牙面距离。"""

    tooth_points, distances, _ = trimesh.proximity.closest_point(dentition, points)
    offsets = points - tooth_points
    lengths = np.linalg.norm(offsets, axis=1)
    valid = lengths > EPS
    directions = np.zeros_like(offsets)
    directions[valid] = offsets[valid] / lengths[valid, None]
    return directions, np.asarray(distances, dtype=float)


def _profile_cutters(cutouts: CutoutPlan) -> tuple[trimesh.Trimesh, ...]:
    """读取第 3 步变截面观察窗切割体。"""

    result = []
    for profile in cutouts.profile_windows:
        loaded = trimesh.load_mesh(profile.cutter_mesh_path, process=False)
        cutter = (
            trimesh.util.concatenate(tuple(loaded.geometry.values()))
            if isinstance(loaded, trimesh.Scene)
            else loaded
        )
        if not isinstance(cutter, trimesh.Trimesh) or cutter.is_empty:
            raise GeometryError(f"观察窗切割体为空：{profile.cutter_mesh_path}")
        result.append(cutter)
    return tuple(result)


def _cutout_clearance_mask(
    points: np.ndarray,
    cutouts: CutoutPlan,
    profile_cutters: tuple[trimesh.Trimesh, ...],
    clearance_mm: float,
) -> np.ndarray:
    """返回满足全部导孔和窗口净距的轨迹点掩码。"""

    retained = np.asarray(
        [
            all(
                window_distance(_vec3(point), window) >= clearance_mm
                for window in cutouts.windows
            )
            and all(
                channel_distance(
                    _vec3(point), channel.start, channel.end, channel.radius_mm
                )
                >= clearance_mm
                for channel in cutouts.channels
            )
            for point in points
        ],
        dtype=bool,
    )
    for cutter in profile_cutters:
        local_indices = np.flatnonzero(retained)
        if not len(local_indices):
            break
        local_points = points[local_indices]
        _, distances, _ = cutter.nearest.on_surface(local_points)
        local_retained = ~cutter.contains(local_points)
        local_retained &= distances >= clearance_mm
        retained[local_indices] = local_retained
    return retained


def _point_at_fraction(
    points: np.ndarray, fraction: float
) -> tuple[np.ndarray, np.ndarray]:
    """按折线弧长比例返回插值点和局部切向。"""

    lengths = np.linalg.norm(np.diff(points, axis=0), axis=1)
    cumulative = np.r_[0.0, np.cumsum(lengths)]
    total = float(cumulative[-1])
    if total <= EPS:
        raise GeometryError("牙位截面外表面轨迹长度为零")
    target = fraction * total
    segment = min(
        int(np.searchsorted(cumulative, target, side="right") - 1),
        len(lengths) - 1,
    )
    alpha = float((target - cumulative[segment]) / lengths[segment])
    point = (1.0 - alpha) * points[segment] + alpha * points[segment + 1]
    return point, _unit(points[segment + 1] - points[segment])


def _measure_thicknesses(
    mesh: trimesh.Trimesh, points: np.ndarray, normals: np.ndarray
) -> np.ndarray:
    """沿局部内法向测量导板厚度。"""

    origins = points + 0.0001 * normals
    directions = -normals
    locations, ray_indices, _ = mesh.ray.intersects_location(
        ray_origins=origins,
        ray_directions=directions,
        multiple_hits=True,
    )
    hits: dict[int, list[float]] = {index: [] for index in range(len(points))}
    for location, ray_index in zip(locations, ray_indices, strict=True):
        distance = float(np.dot(location - points[ray_index], directions[ray_index]))
        if distance >= 0.05:
            hits[int(ray_index)].append(distance)
    return np.asarray(
        [min(hits[index]) if hits[index] else np.nan for index in range(len(points))]
    )


def _anchor_candidate(
    mesh: trimesh.Trimesh,
    dentition: trimesh.Trimesh,
    point: np.ndarray,
    curve_tangent: np.ndarray,
    occlusal: np.ndarray,
    cutouts: CutoutPlan,
    profile_cutters: tuple[trimesh.Trimesh, ...],
    clearance_mm: float,
    patch_clearance_mm: float,
    patch_projection_limit_mm: float = 0.65,
) -> ToothSectionSurfaceAnchor | None:
    """验证轨迹点的完整中心净距以及局部补丁质量和安全余量。"""

    closest, distance, triangle_id = trimesh.proximity.closest_point(mesh, [point])
    anchor = np.asarray(closest[0], dtype=float)
    face_index = int(triangle_id[0])
    normal = _unit(np.asarray(mesh.face_normals[face_index], dtype=float))
    away, _ = _directions_away_from_teeth(dentition, np.asarray([anchor]))
    if (
        float(distance[0]) > 0.01
        or float(np.dot(normal, away[0])) < 0.15
        or abs(float(np.dot(normal, occlusal))) > 0.92
        or not _visible_from_directions(
            mesh,
            np.asarray([anchor]),
            np.asarray([True]),
            away,
        )[0]
        or not _cutout_clearance_mask(
            np.asarray([anchor]),
            cutouts,
            profile_cutters,
            clearance_mm,
        )[0]
    ):
        return None
    tangent = curve_tangent - float(np.dot(curve_tangent, normal)) * normal
    tangent = _unit(tangent)
    cross = _unit(np.cross(normal, tangent))
    targets = [anchor]
    for ring_radius in (0.75, 1.50):
        for angle in np.linspace(0.0, 2.0 * np.pi, 12, endpoint=False):
            targets.append(
                anchor
                + ring_radius
                * (np.cos(angle) * tangent + np.sin(angle) * cross)
            )
    targets = np.asarray(targets)
    patch_points, patch_distances, patch_triangles = trimesh.proximity.closest_point(
        mesh, targets
    )
    patch_normals = np.asarray(mesh.face_normals)[patch_triangles]
    patch_away, _ = _directions_away_from_teeth(dentition, patch_points)
    if (
        np.any(patch_distances > patch_projection_limit_mm)
        or np.any(np.sum(patch_normals * patch_away, axis=1) < 0.10)
        or np.any(patch_normals @ normal < 0.25)
        or not np.all(
            _visible_from_directions(
                mesh,
                patch_points,
                np.ones(len(patch_points), dtype=bool),
                patch_away,
            )
        )
        or not np.all(
            _cutout_clearance_mask(
                patch_points,
                cutouts,
                profile_cutters,
                patch_clearance_mm,
            )
        )
    ):
        return None
    thicknesses = _measure_thicknesses(mesh, patch_points, patch_normals)
    if np.any(~np.isfinite(thicknesses)) or float(np.min(thicknesses)) < 1.0:
        return None
    return ToothSectionSurfaceAnchor(_vec3(anchor), _vec3(normal), face_index)


def _station_geometry(
    station: ToothAnchorStation,
    positions: dict[int, ToothPosition],
    fdi_order: tuple[int, ...] = (),
    missing_teeth: tuple[int, ...] = (),
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """计算切面原点、牙弓切向、牙弓外向和牙齿中心参考点。"""

    selected = []
    for fdi in station.fdis:
        tooth = positions.get(fdi)
        if tooth is None:
            tooth = _interpolate_missing_tooth(
                fdi,
                positions,
                fdi_order,
                missing_teeth,
            )
        selected.append(tooth)
    # 切面法向是水平牙弓切向，因此原点的牙合高度不改变该竖直平面。
    # 当牙位映射无法给出 guide_top 时，使用牙冠中心仅定义切面站位；
    # 后续仍必须与真实导板网格相交并通过可见性、厚度和净距 QA。
    origins = np.asarray(
        [
            (tooth.guide_top or tooth.crown_point).as_tuple()
            for tooth in selected
        ]
    )
    tooth_centers = np.asarray([tooth.crown_point.as_tuple() for tooth in selected])
    tangents = np.asarray([tooth.local_tangent.as_tuple() for tooth in selected])
    outwards = np.asarray([tooth.local_outward.as_tuple() for tooth in selected])
    return (
        np.mean(origins, axis=0),
        _unit(np.sum(tangents, axis=0)),
        _unit(np.sum(outwards, axis=0)),
        np.mean(tooth_centers, axis=0),
    )


def _covered_neighbour_reference_tangent(
    station: ToothAnchorStation,
    positions: dict[int, ToothPosition],
    fdi_order: tuple[int, ...],
    missing_teeth: tuple[int, ...],
) -> tuple[np.ndarray, tuple[int, ...]]:
    """以双牙中心线或最近的导板覆盖邻牙确定局部牙弓切向。"""

    if len(station.fdis) == 2:
        selected = []
        for fdi in station.fdis:
            tooth = positions.get(fdi)
            if tooth is None or tooth.guide_top is None or fdi in missing_teeth:
                raise GeometryError(
                    f"双牙站位 {station.fdis} 的牙位 {fdi} 没有真实导板覆盖"
                )
            selected.append(tooth)
        direction = np.asarray(
            (selected[1].crown_point - selected[0].crown_point).as_tuple(),
            dtype=float,
        )
        return _unit(direction), station.fdis

    target_fdi = station.fdis[0]
    target = positions.get(target_fdi)
    if target is None or target_fdi in missing_teeth:
        raise GeometryError(f"单牙站位 {target_fdi} 不是可用的真实牙位")
    try:
        target_index = fdi_order.index(target_fdi)
    except ValueError as error:
        raise GeometryError(f"牙位 {target_fdi} 不在 FDI 牙弓顺序中") from error

    candidates = []
    for step in (-1, 1):
        index = target_index + step
        while 0 <= index < len(fdi_order):
            neighbour_fdi = fdi_order[index]
            neighbour = positions.get(neighbour_fdi)
            if neighbour is not None and neighbour.guide_top is not None:
                candidates.append((neighbour, abs(index - target_index)))
                break
            index += step
    if not candidates:
        raise GeometryError(f"牙位 {target_fdi} 两侧均没有被导板覆盖的邻牙")
    neighbour, _ = min(
        candidates,
        key=lambda item: (
            target.crown_point.distance_to(item[0].crown_point),
            item[1],
            item[0].fdi,
        ),
    )
    direction = np.asarray(
        (neighbour.crown_point - target.crown_point).as_tuple(),
        dtype=float,
    )
    return _unit(direction), (target_fdi, neighbour.fdi)


def _interpolate_missing_tooth(
    fdi: int,
    positions: dict[int, ToothPosition],
    fdi_order: tuple[int, ...],
    missing_teeth: tuple[int, ...],
) -> ToothPosition:
    """沿 FDI 牙弓顺序用两侧最近现存牙插值一个缺牙站位。"""

    if fdi not in missing_teeth:
        raise GeometryError(f"牙位 {fdi} 不存在，且没有被识别为缺牙")
    try:
        target_index = fdi_order.index(fdi)
    except ValueError as error:
        raise GeometryError(f"缺失牙位 {fdi} 不在 FDI 牙弓顺序中") from error
    left_index = next(
        (
            index
            for index in range(target_index - 1, -1, -1)
            if fdi_order[index] in positions
        ),
        None,
    )
    right_index = next(
        (
            index
            for index in range(target_index + 1, len(fdi_order))
            if fdi_order[index] in positions
        ),
        None,
    )
    if left_index is None or right_index is None:
        raise GeometryError(f"缺失牙位 {fdi} 的牙弓两侧没有可插值的现存牙")
    left = positions[fdi_order[left_index]]
    right = positions[fdi_order[right_index]]
    fraction = (target_index - left_index) / (right_index - left_index)

    def interpolate(first: Vec3, second: Vec3) -> Vec3:
        """按缺牙在两侧现存牙之间的序号比例插值向量。"""

        return first * (1.0 - fraction) + second * fraction

    guide_top = (
        None
        if left.guide_top is None or right.guide_top is None
        else interpolate(left.guide_top, right.guide_top)
    )
    return ToothPosition(
        fdi=fdi,
        crown_point=interpolate(left.crown_point, right.crown_point),
        guide_top=guide_top,
        arch_s_mm=(1.0 - fraction) * left.arch_s_mm + fraction * right.arch_s_mm,
        local_tangent=interpolate(left.local_tangent, right.local_tangent).normalized(),
        local_outward=interpolate(left.local_outward, right.local_outward).normalized(),
    )


def _oriented_across_tooth(
    points: np.ndarray,
    tooth_center: np.ndarray,
    outward: np.ndarray,
) -> np.ndarray:
    """将背牙外表面轨迹定向为牙弓内侧边缘到外侧边缘。"""

    first = float(np.dot(points[0] - tooth_center, outward))
    last = float(np.dot(points[-1] - tooth_center, outward))
    return points if first <= last else points[::-1].copy()


def _arch_outward_coordinate(
    point: np.ndarray,
    tooth_center: np.ndarray,
    outward: np.ndarray,
) -> float:
    """返回点相对牙位中心沿局部牙弓外向方向的有符号坐标。"""

    return float(np.dot(point - tooth_center, outward))


def _external_trajectory(
    mesh: trimesh.Trimesh,
    dentition: trimesh.Trimesh,
    origin: np.ndarray,
    plane_normal: np.ndarray,
    outward: np.ndarray,
    tooth_center: np.ndarray,
    *,
    require_both_sides: bool = True,
) -> np.ndarray:
    """切取目标牙位横断面并返回背牙外表面轨迹。

    外表面按局部法向是否稳定背离最近牙面判定，
    不再等同于牙弓颊侧方向；
    法向与背牙方向点积低于 0.80 的两端边缘厚度/过渡面不计入弧长。
    轨迹长度只由原始导板的完整背牙表面决定，不先扣除导孔、操作窗或
    观察窗净距。切口、局部补丁和厚度只用于随后验证优先位于 20% 和
    80% 弧长处的两个锚点及其回退比例，避免残余有效片段改变弧长参数。
    """

    section = mesh.section(plane_origin=origin, plane_normal=plane_normal)
    polylines = () if section is None else tuple(
        np.asarray(line, dtype=float) for line in section.discrete if len(line) >= 2
    )
    runs = []
    for points in polylines:
        _, distances, triangle_ids = trimesh.proximity.closest_point(mesh, points)
        normals = np.asarray(mesh.face_normals)[triangle_ids]
        away, _ = _directions_away_from_teeth(dentition, points)
        local_mask = (distances <= 0.01) & (
            np.sum(normals * away, axis=1) >= OUTER_WALL_ALIGNMENT_COSINE
        )
        visible = _visible_from_directions(mesh, points, local_mask, away)
        external_mask = _bridge_short_visibility_gaps(
            points,
            local_mask & visible,
        )
        for run in _split_valid_runs(points, external_mask):
            outward_coordinates = (run - tooth_center) @ outward
            if (
                _polyline_length(run) < 2.4
                or np.min(outward_coordinates) > -0.5
                or (require_both_sides and np.max(outward_coordinates) < 0.5)
            ):
                continue
            runs.append(_oriented_across_tooth(run, tooth_center, outward))
    if not runs:
        requirement = "跨越牙齿两侧的" if require_both_sides else "U 形内侧的"
        raise GeometryError(f"牙位切面没有{requirement}连续背牙外表面轨迹")
    if not require_both_sides:
        local_runs = tuple(
            run
            for run in runs
            if float(np.min(np.linalg.norm(run - tooth_center, axis=1))) <= 15.0
        )
        if not local_runs:
            raise GeometryError("牙位切面的 U 形内侧轨迹均距目标牙位超过 15 mm")
        return min(
            local_runs,
            key=lambda run: (
                float(np.min(np.linalg.norm(run - tooth_center, axis=1))),
                -_polyline_length(run),
            ),
        )
    return max(runs, key=_polyline_length)


def _common_positive_sleeve_axis(sleeves: SleeveGenerationResult) -> np.ndarray:
    """返回指向导板外侧的两导管符号对齐平均轴向。"""

    first = _unit(np.asarray(sleeves.sleeves[0].axis.as_tuple(), dtype=float))
    second = _unit(np.asarray(sleeves.sleeves[1].axis.as_tuple(), dtype=float))
    if float(np.dot(first, second)) < 0.0:
        second = -second
    common = _unit(first + second)
    guide_outside = _unit(
        np.asarray(sleeves.template_frame.normal.as_tuple(), dtype=float)
    )
    if float(np.dot(common, guide_outside)) < 0.0:
        common = -common
    if float(np.dot(common, guide_outside)) < 0.25:
        raise GeometryError("两导管公共轴无法可靠定向到导板外侧")
    return common


def _guide_midpoint(guide: GuideSleeve) -> np.ndarray:
    """返回导管轴向几何中点。"""

    center = np.asarray(guide.center.as_tuple(), dtype=float)
    axis = _unit(np.asarray(guide.axis.as_tuple(), dtype=float))
    axial_midpoint = 0.5 * (guide.axial_min_mm + guide.axial_max_mm)
    return center + axial_midpoint * axis


def _rotation_lateral_direction(
    sleeves: SleeveGenerationResult,
    positive_axis: np.ndarray,
) -> np.ndarray:
    """返回两导管连线在公共轴法平面内的单位投影。"""

    guide_line = _guide_midpoint(sleeves.sleeves[1]) - _guide_midpoint(
        sleeves.sleeves[0]
    )
    lateral = guide_line - float(np.dot(guide_line, positive_axis)) * positive_axis
    if float(np.linalg.norm(lateral)) <= 1e-6:
        raise GeometryError("两导管连线与公共轴近似平行，无法建立锚点旋转面")
    return _unit(lateral)


def _u_and_back_u_directions(
    positive_axis: np.ndarray,
    rotation_lateral: np.ndarray,
    arch_outward: np.ndarray,
    u_side_angle_degrees: float = DEFAULT_GUIDE_ANCHOR_U_SIDE_RAY_ANGLE_DEGREES,
    back_u_side_angle_degrees: float = (
        DEFAULT_GUIDE_ANCHOR_BACK_U_SIDE_RAY_ANGLE_DEGREES
    ),
) -> tuple[np.ndarray, np.ndarray]:
    """按牙位局部外向语义返回病例配置的 U 侧与背 U 侧射线。"""

    return (
        _side_rotation_direction(
            positive_axis,
            rotation_lateral,
            arch_outward,
            u_side_angle_degrees,
            u_side=True,
        ),
        _side_rotation_direction(
            positive_axis,
            rotation_lateral,
            arch_outward,
            back_u_side_angle_degrees,
            u_side=False,
        ),
    )


def _side_rotation_direction(
    positive_axis: np.ndarray,
    rotation_lateral: np.ndarray,
    arch_outward: np.ndarray,
    angle_degrees: float,
    *,
    u_side: bool,
) -> np.ndarray:
    """从正导管轴向 U 侧或背 U 侧旋转指定角度。"""

    if not 0.0 < angle_degrees <= 180.0:
        raise GeometryError("锚点旋转射线角度必须位于 (0°, 180°] 范围")
    positive_axis = _unit(positive_axis)
    rotation_lateral = _unit(rotation_lateral)
    arch_outward = _unit(arch_outward)
    alignment = float(np.dot(rotation_lateral, arch_outward))
    if abs(alignment) < 0.10:
        raise GeometryError("两导管连线在当前牙位无法可靠区分 U 侧和背 U 侧")
    back_u_lateral = rotation_lateral if alignment > 0.0 else -rotation_lateral
    side_lateral = -back_u_lateral if u_side else back_u_lateral
    angle = np.deg2rad(angle_degrees)
    return _unit(
        np.cos(angle) * positive_axis + np.sin(angle) * side_lateral
    )


def _ray_outer_exit_anchor(
    mesh: trimesh.Trimesh,
    origin: np.ndarray,
    direction: np.ndarray,
    allowed_face_mask: np.ndarray | None = None,
) -> ToothSectionSurfaceAnchor:
    """沿射线选择首个法向朝射线方向的局部导板外壁出口。"""

    direction = _unit(direction)
    locations, _, triangle_ids = mesh.ray.intersects_location(
        ray_origins=np.asarray([origin]),
        ray_directions=np.asarray([direction]),
        multiple_hits=True,
    )
    hits = sorted(
        (
            float(np.dot(location - origin, direction)),
            np.asarray(location, dtype=float),
            int(triangle_id),
        )
        for location, triangle_id in zip(locations, triangle_ids, strict=True)
        if float(np.dot(location - origin, direction)) > 1e-5
    )
    clusters: list[list[tuple[float, np.ndarray, int]]] = []
    for hit in hits:
        if (
            not clusters
            or hit[0] - clusters[-1][-1][0] > RAY_DUPLICATE_HIT_TOLERANCE_MM
        ):
            clusters.append([hit])
        else:
            clusters[-1].append(hit)
    for cluster in clusters:
        if allowed_face_mask is not None:
            allowed_hits = tuple(
                hit
                for hit in cluster
                if 0 <= hit[2] < len(allowed_face_mask)
                and bool(allowed_face_mask[hit[2]])
            )
            for _, point, face_index in allowed_hits:
                normal = _unit(np.asarray(mesh.face_normals[face_index], dtype=float))
                if float(np.dot(normal, direction)) >= RAY_EXIT_NORMAL_ALIGNMENT_MIN:
                    return ToothSectionSurfaceAnchor(
                        _vec3(point),
                        _vec3(normal),
                        face_index,
                    )
            continue
        point = np.mean(np.asarray([hit[1] for hit in cluster]), axis=0)
        closest, _, face_ids = trimesh.proximity.closest_point(mesh, [point])
        face_index = int(face_ids[0])
        normal = _unit(np.asarray(mesh.face_normals[face_index], dtype=float))
        if float(np.dot(normal, direction)) >= RAY_EXIT_NORMAL_ALIGNMENT_MIN:
            return ToothSectionSurfaceAnchor(
                _vec3(np.asarray(closest[0], dtype=float)),
                _vec3(normal),
                face_index,
            )
    raise GeometryError("旋转射线没有找到法向朝外的局部导板外壁出口")


def select_tooth_section_u_side_ray_anchors(
    case: CaseAnalysis,
    teeth: ToothIdentificationResult,
    stations: tuple[ToothAnchorStation, ...],
    angle_degrees: float,
) -> tuple[ToothSectionSingleRayAnchorSelection, ...]:
    """以牙合轴建立局部邻牙切面，为每个站位选择 U 侧射线外壁锚点。"""

    if not stations:
        raise GeometryError("U 侧旋转射线模式至少需要一个牙位站位")
    mesh = _load_welded(case.config.inputs.template)
    coordinate_system = teeth.mapping_report.get("coordinate_system")
    if not isinstance(coordinate_system, dict):
        raise GeometryError("牙位报告缺少 coordinate_system")
    positive_axis = _unit(np.asarray(coordinate_system.get("e_occ"), dtype=float))
    positions = {position.fdi: position for position in teeth.positions}
    geometries = tuple(
        _station_geometry(
            station,
            positions,
            teeth.fdi_order,
            teeth.missing_teeth,
        )
        for station in stations
    )
    results = []
    for station, geometry in zip(stations, geometries, strict=True):
        _, _, arch_outward, tooth_highest_point = geometry
        ray_origin = tooth_highest_point - ANCHOR_AXIS_DROP_MM * positive_axis
        station_angle_degrees = (
            angle_degrees
            if station.ray_angle_degrees is None
            else station.ray_angle_degrees
        )
        try:
            reference_tangent, reference_fdis = (
                _covered_neighbour_reference_tangent(
                    station,
                    positions,
                    teeth.fdi_order,
                    teeth.missing_teeth,
                )
            )
            plane_normal = _unit(
                reference_tangent
                - float(np.dot(reference_tangent, positive_axis)) * positive_axis
            )
            local_lateral = _unit(np.cross(plane_normal, positive_axis))
            direction = _side_rotation_direction(
                positive_axis,
                local_lateral,
                arch_outward,
                station_angle_degrees,
                u_side=True,
            )
            anchor = _ray_outer_exit_anchor(mesh, ray_origin, direction)
        except GeometryError as error:
            raise GeometryError(f"牙位站位 {station.fdis}：{error}") from error
        anchor_point = np.asarray(anchor.position.as_tuple(), dtype=float)
        outward_coordinate = _arch_outward_coordinate(
            anchor_point,
            tooth_highest_point,
            arch_outward,
        )
        results.append(
            ToothSectionSingleRayAnchorSelection(
                station.fdis,
                _vec3(ray_origin),
                _vec3(plane_normal),
                _vec3(arch_outward),
                (_vec3(ray_origin), _vec3(anchor_point)),
                anchor,
                station_angle_degrees,
                outward_coordinate,
                reference_fdis,
            )
        )
    return tuple(results)


def select_tooth_section_local_anchor_pairs(
    case: CaseAnalysis,
    teeth: ToothIdentificationResult,
    stations: tuple[ToothAnchorStation, ...],
    ray_angles_by_station: tuple[tuple[float, float], ...],
    station_meshes: tuple[trimesh.Trimesh, ...] | None = None,
) -> tuple[ToothSectionAnchorSelection, ...]:
    """以各牙位局部邻牙切面选择 U 侧和背 U 侧两个外壁锚点。"""

    if not stations or len(ray_angles_by_station) != len(stations):
        raise GeometryError("局部双射线站位与角度数量必须相同且非空")
    mesh = _load_welded(case.config.inputs.template)
    ray_meshes = (mesh,) * len(stations) if station_meshes is None else station_meshes
    if len(ray_meshes) != len(stations):
        raise GeometryError("局部双射线的求交网格数量必须与站位数量一致")
    coordinate_system = teeth.mapping_report.get("coordinate_system")
    if not isinstance(coordinate_system, dict):
        raise GeometryError("牙位报告缺少 coordinate_system")
    positive_axis = _unit(np.asarray(coordinate_system.get("e_occ"), dtype=float))
    positions = {position.fdi: position for position in teeth.positions}
    geometries = tuple(
        _station_geometry(
            station,
            positions,
            teeth.fdi_order,
            teeth.missing_teeth,
        )
        for station in stations
    )
    results = []
    for station, geometry, ray_angles, ray_mesh in zip(
        stations, geometries, ray_angles_by_station, ray_meshes, strict=True
    ):
        _, _, arch_outward, tooth_highest_point = geometry
        ray_origin = tooth_highest_point - ANCHOR_AXIS_DROP_MM * positive_axis
        try:
            reference_tangent, _ = _covered_neighbour_reference_tangent(
                station,
                positions,
                teeth.fdi_order,
                teeth.missing_teeth,
            )
            plane_normal = _unit(
                reference_tangent
                - float(np.dot(reference_tangent, positive_axis)) * positive_axis
            )
            local_lateral = _unit(np.cross(plane_normal, positive_axis))
            u_direction = _side_rotation_direction(
                positive_axis,
                local_lateral,
                arch_outward,
                ray_angles[0],
                u_side=True,
            )
            back_u_direction = _side_rotation_direction(
                positive_axis,
                local_lateral,
                arch_outward,
                ray_angles[1],
                u_side=False,
            )
            first = _ray_outer_exit_anchor(ray_mesh, ray_origin, u_direction)
            second = _ray_outer_exit_anchor(ray_mesh, ray_origin, back_u_direction)
        except GeometryError as error:
            raise GeometryError(f"牙位站位 {station.fdis}：{error}") from error
        support_trajectories = (
            (_vec3(ray_origin), first.position),
            (_vec3(ray_origin), second.position),
        )
        results.append(
            ToothSectionAnchorSelection(
                station.fdis,
                _vec3(ray_origin),
                _vec3(plane_normal),
                _vec3(arch_outward),
                (first.position, _vec3(ray_origin), second.position),
                support_trajectories,
                first,
                second,
                ray_angles,
            )
        )
    return tuple(results)


def select_local_independent_guide_anchors(
    case: CaseAnalysis,
    teeth: ToothIdentificationResult,
    anchors: tuple[GuideAnchorLocation, ...],
) -> tuple[IndependentGuideAnchorSelection, ...]:
    """按各锚点自己的牙位局部切面、侧别和角度选择导板外壁点。

    参数:
        case: 当前病例及原始导板网格。
        teeth: 已完成的牙位识别与导板映射结果。
        anchors: 按配置顺序排列的独立锚点参数。

    返回:
        与配置一一对应的独立射线锚点选择结果。
    """

    if not anchors:
        raise GeometryError("局部独立射线模式至少需要一个锚点")
    mesh = _load_welded(case.config.inputs.template)
    coordinate_system = teeth.mapping_report.get("coordinate_system")
    if not isinstance(coordinate_system, dict):
        raise GeometryError("牙位报告缺少 coordinate_system")
    positive_axis = _unit(np.asarray(coordinate_system.get("e_occ"), dtype=float))
    positions = {position.fdi: position for position in teeth.positions}
    results = []
    for configuration in anchors:
        station = configuration.tooth_station
        _, _, arch_outward, tooth_highest_point = _station_geometry(
            station,
            positions,
            teeth.fdi_order,
            teeth.missing_teeth,
        )
        ray_origin = tooth_highest_point - ANCHOR_AXIS_DROP_MM * positive_axis
        try:
            reference_tangent, _ = _covered_neighbour_reference_tangent(
                station,
                positions,
                teeth.fdi_order,
                teeth.missing_teeth,
            )
            plane_normal = _unit(
                reference_tangent
                - float(np.dot(reference_tangent, positive_axis)) * positive_axis
            )
            local_lateral = _unit(np.cross(plane_normal, positive_axis))
            direction = _side_rotation_direction(
                positive_axis,
                local_lateral,
                arch_outward,
                configuration.ray_angle_degrees,
                u_side=configuration.side is GuideAnchorSide.U_SIDE,
            )
            selected = _ray_outer_exit_anchor(mesh, ray_origin, direction)
        except GeometryError as error:
            raise GeometryError(
                f"独立锚点 {configuration.anchor_id} 牙位 {station.fdis}：{error}"
            ) from error
        anchor_point = np.asarray(selected.position.as_tuple(), dtype=float)
        results.append(
            IndependentGuideAnchorSelection(
                configuration,
                station.fdis,
                _vec3(ray_origin),
                _vec3(plane_normal),
                _vec3(arch_outward),
                (_vec3(ray_origin), selected.position),
                selected,
                _arch_outward_coordinate(
                    anchor_point,
                    tooth_highest_point,
                    arch_outward,
                ),
            )
        )
    return tuple(results)


def select_independent_guide_anchors(
    case: CaseAnalysis,
    sleeves: SleeveGenerationResult,
    teeth: ToothIdentificationResult,
    anchors: tuple[GuideAnchorLocation, ...],
) -> tuple[IndependentGuideAnchorSelection, ...]:
    """按公共导管旋转框架和逐锚点参数选择导板外壁点。

    参数:
        case: 当前病例及原始导板网格。
        sleeves: 已重建的同一种植位双导管。
        teeth: 已完成的牙位识别与导板映射结果。
        anchors: 按配置顺序排列的独立锚点参数。

    返回:
        与配置一一对应的独立射线锚点选择结果。
    """

    if not anchors:
        raise GeometryError("独立射线模式至少需要一个锚点")
    mesh = _load_welded(case.config.inputs.template)
    positive_axis = _common_positive_sleeve_axis(sleeves)
    lateral = _rotation_lateral_direction(sleeves, positive_axis)
    rotation_normal = _unit(np.cross(positive_axis, lateral))
    positions = {position.fdi: position for position in teeth.positions}
    results = []
    for configuration in anchors:
        station = configuration.tooth_station
        _, _, arch_outward, tooth_highest_point = _station_geometry(
            station,
            positions,
            teeth.fdi_order,
            teeth.missing_teeth,
        )
        ray_origin = tooth_highest_point - ANCHOR_AXIS_DROP_MM * positive_axis
        try:
            direction = _side_rotation_direction(
                positive_axis,
                lateral,
                arch_outward,
                configuration.ray_angle_degrees,
                u_side=configuration.side is GuideAnchorSide.U_SIDE,
            )
            selected = _ray_outer_exit_anchor(mesh, ray_origin, direction)
        except GeometryError as error:
            raise GeometryError(
                f"独立锚点 {configuration.anchor_id} 牙位 {station.fdis}：{error}"
            ) from error
        anchor_point = np.asarray(selected.position.as_tuple(), dtype=float)
        results.append(
            IndependentGuideAnchorSelection(
                configuration,
                station.fdis,
                _vec3(ray_origin),
                _vec3(rotation_normal),
                _vec3(arch_outward),
                (_vec3(ray_origin), selected.position),
                selected,
                _arch_outward_coordinate(
                    anchor_point,
                    tooth_highest_point,
                    arch_outward,
                ),
            )
        )
    return tuple(results)


def select_tooth_section_anchor_pairs(
    case: CaseAnalysis,
    sleeves: SleeveGenerationResult,
    cutouts: CutoutPlan,
    teeth: ToothIdentificationResult,
    stations: tuple[ToothAnchorStation, ...],
    connector_radius_mm: float,
    clearance_mm: float,
    ray_angles_by_station: tuple[tuple[float, float], ...] | None = None,
) -> tuple[ToothSectionAnchorSelection, ...]:
    """按牙位最高点下移原点的 U 侧/背 U 侧旋转射线选择外壁锚点。

    参数:
        case: 当前导板网格、病例配置和表面分析。
        sleeves: 两根标准生成导管及其公共轴向、连线方向。
        cutouts: 已规划的导孔、操作窗和观察窗；射线模式不做净距 QA。
        teeth: 已通过 QA 的牙位与导板映射。
        stations: 一个或多个单牙中心或双牙中点站位。
        connector_radius_mm: 连接梁半径；射线模式不做跨度 QA。
        clearance_mm: 锚点与所有切口的最低净距；射线模式不使用。

    返回:
        按配置站位顺序排列的两组旋转面、双向射线和双锚点。
    """

    if len(stations) not in {1, 2}:
        raise GeometryError("牙位截面射线选点要求一个或两个站位")
    del cutouts, connector_radius_mm, clearance_mm
    mesh = _load_welded(case.config.inputs.template)
    positive_axis = _common_positive_sleeve_axis(sleeves)
    lateral = _rotation_lateral_direction(sleeves, positive_axis)
    rotation_normal = _unit(np.cross(positive_axis, lateral))
    positions = {position.fdi: position for position in teeth.positions}
    geometries = tuple(
        _station_geometry(
            station,
            positions,
            teeth.fdi_order,
            teeth.missing_teeth,
        )
        for station in stations
    )
    if ray_angles_by_station is None:
        ray_angles_by_station = tuple(
            (
                case.config.guide_anchors.u_side_ray_angle_degrees,
                case.config.guide_anchors.back_u_side_ray_angle_degrees,
            )
            for _ in stations
        )
    if len(ray_angles_by_station) != len(stations):
        raise GeometryError("逐站位射线角度数量必须与牙位站位数量一致")
    results = []
    for station, geometry, ray_angles in zip(
        stations, geometries, ray_angles_by_station, strict=True
    ):
        _, _, arch_outward, tooth_highest_point = geometry
        ray_origin = tooth_highest_point - ANCHOR_AXIS_DROP_MM * positive_axis
        try:
            u_direction, back_u_direction = _u_and_back_u_directions(
                positive_axis,
                lateral,
                arch_outward,
                *ray_angles,
            )
            first = _ray_outer_exit_anchor(mesh, ray_origin, u_direction)
            second = _ray_outer_exit_anchor(mesh, ray_origin, back_u_direction)
        except GeometryError as error:
            raise GeometryError(f"牙位站位 {station.fdis}：{error}") from error
        first_point = np.asarray(first.position.as_tuple(), dtype=float)
        second_point = np.asarray(second.position.as_tuple(), dtype=float)
        support_trajectories = (
            (ray_origin, first_point),
            (ray_origin, second_point),
        )
        results.append(
            ToothSectionAnchorSelection(
                station.fdis,
                _vec3(ray_origin),
                _vec3(rotation_normal),
                _vec3(lateral),
                (_vec3(first_point), _vec3(ray_origin), _vec3(second_point)),
                tuple(
                    tuple(_vec3(point) for point in support)
                    for support in support_trajectories
                ),
                first,
                second,
                ray_angles,
            )
        )
    return tuple(results)


def select_tooth_section_anchor_candidates(
    case: CaseAnalysis,
    sleeves: SleeveGenerationResult,
    cutouts: CutoutPlan,
    teeth: ToothIdentificationResult,
    stations: tuple[ToothAnchorStation, ...],
    connector_radius_mm: float,
    clearance_mm: float,
) -> tuple[ToothSectionAnchorCandidateSet, ...]:
    """为每个牙位站位返回独立通过 QA 的单锚点候选。

    参数:
        case: 当前导板网格、病例配置和表面分析。
        sleeves: 两根标准生成导管的位姿结果。
        cutouts: 已规划的导孔、操作窗和观察窗。
        teeth: 已通过 QA 的牙位与导板映射。
        stations: 两个单牙中心或双牙中点站位。
        connector_radius_mm: 待连接按压梁的半径。
        clearance_mm: 候选中心到各切口的最低净距。

    返回:
        每个站位完整背牙外轨迹上按 20/80 及回退比例排序的可行单点。

    与双锚点接口不同，本接口不要求同一条轨迹的两个比例点同时通过；
    Y 型按压梁在每个牙位只消费一个导板锚点，并只接受局部外向坐标
    不大于 -0.50 mm 的 U 型牙弓凹侧候选，禁止背 U 的唇颊侧外表面。
    """

    if not stations:
        raise GeometryError("Y 型按压梁至少需要一个牙位站位")
    mesh = _load_welded(case.config.inputs.template)
    dentition = _load_welded(case.config.inputs.patient_dentition)
    profile_cutters = _profile_cutters(cutouts)
    positions = {position.fdi: position for position in teeth.positions}
    coordinate_system = teeth.mapping_report.get("coordinate_system")
    if not isinstance(coordinate_system, dict):
        raise GeometryError("牙位报告缺少 coordinate_system")
    occlusal = _unit(np.asarray(coordinate_system.get("e_occ"), dtype=float))
    geometries = tuple(
        _station_geometry(
            station,
            positions,
            teeth.fdi_order,
            teeth.missing_teeth,
        )
        for station in stations
    )
    fractions = (0.20, 0.80, 0.25, 0.75, 0.30, 0.70, 0.15, 0.85, 0.35, 0.65)
    results = []
    for station, geometry in zip(stations, geometries, strict=True):
        origin, plane_normal, outward, tooth_center = geometry
        try:
            trajectory = _external_trajectory(
                mesh,
                dentition,
                origin,
                plane_normal,
                outward,
                tooth_center,
                require_both_sides=False,
            )
        except GeometryError as error:
            raise GeometryError(f"牙位站位 {station.fdis}：{error}") from error
        candidates = []
        accepted_fractions = []
        accepted_outward_coordinates = []
        accepted_positions: set[tuple[float, float, float]] = set()
        for fraction in fractions:
            point, tangent = _point_at_fraction(trajectory, fraction)
            outward_coordinate = _arch_outward_coordinate(
                point,
                tooth_center,
                outward,
            )
            if outward_coordinate > -U_SIDE_MARGIN_MM:
                continue
            candidate = _anchor_candidate(
                mesh,
                dentition,
                point,
                tangent,
                occlusal,
                cutouts,
                profile_cutters,
                clearance_mm,
                case.config.geometry.fusion_voxel_size_mm,
                1.50,
            )
            if candidate is None:
                continue
            key = tuple(round(value, 4) for value in candidate.position.as_tuple())
            if key in accepted_positions:
                continue
            accepted_positions.add(key)
            candidates.append(candidate)
            accepted_fractions.append(fraction)
            accepted_outward_coordinates.append(outward_coordinate)
        if not candidates:
            raise GeometryError(
                f"牙位站位 {station.fdis} 的 U 侧外表面轨迹没有通过"
                "补丁、厚度和净距检查的单锚点"
            )
        results.append(
            ToothSectionAnchorCandidateSet(
                station.fdis,
                _vec3(origin),
                _vec3(plane_normal),
                _vec3(outward),
                tuple(_vec3(point) for point in trajectory),
                tuple(candidates),
                tuple(accepted_fractions),
                tuple(accepted_outward_coordinates),
            )
        )
    return tuple(results)

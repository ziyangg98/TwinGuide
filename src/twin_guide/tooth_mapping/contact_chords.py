"""内部算法说明。\n\nSplit a measured LR/AP crown projection with straight contact chords.

The current enhanced path refines coarse locations into interior topology
seeds, extracts concavities from the smoothed outer silhouette, and joins the
shortest valid buccal/lingual concavity pair.  Exact tooth centres are measured
only after the physical regions have been separated.  FDI identity is absent
from all geometric fitting and is assigned by the caller afterwards.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, replace

import numpy as np
from scipy.ndimage import (
    distance_transform_edt,
    gaussian_filter,
    gaussian_filter1d,
    map_coordinates,
    maximum_filter,
)
from skimage.measure import find_contours
from skimage.measure import label as connected_components
from skimage.morphology import closing, disk, remove_small_holes, remove_small_objects

EPS = 1e-9
MAX_ADJACENT_CORE_MERGE_MM = 5.75


@dataclass(frozen=True)
class ContactChord:
    """内部算法说明。"""

    pair_index: int
    first_instance_id: int
    second_instance_id: int
    kind: str
    line_point_lr_ap_mm: tuple[float, float]
    line_direction_lr_ap: tuple[float, float]
    first_endpoint_lr_ap_mm: tuple[float, float] | None
    second_endpoint_lr_ap_mm: tuple[float, float] | None
    chord_length_mm: float | None
    centre_offset_mm: float
    angle_offset_degrees: float
    mean_projection_score: float | None
    evidence_score: float | None = None
    confidence_margin: float | None = None
    height_valley_mm: float | None = None
    normal_jump: float | None = None
    endpoint_edge_support: float | None = None
    endpoint_concavity_support: float | None = None
    paired_concavity_facing_support: float | None = None
    paired_concavity_axial_alignment: float | None = None
    paired_concavity_crown_support: float | None = None
    paired_concavity_score: float | None = None
    paired_concavity_level: str | None = None
    selection_method: str | None = None


@dataclass(frozen=True)
class CrownSeed:
    """内部算法说明。\n\nInterior seed used for topology; it is not the final tooth centre."""

    instance_id: int
    center_lr_ap_mm: tuple[float, float]
    initial_center_lr_ap_mm: tuple[float, float]
    core_pixel_count: int
    refinement_distance_mm: float


@dataclass(frozen=True)
class CrownCoreCandidate:
    """内部算法说明。\n\nOne distance-transform crown core before FDI identity is assigned."""

    candidate_id: int
    center_lr_ap_mm: tuple[float, float]
    maximum_depth_mm: float
    directed_arch_position_mm: float
    crown_core_quality: float
    member_candidate_ids: tuple[int, ...]
    maximum_merge_step_mm: float
    merge_evidence_sufficient: bool


def select_crown_core_candidates(
    *,
    enhanced_maps: dict[str, np.ndarray | float],
    ordered_instances: list,
) -> tuple[list[CrownCoreCandidate], list[CrownCoreCandidate]]:
    """内部算法说明。\n\nDetect physical crown cores and select exactly one per present FDI."""

    lr_centres = np.asarray(enhanced_maps["lr_centres"], dtype=float)
    ap_centres = np.asarray(enhanced_maps["ap_centres"], dtype=float)
    resolution = float(enhanced_maps["resolution_mm"])
    mask = np.asarray(enhanced_maps["silhouette"], dtype=bool)
    depth = distance_transform_edt(mask) * resolution
    peak_window = max(5, round(2.4 / resolution))
    if peak_window % 2 == 0:
        peak_window += 1
    raw_peaks = mask & (depth >= 0.75) & (depth >= maximum_filter(depth, size=peak_window) - 1e-9)
    peak_labels = connected_components(raw_peaks, connectivity=2)
    points: list[np.ndarray] = []
    depths: list[float] = []
    for peak_label in range(1, int(np.max(peak_labels)) + 1):
        rows, columns = np.nonzero(peak_labels == peak_label)
        if not len(rows):
            continue
        weights = np.maximum(depth[rows, columns], 0.05) ** 2
        points.append(
            np.asarray(
                [
                    np.average(lr_centres[rows], weights=weights),
                    np.average(ap_centres[columns], weights=weights),
                ]
            )
        )
        depths.append(float(np.max(depth[rows, columns])))

    if not points:
        return [], []
    raw_points = np.asarray(points, dtype=float)
    raw_depths = np.asarray(depths, dtype=float)

    # Merge cuspal plateaus from one crown, but preserve neighbouring cores.
    retained: list[int] = []
    for candidate in np.argsort(raw_depths)[::-1]:
        if all(
            np.linalg.norm(raw_points[candidate] - raw_points[prior]) >= 3.0 for prior in retained
        ):
            retained.append(int(candidate))
    points_array = raw_points[retained]
    depths_array = raw_depths[retained]

    reference = np.asarray([item.center_lr_ap_mm for item in ordered_instances], dtype=float)
    vectors = np.empty((0, 2), dtype=float)
    lengths = np.empty(0, dtype=float)
    cumulative = np.asarray([0.0], dtype=float)
    if len(reference) <= 1:
        directed_s = points_array[:, 0].copy()
    else:
        vectors = np.diff(reference, axis=0)
        lengths = np.linalg.norm(vectors, axis=1)
        cumulative = np.concatenate([[0.0], np.cumsum(lengths)])
        directed_s = np.zeros(len(points_array), dtype=float)
        for point_index, point in enumerate(points_array):
            options: list[tuple[float, float]] = []
            for segment_index, (start, vector, length) in enumerate(
                zip(reference[:-1], vectors, lengths, strict=False)
            ):
                if length <= EPS:
                    continue
                fraction = float(np.clip((point - start) @ vector / length**2, 0.0, 1.0))
                projection = start + fraction * vector
                options.append(
                    (
                        float(np.linalg.norm(point - projection)),
                        float(cumulative[segment_index] + fraction * length),
                    )
                )

            # Extrapolate both terminal tangents.  This is essential when the
            # first historical spatial slot belongs to a missing tooth: the
            # first real crown lies outside the first present-slot prior.
            if lengths[0] > EPS:
                unit = vectors[0] / lengths[0]
                fraction_mm = float((point - reference[0]) @ unit)
                if fraction_mm < 0.0:
                    projection = reference[0] + fraction_mm * unit
                    options.append((float(np.linalg.norm(point - projection)), fraction_mm))
            if lengths[-1] > EPS:
                unit = vectors[-1] / lengths[-1]
                fraction_mm = float((point - reference[-1]) @ unit)
                if fraction_mm > 0.0:
                    projection = reference[-1] + fraction_mm * unit
                    options.append(
                        (
                            float(np.linalg.norm(point - projection)),
                            float(cumulative[-1] + fraction_mm),
                        )
                    )
            directed_s[point_index] = min(options, key=lambda item: item[0])[1]

    order = np.argsort(directed_s)
    candidates = [
        CrownCoreCandidate(
            candidate_id=int(original_index + 1),
            center_lr_ap_mm=(
                float(points_array[original_index, 0]),
                float(points_array[original_index, 1]),
            ),
            maximum_depth_mm=float(depths_array[original_index]),
            directed_arch_position_mm=float(directed_s[original_index]),
            crown_core_quality=float(depths_array[original_index]),
            member_candidate_ids=(int(original_index + 1),),
            maximum_merge_step_mm=0.0,
            merge_evidence_sufficient=True,
        )
        for original_index in order
    ]
    grouped = list(candidates)
    target_count = len(ordered_instances)
    while len(grouped) > target_count:
        adjacent_distances = [
            float(
                np.linalg.norm(
                    np.asarray(second.center_lr_ap_mm) - np.asarray(first.center_lr_ap_mm)
                )
            )
            for first, second in itertools.pairwise(grouped)
        ]
        if not adjacent_distances:
            break
        pair_index = int(np.argmin(adjacent_distances))
        merge_distance = adjacent_distances[pair_index]
        if merge_distance > MAX_ADJACENT_CORE_MERGE_MM:
            break
        first = grouped[pair_index]
        second = grouped[pair_index + 1]
        weights = np.asarray(
            [
                max(first.maximum_depth_mm, 0.1) ** 2,
                max(second.maximum_depth_mm, 0.1) ** 2,
            ]
        )
        centres = np.asarray([first.center_lr_ap_mm, second.center_lr_ap_mm], dtype=float)
        merged_center = np.average(centres, axis=0, weights=weights)
        merged = CrownCoreCandidate(
            candidate_id=min(first.member_candidate_ids + second.member_candidate_ids),
            center_lr_ap_mm=(float(merged_center[0]), float(merged_center[1])),
            maximum_depth_mm=float(max(first.maximum_depth_mm, second.maximum_depth_mm)),
            directed_arch_position_mm=float(
                np.average(
                    [first.directed_arch_position_mm, second.directed_arch_position_mm],
                    weights=weights,
                )
            ),
            crown_core_quality=float(max(first.crown_core_quality, second.crown_core_quality)),
            member_candidate_ids=tuple(
                sorted(first.member_candidate_ids + second.member_candidate_ids)
            ),
            maximum_merge_step_mm=float(
                max(
                    first.maximum_merge_step_mm,
                    second.maximum_merge_step_mm,
                    merge_distance,
                )
            ),
            merge_evidence_sufficient=bool(
                first.merge_evidence_sufficient
                and second.merge_evidence_sufficient
                and merge_distance <= MAX_ADJACENT_CORE_MERGE_MM
            ),
        )
        grouped[pair_index : pair_index + 2] = [merged]

    if len(grouped) <= target_count:
        return candidates, grouped

    # A distant surplus core must not be merged merely to satisfy the configured
    # count.  Choose the ordered subset that best agrees with the present-tooth
    # spatial priors and leave the rejected candidate explicit for diagnostics.
    group_points = np.asarray([group.center_lr_ap_mm for group in grouped], dtype=float)
    group_quality = np.asarray([group.crown_core_quality for group in grouped], dtype=float)
    assignment_cost = (
        np.linalg.norm(reference[:, None, :] - group_points[None, :, :], axis=2)
        - 0.08 * group_quality[None, :]
    )
    infinity = float("inf")
    cost = np.full((target_count + 1, len(grouped) + 1), infinity)
    previous = np.full((target_count + 1, len(grouped) + 1), -1, dtype=int)
    cost[0, :] = 0.0
    for tooth_index in range(1, target_count + 1):
        for group_count in range(1, len(grouped) + 1):
            skip = cost[tooth_index, group_count - 1]
            take = (
                cost[tooth_index - 1, group_count - 1]
                + assignment_cost[tooth_index - 1, group_count - 1]
            )
            if take <= skip:
                cost[tooth_index, group_count] = take
                previous[tooth_index, group_count] = 1
            else:
                cost[tooth_index, group_count] = skip
                previous[tooth_index, group_count] = 0
    selected_indices: list[int] = []
    tooth_index = target_count
    group_count = len(grouped)
    while tooth_index > 0 and group_count > 0:
        if previous[tooth_index, group_count] == 1:
            selected_indices.append(group_count - 1)
            tooth_index -= 1
        group_count -= 1
    if tooth_index:
        return candidates, grouped
    selected_indices.reverse()
    return candidates, [grouped[index] for index in selected_indices]


@dataclass(frozen=True)
class ChordContourInstance:
    """内部算法说明。"""

    instance_id: int
    source_instance_id: int
    area_mm2: float
    area_centroid_lr_ap_mm: tuple[float, float]
    interior_center_lr_ap_mm: tuple[float, float]
    maximum_interior_radius_mm: float
    pixel_count: int
    contour_lr_ap_mm: list[list[float]]


def refine_crown_core_seeds(
    *,
    enhanced_maps: dict[str, np.ndarray | float],
    ordered_instances: list,
) -> list[CrownSeed]:
    """内部算法说明。\n\nRefine coarse instance locations into stable interior crown seeds.

    The initial mixture centres only establish one basin per physical instance.
    Within those basins, the seed is recomputed from silhouette depth, crown
    height, occlusal normal and low-edge support.  These seeds are used only to
    validate which side of a contact separator belongs to which tooth.
    """

    lr_centres = np.asarray(enhanced_maps["lr_centres"], dtype=float)
    ap_centres = np.asarray(enhanced_maps["ap_centres"], dtype=float)
    resolution = float(enhanced_maps["resolution_mm"])
    mask = np.asarray(enhanced_maps["silhouette"], dtype=bool)
    height = np.asarray(enhanced_maps["top_height_mm"], dtype=float)
    normals = np.asarray(enhanced_maps["top_normal_lr_ap_occ"], dtype=float)
    fused_edge = np.asarray(enhanced_maps["fused_edge"], dtype=float)
    lr_grid, ap_grid = np.meshgrid(lr_centres, ap_centres, indexing="ij")

    initial = np.asarray([item.center_lr_ap_mm for item in ordered_instances], dtype=float)
    grid = np.column_stack([lr_grid.ravel(), ap_grid.ravel()])
    squared = np.sum((grid[:, None, :] - initial[None, :, :]) ** 2, axis=2)
    basin = np.argmin(squared, axis=1).reshape(mask.shape)

    depth = distance_transform_edt(mask) * resolution
    finite_height = height[np.isfinite(height) & mask]
    if len(finite_height):
        low, high = np.quantile(finite_height, [0.10, 0.90])
        height_score = np.clip((height - low) / max(high - low, EPS), 0.0, 1.0)
    else:
        height_score = np.zeros_like(depth)
    height_score = np.where(np.isfinite(height_score), height_score, 0.0)
    normal_score = np.clip(normals[..., 2], 0.0, 1.0)
    depth_score = np.clip(depth / 3.2, 0.0, 1.0)
    response = (
        0.44 * depth_score
        + 0.24 * height_score
        + 0.20 * normal_score
        + 0.12 * (1.0 - np.clip(fused_edge, 0.0, 1.0))
    )
    response = gaussian_filter(response, sigma=max(0.8, 0.22 / resolution))
    response[~mask] = 0.0

    global_anchors: list[np.ndarray | None] = [None] * len(initial)
    _, selected_candidates = select_crown_core_candidates(
        enhanced_maps=enhanced_maps,
        ordered_instances=ordered_instances,
    )
    if len(selected_candidates) >= len(initial):
        for slot_index, candidate in enumerate(selected_candidates[: len(initial)]):
            global_anchors[slot_index] = np.asarray(candidate.center_lr_ap_mm, dtype=float)

    seeds: list[CrownSeed] = []
    for index, item in enumerate(ordered_instances):
        initial_center = initial[index]
        neighbour_distances = np.linalg.norm(initial - initial_center, axis=1)
        neighbour_distances = neighbour_distances[neighbour_distances > EPS]
        nearest_neighbour = float(np.min(neighbour_distances)) if len(neighbour_distances) else 8.0
        maximum_refinement = min(5.5, max(2.5, 0.62 * nearest_neighbour))
        distance_from_prior = np.hypot(lr_grid - initial_center[0], ap_grid - initial_center[1])
        local = (
            mask & (basin == index) & (depth >= 0.35) & (distance_from_prior <= maximum_refinement)
        )
        if np.count_nonzero(local) < 8:
            local = mask & (basin == index) & (distance_from_prior <= maximum_refinement)
        # These are topology seeds, not final estimated tooth centres.  Select
        # the nearest distinct distance-transform crown-core peak inside the
        # configured slot basin.  Nearest-peak selection prevents a premolar
        # prior from being pulled into a deeper adjacent molar, while still
        # moving a prior that lies in the interproximal neck into crown interior.
        initial_row = int(np.argmin(np.abs(lr_centres - initial_center[0])))
        initial_column = int(np.argmin(np.abs(ap_centres - initial_center[1])))
        if global_anchors[index] is not None:
            anchor = np.asarray(global_anchors[index], dtype=float)
        elif local[initial_row, initial_column]:
            anchor = initial_center
        else:
            candidate_rows, candidate_columns = np.nonzero(local)
            if len(candidate_rows):
                candidate_points = np.column_stack(
                    [lr_centres[candidate_rows], ap_centres[candidate_columns]]
                )
                candidate_distances = np.linalg.norm(candidate_points - initial_center, axis=1)
                candidate_cost = (
                    candidate_distances - 0.20 * response[candidate_rows, candidate_columns]
                )
                anchor = candidate_points[int(np.argmin(candidate_cost))]
            else:
                anchor = initial_center
        core_radius = min(1.10, max(0.75, 0.12 * nearest_neighbour))
        distance_from_anchor = np.hypot(lr_grid - anchor[0], ap_grid - anchor[1])
        core = mask & (depth >= 0.35) & (distance_from_anchor <= core_radius)
        rows, columns = np.nonzero(core)
        if len(rows):
            weights = np.maximum(response[rows, columns], 0.05) ** 2
            centre = np.asarray(
                [
                    np.average(lr_centres[rows], weights=weights),
                    np.average(ap_centres[columns], weights=weights),
                ]
            )
        else:
            centre = initial_center.copy()
        seeds.append(
            CrownSeed(
                instance_id=int(item.instance_id),
                center_lr_ap_mm=(float(centre[0]), float(centre[1])),
                initial_center_lr_ap_mm=(float(initial_center[0]), float(initial_center[1])),
                core_pixel_count=len(rows),
                refinement_distance_mm=float(np.linalg.norm(centre - initial_center)),
            )
        )
    return seeds


def build_continuous_projection_mask(
    feature_maps: dict[str, object],
) -> np.ndarray:
    """内部算法说明。\n\nClose sub-pixel sampling holes without imposing inter-tooth borders."""

    occupied = np.asarray(feature_maps["occupied"], dtype=bool)
    resolution = float(feature_maps["resolution_mm"])
    support = gaussian_filter(occupied.astype(float), sigma=max(0.8, 0.20 / resolution))
    mask = support >= 0.16
    mask |= occupied
    mask = closing(mask, footprint=disk(1))
    minimum_object = max(8, round(0.40 / resolution**2))
    maximum_hole = max(12, round(0.65 / resolution**2))
    mask = remove_small_objects(mask, max_size=minimum_object - 1)
    mask = remove_small_holes(mask, max_size=maximum_hole - 1)
    return np.asarray(mask, dtype=bool)


def _sample_grid(
    values: np.ndarray,
    points: np.ndarray,
    lr_centres: np.ndarray,
    ap_centres: np.ndarray,
    resolution_mm: float,
    order: int,
) -> np.ndarray:
    """内部算法说明。"""
    indices = np.vstack(
        [
            (points[:, 0] - lr_centres[0]) / resolution_mm,
            (points[:, 1] - ap_centres[0]) / resolution_mm,
        ]
    )
    return map_coordinates(values, indices, order=order, mode="constant", cval=0.0)


def _inside_run_containing_origin(values: np.ndarray, origin_index: int) -> tuple[int, int] | None:
    """内部算法说明。"""
    if not values[origin_index]:
        candidates = np.flatnonzero(values)
        if len(candidates) == 0:
            return None
        nearest = int(candidates[np.argmin(np.abs(candidates - origin_index))])
        if abs(nearest - origin_index) > 3:
            return None
        origin_index = nearest
    low = origin_index
    high = origin_index
    while low > 0 and values[low - 1]:
        low -= 1
    while high + 1 < len(values) and values[high + 1]:
        high += 1
    return low, high


def find_contact_chords(
    *,
    feature_maps: dict[str, object],
    projection_mask: np.ndarray,
    ordered_instances: list,
) -> list[ContactChord]:
    """内部算法说明。\n\nFind one physical neck chord, or an empty-gap separator, per pair."""

    lr_centres = np.asarray(feature_maps["lr_centres"], dtype=float)
    ap_centres = np.asarray(feature_maps["ap_centres"], dtype=float)
    resolution = float(feature_maps["resolution_mm"])
    score = np.asarray(feature_maps["feature_score"], dtype=float)
    mask_float = np.asarray(projection_mask, dtype=float)
    sample_q = np.arange(-10.0, 10.0 + 0.5 * resolution, 0.5 * resolution)
    origin_index = int(np.argmin(np.abs(sample_q)))
    chords: list[ContactChord] = []

    for pair_index, (first, second) in enumerate(itertools.pairwise(ordered_instances)):
        first_center = np.asarray(first.center_lr_ap_mm, dtype=float)
        second_center = np.asarray(second.center_lr_ap_mm, dtype=float)
        delta = second_center - first_center
        centre_distance = float(np.linalg.norm(delta))
        axis = delta / max(centre_distance, EPS)
        perpendicular = np.asarray([-axis[1], axis[0]])
        midpoint = 0.5 * (first_center + second_center)
        search_offset = min(1.8, 0.20 * centre_distance)
        best = None

        for offset in np.linspace(-search_offset, search_offset, 25):
            line_point = midpoint + offset * axis
            for angle_degrees in np.linspace(-24.0, 24.0, 17):
                angle = np.radians(angle_degrees)
                direction = np.cos(angle) * perpendicular + np.sin(angle) * axis
                normal = np.asarray([-direction[1], direction[0]])
                signed_first = float((first_center - line_point) @ normal)
                signed_second = float((second_center - line_point) @ normal)
                if signed_first * signed_second >= 0.0:
                    continue
                points = line_point + sample_q[:, None] * direction
                sampled_mask = (
                    _sample_grid(mask_float, points, lr_centres, ap_centres, resolution, order=0)
                    >= 0.5
                )
                run = _inside_run_containing_origin(sampled_mask, origin_index)
                if run is None:
                    continue
                low, high = run
                chord_length = float(sample_q[high] - sample_q[low])
                if not 1.8 <= chord_length <= 12.0:
                    continue
                chord_points = points[low : high + 1]
                mean_score = float(
                    np.mean(
                        _sample_grid(
                            score, chord_points, lr_centres, ap_centres, resolution, order=1
                        )
                    )
                )
                objective = (
                    chord_length
                    + 0.42 * abs(offset)
                    + 0.012 * abs(angle_degrees)
                    + 0.45 * mean_score
                )
                if best is None or objective < best[0]:
                    best = (
                        objective,
                        line_point,
                        direction,
                        points[low],
                        points[high],
                        chord_length,
                        float(offset),
                        float(angle_degrees),
                        mean_score,
                    )

        if best is None:
            # A true edentulous/inter-tooth gap has no physical contact
            # endpoints.  The infinite mid-gap line is retained only as a
            # topological separator and is never reported as a tooth contour.
            chords.append(
                ContactChord(
                    pair_index=pair_index,
                    first_instance_id=int(first.instance_id),
                    second_instance_id=int(second.instance_id),
                    kind="gap",
                    line_point_lr_ap_mm=(float(midpoint[0]), float(midpoint[1])),
                    line_direction_lr_ap=(float(perpendicular[0]), float(perpendicular[1])),
                    first_endpoint_lr_ap_mm=None,
                    second_endpoint_lr_ap_mm=None,
                    chord_length_mm=None,
                    centre_offset_mm=0.0,
                    angle_offset_degrees=0.0,
                    mean_projection_score=None,
                )
            )
        else:
            _, line_point, direction, endpoint_1, endpoint_2, length, offset, angle, mean_score = (
                best
            )
            chords.append(
                ContactChord(
                    pair_index=pair_index,
                    first_instance_id=int(first.instance_id),
                    second_instance_id=int(second.instance_id),
                    kind="contact",
                    line_point_lr_ap_mm=(float(line_point[0]), float(line_point[1])),
                    line_direction_lr_ap=(float(direction[0]), float(direction[1])),
                    first_endpoint_lr_ap_mm=(float(endpoint_1[0]), float(endpoint_1[1])),
                    second_endpoint_lr_ap_mm=(float(endpoint_2[0]), float(endpoint_2[1])),
                    chord_length_mm=float(length),
                    centre_offset_mm=float(offset),
                    angle_offset_degrees=float(angle),
                    mean_projection_score=float(mean_score),
                )
            )
    return chords


def find_multichannel_contact_chords(
    *,
    enhanced_maps: dict[str, np.ndarray | float],
    ordered_instances: list,
) -> list[ContactChord]:
    """内部算法说明。\n\nSearch contact chords using silhouette, height, normal and edge evidence."""

    lr_centres = np.asarray(enhanced_maps["lr_centres"], dtype=float)
    ap_centres = np.asarray(enhanced_maps["ap_centres"], dtype=float)
    resolution = float(enhanced_maps["resolution_mm"])
    mask = np.asarray(enhanced_maps["silhouette"], dtype=bool)
    mask_float = mask.astype(float)
    fused_edge = np.asarray(enhanced_maps["fused_edge"], dtype=float)
    height = np.asarray(enhanced_maps["top_height_mm"], dtype=float)
    height_filled = np.where(np.isfinite(height), height, 0.0)
    normals = np.asarray(enhanced_maps["top_normal_lr_ap_occ"], dtype=float)
    lr_grid, ap_grid = np.meshgrid(lr_centres, ap_centres, indexing="ij")
    grid_points = np.column_stack([lr_grid.ravel(), ap_grid.ravel()])
    sample_q = np.arange(-10.0, 10.0 + 0.5 * resolution, 0.5 * resolution)
    origin_index = int(np.argmin(np.abs(sample_q)))
    chords: list[ContactChord] = []

    def sample(values: np.ndarray, points: np.ndarray, order: int = 1) -> np.ndarray:
        """内部算法说明。"""
        return _sample_grid(values, points, lr_centres, ap_centres, resolution, order)

    for pair_index, (first, second) in enumerate(itertools.pairwise(ordered_instances)):
        first_center = np.asarray(first.center_lr_ap_mm, dtype=float)
        second_center = np.asarray(second.center_lr_ap_mm, dtype=float)
        delta = second_center - first_center
        centre_distance = float(np.linalg.norm(delta))
        axis = delta / max(centre_distance, EPS)
        perpendicular = np.asarray([-axis[1], axis[0]])
        midpoint = 0.5 * (first_center + second_center)
        search_offset = min(1.8, 0.20 * centre_distance)
        candidates = []

        local_delta = grid_points - midpoint
        local_roi = (
            (np.abs(local_delta @ axis) <= 0.70 * centre_distance + 1.5)
            & (np.abs(local_delta @ perpendicular) <= 7.0)
            & mask.ravel()
        )
        for offset in np.linspace(-search_offset, search_offset, 25):
            line_point = midpoint + offset * axis
            for angle_degrees in np.linspace(-24.0, 24.0, 17):
                angle = np.radians(angle_degrees)
                direction = np.cos(angle) * perpendicular + np.sin(angle) * axis
                line_normal = np.asarray([-direction[1], direction[0]])
                signed_first = float((first_center - line_point) @ line_normal)
                signed_second = float((second_center - line_point) @ line_normal)
                if signed_first * signed_second >= 0.0:
                    continue
                points = line_point + sample_q[:, None] * direction
                sampled_mask = sample(mask_float, points, order=0) >= 0.5
                run = _inside_run_containing_origin(sampled_mask, origin_index)
                if run is None:
                    continue
                low, high = run
                chord_length = float(sample_q[high] - sample_q[low])
                if not 1.8 <= chord_length <= 12.0:
                    continue

                signed_grid = (grid_points - line_point) @ line_normal
                first_area = int(np.count_nonzero(local_roi & (signed_grid * signed_first >= 0.0)))
                second_area = int(
                    np.count_nonzero(local_roi & (signed_grid * signed_second >= 0.0))
                )
                if min(first_area, second_area) < max(60, round(4.0 / resolution**2)):
                    continue

                chord_points = points[low : high + 1]
                endpoint_points = np.vstack([points[low], points[high]])
                endpoint_edge = float(np.mean(sample(fused_edge, endpoint_points)))
                line_edge = float(np.mean(sample(fused_edge, chord_points)))

                shift = 0.65 * line_normal
                minus = chord_points - shift
                plus = chord_points + shift
                valid_shift = (sample(mask_float, minus, order=0) >= 0.5) & (
                    sample(mask_float, plus, order=0) >= 0.5
                )
                if np.count_nonzero(valid_shift) >= 4:
                    centre_height = sample(height_filled, chord_points)[valid_shift]
                    side_height = 0.5 * (
                        sample(height_filled, minus)[valid_shift]
                        + sample(height_filled, plus)[valid_shift]
                    )
                    height_valley = float(np.mean(side_height - centre_height))
                    minus_normal = np.column_stack(
                        [sample(normals[..., channel], minus)[valid_shift] for channel in range(3)]
                    )
                    plus_normal = np.column_stack(
                        [sample(normals[..., channel], plus)[valid_shift] for channel in range(3)]
                    )
                    dot = np.sum(minus_normal * plus_normal, axis=1)
                    normal_jump = float(np.mean(np.clip(1.0 - dot, 0.0, 2.0)) / 2.0)
                else:
                    height_valley = 0.0
                    normal_jump = 0.0

                score = (
                    0.28 * endpoint_edge
                    + 0.24 * line_edge
                    + 0.15 * np.clip(abs(height_valley) / 0.80, 0.0, 1.0)
                    + 0.13 * np.clip(normal_jump, 0.0, 1.0)
                    + 0.20 * (1.0 - chord_length / 12.0)
                    - 0.08 * abs(offset) / max(search_offset, EPS)
                    - 0.05 * abs(angle_degrees) / 24.0
                )
                candidates.append(
                    {
                        "score": float(score),
                        "line_point": line_point,
                        "direction": direction,
                        "endpoint_1": points[low],
                        "endpoint_2": points[high],
                        "length": chord_length,
                        "offset": float(offset),
                        "angle": float(angle_degrees),
                        "line_edge": line_edge,
                        "endpoint_edge": endpoint_edge,
                        "height_valley": height_valley,
                        "normal_jump": normal_jump,
                    }
                )

        if not candidates:
            chords.append(
                ContactChord(
                    pair_index=pair_index,
                    first_instance_id=int(first.instance_id),
                    second_instance_id=int(second.instance_id),
                    kind="gap",
                    line_point_lr_ap_mm=(float(midpoint[0]), float(midpoint[1])),
                    line_direction_lr_ap=(float(perpendicular[0]), float(perpendicular[1])),
                    first_endpoint_lr_ap_mm=None,
                    second_endpoint_lr_ap_mm=None,
                    chord_length_mm=None,
                    centre_offset_mm=0.0,
                    angle_offset_degrees=0.0,
                    mean_projection_score=None,
                )
            )
            continue

        candidates.sort(key=lambda item: item["score"], reverse=True)
        best = candidates[0]
        distinct = [
            item
            for item in candidates[1:]
            if abs(item["offset"] - best["offset"]) >= 0.30
            or abs(item["angle"] - best["angle"]) >= 6.0
        ]
        runner_up = distinct[0] if distinct else candidates[min(1, len(candidates) - 1)]
        margin = float(best["score"] - runner_up["score"])
        kind = "contact" if best["score"] >= 0.22 else "uncertain"
        chords.append(
            ContactChord(
                pair_index=pair_index,
                first_instance_id=int(first.instance_id),
                second_instance_id=int(second.instance_id),
                kind=kind,
                line_point_lr_ap_mm=tuple(float(value) for value in best["line_point"]),
                line_direction_lr_ap=tuple(float(value) for value in best["direction"]),
                first_endpoint_lr_ap_mm=tuple(float(value) for value in best["endpoint_1"]),
                second_endpoint_lr_ap_mm=tuple(float(value) for value in best["endpoint_2"]),
                chord_length_mm=float(best["length"]),
                centre_offset_mm=float(best["offset"]),
                angle_offset_degrees=float(best["angle"]),
                mean_projection_score=float(best["line_edge"]),
                evidence_score=float(best["score"]),
                confidence_margin=margin,
                height_valley_mm=float(best["height_valley"]),
                normal_jump=float(best["normal_jump"]),
                endpoint_edge_support=float(best["endpoint_edge"]),
            )
        )
    return chords


def _component_at_point(
    components: np.ndarray,
    point: np.ndarray,
    lr_centres: np.ndarray,
    ap_centres: np.ndarray,
) -> int:
    """内部算法说明。"""
    row = int(np.argmin(np.abs(lr_centres - point[0])))
    column = int(np.argmin(np.abs(ap_centres - point[1])))
    component = int(components[row, column])
    if component:
        return component
    candidates = np.argwhere(components > 0)
    if not len(candidates):
        return 0
    physical = np.column_stack([lr_centres[candidates[:, 0]], ap_centres[candidates[:, 1]]])
    nearest = int(np.argmin(np.linalg.norm(physical - point, axis=1)))
    return int(components[tuple(candidates[nearest])])


def _outer_concavity_candidates(
    component_mask: np.ndarray,
    lr_centres: np.ndarray,
    ap_centres: np.ndarray,
    resolution: float,
    span_mm_values: tuple[float, ...] = (0.55,),
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """返回轮廓点、凹点分数和指向凹口内部的方向。"""

    contours = find_contours(component_mask.astype(float), 0.5)
    if not contours:
        return np.empty((0, 2)), np.empty(0), np.empty((0, 2))

    def physical(raw: np.ndarray) -> np.ndarray:
        """内部算法说明。"""
        return np.column_stack(
            [
                np.interp(raw[:, 0], np.arange(len(lr_centres)), lr_centres),
                np.interp(raw[:, 1], np.arange(len(ap_centres)), ap_centres),
            ]
        )

    physical_contours = [physical(raw) for raw in contours]
    areas = [
        0.5
        * abs(
            np.sum(
                contour[:, 0] * np.roll(contour[:, 1], -1)
                - np.roll(contour[:, 0], -1) * contour[:, 1]
            )
        )
        for contour in physical_contours
    ]
    points = physical_contours[int(np.argmax(areas))]
    if len(points) < 12:
        return points, np.zeros(len(points)), np.zeros((len(points), 2))
    sigma = max(1.0, 0.20 / resolution)
    smooth = np.column_stack(
        [
            gaussian_filter1d(points[:, 0], sigma=sigma, mode="wrap"),
            gaussian_filter1d(points[:, 1], sigma=sigma, mode="wrap"),
        ]
    )
    signed_area = 0.5 * np.sum(
        smooth[:, 0] * np.roll(smooth[:, 1], -1) - np.roll(smooth[:, 0], -1) * smooth[:, 1]
    )
    orientation = 1.0 if signed_area >= 0.0 else -1.0
    # Contact necks occur at different physical scales.  A single 0.55-mm
    # turning window detects sharp embrasures but misses the broad, shallow
    # concavities of molars.  Measure the same exterior-notch condition at
    # several millimetre scales and retain the strongest response per point.
    # The test that the neighbouring-boundary chord traverses the exterior is
    # kept at every scale, so convex cusps and pixel staircases remain rejected.
    score = np.zeros(len(smooth), dtype=float)
    notch_direction = np.zeros((len(smooth), 2), dtype=float)
    fractions = np.linspace(0.12, 0.88, 9)
    for span_mm in span_mm_values:
        span = max(3, round(span_mm / resolution))
        previous = np.roll(smooth, span, axis=0)
        following = np.roll(smooth, -span, axis=0)
        incoming = smooth - previous
        outgoing = following - smooth
        incoming /= np.maximum(np.linalg.norm(incoming, axis=1, keepdims=True), EPS)
        outgoing /= np.maximum(np.linalg.norm(outgoing, axis=1, keepdims=True), EPS)
        cross = incoming[:, 0] * outgoing[:, 1] - incoming[:, 1] * outgoing[:, 0]
        dot = np.sum(incoming * outgoing, axis=1)
        turning = np.arctan2(cross, np.clip(dot, -1.0, 1.0))
        concavity = np.maximum(0.0, -orientation * turning)
        notch_fraction = np.zeros(len(smooth), dtype=float)
        for index in np.flatnonzero(concavity > 0.006):
            samples = previous[index] + fractions[:, None] * (following[index] - previous[index])
            inside = (
                _sample_grid(
                    component_mask.astype(float),
                    samples,
                    lr_centres,
                    ap_centres,
                    resolution,
                    order=0,
                )
                >= 0.5
            )
            notch_fraction[index] = 1.0 - float(np.mean(inside))
        scale_score = concavity * np.clip(notch_fraction / 0.30, 0.0, 1.0)
        # The vector from the local notch mouth towards the indentation point
        # points into the crown union.  Opposing buccal/palatal contact notches
        # should therefore point towards one another along their joining chord.
        direction = smooth - 0.5 * (previous + following)
        direction /= np.maximum(np.linalg.norm(direction, axis=1, keepdims=True), EPS)
        stronger = scale_score > score
        score[stronger] = scale_score[stronger]
        notch_direction[stronger] = direction[stronger]

    # Keep local peaks only; closely spaced staircase points represent one
    # anatomical notch and must not produce artificial ultra-short chords.
    peak_window = max(2, round(0.45 / resolution))
    peaks = np.zeros(len(score), dtype=bool)
    for shift in range(-peak_window, peak_window + 1):
        peaks |= score < np.roll(score, shift)
    score[peaks] = 0.0
    notch_direction[peaks] = 0.0
    return smooth, score, notch_direction


def _paired_concavity_metrics(
    *,
    endpoint_1: np.ndarray,
    endpoint_2: np.ndarray,
    notch_direction_1: np.ndarray,
    notch_direction_2: np.ndarray,
    concavity_1: float,
    concavity_2: float,
    inter_seed_axis: np.ndarray,
    centre_distance: float,
    endpoint_crown_support: float,
) -> dict[str, float | str]:
    """评价两个轮廓凹点是否构成相向的邻牙接触对。"""

    segment = np.asarray(endpoint_2, dtype=float) - np.asarray(endpoint_1, dtype=float)
    length = float(np.linalg.norm(segment))
    chord_direction = segment / max(length, EPS)
    first_faces_second = max(0.0, float(np.asarray(notch_direction_1) @ chord_direction))
    second_faces_first = max(0.0, float(np.asarray(notch_direction_2) @ -chord_direction))
    facing = float(np.sqrt(first_faces_second * second_faces_first))
    axial_mismatch = abs(float(segment @ np.asarray(inter_seed_axis, dtype=float)))
    axial_alignment = float(np.exp(-((axial_mismatch / max(0.22 * centre_distance, 0.60)) ** 2)))
    paired_concavity = float(np.sqrt(max(float(concavity_1), 0.0) * max(float(concavity_2), 0.0)))
    concavity_score = float(np.clip(paired_concavity / 0.35, 0.0, 1.0))
    crown_support = float(np.clip(endpoint_crown_support, 0.0, 1.0))
    score = float(
        0.30 * concavity_score + 0.30 * facing + 0.18 * axial_alignment + 0.22 * crown_support
    )
    strong = bool(
        score >= 0.68 and facing >= 0.55 and axial_alignment >= 0.55 and crown_support >= 0.18
    )
    level = "strong" if strong else "moderate" if score >= 0.38 else "weak"
    return {
        "paired_concavity": paired_concavity,
        "facing": facing,
        "axial_alignment": axial_alignment,
        "crown_support": crown_support,
        "score": score,
        "level": level,
    }


def _segments_cross(first: np.ndarray, second: np.ndarray) -> bool:
    """内部算法说明。\n\nReturn whether two closed 2-D segments properly intersect."""

    a, b = first
    c, d = second

    def side(p: np.ndarray, q: np.ndarray, r: np.ndarray) -> float:
        """内部算法说明。"""
        first = q - p
        second = r - p
        return float(first[0] * second[1] - first[1] * second[0])

    return side(a, b, c) * side(a, b, d) < 0.0 and side(c, d, a) * side(c, d, b) < 0.0


def contact_chords_are_non_crossing(chords: list[ContactChord]) -> bool:
    """内部算法说明。\n\nValidate that finite contact separators do not properly intersect."""

    segments = [
        np.asarray(
            [
                chord.first_endpoint_lr_ap_mm,
                chord.second_endpoint_lr_ap_mm,
            ],
            dtype=float,
        )
        for chord in chords
        if chord.first_endpoint_lr_ap_mm is not None and chord.second_endpoint_lr_ap_mm is not None
    ]
    return not any(
        _segments_cross(first, second)
        for index, first in enumerate(segments)
        for second in segments[index + 1 :]
    )


def find_shortest_concavity_chords(
    *,
    enhanced_maps: dict[str, np.ndarray | float],
    ordered_seeds: list,
    forced_gap_pair_indices: set[int] | None = None,
) -> list[ContactChord]:
    """内部算法说明。\n\nJoin paired outer-contour concavities with the shortest valid chord.

    Candidate endpoints are free contour points rather than intersections from
    a centre-midpoint line family.  Centres are used only as interior topology
    seeds: a valid separator must leave adjacent seeds on opposite sides.
    """

    lr_centres = np.asarray(enhanced_maps["lr_centres"], dtype=float)
    ap_centres = np.asarray(enhanced_maps["ap_centres"], dtype=float)
    resolution = float(enhanced_maps["resolution_mm"])
    mask = np.asarray(enhanced_maps["silhouette"], dtype=bool)
    mask_float = mask.astype(float)
    fused_edge = np.asarray(enhanced_maps["fused_edge"], dtype=float)
    height = np.asarray(enhanced_maps["top_height_mm"], dtype=float)
    height_filled = np.where(np.isfinite(height), height, 0.0)
    normals = np.asarray(enhanced_maps["top_normal_lr_ap_occ"], dtype=float)
    relief_score = np.asarray(
        enhanced_maps.get(
            "relative_crown_relief_score",
            np.ones(mask.shape, dtype=float),
        ),
        dtype=float,
    )
    components = connected_components(mask, connectivity=2)
    lr_grid, ap_grid = np.meshgrid(lr_centres, ap_centres, indexing="ij")
    grid_points = np.column_stack([lr_grid.ravel(), ap_grid.ravel()])
    contour_cache: dict[int, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    accepted_segments: list[np.ndarray] = []
    chords: list[ContactChord] = []
    forced_gaps = set() if forced_gap_pair_indices is None else set(forced_gap_pair_indices)

    def sample(values: np.ndarray, points: np.ndarray, order: int = 1) -> np.ndarray:
        """内部算法说明。"""
        return _sample_grid(values, points, lr_centres, ap_centres, resolution, order)

    def gap(
        pair_index: int, first, second, midpoint: np.ndarray, perpendicular: np.ndarray
    ) -> ContactChord:
        """内部算法说明。"""
        return ContactChord(
            pair_index=pair_index,
            first_instance_id=int(first.instance_id),
            second_instance_id=int(second.instance_id),
            kind="gap",
            line_point_lr_ap_mm=tuple(float(value) for value in midpoint),
            line_direction_lr_ap=tuple(float(value) for value in perpendicular),
            first_endpoint_lr_ap_mm=None,
            second_endpoint_lr_ap_mm=None,
            chord_length_mm=None,
            centre_offset_mm=0.0,
            angle_offset_degrees=0.0,
            mean_projection_score=None,
            selection_method="disconnected_component_gap",
        )

    for pair_index, (first, second) in enumerate(itertools.pairwise(ordered_seeds)):
        first_center = np.asarray(first.center_lr_ap_mm, dtype=float)
        second_center = np.asarray(second.center_lr_ap_mm, dtype=float)
        delta = second_center - first_center
        centre_distance = float(np.linalg.norm(delta))
        axis = delta / max(centre_distance, EPS)
        perpendicular = np.asarray([-axis[1], axis[0]])
        midpoint = 0.5 * (first_center + second_center)
        first_component = _component_at_point(components, first_center, lr_centres, ap_centres)
        second_component = _component_at_point(components, second_center, lr_centres, ap_centres)
        if pair_index in forced_gaps:
            configured_gap = gap(pair_index, first, second, midpoint, perpendicular)
            chords.append(
                replace(
                    configured_gap,
                    selection_method="configured_missing_slot_gap",
                )
            )
            continue
        if first_component == 0 or first_component != second_component:
            chords.append(gap(pair_index, first, second, midpoint, perpendicular))
            continue

        if first_component not in contour_cache:
            contour_cache[first_component] = _outer_concavity_candidates(
                components == first_component, lr_centres, ap_centres, resolution
            )
        contour, concavity, notch_direction = contour_cache[first_component]

        def build_candidates(
            contour_points: np.ndarray,
            concavity_scores: np.ndarray,
            notch_directions: np.ndarray,
            *,
            local_neck: bool = False,
        ) -> list[dict]:
            """内部算法说明。"""
            if not len(contour_points):
                return []
            relative_first = contour_points - first_center
            along = relative_first @ axis
            transverse = (contour_points - midpoint) @ perpendicular
            if local_neck:
                eligible = (along >= 0.18 * centre_distance) & (along <= 0.82 * centre_distance)
            else:
                eligible = (
                    (concavity_scores >= 0.010)
                    & (along >= 0.03 * centre_distance)
                    & (along <= 0.97 * centre_distance)
                )
            positive = np.flatnonzero(eligible & (transverse >= 0.30))
            negative = np.flatnonzero(eligible & (transverse <= -0.30))
            if local_neck:
                axial_offset = np.abs(along - 0.5 * centre_distance)
                positive = positive[np.argsort(axial_offset[positive])[:80]]
                negative = negative[np.argsort(axial_offset[negative])[:80]]
            else:
                positive = positive[np.argsort(concavity_scores[positive])[-18:]]
                negative = negative[np.argsort(concavity_scores[negative])[-18:]]
            result = []

            local_delta = grid_points - midpoint
            local_roi = (
                (np.abs(local_delta @ axis) <= 0.68 * centre_distance + 1.0)
                & (np.abs(local_delta @ perpendicular) <= 7.5)
                & (components.ravel() == first_component)
            )
            for first_index in positive:
                for second_index in negative:
                    endpoint_1 = contour_points[first_index]
                    endpoint_2 = contour_points[second_index]
                    segment = endpoint_2 - endpoint_1
                    length = float(np.linalg.norm(segment))
                    if not 1.8 <= length <= 12.0:
                        continue
                    direction = segment / max(length, EPS)
                    if abs(float(direction @ axis)) > 0.82:
                        continue
                    line_point = 0.5 * (endpoint_1 + endpoint_2)
                    if local_neck and abs(float((line_point - midpoint) @ axis)) > max(
                        1.20, 0.28 * centre_distance
                    ):
                        continue
                    line_normal = np.asarray([-direction[1], direction[0]])
                    signed_first = float((first_center - line_point) @ line_normal)
                    signed_second = float((second_center - line_point) @ line_normal)
                    if (
                        signed_first * signed_second >= 0.0
                        or min(abs(signed_first), abs(signed_second)) < 0.40
                    ):
                        continue
                    samples = (
                        endpoint_1
                        + np.linspace(0.03, 0.97, max(9, int(length / (0.5 * resolution))))[:, None]
                        * segment
                    )
                    inside_fraction = float(np.mean(sample(mask_float, samples, order=0) >= 0.5))
                    if inside_fraction < 0.86:
                        continue
                    signed_grid = (grid_points - line_point) @ line_normal
                    first_area = int(
                        np.count_nonzero(local_roi & (signed_grid * signed_first >= 0.0))
                    )
                    second_area = int(
                        np.count_nonzero(local_roi & (signed_grid * signed_second >= 0.0))
                    )
                    if min(first_area, second_area) < max(60, round(4.0 / resolution**2)):
                        continue
                    candidate_segment = np.vstack([endpoint_1, endpoint_2])
                    if any(
                        _segments_cross(candidate_segment, prior) for prior in accepted_segments
                    ):
                        continue

                    endpoint_edge = float(np.mean(sample(fused_edge, candidate_segment, order=1)))

                    def crown_support(endpoint: np.ndarray) -> float:
                        """评价凹点端点是否同时得到两侧牙冠内部支撑。"""

                        side_support = []
                        for center in (first_center, second_center):
                            inward = center - endpoint
                            inward /= max(float(np.linalg.norm(inward)), EPS)
                            support_points = (
                                endpoint
                                + np.asarray(
                                    [
                                        0.45,
                                        0.90,
                                        1.35,
                                    ]
                                )[:, None]
                                * inward
                            )
                            values = sample(relief_score, support_points, order=1)
                            values = values[np.isfinite(values)]
                            side_support.append(float(np.max(values)) if len(values) else 0.0)
                        # Both neighbouring crown interiors must support the
                        # same contour endpoint.  A gingival notch close to
                        # only one crown therefore remains weak evidence.
                        return min(side_support)

                    endpoint_crown_support = min(
                        crown_support(endpoint_1),
                        crown_support(endpoint_2),
                    )
                    paired = _paired_concavity_metrics(
                        endpoint_1=endpoint_1,
                        endpoint_2=endpoint_2,
                        notch_direction_1=notch_directions[first_index],
                        notch_direction_2=notch_directions[second_index],
                        concavity_1=float(concavity_scores[first_index]),
                        concavity_2=float(concavity_scores[second_index]),
                        inter_seed_axis=axis,
                        centre_distance=centre_distance,
                        endpoint_crown_support=endpoint_crown_support,
                    )
                    line_edge = float(np.mean(sample(fused_edge, samples, order=1)))
                    shift = 0.60 * line_normal
                    minus = samples - shift
                    plus = samples + shift
                    valid = (sample(mask_float, minus, order=0) >= 0.5) & (
                        sample(mask_float, plus, order=0) >= 0.5
                    )
                    if np.count_nonzero(valid) >= 4:
                        centre_height = sample(height_filled, samples)[valid]
                        side_height = 0.5 * (
                            sample(height_filled, minus)[valid] + sample(height_filled, plus)[valid]
                        )
                        height_valley = float(np.mean(side_height - centre_height))
                        minus_normal = np.column_stack(
                            [sample(normals[..., channel], minus)[valid] for channel in range(3)]
                        )
                        plus_normal = np.column_stack(
                            [sample(normals[..., channel], plus)[valid] for channel in range(3)]
                        )
                        normal_jump = float(
                            np.mean(
                                np.clip(1.0 - np.sum(minus_normal * plus_normal, axis=1), 0.0, 2.0)
                            )
                            / 2.0
                        )
                    else:
                        height_valley = 0.0
                        normal_jump = 0.0
                    concavity_support = float(
                        np.mean([concavity_scores[first_index], concavity_scores[second_index]])
                    )
                    base_evidence = (
                        0.42 * np.clip(concavity_support / 0.35, 0.0, 1.0)
                        + 0.24 * endpoint_edge
                        + 0.15 * line_edge
                        + 0.10 * np.clip(abs(height_valley) / 0.8, 0.0, 1.0)
                        + 0.09 * np.clip(normal_jump, 0.0, 1.0)
                    )
                    # Paired-notch geometry is deliberately soft evidence.  It
                    # changes ranking and confidence without making a standard
                    # two-notch shape mandatory for every tooth contact.
                    evidence = 0.84 * base_evidence + 0.16 * float(paired["score"])
                    angle = float(
                        np.degrees(np.arctan2(direction @ axis, direction @ perpendicular))
                    )
                    result.append(
                        {
                            "endpoint_1": endpoint_1,
                            "endpoint_2": endpoint_2,
                            "segment": candidate_segment,
                            "line_point": line_point,
                            "direction": direction,
                            "length": length,
                            "offset": float((line_point - midpoint) @ axis),
                            "angle": angle,
                            "line_edge": line_edge,
                            "endpoint_edge": endpoint_edge,
                            "height_valley": height_valley,
                            "normal_jump": normal_jump,
                            "concavity": concavity_support,
                            "paired": paired,
                            "evidence": float(evidence),
                        }
                    )
            return result

        candidates = build_candidates(contour, concavity, notch_direction)
        selection_method = "shortest_valid_concavity_pair"
        if not candidates:
            # Some posterior contacts form a broad, shallow embrasure.  Only
            # when the sharp-scale search fails, retry at molar-scale turning
            # windows so already-valid sharp contact chords remain unchanged.
            broad_contour, broad_concavity, broad_direction = _outer_concavity_candidates(
                components == first_component,
                lr_centres,
                ap_centres,
                resolution,
                span_mm_values=(0.90, 1.35),
            )
            candidates = build_candidates(broad_contour, broad_concavity, broad_direction)
            if candidates:
                selection_method = "shortest_valid_broad_concavity_pair"

        if not candidates:
            # Last geometric fallback: search the local contact corridor for
            # the shortest outer-contour-to-outer-contour neck.  Unlike the
            # former centre-midline fallback, both endpoints remain measured
            # anatomical silhouette points and the separator must pass inside
            # the crown union while separating both topology seeds.
            candidates = build_candidates(
                contour,
                np.zeros(len(contour), dtype=float),
                np.zeros((len(contour), 2), dtype=float),
                local_neck=True,
            )
            if candidates:
                selection_method = "shortest_valid_local_neck_pair"

        if not candidates:
            # Preserve a usable separator for review, but expose that it did
            # not satisfy the anatomical concavity-endpoint rule.
            legacy = find_multichannel_contact_chords(
                enhanced_maps=enhanced_maps,
                ordered_instances=[first, second],
            )[0]
            if legacy.kind == "gap":
                chords.append(gap(pair_index, first, second, midpoint, perpendicular))
            else:
                fallback = replace(
                    legacy,
                    pair_index=pair_index,
                    first_instance_id=int(first.instance_id),
                    second_instance_id=int(second.instance_id),
                    kind="uncertain",
                    selection_method="legacy_midline_fallback",
                )
                chords.append(fallback)
                accepted_segments.append(
                    np.asarray(
                        [
                            fallback.first_endpoint_lr_ap_mm,
                            fallback.second_endpoint_lr_ap_mm,
                        ],
                        dtype=float,
                    )
                )
            continue

        minimum_length = min(item["length"] for item in candidates)
        shortest = [item for item in candidates if item["length"] <= minimum_length + 0.30]
        shortest.sort(key=lambda item: item["evidence"], reverse=True)
        best = shortest[0]
        other_lengths = sorted(
            item["length"]
            for item in candidates
            if item is not best and item["length"] > best["length"] + 0.05
        )
        length_margin = float(other_lengths[0] - best["length"]) if other_lengths else 0.0
        accepted_segments.append(best["segment"])
        chords.append(
            ContactChord(
                pair_index=pair_index,
                first_instance_id=int(first.instance_id),
                second_instance_id=int(second.instance_id),
                kind="contact",
                line_point_lr_ap_mm=tuple(float(value) for value in best["line_point"]),
                line_direction_lr_ap=tuple(float(value) for value in best["direction"]),
                first_endpoint_lr_ap_mm=tuple(float(value) for value in best["endpoint_1"]),
                second_endpoint_lr_ap_mm=tuple(float(value) for value in best["endpoint_2"]),
                chord_length_mm=float(best["length"]),
                centre_offset_mm=float(best["offset"]),
                angle_offset_degrees=float(best["angle"]),
                mean_projection_score=float(best["line_edge"]),
                evidence_score=float(best["evidence"]),
                confidence_margin=length_margin,
                height_valley_mm=float(best["height_valley"]),
                normal_jump=float(best["normal_jump"]),
                endpoint_edge_support=float(best["endpoint_edge"]),
                endpoint_concavity_support=float(best["concavity"]),
                paired_concavity_facing_support=float(best["paired"]["facing"]),
                paired_concavity_axial_alignment=float(best["paired"]["axial_alignment"]),
                paired_concavity_crown_support=float(best["paired"]["crown_support"]),
                paired_concavity_score=float(best["paired"]["score"]),
                paired_concavity_level=str(best["paired"]["level"]),
                selection_method=selection_method,
            )
        )
    return chords


def _signed_line_distance(points: np.ndarray, chord: ContactChord) -> np.ndarray:
    """内部算法说明。"""
    line_point = np.asarray(chord.line_point_lr_ap_mm, dtype=float)
    direction = np.asarray(chord.line_direction_lr_ap, dtype=float)
    normal = np.asarray([-direction[1], direction[0]])
    return (points - line_point) @ normal


def _contour_from_region(
    region: np.ndarray,
    lr_centres: np.ndarray,
    ap_centres: np.ndarray,
) -> list[list[float]]:
    """内部算法说明。"""
    contours = find_contours(region.astype(float), 0.5)
    if not contours:
        return []
    contour = max(contours, key=len)
    if len(contour) > 300:
        contour = contour[np.linspace(0, len(contour) - 1, 300, dtype=int)]
    lr = np.interp(contour[:, 0], np.arange(len(lr_centres)), lr_centres)
    ap = np.interp(contour[:, 1], np.arange(len(ap_centres)), ap_centres)
    return np.column_stack([lr, ap]).tolist()


def split_projection_by_chords(
    *,
    feature_maps: dict[str, object],
    projection_mask: np.ndarray,
    ordered_instances: list,
    chords: list[ContactChord],
) -> tuple[list[ChordContourInstance], np.ndarray]:
    """内部算法说明。\n\nPartition the silhouette with chord half-planes and measure area centres."""

    lr_centres = np.asarray(feature_maps["lr_centres"], dtype=float)
    ap_centres = np.asarray(feature_maps["ap_centres"], dtype=float)
    resolution = float(feature_maps["resolution_mm"])
    lr_grid, ap_grid = np.meshgrid(lr_centres, ap_centres, indexing="ij")
    points = np.column_stack([lr_grid.ravel(), ap_grid.ravel()])
    base_mask = np.asarray(projection_mask, dtype=bool)
    label_grid = np.zeros(base_mask.shape, dtype=np.int32)
    results: list[ChordContourInstance] = []

    for index, instance in enumerate(ordered_instances):
        region = base_mask.copy().ravel()
        center = np.asarray(instance.center_lr_ap_mm, dtype=float)
        if index > 0:
            previous = chords[index - 1]
            values = _signed_line_distance(points, previous)
            center_sign = float(_signed_line_distance(center[None, :], previous)[0])
            region &= values * center_sign >= 0.0
        if index < len(chords):
            following = chords[index]
            values = _signed_line_distance(points, following)
            center_sign = float(_signed_line_distance(center[None, :], following)[0])
            region &= values * center_sign >= 0.0
        region = region.reshape(base_mask.shape)

        components = connected_components(region, connectivity=2)
        if np.max(components) > 0:
            center_row = int(np.argmin(np.abs(lr_centres - center[0])))
            center_column = int(np.argmin(np.abs(ap_centres - center[1])))
            component = int(components[center_row, center_column])
            if component == 0:
                candidates = np.argwhere(components > 0)
                if len(candidates):
                    physical = np.column_stack(
                        [lr_centres[candidates[:, 0]], ap_centres[candidates[:, 1]]]
                    )
                    nearest = int(np.argmin(np.linalg.norm(physical - center, axis=1)))
                    component = int(components[tuple(candidates[nearest])])
            if component > 0:
                region = components == component

        rows, columns = np.nonzero(region)
        if len(rows) == 0:
            raise RuntimeError(f"contact-chord region {index} is empty")
        centroid = np.asarray(
            [float(np.mean(lr_centres[rows])), float(np.mean(ap_centres[columns]))]
        )
        interior_distance = distance_transform_edt(region) * resolution
        maximum_radius = float(np.max(interior_distance))
        interior_rows, interior_columns = np.nonzero(
            interior_distance >= maximum_radius - 0.25 * resolution
        )
        interior_center = np.asarray(
            [
                float(np.mean(lr_centres[interior_rows])),
                float(np.mean(ap_centres[interior_columns])),
            ]
        )
        contour = _contour_from_region(region, lr_centres, ap_centres)
        if not contour:
            raise RuntimeError(f"contact-chord region {index} has no contour")
        label_grid[region] = index + 1
        results.append(
            ChordContourInstance(
                instance_id=index,
                source_instance_id=int(instance.instance_id),
                area_mm2=float(len(rows) * resolution**2),
                area_centroid_lr_ap_mm=(float(centroid[0]), float(centroid[1])),
                interior_center_lr_ap_mm=(float(interior_center[0]), float(interior_center[1])),
                maximum_interior_radius_mm=maximum_radius,
                pixel_count=len(rows),
                contour_lr_ap_mm=contour,
            )
        )
    return results, label_grid

"""算法说明。 Component-local crown segmentation with finite anatomical separators."""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
from scipy.ndimage import (
    binary_dilation,
    binary_erosion,
    distance_transform_edt,
    gaussian_filter,
)
from scipy.interpolate import RegularGridInterpolator
from skimage.draw import line
from skimage.graph import route_through_array
from skimage.measure import find_contours, label
from skimage.segmentation import watershed

from twin_guide.tooth_mapping.contact_chords import CrownSeed, find_shortest_concavity_chords

from .models import AlignmentPath, ArchFrame, ToothRegion


@dataclass(frozen=True)
class SegmentationDiagnostics:
    """算法说明。"""
    component_count: int
    seeded_component_count: int
    artifact_component_ids: tuple[int, ...]
    finite_separator_count: int
    fallback_component_count: int
    separator_component_local: bool
    assigned_pixel_fraction: float
    separator_records: tuple[dict[str, object], ...]
    separator_candidate_records: tuple[dict[str, object], ...]
    gingiva_or_unassigned_label_enabled: bool = True
    unassigned_pixel_count: int = 0
    unassigned_area_mm2: float = 0.0
    unassigned_component_count: int = 0
    surface_valley_evidence_available: bool = False
    surface_valley_separator_count: int = 0
    surface_valley_separator_records: tuple[dict[str, object], ...] = ()
    multi_view_boundary_evidence_available: bool = False
    multi_view_boundary_fused_into_watershed: bool = False
    boundary_first_segmentation: bool = False
    midpoint_fallback_disabled: bool = False
    unsupported_separator_records: tuple[dict[str, object], ...] = ()
    boundary_topology_records: tuple[dict[str, object], ...] = ()


def resample_partition_maps(
    maps: dict[str, object], target_resolution_mm: float
) -> dict[str, object]:
    """算法说明。

Resample selected physical evidence to a resolution-invariant grid.

    Candidate detection remains on the requested projection grid.  Only the
    final finite separators and component-local measurement use this canonical
    physical lattice, preventing chord endpoints from changing merely because
    the source pixel pitch changed.
    """

    source_resolution = float(maps["resolution_mm"])
    lr = np.asarray(maps["lr_centres"], dtype=float)
    ap = np.asarray(maps["ap_centres"], dtype=float)
    target_resolution = float(target_resolution_mm)
    if (
        abs(source_resolution - target_resolution) < 1.0e-9
        and np.allclose(lr / target_resolution, np.round(lr / target_resolution), atol=1.0e-7)
        and np.allclose(ap / target_resolution, np.round(ap / target_resolution), atol=1.0e-7)
    ):
        return maps

    target_lr = np.arange(
        np.ceil(lr[0] / target_resolution) * target_resolution,
        np.floor(lr[-1] / target_resolution) * target_resolution
        + 0.5 * target_resolution,
        target_resolution,
    )
    target_ap = np.arange(
        np.ceil(ap[0] / target_resolution) * target_resolution,
        np.floor(ap[-1] / target_resolution) * target_resolution
        + 0.5 * target_resolution,
        target_resolution,
    )
    grid_lr, grid_ap = np.meshgrid(target_lr, target_ap, indexing="ij")
    points = np.column_stack([grid_lr.ravel(), grid_ap.ravel()])

    def interpolate(values, *, method: str = "linear", fill_value: float = 0.0):
        """算法说明。"""
        array = np.asarray(values, dtype=float)
        interpolator = RegularGridInterpolator(
            (lr, ap), array, method=method, bounds_error=False,
            fill_value=fill_value,
        )
        return interpolator(points).reshape(grid_lr.shape + array.shape[2:])

    source_mask = np.asarray(maps["silhouette"], dtype=bool)
    silhouette = interpolate(
        source_mask.astype(float), method="nearest", fill_value=0.0
    ) >= 0.5
    valid_height = source_mask & np.isfinite(np.asarray(maps["top_height_mm"], dtype=float))
    support = interpolate(valid_height.astype(float), fill_value=0.0)
    height_numerator = interpolate(
        np.where(valid_height, np.asarray(maps["top_height_mm"], dtype=float), 0.0),
        fill_value=0.0,
    )
    height = np.full(silhouette.shape, np.nan, dtype=float)
    height_valid = silhouette & (support > 1.0e-6)
    height[height_valid] = height_numerator[height_valid] / support[height_valid]

    source_normals = np.asarray(maps["top_normal_lr_ap_occ"], dtype=float)
    normal_numerator = interpolate(
        np.where(valid_height[..., None], source_normals, 0.0), fill_value=0.0
    )
    normals = np.zeros(silhouette.shape + (3,), dtype=float)
    normals[height_valid] = (
        normal_numerator[height_valid] / support[height_valid][:, None]
    )
    norm = np.linalg.norm(normals, axis=2, keepdims=True)
    normals = np.divide(normals, np.maximum(norm, 1.0e-9))
    normal_rgb = np.clip(0.5 * (normals + 1.0), 0.0, 1.0)
    normal_rgb[~silhouette] = 1.0

    output = dict(maps)
    output.update({
        "resolution_mm": target_resolution,
        "lr_centres": target_lr,
        "ap_centres": target_ap,
        "silhouette": silhouette,
        "top_height_mm": height,
        "top_normal_lr_ap_occ": normals,
        "normal_rgb": normal_rgb,
        "fused_edge": np.clip(interpolate(maps["fused_edge"], fill_value=0.0), 0.0, 1.0),
        "covered_pixel_count": int(np.count_nonzero(silhouette)),
        "source_projection_resolution_mm": source_resolution,
    })
    for field in ("local_gingiva_baseline_mm", "relative_crown_relief_mm"):
        if field not in maps:
            continue
        source = np.asarray(maps[field], dtype=float)
        valid = source_mask & np.isfinite(source)
        field_support = interpolate(valid.astype(float), fill_value=0.0)
        numerator = interpolate(np.where(valid, source, 0.0), fill_value=0.0)
        values = np.full(silhouette.shape, np.nan, dtype=float)
        selected = silhouette & (field_support > 1.0e-6)
        values[selected] = numerator[selected] / field_support[selected]
        output[field] = values
    if "relative_crown_relief_score" in maps:
        output["relative_crown_relief_score"] = np.clip(
            interpolate(maps["relative_crown_relief_score"], fill_value=0.0),
            0.0,
            1.0,
        )
    for field in (
        "minimum_curvature_per_mm",
        "surface_valley_score",
        "multi_view_boundary_score",
        "multi_view_consistency",
    ):
        if field not in maps:
            continue
        source = np.asarray(maps[field], dtype=float)
        valid = source_mask & np.isfinite(source)
        field_support = interpolate(valid.astype(float), fill_value=0.0)
        numerator = interpolate(np.where(valid, source, 0.0), fill_value=0.0)
        values = np.full(silhouette.shape, np.nan, dtype=float)
        selected = silhouette & (field_support > 1.0e-6)
        values[selected] = numerator[selected] / field_support[selected]
        if field in {
            "surface_valley_score",
            "multi_view_boundary_score",
            "multi_view_consistency",
        }:
            values[selected] = np.clip(values[selected], 0.0, 1.0)
        output[field] = values
    return output


def _nearest_mask_pixel(mask: np.ndarray, row: int, column: int) -> tuple[int, int]:
    """算法说明。"""
    if mask[row, column]:
        return row, column
    candidates = np.argwhere(mask)
    if not len(candidates):
        raise RuntimeError("cannot place a seed in an empty component")
    nearest = int(np.argmin(np.sum((candidates - np.asarray([row, column])) ** 2, axis=1)))
    return tuple(int(value) for value in candidates[nearest])


def _physical_to_index(
    point: tuple[float, float], lr: np.ndarray, ap: np.ndarray
) -> tuple[int, int]:
    """算法说明。"""
    return int(np.argmin(np.abs(lr - point[0]))), int(np.argmin(np.abs(ap - point[1])))


def _finite_barrier(
    chords,
    shape: tuple[int, int],
    lr: np.ndarray,
    ap: np.ndarray,
    component: np.ndarray,
) -> np.ndarray:
    """算法说明。"""
    barrier = np.zeros(shape, dtype=bool)
    for chord in chords:
        if chord.first_endpoint_lr_ap_mm is None or chord.second_endpoint_lr_ap_mm is None:
            continue
        first = _physical_to_index(chord.first_endpoint_lr_ap_mm, lr, ap)
        second = _physical_to_index(chord.second_endpoint_lr_ap_mm, lr, ap)
        rows, columns = line(first[0], first[1], second[0], second[1])
        valid = (
            (rows >= 0) & (rows < shape[0])
            & (columns >= 0) & (columns < shape[1])
        )
        barrier[rows[valid], columns[valid]] = True
    return binary_dilation(barrier, iterations=1) & component


def _remove_endpoint_collisions(
    chords,
    *,
    resolution: float,
    component_id: int,
) -> tuple[list, list[dict[str, object]]]:
    """内部算法说明。 Reject adjacent separators that reuse one physical contour endpoint.

    Two consecutive inter-tooth separators must occur at two ordered locations
    on both the buccal and lingual component boundaries.  If they reuse an
    endpoint, watershed would enclose a triangular sliver rather than a crown.
    The comparison uses only the raster resolution as its tolerance; no
    case-specific millimetre threshold is involved.
    """

    ordered = sorted(chords, key=lambda item: int(item.pair_index))
    rejected: set[int] = set()
    records: list[dict[str, object]] = []

    def endpoints(chord) -> tuple[np.ndarray, np.ndarray]:
        """内部算法说明。"""
        return (
            np.asarray(chord.first_endpoint_lr_ap_mm, dtype=float),
            np.asarray(chord.second_endpoint_lr_ap_mm, dtype=float),
        )

    def support_key(chord) -> tuple[float, float, float]:
        """内部算法说明。"""
        return (
            float(chord.paired_concavity_crown_support or 0.0),
            float(chord.paired_concavity_score or 0.0),
            float(chord.evidence_score or 0.0),
        )

    tolerance = 3.0 * float(resolution)
    for first, second in zip(ordered, ordered[1:], strict=False):
        if int(second.pair_index) != int(first.pair_index) + 1:
            continue
        first_endpoints = endpoints(first)
        second_endpoints = endpoints(second)
        distance = min(
            float(np.linalg.norm(first_point - second_point))
            for first_point in first_endpoints
            for second_point in second_endpoints
        )
        if distance > tolerance:
            continue
        loser = first if support_key(first) < support_key(second) else second
        rejected.add(int(loser.pair_index))
        records.append({
            "component_id": int(component_id),
            "kind": "separator_endpoint_topology_conflict",
            "first_pair_index": int(first.pair_index),
            "second_pair_index": int(second.pair_index),
            "shared_endpoint_distance_mm": distance,
            "resolution_tolerance_mm": tolerance,
            "rejected_pair_index": int(loser.pair_index),
            "reason": "adjacent_separators_reuse_component_boundary_endpoint",
        })
    return [
        chord for chord in ordered if int(chord.pair_index) not in rejected
    ], records


def _local_midpoint_barrier(
    records: list[dict[str, object]],
    pair_indices: set[int],
    lr: np.ndarray,
    ap: np.ndarray,
    component: np.ndarray,
    resolution: float,
) -> np.ndarray:
    """算法说明。 Build finite, component-local fallback barriers between adjacent seeds."""

    grid_lr, grid_ap = np.meshgrid(lr, ap, indexing="ij")
    barrier = np.zeros(component.shape, dtype=bool)
    for pair_index in sorted(pair_indices):
        first = np.asarray(
            records[pair_index]["assignment"].center_lr_ap_mm, dtype=float
        )
        second = np.asarray(
            records[pair_index + 1]["assignment"].center_lr_ap_mm, dtype=float
        )
        along = second - first
        separation = float(np.linalg.norm(along))
        if separation <= 1.0e-6:
            continue
        along /= separation
        transverse = np.asarray([-along[1], along[0]])
        midpoint = 0.5 * (first + second)
        delta_lr = grid_lr - midpoint[0]
        delta_ap = grid_ap - midpoint[1]
        longitudinal = delta_lr * along[0] + delta_ap * along[1]
        transverse_distance = delta_lr * transverse[0] + delta_ap * transverse[1]
        local = (
            (np.abs(longitudinal) <= 0.75 * resolution)
            & (np.abs(transverse_distance) <= max(1.25 * separation, 6.0))
        )
        barrier |= local & component
    return binary_dilation(barrier, iterations=1) & component


def _surface_valley_barrier(
    *,
    records: list[dict[str, object]],
    pair_index: int,
    maps: dict[str, object],
    frame: ArchFrame,
    component: np.ndarray,
    minimum_mean_support: float,
    minimum_coverage: float,
    require_independent_crown_basins: bool = False,
) -> tuple[np.ndarray, dict[str, object]]:
    """算法说明。 Trace a finite buccolingual separator through projected 3-D valleys."""

    empty = np.zeros(component.shape, dtype=bool)
    base_record: dict[str, object] = {
        "pair_index": int(pair_index),
        "component_id": int(records[pair_index]["component_id"]),
        "first_FDI": int(records[pair_index]["assignment"].fdi),
        "second_FDI": int(records[pair_index + 1]["assignment"].fdi),
        "accepted": False,
    }
    if "surface_valley_score" not in maps:
        return empty, {**base_record, "reason": "surface_valley_score_unavailable"}
    score = np.asarray(maps["surface_valley_score"], dtype=float)
    valid_score = component & np.isfinite(score)
    if np.count_nonzero(valid_score) < 30 or float(np.nanmax(score)) <= 0.0:
        return empty, {**base_record, "reason": "surface_valley_score_degenerate"}

    first_assignment = records[pair_index]["assignment"]
    second_assignment = records[pair_index + 1]["assignment"]
    first = np.asarray(first_assignment.center_lr_ap_mm, dtype=float)
    second = np.asarray(second_assignment.center_lr_ap_mm, dtype=float)
    along = second - first
    separation = float(np.linalg.norm(along))
    if separation <= 1.0e-6:
        return empty, {**base_record, "reason": "coincident_seeds"}
    along /= separation
    transverse = np.asarray([-along[1], along[0]])
    midpoint = 0.5 * (first + second)
    lr = np.asarray(maps["lr_centres"], dtype=float)
    ap = np.asarray(maps["ap_centres"], dtype=float)
    resolution = float(maps["resolution_mm"])
    grid_lr, grid_ap = np.meshgrid(lr, ap, indexing="ij")
    delta_lr = grid_lr - midpoint[0]
    delta_ap = grid_ap - midpoint[1]
    longitudinal = delta_lr * along[0] + delta_ap * along[1]
    transverse_coordinate = delta_lr * transverse[0] + delta_ap * transverse[1]
    mean_s = 0.5 * (float(first_assignment.s_mm) + float(second_assignment.s_mm))
    local_scale = frame.scale_at_s(mean_s)
    basin_support = _independent_crown_basin_support(
        first_center_lr_ap_mm=first,
        second_center_lr_ap_mm=second,
        maps=maps,
        component=component,
        local_scale_mm=local_scale,
    )
    base_record["independent_crown_basin_support"] = basin_support
    if (
        require_independent_crown_basins
        and not bool(basin_support.get("accepted"))
    ):
        return empty, {
            **base_record,
            "reason": "two_independent_crown_basins_not_demonstrated",
        }
    corridor_half_width = max(
        2.5 * resolution,
        min(0.30 * separation, 0.28 * local_scale),
    )
    corridor = component & (np.abs(longitudinal) <= corridor_half_width)
    boundary = component & ~binary_erosion(component, iterations=1)
    endpoint_slice_half_width = max(2.5 * resolution, 0.20 * corridor_half_width)
    endpoints = np.argwhere(
        boundary & corridor
        & (np.abs(longitudinal) <= endpoint_slice_half_width)
    )
    if len(endpoints) < 2:
        return empty, {**base_record, "reason": "corridor_has_no_boundary_span"}
    endpoint_transverse = transverse_coordinate[
        endpoints[:, 0], endpoints[:, 1]
    ]
    transverse_span = float(np.max(endpoint_transverse) - np.min(endpoint_transverse))
    if transverse_span + 1.0e-9 < max(0.10 * local_scale, 4.0 * resolution):
        return empty, {
            **base_record,
            "reason": "corridor_boundary_span_is_too_short",
            "transverse_span_mm": transverse_span,
        }
    endpoint_score = np.nan_to_num(
        score[endpoints[:, 0], endpoints[:, 1]], nan=0.0
    )
    endpoint_longitudinal = np.abs(
        longitudinal[endpoints[:, 0], endpoints[:, 1]]
    )

    def choose_endpoint(selected: np.ndarray) -> tuple[int, int]:
        """算法说明。"""
        candidate_indices = np.flatnonzero(selected)
        utility = (
            endpoint_score[candidate_indices]
            - 0.20 * endpoint_longitudinal[candidate_indices]
            / max(endpoint_slice_half_width, resolution)
        )
        chosen = endpoints[candidate_indices[int(np.argmax(utility))]]
        return int(chosen[0]), int(chosen[1])

    first_endpoint = choose_endpoint(
        endpoint_transverse <= np.min(endpoint_transverse) + 1.5 * resolution
    )
    second_endpoint = choose_endpoint(
        endpoint_transverse >= np.max(endpoint_transverse) - 1.5 * resolution
    )

    # A true interproximal boundary is local to the two adjacent crown basins.
    # Searching the complete connected component allowed a shortest path to
    # stitch unrelated cusp grooves into an artificial separator.  Permit
    # curvature, but never more than a local crown-scale envelope.
    routing_half_width = max(
        2.0 * corridor_half_width,
        0.90 * local_scale,
        0.85 * separation,
    )
    routing_corridor = component & (
        np.abs(longitudinal) <= routing_half_width
    )
    if not (
        routing_corridor[first_endpoint]
        and routing_corridor[second_endpoint]
    ):
        return empty, {
            **base_record,
            "reason": "boundary_endpoints_leave_local_crown_corridor",
            "corridor_half_width_mm": corridor_half_width,
            "routing_half_width_mm": routing_half_width,
        }

    fused_edge = np.asarray(
        maps.get("fused_edge", np.zeros(component.shape)), dtype=float
    )
    multi_view = np.clip(np.nan_to_num(
        np.asarray(
            maps.get("multi_view_boundary_score", np.zeros(component.shape)),
            dtype=float,
        ),
        nan=0.0,
    ), 0.0, 1.0)
    multi_view_consistency = np.clip(np.nan_to_num(
        np.asarray(
            maps.get("multi_view_consistency", np.zeros(component.shape)),
            dtype=float,
        ),
        nan=0.0,
    ), 0.0, 1.0)
    # The face-level aggregate already contains a consistency factor.  The
    # second square root is deliberately mild: it suppresses single-view
    # grooves while retaining contacts repeatedly visible from oblique views.
    view_consistent_boundary = multi_view * np.sqrt(multi_view_consistency)
    normalized_longitudinal = (
        np.abs(longitudinal) / max(corridor_half_width, resolution)
    )
    valley_cost = (
        1.0
        + 2.2 * (1.0 - np.nan_to_num(score, nan=0.0))
        + 0.8 * (1.0 - view_consistent_boundary)
        + 0.6 * (1.0 - np.clip(fused_edge, 0.0, 1.0))
        + 1.2 * normalized_longitudinal**2
    )
    valley_cost[~routing_corridor] = 1.0e6

    def trace_path(cost: np.ndarray, evidence_name: str):
        """内部算法说明。 Trace one finite boundary candidate between the same outer endpoints."""

        try:
            path, total_cost = route_through_array(
                cost,
                first_endpoint,
                second_endpoint,
                fully_connected=True,
                geometric=True,
            )
        except Exception as error:
            return None, None, f"{evidence_name}_path_error: {error}"
        path_array = np.asarray(path, dtype=int)
        if (
            len(path_array) < 3
            or np.any(~routing_corridor[path_array[:, 0], path_array[:, 1]])
        ):
            return None, None, f"{evidence_name}_path_left_local_corridor"
        return path_array, float(total_cost), None

    valley_path, valley_total_cost, valley_error = trace_path(
        valley_cost, "surface_valley"
    )
    if valley_path is None:
        return empty, {**base_record, "reason": valley_error}

    def path_support(path_array: np.ndarray) -> dict[str, float]:
        """内部算法说明。"""
        path_score = np.nan_to_num(
            score[path_array[:, 0], path_array[:, 1]], nan=0.0
        )
        path_multi_view = view_consistent_boundary[
            path_array[:, 0], path_array[:, 1]
        ]
        return {
            "valley_mean": float(np.mean(path_score)),
            "valley_coverage": float(
                np.mean(path_score >= minimum_mean_support)
            ),
            "multi_view_mean": float(np.mean(path_multi_view)),
            "multi_view_p90": float(np.quantile(path_multi_view, 0.90)),
        }

    valley_support = path_support(valley_path)
    valley_accepted = (
        valley_support["valley_mean"] >= minimum_mean_support
        and valley_support["valley_coverage"] >= minimum_coverage
    )

    # A curvature valley is not the only physical manifestation of a contact.
    # On tight interproximal contacts the strongest signal can instead be a
    # repeatable normal/occlusion discontinuity in several oblique views.  It
    # is admitted only as a finite boundary connecting the component exterior,
    # and only when that boundary is a strict local maximum relative to equal
    # paths shifted into both neighbouring crowns.  This is a data-relative
    # topological test, not an absolute case-specific score threshold.
    multiview_available = (
        "multi_view_boundary_score" in maps
        and float(np.max(view_consistent_boundary[component])) > 0.0
    )
    multiview_path = None
    multiview_total_cost = None
    multiview_support = {
        "multi_view_mean": 0.0,
        "multi_view_p90": 0.0,
        "negative_shift_mean": 0.0,
        "positive_shift_mean": 0.0,
        "negative_shift_p90": 0.0,
        "positive_shift_p90": 0.0,
        "multi_view_q25": 0.0,
        "negative_shift_q25": 0.0,
        "positive_shift_q25": 0.0,
        "continuous_dominance_fraction": 0.0,
    }
    multiview_accepted = False
    if multiview_available:
        multiview_cost = (
            1.0
            + 2.4 * (1.0 - view_consistent_boundary)
            + 0.8 * (1.0 - np.clip(fused_edge, 0.0, 1.0))
            + 1.2 * normalized_longitudinal**2
        )
        multiview_cost[~routing_corridor] = 1.0e6
        multiview_path, multiview_total_cost, _ = trace_path(
            multiview_cost, "multi_view"
        )
        if multiview_path is not None:
            central = path_support(multiview_path)
            multiview_support["multi_view_mean"] = central["multi_view_mean"]
            multiview_support["multi_view_p90"] = central["multi_view_p90"]
            central_values = view_consistent_boundary[
                multiview_path[:, 0], multiview_path[:, 1]
            ]
            multiview_support["multi_view_q25"] = float(
                np.quantile(central_values, 0.25)
            )
            shifted_series: dict[str, np.ndarray] = {}
            shift_mm = max(2.0 * resolution, 0.55 * corridor_half_width)
            for name, sign in (("negative", -1.0), ("positive", 1.0)):
                path_lr = lr[multiview_path[:, 0]] + sign * shift_mm * along[0]
                path_ap = ap[multiview_path[:, 1]] + sign * shift_mm * along[1]
                shifted_rows = np.clip(
                    np.rint(np.interp(path_lr, lr, np.arange(len(lr)))).astype(int),
                    0,
                    len(lr) - 1,
                )
                shifted_columns = np.clip(
                    np.rint(np.interp(path_ap, ap, np.arange(len(ap)))).astype(int),
                    0,
                    len(ap) - 1,
                )
                valid = component[shifted_rows, shifted_columns]
                shifted = view_consistent_boundary[
                    shifted_rows[valid], shifted_columns[valid]
                ]
                if len(shifted) >= max(3, len(multiview_path) // 3):
                    shifted_series[name] = shifted
                    multiview_support[f"{name}_shift_mean"] = float(
                        np.mean(shifted)
                    )
                    multiview_support[f"{name}_shift_p90"] = float(
                        np.quantile(shifted, 0.90)
                    )
                    multiview_support[f"{name}_shift_q25"] = float(
                        np.quantile(shifted, 0.25)
                    )
            side_means = (
                multiview_support["negative_shift_mean"],
                multiview_support["positive_shift_mean"],
            )
            side_p90 = (
                multiview_support["negative_shift_p90"],
                multiview_support["positive_shift_p90"],
            )
            side_q25 = (
                multiview_support["negative_shift_q25"],
                multiview_support["positive_shift_q25"],
            )
            # Sparse high responses are typical of an intracrown groove.  A
            # contact must form a continuous ridge: at least the lower quarter
            # of its path remains stronger than the corresponding side paths.
            if len(shifted_series) == 2:
                comparison_length = min(
                    len(central_values),
                    len(shifted_series["negative"]),
                    len(shifted_series["positive"]),
                )
                if comparison_length:
                    indices = np.linspace(
                        0, len(central_values) - 1, comparison_length
                    ).astype(int)
                    negative_indices = np.linspace(
                        0,
                        len(shifted_series["negative"]) - 1,
                        comparison_length,
                    ).astype(int)
                    positive_indices = np.linspace(
                        0,
                        len(shifted_series["positive"]) - 1,
                        comparison_length,
                    ).astype(int)
                    side_envelope = np.maximum(
                        shifted_series["negative"][negative_indices],
                        shifted_series["positive"][positive_indices],
                    )
                    multiview_support["continuous_dominance_fraction"] = float(
                        np.mean(central_values[indices] > side_envelope)
                    )
            multiview_accepted = (
                min(side_means) > 0.0
                and central["multi_view_mean"] > max(side_means)
                and central["multi_view_p90"] > float(np.mean(side_p90))
                and multiview_support["multi_view_q25"] > max(side_q25)
                and multiview_support["multi_view_q25"] > 0.0
                and multiview_support["continuous_dominance_fraction"] >= 0.50
            )

    if valley_accepted:
        path_array = valley_path
        total_cost = valley_total_cost
        accepted_by = "surface_curvature_valley"
    elif multiview_accepted and multiview_path is not None:
        path_array = multiview_path
        total_cost = multiview_total_cost
        accepted_by = "multiview_normal_discontinuity"
    else:
        path_array = valley_path
        total_cost = valley_total_cost
        accepted_by = None
    accepted = bool(valley_accepted or multiview_accepted)
    record = {
        **base_record,
        "accepted": bool(accepted),
        "reason": None if accepted else "insufficient_anatomical_boundary_support",
        "accepted_by": accepted_by,
        "mean_support": valley_support["valley_mean"],
        "coverage": valley_support["valley_coverage"],
        "multi_view_mean_support": multiview_support["multi_view_mean"],
        "multi_view_p90_support": multiview_support["multi_view_p90"],
        "multi_view_local_dominance": multiview_support,
        "multi_view_used_as_independent_boundary_hypothesis": bool(
            multiview_available
        ),
        "minimum_mean_support": float(minimum_mean_support),
        "minimum_coverage": float(minimum_coverage),
        "path_length_mm": float((len(path_array) - 1) * resolution),
        "transverse_span_mm": transverse_span,
        "corridor_half_width_mm": corridor_half_width,
        "routing_half_width_mm": routing_half_width,
        "total_path_cost": float(total_cost),
        "first_endpoint_lr_ap_mm": [
            float(lr[first_endpoint[0]]), float(ap[first_endpoint[1]])
        ],
        "second_endpoint_lr_ap_mm": [
            float(lr[second_endpoint[0]]), float(ap[second_endpoint[1]])
        ],
    }
    if not accepted:
        return empty, record
    barrier = np.zeros(component.shape, dtype=bool)
    barrier[path_array[:, 0], path_array[:, 1]] = True
    barrier = binary_dilation(barrier, iterations=1) & component
    separated = label(component & ~barrier, connectivity=2)
    first_row, first_column = _physical_to_index(
        first_assignment.center_lr_ap_mm, lr, ap
    )
    second_row, second_column = _physical_to_index(
        second_assignment.center_lr_ap_mm, lr, ap
    )
    if barrier[first_row, first_column]:
        first_row, first_column = _nearest_mask_pixel(
            component & ~barrier, first_row, first_column
        )
    if barrier[second_row, second_column]:
        second_row, second_column = _nearest_mask_pixel(
            component & ~barrier, second_row, second_column
        )
    first_side = int(separated[first_row, first_column])
    second_side = int(separated[second_row, second_column])
    topology_separates_seeds = (
        first_side > 0 and second_side > 0 and first_side != second_side
    )
    record["topology_separates_seeds"] = bool(topology_separates_seeds)
    record["first_seed_side"] = first_side
    record["second_seed_side"] = second_side
    if not topology_separates_seeds:
        record["accepted"] = False
        record["reason"] = "finite_boundary_does_not_separate_seed_basins"
        record["accepted_by"] = None
        return empty, record
    return barrier, record


def _independent_crown_basin_support(
    *,
    first_center_lr_ap_mm: np.ndarray,
    second_center_lr_ap_mm: np.ndarray,
    maps: dict[str, object],
    component: np.ndarray,
    local_scale_mm: float,
) -> dict[str, object]:
    """内部算法说明。 Test whether two seeds occupy distinct persistent crown basins.

    The test is deliberately independent of FDI labels and expected tooth
    count.  It constructs a local crown response from silhouette depth and the
    3-D relative-relief field, then measures the merge saddle of the two local
    maxima.  The maxima are accepted as separate basins only when their smaller
    topological prominence exceeds the scan-local response variation.
    """

    if "relative_crown_relief_score" not in maps:
        return {
            "available": False,
            "accepted": False,
            "reason": "relative_crown_relief_score_unavailable",
        }
    lr = np.asarray(maps["lr_centres"], dtype=float)
    ap = np.asarray(maps["ap_centres"], dtype=float)
    resolution = float(maps["resolution_mm"])
    relief = np.clip(np.nan_to_num(
        np.asarray(maps["relative_crown_relief_score"], dtype=float),
        nan=0.0,
    ), 0.0, 1.0)
    if float(np.max(relief[component], initial=0.0)) <= 0.0:
        return {
            "available": False,
            "accepted": False,
            "reason": "relative_crown_relief_score_degenerate",
        }

    first = np.asarray(first_center_lr_ap_mm, dtype=float)
    second = np.asarray(second_center_lr_ap_mm, dtype=float)
    axis = second - first
    separation = float(np.linalg.norm(axis))
    if separation <= 1.0e-9:
        return {
            "available": True,
            "accepted": False,
            "reason": "coincident_crown_seeds",
        }
    axis /= separation
    transverse = np.asarray([-axis[1], axis[0]])
    midpoint = 0.5 * (first + second)
    grid_lr, grid_ap = np.meshgrid(lr, ap, indexing="ij")
    delta_lr = grid_lr - midpoint[0]
    delta_ap = grid_ap - midpoint[1]
    longitudinal = delta_lr * axis[0] + delta_ap * axis[1]
    transverse_distance = delta_lr * transverse[0] + delta_ap * transverse[1]
    local = component & (
        np.abs(longitudinal) <= 0.5 * separation + 0.40 * local_scale_mm
    ) & (
        np.abs(transverse_distance) <= 0.75 * local_scale_mm
    )
    if np.count_nonzero(local) < 30:
        return {
            "available": True,
            "accepted": False,
            "reason": "local_crown_corridor_is_degenerate",
        }

    depth = distance_transform_edt(component) * resolution
    visible_depth = depth[local]
    depth_scale = float(np.quantile(visible_depth, 0.90))
    if depth_scale <= 1.0e-9:
        return {
            "available": True,
            "accepted": False,
            "reason": "local_silhouette_depth_is_degenerate",
        }
    depth_score = np.clip(depth / depth_scale, 0.0, 1.0)
    response = np.sqrt(depth_score * relief)
    response[~local] = 0.0

    distance_first = np.hypot(grid_lr - first[0], grid_ap - first[1])
    distance_second = np.hypot(grid_lr - second[0], grid_ap - second[1])
    first_neighbourhood = local & (distance_first <= distance_second) & (
        distance_first <= 0.70 * local_scale_mm
    )
    second_neighbourhood = local & (distance_second < distance_first) & (
        distance_second <= 0.70 * local_scale_mm
    )
    if not np.any(first_neighbourhood) or not np.any(second_neighbourhood):
        return {
            "available": True,
            "accepted": False,
            "reason": "one_crown_seed_has_no_local_basin_support",
        }

    def peak(mask: np.ndarray) -> tuple[tuple[int, int], float]:
        """内部算法说明。"""
        values = np.where(mask, response, -np.inf)
        flat_index = int(np.argmax(values))
        index = np.unravel_index(flat_index, values.shape)
        return (int(index[0]), int(index[1])), float(values[index])

    first_peak, first_peak_score = peak(first_neighbourhood)
    second_peak, second_peak_score = peak(second_neighbourhood)
    upper = min(first_peak_score, second_peak_score)
    if not np.isfinite(upper) or upper <= 0.0:
        return {
            "available": True,
            "accepted": False,
            "reason": "one_crown_basin_has_zero_response",
        }

    # Binary-search the highest super-level set that still connects both peaks.
    # This is the 0-D persistence merge saddle on the local response field.
    low = 0.0
    high = upper
    for _ in range(14):
        threshold = 0.5 * (low + high)
        superlevel = local & (response >= threshold)
        components = label(superlevel, connectivity=2)
        first_label = int(components[first_peak])
        second_label = int(components[second_peak])
        if first_label > 0 and first_label == second_label:
            low = threshold
        else:
            high = threshold
    saddle_score = low

    neighbour_differences = []
    horizontal = local[1:, :] & local[:-1, :]
    vertical = local[:, 1:] & local[:, :-1]
    if np.any(horizontal):
        neighbour_differences.append(np.abs(
            response[1:, :][horizontal] - response[:-1, :][horizontal]
        ))
    if np.any(vertical):
        neighbour_differences.append(np.abs(
            response[:, 1:][vertical] - response[:, :-1][vertical]
        ))
    differences = (
        np.concatenate(neighbour_differences)
        if neighbour_differences else np.asarray([0.0])
    )
    local_variation = float(np.quantile(differences, 0.75))
    first_prominence = first_peak_score - saddle_score
    second_prominence = second_peak_score - saddle_score
    minimum_prominence = min(first_prominence, second_prominence)
    accepted = bool(
        minimum_prominence > max(local_variation, 1.0e-6)
    )
    return {
        "available": True,
        "accepted": accepted,
        "method": "local_3d_relief_topological_basin_persistence",
        "first_peak_lr_ap_mm": [
            float(lr[first_peak[0]]), float(ap[first_peak[1]])
        ],
        "second_peak_lr_ap_mm": [
            float(lr[second_peak[0]]), float(ap[second_peak[1]])
        ],
        "first_peak_score": first_peak_score,
        "second_peak_score": second_peak_score,
        "merge_saddle_score": saddle_score,
        "first_prominence": first_prominence,
        "second_prominence": second_prominence,
        "minimum_prominence": minimum_prominence,
        "local_response_variation_q75": local_variation,
        "reason": None if accepted else "basin_prominence_does_not_exceed_local_variation",
    }


def _finite_chord_3d_boundary_support(
    *,
    first_endpoint_lr_ap_mm: tuple[float, float],
    second_endpoint_lr_ap_mm: tuple[float, float],
    first_center_lr_ap_mm: np.ndarray,
    second_center_lr_ap_mm: np.ndarray,
    maps: dict[str, object],
    component: np.ndarray,
    local_scale_mm: float,
) -> dict[str, object]:
    """内部算法说明。 Measure whether a finite chord follows a 3-D surface discontinuity.

    The centre line is compared with parallel lines shifted into both crowns.
    This makes the test local and data-relative: a true contact ridge must be
    continuously stronger than intracrown surface variation on both sides.
    No FDI label or expected tooth count contributes to the score.
    """

    lr = np.asarray(maps["lr_centres"], dtype=float)
    ap = np.asarray(maps["ap_centres"], dtype=float)
    resolution = float(maps["resolution_mm"])
    surface = np.clip(np.nan_to_num(
        np.asarray(
            maps.get("surface_valley_score", np.zeros(component.shape)),
            dtype=float,
        ),
        nan=0.0,
    ), 0.0, 1.0)
    multiview = np.clip(np.nan_to_num(
        np.asarray(
            maps.get("multi_view_boundary_score", np.zeros(component.shape)),
            dtype=float,
        ),
        nan=0.0,
    ), 0.0, 1.0)
    consistency = np.clip(np.nan_to_num(
        np.asarray(
            maps.get("multi_view_consistency", np.zeros(component.shape)),
            dtype=float,
        ),
        nan=0.0,
    ), 0.0, 1.0)
    fused_edge = np.clip(np.nan_to_num(
        np.asarray(maps.get("fused_edge", np.zeros(component.shape)), dtype=float),
        nan=0.0,
    ), 0.0, 1.0)
    view_consistent = multiview * np.sqrt(consistency)
    surface_available = bool(
        np.max(surface[component], initial=0.0) > 0.0
    )
    multiview_available = bool(
        np.max(view_consistent[component], initial=0.0) > 0.0
    )
    # A formal 3-D contact needs agreement between two independent geometric
    # channels: mesh curvature and repeated multi-view normal/depth change.
    # Taking max(surface, view) allowed an intracrown groove in either channel
    # to manufacture a boundary.  The geometric mean acts as an AND-like
    # consensus; fused_edge may corroborate but can never create support.
    combined = np.sqrt(surface * view_consistent) * (
        0.75 + 0.25 * fused_edge
    )
    if not (surface_available or multiview_available):
        return {
            "available": False,
            "accepted": False,
            "reason": "3d_boundary_channels_unavailable",
            "surface_valley_available": surface_available,
            "multi_view_boundary_available": multiview_available,
        }

    first_endpoint = np.asarray(first_endpoint_lr_ap_mm, dtype=float)
    second_endpoint = np.asarray(second_endpoint_lr_ap_mm, dtype=float)
    segment_length = float(np.linalg.norm(second_endpoint - first_endpoint))
    sample_count = max(8, int(np.ceil(segment_length / max(resolution, 1.0e-9))))
    interpolation = np.linspace(0.0, 1.0, sample_count)
    base_points = (
        first_endpoint[None, :] * (1.0 - interpolation[:, None])
        + second_endpoint[None, :] * interpolation[:, None]
    )
    crown_axis = np.asarray(second_center_lr_ap_mm, dtype=float) - np.asarray(
        first_center_lr_ap_mm, dtype=float
    )
    crown_axis_norm = float(np.linalg.norm(crown_axis))
    if crown_axis_norm <= 1.0e-9:
        return {"available": True, "accepted": False, "reason": "coincident_crown_centers"}
    crown_axis /= crown_axis_norm
    shift_mm = max(2.0 * resolution, 0.18 * local_scale_mm)

    def sample(field: np.ndarray, points: np.ndarray) -> np.ndarray:
        """内部算法说明。"""
        rows = np.clip(
            np.rint(np.interp(points[:, 0], lr, np.arange(len(lr)))).astype(int),
            0,
            len(lr) - 1,
        )
        columns = np.clip(
            np.rint(np.interp(points[:, 1], ap, np.arange(len(ap)))).astype(int),
            0,
            len(ap) - 1,
        )
        valid = component[rows, columns]
        return field[rows[valid], columns[valid]]

    central = sample(combined, base_points)
    negative = sample(combined, base_points - shift_mm * crown_axis)
    positive = sample(combined, base_points + shift_mm * crown_axis)
    minimum_samples = max(5, sample_count // 3)
    if min(len(central), len(negative), len(positive)) < minimum_samples:
        return {
            "available": True,
            "accepted": False,
            "reason": "insufficient_parallel_surface_support",
            "central_sample_count": int(len(central)),
            "negative_sample_count": int(len(negative)),
            "positive_sample_count": int(len(positive)),
        }

    def summary(
        values: np.ndarray, strong_threshold: float
    ) -> dict[str, float]:
        """内部算法说明。"""
        return {
            "mean": float(np.mean(values)),
            "q25": float(np.quantile(values, 0.25)),
            "coverage": float(np.mean(values >= strong_threshold)),
        }

    def contrast(field: np.ndarray) -> dict[str, object]:
        """内部算法说明。"""
        visible = field[component]
        positive_visible = visible[visible > 0.0]
        strong_threshold = (
            float(np.quantile(positive_visible, 0.75))
            if len(positive_visible) else 1.0
        )
        field_central = sample(field, base_points)
        field_negative = sample(
            field, base_points - shift_mm * crown_axis
        )
        field_positive = sample(
            field, base_points + shift_mm * crown_axis
        )
        if min(
            len(field_central), len(field_negative), len(field_positive)
        ) < minimum_samples:
            return {
                "accepted": False,
                "strong_threshold": strong_threshold,
                "reason": "insufficient_parallel_surface_support",
            }
        central_summary = summary(field_central, strong_threshold)
        negative_summary = summary(field_negative, strong_threshold)
        positive_summary = summary(field_positive, strong_threshold)
        side_mean = max(
            negative_summary["mean"], positive_summary["mean"]
        )
        side_q25 = max(
            negative_summary["q25"], positive_summary["q25"]
        )
        side_coverage = max(
            negative_summary["coverage"], positive_summary["coverage"]
        )
        accepted = bool(
            central_summary["mean"] > side_mean
            and central_summary["q25"] > side_q25
            and central_summary["q25"] > 0.0
            and central_summary["coverage"] > side_coverage
        )
        return {
            "accepted": accepted,
            "strong_threshold": strong_threshold,
            "central": central_summary,
            "negative_crown_side": negative_summary,
            "positive_crown_side": positive_summary,
        }

    consensus = contrast(combined)
    surface_contrast = contrast(surface)
    multiview_contrast = contrast(view_consistent)
    # The two channels originate from the same mesh but have different spatial
    # point-spread functions after rasterisation.  Requiring pixelwise overlap
    # of their ridges is unnecessarily brittle.  Channel-level agreement is the
    # invariant: each must independently show a locally dominant boundary along
    # the same finite chord.  The geometric-mean field remains diagnostic.
    accepted = bool(
        surface_contrast.get("accepted")
        and multiview_contrast.get("accepted")
    )
    return {
        "available": True,
        "accepted": accepted,
        "method": "finite_chord_independent_3d_channel_consensus",
        "shift_mm": float(shift_mm),
        "surface_valley_available": surface_available,
        "multi_view_boundary_available": multiview_available,
        "central": consensus.get("central", {}),
        "negative_crown_side": consensus.get(
            "negative_crown_side", {}
        ),
        "positive_crown_side": consensus.get(
            "positive_crown_side", {}
        ),
        "consensus_contrast": consensus,
        "surface_valley_contrast": surface_contrast,
        "multi_view_boundary_contrast": multiview_contrast,
        "reason": (
            None if accepted
            else "curvature_and_multiview_boundary_channels_do_not_both_support_chord"
        ),
    }


def _refine_finite_chord_to_3d_ridge(
    *,
    chord,
    first_center_lr_ap_mm: np.ndarray,
    second_center_lr_ap_mm: np.ndarray,
    maps: dict[str, object],
    component: np.ndarray,
    local_scale_mm: float,
    maximum_centre_offset_mm: float,
):
    """内部算法说明。 Relocate a 2-D chord onto a nearby, component-spanning 3-D ridge."""

    if (
        chord.first_endpoint_lr_ap_mm is None
        or chord.second_endpoint_lr_ap_mm is None
    ):
        return chord, None
    first_endpoint = np.asarray(chord.first_endpoint_lr_ap_mm, dtype=float)
    second_endpoint = np.asarray(chord.second_endpoint_lr_ap_mm, dtype=float)
    chord_direction = second_endpoint - first_endpoint
    original_length = float(np.linalg.norm(chord_direction))
    if original_length <= 1.0e-9:
        return chord, None
    chord_direction /= original_length
    chord_normal = np.asarray([-chord_direction[1], chord_direction[0]])
    first_center = np.asarray(first_center_lr_ap_mm, dtype=float)
    second_center = np.asarray(second_center_lr_ap_mm, dtype=float)
    crown_axis = second_center - first_center
    crown_separation = float(np.linalg.norm(crown_axis))
    if crown_separation <= 1.0e-9:
        return chord, None
    crown_axis /= crown_separation
    lr = np.asarray(maps["lr_centres"], dtype=float)
    ap = np.asarray(maps["ap_centres"], dtype=float)
    resolution = float(maps["resolution_mm"])
    boundary = component & ~binary_erosion(component, iterations=1)
    boundary_rows, boundary_columns = np.nonzero(boundary)
    boundary_points = np.column_stack([
        lr[boundary_rows], ap[boundary_columns]
    ])
    if len(boundary_points) < 2:
        return chord, None
    seed_midpoint = 0.5 * (first_center + second_center)
    initial_midpoint = 0.5 * (first_endpoint + second_endpoint)
    search_half_span = 0.45 * local_scale_mm
    shifts = np.linspace(-search_half_span, search_half_span, 13)
    candidates = []
    for shift_mm in shifts:
        line_point = initial_midpoint + float(shift_mm) * crown_axis
        perpendicular_distance = np.abs(
            (boundary_points - line_point) @ chord_normal
        )
        selected = boundary_points[
            perpendicular_distance <= 1.75 * resolution
        ]
        if len(selected) < 2:
            continue
        coordinate = (selected - line_point) @ chord_direction
        local = np.abs(coordinate) <= max(
            0.90 * local_scale_mm, 0.75 * original_length
        )
        selected = selected[local]
        coordinate = coordinate[local]
        if len(selected) < 2:
            continue
        candidate_first = selected[int(np.argmin(coordinate))]
        candidate_second = selected[int(np.argmax(coordinate))]
        candidate_length = float(np.linalg.norm(
            candidate_second - candidate_first
        ))
        if candidate_length < 0.35 * original_length:
            continue
        core_clearance = _finite_separator_core_clearance(
            first_endpoint_lr_ap_mm=candidate_first,
            second_endpoint_lr_ap_mm=candidate_second,
            first_center_lr_ap_mm=first_center,
            second_center_lr_ap_mm=second_center,
            maximum_centre_offset_mm=maximum_centre_offset_mm,
        )
        if not bool(core_clearance["accepted"]):
            continue
        support = _finite_chord_3d_boundary_support(
            first_endpoint_lr_ap_mm=(
                float(candidate_first[0]), float(candidate_first[1])
            ),
            second_endpoint_lr_ap_mm=(
                float(candidate_second[0]), float(candidate_second[1])
            ),
            first_center_lr_ap_mm=first_center,
            second_center_lr_ap_mm=second_center,
            maps=maps,
            component=component,
            local_scale_mm=local_scale_mm,
        )
        if not bool(support.get("accepted")):
            continue
        central = support["central"]
        ranking_score = (
            float(central["mean"])
            + float(central["coverage"])
            + float(central["q25"])
        )
        candidates.append((
            ranking_score,
            float(shift_mm),
            candidate_first,
            candidate_second,
            candidate_length,
            support,
        ))
    if not candidates:
        return chord, None
    _, shift_mm, candidate_first, candidate_second, candidate_length, support = max(
        candidates, key=lambda item: item[0]
    )
    refined_midpoint = 0.5 * (candidate_first + candidate_second)
    centre_offset = float((refined_midpoint - seed_midpoint) @ crown_axis)
    refined = replace(
        chord,
        line_point_lr_ap_mm=(
            float(refined_midpoint[0]), float(refined_midpoint[1])
        ),
        first_endpoint_lr_ap_mm=(
            float(candidate_first[0]), float(candidate_first[1])
        ),
        second_endpoint_lr_ap_mm=(
            float(candidate_second[0]), float(candidate_second[1])
        ),
        chord_length_mm=float(candidate_length),
        centre_offset_mm=centre_offset,
        selection_method="local_3d_surface_ridge_refinement",
    )
    return refined, {
        "accepted": True,
        "initial_shift_mm": shift_mm,
        "initial_chord_length_mm": original_length,
        "refined_chord_length_mm": candidate_length,
        "support": support,
    }


def _finite_separator_core_clearance(
    *,
    first_endpoint_lr_ap_mm: np.ndarray | tuple[float, float],
    second_endpoint_lr_ap_mm: np.ndarray | tuple[float, float],
    first_center_lr_ap_mm: np.ndarray | tuple[float, float],
    second_center_lr_ap_mm: np.ndarray | tuple[float, float],
    maximum_centre_offset_mm: float,
) -> dict[str, object]:
    """内部算法说明。 Reject a separator that enters either physical crown core.

    The accepted midpoint corridor already bounds a separator to the central
    part of the inter-core segment.  Its complement defines two core protection
    radii without introducing another case-level distance threshold.  Explicit
    point-to-segment distances additionally reject an oblique chord that has a
    valid midpoint but still crosses a core.
    """

    first_endpoint = np.asarray(first_endpoint_lr_ap_mm, dtype=float)
    second_endpoint = np.asarray(second_endpoint_lr_ap_mm, dtype=float)
    first_center = np.asarray(first_center_lr_ap_mm, dtype=float)
    second_center = np.asarray(second_center_lr_ap_mm, dtype=float)
    segment = second_endpoint - first_endpoint
    segment_length_squared = float(segment @ segment)
    center_axis = second_center - first_center
    seed_separation = float(np.linalg.norm(center_axis))
    if segment_length_squared <= 1.0e-12 or seed_separation <= 1.0e-9:
        return {
            "available": False,
            "accepted": False,
            "reason": "degenerate_separator_or_seed_pair",
        }

    def distance_to_segment(point: np.ndarray) -> float:
        """内部算法说明。"""
        fraction = float(
            np.clip(
                ((point - first_endpoint) @ segment) / segment_length_squared,
                0.0,
                1.0,
            )
        )
        closest = first_endpoint + fraction * segment
        return float(np.linalg.norm(point - closest))

    midpoint = 0.5 * (first_endpoint + second_endpoint)
    seed_midpoint = 0.5 * (first_center + second_center)
    center_axis /= seed_separation
    signed_centre_offset = float((midpoint - seed_midpoint) @ center_axis)
    minimum_clearance = max(
        0.0,
        0.5 * seed_separation - float(maximum_centre_offset_mm),
    )
    first_clearance = distance_to_segment(first_center)
    second_clearance = distance_to_segment(second_center)
    within_midpoint_corridor = bool(
        abs(signed_centre_offset) <= maximum_centre_offset_mm + 1.0e-9
    )
    clears_both_cores = bool(
        min(first_clearance, second_clearance)
        >= minimum_clearance - 1.0e-9
    )
    accepted = bool(within_midpoint_corridor and clears_both_cores)
    return {
        "available": True,
        "accepted": accepted,
        "method": "finite_separator_intercore_corridor_and_core_exclusion",
        "signed_centre_offset_mm": signed_centre_offset,
        "maximum_centre_offset_mm": float(maximum_centre_offset_mm),
        "minimum_required_core_clearance_mm": minimum_clearance,
        "first_core_clearance_mm": first_clearance,
        "second_core_clearance_mm": second_clearance,
        "within_midpoint_corridor": within_midpoint_corridor,
        "clears_both_cores": clears_both_cores,
        "reason": (
            None
            if accepted
            else (
                "separator_midpoint_outside_intercore_corridor"
                if not within_midpoint_corridor
                else "separator_enters_crown_core_protection_zone"
            )
        ),
    }


def _contour(region: np.ndarray, lr: np.ndarray, ap: np.ndarray) -> tuple[tuple[float, float], ...]:
    """算法说明。"""
    candidates = find_contours(region.astype(float), 0.5)
    if not candidates:
        return ()
    raw = max(candidates, key=len)
    if len(raw) > 400:
        raw = raw[np.linspace(0, len(raw) - 1, 400, dtype=int)]
    points = np.column_stack([
        np.interp(raw[:, 0], np.arange(len(lr)), lr),
        np.interp(raw[:, 1], np.arange(len(ap)), ap),
    ])
    return tuple((float(point[0]), float(point[1])) for point in points)


def _sample_height(
    maps: dict[str, object], center: np.ndarray
) -> tuple[float, float]:
    """算法说明。"""
    lr = np.asarray(maps["lr_centres"], dtype=float)
    ap = np.asarray(maps["ap_centres"], dtype=float)
    height = np.asarray(maps["top_height_mm"], dtype=float)
    row = int(np.argmin(np.abs(lr - center[0])))
    column = int(np.argmin(np.abs(ap - center[1])))
    if np.isfinite(height[row, column]):
        return float(height[row, column]), 0.0
    valid = np.argwhere(np.isfinite(height))
    if not len(valid):
        raise RuntimeError("projection contains no finite crown height")
    physical = np.column_stack([lr[valid[:, 0]], ap[valid[:, 1]]])
    distance = np.linalg.norm(physical - center, axis=1)
    nearest = int(np.argmin(distance))
    selected = valid[nearest]
    return float(height[tuple(selected)]), float(distance[nearest])


def choose_partition_map(
    maps_by_quantile: dict[float, dict[str, object]],
    assignments,
) -> tuple[float, dict[str, object]]:
    """算法说明。

Choose the highest scale that geometrically supports every matched seed.

    Merely being close to a foreground pixel is insufficient: at a high global
    height quantile a low posterior crown can be reduced to a tiny island while
    its multi-scale core remains valid.  Such an island cannot be used as the
    final physical tooth region.  Component area is therefore checked against
    a resolution-derived pixel safety limit before a seed counts as supported.
    This only selects the segmentation scale; it never deletes a core or changes
    the monotone FDI alignment.
    """

    matched = [item for item in assignments if item.center_lr_ap_mm is not None]
    ranked: list[tuple[int, float, float]] = []
    for quantile, maps in maps_by_quantile.items():
        mask = np.asarray(maps["silhouette"], dtype=bool)
        lr = np.asarray(maps["lr_centres"], dtype=float)
        ap = np.asarray(maps["ap_centres"], dtype=float)
        resolution = float(maps["resolution_mm"])
        distance = distance_transform_edt(~mask) * resolution
        components = label(mask, connectivity=2)
        component_pixels = np.bincount(components.ravel())
        # The final QA requires a meaningful two-dimensional region.  Thirty
        # pixels is the raster safety floor; the physical-area floor prevents
        # that requirement weakening at coarser perturbation resolutions.
        minimum_component_pixels = max(
            30,
            int(np.ceil(4.0 / max(resolution**2, 1.0e-12))),
        )
        supported = 0
        total_distance = 0.0
        for assignment in matched:
            row, column = _physical_to_index(assignment.center_lr_ap_mm, lr, ap)
            value = float(distance[row, column])
            if mask[row, column]:
                component_id = int(components[row, column])
            elif value <= 1.5:
                nearest_row, nearest_column = _nearest_mask_pixel(mask, row, column)
                component_id = int(components[nearest_row, nearest_column])
            else:
                component_id = 0
            component_is_nondegenerate = (
                component_id > 0
                and int(component_pixels[component_id]) >= minimum_component_pixels
            )
            supported += value <= 1.5 and component_is_nondegenerate
            total_distance += value
        ranked.append((supported, -total_distance, quantile))
    _, _, quantile = max(ranked)
    return float(quantile), maps_by_quantile[quantile]


def _confident_unassigned_pixels(
    *,
    component: np.ndarray,
    records: list[dict[str, object]],
    maps: dict[str, object],
    frame: ArchFrame,
    relief_quantile: float,
    seed_protection_scale: float,
    minimum_area_mm2: float,
) -> np.ndarray:
    """算法说明。 Release only boundary-connected, low-relief pixels far from every seed."""

    if relief_quantile <= 0.0 or "relative_crown_relief_score" not in maps:
        return np.zeros(component.shape, dtype=bool)
    score = np.asarray(maps["relative_crown_relief_score"], dtype=float)
    relief = np.asarray(maps.get("relative_crown_relief_mm"), dtype=float)
    valid = component & np.isfinite(score) & np.isfinite(relief)
    selected = score[valid]
    if len(selected) < 30:
        return np.zeros(component.shape, dtype=bool)
    low, high = np.quantile(selected, [0.05, 0.95])
    if high - low < 0.08:
        return np.zeros(component.shape, dtype=bool)
    threshold = float(np.quantile(selected, relief_quantile))
    candidate = valid & (score <= threshold + 1.0e-9)
    lr = np.asarray(maps["lr_centres"], dtype=float)
    ap = np.asarray(maps["ap_centres"], dtype=float)
    grid_lr, grid_ap = np.meshgrid(lr, ap, indexing="ij")
    protected = np.zeros(component.shape, dtype=bool)
    for record in records:
        assignment = record["assignment"]
        center = np.asarray(assignment.center_lr_ap_mm, dtype=float)
        local_scale = frame.scale_at_s(float(assignment.s_mm))
        distance = np.hypot(grid_lr - center[0], grid_ap - center[1])
        protected |= distance <= seed_protection_scale * local_scale
    candidate &= ~protected
    if not np.any(candidate):
        return candidate
    boundary = component & ~binary_erosion(component, iterations=1)
    candidate_components = label(candidate, connectivity=2)
    resolution = float(maps["resolution_mm"])
    minimum_pixels = max(1, int(np.ceil(minimum_area_mm2 / resolution**2)))
    released = np.zeros(component.shape, dtype=bool)
    for component_id in range(1, int(np.max(candidate_components)) + 1):
        region = candidate_components == component_id
        if np.count_nonzero(region) < minimum_pixels:
            continue
        if not np.any(binary_dilation(region, iterations=1) & boundary):
            continue
        released |= region
    return released


def segment_component_local_regions(
    *,
    alignment: AlignmentPath,
    frame: ArchFrame,
    maps: dict[str, object],
    boundary_smoothing_scale: float = 1.0,
    unassigned_relief_quantile: float = 0.08,
    unassigned_seed_protection_scale: float = 0.55,
    minimum_unassigned_area_mm2: float = 1.50,
    surface_valley_watershed_weight: float = 0.22,
    minimum_surface_valley_mean_support: float = 0.36,
    minimum_surface_valley_coverage: float = 0.32,
    multi_view_watershed_weight: float = 0.12,
    boundary_first_segmentation: bool = False,
    require_anatomical_split_evidence: bool = True,
) -> tuple[list[ToothRegion], SegmentationDiagnostics, np.ndarray]:
    """算法说明。"""
    matched = [item for item in alignment.assignments if item.center_lr_ap_mm is not None]
    if not matched:
        raise RuntimeError("alignment contains no detected tooth")
    mask = np.asarray(maps["silhouette"], dtype=bool)
    lr = np.asarray(maps["lr_centres"], dtype=float)
    ap = np.asarray(maps["ap_centres"], dtype=float)
    resolution = float(maps["resolution_mm"])
    components = label(mask, connectivity=2)
    component_count = int(np.max(components))
    seed_records: list[dict[str, object]] = []
    for region_index, assignment in enumerate(matched, start=1):
        row, column = _physical_to_index(assignment.center_lr_ap_mm, lr, ap)
        if not mask[row, column]:
            row, column = _nearest_mask_pixel(mask, row, column)
        component_id = int(components[row, column])
        seed_records.append({
            "region_index": region_index,
            "assignment": assignment,
            "row": row,
            "column": column,
            "component_id": component_id,
        })

    global_labels = np.zeros(mask.shape, dtype=np.int32)
    unassigned_mask = np.zeros(mask.shape, dtype=bool)
    finite_separator_count = 0
    fallback_count = 0
    boundary_methods: dict[int, str] = {}
    boundary_confidence: dict[int, float] = {}
    separator_records: list[dict[str, object]] = []
    separator_candidate_records: list[dict[str, object]] = []
    surface_valley_separator_records: list[dict[str, object]] = []
    unsupported_separator_records: list[dict[str, object]] = []
    boundary_topology_records: list[dict[str, object]] = []
    surface_valley_separator_count = 0
    seeded_components = sorted({int(item["component_id"]) for item in seed_records})
    for component_id in seeded_components:
        component = components == component_id
        records = [item for item in seed_records if item["component_id"] == component_id]
        released = _confident_unassigned_pixels(
            component=component,
            records=records,
            maps=maps,
            frame=frame,
            relief_quantile=unassigned_relief_quantile,
            seed_protection_scale=unassigned_seed_protection_scale,
            minimum_area_mm2=minimum_unassigned_area_mm2,
        )
        unassigned_mask |= released
        if len(records) == 1:
            region_index = int(records[0]["region_index"])
            global_labels[component & ~released] = region_index
            boundary_methods[region_index] = (
                "component_membership_with_gingiva_release"
                if np.any(released) else "connected_component_membership"
            )
            boundary_confidence[region_index] = 1.0
            continue

        records.sort(key=lambda item: float(item["assignment"].s_mm))
        local_maps = dict(maps)
        local_maps["silhouette"] = component
        seeds = [
            CrownSeed(
                instance_id=int(item["region_index"]),
                center_lr_ap_mm=tuple(item["assignment"].center_lr_ap_mm),
                initial_center_lr_ap_mm=tuple(item["assignment"].center_lr_ap_mm),
                core_pixel_count=1,
                refinement_distance_mm=0.0,
            )
            for item in records
        ]
        try:
            chords = find_shortest_concavity_chords(
                enhanced_maps=local_maps,
                ordered_seeds=seeds,
                forced_gap_pair_indices=set(),
            )
        except Exception:
            chords = []
        contact_evidence = np.asarray([
            float(chord.evidence_score)
            for chord in chords
            if chord.kind == "contact" and chord.evidence_score is not None
        ], dtype=float)
        component_evidence_median = (
            float(np.median(contact_evidence))
            if len(contact_evidence) else float("inf")
        )
        reliable = []
        for chord in chords:
            if (
                chord.kind != "contact"
                or chord.first_endpoint_lr_ap_mm is None
                or chord.second_endpoint_lr_ap_mm is None
                or chord.pair_index < 0
                or chord.pair_index + 1 >= len(records)
            ):
                continue
            first_center = np.asarray(
                records[chord.pair_index]["assignment"].center_lr_ap_mm,
                dtype=float,
            )
            second_center = np.asarray(
                records[chord.pair_index + 1]["assignment"].center_lr_ap_mm,
                dtype=float,
            )
            first_assignment = records[chord.pair_index]["assignment"]
            second_assignment = records[chord.pair_index + 1]["assignment"]
            if (
                first_assignment.kind == "split"
                and second_assignment.kind == "split"
                and first_assignment.hypothesis_id
                == second_assignment.hypothesis_id
            ):
                # Both seeds were generated from one broad physical track.
                # Admit Level 1 only when its measured contact evidence is at
                # least typical for this component; otherwise the stable local
                # Level-2 barrier separates the two subregions.
                split_contact_is_typical = (
                    chord.evidence_score is not None
                    and float(chord.evidence_score) >= component_evidence_median
                )
                if not split_contact_is_typical:
                    separator_candidate_records.append({
                        "component_id": component_id,
                        "pair_index": int(chord.pair_index),
                        "accepted": False,
                        "rejection_reason": "low_evidence_shared_split_uses_level_2",
                        "centre_offset_mm": chord.centre_offset_mm,
                        "evidence_score": chord.evidence_score,
                        "component_evidence_median": component_evidence_median,
                        "paired_concavity_score": (
                            chord.paired_concavity_score
                        ),
                        "paired_concavity_level": (
                            chord.paired_concavity_level
                        ),
                    })
                    continue
            seed_separation = float(np.linalg.norm(second_center - first_center))
            # A chord far from the midpoint is usually a different concavity
            # pair selected by pixelisation, not the anatomical contact between
            # these two seeds.  Route that pair to the Level-2 fused-edge
            # watershed rather than imposing an unstable hard barrier.
            first_s = float(first_assignment.s_mm)
            second_s = float(second_assignment.s_mm)
            local_scale = frame.scale_at_s(0.5 * (first_s + second_s))
            centre_offset = abs(float(chord.centre_offset_mm))
            maximum_centre_offset = min(
                0.35 * max(seed_separation, 1.0e-6),
                0.27 * local_scale,
            )
            direct_acceptance_offset = 0.20 * local_scale
            normalized_chord_length = (
                float(chord.chord_length_mm or 0.0)
                / max(local_scale, 1.0e-6)
            )
            typical_component_evidence = (
                chord.evidence_score is not None
                and float(chord.evidence_score) >= component_evidence_median
            )
            paired_concavity_level = str(
                chord.paired_concavity_level or "none"
            ).lower()
            basin_support = _independent_crown_basin_support(
                first_center_lr_ap_mm=first_center,
                second_center_lr_ap_mm=second_center,
                maps=maps,
                component=component,
                local_scale_mm=local_scale,
            )
            anatomical_contact_supported = (
                typical_component_evidence
                or paired_concavity_level in {"moderate", "strong"}
            )
            if require_anatomical_split_evidence:
                anatomical_contact_supported = bool(
                    anatomical_contact_supported
                    and basin_support.get("accepted")
                )
            boundary_3d_support = _finite_chord_3d_boundary_support(
                first_endpoint_lr_ap_mm=chord.first_endpoint_lr_ap_mm,
                second_endpoint_lr_ap_mm=chord.second_endpoint_lr_ap_mm,
                first_center_lr_ap_mm=first_center,
                second_center_lr_ap_mm=second_center,
                maps=maps,
                component=component,
                local_scale_mm=local_scale,
            )
            ridge_refinement = None
            if (
                anatomical_contact_supported
                and not bool(boundary_3d_support.get("accepted"))
            ):
                chord, ridge_refinement = _refine_finite_chord_to_3d_ridge(
                    chord=chord,
                    first_center_lr_ap_mm=first_center,
                    second_center_lr_ap_mm=second_center,
                    maps=maps,
                    component=component,
                    local_scale_mm=local_scale,
                    maximum_centre_offset_mm=maximum_centre_offset,
                )
                if ridge_refinement is not None:
                    centre_offset = abs(float(chord.centre_offset_mm))
                    normalized_chord_length = (
                        float(chord.chord_length_mm or 0.0)
                        / max(local_scale, 1.0e-6)
                    )
                    boundary_3d_support = ridge_refinement["support"]
            core_clearance = _finite_separator_core_clearance(
                first_endpoint_lr_ap_mm=chord.first_endpoint_lr_ap_mm,
                second_endpoint_lr_ap_mm=chord.second_endpoint_lr_ap_mm,
                first_center_lr_ap_mm=first_center,
                second_center_lr_ap_mm=second_center,
                maximum_centre_offset_mm=maximum_centre_offset,
            )
            shape_and_location_supported = (
                anatomical_contact_supported
                and bool(core_clearance.get("accepted"))
                and centre_offset <= maximum_centre_offset
                and (
                    centre_offset <= direct_acceptance_offset
                    or normalized_chord_length >= 0.60
                    or typical_component_evidence
                    or (
                        bool(boundary_3d_support.get("accepted"))
                        and normalized_chord_length >= 0.45
                    )
                )
            )
            accepted = bool(
                shape_and_location_supported
                and (
                    not require_anatomical_split_evidence
                    or boundary_3d_support.get("accepted")
                )
            )
            separator_candidate_records.append({
                "component_id": component_id,
                "pair_index": int(chord.pair_index),
                "accepted": accepted,
                "rejection_reason": (
                    None
                    if accepted
                    else (
                        "insufficient_anatomical_contact_support"
                        if not anatomical_contact_supported
                        else (
                            str(core_clearance.get("reason"))
                            if not bool(core_clearance.get("accepted"))
                            else (
                            "independent_3d_boundary_consensus_not_demonstrated"
                            if (
                                require_anatomical_split_evidence
                                and not bool(
                                    boundary_3d_support.get("accepted")
                                )
                            )
                            else "off_centre_chord_lacks_local_width"
                            )
                        )
                    )
                ),
                "centre_offset_mm": chord.centre_offset_mm,
                "centre_offset_limit_mm": maximum_centre_offset,
                "direct_acceptance_offset_mm": direct_acceptance_offset,
                "normalized_chord_length": normalized_chord_length,
                "evidence_score": chord.evidence_score,
                "component_evidence_median": component_evidence_median,
                "typical_component_evidence": typical_component_evidence,
                "anatomical_contact_supported": anatomical_contact_supported,
                "core_clearance": core_clearance,
                "independent_crown_basin_support": basin_support,
                "projected_3d_boundary_support": boundary_3d_support,
                "local_3d_ridge_refinement": ridge_refinement,
                "confidence_margin": chord.confidence_margin,
                "selection_method": chord.selection_method,
                "paired_concavity_score": chord.paired_concavity_score,
                "paired_concavity_level": chord.paired_concavity_level,
                "paired_concavity_facing_support": (
                    chord.paired_concavity_facing_support
                ),
                "paired_concavity_axial_alignment": (
                    chord.paired_concavity_axial_alignment
                ),
                "paired_concavity_crown_support": (
                    chord.paired_concavity_crown_support
                ),
            })
            if not accepted:
                continue
            reliable.append(chord)
        reliable, topology_records = _remove_endpoint_collisions(
            reliable,
            resolution=resolution,
            component_id=component_id,
        )
        boundary_topology_records.extend(topology_records)
        separator_records.extend({
            "component_id": component_id,
            "pair_index": int(chord.pair_index),
            "first_instance_id": int(chord.first_instance_id),
            "second_instance_id": int(chord.second_instance_id),
            "first_endpoint_lr_ap_mm": list(chord.first_endpoint_lr_ap_mm),
            "second_endpoint_lr_ap_mm": list(chord.second_endpoint_lr_ap_mm),
            "chord_length_mm": chord.chord_length_mm,
            "centre_offset_mm": chord.centre_offset_mm,
            "angle_offset_degrees": chord.angle_offset_degrees,
            "evidence_score": chord.evidence_score,
            "confidence_margin": chord.confidence_margin,
            "component_evidence_median": component_evidence_median,
            "selection_method": chord.selection_method,
            "endpoint_concavity_support": chord.endpoint_concavity_support,
            "paired_concavity_score": chord.paired_concavity_score,
            "paired_concavity_level": chord.paired_concavity_level,
            "paired_concavity_facing_support": (
                chord.paired_concavity_facing_support
            ),
            "paired_concavity_axial_alignment": (
                chord.paired_concavity_axial_alignment
            ),
            "paired_concavity_crown_support": (
                chord.paired_concavity_crown_support
            ),
        } for chord in reliable)
        reliable_pair_indices = {int(chord.pair_index) for chord in reliable}
        reliable_confidence_by_pair = {
            int(record["pair_index"]): float(np.clip(
                record.get("confidence_margin")
                if record.get("confidence_margin") is not None
                else 1.0,
                0.0,
                1.0,
            ))
            for record in separator_candidate_records
            if (
                int(record.get("component_id", -1)) == component_id
                and bool(record.get("accepted"))
                and int(record.get("pair_index", -1))
                in reliable_pair_indices
            )
        }
        unresolved_pair_indices = (
            set(range(len(records) - 1)) - reliable_pair_indices
        )
        barrier = _finite_barrier(reliable, mask.shape, lr, ap, component)
        valley_resolved_pair_indices: set[int] = set()
        for pair_index in sorted(unresolved_pair_indices):
            valley_barrier, valley_record = _surface_valley_barrier(
                records=records,
                pair_index=pair_index,
                maps=maps,
                frame=frame,
                component=component,
                minimum_mean_support=minimum_surface_valley_mean_support,
                minimum_coverage=minimum_surface_valley_coverage,
                require_independent_crown_basins=(
                    require_anatomical_split_evidence
                ),
            )
            surface_valley_separator_records.append(valley_record)
            if bool(valley_record["accepted"]):
                barrier |= valley_barrier
                valley_resolved_pair_indices.add(pair_index)
        fallback_pair_indices = (
            unresolved_pair_indices - valley_resolved_pair_indices
        )
        if boundary_first_segmentation:
            for pair_index in sorted(fallback_pair_indices):
                first_assignment = records[pair_index]["assignment"]
                second_assignment = records[pair_index + 1]["assignment"]
                unsupported_separator_records.append({
                    "component_id": int(component_id),
                    "pair_index": int(pair_index),
                    "first_FDI": int(first_assignment.fdi),
                    "second_FDI": int(second_assignment.fdi),
                    "first_hypothesis_id": first_assignment.hypothesis_id,
                    "second_hypothesis_id": second_assignment.hypothesis_id,
                    "reason": (
                        "no_contact_chord_surface_valley_or_multiview_separator"
                    ),
                    "formal_midpoint_barrier_created": False,
                })
        else:
            barrier |= _local_midpoint_barrier(
                records,
                fallback_pair_indices,
                lr,
                ap,
                component,
                resolution,
            )
        surface_valley_separator_count += len(valley_resolved_pair_indices)
        finite_separator_count += len(reliable) + len(valley_resolved_pair_indices)
        markers = np.zeros(mask.shape, dtype=np.int32)
        for marker_index, record in enumerate(records, start=1):
            row, column = _nearest_mask_pixel(
                component, int(record["row"]), int(record["column"])
            )
            markers[row, column] = marker_index
        fused_edge = np.asarray(maps["fused_edge"], dtype=float)
        sigma = max(0.35, 0.80 * boundary_smoothing_scale)
        elevation = gaussian_filter(fused_edge, sigma=sigma)
        if "surface_valley_score" in maps:
            valley_score = np.nan_to_num(
                np.asarray(maps["surface_valley_score"], dtype=float), nan=0.0
            )
            weight = float(np.clip(surface_valley_watershed_weight, 0.0, 0.5))
            elevation = (
                (1.0 - weight) * elevation
                + weight * gaussian_filter(valley_score, sigma=sigma)
            )
        if "multi_view_boundary_score" in maps:
            multi_view_score = np.clip(np.nan_to_num(
                np.asarray(maps["multi_view_boundary_score"], dtype=float),
                nan=0.0,
            ), 0.0, 1.0)
            multi_view_consistency = np.clip(np.nan_to_num(
                np.asarray(
                    maps.get(
                        "multi_view_consistency",
                        np.ones(component.shape, dtype=float),
                    ),
                    dtype=float,
                ),
                nan=0.0,
            ), 0.0, 1.0)
            view_consistent_boundary = (
                multi_view_score * np.sqrt(multi_view_consistency)
            )
            view_weight = float(np.clip(
                multi_view_watershed_weight, 0.0, 0.35
            ))
            elevation = (
                (1.0 - view_weight) * elevation
                + view_weight * gaussian_filter(
                    view_consistent_boundary, sigma=sigma
                )
            )
        elevation[barrier] = max(float(np.max(elevation)), 1.0) + 1.0
        local_labels = watershed(elevation, markers=markers, mask=component)
        local_labels[released] = 0
        used_fallback = bool(fallback_pair_indices)
        fallback_count += int(used_fallback)
        for marker_index, record in enumerate(records, start=1):
            region_index = int(record["region_index"])
            global_labels[local_labels == marker_index] = region_index
            adjacent_pair_indices = {
                pair_index
                for pair_index in (marker_index - 2, marker_index - 1)
                if 0 <= pair_index < len(records) - 1
            }
            local_fallback_pairs = (
                adjacent_pair_indices & fallback_pair_indices
            )
            local_valley_pairs = (
                adjacent_pair_indices & valley_resolved_pair_indices
            )
            if boundary_first_segmentation and local_fallback_pairs:
                method = "unsupported_anatomical_boundary_diagnostic_watershed"
                confidence = 0.0
            elif local_valley_pairs:
                method = "surface_curvature_valley_component_watershed"
                confidence = 0.90
            else:
                method = "finite_contact_chord_component_watershed"
                adjacent_reliable_confidence = [
                    reliable_confidence_by_pair[pair_index]
                    for pair_index in adjacent_pair_indices
                    if pair_index in reliable_confidence_by_pair
                ]
                confidence = (
                    min(adjacent_reliable_confidence)
                    if adjacent_reliable_confidence else 1.0
                )
            boundary_methods[region_index] = method
            boundary_confidence[region_index] = confidence

    regions: list[ToothRegion] = []
    for record in seed_records:
        region_index = int(record["region_index"])
        assignment = record["assignment"]
        region = global_labels == region_index
        rows, columns = np.nonzero(region)
        if not len(rows):
            raise RuntimeError(f"component-local region for FDI {assignment.fdi} is empty")
        centroid = np.asarray([
            float(np.mean(lr[rows])), float(np.mean(ap[columns]))
        ])
        interior = distance_transform_edt(region) * resolution
        radius = float(np.max(interior))
        interior_rows, interior_columns = np.nonzero(
            interior >= radius - 0.25 * resolution
        )
        interior_center = np.asarray([
            float(np.mean(lr[interior_rows])),
            float(np.mean(ap[interior_columns])),
        ])
        crown_height, lift_distance = _sample_height(maps, centroid)
        relief_map = np.asarray(
            maps.get("relative_crown_relief_mm", np.zeros(mask.shape)), dtype=float
        )
        relief_score_map = np.asarray(
            maps.get("relative_crown_relief_score", np.zeros(mask.shape)), dtype=float
        )
        region_relief = relief_map[region & np.isfinite(relief_map)]
        region_relief_score = relief_score_map[
            region & np.isfinite(relief_score_map)
        ]
        global_point = (
            frame.origin
            + centroid[0] * frame.e_lr
            + centroid[1] * frame.e_ap
            + crown_height * frame.e_occ
        )
        component_ids = tuple(sorted(
            int(value) for value in np.unique(components[region]) if int(value) > 0
        ))
        confidence = boundary_confidence[region_index]
        if lift_distance > 1.5:
            confidence *= 0.5
        regions.append(ToothRegion(
            fdi=int(assignment.fdi),
            region_id=region_index,
            pixel_count=int(len(rows)),
            area_mm2=float(len(rows) * resolution**2),
            area_centroid_lr_ap_mm=(float(centroid[0]), float(centroid[1])),
            interior_center_lr_ap_mm=(
                float(interior_center[0]), float(interior_center[1])
            ),
            maximum_interior_radius_mm=radius,
            contour_lr_ap_mm=_contour(region, lr, ap),
            component_ids=component_ids,
            boundary_method=boundary_methods[region_index],
            boundary_confidence=float(confidence),
            crown_height_mm=crown_height,
            crown_point_global_mm=tuple(float(value) for value in global_point),
            relative_relief_mean_mm=(
                float(np.mean(region_relief)) if len(region_relief) else 0.0
            ),
            relative_relief_p90_mm=(
                float(np.quantile(region_relief, 0.90)) if len(region_relief) else 0.0
            ),
            relative_relief_score=(
                float(np.mean(region_relief_score))
                if len(region_relief_score) else 0.0
            ),
        ))
    artifact_components = tuple(
        component_id for component_id in range(1, component_count + 1)
        if component_id not in seeded_components
    )
    assigned_fraction = float(np.count_nonzero(global_labels) / max(np.count_nonzero(mask), 1))
    unassigned_components = label(unassigned_mask, connectivity=2)
    unassigned_pixel_count = int(np.count_nonzero(unassigned_mask))
    diagnostics = SegmentationDiagnostics(
        component_count=component_count,
        seeded_component_count=len(seeded_components),
        artifact_component_ids=artifact_components,
        finite_separator_count=finite_separator_count,
        fallback_component_count=fallback_count,
        separator_component_local=True,
        assigned_pixel_fraction=assigned_fraction,
        separator_records=tuple(separator_records),
        separator_candidate_records=tuple(separator_candidate_records),
        gingiva_or_unassigned_label_enabled=True,
        unassigned_pixel_count=unassigned_pixel_count,
        unassigned_area_mm2=float(unassigned_pixel_count * resolution**2),
        unassigned_component_count=int(np.max(unassigned_components)),
        surface_valley_evidence_available=bool(
            "surface_valley_score" in maps
            and np.any(np.isfinite(np.asarray(maps["surface_valley_score"])))
        ),
        surface_valley_separator_count=surface_valley_separator_count,
        surface_valley_separator_records=tuple(surface_valley_separator_records),
        multi_view_boundary_evidence_available=bool(
            "multi_view_boundary_score" in maps
            and np.any(np.isfinite(
                np.asarray(maps["multi_view_boundary_score"])
            ))
        ),
        multi_view_boundary_fused_into_watershed=bool(
            "multi_view_boundary_score" in maps
            and multi_view_watershed_weight > 0.0
        ),
        boundary_first_segmentation=bool(boundary_first_segmentation),
        midpoint_fallback_disabled=bool(boundary_first_segmentation),
        unsupported_separator_records=tuple(unsupported_separator_records),
        boundary_topology_records=tuple(boundary_topology_records),
    )
    return regions, diagnostics, global_labels

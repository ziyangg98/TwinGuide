"""算法说明。 Multi-scale crown projection, raw core detection, and candidate tracking."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
from scipy.ndimage import distance_transform_edt, grey_opening, maximum_filter
from skimage.measure import label

from twin_guide.tooth_mapping.enhanced_projection import rasterise_crown_triangles

from .arch_coordinates import transform_mesh
from .models import ArchFrame, CoreObservation, CoreTrack, ToothFdiMappingNewProfile
from .surface_valleys import SurfaceValleyEvidence


EPS = 1.0e-9


def _robust_score(values: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """算法说明。"""
    result = np.zeros_like(values, dtype=float)
    selected = np.asarray(values, dtype=float)[mask & np.isfinite(values)]
    if not len(selected):
        return result
    low, high = np.quantile(selected, [0.05, 0.95])
    if high - low <= EPS:
        return result
    result[mask] = np.clip((values[mask] - low) / (high - low), 0.0, 1.0)
    return result


def add_relative_relief_fields(
    maps: dict[str, object], baseline_windows_mm: tuple[float, ...]
) -> dict[str, object]:
    """算法说明。

Attach a local gingival baseline and relative crown-relief channels.

    The baseline is the median of several physical-scale grayscale openings.
    This removes slow scan tilt and gingival-height drift while preserving
    crown-scale protrusions.  Existing projection fields remain untouched.
    """

    output = dict(maps)
    mask = np.asarray(maps["silhouette"], dtype=bool)
    height = np.asarray(maps["top_height_mm"], dtype=float)
    resolution = float(maps["resolution_mm"])
    valid = mask & np.isfinite(height)
    if not np.any(valid):
        shape = mask.shape
        output.update({
            "local_gingiva_baseline_mm": np.full(shape, np.nan),
            "relative_crown_relief_mm": np.full(shape, np.nan),
            "relative_crown_relief_score": np.zeros(shape, dtype=float),
        })
        return output
    # Nearest-value extension prevents the missing exterior from becoming an
    # artificial low basin during opening.  Results are masked again below.
    _, nearest = distance_transform_edt(~valid, return_indices=True)
    filled = height.copy()
    filled[~valid] = height[nearest[0][~valid], nearest[1][~valid]]
    baselines = []
    for window_mm in baseline_windows_mm:
        pixels = max(3, int(round(window_mm / resolution)))
        if pixels % 2 == 0:
            pixels += 1
        baselines.append(grey_opening(filled, size=(pixels, pixels), mode="nearest"))
    baseline = np.median(np.stack(baselines), axis=0)
    baseline = np.minimum(baseline, filled)
    relief = np.maximum(filled - baseline, 0.0)
    baseline[~mask] = np.nan
    relief[~mask] = np.nan
    output.update({
        "local_gingiva_baseline_mm": baseline,
        "relative_crown_relief_mm": relief,
        "relative_crown_relief_score": _robust_score(relief, mask),
        "relief_baseline_windows_mm": tuple(float(v) for v in baseline_windows_mm),
    })
    return output


def render_multiscale_maps(
    dental,
    frame: ArchFrame,
    profile: ToothFdiMappingNewProfile,
    surface_valleys: SurfaceValleyEvidence | None = None,
):
    """算法说明。"""
    vertices, normals = transform_mesh(dental, frame)
    faces = np.asarray(dental.faces)
    maps_by_quantile: dict[float, dict[str, object]] = {}
    for quantile in profile.height_quantiles:
        rendered = rasterise_crown_triangles(
            vertices_lr_ap_height=vertices,
            faces=faces,
            vertex_normals_lr_ap_occ=normals,
            height_floor_mm=float(np.quantile(vertices[:, 2], quantile)),
            resolution_mm=profile.projection_resolution_mm,
            vertex_scalar_fields=(
                {
                    "minimum_curvature_per_mm": (
                        surface_valleys.minimum_curvature_per_mm
                    ),
                    "surface_valley_score": surface_valleys.valley_score,
                }
                if surface_valleys is not None else None
            ),
        )
        if surface_valleys is not None:
            rendered["surface_valley_metadata"] = {
                "method": "multi_scale_vertex_normal_shape_operator",
                "valid_vertex_fraction": surface_valleys.valid_vertex_fraction,
                "normalization_scale_mm": (
                    surface_valleys.normalization_scale_mm
                ),
                "smoothing_iterations": surface_valleys.smoothing_iterations,
            }
        maps_by_quantile[quantile] = add_relative_relief_fields(
            rendered, profile.relief_baseline_windows_mm
        )
    return maps_by_quantile


def _core_geometry(
    maps: dict[str, object],
    frame: ArchFrame,
    center_row: int,
    center_column: int,
    s_mm: float,
    radius_mm: float,
    depth: np.ndarray,
) -> tuple[float, float, float, float]:
    """算法说明。 Measure an erosion-isolated core in arch-tangent coordinates."""

    mask = np.asarray(maps["silhouette"], dtype=bool)
    resolution = float(maps["resolution_mm"])
    erosion_depth = max(0.50, 0.30 * radius_mm)
    isolated = label(mask & (depth >= erosion_depth), connectivity=2)
    component_id = int(isolated[center_row, center_column])
    if component_id <= 0:
        rows = np.asarray([center_row])
        columns = np.asarray([center_column])
    else:
        rows, columns = np.nonzero(isolated == component_id)
    lr = np.asarray(maps["lr_centres"], dtype=float)
    ap = np.asarray(maps["ap_centres"], dtype=float)
    points = np.column_stack([lr[rows], ap[columns]])
    center = np.asarray([lr[center_row], ap[center_column]])
    tangent = frame.tangent_at_s(s_mm)
    transverse = np.asarray([-tangent[1], tangent[0]])
    relative = points - center
    diameter = max(2.0 * radius_mm, resolution)

    def span(direction: np.ndarray) -> float:
        """算法说明。"""
        coordinate = relative @ direction
        if len(coordinate) < 3:
            return diameter
        measured = float(np.quantile(coordinate, 0.98) - np.quantile(coordinate, 0.02))
        return float(np.clip(measured + 2.0 * erosion_depth, diameter, 2.75 * diameter))

    relief = np.asarray(maps.get("relative_crown_relief_mm"), dtype=float)
    relief_score = np.asarray(maps.get("relative_crown_relief_score"), dtype=float)
    selected_relief = relief[rows, columns]
    selected_score = relief_score[rows, columns]
    selected_relief = selected_relief[np.isfinite(selected_relief)]
    selected_score = selected_score[np.isfinite(selected_score)]
    crown_height = float(np.quantile(selected_relief, 0.90)) if len(selected_relief) else 0.0
    quality = float(np.quantile(selected_score, 0.75)) if len(selected_score) else 0.0
    return span(tangent), span(transverse), crown_height, quality


def _raw_core_observations(
    maps: dict[str, object],
    frame: ArchFrame,
    scale_index: int,
    quantile: float,
) -> list[CoreObservation]:
    """算法说明。"""
    mask = np.asarray(maps["silhouette"], dtype=bool)
    resolution = float(maps["resolution_mm"])
    depth = distance_transform_edt(mask) * resolution
    silhouette_components = label(mask, connectivity=2)
    component_pixel_counts = np.bincount(silhouette_components.ravel())
    largest_component_pixels = int(
        np.max(component_pixel_counts[1:], initial=1)
    )
    peak_window = max(3, int(round(1.6 / resolution)))
    if peak_window % 2 == 0:
        peak_window += 1
    peaks = mask & (depth >= max(0.75, 3.0 * resolution))
    peaks &= depth >= maximum_filter(depth, size=peak_window) - 1.0e-9
    components = label(peaks, connectivity=2)
    lr = np.asarray(maps["lr_centres"], dtype=float)
    ap = np.asarray(maps["ap_centres"], dtype=float)
    output: list[CoreObservation] = []
    for component_id in range(1, int(np.max(components)) + 1):
        rows, columns = np.nonzero(components == component_id)
        if not len(rows):
            continue
        weights = np.maximum(depth[rows, columns], 0.05) ** 2
        center = np.asarray([
            np.average(lr[rows], weights=weights),
            np.average(ap[columns], weights=weights),
        ])
        center_row = int(np.argmin(np.abs(lr - center[0])))
        center_column = int(np.argmin(np.abs(ap - center[1])))
        silhouette_component = int(
            silhouette_components[center_row, center_column]
        )
        component_area_mm2 = float(
            np.count_nonzero(silhouette_components == silhouette_component)
            * resolution**2
        )
        if silhouette_component == 0 or component_area_mm2 < 4.0:
            continue
        s_mm, u_mm = frame.project_lr_ap(center)
        radius = float(np.max(depth[rows, columns]))
        mesiodistal, buccolingual, relative_height, relief_quality = _core_geometry(
            maps,
            frame,
            center_row,
            center_column,
            s_mm,
            radius,
            depth,
        )
        radius_quality = float(np.clip(radius / 4.0, 0.0, 1.0))
        output.append(CoreObservation(
            scale_index=scale_index,
            height_quantile=quantile,
            center_lr_ap_mm=(float(center[0]), float(center[1])),
            s_mm=s_mm,
            u_mm=u_mm,
            interior_radius_mm=radius,
            # V2.1 records relative relief for gingiva release and transparent
            # QA, but deliberately leaves the proven V2 alignment likelihood
            # unchanged.  Promoting relief into the hypothesis cost requires a
            # larger labelled development set rather than a new hand-tuned mix.
            quality=radius_quality,
            mesiodistal_width_mm=mesiodistal,
            buccolingual_width_mm=buccolingual,
            relative_crown_height_mm=relative_height,
            relief_quality=relief_quality,
            projection_component_area_ratio=float(
                component_pixel_counts[silhouette_component]
                / max(largest_component_pixels, 1)
            ),
        ))
    return output


def _track_center(observations: list[CoreObservation]) -> tuple[np.ndarray, float, float]:
    """算法说明。"""
    weights = np.asarray([max(item.interior_radius_mm, 0.1) ** 2 for item in observations])
    centers = np.asarray([item.center_lr_ap_mm for item in observations])
    center = np.average(centers, axis=0, weights=weights)
    return center, float(np.average([item.s_mm for item in observations], weights=weights)), float(
        np.average([item.u_mm for item in observations], weights=weights)
    )


def detect_core_tracks(
    maps_by_quantile: dict[float, dict[str, object]],
    frame: ArchFrame,
    profile: ToothFdiMappingNewProfile,
) -> tuple[list[CoreTrack], ArchFrame, list[CoreObservation]]:
    """算法说明。"""
    all_observations: list[CoreObservation] = []
    for scale_index, quantile in enumerate(profile.height_quantiles):
        all_observations.extend(_raw_core_observations(
            maps_by_quantile[quantile], frame, scale_index, quantile
        ))
    # Start with high-depth observations, but prevent two observations from the
    # same scale being silently collapsed into one atomic track.
    grouped: list[list[CoreObservation]] = []
    for observation in sorted(
        all_observations,
        key=lambda item: (item.interior_radius_mm, item.quality),
        reverse=True,
    ):
        eligible: list[tuple[float, int]] = []
        for index, group in enumerate(grouped):
            if any(item.scale_index == observation.scale_index for item in group):
                continue
            center, s_mm, u_mm = _track_center(group)
            scale = max(
                2.0 * np.median([item.interior_radius_mm for item in group]),
                2.0 * observation.interior_radius_mm,
                3.5,
            )
            distance = np.hypot(observation.s_mm - s_mm, observation.u_mm - u_mm)
            if distance / scale <= 0.62:
                eligible.append((float(distance / scale), index))
        if eligible:
            grouped[min(eligible)[1]].append(observation)
        else:
            grouped.append([observation])

    raw_scales = [
        2.0 * np.median([item.interior_radius_mm for item in group])
        for group in grouped
    ]
    median_scale = float(np.median(raw_scales)) if raw_scales else 8.0
    lower, upper = 0.65 * median_scale, 1.35 * median_scale
    tracks: list[CoreTrack] = []
    for group in grouped:
        center, s_mm, u_mm = _track_center(group)
        support_scales = tuple(sorted({item.scale_index for item in group}))
        local_scale = float(np.clip(
            2.0 * np.median([item.interior_radius_mm for item in group]),
            lower,
            upper,
        ))
        persistence = len(support_scales) / len(profile.height_quantiles)
        quality = float(np.mean([item.quality for item in group]))
        mesiodistal = float(np.median([item.mesiodistal_width_mm for item in group]))
        buccolingual = float(np.median([item.buccolingual_width_mm for item in group]))
        relative_height = float(np.median([
            item.relative_crown_height_mm for item in group
        ]))
        relief_quality = float(np.mean([item.relief_quality for item in group]))
        component_area_ratio = float(np.median([
            item.projection_component_area_ratio for item in group
        ]))
        crownness = float(np.clip(0.65 * persistence + 0.35 * quality, 1.0e-4, 0.9999))
        # Geometry far outside the local arch corridor is an interference
        # component, not an atomic tooth candidate.  The threshold is scaled
        # by measured crown diameter rather than a fixed global millimetre cut.
        if abs(u_mm) > 1.35 * local_scale:
            continue
        tracks.append(CoreTrack(
            track_id=0,
            observations=tuple(sorted(group, key=lambda item: item.scale_index)),
            center_lr_ap_mm=(float(center[0]), float(center[1])),
            s_mm=s_mm,
            u_mm=u_mm,
            local_scale_mm=local_scale,
            persistence=persistence,
            crownness=crownness,
            support_scale_indices=support_scales,
            mesiodistal_width_mm=mesiodistal,
            buccolingual_width_mm=buccolingual,
            relative_crown_height_mm=relative_height,
            relief_quality=relief_quality,
            projection_component_area_ratio=component_area_ratio,
        ))
    tracks.sort(key=lambda item: item.s_mm)
    stable_reference = [
        item for item in tracks
        if item.persistence >= profile.minimum_track_persistence
        and item.relative_crown_height_mm > 0.0
        and item.relief_quality > 0.0
    ]
    reference_height = float(np.median([
        item.relative_crown_height_mm for item in stable_reference
    ])) if stable_reference else 0.0
    reference_quality = float(np.median([
        item.relief_quality for item in stable_reference
    ])) if stable_reference else 0.0
    if reference_height > 0.0 and reference_quality > 0.0:
        rescored = []
        for track in tracks:
            height_ratio = track.relative_crown_height_mm / reference_height
            quality_ratio = track.relief_quality / reference_quality
            if (
                height_ratio < profile.minimum_relative_crown_height_ratio
                and quality_ratio < profile.minimum_relative_relief_quality_ratio
                and track.projection_component_area_ratio
                < profile.maximum_low_relief_component_area_ratio
            ):
                support = float(np.clip(
                    (
                        height_ratio
                        / profile.minimum_relative_crown_height_ratio
                    )
                    * (
                        quality_ratio
                        / profile.minimum_relative_relief_quality_ratio
                    ),
                    0.05,
                    1.0,
                ))
            else:
                support = 1.0
            rescored.append(replace(
                track, relative_3d_tooth_support=support
            ))
        tracks = rescored
    tracks = [replace(track, track_id=index + 1) for index, track in enumerate(tracks)]
    if tracks:
        track_s = np.asarray([item.s_mm for item in tracks])
        track_scale = np.asarray([item.local_scale_mm for item in tracks])
        order = np.argsort(track_s)
        scale_field = np.interp(
            frame.curve_s,
            track_s[order],
            track_scale[order],
            left=median_scale,
            right=median_scale,
        )
        scale_field = np.clip(scale_field, lower, upper)
        frame = replace(frame, local_scale_mm=scale_field)
    return tracks, frame, all_observations

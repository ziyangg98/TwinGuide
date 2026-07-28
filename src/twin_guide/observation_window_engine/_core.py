"""观察窗内部实现。\n\nCut configured observation windows from an approved tooth-guide mapping.

The script consumes ``tooth_guide_mapping.json``.  It never re-identifies or
renumbers teeth.  Each cutter follows the saved local guide contour for the
configured height, extends 0.4 mm beyond a top-open boundary, and crosses only
the first ray-measured labial/buccal guide wall plus a small overcut margin.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from datetime import UTC, datetime
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/cut_mapped_observation_windows_mpl")
os.environ.setdefault("XDG_CACHE_HOME", "/tmp/cut_mapped_observation_windows_cache")

import matplotlib

matplotlib.use("Agg")
import itertools

import matplotlib.pyplot as plt
import numpy as np
import trimesh
from manifold3d import Manifold, Mesh

from twin_guide.tooth_mapping.controlled_mesh_repair import (
    ControlledVolumeRepairError,
    ensure_closed_volume,
)
from twin_guide.tooth_mapping.pipeline import load_mesh, unit

EPS = 1e-9


def boolean(operation: str, meshes: list[trimesh.Trimesh]) -> trimesh.Trimesh:
    """内部算法说明。"""
    function = getattr(trimesh.boolean, operation)
    result = function([mesh.copy() for mesh in meshes], engine="manifold", check_volume=True)
    if isinstance(result, trimesh.Scene):
        geometries = list(result.geometry.values())
        result = trimesh.util.concatenate(geometries) if geometries else trimesh.Trimesh()
    if result is None or not isinstance(result, trimesh.Trimesh):
        raise RuntimeError(f"Manifold {operation} returned no mesh")
    result.remove_unreferenced_vertices()
    if not result.is_empty and not result.is_volume:
        result.fix_normals(multibody=True)
    return result


def union_batched(meshes: list[trimesh.Trimesh], batch_size: int) -> trimesh.Trimesh:
    """内部算法说明。"""
    if not meshes:
        raise RuntimeError("cannot union an empty cutter list")
    current = list(meshes)
    while len(current) > 1:
        next_level = []
        for start in range(0, len(current), batch_size):
            batch = current[start:start + batch_size]
            next_level.append(batch[0] if len(batch) == 1 else boolean("union", batch))
        current = next_level
    return current[0]


def regularize_manifold(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """内部算法说明。\n\nRe-index a Manifold result to remove coincident seam vertices.

    A second Manifold import/export pass canonicalizes exact seam vertices and
    zero-area triangles.  This is important for STL, which discards indexed
    topology and later welds vertices by coordinate.
    """

    solid = Manifold(mesh=Mesh(
        vert_properties=np.asarray(mesh.vertices, dtype=np.float32),
        tri_verts=np.asarray(mesh.faces, dtype=np.uint32),
    ))
    if str(solid.status()) != "Error.NoError":
        raise RuntimeError(f"Manifold regularization failed: {solid.status()}")
    indexed = solid.to_mesh()
    result = trimesh.Trimesh(
        vertices=np.asarray(indexed.vert_properties),
        faces=np.asarray(indexed.tri_verts),
        process=False,
    )
    if not result.is_volume:
        raise RuntimeError("regularized Manifold result is not a valid volume")
    return result


def retain_positive_volume_components(
    mesh: trimesh.Trimesh,
    minimum_volume_mm3: float = 1e-6,
) -> trimesh.Trimesh:
    """内部算法说明。\n\nDrop zero-volume Boolean seam tetrahedra before STL export."""

    retained = [
        component
        for component in mesh.split(only_watertight=False)
        if component.is_volume and abs(float(component.volume)) >= minimum_volume_mm3
    ]
    if not retained:
        raise RuntimeError("Boolean result contains no positive-volume components")
    result = retained[0] if len(retained) == 1 else trimesh.util.concatenate(retained)
    if not result.is_volume:
        raise RuntimeError("retained Boolean components are not closed volumes")
    return result


def remove_submicron_degenerate_faces(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """内部算法说明。\n\nCollapse numerical sliver triangles while preserving a closed volume."""

    original = mesh.copy()
    if not np.any(original.area_faces < 1e-12):
        return original
    # Manifold exports float32 coordinates.  Try progressively coarser welding
    # scales and accept only a candidate that remains a closed volume and has
    # no submicron sliver faces.
    for digits in (7, 6, 5, 4, 3):
        candidate = original.copy()
        candidate.merge_vertices(digits_vertex=digits)
        candidate.update_faces(candidate.nondegenerate_faces(height=1e-7))
        candidate.remove_unreferenced_vertices()
        if candidate.is_volume and not np.any(candidate.area_faces < 1e-12):
            return candidate
    if original.is_volume:
        return original
    raise RuntimeError("submicron-degenerate cleanup could not preserve the result volume")


def validate_contour_window_samples(window: dict[str, object]) -> None:
    """内部算法说明。\n\nValidate saved profiles used by a contour-following cutter.

    Axis-sweep cutters derive fresh wall support from source-guide rays along
    the saved axis. Legacy contour samples are not inputs to that method and
    must not block an otherwise valid axis-sweep window.
    """

    samples = list(window.get("samples", []))
    if len(samples) < 2:
        raise RuntimeError(f"window {window['id']} has incomplete mapped profiles")
    mapped_indices = sorted(int(item["sample_index"]) for item in samples)
    failed_indices = sorted(
        int(item["sample_index"]) for item in window.get("failures", [])
    )
    internal_failures = [
        index for index in failed_indices
        if mapped_indices[0] < index < mapped_indices[-1]
    ]
    if (
        internal_failures
        or mapped_indices != list(range(mapped_indices[0], mapped_indices[-1] + 1))
    ):
        raise RuntimeError(
            f"window {window['id']} has internal unmapped sections: "
            f"{internal_failures}"
        )


def smooth_surface_normals(
    profiles: np.ndarray,
    outward_rows: np.ndarray,
    e_occ: np.ndarray,
) -> np.ndarray:
    """内部算法说明。"""
    along_arch = np.gradient(profiles, axis=0)
    along_contour = np.gradient(profiles, axis=1)
    normals = np.cross(along_contour, along_arch)
    for row in range(normals.shape[0]):
        preferred = unit(outward_rows[row] + e_occ)
        for column in range(normals.shape[1]):
            normal = normals[row, column]
            normal = preferred if np.linalg.norm(normal) <= EPS else unit(normal)
            if float(np.dot(normal, preferred)) < 0.0:
                normal = -normal
            normals[row, column] = normal
    padded = np.pad(normals, ((1, 1), (1, 1), (0, 0)), mode="edge")
    averaged = (
        4.0 * padded[1:-1, 1:-1]
        + padded[:-2, 1:-1] + padded[2:, 1:-1]
        + padded[1:-1, :-2] + padded[1:-1, 2:]
    ) / 8.0
    lengths = np.linalg.norm(averaged, axis=2)
    return averaged / np.maximum(lengths[:, :, None], EPS)


def adaptive_wall_thickness(
    source: trimesh.Trimesh,
    cutter_surface: np.ndarray,
    normals: np.ndarray,
    outward_margin_mm: float,
    maximum_wall_thickness_mm: float,
    ray_entry_tolerance_mm: float,
    requested_wall_overcut_mm: float,
    following_wall_safety_mm: float = 0.10,
    minimum_valid_fraction: float = 0.75,
) -> tuple[np.ndarray, np.ndarray, dict[str, float | int]]:
    """内部算法说明。\n\nMeasure only the first guide wall encountered behind each profile point.

    Rays start just outside the mapped labial/buccal surface and travel inward.
    The first two distinct intersections are the entry and exit of the local
    guide wall.  Invalid/tangent samples are filled from adjacent valid grid
    samples; a low valid fraction is a hard failure instead of falling back to
    an arbitrary deep cutter.
    """

    shape = cutter_surface.shape[:2]
    origins = (cutter_surface + outward_margin_mm * normals).reshape((-1, 3))
    directions = (-normals).reshape((-1, 3))
    locations, ray_indices, _ = source.ray.intersects_location(
        origins, directions, multiple_hits=True
    )
    grouped: list[list[float]] = [[] for _ in range(len(origins))]
    for location, ray_index in zip(locations, ray_indices, strict=False):
        index = int(ray_index)
        distance = float(np.dot(location - origins[index], directions[index]))
        if distance > 1e-5:
            grouped[index].append(distance)

    measured = np.full(len(origins), np.nan, dtype=float)
    applied_overcut = np.full(len(origins), np.nan, dtype=float)
    following_wall_clearances: list[float] = []
    remaining_clearances: list[float] = []
    curtailed_overcut_count = 0
    duplicate_tolerance_mm = 0.02
    for index, distances in enumerate(grouped):
        unique: list[float] = []
        for distance in sorted(distances):
            if not unique or distance - unique[-1] > duplicate_tolerance_mm:
                unique.append(distance)
        if len(unique) < 2:
            continue
        entry, exit_ = unique[0], unique[1]
        thickness = exit_ - entry
        if abs(entry - outward_margin_mm) > ray_entry_tolerance_mm:
            continue
        if not 0.20 <= thickness <= maximum_wall_thickness_mm:
            continue
        measured[index] = thickness
        local_overcut = requested_wall_overcut_mm
        if len(unique) >= 3:
            clearance = float(unique[2] - exit_)
            following_wall_clearances.append(clearance)
            if clearance <= following_wall_safety_mm + 0.05:
                measured[index] = np.nan
                continue
            local_overcut = min(
                requested_wall_overcut_mm,
                clearance - following_wall_safety_mm,
            )
            if local_overcut < requested_wall_overcut_mm - 1e-6:
                curtailed_overcut_count += 1
            remaining_clearances.append(clearance - local_overcut)
        applied_overcut[index] = local_overcut

    measured = measured.reshape(shape)
    applied_overcut = applied_overcut.reshape(shape)
    valid = np.isfinite(measured)
    valid_count = int(np.count_nonzero(valid))
    valid_fraction = float(valid_count / measured.size)
    if valid_fraction < minimum_valid_fraction:
        raise RuntimeError(
            "adaptive wall-thickness ray casting is incomplete "
            f"({valid_fraction:.1%} valid, need {minimum_valid_fraction:.1%})"
        )

    filled = measured.copy()
    # Propagate local medians into the small set of tangent/extension samples.
    for _ in range(sum(shape)):
        pending = np.argwhere(~np.isfinite(filled))
        if not len(pending):
            break
        updates: list[tuple[int, int, float]] = []
        for row, column in pending:
            neighbours = []
            for d_row, d_column in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                other_row, other_column = row + d_row, column + d_column
                if 0 <= other_row < shape[0] and 0 <= other_column < shape[1]:
                    value = filled[other_row, other_column]
                    if np.isfinite(value):
                        neighbours.append(float(value))
            if neighbours:
                updates.append((int(row), int(column), float(np.median(neighbours))))
        if not updates:
            break
        for row, column, value in updates:
            filled[row, column] = value
    if not np.all(np.isfinite(filled)):
        filled[~np.isfinite(filled)] = float(np.nanmedian(measured))

    filled_overcut = applied_overcut.copy()
    # Use the same local-neighbour propagation for overcut values whose wall
    # thickness ray was tangent or otherwise invalid.
    for _ in range(sum(shape)):
        pending = np.argwhere(~np.isfinite(filled_overcut))
        if not len(pending):
            break
        updates: list[tuple[int, int, float]] = []
        for row, column in pending:
            neighbours = []
            for d_row, d_column in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                other_row, other_column = row + d_row, column + d_column
                if 0 <= other_row < shape[0] and 0 <= other_column < shape[1]:
                    value = filled_overcut[other_row, other_column]
                    if np.isfinite(value):
                        neighbours.append(float(value))
            if neighbours:
                updates.append((int(row), int(column), float(np.min(neighbours))))
        if not updates:
            break
        for row, column, value in updates:
            filled_overcut[row, column] = value
    if not np.all(np.isfinite(filled_overcut)):
        filled_overcut[~np.isfinite(filled_overcut)] = float(np.nanmin(applied_overcut))

    valid_values = measured[valid]
    return filled, filled_overcut, {
        "ray_sample_count": int(measured.size),
        "valid_ray_sample_count": valid_count,
        "filled_ray_sample_count": int(measured.size - valid_count),
        "valid_ray_fraction": valid_fraction,
        "measured_wall_thickness_min_mm": float(np.min(valid_values)),
        "measured_wall_thickness_median_mm": float(np.median(valid_values)),
        "measured_wall_thickness_max_mm": float(np.max(valid_values)),
        "applied_wall_thickness_min_mm": float(np.min(filled)),
        "applied_wall_thickness_max_mm": float(np.max(filled)),
        "following_wall_ray_count": len(following_wall_clearances),
        "nearest_following_wall_clearance_mm": (
            float(np.min(following_wall_clearances))
            if following_wall_clearances else -1.0
        ),
        "requested_wall_overcut_mm": float(requested_wall_overcut_mm),
        "applied_wall_overcut_min_mm": float(np.min(filled_overcut)),
        "applied_wall_overcut_max_mm": float(np.max(filled_overcut)),
        "curtailed_overcut_ray_count": int(curtailed_overcut_count),
        "minimum_clearance_after_overcut_mm": (
            float(np.min(remaining_clearances)) if remaining_clearances else -1.0
        ),
    }


def structured_prism(outer: np.ndarray, inner: np.ndarray) -> trimesh.Trimesh:
    """内部算法说明。\n\nCreate one indexed closed prism from paired structured surface grids.

    This avoids Boolean-union seams between hundreds of convex cells.  Those
    seams can be harmless in indexed PLY but become coincident vertices after
    STL discards topology, producing a non-manifold welded STL.
    """

    rows, columns, _ = outer.shape
    layer_size = rows * columns
    vertices = np.vstack([outer.reshape((-1, 3)), inner.reshape((-1, 3))])

    def outer_index(row: int, column: int) -> int:
        """内部算法说明。"""
        return row * columns + column

    def inner_index(row: int, column: int) -> int:
        """内部算法说明。"""
        return layer_size + row * columns + column

    faces: list[list[int]] = []

    def quad(a: int, b: int, c: int, d: int, reverse: bool = False) -> None:
        """内部算法说明。"""
        if reverse:
            faces.extend([[a, c, b], [a, d, c]])
        else:
            faces.extend([[a, b, c], [a, c, d]])

    for row in range(rows - 1):
        for column in range(columns - 1):
            quad(
                outer_index(row, column), outer_index(row + 1, column),
                outer_index(row + 1, column + 1), outer_index(row, column + 1),
            )
            quad(
                inner_index(row, column), inner_index(row + 1, column),
                inner_index(row + 1, column + 1), inner_index(row, column + 1),
                reverse=True,
            )
    for column in range(columns - 1):
        quad(
            outer_index(0, column), outer_index(0, column + 1),
            inner_index(0, column + 1), inner_index(0, column),
        )
        quad(
            outer_index(rows - 1, column), inner_index(rows - 1, column),
            inner_index(rows - 1, column + 1), outer_index(rows - 1, column + 1),
        )
    for row in range(rows - 1):
        quad(
            outer_index(row, 0), inner_index(row, 0),
            inner_index(row + 1, 0), outer_index(row + 1, 0),
        )
        quad(
            outer_index(row, columns - 1), outer_index(row + 1, columns - 1),
            inner_index(row + 1, columns - 1), inner_index(row, columns - 1),
        )
    mesh = trimesh.Trimesh(vertices=vertices, faces=np.asarray(faces, dtype=np.int64), process=False)
    mesh.remove_unreferenced_vertices()
    if not mesh.is_volume:
        mesh.fix_normals(multibody=True)
    if not mesh.is_volume:
        raise RuntimeError("structured mapped-window cutter is not a valid closed volume")
    return mesh


def build_grid_cutter(
    source: trimesh.Trimesh,
    profiles: np.ndarray,
    outward_rows: np.ndarray,
    e_occ: np.ndarray,
    top_open: bool,
    top_extension_mm: float,
    side_extension_mm: float,
    outward_margin_mm: float,
    wall_overcut_mm: float,
    maximum_wall_thickness_mm: float,
    ray_entry_tolerance_mm: float,
    union_batch_size: int,
) -> tuple[trimesh.Trimesh, np.ndarray, dict[str, float | int]]:
    # Extend past both FDI interval ends.  Ending a cutter exactly on the guide
    # surface can leave distinct coincident vertices: indexed PLY remains
    # watertight, but STL welding then exposes a non-manifold tangency.
    """内部算法说明。"""
    start_direction = unit(np.mean(profiles[0] - profiles[1], axis=0))
    end_direction = unit(np.mean(profiles[-1] - profiles[-2], axis=0))
    start_row = profiles[:1] + side_extension_mm * start_direction[None, None, :]
    end_row = profiles[-1:] + side_extension_mm * end_direction[None, None, :]
    profiles = np.concatenate([start_row, profiles, end_row], axis=0)
    outward_rows = np.concatenate([outward_rows[:1], outward_rows, outward_rows[-1:]], axis=0)
    if top_open:
        extended_top = profiles[:, :1, :] + top_extension_mm * e_occ[None, None, :]
        cutter_surface = np.concatenate([extended_top, profiles], axis=1)
    else:
        cutter_surface = profiles
    normals = smooth_surface_normals(cutter_surface, outward_rows, e_occ)
    wall_thickness, applied_overcut, thickness_report = adaptive_wall_thickness(
        source, cutter_surface, normals, outward_margin_mm,
        maximum_wall_thickness_mm, ray_entry_tolerance_mm, wall_overcut_mm,
    )
    outer = cutter_surface + outward_margin_mm * normals
    inner = cutter_surface - (
        wall_thickness[:, :, None] + applied_overcut[:, :, None]
    ) * normals
    rows, columns, _ = cutter_surface.shape
    cells = []
    for row in range(rows - 1):
        for column in range(columns - 1):
            corners = np.vstack([
                outer[row, column], outer[row + 1, column],
                outer[row + 1, column + 1], outer[row, column + 1],
                inner[row, column], inner[row + 1, column],
                inner[row + 1, column + 1], inner[row, column + 1],
            ])
            cell = trimesh.convex.convex_hull(corners)
            if not cell.is_volume:
                raise RuntimeError(f"cutter cell ({row}, {column}) is not a closed volume")
            cells.append(cell)
    cutter = retain_positive_volume_components(
        regularize_manifold(union_batched(cells, union_batch_size))
    )
    return cutter, cutter_surface, thickness_report


def _fill_axis_sweep_grid(values: np.ndarray) -> np.ndarray:
    """内部算法说明。\n\nFill unsupported directions from adjacent, real exterior-wall hits."""

    filled = np.asarray(values, dtype=float).copy()
    for _ in range(sum(filled.shape)):
        pending = np.argwhere(~np.isfinite(filled))
        if not len(pending):
            break
        updates = []
        for row, column in pending:
            neighbours = []
            for d_row, d_column in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                other_row, other_column = row + d_row, column + d_column
                if (
                    0 <= other_row < filled.shape[0]
                    and 0 <= other_column < filled.shape[1]
                    and np.isfinite(filled[other_row, other_column])
                ):
                    neighbours.append(float(filled[other_row, other_column]))
            if neighbours:
                updates.append((int(row), int(column), float(np.median(neighbours))))
        if not updates:
            break
        for row, column, value in updates:
            filled[row, column] = value
    if not np.all(np.isfinite(filled)):
        raise RuntimeError("axis-sweep ray grid contains an unfillable gap")
    return filled


def axis_sweep_axis_points(
    definition: dict[str, object],
) -> tuple[np.ndarray, np.ndarray]:
    """内部算法说明。\n\nReturn semantic axis rows after optional local downward corrections."""

    axis_start = np.asarray(definition["axis_start_global_mm"], dtype=float)
    axis_end = np.asarray(definition["axis_end_global_mm"], dtype=float)
    row_count = int(definition["axis_section_count"])
    fractions = np.linspace(0.0, 1.0, row_count)
    points = np.asarray([
        (1.0 - fraction) * axis_start + fraction * axis_end
        for fraction in fractions
    ])
    additions = np.asarray(
        definition.get("local_axis_drop_additions_mm", [0.0] * row_count),
        dtype=float,
    )
    if additions.shape != (row_count,):
        raise RuntimeError(
            "local_axis_drop_additions_mm must contain one value per axis row"
        )
    if np.any(additions < 0.0):
        raise RuntimeError("local axis-drop additions must be non-negative")
    zero_direction = unit(np.asarray(
        definition["zero_degree_occlusal_direction_global"], dtype=float
    ))
    points = points - additions[:, None] * zero_direction[None, :]
    return points, additions


def build_axis_sweep_cutter(
    source: trimesh.Trimesh,
    definition: dict[str, object],
    side_extension_mm: float,
    radial_overcut_mm: float,
    following_wall_safety_mm: float,
    axis_core_overcut_mm: float,
    union_batch_size: int,
    minimum_valid_fraction: float = 0.75,
) -> tuple[trimesh.Trimesh, np.ndarray, dict[str, float | int]]:
    """内部算法说明。\n\nBuild a ruled sector cutter around one mapped common-height axis.

    Rays start on the semantic axis and travel through the requested angular
    sector.  Each ray stops just beyond the first guide wall encountered.  This
    keeps the regular angular boundary while avoiding an unbounded wedge that
    could reach a second arm of the U-shaped guide.
    """

    axis_start = np.asarray(definition["axis_start_global_mm"], dtype=float)
    axis_end = np.asarray(definition["axis_end_global_mm"], dtype=float)
    axis_direction = unit(axis_end - axis_start)
    zero_direction = unit(np.asarray(
        definition["zero_degree_occlusal_direction_global"], dtype=float
    ))
    exterior_direction = unit(np.asarray(
        definition["positive_90_degree_exterior_direction_global"], dtype=float
    ))
    row_count = int(definition["axis_section_count"])
    angle_count = int(definition["angle_section_count"])
    angles_deg = np.linspace(
        float(definition["minimum_angle_deg"]),
        float(definition["maximum_angle_deg"]),
        angle_count,
    )
    angles = np.deg2rad(angles_deg)
    directions = np.asarray([
        unit(math.cos(angle) * zero_direction + math.sin(angle) * exterior_direction)
        for angle in angles
    ])
    axis_points, local_drop_additions = axis_sweep_axis_points(definition)
    inside_axis = np.asarray(source.contains(axis_points), dtype=bool)
    radial_directions = np.tile(directions, (row_count, 1))
    repeated_axis = np.repeat(axis_points, angle_count, axis=0)
    source_center = np.asarray(source.bounding_box.centroid, dtype=float)
    ray_span_mm = float(
        2.0 * np.linalg.norm(source.extents)
        + np.max(np.linalg.norm(axis_points - source_center, axis=1))
        + 5.0
    )
    # Start beyond the complete guide and cast back toward the tooth-position
    # axis.  The first hit from this side is always the true exterior boundary;
    # an axis-origin ray is ambiguous whenever G1-G2 lies inside guide material.
    origins = repeated_axis + ray_span_mm * radial_directions
    if bool(np.any(source.contains(origins))):
        raise RuntimeError("axis-sweep external ray origins are not outside the guide")
    ray_directions = -radial_directions
    locations, ray_indices, _ = source.ray.intersects_location(
        origins, ray_directions, multiple_hits=True
    )
    grouped: list[list[float]] = [[] for _ in range(len(origins))]
    for location, ray_index in zip(locations, ray_indices, strict=False):
        index = int(ray_index)
        inward_distance = float(np.dot(
            location - origins[index], ray_directions[index]
        ))
        radial_distance = ray_span_mm - inward_distance
        if radial_distance > 1e-5:
            grouped[index].append(radial_distance)

    radii = np.full((row_count, angle_count), np.nan, dtype=float)
    applied_overcut = np.full_like(radii, np.nan)
    first_wall_thickness = np.full_like(radii, np.nan)
    multi_boundary_ray_count = 0
    duplicate_tolerance_mm = 0.02
    for index, distances in enumerate(grouped):
        row, column = divmod(index, angle_count)
        unique = []
        for distance in sorted(distances):
            if not unique or distance - unique[-1] > duplicate_tolerance_mm:
                unique.append(distance)
        if not unique:
            continue
        if len(unique) >= 3:
            multi_boundary_ray_count += 1
        exterior_boundary = unique[-1]
        exterior_wall_entry = unique[-2] if len(unique) >= 2 else 0.0
        radii[row, column] = exterior_boundary + radial_overcut_mm
        applied_overcut[row, column] = radial_overcut_mm
        first_wall_thickness[row, column] = (
            exterior_boundary - exterior_wall_entry
        )

    valid = np.isfinite(radii)
    valid_count = int(np.count_nonzero(valid))
    valid_fraction = float(valid_count / radii.size)
    row_has_a_wall_hit = np.any(valid, axis=1)
    dense_wall_coverage = bool(
        valid_fraction >= minimum_valid_fraction
        and np.all(row_has_a_wall_hit)
    )
    # A buccal-only or terminally clipped guide legitimately has no material
    # along some requested rays.  Permit interpolation only when the rows that
    # do meet the guide form one contiguous interval and each of those rows is
    # anchored by two consecutive measured hits at the exterior edge.  Empty
    # rows may therefore occur only beyond a guide end, never as an internal
    # hole.  This is a topological support condition, not a case threshold.
    supported_rows = np.flatnonzero(row_has_a_wall_hit)
    supported_rows_are_contiguous = bool(
        len(supported_rows)
        and np.array_equal(
            supported_rows,
            np.arange(supported_rows[0], supported_rows[-1] + 1),
        )
    )
    supported_exterior_anchors_complete = bool(
        angle_count >= 2
        and len(supported_rows) >= 2
        and np.all(valid[supported_rows, -1])
        and np.all(valid[supported_rows, -2])
    )
    terminally_clipped_wall_coverage = bool(
        supported_rows_are_contiguous
        and supported_exterior_anchors_complete
    )
    wall_intersection_support_complete = bool(
        dense_wall_coverage or terminally_clipped_wall_coverage
    )
    if not wall_intersection_support_complete:
        raise RuntimeError(
            "axis-sweep exterior-wall support is incomplete "
            f"({valid_fraction:.1%} rays valid; "
            f"dense coverage={dense_wall_coverage}, "
            f"contiguous rows={supported_rows_are_contiguous}, "
            f"supported-row exterior anchors="
            f"{supported_exterior_anchors_complete})"
        )
    filled_radii = _fill_axis_sweep_grid(radii)
    outer = axis_points[:, None, :] + filled_radii[:, :, None] * directions[None, :, :]
    middle_angle = 0.5 * (angles[0] + angles[-1])
    inward_bisector = unit(
        math.cos(middle_angle) * zero_direction
        + math.sin(middle_angle) * exterior_direction
    )
    # G1/G2 lie at the tooth positions, normally inside the guide cavity.  A
    # small extension behind the semantic axis prevents a zero-thickness apex;
    # guide penetration itself is determined only by the outward sector rays.
    core = axis_points - axis_core_overcut_mm * inward_bisector[None, :]

    start_shift = -side_extension_mm * axis_direction
    end_shift = side_extension_mm * axis_direction
    outer = np.concatenate([
        outer[:1] + start_shift,
        outer,
        outer[-1:] + end_shift,
    ], axis=0)
    core = np.concatenate([
        core[:1] + start_shift,
        core,
        core[-1:] + end_shift,
    ], axis=0)
    cells = []
    for row in range(len(core) - 1):
        for column in range(angle_count - 1):
            corners = np.vstack([
                core[row], core[row + 1],
                outer[row, column], outer[row, column + 1],
                outer[row + 1, column], outer[row + 1, column + 1],
            ])
            cell = trimesh.convex.convex_hull(corners)
            if not cell.is_volume:
                raise RuntimeError(
                    f"axis-sweep cutter cell ({row}, {column}) is not a closed volume"
                )
            cells.append(cell)
    cutter = retain_positive_volume_components(
        regularize_manifold(union_batched(cells, union_batch_size))
    )
    valid_thickness = first_wall_thickness[np.isfinite(first_wall_thickness)]
    valid_overcut = applied_overcut[np.isfinite(applied_overcut)]
    return cutter, outer, {
        "ray_sample_count": int(radii.size),
        "valid_ray_sample_count": valid_count,
        "filled_ray_sample_count": int(radii.size - valid_count),
        "valid_ray_fraction": valid_fraction,
        "wall_intersection_support_complete": wall_intersection_support_complete,
        "wall_intersection_coverage_mode": (
            "dense"
            if dense_wall_coverage
            else (
                "terminal_clip_exterior_anchor_interpolation"
                if len(supported_rows) < row_count
                else "exterior_anchor_interpolation"
            )
        ),
        "exterior_anchor_row_count": int(np.count_nonzero(
            valid[:, -1] & valid[:, -2]
        )),
        "supported_axis_row_count": len(supported_rows),
        "terminal_uncovered_axis_row_count": int(row_count - len(supported_rows)),
        "supported_axis_rows_are_contiguous": supported_rows_are_contiguous,
        "axis_origin_inside_guide_fraction": float(np.mean(inside_axis)),
        "external_ray_origin_distance_mm": ray_span_mm,
        "multi_boundary_ray_count": int(multi_boundary_ray_count),
        "measured_wall_thickness_min_mm": float(np.min(valid_thickness)),
        "measured_wall_thickness_median_mm": float(np.median(valid_thickness)),
        "measured_wall_thickness_max_mm": float(np.max(valid_thickness)),
        "applied_wall_thickness_min_mm": float(np.min(filled_radii)),
        "applied_wall_thickness_max_mm": float(np.max(filled_radii)),
        "following_wall_ray_count": 0,
        "nearest_following_wall_clearance_mm": -1.0,
        "requested_wall_overcut_mm": float(radial_overcut_mm),
        "applied_wall_overcut_min_mm": float(np.min(valid_overcut)),
        "applied_wall_overcut_max_mm": float(np.max(valid_overcut)),
        "curtailed_overcut_ray_count": 0,
        "minimum_clearance_after_overcut_mm": -1.0,
        "axis_core_overcut_mm": float(axis_core_overcut_mm),
        "local_axis_drop_addition_max_mm": float(np.max(local_drop_additions)),
        "locally_corrected_axis_row_count": int(np.count_nonzero(
            local_drop_additions > 1e-9
        )),
    }


def _nearest_ray_hit_distances(
    mesh: trimesh.Trimesh,
    origins: np.ndarray,
    directions: np.ndarray,
) -> np.ndarray:
    """内部算法说明。"""
    distances = np.full(len(origins), np.inf, dtype=float)
    locations, ray_indices, _ = mesh.ray.intersects_location(
        origins, directions, multiple_hits=True
    )
    for location, ray_index in zip(locations, ray_indices, strict=False):
        index = int(ray_index)
        distance = float(np.dot(location - origins[index], directions[index]))
        if distance > 1e-5:
            distances[index] = min(distances[index], distance)
    return distances


def axis_sweep_tooth_visibility(
    result_guide: trimesh.Trimesh,
    dental: trimesh.Trimesh,
    definition: dict[str, object],
    outer_surface: np.ndarray,
) -> dict[str, float | int]:
    """内部算法说明。\n\nCheck that exterior sight rays reach dental before the cut guide."""

    zero_direction = unit(np.asarray(
        definition["zero_degree_occlusal_direction_global"], dtype=float
    ))
    exterior_direction = unit(np.asarray(
        definition["positive_90_degree_exterior_direction_global"], dtype=float
    ))
    angle_count = int(definition["angle_section_count"])
    angles = np.deg2rad(np.linspace(
        float(definition["minimum_angle_deg"]),
        float(definition["maximum_angle_deg"]),
        angle_count,
    ))
    radial_directions = np.asarray([
        unit(math.cos(angle) * zero_direction + math.sin(angle) * exterior_direction)
        for angle in angles
    ])
    # The first and last surface rows are Boolean end extensions.  Visibility is
    # assessed only across the semantic FDI-to-FDI axis interval.
    surface = np.asarray(outer_surface[1:-1], dtype=float)
    directions = -np.tile(radial_directions, (len(surface), 1))
    origins = (
        surface + 0.5 * radial_directions[None, :, :]
    ).reshape((-1, 3))
    guide_distance = _nearest_ray_hit_distances(result_guide, origins, directions)
    dental_distance = _nearest_ray_hit_distances(dental, origins, directions)
    axis_points, _ = axis_sweep_axis_points(definition)
    if len(axis_points) != len(surface):
        raise RuntimeError("axis-sweep visibility rows do not match semantic axis rows")
    repeated_axis = np.repeat(axis_points, angle_count, axis=0)
    corridor_length = np.einsum(
        "ij,ij->i", origins - repeated_axis, -directions
    )
    corridor_clear = (
        ~np.isfinite(guide_distance)
        | (guide_distance + 0.05 >= corridor_length)
    )
    corridor_clear_grid = corridor_clear.reshape((len(surface), angle_count))
    clear_fraction_by_row = np.mean(corridor_clear_grid, axis=1)
    visible = np.isfinite(dental_distance) & (
        dental_distance + 0.05 < guide_distance
    )
    visible_grid = visible.reshape((len(surface), angle_count))
    visible_rows = np.any(visible_grid, axis=1)
    finite_dental = dental_distance[np.isfinite(dental_distance)]
    return {
        "visibility_ray_count": len(origins),
        "dental_hit_ray_count": int(np.count_nonzero(np.isfinite(dental_distance))),
        "visible_dental_ray_count": int(np.count_nonzero(visible)),
        "visible_dental_ray_fraction": float(np.mean(visible)),
        "axis_row_count": len(surface),
        "axis_rows_with_visible_dental_count": int(np.count_nonzero(visible_rows)),
        "axis_rows_with_visible_dental_fraction": float(np.mean(visible_rows)),
        "visible_dental_by_axis_row": [bool(value) for value in visible_rows],
        "axis_rows_without_visible_dental": [
            int(index) for index in np.flatnonzero(~visible_rows)
        ],
        "clear_axis_corridor_ray_count": int(np.count_nonzero(corridor_clear)),
        "clear_axis_corridor_ray_fraction": float(np.mean(corridor_clear)),
        "minimum_clear_axis_corridor_fraction_per_row": float(
            np.min(clear_fraction_by_row)
        ),
        "clear_axis_corridor_fraction_by_row": [
            float(value) for value in clear_fraction_by_row
        ],
        "nearest_dental_hit_distance_mm": (
            float(np.min(finite_dental)) if len(finite_dental) else -1.0
        ),
    }


def sample_vertices(mesh: trimesh.Trimesh, count: int) -> np.ndarray:
    """内部算法说明。"""
    if len(mesh.vertices) <= count:
        return np.asarray(mesh.vertices)
    indices = np.linspace(0, len(mesh.vertices) - 1, count, dtype=int)
    return np.asarray(mesh.vertices)[indices]


def colored(mesh: trimesh.Trimesh, rgba: tuple[int, int, int, int]) -> trimesh.Trimesh:
    """内部算法说明。"""
    result = mesh.copy()
    result.visual.face_colors = np.tile(np.asarray(rgba, dtype=np.uint8), (len(result.faces), 1))
    return result


def render_preview(
    path: Path,
    source: trimesh.Trimesh,
    result: trimesh.Trimesh,
    cutter: trimesh.Trimesh,
    surfaces: dict[str, np.ndarray],
) -> None:
    """内部算法说明。"""
    source_points = sample_vertices(source, 42_000)
    result_points = sample_vertices(result, 58_000)
    cutter_points = sample_vertices(cutter, 30_000)
    figure = plt.figure(figsize=(16, 11), constrained_layout=True)
    for index, (title, first, second) in enumerate(
        (("X-Y", 0, 1), ("X-Z", 0, 2), ("Y-Z", 1, 2)), start=1
    ):
        axis = figure.add_subplot(2, 2, index)
        axis.scatter(source_points[:, first], source_points[:, second], s=0.06, c="#94a3b8", alpha=0.05)
        axis.scatter(result_points[:, first], result_points[:, second], s=0.08, c="#c6a66b", alpha=0.24)
        axis.scatter(cutter_points[:, first], cutter_points[:, second], s=0.06, c="#dc2626", alpha=0.06)
        for name, surface in surfaces.items():
            axis.plot(surface[:, 0, first], surface[:, 0, second], linewidth=2.0, label=f"{name} top")
            axis.plot(surface[:, -1, first], surface[:, -1, second], linewidth=1.2, linestyle="--")
        axis.set_aspect("equal", adjustable="box")
        axis.set_title(f"{title} projection")
        axis.set_xlabel("XYZ"[first] + " (mm)")
        axis.set_ylabel("XYZ"[second] + " (mm)")
        axis.legend(fontsize=7)
    axis = figure.add_subplot(2, 2, 4)
    axis.axis("off")
    axis.text(
        0.02, 0.96,
        "Mapped FDI observation-window cut\n"
        "gray: source guide\n"
        "gold: Boolean result\n"
        "red: cutter\n"
        "solid trajectories: top-open extension\n"
        "dashed trajectories: local-contour bottom boundary",
        va="top", fontsize=13,
    )
    figure.savefig(path, dpi=220)
    plt.close(figure)


def _run_once(args: argparse.Namespace) -> dict[str, object]:
    """内部算法说明。"""
    case_yaml = args.case.resolve()
    case_dir = case_yaml.parent
    mapping_path = (
        args.mapping_report.resolve()
        if args.mapping_report
        else case_dir / "输出/tooth_guide_mapping/tooth_guide_mapping.json"
    )
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    if mapping.get("status") != "tooth_guide_mapping_complete" or not all(mapping.get("QA", {}).values()):
        raise RuntimeError("tooth-guide mapping has not passed all QA gates")
    source_path = args.source.resolve() if args.source else Path(mapping["sources"]["guide"])
    source = load_mesh(source_path)
    output_dir = args.output_dir.resolve() if args.output_dir else case_dir / "输出/mapped_observation_windows"
    output_dir.mkdir(parents=True, exist_ok=True)
    repair_report_path = output_dir / "source_guide_controlled_repair.json"
    try:
        repair = ensure_closed_volume(source)
    except ControlledVolumeRepairError as error:
        repair_report_path.write_text(
            json.dumps(error.report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        raise RuntimeError(f"source guide controlled repair failed: {error}") from error
    source = repair.mesh
    source_repaired_path = None
    if repair.report["applied"]:
        source_repaired_path = output_dir / "source_guide_controlled_repaired.ply"
        source.export(source_repaired_path)
    repair_report = dict(repair.report)
    repair_report["report_json"] = str(repair_report_path)
    repair_report["repaired_mesh_ply"] = (
        str(source_repaired_path) if source_repaired_path else None
    )
    repair_report_path.write_text(
        json.dumps(repair_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    e_occ = unit(np.asarray(mapping["coordinate_system"]["e_occ"], dtype=float))

    selected_windows = mapping["observation_windows"]
    if args.window_id:
        requested_ids = set(args.window_id)
        selected_windows = [item for item in selected_windows if item["id"] in requested_ids]
        missing_ids = sorted(requested_ids - {item["id"] for item in selected_windows})
        if missing_ids:
            raise RuntimeError(f"requested observation-window IDs were not found: {missing_ids}")
    if not selected_windows:
        raise RuntimeError("no observation windows were selected")

    window_wall_overcut = {}
    for item in args.window_wall_overcut_mm or []:
        window_id, separator, value_text = item.partition("=")
        if not separator or not window_id:
            raise RuntimeError(
                "--window-wall-overcut-mm must use WINDOW_ID=MM syntax"
            )
        try:
            value = float(value_text)
        except ValueError as exc:
            raise RuntimeError(
                f"invalid wall overcut for window {window_id!r}: {value_text!r}"
            ) from exc
        if value < 0.0:
            raise RuntimeError("window wall overcut must be non-negative")
        window_wall_overcut[window_id] = value
    unknown_override_ids = sorted(
        set(window_wall_overcut) - {item["id"] for item in selected_windows}
    )
    if unknown_override_ids:
        raise RuntimeError(
            f"wall-overcut overrides refer to unselected windows: {unknown_override_ids}"
        )

    cutters = {}
    surfaces = {}
    window_reports = []
    for window in selected_windows:
        opening_geometry = str(window.get("opening_geometry", "contour_following"))
        failed_indices = sorted(
            int(item["sample_index"]) for item in window.get("failures", [])
        )
        wall_overcut_mm = window_wall_overcut.get(window["id"], args.wall_overcut_mm)
        if opening_geometry == "axis_sweep":
            definition = window.get("axis_sweep")
            if not isinstance(definition, dict):
                raise RuntimeError(
                    f"window {window['id']} has no mapped axis-sweep definition"
                )
            cutter, surface, wall_report = build_axis_sweep_cutter(
                source,
                definition,
                args.side_extension_mm,
                wall_overcut_mm,
                args.following_wall_safety_mm,
                args.axis_core_overcut_mm,
                args.union_batch_size,
            )
            profile_count = int(definition["axis_section_count"])
            profile_point_count = int(definition["angle_section_count"])
        elif opening_geometry == "contour_following":
            validate_contour_window_samples(window)
            profiles = np.asarray([
                item["window_profile_global_mm"] for item in window["samples"]
            ], dtype=float)
            outward = np.asarray([
                item["local_outward_global"] for item in window["samples"]
            ], dtype=float)
            cutter, surface, wall_report = build_grid_cutter(
                source, profiles, outward, e_occ,
                bool(window["top_open"]), args.top_extension_mm, args.side_extension_mm,
                args.outward_margin_mm, wall_overcut_mm,
                args.maximum_wall_thickness_mm, args.ray_entry_tolerance_mm,
                args.union_batch_size,
            )
            profile_count = len(profiles)
            profile_point_count = int(profiles.shape[1])
        else:
            raise RuntimeError(
                f"window {window['id']} has unsupported opening geometry "
                f"{opening_geometry!r}"
            )
        cutters[window["id"]] = cutter
        surfaces[window["id"]] = surface
        cutter_path = output_dir / f"window_{window['id']}_cutter.ply"
        cutter.export(cutter_path)
        window_reports.append({
            "id": window["id"],
            "opening_geometry": opening_geometry,
            "start_fdi": window["start_fdi"],
            "end_fdi": window["end_fdi"],
            "extent_mode": window["extent_mode"],
            "height_mm": window["height_mm"],
            "top_open": window["top_open"],
            "section_count": profile_count,
            "profile_point_count": profile_point_count,
            "guide_coverage_clipped_edge_section_count": len(failed_indices),
            "cutter_volume_mm3": float(abs(cutter.volume)),
            "adaptive_wall_thickness": wall_report,
            "axis_sweep": window.get("axis_sweep"),
            "cutter_ply": str(cutter_path),
        })

    combined = retain_positive_volume_components(
        regularize_manifold(union_batched(list(cutters.values()), args.union_batch_size))
    )
    result_all = regularize_manifold(boolean("difference", [source, combined]))
    has_local_axis_correction = any(
        np.any(np.asarray(
            window.get("axis_sweep", {}).get(
                "local_axis_drop_additions_mm", []
            ),
            dtype=float,
        ) > 1e-9)
        for window in selected_windows
        if window.get("opening_geometry") == "axis_sweep"
    )
    if has_local_axis_correction:
        # A locally curved ruled cutter has many nearly coplanar cell seams.
        # Repeating the same difference removes numerical overlap slivers; it
        # does not enlarge the requested cutter or alter unaffected rows.
        result_all = regularize_manifold(
            boolean("difference", [result_all, combined])
        )
    if result_all.is_empty:
        raise RuntimeError("observation-window difference removed the entire guide")
    source_components = len(source.split(only_watertight=False))
    result_components = sorted(
        result_all.split(only_watertight=False), key=lambda item: abs(float(item.volume)), reverse=True
    )
    significant = [item for item in result_components if abs(float(item.volume)) >= args.fragment_volume_tolerance_mm3]
    discarded = [item for item in result_components if abs(float(item.volume)) < args.fragment_volume_tolerance_mm3]
    discarded_fragment_volume = float(sum(abs(float(item.volume)) for item in discarded))
    result = significant[0] if len(significant) == 1 else trimesh.util.concatenate(significant)
    result.remove_unreferenced_vertices()
    if not result.is_volume:
        result.fix_normals(multibody=True)
    result = remove_submicron_degenerate_faces(result)

    visibility_reports = {}
    axis_window_ids = {
        item["id"] for item in window_reports
        if item["opening_geometry"] == "axis_sweep"
    }
    if axis_window_ids:
        dental_path = Path(mapping["sources"]["dental"])
        dental = load_mesh(dental_path)
        for mapped_window, window_report in zip(selected_windows, window_reports, strict=False):
            if window_report["id"] not in axis_window_ids:
                continue
            visibility = axis_sweep_tooth_visibility(
                result,
                dental,
                mapped_window["axis_sweep"],
                surfaces[window_report["id"]],
            )
            visibility_reports[window_report["id"]] = visibility
            window_report["tooth_visibility"] = visibility

    top_crest_clearances = {}
    axis_clearances = {}
    for mapped_window, window_report in zip(selected_windows, window_reports, strict=False):
        if window_report["opening_geometry"] == "axis_sweep":
            definition = mapped_window["axis_sweep"]
            axis_points, local_drop_additions = axis_sweep_axis_points(definition)
            dense_axis = []
            dense_row_coordinates = []
            samples_per_interval = 10
            for row, (start, end) in enumerate(itertools.pairwise(axis_points)):
                for fraction in np.linspace(
                    0.0, 1.0, samples_per_interval, endpoint=False
                ):
                    dense_axis.append((1.0 - fraction) * start + fraction * end)
                    dense_row_coordinates.append(row + float(fraction))
            dense_axis.append(axis_points[-1])
            dense_row_coordinates.append(float(len(axis_points) - 1))
            dense_axis = np.asarray(dense_axis, dtype=float)
            _, distances, _ = result.nearest.on_surface(dense_axis)
            minimum_clearance = float(np.min(distances))
            clearance_threshold = max(0.15, args.axis_core_overcut_mm - 0.15)
            failed_dense = np.flatnonzero(distances < clearance_threshold)
            failed_rows = sorted({
                int(np.clip(
                    round(dense_row_coordinates[int(index)]),
                    0,
                    len(axis_points) - 1,
                ))
                for index in failed_dense
            })
            _, row_clearances, _ = result.nearest.on_surface(axis_points)
            axis_clearances[window_report["id"]] = minimum_clearance
            window_report["minimum_removed_axis_clearance_mm"] = minimum_clearance
            window_report["axis_clearance_threshold_mm"] = float(clearance_threshold)
            window_report["axis_clearance_by_row_mm"] = [
                float(value) for value in row_clearances
            ]
            window_report["axis_rows_below_clearance_threshold"] = failed_rows
            window_report["local_axis_drop_additions_mm"] = [
                float(value) for value in local_drop_additions
            ]
            continue
        crest = np.asarray([
            item["true_top_global_mm"] for item in mapped_window["samples"]
        ], dtype=float)
        dense_crest = []
        for start, end in itertools.pairwise(crest):
            dense_crest.extend(
                (1.0 - fraction) * start + fraction * end
                for fraction in np.linspace(0.0, 1.0, 6, endpoint=False)
            )
        dense_crest.append(crest[-1])
        _, distances, _ = result.nearest.on_surface(np.asarray(dense_crest))
        minimum_clearance = float(np.min(distances))
        top_crest_clearances[window_report["id"]] = minimum_clearance
        window_report["minimum_removed_top_crest_clearance_mm"] = minimum_clearance

    residual = boolean("intersection", [result, combined])
    residual_volume = 0.0 if residual.is_empty else float(abs(residual.volume))
    intersection = boolean("intersection", [source, combined])
    intersection_volume = 0.0 if intersection.is_empty else float(abs(intersection.volume))
    removed_volume = float(abs(source.volume) - abs(result.volume))
    boolean_result_volume = float(sum(abs(float(item.volume)) for item in result_components))
    boolean_removed_volume = float(abs(source.volume) - boolean_result_volume)
    identity_error = abs(boolean_removed_volume - intersection_volume)
    effective_identity_tolerance = max(
        args.volume_identity_tolerance_mm3,
        args.volume_identity_relative_tolerance
        * max(intersection_volume, 1.0),
    )
    qa = {
        "source_mapping_QA_passed": True,
        "all_configured_windows_have_complete_profiles": all(item["section_count"] >= 2 for item in window_reports),
        "adaptive_wall_thickness_raycast_succeeded": all(
            bool(item["adaptive_wall_thickness"].get(
                "wall_intersection_support_complete",
                item["adaptive_wall_thickness"]["valid_ray_fraction"] >= 0.75,
            ))
            for item in window_reports
        ),
        "adaptive_overcut_preserves_following_wall_clearance": all(
            item["adaptive_wall_thickness"]["following_wall_ray_count"] == 0
            or item["adaptive_wall_thickness"]["minimum_clearance_after_overcut_mm"]
            >= 0.1 - 1e-6
            for item in window_reports
        ),
        "top_crest_is_fully_removed": all(
            clearance >= 0.20 for clearance in top_crest_clearances.values()
        ),
        "axis_sweep_semantic_axis_is_fully_open": all(
            clearance >= max(0.15, args.axis_core_overcut_mm - 0.15)
            for clearance in axis_clearances.values()
        ),
        "axis_sweep_exposes_dental_surface": all(
            report["axis_rows_with_visible_dental_fraction"]
            >= args.minimum_axis_visibility_row_fraction
            for report in visibility_reports.values()
        ),
        "axis_sweep_corridor_is_continuously_clear": all(
            report["minimum_clear_axis_corridor_fraction_per_row"]
            >= args.minimum_axis_clear_corridor_fraction
            for report in visibility_reports.values()
        ),
        "combined_cutter_is_closed_volume": bool(combined.is_volume),
        "result_is_closed_volume": bool(result.is_volume),
        "result_component_count_preserved": len(significant) == source_components,
        "discarded_components_are_sub_tolerance_fragments": all(
            abs(float(item.volume)) < args.fragment_volume_tolerance_mm3
            for item in discarded
        ),
        "window_cut_removed_material": removed_volume >= args.minimum_removed_volume_mm3,
        "no_residual_cutter_overlap": residual_volume <= args.residual_volume_tolerance_mm3,
        "Boolean_volume_identity_holds": identity_error <= effective_identity_tolerance,
        "dental_mesh_was_not_used_as_cutter": True,
    }
    qa_passed = all(qa.values())
    if not qa_passed and not args.write_failed_qa_artifacts:
        raise RuntimeError("mapped observation-window cut failed QA:\n" + json.dumps({
            "QA": qa,
            "source_components": source_components,
            "result_components": len(significant),
            "result_component_volumes_mm3": [
                float(abs(item.volume)) for item in significant
            ],
            "adaptive_wall_thickness": {
                item["id"]: item["adaptive_wall_thickness"]
                for item in window_reports
            },
            "removed_volume_mm3": removed_volume,
            "Boolean_removed_volume_mm3": boolean_removed_volume,
            "discarded_fragment_volume_mm3": discarded_fragment_volume,
            "intersection_volume_mm3": intersection_volume,
            "identity_error_mm3": identity_error,
            "effective_identity_tolerance_mm3": effective_identity_tolerance,
            "residual_volume_mm3": residual_volume,
            "top_crest_clearances_mm": top_crest_clearances,
            "axis_clearances_mm": axis_clearances,
            "axis_sweep_tooth_visibility": visibility_reports,
        }, ensure_ascii=False, indent=2))

    result_ply = output_dir / "guide_with_mapped_observation_windows.ply"
    result_stl = output_dir / "guide_with_mapped_observation_windows.stl"
    cutter_ply = output_dir / "mapped_observation_windows_combined_cutter.ply"
    cutter_stl = output_dir / "mapped_observation_windows_combined_cutter.stl"
    result.export(result_ply)
    result.export(result_stl)
    combined.export(cutter_ply)
    combined.export(cutter_stl)
    preview = output_dir / "mapped_observation_windows_preview.png"
    render_preview(preview, source, result, combined, surfaces)
    context = output_dir / "mapped_observation_windows_context.glb"
    scene = trimesh.Scene()
    scene.add_geometry(colored(source, (148, 163, 184, 35)), node_name="source_guide", geom_name="source_guide")
    scene.add_geometry(colored(result, (198, 166, 107, 210)), node_name="cut_result", geom_name="cut_result")
    scene.add_geometry(colored(combined, (220, 38, 38, 45)), node_name="window_cutter", geom_name="window_cutter")
    scene.export(context)

    report_path = output_dir / "mapped_observation_windows.json"
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "status": (
            "mapped_observation_windows_cut_complete"
            if qa_passed
            else "mapped_observation_windows_cut_failed_qa_diagnostic_artifacts_written"
        ),
        "case_yaml": str(case_yaml),
        "sources": {"mapping_report": str(mapping_path), "guide": str(source_path)},
        "method": {
            "semantic_rule": "consume approved FDI mapping; never re-identify teeth",
            "profile_rule": "follow saved local outer-guide contour from true top",
            "top_extension_mm": float(args.top_extension_mm),
            "side_extension_mm": float(args.side_extension_mm),
            "outward_margin_mm": float(args.outward_margin_mm),
            "wall_depth_rule": "first ray entry/exit wall thickness + overcut",
            "axis_sweep_rule": (
                "common-height straight axis; occlusal zero; exterior first 90 degrees"
            ),
            "wall_overcut_mm": float(args.wall_overcut_mm),
            "maximum_wall_thickness_mm": float(args.maximum_wall_thickness_mm),
            "ray_entry_tolerance_mm": float(args.ray_entry_tolerance_mm),
            "following_wall_safety_mm": float(args.following_wall_safety_mm),
            "axis_core_overcut_mm": float(args.axis_core_overcut_mm),
            "minimum_axis_visibility_row_fraction": float(
                args.minimum_axis_visibility_row_fraction
            ),
            "minimum_axis_clear_corridor_fraction": float(
                args.minimum_axis_clear_corridor_fraction
            ),
            "volume_identity_relative_tolerance": float(
                args.volume_identity_relative_tolerance
            ),
            "source_controlled_auto_repair": repair_report,
            "diagnostic_artifacts_written_despite_failed_QA": bool(
                args.write_failed_qa_artifacts and not qa_passed
            ),
        },
        "windows": window_reports,
        "geometry": {
            "source_volume_mm3": float(abs(source.volume)),
            "result_volume_mm3": float(abs(result.volume)),
            "removed_volume_mm3": removed_volume,
            "Boolean_removed_volume_mm3": boolean_removed_volume,
            "discarded_fragment_volume_mm3": discarded_fragment_volume,
            "source_cutter_intersection_volume_mm3": intersection_volume,
            "Boolean_volume_identity_error_mm3": identity_error,
            "Boolean_volume_identity_tolerance_mm3": effective_identity_tolerance,
            "residual_cutter_overlap_mm3": residual_volume,
            "minimum_removed_axis_clearance_mm": (
                min(axis_clearances.values()) if axis_clearances else None
            ),
            "axis_sweep_tooth_visibility": visibility_reports,
            "source_component_count": source_components,
            "result_component_count": len(significant),
        },
        "QA": qa,
        "outputs": {
            "result_ply": str(result_ply),
            "result_stl": str(result_stl),
            "combined_cutter_ply": str(cutter_ply),
            "combined_cutter_stl": str(cutter_stl),
            "preview_png": str(preview),
            "context_glb": str(context),
            "source_repair_report_json": str(repair_report_path),
            "source_repaired_mesh_ply": (
                str(source_repaired_path) if source_repaired_path else None
            ),
            "report_json": str(report_path),
        },
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _local_axis_failure_rows(
    window_report: dict[str, object],
    args: argparse.Namespace,
) -> tuple[list[int], list[str]]:
    """内部算法说明。\n\nLocate only rows responsible for a failed axis-window QA category."""

    failed_rows: set[int] = set()
    reasons = []
    minimum_clearance = float(
        window_report.get("minimum_removed_axis_clearance_mm", math.inf)
    )
    clearance_threshold = float(
        window_report.get("axis_clearance_threshold_mm", 0.15)
    )
    if minimum_clearance < clearance_threshold:
        failed_rows.update(
            int(value)
            for value in window_report.get(
                "axis_rows_below_clearance_threshold", []
            )
        )
        reasons.append("axis_clearance")

    visibility = window_report.get("tooth_visibility", {})
    if isinstance(visibility, dict):
        visible_fraction = float(
            visibility.get("axis_rows_with_visible_dental_fraction", 1.0)
        )
        if visible_fraction < args.minimum_axis_visibility_row_fraction:
            failed_rows.update(
                int(value)
                for value in visibility.get(
                    "axis_rows_without_visible_dental", []
                )
            )
            reasons.append("dental_visibility")
        corridor_by_row = np.asarray(
            visibility.get("clear_axis_corridor_fraction_by_row", []),
            dtype=float,
        )
        if (
            len(corridor_by_row)
            and float(np.min(corridor_by_row))
            < args.minimum_axis_clear_corridor_fraction
        ):
            failed_rows.update(
                int(value) for value in np.flatnonzero(
                    corridor_by_row < args.minimum_axis_clear_corridor_fraction
                )
            )
            reasons.append("axis_corridor")
    return sorted(failed_rows), reasons


def _smoothed_local_drop_additions(
    row_count: int,
    failed_rows: list[int],
    increment_mm: float,
    transition_rows: int,
) -> np.ndarray:
    """内部算法说明。"""
    additions = np.zeros(row_count, dtype=float)
    for failed_row in failed_rows:
        if not 0 <= failed_row < row_count:
            raise RuntimeError("local correction row index is outside the axis")
        additions[failed_row] = max(additions[failed_row], increment_mm)
        for distance in range(1, transition_rows + 1):
            transition = increment_mm * (
                1.0 - distance / float(transition_rows + 1)
            )
            for row in (failed_row - distance, failed_row + distance):
                if 0 <= row < row_count:
                    additions[row] = max(additions[row], transition)
    return additions


def _run_with_local_failure_correction(
    args: argparse.Namespace,
) -> dict[str, object]:
    """内部算法说明。"""
    increment_mm = float(args.local_failure_drop_increment_mm)
    if increment_mm < 0.0:
        raise RuntimeError("local failure drop increment must be non-negative")
    if increment_mm == 0.0:
        return _run_once(args)
    if int(args.local_failure_transition_rows) < 0:
        raise RuntimeError("local failure transition rows must be non-negative")

    case_yaml = args.case.resolve()
    output_root = (
        args.output_dir.resolve()
        if args.output_dir
        else case_yaml.parent / "输出/mapped_observation_windows_local_adaptive"
    )
    output_root.mkdir(parents=True, exist_ok=True)
    initial_args = argparse.Namespace(**vars(args))
    initial_args.output_dir = output_root / "00_initial_pass"
    initial_args.write_failed_qa_artifacts = True
    initial_args.local_failure_drop_increment_mm = 0.0
    initial_report = _run_once(initial_args)
    if all(initial_report["QA"].values()):
        initial_report["local_failure_adaptation"] = {
            "requested_increment_mm": increment_mm,
            "applied": False,
            "reason": "initial pass already satisfied all QA gates",
        }
        initial_path = Path(initial_report["outputs"]["report_json"])
        initial_path.write_text(
            json.dumps(initial_report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return initial_report

    mapping_path = (
        args.mapping_report.resolve()
        if args.mapping_report
        else case_yaml.parent / "输出/tooth_guide_mapping/tooth_guide_mapping.json"
    )
    corrected_mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    mapping_windows = {
        str(window["id"]): window
        for window in corrected_mapping["observation_windows"]
    }
    adaptations = {}
    for window_report in initial_report["windows"]:
        if window_report["opening_geometry"] != "axis_sweep":
            continue
        failed_rows, reasons = _local_axis_failure_rows(window_report, args)
        if not failed_rows:
            continue
        window_id = str(window_report["id"])
        definition = mapping_windows[window_id]["axis_sweep"]
        row_count = int(definition["axis_section_count"])
        additions = _smoothed_local_drop_additions(
            row_count,
            failed_rows,
            increment_mm,
            int(args.local_failure_transition_rows),
        )
        definition["local_axis_drop_additions_mm"] = [
            float(value) for value in additions
        ]
        definition["maximum_effective_axis_drop_mm"] = (
            float(definition["axis_drop_mm"]) + float(np.max(additions))
        )
        definition["local_failure_adaptation"] = {
            "failed_axis_rows": failed_rows,
            "failure_reasons": reasons,
            "increment_mm": increment_mm,
            "transition_rows": int(args.local_failure_transition_rows),
            "corrected_axis_rows": [
                int(index) for index in np.flatnonzero(additions > 1e-9)
            ],
        }
        adaptations[window_id] = definition["local_failure_adaptation"]
    if not adaptations:
        initial_report["local_failure_adaptation"] = {
            "requested_increment_mm": increment_mm,
            "applied": False,
            "reason": "failed QA did not identify a correctable local axis row",
        }
        initial_path = Path(initial_report["outputs"]["report_json"])
        initial_path.write_text(
            json.dumps(initial_report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return initial_report

    corrected_mapping_path = output_root / "01_local_corrected_mapping.json"
    corrected_mapping_path.write_text(
        json.dumps(corrected_mapping, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    corrected_args = argparse.Namespace(**vars(args))
    corrected_args.mapping_report = corrected_mapping_path
    corrected_args.output_dir = output_root / "02_local_corrected_cut"
    corrected_args.write_failed_qa_artifacts = True
    corrected_args.local_failure_drop_increment_mm = 0.0
    corrected_report = _run_once(corrected_args)
    corrected_report["local_failure_adaptation"] = {
        "requested_increment_mm": increment_mm,
        "applied": True,
        "initial_report": str(initial_report["outputs"]["report_json"]),
        "corrected_mapping_report": str(corrected_mapping_path),
        "windows": adaptations,
        "global_axis_drop_unchanged": True,
    }
    corrected_report_path = Path(corrected_report["outputs"]["report_json"])
    corrected_report_path.write_text(
        json.dumps(corrected_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return corrected_report


def _run_with_local_failure_target_sequence(
    args: argparse.Namespace,
) -> dict[str, object]:
    """内部算法说明。\n\nRetry only failed axis rows at increasing effective drop targets.

    The mapping's ``axis_drop_mm`` remains the global baseline.  A target of
    0.5 mm with a 0.2 mm baseline therefore writes a 0.3 mm local addition,
    never a second global drop.  Rows that pass an earlier attempt keep their
    earlier correction; only rows reported as failed advance to the next
    target.
    """

    targets = tuple(
        float(value)
        for value in getattr(args, "local_failure_drop_target_mm", None) or ()
    )
    if not targets:
        return _run_with_local_failure_correction(args)
    if float(args.local_failure_drop_increment_mm) != 0.0:
        raise RuntimeError(
            "local failure drop targets and increment mode are mutually exclusive"
        )
    if any(not math.isfinite(value) or value <= 0.0 for value in targets):
        raise RuntimeError("local failure drop targets must be positive finite values")
    if any(later <= earlier for earlier, later in itertools.pairwise(targets)):
        raise RuntimeError("local failure drop targets must be strictly increasing")
    if int(args.local_failure_transition_rows) < 0:
        raise RuntimeError("local failure transition rows must be non-negative")

    case_yaml = args.case.resolve()
    output_root = (
        args.output_dir.resolve()
        if args.output_dir
        else case_yaml.parent / "输出/mapped_observation_windows_local_adaptive"
    )
    output_root.mkdir(parents=True, exist_ok=True)
    original_mapping_path = (
        args.mapping_report.resolve()
        if args.mapping_report
        else case_yaml.parent / "输出/tooth_guide_mapping/tooth_guide_mapping.json"
    )
    working_mapping = json.loads(original_mapping_path.read_text(encoding="utf-8"))

    initial_args = argparse.Namespace(**vars(args))
    initial_args.output_dir = output_root / "00_initial_drop"
    initial_args.write_failed_qa_artifacts = True
    initial_args.local_failure_drop_increment_mm = 0.0
    initial_args.local_failure_drop_target_mm = None
    current_report = _run_once(initial_args)
    attempts: list[dict[str, object]] = [{
        "attempt": 0,
        "effective_drop_target_mm": None,
        "report": str(current_report["outputs"]["report_json"]),
        "QA_passed": bool(all(current_report["QA"].values())),
        "windows": {},
    }]
    if all(current_report["QA"].values()):
        current_report["local_failure_adaptation"] = {
            "mode": "effective_drop_target_sequence",
            "requested_targets_mm": list(targets),
            "applied": False,
            "reason": "initial global mapping pass already satisfied all QA gates",
            "attempts": attempts,
            "global_axis_drop_unchanged": True,
        }
        current_path = Path(current_report["outputs"]["report_json"])
        current_path.write_text(
            json.dumps(current_report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return current_report

    mapping_windows = {
        str(window["id"]): window
        for window in working_mapping["observation_windows"]
    }
    last_target: float | None = None
    exhausted = True
    for attempt_index, target_mm in enumerate(targets, 1):
        adaptations: dict[str, object] = {}
        for window_report in current_report["windows"]:
            if window_report["opening_geometry"] != "axis_sweep":
                continue
            failed_rows, reasons = _local_axis_failure_rows(window_report, args)
            if not failed_rows:
                continue
            window_id = str(window_report["id"])
            definition = mapping_windows[window_id]["axis_sweep"]
            baseline_drop_mm = float(definition["axis_drop_mm"])
            if target_mm <= baseline_drop_mm:
                raise RuntimeError(
                    f"local failure target {target_mm} mm must exceed window "
                    f"{window_id!r} global axis drop {baseline_drop_mm} mm"
                )
            row_count = int(definition["axis_section_count"])
            target_addition_mm = target_mm - baseline_drop_mm
            proposed = _smoothed_local_drop_additions(
                row_count,
                failed_rows,
                target_addition_mm,
                int(args.local_failure_transition_rows),
            )
            existing = np.asarray(
                definition.get("local_axis_drop_additions_mm", [0.0] * row_count),
                dtype=float,
            )
            additions = np.maximum(existing, proposed)
            definition["local_axis_drop_additions_mm"] = [
                float(value) for value in additions
            ]
            definition["maximum_effective_axis_drop_mm"] = (
                baseline_drop_mm + float(np.max(additions))
            )
            adaptation = {
                "failed_axis_rows": failed_rows,
                "failure_reasons": reasons,
                "effective_drop_target_mm": target_mm,
                "local_addition_at_failed_rows_mm": target_addition_mm,
                "transition_rows": int(args.local_failure_transition_rows),
                "corrected_axis_rows": [
                    int(index) for index in np.flatnonzero(proposed > 1e-9)
                ],
            }
            definition["local_failure_adaptation"] = adaptation
            adaptations[window_id] = adaptation

        if not adaptations:
            exhausted = False
            break

        mapping_path = output_root / (
            f"{attempt_index:02d}_local_target_{target_mm:g}mm_mapping.json"
        )
        mapping_path.write_text(
            json.dumps(working_mapping, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        retry_args = argparse.Namespace(**vars(args))
        retry_args.mapping_report = mapping_path
        retry_args.output_dir = output_root / (
            f"{attempt_index:02d}_local_target_{target_mm:g}mm_cut"
        )
        retry_args.write_failed_qa_artifacts = True
        retry_args.local_failure_drop_increment_mm = 0.0
        retry_args.local_failure_drop_target_mm = None
        current_report = _run_once(retry_args)
        last_target = target_mm
        attempts.append({
            "attempt": attempt_index,
            "effective_drop_target_mm": target_mm,
            "mapping_report": str(mapping_path),
            "report": str(current_report["outputs"]["report_json"]),
            "QA_passed": bool(all(current_report["QA"].values())),
            "windows": adaptations,
        })
        if all(current_report["QA"].values()):
            exhausted = False
            break

    current_report["local_failure_adaptation"] = {
        "mode": "effective_drop_target_sequence",
        "requested_targets_mm": list(targets),
        "applied": len(attempts) > 1,
        "selected_effective_drop_target_mm": last_target,
        "QA_passed": bool(all(current_report["QA"].values())),
        "target_sequence_exhausted": exhausted,
        "attempts": attempts,
        "global_axis_drop_unchanged": True,
        "final_attempt_is_retained": True,
    }
    current_path = Path(current_report["outputs"]["report_json"])
    current_path.write_text(
        json.dumps(current_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return current_report


def run(args: argparse.Namespace) -> dict[str, object]:
    """内部算法说明。"""
    return _run_with_local_failure_target_sequence(args)


def parse_args() -> argparse.Namespace:
    """内部算法说明。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True, type=Path)
    parser.add_argument("--mapping-report", type=Path, default=None)
    parser.add_argument("--source", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument(
        "--window-id", action="append", default=None,
        help="Cut only the named observation window; repeat to select multiple windows.",
    )
    parser.add_argument("--top-extension-mm", type=float, default=0.4)
    parser.add_argument("--side-extension-mm", type=float, default=0.4)
    parser.add_argument("--outward-margin-mm", type=float, default=0.4)
    parser.add_argument("--wall-overcut-mm", type=float, default=0.4)
    parser.add_argument(
        "--window-wall-overcut-mm", action="append", default=None,
        metavar="WINDOW_ID=MM",
        help="Override wall overcut for one selected window; repeat as needed.",
    )
    parser.add_argument("--maximum-wall-thickness-mm", type=float, default=5.0)
    parser.add_argument("--ray-entry-tolerance-mm", type=float, default=0.65)
    parser.add_argument("--following-wall-safety-mm", type=float, default=0.10)
    parser.add_argument("--axis-core-overcut-mm", type=float, default=0.30)
    parser.add_argument(
        "--minimum-axis-visibility-row-fraction", type=float, default=0.50
    )
    parser.add_argument(
        "--minimum-axis-clear-corridor-fraction", type=float, default=0.95
    )
    parser.add_argument("--union-batch-size", type=int, default=16)
    parser.add_argument("--fragment-volume-tolerance-mm3", type=float, default=2.0)
    parser.add_argument("--minimum-removed-volume-mm3", type=float, default=1.0)
    parser.add_argument("--residual-volume-tolerance-mm3", type=float, default=1e-4)
    parser.add_argument("--volume-identity-tolerance-mm3", type=float, default=5e-3)
    parser.add_argument(
        "--volume-identity-relative-tolerance", type=float, default=1e-4
    )
    parser.add_argument(
        "--write-failed-qa-artifacts",
        action="store_true",
        help=(
            "Write explicitly diagnostic STL/preview/report artifacts when QA fails; "
            "the report remains failed and the command exits with status 2."
        ),
    )
    parser.add_argument(
        "--local-failure-drop-increment-mm",
        type=float,
        default=0.0,
        help=(
            "After a failed initial axis-sweep QA pass, lower only the failed "
            "axis rows by this additional height and cut once more."
        ),
    )
    parser.add_argument(
        "--local-failure-drop-target-mm",
        action="append",
        type=float,
        default=None,
        help=(
            "After a failed pass, set only failed axis rows to this effective "
            "drop target; repeat in strictly increasing order for staged retries."
        ),
    )
    parser.add_argument(
        "--local-failure-transition-rows",
        type=int,
        default=1,
        help="Number of adjacent rows used for a tapered local correction.",
    )
    return parser.parse_args()


def main() -> int:
    """内部算法说明。"""
    report = run(parse_args())
    print(json.dumps({
        "status": report["status"],
        "windows": report["windows"],
        "geometry": report["geometry"],
        "QA": report["QA"],
        "outputs": report["outputs"],
    }, ensure_ascii=False, indent=2))
    return 0 if all(report["QA"].values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())

"""根据已批准的 FDI 牙位映射构造轴扫观察窗。"""

from __future__ import annotations

import itertools
import json
import math
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import trimesh
from manifold3d import Manifold, Mesh

from twin_guide.tooth_identification import stage_2_mapping_payload
from twin_guide.tooth_mapping.controlled_mesh_repair import (
    ControlledVolumeRepairError,
    ensure_closed_volume,
)
from twin_guide.tooth_mapping.pipeline import load_mesh, unit

EPS = 1e-9


@dataclass(frozen=True, slots=True)
class ObservationWindowRequest:
    """第 3 阶段轴扫观察窗的唯一内部请求。"""

    case: Path
    mapping_report: Path
    source: Path
    output_dir: Path
    side_extension_mm: float = 0.4
    wall_overcut_mm: float = 0.4
    following_wall_safety_mm: float = 0.10
    axis_core_overcut_mm: float = 0.30
    minimum_axis_visibility_row_fraction: float = 0.50
    minimum_axis_clear_corridor_fraction: float = 0.95
    union_batch_size: int = 16
    fragment_volume_tolerance_mm3: float = 2.0
    minimum_removed_volume_mm3: float = 1.0
    residual_volume_tolerance_mm3: float = 1e-4
    volume_identity_tolerance_mm3: float = 5e-2
    volume_identity_relative_tolerance: float = 1e-4
    local_failure_drop_targets_mm: tuple[float, ...] = ()
    local_failure_transition_rows: int = 1


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


def _run_once(args: ObservationWindowRequest) -> dict[str, object]:
    """内部算法说明。"""
    case_yaml = args.case.resolve()
    mapping_path = args.mapping_report.resolve()
    mapping = stage_2_mapping_payload(
        json.loads(mapping_path.read_text(encoding="utf-8"))
    )
    if mapping.get("status") != "tooth_guide_mapping_complete" or not all(mapping.get("QA", {}).values()):
        raise RuntimeError("tooth-guide mapping has not passed all QA gates")
    source_path = args.source.resolve()
    source = load_mesh(source_path)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    repair_report_path = output_dir / "source_guide_controlled_repair.json"
    repaired_mesh_path = output_dir / "source_guide_controlled_repaired.ply"
    for stale_path in (repair_report_path, repaired_mesh_path):
        if stale_path.is_file():
            stale_path.unlink()
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
        source_repaired_path = repaired_mesh_path
        source.export(source_repaired_path)
    repair_report = dict(repair.report)
    repair_report["report_json"] = (
        str(repair_report_path) if repair.report["applied"] else None
    )
    repair_report["repaired_mesh_ply"] = (
        str(source_repaired_path) if source_repaired_path else None
    )
    if repair.report["applied"]:
        repair_report_path.write_text(
            json.dumps(repair_report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    selected_windows = mapping["observation_windows"]
    if not selected_windows:
        raise RuntimeError("no observation windows were selected")

    cutters = {}
    surfaces = {}
    window_reports = []
    for window in selected_windows:
        opening_geometry = str(window.get("opening_geometry", ""))
        if opening_geometry != "axis_sweep":
            raise RuntimeError(
                f"window {window['id']} must use axis_sweep, got "
                f"{opening_geometry!r}"
            )
        failed_indices = sorted(
            int(item["sample_index"]) for item in window.get("failures", [])
        )
        definition = window.get("axis_sweep")
        if not isinstance(definition, dict):
            raise RuntimeError(
                f"window {window['id']} has no mapped axis-sweep definition"
            )
        cutter, surface, wall_report = build_axis_sweep_cutter(
            source,
            definition,
            args.side_extension_mm,
            args.wall_overcut_mm,
            args.following_wall_safety_mm,
            args.axis_core_overcut_mm,
            args.union_batch_size,
        )
        profile_count = int(definition["axis_section_count"])
        profile_point_count = int(definition["angle_section_count"])
        cutters[window["id"]] = cutter
        surfaces[window["id"]] = surface
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
            "exterior_wall_sampling": wall_report,
            "axis_sweep": window.get("axis_sweep"),
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
    dental_path = Path(mapping["sources"]["dental"])
    dental = load_mesh(dental_path)
    for mapped_window, window_report in zip(
        selected_windows, window_reports, strict=True
    ):
        visibility = axis_sweep_tooth_visibility(
            result,
            dental,
            mapped_window["axis_sweep"],
            surfaces[window_report["id"]],
        )
        visibility_reports[window_report["id"]] = visibility
        window_report["tooth_visibility"] = visibility

    axis_clearances = {}
    for mapped_window, window_report in zip(
        selected_windows, window_reports, strict=True
    ):
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
        "all_configured_windows_have_complete_profiles": all(item["section_count"] >= 2 for item in window_reports),
        "exterior_wall_raycast_succeeded": all(
            bool(item["exterior_wall_sampling"].get(
                "wall_intersection_support_complete",
                item["exterior_wall_sampling"]["valid_ray_fraction"] >= 0.75,
            ))
            for item in window_reports
        ),
        "radial_overcut_preserves_following_wall_clearance": all(
            item["exterior_wall_sampling"]["following_wall_ray_count"] == 0
            or item["exterior_wall_sampling"]["minimum_clearance_after_overcut_mm"]
            >= 0.1 - 1e-6
            for item in window_reports
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
    }
    qa_passed = all(qa.values())
    cutter_ply = output_dir / "observation-window-cutter.ply"
    combined.export(cutter_ply)
    report_path = output_dir / "observation-window-report.json"
    report = {
        "status": (
            "mapped_observation_windows_cut_complete"
            if qa_passed
            else "mapped_observation_windows_cut_failed_qa_diagnostic_artifacts_written"
        ),
        "case_yaml": str(case_yaml),
        "sources": {"mapping_report": str(mapping_path), "guide": str(source_path)},
        "method": {
            "semantic_rule": "consume approved FDI mapping; never re-identify teeth",
            "profile_rule": "sweep the approved FDI semantic axis through 90 degrees",
            "side_extension_mm": float(args.side_extension_mm),
            "wall_depth_rule": "exterior guide-wall ray hit + radial overcut",
            "axis_sweep_rule": (
                "common-height straight axis; occlusal zero; exterior first 90 degrees"
            ),
            "wall_overcut_mm": float(args.wall_overcut_mm),
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
            "combined_cutter_ply": str(cutter_ply),
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
    args: ObservationWindowRequest,
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


def _run_with_local_failure_target_sequence(
    args: ObservationWindowRequest,
) -> dict[str, object]:
    """内部算法说明。\n\nRetry only failed axis rows at increasing effective drop targets.

    The mapping's ``axis_drop_mm`` remains the global baseline.  A target of
    0.5 mm with a 0.2 mm baseline therefore writes a 0.3 mm local addition,
    never a second global drop.  Rows that pass an earlier attempt keep their
    earlier correction; only rows reported as failed advance to the next
    target.
    """

    targets = tuple(float(value) for value in args.local_failure_drop_targets_mm)
    if any(not math.isfinite(value) or value <= 0.0 for value in targets):
        raise RuntimeError("local failure drop targets must be positive finite values")
    if any(later <= earlier for earlier, later in itertools.pairwise(targets)):
        raise RuntimeError("local failure drop targets must be strictly increasing")
    if int(args.local_failure_transition_rows) < 0:
        raise RuntimeError("local failure transition rows must be non-negative")

    output_root = args.output_dir.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    original_mapping_path = args.mapping_report.resolve()
    working_mapping = stage_2_mapping_payload(
        json.loads(original_mapping_path.read_text(encoding="utf-8"))
    )

    current_report = _run_once(args)
    attempts: list[dict[str, object]] = [{
        "attempt": 0,
        "effective_drop_target_mm": None,
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

        mapping_path = output_root / "local-corrected-mapping.json"
        mapping_path.write_text(
            json.dumps(working_mapping, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        retry_args = replace(args, mapping_report=mapping_path)
        current_report = _run_once(retry_args)
        last_target = target_mm
        attempts.append({
            "attempt": attempt_index,
            "effective_drop_target_mm": target_mm,
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


def run(args: ObservationWindowRequest) -> dict[str, object]:
    """构造唯一轴扫观察窗，只对失败行执行分级局部修正。"""
    return _run_with_local_failure_target_sequence(args)

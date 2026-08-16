"""算法说明。 Public orchestrator for isolated ``fdi_new`` mapping."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import trimesh
import yaml
from skimage.draw import polygon

from twin_guide.tooth_mapping.fdi import crown_width_prior_mm, validate_anatomy
from twin_guide.tooth_mapping.pipeline import resolve_case_path

from .arch_coordinates import build_arch_frame_candidates
from .component_segmentation import (
    SegmentationDiagnostics,
    choose_partition_map,
    resample_partition_maps,
    segment_component_local_regions,
)
from .models import (
    AlignmentPath,
    ArchFrame,
    CoreTrack,
    CrownHypothesis,
    LabeledMissingSlotAnchor,
    ToothFdiMappingNewRequest,
    ToothFdiMappingNewResult,
    ToothRegion,
)
from .missing_slot_anchors import (
    evaluate_anchor_alignment,
    evaluate_anchor_frame,
    extract_labeled_missing_slot_anchors,
)
from .multi_view_boundary import (
    MultiViewBoundaryEvidence,
    assignment_pair_boundary_evidence,
    build_multiview_boundary_evidence,
    resample_occlusal_evidence,
)
from .multiscale_candidates import detect_core_tracks, render_multiscale_maps
from .sequence_alignment import (
    build_crown_hypotheses,
    rank_monotone_fdi_alignments,
)
from .surface_valleys import build_surface_valley_evidence


TOOTH_FDI_MAPPING_NEW_SCHEMA_VERSION = "fdi-new-intercore-separator-exclusion"
SCHEMA_VERSION = TOOTH_FDI_MAPPING_NEW_SCHEMA_VERSION


@dataclass
class _OrientationRun:
    """算法说明。"""
    frame: ArchFrame
    tracks: list[CoreTrack]
    hypotheses: list[CrownHypothesis]
    maps_by_quantile: dict[float, dict[str, object]]


@dataclass
class _CoreRun:
    """算法说明。"""
    orientation: _OrientationRun
    alignment: AlignmentPath
    second: AlignmentPath | None
    alignment_margin: float | None
    partition_quantile: float
    partition_maps: dict[str, object]
    regions: list[ToothRegion]
    segmentation: SegmentationDiagnostics
    label_grid: np.ndarray
    rejected_alignment_paths: tuple[dict[str, object], ...]
    second_alignment_is_feasible: bool
    alignment_margin_mode: str
    equivalent_alignment_paths: tuple[dict[str, object], ...]
    orientation_alternative_cost: float | None
    orientation_margin_per_tooth: float | None
    structural_diagnostics: dict[str, object]
    counterfactual_margin_by_fdi: dict[str, float | None]
    surface_valleys: Any | None
    missing_slot_anchor_diagnostics: dict[str, object]
    present_fdi_constraint_diagnostics: dict[str, object]
    multiview_evidence: MultiViewBoundaryEvidence | None = None


def _prioritize_complete_present_paths(
    paths: list[AlignmentPath],
) -> tuple[list[AlignmentPath], list[AlignmentPath], bool]:
    """内部算法说明。 Keep undetected-present paths diagnostic-only when completion is possible.

    YAML ``present_teeth`` is an operator-confirmed hard identity set.  Physical
    merge/split evidence still determines QA and downstream safety, but it may
    not erase a declared present FDI merely to obtain a cheaper conservative
    path.
    """

    complete = [item for item in paths if not item.undetected_fdi]
    incomplete = [item for item in paths if item.undetected_fdi]
    if complete:
        return complete, incomplete, True
    return paths, [], False


def _counterfactual_margins(
    best: AlignmentPath,
    ranked: list[AlignmentPath],
) -> dict[str, float | None]:
    """算法说明。 Measure the cheapest structurally different assignment for each FDI."""

    best_by_fdi = {item.fdi: item for item in best.assignments}
    result: dict[str, float | None] = {}
    tooth_count = max(len(best_by_fdi), 1)
    for fdi, assignment in best_by_fdi.items():
        alternative_costs = []
        for path in ranked:
            candidate = next(
                (item for item in path.assignments if item.fdi == fdi), None
            )
            if candidate is None:
                continue
            same_physical_assignment = (
                candidate.kind == assignment.kind
                and candidate.core_ids == assignment.core_ids
                and (
                    candidate.s_mm is None
                    or assignment.s_mm is None
                    or abs(candidate.s_mm - assignment.s_mm)
                    <= 0.20 * max(
                        best.global_scale * crown_width_prior_mm(fdi), 2.0
                    )
                )
            )
            if not same_physical_assignment:
                alternative_costs.append(path.total_cost)
        result[str(fdi)] = (
            float((min(alternative_costs) - best.total_cost) / tooth_count)
            if alternative_costs else None
        )
    return result


def _structural_evidence_diagnostics(
    alignment: AlignmentPath,
    regions: list[ToothRegion],
    segmentation: SegmentationDiagnostics,
    tracks: list[CoreTrack],
    maximum_local_assignment_robust_z: float = 4.0,
    maximum_bilateral_region_area_ratio: float = 1.80,
    minimum_reference_persistence: float = 0.60,
    minimum_relative_crown_height_ratio: float = 0.60,
    minimum_relative_relief_quality_ratio: float = 0.75,
    label_grid: np.ndarray | None = None,
    partition_maps: dict[str, object] | None = None,
) -> dict[str, object]:
    """算法说明。

Reject count-correct paths whose physical edit operations lack evidence.

    The checks are deliberately expressed relative to other structures in the
    same case.  They do not contain case names, FDI combinations, or
    case-specific millimetre thresholds.
    """

    conflicts: list[dict[str, object]] = []
    assignments = [
        item for item in alignment.assignments if item.center_lr_ap_mm is not None
    ]
    track_by_id = {item.track_id: item for item in tracks}
    split_hypotheses = {
        item.hypothesis_id for item in assignments if item.kind == "split"
    }
    merge_hypotheses = {
        item.hypothesis_id for item in assignments if item.kind == "merge"
    }

    accepted_valley_pairs = {
        (int(item["component_id"]), int(item["pair_index"]))
        for item in segmentation.surface_valley_separator_records
        if bool(item.get("accepted"))
    }
    accepted_anatomical_fdi_pairs = {
        (int(item["first_FDI"]), int(item["second_FDI"]))
        for item in segmentation.surface_valley_separator_records
        if bool(item.get("accepted"))
        and item.get("first_FDI") is not None
        and item.get("second_FDI") is not None
    }
    weak_split_records = [
        item for item in segmentation.separator_candidate_records
        if item.get("rejection_reason") == "low_evidence_shared_split_uses_level_2"
        and (
            int(item.get("component_id", -1)),
            int(item.get("pair_index", -1)),
        ) not in accepted_valley_pairs
    ]
    for record in weak_split_records:
        conflicts.append({
            "kind": "unresolved_single_or_multiple",
            "reason": "shared_split_has_no_typical_component_local_contact",
            "component_id": record.get("component_id"),
            "pair_index": record.get("pair_index"),
            "evidence_score": record.get("evidence_score"),
            "component_evidence_median": record.get("component_evidence_median"),
        })

    for record in segmentation.unsupported_separator_records:
        conflicts.append({
            "kind": "unresolved_fused_present_teeth",
            "reason": "adjacent_present_FDI_has_no_anatomical_separator",
            **record,
        })
    for record in segmentation.boundary_topology_records:
        conflicts.append({
            "kind": "invalid_intertooth_boundary_topology",
            **record,
        })

    # Use the stable cores in this same scan as the crown-height reference.
    # A flat occlusal patch can have a large distance-transform radius and thus
    # look like a crown in silhouette, but it should not acquire a present FDI
    # when both its local 3-D relief and relief quality are far below its peers.
    stable_reference = [
        item for item in tracks
        if item.persistence >= minimum_reference_persistence
        and item.relative_crown_height_mm > 0.0
        and item.relief_quality > 0.0
    ]
    reference_height = float(np.median([
        item.relative_crown_height_mm for item in stable_reference
    ])) if stable_reference else 0.0
    reference_relief_quality = float(np.median([
        item.relief_quality for item in stable_reference
    ])) if stable_reference else 0.0
    low_crown_support_fdi: list[int] = []
    low_crown_support_records: list[dict[str, object]] = []
    for assignment in assignments:
        supporting_tracks = [
            track_by_id[core_id]
            for core_id in assignment.core_ids if core_id in track_by_id
        ]
        if not supporting_tracks or reference_height <= 0.0:
            continue
        # Merge hypotheses may contain fragmentary duplicate peaks.  One
        # anatomically complete supporting track is sufficient; requiring all
        # fragments to be tall would incorrectly reject valid deduplication.
        height = max(item.relative_crown_height_mm for item in supporting_tracks)
        relief_quality = max(item.relief_quality for item in supporting_tracks)
        height_ratio = height / max(reference_height, 1.0e-9)
        quality_ratio = relief_quality / max(reference_relief_quality, 1.0e-9)
        physical_support = max(
            item.relative_3d_tooth_support for item in supporting_tracks
        )
        if physical_support < 1.0 - 1.0e-6:
            low_crown_support_fdi.append(int(assignment.fdi))
            # Relative relief is deliberately soft evidence.  A terminal real
            # tooth and a detached flat projection lobe can be indistinguishable
            # locally; the confirmed present-FDI sequence and the competing
            # global path resolve that ambiguity.  Keep the observation in the
            # report, but do not veto an otherwise feasible monotone assignment.
            # Hard rejection here previously made tooth-15/16 undetected while
            # fixing tooth-17 only by local thresholding.
            low_crown_support_records.append({
                "kind": "low_relative_3d_crown_support_assignment",
                "reason": "global_sequence_selected_locally_weak_crown_support",
                "FDI": int(assignment.fdi),
                "core_ids": list(assignment.core_ids),
                "relative_crown_height_ratio": float(height_ratio),
                "relative_relief_quality_ratio": float(quality_ratio),
                "relative_3d_tooth_support": float(physical_support),
                "projection_component_area_ratio": float(max(
                    item.projection_component_area_ratio
                    for item in supporting_tracks
                )),
                "case_reference_crown_height_mm": reference_height,
                "case_reference_relief_quality": reference_relief_quality,
            })

    # A high individual assignment cost hidden inside a low total path cost is
    # the characteristic signature of a one-tooth label shift.  Compare costs
    # against their within-path robust distribution; do not tune by tooth type.
    costs = np.asarray([
        max(float(item.match_cost), 0.0) for item in assignments
    ], dtype=float)
    cost_outlier_fdi: list[int] = []
    diagnostic_only_single_cost_outlier_fdi: list[int] = []
    if len(costs) >= 5:
        median = float(np.median(costs))
        mad = float(np.median(np.abs(costs - median)))
        upper_quartile = float(np.quantile(costs, 0.75))
        robust_scale = max(1.4826 * mad, 0.25 * max(upper_quartile, 1.0))
        for assignment, cost in zip(assignments, costs, strict=True):
            robust_z = (float(cost) - median) / robust_scale
            if robust_z > maximum_local_assignment_robust_z:
                cost_outlier_fdi.append(int(assignment.fdi))
                record = {
                    "kind": "semantic_geometry_conflict",
                    "reason": "local_assignment_cost_is_robust_outlier",
                    "FDI": int(assignment.fdi),
                    "hypothesis_kind": assignment.kind,
                    "match_cost": float(cost),
                    "within_path_median": median,
                    "within_path_robust_scale": robust_scale,
                    "robust_z": robust_z,
                }
                split_group = {
                    int(item.fdi) for item in assignments
                    if item.kind == "split"
                    and item.hypothesis_id == assignment.hypothesis_id
                }
                split_boundary_resolved = (
                    assignment.kind == "split"
                    and len(split_group) == 2
                    and any(
                        split_group == set(pair)
                        for pair in accepted_anatomical_fdi_pairs
                    )
                )
                if (
                    assignment.kind in {"merge", "split"}
                    and not split_boundary_resolved
                ):
                    conflicts.append(record)
                else:
                    diagnostic_only_single_cost_outlier_fdi.append(
                        int(assignment.fdi)
                    )

    # Bilateral counterparts provide an internal scale reference.  An area
    # close to two crowns is evidence that a neighbouring physical tooth may
    # have been absorbed.  This remains a review conflict, never an automatic
    # relabel operation.
    region_by_fdi = {item.fdi: item for item in regions}
    region_by_id = {item.region_id: item for item in regions}

    # Detect an implicit split/merge compensation that is invisible in the
    # hypothesis names.  Several nearby single-FDI assignments can occupy one
    # physical crown while a distant terminal region absorbs another stable
    # crown core.  The latter is only considered independent when its centre
    # lies inside the assigned region and is at least 0.75 local crown scales
    # away from every core that was intentionally assigned to that region.
    absorbed_independent_core_records: list[dict[str, object]] = []
    if label_grid is not None and partition_maps is not None:
        lr = np.asarray(partition_maps["lr_centres"], dtype=float)
        ap = np.asarray(partition_maps["ap_centres"], dtype=float)
        assignment_by_fdi = {item.fdi: item for item in assignments}
        for core_id in alignment.artifact_core_ids:
            track = track_by_id.get(core_id)
            if (
                track is None
                or track.persistence < minimum_reference_persistence
                or track.crownness < 0.60
                or track.relative_3d_tooth_support < 0.75
            ):
                continue
            row = int(np.argmin(np.abs(lr - track.center_lr_ap_mm[0])))
            column = int(np.argmin(np.abs(ap - track.center_lr_ap_mm[1])))
            region = region_by_id.get(int(label_grid[row, column]))
            if region is None:
                continue
            assignment = assignment_by_fdi.get(region.fdi)
            if assignment is None:
                continue
            supporting_tracks = [
                track_by_id[item]
                for item in assignment.core_ids if item in track_by_id
            ]
            if not supporting_tracks:
                continue
            normalized_separation = min(
                np.hypot(
                    track.s_mm - supporting.s_mm,
                    track.u_mm - supporting.u_mm,
                ) / max(
                    0.5 * (
                        track.local_scale_mm + supporting.local_scale_mm
                    ),
                    1.0e-9,
                )
                for supporting in supporting_tracks
            )
            if normalized_separation < 0.75:
                continue
            absorbed_independent_core_records.append({
                "FDI": int(region.fdi),
                "absorbed_core_id": int(core_id),
                "assigned_core_ids": list(assignment.core_ids),
                "normalized_core_separation": float(normalized_separation),
                "persistence": float(track.persistence),
                "crownness": float(track.crownness),
            })

    crowded_single_assignment_pairs: list[dict[str, object]] = []
    for assignment_pair_index, (first, second) in enumerate(
        zip(assignments, assignments[1:])
    ):
        if (
            first.kind != "single"
            or second.kind != "single"
            or first.s_mm is None
            or second.s_mm is None
        ):
            continue
        expected_spacing = alignment.global_scale * 0.5 * (
            crown_width_prior_mm(first.fdi)
            + crown_width_prior_mm(second.fdi)
        )
        normalized_spacing = (
            float(second.s_mm - first.s_mm)
            / max(expected_spacing, 1.0e-9)
        )
        if normalized_spacing < 0.65:
            boundary = next((
                item for item in segmentation.separator_records
                if int(item.get("first_instance_id", -1))
                == assignment_pair_index + 1
                and int(item.get("second_instance_id", -1))
                == assignment_pair_index + 2
            ), None)
            crowded_single_assignment_pairs.append({
                "first_FDI": int(first.fdi),
                "second_FDI": int(second.fdi),
                "spacing_mm": float(second.s_mm - first.s_mm),
                "expected_spacing_mm": float(expected_spacing),
                "normalized_spacing": float(normalized_spacing),
                "paired_concavity_score": (
                    boundary.get("paired_concavity_score")
                    if boundary is not None else None
                ),
                "paired_concavity_level": (
                    boundary.get("paired_concavity_level")
                    if boundary is not None else None
                ),
                "paired_concavity_facing_support": (
                    boundary.get("paired_concavity_facing_support")
                    if boundary is not None else None
                ),
                "paired_concavity_axial_alignment": (
                    boundary.get("paired_concavity_axial_alignment")
                    if boundary is not None else None
                ),
                "paired_concavity_crown_support": (
                    boundary.get("paired_concavity_crown_support")
                    if boundary is not None else None
                ),
            })

    if absorbed_independent_core_records:
        conflicts.append({
            "kind": "unresolved_single_or_multiple",
            "reason": "present_region_absorbs_independent_persistent_crown_core",
            "records": absorbed_independent_core_records,
        })
    implicit_compensation = bool(
        absorbed_independent_core_records and crowded_single_assignment_pairs
    )
    if implicit_compensation:
        conflicts.append({
            "kind": "compensatory_hypothesis_conflict",
            "reason": (
                "crowded_single_assignments_and_absorbed_crown_core_"
                "jointly_restore_present_count"
            ),
            "crowded_pairs": crowded_single_assignment_pairs,
            "absorbed_core_records": absorbed_independent_core_records,
        })

    bilateral_area_ratios: dict[str, float] = {}
    checked_pairs: set[tuple[int, int]] = set()
    for fdi, region in region_by_fdi.items():
        quadrant, tooth = divmod(int(fdi), 10)
        counterpart_quadrant = {1: 2, 2: 1, 3: 4, 4: 3}.get(quadrant)
        counterpart = (
            counterpart_quadrant * 10 + tooth
            if counterpart_quadrant is not None else None
        )
        if counterpart not in region_by_fdi:
            continue
        pair = tuple(sorted((fdi, int(counterpart))))
        if pair in checked_pairs:
            continue
        checked_pairs.add(pair)
        other = region_by_fdi[int(counterpart)]
        ratio = max(region.area_mm2, other.area_mm2) / max(
            min(region.area_mm2, other.area_mm2), 1.0e-9
        )
        bilateral_area_ratios[f"{pair[0]}-{pair[1]}"] = float(ratio)
        larger_fdi = fdi if region.area_mm2 >= other.area_mm2 else int(counterpart)
        if ratio >= maximum_bilateral_region_area_ratio:
            conflicts.append({
                "kind": "semantic_geometry_conflict",
                "reason": "region_is_bilateral_area_outlier",
                "FDI": int(larger_fdi),
                "counterpart_FDI": int(pair[0] if larger_fdi == pair[1] else pair[1]),
                "area_ratio": float(ratio),
            })

    compensation = bool(split_hypotheses and merge_hypotheses)
    unproven_split_assignments = [
        item for item in assignments
        if item.kind == "split"
        and (
            item.independent_subbasin_count < 2
            or item.subbasin_persistence < minimum_reference_persistence
        )
    ]
    if unproven_split_assignments:
        conflicts.append({
            "kind": "unresolved_single_or_multiple",
            "reason": "split_has_no_persistent_independent_crown_basins",
            "FDI": [int(item.fdi) for item in unproven_split_assignments],
            "hypotheses": sorted({
                str(item.hypothesis_id) for item in unproven_split_assignments
            }),
        })
    if compensation and (weak_split_records or unproven_split_assignments):
        conflicts.append({
            "kind": "compensatory_hypothesis_conflict",
            "reason": (
                "merge_and_unproven_split_jointly_restore_present_count"
            ),
            "split_hypotheses": sorted(str(item) for item in split_hypotheses),
            "merge_hypotheses": sorted(str(item) for item in merge_hypotheses),
        })

    return {
        "conflicts": conflicts,
        "conflict_count": len(conflicts),
        "safe_physical_hypothesis_path": not bool(conflicts),
        "split_hypotheses": sorted(str(item) for item in split_hypotheses),
        "merge_hypotheses": sorted(str(item) for item in merge_hypotheses),
        "compensatory_merge_split_topology": compensation,
        "implicit_compensatory_single_topology": implicit_compensation,
        "crowded_single_assignment_pairs": crowded_single_assignment_pairs,
        "absorbed_independent_crown_core_records": (
            absorbed_independent_core_records
        ),
        "weak_split_boundary_records": weak_split_records,
        "unproven_split_FDI": [
            int(item.fdi) for item in unproven_split_assignments
        ],
        "surface_valley_resolved_pairs": [
            {"component_id": component_id, "pair_index": pair_index}
            for component_id, pair_index in sorted(accepted_valley_pairs)
        ],
        "match_cost_outlier_FDI": cost_outlier_fdi,
        "diagnostic_only_single_match_cost_outlier_FDI": (
            diagnostic_only_single_cost_outlier_fdi
        ),
        "low_relative_3d_crown_support_FDI": low_crown_support_fdi,
        "low_relative_3d_crown_support_records": low_crown_support_records,
        "case_reference_crown_height_mm": reference_height,
        "case_reference_relief_quality": reference_relief_quality,
        "bilateral_area_ratio": bilateral_area_ratios,
        "track_count": len(tracks),
    }


def _load_mesh(path: Path) -> trimesh.Trimesh:
    """算法说明。"""
    loaded = trimesh.load(path, force="mesh", process=True)
    if isinstance(loaded, trimesh.Scene):
        loaded = trimesh.util.concatenate(tuple(loaded.geometry.values()))
    if not isinstance(loaded, trimesh.Trimesh) or loaded.is_empty:
        raise RuntimeError(f"could not load mesh: {path}")
    loaded.remove_unreferenced_vertices()
    return loaded


def _resolve_candidate_path(case_dir: Path, node: dict[str, object], name: str) -> Path:
    """算法说明。"""
    if node.get("path") is not None:
        return resolve_case_path(case_dir, node, name)
    for raw in node.get("candidate_files", []) or []:
        path = (case_dir / str(raw)).resolve()
        if path.is_file():
            return path
    raise RuntimeError(f"objects.{name} has no existing path or candidate file")


def _present_order(semantics) -> tuple[int, ...]:
    """算法说明。"""
    return tuple(
        fdi for fdi in semantics.fdi_order if fdi in semantics.present_teeth
    )


def _refine_split_seeds_from_regions(
    path: AlignmentPath,
    regions: list[ToothRegion],
    label_grid: np.ndarray,
    maps: dict[str, object],
    frame: ArchFrame,
) -> AlignmentPath:
    """算法说明。 Refine synthetic split seeds from their complete physical support union."""

    assignments = list(path.assignments)
    index_by_fdi = {item.fdi: index for index, item in enumerate(assignments)}
    region_by_fdi = {item.fdi: item for item in regions}
    split_groups: dict[str, list[int]] = {}
    for assignment in assignments:
        if assignment.kind == "split" and assignment.hypothesis_id is not None:
            split_groups.setdefault(assignment.hypothesis_id, []).append(assignment.fdi)
    lr = np.asarray(maps["lr_centres"], dtype=float)
    ap = np.asarray(maps["ap_centres"], dtype=float)
    for fdis in split_groups.values():
        if len(fdis) < 2 or any(fdi not in region_by_fdi for fdi in fdis):
            continue
        region_ids = [region_by_fdi[fdi].region_id for fdi in fdis]
        union = np.isin(label_grid, region_ids)
        rows, columns = np.nonzero(union)
        if len(rows) < 2 * 30:
            continue
        ordered_assignments = [assignments[index_by_fdi[fdi]] for fdi in fdis]
        first = np.asarray(ordered_assignments[0].center_lr_ap_mm, dtype=float)
        last = np.asarray(ordered_assignments[-1].center_lr_ap_mm, dtype=float)
        original_direction = last - first
        mean_s = float(np.mean([
            item.s_mm for item in ordered_assignments if item.s_mm is not None
        ]))
        tangent_step = max(1.0, 0.25 * frame.scale_at_s(mean_s))
        direction = (
            frame.at_s(mean_s + tangent_step)
            - frame.at_s(mean_s - tangent_step)
        )
        if float(np.linalg.norm(direction)) <= 1.0e-6:
            continue
        direction /= np.linalg.norm(direction)
        if float(original_direction @ direction) < 0.0:
            direction = -direction
        points = np.column_stack([lr[rows], ap[columns]])
        coordinate = points @ direction
        cuts = np.quantile(coordinate, np.linspace(0.0, 1.0, len(fdis) + 1))
        for group_index, fdi in enumerate(fdis):
            selected = (
                (coordinate >= cuts[group_index] - 1.0e-9)
                & (coordinate <= cuts[group_index + 1] + 1.0e-9)
            )
            if not np.any(selected):
                continue
            selected_rows = rows[selected]
            selected_columns = columns[selected]
            selected_points = np.column_stack([
                lr[selected_rows], ap[selected_columns]
            ])
            target = np.mean(selected_points, axis=0)
            center = selected_points[int(np.argmin(
                np.linalg.norm(selected_points - target, axis=1)
            ))]
            s_mm, _ = frame.project_lr_ap(center)
            assignment_index = index_by_fdi[fdi]
            assignments[assignment_index] = replace(
                assignments[assignment_index],
                center_lr_ap_mm=(float(center[0]), float(center[1])),
                s_mm=float(s_mm),
            )
    return replace(path, assignments=tuple(assignments))


def _run_core(
    dental,
    guide,
    anatomy,
    semantics,
    profile,
    missing_slot_anchors: tuple[LabeledMissingSlotAnchor, ...] = (),
) -> _CoreRun:
    """算法说明。"""
    surface_valleys = None
    if profile.surface_valley_evidence_enabled:
        surface_valleys = build_surface_valley_evidence(
            np.asarray(dental.vertices, dtype=float),
            np.asarray(dental.faces, dtype=np.int64),
            np.asarray(dental.vertex_normals, dtype=float),
            normalization_scale_mm=(
                profile.surface_valley_normalization_scale_mm
            ),
            smoothing_iterations=(
                profile.surface_valley_smoothing_iterations
            ),
        )
    frame_candidates = build_arch_frame_candidates(
        dental,
        guide,
        anatomy,
        crown_quantile=min(profile.height_quantiles),
        minimum_normal_dot=profile.minimum_normal_dot,
    )
    frame_anchor_evaluations = [
        evaluate_anchor_frame(
            frame, missing_slot_anchors, semantics.fdi_order
        )
        for frame in frame_candidates
    ]
    compatible_frame_names = {
        str(item["orientation"])
        for item in frame_anchor_evaluations if bool(item["compatible"])
    }
    frame_constraint_applied = bool(
        missing_slot_anchors and compatible_frame_names
    )
    if frame_constraint_applied:
        frame_candidates = [
            frame for frame in frame_candidates
            if frame.orientation_name in compatible_frame_names
        ]

    orientations: list[_OrientationRun] = []
    alignment_inputs = []
    for frame in frame_candidates:
        maps_by_quantile = render_multiscale_maps(
            dental, frame, profile, surface_valleys
        )
        tracks, refined_frame, _ = detect_core_tracks(
            maps_by_quantile, frame, profile
        )
        if not tracks:
            continue
        hypotheses = build_crown_hypotheses(
            tracks,
            refined_frame,
            maps_by_quantile,
            minimum_single_persistence=profile.minimum_track_persistence,
            minimum_independent_core_separation_scale=(
                profile.minimum_independent_core_separation_scale
            ),
            minimum_surface_valley_mean_support=(
                profile.minimum_surface_valley_mean_support
            ),
            minimum_surface_valley_coverage=(
                profile.minimum_surface_valley_coverage
            ),
        )
        orientation = _OrientationRun(
            refined_frame, tracks, hypotheses, maps_by_quantile
        )
        orientations.append(orientation)
        alignment_inputs.append((refined_frame, tracks, hypotheses))
    if not orientations:
        raise RuntimeError("no orientation produced a physical crown candidate")
    present_order = _present_order(semantics)
    general_ranked = rank_monotone_fdi_alignments(
        alignment_inputs,
        present_order,
        semantics.jaw,
        missing_fdis=tuple(
            fdi for fdi in semantics.fdi_order if fdi in semantics.missing_teeth
        ),
        midline_offset_search_local_scale=(
            profile.midline_offset_search_local_scale
        ),
    )
    # Physical instances must be explained without creating teeth first.
    # Automatic split hypotheses are intentionally a fallback family because
    # broadness alone is not proof of two simultaneous crown basins.  This
    # also prevents low-cost split/merge compensation paths from pruning a
    # valid single/merge/artifact alignment out of the global beam.
    conservative_inputs = [
        (
            frame,
            tracks,
            [item for item in hypotheses if item.kind != "split"],
        )
        for frame, tracks, hypotheses in alignment_inputs
    ]
    conservative_ranked = rank_monotone_fdi_alignments(
        conservative_inputs,
        present_order,
        semantics.jaw,
        missing_fdis=tuple(
            fdi for fdi in semantics.fdi_order if fdi in semantics.missing_teeth
        ),
        midline_offset_search_local_scale=(
            profile.midline_offset_search_local_scale
        ),
    )
    ranked: list[AlignmentPath] = []
    seen_signatures: set[tuple[str, ...]] = set()
    for candidate in [*conservative_ranked, *general_ranked]:
        if candidate.signature in seen_signatures:
            continue
        seen_signatures.add(candidate.signature)
        ranked.append(candidate)
    anchor_path_evaluations: list[dict[str, object]] = []
    anchor_compatible_ranked: list[AlignmentPath] = []
    if missing_slot_anchors:
        frame_by_name = {
            item.frame.orientation_name: item.frame for item in orientations
        }
        for candidate in ranked:
            evaluation = evaluate_anchor_alignment(
                candidate,
                frame_by_name[candidate.orientation_name],
                missing_slot_anchors,
                semantics.fdi_order,
            )
            anchor_path_evaluations.append(evaluation)
            if bool(evaluation["compatible"]):
                anchor_compatible_ranked.append(candidate)
    path_constraint_applied = bool(
        missing_slot_anchors and anchor_compatible_ranked
    )
    if path_constraint_applied:
        ranked = anchor_compatible_ranked
    ranked, diagnostic_only_incomplete_paths, present_constraint_applied = (
        _prioritize_complete_present_paths(ranked)
    )
    feasible = []
    fallback = None
    fallback_key = None
    rejected: list[dict[str, object]] = [
        {
            "total_cost": item.total_cost,
            "signature": list(item.signature),
            "reason": "diagnostic_only_undetected_present_FDI",
            "undetected_FDI": list(item.undetected_fdi),
        }
        for item in diagnostic_only_incomplete_paths
    ]
    rejected_path_objects: list[AlignmentPath] = []
    equivalent_paths: list[dict[str, object]] = []
    multiview_by_orientation: dict[str, MultiViewBoundaryEvidence] = {}
    for candidate in ranked:
        selected = next(
            item for item in orientations
            if item.frame.orientation_name == candidate.orientation_name
        )
        try:
            quantile, partition_maps = choose_partition_map(
                selected.maps_by_quantile, candidate.assignments
            )
            partition_maps = resample_partition_maps(
                partition_maps,
                profile.component_segmentation_resolution_mm,
            )
            if profile.multi_view_boundary_enabled:
                evidence = multiview_by_orientation.get(
                    selected.frame.orientation_name
                )
                if evidence is None:
                    evidence = build_multiview_boundary_evidence(
                        dental,
                        selected.frame,
                        surface_valleys=surface_valleys,
                        azimuth_count=profile.multi_view_azimuth_count,
                        obliquity_degrees=profile.multi_view_obliquity_degrees,
                        resolution_mm=profile.multi_view_resolution_mm,
                        edge_support_quantile=(
                            profile.multi_view_edge_support_quantile
                        ),
                    )
                    multiview_by_orientation[
                        selected.frame.orientation_name
                    ] = evidence
                boundary, consistency = resample_occlusal_evidence(
                    evidence,
                    np.asarray(partition_maps["lr_centres"], dtype=float),
                    np.asarray(partition_maps["ap_centres"], dtype=float),
                )
                partition_maps = dict(partition_maps)
                partition_maps["multi_view_boundary_score"] = boundary
                partition_maps["multi_view_consistency"] = consistency
            regions, segmentation, label_grid = segment_component_local_regions(
                alignment=candidate,
                frame=selected.frame,
                maps=partition_maps,
                boundary_smoothing_scale=1.0,
                unassigned_relief_quantile=profile.unassigned_relief_quantile,
                unassigned_seed_protection_scale=(
                    profile.unassigned_seed_protection_scale
                ),
                minimum_unassigned_area_mm2=profile.minimum_unassigned_area_mm2,
                surface_valley_watershed_weight=(
                    profile.surface_valley_watershed_weight
                ),
                minimum_surface_valley_mean_support=(
                    profile.minimum_surface_valley_mean_support
                ),
                minimum_surface_valley_coverage=(
                    profile.minimum_surface_valley_coverage
                ),
                multi_view_watershed_weight=(
                    profile.multi_view_watershed_weight
                ),
                boundary_first_segmentation=(
                    profile.boundary_first_segmentation
                ),
                # V2.17 3-D/basin evidence is recorded for QA only.  It is not
                # yet allowed to reject a global alignment path.
                require_anatomical_split_evidence=False,
            )
            refined_candidate = _refine_split_seeds_from_regions(
                candidate,
                regions,
                label_grid,
                partition_maps,
                selected.frame,
            )
            if refined_candidate.assignments != candidate.assignments:
                candidate = refined_candidate
                regions, segmentation, label_grid = segment_component_local_regions(
                    alignment=candidate,
                    frame=selected.frame,
                    maps=partition_maps,
                    boundary_smoothing_scale=1.0,
                    unassigned_relief_quantile=profile.unassigned_relief_quantile,
                    unassigned_seed_protection_scale=(
                        profile.unassigned_seed_protection_scale
                    ),
                    minimum_unassigned_area_mm2=profile.minimum_unassigned_area_mm2,
                    surface_valley_watershed_weight=(
                        profile.surface_valley_watershed_weight
                    ),
                    minimum_surface_valley_mean_support=(
                        profile.minimum_surface_valley_mean_support
                    ),
                    minimum_surface_valley_coverage=(
                        profile.minimum_surface_valley_coverage
                    ),
                    multi_view_watershed_weight=(
                        profile.multi_view_watershed_weight
                    ),
                    boundary_first_segmentation=(
                        profile.boundary_first_segmentation
                    ),
                    require_anatomical_split_evidence=False,
                )
        except Exception as error:
            rejected_path_objects.append(candidate)
            rejected.append({
                "total_cost": candidate.total_cost,
                "signature": list(candidate.signature),
                "reason": f"component_local_segmentation_error: {error}",
            })
            continue
        structural_diagnostics = _structural_evidence_diagnostics(
            candidate,
            regions,
            segmentation,
            selected.tracks,
            maximum_local_assignment_robust_z=(
                profile.maximum_local_assignment_robust_z
            ),
            maximum_bilateral_region_area_ratio=(
                profile.maximum_bilateral_region_area_ratio
            ),
            minimum_reference_persistence=profile.minimum_track_persistence,
            minimum_relative_crown_height_ratio=(
                profile.minimum_relative_crown_height_ratio
            ),
            minimum_relative_relief_quality_ratio=(
                profile.minimum_relative_relief_quality_ratio
            ),
            label_grid=label_grid,
            partition_maps=partition_maps,
        )
        evaluated = (
            candidate, selected, quantile, partition_maps,
            regions, segmentation, label_grid, structural_diagnostics,
        )
        physical_fdi = tuple(item.fdi for item in regions)
        minimum_interior_radius_mm = (
            3.0 * float(partition_maps["resolution_mm"])
        )
        nondegenerate = all(
            item.pixel_count > 30
            and len(item.contour_lr_ap_mm) > 8
            and item.maximum_interior_radius_mm >= minimum_interior_radius_mm
            for item in regions
        )
        reasons = []
        if candidate.undetected_fdi:
            reasons.append("undetected_present_FDI")
        if physical_fdi != present_order:
            reasons.append("incomplete_or_nonmonotone_physical_regions")
        if not nondegenerate:
            reasons.append("empty_or_degenerate_physical_region")
        if not structural_diagnostics["safe_physical_hypothesis_path"]:
            reasons.append("physical_hypothesis_evidence_is_unresolved")
        degenerate_region_count = sum(
            item.pixel_count <= 30
            or len(item.contour_lr_ap_mm) <= 8
            or item.maximum_interior_radius_mm < minimum_interior_radius_mm
            for item in regions
        )
        diagnostic_key = (
            int(bool(candidate.undetected_fdi)),
            int(physical_fdi != present_order),
            int(degenerate_region_count),
            len(structural_diagnostics["conflicts"]),
            float(candidate.total_cost),
        )
        if fallback_key is None or diagnostic_key < fallback_key:
            fallback = evaluated
            fallback_key = diagnostic_key
        if reasons:
            smallest_region = min(
                regions, key=lambda item: item.maximum_interior_radius_mm,
                default=None,
            )
            rejected_path_objects.append(candidate)
            rejected.append({
                "total_cost": candidate.total_cost,
                "signature": list(candidate.signature),
                "reason": ", ".join(reasons),
                "smallest_region_FDI": (
                    smallest_region.fdi if smallest_region is not None else None
                ),
                "smallest_region_interior_radius_mm": (
                    smallest_region.maximum_interior_radius_mm
                    if smallest_region is not None else None
                ),
                "smallest_region_area_mm2": (
                    smallest_region.area_mm2 if smallest_region is not None else None
                ),
                "structural_conflicts": structural_diagnostics["conflicts"],
            })
            continue
        if feasible:
            reference = feasible[0]
            reference_regions = reference[4]
            reference_by_fdi = {item.fdi: item for item in reference_regions}
            candidate_by_fdi = {item.fdi: item for item in regions}
            equivalent = set(reference_by_fdi) == set(candidate_by_fdi)
            centroid_shifts = []
            ious = []
            if equivalent:
                centroid_shifts = [
                    float(np.linalg.norm(
                        np.asarray(reference_by_fdi[fdi].area_centroid_lr_ap_mm)
                        - np.asarray(candidate_by_fdi[fdi].area_centroid_lr_ap_mm)
                    ))
                    for fdi in sorted(reference_by_fdi)
                ]
                reference_lr = np.asarray(reference[3]["lr_centres"], dtype=float)
                reference_ap = np.asarray(reference[3]["ap_centres"], dtype=float)
                reference_masks = _region_masks_on_grid(
                    reference_regions, reference_lr, reference_ap
                )
                candidate_masks = _region_masks_on_grid(
                    regions, reference_lr, reference_ap
                )
                for fdi in sorted(reference_masks):
                    union = np.count_nonzero(
                        reference_masks[fdi] | candidate_masks[fdi]
                    )
                    intersection = np.count_nonzero(
                        reference_masks[fdi] & candidate_masks[fdi]
                    )
                    ious.append(float(intersection / max(union, 1)))
                equivalent = (
                    max(centroid_shifts, default=float("inf")) <= 0.50
                    and min(ious, default=0.0) >= 0.90
                )
            if equivalent:
                equivalent_paths.append({
                    "total_cost": candidate.total_cost,
                    "signature": list(candidate.signature),
                    "maximum_centroid_shift_mm": max(centroid_shifts, default=0.0),
                    "minimum_region_IoU": min(ious, default=1.0),
                })
                continue
        feasible.append(evaluated)
        if len(feasible) >= 2:
            break
    if feasible:
        chosen = feasible[0]
    elif fallback is not None:
        chosen = fallback
    else:
        raise RuntimeError("no alignment path could be segmented component-locally")
    (
        best, selected, quantile, partition_maps, regions, segmentation,
        label_grid, structural_diagnostics,
    ) = chosen
    if len(feasible) > 1:
        second = feasible[1][0]
        second_is_feasible = True
        margin_mode = "best_vs_second_feasible_path_cost_per_present_tooth"
        margin = (
            second.total_cost - best.total_cost
        ) / max(len(present_order), 1)
    else:
        second = rejected_path_objects[0] if rejected_path_objects else None
        second_is_feasible = False
        margin_mode = "unique_feasible_path_cost_margin_not_computable"
        margin = None
    orientation_alternative = next(
        (
            item for item in ranked
            if item.orientation_name != best.orientation_name
        ),
        None,
    )
    orientation_margin = (
        (orientation_alternative.total_cost - best.total_cost)
        / max(len(present_order), 1)
        if orientation_alternative is not None else None
    )
    counterfactual_margins = _counterfactual_margins(best, ranked)
    selected_frame_evaluation = next((
        item for item in frame_anchor_evaluations
        if item["orientation"] == best.orientation_name
    ), {
        "orientation": best.orientation_name,
        "anchors": [],
        "violations": [],
        "compatible": not bool(missing_slot_anchors),
    })
    selected_path_evaluation = evaluate_anchor_alignment(
        best,
        selected.frame,
        missing_slot_anchors,
        semantics.fdi_order,
    )
    anchor_constraints_satisfied = bool(
        not missing_slot_anchors
        or (
            selected_frame_evaluation["compatible"]
            and selected_path_evaluation["compatible"]
        )
    )
    missing_slot_anchor_diagnostics = {
        "enabled": bool(missing_slot_anchors),
        "anchor_count": len(missing_slot_anchors),
        "frame_constraint_applied": frame_constraint_applied,
        "path_constraint_applied": path_constraint_applied,
        "constraints_satisfied": anchor_constraints_satisfied,
        "frame_evaluations": frame_anchor_evaluations,
        "selected_frame_evaluation": selected_frame_evaluation,
        "selected_path_evaluation": selected_path_evaluation,
        "rejected_path_count": (
            len(anchor_path_evaluations) - len(anchor_compatible_ranked)
        ),
        "fallback_reason": (
            None if anchor_constraints_satisfied
            else "no_coordinate_or_alignment_path_satisfied_all_sleeve_anchors"
        ),
    }
    present_fdi_constraint_diagnostics = {
        "mode": "hard_primary_path_set_with_diagnostic_undetected_fallback",
        "complete_path_available": present_constraint_applied,
        "hard_constraint_applied": present_constraint_applied,
        "selected_path_is_complete": not bool(best.undetected_fdi),
        "diagnostic_only_incomplete_path_count": len(
            diagnostic_only_incomplete_paths
        ),
        "selected_undetected_FDI": list(best.undetected_fdi),
        "fallback_reason": (
            None if not best.undetected_fdi
            else "no_complete_monotone_candidate_path_was_available"
        ),
    }
    return _CoreRun(
        selected,
        best,
        second,
        margin,
        quantile,
        partition_maps,
        regions,
        segmentation,
        label_grid,
        tuple(rejected),
        second_is_feasible,
        margin_mode,
        tuple(equivalent_paths),
        (
            float(orientation_alternative.total_cost)
            if orientation_alternative is not None else None
        ),
        float(orientation_margin) if orientation_margin is not None else None,
        structural_diagnostics,
        counterfactual_margins,
        surface_valleys,
        missing_slot_anchor_diagnostics,
        present_fdi_constraint_diagnostics,
        multiview_evidence=multiview_by_orientation.get(
            selected.frame.orientation_name
        ),
    )


def _region_masks_on_grid(
    regions: list[ToothRegion], lr: np.ndarray, ap: np.ndarray
) -> dict[int, np.ndarray]:
    """算法说明。"""
    masks: dict[int, np.ndarray] = {}
    for region in regions:
        points = np.asarray(region.contour_lr_ap_mm, dtype=float)
        mask = np.zeros((len(lr), len(ap)), dtype=bool)
        if len(points) >= 3:
            rows = np.interp(points[:, 0], lr, np.arange(len(lr)))
            columns = np.interp(points[:, 1], ap, np.arange(len(ap)))
            rr, cc = polygon(rows, columns, shape=mask.shape)
            mask[rr, cc] = True
        masks[region.fdi] = mask
    return masks


def _refine_core_with_multiview_boundary(
    core: _CoreRun,
    evidence: MultiViewBoundaryEvidence,
    profile,
) -> _CoreRun:
    """内部算法说明。 Refine only the chosen component-local regions with multi-view evidence.

    Alignment, hypotheses, atomic-core consumption and FDI order are immutable
    here.  Consequently an image response cannot create a tooth or repair an
    invalid semantic path; it can only alter the finite boundary between two
    already matched seeds in the same connected component.
    """

    maps = dict(core.partition_maps)
    boundary, consistency = resample_occlusal_evidence(
        evidence,
        np.asarray(maps["lr_centres"], dtype=float),
        np.asarray(maps["ap_centres"], dtype=float),
    )
    maps["multi_view_boundary_score"] = boundary
    maps["multi_view_consistency"] = consistency
    regions, segmentation, labels = segment_component_local_regions(
        alignment=core.alignment,
        frame=core.orientation.frame,
        maps=maps,
        boundary_smoothing_scale=1.0,
        unassigned_relief_quantile=profile.unassigned_relief_quantile,
        unassigned_seed_protection_scale=(
            profile.unassigned_seed_protection_scale
        ),
        minimum_unassigned_area_mm2=profile.minimum_unassigned_area_mm2,
        surface_valley_watershed_weight=(
            profile.surface_valley_watershed_weight
        ),
        minimum_surface_valley_mean_support=(
            profile.minimum_surface_valley_mean_support
        ),
        minimum_surface_valley_coverage=(
            profile.minimum_surface_valley_coverage
        ),
        multi_view_watershed_weight=profile.multi_view_watershed_weight,
        boundary_first_segmentation=profile.boundary_first_segmentation,
        require_anatomical_split_evidence=False,
    )
    structural = _structural_evidence_diagnostics(
        core.alignment,
        regions,
        segmentation,
        core.orientation.tracks,
        maximum_local_assignment_robust_z=(
            profile.maximum_local_assignment_robust_z
        ),
        maximum_bilateral_region_area_ratio=(
            profile.maximum_bilateral_region_area_ratio
        ),
        minimum_reference_persistence=profile.minimum_track_persistence,
        minimum_relative_crown_height_ratio=(
            profile.minimum_relative_crown_height_ratio
        ),
        minimum_relative_relief_quality_ratio=(
            profile.minimum_relative_relief_quality_ratio
        ),
        label_grid=labels,
        partition_maps=maps,
    )
    return replace(
        core,
        partition_maps=maps,
        regions=regions,
        segmentation=segmentation,
        label_grid=labels,
        structural_diagnostics=structural,
    )


def _compare_runs(base: _CoreRun, other: _CoreRun) -> dict[str, object]:
    """算法说明。"""
    base_by_fdi = {item.fdi: item for item in base.regions}
    other_by_fdi = {item.fdi: item for item in other.regions}
    same_fdi = set(base_by_fdi) == set(other_by_fdi)
    def physical_topology(run: _CoreRun) -> tuple[tuple[int, ...], ...]:
        """算法说明。 Return FDI grouping by final connected physical component."""

        groups: list[list[int]] = []
        component_to_group: dict[int, int] = {}
        for region in run.regions:
            existing = {
                component_to_group[component_id]
                for component_id in region.component_ids
                if component_id in component_to_group
            }
            if existing:
                group_index = min(existing)
            else:
                group_index = len(groups)
                groups.append([])
            groups[group_index].append(region.fdi)
            for component_id in region.component_ids:
                component_to_group[component_id] = group_index
        return tuple(tuple(group) for group in groups if group)

    topology = physical_topology(base) == physical_topology(other)
    centroid_shifts = []
    if same_fdi:
        centroid_shifts = [
            float(np.linalg.norm(
                np.asarray(base_by_fdi[fdi].area_centroid_lr_ap_mm)
                - np.asarray(other_by_fdi[fdi].area_centroid_lr_ap_mm)
            ))
            for fdi in sorted(base_by_fdi)
        ]
    lr = np.asarray(base.partition_maps["lr_centres"], dtype=float)
    ap = np.asarray(base.partition_maps["ap_centres"], dtype=float)
    other_lr = np.asarray(other.partition_maps["lr_centres"], dtype=float)
    other_ap = np.asarray(other.partition_maps["ap_centres"], dtype=float)
    same_grid = (
        base.label_grid.shape == other.label_grid.shape
        and lr.shape == other_lr.shape
        and ap.shape == other_ap.shape
        and np.allclose(lr, other_lr, atol=1.0e-7)
        and np.allclose(ap, other_ap, atol=1.0e-7)
    )
    if same_grid:
        base_masks = {
            item.fdi: base.label_grid == item.region_id for item in base.regions
        }
        other_masks = {
            item.fdi: other.label_grid == item.region_id for item in other.regions
        }
    else:
        base_masks = _region_masks_on_grid(base.regions, lr, ap)
        other_masks = _region_masks_on_grid(other.regions, lr, ap)
    ious = []
    iou_by_fdi: dict[str, float] = {}
    if same_fdi:
        for fdi in sorted(base_masks):
            union = np.count_nonzero(base_masks[fdi] | other_masks[fdi])
            intersection = np.count_nonzero(base_masks[fdi] & other_masks[fdi])
            value = float(intersection / max(union, 1))
            ious.append(value)
            iou_by_fdi[str(fdi)] = value
    return {
        "same_FDI_set": same_fdi,
        "same_hypothesis_topology": topology,
        "maximum_centroid_shift_mm": max(centroid_shifts, default=float("inf")),
        "minimum_region_IoU": min(ious, default=0.0),
        "region_IoU_by_FDI": iou_by_fdi,
        "base_partition_quantile": base.partition_quantile,
        "variant_partition_quantile": other.partition_quantile,
        "base_assignment_topology": [
            [item.fdi, item.kind] for item in base.alignment.assignments
        ],
        "variant_assignment_topology": [
            [item.fdi, item.kind] for item in other.alignment.assignments
        ],
        "base_assignment_centers_lr_ap_mm": {
            str(item.fdi): (
                list(item.center_lr_ap_mm)
                if item.center_lr_ap_mm is not None else None
            ) for item in base.alignment.assignments
        },
        "variant_assignment_centers_lr_ap_mm": {
            str(item.fdi): (
                list(item.center_lr_ap_mm)
                if item.center_lr_ap_mm is not None else None
            ) for item in other.alignment.assignments
        },
        "base_physical_hypothesis_topology": [
            list(group) for group in physical_topology(base)
        ],
        "variant_physical_hypothesis_topology": [
            list(group) for group in physical_topology(other)
        ],
        "base_region_centroids_lr_ap_mm": {
            str(item.fdi): list(item.area_centroid_lr_ap_mm) for item in base.regions
        },
        "variant_region_centroids_lr_ap_mm": {
            str(item.fdi): list(item.area_centroid_lr_ap_mm) for item in other.regions
        },
        "base_segmentation_diagnostics": asdict(base.segmentation),
        "variant_segmentation_diagnostics": asdict(other.segmentation),
    }


def _stability(
    dental,
    guide,
    anatomy,
    semantics,
    profile,
    base: _CoreRun,
    multiview_evidence: MultiViewBoundaryEvidence | None = None,
    missing_slot_anchors: tuple[LabeledMissingSlotAnchor, ...] = (),
):
    """算法说明。"""
    records = []
    for resolution in profile.stability_resolutions_mm:
        if abs(resolution - profile.projection_resolution_mm) < 1.0e-9:
            continue
        variant_profile = replace(
            profile,
            projection_resolution_mm=resolution,
            run_stability=False,
        )
        try:
            variant = _run_core(
                dental,
                guide,
                anatomy,
                semantics,
                variant_profile,
                missing_slot_anchors,
            )
            if (
                multiview_evidence is not None
                and variant.multiview_evidence is None
            ):
                variant = _refine_core_with_multiview_boundary(
                    variant, multiview_evidence, variant_profile
                )
            comparison = _compare_runs(base, variant)
            records.append({"kind": "resolution", "value": resolution, **comparison})
        except Exception as error:
            records.append({
                "kind": "resolution", "value": resolution,
                "same_FDI_set": False, "same_hypothesis_topology": False,
                "maximum_centroid_shift_mm": float("inf"),
                "minimum_region_IoU": 0.0, "error": str(error),
            })
    for scale in profile.boundary_smoothing_scales:
        if abs(scale - 1.0) < 1.0e-9:
            continue
        try:
            regions, segmentation, labels = segment_component_local_regions(
                alignment=base.alignment,
                frame=base.orientation.frame,
                maps=base.partition_maps,
                boundary_smoothing_scale=scale,
                unassigned_relief_quantile=profile.unassigned_relief_quantile,
                unassigned_seed_protection_scale=(
                    profile.unassigned_seed_protection_scale
                ),
                minimum_unassigned_area_mm2=profile.minimum_unassigned_area_mm2,
                surface_valley_watershed_weight=(
                    profile.surface_valley_watershed_weight
                ),
                minimum_surface_valley_mean_support=(
                    profile.minimum_surface_valley_mean_support
                ),
                minimum_surface_valley_coverage=(
                    profile.minimum_surface_valley_coverage
                ),
                multi_view_watershed_weight=(
                    profile.multi_view_watershed_weight
                ),
                boundary_first_segmentation=(
                    profile.boundary_first_segmentation
                ),
                require_anatomical_split_evidence=False,
            )
            variant = replace(
                base, regions=regions, segmentation=segmentation, label_grid=labels
            )
            records.append({
                "kind": "boundary_smoothing", "value": scale,
                **_compare_runs(base, variant),
            })
        except Exception as error:
            records.append({
                "kind": "boundary_smoothing", "value": scale,
                "same_FDI_set": False, "same_hypothesis_topology": False,
                "maximum_centroid_shift_mm": float("inf"),
                "minimum_region_IoU": 0.0, "error": str(error),
            })
    stable = all(
        item["same_FDI_set"]
        and item["same_hypothesis_topology"]
        and float(item["maximum_centroid_shift_mm"]) <= 1.0
        and float(item["minimum_region_IoU"]) >= 0.75
        for item in records
    )
    score = float(np.mean([
        min(float(item["minimum_region_IoU"]), 1.0)
        * max(0.0, 1.0 - float(item["maximum_centroid_shift_mm"]) / 2.0)
        for item in records
    ])) if records else 1.0
    return records, stable, score


def _extent(maps):
    """算法说明。"""
    return [
        float(maps["lr_centres"][0]), float(maps["lr_centres"][-1]),
        float(maps["ap_centres"][0]), float(maps["ap_centres"][-1]),
    ]


def _image(axis, values, maps, title, cmap=None, vmin=None, vmax=None):
    """算法说明。"""
    array = np.asarray(values)
    displayed = array.transpose(1, 0, 2) if array.ndim == 3 else array.T
    artist = axis.imshow(
        displayed, origin="lower", extent=_extent(maps), interpolation="bilinear",
        cmap=cmap, vmin=vmin, vmax=vmax,
    )
    axis.set_aspect("equal", adjustable="box")
    axis.set_title(title)
    axis.set_xlabel("LR (mm)")
    return artist


def _save_multichannel(path: Path, maps, case_name: str):
    """算法说明。"""
    figure, axes = plt.subplots(2, 3, figsize=(18, 11), constrained_layout=True)
    _image(axes[0, 0], maps["silhouette"], maps, "Continuous triangle silhouette", "gray_r", 0, 1)
    height = _image(
        axes[0, 1], maps["top_height_mm"], maps,
        "Highest-surface height (mm)", "viridis",
    )
    figure.colorbar(height, ax=axes[0, 1], shrink=0.76)
    _image(axes[0, 2], maps["normal_rgb"], maps, "Interpolated surface-normal map")
    edge = _image(
        axes[1, 0], maps["fused_edge"], maps,
        "Fused anatomical edge evidence", "magma", 0, 1,
    )
    figure.colorbar(edge, ax=axes[1, 0], shrink=0.76)
    relief = _image(
        axes[1, 1],
        maps.get("relative_crown_relief_mm", np.zeros_like(maps["silhouette"])),
        maps,
        "Relative crown relief (mm)",
        "viridis",
    )
    figure.colorbar(relief, ax=axes[1, 1], shrink=0.76)
    valley = _image(
        axes[1, 2],
        np.nan_to_num(
            maps.get("surface_valley_score", np.zeros_like(maps["silhouette"])),
            nan=0.0,
        ),
        maps,
        "3-D minimum-curvature valley evidence",
        "magma",
        0,
        1,
    )
    figure.colorbar(valley, ax=axes[1, 2], shrink=0.76)
    axes[0, 0].set_ylabel("AP (mm)")
    axes[1, 0].set_ylabel("AP (mm)")
    figure.suptitle(f"{case_name} — mapping v2.11 multi-channel crown projection")
    figure.savefig(path, dpi=220, facecolor="white")
    plt.close(figure)


def _save_multiview_boundary(
    path: Path,
    evidence: MultiViewBoundaryEvidence,
    case_name: str,
    pair_records: list[dict[str, object]] | None = None,
) -> None:
    """内部算法说明。 Save traceable diagnostic views without influencing FDI decisions."""

    occlusal = evidence.rasters[0]
    oblique = evidence.rasters[1:]
    selected = [
        oblique[index]
        for index in np.linspace(
            0, max(len(oblique) - 1, 0), min(3, len(oblique)), dtype=int
        )
    ] if oblique else []
    figure, axes = plt.subplots(2, 3, figsize=(18, 11), constrained_layout=True)

    def show(axis, raster, values, title, cmap="magma", vmin=0.0, vmax=1.0):
        """内部算法说明。"""
        displayed = np.asarray(values).T
        extent = [
            float(raster.x_centres_mm[0]),
            float(raster.x_centres_mm[-1]),
            float(raster.y_centres_mm[0]),
            float(raster.y_centres_mm[-1]),
        ]
        artist = axis.imshow(
            displayed,
            origin="lower",
            extent=extent,
            interpolation="bilinear",
            cmap=cmap,
            vmin=vmin,
            vmax=vmax,
        )
        axis.set_aspect("equal", adjustable="box")
        axis.set_title(title)
        return artist

    depth = show(
        axes[0, 0], occlusal, occlusal.top_depth_mm,
        "Occlusal visible depth", "viridis", None, None,
    )
    figure.colorbar(depth, ax=axes[0, 0], shrink=0.75)
    edge = show(
        axes[0, 1], occlusal, occlusal.boundary_score,
        "Occlusal single-view internal boundary",
    )
    figure.colorbar(edge, ax=axes[0, 1], shrink=0.75)
    aggregate = show(
        axes[0, 2], occlusal, evidence.occlusal_boundary_map,
        "Multi-view boundary evidence back-projected to occlusal surface",
    )
    for record in pair_records or []:
        midpoint = np.asarray(record["midpoint_lr_ap_mm"], dtype=float)
        score = float(record["tooth_tooth_boundary_score"])
        axes[0, 2].scatter(
            midpoint[0], midpoint[1], s=28 + 80 * score,
            c=[[score, 1.0 - score, 0.15]], edgecolor="white", linewidth=0.6,
        )
        axes[0, 2].text(
            midpoint[0], midpoint[1],
            f"{record['first_FDI']}|{record['second_FDI']}\n{score:.2f}",
            fontsize=6, color="white", ha="center", va="bottom",
        )
    figure.colorbar(aggregate, ax=axes[0, 2], shrink=0.75)
    consistency = show(
        axes[1, 0], occlusal, evidence.occlusal_consistency_map,
        "Supporting-view fraction on visible surface", "plasma",
    )
    figure.colorbar(consistency, ax=axes[1, 0], shrink=0.75)
    for axis, raster in zip(axes[1, 1:], selected[:2], strict=False):
        show(
            axis,
            raster,
            raster.boundary_score,
            f"{raster.frame.view_id}: internal boundary",
        )
    for axis in axes.ravel():
        if not axis.has_data():
            axis.axis("off")
    figure.suptitle(
        f"{case_name} — FDI New inter-core separator exclusion"
    )
    figure.savefig(path, dpi=200, facecolor="white")
    plt.close(figure)


def _save_mapping_preview(path: Path, core: _CoreRun, case_name: str):
    """算法说明。"""
    maps = core.partition_maps
    figure, axis = plt.subplots(figsize=(11, 9), constrained_layout=True)
    _image(axis, maps["silhouette"], maps, "", "gray_r", 0, 1)
    palette = plt.get_cmap("tab20")
    for index, region in enumerate(core.regions):
        contour = np.asarray(region.contour_lr_ap_mm)
        center = np.asarray(region.area_centroid_lr_ap_mm)
        color = palette(index % 20)
        if len(contour):
            axis.plot(contour[:, 0], contour[:, 1], color=color, linewidth=1.7)
        axis.scatter(*center, s=54, color=color, edgecolor="black", zorder=5)
        axis.text(
            center[0],
            center[1],
            str(region.fdi),
            ha="center",
            va="center",
            color="white",
            fontsize=12,
            weight="bold",
            zorder=7,
            bbox={
                "boxstyle": "round,pad=0.28",
                "facecolor": color,
                "edgecolor": "white",
                "linewidth": 1.0,
                "alpha": 0.96,
            },
        )
    for assignment in core.alignment.assignments:
        if assignment.center_lr_ap_mm is not None:
            axis.scatter(*assignment.center_lr_ap_mm, marker="x", s=38, color="#111827")
    selected_anchor_records = core.missing_slot_anchor_diagnostics.get(
        "selected_frame_evaluation", {}
    ).get("anchors", [])
    for record in selected_anchor_records:
        point = np.asarray(record["projected_lr_ap_mm"], dtype=float)
        axis.scatter(
            *point,
            marker="D",
            s=82,
            facecolor="#facc15",
            edgecolor="#111827",
            linewidth=1.2,
            zorder=8,
        )
        axis.text(
            point[0],
            point[1],
            f"S{record['fdi']}",
            ha="center",
            va="bottom",
            fontsize=9,
            weight="bold",
            color="#713f12",
            zorder=9,
        )
    axis.set_ylabel("AP (mm)")
    mapped_fdi = ", ".join(str(region.fdi) for region in core.regions)
    structural_state = (
        f"NEEDS REVIEW: undetected FDI {list(core.alignment.undetected_fdi)}"
        if core.alignment.undetected_fdi
        else (
            "geometry resolved"
            if core.structural_diagnostics["safe_physical_hypothesis_path"]
            else "NEEDS REVIEW: geometry/semantic conflict"
        )
    )
    axis.set_title(
        f"{case_name} — monotone FDI alignment and component-local regions\n"
        f"Mapped FDI: {mapped_fdi}; gingiva/unassigned: "
        f"{core.segmentation.unassigned_area_mm2:.2f} mm²; {structural_state}"
    )
    figure.savefig(path, dpi=240, facecolor="white")
    plt.close(figure)


def _alignment_record(path: AlignmentPath | None) -> dict[str, object] | None:
    """算法说明。"""
    if path is None:
        return None
    return {
        "orientation": path.orientation_name,
        "global_scale": path.global_scale,
        "midline_offset_mm": path.midline_offset_mm,
        "total_cost": path.total_cost,
        "artifact_core_ids": list(path.artifact_core_ids),
        "undetected_FDI": list(path.undetected_fdi),
        "signature": list(path.signature),
    }


def _orientation_diagnostics(core: _CoreRun) -> dict[str, object]:
    """算法说明。 Check patient-side signs independently of FDI integer ordering."""

    track_by_id = {item.track_id: item for item in core.orientation.tracks}
    checks = []
    violations = []
    for assignment in core.alignment.assignments:
        if assignment.s_mm is None:
            continue
        quadrant = int(assignment.fdi) // 10
        expected_sign = -1.0 if quadrant in {1, 4} else 1.0
        scales = [
            track_by_id[core_id].local_scale_mm
            for core_id in assignment.core_ids if core_id in track_by_id
        ]
        local_scale = float(np.median(scales)) if scales else 8.0
        centered_s = float(assignment.s_mm - core.alignment.midline_offset_mm)
        signed_support = expected_sign * centered_s / max(local_scale, 1.0e-9)
        compatible = signed_support >= -0.15
        record = {
            "FDI": int(assignment.fdi),
            "expected_patient_side": "right" if expected_sign < 0.0 else "left",
            "centered_arch_s_mm": centered_s,
            "signed_side_support_in_local_scales": signed_support,
            "compatible": bool(compatible),
        }
        checks.append(record)
        if not compatible:
            violations.append(int(assignment.fdi))
    support = [float(item["signed_side_support_in_local_scales"]) for item in checks]
    return {
        "orientation_method": core.orientation.frame.orientation_name,
        "explicit_patient_axes": core.orientation.frame.orientation_name == "confirmed",
        "pca_occlusal_axis_index": core.orientation.frame.pca_occlusal_axis_index,
        "guide_occlusal_alignment": core.orientation.frame.guide_occlusal_alignment,
        "pca_eigenvalues": core.orientation.frame.pca_eigenvalues,
        "midline_offset_mm": core.alignment.midline_offset_mm,
        "alternative_LR_alignment_cost": core.orientation_alternative_cost,
        "LR_reflection_margin_per_present_tooth": core.orientation_margin_per_tooth,
        "side_consistency_score": float(np.mean([
            np.clip((value + 0.15) / 0.65, 0.0, 1.0) for value in support
        ])) if support else 0.0,
        "side_violation_FDI": violations,
        "per_tooth": checks,
    }


def _tooth_seed_labels_are_preserved(core: _CoreRun) -> bool:
    """算法说明。"""
    lr = np.asarray(core.partition_maps["lr_centres"], dtype=float)
    ap = np.asarray(core.partition_maps["ap_centres"], dtype=float)
    region_id_by_fdi = {region.fdi: region.region_id for region in core.regions}
    for assignment in core.alignment.assignments:
        if assignment.center_lr_ap_mm is None:
            continue
        row = int(np.argmin(np.abs(lr - assignment.center_lr_ap_mm[0])))
        column = int(np.argmin(np.abs(ap - assignment.center_lr_ap_mm[1])))
        if int(core.label_grid[row, column]) != region_id_by_fdi.get(assignment.fdi):
            return False
    return True


def _per_tooth_evidence(core: _CoreRun) -> dict[str, dict[str, object]]:
    """算法说明。 Return transparent diagnostic evidence without changing alignment cost."""

    region_by_fdi = {item.fdi: item for item in core.regions}
    track_by_id = {item.track_id: item for item in core.orientation.tracks}
    output: dict[str, dict[str, object]] = {}
    for assignment in core.alignment.assignments:
        region = region_by_fdi.get(assignment.fdi)
        tracks = [
            track_by_id[core_id]
            for core_id in assignment.core_ids if core_id in track_by_id
        ]
        if region is None:
            continue
        mesiodistal = float(np.median([
            item.mesiodistal_width_mm for item in tracks
        ])) if tracks else 0.0
        buccolingual = float(np.median([
            item.buccolingual_width_mm for item in tracks
        ])) if tracks else 0.0
        relative_height = float(np.median([
            item.relative_crown_height_mm for item in tracks
        ])) if tracks else region.relative_relief_p90_mm
        relief_quality = float(np.mean([
            item.relief_quality for item in tracks
        ])) if tracks else region.relative_relief_score
        compact_anisotropy = (
            min(mesiodistal, buccolingual) / max(mesiodistal, buccolingual)
            if max(mesiodistal, buccolingual) > 1.0e-9 else 0.0
        )
        position_score = 1.0 / (1.0 + max(float(assignment.match_cost), 0.0))
        toothness_score = float(np.mean([
            assignment.persistence,
            relief_quality,
            region.relative_relief_score,
        ]))
        morphology_score = float(np.mean([relief_quality, compact_anisotropy]))
        output[str(assignment.fdi)] = {
            "order_score": 1.0,
            "position_score": position_score,
            "toothness_score": toothness_score,
            "morphology_score": morphology_score,
            "boundary_score": region.boundary_confidence,
            "alignment_margin": core.alignment_margin,
            "measurements": {
                "mesiodistal_width_mm": mesiodistal,
                "buccolingual_width_mm": buccolingual,
                "relative_crown_height_mm": relative_height,
                "region_relative_relief_mean_mm": region.relative_relief_mean_mm,
                "region_relative_relief_p90_mm": region.relative_relief_p90_mm,
                "width_ratio_mesiodistal_to_buccolingual": (
                    mesiodistal / buccolingual if buccolingual > 1.0e-9 else None
                ),
            },
            "diagnostic_only": True,
        }
    return output


def recognize_teeth_new(
    request: ToothFdiMappingNewRequest,
) -> ToothFdiMappingNewResult:
    """算法说明。"""
    request = request.resolved()
    config = yaml.safe_load(request.case_yaml.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise RuntimeError("case YAML must contain a mapping")
    semantics = validate_anatomy(config.get("anatomy", {}))
    objects = config.get("objects", {})
    if not isinstance(objects, dict):
        raise RuntimeError("objects must be a mapping")
    dental_path = _resolve_candidate_path(
        request.case_yaml.parent, objects.get("dental", {}), "dental"
    )
    guide_path = _resolve_candidate_path(
        request.case_yaml.parent, objects.get("guide", {}), "guide"
    )
    dental = _load_mesh(dental_path)
    guide = _load_mesh(guide_path)
    missing_slot_anchors, anchor_discovery = (
        extract_labeled_missing_slot_anchors(
            config,
            request.case_yaml.parent,
            semantics.missing_teeth,
        )
    )
    anchor_tuple = tuple(missing_slot_anchors)
    core = _run_core(
        dental,
        guide,
        dict(config["anatomy"]),
        semantics,
        request.profile,
        anchor_tuple,
    )
    multiview_evidence = core.multiview_evidence
    if (
        request.profile.multi_view_boundary_enabled
        and multiview_evidence is None
    ):
        multiview_evidence = build_multiview_boundary_evidence(
            dental,
            core.orientation.frame,
            surface_valleys=core.surface_valleys,
            azimuth_count=request.profile.multi_view_azimuth_count,
            obliquity_degrees=request.profile.multi_view_obliquity_degrees,
            resolution_mm=request.profile.multi_view_resolution_mm,
            edge_support_quantile=(
                request.profile.multi_view_edge_support_quantile
            ),
        )
        core = _refine_core_with_multiview_boundary(
            core, multiview_evidence, request.profile
        )
    stability_records, stability_passed, stability_score = (
        _stability(
            dental, guide, dict(config["anatomy"]), semantics,
            request.profile, core, multiview_evidence, anchor_tuple,
        )
        if request.profile.run_stability else ([], None, None)
    )
    present_order = _present_order(semantics)
    region_fdi = tuple(region.fdi for region in core.regions)
    matched_assignments = [
        item for item in core.alignment.assignments if item.kind != "undetected"
    ]
    hypothesis_cores: dict[str, tuple[int, ...]] = {}
    for assignment in matched_assignments:
        if assignment.hypothesis_id is not None:
            hypothesis_cores.setdefault(assignment.hypothesis_id, assignment.core_ids)
    flattened = [core_id for value in hypothesis_cores.values() for core_id in value]
    track_by_id = {item.track_id: item for item in core.orientation.tracks}
    lr_centres = np.asarray(core.partition_maps["lr_centres"], dtype=float)
    ap_centres = np.asarray(core.partition_maps["ap_centres"], dtype=float)
    high_crown_artifacts = []
    confidently_rejected_artifacts = []
    absorbed_secondary_cores = []
    relief_values = np.asarray([
        item.relief_quality for item in core.orientation.tracks
        if np.isfinite(item.relief_quality)
    ], dtype=float)
    low_relief_reference = (
        float(np.quantile(relief_values, 0.20)) if len(relief_values) else 0.0
    )

    for core_id in core.alignment.artifact_core_ids:
        track = track_by_id[core_id]
        row = int(np.argmin(np.abs(lr_centres - track.center_lr_ap_mm[0])))
        column = int(np.argmin(np.abs(ap_centres - track.center_lr_ap_mm[1])))
        if int(core.label_grid[row, column]) > 0:
            absorbed_secondary_cores.append(core_id)
        elif track.crownness >= 0.75:
            # Distance from the terminal FDI is not artifact evidence: that
            # would let excluded/missing semantics erase a physical crown.
            # Only independently weak crown relief can explain a high-depth
            # peak as interference here; otherwise preserve the conflict.
            if track.relief_quality <= low_relief_reference:
                confidently_rejected_artifacts.append(core_id)
            else:
                high_crown_artifacts.append(core_id)
    orientation_diagnostics = _orientation_diagnostics(core)
    orientation_diagnostics["authoritative_laterality_source"] = (
        "labeled_missing_slot_sleeves"
        if (
            core.missing_slot_anchor_diagnostics["enabled"]
            and core.missing_slot_anchor_diagnostics["constraints_satisfied"]
        )
        else "crown_assignment_self_consistency"
    )
    per_tooth_evidence = _per_tooth_evidence(core)
    minimum_interior_radius_mm = (
        3.0 * float(core.partition_maps["resolution_mm"])
    )
    qa = {
        "FDI_classification_is_complete_exclusive_and_jaw_valid": True,
        "one_region_per_present_FDI": region_fdi == present_order,
        "missing_and_excluded_FDI_have_no_regions": not bool(
            set(region_fdi) & set(semantics.missing_teeth | semantics.excluded_teeth)
        ),
        "all_present_FDI_detected": not bool(core.alignment.undetected_fdi),
        "hypotheses_do_not_reuse_atomic_cores": len(flattened) == len(set(flattened)),
        "FDI_rank_is_strictly_monotone": region_fdi == present_order,
        "all_matched_hypotheses_have_multiscale_support": all(
            item.persistence >= request.profile.minimum_track_persistence
            for item in matched_assignments
        ),
        "alignment_margin_is_sufficient": (
            (
                core.alignment_margin is None
                and not core.second_alignment_is_feasible
            )
            or (
                core.alignment_margin is not None
                and core.alignment_margin
                >= request.profile.minimum_alignment_margin_per_tooth
            )
        ),
        "all_regions_are_nonempty_and_non_degenerate": all(
            item.pixel_count > 30
            and len(item.contour_lr_ap_mm) > 8
            and item.maximum_interior_radius_mm >= minimum_interior_radius_mm
            for item in core.regions
        ),
        "all_separators_are_component_local": core.segmentation.separator_component_local,
        "patient_side_orientation_is_consistent": bool(
            (
                core.missing_slot_anchor_diagnostics["enabled"]
                and core.missing_slot_anchor_diagnostics["constraints_satisfied"]
            )
            or not orientation_diagnostics["side_violation_FDI"]
        ),
        "labeled_missing_slot_anchors_are_consistent": bool(
            anchor_discovery["all_discovered_anchors_are_unambiguous"]
            and core.missing_slot_anchor_diagnostics["constraints_satisfied"]
        ),
        "tooth_seeds_remain_assigned_after_gingiva_release": (
            _tooth_seed_labels_are_preserved(core)
        ),
        "all_high_crownness_artifacts_have_geometric_rejection_evidence": (
            not high_crown_artifacts
        ),
        "physical_hypothesis_operations_have_independent_evidence": bool(
            core.structural_diagnostics["safe_physical_hypothesis_path"]
        ),
        "mapping_stability_was_evaluated": bool(
            request.profile.run_stability
        ),
        "mapping_is_stable_under_global_perturbations": bool(
            stability_passed is True
        ),
        "compatibility_fields_are_complete": all(
            len(item.crown_point_global_mm) == 3 and len(item.contour_lr_ap_mm) >= 3
            for item in core.regions
        ),
    }
    safe = all(qa.values())
    request.output_dir.mkdir(parents=True, exist_ok=True)
    multichannel_path = request.output_dir / "01_multichannel_projection_comparison.png"
    mapping_path = request.output_dir / "02_tooth_fdi_mapping_preview.png"
    multiview_path = (
        request.output_dir / "03_multiview_boundary_evidence.png"
        if request.profile.multi_view_boundary_enabled else None
    )
    report_path = (
        request.output_dir / "tooth_fdi_mapping_new.json"
        if request.write_report_json
        else None
    )
    case_name = request.case_yaml.parent.name
    _save_multichannel(multichannel_path, core.partition_maps, case_name)
    _save_mapping_preview(mapping_path, core, case_name)
    multiview_pair_records: list[dict[str, object]] = []
    if multiview_evidence is not None:
        (
            multiview_pair_records,
            multiview_boundary_grid,
            multiview_consistency_grid,
        ) = assignment_pair_boundary_evidence(
            multiview_evidence,
            core.alignment,
            core.orientation.frame,
            core.partition_maps,
        )
        # These should be numerically identical to the fields used during
        # segmentation; keep the explicit check so report generation cannot
        # accidentally visualize a differently sampled evidence map.
        if not np.allclose(
            core.partition_maps["multi_view_boundary_score"],
            multiview_boundary_grid,
            atol=1.0e-7,
        ) or not np.allclose(
            core.partition_maps["multi_view_consistency"],
            multiview_consistency_grid,
            atol=1.0e-7,
        ):
            raise RuntimeError(
                "reported multi-view evidence differs from segmentation input"
            )
        assert multiview_path is not None
        _save_multiview_boundary(
            multiview_path,
            multiview_evidence,
            case_name,
            multiview_pair_records,
        )
    frame = core.orientation.frame
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete" if safe else "needs_review",
        "safe_for_downstream_use": safe,
        "case": config.get("case", {"id": case_name}),
        "profile": asdict(request.profile),
        "sources": {
            "case_yaml": str(request.case_yaml),
            "dental": str(dental_path),
            "guide": str(guide_path),
            "labeled_missing_slot_sleeves": [
                item.mesh_path for item in missing_slot_anchors
            ],
        },
        "semantics": {
            "jaw": semantics.jaw,
            "canonical_FDI_order": list(semantics.fdi_order),
            "present_FDI_order": list(present_order),
            "missing_teeth": sorted(semantics.missing_teeth),
            "excluded_teeth": sorted(semantics.excluded_teeth),
        },
        "coordinate_system": {
            "origin_global_mm": frame.origin.tolist(),
            "e_patient_right_to_left": frame.e_lr.tolist(),
            "e_anterior_to_posterior": frame.e_ap.tolist(),
            "e_occ": frame.e_occ.tolist(),
            "orientation_method": frame.orientation_name,
            "pca_occlusal_axis_index": frame.pca_occlusal_axis_index,
            "guide_occlusal_alignment": frame.guide_occlusal_alignment,
            "pca_eigenvalues": frame.pca_eigenvalues,
            "orientation_QA": orientation_diagnostics,
        },
        "labeled_missing_slot_anchors": {
            **anchor_discovery,
            **core.missing_slot_anchor_diagnostics,
            "FDI_inference_role": (
                "hard_patient_side_and_canonical_rank_constraints_only; "
                "never a physical crown region"
            ),
        },
        "projection": {
            "height_quantiles": list(request.profile.height_quantiles),
            "selected_partition_quantile": core.partition_quantile,
            "resolution_mm": float(core.partition_maps["resolution_mm"]),
            "height_map_reference": "in-memory multi-scale triangle Z-buffer",
            "local_gingiva_baseline_method": (
                "median_multiscale_grayscale_opening"
            ),
            "relief_baseline_windows_mm": list(
                request.profile.relief_baseline_windows_mm
            ),
            "relative_crown_relief_available": True,
            "surface_curvature_valley_available": (
                "surface_valley_score" in core.partition_maps
            ),
            "surface_curvature_valley_method": (
                "multi_scale_vertex_normal_shape_operator"
                if "surface_valley_score" in core.partition_maps else None
            ),
            "rendered_preview": str(multichannel_path),
            "multi_view_boundary": (
                {
                    **multiview_evidence.summary(),
                    "assignment_pair_evidence": multiview_pair_records,
                }
                if multiview_evidence is not None else {
                    "enabled": False,
                    "FDI_inference_role": (
                        "none; component-local boundary-cost refinement only"
                    ),
                }
            ),
        },
        "core_tracks": [asdict(item) for item in core.orientation.tracks],
        "hypotheses": [asdict(item) for item in core.orientation.hypotheses],
        "alignment": {
            "best": _alignment_record(core.alignment),
            "second_best": _alignment_record(core.second),
            "second_best_is_physically_feasible": core.second_alignment_is_feasible,
            "margin_per_present_tooth": core.alignment_margin,
            "margin_mode": core.alignment_margin_mode,
            "mapping": {
                str(item.fdi): {
                    "hypothesis_id": item.hypothesis_id,
                    "kind": item.kind,
                    "core_ids": list(item.core_ids),
                    "persistence": item.persistence,
                    "match_cost": item.match_cost,
                    "normalized_alignment_margin": core.alignment_margin,
                    "counterfactual_margin": core.counterfactual_margin_by_fdi.get(
                        str(item.fdi)
                    ),
                }
                for item in core.alignment.assignments
            },
            "present_FDI_constraint": core.present_fdi_constraint_diagnostics,
        },
        "per_tooth_evidence": per_tooth_evidence,
        "regions": [asdict(item) for item in core.regions],
        "segmentation": asdict(core.segmentation),
        "stability": {
            "evaluated": bool(request.profile.run_stability),
            "score": stability_score,
            "passed": stability_passed,
            "trials": stability_records,
        },
        "compatibility": {
            "coordinate_system_available": True,
            "present_contours_available": True,
            "crown_points_global_available": True,
            "guide_mapping_not_executed": True,
        },
        "diagnostics": {
            "high_crownness_artifact_core_ids": high_crown_artifacts,
            "confidently_rejected_artifact_core_ids": confidently_rejected_artifacts,
            "absorbed_secondary_core_ids": absorbed_secondary_cores,
            "artifact_core_ids": list(core.alignment.artifact_core_ids),
            "undetected_FDI": list(core.alignment.undetected_fdi),
            "rejected_alignment_paths": list(core.rejected_alignment_paths),
            "equivalent_alignment_paths": list(core.equivalent_alignment_paths),
            "structural_evidence": core.structural_diagnostics,
            "counterfactual_margin_by_FDI": core.counterfactual_margin_by_fdi,
            "unresolved_states": sorted({
                str(item["kind"])
                for item in core.structural_diagnostics["conflicts"]
            }),
        },
        "QA": qa,
        "outputs": {
            "multichannel_preview_png": str(multichannel_path),
            "mapping_preview_png": str(mapping_path),
            "multiview_boundary_preview_png": (
                str(multiview_path) if multiview_path is not None else None
            ),
        },
    }
    if report_path is not None:
        report["outputs"]["report_json"] = str(report_path)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return ToothFdiMappingNewResult(
        case_yaml=request.case_yaml,
        output_dir=request.output_dir,
        profile=request.profile,
        report=report,
        report_path=report_path,
        multichannel_preview_path=multichannel_path,
        mapping_preview_path=mapping_path,
        multiview_preview_path=multiview_path,
    )

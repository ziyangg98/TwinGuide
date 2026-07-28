#!/usr/bin/env python3
"""内部算法说明。\n\nExtract #11 2-D crown contours using straight contact chords."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import matplotlib.pyplot as plt
import numpy as np
import yaml
from scipy.ndimage import label as connected_components
from scipy.spatial import cKDTree

from .arch_progress_core_grouping import (
    ARCH_PROGRESS_POLICY,
    CORE_GROUPING_POLICIES,
    DEFAULT_POLICY,
    core_groups_to_seeds,
    select_crown_core_candidates,
)
from .contact_chords import (
    CrownSeed,
    build_continuous_projection_mask,
    contact_chords_are_non_crossing,
    find_contact_chords,
    find_shortest_concavity_chords,
    refine_crown_core_seeds,
    split_projection_by_chords,
)
from .ellipse_contours import (
    build_lr_ap_feature_maps,
    fit_unlabelled_ellipse_instances,
)
from .fdi import (
    configured_missing_gap_pair_indices,
    validate_anatomy,
)
from .pipeline import PALETTE, estimate_frame_and_arch, load_mesh, resolve_case_path

APPROVABLE_CONTACT_SEPARATOR_METHODS = frozenset({
    "shortest_valid_local_neck_pair",
})


def _approved_contact_separators(config):
    """内部算法说明。\n\nRead explicit, case-local human approvals without weakening global QA."""

    recognition = config.get("tooth_recognition", {})
    if recognition is None:
        recognition = {}
    if not isinstance(recognition, dict):
        raise RuntimeError("tooth_recognition must be a mapping")
    records = recognition.get("approved_contact_separators", [])
    if not isinstance(records, list):
        raise RuntimeError(
            "tooth_recognition.approved_contact_separators must be a list"
        )
    approvals = {}
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise RuntimeError(
                f"approved_contact_separators[{index}] must be a mapping"
            )
        fdis = record.get("fdis")
        if (
            not isinstance(fdis, list)
            or len(fdis) != 2
            or any(isinstance(value, bool) or not isinstance(value, int) for value in fdis)
            or fdis[0] == fdis[1]
        ):
            raise RuntimeError(
                f"approved_contact_separators[{index}].fdis must contain two FDI values"
            )
        method = record.get("selection_method")
        if method not in APPROVABLE_CONTACT_SEPARATOR_METHODS:
            raise RuntimeError(
                f"approved_contact_separators[{index}].selection_method is not approvable"
            )
        if record.get("review_status") != "user_confirmed":
            raise RuntimeError(
                f"approved_contact_separators[{index}] must be user_confirmed"
            )
        key = tuple(sorted(int(value) for value in fdis))
        if key in approvals:
            raise RuntimeError(
                f"duplicate approved contact separator for FDI {key}"
            )
        approvals[key] = str(method)
    return approvals


def _contact_separator_is_approved(chord, labels, approvals):
    """内部算法说明。\n\nAccept strict concavity separators or an exact case-confirmed fallback."""

    if chord.kind != "contact":
        return True
    if chord.selection_method == "shortest_valid_concavity_pair":
        return True
    pair = tuple(sorted((
        int(labels[chord.pair_index]),
        int(labels[chord.pair_index + 1]),
    )))
    return approvals.get(pair) == chord.selection_method


def _save_contact_diagnostics(path, points, maps, mask, instances, chords):
    """内部算法说明。"""
    figure, axis = plt.subplots(figsize=(10, 8), constrained_layout=True)
    axis.scatter(points[:, 0], points[:, 1], s=0.22, color="#cbd5e1", alpha=0.24)
    lr = np.asarray(maps["lr_centres"])
    ap = np.asarray(maps["ap_centres"])
    axis.contour(lr, ap, mask.T.astype(float), levels=[0.5], colors=["#475569"], linewidths=0.8)
    for index, instance in enumerate(instances):
        center = np.asarray(instance.center_lr_ap_mm)
        axis.scatter(*center, s=45, color=PALETTE[index % len(PALETTE)], edgecolor="black", zorder=4)
        axis.text(center[0] + 0.25, center[1] + 0.25, f"I{index + 1}", fontsize=8)
    for chord in chords:
        point = np.asarray(chord.line_point_lr_ap_mm)
        direction = np.asarray(chord.line_direction_lr_ap)
        if chord.kind == "contact":
            first = np.asarray(chord.first_endpoint_lr_ap_mm)
            second = np.asarray(chord.second_endpoint_lr_ap_mm)
            axis.plot([first[0], second[0]], [first[1], second[1]], color="#dc2626", linewidth=1.8)
            axis.scatter([first[0], second[0]], [first[1], second[1]], s=22, color="#facc15", edgecolor="#7f1d1d", zorder=5)
        else:
            segment = np.vstack([point - 4.0 * direction, point + 4.0 * direction])
            axis.plot(segment[:, 0], segment[:, 1], color="#2563eb", linestyle="--", linewidth=1.4)
    axis.set_aspect("equal", adjustable="box")
    axis.set_xlabel("患者右 → 左（mm）")
    axis.set_ylabel("前 → 后（mm）")
    axis.set_title("接触端点与直线接触弦（未编号）")
    figure.savefig(path, dpi=240)
    plt.close(figure)


def _save_final(path, points, contours, chords, labels):
    """内部算法说明。"""
    figure, axis = plt.subplots(figsize=(10, 8), constrained_layout=True)
    axis.scatter(points[:, 0], points[:, 1], s=0.18, color="#dbe4ee", alpha=0.22)
    for index, contour in enumerate(contours):
        polygon = np.asarray(contour.contour_lr_ap_mm)
        center = np.asarray(contour.area_centroid_lr_ap_mm)
        interior = np.asarray(contour.interior_center_lr_ap_mm)
        color = PALETTE[index % len(PALETTE)]
        axis.plot(polygon[:, 0], polygon[:, 1], color=color, linewidth=1.8)
        axis.scatter(*center, s=54, color=color, edgecolor="black", zorder=5)
        axis.scatter(*interior, s=36, marker="x", color="#111827", linewidths=1.4, zorder=6)
        axis.text(center[0] + 0.3, center[1] + 0.3, str(labels[index]), fontsize=9, weight=600)
    for chord in chords:
        if chord.kind != "contact":
            continue
        first = np.asarray(chord.first_endpoint_lr_ap_mm)
        second = np.asarray(chord.second_endpoint_lr_ap_mm)
        axis.plot([first[0], second[0]], [first[1], second[1]], color="#111827", linewidth=1.1)
    axis.set_aspect("equal", adjustable="box")
    axis.set_xlabel("患者右 → 左（mm）")
    axis.set_ylabel("前 → 后（mm）")
    axis.set_title("现存牙冠轮廓：面积质心（圆点）与内部中心（×）")
    figure.savefig(path, dpi=240)
    plt.close(figure)


def _save_regions(path, maps, label_grid):
    """内部算法说明。"""
    extent = [
        float(np.min(maps["lr_centres"])), float(np.max(maps["lr_centres"])),
        float(np.min(maps["ap_centres"])), float(np.max(maps["ap_centres"])),
    ]
    figure, axis = plt.subplots(figsize=(10, 8), constrained_layout=True)
    masked = np.ma.masked_where(label_grid.T == 0, label_grid.T)
    axis.imshow(masked, origin="lower", extent=extent, interpolation="nearest", cmap="tab20")
    axis.set_aspect("equal", adjustable="box")
    axis.set_xlabel("患者右 → 左（mm）")
    axis.set_ylabel("前 → 后（mm）")
    axis.set_title("接触弦分割的牙冠投影区域")
    figure.savefig(path, dpi=220)
    plt.close(figure)


def _configured_projection_support(
    enhanced,
    frame,
    tooth_slots,
    present_teeth,
    ordered_instances,
    core_grouping_policy,
):
    """内部算法说明。\n\nKeep one physical crown-core region per configured present FDI.

    Historical FDI slot intervals are reported as priors only.  They are not
    used as hard spatial crop limits because a missing terminal slot otherwise
    removes the first real crown.  Multiple adjacent local cores may be merged
    into one physical crown; only genuine ungrouped surplus cores are excluded.
    """

    lr = np.asarray(enhanced["lr_centres"], dtype=float)
    ap = np.asarray(enhanced["ap_centres"], dtype=float)
    lr_grid, ap_grid = np.meshgrid(lr, ap, indexing="ij")
    grid_points = np.column_stack([lr_grid.ravel(), ap_grid.ravel()])
    curve_points = np.column_stack([frame["curve"].lr, frame["curve"].ap])
    tree = cKDTree(curve_points)
    transverse_distance, curve_index = tree.query(grid_points, k=1)
    np.asarray(frame["curve"].s)[curve_index]

    used_intervals = []
    for slot in tooth_slots:
        fdi = int(slot["FDI"])
        if fdi not in present_teeth:
            continue
        lower, upper = sorted(float(value) for value in slot["arch_interval_s_mm"])
        used_intervals.append((fdi, lower, upper))

    raw_mask = np.asarray(enhanced["silhouette"], dtype=bool)
    corridor = (transverse_distance <= 11.5).reshape(raw_mask.shape)
    candidate_mask = raw_mask & corridor
    candidate_maps = dict(enhanced)
    candidate_maps["silhouette"] = candidate_mask
    candidates, selected = select_crown_core_candidates(
        enhanced_maps=candidate_maps,
        ordered_instances=ordered_instances,
        policy=core_grouping_policy,
    )
    target_count = len(ordered_instances)
    if len(selected) < target_count:
        raise RuntimeError(
            "enhanced projection omits one or more physical crowns: "
            f"expected={target_count}, detected={len(selected)}. "
            "Regenerate the enhanced projection with automatic short-crown "
            "height-floor selection; configured tooth slots must not be used "
            "to manufacture missing physical instances."
        )
    elif len(selected) > target_count:
        raise RuntimeError(
            "could not find one physical crown core per configured present FDI: "
            f"expected={target_count}, "
            f"raw_candidates={len(candidates)}, "
            f"selected_physical_groups={len(selected)}"
        )

    # Discard isolated projection components that contain no crown core at all.
    components, component_count = connected_components(candidate_mask)
    candidate_components: set[int] = set()
    support_points = [
        np.asarray(candidate.center_lr_ap_mm, dtype=float)
        for candidate in candidates
    ]
    for point in support_points:
        row = int(np.argmin(np.abs(lr - point[0])))
        column = int(np.argmin(np.abs(ap - point[1])))
        component = int(components[row, column])
        if component > 0:
            candidate_components.add(component)
    supported = candidate_mask & np.isin(components, list(candidate_components))

    selected_member_ids = {
        candidate_id
        for group in selected
        for candidate_id in group.member_candidate_ids
    }
    rejected_ids = {
        candidate.candidate_id for candidate in candidates
    } - selected_member_ids
    excluded_pixel_count = 0
    rejection_chords = []
    if rejected_ids:
        all_candidate_seeds = [
            CrownSeed(
                instance_id=int(candidate.candidate_id),
                center_lr_ap_mm=candidate.center_lr_ap_mm,
                initial_center_lr_ap_mm=candidate.center_lr_ap_mm,
                core_pixel_count=0,
                refinement_distance_mm=0.0,
            )
            for candidate in candidates
        ]
        partition_maps = dict(candidate_maps)
        partition_maps["silhouette"] = supported
        rejection_chords = find_shortest_concavity_chords(
            enhanced_maps=partition_maps,
            ordered_seeds=all_candidate_seeds,
        )
        _, candidate_labels = split_projection_by_chords(
            feature_maps=partition_maps,
            projection_mask=supported,
            ordered_instances=all_candidate_seeds,
            chords=rejection_chords,
        )
        selected_label_indices = [
            index + 1
            for index, candidate in enumerate(candidates)
            if candidate.candidate_id not in rejected_ids
        ]
        configured = supported & np.isin(candidate_labels, selected_label_indices)
        excluded_pixel_count = int(np.count_nonzero(supported & ~configured))
    else:
        configured = supported

    candidate_records = [
        {
            "candidate_id": int(candidate.candidate_id),
            "center_LR_AP_mm": list(candidate.center_lr_ap_mm),
            "directed_arch_position_mm": float(
                candidate.directed_arch_position_mm
            ),
            "maximum_depth_mm": float(candidate.maximum_depth_mm),
            "crown_core_quality": float(candidate.crown_core_quality),
            "selected_for_present_FDI": candidate.candidate_id not in rejected_ids,
        }
        for candidate in candidates
    ]
    return configured, {
        "raw_projection_pixel_count": int(np.count_nonzero(raw_mask)),
        "configured_projection_pixel_count": int(np.count_nonzero(configured)),
        "ignored_outside_configured_support_pixel_count": int(np.count_nonzero(raw_mask & ~configured)),
        "configured_present_intervals_s_mm": used_intervals,
        "slot_intervals_used_as_hard_crop": False,
        "maximum_arch_transverse_distance_mm": 11.5,
        "core_grouping_policy": core_grouping_policy,
        "raw_component_count": int(component_count),
        "crown_core_candidates": candidate_records,
        "selected_candidate_count": len(selected),
        "effective_physical_instance_count": len(selected),
        "slot_based_physical_instance_recovery_used": False,
        "rejected_candidate_ids": sorted(int(value) for value in rejected_ids),
        "rejected_interference_pixel_count": int(excluded_pixel_count),
        "interference_partition_chord_count": len(rejection_chords),
        "physical_crown_core_groups": [
            {
                "physical_instance_index": int(index + 1),
                "member_candidate_ids": list(group.member_candidate_ids),
                "center_LR_AP_mm": list(group.center_lr_ap_mm),
                "directed_arch_position_mm": float(
                    group.directed_arch_position_mm
                ),
                "maximum_merge_step_mm": float(group.maximum_merge_step_mm),
                "merge_evidence_sufficient": bool(
                    group.merge_evidence_sufficient
                ),
            }
            for index, group in enumerate(selected)
        ],
        "selection_rule": (
            "merge only locally supported adjacent crown-core peaks; if "
            "well-separated surplus groups remain, select a strictly ordered "
            "present-prior subset and leave the rest unnumbered"
        ),
    }, selected


def run(args):
    """内部算法说明。"""
    case_yaml = args.case.resolve()
    case_dir = case_yaml.parent
    config = yaml.safe_load(case_yaml.read_text(encoding="utf-8"))
    approved_contact_separators = _approved_contact_separators(config)
    mapping_path = (
        args.mapping_report.resolve()
        if args.mapping_report is not None
        else case_dir / "输出/tooth_guide_mapping/tooth_guide_mapping.json"
    )
    source = json.loads(mapping_path.read_text(encoding="utf-8"))
    anatomy = dict(config["anatomy"])
    coordinate = source["coordinate_system"]
    anatomy["orientation"] = {
        "patient_right_to_left_axis": coordinate["e_patient_right_to_left"],
        "anterior_to_posterior_axis": coordinate["e_anterior_to_posterior"],
        "occlusal_axis": coordinate["e_occ"],
    }
    semantics = validate_anatomy(anatomy)
    numbered_order = tuple(
        label for label in semantics.fdi_order if label in semantics.present_teeth
    )
    forbidden = set(semantics.missing_teeth | semantics.excluded_teeth)
    if set(numbered_order) != set(semantics.present_teeth) or set(numbered_order) & forbidden:
        raise RuntimeError("only configured present teeth may be numbered")

    dental = load_mesh(resolve_case_path(case_dir, config["objects"]["dental"], "dental"))
    guide = load_mesh(resolve_case_path(case_dir, config["objects"]["guide"], "guide"))
    parameters = source["mapping_parameters"]
    core_grouping_policy = getattr(
        args,
        "core_grouping_policy",
        DEFAULT_POLICY,
    )
    enhanced = None
    projection_height_quantile = float(parameters["crown_height_quantile"])
    if args.enhanced_maps is not None:
        with np.load(args.enhanced_maps.resolve()) as archive:
            enhanced = {key: archive[key] for key in archive.files}
        if "height_quantile" in enhanced:
            projection_height_quantile = float(enhanced["height_quantile"])
        if "core_grouping_policy" in enhanced:
            recorded_policy = str(
                np.asarray(enhanced["core_grouping_policy"]).item()
            )
            if recorded_policy != core_grouping_policy:
                raise RuntimeError(
                    "enhanced projection/core extraction grouping policies differ: "
                    f"projection={recorded_policy}, extraction={core_grouping_policy}"
                )
    frame = estimate_frame_and_arch(
        dental, guide, anatomy, semantics,
        projection_height_quantile,
        float(parameters["minimum_crown_normal_dot"]),
    )
    vertices = np.asarray(dental.vertices)
    delta = vertices - np.asarray(frame["origin"])
    lr = delta @ np.asarray(frame["e_lr"])
    ap = delta @ np.asarray(frame["e_ap"])
    height = delta @ np.asarray(frame["e_occ"])
    normal_dot = np.asarray(dental.vertex_normals) @ np.asarray(frame["e_occ"])
    height_floor = float(np.quantile(height, float(parameters["crown_height_quantile"])))
    crown_support = (height >= height_floor) & (normal_dot >= float(parameters["minimum_crown_normal_dot"]))
    points = np.column_stack([lr[crown_support], ap[crown_support]])
    maps = build_lr_ap_feature_maps(
        lr=lr, ap=ap, height=height, normal_dot=normal_dot,
        crown_support=crown_support, resolution_mm=args.resolution_mm,
    )
    curve_points = np.column_stack([frame["curve"].lr, frame["curve"].ap])
    curve_tree = cKDTree(curve_points)
    projection_filter_diagnostics = None
    forced_gap_pairs = configured_missing_gap_pair_indices(semantics)

    if args.enhanced_maps is not None:
        assert enhanced is not None
        enhanced["resolution_mm"] = float(np.median(np.diff(enhanced["lr_centres"])))
        slot_by_fdi = {int(item["FDI"]): item for item in source["tooth_slots"]}
        if any(label not in slot_by_fdi for label in numbered_order):
            raise RuntimeError("configured present FDI is missing a tooth-slot prior")
        ordered = [
            SimpleNamespace(
                instance_id=int(label),
                center_lr_ap_mm=tuple(float(value) for value in slot_by_fdi[label]["arch_LR_AP_mm"]),
            )
            for label in numbered_order
        ]
        mask, projection_filter_diagnostics, selected_core_groups = (
            _configured_projection_support(
            enhanced,
            frame,
            source["tooth_slots"],
            semantics.present_teeth,
            ordered,
            core_grouping_policy,
        ))
        enhanced["silhouette"] = mask
        if core_grouping_policy == ARCH_PROGRESS_POLICY:
            partition_instances = core_groups_to_seeds(
                ordered,
                selected_core_groups,
            )
        else:
            partition_instances = refine_crown_core_seeds(
                enhanced_maps=enhanced, ordered_instances=ordered
            )
        projection_filter_diagnostics["post_filter_raw_component_recovery"] = (
            "disabled: filtered interference and arch-corridor pixels may not "
            "be silently restored"
        )
        chords = find_shortest_concavity_chords(
            enhanced_maps=enhanced,
            ordered_seeds=partition_instances,
            forced_gap_pair_indices=forced_gap_pairs,
        )
        partition_maps = enhanced
    else:
        proposals, _ = fit_unlabelled_ellipse_instances(
            maps, len(numbered_order), random_state=args.random_state
        )
        sortable = []
        for proposal in proposals:
            _, curve_index = curve_tree.query(np.asarray(proposal.center_lr_ap_mm), k=1)
            sortable.append((float(frame["curve"].s[int(curve_index)]), proposal))
        sortable.sort(key=lambda item: item[0])
        ordered = [item[1] for item in sortable]
        mask = build_continuous_projection_mask(maps)
        partition_instances = ordered
        chords = find_contact_chords(
            feature_maps=maps, projection_mask=mask, ordered_instances=ordered
        )
        partition_maps = maps
    contours, label_grid = split_projection_by_chords(
        feature_maps=partition_maps,
        projection_mask=mask,
        ordered_instances=partition_instances,
        chords=chords,
    )
    labels = list(numbered_order)
    assigned = set(labels)
    if assigned != set(semantics.present_teeth) or assigned & forbidden:
        raise RuntimeError("contact-chord assignment violated present-only rule")

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "report": output_dir / "contact_chord_report.json",
    }
    if getattr(args, "write_diagnostics", True):
        paths.update({
            "contact_diagnostics": output_dir / "01_contact_endpoints_and_chords.png",
            "partitioned_regions": output_dir / "02_chord_partitioned_regions.png",
            "final_contours": output_dir / "03_final_contact_chord_contours.png",
        })
        _save_contact_diagnostics(
            paths["contact_diagnostics"], points, partition_maps, mask,
            partition_instances, chords,
        )
        _save_regions(paths["partitioned_regions"], partition_maps, label_grid)
        _save_final(paths["final_contours"], points, contours, chords, labels)

    chord_records = []
    for chord in chords:
        record = dict(chord.__dict__)
        record["first_FDI"] = labels[chord.pair_index]
        record["second_FDI"] = labels[chord.pair_index + 1]
        chord_records.append(record)
    contour_records = []
    for label, contour in zip(labels, contours, strict=False):
        contour_records.append({
            "FDI": int(label),
            "source_unlabelled_instance_id": int(contour.source_instance_id),
            "area_mm2": float(contour.area_mm2),
            "area_centroid_LR_AP_mm": list(contour.area_centroid_lr_ap_mm),
            "interior_center_LR_AP_mm": list(contour.interior_center_lr_ap_mm),
            "maximum_interior_radius_mm": float(contour.maximum_interior_radius_mm),
            "pixel_count": int(contour.pixel_count),
            "contour_LR_AP_mm": contour.contour_lr_ap_mm,
        })
    mask_pixel_count = int(np.count_nonzero(mask))
    assigned_pixel_count = int(np.count_nonzero(label_grid))
    mask_coverage = float(assigned_pixel_count / max(mask_pixel_count, 1))
    seed_assignment_checks = []
    for index, item in enumerate(partition_instances):
        center = np.asarray(item.center_lr_ap_mm, dtype=float)
        row = int(np.argmin(np.abs(np.asarray(partition_maps["lr_centres"]) - center[0])))
        column = int(np.argmin(np.abs(np.asarray(partition_maps["ap_centres"]) - center[1])))
        seed_assignment_checks.append(int(label_grid[row, column]) == index + 1)
    qa = {
        "FDI_classification_is_complete_exclusive_and_jaw_valid": True,
        "contour_count_equals_present_teeth_count": len(contours) == len(semantics.present_teeth),
        "chord_or_gap_count_equals_adjacent_pair_count": len(chords) == len(contours) - 1,
        "assigned_FDI_equal_present_teeth": assigned == set(semantics.present_teeth),
        "missing_and_excluded_FDI_are_not_assigned": not bool(assigned & forbidden),
        "all_contours_are_nonempty": all(item.pixel_count > 30 and len(item.contour_lr_ap_mm) > 8 for item in contours),
        "all_contact_chords_have_two_endpoints": all(
            item.kind != "contact" or (
                item.first_endpoint_lr_ap_mm is not None and item.second_endpoint_lr_ap_mm is not None
            ) for item in chords
        ),
        "no_uncertain_contact_chords": not any(item.kind == "uncertain" for item in chords),
        "all_contacts_use_approved_anatomical_separators": all(
            _contact_separator_is_approved(
                item,
                labels,
                approved_contact_separators,
            )
            for item in chords
        ),
        "all_topology_seeds_are_inside_their_final_regions": all(seed_assignment_checks),
        "contact_chords_are_non_crossing": contact_chords_are_non_crossing(chords),
        "all_contours_have_non_degenerate_interior": all(
            item.maximum_interior_radius_mm >= 1.50 for item in contours
        ),
        "physical_instance_count_equals_present_FDI_count": (
            projection_filter_diagnostics is None
            or projection_filter_diagnostics[
                "effective_physical_instance_count"
            ]
            == len(semantics.present_teeth)
        ),
        "slot_based_physical_instance_recovery_is_disabled": (
            projection_filter_diagnostics is None
            or not projection_filter_diagnostics[
                "slot_based_physical_instance_recovery_used"
            ]
        ),
        "all_multi_core_merges_have_local_spacing_evidence": (
            projection_filter_diagnostics is None
            or all(
                bool(item["merge_evidence_sufficient"])
                for item in projection_filter_diagnostics[
                    "physical_crown_core_groups"
                ]
            )
        ),
        "physical_instances_are_strictly_ordered_on_directed_arch": (
            projection_filter_diagnostics is None
            or bool(np.all(np.diff([
                float(item["directed_arch_position_mm"])
                for item in projection_filter_diagnostics[
                    "physical_crown_core_groups"
                ]
            ]) > 0.0))
        ),
        "configured_internal_missing_slots_are_gap_separators": all(
            chords[index].kind == "gap"
            and chords[index].selection_method == "configured_missing_slot_gap"
            for index in forced_gap_pairs
        ),
        # Straight half-plane cuts can isolate sub-tooth projection crumbs.
        # Retain the measured fraction and accept at most 1% unassigned area;
        # the stricter anatomical gates above still require every configured
        # tooth, seed, contact/gap and physical crown core to be valid.
        "projection_mask_is_fully_partitioned": mask_coverage >= 0.990,
    }
    report = {
        "schema_version": "1.2-lr-ap-concavity-chords-with-frame",
        "created_at": datetime.now(UTC).isoformat(),
        "status": "complete" if all(qa.values()) else "needs_review",
        "safe_for_downstream_use": bool(all(qa.values())),
        "case": config["case"],
        "sources": {
            "case_yaml": str(case_yaml),
            "base_coordinate_report": str(mapping_path),
            "surgical_reference": list(
                source.get("sources", {}).get("surgical_reference", []) or []
            ),
            "enhanced_projection_maps": (
                str(args.enhanced_maps.resolve())
                if args.enhanced_maps is not None else None
            ),
        },
        # Persist the exact anatomical frame used to measure LR/AP centres so
        # the downstream guide mapper never has to infer screen orientation or
        # silently reuse an unrelated historical tooth-slot result.
        "coordinate_system": source["coordinate_system"],
        "source_mapping_parameters": source["mapping_parameters"],
        "method": {
            "projection_plane": "anatomical LR/AP",
            "final_boundary_model": "outer projection contour plus straight contact chords",
            "contact_endpoint_definition": (
                "shortest valid pair of smoothed outer-contour concavities; centres are topology seeds only"
                if args.enhanced_maps is not None
                else "two intersections of the locally narrowest supported neck cross-section"
            ),
            "enhanced_projection_used": args.enhanced_maps is not None,
            "enhanced_projection_height_quantile": projection_height_quantile,
            "core_grouping_policy": core_grouping_policy,
            "configured_FDI_used_for_slot_matching_and_support_filter": args.enhanced_maps is not None,
            "FDI_used_to_score_or_choose_concavity_endpoints": False,
            "watershed_used": False,
            "ellipse_used_as_final_contour": False,
            "pre_boundary_seed_definition": (
                "weighted interior crown core from silhouette depth, height, occlusal normal and low-edge support"
                if args.enhanced_maps is not None else "coarse unlabelled ellipse centre"
            ),
            "final_center_definition": "uniform area centroid after the physical contour is complete",
            "interior_center_definition": "centre of the maximum inscribed circle after contour completion",
            "case_approved_contact_separators": [
                {
                    "fdis": list(pair),
                    "selection_method": method,
                    "review_status": "user_confirmed",
                }
                for pair, method in sorted(approved_contact_separators.items())
            ],
        },
        "seeds": [
            {
                "configured_FDI": int(label),
                "instance_id": int(item.instance_id),
                "seed_center_LR_AP_mm": list(item.center_lr_ap_mm),
                "initial_center_LR_AP_mm": list(getattr(
                    item, "initial_center_lr_ap_mm", item.center_lr_ap_mm
                )),
                "core_pixel_count": int(getattr(item, "core_pixel_count", 0)),
                "refinement_distance_mm": float(getattr(item, "refinement_distance_mm", 0.0)),
            }
            for label, item in zip(labels, partition_instances, strict=False)
        ],
        "semantics": {
            "numbered_present_teeth": labels,
            "missing_teeth_not_numbered": sorted(semantics.missing_teeth),
            "excluded_teeth_not_numbered": sorted(semantics.excluded_teeth),
        },
        "chords": chord_records,
        "contours": contour_records,
        "partition_diagnostics": {
            "projection_mask_pixel_count": mask_pixel_count,
            "assigned_pixel_count": assigned_pixel_count,
            "mask_coverage_fraction": mask_coverage,
            "configured_projection_filter": projection_filter_diagnostics,
            "configured_missing_gap_pair_indices": sorted(forced_gap_pairs),
        },
        "QA": qa,
        "outputs": {key: str(value) for key, value in paths.items()},
    }
    paths["report"].write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def parse_args():
    """内部算法说明。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--resolution-mm", type=float, default=0.18)
    parser.add_argument("--random-state", type=int, default=17)
    parser.add_argument("--enhanced-maps", type=Path)
    parser.add_argument("--mapping-report", type=Path)
    parser.add_argument(
        "--core-grouping-policy",
        choices=CORE_GROUPING_POLICIES,
        default=DEFAULT_POLICY,
        help=(
            "must match the enhanced projection; arch_progress is the current "
            "TwinGuide default"
        ),
    )
    return parser.parse_args()


def main():
    """内部算法说明。"""
    report = run(parse_args())
    print(json.dumps({
        "status": report["status"],
        "contact_count": sum(item["kind"] == "contact" for item in report["chords"]),
        "gap_count": sum(item["kind"] == "gap" for item in report["chords"]),
        "QA": report["QA"],
        "outputs": report["outputs"],
    }, ensure_ascii=False, indent=2))
    # A review result is still written for diagnosis, but must not look like a
    # successful batch result to an automated downstream process.
    return 0 if report["safe_for_downstream_use"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

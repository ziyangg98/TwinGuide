"""内部算法说明。 Labeled implant-sleeve anchors for resolving dental-arch mirror ambiguity.

The sleeve is never promoted to a physical crown candidate.  Its FDI label is
used only as a semantic missing-slot landmark: patient side, canonical order,
and bracketing of neighbouring present crowns.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import re
from typing import Iterable

import numpy as np
import trimesh

from .models import AlignmentPath, ArchFrame, LabeledMissingSlotAnchor


_FDI_AFTER_HASH = re.compile(r"#(\d{2})(?=[^0-9]|$)")


def _load_anchor_point(path: Path) -> tuple[tuple[float, float, float], str]:
    """内部算法说明。 Return an orientation-independent centre of the registered sleeve mesh."""

    loaded = trimesh.load(path, force="mesh", process=True)
    if isinstance(loaded, trimesh.Scene):
        loaded = trimesh.util.concatenate(tuple(loaded.geometry.values()))
    if not isinstance(loaded, trimesh.Trimesh) or loaded.is_empty:
        raise RuntimeError(f"could not load sleeve mesh: {path}")
    point = np.asarray(loaded.centroid, dtype=float)
    method = "surface_area_centroid"
    if point.shape != (3,) or not np.all(np.isfinite(point)):
        point = np.mean(np.asarray(loaded.bounds, dtype=float), axis=0)
        method = "axis_aligned_bounds_centroid_fallback"
    return tuple(float(value) for value in point), method


def _sleeve_file_nodes(config: dict[str, object]) -> dict[str, dict[str, object]]:
    """内部算法说明。"""
    objects = config.get("objects", {})
    if not isinstance(objects, dict):
        return {}
    sleeve = objects.get("sleeve", {})
    if isinstance(sleeve, dict):
        files = sleeve.get("files", []) or []
    else:
        files = []
    if not files:
        files = objects.get("guide_sleeves", []) or []
    if not isinstance(files, list):
        return {}
    output: dict[str, dict[str, object]] = {}
    for index, item in enumerate(files, 1):
        if not isinstance(item, dict):
            continue
        sleeve_id = str(item.get("id", f"guide_sleeve_{index}"))
        output[sleeve_id] = item
    return output


def _active_sleeve_ids(config: dict[str, object]) -> set[str]:
    """内部算法说明。"""
    objects = config.get("objects", {})
    sleeve = objects.get("sleeve", {}) if isinstance(objects, dict) else {}
    if not isinstance(sleeve, dict):
        records = objects.get("guide_sleeves", []) if isinstance(objects, dict) else []
        return {
            str(item.get("id", f"guide_sleeve_{index}"))
            for index, item in enumerate(records or [], 1)
            if isinstance(item, dict)
        }
    return {str(item) for item in sleeve.get("active_ids", []) or []}


def extract_labeled_missing_slot_anchors(
    config: dict[str, object],
    case_dir: Path,
    missing_fdis: Iterable[int],
) -> tuple[list[LabeledMissingSlotAnchor], dict[str, object]]:
    """内部算法说明。 Resolve authoritative implant-site mappings, then safe filename fallbacks."""

    missing = {int(value) for value in missing_fdis}
    sleeve_files = _sleeve_file_nodes(config)
    active_ids = _active_sleeve_ids(config)
    requested: list[tuple[int, str, str]] = []
    discovery_issues: list[dict[str, object]] = []

    planning = config.get("planning", {})
    implant_sites = planning.get("implant_sites", []) if isinstance(planning, dict) else []
    if isinstance(implant_sites, list):
        for site in implant_sites:
            if not isinstance(site, dict):
                continue
            try:
                fdi = int(site["fdi"])
                sleeve_id = str(site["sleeve_id"])
            except (KeyError, TypeError, ValueError):
                discovery_issues.append({
                    "reason": "invalid_planning_implant_site",
                    "site": site,
                })
                continue
            if fdi not in missing:
                discovery_issues.append({
                    "reason": "implant_site_FDI_is_not_declared_missing",
                    "fdi": fdi,
                    "sleeve_id": sleeve_id,
                })
                continue
            requested.append((fdi, sleeve_id, "planning.implant_sites"))

    planned_fdis = {item[0] for item in requested}
    planned_ids = {item[1] for item in requested}
    for sleeve_id, node in sleeve_files.items():
        if active_ids and sleeve_id not in active_ids:
            continue
        if sleeve_id in planned_ids:
            continue
        raw_path = node.get("path")
        if raw_path is None:
            continue
        explicit_fdi = node.get("fdi")
        if explicit_fdi is not None:
            try:
                fdi = int(explicit_fdi)
            except (TypeError, ValueError):
                discovery_issues.append({
                    "reason": "invalid_guide_sleeve_FDI",
                    "sleeve_id": sleeve_id,
                    "fdi": explicit_fdi,
                })
                continue
            if fdi in missing and fdi not in planned_fdis:
                requested.append((fdi, sleeve_id, "objects.guide_sleeves.fdi"))
                continue
        matches = _FDI_AFTER_HASH.findall(Path(str(raw_path)).name)
        candidates = {int(value) for value in matches if int(value) in missing}
        if len(candidates) == 1:
            fdi = next(iter(candidates))
            if fdi not in planned_fdis:
                requested.append((fdi, sleeve_id, "sleeve_filename"))
        elif len(candidates) > 1:
            discovery_issues.append({
                "reason": "ambiguous_sleeve_filename_FDI",
                "sleeve_id": sleeve_id,
                "candidates": sorted(candidates),
            })

    anchors: list[LabeledMissingSlotAnchor] = []
    seen_fdi: set[int] = set()
    for fdi, sleeve_id, source in requested:
        if fdi in seen_fdi:
            discovery_issues.append({
                "reason": "duplicate_sleeve_anchor_for_FDI",
                "fdi": fdi,
                "sleeve_id": sleeve_id,
            })
            continue
        node = sleeve_files.get(sleeve_id)
        if node is None or node.get("path") is None:
            discovery_issues.append({
                "reason": "mapped_sleeve_file_is_missing",
                "fdi": fdi,
                "sleeve_id": sleeve_id,
            })
            continue
        path = (case_dir / str(node["path"])).resolve()
        if not path.is_file():
            discovery_issues.append({
                "reason": "mapped_sleeve_path_does_not_exist",
                "fdi": fdi,
                "sleeve_id": sleeve_id,
                "path": str(path),
            })
            continue
        try:
            point, point_method = _load_anchor_point(path)
        except Exception as error:
            discovery_issues.append({
                "reason": "sleeve_geometry_could_not_be_loaded",
                "fdi": fdi,
                "sleeve_id": sleeve_id,
                "path": str(path),
                "error": str(error),
            })
            continue
        anchors.append(LabeledMissingSlotAnchor(
            fdi=fdi,
            sleeve_id=sleeve_id,
            label_source=source,
            mesh_path=str(path),
            point_global_mm=point,
            point_method=point_method,
        ))
        seen_fdi.add(fdi)

    return anchors, {
        "anchor_count": len(anchors),
        "anchors": [asdict(item) for item in anchors],
        "discovery_issues": discovery_issues,
        "all_discovered_anchors_are_unambiguous": not bool(discovery_issues),
    }


def project_missing_slot_anchors(
    frame: ArchFrame,
    anchors: Iterable[LabeledMissingSlotAnchor],
) -> list[dict[str, object]]:
    """内部算法说明。 Project registered sleeve centres onto one directed arch hypothesis."""

    records: list[dict[str, object]] = []
    for anchor in anchors:
        delta = np.asarray(anchor.point_global_mm, dtype=float) - frame.origin
        lr_ap = np.asarray([delta @ frame.e_lr, delta @ frame.e_ap], dtype=float)
        s_mm, u_mm = frame.project_lr_ap(lr_ap)
        quadrant = anchor.fdi // 10
        expected_side = "right" if quadrant in {1, 4} else "left"
        expected_sign = -1.0 if expected_side == "right" else 1.0
        signed_side_support = expected_sign * s_mm
        records.append({
            "fdi": anchor.fdi,
            "sleeve_id": anchor.sleeve_id,
            "label_source": anchor.label_source,
            "mesh_path": anchor.mesh_path,
            "point_method": anchor.point_method,
            "point_global_mm": list(anchor.point_global_mm),
            "projected_lr_ap_mm": [float(lr_ap[0]), float(lr_ap[1])],
            "projected_s_mm": float(s_mm),
            "projected_u_mm": float(u_mm),
            "expected_patient_side": expected_side,
            "signed_side_support_mm": float(signed_side_support),
            "side_compatible": bool(signed_side_support > 0.0),
        })
    return records


def evaluate_anchor_frame(
    frame: ArchFrame,
    anchors: Iterable[LabeledMissingSlotAnchor],
    canonical_order: Iterable[int],
) -> dict[str, object]:
    """内部算法说明。 Apply hard laterality and inter-anchor canonical-order constraints."""

    records = project_missing_slot_anchors(frame, anchors)
    rank = {int(fdi): index for index, fdi in enumerate(canonical_order)}
    violations: list[dict[str, object]] = []
    for record in records:
        if not record["side_compatible"]:
            violations.append({
                "reason": "sleeve_anchor_is_on_wrong_patient_side",
                "fdi": record["fdi"],
                "sleeve_id": record["sleeve_id"],
                "projected_s_mm": record["projected_s_mm"],
                "expected_patient_side": record["expected_patient_side"],
            })
    ordered = sorted(records, key=lambda item: rank[int(item["fdi"])])
    for first, second in zip(ordered, ordered[1:]):
        if float(first["projected_s_mm"]) >= float(second["projected_s_mm"]):
            violations.append({
                "reason": "sleeve_anchors_violate_canonical_rank_order",
                "first_FDI": first["fdi"],
                "second_FDI": second["fdi"],
                "first_s_mm": first["projected_s_mm"],
                "second_s_mm": second["projected_s_mm"],
            })
    return {
        "orientation": frame.orientation_name,
        "anchors": records,
        "violations": violations,
        "compatible": not bool(violations),
    }


def evaluate_anchor_alignment(
    path: AlignmentPath,
    frame: ArchFrame,
    anchors: Iterable[LabeledMissingSlotAnchor],
    canonical_order: Iterable[int],
) -> dict[str, object]:
    """内部算法说明。 Require every detected present crown to bracket each labeled missing slot."""

    anchor_records = project_missing_slot_anchors(frame, anchors)
    rank = {int(fdi): index for index, fdi in enumerate(canonical_order)}
    violations: list[dict[str, object]] = []
    comparisons: list[dict[str, object]] = []
    for anchor in anchor_records:
        anchor_fdi = int(anchor["fdi"])
        anchor_rank = rank[anchor_fdi]
        anchor_s = float(anchor["projected_s_mm"])
        for assignment in path.assignments:
            if assignment.s_mm is None:
                continue
            assignment_rank = rank[int(assignment.fdi)]
            expected_relation = -1 if assignment_rank < anchor_rank else 1
            actual_delta = float(assignment.s_mm - anchor_s)
            compatible = bool(expected_relation * actual_delta > 0.0)
            comparison = {
                "anchor_FDI": anchor_fdi,
                "present_FDI": int(assignment.fdi),
                "anchor_s_mm": anchor_s,
                "present_s_mm": float(assignment.s_mm),
                "expected_relation": (
                    "present_before_anchor" if expected_relation < 0
                    else "present_after_anchor"
                ),
                "compatible": compatible,
            }
            comparisons.append(comparison)
            if not compatible:
                violations.append({
                    "reason": "present_crown_crosses_labeled_missing_slot_rank",
                    **comparison,
                })
    return {
        "orientation": path.orientation_name,
        "path_signature": list(path.signature),
        "comparisons": comparisons,
        "violations": violations,
        "compatible": not bool(violations),
    }

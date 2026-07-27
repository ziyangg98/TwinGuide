#!/usr/bin/env python3
"""内部算法说明。\n\nMap approved contact-chord teeth and configured windows onto a guide."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import yaml
from scipy.spatial import cKDTree

from .contact_guide_mapping import (
    fit_measured_contour_arch,
    locate_contact_teeth,
)
from .fdi import validate_anatomy
from .pipeline import (
    estimate_frame_and_arch,
    export_context,
    guide_physical_coverage_top,
    load_mesh,
    local_arch_frame,
    map_windows,
    render_preview,
    resolve_case_path,
    rounded,
    run_case_mapping,
)


def load_json(path: Path) -> dict:
    """内部算法说明。"""
    return json.loads(path.read_text(encoding="utf-8"))


def configured_anchor_station_fdis(config: dict[str, object]) -> set[int]:
    """内部算法说明。\n\nReturn teeth whose configured guide/press-beam stations need coverage."""

    design = config.get("design", {})
    if not isinstance(design, dict):
        return set()
    required: set[int] = set()
    for section_name in ("guide_anchors", "press_beam"):
        section = design.get(section_name, {})
        if not isinstance(section, dict):
            continue
        for station in section.get("stations", []) or []:
            if not isinstance(station, dict):
                continue
            if station.get("fdi") is not None:
                required.add(int(station["fdi"]))
            required.update(int(value) for value in station.get("fdis", []) or [])
    return required


def run(args: argparse.Namespace) -> dict[str, object]:
    """内部算法说明。"""
    case_yaml = args.case.resolve()
    case_dir = case_yaml.parent
    config = yaml.safe_load(case_yaml.read_text(encoding="utf-8"))
    contact_path = (
        args.contact_report.resolve()
        if args.contact_report
        else case_dir / "输出/contact_chord_contours_v18_semantic_segment_selection/contact_chord_report.json"
    )
    base_path = (
        args.base_mapping_report.resolve()
        if args.base_mapping_report
        else case_dir / "输出/tooth_guide_mapping/tooth_guide_mapping.json"
    )
    enhanced_path = (
        args.enhanced_maps.resolve()
        if args.enhanced_maps
        else case_dir / "输出/enhanced_crown_projection_v2/enhanced_projection_maps.npz"
    )
    output_dir = (
        args.output_dir.resolve()
        if args.output_dir
        else case_dir / "输出/tooth_guide_mapping_contact_chord_v1"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    contact = load_json(contact_path)
    if contact.get("status") != "complete" or not contact.get("safe_for_downstream_use"):
        raise RuntimeError("contact-chord tooth report is not approved for downstream use")
    if not all(contact.get("QA", {}).values()):
        raise RuntimeError("contact-chord tooth report has failed QA gates")
    base = None
    coordinate = contact.get("coordinate_system")
    parameters = contact.get("source_mapping_parameters")
    if coordinate is None or parameters is None:
        # Reports written before schema 1.2 did not persist their frame.  The
        # deterministic base mapper is rebuilt only as a coordinate reference;
        # none of its tooth centres are accepted as final locations.
        if not base_path.exists():
            run_case_mapping(case_yaml)
        base = load_json(base_path)
        coordinate = base["coordinate_system"]
        parameters = base["mapping_parameters"]

    anatomy = dict(config["anatomy"])
    anatomy["orientation"] = {
        "patient_right_to_left_axis": coordinate["e_patient_right_to_left"],
        "anterior_to_posterior_axis": coordinate["e_anterior_to_posterior"],
        "occlusal_axis": coordinate["e_occ"],
    }
    semantics = validate_anatomy(anatomy)
    present_order = tuple(
        label for label in semantics.fdi_order if label in semantics.present_teeth
    )
    forbidden = set(semantics.missing_teeth | semantics.excluded_teeth)
    contour_records = list(contact["contours"])
    contour_order = tuple(int(item["FDI"]) for item in contour_records)
    if contour_order != present_order:
        raise RuntimeError(
            "contact contours must equal configured present FDI in canonical order; "
            f"expected {list(present_order)}, got {list(contour_order)}"
        )
    if set(contour_order) & forbidden:
        raise RuntimeError("contact contours contain missing or excluded FDI")

    dental_path = resolve_case_path(case_dir, config["objects"]["dental"], "dental")
    guide_path = resolve_case_path(case_dir, config["objects"]["guide"], "guide")
    dental = load_mesh(dental_path)
    guide = load_mesh(guide_path)
    with np.load(enhanced_path) as archive:
        enhanced = {key: archive[key] for key in archive.files}
    projection_height_quantile = float(
        enhanced.get("height_quantile", parameters["crown_height_quantile"])
    )
    frame = estimate_frame_and_arch(
        dental,
        guide,
        anatomy,
        semantics,
        projection_height_quantile,
        float(parameters["minimum_crown_normal_dot"]),
    )
    axis_agreement = {
        "e_patient_right_to_left_dot": float(np.dot(
            frame["e_lr"], coordinate["e_patient_right_to_left"]
        )),
        "e_anterior_to_posterior_dot": float(np.dot(
            frame["e_ap"], coordinate["e_anterior_to_posterior"]
        )),
        "e_occ_dot": float(np.dot(frame["e_occ"], coordinate["e_occ"])),
    }
    frame["curve"] = fit_measured_contour_arch(contour_records)
    locations = locate_contact_teeth(
        contour_records=contour_records,
        frame=frame,
        enhanced_maps=enhanced,
    )
    centres = {item.fdi: item.arch_s_mm for item in locations}
    intervals = {item.fdi: item.contour_interval_s_mm for item in locations}
    ordered_centres = np.asarray([centres[label] for label in present_order], dtype=float)

    slots = []
    for sequence_index, item in enumerate(locations):
        tangent, outward, _ = local_arch_frame(frame, item.arch_s_mm)
        guide_top = None
        coverage = None
        mapping_error = None
        try:
            coverage = guide_physical_coverage_top(
                guide,
                np.asarray(item.crown_point_global_mm),
                tangent,
                outward,
                np.asarray(frame["e_occ"]),
            )
            guide_top = coverage["true_top_global_mm"]
        except Exception as error:
            mapping_error = str(error)
        slots.append({
            "FDI": item.fdi,
            "sequence_index": sequence_index,
            "status": "present",
            "guide_coverage_status": "mapped" if guide_top is not None else "outside_guide_coverage",
            "arch_s_mm": item.arch_s_mm,
            "arch_interval_s_mm": rounded(item.contour_interval_s_mm),
            "arch_LR_AP_mm": rounded(item.arch_lr_ap_mm),
            "measured_area_centroid_LR_AP_mm": rounded(item.centroid_lr_ap_mm),
            "dental_crown_height_mm": rounded(item.crown_height_mm),
            "dental_crown_point_global_mm": rounded(item.crown_point_global_mm),
            "crown_height_lift_method": item.lift_method,
            "crown_height_lift_distance_mm": rounded(item.lift_distance_mm),
            "local_tangent_global": rounded(tangent),
            "local_outward_global": rounded(outward),
            "guide_top_global_mm": None if guide_top is None else rounded(guide_top),
            "guide_coverage_method": (
                None if coverage is None else coverage["coverage_method"]
            ),
            "guide_coverage_metrics": (
                None
                if coverage is None
                else {
                    key: value
                    for key, value in coverage.items()
                    if key != "true_top_global_mm"
                }
            ),
            "guide_mapping_error": mapping_error,
        })

    window_nodes = [
        dict(node)
        for node in config.get("design", {}).get("observation_windows", []) or []
    ]
    endpoints = {
        int(node[key]) for node in window_nodes for key in ("start_fdi", "end_fdi")
    }
    if not endpoints <= set(semantics.present_teeth):
        raise RuntimeError(
            "observation-window endpoints require measured present-tooth centres: "
            f"{sorted(endpoints - set(semantics.present_teeth))}"
        )
    windows = map_windows(
        window_nodes,
        frame,
        centres,
        intervals,
        guide,
        tooth_top_points={
            item.fdi: np.asarray(item.crown_point_global_mm, dtype=float)
            for item in locations
        },
    )
    contour_windows = [
        item for item in windows if item["opening_geometry"] == "contour_following"
    ]
    axis_sweep_windows = [
        item for item in windows if item["opening_geometry"] == "axis_sweep"
    ]
    axis_diagnostic_requested_sections = sum(
        int(item["requested_sample_count"]) for item in axis_sweep_windows
    )
    axis_diagnostic_mapped_sections = sum(
        int(item["mapped_sample_count"]) for item in axis_sweep_windows
    )
    requested_sections = sum(
        int(item["requested_sample_count"]) for item in contour_windows
    )
    mapped_sections = sum(
        int(item["mapped_sample_count"]) for item in contour_windows
    )
    mapping_fraction = (
        1.0 if requested_sections == 0 else mapped_sections / requested_sections
    )
    slot_fdi = {int(item["FDI"]) for item in slots}
    covered_fdi = {
        int(item["FDI"])
        for item in slots
        if item.get("guide_coverage_status") == "mapped"
        and item.get("guide_top_global_mm") is not None
        and item.get("guide_coverage_method") == "closed_section_exterior_surface_v1"
    }
    required_anchor_fdi = configured_anchor_station_fdis(config)
    interval_tolerance_mm = 0.05
    interval_contains_center = all(
        intervals[label][0] - interval_tolerance_mm
        <= centres[label]
        <= intervals[label][1] + interval_tolerance_mm
        for label in present_order
    )
    qa = {
        "FDI_classification_is_complete_exclusive_and_jaw_valid": True,
        "contact_chord_source_status_complete": True,
        "contact_chord_source_QA_passed": True,
        "approved_anatomical_axes_reproduced": all(
            value >= 0.999 for value in axis_agreement.values()
        ),
        "tooth_slots_equal_configured_present_teeth": slot_fdi == set(semantics.present_teeth),
        "missing_and_excluded_FDI_have_no_geometric_slots": not bool(slot_fdi & forbidden),
        "physical_guide_coverage_classified_for_every_tooth": all(
            item.get("guide_coverage_status") in {"mapped", "outside_guide_coverage"}
            and (
                item.get("guide_coverage_method")
                == "closed_section_exterior_surface_v1"
            )
            == (item.get("guide_top_global_mm") is not None)
            for item in slots
        ),
        "configured_anchor_station_teeth_have_physical_guide_coverage": (
            required_anchor_fdi <= covered_fdi
        ),
        "measured_centres_are_strictly_ordered": bool(np.all(
            np.diff(ordered_centres) > 0.25
        )),
        "each_measured_center_lies_inside_its_contour_interval": interval_contains_center,
        "all_contour_intervals_have_nonzero_arch_extent": all(
            intervals[label][1] - intervals[label][0]
            >= 1.0
            for label in present_order
        ),
        "all_centres_lifted_to_measured_crown_top": all(
            item.lift_distance_mm <= 1.5
            for item in locations
        ),
        "observation_window_endpoints_are_measured_present_teeth": endpoints <= slot_fdi,
        "contour_following_window_sections_mostly_mapped": mapping_fraction >= 0.80,
        "all_contour_following_profiles_walk_toward_U_exterior": all(
            item["mapped_sample_count"] > 0
            and float(item["minimum_top_outward_offset_mm"]) >= 0.25
            and float(item["minimum_bottom_outward_gain_mm"]) > 0.20
            for item in contour_windows
        ),
        "all_axis_sweep_windows_have_complete_semantic_axes": all(
            isinstance(item.get("axis_sweep"), dict)
            and int(item["axis_sweep"].get("axis_section_count", 0)) >= 2
            and int(item["axis_sweep"].get("angle_section_count", 0)) >= 2
            for item in axis_sweep_windows
        ),
        "guide_was_not_modified": True,
    }

    report_path = output_dir / "tooth_guide_mapping_contact_chord.json"
    preview_path = output_dir / "tooth_guide_mapping_contact_chord_preview.png"
    context_path = output_dir / "tooth_guide_mapping_contact_chord_context.glb"
    preview_instances = []
    by_fdi = {int(item["FDI"]): item for item in contour_records}
    for item in locations:
        contour = np.asarray(by_fdi[item.fdi]["contour_LR_AP_mm"], dtype=float)
        # For plotting only, nearest dense-curve samples preserve the original
        # AP coordinate through the historical (s, AP-offset) convention.
        dense = np.column_stack([frame["curve"].lr, frame["curve"].ap])
        nearest = cKDTree(dense).query(contour, k=1)[1]
        contour_s = np.asarray(frame["curve"].s)[nearest]
        contour_n = contour[:, 1] - np.asarray(frame["curve"].ap)[nearest]
        centroid_arch_ap = float(item.arch_lr_ap_mm[1])
        preview_instances.append({
            "FDI": item.fdi,
            "contour_s_n_mm": np.column_stack([contour_s, contour_n]).tolist(),
            "area_centroid_arch_s_mm": item.arch_s_mm,
            "area_centroid_normal_n_mm": float(item.centroid_lr_ap_mm[1] - centroid_arch_ap),
            "mesial_arch_s_mm": item.contour_interval_s_mm[0],
            "distal_arch_s_mm": item.contour_interval_s_mm[1],
        })
    instance_analysis = {"instances": preview_instances, "assignment": {}, "candidates": []}
    report = {
        "schema_version": "5.1-physical-guide-coverage",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": (
            "tooth_guide_mapping_complete"
            if all(qa.values())
            else "tooth_guide_mapping_needs_review"
        ),
        "case": config["case"],
        "sources": {
            "dental": str(dental_path),
            "guide": str(guide_path),
            "surgical_reference": list(
                contact.get("sources", {}).get("surgical_reference", []) or []
            ),
            "contact_chord_report": str(contact_path),
            "base_coordinate_report": str(base_path) if base_path.exists() else None,
            "enhanced_projection_maps": str(enhanced_path),
        },
        "semantics": {
            "jaw": semantics.jaw,
            "FDI_order": list(semantics.fdi_order),
            "numbered_FDI_order_present_only": list(present_order),
            "present_teeth": sorted(semantics.present_teeth),
            "missing_teeth_without_geometric_centres": sorted(semantics.missing_teeth),
            "excluded_teeth_without_geometric_centres": sorted(semantics.excluded_teeth),
            "rule": "approved contact-chord contours are authoritative; only present FDI are mapped",
        },
        "coordinate_system": coordinate,
        "mapping_parameters": {
            **parameters,
            "tooth_center_source": "uniform area centroid of approved contact-chord contour",
            "tooth_interval_source": "full measured contact-chord contour projected to directed arch",
            "directed_arch_source": (
                "parametric PCHIP through measured present-tooth area centroids "
                "with terminal contour-support extensions"
            ),
            "crown_height_source": "enhanced continuous triangle Z-buffer",
            "enhanced_projection_height_quantile": projection_height_quantile,
            "base_mapping_teeth_used_as_final_centres": False,
        },
        "tooth_slots": slots,
        "observation_windows": windows,
        "diagnostics": {
            "approved_axis_dot_products": axis_agreement,
            "window_endpoint_FDI": sorted(endpoints),
            "requested_contour_following_window_section_count": requested_sections,
            "mapped_contour_following_window_section_count": mapped_sections,
            "contour_following_window_mapping_success_fraction": mapping_fraction,
            "axis_sweep_diagnostic_requested_contour_section_count": (
                axis_diagnostic_requested_sections
            ),
            "axis_sweep_diagnostic_mapped_contour_section_count": (
                axis_diagnostic_mapped_sections
            ),
            "configured_anchor_station_FDI": sorted(required_anchor_fdi),
            "configured_anchor_station_FDI_without_physical_coverage": sorted(
                required_anchor_fdi - covered_fdi
            ),
            "base_mapping_status_not_inherited": (
                None if base is None else base.get("status")
            ),
        },
        "QA": qa,
        "outputs": {
            "report_json": str(report_path),
            "preview_png": str(preview_path),
            "context_glb": str(context_path),
        },
    }
    render_preview(preview_path, frame, slots, windows, instance_analysis)
    export_context(context_path, dental, guide, slots, windows)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def parse_args() -> argparse.Namespace:
    """内部算法说明。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True, type=Path)
    parser.add_argument("--contact-report", type=Path)
    parser.add_argument("--base-mapping-report", type=Path)
    parser.add_argument("--enhanced-maps", type=Path)
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args()


def main() -> int:
    """内部算法说明。"""
    report = run(parse_args())
    print(json.dumps({
        "status": report["status"],
        "QA": report["QA"],
        "outputs": report["outputs"],
    }, ensure_ascii=False, indent=2))
    return 0 if all(report["QA"].values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())

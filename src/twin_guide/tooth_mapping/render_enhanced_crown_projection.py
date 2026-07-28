#!/usr/bin/env python3
"""内部算法说明。\n\nRender continuous silhouette/height/normal/edge crown projections."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import matplotlib.pyplot as plt
import numpy as np
import yaml
from scipy.spatial import cKDTree

from .arch_progress_core_grouping import (
    CORE_GROUPING_POLICIES,
    DEFAULT_POLICY,
    select_crown_core_candidates,
)
from .enhanced_projection import rasterise_crown_triangles
from .fdi import validate_anatomy
from .pipeline import estimate_frame_and_arch, load_mesh, resolve_case_path


def _extent(maps):
    """内部算法说明。"""
    return [
        float(maps["lr_centres"][0]), float(maps["lr_centres"][-1]),
        float(maps["ap_centres"][0]), float(maps["ap_centres"][-1]),
    ]


def _image(axis, values, maps, title, cmap=None, vmin=None, vmax=None):
    """内部算法说明。"""
    array = np.asarray(values)
    displayed = array.transpose(1, 0, 2) if array.ndim == 3 else array.T
    artist = axis.imshow(
        displayed,
        origin="lower",
        extent=_extent(maps),
        interpolation="bilinear",
        cmap=cmap,
        vmin=vmin,
        vmax=vmax,
    )
    axis.set_aspect("equal", adjustable="box")
    axis.set_title(title)
    axis.set_xlabel("患者右 → 左（mm）")
    return artist


def _save_comparison(path, maps, case_label):
    """内部算法说明。"""
    figure, axes = plt.subplots(2, 2, figsize=(14, 12), constrained_layout=True)
    _image(axes[0, 0], maps["silhouette"], maps, "连续牙冠投影轮廓", "gray_r", 0, 1)
    height = _image(axes[0, 1], maps["top_height_mm"], maps, "牙冠顶面高度（mm）", "viridis")
    figure.colorbar(height, ax=axes[0, 1], shrink=0.76)
    _image(axes[1, 0], maps["normal_rgb"], maps, "表面法向映射")
    edge = _image(axes[1, 1], maps["fused_edge"], maps, "融合解剖边界证据", "magma", 0, 1)
    figure.colorbar(edge, ax=axes[1, 1], shrink=0.76)
    axes[0, 0].set_ylabel("前 → 后（mm）")
    axes[1, 0].set_ylabel("前 → 后（mm）")
    figure.suptitle(f"{case_label} — 连续多通道牙冠投影")
    figure.savefig(path, dpi=220, facecolor="white")
    plt.close(figure)


def _save_edge(path, maps, case_label):
    """内部算法说明。"""
    figure, axis = plt.subplots(figsize=(10, 8), constrained_layout=True)
    edge = _image(
        axis, maps["fused_edge"], maps,
        f"{case_label} — 增强牙冠边界投影", "magma", 0, 1,
    )
    axis.set_ylabel("前 → 后（mm）")
    figure.colorbar(edge, ax=axis, shrink=0.78, label="边界证据")
    figure.savefig(path, dpi=300, facecolor="white")
    plt.close(figure)


def _quantile_trials(base_quantile: float) -> list[float]:
    """内部算法说明。\n\nTry the configured single threshold, then lower it for short crowns."""

    lower_bound = max(0.35, base_quantile - 0.15)
    values = np.arange(base_quantile, lower_bound - 1.0e-9, -0.05)
    return [float(round(value, 6)) for value in values]


def _physical_core_diagnostics(
    maps,
    frame,
    ordered_instances,
    core_grouping_policy,
):
    """内部算法说明。\n\nCount physical crown cores inside the same arch corridor as extraction."""

    lr = np.asarray(maps["lr_centres"], dtype=float)
    ap = np.asarray(maps["ap_centres"], dtype=float)
    lr_grid, ap_grid = np.meshgrid(lr, ap, indexing="ij")
    grid_points = np.column_stack([lr_grid.ravel(), ap_grid.ravel()])
    curve_points = np.column_stack([frame["curve"].lr, frame["curve"].ap])
    transverse_distance, _ = cKDTree(curve_points).query(grid_points, k=1)
    candidate_maps = dict(maps)
    corridor = (transverse_distance <= 11.5).reshape(lr_grid.shape)
    candidate_maps["silhouette"] = (
        np.asarray(maps["silhouette"], dtype=bool) & corridor
    )
    candidates, groups = select_crown_core_candidates(
        enhanced_maps=candidate_maps,
        ordered_instances=ordered_instances,
        policy=core_grouping_policy,
    )
    return {
        "candidate_peak_count": len(candidates),
        "physical_core_count": len(groups),
        "physical_core_centres_LR_AP_mm": [
            list(group.center_lr_ap_mm) for group in groups
        ],
    }


def run(args):
    """内部算法说明。"""
    case_yaml = args.case.resolve()
    case_dir = case_yaml.parent
    config = yaml.safe_load(case_yaml.read_text(encoding="utf-8"))
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
    dental = load_mesh(resolve_case_path(case_dir, config["objects"]["dental"], "dental"))
    guide = load_mesh(resolve_case_path(case_dir, config["objects"]["guide"], "guide"))
    parameters = source["mapping_parameters"]
    core_grouping_policy = getattr(
        args,
        "core_grouping_policy",
        DEFAULT_POLICY,
    )
    frame = estimate_frame_and_arch(
        dental, guide, anatomy, semantics,
        float(parameters["crown_height_quantile"]),
        float(parameters["minimum_crown_normal_dot"]),
    )
    vertices = np.asarray(dental.vertices, dtype=float)
    delta = vertices - np.asarray(frame["origin"])
    transformed = np.column_stack([
        delta @ np.asarray(frame["e_lr"]),
        delta @ np.asarray(frame["e_ap"]),
        delta @ np.asarray(frame["e_occ"]),
    ])
    vertex_normals = np.asarray(dental.vertex_normals, dtype=float)
    transformed_normals = np.column_stack([
        vertex_normals @ np.asarray(frame["e_lr"]),
        vertex_normals @ np.asarray(frame["e_ap"]),
        vertex_normals @ np.asarray(frame["e_occ"]),
    ])
    height_quantile = (
        float(args.height_quantile)
        if args.height_quantile is not None
        else float(parameters["crown_height_quantile"])
    )
    if not 0.0 < height_quantile < 1.0:
        raise ValueError("height quantile must lie strictly between 0 and 1")
    numbered_order = tuple(
        label for label in semantics.fdi_order if label in semantics.present_teeth
    )
    slot_by_fdi = {int(item["FDI"]): item for item in source["tooth_slots"]}
    if any(label not in slot_by_fdi for label in numbered_order):
        raise RuntimeError("configured present FDI is missing a tooth-slot prior")
    ordered_instances = [
        SimpleNamespace(
            instance_id=int(label),
            center_lr_ap_mm=tuple(
                float(value) for value in slot_by_fdi[label]["arch_LR_AP_mm"]
            ),
        )
        for label in numbered_order
    ]
    expected_core_count = len(ordered_instances)
    trial_quantiles = (
        [height_quantile]
        if args.height_quantile is not None
        else _quantile_trials(height_quantile)
    )
    trials = []
    selected_trial = None
    best_trial = None
    for trial_quantile in trial_quantiles:
        trial_floor = float(np.quantile(transformed[:, 2], trial_quantile))
        trial_maps = rasterise_crown_triangles(
            vertices_lr_ap_height=transformed,
            faces=np.asarray(dental.faces),
            vertex_normals_lr_ap_occ=transformed_normals,
            height_floor_mm=trial_floor,
            resolution_mm=args.resolution_mm,
        )
        core_diagnostics = _physical_core_diagnostics(
            trial_maps,
            frame,
            ordered_instances,
            core_grouping_policy,
        )
        trial = {
            "height_quantile": float(trial_quantile),
            "height_floor_mm": trial_floor,
            "maps": trial_maps,
            **core_diagnostics,
        }
        trials.append(trial)
        if (
            best_trial is None
            or abs(trial["physical_core_count"] - expected_core_count)
            < abs(best_trial["physical_core_count"] - expected_core_count)
        ):
            best_trial = trial
        if trial["physical_core_count"] == expected_core_count:
            selected_trial = trial
            break
    if selected_trial is None:
        selected_trial = best_trial
    maps = selected_trial["maps"]
    height_quantile = float(selected_trial["height_quantile"])
    height_floor = float(selected_trial["height_floor_mm"])
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    case_label = f"病例 {config['case']['id']}"
    paths = {
        "arrays": output_dir / "enhanced_projection_maps.npz",
        "report": output_dir / "enhanced_projection_report.json",
    }
    if getattr(args, "write_diagnostics", True):
        paths.update({
            "comparison": output_dir / "01_multichannel_projection_comparison.png",
            "enhanced_edge": output_dir / "02_enhanced_crown_edge.png",
        })
        _save_comparison(paths["comparison"], maps, case_label)
        _save_edge(paths["enhanced_edge"], maps, case_label)
    np.savez_compressed(
        paths["arrays"],
        lr_centres=maps["lr_centres"],
        ap_centres=maps["ap_centres"],
        silhouette=maps["silhouette"],
        top_height_mm=maps["top_height_mm"],
        top_normal_lr_ap_occ=maps["top_normal_lr_ap_occ"],
        height_edge=maps["height_edge"],
        normal_edge=maps["normal_edge"],
        curvature=maps["curvature"],
        fused_edge=maps["fused_edge"],
        height_quantile=np.asarray(height_quantile, dtype=float),
        height_floor_mm=np.asarray(height_floor, dtype=float),
        expected_physical_core_count=np.asarray(
            expected_core_count, dtype=int
        ),
        core_grouping_policy=np.asarray(core_grouping_policy),
    )
    report = {
        "case": config["case"],
        "method": "continuous triangle Z-buffer with height/normal/curvature edge fusion",
        "resolution_mm": args.resolution_mm,
        "height_quantile": height_quantile,
        "height_floor_mm": height_floor,
        "core_grouping_policy": core_grouping_policy,
        "height_floor_selection": {
            "mode": (
                "explicit_override"
                if args.height_quantile is not None
                else "automatic_highest_quantile_with_expected_physical_core_count"
            ),
            "configured_height_quantile": float(
                parameters["crown_height_quantile"]
            ),
            "selected_height_quantile": height_quantile,
            "adapted_for_short_crowns": bool(
                args.height_quantile is None
                and height_quantile
                < float(parameters["crown_height_quantile"]) - 1.0e-9
            ),
            "expected_physical_core_count": expected_core_count,
            "selected_physical_core_count": int(
                selected_trial["physical_core_count"]
            ),
            "selection_succeeded": bool(
                selected_trial["physical_core_count"] == expected_core_count
            ),
            "trials": [
                {
                    key: value for key, value in trial.items()
                    if key != "maps"
                }
                for trial in trials
            ],
        },
        "source_mapping_report": str(mapping_path),
        "selected_triangle_count": maps["selected_triangle_count"],
        "covered_pixel_count": maps["covered_pixel_count"],
        "outputs": {key: str(value) for key, value in paths.items()},
    }
    paths["report"].write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def parse_args():
    """内部算法说明。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--mapping-report", type=Path)
    parser.add_argument("--resolution-mm", type=float, default=0.12)
    parser.add_argument(
        "--core-grouping-policy",
        choices=CORE_GROUPING_POLICIES,
        default=DEFAULT_POLICY,
        help=(
            "crown-core merge order; arch_progress is the current TwinGuide "
            "and TwinGuide default"
        ),
    )
    parser.add_argument(
        "--height-quantile",
        type=float,
        help=(
            "optional diagnostic override for the global crown-height floor; "
            "the case mapping value is used by default"
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    print(json.dumps(run(parse_args()), ensure_ascii=False, indent=2))

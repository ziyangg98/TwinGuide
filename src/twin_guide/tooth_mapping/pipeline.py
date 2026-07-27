"""内部算法说明。\n\nFDI-constrained dental-arch and guide mapping pipeline.

This stage does not modify the guide.  It creates a directed anatomical frame,
one slot per configured FDI code, guide-top mappings, and observation-window
top/bottom boundary trajectories suitable for a downstream cutter stage.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

os.environ.setdefault("MPLCONFIGDIR", "/tmp/tooth_guide_mapping_mpl")
os.environ.setdefault("XDG_CACHE_HOME", "/tmp/tooth_guide_mapping_cache")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import trimesh
import yaml
from scipy.interpolate import PchipInterpolator, UnivariateSpline
from scipy.ndimage import gaussian_filter1d

from .fdi import (
    AnatomySemantics,
    FDIError,
    crown_width_prior_mm,
    observation_window_interval,
    signed_midline_distance_prior_mm,
    tooth_interval_from_centres,
    validate_anatomy,
)


EPS = 1e-9
MAX_MISSING_TO_SURGICAL_REFERENCE_MM = 15.0
MIN_ORIENTATION_DISTANCE_MARGIN_MM = 3.0
PALETTE = (
    "#dc2626", "#ea580c", "#d97706", "#65a30d", "#059669", "#0891b2",
    "#2563eb", "#4f46e5", "#7c3aed", "#c026d3", "#db2777", "#475569",
    "#0f766e", "#9333ea", "#b45309", "#0369a1",
)


def unit(vector: np.ndarray) -> np.ndarray:
    """内部算法说明。"""
    value = np.asarray(vector, dtype=float)
    length = float(np.linalg.norm(value))
    if length <= EPS:
        raise RuntimeError("cannot normalize a near-zero vector")
    return value / length


def rounded(value: np.ndarray | float, digits: int = 6):
    """内部算法说明。"""
    array = np.asarray(value)
    if array.ndim == 0:
        return round(float(array), digits)
    return np.round(array.astype(float), digits).tolist()


def load_mesh(path: Path) -> trimesh.Trimesh:
    """内部算法说明。"""
    loaded = trimesh.load(path, force="mesh", process=True)
    if isinstance(loaded, trimesh.Scene):
        loaded = trimesh.util.concatenate(tuple(loaded.geometry.values()))
    if not isinstance(loaded, trimesh.Trimesh) or loaded.is_empty:
        raise RuntimeError(f"could not load mesh: {path}")
    loaded.remove_unreferenced_vertices()
    return loaded


def resolve_case_path(case_dir: Path, node: dict[str, object], object_name: str) -> Path:
    """内部算法说明。"""
    raw = node.get("path")
    if raw is None:
        raise RuntimeError(f"objects.{object_name}.path is not configured")
    result = (case_dir / str(raw)).resolve()
    if not result.exists():
        raise FileNotFoundError(f"objects.{object_name}.path does not exist: {result}")
    return result


def resolve_active_object_paths(
    case_dir: Path,
    node: dict[str, object],
    object_name: str,
) -> list[Path]:
    """内部算法说明。\n\nResolve active paths from either a single-path or files/active_ids node."""

    if node.get("path") is not None:
        return [resolve_case_path(case_dir, node, object_name)]
    records = list(node.get("files", []) or [])
    active_ids = set(node.get("active_ids", []) or [item.get("id") for item in records])
    selected = []
    for record in records:
        if not isinstance(record, dict) or record.get("id") not in active_ids:
            continue
        raw = record.get("path")
        if raw is None:
            raise RuntimeError(f"objects.{object_name}.files contains an active item without path")
        path = (case_dir / str(raw)).resolve()
        if not path.exists():
            raise FileNotFoundError(f"objects.{object_name} active path does not exist: {path}")
        selected.append(path)
    if not selected:
        raise RuntimeError(f"objects.{object_name} has no active geometry")
    unknown_ids = active_ids - {item.get("id") for item in records if isinstance(item, dict)}
    if unknown_ids:
        raise RuntimeError(
            f"objects.{object_name}.active_ids contains unknown ids: {sorted(unknown_ids)}"
        )
    return selected


def surgical_reference_centroid(
    case_dir: Path,
    objects: dict[str, object],
) -> tuple[np.ndarray | None, list[Path]]:
    """内部算法说明。\n\nReturn the area-weighted centroid of active sleeve/surgical geometry."""

    sleeve_node = objects.get("sleeve")
    if not isinstance(sleeve_node, dict):
        return None, []
    paths = resolve_active_object_paths(case_dir, sleeve_node, "sleeve")
    meshes = [load_mesh(path) for path in paths]
    areas = np.asarray([max(float(mesh.area), EPS) for mesh in meshes], dtype=float)
    centroids = np.asarray([np.asarray(mesh.centroid, dtype=float) for mesh in meshes])
    return np.average(centroids, axis=0, weights=areas), paths


def parse_axis(value: object) -> np.ndarray:
    """内部算法说明。"""
    mapping = {
        "+X": np.array([1.0, 0.0, 0.0]), "-X": np.array([-1.0, 0.0, 0.0]),
        "+Y": np.array([0.0, 1.0, 0.0]), "-Y": np.array([0.0, -1.0, 0.0]),
        "+Z": np.array([0.0, 0.0, 1.0]), "-Z": np.array([0.0, 0.0, -1.0]),
    }
    if isinstance(value, str) and value.upper() in mapping:
        return mapping[value.upper()].copy()
    array = np.asarray(value, dtype=float)
    if array.shape != (3,):
        raise RuntimeError(f"axis must be +X/-X/+Y/-Y/+Z/-Z or a 3-vector, got {value!r}")
    return unit(array)


@dataclass
class CurveModel:
    """内部算法说明。"""
    lr: np.ndarray
    ap: np.ndarray
    s: np.ndarray
    apex_index: int
    lr_to_ap: Any
    lr_to_s: Any
    s_to_lr: Any
    s_to_ap: Any

    def at_s(self, values: np.ndarray | float) -> np.ndarray:
        """内部算法说明。"""
        values_array = np.asarray(values, dtype=float)
        return np.column_stack([self.s_to_lr(values_array), self.s_to_ap(values_array)])

    def tangent_at_s(self, value: float) -> np.ndarray:
        """内部算法说明。"""
        delta = max(0.12, 0.003 * (self.s[-1] - self.s[0]))
        lo = max(float(self.s[0]), value - delta)
        hi = min(float(self.s[-1]), value + delta)
        first = self.at_s(np.asarray([lo]))[0]
        second = self.at_s(np.asarray([hi]))[0]
        return unit(second - first)


def select_crown_points(
    dental: trimesh.Trimesh,
    origin: np.ndarray,
    e_occ: np.ndarray,
    quantile: float,
    minimum_normal_dot: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """内部算法说明。"""
    vertices = np.asarray(dental.vertices, dtype=float)
    height = (vertices - origin) @ e_occ
    normals = np.asarray(dental.vertex_normals, dtype=float) @ e_occ
    floor = float(np.quantile(height, quantile))
    mask = (height >= floor) & (normals >= minimum_normal_dot)
    if np.count_nonzero(mask) < 1_000:
        mask = height >= floor
    points = vertices[mask]
    selected_height = height[mask]
    weights = np.maximum(selected_height - floor + 0.15, 0.15)
    return points, selected_height, weights


def _fit_curve(lr: np.ndarray, ap: np.ndarray) -> CurveModel:
    """内部算法说明。"""
    low, high = np.quantile(lr, [0.008, 0.992])
    edges = np.linspace(low, high, 91)
    centres = 0.5 * (edges[:-1] + edges[1:])
    median_ap = []
    used_lr = []
    for left, right, center in zip(edges[:-1], edges[1:], centres):
        select = (lr >= left) & (lr < right)
        if np.count_nonzero(select) < 25:
            continue
        used_lr.append(center)
        median_ap.append(float(np.median(ap[select])))
    used_lr_array = np.asarray(used_lr, dtype=float)
    median_ap_array = np.asarray(median_ap, dtype=float)
    if len(used_lr_array) < 12:
        raise RuntimeError("insufficient crown-support bins to fit a dental arch")
    smooth = max(2.0, 0.18 * len(used_lr_array) * float(np.var(median_ap_array)))
    spline = UnivariateSpline(used_lr_array, median_ap_array, k=3, s=smooth)
    grid_lr = np.linspace(used_lr_array[0], used_lr_array[-1], 601)
    grid_ap = spline(grid_lr)
    # A small rolling smooth keeps the derived tangent stable at tooth contacts.
    grid_ap = gaussian_filter1d(grid_ap, sigma=2.0)
    segment = np.linalg.norm(np.diff(np.column_stack([grid_lr, grid_ap]), axis=0), axis=1)
    cumulative = np.r_[0.0, np.cumsum(segment)]
    apex = int(np.argmin(grid_ap))
    signed_s = cumulative - cumulative[apex]
    return CurveModel(
        lr=grid_lr,
        ap=grid_ap,
        s=signed_s,
        apex_index=apex,
        lr_to_ap=PchipInterpolator(grid_lr, grid_ap, extrapolate=True),
        lr_to_s=PchipInterpolator(grid_lr, signed_s, extrapolate=True),
        s_to_lr=PchipInterpolator(signed_s, grid_lr, extrapolate=True),
        s_to_ap=PchipInterpolator(signed_s, grid_ap, extrapolate=True),
    )


def _support_signal(
    point_s: np.ndarray,
    weights: np.ndarray,
    curve: CurveModel,
) -> tuple[np.ndarray, np.ndarray, Any]:
    """内部算法说明。"""
    edges = np.linspace(curve.s[0], curve.s[-1], 801)
    histogram, _ = np.histogram(point_s, bins=edges, weights=weights)
    counts, _ = np.histogram(point_s, bins=edges)
    density = histogram / np.maximum(counts, 1)
    density *= np.log1p(counts)
    density = gaussian_filter1d(density.astype(float), sigma=10.0)
    if float(np.ptp(density)) > EPS:
        density = (density - np.min(density)) / np.ptp(density)
    centres = 0.5 * (edges[:-1] + edges[1:])
    interpolator = PchipInterpolator(centres, density, extrapolate=False)
    return centres, density, interpolator


def _semantic_fit(
    semantics: AnatomySemantics,
    curve: CurveModel,
    support: Any,
) -> dict[str, object]:
    """内部算法说明。"""
    best: dict[str, object] | None = None
    for scale in np.linspace(0.78, 1.22, 45):
        for offset in np.linspace(-2.5, 2.5, 41):
            locations = {
                label: scale * signed_midline_distance_prior_mm(label, semantics.jaw) + offset
                for label in semantics.fdi_order
            }
            values = {label: float(np.nan_to_num(support(value), nan=0.0)) for label, value in locations.items()}
            present_score = float(np.mean([values[label] for label in semantics.present_teeth]))
            missing_score = float(np.mean([values[label] for label in semantics.missing_teeth])) if semantics.missing_teeth else 0.0
            outside = sum(
                max(curve.s[0] - value, 0.0) + max(value - curve.s[-1], 0.0)
                for value in locations.values()
            )
            score = present_score - 0.75 * missing_score - 0.08 * outside - 0.10 * abs(scale - 1.0)
            candidate = {
                "score": score,
                "scale": float(scale),
                "offset_mm": float(offset),
                "locations": locations,
                "present_support_mean": present_score,
                "missing_support_mean": missing_score,
            }
            if best is None or score > float(best["score"]):
                best = candidate
    assert best is not None
    return best


def _annotate_missing_surgical_consistency(
    candidate: dict[str, object],
    semantics: AnatomySemantics,
    origin: np.ndarray,
    e_ap: np.ndarray,
    e_occ: np.ndarray,
    surgical_reference_point: np.ndarray,
) -> None:
    """内部算法说明。\n\nMeasure the closest missing-slot prediction to the surgical reference.

    The comparison deliberately ignores the occlusal-axis component.  A sleeve
    may extend far above/below the dental arch, while its in-plane location is
    the evidence needed to resolve a left/right reflection.
    """

    curve: CurveModel = candidate["curve"]
    semantic = candidate["semantic"]
    records = []
    for fdi in semantics.missing_teeth:
        s_mm = float(semantic["locations"][fdi])
        lr_ap = curve.at_s(np.asarray([s_mm]))[0]
        predicted = (
            np.asarray(origin, dtype=float)
            + lr_ap[0] * np.asarray(candidate["e_lr"], dtype=float)
            + lr_ap[1] * np.asarray(e_ap, dtype=float)
        )
        delta = np.asarray(surgical_reference_point, dtype=float) - predicted
        planar_delta = delta - float(np.dot(delta, e_occ)) * np.asarray(e_occ, dtype=float)
        records.append({
            "FDI": int(fdi),
            "predicted_global_mm": predicted,
            "planar_distance_mm": float(np.linalg.norm(planar_delta)),
        })
    closest = min(records, key=lambda item: float(item["planar_distance_mm"]))
    candidate["missing_surgical_consistency"] = {
        "closest_missing_FDI": int(closest["FDI"]),
        "predicted_global_mm": np.asarray(closest["predicted_global_mm"], dtype=float),
        "planar_distance_mm": float(closest["planar_distance_mm"]),
        "missing_slot_candidates": records,
    }


def _select_orientation_candidate(
    candidates: list[dict[str, object]],
    consistency_applied: bool,
) -> tuple[dict[str, object], bool, float | None]:
    """内部算法说明。\n\nSelect an LR direction and enforce a fail-closed surgical-site gate."""

    semantic_ranked = sorted(
        candidates, key=lambda item: float(item["semantic"]["score"]), reverse=True
    )
    if not consistency_applied:
        return semantic_ranked[0], False, None

    distance_ranked = sorted(
        candidates,
        key=lambda item: float(
            item["missing_surgical_consistency"]["planar_distance_mm"]
        ),
    )
    selected_distance = float(
        distance_ranked[0]["missing_surgical_consistency"]["planar_distance_mm"]
    )
    next_distance = float(
        distance_ranked[1]["missing_surgical_consistency"]["planar_distance_mm"]
    )
    distance_margin = next_distance - selected_distance
    if selected_distance > MAX_MISSING_TO_SURGICAL_REFERENCE_MM:
        raise RuntimeError(
            "automatic left/right orientation stopped: neither direction places "
            f"a configured missing tooth near the surgical reference "
            f"(best={selected_distance:.3f} mm, allowed<="
            f"{MAX_MISSING_TO_SURGICAL_REFERENCE_MM:.3f} mm)"
        )
    if distance_margin < MIN_ORIENTATION_DISTANCE_MARGIN_MM:
        raise RuntimeError(
            "automatic left/right orientation stopped: missing-to-surgical-site "
            f"distances are ambiguous (best={selected_distance:.3f} mm, "
            f"other={next_distance:.3f} mm, required margin>="
            f"{MIN_ORIENTATION_DISTANCE_MARGIN_MM:.3f} mm)"
        )
    return distance_ranked[0], True, distance_margin


def estimate_frame_and_arch(
    dental: trimesh.Trimesh,
    guide: trimesh.Trimesh,
    anatomy_node: dict[str, object],
    semantics: AnatomySemantics,
    crown_height_quantile: float,
    minimum_normal_dot: float,
    surgical_reference_point: np.ndarray | None = None,
) -> dict[str, object]:
    """内部算法说明。"""
    vertices = np.asarray(dental.vertices, dtype=float)
    origin = np.mean(vertices, axis=0)
    orientation = anatomy_node.get("orientation")
    explicit = isinstance(orientation, dict) and all(
        key in orientation for key in (
            "patient_right_to_left_axis", "anterior_to_posterior_axis", "occlusal_axis"
        )
    )
    covariance = np.cov((vertices - origin).T)
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    if explicit:
        e_lr_base = parse_axis(orientation["patient_right_to_left_axis"])
        e_ap = parse_axis(orientation["anterior_to_posterior_axis"])
        e_occ = parse_axis(orientation["occlusal_axis"])
        orientation_method = "confirmed_axes_from_case_yaml"
    else:
        e_occ = unit(eigenvectors[:, 0])
        if float(np.dot(np.asarray(guide.centroid) - np.asarray(dental.centroid), e_occ)) < 0.0:
            e_occ = -e_occ
        e_lr_base = unit(eigenvectors[:, 2])
        e_ap = unit(eigenvectors[:, 1])
        orientation_method = "PCA_plane_guide_side_occlusal_and_FDI_semantic_direction"

    crown_points, crown_height, crown_weights = select_crown_points(
        dental, origin, e_occ, crown_height_quantile, minimum_normal_dot
    )
    delta = crown_points - origin
    if not explicit:
        provisional_lr = delta @ e_lr_base
        provisional_ap = delta @ e_ap
        correlation = float(np.corrcoef(np.abs(provisional_lr), provisional_ap)[0, 1])
        if not np.isfinite(correlation) or correlation < 0.0:
            e_ap = -e_ap

    candidates = []
    signs = (1.0,) if explicit else (-1.0, 1.0)
    for sign in signs:
        e_lr = sign * e_lr_base
        lr = delta @ e_lr
        ap = delta @ e_ap
        curve = _fit_curve(lr, ap)
        point_s = curve.lr_to_s(np.clip(lr, curve.lr[0], curve.lr[-1]))
        support_s, support_values, support = _support_signal(point_s, crown_weights, curve)
        semantic = _semantic_fit(semantics, curve, support)
        candidates.append({
            "sign": sign,
            "e_lr": e_lr,
            "curve": curve,
            "point_lr": lr,
            "point_ap": ap,
            "point_s": np.asarray(point_s),
            "support_s": support_s,
            "support_values": support_values,
            "semantic": semantic,
        })
    consistency_applied = bool(
        not explicit
        and semantics.missing_teeth
        and surgical_reference_point is not None
        and len(candidates) == 2
    )
    if consistency_applied:
        for candidate in candidates:
            _annotate_missing_surgical_consistency(
                candidate,
                semantics,
                origin,
                e_ap,
                e_occ,
                np.asarray(surgical_reference_point, dtype=float),
            )
    candidates.sort(key=lambda item: float(item["semantic"]["score"]), reverse=True)
    selected, consistency_confirmed, distance_margin = _select_orientation_candidate(
        candidates, consistency_applied
    )
    if consistency_confirmed:
        orientation_method = (
            "PCA_plane_guide_side_occlusal_and_missing_to_surgical_site_consistency"
        )
    # Explicit axes are already confirmed; use a finite sentinel so the JSON
    # remains standards-compliant (no non-standard Infinity literal).
    margin = 1.0 if len(candidates) == 1 else float(
        candidates[0]["semantic"]["score"] - candidates[1]["semantic"]["score"]
    )
    selected_consistency = selected.get("missing_surgical_consistency", {})
    consistency_report = {
        "applied": consistency_applied,
        "confirmed": consistency_confirmed,
        "surgical_reference_global_mm": (
            None if surgical_reference_point is None
            else rounded(np.asarray(surgical_reference_point, dtype=float))
        ),
        "maximum_distance_mm": MAX_MISSING_TO_SURGICAL_REFERENCE_MM,
        "minimum_distance_margin_mm": MIN_ORIENTATION_DISTANCE_MARGIN_MM,
        "selected_missing_FDI": selected_consistency.get("closest_missing_FDI"),
        "selected_distance_mm": selected_consistency.get("planar_distance_mm"),
        "distance_margin_mm": distance_margin,
        "candidates": [
            {
                "sign": float(item["sign"]),
                "semantic_score": float(item["semantic"]["score"]),
                "closest_missing_FDI": item.get("missing_surgical_consistency", {}).get(
                    "closest_missing_FDI"
                ),
                "predicted_missing_global_mm": (
                    None
                    if not item.get("missing_surgical_consistency")
                    else rounded(item["missing_surgical_consistency"]["predicted_global_mm"])
                ),
                "planar_distance_mm": item.get("missing_surgical_consistency", {}).get(
                    "planar_distance_mm"
                ),
            }
            for item in candidates
        ],
    }
    return {
        "origin": origin,
        "e_lr": selected["e_lr"],
        "e_ap": e_ap,
        "e_occ": e_occ,
        "curve": selected["curve"],
        "crown_points": crown_points,
        "crown_height": crown_height,
        "crown_weights": crown_weights,
        "point_lr": selected["point_lr"],
        "point_ap": selected["point_ap"],
        "point_s": selected["point_s"],
        "support_s": selected["support_s"],
        "support_values": selected["support_values"],
        "semantic": selected["semantic"],
        "orientation_method": orientation_method,
        "orientation_score_margin": margin,
        "candidate_scores": [float(item["semantic"]["score"]) for item in candidates],
        "selected_orientation_candidate_index": next(
            index for index, item in enumerate(candidates) if item is selected
        ),
        "missing_to_surgical_site_consistency": consistency_report,
        "eigenvalues": eigenvalues,
    }


def refine_slot_centres(
    semantics: AnatomySemantics,
    frame: dict[str, object],
) -> tuple[dict[int, float], dict[int, tuple[float, float]]]:
    """内部算法说明。"""
    scale = float(frame["semantic"]["scale"])
    offset = float(frame["semantic"]["offset_mm"])
    point_s = np.asarray(frame["point_s"], dtype=float)
    weights = np.asarray(frame["crown_weights"], dtype=float)
    centres = {
        label: scale * signed_midline_distance_prior_mm(label, semantics.jaw) + offset
        for label in semantics.fdi_order
    }
    # Refinement is deliberately one centre per configured present tooth.  A
    # bicuspid may alter the local weighted mean but can never create a slot.
    for label in semantics.fdi_order:
        if label in semantics.missing_teeth:
            continue
        prior = centres[label]
        radius = 0.46 * scale * crown_width_prior_mm(label)
        select = np.abs(point_s - prior) <= radius
        if np.count_nonzero(select) < 50:
            continue
        local_distance = (point_s[select] - prior) / max(radius, EPS)
        local_weights = weights[select] * np.exp(-1.5 * local_distance**2)
        estimate = float(np.average(point_s[select], weights=local_weights))
        centres[label] = prior + float(np.clip(estimate - prior, -1.0, 1.0))
    # Preserve strict order even in crowded or strongly rotated anterior teeth.
    ordered_values = np.asarray([centres[label] for label in semantics.fdi_order], dtype=float)
    if np.any(np.diff(ordered_values) <= 0.25):
        raise RuntimeError("refined FDI centres are not strictly ordered along the arch")
    intervals = {
        label: tooth_interval_from_centres(label, centres, semantics.fdi_order, scale)
        for label in semantics.fdi_order
    }
    return centres, intervals


def curve_global_point(frame: dict[str, object], s_mm: float, height_mm: float = 0.0) -> np.ndarray:
    """内部算法说明。"""
    curve: CurveModel = frame["curve"]
    lr_ap = curve.at_s(np.asarray([s_mm]))[0]
    return (
        np.asarray(frame["origin"])
        + lr_ap[0] * np.asarray(frame["e_lr"])
        + lr_ap[1] * np.asarray(frame["e_ap"])
        + height_mm * np.asarray(frame["e_occ"])
    )


def local_crown_height(frame: dict[str, object], s_mm: float, radius_mm: float) -> float:
    """内部算法说明。"""
    select = np.abs(np.asarray(frame["point_s"]) - s_mm) <= radius_mm
    values = np.asarray(frame["crown_height"])[select]
    if len(values) < 20:
        return float(np.quantile(np.asarray(frame["crown_height"]), 0.70))
    return float(np.quantile(values, 0.72))


def local_arch_frame(frame: dict[str, object], s_mm: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """内部算法说明。"""
    curve: CurveModel = frame["curve"]
    tangent_2d = curve.tangent_at_s(s_mm)
    tangent = unit(tangent_2d[0] * np.asarray(frame["e_lr"]) + tangent_2d[1] * np.asarray(frame["e_ap"]))
    point_2d = curve.at_s(np.asarray([s_mm]))[0]
    interior = np.array([
        curve.lr[curve.apex_index],
        float(np.quantile(curve.ap, 0.92)) + 5.0,
    ])
    normal_2d = unit(np.array([-tangent_2d[1], tangent_2d[0]]))
    if float(np.dot(normal_2d, point_2d - interior)) < 0.0:
        normal_2d = -normal_2d
    outward = unit(normal_2d[0] * np.asarray(frame["e_lr"]) + normal_2d[1] * np.asarray(frame["e_ap"]))
    return tangent, outward, point_2d


def polyline_length(points: np.ndarray) -> float:
    """内部算法说明。"""
    return float(np.sum(np.linalg.norm(np.diff(points, axis=0), axis=1)))


def interpolate_polyline(points: np.ndarray, distance: float) -> np.ndarray:
    """内部算法说明。"""
    segment = np.linalg.norm(np.diff(points, axis=0), axis=1)
    cumulative = np.r_[0.0, np.cumsum(segment)]
    if distance > cumulative[-1] + 1e-6:
        raise RuntimeError(f"section contour length {cumulative[-1]:.3f} mm is below requested {distance:.3f} mm")
    index = int(np.clip(np.searchsorted(cumulative, distance, side="right") - 1, 0, len(segment) - 1))
    fraction = (distance - cumulative[index]) / max(segment[index], EPS)
    return points[index] + fraction * (points[index + 1] - points[index])


def ordered_walk(points: np.ndarray, start: int, direction: int) -> np.ndarray:
    """内部算法说明。"""
    indices = [(start + direction * offset) % len(points) for offset in range(len(points))]
    ordered = points[indices]
    return np.vstack([ordered, ordered[0]])


def contour_arc(points: np.ndarray, start: int, end: int, direction: int) -> np.ndarray:
    """内部算法说明。"""
    indices = [start]
    cursor = start
    for _ in range(len(points)):
        if cursor == end:
            return points[np.asarray(indices, dtype=int)]
        cursor = (cursor + direction) % len(points)
        indices.append(cursor)
    raise RuntimeError("could not trace a finite contour arc")


def resample_polyline(points: np.ndarray, count: int) -> np.ndarray:
    """内部算法说明。"""
    if count < 2:
        raise RuntimeError("polyline resampling requires at least two points")
    if len(points) == 1:
        return np.repeat(points, count, axis=0)
    length = polyline_length(points)
    if length <= EPS:
        return np.repeat(points[:1], count, axis=0)
    return np.asarray([
        interpolate_polyline(points, float(distance))
        for distance in np.linspace(0.0, length, count)
    ])


def polyline_prefix(points: np.ndarray, maximum_length_mm: float) -> np.ndarray:
    """内部算法说明。\n\nReturn a polyline prefix ending exactly at the requested arc length."""

    if len(points) < 2 or polyline_length(points) <= maximum_length_mm:
        return points
    output = [points[0]]
    remaining = float(maximum_length_mm)
    for start, end in zip(points[:-1], points[1:]):
        segment_length = float(np.linalg.norm(end - start))
        if segment_length <= EPS:
            continue
        if segment_length >= remaining:
            output.append(start + (remaining / segment_length) * (end - start))
            return np.asarray(output)
        output.append(end)
        remaining -= segment_length
    return np.asarray(output)


def exterior_facing_component(
    points: np.ndarray,
    base: np.ndarray,
    outward: np.ndarray,
    e_occ: np.ndarray,
    minimum_normal_dot: float = 0.10,
    minimum_outward_offset_mm: float = 0.25,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """内部算法说明。\n\nReturn the connected labial/buccal-facing arc of a section loop.

    A guide-shell section is a closed loop containing occlusal, exterior and
    palatal/lingual portions.  Selecting the globally highest loop point can
    therefore start a window on the U interior.  The exterior arc is the
    connected set containing the most outward point whose 2-D boundary normal
    faces the labial/buccal direction and remains outside the arch centreline.
    """

    uv = np.column_stack(((points - base) @ outward, (points - base) @ e_occ))
    signed_area = 0.5 * float(np.sum(
        uv[:, 0] * np.roll(uv[:, 1], -1)
        - np.roll(uv[:, 0], -1) * uv[:, 1]
    ))
    tangent = np.roll(uv, -1, axis=0) - np.roll(uv, 1, axis=0)
    if signed_area >= 0.0:
        normals = np.column_stack((tangent[:, 1], -tangent[:, 0]))
    else:
        normals = np.column_stack((-tangent[:, 1], tangent[:, 0]))
    normals /= np.maximum(np.linalg.norm(normals, axis=1)[:, None], EPS)

    exterior_index = int(np.argmax(uv[:, 0]))
    # The loop winding from trimesh is not guaranteed.  The normal at the most
    # outward point must face +outward; flip the complete field when necessary.
    if float(normals[exterior_index, 0]) < 0.0:
        normals = -normals
    mask = (
        (normals[:, 0] >= minimum_normal_dot)
        & (uv[:, 0] >= minimum_outward_offset_mm)
    )
    if not bool(mask[exterior_index]):
        raise RuntimeError("section loop has no verified labial/buccal-facing exterior arc")

    component = [exterior_index]
    cursor = (exterior_index + 1) % len(points)
    while bool(mask[cursor]) and cursor not in component:
        component.append(cursor)
        cursor = (cursor + 1) % len(points)
    cursor = (exterior_index - 1) % len(points)
    while bool(mask[cursor]) and cursor not in component:
        component.append(cursor)
        cursor = (cursor - 1) % len(points)
    if len(component) < 3:
        raise RuntimeError("verified exterior arc is too short")
    return np.asarray(component, dtype=int), uv, normals


def guide_physical_coverage_top(
    guide: trimesh.Trimesh,
    base: np.ndarray,
    tangent: np.ndarray,
    outward: np.ndarray,
    e_occ: np.ndarray,
    minimum_normal_dot: float = 0.10,
    minimum_outward_offset_mm: float = 0.25,
) -> dict[str, object]:
    """内部算法说明。\n\nLocate a real exterior guide surface without contour-window shape rules.

    Physical coverage requires a closed guide section and a connected exterior-
    facing surface arc.  Unlike :func:`guide_section_profile`, it does not
    require the wall to flare outward while walking away from its top; that
    slope constraint belongs only to the legacy contour-following cutter.
    """

    section = guide.section(plane_origin=base, plane_normal=tangent)
    if section is None:
        raise RuntimeError("guide coverage section is empty")
    candidates = []
    rejected_open_contours = 0
    rejected_non_exterior_contours = 0
    for raw in section.discrete:
        points = np.asarray(raw, dtype=float)
        if len(points) < 8:
            continue
        closure_error = float(np.linalg.norm(points[0] - points[-1]))
        if closure_error > 1e-5:
            rejected_open_contours += 1
            continue
        points = points[:-1]
        if len(points) < 8:
            continue
        uv = np.column_stack(((points - base) @ outward, (points - base) @ e_occ))
        signed_area = 0.5 * float(np.sum(
            uv[:, 0] * np.roll(uv[:, 1], -1)
            - np.roll(uv[:, 0], -1) * uv[:, 1]
        ))
        boundary_tangent = np.roll(uv, -1, axis=0) - np.roll(uv, 1, axis=0)
        if signed_area >= 0.0:
            normals = np.column_stack((boundary_tangent[:, 1], -boundary_tangent[:, 0]))
        else:
            normals = np.column_stack((-boundary_tangent[:, 1], boundary_tangent[:, 0]))
        normals /= np.maximum(np.linalg.norm(normals, axis=1)[:, None], EPS)
        absolute_exterior_index = int(np.argmax(uv[:, 0]))
        if float(normals[absolute_exterior_index, 0]) < 0.0:
            normals = -normals
        mask = (
            (normals[:, 0] >= minimum_normal_dot)
            & (uv[:, 0] >= minimum_outward_offset_mm)
        )
        valid_indices = np.flatnonzero(mask)
        if len(valid_indices) == 0:
            rejected_non_exterior_contours += 1
            continue
        # A rounded or locally vertical wall can make the absolute outermost
        # vertex nearly tangent. Seed from the most-outward verified neighbour
        # instead of rejecting the complete physical section.
        seed = int(valid_indices[np.argmax(uv[valid_indices, 0])])
        component = [seed]
        cursor = (seed + 1) % len(points)
        while bool(mask[cursor]) and cursor not in component:
            component.append(cursor)
            cursor = (cursor + 1) % len(points)
        cursor = (seed - 1) % len(points)
        while bool(mask[cursor]) and cursor not in component:
            component.append(cursor)
            cursor = (cursor - 1) % len(points)
        if len(component) < 2:
            rejected_non_exterior_contours += 1
            continue
        component_indices = np.asarray(component, dtype=int)
        candidates.append((
            float(np.max(uv[component_indices, 0])),
            points,
            component_indices,
            uv,
            normals,
            seed,
            closure_error,
        ))
    if not candidates:
        raise RuntimeError(
            "guide section has no verified closed exterior surface for physical coverage"
        )

    (
        maximum_outward_offset,
        points,
        exterior_component,
        uv,
        normals,
        seed,
        closure_error,
    ) = max(candidates, key=lambda item: item[0])
    heights = points @ e_occ
    exterior_top_index = int(exterior_component[np.argmax(
        heights[exterior_component] + 1e-5 * uv[exterior_component, 0]
    )])
    global_crest_index = int(np.argmax(heights + 1e-5 * uv[:, 0]))
    route_forward = contour_arc(points, exterior_top_index, global_crest_index, +1)
    route_reverse = contour_arc(points, exterior_top_index, global_crest_index, -1)
    top_route = min((route_forward, route_reverse), key=polyline_length)
    route_was_truncated = False
    if polyline_length(top_route) > 3.0:
        top_route = polyline_prefix(top_route, 3.0)
        route_was_truncated = True
    guide_top = top_route[int(np.argmax(top_route @ e_occ))]
    return {
        "true_top_global_mm": guide_top,
        "coverage_method": "closed_section_exterior_surface_v1",
        "section_contour_count": int(len(section.discrete)),
        "accepted_closed_exterior_contour_count": int(len(candidates)),
        "rejected_open_contour_count": int(rejected_open_contours),
        "rejected_non_exterior_contour_count": int(rejected_non_exterior_contours),
        "exterior_component_point_count": int(len(exterior_component)),
        "maximum_outward_offset_mm": maximum_outward_offset,
        "selected_seed_outward_offset_mm": float(uv[seed, 0]),
        "selected_seed_normal_dot": float(normals[seed, 0]),
        "absolute_outermost_normal_dot": float(
            normals[int(np.argmax(uv[:, 0])), 0]
        ),
        "closure_error_mm": closure_error,
        "top_route_was_truncated": route_was_truncated,
    }


def axis_sweep_angle_bounds(sweep_angle_deg: float) -> tuple[float, float]:
    """内部算法说明。\n\nReturn the configured occlusal/exterior angular interval in degrees.

    Zero degrees is the occlusal direction and positive angles rotate toward
    the labial/buccal exterior.  The exterior side is filled first up to 90
    degrees; any remaining angle is assigned to the arch-interior side.
    """

    angle = float(sweep_angle_deg)
    if not 0.0 < angle <= 180.0:
        raise RuntimeError("axis-sweep angle must lie in (0, 180] degrees")
    return (-max(0.0, angle - 90.0), min(angle, 90.0))


def map_axis_sweep(
    node: dict[str, object],
    frame: dict[str, object],
    centres: dict[int, float],
    guide: trimesh.Trimesh,
    tooth_top_points: dict[int, np.ndarray] | None = None,
) -> dict[str, object]:
    """内部算法说明。\n\nMap a common-height straight axis through two tooth positions.

    The guide is intentionally not used to place G1/G2.  It is retained in the
    signature because guide penetration is evaluated by the downstream cutter.
    """

    start_fdi = int(node["start_fdi"])
    end_fdi = int(node["end_fdi"])
    drop_mm = float(node.get("axis_drop_mm", 1.0))
    if drop_mm <= 0.0:
        raise RuntimeError("axis_drop_mm must be positive")
    sweep_angle_deg = float(node.get("sweep_angle_deg", 90.0))
    minimum_angle_deg, maximum_angle_deg = axis_sweep_angle_bounds(
        sweep_angle_deg
    )
    e_occ = unit(np.asarray(frame["e_occ"], dtype=float))
    endpoint_records = []
    for fdi in (start_fdi, end_fdi):
        s_mm = float(centres[fdi])
        crown_height = local_crown_height(frame, s_mm, 3.5)
        fallback = curve_global_point(frame, s_mm, crown_height)
        base = np.asarray(
            (tooth_top_points or {}).get(fdi, fallback), dtype=float
        )
        tangent, outward, _ = local_arch_frame(frame, s_mm)
        endpoint_records.append({
            "FDI": fdi,
            "arch_s_mm": s_mm,
            "base": base,
            "tangent": tangent,
            "outward": outward,
            "tooth_top": base,
            "tooth_top_height_mm": float(base @ e_occ),
        })

    top_heights = [
        float(item["tooth_top_height_mm"]) for item in endpoint_records
    ]
    reference_index = int(np.argmax(top_heights))
    common_height_mm = top_heights[reference_index] - drop_mm
    anchors = [
        np.asarray(item["tooth_top"], dtype=float)
        + (common_height_mm - float(item["tooth_top_height_mm"])) * e_occ
        for item in endpoint_records
    ]
    axis_vector = anchors[1] - anchors[0]
    axis_length_mm = float(np.linalg.norm(axis_vector))
    if axis_length_mm <= 0.5:
        raise RuntimeError("axis-sweep endpoints do not define a usable straight axis")
    axis_direction = axis_vector / axis_length_mm
    zero_direction = e_occ - float(np.dot(e_occ, axis_direction)) * axis_direction
    zero_direction = unit(zero_direction)
    mean_outward = unit(endpoint_records[0]["outward"] + endpoint_records[1]["outward"])
    exterior_direction = (
        mean_outward
        - float(np.dot(mean_outward, axis_direction)) * axis_direction
        - float(np.dot(mean_outward, zero_direction)) * zero_direction
    )
    exterior_direction = unit(exterior_direction)
    if float(np.dot(exterior_direction, mean_outward)) < 0.0:
        exterior_direction = -exterior_direction
    axis_sections = int(node.get(
        "axis_sections", max(3, math.ceil(axis_length_mm / 0.6) + 1)
    ))
    angular_spacing_deg = float(node.get("angular_spacing_deg", 3.0))
    if angular_spacing_deg <= 0.0:
        raise RuntimeError("angular_spacing_deg must be positive")
    angle_sections = int(node.get(
        "angle_sections",
        max(3, math.ceil(sweep_angle_deg / angular_spacing_deg) + 1),
    ))
    if axis_sections < 2 or angle_sections < 2:
        raise RuntimeError("axis-sweep sampling requires at least two sections per axis")
    return {
        "rule": "higher endpoint tooth top minus a common fixed height",
        "axis_drop_mm": drop_mm,
        "sweep_angle_deg": sweep_angle_deg,
        "minimum_angle_deg": minimum_angle_deg,
        "maximum_angle_deg": maximum_angle_deg,
        "reference_FDI": int(endpoint_records[reference_index]["FDI"]),
        "reference_top_height_mm": top_heights[reference_index],
        "common_axis_height_mm": common_height_mm,
        "top_height_difference_mm": abs(top_heights[1] - top_heights[0]),
        "endpoint_tooth_top_global_mm": [
            rounded(item["tooth_top"])
            for item in endpoint_records
        ],
        "axis_start_global_mm": rounded(anchors[0]),
        "axis_end_global_mm": rounded(anchors[1]),
        "axis_direction_global": rounded(axis_direction),
        "zero_degree_occlusal_direction_global": rounded(zero_direction),
        "positive_90_degree_exterior_direction_global": rounded(exterior_direction),
        "axis_length_mm": axis_length_mm,
        "axis_section_count": axis_sections,
        "angle_section_count": angle_sections,
    }


def guide_section_profile(
    guide: trimesh.Trimesh,
    base: np.ndarray,
    tangent: np.ndarray,
    outward: np.ndarray,
    e_occ: np.ndarray,
    top_margin_mm: float,
    height_mm: float,
    profile_spacing_mm: float = 0.25,
) -> dict[str, object]:
    """内部算法说明。"""
    section = guide.section(plane_origin=base, plane_normal=tangent)
    if section is None:
        raise RuntimeError("guide section is empty")
    loops = []
    rejected_loops = 0
    for raw in section.discrete:
        points = np.asarray(raw, dtype=float)
        if len(points) < 8:
            continue
        if np.linalg.norm(points[0] - points[-1]) <= 1e-5:
            points = points[:-1]
        if len(points) < 8:
            continue
        try:
            exterior_component, uv, _ = exterior_facing_component(
                points, base, outward, e_occ
            )
        except RuntimeError:
            rejected_loops += 1
            continue
        loops.append((
            float(np.max(uv[:, 0])),
            polyline_length(np.vstack([points, points[0]])),
            points,
            exterior_component,
            uv,
        ))
    if not loops:
        raise RuntimeError("guide section has no verified exterior closed contour")
    _, loop_length, points, exterior_component, uv = max(loops, key=lambda item: item[0])
    heights = points @ e_occ
    exterior_top_index = int(exterior_component[np.argmax(
        heights[exterior_component] + 1e-5 * uv[exterior_component, 0]
    )])
    global_crest_index = int(np.argmax(heights + 1e-5 * uv[:, 0]))
    forward = ordered_walk(points, exterior_top_index, +1)
    reverse = ordered_walk(points, exterior_top_index, -1)
    probe = min(1.0, 0.40 * min(polyline_length(forward), polyline_length(reverse)))
    forward_gain = float(np.dot(interpolate_polyline(forward, probe) - points[exterior_top_index], outward))
    reverse_gain = float(np.dot(interpolate_polyline(reverse, probe) - points[exterior_top_index], outward))
    walk = forward if forward_gain >= reverse_gain else reverse
    exterior_top = interpolate_polyline(walk, top_margin_mm)
    bottom = interpolate_polyline(walk, top_margin_mm + height_mm)
    top_outward_offset = float(np.dot(exterior_top - base, outward))
    if top_outward_offset < 0.25:
        raise RuntimeError(
            "selected guide top is not on the labial/buccal exterior "
            f"(outward offset {top_outward_offset:.3f} mm)"
        )
    bottom_outward_gain = float(np.dot(bottom - exterior_top, outward))
    if bottom_outward_gain <= 0.20:
        raise RuntimeError(
            "selected guide contour walks toward the U interior instead of the "
            f"labial/buccal exterior (outward gain {bottom_outward_gain:.3f} mm)"
        )
    profile_count = max(2, int(math.ceil(height_mm / max(profile_spacing_mm, 0.05))) + 1)
    profile_distances = np.linspace(top_margin_mm, top_margin_mm + height_mm, profile_count)
    exterior_profile = np.asarray([
        interpolate_polyline(walk, float(distance)) for distance in profile_distances
    ])
    top_cap_length = 0.0
    top_cap_was_truncated = False
    crest_point = points[exterior_top_index]
    if top_margin_mm <= EPS:
        route_forward = contour_arc(points, exterior_top_index, global_crest_index, +1)
        route_reverse = contour_arc(points, exterior_top_index, global_crest_index, -1)
        top_route = min((route_forward, route_reverse), key=polyline_length)
        if polyline_length(top_route) > 3.0:
            top_route = polyline_prefix(top_route, 3.0)
            top_cap_was_truncated = True
        route_heights = top_route @ e_occ
        local_crest_index = int(np.argmax(route_heights))
        # Reverse the safe exterior-to-crest prefix so the combined cutter
        # starts at the crest, reaches the exterior top, then walks downward.
        top_cap = top_route[:local_crest_index + 1][::-1]
        top_cap_length = polyline_length(top_cap)
        cap_profile = resample_polyline(top_cap, 7)
        # The cap stops at the crest and joins the exterior 4 mm profile.  It
        # never continues down the palatal/lingual branch.
        profile = np.vstack([cap_profile[:-1], exterior_profile])
        crest_point = top_cap[0]
        window_top = crest_point
    else:
        profile = exterior_profile
        window_top = exterior_top
    return {
        "true_top_global_mm": crest_point,
        "window_top_global_mm": window_top,
        "outer_profile_top_global_mm": exterior_top,
        "window_bottom_global_mm": bottom,
        "window_profile_global_mm": profile,
        "top_cap_length_mm": top_cap_length,
        "top_cap_was_truncated": top_cap_was_truncated,
        "window_top_outward_offset_mm": top_outward_offset,
        "window_bottom_outward_gain_mm": bottom_outward_gain,
        "outer_top_height_gap_mm": float(np.dot(crest_point - points[exterior_top_index], e_occ)),
        "exterior_component_point_count": int(len(exterior_component)),
        "selected_contour_length_mm": loop_length,
        "section_contour_count": len(loops) + rejected_loops,
        "rejected_non_exterior_contour_count": rejected_loops,
    }


def map_slots_to_geometry(
    semantics: AnatomySemantics,
    frame: dict[str, object],
    centres: dict[int, float],
    intervals: dict[int, tuple[float, float]],
    guide: trimesh.Trimesh,
) -> list[dict[str, object]]:
    """内部算法说明。"""
    result = []
    scale = float(frame["semantic"]["scale"])
    for sequence_index, label in enumerate(semantics.fdi_order):
        s_mm = float(centres[label])
        crown_height = local_crown_height(frame, s_mm, 0.55 * scale * crown_width_prior_mm(label))
        base = curve_global_point(frame, s_mm, crown_height)
        tangent, outward, lr_ap = local_arch_frame(frame, s_mm)
        guide_top = None
        coverage = None
        mapping_error = None
        try:
            coverage = guide_physical_coverage_top(
                guide, base, tangent, outward, np.asarray(frame["e_occ"])
            )
            guide_top = coverage["true_top_global_mm"]
        except Exception as error:
            mapping_error = str(error)
        result.append({
            "FDI": label,
            "sequence_index": sequence_index,
            "status": "missing_slot" if label in semantics.missing_teeth else "present",
            "guide_coverage_status": "mapped" if guide_top is not None else "outside_guide_coverage",
            "arch_s_mm": s_mm,
            "arch_interval_s_mm": rounded(intervals[label]),
            "arch_LR_AP_mm": rounded(lr_ap),
            "dental_crown_point_global_mm": rounded(base),
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
    return result


def map_windows(
    window_nodes: list[dict[str, object]],
    frame: dict[str, object],
    centres: dict[int, float],
    intervals: dict[int, tuple[float, float]],
    guide: trimesh.Trimesh,
    tooth_top_points: dict[int, np.ndarray] | None = None,
) -> list[dict[str, object]]:
    """内部算法说明。"""
    reports = []
    for node in window_nodes:
        window_id = str(node.get("id", "window"))
        opening_geometry = str(node.get("opening_geometry", "contour_following"))
        if opening_geometry not in {"contour_following", "axis_sweep"}:
            raise RuntimeError(
                f"observation window {window_id!r} has unsupported opening_geometry "
                f"{opening_geometry!r}"
            )
        opening_side = str(node.get("opening_side", "labial_buccal_exterior"))
        if opening_side != "labial_buccal_exterior":
            raise RuntimeError(
                f"observation window {window_id!r} has unsupported opening_side "
                f"{opening_side!r}; only 'labial_buccal_exterior' is allowed"
            )
        start = int(node["start_fdi"])
        end = int(node["end_fdi"])
        extent_mode = str(node.get("extent_mode", "center_to_center"))
        s_start, s_end = observation_window_interval(start, end, extent_mode, centres, intervals)
        requested = int(node.get("requested_sections", max(9, math.ceil(abs(s_end - s_start) / 0.8) + 1)))
        samples_s = np.linspace(s_start, s_end, requested)
        height_mm = float(node.get("height_mm", 4.0))
        top_open = bool(node.get("top_open", True))
        top_margin = 0.0 if top_open else float(node.get("top_bridge_margin_mm", 0.5))
        samples = []
        failures = []
        for index, s_mm in enumerate(samples_s):
            crown_height = local_crown_height(frame, float(s_mm), 3.5)
            base = curve_global_point(frame, float(s_mm), crown_height)
            tangent, outward, _ = local_arch_frame(frame, float(s_mm))
            try:
                profile = guide_section_profile(
                    guide, base, tangent, outward, np.asarray(frame["e_occ"]), top_margin, height_mm
                )
                samples.append({
                    "sample_index": index,
                    "arch_s_mm": float(s_mm),
                    "true_top_global_mm": rounded(profile["true_top_global_mm"]),
                    "window_top_global_mm": rounded(profile["window_top_global_mm"]),
                    "outer_profile_top_global_mm": rounded(profile["outer_profile_top_global_mm"]),
                    "window_bottom_global_mm": rounded(profile["window_bottom_global_mm"]),
                    "window_profile_global_mm": rounded(profile["window_profile_global_mm"]),
                    "top_cap_length_mm": rounded(profile["top_cap_length_mm"]),
                    "top_cap_was_truncated": bool(profile["top_cap_was_truncated"]),
                    "window_top_outward_offset_mm": rounded(profile["window_top_outward_offset_mm"]),
                    "window_bottom_outward_gain_mm": rounded(profile["window_bottom_outward_gain_mm"]),
                    "local_outward_global": rounded(outward),
                    "local_tangent_global": rounded(tangent),
                })
            except Exception as error:
                failures.append({"sample_index": index, "arch_s_mm": float(s_mm), "error": str(error)})
        axis_sweep = None
        if opening_geometry == "axis_sweep":
            axis_sweep = map_axis_sweep(
                node, frame, centres, guide, tooth_top_points
            )
        reports.append({
            "id": window_id,
            "opening_geometry": opening_geometry,
            "start_fdi": start,
            "end_fdi": end,
            "extent_mode": extent_mode,
            "arch_interval_s_mm": [float(s_start), float(s_end)],
            "height_mm": height_mm,
            "top_open": top_open,
            "opening_side": opening_side,
            "contour_samples_role": (
                "cutter_geometry"
                if opening_geometry == "contour_following"
                else "diagnostic_only"
            ),
            "top_bridge_margin_mm": top_margin,
            "requested_sample_count": requested,
            "mapped_sample_count": len(samples),
            "failed_sample_count": len(failures),
            "minimum_bottom_outward_gain_mm": (
                min(item["window_bottom_outward_gain_mm"] for item in samples)
                if samples else None
            ),
            "minimum_top_outward_offset_mm": (
                min(item["window_top_outward_offset_mm"] for item in samples)
                if samples else None
            ),
            "samples": samples,
            "failures": failures,
            "axis_sweep": axis_sweep,
        })
    return reports


def _colored(mesh: trimesh.Trimesh, rgba: tuple[int, int, int, int]) -> trimesh.Trimesh:
    """内部算法说明。"""
    result = mesh.copy()
    result.visual.face_colors = np.tile(np.asarray(rgba, dtype=np.uint8), (len(result.faces), 1))
    return result


def _sphere(point: np.ndarray, color: str, radius: float) -> trimesh.Trimesh:
    """内部算法说明。"""
    mesh = trimesh.creation.icosphere(subdivisions=2, radius=radius)
    mesh.apply_translation(point)
    rgb = tuple(int(color[index:index + 2], 16) for index in (1, 3, 5))
    mesh.visual.face_colors = np.tile(np.asarray((*rgb, 255), dtype=np.uint8), (len(mesh.faces), 1))
    return mesh


def export_context(
    path: Path,
    dental: trimesh.Trimesh,
    guide: trimesh.Trimesh,
    slots: list[dict[str, object]],
    windows: list[dict[str, object]],
) -> None:
    """内部算法说明。"""
    scene = trimesh.Scene()
    scene.add_geometry(_colored(dental, (148, 163, 184, 45)), node_name="dental", geom_name="dental")
    scene.add_geometry(_colored(guide, (198, 166, 107, 60)), node_name="guide", geom_name="guide")
    for index, slot in enumerate(slots):
        color = PALETTE[index % len(PALETTE)]
        point = np.asarray(slot["dental_crown_point_global_mm"], dtype=float)
        scene.add_geometry(_sphere(point, color, 0.52), node_name=f"FDI_{slot['FDI']}_dental", geom_name=f"FDI_{slot['FDI']}_dental")
        if slot["guide_top_global_mm"] is not None:
            top = np.asarray(slot["guide_top_global_mm"], dtype=float)
            scene.add_geometry(_sphere(top, color, 0.38), node_name=f"FDI_{slot['FDI']}_guide", geom_name=f"FDI_{slot['FDI']}_guide")
    for window in windows:
        top_points = [np.asarray(item["window_top_global_mm"], dtype=float) for item in window["samples"]]
        bottom_points = [np.asarray(item["window_bottom_global_mm"], dtype=float) for item in window["samples"]]
        if len(top_points) >= 2:
            for name, points, rgba in (
                ("top", top_points, (239, 68, 68, 255)),
                ("bottom", bottom_points, (59, 130, 246, 255)),
            ):
                path3d = trimesh.load_path(np.asarray(points))
                path3d.colors = np.tile(np.asarray(rgba, dtype=np.uint8), (len(path3d.entities), 1))
                scene.add_geometry(path3d, node_name=f"window_{window['id']}_{name}", geom_name=f"window_{window['id']}_{name}")
    scene.export(path)


def render_preview(
    path: Path,
    frame: dict[str, object],
    slots: list[dict[str, object]],
    windows: list[dict[str, object]],
    instance_analysis: dict[str, object] | None = None,
) -> None:
    """内部算法说明。"""
    figure, axes = plt.subplots(1, 2, figsize=(18, 8), constrained_layout=True)
    axis = axes[0]
    points = np.column_stack([frame["point_lr"], frame["point_ap"]])
    if len(points) > 80_000:
        points = points[np.linspace(0, len(points) - 1, 80_000, dtype=int)]
    axis.scatter(points[:, 0], points[:, 1], s=0.25, c="#cbd5e1", alpha=0.24)
    curve: CurveModel = frame["curve"]
    axis.plot(curve.lr, curve.ap, color="#111827", linewidth=1.8, label="directed arch")
    if instance_analysis is not None:
        for index, instance in enumerate(instance_analysis.get("instances", [])):
            contour = np.asarray(instance.get("contour_s_n_mm", []), dtype=float)
            if contour.ndim != 2 or contour.shape[0] < 2:
                continue
            base = curve.at_s(contour[:, 0])
            contour_lr = base[:, 0]
            contour_ap = base[:, 1] + contour[:, 1]
            color = PALETTE[index % len(PALETTE)]
            axis.plot(contour_lr, contour_ap, color=color, linewidth=1.1, alpha=0.9)
        assigned_ids = {
            int(value) for value in instance_analysis.get("assignment", {}).values()
        }
        terminal_ids = {
            int(value) for value in instance_analysis.get(
                "high_confidence_unmatched_terminal_candidate_ids", []
            )
        }
        for candidate in instance_analysis.get("candidates", []):
            candidate_id = int(candidate["candidate_id"])
            s_mm = float(candidate["arch_s_mm"])
            lr, ap = curve.at_s(np.asarray([s_mm]))[0]
            if candidate_id in assigned_ids:
                axis.scatter(
                    lr, ap, s=38, marker="o", facecolors="none",
                    edgecolors="#16a34a", linewidths=1.1, zorder=4,
                )
            else:
                color = "#dc2626" if candidate_id in terminal_ids else "#94a3b8"
                axis.scatter(lr, ap, s=28, marker="x", c=color, linewidths=1.0, zorder=3)
    centroid_by_fdi = {
        int(item["FDI"]): item
        for item in (instance_analysis or {}).get("instances", [])
    }
    for index, slot in enumerate(slots):
        arch_lr, arch_ap = slot["arch_LR_AP_mm"]
        instance = centroid_by_fdi.get(int(slot["FDI"]))
        if instance is not None and "area_centroid_arch_s_mm" in instance:
            centroid_s = float(instance["area_centroid_arch_s_mm"])
            centroid_n = float(instance["area_centroid_normal_n_mm"])
            centroid_base = curve.at_s(np.asarray([centroid_s]))[0]
            lr = float(centroid_base[0])
            ap = float(centroid_base[1] + centroid_n)
            axis.plot(
                [lr, arch_lr], [ap, arch_ap], color="#64748b",
                linewidth=0.65, linestyle="--", alpha=0.75, zorder=3,
            )
        else:
            lr, ap = arch_lr, arch_ap
        color = PALETTE[index % len(PALETTE)]
        marker = "x" if slot["status"] == "missing_slot" else "o"
        axis.scatter(lr, ap, s=80, marker=marker, c=color, edgecolors="black" if marker == "o" else None, zorder=5)
        axis.text(lr + 0.35, ap + 0.35, str(slot["FDI"]), fontsize=9, weight="bold")
    axis.set_aspect("equal", adjustable="box")
    axis.set_xlabel("patient right → left (mm)")
    axis.set_ylabel("anterior → posterior (mm)")
    axis.set_title(
        "Physical-tooth area centroids; dashed links show arch projection"
        if instance_analysis is not None
        else "FDI slots constrained by case.yaml"
    )
    axis.legend(loc="best")

    axis = axes[1]
    axis.plot(frame["support_s"], frame["support_values"], color="#64748b", linewidth=1.5, label="crown support")
    if instance_analysis is not None:
        for index, instance in enumerate(instance_analysis.get("instances", [])):
            color = PALETTE[index % len(PALETTE)]
            axis.axvspan(
                float(instance["mesial_arch_s_mm"]),
                float(instance["distal_arch_s_mm"]),
                color=color,
                alpha=0.055,
            )
        assigned_ids = {
            int(value) for value in instance_analysis.get("assignment", {}).values()
        }
        terminal_ids = {
            int(value) for value in instance_analysis.get(
                "high_confidence_unmatched_terminal_candidate_ids", []
            )
        }
        for candidate in instance_analysis.get("candidates", []):
            candidate_id = int(candidate["candidate_id"])
            if candidate_id in assigned_ids:
                color = "#16a34a"
                width = 0.8
            elif candidate_id in terminal_ids:
                color = "#dc2626"
                width = 1.4
            else:
                color = "#cbd5e1"
                width = 0.6
            axis.axvline(
                float(candidate["arch_s_mm"]), color=color,
                linewidth=width, linestyle=":", alpha=0.8,
            )
    for index, slot in enumerate(slots):
        color = PALETTE[index % len(PALETTE)]
        style = "--" if slot["status"] == "missing_slot" else "-"
        axis.axvline(slot["arch_s_mm"], color=color, linewidth=1.1, linestyle=style)
        axis.text(slot["arch_s_mm"], 1.02, str(slot["FDI"]), rotation=90, va="bottom", ha="center", fontsize=8)
    for window in windows:
        lo, hi = window["arch_interval_s_mm"]
        axis.axvspan(min(lo, hi), max(lo, hi), alpha=0.10, label=f"window:{window['id']}")
    axis.set_ylim(0.0, 1.15)
    axis.set_xlabel("directed arch distance s (mm)")
    axis.set_ylabel("normalized crown-support evidence")
    axis.set_title(
        "Physical contour spans and projected area-centroid positions"
        if instance_analysis is not None
        else "One configured FDI slot per semantic position"
    )
    axis.legend(loc="lower center", ncol=2, fontsize=8)
    figure.savefig(path, dpi=220)
    plt.close(figure)


def run_case_mapping(
    case_yaml: Path,
    output_dir: Path | None = None,
    crown_height_quantile: float = 0.55,
    minimum_normal_dot: float = 0.05,
) -> dict[str, object]:
    """内部算法说明。"""
    case_yaml = Path(case_yaml).resolve()
    case_dir = case_yaml.parent
    config = yaml.safe_load(case_yaml.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise RuntimeError(f"case YAML must contain a mapping: {case_yaml}")
    semantics = validate_anatomy(config.get("anatomy", {}))
    objects = config.get("objects", {})
    if not isinstance(objects, dict):
        raise RuntimeError("objects must be a mapping")
    dental_path = resolve_case_path(case_dir, objects.get("dental", {}), "dental")
    guide_path = resolve_case_path(case_dir, objects.get("guide", {}), "guide")
    surgical_reference_point, surgical_reference_paths = surgical_reference_centroid(
        case_dir, objects
    )
    destination = Path(output_dir).resolve() if output_dir else case_dir / "输出/tooth_guide_mapping"
    destination.mkdir(parents=True, exist_ok=True)

    dental = load_mesh(dental_path)
    guide = load_mesh(guide_path)
    frame = estimate_frame_and_arch(
        dental, guide, config.get("anatomy", {}), semantics,
        crown_height_quantile, minimum_normal_dot,
        surgical_reference_point=surgical_reference_point,
    )
    centres, intervals = refine_slot_centres(semantics, frame)
    slots = map_slots_to_geometry(semantics, frame, centres, intervals, guide)
    window_nodes = list(config.get("design", {}).get("observation_windows", []) or [])
    windows = map_windows(window_nodes, frame, centres, intervals, guide)

    slot_mapping_failures = [item["FDI"] for item in slots if item["guide_mapping_error"]]
    contour_windows = [
        item for item in windows if item["opening_geometry"] == "contour_following"
    ]
    axis_sweep_windows = [
        item for item in windows if item["opening_geometry"] == "axis_sweep"
    ]
    axis_diagnostic_failed_sections = sum(
        int(item["failed_sample_count"]) for item in axis_sweep_windows
    )
    axis_diagnostic_requested_sections = sum(
        int(item["requested_sample_count"]) for item in axis_sweep_windows
    )
    failed_window_sections = sum(
        int(item["failed_sample_count"]) for item in contour_windows
    )
    requested_window_sections = sum(
        int(item["requested_sample_count"]) for item in contour_windows
    )
    window_success_fraction = (
        1.0
        if requested_window_sections == 0
        else (requested_window_sections - failed_window_sections)
        / requested_window_sections
    )
    explicit_orientation = frame["orientation_method"] == "confirmed_axes_from_case_yaml"
    orientation_consistency = frame["missing_to_surgical_site_consistency"]
    consistency_confirmed = bool(orientation_consistency["confirmed"])
    present_support_mean = float(frame["semantic"]["present_support_mean"])
    missing_support_mean = float(frame["semantic"]["missing_support_mean"])
    automatic_orientation_missing_slot_support_is_distinct = bool(
        explicit_orientation
        or not semantics.missing_teeth
        or missing_support_mean + 0.08 < present_support_mean
    )
    qa = {
        "FDI_classification_is_complete_exclusive_and_jaw_valid": True,
        "FDI_sets_are_disjoint_and_jaw_valid": True,
        "FDI_order_is_canonical_and_complete": len(semantics.fdi_order) == len(semantics.present_teeth) + len(semantics.missing_teeth),
        "one_and_only_one_slot_per_configured_FDI": len(slots) == len(semantics.fdi_order) == len({item["FDI"] for item in slots}),
        "no_geometry_created_unconfigured_FDI": {item["FDI"] for item in slots} == set(semantics.fdi_order),
        "directed_arch_centres_are_strictly_monotonic": bool(np.all(np.diff([item["arch_s_mm"] for item in slots]) > 0.25)),
        # A declared missing slot must look materially less crown-like than
        # configured present slots.  Without this gate, a mirrored LR axis can
        # place the missing FDI label on a real tooth yet still win the global
        # semantic candidate score, as happened in case #15.
        "automatic_orientation_missing_slot_support_is_distinct": automatic_orientation_missing_slot_support_is_distinct,
        "missing_to_surgical_site_orientation_is_consistent": bool(
            explicit_orientation
            or not orientation_consistency["applied"]
            or consistency_confirmed
        ),
        # A base guide commonly stops before the terminal teeth.  Those teeth
        # remain valid dental slots but are explicitly outside guide coverage.
        # Window mapping, rather than full-arch coverage, is the hard gate.
        "guide_coverage_status_recorded_for_every_slot": all(
            item["guide_coverage_status"] in {"mapped", "outside_guide_coverage"}
            for item in slots
        ),
        "contour_following_window_sections_mostly_mapped": window_success_fraction >= 0.80,
        "all_contour_following_profiles_walk_toward_U_exterior": all(
            window["mapped_sample_count"] > 0
            and float(window["minimum_top_outward_offset_mm"]) >= 0.25
            and float(window["minimum_bottom_outward_gain_mm"]) > 0.20
            for window in contour_windows
        ),
        "all_axis_sweep_windows_have_complete_semantic_axes": all(
            isinstance(window.get("axis_sweep"), dict)
            and int(window["axis_sweep"].get("axis_section_count", 0)) >= 2
            and int(window["axis_sweep"].get("angle_section_count", 0)) >= 2
            for window in axis_sweep_windows
        ),
        "orientation_is_confirmed_or_semantically_disambiguated": bool(
            explicit_orientation
            or consistency_confirmed
            or float(frame["orientation_score_margin"]) >= 0.020
            or (
                float(frame["orientation_score_margin"]) >= 0.005
                and
                bool(semantics.missing_teeth)
                and float(frame["semantic"]["missing_support_mean"]) + 0.15
                < float(frame["semantic"]["present_support_mean"])
            )
        ),
        "guide_was_not_modified": True,
    }

    report_path = destination / "tooth_guide_mapping.json"
    preview_path = destination / "tooth_guide_mapping_preview.png"
    context_path = destination / "tooth_guide_mapping_context.glb"
    report = {
        "schema_version": "1.2-physical-guide-coverage",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "tooth_guide_mapping_complete" if all(qa.values()) else "tooth_guide_mapping_needs_review",
        "case": {
            "id": config.get("case", {}).get("id", case_dir.name),
            "case_yaml": str(case_yaml),
        },
        "sources": {
            "dental": str(dental_path),
            "guide": str(guide_path),
            "surgical_reference": [str(path) for path in surgical_reference_paths],
        },
        "semantics": {
            "jaw": semantics.jaw,
            "FDI_order": list(semantics.fdi_order),
            "present_teeth": sorted(semantics.present_teeth),
            "missing_teeth": sorted(semantics.missing_teeth),
            "excluded_teeth": sorted(semantics.excluded_teeth),
            "rule": "configured FDI slots are authoritative; geometric cusp regions cannot create labels",
        },
        "coordinate_system": {
            "origin_global_mm": rounded(frame["origin"]),
            "e_patient_right_to_left": rounded(frame["e_lr"]),
            "e_anterior_to_posterior": rounded(frame["e_ap"]),
            "e_occ": rounded(frame["e_occ"]),
            "method": frame["orientation_method"],
            "semantic_orientation_score_margin": rounded(frame["orientation_score_margin"]),
            "orientation_candidate_scores": rounded(frame["candidate_scores"]),
            "selected_orientation_candidate_index": int(
                frame["selected_orientation_candidate_index"]
            ),
            "missing_to_surgical_site_consistency": orientation_consistency,
            "PCA_eigenvalues": rounded(frame["eigenvalues"]),
        },
        "mapping_parameters": {
            "crown_height_quantile": crown_height_quantile,
            "minimum_crown_normal_dot": minimum_normal_dot,
            "semantic_width_scale": rounded(frame["semantic"]["scale"]),
            "semantic_midline_offset_mm": rounded(frame["semantic"]["offset_mm"]),
            "semantic_fit_score": rounded(frame["semantic"]["score"]),
            "present_support_mean": rounded(frame["semantic"]["present_support_mean"]),
            "missing_support_mean": rounded(frame["semantic"]["missing_support_mean"]),
        },
        "tooth_slots": slots,
        "observation_windows": windows,
        "diagnostics": {
            "slot_guide_mapping_failures": slot_mapping_failures,
            "outside_guide_coverage_FDI": slot_mapping_failures,
            "failed_contour_following_window_section_count": failed_window_sections,
            "requested_contour_following_window_section_count": requested_window_sections,
            "contour_following_window_mapping_success_fraction": window_success_fraction,
            "axis_sweep_diagnostic_failed_contour_section_count": (
                axis_diagnostic_failed_sections
            ),
            "axis_sweep_diagnostic_requested_contour_section_count": (
                axis_diagnostic_requested_sections
            ),
        },
        "QA": qa,
        "outputs": {
            "report_json": str(report_path),
            "preview_png": str(preview_path),
            "context_glb": str(context_path),
        },
    }
    render_preview(preview_path, frame, slots, windows)
    export_context(context_path, dental, guide, slots, windows)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report

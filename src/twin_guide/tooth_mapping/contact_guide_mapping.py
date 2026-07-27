"""内部算法说明。\n\nMap approved contact-chord tooth centres into the 3-D arch frame.

The contact-chord stage measures tooth centres and contours in the anatomical
LR/AP plane.  This module projects those measurements onto the directed arch,
lifts each 2-D centre to the measured crown top, and exposes contour-derived
arch intervals for observation-window mapping.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.interpolate import PchipInterpolator
from scipy.optimize import minimize_scalar
from scipy.spatial import cKDTree


EPS = 1e-9


@dataclass(frozen=True)
class ContactToothLocation:
    """内部算法说明。"""
    fdi: int
    centroid_lr_ap_mm: tuple[float, float]
    arch_s_mm: float
    arch_lr_ap_mm: tuple[float, float]
    contour_interval_s_mm: tuple[float, float]
    crown_height_mm: float
    crown_point_global_mm: tuple[float, float, float]
    lift_method: str
    lift_distance_mm: float


@dataclass(frozen=True)
class MeasuredArchCurve:
    """内部算法说明。\n\nParametric LR/AP arch fitted through measured tooth contours."""

    lr: np.ndarray
    ap: np.ndarray
    s: np.ndarray
    apex_index: int
    s_to_lr: Any
    s_to_ap: Any

    def at_s(self, values: np.ndarray | float) -> np.ndarray:
        """内部算法说明。"""
        values_array = np.asarray(values, dtype=float)
        return np.column_stack([
            self.s_to_lr(values_array), self.s_to_ap(values_array)
        ])

    def tangent_at_s(self, value: float) -> np.ndarray:
        """内部算法说明。"""
        delta = max(0.12, 0.003 * (self.s[-1] - self.s[0]))
        lo = max(float(self.s[0]), value - delta)
        hi = min(float(self.s[-1]), value + delta)
        vector = self.at_s(np.asarray([hi]))[0] - self.at_s(
            np.asarray([lo])
        )[0]
        length = float(np.linalg.norm(vector))
        if length <= EPS:
            raise RuntimeError("measured arch has a degenerate local tangent")
        return vector / length


def fit_measured_contour_arch(
    contour_records: list[dict[str, object]],
    sample_count: int = 1001,
) -> MeasuredArchCurve:
    """内部算法说明。\n\nFit a true parametric arch through present-tooth area centroids.

    The legacy AP=f(LR) centreline can terminate early on nearly vertical
    posterior arms.  A cumulative-distance parameter has no such restriction.
    Terminal controls extend through the distal contour support so small last
    molars retain a measurable interval and local guide axis.
    """

    if len(contour_records) < 3:
        raise RuntimeError("at least three measured tooth contours are required")
    centroids = np.asarray([
        record["area_centroid_LR_AP_mm"] for record in contour_records
    ], dtype=float)
    if centroids.shape != (len(contour_records), 2):
        raise RuntimeError("measured tooth centroids must be LR/AP pairs")
    adjacent = np.linalg.norm(np.diff(centroids, axis=0), axis=1)
    if np.any(adjacent <= 0.25):
        raise RuntimeError("measured tooth centroids are not spatially distinct")

    start_direction = (centroids[0] - centroids[1]) / adjacent[0]
    end_direction = (centroids[-1] - centroids[-2]) / adjacent[-1]
    first_contour = np.asarray(
        contour_records[0]["contour_LR_AP_mm"], dtype=float
    )
    last_contour = np.asarray(
        contour_records[-1]["contour_LR_AP_mm"], dtype=float
    )
    start_support = float(np.max(
        (first_contour - centroids[0]) @ start_direction
    ))
    end_support = float(np.max(
        (last_contour - centroids[-1]) @ end_direction
    ))
    start_extension = max(start_support + 0.35, 0.35 * adjacent[0])
    end_extension = max(end_support + 0.35, 0.35 * adjacent[-1])
    controls = np.vstack([
        centroids[0] + start_extension * start_direction,
        centroids,
        centroids[-1] + end_extension * end_direction,
    ])
    control_parameter = np.r_[
        0.0, np.cumsum(np.linalg.norm(np.diff(controls, axis=0), axis=1))
    ]
    if np.any(np.diff(control_parameter) <= EPS):
        raise RuntimeError("measured arch controls contain duplicate points")
    lr_interpolator = PchipInterpolator(
        control_parameter, controls[:, 0], extrapolate=False
    )
    ap_interpolator = PchipInterpolator(
        control_parameter, controls[:, 1], extrapolate=False
    )
    parameter = np.linspace(
        control_parameter[0], control_parameter[-1], sample_count
    )
    lr = np.asarray(lr_interpolator(parameter), dtype=float)
    ap = np.asarray(ap_interpolator(parameter), dtype=float)
    segment = np.linalg.norm(
        np.diff(np.column_stack([lr, ap]), axis=0), axis=1
    )
    cumulative = np.r_[0.0, np.cumsum(segment)]
    apex_index = int(np.argmin(ap))
    s = cumulative - cumulative[apex_index]
    return MeasuredArchCurve(
        lr=lr,
        ap=ap,
        s=s,
        apex_index=apex_index,
        s_to_lr=PchipInterpolator(s, lr, extrapolate=True),
        s_to_ap=PchipInterpolator(s, ap, extrapolate=True),
    )


def project_point_to_arch(curve: Any, point_lr_ap: np.ndarray) -> tuple[float, np.ndarray, float]:
    """内部算法说明。\n\nReturn the nearest directed-arch coordinate to one LR/AP point."""

    point = np.asarray(point_lr_ap, dtype=float)
    if point.shape != (2,):
        raise RuntimeError(f"LR/AP point must have shape (2,), got {point.shape}")
    samples = np.column_stack([curve.lr, curve.ap])
    nearest = int(np.argmin(np.linalg.norm(samples - point, axis=1)))
    lower_index = max(0, nearest - 2)
    upper_index = min(len(curve.s) - 1, nearest + 2)
    lower = float(curve.s[lower_index])
    upper = float(curve.s[upper_index])

    def objective(value: float) -> float:
        """内部算法说明。"""
        candidate = np.asarray(curve.at_s(np.asarray([value]))[0], dtype=float)
        return float(np.sum((candidate - point) ** 2))

    if upper - lower <= EPS:
        s_mm = float(curve.s[nearest])
    else:
        result = minimize_scalar(objective, bounds=(lower, upper), method="bounded")
        s_mm = float(result.x) if result.success else float(curve.s[nearest])
    projected = np.asarray(curve.at_s(np.asarray([s_mm]))[0], dtype=float)
    distance = float(np.linalg.norm(projected - point))
    return s_mm, projected, distance


def contour_arch_interval(curve: Any, contour_lr_ap: np.ndarray) -> tuple[float, float]:
    """内部算法说明。\n\nProject a measured contour to a conservative directed-arch interval."""

    contour = np.asarray(contour_lr_ap, dtype=float)
    if contour.ndim != 2 or contour.shape[1] != 2 or len(contour) < 3:
        raise RuntimeError("contact contour must contain at least three LR/AP points")
    tree = cKDTree(np.column_stack([curve.lr, curve.ap]))
    _, indices = tree.query(contour, k=1)
    values = np.asarray(curve.s, dtype=float)[np.asarray(indices, dtype=int)]
    return float(np.min(values)), float(np.max(values))


def sample_crown_height(
    maps: dict[str, np.ndarray],
    point_lr_ap: np.ndarray,
    maximum_fallback_distance_mm: float = 1.5,
) -> tuple[float, str, float]:
    """内部算法说明。\n\nSample the continuous crown-top map at a measured 2-D centroid.

    Bilinear interpolation is used when all four surrounding pixels are valid.
    A nearest valid crown pixel is allowed only inside a small, reported
    fallback radius.  Larger gaps are a hard mapping error.
    """

    lr = np.asarray(maps["lr_centres"], dtype=float)
    ap = np.asarray(maps["ap_centres"], dtype=float)
    height = np.asarray(maps["top_height_mm"], dtype=float)
    point = np.asarray(point_lr_ap, dtype=float)
    if height.shape != (len(lr), len(ap)):
        raise RuntimeError("enhanced crown-height map dimensions are inconsistent")
    if not (lr[0] <= point[0] <= lr[-1] and ap[0] <= point[1] <= ap[-1]):
        raise RuntimeError("tooth centroid lies outside the enhanced projection grid")

    row_hi = int(np.clip(np.searchsorted(lr, point[0]), 1, len(lr) - 1))
    col_hi = int(np.clip(np.searchsorted(ap, point[1]), 1, len(ap) - 1))
    row_lo = row_hi - 1
    col_lo = col_hi - 1
    values = np.asarray([
        height[row_lo, col_lo], height[row_hi, col_lo],
        height[row_lo, col_hi], height[row_hi, col_hi],
    ])
    if np.all(np.isfinite(values)):
        x_fraction = (point[0] - lr[row_lo]) / max(lr[row_hi] - lr[row_lo], EPS)
        y_fraction = (point[1] - ap[col_lo]) / max(ap[col_hi] - ap[col_lo], EPS)
        lower = (1.0 - x_fraction) * values[0] + x_fraction * values[1]
        upper = (1.0 - x_fraction) * values[2] + x_fraction * values[3]
        return float((1.0 - y_fraction) * lower + y_fraction * upper), "bilinear_top_height", 0.0

    valid = np.argwhere(np.isfinite(height))
    if not len(valid):
        raise RuntimeError("enhanced crown-height map contains no finite samples")
    physical = np.column_stack([lr[valid[:, 0]], ap[valid[:, 1]]])
    distance, nearest = cKDTree(physical).query(point, k=1)
    if float(distance) > maximum_fallback_distance_mm:
        raise RuntimeError(
            "tooth centroid has no nearby crown-height sample "
            f"({float(distance):.3f} mm > {maximum_fallback_distance_mm:.3f} mm)"
        )
    row, column = valid[int(nearest)]
    return float(height[row, column]), "nearest_valid_top_height", float(distance)


def locate_contact_teeth(
    *,
    contour_records: list[dict[str, object]],
    frame: dict[str, object],
    enhanced_maps: dict[str, np.ndarray],
) -> list[ContactToothLocation]:
    """内部算法说明。\n\nConvert approved 2-D contact contours to 3-D crown locations."""

    curve = frame["curve"]
    origin = np.asarray(frame["origin"], dtype=float)
    e_lr = np.asarray(frame["e_lr"], dtype=float)
    e_ap = np.asarray(frame["e_ap"], dtype=float)
    e_occ = np.asarray(frame["e_occ"], dtype=float)
    output: list[ContactToothLocation] = []
    for record in contour_records:
        fdi = int(record["FDI"])
        centroid = np.asarray(record["area_centroid_LR_AP_mm"], dtype=float)
        contour = np.asarray(record["contour_LR_AP_mm"], dtype=float)
        s_mm, arch_point, _ = project_point_to_arch(curve, centroid)
        interval = contour_arch_interval(curve, contour)
        height, lift_method, lift_distance = sample_crown_height(
            enhanced_maps, centroid
        )
        global_point = (
            origin + centroid[0] * e_lr + centroid[1] * e_ap + height * e_occ
        )
        output.append(ContactToothLocation(
            fdi=fdi,
            centroid_lr_ap_mm=(float(centroid[0]), float(centroid[1])),
            arch_s_mm=s_mm,
            arch_lr_ap_mm=(float(arch_point[0]), float(arch_point[1])),
            contour_interval_s_mm=interval,
            crown_height_mm=height,
            crown_point_global_mm=tuple(float(value) for value in global_point),
            lift_method=lift_method,
            lift_distance_mm=lift_distance,
        ))
    return output

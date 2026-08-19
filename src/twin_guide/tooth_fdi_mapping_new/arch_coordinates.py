"""算法说明。 Anatomical orientation candidates and directed arch coordinates."""

from __future__ import annotations

import itertools

import numpy as np
from scipy.interpolate import PchipInterpolator, UnivariateSpline
from scipy.ndimage import gaussian_filter1d

from twin_guide.tooth_mapping.pipeline import parse_axis, unit

from .models import ArchFrame


def _fit_arch(lr: np.ndarray, ap: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """算法说明。"""
    low, high = np.quantile(lr, [0.008, 0.992])
    edges = np.linspace(low, high, 91)
    used_lr: list[float] = []
    median_ap: list[float] = []
    for left, right in itertools.pairwise(edges):
        selected = (lr >= left) & (lr < right)
        if np.count_nonzero(selected) < 25:
            continue
        used_lr.append(float(0.5 * (left + right)))
        median_ap.append(float(np.median(ap[selected])))
    if len(used_lr) < 12:
        raise RuntimeError("insufficient crown-support bins to fit the dental arch")
    used = np.asarray(used_lr)
    median = np.asarray(median_ap)
    smooth = max(2.0, 0.18 * len(used) * float(np.var(median)))
    spline = UnivariateSpline(used, median, k=3, s=smooth)
    curve_lr = np.linspace(used[0], used[-1], 601)
    curve_ap = gaussian_filter1d(np.asarray(spline(curve_lr)), sigma=2.0)
    segment = np.linalg.norm(np.diff(np.column_stack([curve_lr, curve_ap]), axis=0), axis=1)
    cumulative = np.r_[0.0, np.cumsum(segment)]
    apex = int(np.argmin(curve_ap))
    curve_s = cumulative - cumulative[apex]
    # Re-evaluate through monotone PCHIP to guard tiny spline reversals.
    curve_lr = np.asarray(PchipInterpolator(curve_s, curve_lr)(curve_s))
    curve_ap = np.asarray(PchipInterpolator(curve_s, curve_ap)(curve_s))
    return curve_lr, curve_ap, curve_s


def _explicit_axes(anatomy: dict[str, object]) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """算法说明。"""
    orientation = anatomy.get("orientation")
    if not isinstance(orientation, dict):
        return None
    values = [
        orientation.get("patient_right_to_left_axis"),
        orientation.get("anterior_to_posterior_axis"),
        orientation.get("occlusal_axis"),
    ]
    if any(value is None for value in values):
        return None
    return tuple(parse_axis(value) for value in values)  # type: ignore[return-value]


def _pca_axis_permutation_candidates(
    vertices: np.ndarray,
    origin: np.ndarray,
    guide_centroid: np.ndarray,
) -> list[dict[str, object]]:
    """内部算法说明。 Return both anatomically possible PCA minor-axis assignments.

    A dental mesh with a tall trimmed base can have similar variance along its
    AP and occlusal directions.  Treating the smallest eigenvector as
    occlusal unconditionally then turns an occlusal projection into a side
    view.  Both minor axes are therefore retained as global hypotheses.  The
    guide offset fixes only the sign; it does not delete either hypothesis.
    """

    eigenvalues, eigenvectors = np.linalg.eigh(
        np.cov((np.asarray(vertices, dtype=float) - origin).T)
    )
    guide_delta = np.asarray(guide_centroid, dtype=float) - origin
    guide_norm = float(np.linalg.norm(guide_delta))
    guide_direction = guide_delta / guide_norm if guide_norm > 1.0e-9 else None
    candidates: list[dict[str, object]] = []
    for occlusal_index in (0, 1):
        ap_index = 1 - occlusal_index
        e_occ = unit(eigenvectors[:, occlusal_index])
        signed_alignment = float(e_occ @ guide_direction) if guide_direction is not None else 0.0
        if signed_alignment < 0.0:
            e_occ = -e_occ
            signed_alignment = -signed_alignment
        candidates.append(
            {
                "occlusal_axis_index": occlusal_index,
                "e_lr_base": unit(eigenvectors[:, 2]),
                "e_ap": unit(eigenvectors[:, ap_index]),
                "e_occ": e_occ,
                "guide_occlusal_alignment": signed_alignment,
                "eigenvalues": tuple(float(value) for value in eigenvalues),
            }
        )
    return candidates


def build_arch_frame_candidates(
    dental,
    guide,
    anatomy: dict[str, object],
    *,
    crown_quantile: float = 0.35,
    minimum_normal_dot: float = 0.05,
) -> list[ArchFrame]:
    """内部算法说明。 Return confirmed axes or every PCA minor-axis/LR hypothesis."""

    vertices = np.asarray(dental.vertices, dtype=float)
    normals = np.asarray(dental.vertex_normals, dtype=float)
    origin = np.mean(vertices, axis=0)
    explicit = _explicit_axes(anatomy)
    if explicit is not None:
        e_lr_base, e_ap, e_occ = explicit
        axis_candidates = [
            {
                "occlusal_axis_index": None,
                "e_lr_base": e_lr_base,
                "e_ap": e_ap,
                "e_occ": e_occ,
                "guide_occlusal_alignment": None,
                "eigenvalues": None,
            }
        ]
    else:
        axis_candidates = _pca_axis_permutation_candidates(
            vertices,
            origin,
            np.asarray(guide.centroid, dtype=float),
        )

    frames: list[ArchFrame] = []
    for axis_candidate in axis_candidates:
        e_lr_base = np.asarray(axis_candidate["e_lr_base"], dtype=float)
        e_ap = np.asarray(axis_candidate["e_ap"], dtype=float)
        e_occ = np.asarray(axis_candidate["e_occ"], dtype=float)
        height = (vertices - origin) @ e_occ
        support = (height >= np.quantile(height, crown_quantile)) & (
            normals @ e_occ >= minimum_normal_dot
        )
        if np.count_nonzero(support) < 1_000:
            support = height >= np.quantile(height, crown_quantile)
        provisional_lr = (vertices[support] - origin) @ e_lr_base
        provisional_ap = (vertices[support] - origin) @ e_ap
        correlation = float(np.corrcoef(np.abs(provisional_lr), provisional_ap)[0, 1])
        if not np.isfinite(correlation) or correlation < 0.0:
            e_ap = -e_ap
        signs = (1.0,) if explicit is not None else (-1.0, 1.0)
        axis_index = axis_candidate["occlusal_axis_index"]
        names = (
            ("confirmed",)
            if explicit is not None
            else tuple(
                f"pca_occ_minor_{axis_index}_lr_{label}" for label in ("negative", "positive")
            )
        )
        delta = vertices[support] - origin
        for sign, name in zip(signs, names, strict=True):
            e_lr = sign * e_lr_base
            lr = delta @ e_lr
            ap = delta @ e_ap
            try:
                curve_lr, curve_ap, curve_s = _fit_arch(lr, ap)
            except RuntimeError:
                # One side-view hypothesis can lack enough crown-support bins;
                # retain every geometrically valid alternative.
                continue
            frames.append(
                ArchFrame(
                    origin=origin,
                    e_lr=e_lr,
                    e_ap=e_ap,
                    e_occ=e_occ,
                    curve_lr=curve_lr,
                    curve_ap=curve_ap,
                    curve_s=curve_s,
                    local_scale_mm=np.full_like(curve_s, 8.0, dtype=float),
                    orientation_name=name,
                    pca_occlusal_axis_index=(int(axis_index) if axis_index is not None else None),
                    guide_occlusal_alignment=(
                        float(axis_candidate["guide_occlusal_alignment"])
                        if axis_candidate["guide_occlusal_alignment"] is not None
                        else None
                    ),
                    pca_eigenvalues=axis_candidate["eigenvalues"],
                )
            )
    if not frames:
        raise RuntimeError("no PCA/confirmed coordinate hypothesis can fit an arch")
    return frames


def transform_mesh(dental, frame: ArchFrame) -> tuple[np.ndarray, np.ndarray]:
    """算法说明。"""
    vertices = np.asarray(dental.vertices, dtype=float)
    normals = np.asarray(dental.vertex_normals, dtype=float)
    delta = vertices - frame.origin
    transformed = np.column_stack(
        [
            delta @ frame.e_lr,
            delta @ frame.e_ap,
            delta @ frame.e_occ,
        ]
    )
    transformed_normals = np.column_stack(
        [
            normals @ frame.e_lr,
            normals @ frame.e_ap,
            normals @ frame.e_occ,
        ]
    )
    return transformed, transformed_normals

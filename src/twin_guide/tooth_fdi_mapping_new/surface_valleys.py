"""算法说明。 Scale-aware minimum-curvature evidence on a triangular dental surface."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


EPS = 1.0e-12


@dataclass(frozen=True)
class SurfaceValleyEvidence:
    """算法说明。 Per-vertex curvature fields used as independent tooth-boundary evidence."""

    minimum_curvature_per_mm: np.ndarray
    valley_strength: np.ndarray
    valley_score: np.ndarray
    valid_vertex_fraction: float
    normalization_scale_mm: float
    smoothing_iterations: tuple[int, ...]


def _unique_edges(faces: np.ndarray) -> np.ndarray:
    """算法说明。"""
    triangles = np.asarray(faces, dtype=np.int64)
    edges = np.vstack([
        triangles[:, (0, 1)],
        triangles[:, (1, 2)],
        triangles[:, (2, 0)],
    ])
    edges = np.sort(edges, axis=1)
    return np.unique(edges, axis=0)


def _tangent_basis(normals: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """算法说明。"""
    normal = np.asarray(normals, dtype=float)
    references = np.zeros_like(normal)
    use_x = np.abs(normal[:, 0]) < 0.80
    references[use_x, 0] = 1.0
    references[~use_x, 1] = 1.0
    first = np.cross(normal, references)
    first /= np.maximum(np.linalg.norm(first, axis=1, keepdims=True), EPS)
    second = np.cross(normal, first)
    second /= np.maximum(np.linalg.norm(second, axis=1, keepdims=True), EPS)
    return first, second


def estimate_minimum_curvature(
    vertices: np.ndarray,
    faces: np.ndarray,
    vertex_normals: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """算法说明。

Estimate signed minimum principal curvature from normal variation.

    A local symmetric shape operator is fitted at every vertex from all
    incident edge tangents and normal differences.  With consistently outward
    normals, convex crown domes are positive and concave dental valleys are
    negative, matching the convention used by Yuan et al.
    """

    points = np.asarray(vertices, dtype=float)
    normals = np.array(vertex_normals, dtype=float, copy=True)
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), EPS)
    edges = _unique_edges(faces)
    source = np.concatenate([edges[:, 0], edges[:, 1]])
    target = np.concatenate([edges[:, 1], edges[:, 0]])
    delta = points[target] - points[source]
    normal_delta = normals[target] - normals[source]
    edge_length = np.linalg.norm(delta, axis=1)
    first, second = _tangent_basis(normals)
    source_first = first[source]
    source_second = second[source]
    x1 = np.einsum("ij,ij->i", delta, source_first)
    x2 = np.einsum("ij,ij->i", delta, source_second)
    y1 = np.einsum("ij,ij->i", normal_delta, source_first)
    y2 = np.einsum("ij,ij->i", normal_delta, source_second)
    weight = 1.0 / np.maximum(edge_length, 1.0e-6)
    count = len(points)

    def accumulate(values: np.ndarray) -> np.ndarray:
        """算法说明。"""
        return np.bincount(source, weights=values, minlength=count)

    a11 = accumulate(weight * x1 * x1)
    a12 = accumulate(weight * x1 * x2)
    a22 = accumulate(weight * x2 * x2)
    b11 = accumulate(weight * y1 * x1)
    b12 = accumulate(weight * y1 * x2)
    b21 = accumulate(weight * y2 * x1)
    b22 = accumulate(weight * y2 * x2)
    determinant = a11 * a22 - a12 * a12
    trace = a11 + a22
    valid = determinant > np.maximum(1.0e-10 * trace * trace, EPS)
    safe_determinant = np.where(valid, determinant, 1.0)
    s11 = (b11 * a22 - b12 * a12) / safe_determinant
    s12 = (-b11 * a12 + b12 * a11) / safe_determinant
    s21 = (b21 * a22 - b22 * a12) / safe_determinant
    s22 = (-b21 * a12 + b22 * a11) / safe_determinant
    off_diagonal = 0.5 * (s12 + s21)
    half_trace = 0.5 * (s11 + s22)
    radius = np.sqrt(np.maximum(
        0.25 * (s11 - s22) ** 2 + off_diagonal**2,
        0.0,
    ))
    minimum = half_trace - radius
    minimum[~valid] = 0.0
    minimum[~np.isfinite(minimum)] = 0.0
    return minimum, valid


def _smooth_on_mesh(
    values: np.ndarray,
    edges: np.ndarray,
    iterations: int,
) -> np.ndarray:
    """算法说明。"""
    output = np.asarray(values, dtype=float).copy()
    if iterations <= 0:
        return output
    first = edges[:, 0]
    second = edges[:, 1]
    count = len(output)
    degree = np.bincount(
        np.concatenate([first, second]), minlength=count
    ).astype(float)
    for _ in range(iterations):
        neighbor_sum = (
            np.bincount(first, weights=output[second], minlength=count)
            + np.bincount(second, weights=output[first], minlength=count)
        )
        output = (2.0 * output + neighbor_sum) / np.maximum(2.0 + degree, 1.0)
    return output


def _robust_valley_score(strength: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """算法说明。"""
    score = np.zeros_like(strength, dtype=float)
    positive = strength[valid & np.isfinite(strength) & (strength > 0.0)]
    if len(positive) < 32:
        return score
    low, high = np.quantile(positive, [0.45, 0.985])
    if high - low <= EPS:
        return score
    score[valid] = np.clip((strength[valid] - low) / (high - low), 0.0, 1.0)
    return score


def build_surface_valley_evidence(
    vertices: np.ndarray,
    faces: np.ndarray,
    vertex_normals: np.ndarray,
    *,
    normalization_scale_mm: float,
    smoothing_iterations: tuple[int, ...],
) -> SurfaceValleyEvidence:
    """算法说明。 Return robust multi-scale concave-valley evidence for every vertex."""

    if normalization_scale_mm <= 0.0:
        raise ValueError("normalization_scale_mm must be positive")
    scales = tuple(sorted(set(int(value) for value in smoothing_iterations)))
    if not scales or scales[0] < 0:
        raise ValueError("smoothing_iterations must contain non-negative integers")
    minimum, valid = estimate_minimum_curvature(
        vertices, faces, vertex_normals
    )
    edges = _unique_edges(faces)
    smoothed = np.stack([
        _smooth_on_mesh(minimum, edges, iterations) for iterations in scales
    ])
    stable_minimum = np.median(smoothed, axis=0)
    strength = np.maximum(-stable_minimum * normalization_scale_mm, 0.0)
    score = _robust_valley_score(strength, valid)
    score = _smooth_on_mesh(score, edges, 1)
    score = np.clip(score, 0.0, 1.0)
    return SurfaceValleyEvidence(
        minimum_curvature_per_mm=stable_minimum,
        valley_strength=strength,
        valley_score=score,
        valid_vertex_fraction=float(np.mean(valid)) if len(valid) else 0.0,
        normalization_scale_mm=float(normalization_scale_mm),
        smoothing_iterations=scales,
    )

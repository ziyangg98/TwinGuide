"""内部算法说明。\n\nFDI-free 2-D tooth instance proposals with smooth ellipse contours.

This module works in the original anatomical LR/AP projection plane.  It does
not rasterize in arch-distance coordinates, does not use watershed, and does
not insert midpoint/Voronoi guards.  A fixed number of unlabeled instances is
fit first; FDI identity is intentionally assigned by the caller afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import gaussian_filter, map_coordinates
from scipy.spatial import cKDTree
from sklearn.cluster import KMeans
from sklearn.mixture import GaussianMixture

EPS = 1e-9


@dataclass(frozen=True)
class EllipseInstance:
    """内部算法说明。"""
    instance_id: int
    center_lr_ap_mm: tuple[float, float]
    semi_axes_mm: tuple[float, float]
    angle_degrees: float
    covariance: tuple[tuple[float, float], tuple[float, float]]
    support_pixel_count: int
    boundary_mean_distance_mm: float
    boundary_p95_distance_mm: float
    owned_support_coverage: float
    boundary_edge_support: float
    boundary_inside_outside_contrast: float
    contour_scale_from_initial_fit: float
    contour_lr_ap_mm: list[list[float]]


def _normalise(values: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """内部算法说明。"""
    result = np.zeros_like(values, dtype=float)
    selected = np.asarray(values, dtype=float)[valid]
    if len(selected) == 0:
        return result
    low, high = np.quantile(selected, [0.05, 0.95])
    if high - low <= EPS:
        return result
    result[valid] = np.clip((values[valid] - low) / (high - low), 0.0, 1.0)
    return result


def build_lr_ap_feature_maps(
    *,
    lr: np.ndarray,
    ap: np.ndarray,
    height: np.ndarray,
    normal_dot: np.ndarray,
    crown_support: np.ndarray,
    resolution_mm: float = 0.18,
) -> dict[str, object]:
    """内部算法说明。\n\nRasterise crown geometry without merging it into a filled union mask."""

    lr = np.asarray(lr, dtype=float)
    ap = np.asarray(ap, dtype=float)
    height = np.asarray(height, dtype=float)
    normal_dot = np.asarray(normal_dot, dtype=float)
    selected = np.asarray(crown_support, dtype=bool)
    if np.count_nonzero(selected) < 1_000:
        raise RuntimeError("insufficient crown points for LR/AP projection")

    lr_low, lr_high = np.quantile(lr[selected], [0.002, 0.998])
    ap_low, ap_high = np.quantile(ap[selected], [0.002, 0.998])
    padding = 1.0
    lr_edges = np.arange(lr_low - padding, lr_high + padding + resolution_mm, resolution_mm)
    ap_edges = np.arange(ap_low - padding, ap_high + padding + resolution_mm, resolution_mm)
    shape = (len(lr_edges) - 1, len(ap_edges) - 1)
    lr_index = np.clip(np.digitize(lr[selected], lr_edges) - 1, 0, shape[0] - 1)
    ap_index = np.clip(np.digitize(ap[selected], ap_edges) - 1, 0, shape[1] - 1)

    counts = np.zeros(shape, dtype=float)
    height_sum = np.zeros(shape, dtype=float)
    normal_sum = np.zeros(shape, dtype=float)
    height_max = np.full(shape, -np.inf, dtype=float)
    np.add.at(counts, (lr_index, ap_index), 1.0)
    np.add.at(height_sum, (lr_index, ap_index), height[selected])
    np.add.at(normal_sum, (lr_index, ap_index), normal_dot[selected])
    np.maximum.at(height_max, (lr_index, ap_index), height[selected])
    occupied = counts > 0.0
    height_mean = np.zeros(shape, dtype=float)
    normal_mean = np.zeros(shape, dtype=float)
    height_mean[occupied] = height_sum[occupied] / counts[occupied]
    normal_mean[occupied] = normal_sum[occupied] / counts[occupied]
    height_max[~occupied] = 0.0

    sigma = max(1.0, 0.35 / resolution_mm)
    density = gaussian_filter(counts, sigma=sigma)
    density_score = _normalise(density, density > 0.0)
    height_score = _normalise(height_mean, occupied)
    normal_score = _normalise(normal_mean, occupied)
    feature_score = gaussian_filter(
        0.55 * density_score + 0.30 * height_score + 0.15 * normal_score,
        sigma=max(0.8, 0.18 / resolution_mm),
    )
    lr_centres = 0.5 * (lr_edges[:-1] + lr_edges[1:])
    ap_centres = 0.5 * (ap_edges[:-1] + ap_edges[1:])
    return {
        "resolution_mm": float(resolution_mm),
        "lr_edges": lr_edges,
        "ap_edges": ap_edges,
        "lr_centres": lr_centres,
        "ap_centres": ap_centres,
        "counts": counts,
        "occupied": occupied,
        "density_score": density_score,
        "height_score": height_score,
        "normal_score": normal_score,
        "feature_score": feature_score,
    }


def _ellipse_polygon(
    center: np.ndarray,
    eigenvectors: np.ndarray,
    semi_axes: np.ndarray,
    sample_count: int = 181,
) -> np.ndarray:
    """内部算法说明。"""
    angle = np.linspace(0.0, 2.0 * np.pi, sample_count)
    local = np.column_stack([semi_axes[0] * np.cos(angle), semi_axes[1] * np.sin(angle)])
    return center + local @ eigenvectors.T


def _fit_trimmed_ellipse(points: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """内部算法说明。"""
    if len(points) < 20:
        raise RuntimeError("too few pixels for ellipse fit")
    retained = np.asarray(points, dtype=float)
    for _ in range(4):
        center = np.mean(retained, axis=0)
        covariance = np.cov((retained - center).T) + 0.08 * np.eye(2)
        inverse = np.linalg.inv(covariance)
        delta = points - center
        distance = np.einsum("ni,ij,nj->n", delta, inverse, delta)
        threshold = float(np.quantile(distance, 0.90))
        updated = points[distance <= threshold]
        if len(updated) < 20 or len(updated) == len(retained):
            break
        retained = updated
    center = np.mean(retained, axis=0)
    covariance = np.cov((retained - center).T) + 0.08 * np.eye(2)
    inverse = np.linalg.inv(covariance)
    delta = points - center
    distance = np.einsum("ni,ij,nj->n", delta, inverse, delta)
    radius_squared = float(np.quantile(distance, 0.96))
    values, vectors = np.linalg.eigh(covariance)
    order = np.argsort(values)[::-1]
    values = np.maximum(values[order], 0.04)
    vectors = vectors[:, order]
    semi_axes = np.sqrt(values * radius_squared)
    # Keep a general tooth-like ellipse without using FDI-specific dimensions.
    semi_axes = np.clip(semi_axes, 1.7, 7.5)
    if semi_axes[0] / max(semi_axes[1], EPS) > 2.0:
        semi_axes[1] = semi_axes[0] / 2.0
    return center, vectors, semi_axes


def _sample_map(
    values: np.ndarray,
    points: np.ndarray,
    lr_centres: np.ndarray,
    ap_centres: np.ndarray,
    resolution_mm: float,
) -> np.ndarray:
    """内部算法说明。"""
    indices = np.vstack([
        (points[:, 0] - lr_centres[0]) / resolution_mm,
        (points[:, 1] - ap_centres[0]) / resolution_mm,
    ])
    return map_coordinates(values, indices, order=1, mode="nearest")


def _refine_scale_against_projection(
    *,
    center: np.ndarray,
    vectors: np.ndarray,
    semi_axes: np.ndarray,
    owned_points: np.ndarray,
    occupied_tree: cKDTree,
    feature_score: np.ndarray,
    edge_score: np.ndarray,
    lr_centres: np.ndarray,
    ap_centres: np.ndarray,
    resolution_mm: float,
) -> tuple[np.ndarray, np.ndarray, dict[str, float]]:
    """内部算法说明。\n\nChoose a smooth contour scale from local image evidence.

    The Gaussian mixture supplies only an unlabeled centre, orientation and
    initial aspect ratio.  No component/Voronoi boundary is converted to a
    contour.  Instead, several concentric smooth ellipses are evaluated using
    projection-edge strength, inside/outside feature contrast, and how much of
    the component's high-confidence physical support they enclose.
    """

    local_owned = (owned_points - center) @ vectors
    best: tuple[float, np.ndarray, np.ndarray, dict[str, float]] | None = None
    for scale in np.linspace(0.70, 1.02, 33):
        candidate_axes = semi_axes * scale
        contour = _ellipse_polygon(center, vectors, candidate_axes)
        inner = _ellipse_polygon(center, vectors, candidate_axes * 0.86)
        outer = _ellipse_polygon(center, vectors, candidate_axes * 1.14)
        normalized_radius = np.sum((local_owned / candidate_axes) ** 2, axis=1)
        coverage = float(np.mean(normalized_radius <= 1.0))
        edge = float(np.mean(_sample_map(
            edge_score, contour, lr_centres, ap_centres, resolution_mm
        )))
        inside_score = float(np.mean(_sample_map(
            feature_score, inner, lr_centres, ap_centres, resolution_mm
        )))
        outside_score = float(np.mean(_sample_map(
            feature_score, outer, lr_centres, ap_centres, resolution_mm
        )))
        contrast = inside_score - outside_score
        boundary_distance, _ = occupied_tree.query(contour, k=1)
        mean_distance = float(np.mean(boundary_distance))
        p95_distance = float(np.quantile(boundary_distance, 0.95))

        # The preferred contour encloses about 88% of confidently owned
        # support.  Edge/contrast terms move it to the visible crown margin;
        # the distance term rejects unsupported excursions into empty space.
        objective = (
            1.15 * edge
            + 0.70 * contrast
            - 0.95 * abs(coverage - 0.88)
            - 0.10 * mean_distance
            - 0.04 * p95_distance
        )
        metrics = {
            "scale": float(scale),
            "coverage": coverage,
            "edge_support": edge,
            "inside_outside_contrast": float(contrast),
            "boundary_mean_distance_mm": mean_distance,
            "boundary_p95_distance_mm": p95_distance,
        }
        if best is None or objective > best[0]:
            best = (objective, candidate_axes, contour, metrics)
    assert best is not None
    return best[1], best[2], best[3]


def fit_unlabelled_ellipse_instances(
    feature_maps: dict[str, object],
    instance_count: int,
    random_state: int = 17,
) -> tuple[list[EllipseInstance], dict[str, np.ndarray]]:
    """内部算法说明。\n\nFit smooth unlabeled 2-D instances with no FDI or arch-axis guards."""

    occupied = np.asarray(feature_maps["occupied"], dtype=bool)
    counts = np.asarray(feature_maps["counts"], dtype=float)
    score = np.asarray(feature_maps["feature_score"], dtype=float)
    lr_grid, ap_grid = np.meshgrid(
        np.asarray(feature_maps["lr_centres"], dtype=float),
        np.asarray(feature_maps["ap_centres"], dtype=float),
        indexing="ij",
    )
    usable = occupied & (score >= np.quantile(score[occupied], 0.10))
    coordinates = np.column_stack([lr_grid[usable], ap_grid[usable]])
    local_counts = counts[usable]
    if len(coordinates) < 20 * instance_count:
        raise RuntimeError("insufficient occupied pixels for ellipse mixture")

    # A small capped repetition preserves density modes without letting scan
    # tessellation density dominate the geometric fit.
    count_scale = max(float(np.quantile(local_counts, 0.90)), 1.0)
    repetitions = 1 + np.floor(2.0 * np.clip(local_counts / count_scale, 0.0, 1.0)).astype(int)
    samples = np.repeat(coordinates, repetitions, axis=0)
    initial = KMeans(
        n_clusters=instance_count,
        n_init=30,
        random_state=random_state,
    ).fit(samples)
    mixture = GaussianMixture(
        n_components=instance_count,
        covariance_type="full",
        means_init=initial.cluster_centers_,
        n_init=8,
        reg_covar=0.20,
        max_iter=500,
        random_state=random_state,
    ).fit(samples)

    posterior = mixture.predict_proba(coordinates)
    ownership = np.argmax(posterior, axis=1)
    occupied_tree = cKDTree(coordinates)
    resolution_mm = float(feature_maps["resolution_mm"])
    lr_centres = np.asarray(feature_maps["lr_centres"], dtype=float)
    ap_centres = np.asarray(feature_maps["ap_centres"], dtype=float)
    feature_score = np.asarray(feature_maps["feature_score"], dtype=float)
    feature_gradient = np.gradient(feature_score, resolution_mm, resolution_mm)
    edge_score = np.hypot(feature_gradient[0], feature_gradient[1])
    edge_reference = float(np.quantile(edge_score[occupied], 0.95))
    if edge_reference > EPS:
        edge_score = np.clip(edge_score / edge_reference, 0.0, 1.5)
    instances: list[EllipseInstance] = []
    for component in range(instance_count):
        owned = coordinates[ownership == component]
        confidence = posterior[ownership == component, component]
        selected = owned[confidence >= 0.55]
        if len(selected) < 20:
            selected = owned
        center, vectors, semi_axes = _fit_trimmed_ellipse(selected)
        semi_axes, contour, refinement = _refine_scale_against_projection(
            center=center,
            vectors=vectors,
            semi_axes=semi_axes,
            owned_points=selected,
            occupied_tree=occupied_tree,
            feature_score=feature_score,
            edge_score=edge_score,
            lr_centres=lr_centres,
            ap_centres=ap_centres,
            resolution_mm=resolution_mm,
        )
        angle = float(np.degrees(np.arctan2(vectors[1, 0], vectors[0, 0])))
        covariance = vectors @ np.diag(semi_axes**2) @ vectors.T
        instances.append(EllipseInstance(
            instance_id=int(component),
            center_lr_ap_mm=(float(center[0]), float(center[1])),
            semi_axes_mm=(float(semi_axes[0]), float(semi_axes[1])),
            angle_degrees=angle,
            covariance=(
                (float(covariance[0, 0]), float(covariance[0, 1])),
                (float(covariance[1, 0]), float(covariance[1, 1])),
            ),
            support_pixel_count=len(selected),
            boundary_mean_distance_mm=refinement["boundary_mean_distance_mm"],
            boundary_p95_distance_mm=refinement["boundary_p95_distance_mm"],
            owned_support_coverage=refinement["coverage"],
            boundary_edge_support=refinement["edge_support"],
            boundary_inside_outside_contrast=refinement["inside_outside_contrast"],
            contour_scale_from_initial_fit=refinement["scale"],
            contour_lr_ap_mm=contour.tolist(),
        ))

    component_grid = np.full(occupied.shape, -1, dtype=int)
    posterior_max_grid = np.zeros(occupied.shape, dtype=float)
    component_grid[usable] = ownership
    posterior_max_grid[usable] = np.max(posterior, axis=1)
    diagnostics = {
        "component_grid": component_grid,
        "posterior_max_grid": posterior_max_grid,
    }
    return instances, diagnostics


def ellipse_overlap_fraction(
    first: EllipseInstance,
    second: EllipseInstance,
    resolution_mm: float = 0.15,
) -> float:
    """内部算法说明。\n\nApproximate intersection divided by the smaller ellipse area."""

    centres = np.asarray([first.center_lr_ap_mm, second.center_lr_ap_mm], dtype=float)
    radii = max(max(first.semi_axes_mm), max(second.semi_axes_mm)) + 0.5
    low = np.min(centres, axis=0) - radii
    high = np.max(centres, axis=0) + radii
    x = np.arange(low[0], high[0] + resolution_mm, resolution_mm)
    y = np.arange(low[1], high[1] + resolution_mm, resolution_mm)
    xx, yy = np.meshgrid(x, y, indexing="ij")
    points = np.column_stack([xx.ravel(), yy.ravel()])

    def inside(instance: EllipseInstance) -> np.ndarray:
        """内部算法说明。"""
        center = np.asarray(instance.center_lr_ap_mm)
        covariance = np.asarray(instance.covariance)
        delta = points - center
        return np.einsum("ni,ij,nj->n", delta, np.linalg.inv(covariance), delta) <= 1.0

    first_inside = inside(first)
    second_inside = inside(second)
    intersection = int(np.count_nonzero(first_inside & second_inside))
    smaller = min(int(np.count_nonzero(first_inside)), int(np.count_nonzero(second_inside)))
    return float(intersection / max(smaller, 1))

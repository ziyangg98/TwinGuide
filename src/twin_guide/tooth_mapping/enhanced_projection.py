"""内部算法说明。\n\nContinuous multi-channel crown projection from mesh triangles."""

from __future__ import annotations

import numpy as np
from scipy.ndimage import (
    binary_dilation,
    binary_erosion,
    distance_transform_edt,
    gaussian_filter,
    laplace,
)
from skimage.draw import polygon
from skimage.filters import scharr
from skimage.morphology import closing, disk, remove_small_holes, remove_small_objects

EPS = 1e-9


def _robust_normalise(values: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """内部算法说明。"""
    result = np.zeros_like(values, dtype=float)
    selected = np.asarray(values, dtype=float)[valid & np.isfinite(values)]
    if len(selected) == 0:
        return result
    low, high = np.quantile(selected, [0.02, 0.98])
    if high - low <= EPS:
        return result
    result[valid] = np.clip((values[valid] - low) / (high - low), 0.0, 1.0)
    return result


def rasterise_crown_triangles(
    *,
    vertices_lr_ap_height: np.ndarray,
    faces: np.ndarray,
    vertex_normals_lr_ap_occ: np.ndarray,
    height_floor_mm: float,
    resolution_mm: float = 0.12,
    padding_mm: float = 1.0,
    vertex_scalar_fields: dict[str, np.ndarray] | None = None,
) -> dict[str, object]:
    """内部算法说明。\n\nOrthographically rasterise the highest triangle surface in LR/AP.

    Unlike a vertex scatter plot, every pixel covered by a projected triangle
    receives interpolated height and normal values.  A highest-surface Z buffer
    resolves overlapping triangles.
    """

    vertices = np.asarray(vertices_lr_ap_height, dtype=float)
    faces = np.asarray(faces, dtype=np.int64)
    normals = np.asarray(vertex_normals_lr_ap_occ, dtype=float)
    face_height = vertices[faces, 2]
    selected_faces = faces[np.max(face_height, axis=1) >= height_floor_mm]
    selected_vertices = np.unique(selected_faces.ravel())
    selected_xy = vertices[selected_vertices, :2]
    low = np.min(selected_xy, axis=0) - padding_mm
    high = np.max(selected_xy, axis=0) + padding_mm
    lr_centres = np.arange(low[0], high[0] + resolution_mm, resolution_mm)
    ap_centres = np.arange(low[1], high[1] + resolution_mm, resolution_mm)
    shape = (len(lr_centres), len(ap_centres))
    top_height = np.full(shape, -np.inf, dtype=np.float32)
    top_normal = np.zeros((*shape, 3), dtype=np.float32)
    triangle_hits = np.zeros(shape, dtype=np.uint16)
    scalar_fields = {
        str(name): np.asarray(values, dtype=float)
        for name, values in (vertex_scalar_fields or {}).items()
    }
    if any(len(values) != len(vertices) for values in scalar_fields.values()):
        raise ValueError("every vertex scalar field must match the vertex count")
    top_scalars = {name: np.full(shape, np.nan, dtype=np.float32) for name in scalar_fields}

    for face in selected_faces:
        triangle = vertices[face]
        xy = triangle[:, :2]
        x = (xy[:, 0] - lr_centres[0]) / resolution_mm
        y = (xy[:, 1] - ap_centres[0]) / resolution_mm
        determinant = (xy[1, 1] - xy[2, 1]) * (xy[0, 0] - xy[2, 0]) + (xy[2, 0] - xy[1, 0]) * (
            xy[0, 1] - xy[2, 1]
        )
        if abs(determinant) <= 1e-8:
            continue
        rows, columns = polygon(x, y, shape=shape)
        if len(rows) == 0:
            continue
        points_x = lr_centres[rows]
        points_y = ap_centres[columns]
        weight_0 = (
            (xy[1, 1] - xy[2, 1]) * (points_x - xy[2, 0])
            + (xy[2, 0] - xy[1, 0]) * (points_y - xy[2, 1])
        ) / determinant
        weight_1 = (
            (xy[2, 1] - xy[0, 1]) * (points_x - xy[2, 0])
            + (xy[0, 0] - xy[2, 0]) * (points_y - xy[2, 1])
        ) / determinant
        weight_2 = 1.0 - weight_0 - weight_1
        height = weight_0 * triangle[0, 2] + weight_1 * triangle[1, 2] + weight_2 * triangle[2, 2]
        above = height >= height_floor_mm
        if not np.any(above):
            continue
        rows = rows[above]
        columns = columns[above]
        height = height[above]
        weight_0 = weight_0[above]
        weight_1 = weight_1[above]
        weight_2 = weight_2[above]
        triangle_hits[rows, columns] = np.minimum(
            triangle_hits[rows, columns].astype(np.uint32) + 1, 65535
        ).astype(np.uint16)
        current = top_height[rows, columns]
        higher = height > current
        if not np.any(higher):
            continue
        update_rows = rows[higher]
        update_columns = columns[higher]
        top_height[update_rows, update_columns] = height[higher]
        face_vertex_normals = normals[face]
        interpolated_normal = (
            weight_0[:, None] * face_vertex_normals[0]
            + weight_1[:, None] * face_vertex_normals[1]
            + weight_2[:, None] * face_vertex_normals[2]
        )
        interpolated_normal /= np.maximum(
            np.linalg.norm(interpolated_normal, axis=1, keepdims=True), EPS
        )
        top_normal[update_rows, update_columns] = interpolated_normal[higher]
        for name, values in scalar_fields.items():
            face_values = values[face]
            interpolated = (
                weight_0 * face_values[0] + weight_1 * face_values[1] + weight_2 * face_values[2]
            )
            top_scalars[name][update_rows, update_columns] = interpolated[higher]

    raw_mask = np.isfinite(top_height)
    minimum_object = max(12, round(0.35 / resolution_mm**2))
    maximum_hole = max(20, round(0.50 / resolution_mm**2))
    silhouette = closing(raw_mask, footprint=disk(1))
    silhouette = remove_small_objects(silhouette, max_size=minimum_object - 1)
    silhouette = remove_small_holes(silhouette, max_size=maximum_hole - 1)

    # Fill only sub-pixel raster holes by borrowing the nearest measured pixel.
    holes = silhouette & ~raw_mask
    if np.any(holes):
        _, nearest = distance_transform_edt(~raw_mask, return_indices=True)
        top_height[holes] = top_height[nearest[0][holes], nearest[1][holes]]
        top_normal[holes] = top_normal[nearest[0][holes], nearest[1][holes]]
        for values in top_scalars.values():
            values[holes] = values[nearest[0][holes], nearest[1][holes]]
    valid_height = np.where(silhouette, top_height, 0.0)
    boundary_sigma = max(1.2, 0.30 / resolution_mm)
    normal_sigma = max(1.2, 0.24 / resolution_mm)
    weight = gaussian_filter(silhouette.astype(float), sigma=boundary_sigma)
    smooth_height = gaussian_filter(valid_height, sigma=boundary_sigma) / np.maximum(weight, EPS)
    smooth_normal = np.zeros_like(top_normal, dtype=float)
    for channel in range(3):
        smooth_normal[..., channel] = gaussian_filter(
            np.where(silhouette, top_normal[..., channel], 0.0), sigma=normal_sigma
        ) / np.maximum(gaussian_filter(silhouette.astype(float), sigma=normal_sigma), EPS)
    smooth_normal /= np.maximum(np.linalg.norm(smooth_normal, axis=2, keepdims=True), EPS)

    height_edge_raw = scharr(smooth_height) / resolution_mm
    normal_edge_raw = np.sqrt(
        sum((scharr(smooth_normal[..., channel]) / resolution_mm) ** 2 for channel in range(3))
    )
    curvature_raw = np.abs(laplace(smooth_height)) / max(resolution_mm**2, EPS)
    interior = silhouette & binary_erosion(silhouette, iterations=2)
    height_edge = _robust_normalise(height_edge_raw, interior)
    normal_edge = _robust_normalise(normal_edge_raw, interior)
    curvature = _robust_normalise(curvature_raw, interior)
    silhouette_edge = binary_dilation(silhouette) ^ binary_erosion(silhouette)
    fused_edge = np.clip(
        0.46 * height_edge
        + 0.40 * normal_edge
        + 0.05 * curvature
        + 0.45 * silhouette_edge.astype(float),
        0.0,
        1.0,
    )
    fused_edge = gaussian_filter(fused_edge, sigma=0.80)
    fused_edge = _robust_normalise(fused_edge, silhouette | silhouette_edge)

    height_score = _robust_normalise(smooth_height, silhouette)
    normal_rgb = np.clip(0.5 * (smooth_normal + 1.0), 0.0, 1.0)
    normal_rgb[~silhouette] = 1.0
    result = {
        "resolution_mm": float(resolution_mm),
        "lr_centres": lr_centres,
        "ap_centres": ap_centres,
        "silhouette": silhouette,
        "top_height_mm": np.where(silhouette, smooth_height, np.nan),
        "height_score": height_score,
        "top_normal_lr_ap_occ": smooth_normal,
        "normal_rgb": normal_rgb,
        "height_edge": height_edge,
        "normal_edge": normal_edge,
        "curvature": curvature,
        "fused_edge": fused_edge,
        "triangle_hit_count": triangle_hits,
        "selected_triangle_count": len(selected_faces),
        "covered_pixel_count": int(np.count_nonzero(silhouette)),
    }
    for name, values in top_scalars.items():
        result[name] = np.where(silhouette, values, np.nan)
    return result

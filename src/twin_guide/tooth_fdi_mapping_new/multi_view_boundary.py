"""内部算法说明。 Deterministic multi-view geometric boundary evidence on the dental mesh.

This module borrows the useful geometric idea from CrossTooth without making
the mapping pipeline depend on a learned image model.  Orthographic views are
defined in the confirmed arch frame, rasterized with a triangle/face-id
Z-buffer, and aggregated back onto the original faces.  Integration is
component-local: FDI semantics are never inferred here and no candidate is
created or deleted.  The aggregate may refine an already chosen local
segmentation boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
from scipy.ndimage import binary_erosion, gaussian_filter
from scipy.interpolate import RegularGridInterpolator
from skimage.draw import polygon
from skimage.filters import scharr

from .models import AlignmentPath, ArchFrame
from .surface_valleys import SurfaceValleyEvidence


EPS = 1.0e-9


@dataclass(frozen=True)
class MultiViewFrame:
    """内部算法说明。 One orthographic camera expressed in global model coordinates."""

    view_id: str
    azimuth_degrees: float | None
    obliquity_degrees: float
    e_x: np.ndarray
    e_y: np.ndarray
    e_depth: np.ndarray


@dataclass(frozen=True)
class MultiViewRaster:
    """内部算法说明。 Visible highest surface and boundary response for one camera."""

    frame: MultiViewFrame
    x_centres_mm: np.ndarray
    y_centres_mm: np.ndarray
    silhouette: np.ndarray
    top_depth_mm: np.ndarray
    top_face_id: np.ndarray
    top_normal_view: np.ndarray
    depth_edge: np.ndarray
    normal_edge: np.ndarray
    valley_edge: np.ndarray
    boundary_score: np.ndarray
    visible_face_score: np.ndarray
    visible_face_pixel_count: np.ndarray
    supported_face: np.ndarray


@dataclass(frozen=True)
class MultiViewBoundaryEvidence:
    """内部算法说明。 View-consistent boundary evidence aggregated on original mesh faces."""

    rasters: tuple[MultiViewRaster, ...]
    face_boundary_score: np.ndarray
    face_view_consistency: np.ndarray
    face_visible_view_count: np.ndarray
    face_supporting_view_count: np.ndarray
    occlusal_boundary_map: np.ndarray
    occlusal_consistency_map: np.ndarray
    resolution_mm: float

    def summary(self) -> dict[str, object]:
        """内部算法说明。 Return compact JSON-safe diagnostics without per-face arrays."""

        visible = self.face_visible_view_count > 0
        supported = self.face_supporting_view_count > 0
        multi_supported = self.face_supporting_view_count >= 2
        return {
            "method": "deterministic_arch_frame_multiview_face_id_backprojection",
            "integration_mode": "component_local_watershed_cost_only",
            "view_count": len(self.rasters),
            "view_ids": [item.frame.view_id for item in self.rasters],
            "resolution_mm": float(self.resolution_mm),
            "visible_face_fraction": float(np.mean(visible)) if len(visible) else 0.0,
            "boundary_supported_face_fraction": (
                float(np.mean(supported)) if len(supported) else 0.0
            ),
            "multi_view_supported_face_fraction": (
                float(np.mean(multi_supported)) if len(multi_supported) else 0.0
            ),
            "face_boundary_score_quantiles": _finite_quantiles(
                self.face_boundary_score[visible]
            ),
            "view_consistency_quantiles": _finite_quantiles(
                self.face_view_consistency[visible]
            ),
            "views": [
                {
                    "view_id": item.frame.view_id,
                    "azimuth_degrees": item.frame.azimuth_degrees,
                    "obliquity_degrees": item.frame.obliquity_degrees,
                    "covered_pixel_count": int(np.count_nonzero(item.silhouette)),
                    "visible_face_count": int(np.count_nonzero(
                        item.visible_face_pixel_count
                    )),
                    "supported_face_count": int(np.count_nonzero(
                        item.supported_face
                    )),
                }
                for item in self.rasters
            ],
        }


def _finite_quantiles(values: np.ndarray) -> dict[str, float]:
    """内部算法说明。"""
    selected = np.asarray(values, dtype=float)
    selected = selected[np.isfinite(selected)]
    if not len(selected):
        return {"q10": 0.0, "q50": 0.0, "q90": 0.0}
    q10, q50, q90 = np.quantile(selected, [0.10, 0.50, 0.90])
    return {"q10": float(q10), "q50": float(q50), "q90": float(q90)}


def _unit(vector: np.ndarray) -> np.ndarray:
    """内部算法说明。"""
    vector = np.asarray(vector, dtype=float)
    norm = float(np.linalg.norm(vector))
    if norm <= EPS:
        raise ValueError("cannot normalize a zero view vector")
    return vector / norm


def build_multiview_frames(
    arch: ArchFrame,
    *,
    azimuth_count: int = 8,
    obliquity_degrees: float = 45.0,
) -> tuple[MultiViewFrame, ...]:
    """内部算法说明。 Create one occlusal and an evenly spaced upper-hemisphere view ring."""

    if azimuth_count < 4:
        raise ValueError("azimuth_count must be at least four")
    if not 0.0 < obliquity_degrees < 90.0:
        raise ValueError("obliquity_degrees must lie in (0, 90)")
    lr = _unit(arch.e_lr)
    ap = _unit(arch.e_ap - np.dot(arch.e_ap, lr) * lr)
    occ = _unit(np.cross(lr, ap))
    if float(occ @ arch.e_occ) < 0.0:
        occ = -occ
    frames = [MultiViewFrame(
        view_id="occlusal",
        azimuth_degrees=None,
        obliquity_degrees=0.0,
        e_x=lr,
        e_y=ap,
        e_depth=occ,
    )]
    angle = math.radians(obliquity_degrees)
    for index in range(azimuth_count):
        azimuth = 2.0 * math.pi * index / azimuth_count
        radial = math.cos(azimuth) * lr + math.sin(azimuth) * ap
        depth = _unit(math.cos(angle) * occ + math.sin(angle) * radial)
        # A horizontal camera axis followed by its in-plane vertical axis.
        e_x = _unit(np.cross(occ, depth))
        e_y = _unit(np.cross(depth, e_x))
        frames.append(MultiViewFrame(
            view_id=f"oblique_{index + 1:02d}",
            azimuth_degrees=float(math.degrees(azimuth)),
            obliquity_degrees=float(obliquity_degrees),
            e_x=e_x,
            e_y=e_y,
            e_depth=depth,
        ))
    return tuple(frames)


def _robust_normalise(values: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """内部算法说明。"""
    output = np.zeros_like(values, dtype=float)
    selected = np.asarray(values, dtype=float)[valid & np.isfinite(values)]
    if not len(selected):
        return output
    low, high = np.quantile(selected, [0.05, 0.95])
    if high - low <= EPS:
        return output
    output[valid] = np.clip(
        (np.asarray(values)[valid] - low) / (high - low), 0.0, 1.0
    )
    return output


def _rasterise_view(
    *,
    vertices: np.ndarray,
    faces: np.ndarray,
    vertex_normals: np.ndarray,
    origin_global_mm: np.ndarray,
    frame: MultiViewFrame,
    resolution_mm: float,
    face_valley_score: np.ndarray | None,
    edge_support_quantile: float,
) -> MultiViewRaster:
    """内部算法说明。 Rasterize the visible surface and retain the original face identity."""

    if resolution_mm <= 0.0:
        raise ValueError("resolution_mm must be positive")
    relative_vertices = vertices - np.asarray(origin_global_mm, dtype=float)
    transformed = np.column_stack([
        relative_vertices @ frame.e_x,
        relative_vertices @ frame.e_y,
        relative_vertices @ frame.e_depth,
    ])
    transformed_normals = np.column_stack([
        vertex_normals @ frame.e_x,
        vertex_normals @ frame.e_y,
        vertex_normals @ frame.e_depth,
    ])
    low = np.min(transformed[:, :2], axis=0) - resolution_mm
    high = np.max(transformed[:, :2], axis=0) + resolution_mm
    x_centres = np.arange(low[0], high[0] + resolution_mm, resolution_mm)
    y_centres = np.arange(low[1], high[1] + resolution_mm, resolution_mm)
    shape = (len(x_centres), len(y_centres))
    top_depth = np.full(shape, -np.inf, dtype=np.float32)
    top_face_id = np.full(shape, -1, dtype=np.int32)
    top_normal = np.zeros(shape + (3,), dtype=np.float32)
    top_valley = np.zeros(shape, dtype=np.float32)

    for face_id, face in enumerate(np.asarray(faces, dtype=np.int64)):
        triangle = transformed[face]
        xy = triangle[:, :2]
        determinant = (
            (xy[1, 1] - xy[2, 1]) * (xy[0, 0] - xy[2, 0])
            + (xy[2, 0] - xy[1, 0]) * (xy[0, 1] - xy[2, 1])
        )
        if abs(determinant) <= 1.0e-10:
            continue
        pixel_x = (xy[:, 0] - x_centres[0]) / resolution_mm
        pixel_y = (xy[:, 1] - y_centres[0]) / resolution_mm
        rows, columns = polygon(pixel_x, pixel_y, shape=shape)
        if not len(rows):
            continue
        points_x = x_centres[rows]
        points_y = y_centres[columns]
        weight_0 = (
            (xy[1, 1] - xy[2, 1]) * (points_x - xy[2, 0])
            + (xy[2, 0] - xy[1, 0]) * (points_y - xy[2, 1])
        ) / determinant
        weight_1 = (
            (xy[2, 1] - xy[0, 1]) * (points_x - xy[2, 0])
            + (xy[0, 0] - xy[2, 0]) * (points_y - xy[2, 1])
        ) / determinant
        weight_2 = 1.0 - weight_0 - weight_1
        depth = (
            weight_0 * triangle[0, 2]
            + weight_1 * triangle[1, 2]
            + weight_2 * triangle[2, 2]
        )
        higher = depth > top_depth[rows, columns]
        if not np.any(higher):
            continue
        update_rows = rows[higher]
        update_columns = columns[higher]
        top_depth[update_rows, update_columns] = depth[higher]
        top_face_id[update_rows, update_columns] = face_id
        normal = (
            weight_0[:, None] * transformed_normals[face[0]]
            + weight_1[:, None] * transformed_normals[face[1]]
            + weight_2[:, None] * transformed_normals[face[2]]
        )
        normal /= np.maximum(np.linalg.norm(normal, axis=1, keepdims=True), EPS)
        top_normal[update_rows, update_columns] = normal[higher]
        if face_valley_score is not None:
            top_valley[update_rows, update_columns] = face_valley_score[face_id]

    silhouette = top_face_id >= 0
    interior = silhouette & binary_erosion(silhouette, iterations=2)
    filled_depth = np.where(silhouette, top_depth, 0.0)
    weight = gaussian_filter(silhouette.astype(float), sigma=1.0)
    smooth_depth = gaussian_filter(filled_depth, sigma=1.0) / np.maximum(weight, EPS)
    smooth_normal = np.zeros_like(top_normal, dtype=float)
    for channel in range(3):
        smooth_normal[..., channel] = gaussian_filter(
            np.where(silhouette, top_normal[..., channel], 0.0), sigma=1.0
        ) / np.maximum(weight, EPS)
    smooth_normal /= np.maximum(
        np.linalg.norm(smooth_normal, axis=2, keepdims=True), EPS
    )
    depth_edge = _robust_normalise(
        scharr(smooth_depth) / resolution_mm, interior
    )
    normal_raw = np.sqrt(sum(
        (scharr(smooth_normal[..., channel]) / resolution_mm) ** 2
        for channel in range(3)
    ))
    normal_edge = _robust_normalise(normal_raw, interior)
    valley_edge = np.where(interior, np.clip(top_valley, 0.0, 1.0), 0.0)
    boundary = np.where(
        interior,
        np.clip(0.50 * depth_edge + 0.35 * normal_edge + 0.15 * valley_edge, 0.0, 1.0),
        0.0,
    )

    face_ids = top_face_id[silhouette]
    pixel_scores = boundary[silhouette]
    face_count = len(faces)
    visible_pixels = np.bincount(face_ids, minlength=face_count).astype(np.int32)
    score_sum = np.bincount(
        face_ids, weights=pixel_scores, minlength=face_count
    )
    score_max = np.zeros(face_count, dtype=float)
    np.maximum.at(score_max, face_ids, pixel_scores)
    mean = np.divide(
        score_sum,
        np.maximum(visible_pixels, 1),
        out=np.zeros(face_count, dtype=float),
    )
    face_score = np.sqrt(np.maximum(mean * score_max, 0.0))
    visible_scores = face_score[visible_pixels > 0]
    threshold = (
        float(np.quantile(visible_scores, edge_support_quantile))
        if len(visible_scores) else 1.0
    )
    supported = (visible_pixels > 0) & (face_score >= threshold)
    return MultiViewRaster(
        frame=frame,
        x_centres_mm=x_centres,
        y_centres_mm=y_centres,
        silhouette=silhouette,
        top_depth_mm=np.where(silhouette, smooth_depth, np.nan),
        top_face_id=top_face_id,
        top_normal_view=smooth_normal,
        depth_edge=depth_edge,
        normal_edge=normal_edge,
        valley_edge=valley_edge,
        boundary_score=boundary,
        visible_face_score=face_score,
        visible_face_pixel_count=visible_pixels,
        supported_face=supported,
    )


def build_multiview_boundary_evidence(
    dental,
    arch: ArchFrame,
    *,
    surface_valleys: SurfaceValleyEvidence | None = None,
    azimuth_count: int = 8,
    obliquity_degrees: float = 45.0,
    resolution_mm: float = 0.24,
    edge_support_quantile: float = 0.75,
) -> MultiViewBoundaryEvidence:
    """内部算法说明。 Render views and aggregate their soft boundary response on mesh faces."""

    if not 0.50 <= edge_support_quantile < 1.0:
        raise ValueError("edge_support_quantile must lie in [0.50, 1)")
    vertices = np.asarray(dental.vertices, dtype=float)
    faces = np.asarray(dental.faces, dtype=np.int64)
    normals = np.asarray(dental.vertex_normals, dtype=float)
    face_valley_score = None
    if surface_valleys is not None:
        face_valley_score = np.mean(
            surface_valleys.valley_score[faces], axis=1
        )
    rasters = tuple(
        _rasterise_view(
            vertices=vertices,
            faces=faces,
            vertex_normals=normals,
            origin_global_mm=arch.origin,
            frame=view,
            resolution_mm=resolution_mm,
            face_valley_score=face_valley_score,
            edge_support_quantile=edge_support_quantile,
        )
        for view in build_multiview_frames(
            arch,
            azimuth_count=azimuth_count,
            obliquity_degrees=obliquity_degrees,
        )
    )
    visible = np.stack([
        item.visible_face_pixel_count > 0 for item in rasters
    ])
    scores = np.stack([item.visible_face_score for item in rasters])
    support = np.stack([item.supported_face for item in rasters])
    visible_count = np.sum(visible, axis=0).astype(np.int16)
    supporting_count = np.sum(support, axis=0).astype(np.int16)
    mean_score = np.divide(
        np.sum(np.where(visible, scores, 0.0), axis=0),
        np.maximum(visible_count, 1),
        out=np.zeros(len(faces), dtype=float),
    )
    consistency = np.divide(
        supporting_count,
        np.maximum(visible_count, 1),
        out=np.zeros(len(faces), dtype=float),
    )
    aggregate = np.clip(mean_score * np.sqrt(consistency), 0.0, 1.0)
    occlusal = rasters[0]
    occlusal_boundary = np.zeros_like(occlusal.boundary_score, dtype=float)
    occlusal_consistency = np.zeros_like(occlusal.boundary_score, dtype=float)
    valid = occlusal.top_face_id >= 0
    ids = occlusal.top_face_id[valid]
    occlusal_boundary[valid] = aggregate[ids]
    occlusal_consistency[valid] = consistency[ids]
    return MultiViewBoundaryEvidence(
        rasters=rasters,
        face_boundary_score=aggregate,
        face_view_consistency=consistency,
        face_visible_view_count=visible_count,
        face_supporting_view_count=supporting_count,
        occlusal_boundary_map=occlusal_boundary,
        occlusal_consistency_map=occlusal_consistency,
        resolution_mm=float(resolution_mm),
    )


def resample_occlusal_evidence(
    evidence: MultiViewBoundaryEvidence,
    lr_centres_mm: np.ndarray,
    ap_centres_mm: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """内部算法说明。 Resample face-backprojected evidence to the mapping LR/AP grid."""

    occlusal = evidence.rasters[0]
    target_lr = np.asarray(lr_centres_mm, dtype=float)
    target_ap = np.asarray(ap_centres_mm, dtype=float)
    grid_lr, grid_ap = np.meshgrid(target_lr, target_ap, indexing="ij")
    points = np.column_stack([grid_lr.ravel(), grid_ap.ravel()])

    def interpolate(values: np.ndarray) -> np.ndarray:
        """内部算法说明。"""
        sampler = RegularGridInterpolator(
            (occlusal.x_centres_mm, occlusal.y_centres_mm),
            np.asarray(values, dtype=float),
            bounds_error=False,
            fill_value=0.0,
        )
        return sampler(points).reshape(grid_lr.shape)

    return (
        np.clip(interpolate(evidence.occlusal_boundary_map), 0.0, 1.0),
        np.clip(interpolate(evidence.occlusal_consistency_map), 0.0, 1.0),
    )


def assignment_pair_boundary_evidence(
    evidence: MultiViewBoundaryEvidence,
    alignment: AlignmentPath,
    arch: ArchFrame,
    maps: dict[str, object],
) -> tuple[list[dict[str, object]], np.ndarray, np.ndarray]:
    """内部算法说明。 Measure view-consistent boundary support between adjacent mapped teeth.

    The measurement is deliberately diagnostic-only.  It uses a thin strip at
    the pair midpoint and a transverse span scaled by the local crown size,
    preventing FDI semantics from manufacturing a boundary where none exists.
    """

    lr = np.asarray(maps["lr_centres"], dtype=float)
    ap = np.asarray(maps["ap_centres"], dtype=float)
    boundary, consistency = resample_occlusal_evidence(evidence, lr, ap)
    silhouette = np.asarray(maps["silhouette"], dtype=bool)
    relief = np.nan_to_num(
        np.asarray(
            maps.get("relative_crown_relief_score", np.zeros(silhouette.shape)),
            dtype=float,
        ),
        nan=0.0,
    )
    grid_lr, grid_ap = np.meshgrid(lr, ap, indexing="ij")
    visible_values = boundary[silhouette]
    positive_visible_values = visible_values[visible_values > EPS]
    strong_threshold = (
        float(np.quantile(positive_visible_values, 0.75))
        if len(positive_visible_values) else 1.0
    )
    assignments = [
        item for item in alignment.assignments
        if item.center_lr_ap_mm is not None and item.s_mm is not None
    ]
    records: list[dict[str, object]] = []
    resolution = float(maps["resolution_mm"])
    for pair_index, (first_item, second_item) in enumerate(
        zip(assignments, assignments[1:], strict=False)
    ):
        first = np.asarray(first_item.center_lr_ap_mm, dtype=float)
        second = np.asarray(second_item.center_lr_ap_mm, dtype=float)
        direction = second - first
        separation = float(np.linalg.norm(direction))
        if separation <= EPS:
            continue
        direction /= separation
        transverse = np.asarray([-direction[1], direction[0]])
        midpoint = 0.5 * (first + second)
        delta_lr = grid_lr - midpoint[0]
        delta_ap = grid_ap - midpoint[1]
        longitudinal = delta_lr * direction[0] + delta_ap * direction[1]
        transverse_coordinate = (
            delta_lr * transverse[0] + delta_ap * transverse[1]
        )
        local_scale = arch.scale_at_s(
            0.5 * (float(first_item.s_mm) + float(second_item.s_mm))
        )
        strip_half_width = max(2.0 * resolution, 0.12 * local_scale)
        transverse_half_span = max(3.0 * resolution, 0.60 * local_scale)
        corridor = (
            silhouette
            & (np.abs(longitudinal) <= strip_half_width)
            & (np.abs(transverse_coordinate) <= transverse_half_span)
        )
        values = boundary[corridor]
        view_values = consistency[corridor]
        if not len(values):
            mean_score = p90_score = coverage = mean_consistency = 0.0
        else:
            mean_score = float(np.mean(values))
            p90_score = float(np.quantile(values, 0.90))
            coverage = float(np.mean(
                (values > EPS) & (values >= strong_threshold)
            ))
            supported = values > 0.0
            mean_consistency = (
                float(np.mean(view_values[supported]))
                if np.any(supported) else 0.0
            )

        def crown_side_score(center: np.ndarray) -> float:
            """内部算法说明。"""
            radius = 0.30 * local_scale
            local = silhouette & (
                (grid_lr - center[0]) ** 2 + (grid_ap - center[1]) ** 2
                <= radius**2
            )
            selected = relief[local]
            return float(np.quantile(selected, 0.75)) if len(selected) else 0.0

        first_crown = crown_side_score(first)
        second_crown = crown_side_score(second)
        crown_support = math.sqrt(max(first_crown * second_crown, 0.0))
        tooth_tooth_score = float(np.clip(
            math.sqrt(max(p90_score * mean_consistency, 0.0))
            * math.sqrt(max(crown_support, 0.0))
            * math.sqrt(max(coverage, EPS)),
            0.0,
            1.0,
        ))
        records.append({
            "pair_index": int(pair_index),
            "first_FDI": int(first_item.fdi),
            "second_FDI": int(second_item.fdi),
            "first_hypothesis_kind": first_item.kind,
            "second_hypothesis_kind": second_item.kind,
            "same_hypothesis": bool(
                first_item.hypothesis_id is not None
                and first_item.hypothesis_id == second_item.hypothesis_id
            ),
            "seed_separation_mm": separation,
            "local_scale_mm": float(local_scale),
            "midpoint_lr_ap_mm": [float(midpoint[0]), float(midpoint[1])],
            "corridor_pixel_count": int(np.count_nonzero(corridor)),
            "mean_boundary_score": mean_score,
            "p90_boundary_score": p90_score,
            "strong_boundary_coverage": coverage,
            "mean_supporting_view_fraction": mean_consistency,
            "first_crown_side_support": first_crown,
            "second_crown_side_support": second_crown,
            "tooth_tooth_boundary_score": tooth_tooth_score,
            "diagnostic_only": True,
        })
    return records, boundary, consistency

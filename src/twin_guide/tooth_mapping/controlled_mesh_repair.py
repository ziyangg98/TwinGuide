"""内部算法说明。\n\nConservative repair of nearly closed guide meshes before solid Booleans.

The interface deliberately repairs only two narrow classes of defects:

* a microscopic boundary spur face whose removal restores the underlying
  closed surface; and
* a small fraction of non-manifold edges that can be canonicalized by
  Manifold without materially changing the surface or volume.

Open shells, real holes, and geometrically significant changes remain hard
failures.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import trimesh
from manifold3d import Manifold, Mesh


@dataclass(frozen=True)
class ControlledVolumeRepairPolicy:
    """内部算法说明。\n\nScale-independent safety limits for automatic volume repair."""

    maximum_boundary_edge_fraction: float = 1e-4
    maximum_boundary_spur_face_fraction: float = 1e-4
    maximum_boundary_spur_edge_fraction: float = 0.02
    maximum_non_manifold_edge_fraction: float = 1e-4
    maximum_relative_volume_change: float = 1e-5
    maximum_surface_deviation_edge_fraction: float = 0.02
    minimum_surface_deviation_limit_mm: float = 1e-5
    surface_sample_count: int = 20_000
    random_seed: int = 17

    def __post_init__(self) -> None:
        """内部算法说明。"""
        if not 0.0 < self.maximum_boundary_edge_fraction <= 1.0:
            raise ValueError("maximum_boundary_edge_fraction must be in (0, 1]")
        if not 0.0 < self.maximum_boundary_spur_face_fraction <= 1.0:
            raise ValueError(
                "maximum_boundary_spur_face_fraction must be in (0, 1]"
            )
        if not 0.0 < self.maximum_boundary_spur_edge_fraction <= 1.0:
            raise ValueError(
                "maximum_boundary_spur_edge_fraction must be in (0, 1]"
            )
        if not 0.0 < self.maximum_non_manifold_edge_fraction <= 1.0:
            raise ValueError("maximum_non_manifold_edge_fraction must be in (0, 1]")
        if not 0.0 <= self.maximum_relative_volume_change <= 1.0:
            raise ValueError("maximum_relative_volume_change must be in [0, 1]")
        if not 0.0 < self.maximum_surface_deviation_edge_fraction <= 1.0:
            raise ValueError(
                "maximum_surface_deviation_edge_fraction must be in (0, 1]"
            )
        if self.minimum_surface_deviation_limit_mm <= 0.0:
            raise ValueError("minimum_surface_deviation_limit_mm must be positive")
        if self.surface_sample_count < 100:
            raise ValueError("surface_sample_count must be at least 100")


@dataclass(frozen=True)
class ControlledVolumeRepairResult:
    """内部算法说明。"""
    mesh: trimesh.Trimesh
    report: dict[str, Any]


class ControlledVolumeRepairError(RuntimeError):
    """内部算法说明。\n\nRaised when a non-volume mesh cannot be repaired within policy limits."""

    def __init__(self, message: str, report: dict[str, Any]) -> None:
        """内部算法说明。"""
        super().__init__(message)
        self.report = report


def _topology_summary(mesh: trimesh.Trimesh) -> dict[str, Any]:
    """内部算法说明。"""
    inverse = np.asarray(mesh.edges_unique_inverse, dtype=np.int64)
    counts = np.bincount(inverse, minlength=len(mesh.edges_unique))
    return {
        "vertex_count": len(mesh.vertices),
        "face_count": len(mesh.faces),
        "unique_edge_count": len(mesh.edges_unique),
        "boundary_edge_count": int(np.count_nonzero(counts == 1)),
        "non_manifold_edge_count": int(np.count_nonzero(counts > 2)),
        "is_watertight": bool(mesh.is_watertight),
        "is_winding_consistent": bool(mesh.is_winding_consistent),
        "is_closed_volume": bool(mesh.is_volume),
        "signed_volume_mm3": float(mesh.volume),
    }


def _surface_deviation(
    original: trimesh.Trimesh,
    candidate: trimesh.Trimesh,
    sample_count: int,
    random_seed: int,
) -> tuple[float, float, float]:
    """内部算法说明。"""
    original_points, _ = trimesh.sample.sample_surface(
        original, sample_count, seed=random_seed
    )
    candidate_points, _ = trimesh.sample.sample_surface(
        candidate, sample_count, seed=random_seed + 1
    )
    _, original_to_candidate, _ = trimesh.proximity.closest_point(
        candidate, original_points
    )
    _, candidate_to_original, _ = trimesh.proximity.closest_point(
        original, candidate_points
    )
    combined = np.concatenate((original_to_candidate, candidate_to_original))
    return (
        float(np.max(combined)),
        float(np.quantile(combined, 0.99)),
        float(np.sqrt(np.mean(np.square(combined)))),
    )


def _rejection(
    message: str,
    report: dict[str, Any],
) -> ControlledVolumeRepairError:
    """内部算法说明。"""
    report["status"] = "rejected"
    report["reason"] = message
    return ControlledVolumeRepairError(message, report)


def ensure_closed_volume(
    mesh: trimesh.Trimesh,
    policy: ControlledVolumeRepairPolicy | None = None,
) -> ControlledVolumeRepairResult:
    """内部算法说明。\n\nReturn a verified volume, repairing only negligible local topology.

    A boundary is accepted only when every boundary face is a microscopic
    topological spur: it has one boundary edge and two over-shared edges, and
    removing all such faces eliminates the boundary.  Real holes and open
    shells therefore remain rejected.  Remaining negligible non-manifold
    topology is canonicalized through Manifold.  Every repaired result is
    verified for topology, volume change, bounds, and bidirectional sampled
    surface deviation.
    """

    selected_policy = policy or ControlledVolumeRepairPolicy()
    original = mesh.copy()
    original.remove_unreferenced_vertices()
    before = _topology_summary(original)
    report: dict[str, Any] = {
        "applied": False,
        "method": "none",
        "policy": asdict(selected_policy),
        "before": before,
    }
    if original.is_volume:
        report.update({
            "status": "not_needed",
            "reason": "source already is a closed volume",
            "after": before,
        })
        return ControlledVolumeRepairResult(original, report)

    edge_lengths = np.asarray(original.edges_unique_length, dtype=float)
    positive_edge_lengths = edge_lengths[edge_lengths > 0.0]
    if len(positive_edge_lengths) == 0:
        raise _rejection("source mesh has no positive-length edge", report)
    median_edge_length = float(np.median(positive_edge_lengths))

    working = original
    boundary_pruned = False
    boundary_edge_count = int(before["boundary_edge_count"])
    if boundary_edge_count:
        allowed_boundary_edge_count = max(
            1,
            int(
                np.ceil(
                    int(before["unique_edge_count"])
                    * selected_policy.maximum_boundary_edge_fraction
                )
            ),
        )
        report["allowed_boundary_edge_count"] = allowed_boundary_edge_count
        if boundary_edge_count > allowed_boundary_edge_count:
            raise _rejection(
                "open boundary edge count exceeds the controlled repair limit",
                report,
            )

        inverse = np.asarray(original.edges_unique_inverse, dtype=np.int64)
        edge_face_counts = np.bincount(
            inverse,
            minlength=len(original.edges_unique),
        )
        boundary_edge_ids = np.flatnonzero(edge_face_counts == 1)
        boundary_face_mask = np.isin(
            original.faces_unique_edges,
            boundary_edge_ids,
        ).any(axis=1)
        boundary_face_ids = np.flatnonzero(boundary_face_mask)
        allowed_spur_face_count = max(
            1,
            int(
                np.ceil(
                    int(before["face_count"])
                    * selected_policy.maximum_boundary_spur_face_fraction
                )
            ),
        )
        report["allowed_boundary_spur_face_count"] = allowed_spur_face_count
        if len(boundary_face_ids) > allowed_spur_face_count:
            raise _rejection(
                "open boundary face count exceeds the controlled spur limit",
                report,
            )

        boundary_face_edge_counts = edge_face_counts[
            original.faces_unique_edges[boundary_face_ids]
        ]
        spur_topology_mask = (
            (boundary_face_edge_counts == 1).sum(axis=1) == 1
        ) & (
            (boundary_face_edge_counts > 2).sum(axis=1) == 2
        )
        if not bool(np.all(spur_topology_mask)):
            raise _rejection(
                "open boundary is not composed exclusively of removable spur faces",
                report,
            )

        spur_edge_limit = (
            median_edge_length
            * selected_policy.maximum_boundary_spur_edge_fraction
        )
        maximum_spur_edge_length = float(
            np.max(
                original.edges_unique_length[
                    original.faces_unique_edges[boundary_face_ids]
                ]
            )
        )
        report["boundary_spur_pruning"] = {
            "face_indices": boundary_face_ids.astype(int).tolist(),
            "face_count": len(boundary_face_ids),
            "maximum_edge_length_mm": maximum_spur_edge_length,
            "edge_length_limit_mm": spur_edge_limit,
        }
        if maximum_spur_edge_length > spur_edge_limit:
            raise _rejection(
                "boundary spur exceeds the local mesh-scale limit",
                report,
            )

        working = original.copy()
        keep_faces = np.ones(len(working.faces), dtype=bool)
        keep_faces[boundary_face_ids] = False
        working.update_faces(keep_faces)
        working.remove_unreferenced_vertices()
        after_pruning = _topology_summary(working)
        report["after_boundary_spur_pruning"] = after_pruning
        if int(after_pruning["boundary_edge_count"]) != 0:
            raise _rejection(
                "boundary spur pruning did not eliminate all open boundaries",
                report,
            )
        boundary_pruned = True

    working_summary = _topology_summary(working)
    non_manifold_count = int(working_summary["non_manifold_edge_count"])
    if working.is_volume:
        candidate = working
        after = working_summary
        report["after"] = after
        manifold_canonicalized = False
    else:
        manifold_canonicalized = True
        if non_manifold_count == 0:
            raise _rejection(
                "source is not a volume but has no repairable non-manifold edge",
                report,
            )
        allowed_count = max(
            1,
            int(
                np.ceil(
                    int(working_summary["unique_edge_count"])
                    * selected_policy.maximum_non_manifold_edge_fraction
                )
            ),
        )
        report["allowed_non_manifold_edge_count"] = allowed_count
        if non_manifold_count > allowed_count:
            raise _rejection(
                "non-manifold defect count exceeds the controlled repair limit",
                report,
            )

        solid = Manifold(mesh=Mesh(
            vert_properties=np.asarray(working.vertices, dtype=np.float32),
            tri_verts=np.asarray(working.faces, dtype=np.uint32),
        ))
        status = str(solid.status())
        report["manifold_status"] = status
        if status != "Error.NoError":
            raise _rejection(
                f"Manifold canonicalization rejected the source mesh: {status}",
                report,
            )
        indexed = solid.to_mesh()
        candidate = trimesh.Trimesh(
            vertices=np.asarray(indexed.vert_properties),
            faces=np.asarray(indexed.tri_verts),
            process=False,
        )
        candidate.remove_unreferenced_vertices()
        candidate.fix_normals(multibody=True)
        after = _topology_summary(candidate)
        report["after"] = after
        if not candidate.is_volume:
            raise _rejection(
                "Manifold canonicalization did not produce a closed volume",
                report,
            )

    surface_limit = max(
        selected_policy.minimum_surface_deviation_limit_mm,
        median_edge_length
        * selected_policy.maximum_surface_deviation_edge_fraction,
    )
    bounds_change = float(np.max(np.abs(candidate.bounds - original.bounds)))
    original_volume = float(abs(original.volume))
    candidate_volume = float(abs(candidate.volume))
    relative_volume_change = abs(candidate_volume - original_volume) / max(
        original_volume, 1e-12
    )
    maximum_deviation, p99_deviation, rms_deviation = _surface_deviation(
        original,
        candidate,
        selected_policy.surface_sample_count,
        selected_policy.random_seed,
    )
    report["geometry_preservation"] = {
        "median_source_edge_length_mm": median_edge_length,
        "surface_deviation_limit_mm": surface_limit,
        "bounds_maximum_change_mm": bounds_change,
        "sampled_surface_maximum_deviation_mm": maximum_deviation,
        "sampled_surface_p99_deviation_mm": p99_deviation,
        "sampled_surface_rms_deviation_mm": rms_deviation,
        "relative_volume_change": relative_volume_change,
    }
    if bounds_change > surface_limit:
        raise _rejection(
            "controlled repair changed the guide bounds beyond tolerance",
            report,
        )
    if maximum_deviation > surface_limit:
        raise _rejection(
            "controlled repair changed the guide surface beyond tolerance",
            report,
        )
    if relative_volume_change > selected_policy.maximum_relative_volume_change:
        raise _rejection(
            "controlled repair changed the guide volume beyond tolerance",
            report,
        )

    if boundary_pruned and manifold_canonicalized:
        method = "boundary_spur_pruning_and_manifold3d_canonicalization"
        reason = "microscopic boundary spur and local non-manifold topology repaired"
    elif boundary_pruned:
        method = "microscopic_boundary_spur_face_pruning"
        reason = "microscopic boundary spur removed within all safety limits"
    else:
        method = "manifold3d_indexed_canonicalization"
        reason = "local non-manifold topology repaired within all safety limits"
    report.update({
        "applied": True,
        "method": method,
        "status": "repaired",
        "reason": reason,
    })
    return ControlledVolumeRepairResult(candidate, report)


__all__ = [
    "ControlledVolumeRepairError",
    "ControlledVolumeRepairPolicy",
    "ControlledVolumeRepairResult",
    "ensure_closed_volume",
]

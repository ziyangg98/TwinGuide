"""算法说明。 Typed records for the isolated ``fdi_new`` workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from twin_guide.config import (
    TOOTH_FDI_MAPPING_NEW_PROFILE_ID as _TOOTH_FDI_MAPPING_NEW_PROFILE_ID,
)
from twin_guide.config import ToothFdiMappingNewProfile

TOOTH_FDI_MAPPING_NEW_PROFILE_ID = _TOOTH_FDI_MAPPING_NEW_PROFILE_ID


@dataclass(frozen=True)
class ToothFdiMappingNewRequest:
    """算法说明。"""

    case_yaml: Path
    output_dir: Path
    profile: ToothFdiMappingNewProfile = field(default_factory=ToothFdiMappingNewProfile)
    write_report_json: bool = False

    def resolved(self) -> ToothFdiMappingNewRequest:
        """算法说明。"""
        case_yaml = Path(self.case_yaml).resolve()
        if not case_yaml.is_file():
            raise FileNotFoundError(f"case YAML does not exist: {case_yaml}")
        return ToothFdiMappingNewRequest(
            case_yaml=case_yaml,
            output_dir=Path(self.output_dir).resolve(),
            profile=self.profile,
            write_report_json=self.write_report_json,
        )


@dataclass
class ArchFrame:
    """算法说明。"""

    origin: np.ndarray
    e_lr: np.ndarray
    e_ap: np.ndarray
    e_occ: np.ndarray
    curve_lr: np.ndarray
    curve_ap: np.ndarray
    curve_s: np.ndarray
    local_scale_mm: np.ndarray
    orientation_name: str
    pca_occlusal_axis_index: int | None = None
    guide_occlusal_alignment: float | None = None
    pca_eigenvalues: tuple[float, float, float] | None = None

    def at_s(self, value: float) -> np.ndarray:
        """算法说明。"""
        return np.asarray(
            [
                np.interp(value, self.curve_s, self.curve_lr),
                np.interp(value, self.curve_s, self.curve_ap),
            ]
        )

    def project_lr_ap(self, point: np.ndarray) -> tuple[float, float]:
        """算法说明。"""
        samples = np.column_stack([self.curve_lr, self.curve_ap])
        index = int(np.argmin(np.linalg.norm(samples - point, axis=1)))
        tangent = np.asarray(
            [
                np.gradient(self.curve_lr)[index],
                np.gradient(self.curve_ap)[index],
            ]
        )
        tangent /= max(float(np.linalg.norm(tangent)), 1.0e-9)
        normal = np.asarray([-tangent[1], tangent[0]])
        return float(self.curve_s[index]), float((point - samples[index]) @ normal)

    def scale_at_s(self, value: float) -> float:
        """算法说明。"""
        return float(np.interp(value, self.curve_s, self.local_scale_mm))

    def tangent_at_s(self, value: float) -> np.ndarray:
        """算法说明。 Return a unit LR/AP arch tangent at the requested arc position."""

        step = max(0.50, 0.10 * self.scale_at_s(value))
        tangent = self.at_s(value + step) - self.at_s(value - step)
        norm = float(np.linalg.norm(tangent))
        if norm <= 1.0e-9:
            return np.asarray([1.0, 0.0])
        return tangent / norm


@dataclass(frozen=True)
class LabeledMissingSlotAnchor:
    """内部算法说明。 A registered sleeve carrying the hard FDI identity of a missing slot."""

    fdi: int
    sleeve_id: str
    label_source: str
    mesh_path: str
    point_global_mm: tuple[float, float, float]
    point_method: str


@dataclass(frozen=True)
class CoreObservation:
    """算法说明。"""

    scale_index: int
    height_quantile: float
    center_lr_ap_mm: tuple[float, float]
    s_mm: float
    u_mm: float
    interior_radius_mm: float
    quality: float
    mesiodistal_width_mm: float = 0.0
    buccolingual_width_mm: float = 0.0
    relative_crown_height_mm: float = 0.0
    relief_quality: float = 0.0
    projection_component_area_ratio: float = 1.0


@dataclass(frozen=True)
class CoreTrack:
    """算法说明。"""

    track_id: int
    observations: tuple[CoreObservation, ...]
    center_lr_ap_mm: tuple[float, float]
    s_mm: float
    u_mm: float
    local_scale_mm: float
    persistence: float
    crownness: float
    support_scale_indices: tuple[int, ...]
    mesiodistal_width_mm: float = 0.0
    buccolingual_width_mm: float = 0.0
    relative_crown_height_mm: float = 0.0
    relief_quality: float = 0.0
    relative_3d_tooth_support: float = 1.0
    projection_component_area_ratio: float = 1.0


@dataclass(frozen=True)
class CrownHypothesis:
    """算法说明。"""

    hypothesis_id: str
    kind: str
    first_core_index: int
    last_core_index: int
    core_ids: tuple[int, ...]
    fdi_count: int
    centers_lr_ap_mm: tuple[tuple[float, float], ...]
    centers_s_mm: tuple[float, ...]
    width_mm: float
    crownness: float
    persistence: float
    evidence_probability: float
    # A split is a physical two-crown claim, not merely a convenient way to
    # consume two FDI labels.  These fields record the simultaneous,
    # multi-height basin evidence that existed before any separator was drawn.
    subbasin_persistence: float = 0.0
    independent_subbasin_count: int = 1


@dataclass(frozen=True)
class AlignmentAssignment:
    """算法说明。"""

    fdi: int
    hypothesis_id: str | None
    kind: str
    core_ids: tuple[int, ...]
    center_lr_ap_mm: tuple[float, float] | None
    s_mm: float | None
    persistence: float
    match_cost: float
    subbasin_persistence: float = 0.0
    independent_subbasin_count: int = 1


@dataclass(frozen=True)
class AlignmentPath:
    """算法说明。"""

    orientation_name: str
    global_scale: float
    midline_offset_mm: float
    total_cost: float
    assignments: tuple[AlignmentAssignment, ...]
    artifact_core_ids: tuple[int, ...]
    consumed_core_ids: tuple[int, ...]
    undetected_fdi: tuple[int, ...]
    signature: tuple[str, ...]


@dataclass(frozen=True)
class ToothRegion:
    """算法说明。"""

    fdi: int
    region_id: int
    pixel_count: int
    area_mm2: float
    area_centroid_lr_ap_mm: tuple[float, float]
    interior_center_lr_ap_mm: tuple[float, float]
    maximum_interior_radius_mm: float
    contour_lr_ap_mm: tuple[tuple[float, float], ...]
    component_ids: tuple[int, ...]
    boundary_method: str
    boundary_confidence: float
    crown_height_mm: float
    crown_point_global_mm: tuple[float, float, float]
    relative_relief_mean_mm: float = 0.0
    relative_relief_p90_mm: float = 0.0
    relative_relief_score: float = 0.0


@dataclass(frozen=True)
class ToothFdiMappingNewResult:
    """算法说明。"""

    case_yaml: Path
    output_dir: Path
    profile: ToothFdiMappingNewProfile
    report: dict[str, Any]
    report_path: Path | None
    multichannel_preview_path: Path
    mapping_preview_path: Path
    multiview_preview_path: Path | None = None

    @property
    def status(self) -> str:
        """算法说明。"""
        return str(self.report.get("status", "needs_review"))

    @property
    def safe_for_downstream_use(self) -> bool:
        """算法说明。"""
        return bool(
            self.report.get("safe_for_downstream_use", False)
            and all(self.report.get("QA", {}).values())
        )

"""算法说明。 Typed records for the isolated ``fdi_new`` workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np


TOOTH_FDI_MAPPING_NEW_PROFILE_ID = (
    "tooth_fdi_mapping_new_intercore_separator_exclusion"
)


@dataclass(frozen=True)
class ToothFdiMappingNewProfile:
    """算法说明。 Versioned, case-independent recognition settings."""

    profile_id: str = TOOTH_FDI_MAPPING_NEW_PROFILE_ID
    height_quantiles: tuple[float, ...] = (0.35, 0.40, 0.45, 0.50, 0.55)
    projection_resolution_mm: float = 0.12
    component_segmentation_resolution_mm: float = 0.12
    minimum_normal_dot: float = 0.05
    minimum_track_persistence: float = 0.60
    minimum_alignment_margin_per_tooth: float = 0.05
    minimum_independent_core_separation_scale: float = 0.35
    boundary_first_segmentation: bool = True
    require_anatomical_split_evidence: bool = True
    maximum_local_assignment_robust_z: float = 4.0
    maximum_bilateral_region_area_ratio: float = 1.80
    midline_offset_search_local_scale: float = 1.50
    minimum_relative_crown_height_ratio: float = 0.60
    minimum_relative_relief_quality_ratio: float = 0.75
    maximum_low_relief_component_area_ratio: float = 0.50
    relief_baseline_windows_mm: tuple[float, ...] = (6.0, 8.0, 10.0)
    unassigned_relief_quantile: float = 0.08
    unassigned_seed_protection_scale: float = 0.55
    minimum_unassigned_area_mm2: float = 1.50
    surface_valley_evidence_enabled: bool = True
    surface_valley_normalization_scale_mm: float = 8.0
    surface_valley_smoothing_iterations: tuple[int, ...] = (1, 2, 4)
    surface_valley_watershed_weight: float = 0.22
    minimum_surface_valley_mean_support: float = 0.36
    minimum_surface_valley_coverage: float = 0.32
    multi_view_boundary_enabled: bool = True
    multi_view_azimuth_count: int = 8
    multi_view_obliquity_degrees: float = 45.0
    multi_view_resolution_mm: float = 0.24
    multi_view_edge_support_quantile: float = 0.75
    multi_view_watershed_weight: float = 0.12
    stability_resolutions_mm: tuple[float, ...] = (0.10, 0.12, 0.14)
    boundary_smoothing_scales: tuple[float, ...] = (0.85, 1.0, 1.15)
    run_stability: bool = True

    def __post_init__(self) -> None:
        """算法说明。"""
        if not self.profile_id.strip():
            raise ValueError("profile_id must not be empty")
        if not self.height_quantiles or any(
            not 0.0 < value < 1.0 for value in self.height_quantiles
        ):
            raise ValueError("height_quantiles must lie strictly between zero and one")
        if tuple(sorted(set(self.height_quantiles))) != self.height_quantiles:
            raise ValueError("height_quantiles must be unique and increasing")
        if self.projection_resolution_mm <= 0.0:
            raise ValueError("projection_resolution_mm must be positive")
        if self.component_segmentation_resolution_mm <= 0.0:
            raise ValueError("component_segmentation_resolution_mm must be positive")
        if not 0.0 <= self.minimum_track_persistence <= 1.0:
            raise ValueError("minimum_track_persistence must lie in [0, 1]")
        if self.minimum_independent_core_separation_scale <= 0.0:
            raise ValueError(
                "minimum_independent_core_separation_scale must be positive"
            )
        if self.maximum_local_assignment_robust_z <= 0.0:
            raise ValueError("maximum_local_assignment_robust_z must be positive")
        if self.maximum_bilateral_region_area_ratio <= 1.0:
            raise ValueError(
                "maximum_bilateral_region_area_ratio must be greater than one"
            )
        if self.midline_offset_search_local_scale <= 0.0:
            raise ValueError(
                "midline_offset_search_local_scale must be positive"
            )
        if not 0.0 < self.minimum_relative_crown_height_ratio <= 1.0:
            raise ValueError(
                "minimum_relative_crown_height_ratio must lie in (0, 1]"
            )
        if not 0.0 < self.minimum_relative_relief_quality_ratio <= 1.0:
            raise ValueError(
                "minimum_relative_relief_quality_ratio must lie in (0, 1]"
            )
        if not 0.0 < self.maximum_low_relief_component_area_ratio < 1.0:
            raise ValueError(
                "maximum_low_relief_component_area_ratio must lie in (0, 1)"
            )
        if (
            not self.relief_baseline_windows_mm
            or any(value <= 0.0 for value in self.relief_baseline_windows_mm)
        ):
            raise ValueError("relief_baseline_windows_mm must be positive")
        if not 0.0 <= self.unassigned_relief_quantile <= 0.25:
            raise ValueError("unassigned_relief_quantile must lie in [0, 0.25]")
        if self.unassigned_seed_protection_scale <= 0.0:
            raise ValueError("unassigned_seed_protection_scale must be positive")
        if self.minimum_unassigned_area_mm2 < 0.0:
            raise ValueError("minimum_unassigned_area_mm2 must be non-negative")
        if self.surface_valley_normalization_scale_mm <= 0.0:
            raise ValueError(
                "surface_valley_normalization_scale_mm must be positive"
            )
        if (
            not self.surface_valley_smoothing_iterations
            or any(value < 0 for value in self.surface_valley_smoothing_iterations)
        ):
            raise ValueError(
                "surface_valley_smoothing_iterations must be non-negative"
            )
        if not 0.0 <= self.surface_valley_watershed_weight <= 0.5:
            raise ValueError(
                "surface_valley_watershed_weight must lie in [0, 0.5]"
            )
        if not 0.0 <= self.minimum_surface_valley_mean_support <= 1.0:
            raise ValueError(
                "minimum_surface_valley_mean_support must lie in [0, 1]"
            )
        if not 0.0 <= self.minimum_surface_valley_coverage <= 1.0:
            raise ValueError(
                "minimum_surface_valley_coverage must lie in [0, 1]"
            )
        if self.multi_view_azimuth_count < 4:
            raise ValueError("multi_view_azimuth_count must be at least four")
        if not 0.0 < self.multi_view_obliquity_degrees < 90.0:
            raise ValueError(
                "multi_view_obliquity_degrees must lie in (0, 90)"
            )
        if self.multi_view_resolution_mm <= 0.0:
            raise ValueError("multi_view_resolution_mm must be positive")
        if not 0.50 <= self.multi_view_edge_support_quantile < 1.0:
            raise ValueError(
                "multi_view_edge_support_quantile must lie in [0.50, 1)"
            )
        if not 0.0 <= self.multi_view_watershed_weight <= 0.35:
            raise ValueError(
                "multi_view_watershed_weight must lie in [0, 0.35]"
            )


@dataclass(frozen=True)
class ToothFdiMappingNewRequest:
    """算法说明。"""
    case_yaml: Path
    output_dir: Path
    profile: ToothFdiMappingNewProfile = field(
        default_factory=ToothFdiMappingNewProfile
    )
    write_report_json: bool = False

    def resolved(self) -> "ToothFdiMappingNewRequest":
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
        return np.asarray([
            np.interp(value, self.curve_s, self.curve_lr),
            np.interp(value, self.curve_s, self.curve_ap),
        ])

    def project_lr_ap(self, point: np.ndarray) -> tuple[float, float]:
        """算法说明。"""
        samples = np.column_stack([self.curve_lr, self.curve_ap])
        index = int(np.argmin(np.linalg.norm(samples - point, axis=1)))
        tangent = np.asarray([
            np.gradient(self.curve_lr)[index],
            np.gradient(self.curve_ap)[index],
        ])
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

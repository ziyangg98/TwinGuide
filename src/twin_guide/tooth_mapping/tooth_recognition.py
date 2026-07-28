"""内部算法说明。\n\nStable library interface for the current tooth-recognition workflow.

The public contract in this module deliberately hides the three historical
CLI stages (base anatomical frame, enhanced projection, and contact-chord
partitioning).  Callers provide one case definition and receive one typed
result whose manifest can be handed to the guide-mapping module.

The historical stage implementations are now private modules of TwinGuide.
Code outside this package should depend on :func:`recognize_teeth`, not on
individual stage modules.
"""

from __future__ import annotations

import json
from argparse import Namespace
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .arch_progress_core_grouping import (
    CORE_GROUPING_POLICIES,
    DEFAULT_POLICY,
    LEGACY_POLICY,
)
from .extract_contact_chord_contours import run as _extract_contours
from .pipeline import run_case_mapping
from .render_enhanced_crown_projection import run as _render_projection

LOCKED_RECOGNITION_PARAMETERS = {
    "arch_corridor_half_width_mm": 11.5,
    "core_peak_window_mm": 2.4,
    "minimum_core_depth_mm": 0.75,
    "duplicate_peak_suppression_mm": 3.0,
    "maximum_adjacent_core_merge_mm": 5.75,
    "default_core_grouping_policy": DEFAULT_POLICY,
    "separator_policy": "concavity_only_fail_closed",
}


class ToothRecognitionError(RuntimeError):
    """内部算法说明。\n\nRaised when a recognition request or stage contract is invalid."""


@dataclass(frozen=True)
class ToothRecognitionProfile:
    """内部算法说明。\n\nVersioned, case-independent parameters for tooth recognition."""

    profile_id: str = "tooth_recognition_arch_progress_v2"
    crown_height_quantile: float = 0.55
    minimum_crown_normal_dot: float = 0.05
    projection_resolution_mm: float = 0.12
    contour_resolution_mm: float = 0.18
    random_state: int = 17
    height_quantile_override: float | None = None
    core_grouping_policy: str = DEFAULT_POLICY

    def __post_init__(self) -> None:
        """内部算法说明。"""
        if not self.profile_id.strip():
            raise ValueError("recognition profile_id must not be empty")
        if not 0.0 < self.crown_height_quantile < 1.0:
            raise ValueError("crown_height_quantile must lie between 0 and 1")
        if (
            self.height_quantile_override is not None
            and not 0.0 < self.height_quantile_override < 1.0
        ):
            raise ValueError("height_quantile_override must lie between 0 and 1")
        if self.projection_resolution_mm <= 0.0:
            raise ValueError("projection_resolution_mm must be positive")
        if self.contour_resolution_mm <= 0.0:
            raise ValueError("contour_resolution_mm must be positive")
        if self.core_grouping_policy not in CORE_GROUPING_POLICIES:
            raise ValueError(
                "core_grouping_policy must be one of "
                f"{CORE_GROUPING_POLICIES}, got {self.core_grouping_policy!r}"
            )


@dataclass(frozen=True)
class ToothRecognitionRequest:
    """内部算法说明。\n\nInputs for one complete recognition run.

    ``case_yaml`` is the current compatibility carrier for the oral-scan path,
    jaw, authoritative FDI sets, and optional confirmed anatomical axes.  The
    contract is intentionally typed so a later byte/mesh request adapter can
    be added without changing the result consumed by guide mapping.
    """

    case_yaml: Path
    output_dir: Path
    profile: ToothRecognitionProfile = field(
        default_factory=ToothRecognitionProfile
    )

    def resolved(self) -> ToothRecognitionRequest:
        """内部算法说明。"""
        case_yaml = Path(self.case_yaml).resolve()
        if not case_yaml.is_file():
            raise ToothRecognitionError(f"case YAML does not exist: {case_yaml}")
        return ToothRecognitionRequest(
            case_yaml=case_yaml,
            output_dir=Path(self.output_dir).resolve(),
            profile=self.profile,
        )


@dataclass(frozen=True)
class ToothRecognitionResult:
    """内部算法说明。\n\nApproved recognition artifacts shared with downstream modules."""

    case_yaml: Path
    output_dir: Path
    profile: ToothRecognitionProfile
    created_at: str
    base_mapping_report: dict[str, Any]
    projection_report: dict[str, Any]
    contour_report: dict[str, Any]
    manifest_path: Path

    @property
    def status(self) -> str:
        """内部算法说明。"""
        return str(self.contour_report.get("status", "needs_review"))

    @property
    def safe_for_guide_mapping(self) -> bool:
        """内部算法说明。"""
        return bool(
            self.contour_report.get("safe_for_downstream_use", False)
            and all(self.contour_report.get("QA", {}).values())
        )

    @property
    def base_mapping_path(self) -> Path:
        """内部算法说明。"""
        return Path(self.base_mapping_report["outputs"]["report_json"])

    @property
    def enhanced_maps_path(self) -> Path:
        """内部算法说明。"""
        return Path(self.projection_report["outputs"]["arrays"])

    @property
    def contact_report_path(self) -> Path:
        """内部算法说明。"""
        return Path(self.contour_report["outputs"]["report"])

    def manifest(self) -> dict[str, Any]:
        """内部算法说明。"""
        return {
            "schema_version": "1.0-tooth-recognition-workflow",
            "created_at": self.created_at,
            "status": self.status,
            "safe_for_guide_mapping": self.safe_for_guide_mapping,
            "case_yaml": str(self.case_yaml),
            "profile": asdict(self.profile),
            "locked_implementation_parameters": dict(
                LOCKED_RECOGNITION_PARAMETERS
            ),
            "stages": {
                "base_mapping_status": self.base_mapping_report.get("status"),
                "projection_selection_succeeded": self.projection_report.get(
                    "height_floor_selection", {}
                ).get("selection_succeeded", False),
                "contour_status": self.contour_report.get("status"),
            },
            "outputs": {
                "base_mapping_report": str(self.base_mapping_path),
                "enhanced_projection_report": str(
                    self.projection_report["outputs"]["report"]
                ),
                "enhanced_projection_maps": str(self.enhanced_maps_path),
                "contact_chord_report": str(self.contact_report_path),
                "workflow_manifest": str(self.manifest_path),
            },
        }


def _read_json(path: Path) -> dict[str, Any]:
    """内部算法说明。"""
    return json.loads(path.read_text(encoding="utf-8"))


def load_tooth_recognition_result(path: Path) -> ToothRecognitionResult:
    """内部算法说明。\n\nLoad a recognition result written by :func:`recognize_teeth`."""

    manifest_path = Path(path).resolve()
    if manifest_path.is_dir():
        manifest_path = manifest_path / "tooth_recognition_result.json"
    manifest = _read_json(manifest_path)
    if manifest.get("schema_version") != "1.0-tooth-recognition-workflow":
        raise ToothRecognitionError(
            f"unsupported tooth-recognition manifest: {manifest_path}"
        )
    outputs = manifest["outputs"]
    profile_values = dict(manifest["profile"])
    # Manifests produced before the arch-progress integration did not record a
    # grouping policy and must retain their historical Euclidean semantics.
    profile_values.setdefault("core_grouping_policy", LEGACY_POLICY)
    profile = ToothRecognitionProfile(**profile_values)
    return ToothRecognitionResult(
        case_yaml=Path(manifest["case_yaml"]),
        output_dir=manifest_path.parent,
        profile=profile,
        created_at=str(manifest["created_at"]),
        base_mapping_report=_read_json(Path(outputs["base_mapping_report"])),
        projection_report=_read_json(Path(outputs["enhanced_projection_report"])),
        contour_report=_read_json(Path(outputs["contact_chord_report"])),
        manifest_path=manifest_path,
    )


def recognize_teeth(request: ToothRecognitionRequest) -> ToothRecognitionResult:
    """内部算法说明。\n\nRun the unified non-multiscale tooth-recognition workflow."""

    request = request.resolved()
    profile = request.profile
    request.output_dir.mkdir(parents=True, exist_ok=True)
    base_dir = request.output_dir / "01_base_mapping"
    projection_dir = request.output_dir / "02_enhanced_projection"
    contour_dir = request.output_dir / "03_contact_contours"

    base_report = run_case_mapping(
        request.case_yaml,
        output_dir=base_dir,
        crown_height_quantile=profile.crown_height_quantile,
        minimum_normal_dot=profile.minimum_crown_normal_dot,
    )
    base_path = Path(base_report["outputs"]["report_json"])
    projection_report = _render_projection(Namespace(
        case=request.case_yaml,
        output_dir=projection_dir,
        mapping_report=base_path,
        resolution_mm=profile.projection_resolution_mm,
        height_quantile=profile.height_quantile_override,
        core_grouping_policy=profile.core_grouping_policy,
    ))
    contour_report = _extract_contours(Namespace(
        case=request.case_yaml,
        output_dir=contour_dir,
        resolution_mm=profile.contour_resolution_mm,
        random_state=profile.random_state,
        enhanced_maps=Path(projection_report["outputs"]["arrays"]),
        mapping_report=base_path,
        core_grouping_policy=profile.core_grouping_policy,
    ))
    manifest_path = request.output_dir / "tooth_recognition_result.json"
    result = ToothRecognitionResult(
        case_yaml=request.case_yaml,
        output_dir=request.output_dir,
        profile=profile,
        created_at=datetime.now(UTC).isoformat(),
        base_mapping_report=base_report,
        projection_report=projection_report,
        contour_report=contour_report,
        manifest_path=manifest_path,
    )
    manifest_path.write_text(
        json.dumps(result.manifest(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


__all__ = [
    "LOCKED_RECOGNITION_PARAMETERS",
    "ToothRecognitionError",
    "ToothRecognitionProfile",
    "ToothRecognitionRequest",
    "ToothRecognitionResult",
    "load_tooth_recognition_result",
    "recognize_teeth",
]

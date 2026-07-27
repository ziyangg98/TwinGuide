"""内部算法说明。\n\nStable library interface for mapping recognized teeth onto a guide."""

from __future__ import annotations

import json
from argparse import Namespace
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .map_contact_chord_teeth_to_guide import run as _map_to_guide

from .tooth_recognition import (
    ToothRecognitionResult,
    load_tooth_recognition_result,
)


LOCKED_GUIDE_MAPPING_PARAMETERS = {
    "maximum_crown_height_fallback_mm": 1.5,
    "minimum_ordered_center_spacing_mm": 0.25,
    "minimum_contour_arch_extent_mm": 1.0,
    "contour_interval_tolerance_mm": 0.05,
    "minimum_contour_following_window_mapping_fraction": 0.80,
    "physical_coverage_minimum_normal_dot": 0.10,
    "physical_coverage_minimum_outward_offset_mm": 0.25,
}


class GuideMappingError(RuntimeError):
    """内部算法说明。\n\nRaised when approved recognition artifacts cannot be guide-mapped."""


@dataclass(frozen=True)
class GuideMappingProfile:
    """内部算法说明。\n\nVersion identifier for the current fixed guide-mapping policy."""

    profile_id: str = "contact_chord_guide_mapping_v2_physical_coverage"

    def __post_init__(self) -> None:
        """内部算法说明。"""
        if not self.profile_id.strip():
            raise ValueError("guide-mapping profile_id must not be empty")


@dataclass(frozen=True)
class GuideMappingRequest:
    """内部算法说明。\n\nMap one approved recognition result to its configured guide."""

    recognition: ToothRecognitionResult | Path
    output_dir: Path
    case_yaml: Path | None = None
    profile: GuideMappingProfile = field(default_factory=GuideMappingProfile)

    def resolved_recognition(self) -> ToothRecognitionResult:
        """内部算法说明。"""
        if isinstance(self.recognition, ToothRecognitionResult):
            return self.recognition
        return load_tooth_recognition_result(Path(self.recognition))


@dataclass(frozen=True)
class GuideMappingResult:
    """内部算法说明。"""
    case_yaml: Path
    output_dir: Path
    profile: GuideMappingProfile
    created_at: str
    recognition_manifest_path: Path
    mapping_report: dict[str, Any]
    manifest_path: Path

    @property
    def status(self) -> str:
        """内部算法说明。"""
        return str(
            self.mapping_report.get("status", "tooth_guide_mapping_needs_review")
        )

    @property
    def complete(self) -> bool:
        """内部算法说明。"""
        return bool(
            self.status == "tooth_guide_mapping_complete"
            and all(self.mapping_report.get("QA", {}).values())
        )

    @property
    def report_path(self) -> Path:
        """内部算法说明。"""
        return Path(self.mapping_report["outputs"]["report_json"])

    def manifest(self) -> dict[str, Any]:
        """内部算法说明。"""
        return {
            "schema_version": "1.0-guide-mapping-workflow",
            "created_at": self.created_at,
            "status": self.status,
            "complete": self.complete,
            "case_yaml": str(self.case_yaml),
            "profile": asdict(self.profile),
            "locked_implementation_parameters": dict(
                LOCKED_GUIDE_MAPPING_PARAMETERS
            ),
            "recognition_manifest": str(self.recognition_manifest_path),
            "outputs": {
                "guide_mapping_report": str(self.report_path),
                "preview_png": self.mapping_report["outputs"]["preview_png"],
                "context_glb": self.mapping_report["outputs"]["context_glb"],
                "workflow_manifest": str(self.manifest_path),
            },
        }


def map_recognized_teeth_to_guide(
    request: GuideMappingRequest,
) -> GuideMappingResult:
    """内部算法说明。\n\nMap an approved recognition result into guide coordinates."""

    recognition = request.resolved_recognition()
    if not recognition.safe_for_guide_mapping:
        raise GuideMappingError(
            "tooth recognition has not passed all downstream safety gates: "
            f"{recognition.manifest_path}"
        )
    case_yaml = (
        Path(request.case_yaml).resolve()
        if request.case_yaml is not None
        else recognition.case_yaml.resolve()
    )
    if not case_yaml.is_file():
        raise GuideMappingError(f"case YAML does not exist: {case_yaml}")
    output_dir = Path(request.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    mapping_report = _map_to_guide(Namespace(
        case=case_yaml,
        contact_report=recognition.contact_report_path,
        base_mapping_report=recognition.base_mapping_path,
        enhanced_maps=recognition.enhanced_maps_path,
        output_dir=output_dir,
    ))
    manifest_path = output_dir / "guide_mapping_result.json"
    result = GuideMappingResult(
        case_yaml=case_yaml,
        output_dir=output_dir,
        profile=request.profile,
        created_at=datetime.now(timezone.utc).isoformat(),
        recognition_manifest_path=recognition.manifest_path,
        mapping_report=mapping_report,
        manifest_path=manifest_path,
    )
    manifest_path.write_text(
        json.dumps(result.manifest(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


__all__ = [
    "GuideMappingError",
    "LOCKED_GUIDE_MAPPING_PARAMETERS",
    "GuideMappingProfile",
    "GuideMappingRequest",
    "GuideMappingResult",
    "map_recognized_teeth_to_guide",
]

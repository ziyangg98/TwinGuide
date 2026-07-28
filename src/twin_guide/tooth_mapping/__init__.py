"""内部算法说明。\n\nCase-independent FDI tooth-to-guide mapping.

The package deliberately separates clinical semantics (confirmed FDI order)
from geometric evidence (dental and guide meshes).  Geometry may refine one
centre per configured slot, but it is never allowed to create a new tooth
number from a cusp or a projected surface island.
"""

from .controlled_mesh_repair import (
    ControlledVolumeRepairError,
    ControlledVolumeRepairPolicy,
    ControlledVolumeRepairResult,
    ensure_closed_volume,
)
from .fdi import FDIError, derive_fdi_order, validate_anatomy
from .pipeline import run_case_mapping

__all__ = [
    "ControlledVolumeRepairError",
    "ControlledVolumeRepairPolicy",
    "ControlledVolumeRepairResult",
    "FDIError",
    "derive_fdi_order",
    "ensure_closed_volume",
    "run_case_mapping",
    "validate_anatomy",
]

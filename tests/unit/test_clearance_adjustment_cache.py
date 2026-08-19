import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from twin_guide.clearance_adjustment import _cached_plan


class ClearanceAdjustmentCacheTests(unittest.TestCase):
    def test_preview_trusts_internally_validated_cached_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            envelope = root / "envelope.ply"
            report = root / "report.json"
            envelope.write_bytes(b"cached")
            report.write_text(
                json.dumps(
                    {
                        "fingerprint": {"geometry": "current"},
                        "motion_model": {
                            "rotation_axis": [0.0, 0.0, 1.0],
                            "pivot_global_mm": [1.0, 2.0, 3.0],
                            "matched_stop_patch_ids": ["left", "right"],
                            "angle_samples_degrees": [-5.0, 0.0, 5.0],
                            "axial_depth_samples_mm": [0.0],
                            "automatic_interpolation_clearance_mm": 0.1,
                        },
                        "envelope": {"is_closed_volume": True},
                    }
                ),
                encoding="utf-8",
            )

            with patch("twin_guide.clearance_adjustment._load_mesh") as load_mesh:
                cached = _cached_plan(
                    envelope,
                    report,
                    {"geometry": "current"},
                    "phone",
                    0.0,
                    validate_mesh=False,
                )

            self.assertIsNotNone(cached)
            assert cached is not None
            self.assertTrue(cached.cache_reused)
            self.assertAlmostEqual(cached.effective_clearance_mm, 0.1)
            load_mesh.assert_not_called()


if __name__ == "__main__":
    unittest.main()

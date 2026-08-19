"""第二阶段正式计算与诊断输出边界测试。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import trimesh

from twin_guide.tooth_mapping import tooth_recognition
from twin_guide.tooth_mapping.contact_guide_mapping import ContactToothLocation
from twin_guide.tooth_mapping.guide_mapping import (
    GuideMappingProfile,
    GuideMappingResult,
)
from twin_guide.tooth_mapping.map_contact_chord_teeth_to_guide import (
    _preview_instance_records,
)
from twin_guide.tooth_mapping.pipeline import _core
from twin_guide.tooth_mapping.tooth_recognition import (
    ToothRecognitionRequest,
    recognize_teeth,
)


class ToothMappingWorkflowTests(unittest.TestCase):
    """诊断渲染不得成为正式牙位计算的必需步骤。"""

    def test_recognition_disables_diagnostics_by_default(self) -> None:
        """正式识别把同一个诊断策略传给三个内部计算步骤。"""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            case_yaml = root / "case.yaml"
            case_yaml.write_text("case: {id: test}\n", encoding="utf-8")
            base_report = {
                "status": "tooth_guide_mapping_complete",
                "outputs": {"report_json": str(root / "base.json")},
            }
            projection_report = {
                "height_floor_selection": {"selection_succeeded": True},
                "outputs": {
                    "arrays": str(root / "projection.npz"),
                    "report": str(root / "projection.json"),
                },
            }
            contour_report = {
                "status": "complete",
                "safe_for_downstream_use": True,
                "QA": {"approved": True},
                "outputs": {"report": str(root / "contours.json")},
            }
            with (
                patch.object(
                    tooth_recognition,
                    "run_case_mapping",
                    return_value=base_report,
                ) as base,
                patch.object(
                    tooth_recognition,
                    "_render_projection",
                    return_value=projection_report,
                ) as projection,
                patch.object(
                    tooth_recognition,
                    "_extract_contours",
                    return_value=contour_report,
                ) as contours,
            ):
                recognize_teeth(ToothRecognitionRequest(case_yaml, root / "output"))

            self.assertFalse(base.call_args.kwargs["write_diagnostics"])
            self.assertFalse(projection.call_args.args[0].write_diagnostics)
            self.assertFalse(contours.call_args.args[0].write_diagnostics)

    def test_guide_manifest_allows_absent_diagnostic_artifacts(self) -> None:
        """权威报告存在时，清单不要求预览图和 GLB。"""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            result = GuideMappingResult(
                case_yaml=root / "case.yaml",
                output_dir=root,
                profile=GuideMappingProfile(),
                created_at=datetime.now(UTC).isoformat(),
                recognition_manifest_path=root / "recognition.json",
                mapping_report={
                    "status": "tooth_guide_mapping_complete",
                    "QA": {"approved": True},
                    "outputs": {"report_json": str(root / "mapping.json")},
                },
                manifest_path=root / "guide_mapping_result.json",
            )

            outputs = result.manifest()["outputs"]
            self.assertNotIn("preview_png", outputs)
            self.assertNotIn("context_glb", outputs)

    def test_mesh_loader_reuses_unchanged_mesh(self) -> None:
        """同一运行中重复读取相同 STL 时只解析一次。"""

        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "mesh.stl"
            path.write_bytes(b"test")
            mesh = trimesh.creation.box()
            _core._load_mesh_cached.cache_clear()
            with patch.object(_core.trimesh, "load", return_value=mesh) as loader:
                first = _core.load_mesh(path)
                second = _core.load_mesh(path)

            self.assertIs(first, second)
            loader.assert_called_once()
            _core._load_mesh_cached.cache_clear()

    def test_preview_instances_include_shared_lr_ap_geometry(self) -> None:
        """fdi_new 预览记录必须满足公共渲染器的字段契约。"""

        locations = [
            ContactToothLocation(
                fdi=14,
                centroid_lr_ap_mm=(1.5, 2.5),
                arch_s_mm=3.0,
                arch_lr_ap_mm=(1.25, 2.0),
                contour_interval_s_mm=(2.0, 4.0),
                crown_height_mm=8.0,
                crown_point_global_mm=(1.5, 2.5, 8.0),
                lift_method="authoritative_v2_component_crown_point",
                lift_distance_mm=0.0,
            )
        ]
        contour_records = [
            {
                "FDI": 14,
                "contour_LR_AP_mm": [[1.0, 2.0], [2.0, 2.0], [1.5, 3.0]],
            }
        ]
        frame = {
            "curve": SimpleNamespace(
                lr=np.asarray([1.0, 2.0]),
                ap=np.asarray([2.0, 2.0]),
                s=np.asarray([2.0, 4.0]),
            )
        }

        records = _preview_instance_records(locations, contour_records, frame)

        self.assertEqual(records[0]["area_centroid_lr_ap_mm"], [1.5, 2.5])
        self.assertEqual(
            records[0]["contour_lr_ap_mm"],
            contour_records[0]["contour_LR_AP_mm"],
        )
        self.assertEqual(records[0]["crown_point_global_mm"], [1.5, 2.5, 8.0])

    def test_preview_contract_rejects_missing_contour(self) -> None:
        location = ContactToothLocation(
            fdi=14,
            centroid_lr_ap_mm=(1.5, 2.5),
            arch_s_mm=3.0,
            arch_lr_ap_mm=(1.25, 2.0),
            contour_interval_s_mm=(2.0, 4.0),
            crown_height_mm=8.0,
            crown_point_global_mm=(1.5, 2.5, 8.0),
            lift_method="test",
            lift_distance_mm=0.0,
        )
        frame = {
            "curve": SimpleNamespace(
                lr=np.asarray([1.0, 2.0]),
                ap=np.asarray([2.0, 2.0]),
                s=np.asarray([2.0, 4.0]),
            )
        }

        with self.assertRaisesRegex(RuntimeError, "FDI 14.*牙冠轮廓"):
            _preview_instance_records([location], [], frame)


if __name__ == "__main__":
    unittest.main()

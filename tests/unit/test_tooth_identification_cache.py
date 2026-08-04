import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from twin_guide.tooth_identification import (
    STAGE_RESULT_SCHEMA,
    WORKFLOW_CACHE_NAME,
    WORKFLOW_CACHE_RESULT_NAME,
    _input_fingerprint,
    _load_current_result,
)


class ToothIdentificationCacheTests(unittest.TestCase):
    def test_fingerprint_ignores_temporary_yaml_path_and_editor_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_case = root / "first.yaml"
            second_case = root / "second.yaml"
            first_case.write_text(
                "case: {id: demo}\neditor_overrides: {press_junction_mm: [1, 2, 3]}\n",
                encoding="utf-8",
            )
            second_case.write_text(
                "case: {id: demo}\neditor_overrides: {press_junction_mm: [2, 2, 3]}\n",
                encoding="utf-8",
            )
            dental = root / "dental.stl"
            template = root / "template.stl"
            sleeve = root / "sleeve.stl"
            for path in (dental, template, sleeve):
                path.write_bytes(b"mesh")

            def config(case_yaml):
                return SimpleNamespace(
                    case_id="demo",
                    jaw=SimpleNamespace(value="lower"),
                    tooth_identification=SimpleNamespace(case_yaml=case_yaml),
                    inputs=SimpleNamespace(
                        patient_dentition=dental,
                        template=template,
                        guide_sleeve_assemblies=(sleeve,),
                    ),
                )

            self.assertEqual(
                _input_fingerprint(config(first_case)),
                _input_fingerprint(config(second_case)),
            )

    def test_cached_result_does_not_depend_on_stage_documents(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            cache = output / ".cache" / WORKFLOW_CACHE_NAME
            for name in (
                "tooth-recognition/guide-surface-mapping",
                "tooth-recognition/crown-projection",
                "tooth-recognition/contact-contours",
                "guide-mapping",
            ):
                (cache / name).mkdir(parents=True)
            result_path = cache / WORKFLOW_CACHE_RESULT_NAME
            result_path.write_text(
                json.dumps(
                    {
                        "schema_version": STAGE_RESULT_SCHEMA,
                        "inputs": {"fingerprint": {"case": "current"}},
                    }
                ),
                encoding="utf-8",
            )
            sentinel = object()
            config = SimpleNamespace(output_directory=output)

            with patch(
                "twin_guide.tooth_identification._input_fingerprint",
                return_value={"case": "current"},
            ), patch(
                "twin_guide.tooth_identification._validated_result",
                return_value=sentinel,
            ):
                result = _load_current_result(config, write_overview=False)

            self.assertIs(result, sentinel)
            self.assertFalse((output / "stage-02-tooth-mapping.json").exists())


if __name__ == "__main__":
    unittest.main()

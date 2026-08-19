"""有效病例配置产物测试。"""

import hashlib
import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from twin_guide.config import ToothIdentificationBackend, ToothIdentificationInputs
from twin_guide.effective_case import EFFECTIVE_CASE_SCHEMA, write_effective_case


@dataclass(frozen=True)
class _Config:
    output_directory: Path
    tooth_identification: ToothIdentificationInputs
    default_value: float = 5.0


class EffectiveCaseTests(unittest.TestCase):
    def test_writes_source_hash_normalized_definition_and_resolved_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "case.yaml"
            source.write_text(
                "schema_version: '1.0'\ncase: {id: test}\ndesign: {}\n",
                encoding="utf-8",
            )
            config = _Config(
                output_directory=root / "output",
                tooth_identification=ToothIdentificationInputs(
                    source, ToothIdentificationBackend.FDI_NEW
                ),
            )

            path = write_effective_case(config)  # type: ignore[arg-type]
            report = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual(report["schema_version"], EFFECTIVE_CASE_SCHEMA)
            self.assertEqual(report["tooth_identification_backend"], "fdi_new")
            self.assertEqual(
                report["source"]["sha256"], hashlib.sha256(source.read_bytes()).hexdigest()
            )
            self.assertEqual(report["resolved_config"]["default_value"], 5.0)


if __name__ == "__main__":
    unittest.main()

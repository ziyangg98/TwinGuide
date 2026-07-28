import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from twin_guide.config import Jaw
from twin_guide.geometry import Vec3
from twin_guide.stage_artifacts import (
    STAGE_ARTIFACT_STEMS,
    STAGE_RESULT_SCHEMA,
    write_stage_result_documents,
)
from twin_guide.types import (
    GenerationContext,
    GenerationProcessResult,
    StageDefinition,
    StageMaturity,
    StageResult,
    StageRunStatus,
)


@dataclass(frozen=True)
class ExampleOutput:
    sleeves: tuple["ExampleSleeve", ...]
    point: Vec3
    path: Path


@dataclass(frozen=True)
class ExampleSleeve:
    guide_index: int
    length_mm: float
    bore_radius_mm: float


@dataclass(frozen=True)
class ExampleSleeveParameters:
    height_mm: float


@dataclass(frozen=True)
class ExampleInputs:
    template: Path
    guide_sleeve_assemblies: tuple[Path, ...]
    patient_dentition: Path


class StageArtifactTests(unittest.TestCase):
    def test_all_stage_stems_are_flat_and_unique(self):
        self.assertEqual(tuple(STAGE_ARTIFACT_STEMS), tuple(range(1, 8)))
        self.assertEqual(len(set(STAGE_ARTIFACT_STEMS.values())), 7)
        self.assertTrue(
            all("/" not in stem for stem in STAGE_ARTIFACT_STEMS.values())
        )

    def test_completed_and_skipped_stages_share_one_document_shape(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_directory = Path(temporary_directory)
            config = SimpleNamespace(
                output_directory=output_directory,
                case_id="fixture",
                jaw=Jaw.UPPER,
                inputs=ExampleInputs(
                    output_directory / "template.stl",
                    (output_directory / "sleeves.stl",),
                    output_directory / "dentition.stl",
                ),
                sleeve=ExampleSleeveParameters(20.0),
            )
            definition_1 = StageDefinition(
                1,
                "sleeve_generation",
                "导管识别与标准重建",
                StageMaturity.STABLE,
                "1.0",
                ("source_meshes",),
                "sleeve_generation",
            )
            definition_2 = StageDefinition(
                2,
                "tooth_identification",
                "牙位识别",
                StageMaturity.EXPERIMENTAL,
                "0.4",
                ("source_meshes",),
                "tooth_identification",
            )
            process = GenerationProcessResult(
                GenerationContext(config=config),
                (
                    StageResult(
                        definition_1,
                        StageRunStatus.COMPLETED,
                        ExampleOutput(
                            (ExampleSleeve(1, 20.0, 0.45),),
                            Vec3(1.0, 2.0, 3.0),
                            output_directory / "x.stl",
                        ),
                    ),
                    StageResult(
                        definition_2,
                        StageRunStatus.SKIPPED,
                        reason="not configured",
                    ),
                ),
            )

            paths = write_stage_result_documents(process)

            self.assertEqual(len(paths), 2)
            documents = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
            expected_keys = [
                "schema_version",
                "stage",
                "case",
                "inputs",
                "parameters",
                "result",
                "quality",
                "artifacts",
            ]
            self.assertTrue(all(list(document) == expected_keys for document in documents))
            self.assertTrue(all(document["schema_version"] == STAGE_RESULT_SCHEMA for document in documents))
            self.assertEqual(documents[0]["result"]["point"], {"x": 1.0, "y": 2.0, "z": 3.0})
            self.assertIsNone(documents[1]["result"])
            self.assertIsNone(documents[1]["quality"]["passed"])
            self.assertIsNone(documents[1]["artifacts"]["overview_png"])


if __name__ == "__main__":
    unittest.main()

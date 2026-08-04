import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from twin_guide.guide_generation import _generate_guide_with_process, generate_guide


class GuideGenerationTests(unittest.TestCase):
    def test_preview_keeps_full_geometry_plan(self):
        config = object()
        case = object()
        cutouts = object()
        links = object()
        avoidance = object()
        context = SimpleNamespace(
            case=case,
            window_cutouts=cutouts,
            point_linking=links,
            clearance_adjustment=avoidance,
        )
        process = SimpleNamespace(context=context)
        artifacts = Mock()

        with patch(
            "twin_guide.guide_generation.run_generation_process",
            return_value=process,
        ) as run_process, patch(
            "twin_guide.guide_generation.build_guide_from_links",
            return_value=artifacts,
        ) as build, patch(
            "twin_guide.guide_generation.compose_stage_overviews",
        ) as compose:
            result, returned_process = _generate_guide_with_process(
                config,
                preview=True,
            )

        self.assertIs(result, artifacts)
        self.assertIs(returned_process, process)
        run_process.assert_called_once_with(
            config,
            require_observation_qa=False,
            write_stage_documents=False,
            include_clearance_adjustment=True,
            validate_cached_geometry=False,
            force_rebuild=False,
            changed_feature_ids=(),
        )
        build.assert_called_once_with(
            case,
            cutouts,
            links,
            avoidance,
            preview=True,
            force_rebuild=False,
        )
        compose.assert_not_called()

    def test_formal_generation_reuses_matching_complete_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            model = output / "twin_guide.stl"
            image = output / "guide_iso.png"
            model.write_text("model", encoding="utf-8")
            image.write_text("image", encoding="utf-8")
            cache = output / ".cache" / "generation-result.json"
            cache.parent.mkdir()
            cache.write_text(
                json.dumps(
                    {
                        "fingerprint": "formal-test",
                        "model_path": str(model),
                        "image_paths": [str(image)],
                    }
                ),
                encoding="utf-8",
            )
            config = SimpleNamespace(output_directory=output)

            with patch(
                "twin_guide.guide_generation._formal_fingerprint",
                return_value="formal-test",
            ), patch(
                "twin_guide.guide_generation._generate_guide_with_process"
            ) as generate:
                artifacts = generate_guide(config)

            generate.assert_not_called()
            self.assertEqual(artifacts.model_path, model)
            self.assertEqual(artifacts.image_paths, (image,))

    def test_force_rebuild_bypasses_formal_artifact_cache(self):
        config = SimpleNamespace(output_directory=Path("/tmp/formal"))
        artifacts = Mock()
        process = Mock()

        with patch(
            "twin_guide.guide_generation._cached_formal_artifacts"
        ) as cached, patch(
            "twin_guide.guide_generation._generate_guide_with_process",
            return_value=(artifacts, process),
        ) as generate, patch(
            "twin_guide.guide_generation._write_formal_artifacts_cache"
        ) as write_cache:
            result = generate_guide(config, force_rebuild=True)

        self.assertIs(result, artifacts)
        cached.assert_not_called()
        generate.assert_called_once_with(
            config,
            preview=False,
            force_rebuild=True,
        )
        write_cache.assert_called_once_with(config, artifacts)


if __name__ == "__main__":
    unittest.main()

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from twin_guide.guide_generation import _generate_guide_with_process


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
        )
        build.assert_called_once_with(
            case,
            cutouts,
            links,
            avoidance,
            preview=True,
        )
        compose.assert_not_called()


if __name__ == "__main__":
    unittest.main()

"""稳定公开接口与生成/验证边界测试。"""

from __future__ import annotations

import unittest
from pathlib import Path

import twin_guide
from twin_guide.config import CaseConfig
from twin_guide.types import (
    GenerationContext,
    GenerationProcessResult,
    StageDefinition,
    StageMaturity,
    StageResult,
    StageRunStatus,
)


class PublicApiTests(unittest.TestCase):
    """检查 0.3 版顶层 API 和类型化阶段约束。"""

    def test_top_level_exports_only_stable_api(self) -> None:
        """顶层 ``__all__`` 不再暴露锚点或内部参数类型。"""

        self.assertEqual(
            {
                "BuildArtifacts",
                "CaseConfig",
                "GenerationProcessResult",
                "StageResult",
                "ValidationResult",
                "generate_guide",
                "run_generation_process",
                "validate_guide",
            },
            set(twin_guide.__all__),
        )
        self.assertNotIn("GuideAnchorParameters", twin_guide.__all__)
        self.assertNotIn("PointLinkingPlan", twin_guide.__all__)

    def test_case_config_has_yaml_only_constructor(self) -> None:
        """0.3 版不保留 JSON 构造器。"""

        self.assertTrue(callable(CaseConfig.from_yaml))
        self.assertFalse(hasattr(CaseConfig, "from_json"))

    def test_completed_stage_requires_typed_output(self) -> None:
        """完成阶段必须携带输出。"""

        definition = StageDefinition(
            1,
            "example",
            "示例",
            StageMaturity.EXPERIMENTAL,
            "0.1",
            (),
            "example_output",
        )
        with self.assertRaisesRegex(ValueError, "必须提供输出"):
            StageResult(definition, StageRunStatus.COMPLETED)

    def test_skipped_stage_requires_reason(self) -> None:
        """跳过阶段必须记录原因。"""

        definition = StageDefinition(
            2,
            "example",
            "示例",
            StageMaturity.EXPERIMENTAL,
            "0.1",
            (),
            "example_output",
        )
        with self.assertRaisesRegex(ValueError, "必须提供原因"):
            StageResult(definition, StageRunStatus.SKIPPED)

    def test_process_result_indexes_completed_outputs(self) -> None:
        """流程结果按阶段声明的输出键索引计划。"""

        definition = StageDefinition(
            1,
            "example",
            "示例",
            StageMaturity.STABLE,
            "1.0",
            (),
            "example_output",
        )
        output = object()
        result = GenerationProcessResult(
            GenerationContext(config=object()),  # type: ignore[arg-type]
            (StageResult(definition, StageRunStatus.COMPLETED, output),),
        )
        self.assertIs(result.stage(1).output, output)
        self.assertIs(result.completed_outputs["example_output"], output)

    def test_validation_consumes_shared_generation_plan(self) -> None:
        """验证层消费公开规划结果，不复制阶段选择器。"""

        source = (
            Path(__file__).resolve().parents[2] / "src/twin_guide/guide_validation.py"
        ).read_text(encoding="utf-8")
        self.assertIn("process = run_generation_process(config)", source)
        for duplicated_selector in (
            "select_template_link_points(",
            "select_press_beam_points(",
            "link_selected_points(",
            "plan_window_cutouts(",
        ):
            self.assertNotIn(duplicated_selector, source)


if __name__ == "__main__":
    unittest.main()

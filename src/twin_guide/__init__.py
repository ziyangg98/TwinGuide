"""双导套牙科导板的三维构建接口。"""

from pathlib import Path

from twin_guide.config import CaseConfig
from twin_guide.models import BuildArtifacts, ValidationResult
from twin_guide.point_linking import (
    PointLink,
    PointLinkingConfig,
    PointLinkingPlan,
    link_selected_points,
)
from twin_guide.sleeve_anchors import (
    SleeveAnchorPlan,
    SleeveAnchorPoint,
    SleeveAnchorSelection,
    select_sleeve_anchors,
)
from twin_guide.template_anchors import (
    TemplateAnchorPoint,
    TemplatePointPlan,
    TemplatePointSelection,
    TemplatePointSelectionConfig,
    select_template_points,
)
from twin_guide.template_link_points import (
    TemplateLinkPointContext,
    TemplateLinkPointPlan,
    select_template_link_points,
)
from twin_guide.types import (
    GenerationContext,
    GenerationProcessResult,
    SleeveGenerationResult,
    StageMaturity,
    StageResult,
    StageRunStatus,
)
from twin_guide.window_cutouts import (
    WindowCutoutPlan,
    plan_window_cutouts,
)

__all__ = [
    "BuildArtifacts",
    "CaseConfig",
    "GenerationContext",
    "GenerationProcessResult",
    "PointLink",
    "PointLinkingConfig",
    "PointLinkingPlan",
    "SleeveAnchorPlan",
    "SleeveAnchorPoint",
    "SleeveAnchorSelection",
    "SleeveGenerationInputs",
    "SleeveGenerationResult",
    "StageMaturity",
    "StageResult",
    "StageRunStatus",
    "TemplateAnchorPoint",
    "TemplateLinkPointContext",
    "TemplateLinkPointPlan",
    "TemplatePointPlan",
    "TemplatePointSelection",
    "TemplatePointSelectionConfig",
    "ValidationResult",
    "WindowCutoutPlan",
    "generate_guide",
    "link_selected_points",
    "plan_window_cutouts",
    "recognize_and_build_sleeves",
    "run_generation_process",
    "select_sleeve_anchors",
    "select_template_link_points",
    "select_template_points",
    "validate_guide",
]

__version__ = "0.2.0"


def __getattr__(name: str) -> object:
    """延迟加载需要 Blender 的第 1 步接口。"""

    if name in {"SleeveGenerationInputs", "recognize_and_build_sleeves"}:
        from twin_guide import sleeve_generation

        return getattr(sleeve_generation, name)
    raise AttributeError(name)


def generate_guide(config: CaseConfig) -> BuildArtifacts:
    """在 Blender 中构建并导出牙科导板。

    参数:
        config: 已通过校验的病例配置。

    返回:
        导出的 STL 和诊断图路径。
    """

    from twin_guide.guide_generation import generate_guide as run_pipeline

    return run_pipeline(config)


def validate_guide(model_path: str | Path, config: CaseConfig) -> tuple[ValidationResult, ...]:
    """在 Blender 中检查已导出的牙科导板 STL。

    参数:
        model_path: 待检查的 STL 路径。
        config: 已通过校验的病例配置。

    返回:
        拓扑、导套保留、连接管、导孔、窗口和手机净距检查结果。
    """

    from twin_guide.guide_validation import validate_guide as run_validation

    return run_validation(Path(model_path), config)


def run_generation_process(config: CaseConfig) -> GenerationProcessResult:
    """在 Blender 中按顺序运行已实现的构建阶段。

    参数:
        config: 已通过校验的病例配置。

    返回:
        各阶段的执行状态、输出和共享上下文。
    """

    from twin_guide.generation_process import run_generation_process as execute

    return execute(config)

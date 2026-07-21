"""双导套牙科导板的三维构建接口。"""

from pathlib import Path

from twin_guide.clearance_adjustment import adjust_clearance
from twin_guide.config import CaseConfig, Jaw, SleeveParameters
from twin_guide.models import BuildArtifacts, ValidationResult
from twin_guide.point_linking import (
    PointLink,
    PointLinkingConfig,
    PointLinkingPlan,
    link_selected_points,
)
from twin_guide.press_beam_points import select_press_beam_points
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
from twin_guide.tooth_identification import identify_tooth_positions
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
    "Jaw",
    "PointLink",
    "PointLinkingConfig",
    "PointLinkingPlan",
    "SleeveAnchorPlan",
    "SleeveAnchorPoint",
    "SleeveAnchorSelection",
    "SleeveGenerationInputs",
    "SleeveGenerationResult",
    "SleeveParameters",
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
    "adjust_clearance",
    "generate_guide",
    "identify_tooth_positions",
    "link_selected_points",
    "plan_window_cutouts",
    "recognize_and_build_sleeves",
    "run_generation_process",
    "select_press_beam_points",
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

    异常:
        TwinGuideError: 病例读取、几何计算、Blender 建模或导出失败。

    算法说明:
        分析三个病例网格，依次计算导套、切口、联建选点和曲线连接，
        再调用 Blender 完成实体化、布尔运算、固定孔复切、网格清理和 STL 导出。
    """

    from twin_guide.guide_generation import generate_guide as run_pipeline

    return run_pipeline(config)


def validate_guide(model_path: str | Path, config: CaseConfig) -> tuple[ValidationResult, ...]:
    """在 Blender 中检查已导出的牙科导板 STL。

    参数:
        model_path: 待检查的 STL 路径。
        config: 已通过校验的病例配置。

    返回:
        拓扑、导套保留、连接管、导孔和窗口检查结果。

    异常:
        TwinGuideError: STL 读取、病例分析或几何检查失败。

    算法说明:
        读取导出 STL 和病例基准，分别计算网格拓扑、导套保留、连接管、
        导孔和窗口指标，每项检查返回独立的 ``ValidationResult``。
        牙科手机净距属于待实现的第 7 步，当前不执行。
    """

    from twin_guide.guide_validation import validate_guide as run_validation

    return run_validation(Path(model_path), config)


def run_generation_process(config: CaseConfig) -> GenerationProcessResult:
    """在 Blender 中按顺序运行已实现的构建阶段。

    参数:
        config: 已通过校验的病例配置。

    返回:
        各阶段的执行状态、输出和共享上下文。

    异常:
        TwinGuideError: 病例读取或任一已执行几何步骤失败。

    算法说明:
        分析病例后依次执行第 1、3、4、6 步，将输出写入 ``GenerationContext``；
        第 2、5、7 步记录为 ``skipped``。每一步均生成一个 ``StageResult``。
    """

    from twin_guide.generation_process import run_generation_process as execute

    return execute(config)

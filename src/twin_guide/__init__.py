"""TwinGuide 的稳定公开接口。"""

from pathlib import Path

from twin_guide.config import CaseConfig
from twin_guide.models import BuildArtifacts, ValidationResult
from twin_guide.types import GenerationProcessResult, StageResult

__all__ = [
    "BuildArtifacts",
    "CaseConfig",
    "GenerationProcessResult",
    "StageResult",
    "ValidationResult",
    "generate_guide",
    "run_generation_process",
    "validate_guide",
]

__version__ = "0.3.0"


def generate_guide(config: CaseConfig, *, force_rebuild: bool = False) -> BuildArtifacts:
    """构建并导出一体化牙科导板。

    参数:
        config: 已加载并通过业务校验的病例配置。

    返回:
        STL 模型和过程图像路径。
    """

    from twin_guide.guide_generation import generate_guide as execute

    return execute(config, force_rebuild=force_rebuild)


def run_generation_process(
    config: CaseConfig,
    *,
    force_rebuild: bool = False,
) -> GenerationProcessResult:
    """执行七阶段几何规划并返回类型化阶段结果。

    参数:
        config: 已加载并通过业务校验的病例配置。

    返回:
        包含七个阶段状态与类型化计划的流程结果。
    """

    from twin_guide.generation_process import run_generation_process as execute

    return execute(config, force_rebuild=force_rebuild)


def validate_guide(
    model_path: str | Path,
    config: CaseConfig,
) -> tuple[ValidationResult, ...]:
    """独立检查已有牙科导板 STL，不修改输入模型。

    参数:
        model_path: 待检查的 STL 路径。
        config: 用于重建验证计划和阈值的病例配置。

    返回:
        独立检查项及其通过状态。
    """

    from twin_guide.guide_validation import validate_guide as execute

    return execute(Path(model_path), config)

"""根据病例配置生成导管—导板联建结构 STL。"""

from __future__ import annotations

from twin_guide.blender.guide_modeling import build_guide_from_links
from twin_guide.config import CaseConfig
from twin_guide.generation_process import run_generation_process
from twin_guide.models import BuildArtifacts
from twin_guide.stage_artifacts import compose_stage_overviews
from twin_guide.types import GenerationProcessResult


def _generate_guide_with_process(
    config: CaseConfig,
    *,
    preview: bool = False,
) -> tuple[BuildArtifacts, GenerationProcessResult]:
    """运行一次规划与实体化；预览仅省略 QA、文档和渲染产物。"""

    process = run_generation_process(
        config,
        require_observation_qa=not preview,
        write_stage_documents=not preview,
        include_clearance_adjustment=True,
    )
    context = process.context
    if context.case is None or context.window_cutouts is None or context.point_linking is None:
        raise RuntimeError("生成阶段未产生完整建模上下文")
    artifacts = build_guide_from_links(
        context.case,
        context.window_cutouts,
        context.point_linking,
        context.clearance_adjustment,
        preview=preview,
    )
    if not preview:
        compose_stage_overviews(process)
    return artifacts, process


def generate_guide(config: CaseConfig, *, preview: bool = False) -> BuildArtifacts:
    """生成包含双导管、窗口和连续曲线梁架的牙科导板。

    参数:
        config: 已通过校验的病例配置。

    返回:
        最终 STL 路径和过程图路径。

    算法说明:
        统一调用七阶段生成编排器，因此配置的牙位报告、FDI 观察窗、
        选点和曲线连接与 ``process`` 命令使用同一份上下文。
        几何生成必须在 Blender 提供的 Python 环境中运行。
    """

    artifacts, _process = _generate_guide_with_process(config, preview=preview)
    return artifacts


__all__ = ["generate_guide"]

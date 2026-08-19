"""根据病例配置生成导管—导板联建结构 STL。"""

from __future__ import annotations

import json
from pathlib import Path

from twin_guide.config import CaseConfig
from twin_guide.effective_case import write_effective_case
from twin_guide.generation_process import run_generation_process
from twin_guide.models import BuildArtifacts
from twin_guide.stage_artifacts import compose_stage_overviews
from twin_guide.types import GenerationProcessResult
from twin_guide.ui_jobs import write_manifest

FORMAL_CACHE_VERSION = "formal-generation-v1"


def build_guide_from_links(*args: object, **kwargs: object) -> BuildArtifacts:
    """延迟加载 Blender 实体化代码，使缓存与编排测试可独立运行。"""

    from twin_guide.blender.guide_modeling import build_guide_from_links as build

    return build(*args, **kwargs)


def _formal_fingerprint(config: CaseConfig) -> str:
    """返回正式生成产物使用的语义指纹。"""

    from twin_guide.editor_plan import editor_geometry_fingerprint

    config_path = (
        None if config.tooth_identification is None else config.tooth_identification.case_yaml
    )
    return f"{FORMAL_CACHE_VERSION}:{editor_geometry_fingerprint(config, config_path)}"


def _cached_formal_artifacts(config: CaseConfig) -> BuildArtifacts | None:
    """读取当前配置对应且文件完整的正式生成结果。"""

    manifest_path = config.output_directory / ".cache" / "generation-result.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if manifest.get("fingerprint") != _formal_fingerprint(config):
        return None
    model_path = Path(str(manifest.get("model_path", "")))
    image_paths = tuple(Path(str(path)) for path in manifest.get("image_paths", []))
    if not model_path.is_file() or not all(path.is_file() for path in image_paths):
        return None
    return BuildArtifacts(model_path, image_paths)


def _write_formal_artifacts_cache(
    config: CaseConfig,
    artifacts: BuildArtifacts,
) -> None:
    """原子记录一次完整正式生成的产物路径。"""

    write_manifest(
        config.output_directory / ".cache" / "generation-result.json",
        {
            "fingerprint": _formal_fingerprint(config),
            "model_path": str(artifacts.model_path),
            "image_paths": [str(path) for path in artifacts.image_paths],
        },
    )


def _generate_guide_with_process(
    config: CaseConfig,
    *,
    preview: bool = False,
    force_rebuild: bool = False,
    changed_feature_ids: tuple[str, ...] = (),
) -> tuple[BuildArtifacts, GenerationProcessResult]:
    """运行一次规划与实体化；预览仅省略 QA、文档和渲染产物。"""

    process = run_generation_process(
        config,
        require_observation_qa=not preview,
        write_stage_documents=not preview,
        include_clearance_adjustment=True,
        validate_cached_geometry=not preview,
        force_rebuild=force_rebuild,
        changed_feature_ids=changed_feature_ids,
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
        force_rebuild=force_rebuild,
    )
    if not preview:
        compose_stage_overviews(process)
    return artifacts, process


def generate_guide(
    config: CaseConfig,
    *,
    preview: bool = False,
    force_rebuild: bool = False,
) -> BuildArtifacts:
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

    write_effective_case(config)
    if not preview and not force_rebuild:
        cached = _cached_formal_artifacts(config)
        if cached is not None:
            return cached
    artifacts, _process = _generate_guide_with_process(
        config,
        preview=preview,
        force_rebuild=force_rebuild,
    )
    if not preview:
        _write_formal_artifacts_cache(config, artifacts)
    return artifacts


__all__ = ["generate_guide"]

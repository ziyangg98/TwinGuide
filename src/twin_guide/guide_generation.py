"""根据病例配置生成导套—导板联建结构 STL。"""

from __future__ import annotations

from twin_guide.blender.guide_modeling import build_guide_from_links
from twin_guide.case_analysis import analyze_case
from twin_guide.config import CaseConfig
from twin_guide.models import BuildArtifacts
from twin_guide.point_linking import PointLinkingConfig, link_selected_points
from twin_guide.template_anchors import TemplatePointSelectionConfig
from twin_guide.template_link_points import (
    TemplateLinkPointContext,
    select_template_link_points,
)
from twin_guide.types import SleeveGenerationResult
from twin_guide.window_cutouts import plan_window_cutouts


def generate_guide(config: CaseConfig) -> BuildArtifacts:
    """生成包含双导套、窗口和曲线连接管的牙科导板。

    参数:
        config: 已通过校验的病例配置。

    返回:
        最终 STL 路径和过程图路径。

    算法说明:
        程序先分析病例并规划第 3 步切口，再将导套分析封装为第 1 步输出，
        依次调用第 4 步选点、第 6 步曲线规划和 Blender 建模导出。
        牙科导板选点和曲线连接均使用病例配置中的
        ``connector_diameter_mm``。几何生成必须在 Blender 提供的 Python 环境中运行。
    """

    case = analyze_case(config)
    sleeves = SleeveGenerationResult(case.guide_sleeves, case.template_frame)
    cutout_plan = plan_window_cutouts(case, sleeves)
    points = select_template_link_points(
        TemplateLinkPointContext(case, sleeves, cutout_plan),
        TemplatePointSelectionConfig(connector_radius_mm=config.geometry.connector_radius_mm),
    )
    links = link_selected_points(
        points,
        PointLinkingConfig(radius_mm=config.geometry.connector_radius_mm),
    )
    return build_guide_from_links(case, cutout_plan, links)

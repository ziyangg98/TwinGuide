"""第 6 步：用光滑曲线连接导套锚点和牙科导板锚点。"""

from __future__ import annotations

from dataclasses import dataclass

from twin_guide.geometry import Vec3
from twin_guide.template_link_points import TemplateLinkPointPlan


@dataclass(frozen=True, slots=True)
class PointLinkingConfig:
    """第 6 步的曲线与实体化参数。

    属性:
        radius_mm: 连接管半径，单位毫米。
        handle_factor: 贝塞尔控制柄长度相对于半径的上限倍数。
        curve_resolution: Blender 曲线轴向细分级别。
        recut_sleeve_bore: 实体化后是否复切导套固定孔。
    """

    radius_mm: float
    handle_factor: float = 3.0
    curve_resolution: int = 24
    recut_sleeve_bore: bool = True

    def __post_init__(self) -> None:
        """校验连接管半径、控制柄系数和曲线分辨率。"""

        if self.radius_mm <= 0.0 or self.handle_factor <= 0.0:
            raise ValueError("连接管半径和贝塞尔控制柄系数必须为正")
        if self.curve_resolution <= 0:
            raise ValueError("曲线细分级别必须为正整数")


@dataclass(frozen=True, slots=True)
class PointLink:
    """一条导套锚点到牙科导板锚点的三次贝塞尔连接。

    属性:
        guide_index: 所属导套编号。
        sleeve_label: 导套端标签，为 ``lower`` 或 ``upper``。
        template_label: 牙科导板端标签，为 ``left`` 或 ``right``。
        start: 导套端点。
        end: 牙科导板端点。
        control_points: 三次贝塞尔曲线的四个控制点。
        centerline: 供验证和诊断使用的离散中心线。
    """

    guide_index: int
    sleeve_label: str
    template_label: str
    start: Vec3
    end: Vec3
    control_points: tuple[Vec3, Vec3, Vec3, Vec3]
    centerline: tuple[Vec3, ...]


@dataclass(frozen=True, slots=True)
class PointLinkingPlan:
    """第 6 步输出。

    属性:
        links: 每个导套四条、当前共八条的完全二部图连接。
        radius_mm: 连接管半径。
        curve_resolution: Blender 实体化分辨率。
        recut_sleeve_bore: 是否在实体化后复切固定孔。
        connection_type: 当前实现的连接类型。
        press_beam_links_included: 是否包含第 5 步按压梁柱连接。
    """

    links: tuple[PointLink, ...]
    radius_mm: float
    curve_resolution: int
    recut_sleeve_bore: bool
    connection_type: str = "sleeve_template"
    press_beam_links_included: bool = False


def _bezier_centerline(
    start: Vec3,
    end: Vec3,
    start_normal: Vec3,
    end_normal: Vec3,
    config: PointLinkingConfig,
) -> tuple[tuple[Vec3, Vec3, Vec3, Vec3], tuple[Vec3, ...]]:
    """构造三次贝塞尔控制点和自适应采样中心线。

    参数:
        start: 导套端点。
        end: 牙科导板端点。
        start_normal: 导套端伸出方向。
        end_normal: 牙科导板表面法向。
        config: 连接半径和控制柄配置。

    返回:
        四个贝塞尔控制点及离散中心线。
    """

    distance = start.distance_to(end)
    handle = min(distance / 3.0, config.handle_factor * config.radius_mm)
    first_control = start + start_normal.normalized() * handle
    template_normal = end_normal.normalized()
    if template_normal.dot(start - end) < 0.0:
        template_normal = -template_normal
    second_control = end + template_normal * handle
    sample_count = max(16, int(distance / max(0.35 * config.radius_mm, 0.15)) + 1)
    controls = start, first_control, second_control, end
    centerline = tuple(
        start * ((1.0 - fraction) ** 3)
        + first_control * (3.0 * ((1.0 - fraction) ** 2) * fraction)
        + second_control * (3.0 * (1.0 - fraction) * (fraction**2))
        + end * (fraction**3)
        for fraction in (index / (sample_count - 1) for index in range(sample_count))
    )
    return controls, centerline


def link_selected_points(
    points: TemplateLinkPointPlan,
    config: PointLinkingConfig,
) -> PointLinkingPlan:
    """将每个导套的两个锚点分别连接到两个牙科导板点。

    参数:
        points: 第 4 步的导套侧和牙科导板侧选点。
        config: 曲线形状、半径、分辨率和复切配置。

    返回:
        可交给 Blender 建模层的纯几何连接计划。

    异常:
        ValueError: 任一导套侧或牙科导板侧选点不可行。

    算法说明:
        算法按以下顺序执行：

        1. 检查每个导套的上下锚点和牙科导板左右点均可行。
        2. 构造 ``lower-left``、``lower-right``、``upper-left``、
           ``upper-right`` 四条连接。
        3. 对每条连接计算端点距离 ``d``，控制柄长度为
           ``min(d / 3, handle_factor * radius_mm)``。
        4. 起点控制柄沿导套径向伸出；终点法向先翻转到朝向导套的半空间，
           再生成终点控制柄。
        5. 用四个控制点生成三次贝塞尔曲线，离散样本数为
           ``max(16, int(d / max(0.35 * radius_mm, 0.15)) + 1)``。
        6. 返回纯几何计划，并明确记录当前不包含按压梁柱连接。

        本函数不创建 Blender 对象。
    """

    links: list[PointLink] = []
    for sleeve, template in zip(
        points.sleeve_anchors.selections,
        points.template_points.selections,
        strict=True,
    ):
        if not sleeve.feasible:
            raise ValueError(f"导套 {sleeve.guide_index} 的导套侧锚点不可行")
        if not template.feasible:
            raise ValueError(f"导套 {sleeve.guide_index} 的牙科导板侧锚点不可行")
        for sleeve_label, sleeve_point in (
            ("lower", sleeve.lower),
            ("upper", sleeve.upper),
        ):
            for template_label, template_point in (
                ("left", template.left),
                ("right", template.right),
            ):
                controls, centerline = _bezier_centerline(
                    sleeve_point.position,
                    template_point.position,
                    sleeve.radial_direction,
                    template_point.normal,
                    config,
                )
                links.append(
                    PointLink(
                        sleeve.guide_index,
                        sleeve_label,
                        template_label,
                        sleeve_point.position,
                        template_point.position,
                        controls,
                        centerline,
                    )
                )
    return PointLinkingPlan(
        tuple(links),
        config.radius_mm,
        config.curve_resolution,
        config.recut_sleeve_bore,
    )

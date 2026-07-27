"""第 4 步的牙科导板侧左右点选择。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from twin_guide.config import GuideAnchorMode
from twin_guide.clearance import channel_distance, window_distance
from twin_guide.geometry import Vec3
from twin_guide.models import CaseAnalysis, CutoutPlan, GuideSleeve, SurfaceSample
from twin_guide.sleeve_anchors import SleeveAnchorPlan, SleeveAnchorSelection
from twin_guide.types import ConnectorEndpointSource, SleeveGenerationResult

if TYPE_CHECKING:
    from twin_guide.terminal_distal_common_node import TerminalDistalCommonNodePlan
    from twin_guide.tooth_identification import ToothIdentificationResult
    from twin_guide.tooth_section_anchors import IndependentGuideAnchorSelection

_SURFACE_TOLERANCE = 1e-9


@dataclass(frozen=True, slots=True)
class TemplatePointSelectionConfig:
    """牙科导板侧选点的独立配置。

    属性:
        template_clearance_mm: 候选点到窗口和通道的最小净距，单位毫米。
        connector_radius_mm: 连接管半径，单位毫米，用于计算左右点最小跨度。
        surface_sample_limit: 按中点距离保留的牙科导板表面样本上限。
        candidate_limit: 每一侧进入成对评分的候选点上限。

    算法说明:
        左右点最小跨度取导套外半径与连接管直径的 1.25 倍中的较大值，
        即 ``max(body_radius_mm, 2.5 * connector_radius_mm)``。
    """

    template_clearance_mm: float = 1.2
    connector_radius_mm: float = 2.30
    surface_sample_limit: int = 4096
    candidate_limit: int = 512

    def __post_init__(self) -> None:
        """校验净距、最小跨度和候选数量的取值范围。"""

        if self.template_clearance_mm < 0.0 or self.connector_radius_mm <= 0.0:
            raise ValueError("牙科导板净距不能为负，连接管半径必须为正")
        if self.surface_sample_limit <= 0 or self.candidate_limit <= 0:
            raise ValueError("牙科导板表面样本上限和单侧候选上限必须为正")

    def minimum_span_mm(self, body_radius_mm: float) -> float:
        """计算一个导套对应的牙科导板左右点最小跨度。

        参数:
            body_radius_mm: 导套主体外半径，单位毫米。

        返回:
            导套外半径与 ``2.5`` 倍连接管半径中的较大值。

        异常:
            ValueError: 导套主体外半径不是正数。
        """

        if body_radius_mm <= 0.0:
            raise ValueError("导套主体外半径必须为正")
        return max(body_radius_mm, 2.5 * self.connector_radius_mm)


@dataclass(frozen=True, slots=True)
class TemplateAnchorPoint:
    """牙科导板表面锚点。

    属性:
        position: 锚点世界坐标。
        normal: 牙科导板表面单位法向。
        polygon_index: 来源牙科导板多边形索引，用于确定性排序和诊断。
    """

    position: Vec3
    normal: Vec3
    polygon_index: int | None


@dataclass(frozen=True, slots=True)
class TemplatePointSelection:
    """一个导套对应的牙科导板左右点及诊断。

    属性:
        guide_index: 导套编号。
        sleeve_midpoint: 导套上下锚点中点。
        lateral_direction: 区分左右侧的单位向量。
        left: 牙科导板左侧锚点；不可行时为 ``None``。
        right: 牙科导板右侧锚点；不可行时为 ``None``。
        minimum_span_mm: 实际使用的最小左右间距。
        reason: 不可行原因；可行时为 ``None``。
    """

    guide_index: int
    sleeve_midpoint: Vec3
    lateral_direction: Vec3
    left: TemplateAnchorPoint | None
    right: TemplateAnchorPoint | None
    minimum_span_mm: float
    reason: str | None = None
    left_station_fdis: tuple[int, ...] = ()
    right_station_fdis: tuple[int, ...] = ()
    chosen_ray_angles_degrees: tuple[float, float] | None = None
    left_source: ConnectorEndpointSource = ConnectorEndpointSource.TEMPLATE
    right_source: ConnectorEndpointSource = ConnectorEndpointSource.TEMPLATE
    left_centerline_anchor: Vec3 | None = None
    right_centerline_anchor: Vec3 | None = None

    @property
    def feasible(self) -> bool:
        """返回牙科导板左右锚点是否同时存在。"""

        return self.left is not None and self.right is not None


@dataclass(frozen=True, slots=True)
class TemplatePointPlan:
    """所有导套的牙科导板侧选点计划。"""

    selections: tuple[TemplatePointSelection, ...]
    trajectories: tuple[tuple[Vec3, ...], ...] = ()
    terminal_distal_common_node: TerminalDistalCommonNodePlan | None = None
    multi_site_paths: tuple["MultiSiteTemplatePath", ...] = ()


@dataclass(frozen=True, slots=True)
class MultiSiteTemplatePath:
    """跨越全部相邻种植位双导管的一条同牙弓侧连续路径。"""

    side: str
    start: TemplateAnchorPoint
    end: TemplateAnchorPoint
    guide_indices: tuple[int, ...]
    start_station_fdis: tuple[int, ...]
    end_station_fdis: tuple[int, ...]
    ray_angles_degrees: tuple[float, float]
    start_source: ConnectorEndpointSource = ConnectorEndpointSource.TEMPLATE
    end_source: ConnectorEndpointSource = ConnectorEndpointSource.TEMPLATE
    start_centerline_anchor: Vec3 | None = None
    end_centerline_anchor: Vec3 | None = None


def _independent_anchor_endpoints(
    selections: tuple["IndependentGuideAnchorSelection", ...],
) -> tuple[dict[str, "IndependentGuideAnchorSelection"], ...]:
    """按配置首次出现顺序把独立锚点归入端部及 U/背 U 侧。"""

    grouped: dict[str, dict[str, IndependentGuideAnchorSelection]] = {}
    for selection in selections:
        endpoint = grouped.setdefault(selection.configuration.endpoint_id, {})
        endpoint[selection.configuration.side.value] = selection
    return tuple(grouped.values())


def _remaining_template_samples(
    case: CaseAnalysis, cutouts: CutoutPlan, clearance_mm: float
) -> tuple[SurfaceSample, ...]:
    """返回满足切口净距的牙科导板样本。

    参数:
        case: 包含牙科导板表面样本的病例分析。
        cutouts: 通道与窗口计划。
        clearance_mm: 样本到任一切口的最小净距。

    返回:
        不在切口内且净距达标的表面样本。
    """

    analytic_samples = tuple(
        sample
        for sample in case.template_samples
        if all(
            window_distance(sample.position, window) >= clearance_mm for window in cutouts.windows
        )
        and all(
            channel_distance(sample.position, channel.start, channel.end, channel.radius_mm)
            >= clearance_mm
            for channel in cutouts.channels
        )
    )
    if not cutouts.profile_windows or not analytic_samples:
        return analytic_samples

    # 变截面 cutter 无法用解析长方体距离表示，因此只在配置该路径时
    # 延迟加载 trimesh，不影响旧病例和纯几何单元测试。
    import numpy as np
    import trimesh

    points = np.asarray([sample.position.as_tuple() for sample in analytic_samples])
    retained = np.ones(len(analytic_samples), dtype=bool)
    for profile in cutouts.profile_windows:
        # PLY cutter 保留多个相接闭合体的独立索引；自动合并相同坐标
        # 会把有效的接触边误变为非流形边。
        loaded = trimesh.load_mesh(profile.cutter_mesh_path, process=False)
        cutter = (
            trimesh.util.concatenate(tuple(loaded.geometry.values()))
            if isinstance(loaded, trimesh.Scene)
            else loaded
        )
        if not isinstance(cutter, trimesh.Trimesh) or cutter.is_empty:
            raise ValueError(f"观察窗切割体为空：{profile.cutter_mesh_path}")
        lower_bound = cutter.bounds[0] - clearance_mm
        upper_bound = cutter.bounds[1] + clearance_mm
        local_indices = np.flatnonzero(
            retained
            & np.all(points >= lower_bound, axis=1)
            & np.all(points <= upper_bound, axis=1)
        )
        if not len(local_indices):
            continue
        local_points = points[local_indices]
        _, distances, _ = cutter.nearest.on_surface(local_points)
        local_retained = ~cutter.contains(local_points)
        local_retained &= distances >= clearance_mm
        retained[local_indices] = local_retained
    return tuple(
        sample
        for sample, keep in zip(analytic_samples, retained.tolist(), strict=True)
        if keep
    )


def _template_anchor(sample: SurfaceSample) -> TemplateAnchorPoint:
    """将表面样本转换为牙科导板锚点。

    参数:
        sample: 牙科导板表面样本。

    返回:
        法向已归一化的牙科导板锚点。
    """

    return TemplateAnchorPoint(sample.position, sample.normal.normalized(), sample.polygon_index)


def _sleeve_side_direction(guide: GuideSleeve, sleeve: SleeveAnchorSelection) -> Vec3:
    """计算牙科导板点的左右分组方向。

    参数:
        guide: 待处理导套。
        sleeve: 该导套的上下锚点选择。

    返回:
        轴向与 C 口反向的叉积单位向量。
    """

    direction = guide.axis.cross(sleeve.radial_direction)
    return direction.normalized()


def _select_template_pair(
    guide: GuideSleeve,
    sleeve: SleeveAnchorSelection,
    samples: tuple[SurfaceSample, ...],
    config: TemplatePointSelectionConfig,
) -> TemplatePointSelection:
    """为一个导套选择牙科导板左右点。

    参数:
        guide: 待联建导套。
        sleeve: 导套侧锚点。
        samples: 已通过净距筛选的牙科导板样本。
        config: 搜索数量和最小跨度配置。

    返回:
        最优双侧点对；无可行组合时返回带原因的不可行结果。
    """

    minimum_span = config.minimum_span_mm(guide.body_radius_mm)
    midpoint = (sleeve.lower.position + sleeve.upper.position) * 0.5
    ranked = sorted(
        samples, key=lambda sample: (midpoint.distance_to(sample.position), sample.polygon_index)
    )[: config.surface_sample_limit]
    if not ranked:
        return TemplatePointSelection(
            guide.guide_index,
            midpoint,
            guide.axis.cross(sleeve.radial_direction).normalized(),
            None,
            None,
            minimum_span,
            "牙科导板上没有剩余可选样本",
        )
    lateral = _sleeve_side_direction(guide, sleeve)
    left = tuple(
        sample
        for sample in ranked
        if (sample.position - midpoint).dot(lateral) < -_SURFACE_TOLERANCE
    )[: config.candidate_limit]
    right = tuple(
        sample
        for sample in ranked
        if (sample.position - midpoint).dot(lateral) > _SURFACE_TOLERANCE
    )[: config.candidate_limit]
    best: tuple[tuple[float, float, int, int], SurfaceSample, SurfaceSample] | None = None
    for left_sample in left:
        for right_sample in right:
            span = left_sample.position.distance_to(right_sample.position)
            if span < minimum_span:
                continue
            score = (
                midpoint.distance_to(left_sample.position)
                + midpoint.distance_to(right_sample.position),
                span,
                left_sample.polygon_index,
                right_sample.polygon_index,
            )
            if best is None or score < best[0]:
                best = score, left_sample, right_sample
    if best is None:
        return TemplatePointSelection(
            guide.guide_index,
            midpoint,
            lateral,
            None,
            None,
            minimum_span,
            "没有分居两侧的牙科导板点对满足最小跨度",
        )
    return TemplatePointSelection(
        guide.guide_index,
        midpoint,
        lateral,
        _template_anchor(best[1]),
        _template_anchor(best[2]),
        minimum_span,
    )


def select_template_points(
    case: CaseAnalysis,
    sleeves: SleeveGenerationResult,
    cutouts: CutoutPlan,
    sleeve_anchors: SleeveAnchorPlan,
    config: TemplatePointSelectionConfig,
    tooth_identification: ToothIdentificationResult | None = None,
) -> TemplatePointPlan:
    """在已切窗牙科导板的剩余表面上选择双侧最近点对。

    参数:
        case: 包含牙科导板表面样本的病例分析。
        sleeves: 第 1 步导套结果。
        cutouts: 第 3 步通道与窗口计划。
        sleeve_anchors: 已确定的导套侧上下锚点。
        config: 牙科导板点净距、跨度和候选数量配置。
        tooth_identification: 牙位截面轨迹模式所需的牙位与导板映射。

    返回:
        按导套顺序排列的牙科导板左右点计划。

    算法说明:
        算法按以下顺序执行：

        1. 剔除到任一窗口或通道的有符号距离小于
           ``template_clearance_mm`` 的牙科导板样本。
        2. 用导套上下锚点均值定义搜索中心，按到中心的距离排序，
           面索引作为确定性次序。
        3. 使用 ``guide.axis.cross(radial_direction)`` 定义左右方向，
           按点乘符号将候选点分到两侧。
        4. 枚举左右候选点对，丢弃间距小于
           ``max(body_radius_mm, 2.5 * connector_radius_mm)`` 的组合。
        5. 按 ``(两点到中心的距离和, 左右间距, 左面索引, 右面索引)``
           的字典序选择最小点对。
        6. 没有双侧候选或没有组合满足最小间距时，保留诊断原因，
           不使用同侧两点替代。

    """
    if (
        case.config.guide_anchors.mode
        is GuideAnchorMode.ADJACENT_TWO_IMPLANT_TERMINAL_DISTAL_NODE_PATHS
    ):
        if tooth_identification is None:
            raise ValueError("双种植位末端远中节点模式缺少牙位识别结果")
        if len(sleeves.sleeves) != 4 or len(sleeve_anchors.selections) != 4:
            raise ValueError("双种植位末端远中节点模式必须恰有四根导管")
        from twin_guide.terminal_distal_common_node import (
            select_terminal_distal_common_node,
        )
        from twin_guide.tooth_section_anchors import (
            select_local_independent_guide_anchors,
        )
        from twin_guide.types import GenerationContext

        independent = select_local_independent_guide_anchors(
            case,
            tooth_identification,
            case.config.guide_anchors.anchors,
        )
        endpoints = _independent_anchor_endpoints(independent)
        if len(endpoints) != 1:
            raise ValueError("双种植位末端远中节点模式必须产生一个近中锚点端部")
        side_selections = endpoints[0]
        side_anchors = {
            side: selection.anchor for side, selection in side_selections.items()
        }
        pair_assignments = []
        for pair_index in range(0, len(sleeves.sleeves), 2):
            pair = sleeves.sleeves[pair_index : pair_index + 2]
            direct = pair[0].center.distance_to(
                side_anchors["u_side"].position
            ) + pair[1].center.distance_to(side_anchors["back_u_side"].position)
            reverse = pair[1].center.distance_to(
                side_anchors["u_side"].position
            ) + pair[0].center.distance_to(side_anchors["back_u_side"].position)
            pair_assignments.append(
                {
                    "u_side": pair[0] if direct <= reverse else pair[1],
                    "back_u_side": pair[1] if direct <= reverse else pair[0],
                }
            )

        parameters = case.config.guide_anchors.terminal_distal_common_node
        if parameters is None or len(parameters.implant_fdis) != len(pair_assignments):
            raise ValueError("双种植位末端远中节点配置与导管装配体数量不一致")
        terminal_pair_index = parameters.implant_fdis.index(parameters.missing_fdi)
        terminal_assignment = pair_assignments[terminal_pair_index]
        sleeve_anchor_by_index = {
            selection.guide_index: selection
            for selection in sleeve_anchors.selections
        }
        terminal_guides = (
            terminal_assignment["u_side"],
            terminal_assignment["back_u_side"],
        )
        terminal_lower_midpoint = (
            sleeve_anchor_by_index[terminal_guides[0].guide_index].lower.position
            + sleeve_anchor_by_index[terminal_guides[1].guide_index].lower.position
        ) * 0.5
        terminal_anchor = select_terminal_distal_common_node(
            GenerationContext(
                config=case.config,
                case=case,
                sleeve_generation=sleeves,
                tooth_identification=tooth_identification,
                window_cutouts=cutouts,
            ),
            terminal_lower_midpoint,
            terminal_guides=terminal_guides,
        )
        distal_node = TemplateAnchorPoint(
            terminal_anchor.centerline_node,
            terminal_anchor.distal_direction,
            None,
        )
        paths = tuple(
            MultiSiteTemplatePath(
                side,
                TemplateAnchorPoint(
                    anchor.position,
                    anchor.normal.normalized(),
                    anchor.polygon_index,
                ),
                distal_node,
                tuple(
                    assignment[side].guide_index
                    for assignment in pair_assignments
                ),
                side_selections[side].station_fdis,
                (parameters.missing_fdi,),
                (
                    side_selections[side].configuration.ray_angle_degrees,
                    0.0,
                ),
                end_source=ConnectorEndpointSource.DISTAL_COMMON_NODE,
                end_centerline_anchor=terminal_anchor.centerline_node,
            )
            for side, anchor in side_anchors.items()
        )
        selections = tuple(
            TemplatePointSelection(
                guide_index,
                (
                    sleeve_anchor_by_index[guide_index].lower.position
                    + sleeve_anchor_by_index[guide_index].upper.position
                )
                * 0.5,
                (terminal_anchor.centerline_node - path.start.position).normalized(),
                path.start,
                distal_node,
                0.0,
                left_station_fdis=side_selections[path.side].station_fdis,
                right_station_fdis=(parameters.missing_fdi,),
                chosen_ray_angles_degrees=path.ray_angles_degrees,
                right_source=ConnectorEndpointSource.DISTAL_COMMON_NODE,
                right_centerline_anchor=terminal_anchor.centerline_node,
            )
            for path in paths
            for guide_index in path.guide_indices
        )
        return TemplatePointPlan(
            selections,
            tuple(selection.support_trajectory for selection in independent),
            terminal_anchor,
            paths,
        )

    if (
        case.config.guide_anchors.mode
        is GuideAnchorMode.ADJACENT_TWO_IMPLANT_CONTINUOUS_PATHS
    ):
        if tooth_identification is None:
            raise ValueError("相邻双种植位连续路径模式缺少牙位识别结果")
        from twin_guide.tooth_section_anchors import (
            select_local_independent_guide_anchors,
        )

        independent = select_local_independent_guide_anchors(
            case,
            tooth_identification,
            case.config.guide_anchors.anchors,
        )
        endpoints = _independent_anchor_endpoints(independent)
        if len(endpoints) != 2:
            raise ValueError("相邻双种植位连续路径必须产生两个端部站位")

        path_anchors = {
            side: (endpoints[0][side].anchor, endpoints[1][side].anchor)
            for side in ("u_side", "back_u_side")
        }
        pair_assignments = []
        for pair_index in range(0, len(sleeves.sleeves), 2):
            pair = sleeves.sleeves[pair_index : pair_index + 2]
            if len(pair) != 2:
                raise ValueError("每个种植位必须恰有两根导管")
            targets = {
                side: (anchors[0].position + anchors[1].position) * 0.5
                for side, anchors in path_anchors.items()
            }
            direct = pair[0].center.distance_to(targets["u_side"]) + pair[1].center.distance_to(
                targets["back_u_side"]
            )
            reverse = pair[1].center.distance_to(targets["u_side"]) + pair[0].center.distance_to(
                targets["back_u_side"]
            )
            pair_assignments.append(
                {
                    "u_side": pair[0] if direct <= reverse else pair[1],
                    "back_u_side": pair[1] if direct <= reverse else pair[0],
                }
            )

        endpoint_midline_start = (
            endpoints[0]["u_side"].anchor.position
            + endpoints[0]["back_u_side"].anchor.position
        ) * 0.5
        endpoint_midline_end = (
            endpoints[1]["u_side"].anchor.position
            + endpoints[1]["back_u_side"].anchor.position
        ) * 0.5
        progression = (endpoint_midline_end - endpoint_midline_start).normalized()
        pair_assignments.sort(
            key=lambda assignment: (
                (
                    (
                        assignment["u_side"].center
                        + assignment["back_u_side"].center
                    )
                    * 0.5
                    - endpoint_midline_start
                ).dot(progression)
            )
        )

        paths = tuple(
            MultiSiteTemplatePath(
                side,
                TemplateAnchorPoint(
                    anchors[0].position,
                    anchors[0].normal.normalized(),
                    anchors[0].polygon_index,
                ),
                TemplateAnchorPoint(
                    anchors[1].position,
                    anchors[1].normal.normalized(),
                    anchors[1].polygon_index,
                ),
                tuple(assignment[side].guide_index for assignment in pair_assignments),
                endpoints[0][side].station_fdis,
                endpoints[1][side].station_fdis,
                (
                    endpoints[0][side].configuration.ray_angle_degrees,
                    endpoints[1][side].configuration.ray_angle_degrees,
                ),
            )
            for side, anchors in path_anchors.items()
        )
        sleeve_by_index = {
            selection.guide_index: selection
            for selection in sleeve_anchors.selections
        }
        selections = []
        for path in paths:
            for guide_index in path.guide_indices:
                sleeve = sleeve_by_index[guide_index]
                midpoint = (sleeve.lower.position + sleeve.upper.position) * 0.5
                selections.append(
                    TemplatePointSelection(
                        guide_index,
                        midpoint,
                        (path.end.position - path.start.position).normalized(),
                        path.start,
                        path.end,
                        0.0,
                        left_station_fdis=path.start_station_fdis,
                        right_station_fdis=path.end_station_fdis,
                        chosen_ray_angles_degrees=path.ray_angles_degrees,
                    )
                )
        return TemplatePointPlan(
            tuple(selections),
            tuple(selection.support_trajectory for selection in independent),
            multi_site_paths=paths,
        )

    if case.config.guide_anchors.mode in {
        GuideAnchorMode.TOOTH_SECTION_TRAJECTORY,
        GuideAnchorMode.TERMINAL_DISTAL_COMMON_NODE,
    }:
        if tooth_identification is None:
            raise ValueError("牙位截面轨迹锚点模式缺少牙位识别结果")
        from twin_guide.tooth_section_anchors import select_independent_guide_anchors

        independent = select_independent_guide_anchors(
            case,
            sleeves,
            tooth_identification,
            case.config.guide_anchors.anchors,
        )
        endpoints = _independent_anchor_endpoints(independent)
        sleeve_midpoints = tuple(
            (sleeve.lower.position + sleeve.upper.position) * 0.5
            for sleeve in sleeve_anchors.selections
        )
        assigned: list[list[tuple[object, tuple[int, ...], float]]] = [[], []]
        for endpoint in endpoints:
            options = tuple(
                (
                    endpoint[side].anchor,
                    endpoint[side].station_fdis,
                    endpoint[side].configuration.ray_angle_degrees,
                )
                for side in ("u_side", "back_u_side")
            )
            direct_cost = options[0][0].position.distance_to(sleeve_midpoints[0]) + options[
                1
            ][0].position.distance_to(sleeve_midpoints[1])
            reverse_cost = options[1][0].position.distance_to(sleeve_midpoints[0]) + options[
                0
            ][0].position.distance_to(sleeve_midpoints[1])
            if direct_cost <= reverse_cost:
                assigned[0].append(options[0])
                assigned[1].append(options[1])
            else:
                assigned[0].append(options[1])
                assigned[1].append(options[0])

        if (
            case.config.guide_anchors.mode
            is GuideAnchorMode.TERMINAL_DISTAL_COMMON_NODE
        ):
            from twin_guide.terminal_distal_common_node import (
                select_terminal_distal_common_node,
            )
            from twin_guide.types import GenerationContext

            lower_anchor_midpoint = (
                sleeve_anchors.selections[0].lower.position
                + sleeve_anchors.selections[1].lower.position
            ) * 0.5
            terminal_anchor = select_terminal_distal_common_node(
                GenerationContext(
                    config=case.config,
                    case=case,
                    sleeve_generation=sleeves,
                    tooth_identification=tooth_identification,
                    window_cutouts=cutouts,
                ),
                lower_anchor_midpoint,
            )
            selections = []
            for index, (guide, sleeve) in enumerate(
                zip(sleeves.sleeves, sleeve_anchors.selections, strict=True)
            ):
                if len(assigned[index]) != 1:
                    raise ValueError("末端远中公共节点模式每根导管必须分配一个导板锚点")
                guide_anchor, station_fdis, ray_angle = assigned[index][0]
                distal_node = TemplateAnchorPoint(
                    terminal_anchor.centerline_node,
                    terminal_anchor.distal_direction,
                    None,
                )
                lateral = (
                    terminal_anchor.centerline_node - guide_anchor.position
                ).normalized()
                selections.append(
                    TemplatePointSelection(
                        guide.guide_index,
                        (sleeve.lower.position + sleeve.upper.position) * 0.5,
                        lateral,
                        TemplateAnchorPoint(
                            guide_anchor.position,
                            guide_anchor.normal.normalized(),
                            guide_anchor.polygon_index,
                        ),
                        distal_node,
                        config.minimum_span_mm(guide.body_radius_mm),
                        left_station_fdis=station_fdis,
                        chosen_ray_angles_degrees=(ray_angle, 0.0),
                        left_source=ConnectorEndpointSource.TEMPLATE,
                        right_source=ConnectorEndpointSource.DISTAL_COMMON_NODE,
                        left_centerline_anchor=guide_anchor.position,
                        right_centerline_anchor=terminal_anchor.centerline_node,
                    )
                )
            return TemplatePointPlan(
                tuple(selections),
                tuple(selection.support_trajectory for selection in independent),
                terminal_anchor,
            )

        selections = []
        for index, (guide, sleeve) in enumerate(
            zip(sleeves.sleeves, sleeve_anchors.selections, strict=True)
        ):
            midpoint = (sleeve.lower.position + sleeve.upper.position) * 0.5
            left_source, right_source = assigned[index]
            left_anchor, left_fdis, left_angle = left_source
            right_anchor, right_fdis, right_angle = right_source
            lateral = (right_anchor.position - left_anchor.position).normalized()
            minimum_span = config.minimum_span_mm(guide.body_radius_mm)
            selections.append(
                TemplatePointSelection(
                    guide.guide_index,
                    midpoint,
                    lateral,
                    TemplateAnchorPoint(
                        left_anchor.position,
                        left_anchor.normal.normalized(),
                        left_anchor.polygon_index,
                    ),
                    TemplateAnchorPoint(
                        right_anchor.position,
                        right_anchor.normal.normalized(),
                        right_anchor.polygon_index,
                    ),
                    minimum_span,
                    left_station_fdis=left_fdis,
                    right_station_fdis=right_fdis,
                    chosen_ray_angles_degrees=(left_angle, right_angle),
                )
            )
        return TemplatePointPlan(
            tuple(selections),
            tuple(selection.support_trajectory for selection in independent),
        )

    samples = _remaining_template_samples(case, cutouts, config.template_clearance_mm)
    return TemplatePointPlan(
        tuple(
            _select_template_pair(guide, anchor, samples, config)
            for guide, anchor in zip(sleeves.sleeves, sleeve_anchors.selections, strict=True)
        )
    )

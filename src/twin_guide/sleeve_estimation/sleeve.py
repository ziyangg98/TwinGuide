"""无外部依赖的开口导套几何参数估计。"""

from __future__ import annotations

import math
from itertools import pairwise
from statistics import median

from twin_guide.geometry import Vec3, mean_point, orthonormal_tangent, principal_axis, quantile

from .fitting import (
    CircleFit,
    fit_axis,
    fit_circle,
    observed_arc_angle,
)
from .slicing import Section, section_offsets, slice_mesh
from .types import EstimationConfig, ParameterDiagnostic, SleeveEstimate, TriangleMeshData


def _axial_extent(mesh: TriangleMeshData, origin: Vec3, axis: Vec3) -> tuple[float, float]:
    """用顶点轴向投影的 0.5% 和 99.5% 分位数估计稳健轴向范围。"""

    values = tuple((point - origin).dot(axis) for point in mesh.vertices)
    return quantile(values, 0.005), quantile(values, 0.995)


def _wall_groups(
    section: Section, axis_point: Vec3, threshold: float
) -> tuple[tuple[Vec3, ...], tuple[Vec3, ...]]:
    """根据表面法向与径向的点乘符号将截面样本分成内外壁。"""

    positive: list[Vec3] = []
    negative: list[Vec3] = []
    for sample in section.samples:
        radial = sample.point - axis_point
        radial -= section.plane_normal * radial.dot(section.plane_normal)
        if radial.length < 1e-10:
            continue
        score = sample.normal.dot(radial.normalized())
        if score >= threshold:
            positive.append(sample.point)
        elif score <= -threshold:
            negative.append(sample.point)
    return tuple(positive), tuple(negative)


def _curved_wall_groups(
    section: Section, axis_point: Vec3, threshold: float
) -> tuple[tuple[Vec3, ...], tuple[Vec3, ...]]:
    """利用有序转折点排除槽壁和平台直壁。"""

    positive: list[Vec3] = []
    negative: list[Vec3] = []
    effective_threshold = max(threshold, 0.55)
    for polyline in section.polylines:
        samples = polyline.samples
        count = len(samples)
        if count < 5:
            continue
        curved: set[int] = set()
        limits = range(count) if polyline.closed else range(1, count - 1)
        for index in limits:
            previous = samples[(index - 1) % count].point
            current = samples[index].point
            following = samples[(index + 1) % count].point
            incoming = current - previous
            outgoing = following - current
            if incoming.length < 1e-10 or outgoing.length < 1e-10:
                continue
            cosine = max(-1.0, min(1.0, incoming.normalized().dot(outgoing.normalized())))
            if math.acos(cosine) >= 2e-3:
                curved.update(((index - 1) % count, index, (index + 1) % count))
        for index in sorted(curved):
            sample = samples[index]
            radial = sample.point - axis_point
            radial -= section.plane_normal * radial.dot(section.plane_normal)
            if radial.length < 1e-10:
                continue
            score = sample.normal.dot(radial.normalized())
            if score >= effective_threshold:
                positive.append(sample.point)
            elif score <= -effective_threshold:
                negative.append(sample.point)
    minimum = 3
    if len(positive) >= minimum and len(negative) >= minimum:
        return tuple(positive), tuple(negative)
    return _wall_groups(section, axis_point, threshold)


def _fit_section_walls(
    section: Section,
    axis_origin: Vec3,
    config: EstimationConfig,
) -> tuple[CircleFit, CircleFit, tuple[Vec3, ...], tuple[Vec3, ...]]:
    """对单个轴向截面拟合内外壁圆，必要时使用径向分组退化方案。"""

    axis_point = axis_origin + section.plane_normal * section.offset
    groups = _curved_wall_groups(section, axis_point, config.normal_radial_threshold)
    fits: list[tuple[CircleFit, tuple[Vec3, ...]]] = []
    for group in groups:
        if len(group) < config.minimum_arc_points:
            continue
        try:
            fit = fit_circle(group, axis_point, section.plane_normal, config.trim_sigma)
        except ValueError:
            continue
        fits.append((fit, group))
    if len(fits) < 2:
        # 法向方位不可靠时，按径向距离确定性地分组，并用最终残差评估结果。
        points = tuple(sample.point for sample in section.samples)
        radial_pairs = sorted(
            ((point.distance_to(axis_point), point) for point in points),
            key=lambda item: item[0],
        )
        split = len(radial_pairs) // 2
        candidates = (
            tuple(value[1] for value in radial_pairs[:split]),
            tuple(value[1] for value in radial_pairs[split:]),
        )
        fits = [
            (
                fit_circle(group, axis_point, section.plane_normal, config.trim_sigma),
                group,
            )
            for group in candidates
            if len(group) >= 3
        ]
    if len(fits) < 2:
        raise ValueError("截面中无法分离内壁与外壁")
    fits.sort(key=lambda item: item[0].radius)
    inner, inner_points = fits[0]
    outer_group = fits[-1][1]
    # 重新拟合主体圆前去除单侧平台尾部。
    distances = tuple(point.distance_to(fits[-1][0].center) for point in outer_group)
    cutoff = quantile(distances, 0.72)
    body_points = tuple(
        point
        for point, distance in zip(outer_group, distances, strict=True)
        if distance <= cutoff
    )
    outer = (
        fit_circle(body_points, axis_point, section.plane_normal, config.trim_sigma)
        if len(body_points) >= 3
        else fits[-1][0]
    )
    return inner, outer, inner_points, body_points


def _transition_planes(
    mesh: TriangleMeshData,
    origin: Vec3,
    axis: Vec3,
    minimum: float,
    maximum: float,
) -> tuple[float, float, int, bool]:
    """聚合近垂直于轴线的内部三角面，估计平台和固定孔转换面。"""

    span = maximum - minimum
    clusters: dict[int, tuple[float, float, float]] = {}
    tolerance = max(span * 0.01, 1e-5)
    for face in mesh.faces:
        first, second, third = (mesh.vertices[index] for index in face)
        cross = (second - first).cross(third - first)
        area = 0.5 * cross.length
        if area <= 1e-12 or abs(cross.normalized().dot(axis)) < 0.85:
            continue
        centroid = (first + second + third) / 3.0
        z_value = (centroid - origin).dot(axis)
        if z_value <= minimum + 0.12 * span or z_value >= maximum - 0.12 * span:
            continue
        radial = (centroid - origin) - axis * z_value
        key = round(z_value / tolerance)
        total_area, weighted_z, radial_extent = clusters.get(key, (0.0, 0.0, 0.0))
        clusters[key] = (
            total_area + area,
            weighted_z + area * z_value,
            max(radial_extent, radial.length),
        )
    if not clusters:
        return maximum - 0.35 * span, maximum - 0.18 * span, 0, False
    candidates = sorted(
        ((weighted / area, area, extent) for area, weighted, extent in clusters.values()),
        key=lambda item: (item[2], item[1]),
        reverse=True,
    )
    low_radii: list[float] = []
    high_radii: list[float] = []
    for point in mesh.vertices:
        z_value = (point - origin).dot(axis)
        radial = ((point - origin) - axis * z_value).length
        if z_value <= minimum + 0.18 * span:
            low_radii.append(radial)
        if z_value >= maximum - 0.18 * span:
            high_radii.append(radial)
    platform_at_minimum = max(low_radii) >= max(high_radii)
    selected: list[float] = []
    for position, _, _ in candidates:
        if all(abs(position - existing) >= 0.08 * span for existing in selected):
            selected.append(position)
        if len(selected) == 2:
            break
    if len(selected) < 2:
        if platform_at_minimum:
            selected = [minimum + 0.33 * span, minimum + 0.66 * span]
        else:
            selected = [minimum + 0.34 * span, minimum + 0.67 * span]
    lower, upper = sorted(selected)
    return lower, upper, len(clusters), platform_at_minimum


def _section_regime(
    section: Section,
    axis_origin: Vec3,
    axis: Vec3,
    outer_radius: float,
) -> str:
    """将稳定截面分为上段 C 形区、中段槽区或下段固定孔区。"""

    substantial_closed = sum(
        polyline.closed and len(polyline.samples) >= 6
        for polyline in section.polylines
    )
    if substantial_closed >= 2:
        return "lower"
    axis_point = axis_origin + axis * section.offset
    radial_max = max(
        (
            ((sample.point - axis_point) - axis * (sample.point - axis_point).dot(axis)).length
            for sample in section.samples
        ),
        default=0.0,
    )
    return "middle" if radial_max > 1.04 * outer_radius else "upper"


def _refine_regime_boundary(
    mesh: TriangleMeshData,
    origin: Vec3,
    axis: Vec3,
    low: float,
    high: float,
    low_regime: str,
    outer_radius: float,
    config: EstimationConfig,
) -> float:
    """在截面类型发生变化的区间内二分定位分界面。"""

    for _ in range(7):
        middle = 0.5 * (low + high)
        section = slice_mesh(mesh, origin, axis, middle, config.section_tolerance)
        regime = _section_regime(section, origin, axis, outer_radius)
        if regime == low_regime:
            low = middle
        else:
            high = middle
    return 0.5 * (low + high)


def _topology_transition_planes(
    mesh: TriangleMeshData,
    origin: Vec3,
    axis: Vec3,
    sections: list[Section],
    outer_radius: float,
    platform_at_minimum: bool,
    fallback: tuple[float, float],
    config: EstimationConfig,
) -> tuple[float, float, int, bool]:
    """优先使用有序截面拓扑定位转换面，失败时使用轴向面位置。"""

    ordered = sorted(
        ((section.offset, _section_regime(section, origin, axis, outer_radius)) for section in sections),
        key=lambda item: item[0],
    )
    boundaries: dict[frozenset[str], float] = {}
    for (low, low_regime), (high, high_regime) in pairwise(ordered):
        pair = frozenset((low_regime, high_regime))
        if low_regime == high_regime or pair in boundaries:
            continue
        if pair in (frozenset(("upper", "middle")), frozenset(("middle", "lower"))):
            boundaries[pair] = _refine_regime_boundary(
                mesh,
                origin,
                axis,
                low,
                high,
                low_regime,
                outer_radius,
                config,
            )
    shoulder = boundaries.get(frozenset(("upper", "middle")))
    slot_end = boundaries.get(frozenset(("middle", "lower")))
    if shoulder is None or slot_end is None:
        return fallback[0], fallback[1], len(boundaries), True
    lower, upper = sorted((shoulder, slot_end))
    expected = slot_end < shoulder if platform_at_minimum else shoulder < slot_end
    if not expected:
        return fallback[0], fallback[1], len(boundaries), True
    return lower, upper, len(boundaries), False


def estimate_sleeve(
    mesh: TriangleMeshData,
    config: EstimationConfig | None = None,
    preferred_axis: Vec3 | None = None,
) -> SleeveEstimate:
    """根据有序截面序列估计导套的核心几何参数。"""

    controls = config or EstimationConfig()
    center = mean_point(mesh.vertices)
    axis = principal_axis(mesh.vertices)
    if preferred_axis is not None and axis.dot(preferred_axis) < 0.0:
        axis = -axis

    inner_fits: list[CircleFit] = []
    outer_fits: list[CircleFit] = []
    inner_points: list[tuple[Vec3, ...]] = []
    outer_points: list[tuple[Vec3, ...]] = []
    sections: list[Section] = []
    axis_residual = 0.0
    for _ in range(controls.axis_iterations):
        minimum, maximum = _axial_extent(mesh, center, axis)
        inner_fits.clear()
        outer_fits.clear()
        inner_points.clear()
        outer_points.clear()
        sections.clear()
        for offset in section_offsets(
            minimum,
            maximum,
            controls.slice_count,
            controls.slice_fraction_low,
            controls.slice_fraction_high,
        ):
            section = slice_mesh(mesh, center, axis, offset, controls.section_tolerance)
            try:
                inner, outer, inner_group, outer_group = _fit_section_walls(
                    section, center, controls
                )
            except ValueError:
                continue
            inner_fits.append(inner)
            outer_fits.append(outer)
            inner_points.append(inner_group)
            outer_points.append(outer_group)
            sections.append(section)
        if len(inner_fits) < 3:
            raise ValueError("有效有序截面过少，无法识别导套轴线")
        fitted_axis = fit_axis(tuple(item.center for item in inner_fits))
        if fitted_axis.direction.dot(axis) < 0.0:
            fitted_axis = type(fitted_axis)(
                fitted_axis.origin,
                -fitted_axis.direction,
                fitted_axis.rms_residual,
                fitted_axis.point_count,
            )
        center, axis = fitted_axis.origin, fitted_axis.direction
        axis_residual = fitted_axis.rms_residual

    minimum, maximum = _axial_extent(mesh, center, axis)
    fallback_lower, fallback_upper, face_transition_count, platform_at_minimum = _transition_planes(
        mesh, center, axis, minimum, maximum
    )
    inner_radius = median(item.radius for item in inner_fits)
    outer_radius = median(item.radius for item in outer_fits)
    topology_sections = [
        slice_mesh(mesh, center, axis, offset, controls.section_tolerance)
        for offset in section_offsets(
            minimum,
            maximum,
            max(13, 2 * controls.slice_count),
            0.04,
            0.96,
        )
    ]
    lower, upper, transition_count, transition_fallback = _topology_transition_planes(
        mesh,
        center,
        axis,
        topology_sections,
        outer_radius,
        platform_at_minimum,
        (fallback_lower, fallback_upper),
        controls,
    )
    height = maximum - minimum
    if platform_at_minimum:
        canonical_axis = -axis
        axis_origin = center + axis * maximum
        shoulder = upper
        slot_end = lower
        platform_height = shoulder - minimum
        closed_bore_height = slot_end - minimum
    else:
        canonical_axis = axis
        axis_origin = center + axis * minimum
        shoulder = lower
        slot_end = upper
        platform_height = maximum - shoulder
        closed_bore_height = maximum - slot_end

    upper_indices = tuple(
        index
        for index, section in enumerate(sections)
        if (section.offset >= shoulder if platform_at_minimum else section.offset <= shoulder)
    )
    if not upper_indices:
        upper_indices = tuple(
            index for index, section in enumerate(sections)
            if len(section.polylines) == 1
        ) or tuple(range(len(sections)))

    directions: list[Vec3] = []
    platform_vectors: list[Vec3] = []
    for outer, section in zip(outer_fits, sections, strict=True):
        in_platform = (
            minimum <= section.offset <= shoulder
            if platform_at_minimum
            else shoulder <= section.offset <= maximum
        )
        if not in_platform:
            continue
        for sample in section.samples:
            radial = sample.point - outer.center
            radial -= axis * radial.dot(axis)
            platform_vectors.append(radial)
        excess = tuple(
            value for value in platform_vectors[-len(section.samples):]
            if value.length > 1.03 * outer.radius
        )
        if excess:
            direction = mean_point(excess)
            if direction.length > 1e-10:
                direction = direction.normalized()
                if directions and direction.dot(directions[0]) < 0.0:
                    direction = -direction
                directions.append(direction)
    platform_direction = (
        mean_point(directions).normalized()
        if directions
        else orthonormal_tangent(canonical_axis, Vec3(1.0, 0.0, 0.0))
    )
    platform_direction -= canonical_axis * platform_direction.dot(canonical_axis)
    platform_direction = platform_direction.normalized()

    def fitted_arc_points(points: tuple[Vec3, ...], fit: CircleFit) -> tuple[Vec3, ...]:
        """保留径向残差不超过拟合容差的圆弧点。"""

        tolerance = max(3.0 * fit.rms_residual, 0.015 * fit.radius)
        retained = tuple(
            point for point in points
            if abs(point.distance_to(fit.center) - fit.radius) <= tolerance
        )
        return retained if len(retained) >= controls.minimum_arc_points else points

    fitted_inner_points = tuple(
        fitted_arc_points(points, fit)
        for points, fit in zip(inner_points, inner_fits, strict=True)
    )
    fitted_outer_points = tuple(
        fitted_arc_points(points, fit)
        for points, fit in zip(outer_points, outer_fits, strict=True)
    )

    def opening_gap(points: tuple[Vec3, ...], fit: CircleFit) -> tuple[float, Vec3]:
        """返回圆弧最大缺口的张角和中心方向。"""

        tangent = orthonormal_tangent(fit.plane_normal, Vec3(1.0, 0.0, 0.0))
        bitangent = fit.plane_normal.cross(tangent).normalized()
        angles = sorted(
            math.atan2(
                (point - fit.center).dot(bitangent),
                (point - fit.center).dot(tangent),
            ) % (2.0 * math.pi)
            for point in points
        )
        gaps = tuple(
            (
                (angles[(index + 1) % len(angles)] - angles[index])
                % (2.0 * math.pi),
                index,
            )
            for index in range(len(angles))
        )
        gap, index = max(gaps)
        midpoint = angles[index] + 0.5 * gap
        return gap, tangent * math.cos(midpoint) + bitangent * math.sin(midpoint)

    gap_directions = tuple(
        (*opening_gap(fitted_inner_points[index], inner_fits[index]), index)
        for index in upper_indices
        if len(fitted_inner_points[index]) >= controls.minimum_arc_points
    )
    significant_gaps = tuple(
        (gap, direction, index)
        for gap, direction, index in gap_directions
        if gap >= max(0.20, 4.0 * 2.0 * math.pi / len(fitted_inner_points[index]))
    )
    largest_gap = max((gap for gap, _, _ in significant_gaps), default=0.0)
    informative_directions = tuple(
        direction
        for gap, direction, _ in significant_gaps
        if gap >= max(0.15, 0.5 * largest_gap)
    )
    if informative_directions:
        opening_direction = mean_point(informative_directions)
        opening_direction -= canonical_axis * opening_direction.dot(canonical_axis)
        if opening_direction.length > 1e-10:
            platform_direction = opening_direction.normalized()

    cut_indices = tuple(
        index
        for gap, _, index in significant_gaps
        if gap >= max(0.20, 0.5 * largest_gap)
    )
    if not cut_indices:
        raise ValueError("上段截面的开口均未超过网格离散误差")

    inner_sweeps = tuple(
        observed_arc_angle(fitted_inner_points[index], inner_fits[index])
        for index in cut_indices
        if len(fitted_inner_points[index]) >= controls.minimum_arc_points
    )
    outer_sweeps = tuple(
        observed_arc_angle(fitted_outer_points[index], outer_fits[index])
        for index in cut_indices
        if len(fitted_outer_points[index]) >= controls.minimum_arc_points
    )
    if not inner_sweeps or not outer_sweeps:
        raise ValueError("上段 C 形截面未同时暴露可拟合的内外圆弧")

    inner_cut_values = tuple(
        inner_fits[index].radius * math.cos(
            0.5 * opening_gap(fitted_inner_points[index], inner_fits[index])[0]
        )
        for index in cut_indices
        if len(fitted_inner_points[index]) >= controls.minimum_arc_points
    )
    outer_cut_values = tuple(
        outer_fits[index].radius * math.cos(
            0.5 * opening_gap(fitted_outer_points[index], outer_fits[index])[0]
        )
        for index in cut_indices
        if len(fitted_outer_points[index]) >= controls.minimum_arc_points
    )
    # 内孔圆弧采样较密且直接定义功能开口，因此用它定位公共切口。
    # 稳定性较低的外圆弧端点估计仅用于一致性诊断。
    common_cut = median(inner_cut_values)
    common_cut = max(-0.999 * inner_radius, min(0.999 * inner_radius, common_cut))
    inner_arc_angle = 2.0 * math.pi - 2.0 * math.acos(common_cut / inner_radius)
    outer_arc_angle = 2.0 * math.pi - 2.0 * math.acos(common_cut / outer_radius)

    edge_coordinates = tuple(
        value.dot(platform_direction) for value in platform_vectors
    )
    if not edge_coordinates:
        raise ValueError("没有可用于估计 Wp 的平台截面")
    platform_edge = quantile(edge_coordinates, 0.995)
    platform_width = max(0.0, platform_edge - common_cut)

    def spread(values: tuple[float, ...]) -> float:
        """返回数值序列的中位绝对偏差。"""

        middle = median(values)
        return median(abs(value - middle) for value in values)

    cut_difference = abs(median(inner_cut_values) - median(outer_cut_values))
    diagnostics = (
        ParameterDiagnostic("axis", True, len(inner_fits), axis_residual),
        ParameterDiagnostic("H", height > 0.0, len(mesh.vertices), spread=0.005 * height),
        ParameterDiagnostic(
            "hp",
            0.0 < platform_height < height and not transition_fallback,
            transition_count,
            message=(
                "有序截面拓扑与二分定位"
                if not transition_fallback
                else f"使用轴向面位置；检出 {face_transition_count} 个面簇"
            ),
        ),
        ParameterDiagnostic(
            "hs",
            0.0 < closed_bore_height < platform_height and not transition_fallback,
            transition_count,
            message=(
                "有序截面拓扑与二分定位"
                if not transition_fallback
                else "未找到包围侧槽闭合转换的截面区间"
            ),
        ),
        ParameterDiagnostic("Wp", platform_width > 0.0, len(edge_coordinates)),
        ParameterDiagnostic(
            "Rin", inner_radius > 0.0, len(inner_fits),
            median(item.rms_residual for item in inner_fits),
            spread(tuple(item.radius for item in inner_fits)),
        ),
        ParameterDiagnostic(
            "Rout", outer_radius > inner_radius, len(outer_fits),
            median(item.rms_residual for item in outer_fits),
            spread(tuple(item.radius for item in outer_fits)),
        ),
        ParameterDiagnostic(
            "phi_in", 0.0 < inner_arc_angle < 2.0 * math.pi,
            len(inner_cut_values), spread=spread(inner_cut_values),
        ),
        ParameterDiagnostic(
            "phi_out",
            0.0 < outer_arc_angle < 2.0 * math.pi
            and cut_difference <= 0.1 * outer_radius,
            len(outer_cut_values),
            residual=cut_difference,
            spread=spread(outer_cut_values),
            message="共享切割位置的端点一致性",
        ),
    )
    return SleeveEstimate(
        axis_origin=axis_origin,
        axis=canonical_axis,
        platform_direction=platform_direction,
        height=height,
        platform_height=platform_height,
        closed_bore_height=closed_bore_height,
        platform_width=platform_width,
        inner_radius=inner_radius,
        outer_radius=outer_radius,
        inner_arc_angle=inner_arc_angle,
        outer_arc_angle=outer_arc_angle,
        diagnostics=diagnostics,
    )


def estimate_sleeve_parameters(
    mesh: TriangleMeshData,
    config: EstimationConfig | None = None,
    preferred_axis: Vec3 | None = None,
) -> SleeveEstimate:
    """调用 :func:`estimate_sleeve` 估计导套参数。"""

    return estimate_sleeve(mesh, config, preferred_axis)

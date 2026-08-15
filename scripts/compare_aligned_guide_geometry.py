"""对齐真实参考与生成双导柱，只比较同侧导柱的形状并绘制截面与误差图。"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import trimesh
from scipy.spatial import cKDTree


@dataclass(frozen=True, slots=True)
class PairFrame:
    """双导柱共用的正交局部坐标系。"""

    pair_axis: np.ndarray
    across_axis: np.ndarray
    axial_axis: np.ndarray


@dataclass(frozen=True, slots=True)
class LocalizedGuide:
    """已转换到轴心局部坐标系的单根导柱。"""

    mesh: trimesh.Trimesh
    axis_center_pair_coordinate: float
    radial_center_across_coordinate: float
    axial_min_coordinate: float
    outer_radius_mm: float


def _unit(vector: np.ndarray) -> np.ndarray:
    length = float(np.linalg.norm(vector))
    if length <= 1e-12:
        raise ValueError("无法归一化零向量")
    return vector / length


def _load_pair(path: Path) -> trimesh.Trimesh:
    mesh = trimesh.load_mesh(path, process=True)
    if not isinstance(mesh, trimesh.Trimesh):
        raise ValueError(f"STL 未解析为单个三角网格：{path}")
    components = list(mesh.split(only_watertight=False))
    if len(components) != 2:
        raise ValueError(f"双导柱应包含两个连通分量，实际为 {len(components)}：{path}")
    if not all(component.is_watertight for component in components):
        raise ValueError(f"双导柱包含非封闭分量：{path}")
    return mesh


def _principal_axis(mesh: trimesh.Trimesh) -> np.ndarray:
    covariance = np.cov(np.asarray(mesh.vertices, dtype=float).T)
    values, vectors = np.linalg.eigh(covariance)
    return _unit(vectors[:, int(np.argmax(values))])


def _refine_axial_axis(
    components: tuple[trimesh.Trimesh, trimesh.Trimesh],
) -> np.ndarray:
    """先用 PCA 找长轴，再用端部平面法向消除非对称截面造成的轻微倾斜。"""

    axes = [_principal_axis(component) for component in components]
    if float(np.dot(axes[0], axes[1])) < 0.0:
        axes[1] = -axes[1]
    rough = _unit(axes[0] + axes[1])
    candidates: list[np.ndarray] = []
    weights: list[float] = []
    for component in components:
        for normal, area in zip(
            np.asarray(component.facets_normal, dtype=float),
            np.asarray(component.facets_area, dtype=float),
            strict=True,
        ):
            dot = float(np.dot(normal, rough))
            if abs(dot) < 0.985:
                continue
            candidates.append(normal if dot >= 0.0 else -normal)
            weights.append(float(area))
    if not candidates:
        return rough
    return _unit(np.average(np.asarray(candidates), axis=0, weights=np.asarray(weights)))


def _pair_frame(mesh: trimesh.Trimesh, *, flip_axis: bool = False) -> PairFrame:
    components = tuple(mesh.split(only_watertight=False))
    if len(components) != 2:
        raise ValueError("双导柱局部标架要求恰好两个连通分量")
    axial = _refine_axial_axis((components[0], components[1]))
    if flip_axis:
        axial = -axial
    rough_pair = np.asarray(components[1].centroid) - np.asarray(components[0].centroid)
    rough_pair -= axial * float(np.dot(rough_pair, axial))
    pair = _unit(rough_pair)
    across = _unit(np.cross(axial, pair))
    pair = _unit(np.cross(across, axial))
    return PairFrame(pair, across, axial)


def _component_projections(
    component: trimesh.Trimesh,
    frame: PairFrame,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    vertices = np.asarray(component.vertices, dtype=float)
    return (
        vertices @ frame.pair_axis,
        vertices @ frame.across_axis,
        vertices @ frame.axial_axis,
    )


def _localized_positive_guide(mesh: trimesh.Trimesh, frame: PairFrame) -> LocalizedGuide:
    """选择局部 pair 正侧导柱，并以其圆柱轴心和低端面建立局部坐标。"""

    components = list(mesh.split(only_watertight=False))
    components.sort(key=lambda item: float(np.asarray(item.centroid) @ frame.pair_axis))
    component = components[-1].copy()
    pair, across, axial = _component_projections(component, frame)
    across_center = 0.5 * (float(across.min()) + float(across.max()))
    outer_radius = 0.5 * float(np.ptp(across))
    # 正侧导柱的非开口圆弧位于 pair 正方向，因此 xmax - R 是圆柱轴心。
    axis_center = float(pair.max()) - outer_radius
    axial_min = float(axial.min())
    vertices = np.asarray(component.vertices, dtype=float)
    local_vertices = np.column_stack(
        (
            vertices @ frame.pair_axis - axis_center,
            vertices @ frame.across_axis - across_center,
            vertices @ frame.axial_axis - axial_min,
        )
    )
    component.vertices = local_vertices
    return LocalizedGuide(component, axis_center, across_center, axial_min, outer_radius)


def _pair_dimensions(mesh: trimesh.Trimesh, frame: PairFrame) -> dict[str, float]:
    components = list(mesh.split(only_watertight=False))
    components.sort(key=lambda item: float(np.asarray(item.centroid) @ frame.pair_axis))
    axis_centers: list[float] = []
    for index, component in enumerate(components):
        pair, across, _axial = _component_projections(component, frame)
        radius = 0.5 * float(np.ptp(across))
        axis_centers.append(
            float(pair.min()) + radius if index == 0 else float(pair.max()) - radius
        )
    all_pair = np.asarray(mesh.vertices, dtype=float) @ frame.pair_axis
    return {
        "axis_spacing_mm": axis_centers[1] - axis_centers[0],
        "outer_span_mm": float(np.ptp(all_pair)),
    }


def _canonical_pair(mesh: trimesh.Trimesh, frame: PairFrame) -> trimesh.Trimesh:
    """将完整双导柱刚性变换到以双柱中点为原点的共用局部坐标系。"""

    components = list(mesh.split(only_watertight=False))
    components.sort(key=lambda item: float(np.asarray(item.centroid) @ frame.pair_axis))
    axis_centers: list[float] = []
    for index, component in enumerate(components):
        pair, across, _axial = _component_projections(component, frame)
        radius = 0.5 * float(np.ptp(across))
        axis_centers.append(
            float(pair.min()) + radius if index == 0 else float(pair.max()) - radius
        )
    vertices = np.asarray(mesh.vertices, dtype=float)
    pair_coordinates = vertices @ frame.pair_axis
    across_coordinates = vertices @ frame.across_axis
    axial_coordinates = vertices @ frame.axial_axis
    canonical = mesh.copy()
    canonical.vertices = np.column_stack(
        (
            pair_coordinates - 0.5 * sum(axis_centers),
            across_coordinates
            - 0.5 * (float(across_coordinates.min()) + float(across_coordinates.max())),
            axial_coordinates - float(axial_coordinates.min()),
        )
    )
    return canonical


def _pair_section_gap(
    mesh: trimesh.Trimesh,
    frame: PairFrame,
    axial_coordinate: float,
    axial_origin_coordinate: float,
) -> float:
    plane_origin = frame.axial_axis * (axial_origin_coordinate + axial_coordinate)
    ranges: list[tuple[float, float]] = []
    for component in mesh.split(only_watertight=False):
        section = component.section(plane_origin=plane_origin, plane_normal=frame.axial_axis)
        if section is None or not section.discrete:
            raise ValueError(f"轴向 {axial_coordinate:.3f} mm 未形成有效截面")
        coordinates = np.vstack(section.discrete) @ frame.pair_axis
        ranges.append((float(coordinates.min()), float(coordinates.max())))
    ranges.sort()
    return ranges[1][0] - ranges[0][1]


def _section_paths(mesh: trimesh.Trimesh, height_mm: float) -> tuple[np.ndarray, ...]:
    section = mesh.section(
        plane_origin=np.asarray((0.0, 0.0, height_mm)),
        plane_normal=np.asarray((0.0, 0.0, 1.0)),
    )
    if section is None:
        raise ValueError(f"单柱在 {height_mm:.3f} mm 未形成截面")
    return tuple(np.asarray(path, dtype=float)[:, :2] for path in section.discrete)


def _sample_surface(
    mesh: trimesh.Trimesh,
    sample_count: int,
    rng: np.random.Generator,
) -> np.ndarray:
    areas = np.asarray(mesh.area_faces, dtype=float)
    face_indices = rng.choice(len(areas), sample_count, p=areas / areas.sum())
    triangles = np.asarray(mesh.triangles, dtype=float)[face_indices]
    barycentric = rng.random((sample_count, 2))
    reflected = barycentric.sum(axis=1) > 1.0
    barycentric[reflected] = 1.0 - barycentric[reflected]
    return (
        triangles[:, 0]
        + barycentric[:, 0, None] * (triangles[:, 1] - triangles[:, 0])
        + barycentric[:, 1, None] * (triangles[:, 2] - triangles[:, 0])
    )


def _surface_distances(
    reference: trimesh.Trimesh,
    generated: trimesh.Trimesh,
    sample_count: int,
    seed: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    reference_points = _sample_surface(reference, sample_count, rng)
    generated_points = _sample_surface(generated, sample_count, rng)
    reference_to_generated = cKDTree(generated_points).query(reference_points, workers=-1)[0]
    generated_to_reference = cKDTree(reference_points).query(generated_points, workers=-1)[0]
    return reference_points, generated_points, reference_to_generated, generated_to_reference


def _rms(values: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(values))))


def _distance_summary(values: np.ndarray) -> dict[str, float]:
    return {
        "rms": _rms(values),
        "median": float(np.median(values)),
        "percentile_95": float(np.percentile(values, 95.0)),
        "percentile_99": float(np.percentile(values, 99.0)),
        "maximum_sample": float(values.max()),
    }


def _regional_distance_summary(
    reference_points: np.ndarray,
    generated_points: np.ndarray,
    reference_distances: np.ndarray,
    generated_distances: np.ndarray,
) -> dict[str, dict[str, object]]:
    regions = {
        "top_recess_0.00_0.30_mm": (0.00, 0.30),
        "c_opening_0.30_5.50_mm": (0.30, 5.50),
        "platform_slot_5.50_10.60_mm": (5.50, 10.60),
        "closed_10.60_15.50_mm": (10.60, 15.5002),
    }
    result: dict[str, dict[str, object]] = {}
    for name, (lower, upper) in regions.items():
        reference_selected = reference_distances[
            (reference_points[:, 2] >= lower) & (reference_points[:, 2] < upper)
        ]
        generated_selected = generated_distances[
            (generated_points[:, 2] >= lower) & (generated_points[:, 2] < upper)
        ]
        combined = np.concatenate((reference_selected, generated_selected))
        result[name] = {
            "sample_count": len(combined),
            **_distance_summary(combined),
        }
    return result


def _quick_alignment_score(
    reference: trimesh.Trimesh,
    generated: trimesh.Trimesh,
) -> float:
    _rp, _gp, first, second = _surface_distances(reference, generated, 20_000, 20260815)
    return _rms(np.concatenate((first, second)))


def _configure_matplotlib() -> None:
    plt.rcParams.update(
        {
            "font.sans-serif": ["PingFang SC", "Heiti SC", "Arial Unicode MS", "DejaVu Sans"],
            "axes.unicode_minus": False,
            "figure.dpi": 150,
            "savefig.dpi": 220,
        }
    )


def _draw_comparison(
    output_path: Path,
    reference: trimesh.Trimesh,
    generated: trimesh.Trimesh,
    reference_points: np.ndarray,
    generated_points: np.ndarray,
    reference_distances: np.ndarray,
    generated_distances: np.ndarray,
    section_heights: tuple[float, float, float],
) -> None:
    _configure_matplotlib()
    figure = plt.figure(figsize=(13.2, 8.4), constrained_layout=True)
    grid = figure.add_gridspec(2, 3)
    axes = [figure.add_subplot(grid[row, column]) for row in range(2) for column in range(3)]
    display_count = min(24_000, len(reference_points))
    display_indices = np.linspace(0, len(reference_points) - 1, display_count, dtype=int)
    cap = max(float(np.percentile(reference_distances, 99.0)), 1e-6)

    first = axes[0].scatter(
        reference_points[display_indices, 0],
        reference_points[display_indices, 2],
        c=reference_distances[display_indices],
        s=2.0,
        cmap="viridis",
        vmin=0.0,
        vmax=cap,
        rasterized=True,
    )
    axes[0].set_title("真实表面到生成表面的距离：正视")
    axes[0].set_xlabel("两柱连线方向／毫米")
    axes[0].set_ylabel("轴向／毫米")
    axes[0].set_aspect("equal", adjustable="box")
    figure.colorbar(first, ax=axes[0], label="距离／毫米", shrink=0.82)

    second = axes[1].scatter(
        reference_points[display_indices, 0],
        reference_points[display_indices, 1],
        c=reference_distances[display_indices],
        s=2.0,
        cmap="viridis",
        vmin=0.0,
        vmax=cap,
        rasterized=True,
    )
    axes[1].set_title("真实表面到生成表面的距离：轴向投影")
    axes[1].set_xlabel("开口方向／毫米")
    axes[1].set_ylabel("横向／毫米")
    axes[1].set_aspect("equal", adjustable="box")
    figure.colorbar(second, ax=axes[1], label="距离／毫米", shrink=0.82)

    maximum = max(
        float(np.percentile(reference_distances, 99.8)),
        float(np.percentile(generated_distances, 99.8)),
    )
    bins = np.linspace(0.0, maximum, 70)
    axes[2].hist(
        reference_distances,
        bins=bins,
        density=True,
        histtype="step",
        linewidth=1.8,
        label="真实 → 生成",
    )
    axes[2].hist(
        generated_distances,
        bins=bins,
        density=True,
        histtype="step",
        linewidth=1.8,
        label="生成 → 真实",
    )
    axes[2].set_title("双向最近表面距离分布")
    axes[2].set_xlabel("距离／毫米")
    axes[2].set_ylabel("密度")
    axes[2].legend(frameon=False)
    axes[2].grid(alpha=0.2)

    section_names = ("下部 C 口段", "中部平台槽段", "上部闭合段")
    for axis, height, section_name in zip(axes[3:], section_heights, section_names, strict=True):
        for path_index, path in enumerate(_section_paths(reference, height)):
            axis.plot(
                path[:, 0],
                path[:, 1],
                color="black",
                linewidth=1.6,
                label="真实参考" if path_index == 0 else None,
            )
        for path_index, path in enumerate(_section_paths(generated, height)):
            axis.plot(
                path[:, 0],
                path[:, 1],
                color="#e76f51",
                linewidth=1.3,
                linestyle="--",
                label="生成结果" if path_index == 0 else None,
            )
        axis.set_title(f"{section_name}：距凹槽端 {height:.2f} 毫米")
        axis.set_xlabel("开口方向／毫米")
        axis.set_ylabel("横向／毫米")
        axis.set_aspect("equal", adjustable="box")
        axis.grid(alpha=0.18)
        axis.legend(frameon=False, loc="best")

    figure.suptitle("生成导柱与真实参考：刚性对齐后的几何对比", fontsize=16)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, bbox_inches="tight")
    plt.close(figure)


def _draw_pair_spacing_comparison(
    output_path: Path,
    reference: trimesh.Trimesh,
    generated: trimesh.Trimesh,
    reference_dimensions: dict[str, object],
    generated_dimensions: dict[str, object],
    section_heights: tuple[float, float, float],
) -> None:
    """绘制完整双导柱刚性对齐后的间距叠加图。"""

    _configure_matplotlib()
    figure, axes = plt.subplots(2, 1, figsize=(8.8, 6.4))
    panels = (
        (axes[0], section_heights[0], "甲　C 口段", "D 面净距"),
        (axes[1], section_heights[1], "乙　平台开槽段", "平台端面净距"),
    )
    reference_color = "#203864"
    generated_color = "#a63d40"
    dimension_color = "#374151"
    reference_spacing = float(reference_dimensions["axis_spacing_mm"])
    generated_spacing = float(generated_dimensions["axis_spacing_mm"])
    for panel_index, (axis, height, title, gap_name) in enumerate(panels):
        for path_index, path in enumerate(_section_paths(reference, height)):
            axis.plot(
                path[:, 0],
                path[:, 1],
                color=reference_color,
                linewidth=1.65,
                label="真实双导" if path_index == 0 else None,
            )
        for path_index, path in enumerate(_section_paths(generated, height)):
            axis.plot(
                path[:, 0],
                path[:, 1],
                color=generated_color,
                linewidth=1.35,
                linestyle="--",
                dashes=(4.0, 2.2),
                label="生成双导" if path_index == 0 else None,
            )
        for center in (-0.5 * reference_spacing, 0.5 * reference_spacing):
            axis.plot(
                (center, center),
                (-2.75, 2.75),
                color="#8b95a5",
                linewidth=0.75,
                linestyle=":",
                zorder=0,
            )
        axis.annotate(
            "",
            xy=(-0.5 * reference_spacing, 2.92),
            xytext=(0.5 * reference_spacing, 2.92),
            arrowprops={"arrowstyle": "|-|", "color": dimension_color, "lw": 0.95},
        )
        axis.text(
            0.0,
            3.04,
            f"轴心距　真实 {reference_spacing:.2f}；生成 {generated_spacing:.2f} 毫米",
            ha="center",
            va="bottom",
            fontsize=9.2,
            color=dimension_color,
        )
        key = f"z_{height:.2f}"
        reference_gap = float(reference_dimensions["section_gaps_mm"][key])
        generated_gap = float(generated_dimensions["section_gaps_mm"][key])
        axis.annotate(
            "",
            xy=(-0.5 * reference_gap, 0.0),
            xytext=(0.5 * reference_gap, 0.0),
            arrowprops={"arrowstyle": "|-|", "color": dimension_color, "lw": 0.95},
        )
        axis.text(
            0.0,
            -0.18,
            f"{gap_name}　真实 {reference_gap:.2f}；生成 {generated_gap:.2f} 毫米",
            ha="center",
            va="top",
            fontsize=9.2,
            color=dimension_color,
            bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.94, "pad": 1.2},
        )
        axis.set_title(f"{title}　距凹槽端 {height:.2f} 毫米", loc="left", fontsize=11.2)
        axis.set_xlabel("两柱连线方向（毫米）")
        if panel_index == 0:
            axis.set_ylabel("横向（毫米）")
        axis.set_aspect("equal", adjustable="box")
        axis.set_xlim(-12.2, 12.2)
        axis.set_ylim(-3.0, 3.42)
        axis.set_yticks((-2.0, 0.0, 2.0))
        axis.tick_params(direction="in", length=3.5, width=0.8, labelsize=9)
        axis.grid(color="#d1d5db", linewidth=0.45, alpha=0.45)
        for spine in axis.spines.values():
            spine.set_linewidth(0.8)
            spine.set_color("#4b5563")
    handles, labels = axes[0].get_legend_handles_labels()
    figure.legend(
        handles,
        labels,
        frameon=False,
        loc="lower center",
        bbox_to_anchor=(0.5, 0.005),
        ncol=2,
        fontsize=9.2,
        handlelength=2.8,
    )
    figure.subplots_adjust(left=0.10, right=0.985, top=0.965, bottom=0.105, hspace=0.42)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, bbox_inches="tight", facecolor="white")
    plt.close(figure)


def _mesh_summary(mesh: trimesh.Trimesh) -> dict[str, object]:
    return {
        "vertices": len(mesh.vertices),
        "faces": len(mesh.faces),
        "watertight": bool(mesh.is_watertight),
        "winding_consistent": bool(mesh.is_winding_consistent),
        "volume_mm3": float(mesh.volume),
        "area_mm2": float(mesh.area),
        "bounds_mm": np.asarray(mesh.bounds, dtype=float).tolist(),
    }


def compare(
    reference_path: Path,
    generated_path: Path,
    output_directory: Path,
    sample_count: int,
    seed: int,
    section_heights: tuple[float, float, float],
    figure_output: Path | None = None,
    pair_figure_output: Path | None = None,
) -> dict[str, object]:
    reference_pair = _load_pair(reference_path)
    generated_pair = _load_pair(generated_path)
    reference_frame = _pair_frame(reference_pair)
    reference_guide = _localized_positive_guide(reference_pair, reference_frame)

    generated_candidates: list[tuple[float, bool, PairFrame, LocalizedGuide]] = []
    for flipped in (False, True):
        frame = _pair_frame(generated_pair, flip_axis=flipped)
        guide = _localized_positive_guide(generated_pair, frame)
        score = _quick_alignment_score(reference_guide.mesh, guide.mesh)
        generated_candidates.append((score, flipped, frame, guide))
    _score, generated_axis_flipped, generated_frame, generated_guide = min(
        generated_candidates, key=lambda item: item[0]
    )

    reference_points, generated_points, reference_distances, generated_distances = (
        _surface_distances(
            reference_guide.mesh,
            generated_guide.mesh,
            sample_count,
            seed,
        )
    )
    combined_distances = np.concatenate((reference_distances, generated_distances))
    combined_distance_summary = _distance_summary(combined_distances)

    reference_pair_dimensions = _pair_dimensions(reference_pair, reference_frame)
    generated_pair_dimensions = _pair_dimensions(generated_pair, generated_frame)
    reference_pair_dimensions["section_gaps_mm"] = {
        f"z_{height:.2f}": _pair_section_gap(
            reference_pair,
            reference_frame,
            height,
            reference_guide.axial_min_coordinate,
        )
        for height in section_heights
    }
    generated_pair_dimensions["section_gaps_mm"] = {
        f"z_{height:.2f}": _pair_section_gap(
            generated_pair,
            generated_frame,
            height,
            generated_guide.axial_min_coordinate,
        )
        for height in section_heights
    }
    reference_pair_aligned = _canonical_pair(reference_pair, reference_frame)
    generated_pair_aligned = _canonical_pair(generated_pair, generated_frame)
    dimension_differences = {
        "axis_spacing_mm": (
            generated_pair_dimensions["axis_spacing_mm"]
            - reference_pair_dimensions["axis_spacing_mm"]
        ),
        "outer_span_mm": (
            generated_pair_dimensions["outer_span_mm"] - reference_pair_dimensions["outer_span_mm"]
        ),
        "section_gaps_mm": {
            key: generated_pair_dimensions["section_gaps_mm"][key]
            - reference_pair_dimensions["section_gaps_mm"][key]
            for key in reference_pair_dimensions["section_gaps_mm"]
        },
    }

    output_directory.mkdir(parents=True, exist_ok=True)
    figure_path = (
        output_directory / "aligned-generated-vs-reference.png"
        if figure_output is None
        else figure_output
    )
    _draw_comparison(
        figure_path,
        reference_guide.mesh,
        generated_guide.mesh,
        reference_points,
        generated_points,
        reference_distances,
        generated_distances,
        section_heights,
    )
    pair_figure_path = (
        output_directory / "aligned-pair-spacing.png"
        if pair_figure_output is None
        else pair_figure_output
    )
    _draw_pair_spacing_comparison(
        pair_figure_path,
        reference_pair_aligned,
        generated_pair_aligned,
        reference_pair_dimensions,
        generated_pair_dimensions,
        section_heights,
    )

    result: dict[str, object] = {
        "reference_stl": str(reference_path.resolve()),
        "generated_stl": str(generated_path.resolve()),
        "comparison_scope": (
            "complete-pair spacing after one rigid transform per pair, plus one corresponding "
            "guide shape in the same intrinsic frame"
        ),
        "alignment": {
            "reference_pair_axis": reference_frame.pair_axis.tolist(),
            "reference_across_axis": reference_frame.across_axis.tolist(),
            "reference_axial_axis": reference_frame.axial_axis.tolist(),
            "generated_pair_axis": generated_frame.pair_axis.tolist(),
            "generated_across_axis": generated_frame.across_axis.tolist(),
            "generated_axial_axis": generated_frame.axial_axis.tolist(),
            "generated_axis_flipped_for_matching": generated_axis_flipped,
        },
        "mesh": {
            "reference_guide": _mesh_summary(reference_guide.mesh),
            "generated_guide": _mesh_summary(generated_guide.mesh),
            "volume_difference_percent": float(
                100.0 * (generated_guide.mesh.volume / reference_guide.mesh.volume - 1.0)
            ),
        },
        "pair_dimensions": {
            "reference": reference_pair_dimensions,
            "generated": generated_pair_dimensions,
            "generated_minus_reference": dimension_differences,
        },
        "surface_distance_mm": {
            "method": (
                "bidirectional nearest-neighbour distances on deterministic uniform "
                "surface samples after intrinsic rigid-frame alignment"
            ),
            "sample_count_per_mesh": sample_count,
            "reference_to_generated_rms": _rms(reference_distances),
            "generated_to_reference_rms": _rms(generated_distances),
            "symmetric_rms": combined_distance_summary["rms"],
            "median": combined_distance_summary["median"],
            "percentile_95": combined_distance_summary["percentile_95"],
            "percentile_99": combined_distance_summary["percentile_99"],
            "maximum_sample": combined_distance_summary["maximum_sample"],
            "regions": _regional_distance_summary(
                reference_points,
                generated_points,
                reference_distances,
                generated_distances,
            ),
        },
        "outputs": {
            "figure": str(figure_path.resolve()),
            "pair_spacing_figure": str(pair_figure_path.resolve()),
        },
    }
    report_path = output_directory / "aligned-generated-vs-reference.json"
    result["outputs"]["report"] = str(report_path.resolve())  # type: ignore[index]
    report_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference_stl", type=Path)
    parser.add_argument("generated_stl", type=Path)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument(
        "--figure-output",
        type=Path,
        help="可选的图像输出路径；用于把同一张验证图写入文档目录",
    )
    parser.add_argument(
        "--pair-figure-output",
        type=Path,
        help="可选的完整双导间距叠加图输出路径",
    )
    parser.add_argument("--sample-count", type=int, default=200_000)
    parser.add_argument("--seed", type=int, default=20260815)
    parser.add_argument(
        "--section-heights",
        type=float,
        nargs=3,
        default=(2.75, 8.00, 13.00),
        metavar=("C_OPENING", "PLATFORM", "CLOSED"),
    )
    arguments = parser.parse_args()
    if arguments.sample_count < 10_000:
        parser.error("--sample-count 不得小于 10000")
    if not all(math.isfinite(value) and value > 0.0 for value in arguments.section_heights):
        parser.error("--section-heights 必须是三个正有限数")
    result = compare(
        arguments.reference_stl,
        arguments.generated_stl,
        arguments.output_directory,
        arguments.sample_count,
        arguments.seed,
        tuple(arguments.section_heights),
        arguments.figure_output,
        arguments.pair_figure_output,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

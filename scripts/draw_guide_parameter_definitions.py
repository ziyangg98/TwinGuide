"""从病例 YAML 绘制导柱定位、单柱参数和双柱间距三张示意图。"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import matplotlib.pyplot as plt
import yaml
from matplotlib.axes import Axes
from matplotlib.patches import Arc, Circle, Ellipse, Polygon, Rectangle

from twin_guide.guide_post_positioning import DRILL_LENGTH_INSIDE_HANDPIECE_MM
from twin_guide.sleeve_estimation.c_opening import rounded_c_opening_slot_profile

BODY_COLOR = "#dfe7f0"
BODY_EDGE = "#203864"
PLATFORM_COLOR = "#eee2d2"
PLATFORM_EDGE = "#8a6742"
DIMENSION_COLOR = "#374151"
ACCENT_COLOR = "#a63d40"
GUIDE_COLOR = "#8b95a5"


def _configure_matplotlib() -> None:
    plt.rcParams.update(
        {
            "font.sans-serif": [
                "PingFang SC",
                "Heiti SC",
                "Arial Unicode MS",
                "DejaVu Sans",
            ],
            "axes.unicode_minus": False,
            "font.size": 9.5,
            "axes.titleweight": "regular",
            "figure.dpi": 150,
            "savefig.dpi": 220,
        }
    )


def _read_parameters(config_path: Path) -> dict[str, float]:
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    sleeve = raw.get("runtime", {}).get("sleeve") if isinstance(raw, dict) else None
    if not isinstance(sleeve, dict):
        raise ValueError(f"{config_path} 缺少 runtime.sleeve")
    required = {
        "inner_diameter_mm",
        "outer_diameter_mm",
        "top_recess_diameter_mm",
        "top_recess_depth_mm",
        "height_mm",
        "platform_slot_width_mm",
        "platform_overhang_mm",
        "platform_height_mm",
        "closed_bore_height_mm",
        "inner_arc_angle_degrees",
        "outer_arc_angle_degrees",
        "guide_spacing_mm",
    }
    missing = sorted(required - sleeve.keys())
    if missing:
        raise ValueError(f"runtime.sleeve 缺少参数：{', '.join(missing)}")
    parameters = {name: float(sleeve[name]) for name in required}
    guide_posts = raw.get("planning", {}).get("guide_posts", [])
    if not isinstance(guide_posts, list) or not guide_posts or not isinstance(guide_posts[0], dict):
        raise ValueError(f"{config_path} 缺少 planning.guide_posts[0]")
    first_post = guide_posts[0]
    for name in ("drill_length_mm", "implant_length_mm", "sleeve_template_extension_mm"):
        if name not in first_post:
            raise ValueError(f"planning.guide_posts[0] 缺少 {name}")
    parameters["template_extension_mm"] = float(first_post["sleeve_template_extension_mm"])
    parameters["twin_extension_mm"] = (
        float(first_post["drill_length_mm"])
        - DRILL_LENGTH_INSIDE_HANDPIECE_MM
        - float(first_post["implant_length_mm"])
    )
    return parameters


def _dimension(
    axis: Axes,
    start: tuple[float, float],
    end: tuple[float, float],
    label: str,
    *,
    text_offset: tuple[float, float] = (0.0, 0.0),
) -> None:
    axis.annotate(
        "",
        xy=end,
        xytext=start,
        arrowprops={"arrowstyle": "|-|", "color": DIMENSION_COLOR, "lw": 0.95},
    )
    midpoint = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
    axis.text(
        midpoint[0] + text_offset[0],
        midpoint[1] + text_offset[1],
        label,
        ha="center",
        va="center",
        fontsize=9.2,
        color=DIMENSION_COLOR,
    )


def _save(figure: plt.Figure, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, bbox_inches="tight", facecolor="white")
    plt.close(figure)


def draw_positioning(output_path: Path, p: dict[str, float]) -> None:
    """绘制圆环到双导导柱止停位置的定位关系。"""

    figure, axis = plt.subplots(figsize=(8.8, 6.4))
    axis.add_patch(
        Ellipse(
            (0.0, 0.0),
            4.6,
            1.25,
            facecolor=BODY_COLOR,
            edgecolor=BODY_EDGE,
            linewidth=1.35,
        )
    )
    axis.plot([-2.3, 2.3], [0.0, 0.0], color=GUIDE_COLOR, linewidth=0.8)
    axis.scatter([0.0], [0.0], s=22, color=BODY_EDGE, zorder=4)
    axis.text(-2.50, 0.62, "传统模板定位圆环", fontsize=9.5)
    axis.text(-2.50, -0.68, "圆环上平面中心", fontsize=8.8, color=DIMENSION_COLOR)
    axis.annotate(
        "",
        xy=(0.0, 3.25),
        xytext=(0.0, 0.0),
        arrowprops={"arrowstyle": "->", "color": DIMENSION_COLOR, "lw": 1.0},
    )
    axis.text(0.20, 3.00, "圆环外法向", fontsize=8.8, color=DIMENSION_COLOR)
    axis.annotate(
        "",
        xy=(0.0, -3.55),
        xytext=(0.0, 0.0),
        arrowprops={"arrowstyle": "->", "color": DIMENSION_COLOR, "lw": 1.0},
    )
    axis.text(0.20, -3.38, "导柱轴向（与外法向相反）", fontsize=8.8, color=DIMENSION_COLOR)
    template_extension = p["template_extension_mm"]
    twin_extension = p["twin_extension_mm"]
    drawing_scale = 3.0 / max(template_extension, twin_extension)
    implant_y = -template_extension * drawing_scale
    stop_y = implant_y + twin_extension * drawing_scale
    axis.scatter([0.0], [implant_y], s=28, color=ACCENT_COLOR, zorder=5)
    axis.plot([-2.85, 2.85], [stop_y, stop_y], color=PLATFORM_EDGE, linewidth=1.35)
    axis.text(0.20, implant_y, "植体顶端", va="center", fontsize=8.8)
    axis.text(2.98, stop_y, "双导止停平面", va="center", fontsize=8.8)
    _dimension(
        axis,
        (-3.35, 0.0),
        (-3.35, implant_y),
        f"模板延伸长度\n{template_extension:.2f} 毫米",
        text_offset=(-0.68, 0.0),
    )
    _dimension(
        axis,
        (3.35, implant_y),
        (3.35, stop_y),
        f"双导延伸长度\n{twin_extension:.2f} 毫米",
        text_offset=(0.68, 0.0),
    )
    for x in (-1.25, 1.25):
        axis.add_patch(
            Rectangle(
                (x - 0.34, stop_y - 0.05),
                0.68,
                1.85,
                facecolor=BODY_COLOR,
                edgecolor=BODY_EDGE,
                linewidth=1.2,
            )
        )
    axis.annotate(
        "",
        xy=(2.55, 2.42),
        xytext=(-2.55, 2.42),
        arrowprops={"arrowstyle": "|-|", "color": DIMENSION_COLOR, "lw": 0.95},
    )
    axis.text(0.0, 2.58, "双柱排列方向", ha="center", fontsize=8.8)
    axis.text(
        4.62,
        -4.02,
        "双导延伸长度＝钻针总长－植体长度－手机内钻针固定长度（12.00 毫米）",
        ha="right",
        fontsize=8.2,
        color=DIMENSION_COLOR,
    )
    axis.set_title("导柱轴向定位", loc="left", fontsize=11.5, pad=8)
    axis.set_xlim(-4.75, 4.75)
    axis.set_ylim(min(-4.25, implant_y - 1.15), max(3.55, stop_y + 2.25))
    axis.set_aspect("equal")
    axis.axis("off")
    figure.subplots_adjust(left=0.035, right=0.965, top=0.94, bottom=0.04)
    _save(figure, output_path)


def _draw_axial(axis: Axes, p: dict[str, float]) -> None:
    height = p["height_mm"]
    platform_height = p["platform_height_mm"]
    closed_height = p["closed_bore_height_mm"]
    platform_start = height - platform_height
    closed_start = height - closed_height

    body_left = 1.20
    body_right = 3.40
    body_center = 0.5 * (body_left + body_right)
    bore_width = (body_right - body_left) * p["inner_diameter_mm"] / p["outer_diameter_mm"]
    bore_left = body_center - 0.5 * bore_width
    bore_right = body_center + 0.5 * bore_width
    platform_end = 3.78

    # 先画实体纵剖面：蓝色为圆柱主体，橙色为平台，白色为中心导孔/C 口。
    axis.add_patch(
        Rectangle(
            (body_left, 0.0),
            body_right - body_left,
            height,
            facecolor=BODY_COLOR,
            edgecolor=BODY_EDGE,
            lw=1.35,
            zorder=2,
        )
    )
    axis.add_patch(
        Rectangle(
            (body_right, platform_start),
            platform_end - body_right,
            platform_height,
            facecolor=PLATFORM_COLOR,
            edgecolor=PLATFORM_EDGE,
            lw=1.15,
            zorder=2,
        )
    )

    # 中心孔贯穿全高；C 口在下两段向右开放，闭合段只保留中心孔。
    axis.add_patch(
        Rectangle(
            (bore_left, 0.0),
            bore_right - bore_left,
            height,
            facecolor="white",
            edgecolor=GUIDE_COLOR,
            lw=0.9,
            zorder=3,
        )
    )
    axis.add_patch(
        Rectangle(
            (bore_right, 0.0),
            body_right - bore_right,
            platform_start,
            facecolor="white",
            edgecolor="none",
            zorder=3,
        )
    )
    axis.add_patch(
        Rectangle(
            (bore_right, platform_start),
            platform_end - bore_right,
            closed_start - platform_start,
            facecolor="white",
            edgecolor=GUIDE_COLOR,
            lw=0.9,
            zorder=3,
        )
    )
    axis.plot(
        [body_right, platform_end, platform_end],
        [platform_start, platform_start, height],
        color=PLATFORM_EDGE,
        lw=1.15,
        zorder=4,
    )

    recess_width = (body_right - body_left) * p["top_recess_diameter_mm"] / p["outer_diameter_mm"]
    recess_left = body_center - 0.5 * recess_width
    recess_right = body_center + 0.5 * recess_width
    axis.add_patch(
        Polygon(
            (
                (recess_left, 0.0),
                (recess_right, 0.0),
                (bore_right, p["top_recess_depth_mm"]),
                (bore_left, p["top_recess_depth_mm"]),
            ),
            closed=True,
            facecolor="white",
            edgecolor=ACCENT_COLOR,
            lw=1.3,
            zorder=5,
        )
    )

    # 三条分界线贯穿实体、尺寸和区段标签，使范围可以直接读出。
    for value in (0.0, platform_start, closed_start, height):
        axis.plot(
            [0.65, 8.55],
            [value, value],
            color=GUIDE_COLOR,
            ls="--" if value not in (0.0, height) else "-",
            lw=0.75,
            zorder=1,
        )

    _dimension(
        axis,
        (0.28, 0.0),
        (0.28, height),
        f"总高\n{height:.2f} 毫米",
        text_offset=(-0.62, 0.0),
    )
    axis.annotate(
        "",
        xy=(4.08, height),
        xytext=(4.08, platform_start),
        arrowprops={"arrowstyle": "|-|", "color": DIMENSION_COLOR, "lw": 0.95},
    )
    axis.text(
        4.42,
        0.5 * (platform_start + height),
        f"平台总高度 {platform_height:.2f} 毫米",
        ha="center",
        va="center",
        rotation=90,
        fontsize=8.8,
        color=DIMENSION_COLOR,
    )
    _dimension(
        axis,
        (8.85, closed_start),
        (8.85, height),
        f"闭合段高度\n{closed_height:.2f} 毫米",
        text_offset=(0.72, 0.0),
    )

    segment_x = 6.4
    segment_width = 2.15
    segments = (
        (0.0, platform_start, "① C 口段", f"距凹槽端 0～{platform_start:.2f} 毫米"),
        (
            platform_start,
            closed_start,
            "② 平台开槽段",
            f"距凹槽端 {platform_start:.2f}～{closed_start:.2f} 毫米",
        ),
        (
            closed_start,
            height,
            "③ C 口闭合段",
            f"距凹槽端 {closed_start:.2f}～{height:.2f} 毫米",
        ),
    )
    segment_colors = ("#edf2f7", "#f5efe7", "#f3f4f6")
    for (bottom, top, name, interval), color in zip(segments, segment_colors, strict=True):
        axis.add_patch(
            Rectangle(
                (segment_x, bottom),
                segment_width,
                top - bottom,
                facecolor=color,
                edgecolor=GUIDE_COLOR,
                lw=0.85,
                zorder=1,
            )
        )
        midpoint = 0.5 * (bottom + top)
        axis.text(
            segment_x + 0.16,
            midpoint + 0.34,
            name,
            va="center",
            fontsize=9.5,
            fontweight="medium",
        )
        axis.text(
            segment_x + 0.16,
            midpoint - 0.34,
            interval,
            va="center",
            fontsize=8.2,
            color=DIMENSION_COLOR,
        )

    boundary_labels = (
        (0.0, "凹槽端：0.00 毫米"),
        (platform_start, f"平台起点：{platform_start:.2f} 毫米"),
        (closed_start, f"闭合段起点：{closed_start:.2f} 毫米"),
        (height, f"闭合端：{height:.2f} 毫米"),
    )
    for value, label in boundary_labels:
        axis.text(
            4.95,
            value,
            label,
            ha="left",
            va="center",
            fontsize=8.2,
            color=DIMENSION_COLOR,
            bbox={"facecolor": "white", "edgecolor": "none", "pad": 0.8},
        )

    axis.annotate(
        f"端部凹槽\n直径 {p['top_recess_diameter_mm']:.2f} 毫米，"
        f"深度 {p['top_recess_depth_mm']:.2f} 毫米",
        xy=(recess_left, p["top_recess_depth_mm"]),
        xytext=(3.15, -1.05),
        arrowprops={"arrowstyle": "->", "color": DIMENSION_COLOR},
        ha="center",
        fontsize=8.2,
    )
    axis.text(
        body_center,
        height + 0.55,
        "纵向剖面（白色为贯穿中心孔）",
        ha="center",
        fontsize=8.4,
        color=DIMENSION_COLOR,
    )
    axis.set_title("甲　轴向分段与端部凹槽", loc="left", fontsize=11.2, pad=6)
    axis.set_xlim(-0.85, 9.65)
    axis.set_ylim(-1.45, height + 1.15)
    axis.axis("off")


def _draw_section(axis: Axes, p: dict[str, float]) -> None:
    inner_radius = p["inner_diameter_mm"] / 2.0
    outer_radius = p["outer_diameter_mm"] / 2.0
    platform_end = outer_radius + p["platform_overhang_mm"]
    slot_half_width = p["platform_slot_width_mm"] / 2.0
    outer_gap_half = 0.5 * (360.0 - p["outer_arc_angle_degrees"])
    inner_gap_half = 0.5 * (360.0 - p["inner_arc_angle_degrees"])
    outer_cut = outer_radius * math.cos(math.radians(outer_gap_half))
    axis.add_patch(
        Rectangle(
            (0.0, -outer_radius),
            platform_end,
            2.0 * outer_radius,
            facecolor=PLATFORM_COLOR,
            edgecolor=PLATFORM_EDGE,
            lw=1.15,
        )
    )
    axis.add_patch(
        Circle((0.0, 0.0), outer_radius, facecolor=BODY_COLOR, edgecolor=BODY_EDGE, lw=1.35)
    )
    axis.add_patch(
        Circle((0.0, 0.0), inner_radius, facecolor="white", edgecolor=GUIDE_COLOR, lw=0.95)
    )
    axis.add_patch(
        Polygon(
            rounded_c_opening_slot_profile(
                inner_radius,
                slot_half_width,
                outer_cut,
                platform_end + 0.5,
            ),
            closed=True,
            facecolor="white",
            edgecolor=GUIDE_COLOR,
            lw=0.95,
        )
    )
    axis.add_patch(
        Arc(
            (0.0, 0.0),
            2.0 * outer_radius,
            2.0 * outer_radius,
            theta1=outer_gap_half,
            theta2=360.0 - outer_gap_half,
            color=ACCENT_COLOR,
            lw=1.25,
        )
    )
    axis.add_patch(
        Arc(
            (0.0, 0.0),
            2.0 * inner_radius,
            2.0 * inner_radius,
            theta1=inner_gap_half,
            theta2=360.0 - inner_gap_half,
            color=ACCENT_COLOR,
            lw=1.25,
        )
    )
    _dimension(
        axis,
        (-outer_radius, -3.25),
        (outer_radius, -3.25),
        f"外径 {p['outer_diameter_mm']:.2f} 毫米",
        text_offset=(0.0, -0.35),
    )
    _dimension(
        axis,
        (-inner_radius, 3.25),
        (inner_radius, 3.25),
        f"内径 {p['inner_diameter_mm']:.2f} 毫米",
        text_offset=(0.0, 0.35),
    )
    _dimension(
        axis,
        (3.25, -slot_half_width),
        (3.25, slot_half_width),
        f"槽宽 {p['platform_slot_width_mm']:.2f} 毫米",
        text_offset=(0.75, 0.0),
    )
    axis.annotate(
        f"平台凸出 {p['platform_overhang_mm']:.2f} 毫米",
        xy=(platform_end, -2.3),
        xytext=(3.0, -2.9),
        arrowprops={"arrowstyle": "->", "color": DIMENSION_COLOR},
        fontsize=9,
    )
    axis.text(
        -3.45,
        -4.05,
        f"内弧角 {p['inner_arc_angle_degrees']:.2f}°    外弧角 {p['outer_arc_angle_degrees']:.2f}°",
        fontsize=8.8,
    )
    axis.text(
        -3.45,
        -4.62,
        "平台直槽与中心孔之间采用两端相切的浅圆角",
        fontsize=8.8,
        color=DIMENSION_COLOR,
    )
    axis.set_title("乙　平台开槽段横截面", loc="left", fontsize=11.2, pad=6)
    axis.set_xlim(-3.65, 4.55)
    axis.set_ylim(-5.05, 3.85)
    axis.set_aspect("equal")
    axis.axis("off")


def draw_single_parameters(output_path: Path, parameters: dict[str, float]) -> None:
    """绘制一根导柱的 11 项形状参数和轴向分段。"""

    figure, axes = plt.subplots(
        2,
        1,
        figsize=(8.8, 9.4),
        gridspec_kw={"height_ratios": (1.0, 1.0)},
    )
    _draw_axial(axes[0], parameters)
    _draw_section(axes[1], parameters)
    figure.subplots_adjust(left=0.04, right=0.97, top=0.975, bottom=0.035, hspace=0.18)
    _save(figure, output_path)


def draw_pair_spacing(output_path: Path, p: dict[str, float]) -> None:
    """绘制同一种植位一对导柱的四个横向尺寸。"""

    radius = p["outer_diameter_mm"] / 2.0
    gap = p["guide_spacing_mm"]
    axis_spacing = gap + 2.0 * (radius + p["platform_overhang_mm"])
    outer_span = axis_spacing + p["outer_diameter_mm"]
    outer_d_face = radius * math.cos(math.radians(0.5 * (360.0 - p["outer_arc_angle_degrees"])))
    c_gap = axis_spacing - 2.0 * outer_d_face
    centers = (-axis_spacing / 2.0, axis_spacing / 2.0)
    faces = (-gap / 2.0, gap / 2.0)
    figure, axis = plt.subplots(figsize=(8.8, 4.8))
    outer_gap_half = 0.5 * (360.0 - p["outer_arc_angle_degrees"])
    outer_d_y = radius * math.sin(math.radians(outer_gap_half))
    slot_profile = rounded_c_opening_slot_profile(
        p["inner_diameter_mm"] / 2.0,
        p["platform_slot_width_mm"] / 2.0,
        outer_d_face,
        radius + p["platform_overhang_mm"] + 0.5,
    )
    d_faces = []
    for center, inward in ((centers[0], 1.0), (centers[1], -1.0)):
        axis.add_patch(
            Circle((center, 0.0), radius, facecolor=BODY_COLOR, edgecolor=BODY_EDGE, lw=1.35)
        )
        x = center if inward > 0 else faces[1]
        width = faces[0] - center if inward > 0 else center - faces[1]
        axis.add_patch(
            Rectangle(
                (x, -1.55), width, 3.10, facecolor=PLATFORM_COLOR, edgecolor=PLATFORM_EDGE, lw=1.15
            )
        )
        axis.add_patch(
            Polygon(
                [(center + inward * point_x, point_y) for point_x, point_y in slot_profile],
                closed=True,
                facecolor="white",
                edgecolor=ACCENT_COLOR,
                lw=1.1,
                zorder=4,
            )
        )
        d_face_x = center + inward * outer_d_face
        d_faces.append(d_face_x)
        axis.plot(
            [d_face_x, d_face_x],
            [-outer_d_y, outer_d_y],
            color=ACCENT_COLOR,
            lw=1.35,
            zorder=5,
        )
        axis.plot(
            [center, center],
            [-2.85, 2.85],
            color=GUIDE_COLOR,
            linestyle=":",
            linewidth=0.8,
            zorder=0,
        )
        axis.annotate(
            "C 口向内",
            xy=(center + inward * 1.3, 0.0),
            xytext=(center, 2.72),
            ha="center",
            arrowprops={"arrowstyle": "->", "color": ACCENT_COLOR, "lw": 0.9},
            fontsize=8.4,
        )
    _dimension(
        axis,
        (centers[0], 3.35),
        (centers[1], 3.35),
        f"轴心距  {axis_spacing:.2f} 毫米",
        text_offset=(0.0, 0.30),
    )
    _dimension(
        axis,
        (d_faces[0], -3.05),
        (d_faces[1], -3.05),
        f"C 口 D 面净距  {c_gap:.2f} 毫米",
        text_offset=(0.0, -0.30),
    )
    _dimension(
        axis,
        (faces[0], -4.20),
        (faces[1], -4.20),
        f"平台端面净距  {gap:.2f} 毫米",
        text_offset=(0.0, -0.30),
    )
    _dimension(
        axis,
        (centers[0] - radius, -5.40),
        (centers[1] + radius, -5.40),
        f"双柱外侧总宽  {outer_span:.2f} 毫米",
        text_offset=(0.0, -0.30),
    )
    axis.text(
        0.0,
        -6.35,
        "导柱间距仅表示两个相向平台端面之间的净距",
        ha="center",
        fontsize=8.6,
        color=DIMENSION_COLOR,
    )
    axis.set_title("双柱横向尺寸", loc="left", fontsize=11.5, pad=8)
    axis.set_xlim(-0.5 * outer_span - 1.0, 0.5 * outer_span + 1.0)
    axis.set_ylim(-6.75, 4.20)
    axis.set_aspect("equal")
    axis.axis("off")
    figure.subplots_adjust(left=0.025, right=0.975, top=0.94, bottom=0.04)
    _save(figure, output_path)


def draw_all(config_path: Path, output_directory: Path) -> tuple[Path, Path, Path]:
    """读取标准参数并输出三张图。"""

    _configure_matplotlib()
    parameters = _read_parameters(config_path)
    outputs = (
        output_directory / "guide-positioning.png",
        output_directory / "guide-single-parameters.png",
        output_directory / "guide-pair-spacing.png",
    )
    draw_positioning(outputs[0], parameters)
    draw_single_parameters(outputs[1], parameters)
    draw_pair_spacing(outputs[2], parameters)
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("examples/case.example.yaml"))
    parser.add_argument("--output-directory", type=Path, default=Path("docs/images"))
    arguments = parser.parse_args()
    for output in draw_all(arguments.config, arguments.output_directory):
        print(output)


if __name__ == "__main__":
    main()

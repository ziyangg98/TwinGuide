"""七阶段运行结果的统一 JSON 输出。"""

from __future__ import annotations

import json
import shutil
from dataclasses import fields, is_dataclass, replace
from enum import Enum
from pathlib import Path

from twin_guide.config import CaseConfig
from twin_guide.types import GenerationProcessResult, StageResult, StageRunStatus

STAGE_RESULT_SCHEMA = "twin-guide.stage-result/1.0"
STAGE_ARTIFACT_STEMS = {
    1: "stage-01-sleeve-reconstruction",
    2: "stage-02-tooth-mapping",
    3: "stage-03-cutout-planning",
    4: "stage-04-anchor-selection",
    5: "stage-05-press-beam",
    6: "stage-06-structure-linking",
    7: "stage-07-clearance-adjustment",
}

STAGE_LEGENDS = {
    1: ("传统模板：蓝色", "标准重建导柱：灰色"),
    2: ("实测牙冠投影与 FDI 中心", "牙弓距离与观察窗范围"),
    3: ("蓝色：已完成导孔与窗口切除的牙科导板", "灰色：标准重建导管"),
    4: ("导管锚点：红色", "导板锚点：黄色", "射线/轨迹：黄色细管"),
    5: ("按压梁：金色", "三锚点与 Y 型汇合关系"),
    6: ("连续梁架：金色", "导管：灰色", "导板：蓝色"),
    7: (
        "半透明棕色：手机摆动包络",
        "黑色：旋转轴",
        "红色：枢轴",
    ),
}


def _json_value(value: object) -> object:
    """将阶段类型转换为稳定、可读的 JSON 值。"""

    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Path):
        return str(value.resolve())
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_value(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _json_value(getattr(value, field.name))
            for field in fields(value)
            # Blender 对象只是第 1 阶段的运行时载体，不是业务结果。
            if field.name not in {"guide_mesh", "mapping_report"}
        }
    raise TypeError(f"阶段结果包含不可序列化类型：{type(value).__name__}")


def _stage_parameters(config: CaseConfig, stage_number: int) -> object:
    """返回该阶段实际消费的主要配置。"""

    if stage_number == 1:
        sleeve_by_ring = []
        for item in config.guide_posts:
            parameters = item.resolved_sleeve(config.sleeve)
            editor_overrides = getattr(config, "editor_overrides", None)
            editor_override = (
                editor_overrides.sleeve_for(item.ring_index)
                if editor_overrides is not None
                else None
            )
            if editor_override is not None:
                parameters = replace(
                    parameters,
                    height_mm=editor_override.height_mm,
                    platform_height_mm=editor_override.platform_height_mm,
                    closed_bore_height_mm=editor_override.closed_bore_height_mm,
                )
            sleeve_by_ring.append(
                {"ring_index": item.ring_index, "parameters": _json_value(parameters)}
            )
        return {
            "sleeve_defaults": _json_value(config.sleeve),
            "sleeve_by_ring": sleeve_by_ring,
        }
    if stage_number == 3:
        return {
            "windows": _json_value(config.windows),
            "channel_axial_margin_mm": config.geometry.channel_axial_margin_mm,
        }
    if stage_number == 4:
        return {
            "guide_anchors": _json_value(config.guide_anchors),
            "connector_diameter_mm": config.geometry.connector_diameter_mm,
        }
    if stage_number == 5:
        return _json_value(config.press_beam)
    if stage_number == 6:
        return {
            "connector_diameter_mm": config.geometry.connector_diameter_mm,
            "connector_dental_clearance_mm": (config.geometry.connector_dental_clearance_mm),
            "fusion_voxel_size_mm": config.geometry.fusion_voxel_size_mm,
        }
    if stage_number == 7:
        return _json_value(config.handpiece_avoidance)
    return {}


def _stage_metrics(stage: StageResult) -> dict[str, object]:
    """从类型化结果提取可直接审核的少量指标。"""

    output = stage.output
    if stage.status is StageRunStatus.SKIPPED or output is None:
        return {}
    number = stage.definition.number
    if number == 1:
        sleeves = output.sleeves
        return {
            "sleeve_count": len(sleeves),
            "guide_indices": [sleeve.guide_index for sleeve in sleeves],
            "lengths_mm": [round(sleeve.length_mm, 3) for sleeve in sleeves],
            "bore_diameters_mm": [round(2.0 * sleeve.bore_radius_mm, 3) for sleeve in sleeves],
        }
    if number == 2:
        return {
            "present_tooth_count": len(output.present_teeth),
            "present_fdi": list(output.present_teeth),
            "missing_fdi": list(output.missing_teeth),
            "observation_window_count": len(output.windows),
        }
    if number == 3:
        metrics = {
            "channel_count": len(output.channels),
            "operation_window_count": len(output.windows),
            "profile_window_count": len(output.profile_windows),
            "profile_window_ids": [
                window_id for window in output.profile_windows for window_id in window.window_ids
            ],
        }
        if output.profile_windows:
            report = json.loads(output.profile_windows[0].report_path.read_text(encoding="utf-8"))
            geometry = report["geometry"]
            solution = report.get("constraint_solution", {})
            depths = [
                float(window["exterior_wall_sampling"]["calculated_axis_core_depth_mm"])
                for window in report.get("windows", [])
            ]
            metrics.update(
                {
                    "removed_guide_volume_mm3": round(float(geometry["removed_volume_mm3"]), 3),
                    "minimum_axis_clearance_mm": round(
                        float(geometry["minimum_removed_axis_clearance_mm"]), 3
                    ),
                    "constraint_solution_mode": solution.get("mode"),
                    "maximum_inner_depth_mm": round(max(depths), 3) if depths else None,
                }
            )
        return metrics
    if number == 4:
        return {
            "sleeve_anchor_selection_count": len(output.sleeve_anchors.selections),
            "template_anchor_selection_count": len(output.template_points.selections),
            "trajectory_count": len(output.template_points.trajectories),
        }
    if number == 5:
        return {
            "connection_type": output.connection_type,
            "guide_anchor_count": len(output.guide_anchors),
            "junction_minimum_angle_degrees": round(output.junction_minimum_angle_degrees, 3),
            "junction_sleeve_distance_mm": round(output.junction_sleeve_distance_mm, 3),
            "beam_diameter_mm": round(2.0 * output.radius_mm, 3),
        }
    if number == 6:
        avoidance_routes = tuple(
            route
            for link in output.links
            for route in link.platform_avoidance_routes
        )
        return {
            "main_link_count": len(output.links),
            "press_beam_link_count": len(output.press_beam_links),
            "connector_diameter_mm": round(2.0 * output.radius_mm, 3),
            "recut_sleeve_bore": output.recut_sleeve_bore,
            "trim_against_dentition": output.trim_against_dentition,
            "platform_avoidance_side_count": len(avoidance_routes),
            "minimum_platform_projection_clearance_mm": (
                round(
                    min(route.minimum_clearance_mm for route in avoidance_routes),
                    3,
                )
                if avoidance_routes
                else None
            ),
            "platform_avoidance_offsets_mm": [
                {
                    "guide_index": route.guide_index,
                    "side": route.side,
                    "offset_mm": round(route.actual_offset_mm, 3),
                    "clearance_mm": round(route.minimum_clearance_mm, 3),
                }
                for route in avoidance_routes
            ],
        }
    plans = output
    return {
        "handpiece_count": len(plans),
        "pose_counts": [len(plan.angle_samples_degrees) for plan in plans],
        "angle_ranges_degrees": [
            [plan.angle_samples_degrees[0], plan.angle_samples_degrees[-1]] for plan in plans
        ],
        "extra_clearances_mm": [plan.extra_clearance_mm for plan in plans],
    }


def _source_files(config: CaseConfig) -> dict[str, object]:
    """返回本次运行实际使用的两个源网格。"""

    return {
        "template": str(config.inputs.template.resolve()),
        "patient_dentition": str(config.inputs.patient_dentition.resolve()),
    }


def _stage_document(
    config: CaseConfig,
    stage: StageResult,
    result_path: Path,
    overview_path: Path,
) -> dict[str, object]:
    """按共享顶层契约构造一个阶段文档。"""

    completed = stage.status is StageRunStatus.COMPLETED
    metrics = _stage_metrics(stage)
    checks = {
        "typed_result_available": completed and stage.output is not None,
        "business_metrics_available": bool(metrics),
    }
    if completed and stage.definition.number == 3 and stage.output.profile_windows:
        report = json.loads(stage.output.profile_windows[0].report_path.read_text(encoding="utf-8"))
        checks.update(
            {f"observation_window.{name}": bool(passed) for name, passed in report["QA"].items()}
        )
    return {
        "schema_version": STAGE_RESULT_SCHEMA,
        "stage": {
            "number": stage.definition.number,
            "key": stage.definition.key,
            "title": stage.definition.title_zh,
            "status": stage.status.value,
            "maturity": stage.definition.maturity.value,
            "implementation_version": stage.definition.implementation_version,
        },
        "case": {
            "id": config.case_id,
            "jaw": config.jaw.value,
        },
        "inputs": {
            "requires": list(stage.definition.requires),
            "source_files": _source_files(config),
        },
        "parameters": _stage_parameters(config, stage.definition.number),
        "result": _json_value(stage.output) if completed else None,
        "quality": {
            "passed": all(checks.values()) if completed else None,
            "checks": checks if completed else {},
            "metrics": metrics,
            "reason": stage.reason,
        },
        "artifacts": {
            "result_json": str(result_path.resolve()),
            "overview_png": (str(overview_path.resolve()) if overview_path.is_file() else None),
        },
    }


def write_stage_result_documents(process: GenerationProcessResult) -> tuple[Path, ...]:
    """在输出根目录为七个阶段写入同构 JSON。"""

    config = process.context.config
    output_directory = config.output_directory
    output_directory.mkdir(parents=True, exist_ok=True)
    written = []
    for stage in process.stages:
        stem = STAGE_ARTIFACT_STEMS[stage.definition.number]
        result_path = output_directory / f"{stem}.json"
        overview_path = output_directory / f"{stem}.png"
        # 第 2 阶段的统一工作流已经写入更完整的同版本文档。
        if (
            stage.definition.number == 2
            and stage.status is StageRunStatus.COMPLETED
            and result_path.is_file()
        ):
            written.append(result_path)
            continue
        document = _stage_document(config, stage, result_path, overview_path)
        result_path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        written.append(result_path)
    return tuple(written)


def compose_stage_overviews(process: GenerationProcessResult) -> tuple[Path, ...]:
    """将真实阶段图组合为无侧栏的论文式业务图。"""

    import matplotlib.image as mpimg
    import matplotlib.pyplot as plt

    output_directory = process.context.config.output_directory
    written = []
    plt.rcParams["font.sans-serif"] = ["PingFang SC", "Arial Unicode MS", "DejaVu Sans"]
    for stage in process.stages:
        image_path = output_directory / f"{STAGE_ARTIFACT_STEMS[stage.definition.number]}.png"
        if not image_path.is_file():
            continue
        source_path = image_path
        raw_path = (
            output_directory
            / ".cache"
            / STAGE_ARTIFACT_STEMS[stage.definition.number]
            / "raw-overview.png"
        )
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        if stage.definition.number == 2:
            if raw_path.is_file():
                source_path = raw_path
        else:
            current_image = mpimg.imread(image_path)
            expected_shape = (
                process.context.config.render.height_px,
                process.context.config.render.width_px,
            )
            if current_image.shape[:2] == expected_shape:
                shutil.copy2(image_path, raw_path)
            if raw_path.is_file():
                source_path = raw_path
        image = mpimg.imread(source_path)
        figure = plt.figure(figsize=(16, 9), facecolor="white")
        image_axes = figure.add_axes((0.02, 0.075, 0.96, 0.86))
        image_axes.imshow(image)
        image_axes.set_axis_off()
        figure.text(
            0.025,
            0.955,
            f"第 {stage.definition.number} 阶段：{stage.definition.title_zh}",
            color="#111827",
            fontsize=16,
            fontweight=600,
        )
        figure.text(
            0.975,
            0.955,
            process.context.config.case_id,
            color="#4b5563",
            fontsize=10,
            ha="right",
        )
        figure.text(
            0.025,
            0.025,
            "   |   ".join(STAGE_LEGENDS[stage.definition.number]),
            color="#374151",
            fontsize=9.5,
        )
        figure.savefig(
            image_path,
            dpi=120,
            facecolor="white",
            bbox_inches=None,
        )
        plt.close(figure)
        written.append(image_path)
    return tuple(written)


__all__ = [
    "STAGE_ARTIFACT_STEMS",
    "STAGE_RESULT_SCHEMA",
    "compose_stage_overviews",
    "write_stage_result_documents",
]

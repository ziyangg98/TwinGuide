"""运行全部规范病例并写入增量回归摘要。"""

from __future__ import annotations

import hashlib
import json
import time
import traceback
import warnings
from dataclasses import replace
from pathlib import Path

import bpy

from twin_guide import CaseConfig, generate_guide, validate_guide

ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = ROOT.parent / "data" / "cases"
CONFIGS = (
    *(
        DATA_ROOT / "single" / f"tooth-{fdi}" / "case.yaml"
        for fdi in (11, 12, 13, 14, 15, 16, 17, 47)
    ),
    *(
        DATA_ROOT / "multiple" / f"teeth-{pair}" / "case.yaml"
        for pair in ("12-13", "14-15", "15-16", "16-17")
    ),
)
SUMMARY_PATH = ROOT / "output" / "arch_progress_v2_all_case_regression.json"
OUTPUT_ROOT: Path | None = None


def _read_json(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_summary(records: list[dict[str, object]]) -> None:
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(
        json.dumps(
            {
                "recognition_profile": "standard_sleeve_reconstruction",
                "core_grouping_policy": "arch_progress",
                "cases": records,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )


def _read_stage_statuses(output_directory: Path) -> dict[str, str]:
    """读取七阶段报告，并拒绝缺失或重复的正式病例阶段记录。"""

    statuses: dict[str, str] = {}
    for number in range(1, 8):
        reports = tuple(output_directory.glob(f"stage-{number:02d}-*.json"))
        if len(reports) != 1:
            raise RuntimeError(
                f"阶段 {number} 报告数量应为 1，实际为 {len(reports)}：{output_directory}"
            )
        report = _read_json(reports[0])
        if report is None:
            raise RuntimeError(f"无法读取阶段 {number} 报告：{reports[0]}")
        stage = report.get("stage")
        if not isinstance(stage, dict) or not isinstance(stage.get("status"), str):
            raise RuntimeError(f"阶段 {number} 报告缺少 status：{reports[0]}")
        statuses[str(number)] = stage["status"]
    return statuses


def main() -> int:
    warnings.simplefilter("error", RuntimeWarning)
    missing = [path for path in CONFIGS if not path.is_file()]
    if missing:
        print("SKIP external case data is unavailable:")
        for path in missing:
            print(path)
        return 0
    records: list[dict[str, object]] = []
    for index, config_path in enumerate(CONFIGS, start=1):
        started = time.monotonic()
        record: dict[str, object] = {
            "config": str(config_path),
            "case": config_path.parent.name,
            "status": "running",
        }
        timings: dict[str, float] = {}
        print(f"CASE {index}/{len(CONFIGS)} START {config_path.name}", flush=True)
        try:
            step_started = time.monotonic()
            bpy.ops.wm.read_factory_settings(use_empty=True)
            config = CaseConfig.from_yaml(config_path)
            if OUTPUT_ROOT is not None:
                config = replace(
                    config,
                    output_directory=OUTPUT_ROOT / config.output_directory.name,
                )
            timings["setup"] = time.monotonic() - step_started
            step_started = time.monotonic()
            try:
                artifacts = generate_guide(config)
            finally:
                timings["generation"] = time.monotonic() - step_started
            step_started = time.monotonic()
            try:
                validations = validate_guide(artifacts.model_path, config)
            finally:
                timings["validation"] = time.monotonic() - step_started
            stage_2_result = _read_json(config.output_directory / "stage-02-tooth-mapping.json")
            validation_rows = [
                {
                    "name": item.name,
                    "passed": bool(item.passed),
                    "metrics": item.metrics,
                }
                for item in validations
            ]
            stage_statuses = _read_stage_statuses(config.output_directory)
            all_validations_passed = all(item["passed"] for item in validation_rows)
            all_stages_completed = all(status == "completed" for status in stage_statuses.values())
            record.update(
                {
                    "status": (
                        "complete"
                        if all_validations_passed and all_stages_completed
                        else ("incomplete" if all_validations_passed else "validation_failed")
                    ),
                    "stage_statuses": stage_statuses,
                    "output_directory": str(config.output_directory),
                    "model": str(artifacts.model_path),
                    "model_sha256": hashlib.sha256(artifacts.model_path.read_bytes()).hexdigest(),
                    "stage_2_result": stage_2_result,
                    "validation": validation_rows,
                }
            )
        except Exception as error:
            record.update(
                {
                    "status": "error",
                    "error_type": type(error).__name__,
                    "error": str(error),
                    "traceback": traceback.format_exc(),
                }
            )
        record["timings_seconds"] = {name: round(value, 3) for name, value in timings.items()}
        record["elapsed_seconds"] = round(time.monotonic() - started, 3)
        records.append(record)
        _write_summary(records)
        print(
            f"CASE {index}/{len(CONFIGS)} END {config_path.name} "
            f"{record['status']} {record['elapsed_seconds']}s",
            flush=True,
        )
    return 0 if all(item["status"] == "complete" for item in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())

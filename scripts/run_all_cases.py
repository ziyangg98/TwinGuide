"""逐例运行全部已配置 TwinGuide 数据，并生成可浏览的汇总报告。"""

from __future__ import annotations

import argparse
import html
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_ROOT = ROOT.parent / "data" / "cases"
DEFAULT_OUTPUT_ROOT = ROOT / "output" / "all-cases"


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="生成并验证 data/cases 下所有具有 case.yaml 的病例。"
    )
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument(
        "--case",
        action="append",
        default=[],
        help="只运行指定病例 ID；可重复填写。省略时运行全部已配置病例。",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="忽略病例计算缓存并完整重建。",
    )
    parser.add_argument(
        "--stop-on-failure",
        action="store_true",
        help="首个失败后停止；默认继续运行其余病例。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅列出将运行和跳过的病例，不启动 Blender。",
    )
    return parser.parse_args()


def _discover(data_root: Path, selected: set[str]) -> tuple[list[Path], list[str]]:
    case_directories = sorted(
        path for path in data_root.glob("case-*") if path.is_dir()
    )
    if selected:
        missing = sorted(selected - {path.name for path in case_directories})
        if missing:
            raise SystemExit(f"未找到病例目录：{', '.join(missing)}")
        case_directories = [path for path in case_directories if path.name in selected]
    configs = [path / "case.yaml" for path in case_directories if (path / "case.yaml").is_file()]
    unconfigured = [path.name for path in case_directories if not (path / "case.yaml").is_file()]
    return configs, unconfigured


def _stage_statuses(output_directory: Path) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for report_path in sorted(output_directory.glob("stage-??-*.json")):
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            stage = report["stage"]
            statuses[str(stage["number"])] = str(stage["status"])
        except (KeyError, TypeError, ValueError, OSError):
            statuses[report_path.stem] = "unreadable"
    return statuses


def _write_json(
    output_root: Path,
    records: list[dict[str, object]],
    unconfigured: list[str],
    started_at: str,
) -> None:
    passed = sum(record["status"] == "passed" for record in records)
    failed = sum(record["status"] == "failed" for record in records)
    payload = {
        "schema_version": "twin-guide.all-cases/1.0",
        "started_at": started_at,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "configured_case_count": len(records),
        "passed_case_count": passed,
        "failed_case_count": failed,
        "unconfigured_case_count": len(unconfigured),
        "unconfigured_cases": unconfigured,
        "cases": records,
    }
    (output_root / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _write_html(
    output_root: Path,
    records: list[dict[str, object]],
    unconfigured: list[str],
) -> None:
    cards: list[str] = []
    for record in records:
        case_id = str(record["case_id"])
        status = str(record["status"])
        status_label = {"passed": "通过", "failed": "失败", "pending": "等待"}.get(
            status, status
        )
        output_relative = f"cases/{case_id}"
        image_path = output_root / output_relative / "guide_iso.png"
        image = (
            f'<a href="{output_relative}/guide_iso.png"><img src="{output_relative}/guide_iso.png" '
            f'alt="{html.escape(case_id)}"></a>'
            if image_path.is_file()
            else '<div class="placeholder">暂无预览</div>'
        )
        error = html.escape(str(record.get("error", "")))
        details = f'<p class="error">{error}</p>' if error else ""
        cards.append(
            f'<article class="card {status}">{image}<h2>{html.escape(case_id)}</h2>'
            f'<p><span class="badge">{status_label}</span> '
            f'{record.get("elapsed_seconds", 0)} 秒</p>{details}'
            f'<p><a href="{output_relative}/twin_guide.stl">STL</a> · '
            f'<a href="logs/{case_id}.log">日志</a></p></article>'
        )
    passed = sum(record["status"] == "passed" for record in records)
    failed = sum(record["status"] == "failed" for record in records)
    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>TwinGuide 全病例运行结果</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:32px;background:#f5f7fa;color:#172033}}
.summary{{background:white;padding:20px;border-radius:12px;margin-bottom:24px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:20px}}
.card{{background:white;padding:16px;border-radius:12px;border-top:6px solid #8a94a6}}
.card.passed{{border-color:#198754}}.card.failed{{border-color:#dc3545}}
img,.placeholder{{width:100%;aspect-ratio:4/3;object-fit:contain;background:#eef1f5;border-radius:8px}}
.placeholder{{display:grid;place-items:center;color:#6c757d}}.badge{{font-weight:700}}.error{{color:#b42318}}
a{{color:#175cd3}}code{{word-break:break-all}}
</style></head><body>
<section class="summary"><h1>TwinGuide 全病例运行结果</h1>
<p>已配置 {len(records)} 例：通过 {passed}，失败 {failed}；缺少 case.yaml、未运行 {len(unconfigured)} 例。</p>
<p>机器可读汇总：<a href="summary.json">summary.json</a></p></section>
<main class="grid">{''.join(cards)}</main>
</body></html>"""
    (output_root / "report.html").write_text(document, encoding="utf-8")


def _run_case(
    config_path: Path,
    output_root: Path,
    *,
    force: bool,
) -> dict[str, object]:
    case_id = config_path.parent.name
    case_output = output_root / "cases" / case_id
    log_path = output_root / "logs" / f"{case_id}.log"
    command = [
        str(ROOT / "twinguide"),
        "generate",
        "--config",
        str(config_path),
        "--output",
        str(case_output),
        "--validate",
        "--allow-unreviewed",
    ]
    if force:
        command.append("--force")
    started = time.monotonic()
    validation_passed = 0
    validation_failed = 0
    last_error = ""
    with log_path.open("w", encoding="utf-8") as log:
        log.write("COMMAND " + " ".join(command) + "\n")
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(f"[{case_id}] {line}", end="", flush=True)
            log.write(line)
            if line.startswith("通过 "):
                validation_passed += 1
            elif line.startswith("失败 "):
                validation_failed += 1
            elif line.strip():
                last_error = line.strip()
        return_code = process.wait()
    elapsed = round(time.monotonic() - started, 3)
    model_path = case_output / "twin_guide.stl"
    status = "passed" if return_code == 0 and model_path.is_file() else "failed"
    return {
        "case_id": case_id,
        "config": str(config_path),
        "status": status,
        "return_code": return_code,
        "elapsed_seconds": elapsed,
        "output_directory": str(case_output),
        "model": str(model_path) if model_path.is_file() else None,
        "stage_statuses": _stage_statuses(case_output),
        "validation_passed_count": validation_passed,
        "validation_failed_count": validation_failed,
        "log": str(log_path),
        "error": "" if status == "passed" else last_error,
    }


def main() -> int:
    args = _arguments()
    data_root = args.data_root.resolve()
    output_root = args.output_root.resolve()
    configs, unconfigured = _discover(data_root, set(args.case))
    print(
        f"发现 {len(configs) + len(unconfigured)} 个病例目录："
        f"可运行 {len(configs)}，缺少 case.yaml {len(unconfigured)}。"
    )
    if args.dry_run:
        for config in configs:
            print(f"RUN  {config.parent.name} {config}")
        for case_id in unconfigured:
            print(f"SKIP {case_id} missing case.yaml")
        return 0
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "cases").mkdir(exist_ok=True)
    (output_root / "logs").mkdir(exist_ok=True)
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    records: list[dict[str, object]] = []
    _write_json(output_root, records, unconfigured, started_at)
    _write_html(output_root, records, unconfigured)
    for index, config in enumerate(configs, start=1):
        print(f"\nCASE {index}/{len(configs)} START {config.parent.name}", flush=True)
        record = _run_case(config, output_root, force=args.force)
        records.append(record)
        _write_json(output_root, records, unconfigured, started_at)
        _write_html(output_root, records, unconfigured)
        print(
            f"CASE {index}/{len(configs)} END {record['case_id']} "
            f"{record['status']} {record['elapsed_seconds']}s",
            flush=True,
        )
        if record["status"] == "failed" and args.stop_on_failure:
            break
    return 0 if records and all(record["status"] == "passed" for record in records) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n用户中断。", file=sys.stderr)
        raise SystemExit(130) from None

"""Tests for the all-cases batch runner."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_runner():
    path = Path(__file__).parents[2] / "scripts" / "run_all_cases.py"
    spec = importlib.util.spec_from_file_location("run_all_cases", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_exception_pattern_prefers_python_error_over_blender_shutdown() -> None:
    runner = _load_runner()

    assert runner.EXCEPTION_LINE.match(
        "twin_guide.errors.GeometryError: FDI New 牙位识别未通过下游安全门"
    )
    assert not runner.EXCEPTION_LINE.match("Blender quit")
    assert not runner.EXCEPTION_LINE.match("Error: script failed, exiting.")

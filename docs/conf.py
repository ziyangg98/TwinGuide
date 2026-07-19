"""Twinguide 的 Sphinx 文档配置。"""

from __future__ import annotations

import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE_ROOT))

project = "Twinguide"
author = "Twinguide 开发组"
language = "zh_CN"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.viewcode",
]

autosummary_generate = True
autodoc_mock_imports = [
    "bpy",
    "bmesh",
    "mathutils",
    "mathutils.bvhtree",
]
autodoc_default_options = {
    "members": True,
    "private-members": True,
    "member-order": "bysource",
    "show-inheritance": True,
}
autodoc_typehints = "description"
autodoc_typehints_description_target = "documented"

exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]
html_theme = "alabaster"
html_title = "Twinguide 代码文档"
html_static_path: list[str] = []

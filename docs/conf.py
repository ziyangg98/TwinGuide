"""TwinGuide 文档配置。"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from sphinx.application import Sphinx

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE_ROOT))

project = "TwinGuide"
author = "TwinGuide 开发组"
language = "zh_CN"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.viewcode",
    "myst_parser",
    "sphinxcontrib.mermaid",
]

autosummary_generate = True
autodoc_mock_imports = [
    "bpy",
    "bmesh",
    "mathutils",
    "mathutils.bvhtree",
    "manifold3d",
    "matplotlib",
    "numpy",
    "scipy",
    "sklearn",
    "trimesh",
]
autodoc_default_options = {
    "members": True,
    "member-order": "bysource",
    "show-inheritance": True,
}
autodoc_typehints = "description"
autodoc_typehints_description_target = "documented"

exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]
source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}
myst_enable_extensions = ["colon_fence", "deflist", "fieldlist"]
myst_fence_as_directive = ["mermaid"]
myst_heading_anchors = 3

html_theme = "furo"
html_title = "TwinGuide 文档"
html_static_path = ["_static"]
html_css_files = ["custom.css"]
html_theme_options = {
    "light_css_variables": {
        "color-brand-primary": "#256d85",
        "color-brand-content": "#1f647b",
    },
    "dark_css_variables": {
        "color-brand-primary": "#75c4dd",
        "color-brand-content": "#75c4dd",
    },
}


def _copy_readme_images(app: Sphinx, exception: Exception | None) -> None:
    """复制 README 引用的图片。"""
    if exception is not None:
        return
    source = Path(app.srcdir) / "images"
    target = Path(app.outdir) / "docs" / "images"
    shutil.copytree(source, target, dirs_exist_ok=True)


def setup(app: Sphinx) -> None:
    """注册文档构建钩子。"""
    app.connect("build-finished", _copy_readme_images)

"""Verify the standard TwinGuide Blender runtime and scientific dependencies."""

from __future__ import annotations

import platform
import sys

import bpy
import manifold3d
import matplotlib
import networkx
import numpy
import pytest
import rtree
import scipy
import shapely
import skimage
import sklearn
import trimesh
import yaml


EXPECTED_BLENDER = (5, 2, 0)

if bpy.app.version[:3] != EXPECTED_BLENDER:
    raise RuntimeError(
        f"Expected Blender {EXPECTED_BLENDER}, found {bpy.app.version[:3]}"
    )
if sys.version_info[:2] != (3, 13):
    raise RuntimeError(f"Expected Python 3.13, found {sys.version}")
if platform.machine() != "arm64":
    raise RuntimeError(f"Expected arm64, found {platform.machine()}")

versions = {
    "Blender": bpy.app.version_string,
    "Python": platform.python_version(),
    "NumPy": numpy.__version__,
    "pytest": pytest.__version__,
    "SciPy": scipy.__version__,
    "scikit-image": skimage.__version__,
    "scikit-learn": sklearn.__version__,
    "Trimesh": trimesh.__version__,
    "PyYAML": yaml.__version__,
    "Matplotlib": matplotlib.__version__,
    "Shapely": shapely.__version__,
    "Rtree": rtree.__version__,
    "NetworkX": networkx.__version__,
    "manifold3d": getattr(manifold3d, "__version__", "import-ok"),
}

print("TWIN_GUIDE_ENV_OK")
for name, version in versions.items():
    print(f"{name}: {version}")

"""观察窗的纯几何构造接口。"""

from twin_guide.observation_window_engine._core import (
    adaptive_wall_thickness,
    axis_sweep_axis_points,
    axis_sweep_tooth_visibility,
    build_axis_sweep_cutter,
    build_grid_cutter,
    structured_prism,
)

__all__ = [
    "adaptive_wall_thickness",
    "axis_sweep_axis_points",
    "axis_sweep_tooth_visibility",
    "build_axis_sweep_cutter",
    "build_grid_cutter",
    "structured_prism",
]

"""TwinGuide 专用 Blender 工作区布局。"""

from __future__ import annotations

import bpy


def configure_workspace() -> None:
    """切换并配置专用 TwinGuide 三维编辑工作区。"""

    window = bpy.context.window
    if window is not None:
        existing = bpy.data.workspaces.get("TwinGuide")
        if existing is None:
            window.workspace.name = "TwinGuide"
        else:
            window.workspace = existing
    screen = bpy.context.screen
    if screen is None:
        return
    for area in screen.areas:
        if area.type != "VIEW_3D":
            continue
        space = area.spaces.active
        space.show_region_ui = True
        space.show_region_toolbar = True
        space.overlay.show_outline_selected = True
        space.shading.type = "SOLID"
        space.shading.color_type = "OBJECT"


__all__ = ["configure_workspace"]

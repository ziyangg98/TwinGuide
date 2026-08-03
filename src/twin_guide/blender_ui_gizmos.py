"""TwinGuide 受约束三维 Gizmo。"""

from __future__ import annotations

from typing import ClassVar

import bpy
from mathutils import Matrix


class TwinGuideFeatureGizmoGroup(bpy.types.GizmoGroup):
    """为当前结构显示一至两个直接拖动的局部轴 Gizmo。"""

    bl_idname = "TWINGUIDE_GGT_feature"
    bl_label = "TwinGuide 结构手柄"
    bl_space_type = "VIEW_3D"
    bl_region_type = "WINDOW"
    bl_options: ClassVar[set[str]] = {"3D", "PERSISTENT"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        """只在未锁定且选中可编辑代理时显示。"""

        from twin_guide import blender_ui

        object_ = context.active_object
        state = getattr(context.scene, "twin_guide_state", None)
        return bool(
            object_ is not None
            and object_.name.startswith(blender_ui.CONTROL_PREFIX)
            and not object_.hide_get()
            and state is not None
            and not state.editing_locked
            and (
                object_.get("tg_kind") == "surface_anchor"
                or blender_ui._gizmo_axes(object_)
            )
        )

    def setup(self, _context: bpy.types.Context) -> None:
        """建立两支可复用的 Blender 原生箭头。"""

        self.axis_gizmos = []
        for index, color in enumerate(((0.1, 0.65, 1.0), (0.2, 0.9, 0.45))):
            gizmo = self.gizmos.new("GIZMO_GT_arrow_3d")
            gizmo.color = color
            gizmo.alpha = 0.9
            gizmo.color_highlight = (1.0, 0.65, 0.1)
            gizmo.alpha_highlight = 1.0
            gizmo.scale_basis = 1.2
            gizmo.use_draw_modal = True
            gizmo.use_draw_value = False
            operator = gizmo.target_set_operator("twinguide.drag_feature_handle")
            operator.axis_index = index
            self.axis_gizmos.append(gizmo)
        self.surface_gizmo = self.gizmos.new("GIZMO_GT_dial_3d")
        self.surface_gizmo.color = (0.25, 0.9, 0.35)
        self.surface_gizmo.alpha = 0.85
        self.surface_gizmo.color_highlight = (1.0, 0.65, 0.1)
        self.surface_gizmo.alpha_highlight = 1.0
        self.surface_gizmo.scale_basis = 0.9
        self.surface_gizmo.target_set_operator("twinguide.drag_surface_anchor")

    def refresh(self, context: bpy.types.Context) -> None:
        """把箭头放到当前代理的局部轴上。"""

        from twin_guide import blender_ui

        object_ = context.active_object
        axes = () if object_ is None else blender_ui._gizmo_axes(object_)
        surface_anchor = bool(
            object_ is not None and object_.get("tg_kind") == "surface_anchor"
        )
        for index, gizmo in enumerate(self.axis_gizmos):
            gizmo.hide = surface_anchor or index >= len(axes)
            if gizmo.hide:
                continue
            _origin, axis = axes[index]
            rotation = axis.to_track_quat("Z", "Y").to_matrix().to_4x4()
            gizmo.matrix_basis = Matrix.Translation(object_.location) @ rotation
        self.surface_gizmo.hide = not surface_anchor
        if surface_anchor:
            normal = object_.rotation_quaternion.to_matrix().to_4x4()
            self.surface_gizmo.matrix_basis = (
                Matrix.Translation(object_.location) @ normal
            )


__all__ = ["TwinGuideFeatureGizmoGroup"]

"""TwinGuide 结构列表和参数面板类型。"""

from __future__ import annotations

import bpy


class TwinGuideFeatureItem(bpy.types.PropertyGroup):
    """结构列表中的一个稳定结构条目。"""

    feature_id: bpy.props.StringProperty()
    group_label: bpy.props.StringProperty()
    label: bpy.props.StringProperty()


class TWINGUIDE_UL_feature_list(bpy.types.UIList):  # noqa: N801
    """按临床结构名称绘制可选择列表。"""

    def draw_item(
        self,
        _context: bpy.types.Context,
        layout: bpy.types.UILayout,
        _data: object,
        item: TwinGuideFeatureItem,
        _icon: int,
        _active_data: object,
        _active_property: str,
        _index: int,
    ) -> None:
        """绘制分组名称和结构名称。"""

        row = layout.row(align=True)
        icon = {
            "操作窗": "MESH_PLANE",
            "观察窗": "HIDE_OFF",
            "连接线": "CURVE_DATA",
            "支撑结构": "PINNED",
            "导柱": "MESH_CYLINDER",
        }.get(item.group_label, "DOT")
        split = row.split(factor=0.72)
        split.label(text=item.label, icon=icon)
        split.label(text=item.group_label)


class TwinGuidePanel(bpy.types.Panel):
    """显示结构列表、精确参数和任务操作。"""

    bl_label = "TwinGuide"
    bl_idname = "TWINGUIDE_PT_editor"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "TwinGuide"

    def draw(self, context: bpy.types.Context) -> None:
        """委托编辑器状态模块绘制动态内容。"""

        from twin_guide import blender_ui

        renderer = blender_ui._TwinGuidePanelRenderer()
        renderer.layout = self.layout
        renderer.draw(context)


class TwinGuideStructurePanel(bpy.types.Panel):
    """在三维视图左侧显示按临床结构分组的选择列表。"""

    bl_label = "TwinGuide 结构"
    bl_idname = "TWINGUIDE_PT_structures"
    bl_space_type = "VIEW_3D"
    bl_region_type = "TOOLS"

    def draw(self, context: bpy.types.Context) -> None:
        """绘制模型查看入口和稳定结构列表。"""

        layout = self.layout
        state = context.scene.twin_guide_state
        layout.operator(
            "twinguide.model_view",
            text="模型查看",
            icon="HIDE_OFF",
        )
        layout.separator()
        layout.label(text="选择结构", icon="RESTRICT_SELECT_OFF")
        layout.template_list(
            "TWINGUIDE_UL_feature_list",
            "features",
            context.scene,
            "twin_guide_features",
            state,
            "active_feature_index",
            rows=12,
        )
        legend = layout.column(align=True)
        legend.enabled = False
        legend.label(text="绿 操作窗 · 紫 观察窗")
        legend.label(text="橙 连接线 · 蓝 导柱 · 图钉 支撑")


__all__ = [
    "TWINGUIDE_UL_feature_list",
    "TwinGuideFeatureItem",
    "TwinGuidePanel",
    "TwinGuideStructurePanel",
]

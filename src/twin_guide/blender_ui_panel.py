"""TwinGuide 结构列表和参数面板类型。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import bpy
from mathutils import Vector

from twin_guide.blender_ui_proxies import CONTROL_PREFIX
from twin_guide.config import CaseConfig


class PanelState(Protocol):
    """面板绘制实际读取的场景状态接口。"""

    case_label: str
    review_status: str
    validation_status: str
    task_status: str
    dirty: bool
    editing_locked: bool
    show_advanced: bool
    show_dentition_reference: bool
    show_template_reference: bool


@dataclass(frozen=True, slots=True)
class PanelBindings:
    """面板展示所需的只读运行状态和查询回调。"""

    editor_ready: bool
    job_active: bool
    config: CaseConfig | None
    feature_label: Callable[[str], tuple[str, str]]
    find_control: Callable[..., bpy.types.Object | None]


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
            "连接避让": "CURVE_DATA",
            "按压梁": "PINNED",
            "双导柱": "MESH_CYLINDER",
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

        renderer = TwinGuidePanelRenderer(blender_ui._panel_bindings())
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
        legend.label(text="● 绿色  操作窗    ● 紫色  观察窗")
        legend.label(text="● 橙色  连接避让  ● 蓝色  双导柱 / 按压梁")


class TwinGuidePanelRenderer:
    """绘制病例状态、结构参数和输出操作。"""

    layout: bpy.types.UILayout

    def __init__(self, bindings: PanelBindings) -> None:
        """保存只读面板依赖，绘制时不直接管理全局状态。"""

        self.bindings = bindings

    @staticmethod
    def _status_icon(value: str) -> str:
        """为短状态选择稳定的 Blender 图标。"""

        if any(token in value for token in ("失败", "上一版")):
            return "ERROR"
        if any(token in value for token in ("通过", "已", "完成", "空闲")):
            return "CHECKMARK"
        return "INFO"

    def _draw_status_card(
        self,
        layout: bpy.types.UILayout,
        state: PanelState,
    ) -> None:
        """绘制病例、审核、预览和任务状态。"""

        box = layout.box()
        header = box.row(align=True)
        header.label(text=state.case_label, icon="FILE_TICK")
        header.label(
            text="未保存" if state.dirty else "已保存",
            icon="ERROR" if state.dirty else "CHECKMARK",
        )
        status = box.grid_flow(columns=2, even_columns=True, align=True)
        status.label(text=f"审核 {state.review_status}")
        status.label(text=f"检验 {state.validation_status}")
        if state.task_status not in {"", "空闲", "已完成"}:
            box.label(text=state.task_status, icon=self._status_icon(state.task_status))

    @staticmethod
    def _feature_instruction(feature_id: str) -> str:
        """返回当前结构的一句话操作提示。"""

        instructions = {
            "operation_window:": "拖中心移动窗口；拖边缘调宽高；拖前/后点调切除量。",
            "observation_window:": "拖起止点换牙位；拖轴向手柄调下沉、高度和扫掠角。",
            "sleeve:": "拖蓝色圆环调高度；选中橙色导柱标记后拖旁边小箭头，整对导柱同步转动。",
            "connector:": "拖沿线箭头改避让节点位置，拖向下箭头改正视避让量。",
            "press_anchor:": "选择吸附表面后，按“在表面重新定位”让图钉贴面滑动。",
        }
        for prefix, instruction in instructions.items():
            if feature_id.startswith(prefix):
                return instruction
        if feature_id == "press_junction":
            return "拖二维十字，只在当前工作平面内移动汇合点。"
        return "拖三个圆环分别调整底部、平台和总高度。"

    def _draw_feature_values(
        self,
        box: bpy.types.UILayout,
        state: PanelState,
        feature_id: str,
    ) -> None:
        """绘制当前完整结构的临床参数输入框。"""

        box.use_property_split = True
        box.use_property_decorate = False
        if feature_id.startswith("operation_window:"):
            labels = (
                "宽度 (mm)",
                "高度 (mm)",
                "前部切除量 (mm)",
                "后部切除量 (mm)",
                "局部横向 (mm)",
                "局部纵向 (mm)",
            )
        elif feature_id.startswith("connector:"):
            labels = ("沿线位置", "向下偏移 (mm)")
            if self.bindings.config is not None:
                geometry = self.bindings.config.geometry
                box.label(text=f"梁直径：{geometry.connector_diameter_mm:.3f} mm")
                blocks = geometry.connection_blocks
                enabled = [
                    label
                    for label, value in (
                        ("下层", blocks.lower_main),
                        ("上层", blocks.upper_main),
                        ("按压梁", blocks.press_beam),
                    )
                    if value
                ]
                box.label(text=f"连接分块：{'、'.join(enabled)}")
        elif feature_id.startswith("sleeve:"):
            labels = (
                "底部高度 (mm)",
                "平台高度 (mm)",
                "总高度 (mm)",
                "双导柱整体方位角 (°)",
            )
        elif feature_id.startswith("observation_window:"):
            row = box.row(align=True)
            row.prop(state, "feature_fdi_start", text="起点 FDI")
            row.prop(state, "feature_fdi_end", text="终点 FDI")
            labels = ("轴向下沉 (mm)", "窗口高度 (mm)", "扫掠角 (°)")
        elif feature_id == "press_junction":
            labels = ("工作平面 X (mm)", "工作平面 Y (mm)")
        elif feature_id.startswith("press_anchor:"):
            box.prop(state, "surface_role")
            box.prop(state, "feature_position", text="位置 (mm)")
            box.operator("twinguide.drag_surface_anchor")
            labels = ()
        else:
            labels = ()
        for index, label in enumerate(labels, start=1):
            box.prop(state, f"feature_value_{index}", text=label)
        if feature_id.startswith("sleeve:"):
            row = box.row(align=True)
            for text, delta in (("−5°", -5.0), ("−1°", -1.0)):
                operator = row.operator("twinguide.step_sleeve_rotation", text=text)
                operator.delta_degrees = delta
            reset = row.operator("twinguide.step_sleeve_rotation", text="归零")
            reset.reset = True
            for text, delta in (("+1°", 1.0), ("+5°", 5.0)):
                operator = row.operator("twinguide.step_sleeve_rotation", text=text)
                operator.delta_degrees = delta

    @staticmethod
    def _draw_advanced(
        box: bpy.types.UILayout,
        state: PanelState,
        selected: bpy.types.Object,
    ) -> None:
        """在折叠区域显示不用于常规调整的世界信息。"""

        row = box.row()
        icon = "TRIA_DOWN" if state.show_advanced else "TRIA_RIGHT"
        row.prop(state, "show_advanced", text="高级信息", icon=icon, emboss=False)
        if not state.show_advanced:
            return
        location = selected.location
        box.label(text=f"世界坐标：({location.x:.3f}, {location.y:.3f}, {location.z:.3f}) mm")
        if "tg_normal" in selected:
            normal = Vector(selected["tg_normal"])
            box.label(text=f"法向：({normal.x:.4f}, {normal.y:.4f}, {normal.z:.4f})")

    def draw(self, context: bpy.types.Context) -> None:
        """绘制当前病例编辑工作流。"""

        layout = self.layout
        state = context.scene.twin_guide_state
        self._draw_status_card(layout, state)
        if not self.bindings.editor_ready:
            box = layout.box()
            box.label(text="正在准备编辑数据", icon="TIME")
            hint = box.column(align=True)
            hint.enabled = False
            hint.label(text="模型可以查看，几何编辑和预览稍后可用")
            hint.label(text="准备完成后会自动显示控制点")
            cancel = layout.column()
            cancel.enabled = self.bindings.job_active
            cancel.operator("twinguide.cancel_job")
            return
        editor = layout.column()
        editor.enabled = not state.editing_locked
        reference_box = editor.box()
        reference_box.label(text="参考显示", icon="HIDE_OFF")
        reference_row = reference_box.row(align=True)
        reference_row.prop(
            state,
            "show_dentition_reference",
            text="牙列",
            icon="HIDE_OFF" if state.show_dentition_reference else "HIDE_ON",
            toggle=True,
        )
        reference_row.prop(
            state,
            "show_template_reference",
            text="原始导板",
            icon="HIDE_OFF" if state.show_template_reference else "HIDE_ON",
            toggle=True,
        )
        selected = context.active_object
        if (
            selected is not None
            and selected.name.startswith(CONTROL_PREFIX)
            and not selected.hide_get()
        ):
            box = editor.box()
            feature_id = str(selected.get("tg_feature_id", ""))
            _group, feature_name = self.bindings.feature_label(feature_id)
            title = box.row(align=True)
            title.scale_y = 1.25
            title.label(text=feature_name, icon="EDITMODE_HLT")
            handle_hint = str(selected.get("tg_hint", ""))
            if handle_hint:
                box.label(text=f"手柄：{handle_hint}")
            hint = box.column(align=True)
            hint.enabled = False
            hint.label(text=self._feature_instruction(feature_id))
            hint.label(text="Shift 0.01 精调，Ctrl 0.1 对齐，Esc 取消")
            box.separator()
            box.label(text="精确参数", icon="DRIVER")
            self._draw_feature_values(box, state, feature_id)
            self._draw_advanced(box, state, selected)
            if feature_id.startswith("operation_window:"):
                center = self.bindings.find_control(
                    "window_center",
                    site_index=int(feature_id.rsplit(":", 1)[-1]),
                )
                if center is not None and center.get("tg_projection_failed"):
                    box.label(text="部分轮廓未能投影到当前模型", icon="ERROR")
            if selected.get("tg_resnap_required"):
                box.label(text="距已保存表面过远，请重新吸附", icon="ERROR")
        else:
            hint = editor.box()
            hint.label(text="选择一个结构开始调整", icon="INFO")
            hint.label(text="可点三维标签、彩色热点或左侧列表")
        edit_box = editor.box()
        edit_box.label(text="编辑", icon="EDITMODE_HLT")
        row = edit_box.row(align=True)
        row.operator("twinguide.reset_selected", text="撤销", icon="LOOP_BACK")
        row.operator("twinguide.redo_adjustment", text="重做", icon="LOOP_FORWARDS")
        edit_box.operator("twinguide.restore_saved", text="恢复已保存值")
        edit_box.operator("twinguide.save_adjustments", text="保存调整")
        output_box = editor.box()
        output_box.label(text="预览与输出", icon="INFO")
        note = output_box.column(align=True)
        note.enabled = False
        note.label(text="预览用于快速检查形态")
        note.label(text="最终结果以导出并检验为准")
        output_box.operator("twinguide.update_preview", text="更新预览")
        output_box.operator("twinguide.final_export", text="确认导出并检验")
        cancel = layout.column()
        cancel.enabled = self.bindings.job_active
        cancel.operator("twinguide.cancel_job")


__all__ = [
    "PanelBindings",
    "TWINGUIDE_UL_feature_list",
    "TwinGuideFeatureItem",
    "TwinGuidePanel",
    "TwinGuidePanelRenderer",
    "TwinGuideStructurePanel",
]

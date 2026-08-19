"""TwinGuide 编辑器中不负责连续拖动的离散操作器。"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

import bpy

from twin_guide.config import CaseConfig
from twin_guide.config.editor_storage import save_editor_overrides


def _ui() -> ModuleType:
    """延迟取得主控制器，避免 Blender 注册阶段循环导入。"""

    from twin_guide import blender_ui

    return blender_ui


class TwinGuideSleeveRotationStepOperator(bpy.types.Operator):
    """用固定步长快速调整双导柱整体方位角。"""

    bl_idname = "twinguide.step_sleeve_rotation"
    bl_label = "调整双导柱整体方位"

    delta_degrees: bpy.props.FloatProperty(default=0.0)
    reset: bpy.props.BoolProperty(default=False)

    @classmethod
    def poll(cls, _context: bpy.types.Context) -> bool:
        """仅在选中双导柱结构且编辑未锁定时启用。"""

        session = _ui()._SESSION
        return bool(
            session is not None
            and not session.locked
            and (session.selected_feature_id or "").startswith("sleeve:")
        )

    def execute(self, context: bpy.types.Context) -> set[str]:
        """应用步长、同步视觉预览并提交一个撤销步骤。"""

        ui = _ui()
        session = ui._SESSION
        if session is None or session.selected_feature_id is None:
            return {"CANCELLED"}
        feature_id = session.selected_feature_id
        control = ui._sleeve_rotation_control(ui._controls_for_feature(feature_id))
        if control is None:
            return {"CANCELLED"}
        current = float(control["tg_angle_degrees"])
        target = 0.0 if self.reset else current + self.delta_degrees
        session.begin_edit()
        ui._update_sleeve_rotation_preview(int(control["tg_ring_index"]), target)
        ui._preview_feature_edit(feature_id)
        session.commit_edit()
        context.scene.twin_guide_state.dirty = session.dirty
        ui._sync_feature_values(feature_id)
        return {"FINISHED"}


class TwinGuideModelViewOperator(bpy.types.Operator):
    """回到只显示结构概览的模型查看状态。"""

    bl_idname = "twinguide.model_view"
    bl_label = "模型查看"

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        """编辑数据准备完成且未锁定时允许切换结构。"""

        ui = _ui()
        return bool(
            ui._EDITOR_PLAN_PATH is not None and not context.scene.twin_guide_state.editing_locked
        )

    def execute(self, context: bpy.types.Context) -> set[str]:
        """显示指定结构的控制点。"""

        ui = _ui()
        ui._SELECTION_SYNC = True
        context.scene.twin_guide_state.active_feature_index = -1
        ui._SELECTION_SYNC = False
        ui._show_model_view()
        return {"FINISHED"}


class TwinGuideSaveOperator(bpy.types.Operator):
    """仅在明确点击时原子写回病例覆盖值。"""

    bl_idname = "twinguide.save_adjustments"
    bl_label = "保存调整"

    @classmethod
    def poll(cls, _context: bpy.types.Context) -> bool:
        """最终任务锁定期间不允许写回病例。"""

        session = _ui()._SESSION
        return bool(session is not None and not session.locked)

    def execute(self, context: bpy.types.Context) -> set[str]:
        """保存当前控制点并刷新保存基线。"""

        ui = _ui()
        try:
            session = ui._SESSION
            if session is not None and session.editing:
                session.commit_edit()
            if session is None:
                raise RuntimeError("编辑会话尚未初始化")
            config_path = Path(context.scene.twin_guide_state.config_path)
            save_editor_overrides(config_path, session.working_overrides)
            ui._CONFIG = CaseConfig.from_yaml(config_path)
            session.mark_saved()
            context.scene.twin_guide_state.dirty = False
        except Exception as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class TwinGuideResetSelectedOperator(bpy.types.Operator):
    """撤销最近一次已确认的语义编辑。"""

    bl_idname = "twinguide.reset_selected"
    bl_label = "撤销"

    @classmethod
    def poll(cls, _context: bpy.types.Context) -> bool:
        """存在可撤销快照且编辑未锁定时启用。"""

        session = _ui()._SESSION
        return bool(session is not None and not session.locked and session.undo_stack)

    def execute(self, _context: bpy.types.Context) -> set[str]:
        """撤销会话快照并重建工作代理。"""

        ui = _ui()
        if ui._SESSION is None or not ui._SESSION.undo():
            return {"CANCELLED"}
        ui._rebuild_working_proxies()
        return {"FINISHED"}


class TwinGuideRedoOperator(bpy.types.Operator):
    """重做最近一次已撤销的语义编辑。"""

    bl_idname = "twinguide.redo_adjustment"
    bl_label = "重做"

    @classmethod
    def poll(cls, _context: bpy.types.Context) -> bool:
        """存在可重做快照且编辑未锁定时启用。"""

        session = _ui()._SESSION
        return bool(session is not None and not session.locked and session.redo_stack)

    def execute(self, _context: bpy.types.Context) -> set[str]:
        """重做会话快照并重建工作代理。"""

        ui = _ui()
        if ui._SESSION is None or not ui._SESSION.redo():
            return {"CANCELLED"}
        ui._rebuild_working_proxies()
        return {"FINISHED"}


class TwinGuideRestoreOperator(bpy.types.Operator):
    """从已保存阶段结果恢复全部控制点。"""

    bl_idname = "twinguide.restore_saved"
    bl_label = "恢复已保存值"

    @classmethod
    def poll(cls, _context: bpy.types.Context) -> bool:
        """工作值偏离保存值且编辑未锁定时启用。"""

        session = _ui()._SESSION
        return bool(session is not None and not session.locked and session.dirty)

    def execute(self, _context: bpy.types.Context) -> set[str]:
        """恢复已保存覆盖值并重建工作代理。"""

        ui = _ui()
        if ui._SESSION is None:
            return {"CANCELLED"}
        ui._SESSION.replace(ui._SESSION.saved_overrides)
        ui._rebuild_working_proxies()
        return {"FINISHED"}


class TwinGuidePreviewOperator(bpy.types.Operator):
    """启动不运行几何检验的实体预览。"""

    bl_idname = "twinguide.update_preview"
    bl_label = "更新预览"

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        """编辑计划有效且没有后台任务时启用。"""

        ui = _ui()
        return bool(
            ui._EDITOR_PLAN_PATH is not None
            and not ui._EDITOR_PLAN_STALE
            and ui._JOB is None
            and not context.scene.twin_guide_state.editing_locked
        )

    def execute(self, _context: bpy.types.Context) -> set[str]:
        """启动不含最终检验的后台预览任务。"""

        try:
            _ui()._start_job("preview")
        except Exception as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class TwinGuideFinalOperator(bpy.types.Operator):
    """保存、生成候选、检验并安全提升正式输出。"""

    bl_idname = "twinguide.final_export"
    bl_label = "确认导出并检验"

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        """编辑计划就绪且当前没有后台任务时启用。"""

        ui = _ui()
        return bool(
            ui._EDITOR_PLAN_PATH is not None
            and ui._JOB is None
            and not context.scene.twin_guide_state.editing_locked
        )

    def execute(self, context: bpy.types.Context) -> set[str]:
        """保存调整并启动最终生成与检验任务。"""

        result = bpy.ops.twinguide.save_adjustments()
        if "FINISHED" not in result:
            return {"CANCELLED"}
        ui = _ui()
        try:
            if ui._SESSION is not None:
                ui._SESSION.locked = True
            context.scene.twin_guide_state.editing_locked = True
            ui._start_job("final")
        except Exception as error:
            if ui._SESSION is not None:
                ui._SESSION.locked = False
            context.scene.twin_guide_state.editing_locked = False
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class TwinGuideCancelOperator(bpy.types.Operator):
    """取消当前后台生成任务。"""

    bl_idname = "twinguide.cancel_job"
    bl_label = "取消后台任务"

    @classmethod
    def poll(cls, _context: bpy.types.Context) -> bool:
        """存在可取消的后台任务时启用。"""

        return _ui()._JOB is not None

    def execute(self, _context: bpy.types.Context) -> set[str]:
        """请求终止尚未进入原子提升阶段的任务。"""

        job = _ui()._JOB
        if job is not None and not job.cancel():
            self.report({"INFO"}, "正式模型正在更新，当前阶段不能取消")
        return {"FINISHED"}


__all__ = [
    "TwinGuideCancelOperator",
    "TwinGuideFinalOperator",
    "TwinGuideModelViewOperator",
    "TwinGuidePreviewOperator",
    "TwinGuideRedoOperator",
    "TwinGuideResetSelectedOperator",
    "TwinGuideRestoreOperator",
    "TwinGuideSaveOperator",
    "TwinGuideSleeveRotationStepOperator",
]

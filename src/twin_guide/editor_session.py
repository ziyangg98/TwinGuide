"""与 Blender 对象无关的 TwinGuide 图形编辑会话。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from twin_guide.config import EditorOverrides


def changed_feature_ids(
    previous: EditorOverrides,
    current: EditorOverrides,
) -> tuple[str, ...]:
    """返回两份覆盖值之间发生变化的稳定结构编号。"""

    changed = []

    def compare(
        before: tuple[object, ...],
        after: tuple[object, ...],
        key: Callable[[object], object],
        feature_id: Callable[[object], str],
    ) -> None:
        """比较一类带稳定业务键的覆盖值。"""

        previous_values = {key(item): item for item in before}
        current_values = {key(item): item for item in after}
        for item_key in previous_values.keys() | current_values.keys():
            if previous_values.get(item_key) != current_values.get(item_key):
                changed.append(feature_id(item_key))

    compare(
        previous.sleeve_sites,
        current.sleeve_sites,
        lambda item: item.ring_index,
        lambda index: f"sleeve:site_{index}",
    )
    compare(
        previous.operation_windows,
        current.operation_windows,
        lambda item: item.site_index,
        lambda index: f"operation_window:{index}",
    )
    compare(
        previous.observation_windows,
        current.observation_windows,
        lambda item: item.window_id,
        lambda window_id: f"observation_window:{window_id}",
    )
    compare(
        previous.connector_avoidance,
        current.connector_avoidance,
        lambda item: item.guide_index,
        lambda index: f"connector:guide_{index}",
    )
    compare(
        previous.surface_anchors,
        current.surface_anchors,
        lambda item: item.anchor_id,
        lambda anchor_id: f"press_anchor:{str(anchor_id).rsplit('_', 1)[-1]}",
    )
    if previous.press_junction_mm != current.press_junction_mm:
        changed.append("press_junction")
    return tuple(sorted(changed))


@dataclass(slots=True)
class EditorSession:
    """管理已保存值、工作值、选择、版本和本地撤销栈。"""

    saved_overrides: EditorOverrides
    working_overrides: EditorOverrides
    selected_feature_id: str | None = None
    revision: int = 0
    locked: bool = False
    undo_stack: list[EditorOverrides] = field(default_factory=list)
    redo_stack: list[EditorOverrides] = field(default_factory=list)
    _drag_origin: EditorOverrides | None = None

    @classmethod
    def create(cls, overrides: EditorOverrides) -> EditorSession:
        """使用病例当前覆盖值建立一个干净会话。"""

        return cls(overrides, overrides)

    @property
    def dirty(self) -> bool:
        """返回工作值是否尚未保存。"""

        return self.working_overrides != self.saved_overrides

    @property
    def editing(self) -> bool:
        """返回当前是否存在尚未确认的拖动。"""

        return self._drag_origin is not None

    def select(self, feature_id: str | None) -> None:
        """选择一个稳定结构编号；锁定不影响查看选择。"""

        self.selected_feature_id = feature_id

    def begin_edit(self) -> None:
        """保存一次拖动开始前的语义快照。"""

        if self.locked:
            raise RuntimeError("最终导出期间不能修改几何")
        if self._drag_origin is None:
            self._drag_origin = self.working_overrides

    def preview_edit(self, overrides: EditorOverrides) -> None:
        """更新拖动中的工作值，但暂不增加 revision。"""

        if self.locked:
            raise RuntimeError("最终导出期间不能修改几何")
        if self._drag_origin is None:
            self.begin_edit()
        self.working_overrides = overrides

    def commit_edit(self) -> bool:
        """确认本次编辑并建立一个可撤销步骤。"""

        origin = self._drag_origin
        self._drag_origin = None
        if origin is None or origin == self.working_overrides:
            return False
        self.undo_stack.append(origin)
        self.redo_stack.clear()
        self.revision += 1
        return True

    def cancel_edit(self) -> None:
        """取消拖动并恢复开始前的工作值。"""

        if self._drag_origin is not None:
            self.working_overrides = self._drag_origin
        self._drag_origin = None

    def replace(self, overrides: EditorOverrides) -> bool:
        """把一次精确数值输入记录为单独撤销步骤。"""

        if self.locked:
            raise RuntimeError("最终导出期间不能修改几何")
        if overrides == self.working_overrides:
            return False
        self.undo_stack.append(self.working_overrides)
        self.working_overrides = overrides
        self.redo_stack.clear()
        self.revision += 1
        return True

    def undo(self) -> bool:
        """撤销最近一次已确认编辑。"""

        if self.locked or not self.undo_stack:
            return False
        self.redo_stack.append(self.working_overrides)
        self.working_overrides = self.undo_stack.pop()
        self.revision += 1
        return True

    def redo(self) -> bool:
        """重做最近一次已撤销编辑。"""

        if self.locked or not self.redo_stack:
            return False
        self.undo_stack.append(self.working_overrides)
        self.working_overrides = self.redo_stack.pop()
        self.revision += 1
        return True

    def mark_saved(self) -> None:
        """将当前工作值设为最近保存值。"""

        self.saved_overrides = self.working_overrides

    def preview_is_current(self, job_revision: int) -> bool:
        """判断后台预览是否对应当前工作版本。"""

        return job_revision == self.revision


__all__ = ["EditorSession", "changed_feature_ids"]

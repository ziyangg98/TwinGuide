"""语义编辑值与单个 TwinGuide 结构之间的不可变 Adapter。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from twin_guide.config import (
    ConnectorAvoidanceOverride,
    EditorOverrides,
    ObservationWindowOverride,
    OperationWindowOverride,
    SleeveSiteOverride,
    SurfaceAnchorOverride,
)


def _upsert[T](items: tuple[T, ...], value: T, key: Callable[[T], object]) -> tuple[T, ...]:
    """按稳定 ID 替换一个值，并保持确定顺序。"""

    identifier = key(value)
    retained = [item for item in items if key(item) != identifier]
    retained.append(value)
    return tuple(sorted(retained, key=key))


def with_sleeve(
    overrides: EditorOverrides,
    value: SleeveSiteOverride,
) -> EditorOverrides:
    """替换一个种植位左右导柱共用的语义高度值。"""

    return replace(
        overrides,
        sleeve_sites=_upsert(
            overrides.sleeve_sites,
            value,
            lambda item: item.ring_index,
        ),
    )


def with_operation_window(
    overrides: EditorOverrides,
    value: OperationWindowOverride,
) -> EditorOverrides:
    """替换一个操作窗的语义参数。"""

    return replace(
        overrides,
        operation_windows=_upsert(
            overrides.operation_windows,
            value,
            lambda item: item.site_index,
        ),
    )


def with_observation_window(
    overrides: EditorOverrides,
    value: ObservationWindowOverride,
) -> EditorOverrides:
    """替换一个观察窗的语义参数。"""

    return replace(
        overrides,
        observation_windows=_upsert(
            overrides.observation_windows,
            value,
            lambda item: item.window_id,
        ),
    )


def with_connector(
    overrides: EditorOverrides,
    value: ConnectorAvoidanceOverride,
) -> EditorOverrides:
    """替换一根连接线的独立避让参数。"""

    return replace(
        overrides,
        connector_avoidance=_upsert(
            overrides.connector_avoidance,
            value,
            lambda item: item.guide_index,
        ),
    )


def with_surface_anchor(
    overrides: EditorOverrides,
    value: SurfaceAnchorOverride,
) -> EditorOverrides:
    """替换一个表面锚点的位置和法向。"""

    return replace(
        overrides,
        surface_anchors=_upsert(
            overrides.surface_anchors,
            value,
            lambda item: item.anchor_id,
        ),
    )


def with_press_junction(
    overrides: EditorOverrides,
    position_mm: tuple[float, float, float],
) -> EditorOverrides:
    """替换 Y 型按压梁汇合点。"""

    return replace(overrides, press_junction_mm=position_mm)


__all__ = [
    "with_connector",
    "with_observation_window",
    "with_operation_window",
    "with_press_junction",
    "with_sleeve",
    "with_surface_anchor",
]

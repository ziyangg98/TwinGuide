"""影响生成结果的算法参数解析器。"""

from __future__ import annotations

from dataclasses import fields, replace
from pathlib import Path

from twin_guide.config.parsing import (
    _boolean,
    _mapping,
    _number,
    _positive_integer,
    _reject_unknown,
)
from twin_guide.config.types import (
    AnchorSelectionParameters,
    ConnectorPathParameters,
    ObservationSolverParameters,
    ToothFdiMappingNewProfile,
    ToothIdentificationBackend,
    ToothIdentificationInputs,
)
from twin_guide.errors import ConfigurationError


def parse_tooth_identification(value: object, case_path: Path) -> ToothIdentificationInputs:
    """读取牙位后端及其可复现识别参数。"""

    section = "runtime.tooth_identification"
    raw = _mapping({} if value is None else value, section)
    _reject_unknown(raw, {"backend", "case_yaml", "profile"}, section)
    try:
        backend = ToothIdentificationBackend(
            str(raw.get("backend", ToothIdentificationBackend.FDI_NEW))
        )
    except ValueError as error:
        raise ConfigurationError(
            "runtime.tooth_identification.backend 必须为 standard 或 fdi_new"
        ) from error

    profile_raw = _mapping(raw.get("profile", {}), f"{section}.profile")
    defaults = ToothFdiMappingNewProfile()
    profile_fields = {item.name for item in fields(defaults)}
    _reject_unknown(profile_raw, profile_fields, f"{section}.profile")
    updates: dict[str, object] = {}
    for item in fields(defaults):
        if item.name not in profile_raw:
            continue
        name = f"{section}.profile.{item.name}"
        raw_value = profile_raw[item.name]
        default = getattr(defaults, item.name)
        if isinstance(default, bool):
            updates[item.name] = _boolean(raw_value, name)
        elif isinstance(default, int):
            updates[item.name] = _positive_integer(raw_value, name)
        elif isinstance(default, float):
            updates[item.name] = _number(raw_value, name)
        elif isinstance(default, str):
            text = str(raw_value).strip()
            if not text:
                raise ConfigurationError(f"{name} 不能为空")
            updates[item.name] = text
        elif isinstance(default, tuple):
            if not isinstance(raw_value, list) or not raw_value:
                raise ConfigurationError(f"{name} 必须是非空列表")
            if default and isinstance(default[0], int):
                if any(
                    isinstance(entry, bool) or not isinstance(entry, int) or entry < 0
                    for entry in raw_value
                ):
                    raise ConfigurationError(f"{name} 必须是非负整数列表")
                updates[item.name] = tuple(raw_value)
            else:
                updates[item.name] = tuple(
                    _number(entry, f"{name}[{index}]") for index, entry in enumerate(raw_value)
                )
    try:
        profile = replace(defaults, **updates)
    except ValueError as error:
        raise ConfigurationError(f"{section}.profile: {error}") from error
    return ToothIdentificationInputs(case_path, backend, profile)


def parse_anchor_selection(raw_value: object) -> AnchorSelectionParameters:
    """解析导板表面锚点筛选参数。"""

    section = "geometry.anchor_selection"
    raw = _mapping({} if raw_value is None else raw_value, section)
    defaults = AnchorSelectionParameters()
    _reject_unknown(raw, {item.name for item in fields(defaults)}, section)
    clearance = raw.get("clearance_mm")
    parameters = AnchorSelectionParameters(
        lower_edge_clearance_mm=_number(
            raw.get("lower_edge_clearance_mm", defaults.lower_edge_clearance_mm),
            f"{section}.lower_edge_clearance_mm",
        ),
        axial_margin_mm=_number(
            raw.get("axial_margin_mm", defaults.axial_margin_mm),
            f"{section}.axial_margin_mm",
        ),
        upper_cutter_clearance_mm=_number(
            raw.get("upper_cutter_clearance_mm", defaults.upper_cutter_clearance_mm),
            f"{section}.upper_cutter_clearance_mm",
        ),
        clearance_mm=(None if clearance is None else _number(clearance, f"{section}.clearance_mm")),
        minimum_span_connector_diameters=_number(
            raw.get(
                "minimum_span_connector_diameters",
                defaults.minimum_span_connector_diameters,
            ),
            f"{section}.minimum_span_connector_diameters",
            positive=True,
        ),
        surface_sample_limit=_positive_integer(
            raw.get("surface_sample_limit", defaults.surface_sample_limit),
            f"{section}.surface_sample_limit",
        ),
        candidate_limit=_positive_integer(
            raw.get("candidate_limit", defaults.candidate_limit),
            f"{section}.candidate_limit",
        ),
    )
    if any(
        value is not None and value < 0.0
        for value in (
            parameters.lower_edge_clearance_mm,
            parameters.axial_margin_mm,
            parameters.upper_cutter_clearance_mm,
            parameters.clearance_mm,
        )
    ):
        raise ConfigurationError(f"{section} 的净距和安全余量不得为负")
    return parameters


def parse_connector_path(raw_value: object) -> ConnectorPathParameters:
    """解析连接梁中心线路径参数。"""

    section = "geometry.connector_path"
    raw = _mapping({} if raw_value is None else raw_value, section)
    defaults = ConnectorPathParameters()
    _reject_unknown(raw, {item.name for item in fields(defaults)}, section)
    parameters = ConnectorPathParameters(
        curve_resolution=_positive_integer(
            raw.get("curve_resolution", defaults.curve_resolution),
            f"{section}.curve_resolution",
        ),
        recut_sleeve_bore=_boolean(
            raw.get("recut_sleeve_bore", defaults.recut_sleeve_bore),
            f"{section}.recut_sleeve_bore",
        ),
        endpoint_tension=_number(
            raw.get("endpoint_tension", defaults.endpoint_tension),
            f"{section}.endpoint_tension",
            positive=True,
        ),
        contact_tension=_number(
            raw.get("contact_tension", defaults.contact_tension),
            f"{section}.contact_tension",
            positive=True,
        ),
        lower_approach_overlap_mm=_number(
            raw.get("lower_approach_overlap_mm", defaults.lower_approach_overlap_mm),
            f"{section}.lower_approach_overlap_mm",
        ),
        lower_dive_merge_arc_mm=_number(
            raw.get("lower_dive_merge_arc_mm", defaults.lower_dive_merge_arc_mm),
            f"{section}.lower_dive_merge_arc_mm",
            positive=True,
        ),
        centerline_spacing_mm=_number(
            raw.get("centerline_spacing_mm", defaults.centerline_spacing_mm),
            f"{section}.centerline_spacing_mm",
            positive=True,
        ),
    )
    if parameters.curve_resolution < 8:
        raise ConfigurationError(f"{section}.curve_resolution 不得小于 8")
    if parameters.lower_approach_overlap_mm < 0.0:
        raise ConfigurationError(f"{section}.lower_approach_overlap_mm 不得为负")
    return parameters


def parse_observation_solver(raw_value: object) -> ObservationSolverParameters:
    """解析观察窗布尔求解和 QA 阈值。"""

    section = "windows.observation_solver"
    raw = _mapping({} if raw_value is None else raw_value, section)
    defaults = ObservationSolverParameters()
    fraction_fields = {
        "minimum_axis_visibility_row_fraction",
        "minimum_axis_clear_corridor_fraction",
        "volume_identity_relative_tolerance",
    }
    positive_fields = {item.name for item in fields(defaults) if item.name != "union_batch_size"}
    _reject_unknown(raw, {*positive_fields, "union_batch_size"}, section)
    values = {
        name: _number(
            raw.get(name, getattr(defaults, name)),
            f"{section}.{name}",
            positive=True,
        )
        for name in positive_fields
    }
    for name in fraction_fields:
        if values[name] > 1.0:
            raise ConfigurationError(f"{section}.{name} 必须位于 (0, 1]")
    return ObservationSolverParameters(
        **values,
        union_batch_size=_positive_integer(
            raw.get("union_batch_size", defaults.union_batch_size),
            f"{section}.union_batch_size",
        ),
    )

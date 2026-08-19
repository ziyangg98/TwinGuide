"""病例 YAML 紧凑设计语义到运行时结构的规范化。"""

from __future__ import annotations

from copy import deepcopy

from twin_guide.errors import ConfigurationError

_OBSERVATION_DEFAULTS: dict[str, object] = {
    "extent_mode": "center_to_center",
    "height_mm": 5.0,
    "top_open": True,
    "opening_side": "labial_buccal_exterior",
    "opening_geometry": "axis_sweep",
    "axis_drop_mm": 0.2,
    "sweep_angle_deg": 90.0,
    "angular_spacing_deg": 3.0,
}

_OBSERVATION_OVERRIDE_FIELDS = {
    *_OBSERVATION_DEFAULTS,
    "requested_sections",
    "axis_sections",
    "angle_sections",
    "top_bridge_margin_mm",
    "minimum_bottom_support_mm",
    "maximum_boundary_step_mm",
    "arc_spacing_mm",
    "outward_margin_mm",
    "wall_depth_mode",
    "wall_overcut_mm",
}

_GUIDE_ENDPOINT_IDS = {
    "tooth_section_trajectory": ("station_1", "station_2"),
    "adjacent_two_implant_continuous_paths": ("s_minus", "s_plus"),
    "terminal_distal_common_node": ("s_mesial",),
    "adjacent_two_implant_terminal_distal_node_paths": ("s_mesial",),
    "nearest": (),
}

_GUIDE_SIDES = ("u_side", "back_u_side")

_PRESS_ANCHOR_COUNTS = {
    "disabled": 0,
    "inner_sleeve_upper_y": 2,
    "three_tooth_anchors_y": 3,
    "terminal_u_extension_anchor_y": 2,
}

_PRESS_OVERRIDE_FIELDS = {
    "diameter_mm",
    "guide_overlap_mm",
    "junction_sleeve_distance_mm",
    "junction_axial_lift_mm",
    "minimum_junction_angle_degrees",
    "sleeve_anchor_selection",
    "guide_endpoint",
}

_EXTENSION_OVERRIDE_FIELDS = {
    "selection",
    "start_margin_mm",
    "end_margin_mm",
    "overlap_mm",
}


def _mapping(value: object, name: str) -> dict[str, object]:
    """内部算法说明。"""
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ConfigurationError(f"{name} 必须为对象")
    return value


def _sequence(value: object, name: str) -> list[object]:
    """内部算法说明。"""
    if not isinstance(value, list):
        raise ConfigurationError(f"{name} 必须为数组")
    return value


def _reject_unknown(values: dict[str, object], allowed: set[str], section: str) -> None:
    """内部算法说明。"""
    unknown = sorted(values.keys() - allowed)
    if unknown:
        raise ConfigurationError(f"{section} 包含未知字段：{', '.join(unknown)}")


def _station(value: object, name: str) -> dict[str, object]:
    """把单牙或双牙表示转换为运行时牙位站对象。"""

    if isinstance(value, bool):
        raise ConfigurationError(f"{name} 必须为单个 FDI 或两个 FDI 的数组")
    if isinstance(value, int):
        return {"type": "tooth_center", "fdi": value}
    if (
        isinstance(value, list)
        and len(value) == 2
        and all(isinstance(item, int) and not isinstance(item, bool) for item in value)
    ):
        return {"type": "tooth_pair_midpoint", "fdis": list(value)}
    raise ConfigurationError(f"{name} 必须为单个 FDI 或两个 FDI 的数组")


def _overrides(value: object, allowed: set[str], name: str) -> dict[str, object]:
    """内部算法说明。"""
    if value is None:
        return {}
    result = _mapping(value, name)
    _reject_unknown(result, allowed, name)
    return deepcopy(result)


def _normalize_observation_windows(value: object) -> object:
    """内部算法说明。"""
    if not isinstance(value, list):
        return value
    normalized: list[object] = []
    for index, raw_window in enumerate(value):
        if not isinstance(raw_window, dict) or not ({"fdi", "teeth"} & raw_window.keys()):
            normalized.append(deepcopy(raw_window))
            continue
        section = f"design.observation_windows[{index}]"
        window = _mapping(raw_window, section)
        if "fdi" in window:
            _reject_unknown(window, {"id", "fdi", *_OBSERVATION_OVERRIDE_FIELDS}, section)
            teeth = _sequence(window.get("fdi"), f"{section}.fdi")
            overrides = {
                key: item for key, item in window.items() if key in _OBSERVATION_OVERRIDE_FIELDS
            }
        else:
            _reject_unknown(window, {"id", "teeth", "extent", "overrides"}, section)
            teeth = _sequence(window.get("teeth"), f"{section}.teeth")
            overrides = _overrides(
                window.get("overrides"), _OBSERVATION_OVERRIDE_FIELDS, f"{section}.overrides"
            )
            if "extent" in window and "extent_mode" in overrides:
                raise ConfigurationError(f"{section} 不得同时配置 extent 和 overrides.extent_mode")
            if "extent" in window:
                overrides["extent_mode"] = window["extent"]
        if len(teeth) != 2 or any(
            not isinstance(item, int) or isinstance(item, bool) for item in teeth
        ):
            raise ConfigurationError(f"{section} 的牙位必须包含两个 FDI")
        expanded = {
            "id": str(window.get("id", f"window_{index + 1}")),
            "start_fdi": teeth[0],
            "end_fdi": teeth[1],
            **_OBSERVATION_DEFAULTS,
        }
        expanded.update(deepcopy(overrides))
        normalized.append(expanded)
    return normalized


def _endpoint_side(value: object, name: str) -> tuple[dict[str, object], object]:
    """内部算法说明。"""
    side = _mapping(value, name)
    _reject_unknown(side, {"station", "angle"}, name)
    if "station" not in side or "angle" not in side:
        raise ConfigurationError(f"{name} 必须配置 station 和 angle")
    return _station(side["station"], f"{name}.station"), side["angle"]


def _expand_guide_terminal(
    compact: dict[str, object], expanded: dict[str, object], section: str
) -> None:
    """内部算法说明。"""
    raw_terminal = compact.get("terminal")
    if raw_terminal is None:
        return
    terminal = _mapping(raw_terminal, f"{section}.terminal")
    _reject_unknown(
        terminal,
        {"missing_fdi", "reference_fdi", "implant_fdis", "overrides"},
        f"{section}.terminal",
    )
    if "missing_fdi" not in terminal or "reference_fdi" not in terminal:
        raise ConfigurationError(f"{section}.terminal 必须配置 missing_fdi 和 reference_fdi")
    terminal_overrides = _overrides(
        terminal.get("overrides"),
        {"node_radius_factor", "distal_offset_sleeve_diameters"},
        f"{section}.terminal.overrides",
    )
    expanded_terminal = {
        "missing_fdi": terminal["missing_fdi"],
        "reference_neighbor_fdi": terminal["reference_fdi"],
        **terminal_overrides,
    }
    if "implant_fdis" in terminal:
        expanded_terminal["implant_fdis"] = deepcopy(terminal["implant_fdis"])
    expanded["terminal_distal_common_node"] = expanded_terminal


def _normalize_guide_anchors(value: object) -> object:
    """内部算法说明。"""
    if not isinstance(value, dict):
        return deepcopy(value)
    raw_anchor_values = value.get("anchors", [])
    has_compact_anchors = "anchors" in value and (
        raw_anchor_values == []
        or any(
            isinstance(item, dict) and ({"fdi", "angle"} & item.keys())
            for item in raw_anchor_values or []
        )
    )
    if "endpoints" not in value and not has_compact_anchors:
        return deepcopy(value)
    section = "design.guide_anchors"
    compact = _mapping(value, section)
    mode = str(compact.get("mode", "nearest"))
    if mode not in _GUIDE_ENDPOINT_IDS:
        raise ConfigurationError(f"{section}.mode 不受紧凑病例规范支持：{mode}")
    expected_ids = _GUIDE_ENDPOINT_IDS[mode]
    anchors: list[dict[str, object]] = []
    if "anchors" in compact:
        _reject_unknown(compact, {"mode", "anchors", "terminal"}, section)
        raw_anchors = _sequence(compact.get("anchors"), f"{section}.anchors")
        if len(raw_anchors) != 2 * len(expected_ids):
            raise ConfigurationError(
                f"{section}.{mode} 必须配置 {2 * len(expected_ids)} 个 anchors"
            )
        for index, raw_anchor in enumerate(raw_anchors):
            name = f"{section}.anchors[{index}]"
            anchor = _mapping(raw_anchor, name)
            _reject_unknown(anchor, {"id", "endpoint", "side", "fdi", "angle"}, name)
            if not {"side", "fdi", "angle"} <= anchor.keys():
                raise ConfigurationError(f"{name} 必须配置 side、fdi 和 angle")
            endpoint_id = str(anchor.get("endpoint", expected_ids[index // 2]))
            if endpoint_id not in expected_ids:
                raise ConfigurationError(f"{name}.endpoint 必须属于当前 mode 的端部")
            side = str(anchor["side"])
            if side not in _GUIDE_SIDES:
                raise ConfigurationError(f"{name}.side 必须为 u_side 或 back_u_side")
            suffix = "u" if side == "u_side" else "back_u"
            anchors.append(
                {
                    "id": str(anchor.get("id", f"{endpoint_id}_{suffix}")),
                    "endpoint": endpoint_id,
                    "side": side,
                    "station": _station(anchor["fdi"], f"{name}.fdi"),
                    "ray_angle_degrees": anchor["angle"],
                }
            )
    else:
        _reject_unknown(compact, {"mode", "endpoints", "terminal"}, section)
        endpoints = _sequence(compact.get("endpoints"), f"{section}.endpoints")
        if len(endpoints) != len(expected_ids):
            raise ConfigurationError(f"{section}.{mode} 必须配置 {len(expected_ids)} 个 endpoints")
        for index, raw_endpoint in enumerate(endpoints):
            name = f"{section}.endpoints[{index}]"
            endpoint = _mapping(raw_endpoint, name)
            _reject_unknown(endpoint, {"id", "station", "angles", "u", "back_u"}, name)
            endpoint_id = str(endpoint.get("id", expected_ids[index]))
            has_common = "station" in endpoint or "angles" in endpoint
            has_independent = "u" in endpoint or "back_u" in endpoint
            if has_common == has_independent:
                raise ConfigurationError(f"{name} 必须选择 station+angles 或 u+back_u 之一")
            if has_common:
                if "station" not in endpoint or "angles" not in endpoint:
                    raise ConfigurationError(f"{name} 必须同时配置 station 和 angles")
                station = _station(endpoint["station"], f"{name}.station")
                angles = _sequence(endpoint["angles"], f"{name}.angles")
                if len(angles) != 2:
                    raise ConfigurationError(f"{name}.angles 必须依次包含 U 侧和背 U 侧角度")
                sides = (("u_side", station, angles[0]), ("back_u_side", station, angles[1]))
            else:
                if "u" not in endpoint or "back_u" not in endpoint:
                    raise ConfigurationError(f"{name} 必须同时配置 u 和 back_u")
                u_station, u_angle = _endpoint_side(endpoint["u"], f"{name}.u")
                back_station, back_angle = _endpoint_side(endpoint["back_u"], f"{name}.back_u")
                sides = (
                    ("u_side", u_station, u_angle),
                    ("back_u_side", back_station, back_angle),
                )
            for side, station, angle in sides:
                suffix = "u" if side == "u_side" else "back_u"
                anchors.append(
                    {
                        "id": f"{endpoint_id}_{suffix}",
                        "endpoint": endpoint_id,
                        "side": side,
                        "station": deepcopy(station),
                        "ray_angle_degrees": angle,
                    }
                )
    expanded: dict[str, object] = {"mode": mode, "anchors": anchors}
    _expand_guide_terminal(compact, expanded, section)
    return expanded


def _normalize_press_anchor(value: object, name: str, *, field: str) -> dict[str, object]:
    """内部算法说明。"""
    anchor = _mapping(value, name)
    _reject_unknown(anchor, {"id", field, "angle"}, name)
    if field not in anchor or "angle" not in anchor:
        raise ConfigurationError(f"{name} 必须配置 {field} 和 angle")
    expanded = _station(anchor[field], f"{name}.{field}")
    if "id" in anchor:
        expanded["id"] = anchor["id"]
    expanded["ray_angle_degrees"] = anchor["angle"]
    return expanded


def _normalize_press_beam(value: object) -> object:
    """内部算法说明。"""
    if not isinstance(value, dict):
        return deepcopy(value)
    is_compact = (
        "anchors" in value
        or any(key in value for key in ("overrides", "extension"))
        or any(
            isinstance(item, dict) and "teeth" in item for item in value.get("stations", []) or []
        )
    )
    if not is_compact:
        return deepcopy(value)
    section = "design.press_beam"
    compact = _mapping(value, section)
    _reject_unknown(compact, {"mode", "anchors", "stations", "extension", "overrides"}, section)
    if "anchors" in compact and "stations" in compact:
        raise ConfigurationError(f"{section} 不得同时配置 anchors 和 stations")
    mode = str(compact.get("mode", "disabled"))
    if mode not in _PRESS_ANCHOR_COUNTS:
        raise ConfigurationError(f"{section}.mode 不受紧凑病例规范支持：{mode}")
    if "anchors" in compact:
        raw_anchors = _sequence(compact.get("anchors"), f"{section}.anchors")
        expected_count = _PRESS_ANCHOR_COUNTS[mode]
        if len(raw_anchors) != expected_count:
            raise ConfigurationError(f"{section}.{mode} 必须配置 {expected_count} 个 anchors")
        stations = [
            _normalize_press_anchor(item, f"{section}.anchors[{index}]", field="fdi")
            for index, item in enumerate(raw_anchors)
        ]
    else:
        stations = [
            _normalize_press_anchor(item, f"{section}.stations[{index}]", field="teeth")
            for index, item in enumerate(
                _sequence(compact.get("stations", []), f"{section}.stations")
            )
        ]
    expanded: dict[str, object] = {"mode": mode, "stations": stations}
    expanded.update(
        _overrides(compact.get("overrides"), _PRESS_OVERRIDE_FIELDS, f"{section}.overrides")
    )
    if mode == "inner_sleeve_upper_y" and "sleeve_anchor_selection" not in expanded:
        expanded["sleeve_anchor_selection"] = {
            "candidate_scope": "inner_sleeve_upper_per_implant_site",
            "distance_score": "maximin_to_two_guide_anchors",
            "tie_breaker": "larger_sum_distance",
        }
    raw_extension = compact.get("extension")
    if raw_extension is not None:
        extension = _mapping(raw_extension, f"{section}.extension")
        _reject_unknown(extension, {"segment", "overrides"}, f"{section}.extension")
        if "segment" not in extension:
            raise ConfigurationError(f"{section}.extension 必须配置 segment")
        expanded["extension_anchor"] = {
            "segment": extension["segment"],
            **_overrides(
                extension.get("overrides"),
                _EXTENSION_OVERRIDE_FIELDS,
                f"{section}.extension.overrides",
            ),
        }
    return expanded


def normalize_case_definition(value: object) -> object:
    """返回不修改输入对象的完整运行时病例定义。"""

    if not isinstance(value, dict):
        return value
    result = deepcopy(value)
    raw_design = result.get("design")
    if not isinstance(raw_design, dict):
        return result
    if "observation_windows" in raw_design:
        raw_design["observation_windows"] = _normalize_observation_windows(
            raw_design["observation_windows"]
        )
    if "guide_anchors" in raw_design:
        raw_design["guide_anchors"] = _normalize_guide_anchors(raw_design["guide_anchors"])
    if "press_beam" in raw_design:
        raw_design["press_beam"] = _normalize_press_beam(raw_design["press_beam"])
    return result


__all__ = ["normalize_case_definition"]

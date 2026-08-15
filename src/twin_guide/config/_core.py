"""配置数据类型与解析实现；外部调用统一经过 ``twin_guide.config``。"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, replace
from dataclasses import field as dataclass_field
from pathlib import Path

from twin_guide.config.loading import load_case_yaml
from twin_guide.config.parsing import (
    CASE_ID_PATTERN,
    _boolean,
    _case_id,
    _case_yaml_jaw,
    _json_path,
    _mapping,
    _number,
    _path,
    _positive_integer,
    _reject_unknown,
    _required,
    _section,
    _stl_path,
)
from twin_guide.config.types import (
    DEFAULT_CONNECTOR_DIAMETER_MM,
    DEFAULT_GUIDE_ANCHOR_BACK_U_SIDE_RAY_ANGLE_DEGREES,
    DEFAULT_GUIDE_ANCHOR_U_SIDE_RAY_ANGLE_DEGREES,
    DEFAULT_GUIDE_SPACING_MM,
    DEFAULT_OPERATION_BITANGENT_MARGIN_MM,
    DEFAULT_PRESS_BEAM_DIAMETER_MM,
    ClinicalPlanningParameters,
    ConnectionBlockParameters,
    ConnectorAvoidanceOverride,
    EditorOverrides,
    GeometryParameters,
    GuideAnchorLocation,
    GuideAnchorMode,
    GuideAnchorParameters,
    GuideAnchorSide,
    GuideComponentBridgeMode,
    GuideComponentBridgeParameters,
    GuideComponentBridgeStation,
    GuidePostParameters,
    GuideTerminalUExtensionMode,
    GuideTerminalUExtensionParameters,
    HandpieceAvoidanceParameters,
    InputMeshPaths,
    Jaw,
    ObservationWindowOverride,
    OperationWindowOverride,
    PressBeamExtensionAnchorParameters,
    PressBeamGuideEndpointParameters,
    PressBeamMode,
    PressBeamParameters,
    PressBeamSleeveAnchorSelectionParameters,
    RenderParameters,
    SleeveParameterOverrides,
    SleeveParameters,
    SleeveSiteOverride,
    SurfaceAnchorOverride,
    TerminalDistalCommonNodeParameters,
    ToothAnchorStation,
    ToothIdentificationInputs,
    WindowParameters,
)
from twin_guide.config.validation import _fdi, validate_special_case_anatomy
from twin_guide.errors import ConfigurationError
from twin_guide.guide_post_positioning import calculate_twin_guide_extension_mm


@dataclass(frozen=True, slots=True)
class CaseConfig:
    """构建与检查命令共用的已校验配置。"""

    case_id: str
    jaw: Jaw
    inputs: InputMeshPaths
    sleeve: SleeveParameters
    geometry: GeometryParameters
    windows: WindowParameters
    tooth_identification: ToothIdentificationInputs | None
    handpiece_avoidance: tuple[HandpieceAvoidanceParameters, ...]
    guide_anchors: GuideAnchorParameters
    guide_component_bridge: GuideComponentBridgeParameters
    guide_terminal_u_extension: GuideTerminalUExtensionParameters
    press_beam: PressBeamParameters
    clinical_planning: ClinicalPlanningParameters
    guide_posts: tuple[GuidePostParameters, ...]
    render: RenderParameters
    output_directory: Path
    editor_overrides: EditorOverrides = dataclass_field(default_factory=EditorOverrides)

    @classmethod
    def from_yaml(cls, config_file: str | Path) -> CaseConfig:
        """读取一份完整病例 YAML 并校验运行配置。"""

        path = Path(config_file).resolve()
        if path.suffix.lower() not in {".yaml", ".yml"}:
            raise ConfigurationError("病例配置必须为 YAML 文件")
        raw_value = load_case_yaml(path)
        root = _mapping(raw_value, "configuration")
        _reject_unknown(
            root,
            {
                "schema_version",
                "case",
                "objects",
                "runtime",
                "anatomy",
                "design",
                "planning",
                "review",
                "tooth_recognition",
                "qa",
                "editor_overrides",
            },
            "configuration",
        )
        base_directory = path.parent
        case = _section(root, "case")
        anatomy = _section(root, "anatomy")
        runtime = _section(root, "runtime")
        _reject_unknown(
            runtime,
            {
                "sleeve",
                "geometry",
                "windows",
                "handpiece_avoidance",
                "press_beam",
                "render",
            },
            "runtime",
        )
        yaml_design = _mapping(root.get("design", {}), "design")
        _reject_unknown(
            yaml_design,
            {
                "observation_windows",
                "guide_anchors",
                "press_beam",
                "guide_component_bridge",
                "guide_terminal_u_extension",
                "tube_opening",
                "reinforcement",
                "handpiece_motion",
            },
            "design",
        )
        yaml_planning = _mapping(root.get("planning", {}), "planning")
        tooth_identification = ToothIdentificationInputs(path)
        guide_anchor_raw = _merge_case_design_section(
            None,
            yaml_design.get("guide_anchors"),
            "guide_anchors",
        )
        press_beam_raw = _merge_case_design_section(
            runtime.get("press_beam"),
            yaml_design.get("press_beam"),
            "press_beam",
        )
        bridge_raw = _merge_case_design_section(
            None,
            yaml_design.get("guide_component_bridge"),
            "guide_component_bridge",
        )
        terminal_u_raw = _merge_case_design_section(
            None,
            yaml_design.get("guide_terminal_u_extension"),
            "guide_terminal_u_extension",
        )
        geometry = _parse_geometry(_section(runtime, "geometry"))
        window_raw = _merge_operation_window_parameters(
            _section(runtime, "windows"),
            yaml_planning.get("operation_windows"),
        )
        case_id = _case_id(_required(case, "id"))
        sleeve = _parse_sleeve(_section(runtime, "sleeve"))
        guide_posts = _parse_guide_posts(yaml_planning.get("guide_posts"), sleeve)
        config = cls(
            case_id=case_id,
            jaw=_case_yaml_jaw(_required(anatomy, "jaw")),
            inputs=_parse_case_objects(_section(root, "objects"), base_directory),
            sleeve=sleeve,
            geometry=geometry,
            windows=_parse_windows(
                window_raw,
                default_operation_axial_margin_mm=geometry.channel_axial_margin_mm,
            ),
            tooth_identification=tooth_identification,
            handpiece_avoidance=_parse_handpiece_avoidances(
                runtime.get("handpiece_avoidance"), base_directory
            ),
            guide_anchors=_parse_guide_anchors(guide_anchor_raw),
            guide_component_bridge=_parse_guide_component_bridge(bridge_raw),
            guide_terminal_u_extension=_parse_guide_terminal_u_extension(terminal_u_raw),
            press_beam=_parse_press_beam(press_beam_raw),
            clinical_planning=_parse_clinical_planning(
                yaml_planning.get("clinical_parameters"),
                base_directory,
            ),
            guide_posts=guide_posts,
            editor_overrides=_parse_editor_overrides(root.get("editor_overrides"), guide_posts),
            render=_parse_render(_section(runtime, "render")),
            output_directory=Path(__file__).resolve().parents[3] / "output" / case_id,
        )
        requires_tooth_identification = (
            config.guide_anchors.mode is not GuideAnchorMode.NEAREST
            or config.guide_component_bridge.enabled
            or config.guide_terminal_u_extension.enabled
            or config.press_beam.mode is not PressBeamMode.DISABLED
        )
        if requires_tooth_identification and config.tooth_identification is None:
            raise ConfigurationError(
                "启用牙位锚点、断裂导板桥接、末端 U 型延伸梁或 "
                "Y 型按压梁时必须配置 tooth_identification"
            )
        if (
            config.press_beam.mode is PressBeamMode.TERMINAL_U_EXTENSION_ANCHOR_Y
            and not config.guide_terminal_u_extension.enabled
        ):
            raise ConfigurationError(
                "terminal_u_extension_anchor_y 必须同时启用 guide_terminal_u_extension"
            )
        if (
            config.guide_anchors.terminal_distal_common_node is not None
            and config.guide_terminal_u_extension.enabled
        ):
            raise ConfigurationError(
                "terminal_distal_common_node 与 guide_terminal_u_extension 不得在同一病例中同时启用"
            )
        if (
            config.guide_anchors.terminal_distal_common_node is not None
            and not config.geometry.connection_blocks.lower_main
        ):
            raise ConfigurationError("末端远中公共节点必须保留低位主连接梁")
        if config.tooth_identification is not None:
            validate_special_case_anatomy(config)
        if not config.guide_posts:
            raise ConfigurationError("正式流程必须配置至少一个 planning.guide_posts")
        for guide_post in config.guide_posts:
            effective = guide_post.resolved_sleeve(config.sleeve)
            height_override = config.editor_overrides.sleeve_for(guide_post.ring_index)
            if height_override is not None:
                effective = replace(
                    effective,
                    height_mm=height_override.height_mm,
                    platform_height_mm=height_override.platform_height_mm,
                    closed_bore_height_mm=height_override.closed_bore_height_mm,
                )
            _validate_sleeve(
                effective,
                f"planning.guide_posts[ring_index={guide_post.ring_index}].sleeve",
            )
        return config


def _parse_case_objects(raw: dict[str, object], base_directory: Path) -> InputMeshPaths:
    """从病例对象表解析牙科导板和患者牙列。"""

    _reject_unknown(raw, {"dental", "guide", "handpiece", "cutter"}, "objects")
    dental = _mapping(_required(raw, "dental"), "objects.dental")
    guide = _mapping(_required(raw, "guide"), "objects.guide")
    return InputMeshPaths(
        template=_stl_path(_required(guide, "path"), base_directory, "objects.guide.path"),
        patient_dentition=_stl_path(
            _required(dental, "path"), base_directory, "objects.dental.path"
        ),
    )


def _parse_sleeve(raw: dict[str, object]) -> SleeveParameters:
    """解析并校验导管主体及可选顶部环形凹陷参数。"""

    fields = {
        "inner_diameter_mm",
        "outer_diameter_mm",
        "height_mm",
        "platform_slot_width_mm",
        "platform_height_mm",
        "closed_bore_height_mm",
        "inner_arc_angle_degrees",
        "outer_arc_angle_degrees",
        "guide_spacing_mm",
        "platform_overhang_mm",
        "top_recess_diameter_mm",
        "top_recess_depth_mm",
    }
    _reject_unknown(raw, fields, "sleeve")
    recess_diameter_raw = raw.get("top_recess_diameter_mm")
    recess_depth_raw = raw.get("top_recess_depth_mm")
    outer_diameter = _number(
        _required(raw, "outer_diameter_mm"),
        "sleeve.outer_diameter_mm",
        positive=True,
    )
    slot_width = _number(
        _required(raw, "platform_slot_width_mm"),
        "sleeve.platform_slot_width_mm",
        positive=True,
    )
    if slot_width >= outer_diameter:
        raise ConfigurationError("sleeve.platform_slot_width_mm 必须小于 outer_diameter_mm")
    if (recess_diameter_raw is None) != (recess_depth_raw is None):
        raise ConfigurationError(
            "sleeve.top_recess_diameter_mm 与 sleeve.top_recess_depth_mm 必须同时提供"
        )
    parameters = SleeveParameters(
        inner_diameter_mm=_number(
            _required(raw, "inner_diameter_mm"),
            "sleeve.inner_diameter_mm",
            positive=True,
        ),
        outer_diameter_mm=outer_diameter,
        height_mm=_number(_required(raw, "height_mm"), "sleeve.height_mm", positive=True),
        platform_slot_width_mm=slot_width,
        platform_height_mm=_number(
            _required(raw, "platform_height_mm"),
            "sleeve.platform_height_mm",
            positive=True,
        ),
        closed_bore_height_mm=_number(
            _required(raw, "closed_bore_height_mm"),
            "sleeve.closed_bore_height_mm",
            positive=True,
        ),
        inner_arc_angle_degrees=_number(
            _required(raw, "inner_arc_angle_degrees"),
            "sleeve.inner_arc_angle_degrees",
            positive=True,
        ),
        outer_arc_angle_degrees=_number(
            _required(raw, "outer_arc_angle_degrees"),
            "sleeve.outer_arc_angle_degrees",
            positive=True,
        ),
        guide_spacing_mm=(
            DEFAULT_GUIDE_SPACING_MM
            if "guide_spacing_mm" not in raw
            else _number(
                raw["guide_spacing_mm"],
                "sleeve.guide_spacing_mm",
                positive=True,
            )
        ),
        platform_overhang_mm=(
            0.20
            if "platform_overhang_mm" not in raw
            else _number(
                raw["platform_overhang_mm"],
                "sleeve.platform_overhang_mm",
            )
        ),
        top_recess_diameter_mm=(
            None
            if recess_diameter_raw is None
            else _number(
                recess_diameter_raw,
                "sleeve.top_recess_diameter_mm",
                positive=True,
            )
        ),
        top_recess_depth_mm=(
            0.0
            if recess_depth_raw is None
            else _number(
                recess_depth_raw,
                "sleeve.top_recess_depth_mm",
                positive=True,
            )
        ),
    )
    _validate_sleeve(parameters, "sleeve")
    return parameters


def _validate_sleeve(parameters: SleeveParameters, section: str) -> None:
    """校验一组已经继承完成的导柱参数。"""

    if parameters.outer_diameter_mm <= parameters.inner_diameter_mm:
        raise ConfigurationError(
            f"{section}.outer_diameter_mm 必须大于 {section}.inner_diameter_mm"
        )
    if parameters.platform_slot_width_mm >= parameters.outer_diameter_mm:
        raise ConfigurationError(f"{section}.platform_slot_width_mm 必须小于 outer_diameter_mm")
    if parameters.platform_overhang_mm < 0.0:
        raise ConfigurationError(f"{section}.platform_overhang_mm 不得小于 0")
    for field_name, angle in (
        ("inner_arc_angle_degrees", parameters.inner_arc_angle_degrees),
        ("outer_arc_angle_degrees", parameters.outer_arc_angle_degrees),
    ):
        if angle >= 360.0:
            raise ConfigurationError(f"{section}.{field_name} 必须小于 360")
    if not 180.0 <= parameters.inner_arc_angle_degrees <= 350.0:
        raise ConfigurationError(
            f"{section}.inner_arc_angle_degrees 必须在 180 至 350 之间，"
            "以保证 C 口形态和圆滑过渡可可靠离散"
        )
    if not (
        0.0
        < parameters.closed_bore_height_mm
        < parameters.platform_height_mm
        < parameters.height_mm
    ):
        raise ConfigurationError(
            f"{section} 高度必须满足 0 < closed_bore_height_mm < platform_height_mm < height_mm"
        )
    if parameters.top_recess_diameter_mm is not None:
        if not (
            parameters.inner_diameter_mm
            < parameters.top_recess_diameter_mm
            < parameters.outer_diameter_mm
        ):
            raise ConfigurationError(
                f"{section} 顶部凹陷直径必须满足 inner_diameter_mm < "
                "top_recess_diameter_mm < outer_diameter_mm"
            )
        if parameters.top_recess_depth_mm >= (parameters.height_mm - parameters.platform_height_mm):
            raise ConfigurationError(f"{section}.top_recess_depth_mm 必须小于顶部 C 口段高度")


def _parse_sleeve_overrides(
    raw_value: object,
    name: str,
    defaults: SleeveParameters,
) -> SleeveParameterOverrides:
    """解析一个种植位可省略的三项轴向高度覆盖。"""

    if raw_value is None:
        return SleeveParameterOverrides()
    raw = _mapping(raw_value, name)
    fields = {
        "height_mm",
        "platform_height_mm",
        "closed_bore_height_mm",
    }
    _reject_unknown(raw, fields, name)
    values: dict[str, float | None] = {}
    for field_name in fields:
        if field_name not in raw:
            values[field_name] = None
            continue
        values[field_name] = _number(
            raw[field_name],
            f"{name}.{field_name}",
            positive=True,
        )
    overrides = SleeveParameterOverrides(**values)
    _validate_sleeve(overrides.resolve(defaults), name)
    return overrides


def _parse_guide_endpoint(
    raw_value: object,
    section: str,
) -> PressBeamGuideEndpointParameters:
    """解析一组梁架导板端渐粗、根部球和贴合脚参数。"""

    raw = _mapping(raw_value, section)
    _reject_unknown(
        raw,
        {
            "root_radius_factor",
            "transition_length_mm",
            "bulb_radius_factor",
            "bulb_forward_offset_mm",
            "foot_major_radius_mm",
            "foot_minor_radius_mm",
            "foot_peak_height_mm",
            "foot_embed_depth_mm",
        },
        section,
    )
    parameters = PressBeamGuideEndpointParameters(
        root_radius_factor=_number(
            raw.get("root_radius_factor", 1.08),
            f"{section}.root_radius_factor",
            positive=True,
        ),
        transition_length_mm=_number(
            raw.get("transition_length_mm", 3.0),
            f"{section}.transition_length_mm",
            positive=True,
        ),
        bulb_radius_factor=_number(
            raw.get("bulb_radius_factor", 1.08),
            f"{section}.bulb_radius_factor",
            positive=True,
        ),
        bulb_forward_offset_mm=_number(
            raw.get("bulb_forward_offset_mm", 0.08),
            f"{section}.bulb_forward_offset_mm",
        ),
        foot_major_radius_mm=_number(
            raw.get("foot_major_radius_mm", 3.0),
            f"{section}.foot_major_radius_mm",
            positive=True,
        ),
        foot_minor_radius_mm=_number(
            raw.get("foot_minor_radius_mm", 2.2),
            f"{section}.foot_minor_radius_mm",
            positive=True,
        ),
        foot_peak_height_mm=_number(
            raw.get("foot_peak_height_mm", 2.55),
            f"{section}.foot_peak_height_mm",
            positive=True,
        ),
        foot_embed_depth_mm=_number(
            raw.get("foot_embed_depth_mm", 0.25),
            f"{section}.foot_embed_depth_mm",
            positive=True,
        ),
    )
    if parameters.root_radius_factor < 1.0:
        raise ConfigurationError(f"{section}.root_radius_factor 不得小于 1")
    if parameters.bulb_radius_factor < 1.0:
        raise ConfigurationError(f"{section}.bulb_radius_factor 不得小于 1")
    if parameters.foot_minor_radius_mm > parameters.foot_major_radius_mm:
        raise ConfigurationError(f"{section}.foot_minor_radius_mm 不得大于主半径")
    return parameters


def _parse_connection_blocks(raw_value: object) -> ConnectionBlockParameters:
    """解析可独立启用的主梁和按压梁连接块。"""

    if raw_value is None:
        return ConnectionBlockParameters()
    raw = _mapping(raw_value, "geometry.connection_blocks")
    _reject_unknown(
        raw,
        {"lower_main", "upper_main", "press_beam"},
        "geometry.connection_blocks",
    )
    blocks = ConnectionBlockParameters(
        lower_main=_boolean(raw.get("lower_main", True), "connection_blocks.lower_main"),
        upper_main=_boolean(raw.get("upper_main", True), "connection_blocks.upper_main"),
        press_beam=_boolean(raw.get("press_beam", True), "connection_blocks.press_beam"),
    )
    if not blocks.lower_main and not blocks.upper_main:
        raise ConfigurationError("connection_blocks 至少保留一组主连接梁")
    return blocks


def _parse_geometry(raw: dict[str, object]) -> GeometryParameters:
    """解析并校验通道、连接和融合几何参数。"""

    fields = {
        "channel_axial_margin_mm",
        "connector_diameter_mm",
        "fusion_voxel_size_mm",
        "connector_dental_clearance_mm",
        "sleeve_stop_clearance_mm",
        "sleeve_stop_front_avoidance_mm",
        "connection_blocks",
        "connector_guide_endpoint",
    }
    _reject_unknown(raw, fields, "geometry")
    return GeometryParameters(
        channel_axial_margin_mm=_number(
            _required(raw, "channel_axial_margin_mm"), "geometry.channel_axial_margin_mm"
        ),
        connector_diameter_mm=_number(
            raw.get("connector_diameter_mm", DEFAULT_CONNECTOR_DIAMETER_MM),
            "geometry.connector_diameter_mm",
            positive=True,
        ),
        fusion_voxel_size_mm=_number(
            _required(raw, "fusion_voxel_size_mm"),
            "geometry.fusion_voxel_size_mm",
            positive=True,
        ),
        connector_dental_clearance_mm=_number(
            raw.get("connector_dental_clearance_mm", 0.20),
            "geometry.connector_dental_clearance_mm",
        ),
        sleeve_stop_clearance_mm=_number(
            raw.get("sleeve_stop_clearance_mm", 2.0),
            "geometry.sleeve_stop_clearance_mm",
        ),
        sleeve_stop_front_avoidance_mm=_number(
            raw.get("sleeve_stop_front_avoidance_mm", 0.0),
            "geometry.sleeve_stop_front_avoidance_mm",
        ),
        connection_blocks=_parse_connection_blocks(raw.get("connection_blocks")),
        connector_guide_endpoint=_parse_guide_endpoint(
            raw.get("connector_guide_endpoint", {}),
            "geometry.connector_guide_endpoint",
        ),
    )


def _parse_windows(
    raw: dict[str, object],
    *,
    default_operation_axial_margin_mm: float,
) -> WindowParameters:
    """解析操作窗和轴扫掠观察窗参数。"""

    fields = {
        "operation_tangent_margin_mm",
        "operation_bitangent_margin_mm",
        "operation_axial_margin_mm",
        "operation_front_axial_margin_mm",
        "operation_rear_axial_margin_mm",
        "operation_corner_radius_mm",
        "observation_axis_drop_mm",
        "observation_sweep_angle_degrees",
        "observation_local_failure_drop_targets_mm",
        "observation_local_failure_transition_rows",
    }
    _reject_unknown(raw, fields, "windows")
    axis_drop_mm = _number(
        raw.get("observation_axis_drop_mm", 0.2),
        "windows.observation_axis_drop_mm",
        positive=True,
    )
    sweep_angle_degrees = _number(
        raw.get("observation_sweep_angle_degrees", 90.0),
        "windows.observation_sweep_angle_degrees",
        positive=True,
    )
    if sweep_angle_degrees > 180.0:
        raise ConfigurationError("windows.observation_sweep_angle_degrees 必须小于或等于 180")
    raw_targets = raw.get("observation_local_failure_drop_targets_mm", [0.5, 1.0, 2.0])
    if not isinstance(raw_targets, list) or not raw_targets:
        raise ConfigurationError("windows.observation_local_failure_drop_targets_mm 必须为非空数组")
    targets = tuple(
        _number(
            value,
            f"windows.observation_local_failure_drop_targets_mm[{index}]",
            positive=True,
        )
        for index, value in enumerate(raw_targets)
    )
    if any(target <= axis_drop_mm for target in targets):
        raise ConfigurationError("观察窗局部失败高度目标必须全部大于全局高度")
    if any(later <= earlier for earlier, later in itertools.pairwise(targets)):
        raise ConfigurationError("观察窗局部失败高度目标必须严格递增")
    transition_rows_value = raw.get("observation_local_failure_transition_rows", 1)
    if (
        isinstance(transition_rows_value, bool)
        or not isinstance(transition_rows_value, int)
        or transition_rows_value < 0
    ):
        raise ConfigurationError("windows.observation_local_failure_transition_rows 必须为非负整数")
    operation_bitangent_margin_mm = _number(
        raw.get(
            "operation_bitangent_margin_mm",
            DEFAULT_OPERATION_BITANGENT_MARGIN_MM,
        ),
        "windows.operation_bitangent_margin_mm",
    )
    operation_axial_margin_mm = _number(
        raw.get(
            "operation_axial_margin_mm",
            default_operation_axial_margin_mm,
        ),
        "windows.operation_axial_margin_mm",
    )
    return WindowParameters(
        operation_tangent_margin_mm=_number(
            _required(raw, "operation_tangent_margin_mm"),
            "windows.operation_tangent_margin_mm",
        ),
        operation_bitangent_margin_mm=operation_bitangent_margin_mm,
        operation_axial_margin_mm=operation_axial_margin_mm,
        operation_front_axial_margin_mm=_number(
            raw.get("operation_front_axial_margin_mm", operation_axial_margin_mm),
            "windows.operation_front_axial_margin_mm",
        ),
        operation_rear_axial_margin_mm=_number(
            raw.get("operation_rear_axial_margin_mm", operation_axial_margin_mm),
            "windows.operation_rear_axial_margin_mm",
        ),
        operation_corner_radius_mm=_number(
            raw.get(
                "operation_corner_radius_mm",
                min(
                    1.0,
                    max(0.2, operation_bitangent_margin_mm),
                ),
            ),
            "windows.operation_corner_radius_mm",
        ),
        observation_axis_drop_mm=axis_drop_mm,
        observation_sweep_angle_degrees=sweep_angle_degrees,
        observation_local_failure_drop_targets_mm=targets,
        observation_local_failure_transition_rows=transition_rows_value,
    )


def _parse_single_handpiece_avoidance(
    raw_value: object,
    base_directory: Path,
    index: int,
) -> HandpieceAvoidanceParameters:
    """解析一个当前深度牙科手机左右摆动避障项。"""

    section = f"handpiece_avoidance[{index}]"
    raw = _mapping(raw_value, section)
    _reject_unknown(
        raw,
        {
            "id",
            "handpiece",
            "stop_report",
            "maximum_angle_degrees",
            "pose_samples",
            "union_batch_size",
            "extra_clearance_mm",
        },
        section,
    )
    raw_id = raw.get("id", f"handpiece_{index + 1}")
    avoidance_id = _case_id(raw_id)
    maximum_angle_degrees = _number(
        raw.get("maximum_angle_degrees", 5.0),
        "handpiece_avoidance.maximum_angle_degrees",
        positive=True,
    )
    if maximum_angle_degrees > 45.0:
        raise ConfigurationError("handpiece_avoidance.maximum_angle_degrees 必须小于或等于 45")
    pose_samples = _positive_integer(
        raw.get("pose_samples", 41),
        "handpiece_avoidance.pose_samples",
    )
    if pose_samples < 3 or pose_samples % 2 == 0:
        raise ConfigurationError(
            "handpiece_avoidance.pose_samples 必须为不小于 3 的奇数，以包含 0° 姿态"
        )
    union_batch_size = _positive_integer(
        raw.get("union_batch_size", 7),
        "handpiece_avoidance.union_batch_size",
    )
    if union_batch_size < 2:
        raise ConfigurationError("handpiece_avoidance.union_batch_size 必须不小于 2")
    return HandpieceAvoidanceParameters(
        avoidance_id=avoidance_id,
        handpiece=_stl_path(
            _required(raw, "handpiece"),
            base_directory,
            "handpiece_avoidance.handpiece",
        ),
        stop_report=_json_path(
            _required(raw, "stop_report"),
            base_directory,
            "handpiece_avoidance.stop_report",
        ),
        maximum_angle_degrees=maximum_angle_degrees,
        pose_samples=pose_samples,
        union_batch_size=union_batch_size,
        extra_clearance_mm=_number(
            raw.get("extra_clearance_mm", 0.0),
            "handpiece_avoidance.extra_clearance_mm",
        ),
    )


def _parse_handpiece_avoidances(
    raw_value: object,
    base_directory: Path,
) -> tuple[HandpieceAvoidanceParameters, ...]:
    """解析零个、单个或多个牙科手机避障项并校验编号唯一。"""

    if raw_value is None:
        return ()
    raw_items = raw_value if isinstance(raw_value, list) else [raw_value]
    if not raw_items:
        raise ConfigurationError("handpiece_avoidance 数组不得为空")
    items = tuple(
        _parse_single_handpiece_avoidance(item, base_directory, index)
        for index, item in enumerate(raw_items)
    )
    identifiers = tuple(item.avoidance_id for item in items)
    if len(set(identifiers)) != len(identifiers):
        raise ConfigurationError("handpiece_avoidance.id 不得重复")
    return items


def _merge_operation_window_parameters(
    runtime_windows: dict[str, object],
    yaml_value: object,
) -> dict[str, object]:
    """将病例规划中的操作窗参数合入同一 YAML 的运行参数。"""

    if yaml_value is None:
        return runtime_windows
    yaml_windows = _mapping(
        yaml_value,
        "case.yaml planning.operation_windows",
    )
    _reject_unknown(
        yaml_windows,
        {
            "mode",
            "center_mode",
            "axis_mode",
            "tangent_margin_mm",
            "bitangent_margin_mm",
            "axial_margin_mm",
            "front_axial_margin_mm",
            "rear_axial_margin_mm",
            "corner_radius_mm",
            "overlap_rule",
            "cut_target",
            "sites",
        },
        "case.yaml planning.operation_windows",
    )
    supported_values = {
        "mode": "per_implant_site",
        "center_mode": "paired_sleeve_operation_feature",
        "axis_mode": "paired_sleeve_average_axis",
        "overlap_rule": "union_cutters",
        "cut_target": "guide_template_only",
    }
    for field, expected in supported_values.items():
        value = yaml_windows.get(field, expected)
        if value != expected:
            raise ConfigurationError(
                f"case.yaml planning.operation_windows.{field} 当前仅支持 {expected}"
            )
    if "sites" in yaml_windows and not isinstance(yaml_windows["sites"], list):
        raise ConfigurationError("case.yaml planning.operation_windows.sites 必须为数组")
    key_map = {
        "tangent_margin_mm": "operation_tangent_margin_mm",
        "bitangent_margin_mm": "operation_bitangent_margin_mm",
        "axial_margin_mm": "operation_axial_margin_mm",
        "front_axial_margin_mm": "operation_front_axial_margin_mm",
        "rear_axial_margin_mm": "operation_rear_axial_margin_mm",
        "corner_radius_mm": "operation_corner_radius_mm",
    }
    overrides = {
        target: yaml_windows[source] for source, target in key_map.items() if source in yaml_windows
    }
    return {**runtime_windows, **overrides}


def _optional_text(value: object, name: str) -> str | None:
    """读取可选非空文本参数。"""

    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{name} 必须为非空字符串")
    return value.strip()


def _vector3(value: object, name: str) -> tuple[float, float, float]:
    """解析允许正负值的有限三维向量。"""

    if (
        not isinstance(value, list | tuple)
        or len(value) != 3
        or any(isinstance(item, bool) or not isinstance(item, int | float) for item in value)
    ):
        raise ConfigurationError(f"{name} 必须为三元素数值数组")
    result = tuple(float(item) for item in value)
    if not all(math.isfinite(item) for item in result):
        raise ConfigurationError(f"{name} 必须为有限三维向量")
    return result


def _editor_items(raw: dict[str, object], key: str) -> list[object]:
    """返回编辑器覆盖分组中的数组。"""

    value = raw.get(key, [])
    if not isinstance(value, list):
        raise ConfigurationError(f"editor_overrides.{key} 必须为数组")
    return value


def _parse_editor_overrides(
    raw_value: object,
    guide_posts: tuple[GuidePostParameters, ...],
) -> EditorOverrides:
    """解析 Blender 图形化编辑器写回的显式几何覆盖值。"""

    if raw_value is None:
        return EditorOverrides()
    raw = _mapping(raw_value, "editor_overrides")
    _reject_unknown(
        raw,
        {
            "sleeve_sites",
            "sleeve_guides",
            "operation_windows",
            "observation_windows",
            "connector_avoidance",
            "surface_anchors",
            "press_junction_mm",
        },
        "editor_overrides",
    )
    sleeve_sites = []
    for index, value in enumerate(_editor_items(raw, "sleeve_sites")):
        item = _mapping(value, f"editor_overrides.sleeve_sites[{index}]")
        _reject_unknown(
            item,
            {"ring_index", "height_mm", "platform_height_mm", "closed_bore_height_mm"},
            f"editor_overrides.sleeve_sites[{index}]",
        )
        try:
            sleeve_site = SleeveSiteOverride(
                ring_index=_positive_integer(
                    _required(item, "ring_index"),
                    f"editor_overrides.sleeve_sites[{index}].ring_index",
                ),
                height_mm=_number(
                    _required(item, "height_mm"),
                    f"editor_overrides.sleeve_sites[{index}].height_mm",
                    positive=True,
                ),
                platform_height_mm=_number(
                    _required(item, "platform_height_mm"),
                    f"editor_overrides.sleeve_sites[{index}].platform_height_mm",
                    positive=True,
                ),
                closed_bore_height_mm=_number(
                    _required(item, "closed_bore_height_mm"),
                    f"editor_overrides.sleeve_sites[{index}].closed_bore_height_mm",
                    positive=True,
                ),
            )
        except ValueError as error:
            raise ConfigurationError(f"editor_overrides.sleeve_sites[{index}]: {error}") from error
        sleeve_sites.append(sleeve_site)

    legacy_sleeves: list[tuple[int, float, float, float]] = []
    for index, value in enumerate(_editor_items(raw, "sleeve_guides")):
        item = _mapping(value, f"editor_overrides.sleeve_guides[{index}]")
        _reject_unknown(
            item,
            {"guide_index", "height_mm", "platform_height_mm", "closed_bore_height_mm"},
            f"editor_overrides.sleeve_guides[{index}]",
        )
        legacy_sleeves.append(
            (
                _positive_integer(
                    _required(item, "guide_index"),
                    f"editor_overrides.sleeve_guides[{index}].guide_index",
                ),
                _number(
                    _required(item, "height_mm"),
                    f"editor_overrides.sleeve_guides[{index}].height_mm",
                    positive=True,
                ),
                _number(
                    _required(item, "platform_height_mm"),
                    f"editor_overrides.sleeve_guides[{index}].platform_height_mm",
                    positive=True,
                ),
                _number(
                    _required(item, "closed_bore_height_mm"),
                    f"editor_overrides.sleeve_guides[{index}].closed_bore_height_mm",
                    positive=True,
                ),
            )
        )
    if sleeve_sites and legacy_sleeves:
        raise ConfigurationError("editor_overrides.sleeve_sites 与旧式 sleeve_guides 不得同时提供")
    if legacy_sleeves:
        legacy_by_guide = {item[0]: item[1:] for item in legacy_sleeves}
        if len(legacy_by_guide) != len(legacy_sleeves):
            raise ConfigurationError("editor_overrides.sleeve_guides 导柱编号不得重复")
        expected_indices = set(range(1, 2 * len(guide_posts) + 1))
        if not set(legacy_by_guide) <= expected_indices:
            raise ConfigurationError("editor_overrides.sleeve_guides 包含未知导柱编号")
        for site_index, guide_post in enumerate(guide_posts):
            left = legacy_by_guide.get(2 * site_index + 1)
            right = legacy_by_guide.get(2 * site_index + 2)
            if left is None and right is None:
                continue
            left_display = tuple(round(value, 2) for value in left) if left is not None else None
            right_display = tuple(round(value, 2) for value in right) if right is not None else None
            matching_pair = left_display is not None and left_display == right_display
            if not matching_pair:
                raise ConfigurationError(
                    "旧式 editor_overrides.sleeve_guides 必须为同一种植位的左右导柱"
                    "提供保留两位小数后完全一致的高度，才能迁移为 sleeve_sites"
                )
            assert left_display is not None
            try:
                migrated = SleeveSiteOverride(guide_post.ring_index, *left_display)
            except ValueError as error:
                raise ConfigurationError(
                    f"旧式 editor_overrides.sleeve_guides 保留两位小数后的高度无效：{error}"
                ) from error
            sleeve_sites.append(migrated)
    known_ring_indices = {item.ring_index for item in guide_posts}
    unknown_ring_indices = {
        item.ring_index for item in sleeve_sites if item.ring_index not in known_ring_indices
    }
    if unknown_ring_indices:
        joined = ", ".join(map(str, sorted(unknown_ring_indices)))
        raise ConfigurationError(f"editor_overrides.sleeve_sites 包含未知 ring_index：{joined}")
    operation_windows = []
    for index, value in enumerate(_editor_items(raw, "operation_windows")):
        item = _mapping(value, f"editor_overrides.operation_windows[{index}]")
        _reject_unknown(
            item,
            {
                "site_index",
                "tangent_margin_mm",
                "bitangent_margin_mm",
                "front_axial_margin_mm",
                "rear_axial_margin_mm",
                "center_offset_mm",
            },
            f"editor_overrides.operation_windows[{index}]",
        )
        operation_windows.append(
            OperationWindowOverride(
                site_index=_positive_integer(
                    _required(item, "site_index"),
                    f"editor_overrides.operation_windows[{index}].site_index",
                ),
                tangent_margin_mm=_number(
                    _required(item, "tangent_margin_mm"),
                    f"editor_overrides.operation_windows[{index}].tangent_margin_mm",
                ),
                bitangent_margin_mm=_number(
                    _required(item, "bitangent_margin_mm"),
                    f"editor_overrides.operation_windows[{index}].bitangent_margin_mm",
                ),
                front_axial_margin_mm=_number(
                    _required(item, "front_axial_margin_mm"),
                    f"editor_overrides.operation_windows[{index}].front_axial_margin_mm",
                ),
                rear_axial_margin_mm=_number(
                    _required(item, "rear_axial_margin_mm"),
                    f"editor_overrides.operation_windows[{index}].rear_axial_margin_mm",
                ),
                center_offset_mm=_vector3(
                    item.get("center_offset_mm", (0.0, 0.0, 0.0)),
                    f"editor_overrides.operation_windows[{index}].center_offset_mm",
                ),
            )
        )
    observation_windows = []
    for index, value in enumerate(_editor_items(raw, "observation_windows")):
        item = _mapping(value, f"editor_overrides.observation_windows[{index}]")
        _reject_unknown(
            item,
            {
                "window_id",
                "start_fdi",
                "end_fdi",
                "axis_drop_mm",
                "height_mm",
                "sweep_angle_degrees",
            },
            f"editor_overrides.observation_windows[{index}]",
        )
        observation_windows.append(
            ObservationWindowOverride(
                window_id=_optional_text(
                    _required(item, "window_id"),
                    f"editor_overrides.observation_windows[{index}].window_id",
                )
                or "",
                start_fdi=_positive_integer(
                    _required(item, "start_fdi"),
                    f"editor_overrides.observation_windows[{index}].start_fdi",
                ),
                end_fdi=_positive_integer(
                    _required(item, "end_fdi"),
                    f"editor_overrides.observation_windows[{index}].end_fdi",
                ),
                axis_drop_mm=_number(
                    _required(item, "axis_drop_mm"),
                    f"editor_overrides.observation_windows[{index}].axis_drop_mm",
                ),
                height_mm=_number(
                    _required(item, "height_mm"),
                    f"editor_overrides.observation_windows[{index}].height_mm",
                    positive=True,
                ),
                sweep_angle_degrees=_number(
                    _required(item, "sweep_angle_degrees"),
                    f"editor_overrides.observation_windows[{index}].sweep_angle_degrees",
                    positive=True,
                ),
            )
        )
    connectors = []
    for index, value in enumerate(_editor_items(raw, "connector_avoidance")):
        item = _mapping(value, f"editor_overrides.connector_avoidance[{index}]")
        _reject_unknown(
            item,
            {"guide_index", "path_fraction", "downward_offset_mm"},
            f"editor_overrides.connector_avoidance[{index}]",
        )
        connectors.append(
            ConnectorAvoidanceOverride(
                guide_index=_positive_integer(
                    _required(item, "guide_index"),
                    f"editor_overrides.connector_avoidance[{index}].guide_index",
                ),
                path_fraction=_number(
                    _required(item, "path_fraction"),
                    f"editor_overrides.connector_avoidance[{index}].path_fraction",
                ),
                downward_offset_mm=_number(
                    _required(item, "downward_offset_mm"),
                    f"editor_overrides.connector_avoidance[{index}].downward_offset_mm",
                ),
            )
        )
    anchors = []
    for index, value in enumerate(_editor_items(raw, "surface_anchors")):
        item = _mapping(value, f"editor_overrides.surface_anchors[{index}]")
        _reject_unknown(
            item,
            {"anchor_id", "surface_role", "position_mm", "normal"},
            f"editor_overrides.surface_anchors[{index}]",
        )
        anchors.append(
            SurfaceAnchorOverride(
                anchor_id=_optional_text(
                    _required(item, "anchor_id"),
                    f"editor_overrides.surface_anchors[{index}].anchor_id",
                )
                or "",
                surface_role=_optional_text(
                    _required(item, "surface_role"),
                    f"editor_overrides.surface_anchors[{index}].surface_role",
                )
                or "",
                position_mm=_vector3(
                    _required(item, "position_mm"),
                    f"editor_overrides.surface_anchors[{index}].position_mm",
                ),
                normal=_vector3(
                    _required(item, "normal"),
                    f"editor_overrides.surface_anchors[{index}].normal",
                ),
            )
        )
    for name, values, key in (
        ("导柱种植位", sleeve_sites, "ring_index"),
        ("操作窗口", operation_windows, "site_index"),
        ("观察窗口", observation_windows, "window_id"),
        ("连接节点", connectors, "guide_index"),
        ("表面锚点", anchors, "anchor_id"),
    ):
        identifiers = [getattr(item, key) for item in values]
        if len(set(identifiers)) != len(identifiers):
            raise ConfigurationError(f"editor_overrides.{name} 编号不得重复")
    return EditorOverrides(
        sleeve_sites=tuple(sleeve_sites),
        operation_windows=tuple(operation_windows),
        observation_windows=tuple(observation_windows),
        connector_avoidance=tuple(connectors),
        surface_anchors=tuple(anchors),
        press_junction_mm=(
            None
            if "press_junction_mm" not in raw
            else _vector3(raw["press_junction_mm"], "editor_overrides.press_junction_mm")
        ),
    )


def _parse_clinical_planning(
    raw_value: object,
    base_directory: Path,
) -> ClinicalPlanningParameters:
    """解析尚需临床定义确认的坐标、延长量和高度计算输入。"""

    if raw_value is None:
        return ClinicalPlanningParameters()
    raw = _mapping(raw_value, "planning.clinical_parameters")
    _reject_unknown(
        raw,
        {
            "implant_coordinates_path",
            "implant_coordinates_format",
            "extension_mm",
            "extension_definition",
            "mouth_opening_mm",
            "adapter_length_mm",
            "height_formula_id",
        },
        "planning.clinical_parameters",
    )
    coordinates_path = None
    if "implant_coordinates_path" in raw:
        coordinates_path = _path(
            raw["implant_coordinates_path"],
            base_directory,
            "planning.clinical_parameters.implant_coordinates_path",
        )
        if not coordinates_path.is_file():
            raise ConfigurationError(
                "planning.clinical_parameters.implant_coordinates_path "
                f"必须指向已存在的文件：{coordinates_path}"
            )
    coordinates_format = _optional_text(
        raw.get("implant_coordinates_format"),
        "planning.clinical_parameters.implant_coordinates_format",
    )
    if (coordinates_path is None) != (coordinates_format is None):
        raise ConfigurationError("种植体坐标路径和格式必须同时提供")
    extension_mm = (
        None
        if "extension_mm" not in raw
        else _number(raw["extension_mm"], "planning.clinical_parameters.extension_mm")
    )
    extension_definition = _optional_text(
        raw.get("extension_definition"),
        "planning.clinical_parameters.extension_definition",
    )
    if (extension_mm is None) != (extension_definition is None):
        raise ConfigurationError("延长量数值和定义必须同时提供")
    return ClinicalPlanningParameters(
        implant_coordinates_path=coordinates_path,
        implant_coordinates_format=coordinates_format,
        extension_mm=extension_mm,
        extension_definition=extension_definition,
        mouth_opening_mm=(
            None
            if "mouth_opening_mm" not in raw
            else _number(
                raw["mouth_opening_mm"],
                "planning.clinical_parameters.mouth_opening_mm",
                positive=True,
            )
        ),
        adapter_length_mm=(
            None
            if "adapter_length_mm" not in raw
            else _number(
                raw["adapter_length_mm"],
                "planning.clinical_parameters.adapter_length_mm",
                positive=True,
            )
        ),
        height_formula_id=_optional_text(
            raw.get("height_formula_id"),
            "planning.clinical_parameters.height_formula_id",
        ),
    )


def _parse_guide_posts(
    raw_value: object,
    sleeve_defaults: SleeveParameters,
) -> tuple[GuidePostParameters, ...]:
    """解析各识别圆环对应的钻针长度和植体长度。"""

    if raw_value is None:
        return ()
    if not isinstance(raw_value, list):
        raise ConfigurationError("planning.guide_posts 必须为数组")
    guide_posts: list[GuidePostParameters] = []
    for index, raw_item in enumerate(raw_value):
        name = f"planning.guide_posts[{index}]"
        item = _mapping(raw_item, name)
        _reject_unknown(
            item,
            {
                "ring_index",
                "drill_length_mm",
                "implant_length_mm",
                "sleeve_template_extension_mm",
                "sleeve",
            },
            name,
        )
        ring_index = _positive_integer(
            _required(item, "ring_index"),
            f"{name}.ring_index",
        )
        drill_length = _number(
            _required(item, "drill_length_mm"),
            f"{name}.drill_length_mm",
            positive=True,
        )
        implant_length = _number(
            _required(item, "implant_length_mm"),
            f"{name}.implant_length_mm",
            positive=True,
        )
        sleeve_template_extension = _number(
            _required(item, "sleeve_template_extension_mm"),
            f"{name}.sleeve_template_extension_mm",
            positive=True,
        )
        try:
            calculate_twin_guide_extension_mm(drill_length, implant_length)
        except ValueError as error:
            raise ConfigurationError(f"{name}: {error}") from error
        guide_posts.append(
            GuidePostParameters(
                ring_index=ring_index,
                drill_length_mm=drill_length,
                implant_length_mm=implant_length,
                sleeve_template_extension_mm=sleeve_template_extension,
                sleeve=_parse_sleeve_overrides(
                    item.get("sleeve"),
                    f"{name}.sleeve",
                    sleeve_defaults,
                ),
            )
        )
    ring_indices = [item.ring_index for item in guide_posts]
    if len(set(ring_indices)) != len(ring_indices):
        raise ConfigurationError("planning.guide_posts 的 ring_index 不得重复")
    return tuple(sorted(guide_posts, key=lambda item: item.ring_index))


def _merge_case_design_section(
    runtime_value: object,
    design_value: object,
    section: str,
) -> dict[str, object] | None:
    """合并同一 YAML 的运行参数与病例设计，并拒绝重复字段。"""

    if runtime_value is None and design_value is None:
        return None
    runtime_section = {} if runtime_value is None else _mapping(runtime_value, f"runtime.{section}")
    design_section = {} if design_value is None else _mapping(design_value, f"design.{section}")
    duplicates = sorted(runtime_section.keys() & design_section.keys())
    if duplicates:
        raise ConfigurationError(
            f"{section} 在 runtime 与 design 中重复配置字段：{', '.join(duplicates)}"
        )
    return {**runtime_section, **design_section}


def _parse_guide_anchors(raw_value: object) -> GuideAnchorParameters:
    """解析可选的导板侧牙位截面轨迹锚点配置。"""

    if raw_value is None:
        return GuideAnchorParameters()
    raw = _mapping(raw_value, "guide_anchors")
    _reject_unknown(
        raw,
        {
            "mode",
            "anchors",
            "stations",
            "u_side_ray_angle_degrees",
            "back_u_side_ray_angle_degrees",
            "terminal_distal_common_node",
        },
        "guide_anchors",
    )
    try:
        mode = GuideAnchorMode(str(raw.get("mode", GuideAnchorMode.NEAREST.value)))
    except ValueError as error:
        raise ConfigurationError(
            "guide_anchors.mode 必须为 nearest、tooth_section_trajectory "
            "、adjacent_two_implant_continuous_paths、"
            "terminal_distal_common_node 或 "
            "adjacent_two_implant_terminal_distal_node_paths"
        ) from error
    if "anchors" in raw and "stations" in raw:
        raise ConfigurationError("guide_anchors 不得同时配置 anchors 和旧版 stations")
    terminal_distal_common_node = None
    raw_terminal_anchor = raw.get("terminal_distal_common_node")
    if raw_terminal_anchor is not None:
        terminal = _mapping(
            raw_terminal_anchor,
            "guide_anchors.terminal_distal_common_node",
        )
        _reject_unknown(
            terminal,
            {
                "missing_fdi",
                "reference_neighbor_fdi",
                "implant_fdis",
                "node_radius_factor",
                "distal_offset_sleeve_diameters",
            },
            "guide_anchors.terminal_distal_common_node",
        )
        missing_fdi = _fdi(
            _required(terminal, "missing_fdi"),
            "guide_anchors.terminal_distal_common_node.missing_fdi",
        )
        neighbor_fdi = _fdi(
            _required(terminal, "reference_neighbor_fdi"),
            "guide_anchors.terminal_distal_common_node.reference_neighbor_fdi",
        )
        if missing_fdi == neighbor_fdi:
            raise ConfigurationError("末端缺牙牙位与参考邻牙不得相同")
        raw_implant_fdis = terminal.get("implant_fdis", [])
        if not isinstance(raw_implant_fdis, list):
            raise ConfigurationError(
                "guide_anchors.terminal_distal_common_node.implant_fdis 必须为 FDI 数组"
            )
        implant_fdis = tuple(
            _fdi(
                value,
                f"guide_anchors.terminal_distal_common_node.implant_fdis[{index}]",
            )
            for index, value in enumerate(raw_implant_fdis)
        )
        node_radius_factor = _number(
            terminal.get("node_radius_factor", 1.12),
            "guide_anchors.terminal_distal_common_node.node_radius_factor",
            positive=True,
        )
        if node_radius_factor < 1.0:
            raise ConfigurationError("远中公共节点半径系数不得小于 1.0")
        distal_offset_sleeve_diameters = _number(
            terminal.get("distal_offset_sleeve_diameters", 2.0),
            "guide_anchors.terminal_distal_common_node.distal_offset_sleeve_diameters",
            positive=True,
        )
        if abs(distal_offset_sleeve_diameters - 2.0) > 1e-9:
            raise ConfigurationError("远中公共节点必须固定沿远中方向移动 2 个平均导管外径")
        terminal_distal_common_node = TerminalDistalCommonNodeParameters(
            missing_fdi=missing_fdi,
            reference_neighbor_fdi=neighbor_fdi,
            implant_fdis=implant_fdis,
            node_radius_factor=node_radius_factor,
            distal_offset_sleeve_diameters=distal_offset_sleeve_diameters,
        )
    if (
        mode
        in {
            GuideAnchorMode.TERMINAL_DISTAL_COMMON_NODE,
            GuideAnchorMode.ADJACENT_TWO_IMPLANT_TERMINAL_DISTAL_NODE_PATHS,
        }
        and terminal_distal_common_node is None
    ):
        raise ConfigurationError("末端远中公共节点模式必须配置 terminal_distal_common_node")
    if (
        mode
        not in {
            GuideAnchorMode.TERMINAL_DISTAL_COMMON_NODE,
            GuideAnchorMode.ADJACENT_TWO_IMPLANT_TERMINAL_DISTAL_NODE_PATHS,
        }
        and terminal_distal_common_node is not None
    ):
        raise ConfigurationError(
            "只有 terminal_distal_common_node 可以配置 terminal_distal_common_node"
        )
    u_side_angle = _ray_angle_degrees(
        raw.get(
            "u_side_ray_angle_degrees",
            DEFAULT_GUIDE_ANCHOR_U_SIDE_RAY_ANGLE_DEGREES,
        ),
        "guide_anchors.u_side_ray_angle_degrees",
    )
    back_u_side_angle = _ray_angle_degrees(
        raw.get(
            "back_u_side_ray_angle_degrees",
            DEFAULT_GUIDE_ANCHOR_BACK_U_SIDE_RAY_ANGLE_DEGREES,
        ),
        "guide_anchors.back_u_side_ray_angle_degrees",
    )
    if mode is GuideAnchorMode.NEAREST and (
        "u_side_ray_angle_degrees" in raw or "back_u_side_ray_angle_degrees" in raw
    ):
        raise ConfigurationError("nearest 锚点模式不得配置旋转射线角度")
    stations = _parse_tooth_anchor_stations(
        raw.get("stations", []),
        "guide_anchors.stations",
    )
    if "anchors" in raw:
        if "u_side_ray_angle_degrees" in raw or "back_u_side_ray_angle_degrees" in raw:
            raise ConfigurationError("独立 anchors 模式的角度必须配置在每个锚点内")
        anchors = _parse_guide_anchor_locations(raw["anchors"])
    else:
        anchors = _expand_legacy_guide_anchor_stations(
            raw.get("stations", []),
            stations,
            u_side_angle,
            back_u_side_angle,
            require_station_angles=mode
            in {
                GuideAnchorMode.ADJACENT_TWO_IMPLANT_CONTINUOUS_PATHS,
                GuideAnchorMode.ADJACENT_TWO_IMPLANT_TERMINAL_DISTAL_NODE_PATHS,
            },
        )
    _validate_guide_anchor_locations(mode, anchors)
    return GuideAnchorParameters(
        mode=mode,
        anchors=anchors,
        stations=stations,
        u_side_ray_angle_degrees=u_side_angle,
        back_u_side_ray_angle_degrees=back_u_side_angle,
        terminal_distal_common_node=terminal_distal_common_node,
    )


def _parse_guide_anchor_locations(
    raw_anchors: object,
) -> tuple[GuideAnchorLocation, ...]:
    """解析逐锚点牙位轨迹、侧别与角度参数。"""

    if not isinstance(raw_anchors, list):
        raise ConfigurationError("guide_anchors.anchors 必须为数组")
    anchors = []
    for index, raw_anchor in enumerate(raw_anchors):
        section = f"guide_anchors.anchors[{index}]"
        anchor = _mapping(raw_anchor, section)
        _reject_unknown(
            anchor,
            {"id", "endpoint", "side", "station", "ray_angle_degrees"},
            section,
        )
        anchor_id = str(_required(anchor, "id")).strip()
        endpoint_id = str(_required(anchor, "endpoint")).strip()
        if not CASE_ID_PATTERN.fullmatch(anchor_id):
            raise ConfigurationError(f"{section}.id 必须为小写字母数字标识")
        if not CASE_ID_PATTERN.fullmatch(endpoint_id):
            raise ConfigurationError(f"{section}.endpoint 必须为小写字母数字标识")
        try:
            side = GuideAnchorSide(str(_required(anchor, "side")))
        except ValueError as error:
            raise ConfigurationError(f"{section}.side 必须为 u_side 或 back_u_side") from error
        tooth_station = _parse_tooth_anchor_stations(
            [_mapping(_required(anchor, "station"), f"{section}.station")],
            f"{section}.station",
        )[0]
        anchors.append(
            GuideAnchorLocation(
                anchor_id,
                endpoint_id,
                side,
                tooth_station,
                _ray_angle_degrees(
                    _required(anchor, "ray_angle_degrees"),
                    f"{section}.ray_angle_degrees",
                ),
            )
        )
    identifiers = tuple(anchor.anchor_id for anchor in anchors)
    if len(set(identifiers)) != len(identifiers):
        raise ConfigurationError("guide_anchors.anchors.id 不得重复")
    return tuple(anchors)


def _expand_legacy_guide_anchor_stations(
    raw_stations: object,
    stations: tuple[ToothAnchorStation, ...],
    default_u_angle: float,
    default_back_u_angle: float,
    *,
    require_station_angles: bool,
) -> tuple[GuideAnchorLocation, ...]:
    """把旧版成对站位等价展开为逐锚点配置。"""

    if not isinstance(raw_stations, list):
        raise ConfigurationError("guide_anchors.stations 必须为数组")
    anchors = []
    endpoint_ids = []
    for index, (raw_station, station) in enumerate(zip(raw_stations, stations, strict=True)):
        mapping = _mapping(raw_station, f"guide_anchors.stations[{index}]")
        endpoint_id = str(mapping.get("id", f"station_{index + 1}")).strip()
        if not CASE_ID_PATTERN.fullmatch(endpoint_id):
            raise ConfigurationError("guide_anchors.stations.id 必须为小写字母数字标识")
        endpoint_ids.append(endpoint_id)
        if require_station_angles and (
            station.u_side_ray_angle_degrees is None
            or station.back_u_side_ray_angle_degrees is None
        ):
            raise ConfigurationError("当前连续路径模式的每个站位必须配置 U/背 U 角度")
        u_angle = station.u_side_ray_angle_degrees or default_u_angle
        back_u_angle = station.back_u_side_ray_angle_degrees or default_back_u_angle
        anchors.extend(
            (
                GuideAnchorLocation(
                    f"{endpoint_id}_u",
                    endpoint_id,
                    GuideAnchorSide.U_SIDE,
                    station,
                    u_angle,
                ),
                GuideAnchorLocation(
                    f"{endpoint_id}_back_u",
                    endpoint_id,
                    GuideAnchorSide.BACK_U_SIDE,
                    station,
                    back_u_angle,
                ),
            )
        )
    if len(set(endpoint_ids)) != len(endpoint_ids):
        raise ConfigurationError("guide_anchors.stations.id 不得重复")
    return tuple(anchors)


def _validate_guide_anchor_locations(
    mode: GuideAnchorMode,
    anchors: tuple[GuideAnchorLocation, ...],
) -> None:
    """校验各拓扑所需端部数量及每端 U/背 U 锚点完整性。"""

    expected_endpoint_count = {
        GuideAnchorMode.NEAREST: 0,
        GuideAnchorMode.TOOTH_SECTION_TRAJECTORY: 2,
        GuideAnchorMode.ADJACENT_TWO_IMPLANT_CONTINUOUS_PATHS: 2,
        GuideAnchorMode.TERMINAL_DISTAL_COMMON_NODE: 1,
        GuideAnchorMode.ADJACENT_TWO_IMPLANT_TERMINAL_DISTAL_NODE_PATHS: 1,
    }[mode]
    endpoint_ids = tuple(dict.fromkeys(anchor.endpoint_id for anchor in anchors))
    if len(endpoint_ids) != expected_endpoint_count:
        raise ConfigurationError(
            f"guide_anchors.{mode.value} 必须配置 {expected_endpoint_count} 个端部的独立锚点"
        )
    for endpoint_id in endpoint_ids:
        endpoint_anchors = tuple(anchor for anchor in anchors if anchor.endpoint_id == endpoint_id)
        sides = tuple(anchor.side for anchor in endpoint_anchors)
        if len(endpoint_anchors) != 2 or set(sides) != set(GuideAnchorSide):
            raise ConfigurationError(
                f"guide_anchors 端部 {endpoint_id} 必须各配置一个 U 侧和背 U 侧锚点"
            )


def _ray_angle_degrees(value: object, name: str) -> float:
    """校验从正参考轴起算的射线旋转角。"""

    angle = _number(value, name, positive=True)
    if angle > 180.0:
        raise ConfigurationError(f"{name} 必须小于或等于 180")
    return angle


def _parse_tooth_anchor_stations(
    raw_stations: object,
    section: str,
    *,
    allow_ray_angle: bool = False,
) -> tuple[ToothAnchorStation, ...]:
    """解析由单牙中心或双牙中点定义的牙位站位数组。"""

    if not isinstance(raw_stations, list):
        raise ConfigurationError(f"{section} 必须为数组")
    stations = []
    for index, raw_station in enumerate(raw_stations):
        station_name = f"{section}[{index}]"
        station = _mapping(raw_station, station_name)
        _reject_unknown(
            station,
            {
                "id",
                "type",
                "fdi",
                "fdis",
                "u_side_ray_angle_degrees",
                "back_u_side_ray_angle_degrees",
            }
            | ({"ray_angle_degrees"} if allow_ray_angle else set()),
            station_name,
        )
        station_type = str(_required(station, "type"))
        if station_type == "tooth_center":
            fdis = (_fdi(_required(station, "fdi"), f"{station_name}.fdi"),)
            if station.get("fdis") is not None:
                raise ConfigurationError("tooth_center 站位不得同时配置 fdis")
        elif station_type == "tooth_pair_midpoint":
            raw_fdis = _required(station, "fdis")
            if not isinstance(raw_fdis, list) or len(raw_fdis) != 2:
                raise ConfigurationError("tooth_pair_midpoint.fdis 必须包含两个 FDI")
            fdis = tuple(
                _fdi(value, f"{station_name}.fdis[{fdi_index}]")
                for fdi_index, value in enumerate(raw_fdis)
            )
            if fdis[0] == fdis[1]:
                raise ConfigurationError("tooth_pair_midpoint 的两个 FDI 不得相同")
            if station.get("fdi") is not None:
                raise ConfigurationError("tooth_pair_midpoint 站位不得同时配置 fdi")
        else:
            raise ConfigurationError(
                f"{section} station.type 必须为 tooth_center 或 tooth_pair_midpoint"
            )
        ray_angle_degrees = None
        if "ray_angle_degrees" in station:
            ray_angle_degrees = _ray_angle_degrees(
                station["ray_angle_degrees"],
                f"{station_name}.ray_angle_degrees",
            )
        u_side_angle = (
            _ray_angle_degrees(
                station["u_side_ray_angle_degrees"],
                f"{station_name}.u_side_ray_angle_degrees",
            )
            if station.get("u_side_ray_angle_degrees") is not None
            else None
        )
        back_u_side_angle = (
            _ray_angle_degrees(
                station["back_u_side_ray_angle_degrees"],
                f"{station_name}.back_u_side_ray_angle_degrees",
            )
            if station.get("back_u_side_ray_angle_degrees") is not None
            else None
        )
        stations.append(
            ToothAnchorStation(
                fdis,
                ray_angle_degrees,
                u_side_angle,
                back_u_side_angle,
            )
        )
    return tuple(stations)


def _parse_guide_component_bridge(
    raw_value: object,
) -> GuideComponentBridgeParameters:
    """解析断裂导板两分量之间的同侧双梁预连接。"""

    if raw_value is None:
        return GuideComponentBridgeParameters()
    raw = _mapping(raw_value, "guide_component_bridge")
    _reject_unknown(
        raw,
        {
            "enabled",
            "mode",
            "required_guide_component_count",
            "stations",
            "connection_rule",
            "require_different_guide_components",
            "diameter_mm",
            "dental_clearance_mm",
            "endpoint_reinforcement",
        },
        "guide_component_bridge",
    )
    enabled = _boolean(raw.get("enabled", False), "guide_component_bridge.enabled")
    mode_value = str(
        raw.get(
            "mode",
            (
                GuideComponentBridgeMode.SAME_SIDE_DUAL_BEAM.value
                if enabled
                else GuideComponentBridgeMode.DISABLED.value
            ),
        )
    )
    try:
        mode = GuideComponentBridgeMode(mode_value)
    except ValueError as error:
        raise ConfigurationError(
            "guide_component_bridge.mode 必须为 disabled 或 same_side_dual_beam"
        ) from error
    if enabled != (mode is not GuideComponentBridgeMode.DISABLED):
        raise ConfigurationError("guide_component_bridge.enabled 必须与 mode 是否为 disabled 一致")
    required_count = _positive_integer(
        raw.get("required_guide_component_count", 2),
        "guide_component_bridge.required_guide_component_count",
    )
    if enabled and required_count != 2:
        raise ConfigurationError("same_side_dual_beam 当前要求恰好两个导板分量")
    if str(raw.get("connection_rule", "same_side")) != "same_side":
        raise ConfigurationError("guide_component_bridge.connection_rule 必须为 same_side")
    require_different = _boolean(
        raw.get("require_different_guide_components", True),
        "guide_component_bridge.require_different_guide_components",
    )
    raw_stations = raw.get("stations", [])
    if not isinstance(raw_stations, list):
        raise ConfigurationError("guide_component_bridge.stations 必须为数组")
    stations = []
    for index, raw_station_value in enumerate(raw_stations):
        section = f"guide_component_bridge.stations[{index}]"
        raw_station = _mapping(raw_station_value, section)
        _reject_unknown(
            raw_station,
            {
                "id",
                "type",
                "fdi",
                "fdis",
                "u_side_ray_angle_degrees",
                "back_u_side_ray_angle_degrees",
            },
            section,
        )
        station_id = str(_required(raw_station, "id"))
        if not CASE_ID_PATTERN.fullmatch(station_id):
            raise ConfigurationError(f"{section}.id 含有无效字符")
        tooth_raw = {
            key: value for key, value in raw_station.items() if key in {"type", "fdi", "fdis"}
        }
        tooth_station = _parse_tooth_anchor_stations([tooth_raw], section)[0]
        stations.append(
            GuideComponentBridgeStation(
                station_id,
                tooth_station,
                _ray_angle_degrees(
                    _required(raw_station, "u_side_ray_angle_degrees"),
                    f"{section}.u_side_ray_angle_degrees",
                ),
                _ray_angle_degrees(
                    _required(raw_station, "back_u_side_ray_angle_degrees"),
                    f"{section}.back_u_side_ray_angle_degrees",
                ),
            )
        )
    if enabled and len(stations) != 2:
        raise ConfigurationError("same_side_dual_beam 必须配置两个牙位站位")
    if not enabled and stations:
        raise ConfigurationError("disabled 导板预连接不得配置牙位站位")
    if len({station.station_id for station in stations}) != len(stations):
        raise ConfigurationError("guide_component_bridge station.id 不得重复")
    endpoint_raw = _mapping(
        raw.get("endpoint_reinforcement", {"enabled": False}),
        "guide_component_bridge.endpoint_reinforcement",
    )
    _reject_unknown(
        endpoint_raw,
        {"enabled", "method"},
        "guide_component_bridge.endpoint_reinforcement",
    )
    endpoint_enabled = _boolean(
        endpoint_raw.get("enabled", False),
        "guide_component_bridge.endpoint_reinforcement.enabled",
    )
    method = str(endpoint_raw.get("method", "bulb_and_conformal_foot"))
    if endpoint_enabled and method != "bulb_and_conformal_foot":
        raise ConfigurationError(
            "guide_component_bridge.endpoint_reinforcement.method 必须为 bulb_and_conformal_foot"
        )
    return GuideComponentBridgeParameters(
        mode=mode,
        required_guide_component_count=required_count,
        stations=tuple(stations),
        require_different_guide_components=require_different,
        diameter_mm=_number(
            raw.get("diameter_mm", DEFAULT_CONNECTOR_DIAMETER_MM),
            "guide_component_bridge.diameter_mm",
            positive=True,
        ),
        dental_clearance_mm=_number(
            raw.get("dental_clearance_mm", 0.20),
            "guide_component_bridge.dental_clearance_mm",
        ),
        endpoint_reinforcement=(PressBeamGuideEndpointParameters() if endpoint_enabled else None),
    )


def _parse_guide_terminal_u_extension(
    raw_value: object,
) -> GuideTerminalUExtensionParameters:
    """解析从导板末端双锚点绕末端牙回转的 U 型延伸梁。"""

    if raw_value is None:
        return GuideTerminalUExtensionParameters()
    raw = _mapping(raw_value, "guide_terminal_u_extension")
    _reject_unknown(
        raw,
        {
            "enabled",
            "mode",
            "anchor_station",
            "u_side_ray_angle_degrees",
            "back_u_side_ray_angle_degrees",
            "terminal_fdi",
            "reference_neighbor_fdi",
            "diameter_mm",
            "dental_clearance_mm",
            "safety_margin_mm",
            "turnaround_depth_mm",
            "endpoint_reinforcement",
        },
        "guide_terminal_u_extension",
    )
    enabled = _boolean(raw.get("enabled", False), "guide_terminal_u_extension.enabled")
    mode_value = str(
        raw.get(
            "mode",
            (
                GuideTerminalUExtensionMode.TOOTH_WRAPPING_U_BEAM.value
                if enabled
                else GuideTerminalUExtensionMode.DISABLED.value
            ),
        )
    )
    try:
        mode = GuideTerminalUExtensionMode(mode_value)
    except ValueError as error:
        raise ConfigurationError(
            "guide_terminal_u_extension.mode 必须为 disabled 或 tooth_wrapping_u_beam"
        ) from error
    if enabled != (mode is not GuideTerminalUExtensionMode.DISABLED):
        raise ConfigurationError(
            "guide_terminal_u_extension.enabled 必须与 mode 是否为 disabled 一致"
        )

    anchor_station = None
    raw_anchor = raw.get("anchor_station")
    if raw_anchor is not None:
        anchor_station = _parse_tooth_anchor_stations(
            [_mapping(raw_anchor, "guide_terminal_u_extension.anchor_station")],
            "guide_terminal_u_extension.anchor_station",
        )[0]
    if enabled and anchor_station is None:
        raise ConfigurationError("tooth_wrapping_u_beam 必须配置 anchor_station")
    if not enabled and anchor_station is not None:
        raise ConfigurationError("disabled 末端 U 型延伸梁不得配置 anchor_station")

    terminal_fdi = (
        _fdi(
            _required(raw, "terminal_fdi"),
            "guide_terminal_u_extension.terminal_fdi",
        )
        if enabled
        else None
    )
    neighbor_fdi = (
        _fdi(
            _required(raw, "reference_neighbor_fdi"),
            "guide_terminal_u_extension.reference_neighbor_fdi",
        )
        if enabled
        else None
    )
    if enabled and terminal_fdi == neighbor_fdi:
        raise ConfigurationError("末端牙与参考邻牙不得相同")

    diameter_mm = _number(
        raw.get("diameter_mm", DEFAULT_CONNECTOR_DIAMETER_MM),
        "guide_terminal_u_extension.diameter_mm",
        positive=True,
    )
    turnaround_depth_mm = _number(
        raw.get("turnaround_depth_mm", 3.0),
        "guide_terminal_u_extension.turnaround_depth_mm",
        positive=True,
    )
    if enabled and turnaround_depth_mm < diameter_mm / 2.0:
        raise ConfigurationError("guide_terminal_u_extension.turnaround_depth_mm 不得小于梁半径")

    endpoint_raw = _mapping(
        raw.get("endpoint_reinforcement", {"enabled": False}),
        "guide_terminal_u_extension.endpoint_reinforcement",
    )
    _reject_unknown(
        endpoint_raw,
        {"enabled", "method"},
        "guide_terminal_u_extension.endpoint_reinforcement",
    )
    endpoint_enabled = _boolean(
        endpoint_raw.get("enabled", False),
        "guide_terminal_u_extension.endpoint_reinforcement.enabled",
    )
    method = str(endpoint_raw.get("method", "bulb_and_conformal_foot"))
    if endpoint_enabled and method != "bulb_and_conformal_foot":
        raise ConfigurationError(
            "guide_terminal_u_extension.endpoint_reinforcement.method "
            "必须为 bulb_and_conformal_foot"
        )
    return GuideTerminalUExtensionParameters(
        mode=mode,
        anchor_station=anchor_station,
        u_side_ray_angle_degrees=_ray_angle_degrees(
            raw.get(
                "u_side_ray_angle_degrees",
                DEFAULT_GUIDE_ANCHOR_U_SIDE_RAY_ANGLE_DEGREES,
            ),
            "guide_terminal_u_extension.u_side_ray_angle_degrees",
        ),
        back_u_side_ray_angle_degrees=_ray_angle_degrees(
            raw.get(
                "back_u_side_ray_angle_degrees",
                DEFAULT_GUIDE_ANCHOR_BACK_U_SIDE_RAY_ANGLE_DEGREES,
            ),
            "guide_terminal_u_extension.back_u_side_ray_angle_degrees",
        ),
        terminal_fdi=terminal_fdi,
        reference_neighbor_fdi=neighbor_fdi,
        diameter_mm=diameter_mm,
        dental_clearance_mm=_number(
            raw.get("dental_clearance_mm", 0.20),
            "guide_terminal_u_extension.dental_clearance_mm",
        ),
        safety_margin_mm=_number(
            raw.get("safety_margin_mm", 0.30),
            "guide_terminal_u_extension.safety_margin_mm",
        ),
        turnaround_depth_mm=turnaround_depth_mm,
        endpoint_reinforcement=(PressBeamGuideEndpointParameters() if endpoint_enabled else None),
    )


def _parse_press_beam(raw_value: object) -> PressBeamParameters:
    """解析可选的牙弓内侧导管高端 Y 型按压梁配置。"""

    if raw_value is None:
        return PressBeamParameters()
    raw = _mapping(raw_value, "press_beam")
    _reject_unknown(
        raw,
        {
            "mode",
            "stations",
            "extension_anchor",
            "diameter_mm",
            "guide_overlap_mm",
            "junction_sleeve_distance_mm",
            "junction_axial_lift_mm",
            "sleeve_anchor_selection",
            "minimum_junction_angle_degrees",
            "guide_endpoint",
        },
        "press_beam",
    )
    try:
        mode = PressBeamMode(str(raw.get("mode", PressBeamMode.DISABLED.value)))
    except ValueError as error:
        raise ConfigurationError(
            "press_beam.mode 必须为 disabled、inner_sleeve_upper_y "
            "、three_tooth_anchors_y 或 terminal_u_extension_anchor_y"
        ) from error
    stations = _parse_tooth_anchor_stations(
        raw.get("stations", []),
        "press_beam.stations",
        allow_ray_angle=True,
    )
    diameter_mm = _number(
        raw.get("diameter_mm", DEFAULT_PRESS_BEAM_DIAMETER_MM),
        "press_beam.diameter_mm",
        positive=True,
    )
    overlap_mm = _number(
        raw.get("guide_overlap_mm", 0.30),
        "press_beam.guide_overlap_mm",
    )
    junction_distance_mm = _number(
        raw.get("junction_sleeve_distance_mm", 6.0),
        "press_beam.junction_sleeve_distance_mm",
        positive=True,
    )
    junction_axial_lift_mm = _number(
        raw.get("junction_axial_lift_mm", 2.0),
        "press_beam.junction_axial_lift_mm",
        positive=True,
    )
    guide_endpoint = _parse_guide_endpoint(
        raw.get("guide_endpoint", {}),
        "press_beam.guide_endpoint",
    )
    raw_sleeve_selection = raw.get("sleeve_anchor_selection")
    sleeve_anchor_selection = None
    if raw_sleeve_selection is not None:
        selection = _mapping(
            raw_sleeve_selection,
            "press_beam.sleeve_anchor_selection",
        )
        _reject_unknown(
            selection,
            {"candidate_scope", "distance_score", "tie_breaker"},
            "press_beam.sleeve_anchor_selection",
        )
        if str(_required(selection, "candidate_scope")) != ("inner_sleeve_upper_per_implant_site"):
            raise ConfigurationError("不支持的多种植位 Y 梁导管候选范围")
        if str(_required(selection, "distance_score")) != "maximin_to_two_guide_anchors":
            raise ConfigurationError("多种植位 Y 梁距离评分必须为 maximin_to_two_guide_anchors")
        if str(_required(selection, "tie_breaker")) != "larger_sum_distance":
            raise ConfigurationError("多种植位 Y 梁平局规则必须为 larger_sum_distance")
        sleeve_anchor_selection = PressBeamSleeveAnchorSelectionParameters(
            candidate_scope=str(_required(selection, "candidate_scope")),
            distance_score=str(_required(selection, "distance_score")),
            tie_breaker=str(_required(selection, "tie_breaker")),
        )
    minimum_junction_angle = _number(
        raw.get("minimum_junction_angle_degrees", 25.0),
        "press_beam.minimum_junction_angle_degrees",
        positive=True,
    )
    if minimum_junction_angle > 180.0:
        raise ConfigurationError("press_beam.minimum_junction_angle_degrees 必须不大于 180")
    extension_anchor = None
    raw_extension_anchor = raw.get("extension_anchor")
    if raw_extension_anchor is not None:
        anchor = _mapping(raw_extension_anchor, "press_beam.extension_anchor")
        _reject_unknown(
            anchor,
            {
                "segment",
                "selection",
                "start_margin_mm",
                "end_margin_mm",
                "overlap_mm",
            },
            "press_beam.extension_anchor",
        )
        segment = str(_required(anchor, "segment"))
        if segment not in {"u_side", "back_u_side", "turnaround", "full"}:
            raise ConfigurationError(
                "press_beam.extension_anchor.segment 必须为 u_side、back_u_side、turnaround 或 full"
            )
        selection = str(anchor.get("selection", "farthest_from_guide_anchors"))
        if selection != "farthest_from_guide_anchors":
            raise ConfigurationError(
                "press_beam.extension_anchor.selection 必须为 farthest_from_guide_anchors"
            )
        anchor_overlap_mm = _number(
            anchor.get("overlap_mm", 0.30),
            "press_beam.extension_anchor.overlap_mm",
        )
        if anchor_overlap_mm >= diameter_mm / 2.0:
            raise ConfigurationError("press_beam.extension_anchor.overlap_mm 必须小于按压梁半径")
        extension_anchor = PressBeamExtensionAnchorParameters(
            segment=segment,
            selection=selection,
            start_margin_mm=_number(
                anchor.get("start_margin_mm", DEFAULT_CONNECTOR_DIAMETER_MM),
                "press_beam.extension_anchor.start_margin_mm",
            ),
            end_margin_mm=_number(
                anchor.get("end_margin_mm", 0.0),
                "press_beam.extension_anchor.end_margin_mm",
            ),
            overlap_mm=anchor_overlap_mm,
        )
    if mode is PressBeamMode.DISABLED and stations:
        raise ConfigurationError("disabled 按压梁模式不得配置牙位站位")
    if mode is PressBeamMode.INNER_SLEEVE_UPPER_Y and len(stations) != 2:
        raise ConfigurationError("内侧导管高端 Y 型按压梁必须配置两个牙位站位")
    if mode is PressBeamMode.INNER_SLEEVE_UPPER_Y and sleeve_anchor_selection is None:
        sleeve_anchor_selection = PressBeamSleeveAnchorSelectionParameters()
    if mode is not PressBeamMode.INNER_SLEEVE_UPPER_Y and sleeve_anchor_selection is not None:
        raise ConfigurationError("只有 inner_sleeve_upper_y 可以配置 sleeve_anchor_selection")
    if mode is PressBeamMode.THREE_TOOTH_ANCHORS_Y and len(stations) != 3:
        raise ConfigurationError("全牙位锚点 Y 型按压梁必须配置三个牙位站位")
    if mode is PressBeamMode.TERMINAL_U_EXTENSION_ANCHOR_Y and len(stations) != 2:
        raise ConfigurationError("末端 U 型延伸梁锚点 Y 型按压梁必须配置两个牙位站位")
    if mode is PressBeamMode.TERMINAL_U_EXTENSION_ANCHOR_Y and extension_anchor is None:
        raise ConfigurationError("末端 U 型延伸梁锚点 Y 型按压梁必须配置 extension_anchor")
    if mode is not PressBeamMode.TERMINAL_U_EXTENSION_ANCHOR_Y and extension_anchor is not None:
        raise ConfigurationError("只有 terminal_u_extension_anchor_y 可以配置 extension_anchor")
    if mode is not PressBeamMode.DISABLED and any(
        station.ray_angle_degrees is None for station in stations
    ):
        raise ConfigurationError("Y 型按压梁的每个导板锚点必须显式配置 ray_angle_degrees")
    if overlap_mm >= diameter_mm / 2.0:
        raise ConfigurationError("press_beam.guide_overlap_mm 必须小于按压梁半径")
    return PressBeamParameters(
        mode=mode,
        stations=stations,
        extension_anchor=extension_anchor,
        diameter_mm=diameter_mm,
        guide_overlap_mm=overlap_mm,
        junction_sleeve_distance_mm=junction_distance_mm,
        junction_axial_lift_mm=junction_axial_lift_mm,
        minimum_junction_angle_degrees=minimum_junction_angle,
        sleeve_anchor_selection=sleeve_anchor_selection,
        guide_endpoint=guide_endpoint,
    )


def _parse_render(raw: dict[str, object]) -> RenderParameters:
    """解析并校验渲染图像的像素尺寸。"""

    fields = {"width_px", "height_px"}
    _reject_unknown(raw, fields, "render")
    return RenderParameters(
        width_px=_positive_integer(_required(raw, "width_px"), "render.width_px"),
        height_px=_positive_integer(_required(raw, "height_px"), "render.height_px"),
    )

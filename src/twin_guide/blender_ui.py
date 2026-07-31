"""TwinGuide 在 Blender 三维视图中的病例微调面板。"""

import argparse
import sys
from dataclasses import replace
from pathlib import Path

import bpy

from twin_guide.config import CaseConfig, require_production_review


def _station_text(config: CaseConfig) -> str:
    """把当前按压点牙位编码为面板可编辑文本。"""

    return ";".join(",".join(str(fdi) for fdi in station.fdis) for station in config.press_beam.stations)


def _parse_station_text(value: str, expected_count: int) -> tuple[tuple[int, ...], ...]:
    """解析分号分组的按压点牙位，并保持原站位数量。"""

    groups = tuple(group.strip() for group in value.split(";") if group.strip())
    if len(groups) != expected_count:
        raise ValueError(f"按压点必须保留 {expected_count} 组牙位")
    parsed = []
    for group in groups:
        fdis = tuple(int(item.strip()) for item in group.split(",") if item.strip())
        if not fdis:
            raise ValueError("按压点牙位组不得为空")
        parsed.append(fdis)
    return tuple(parsed)


def _adjusted_config(settings: "TwinGuideAdjustmentProperties") -> CaseConfig:
    """将面板微调值合入一份新的不可变病例配置。"""

    config = CaseConfig.from_yaml(Path(settings.config_path))
    windows = replace(
        config.windows,
        operation_front_axial_margin_mm=settings.operation_front_margin_mm,
        operation_rear_axial_margin_mm=settings.operation_rear_margin_mm,
    )
    blocks = replace(
        config.geometry.connection_blocks,
        lower_main=settings.include_lower_main,
        upper_main=settings.include_upper_main,
        press_beam=settings.include_press_beam,
    )
    geometry = replace(
        config.geometry,
        sleeve_stop_clearance_mm=settings.sleeve_stop_clearance_mm,
        sleeve_stop_front_avoidance_mm=(
            settings.sleeve_stop_front_avoidance_mm
        ),
        connection_blocks=blocks,
    )
    sleeve = replace(
        config.sleeve,
        height_mm=settings.sleeve_height_mm,
        platform_height_mm=settings.platform_height_mm,
        closed_bore_height_mm=settings.closed_bore_height_mm,
    )
    if not 0.0 < sleeve.closed_bore_height_mm < sleeve.platform_height_mm < sleeve.height_mm:
        raise ValueError("高度必须满足：底部高度 < 平台高度 < 导管总高度")
    stations = config.press_beam.stations
    if stations:
        groups = _parse_station_text(settings.press_station_fdis, len(stations))
        stations = tuple(
            replace(station, fdis=fdis)
            for station, fdis in zip(stations, groups, strict=True)
        )
    press_beam = replace(config.press_beam, stations=stations)
    return replace(
        config,
        windows=windows,
        geometry=geometry,
        sleeve=sleeve,
        press_beam=press_beam,
    )


class TwinGuideAdjustmentProperties(bpy.types.PropertyGroup):
    """面板中的非持久化微调值。"""

    config_path: bpy.props.StringProperty(name="病例配置", subtype="FILE_PATH")
    operation_front_margin_mm: bpy.props.FloatProperty(name="术区切除量", min=0.0, precision=3)
    operation_rear_margin_mm: bpy.props.FloatProperty(name="后部切除量", min=0.0, precision=3)
    sleeve_stop_clearance_mm: bpy.props.FloatProperty(name="止停台净距", min=0.0, precision=3)
    sleeve_stop_front_avoidance_mm: bpy.props.FloatProperty(
        name="止停台正面避让",
        min=0.0,
        precision=3,
    )
    include_lower_main: bpy.props.BoolProperty(name="保留低位连接", default=True)
    include_upper_main: bpy.props.BoolProperty(name="保留高位连接", default=True)
    include_press_beam: bpy.props.BoolProperty(name="保留按压梁", default=True)
    press_station_fdis: bpy.props.StringProperty(name="按压点牙位")
    sleeve_height_mm: bpy.props.FloatProperty(name="导管总高度", min=0.001, precision=3)
    platform_height_mm: bpy.props.FloatProperty(name="平台高度", min=0.001, precision=3)
    closed_bore_height_mm: bpy.props.FloatProperty(name="底部高度", min=0.001, precision=3)
    implant_coordinate_source: bpy.props.StringProperty(name="种植体坐标")
    extension_definition: bpy.props.StringProperty(name="延长量定义")
    extension_mm: bpy.props.FloatProperty(name="延长量", default=-1.0, precision=3)
    mouth_opening_mm: bpy.props.FloatProperty(name="张口度", default=-1.0, precision=3)
    adapter_length_mm: bpy.props.FloatProperty(name="转口长度", default=-1.0, precision=3)
    height_formula_id: bpy.props.StringProperty(name="高度公式")
    status: bpy.props.StringProperty(name="状态")


class TwinGuideLoadCaseOperator(bpy.types.Operator):
    """读取病例配置并填充面板。"""

    bl_idname = "twinguide.load_case"
    bl_label = "读取病例"

    def execute(self, context: bpy.types.Context) -> set[str]:
        """读取配置并把可调字段同步到当前场景面板。"""

        settings = context.scene.twin_guide_adjustments
        try:
            config = CaseConfig.from_yaml(Path(settings.config_path))
            settings.operation_front_margin_mm = config.windows.operation_front_axial_margin_mm
            settings.operation_rear_margin_mm = config.windows.operation_rear_axial_margin_mm
            settings.sleeve_stop_clearance_mm = config.geometry.sleeve_stop_clearance_mm
            settings.sleeve_stop_front_avoidance_mm = (
                config.geometry.sleeve_stop_front_avoidance_mm
            )
            settings.include_lower_main = config.geometry.connection_blocks.lower_main
            settings.include_upper_main = config.geometry.connection_blocks.upper_main
            settings.include_press_beam = config.geometry.connection_blocks.press_beam
            settings.press_station_fdis = _station_text(config)
            settings.sleeve_height_mm = config.sleeve.height_mm
            settings.platform_height_mm = config.sleeve.platform_height_mm
            settings.closed_bore_height_mm = config.sleeve.closed_bore_height_mm
            clinical = config.clinical_planning
            settings.implant_coordinate_source = (
                "未提供"
                if clinical.implant_coordinates_path is None
                else f"{clinical.implant_coordinates_format}: {clinical.implant_coordinates_path}"
            )
            settings.extension_definition = clinical.extension_definition or "未确认"
            settings.extension_mm = -1.0 if clinical.extension_mm is None else clinical.extension_mm
            settings.mouth_opening_mm = (
                -1.0 if clinical.mouth_opening_mm is None else clinical.mouth_opening_mm
            )
            settings.adapter_length_mm = (
                -1.0 if clinical.adapter_length_mm is None else clinical.adapter_length_mm
            )
            settings.height_formula_id = clinical.height_formula_id or "未确认"
            settings.status = f"已读取：{config.case_id}"
        except Exception as error:  # Blender 操作器必须把配置错误显示在面板中。
            settings.status = str(error)
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class TwinGuideGenerateOperator(bpy.types.Operator):
    """使用面板微调值调用现有七阶段生成流程。"""

    bl_idname = "twinguide.generate"
    bl_label = "重新生成"

    def execute(self, context: bpy.types.Context) -> set[str]:
        """使用当前面板参数重新运行既有生成流程。"""

        settings = context.scene.twin_guide_adjustments
        try:
            config = _adjusted_config(settings)
            require_production_review(config)
            from twin_guide.guide_generation import generate_guide

            artifacts = generate_guide(config)
            settings.status = f"已生成：{artifacts.model_path}"
        except Exception as error:  # Blender 操作器必须把建模错误显示在面板中。
            settings.status = str(error)
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class TwinGuideAdjustmentsPanel(bpy.types.Panel):
    """三维视图侧栏中的 TwinGuide 微调入口。"""

    bl_label = "TwinGuide 微调"
    bl_idname = "TWINGUIDE_PT_adjustments"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "TwinGuide"

    def draw(self, context: bpy.types.Context) -> None:
        """绘制病例加载、微调和重新生成控件。"""

        layout = self.layout
        settings = context.scene.twin_guide_adjustments
        layout.prop(settings, "config_path")
        layout.operator("twinguide.load_case")
        layout.separator()
        layout.prop(settings, "operation_front_margin_mm")
        layout.prop(settings, "operation_rear_margin_mm")
        layout.prop(settings, "sleeve_stop_clearance_mm")
        layout.prop(settings, "sleeve_stop_front_avoidance_mm")
        layout.prop(settings, "include_lower_main")
        layout.prop(settings, "include_upper_main")
        layout.prop(settings, "include_press_beam")
        layout.prop(settings, "press_station_fdis")
        layout.prop(settings, "closed_bore_height_mm")
        layout.prop(settings, "platform_height_mm")
        layout.prop(settings, "sleeve_height_mm")
        pending = layout.column()
        pending.enabled = False
        pending.label(text="待临床定义确认（暂不参与生成）")
        pending.prop(settings, "implant_coordinate_source")
        pending.prop(settings, "extension_definition")
        pending.prop(settings, "extension_mm")
        pending.prop(settings, "mouth_opening_mm")
        pending.prop(settings, "adapter_length_mm")
        pending.prop(settings, "height_formula_id")
        layout.operator("twinguide.generate")
        if settings.status:
            layout.label(text=settings.status)


CLASSES = (
    TwinGuideAdjustmentProperties,
    TwinGuideLoadCaseOperator,
    TwinGuideGenerateOperator,
    TwinGuideAdjustmentsPanel,
)


def register() -> None:
    """注册 TwinGuide Blender 面板。"""

    for item in CLASSES:
        bpy.utils.register_class(item)
    bpy.types.Scene.twin_guide_adjustments = bpy.props.PointerProperty(
        type=TwinGuideAdjustmentProperties
    )


def unregister() -> None:
    """注销 TwinGuide Blender 面板。"""

    del bpy.types.Scene.twin_guide_adjustments
    for item in reversed(CLASSES):
        bpy.utils.unregister_class(item)


def launch_from_argv() -> None:
    """注册面板，并读取 Blender ``--`` 后提供的病例路径。"""

    arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(prog="twinguide ui")
    parser.add_argument("--config", required=True, type=Path)
    parsed = parser.parse_args(arguments)
    register()
    settings = bpy.context.scene.twin_guide_adjustments
    settings.config_path = str(parsed.config.resolve())
    bpy.ops.twinguide.load_case()

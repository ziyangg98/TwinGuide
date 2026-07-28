"""几何分析、选点、建模和检查共用的领域数据类型。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from twin_guide.config import CaseConfig
from twin_guide.geometry import Vec3
from twin_guide.sleeve_estimation.types import SleeveEstimate

if TYPE_CHECKING:
    import bpy


@dataclass(frozen=True, slots=True)
class SurfaceSample:
    """网格表面采样点的位置、外法向和原始多边形索引。"""

    position: Vec3
    normal: Vec3
    polygon_index: int


@dataclass(frozen=True, slots=True)
class GuideSleeve:
    """单个已识别导管的几何参数和 Blender 网格。"""

    guide_index: int
    guide_mesh: bpy.types.Object
    parameters: SleeveEstimate
    axial_min_mm: float
    axial_max_mm: float
    source_component_index: int | None = None
    axial_bore_clear_fraction: float | None = None

    @property
    def center(self) -> Vec3:
        """返回拟合固定孔轴线上的基准点。"""

        return self.parameters.axis_origin

    @property
    def axis(self) -> Vec3:
        """返回从导管顶部指向底部的内在轴向。"""

        return self.parameters.axis

    @property
    def outer_radius_mm(self) -> float:
        """返回导管径向包络半径。"""

        return self.parameters.outer_radius + self.parameters.platform_width

    @property
    def bore_radius_mm(self) -> float:
        """返回拟合内圆弧半径。"""

        return self.parameters.inner_radius

    @property
    def body_radius_mm(self) -> float:
        """返回拟合主体外圆弧半径。"""

        return self.parameters.outer_radius

    @property
    def flange_radius_mm(self) -> float:
        """返回平台的保守径向包络半径。"""

        return self.outer_radius_mm

    @property
    def flange_height_mm(self) -> float:
        """返回平台高度。"""

        return self.parameters.platform_height

    @property
    def length_mm(self) -> float:
        """返回导管的轴向高度。"""

        return self.axial_max_mm - self.axial_min_mm


@dataclass(frozen=True, slots=True)
class TemplateFrame:
    """由牙科导板几何确定的右手局部坐标系。"""

    origin: Vec3
    lateral: Vec3
    depth: Vec3
    normal: Vec3

    def coordinates(self, point: Vec3) -> tuple[float, float, float]:
        """将世界坐标中的点转换为牙科导板局部坐标。"""

        offset = point - self.origin
        return offset.dot(self.lateral), offset.dot(self.depth), offset.dot(self.normal)


@dataclass(frozen=True, slots=True)
class GenerationMeshes:
    """一个病例中已读取的 Blender 输入网格。"""

    template_mesh: bpy.types.Object
    guide_sleeve_assembly_meshes: tuple[bpy.types.Object, ...]
    patient_dentition_mesh: bpy.types.Object


@dataclass(frozen=True, slots=True)
class OperationFeature:
    """操作窗所暴露圆形结构的中心和外径。"""

    center: Vec3
    diameter_mm: float


@dataclass(frozen=True, slots=True)
class CaseAnalysis:
    """单次构建所需的病例几何分析和保留网格。"""

    config: CaseConfig
    input_meshes: GenerationMeshes
    guide_sleeves: tuple[GuideSleeve, ...]
    retained_accessory_meshes: tuple[bpy.types.Object, ...]
    operation_features: tuple[OperationFeature, ...]
    template_frame: TemplateFrame
    template_samples: tuple[SurfaceSample, ...]
    dentition_samples: tuple[SurfaceSample, ...]

    @property
    def guide_sleeve_pairs(self) -> tuple[tuple[GuideSleeve, GuideSleeve], ...]:
        """按种植位返回每个装配体所属的两根导管。"""

        if len(self.guide_sleeves) % 2:
            raise ValueError("导管数量必须为偶数")
        return tuple(
            (self.guide_sleeves[index], self.guide_sleeves[index + 1])
            for index in range(0, len(self.guide_sleeves), 2)
        )


@dataclass(frozen=True, slots=True)
class CylinderCutout:
    """用于切除一个导孔的有限圆柱体。"""

    name: str
    start: Vec3
    end: Vec3
    radius_mm: float


class WindowPurpose(StrEnum):
    """窗口的功能类型。"""

    OPERATION = "operation"
    OBSERVATION = "observation"


@dataclass(frozen=True, slots=True)
class WindowCutout:
    """由中心、局部方向和尺寸定义的圆角窗口切割体。"""

    name: str
    purpose: WindowPurpose
    center: Vec3
    normal: Vec3
    tangent: Vec3
    width_mm: float
    height_mm: float
    depth_mm: float
    corner_radius_mm: float


@dataclass(frozen=True, slots=True)
class ProfileWindowCutout:
    """第 3 阶段由本次牙位映射生成的观察窗组合切割体。"""

    name: str
    cutter_mesh_path: Path
    report_path: Path
    window_ids: tuple[str, ...]
    crest_points: tuple[Vec3, ...]
    window_crest_points: tuple[tuple[Vec3, ...], ...]


@dataclass(frozen=True, slots=True)
class CutoutPlan:
    """导孔和窗口的纯几何计划，不包含 Blender 切割对象。"""

    channels: tuple[CylinderCutout, CylinderCutout]
    windows: tuple[WindowCutout, ...]
    profile_windows: tuple[ProfileWindowCutout, ...] = ()


@dataclass(frozen=True, slots=True)
class BuildArtifacts:
    """牙科导板构建完成后写入的文件路径。"""

    model_path: Path
    image_paths: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """带有名称和数值指标的结构化检查结果。"""

    name: str
    passed: bool
    metrics: dict[str, int | float]

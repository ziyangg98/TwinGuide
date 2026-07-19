"""导套参数估计的无外部依赖数据契约。"""

from __future__ import annotations

from dataclasses import dataclass, field

from twin_guide.geometry import Vec3

Face = tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class TriangleMeshData:
    """在调用方笛卡尔坐标系中表示的三角表面网格。"""

    vertices: tuple[Vec3, ...]
    faces: tuple[Face, ...]

    def __post_init__(self) -> None:
        """校验顶点数量和三角面索引。"""

        if len(self.vertices) < 3 or not self.faces:
            raise ValueError("需要非空三角网格")
        vertex_count = len(self.vertices)
        if any(
            len(face) != 3
            or any(index < 0 or index >= vertex_count for index in face)
            for face in self.faces
        ):
            raise ValueError("每个网格面必须包含三个有效顶点索引")


@dataclass(frozen=True, slots=True)
class ParameterDiagnostic:
    """单个估计参数的质量证据。"""

    parameter: str
    valid: bool
    sample_count: int
    residual: float | None = None
    spread: float | None = None
    message: str = ""


@dataclass(frozen=True, slots=True)
class EstimationConfig:
    """纯 Python 参数估计器的数值和几何控制参数。"""

    slice_count: int = 9
    slice_fraction_low: float = 0.18
    slice_fraction_high: float = 0.82
    axis_iterations: int = 4
    section_tolerance: float = 1e-7
    normal_radial_threshold: float = 0.25
    minimum_arc_points: int = 6
    trim_sigma: float = 3.5
    endpoint_normal_cosine: float = 0.85

    def __post_init__(self) -> None:
        """校验切片数量、分数范围和迭代参数。"""

        if self.slice_count < 3:
            raise ValueError("slice_count 不得小于 3")
        if not 0.0 < self.slice_fraction_low < self.slice_fraction_high < 1.0:
            raise ValueError("切片分数必须严格位于 (0, 1) 内")
        if self.axis_iterations < 1 or self.minimum_arc_points < 3:
            raise ValueError("迭代次数或最小点数过小")


@dataclass(frozen=True, slots=True)
class SleeveEstimate:
    """描述并重建单个导套的核心几何参数。"""

    axis_origin: Vec3
    axis: Vec3
    platform_direction: Vec3
    height: float
    platform_height: float
    closed_bore_height: float
    platform_width: float
    inner_radius: float
    outer_radius: float
    inner_arc_angle: float
    outer_arc_angle: float
    diagnostics: tuple[ParameterDiagnostic, ...] = field(default_factory=tuple)

    @property
    def valid(self) -> bool:
        """返回所有参数诊断是否均通过。"""

        return all(item.valid for item in self.diagnostics)

@dataclass(frozen=True, slots=True)
class ReconstructionValidation:
    """输入网格与重建网格之间的双向表面误差。"""

    input_to_reconstruction_rms: float
    reconstruction_to_input_rms: float
    symmetric_rms: float
    median_distance: float
    percentile_95_distance: float
    hausdorff_approximation: float
    region_rms: tuple[tuple[str, float], ...]
    sample_count: int


# 集成层使用的公开类型名。
ParameterEstimate = ParameterDiagnostic
SleeveEstimationReport = SleeveEstimate

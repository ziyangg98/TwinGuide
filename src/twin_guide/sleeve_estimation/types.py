"""导套参数估计的无外部依赖数据契约。"""

from __future__ import annotations

from dataclasses import dataclass

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
            len(face) != 3 or any(index < 0 or index >= vertex_count for index in face)
            for face in self.faces
        ):
            raise ValueError("每个网格面必须包含三个有效顶点索引")


@dataclass(frozen=True, slots=True)
class SleeveAxis:
    """从输入导柱 STL 估计的有向轴线。"""

    axis_origin: Vec3
    axis: Vec3


@dataclass(frozen=True, slots=True)
class SleeveEstimate:
    """描述单个导套的轴线、C 口方向和八个标量尺寸参数。"""

    axis_origin: Vec3
    axis: Vec3
    c_opening_direction: Vec3
    height: float
    platform_height: float
    closed_bore_height: float
    platform_width: float
    inner_radius: float
    outer_radius: float
    inner_arc_angle: float
    outer_arc_angle: float
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

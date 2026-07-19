"""对重建三角实体执行无外部依赖的完整性检查。"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from .types import TriangleMeshData


@dataclass(frozen=True, slots=True)
class MeshIntegrityReport:
    """三角网格的连通性、流形性和方向检查结果。"""

    component_count: int
    boundary_edge_count: int
    non_manifold_edge_count: int
    degenerate_face_count: int
    duplicate_face_count: int
    signed_volume: float

    @property
    def valid(self) -> bool:
        """返回网格是否为单一连通的封闭定向实体。"""

        return (
            self.component_count == 1
            and self.boundary_edge_count == 0
            and self.non_manifold_edge_count == 0
            and self.degenerate_face_count == 0
            and self.duplicate_face_count == 0
            and self.signed_volume > 0.0
        )


def inspect_triangle_mesh(mesh: TriangleMeshData) -> MeshIntegrityReport:
    """检查网格的连通性、流形性、退化面、重复面和方向。"""

    coordinates = [coordinate for point in mesh.vertices for coordinate in point.as_tuple()]
    scale = max(max(coordinates) - min(coordinates), 1.0)
    area_tolerance = scale * scale * 1e-12
    edge_faces: dict[tuple[int, int], list[int]] = defaultdict(list)
    duplicate_keys: Counter[tuple[int, int, int]] = Counter()
    degenerate = 0
    signed_volume = 0.0
    for face_index, (first_index, second_index, third_index) in enumerate(mesh.faces):
        first = mesh.vertices[first_index]
        second = mesh.vertices[second_index]
        third = mesh.vertices[third_index]
        if (second - first).cross(third - first).length <= area_tolerance:
            degenerate += 1
        signed_volume += first.dot(second.cross(third)) / 6.0
        duplicate_keys[tuple(sorted((first_index, second_index, third_index)))] += 1
        for edge in (
            (first_index, second_index),
            (second_index, third_index),
            (third_index, first_index),
        ):
            edge_faces[tuple(sorted(edge))].append(face_index)

    adjacency = [set() for _ in mesh.faces]
    for incident_faces in edge_faces.values():
        for face_index in incident_faces:
            adjacency[face_index].update(
                other for other in incident_faces if other != face_index
            )
    unseen = set(range(len(mesh.faces)))
    component_count = 0
    while unseen:
        component_count += 1
        pending = [unseen.pop()]
        while pending:
            for neighbor in adjacency[pending.pop()]:
                if neighbor in unseen:
                    unseen.remove(neighbor)
                    pending.append(neighbor)

    return MeshIntegrityReport(
        component_count=component_count,
        boundary_edge_count=sum(len(faces) == 1 for faces in edge_faces.values()),
        non_manifold_edge_count=sum(len(faces) > 2 for faces in edge_faces.values()),
        degenerate_face_count=degenerate,
        duplicate_face_count=sum(count - 1 for count in duplicate_keys.values()),
        signed_volume=signed_volume,
    )

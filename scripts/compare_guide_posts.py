"""只比较模板生成导柱与现有参考导柱的关键几何量。"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import trimesh


def _unit(vector: np.ndarray) -> np.ndarray:
    length = float(np.linalg.norm(vector))
    if length <= 1e-12:
        raise ValueError("无法归一化零向量")
    return vector / length


def _unsigned_angle_degrees(first: np.ndarray, second: np.ndarray) -> float:
    cosine = abs(float(np.dot(_unit(first), _unit(second))))
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


def _principal_axis(mesh: trimesh.Trimesh) -> np.ndarray:
    covariance = np.cov(np.asarray(mesh.vertices, dtype=float).T)
    values, vectors = np.linalg.eigh(covariance)
    return _unit(vectors[:, int(np.argmax(values))])


def _reference_d_face_measurements(
    mesh: trimesh.Trimesh,
) -> tuple[np.ndarray, np.ndarray, float, float]:
    components = list(mesh.split(only_watertight=False))
    if len(components) != 2:
        raise ValueError(f"参考导柱应包含两个连通分量，实际为 {len(components)} 个")
    axes = [_principal_axis(component) for component in components]
    if float(np.dot(axes[0], axes[1])) < 0.0:
        axes[1] = -axes[1]
    axis = _unit(axes[0] + axes[1])
    rough_pair = np.asarray(components[1].centroid) - np.asarray(components[0].centroid)
    rough_pair -= axis * float(np.dot(rough_pair, axis))
    rough_pair = _unit(rough_pair)
    if float(np.asarray(components[0].centroid) @ rough_pair) > float(
        np.asarray(components[1].centroid) @ rough_pair
    ):
        components.reverse()
        rough_pair = -rough_pair

    face_planes: list[tuple[np.ndarray, np.ndarray]] = []
    for index, component in enumerate(components):
        candidates: list[tuple[float, np.ndarray, np.ndarray]] = []
        desired_sign = 1.0 if index == 0 else -1.0
        for facet_index, faces in enumerate(component.facets):
            normal = np.asarray(component.facets_normal[facet_index], dtype=float)
            if abs(float(np.dot(normal, axis))) >= 0.05:
                continue
            if desired_sign * float(np.dot(normal, rough_pair)) <= 0.95:
                continue
            area = float(component.facets_area[facet_index])
            center = np.average(
                np.asarray(component.triangles_center)[faces],
                axis=0,
                weights=np.asarray(component.area_faces)[faces],
            )
            candidates.append((area, normal, center))
        if not candidates:
            raise ValueError("参考导柱未识别到相向 D 平面")
        coordinates = [float(center @ rough_pair) for _, _, center in candidates]
        target = max(coordinates) if index == 0 else min(coordinates)
        selected = [
            item for item in candidates if abs(float(item[2] @ rough_pair) - target) <= 0.03
        ]
        weights = np.asarray([item[0] for item in selected])
        normal = _unit(
            np.average(np.asarray([item[1] for item in selected]), axis=0, weights=weights)
        )
        center = np.average(
            np.asarray([item[2] for item in selected]),
            axis=0,
            weights=weights,
        )
        face_planes.append((normal, center))

    pair_direction = face_planes[0][0] - face_planes[1][0]
    pair_direction -= axis * float(np.dot(pair_direction, axis))
    pair_direction = _unit(pair_direction)
    if float(np.dot(pair_direction, rough_pair)) < 0.0:
        pair_direction = -pair_direction
    d_face_spacing = float((face_planes[1][1] - face_planes[0][1]) @ pair_direction)
    coordinates = np.asarray(mesh.vertices, dtype=float) @ pair_direction
    outer_span = float(np.ptp(coordinates))
    return axis, pair_direction, d_face_spacing, outer_span


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("generated_report", type=Path)
    parser.add_argument("reference_guide_stl", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    generated = json.loads(arguments.generated_report.read_text(encoding="utf-8"))
    reference_mesh = trimesh.load_mesh(arguments.reference_guide_stl, process=True)
    reference_axis, reference_pair, reference_d_gap, reference_outer_span = (
        _reference_d_face_measurements(reference_mesh)
    )
    generated_axis = -np.asarray(generated["ring_outward_axis"], dtype=float)
    generated_pair = np.asarray(generated["guide_pair_direction"], dtype=float)
    measured_generated_gap = float(generated["measured_exported_d_face_spacing_mm"])
    result = {
        "generated_guide_stl": generated["output_stl"],
        "reference_guide_stl": str(arguments.reference_guide_stl.resolve()),
        "generated_d_face_spacing_mm": measured_generated_gap,
        "reference_d_face_spacing_mm": reference_d_gap,
        "d_face_spacing_difference_mm": measured_generated_gap - reference_d_gap,
        "reference_outer_span_mm": reference_outer_span,
        "axis_angle_difference_degrees": _unsigned_angle_degrees(
            generated_axis,
            reference_axis,
        ),
        "rotation_difference_degrees": _unsigned_angle_degrees(
            generated_pair,
            reference_pair,
        ),
        "reference_axis": reference_axis.tolist(),
        "reference_pair_direction": reference_pair.tolist(),
    }
    document = json.dumps(result, ensure_ascii=False, indent=2)
    print(document)
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(document + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

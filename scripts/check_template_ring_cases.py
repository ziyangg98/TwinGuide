"""批量检查病例参考模板的圆环中心和上平面识别。"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import trimesh
import yaml

from twin_guide.template_ring_estimation import (
    estimate_template_ring_top_plane,
    estimate_template_rings,
)


def _sleeve_midpoint_comparison(
    path: Path,
    output_root: Path | None,
    ring_geometry: tuple[tuple[tuple[float, float, float], tuple[float, float, float]], ...],
) -> dict[str, object]:
    """用已有 sleeve 阶段结果独立核验圆环是否落在导柱中线。"""

    case_yaml = path.parent.parent / "case.yaml"
    if output_root is None or not case_yaml.is_file():
        return {}
    case_id = yaml.safe_load(case_yaml.read_text(encoding="utf-8"))["case"]["id"]
    stage_path = output_root / case_id / "stage-01-sleeve-reconstruction.json"
    if not stage_path.is_file():
        return {}
    stage = json.loads(stage_path.read_text(encoding="utf-8"))
    sleeves = stage["result"]["sleeves"]
    sleeve_pairs = []
    for pair_index in range(0, len(sleeves) - 1, 2):
        sleeve_pairs.append(sleeves[pair_index : pair_index + 2])
    comparisons = []
    for ring_index, (ring_center, ring_axis) in enumerate(ring_geometry, 1):
        center = np.asarray(ring_center, dtype=float)
        normal = np.asarray(ring_axis, dtype=float)
        for pair_index, pair in enumerate(sleeve_pairs, 1):
            intersections = []
            for sleeve in pair:
                parameters = sleeve["parameters"]
                origin = np.asarray(
                    [parameters["axis_origin"][coordinate] for coordinate in "xyz"],
                    dtype=float,
                )
                axis = np.asarray(
                    [parameters["axis"][coordinate] for coordinate in "xyz"],
                    dtype=float,
                )
                distance = float(np.dot(center - origin, normal) / np.dot(axis, normal))
                intersections.append(origin + axis * distance)
            midpoint = sum(intersections) / 2.0
            comparisons.append(
                (
                    float(np.linalg.norm(midpoint - center)),
                    ring_index,
                    pair_index,
                    float(np.linalg.norm(intersections[1] - intersections[0])),
                )
            )
    assigned_rings = set()
    assigned_pairs = set()
    matches = []
    for error, ring_index, pair_index, spacing in sorted(comparisons):
        if ring_index in assigned_rings or pair_index in assigned_pairs:
            continue
        assigned_rings.add(ring_index)
        assigned_pairs.add(pair_index)
        matches.append(
            {
                "ring": ring_index,
                "sleeve_pair": pair_index,
                "midpoint_error_mm": round(error, 4),
                "spacing_mm": round(spacing, 4),
            }
        )
    return {
        "expected_ring_count_from_sleeves": len(sleeve_pairs),
        "sleeve_matches": sorted(matches, key=lambda match: match["ring"]),
    }


def _check_case(
    path: Path,
    cases_root: Path,
    output_root: Path | None,
) -> dict[str, object]:
    """检查一个病例，并返回可序列化的质量摘要。"""

    try:
        mesh = trimesh.load_mesh(path, process=True)
        rings = estimate_template_rings(mesh)
        top_planes = tuple(estimate_template_ring_top_plane(mesh, ring) for ring in rings)
    except Exception as error:
        return {
            "case": str(path.parent.parent.relative_to(cases_root)),
            "ok": False,
            "error": f"{type(error).__name__}: {error}",
        }
    result = {
        "case": str(path.parent.parent.relative_to(cases_root)),
        "ok": True,
        "ring_count": len(rings),
        "rings": [
            {
                "center": [round(value, 4) for value in ring.center.as_tuple()],
                "radius_mm": round(ring.radius_mm, 4),
                "circle_rms_mm": round(ring.circle_rms_mm, 5),
                "axis_rms_mm": round(ring.axis_rms_mm, 5),
                "supporting_slices": ring.supporting_slice_count,
                "top_plane_faces": top_plane.supporting_face_count,
                "top_plane_area_mm2": round(top_plane.supporting_area_mm2, 3),
                "top_center": [round(value, 4) for value in top_plane.center.as_tuple()],
                "outward_normal": [round(value, 6) for value in top_plane.normal.as_tuple()],
            }
            for ring, top_plane in zip(rings, top_planes, strict=True)
        ],
    }
    result.update(
        _sleeve_midpoint_comparison(
            path,
            output_root,
            tuple((ring.center.as_tuple(), ring.axis.as_tuple()) for ring in rings),
        )
    )
    return result


def main() -> None:
    """并行检查指定病例根目录下的全部参考模板。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases_root", type=Path)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--stage-output-root", type=Path)
    arguments = parser.parse_args()
    root = arguments.cases_root.resolve()
    output_root = arguments.stage_output_root.resolve() if arguments.stage_output_root else None
    paths = sorted(root.glob("**/input/taohuandaoban.stl"))
    results: list[dict[str, object]] = []
    with ProcessPoolExecutor(max_workers=arguments.workers) as pool:
        futures = {pool.submit(_check_case, path, root, output_root): path for path in paths}
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    passed = sum(bool(result["ok"]) for result in results)
    print(f"SUMMARY {passed}/{len(results)} ok")
    if passed != len(results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

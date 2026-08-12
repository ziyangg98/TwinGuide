"""快速渲染传统模板与按牙弓局部法向排列的导柱。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bpy

from twin_guide.blender.rendering import render_objects
from twin_guide.case_analysis import analyze_case
from twin_guide.config import CaseConfig, RenderParameters


def main() -> None:
    """生成只包含传统模板和导柱的方向预览。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    script_arguments = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    arguments = parser.parse_args(script_arguments)
    config = CaseConfig.from_yaml(arguments.config)
    case = analyze_case(config, force_rebuild=True)
    template_material = bpy.data.materials.new("template_blue")
    template_material.diffuse_color = (0.10, 0.36, 0.68, 1.0)
    guide_material = bpy.data.materials.new("guide_orange")
    guide_material.diffuse_color = (1.0, 0.30, 0.03, 1.0)
    case.input_meshes.template_mesh.data.materials.append(template_material)
    for guide in case.guide_sleeves:
        guide.guide_mesh.data.materials.append(guide_material)
    arguments.output.mkdir(parents=True, exist_ok=True)
    image_path = arguments.output / "guide-post-direction.png"
    report_path = arguments.output / "guide-post-direction.json"
    render_objects(
        image_path,
        (case.input_meshes.template_mesh, *(item.guide_mesh for item in case.guide_sleeves)),
        RenderParameters(width_px=1200, height_px=1000),
        "iso",
    )
    first, second = case.guide_sleeves[:2]
    pair = (second.center - first.center).normalized()
    report_path.write_text(
        json.dumps(
            {
                "case": config.case_id,
                "pair_direction": pair.as_tuple(),
                "guide_centers": [item.center.as_tuple() for item in case.guide_sleeves],
                "image": str(image_path.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(image_path.resolve())


if __name__ == "__main__":
    main()

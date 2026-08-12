"""输出参考模板圆环中心轴线的 JSON 诊断。"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

import trimesh

from twin_guide.config import CaseConfig
from twin_guide.template_ring_estimation import (
    estimate_template_ring_top_plane,
    estimate_template_rings_from_stl,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--template", type=Path, help="参考模板 STL")
    source.add_argument("--case-config", type=Path, help="包含 guide_posts 的病例 YAML")
    arguments = parser.parse_args()
    config = None if arguments.case_config is None else CaseConfig.from_yaml(arguments.case_config)
    template = arguments.template if config is None else config.inputs.template
    rings = estimate_template_rings_from_stl(template)
    mesh = trimesh.load_mesh(template, process=True)
    guide_posts = {} if config is None else {item.ring_index: item for item in config.guide_posts}
    invalid_indices = sorted(set(guide_posts) - set(range(1, len(rings) + 1)))
    if invalid_indices:
        parser.error(
            "guide_posts.ring_index 超出识别圆环数量：" + ", ".join(map(str, invalid_indices))
        )
    result = {
        "template": str(template.resolve()),
        "used_sleeve_stl": False,
        "ring_count": len(rings),
        "rings": [
            {
                **asdict(ring),
                "top_plane": asdict(estimate_template_ring_top_plane(mesh, ring)),
                "guide_post_parameters": (
                    None
                    if (parameters := guide_posts.get(index)) is None
                    else {
                        "drill_length_mm": parameters.drill_length_mm,
                        "implant_length_mm": parameters.implant_length_mm,
                        "sleeve_template_extension_mm": (parameters.sleeve_template_extension_mm),
                        "guide_spacing_mm": config.sleeve.guide_spacing_mm,
                        "handpiece_insertion_length_mm": 12.0,
                        "twin_guide_extension_mm": parameters.twin_guide_extension_mm,
                    }
                ),
            }
            for index, ring in enumerate(rings, 1)
        ],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

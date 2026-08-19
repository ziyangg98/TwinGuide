"""生成可审核、包含全部默认值的有效病例配置。"""

from __future__ import annotations

import hashlib
from pathlib import Path

from twin_guide.case_schema import normalize_case_definition
from twin_guide.config import CaseConfig
from twin_guide.config.loading import load_case_yaml
from twin_guide.stage_artifacts import _json_value
from twin_guide.ui_jobs import write_manifest

EFFECTIVE_CASE_SCHEMA = "twin-guide.effective-case/1.0"
EFFECTIVE_CASE_NAME = "effective-case.json"


def write_effective_case(config: CaseConfig) -> Path:
    """原子写出原始来源、规范化病例和已解析运行配置。"""

    inputs = config.tooth_identification
    if inputs is None:
        raise ValueError("有效病例配置缺少源 case.yaml")
    source = inputs.case_yaml.resolve()
    source_bytes = source.read_bytes()
    normalized = normalize_case_definition(load_case_yaml(source))
    path = config.output_directory / EFFECTIVE_CASE_NAME
    write_manifest(
        path,
        {
            "schema_version": EFFECTIVE_CASE_SCHEMA,
            "source": {
                "path": str(source),
                "sha256": hashlib.sha256(source_bytes).hexdigest(),
            },
            "tooth_identification_backend": inputs.backend.value,
            "normalized_case_definition": normalized,
            "resolved_config": _json_value(config),
        },
    )
    return path


__all__ = ["EFFECTIVE_CASE_NAME", "EFFECTIVE_CASE_SCHEMA", "write_effective_case"]

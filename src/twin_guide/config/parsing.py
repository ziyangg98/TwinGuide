"""YAML 字段的基础类型、路径与未知键解析。"""

from __future__ import annotations

import math
import re
from pathlib import Path

from twin_guide.config.types import Jaw
from twin_guide.errors import ConfigurationError

CASE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

def _mapping(value: object, name: str) -> dict[str, object]:
    """校验配置值为字符串键映射并返回该映射。"""

    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ConfigurationError(f"{name} 必须为对象")
    return value


def _required(values: dict[str, object], name: str) -> object:
    """读取配置中必须提供的字段。"""

    if name not in values:
        raise ConfigurationError(f"缺少必填字段：{name}")
    return values[name]


def _section(values: dict[str, object], name: str) -> dict[str, object]:
    """读取必填配置分组并校验其映射类型。"""

    return _mapping(_required(values, name), name)


def _reject_unknown(values: dict[str, object], allowed: set[str], section: str) -> None:
    """拒绝配置分组中不在允许集合内的字段。"""

    unknown = sorted(values.keys() - allowed)
    if unknown:
        raise ConfigurationError(f"{section} 包含未知字段：{', '.join(unknown)}")


def _number(value: object, name: str, *, positive: bool = False) -> float:
    """将配置值校验为有限非负数或正数。"""

    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigurationError(f"{name} 必须为数值")
    number = float(value)
    if not math.isfinite(number):
        raise ConfigurationError(f"{name} 必须为有限数")
    if positive and number <= 0:
        raise ConfigurationError(f"{name} 必须为正数")
    if not positive and number < 0:
        raise ConfigurationError(f"{name} 不得为负数")
    return number


def _positive_integer(value: object, name: str) -> int:
    """将配置值校验为正整数。"""

    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigurationError(f"{name} 必须为正整数")
    return value


def _boolean(value: object, name: str) -> bool:
    """校验严格布尔值，拒绝用 0/1 或字符串代替。"""

    if not isinstance(value, bool):
        raise ConfigurationError(f"{name} 必须为布尔值")
    return value


def _case_id(value: object) -> str:
    """校验病例标识符只含允许的小写字符。"""

    if not isinstance(value, str) or not CASE_ID_PATTERN.fullmatch(value):
        raise ConfigurationError("case_id 只能包含小写字母、数字、'-' 或 '_'")
    return value


def _case_yaml_jaw(value: object) -> Jaw:
    """将病例 YAML 的解剖学上下颌名称转换为运行枚举。"""

    aliases = {"maxillary": Jaw.UPPER, "mandibular": Jaw.LOWER}
    if not isinstance(value, str) or value not in aliases:
        raise ConfigurationError("anatomy.jaw 必须为 maxillary 或 mandibular")
    return aliases[value]


def _path(value: object, base_directory: Path, name: str) -> Path:
    """解析相对病例目录且不允许越界的路径。"""

    if not isinstance(value, str) or not value:
        raise ConfigurationError(f"{name} 必须为非空路径字符串")
    path = Path(value)
    if path.is_absolute():
        raise ConfigurationError(f"{name} 必须使用相对病例目录的路径")
    case_directory = base_directory.resolve()
    resolved = (case_directory / path).resolve()
    if not resolved.is_relative_to(case_directory):
        raise ConfigurationError(f"{name} 必须位于病例目录内")
    return resolved


def _stl_reference(value: object, base_directory: Path, name: str) -> Path:
    """解析路径并校验其扩展名为 STL。"""

    path = _path(value, base_directory, name)
    if path.suffix.lower() != ".stl":
        raise ConfigurationError(f"{name} 必须指向 STL 文件：{path}")
    return path


def _stl_path(value: object, base_directory: Path, name: str) -> Path:
    """解析 STL 路径并校验文件实际存在。"""

    path = _stl_reference(value, base_directory, name)
    if not path.is_file():
        raise ConfigurationError(f"{name} 必须指向已存在的 STL 文件：{path}")
    return path


def _json_path(value: object, base_directory: Path, name: str) -> Path:
    """解析并校验实际存在的 JSON 报告路径。"""

    path = _path(value, base_directory, name)
    if path.suffix.lower() != ".json" or not path.is_file():
        raise ConfigurationError(f"{name} 必须指向已存在的 JSON 文件：{path}")
    return path



__all__ = [
    "CASE_ID_PATTERN",
    "_boolean",
    "_case_id",
    "_case_yaml_jaw",
    "_json_path",
    "_mapping",
    "_number",
    "_path",
    "_positive_integer",
    "_reject_unknown",
    "_required",
    "_section",
    "_stl_path",
    "_stl_reference",
]

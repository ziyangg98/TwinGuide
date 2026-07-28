"""严格且拒绝重复键的病例 YAML 加载器。"""

from __future__ import annotations

from pathlib import Path

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

from twin_guide.errors import ConfigurationError


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """拒绝重复映射键的安全 YAML 加载器。"""


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader,
    node: MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    """构造 YAML 映射，并在同层键重复时立即报错。"""

    loader.flatten_mapping(node)
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicated = key in mapping
        except TypeError as error:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            ) from error
        if duplicated:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def load_case_yaml(path: Path) -> object:
    """以严格重复键策略读取一份病例 YAML。"""

    try:
        return yaml.load(
            path.read_text(encoding="utf-8"),
            Loader=_UniqueKeySafeLoader,
        )
    except (OSError, yaml.YAMLError) as error:
        raise ConfigurationError(f"无法读取病例 YAML {path}：{error}") from error




__all__ = ["load_case_yaml"]


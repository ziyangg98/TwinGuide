"""跨字段病例语义、牙合轴与生产审核检查。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from twin_guide.config.loading import load_case_yaml
from twin_guide.config.parsing import _mapping, _required
from twin_guide.config.types import (
    GuideAnchorMode,
    ToothIdentificationInputs,
)
from twin_guide.errors import ConfigurationError

if TYPE_CHECKING:
    from twin_guide.config._core import CaseConfig


@dataclass(frozen=True, slots=True)
class ProductionReviewStatus:
    """与几何检验相互独立的病例人工审核状态。"""

    pending_fields: tuple[str, ...] = ()

    @property
    def confirmed(self) -> bool:
        """返回病例是否没有待人工确认字段。"""
        return not self.pending_fields


def _load_case_yaml_anatomy(inputs: ToothIdentificationInputs) -> dict[str, object]:
    """读取特殊拓扑配置所需的病例牙位语义。"""

    raw_value = load_case_yaml(inputs.case_yaml)
    root = _mapping(raw_value, "case.yaml")
    return _mapping(_required(root, "anatomy"), "case.yaml anatomy")


def case_occlusal_axis(config: CaseConfig) -> tuple[float, float, float] | None:
    """返回病例 YAML 中确认的世界坐标牙合轴。

    未配置牙位病例或 YAML 未显式提供 ``anatomy.orientation`` 时返回
    ``None``，调用方可继续采用与旧病例兼容的上下颌世界 Z 轴规则。
    """

    inputs = config.tooth_identification
    if inputs is None:
        return None
    anatomy = _load_case_yaml_anatomy(inputs)
    raw_orientation = anatomy.get("orientation")
    if raw_orientation is None:
        return None
    orientation = _mapping(raw_orientation, "case.yaml anatomy.orientation")
    raw_axis = _required(orientation, "occlusal_axis")
    named_axes = {
        "+X": (1.0, 0.0, 0.0),
        "-X": (-1.0, 0.0, 0.0),
        "+Y": (0.0, 1.0, 0.0),
        "-Y": (0.0, -1.0, 0.0),
        "+Z": (0.0, 0.0, 1.0),
        "-Z": (0.0, 0.0, -1.0),
    }
    if isinstance(raw_axis, str):
        values = named_axes.get(raw_axis.strip().upper())
        if values is None:
            raise ConfigurationError(
                "case.yaml anatomy.orientation.occlusal_axis 必须为 "
                "+X/-X/+Y/-Y/+Z/-Z 或三元素数值数组"
            )
    elif (
        isinstance(raw_axis, list | tuple)
        and len(raw_axis) == 3
        and all(
            not isinstance(value, bool) and isinstance(value, int | float) for value in raw_axis
        )
    ):
        values = tuple(float(value) for value in raw_axis)
    else:
        raise ConfigurationError(
            "case.yaml anatomy.orientation.occlusal_axis 必须为 +X/-X/+Y/-Y/+Z/-Z 或三元素数值数组"
        )
    length = math.sqrt(sum(value * value for value in values))
    if not math.isfinite(length) or length <= 1e-8:
        raise ConfigurationError("case.yaml anatomy.orientation.occlusal_axis 必须为有限非零向量")
    return tuple(value / length for value in values)


def production_review_status(config: CaseConfig) -> ProductionReviewStatus:
    """读取病例审核状态，但不阻止预览或几何检验。"""
    inputs = config.tooth_identification
    if inputs is None:
        return ProductionReviewStatus()
    raw_value = load_case_yaml(inputs.case_yaml)
    root = _mapping(raw_value, "case.yaml")
    pending_values = {"pending", "pending_user_input", "unreviewed"}
    pending_fields = []
    raw_anatomy = root.get("anatomy")
    if isinstance(raw_anatomy, dict):
        value = raw_anatomy.get("review_status")
        if isinstance(value, str) and value.strip().lower() in pending_values:
            pending_fields.append("anatomy.review_status")
    raw_review = root.get("review")
    if isinstance(raw_review, dict):
        pending_fields.extend(
            f"review.{key}"
            for key, value in raw_review.items()
            if key.endswith("_status")
            and isinstance(value, str)
            and value.strip().lower() in pending_values
        )
    return ProductionReviewStatus(tuple(pending_fields))


def require_production_review(config: CaseConfig) -> None:
    """拒绝使用明确标记为待人工审核的病例执行生产生成。"""

    status = production_review_status(config)
    if not status.confirmed:
        raise ConfigurationError(
            "生产生成被 case.yaml 待审核状态阻止："
            + ", ".join(status.pending_fields)
            + "；请完成人工确认，或在明确承担风险时使用 "
            "--allow-unreviewed"
        )


def _anatomy_fdis(anatomy: dict[str, object], key: str) -> tuple[int, ...]:
    """读取并校验病例牙列语义中的 FDI 数组。"""

    raw = _required(anatomy, key)
    if not isinstance(raw, list):
        raise ConfigurationError(f"case.yaml anatomy.{key} 必须为 FDI 数组")
    return tuple(
        _fdi(value, f"case.yaml anatomy.{key}[{index}]") for index, value in enumerate(raw)
    )


def _validate_distal_pair(
    terminal_fdi: int,
    reference_fdi: int,
    present_fdis: set[int],
    *,
    terminal_must_be_present: bool,
    section: str,
) -> None:
    """要求参考牙为终末牙的直接近中邻牙，且远中无更后现存牙。"""

    terminal_quadrant, terminal_position = divmod(terminal_fdi, 10)
    reference_quadrant, reference_position = divmod(reference_fdi, 10)
    if terminal_quadrant != reference_quadrant or terminal_position != reference_position + 1:
        raise ConfigurationError(f"{section} 必须满足参考牙→直接远中终末牙的相邻关系")
    if reference_fdi not in present_fdis:
        raise ConfigurationError(f"{section} 的参考邻牙必须为现存牙")
    if terminal_must_be_present and terminal_fdi not in present_fdis:
        raise ConfigurationError(f"{section} 的终末牙必须为现存牙")
    if any(
        divmod(fdi, 10)[0] == terminal_quadrant and divmod(fdi, 10)[1] > terminal_position
        for fdi in present_fdis
    ):
        raise ConfigurationError(f"{section} 的 terminal_fdi 不是当前牙列末端")


def validate_special_case_anatomy(config: CaseConfig) -> None:
    """在进入几何阶段前校验 #14/#17 类特殊病例的牙位语义。"""

    assert config.tooth_identification is not None
    terminal = config.guide_anchors.terminal_distal_common_node
    extension = config.guide_terminal_u_extension
    if terminal is None and not extension.enabled:
        return
    anatomy = _load_case_yaml_anatomy(config.tooth_identification)
    present = set(_anatomy_fdis(anatomy, "present_teeth"))
    missing = set(_anatomy_fdis(anatomy, "missing_teeth"))
    if terminal is not None:
        if terminal.missing_fdi not in missing:
            raise ConfigurationError(
                "guide_anchors.terminal_distal_common_node.missing_fdi "
                "必须在 anatomy.missing_teeth 中"
            )
        if (
            config.guide_anchors.mode
            is GuideAnchorMode.ADJACENT_TWO_IMPLANT_TERMINAL_DISTAL_NODE_PATHS
        ):
            implant_fdis = terminal.implant_fdis
            if len(implant_fdis) != 2:
                raise ConfigurationError("双种植位末端牙龈模式必须配置两个 implant_fdis")
            if terminal.missing_fdi != implant_fdis[-1]:
                raise ConfigurationError(
                    "terminal_distal_common_node.missing_fdi 必须是 implant_fdis 的远中末项"
                )
            quadrant, reference_position = divmod(terminal.reference_neighbor_fdi, 10)
            expected = tuple(
                quadrant * 10 + reference_position + offset
                for offset in range(1, len(implant_fdis) + 1)
            )
            if implant_fdis != expected:
                raise ConfigurationError("双种植位末端牙龈模式必须满足参考邻牙→两个连续远中种植位")
            if terminal.reference_neighbor_fdi not in present:
                raise ConfigurationError("末端远中公共节点参考邻牙必须为现存牙")
            if any(fdi not in missing for fdi in implant_fdis):
                raise ConfigurationError(
                    "双种植位末端牙龈模式的 implant_fdis 必须均在 missing_teeth 中"
                )
            if any(
                divmod(fdi, 10)[0] == quadrant
                and divmod(fdi, 10)[1] > divmod(implant_fdis[-1], 10)[1]
                for fdi in present
            ):
                raise ConfigurationError("末端种植位不是当前牙列远中末端")
        else:
            _validate_distal_pair(
                terminal.missing_fdi,
                terminal.reference_neighbor_fdi,
                present,
                terminal_must_be_present=False,
                section="guide_anchors.terminal_distal_common_node",
            )
    if extension.enabled:
        assert extension.terminal_fdi is not None
        assert extension.reference_neighbor_fdi is not None
        _validate_distal_pair(
            extension.terminal_fdi,
            extension.reference_neighbor_fdi,
            present,
            terminal_must_be_present=True,
            section="guide_terminal_u_extension",
        )


def _fdi(value: object, name: str) -> int:
    """校验一个恒牙 FDI 编码。"""

    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigurationError(f"{name} 必须为整数 FDI 编码")
    quadrant, position = divmod(value, 10)
    if quadrant not in {1, 2, 3, 4} or position not in set(range(1, 9)):
        raise ConfigurationError(f"{name} 不是有效恒牙 FDI 编码")
    return value


__all__ = [
    "case_occlusal_axis",
    "require_production_review",
    "validate_special_case_anatomy",
]

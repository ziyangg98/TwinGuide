"""计算双导导板止停台相对植体顶端的轴向延长量。"""

from __future__ import annotations

DRILL_LENGTH_INSIDE_HANDPIECE_MM = 12.0
DRILL_HANDPIECE_INSERTION_MM = DRILL_LENGTH_INSIDE_HANDPIECE_MM


def calculate_twin_guide_extension_mm(
    drill_length_mm: float,
    implant_length_mm: float,
) -> float:
    """返回植体顶端到双导止停台的轴向高度，单位为毫米。"""

    if drill_length_mm <= 0.0:
        raise ValueError("钻针长度必须大于 0 mm")
    if implant_length_mm <= 0.0:
        raise ValueError("植体长度必须大于 0 mm")

    extension_mm = drill_length_mm - DRILL_LENGTH_INSIDE_HANDPIECE_MM - implant_length_mm
    if extension_mm <= 0.0:
        raise ValueError("双导导板延长量必须大于 0 mm")
    return extension_mm


__all__ = [
    "DRILL_HANDPIECE_INSERTION_MM",
    "DRILL_LENGTH_INSIDE_HANDPIECE_MM",
    "calculate_twin_guide_extension_mm",
]

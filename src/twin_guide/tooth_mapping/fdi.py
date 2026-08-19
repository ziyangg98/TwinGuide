"""内部算法说明。\n\nFDI semantics and order-preserving interval utilities."""

from __future__ import annotations

import itertools
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass


class FDIError(ValueError):
    """内部算法说明。\n\nRaised when configured FDI semantics are inconsistent."""


CANONICAL_ORDER = {
    "maxillary": tuple(range(18, 10, -1)) + tuple(range(21, 29)),
    "mandibular": tuple(range(48, 40, -1)) + tuple(range(31, 39)),
}

# Full-jaw classification order requested by the clinical case schema.  This
# is intentionally separate from CANONICAL_ORDER: the latter is the directed
# patient-right-to-left traversal used by the geometry pipeline, while this
# order is used to prove that every permanent FDI position was classified
# exactly once as present, missing, or excluded.
CLASSIFICATION_ORDER = {
    "maxillary": tuple(range(18, 10, -1)) + tuple(range(21, 29)),
    "mandibular": tuple(range(38, 30, -1)) + tuple(range(41, 49)),
}
ALL_PERMANENT_FDI = frozenset(value for order in CLASSIFICATION_ORDER.values() for value in order)

# Approximate mesiodistal crown widths.  They are only a regularising prior;
# actual centres are refined from the current case's crown-support signal.
WIDTH_PRIOR_MM = {
    1: 8.5,
    2: 6.5,
    3: 7.5,
    4: 7.0,
    5: 7.0,
    6: 10.5,
    7: 10.0,
    8: 9.5,
}


@dataclass(frozen=True)
class AnatomySemantics:
    """内部算法说明。"""

    jaw: str
    fdi_order: tuple[int, ...]
    present_teeth: frozenset[int]
    missing_teeth: frozenset[int]
    excluded_teeth: frozenset[int]


def _as_unique_ints(name: str, values: Iterable[int] | None) -> tuple[int, ...]:
    """内部算法说明。"""
    if values is None:
        return ()
    result = tuple(int(value) for value in values)
    if len(set(result)) != len(result):
        raise FDIError(f"{name} contains duplicate FDI values: {result}")
    return result


def _as_ints(values: Iterable[int] | None) -> tuple[int, ...]:
    """内部算法说明。"""
    if values is None:
        return ()
    return tuple(int(value) for value in values)


def _ordered(values: set[int] | frozenset[int], order: tuple[int, ...]) -> list[int]:
    """内部算法说明。"""
    known = [value for value in order if value in values]
    unknown = sorted(set(values) - set(order))
    return known + unknown


def anatomy_classification_diagnostics(
    anatomy: dict[str, object],
) -> dict[str, object]:
    """内部算法说明。\n\nReturn exhaustive present/missing/excluded FDI classification QA."""

    jaw = str(anatomy.get("jaw", "")).strip().lower()
    expected_jaw_quadrants(jaw)
    expected_order = CLASSIFICATION_ORDER[jaw]
    expected = frozenset(expected_order)
    raw = {
        name: _as_ints(anatomy.get(name))
        for name in ("present_teeth", "missing_teeth", "excluded_teeth")
    }
    sets = {name: frozenset(values) for name, values in raw.items()}
    duplicate_teeth = {
        name: _ordered(
            {value for value, count in Counter(values).items() if count > 1},
            expected_order,
        )
        for name, values in raw.items()
    }
    duplicate_teeth = {name: values for name, values in duplicate_teeth.items() if values}
    multiply_classified = (
        (sets["present_teeth"] & sets["missing_teeth"])
        | (sets["present_teeth"] & sets["excluded_teeth"])
        | (sets["missing_teeth"] & sets["excluded_teeth"])
    )
    all_values = frozenset().union(*sets.values())
    unknown_fdi = all_values - ALL_PERMANENT_FDI
    invalid_for_jaw = (all_values & ALL_PERMANENT_FDI) - expected
    classified_for_jaw = all_values & expected
    unclassified = expected - classified_for_jaw
    failures = bool(
        duplicate_teeth
        or multiply_classified
        or unknown_fdi
        or invalid_for_jaw
        or unclassified
        or not sets["present_teeth"]
    )
    return {
        "jaw": jaw,
        "expected_FDI_order": list(expected_order),
        "present_teeth_empty": not bool(sets["present_teeth"]),
        "duplicate_teeth": duplicate_teeth,
        "multiply_classified": _ordered(multiply_classified, expected_order),
        "unknown_FDI": sorted(unknown_fdi),
        "invalid_for_jaw": _ordered(invalid_for_jaw, expected_order),
        "unclassified_teeth": _ordered(unclassified, expected_order),
        "classified_tooth_count": len(classified_for_jaw),
        "expected_tooth_count": len(expected),
        "classification_complete_and_exclusive": not failures,
    }


def quadrant(fdi: int) -> int:
    """内部算法说明。"""
    return int(fdi) // 10


def tooth_index(fdi: int) -> int:
    """内部算法说明。"""
    return int(fdi) % 10


def expected_jaw_quadrants(jaw: str) -> frozenset[int]:
    """内部算法说明。"""
    if jaw == "maxillary":
        return frozenset({1, 2})
    if jaw == "mandibular":
        return frozenset({3, 4})
    raise FDIError(f"jaw must be 'maxillary' or 'mandibular', got {jaw!r}")


def derive_fdi_order(
    jaw: str,
    present_teeth: Iterable[int],
    missing_teeth: Iterable[int],
    explicit_order: Iterable[int] | None = None,
) -> tuple[int, ...]:
    """内部算法说明。\n\nReturn the patient-right-posterior to patient-left-posterior order.

    The order contains present and explicit missing slots.  Excluded teeth are
    intentionally absent.  An explicit order is accepted only if it is a
    monotonic subsequence of the canonical FDI order and contains exactly the
    configured active slots.
    """

    present = _as_unique_ints("present_teeth", present_teeth)
    missing = _as_unique_ints("missing_teeth", missing_teeth)
    active = set(present) | set(missing)
    canonical = CANONICAL_ORDER.get(jaw)
    if canonical is None:
        expected_jaw_quadrants(jaw)
        raise AssertionError("unreachable")
    unknown = active - set(canonical)
    if unknown:
        raise FDIError(f"FDI values {sorted(unknown)} do not belong to {jaw}")
    derived = tuple(label for label in canonical if label in active)
    if explicit_order is None:
        return derived
    explicit = _as_unique_ints("fdi_order", explicit_order)
    if set(explicit) != active:
        raise FDIError(
            "fdi_order must contain exactly present_teeth + missing_teeth; "
            f"expected {sorted(active)}, got {sorted(explicit)}"
        )
    if explicit != derived:
        raise FDIError(
            "fdi_order is not canonical patient-right-to-left order; "
            f"expected {list(derived)}, got {list(explicit)}"
        )
    return explicit


def validate_anatomy(anatomy: dict[str, object]) -> AnatomySemantics:
    """内部算法说明。"""
    jaw = str(anatomy.get("jaw", "")).strip().lower()
    expected_jaw_quadrants(jaw)
    diagnostics = anatomy_classification_diagnostics(anatomy)
    if not diagnostics["classification_complete_and_exclusive"]:
        raise FDIError(f"invalid FDI present/missing/excluded classification: {diagnostics}")
    present = frozenset(_as_unique_ints("present_teeth", anatomy.get("present_teeth")))
    missing = frozenset(_as_unique_ints("missing_teeth", anatomy.get("missing_teeth")))
    excluded = frozenset(_as_unique_ints("excluded_teeth", anatomy.get("excluded_teeth")))
    order = derive_fdi_order(jaw, present, missing, anatomy.get("fdi_order"))
    return AnatomySemantics(jaw, order, present, missing, excluded)


def crown_width_prior_mm(fdi: int) -> float:
    """内部算法说明。"""
    try:
        return float(WIDTH_PRIOR_MM[tooth_index(fdi)])
    except KeyError as error:
        raise FDIError(f"invalid permanent-tooth FDI code: {fdi}") from error


def configured_missing_gap_pair_indices(
    semantics: AnatomySemantics,
) -> set[int]:
    """内部算法说明。\n\nReturn present-tooth pair indices separated by configured missing FDI.

    A missing tooth before the first or after the last present tooth does not
    create a gap in the observed instance sequence.  Only a missing slot lying
    between two present teeth forces a semantic gap separator.
    """

    present_order = [label for label in semantics.fdi_order if label in semantics.present_teeth]
    position = {label: index for index, label in enumerate(semantics.fdi_order)}
    return {
        pair_index
        for pair_index, (first, second) in enumerate(itertools.pairwise(present_order))
        if position[second] - position[first] > 1
    }


def signed_midline_distance_prior_mm(fdi: int, jaw: str) -> float:
    """内部算法说明。\n\nSigned centre distance from the dental midline.

    Negative is patient right and positive is patient left.  The value is
    derived from tooth identity, never from the number of detected cusps.
    """

    index = tooth_index(fdi)
    distance = sum(WIDTH_PRIOR_MM[item] for item in range(1, index)) + 0.5 * WIDTH_PRIOR_MM[index]
    q = quadrant(fdi)
    right_quadrant = 1 if jaw == "maxillary" else 4
    left_quadrant = 2 if jaw == "maxillary" else 3
    if q == right_quadrant:
        return -float(distance)
    if q == left_quadrant:
        return float(distance)
    raise FDIError(f"FDI {fdi} does not belong to {jaw}")


def tooth_interval_from_centres(
    label: int,
    centres_s_mm: dict[int, float],
    order: tuple[int, ...],
    scale: float,
) -> tuple[float, float]:
    """内部算法说明。\n\nReturn a conservative full-crown interval in directed arch distance."""

    center = float(centres_s_mm[label])
    index = order.index(label)
    if index > 0:
        lower = 0.5 * (float(centres_s_mm[order[index - 1]]) + center)
    else:
        lower = center - 0.5 * scale * crown_width_prior_mm(label)
    if index + 1 < len(order):
        upper = 0.5 * (center + float(centres_s_mm[order[index + 1]]))
    else:
        upper = center + 0.5 * scale * crown_width_prior_mm(label)
    return (min(lower, upper), max(lower, upper))


def observation_window_interval(
    start_fdi: int,
    end_fdi: int,
    extent_mode: str,
    centres_s_mm: dict[int, float],
    intervals_s_mm: dict[int, tuple[float, float]],
) -> tuple[float, float]:
    """内部算法说明。"""
    if start_fdi not in centres_s_mm or end_fdi not in centres_s_mm:
        raise FDIError(f"window endpoints {start_fdi}->{end_fdi} are not mapped FDI slots")
    start_center = float(centres_s_mm[start_fdi])
    end_center = float(centres_s_mm[end_fdi])
    if extent_mode == "center_to_center":
        return start_center, end_center
    if extent_mode == "full_teeth":
        if start_center <= end_center:
            return intervals_s_mm[start_fdi][0], intervals_s_mm[end_fdi][1]
        return intervals_s_mm[start_fdi][1], intervals_s_mm[end_fdi][0]
    raise FDIError(f"unsupported observation-window extent_mode: {extent_mode!r}")

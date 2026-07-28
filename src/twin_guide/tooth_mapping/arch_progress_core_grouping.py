"""内部算法说明。\n\nCrown-core grouping policies used by TwinGuide workflows.

The production library currently merges adjacent crown cores by their planar
Euclidean distance.  That can split a terminal molar when its buccal and
lingual cores are farther apart in the projection than two genuinely adjacent
anterior teeth.  This module keeps the existing core detector, but changes the
merge priority to separation along the directed dental arch. ``arch_progress``
is the current default; ``legacy_euclidean`` remains available only for
reproducibility of historical reports.
"""

from __future__ import annotations

import itertools

import numpy as np

from .contact_chords import (
    CrownCoreCandidate,
    CrownSeed,
)
from .contact_chords import (
    select_crown_core_candidates as _select_legacy_candidates,
)

LEGACY_POLICY = "legacy_euclidean"
ARCH_PROGRESS_POLICY = "arch_progress"
CORE_GROUPING_POLICIES = (LEGACY_POLICY, ARCH_PROGRESS_POLICY)
DEFAULT_POLICY = ARCH_PROGRESS_POLICY
MAXIMUM_PLANAR_MERGE_DISTANCE_MM = 5.75


def _merge_pair(
    first: CrownCoreCandidate,
    second: CrownCoreCandidate,
    planar_distance_mm: float,
) -> CrownCoreCandidate:
    """内部算法说明。"""
    weights = np.asarray([
        max(first.maximum_depth_mm, 0.1) ** 2,
        max(second.maximum_depth_mm, 0.1) ** 2,
    ])
    centres = np.asarray([
        first.center_lr_ap_mm,
        second.center_lr_ap_mm,
    ], dtype=float)
    merged_center = np.average(centres, axis=0, weights=weights)
    return CrownCoreCandidate(
        candidate_id=min(first.member_candidate_ids + second.member_candidate_ids),
        center_lr_ap_mm=(float(merged_center[0]), float(merged_center[1])),
        maximum_depth_mm=float(max(
            first.maximum_depth_mm,
            second.maximum_depth_mm,
        )),
        directed_arch_position_mm=float(np.average(
            [
                first.directed_arch_position_mm,
                second.directed_arch_position_mm,
            ],
            weights=weights,
        )),
        crown_core_quality=float(max(
            first.crown_core_quality,
            second.crown_core_quality,
        )),
        member_candidate_ids=tuple(sorted(
            first.member_candidate_ids + second.member_candidate_ids
        )),
        maximum_merge_step_mm=float(max(
            first.maximum_merge_step_mm,
            second.maximum_merge_step_mm,
            planar_distance_mm,
        )),
        merge_evidence_sufficient=bool(
            first.merge_evidence_sufficient
            and second.merge_evidence_sufficient
            and planar_distance_mm <= MAXIMUM_PLANAR_MERGE_DISTANCE_MM
        ),
    )


def group_candidates_by_arch_progress(
    candidates: list[CrownCoreCandidate],
    target_count: int,
) -> list[CrownCoreCandidate]:
    """内部算法说明。\n\nMerge adjacent cores with the smallest directed-arch separation first."""

    grouped = list(candidates)
    while len(grouped) > target_count:
        eligible: list[tuple[float, float, int]] = []
        for pair_index, (first, second) in enumerate(itertools.pairwise(grouped)):
            planar_distance = float(np.linalg.norm(
                np.asarray(second.center_lr_ap_mm, dtype=float)
                - np.asarray(first.center_lr_ap_mm, dtype=float)
            ))
            if planar_distance > MAXIMUM_PLANAR_MERGE_DISTANCE_MM:
                continue
            arch_separation = abs(
                second.directed_arch_position_mm
                - first.directed_arch_position_mm
            )
            eligible.append((float(arch_separation), planar_distance, pair_index))
        if not eligible:
            break
        _, planar_distance, pair_index = min(eligible)
        grouped[pair_index:pair_index + 2] = [
            _merge_pair(
                grouped[pair_index],
                grouped[pair_index + 1],
                planar_distance,
            )
        ]
    return grouped


def select_crown_core_candidates(
    *,
    enhanced_maps: dict[str, np.ndarray | float],
    ordered_instances: list,
    policy: str,
) -> tuple[list[CrownCoreCandidate], list[CrownCoreCandidate]]:
    """内部算法说明。\n\nSelect crown cores with an explicitly chosen grouping policy."""

    if policy not in CORE_GROUPING_POLICIES:
        raise ValueError(f"unsupported crown-core grouping policy: {policy}")
    candidates, legacy_selected = _select_legacy_candidates(
        enhanced_maps=enhanced_maps,
        ordered_instances=ordered_instances,
    )
    if policy == LEGACY_POLICY:
        return candidates, legacy_selected
    selected = group_candidates_by_arch_progress(
        candidates,
        len(ordered_instances),
    )
    # Preserve the fail-closed/subset behavior of the established selector when
    # the experimental merge evidence cannot reach the configured tooth count.
    if len(selected) != len(ordered_instances):
        return candidates, legacy_selected
    return candidates, selected


def core_groups_to_seeds(
    ordered_instances: list,
    selected_groups: list[CrownCoreCandidate],
) -> list[CrownSeed]:
    """内部算法说明。\n\nUse measured interior core centres as topology seeds for script trials."""

    if len(ordered_instances) != len(selected_groups):
        raise ValueError("one selected crown-core group is required per present tooth")
    return [
        CrownSeed(
            instance_id=int(instance.instance_id),
            center_lr_ap_mm=tuple(float(value) for value in group.center_lr_ap_mm),
            initial_center_lr_ap_mm=tuple(
                float(value) for value in instance.center_lr_ap_mm
            ),
            core_pixel_count=0,
            refinement_distance_mm=float(np.linalg.norm(
                np.asarray(group.center_lr_ap_mm, dtype=float)
                - np.asarray(instance.center_lr_ap_mm, dtype=float)
            )),
        )
        for instance, group in zip(ordered_instances, selected_groups, strict=False)
    ]


__all__ = [
    "ARCH_PROGRESS_POLICY",
    "CORE_GROUPING_POLICIES",
    "DEFAULT_POLICY",
    "LEGACY_POLICY",
    "core_groups_to_seeds",
    "group_candidates_by_arch_progress",
    "select_crown_core_candidates",
]

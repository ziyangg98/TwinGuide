"""算法说明。 Non-overlapping crown hypotheses and global monotone FDI alignment."""

from __future__ import annotations

from dataclasses import dataclass
import math
from types import SimpleNamespace

import numpy as np
from scipy.ndimage import distance_transform_edt
from skimage.measure import label

from twin_guide.tooth_mapping.contact_chords import (
    CrownSeed,
    find_shortest_concavity_chords,
)
from twin_guide.tooth_mapping.fdi import crown_width_prior_mm, signed_midline_distance_prior_mm

from .component_segmentation import (
    _finite_chord_3d_boundary_support,
    _independent_crown_basin_support,
    _surface_valley_barrier,
)
from .models import (
    AlignmentAssignment,
    AlignmentPath,
    ArchFrame,
    CoreObservation,
    CoreTrack,
    CrownHypothesis,
)


EPS = 1.0e-6


def _physical_crownness(track: CoreTrack) -> float:
    """算法说明。 One-sided crownness adjusted only for jointly flat 3-D support."""

    return float(np.clip(
        track.crownness * track.relative_3d_tooth_support, EPS, 0.9999
    ))


def _persistent_subbasin_evidence(
    track: CoreTrack,
    tracks: list[CoreTrack],
    maps_by_quantile: dict[float, dict[str, object]] | None,
    minimum_persistence: float,
    minimum_separation_scale: float,
) -> tuple[float, tuple[tuple[float, float], ...]]:
    """内部算法说明。 Return two *simultaneous* crown basins, or no split evidence.

    A curve through one crown is not evidence of two teeth.  Before a split
    hypothesis exists, two distance-transform basins must coexist at several
    height levels in the same physical support.  If the second basin is already
    represented by another persistent atomic track, it must be consumed as
    that track rather than duplicated by splitting its neighbour.
    """

    if not maps_by_quantile:
        return 0.0, ()
    local_scale = max(float(track.local_scale_mm), EPS)
    scale_count = max(len(maps_by_quantile), 1)
    observations_by_scale: dict[int, list[tuple[CoreObservation, int]]] = {}
    for candidate in tracks:
        for observation in candidate.observations:
            observations_by_scale.setdefault(observation.scale_index, []).append(
                (observation, candidate.track_id)
            )

    simultaneous_pairs: list[
        tuple[tuple[float, float], tuple[float, float], int, int]
    ] = []
    for scale_index, maps in enumerate(maps_by_quantile.values()):
        mask = np.asarray(maps["silhouette"], dtype=bool)
        lr = np.asarray(maps["lr_centres"], dtype=float)
        ap = np.asarray(maps["ap_centres"], dtype=float)
        components = label(mask, connectivity=2)
        center_row = int(np.argmin(np.abs(lr - track.center_lr_ap_mm[0])))
        center_column = int(np.argmin(np.abs(ap - track.center_lr_ap_mm[1])))
        component_id = int(components[center_row, center_column])
        if component_id <= 0:
            continue
        candidates: list[tuple[CoreObservation, int]] = []
        for observation, owner_id in observations_by_scale.get(scale_index, []):
            if abs(observation.s_mm - track.s_mm) > 1.05 * local_scale:
                continue
            if abs(observation.u_mm - track.u_mm) > 0.70 * local_scale:
                continue
            row = int(np.argmin(np.abs(lr - observation.center_lr_ap_mm[0])))
            column = int(np.argmin(np.abs(ap - observation.center_lr_ap_mm[1])))
            if int(components[row, column]) != component_id:
                continue
            candidates.append((observation, owner_id))

        # Greedy peak de-duplication is resolution relative.  It only removes
        # duplicate maxima at the same physical basin; it never joins separated
        # basins or consults the expected tooth count.
        unique: list[tuple[CoreObservation, int]] = []
        for candidate in sorted(
            candidates,
            key=lambda item: item[0].interior_radius_mm,
            reverse=True,
        ):
            point = np.asarray(candidate[0].center_lr_ap_mm, dtype=float)
            if any(
                np.linalg.norm(
                    point - np.asarray(existing[0].center_lr_ap_mm, dtype=float)
                ) <= 0.12 * local_scale
                for existing in unique
            ):
                continue
            unique.append(candidate)

        possible_pairs = []
        for first_index, first in enumerate(unique):
            for second in unique[first_index + 1:]:
                s_separation = abs(first[0].s_mm - second[0].s_mm)
                if not (
                    minimum_separation_scale * local_scale
                    <= s_separation
                    <= 1.25 * local_scale
                ):
                    continue
                # Prefer two deep, similarly supported basins.  The ordering is
                # used only to select among already valid simultaneous pairs.
                strength = min(
                    first[0].interior_radius_mm,
                    second[0].interior_radius_mm,
                )
                possible_pairs.append((strength, first, second))
        if not possible_pairs:
            continue
        _, first, second = max(possible_pairs, key=lambda item: item[0])
        if first[0].s_mm > second[0].s_mm:
            first, second = second, first
        simultaneous_pairs.append((
            first[0].center_lr_ap_mm,
            second[0].center_lr_ap_mm,
            first[1],
            second[1],
        ))

    persistence = len(simultaneous_pairs) / scale_count
    if persistence + EPS < minimum_persistence:
        return 0.0, ()

    # If another accepted atomic track persistently occupies one branch, the
    # geometry already contains two consumable objects.  Creating a split on
    # top of them is the exact split+merge cardinality compensation failure.
    stable_ids = {
        item.track_id for item in tracks
        if item.track_id != track.track_id
        and item.persistence >= minimum_persistence
        and item.relative_3d_tooth_support >= 1.0 - EPS
    }
    branch_owner_ids = [
        {pair[2] for pair in simultaneous_pairs},
        {pair[3] for pair in simultaneous_pairs},
    ]
    if any(owners & stable_ids for owners in branch_owner_ids):
        return 0.0, ()

    first_center = np.median(
        np.asarray([item[0] for item in simultaneous_pairs], dtype=float), axis=0
    )
    second_center = np.median(
        np.asarray([item[1] for item in simultaneous_pairs], dtype=float), axis=0
    )
    return float(persistence), (
        (float(first_center[0]), float(first_center[1])),
        (float(second_center[0]), float(second_center[1])),
    )


def build_crown_hypotheses(
    tracks: list[CoreTrack],
    frame: ArchFrame,
    maps_by_quantile: dict[float, dict[str, object]] | None = None,
    minimum_single_persistence: float = 0.60,
    minimum_independent_core_separation_scale: float = 0.35,
    minimum_surface_valley_mean_support: float = 0.36,
    minimum_surface_valley_coverage: float = 0.32,
) -> list[CrownHypothesis]:
    """算法说明。 Generate alternatives without permanently merging or deleting cores."""

    hypotheses: list[CrownHypothesis] = []
    median_scale = float(np.median([track.local_scale_mm for track in tracks])) if tracks else 8.0
    total_scale_count = len(maps_by_quantile) if maps_by_quantile else max(
        (max(track.support_scale_indices, default=-1) for track in tracks), default=4
    ) + 1
    total_scale_count = max(total_scale_count, 1)

    # Determine physical crown groups before generating label hypotheses.  Two
    # adjacent atomic tracks whose local 3-D relief maxima merge without a
    # persistent saddle are alternative peaks of one physical crown, not two
    # teeth.  This topology is independent of present_FDI count and prevents the
    # alignment stage from assigning two labels to the same crown basin.
    basin_parent = list(range(len(tracks)))
    independent_adjacent_pairs: set[tuple[int, int]] = set()

    def find(index: int) -> int:
        """内部算法说明。"""
        while basin_parent[index] != index:
            basin_parent[index] = basin_parent[basin_parent[index]]
            index = basin_parent[index]
        return index

    def union(first: int, second: int) -> None:
        """内部算法说明。"""
        first_root = find(first)
        second_root = find(second)
        if first_root != second_root:
            basin_parent[second_root] = first_root

    # Disabled deliberately: 3-D local boundary evidence is diagnostic during
    # segmentation and is not an admissibility constraint for the global path.
    # Pre-clustering here caused one uncertain local measurement to rewrite the
    # whole-arch FDI assignment (notably WHL-26).
    if False and maps_by_quantile and len(tracks) > 1:
        # The highest crown slice is the least contaminated by gingival bridges
        # and therefore the authoritative topology for deciding whether two
        # tracks are distinct crowns.  Lower slices remain available to track
        # persistence and later full-component boundary reconstruction.
        basin_maps = maps_by_quantile[max(maps_by_quantile)]
        basin_mask = np.asarray(basin_maps["silhouette"], dtype=bool)
        basin_components = label(basin_mask, connectivity=2)
        basin_lr = np.asarray(basin_maps["lr_centres"], dtype=float)
        basin_ap = np.asarray(basin_maps["ap_centres"], dtype=float)

        def component_at(track: CoreTrack) -> int:
            """内部算法说明。"""
            row = int(np.argmin(np.abs(basin_lr - track.center_lr_ap_mm[0])))
            column = int(np.argmin(np.abs(basin_ap - track.center_lr_ap_mm[1])))
            return int(basin_components[row, column])

        def surface_boundary_separates(
            first_center: np.ndarray,
            second_center: np.ndarray,
            first_s: float,
            second_s: float,
            component_id: int,
        ) -> bool:
            """内部算法说明。"""
            records = [
                {
                    "component_id": component_id,
                    "assignment": SimpleNamespace(
                        fdi=1,
                        center_lr_ap_mm=(
                            float(first_center[0]), float(first_center[1])
                        ),
                        s_mm=float(first_s),
                    ),
                },
                {
                    "component_id": component_id,
                    "assignment": SimpleNamespace(
                        fdi=2,
                        center_lr_ap_mm=(
                            float(second_center[0]), float(second_center[1])
                        ),
                        s_mm=float(second_s),
                    ),
                },
            ]
            _, record = _surface_valley_barrier(
                records=records,
                pair_index=0,
                maps=basin_maps,
                frame=frame,
                component=basin_components == component_id,
                minimum_mean_support=minimum_surface_valley_mean_support,
                minimum_coverage=minimum_surface_valley_coverage,
                require_independent_crown_basins=False,
            )
            return bool(record.get("accepted"))

        def surface_chord_separates(
            first_center: np.ndarray,
            second_center: np.ndarray,
            component_id: int,
            local_scale_mm: float,
        ) -> bool:
            """内部算法说明。"""
            local_maps = dict(basin_maps)
            local_maps["silhouette"] = basin_components == component_id
            seeds = [
                CrownSeed(
                    instance_id=1,
                    center_lr_ap_mm=(
                        float(first_center[0]), float(first_center[1])
                    ),
                    initial_center_lr_ap_mm=(
                        float(first_center[0]), float(first_center[1])
                    ),
                    core_pixel_count=1,
                    refinement_distance_mm=0.0,
                ),
                CrownSeed(
                    instance_id=2,
                    center_lr_ap_mm=(
                        float(second_center[0]), float(second_center[1])
                    ),
                    initial_center_lr_ap_mm=(
                        float(second_center[0]), float(second_center[1])
                    ),
                    core_pixel_count=1,
                    refinement_distance_mm=0.0,
                ),
            ]
            try:
                chords = find_shortest_concavity_chords(
                    enhanced_maps=local_maps,
                    ordered_seeds=seeds,
                    forced_gap_pair_indices=set(),
                )
            except Exception:
                return False
            for chord in chords:
                if (
                    chord.kind != "contact"
                    or chord.first_endpoint_lr_ap_mm is None
                    or chord.second_endpoint_lr_ap_mm is None
                ):
                    continue
                support = _finite_chord_3d_boundary_support(
                    first_endpoint_lr_ap_mm=chord.first_endpoint_lr_ap_mm,
                    second_endpoint_lr_ap_mm=chord.second_endpoint_lr_ap_mm,
                    first_center_lr_ap_mm=first_center,
                    second_center_lr_ap_mm=second_center,
                    maps=basin_maps,
                    component=basin_components == component_id,
                    local_scale_mm=local_scale_mm,
                )
                if bool(
                    support.get("surface_valley_contrast", {}).get(
                        "accepted"
                    )
                ):
                    return True
            return False

        for index, (first_track, second_track) in enumerate(
            zip(tracks, tracks[1:])
        ):
            component_id = component_at(first_track)
            if component_id <= 0 or component_id != component_at(second_track):
                independent_adjacent_pairs.add((index, index + 1))
                continue
            support = _independent_crown_basin_support(
                first_center_lr_ap_mm=np.asarray(
                    first_track.center_lr_ap_mm, dtype=float
                ),
                second_center_lr_ap_mm=np.asarray(
                    second_track.center_lr_ap_mm, dtype=float
                ),
                maps=basin_maps,
                component=basin_components == component_id,
                local_scale_mm=0.5 * (
                    first_track.local_scale_mm + second_track.local_scale_mm
                ),
            )
            if not bool(support.get("available")):
                continue
            first_center = np.asarray(
                first_track.center_lr_ap_mm, dtype=float
            )
            second_center = np.asarray(
                second_track.center_lr_ap_mm, dtype=float
            )
            local_scale = 0.5 * (
                first_track.local_scale_mm + second_track.local_scale_mm
            )
            if (
                bool(support.get("accepted"))
                or surface_boundary_separates(
                    first_center,
                    second_center,
                    first_track.s_mm,
                    second_track.s_mm,
                    component_id,
                )
                or surface_chord_separates(
                    first_center,
                    second_center,
                    component_id,
                    local_scale,
                )
            ):
                independent_adjacent_pairs.add((index, index + 1))
            else:
                union(index, index + 1)

        # Pairwise core comparisons are not sufficient after duplicate peaks
        # have fused: the centre of the complete physical support can reveal
        # that a third adjacent core belongs to the same crown even when the
        # last two raw peaks alone formed an apparent saddle.  Agglomerate until
        # every neighbouring group is separated by a persistent basin saddle.
        changed = True
        while changed:
            changed = False
            ordered_groups: list[list[int]] = []
            for index in range(len(tracks)):
                root = find(index)
                if not ordered_groups or find(ordered_groups[-1][0]) != root:
                    ordered_groups.append([index])
                else:
                    ordered_groups[-1].append(index)
            for first_group, second_group in zip(
                ordered_groups, ordered_groups[1:]
            ):
                def group_center(indices: list[int]) -> np.ndarray:
                    """内部算法说明。"""
                    weights = np.asarray([
                        max(_physical_crownness(tracks[index]), 0.05)
                        for index in indices
                    ])
                    centers = np.asarray([
                        tracks[index].center_lr_ap_mm for index in indices
                    ], dtype=float)
                    return np.average(centers, axis=0, weights=weights)

                first_center = group_center(first_group)
                second_center = group_center(second_group)
                first_row = int(np.argmin(np.abs(basin_lr - first_center[0])))
                first_column = int(np.argmin(np.abs(basin_ap - first_center[1])))
                second_row = int(np.argmin(np.abs(basin_lr - second_center[0])))
                second_column = int(np.argmin(np.abs(basin_ap - second_center[1])))
                component_id = int(basin_components[first_row, first_column])
                if (
                    component_id <= 0
                    or component_id
                    != int(basin_components[second_row, second_column])
                ):
                    continue
                group_scale = float(np.mean([
                    tracks[index].local_scale_mm
                    for index in first_group + second_group
                ]))
                support = _independent_crown_basin_support(
                    first_center_lr_ap_mm=first_center,
                    second_center_lr_ap_mm=second_center,
                    maps=basin_maps,
                    component=basin_components == component_id,
                    local_scale_mm=group_scale,
                )
                first_s = float(np.mean([
                    tracks[index].s_mm for index in first_group
                ]))
                second_s = float(np.mean([
                    tracks[index].s_mm for index in second_group
                ]))
                if (
                    bool(support.get("available"))
                    and not bool(support.get("accepted"))
                    and not surface_boundary_separates(
                        first_center,
                        second_center,
                        first_s,
                        second_s,
                        component_id,
                    )
                    and not surface_chord_separates(
                        first_center,
                        second_center,
                        component_id,
                        group_scale,
                    )
                ):
                    union(first_group[0], second_group[0])
                    changed = True
                    break

        independent_adjacent_pairs = {
            (index, index + 1)
            for index in range(len(tracks) - 1)
            if find(index) != find(index + 1)
        }

    basin_group_by_index = {
        index: find(index) for index in range(len(tracks))
    }
    basin_group_members: dict[int, list[int]] = {}
    for index, group_id in basin_group_by_index.items():
        basin_group_members.setdefault(group_id, []).append(index)
    single_basin_indices = {
        members[0]
        for members in basin_group_members.values()
        if len(members) == 1
    }

    depth_maps: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
    if maps_by_quantile:
        for maps in maps_by_quantile.values():
            mask = np.asarray(maps["silhouette"], dtype=bool)
            resolution = float(maps["resolution_mm"])
            depth_maps.append((
                distance_transform_edt(mask) * resolution,
                np.asarray(maps["lr_centres"], dtype=float),
                np.asarray(maps["ap_centres"], dtype=float),
            ))

    def projection_persistence(
        centers: tuple[tuple[float, float], ...], width_mm: float
    ) -> float:
        """算法说明。

Count a scale when every proposed center has physical core support.

        A crown that is merged with its neighbour at one height may lose its
        own local maximum while a deep support region remains.  This geometric
        support is the one-to-many/many-to-one relation required by the
        multi-scale model; it does not create another atomic core.
        """

        if not depth_maps:
            return 0.0
        minimum_depth = max(0.75, 0.12 * width_mm)
        supported = 0
        for depth, lr, ap in depth_maps:
            valid = True
            for center in centers:
                row = int(np.argmin(np.abs(lr - center[0])))
                column = int(np.argmin(np.abs(ap - center[1])))
                if float(depth[row, column]) < minimum_depth:
                    valid = False
                    break
            supported += int(valid)
        return float(supported / total_scale_count)

    for index, track in enumerate(tracks):
        single_centers = (track.center_lr_ap_mm,)
        # A point remaining inside a broad foreground region is not evidence
        # that it is an independent physical crown.  Earlier versions used
        # ``max(track.persistence, projection_persistence)`` here; that could
        # promote a two-scale contact/ridge fragment to a five-scale tooth and
        # was the direct source of the XJ-#45/WHL-26 duplicate markers.  Keep
        # projection support as supplementary evidence only.  Admission of a
        # single crown is controlled by the actual linked core trajectory.
        single_persistence = track.persistence
        single_projection_support = projection_persistence(
            single_centers, track.local_scale_mm
        )
        if (
            single_persistence >= minimum_single_persistence
            and index in single_basin_indices
        ):
            hypotheses.append(CrownHypothesis(
                hypothesis_id=f"single:{track.track_id}",
                kind="single",
                first_core_index=index,
                last_core_index=index,
                core_ids=(track.track_id,),
                fdi_count=1,
                centers_lr_ap_mm=single_centers,
                centers_s_mm=(track.s_mm,),
                width_mm=track.local_scale_mm,
                crownness=track.crownness,
                persistence=single_persistence,
                evidence_probability=float(np.clip(
                    _physical_crownness(track)
                    * single_persistence
                    * (0.80 + 0.20 * single_projection_support),
                    0.01,
                    0.99,
                )),
            ))
        # A split exists only after two simultaneous crown basins persist over
        # height.  Surface valleys and multiview curves validate their mutual
        # boundary later; they are not allowed to manufacture a second tooth.
        observation_s = np.asarray([item.s_mm for item in track.observations])
        observation_span = (
            float(np.ptp(observation_s)) if len(observation_s) else 0.0
        )
        broad = track.local_scale_mm >= 1.20 * median_scale
        multi_position = (
            len(observation_s) >= 2
            and observation_span >= 0.45 * median_scale
        )
        # Every stable physical support may participate in a *candidate* split
        # during global fusion reconciliation.  Broadness remains a prior on
        # its cost, never proof that the split exists.  The component-local
        # boundary stage must subsequently prove a real separator before this
        # hypothesis is allowed to survive.
        proposed_separation = max(
            0.50 * median_scale,
            observation_span,
        )
        proposed_s_values = (
            track.s_mm - 0.5 * proposed_separation,
            track.s_mm + 0.5 * proposed_separation,
        )
        curve_anchor = frame.at_s(track.s_mm)
        track_center = np.asarray(track.center_lr_ap_mm, dtype=float)
        proposed_centers = tuple(
            tuple(float(value) for value in (
                track_center + frame.at_s(s_mm) - curve_anchor
            ))
            for s_mm in proposed_s_values
        )
        subbasin_persistence, subbasin_centers = _persistent_subbasin_evidence(
            track,
            tracks,
            maps_by_quantile,
            minimum_single_persistence,
            minimum_independent_core_separation_scale,
        )
        if (
            track.relative_3d_tooth_support >= 1.0 - EPS
            and track.persistence >= minimum_single_persistence
            and subbasin_persistence >= minimum_single_persistence
        ):
            centers = subbasin_centers
            s_values = tuple(frame.project_lr_ap(np.asarray(center))[0] for center in centers)
            separation = float(abs(s_values[1] - s_values[0]))
            width_ratio = track.local_scale_mm / max(median_scale, EPS)
            # Broadness is only permissive candidate evidence.  A crown just
            # above the broad threshold must not make splitting almost free;
            # stronger width or cross-scale positional separation increases
            # the probability continuously.
            broad_score = float(np.clip((width_ratio - 1.0) / 0.75, 0.03, 0.90))
            if multi_position:
                broad_score = min(0.95, broad_score + 0.20)
            split_persistence = min(
                track.persistence,
                subbasin_persistence,
            )
            evidence = float(np.clip(
                _physical_crownness(track)
                * broad_score * split_persistence, 0.01, 0.95
            ))
            hypotheses.append(CrownHypothesis(
                hypothesis_id=f"split:{track.track_id}",
                kind="split",
                first_core_index=index,
                last_core_index=index,
                core_ids=(track.track_id,),
                fdi_count=2,
                centers_lr_ap_mm=centers,
                centers_s_mm=s_values,
                width_mm=track.local_scale_mm,
                crownness=track.crownness,
                persistence=split_persistence,
                evidence_probability=evidence,
                subbasin_persistence=subbasin_persistence,
                independent_subbasin_count=2,
            ))

    merge_sizes = sorted({
        2,
        3,
        *(len(members) for members in basin_group_members.values()
          if len(members) > 1),
    })
    for size in merge_sizes:
        for first_index in range(0, len(tracks) - size + 1):
            selected = tracks[first_index:first_index + size]
            gaps = np.diff([item.s_mm for item in selected])
            local_scale = float(np.mean([item.local_scale_mm for item in selected]))
            transverse_span = float(np.ptp([item.u_mm for item in selected]))
            selected_indices = range(first_index, first_index + size)
            same_physical_basin = len({
                basin_group_by_index[index] for index in selected_indices
            }) == 1
            if same_physical_basin:
                group_members = basin_group_members[
                    basin_group_by_index[first_index]
                ]
                if list(selected_indices) != group_members:
                    continue
            contains_independent_basin_boundary = any(
                (index, index + 1) in independent_adjacent_pairs
                for index in range(first_index, first_index + size - 1)
            )
            if contains_independent_basin_boundary:
                continue
            if (
                not same_physical_basin
                and np.max(gaps, initial=0.0) / max(local_scale, EPS) > 1.05
            ):
                continue
            if (
                not same_physical_basin
                and transverse_span / max(local_scale, EPS) > 1.05
            ):
                continue
            # A merge may absorb duplicate/fragmentary peaks belonging to one
            # crown, but it must not erase two independently persistent
            # physical cores merely to make the candidate count equal the FDI
            # count.  Two tracks supported at >= the accepted single-crown
            # persistence and separated along the arch are independent tooth
            # evidence.  This is a dimensionless, case-local topology test.
            persistent = [
                item for item in selected
                if item.persistence >= minimum_single_persistence
            ]
            independent_persistent_pair = any(
                abs(second.s_mm - first.s_mm)
                / max(0.5 * (first.local_scale_mm + second.local_scale_mm), EPS)
                >= minimum_independent_core_separation_scale
                for first_index_in_group, first in enumerate(persistent)
                for second in persistent[first_index_in_group + 1:]
            )
            if independent_persistent_pair and not same_physical_basin:
                continue
            weights = np.asarray([
                max(_physical_crownness(item), 0.05) for item in selected
            ])
            centers = np.asarray([item.center_lr_ap_mm for item in selected])
            center = np.average(centers, axis=0, weights=weights)
            s_mm = float(np.average([item.s_mm for item in selected], weights=weights))
            gap_norm = float(np.mean(gaps) / max(local_scale, EPS)) if len(gaps) else 0.0
            common_scales = set(selected[0].support_scale_indices)
            for track in selected[1:]:
                common_scales &= set(track.support_scale_indices)
            union_scales = set().union(*(
                set(item.support_scale_indices) for item in selected
            ))
            common_support = len(common_scales) / max(
                len(union_scales), 1
            )
            evidence = float(np.clip(
                np.mean([_physical_crownness(item) for item in selected])
                * math.exp(-gap_norm**2)
                * (0.70 + 0.30 * common_support),
                0.02,
                0.98,
            ))
            merge_width = float(
                selected[-1].s_mm - selected[0].s_mm
                + 0.5 * selected[0].local_scale_mm
                + 0.5 * selected[-1].local_scale_mm
            )
            # Foreground occupancy cannot promote a weak collection of peaks
            # into a physical tooth.  It only says that the proposed centre is
            # somewhere inside a broad crown/gingiva component.  A merge must
            # inherit actual cross-height core support, exactly as a single
            # hypothesis does.  The old max(..., projection_persistence) made
            # WHL-26 cores 2+3 jump from 2/5 to 5/5 and inserted a fictitious
            # tooth before the real stable core 4.
            merge_persistence = float(len(union_scales) / total_scale_count)
            if merge_persistence < minimum_single_persistence:
                continue
            evidence = float(np.clip(
                evidence
                * merge_persistence
                * (0.70 ** (size - 1)),
                0.01,
                0.98,
            ))
            hypotheses.append(CrownHypothesis(
                hypothesis_id="merge:" + "+".join(str(item.track_id) for item in selected),
                kind="merge",
                first_core_index=first_index,
                last_core_index=first_index + size - 1,
                core_ids=tuple(item.track_id for item in selected),
                fdi_count=1,
                centers_lr_ap_mm=((float(center[0]), float(center[1])),),
                centers_s_mm=(s_mm,),
                width_mm=merge_width,
                crownness=float(np.mean([
                    _physical_crownness(item) for item in selected
                ])),
                persistence=merge_persistence,
                evidence_probability=evidence,
            ))
    return hypotheses


@dataclass(frozen=True)
class _PartialPath:
    """算法说明。"""
    cost: float
    assignments: tuple[AlignmentAssignment, ...]
    artifact_core_ids: tuple[int, ...]
    consumed_core_ids: tuple[int, ...]
    last_matched_s: float | None
    last_matched_fdi: int | None
    operations: tuple[str, ...]


def _expected_s(fdi: int, jaw: str, scale: float, offset: float) -> float:
    """算法说明。"""
    return scale * signed_midline_distance_prior_mm(fdi, jaw) + offset


def _match_hypothesis(
    path: _PartialPath,
    hypothesis: CrownHypothesis,
    fdis: tuple[int, ...],
    jaw: str,
    scale: float,
    offset: float,
) -> _PartialPath:
    """算法说明。"""
    assignments = list(path.assignments)
    cost = path.cost - math.log(max(hypothesis.evidence_probability, EPS))
    expected_width = scale * sum(crown_width_prior_mm(fdi) for fdi in fdis)
    width_residual = (hypothesis.width_mm - expected_width) / max(0.45 * expected_width, 1.0)
    cost += min(width_residual**2, 9.0)
    previous_s = path.last_matched_s
    previous_fdi = path.last_matched_fdi
    for fdi, center, s_mm in zip(
        fdis, hypothesis.centers_lr_ap_mm, hypothesis.centers_s_mm, strict=True
    ):
        expected = _expected_s(fdi, jaw, scale, offset)
        tooth_width = max(scale * crown_width_prior_mm(fdi), 2.0)
        # A displacement of one crown width is a one-tooth label shift, not a
        # routine anatomical residual.  Thirty-five percent of local crown width is the
        # global robust position scale so missing-slot semantics can disambiguate
        # otherwise plausible shifted paths.
        position_residual = (s_mm - expected) / max(0.35 * tooth_width, 1.2)
        match_cost = min(position_residual**2, 16.0) - math.log(
            max(hypothesis.crownness, EPS)
        )
        if previous_s is not None and previous_fdi is not None:
            actual_spacing = s_mm - previous_s
            expected_spacing = expected - _expected_s(previous_fdi, jaw, scale, offset)
            previous_width = scale * crown_width_prior_mm(previous_fdi)
            expected_local_width = 0.5 * (tooth_width + previous_width)
            # Adjacent crown centres are much more tightly distributed than a
            # whole crown width.  Using a full-width residual scale allowed two
            # nearby peaks from one physical crown to masquerade as consecutive
            # FDI regions.  A global 30% robust scale still tolerates anatomy
            # variation while making that duplicate-core topology expensive.
            spacing_residual = (actual_spacing - expected_spacing) / max(
                0.30 * expected_local_width, 1.2
            )
            match_cost += min(spacing_residual**2, 9.0)
        cost += match_cost
        assignments.append(AlignmentAssignment(
            fdi=int(fdi),
            hypothesis_id=hypothesis.hypothesis_id,
            kind=hypothesis.kind,
            core_ids=hypothesis.core_ids,
            center_lr_ap_mm=center,
            s_mm=float(s_mm),
            persistence=hypothesis.persistence,
            match_cost=float(match_cost),
            subbasin_persistence=hypothesis.subbasin_persistence,
            independent_subbasin_count=hypothesis.independent_subbasin_count,
        ))
        previous_s = float(s_mm)
        previous_fdi = int(fdi)
    return _PartialPath(
        cost=float(cost),
        assignments=tuple(assignments),
        artifact_core_ids=path.artifact_core_ids,
        consumed_core_ids=tuple(sorted(set(path.consumed_core_ids + hypothesis.core_ids))),
        last_matched_s=previous_s,
        last_matched_fdi=previous_fdi,
        operations=path.operations + (hypothesis.hypothesis_id,),
    )


def _retain(paths: list[_PartialPath], limit: int = 24) -> list[_PartialPath]:
    """算法说明。"""
    unique: dict[tuple[str, ...], _PartialPath] = {}
    for path in paths:
        signature = path.operations
        current = unique.get(signature)
        if current is None or path.cost < current.cost:
            unique[signature] = path
    ordered = sorted(unique.values(), key=lambda item: item.cost)
    # Preserve at least one representative of each edit topology.  Pure
    # cost-only pruning previously removed all no-split complete paths before
    # component evidence could evaluate them, making a later split+merge path
    # appear unavoidable.
    representatives: list[_PartialPath] = []
    represented: set[tuple[int, int, int]] = set()
    for path in ordered:
        topology = (
            sum(item.kind == "undetected" for item in path.assignments),
            sum(operation.startswith("split:") for operation in path.operations),
            sum(operation.startswith("merge:") for operation in path.operations),
        )
        if topology in represented:
            continue
        represented.add(topology)
        representatives.append(path)
        if len(representatives) >= limit // 2:
            break
    selected = list(representatives)
    selected_ids = {id(item) for item in selected}
    selected.extend(item for item in ordered if id(item) not in selected_ids)
    return selected[:limit]


def _solve_one_configuration(
    tracks: list[CoreTrack],
    hypotheses: list[CrownHypothesis],
    present_fdis: tuple[int, ...],
    jaw: str,
    frame: ArchFrame,
    scale: float,
    offset: float,
    missing_fdis: tuple[int, ...] = (),
) -> list[AlignmentPath]:
    """算法说明。"""
    by_start: dict[int, list[CrownHypothesis]] = {}
    for hypothesis in hypotheses:
        by_start.setdefault(hypothesis.first_core_index, []).append(hypothesis)
    cells: dict[tuple[int, int], list[_PartialPath]] = {
        (0, 0): [_PartialPath(0.0, (), (), (), None, None, ())]
    }
    for core_index in range(len(tracks) + 1):
        for fdi_index in range(len(present_fdis) + 1):
            paths = cells.get((core_index, fdi_index), [])
            if not paths:
                continue
            if core_index < len(tracks):
                track = tracks[core_index]
                artifact_probability = max(
                    1.0 - _physical_crownness(track), EPS
                )
                # Missing/excluded semantics are labels on geometry, not
                # geometric evidence.  In particular, a persistent crown-like
                # core cannot become a cheap artifact only because its expected
                # position is declared missing.  Such a disagreement must
                # survive into QA as a semantic/geometry conflict.
                artifact_penalty = -math.log(max(artifact_probability, EPS))
                next_paths = cells.setdefault((core_index + 1, fdi_index), [])
                for path in paths:
                    next_paths.append(_PartialPath(
                        cost=path.cost + artifact_penalty,
                        assignments=path.assignments,
                        artifact_core_ids=path.artifact_core_ids + (track.track_id,),
                        consumed_core_ids=path.consumed_core_ids + (track.track_id,),
                        last_matched_s=path.last_matched_s,
                        last_matched_fdi=path.last_matched_fdi,
                        operations=path.operations + (f"artifact:{track.track_id}",),
                    ))
                cells[(core_index + 1, fdi_index)] = _retain(next_paths)
                for hypothesis in by_start.get(core_index, []):
                    if fdi_index + hypothesis.fdi_count > len(present_fdis):
                        continue
                    next_core = hypothesis.last_core_index + 1
                    fdis = present_fdis[fdi_index:fdi_index + hypothesis.fdi_count]
                    target = cells.setdefault((next_core, fdi_index + hypothesis.fdi_count), [])
                    target.extend(
                        _match_hypothesis(path, hypothesis, fdis, jaw, scale, offset)
                        for path in paths
                    )
                    cells[(next_core, fdi_index + hypothesis.fdi_count)] = _retain(target)
            if fdi_index < len(present_fdis):
                fdi = present_fdis[fdi_index]
                target = cells.setdefault((core_index, fdi_index + 1), [])
                for path in paths:
                    target.append(_PartialPath(
                        cost=path.cost + 12.0,
                        assignments=path.assignments + (AlignmentAssignment(
                            fdi=fdi,
                            hypothesis_id=None,
                            kind="undetected",
                            core_ids=(),
                            center_lr_ap_mm=None,
                            s_mm=None,
                            persistence=0.0,
                            match_cost=12.0,
                        ),),
                        artifact_core_ids=path.artifact_core_ids,
                        consumed_core_ids=path.consumed_core_ids,
                        last_matched_s=path.last_matched_s,
                        last_matched_fdi=path.last_matched_fdi,
                        operations=path.operations + (f"undetected:{fdi}",),
                    ))
                cells[(core_index, fdi_index + 1)] = _retain(target)

    results: list[AlignmentPath] = []
    for path in cells.get((len(tracks), len(present_fdis)), []):
        signature = tuple(
            f"{item.fdi}:{item.kind}:{','.join(map(str, item.core_ids))}"
            for item in path.assignments
        ) + tuple(f"artifact:{item}" for item in path.artifact_core_ids)
        results.append(AlignmentPath(
            orientation_name=frame.orientation_name,
            global_scale=float(scale),
            midline_offset_mm=float(offset),
            total_cost=float(path.cost),
            assignments=path.assignments,
            artifact_core_ids=tuple(sorted(path.artifact_core_ids)),
            consumed_core_ids=tuple(sorted(path.consumed_core_ids)),
            undetected_fdi=tuple(
                item.fdi for item in path.assignments if item.kind == "undetected"
            ),
            signature=signature,
        ))
    return results


def _physically_equivalent(
    first: AlignmentPath, second_path: AlignmentPath
) -> bool:
    """算法说明。 Collapse redundant-core choices that preserve every physical centre."""

    if first.orientation_name != second_path.orientation_name:
        return False
    if len(first.assignments) != len(second_path.assignments):
        return False
    for first_item, second_item in zip(
        first.assignments, second_path.assignments, strict=True
    ):
        if first_item.fdi != second_item.fdi:
            return False
        if (first_item.s_mm is None) != (second_item.s_mm is None):
            return False
        if first_item.s_mm is not None:
            equivalent_radius = 0.35 * first.global_scale * crown_width_prior_mm(
                first_item.fdi
            )
            if abs(first_item.s_mm - second_item.s_mm) > equivalent_radius:
                return False
    return True


def rank_monotone_fdi_alignments(
    candidates: list[tuple[ArchFrame, list[CoreTrack], list[CrownHypothesis]]],
    present_fdis: tuple[int, ...],
    jaw: str,
    limit: int = 48,
    missing_fdis: tuple[int, ...] = (),
    midline_offset_search_local_scale: float = 1.50,
) -> list[AlignmentPath]:
    """算法说明。 Rank physically distinct paths across orientation and global geometry."""

    all_paths: list[AlignmentPath] = []
    for frame, tracks, hypotheses in candidates:
        median_local_scale = float(np.median([
            item.local_scale_mm for item in tracks
        ])) if tracks else 8.0
        offset_limit = (
            midline_offset_search_local_scale * median_local_scale
        )
        for scale in np.linspace(0.78, 1.22, 9):
            for offset in np.linspace(-offset_limit, offset_limit, 9):
                all_paths.extend(_solve_one_configuration(
                    tracks, hypotheses, present_fdis, jaw, frame,
                    float(scale), float(offset), missing_fdis,
                )[:24])
    if not all_paths:
        raise RuntimeError("global monotone alignment produced no complete path")
    all_paths.sort(key=lambda item: item.total_cost)
    ranked: list[AlignmentPath] = []
    for path in all_paths:
        if any(_physically_equivalent(path, existing) for existing in ranked):
            continue
        ranked.append(path)
        if len(ranked) >= limit:
            break
    return ranked


def solve_monotone_fdi_alignment(
    candidates: list[tuple[ArchFrame, list[CoreTrack], list[CrownHypothesis]]],
    present_fdis: tuple[int, ...],
    jaw: str,
    missing_fdis: tuple[int, ...] = (),
    midline_offset_search_local_scale: float = 1.50,
) -> tuple[AlignmentPath, AlignmentPath | None, float]:
    """算法说明。 Compare LR reflections, global scales, offsets, and hypothesis paths."""

    ranked = rank_monotone_fdi_alignments(
        candidates,
        present_fdis,
        jaw,
        missing_fdis=missing_fdis,
        midline_offset_search_local_scale=midline_offset_search_local_scale,
    )
    best = ranked[0]
    second = ranked[1] if len(ranked) > 1 else None
    margin = 0.0 if second is None else (
        second.total_cost - best.total_cost
    ) / max(len(present_fdis), 1)
    return best, second, float(margin)

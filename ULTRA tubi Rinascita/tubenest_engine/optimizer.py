"""Geometry-aware tube nesting optimizer.

Material usage is the primary objective. Cut-start/toolpath choices are not part
of this optimizer; they are applied only after the nesting pose is frozen.

The search is a bounded beam search because unrestricted tube nesting is
combinatorial. It explores piece order, proper rigid end-for-end reversal and
legal axial rotations, then performs a rod-merge pass to reduce stock count.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from concurrent.futures import ProcessPoolExecutor
import math
import os
import time
from typing import Optional

from .fit import (
    PartPose,
    fit_adjacent_parts,
    posed_ends,
    tail_flip_eligibility,
)


EPS = 1e-7
DEFAULT_ROD_LENGTH = 6000.0
DEFAULT_CHUCK_DEAD_ZONE = 400.0
DEFAULT_GAP_MM = 2.0
DEFAULT_BEAM_WIDTH = 120
DEFAULT_MAX_CANDIDATE_TYPES = 24
EXACT_REQUIRE_ALL_MAX_PIECES = 8
# Small require-all searches used to disable beam pruning solely by piece
# count. Eight square-tube pieces can have 8 legal poses each, so an "exact"
# refinement can explode into tens of millions/billions of pose/order branches.
# Keep exact search only when a conservative branch estimate is genuinely small.
EXACT_REQUIRE_ALL_MAX_ESTIMATED_BRANCHES = 100_000
MERGE_PAIR_ATTEMPT_LIMIT = 160
PARALLEL_MIN_ITEMS = 8
PARALLEL_MIN_UNIQUE_TYPES = 6
# Windows spawn duplicates the item geometry and fit cache in every worker.
# Keep very large/diverse jobs in one shared process instead of multiplying
# their RAM footprint across the whole CPU.
PARALLEL_MAX_ITEMS = 96
PARALLEL_MAX_UNIQUE_TYPES = 48
CONTOUR_MAX_CPU_WORKERS = 12
CONTOUR_MEMORY_SAFE_CPU_WORKERS = 6
PAIRWISE_PRECOMPUTE_MAX_TRANSITIONS = 100_000
CONTOUR_LARGE_JOB_MIN_ITEMS = 24
LARGE_SEARCH_MIN_ITEMS = 32
LARGE_SEARCH_MIN_UNIQUE_TYPES = 8
CONTOUR_INITIAL_BEAM_WIDTH = 48
CONTOUR_INITIAL_MAX_CANDIDATE_TYPES = 18
CONTOUR_MERGE_PAIR_ATTEMPT_LIMIT = 32
CONTOUR_MERGE_BEAM_WIDTH = 72
CONTOUR_MERGE_MAX_CANDIDATE_TYPES = 24
CONTOUR_REFINE_BEAM_WIDTH = 72
CONTOUR_REFINE_MAX_CANDIDATE_TYPES = 24
CONTOUR_REFINE_UTILIZATION_THRESHOLD = 0.90
MAX_CPU_WORKERS = 24

# Optional second algorithm: fast best-fit insertion.
#
# It deliberately avoids the combinatorial beam/global-compaction search.
# Pieces are inserted into every useful position of existing rod sequences and
# only the poses for that fixed order are solved. This keeps the search close to
# polynomial while still allowing a small piece to be inserted before the last
# piece so that the last piece can legally extend into the 400 mm chuck zone.
FAST_BEST_FIT_MAX_INSERTION_POSITIONS = 4
FAST_BEST_FIT_CONSOLIDATION_PASSES = 1
FAST_BEST_FIT_DONOR_MAX_PIECES = 10
FAST_BEST_FIT_DONOR_ATTEMPTS = 48
FAST_BEST_FIT_TIME_BUDGET_SECONDS = 12.0
FAST_CHAIN_OVERLAP_EPS_MM = 0.05
FAST_CHAIN_ANGLE_EPS_DEGREES = 1.0

_PAIRWISE_FIT_CACHE = {}
_PAIRWISE_FIT_ERROR_TAG = "__FIT_ERROR__"
_PAIRWISE_FIT_CACHE_LIMIT = 200000
_PAIRWISE_FIT_STATS = {
    "hits": 0,
    "misses": 0,
}


_PARALLEL_STATS = {
    "pool_runs": 0,
    "initial_pool_runs": 0,
    "mask_pool_runs": 0,
    "tasks": 0,
    "fallbacks": 0,
    "last_error": "",
    "item_count": 0,
    "workers_used": 1,
    "used_process_pool": False,
    "initial_seconds": 0.0,
    "merge_seconds": 0.0,
    "refine_seconds": 0.0,
    "total_seconds": 0.0,
    "precompute_seconds": 0.0,
    "precomputed_transitions": 0,
    "precomputed_failures": 0,
}


def _reset_pairwise_fit_stats():
    _PAIRWISE_FIT_STATS["hits"] = 0
    _PAIRWISE_FIT_STATS["misses"] = 0


def _reset_parallel_stats():
    _PARALLEL_STATS.update(
        pool_runs=0,
        initial_pool_runs=0,
        mask_pool_runs=0,
        tasks=0,
        fallbacks=0,
        last_error="",
        item_count=0,
        workers_used=1,
        used_process_pool=False,
        initial_seconds=0.0,
        merge_seconds=0.0,
        refine_seconds=0.0,
        total_seconds=0.0,
        precompute_seconds=0.0,
        precomputed_transitions=0,
        precomputed_failures=0,
        precompute_estimated_transitions=0,
        precompute_skipped_memory_guard=False,
        unique_type_count=0,
        strategy="serial_small_job",
        algorithm="current",
        fast_best_fit_seconds=0.0,
        fast_best_fit_insertion_attempts=0,
        fast_best_fit_consolidation_attempts=0,
        fast_best_fit_rods=0,
    )


def get_parallel_stats():
    result = dict(_PARALLEL_STATS)
    result["configured_workers"] = nesting_cpu_worker_count()
    result["parallel_min_items"] = PARALLEL_MIN_ITEMS
    result["parallel_min_unique_types"] = PARALLEL_MIN_UNIQUE_TYPES
    result["pairwise_cache_size"] = len(_PAIRWISE_FIT_CACHE)
    result["pairwise_cache_hits"] = _PAIRWISE_FIT_STATS["hits"]
    result["pairwise_cache_misses"] = _PAIRWISE_FIT_STATS["misses"]
    return result



def _part_fit_key(part):
    if not isinstance(part, dict):
        return None
    fingerprint = part.get("part_fingerprint")
    if fingerprint:
        return ("fp", fingerprint)

    profile = part.get("profile") or {}
    ends = part.get("ends") or []
    end_planes = []
    for end in ends[:2]:
        plane = end.get("plane_z_equals_c_plus_ax_plus_by") if isinstance(end, dict) else None
        end_planes.append(tuple(round(float(v), 8) for v in plane) if plane else None)
    return (
        "geom",
        round(float(part.get("overall_length") or 0.0), 8),
        profile.get("kind"),
        round(float(profile.get("outside_width") or 0.0), 8),
        round(float(profile.get("outside_height") or 0.0), 8),
        round(float(profile.get("outside_diameter") or 0.0), 8),
        tuple(end_planes),
    )


def _pairwise_fit_key(previous_item, previous_pose, item, pose, gap_mm):
    return (
        _part_fit_key(previous_item.tube_part),
        round(_normalize_angle(previous_pose.axial_rotation_degrees), 6),
        bool(previous_pose.reversed_end_for_end),
        _part_fit_key(item.tube_part),
        round(_normalize_angle(pose.axial_rotation_degrees), 6),
        bool(pose.reversed_end_for_end),
        round(float(gap_mm), 6),
    )


def _store_pairwise_cache_value(key, value):
    if len(_PAIRWISE_FIT_CACHE) >= _PAIRWISE_FIT_CACHE_LIMIT:
        _PAIRWISE_FIT_CACHE.pop(next(iter(_PAIRWISE_FIT_CACHE)))
    _PAIRWISE_FIT_CACHE[key] = value


def _cached_pairwise_fit(previous_item, previous_pose, item, pose, gap_mm):
    key = _pairwise_fit_key(previous_item, previous_pose, item, pose, gap_mm)
    if key in _PAIRWISE_FIT_CACHE:
        _PAIRWISE_FIT_STATS["hits"] += 1
        cached = _PAIRWISE_FIT_CACHE[key]
        if (
            isinstance(cached, tuple)
            and cached
            and cached[0] == _PAIRWISE_FIT_ERROR_TAG
        ):
            raise ValueError(cached[1])
        return cached

    _PAIRWISE_FIT_STATS["misses"] += 1
    try:
        fit = fit_adjacent_parts(
            previous_item.tube_part,
            previous_pose,
            0.0,
            item.tube_part,
            pose,
            gap_mm=gap_mm,
            allow_common_line=True,
        )
    except (ValueError, TypeError) as exc:
        cached = (_PAIRWISE_FIT_ERROR_TAG, str(exc))
        _store_pairwise_cache_value(key, cached)
        raise

    cached = (
        float(fit.next_origin),
        float(fit.minimum_clearance_mm),
        bool(fit.common_line),
        float(fit.slope_delta),
        float(fit.overlap_of_axial_envelopes_mm),
    )
    _store_pairwise_cache_value(key, cached)
    return cached



@dataclass
class OptimizerItem:
    index: int
    instance_key: str
    source_id: str
    nominal_length: float
    tube_part: Optional[dict] = None
    payload: dict = field(default_factory=dict)

    @property
    def geometry_available(self):
        return _has_fit_geometry(self.tube_part)

    @property
    def physical_length(self):
        if self.geometry_available:
            value = float(self.tube_part.get("overall_length") or 0.0)
            if value > 0:
                return value
        return float(self.nominal_length)

    @property
    def type_key(self):
        part = self.tube_part or {}
        return (
            self.source_id,
            round(float(self.nominal_length), 6),
            part.get("part_fingerprint"),
        )


@dataclass
class PlacedItem:
    item_index: int
    pose: PartPose
    origin: float
    common_line_before: bool = False
    gap_before_mm: float = 0.0
    overlap_before_mm: float = 0.0
    requires_tail_flip: bool = False
    geometry_fallback_before: Optional[str] = None


@dataclass
class RodSearchState:
    used_mask: int = 0
    placed: tuple = ()
    used_span: float = 0.0
    nominal_packed: float = 0.0
    common_lines: int = 0
    rectangle_family: Optional[int] = None
    tail_used: bool = False

    @property
    def piece_count(self):
        return len(self.placed)


def _normalize_angle(value):
    result = float(value) % 360.0
    if abs(result - 360.0) <= EPS or abs(result) <= EPS:
        return 0.0
    return result


def _has_fit_geometry(part):
    if not isinstance(part, dict):
        return False
    ends = list(part.get("ends") or [])
    if len(ends) < 2:
        return False
    for end in ends[:2]:
        if not isinstance(end, dict):
            return False
        plane = end.get("plane_z_equals_c_plus_ax_plus_by")
        plane_ok = bool(
            plane
            and len(plane) == 3
            and all(math.isfinite(float(value)) for value in plane)
        )
        envelope = end.get("perimeter_envelope")
        envelope_ok = bool(
            isinstance(envelope, list)
            and len(envelope) >= 32
            and all(
                isinstance(pair, (list, tuple))
                and len(pair) == 2
                and all(math.isfinite(float(value)) for value in pair)
                for pair in envelope
            )
        )
        if not plane_ok and not envelope_ok:
            return False
    return True


def _profile_kind(item):
    if not item.geometry_available:
        return ""
    return str((item.tube_part.get("profile") or {}).get("kind") or "")


def _rectangle_family(rotation):
    # Legal rectangle rotations are multiples of 90. Family 0 keeps width/height
    # axes; family 90 swaps them. Every piece on a rod must use the same family.
    quarter = int(round(_normalize_angle(rotation) / 90.0)) % 4
    return 0 if quarter % 2 == 0 else 90


def _dedupe_poses(poses):
    result = []
    seen = set()
    for pose in poses:
        key = (round(_normalize_angle(pose.axial_rotation_degrees), 6), bool(pose.reversed_end_for_end))
        if key in seen:
            continue
        seen.add(key)
        result.append(
            PartPose(
                axial_rotation_degrees=key[0],
                reversed_end_for_end=key[1],
            )
        )
    return result


def _first_pose_candidates(item):
    if not item.geometry_available:
        return [PartPose()]

    kind = _profile_kind(item)
    poses = []
    if kind == "Square":
        rotations = (0.0, 90.0, 180.0, 270.0)
    elif kind == "Rect":
        # The first rectangle establishes the orientation family of the rod.
        rotations = (0.0, 90.0, 180.0, 270.0)
    elif kind == "Circle":
        # Whole-rod axial orientation is arbitrary for round stock, so 0° is
        # sufficient for the first part. Following parts rotate relative to it.
        rotations = (0.0,)
    else:
        rotations = (0.0,)

    for reversed_flag in (False, True):
        for rotation in rotations:
            poses.append(PartPose(rotation, reversed_flag))
    return _dedupe_poses(poses)


def _next_pose_candidates(previous_item, previous_pose, item, rectangle_family):
    if not item.geometry_available:
        return [PartPose()]

    kind = _profile_kind(item)
    poses = []

    if kind == "Square":
        rotations = (0.0, 90.0, 180.0, 270.0)
        for reversed_flag in (False, True):
            for rotation in rotations:
                poses.append(PartPose(rotation, reversed_flag))
        return _dedupe_poses(poses)

    if kind == "Rect":
        base = 0.0 if rectangle_family in (None, 0) else 90.0
        rotations = (base, base + 180.0)
        for reversed_flag in (False, True):
            for rotation in rotations:
                poses.append(PartPose(rotation, reversed_flag))
        return _dedupe_poses(poses)

    if kind == "Circle" and previous_item.geometry_available:
        _, previous_end = posed_ends(previous_item.tube_part, previous_pose)
        for reversed_flag in (False, True):
            next_start, _ = posed_ends(item.tube_part, PartPose(0.0, reversed_flag))
            rotation = 0.0
            if previous_end is not None and next_start is not None:
                p_mag = math.hypot(previous_end.slope_x, previous_end.slope_vertical)
                n_mag = math.hypot(next_start.slope_x, next_start.slope_vertical)
                if p_mag > EPS and n_mag > EPS:
                    p_angle = math.atan2(previous_end.slope_vertical, previous_end.slope_x)
                    n_angle = math.atan2(next_start.slope_vertical, next_start.slope_x)
                    rotation = math.degrees(p_angle - n_angle)
            poses.append(PartPose(_normalize_angle(rotation), reversed_flag))
        return _dedupe_poses(poses)

    for reversed_flag in (False, True):
        poses.append(PartPose(0.0, reversed_flag))
    return _dedupe_poses(poses)


def _tail_is_usable(item, pose, origin, accessible_limit, dead_zone_mm):
    if not item.geometry_available:
        return False

    check = tail_flip_eligibility(item.tube_part, pose, dead_zone_mm=dead_zone_mm)
    if not check.get("eligible"):
        return False

    start, _ = posed_ends(item.tube_part, pose)
    if start is None or not start.is_straight:
        return False

    # The first clean cut must itself still be reachable before the chuck zone.
    first_cut_position = float(origin) + float(start.c)
    return first_cut_position <= float(accessible_limit) + EPS


def _normalize_items(items, rod_length):
    normalized = []
    for index, item in enumerate(items or []):
        if isinstance(item, OptimizerItem):
            result = item
        else:
            length_value = item.get("length")
            if length_value is None:
                length_value = item.get("nominal_length")
            result = OptimizerItem(
                index=index,
                instance_key=str(item.get("instanceKey") or item.get("instance_key") or item.get("id") or index),
                source_id=str(item.get("sourceId") or item.get("source_id") or item.get("id") or ""),
                nominal_length=float(length_value),
                tube_part=item.get("tubePart") or item.get("tube_part"),
                payload={k: v for k, v in item.items() if k not in {
                    "instanceKey", "instance_key", "sourceId", "source_id",
                    "id", "length", "nominal_length", "tubePart", "tube_part",
                }},
            )

        if result.nominal_length <= 0:
            raise ValueError(f"Piece {result.instance_key} has non-positive length")
        if result.physical_length > float(rod_length) + EPS:
            raise ValueError(
                f"Piece {result.instance_key} physical length {result.physical_length:g} "
                f"exceeds rod length {float(rod_length):g}"
            )
        normalized.append(result)
    return normalized


def _representative_indices(items, allowed_mask, used_mask, max_types):
    representatives = {}
    for item in items:
        bit = 1 << item.index
        if not (allowed_mask & bit) or (used_mask & bit):
            continue
        representatives.setdefault(item.type_key, item.index)

    values = list(representatives.values())
    if len(values) <= max_types:
        return values

    # Preserve the longest candidates first, but ensure deterministic behavior.
    values.sort(key=lambda idx: (-items[idx].nominal_length, items[idx].instance_key))
    return values[:max_types]


def _pose_branch_estimate(item, *, first):
    if not item.geometry_available:
        return 1
    kind = _profile_kind(item)
    if kind == "Square":
        return 8
    if kind == "Rect":
        return 8 if first else 4
    if kind == "Circle":
        return 2
    return 2


def _estimate_exact_require_all_branches(items, target_mask):
    selected = [
        item
        for item in items
        if int(target_mask) & (1 << item.index)
    ]
    piece_count = len(selected)
    if piece_count <= 1:
        return 1

    counts = {}
    representatives = {}
    for item in selected:
        counts[item.type_key] = counts.get(item.type_key, 0) + 1
        representatives.setdefault(item.type_key, item)

    unique_orders = math.factorial(piece_count)
    for count in counts.values():
        unique_orders //= math.factorial(count)

    first_pose_branches = max(
        _pose_branch_estimate(item, first=True)
        for item in representatives.values()
    )
    later_pose_branches = max(
        _pose_branch_estimate(item, first=False)
        for item in representatives.values()
    )
    return (
        unique_orders
        * first_pose_branches
        * (later_pose_branches ** max(0, piece_count - 1))
    )


def _use_exact_require_all_search(items, target_mask):
    piece_count = int(target_mask).bit_count()
    if piece_count > EXACT_REQUIRE_ALL_MAX_PIECES:
        return False
    return (
        _estimate_exact_require_all_branches(items, target_mask)
        <= EXACT_REQUIRE_ALL_MAX_ESTIMATED_BRANCHES
    )


def _state_dedupe_key(state):
    if not state.placed:
        return (state.used_mask, None, None, None, state.rectangle_family, state.tail_used)
    last = state.placed[-1]
    return (
        state.used_mask,
        last.item_index,
        round(_normalize_angle(last.pose.axial_rotation_degrees), 5),
        bool(last.pose.reversed_end_for_end),
        round(float(last.origin), 5),
        state.rectangle_family,
        state.tail_used,
    )


def _partial_score(state):
    # Beam pruning: favor states that have packed more nominal material, then
    # tighter physical span, then common lines. Rod count is handled globally.
    return (
        -round(state.nominal_packed, 6),
        round(state.used_span, 6),
        -state.common_lines,
        state.tail_used,
        -state.piece_count,
    )


def _terminal_score(state, require_all):
    if require_all:
        # Membership is fixed: minimize material span before common-line count.
        return (
            round(state.used_span, 6),
            -state.common_lines,
            state.tail_used,
        )
    # While filling a rod, pack as much demand as possible first.
    return (
        -round(state.nominal_packed, 6),
        round(state.used_span, 6),
        -state.common_lines,
        state.tail_used,
        -state.piece_count,
    )


def _bounded_state_map_add(
    best_by_key,
    state,
    beam_width,
    *,
    exact_mode=False,
    buffer_factor=3,
):
    """Keep one best state per dedupe key without retaining a whole layer.

    Beam search only needs its best bounded frontier. The old implementation
    first materialized every candidate in a layer and pruned afterwards; a
    wide square-tube job could therefore create hundreds of thousands of
    RodSearchState objects at once. Periodic prefix pruning keeps RAM bounded.
    """
    key = _state_dedupe_key(state)
    previous = best_by_key.get(key)
    if (
        previous is None
        or _partial_score(state) < _partial_score(previous)
    ):
        best_by_key[key] = state

    if exact_mode:
        return

    beam_width = max(1, int(beam_width))
    hard_limit = max(
        beam_width * max(2, int(buffer_factor)),
        beam_width + 32,
    )
    if len(best_by_key) <= hard_limit:
        return

    keep = max(
        beam_width * 2,
        beam_width + 16,
    )
    ranked = sorted(
        best_by_key.values(),
        key=_partial_score,
    )[:keep]
    best_by_key.clear()
    for candidate in ranked:
        best_by_key[_state_dedupe_key(candidate)] = candidate


def _append_candidate(
    state,
    item,
    pose,
    items,
    rod_length,
    accessible_limit,
    dead_zone_mm,
    gap_mm,
):
    new_family = state.rectangle_family
    if _profile_kind(item) == "Rect":
        candidate_family = _rectangle_family(pose.axial_rotation_degrees)
        if new_family is None:
            new_family = candidate_family
        elif candidate_family != new_family:
            return None

    geometry_fallback_before = None
    if not state.placed:
        origin = 0.0
        common_line = False
        actual_gap = 0.0
        overlap = 0.0
    else:
        previous_placed = state.placed[-1]
        previous_item = items[previous_placed.item_index]

        if previous_item.geometry_available and item.geometry_available:
            try:
                (
                    relative_origin,
                    actual_gap,
                    common_line,
                    _slope_delta,
                    overlap,
                ) = _cached_pairwise_fit(
                    previous_item,
                    previous_placed.pose,
                    item,
                    pose,
                    gap_mm,
                )
                origin = float(previous_placed.origin) + float(relative_origin)
            except (ValueError, TypeError) as exc:
                # A geometry-fit failure must not make two otherwise valid
                # pieces impossible to place on the same stock. Fall back to
                # the conservative axial envelope plus configured gap. This
                # disables interlocking/common-line only for this boundary.
                origin = float(state.used_span) + float(gap_mm)
                common_line = False
                actual_gap = float(gap_mm)
                overlap = 0.0
                geometry_fallback_before = str(exc)
        else:
            # Geometry-less legacy arrays remain opaque. Keep a conservative
            # configured gap and never claim a common line.
            origin = float(state.used_span) + float(gap_mm)
            common_line = False
            actual_gap = float(gap_mm)
            overlap = 0.0
            geometry_fallback_before = "geometry unavailable"

    if origin < -EPS:
        return None

    physical_end = origin + item.physical_length
    used_span = max(float(state.used_span), physical_end)
    if used_span > float(rod_length) + EPS:
        return None

    tail_used = False
    requires_tail_flip = False
    if used_span > float(accessible_limit) + EPS:
        # The only legal way into the chuck zone is for the just-added final
        # piece to qualify for the deliberate flip strategy.
        if not _tail_is_usable(
            item,
            pose,
            origin,
            accessible_limit=accessible_limit,
            dead_zone_mm=dead_zone_mm,
        ):
            return None
        tail_used = True
        requires_tail_flip = True

    placed = PlacedItem(
        item_index=item.index,
        pose=pose,
        origin=origin,
        common_line_before=common_line,
        gap_before_mm=actual_gap,
        overlap_before_mm=overlap,
        requires_tail_flip=requires_tail_flip,
        geometry_fallback_before=geometry_fallback_before,
    )

    return RodSearchState(
        used_mask=state.used_mask | (1 << item.index),
        placed=state.placed + (placed,),
        used_span=used_span,
        nominal_packed=state.nominal_packed + item.nominal_length,
        common_lines=state.common_lines + (1 if common_line else 0),
        rectangle_family=new_family,
        tail_used=tail_used,
    )


def _search_single_rod(
    items,
    allowed_mask,
    rod_length,
    dead_zone_mm,
    gap_mm,
    require_all=False,
    beam_width=DEFAULT_BEAM_WIDTH,
    max_candidate_types=DEFAULT_MAX_CANDIDATE_TYPES,
):
    accessible_limit = float(rod_length) - float(dead_zone_mm)
    initial = RodSearchState()
    beam = [initial]
    terminals = []
    target_mask = int(allowed_mask)
    exact_mode = bool(
        require_all
        and _use_exact_require_all_search(items, target_mask)
    )

    for _depth in range(len(items) + 1):
        best_by_key = {}

        for state in beam:
            if state.used_mask:
                terminals.append(state)
                if require_all and state.used_mask == target_mask:
                    continue
            if state.tail_used:
                continue

            reps = _representative_indices(
                items,
                allowed_mask=allowed_mask,
                used_mask=state.used_mask,
                max_types=max_candidate_types,
            )
            if not reps:
                continue

            for item_index in reps:
                item = items[item_index]
                if not state.placed:
                    poses = _first_pose_candidates(item)
                else:
                    previous = state.placed[-1]
                    previous_item = items[previous.item_index]
                    poses = _next_pose_candidates(
                        previous_item,
                        previous.pose,
                        item,
                        state.rectangle_family,
                    )

                for pose in poses:
                    candidate = _append_candidate(
                        state,
                        item,
                        pose,
                        items,
                        rod_length=rod_length,
                        accessible_limit=accessible_limit,
                        dead_zone_mm=dead_zone_mm,
                        gap_mm=gap_mm,
                    )
                    if candidate is not None:
                        _bounded_state_map_add(
                            best_by_key,
                            candidate,
                            beam_width,
                            exact_mode=exact_mode,
                        )

        if not best_by_key:
            break

        ordered_states = sorted(
            best_by_key.values(),
            key=_partial_score,
        )
        beam = (
            ordered_states
            if exact_mode
            else ordered_states[:beam_width]
        )

    terminals.extend(beam)

    if require_all:
        terminals = [state for state in terminals if state.used_mask == target_mask]
    else:
        terminals = [state for state in terminals if state.used_mask]

    if not terminals:
        return None
    return min(terminals, key=lambda state: _terminal_score(state, require_all))


def _build_initial_rods(
    items,
    rod_length,
    dead_zone_mm,
    gap_mm,
    executor=None,
    beam_width=DEFAULT_BEAM_WIDTH,
    max_candidate_types=DEFAULT_MAX_CANDIDATE_TYPES,
):
    all_mask = (1 << len(items)) - 1
    remaining_mask = all_mask
    rods = []

    while remaining_mask:
        state = _search_single_rod_parallel(
            items,
            allowed_mask=remaining_mask,
            rod_length=rod_length,
            dead_zone_mm=dead_zone_mm,
            gap_mm=gap_mm,
            require_all=False,
            beam_width=int(beam_width),
            max_candidate_types=int(max_candidate_types),
            executor=executor,
        )
        if state is None or state.used_mask == 0:
            missing = [item.instance_key for item in items if remaining_mask & (1 << item.index)]
            raise ValueError(
                "No machine-safe nesting found for remaining pieces: "
                + ", ".join(missing[:5])
            )

        rods.append(state)
        remaining_mask &= ~state.used_mask

    return rods


_PROCESS_ITEMS = None
_PROCESS_ROD_LENGTH = None
_PROCESS_DEAD_ZONE_MM = None
_PROCESS_GAP_MM = None


def nesting_cpu_worker_count():
    """Use most logical CPUs while leaving a little headroom for Windows/UI."""
    logical = max(1, int(os.cpu_count() or 1))
    if logical <= 2:
        return 1
    return max(2, min(MAX_CPU_WORKERS, logical - 2))


def _unique_type_count(items):
    return len({item.type_key for item in items})


def _has_contour_fit_geometry(item):
    part = item.tube_part if isinstance(item, OptimizerItem) else item
    if not isinstance(part, dict):
        return False
    for end in list(part.get("ends") or [])[:2]:
        if not isinstance(end, dict):
            continue
        residual = end.get("plane_max_residual_mm")
        envelope = end.get("perimeter_envelope")
        try:
            nonplanar = residual is None or float(residual) > 0.05
        except (TypeError, ValueError):
            nonplanar = True
        if (
            nonplanar
            and isinstance(envelope, list)
            and len(envelope) >= 32
        ):
            return True
    return False


def _contains_contour_fit_geometry(items):
    return any(_has_contour_fit_geometry(item) for item in items)


def _representative_items_by_type(items):
    representatives = {}
    for item in items:
        representatives.setdefault(item.type_key, item)
    return list(representatives.values())


def _precompute_pairwise_catalog(items, gap_mm):
    """Build a bounded legal type/pose catalog in the parent process."""
    representatives = _representative_items_by_type(items)
    started = time.perf_counter()

    next_pose_total = sum(
        _pose_branch_estimate(item, first=False)
        for item in representatives
    )
    estimated_transitions = sum(
        _pose_branch_estimate(item, first=True)
        * next_pose_total
        for item in representatives
    )
    _PARALLEL_STATS["precompute_estimated_transitions"] = int(
        estimated_transitions
    )

    if estimated_transitions > PAIRWISE_PRECOMPUTE_MAX_TRANSITIONS:
        _PARALLEL_STATS["precompute_seconds"] = (
            time.perf_counter() - started
        )
        _PARALLEL_STATS["precomputed_transitions"] = 0
        _PARALLEL_STATS["precomputed_failures"] = 0
        _PARALLEL_STATS["precompute_skipped_memory_guard"] = True
        print(
            "[TubeNest] pairwise precompute skipped: "
            f"estimated={estimated_transitions}, "
            f"limit={PAIRWISE_PRECOMPUTE_MAX_TRANSITIONS}"
        )
        return {}

    catalog_keys = set()
    failures = 0

    for previous_item in representatives:
        for previous_pose in _first_pose_candidates(previous_item):
            rectangle_family = (
                _rectangle_family(previous_pose.axial_rotation_degrees)
                if _profile_kind(previous_item) == "Rect"
                else None
            )
            for item in representatives:
                poses = _next_pose_candidates(
                    previous_item,
                    previous_pose,
                    item,
                    rectangle_family,
                )
                for pose in poses:
                    key = _pairwise_fit_key(
                        previous_item,
                        previous_pose,
                        item,
                        pose,
                        gap_mm,
                    )
                    catalog_keys.add(key)
                    if key in _PAIRWISE_FIT_CACHE:
                        continue
                    try:
                        _cached_pairwise_fit(
                            previous_item,
                            previous_pose,
                            item,
                            pose,
                            gap_mm,
                        )
                    except (ValueError, TypeError):
                        failures += 1

    catalog = {
        key: _PAIRWISE_FIT_CACHE[key]
        for key in catalog_keys
        if key in _PAIRWISE_FIT_CACHE
    }
    elapsed = time.perf_counter() - started
    _PARALLEL_STATS["precompute_seconds"] = elapsed
    _PARALLEL_STATS["precomputed_transitions"] = len(catalog)
    _PARALLEL_STATS["precomputed_failures"] = sum(
        1
        for value in catalog.values()
        if (
            isinstance(value, tuple)
            and value
            and value[0] == _PAIRWISE_FIT_ERROR_TAG
        )
    )
    return catalog


def _should_use_process_pool(items):
    """Parallelize diverse jobs; keep repeat-heavy jobs in one shared cache.

    Windows process IPC and per-process fit caches are expensive when dozens of
    physical pieces come from only a few distinct drawings. Those jobs are
    faster in-process because each unique geometry/pose transition is solved
    once and then reused.
    """
    workers = nesting_cpu_worker_count()
    item_count = len(items)
    unique_types = _unique_type_count(items)

    return bool(
        workers > 1
        and item_count >= PARALLEL_MIN_ITEMS
        and unique_types >= PARALLEL_MIN_UNIQUE_TYPES
        and item_count <= PARALLEL_MAX_ITEMS
        and unique_types <= PARALLEL_MAX_UNIQUE_TYPES
    )


def _init_search_worker(
    items,
    rod_length,
    dead_zone_mm,
    gap_mm,
    seeded_pairwise_cache=None,
):
    global _PROCESS_ITEMS
    global _PROCESS_ROD_LENGTH
    global _PROCESS_DEAD_ZONE_MM
    global _PROCESS_GAP_MM
    global _PAIRWISE_FIT_CACHE
    _PROCESS_ITEMS = items
    _PROCESS_ROD_LENGTH = float(rod_length)
    _PROCESS_DEAD_ZONE_MM = float(dead_zone_mm)
    _PROCESS_GAP_MM = float(gap_mm)
    _PAIRWISE_FIT_CACHE = dict(seeded_pairwise_cache or {})


def _expand_state_worker(task):
    state, allowed_mask, max_candidate_types = task
    if state.tail_used:
        return []

    reps = _representative_indices(
        _PROCESS_ITEMS,
        allowed_mask=int(allowed_mask),
        used_mask=state.used_mask,
        max_types=int(max_candidate_types),
    )
    if not reps:
        return []

    candidates = []
    accessible_limit = _PROCESS_ROD_LENGTH - _PROCESS_DEAD_ZONE_MM
    for item_index in reps:
        item = _PROCESS_ITEMS[item_index]
        if not state.placed:
            poses = _first_pose_candidates(item)
        else:
            previous = state.placed[-1]
            previous_item = _PROCESS_ITEMS[previous.item_index]
            poses = _next_pose_candidates(
                previous_item,
                previous.pose,
                item,
                state.rectangle_family,
            )

        for pose in poses:
            candidate = _append_candidate(
                state,
                item,
                pose,
                _PROCESS_ITEMS,
                rod_length=_PROCESS_ROD_LENGTH,
                accessible_limit=accessible_limit,
                dead_zone_mm=_PROCESS_DEAD_ZONE_MM,
                gap_mm=_PROCESS_GAP_MM,
            )
            if candidate is not None:
                candidates.append(candidate)
    return candidates


def _expand_state_chunk_worker(task):
    (
        states,
        allowed_mask,
        max_candidate_types,
        return_limit,
    ) = task

    # Exact searches are already capped by the branch estimator and must not
    # prune. Normal beam searches keep only a bounded local frontier before
    # sending results through Windows multiprocessing pipes.
    if return_limit is None:
        result = []
        for state in states:
            result.extend(
                _expand_state_worker(
                    (state, allowed_mask, max_candidate_types)
                )
            )
        return result

    best_by_key = {}
    for state in states:
        for candidate in _expand_state_worker(
            (state, allowed_mask, max_candidate_types)
        ):
            _bounded_state_map_add(
                best_by_key,
                candidate,
                int(return_limit),
                exact_mode=False,
            )

    return sorted(
        best_by_key.values(),
        key=_partial_score,
    )[:int(return_limit)]


def _search_mask_worker(task):
    mask, beam_width, max_candidate_types = task
    return _search_single_rod(
        _PROCESS_ITEMS,
        allowed_mask=int(mask),
        rod_length=_PROCESS_ROD_LENGTH,
        dead_zone_mm=_PROCESS_DEAD_ZONE_MM,
        gap_mm=_PROCESS_GAP_MM,
        require_all=True,
        beam_width=int(beam_width),
        max_candidate_types=int(max_candidate_types),
    )


def _parallel_search_masks(
    items,
    tasks,
    rod_length,
    dead_zone_mm,
    gap_mm,
    executor=None,
):
    tasks = list(tasks)
    workers = nesting_cpu_worker_count()
    if (
        not _should_use_process_pool(items)
        or len(tasks) < 2
    ):
        return [
            _search_single_rod(
                items,
                allowed_mask=int(mask),
                rod_length=rod_length,
                dead_zone_mm=dead_zone_mm,
                gap_mm=gap_mm,
                require_all=True,
                beam_width=int(beam_width),
                max_candidate_types=int(max_types),
            )
            for mask, beam_width, max_types in tasks
        ]

    own_executor = executor is None
    local_executor = executor
    try:
        if own_executor:
            _PARALLEL_STATS["pool_runs"] += 1
            local_executor = ProcessPoolExecutor(
                max_workers=min(workers, len(tasks)),
                initializer=_init_search_worker,
                initargs=(
                    items,
                    rod_length,
                    dead_zone_mm,
                    gap_mm,
                    dict(_PAIRWISE_FIT_CACHE),
                ),
            )

        _PARALLEL_STATS["mask_pool_runs"] += 1
        _PARALLEL_STATS["tasks"] += len(tasks)
        return list(
            local_executor.map(
                _search_mask_worker,
                tasks,
                chunksize=1,
            )
        )
    except Exception as exc:
        _PARALLEL_STATS["fallbacks"] += 1
        _PARALLEL_STATS["last_error"] = f"{type(exc).__name__}: {exc}"
        print(
            "[TubeNest] multiprocessing mask search failed; "
            f"falling back to serial: {_PARALLEL_STATS['last_error']}"
        )
        return [
            _search_single_rod(
                items,
                allowed_mask=int(mask),
                rod_length=rod_length,
                dead_zone_mm=dead_zone_mm,
                gap_mm=gap_mm,
                require_all=True,
                beam_width=int(beam_width),
                max_candidate_types=int(max_types),
            )
            for mask, beam_width, max_types in tasks
        ]
    finally:
        if own_executor and local_executor is not None:
            local_executor.shutdown(wait=True)


def _search_single_rod_parallel(
    items,
    allowed_mask,
    rod_length,
    dead_zone_mm,
    gap_mm,
    require_all=False,
    beam_width=DEFAULT_BEAM_WIDTH,
    max_candidate_types=DEFAULT_MAX_CANDIDATE_TYPES,
    executor=None,
):
    """Same beam semantics, with coarse beam chunks expanded in worker processes."""
    workers = nesting_cpu_worker_count()
    if not _should_use_process_pool(items):
        return _search_single_rod(
            items,
            allowed_mask=allowed_mask,
            rod_length=rod_length,
            dead_zone_mm=dead_zone_mm,
            gap_mm=gap_mm,
            require_all=require_all,
            beam_width=beam_width,
            max_candidate_types=max_candidate_types,
        )

    accessible_limit = float(rod_length) - float(dead_zone_mm)
    target_mask = int(allowed_mask)
    exact_mode = bool(
        require_all
        and _use_exact_require_all_search(items, target_mask)
    )

    initial = RodSearchState()
    next_states = []
    reps = _representative_indices(
        items,
        allowed_mask=allowed_mask,
        used_mask=0,
        max_types=max_candidate_types,
    )
    for item_index in reps:
        item = items[item_index]
        for pose in _first_pose_candidates(item):
            candidate = _append_candidate(
                initial,
                item,
                pose,
                items,
                rod_length=rod_length,
                accessible_limit=accessible_limit,
                dead_zone_mm=dead_zone_mm,
                gap_mm=gap_mm,
            )
            if candidate is not None:
                next_states.append(candidate)

    if not next_states:
        return None

    best_by_key = {}
    for state in next_states:
        key = _state_dedupe_key(state)
        previous = best_by_key.get(key)
        if previous is None or _partial_score(state) < _partial_score(previous):
            best_by_key[key] = state
    ordered_states = sorted(best_by_key.values(), key=_partial_score)
    beam = ordered_states if exact_mode else ordered_states[:beam_width]
    terminals = []

    own_executor = executor is None
    local_executor = executor
    try:
        if own_executor:
            _PARALLEL_STATS["pool_runs"] += 1
            local_executor = ProcessPoolExecutor(
                max_workers=workers,
                initializer=_init_search_worker,
                initargs=(
                    items,
                    rod_length,
                    dead_zone_mm,
                    gap_mm,
                    dict(_PAIRWISE_FIT_CACHE),
                ),
            )

        _PARALLEL_STATS["initial_pool_runs"] += 1

        for _depth in range(1, len(items) + 1):
            expandable = []
            for state in beam:
                if state.used_mask:
                    terminals.append(state)
                    if require_all and state.used_mask == target_mask:
                        continue
                if state.tail_used:
                    continue
                expandable.append(state)

            if not expandable:
                break

            # Send coarse chunks, not one IPC job per beam state. This keeps
            # all cores busy while greatly reducing Windows pickle/pipe cost.
            target_chunks = min(
                len(expandable),
                max(1, workers * 2),
            )
            chunk_size = max(
                1,
                math.ceil(len(expandable) / target_chunks),
            )
            chunks = [
                expandable[index:index + chunk_size]
                for index in range(0, len(expandable), chunk_size)
            ]
            _PARALLEL_STATS["tasks"] += len(chunks)

            worker_return_limit = (
                None
                if exact_mode
                else max(
                    int(beam_width) * 2,
                    int(beam_width) + 32,
                )
            )
            task_iter = (
                (
                    chunk,
                    allowed_mask,
                    max_candidate_types,
                    worker_return_limit,
                )
                for chunk in chunks
            )
            batches = local_executor.map(
                _expand_state_chunk_worker,
                task_iter,
                chunksize=1,
            )
            next_states = [
                candidate
                for batch in batches
                for candidate in batch
            ]
            if not next_states:
                break

            best_by_key = {}
            for state in next_states:
                key = _state_dedupe_key(state)
                previous = best_by_key.get(key)
                if (
                    previous is None
                    or _partial_score(state) < _partial_score(previous)
                ):
                    best_by_key[key] = state

            ordered_states = sorted(
                best_by_key.values(),
                key=_partial_score,
            )
            beam = (
                ordered_states
                if exact_mode
                else ordered_states[:beam_width]
            )
    except Exception as exc:
        _PARALLEL_STATS["fallbacks"] += 1
        _PARALLEL_STATS["last_error"] = f"{type(exc).__name__}: {exc}"
        print(
            "[TubeNest] multiprocessing beam search failed; "
            f"falling back to serial: {_PARALLEL_STATS['last_error']}"
        )
        return _search_single_rod(
            items,
            allowed_mask=allowed_mask,
            rod_length=rod_length,
            dead_zone_mm=dead_zone_mm,
            gap_mm=gap_mm,
            require_all=require_all,
            beam_width=beam_width,
            max_candidate_types=max_candidate_types,
        )
    finally:
        if own_executor and local_executor is not None:
            local_executor.shutdown(wait=True)

    terminals.extend(beam)
    if require_all:
        terminals = [
            state
            for state in terminals
            if state.used_mask == target_mask
        ]
    else:
        terminals = [state for state in terminals if state.used_mask]

    if not terminals:
        return None
    return min(
        terminals,
        key=lambda state: _terminal_score(state, require_all),
    )


def _stitch_rod_states(
    items,
    first,
    second,
    rod_length,
    dead_zone_mm,
    gap_mm,
):
    """Append an already-valid rod sequence after another without re-searching.

    Internal poses/order are preserved. Only the connecting boundary and the
    translated downstream positions are recalculated through _append_candidate.
    """
    if first is None or second is None:
        return None
    if first.tail_used:
        return None
    if first.used_mask & second.used_mask:
        return None

    state = first
    accessible_limit = float(rod_length) - float(dead_zone_mm)
    for placed in second.placed:
        item = items[placed.item_index]
        candidate = _append_candidate(
            state,
            item,
            placed.pose,
            items,
            rod_length=rod_length,
            accessible_limit=accessible_limit,
            dead_zone_mm=dead_zone_mm,
            gap_mm=gap_mm,
        )
        if candidate is None:
            return None
        state = candidate

    if state.used_mask != (first.used_mask | second.used_mask):
        return None
    return state


def _try_stitch_merge_rods(
    items,
    rods,
    rod_length,
    dead_zone_mm,
    gap_mm,
):
    """Greedily merge existing sequences using only boundary lookups."""
    rods = list(rods)
    while True:
        best = None
        for i in range(len(rods)):
            for j in range(i + 1, len(rods)):
                a = rods[i]
                b = rods[j]

                # Quick impossible lower bound: even if the connecting
                # boundary recovered all configured gap, wildly oversized
                # pairs are not worth replaying.
                if (
                    float(a.nominal_packed) + float(b.nominal_packed)
                    > float(rod_length) + 3000.0
                ):
                    continue

                candidates = (
                    _stitch_rod_states(
                        items,
                        a,
                        b,
                        rod_length,
                        dead_zone_mm,
                        gap_mm,
                    ),
                    _stitch_rod_states(
                        items,
                        b,
                        a,
                        rod_length,
                        dead_zone_mm,
                        gap_mm,
                    ),
                )
                for merged in candidates:
                    if merged is None:
                        continue
                    key = (
                        round(float(merged.used_span), 6),
                        -int(merged.common_lines),
                        bool(merged.tail_used),
                        i,
                        j,
                    )
                    if best is None or key < best[0]:
                        best = (key, i, j, merged)

        if best is None:
            break

        _key, i, j, merged = best
        rods = [
            rod
            for index, rod in enumerate(rods)
            if index not in (i, j)
        ] + [merged]

    return rods


def _try_merge_rods(
    items,
    rods,
    rod_length,
    dead_zone_mm,
    gap_mm,
    executor=None,
    attempt_limit=MERGE_PAIR_ATTEMPT_LIMIT,
    beam_width=None,
    max_candidate_types=100,
    stitch_only=False,
):
    if stitch_only:
        return _try_stitch_merge_rods(
            items,
            rods,
            rod_length,
            dead_zone_mm,
            gap_mm,
        )

    # Test the most promising rod pairs first. On large jobs, independent pair
    # searches from the current round are distributed across CPU processes.
    attempts = 0
    attempt_limit = max(0, int(attempt_limit))
    if beam_width is None:
        beam_width = max(DEFAULT_BEAM_WIDTH * 2, 220)

    while attempts < attempt_limit:
        pair_candidates = []
        for i in range(len(rods)):
            for j in range(i + 1, len(rods)):
                combined_used = float(rods[i].used_span) + float(rods[j].used_span)
                combined_nominal = float(rods[i].nominal_packed) + float(rods[j].nominal_packed)
                pair_candidates.append((
                    combined_used,
                    min(float(rods[i].used_span), float(rods[j].used_span)),
                    combined_nominal,
                    i,
                    j,
                ))
        pair_candidates.sort()

        remaining_budget = attempt_limit - attempts
        selected = []
        for _combined_used, _shortest, union_nominal, i, j in pair_candidates:
            if len(selected) >= remaining_budget:
                break
            if union_nominal > float(rod_length) + 3000.0:
                continue
            selected.append((i, j, rods[i].used_mask | rods[j].used_mask))

        if not selected:
            break

        attempts += len(selected)
        tasks = [
            (mask, int(beam_width), int(max_candidate_types))
            for _i, _j, mask in selected
        ]
        merged_states = _parallel_search_masks(
            items,
            tasks,
            rod_length=rod_length,
            dead_zone_mm=dead_zone_mm,
            gap_mm=gap_mm,
            executor=executor,
        )

        best_merge = None
        for (i, j, _mask), merged in zip(selected, merged_states):
            if merged is None:
                continue
            key = (
                round(merged.used_span, 6),
                -merged.common_lines,
                merged.tail_used,
                i,
                j,
            )
            if best_merge is None or key < best_merge[0]:
                best_merge = (key, i, j, merged)

        if best_merge is None:
            break

        _, i, j, merged = best_merge
        rods = [
            rod for index, rod in enumerate(rods)
            if index not in (i, j)
        ] + [merged]

    return rods

def _refine_fixed_sequence_poses(
    items,
    rod,
    rod_length,
    dead_zone_mm,
    gap_mm,
):
    """Optimize poses for one existing piece order without reordering pieces."""
    if rod is None or not rod.placed:
        return rod

    sequence = [
        placed.item_index
        for placed in rod.placed
    ]
    best = _optimize_fixed_item_sequence(
        items,
        sequence,
        rod_length=rod_length,
        dead_zone_mm=dead_zone_mm,
        gap_mm=gap_mm,
    )
    if best is None or best.used_mask != rod.used_mask:
        return rod
    return best


def _refine_rods_fixed_order(
    items,
    rods,
    rod_length,
    dead_zone_mm,
    gap_mm,
):
    return [
        _refine_fixed_sequence_poses(
            items,
            rod,
            rod_length,
            dead_zone_mm,
            gap_mm,
        )
        for rod in rods
    ]


def _refine_rods(
    items,
    rods,
    rod_length,
    dead_zone_mm,
    gap_mm,
    executor=None,
    beam_width=None,
    max_candidate_types=40,
    utilization_threshold=None,
):
    if not rods:
        return []

    if beam_width is None:
        beam_width = max(DEFAULT_BEAM_WIDTH * 2, 220)

    selected_indices = []
    for index, rod in enumerate(rods):
        if utilization_threshold is None:
            selected_indices.append(index)
            continue
        utilization = (
            float(rod.used_span) / float(rod_length)
            if float(rod_length) > EPS
            else 1.0
        )
        # Small memberships are cheap/exact; otherwise only spend refinement
        # time on rods with meaningful free material left.
        if rod.piece_count <= EXACT_REQUIRE_ALL_MAX_PIECES or utilization < float(utilization_threshold):
            selected_indices.append(index)

    if not selected_indices:
        return list(rods)

    tasks = [
        (
            rods[index].used_mask,
            int(beam_width),
            int(max_candidate_types),
        )
        for index in selected_indices
    ]
    refined_states = _parallel_search_masks(
        items,
        tasks,
        rod_length=rod_length,
        dead_zone_mm=dead_zone_mm,
        gap_mm=gap_mm,
        executor=executor,
    )

    result = list(rods)
    for index, refined in zip(selected_indices, refined_states):
        if refined is not None:
            result[index] = refined
    return result


def _optimize_fixed_item_sequence(
    items,
    sequence,
    rod_length,
    dead_zone_mm,
    gap_mm,
):
    """Optimize legal poses for one fixed piece order.

    State count is bounded by the legal pose set of the last part rather than
    by permutations of every part. This makes it suitable for interactive
    insertion tests even on production jobs with many pieces.
    """
    sequence = [int(index) for index in sequence]
    if not sequence:
        return None

    accessible_limit = float(rod_length) - float(dead_zone_mm)
    states = [RodSearchState()]

    for item_index in sequence:
        item = items[item_index]
        next_states = []

        for state in states:
            # Once a piece enters the chuck/dead zone it must be the final
            # piece on the rod. This mirrors _search_single_rod.
            if state.tail_used:
                continue

            if not state.placed:
                poses = _first_pose_candidates(item)
            else:
                previous = state.placed[-1]
                previous_item = items[previous.item_index]
                poses = _next_pose_candidates(
                    previous_item,
                    previous.pose,
                    item,
                    state.rectangle_family,
                )

            for pose in poses:
                candidate = _append_candidate(
                    state,
                    item,
                    pose,
                    items,
                    rod_length=rod_length,
                    accessible_limit=accessible_limit,
                    dead_zone_mm=dead_zone_mm,
                    gap_mm=gap_mm,
                )
                if candidate is not None:
                    next_states.append(candidate)

        if not next_states:
            return None

        best_by_pose = {}
        for state in next_states:
            last = state.placed[-1]
            key = (
                round(
                    _normalize_angle(
                        last.pose.axial_rotation_degrees
                    ),
                    6,
                ),
                bool(last.pose.reversed_end_for_end),
                state.rectangle_family,
                bool(state.tail_used),
            )
            previous = best_by_pose.get(key)
            if (
                previous is None
                or _terminal_score(state, True)
                < _terminal_score(previous, True)
            ):
                best_by_pose[key] = state
        states = list(best_by_pose.values())

    expected_mask = 0
    for item_index in sequence:
        expected_mask |= 1 << item_index
    candidates = [
        state
        for state in states
        if state.used_mask == expected_mask
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda state: _terminal_score(state, True),
    )


def _insertion_positions(sequence):
    count = len(sequence)
    positions = list(range(count + 1))
    if len(positions) <= FAST_BEST_FIT_MAX_INSERTION_POSITIONS:
        return positions

    # Keep both ends, the positions around the last piece (important for the
    # chuck-zone case), and evenly distributed interior samples.
    wanted = {
        0,
        1,
        max(0, count - 1),
        count,
    }
    slots = max(
        0,
        FAST_BEST_FIT_MAX_INSERTION_POSITIONS - len(wanted),
    )
    if slots:
        for step in range(1, slots + 1):
            wanted.add(
                round(
                    step * count / (slots + 1)
                )
            )
    return sorted(
        position
        for position in wanted
        if 0 <= position <= count
    )


def _boundary_chain_strength(
    items,
    previous_placed,
    current_placed,
):
    """Return how strongly an existing adjacency should be preserved.

    Fast best-fit is meant to move *groups* of compatible cuts together, not
    scatter straight pieces between 45-degree/common-line chains merely
    because a local insertion looks marginally tighter. The score is only used
    to protect an existing boundary during insertion; if no other insertion is
    legal, the algorithm may still split it as a fallback.
    """
    if bool(current_placed.common_line_before):
        return 3

    if (
        float(current_placed.overlap_before_mm or 0.0)
        > FAST_CHAIN_OVERLAP_EPS_MM
    ):
        return 2

    previous_item = items[previous_placed.item_index]
    current_item = items[current_placed.item_index]
    if (
        previous_item.geometry_available
        and current_item.geometry_available
    ):
        try:
            _previous_start, previous_end = posed_ends(
                previous_item.tube_part,
                previous_placed.pose,
            )
            current_start, _current_end = posed_ends(
                current_item.tube_part,
                current_placed.pose,
            )
            previous_angle = math.degrees(
                math.atan(previous_end.slope_magnitude)
            )
            current_angle = math.degrees(
                math.atan(current_start.slope_magnitude)
            )
            if (
                previous_angle >= FAST_CHAIN_ANGLE_EPS_DEGREES
                and current_angle >= FAST_CHAIN_ANGLE_EPS_DEGREES
            ):
                return 1
        except (ValueError, TypeError, AttributeError):
            pass

    return 0


def _protected_insertion_positions(items, rod):
    """Positions that would break an efficient/angled existing cut chain."""
    protected = set()
    placed = list(rod.placed)
    for position in range(1, len(placed)):
        if (
            _boundary_chain_strength(
                items,
                placed[position - 1],
                placed[position],
            )
            > 0
        ):
            protected.add(position)
    return protected


def _best_insert_item_fixed_order(
    items,
    rod,
    item_index,
    rod_length,
    dead_zone_mm,
    gap_mm,
    deadline=None,
):
    """Return the best rod obtained by inserting one item into this sequence."""
    sequence = [
        placed.item_index
        for placed in rod.placed
    ]
    positions = _insertion_positions(sequence)
    protected = _protected_insertion_positions(
        items,
        rod,
    )

    # First preserve already-efficient chains (common-line, interlocked, or
    # angled-to-angled). Only if the item cannot be inserted anywhere else do
    # we permit breaking one of those boundaries.
    passes = [
        [
            position
            for position in positions
            if position not in protected
        ],
        [
            position
            for position in positions
            if position in protected
        ],
    ]

    for candidate_positions in passes:
        if not candidate_positions:
            continue

        best = None
        for position in candidate_positions:
            if (
                deadline is not None
                and time.perf_counter() >= float(deadline)
            ):
                break
            candidate_sequence = (
                sequence[:position]
                + [int(item_index)]
                + sequence[position:]
            )
            candidate = _optimize_fixed_item_sequence(
                items,
                candidate_sequence,
                rod_length=rod_length,
                dead_zone_mm=dead_zone_mm,
                gap_mm=gap_mm,
            )
            _PARALLEL_STATS[
                "fast_best_fit_insertion_attempts"
            ] += 1
            if candidate is None:
                continue

            # Keep material span primary, but among equally good placements
            # prefer the solution with more shared/interlocking boundaries.
            key = (
                round(float(candidate.used_span), 6),
                -int(candidate.common_lines),
                -round(
                    sum(
                        float(placed.overlap_before_mm or 0.0)
                        for placed in candidate.placed[1:]
                    ),
                    6,
                ),
                0 if candidate.tail_used else 1,
                position,
            )
            if best is None or key < best[0]:
                best = (key, candidate)

        if best is not None:
            return best[1]

        if (
            deadline is not None
            and time.perf_counter() >= float(deadline)
        ):
            break

    return None


def _fast_best_fit_build(
    items,
    rod_length,
    dead_zone_mm,
    gap_mm,
    order,
    deadline=None,
):
    rods = []

    for item_index in order:
        choices = []
        for rod_index, rod in enumerate(rods):
            if (
                deadline is not None
                and time.perf_counter() >= float(deadline)
            ):
                break
            candidate = _best_insert_item_fixed_order(
                items,
                rod,
                item_index,
                rod_length=rod_length,
                dead_zone_mm=dead_zone_mm,
                gap_mm=gap_mm,
                deadline=deadline,
            )
            if candidate is None:
                continue

            # Best-fit: fill an existing rod as tightly as possible. A legal
            # tail-flip is preferred when utilization is otherwise similar.
            key = (
                round(
                    float(rod_length)
                    - float(candidate.used_span),
                    6,
                ),
                0 if candidate.tail_used else 1,
                -int(candidate.common_lines),
                rod_index,
            )
            choices.append((key, rod_index, candidate))

        if choices:
            _key, rod_index, candidate = min(
                choices,
                key=lambda value: value[0],
            )
            rods[rod_index] = candidate
            continue

        candidate = _optimize_fixed_item_sequence(
            items,
            [item_index],
            rod_length=rod_length,
            dead_zone_mm=dead_zone_mm,
            gap_mm=gap_mm,
        )
        if candidate is None:
            raise ValueError(
                "No machine-safe nesting found for piece "
                + items[item_index].instance_key
            )
        rods.append(candidate)

    return rods


def _fast_plan_score(rods):
    """Prefer fewer rods, then a smaller final/shortest leftover rod."""
    if not rods:
        return (0, 0.0, 0.0, ())
    used = sorted(
        (float(rod.used_span) for rod in rods),
        reverse=True,
    )
    return (
        len(rods),
        round(sum(float(rod.used_span) for rod in rods), 6),
        round(min(used), 6),
        tuple(round(value, 6) for value in used),
    )


def _fast_redistribute_short_rods(
    items,
    rods,
    rod_length,
    dead_zone_mm,
    gap_mm,
    deadline=None,
):
    """Bounded greedy attempt to eliminate the shortest leftover rod.

    No recursive/backtracking assignment is used here. The previous version
    could revisit many receiver combinations and became unexpectedly slow even
    on modest production groups. We try at most two deterministic donor orders
    and keep the first complete elimination.
    """
    rods = list(rods)
    if len(rods) <= 1:
        return rods

    for _pass in range(FAST_BEST_FIT_CONSOLIDATION_PASSES):
        if deadline is not None and time.perf_counter() >= float(deadline):
            break

        improved = False
        donor_order = sorted(
            range(len(rods)),
            key=lambda index: (
                float(rods[index].nominal_packed),
                float(rods[index].used_span),
                int(rods[index].piece_count),
                index,
            ),
        )

        for donor_index in donor_order:
            if deadline is not None and time.perf_counter() >= float(deadline):
                break

            donor = rods[donor_index]
            donor_items = [
                placed.item_index
                for placed in donor.placed
            ]
            if (
                not donor_items
                or len(donor_items) > FAST_BEST_FIT_DONOR_MAX_PIECES
            ):
                continue

            receivers_base = [
                rod
                for index, rod in enumerate(rods)
                if index != donor_index
            ]
            donor_orders = [
                sorted(
                    donor_items,
                    key=lambda index: (
                        -float(items[index].physical_length),
                        str(items[index].instance_key),
                    ),
                ),
                sorted(
                    donor_items,
                    key=lambda index: (
                        float(items[index].physical_length),
                        str(items[index].instance_key),
                    ),
                ),
            ]

            seen_orders = set()
            for ordered_items in donor_orders:
                order_key = tuple(ordered_items)
                if order_key in seen_orders:
                    continue
                seen_orders.add(order_key)

                receivers = list(receivers_base)
                attempts = 0
                complete = True

                for item_index in ordered_items:
                    if (
                        attempts >= FAST_BEST_FIT_DONOR_ATTEMPTS
                        or (
                            deadline is not None
                            and time.perf_counter() >= float(deadline)
                        )
                    ):
                        complete = False
                        break

                    choices = []
                    for receiver_index, receiver in enumerate(receivers):
                        if (
                            attempts >= FAST_BEST_FIT_DONOR_ATTEMPTS
                            or (
                                deadline is not None
                                and time.perf_counter() >= float(deadline)
                            )
                        ):
                            break

                        attempts += 1
                        _PARALLEL_STATS[
                            "fast_best_fit_consolidation_attempts"
                        ] += 1
                        candidate = _best_insert_item_fixed_order(
                            items,
                            receiver,
                            item_index,
                            rod_length=rod_length,
                            dead_zone_mm=dead_zone_mm,
                            gap_mm=gap_mm,
                            deadline=deadline,
                        )
                        if candidate is None:
                            continue

                        choices.append(
                            (
                                round(
                                    float(rod_length)
                                    - float(candidate.used_span),
                                    6,
                                ),
                                0 if candidate.tail_used else 1,
                                -int(candidate.common_lines),
                                receiver_index,
                                candidate,
                            )
                        )

                    if not choices:
                        complete = False
                        break

                    (
                        _remaining,
                        _tail_rank,
                        _common_rank,
                        receiver_index,
                        candidate,
                    ) = min(
                        choices,
                        key=lambda value: value[:4],
                    )
                    receivers[receiver_index] = candidate

                if complete:
                    rods = receivers
                    improved = True
                    break

            if improved:
                break

        if not improved:
            break

    return rods


def _fast_best_fit_optimize(
    items,
    rod_length,
    dead_zone_mm,
    gap_mm,
):
    """Time-bounded secondary algorithm for interactive production use."""
    started = time.perf_counter()
    deadline = started + FAST_BEST_FIT_TIME_BUDGET_SECONDS

    # One strong deterministic order is enough for the interactive mode.
    # Longest-first is standard bin-packing behavior; insertion before the last
    # piece still allows the final legal flip-tail to enter the 400 mm zone.
    indices = list(range(len(items)))
    order = sorted(
        indices,
        key=lambda index: (
            -float(items[index].physical_length),
            str(items[index].instance_key),
        ),
    )

    rods = _fast_best_fit_build(
        items,
        rod_length=rod_length,
        dead_zone_mm=dead_zone_mm,
        gap_mm=gap_mm,
        order=order,
        deadline=deadline,
    )
    rods = _fast_redistribute_short_rods(
        items,
        rods,
        rod_length=rod_length,
        dead_zone_mm=dead_zone_mm,
        gap_mm=gap_mm,
        deadline=deadline,
    )
    _PARALLEL_STATS["algorithm"] = "best_fit"
    _PARALLEL_STATS["strategy"] = "time_bounded_best_fit"
    _PARALLEL_STATS["fast_best_fit_time_budget_seconds"] = (
        FAST_BEST_FIT_TIME_BUDGET_SECONDS
    )
    _PARALLEL_STATS["fast_best_fit_budget_exhausted"] = (
        time.perf_counter() >= deadline
    )
    _PARALLEL_STATS["fast_best_fit_rods"] = len(rods)
    _PARALLEL_STATS["fast_best_fit_seconds"] = (
        time.perf_counter() - started
    )
    _PARALLEL_STATS["total_seconds"] = (
        _PARALLEL_STATS["fast_best_fit_seconds"]
    )
    return rods


def _posed_end_dict(end):
    if end is None:
        return None
    return {
        "c": float(end.c),
        "slope_x": float(end.slope_x),
        "slope_vertical": float(end.slope_vertical),
        "source_label": end.source_label,
        "operation_layer": end.operation_layer,
        "plane_max_residual_mm": end.residual_mm,
        "angle_from_perpendicular_degrees": math.degrees(
            math.atan(end.slope_magnitude)
        ),
    }


def _rod_to_dict(state, items, rod_length, dead_zone_mm, gap_mm):
    placements = []
    for position, placed in enumerate(state.placed):
        item = items[placed.item_index]
        part = item.tube_part if item.geometry_available else None
        end_a = end_b = None
        if part is not None:
            end_a, end_b = posed_ends(part, placed.pose)

        physical_length = item.physical_length
        if position == 0:
            attempted_poses = _first_pose_candidates(item)
        else:
            previous_placed = state.placed[position - 1]
            previous_item = items[previous_placed.item_index]
            attempted_poses = _next_pose_candidates(
                previous_item,
                previous_placed.pose,
                item,
                state.rectangle_family,
            )
        attempted_rotations = sorted(
            {
                round(
                    _normalize_angle(
                        candidate.axial_rotation_degrees
                    ),
                    3,
                )
                for candidate in attempted_poses
            }
        )
        attempted_reversed = any(
            bool(candidate.reversed_end_for_end)
            for candidate in attempted_poses
        )
        attempted_normal = any(
            not bool(candidate.reversed_end_for_end)
            for candidate in attempted_poses
        )

        placement = {
            "instance_key": item.instance_key,
            "source_id": item.source_id,
            "z_start": float(placed.origin),
            "z_end": float(placed.origin + physical_length),
            "occupied_length": float(physical_length),
            "nominal_length": float(item.nominal_length),
            "axial_rotation_degrees": float(_normalize_angle(placed.pose.axial_rotation_degrees)),
            "reversed_end_for_end": bool(placed.pose.reversed_end_for_end),
            "rotation_candidates_degrees": attempted_rotations,
            "candidate_normal_orientation": attempted_normal,
            "candidate_reversed_orientation": attempted_reversed,
            "geometry_available": bool(part),
            "part_fingerprint": part.get("part_fingerprint") if part else None,
            "source_sha256": part.get("source_sha256") if part else None,
            "end_a": _posed_end_dict(end_a),
            "end_b": _posed_end_dict(end_b),
            "common_line_before": bool(placed.common_line_before),
            "gap_before_mm": float(placed.gap_before_mm),
            "overlap_before_mm": float(placed.overlap_before_mm),
            "requires_tail_flip": bool(placed.requires_tail_flip),
            "geometry_fallback_before": placed.geometry_fallback_before,
            "payload": dict(item.payload or {}),
        }
        placements.append(placement)

    # Mark the other side of every shared boundary too.
    for index in range(1, len(placements)):
        if placements[index]["common_line_before"]:
            placements[index - 1]["common_line_after"] = True
        else:
            placements[index - 1]["common_line_after"] = False
    if placements:
        placements[-1]["common_line_after"] = False

    return {
        "rodLength": float(rod_length),
        "accessibleLength": float(rod_length) - float(dead_zone_mm),
        "chuckDeadZoneMm": float(dead_zone_mm),
        "gapMm": float(gap_mm),
        "used": float(state.used_span),
        "remaining": max(0.0, float(rod_length) - float(state.used_span)),
        "usesTailFlip": bool(state.tail_used),
        "commonLineCount": int(state.common_lines),
        "rectangleOrientationFamily": state.rectangle_family,
        "placements": placements,
        "pieces": [placement["source_id"] for placement in placements],
    }


def optimize_items(
    items,
    rod_length=DEFAULT_ROD_LENGTH,
    gap_mm=DEFAULT_GAP_MM,
    dead_zone_mm=DEFAULT_CHUCK_DEAD_ZONE,
    algorithm="current",
):
    _reset_parallel_stats()
    rod_length = float(rod_length)
    gap_mm = max(0.0, float(gap_mm))
    dead_zone_mm = max(0.0, float(dead_zone_mm))

    if rod_length <= 0:
        raise ValueError("rod_length must be positive")
    if dead_zone_mm >= rod_length:
        raise ValueError("dead_zone_mm must be smaller than rod_length")

    normalized = _normalize_items(items, rod_length)
    if not normalized:
        return []

    algorithm = str(algorithm or "current").strip().lower()
    if algorithm == "global_compaction":
        algorithm = "best_fit"
    if algorithm not in {"current", "best_fit"}:
        algorithm = "current"
    _PARALLEL_STATS["algorithm"] = algorithm

    if algorithm == "best_fit":
        rods = _fast_best_fit_optimize(
            normalized,
            rod_length=rod_length,
            dead_zone_mm=dead_zone_mm,
            gap_mm=gap_mm,
        )
    else:
        rods = _optimize_normalized(
            normalized,
            rod_length=rod_length,
            dead_zone_mm=dead_zone_mm,
            gap_mm=gap_mm,
        )

    rods.sort(
        key=lambda state: (
            -round(state.used_span, 6),
            -state.common_lines,
            tuple(normalized[p.item_index].instance_key for p in state.placed),
        )
    )
    return rods



def _mask_from_rod_dict(rod, item_by_instance):
    mask = 0
    for placement in (rod or {}).get("placements") or []:
        item = item_by_instance.get(str(placement.get("instance_key") or ""))
        if item is not None:
            mask |= 1 << item.index
    return mask


def _rod_piece_summary(rod):
    placements = list((rod or {}).get("placements") or [])
    parts = []
    for placement in placements:
        length = float(placement.get("nominal_length") or placement.get("occupied_length") or 0.0)
        angle_a = float(((placement.get("end_a") or {}).get("angle_from_perpendicular_degrees") or 0.0))
        angle_b = float(((placement.get("end_b") or {}).get("angle_from_perpendicular_degrees") or 0.0))
        flags = []
        if placement.get("common_line_before"):
            flags.append("CL prima")
        if placement.get("common_line_after"):
            flags.append("CL dopo")
        if placement.get("reversed_end_for_end"):
            flags.append("girato 180°")
        rotation = float(placement.get("axial_rotation_degrees") or 0.0)
        if abs(rotation) > 1e-6:
            flags.append(f"rotY {rotation:g}°")
        suffix = f" [{' / '.join(flags)}]" if flags else ""
        parts.append(
            f"{length:g}mm ({angle_a:.1f}°/{angle_b:.1f}°){suffix}"
        )
    return ", ".join(parts)


def _possible_common_line_between_items(item_a, item_b):
    if not item_a.geometry_available or not item_b.geometry_available:
        return None

    best = None
    poses_a = _first_pose_candidates(item_a)
    for pose_a in poses_a:
        family = _rectangle_family(pose_a.axial_rotation_degrees) if _profile_kind(item_a) == "Rect" else None
        poses_b = _next_pose_candidates(item_a, pose_a, item_b, family)
        for pose_b in poses_b:
            try:
                fit = fit_adjacent_parts(
                    item_a.tube_part,
                    pose_a,
                    0.0,
                    item_b.tube_part,
                    pose_b,
                    gap_mm=0.0,
                    allow_common_line=True,
                )
            except (ValueError, TypeError):
                continue
            if not fit.common_line:
                continue
            candidate = (
                abs(float(fit.next_origin)),
                pose_a,
                pose_b,
            )
            if best is None or candidate[0] < best[0]:
                best = candidate

    if best is None:
        return None
    _, pose_a, pose_b = best
    return {
        "aRotation": _normalize_angle(pose_a.axial_rotation_degrees),
        "aReversed": bool(pose_a.reversed_end_for_end),
        "bRotation": _normalize_angle(pose_b.axial_rotation_degrees),
        "bReversed": bool(pose_b.reversed_end_for_end),
    }


def _item_debug_label(item):
    if item is None:
        return "?"
    file_name = str((item.payload or {}).get("fileName") or "").strip()
    if file_name:
        return f"{item.nominal_length:g}mm | {file_name}"
    return f"{item.nominal_length:g}mm | {item.instance_key}"


def _placement_pose(placement):
    return PartPose(
        axial_rotation_degrees=float(placement.get("axial_rotation_degrees") or 0.0),
        reversed_end_for_end=bool(placement.get("reversed_end_for_end")),
    )


def _signed_end_summary(end):
    if not isinstance(end, dict):
        return "?"
    sx = float(end.get("slope_x") or 0.0)
    sv = float(end.get("slope_vertical") or 0.0)
    angle = float(end.get("angle_from_perpendicular_degrees") or 0.0)
    return f"{angle:.1f}° [sx={sx:+.3f}, sv={sv:+.3f}]"


def _boundary_pose_alternatives(items, rod):
    placements = list((rod or {}).get("placements") or [])
    item_by_instance = {item.instance_key: item for item in items}
    results = []

    for index in range(1, len(placements)):
        prev_p = placements[index - 1]
        next_p = placements[index]
        prev_item = item_by_instance.get(str(prev_p.get("instance_key") or ""))
        next_item = item_by_instance.get(str(next_p.get("instance_key") or ""))
        if prev_item is None or next_item is None:
            continue
        if not prev_item.geometry_available or not next_item.geometry_available:
            continue

        prev_pose = _placement_pose(prev_p)
        current_pose = _placement_pose(next_p)
        previous_origin = float(prev_p.get("z_start") or 0.0)
        current_origin = float(next_p.get("z_start") or 0.0)

        rectangle_family = None
        if _profile_kind(prev_item) == "Rect":
            rectangle_family = _rectangle_family(prev_pose.axial_rotation_degrees)
        poses = _next_pose_candidates(
            prev_item,
            prev_pose,
            next_item,
            rectangle_family,
        )

        alternatives = []
        rejected = []
        for pose in poses:
            try:
                fit = fit_adjacent_parts(
                    prev_item.tube_part,
                    prev_pose,
                    previous_origin,
                    next_item.tube_part,
                    pose,
                    gap_mm=float((rod or {}).get("gapMm") or DEFAULT_GAP_MM),
                    allow_common_line=True,
                )
            except (ValueError, TypeError) as exc:
                rejected.append({
                    "rotation": _normalize_angle(pose.axial_rotation_degrees),
                    "reversed": bool(pose.reversed_end_for_end),
                    "reason": str(exc),
                })
                continue

            candidate_end = float(fit.next_origin) + next_item.physical_length
            alternatives.append({
                "rotation": _normalize_angle(pose.axial_rotation_degrees),
                "reversed": bool(pose.reversed_end_for_end),
                "origin": float(fit.next_origin),
                "end": candidate_end,
                "commonLine": bool(fit.common_line),
                "gap": float(fit.minimum_clearance_mm),
                "overlap": float(fit.overlap_of_axial_envelopes_mm),
                "deltaOrigin": float(fit.next_origin) - current_origin,
            })

        alternatives.sort(
            key=lambda row: (
                round(row["end"], 6),
                0 if row["commonLine"] else 1,
                abs(row["deltaOrigin"]),
                row["reversed"],
                row["rotation"],
            )
        )
        results.append({
            "boundaryIndex": index,
            "previous": prev_p,
            "next": next_p,
            "alternatives": alternatives,
            "currentPose": {
                "rotation": _normalize_angle(current_pose.axial_rotation_degrees),
                "reversed": bool(current_pose.reversed_end_for_end),
                "origin": current_origin,
            },
            "rejected": rejected,
        })

    return results


def diagnose_items_dict(
    items,
    rods,
    rod_length=DEFAULT_ROD_LENGTH,
    gap_mm=DEFAULT_GAP_MM,
    dead_zone_mm=DEFAULT_CHUCK_DEAD_ZONE,
    deep_pair_checks=24,
    focus_rod_number=None,
):
    """Explain likely missed optimization opportunities in an existing plan.

    This is intentionally more expensive than normal nesting and should only be
    called on demand from the debug UI.
    """
    normalized = _normalize_items(items, rod_length)
    item_by_instance = {item.instance_key: item for item in normalized}
    rod_masks = [_mask_from_rod_dict(rod, item_by_instance) for rod in rods or []]
    rod_count = len(rod_masks)
    total_pairs = rod_count * (rod_count - 1) // 2
    focus_index = None
    if focus_rod_number is not None:
        try:
            candidate = int(focus_rod_number) - 1
        except (TypeError, ValueError):
            candidate = -1
        if 0 <= candidate < rod_count:
            focus_index = candidate

    lines = []
    lines.append("=== DEBUG NESTING GEOMETRICO ===")
    lines.append(f"Pezzi liberi: {len(normalized)}")
    lines.append(f"Verghe risultanti: {rod_count}")
    lines.append(f"Gap: {float(gap_mm):g} mm")
    lines.append(f"Beam normale: {DEFAULT_BEAM_WIDTH}")
    lines.append(f"Tipi candidati per livello: {DEFAULT_MAX_CANDIDATE_TYPES}")
    cpu_stats = get_parallel_stats()
    lines.append(
        f"CPU nesting configurato: fino a {cpu_stats['configured_workers']} processi "
        f"(parallelismo da {cpu_stats['parallel_min_items']} pezzi)"
    )
    lines.append(
        f"CPU ultimo nesting: pool={cpu_stats['pool_runs']}, "
        f"beam iniziale={cpu_stats['initial_pool_runs']}, "
        f"merge/refine={cpu_stats['mask_pool_runs']}, "
        f"task={cpu_stats['tasks']}, fallback={cpu_stats['fallbacks']}"
    )
    if cpu_stats["last_error"]:
        lines.append(f"ERRORE multiprocessing: {cpu_stats['last_error']}")
    lines.append(f"Coppie di verghe possibili: {total_pairs}")
    lines.append(
        f"Limite merge corrente: {MERGE_PAIR_ATTEMPT_LIMIT} tentativi, "
        "ordinati dalle coppie più promettenti"
    )
    if total_pairs > MERGE_PAIR_ATTEMPT_LIMIT:
        lines.append(
            f"ATTENZIONE: fino a {total_pairs - MERGE_PAIR_ATTEMPT_LIMIT} coppie "
            "meno promettenti possono non essere testate."
        )
    lines.append("")

    lines.append("=== PIANO ATTUALE ===")
    for index, rod in enumerate(rods or [], 1):
        if focus_index is not None and (index - 1) != focus_index:
            continue
        lines.append(
            f"Verga {index}: usati {float(rod.get('used') or 0.0):.1f} mm, "
            f"rimasti {float(rod.get('remaining') or 0.0):.1f} mm, "
            f"common-line {int(rod.get('commonLineCount') or 0)}"
        )
        lines.append(f"  {_rod_piece_summary(rod)}")
    lines.append("")

    if focus_index is None:
        # Rank pair checks by how likely they are to improve material usage:
        # low combined used span first, then pairs involving the shortest rod.
        pair_candidates = []
        for i in range(rod_count):
            for j in range(i + 1, rod_count):
                used_i = float((rods[i] or {}).get("used") or 0.0)
                used_j = float((rods[j] or {}).get("used") or 0.0)
                pair_ordinal = i * rod_count - (i * (i + 1)) // 2 + (j - i)
                pair_candidates.append((
                    used_i + used_j,
                    min(used_i, used_j),
                    pair_ordinal,
                    i,
                    j,
                ))
        pair_candidates.sort()

        lines.append("=== CONTROLLO APPROFONDITO COPPIE PROMETTENTI ===")
        definite_merges = []
        checked = 0
        for _combined, _shortest, ordinal, i, j in pair_candidates:
            if checked >= int(deep_pair_checks):
                break
            union_mask = rod_masks[i] | rod_masks[j]
            if not union_mask:
                continue
            checked += 1

            merged = _search_single_rod(
                normalized,
                allowed_mask=union_mask,
                rod_length=float(rod_length),
                dead_zone_mm=float(dead_zone_mm),
                gap_mm=max(0.0, float(gap_mm)),
                require_all=True,
                beam_width=max(DEFAULT_BEAM_WIDTH * 4, 500),
                max_candidate_types=100,
            )

            prefix = f"Verga {i + 1} + Verga {j + 1} (coppia #{ordinal})"
            if merged is not None:
                definite_merges.append((i + 1, j + 1, merged))
                cap_note = f" [indice storico coppia #{ordinal}]" if ordinal > MERGE_PAIR_ATTEMPT_LIMIT else ""
                lines.append(
                    f"{prefix}: PUÒ DIVENTARE 1 VERGA -> "
                    f"{merged.used_span:.1f} mm, CL={merged.common_lines}{cap_note}"
                )
            else:
                lines.append(f"{prefix}: non entra in una sola verga con le regole attuali")

        if not definite_merges:
            lines.append("Nessuna eliminazione certa di una verga trovata nelle coppie approfondite.")
        lines.append("")

        lines.append("=== COMMON-LINE POSSIBILI TRA VERGHE DIVERSE ===")
        common_candidates = []
        seen_types = set()
        for i in range(rod_count):
            placements_i = list((rods[i] or {}).get("placements") or [])
            for j in range(i + 1, rod_count):
                placements_j = list((rods[j] or {}).get("placements") or [])
                for pa in placements_i:
                    ia = item_by_instance.get(str(pa.get("instance_key") or ""))
                    if ia is None:
                        continue
                    for pb in placements_j:
                        ib = item_by_instance.get(str(pb.get("instance_key") or ""))
                        if ib is None:
                            continue
                        type_pair = tuple(sorted((ia.type_key, ib.type_key), key=str))
                        if type_pair in seen_types:
                            continue
                        seen_types.add(type_pair)
                        match = _possible_common_line_between_items(ia, ib)
                        if match is None:
                            continue
                        common_candidates.append((
                            i + 1,
                            j + 1,
                            ia,
                            ib,
                            match,
                        ))

        if not common_candidates:
            lines.append("Nessuna common-line geometrica trovata tra pezzi su verghe diverse.")
        else:
            for i, j, ia, ib, match in common_candidates[:30]:
                lines.append(
                    f"Verga {i} <-> Verga {j}: "
                    f"{ia.nominal_length:g}mm / {ib.nominal_length:g}mm "
                    f"può condividere un taglio; "
                    f"pose A rotY={match['aRotation']:g}° rev={match['aReversed']}, "
                    f"B rotY={match['bRotation']:g}° rev={match['bReversed']}"
                )
            if len(common_candidates) > 30:
                lines.append(f"... altre {len(common_candidates) - 30} compatibilità non mostrate.")
        lines.append("")

    lines.append("=== DEBUG CONFINI / POSE ALTERNATIVE ===")
    for rod_index, rod in enumerate(rods or [], 1):
        if focus_index is not None and (rod_index - 1) != focus_index:
            continue
        boundary_rows = _boundary_pose_alternatives(normalized, rod)
        for row in boundary_rows:
            current = row["currentPose"]
            alternatives = row["alternatives"]
            if not alternatives:
                continue

            current_match = None
            for alt in alternatives:
                if (
                    abs(alt["rotation"] - current["rotation"]) <= 1e-5
                    and alt["reversed"] == current["reversed"]
                ):
                    current_match = alt
                    break

            # Focus the report on boundaries where the current transition is not
            # already common-line, or where another pose is materially better.
            interesting = not bool(row["next"].get("common_line_before"))
            if current_match is not None and alternatives:
                best = alternatives[0]
                if best["end"] < current_match["end"] - 1e-4:
                    interesting = True
                if best["commonLine"] and not current_match["commonLine"]:
                    interesting = True
            if not interesting:
                continue

            prev_len = float(row["previous"].get("nominal_length") or row["previous"].get("occupied_length") or 0.0)
            next_len = float(row["next"].get("nominal_length") or row["next"].get("occupied_length") or 0.0)
            prev_item = next(
                (item for item in normalized if item.instance_key == str(row["previous"].get("instance_key") or "")),
                None,
            )
            next_item = next(
                (item for item in normalized if item.instance_key == str(row["next"].get("instance_key") or "")),
                None,
            )
            lines.append(
                f"Verga {rod_index}, confine {row['boundaryIndex']}: "
                f"{_item_debug_label(prev_item)} -> {_item_debug_label(next_item)}"
            )
            lines.append(
                f"  uscita attuale: {_signed_end_summary(row['previous'].get('end_b'))}"
            )
            lines.append(
                f"  ingresso attuale: {_signed_end_summary(row['next'].get('end_a'))}; "
                f"pose rotY={current['rotation']:g}° rev={current['reversed']}"
            )

            for alt in alternatives[:8]:
                marker = "ATTUALE" if (
                    abs(alt["rotation"] - current["rotation"]) <= 1e-5
                    and alt["reversed"] == current["reversed"]
                ) else "ALT"
                lines.append(
                    f"    {marker}: rotY={alt['rotation']:g}° rev={alt['reversed']} "
                    f"-> origine {alt['origin']:.3f}, fine {alt['end']:.3f}, "
                    f"CL={alt['commonLine']}, gap={alt['gap']:.3f}, "
                    f"overlap={alt['overlap']:.3f}, Δorigine={alt['deltaOrigin']:+.3f}"
                )
            for rejected in row.get("rejected") or []:
                lines.append(
                    f"    RIFIUTATA: rotY={rejected['rotation']:g}° "
                    f"rev={rejected['reversed']} -> {rejected['reason']}"
                )

            # Include raw residuals because planar-fit tolerance is one of the
            # few reasons a legal square-tube pose can disappear before scoring.
            prev_residual = (row["previous"].get("end_b") or {}).get("plane_max_residual_mm")
            next_residual = (row["next"].get("end_a") or {}).get("plane_max_residual_mm")
            lines.append(
                f"  residuali piani attuali: uscita={prev_residual}, ingresso={next_residual}"
            )
    lines.append("")

    lines.append("=== INTERPRETAZIONE ===")
    if focus_index is not None:
        lines.append(
            "Debug focalizzato: confronta ATTUALE con le pose ALT del confine sospetto. "
            "Una posa ALT con CL=True e fine minore o uguale indica che il pose/scoring corrente "
            "sta perdendo una soluzione migliore."
        )
        definite_merge_count = 0
        cross_common_count = 0
        checked = 0
    else:
        if definite_merges:
            lines.append(
                "Almeno una coppia del piano attuale può essere compressa in una sola verga. "
                "Questa è una prova concreta che il piano corrente non è ottimo nel numero di verghe."
            )
            lines.append(
                "Il merge pass normale ora ordina le coppie per probabilità di fusione, "
                "quindi le verghe corte vengono controllate prima."
            )
        else:
            lines.append(
                "Nelle coppie approfondite non è stata trovata una fusione completa; "
                "le inefficienze residue possono richiedere uno scambio di pezzi tra due o più verghe."
            )

        if common_candidates:
            lines.append(
                "Esistono common-line tra verghe diverse. Il refine corrente non può spostare "
                "un singolo pezzo da una verga all'altra: riordina solo i pezzi già assegnati alla stessa verga."
            )
        definite_merge_count = len(definite_merges)
        cross_common_count = len(common_candidates)

    return {
        "text": "\n".join(lines),
        "rodCount": rod_count,
        "totalRodPairs": total_pairs,
        "deepPairsChecked": checked,
        "definiteMergeCount": definite_merge_count,
        "crossRodCommonLineCount": cross_common_count,
    }


def _optimize_normalized(
    normalized,
    rod_length,
    dead_zone_mm,
    gap_mm,
):
    total_started = time.perf_counter()
    configured_workers = nesting_cpu_worker_count()
    unique_types = _unique_type_count(normalized)
    contour_heavy = _contains_contour_fit_geometry(normalized)

    seeded_catalog = None
    if contour_heavy:
        seeded_catalog = _precompute_pairwise_catalog(
            normalized,
            gap_mm,
        )

    use_pool = _should_use_process_pool(normalized)
    if contour_heavy:
        contour_worker_limit = (
            CONTOUR_MEMORY_SAFE_CPU_WORKERS
            if (
                len(normalized) >= 48
                or unique_types >= 24
            )
            else CONTOUR_MAX_CPU_WORKERS
        )
        worker_cap = min(
            configured_workers,
            contour_worker_limit,
        )
    else:
        worker_cap = configured_workers
    pool_workers = min(
        worker_cap,
        max(1, len(normalized)),
    )
    _PARALLEL_STATS["item_count"] = len(normalized)
    _PARALLEL_STATS["unique_type_count"] = unique_types
    _PARALLEL_STATS["workers_used"] = pool_workers if use_pool else 1
    _PARALLEL_STATS["used_process_pool"] = bool(use_pool)
    _PARALLEL_STATS["large_contour_job"] = bool(
        contour_heavy
        and len(normalized) >= CONTOUR_LARGE_JOB_MIN_ITEMS
    )
    _PARALLEL_STATS["large_search_job"] = bool(
        len(normalized) >= LARGE_SEARCH_MIN_ITEMS
        and unique_types >= LARGE_SEARCH_MIN_UNIQUE_TYPES
    )
    _PARALLEL_STATS["strategy"] = (
        "precomputed_contour_catalog_process_pool"
        if contour_heavy and use_pool
        else (
            "serial_precomputed_contour_catalog"
            if contour_heavy
            else (
                "process_pool_diverse_types"
                if use_pool
                else (
                    "serial_shared_cache_repeated_types"
                    if len(normalized) >= PARALLEL_MIN_ITEMS
                    and unique_types < PARALLEL_MIN_UNIQUE_TYPES
                    else "serial_small_job"
                )
            )
        )
    )

    executor = None
    try:
        if use_pool:
            _PARALLEL_STATS["pool_runs"] += 1
            executor = ProcessPoolExecutor(
                max_workers=pool_workers,
                initializer=_init_search_worker,
                initargs=(
                    normalized,
                    rod_length,
                    dead_zone_mm,
                    gap_mm,
                    seeded_catalog or {},
                ),
            )

        large_contour_job = bool(
            contour_heavy
            and len(normalized) >= CONTOUR_LARGE_JOB_MIN_ITEMS
        )
        large_search_job = bool(
            len(normalized) >= LARGE_SEARCH_MIN_ITEMS
            and unique_types >= LARGE_SEARCH_MIN_UNIQUE_TYPES
        )
        initial_beam_width = (
            CONTOUR_INITIAL_BEAM_WIDTH
            if large_contour_job
            else DEFAULT_BEAM_WIDTH
        )
        initial_max_types = (
            CONTOUR_INITIAL_MAX_CANDIDATE_TYPES
            if large_contour_job
            else DEFAULT_MAX_CANDIDATE_TYPES
        )

        stage_started = time.perf_counter()
        rods = _build_initial_rods(
            normalized,
            rod_length=rod_length,
            dead_zone_mm=dead_zone_mm,
            gap_mm=gap_mm,
            executor=executor,
            beam_width=initial_beam_width,
            max_candidate_types=initial_max_types,
        )
        _PARALLEL_STATS["initial_seconds"] = (
            time.perf_counter() - stage_started
        )

        stage_started = time.perf_counter()
        rods = _try_merge_rods(
            normalized,
            rods,
            rod_length=rod_length,
            dead_zone_mm=dead_zone_mm,
            gap_mm=gap_mm,
            executor=executor,
            attempt_limit=(
                CONTOUR_MERGE_PAIR_ATTEMPT_LIMIT
                if large_search_job
                else MERGE_PAIR_ATTEMPT_LIMIT
            ),
            beam_width=(
                CONTOUR_MERGE_BEAM_WIDTH
                if large_search_job
                else max(DEFAULT_BEAM_WIDTH * 2, 220)
            ),
            max_candidate_types=(
                CONTOUR_MERGE_MAX_CANDIDATE_TYPES
                if large_search_job
                else 100
            ),
            stitch_only=large_search_job,
        )
        _PARALLEL_STATS["merge_seconds"] = (
            time.perf_counter() - stage_started
        )
        _PARALLEL_STATS["merge_strategy"] = (
            "stitch_existing_sequences"
            if large_search_job
            else "beam_research"
        )

        stage_started = time.perf_counter()
        if large_search_job:
            rods = _refine_rods_fixed_order(
                normalized,
                rods,
                rod_length=rod_length,
                dead_zone_mm=dead_zone_mm,
                gap_mm=gap_mm,
            )
            _PARALLEL_STATS["refine_strategy"] = (
                "fixed_order_pose_dynamic_program"
            )
        else:
            rods = _refine_rods(
                normalized,
                rods,
                rod_length=rod_length,
                dead_zone_mm=dead_zone_mm,
                gap_mm=gap_mm,
                executor=executor,
                beam_width=max(DEFAULT_BEAM_WIDTH * 2, 220),
                max_candidate_types=40,
                utilization_threshold=None,
            )
            _PARALLEL_STATS["refine_strategy"] = "beam_research"
        _PARALLEL_STATS["refine_seconds"] = (
            time.perf_counter() - stage_started
        )
        return rods
    finally:
        if executor is not None:
            executor.shutdown(wait=True)
        _PARALLEL_STATS["total_seconds"] = (
            time.perf_counter() - total_started
        )


def optimize_items_dict(
    items,
    rod_length=DEFAULT_ROD_LENGTH,
    gap_mm=DEFAULT_GAP_MM,
    dead_zone_mm=DEFAULT_CHUCK_DEAD_ZONE,
    algorithm="current",
):
    _reset_parallel_stats()
    _reset_pairwise_fit_stats()
    _PAIRWISE_FIT_CACHE.clear()
    normalized = _normalize_items(items, rod_length)
    if not normalized:
        return []

    algorithm = str(algorithm or "current").strip().lower()
    if algorithm == "global_compaction":
        algorithm = "best_fit"
    if algorithm not in {"current", "best_fit"}:
        algorithm = "current"
    _PARALLEL_STATS["algorithm"] = algorithm

    if algorithm == "best_fit":
        rods = _fast_best_fit_optimize(
            normalized,
            rod_length=float(rod_length),
            dead_zone_mm=float(dead_zone_mm),
            gap_mm=max(0.0, float(gap_mm)),
        )
    else:
        rods = _optimize_normalized(
            normalized,
            rod_length=float(rod_length),
            dead_zone_mm=float(dead_zone_mm),
            gap_mm=max(0.0, float(gap_mm)),
        )
    rods.sort(
        key=lambda state: (
            -round(state.used_span, 6),
            -state.common_lines,
            tuple(normalized[p.item_index].instance_key for p in state.placed),
        )
    )
    return [
        _rod_to_dict(
            state,
            normalized,
            rod_length=float(rod_length),
            dead_zone_mm=float(dead_zone_mm),
            gap_mm=max(0.0, float(gap_mm)),
        )
        for state in rods
    ]

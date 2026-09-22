"""Geometry-aware tube nesting optimizer.

Material usage is the primary objective. Cut-start/toolpath choices are not part
of this optimizer; they are applied only after the nesting pose is frozen.

The search is a bounded beam search because unrestricted tube nesting is
combinatorial. It explores piece order, proper rigid end-for-end reversal and
legal axial rotations, then performs a rod-merge pass to reduce stock count.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
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
MERGE_PAIR_ATTEMPT_LIMIT = 160

_PAIRWISE_FIT_CACHE = {}
_PAIRWISE_FIT_CACHE_LIMIT = 50000


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


def _cached_pairwise_fit(previous_item, previous_pose, item, pose, gap_mm):
    key = _pairwise_fit_key(previous_item, previous_pose, item, pose, gap_mm)
    cached = _PAIRWISE_FIT_CACHE.get(key)
    if cached is not None:
        return cached

    fit = fit_adjacent_parts(
        previous_item.tube_part,
        previous_pose,
        0.0,
        item.tube_part,
        pose,
        gap_mm=gap_mm,
        allow_common_line=True,
    )
    cached = (
        float(fit.next_origin),
        float(fit.minimum_clearance_mm),
        bool(fit.common_line),
        float(fit.slope_delta),
        float(fit.overlap_of_axial_envelopes_mm),
    )

    if len(_PAIRWISE_FIT_CACHE) >= _PAIRWISE_FIT_CACHE_LIMIT:
        _PAIRWISE_FIT_CACHE.pop(next(iter(_PAIRWISE_FIT_CACHE)))
    _PAIRWISE_FIT_CACHE[key] = cached
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
        return _has_planar_ends(self.tube_part)

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


def _has_planar_ends(part):
    if not isinstance(part, dict):
        return False
    ends = list(part.get("ends") or [])
    if len(ends) < 2:
        return False
    for end in ends[:2]:
        plane = end.get("plane_z_equals_c_plus_ax_plus_by") if isinstance(end, dict) else None
        if not plane or len(plane) != 3:
            return False
        if not all(math.isfinite(float(v)) for v in plane):
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
            except (ValueError, TypeError):
                return None
            origin = float(previous_placed.origin) + float(relative_origin)
        else:
            # Geometry-less legacy arrays remain opaque. Keep a conservative
            # configured gap and never claim a common line.
            origin = float(state.used_span) + float(gap_mm)
            common_line = False
            actual_gap = float(gap_mm)
            overlap = 0.0

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
        and int(target_mask).bit_count() <= EXACT_REQUIRE_ALL_MAX_PIECES
    )

    for _depth in range(len(items) + 1):
        next_states = []

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
                        next_states.append(candidate)

        if not next_states:
            break

        best_by_key = {}
        for state in next_states:
            key = _state_dedupe_key(state)
            previous = best_by_key.get(key)
            if previous is None or _partial_score(state) < _partial_score(previous):
                best_by_key[key] = state

        ordered_states = sorted(best_by_key.values(), key=_partial_score)
        beam = ordered_states if exact_mode else ordered_states[:beam_width]

    terminals.extend(beam)

    if require_all:
        terminals = [state for state in terminals if state.used_mask == target_mask]
    else:
        terminals = [state for state in terminals if state.used_mask]

    if not terminals:
        return None
    return min(terminals, key=lambda state: _terminal_score(state, require_all))


def _build_initial_rods(items, rod_length, dead_zone_mm, gap_mm):
    all_mask = (1 << len(items)) - 1
    remaining_mask = all_mask
    rods = []

    while remaining_mask:
        state = _search_single_rod(
            items,
            allowed_mask=remaining_mask,
            rod_length=rod_length,
            dead_zone_mm=dead_zone_mm,
            gap_mm=gap_mm,
            require_all=False,
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


def _try_merge_rods(items, rods, rod_length, dead_zone_mm, gap_mm):
    # Test the most promising rod pairs first instead of raw rod-index order.
    # This prevents a short orphan rod near the end of the list from being
    # skipped just because the group has many earlier rod combinations.
    attempts = 0

    while attempts < MERGE_PAIR_ATTEMPT_LIMIT:
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

        best_merge = None
        for _combined_used, _shortest, union_nominal, i, j in pair_candidates:
            if attempts >= MERGE_PAIR_ATTEMPT_LIMIT:
                break
            attempts += 1

            if union_nominal > float(rod_length) + 3000.0:
                continue

            union_mask = rods[i].used_mask | rods[j].used_mask
            merged = _search_single_rod(
                items,
                allowed_mask=union_mask,
                rod_length=rod_length,
                dead_zone_mm=dead_zone_mm,
                gap_mm=gap_mm,
                require_all=True,
                beam_width=max(DEFAULT_BEAM_WIDTH * 2, 220),
                max_candidate_types=100,
            )
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

def _refine_rods(items, rods, rod_length, dead_zone_mm, gap_mm):
    result = []
    for rod in rods:
        refined = _search_single_rod(
            items,
            allowed_mask=rod.used_mask,
            rod_length=rod_length,
            dead_zone_mm=dead_zone_mm,
            gap_mm=gap_mm,
            require_all=True,
            beam_width=max(DEFAULT_BEAM_WIDTH * 2, 220),
            max_candidate_types=40,
        )
        result.append(refined or rod)
    return result


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
        placement = {
            "instance_key": item.instance_key,
            "source_id": item.source_id,
            "z_start": float(placed.origin),
            "z_end": float(placed.origin + physical_length),
            "occupied_length": float(physical_length),
            "nominal_length": float(item.nominal_length),
            "axial_rotation_degrees": float(_normalize_angle(placed.pose.axial_rotation_degrees)),
            "reversed_end_for_end": bool(placed.pose.reversed_end_for_end),
            "geometry_available": bool(part),
            "part_fingerprint": part.get("part_fingerprint") if part else None,
            "source_sha256": part.get("source_sha256") if part else None,
            "end_a": _posed_end_dict(end_a),
            "end_b": _posed_end_dict(end_b),
            "common_line_before": bool(placed.common_line_before),
            "gap_before_mm": float(placed.gap_before_mm),
            "overlap_before_mm": float(placed.overlap_before_mm),
            "requires_tail_flip": bool(placed.requires_tail_flip),
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
):
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

    rods = _build_initial_rods(
        normalized,
        rod_length=rod_length,
        dead_zone_mm=dead_zone_mm,
        gap_mm=gap_mm,
    )
    rods = _try_merge_rods(
        normalized,
        rods,
        rod_length=rod_length,
        dead_zone_mm=dead_zone_mm,
        gap_mm=gap_mm,
    )
    rods = _refine_rods(
        normalized,
        rods,
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
            except (ValueError, TypeError):
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
        })

    return results


def diagnose_items_dict(
    items,
    rods,
    rod_length=DEFAULT_ROD_LENGTH,
    gap_mm=DEFAULT_GAP_MM,
    dead_zone_mm=DEFAULT_CHUCK_DEAD_ZONE,
    deep_pair_checks=24,
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

    lines = []
    lines.append("=== DEBUG NESTING GEOMETRICO ===")
    lines.append(f"Pezzi liberi: {len(normalized)}")
    lines.append(f"Verghe risultanti: {rod_count}")
    lines.append(f"Gap: {float(gap_mm):g} mm")
    lines.append(f"Beam normale: {DEFAULT_BEAM_WIDTH}")
    lines.append(f"Tipi candidati per livello: {DEFAULT_MAX_CANDIDATE_TYPES}")
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
        lines.append(
            f"Verga {index}: usati {float(rod.get('used') or 0.0):.1f} mm, "
            f"rimasti {float(rod.get('remaining') or 0.0):.1f} mm, "
            f"common-line {int(rod.get('commonLineCount') or 0)}"
        )
        lines.append(f"  {_rod_piece_summary(rod)}")
    lines.append("")

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
    lines.append("")

    lines.append("=== INTERPRETAZIONE ===")
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

    return {
        "text": "\n".join(lines),
        "rodCount": rod_count,
        "totalRodPairs": total_pairs,
        "deepPairsChecked": checked,
        "definiteMergeCount": len(definite_merges),
        "crossRodCommonLineCount": len(common_candidates),
    }


def optimize_items_dict(
    items,
    rod_length=DEFAULT_ROD_LENGTH,
    gap_mm=DEFAULT_GAP_MM,
    dead_zone_mm=DEFAULT_CHUCK_DEAD_ZONE,
):
    normalized = _normalize_items(items, rod_length)
    if not normalized:
        return []

    rods = _build_initial_rods(
        normalized,
        rod_length=float(rod_length),
        dead_zone_mm=float(dead_zone_mm),
        gap_mm=max(0.0, float(gap_mm)),
    )
    rods = _try_merge_rods(
        normalized,
        rods,
        rod_length=float(rod_length),
        dead_zone_mm=float(dead_zone_mm),
        gap_mm=max(0.0, float(gap_mm)),
    )
    rods = _refine_rods(
        normalized,
        rods,
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

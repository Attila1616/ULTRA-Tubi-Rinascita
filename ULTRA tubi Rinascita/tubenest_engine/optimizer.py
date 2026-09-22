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
        return (state.used_mask, None, None, state.rectangle_family, state.tail_used)
    last = state.placed[-1]
    return (
        state.used_mask,
        last.item_index,
        round(_normalize_angle(last.pose.axial_rotation_degrees), 5),
        bool(last.pose.reversed_end_for_end),
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
                fit = fit_adjacent_parts(
                    previous_item.tube_part,
                    previous_placed.pose,
                    previous_placed.origin,
                    item.tube_part,
                    pose,
                    gap_mm=gap_mm,
                    allow_common_line=True,
                )
            except (ValueError, TypeError):
                return None
            origin = float(fit.next_origin)
            common_line = bool(fit.common_line)
            actual_gap = float(fit.minimum_clearance_mm)
            overlap = float(fit.overlap_of_axial_envelopes_mm)
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

        beam = sorted(best_by_key.values(), key=_partial_score)[:beam_width]

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
    # Pairwise merge is intentionally bounded. It catches the common case where
    # geometry/interlocking lets two greedy rods become one without turning the
    # UI refresh into an unbounded combinatorial search.
    attempts = 0
    max_attempts = 80

    while attempts < max_attempts:
        best_merge = None

        for i in range(len(rods)):
            for j in range(i + 1, len(rods)):
                attempts += 1
                if attempts > max_attempts:
                    break

                union_mask = rods[i].used_mask | rods[j].used_mask
                union_nominal = rods[i].nominal_packed + rods[j].nominal_packed

                # Geometry can save substantial material, but a very large
                # combined nominal demand cannot realistically collapse to one
                # 6m stock in this local merge pass.
                if union_nominal > float(rod_length) + 3000.0:
                    continue

                merged = _search_single_rod(
                    items,
                    allowed_mask=union_mask,
                    rod_length=rod_length,
                    dead_zone_mm=dead_zone_mm,
                    gap_mm=gap_mm,
                    require_all=True,
                    beam_width=max(DEFAULT_BEAM_WIDTH * 2, 220),
                    max_candidate_types=40,
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

            if attempts > max_attempts:
                break

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
            tuple(items[p.item_index].instance_key for p in state.placed),
        )
    )
    return rods


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

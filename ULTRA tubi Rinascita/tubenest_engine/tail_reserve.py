"""Tail-first reserved-piece packing for the optional deep nesting action.

Reserved pieces must be genuinely eligible for the chuck-zone flip in at
least one legal pose. The preliminary core rods are nested WITHOUT these
pieces, and the reserved ones are used to finish the resulting rods.
Every proposed arrangement uses the ordinary fixed-sequence geometry
solver; classification itself never authorizes an unsafe cut.
"""
import math
import time

from . import optimizer as core


def tail_reserve_eligible(item, max_length_mm, dead_zone_mm):
    """True only for a short piece with at least one usable tail pose."""
    try:
        limit = float(max_length_mm)
        length = float(item.physical_length)
        if (
            not math.isfinite(limit) or not math.isfinite(length)
            or limit <= 0 or length > limit + core.EPS
            or length <= float(dead_zone_mm) + core.EPS
            or not item.geometry_available
        ):
            return False
        poses = core._first_pose_candidates(item)
        return any(
            core.tail_flip_eligibility(
                item.tube_part, pose, dead_zone_mm=dead_zone_mm
            ).get("eligible", False)
            for pose in poses
        )
    except (ValueError, TypeError, OverflowError, AttributeError):
        return False


def _sequence_from_rods(rods, index_by_key):
    result = []
    for rod in rods:
        seq = []
        for placement in rod.get("placements") or []:
            key = str(placement.get("instance_key") or "")
            if key not in index_by_key:
                raise ValueError("Unknown piece in generated tail-reserve rod.")
            seq.append(index_by_key[key])
        if seq:
            result.append(tuple(seq))
    return result


def reserve_tail_packing_seed(
    raw_pieces, normalized, baseline_rods, fit,
    *, rod_length, dead_zone_mm, gap_mm,
    max_length_mm=5000.0, deadline=None, progress=None,
):
    """Try core-first / tail-last packing and return verified rods or None.

    Intended as an additional deep-search seed, not a replacement for the
    proven existing nesting. If this method cannot find a complete,
    geometrically valid partition in the allowed time, return None.
    """
    if deadline is None:
        deadline = time.perf_counter() + 45.0
    count = len(normalized)
    index_by_key = {item.instance_key: item.index for item in normalized}
    if len(index_by_key) != count:
        return None, {"reservedPieces": 0, "corePieces": count,
                      "reserveSeedStatus": "duplicate-ids"}

    reserved = {
        item.index for item in normalized
        if tail_reserve_eligible(item, max_length_mm, dead_zone_mm)
    }
    stats = {
        "reservedPieces": len(reserved),
        "corePieces": count - len(reserved),
        "reserveThresholdMm": float(max_length_mm),
        "reserveSeedStatus": "no-eligible-pieces",
        "reserveCandidateFits": 0,
        "reservePlacedInCore": 0,
        "reserveCoreRods": 0,
        "reserveSeedRods": 0,
    }
    if not reserved:
        return None, stats
    core_indices = set(range(count)) - reserved
    if progress:
        progress("Selezione pezzi finali", stats)

    # First construct rods from ONLY pieces unable to serve as tails.
    # When all core cuts are planar, use the existing very fast angle-aware
    # nesting engine. For mixed/nonplanar jobs, preserve the baseline piece
    # order instead of launching its potentially expensive beam search.
    core_sequences = None
    if core_indices and all(
        core._planar_fast_path_eligible(normalized[i])
        for i in core_indices
    ) and time.perf_counter() < deadline - 2.0:
        try:
            source_by_key = {
                str(piece.get("instanceKey") or piece.get("instance_key")): piece
                for piece in raw_pieces
            }
            subset = [
                source_by_key[normalized[i].instance_key]
                for i in sorted(core_indices)
            ]
            if len(subset) == len(core_indices):
                core_rods = core.optimize_items_dict(
                    subset, rod_length=rod_length,
                    dead_zone_mm=dead_zone_mm, gap_mm=gap_mm,
                )
                core_sequences = _sequence_from_rods(
                    core_rods, index_by_key
                )
        except (KeyError, ValueError, TypeError, OverflowError):
            core_sequences = None
    if core_sequences is None:
        core_sequences = [
            tuple(
                index_by_key[str(p.get("instance_key"))]
                for p in rod.get("placements") or []
                if index_by_key.get(str(p.get("instance_key"))) in core_indices
            )
            for rod in baseline_rods
        ]
        core_sequences = [seq for seq in core_sequences if seq]

    if (
        sorted(i for seq in core_sequences for i in seq)
        != sorted(core_indices)
    ):
        stats["reserveSeedStatus"] = "core-partition-invalid"
        return None, stats

    rods = []
    for seq in core_sequences:
        if time.perf_counter() >= deadline:
            stats["reserveSeedStatus"] = "time-budget"
            return None, stats
        result = fit(seq)
        if result is None:
            stats["reserveSeedStatus"] = "core-refit-failed"
            return None, stats
        rods.append(result)
    stats["reserveCoreRods"] = len(rods)

    if progress:
        progress("Riempimento finale con pezzi riservati", stats)

    remaining = set(reserved)
    # Refill existing core rods first. Start with fuller rods to target a
    # small remaining gap near the chuck and then work toward shorter rods.
    order = sorted(range(len(rods)), key=lambda i: -rods[i].used_span)
    for rod_i in order:
        while remaining and time.perf_counter() < deadline:
            rod = rods[rod_i]
            old = [p.item_index for p in rod.placed]
            available = max(0.0, float(rod_length) - rod.used_span)
            if available <= core.EPS:
                break
            # Try closest-fitting sizes, including shorter pieces and
            # moderately overlong angled pieces where the end geometry
            # could produce a valid overlap.
            candidates = sorted(
                remaining, key=lambda i: (
                    abs(normalized[i].physical_length - available),
                    normalized[i].physical_length > available,
                    normalized[i].physical_length,
                    i,
                )
            )
            # Prevent dozens of identical copies from consuming the same
            # search budget; each drawing type still has a representative.
            unique_types = set()
            candidate_indices = []
            for i in candidates:
                key = normalized[i].type_key
                if key in unique_types:
                    continue
                unique_types.add(key)
                candidate_indices.append(i)
                if len(candidate_indices) >= 20:
                    break
            best = None
            # Prefer append, which makes the reserved piece the last and
            # permits the protected chuck-zone flip. Also try immediately
            # before the former last piece and interior positions.
            slots = list(dict.fromkeys(
                [len(old), max(0, len(old)-1)] +
                core._fill_interior_slots(rod, normalized, max_slots=6)
            ))
            for piece_i in candidate_indices:
                if time.perf_counter() >= deadline:
                    break
                for position in slots:
                    if time.perf_counter() >= deadline:
                        break
                    seq = old[:position] + [piece_i] + old[position:]
                    result = fit(seq)
                    stats["reserveCandidateFits"] += 1
                    if result is None:
                        continue
                    new_end = result.placed[-1]
                    tail_is_reserved = new_end.item_index in reserved
                    score = (
                        round(rod_length-result.used_span, 5),
                        0 if tail_is_reserved else 1,
                        -result.common_lines,
                        piece_i,
                        position,
                    )
                    if best is None or score < best[0]:
                        best = (score, piece_i, result)
            if best is None:
                break
            _, chosen, result = best
            rods[rod_i] = result
            remaining.remove(chosen)
            stats["reservePlacedInCore"] += 1

    if remaining:
        if progress:
            progress("Completamento delle verghe riservate", stats)
        if time.perf_counter() >= deadline:
            stats["reserveSeedStatus"] = "time-budget"
            return None, stats
        # Remaining eligible pieces still have to be cut. Pack them on
        # additional rods, without risking a missing or duplicated piece.
        try:
            source_by_key = {
                str(p.get("instanceKey") or p.get("instance_key")): p
                for p in raw_pieces
            }
            remaining_raw = [
                source_by_key[normalized[i].instance_key]
                for i in sorted(remaining)
            ]
            tail_rods = core.optimize_items_dict(
                remaining_raw, rod_length=rod_length,
                dead_zone_mm=dead_zone_mm, gap_mm=gap_mm,
            )
            sequences = _sequence_from_rods(tail_rods, index_by_key)
            for sequence in sequences:
                if time.perf_counter() >= deadline:
                    stats["reserveSeedStatus"] = "time-budget"
                    return None, stats
                state = fit(sequence)
                if state is None:
                    stats["reserveSeedStatus"] = "reserved-refit-failed"
                    return None, stats
                rods.append(state)
        except (KeyError, ValueError, TypeError, OverflowError):
            stats["reserveSeedStatus"] = "reserved-pack-failed"
            return None, stats

    ids = [p.item_index for rod in rods for p in rod.placed]
    if sorted(ids) != list(range(count)):
        stats["reserveSeedStatus"] = "invalid-final-assignment"
        return None, stats
    if any(rod.used_span > rod_length + core.EPS for rod in rods):
        stats["reserveSeedStatus"] = "over-length"
        return None, stats
    stats["reserveSeedRods"] = len(rods)
    stats["reserveSeedStatus"] = "complete"
    return rods, stats

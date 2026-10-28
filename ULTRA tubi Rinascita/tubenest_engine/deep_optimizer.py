"""Opt-in bounded deep nesting search; never used by the normal fast pass.

Works on *existing* validated rods. Uses the authoritative fixed-order
geometry solver on each candidate so nominal lengths, angles and chuck
clearance are never used as substitutes for an actual cuttable placement.
"""
import math
import random
import time
from collections import OrderedDict

from . import optimizer as core


def optimize_existing_rods(
    pieces, initial_rods, *, rod_length=6000.0, dead_zone_mm=400.0,
    gap_mm=2.0, time_budget_s=None, progress=None,
    reserve_tail_max_length_mm=5000.0,
):
    started = time.perf_counter()
    normalized = core._normalize_items(pieces, rod_length)
    count = len(normalized)
    if not count:
        return {"rods": [], "stats": {"trials": 0, "elapsedSeconds": 0}}
    budget = (
        min(300.0, max(35.0, 15.0 + 2.3 * count))
        if time_budget_s is None else
        min(300.0, max(0.0, float(time_budget_s)))
    )
    deadline = started + budget
    max_trials = min(28000, max(400, 100 * count))
    index_by_key = {item.instance_key: item.index for item in normalized}
    if len(index_by_key) != count:
        raise ValueError("Instance keys must be unique for deep optimization.")
    sequences = []
    used = []
    for rod in initial_rods or []:
        seq = []
        for p in rod.get("placements") or []:
            key = str(p.get("instance_key") or "")
            if key not in index_by_key:
                raise ValueError(f"Unknown instance in initial nesting: {key}")
            seq.append(index_by_key[key])
            used.append(key)
        if seq:
            sequences.append(tuple(seq))
    if len(used) != count or len(set(used)) != count or set(used) != set(index_by_key):
        raise ValueError("Initial nest must contain every free piece exactly once.")
    if not sequences:
        raise ValueError("No calculated rods to optimize.")

    cache = OrderedDict()
    trials = 0
    tested = 0
    last_report = 0.0

    def fit(seq):
        nonlocal trials
        seq = tuple(seq)
        if not seq:
            return None
        if seq in cache:
            cache.move_to_end(seq)
            return cache[seq]
        if time.perf_counter() >= deadline or trials >= max_trials:
            return None
        trials += 1
        try:
            value = core._optimize_fixed_item_sequence(
                normalized, seq, rod_length, dead_zone_mm, gap_mm
            )
        except (ValueError, TypeError, OverflowError):
            value = None  # Reject this mutation, keep the existing plan.
        cache[seq] = value
        if len(cache) > 2800:
            cache.popitem(last=False)
        return value

    def objective(states):
        # Fewer rods are always better. For equal rod count, consider
        # concentration AND actual used stock. It is often worth spending
        # one extra cutting gap to turn a 5500-mm rod into a ~5800-mm rod
        # and leave one longer, more reusable offcut elsewhere.
        used_span = sum(r.used_span for r in states)
        concentration = core._rod_fill_concentration(states, rod_length)
        return (
            len(states),
            round(used_span - 400.0 * concentration, 4),
            round(used_span, 4),
            -sum(r.common_lines for r in states),
        )

    def emit(stage, current, *, force=False):
        nonlocal last_report
        if progress is None:
            return
        now = time.perf_counter()
        if not force and now-last_report < .8:
            return
        last_report = now
        elapsed = now-started
        progress({
            "stage": stage,
            "elapsedSeconds": round(elapsed, 1),
            "etaSeconds": round(max(0.0, budget-elapsed), 1),
            "percent": round(min(99, 100*elapsed/max(budget, .01)), 1),
            "trials": trials,
            "maxTrials": max_trials,
            "rodCount": len(current),
        })


    guided_trials = 0
    guided_transfers = 0
    exploratory_transfers = 0
    elimination_attempts = 0
    eliminated_rods = 0
    visited = set()

    def state_signature(states):
        # Keep each physical instance in exactly one rod; canonicalize
        # rod ordering so revisiting a permuted plan has no benefit.
        return tuple(sorted(
            tuple(p.item_index for p in rod.placed) for rod in states
        ))

    angle_cache = {
        item.index: core._item_angle_buckets(item)
        for item in normalized
    }

    def candidate_slots(rod, piece_idx=None):
        # Rank *all* useful insertion boundaries by potential matching end
        # angles, then prioritize insertion before the final piece. These
        # are hints only: the exact solver decides whether co-edge is safe.
        base = core._fill_interior_slots(
            rod, normalized, max_slots=12
        )
        if piece_idx is None:
            return base
        cuts = {
            cls for cls in angle_cache.get(piece_idx, ()) if cls is not None
        }
        if not cuts:
            return base
        placements = rod.placed
        count = len(placements)

        def promise(slot):
            matches = 0
            if slot > 0:
                prev = placements[slot-1]
                c = angle_cache.get(prev.item_index, (None, None))
                outgoing = c[0 if prev.pose.reversed_end_for_end else 1]
                matches += int(outgoing in cuts and outgoing is not None)
            if slot < count:
                nxt = placements[slot]
                c = angle_cache.get(nxt.item_index, (None, None))
                incoming = c[1 if nxt.pose.reversed_end_for_end else 0]
                matches += int(incoming in cuts and incoming is not None)
            return (-matches, slot != count-1, abs(slot-(count-1)), slot)

        return sorted(base, key=promise)

    def likely_fit(piece_index, target):
        # Cheap feasibility hint only. Angle overlap may allow a nominally
        # longer piece; allow a generous margin and let the real fitter decide.
        piece = normalized[piece_index]
        profile = (piece.tube_part or {}).get("profile") or {}
        width = max(
            float(profile.get("outside_width") or 0.0),
            float(profile.get("outside_height") or 0.0),
            float(profile.get("outside_diameter") or 0.0),
        )
        ends = (piece.tube_part or {}).get("ends") or []
        inclination = 0.0
        for end in ends[:2]:
            plane = end.get("plane_z_equals_c_plus_ax_plus_by") if isinstance(end,dict) else None
            if isinstance(plane, (tuple,list)) and len(plane) == 3:
                try:
                    inclination = max(inclination, math.hypot(
                        float(plane[1]), float(plane[2])
                    ))
                except (TypeError,ValueError,OverflowError):
                    pass
        allowance = min(1250.0, 4.0*width*inclination+60.0)
        return (
            piece.physical_length
            <= rod_length-target.used_span+allowance
        )

    def directed_gap_transfer(states, *, candidate_limit=240):
        """Systematically test small pieces in the gaps of *every* rod.

        The original search examined only a few candidate donors and at
        most 48 fits, so it frequently never reached a 215-mm piece on
        the short bottom rod. Here, all donor rods are eligible and the
        important insertion immediately BEFORE the last piece is tested
        explicitly, preserving the final piece's tail-flip opportunity.

        Each accepted geometric candidate is still refitted in its new
        sequence. We can prefer filling a nearly full rod even if total
        used length is unchanged (or incurs a tiny extra cutting gap).
        """
        nonlocal guided_trials
        if len(states) < 2:
            return None
        begin = trials
        allowed_extra = max(10.0, gap_mm * 4.0)
        selected = None
        # A fair per-target work budget is essential. Previously the first
        # few full rods consumed the entire 48-trial search, so the 5500-mm
        # rods with a usable 215-mm gap were never visited. Skip only rods
        # which have no candidate that could plausibly fit.
        targets = sorted(
            range(len(states)),
            key=lambda i: (
                0 if 100 <= rod_length-states[i].used_span <= 1400 else 1,
                abs((rod_length-states[i].used_span)-350),
                i,
            ),
        )
        per_target_budget = max(
            12, min(65, candidate_limit // max(1, len(targets)))
        )
        for target_i in targets:
            if trials-begin >= candidate_limit or time.perf_counter() >= deadline:
                break
            target_start_trials = trials
            target = states[target_i]
            target_seq = [p.item_index for p in target.placed]
            empty_space = max(0.0, rod_length - target.used_span)
            if empty_space < 4.0:
                continue

            # Scan every donor, not just the shortest few. Prefer a piece
            # close to the available gap; include smaller pieces because
            # using several of them can be needed to fill a rod.
            possible_pieces = []
            for donor_i, donor in enumerate(states):
                if donor_i == target_i:
                    continue
                for source_pos, placement in enumerate(donor.placed):
                    piece_idx = placement.item_index
                    if not likely_fit(piece_idx, target):
                        continue
                    length = normalized[piece_idx].physical_length
                    possible_pieces.append((
                        (
                            abs(empty_space-length),
                            0 if length <= empty_space + core.EPS else 1,
                            -donor.used_span,
                            length,
                            donor_i,
                            source_pos,
                        ),
                        donor_i, source_pos, piece_idx,
                    ))
            possible_pieces.sort(key=lambda row: row[0])
            # Balanced candidate sampling: exact-gap fits, small parts,
            # pieces from other full rods and pieces from low-filled rods.
            # This avoids burying 215mm copies behind dozens of 400mm
            # copies that happen to be a closer nominal match.
            near = possible_pieces[:min(20, len(possible_pieces))]
            shorter = sorted(possible_pieces,
                             key=lambda x: normalized[x[3]].physical_length)[:12]
            sparse = sorted(possible_pieces,
                            key=lambda x: states[x[1]].used_span)[:10]
            # Keep a representative from the first and last occurrence
            # of each drawing per donor; identical quantity instances do
            # not need to monopolize the same target's test budget.
            options_by_drawing = {}
            for entry in near + shorter + sparse:
                donor_i, source_pos, piece_idx = entry[1:]
                drawing_key = (
                    donor_i, normalized[piece_idx].type_key
                )
                seen = options_by_drawing.setdefault(drawing_key, [])
                if len(seen) < 2:
                    seen.append((donor_i, source_pos, piece_idx))
            donor_options = [
                item for variants in options_by_drawing.values()
                for item in variants
            ]
            for donor_i, source_pos, piece_idx in donor_options:
                if (trials-begin >= candidate_limit
                    or trials-target_start_trials >= per_target_budget
                    or time.perf_counter() >= deadline):
                    break
                donor = states[donor_i]
                donor_seq = [p.item_index for p in donor.placed]
                remainder_seq = (
                    donor_seq[:source_pos] + donor_seq[source_pos+1:]
                )
                # Rank actual matching end-cut angles, but never equate a
                # matching angle with permission to share one laser cut.
                insert_positions = candidate_slots(target, piece_idx)
                for pos in insert_positions:
                    if (trials-begin >= candidate_limit
                        or trials-target_start_trials >= per_target_budget
                        or time.perf_counter() >= deadline):
                        break
                    new_target = fit(
                        target_seq[:pos] + [piece_idx] + target_seq[pos:]
                    )
                    if new_target is None:
                        continue
                    remainder = fit(remainder_seq) if remainder_seq else None
                    if remainder_seq and remainder is None:
                        continue
                    old_used = target.used_span + donor.used_span
                    new_used = new_target.used_span + (
                        remainder.used_span if remainder else 0.0
                    )
                    if new_used > old_used + allowed_extra:
                        continue
                    attempt = list(states)
                    attempt[target_i] = new_target
                    if remainder is None:
                        attempt.pop(donor_i)
                    else:
                        attempt[donor_i] = remainder
                    if state_signature(attempt) in visited:
                        continue
                    # Measure whether the move creates more reusable stock
                    # by making one rod fuller and one donor emptier.
                    new_concentration = (
                        new_target.used_span**2 +
                        (remainder.used_span if remainder else 0.0)**2
                    )
                    old_concentration = (
                        target.used_span**2 + donor.used_span**2
                    )
                    if len(attempt) == len(states) and (
                        new_concentration <= old_concentration + 0.01
                        and new_used >= old_used - 0.05
                    ):
                        continue
                    score = (
                        len(attempt)-len(states),
                        -(new_concentration-old_concentration)
                            / max(1.0, rod_length**2),
                        round(new_used-old_used, 3),
                        abs(rod_length-new_target.used_span),
                        donor_i, target_i, source_pos, pos,
                    )
                    if selected is None or score < selected[0]:
                        selected = (score, attempt)
                # If an excellent candidate is found, keep scanning more
                # donors until budget expires; another move may eliminate a
                # rod entirely rather than just filling one gap.

        guided_trials += trials-begin
        return selected[1] if selected else None

    def absorb_short_rod(states, *, trial_limit=75):
        """Try eliminating a short rod by fitting every piece elsewhere.

        This is a bounded multi-move search. Partial transfers stay private
        until all donor pieces are placed and the stock rod is eliminated.
        """
        nonlocal elimination_attempts, eliminated_rods
        if len(states) < 2:
            return None
        donors = sorted(
            range(len(states)),
            key=lambda i: (states[i].used_span, states[i].piece_count)
        )[:3]
        for donor_i in donors:
            donor = states[donor_i]
            if donor.piece_count > 7:
                continue
            elimination_attempts += 1
            pieces_left = [
                p.item_index for p in donor.placed
            ]
            for reverse_order in (True, False):
                recipients = {
                    i: r for i, r in enumerate(states) if i != donor_i
                }
                possible = True
                start_trials = trials
                for piece_idx in sorted(
                    pieces_left,
                    key=lambda idx: normalized[idx].physical_length,
                    reverse=reverse_order,
                ):
                    best_placement = None
                    target_order = sorted(
                        recipients,
                        key=lambda i: (
                            abs(rod_length-recipients[i].used_span
                                -normalized[piece_idx].physical_length),
                            i
                        )
                    )
                    for target_i in target_order[:min(12, len(target_order))]:
                        target = recipients[target_i]
                        if not likely_fit(piece_idx, target):
                            continue
                        seq = [p.item_index for p in target.placed]
                        for position in candidate_slots(target, piece_idx)[:6]:
                            result = fit(seq[:position]+[piece_idx]+seq[position:])
                            if result is None:
                                continue
                            key = (
                                round(rod_length-result.used_span, 4),
                                -result.common_lines,
                                target_i,
                            )
                            if best_placement is None or key < best_placement[0]:
                                best_placement = (key, target_i, result)
                            if trials-start_trials >= trial_limit:
                                break
                        if trials-start_trials >= trial_limit:
                            break
                    if best_placement is None:
                        possible = False
                        break
                    _, chosen_i, fitted = best_placement
                    recipients[chosen_i] = fitted
                    if time.perf_counter() >= deadline or trials-start_trials >= trial_limit:
                        possible = False
                        break
                if possible:
                    attempt = [recipients[i] for i in sorted(recipients)]
                    if state_signature(attempt) not in visited:
                        eliminated_rods += 1
                        return attempt
        return None

    # Refit baseline; original rod geometry is never replaced by an unverified
    # candidate. If an original sequence cannot be reproduced, fail safely.
    states = []
    for seq in sequences:
        fitted = fit(seq)
        if fitted is None:
            raise ValueError("Cannot geometrically reproduce an existing rod.")
        states.append(fitted)
    best = list(states)
    best_score = objective(best)
    working = list(best)
    working_score = best_score
    baseline_count = len(best)
    baseline_used = sum(r.used_span for r in best)
    rng = random.Random(0xC0ED6E + count)
    no_improve = 0
    visited.add(state_signature(best))
    iteration = 0
    emit("Analisi verghe e geometrie", best, force=True)

    # Experimental core-first, eligible-tail-last construction.
    # This is invoked ONLY by the opt-in deep optimizer, never by the
    # automatic first-pass nesting engine. Keep the original rods as a
    # guaranteed safe fallback even when the new seed uses more stock.
    reserve_stats = {
        "reserveThresholdMm": float(reserve_tail_max_length_mm),
        "reservedPieces": 0,
        "corePieces": count,
        "reserveSeedStatus": "disabled",
        "reserveSeedAccepted": False,
    }
    if (
        float(reserve_tail_max_length_mm) > 0
        and time.perf_counter() < deadline
    ):
        from .tail_reserve import reserve_tail_packing_seed

        def reserve_progress(stage, _status):
            emit(stage, best, force=True)

        reserve_deadline = min(
            deadline, time.perf_counter()
            + min(60.0, max(2.0, budget * 0.22))
        )
        try:
            reserved_plan, reserve_stats = reserve_tail_packing_seed(
                pieces, normalized, initial_rods, fit,
                rod_length=rod_length, dead_zone_mm=dead_zone_mm,
                gap_mm=gap_mm,
                max_length_mm=reserve_tail_max_length_mm,
                deadline=reserve_deadline,
                progress=reserve_progress,
            )
        except (TypeError, ValueError, OverflowError) as exc:
            reserved_plan = None
            reserve_stats["reserveSeedStatus"] = "error: " + str(exc)
        if reserved_plan is not None:
            signature = state_signature(reserved_plan)
            candidate_score = objective(reserved_plan)
            reserve_stats["reserveSeedAccepted"] = candidate_score < best_score
            if candidate_score < best_score:
                best, best_score = list(reserved_plan), candidate_score
                working, working_score = list(reserved_plan), candidate_score
                no_improve = 0
                emit("Miglioramento con pezzi finali riservati", best, force=True)
            else:
                # An initially worse partition can still be a useful start
                # for guided transfers and rod-elimination attempts.
                working, working_score = list(reserved_plan), candidate_score
            visited.add(signature)


    # One outer iteration tests multiple mutations; restarts prevent a local
    # optimum from trapping the optimizer. No exhaustive permutations.
    while trials < max_trials and time.perf_counter() < deadline:
        iteration += 1
        if len(best) == 1 and no_improve > 450:
            break
        if no_improve > 550:
            working = list(best)
            working_score = best_score
            no_improve = 0

        # Directed gap insertion and short-rod elimination get first claim
        # on the search budget. Random swaps/reorders remain for escaping
        # geometry-specific local optima.
        attempt = None
        guided = False
        if len(working) > 1 and iteration % 5 == 0:
            emit("Tentativo eliminazione verga", best)
            attempt = absorb_short_rod(working)
            guided = attempt is not None
        if attempt is None and len(working) > 1 and iteration % 3 != 2:
            emit("Riempimento mirato delle verghe", best)
            attempt = directed_gap_transfer(
                working,
                candidate_limit=min(360, max(100, 5*count)),
            )
            guided = attempt is not None

        # Random fallback may take pieces from *any* rod, including the
        # fuller rods at the top of the list.
        donor_i = rng.randrange(len(working))
        donor = working[donor_i]
        donor_seq = [p.item_index for p in donor.placed]

        # More often relocate than swap, since putting short pieces inside
        # longer rods is the highest-value action for the requested workload.
        operation = rng.choices(
            ("move", "swap", "reorder", "pair_merge"),
            weights=(52, 23, 20, 5),
        )[0]
        if attempt is None and (operation == "reorder" or len(working) == 1):
            if len(donor_seq) >= 3:
                src = rng.randrange(len(donor_seq))
                seq = donor_seq[:src] + donor_seq[src+1:]
                # Prioritize immediately before the final, plus interior slots.
                dest = rng.choice(
                    list(dict.fromkeys(
                        [max(0, len(seq)-1), len(seq)//2, rng.randrange(len(seq)+1)]
                    ))
                )
                seq.insert(dest, donor_seq[src])
                if seq != donor_seq:
                    a = fit(seq)
                    if a is not None:
                        attempt = list(working)
                        attempt[donor_i] = a
        elif attempt is None and len(working) >= 2:
            target_order = sorted(
                (i for i in range(len(working)) if i != donor_i),
                key=lambda i: -working[i].used_span,
            )
            if rng.random() < 0.5:
                target_i = rng.choice(target_order[:min(8, len(target_order))])
            else:
                # Also sample the underfilled bottom rods, not just the
                # fullest targets at the top of the displayed list.
                target_i = rng.choice(target_order[-min(8, len(target_order)):])
            target_seq = [p.item_index for p in working[target_i].placed]
            if operation == "move":
                # Small first, but also sample other pieces for angled-edge
                # compatibility; use all physically available pieces.
                small = sorted(
                    donor_seq, key=lambda i: normalized[i].physical_length
                )
                piece = rng.choice(small[:min(5, len(small))])
                src = donor_seq.index(piece)
                remain = donor_seq[:src]+donor_seq[src+1:]
                rd = fit(remain) if remain else None
                if not remain or rd is not None:
                    pos = rng.choice(list(dict.fromkeys([
                        max(0, len(target_seq)-1),
                        len(target_seq)//2,
                        0, len(target_seq), rng.randrange(len(target_seq)+1),
                    ])))
                    rt = fit(target_seq[:pos]+[piece]+target_seq[pos:])
                    if rt is not None:
                        attempt = list(working)
                        attempt[target_i] = rt
                        if remain:
                            attempt[donor_i] = rd
                        else:
                            attempt.pop(donor_i)
            elif operation == "swap" and donor_seq and target_seq:
                aidx = rng.choice(donor_seq)
                bidx = rng.choice(target_seq)
                apos, bpos = donor_seq.index(aidx), target_seq.index(bidx)
                seq_d = list(donor_seq)
                seq_t = list(target_seq)
                seq_d[apos] = bidx
                seq_t[bpos] = aidx
                rd, rt = fit(seq_d), fit(seq_t)
                if rd is not None and rt is not None:
                    attempt = list(working)
                    attempt[donor_i], attempt[target_i] = rd, rt
            elif operation == "pair_merge" and (
                len(donor_seq) + len(target_seq) <= 17
            ):
                # Several bounded interleavings; one can recover shared
                # angled cuts that neither original sequence exposes.
                combinations = [
                    donor_seq+target_seq, target_seq+donor_seq,
                    target_seq[:max(0,len(target_seq)-1)]
                        + donor_seq + target_seq[max(0,len(target_seq)-1):],
                ]
                for seq in combinations:
                    merged = fit(seq)
                    if merged is not None:
                        attempt = [
                            r for i, r in enumerate(working)
                            if i not in (donor_i, target_i)
                        ] + [merged]
                        break

        if attempt is None:
            no_improve += 1
        else:
            signature = state_signature(attempt)
            if signature in visited:
                no_improve += 1
            else:
                visited.add(signature)
                if len(visited) > 14000:
                    visited.clear()
                    visited.add(signature)
                tested += 1
                score = objective(attempt)
                if guided:
                    guided_transfers += 1
                if score < best_score:
                    best, best_score = list(attempt), score
                    working, working_score = list(attempt), score
                    no_improve = 0
                    emit("Miglioramento trovato", best, force=True)
                elif guided:
                    # This may not reduce material immediately, but placing a
                    # 400 mm piece on an underfilled rod can unlock a later
                    # piece exchange or elimination of another rod.
                    working, working_score = list(attempt), score
                    exploratory_transfers += 1
                    no_improve += 1
                elif score < working_score or rng.random() < .055:
                    working, working_score = list(attempt), score
                    no_improve += 1
                else:
                    no_improve += 1
        emit("Ricerca approfondita", best)

    # Strong final invariant: no piece may have been duplicated or omitted.
    actual = [
        p.item_index for rod in best for p in rod.placed
    ]
    if len(actual) != count or len(set(actual)) != count or set(actual) != set(range(count)):
        raise ValueError("Deep optimization piece assignment verification failed.")
    if any(rod.used_span > rod_length + core.EPS for rod in best):
        raise ValueError("Deep optimization produced an over-length rod.")

    best.sort(key=lambda r: (-r.used_span, -r.common_lines))
    emit("Completato", best, force=True)
    return {
        "rods": [
            core._rod_to_dict(r, normalized, rod_length, dead_zone_mm, gap_mm)
            for r in best
        ],
        "stats": {
            "trials": trials,
            "validCandidates": tested,
            **reserve_stats,
            "guidedTrials": guided_trials,
            "guidedMoves": guided_transfers,
            "exploratoryMoves": exploratory_transfers,
            "rodEliminationAttempts": elimination_attempts,
            "rodEliminationsFound": eliminated_rods,
            "elapsedSeconds": round(time.perf_counter()-started, 2),
            "timeBudgetSeconds": budget,
            "initialRods": baseline_count,
            "finalRods": len(best),
            "initialUsedMm": round(baseline_used, 2),
            "finalUsedMm": round(sum(r.used_span for r in best), 2),
        }
    }

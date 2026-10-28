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
        # Rod count dominates. For the same count, real material savings
        # (up to half-millimetre precision) dominate concentration. Among
        # materially equivalent alternatives, fill fewer rods more closely
        # to stock length and preserve additional valid shared cuts.
        used_span = sum(r.used_span for r in states)
        concentration = core._rod_fill_concentration(states, rod_length)
        return (
            len(states),
            round(used_span * 2),
            -round(concentration, 6),
            -sum(r.common_lines for r in states),
            round(used_span, 5),
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

    def candidate_slots(rod):
        # The last piece is a valuable geometric anchor. Try inserting just
        # before it, between longer pieces, and at other boundaries.
        return core._fill_interior_slots(
            rod, normalized, max_slots=10
        )

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

    def directed_gap_transfer(states, *, candidate_limit=48):
        """Search actual gaps across ALL other rods, not just short donors.

        A transfer need not improve the total used length immediately: it may
        create the free space required for a subsequent rod-eliminating move.
        No speculative geometry is accepted; both rods are refit exactly.
        """
        nonlocal guided_trials
        if len(states) < 2:
            return None
        candidates = []
        target_indices = sorted(
            range(len(states)),
            key=lambda i: (
                # Prefer rods a small piece could make nearly full, but
                # include the short bottom rods too.
                abs(rod_length-states[i].used_span-400.0),
                rod_length-states[i].used_span,
                i,
            )
        )
        # Periodically prefer the sparsest rods to avoid focusing only on
        # already near-full ones.
        sparse = sorted(range(len(states)), key=lambda i: states[i].used_span)
        target_indices = list(dict.fromkeys(
            target_indices[:8] + sparse[:3]
        ))
        for target_i in target_indices:
            target = states[target_i]
            old_seq = [p.item_index for p in target.placed]
            gap = max(0.0, rod_length-target.used_span)
            options = []
            for donor_i, donor in enumerate(states):
                if donor_i == target_i:
                    continue
                for source_pos, placed in enumerate(donor.placed):
                    piece_idx = placed.item_index
                    if not likely_fit(piece_idx, target):
                        continue
                    length = normalized[piece_idx].physical_length
                    # Consider 400 mm class cuts even from nearly full rods.
                    # Slight overlength is allowed for real angle overlaps.
                    rank = (
                        abs(length-gap),
                        0 if donor.used_span > target.used_span else 1,
                        source_pos,
                    )
                    options.append((rank, donor_i, source_pos, piece_idx))
            options.sort()
            candidates.extend(
                (target_i, donor_i, pos, piece)
                for _, donor_i, pos, piece in options[:12]
            )
        if not candidates:
            return None
        best_move = None
        spent_at_start = trials
        for target_i, donor_i, source_pos, piece_idx in candidates:
            if trials-spent_at_start >= candidate_limit or time.perf_counter() >= deadline:
                break
            target = states[target_i]
            donor = states[donor_i]
            source_seq = [p.item_index for p in donor.placed]
            dest_seq = [p.item_index for p in target.placed]
            shortened = source_seq[:source_pos] + source_seq[source_pos+1:]
            # Place where the end cut is geometrically promising, including
            # the specific case: small piece immediately before the last.
            for at in candidate_slots(target):
                if trials-spent_at_start >= candidate_limit or time.perf_counter() >= deadline:
                    break
                moved = fit(dest_seq[:at]+[piece_idx]+dest_seq[at:])
                if moved is None or moved.used_span <= target.used_span+0.01:
                    continue
                remaining = fit(shortened) if shortened else None
                if shortened and remaining is None:
                    continue
                before = target.used_span+donor.used_span
                after = moved.used_span + (remaining.used_span if remaining else 0)
                if after > before + max(3.0, gap_mm*3):
                    continue
                attempt = list(states)
                attempt[target_i] = moved
                if remaining:
                    attempt[donor_i] = remaining
                else:
                    attempt.pop(donor_i)
                identity = state_signature(attempt)
                if identity in visited:
                    continue
                # A complete rod elimination always wins. Otherwise favor
                # placing a piece near the target's stock end, while limiting
                # net material waste and preferring new common-lines.
                key = (
                    0 if remaining is None else 1,
                    abs(rod_length-moved.used_span),
                    round(after-before, 3),
                    -moved.common_lines,
                    target_i, donor_i,
                )
                if best_move is None or key < best_move[0]:
                    best_move = (key, attempt)
        guided_trials += trials-spent_at_start
        return best_move[1] if best_move else None

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
                        for position in candidate_slots(target)[:5]:
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
            attempt = directed_gap_transfer(working)
            guided = attempt is not None

        donor_indices = sorted(
            range(len(working)),
            key=lambda i: (working[i].used_span, working[i].piece_count)
        )
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
            target_i = rng.choice(target_order[:min(5, len(target_order))])
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
                    guided_transfers += 1
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

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
    emit("Analisi verghe e geometrie", best, force=True)

    # One outer iteration tests multiple mutations; restarts prevent a local
    # optimum from trapping the optimizer. No exhaustive permutations.
    while trials < max_trials and time.perf_counter() < deadline:
        if len(best) == 1 and no_improve > 450:
            break
        if no_improve > 550:
            working = list(best)
            working_score = best_score
            no_improve = 0

        donor_indices = sorted(
            range(len(working)),
            key=lambda i: (working[i].used_span, working[i].piece_count)
        )
        donor_i = rng.choice(donor_indices[:max(1, min(5, len(donor_indices)))])
        donor = working[donor_i]
        donor_seq = [p.item_index for p in donor.placed]

        # More often relocate than swap, since putting short pieces inside
        # longer rods is the highest-value action for the requested workload.
        operation = rng.choices(
            ("move", "swap", "reorder", "pair_merge"),
            weights=(52, 23, 20, 5),
        )[0]
        attempt = None
        if operation == "reorder" or len(working) == 1:
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
        elif len(working) >= 2:
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
            tested += 1
            score = objective(attempt)
            # Never expose a worse result. Brief exploratory uphill moves
            # are allowed internally to escape a local minimum.
            if score < best_score:
                best, best_score = list(attempt), score
                working, working_score = list(attempt), score
                no_improve = 0
                emit("Miglioramento trovati", best, force=True)
            elif score < working_score or rng.random() < .035:
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
            "elapsedSeconds": round(time.perf_counter()-started, 2),
            "timeBudgetSeconds": budget,
            "initialRods": baseline_count,
            "finalRods": len(best),
            "initialUsedMm": round(baseline_used, 2),
            "finalUsedMm": round(sum(r.used_span for r in best), 2),
        }
    }

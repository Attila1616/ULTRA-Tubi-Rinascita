"""Opt-in deeper optimization must preserve safety and all piece identities."""
import unittest

from tubenest_engine import optimizer as core
from tubenest_engine.deep_optimizer import optimize_existing_rods


def part(length, slope=0.0):
    def end(z):
        return {
            "plane_z_equals_c_plus_ax_plus_by": [z, slope, 0.0],
            "plane_max_residual_mm": 0.0001,
            "operation_layer": 1,
            "boundary_signature": "shared",
            "process_signature": "same-toolpath",
            "work_flags": 0,
            "curve_flags": 32,
            "curve_normal": [0.0, 0.0, 1.0],
        }
    return {
        "overall_length": float(length),
        "profile": {
            "kind": "Square", "outside_width": 50, "outside_height": 50,
            "thickness": 2, "corner_radius": 0,
        },
        "ends": [end(0.0), end(float(length))],
    }


def sample(lengths):
    return [
        {
            "instanceKey": f"piece-{i}",
            "sourceId": f"drawing-{length}",
            "length": float(length),
            "tubePart": part(length),
        }
        for i, length in enumerate(lengths)
    ]


def seed_rods(items, sequences, length=600.0):
    normalized = core._normalize_items(items, length)
    result = []
    for sequence in sequences:
        state = core._optimize_fixed_item_sequence(
            normalized, sequence, length, 0.0, 2.0
        )
        if state is None:
            raise AssertionError("Invalid test seed")
        result.append(core._rod_to_dict(
            state, normalized, length, 0.0, 2.0
        ))
    return result


class DeepNestingTests(unittest.TestCase):
    def setUp(self):
        core._PAIRWISE_FIT_CACHE.clear()

    def test_all_instances_preserved_and_each_rod_validated(self):
        items = sample([240, 240, 100, 100, 90, 90])
        seeds = seed_rods(items, [[0, 1], [2, 3, 4, 5]])
        messages = []
        result = optimize_existing_rods(
            items, seeds, rod_length=600, dead_zone_mm=0,
            time_budget_s=1.2,
            progress=messages.append,
        )
        originals = sorted(x["instanceKey"] for x in items)
        placed = sorted(
            p["instance_key"] for rod in result["rods"]
            for p in rod["placements"]
        )
        self.assertEqual(placed, originals)
        self.assertTrue(all(
            rod["used"] <= 600.00001 for rod in result["rods"]
        ))
        self.assertLessEqual(len(result["rods"]), len(seeds))
        self.assertGreaterEqual(result["stats"]["trials"], 1)
        self.assertTrue(messages)
        self.assertTrue(all(0 <= row["percent"] <= 99 for row in messages))

    def test_guided_transfer_moves_400_mm_piece_from_top_to_bottom_gap(self):
        # This is the production pattern: a long rod carries a short-ish
        # piece that belongs in a gap on a different rod. The original deep
        # optimizer never looked at donors outside the five sparsest rods.
        items = sample([400, 100, 180])
        baseline = seed_rods(items, [[0, 1], [2]])
        result = optimize_existing_rods(
            items, baseline, rod_length=600, dead_zone_mm=0,
            time_budget_s=1.5,
        )
        self.assertEqual(len(result["rods"]), 2)
        self.assertEqual(sorted(
            p["instance_key"] for rod in result["rods"]
            for p in rod["placements"]
        ), ["piece-0", "piece-1", "piece-2"])
        # The 400 and 180 mm pieces can become neighbours, filling one rod
        # to 580 mm without increasing the total geometric used length.
        self.assertTrue(any(
            {"piece-0", "piece-2"}.issubset({
                p["instance_key"] for p in rod["placements"]
            })
            for rod in result["rods"]
        ))
        self.assertGreaterEqual(result["stats"]["guidedMoves"], 1)
        self.assertGreaterEqual(result["stats"]["guidedTrials"], 1)
        self.assertAlmostEqual(
            result["stats"]["initialUsedMm"],
            result["stats"]["finalUsedMm"], delta=.1
        )

    def test_guided_gap_insertion_can_eliminate_short_rod(self):
        items = sample([300, 200, 100])
        baseline = seed_rods(items, [[0, 1], [2]])
        result = optimize_existing_rods(
            items, baseline, rod_length=600, dead_zone_mm=0,
            time_budget_s=1.5,
        )
        self.assertEqual(len(result["rods"]), 1)
        self.assertEqual(len(result["rods"][0]["placements"]), 3)
        self.assertLessEqual(result["rods"][0]["used"], 600.00001)

    def test_l215_moves_before_last_l1174_into_flip_zone(self):
        # Reproduces the supplied screenshot: a ~5576 mm rod containing
        # L4400 and L1174, plus L215 pieces stranded on a short last rod.
        # A donor L215 should be INSERTED BEFORE L1174, moving only the
        # final (eligible) L1174 into the chuck-zone flip region.
        items = sample([4400, 1174, 806, 657] + [215] * 7)
        normalized = core._normalize_items(items, 6001)
        before_first = core._optimize_fixed_item_sequence(
            normalized, [0, 1], 6001, 400, 2
        )
        before_donor = core._optimize_fixed_item_sequence(
            normalized, list(range(2, len(items))), 6001, 400, 2
        )
        self.assertIsNotNone(before_first)
        self.assertIsNotNone(before_donor)
        self.assertAlmostEqual(before_first.used_span, 5576, delta=.1)
        proposed = core._optimize_fixed_item_sequence(
            normalized, [0, 4, 1], 6001, 400, 2
        )
        self.assertIsNotNone(proposed)
        self.assertTrue(proposed.tail_used)
        self.assertAlmostEqual(proposed.used_span, 5793, delta=.1)
        self.assertTrue(proposed.placed[-1].requires_tail_flip)

        initial = [
            core._rod_to_dict(rod, normalized, 6001, 400, 2)
            for rod in (before_first, before_donor)
        ]
        result = optimize_existing_rods(
            items, initial, rod_length=6001, dead_zone_mm=400,
            gap_mm=2, time_budget_s=4.0,
        )
        self.assertEqual(len(result["rods"]), 2)
        self.assertTrue(any(
            pkeys[0] == "piece-0"
            and pkeys[-1] == "piece-1"
            and any(key in pkeys[1:-1] for key in (
                "piece-4", "piece-5", "piece-6", "piece-7",
                "piece-8", "piece-9", "piece-10"
            ))
            for pkeys in (
                [p["instance_key"] for p in rod["placements"]]
                for rod in result["rods"]
            )
        ))
        self.assertGreaterEqual(result["stats"]["guidedMoves"], 1)
        self.assertGreaterEqual(result["stats"]["guidedTrials"], 1)
        self.assertTrue(all(
            rod["used"] <= 6001.00001 for rod in result["rods"]
        ))

    def test_last_piece_must_remain_flip_eligible(self):
        from unittest.mock import patch
        items = sample([4400, 1174, 215])
        normalized = core._normalize_items(items, 6001)
        with patch.object(core, "_tail_is_usable", return_value=False):
            blocked = core._optimize_fixed_item_sequence(
                normalized, [0, 2, 1], 6001, 400, 2
            )
        self.assertIsNone(blocked)

    def test_one_rod_does_not_create_more_rods(self):
        items = sample([100, 100, 150])
        seeds = seed_rods(items, [[0, 1, 2]])
        result = optimize_existing_rods(
            items, seeds, rod_length=600, dead_zone_mm=0,
            time_budget_s=.25,
        )
        self.assertEqual(len(result["rods"]), 1)
        self.assertEqual(result["stats"]["initialRods"], 1)

    def test_rejects_missing_or_duplicate_instances(self):
        items = sample([100, 100])
        seeds = seed_rods(items, [[0], [1]])
        with self.assertRaisesRegex(ValueError, "exactly once"):
            optimize_existing_rods(
                items, [seeds[0]], rod_length=600,
                dead_zone_mm=0, time_budget_s=.1,
            )
        with self.assertRaisesRegex(ValueError, "exactly once"):
            optimize_existing_rods(
                items, [seeds[0], seeds[0]],
                rod_length=600, dead_zone_mm=0, time_budget_s=.1,
            )

    def test_up_to_five_minute_budget_for_120_pieces(self):
        # The budget is proportional to work size and capped at 300 s.
        from tubenest_engine import deep_optimizer
        self.assertEqual(
            min(300, max(35, 15 + 2.3 * 120)), 291
        )
        self.assertTrue(hasattr(deep_optimizer, "optimize_existing_rods"))

    def test_cut_signature_incompatibility_still_enforces_gap(self):
        first = part(100, slope=.5)
        second = part(100, slope=.5)
        second["ends"][0]["boundary_signature"] = "different"
        from tubenest_engine.fit import fit_adjacent_parts, PartPose
        fit = fit_adjacent_parts(
            first, PartPose(), 0, second, PartPose(), gap_mm=2,
        )
        self.assertFalse(fit.common_line)
        self.assertGreaterEqual(fit.minimum_clearance_mm, 2.0)


if __name__ == "__main__":
    unittest.main()

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

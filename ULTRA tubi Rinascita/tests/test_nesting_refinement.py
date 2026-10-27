"""Bounded endpoint-anchored refinement for the indexed planar optimizer."""
import math
import time
import unittest
from unittest.mock import patch

from tubenest_engine import optimizer as engine


def sample_piece(name, length, start=(0., 0.), end=(0., 0.)):
    def end_cut(z, slope):
        return {
            "plane_z_equals_c_plus_ax_plus_by": [z, *slope],
            "plane_max_residual_mm": 0.0001,
            "operation_layer": 1,
            "boundary_signature": "same",
            "process_signature": "same",
            "work_flags": 0,
            "curve_flags": 32,
            "curve_normal": [0, 0, 1],
        }
    return {
        "part_fingerprint": name,
        "overall_length": length,
        "profile": {
            "kind": "Square",
            "outside_width": 50,
            "outside_height": 50,
            "thickness": 2,
            "corner_radius": 0,
        },
        "ends": [end_cut(0., start), end_cut(length, end)],
    }


def instances(specs):
    data = []
    for name, length, slope, count in specs:
        geometry = sample_piece(
            name, length, start=slope, end=slope
        )
        for i in range(count):
            data.append({
                "instanceKey": f"{name}-{i}",
                "sourceId": name,
                "length": length,
                "tubePart": geometry,
            })
    return engine._normalize_items(data, 600.)


class IndexedRefinementTests(unittest.TestCase):
    def test_bounded_refinement_preserves_every_instance_and_stock_limit(self):
        items = instances([
            ("A", 135, (0., 0.), 16),
            ("B", 210, (0.5, 0.), 12),
            ("C", 330, (0., 0.5), 8),
        ])
        groups = {}
        for item in items:
            groups.setdefault(item.type_key, []).append(item.index)
        classes = {
            key: engine._item_cut_classes(items[indices[0]])
            for key, indices in groups.items()
        }
        angles = {
            key: engine._item_angle_buckets(items[indices[0]])
            for key, indices in groups.items()
        }
        engine._reset_parallel_stats()
        initial = engine._indexed_planar_chain(
            items, 600, 0, 2, groups, classes, angles,
            {}, packing_first=False,
        )
        self.assertIsNotNone(initial)
        refined = engine._refine_indexed_planar_rods(
            items, initial, 600, 0, 2, max_trials=60, time_budget_s=0.6,
        )
        self.assertLessEqual(len(refined), len(initial))
        self.assertTrue(all(rod.used_span <= 600 + 1e-6 for rod in refined))
        self.assertEqual(sorted(
            placed.item_index for rod in refined for placed in rod.placed
        ), list(range(len(items))))
        self.assertLessEqual(engine.get_parallel_stats()["edge_refine_trials"], 60)

    def test_small_piece_is_inserted_between_long_end_pieces(self):
        # A 250mm and 260mm piece leave space for a 60mm piece.
        # Moving it here does NOT reduce the sum of occupied lengths,
        # which is why the previous optimizer rejected this obvious fill.
        items = instances([
            ("front", 250, (0., 0.), 1),
            ("last", 260, (0., 0.), 1),
            ("small", 60, (0., 0.), 1),
            ("other", 100, (0., 0.), 1),
        ])
        first = engine._optimize_fixed_item_sequence(
            items, [0, 1], 600, 0, 2,
        )
        donor = engine._optimize_fixed_item_sequence(
            items, [2, 3], 600, 0, 2,
        )
        self.assertIsNotNone(first)
        self.assertIsNotNone(donor)
        initial = [first, donor]
        original_total = sum(rod.used_span for rod in initial)
        engine._reset_parallel_stats()
        refined = engine._refine_indexed_planar_rods(
            items, initial, 600, 0, 2,
            max_trials=90, time_budget_s=1.5,
        )
        self.assertEqual(len(refined), 2)
        self.assertEqual(
            sorted(p.item_index for rod in refined for p in rod.placed),
            [0, 1, 2, 3],
        )
        filled = next(rod for rod in refined if any(
            p.item_index == 0 for p in rod.placed
        ))
        self.assertEqual([p.item_index for p in filled.placed], [0, 2, 1])
        self.assertGreater(filled.used_span, 570)
        self.assertLessEqual(filled.used_span, 600)
        self.assertAlmostEqual(
            sum(rod.used_span for rod in refined), original_total,
            delta=0.1,
        )
        self.assertGreater(
            engine._rod_fill_concentration(refined, 600),
            engine._rod_fill_concentration(initial, 600),
        )
        self.assertGreaterEqual(
            engine.get_parallel_stats()["edge_fill_transfers"], 1
        )

    def test_fill_priority_prefers_concentrating_equal_length_waste(self):
        items = instances([("p", 100, (0., 0.), 4)])
        a = engine._optimize_fixed_item_sequence(items, [0, 1], 600, 0, 2)
        b = engine._optimize_fixed_item_sequence(items, [2, 3], 600, 0, 2)
        c = engine._optimize_fixed_item_sequence(items, [0, 1, 2], 600, 0, 2)
        d = engine._optimize_fixed_item_sequence(items, [3], 600, 0, 2)
        self.assertGreater(
            engine._rod_fill_concentration([c, d], 600),
            engine._rod_fill_concentration([a, b], 600),
        )

    def test_refiner_absorbs_a_short_rod_by_geometric_insertion(self):
        items = instances([
            ("A", 200, (0., 0.), 2),
            ("B", 150, (0., 0.), 1),
        ])
        initial = [
            engine._optimize_fixed_item_sequence(
                items, [0, 1], rod_length=600,
                dead_zone_mm=0, gap_mm=2,
            ),
            engine._optimize_fixed_item_sequence(
                items, [2], rod_length=600,
                dead_zone_mm=0, gap_mm=2,
            ),
        ]
        self.assertTrue(all(rod is not None for rod in initial))
        refined = engine._refine_indexed_planar_rods(
            items, initial, 600, 0, 2,
            max_trials=80, time_budget_s=1.0,
        )
        self.assertEqual(len(refined), 1)
        self.assertEqual(sorted(p.item_index for p in refined[0].placed),
                         [0, 1, 2])
        self.assertLessEqual(refined[0].used_span, 600)

    def test_physical_tolerance_passes_to_exact_fit(self):
        items = engine._normalize_items([
            {
                "instanceKey": "long",
                "sourceId": "long",
                "length": 6000.5,
                "tubePart": sample_piece("long", 6000.5),
            },
        ], 6001)
        self.assertIsNotNone(
            engine._optimize_fixed_item_sequence(
                items, [0], rod_length=6001,
                dead_zone_mm=0, gap_mm=2,
            )
        )

    def test_refiner_can_be_disabled_without_changing_plan(self):
        items = instances([("A", 100, (0., 0.), 8)])
        engine._reset_parallel_stats()
        original = engine._certified_edge_chain_plan(items, 600, 0, 2)
        result = engine._refine_indexed_planar_rods(
            items, original, 600, 0, 2, max_trials=0, time_budget_s=0.,
        )
        self.assertEqual(
            [[p.item_index for p in rod.placed] for rod in original],
            [[p.item_index for p in rod.placed] for rod in result],
        )

    def test_tail_piece_is_anchored_during_interior_permutations(self):
        items = instances([("A", 100, (0., 0.), 6)])
        state = engine._optimize_fixed_item_sequence(
            items, [0, 1, 2, 3, 4, 5],
            rod_length=600, dead_zone_mm=0, gap_mm=2,
        )
        self.assertIsNotNone(state)
        attempted = []
        real_fit = engine._optimize_fixed_item_sequence

        def trace(_items, sequence, **kwargs):
            attempted.append(tuple(sequence))
            return real_fit(_items, sequence, **kwargs)

        with patch.object(engine, "_optimize_fixed_item_sequence", side_effect=trace):
            engine._refine_indexed_planar_rods(
                items, [state], 600, 0, 2,
                max_trials=35, time_budget_s=0.5,
            )
        self.assertTrue(attempted)
        self.assertTrue(all(seq[0] == 0 and seq[-1] == 5 for seq in attempted))


if __name__ == "__main__":
    unittest.main()

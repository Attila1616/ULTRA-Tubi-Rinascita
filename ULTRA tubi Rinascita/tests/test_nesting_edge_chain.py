"""Regression tests for reusable end-cut classes and fast planar chains."""
import math
import unittest
from unittest.mock import patch

from tubenest_engine.fit import PartPose, fit_adjacent_parts
import tubenest_engine.optimizer as engine


def drawing(length=100.0, *, angled=False, variant="same",
            kind="Square", code="same"):
    slope = 0.3 if angled else 0.0
    def end(z):
        return {
            "plane_z_equals_c_plus_ax_plus_by": [z, slope, 0.0],
            "plane_max_residual_mm": 0.0001,
            "operation_layer": 1,
            "boundary_signature": "geometry-" + variant,
            "process_signature": "verified-cut",
            "work_flags": 0,
            "curve_flags": 32,
            "curve_normal": [0.0, 0.0, 1.0],
        }
    return {
        "part_fingerprint": str((code, length, angled, variant, kind)),
        "overall_length": float(length),
        "profile": {
            "kind": kind,
            "outside_width": 50 if kind == "Square" else 80,
            "outside_height": 50,
            "corner_radius": 0,
            "thickness": 2,
        },
        "ends": [end(0), end(length)],
    }


def batch(n, *, angled=False, kind="Square", length=100.0):
    part = drawing(length, angled=angled, kind=kind)
    return [
        {
            "instanceKey": str(i),
            "sourceId": "same-drawing",
            "length": length,
            "tubePart": part,
        }
        for i in range(n)
    ]


class IndexedCutChainTests(unittest.TestCase):
    def setup_items(self, n=45, **kwargs):
        return engine._normalize_items(batch(n, **kwargs), rod_length=600.0)

    def test_cut_classification_is_reused_and_fits_are_still_validated(self):
        a = self.setup_items(2)
        self.assertEqual(engine._item_cut_classes(a[0]),
                         engine._item_cut_classes(a[1]))
        signature = engine._item_cut_classes(a[0])
        self.assertIsNotNone(signature[0])
        self.assertEqual(signature[0], signature[1])
        fit = fit_adjacent_parts(
            a[0].tube_part, PartPose(), 0.0,
            a[1].tube_part, PartPose(), gap_mm=2
        )
        self.assertTrue(fit.common_line)

    def test_same_angle_but_different_boundary_never_claimed_as_common_line(self):
        a = drawing(variant="a")
        b = drawing(variant="b")
        self.assertNotEqual(
            engine._end_cut_class(a, a["ends"][1]),
            engine._end_cut_class(b, b["ends"][0]),
        )
        fit = fit_adjacent_parts(
            a, PartPose(), 0.0,
            b, PartPose(), gap_mm=2
        )
        self.assertFalse(fit.common_line)
        self.assertEqual(fit.minimum_clearance_mm, 2.0)

    def test_45_identical_straight_pieces_have_certified_minimum_rod_count(self):
        items = self.setup_items()
        engine._reset_parallel_stats()
        rods = engine._certified_edge_chain_plan(
            items, rod_length=600, dead_zone_mm=0, gap_mm=2,
        )
        self.assertIsNotNone(rods)
        self.assertEqual(len(rods), math.ceil(45 * 100 / 600))
        self.assertTrue(engine.get_parallel_stats()["edge_chain_accepted"])
        self.assertEqual(
            sum(len(rod.placed) for rod in rods), 45
        )
        used_indices = [
            placement.item_index
            for rod in rods for placement in rod.placed
        ]
        self.assertEqual(len(set(used_indices)), 45)
        self.assertEqual(set(used_indices), set(range(45)))
        self.assertTrue(all(
            rod.used_mask.bit_count() == len(rod.placed)
            for rod in rods
        ))
        self.assertTrue(all(rod.used_span <= 600 for rod in rods))
        self.assertGreater(
            sum(rod.common_lines for rod in rods), 0
        )

    def test_normal_optimizer_skips_expensive_beam_for_straight_job(self):
        engine._reset_parallel_stats()
        with patch.object(
            engine, "_build_initial_rods",
            side_effect=AssertionError("beam must not run"),
        ), patch.object(
            engine, "_precompute_pairwise_catalog",
            side_effect=AssertionError("catalog must not run"),
        ):
            states = engine.optimize_items(
                batch(45), rod_length=600, gap_mm=2,
                dead_zone_mm=0,
            )
        self.assertEqual(len(states), 8)
        stats = engine.get_parallel_stats()
        self.assertTrue(stats["edge_chain_accepted"])
        self.assertEqual(stats["strategy"], "indexed_any_planar_angle")
        self.assertEqual(stats["pool_runs"], 0)

    def test_angled_identical_ends_also_use_direct_chain(self):
        items = self.setup_items(24, angled=True)
        engine._reset_parallel_stats()
        states = engine._certified_edge_chain_plan(
            items, rod_length=600, dead_zone_mm=0, gap_mm=2,
        )
        self.assertIsNotNone(states)
        self.assertEqual(len(states), 4)
        self.assertEqual(sum(len(rod.placed) for rod in states), 24)
        self.assertEqual({
            placement.item_index
            for rod in states for placement in rod.placed
        }, set(range(24)))
        self.assertGreater(sum(rod.common_lines for rod in states), 0)

    def test_rotated_matching_angles_still_get_common_line_after_real_fit(self):
        prev = drawing(angled=True)
        new = drawing(angled=True)
        new["ends"][0]["plane_z_equals_c_plus_ax_plus_by"] = [0, 0, 0.3]
        fit = fit_adjacent_parts(
            prev, PartPose(0, False), 0,
            new, PartPose(270, False), gap_mm=2,
        )
        self.assertTrue(fit.common_line)
        self.assertEqual(fit.minimum_clearance_mm, 0.0)

    def test_straight_sum_length_bound_is_not_used_on_angled_pieces(self):
        item = self.setup_items(1, angled=True)[0]
        self.assertFalse(engine._has_noninterlocking_straight_ends(item))
        self.assertIsNotNone(
            engine._uniform_angular_lower_bound(
                self.setup_items(10, angled=True), 600.0, 2.0
            )
        )

    def test_round_angular_proof_is_skipped_because_orientations_are_continuous(self):
        items = self.setup_items(10, angled=True, kind="Circle")
        self.assertIsNone(
            engine._uniform_angular_lower_bound(items, 600.0, 2.0)
        )

    def test_incomplete_signatures_are_only_priority_hints(self):
        part = drawing()
        part["ends"][0]["boundary_signature"] = None
        self.assertIsNone(engine._end_cut_class(part, part["ends"][0]))

    def test_mixed_unprovable_plan_preserves_fallback(self):
        items = self.setup_items(10, angled=True)
        items[1].source_id = "other-drawing"
        self.assertIsNone(
            engine._uniform_angular_lower_bound(items, 600, 2)
        )

    def test_one_rod_is_a_valid_general_rod_count_certificate(self):
        items = self.setup_items(6, angled=True)
        engine._reset_parallel_stats()
        rods = engine._certified_edge_chain_plan(
            items, rod_length=800, dead_zone_mm=0, gap_mm=2
        )
        self.assertIsNotNone(rods)
        self.assertEqual(len(rods), 1)
        self.assertEqual(len(rods[0].placed), 6)


if __name__ == "__main__":
    unittest.main()

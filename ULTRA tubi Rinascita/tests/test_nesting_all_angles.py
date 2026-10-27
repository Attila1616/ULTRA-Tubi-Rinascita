"""End-angle agnostic nesting, with validated co-edges and no beam fallback.

Every result still passes the standard geometric adjacency and tail checks.
"""
import math
import unittest
from unittest.mock import patch

from tubenest_engine.fit import PartPose, fit_adjacent_parts
import tubenest_engine.optimizer as engine


def part(code, length=100.0, first=(0.0, 0.0), last=(0.0, 0.0),
         kind="Square", signature="same"):
    def end(c, slopes):
        return {
            "plane_z_equals_c_plus_ax_plus_by": [
                float(c), float(slopes[0]), float(slopes[1])
            ],
            "plane_max_residual_mm": 0.0001,
            "operation_layer": 1,
            "boundary_signature": signature,
            "process_signature": "cut",
            "work_flags": 0,
            "curve_flags": 32,
            "curve_normal": [0.0, 0.0, 1.0],
        }
    return {
        "part_fingerprint": code,
        "overall_length": length,
        "profile": {
            "kind": kind,
            "outside_width": 50.0,
            "outside_height": 50.0,
            "outside_diameter": 50.0,
            "corner_radius": 0.0,
            "thickness": 2.0,
        },
        "ends": [end(0, first), end(length, last)],
    }


def make_job(specs):
    result = []
    for index, (code, geometry, count) in enumerate(specs):
        for n in range(count):
            result.append({
                "instanceKey": code + "-" + str(n),
                "sourceId": code,
                "length": geometry["overall_length"],
                "tubePart": geometry,
            })
    return result


class ArbitraryAngleFastChainTests(unittest.TestCase):
    def setUp(self):
        engine._PAIRWISE_FIT_CACHE.clear()
        engine._reset_parallel_stats()

    def test_all_slope_angles_are_fast_path_eligible(self):
        for angle in (0, 15, 23, 30, 45, 60, 73):
            with self.subTest(angle=angle):
                slope = math.tan(math.radians(angle))
                geometry = part("test", last=(slope, 0))
                normalized = engine._normalize_items(
                    make_job([("a", geometry, 1)]), 600
                )
                self.assertTrue(
                    engine._planar_fast_path_eligible(normalized[0])
                )

    def test_rotated_45_degree_square_end_is_reused(self):
        slope = math.tan(math.radians(45))
        a = part("a", first=(0, 0), last=(slope, 0))
        b = part("b", first=(0, slope), last=(0, 0))
        a_item, b_item = engine._normalize_items(
            make_job([("a", a, 1), ("b", b, 1)]), 600
        )
        poses = engine._next_pose_candidates(
            a_item, PartPose(), b_item, None
        )
        self.assertEqual(poses[0], PartPose(270.0, False))
        fit = fit_adjacent_parts(
            a, PartPose(), 0.0, b, poses[0], gap_mm=2
        )
        self.assertTrue(fit.common_line)

    def test_rotated_23_degree_round_end_matches_any_rotation(self):
        slope = math.tan(math.radians(23))
        a = part("a", first=(0, 0), last=(slope, 0), kind="Circle")
        b = part("b", first=(
            slope * math.cos(math.radians(17)),
            slope * math.sin(math.radians(17))
        ), last=(0, 0), kind="Circle")
        first, second = engine._normalize_items(
            make_job([("a", a, 1), ("b", b, 1)]), 600
        )
        poses = engine._next_pose_candidates(
            first, PartPose(), second, None
        )
        self.assertTrue(any(
            abs(p.axial_rotation_degrees - 343) < 1e-5
            for p in poses
        ))
        matching = next(
            p for p in poses
            if abs(p.axial_rotation_degrees - 343) < 1e-5
        )
        self.assertTrue(fit_adjacent_parts(
            a, PartPose(), 0, b, matching, gap_mm=2
        ).common_line)

    def test_unproven_mixed_angled_job_skips_full_beam_search(self):
        slope = math.tan(math.radians(37))
        first = part("a", length=100, first=(slope, 0), last=(slope, 0))
        second = part("b", length=90, first=(0, slope), last=(0, slope))
        job = make_job([("a", first, 18), ("b", second, 18)])
        with patch.object(
            engine, "_build_initial_rods",
            side_effect=AssertionError("beam search should not start"),
        ):
            rods = engine.optimize_items_dict(
                job, rod_length=600, gap_mm=2, dead_zone_mm=0
            )
        self.assertGreater(len(rods), 1)
        stats = engine.get_parallel_stats()
        self.assertTrue(stats["edge_chain_accepted"])
        self.assertEqual(stats["strategy"], "indexed_any_planar_angle")
        self.assertGreater(stats["edge_catalog_entries"], 0)
        self.assertGreater(stats["edge_catalog_hits"], 0)
        placements = [
            p["instance_key"] for rod in rods
            for p in rod["placements"]
        ]
        self.assertEqual(len(placements), 36)
        self.assertEqual(len(set(placements)), 36)

    def test_50_mixed_straight_and_oblique_pieces_do_not_run_beam(self):
        p1 = part("straight", length=135)
        p2 = part("oblique", length=205,
                  first=(math.tan(math.radians(30)), 0),
                  last=(0, math.tan(math.radians(30))))
        job = make_job([("straight", p1, 30), ("oblique", p2, 20)])
        with patch.object(
            engine, "_build_initial_rods",
            side_effect=AssertionError("beam search should not start"),
        ):
            rods = engine.optimize_items_dict(
                job, rod_length=600, gap_mm=2, dead_zone_mm=0
            )
        self.assertGreaterEqual(len(rods), 1)
        self.assertEqual(
            sum(len(rod["placements"]) for rod in rods), 50
        )

    def test_different_cut_signatures_do_not_enable_common_line(self):
        a = part("a", signature="a")
        b = part("b", signature="b")
        fit = fit_adjacent_parts(
            a, PartPose(), 0.0, b, PartPose(), gap_mm=2
        )
        self.assertFalse(fit.common_line)
        self.assertEqual(fit.minimum_clearance_mm, 2.0)

    def test_nonplanar_data_still_uses_old_optimizer(self):
        geometry = part("complex")
        geometry["ends"][0]["plane_z_equals_c_plus_ax_plus_by"] = None
        job = engine._normalize_items(
            make_job([("complex", geometry, 6)]), 600
        )
        self.assertIsNone(
            engine._certified_edge_chain_plan(job, 600, 0, 2)
        )

    def test_tube_end_limit_is_enforced_even_with_matching_angles(self):
        slope = math.tan(math.radians(45))
        geometry = part("angled", length=270,
                        first=(slope, 0), last=(slope, 0))
        job = make_job([("a", geometry, 7)])
        rods = engine.optimize_items_dict(
            job, rod_length=600, gap_mm=2, dead_zone_mm=0
        )
        self.assertGreaterEqual(len(rods), 4)
        self.assertTrue(all(
            rod["used"] <= 600 + 1e-5 for rod in rods
        ))

    def test_reuse_transitions_does_not_skip_actual_verification(self):
        geometry = part("repeat", length=100)
        job = make_job([("repeat", geometry, 18)])
        rods = engine.optimize_items_dict(
            job, rod_length=600, gap_mm=2, dead_zone_mm=0
        )
        self.assertGreaterEqual(len(rods), 3)
        self.assertGreater(
            engine.get_parallel_stats()["edge_catalog_hits"], 0
        )
        self.assertTrue(all(
            p["common_line_before"] in (True, False)
            for rod in rods for p in rod["placements"]
        ))


if __name__ == "__main__":
    unittest.main()

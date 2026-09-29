import unittest

import tubenest_engine.optimizer as optimizer_module
from tubenest_engine.optimizer import diagnose_items_dict, optimize_items_dict


def make_part(
    length,
    kind="Square",
    width=100.0,
    height=100.0,
    start_plane=(0.0, 0.0, 0.0),
    end_plane=None,
    features=None,
):
    if end_plane is None:
        end_plane = (float(length), 0.0, 0.0)

    profile = {
        "kind": kind,
        "outside_width": float(width),
        "outside_height": float(height),
        "outside_diameter": float(width) if kind == "Circle" else None,
        "corner_radius": 0.0,
        "thickness": 2.0,
    }
    return {
        "overall_length": float(length),
        "profile": profile,
        "part_fingerprint": f"{kind}-{length}-{start_plane}-{end_plane}",
        "source_sha256": "a" * 64,
        "ends": [
            {
                "label": "A",
                "operation_layer": 1,
                "plane_z_equals_c_plus_ax_plus_by": list(start_plane),
                "plane_max_residual_mm": 0.0,
                "boundary_signature": "end-A",
                "process_signature": "process-1",
                "work_flags": 0,
                "curve_flags": 34,
                "curve_normal": [0.0, 1.0, 0.0],
            },
            {
                "label": "B",
                "operation_layer": 1,
                "plane_z_equals_c_plus_ax_plus_by": list(end_plane),
                "plane_max_residual_mm": 0.0,
                "boundary_signature": "end-B",
                "process_signature": "process-1",
                "work_flags": 0,
                "curve_flags": 34,
                "curve_normal": [0.0, 1.0, 0.0],
            },
        ],
        "features": features or [],
    }


def item(key, length, part):
    return {
        "instanceKey": key,
        "sourceId": key,
        "length": float(length),
        "tubePart": part,
    }


def make_nonplanar_v_part(length=984.0, sample_count=360):
    import math
    part = make_part(
        length,
        start_plane=(75.0, 0.0, 1.0),
        end_plane=(length - 75.0, 0.0, -1.0),
    )
    for end in part["ends"]:
        end["plane_max_residual_mm"] = 8.0

    start = []
    finish = []
    for index in range(sample_count):
        angle = 2.0 * math.pi * index / sample_count
        wave = 75.0 * math.sin(angle)
        start.append([75.0 + wave, 75.0 + wave])
        finish.append([
            length - 75.0 - wave,
            length - 75.0 - wave,
        ])
    part["ends"][0]["perimeter_envelope"] = start
    part["ends"][1]["perimeter_envelope"] = finish
    return part


class GeometryOptimizerTests(unittest.TestCase):
    def test_stitch_merge_replays_existing_sequences(self):
        part = make_nonplanar_v_part()
        pieces = [
            {
                "instanceKey": f"S::{index}",
                "sourceId": "same-v-cut",
                "length": 984.0,
                "tubePart": part,
            }
            for index in range(4)
        ]
        normalized = optimizer_module._normalize_items(
            pieces,
            6000.0,
        )
        first = optimizer_module._search_single_rod(
            normalized,
            allowed_mask=(1 << 0) | (1 << 1),
            rod_length=6000.0,
            dead_zone_mm=400.0,
            gap_mm=2.0,
            require_all=True,
        )
        second = optimizer_module._search_single_rod(
            normalized,
            allowed_mask=(1 << 2) | (1 << 3),
            rod_length=6000.0,
            dead_zone_mm=400.0,
            gap_mm=2.0,
            require_all=True,
        )
        merged = optimizer_module._stitch_rod_states(
            normalized,
            first,
            second,
            6000.0,
            400.0,
            2.0,
        )
        self.assertIsNotNone(merged)
        self.assertEqual(merged.used_mask.bit_count(), 4)
        self.assertLess(merged.used_span, 3600.0)

    def test_repeated_nonplanar_v_cuts_choose_interlocking_rotation(self):
        part = make_nonplanar_v_part()
        pieces = [
            {
                "instanceKey": f"V::{index}",
                "sourceId": "same-v-cut",
                "length": 984.0,
                "tubePart": part,
            }
            for index in range(4)
        ]

        rods = optimize_items_dict(
            pieces,
            rod_length=6000.0,
            gap_mm=2.0,
            dead_zone_mm=400.0,
        )

        self.assertEqual(len(rods), 1)
        self.assertLess(rods[0]["used"], 3600.0)
        overlaps = [
            placement["overlap_before_mm"]
            for placement in rods[0]["placements"][1:]
        ]
        self.assertTrue(all(value > 140.0 for value in overlaps))
        rotations = {
            round(placement["axial_rotation_degrees"]) % 360
            for placement in rods[0]["placements"]
        }
        self.assertTrue({0, 180}.issubset(rotations))

    def test_angled_45_and_30_ends_interlock_with_two_mm_surface_gap(self):
        previous = make_part(
            1000,
            start_plane=(0.0, 0.0, 0.0),
            end_plane=(950.0, 0.0, 1.0),
        )
        following = make_part(
            1000,
            start_plane=(50.0, 0.0, 0.5773502691896257),
            end_plane=(1000.0, 0.0, 0.0),
        )

        rods = optimize_items_dict(
            [item("A", 1000, previous), item("B", 1000, following)],
            rod_length=6000,
            gap_mm=2.0,
            dead_zone_mm=400,
        )

        self.assertEqual(len(rods), 1)
        self.assertLess(rods[0]["used"], 2002.0)
        second = rods[0]["placements"][1]
        self.assertAlmostEqual(second["gap_before_mm"], 2.0, places=6)
        self.assertGreater(second["overlap_before_mm"], 0.0)

    def test_matching_faces_use_common_line(self):
        a = make_part(
            1000,
            start_plane=(0.0, 0.0, 0.0),
            end_plane=(950.0, 0.0, 1.0),
        )
        b = make_part(
            1000,
            start_plane=(50.0, 0.0, 1.0),
            end_plane=(1000.0, 0.0, 0.0),
        )
        a["ends"][1]["boundary_signature"] = "shared-boundary"
        b["ends"][0]["boundary_signature"] = "shared-boundary"

        rods = optimize_items_dict(
            [item("A", 1000, a), item("B", 1000, b)],
            gap_mm=2.0,
        )

        self.assertEqual(len(rods), 1)
        self.assertEqual(rods[0]["commonLineCount"], 1)
        self.assertTrue(rods[0]["placements"][1]["common_line_before"])
        self.assertEqual(rods[0]["placements"][1]["gap_before_mm"], 0.0)

    def test_rectangles_keep_one_orientation_family_per_rod(self):
        part = make_part(
            1000,
            kind="Rect",
            width=150,
            height=50,
            start_plane=(0.0, 0.0, 0.5),
            end_plane=(975.0, 0.0, -0.5),
        )
        rods = optimize_items_dict(
            [item("A", 1000, part), item("B", 1000, part), item("C", 1000, part)],
            gap_mm=2.0,
        )

        self.assertEqual(len(rods), 1)
        family = rods[0]["rectangleOrientationFamily"]
        self.assertIn(family, (0, 90))
        for placement in rods[0]["placements"]:
            rotation = round(placement["axial_rotation_degrees"]) % 180
            self.assertEqual(rotation, family)

    def test_tail_flip_can_use_last_400mm(self):
        long_piece = make_part(5200)
        tail_piece = make_part(700)

        rods = optimize_items_dict(
            [item("LONG", 5200, long_piece), item("TAIL", 700, tail_piece)],
            gap_mm=2.0,
        )

        self.assertEqual(len(rods), 1)
        self.assertGreater(rods[0]["used"], 5600.0)
        self.assertLessEqual(rods[0]["used"], 6000.0)
        self.assertTrue(rods[0]["usesTailFlip"])
        self.assertTrue(rods[0]["placements"][-1]["requires_tail_flip"])

    def test_ineligible_tail_does_not_enter_chuck_zone(self):
        # Both pieces have angled ends, so neither can become a valid flip-tail
        # regardless of optimizer ordering or end-for-end reversal.
        long_piece = make_part(
            5300,
            start_plane=(25.0, 0.0, 0.5),
            end_plane=(5275.0, 0.0, -0.5),
        )
        bad_tail = make_part(
            700,
            start_plane=(25.0, 0.0, 0.5),
            end_plane=(675.0, 0.0, -0.5),
        )

        rods = optimize_items_dict(
            [item("LONG", 5300, long_piece), item("BAD", 700, bad_tail)],
            gap_mm=2.0,
        )

        self.assertEqual(len(rods), 2)
        self.assertTrue(all(rod["used"] <= 5600.0 + 1e-6 for rod in rods))

    def test_parallel_threshold_targets_medium_groups(self):
        self.assertEqual(optimizer_module.PARALLEL_MIN_ITEMS, 8)
        self.assertEqual(optimizer_module.PARALLEL_MIN_UNIQUE_TYPES, 6)
        self.assertEqual(optimizer_module.MAX_CPU_WORKERS, 24)

    def test_repeat_heavy_group_uses_shared_serial_cache_strategy(self):
        repeated = [
            optimizer_module.OptimizerItem(
                index=index,
                instance_key=f"A::{index}",
                source_id="A",
                nominal_length=100.0,
                tube_part=make_part(100.0),
            )
            for index in range(20)
        ]
        self.assertFalse(
            optimizer_module._should_use_process_pool(repeated)
        )

    def test_contour_catalog_precomputes_successes_and_failures(self):
        items = [
            optimizer_module.OptimizerItem(
                index=index,
                instance_key=f"NP::{index}",
                source_id=f"NP{index}",
                nominal_length=984.0 + index,
                tube_part=make_nonplanar_v_part(length=984.0 + index),
            )
            for index in range(3)
        ]
        optimizer_module._PAIRWISE_FIT_CACHE.clear()
        catalog = optimizer_module._precompute_pairwise_catalog(
            items,
            2.0,
        )
        self.assertTrue(catalog)
        before = optimizer_module._PAIRWISE_FIT_STATS["misses"]
        previous = items[0]
        following = items[1]
        previous_pose = optimizer_module._first_pose_candidates(previous)[0]
        next_pose = optimizer_module._next_pose_candidates(
            previous,
            previous_pose,
            following,
            None,
        )[0]
        optimizer_module._cached_pairwise_fit(
            previous,
            previous_pose,
            following,
            next_pose,
            2.0,
        )
        self.assertEqual(
            optimizer_module._PAIRWISE_FIT_STATS["misses"],
            before,
        )

    def test_diverse_group_can_use_process_pool_strategy(self):
        diverse = [
            optimizer_module.OptimizerItem(
                index=index,
                instance_key=f"P::{index}",
                source_id=f"P{index}",
                nominal_length=100.0 + index,
                tube_part=make_part(100.0 + index),
            )
            for index in range(8)
        ]
        expected = optimizer_module.nesting_cpu_worker_count() > 1
        self.assertEqual(
            optimizer_module._should_use_process_pool(diverse),
            expected,
        )

    def test_pairwise_geometry_fit_cache_is_reused(self):
        optimizer_module._PAIRWISE_FIT_CACHE.clear()
        a = make_part(
            1000,
            start_plane=(0.0, 0.0, 0.0),
            end_plane=(950.0, 0.0, 1.0),
        )
        b = make_part(
            1000,
            start_plane=(50.0, 0.0, 0.5773502691896257),
            end_plane=(1000.0, 0.0, 0.0),
        )
        pieces = [item("A", 1000, a), item("B", 1000, b)]

        first = optimize_items_dict(pieces, gap_mm=2.0)
        cache_size_after_first = len(optimizer_module._PAIRWISE_FIT_CACHE)
        second = optimize_items_dict(pieces, gap_mm=2.0)
        cache_size_after_second = len(optimizer_module._PAIRWISE_FIT_CACHE)

        self.assertGreater(cache_size_after_first, 0)
        self.assertEqual(cache_size_after_first, cache_size_after_second)
        self.assertEqual(first, second)

    def test_small_required_merge_ignores_beam_truncation(self):
        parts = [
            item("A", 1110, make_part(1110, start_plane=(25.0, 0.0, 0.5), end_plane=(1110.0, 0.0, 0.0))),
            item("B", 722, make_part(722)),
            item("C", 290, make_part(290)),
            item("D", 810, make_part(810, start_plane=(25.0, 0.0, 0.5), end_plane=(785.0, 0.0, -0.5))),
            item("E", 810, make_part(810, start_plane=(25.0, 0.0, 0.5), end_plane=(785.0, 0.0, -0.5))),
        ]
        normalized = optimizer_module._normalize_items(parts, 6000)
        mask = (1 << len(normalized)) - 1

        result = optimizer_module._search_single_rod(
            normalized,
            allowed_mask=mask,
            rod_length=6000,
            dead_zone_mm=400,
            gap_mm=2.0,
            require_all=True,
            beam_width=1,
            max_candidate_types=1,
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.used_mask, mask)
        self.assertLess(result.used_span, 5600.0)

    def test_diagnostic_reports_definite_cross_rod_merge(self):
        a = make_part(1000)
        b = make_part(1000)
        pieces = [item("A", 1000, a), item("B", 1000, b)]

        # Deliberately inefficient current plan: one short piece per rod.
        rods = [
            {
                "used": 1000.0,
                "remaining": 5000.0,
                "commonLineCount": 0,
                "placements": [{
                    "instance_key": "A",
                    "nominal_length": 1000.0,
                    "occupied_length": 1000.0,
                    "end_a": {"angle_from_perpendicular_degrees": 0.0},
                    "end_b": {"angle_from_perpendicular_degrees": 0.0},
                }],
            },
            {
                "used": 1000.0,
                "remaining": 5000.0,
                "commonLineCount": 0,
                "placements": [{
                    "instance_key": "B",
                    "nominal_length": 1000.0,
                    "occupied_length": 1000.0,
                    "end_a": {"angle_from_perpendicular_degrees": 0.0},
                    "end_b": {"angle_from_perpendicular_degrees": 0.0},
                }],
            },
        ]

        report = diagnose_items_dict(
            pieces,
            rods,
            gap_mm=2.0,
            deep_pair_checks=4,
        )

        self.assertEqual(report["definiteMergeCount"], 1)
        self.assertIn("PUÒ DIVENTARE 1 VERGA", report["text"])

    def test_nonplanar_boundaries_fall_back_instead_of_splitting_short_job(self):
        pieces = []
        for i in range(8):
            part = make_part(69)
            part["ends"][0]["plane_max_residual_mm"] = 0.2
            part["ends"][1]["plane_max_residual_mm"] = 0.2
            pieces.append(item(f"S{i}", 69, part))

        for i in range(2):
            part = make_part(1450)
            part["ends"][0]["plane_max_residual_mm"] = 0.2
            part["ends"][1]["plane_max_residual_mm"] = 0.2
            pieces.append(item(f"L{i}", 1450, part))

        rods = optimize_items_dict(
            pieces,
            rod_length=6000,
            gap_mm=2.0,
            dead_zone_mm=400,
        )

        self.assertEqual(len(rods), 1)
        self.assertEqual(len(rods[0]["placements"]), 10)
        self.assertLess(rods[0]["used"], 5600.0)
        self.assertTrue(
            any(
                placement.get("geometry_fallback_before")
                for placement in rods[0]["placements"][1:]
            )
        )
        self.assertEqual(rods[0]["commonLineCount"], 0)

    def test_tail_feature_inside_last_400_blocks_flip(self):
        # Make the long piece ineligible too, otherwise the optimizer could
        # simply reorder and use it as the flip-tail instead.
        long_piece = make_part(
            5300,
            start_plane=(25.0, 0.0, 0.5),
            end_plane=(5275.0, 0.0, -0.5),
        )
        dirty_tail = make_part(
            700,
            features=[{
                "shape_handle": 99,
                "feature_type": "cut",
                "sampled_bounds": [[-1, -1, 300], [1, 1, 400]],
            }],
        )

        rods = optimize_items_dict(
            [item("LONG", 5300, long_piece), item("DIRTY", 700, dirty_tail)],
            gap_mm=2.0,
        )

        self.assertEqual(len(rods), 2)


if __name__ == "__main__":
    unittest.main()

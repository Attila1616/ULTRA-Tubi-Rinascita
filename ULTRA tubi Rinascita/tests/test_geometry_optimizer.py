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
            },
            {
                "label": "B",
                "operation_layer": 1,
                "plane_z_equals_c_plus_ax_plus_by": list(end_plane),
                "plane_max_residual_mm": 0.0,
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


class GeometryOptimizerTests(unittest.TestCase):
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

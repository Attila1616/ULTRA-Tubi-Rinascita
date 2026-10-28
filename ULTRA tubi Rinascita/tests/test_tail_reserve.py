"""Opt-in tail reservation: length cutoff, chuck safety and core-first packing."""
import time
import unittest

from tubenest_engine import optimizer as core
from tubenest_engine.tail_reserve import (
    tail_reserve_eligible, reserve_tail_packing_seed,
)


def piece(name, length, first_slope=0.0, last_slope=0.0, features=None):
    def end(z, slope):
        return {
            "plane_z_equals_c_plus_ax_plus_by": [float(z), slope, 0.0],
            "plane_max_residual_mm": 0.0001,
            "operation_layer": 1,
            "boundary_signature": "cut-" + name,
            "process_signature": "laser",
            "work_flags": 0,
            "curve_flags": 32,
            "curve_normal": [0., 0., 1.],
        }
    geometry = {
        "overall_length": float(length),
        "profile": {
            "kind": "Square", "outside_width": 50,
            "outside_height": 50, "thickness": 2,
            "corner_radius": 0,
        },
        "ends": [end(0, first_slope), end(length, last_slope)],
        "features": features or [],
    }
    return {
        "instanceKey": name,
        "sourceId": name,
        "length": float(length),
        "tubePart": geometry,
    }


def setup_case(inputs):
    norm = core._normalize_items(inputs, 6001.)
    def fit(sequence):
        return core._optimize_fixed_item_sequence(norm, sequence, 6001., 400., 2.)
    # The existing fast plan can be any valid distribution of the same
    # pieces; two separate rods make the reservation's impact measurable.
    initial = []
    for index in range(len(inputs)):
        state = fit([index])
        if state is None:
            raise AssertionError("Invalid synthetic single-piece rod.")
        initial.append(core._rod_to_dict(state, norm, 6001., 400., 2.))
    return norm, fit, initial


class TailReserveTests(unittest.TestCase):
    def test_threshold_includes_small_eligible_piece_but_not_long_piece(self):
        data = [
            piece("large", 5200),
            piece("tail", 600),
            piece("at-limit", 5000),
            piece("too-short-for-flip", 400),
        ]
        norm = core._normalize_items(data, 6001.)
        self.assertFalse(tail_reserve_eligible(norm[0], 5000, 400))
        self.assertTrue(tail_reserve_eligible(norm[1], 5000, 400))
        self.assertTrue(tail_reserve_eligible(norm[2], 5000, 400))
        self.assertFalse(tail_reserve_eligible(norm[3], 5000, 400))
        self.assertFalse(tail_reserve_eligible(norm[1], 500, 400))
        self.assertFalse(tail_reserve_eligible(norm[1], 0, 400))

    def test_angled_start_on_both_orientations_is_not_reserved(self):
        norm = core._normalize_items([
            piece("double-angled", 600, .3, .4),
            piece("angled-final-but-straight-start", 600, 0., .3),
        ], 6001.)
        self.assertFalse(tail_reserve_eligible(norm[0], 5000, 400))
        self.assertTrue(tail_reserve_eligible(norm[1], 5000, 400))

    def test_machining_near_both_ends_prevents_flip_reservation(self):
        features = [
            {"shape_handle": "hole-front",
             "sampled_bounds": [[0.,0.,50.],[0.,0.,70.]]},
            {"shape_handle": "hole-back",
             "sampled_bounds": [[0.,0.,550.],[0.,0.,570.]]},
        ]
        norm = core._normalize_items([
            piece("drilled", 600, features=features)
        ], 6001.)
        self.assertFalse(tail_reserve_eligible(norm[0], 5000, 400))

    def test_core_piece_is_nested_before_reserved_tail(self):
        data = [piece("core-5200", 5200), piece("reserved-600", 600)]
        norm, fit, initial = setup_case(data)
        result, stats = reserve_tail_packing_seed(
            data, norm, initial, fit,
            rod_length=6001, dead_zone_mm=400, gap_mm=2,
            max_length_mm=5000,
            deadline=time.perf_counter()+8,
        )
        self.assertIsNotNone(result)
        self.assertEqual(stats["reservedPieces"], 1)
        self.assertEqual(stats["corePieces"], 1)
        self.assertEqual(stats["reserveSeedStatus"], "complete")
        self.assertEqual(stats["reserveCoreRods"], 1)
        self.assertEqual(stats["reservePlacedInCore"], 1)
        self.assertEqual(len(result), 1)
        self.assertEqual(
            [p.item_index for p in result[0].placed], [0, 1]
        )
        self.assertTrue(result[0].tail_used)
        self.assertTrue(result[0].placed[-1].requires_tail_flip)
        self.assertLessEqual(result[0].used_span, 6001.00001)

    def test_no_reservation_when_threshold_disabled(self):
        data = [piece("core", 5200), piece("tail", 600)]
        norm, fit, initial = setup_case(data)
        result, stats = reserve_tail_packing_seed(
            data, norm, initial, fit,
            rod_length=6001, dead_zone_mm=400, gap_mm=2,
            max_length_mm=0,
            deadline=time.perf_counter()+4,
        )
        self.assertIsNone(result)
        self.assertEqual(stats["reservedPieces"], 0)

    def test_every_piece_kept_when_reserved_tail_does_not_fit_core(self):
        data = [piece("core-long", 5800), piece("reserved", 600)]
        norm, fit, initial = setup_case(data)
        result, stats = reserve_tail_packing_seed(
            data, norm, initial, fit,
            rod_length=6001, dead_zone_mm=400, gap_mm=2,
            max_length_mm=5000,
            deadline=time.perf_counter()+8,
        )
        self.assertIsNotNone(result)
        self.assertEqual(stats["reservePlacedInCore"], 0)
        self.assertEqual(len(result), 2)
        self.assertEqual(
            sorted(p.item_index for rod in result for p in rod.placed),
            [0, 1],
        )
        self.assertTrue(all(r.used_span <= 6001 for r in result))


if __name__ == "__main__":
    unittest.main()

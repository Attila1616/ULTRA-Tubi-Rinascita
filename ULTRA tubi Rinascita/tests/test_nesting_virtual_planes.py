"""Regression tests for planar co-edge prioritization and virtual contour bounds.

Virtual bounds are rejection-only. Valid placements still require the exact
contour or planar adjacency fit and ordinary machine/chuck safety checks.
"""
import math
import unittest
from unittest.mock import patch

from tubenest_engine.fit import (
    PartPose,
    contour_virtual_plane_lower_bound,
    fit_adjacent_parts,
)
from tubenest_engine.optimizer import (
    OptimizerItem,
    PlacedItem,
    RodSearchState,
    _append_candidate,
    _first_pose_candidates,
    _next_pose_candidates,
    _precompute_pairwise_catalog,
    _reset_parallel_stats,
    get_parallel_stats,
    CONTOUR_EAGER_PRECOMPUTE_LIMIT,
)


def contour_part(length=300.0, count=64, wobble=2.0, shift=0.0):
    def end(z, sign):
        bounds = []
        for n in range(count):
            theta = 2.0 * math.pi * n / count
            value = z + shift + sign * wobble * math.sin(3 * theta)
            bounds.append([value - 0.3, value + 0.3])
        return {
            "plane_z_equals_c_plus_ax_plus_by": None,
            "plane_max_residual_mm": 0.25,
            "perimeter_envelope": bounds,
        }
    return {
        "profile": {"kind": "Circle", "outside_diameter": 50, "thickness": 2},
        "ends": [end(0, 1), end(length, -1)],
        "overall_length": length,
    }


def angled_part(near, far, source):
    def end(c, xy_slope):
        x, y = xy_slope
        return {
            "plane_z_equals_c_plus_ax_plus_by": [c, x, y],
            "plane_max_residual_mm": 0.001,
            "operation_layer": 1,
            "boundary_signature": "shared",
            "process_signature": "same-process",
            "work_flags": 0,
            "curve_flags": 32,
            "curve_normal": [0.0, 0.0, 1.0],
        }
    return OptimizerItem(
        index=0, instance_key=source, source_id=source,
        nominal_length=250,
        tube_part={
            "profile": {
                "kind": "Square", "outside_width": 50,
                "outside_height": 50, "thickness": 2,
                "corner_radius": 0,
            },
            "ends": [end(0, near), end(250, far)],
            "overall_length": 250,
        },
    )


def contour_item(index, length=300.0, count=64):
    return OptimizerItem(
        index=index,
        instance_key=str(index),
        source_id=str(index),
        nominal_length=length,
        tube_part=contour_part(length=length, count=count),
    )


class ContourVirtualPlaneTests(unittest.TestCase):
    def test_bound_never_exceeds_precise_contour_fit_for_rotations(self):
        for count_a, count_b in [(64, 64), (64, 96)]:
            first = contour_part(count=count_a, shift=0.4)
            second = contour_part(count=count_b, shift=-0.2)
            for angle in (0.0, 90.0, 180.0, 270.0):
                for reverse in (False, True):
                    with self.subTest(
                        counts=(count_a, count_b), angle=angle,
                        reversed=reverse,
                    ):
                        p0 = PartPose(0.0, False)
                        p1 = PartPose(angle, reverse)
                        lower = contour_virtual_plane_lower_bound(
                            first, p0, second, p1, gap_mm=2.0
                        )
                        full = fit_adjacent_parts(
                            first, p0, 0.0, second, p1, gap_mm=2.0
                        )
                        self.assertIsNotNone(lower)
                        self.assertLessEqual(
                            lower, full.next_origin + 1e-6
                        )
                        self.assertFalse(full.common_line)

    def test_nonplanar_parts_never_claim_common_line(self):
        a, b = contour_part(), contour_part()
        result = fit_adjacent_parts(
            a, PartPose(), 0, b, PartPose(), gap_mm=2
        )
        self.assertFalse(result.common_line)
        self.assertEqual(result.minimum_clearance_mm, 2.0)

    def test_missing_or_invalid_envelopes_disable_early_rejection(self):
        a, b = contour_part(), contour_part()
        a["ends"][1]["perimeter_envelope"] = []
        self.assertIsNone(
            contour_virtual_plane_lower_bound(
                a, PartPose(), b, PartPose()
            )
        )

    def test_impossible_candidate_skips_full_pair_fitting(self):
        first, second = contour_item(0), contour_item(1)
        previous = RodSearchState(
            used_mask=1, placed=(PlacedItem(0, PartPose(), 0),),
            used_span=300, nominal_packed=300
        )
        with patch(
            "tubenest_engine.optimizer._cached_pairwise_fit",
            side_effect=AssertionError("expensive fit must be skipped"),
        ):
            result = _append_candidate(
                previous, second, PartPose(), [first, second],
                rod_length=500, accessible_limit=500,
                dead_zone_mm=0, gap_mm=2,
            )
        self.assertIsNone(result)

    def test_feasible_candidate_still_uses_exact_fit(self):
        first, second = contour_item(0), contour_item(1)
        previous = RodSearchState(
            used_mask=1, placed=(PlacedItem(0, PartPose(), 0),),
            used_span=300, nominal_packed=300
        )
        import tubenest_engine.optimizer as optimizer
        original = optimizer._cached_pairwise_fit
        with patch.object(
            optimizer, "_cached_pairwise_fit",
            wraps=original,
        ) as spy:
            result = _append_candidate(
                previous, second, PartPose(), [first, second],
                rod_length=1000, accessible_limit=1000,
                dead_zone_mm=0, gap_mm=2,
            )
            self.assertIsNotNone(result)
            self.assertEqual(spy.call_count, 1)
            self.assertFalse(result.placed[-1].common_line_before)

    def test_angle_first_search_preserves_all_legal_poses(self):
        previous = angled_part(near=(0.3, 0), far=(1.0, 0), source="a")
        next_piece = angled_part(near=(0, 1.0), far=(0.2, 0),
                                 source="b")
        p0 = PartPose(0.0, False)
        poses = _next_pose_candidates(previous, p0, next_piece, None)
        self.assertEqual(len(poses), 8)
        self.assertEqual(
            {pose.axial_rotation_degrees for pose in poses},
            {0.0, 90.0, 180.0, 270.0},
        )
        self.assertEqual(
            {pose.reversed_end_for_end for pose in poses},
            {False, True},
        )
        # A +90-degree rotation maps slope (0,1) to (-1,0); +270 maps
        # it to (+1,0), exactly matching the previous far end.
        self.assertEqual(poses[0], PartPose(270.0, False))

    def test_matching_angle_does_not_fake_shared_cut(self):
        previous = angled_part(near=(0, 0), far=(1, 0), source="a")
        next_piece = angled_part(near=(0, 1), far=(0, 0), source="b")
        # Exact fitting decides whether the boundary metadata and
        # machine constraints permit a shared cut.
        poses = _next_pose_candidates(previous, PartPose(), next_piece, None)
        fit = fit_adjacent_parts(
            previous.tube_part, PartPose(), 0,
            next_piece.tube_part, poses[0],
            gap_mm=2,
        )
        self.assertEqual(poses[0].axial_rotation_degrees, 270)
        # Signatures in this synthetic case describe equal machining.
        self.assertTrue(fit.common_line)

    def test_equal_angles_without_compatible_cut_metadata_keep_gap(self):
        previous = angled_part(near=(0, 0), far=(1, 0), source="a")
        next_piece = angled_part(near=(0, 1), far=(0, 0), source="b")
        next_piece.tube_part["ends"][0]["boundary_signature"] = "different"
        poses = _next_pose_candidates(previous, PartPose(), next_piece, None)
        self.assertEqual(len(poses), 8)
        matching = next(
            pose for pose in poses
            if pose.axial_rotation_degrees == 270.0
            and not pose.reversed_end_for_end
        )
        fit = fit_adjacent_parts(
            previous.tube_part, PartPose(), 0,
            next_piece.tube_part, matching,
            gap_mm=2,
        )
        self.assertFalse(fit.common_line)
        self.assertEqual(fit.minimum_clearance_mm, 2.0)

    def test_contour_catalog_skips_speculation_when_large(self):
        parts = [
            angled_part((0.1, 0), (0.3, 0), str(n))
            for n in range(25)
        ]
        # 25 unique square types x eight/eight pose possibilities >8192.
        _reset_parallel_stats()
        with patch(
            "tubenest_engine.optimizer._cached_pairwise_fit",
            side_effect=AssertionError("catalog must be skipped"),
        ):
            result = _precompute_pairwise_catalog(parts, gap_mm=2.0)
        self.assertEqual(result, {})
        self.assertTrue(
            get_parallel_stats()["precompute_skipped_work_budget"]
        )
        self.assertGreater(
            get_parallel_stats()["precompute_estimated_transitions"],
            CONTOUR_EAGER_PRECOMPUTE_LIMIT,
        )

    def test_small_contour_catalog_still_precomputes(self):
        a = contour_item(0)
        _reset_parallel_stats()
        catalog = _precompute_pairwise_catalog([a], gap_mm=2.0)
        self.assertIsInstance(catalog, dict)
        self.assertFalse(
            get_parallel_stats()["precompute_skipped_work_budget"]
        )


if __name__ == "__main__":
    unittest.main()

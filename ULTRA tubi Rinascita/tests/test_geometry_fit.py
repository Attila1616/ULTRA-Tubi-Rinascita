from pathlib import Path
import unittest

from tubenest_engine import read_tube_parts
from tubenest_engine.fit import (
    PartPose,
    fit_adjacent_parts,
    posed_ends,
    rectangular_rotation_family,
    tail_flip_eligibility,
)


ROOT = Path(__file__).resolve().parents[1]
REAL_CHEVRON_SAMPLE = (
    ROOT / "tests" / "fixtures" / "TD1909A00039_150x150x5_L984.zzx"
)


def make_perimeter_envelope(center, amplitude, sample_count=360):
    import math
    return [
        [
            float(center) + float(amplitude) * math.sin(
                2.0 * math.pi * index / sample_count
            ),
            float(center) + float(amplitude) * math.sin(
                2.0 * math.pi * index / sample_count
            ),
        ]
        for index in range(sample_count)
    ]


def make_part(
    length=1000.0,
    profile=None,
    start_plane=(50.0, 0.0, 1.0),
    end_plane=(950.0, 0.0, 1.0),
    features=None,
):
    profile = profile or {
        "kind": "Square",
        "outside_width": 100.0,
        "outside_height": 100.0,
        "corner_radius": 0.0,
        "thickness": 3.0,
    }
    return {
        "overall_length": length,
        "profile": profile,
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


class GeometryFitTests(unittest.TestCase):
    def test_two_mm_is_minimum_surface_clearance_and_envelopes_can_overlap(self):
        previous = make_part(end_plane=(975.0, 0.0, 0.5))
        following = make_part(start_plane=(50.0, 0.0, 1.0))

        fit = fit_adjacent_parts(
            previous,
            PartPose(),
            0.0,
            following,
            PartPose(),
            gap_mm=2.0,
            allow_common_line=False,
        )

        self.assertAlmostEqual(fit.next_origin, 952.0, places=6)
        self.assertAlmostEqual(fit.minimum_clearance_mm, 2.0, places=6)
        self.assertAlmostEqual(fit.overlap_of_axial_envelopes_mm, 48.0, places=6)
        self.assertFalse(fit.common_line)

    def test_parallel_matching_faces_become_common_line(self):
        previous = make_part(end_plane=(950.0, 0.0, 1.0))
        following = make_part(start_plane=(50.0, 0.0, 1.0))
        previous["ends"][1]["boundary_signature"] = "shared-boundary"
        following["ends"][0]["boundary_signature"] = "shared-boundary"

        fit = fit_adjacent_parts(
            previous,
            PartPose(),
            0.0,
            following,
            PartPose(),
            gap_mm=2.0,
            allow_common_line=True,
        )

        self.assertTrue(fit.common_line)
        self.assertAlmostEqual(fit.minimum_clearance_mm, 0.0)
        self.assertAlmostEqual(fit.next_origin, 900.0)

    def test_reversal_is_end_swap_plus_proper_rotation_not_mirror(self):
        part = make_part(
            start_plane=(20.0, 0.25, 0.5),
            end_plane=(980.0, -0.75, -1.0),
        )
        start, end = posed_ends(part, PartPose(reversed_end_for_end=True))

        self.assertEqual(start.source_label, "B")
        self.assertEqual(end.source_label, "A")
        self.assertAlmostEqual(start.c, 20.0)
        self.assertAlmostEqual(start.slope_x, -0.75)
        self.assertAlmostEqual(start.slope_vertical, 1.0)
        self.assertAlmostEqual(end.c, 980.0)
        self.assertAlmostEqual(end.slope_x, 0.25)
        self.assertAlmostEqual(end.slope_vertical, -0.5)

    def test_reversal_plus_180_axial_rotation_matches_z_then_y_flip(self):
        part = make_part(
            length=1000,
            start_plane=(20.0, 0.25, 0.5),
            end_plane=(980.0, -0.75, -1.0),
        )
        start, end = posed_ends(
            part,
            PartPose(
                axial_rotation_degrees=180.0,
                reversed_end_for_end=True,
            ),
        )

        # reverse about machine vertical Z, then rotate 180° about machine Y:
        # (sx, sv) -> (-sx, +sv) after the end swap.
        self.assertEqual(start.source_label, "B")
        self.assertEqual(end.source_label, "A")
        self.assertAlmostEqual(start.c, 20.0)
        self.assertAlmostEqual(start.slope_x, 0.75)
        self.assertAlmostEqual(start.slope_vertical, -1.0)
        self.assertAlmostEqual(end.c, 980.0)
        self.assertAlmostEqual(end.slope_x, -0.25)
        self.assertAlmostEqual(end.slope_vertical, 0.5)

    def test_small_plane_residual_is_accepted_for_common_line(self):
        previous = make_part(end_plane=(950.0, 0.0, 1.0))
        following = make_part(start_plane=(50.0, 0.0, 1.0))
        previous["ends"][1]["boundary_signature"] = "shared-boundary"
        following["ends"][0]["boundary_signature"] = "shared-boundary"
        previous["ends"][1]["plane_max_residual_mm"] = 0.001
        following["ends"][0]["plane_max_residual_mm"] = 0.002

        fit = fit_adjacent_parts(
            previous,
            PartPose(),
            0.0,
            following,
            PartPose(),
            gap_mm=2.0,
            allow_common_line=True,
        )

        self.assertTrue(fit.common_line)
        self.assertEqual(fit.minimum_clearance_mm, 0.0)

    def test_moderate_plane_residual_allows_gap_fit_but_not_common_line(self):
        previous = make_part(end_plane=(950.0, 0.0, 1.0))
        following = make_part(start_plane=(50.0, 0.0, 1.0))
        previous["ends"][1]["boundary_signature"] = "shared-boundary"
        following["ends"][0]["boundary_signature"] = "shared-boundary"
        following["ends"][0]["plane_max_residual_mm"] = 0.02

        fit = fit_adjacent_parts(
            previous,
            PartPose(),
            0.0,
            following,
            PartPose(),
            gap_mm=2.0,
            allow_common_line=True,
        )

        self.assertFalse(fit.common_line)
        self.assertEqual(fit.minimum_clearance_mm, 2.0)

    def test_vectorized_contour_arrays_are_numpy(self):
        import numpy as np
        from tubenest_engine import fit as fit_module

        part = make_part(length=984.0)
        part["part_fingerprint"] = "numpy-array-test"
        part["ends"][0]["perimeter_envelope"] = make_perimeter_envelope(
            75.0, 75.0
        )
        arrays = fit_module._cached_posed_uniform_envelope_arrays(
            part,
            PartPose(axial_rotation_degrees=180.0),
            "start",
        )
        self.assertIsInstance(arrays[0], np.ndarray)
        self.assertIsInstance(arrays[1], np.ndarray)

    def test_posed_contour_envelope_cache_reuses_pose(self):
        from tubenest_engine import fit as fit_module

        part = make_part(length=984.0)
        part["part_fingerprint"] = "cache-test"
        part["ends"][0]["perimeter_envelope"] = make_perimeter_envelope(
            75.0, 75.0
        )
        pose = PartPose(axial_rotation_degrees=180.0)

        first = fit_module._cached_posed_uniform_envelope_arrays(
            part, pose, "start"
        )
        second = fit_module._cached_posed_uniform_envelope_arrays(
            part, pose, "start"
        )
        self.assertIs(first, second)

    def test_real_150_square_chevron_interlocks_at_180(self):
        part = read_tube_parts(REAL_CHEVRON_SAMPLE)[0].to_dict()
        result = fit_adjacent_parts(
            part,
            PartPose(),
            0.0,
            part,
            PartPose(axial_rotation_degrees=180.0),
            gap_mm=2.0,
            allow_common_line=True,
        )
        self.assertFalse(result.common_line)
        self.assertGreater(
            result.overlap_of_axial_envelopes_mm,
            100.0,
        )
        self.assertLess(
            result.next_origin,
            float(part["overall_length"]) - 100.0,
        )

    def test_nonplanar_end_contours_can_interlock_without_common_line(self):
        length = 984.0
        previous = make_part(
            length=length,
            start_plane=(75.0, 0.0, 1.0),
            end_plane=(length - 75.0, 0.0, -1.0),
        )
        following = make_part(
            length=length,
            start_plane=(75.0, 0.0, 1.0),
            end_plane=(length - 75.0, 0.0, -1.0),
        )
        for part in (previous, following):
            part["ends"][0]["plane_max_residual_mm"] = 8.0
            part["ends"][1]["plane_max_residual_mm"] = 8.0
            part["ends"][0]["perimeter_envelope"] = make_perimeter_envelope(
                75.0, 75.0
            )
            part["ends"][1]["perimeter_envelope"] = make_perimeter_envelope(
                length - 75.0, -75.0
            )

        result = fit_adjacent_parts(
            previous,
            PartPose(),
            0.0,
            following,
            PartPose(axial_rotation_degrees=180.0),
            gap_mm=2.0,
            allow_common_line=True,
        )

        self.assertFalse(result.common_line)
        self.assertAlmostEqual(result.minimum_clearance_mm, 2.0)
        self.assertAlmostEqual(
            result.next_origin,
            length - 150.0 + 2.0,
            places=3,
        )
        self.assertGreater(
            result.overlap_of_axial_envelopes_mm,
            140.0,
        )

    def test_large_plane_residual_is_rejected(self):
        previous = make_part(end_plane=(950.0, 0.0, 1.0))
        following = make_part(start_plane=(50.0, 0.0, 1.0))
        following["ends"][0]["plane_max_residual_mm"] = 0.2

        with self.assertRaisesRegex(ValueError, "residual"):
            fit_adjacent_parts(
                previous,
                PartPose(),
                0.0,
                following,
                PartPose(),
                gap_mm=2.0,
                allow_common_line=True,
            )

    def test_rectangle_rotation_family_never_turns_long_side_into_short_side(self):
        self.assertEqual(rectangular_rotation_family(0), [0.0, 180.0])
        self.assertEqual(rectangular_rotation_family(90), [90.0, 270.0])

    def test_tail_flip_requires_straight_first_cut_and_clean_last_400mm(self):
        clean = make_part(
            length=1000,
            start_plane=(0.0, 0.0, 0.0),
            end_plane=(975.0, 0.0, -0.5),
            features=[{
                "shape_handle": 20,
                "feature_type": "cut",
                "sampled_bounds": [[-1, -1, 200], [1, 1, 250]],
            }],
        )
        result = tail_flip_eligibility(clean, PartPose(), dead_zone_mm=400)
        self.assertTrue(result["eligible"])
        self.assertTrue(result["requiresFlip"])
        self.assertTrue(result["finalEndAngled"])

        dirty = make_part(
            length=1000,
            start_plane=(0.0, 0.0, 0.0),
            end_plane=(975.0, 0.0, -0.5),
            features=[{
                "shape_handle": 21,
                "feature_type": "cut",
                "sampled_bounds": [[-1, -1, 650], [1, 1, 700]],
            }],
        )
        result = tail_flip_eligibility(dirty, PartPose(), dead_zone_mm=400)
        self.assertFalse(result["eligible"])
        self.assertTrue(any("final 400" in reason for reason in result["reasons"]))

    def test_reversal_can_make_other_straight_end_the_first_cut(self):
        part = make_part(
            length=1000,
            start_plane=(25.0, 0.0, 0.5),
            end_plane=(1000.0, 0.0, 0.0),
        )
        normal = tail_flip_eligibility(part, PartPose(), dead_zone_mm=400)
        reversed_result = tail_flip_eligibility(
            part,
            PartPose(reversed_end_for_end=True),
            dead_zone_mm=400,
        )
        self.assertFalse(normal["eligible"])
        self.assertTrue(reversed_result["eligible"])


    def test_common_line_requires_both_end_operations_on_channel_1(self):
        previous = make_part(end_plane=(950.0, 0.0, 1.0))
        following = make_part(start_plane=(50.0, 0.0, 1.0))
        previous["ends"][1]["boundary_signature"] = "shared-boundary"
        following["ends"][0]["boundary_signature"] = "shared-boundary"
        previous["ends"][1]["operation_layer"] = 4

        fit = fit_adjacent_parts(
            previous,
            PartPose(),
            0.0,
            following,
            PartPose(),
            gap_mm=2.0,
            allow_common_line=True,
        )
        self.assertFalse(fit.common_line)
        self.assertAlmostEqual(fit.minimum_clearance_mm, 2.0)

    def test_common_line_rejects_incompatible_profile_before_zero_gap(self):
        previous = make_part(
            end_plane=(950.0, 0.0, 1.0),
            profile={
                "kind": "Square",
                "outside_width": 50.0,
                "outside_height": 50.0,
                "corner_radius": 3.2,
                "thickness": 2.0,
            },
        )
        following = make_part(
            start_plane=(50.0, 0.0, 1.0),
            profile={
                "kind": "Square",
                "outside_width": 50.0,
                "outside_height": 50.0,
                "corner_radius": 5.0,
                "thickness": 2.0,
            },
        )
        previous["ends"][1]["boundary_signature"] = "shared-boundary"
        following["ends"][0]["boundary_signature"] = "shared-boundary"

        fit = fit_adjacent_parts(
            previous,
            PartPose(),
            0.0,
            following,
            PartPose(),
            gap_mm=2.0,
            allow_common_line=True,
        )
        self.assertFalse(fit.common_line)
        self.assertAlmostEqual(fit.minimum_clearance_mm, 2.0)

    def test_common_line_rejects_boundary_and_process_mismatch(self):
        previous = make_part(end_plane=(950.0, 0.0, 1.0))
        following = make_part(start_plane=(50.0, 0.0, 1.0))

        previous["ends"][1]["boundary_signature"] = "boundary-A"
        following["ends"][0]["boundary_signature"] = "boundary-B"
        fit = fit_adjacent_parts(
            previous,
            PartPose(),
            0.0,
            following,
            PartPose(),
            gap_mm=2.0,
            allow_common_line=True,
        )
        self.assertFalse(fit.common_line)

        following["ends"][0]["boundary_signature"] = "boundary-A"
        following["ends"][0]["process_signature"] = "different-process"
        fit = fit_adjacent_parts(
            previous,
            PartPose(),
            0.0,
            following,
            PartPose(),
            gap_mm=2.0,
            allow_common_line=True,
        )
        self.assertFalse(fit.common_line)
        self.assertAlmostEqual(fit.minimum_clearance_mm, 2.0)


if __name__ == "__main__":
    unittest.main()

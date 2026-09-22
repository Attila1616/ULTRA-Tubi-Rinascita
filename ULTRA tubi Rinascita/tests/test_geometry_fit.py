import unittest

from tubenest_engine.fit import (
    PartPose,
    fall_friendly_final_cut,
    fit_adjacent_parts,
    posed_ends,
    rectangular_rotation_family,
    tail_flip_eligibility,
)


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


class GeometryFitTests(unittest.TestCase):
    def test_two_mm_is_minimum_surface_clearance_and_envelopes_can_overlap(self):
        previous = make_part(
            end_plane=(975.0, 0.0, 0.5),
        )
        following = make_part(
            start_plane=(50.0, 0.0, 1.0),
        )

        fit = fit_adjacent_parts(
            previous,
            PartPose(),
            0.0,
            following,
            PartPose(),
            gap_mm=2.0,
            allow_common_line=False,
        )

        # The second part's axial bounding box starts before the first part's
        # bounding box ends, but the closest pair of actual cut surfaces is 2mm.
        self.assertAlmostEqual(fit.next_origin, 952.0, places=6)
        self.assertAlmostEqual(fit.minimum_clearance_mm, 2.0, places=6)
        self.assertAlmostEqual(fit.overlap_of_axial_envelopes_mm, 48.0, places=6)
        self.assertFalse(fit.common_line)

    def test_parallel_matching_faces_become_common_line(self):
        previous = make_part(end_plane=(950.0, 0.0, 1.0))
        following = make_part(start_plane=(50.0, 0.0, 1.0))

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

    def test_fall_friendly_final_cut_prefers_backslash_not_slash(self):
        backslash = make_part(end_plane=(975.0, 0.0, -0.5))
        slash = make_part(end_plane=(975.0, 0.0, 0.5))
        straight = make_part(end_plane=(1000.0, 0.0, 0.0))

        self.assertTrue(fall_friendly_final_cut(backslash, PartPose()))
        self.assertFalse(fall_friendly_final_cut(slash, PartPose()))
        self.assertTrue(fall_friendly_final_cut(straight, PartPose()))


if __name__ == "__main__":
    unittest.main()

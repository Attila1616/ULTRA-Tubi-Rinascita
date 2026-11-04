"""Regression tests for IGES section selection around transverse holes."""

import unittest

from tubenest_engine.igs_converter import (
    _section_quality_key,
    _sections_match,
)


class IgesSectionSelectionTests(unittest.TestCase):
    def test_midpoint_hole_is_not_treated_as_best_stock_profile(self):
        # The tube can still yield two closed loops where the drilling cuts
        # through its wall. The split boundary adds misleading contour edges.
        drilled_midpoint = {
            "fraction": 0.50,
            "edges": 24,
            "areas": [9950.0, 8810.0],
        }
        intact_left = {
            "fraction": 0.45,
            "edges": 16,
            "areas": [9969.1, 8828.3],
        }
        intact_right = {
            "fraction": 0.55,
            "edges": 16,
            "areas": [9969.1, 8828.3],
        }

        self.assertLess(
            _section_quality_key(intact_left),
            _section_quality_key(drilled_midpoint),
        )
        self.assertTrue(_sections_match(intact_left, intact_right))
        self.assertFalse(_sections_match(drilled_midpoint, intact_left))

    def test_intact_middle_is_still_preferred_when_equivalent(self):
        midpoint = {
            "fraction": 0.50,
            "edges": 16,
            "areas": [9969.1, 8828.3],
        }
        neighbor = {
            "fraction": 0.45,
            "edges": 16,
            "areas": [9969.1, 8828.3],
        }
        self.assertLess(
            _section_quality_key(midpoint),
            _section_quality_key(neighbor),
        )
        self.assertTrue(_sections_match(midpoint, neighbor))

    def test_unequal_geometry_is_not_confirmed_as_stable(self):
        reference = {
            "fraction": 0.50,
            "edges": 16,
            "areas": [9969.1, 8828.3],
        }
        clipped_end = {
            "fraction": 0.45,
            "edges": 16,
            "areas": [8500.0, 7500.0],
        }
        self.assertFalse(_sections_match(reference, clipped_end))

    def test_matching_allows_small_area_tolerance_from_cad(self):
        first = {
            "fraction": 0.45,
            "edges": 16,
            "areas": [9969.1, 8828.3],
        }
        second = {
            "fraction": 0.55,
            "edges": 16,
            "areas": [9969.2, 8828.4],
        }
        self.assertTrue(_sections_match(first, second))


if __name__ == "__main__":
    unittest.main()

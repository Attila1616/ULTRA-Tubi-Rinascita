import unittest

from tubenest_engine.validation import (
    LENGTH_TOLERANCE_MM,
    filename_profile,
    is_known_array_without_terminal_cut,
    profile_size_issue,
)


class ZzxValidationPolicyTests(unittest.TestCase):
    def test_length_tolerance_is_one_mm(self):
        self.assertEqual(LENGTH_TOLERANCE_MM, 1.0)

    def test_thickness_difference_does_not_create_profile_issue(self):
        filename = filename_profile("example aisi 304 tubo Ø42.4x2 L283 1pz.zzx")
        geometry = {
            "kind": "Circle",
            "outside_diameter": 42.4,
            "thickness": 1.5,
        }
        self.assertIsNone(profile_size_issue(filename, geometry))

    def test_outer_size_difference_is_reported(self):
        filename = filename_profile("example tubolare 30x30x2 L430 2pz.zzx")
        geometry = {
            "kind": "Square",
            "outside_width": 26.0,
            "outside_height": 26.0,
            "thickness": 2.0,
        }
        issue = profile_size_issue(filename, geometry)
        self.assertIsNotNone(issue)
        self.assertIn("30x30", issue)
        self.assertIn("26", issue)

    def test_known_array_without_terminal_cut_is_allowed(self):
        self.assertTrue(
            is_known_array_without_terminal_cut(
                "3x TD9903A02234 aisi 304 tubo Ø129x2 L250 3pz.zzx",
                "TubeSegment 1014 has no usable axial bounds",
            )
        )
        self.assertTrue(
            is_known_array_without_terminal_cut(
                "6x TD9903A02247 aisi 304 tubo Ø129x2 L200 3pz.zzx",
                "TubeSegment 1067 has no usable axial bounds",
            )
        )

    def test_regular_file_without_bounds_is_not_silenced(self):
        self.assertFalse(
            is_known_array_without_terminal_cut(
                "TD9903A02247 aisi 304 tubo Ø129x2 L200 3pz.zzx",
                "TubeSegment 1067 has no usable axial bounds",
            )
        )


if __name__ == "__main__":
    unittest.main()

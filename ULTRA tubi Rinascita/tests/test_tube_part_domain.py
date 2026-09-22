from pathlib import Path
import unittest

from tubenest_engine import read_tube_parts


ROOT = Path(__file__).resolve().parents[1]
ROUND_SAMPLE = ROOT / "Round tube Ø30 L1215, first cut 0° layer 1, second cut 45° layer 4.zzx"
TEXT_SAMPLE = ROOT / "Tube with sample text.zzx"


class TubePartDomainTests(unittest.TestCase):
    def test_round_sample_builds_normalized_part(self):
        parts = read_tube_parts(ROUND_SAMPLE)
        self.assertEqual(len(parts), 1)

        part = parts[0]
        self.assertEqual(part.profile.kind, "Circle")
        self.assertAlmostEqual(part.profile.outside_diameter, 30.0, places=6)
        self.assertTrue(part.profile.continuous_axial_rotation_symmetry)
        self.assertAlmostEqual(part.overall_length, 1215.0, places=3)
        self.assertAlmostEqual(part.axial_min + part.overall_length, part.axial_max, places=9)
        self.assertEqual(len(part.ends), 2)
        self.assertEqual(part.active_layers, [1, 4])
        self.assertEqual(len(part.source_sha256), 64)
        self.assertEqual(part.part_fingerprint, f"{part.source_sha256}:{part.segment_handle}")

        # Normalized bounds start at axial z=0 even when source geometry is translated.
        z_values = [
            bound
            for end in part.ends
            if end.sampled_bounds
            for bound in (end.sampled_bounds[0][2], end.sampled_bounds[1][2])
        ]
        self.assertAlmostEqual(min(z_values), 0.0, places=6)
        self.assertAlmostEqual(max(z_values), part.overall_length, places=6)

    def test_text_sample_classifies_markings_separately(self):
        part = read_tube_parts(TEXT_SAMPLE)[0]
        self.assertGreater(part.marking_feature_count, 0)
        self.assertTrue(any(feature.feature_type == "marking" for feature in part.features))
        self.assertTrue(all(feature.operation_layer is not None for feature in part.features))

    def test_profile_symmetry_is_explicit(self):
        part = read_tube_parts(TEXT_SAMPLE)[0]
        if part.profile.kind == "Square":
            self.assertEqual(
                part.profile.equivalent_axial_rotations_degrees,
                [0.0, 90.0, 180.0, 270.0],
            )
        elif part.profile.kind == "Rect":
            self.assertEqual(
                part.profile.equivalent_axial_rotations_degrees,
                [0.0, 180.0],
            )


if __name__ == "__main__":
    unittest.main()

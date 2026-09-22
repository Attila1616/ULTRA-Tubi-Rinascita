import os
from pathlib import Path
import unittest

from tubenest_engine import describe_zzx, read_zzx


ROOT = Path(__file__).resolve().parents[1]
ROUND_SAMPLE = ROOT / "Round tube Ø30 L1215, first cut 0° layer 1, second cut 45° layer 4.zzx"
TEXT_SAMPLE = ROOT / "Tube with sample text.zzx"


class ZzxReaderTests(unittest.TestCase):
    def test_round_sample_reads_profile_layers_and_end_angles(self):
        doc = read_zzx(ROUND_SAMPLE)
        self.assertEqual(doc.document_type, "NestResults3D")
        self.assertEqual(doc.file_version, "65542")
        self.assertEqual(len(doc.segments), 1)

        segment = doc.segments[0]
        self.assertEqual(segment.profile.section_class, "Circle")
        self.assertAlmostEqual(segment.profile.diameter, 30.0, places=6)
        self.assertAlmostEqual(segment.profile.thickness, 3.0, places=6)
        self.assertEqual(segment.active_layers, [1, 4])
        self.assertEqual([cut.operation_layer for cut in segment.end_cuts], [1, 4])

        angles = [cut.cut_angle_from_perpendicular_degrees for cut in segment.end_cuts]
        self.assertAlmostEqual(angles[0], 0.0, places=3)
        self.assertAlmostEqual(angles[1], 45.0, places=2)

    def test_text_sample_exposes_layer_four_marking_geometry(self):
        doc = read_zzx(TEXT_SAMPLE)
        self.assertEqual(len(doc.segments), 1)
        segment = doc.segments[0]
        self.assertIn(4, segment.active_layers)
        self.assertGreater(segment.marking_shape_count, 0)
        marked = [shape for shape in segment.shapes if shape.is_marking]
        self.assertTrue(any(shape.geometry_class == "Polyline3D" for shape in marked))
        self.assertTrue(any(shape.point_count >= 2 for shape in marked))

    def test_describe_zzx_is_failure_safe(self):
        result = describe_zzx(ROOT / "missing-file.zzx")
        self.assertEqual(result["status"], "error")
        self.assertIn("FileNotFoundError", result["error"])


if __name__ == "__main__":
    unittest.main()

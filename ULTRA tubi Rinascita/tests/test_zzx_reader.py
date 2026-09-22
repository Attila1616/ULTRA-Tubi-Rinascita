import os
from pathlib import Path
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET

from tubenest_engine import describe_zzx, read_zzx
from tubenest_engine.geometry import rounded_rectangle
from tubenest_engine.reader import _profile_from_unknown_outline


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


    def test_older_393222_dialect_is_accepted_for_reading(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "older.zzx"
            with zipfile.ZipFile(ROUND_SAMPLE, "r") as source, zipfile.ZipFile(target, "w") as dest:
                for info in source.infolist():
                    data = source.read(info.filename)
                    if info.filename == "content.xml":
                        root = ET.fromstring(data)
                        root.set("FileVer", "393222")
                        data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
                    dest.writestr(info, data)

            doc = read_zzx(target)
            self.assertEqual(doc.file_version, "393222")
            self.assertEqual(len(doc.segments), 1)
            self.assertEqual(doc.segments[0].profile.section_class, "Circle")

    def test_unknown_section_can_be_inferred_from_rounded_rectangle_outline(self):
        profile = _profile_from_unknown_outline(
            "Unknown",
            3.0,
            "TRvUnknownSection",
            rounded_rectangle(50.0, 150.0, 6.0),
        )
        self.assertEqual(profile.section_class, "Rect")
        self.assertEqual(profile.source_section_class, "Unknown")
        self.assertEqual(profile.native_record_class, "TRvUnknownSection")
        self.assertTrue(profile.inferred_from_geometry)
        self.assertAlmostEqual(profile.width, 50.0, places=6)
        self.assertAlmostEqual(profile.height, 150.0, places=6)
        self.assertAlmostEqual(profile.radius, 6.0, places=6)

    def test_describe_zzx_is_failure_safe(self):
        result = describe_zzx(ROOT / "missing-file.zzx")
        self.assertEqual(result["status"], "error")
        self.assertIn("FileNotFoundError", result["error"])


if __name__ == "__main__":
    unittest.main()

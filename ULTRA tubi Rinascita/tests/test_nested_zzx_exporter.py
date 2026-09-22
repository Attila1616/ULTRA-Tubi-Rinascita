import tempfile
import unittest
from collections import Counter
from pathlib import Path

from tubenest_engine.archive import Archive
from tubenest_engine.bcmp import read_vector
from tubenest_engine.domain import read_tube_parts
from tubenest_engine.exporter import export_nested_rod


APP_ROOT = Path(__file__).resolve().parents[1]


class NestedZzxExporterTests(unittest.TestCase):
    def test_exports_two_real_text_parts_and_preserves_all_shape_kinds(self):
        source = APP_ROOT / "Tube with sample text.zzx"
        self.assertTrue(source.is_file())

        parts = read_tube_parts(source)
        self.assertEqual(len(parts), 1)
        part = parts[0]
        length = float(part.overall_length)

        source_archive = Archive.read(source)
        source_archive.validate()
        source_segment = source_archive.xml("Segments/content.xml").find("TubeSegment")
        source_refs = list(source_segment.find("Shapes"))
        source_shapes = {
            int(element.get("Handle")): element
            for element in source_archive.xml("Shapes/content.xml")
            if element.tag != "MD5"
        }
        source_kinds = Counter(source_shapes[int(ref.get("Handle"))].tag for ref in source_refs)

        placements = [
            {
                "instanceKey": "text::1",
                "filePath": str(source),
                "fileName": source.name,
                "segmentHandle": part.segment_handle,
                "nestPlacement": {
                    "z_start": 0.0,
                    "z_end": length,
                    "axial_rotation_degrees": 0.0,
                    "reversed_end_for_end": False,
                    "common_line_before": False,
                    "common_line_after": False,
                },
            },
            {
                "instanceKey": "text::2",
                "filePath": str(source),
                "fileName": source.name,
                "segmentHandle": part.segment_handle,
                "nestPlacement": {
                    "z_start": length + 5.0,
                    "z_end": 2.0 * length + 5.0,
                    "axial_rotation_degrees": 180.0,
                    "reversed_end_for_end": True,
                    "common_line_before": False,
                    "common_line_after": False,
                },
            },
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "nested_text.zzx"
            result = export_nested_rod(
                placements,
                output,
                rod_length=6000,
                title="Nested text test",
            )
            self.assertEqual(result["pieceCount"], 2)
            self.assertTrue(output.is_file())

            archive = Archive.read(output)
            archive.validate()
            segments = archive.xml("Segments/content.xml").findall("TubeSegment")
            self.assertEqual(len(segments), 2)

            shape_xml = {
                int(element.get("Handle")): element
                for element in archive.xml("Shapes/content.xml")
                if element.tag != "MD5"
            }
            output_kinds = Counter()
            for segment in segments:
                refs = list(segment.find("Shapes"))
                self.assertEqual(len(refs), len(source_refs))
                output_kinds.update(shape_xml[int(ref.get("Handle"))].tag for ref in refs)

            self.assertEqual(
                output_kinds,
                Counter({key: value * 2 for key, value in source_kinds.items()}),
            )

    def test_segment_transform_places_normal_and_reversed_parts(self):
        source = APP_ROOT / "Round tube Ø30 L1215, first cut 0° layer 1, second cut 45° layer 4.zzx"
        parts = read_tube_parts(source)
        self.assertEqual(len(parts), 1)
        part = parts[0]
        length = float(part.overall_length)

        placements = [
            {
                "instanceKey": "round::1",
                "filePath": str(source),
                "fileName": source.name,
                "segmentHandle": part.segment_handle,
                "nestPlacement": {
                    "z_start": 100.0,
                    "z_end": 100.0 + length,
                    "axial_rotation_degrees": 90.0,
                    "reversed_end_for_end": False,
                },
            },
            {
                "instanceKey": "round::2",
                "filePath": str(source),
                "fileName": source.name,
                "segmentHandle": part.segment_handle,
                "nestPlacement": {
                    "z_start": 1600.0,
                    "z_end": 1600.0 + length,
                    "axial_rotation_degrees": 180.0,
                    "reversed_end_for_end": True,
                },
            },
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "nested_round.zzx"
            export_nested_rod(placements, output, rod_length=6000)
            archive = Archive.read(output)
            archive.validate()

            records = {
                record.address: record
                for record in archive.stream("Segments").records
            }
            segments = archive.xml("Segments/content.xml").findall("TubeSegment")

            first_record = records[int(segments[0].get("DataAddr"))]
            first_block = next(block for block in first_record.blocks if block.name == "TubeSegment")
            self.assertAlmostEqual(read_vector(first_block.payload, 8)[0], 0.0, places=6)
            self.assertAlmostEqual(read_vector(first_block.payload, 8)[1], 1.0, places=6)
            self.assertAlmostEqual(read_vector(first_block.payload, 64)[2], 1.0, places=6)
            self.assertAlmostEqual(
                read_vector(first_block.payload, 92)[2],
                100.0 - float(part.axial_min),
                places=6,
            )

            second_record = records[int(segments[1].get("DataAddr"))]
            second_block = next(block for block in second_record.blocks if block.name == "TubeSegment")
            self.assertAlmostEqual(read_vector(second_block.payload, 64)[2], -1.0, places=6)
            self.assertAlmostEqual(
                read_vector(second_block.payload, 92)[2],
                1600.0 + float(part.axial_max),
                places=6,
            )


if __name__ == "__main__":
    unittest.main()

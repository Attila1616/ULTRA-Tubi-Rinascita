import struct
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
                gap_mm=5.0,
                title="Nested text test",
            )
            self.assertEqual(result["pieceCount"], 2)
            self.assertTrue(output.is_file())

            archive = Archive.read(output)
            archive.validate()
            segments = archive.xml("Segments/content.xml").findall("TubeSegment")
            self.assertEqual(len(segments), 2)

            pack_xml = archive.xml("Portions/content.xml").find("DocPortion/PackSegments")
            pack_records = {
                record.address: record
                for record in archive.stream("Segments").records
            }
            pack_record = pack_records[int(pack_xml.get("DataAddr"))]
            pack_block = next(
                block for block in pack_record.blocks
                if block.name == "TubeSegments"
            )
            self.assertAlmostEqual(
                struct.unpack_from("<d", pack_block.payload, 0)[0],
                5.0,
                places=6,
            )

            portion_record = archive.stream("Portions").records[0]
            portion_block = next(
                block for block in portion_record.blocks
                if block.name == "DocPortion"
            )
            self.assertAlmostEqual(
                read_vector(portion_block.payload, 32)[2],
                2.0 * length + 5.0,
                places=5,
            )

            root = archive.xml("content.xml")
            handle_seed = int(root.find("Header").get("HandleSeed"))
            viewport_handles = [
                int(element.get("Handle"))
                for element in archive.xml("Viewports/content.xml").iter()
                if element.get("Handle") is not None
            ]
            self.assertGreater(handle_seed, max(viewport_handles))

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

            source_archive = Archive.read(source)
            source_segment = source_archive.xml("Segments/content.xml").find("TubeSegment")
            source_records = {
                record.address: record
                for record in source_archive.stream("Segments").records
            }
            source_record = source_records[int(source_segment.get("DataAddr"))]
            source_block = next(
                block for block in source_record.blocks
                if block.name == "TubeSegment"
            )
            source_origin = read_vector(source_block.payload, 92)
            source_z = read_vector(source_block.payload, 64)

            first_record = records[int(segments[0].get("DataAddr"))]
            first_block = next(block for block in first_record.blocks if block.name == "TubeSegment")
            self.assertAlmostEqual(read_vector(first_block.payload, 8)[0], 0.0, places=6)
            self.assertAlmostEqual(read_vector(first_block.payload, 8)[1], 1.0, places=6)
            self.assertAlmostEqual(read_vector(first_block.payload, 64)[2], 1.0, places=6)
            # z_start belongs to PackSegments placement and must not leak into
            # the source-local TubeSegment translation.
            self.assertAlmostEqual(
                read_vector(first_block.payload, 92)[2],
                source_origin[2],
                places=6,
            )

            second_record = records[int(segments[1].get("DataAddr"))]
            second_block = next(block for block in second_record.blocks if block.name == "TubeSegment")
            self.assertAlmostEqual(read_vector(second_block.payload, 64)[2], -1.0, places=6)
            expected_reversed_z = (
                source_origin[2]
                + source_z[2] * (float(part.axial_min) + float(part.axial_max))
            )
            self.assertAlmostEqual(
                read_vector(second_block.payload, 92)[2],
                expected_reversed_z,
                places=6,
            )


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import backend_logic
from tubenest_engine.archive import Archive
from tubenest_engine.domain import read_tube_parts
from tubenest_engine.flat_exporter import export_flat_nested_rod


APP_ROOT = Path(__file__).resolve().parents[1]


class NestedTextMarkingTests(unittest.TestCase):
    def test_prod_td_length_label(self):
        config = {"da_fare_path": r"C:\DA FARE"}
        segment = {
            "filePath": (
                "C:/DA FARE/05 PROD 219/TD1901A00438/TT/"
                "pezzo 100x100x3 aisi 304 L1110 2pz.zzx"
            ),
            "fileName": "pezzo 100x100x3 aisi 304 L1110 2pz.zzx",
            "mainFolder": "05 PROD 219",
            "length": 1110,
        }
        self.assertEqual(
            backend_logic._build_nested_marking_text(segment, config),
            "PROD 219  |  TD1901A00438  | L1110",
        )

    def test_ta_code_and_decimal_length_are_supported(self):
        segment = {
            "filePath": (
                "C:/DA FARE/07 PROD 310/TA1234B56789/"
                "part L1250.5 1pz.zzx"
            ),
            "fileName": "part L1250.5 1pz.zzx",
            "mainFolder": "07 PROD 310",
            "length": 1250.5,
        }
        self.assertEqual(
            backend_logic._build_nested_marking_text(segment, {}),
            "PROD 310  |  TA1234B56789  | L1250.5",
        )

    def test_missing_td_is_rejected(self):
        segment = {
            "filePath": "C:/DA FARE/05 PROD 219/TT/part L500 1pz.zzx",
            "fileName": "part L500 1pz.zzx",
            "mainFolder": "05 PROD 219",
            "length": 500,
        }
        with self.assertRaisesRegex(ValueError, "TD/TA"):
            backend_logic._build_nested_marking_text(segment, {})

    def test_channel4_text_is_ordered_before_far_cut(self):
        source = APP_ROOT / "Tube with sample text.zzx"
        part = read_tube_parts(source)[0]
        self.assertIn(part.profile.kind, {"Square", "Rect"})
        length = float(part.overall_length)

        placements = [
            {
                "instanceKey": "piece::1",
                "filePath": str(source),
                "fileName": source.name,
                "segmentHandle": part.segment_handle,
                "markingText": "PROD 219  |  TD1901A00438  | L1250",
                "nestPlacement": {
                    "z_start": 0.0,
                    "z_end": length,
                    "axial_rotation_degrees": 0.0,
                    "reversed_end_for_end": False,
                    "common_line_before": False,
                },
            }
        ]

        fake_strokes = [
            [(0.0, 0.0), (4.0, 0.0)],
            [(0.0, 2.0), (4.0, 2.0)],
        ]
        fake_report = {
            "font_sha256": "test",
            "font_name": "ROMANS",
            "cap_height_units": 21,
            "decoded_characters": list("TEST"),
            "glyph_advances_mm": [],
            "validated_shape_numbers": [],
            "opcodes": ["vector"],
            "text_bounds_2d_mm": [[0.0, 0.0], [4.0, 2.0]],
            "glyph_line_primitives": 2,
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "marked.zzx"
            with patch(
                "tubenest_engine.flat_exporter.validate_romans_font",
                return_value="test",
            ), patch(
                "tubenest_engine.text_marking.decode_strokes",
                return_value=(fake_strokes, fake_report),
            ):
                result = export_flat_nested_rod(
                    placements,
                    output,
                    text_marking_enabled=True,
                    text_marking_height_mm=5.0,
                    text_marking_font_path="dummy.shx",
                )

            self.assertTrue(result["textMarkingEnabled"])
            self.assertEqual(len(result["textMarkings"]), 1)
            new_handles = {
                str(value)
                for value in result["textMarkings"][0]["new_shape_handles"]
            }

            archive = Archive.read(output)
            archive.validate()
            segment = archive.xml("Segments/content.xml").find("TubeSegment")
            refs = list(segment.find("Shapes"))
            ordered_handles = [ref.get("Handle") for ref in refs]

            near_index = ordered_handles.index(segment.get("CutOffA"))
            far_index = ordered_handles.index(segment.get("CutOffB"))
            marking_indices = [
                ordered_handles.index(handle)
                for handle in new_handles
            ]

            self.assertTrue(marking_indices)
            self.assertLess(near_index, min(marking_indices))
            self.assertLess(max(marking_indices), far_index)

            shape_xml = {
                element.get("Handle"): element
                for element in archive.xml("Shapes/content.xml")
                if element.tag != "MD5"
            }
            shape_records = {
                record.address: record
                for record in archive.stream("Shapes").records
            }
            for handle in new_handles:
                record = shape_records[int(shape_xml[handle].get("DataAddr"))]
                shape_block = next(
                    block for block in record.blocks
                    if block.name == "Shape"
                )
                channel = int.from_bytes(
                    shape_block.payload[:4],
                    "little",
                )
                self.assertEqual(channel, 4)


if __name__ == "__main__":
    unittest.main()

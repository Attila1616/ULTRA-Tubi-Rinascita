import math
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import backend_logic
from tubenest_engine.archive import Archive
from tubenest_engine.bcmp import read_vector
from tubenest_engine.domain import read_tube_parts
from tubenest_engine.flat_exporter import (
    _bounds_overlap,
    _candidate_marking_start_positions,
    _flat_marking_faces,
    _round_marking_angles,
    export_flat_nested_rod,
)
from tubenest_engine.geometry import primitives
from tubenest_engine.text_marking import marking_layout_candidates


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

    def test_missing_td_is_allowed_and_omitted(self):
        segment = {
            "filePath": "C:/DA FARE/05 PROD 219/TT/part L500 1pz.zzx",
            "fileName": "part L500 1pz.zzx",
            "mainFolder": "05 PROD 219",
            "length": 500,
        }
        self.assertEqual(
            backend_logic._build_nested_marking_text(segment, {}),
            "PROD 219  | L500",
        )
        self.assertEqual(
            marking_layout_candidates("PROD 219  | L500"),
            [
                ["PROD 219  | L500"],
                ["PROD 219", "L500"],
                ["PROD", "219", "L500"],
            ],
        )

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
            flat_report = result["textMarkings"][0]
            x_bounds = flat_report["geometry_3d_bounds"]
            self.assertLess(abs(x_bounds[0][0]), flat_report["flat_x_limit"])
            self.assertLess(abs(x_bounds[1][0]), flat_report["flat_x_limit"])
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


    def test_progressive_layout_candidates(self):
        self.assertEqual(
            marking_layout_candidates(
                "PROD 219  |  TD1901A00438  | L1110"
            ),
            [
                ["PROD 219  |  TD1901A00438  | L1110"],
                ["PROD 219", "TD1901A00438", "L1110"],
                ["PROD", "219", "TD1901", "A00438", "L1110"],
            ],
        )

    def test_short_piece_reaches_second_multiline_fallback(self):
        source = APP_ROOT / "Tube with sample text.zzx"
        part = read_tube_parts(source)[0]
        length = float(part.overall_length)
        placements = [
            {
                "instanceKey": "piece::compact",
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

        def fake_decode(_font, text, _height):
            # Force the one-line layout to fail. Force the full TD line in the
            # 3-line layout to fail too. Split TD pieces then fit.
            width = (
                2000.0
                if "|" in text or text == "TD1901A00438"
                else 20.0
            )
            strokes = [[(0.0, 0.0), (width, 0.0)], [(0.0, 2.0), (width, 2.0)]]
            return strokes, {
                "font_sha256": "test",
                "font_name": "ROMANS",
                "cap_height_units": 21,
                "decoded_characters": list(text),
                "glyph_advances_mm": [],
                "validated_shape_numbers": [],
                "opcodes": ["vector"],
                "text_bounds_2d_mm": [[0.0, 0.0], [width, 2.0]],
                "glyph_line_primitives": 2,
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "compact.zzx"
            with patch(
                "tubenest_engine.flat_exporter.validate_romans_font",
                return_value="test",
            ), patch(
                "tubenest_engine.text_marking.decode_strokes",
                side_effect=fake_decode,
            ):
                result = export_flat_nested_rod(
                    placements,
                    output,
                    text_marking_enabled=True,
                    text_marking_height_mm=5.0,
                    text_marking_font_path="dummy.shx",
                )

        report = result["textMarkings"][0]
        self.assertEqual(report["status"], "generated")
        self.assertEqual(report["layoutAttempt"], 3)
        self.assertTrue(report["fallbackUsed"])
        self.assertEqual(
            report["layout_lines"],
            ["PROD", "219", "TD1901", "A00438", "L1250"],
        )

    def test_marking_is_skipped_when_no_layout_can_fit(self):
        source = APP_ROOT / "Tube with sample text.zzx"
        part = read_tube_parts(source)[0]
        length = float(part.overall_length)
        placements = [
            {
                "instanceKey": "piece::skip",
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

        def fake_decode(_font, text, _height):
            width = 2000.0
            strokes = [[(0.0, 0.0), (width, 0.0)]]
            return strokes, {
                "font_sha256": "test",
                "font_name": "ROMANS",
                "cap_height_units": 21,
                "decoded_characters": list(text),
                "glyph_advances_mm": [],
                "validated_shape_numbers": [],
                "opcodes": ["vector"],
                "text_bounds_2d_mm": [[0.0, 0.0], [width, 0.0]],
                "glyph_line_primitives": 1,
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "skipped.zzx"
            with patch(
                "tubenest_engine.flat_exporter.validate_romans_font",
                return_value="test",
            ), patch(
                "tubenest_engine.text_marking.decode_strokes",
                side_effect=fake_decode,
            ):
                result = export_flat_nested_rod(
                    placements,
                    output,
                    text_marking_enabled=True,
                    text_marking_height_mm=5.0,
                    text_marking_font_path="dummy.shx",
                )

        report = result["textMarkings"][0]
        self.assertEqual(report["status"], "skipped")
        self.assertFalse(report.get("new_shape_handles"))
        self.assertTrue(
            any("TEXT marking omitted" in warning for warning in result["warnings"])
        )

    def test_round_tube_marking_uses_cylindrical_channel4_geometry(self):
        source = APP_ROOT / (
            "Round tube Ø30 L1215, first cut 0° layer 1, "
            "second cut 45° layer 4.zzx"
        )
        part = read_tube_parts(source)[0]
        self.assertEqual(part.profile.kind, "Circle")
        length = float(part.overall_length)
        placements = [
            {
                "instanceKey": "round::1",
                "filePath": str(source),
                "fileName": source.name,
                "segmentHandle": part.segment_handle,
                "markingText": "PROD 219  |  TD1901A00438  | L1215",
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
            output = Path(temp_dir) / "round_marked.zzx"
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

            report = result["textMarkings"][0]
            self.assertEqual(report["status"], "generated")
            self.assertEqual(report["profileKind"], "Circle")
            self.assertEqual(report["curve_flags"], 0)
            self.assertEqual(report["planar_normal"], [0.0, 0.0, 0.0])

            archive = Archive.read(output)
            archive.validate()
            shape_xml = {
                element.get("Handle"): element
                for element in archive.xml("Shapes/content.xml")
                if element.tag != "MD5"
            }
            shape_records = {
                record.address: record
                for record in archive.stream("Shapes").records
            }
            geometry_records = {
                record.address: record
                for record in archive.stream("LiteGeos").records
            }
            radius = float(part.profile.outside_diameter) / 2.0

            for handle_value in report["new_shape_handles"]:
                element = shape_xml[str(handle_value)]
                record = shape_records[int(element.get("DataAddr"))]
                shape_block = next(
                    block for block in record.blocks
                    if block.name == "Shape"
                )
                curve_block = next(
                    block for block in record.blocks
                    if block.name == "Curve"
                )
                self.assertEqual(
                    struct.unpack_from("<I", shape_block.payload, 0)[0],
                    4,
                )
                self.assertEqual(
                    struct.unpack_from("<I", curve_block.payload, 12)[0],
                    0,
                )
                self.assertEqual(
                    read_vector(curve_block.payload, 16),
                    (0.0, 0.0, 0.0),
                )

                geometry = element.find("Geometry")
                record = geometry_records[int(geometry.get("GeoAddr"))]
                for curve in primitives(record):
                    for point in (curve.at(0.0), curve.at(1.0)):
                        self.assertAlmostEqual(
                            math.hypot(point[0], point[1]),
                            radius,
                            places=8,
                        )


    def test_surface_scan_covers_all_flat_faces_and_round_circumference(self):
        self.assertEqual(
            set(_flat_marking_faces()),
            {"+Y", "+X", "-Y", "-X"},
        )
        angles = _round_marking_angles()
        self.assertEqual(angles[:4], [0, 90, 180, 270])
        self.assertEqual(set(angles), set(range(0, 360, 15)))

    def test_collision_bounds_and_axial_shift_candidates(self):
        obstacle = {
            "handle": "123",
            "bounds": [[10.0, 10.0, 40.0], [20.0, 20.0, 60.0]],
        }
        self.assertTrue(
            _bounds_overlap(
                [[12.0, 12.0, 45.0], [18.0, 18.0, 55.0]],
                obstacle["bounds"],
                clearance=1.0,
            )
        )
        starts = _candidate_marking_start_positions(
            5.0,
            100.0,
            20.0,
            [obstacle],
            clearance=1.0,
        )
        self.assertIn(61.0, starts)
        self.assertIn(19.0, starts)
        self.assertIn(5.0, starts)


if __name__ == "__main__":
    unittest.main()

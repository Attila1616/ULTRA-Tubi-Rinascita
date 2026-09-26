import hashlib
import struct
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import backend_logic
from unittest.mock import patch

from tubenest_engine.archive import Archive
from tubenest_engine.domain import read_tube_parts
from tubenest_engine.exporter import export_nested_rod
from tubenest_engine.fit import PartPose, fit_adjacent_parts


APP_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = APP_ROOT / "tests" / "fixtures" / "array test, co-edged array.zzx"
FIXTURE_SHA256 = "45be517cbb9438763650cd840021fa24112d94795c24f87b2ae6f697fa741ca6"


def _shape_record_map(archive):
    xml = {
        int(element.get("Handle")): element
        for element in archive.xml("Shapes/content.xml")
        if element.tag != "MD5"
    }
    records = {
        record.address: record
        for record in archive.stream("Shapes").records
    }
    return {
        handle: records[int(element.get("DataAddr"))]
        for handle, element in xml.items()
    }


def _shape_meta(record):
    shape = next(block for block in record.blocks if block.name == "Shape")
    curve = next(block for block in record.blocks if block.name == "Curve")
    return {
        "channel": struct.unpack_from("<I", shape.payload, 0)[0],
        "work_flags": struct.unpack_from("<I", shape.payload, 8)[0],
        "curve_flags": struct.unpack_from("<I", curve.payload, 12)[0],
        "start_parameter": struct.unpack_from("<d", curve.payload, 4)[0],
    }


def _maximum_explicit_xml_handle(archive):
    maximum = 0
    for name, data in archive.entries.items():
        if not name.endswith("content.xml"):
            continue
        root = ET.fromstring(data)
        for element in root.iter():
            value = element.get("Handle")
            if value is not None:
                maximum = max(maximum, int(value))
    return maximum


class NativeArrayExporterTests(unittest.TestCase):
    def test_tubest_handle_seed_is_highest_existing_handle(self):
        for fixture in (
            FIXTURE,
            APP_ROOT
            / "tests"
            / "fixtures"
            / "Nested_50x50x2_304_2B_locked-1790190392777-ea489ec4fd24f_20260924_005148.zzx",
        ):
            archive = Archive.read(fixture)
            root = archive.xml("content.xml")
            seed = int(root.find("Header").get("HandleSeed"))
            self.assertEqual(
                seed,
                _maximum_explicit_xml_handle(archive),
            )

    def test_tubest_coedge_fixture_documents_native_representation(self):
        raw = FIXTURE.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), FIXTURE_SHA256)

        archive = Archive.read(FIXTURE)
        archive.validate()

        segments = archive.xml("Segments/content.xml").findall("TubeSegment")
        self.assertEqual(len(segments), 2)

        pack_xml = archive.xml("Portions/content.xml").find(
            "DocPortion/PackSegments"
        )
        work_seq = [
            item.get("Handle")
            for item in pack_xml.findall("WorkSeq/Seg")
        ]
        packed = [
            item.get("Handle")
            for item in pack_xml.findall("TubeSegment")
        ]
        self.assertEqual(work_seq, ["1001", "1035"])
        self.assertEqual(packed, work_seq)

        segment_records = {
            record.address: record
            for record in archive.stream("Segments").records
        }
        pack_record = segment_records[int(pack_xml.get("DataAddr"))]
        pack_block = next(
            block
            for block in pack_record.blocks
            if block.name == "TubeSegments"
        )
        self.assertAlmostEqual(
            struct.unpack_from("<d", pack_block.payload, 0)[0],
            5.0,
            places=9,
        )
        self.assertEqual(
            struct.unpack_from("<I", pack_block.payload, 8)[0],
            1,
        )
        self.assertEqual(
            struct.unpack_from("<I", pack_block.payload, 12)[0],
            0,
        )

        shape_records = _shape_record_map(archive)
        first_far = _shape_meta(shape_records[1005])
        second_near = _shape_meta(shape_records[1038])
        for meta in (first_far, second_near):
            self.assertEqual(meta["channel"], 1)
            self.assertEqual(meta["work_flags"], 0)
            self.assertTrue(meta["curve_flags"] & 0x20)
            self.assertTrue(meta["curve_flags"] & 0x04)

        # Native TubesT keeps both segment-local copies; co-edge is encoded
        # by the pack mode + common-line Curve flag, not by deleting one.
        self.assertIn(1005, shape_records)
        self.assertIn(1038, shape_records)

        portion = archive.stream("Portions").records[0]
        block = next(
            item for item in portion.blocks
            if item.name == "DocPortion"
        )
        from tubenest_engine.bcmp import read_vector
        self.assertAlmostEqual(
            read_vector(block.payload, 32)[2],
            1000.0000256725153,
            places=6,
        )

    def test_exporter_reproduces_common_line_pack_mode(self):
        parts = read_tube_parts(FIXTURE)
        self.assertEqual(len(parts), 2)
        first, second = parts

        fit = fit_adjacent_parts(
            first.to_dict(),
            PartPose(),
            0.0,
            second.to_dict(),
            PartPose(),
            gap_mm=5.0,
            allow_common_line=True,
        )
        self.assertTrue(fit.common_line)

        placements = [
            {
                "instanceKey": "fixture::1",
                "filePath": str(FIXTURE),
                "fileName": FIXTURE.name,
                "segmentHandle": first.segment_handle,
                "nestPlacement": {
                    "z_start": 0.0,
                    "z_end": float(first.overall_length),
                    "axial_rotation_degrees": 0.0,
                    "reversed_end_for_end": False,
                    "common_line_before": False,
                    "common_line_after": True,
                },
            },
            {
                "instanceKey": "fixture::2",
                "filePath": str(FIXTURE),
                "fileName": FIXTURE.name,
                "segmentHandle": second.segment_handle,
                "nestPlacement": {
                    "z_start": float(fit.next_origin),
                    "z_end": float(fit.next_origin + second.overall_length),
                    "axial_rotation_degrees": 0.0,
                    "reversed_end_for_end": False,
                    "common_line_before": True,
                    "common_line_after": False,
                },
            },
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "native_common_line.zzx"
            result = export_nested_rod(
                placements,
                output,
                gap_mm=5.0,
            )
            self.assertEqual(result["segmentCount"], 2)
            self.assertEqual(
                result["exportMode"],
                "native_multi_segment_array",
            )
            self.assertTrue(result["commonLinePackMode"])

            archive = Archive.read(output)
            archive.validate()
            root = archive.xml("content.xml")
            self.assertEqual(
                int(root.find("Header").get("HandleSeed")),
                _maximum_explicit_xml_handle(archive),
            )
            segments = archive.xml("Segments/content.xml").findall(
                "TubeSegment"
            )
            self.assertEqual(len(segments), 2)

            pack_xml = archive.xml("Portions/content.xml").find(
                "DocPortion/PackSegments"
            )
            records = {
                record.address: record
                for record in archive.stream("Segments").records
            }
            pack_record = records[int(pack_xml.get("DataAddr"))]
            pack_block = next(
                block
                for block in pack_record.blocks
                if block.name == "TubeSegments"
            )
            self.assertAlmostEqual(
                struct.unpack_from("<d", pack_block.payload, 0)[0],
                5.0,
                places=9,
            )
            self.assertEqual(
                struct.unpack_from("<I", pack_block.payload, 8)[0],
                1,
            )

            shapes = _shape_record_map(archive)
            first_far = int(segments[0].get("CutOffB"))
            second_near = int(segments[1].get("CutOffA"))
            self.assertTrue(_shape_meta(shapes[first_far])["curve_flags"] & 0x04)
            self.assertTrue(_shape_meta(shapes[second_near])["curve_flags"] & 0x04)

    def test_round_native_array_release_start_normalizes_local_negative_z(self):
        source = APP_ROOT / (
            "Round tube Ø30 L1215, first cut 0° layer 1, "
            "second cut 45° layer 4.zzx"
        )
        part = read_tube_parts(source)[0]
        length = float(part.overall_length)
        placements = [
            {
                "instanceKey": "round::negative-local-z",
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
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "round_native_array.zzx"
            result = export_nested_rod(
                placements,
                output,
                gap_mm=2.0,
            )

            self.assertEqual(result["segmentCount"], 1)
            self.assertEqual(len(result["releaseStarts"]), 2)
            self.assertTrue(output.is_file())

            archive = Archive.read(output)
            archive.validate()

            for release in result["releaseStarts"]:
                self.assertGreaterEqual(
                    float(release["minimumZ"]),
                    -1e-7,
                )
                self.assertIn(
                    "positionedStockMinZBeforeNormalization",
                    release,
                )


    def test_backend_uses_native_array_exporter_not_flattened_exporter(self):
        fake_result = {
            "path": "nested.zzx",
            "pieceCount": 1,
            "segmentCount": 1,
            "exportMode": "native_multi_segment_array",
        }
        payload = {
            "segments": [{"instanceKey": "piece::1"}],
            "tubeType": "50x50x2",
            "rodId": "rod-1",
            "rodLength": 6000.0,
        }
        with patch(
            "backend_logic.load_config",
            return_value={
                "nested_zzx_output_dir": "C:/output",
                "nested_text_marking_enabled": False,
                "nesting_gap_mm": 2.0,
            },
        ), patch(
            "backend_logic.tubenest_engine.export_nested_rod_to_directory",
            return_value=fake_result,
        ) as native_export, patch(
            "backend_logic.tubenest_engine.export_flat_nested_rod_to_directory",
        ) as flat_export:
            result = backend_logic.export_locked_rod_zzx(payload)

        self.assertEqual(result["status"], "success")
        self.assertEqual(
            result["exportMode"],
            "native_multi_segment_array",
        )
        native_export.assert_called_once()
        flat_export.assert_not_called()


    def test_native_array_text_marking_is_before_release_cut(self):
        source = APP_ROOT / "Tube with sample text.zzx"
        part = read_tube_parts(source)[0]
        length = float(part.overall_length)
        placements = [
            {
                "instanceKey": "marked::1",
                "filePath": str(source),
                "fileName": source.name,
                "segmentHandle": part.segment_handle,
                "markingText": "PROD 219  | L500",
                "nestPlacement": {
                    "z_start": 0.0,
                    "z_end": length,
                    "axial_rotation_degrees": 0.0,
                    "reversed_end_for_end": False,
                    "common_line_before": False,
                    "common_line_after": False,
                },
            },
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
            output = Path(temp_dir) / "native_marked.zzx"
            with patch(
                "tubenest_engine.exporter.validate_romans_font",
                return_value="test",
            ), patch(
                "tubenest_engine.text_marking.decode_strokes",
                return_value=(fake_strokes, fake_report),
            ):
                result = export_nested_rod(
                    placements,
                    output,
                    text_marking_enabled=True,
                    text_marking_height_mm=5.0,
                    text_marking_font_path="dummy.shx",
                )

            self.assertTrue(result["textMarkingEnabled"])
            self.assertEqual(len(result["textMarkings"]), 1)
            report = result["textMarkings"][0]
            self.assertEqual(report["status"], "generated")

            archive = Archive.read(output)
            archive.validate()
            segment = archive.xml("Segments/content.xml").find("TubeSegment")
            handles = [ref.get("Handle") for ref in segment.find("Shapes")]
            marking_handles = {
                str(value)
                for value in report["new_shape_handles"]
            }
            near = handles.index(segment.get("CutOffA"))
            far = handles.index(segment.get("CutOffB"))
            marking_indices = [
                handles.index(handle)
                for handle in marking_handles
            ]
            self.assertLess(near, min(marking_indices))
            self.assertLess(max(marking_indices), far)

            shape_records = _shape_record_map(archive)
            for handle in marking_handles:
                self.assertEqual(
                    _shape_meta(shape_records[int(handle)])["channel"],
                    4,
                )

            self.assertEqual(len(result["releaseStarts"]), 2)


if __name__ == "__main__":
    unittest.main()

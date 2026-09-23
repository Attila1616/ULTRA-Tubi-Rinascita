import base64
import hashlib
import io
import math
import struct
import unittest
from pathlib import Path

from tubenest_engine.archive import Archive
from tubenest_engine.bcmp import read_vector, vector
from tubenest_engine.curve_parameters import evaluate_parameter
from tubenest_engine.cut_release import (
    CUTOFF_FLAG,
    EXCLUSION_WORK_BIT,
    _record_handle,
    _shape_geometry,
    curve_flags,
    repair_single_segment_cut_release,
    shape_channel,
    whole_planar_contour,
    work_flags,
)
from tubenest_engine.geometry import Spline, primitives


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
FIXTURE_PREFIX = "duplicate_cut_20260924.zzx.b64.part"
FIXTURE_SHA256 = "d9f0a9317cceb31df8e6de0791f2f4fb60a0fcd4ed98d554fff4388468efbac2"

DUPLICATE_PAIRS = [
    ("1004", "1015"),
    ("1016", "1026"),
    ("1027", "1037"),
    ("1038", "1049"),
    ("1048", "1059"),
]
INCOMPATIBLE_PAIR = ("1060", "1070")
ALL_CANDIDATE_PAIRS = DUPLICATE_PAIRS + [INCOMPATIBLE_PAIR]


def _fixture_bytes():
    parts = sorted(FIXTURE_DIR.glob(FIXTURE_PREFIX + "*"))
    if len(parts) != 7:
        raise AssertionError(f"Expected 7 regression-fixture chunks, found {len(parts)}")
    encoded = "".join(part.read_text(encoding="ascii").strip() for part in parts)
    raw = base64.b64decode(encoded, validate=True)
    digest = hashlib.sha256(raw).hexdigest()
    if digest != FIXTURE_SHA256:
        raise AssertionError(
            f"Regression fixture SHA-256 mismatch: {digest} != {FIXTURE_SHA256}"
        )
    return raw


def _fresh_archive():
    return Archive.read(io.BytesIO(_fixture_bytes()))


def _shape_maps(archive):
    root = archive.xml("Shapes/content.xml")
    xml_by_handle = {
        element.get("Handle"): element
        for element in root
        if element.tag != "MD5"
    }
    records = archive.stream("Shapes").records
    records_by_handle = {_record_handle(record): record for record in records}
    lite_by_addr = {
        record.address: record
        for record in archive.stream("LiteGeos").records
    }
    return xml_by_handle, records_by_handle, lite_by_addr


def _cutoff_release_handles(archive):
    segment = archive.xml("Segments/content.xml").find("TubeSegment")
    xml_by_handle, records_by_handle, _lite = _shape_maps(archive)
    result = []
    for ref in segment.find("Shapes"):
        handle = ref.get("Handle")
        if handle not in xml_by_handle or handle not in records_by_handle:
            continue
        record = records_by_handle[handle]
        if shape_channel(record) > 0 and curve_flags(record) & CUTOFF_FLAG:
            result.append(handle)
    return result


def _active_layer1_cutoffs(archive):
    segment = archive.xml("Segments/content.xml").find("TubeSegment")
    _xml, records_by_handle, _lite = _shape_maps(archive)
    result = []
    for ref in segment.find("Shapes"):
        handle = ref.get("Handle")
        record = records_by_handle.get(handle)
        if record is None:
            continue
        if (
            shape_channel(record) == 1
            and not (work_flags(record) & EXCLUSION_WORK_BIT)
            and curve_flags(record) & CUTOFF_FLAG
        ):
            result.append(handle)
    return result


def _set_excluded(archive, handle):
    stream = archive.stream("Shapes")
    for record in stream.records:
        if _record_handle(record) != str(handle):
            continue
        block = next(block for block in record.blocks if block.name == "Shape")
        raw = bytearray(block.payload)
        flags = struct.unpack_from("<I", raw, 8)[0]
        struct.pack_into("<I", raw, 8, flags | EXCLUSION_WORK_BIT)
        block.payload = bytes(raw)
        break
    else:
        raise AssertionError(f"Shape {handle} not found")
    archive.entries["Shapes/data.bin"] = stream.encode()
    archive.refresh_checksums()


def _translate_shape_geometry_z(archive, handle, delta_z):
    shapes = archive.xml("Shapes/content.xml")
    element = next(
        item
        for item in shapes
        if item.tag != "MD5" and item.get("Handle") == str(handle)
    )
    stream = archive.stream("LiteGeos")
    records = {record.address: record for record in stream.records}
    addresses = {
        int(child.get("GeoAddr"))
        for child in element
        if child.tag in ("Geometry", "InnerGeometry")
        and child.get("GeoAddr") is not None
    }

    for address in addresses:
        record = records[address]
        for block in record.blocks:
            if not block.payload:
                continue
            if block.name == "Spline3D":
                curve = Spline.decode(block.payload)
                curve.points = [
                    (float(x), float(y), float(z) + float(delta_z))
                    for x, y, z in curve.points
                ]
                block.payload = curve.payload()
            elif block.name == "Line3D":
                start = read_vector(block.payload, 0)
                direction = read_vector(block.payload, 28)
                block.payload = vector(
                    (start[0], start[1], start[2] + float(delta_z))
                ) + vector(direction)
            elif block.name == "Polyline3D":
                count = struct.unpack_from("<I", block.payload, 0)[0]
                points = [
                    read_vector(block.payload, 4 + index * 28)
                    for index in range(count)
                ]
                block.payload = struct.pack("<I", count) + b"".join(
                    vector((x, y, z + float(delta_z)))
                    for x, y, z in points
                )

    archive.entries["LiteGeos/data.bin"] = stream.encode()
    archive.refresh_checksums()


class DuplicateCutFixtureTests(unittest.TestCase):
    def test_fixture_hash_and_archive_are_stable(self):
        raw = _fixture_bytes()
        self.assertEqual(len(raw), 18679)
        archive = Archive.read(io.BytesIO(raw))
        archive.validate()
        self.assertEqual(len(_active_layer1_cutoffs(archive)), 16)

    def test_internal_join_allowance_is_separate_from_duplicate_tolerance(self):
        archive = _fresh_archive()
        xml_by_handle, _records, lite_by_addr = _shape_maps(archive)
        curves = _shape_geometry(
            xml_by_handle["1004"],
            lite_by_addr,
            "Geometry",
        )
        max_join_gap = max(
            math.dist(curve.at(1), following.at(0))
            for curve, following in zip(curves, curves[1:] + curves[:1])
        )
        self.assertGreater(max_join_gap, 0.002)
        self.assertLess(max_join_gap, 0.01)
        self.assertFalse(
            whole_planar_contour(
                curves,
                join_tolerance_mm=0.002,
                planarity_tolerance_mm=0.002,
            )
        )
        self.assertTrue(
            whole_planar_contour(
                curves,
                join_tolerance_mm=0.01,
                planarity_tolerance_mm=0.002,
            )
        )

    def test_confirmed_five_pairs_deduplicate_and_incompatible_pair_survives(self):
        archive = _fresh_archive()
        archive.validate()
        before_lite = archive.entries["LiteGeos/data.bin"]
        before_technical = {
            name: data
            for name, data in archive.entries.items()
            if name.startswith("Technical/")
        }
        release_handles = _cutoff_release_handles(archive)

        report = repair_single_segment_cut_release(
            archive,
            profile_kind="Square",
            outside_width=50.0,
            outside_height=50.0,
            shared_pairs=ALL_CANDIDATE_PAIRS,
            release_handles=release_handles,
        )

        self.assertEqual(
            report["removed_to_retained"],
            {
                "1015": "1004",
                "1026": "1016",
                "1037": "1027",
                "1049": "1038",
                "1059": "1048",
            },
        )
        self.assertEqual(report["join_tolerance_mm"], 0.01)
        self.assertEqual(report["duplicate_tolerance_mm"], 0.002)
        self.assertEqual(report["planarity_tolerance_mm"], 0.002)
        self.assertEqual(
            report["candidate_shared_pairs"],
            [list(pair) for pair in ALL_CANDIDATE_PAIRS],
        )

        self.assertEqual(len(report["rejected_shared_pairs"]), 1)
        rejected = report["rejected_shared_pairs"][0]
        self.assertEqual((rejected["a"], rejected["b"]), INCOMPATIBLE_PAIR)
        self.assertRegex(
            rejected["reason"],
            "real gap|outer contours differ|conflicting process metadata",
        )

        self.assertEqual(len(_active_layer1_cutoffs(archive)), 11)
        segment = archive.xml("Segments/content.xml").find("TubeSegment")
        operation_handles = [ref.get("Handle") for ref in segment.find("Shapes")]
        shape_handles = {
            element.get("Handle")
            for element in archive.xml("Shapes/content.xml")
            if element.tag != "MD5"
        }
        binary_handles = {
            _record_handle(record)
            for record in archive.stream("Shapes").records
        }

        for removed, retained in report["removed_to_retained"].items():
            self.assertNotIn(removed, operation_handles)
            self.assertNotIn(removed, shape_handles)
            self.assertNotIn(removed, binary_handles)
            self.assertIn(retained, operation_handles)

        self.assertIn("1060", operation_handles)
        self.assertIn("1070", operation_handles)
        self.assertEqual(archive.entries["LiteGeos/data.bin"], before_lite)
        self.assertEqual(
            {
                name: data
                for name, data in archive.entries.items()
                if name.startswith("Technical/")
            },
            before_technical,
        )
        archive.validate()

        # The retained releases still use the native composite parameter and
        # land on the actual centre of a 50x50 flat face.
        xml_by_handle, _records, lite_by_addr = _shape_maps(archive)
        self.assertEqual(len(report["start_points"]), 11)
        for item in report["start_points"]:
            point = tuple(item["point"])
            x, y, z = point
            on_face_center = (
                abs(abs(x) - 25.0) <= 1e-3 and abs(y) <= 1e-3
            ) or (
                abs(abs(y) - 25.0) <= 1e-3 and abs(x) <= 1e-3
            )
            self.assertTrue(
                on_face_center,
                f"{item['handle']} is not at a flat-face centre: {point}",
            )
            self.assertAlmostEqual(z, item["minimum_z"], delta=1e-6)
            curves = _shape_geometry(
                xml_by_handle[item["handle"]],
                lite_by_addr,
                "Geometry",
            )
            evaluated = evaluate_parameter(curves, item["parameter"])
            self.assertLess(math.dist(evaluated, point), 1e-6)

    def test_incompatible_1060_1070_is_not_force_deleted(self):
        archive = _fresh_archive()
        report = repair_single_segment_cut_release(
            archive,
            profile_kind="Square",
            outside_width=50.0,
            outside_height=50.0,
            shared_pairs=[INCOMPATIBLE_PAIR],
            release_handles=[],
        )
        self.assertEqual(report["removed_to_retained"], {})
        self.assertEqual(len(report["rejected_shared_pairs"]), 1)
        self.assertEqual(
            (
                report["rejected_shared_pairs"][0]["a"],
                report["rejected_shared_pairs"][0]["b"],
            ),
            INCOMPATIBLE_PAIR,
        )
        active = _active_layer1_cutoffs(archive)
        self.assertIn("1060", active)
        self.assertIn("1070", active)

    def test_real_gap_over_duplicate_tolerance_is_not_merged(self):
        archive = _fresh_archive()
        _translate_shape_geometry_z(archive, "1015", 0.003)
        report = repair_single_segment_cut_release(
            archive,
            profile_kind="Square",
            outside_width=50.0,
            outside_height=50.0,
            shared_pairs=[("1004", "1015")],
            release_handles=[],
        )
        self.assertEqual(report["removed_to_retained"], {})
        self.assertEqual(len(report["rejected_shared_pairs"]), 1)
        self.assertRegex(
            report["rejected_shared_pairs"][0]["reason"],
            "real gap|outer contours differ",
        )
        active = _active_layer1_cutoffs(archive)
        self.assertIn("1004", active)
        self.assertIn("1015", active)

    def test_active_copy_wins_over_earlier_excluded_copy(self):
        archive = _fresh_archive()
        _set_excluded(archive, "1004")
        report = repair_single_segment_cut_release(
            archive,
            profile_kind="Square",
            outside_width=50.0,
            outside_height=50.0,
            shared_pairs=[("1004", "1015")],
            release_handles=[],
        )
        self.assertEqual(
            report["removed_to_retained"],
            {"1004": "1015"},
        )
        active = _active_layer1_cutoffs(archive)
        self.assertNotIn("1004", active)
        self.assertIn("1015", active)
        archive.validate()


if __name__ == "__main__":
    unittest.main()

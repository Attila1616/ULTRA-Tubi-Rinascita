"""TEXT marking safety margin and TubeST-compatible round head-motion ZZX."""
import struct
import unittest

from tubenest_engine.bcmp import Block, Record
from tubenest_engine.text_marking import (
    MarkingFitError, build_marking_records, build_round_marking_records,
)


def source_record():
    return Record("TGksGeoCurve", [
        Block("Object", payload=struct.pack("<I", 1004)),
        Block("Shape", payload=struct.pack("<III", 1, 0, 0)),
        Block("Curve", payload=bytes(108)),
    ])


def prepared(strokes, lines):
    return strokes, {
        "layout_lines": lines,
        "text_bounds_2d_mm": [[0., -8.], [15., 8.]],
    }


class NarrowFlatTextTests(unittest.TestCase):
    def test_20x20_flat_face_with_two_mm_corner_clearance(self):
        test = build_marking_records(
            source_shape_record=source_record(),
            font_path="unused-with-prepared-layout",
            lines=["L200"],
            height_mm=3.,
            start_z=5.,
            outside_width=20.,
            outside_height=20.,
            corner_radius=2.,
            max_z=100.,
            first_handle=1100,
            face="+Y",
            prepared_layout=prepared(
                [[(0., -5.8), (10., 5.8)]], ["L200"]
            ),
        )
        self.assertAlmostEqual(test["report"]["flat_transverse_limit"], 6.)
        self.assertAlmostEqual(test["report"]["corner_safety_margin_mm"], 2.)
        self.assertEqual(test["report"]["new_shape_channels"], [4])

    def test_rejects_strokes_inside_corner_safety_margin(self):
        for face in ("+Y", "-Y", "+X", "-X"):
            with self.subTest(face=face):
                with self.assertRaises(MarkingFitError):
                    build_marking_records(
                        source_shape_record=source_record(),
                        font_path="unused",
                        lines=["TD.12345"],
                        height_mm=4.,
                        start_z=5.,
                        outside_width=20.,
                        outside_height=20.,
                        corner_radius=2.,
                        max_z=100.,
                        first_handle=1100,
                        face=face,
                        prepared_layout=prepared(
                            [[(0., -6.01), (10., 6.01)]], ["TD.12345"]
                        ),
                    )


class TubeSTReferenceRoundTests(unittest.TestCase):
    def test_head_motion_matches_tubest_polyline_and_curve_flag(self):
        result = build_round_marking_records(
            source_shape_record=source_record(),
            font_path="unused",
            lines=["ABC"],
            height_mm=3.,
            start_z=10.,
            radius=40.,
            max_z=100.,
            first_handle=2000,
            head_motion_mode=True,
            prepared_layout=prepared(
                [[(0., -1.), (5., 1.)]], ["ABC"]
            ),
        )
        record = result["geometry_records"][0]
        self.assertEqual(record.name, "TGePolyline3D")
        self.assertEqual(record.blocks[0].name, "Polyline3D")
        self.assertEqual(record.blocks[-1].name, "Polyline3D")
        payload = record.blocks[-1].payload
        count = struct.unpack_from("<I", payload, 0)[0]
        self.assertGreaterEqual(count, 2)
        self.assertEqual(len(payload), 4 + 28 * count)
        self.assertEqual(
            result["shape_elements"][0][1].get("Class"), "Polyline3D"
        )
        curve = next(
            b for b in result["shape_records"][0].blocks if b.name == "Curve"
        )
        self.assertEqual(struct.unpack_from("<I", curve.payload, 12)[0], 128)
        shape = next(
            b for b in result["shape_records"][0].blocks if b.name == "Shape"
        )
        self.assertEqual(struct.unpack_from("<I", shape.payload, 0)[0], 4)

    def test_round_rotation_compatibility_mode_remains_available(self):
        result = build_round_marking_records(
            source_shape_record=source_record(),
            font_path="unused",
            lines=["A"],
            height_mm=2.,
            start_z=10.,
            radius=40.,
            max_z=100.,
            first_handle=2000,
            head_motion_mode=False,
            prepared_layout=prepared([[(0., 0.), (4., 1.)]], ["A"]),
        )
        self.assertEqual(result["geometry_records"][0].name,
                         "TGeCompositeCurve3D")
        self.assertEqual(result["report"]["curve_flags"], 0)


if __name__ == "__main__":
    unittest.main()

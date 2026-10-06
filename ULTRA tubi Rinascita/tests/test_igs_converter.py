from pathlib import Path
import struct
import tempfile
import unittest
import zipfile
from unittest.mock import patch

import numpy as np

from tubenest_engine import convert_igs_to_zzx, read_tube_parts
from tubenest_engine.archive import Archive
from tubenest_engine.igs_converter import (
    _pair_feature_boundary_loops,
    _rounded_rectangle_boundary_error,
    _section_loops,
    _usable_boundary_loops,
)


def _closed_square(half_size, z_function):
    corners = [
        (-half_size, -half_size),
        (half_size, -half_size),
        (half_size, half_size),
        (-half_size, half_size),
        (-half_size, -half_size),
    ]
    return np.asarray(
        [
            [x, y, float(z_function(x, y))]
            for x, y in corners
        ],
        dtype=float,
    )


def _sample_model():
    near_outer = _closed_square(
        25.0,
        lambda x, _y: x + 25.0,
    )
    near_inner = _closed_square(
        23.0,
        lambda x, _y: x + 25.0,
    )
    far_outer = _closed_square(
        25.0,
        lambda _x, _y: 200.0,
    )
    far_inner = _closed_square(
        23.0,
        lambda _x, _y: 200.0,
    )
    return {
        "profile_kind": "Square",
        "outside_width": 50.0,
        "outside_height": 50.0,
        "thickness": 2.0,
        "corner_radius": 3.2,
        "overall_length": 200.0,
        # Deliberately use a non-Z world axis to document that source
        # orientation is diagnostic only after the converter normalizes it.
        "axis": [0.0, 1.0, 0.0],
        "local_x_axis": [1.0, 0.0, 0.0],
        "local_y_axis": [0.0, 0.0, -1.0],
        "operations": [
            {
                "outer": near_outer,
                "inner": near_inner,
                "z_min": 0.0,
                "z_max": 50.0,
                "z_mean": 25.0,
            },
            {
                "outer": far_outer,
                "inner": far_inner,
                "z_min": 200.0,
                "z_max": 200.0,
                "z_mean": 200.0,
            },
        ],
        "display_operations": [
            {
                "outer": np.asarray(
                    [
                        [25.0, 25.0, 50.0],
                        [25.0, 25.0, 200.0],
                    ],
                    dtype=float,
                ),
                "inner": np.asarray(
                    [
                        [23.0, 23.0, 48.0],
                        [23.0, 23.0, 200.0],
                    ],
                    dtype=float,
                ),
            },
            {
                "outer": np.asarray(
                    [
                        [-25.0, -25.0, 0.0],
                        [-25.0, -25.0, 200.0],
                    ],
                    dtype=float,
                ),
                "inner": np.asarray(
                    [
                        [-23.0, -23.0, 2.0],
                        [-23.0, -23.0, 200.0],
                    ],
                    dtype=float,
                ),
            },
        ],
    }


class IgsConverterTests(unittest.TestCase):
    def test_round_section_accepts_two_closed_edges(self):
        class FakeSection:
            def __init__(self, *_args):
                self._shape = object()

            def Build(self):
                return None

            def Shape(self):
                return self._shape

        class FakeExplorer:
            def __init__(self, _shape, _kind):
                self.items = ["outer-circle", "inner-circle"]
                self.index = 0

            def More(self):
                return self.index < len(self.items)

            def Current(self):
                return self.items[self.index]

            def Next(self):
                self.index += 1

        class FakeTopoDS:
            @staticmethod
            def Edge_s(value):
                return value

        class FakePlane:
            def __init__(self, *_args):
                pass

        class FakePoint:
            def __init__(self, *_args):
                pass

        class FakeDirection:
            def __init__(self, *_args):
                pass

        api = {
            "gp_Pln": FakePlane,
            "gp_Pnt": FakePoint,
            "gp_Dir": FakeDirection,
            "BRepAlgoAPI_Section": FakeSection,
            "TopExp_Explorer": FakeExplorer,
            "TopAbs_EDGE": object(),
            "TopoDS": FakeTopoDS,
        }

        outer_loop = [
            np.array([10.0, 0.0, 0.0]),
            np.array([0.0, 10.0, 0.0]),
            np.array([-10.0, 0.0, 0.0]),
            np.array([0.0, -10.0, 0.0]),
            np.array([10.0, 0.0, 0.0]),
        ]
        inner_loop = [
            np.array([8.0, 0.0, 0.0]),
            np.array([0.0, 8.0, 0.0]),
            np.array([-8.0, 0.0, 0.0]),
            np.array([0.0, -8.0, 0.0]),
            np.array([8.0, 0.0, 0.0]),
        ]

        with patch(
            "tubenest_engine.igs_converter._chain_edges",
            return_value=[outer_loop, inner_loop],
        ):
            result = _section_loops(
                object(),
                np.array([0.0, 0.0, 1.0]),
                0.0,
                np.array([1.0, 0.0, 0.0]),
                np.array([0.0, 1.0, 0.0]),
                api,
            )

        self.assertIsNotNone(result)
        _origin, loops, edges = result
        self.assertEqual(len(edges), 2)
        self.assertEqual(len(loops), 2)

    def test_rounded_rectangle_surface_distance_keeps_corner_faces_as_stock(self):
        # 150x50 with R5.99 mirrors the problematic rectangular IGES. Points
        # sampled along the actual rounded corner must lie on the stock
        # boundary even if the source section curve itself is sparsely sampled.
        radius = 5.99
        width = 150.0
        height = 50.0
        center = np.array(
            [width / 2.0 - radius, height / 2.0 - radius],
            dtype=float,
        )
        angles = np.linspace(0.0, np.pi / 2.0, 17)
        points = np.asarray(
            [
                center
                + radius * np.array(
                    [np.cos(angle), np.sin(angle)]
                )
                for angle in angles
            ]
        )

        errors = _rounded_rectangle_boundary_error(
            points,
            width,
            height,
            radius,
        )

        self.assertLess(float(np.max(errors)), 1e-8)

    def test_degenerate_iges_seam_loops_are_ignored(self):
        real_loop = [
            np.array([-75.0, -25.0, 0.0]),
            np.array([75.0, -25.0, 0.0]),
            np.array([75.0, 25.0, 0.0]),
            np.array([-75.0, 25.0, 0.0]),
            np.array([-75.0, -25.0, 0.0]),
        ]
        seam_loop = [
            np.array([69.01, -25.0, 0.0]),
            np.array([69.01, -25.0, 0.0]),
        ]

        filtered = _usable_boundary_loops(
            [real_loop, seam_loop]
        )

        self.assertEqual(filtered, [real_loop])

    def test_multi_opening_cut_face_is_split_into_nearest_wall_pairs(self):
        def loop(center):
            x, y, z = center
            return [
                np.array([x - 1.0, y - 1.0, z]),
                np.array([x + 1.0, y - 1.0, z]),
                np.array([x + 1.0, y + 1.0, z]),
                np.array([x - 1.0, y + 1.0, z]),
                np.array([x - 1.0, y - 1.0, z]),
            ]

        outer = [
            loop((-25.0, 0.0, 80.0)),
            loop((25.0, 0.0, 80.0)),
        ]
        inner = [
            loop((23.0, 0.0, 80.0)),
            loop((-23.0, 0.0, 80.0)),
        ]

        pairs = _pair_feature_boundary_loops(
            outer,
            inner,
        )

        self.assertEqual(len(pairs), 2)
        for outside, inside in pairs:
            outside_center = np.asarray(outside).mean(axis=0)
            inside_center = np.asarray(inside).mean(axis=0)
            self.assertLess(
                np.linalg.norm(
                    outside_center - inside_center
                ),
                5.0,
            )

    def test_writer_creates_geometry_aware_square_zzx(self):
        model = _sample_model()

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "sample.igs"
            source.write_text("mock IGES", encoding="ascii")
            output = Path(temp_dir) / "sample.zzx"

            with patch(
                "tubenest_engine.igs_converter._analyse_iges",
                return_value=model,
            ):
                result = convert_igs_to_zzx(
                    source,
                    output,
                )

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["outputPath"], str(output))
            self.assertEqual(result["displayShapeCount"], 2)

            archive = Archive.read(output)
            archive.validate()

            shape_records = archive.stream("Shapes").records
            channels = []
            for record in shape_records:
                shape_block = next(
                    block
                    for block in record.blocks
                    if block.name == "Shape"
                )
                channels.append(
                    struct.unpack_from(
                        "<I",
                        shape_block.payload,
                        0,
                    )[0]
                )
            self.assertEqual(channels, [1, 0, 0, 1])

            shape_root = archive.xml("Shapes/content.xml")
            shape_elements = [
                element
                for element in shape_root
                if element.tag != "MD5"
            ]
            self.assertEqual(
                [
                    element.find("Geometry").get("Class")
                    for element in shape_elements
                ],
                [
                    "CompositeCurve3D",
                    "Spline3D",
                    "Spline3D",
                    "CompositeCurve3D",
                ],
            )

            with zipfile.ZipFile(output) as package:
                infos = {
                    info.filename: info
                    for info in package.infolist()
                }
                self.assertEqual(
                    infos["sign"].compress_type,
                    zipfile.ZIP_STORED,
                )
                self.assertEqual(
                    infos["Shapes/sign"].compress_type,
                    zipfile.ZIP_STORED,
                )
                self.assertEqual(
                    infos["Shapes/"].compress_type,
                    zipfile.ZIP_STORED,
                )
                self.assertEqual(
                    infos["sign"].create_system,
                    0,
                )
                self.assertEqual(
                    infos["Shapes/sign"].create_system,
                    0,
                )

            parts = read_tube_parts(output)
            self.assertEqual(len(parts), 1)
            part = parts[0]
            self.assertEqual(part.profile.kind, "Square")
            self.assertAlmostEqual(part.profile.outside_width, 50.0, places=3)
            self.assertAlmostEqual(part.profile.outside_height, 50.0, places=3)
            self.assertAlmostEqual(part.profile.thickness, 2.0, places=3)
            self.assertAlmostEqual(part.overall_length, 200.0, places=3)

            angles = sorted(
                float(end.angle_from_perpendicular_degrees)
                for end in part.ends
            )
            self.assertAlmostEqual(angles[0], 0.0, places=2)
            self.assertAlmostEqual(angles[1], 45.0, places=2)

    def test_default_output_is_sibling_and_overwrite_requires_confirmation(self):
        model = _sample_model()

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "angled member.igs"
            source.write_text("mock IGES", encoding="ascii")
            expected = source.with_suffix(".zzx")

            with patch(
                "tubenest_engine.igs_converter._analyse_iges",
                return_value=model,
            ):
                first = convert_igs_to_zzx(source)
                second = convert_igs_to_zzx(source)
                third = convert_igs_to_zzx(
                    source,
                    overwrite=True,
                )

            self.assertEqual(first["status"], "success")
            self.assertEqual(first["outputPath"], str(expected))
            self.assertTrue(expected.exists())
            self.assertEqual(second["status"], "exists")
            self.assertEqual(third["status"], "success")


if __name__ == "__main__":
    unittest.main()

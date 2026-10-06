from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from tubenest_engine import convert_igs_to_zzx, read_tube_parts
from tubenest_engine.archive import Archive
from tubenest_engine.igs_converter import (
    _pair_feature_boundary_loops,
    _rounded_rectangle_boundary_error,
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
    }


class IgsConverterTests(unittest.TestCase):
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

            archive = Archive.read(output)
            archive.validate()

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

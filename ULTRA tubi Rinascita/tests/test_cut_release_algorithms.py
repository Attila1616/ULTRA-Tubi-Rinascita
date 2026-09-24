import math
import unittest
from pathlib import Path

from tubenest_engine.archive import Archive
from tubenest_engine.curve_parameters import (
    composite_parameter,
    evaluate_parameter,
    locate_parameter,
)
from tubenest_engine.geometry import Line, Spline, primitives
from tubenest_engine.release_starts import (
    select_face_start,
    select_round_start,
)


APP_ROOT = Path(__file__).resolve().parents[1]


class CutReleaseAlgorithmTests(unittest.TestCase):
    def test_native_span_is_not_primitive_index_plus_fraction(self):
        curves = [
            Spline(
                1,
                [0.0, 0.0, 0.04802, 0.04802],
                [
                    (0.0, -24.01, 580.0),
                    (0.0, 24.01, 580.0),
                ],
                [1.0, 1.0],
            ),
            Spline(
                1,
                [-6.0, -6.0, -4.5, -4.5],
                [
                    (0.0, 24.01, 580.0),
                    (6.0, 30.0, 586.0),
                ],
                [1.0, 1.0],
            ),
        ]

        parameter = composite_parameter(
            curves,
            0,
            0.5,
        )
        self.assertEqual(
            parameter,
            0.02401,
        )
        self.assertEqual(
            evaluate_parameter(
                curves,
                parameter,
            ),
            (0.0, 0.0, 580.0),
        )
        self.assertEqual(
            locate_parameter(
                curves,
                0.5,
            )[0],
            1,
        )


    def test_flagged_clamped_spline_uses_native_knot_interval(self):
        curve = Spline(
            3,
            [
                math.pi / 2,
                math.pi / 2,
                math.pi / 2,
                math.pi / 2,
                3 * math.pi / 2,
                3 * math.pi / 2,
                3 * math.pi / 2,
                5 * math.pi / 2,
                5 * math.pi / 2,
                5 * math.pi / 2,
                5 * math.pi / 2,
            ],
            [
                (0.0, 1.0, 0.0),
                (-1.0, 1.0, 0.0),
                (-1.0, -1.0, 0.0),
                (0.0, -1.0, 0.0),
                (1.0, -1.0, 0.0),
                (1.0, 1.0, 0.0),
                (0.0, 1.0, 0.0),
            ],
            [1.0] * 7,
            flag=1,
        )

        self.assertAlmostEqual(
            composite_parameter([curve], 0, 0.0),
            0.0,
            places=12,
        )
        self.assertAlmostEqual(
            composite_parameter([curve], 0, 1.0),
            2 * math.pi,
            places=12,
        )
        index, normalized = locate_parameter(
            [curve],
            math.pi,
        )
        self.assertEqual(index, 0)
        self.assertAlmostEqual(
            normalized,
            0.5,
            places=12,
        )

    def test_line_and_negative_native_spline_domain(self):
        curves = [
            Line(
                (0.0, 0.0, 0.0),
                (0.0, 1.0, 0.0),
            ),
            Spline(
                1,
                [-7.0, -7.0, -5.0, -5.0],
                [
                    (0.0, 1.0, 0.0),
                    (0.0, 3.0, 0.0),
                ],
                [1.0, 1.0],
            ),
        ]
        self.assertEqual(
            composite_parameter(
                curves,
                1,
                0.5,
            ),
            2.0,
        )
        self.assertEqual(
            evaluate_parameter(
                curves,
                2.0,
            ),
            (0.0, 2.0, 0.0),
        )

    def test_flat_face_center_uses_native_span(self):
        curves = [
            Spline(
                1,
                [
                    0.0,
                    0.0,
                    0.04802,
                    0.04802,
                ],
                [
                    (
                        -30.0,
                        -24.01,
                        580.0,
                    ),
                    (
                        -30.0,
                        24.01,
                        580.0,
                    ),
                ],
                [1.0, 1.0],
            ),
            Line(
                (
                    -30.0,
                    24.01,
                    580.0,
                ),
                (
                    60.0,
                    0.0,
                    60.0,
                ),
            ),
        ]

        selected = select_face_start(
            curves,
            60.0,
            60.0,
        )
        self.assertAlmostEqual(
            selected["parameter"],
            0.02401,
            places=12,
        )
        self.assertAlmostEqual(
            selected["point"][1],
            0.0,
            places=12,
        )

    def test_perpendicular_round_tie_uses_local_positive_y(self):
        source = APP_ROOT / (
            "Round tube Ø30 L1215, first cut 0° layer 1, "
            "second cut 45° layer 4.zzx"
        )
        archive = Archive.read(source)
        segment = archive.xml(
            "Segments/content.xml"
        ).find("TubeSegment")
        shapes = {
            element.get("Handle"): element
            for element in archive.xml(
                "Shapes/content.xml"
            )
            if element.tag != "MD5"
        }
        lite = {
            record.address: record
            for record in archive.stream(
                "LiteGeos"
            ).records
        }

        cutoff = shapes[
            segment.get("CutOffA")
        ]
        geometry = cutoff.find("Geometry")
        curves = primitives(
            lite[
                int(
                    geometry.get(
                        "GeoAddr"
                    )
                )
            ]
        )
        selected = select_round_start(
            curves,
            15.0,
        )

        self.assertAlmostEqual(
            selected["point"][0],
            0.0,
            delta=1e-7,
        )
        self.assertAlmostEqual(
            selected["point"][1],
            15.0,
            delta=1e-7,
        )
        self.assertGreaterEqual(
            selected["minimum_z"],
            -1e-7,
        )


if __name__ == "__main__":
    unittest.main()

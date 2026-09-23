import struct
import unittest

from tubenest_engine.bcmp import Block, Record
from tubenest_engine.geometry import Line, Spline
from tubenest_engine.toolpath import (
    curve_start_parameter,
    point_at_composite_parameter,
)


class ToolpathStartPositionTests(unittest.TestCase):
    def test_curve_block_start_parameter_is_double_at_offset_four(self):
        payload = bytearray(32)
        struct.pack_into("<d", payload, 4, 6.5)
        record = Record("TGksGeoCurve", [Block("Curve", 4, bytes(payload))])
        self.assertAlmostEqual(curve_start_parameter(record), 6.5)

    def test_composite_parameter_uses_native_child_spans(self):
        curves = [
            Spline(
                1,
                [0.0, 0.0, 0.04802, 0.04802],
                [(0.0, -24.01, 580.0), (0.0, 24.01, 580.0)],
                [1.0, 1.0],
            ),
            Spline(
                1,
                [-6.0, -6.0, -4.5, -4.5],
                [(0.0, 24.01, 580.0), (6.0, 30.0, 586.0)],
                [1.0, 1.0],
            ),
        ]
        point = point_at_composite_parameter(curves, 0.02401)
        self.assertAlmostEqual(point[0], 0.0, places=12)
        self.assertAlmostEqual(point[1], 0.0, places=12)
        self.assertAlmostEqual(point[2], 580.0, places=12)

    def test_line_children_still_use_unit_native_spans(self):
        curves = [
            Line((0.0, 0.0, 0.0), (10.0, 0.0, 0.0)),
            Line((10.0, 0.0, 0.0), (0.0, 20.0, 0.0)),
        ]
        self.assertEqual(point_at_composite_parameter(curves, 0.5), (5.0, 0.0, 0.0))
        self.assertEqual(point_at_composite_parameter(curves, 1.5), (10.0, 10.0, 0.0))
        self.assertEqual(point_at_composite_parameter(curves, 2.0), (10.0, 20.0, 0.0))


if __name__ == "__main__":
    unittest.main()

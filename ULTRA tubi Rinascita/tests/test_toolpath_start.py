import struct
import unittest

from tubenest_engine.bcmp import Block, Record
from tubenest_engine.geometry import Line
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

    def test_composite_parameter_uses_one_unit_per_primitive(self):
        curves = [
            Line((0.0, 0.0, 0.0), (10.0, 0.0, 0.0)),
            Line((10.0, 0.0, 0.0), (0.0, 20.0, 0.0)),
        ]
        self.assertEqual(point_at_composite_parameter(curves, 0.5), (5.0, 0.0, 0.0))
        self.assertEqual(point_at_composite_parameter(curves, 1.5), (10.0, 10.0, 0.0))
        self.assertEqual(point_at_composite_parameter(curves, 2.0), (10.0, 20.0, 0.0))


if __name__ == "__main__":
    unittest.main()

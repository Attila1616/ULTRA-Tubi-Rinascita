"""ZZX cut-start metadata helpers.

Observed from TubesT 7.1.27.1 fixtures: changing only the user-selected start
position of a GeoCurve changes one double in the Shape record's Curve block,
at payload offset 4. The geometric LiteGeos contour remains identical.

For CompositeCurve3D contours, the observed scalar parameter uses one unit per
ordered primitive: integer part selects a primitive, fractional part is that
primitive's local t in [0,1]. A value equal to len(curves) denotes the closing
endpoint of the last primitive.
"""
from __future__ import annotations

import math
import struct


def curve_start_parameter(shape_record):
    block = next(
        (b for b in shape_record.blocks if b.name == "Curve" and len(b.payload) >= 12),
        None,
    )
    if block is None:
        return None
    value = struct.unpack_from("<d", block.payload, 4)[0]
    return float(value) if math.isfinite(value) else None


def point_at_composite_parameter(curves, parameter, epsilon=1e-7):
    curves = list(curves or [])
    if not curves or parameter is None:
        return None

    value = float(parameter)
    if not math.isfinite(value) or value < -epsilon or value > len(curves) + epsilon:
        return None

    if value <= 0:
        return tuple(curves[0].at(0.0))
    if value >= len(curves):
        return tuple(curves[-1].at(1.0))

    index = int(math.floor(value))
    local_t = value - index
    if index >= len(curves):
        index = len(curves) - 1
        local_t = 1.0
    return tuple(curves[index].at(local_t))

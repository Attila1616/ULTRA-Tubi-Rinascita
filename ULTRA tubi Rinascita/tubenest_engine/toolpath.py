"""ZZX cut-start metadata helpers.

Observed from TubesT 7.1.27.1 fixtures: changing only the user-selected start
position of a GeoCurve changes one double in the Shape record's Curve block,
at payload offset 4. The geometric LiteGeos contour remains identical.

For CompositeCurve3D contours, PathStartParam accumulates each child's native
parameter span. Imported spline knot domains can be unequal or negative, so
primitive_index + normalized_fraction is not generally valid.
"""
from __future__ import annotations

import math
import struct

from .curve_parameters import evaluate_parameter


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
    if not math.isfinite(value) or value < -epsilon:
        return None
    value = max(0.0, value)
    try:
        return tuple(evaluate_parameter(curves, value))
    except ValueError:
        return None

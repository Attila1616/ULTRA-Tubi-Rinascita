"""Native composite curve parameter conversion.

Confirmed against the cut-release handoff: a composite parameter accumulates
each child's native parameter span. It is not child_index + normalized_t unless
all children happen to span exactly one native unit.
"""
import math

from .geometry import Line, Spline


def native_interval(curve):
    if isinstance(curve, Line):
        return 0.0, 1.0
    if isinstance(curve, Spline):
        if (
            curve.flag
            or any(
                knot != curve.knots[0]
                for knot in curve.knots[: curve.degree + 1]
            )
            or any(
                knot != curve.knots[-1]
                for knot in curve.knots[-curve.degree - 1 :]
            )
        ):
            raise ValueError(
                "Unsupported nonclamped/flagged spline parameter interval"
            )
        return curve.knots[0], curve.knots[-1]
    raise ValueError("Unsupported native curve parameterization")


def composite_parameter(curves, index, normalized_t):
    if (
        not 0 <= index < len(curves)
        or not math.isfinite(normalized_t)
        or not 0 <= normalized_t <= 1
    ):
        raise ValueError("Invalid child index/normalized parameter")
    spans = [
        high - low
        for low, high in map(native_interval, curves)
    ]
    return sum(spans[:index]) + normalized_t * spans[index]


def locate_parameter(curves, parameter):
    if not math.isfinite(parameter) or parameter < 0:
        raise ValueError("Invalid composite parameter")
    cursor = 0.0
    for index, curve in enumerate(curves):
        low, high = native_interval(curve)
        span = high - low
        if parameter <= cursor + span:
            return index, (parameter - cursor) / span
        cursor += span
    raise ValueError("Composite parameter outside curve interval")


def evaluate_parameter(curves, parameter):
    index, normalized_t = locate_parameter(curves, parameter)
    return curves[index].at(normalized_t)

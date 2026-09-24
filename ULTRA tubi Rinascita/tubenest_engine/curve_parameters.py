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
        # Observed ZZX files use flag=1 on otherwise ordinary clamped
        # rational splines (notably circular tube end contours).  The flag
        # does not change the spline's active native parameter domain.
        # Keep unknown flag values conservative, but accept the confirmed
        # 0/1 variants.
        if curve.flag not in (0, 1):
            raise ValueError(
                f"Unsupported spline parameter flag: {curve.flag}"
            )
        if (
            any(
                knot != curve.knots[0]
                for knot in curve.knots[: curve.degree + 1]
            )
            or any(
                knot != curve.knots[-1]
                for knot in curve.knots[-curve.degree - 1 :]
            )
        ):
            raise ValueError(
                "Unsupported nonclamped spline parameter interval"
            )
        return curve.knots[curve.degree], curve.knots[len(curve.points)]
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

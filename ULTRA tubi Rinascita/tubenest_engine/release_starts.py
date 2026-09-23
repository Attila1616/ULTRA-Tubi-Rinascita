"""Release-side start selection using native composite parameters.

Round minima use rational B-spline derivative roots on every nonzero knot
span, including endpoints and internal knots. This is the implementation from
the cut-release handoff; round TubePro acceptance remains pending.
"""
import math

import numpy as np
from numpy.polynomial import Polynomial
from scipy.interpolate import BSpline, PPoly

from .curve_parameters import (
    composite_parameter,
    evaluate_parameter,
    native_interval,
)
from .geometry import Line, Spline


def coordinate_candidates(curve, axis):
    """Normalized parameters containing all endpoints and coordinate extrema."""
    if isinstance(curve, Line):
        return [0.0, 1.0]
    if not isinstance(curve, Spline):
        raise ValueError("Unsupported release curve primitive")

    low, high = native_interval(curve)
    knots = np.asarray(curve.knots)
    weights = np.asarray(curve.weights)
    numerator = PPoly.from_spline(
        BSpline(
            knots,
            np.asarray(curve.points)[:, axis] * weights,
            curve.degree,
        )
    )
    denominator = PPoly.from_spline(
        BSpline(knots, weights, curve.degree)
    )

    result = {0.0, 1.0}
    for index, (start, end) in enumerate(
        zip(numerator.x, numerator.x[1:])
    ):
        if end <= start or start < low or end > high:
            continue

        span = end - start
        factors = span ** np.arange(curve.degree, -1, -1)
        numerator_poly = Polynomial(
            (numerator.c[:, index] * factors)[::-1]
        )
        weight_poly = Polynomial(
            (denominator.c[:, index] * factors)[::-1]
        )
        derivative = (
            numerator_poly.deriv() * weight_poly
            - numerator_poly * weight_poly.deriv()
        )

        result.update(
            (
                (start - low) / (high - low),
                (end - low) / (high - low),
            )
        )
        if np.max(np.abs(derivative.coef)) < 1e-14:
            continue

        for root in derivative.roots():
            if (
                abs(root.imag) <= 1e-8
                and -1e-10 <= root.real <= 1 + 1e-10
            ):
                local = min(1.0, max(0.0, float(root.real)))
                result.add(
                    (start + local * span - low) / (high - low)
                )

    return sorted(result)


def select_round_start(curves, radius, tie_tolerance=1e-7):
    """Select the global minimum-Z outer-contour point.

    Positioned stock must use nonnegative global Z with +Z toward the
    retained/chuck side. Perpendicular ties are resolved at local +Y, then by
    smallest |X|, then deterministically by child index/parameter.
    """
    if not math.isfinite(radius) or radius <= 0:
        raise ValueError("Invalid circle radius")

    choices = []
    for index, curve in enumerate(curves):
        normalized_values = set()
        for axis in (0, 1, 2):
            normalized_values.update(
                coordinate_candidates(curve, axis)
            )
        for normalized_t in normalized_values:
            point = curve.at(normalized_t)
            if not all(math.isfinite(value) for value in point):
                raise ValueError("Nonfinite cutoff")
            choices.append(
                (point[2], index, normalized_t, point)
            )

    if not choices:
        raise ValueError("Empty round cutoff contour")

    minimum = min(choice[0] for choice in choices)
    if minimum < -tie_tolerance:
        raise ValueError(
            "Negative stock Z: establish chuck direction/origin before "
            "choosing closest-to-zero release"
        )

    eligible = [
        choice
        for choice in choices
        if choice[0] <= minimum + tie_tolerance
    ]
    _, index, normalized_t, point = min(
        eligible,
        key=lambda choice: (
            -choice[3][1],
            abs(choice[3][0]),
            choice[1],
            choice[2],
        ),
    )

    if abs(math.hypot(point[0], point[1]) - radius) > 1e-3:
        raise ValueError(
            "Selected cutoff point is not on the circle outside surface"
        )

    parameter = composite_parameter(
        curves,
        index,
        normalized_t,
    )
    if math.dist(
        evaluate_parameter(curves, parameter),
        point,
    ) > 1e-6:
        raise ValueError("Composite seam mismatch")

    return {
        "profile": "Circle",
        "parameter": parameter,
        "point": point,
        "child_index": index,
        "child_t": normalized_t,
        "minimum_z": minimum,
        "rule": "minimum-Z outer contour; +Y tie-break",
    }


def select_face_start(curves, width, height, tolerance=1e-3):
    """Select the actual flat-face center with minimum Z, never a corner."""
    choices = []
    for index, curve in enumerate(curves):
        if not (
            isinstance(curve, Line)
            or (
                isinstance(curve, Spline)
                and curve.degree == 1
                and len(curve.points) == 2
            )
        ):
            continue

        first = curve.at(0)
        last = curve.at(1)
        for fixed_axis, half_size, transverse_axis in (
            (0, width / 2, 1),
            (1, height / 2, 0),
        ):
            if (
                abs(first[fixed_axis] - last[fixed_axis])
                > tolerance
                or abs(abs(first[fixed_axis]) - half_size)
                > tolerance
            ):
                continue

            delta = (
                last[transverse_axis]
                - first[transverse_axis]
            )
            if abs(delta) < 1e-12:
                continue

            normalized_t = -first[transverse_axis] / delta
            if -1e-9 <= normalized_t <= 1 + 1e-9:
                normalized_t = min(
                    1.0,
                    max(0.0, normalized_t),
                )
                point = curve.at(normalized_t)
                choices.append(
                    (
                        point[2],
                        index,
                        normalized_t,
                        point,
                    )
                )

    if not choices:
        raise ValueError(
            "No flat-face center found; do not substitute a corner"
        )

    minimum_z = min(choice[0] for choice in choices)
    eligible = [
        choice
        for choice in choices
        if choice[0] <= minimum_z + 1e-7
    ]
    _, index, normalized_t, point = min(
        eligible,
        key=lambda choice: (
            -choice[3][1],
            abs(choice[3][0]),
            choice[1],
        ),
    )

    return {
        "profile": "Square/Rect",
        "parameter": composite_parameter(
            curves,
            index,
            normalized_t,
        ),
        "point": point,
        "child_index": index,
        "child_t": normalized_t,
        "minimum_z": minimum_z,
        "rule": "minimum-Z flat-face center",
    }

"""Geometry rules for fitting real TubePart ends on one stock rod.

Coordinate convention inside the ZZX reader:
- local axial coordinate is Z in the source archive;
- local cross-section coordinates are X/Y.

At the machine/UI level we treat that local axial coordinate as the tube-laser
Y axis.

Cut start-position/toolpath choices are intentionally handled after nesting and
must not influence the chosen part pose.

Only proper rigid rotations are allowed. No reflection/mirroring operation is
defined anywhere in this module.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Optional


ANGLE_EPS = 1e-7
PLANE_EPS = 1e-5
STRAIGHT_SLOPE_EPS = 1e-5


@dataclass(frozen=True)
class PartPose:
    axial_rotation_degrees: float = 0.0
    reversed_end_for_end: bool = False


@dataclass(frozen=True)
class PosedEnd:
    c: float
    slope_x: float
    slope_vertical: float
    source_label: str
    operation_layer: Optional[int] = None
    residual_mm: Optional[float] = None

    @property
    def slope_magnitude(self):
        return math.hypot(self.slope_x, self.slope_vertical)

    @property
    def is_straight(self):
        return self.slope_magnitude <= STRAIGHT_SLOPE_EPS


@dataclass(frozen=True)
class AdjacencyFit:
    next_origin: float
    minimum_clearance_mm: float
    common_line: bool
    slope_delta: float
    overlap_of_axial_envelopes_mm: float


def _rotate2(x, y, degrees):
    angle = math.radians(float(degrees))
    co, si = math.cos(angle), math.sin(angle)
    return (co * x - si * y, si * x + co * y)


def _raw_end(end):
    if not isinstance(end, dict):
        return None
    plane = end.get("plane_z_equals_c_plus_ax_plus_by")
    if not plane or len(plane) != 3:
        return None
    return (
        float(plane[0]),
        float(plane[1]),
        float(plane[2]),
        str(end.get("label") or "?"),
        end.get("operation_layer"),
        end.get("plane_max_residual_mm"),
    )


def posed_ends(part, pose: PartPose):
    """Return posed (start, end) planes without mirroring the part."""
    ends = list((part or {}).get("ends") or [])
    if len(ends) < 2:
        return (None, None)

    length = float(part.get("overall_length") or 0.0)
    a = _raw_end(ends[0])
    b = _raw_end(ends[1])
    if a is None or b is None:
        return (None, None)

    def make(raw, reverse_transform=False):
        c, sx, sv, label, layer, residual = raw
        if reverse_transform:
            # Physical 180 degree reversal about the machine vertical axis:
            # axial -> -axial, horizontal cross axis -> -horizontal,
            # vertical cross axis remains vertical. This is a proper 3D rotation.
            c = length - c
            sx = sx
            sv = -sv

        sx, sv = _rotate2(sx, sv, pose.axial_rotation_degrees)
        return PosedEnd(
            c=c,
            slope_x=sx,
            slope_vertical=sv,
            source_label=label,
            operation_layer=layer,
            residual_mm=residual,
        )

    if pose.reversed_end_for_end:
        return (make(b, True), make(a, True))
    return (make(a, False), make(b, False))


def equivalent_axial_rotations(profile):
    """Discrete rotations for non-round sections.

    Rectangles intentionally stay in one orientation family: 0/180. A whole
    rod may choose the alternate 90/270 family separately.
    """
    kind = str((profile or {}).get("kind") or "")
    if kind == "Square":
        return [0.0, 90.0, 180.0, 270.0]
    if kind == "Rect":
        return [0.0, 180.0]
    if kind == "Circle":
        return []
    return [0.0]


def rectangular_rotation_family(base_degrees=0.0):
    base = float(base_degrees) % 180.0
    return [base, (base + 180.0) % 360.0]


def support_radius(profile, dx, dy):
    """Support function h(d) of the centered outer cross-section."""
    dx, dy = float(dx), float(dy)
    kind = str((profile or {}).get("kind") or "")

    if kind == "Circle":
        diameter = profile.get("outside_diameter")
        if diameter is None:
            raise ValueError("Circle profile has no outside diameter")
        radius = float(diameter) / 2.0
        return radius * math.hypot(dx, dy)

    width = profile.get("outside_width")
    height = profile.get("outside_height")
    if width is None or height is None:
        raise ValueError(f"{kind or 'Unknown'} profile has no outer dimensions")

    hx, hy = float(width) / 2.0, float(height) / 2.0
    radius = max(0.0, float(profile.get("corner_radius") or 0.0))
    radius = min(radius, hx, hy)

    # Rounded rectangle = smaller axis-aligned rectangle Minkowski-summed
    # with a radius-r circle.
    return (
        max(0.0, hx - radius) * abs(dx)
        + max(0.0, hy - radius) * abs(dy)
        + radius * math.hypot(dx, dy)
    )


def fit_adjacent_parts(
    previous_part,
    previous_pose: PartPose,
    previous_origin,
    next_part,
    next_pose: PartPose,
    gap_mm=2.0,
    allow_common_line=True,
):
    """Place next part as close as safely possible to previous part.

    Clearance is measured at corresponding cross-section coordinates:
      next_start(x,y) - previous_end(x,y) >= gap

    This deliberately allows the two axial *bounding boxes* to overlap when
    differently angled faces interlock.
    """
    previous_start, previous_end = posed_ends(previous_part, previous_pose)
    next_start, next_end = posed_ends(next_part, next_pose)
    if previous_end is None or next_start is None:
        raise ValueError("Both adjacent ends need planar geometry")

    if previous_end.residual_mm is not None and previous_end.residual_mm > PLANE_EPS:
        raise ValueError("Previous end is not planar enough for planar fitting")
    if next_start.residual_mm is not None and next_start.residual_mm > PLANE_EPS:
        raise ValueError("Next start is not planar enough for planar fitting")

    dsx = next_start.slope_x - previous_end.slope_x
    dsy = next_start.slope_vertical - previous_end.slope_vertical
    slope_delta = math.hypot(dsx, dsy)

    same_plane_shape = slope_delta <= PLANE_EPS
    common_line = bool(allow_common_line and same_plane_shape)
    target_gap = 0.0 if common_line else float(gap_mm)
    if target_gap < 0:
        raise ValueError("gap_mm cannot be negative")

    # Evaluate the support in the previous part's local cross-section frame.
    # This matters for rectangular stock when the whole rod uses the 90/270
    # orientation family; squares and circles are rotationally invariant.
    local_dsx, local_dsy = _rotate2(
        dsx,
        dsy,
        -float(previous_pose.axial_rotation_degrees),
    )
    support = support_radius(
        previous_part.get("profile") or {},
        local_dsx,
        local_dsy,
    )

    # previous surface is previous_origin + c_prev + d_prev dot p
    # next surface is next_origin + c_next + d_next dot p.
    # Set the minimum difference over the symmetric cross-section to target_gap.
    next_origin = (
        float(previous_origin)
        + previous_end.c
        - next_start.c
        + support
        + target_gap
    )

    prev_length = float(previous_part.get("overall_length") or 0.0)
    next_length = float(next_part.get("overall_length") or 0.0)
    prev_max = float(previous_origin) + prev_length
    next_min = next_origin
    envelope_overlap = max(0.0, prev_max - next_min)

    return AdjacencyFit(
        next_origin=next_origin,
        minimum_clearance_mm=target_gap,
        common_line=common_line,
        slope_delta=slope_delta,
        overlap_of_axial_envelopes_mm=envelope_overlap,
    )


def transformed_feature_axial_bounds(feature, part, pose: PartPose):
    bounds = (feature or {}).get("sampled_bounds")
    if not bounds or len(bounds) != 2 or len(bounds[0]) < 3:
        return None
    lo, hi = float(bounds[0][2]), float(bounds[1][2])
    if pose.reversed_end_for_end:
        length = float(part.get("overall_length") or 0.0)
        lo, hi = length - hi, length - lo
    return (min(lo, hi), max(lo, hi))


def tail_flip_eligibility(part, pose: PartPose, dead_zone_mm=400.0):
    """Check whether a final part can use the chuck-zone flip strategy."""
    length = float((part or {}).get("overall_length") or 0.0)
    dead_zone = float(dead_zone_mm)
    reasons = []

    if length <= dead_zone:
        reasons.append(f"piece length {length:g} <= dead zone {dead_zone:g}")

    start, end = posed_ends(part, pose)
    if start is None:
        reasons.append("start end has no planar geometry")
    elif not start.is_straight:
        reasons.append("first cut is not straight")

    protected_start = max(0.0, length - dead_zone)
    for feature in (part or {}).get("features") or []:
        fb = transformed_feature_axial_bounds(feature, part, pose)
        if fb is None:
            continue
        if fb[1] > protected_start + 1e-6:
            reasons.append(
                f"feature {feature.get('shape_handle')} enters final {dead_zone:g} mm"
            )

    return {
        "eligible": not reasons,
        "reasons": reasons,
        "deadZoneMm": dead_zone,
        "protectedInterval": [protected_start, length],
        "requiresFlip": not reasons,
        "finalEndAngled": bool(end and not end.is_straight),
    }


def pose_is_proper_rotation(_pose: PartPose):
    """All representable poses are rotations; reflections are unrepresentable."""
    return True

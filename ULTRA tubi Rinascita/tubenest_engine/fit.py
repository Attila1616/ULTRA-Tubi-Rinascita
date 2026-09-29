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
PLANAR_FIT_TOLERANCE_MM = 0.05
COMMON_LINE_RESIDUAL_TOLERANCE_MM = 0.01
COMMON_LINE_PROFILE_TOLERANCE_MM = 0.002
COMMON_LINE_NORMAL_TOLERANCE = 1e-6
CUTOFF_FLAG = 32
EXCLUSION_WORK_BIT = 0x2
STRAIGHT_SLOPE_EPS = 1e-5
CONTOUR_PROFILE_TOLERANCE_MM = 0.25
MIN_CONTOUR_SAMPLES = 32


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
    boundary_signature: Optional[str] = None
    process_signature: Optional[str] = None
    work_flags: Optional[int] = None
    curve_flags: Optional[int] = None
    curve_normal: Optional[tuple] = None

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
    normal = end.get("curve_normal")
    return {
        "c": float(plane[0]),
        "sx": float(plane[1]),
        "sv": float(plane[2]),
        "label": str(end.get("label") or "?"),
        "layer": end.get("operation_layer"),
        "residual": end.get("plane_max_residual_mm"),
        "boundary_signature": end.get("boundary_signature"),
        "process_signature": end.get("process_signature"),
        "work_flags": end.get("work_flags"),
        "curve_flags": end.get("curve_flags"),
        "curve_normal": (
            tuple(map(float, normal))
            if isinstance(normal, (list, tuple)) and len(normal) == 3
            else None
        ),
    }


def _pose_curve_normal(normal, pose, reverse_transform):
    if normal is None:
        return None
    x, y, z = map(float, normal)
    if reverse_transform:
        x = -x
        z = -z
    x, y = _rotate2(x, y, pose.axial_rotation_degrees)
    return (x, y, z)


def _close_optional(left, right, tolerance):
    if left is None and right is None:
        return True
    if left is None or right is None:
        return False
    return abs(float(left) - float(right)) <= float(tolerance)


def _profiles_common_line_compatible(previous_profile, next_profile):
    previous_profile = previous_profile or {}
    next_profile = next_profile or {}
    if str(previous_profile.get("kind") or "") != str(next_profile.get("kind") or ""):
        return False
    if not _close_optional(
        previous_profile.get("thickness"),
        next_profile.get("thickness"),
        COMMON_LINE_PROFILE_TOLERANCE_MM,
    ):
        return False
    kind = str(previous_profile.get("kind") or "")
    keys = (
        ("outside_diameter",)
        if kind == "Circle"
        else ("outside_width", "outside_height", "corner_radius")
        if kind in ("Square", "Rect")
        else ()
    )
    return bool(keys) and all(
        _close_optional(
            previous_profile.get(key),
            next_profile.get(key),
            COMMON_LINE_PROFILE_TOLERANCE_MM,
        )
        for key in keys
    )


def _common_line_boundary_compatible(previous_part, previous_end, next_part, next_start):
    if not _profiles_common_line_compatible(
        (previous_part or {}).get("profile") or {},
        (next_part or {}).get("profile") or {},
    ):
        return False
    if (
        not previous_end.boundary_signature
        or previous_end.boundary_signature != next_start.boundary_signature
    ):
        return False
    if (
        not previous_end.process_signature
        or previous_end.process_signature != next_start.process_signature
    ):
        return False
    if (
        previous_end.work_flags is None
        or next_start.work_flags is None
        or int(previous_end.work_flags) & EXCLUSION_WORK_BIT
        or int(next_start.work_flags) & EXCLUSION_WORK_BIT
    ):
        return False
    if (
        previous_end.curve_flags is None
        or next_start.curve_flags is None
        or not (int(previous_end.curve_flags) & CUTOFF_FLAG)
        or int(previous_end.curve_flags) != int(next_start.curve_flags)
    ):
        return False
    if (
        previous_end.curve_normal is None
        or next_start.curve_normal is None
        or math.dist(previous_end.curve_normal, next_start.curve_normal)
        > COMMON_LINE_NORMAL_TOLERANCE
    ):
        return False
    return True


def posed_ends(part, pose: PartPose):
    """Return posed (start, end) planes without mirroring the part."""
    ends = list((part or {}).get("ends") or [])
    if len(ends) < 2:
        return (None, None)

    length = float(part.get("overall_length") or 0.0)
    a = _raw_end(ends[0])
    b = _raw_end(ends[1])

    def make(raw, reverse_transform=False):
        if raw is None:
            return None
        c = raw["c"]
        sx = raw["sx"]
        sv = raw["sv"]
        if reverse_transform:
            c = length - c
            sv = -sv

        sx, sv = _rotate2(sx, sv, pose.axial_rotation_degrees)
        return PosedEnd(
            c=c,
            slope_x=sx,
            slope_vertical=sv,
            source_label=raw["label"],
            operation_layer=raw["layer"],
            residual_mm=raw["residual"],
            boundary_signature=raw["boundary_signature"],
            process_signature=raw["process_signature"],
            work_flags=raw["work_flags"],
            curve_flags=raw["curve_flags"],
            curve_normal=_pose_curve_normal(
                raw["curve_normal"], pose, reverse_transform
            ),
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


def _valid_perimeter_envelope(end):
    envelope = (end or {}).get("perimeter_envelope")
    if not isinstance(envelope, list) or len(envelope) < MIN_CONTOUR_SAMPLES:
        return None
    result = []
    for pair in envelope:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            return None
        lo, hi = map(float, pair)
        if not math.isfinite(lo) or not math.isfinite(hi) or lo > hi:
            return None
        result.append((lo, hi))
    return result


def _contour_profiles_compatible(previous_profile, next_profile):
    previous_profile = previous_profile or {}
    next_profile = next_profile or {}
    kind = str(previous_profile.get("kind") or "")
    if kind != str(next_profile.get("kind") or ""):
        return False
    keys = (
        ("outside_diameter", "thickness")
        if kind == "Circle"
        else ("outside_width", "outside_height", "thickness")
        if kind in ("Square", "Rect")
        else ()
    )
    return bool(keys) and all(
        _close_optional(
            previous_profile.get(key),
            next_profile.get(key),
            CONTOUR_PROFILE_TOLERANCE_MM,
        )
        for key in keys
    )


def _select_raw_end(part, pose, position):
    ends = list((part or {}).get("ends") or [])
    if len(ends) < 2:
        return None, False
    reverse = bool(pose.reversed_end_for_end)
    if position == "start":
        index = 1 if reverse else 0
    else:
        index = 0 if reverse else 1
    return ends[index], reverse


def _interpolate_uniform_envelope(envelope, angle):
    count = len(envelope)
    position = (float(angle) % (2.0 * math.pi)) * count / (
        2.0 * math.pi
    )
    base_float = math.floor(position)
    base = int(base_float) % count
    fraction = position - base_float
    following = (base + 1) % count
    lo = envelope[base][0] * (1.0 - fraction) + envelope[following][0] * fraction
    hi = envelope[base][1] * (1.0 - fraction) + envelope[following][1] * fraction
    return lo, hi


def _posed_envelope_at(end, pose, length, global_angle, reverse_transform):
    envelope = _valid_perimeter_envelope(end)
    if envelope is None:
        return None
    rotation = math.radians(float(pose.axial_rotation_degrees))
    if reverse_transform:
        source_angle = math.pi + rotation - float(global_angle)
    else:
        source_angle = float(global_angle) - rotation
    lo, hi = _interpolate_uniform_envelope(envelope, source_angle)
    if reverse_transform:
        return float(length) - hi, float(length) - lo
    return lo, hi


def _fit_adjacent_contours(
    previous_part,
    previous_pose,
    previous_origin,
    next_part,
    next_pose,
    gap_mm,
):
    if not _contour_profiles_compatible(
        (previous_part or {}).get("profile"),
        (next_part or {}).get("profile"),
    ):
        raise ValueError("End contours use incompatible stock profiles")

    previous_end, previous_reverse = _select_raw_end(
        previous_part, previous_pose, "end"
    )
    next_start, next_reverse = _select_raw_end(
        next_part, next_pose, "start"
    )
    previous_envelope = _valid_perimeter_envelope(previous_end)
    next_envelope = _valid_perimeter_envelope(next_start)
    if previous_envelope is None or next_envelope is None:
        raise ValueError("End contour envelope is unavailable")

    target_gap = float(gap_mm)
    if target_gap < 0:
        raise ValueError("gap_mm cannot be negative")

    sample_count = max(len(previous_envelope), len(next_envelope))
    previous_length = float(previous_part.get("overall_length") or 0.0)
    next_length = float(next_part.get("overall_length") or 0.0)
    required_relative_origin = -math.inf

    for index in range(sample_count):
        angle = (2.0 * math.pi * index) / float(sample_count)
        previous_bounds = _posed_envelope_at(
            previous_end,
            previous_pose,
            previous_length,
            angle,
            previous_reverse,
        )
        next_bounds = _posed_envelope_at(
            next_start,
            next_pose,
            next_length,
            angle,
            next_reverse,
        )
        if previous_bounds is None or next_bounds is None:
            raise ValueError("End contour envelope interpolation failed")
        required_relative_origin = max(
            required_relative_origin,
            previous_bounds[1] - next_bounds[0] + target_gap,
        )

    if not math.isfinite(required_relative_origin):
        raise ValueError("End contour fit produced no finite placement")

    next_origin = float(previous_origin) + required_relative_origin
    previous_max = float(previous_origin) + previous_length
    overlap = max(0.0, previous_max - next_origin)

    # Non-planar contour fitting does not claim common-line. Shared-cut
    # verification/removal is intentionally still limited to planar contours.
    return AdjacencyFit(
        next_origin=next_origin,
        minimum_clearance_mm=target_gap,
        common_line=False,
        slope_delta=0.0,
        overlap_of_axial_envelopes_mm=overlap,
    )


def _plane_fit_is_usable(end):
    if end is None:
        return False
    return float(end.residual_mm or 0.0) <= PLANAR_FIT_TOLERANCE_MM


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

    if not (
        _plane_fit_is_usable(previous_end)
        and _plane_fit_is_usable(next_start)
    ):
        try:
            return _fit_adjacent_contours(
                previous_part,
                previous_pose,
                previous_origin,
                next_part,
                next_pose,
                gap_mm,
            )
        except ValueError:
            if previous_end is None or next_start is None:
                raise ValueError(
                    "Adjacent ends need either usable planar geometry or "
                    "complete perimeter contour envelopes"
                )
            if (
                previous_end.residual_mm is not None
                and previous_end.residual_mm > PLANAR_FIT_TOLERANCE_MM
            ):
                raise ValueError(
                    f"Previous end plane residual "
                    f"{previous_end.residual_mm:.6f} mm exceeds "
                    f"{PLANAR_FIT_TOLERANCE_MM:.3f} mm fitting tolerance "
                    "and no safe contour fit is available"
                )
            raise ValueError(
                f"Next start plane residual {next_start.residual_mm:.6f} mm "
                f"exceeds {PLANAR_FIT_TOLERANCE_MM:.3f} mm fitting tolerance "
                "and no safe contour fit is available"
            )

    dsx = next_start.slope_x - previous_end.slope_x
    dsy = next_start.slope_vertical - previous_end.slope_vertical
    slope_delta = math.hypot(dsx, dsy)

    same_plane_shape = slope_delta <= PLANE_EPS
    previous_residual = float(previous_end.residual_mm or 0.0)
    next_residual = float(next_start.residual_mm or 0.0)
    residuals_precise_enough_for_common_line = (
        previous_residual <= COMMON_LINE_RESIDUAL_TOLERANCE_MM
        and next_residual <= COMMON_LINE_RESIDUAL_TOLERANCE_MM
    )
    # The verified shared-cut cleanup is defined for ordinary enabled
    # channel-1 cutoff contours. Other end-operation channels may still be
    # valid machining, but they are not automatically interchangeable as one
    # shared physical cut.
    channel1_pair = (
        previous_end.operation_layer == 1
        and next_start.operation_layer == 1
    )
    boundary_compatible = _common_line_boundary_compatible(
        previous_part,
        previous_end,
        next_part,
        next_start,
    )
    common_line = bool(
        allow_common_line
        and channel1_pair
        and same_plane_shape
        and residuals_precise_enough_for_common_line
        and boundary_compatible
    )
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

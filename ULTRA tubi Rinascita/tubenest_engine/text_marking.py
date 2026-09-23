"""SHX stroke text -> channel-4 ZZX marking primitives.

This is the standalone proof-of-concept logic adapted for ULTRA's flat
single-stock exporter. It deliberately emits exploded SHX centerline strokes,
not high-level text entities and not filled/offset glyph outlines.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import math
import re
from pathlib import Path
import struct
import xml.etree.ElementTree as ET

from .bcmp import FormatError, vector
from .geometry import Line, composite


ROMANS_SHA256 = "b22f4abadf72c9184c6c33dbf159b504cc09185a4c24102223afbe58b400f9bd"
MARKING_CHANNEL = 4
PLANAR_ONLY_CURVE_FLAGS = 64
ROUND_CURVE_FLAGS = 0
ROUND_CURVE_TOLERANCE_MM = 0.02
MAX_ROUND_WRAP_RADIANS = math.pi / 2.0
MULTILINE_GAP_RATIO = 0.25


class MarkingFitError(ValueError):
    """The requested marking is valid, but cannot fit this piece safely."""




def _ezdxf_shapefile():
    try:
        from ezdxf.fonts import shapefile
        from ezdxf.path import Path as VectorPath
    except ImportError as exc:
        raise RuntimeError(
            "La marcatura testo richiede ezdxf==1.4.3. "
            "Installa le dipendenze del progetto e riprova."
        ) from exc
    return shapefile, VectorPath


def decode_strokes(font_path, text, height):
    """Decode one horizontal SHX text line to scaled pen-down 2D strokes."""
    height = float(height)
    if not math.isfinite(height) or height <= 0:
        raise ValueError("Height must be finite and positive")

    font_path = Path(font_path)
    if not font_path.is_file():
        raise FileNotFoundError(f"SHX font not found: {font_path}")

    shapefile, VectorPath = _ezdxf_shapefile()
    font = shapefile.readfile(str(font_path))
    if not font.is_font or font.cap_height <= 0:
        raise ValueError("Expected an SHX font with a positive cap height")

    checked = set()
    active = set()
    opcodes = set()

    def check(number):
        if number in active:
            raise ValueError(f"Recursive SHX subshape {number}")
        if number in checked:
            return
        if number not in font.shapes:
            raise ValueError(f"Missing SHX glyph/subshape U+{number:04X}")

        active.add(number)
        codes = font.get_codes(number)
        i = 0
        while i < len(codes):
            op = codes[i]
            i += 1
            opcodes.add(op if op <= 15 else "vector")

            if op == 0:
                break
            if op > 15 or op in (1, 2, 5, 6, 14):
                continue
            if op in (3, 4, 7):
                if i >= len(codes):
                    raise ValueError("Truncated SHX command")
                arg = codes[i]
                i += 1
                if op == 7:
                    check(arg)
                elif arg <= 0:
                    raise ValueError("Invalid SHX scale factor")
            elif op == 8:
                i += 2
            elif op == 9:
                while True:
                    if i + 1 >= len(codes):
                        raise ValueError("Truncated SHX displacement list")
                    x, y = codes[i:i + 2]
                    i += 2
                    if x == 0 and y == 0:
                        break
            else:
                raise ValueError(
                    f"Unsupported SHX opcode {op} in U+{number:04X}; "
                    "no approximation/fallback performed"
                )

            if i > len(codes):
                raise ValueError("Truncated SHX operands")
        else:
            raise ValueError("SHX glyph lacks terminator")

        active.remove(number)
        checked.add(number)

    if not text or any(ord(char) < 32 for char in text):
        raise ValueError("Supply one nonempty text line without control characters")

    for char in text:
        check(ord(char))

    path = VectorPath()
    renderer = shapefile.ShapeRenderer(path, font.get_codes, stacked=False)
    advances = []

    for char in text:
        before = renderer.current_location.x
        renderer.render(ord(char), reset_to_baseline=True)
        advances.append(renderer.current_location.x - before)

    scale = height / font.cap_height
    strokes = []
    for sub in path.sub_paths():
        if not len(sub):
            continue
        points = [(sub.start.x * scale, sub.start.y * scale)]
        for command in sub:
            if command.type.name != "LINE_TO":
                raise ValueError(
                    f"Unexpected SHX path command {command.type.name}"
                )
            point = (command.end.x * scale, command.end.y * scale)
            if point != points[-1]:
                points.append(point)
        if len(points) > 1:
            strokes.append(points)

    if not strokes:
        raise ValueError("Text contains no visible SHX strokes")
    if not all(
        math.isfinite(value)
        for stroke in strokes
        for point in stroke
        for value in point
    ):
        raise ValueError("Nonfinite SHX geometry")

    points = [point for stroke in strokes for point in stroke]
    bounds = [
        list(map(min, zip(*points))),
        list(map(max, zip(*points))),
    ]

    return strokes, {
        "font_sha256": hashlib.sha256(font_path.read_bytes()).hexdigest(),
        "font_name": bytes(font.name).decode("latin1"),
        "cap_height_units": font.cap_height,
        "decoded_characters": list(text),
        "glyph_advances_mm": [advance * scale for advance in advances],
        "validated_shape_numbers": sorted(checked),
        "opcodes": sorted(map(str, opcodes)),
        "text_bounds_2d_mm": bounds,
        "glyph_line_primitives": sum(len(stroke) - 1 for stroke in strokes),
    }


def validate_romans_font(font_path):
    digest = hashlib.sha256(Path(font_path).read_bytes()).hexdigest()
    if digest != ROMANS_SHA256:
        raise ValueError(
            "The bundled romans.shx does not match the validated font "
            f"(expected {ROMANS_SHA256}, got {digest})."
        )
    return digest


_TD_CODE_RE = re.compile(r"^T[DA]\d{4}[A-Z]\d{5}$", re.IGNORECASE)


def marking_layout_candidates(text):
    """Return progressively more compact PROD/[TD]/length layouts.

    TD/TA is optional. When present it is kept in the first two layouts and
    split after T(D/A)+4 digits for the most compact layout.
    """
    value = str(text or "").strip()
    if not value:
        return []

    parts = [part.strip() for part in value.split("|") if part.strip()]
    prod = None
    td_code = None
    length = None

    if len(parts) == 3 and _TD_CODE_RE.match(parts[1]):
        prod, td_code, length = parts
        td_code = td_code.upper()
    elif len(parts) == 2:
        prod, length = parts
    else:
        return [[value]]

    if not prod or not length or not length.upper().startswith("L"):
        return [[value]]

    length = length.upper()
    prod_parts = prod.split(None, 1)
    if (
        len(prod_parts) == 2
        and prod_parts[0].upper() == "PROD"
        and prod_parts[1].strip()
    ):
        compact_prod = ["PROD", prod_parts[1].strip()]
    else:
        compact_prod = [prod]

    stacked = [prod]
    compact = list(compact_prod)
    if td_code:
        stacked.append(td_code)
        compact.extend([td_code[:6], td_code[6:]])
    stacked.append(length)
    compact.append(length)

    candidates = [[value], stacked, compact]
    unique = []
    seen = set()
    for lines in candidates:
        cleaned = tuple(str(line).strip() for line in lines if str(line).strip())
        if cleaned and cleaned not in seen:
            unique.append(list(cleaned))
            seen.add(cleaned)
    return unique

def layout_text_strokes(
    font_path,
    lines,
    height_mm,
    *,
    line_gap_ratio=MULTILINE_GAP_RATIO,
):
    """Decode one or more SHX lines into a single left-aligned 2D layout.

    X is the eventual tube axial direction. Y is the transverse/rotary
    direction. Multiple lines therefore reduce required axial length by
    stacking around the usable face/circumference.
    """
    if isinstance(lines, str):
        lines = [lines]
    lines = [str(line).strip() for line in (lines or []) if str(line).strip()]
    if not lines:
        raise ValueError("Marking layout has no visible lines")

    height_mm = float(height_mm)
    line_gap_ratio = float(line_gap_ratio)
    if not math.isfinite(height_mm) or height_mm <= 0:
        raise ValueError("Height must be finite and positive")
    if not math.isfinite(line_gap_ratio) or line_gap_ratio < 0:
        raise ValueError("Line gap ratio must be finite and nonnegative")

    decoded = []
    for line in lines:
        strokes, report = decode_strokes(font_path, line, height_mm)
        lo, hi = report["text_bounds_2d_mm"]
        decoded.append(
            {
                "line": line,
                "strokes": strokes,
                "report": report,
                "lo": (float(lo[0]), float(lo[1])),
                "hi": (float(hi[0]), float(hi[1])),
            }
        )

    gap = height_mm * line_gap_ratio
    line_heights = [
        max(0.0, item["hi"][1] - item["lo"][1])
        for item in decoded
    ]
    total_height = sum(line_heights) + gap * max(0, len(decoded) - 1)
    cursor_top = total_height / 2.0

    combined = []
    for item, line_height in zip(decoded, line_heights):
        x_shift = -item["lo"][0]
        y_shift = cursor_top - item["hi"][1]
        for stroke in item["strokes"]:
            combined.append(
                [
                    (float(x) + x_shift, float(y) + y_shift)
                    for x, y in stroke
                ]
            )
        cursor_top -= line_height + gap

    points = [point for stroke in combined for point in stroke]
    lo = list(map(min, zip(*points)))
    hi = list(map(max, zip(*points)))

    first_report = dict(decoded[0]["report"])
    first_report.update(
        {
            "decoded_characters": [
                char
                for item in decoded
                for char in item["line"]
            ],
            "glyph_advances_mm": [
                value
                for item in decoded
                for value in item["report"].get("glyph_advances_mm", [])
            ],
            "validated_shape_numbers": sorted(
                {
                    value
                    for item in decoded
                    for value in item["report"].get(
                        "validated_shape_numbers",
                        [],
                    )
                }
            ),
            "opcodes": sorted(
                {
                    str(value)
                    for item in decoded
                    for value in item["report"].get("opcodes", [])
                }
            ),
            "glyph_line_primitives": sum(
                int(item["report"].get("glyph_line_primitives", 0))
                for item in decoded
            ),
            "text_bounds_2d_mm": [lo, hi],
            "layout_lines": list(lines),
            "line_count": len(lines),
            "line_gap_mm": gap,
            "visible_axial_width_mm": hi[0] - lo[0],
            "visible_transverse_height_mm": hi[1] - lo[1],
        }
    )
    return combined, first_report


def _build_shape_records(
    *,
    source_shape_record,
    paths,
    first_handle,
    curve_flags,
    planar_normal,
):
    shape_records = []
    geometry_records = []
    shape_elements = []
    segment_refs = []
    next_handle = int(first_handle)

    for points in paths:
        geometry_record = composite(
            [
                Line(
                    point,
                    tuple(b - a for a, b in zip(point, next_point)),
                )
                for point, next_point in zip(points, points[1:])
            ]
        )

        shape_record = deepcopy(source_shape_record)
        object_block = next(
            block for block in shape_record.blocks
            if block.name == "Object"
        )
        object_block.payload = struct.pack("<I", next_handle)

        shape_block = next(
            block for block in shape_record.blocks
            if block.name == "Shape"
        )
        shape_payload = bytearray(shape_block.payload)
        if len(shape_payload) < 12:
            raise FormatError("Unexpected Shape block for marking template")
        struct.pack_into("<I", shape_payload, 0, MARKING_CHANNEL)
        # Never inherit a cutoff/common-line do-not-cut marker from the
        # prototype end cut.
        struct.pack_into("<I", shape_payload, 8, 0)
        shape_block.payload = bytes(shape_payload)

        curve_block = next(
            block for block in shape_record.blocks
            if block.name == "Curve"
        )
        curve_payload = bytearray(curve_block.payload)
        if len(curve_payload) < 44:
            raise FormatError("Unexpected Curve block for marking template")
        struct.pack_into(
            "<IdI",
            curve_payload,
            0,
            0,
            0.0,
            int(curve_flags),
        )
        curve_payload[16:44] = vector(tuple(map(float, planar_normal)))
        curve_block.payload = bytes(curve_payload)

        shape_element = ET.Element(
            "GeoCurve",
            Class="GeoCurve",
            Handle=str(next_handle),
        )
        geometry_element = ET.SubElement(
            shape_element,
            "Geometry",
            Class="CompositeCurve3D",
        )
        segment_ref = ET.Element("GeoCurve", Handle=str(next_handle))

        shape_records.append(shape_record)
        geometry_records.append(geometry_record)
        shape_elements.append((shape_element, geometry_element))
        segment_refs.append(segment_ref)
        next_handle += 1

    return {
        "shape_records": shape_records,
        "geometry_records": geometry_records,
        "shape_elements": shape_elements,
        "segment_refs": segment_refs,
        "next_handle": next_handle,
    }


def build_marking_records(
    *,
    source_shape_record,
    font_path,
    height_mm,
    start_z,
    corner_radius,
    max_z,
    first_handle,
    text=None,
    lines=None,
    face="+Y",
    outside_width=None,
    outside_height=None,
    face_width=None,
    face_y=None,
    prepared_layout=None,
):
    """Build planar channel-4 text on one true flat tube face.

    The four supported faces are +Y, +X, -Y and -X. The usable transverse
    extent always excludes the rounded corner radius on both sides.
    """
    height_mm = float(height_mm)
    start_z = float(start_z)
    corner_radius = max(0.0, float(corner_radius or 0.0))
    max_z = float(max_z)
    face = str(face or "+Y").upper()

    if outside_width is None:
        outside_width = face_width
    if outside_height is None and face_y is not None:
        outside_height = abs(float(face_y)) * 2.0
    outside_width = float(outside_width or 0.0)
    outside_height = float(outside_height or 0.0)
    if outside_width <= 0.0 or outside_height <= 0.0:
        raise ValueError("Flat tube outside dimensions must be positive")

    layout_lines = lines if lines is not None else [text]
    if prepared_layout is None:
        strokes, base_report = layout_text_strokes(
            font_path,
            layout_lines,
            height_mm,
        )
    else:
        strokes, base_report = prepared_layout
    report = dict(base_report)

    if face == "+Y":
        transverse_limit = outside_width / 2.0 - corner_radius
        normal = (0.0, 1.0, 0.0)

        def lift(point):
            return (
                float(point[1]),
                outside_height / 2.0,
                start_z + float(point[0]),
            )

    elif face == "+X":
        transverse_limit = outside_height / 2.0 - corner_radius
        normal = (1.0, 0.0, 0.0)

        def lift(point):
            return (
                outside_width / 2.0,
                -float(point[1]),
                start_z + float(point[0]),
            )

    elif face == "-Y":
        transverse_limit = outside_width / 2.0 - corner_radius
        normal = (0.0, -1.0, 0.0)

        def lift(point):
            return (
                -float(point[1]),
                -outside_height / 2.0,
                start_z + float(point[0]),
            )

    elif face == "-X":
        transverse_limit = outside_height / 2.0 - corner_radius
        normal = (-1.0, 0.0, 0.0)

        def lift(point):
            return (
                -outside_width / 2.0,
                float(point[1]),
                start_z + float(point[0]),
            )

    else:
        raise ValueError(f"Unsupported flat marking face {face!r}")

    paths = [[lift(point) for point in stroke] for stroke in strokes]
    if transverse_limit <= 0:
        raise MarkingFitError("Tube profile has no usable flat marking face")

    for stroke_2d in strokes:
        for _u, transverse in stroke_2d:
            if abs(float(transverse)) >= transverse_limit - 1e-9:
                raise MarkingFitError(
                    f"Text layout reaches the rounded edge of face {face} "
                    f"(transverse={float(transverse):.3f}, "
                    f"planar limit={transverse_limit:.3f})."
                )

    for path in paths:
        for x, y, z in path:
            if not all(math.isfinite(value) for value in (x, y, z)):
                raise ValueError("Nonfinite marking point")
            if not (start_z - 1e-6 <= z < max_z - 1e-6):
                raise MarkingFitError(
                    "Text layout does not fit between this piece's end cuts "
                    f"({start_z:.3f} .. {max_z:.3f} mm)."
                )

    result = _build_shape_records(
        source_shape_record=source_shape_record,
        paths=paths,
        first_handle=first_handle,
        curve_flags=PLANAR_ONLY_CURVE_FLAGS,
        planar_normal=normal,
    )

    all_points = [point for path in paths for point in path]
    report.update(
        {
            "text": "\n".join(report["layout_lines"]),
            "height_mm": height_mm,
            "face": face,
            "profile_kind": "flat",
            "marking_shapes": len(result["shape_records"]),
            "new_shape_handles": [
                int(element.get("Handle"))
                for element, _geometry in result["shape_elements"]
            ],
            "new_shape_channels": [MARKING_CHANNEL],
            "start_z": start_z,
            "max_z": max_z,
            "outside_width_mm": outside_width,
            "outside_height_mm": outside_height,
            "corner_radius_mm": corner_radius,
            "flat_transverse_limit": transverse_limit,
            # Backward-compatible diagnostic key used by older tests/tools.
            "flat_x_limit": transverse_limit,
            "curve_flags": PLANAR_ONLY_CURVE_FLAGS,
            "planar_normal": list(normal),
            "geometry_3d_bounds": [
                list(map(min, zip(*all_points))),
                list(map(max, zip(*all_points))),
            ],
        }
    )
    result["report"] = report
    return result

def wrap_strokes_to_cylinder(
    strokes,
    radius,
    start_z,
    *,
    circumferential_center_deg=0.0,
    curve_tolerance_mm=ROUND_CURVE_TOLERANCE_MM,
):
    """Map 2D marking strokes onto the outside cylinder with bounded chords."""
    radius = float(radius)
    start_z = float(start_z)
    center_deg = float(circumferential_center_deg)
    tolerance = float(curve_tolerance_mm)

    if not all(
        math.isfinite(value)
        for value in (radius, start_z, center_deg, tolerance)
    ):
        raise ValueError("Nonfinite round marking parameter")
    if radius <= 0:
        raise ValueError("Round tube outside radius must be positive")
    if not (0.0 < tolerance < radius):
        raise ValueError("Require 0 < round curve tolerance < outside radius")

    points = [point for stroke in strokes for point in stroke]
    ulo = min(float(point[0]) for point in points)
    vlo = min(float(point[1]) for point in points)
    vhi = max(float(point[1]) for point in points)
    vc = (vlo + vhi) / 2.0

    angular_span = (vhi - vlo) / radius
    if angular_span > MAX_ROUND_WRAP_RADIANS + 1e-12:
        raise MarkingFitError(
            "Text layout would wrap more than 90 degrees around the round tube"
        )

    center = math.radians(center_deg)
    step = min(
        math.sqrt(8.0 * tolerance / radius),
        math.pi / 18.0,
    )

    paths = []
    max_bound = 0.0
    max_sag = 0.0
    segment_count = 0

    def mapped(u, v):
        theta = center + (float(v) - vc) / radius
        return (
            radius * math.sin(theta),
            radius * math.cos(theta),
            start_z + float(u) - ulo,
        )

    for stroke in strokes:
        out = [mapped(*stroke[0])]
        for first, second in zip(stroke, stroke[1:]):
            delta = (float(second[1]) - float(first[1])) / radius
            count = max(1, math.ceil(abs(delta) / step))
            segment_count += count
            if segment_count > 200000:
                raise ValueError(
                    "Round marking tessellation exceeds 200000 primitives"
                )

            per_segment = abs(delta) / count
            max_bound = max(
                max_bound,
                radius * per_segment * per_segment / 8.0,
            )
            max_sag = max(
                max_sag,
                2.0 * radius * math.sin(per_segment / 4.0) ** 2,
            )
            for index in range(1, count + 1):
                factor = index / count
                u = float(first[0]) + factor * (
                    float(second[0]) - float(first[0])
                )
                v = float(first[1]) + factor * (
                    float(second[1]) - float(first[1])
                )
                out.append(mapped(u, v))
        paths.append(out)

    return paths, {
        "outside_radius_mm": radius,
        "start_z_mm": start_z,
        "curve_tolerance_mm": tolerance,
        "theta_min_rad": center + (vlo - vc) / radius,
        "theta_max_rad": center + (vhi - vc) / radius,
        "angular_height_deg": math.degrees(angular_span),
        "visible_arc_height_mm": vhi - vlo,
        "max_chord_surface_deviation_mm": max_sag,
        "max_parametric_curve_error_bound_mm": max_bound,
        "generated_3d_primitives": segment_count,
        "generated_strokes": len(paths),
        "max_vertex_radius_error_mm": max(
            abs(math.hypot(x, y) - radius)
            for path in paths
            for x, y, _z in path
        ),
        "min_z_mm": min(point[2] for path in paths for point in path),
        "max_z_mm": max(point[2] for path in paths for point in path),
    }


def build_round_marking_records(
    *,
    source_shape_record,
    font_path,
    height_mm,
    start_z,
    radius,
    max_z,
    first_handle,
    text=None,
    lines=None,
    circumferential_center_deg=0.0,
    curve_tolerance_mm=ROUND_CURVE_TOLERANCE_MM,
    prepared_layout=None,
):
    """Build channel-4 marking geometry conformed to a round tube surface."""
    height_mm = float(height_mm)
    start_z = float(start_z)
    max_z = float(max_z)
    radius = float(radius)

    layout_lines = lines if lines is not None else [text]
    if prepared_layout is None:
        strokes, base_report = layout_text_strokes(
            font_path,
            layout_lines,
            height_mm,
        )
    else:
        strokes, base_report = prepared_layout
    report = dict(base_report)
    paths, geometry_report = wrap_strokes_to_cylinder(
        strokes,
        radius,
        start_z,
        circumferential_center_deg=circumferential_center_deg,
        curve_tolerance_mm=curve_tolerance_mm,
    )

    if geometry_report["max_z_mm"] >= max_z - 1e-6:
        raise MarkingFitError(
            "Text layout does not fit between this piece's end cuts "
            f"({start_z:.3f} .. {max_z:.3f} mm)."
        )

    result = _build_shape_records(
        source_shape_record=source_shape_record,
        paths=paths,
        first_handle=first_handle,
        curve_flags=ROUND_CURVE_FLAGS,
        planar_normal=(0.0, 0.0, 0.0),
    )
    report.update(geometry_report)
    all_points = [point for path in paths for point in path]
    report.update(
        {
            "text": "\n".join(report["layout_lines"]),
            "geometry_3d_bounds": [
                list(map(min, zip(*all_points))),
                list(map(max, zip(*all_points))),
            ],
            "height_mm": height_mm,
            "face": "round outside cylinder centered at local +Y",
            "profile_kind": "Circle",
            "circumferential_center_deg": float(circumferential_center_deg),
            "marking_shapes": len(result["shape_records"]),
            "new_shape_handles": [
                int(element.get("Handle"))
                for element, _geometry in result["shape_elements"]
            ],
            "new_shape_channels": [MARKING_CHANNEL],
            "max_z": max_z,
            "curve_flags": ROUND_CURVE_FLAGS,
            "planar_normal": [0.0, 0.0, 0.0],
            "representation": (
                "CompositeCurve3D with subdivided Line3D chords on the "
                "outside cylinder"
            ),
        }
    )
    result["report"] = report
    return result

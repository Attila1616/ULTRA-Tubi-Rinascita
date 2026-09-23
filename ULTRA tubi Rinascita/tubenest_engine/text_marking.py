"""SHX stroke text -> channel-4 ZZX marking primitives.

This is the standalone proof-of-concept logic adapted for ULTRA's flat
single-stock exporter. It deliberately emits exploded SHX centerline strokes,
not high-level text entities and not filled/offset glyph outlines.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import math
from pathlib import Path
import struct
import xml.etree.ElementTree as ET

from .bcmp import FormatError, vector
from .geometry import Line, composite


ROMANS_SHA256 = "b22f4abadf72c9184c6c33dbf159b504cc09185a4c24102223afbe58b400f9bd"
MARKING_CHANNEL = 4
PLANAR_ONLY_CURVE_FLAGS = 64


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


def build_marking_records(
    *,
    source_shape_record,
    font_path,
    text,
    height_mm,
    start_z,
    face_width,
    face_y,
    corner_radius,
    max_z,
    first_handle,
):
    """Build channel-4 text records/XML for one nested piece.

    Text visible bounds are left-aligned at start_z and centered across
    the selected +Y flat face. One connected SHX pen-down stroke becomes one
    marking GeoCurve.
    """
    height_mm = float(height_mm)
    start_z = float(start_z)
    face_width = float(face_width)
    face_y = float(face_y)
    corner_radius = max(0.0, float(corner_radius or 0.0))
    max_z = float(max_z)
    first_handle = int(first_handle)

    strokes, report = decode_strokes(font_path, text, height_mm)
    lo, hi = report["text_bounds_2d_mm"]
    visible_left = float(lo[0])
    visible_vertical_center = (float(lo[1]) + float(hi[1])) / 2.0

    def lift(point):
        return (
            float(point[1]) - visible_vertical_center,
            face_y,
            start_z + (float(point[0]) - visible_left),
        )

    paths = [[lift(point) for point in stroke] for stroke in strokes]
    flat_limit = face_width / 2.0 - corner_radius
    if flat_limit <= 0:
        raise ValueError("Tube profile has no usable flat marking face")

    for path in paths:
        for x, y, z in path:
            if not all(math.isfinite(value) for value in (x, y, z)):
                raise ValueError("Nonfinite marking point")
            if abs(x) >= flat_limit:
                raise ValueError(
                    f"Text '{text}' at {height_mm:g} mm does not fit the "
                    f"flat tube face (X={x:.3f}, limit={flat_limit:.3f})."
                )
            if not (start_z - 1e-6 <= z < max_z - 1e-6):
                raise ValueError(
                    f"Text '{text}' does not fit between this piece's end cuts "
                    f"({start_z:.3f} .. {max_z:.3f} mm)."
                )

    shape_records = []
    geometry_records = []
    shape_elements = []
    segment_refs = []
    next_handle = first_handle

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
            PLANAR_ONLY_CURVE_FLAGS,
        )
        curve_payload[16:44] = vector((0.0, 1.0, 0.0))
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

    all_points = [point for path in paths for point in path]
    report.update(
        {
            "text": text,
            "height_mm": height_mm,
            "face": "+Y",
            "marking_shapes": len(shape_records),
            "new_shape_handles": [
                int(element.get("Handle"))
                for element, _geometry in shape_elements
            ],
            "new_shape_channels": [MARKING_CHANNEL],
            "start_z": start_z,
            "max_z": max_z,
            "face_y": face_y,
            "flat_x_limit": flat_limit,
            "geometry_3d_bounds": [
                list(map(min, zip(*all_points))),
                list(map(max, zip(*all_points))),
            ],
        }
    )

    return {
        "shape_records": shape_records,
        "geometry_records": geometry_records,
        "shape_elements": shape_elements,
        "segment_refs": segment_refs,
        "next_handle": next_handle,
        "report": report,
    }

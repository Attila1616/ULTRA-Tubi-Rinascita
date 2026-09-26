"""Build one multi-segment ZZX from an ULTRA locked nesting rod.

The exporter deliberately preserves source machining geometry. Each source
TubeSegment's Shapes and referenced LiteGeos records are cloned byte-for-byte;
nesting is represented by the TubeSegment rigid transform. Unknown shape
payloads therefore survive unchanged.

Scope/safety:
- only the observed writable NestResults3D FileVer 65542 dialect is emitted;
- no laser technology, gas, speed or compensation values are invented;
- output is structurally validated but still requires Friendess/TubesT review
  before any machine use.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import math
from pathlib import Path
import re
import struct
import xml.etree.ElementTree as ET

from .archive import Archive, xml_bytes
from .bcmp import FormatError, Stream, read_vector, vector
from .domain import read_tube_parts
from .fit import PartPose, fit_adjacent_parts
from .geometry import Line, Spline, primitives
from .release_starts import select_face_start, select_round_start
from .text_marking import (
    MarkingFitError,
    build_marking_records,
    build_round_marking_records,
    layout_text_strokes,
    marking_layout_candidates,
    validate_romans_font,
)


WRITABLE_FILE_VERSION = "65542"
COMMON_LINE_FLAG = 0x04
EXCLUSION_WORK_BIT = 0x2
MARKING_COLLISION_CLEARANCE_MM = 1.0
ROUND_MARKING_ANGLE_STEP_DEG = 15


@dataclass
class _ResolvedPlacement:
    source_path: str
    source_archive: Archive
    source_segment_xml: ET.Element
    source_segment_record: object
    source_part: object
    placement: dict
    instance_key: str
    source_file_name: str
    default_channel: int
    marking_text: str | None = None


def _empty_root_like(root):
    return ET.Element(root.tag, dict(root.attrib))


def _record_by_address(archive, section):
    return {record.address: record for record in archive.stream(section).records}


def _xml_by_handle(archive, section):
    root = archive.xml(section + "/content.xml")
    return {
        int(element.get("Handle")): element
        for element in root
        if element.tag != "MD5" and element.get("Handle") is not None
    }


def _shape_geometry_source(shape_xml_by_handle, element):
    """Follow source CopyHandle links until real geometry XML is found."""
    current = element
    seen = set()
    while True:
        handle = int(current.get("Handle"))
        if handle in seen:
            raise FormatError("Cyclic Shape CopyHandle chain")
        seen.add(handle)

        if any(child.get("GeoAddr") is not None for child in list(current)):
            return current

        copy_handle = current.get("CopyHandle")
        if copy_handle is None:
            return current
        current = shape_xml_by_handle.get(int(copy_handle))
        if current is None:
            raise FormatError(f"Dangling source Shape CopyHandle {copy_handle}")


def _set_object_handle(record, handle):
    block = next(
        (block for block in record.blocks if block.name == "Object" and len(block.payload) >= 4),
        None,
    )
    if block is None:
        raise FormatError(f"{record.name} record has no writable Object handle")
    payload = bytearray(block.payload)
    struct.pack_into("<I", payload, 0, int(handle))
    block.payload = bytes(payload)


def _shape_channel(record):
    block = next(
        (block for block in record.blocks if block.name == "Shape" and len(block.payload) >= 4),
        None,
    )
    if block is None:
        return 0
    return int(struct.unpack_from("<I", block.payload, 0)[0])


def _shape_do_not_cut(record):
    block = next(
        (
            block
            for block in record.blocks
            if block.name == "Shape" and len(block.payload) >= 12
        ),
        None,
    )
    if block is None:
        return False
    return bool(struct.unpack_from("<I", block.payload, 8)[0] & EXCLUSION_WORK_BIT)


def _object_handle(record):
    block = next(
        (
            block
            for block in record.blocks
            if block.name == "Object" and len(block.payload) >= 4
        ),
        None,
    )
    if block is None:
        raise FormatError(f"{record.name} record has no Object handle")
    return int(struct.unpack_from("<I", block.payload, 0)[0])


def _curve_flags(record):
    block = next(
        (
            block
            for block in record.blocks
            if block.name == "Curve" and len(block.payload) >= 16
        ),
        None,
    )
    return 0 if block is None else int(struct.unpack_from("<I", block.payload, 12)[0])


def _make_inert_geometry_master(record, handle):
    """Clone an active Shape as a first-segment geometry-only catalog master."""
    master = copy.deepcopy(record)
    _set_object_handle(master, handle)

    shape_block = next(
        (
            block
            for block in master.blocks
            if block.name == "Shape" and len(block.payload) >= 12
        ),
        None,
    )
    if shape_block is None:
        raise FormatError("Geometry master Shape has no writable Shape block")
    payload = bytearray(shape_block.payload)
    struct.pack_into("<I", payload, 0, 0)
    work = struct.unpack_from("<I", payload, 8)[0]
    struct.pack_into("<I", payload, 8, work & ~EXCLUSION_WORK_BIT)
    shape_block.payload = bytes(payload)

    curve_block = next(
        (
            block
            for block in master.blocks
            if block.name == "Curve" and len(block.payload) >= 16
        ),
        None,
    )
    if curve_block is None:
        raise FormatError("Geometry master Shape has no writable Curve block")
    payload = bytearray(curve_block.payload)
    struct.pack_into("<I", payload, 12, 0)
    curve_block.payload = bytes(payload)
    return master


def _geometry_catalog_signature(shape_element, geometry_pairs):
    digest = hashlib.sha256()
    digest.update(str(shape_element.tag).encode("utf-8"))
    for child, record in geometry_pairs:
        digest.update(str(child.tag).encode("utf-8"))
        digest.update(str(child.get("Class") or "").encode("utf-8"))
        digest.update(str(record.name).encode("ascii", "strict"))
        digest.update(struct.pack("<I", int(record.version)))
        for block in record.blocks:
            digest.update(str(block.name).encode("ascii", "strict"))
            digest.update(struct.pack("<I", int(block.version)))
            digest.update(struct.pack("<I", len(block.payload)))
            digest.update(block.payload)
        digest.update(record.tail)
    return digest.hexdigest()


def _geometry_z_bounds(geometry_pairs):
    values = []
    for _child, record in geometry_pairs:
        for curve in primitives(record):
            values.extend(float(point[2]) for point in curve.sample(64))
    if not values:
        raise FormatError("Geometry catalog entry has no sampled 3D points")
    return min(values), max(values)


def _order_segment_refs_release_safe(
    segment_xml,
    output_record_by_handle,
    *,
    near_handle,
    far_handle,
):
    refs_parent = segment_xml.find("Shapes")
    if refs_parent is None:
        raise FormatError("TubeSegment has no Shapes list")

    near_handle = str(near_handle)
    far_handle = str(far_handle)
    near_ref = None
    far_ref = None
    internal = []
    display = []

    for index, ref in enumerate(list(refs_parent)):
        handle = str(ref.get("Handle"))
        if handle == near_handle:
            near_ref = ref
            continue
        if handle == far_handle:
            far_ref = ref
            continue

        record = output_record_by_handle.get(handle)
        if record is None or _shape_channel(record) <= 0:
            display.append((index, ref))
        else:
            internal.append((index, ref))

    if near_ref is None or far_ref is None:
        raise FormatError(
            f"Cannot resolve physical near/far cutoffs {near_handle}/{far_handle}"
        )

    refs_parent[:] = (
        [near_ref]
        + [ref for _index, ref in internal]
        + [far_ref]
        + [ref for _index, ref in display]
    )


def _frame_point(frame, point):
    basis_x, basis_y, basis_z, origin = frame
    x, y, z = map(float, point)
    return tuple(
        float(origin[row])
        + float(basis_x[row]) * x
        + float(basis_y[row]) * y
        + float(basis_z[row]) * z
        for row in range(3)
    )


def _frame_vector(frame, direction):
    basis_x, basis_y, basis_z, _origin = frame
    x, y, z = map(float, direction)
    return tuple(
        float(basis_x[row]) * x
        + float(basis_y[row]) * y
        + float(basis_z[row]) * z
        for row in range(3)
    )


def _inverse_frame_point(frame, point):
    basis_x, basis_y, basis_z, origin = frame
    delta = tuple(float(point[i]) - float(origin[i]) for i in range(3))
    return (
        sum(delta[i] * float(basis_x[i]) for i in range(3)),
        sum(delta[i] * float(basis_y[i]) for i in range(3)),
        sum(delta[i] * float(basis_z[i]) for i in range(3)),
    )


def _inverse_frame_vector(frame, direction):
    basis_x, basis_y, basis_z, _origin = frame
    values = tuple(map(float, direction))
    return (
        sum(values[i] * float(basis_x[i]) for i in range(3)),
        sum(values[i] * float(basis_y[i]) for i in range(3)),
        sum(values[i] * float(basis_z[i]) for i in range(3)),
    )


def _posed_curve(curve, frame):
    if isinstance(curve, Line):
        start = _frame_point(frame, curve.at(0.0))
        end = _frame_point(frame, curve.at(1.0))
        return Line(start, tuple(b - a for a, b in zip(start, end)))
    if isinstance(curve, Spline):
        return Spline(
            curve.degree,
            list(curve.knots),
            [_frame_point(frame, point) for point in curve.points],
            list(curve.weights),
            curve.flag,
        )
    raise ValueError(
        f"Unsupported cutoff primitive {type(curve).__name__} for release start"
    )


def _source_shape_curves(source_shape_xml, source_shape_xml_map, source_lite_records):
    geometry_source = _shape_geometry_source(
        source_shape_xml_map,
        source_shape_xml,
    )
    geometry = geometry_source.find("Geometry")
    if geometry is None or geometry.get("GeoAddr") is None:
        return []
    record = source_lite_records.get(int(geometry.get("GeoAddr")))
    if record is None:
        raise FormatError(
            f"Missing LiteGeos {geometry.get('GeoAddr')} for cutoff geometry"
        )
    return list(primitives(record))


def _posed_shape_points(
    source_shape_xml,
    source_shape_xml_map,
    source_lite_records,
    frame,
):
    geometry_source = _shape_geometry_source(
        source_shape_xml_map,
        source_shape_xml,
    )
    points = []
    for child in geometry_source:
        geo_addr = child.get("GeoAddr")
        if geo_addr is None:
            continue
        record = source_lite_records.get(int(geo_addr))
        if record is None:
            continue
        for curve in primitives(record):
            points.extend(
                _frame_point(frame, point)
                for point in curve.sample(64)
            )
    return points


def _xyz_bounds(points):
    finite = [
        tuple(map(float, point[:3]))
        for point in points
        if len(point) >= 3
        and all(math.isfinite(float(value)) for value in point[:3])
    ]
    if not finite:
        return None
    return [
        list(map(min, zip(*finite))),
        list(map(max, zip(*finite))),
    ]


def _bounds_overlap(first, second, clearance=0.0):
    clearance = max(0.0, float(clearance))
    for axis in range(3):
        if first[1][axis] < second[0][axis] - clearance:
            return False
        if first[0][axis] > second[1][axis] + clearance:
            return False
    return True


def _candidate_marking_start_positions(
    preferred_start,
    max_z,
    axial_width,
    obstacles,
    *,
    clearance=MARKING_COLLISION_CLEARANCE_MM,
):
    preferred_start = float(preferred_start)
    max_z = float(max_z)
    axial_width = max(0.0, float(axial_width))
    clearance = max(0.0, float(clearance))
    latest_start = max_z - axial_width - 1e-6
    if latest_start < preferred_start - 1e-9:
        return []

    candidates = {preferred_start, latest_start}
    for obstacle in obstacles:
        lo_z = float(obstacle["bounds"][0][2])
        hi_z = float(obstacle["bounds"][1][2])
        candidates.add(hi_z + clearance)
        candidates.add(lo_z - axial_width - clearance)

    return sorted(
        {
            round(value, 6)
            for value in candidates
            if preferred_start - 1e-6 <= value <= latest_start + 1e-6
        }
    )


def _flat_marking_faces():
    return ("+Y", "+X", "-Y", "-X")


def _round_marking_angles():
    preferred = [0, 90, 180, 270]
    return preferred + [
        angle
        for angle in range(0, 360, ROUND_MARKING_ANGLE_STEP_DEG)
        if angle not in preferred
    ]


def _inverse_transform_marking_geometry(record, frame):
    for block in record.blocks:
        if block.name != "Line3D" or not block.payload:
            continue
        if len(block.payload) != 56:
            raise FormatError("Unexpected generated Line3D payload")
        start = read_vector(block.payload, 0)
        direction = read_vector(block.payload, 28)
        end = tuple(a + b for a, b in zip(start, direction))
        local_start = _inverse_frame_point(frame, start)
        local_end = _inverse_frame_point(frame, end)
        block.payload = vector(local_start) + vector(
            tuple(b - a for a, b in zip(local_start, local_end))
        )


def _inverse_transform_marking_shape(record, frame):
    block = next(
        (
            block
            for block in record.blocks
            if block.name == "Curve" and len(block.payload) >= 44
        ),
        None,
    )
    if block is None:
        return
    normal = read_vector(block.payload, 16)
    if math.sqrt(sum(float(value) ** 2 for value in normal)) <= 1e-12:
        return
    payload = bytearray(block.payload)
    payload[16:44] = vector(_inverse_frame_vector(frame, normal))
    block.payload = bytes(payload)


def _normalize_release_selection_z(curves, stock_min_z):
    """Shift only the release-selection view so positioned stock starts at Z=0.

    Native multi-segment ZZX keeps machining geometry in each TubeSegment's
    local coordinate system. Angled end cuts can therefore extend below local
    Z=0 even though the physical positioned stock is valid. The release-start
    rules are defined in nonnegative positioned-stock coordinates, so apply a
    temporary axial translation for selection only. Native curve domains and
    the serialized geometry remain unchanged.
    """
    stock_min_z = float(stock_min_z)
    if not math.isfinite(stock_min_z):
        raise ValueError("Nonfinite positioned-stock minimum Z")

    if abs(stock_min_z) <= 1e-12:
        return list(curves)

    frame = (
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
        (0.0, 0.0, -stock_min_z),
    )
    return [_posed_curve(curve, frame) for curve in curves]


def _select_release_start(curves, profile):
    if profile.kind == "Circle":
        diameter = float(profile.outside_diameter or 0.0)
        if not math.isfinite(diameter) or diameter <= 0.0:
            raise ValueError("Invalid circle diameter for release start")
        return select_round_start(curves, diameter / 2.0)
    if profile.kind in ("Square", "Rect"):
        width = float(profile.outside_width or 0.0)
        height = float(profile.outside_height or 0.0)
        if not all(math.isfinite(v) and v > 0 for v in (width, height)):
            raise ValueError("Invalid flat profile dimensions for release start")
        return select_face_start(curves, width, height)
    raise ValueError(
        f"Unsupported profile {profile.kind!r} for release start correction"
    )


def _write_release_start(shape_record, parameter):
    block = next(
        (
            block
            for block in shape_record.blocks
            if block.name == "Curve" and len(block.payload) >= 44
        ),
        None,
    )
    if block is None:
        raise FormatError("Cutoff Shape has no writable Curve block")
    payload = bytearray(block.payload)
    before = struct.unpack_from("<d", payload, 4)[0]
    struct.pack_into("<d", payload, 4, float(parameter))
    block.payload = bytes(payload)
    return before


def _read_segment_frame(record):
    block = next(
        (block for block in record.blocks if block.name == "TubeSegment"),
        None,
    )
    if block is None or len(block.payload) < 120:
        raise FormatError("Unsupported TubeSegment transform payload")

    return (
        tuple(read_vector(block.payload, 8)),
        tuple(read_vector(block.payload, 36)),
        tuple(read_vector(block.payload, 64)),
        tuple(read_vector(block.payload, 92)),
    )


def _linear_combination(basis, coefficients):
    return tuple(
        sum(float(basis[column][row]) * float(coefficients[column]) for column in range(3))
        for row in range(3)
    )


def _set_segment_transform(record, part, placement):
    """Compose only the physical pose onto the source-local segment frame.

    Native TubesT multi-piece ZZX keeps every segment in its source-local
    axial coordinate system. PackSegments + WorkSeq performs the axial packing.
    Therefore z_start/z_end MUST NOT be written into the TubeSegment
    translation.
    """
    block = next(
        (block for block in record.blocks if block.name == "TubeSegment"),
        None,
    )
    if block is None or len(block.payload) < 120:
        raise FormatError("Unsupported TubeSegment transform payload")

    source_x, source_y, source_z, source_origin = _read_segment_frame(record)

    angle = math.radians(float(placement.get("axial_rotation_degrees") or 0.0))
    co, si = math.cos(angle), math.sin(angle)
    reversed_end = bool(placement.get("reversed_end_for_end"))

    if reversed_end:
        pose_x = (-co, -si, 0.0)
        pose_y = (-si, co, 0.0)
        pose_z = (0.0, 0.0, -1.0)
        # Preserve the original raw axial interval while swapping its ends:
        # z' = axial_min + axial_max - z.
        pose_translation = (
            0.0,
            0.0,
            float(part.axial_min) + float(part.axial_max),
        )
    else:
        pose_x = (co, si, 0.0)
        pose_y = (-si, co, 0.0)
        pose_z = (0.0, 0.0, 1.0)
        pose_translation = (0.0, 0.0, 0.0)

    basis = (source_x, source_y, source_z)
    basis_x = _linear_combination(basis, pose_x)
    basis_y = _linear_combination(basis, pose_y)
    basis_z = _linear_combination(basis, pose_z)
    translated = _linear_combination(basis, pose_translation)
    origin = tuple(float(source_origin[i]) + translated[i] for i in range(3))

    payload = bytearray(block.payload)
    for offset, values in (
        (8, basis_x),
        (36, basis_y),
        (64, basis_z),
        (92, origin),
    ):
        payload[offset:offset + 28] = vector(values)
    block.payload = bytes(payload)


def _set_pack_options(record, gap_mm, common_line_enabled):
    """Write the TubesT-observed pack fields without guessing the last word.

    Reference arrays keep the configured gap in the float64 at offset 0.
    Their uint32 at offset 8 is 0 for normal arrays and 1 when co-edge is
    enabled. The uint32 at offset 12 is preserved verbatim.
    """
    block = next(
        (
            block
            for block in record.blocks
            if block.name == "TubeSegments"
        ),
        None,
    )
    if block is None or len(block.payload) < 16:
        raise FormatError("Unsupported PackSegments payload")
    gap_mm = float(gap_mm)
    if not math.isfinite(gap_mm) or gap_mm < 0:
        raise ValueError("gap_mm must be finite and non-negative")
    payload = bytearray(block.payload)
    struct.pack_into("<d", payload, 0, gap_mm)
    struct.pack_into("<I", payload, 8, 1 if common_line_enabled else 0)
    block.payload = bytes(payload)


def _set_common_line_flag(shape_record):
    block = next(
        (block for block in shape_record.blocks if block.name == "Curve" and len(block.payload) >= 16),
        None,
    )
    if block is None:
        return False
    payload = bytearray(block.payload)
    flags = struct.unpack_from("<I", payload, 12)[0]
    struct.pack_into("<I", payload, 12, flags | COMMON_LINE_FLAG)
    block.payload = bytes(payload)
    return True


def _sanitize_name(value):
    safe = re.sub(r"[^A-Za-z0-9._+-]+", "_", str(value or "")).strip("._")
    return safe or "Nested_Rod"


def _unique_output_path(output_dir, suggested_name):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = _sanitize_name(Path(suggested_name).stem)
    candidate = output_dir / f"{stem}.zzx"
    if not candidate.exists():
        return candidate
    for index in range(2, 10000):
        candidate = output_dir / f"{stem}_{index}.zzx"
        if not candidate.exists():
            return candidate
    raise FileExistsError("Too many files with the same nested ZZX name")


def _source_segment(archive, segment_handle):
    segment_xml = None
    for element in archive.xml("Segments/content.xml").findall("TubeSegment"):
        if int(element.get("Handle")) == int(segment_handle):
            segment_xml = element
            break
    if segment_xml is None:
        raise FormatError(f"TubeSegment {segment_handle} not found")

    segment_records = _record_by_address(archive, "Segments")
    data_addr = int(segment_xml.get("DataAddr"))
    segment_record = segment_records.get(data_addr)
    if segment_record is None:
        raise FormatError(f"TubeSegment {segment_handle} DataAddr is missing")
    return segment_xml, segment_record


def _resolve_placements(placements):
    if not placements:
        raise ValueError("Locked rod has no pieces")

    archive_cache = {}
    parts_cache = {}
    resolved = []
    all_layers = set()

    for index, raw in enumerate(placements, 1):
        source_path = str(raw.get("filePath") or "").strip()
        if not source_path:
            raise ValueError(f"Piece {index} has no source ZZX path")
        source_file = Path(source_path)
        if not source_file.is_file():
            raise FileNotFoundError(f"Source ZZX not found: {source_path}")

        placement = raw.get("nestPlacement")
        if not isinstance(placement, dict):
            raise ValueError(
                f"Piece {index} has no geometry nesting placement. "
                "Unlock/recalculate/relock the rod before exporting."
            )
        for key in ("z_start", "z_end"):
            if placement.get(key) is None:
                raise ValueError(f"Piece {index} placement is missing {key}")

        if source_path not in archive_cache:
            archive = Archive.read(source_path)
            root = archive.xml("content.xml")
            if root.get("DocType") != "NestResults3D" or root.get("FileVer") != WRITABLE_FILE_VERSION:
                raise FormatError(
                    f"{source_file.name}: export supports writable FileVer "
                    f"{WRITABLE_FILE_VERSION} only"
                )
            archive.validate()
            archive_cache[source_path] = archive
            parts_cache[source_path] = read_tube_parts(source_path)

        archive = archive_cache[source_path]
        parts = parts_cache[source_path]
        requested_handle = raw.get("segmentHandle")
        if requested_handle is None:
            requested_handle = raw.get("segment_handle")

        if requested_handle is None:
            if len(parts) != 1:
                raise ValueError(
                    f"{source_file.name}: source contains {len(parts)} TubeSegments; "
                    "a specific segment handle is required"
                )
            part = parts[0]
        else:
            part = next(
                (candidate for candidate in parts if int(candidate.segment_handle) == int(requested_handle)),
                None,
            )
            if part is None:
                raise ValueError(
                    f"{source_file.name}: TubeSegment {requested_handle} was not found"
                )

        segment_xml, segment_record = _source_segment(archive, part.segment_handle)
        header = archive.xml("content.xml").find("Header")
        default_channel = int(header.get("DefaultChannel", "1")) if header is not None else 1
        all_layers.update(int(value) for value in (part.active_layers or []))

        resolved.append(
            _ResolvedPlacement(
                source_path=source_path,
                source_archive=archive,
                source_segment_xml=segment_xml,
                source_segment_record=segment_record,
                source_part=part,
                placement=placement,
                instance_key=str(raw.get("instanceKey") or f"piece-{index}"),
                source_file_name=str(raw.get("fileName") or source_file.name),
                default_channel=default_channel,
                marking_text=(
                    str(raw.get("markingText") or "").strip()
                    or None
                ),
            )
        )

    return resolved, all_layers


def _saved_stock_profile_signature(item):
    segment = item.source_segment_xml
    cross = segment.find("CrossSection")
    if cross is None or cross.get("DataAddr") is None:
        raise FormatError(
            f"{item.source_file_name}: TubeSegment has no saved CrossSection record"
        )
    records = _record_by_address(item.source_archive, "Curves")
    record = records.get(int(cross.get("DataAddr")))
    if record is None:
        raise FormatError(
            f"{item.source_file_name}: saved CrossSection record is missing"
        )
    return (
        str(cross.get("SectionClass") or ""),
        str(cross.get("ThickNess") or ""),
        str(record.name),
        tuple(
            (str(block.name), int(block.version), bytes(block.payload))
            for block in record.blocks
        ),
        bytes(record.tail),
    )


def _validate_saved_stock_profiles(resolved):
    first = resolved[0]
    reference = _saved_stock_profile_signature(first)
    for item in resolved[1:]:
        if _saved_stock_profile_signature(item) != reference:
            raise ValueError(
                "Mixed-segment TubePro export requires identical saved stock "
                "profiles for every piece, including section class, thickness, "
                "dimensions and corner radius. "
                f"{item.source_file_name} does not match {first.source_file_name}."
            )


def _choose_template(resolved, all_layers):
    required = set(all_layers)
    candidates = [
        item for item in resolved
        if required.issubset(set(int(value) for value in (item.source_part.active_layers or [])))
    ]
    if candidates:
        return candidates[0]

    layer_sets = sorted(
        {tuple(sorted(int(value) for value in item.source_part.active_layers or [])) for item in resolved}
    )
    raise FormatError(
        "No single source ZZX contains all operation layers required by this rod "
        f"({sorted(required)}; available sets: {layer_sets}). "
        "Layer/technology merging is intentionally not guessed."
    )


def _portion_template(archive):
    portions_xml = archive.xml("Portions/content.xml")
    doc = portions_xml.find("DocPortion")
    if doc is None:
        raise FormatError("Source ZZX has no DocPortion")
    records = _record_by_address(archive, "Portions")
    record = records.get(int(doc.get("DataAddr", "-1")))
    if record is None:
        raise FormatError("Source DocPortion has no binary record")
    return doc, record


def _pack_template(archive):
    doc = archive.xml("Portions/content.xml").find("DocPortion")
    pack = doc.find("PackSegments") if doc is not None else None
    if pack is None:
        raise FormatError("Source ZZX has no PackSegments")
    records = _record_by_address(archive, "Segments")
    record = records.get(int(pack.get("DataAddr", "-1")))
    if record is None:
        raise FormatError("Source PackSegments has no binary record")
    return pack, record


def _patch_portion_bounds(record, profile, used_span):
    block = next(
        (block for block in record.blocks if block.name == "DocPortion" and len(block.payload) >= 60),
        None,
    )
    if block is None:
        return False

    if profile.kind == "Circle":
        radius = float(profile.outside_diameter or profile.outside_width or 0.0) / 2.0
        hx = hy = radius
    else:
        hx = float(profile.outside_width or 0.0) / 2.0
        hy = float(profile.outside_height or profile.outside_width or 0.0) / 2.0

    used_span = float(used_span)
    if not math.isfinite(used_span) or used_span <= 0:
        raise ValueError("used_span must be finite and positive")

    payload = bytearray(block.payload)
    payload[4:32] = vector((-hx, -hy, 0.0))
    payload[32:60] = vector((hx, hy, used_span))
    block.payload = bytes(payload)
    return True


def _remap_viewport_handles(entries, reserved_handles):
    """Move VPort object handles outside generated segment/shape handle space.

    VPort handles are global object handles in native ZZX. Reusing one for a
    Shape makes TubePro resolve the wrong object type and can crash while
    opening the document.
    """
    name = "Viewports/content.xml"
    if name not in entries:
        return set()

    root = ET.fromstring(entries[name])
    used = set(int(value) for value in reserved_handles)
    remapped = set()
    next_handle = max(used, default=1000) + 1

    for viewport in root.findall(".//VPort"):
        while next_handle in used:
            next_handle += 1
        viewport.set("Handle", str(next_handle))
        used.add(next_handle)
        remapped.add(next_handle)
        next_handle += 1

    entries[name] = xml_bytes(root)
    return remapped


def _max_explicit_xml_handle(entries):
    maximum = 0
    for name, data in entries.items():
        if not name.endswith("content.xml"):
            continue
        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            continue
        for element in root.iter():
            value = element.get("Handle")
            if value is None:
                continue
            try:
                maximum = max(maximum, int(value))
            except (TypeError, ValueError):
                pass
    return maximum

def _update_metadata(entries, title):
    now = datetime.now(timezone.utc)
    if "content.xml" in entries:
        root = ET.fromstring(entries["content.xml"])
        metadata = root.find("MetaData")
        if metadata is not None:
            values = {
                "Title": title,
                "SaveApp": "ULTRA TubeNest",
                "SaveAppVer": "0.1.0",
                "SaveTime": now.strftime("%Y-%m-%d %H:%M:%S"),
            }
            for key, value in values.items():
                element = metadata.find(key)
                if element is None:
                    element = ET.SubElement(metadata, key)
                element.text = value
        entries["content.xml"] = xml_bytes(root)

    if "info.xml" in entries:
        root = ET.fromstring(entries["info.xml"])
        application = root.find("Application")
        if application is not None:
            application.set("AppName", "ULTRA TubeNest")
            application.set("AppVer", "0.1.0")
        saved = root.find("SavedBy")
        if saved is not None:
            saved.set("SaveTime", now.strftime("%Y-%m-%dT%H:%M:%SZ"))
        entries["info.xml"] = xml_bytes(root)


def _validate_locked_common_lines(resolved, gap_mm, tolerance=1e-4):
    for index in range(1, len(resolved)):
        current = resolved[index]
        if not bool(current.placement.get("common_line_before")):
            continue
        previous = resolved[index - 1]
        previous_pose = PartPose(
            axial_rotation_degrees=float(
                previous.placement.get("axial_rotation_degrees") or 0.0
            ),
            reversed_end_for_end=bool(
                previous.placement.get("reversed_end_for_end")
            ),
        )
        current_pose = PartPose(
            axial_rotation_degrees=float(
                current.placement.get("axial_rotation_degrees") or 0.0
            ),
            reversed_end_for_end=bool(
                current.placement.get("reversed_end_for_end")
            ),
        )
        previous_origin = float(previous.placement.get("z_start") or 0.0)
        expected = fit_adjacent_parts(
            previous.source_part.to_dict(),
            previous_pose,
            previous_origin,
            current.source_part.to_dict(),
            current_pose,
            gap_mm=float(gap_mm),
            allow_common_line=True,
        )
        actual_origin = float(current.placement.get("z_start") or 0.0)
        if not expected.common_line:
            raise ValueError(
                f"Locked shared boundary before {current.source_file_name} "
                "is no longer profile/boundary/process compatible. "
                "Recalculate and re-lock the rod before exporting."
            )
        if abs(float(expected.next_origin) - actual_origin) > tolerance:
            raise ValueError(
                f"Locked shared boundary before {current.source_file_name} "
                f"expects Z={expected.next_origin:.6f} but the saved placement "
                f"is Z={actual_origin:.6f}. Recalculate and re-lock the rod."
            )




def export_nested_rod(
    placements,
    output_path,
    *,
    rod_length=6000.0,
    gap_mm=2.0,
    title=None,
    text_marking_enabled=False,
    text_marking_height_mm=5.0,
    text_marking_font_path=None,
):
    """Export a locked ULTRA rod as one multi-segment ZZX."""
    rod_length = float(rod_length)
    if not math.isfinite(rod_length) or rod_length <= 0:
        raise ValueError("rod_length must be positive")

    resolved, all_layers = _resolve_placements(placements)
    _validate_saved_stock_profiles(resolved)
    template_item = _choose_template(resolved, all_layers)
    template = template_item.source_archive
    gap_mm = float(gap_mm)
    if not math.isfinite(gap_mm) or gap_mm < 0:
        raise ValueError("gap_mm must be finite and non-negative")

    _validate_locked_common_lines(resolved, gap_mm)

    text_marking_enabled = bool(text_marking_enabled)
    text_marking_height_mm = float(text_marking_height_mm)
    if text_marking_enabled:
        if text_marking_font_path is None:
            raise ValueError("Text marking font path is not configured")
        validate_romans_font(text_marking_font_path)
        if not (1.0 <= text_marking_height_mm <= 10.0):
            raise ValueError("Text marking height must be between 1 and 10 mm")

    used_span = max(float(item.placement.get("z_end") or 0.0) for item in resolved)
    if not math.isfinite(used_span) or used_span <= 0:
        raise ValueError("Locked rod has no valid used span")

    entries = copy.deepcopy(template.entries)
    _update_metadata(entries, title or Path(output_path).stem)

    segments_root = _empty_root_like(template.xml("Segments/content.xml"))
    shapes_root = _empty_root_like(template.xml("Shapes/content.xml"))
    curves_root = _empty_root_like(template.xml("Curves/content.xml"))
    lite_root = _empty_root_like(template.xml("LiteGeos/content.xml"))

    curves_stream = Stream()
    lite_stream = Stream()
    shapes_stream = Stream()
    segments_stream = Stream()

    _source_pack_xml, source_pack_record = _pack_template(template)
    pack_record = copy.deepcopy(source_pack_record)
    common_line_pack_mode = any(
        bool(item.placement.get("common_line_before"))
        or bool(item.placement.get("common_line_after"))
        for item in resolved
    )
    _set_pack_options(
        pack_record,
        gap_mm,
        common_line_pack_mode,
    )
    segments_stream.records.append(pack_record)

    source_doc_xml, source_portion_record = _portion_template(template)
    portion_record = copy.deepcopy(source_portion_record)
    _patch_portion_bounds(
        portion_record,
        template_item.source_part.profile,
        used_span,
    )
    portions_stream = Stream([portion_record])
    portions_root = _empty_root_like(template.xml("Portions/content.xml"))
    doc_xml = copy.deepcopy(source_doc_xml)
    doc_xml.attrib.pop("DataAddr", None)
    pack_xml = doc_xml.find("PackSegments")
    if pack_xml is None:
        raise FormatError("Source DocPortion lost PackSegments")
    pack_xml.attrib.pop("DataAddr", None)
    for child in list(pack_xml):
        pack_xml.remove(child)
    work_seq = ET.SubElement(pack_xml, "WorkSeq")
    portions_root.append(doc_xml)

    next_handle = 1001
    used_handles = set()
    segment_outputs = []
    warnings = []
    marking_reports = []
    release_start_reports = []
    master_shape_handles = {}

    def reserve_source_handle_block(source_segment, source_shape_refs):
        """Preserve the source's visible handle offsets within each segment.

        Real TubesT files reserve handles immediately after TubeSegment
        (commonly +1/+2) even though those objects are not exposed in XML.
        Collapsing those gaps caused cutoff handles such as 1004/1005 to
        become 1002/1003.
        """
        nonlocal next_handle
        old_segment_handle = int(source_segment.get("Handle"))
        old_shape_handles = [int(ref.get("Handle")) for ref in source_shape_refs]
        deltas = [handle - old_segment_handle for handle in old_shape_handles]
        if any(delta <= 0 for delta in deltas):
            raise FormatError("Unsupported source handle layout inside TubeSegment")

        base = max(1001, next_handle)
        while True:
            proposed = [base] + [base + delta for delta in deltas]
            if (
                not any(value in used_handles or value in (2, 3, 4, 7, 8) for value in proposed)
                and len(proposed) == len(set(proposed))
            ):
                break
            base += 1

        used_handles.update(proposed)
        next_handle = max(proposed) + 1
        return base, {
            old_handle: base + (old_handle - old_segment_handle)
            for old_handle in old_shape_handles
        }

    for ordinal, item in enumerate(resolved, 1):
        archive = item.source_archive
        source_segment = item.source_segment_xml
        source_part = item.source_part

        source_shape_refs = source_segment.find("Shapes")
        if source_shape_refs is None:
            raise FormatError(f"{item.source_file_name}: TubeSegment has no Shapes list")
        source_shape_ref_list = list(source_shape_refs)
        segment_handle, shape_handle_map = reserve_source_handle_block(
            source_segment,
            source_shape_ref_list,
        )

        segment_record = copy.deepcopy(item.source_segment_record)
        _set_object_handle(segment_record, segment_handle)
        _set_segment_transform(segment_record, source_part, item.placement)
        output_frame = _read_segment_frame(segment_record)
        segments_stream.records.append(segment_record)

        segment_xml = copy.deepcopy(source_segment)
        segment_xml.set("Handle", str(segment_handle))
        source_name = str(source_segment.get("Name") or "").strip()
        if not source_name:
            source_name = Path(item.source_file_name).stem
        safe_segment_name = re.sub(r"[\\/|<>]", "_", source_name).strip()
        segment_xml.set("Name", safe_segment_name[:64])
        segment_xml.attrib.pop("DataAddr", None)

        source_curve_records = _record_by_address(archive, "Curves")
        source_lite_records = _record_by_address(archive, "LiteGeos")
        source_shape_records = _record_by_address(archive, "Shapes")
        source_shape_xml = _xml_by_handle(archive, "Shapes")

        cross = segment_xml.find("CrossSection")
        if cross is None:
            raise FormatError(f"{item.source_file_name}: TubeSegment has no CrossSection")
        source_curve_addr = int(cross.get("DataAddr"))
        curve_record = copy.deepcopy(source_curve_records[source_curve_addr])
        curves_stream.records.append(curve_record)
        cross.attrib.pop("DataAddr", None)

        cross_geo = cross.find("Geometry")
        if cross_geo is None or cross_geo.get("GeoAddr") is None:
            raise FormatError(f"{item.source_file_name}: CrossSection has no geometry")
        source_cross_geo_addr = int(cross_geo.get("GeoAddr"))
        cross_geo_record = copy.deepcopy(source_lite_records[source_cross_geo_addr])
        lite_stream.records.append(cross_geo_record)
        cross_geo.attrib.pop("GeoAddr", None)

        output_shape_refs = segment_xml.find("Shapes")
        if output_shape_refs is None:
            raise FormatError(f"{item.source_file_name}: TubeSegment has no Shapes list")
        for child in list(output_shape_refs):
            output_shape_refs.remove(child)

        shape_record_map = {}
        shape_xml_map = {}
        output_record_by_handle = {}
        geo_clones = {}
        marking_pending = []

        for source_ref in source_shape_ref_list:
            old_handle = int(source_ref.get("Handle"))
            source_element = source_shape_xml.get(old_handle)
            if source_element is None:
                raise FormatError(
                    f"{item.source_file_name}: shape handle {old_handle} is missing"
                )
            old_record_addr = int(source_element.get("DataAddr"))
            source_record = source_shape_records.get(old_record_addr)
            if source_record is None:
                raise FormatError(
                    f"{item.source_file_name}: shape {old_handle} binary record is missing"
                )

            new_handle = shape_handle_map[old_handle]

            cloned_record = copy.deepcopy(source_record)
            _set_object_handle(cloned_record, new_handle)
            shapes_stream.records.append(cloned_record)
            shape_record_map[old_handle] = cloned_record
            output_record_by_handle[str(new_handle)] = cloned_record

            cloned_xml = copy.deepcopy(source_element)
            cloned_xml.set("Handle", str(new_handle))
            cloned_xml.attrib.pop("DataAddr", None)
            cloned_xml.attrib.pop("CopyHandle", None)

            geometry_source = _shape_geometry_source(source_shape_xml, source_element)
            geometry_children = [
                child for child in list(geometry_source)
                if child.get("GeoAddr") is not None
            ]
            master_key = (
                str(source_part.part_fingerprint),
                int(old_handle),
            )
            master_handle = master_shape_handles.get(master_key)
            copyable_machining_geometry = bool(geometry_children) and _shape_channel(source_record) > 0

            if copyable_machining_geometry and master_handle is not None:
                # Native TubesT duplication semantics: copied machining
                # curves carry their own Shape/Curve metadata record but the
                # XML points to the original shape through CopyHandle and
                # contains no duplicate Geometry tree.
                for child in list(cloned_xml):
                    cloned_xml.remove(child)
                cloned_xml.set("CopyHandle", str(master_handle))
            else:
                # First occurrence (or non-copyable XML shape): materialize
                # the real geometry, following any CopyHandle present in the
                # source document.
                for child in list(cloned_xml):
                    cloned_xml.remove(child)
                for source_child in list(geometry_source):
                    child = copy.deepcopy(source_child)
                    if child.get("GeoAddr") is not None:
                        old_geo_addr = int(child.get("GeoAddr"))
                        cloned_geo = geo_clones.get(old_geo_addr)
                        if cloned_geo is None:
                            source_geo = source_lite_records.get(old_geo_addr)
                            if source_geo is None:
                                raise FormatError(
                                    f"{item.source_file_name}: LiteGeos {old_geo_addr} is missing"
                                )
                            cloned_geo = copy.deepcopy(source_geo)
                            geo_clones[old_geo_addr] = cloned_geo
                            lite_stream.records.append(cloned_geo)
                        child.attrib.pop("GeoAddr", None)
                        child.set("_SourceGeoAddr", str(old_geo_addr))
                    cloned_xml.append(child)
                if copyable_machining_geometry:
                    master_shape_handles[master_key] = new_handle

            shapes_root.append(cloned_xml)
            shape_xml_map[old_handle] = cloned_xml

            cloned_ref = copy.deepcopy(source_ref)
            cloned_ref.set("Handle", str(new_handle))
            output_shape_refs.append(cloned_ref)

        old_cut_a = int(source_segment.get("CutOffA"))
        old_cut_b = int(source_segment.get("CutOffB"))
        if old_cut_a not in shape_handle_map or old_cut_b not in shape_handle_map:
            raise FormatError(f"{item.source_file_name}: end-cut handles are not in Shapes")
        segment_xml.set("CutOffA", str(shape_handle_map[old_cut_a]))
        segment_xml.set("CutOffB", str(shape_handle_map[old_cut_b]))

        reversed_end = bool(item.placement.get("reversed_end_for_end"))
        physical_near_old = old_cut_b if reversed_end else old_cut_a
        physical_far_old = old_cut_a if reversed_end else old_cut_b
        physical_near_new = shape_handle_map[physical_near_old]
        physical_far_new = shape_handle_map[physical_far_old]

        # Native segment geometry may use an arbitrary local axial origin.
        # Convert the posed stock interval to the nonnegative convention used
        # by the release-start selector without modifying serialized geometry.
        posed_stock_endpoints = (
            _frame_point(
                output_frame,
                (0.0, 0.0, float(source_part.axial_min)),
            ),
            _frame_point(
                output_frame,
                (0.0, 0.0, float(source_part.axial_max)),
            ),
        )
        positioned_stock_min_z = min(
            point[2] for point in posed_stock_endpoints
        )

        # Recompute PathStartParam in the segment's final posed frame while
        # preserving the original local geometry and native child domains.
        for old_handle in (physical_near_old, physical_far_old):
            source_element = source_shape_xml[old_handle]
            raw_curves = _source_shape_curves(
                source_element,
                source_shape_xml,
                source_lite_records,
            )
            posed_curves = [
                _posed_curve(curve, output_frame)
                for curve in raw_curves
            ]
            selection_curves = _normalize_release_selection_z(
                posed_curves,
                positioned_stock_min_z,
            )
            selected = _select_release_start(
                selection_curves,
                source_part.profile,
            )
            before = _write_release_start(
                shape_record_map[old_handle],
                selected["parameter"],
            )
            release_start_reports.append(
                {
                    "instanceKey": item.instance_key,
                    "fileName": item.source_file_name,
                    "segmentHandle": segment_handle,
                    "shapeHandle": shape_handle_map[old_handle],
                    "oldParameter": before,
                    "parameter": float(selected["parameter"]),
                    "point": [float(v) for v in selected["point"]],
                    "minimumZ": float(selected["minimum_z"]),
                    "positionedStockMinZBeforeNormalization": float(
                        positioned_stock_min_z
                    ),
                    "profile": selected["profile"],
                    "rule": selected["rule"],
                }
            )

        if text_marking_enabled:
            if not item.marking_text:
                warnings.append(
                    f"{item.source_file_name}: TEXT marking omitted because "
                    "label metadata is incomplete."
                )
                marking_reports.append(
                    {
                        "status": "skipped",
                        "instanceKey": item.instance_key,
                        "fileName": item.source_file_name,
                        "reason": "missing marking metadata",
                    }
                )
            else:
                near_points = _posed_shape_points(
                    source_shape_xml[physical_near_old],
                    source_shape_xml,
                    source_lite_records,
                    output_frame,
                )
                far_points = _posed_shape_points(
                    source_shape_xml[physical_far_old],
                    source_shape_xml,
                    source_lite_records,
                    output_frame,
                )
                near_bounds = _xyz_bounds(near_points)
                far_bounds = _xyz_bounds(far_points)
                if near_bounds is None or far_bounds is None:
                    raise FormatError(
                        f"{item.source_file_name}: cannot determine end-cut "
                        "bounds for text marking"
                    )
                preferred_start_z = (
                    float(near_bounds[1][2]) + 5.0
                )
                marking_max_z = float(far_bounds[0][2])

                obstacles = []
                excluded_old = {physical_near_old, physical_far_old}
                for source_ref in source_shape_ref_list:
                    old_handle = int(source_ref.get("Handle"))
                    if old_handle in excluded_old:
                        continue
                    record = source_shape_records.get(
                        int(source_shape_xml[old_handle].get("DataAddr"))
                    )
                    if (
                        record is None
                        or _shape_channel(record) <= 0
                        or _shape_do_not_cut(record)
                    ):
                        continue
                    bounds = _xyz_bounds(
                        _posed_shape_points(
                            source_shape_xml[old_handle],
                            source_shape_xml,
                            source_lite_records,
                            output_frame,
                        )
                    )
                    if bounds is not None:
                        obstacles.append(
                            {
                                "handle": str(shape_handle_map[old_handle]),
                                "bounds": bounds,
                            }
                        )

                addition = None
                fit_errors = []
                collision_attempts = []
                layouts = marking_layout_candidates(item.marking_text)
                selected_layout_index = None
                selected_position = None
                selected_start_z = None
                profile = source_part.profile

                for layout_index, layout_lines in enumerate(layouts):
                    prepared_layout = layout_text_strokes(
                        text_marking_font_path,
                        layout_lines,
                        text_marking_height_mm,
                    )
                    axial_width = float(
                        prepared_layout[1].get(
                            "visible_axial_width_mm",
                            0.0,
                        )
                    )
                    starts = _candidate_marking_start_positions(
                        preferred_start_z,
                        marking_max_z,
                        axial_width,
                        obstacles,
                    )
                    if not starts:
                        fit_errors.append(
                            f"layout {layout_index + 1}: axial width "
                            f"{axial_width:.3f} mm does not fit"
                        )
                        continue

                    positions = (
                        _flat_marking_faces()
                        if profile.kind in ("Square", "Rect")
                        else _round_marking_angles()
                        if profile.kind == "Circle"
                        else ()
                    )
                    if not positions:
                        raise ValueError(
                            f"TEXT marking is not supported for "
                            f"profile {profile.kind!r}"
                        )

                    for start_z in starts:
                        for position in positions:
                            try:
                                if profile.kind in ("Square", "Rect"):
                                    candidate = build_marking_records(
                                        source_shape_record=shape_record_map[
                                            physical_near_old
                                        ],
                                        font_path=text_marking_font_path,
                                        lines=layout_lines,
                                        height_mm=text_marking_height_mm,
                                        start_z=start_z,
                                        outside_width=float(
                                            profile.outside_width or 0.0
                                        ),
                                        outside_height=float(
                                            profile.outside_height or 0.0
                                        ),
                                        corner_radius=float(
                                            profile.corner_radius or 0.0
                                        ),
                                        max_z=marking_max_z,
                                        first_handle=next_handle,
                                        face=position,
                                        prepared_layout=prepared_layout,
                                    )
                                else:
                                    candidate = build_round_marking_records(
                                        source_shape_record=shape_record_map[
                                            physical_near_old
                                        ],
                                        font_path=text_marking_font_path,
                                        lines=layout_lines,
                                        height_mm=text_marking_height_mm,
                                        start_z=start_z,
                                        radius=float(
                                            profile.outside_diameter or 0.0
                                        ) / 2.0,
                                        max_z=marking_max_z,
                                        first_handle=next_handle,
                                        circumferential_center_deg=position,
                                        prepared_layout=prepared_layout,
                                    )
                            except MarkingFitError as exc:
                                fit_errors.append(
                                    f"layout {layout_index + 1}, "
                                    f"position {position}, Z {start_z:.3f}: {exc}"
                                )
                                continue

                            collisions = [
                                obstacle["handle"]
                                for obstacle in obstacles
                                if _bounds_overlap(
                                    candidate["report"][
                                        "geometry_3d_bounds"
                                    ],
                                    obstacle["bounds"],
                                    clearance=MARKING_COLLISION_CLEARANCE_MM,
                                )
                            ]
                            if collisions:
                                collision_attempts.append(
                                    {
                                        "layout": layout_index + 1,
                                        "position": position,
                                        "startZ": start_z,
                                        "shapeHandles": collisions,
                                    }
                                )
                                continue

                            addition = candidate
                            selected_layout_index = layout_index
                            selected_position = position
                            selected_start_z = start_z
                            break
                        if addition is not None:
                            break
                    if addition is not None:
                        break

                if addition is None:
                    reason = (
                        fit_errors[-1]
                        if fit_errors
                        else (
                            "all otherwise-valid placements collide with "
                            "existing machining geometry"
                            if collision_attempts
                            else "no safe marking layout was available"
                        )
                    )
                    warnings.append(
                        f"{item.source_file_name}: TEXT marking omitted after "
                        f"scanning available surfaces/positions. {reason}"
                    )
                    marking_reports.append(
                        {
                            "status": "skipped",
                            "instanceKey": item.instance_key,
                            "fileName": item.source_file_name,
                            "originalText": item.marking_text,
                            "attemptedLayouts": layouts,
                            "fitErrors": fit_errors,
                            "collisionAttempts": collision_attempts,
                        }
                    )
                else:
                    next_marking_handle = int(addition["next_handle"])
                    for handle in range(next_handle, next_marking_handle):
                        used_handles.add(handle)
                    next_handle = max(next_handle, next_marking_handle)

                    for (
                        shape_record,
                        geometry_record,
                        (shape_element, geometry_element),
                        segment_ref,
                    ) in zip(
                        addition["shape_records"],
                        addition["geometry_records"],
                        addition["shape_elements"],
                        addition["segment_refs"],
                    ):
                        _inverse_transform_marking_shape(
                            shape_record,
                            output_frame,
                        )
                        _inverse_transform_marking_geometry(
                            geometry_record,
                            output_frame,
                        )
                        shapes_stream.records.append(shape_record)
                        lite_stream.records.append(geometry_record)
                        shapes_root.append(shape_element)
                        output_shape_refs.append(segment_ref)
                        output_record_by_handle[
                            str(shape_element.get("Handle"))
                        ] = shape_record
                        marking_pending.append(
                            (
                                shape_element,
                                geometry_element,
                                shape_record,
                                geometry_record,
                            )
                        )

                    report = dict(addition["report"])
                    report.update(
                        {
                            "status": "generated",
                            "instanceKey": item.instance_key,
                            "fileName": item.source_file_name,
                            "originalText": item.marking_text,
                            "layoutAttempt": selected_layout_index + 1,
                            "fallbackUsed": bool(selected_layout_index),
                            "selectedSurfacePosition": selected_position,
                            "selectedStartZ": selected_start_z,
                            "preferredStartZ": preferred_start_z,
                            "shiftedAxially": (
                                abs(selected_start_z - preferred_start_z)
                                > 1e-6
                            ),
                            "collisionAttemptsBeforeSuccess": (
                                collision_attempts
                            ),
                            "fitErrorsBeforeSuccess": fit_errors,
                            "segmentHandle": segment_handle,
                        }
                    )
                    marking_reports.append(report)

        for side, enabled in (
            ("before", bool(item.placement.get("common_line_before"))),
            ("after", bool(item.placement.get("common_line_after"))),
        ):
            if not enabled:
                continue
            if side == "before":
                end_index = 1 if reversed_end else 0
            else:
                end_index = 0 if reversed_end else 1
            end = source_part.ends[end_index]
            old_end_handle = int(end.shape_handle)
            if int(end.operation_layer or 0) != int(item.default_channel):
                warnings.append(
                    f"{item.source_file_name}: common-line {side} kept as "
                    f"ordinary source operation because layer {end.operation_layer} "
                    f"is not default cutting layer {item.default_channel}."
                )
            elif not _set_common_line_flag(shape_record_map[old_end_handle]):
                warnings.append(
                    f"{item.source_file_name}: common-line {side} flag could not "
                    "be written; source cut record was preserved."
                )

        _order_segment_refs_release_safe(
            segment_xml,
            output_record_by_handle,
            near_handle=physical_near_new,
            far_handle=physical_far_new,
        )

        segments_root.append(segment_xml)
        ET.SubElement(work_seq, "Seg", Handle=str(segment_handle))
        ET.SubElement(pack_xml, "TubeSegment", Handle=str(segment_handle))

        segment_outputs.append({
            "segment_xml": segment_xml,
            "segment_record": segment_record,
            "curve_record": curve_record,
            "cross_geo_xml": cross_geo,
            "cross_geo_record": cross_geo_record,
            "shape_record_map": shape_record_map,
            "shape_xml_map": shape_xml_map,
            "output_record_by_handle": output_record_by_handle,
            "part_fingerprint": str(source_part.part_fingerprint),
            "geo_clones": geo_clones,
            "marking_pending": marking_pending,
        })

    # TubePro mixed-segment import requires every later machining Shape
    # to resolve geometry through a Shape owned by the first TubeSegment.
    # Repeated geometry may point at an existing first-segment active Shape;
    # new geometry gets a channel-0 / Curve-flags-0 inert catalog master.
    first_output = segment_outputs[0]
    first_segment_xml = first_output["segment_xml"]
    first_shapes_parent = first_segment_xml.find("Shapes")
    if first_shapes_parent is None:
        raise FormatError("First TubeSegment has no Shapes list")

    first_min_z = float(resolved[0].source_part.axial_min)
    first_max_z = float(resolved[0].source_part.axial_max)
    if first_min_z > first_max_z:
        first_min_z, first_max_z = first_max_z, first_min_z

    global_shape_xml = {
        str(element.get("Handle")): element
        for element in shapes_root
        if element.tag != "MD5" and element.get("Handle") is not None
    }
    global_shape_records = {}
    geometry_pairs_by_handle = {}

    for output in segment_outputs:
        global_shape_records.update(output["output_record_by_handle"])
        for _old_handle, element in output["shape_xml_map"].items():
            handle = str(element.get("Handle"))
            pairs = []
            for child in list(element):
                source_addr = child.get("_SourceGeoAddr")
                if source_addr is None:
                    continue
                geometry_record = output["geo_clones"].get(int(source_addr))
                if geometry_record is None:
                    raise FormatError(
                        f"Missing cloned geometry {source_addr} for Shape {handle}"
                    )
                pairs.append((child, geometry_record))
            if pairs:
                geometry_pairs_by_handle[handle] = pairs

        for (
            shape_element,
            geometry_element,
            shape_record,
            geometry_record,
        ) in output["marking_pending"]:
            handle = str(shape_element.get("Handle"))
            global_shape_xml[handle] = shape_element
            global_shape_records[handle] = shape_record
            geometry_pairs_by_handle[handle] = [
                (geometry_element, geometry_record)
            ]

    def resolve_geometry_pairs(handle):
        handle = str(handle)
        seen = set()
        while True:
            if handle in seen:
                raise FormatError("Cyclic output Shape CopyHandle chain")
            seen.add(handle)
            pairs = geometry_pairs_by_handle.get(handle)
            if pairs:
                return pairs
            element = global_shape_xml.get(handle)
            if element is None:
                raise FormatError(f"Missing output Shape {handle}")
            copied = element.get("CopyHandle")
            if copied is None:
                raise FormatError(
                    f"Machining Shape {handle} has neither geometry nor CopyHandle"
                )
            handle = str(copied)

    def reserve_catalog_handle():
        nonlocal next_handle
        while next_handle in used_handles or next_handle in (2, 3, 4, 7, 8):
            next_handle += 1
        handle = int(next_handle)
        used_handles.add(handle)
        next_handle += 1
        return handle

    first_handles = {
        str(ref.get("Handle"))
        for ref in first_shapes_parent
        if ref.get("Handle") is not None
    }
    first_part_fingerprint = first_output["part_fingerprint"]
    first_active_by_signature = {}
    inert_master_by_signature = {}
    for handle in list(first_handles):
        element = global_shape_xml.get(handle)
        record = global_shape_records.get(handle)
        if element is None or record is None:
            continue
        try:
            pairs = resolve_geometry_pairs(handle)
        except FormatError:
            continue
        signature = _geometry_catalog_signature(element, pairs)
        if _shape_channel(record) > 0:
            first_active_by_signature.setdefault(signature, handle)
        elif _shape_channel(record) == 0 and _curve_flags(record) == 0:
            inert_master_by_signature.setdefault(signature, handle)

    catalog_pending = []
    catalog_created = []
    catalog_reused = []

    for output in segment_outputs[1:]:
        segment_xml = output["segment_xml"]
        refs_parent = segment_xml.find("Shapes")
        if refs_parent is None:
            raise FormatError("Later TubeSegment has no Shapes list")

        for ref in list(refs_parent):
            handle = str(ref.get("Handle"))
            record = global_shape_records.get(handle)
            element = global_shape_xml.get(handle)
            if record is None or element is None:
                raise FormatError(f"Missing output Shape {handle}")
            if _shape_channel(record) <= 0:
                continue

            pairs = resolve_geometry_pairs(handle)
            signature = _geometry_catalog_signature(element, pairs)
            same_physical_part = (
                output["part_fingerprint"] == first_part_fingerprint
            )
            master_handle = (
                first_active_by_signature.get(signature)
                if same_physical_part
                else None
            )
            if master_handle is None:
                master_handle = inert_master_by_signature.get(signature)

            if master_handle is None:
                geometry_min_z, geometry_max_z = _geometry_z_bounds(pairs)
                tolerance = 0.01
                if (
                    geometry_min_z < first_min_z - tolerance
                    or geometry_max_z > first_max_z + tolerance
                ):
                    raise ValueError(
                        "TubePro mixed-segment geometry catalog cannot safely "
                        f"host Shape {handle} from segment {segment_xml.get('Handle')}: "
                        f"its local Z range {geometry_min_z:.3f}..{geometry_max_z:.3f} mm "
                        f"does not fit inside the first piece {first_min_z:.3f}.."
                        f"{first_max_z:.3f} mm. Reorder the rod so the first piece "
                        "can contain every later geometry master."
                    )

                master_handle_int = reserve_catalog_handle()
                master_handle = str(master_handle_int)
                master_record = _make_inert_geometry_master(
                    record,
                    master_handle_int,
                )
                shapes_stream.records.append(master_record)

                master_element = copy.deepcopy(element)
                master_element.set("Handle", master_handle)
                master_element.attrib.pop("DataAddr", None)
                master_element.attrib.pop("CopyHandle", None)
                for child in list(master_element):
                    if child.tag in ("Geometry", "InnerGeometry"):
                        master_element.remove(child)

                master_pairs = []
                for source_child, geometry_record in pairs:
                    child = copy.deepcopy(source_child)
                    master_element.append(child)
                    master_pairs.append((child, geometry_record))

                shapes_root.append(master_element)
                ET.SubElement(
                    first_shapes_parent,
                    ref.tag,
                    Handle=master_handle,
                )

                global_shape_xml[master_handle] = master_element
                global_shape_records[master_handle] = master_record
                geometry_pairs_by_handle[master_handle] = master_pairs
                first_handles.add(master_handle)
                inert_master_by_signature[signature] = master_handle
                catalog_pending.append(
                    (
                        master_element,
                        master_record,
                        master_pairs,
                    )
                )
                catalog_created.append(master_handle)
            else:
                catalog_reused.append(master_handle)

            element.set("CopyHandle", str(master_handle))
            for child in list(element):
                if child.tag in ("Geometry", "InnerGeometry"):
                    element.remove(child)

    # Every CopyHandle in the generated document must resolve to the first
    # physical segment, matching the TubePro-confirmed mixed-segment probe.
    for handle, element in global_shape_xml.items():
        copied = element.get("CopyHandle")
        if copied is not None and str(copied) not in first_handles:
            raise FormatError(
                f"Shape {handle} CopyHandle {copied} does not resolve to "
                "the first TubeSegment"
            )

    curves_data = curves_stream.encode()
    lite_data = lite_stream.encode()
    shapes_data = shapes_stream.encode()
    segments_data = segments_stream.encode()
    portions_data = portions_stream.encode()

    for output in segment_outputs:
        segment_xml = output["segment_xml"]
        segment_xml.set("DataAddr", str(output["segment_record"].address))
        cross = segment_xml.find("CrossSection")
        cross.set("DataAddr", str(output["curve_record"].address))
        output["cross_geo_xml"].set("GeoAddr", str(output["cross_geo_record"].address))

        for old_handle, shape_xml in output["shape_xml_map"].items():
            shape_xml.set("DataAddr", str(output["shape_record_map"][old_handle].address))
            for child in list(shape_xml):
                source_addr = child.attrib.pop("_SourceGeoAddr", None)
                if source_addr is not None:
                    child.set(
                        "GeoAddr",
                        str(output["geo_clones"][int(source_addr)].address),
                    )

        for (
            shape_element,
            geometry_element,
            shape_record,
            geometry_record,
        ) in output["marking_pending"]:
            shape_element.set("DataAddr", str(shape_record.address))
            geometry_element.set("GeoAddr", str(geometry_record.address))

    for master_element, master_record, master_pairs in catalog_pending:
        master_element.set("DataAddr", str(master_record.address))
        for child, geometry_record in master_pairs:
            child.attrib.pop("_SourceGeoAddr", None)
            child.set("GeoAddr", str(geometry_record.address))

    doc_xml.set("DataAddr", str(portion_record.address))
    pack_xml.set("DataAddr", str(pack_record.address))

    entries["Segments/data.bin"] = segments_data
    entries["Segments/content.xml"] = xml_bytes(segments_root)
    entries["Curves/data.bin"] = curves_data
    entries["Curves/content.xml"] = xml_bytes(curves_root)
    entries["LiteGeos/data.bin"] = lite_data
    entries["LiteGeos/content.xml"] = xml_bytes(lite_root)
    entries["Shapes/data.bin"] = shapes_data
    entries["Shapes/content.xml"] = xml_bytes(shapes_root)
    entries["Portions/data.bin"] = portions_data
    entries["Portions/content.xml"] = xml_bytes(portions_root)

    # The TubePro-confirmed mixed-segment probe stores the next free handle.
    root_content = ET.fromstring(entries["content.xml"])
    header = root_content.find("Header")
    if header is not None:
        header.set(
            "HandleSeed",
            str(_max_explicit_xml_handle(entries) + 1),
        )
    entries["content.xml"] = xml_bytes(root_content)

    archive = Archive(entries)
    archive.refresh_checksums()
    checks = archive.validate()
    archive.write(output_path)

    return {
        "path": str(Path(output_path)),
        "pieceCount": len(resolved),
        "segmentCount": len(resolved),
        "warnings": warnings,
        "validation": checks,
        "textMarkings": marking_reports,
        "textMarkingEnabled": text_marking_enabled,
        "releaseStarts": release_start_reports,
        "commonLinePackMode": bool(common_line_pack_mode),
        "geometryCatalog": {
            "createdInertMasterHandles": catalog_created,
            "reusedFirstSegmentHandles": catalog_reused,
            "firstSegmentHandle": first_segment_xml.get("Handle"),
            "copyHandlesRestrictedToFirstSegment": True,
        },
        "experimentalHolderBehavior": True,
        "fileVersion": WRITABLE_FILE_VERSION,
        "exportMode": "native_multi_segment_array_catalog",
    }


def export_nested_rod_to_directory(
    placements,
    output_dir,
    *,
    tube_type="Tube",
    rod_id="rod",
    rod_length=6000.0,
    gap_mm=2.0,
    text_marking_enabled=False,
    text_marking_height_mm=5.0,
    text_marking_font_path=None,
):
    if not str(output_dir or "").strip():
        raise ValueError("Nested ZZX output directory is not configured")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suggested = f"Nested_{tube_type}_{rod_id}_{timestamp}.zzx"
    output_path = _unique_output_path(output_dir, suggested)
    return export_nested_rod(
        placements,
        output_path,
        rod_length=rod_length,
        gap_mm=gap_mm,
        title=output_path.stem,
        text_marking_enabled=text_marking_enabled,
        text_marking_height_mm=text_marking_height_mm,
        text_marking_font_path=text_marking_font_path,
    )

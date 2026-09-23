"""TubePro-compatible locked-rod ZZX export.

The verified representation is one TubeSegment per stock bar. Every source
part's 3D machining/display geometry is transformed explicitly into its final
nested position, then all shapes are attached to the first TubeSegment.

This intentionally avoids TubePro's subsequent-segment CopyHandle loader.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from pathlib import Path
import re
import struct
import xml.etree.ElementTree as ET

from .archive import Archive, xml_bytes
from .bcmp import FormatError, read_vector, vector
from .domain import read_tube_parts
from .geometry import Line, Polyline, Spline, primitives
from .zzx_merge import nest_singletons
from .cut_release import repair_single_segment_cut_release
from .text_marking import (
    MarkingFitError,
    build_marking_records,
    build_round_marking_records,
    layout_text_strokes,
    marking_layout_candidates,
    validate_romans_font,
)


WRITABLE_FILE_VERSION = "65542"


@dataclass
class _InputPart:
    archive: Archive
    part: object
    placement: dict
    source_path: str
    file_name: str
    instance_key: str
    marking_text: str | None = None


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


def _update_metadata(entries, title):
    now = datetime.now(timezone.utc)
    root = ET.fromstring(entries["content.xml"])
    metadata = root.find("MetaData")
    if metadata is not None:
        values = {
            "Title": title,
            "SaveApp": "ULTRA TubeNest",
            "SaveAppVer": "0.2.0",
            "SaveTime": now.strftime("%Y-%m-%d %H:%M:%S"),
        }
        for key, value in values.items():
            element = metadata.find(key)
            if element is None:
                element = ET.SubElement(metadata, key)
            element.text = value
    entries["content.xml"] = xml_bytes(root)

    if "info.xml" in entries:
        info = ET.fromstring(entries["info.xml"])
        app = info.find("Application")
        if app is not None:
            app.set("AppName", "ULTRA TubeNest")
            app.set("AppVer", "0.2.0")
        saved = info.find("SavedBy")
        if saved is not None:
            saved.set("SaveTime", now.strftime("%Y-%m-%dT%H:%M:%SZ"))
        entries["info.xml"] = xml_bytes(info)


def _segment_record(archive):
    segment_xml = archive.xml("Segments/content.xml").find("TubeSegment")
    if segment_xml is None:
        raise FormatError("Source ZZX has no TubeSegment")
    records = {record.address: record for record in archive.stream("Segments").records}
    record = records.get(int(segment_xml.get("DataAddr")))
    if record is None:
        raise FormatError("Source TubeSegment binary record is missing")
    return segment_xml, record


def _identity_segment_frame(record, tolerance=1e-7):
    block = next(
        (block for block in record.blocks if block.name == "TubeSegment"),
        None,
    )
    if block is None or len(block.payload) < 120:
        raise FormatError("Unsupported TubeSegment transform payload")
    expected = (
        ((1.0, 0.0, 0.0), 8),
        ((0.0, 1.0, 0.0), 36),
        ((0.0, 0.0, 1.0), 64),
        ((0.0, 0.0, 0.0), 92),
    )
    for target, offset in expected:
        actual = read_vector(block.payload, offset)
        if any(abs(a - b) > tolerance for a, b in zip(actual, target)):
            return False
    return True


def _resolve_inputs(placements):
    if not placements:
        raise ValueError("Locked rod has no pieces")

    cache = {}
    resolved = []
    all_layers = set()

    for index, raw in enumerate(placements, 1):
        source_path = str(raw.get("filePath") or "").strip()
        if not source_path:
            raise ValueError(f"Piece {index} has no source ZZX path")
        path = Path(source_path)
        if not path.is_file():
            raise FileNotFoundError(f"Source ZZX not found: {source_path}")

        placement = raw.get("nestPlacement")
        if not isinstance(placement, dict):
            raise ValueError(
                f"Piece {index} has no saved nesting placement; recalculate and relock it"
            )
        if placement.get("z_start") is None or placement.get("z_end") is None:
            raise ValueError(f"Piece {index} placement has no axial bounds")

        if source_path not in cache:
            archive = Archive.read(source_path)
            root = archive.xml("content.xml")
            if (
                root.get("DocType") != "NestResults3D"
                or root.get("FileVer") != WRITABLE_FILE_VERSION
            ):
                raise FormatError(
                    f"{path.name}: flat export supports FileVer {WRITABLE_FILE_VERSION} only"
                )
            archive.validate()
            segment_xmls = archive.xml("Segments/content.xml").findall("TubeSegment")
            if len(segment_xmls) != 1:
                raise FormatError(
                    f"{path.name}: flat export currently requires a singleton source ZZX "
                    f"(found {len(segment_xmls)} TubeSegments)"
                )
            parts = read_tube_parts(source_path)
            if len(parts) != 1:
                raise FormatError(
                    f"{path.name}: expected one normalized TubePart, found {len(parts)}"
                )
            _, segment_record = _segment_record(archive)
            if not _identity_segment_frame(segment_record):
                raise FormatError(
                    f"{path.name}: non-identity source TubeSegment transforms are not "
                    "yet supported by the verified flat exporter"
                )
            cache[source_path] = (archive, parts[0])

        archive, part = cache[source_path]
        requested_handle = raw.get("segmentHandle")
        if requested_handle is not None and int(requested_handle) != int(part.segment_handle):
            raise ValueError(
                f"{path.name}: requested TubeSegment {requested_handle}, "
                f"but singleton source contains {part.segment_handle}"
            )

        all_layers.update(int(value) for value in (part.active_layers or []))
        resolved.append(
            _InputPart(
                archive=archive,
                part=part,
                placement=placement,
                source_path=source_path,
                file_name=str(raw.get("fileName") or path.name),
                instance_key=str(raw.get("instanceKey") or f"piece-{index}"),
                marking_text=(
                    str(raw.get("markingText") or "").strip()
                    or None
                ),
            )
        )

    return resolved, all_layers


def _choose_template(resolved, all_layers):
    required = set(all_layers)
    for item in resolved:
        if required.issubset(set(int(v) for v in (item.part.active_layers or []))):
            return item.archive
    # Do not invent/merge unknown technical layer definitions.
    raise FormatError(
        "No source ZZX on this rod contains every operation layer required by "
        f"the combined stock ({sorted(required)})"
    )


def _rotate_xy(x, y, degrees):
    angle = math.radians(float(degrees))
    co, si = math.cos(angle), math.sin(angle)
    return co * x - si * y, si * x + co * y


def _transform_point(point, item, base_rotation, rod_shift):
    x, y, z = map(float, point)
    part = item.part
    placement = item.placement
    local_z = z - float(part.axial_min)

    if bool(placement.get("reversed_end_for_end")):
        x = -x
        local_z = float(part.overall_length) - local_z

    relative_rotation = (
        float(placement.get("axial_rotation_degrees") or 0.0)
        - float(base_rotation)
    )
    x, y = _rotate_xy(x, y, relative_rotation)
    z = (
        float(placement.get("z_start") or 0.0)
        + float(rod_shift)
        + local_z
    )
    return x, y, z


def _transform_vector3(direction, item, base_rotation):
    x, y, z = map(float, direction)
    if bool(item.placement.get("reversed_end_for_end")):
        x = -x
        z = -z
    relative_rotation = (
        float(item.placement.get("axial_rotation_degrees") or 0.0)
        - float(base_rotation)
    )
    x, y = _rotate_xy(x, y, relative_rotation)
    return x, y, z


def _transform_geometry_record(record, item, base_rotation, rod_shift):
    for block in record.blocks:
        if not block.payload:
            continue

        if block.name == "Spline3D":
            curve = Spline.decode(block.payload)
            curve.points = [
                _transform_point(point, item, base_rotation, rod_shift)
                for point in curve.points
            ]
            block.payload = curve.payload()

        elif block.name == "Line3D":
            if len(block.payload) != 56:
                raise FormatError("Unexpected Line3D payload size")
            start = _transform_point(
                read_vector(block.payload, 0),
                item,
                base_rotation,
                rod_shift,
            )
            direction = _transform_vector3(
                read_vector(block.payload, 28),
                item,
                base_rotation,
            )
            block.payload = vector(start) + vector(direction)

        elif block.name == "Polyline3D":
            if len(block.payload) < 4:
                raise FormatError("Truncated Polyline3D")
            count = struct.unpack_from("<I", block.payload, 0)[0]
            if len(block.payload) != 4 + count * 28:
                raise FormatError("Unexpected Polyline3D payload size")
            points = [
                _transform_point(
                    read_vector(block.payload, 4 + index * 28),
                    item,
                    base_rotation,
                    rod_shift,
                )
                for index in range(count)
            ]
            block.payload = struct.pack("<I", count) + b"".join(
                vector(point) for point in points
            )


def _shape_channel(shape_record):
    block = next(
        (
            block for block in shape_record.blocks
            if block.name == "Shape" and len(block.payload) >= 4
        ),
        None,
    )
    if block is None:
        return 0
    return int(struct.unpack_from("<I", block.payload, 0)[0])


def _shape_do_not_cut(shape_record):
    block = next(
        (
            block for block in shape_record.blocks
            if block.name == "Shape" and len(block.payload) >= 12
        ),
        None,
    )
    if block is None:
        return False
    return bool(int(struct.unpack_from("<I", block.payload, 8)[0]) & 0x2)


def _shape_axial_center(shape_xml, lite_by_addr):
    points = []
    for child in shape_xml:
        if child.get("GeoAddr") is None:
            continue
        record = lite_by_addr.get(int(child.get("GeoAddr")))
        if record is None:
            continue
        for curve in primitives(record):
            points.extend(curve.sample(32))

    if shape_xml.tag == "Line":
        start = shape_xml.find("LineStart")
        direction = shape_xml.find("LineVector")
        if start is not None:
            p = tuple(float(start.get(axis, "0")) for axis in "XYZ")
            points.append(p)
            if direction is not None:
                d = tuple(float(direction.get(axis, "0")) for axis in "XYZ")
                points.append(tuple(a + b for a, b in zip(p, d)))

    z_values = [float(point[2]) for point in points if len(point) >= 3]
    if not z_values:
        return float("inf")
    return (min(z_values) + max(z_values)) / 2.0


def _order_shape_references_left_to_right(
    shape_refs,
    shapes_by_handle,
    shape_record_by_addr,
    lite_by_addr,
):
    decorated = []
    for original_index, ref in enumerate(list(shape_refs)):
        shape_xml = shapes_by_handle.get(ref.get("Handle"))
        if shape_xml is None:
            continue
        record = shape_record_by_addr.get(int(shape_xml.get("DataAddr")))
        if record is None:
            continue
        channel = _shape_channel(record)
        if channel > 0:
            key = (
                0,
                round(_shape_axial_center(shape_xml, lite_by_addr), 6),
                1 if _shape_do_not_cut(record) else 0,
                original_index,
            )
        else:
            # Display/silhouette geometry is not a machining operation.
            key = (1, float("inf"), 0, original_index)
        decorated.append((key, ref))

    decorated.sort(key=lambda row: row[0])
    return [ref for _key, ref in decorated]


def _transform_shape_record(record, item, base_rotation):
    # Curve normals are directions: rotate/reverse them, never translate them.
    curve_block = next(
        (
            block
            for block in record.blocks
            if block.name == "Curve" and len(block.payload) >= 44
        ),
        None,
    )
    if curve_block is None:
        return
    try:
        normal = read_vector(curve_block.payload, 16)
    except FormatError:
        return
    payload = bytearray(curve_block.payload)
    payload[16:44] = vector(_transform_vector3(normal, item, base_rotation))
    curve_block.payload = bytes(payload)


def _transform_xml_line(element, item, base_rotation, rod_shift):
    start = element.find("LineStart")
    direction = element.find("LineVector")
    if start is None or direction is None:
        return

    p = tuple(float(start.get(axis, "0")) for axis in "XYZ")
    d = tuple(float(direction.get(axis, "0")) for axis in "XYZ")
    p = _transform_point(p, item, base_rotation, rod_shift)
    d = _transform_vector3(d, item, base_rotation)

    for axis, value in zip("XYZ", p):
        start.set(axis, format(value, ".17g"))
    for axis, value in zip("XYZ", d):
        direction.set(axis, format(value, ".17g"))


def _shape_z_bounds(shape_xml, lite_by_addr):
    points = []
    for child in shape_xml:
        if child.get("GeoAddr") is None:
            continue
        record = lite_by_addr.get(int(child.get("GeoAddr")))
        if record is None:
            continue
        for curve in primitives(record):
            points.extend(curve.sample(96))
    z_values = [
        float(point[2])
        for point in points
        if len(point) >= 3 and math.isfinite(float(point[2]))
    ]
    if not z_values:
        raise FormatError(
            f"Shape {shape_xml.get('Handle')} has no usable 3D Z bounds"
        )
    return min(z_values), max(z_values)



MARKING_COLLISION_CLEARANCE_MM = 1.0
ROUND_MARKING_ANGLE_STEP_DEG = 15


def _shape_xyz_bounds(shape_xml, lite_by_addr):
    points = []
    for child in shape_xml:
        if child.get("GeoAddr") is None:
            continue
        record = lite_by_addr.get(int(child.get("GeoAddr")))
        if record is None:
            continue
        for curve in primitives(record):
            points.extend(curve.sample(64))

    if shape_xml.tag == "Line":
        start = shape_xml.find("LineStart")
        direction = shape_xml.find("LineVector")
        if start is not None:
            point = tuple(float(start.get(axis, "0")) for axis in "XYZ")
            points.append(point)
            if direction is not None:
                delta = tuple(
                    float(direction.get(axis, "0"))
                    for axis in "XYZ"
                )
                points.append(tuple(a + b for a, b in zip(point, delta)))

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


def _collect_marking_obstacles(
    references,
    *,
    excluded_handles,
    shapes_by_handle,
    shape_record_by_addr,
    lite_by_addr,
):
    excluded = {str(value) for value in excluded_handles}
    obstacles = []
    for ref in references:
        handle = str(ref.get("Handle"))
        if handle in excluded:
            continue
        shape_xml = shapes_by_handle.get(handle)
        if shape_xml is None or shape_xml.get("DataAddr") is None:
            continue
        shape_record = shape_record_by_addr.get(
            int(shape_xml.get("DataAddr"))
        )
        if shape_record is None:
            continue
        if _shape_channel(shape_record) <= 0 or _shape_do_not_cut(shape_record):
            continue
        bounds = _shape_xyz_bounds(shape_xml, lite_by_addr)
        if bounds is None:
            continue
        obstacles.append(
            {
                "handle": handle,
                "channel": _shape_channel(shape_record),
                "bounds": bounds,
            }
        )
    return obstacles


def _bounds_overlap(first, second, clearance=0.0):
    clearance = max(0.0, float(clearance))
    for axis in range(3):
        if first[1][axis] < second[0][axis] - clearance:
            return False
        if first[0][axis] > second[1][axis] + clearance:
            return False
    return True


def _colliding_obstacle_handles(
    marking_bounds,
    obstacles,
    clearance=MARKING_COLLISION_CLEARANCE_MM,
):
    return [
        obstacle["handle"]
        for obstacle in obstacles
        if _bounds_overlap(
            marking_bounds,
            obstacle["bounds"],
            clearance=clearance,
        )
    ]


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

    valid = sorted(
        {
            round(value, 6)
            for value in candidates
            if preferred_start - 1e-6 <= value <= latest_start + 1e-6
        }
    )
    return valid


def _flat_marking_faces():
    return ("+Y", "+X", "-Y", "-X")


def _round_marking_angles():
    # Try the four principal orientations first, then fill the full circle
    # at 15-degree increments.
    preferred = [0, 90, 180, 270]
    return preferred + [
        angle
        for angle in range(0, 360, ROUND_MARKING_ANGLE_STEP_DEG)
        if angle not in preferred
    ]


def _next_export_handle(archive):
    values = []
    for name, data in archive.entries.items():
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
                values.append(int(value))
            except ValueError:
                continue

    try:
        seed = int(
            archive.xml("content.xml").find("Header").get("HandleSeed")
        )
        values.append(seed)
    except (AttributeError, TypeError, ValueError):
        pass
    return max(values or [1000]) + 1


def _flat_transform(
    archive,
    resolved,
    rod_length,
    *,
    text_marking_enabled=False,
    text_marking_height_mm=5.0,
    text_marking_font_path=None,
    text_marking_offset_mm=5.0,
):
    segments_root = archive.xml("Segments/content.xml")
    shapes_root = archive.xml("Shapes/content.xml")
    segments = segments_root.findall("TubeSegment")
    if len(segments) != len(resolved):
        raise FormatError("Intermediate segment count does not match locked rod")

    shapes_by_handle = {
        element.get("Handle"): element
        for element in shapes_root
        if element.tag != "MD5"
    }
    lite_stream = archive.stream("LiteGeos")
    lite_by_addr = {record.address: record for record in lite_stream.records}
    shapes_stream = archive.stream("Shapes")
    shape_record_by_addr = {
        record.address: record for record in shapes_stream.records
    }

    base_rotation = float(
        resolved[0].placement.get("axial_rotation_degrees") or 0.0
    )
    minimum_start = min(
        float(item.placement.get("z_start") or 0.0)
        for item in resolved
    )
    rod_shift = -min(0.0, minimum_start)

    first_near_handle = None
    last_far_handle = None
    warnings = []
    placement_audit = []
    marking_reports = []
    marking_pending = []
    next_marking_handle = _next_export_handle(archive)
    shared_cut_pairs = []
    release_handles = []
    previous_far_handle = None
    stock_profile = resolved[0].part.profile

    if text_marking_enabled:
        if text_marking_font_path is None:
            raise ValueError("Text marking font path is not configured")
        validate_romans_font(text_marking_font_path)
        text_marking_height_mm = float(text_marking_height_mm)
        if not (1.0 <= text_marking_height_mm <= 10.0):
            raise ValueError("Text marking height must be between 1 and 10 mm")

        marking_profile_kind = str(stock_profile.kind or "")
        if marking_profile_kind in {"Square", "Rect"}:
            marking_outside_width = float(stock_profile.outside_width or 0.0)
            marking_outside_height = float(stock_profile.outside_height or 0.0)
            marking_corner_radius = float(stock_profile.corner_radius or 0.0)
            if marking_outside_width <= 0.0 or marking_outside_height <= 0.0:
                raise ValueError("Invalid flat tube dimensions for TEXT marking")
        elif marking_profile_kind == "Circle":
            marking_diameter = float(stock_profile.outside_diameter or 0.0)
            if not math.isfinite(marking_diameter) or marking_diameter <= 0.0:
                raise ValueError("Invalid round tube diameter for TEXT marking")
            marking_radius = marking_diameter / 2.0
        else:
            raise ValueError(
                f"TEXT marking is not supported for profile {marking_profile_kind!r}"
            )

    for segment, item in zip(segments, resolved):
        references = list(segment.find("Shapes") or [])
        addresses = set()

        for ref in references:
            element = shapes_by_handle.get(ref.get("Handle"))
            if element is None:
                raise FormatError(
                    f"Shape handle {ref.get('Handle')} is missing from merged XML"
                )

            # Transform each shape's plane normal consistently with its contour.
            data_addr = int(element.get("DataAddr"))
            shape_record = shape_record_by_addr.get(data_addr)
            if shape_record is None:
                raise FormatError(f"Shape DataAddr {data_addr} is missing")
            _transform_shape_record(shape_record, item, base_rotation)

            for child in element:
                if "GeoAddr" in child.attrib:
                    addresses.add(int(child.get("GeoAddr")))

            if element.tag == "Line":
                _transform_xml_line(
                    element,
                    item,
                    base_rotation,
                    rod_shift,
                )

        for address in addresses:
            record = lite_by_addr.get(address)
            if record is None:
                raise FormatError(f"LiteGeos address {address} is missing")
            _transform_geometry_record(
                record,
                item,
                base_rotation,
                rod_shift,
            )

        # Release PathStartParam is repaired only after every part has been
        # transformed into final positioned-stock coordinates and shared
        # boundaries have been identified. This avoids applying the rule in a
        # source/local frame or encoding child_index + normalized_fraction.

        reversed_end = bool(item.placement.get("reversed_end_for_end"))
        near_handle = (
            segment.get("CutOffB") if reversed_end else segment.get("CutOffA")
        )
        far_handle = (
            segment.get("CutOffA") if reversed_end else segment.get("CutOffB")
        )
        if first_near_handle is None:
            first_near_handle = near_handle
        last_far_handle = far_handle

        release_handles.extend(
            [str(near_handle), str(far_handle)]
        )
        if item.placement.get("common_line_before"):
            if previous_far_handle is None:
                raise FormatError(
                    "First nested piece cannot have common_line_before"
                )
            shared_cut_pairs.append(
                (str(previous_far_handle), str(near_handle))
            )
        previous_far_handle = str(far_handle)

        if text_marking_enabled:
            if not item.marking_text:
                warnings.append(
                    f"{item.file_name}: TEXT marking omitted because label "
                    "metadata is incomplete."
                )
                marking_reports.append(
                    {
                        "status": "skipped",
                        "instanceKey": item.instance_key,
                        "fileName": item.file_name,
                        "profileKind": marking_profile_kind,
                        "reason": "missing marking metadata",
                    }
                )
            else:
                near_xml = shapes_by_handle.get(str(near_handle))
                far_xml = shapes_by_handle.get(str(far_handle))
                if near_xml is None or far_xml is None:
                    raise FormatError(
                        f"{item.file_name}: cannot resolve end cuts for text marking"
                    )

                near_min_z, near_max_z = _shape_z_bounds(
                    near_xml,
                    lite_by_addr,
                )
                far_min_z, far_max_z = _shape_z_bounds(
                    far_xml,
                    lite_by_addr,
                )
                preferred_start_z = (
                    near_max_z + float(text_marking_offset_mm)
                )
                marking_max_z = far_min_z

                source_marking_record = shape_record_by_addr.get(
                    int(near_xml.get("DataAddr"))
                )
                if source_marking_record is None:
                    raise FormatError(
                        f"{item.file_name}: incoming cut record is missing"
                    )

                obstacles = _collect_marking_obstacles(
                    references,
                    excluded_handles=(near_handle, far_handle),
                    shapes_by_handle=shapes_by_handle,
                    shape_record_by_addr=shape_record_by_addr,
                    lite_by_addr=lite_by_addr,
                )

                layouts = marking_layout_candidates(item.marking_text)
                addition = None
                fit_errors = []
                collision_attempts = []
                selected_layout_index = None
                selected_spatial_position = None
                selected_start_z = None

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
                    start_positions = _candidate_marking_start_positions(
                        preferred_start_z,
                        marking_max_z,
                        axial_width,
                        obstacles,
                    )
                    if not start_positions:
                        fit_errors.append(
                            f"layout {layout_index + 1}: axial width "
                            f"{axial_width:.3f} mm does not fit"
                        )
                        continue

                    spatial_positions = (
                        _flat_marking_faces()
                        if marking_profile_kind in {"Square", "Rect"}
                        else _round_marking_angles()
                    )

                    for candidate_start_z in start_positions:
                        for spatial_position in spatial_positions:
                            try:
                                if marking_profile_kind in {"Square", "Rect"}:
                                    candidate = build_marking_records(
                                        source_shape_record=source_marking_record,
                                        font_path=text_marking_font_path,
                                        lines=layout_lines,
                                        height_mm=text_marking_height_mm,
                                        start_z=candidate_start_z,
                                        outside_width=marking_outside_width,
                                        outside_height=marking_outside_height,
                                        corner_radius=marking_corner_radius,
                                        max_z=marking_max_z,
                                        first_handle=next_marking_handle,
                                        face=spatial_position,
                                        prepared_layout=prepared_layout,
                                    )
                                else:
                                    candidate = build_round_marking_records(
                                        source_shape_record=source_marking_record,
                                        font_path=text_marking_font_path,
                                        lines=layout_lines,
                                        height_mm=text_marking_height_mm,
                                        start_z=candidate_start_z,
                                        radius=marking_radius,
                                        max_z=marking_max_z,
                                        first_handle=next_marking_handle,
                                        circumferential_center_deg=spatial_position,
                                        prepared_layout=prepared_layout,
                                    )
                            except MarkingFitError as exc:
                                message = (
                                    f"layout {layout_index + 1}, "
                                    f"position {spatial_position}, "
                                    f"Z {candidate_start_z:.3f}: {exc}"
                                )
                                if message not in fit_errors:
                                    fit_errors.append(message)
                                continue

                            colliding = _colliding_obstacle_handles(
                                candidate["report"]["geometry_3d_bounds"],
                                obstacles,
                            )
                            if colliding:
                                collision_attempts.append(
                                    {
                                        "layout": layout_index + 1,
                                        "position": spatial_position,
                                        "startZ": candidate_start_z,
                                        "shapeHandles": colliding,
                                    }
                                )
                                continue

                            addition = candidate
                            selected_layout_index = layout_index
                            selected_spatial_position = spatial_position
                            selected_start_z = candidate_start_z
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
                        f"{item.file_name}: TEXT marking omitted after scanning "
                        f"the available tube surfaces and axial positions. {reason}"
                    )
                    marking_reports.append(
                        {
                            "status": "skipped",
                            "instanceKey": item.instance_key,
                            "fileName": item.file_name,
                            "profileKind": marking_profile_kind,
                            "originalText": item.marking_text,
                            "attemptedLayouts": layouts,
                            "fitErrors": fit_errors,
                            "collisionAttempts": collision_attempts,
                            "obstacleCount": len(obstacles),
                            "nearCutZBounds": [near_min_z, near_max_z],
                            "farCutZBounds": [far_min_z, far_max_z],
                        }
                    )
                else:
                    next_marking_handle = addition["next_handle"]

                    segment_shapes = segment.find("Shapes")
                    if segment_shapes is None:
                        raise FormatError(
                            f"{item.file_name}: TubeSegment has no Shapes list"
                        )

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
                        shapes_stream.records.append(shape_record)
                        lite_stream.records.append(geometry_record)
                        shapes_root.append(shape_element)
                        segment_shapes.append(segment_ref)
                        shapes_by_handle[shape_element.get("Handle")] = shape_element
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
                            "fileName": item.file_name,
                            "profileKind": marking_profile_kind,
                            "originalText": item.marking_text,
                            "layoutAttempt": selected_layout_index + 1,
                            "fallbackUsed": bool(selected_layout_index),
                            "selectedSurfacePosition": selected_spatial_position,
                            "selectedStartZ": selected_start_z,
                            "preferredStartZ": preferred_start_z,
                            "shiftedAxially": (
                                abs(selected_start_z - preferred_start_z) > 1e-6
                            ),
                            "obstacleCount": len(obstacles),
                            "collisionAttemptsBeforeSuccess": collision_attempts,
                            "fitErrorsBeforeSuccess": fit_errors,
                            "nearCutZBounds": [near_min_z, near_max_z],
                            "farCutZBounds": [far_min_z, far_max_z],
                        }
                    )
                    marking_reports.append(report)

        placement_audit.append(
            {
                "instanceKey": item.instance_key,
                "fileName": item.file_name,
                "zStart": float(item.placement.get("z_start") or 0.0) + rod_shift,
                "zEnd": float(item.placement.get("z_end") or 0.0) + rod_shift,
                "axialRotationDegrees": (
                    float(item.placement.get("axial_rotation_degrees") or 0.0)
                    - base_rotation
                )
                % 360.0,
                "reversedEndForEnd": reversed_end,
            }
        )

    # Serialize transformed/or newly appended records before collapsing.
    archive.entries["LiteGeos/data.bin"] = lite_stream.encode()
    archive.entries["Shapes/data.bin"] = shapes_stream.encode()

    for (
        shape_element,
        geometry_element,
        shape_record,
        geometry_record,
    ) in marking_pending:
        shape_element.set("DataAddr", str(shape_record.address))
        geometry_element.set("GeoAddr", str(geometry_record.address))

    root_content = archive.xml("content.xml")
    header = root_content.find("Header")
    if header is not None and marking_pending:
        header.set("HandleSeed", str(next_marking_handle))
        archive.entries["content.xml"] = xml_bytes(root_content)

    archive.entries["Shapes/content.xml"] = xml_bytes(shapes_root)

    # Addresses are now final, so include new markings in the ordering maps.
    lite_by_addr = {
        record.address: record
        for record in lite_stream.records
    }
    shape_record_by_addr = {
        record.address: record
        for record in shapes_stream.records
    }

    first = segments[0]
    first.set("Name", "ULTRA positioned stock contours")
    first.set("CutOffA", str(first_near_handle))
    first.set("CutOffB", str(last_far_handle))

    first_shapes = first.find("Shapes")
    if first_shapes is None:
        raise FormatError("First TubeSegment has no Shapes list")
    for segment in segments[1:]:
        other_shapes = segment.find("Shapes")
        if other_shapes is not None:
            first_shapes.extend(list(other_shapes))
        segments_root.remove(segment)

    ordered_refs = _order_shape_references_left_to_right(
        list(first_shapes),
        shapes_by_handle,
        shape_record_by_addr,
        lite_by_addr,
    )
    first_shapes[:] = ordered_refs

    # The verified TubePro workaround is exactly one segment record after the
    # pack record. All machining geometry remains in Shapes/LiteGeos.
    segment_stream = archive.stream("Segments")
    if len(segment_stream.records) < 2:
        raise FormatError("Intermediate archive has no segment record")
    segment_stream.records = segment_stream.records[:2]
    archive.entries["Segments/data.bin"] = segment_stream.encode()
    archive.entries["Segments/content.xml"] = xml_bytes(segments_root)

    portions_root = archive.xml("Portions/content.xml")
    pack_xml = portions_root.find("DocPortion/PackSegments")
    if pack_xml is None:
        raise FormatError("Intermediate archive has no PackSegments")

    work_seq = pack_xml.find("WorkSeq")
    if work_seq is None or not list(work_seq):
        raise FormatError("Intermediate archive has no WorkSeq")
    work_seq[:] = list(work_seq)[:1]
    for ref in list(pack_xml.findall("TubeSegment"))[1:]:
        pack_xml.remove(ref)
    archive.entries["Portions/content.xml"] = xml_bytes(portions_root)

    used_end = max(
        float(item.placement.get("z_end") or 0.0) + rod_shift
        for item in resolved
    )
    if used_end > float(rod_length) + 1e-5:
        raise ValueError(
            f"Nested geometry ends at {used_end:.3f} mm, beyond "
            f"{float(rod_length):.3f} mm stock"
        )

    portion_stream = archive.stream("Portions")
    portion_block = next(
        block
        for block in portion_stream.records[0].blocks
        if block.name == "DocPortion"
    )
    raw = bytearray(portion_block.payload)
    min_bound = read_vector(raw, 4)
    max_bound = read_vector(raw, 32)
    raw[4:32] = vector((min_bound[0], min_bound[1], 0.0))
    raw[32:60] = vector((max_bound[0], max_bound[1], used_end))
    portion_block.payload = bytes(raw)
    archive.entries["Portions/data.bin"] = portion_stream.encode()

    cut_release_report = repair_single_segment_cut_release(
        archive,
        profile_kind=str(stock_profile.kind or ""),
        outside_width=stock_profile.outside_width,
        outside_height=stock_profile.outside_height,
        outside_diameter=stock_profile.outside_diameter,
        shared_pairs=shared_cut_pairs,
        release_handles=release_handles,
    )

    if cut_release_report["disabled_duplicate_groups"]:
        warnings.append(
            "Shared cutoff group left disabled because no enabled channel-1 "
            "copy was present: "
            + repr(cut_release_report["disabled_duplicate_groups"])
        )

    archive.refresh_checksums()
    checks = archive.validate()
    return (
        archive,
        placement_audit,
        warnings,
        used_end,
        marking_reports,
        cut_release_report,
    )


def export_flat_nested_rod(
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
    """Export one locked rod using the TubePro-verified single-segment form."""
    rod_length = float(rod_length)
    gap_mm = float(gap_mm)
    if not math.isfinite(rod_length) or rod_length <= 0:
        raise ValueError("rod_length must be positive")
    if not math.isfinite(gap_mm) or gap_mm < 0:
        raise ValueError("gap_mm must be finite and nonnegative")

    resolved, all_layers = _resolve_inputs(placements)
    template = _choose_template(resolved, all_layers)

    # The handoff-proven merger remaps all binary/XML handles and addresses.
    intermediate = nest_singletons(
        [item.archive for item in resolved],
        gap=gap_mm,
        template_archive=template,
    )

    (
        archive,
        audit,
        warnings,
        used_end,
        marking_reports,
        cut_release_report,
    ) = _flat_transform(
        intermediate,
        resolved,
        rod_length,
        text_marking_enabled=bool(text_marking_enabled),
        text_marking_height_mm=float(text_marking_height_mm),
        text_marking_font_path=text_marking_font_path,
    )
    _update_metadata(archive.entries, title or Path(output_path).stem)
    archive.refresh_checksums()
    checks = archive.validate()
    archive.write(output_path)

    return {
        "path": str(Path(output_path)),
        "pieceCount": len(resolved),
        "segmentCount": 1,
        "usedSpan": float(used_end),
        "warnings": warnings,
        "validation": checks,
        "placements": audit,
        "textMarkings": marking_reports,
        "textMarkingEnabled": bool(text_marking_enabled),
        "cutReleaseRepair": cut_release_report,
        "fileVersion": WRITABLE_FILE_VERSION,
        "exportMode": "single_segment_positioned_contours",
    }


def export_flat_nested_rod_to_directory(
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
    return export_flat_nested_rod(
        placements,
        output_path,
        rod_length=rod_length,
        gap_mm=gap_mm,
        title=output_path.stem,
        text_marking_enabled=text_marking_enabled,
        text_marking_height_mm=text_marking_height_mm,
        text_marking_font_path=text_marking_font_path,
    )

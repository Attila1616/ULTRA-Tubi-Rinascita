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
from .release_starts import coordinate_candidates
from .text_marking import (
    MarkingFitError,
    build_marking_records,
    build_round_marking_records,
    build_round_tip_line_records,
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


def _order_piece_shape_references_release_safe(
    shape_refs,
    shapes_by_handle,
    shape_record_by_addr,
    lite_by_addr,
    *,
    near_handle,
    far_handle,
    text_marking_group_by_handle=None,
):
    """Order one physical piece so its releasing cutoff is always last.

    Internal machining may still be ordered axially for efficiency, but no
    hole, slot or marking is allowed to follow the far/releasing end cut.
    Generated TEXT is treated as one atomic machining block: all of its
    strokes stay contiguous and retain their generated order (therefore line
    1 completes before line 2, etc.) before axial scanning resumes.
    Display-only geometry is preserved after machining references.
    """
    near_handle = str(near_handle)
    far_handle = str(far_handle)
    text_marking_group_by_handle = dict(
        text_marking_group_by_handle or {}
    )
    near_ref = None
    far_ref = None
    internal = []
    display = []
    text_groups = {}

    for original_index, ref in enumerate(list(shape_refs)):
        handle = str(ref.get("Handle"))
        shape_xml = shapes_by_handle.get(handle)
        if shape_xml is None:
            continue
        record = shape_record_by_addr.get(int(shape_xml.get("DataAddr")))
        if record is None:
            continue

        if handle == near_handle:
            near_ref = ref
            continue
        if handle == far_handle:
            far_ref = ref
            continue

        channel = _shape_channel(record)
        if channel <= 0:
            display.append((original_index, ref))
            continue

        text_group = text_marking_group_by_handle.get(handle)
        if text_group is not None:
            group_id, member_index = text_group
            text_groups.setdefault(group_id, []).append(
                (
                    int(member_index),
                    original_index,
                    ref,
                    shape_xml,
                )
            )
            continue

        internal.append(
            (
                (
                    round(_shape_axial_center(shape_xml, lite_by_addr), 6),
                    1 if _shape_do_not_cut(record) else 0,
                    original_index,
                ),
                [ref],
            )
        )

    for group_id, members in text_groups.items():
        members.sort(key=lambda row: (row[0], row[1]))
        group_refs = [row[2] for row in members]
        group_start = min(
            _shape_z_bounds(row[3], lite_by_addr)[0]
            for row in members
        )
        first_original_index = min(row[1] for row in members)
        internal.append(
            (
                (
                    round(group_start, 6),
                    0,
                    first_original_index,
                ),
                group_refs,
            )
        )

    if near_ref is None or far_ref is None:
        raise FormatError(
            f"Piece cutoff ordering cannot resolve near={near_handle} "
            f"far={far_handle}"
        )

    internal.sort(key=lambda row: row[0])
    ordered = [near_ref]
    for _key, refs in internal:
        ordered.extend(refs)
    ordered.append(far_ref)
    ordered.extend(ref for _index, ref in display)
    return ordered


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
MARKING_END_PLANE_TOLERANCE_MM = 0.05
MARKING_END_EPSILON_MM = 1e-5
ROUND_MARKING_ANGLE_STEP_DEG = 15
ROUND_TIP_MARKING_PLANAR_TOLERANCE_MM = 0.05
ROUND_TIP_MARKING_MIN_ANGLE_DEG = 0.25
ROUND_TIP_MARKING_RADIUS_TOLERANCE_MM = 0.1


def _shape_geometry_points(shape_xml, lite_by_addr, sample_count=64):
    points = []
    sample_count = max(2, int(sample_count))
    for child in shape_xml:
        if child.get("GeoAddr") is None:
            continue
        record = lite_by_addr.get(int(child.get("GeoAddr")))
        if record is None:
            continue
        for curve in primitives(record):
            points.extend(curve.sample(sample_count))

    finite = [
        tuple(map(float, point[:3]))
        for point in points
        if len(point) >= 3
        and all(math.isfinite(float(value)) for value in point[:3])
    ]
    return finite


def _solve_marking_plane_3x3(matrix, values):
    augmented = [
        [float(value) for value in row] + [float(rhs)]
        for row, rhs in zip(matrix, values)
    ]
    for column in range(3):
        pivot = max(
            range(column, 3),
            key=lambda row: abs(augmented[row][column]),
        )
        if abs(augmented[pivot][column]) < 1e-12:
            raise ValueError("Singular marking end-plane fit")
        augmented[column], augmented[pivot] = (
            augmented[pivot],
            augmented[column],
        )
        scale = augmented[column][column]
        for index in range(column, 4):
            augmented[column][index] /= scale
        for row in range(3):
            if row == column:
                continue
            factor = augmented[row][column]
            for index in range(column, 4):
                augmented[row][index] -= factor * augmented[column][index]
    return [augmented[index][3] for index in range(3)]


def _fit_shape_z_plane(
    shape_xml,
    lite_by_addr,
    *,
    tolerance=MARKING_END_PLANE_TOLERANCE_MM,
):
    points = _shape_geometry_points(shape_xml, lite_by_addr, sample_count=64)
    if len(points) < 3:
        return None

    count = float(len(points))
    sx = sum(point[0] for point in points)
    sy = sum(point[1] for point in points)
    sz = sum(point[2] for point in points)
    sxx = sum(point[0] * point[0] for point in points)
    syy = sum(point[1] * point[1] for point in points)
    sxy = sum(point[0] * point[1] for point in points)
    sxz = sum(point[0] * point[2] for point in points)
    syz = sum(point[1] * point[2] for point in points)
    try:
        c, ax, by = _solve_marking_plane_3x3(
            [
                [count, sx, sy],
                [sx, sxx, sxy],
                [sy, sxy, syy],
            ],
            [sz, sxz, syz],
        )
    except ValueError:
        return None

    residual = max(
        abs(point[2] - (c + ax * point[0] + by * point[1]))
        for point in points
    )
    if not math.isfinite(residual) or residual > float(tolerance):
        return None
    return {
        "c": float(c),
        "ax": float(ax),
        "by": float(by),
        "residual": float(residual),
    }


def _plane_z_at(plane, x, y):
    return (
        float(plane["c"])
        + float(plane["ax"]) * float(x)
        + float(plane["by"]) * float(y)
    )


def _candidate_geometry_points(addition):
    points = []
    for record in addition.get("geometry_records") or []:
        for curve in primitives(record):
            sample_count = 2 if isinstance(curve, Line) else 8
            points.extend(curve.sample(sample_count))
    return [
        tuple(map(float, point[:3]))
        for point in points
        if len(point) >= 3
        and all(math.isfinite(float(value)) for value in point[:3])
    ]


def _marking_start_limits(
    probe,
    *,
    near_plane,
    far_plane,
    near_max_z,
    far_min_z,
    near_clearance_mm,
    far_clearance_mm=0.0,
):
    points = _candidate_geometry_points(probe)
    if not points:
        raise MarkingFitError("Generated TEXT marking has no usable 3D points")

    local_min_z = min(point[2] for point in points)
    local_max_z = max(point[2] for point in points)
    near_clearance_mm = max(0.0, float(near_clearance_mm))
    far_clearance_mm = max(0.0, float(far_clearance_mm))

    if near_plane is not None:
        earliest_start = max(
            _plane_z_at(near_plane, point[0], point[1])
            + float(near_plane["residual"])
            + near_clearance_mm
            - point[2]
            for point in points
        )
    else:
        earliest_start = (
            float(near_max_z)
            + near_clearance_mm
            - local_min_z
        )

    if far_plane is not None:
        latest_start = min(
            _plane_z_at(far_plane, point[0], point[1])
            - float(far_plane["residual"])
            - far_clearance_mm
            - MARKING_END_EPSILON_MM
            - point[2]
            for point in points
        )
    else:
        latest_start = (
            float(far_min_z)
            - far_clearance_mm
            - MARKING_END_EPSILON_MM
            - local_max_z
        )

    return {
        "earliest": float(earliest_start),
        "latest": float(latest_start),
        "localMinZ": float(local_min_z),
        "localMaxZ": float(local_max_z),
    }


def _marking_end_clearances(
    candidate,
    *,
    near_plane,
    far_plane,
    near_max_z,
    far_min_z,
):
    points = _candidate_geometry_points(candidate)
    if not points:
        return None

    if near_plane is not None:
        near_values = [
            point[2]
            - (
                _plane_z_at(near_plane, point[0], point[1])
                + float(near_plane["residual"])
            )
            for point in points
        ]
    else:
        near_values = [
            point[2] - float(near_max_z)
            for point in points
        ]

    if far_plane is not None:
        far_values = [
            (
                _plane_z_at(far_plane, point[0], point[1])
                - float(far_plane["residual"])
            )
            - point[2]
            for point in points
        ]
    else:
        far_values = [
            float(far_min_z) - point[2]
            for point in points
        ]

    return {
        "near": min(near_values),
        "far": min(far_values),
    }


def _candidate_marking_start_positions_in_range(
    preferred_start,
    earliest_start,
    latest_start,
    local_min_z,
    local_max_z,
    obstacles,
    *,
    clearance=MARKING_COLLISION_CLEARANCE_MM,
):
    preferred_start = float(preferred_start)
    earliest_start = float(earliest_start)
    latest_start = float(latest_start)
    local_min_z = float(local_min_z)
    local_max_z = float(local_max_z)
    clearance = max(0.0, float(clearance))
    if latest_start < earliest_start - 1e-8:
        return []

    candidates = {earliest_start, latest_start}
    if earliest_start - 1e-8 <= preferred_start <= latest_start + 1e-8:
        candidates.add(preferred_start)

    for obstacle in obstacles:
        lo_z = float(obstacle["bounds"][0][2])
        hi_z = float(obstacle["bounds"][1][2])
        candidates.add(hi_z + clearance - local_min_z)
        candidates.add(lo_z - clearance - local_max_z)

    valid = sorted(
        {
            round(value, 9)
            for value in candidates
            if earliest_start - 1e-7 <= value <= latest_start + 1e-7
        }
    )
    if not valid:
        return []

    # Preserve the marking's local axial position through end-for-end flips:
    # choose the valid candidate closest to the preferred local-A anchor.
    return sorted(
        valid,
        key=lambda value: (
            abs(value - preferred_start),
            value,
        ),
    )


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


_FLAT_MARKING_FACE_VECTORS = {
    "+Y": (0.0, 1.0),
    "+X": (1.0, 0.0),
    "-Y": (0.0, -1.0),
    "-X": (-1.0, 0.0),
}


def _relative_axial_rotation(item, base_rotation):
    return (
        float(item.placement.get("axial_rotation_degrees") or 0.0)
        - float(base_rotation)
    )


def _normalize_marking_angle(degrees):
    value = float(degrees) % 360.0
    if abs(value - 360.0) <= 1e-9 or abs(value) <= 1e-9:
        return 0.0
    return value


def _posed_flat_marking_faces(item, base_rotation):
    rotation = _relative_axial_rotation(item, base_rotation)
    reversed_end = bool(item.placement.get("reversed_end_for_end"))
    result = []
    for local_face in _flat_marking_faces():
        x, y = _FLAT_MARKING_FACE_VECTORS[local_face]
        if reversed_end:
            x = -x
        x, y = _rotate_xy(x, y, rotation)
        posed_face, alignment = max(
            (
                (face, x * vector_xy[0] + y * vector_xy[1])
                for face, vector_xy in _FLAT_MARKING_FACE_VECTORS.items()
            ),
            key=lambda item: item[1],
        )
        if alignment < 1.0 - 1e-6:
            raise FormatError(
                "Flat-profile TEXT marking requires a quarter-turn axial "
                f"orientation; got relative rotation {rotation:.6f} degrees"
            )
        if posed_face not in [value for _local, value in result]:
            result.append((local_face, posed_face))
    return result


def _posed_flat_profile_dimensions(item, base_rotation):
    profile = item.part.profile
    width = float(profile.outside_width or 0.0)
    height = float(profile.outside_height or 0.0)
    if width <= 0.0 or height <= 0.0:
        raise ValueError("Invalid flat tube dimensions for TEXT marking")

    rotation = _relative_axial_rotation(item, base_rotation) % 180.0
    distance_zero = min(abs(rotation), abs(rotation - 180.0))
    distance_ninety = abs(rotation - 90.0)
    if distance_zero <= 1e-6:
        return width, height
    if distance_ninety <= 1e-6:
        return height, width
    raise FormatError(
        "Flat-profile TEXT marking requires a 0/90/180-degree posed profile; "
        f"got relative rotation {rotation:.6f} degrees"
    )


def _round_marking_angles():
    # Preserve the original TEXT convention: source-local +X is the preferred
    # marking face. Nesting may rotate or end-for-end flip the physical piece,
    # so _posed_round_marking_angles() carries this +X face with the part.
    # In particular, a zero-rotation end-for-end flip maps local +X to X-.
    preferred = [90, 0, 180, 270]
    return preferred + [
        angle
        for angle in range(0, 360, ROUND_MARKING_ANGLE_STEP_DEG)
        if angle not in preferred
    ]


def _posed_round_marking_angles(item, base_rotation):
    rotation = _relative_axial_rotation(item, base_rotation)
    reversed_end = bool(item.placement.get("reversed_end_for_end"))
    result = []
    seen = set()
    for local_angle in _round_marking_angles():
        posed_angle = float(local_angle)
        if reversed_end:
            posed_angle = -posed_angle
        posed_angle = _normalize_marking_angle(posed_angle - rotation)
        key = round(posed_angle, 9)
        if key in seen:
            continue
        seen.add(key)
        result.append((float(local_angle), posed_angle))
    return result


def _prefer_local_marking_surface(spatial_positions, preferred_local):
    positions = list(spatial_positions or [])
    if preferred_local is None:
        return positions

    def matches(value):
        if isinstance(value, str) or isinstance(preferred_local, str):
            return str(value) == str(preferred_local)
        try:
            return (
                abs(
                    _normalize_marking_angle(float(value))
                    - _normalize_marking_angle(float(preferred_local))
                )
                <= 1e-6
            )
        except (TypeError, ValueError):
            return value == preferred_local

    preferred = [
        row
        for row in positions
        if row and matches(row[0])
    ]
    if not preferred:
        return positions
    return preferred + [
        row
        for row in positions
        if row not in preferred
    ]


def _posed_part_end(item, side):
    ends = list(item.part.ends or [])
    if len(ends) < 2:
        return None
    reversed_end = bool(item.placement.get("reversed_end_for_end"))
    if side == "near":
        index = 1 if reversed_end else 0
    elif side == "far":
        index = 0 if reversed_end else 1
    else:
        raise ValueError(f"Unknown piece end side {side!r}")
    return ends[index]


def _is_angled_planar_round_end(end):
    if end is None:
        return False
    try:
        residual = float(end.plane_max_residual_mm)
        angle = abs(float(end.angle_from_perpendicular_degrees))
    except (TypeError, ValueError):
        return False
    return (
        math.isfinite(residual)
        and math.isfinite(angle)
        and residual <= ROUND_TIP_MARKING_PLANAR_TOLERANCE_MM
        and angle >= ROUND_TIP_MARKING_MIN_ANGLE_DEG
    )


def _shape_outer_curves(shape_xml, lite_by_addr):
    curves = []
    for child in list(shape_xml):
        if child.tag != "Geometry" or child.get("GeoAddr") is None:
            continue
        record = lite_by_addr.get(int(child.get("GeoAddr")))
        if record is None:
            continue
        curves.extend(primitives(record))
    return curves


def _round_cut_tip_point(
    shape_xml,
    lite_by_addr,
    radius,
    *,
    maximum_z,
):
    curves = _shape_outer_curves(shape_xml, lite_by_addr)
    if not curves:
        raise FormatError(
            f"Shape {shape_xml.get('Handle')} has no outer contour geometry"
        )

    choices = []
    for curve_index, curve in enumerate(curves):
        if isinstance(curve, (Line, Spline)):
            parameters = coordinate_candidates(curve, 2)
            points = [
                (parameter, curve.at(parameter))
                for parameter in parameters
            ]
        else:
            sampled = curve.sample(256)
            points = [
                (
                    index / max(1, len(sampled) - 1),
                    point,
                )
                for index, point in enumerate(sampled)
            ]

        for parameter, point in points:
            if not all(math.isfinite(float(value)) for value in point[:3]):
                continue
            choices.append(
                (
                    float(point[2]),
                    float(point[1]),
                    -abs(float(point[0])),
                    curve_index,
                    float(parameter),
                    tuple(map(float, point[:3])),
                )
            )

    if not choices:
        raise FormatError(
            f"Shape {shape_xml.get('Handle')} has no finite outer contour points"
        )

    selected = (
        max(choices)
        if maximum_z
        else min(
            choices,
            key=lambda value: (
                value[0],
                -value[1],
                abs(value[2]),
                value[3],
                value[4],
            ),
        )
    )
    point = selected[-1]
    radial = math.hypot(point[0], point[1])
    if abs(radial - float(radius)) > ROUND_TIP_MARKING_RADIUS_TOLERANCE_MM:
        raise ValueError(
            f"Selected round cut tip radius {radial:.4f} mm does not match "
            f"outside radius {float(radius):.4f} mm"
        )
    return point


def _append_marking_addition(
    addition,
    *,
    segment,
    shapes_stream,
    lite_stream,
    shapes_root,
    shapes_by_handle,
    marking_pending,
):
    segment_shapes = segment.find("Shapes")
    if segment_shapes is None:
        raise FormatError("TubeSegment has no Shapes list for marking")

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
    return addition["next_handle"]


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
    round_text_head_motion_enabled=True,
    round_tip_marking_enabled=False,
    round_tip_marking_mode=None,
    round_tip_marking_length_mm=20.0,
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
    round_tip_marking_reports = []
    marking_pending = []
    text_marking_group_by_handle = {}
    preferred_text_surface_by_part = {}
    next_text_marking_group_id = 1
    next_marking_handle = _next_export_handle(archive)
    shared_cut_pairs = []
    release_handles = []
    previous_far_handle = None
    piece_order_specs = []
    stock_profile = resolved[0].part.profile
    stock_profile_kind = str(stock_profile.kind or "")

    if round_tip_marking_mode is None:
        round_tip_marking_mode = (
            "short" if bool(round_tip_marking_enabled) else "none"
        )
    else:
        round_tip_marking_mode = str(round_tip_marking_mode).strip().lower()
    if round_tip_marking_mode not in {"short", "long", "both", "none"}:
        raise ValueError(
            "Round tip marking mode must be short, long, both, or none"
        )
    round_tip_marking_enabled = round_tip_marking_mode != "none"

    round_tip_marking_length_mm = float(round_tip_marking_length_mm)
    if (
        round_tip_marking_enabled
        and (
            not math.isfinite(round_tip_marking_length_mm)
            or round_tip_marking_length_mm <= 0.0
        )
    ):
        raise ValueError("Round tip marking length must be positive")

    round_tip_marking_radius = None
    if round_tip_marking_enabled and stock_profile_kind == "Circle":
        diameter = float(stock_profile.outside_diameter or 0.0)
        if not math.isfinite(diameter) or diameter <= 0.0:
            raise ValueError("Invalid round tube diameter for tip marking")
        round_tip_marking_radius = diameter / 2.0

    if text_marking_enabled:
        if text_marking_font_path is None:
            raise ValueError("Text marking font path is not configured")
        validate_romans_font(text_marking_font_path)
        text_marking_height_mm = float(text_marking_height_mm)
        if not (1.0 <= text_marking_height_mm <= 10.0):
            raise ValueError("Text marking height must be between 1 and 10 mm")

        marking_profile_kind = stock_profile_kind
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
                marking_anchor_side = (
                    "far" if reversed_end else "near"
                )
                marking_axial_direction = (
                    -1.0 if reversed_end else 1.0
                )
                conservative_near_z = (
                    near_max_z + float(text_marking_offset_mm)
                )
                conservative_far_z = (
                    far_min_z - float(text_marking_offset_mm)
                )
                near_plane = _fit_shape_z_plane(
                    near_xml,
                    lite_by_addr,
                )
                far_plane = _fit_shape_z_plane(
                    far_xml,
                    lite_by_addr,
                )

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
                selected_local_surface_position = None
                selected_start_z = None
                selected_preferred_start_z = None
                selected_start_limits = None
                selected_end_clearances = None

                if marking_profile_kind in {"Square", "Rect"}:
                    posed_width, posed_height = _posed_flat_profile_dimensions(
                        item,
                        base_rotation,
                    )
                    posed_corner_radius = float(
                        item.part.profile.corner_radius or 0.0
                    )
                    spatial_positions = _posed_flat_marking_faces(
                        item,
                        base_rotation,
                    )
                else:
                    posed_width = None
                    posed_height = None
                    posed_corner_radius = None
                    spatial_positions = _posed_round_marking_angles(
                        item,
                        base_rotation,
                    )

                surface_cache_key = (
                    str(
                        getattr(item.part, "part_fingerprint", "")
                        or item.source_path
                    ),
                    marking_profile_kind,
                )
                spatial_positions = _prefer_local_marking_surface(
                    spatial_positions,
                    preferred_text_surface_by_part.get(surface_cache_key),
                )

                for layout_index, layout_lines in enumerate(layouts):
                    prepared_layout = layout_text_strokes(
                        text_marking_font_path,
                        layout_lines,
                        text_marking_height_mm,
                    )

                    for local_surface_position, spatial_position in spatial_positions:
                        try:
                            if marking_profile_kind in {"Square", "Rect"}:
                                probe = build_marking_records(
                                    source_shape_record=source_marking_record,
                                    font_path=text_marking_font_path,
                                    lines=layout_lines,
                                    height_mm=text_marking_height_mm,
                                    start_z=0.0,
                                    outside_width=posed_width,
                                    outside_height=posed_height,
                                    corner_radius=posed_corner_radius,
                                    max_z=1e12,
                                    first_handle=next_marking_handle,
                                    face=spatial_position,
                                    prepared_layout=prepared_layout,
                                    axial_direction=marking_axial_direction,
                                )
                            else:
                                probe = build_round_marking_records(
                                    source_shape_record=source_marking_record,
                                    font_path=text_marking_font_path,
                                    lines=layout_lines,
                                    height_mm=text_marking_height_mm,
                                    start_z=0.0,
                                    radius=marking_radius,
                                    max_z=1e12,
                                    first_handle=next_marking_handle,
                                    circumferential_center_deg=spatial_position,
                                    prepared_layout=prepared_layout,
                                    axial_direction=marking_axial_direction,
                                    head_motion_mode=round_text_head_motion_enabled,
                                )

                            required_near_clearance = (
                                float(text_marking_offset_mm)
                                if marking_anchor_side == "near"
                                else 0.0
                            )
                            required_far_clearance = (
                                float(text_marking_offset_mm)
                                if marking_anchor_side == "far"
                                else 0.0
                            )
                            start_limits = _marking_start_limits(
                                probe,
                                near_plane=near_plane,
                                far_plane=far_plane,
                                near_max_z=near_max_z,
                                far_min_z=far_min_z,
                                near_clearance_mm=required_near_clearance,
                                far_clearance_mm=required_far_clearance,
                            )
                            if marking_anchor_side == "near":
                                preferred_start_z = (
                                    conservative_near_z
                                    - start_limits["localMinZ"]
                                )
                            else:
                                preferred_start_z = (
                                    conservative_far_z
                                    - start_limits["localMaxZ"]
                                )
                        except MarkingFitError as exc:
                            message = (
                                f"layout {layout_index + 1}, "
                                f"position {spatial_position}: {exc}"
                            )
                            if message not in fit_errors:
                                fit_errors.append(message)
                            continue

                        start_positions = _candidate_marking_start_positions_in_range(
                            preferred_start_z,
                            start_limits["earliest"],
                            start_limits["latest"],
                            start_limits["localMinZ"],
                            start_limits["localMaxZ"],
                            obstacles,
                        )
                        if not start_positions:
                            fit_errors.append(
                                f"layout {layout_index + 1}, "
                                f"position {spatial_position}: surface-aware "
                                "axial range is too short"
                            )
                            continue

                        for candidate_start_z in start_positions:
                            try:
                                if marking_profile_kind in {"Square", "Rect"}:
                                    candidate = build_marking_records(
                                        source_shape_record=source_marking_record,
                                        font_path=text_marking_font_path,
                                        lines=layout_lines,
                                        height_mm=text_marking_height_mm,
                                        start_z=candidate_start_z,
                                        outside_width=posed_width,
                                        outside_height=posed_height,
                                        corner_radius=posed_corner_radius,
                                        max_z=1e12,
                                        first_handle=next_marking_handle,
                                        face=spatial_position,
                                        prepared_layout=prepared_layout,
                                        axial_direction=marking_axial_direction,
                                    )
                                else:
                                    candidate = build_round_marking_records(
                                        source_shape_record=source_marking_record,
                                        font_path=text_marking_font_path,
                                        lines=layout_lines,
                                        height_mm=text_marking_height_mm,
                                        start_z=candidate_start_z,
                                        radius=marking_radius,
                                        max_z=1e12,
                                        first_handle=next_marking_handle,
                                        circumferential_center_deg=spatial_position,
                                        prepared_layout=prepared_layout,
                                        axial_direction=marking_axial_direction,
                                        head_motion_mode=round_text_head_motion_enabled,
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

                            end_clearances = _marking_end_clearances(
                                candidate,
                                near_plane=near_plane,
                                far_plane=far_plane,
                                near_max_z=near_max_z,
                                far_min_z=far_min_z,
                            )
                            if (
                                end_clearances is None
                                or end_clearances["near"]
                                < required_near_clearance
                                - MARKING_END_EPSILON_MM
                                or end_clearances["far"]
                                < required_far_clearance
                                - MARKING_END_EPSILON_MM
                            ):
                                fit_errors.append(
                                    f"layout {layout_index + 1}, "
                                    f"position {spatial_position}, "
                                    f"Z {candidate_start_z:.3f}: marking "
                                    "crosses an end-cut boundary"
                                )
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
                                        "localPosition": local_surface_position,
                                        "startZ": candidate_start_z,
                                        "shapeHandles": colliding,
                                    }
                                )
                                continue

                            addition = candidate
                            selected_layout_index = layout_index
                            selected_spatial_position = spatial_position
                            selected_local_surface_position = (
                                local_surface_position
                            )
                            preferred_text_surface_by_part.setdefault(
                                surface_cache_key,
                                local_surface_position,
                            )
                            selected_start_z = candidate_start_z
                            selected_preferred_start_z = preferred_start_z
                            selected_start_limits = start_limits
                            selected_end_clearances = end_clearances
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
                            "nearEndPlaneAware": near_plane is not None,
                            "farEndPlaneAware": far_plane is not None,
                        }
                    )
                else:
                    next_marking_handle = addition["next_handle"]
                    text_group_id = next_text_marking_group_id
                    next_text_marking_group_id += 1
                    for member_index, (
                        shape_element,
                        _geometry_element,
                    ) in enumerate(addition["shape_elements"]):
                        text_marking_group_by_handle[
                            str(shape_element.get("Handle"))
                        ] = (text_group_id, member_index)

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
                    geometry_bounds = report["geometry_3d_bounds"]
                    tip_region_used = (
                        float(geometry_bounds[0][2])
                        < conservative_near_z - MARKING_END_EPSILON_MM
                        or float(geometry_bounds[1][2])
                        > conservative_far_z + MARKING_END_EPSILON_MM
                    )
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
                            "selectedLocalSurfacePosition": (
                                selected_local_surface_position
                            ),
                            "selectedStartZ": selected_start_z,
                            "preferredStartZ": selected_preferred_start_z,
                            "shiftedAxially": (
                                abs(
                                    selected_start_z
                                    - selected_preferred_start_z
                                ) > 1e-6
                            ),
                            "markingAnchorEnd": marking_anchor_side,
                            "textAxialDirection": int(
                                marking_axial_direction
                            ),
                            "tipRegionUsed": tip_region_used,
                            "surfaceAwareStartRange": [
                                selected_start_limits["earliest"],
                                selected_start_limits["latest"],
                            ],
                            "nearEndClearanceMm": (
                                selected_end_clearances["near"]
                            ),
                            "farEndClearanceMm": (
                                selected_end_clearances["far"]
                            ),
                            "requiredNearEndClearanceMm": (
                                float(text_marking_offset_mm)
                                if marking_anchor_side == "near"
                                else 0.0
                            ),
                            "requiredFarEndClearanceMm": (
                                float(text_marking_offset_mm)
                                if marking_anchor_side == "far"
                                else 0.0
                            ),
                            "nearEndPlaneAware": near_plane is not None,
                            "farEndPlaneAware": far_plane is not None,
                            "obstacleCount": len(obstacles),
                            "collisionAttemptsBeforeSuccess": collision_attempts,
                            "fitErrorsBeforeSuccess": fit_errors,
                            "nearCutZBounds": [near_min_z, near_max_z],
                            "farCutZBounds": [far_min_z, far_max_z],
                        }
                    )
                    marking_reports.append(report)


        if (
            round_tip_marking_enabled
            and stock_profile_kind == "Circle"
            and round_tip_marking_radius is not None
        ):
            near_xml = shapes_by_handle.get(str(near_handle))
            far_xml = shapes_by_handle.get(str(far_handle))
            if near_xml is None or far_xml is None:
                raise FormatError(
                    f"{item.file_name}: cannot resolve end cuts for round tip marking"
                )

            near_min_z, near_max_z = _shape_z_bounds(
                near_xml,
                lite_by_addr,
            )
            far_min_z, far_max_z = _shape_z_bounds(
                far_xml,
                lite_by_addr,
            )

            for side, end_xml, end_handle, direction in (
                ("near", near_xml, near_handle, 1.0),
                ("far", far_xml, far_handle, -1.0),
            ):
                end_meta = _posed_part_end(item, side)
                if not _is_angled_planar_round_end(end_meta):
                    continue

                marking_sides = (
                    ("short", "long")
                    if round_tip_marking_mode == "both"
                    else (round_tip_marking_mode,)
                )
                for marking_side in marking_sides:
                    maximum_z = (
                        (side == "near" and marking_side == "short")
                        or (side == "far" and marking_side == "long")
                    )
                    try:
                        tip_point = _round_cut_tip_point(
                            end_xml,
                            lite_by_addr,
                            round_tip_marking_radius,
                            maximum_z=maximum_z,
                        )
                        tip_z = float(tip_point[2])
                        safe_length = (
                            far_min_z - tip_z
                            if side == "near"
                            else tip_z - near_max_z
                        )
                        actual_length = min(
                            round_tip_marking_length_mm,
                            max(0.0, safe_length - 1e-6),
                        )
                        if actual_length <= 1e-3:
                            raise MarkingFitError(
                                "No axial room is available for the configured "
                                "tip reference line"
                            )

                        angle_deg = math.degrees(
                            math.atan2(
                                float(tip_point[0]),
                                float(tip_point[1]),
                            )
                        )
                        source_record = shape_record_by_addr.get(
                            int(end_xml.get("DataAddr"))
                        )
                        if source_record is None:
                            raise FormatError(
                                f"{item.file_name}: tip cut record is missing"
                            )

                        addition = build_round_tip_line_records(
                            source_shape_record=source_record,
                            radius=round_tip_marking_radius,
                            circumferential_angle_deg=angle_deg,
                            tip_z=tip_z,
                            length_mm=actual_length,
                            inward_direction=direction,
                            first_handle=next_marking_handle,
                        )
                        next_marking_handle = _append_marking_addition(
                            addition,
                            segment=segment,
                            shapes_stream=shapes_stream,
                            lite_stream=lite_stream,
                            shapes_root=shapes_root,
                            shapes_by_handle=shapes_by_handle,
                            marking_pending=marking_pending,
                        )

                        report = dict(addition["report"])
                        report.update(
                            {
                                "status": "generated",
                                "instanceKey": item.instance_key,
                                "fileName": item.file_name,
                                "side": side,
                                "tipSide": marking_side,
                                "cutHandle": str(end_handle),
                                "cutAngleFromPerpendicularDegrees": float(
                                    end_meta.angle_from_perpendicular_degrees
                                ),
                                "planeResidualMm": float(
                                    end_meta.plane_max_residual_mm
                                ),
                                "configuredLengthMm": round_tip_marking_length_mm,
                                "actualLengthMm": actual_length,
                                "lengthClipped": (
                                    actual_length
                                    < round_tip_marking_length_mm - 1e-6
                                ),
                                "tipPoint": list(tip_point),
                            }
                        )
                        round_tip_marking_reports.append(report)
                    except (FormatError, ValueError, MarkingFitError) as exc:
                        warnings.append(
                            f"{item.file_name}: round tip marking omitted on "
                            f"{side} angled cut ({marking_side} side): {exc}"
                        )
                        round_tip_marking_reports.append(
                            {
                                "status": "skipped",
                                "instanceKey": item.instance_key,
                                "fileName": item.file_name,
                                "side": side,
                                "tipSide": marking_side,
                                "cutHandle": str(end_handle),
                                "reason": str(exc),
                            }
                        )

        piece_order_specs.append(
            (segment, str(near_handle), str(far_handle), item.file_name)
        )

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

    for segment, near_handle, far_handle, file_name in piece_order_specs:
        segment_shapes_for_order = segment.find("Shapes")
        if segment_shapes_for_order is None:
            raise FormatError(f"{file_name}: TubeSegment has no Shapes list")
        segment_shapes_for_order[:] = _order_piece_shape_references_release_safe(
            list(segment_shapes_for_order),
            shapes_by_handle,
            shape_record_by_addr,
            lite_by_addr,
            near_handle=near_handle,
            far_handle=far_handle,
            text_marking_group_by_handle=text_marking_group_by_handle,
        )

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

    if cut_release_report["rejected_shared_pairs"]:
        details = "; ".join(
            f"{item['a']}/{item['b']}: {item['reason']}"
            for item in cut_release_report["rejected_shared_pairs"]
        )
        raise ValueError(
            "Shared-cut verification failed after final positioning. "
            "A zero-gap common-line boundary is not a complete compatible "
            "outer/inner contour with matching process metadata: "
            f"{details}. Recalculate and re-lock the nesting with the current "
            "common-line rules, or use a normal gap, before exporting."
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
        round_tip_marking_reports,
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
    round_text_head_motion_enabled=True,
    round_tip_marking_enabled=False,
    round_tip_marking_mode=None,
    round_tip_marking_length_mm=20.0,
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
        round_tip_marking_reports,
        cut_release_report,
    ) = _flat_transform(
        intermediate,
        resolved,
        rod_length,
        text_marking_enabled=bool(text_marking_enabled),
        text_marking_height_mm=float(text_marking_height_mm),
        text_marking_font_path=text_marking_font_path,
        round_text_head_motion_enabled=bool(round_text_head_motion_enabled),
        round_tip_marking_enabled=bool(round_tip_marking_enabled),
        round_tip_marking_mode=round_tip_marking_mode,
        round_tip_marking_length_mm=float(round_tip_marking_length_mm),
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
        "roundTextHeadMotionEnabled": bool(round_text_head_motion_enabled),
        "roundTipMarkings": round_tip_marking_reports,
        "roundTipMarkingEnabled": (
            str(round_tip_marking_mode).strip().lower() != "none"
            if round_tip_marking_mode is not None
            else bool(round_tip_marking_enabled)
        ),
        "roundTipMarkingMode": (
            str(round_tip_marking_mode).strip().lower()
            if round_tip_marking_mode is not None
            else ("short" if bool(round_tip_marking_enabled) else "none")
        ),
        "roundTipMarkingLengthMm": float(round_tip_marking_length_mm),
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
    round_text_head_motion_enabled=True,
    round_tip_marking_enabled=False,
    round_tip_marking_mode=None,
    round_tip_marking_length_mm=20.0,
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
        round_text_head_motion_enabled=round_text_head_motion_enabled,
        round_tip_marking_enabled=round_tip_marking_enabled,
        round_tip_marking_mode=round_tip_marking_mode,
        round_tip_marking_length_mm=round_tip_marking_length_mm,
    )

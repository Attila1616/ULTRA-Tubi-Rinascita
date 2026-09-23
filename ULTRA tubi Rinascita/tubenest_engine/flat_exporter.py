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
from .geometry import Spline
from .zzx_merge import nest_singletons


WRITABLE_FILE_VERSION = "65542"


@dataclass
class _InputPart:
    archive: Archive
    part: object
    placement: dict
    source_path: str
    file_name: str
    instance_key: str


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


def _flat_transform(archive, resolved, rod_length):
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

        if item.placement.get("common_line_before"):
            warnings.append(
                f"{item.file_name}: common-line boundary is exported as "
                "coincident ordinary source contours; native common-line cutting "
                "is not yet serialized by the verified flat method."
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

    # Serialize transformed records before collapsing the segment records.
    archive.entries["LiteGeos/data.bin"] = lite_stream.encode()
    archive.entries["Shapes/data.bin"] = shapes_stream.encode()
    archive.entries["Shapes/content.xml"] = xml_bytes(shapes_root)

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

    archive.refresh_checksums()
    checks = archive.validate()
    return archive, placement_audit, warnings, used_end


def export_flat_nested_rod(
    placements,
    output_path,
    *,
    rod_length=6000.0,
    gap_mm=2.0,
    title=None,
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

    archive, audit, warnings, used_end = _flat_transform(
        intermediate,
        resolved,
        rod_length,
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
    )

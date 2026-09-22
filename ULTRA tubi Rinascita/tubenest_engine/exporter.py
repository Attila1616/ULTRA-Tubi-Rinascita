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
import math
from pathlib import Path
import re
import struct
import xml.etree.ElementTree as ET

from .archive import Archive, xml_bytes
from .bcmp import FormatError, Stream, vector
from .domain import read_tube_parts


WRITABLE_FILE_VERSION = "65542"
COMMON_LINE_FLAG = 0x04


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


def _set_segment_transform(record, part, placement):
    block = next(
        (block for block in record.blocks if block.name == "TubeSegment"),
        None,
    )
    if block is None or len(block.payload) < 120:
        raise FormatError("Unsupported TubeSegment transform payload")

    angle = math.radians(float(placement.get("axial_rotation_degrees") or 0.0))
    co, si = math.cos(angle), math.sin(angle)
    reversed_end = bool(placement.get("reversed_end_for_end"))
    z_start = float(placement.get("z_start") or 0.0)

    if reversed_end:
        # Proper 180-degree end-for-end rotation followed by the requested
        # axial rotation. Source local coordinates are (X,Y,Z), with Z axial.
        basis_x = (-co, -si, 0.0)
        basis_y = (-si, co, 0.0)
        basis_z = (0.0, 0.0, -1.0)
        origin = (0.0, 0.0, z_start + float(part.axial_max))
    else:
        basis_x = (co, si, 0.0)
        basis_y = (-si, co, 0.0)
        basis_z = (0.0, 0.0, 1.0)
        origin = (0.0, 0.0, z_start - float(part.axial_min))

    payload = bytearray(block.payload)
    for offset, values in (
        (8, basis_x),
        (36, basis_y),
        (64, basis_z),
        (92, origin),
    ):
        payload[offset:offset + 28] = vector(values)
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
            )
        )

    return resolved, all_layers


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


def _patch_portion_bounds(record, profile, rod_length):
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

    extent = max(hx, hy)
    payload = bytearray(block.payload)
    payload[4:32] = vector((-extent, -extent, 0.0))
    payload[32:60] = vector((extent, extent, float(rod_length)))
    block.payload = bytes(payload)
    return True


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


def export_nested_rod(
    placements,
    output_path,
    *,
    rod_length=6000.0,
    title=None,
):
    """Export a locked ULTRA rod as one multi-segment ZZX."""
    rod_length = float(rod_length)
    if not math.isfinite(rod_length) or rod_length <= 0:
        raise ValueError("rod_length must be positive")

    resolved, all_layers = _resolve_placements(placements)
    template_item = _choose_template(resolved, all_layers)
    template = template_item.source_archive

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
    segments_stream.records.append(pack_record)

    source_doc_xml, source_portion_record = _portion_template(template)
    portion_record = copy.deepcopy(source_portion_record)
    _patch_portion_bounds(
        portion_record,
        template_item.source_part.profile,
        rod_length,
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

    def allocate_handle():
        nonlocal next_handle
        while next_handle in used_handles or next_handle in (2, 3, 4, 7, 8):
            next_handle += 1
        value = next_handle
        used_handles.add(value)
        next_handle += 1
        return value

    for ordinal, item in enumerate(resolved, 1):
        archive = item.source_archive
        source_segment = item.source_segment_xml
        source_part = item.source_part

        segment_handle = allocate_handle()
        segment_record = copy.deepcopy(item.source_segment_record)
        _set_object_handle(segment_record, segment_handle)
        _set_segment_transform(segment_record, source_part, item.placement)
        segments_stream.records.append(segment_record)

        segment_xml = copy.deepcopy(source_segment)
        segment_xml.set("Handle", str(segment_handle))
        segment_xml.set(
            "Name",
            f"{ordinal:02d} | {item.source_file_name} | {item.instance_key}",
        )
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

        source_shape_refs = source_segment.find("Shapes")
        output_shape_refs = segment_xml.find("Shapes")
        if source_shape_refs is None or output_shape_refs is None:
            raise FormatError(f"{item.source_file_name}: TubeSegment has no Shapes list")
        for child in list(output_shape_refs):
            output_shape_refs.remove(child)

        shape_handle_map = {}
        shape_record_map = {}
        shape_xml_map = {}
        geo_clones = {}

        for source_ref in list(source_shape_refs):
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

            new_handle = allocate_handle()
            shape_handle_map[old_handle] = new_handle

            cloned_record = copy.deepcopy(source_record)
            _set_object_handle(cloned_record, new_handle)
            shapes_stream.records.append(cloned_record)
            shape_record_map[old_handle] = cloned_record

            cloned_xml = copy.deepcopy(source_element)
            cloned_xml.set("Handle", str(new_handle))
            cloned_xml.attrib.pop("DataAddr", None)

            for child in list(cloned_xml):
                if child.get("GeoAddr") is None:
                    continue
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

        for side, enabled in (
            ("before", bool(item.placement.get("common_line_before"))),
            ("after", bool(item.placement.get("common_line_after"))),
        ):
            if not enabled:
                continue
            reversed_end = bool(item.placement.get("reversed_end_for_end"))
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
            "geo_clones": geo_clones,
        })

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

    doc_xml.set("DataAddr", str(portion_record.address))
    pack_xml.set("DataAddr", str(pack_record.address))

    root_content = ET.fromstring(entries["content.xml"])
    header = root_content.find("Header")
    if header is not None:
        header.set("HandleSeed", str(max(used_handles, default=1000) + 1))
    entries["content.xml"] = xml_bytes(root_content)

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
        "fileVersion": WRITABLE_FILE_VERSION,
    }


def export_nested_rod_to_directory(
    placements,
    output_dir,
    *,
    tube_type="Tube",
    rod_id="rod",
    rod_length=6000.0,
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
        title=output_path.stem,
    )

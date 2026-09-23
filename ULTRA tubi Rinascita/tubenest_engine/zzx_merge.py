"""Intermediate ZZX merger used only before single-segment flattening.

This is based on the TubePro-verified handoff implementation. The intermediate
archive may contain multiple TubeSegments, but it is never written as the
deliverable. flat_exporter collapses it to one TubeSegment per stock bar.
"""
from copy import deepcopy
import math
import struct
import xml.etree.ElementTree as ET

from .archive import Archive, xml_bytes
from .bcmp import Stream, vector, read_vector


def _handle(record, value):
    next(block for block in record.blocks if block.name == "Object").payload = struct.pack("<I", value)


def nest_singletons(parts, gap=2.0, template_archive=None):
    """Merge singleton source archives into one remapped intermediate archive."""
    if not parts:
        raise ValueError("At least one source part is required")
    if not math.isfinite(float(gap)) or float(gap) < 0:
        raise ValueError("Gap must be finite and nonnegative")

    base = template_archive or parts[0]
    out = Archive(deepcopy(base.entries))
    streams = {
        section: Stream()
        for section in ("Segments", "Curves", "LiteGeos", "Shapes", "Portions")
    }
    roots = {section: ET.Element(section) for section in streams}
    pending = []
    counter = 1000

    def alloc():
        nonlocal counter
        counter += 1
        return counter

    def link(element, attribute, record):
        pending.append((element, attribute, record))

    # One stock / one portion.
    pack = deepcopy(base.stream("Segments").records[0])
    _handle(pack, alloc())
    block = next(block for block in pack.blocks if block.name == "TubeSegments")
    if len(block.payload) != 16:
        raise ValueError("Unsupported pack payload")
    block.payload = struct.pack("<dII", float(gap), 0, 0)
    streams["Segments"].records.append(pack)

    portion = deepcopy(base.stream("Portions").records[0])
    portion_handle = alloc()
    _handle(portion, portion_handle)
    streams["Portions"].records.append(portion)

    doc = ET.SubElement(roots["Portions"], "DocPortion", Handle=str(portion_handle))
    link(doc, "DataAddr", portion)
    pack_xml = ET.SubElement(doc, "PackSegments")
    link(pack_xml, "DataAddr", pack)
    work_seq = ET.SubElement(pack_xml, "WorkSeq")

    total = 0.0
    minxy = [float("inf"), float("inf")]
    maxxy = [-float("inf"), -float("inf")]

    for source in parts:
        source.validate()
        segments = source.xml("Segments/content.xml").findall("TubeSegment")
        if len(segments) != 1:
            raise ValueError(
                "Flat ZZX export currently requires singleton source ZZX files"
            )

        segment = deepcopy(segments[0])
        old_segment_addr = int(segment.get("DataAddr"))

        handle_map = {segment.get("Handle"): str(alloc())}
        shapes = [
            deepcopy(element)
            for element in source.xml("Shapes/content.xml")
            if element.tag != "MD5"
        ]
        for element in shapes:
            handle_map[element.get("Handle")] = str(alloc())

        maps = {}
        for section in ("Curves", "LiteGeos", "Shapes", "Segments"):
            maps[section] = {}
            for record in source.stream(section).records:
                if section == "Segments" and record.address != old_segment_addr:
                    continue
                old_addr = record.address
                record = deepcopy(record)
                for obj_block in record.blocks:
                    if obj_block.name == "Object":
                        old_handle = str(struct.unpack("<I", obj_block.payload)[0])
                        if old_handle in handle_map:
                            obj_block.payload = struct.pack(
                                "<I", int(handle_map[old_handle])
                            )
                maps[section][old_addr] = record
                streams[section].records.append(record)

        # Remap geometry references in both the segment and every shape XML.
        for element in [
            *segment.iter(),
            *[child for shape in shapes for child in shape.iter()],
        ]:
            if "GeoAddr" in element.attrib:
                old_addr = int(element.get("GeoAddr"))
                link(element, "GeoAddr", maps["LiteGeos"][old_addr])

        for element in segment.iter():
            for attr in ("Handle", "CutOffA", "CutOffB"):
                if attr in element.attrib:
                    element.set(attr, handle_map[element.get(attr)])

        link(segment, "DataAddr", maps["Segments"][old_segment_addr])
        cross_section = segment.find("CrossSection")
        link(
            cross_section,
            "DataAddr",
            maps["Curves"][int(cross_section.get("DataAddr"))],
        )
        roots["Segments"].append(segment)

        for element in shapes:
            link(
                element,
                "DataAddr",
                maps["Shapes"][int(element.get("DataAddr"))],
            )
            for attr in ("Handle", "CopyHandle"):
                if attr in element.attrib:
                    old_value = element.get(attr)
                    if old_value not in handle_map:
                        raise ValueError(
                            f"Shape {attr} {old_value} cannot be remapped"
                        )
                    element.set(attr, handle_map[old_value])
            roots["Shapes"].append(element)

        ET.SubElement(work_seq, "Seg", Handle=segment.get("Handle"))
        ET.SubElement(pack_xml, "TubeSegment", Handle=segment.get("Handle"))

        portion_block = next(
            block
            for block in source.stream("Portions").records[0].blocks
            if block.name == "DocPortion"
        )
        low = read_vector(portion_block.payload, 4)
        high = read_vector(portion_block.payload, 32)
        total += high[2] - low[2]
        minxy = [min(a, b) for a, b in zip(minxy, low[:2])]
        maxxy = [max(a, b) for a, b in zip(maxxy, high[:2])]

    total += float(gap) * (len(parts) - 1)
    portion_block = next(
        block for block in portion.blocks if block.name == "DocPortion"
    )
    raw = bytearray(portion_block.payload)
    raw[4:32] = vector((*minxy, 0.0))
    raw[32:60] = vector((*maxxy, total))
    portion_block.payload = bytes(raw)

    for section, stream in streams.items():
        out.entries[section + "/data.bin"] = stream.encode()

    for element, attribute, record in pending:
        element.set(attribute, str(record.address))

    for section, root in roots.items():
        out.entries[section + "/content.xml"] = xml_bytes(root)

    root = out.xml("content.xml")
    root.find("Header").set("HandleSeed", str(alloc()))
    out.entries["content.xml"] = xml_bytes(root)

    viewport_root = out.xml("Viewports/content.xml")
    for viewport in viewport_root.findall("VPort"):
        viewport.set("Handle", str(alloc()))
    root.find("Header").set("HandleSeed", str(counter))
    out.entries["content.xml"] = xml_bytes(root)
    out.entries["Viewports/content.xml"] = xml_bytes(viewport_root)

    out.refresh_checksums()
    out.validate()
    return out

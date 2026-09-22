"""Deep diagnostic for ZZX files that the normal TubeNest reader cannot classify.

Read-only: prints archive/XML/BCMP metadata and never modifies the source file.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import struct
import sys
import zipfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import runtime_paths
from tubenest_engine.bcmp import Stream


def _safe_xml(raw):
    try:
        return ET.fromstring(raw)
    except Exception:
        return None


def _block_summary(block):
    preview = block.payload[:96].hex()
    ints = []
    doubles = []
    if len(block.payload) >= 4:
        for off in range(0, min(len(block.payload), 48) - 3, 4):
            ints.append(struct.unpack_from("<I", block.payload, off)[0])
    if len(block.payload) >= 8:
        for off in range(0, min(len(block.payload), 64) - 7, 8):
            try:
                doubles.append(struct.unpack_from("<d", block.payload, off)[0])
            except Exception:
                break
    return {
        "name": block.name,
        "version": block.version,
        "payload_bytes": len(block.payload),
        "payload_hex_preview": preview,
        "uint32_preview": ints[:12],
        "double_preview": doubles[:8],
    }


def inspect_file(path):
    print("\n" + "=" * 100)
    print(path)
    print("=" * 100)

    try:
        stat = os.stat(path)
        print(f"size={stat.st_size} bytes")
    except OSError as exc:
        print("stat error:", exc)
        return

    try:
        with zipfile.ZipFile(path, "r") as z:
            names = z.namelist()
            print("members:", len(names))
            print("member names:", ", ".join(names))

            if "content.xml" in names:
                root = _safe_xml(z.read("content.xml"))
                if root is not None:
                    print("root:", root.tag, dict(root.attrib))
                    header = root.find("Header")
                    if header is not None:
                        print("header:", dict(header.attrib))
                else:
                    print("content.xml: XML parse failed")

            if "Segments/content.xml" in names:
                seg_root = _safe_xml(z.read("Segments/content.xml"))
                if seg_root is not None:
                    for seg in seg_root.findall("TubeSegment"):
                        print("\nTubeSegment:", dict(seg.attrib))
                        section = seg.find("CrossSection")
                        if section is not None:
                            print("CrossSection:", dict(section.attrib))
                            geom = section.find("Geometry")
                            if geom is not None:
                                print("CrossSection Geometry:", dict(geom.attrib))

            for section_name in ("Curves", "LiteGeos", "Segments", "Shapes", "Portions"):
                bin_name = f"{section_name}/data.bin"
                if bin_name not in names:
                    continue
                print(f"\n--- {section_name} BCMP ---")
                try:
                    stream = Stream.decode(z.read(bin_name))
                except Exception as exc:
                    print("decode error:", type(exc).__name__, exc)
                    continue

                for record in stream.records:
                    print(f"record addr={record.address} class={record.name} version={record.version} blocks={len(record.blocks)}")
                    for block in record.blocks:
                        summary = _block_summary(block)
                        print(
                            "  block",
                            summary["name"],
                            "v", summary["version"],
                            "bytes", summary["payload_bytes"],
                            "u32", summary["uint32_preview"],
                            "dbl", summary["double_preview"],
                            "hex", summary["payload_hex_preview"],
                        )
    except Exception as exc:
        print("archive error:", type(exc).__name__, exc)


def main():
    if not os.path.exists(runtime_paths.CONFIG_FILE):
        print(f"Config not found: {runtime_paths.CONFIG_FILE}")
        return 2

    with open(runtime_paths.CONFIG_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)

    tubi_root = str(config.get("da_fare_path") or runtime_paths.TUBI_DIR).strip()
    if not os.path.isdir(tubi_root):
        print(f"Tubi folder not found: {tubi_root}")
        return 2

    targets = []
    for root, _, files in os.walk(tubi_root):
        for name in files:
            if not name.lower().endswith(".zzx"):
                continue
            path = os.path.join(root, name)
            try:
                with zipfile.ZipFile(path, "r") as z:
                    root_xml = _safe_xml(z.read("content.xml"))
                    file_ver = root_xml.get("FileVer") if root_xml is not None else None

                    unknown_section = False
                    if "Segments/content.xml" in z.namelist():
                        seg_root = _safe_xml(z.read("Segments/content.xml"))
                        if seg_root is not None:
                            unknown_section = any(
                                (seg.find("CrossSection") is not None)
                                and (seg.find("CrossSection").get("SectionClass") in {"", None, "Unknown"})
                                for seg in seg_root.findall("TubeSegment")
                            )

                    if file_ver != "65542" or unknown_section:
                        targets.append(path)
            except Exception:
                targets.append(path)

    print(f"Deep-inspecting {len(targets)} candidate file(s) under {tubi_root}")
    for path in targets:
        inspect_file(path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

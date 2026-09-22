from __future__ import annotations

import hashlib
import struct
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SAMPLES = [
    ROOT / "Round tube Ø30 L1215, first cut 0° layer 1, second cut 45° layer 4.zzx",
    ROOT / "Tube with sample text.zzx",
]


def align8(n):
    return (n + 7) & ~7


def read_header(data, off, marker, limit):
    if off + 20 > limit or data[off:off + 4] != marker:
        raise ValueError(f"bad record at {off}")
    ver, size, tag, n = struct.unpack_from("<IQHH", data, off + 4)
    end = off + size
    pos = align8(off + 20 + n)
    name = data[off + 20:off + 20 + n].decode("ascii")
    return name, ver, end, pos


def decode_bcmp(data):
    if len(data) < 4096 or data[:4] != b"BCMP":
        return []
    rows = []
    off = 4096
    while off < len(data):
        name, ver, end, pos = read_header(data, off, b"\xd1" * 4, len(data))
        blocks = []
        while pos < end - 4 and data[pos:pos + 4] == b"\xc1" * 4:
            bname, bver, bend, payload = read_header(data, pos, b"\xc1" * 4, end - 4)
            raw = data[payload:bend - 4]
            blocks.append((bname, bver, payload, raw))
            pos = bend
        rows.append((off, name, ver, blocks, data[pos:end - 4]))
        off = align8(end)
    return rows


def attrs_with_layer(root):
    found = []
    for elem in root.iter():
        relevant = {k: v for k, v in elem.attrib.items() if "layer" in k.lower() or "channel" in k.lower()}
        if relevant:
            found.append((elem.tag, relevant))
    return found


def print_xml(name, raw):
    try:
        root = ET.fromstring(raw)
    except Exception as exc:
        print(f"XML {name}: parse error: {exc}")
        print(raw.decode("utf-8", errors="replace"))
        return
    print(f"\n--- XML {name} ---")
    print(raw.decode("utf-8", errors="replace"))
    layerish = attrs_with_layer(root)
    if layerish:
        print("LAYER/CHANNEL ATTRIBUTES:", layerish)
    print("TAG COUNTS:", dict(Counter(e.tag for e in root.iter())))


def inspect(path):
    print("\n" + "=" * 100)
    print(path.name)
    print("sha256", hashlib.sha256(path.read_bytes()).hexdigest())
    with zipfile.ZipFile(path, "r") as z:
        print("MEMBERS:")
        for info in z.infolist():
            print(f"  {info.filename:32s} usize={info.file_size:6d} csize={info.compress_size:6d}")

        for name in [
            "content.xml",
            "Segments/content.xml",
            "Layers/content.xml",
            "Shapes/content.xml",
            "LiteGeos/content.xml",
            "Technical/content.xml",
            "Portions/content.xml",
        ]:
            if name in z.namelist():
                print_xml(name, z.read(name))

        for name in ["Shapes/data.bin", "LiteGeos/data.bin", "Segments/data.bin"]:
            if name not in z.namelist():
                continue
            raw = z.read(name)
            print(f"\n--- BCMP {name} ({len(raw)} bytes) ---")
            for addr, rec_name, rec_ver, blocks, tail in decode_bcmp(raw):
                print(f"RECORD addr={addr} name={rec_name} ver={rec_ver} tail={tail.hex()}")
                for bname, bver, payload_addr, payload in blocks:
                    # Curve and Shape are the most likely places for per-shape metadata.
                    print(
                        f"  BLOCK {bname} v{bver} payload_addr={payload_addr} "
                        f"len={len(payload)} hex={payload.hex()}"
                    )


if __name__ == "__main__":
    for sample in SAMPLES:
        inspect(sample)

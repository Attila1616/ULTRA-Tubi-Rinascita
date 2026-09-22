"""Lossless codec for the BCMP object streams observed in ZZX FileVer 65542.

Unknown payloads remain bytes. Names and payloads are aligned to absolute file
positions, not relative to their containing record. No vendor SDK is required.
"""
from dataclasses import dataclass, field
import struct


class FormatError(ValueError):
    pass


def align8(n):
    return (n + 7) & ~7


@dataclass
class Block:
    name: str
    version: int = 1
    payload: bytes = b""
    address: int = 0
    payload_address: int = 0


@dataclass
class Record:
    name: str
    blocks: list[Block] = field(default_factory=list)
    tail: bytes = b""
    version: int = 0
    address: int = 0


@dataclass
class Stream:
    records: list[Record] = field(default_factory=list)
    header: bytes = b"BCMP" + struct.pack("<II", 4096, 1) + bytes(4084)

    @classmethod
    def decode(cls, data):
        if len(data) < 4096 or data[:4] != b"BCMP":
            raise FormatError("Missing 4096-byte BCMP header")
        if struct.unpack_from("<I", data, 4)[0] != len(data):
            raise FormatError("BCMP byte count does not match file length")
        result = cls(header=data[:4096])
        off = 4096
        while off < len(data):
            name, ver, end, pos = _header(data, off, b"\xd1" * 4, len(data))
            if data[end - 4:end] != b"\xd0" * 4:
                raise FormatError(f"Missing object terminator at {end - 4}")
            rec = Record(name, version=ver, address=off)
            while pos < end - 4 and data[pos:pos + 4] == b"\xc1" * 4:
                bname, bver, bend, payload = _header(data, pos, b"\xc1" * 4, end - 4)
                if data[bend - 4:bend] != b"\xc0" * 4:
                    raise FormatError(f"Missing block terminator at {bend - 4}")
                rec.blocks.append(Block(bname, bver, data[payload:bend - 4], pos, payload))
                pos = bend
            rec.tail = data[pos:end - 4]
            result.records.append(rec)
            if any(data[end:align8(end)]):
                raise FormatError("Nonzero object alignment padding")
            off = align8(end)
        return result

    def encode(self):
        out = bytearray(self.header)
        for rec in self.records:
            rec.address = len(out)
            _begin(out, b"\xd1" * 4, rec.version, rec.name)
            for block in rec.blocks:
                block.address = len(out)
                _begin(out, b"\xc1" * 4, block.version, block.name)
                block.payload_address = len(out)
                out.extend(block.payload)
                out.extend(b"\xc0" * 4)
                struct.pack_into("<Q", out, block.address + 8, len(out) - block.address)
            out.extend(rec.tail)
            out.extend(b"\xd0" * 4)
            struct.pack_into("<Q", out, rec.address + 8, len(out) - rec.address)
            out.extend(bytes(align8(len(out)) - len(out)))
        struct.pack_into("<I", out, 4, len(out))
        return bytes(out)


def _header(data, off, marker, limit):
    if off + 20 > limit or data[off:off + 4] != marker:
        raise FormatError(f"Invalid record at {off}")
    ver, size, tag, n = struct.unpack_from("<IQHH", data, off + 4)
    end = off + size
    pos = align8(off + 20 + n)
    if tag != 0x53 or end > limit or pos > end - 4:
        raise FormatError(f"Invalid record size/name at {off}")
    if any(data[off + 20 + n:pos]):
        raise FormatError(f"Nonzero name padding at {off}")
    try:
        name = data[off + 20:off + 20 + n].decode("ascii")
    except UnicodeDecodeError as exc:
        raise FormatError(f"Unsupported class name at {off}") from exc
    return name, ver, end, pos


def _begin(out, marker, version, name):
    name = name.encode("ascii")
    out.extend(marker + struct.pack("<IQHH", version, 0, 0x53, len(name)) + name)
    out.extend(bytes(align8(len(out)) - len(out)))


def vector(values):
    return struct.pack("<HH", len(values) * 8, len(values)) + struct.pack("<" + "d" * len(values), *values)


def read_vector(data, offset=0, dimension=3):
    if data[offset:offset + 4] != struct.pack("<HH", dimension * 8, dimension):
        raise FormatError(f"Invalid {dimension}D vector tag at {offset}")
    return struct.unpack_from("<" + "d" * dimension, data, offset + 4)

"""rEffectStrip ``.efs``: strip paths effects follow (vertex chains for trails, ribbons and path moves).

``rEffectStrip::load`` (PS3 0x0102A0F8, DDDA.exe 0x011DA100) checks the magic and version, copies the
header's counts into the class (mParamBuffSize, mPartsNum, mJointNum, mTotalVertexNum, mTotalIndexNum
-- names from DDO.exe's createProperty) and the body verbatim.  Layout, measured on every file and
consistent with the counts:

  0x00 "EFS\\0"  0x04 u32 0x20080912 (both games)  0x08 mParamBuffSize (= file size - 0x20)  0x0C u32 (0)
  0x10 mPartsNum  0x14 mJointNum  0x18 mTotalVertexNum  0x1C mTotalIndexNum
  body: mPartsNum x u32 part offsets (from the body start, in order, back to back), then per part:
        u32 vertexNum, u32 indexNum, vertexNum x 32-byte vertex, indexNum x 8-byte record

mTotalVertexNum / mTotalIndexNum are the sums over the parts (checked).  The vertex and 8-byte record
layouts (the engine's VERTEX_PARAM; the code that reads them is calcVertices / getPath*Vertex) have
no names in the PS3 build; Riftstone keeps them as bytes.

Proof: every distinct .efs parses and rebuilds byte-for-byte (DDDA 25, DDO 6).  UNKNOWN: the vertex
and record fields, the u32 at 0x0C.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .errors import FormatError

MAGIC = b"EFS\0"
VERSION = 0x20080912
HEADER = 0x20
VERTEX = 32
RECORD = 8
_HEAD = struct.Struct("<4sIIIIIII")


@dataclass
class Part:
    vertices: list[bytes] = field(default_factory=list)    # 32 bytes each
    records: list[bytes] = field(default_factory=list)     # 8 bytes each


@dataclass
class Efs:
    joint_num: int = 0
    unk0c: int = 0
    parts: list[Part] = field(default_factory=list)


def parse(data: bytes) -> Efs:
    data = bytes(data)
    if data[:4] != MAGIC:
        raise FormatError("efs", f"not an .efs file (magic {data[:4]!r}, expected {MAGIC!r})", 0)
    if len(data) < HEADER:
        raise FormatError("efs", f"truncated header ({len(data)} bytes)", 0)
    _, version, size, unk, parts_n, joints, total_v, total_i = _HEAD.unpack_from(data, 0)
    if version != VERSION:
        raise FormatError("efs", f"version 0x{version:08x} is not 0x{VERSION:08x}", 4)
    body = data[HEADER:]
    if size != len(body):
        raise FormatError("efs", f"mParamBuffSize {size} != {len(body)} bytes after the header", 8)
    if parts_n > len(body) // 12:
        raise FormatError("efs", f"{parts_n} parts do not fit in {len(body)} bytes", 0x10)
    offs = struct.unpack_from(f"<{parts_n}I", body, 0)
    p = 4 * parts_n
    efs = Efs(joints, unk)
    for k, o in enumerate(offs):
        if o != p:
            raise FormatError("efs", f"part {k} starts at 0x{o:x}, expected 0x{p:x} (parts are back to back)",
                              HEADER + 4 * k)
        if p + 8 > len(body):
            raise FormatError("efs", f"part {k} header runs past the end", HEADER + p)
        nv, ni = struct.unpack_from("<II", body, p)
        p += 8
        end = p + VERTEX * nv + RECORD * ni
        if nv > len(body) or ni > len(body) or end > len(body):
            raise FormatError("efs", f"part {k}: {nv} vertices and {ni} records run past the end", HEADER + p - 8)
        part = Part([body[p + VERTEX * i:p + VERTEX * (i + 1)] for i in range(nv)])
        p += VERTEX * nv
        part.records = [body[p + RECORD * i:p + RECORD * (i + 1)] for i in range(ni)]
        p = end
        efs.parts.append(part)
    if p != len(body):
        raise FormatError("efs", f"{len(body) - p} byte(s) after the last part", HEADER + p)
    if total_v != sum(len(x.vertices) for x in efs.parts) or total_i != sum(len(x.records) for x in efs.parts):
        raise FormatError("efs", "mTotalVertexNum / mTotalIndexNum do not match the parts", 0x18)
    return efs


def build(efs: Efs) -> bytes:
    body = bytearray()
    p = 4 * len(efs.parts)
    table = bytearray()
    for k, part in enumerate(efs.parts):
        if any(len(v) != VERTEX for v in part.vertices) or any(len(r) != RECORD for r in part.records):
            raise FormatError("efs", f"part {k}: vertices are {VERTEX} bytes and records {RECORD}")
        table += struct.pack("<I", p)
        chunk = (struct.pack("<II", len(part.vertices), len(part.records))
                 + b"".join(part.vertices) + b"".join(part.records))
        body += chunk
        p += len(chunk)
    body = table + body
    try:
        head = _HEAD.pack(MAGIC, VERSION, len(body), efs.unk0c, len(efs.parts), efs.joint_num,
                          sum(len(x.vertices) for x in efs.parts), sum(len(x.records) for x in efs.parts))
    except struct.error as e:
        raise FormatError("efs", f"a header value does not fit: {e}") from None
    return head + bytes(body)


def info(efs: Efs) -> str:
    v = sum(len(x.vertices) for x in efs.parts)
    r = sum(len(x.records) for x in efs.parts)
    return (f"rEffectStrip: {len(efs.parts)} part(s), {v} vertices ({VERTEX} bytes each), {r} records "
            f"({RECORD} bytes), mJointNum {efs.joint_num}; vertex fields kept as bytes")

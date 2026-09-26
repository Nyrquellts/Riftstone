"""DDDA SPAC v12 split RIFF banks and safe edits to existing sound-cue codecs.

SPAC wave headers precede two opaque engine tables; sample payloads are stored
separately. Unknown table fields are retained. Replacement is deliberately
size/format preserving until the opaque metadata grammar is independently mapped.
"""
from __future__ import annotations

import copy
import struct
from dataclasses import dataclass

from . import sound
from .errors import FormatError

MAX_BANK = 256 * 1024 * 1024


def _bad(message: str) -> FormatError:
    return FormatError("SPAC", message)


@dataclass(frozen=True)
class Wave:
    riff: bytes
    header_spans: tuple[tuple[int, int, int], ...]  # bank offset, wave offset, size
    payload_spans: tuple[tuple[int, int, int], ...]
    format: bytes


@dataclass(frozen=True)
class Bank:
    raw: bytes
    waves: tuple[Wave, ...]
    table0_count: int
    table1_count: int

    def build(self) -> bytes:
        return self.raw

    def replace(self, index: int, riff: bytes) -> "Bank":
        if type(index) is not int or not 0 <= index < len(self.waves):
            raise _bad("wave index out of range")
        old = self.waves[index]
        fmt, chunks = riff_chunks(riff)
        _, old_chunks = riff_chunks(old.riff)
        if len(riff) != len(old.riff) or fmt != old.format or [(c[0], c[2]) for c in chunks] != [(c[0], c[2]) for c in old_chunks]:
            raise _bad("replacement must preserve RIFF chunk layout, encoded size and fmt bytes; bank table resizing is unverified")
        out = bytearray(self.raw)
        for bank_at, wave_at, size in old.header_spans + old.payload_spans:
            out[bank_at:bank_at + size] = riff[wave_at:wave_at + size]
        result = parse(bytes(out))
        if result.waves[index].riff != riff:
            raise _bad("replacement did not reconstruct exactly")
        return result


def riff_chunks(data: bytes) -> tuple[bytes, list[tuple[bytes, int, int]]]:
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE" or struct.unpack_from("<I", data, 4)[0] + 8 != len(data):
        raise _bad("invalid RIFF/WAVE size or magic")
    chunks = []
    pos = 12
    fmt = None
    while pos < len(data):
        if pos + 8 > len(data):
            raise _bad("truncated RIFF chunk header")
        kind, size = struct.unpack_from("<4sI", data, pos)
        end = pos + 8 + size + (size & 1)
        if end > len(data):
            raise _bad("RIFF chunk exceeds file")
        if kind == b"fmt ":
            if fmt is not None or size < 16:
                raise _bad("invalid or duplicate fmt chunk")
            fmt = data[pos + 8:pos + 8 + size]
        chunks.append((kind, pos, size))
        pos = end
    if fmt is None or sum(c[0] == b"data" for c in chunks) != 1:
        raise _bad("one fmt and one data chunk are required")
    return fmt, chunks


def parse(data: bytes) -> Bank:
    if len(data) < 32 or len(data) > MAX_BANK:
        raise _bad("bank is truncated or exceeds 256 MiB")
    magic, version, count, count0, count1, table0, table1, payload = struct.unpack_from("<4s7I", data)
    if magic != b"SPAC" or version != 12:
        raise _bad("requires DDDA little-endian SPAC version 12")
    if not 32 <= table0 <= table1 <= payload <= len(data) or count > (table0 - 32) // 12:
        raise _bad("invalid SPAC offsets/count")
    if table1 - table0 != count0 * 20 or payload - table1 != count1 * 44:
        raise _bad("opaque metadata table strides are not the measured DDDA profile")
    pos, sample_at = 32, payload
    waves = []
    for _ in range(count):
        if pos + 12 > table0 or data[pos:pos + 4] != b"RIFF" or data[pos + 8:pos + 12] != b"WAVE":
            raise _bad("expected split RIFF header")
        total = struct.unpack_from("<I", data, pos + 4)[0] + 8
        if total > MAX_BANK or total < 12:
            raise _bad("invalid reconstructed RIFF size")
        wave = bytearray(data[pos:pos + 12])
        headers = [(pos, 0, 12)]
        samples = []
        pos += 12
        while len(wave) < total:
            if pos + 8 > table0:
                raise _bad("truncated split RIFF chunk")
            kind, size = struct.unpack_from("<4sI", data, pos)
            padded = size + (size & 1)
            if padded > total - len(wave) - 8:
                raise _bad("split RIFF chunk exceeds declared wave")
            if kind == b"data":
                if sample_at + padded > len(data):
                    raise _bad("wave payload exceeds bank")
                headers.append((pos, len(wave), 8))
                wave += data[pos:pos + 8]
                samples.append((sample_at, len(wave), padded))
                wave += data[sample_at:sample_at + padded]
                sample_at += padded
                pos += 8
            else:
                if pos + 8 + padded > table0:
                    raise _bad("wave metadata overlaps the first opaque table")
                headers.append((pos, len(wave), 8 + padded))
                wave += data[pos:pos + 8 + padded]
                pos += 8 + padded
        fmt, _ = riff_chunks(bytes(wave))
        if struct.unpack_from("<H", fmt)[0] not in (1, 2):
            raise _bad("only PCM and MS-ADPCM RIFF waves are supported")
        waves.append(Wave(bytes(wave), tuple(headers), tuple(samples), fmt))
    if pos != table0 or sample_at != len(data):
        raise _bad("unrecognized padding/trailing bytes in SPAC profile")
    return Bank(data, tuple(waves), count0, count1)


def clone_cue(source: sound.Sound, template: int, cue_id: int) -> sound.Sound:
    """Append a cloned DDDA request by ID, rebuilding dynamic memory-image offsets."""
    if source.fmt not in ("srq", "stq"):
        raise FormatError("cue", "cue cloning requires DDDA SRQ or STQ")
    if type(cue_id) is not int or not 0 <= cue_id < 65535:
        raise FormatError("cue", "new cue ID must be 0..65534; 65535 is reserved")
    ids = [e["mReqNo"] for e in source.data["elements"]]
    if cue_id in ids or ids.count(template) != 1:
        raise FormatError("cue", "duplicate ID or missing/ambiguous template")
    if len(ids) >= 65535:
        raise FormatError("cue", "16-bit cue ID space is exhausted")
    result = copy.deepcopy(source)
    element = copy.deepcopy(result.data["elements"][ids.index(template)])
    element["mReqNo"] = cue_id
    result.data["elements"].append(element)
    # Validation/reparse checks all emitted offsets and integer widths.
    return sound.parse(sound.build(result))


def add_random(source: sound.Sound, random_id: int, picks: list[tuple[int, int]]) -> sound.Sound:
    if source.fmt != "srd":
        raise FormatError("cue", "random picks require DDDA SRD, not a resource definition table")
    if type(random_id) is not int or not 0 <= random_id < 0xFFFFFFFF:
        raise FormatError("cue", "random ID must be 0..4294967294")
    if any(e["mRandomNo"] == random_id for e in source.data["elements"]):
        raise FormatError("cue", "random ID already exists")
    if not 1 <= len(picks) <= 16:
        raise FormatError("cue", "a random entry has exactly 16 storage slots; supply 1..16 picks")
    if any(type(c) is not int or not 0 <= c < 65535 or type(w) is not int or not 1 <= w <= 0xFFFFFFFF for c, w in picks):
        raise FormatError("cue", "invalid cue ID or weight")
    if sum(w for _, w in picks) > 0xFFFFFFFF:
        raise FormatError("cue", "random weight sum overflows 32 bits")
    result = copy.deepcopy(source)
    table = [{"mReqNo": c, "mRate": w} for c, w in picks]
    table += [{"mReqNo": 0xFFFFFFFF, "mRate": 0}] * (16 - len(table))
    result.data["elements"].append({"mRandomNo": random_id, "mTable": table, "mAllowRepeat": 0,
                                    "pad_85": 0, "pad_86": 0, "pad_87": 0, "mLastReqNo": -1})
    return sound.parse(sound.build(result))

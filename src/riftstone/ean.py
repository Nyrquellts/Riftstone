"""rEffectAnim ``.ean``: effect texture UV animation (sprite-atlas frames).

39 files, `effect\\tex\\..\\t_*`. The loader (`rEffectAnim::load`, PS3 `0x01027618`,
named) validates the magic and version, reads a payload byte count and a
frame count, then copies the whole payload verbatim into one buffer -- so to the
engine the body is a single opaque blob. Riftstone models it the same way and
round-trips every file byte-exact (`check_corpus --only ean`).

Layout (measured on the corpus and confirmed against the loader):

  0x00  "EAN\\0"                (magic 0x004E4145)
  0x04  u32 0x20100924         (version)
  0x08  u32 payloadSize        (= file size - 0x10)
  0x0C  u32 frameCount
  0x10  payloadSize bytes      (frame data)

The payload is two parallel per-frame arrays -- ``frameCount x 32`` then
``frameCount x 24`` bytes (every file is ``frameCount * 56``); the 0x08 size is the
first array's length plus the second. Their field layouts (UV rect, timing) are not
decoded yet, so the payload is kept opaque; that is enough to round-trip and to
retarget the effect by swapping the whole animation. ``info`` reports the counts.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from .errors import FormatError

MAGIC = b"EAN\0"
VERSION = 0x20100924
# Dragon's Dogma Online: the same container with a later date (all 50 of its files; the header's size
# equals the payload in every one). Its frame records are not always 56 bytes (24 files differ), so the
# payload stays opaque there too.
VERSION_DDO = 0x20120224
VERSIONS = (VERSION, VERSION_DDO)
_HEAD = struct.Struct("<4sIII")
HEADER_LEN = _HEAD.size  # 0x10
FRAME_A = 32
FRAME_B = 24


@dataclass
class Ean:
    count: int
    payload: bytes
    version: int = VERSION


def parse(data: bytes) -> Ean:
    if data[:4] != MAGIC:
        raise FormatError("ean", f"not a .ean file (magic {bytes(data[:4])!r}, expected {MAGIC!r})")
    if len(data) < HEADER_LEN:
        raise FormatError("ean", f"truncated header ({len(data)} bytes)")
    _, version, size, count = _HEAD.unpack_from(data, 0)
    if version not in VERSIONS:
        raise FormatError("ean", f"unexpected version 0x{version:08x} (expected 0x{VERSION:08x} or "
                                 f"0x{VERSION_DDO:08x})")
    payload = data[HEADER_LEN:]
    if size != len(payload):
        raise FormatError("ean", f"payload size {size} != {len(payload)} bytes after the header")
    return Ean(count, payload, version)


def build(e: Ean) -> bytes:
    if e.version not in VERSIONS:
        raise FormatError("ean", f"version 0x{e.version:08x} is not one the games use")
    return _HEAD.pack(MAGIC, e.version, len(e.payload), e.count) + e.payload


def info(e: Ean) -> str:
    n = e.count
    structured = len(e.payload) == n * (FRAME_A + FRAME_B)
    shape = f"{n} x {FRAME_A} + {n} x {FRAME_B}" if structured else "opaque"
    return (f"rEffectAnim: {n} frames, {len(e.payload)} bytes of frame data ({shape})")

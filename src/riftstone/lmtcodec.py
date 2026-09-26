"""Keyframe codecs of ``.lmt`` bone tracks: raw keys out, the same bytes back, values to read.

A track's buffer is a run of fixed-size keys in one of twelve codecs (``lmt.CODECS``). Each key
holds its components and, for every codec but 1 and 2, the number of frames it lasts (the
"delta"; codecs 1 and 2 hold one key per frame). ``keys`` splits a buffer into
``(delta, components)`` with every bit accounted for, so ``pack(codec, keys(...))`` gives the
buffer back byte for byte (measured on every buffer of both games, ``check_corpus --only
lmt``). ``values`` turns keys into numbers: floats as stored; packed components scaled to
0..1 (bilinear) or signed (codec 6), then, for bilinear codecs, mapped through the track's
extremes (value = offset + scale * component; extremes hold scale[4] then offset[4]).

Bit layouts (little-endian words; "a|b" = a field whose high bits are ``a`` and low bits
``b``, as the 16-bit-word and byte-wise packing of the 9- and 11-bit codecs stores them):

  1  vector3        f32 x, y, z                              one key per frame
  2  quat3          f32 x, y, z  (w = sqrt(1 - x2 - y2 - z2))   one key per frame
  3  lin. vector3   f32 x, y, z, u32 delta
  4  vector3 16-bit u16 x, y, z, u16 delta                   / 65535
  5  vector3 8-bit  u8 x, y, z, u8 delta                     / 255
  6  quat 14-bit    u64: x 42-55, y 28-41, z 14-27, w 0-13, delta 56-63; signed (> 8191 is
                    negative: v - 16383), * 4 / 16383
  7  quat 7-bit     u32: x 21-27, y 14-20, z 7-13, w 0-6, delta 28-31     / 127
  11/12/13 quat XW/YW/ZW 14-bit  u32: that axis 0-13, w 14-27, delta 28-31; other axes 0.
                    DDO (v67) tracks carry extremes: / 16383 through them (all 119,809).
                    DDDA (v66) tracks carry none: the stored axis and w are signed like codec 6,
                    * 4 / 16383, and the two other axes are the track's reference value -- the
                    loader points these tracks' extremes at their own reference (0x00E9D402).
                    Proof: over 4.59 M keys |q| - 1 has median 0.00011, p99 0.00026 (beats
                    / 4096 two's complement at 0.00014 / 0.00030); on the 8,959 keys whose
                    reference is non-zero on the other axes, 100% are unit within 0.01 with the
                    reference and 55% with zeros
  14 quat 11-bit    u48: x 0-10, y 11-15|16-21, z 22-31|32, w 33-43, delta 44-47   / 2047
  15 quat 9-bit     5 bytes: x b0|b1.0, y b1.1-7|b2.0-1, z b2.2-7|b3.0-2, w b3.3-7|b4.0-3,
                    delta b4.4-7                                           / 511

Proof of the layouts beyond the byte round trip: dequantised rotation keys come out unit
length and the key deltas add up to the motion's frame count (``tools/check_corpus.py``
reports both; see docs/formats.md "LMT").
"""
from __future__ import annotations

import math
import struct

from .errors import FormatError

KEY_SIZE = {1: 12, 2: 12, 3: 16, 4: 8, 5: 4, 6: 8, 7: 4, 11: 4, 12: 4, 13: 4, 14: 6, 15: 5}
PACKED_QUAT = (7, 11, 12, 13, 14, 15)
BILINEAR = (4, 5, 7, 11, 12, 13, 14, 15)


def _need(codec: int, buf: bytes) -> int:
    size = KEY_SIZE.get(codec)
    if size is None:
        raise FormatError("lmt", f"unknown track codec {codec}")
    if len(buf) % size:
        raise FormatError("lmt", f"codec {codec} buffer of {len(buf)} bytes is not whole {size}-byte keys")
    return size


def keys(codec: int, buf: bytes) -> list[tuple[int, tuple]]:
    """[(delta, components)] -- components are floats' exact bit patterns (u32) for codecs 1-3
    and the packed integers for the rest; delta is 1 for codecs 1 and 2."""
    size = _need(codec, buf)
    out = []
    for at in range(0, len(buf), size):
        k = buf[at:at + size]
        if codec in (1, 2):
            out.append((1, struct.unpack("<3I", k)))
        elif codec == 3:
            x, y, z, d = struct.unpack("<4I", k)
            out.append((d, (x, y, z)))
        elif codec == 4:
            x, y, z, d = struct.unpack("<4H", k)
            out.append((d, (x, y, z)))
        elif codec == 5:
            x, y, z, d = k
            out.append((d, (x, y, z)))
        elif codec == 6:
            v = struct.unpack("<Q", k)[0]
            out.append((v >> 56, ((v >> 42) & 0x3FFF, (v >> 28) & 0x3FFF, (v >> 14) & 0x3FFF, v & 0x3FFF)))
        elif codec == 7:
            v = struct.unpack("<I", k)[0]
            out.append((v >> 28, ((v >> 21) & 0x7F, (v >> 14) & 0x7F, (v >> 7) & 0x7F, v & 0x7F)))
        elif codec in (11, 12, 13):
            v = struct.unpack("<I", k)[0]
            out.append((v >> 28, (v & 0x3FFF, (v >> 14) & 0x3FFF)))
        elif codec == 14:
            w0, w1, w2 = struct.unpack("<3H", k)
            v = w0 | w1 << 16 | w2 << 32
            x = v & 0x7FF
            y = ((v >> 11) & 0x1F) << 6 | (w1 & 0x3F)
            z = ((v >> 22) & 0x3FF) << 1 | (w2 & 1)
            w = (v >> 33) & 0x7FF
            out.append((w2 >> 12, (x, y, z, w)))   # all 48 bits used once: 11+5+6+10+1+11+4
        elif codec == 15:
            b0, b1, b2, b3, b4 = k
            x = b0 << 1 | (b1 & 1)
            y = (b1 >> 1) << 2 | (b2 & 3)
            z = (b2 >> 2) << 3 | (b3 & 7)
            w = (b3 >> 3) << 4 | (b4 & 0xF)
            out.append((b4 >> 4, (x, y, z, w)))
    return out


def pack(codec: int, ks: list[tuple[int, tuple]]) -> bytes:
    """The inverse of ``keys``; refuses a field that does not fit its bits."""
    if codec not in KEY_SIZE:
        raise FormatError("lmt", f"unknown track codec {codec}")
    out = bytearray()

    def fit(v, bits, what):
        if not isinstance(v, int) or not 0 <= v < (1 << bits):
            raise FormatError("lmt", f"codec {codec}: {what} {v!r} does not fit {bits} bits")
        return v

    for delta, c in ks:
        if codec in (1, 2):
            if delta != 1:
                raise FormatError("lmt", f"codec {codec} keys last one frame each")
            out += struct.pack("<3I", *(fit(x, 32, "component") for x in c))
        elif codec == 3:
            out += struct.pack("<4I", *(fit(x, 32, "component") for x in c), fit(delta, 32, "delta"))
        elif codec == 4:
            out += struct.pack("<4H", *(fit(x, 16, "component") for x in c), fit(delta, 16, "delta"))
        elif codec == 5:
            out += bytes([*(fit(x, 8, "component") for x in c), fit(delta, 8, "delta")])
        elif codec == 6:
            x, y, z, w = (fit(v, 14, "component") for v in c)
            out += struct.pack("<Q", fit(delta, 8, "delta") << 56 | x << 42 | y << 28 | z << 14 | w)
        elif codec == 7:
            x, y, z, w = (fit(v, 7, "component") for v in c)
            out += struct.pack("<I", fit(delta, 4, "delta") << 28 | x << 21 | y << 14 | z << 7 | w)
        elif codec in (11, 12, 13):
            a, w = (fit(v, 14, "component") for v in c)
            out += struct.pack("<I", fit(delta, 4, "delta") << 28 | w << 14 | a)
        elif codec == 14:
            x, y, z, w = (fit(v, 11, "component") for v in c)
            d = fit(delta, 4, "delta")
            v = x | (y >> 6) << 11 | (z >> 1) << 22 | w << 33 | d << 44
            w1 = (v >> 16) & 0xFFFF
            w2 = (v >> 32) & 0xFFFF
            # low parts of y and z live in the low bits of the next 16-bit word
            w1 = (w1 & ~0x3F) | (y & 0x3F)
            w2 = (w2 & ~1) | (z & 1)
            out += struct.pack("<3H", v & 0xFFFF, w1, w2)
        elif codec == 15:
            x, y, z, w = (fit(v, 9, "component") for v in c)
            d = fit(delta, 4, "delta")
            out += bytes([x >> 1, (x & 1) | (y >> 2) << 1, (y & 3) | (z >> 3) << 2,
                          (z & 7) | (w >> 4) << 3, (w & 0xF) | d << 4])
    return bytes(out)


def _f(bits: int) -> float:
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def values(codec: int, buf: bytes, extremes: bytes | None = None,
           reference: bytes | None = None) -> list[tuple[int, tuple[float, ...]]]:
    """[(frame, (x, y, z[, w]))]: each key's value and the frame it starts on (keys' deltas
    accumulated from 0). Rotations come out as quaternions (x, y, z, w). ``reference`` is the
    track's 16-byte reference value: DDDA's codec 11-13 keys take their two unstored axes from it
    (zeros when it is not given)."""
    ks = keys(codec, buf)
    ref = struct.unpack("<4f", reference) if reference is not None and len(reference) == 16 else (0.0,) * 4
    scale = offset = None
    if codec in (11, 12, 13) and extremes is None:
        pass    # DDDA's single-axis quaternions carry no extremes (see the module notes)
    elif codec in BILINEAR:
        if extremes is None or len(extremes) != 32:
            raise FormatError("lmt", f"codec {codec} needs the track's 32-byte extremes")
        e = struct.unpack("<8f", extremes)
        scale, offset = e[:4], e[4:]
    out = []
    frame = 0
    for delta, c in ks:
        if codec in (1, 3):
            v = tuple(_f(x) for x in c)
        elif codec == 2:
            x, y, z = (_f(b) for b in c)
            v = (x, y, z, math.sqrt(max(0.0, 1.0 - x * x - y * y - z * z)))
        elif codec == 4:
            v = tuple(offset[i] + scale[i] * (c[i] / 65535) for i in range(3))
        elif codec == 5:
            v = tuple(offset[i] + scale[i] * (c[i] / 255) for i in range(3))
        elif codec == 6:
            v = tuple((x - 16383 if x > 8191 else x) * 4 / 16383 for x in c)
        elif codec == 7:
            v = tuple(offset[i] + scale[i] * (c[i] / 127) for i in range(4))
        elif codec in (11, 12, 13):
            axis = codec - 11
            if scale is None:   # DDDA: signed like codec 6; the other axes are the reference's
                q = [ref[0], ref[1], ref[2], (c[1] - 16383 if c[1] > 8191 else c[1]) * 4 / 16383]
                q[axis] = (c[0] - 16383 if c[0] > 8191 else c[0]) * 4 / 16383
                v = tuple(q)
            else:
                q = [0.0, 0.0, 0.0, c[1] / 16383]
                q[axis] = c[0] / 16383
                v = tuple(offset[i] + scale[i] * q[i] for i in range(4))
        elif codec == 14:
            v = tuple(offset[i] + scale[i] * (c[i] / 2047) for i in range(4))
        else:  # 15
            v = tuple(offset[i] + scale[i] * (c[i] / 511) for i in range(4))
        out.append((frame, v))
        frame += delta
    return out


def span(codec: int, buf: bytes) -> int:
    """Frames covered by a buffer: the sum of its keys' deltas."""
    return sum(d for d, _ in keys(codec, buf))

"""rCameraList ``.lcm``: the camera paths of events and enemy cameras, both games, byte-exact, with YAML.

A camera list holds up to ``camera_num`` (256 in every file) cameras by slot; uMotionCamera plays the
slot its ``mCut`` names, advancing ``mFrame`` by ``mSpeed`` (DDO.exe properties; uMotionCamera::move
0x01578730 indexes the table with mCut).  Both loaders read the file whole, check the
header and turn the stored offsets into pointers (DDDA ``rCameraList::load`` 0x01127660, PS3
0x00FEBA68; DDO 0x016738E0), so the layout below is the engine's in-memory layout.

Header (both games)::

  0x00 "LCM\\0"  u16 version (DDDA 3, DDO 5)  u16 camera_num  then camera_num x u32 offset (0 = empty slot)

**DDDA, version 3** -- names from the PS3 build (``rCameraList::CAMERA_INFO``,
``CAMERA_LIST_HDR``, cCameraGameMot.cpp).  Camera block, 0x2C bytes::

  +0x00 s32 userdata[4]   +0x10 u32 frame_num   +0x14 u32 fovtype   +0x18 f32 aspect
  +0x1C MtFloat3* pos   +0x20 MtFloat3* target   +0x24 MtFloat4* quat   +0x28 f32* fov

One key per frame: the eye position, the look-at point, a unit quaternion (every one of the 142,153
frames is unit length) and the field of view in degrees.  ``fovtype`` is uCameraBase::FOV_TYPE
(FOV_V = 0 vertical, FOV_H = 1 horizontal).  Layout of every file: blocks in slot order right
after the table, then per camera in slot order its pos, target, quat and fov arrays, no padding.

**Dragon's Dogma Online, version 5** -- DDO.exe registers no names, so this is measured from its
loader and uMotionCamera::move (0x01578730).  Camera block, 0xAC bytes::

  +0x00 s32 userdata[8] (0 in every file; DDDA's block starts with userdata[4])
  +0x20 u32 frame_num   +0x24 u32 fovtype (copied to uMotionCamera.mFovType)   +0x28 f32 aspect
  +0x2C track pos (-> uCamera.mCameraPos)    +0x50 track target (-> mTargetPos)
  +0x74 track rot (a quaternion; its Y axis becomes mCameraUp)    +0x98 float track fov (-> mFov)

A track is LMT's 36-byte bone track (``lmt.py``): u8 codec, u8 usage (1 on pos/target, 0 on rot in
every file), u8 unk2, u8 unk3 (0), f32 weight (0), u32 buffer size, u32 buffer offset, f32[4]
reference (the value of the constant codecs 1 and 2), u32 extremes offset (32 bytes: f32[4] scale,
f32[4] offset).  A float track is 20 bytes: u32 codec (low byte; the rest 0), u32 buffer size, f32
offset, f32 scale, u32 buffer offset.  The loader relocates a track's buffer and extremes offsets
and the float track's buffer offset when they are not 0.

Keys are decoded by the camera evaluator (vector 0x01821450, float 0x018212C0) through its own codec
tables (0x020D4C50 / 0x020D4D10: key size, frame-count reader, decoder), which differ from the LMT
motion decoder: an n-bit packed value q means (q - 8) / (2**n - 16) through the extremes
(offset + scale * that), and rotations are normalised after decoding.  Vector codecs (key bytes):
0, 1, 2, 10 no keys (1, 2: the reference; 0, 10: a fixed vector) -- 3 (16) f32 x, y, z + u32
frames -- 4 (8) u16 x, y, z, frames / 65520 -- 5 (4) u8 x, y, z, frames / 240 -- 6 (8) u64 x
42-55, y 28-41, z 14-27, w 0-13 as 14-bit two's complement / 4096, frames 56-63 -- 7 (4) u32 x
21-27, y 14-20, z 7-13, w 0-6 / 112, frames 28-31 -- 8 (12) f32 x, y, z, one key per frame -- 9
(16) f32 x, y, z, w, one per frame -- 11/12/13 (4) the X/Y/Z axis 0-13 and w 14-27 / 16368 (the
other two axes are the extremes' offset), frames 28-31 -- 14 (6) 11-bit x, y, z, w / 2032 in
lmtcodec's bit layout, frames 44-47 -- 15 (5) 9-bit / 496, frames 36-39.  Float codecs: 0, 2 no
keys (2: the offset is the value) -- 1 (4) f32, one per frame -- 3 (8) f32 + u32 frames -- 4 (4)
u16 q, u16 frames -- 5 (2) u8 q, u8 frames.  A key lasts ``frames`` frames and blends toward the
next.  Layout of every file: blocks in slot order after the table; zero padding to 16; every
track's extremes (camera by camera, pos/target/rot); then per camera its pos, target and rot
buffers and its fov buffer, each from a 16-byte boundary (zero padding); nothing after.

The decoders are checked against the data too: in every DDO track the first key decodes to the
track's stored reference within one quantisation step (positions <= 0.05, rotations <= 4e-4, all
codecs 3-7 and 11-15); every packed value lies in [8, 2**n - 8] (so the "- 8" is the encoder's
margin); every keyed track's frame counts add up to frame_num - 1 (2,891 tracks).

Proof: every distinct file of both games parses, rebuilds byte-for-byte and rebuilds byte-for-byte
from its YAML -- DDDA 99/99 (999 cameras), DDO 52/52 (1,101 cameras) (``tests/test_camera.py``;
scratch ``weather_camera/prove.py``).  ``parse`` accepts only that layout, so ``build(parse(x)) ==
x`` for everything it accepts (60,000 mutated vanilla camera lists: each refused with FormatError
or rebuilt exactly).  Vector codecs 0, 8, 9, 10 and float codecs 0, 1, 3 come from the evaluator's
tables; no file uses them.

UNKNOWN: userdata (always 0), the meaning of usage/unk2/unk3/weight in a camera track (the
evaluator reads only the codec byte), what the aspect value is used for (16:9 in the files).
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field

from .errors import FormatError, ParamError

MAGIC = b"LCM\0"
VERSION_DDDA = 3
VERSION_DDO = 5
VERSIONS = (VERSION_DDDA, VERSION_DDO)
TAG = "lcm/1"
_HEAD = struct.Struct("<4sHH")
HEADER = 8
BLOCK3 = 0x2C
BLOCK5 = 0xAC
EXTREMES = 32
FRAME3 = 12 + 12 + 16 + 4
FOV_TYPES = {0: "FOV_V", 1: "FOV_H"}          # uCameraBase::FOV_TYPE (PS3 build)
TRACKS = ("pos", "target", "rot")

_B3 = struct.Struct("<4iIII4I")                 # userdata[4], frame_num, fovtype, aspect, 4 pointers
_B5 = struct.Struct("<8iIII")                   # userdata[8], frame_num, fovtype, aspect
_TR = struct.Struct("<BBBBIII4II")              # codec, usage, unk2, unk3, weight, size, buffer, ref[4], extremes
_FT = struct.Struct("<IIIII")                   # flags, size, offset, scale, buffer

# Vector codecs of the DDO camera evaluator: key fields ("f" = f32 bits, int = bit width).  The last
# field is the key's frame count, except for the one-key-per-frame codecs 8 and 9.
VEC_FIELDS: dict[int, tuple] = {
    3: ("f", "f", "f", 32), 4: (16, 16, 16, 16), 5: (8, 8, 8, 8), 6: (14, 14, 14, 14, 8),
    7: (7, 7, 7, 7, 4), 8: ("f", "f", "f"), 9: ("f", "f", "f", "f"), 11: (14, 14, 4), 12: (14, 14, 4),
    13: (14, 14, 4), 14: (11, 11, 11, 11, 4), 15: (9, 9, 9, 9, 4),
}
VEC_KEY = {3: 16, 4: 8, 5: 4, 6: 8, 7: 4, 8: 12, 9: 16, 11: 4, 12: 4, 13: 4, 14: 6, 15: 5}
VEC_KEYLESS = (0, 1, 2, 10)
BILINEAR = (4, 5, 7, 11, 12, 13, 14, 15)       # decoded through the extremes: they must be present
FLT_FIELDS: dict[int, tuple] = {1: ("f",), 3: ("f", 32), 4: (16, 16), 5: (8, 8)}
FLT_KEY = {1: 4, 3: 8, 4: 4, 5: 2}
FLT_KEYLESS = (0, 2)
PER_FRAME = (8, 9)                              # vector codecs with one key per frame (and float 1)


# -- model -------------------------------------------------------------------------------------
@dataclass
class Camera3:
    """A DDDA camera: frames are (pos xyz, target xyz, quat xyzw, fov) as f32 bit patterns."""
    userdata: tuple = (0, 0, 0, 0)
    fovtype: int = 1
    aspect: int = 0x3FE38E39                   # 16/9
    frames: list = field(default_factory=list)


@dataclass
class Track:
    """A DDO vector track (LMT bone-track layout); keys are tuples in the codec's VEC_FIELDS order."""
    codec: int
    usage: int = 0
    unk2: int = 0
    unk3: int = 0
    weight: int = 0                             # f32 bits
    reference: tuple = (0, 0, 0, 0)             # f32 bits
    extremes: tuple | None = None               # 8 x f32 bits: scale[4], offset[4]
    keys: list = field(default_factory=list)


@dataclass
class FloatTrack:
    codec: int
    unk: int = 0                                # flags >> 8
    offset: int = 0                             # f32 bits (the value of codec 2)
    scale: int = 0                              # f32 bits
    keys: list = field(default_factory=list)


@dataclass
class Camera5:
    userdata: tuple
    frame_num: int
    fovtype: int
    aspect: int
    pos: Track
    target: Track
    rot: Track
    fov: FloatTrack


@dataclass
class CameraList:
    version: int
    camera_num: int = 256
    cameras: dict = field(default_factory=dict)     # slot -> Camera3 | Camera5


# -- key packing -------------------------------------------------------------------------------
def _split(codec: int, k: bytes, vec: bool) -> tuple:
    if not vec:
        if codec == 1:
            return struct.unpack("<I", k)
        if codec == 3:
            return struct.unpack("<II", k)
        if codec == 4:
            return struct.unpack("<HH", k)
        return (k[0], k[1])                                     # 5
    if codec == 3:
        return struct.unpack("<4I", k)
    if codec == 4:
        return struct.unpack("<4H", k)
    if codec == 5:
        return tuple(k)
    if codec == 6:
        v = struct.unpack("<Q", k)[0]
        return ((v >> 42) & 0x3FFF, (v >> 28) & 0x3FFF, (v >> 14) & 0x3FFF, v & 0x3FFF, v >> 56)
    if codec == 7:
        v = struct.unpack("<I", k)[0]
        return ((v >> 21) & 0x7F, (v >> 14) & 0x7F, (v >> 7) & 0x7F, v & 0x7F, v >> 28)
    if codec == 8:
        return struct.unpack("<3I", k)
    if codec == 9:
        return struct.unpack("<4I", k)
    if codec in (11, 12, 13):
        v = struct.unpack("<I", k)[0]
        return (v & 0x3FFF, (v >> 14) & 0x3FFF, v >> 28)
    if codec == 14:
        w0, w1, w2 = struct.unpack("<3H", k)
        return (w0 & 0x7FF, (w0 >> 11) << 6 | (w1 & 0x3F), (w1 >> 6) << 1 | (w2 & 1), (w2 >> 1) & 0x7FF, w2 >> 12)
    b0, b1, b2, b3, b4 = k                                      # 15
    return (b0 << 1 | (b1 & 1), (b1 >> 1) << 2 | (b2 & 3), (b2 >> 2) << 3 | (b3 & 7), (b3 >> 3) << 4 | (b4 & 0xF),
            b4 >> 4)


def _check_key(codec: int, key, vec: bool, where: str) -> tuple:
    spec = (VEC_FIELDS if vec else FLT_FIELDS)[codec]
    if not isinstance(key, (tuple, list)) or len(key) != len(spec):
        raise FormatError("lcm", f"{where}: a codec {codec} key has {len(spec)} values")
    for v, bits in zip(key, spec):
        width = 32 if bits == "f" else bits
        if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v < (1 << width):
            raise FormatError("lcm", f"{where}: codec {codec} key value {v!r} does not fit {width} bits")
    return tuple(key)


def _pack(codec: int, key: tuple, vec: bool) -> bytes:
    if not vec:
        if codec == 1:
            return struct.pack("<I", *key)
        if codec == 3:
            return struct.pack("<II", *key)
        if codec == 4:
            return struct.pack("<HH", *key)
        return bytes(key)
    if codec == 3:
        return struct.pack("<4I", *key)
    if codec == 4:
        return struct.pack("<4H", *key)
    if codec == 5:
        return bytes(key)
    if codec == 6:
        x, y, z, w, d = key
        return struct.pack("<Q", d << 56 | x << 42 | y << 28 | z << 14 | w)
    if codec == 7:
        x, y, z, w, d = key
        return struct.pack("<I", d << 28 | x << 21 | y << 14 | z << 7 | w)
    if codec == 8:
        return struct.pack("<3I", *key)
    if codec == 9:
        return struct.pack("<4I", *key)
    if codec in (11, 12, 13):
        a, w, d = key
        return struct.pack("<I", d << 28 | w << 14 | a)
    if codec == 14:
        x, y, z, w, d = key
        return struct.pack("<3H", x | (y >> 6) << 11, (y & 0x3F) | (z >> 1) << 6, (z & 1) | w << 1 | d << 12)
    x, y, z, w, d = key                                         # 15
    return bytes([x >> 1, (x & 1) | (y >> 2) << 1, (y & 3) | (z >> 3) << 2, (z & 7) | (w >> 4) << 3, (w & 0xF) | d << 4])


# -- decoding (the DDO camera evaluator's arithmetic, for reading a path) ----------------------------
def _f(bits: int) -> float:
    return struct.unpack("<f", struct.pack("<I", bits & 0xFFFFFFFF))[0]


def _norm(q):
    n = math.sqrt(sum(c * c for c in q))
    return tuple(c / n for c in q) if n > 0 and math.isfinite(n) else tuple(q)


def _s14(v: int) -> int:
    return v - 0x4000 if v & 0x2000 else v


def key_frames(codec: int, key: tuple, vec: bool = True) -> int:
    """How many frames a key lasts (1 for the one-key-per-frame codecs)."""
    if (vec and codec in PER_FRAME) or (not vec and codec == 1):
        return 1
    return key[-1]


def decode_key(track: Track | FloatTrack, key: tuple):
    """A key's value as the DDO camera evaluator computes it: (x, y, z[, w]) or a float."""
    c = track.codec
    if isinstance(track, FloatTrack):
        if c in (1, 3):
            return _f(key[0])
        q = key[0] - 8
        return _f(track.offset) + _f(track.scale) * q / (65520 if c == 4 else 240)
    if c in (3, 8):
        return tuple(_f(b) for b in key[:3])
    if c == 9:
        return tuple(_f(b) for b in key[:4])
    if c == 6:
        return _norm(tuple(_s14(v) / 4096 for v in key[:4]))
    e = [_f(b) for b in track.extremes] if track.extremes is not None else [1.0] * 4 + [0.0] * 4
    scale, off = e[:4], e[4:]
    if c in (4, 5):
        den = 65520 if c == 4 else 240
        return tuple(off[i] + scale[i] * (key[i] - 8) / den for i in range(3))
    if c in (11, 12, 13):
        axis = c - 11
        q = list(off)
        q[axis] = off[axis] + scale[axis] * (key[0] - 8) / 16368
        q[3] = off[3] + scale[3] * (key[1] - 8) / 16368
        return _norm(q)
    den = {7: 112, 14: 2032, 15: 496}[c]
    return _norm(tuple(off[i] + scale[i] * (key[i] - 8) / den for i in range(4)))


def values(track: Track | FloatTrack) -> list:
    """[(start frame, value)] for each key; a keyless track gives its constant at frame 0."""
    if isinstance(track, FloatTrack):
        if track.codec in FLT_KEYLESS:
            return [(0, _f(track.offset) if track.codec == 2 else 0.0)]
    elif track.codec in VEC_KEYLESS:
        return [(0, tuple(_f(b) for b in track.reference) if track.codec in (1, 2) else None)]
    out, frame = [], 0
    vec = isinstance(track, Track)
    for k in track.keys:
        out.append((frame, decode_key(track, k)))
        frame += key_frames(track.codec, k, vec)
    return out


# -- parse --------------------------------------------------------------------------------------
def _align(p: int) -> int:
    return (p + 15) & ~15


def parse(data: bytes) -> CameraList:
    data = bytes(data)
    if len(data) < HEADER or data[:4] != MAGIC:
        raise FormatError("lcm", f"not a camera list (magic {data[:4]!r}, expected {MAGIC!r})", 0)
    _, version, num = _HEAD.unpack_from(data, 0)
    if version not in VERSIONS:
        raise FormatError("lcm", f"version {version}; DDDA camera lists are 3, Dragon's Dogma Online's 5", 4)
    table_end = HEADER + 4 * num
    if table_end > len(data):
        raise FormatError("lcm", f"the {num}-slot table runs past the end of the file", HEADER)
    offs = struct.unpack_from(f"<{num}I", data, HEADER)
    slots = [(i, o) for i, o in enumerate(offs) if o]
    size = BLOCK3 if version == VERSION_DDDA else BLOCK5
    p = table_end
    for i, o in slots:
        if o != p:
            raise FormatError("lcm", f"slot {i}: camera block at 0x{o:x}; the game's files put it at 0x{p:x}", HEADER + 4 * i)
        p += size
    if p > len(data):
        raise FormatError("lcm", "the camera blocks run past the end of the file", table_end)
    cl = CameraList(version, num)
    if version == VERSION_DDDA:
        _parse3(data, slots, p, cl)
    else:
        _parse5(data, slots, p, cl)
    return cl


def _parse3(data: bytes, slots, p: int, cl: CameraList) -> None:
    for i, o in slots:
        v = _B3.unpack_from(data, o)
        n, fovtype, aspect, ptrs = v[4], v[5], v[6], v[7:]
        if n > (len(data) - p) // FRAME3:
            raise FormatError("lcm", f"slot {i}: {n} frames do not fit in the file", o + 0x10)
        arrays = []
        for k, (ptr, width) in enumerate(zip(ptrs, (3, 3, 4, 1))):
            if ptr != p:
                raise FormatError("lcm", f"slot {i}: array {k} at 0x{ptr:x}; the game's files put it at 0x{p:x}", o + 0x1C + 4 * k)
            arrays.append(struct.unpack_from(f"<{n * width}I", data, p))
            p += 4 * width * n
        pos, tgt, quat, fov = arrays
        frames = [(pos[3 * f:3 * f + 3], tgt[3 * f:3 * f + 3], quat[4 * f:4 * f + 4], fov[f]) for f in range(n)]
        cl.cameras[i] = Camera3(tuple(v[:4]), fovtype, aspect, frames)
    if p != len(data):
        raise FormatError("lcm", f"{len(data) - p} byte(s) after the last camera's frames", p)


def _zeros(data: bytes, a: int, b: int, what: str) -> None:
    if data[a:b] != bytes(b - a):
        raise FormatError("lcm", f"non-zero padding before {what}", a)


def _parse5(data: bytes, slots, p: int, cl: CameraList) -> None:
    raw = []
    for i, o in slots:
        v = _B5.unpack_from(data, o)
        tracks = [_TR.unpack_from(data, o + 0x2C + 0x24 * t) for t in range(3)]
        ft = _FT.unpack_from(data, o + 0x98)
        raw.append((i, o, v, tracks, ft))
    q = _align(p) if any(tr[11] for _, _, _, tracks, _ in raw for tr in tracks) else p
    if q > len(data):
        raise FormatError("lcm", "the file ends inside the padding after the camera blocks", p)
    _zeros(data, p, q, "the track extremes")
    ext = {}
    for i, o, v, tracks, ft in raw:                        # extremes: camera by camera, pos/target/rot
        for t, tr in enumerate(tracks):
            if tr[11]:
                if tr[11] != q or q + EXTREMES > len(data):
                    raise FormatError("lcm", f"slot {i} {TRACKS[t]}: extremes at 0x{tr[11]:x}; the game's files put them at 0x{q:x}",
                                      o + 0x2C + 0x24 * t + 0x20)
                ext[(i, t)] = struct.unpack_from("<8I", data, q)
                q += EXTREMES
    for i, o, v, tracks, ft in raw:
        built = []
        for t, tr in enumerate(tracks):
            codec, usage, unk2, unk3, weight, size, buf = tr[:7]
            ref = tuple(tr[7:11])
            where = f"slot {i} {TRACKS[t]}"
            if codec in VEC_KEYLESS:
                if size or buf:
                    raise FormatError("lcm", f"{where}: codec {codec} has no keys but the track points at {size} bytes",
                                      o + 0x2C + 0x24 * t)
                keys = []
            elif codec in VEC_KEY:
                keys, q = _buffer(data, q, codec, size, buf, True, where)
            else:
                raise FormatError("lcm", f"{where}: codec {codec} is not one the camera evaluator knows", o + 0x2C + 0x24 * t)
            e = ext.get((i, t))
            if codec in BILINEAR and e is None:
                raise FormatError("lcm", f"{where}: codec {codec} needs extremes and the track has none", o + 0x2C + 0x24 * t + 0x20)
            built.append(Track(codec, usage, unk2, unk3, weight, ref, e, keys))
        flags, fsize, foff, fscale, fbuf = ft
        fcodec = flags & 0xFF
        where = f"slot {i} fov"
        if fcodec in FLT_KEYLESS:
            if fsize or fbuf:
                raise FormatError("lcm", f"{where}: codec {fcodec} has no keys but the track points at {fsize} bytes", o + 0x98)
            fkeys = []
        elif fcodec in FLT_KEY:
            fkeys, q = _buffer(data, q, fcodec, fsize, fbuf, False, where)
        else:
            raise FormatError("lcm", f"{where}: float codec {fcodec} is not one the camera evaluator knows", o + 0x98)
        fov = FloatTrack(fcodec, flags >> 8, foff, fscale, fkeys)
        cl.cameras[i] = Camera5(tuple(v[:8]), v[8], v[9], v[10], *built, fov)
    if q != len(data):
        raise FormatError("lcm", f"{len(data) - q} byte(s) after the last track buffer", q)


def _buffer(data: bytes, q: int, codec: int, size: int, buf: int, vec: bool, where: str):
    ks = (VEC_KEY if vec else FLT_KEY)[codec]
    if size == 0 or size % ks:
        raise FormatError("lcm", f"{where}: a codec {codec} buffer of {size} bytes is not whole {ks}-byte keys (at least one)")
    a = _align(q)
    if buf != a or a + size > len(data):
        raise FormatError("lcm", f"{where}: buffer at 0x{buf:x} ({size} bytes); the game's files put it at 0x{a:x}", q)
    _zeros(data, q, a, f"{where}'s buffer")
    keys = [_split(codec, data[k:k + ks], vec) for k in range(a, a + size, ks)]
    return keys, a + size


# -- build --------------------------------------------------------------------------------------
def _u32(v, what: str) -> int:
    if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 0xFFFFFFFF:
        raise FormatError("lcm", f"{what} {v!r} is not a 32-bit value")
    return v


def _s32s(vals, n: int, what: str) -> tuple:
    if not isinstance(vals, (tuple, list)) or len(vals) != n:
        raise FormatError("lcm", f"{what} holds {n} values")
    for v in vals:
        if not isinstance(v, int) or isinstance(v, bool) or not -(1 << 31) <= v < (1 << 31):
            raise FormatError("lcm", f"{what}: {v!r} is not a signed 32-bit value")
    return tuple(vals)


def _bits(vals, n: int, what: str) -> tuple:
    if not isinstance(vals, (tuple, list)) or len(vals) != n:
        raise FormatError("lcm", f"{what} holds {n} values")
    return tuple(_u32(v, what) for v in vals)


def build(cl: CameraList) -> bytes:
    if cl.version not in VERSIONS:
        raise FormatError("lcm", f"version {cl.version} is not one the games use (3 or 5)")
    num = cl.camera_num
    if not isinstance(num, int) or not 0 <= num <= 0xFFFF:
        raise FormatError("lcm", f"camera_num {num!r} does not fit 16 bits")
    for s in cl.cameras:
        if not isinstance(s, int) or not 0 <= s < num:
            raise FormatError("lcm", f"slot {s!r} is outside the {num}-slot table")
    order = sorted(cl.cameras)
    want = Camera3 if cl.version == VERSION_DDDA else Camera5
    for s in order:
        if not isinstance(cl.cameras[s], want):
            raise FormatError("lcm", f"slot {s}: a version {cl.version} list holds {want.__name__} cameras")
    return _build3(cl, order) if cl.version == VERSION_DDDA else _build5(cl, order)


def _build3(cl: CameraList, order: list) -> bytes:
    num = cl.camera_num
    p = HEADER + 4 * num
    table = [0] * num
    blocks, data = bytearray(), bytearray()
    dstart = p + BLOCK3 * len(order)
    for s in order:
        c = cl.cameras[s]
        table[s] = p
        p += BLOCK3
        n = len(c.frames)
        pos, tgt, quat, fov = [], [], [], []
        for k, fr in enumerate(c.frames):
            if not isinstance(fr, (tuple, list)) or len(fr) != 4:
                raise FormatError("lcm", f"slot {s} frame {k}: a frame is (pos, target, quat, fov)")
            pos += _bits(fr[0], 3, f"slot {s} frame {k} pos")
            tgt += _bits(fr[1], 3, f"slot {s} frame {k} target")
            quat += _bits(fr[2], 4, f"slot {s} frame {k} quat")
            fov.append(_u32(fr[3], f"slot {s} frame {k} fov"))
        ptrs = []
        for arr in (pos, tgt, quat, fov):
            ptrs.append(dstart + len(data))
            data += struct.pack(f"<{len(arr)}I", *arr)
        blocks += _B3.pack(*_s32s(c.userdata, 4, f"slot {s} userdata"), n, _u32(c.fovtype, f"slot {s} fovtype"),
                           _u32(c.aspect, f"slot {s} aspect"), *ptrs)
    return _HEAD.pack(MAGIC, cl.version, num) + struct.pack(f"<{num}I", *table) + blocks + data


def _check_track(t, s: int, name: str) -> None:
    vec = isinstance(t, Track)
    if not isinstance(t, Track if name != "fov" else FloatTrack):
        raise FormatError("lcm", f"slot {s} {name}: expected a {'FloatTrack' if name == 'fov' else 'Track'}")
    table, keyless = (VEC_KEY, VEC_KEYLESS) if vec else (FLT_KEY, FLT_KEYLESS)
    if t.codec in keyless:
        if t.keys:
            raise FormatError("lcm", f"slot {s} {name}: codec {t.codec} holds no keys")
    elif t.codec in table:
        if not t.keys:
            raise FormatError("lcm", f"slot {s} {name}: a codec {t.codec} track needs at least one key")
        for i, k in enumerate(t.keys):
            _check_key(t.codec, k, vec, f"slot {s} {name} key {i}")
    else:
        raise FormatError("lcm", f"slot {s} {name}: codec {t.codec!r} is not one the camera evaluator knows")
    if vec:
        for v, bits, what in ((t.usage, 8, "usage"), (t.unk2, 8, "unk2"), (t.unk3, 8, "unk3")):
            if not isinstance(v, int) or not 0 <= v < 1 << bits:
                raise FormatError("lcm", f"slot {s} {name}: {what} {v!r} does not fit {bits} bits")
        _u32(t.weight, f"slot {s} {name} weight")
        _bits(t.reference, 4, f"slot {s} {name} reference")
        if t.extremes is not None:
            _bits(t.extremes, 8, f"slot {s} {name} extremes")
        elif t.codec in BILINEAR:
            raise FormatError("lcm", f"slot {s} {name}: codec {t.codec} is decoded through extremes; give them")
    else:
        if not isinstance(t.unk, int) or not 0 <= t.unk < 1 << 24:
            raise FormatError("lcm", f"slot {s} fov: unk {t.unk!r} does not fit 24 bits")
        _u32(t.offset, f"slot {s} fov offset")
        _u32(t.scale, f"slot {s} fov scale")


def _build5(cl: CameraList, order: list) -> bytes:
    num = cl.camera_num
    cams = [(s, cl.cameras[s]) for s in order]
    for s, c in cams:
        for name in (*TRACKS, "fov"):
            _check_track(getattr(c, name), s, name)
    p = HEADER + 4 * num + BLOCK5 * len(cams)
    has_ext = any(getattr(c, n).extremes is not None for _, c in cams for n in TRACKS)
    q = _align(p) if has_ext else p
    ext_at, q0 = {}, q
    for s, c in cams:
        for n in TRACKS:
            if getattr(c, n).extremes is not None:
                ext_at[(s, n)] = q
                q += EXTREMES
    tail = bytearray(q0 - p)                    # padding, then the extremes
    for s, c in cams:
        for n in TRACKS:
            e = getattr(c, n).extremes
            if e is not None:
                tail += struct.pack("<8I", *e)
    buf_at = {}
    for s, c in cams:
        for n in (*TRACKS, "fov"):
            t = getattr(c, n)
            if t.keys:
                a = _align(q)
                tail += bytes(a - q)
                vec = n != "fov"
                blob = b"".join(_pack(t.codec, k, vec) for k in t.keys)
                buf_at[(s, n)] = (a, len(blob))
                tail += blob
                q = a + len(blob)
    out = bytearray(_HEAD.pack(MAGIC, cl.version, num))
    table = [0] * num
    blocks = bytearray()
    at = HEADER + 4 * num
    for s, c in cams:
        table[s] = at
        at += BLOCK5
        blocks += _B5.pack(*_s32s(c.userdata, 8, f"slot {s} userdata"), _u32(c.frame_num, f"slot {s} frame_num"),
                           _u32(c.fovtype, f"slot {s} fovtype"), _u32(c.aspect, f"slot {s} aspect"))
        for n in TRACKS:
            t = getattr(c, n)
            boff, bsize = buf_at.get((s, n), (0, 0))
            blocks += _TR.pack(t.codec, t.usage, t.unk2, t.unk3, t.weight, bsize, boff, *t.reference,
                               ext_at.get((s, n), 0))
        f = c.fov
        boff, bsize = buf_at.get((s, "fov"), (0, 0))
        blocks += _FT.pack(f.codec | f.unk << 8, bsize, f.offset, f.scale, boff)
    out += struct.pack(f"<{num}I", *table) + blocks + tail
    return bytes(out)


# -- summary ------------------------------------------------------------------------------------
def info(cl: CameraList) -> str:
    n = len(cl.cameras)
    if cl.version == VERSION_DDDA:
        frames = sum(len(c.frames) for c in cl.cameras.values())
        return (f"rCameraList v3 (Dragon's Dogma: Dark Arisen): {n} camera(s) in {cl.camera_num} slots, "
                f"{frames} frames (position, target, rotation, fov per frame)")
    frames = sum(c.frame_num for c in cl.cameras.values())
    keys = sum(len(getattr(c, t).keys) for c in cl.cameras.values() for t in (*TRACKS, "fov"))
    return (f"rCameraList v5 (Dragon's Dogma Online): {n} camera(s) in {cl.camera_num} slots, {frames} frames "
            f"as {keys} compressed keys (position, target, rotation and fov tracks)")


# -- YAML ---------------------------------------------------------------------------------------
def _ftext(bits: int) -> str:
    from .params import f32_bits_text
    return f32_bits_text(bits)


def _fl(bits_seq, comment=None):
    from .yamlish import Scalar, Seq
    return Seq([Scalar(_ftext(b)) for b in bits_seq], flow=True, comment=comment)


def _short(v) -> str:
    if v is None:
        return "fixed vector"
    if isinstance(v, float):
        return f"{v:.6g}"
    return "(" + ", ".join(f"{x:.6g}" for x in v) + ")"


def _key_node(codec: int, key: tuple, vec: bool, comment: str | None):
    from .yamlish import Scalar, Seq
    spec = (VEC_FIELDS if vec else FLT_FIELDS)[codec]
    return Seq([Scalar(_ftext(v) if bits == "f" else str(v)) for v, bits in zip(key, spec)], flow=True,
               comment=comment)


def _track_node(t, name: str):
    from .yamlish import Map, Scalar, Seq
    vec = name != "fov"
    items = [(Scalar("codec"), Scalar(str(t.codec)))]
    if vec:
        items += [(Scalar("usage"), Scalar(str(t.usage))), (Scalar("unk2"), Scalar(str(t.unk2))),
                  (Scalar("unk3"), Scalar(str(t.unk3))), (Scalar("weight"), Scalar(_ftext(t.weight))),
                  (Scalar("reference"), _fl(t.reference))]
        if t.extremes is not None:
            items.append((Scalar("extremes"), Map([(Scalar("scale"), _fl(t.extremes[:4])),
                                                   (Scalar("offset"), _fl(t.extremes[4:]))], flow=True)))
    else:
        items += [(Scalar("unk"), Scalar(str(t.unk))), (Scalar("offset"), Scalar(_ftext(t.offset))),
                  (Scalar("scale"), Scalar(_ftext(t.scale)))]
    keys = []
    frame = 0
    for k in t.keys:
        note = f"frame {frame}: {_short(decode_key(t, k))}"
        keys.append(_key_node(t.codec, k, vec, note))
        frame += key_frames(t.codec, k, vec)
    items.append((Scalar("keys"), Seq(keys, flow=not keys)))
    return Map(items)


def to_yaml(cl: CameraList, name: str | None = None) -> str:
    from . import yamlish
    from .yamlish import Map, Scalar, Seq

    ddo = cl.version == VERSION_DDO
    shown = " -- " + "".join(c if c.isprintable() else " " for c in name) if name else ""   # stays in its comment
    head = ["Riftstone camera list (.lcm, rCameraList" + (", Dragon's Dogma Online)" if ddo else ")") + shown,
            f"{len(cl.cameras)} camera(s) by slot (0..{cl.camera_num - 1}). fovtype: 0 FOV_V, 1 FOV_H; fov in degrees."]
    if ddo:
        head += ["Each camera is four tracks of keys (codecs in camera.py): packed keys are whole numbers,",
                 "the last value of a key is how many frames it lasts; the comment shows the decoded value.",
                 "Codec 3 [x, y, z, frames] / 8 [x, y, z] (per frame) / 9 [x, y, z, w] (per frame) keep floats",
                 "as written. Rebuilds byte-for-byte when untouched."]
    else:
        head += ["One row per frame: eye position, look-at target, rotation quaternion [x, y, z, w], fov.",
                 "Add or remove rows to change a path's length. Rebuilds byte-for-byte when untouched."]
    items = [(Scalar("riftstone"), Scalar(TAG))]
    if name:
        items.append((Scalar("resource"), Scalar(name, "double")))
    items += [(Scalar("version"), Scalar(str(cl.version))), (Scalar("camera_num"), Scalar(str(cl.camera_num)))]
    cams = []
    for s in sorted(cl.cameras):
        c = cl.cameras[s]
        f = [(Scalar("slot"), Scalar(str(s))),
             (Scalar("userdata"), Seq([Scalar(str(u)) for u in c.userdata], flow=True))]
        if ddo:
            f.append((Scalar("frame_num"), Scalar(str(c.frame_num))))
        f += [(Scalar("fovtype"), Scalar(str(c.fovtype), comment=FOV_TYPES.get(c.fovtype))),
              (Scalar("aspect"), Scalar(_ftext(c.aspect)))]
        if ddo:
            for n in (*TRACKS, "fov"):
                f.append((Scalar(n), _track_node(getattr(c, n), n)))
        else:
            rows = [Map([(Scalar("pos"), _fl(fr[0])), (Scalar("target"), _fl(fr[1])), (Scalar("quat"), _fl(fr[2])),
                         (Scalar("fov"), Scalar(_ftext(fr[3])))], flow=True) for fr in c.frames]
            f.append((Scalar("frames"), Seq(rows, flow=not rows)))
        cams.append(Map(f))
    items.append((Scalar("cameras"), Seq(cams, flow=not cams)))
    return yamlish.emit(Map(items), head)


def _num(v: int, text: str) -> str:
    """A number for a message: in decimal, or its text cut short past 64 bits (int -> str refuses over
    4,300 digits, and a hex number has no such limit)."""
    return str(v) if v.bit_length() <= 64 else repr(text.strip()[:16] + "...")


class _Y:
    """Typed reads from yamlish nodes with line-numbered errors."""

    def __init__(self, source):
        self.source = source

    def err(self, msg, node=None):
        return ParamError(msg, getattr(node, "line", None), getattr(node, "col", None), self.source)

    def get(self, m, key, what):
        from .yamlish import Map
        if not isinstance(m, Map):
            raise self.err(f"{what} must be a block of fields", m)
        v = m.get(key)
        if v is None:
            raise self.err(f"{what}: '{key}' is missing", m)
        return v

    def int(self, node, lo, hi, what):
        from .yamlish import Scalar
        if not isinstance(node, Scalar):
            raise self.err(f"{what} must be a whole number", node)
        try:
            v = int(node.text.strip(), 0)
        except ValueError:
            raise self.err(f"{what}: {node.text!r} is not a whole number", node) from None
        if not lo <= v <= hi:
            raise self.err(f"{what}: {_num(v, node.text)} is outside {lo}..{hi}", node)
        return v

    def f32(self, node, what):
        from .params import f32_bits
        from .yamlish import Scalar
        if not isinstance(node, Scalar):
            raise self.err(f"{what} must be a number", node)
        try:
            return f32_bits(node.text)
        except ValueError:
            raise self.err(f"{what}: {node.text!r} is not a 32-bit float", node) from None

    def seq(self, node, n, what):
        from .yamlish import Seq
        if not isinstance(node, Seq) or (n is not None and len(node.items) != n):
            raise self.err(f"{what} is a list of {n} values" if n is not None else f"{what} is a list", node)
        return node.items

    def fvec(self, node, n, what):
        return tuple(self.f32(x, what) for x in self.seq(node, n, what))

    def only(self, m, allowed, what):
        """Refuse a field this block does not have (a typo would otherwise be ignored silently)."""
        from .yamlish import Map
        if isinstance(m, Map):
            for k, _ in m.items:
                if k.text not in allowed:
                    raise self.err(f"{what}: unexpected field '{k.text}'", k)


_TRACK_KEYS = ("codec", "usage", "unk2", "unk3", "weight", "reference", "extremes", "keys")
_FLOAT_KEYS = ("codec", "unk", "offset", "scale", "keys")
_CAM3_KEYS = ("slot", "userdata", "fovtype", "aspect", "frames")
_CAM5_KEYS = ("slot", "userdata", "frame_num", "fovtype", "aspect", *TRACKS, "fov")


def _key_from(y: _Y, node, codec: int, vec: bool, what: str) -> tuple:
    spec = (VEC_FIELDS if vec else FLT_FIELDS)[codec]
    vals = y.seq(node, len(spec), f"{what} (codec {codec})")
    return tuple(y.f32(v, what) if bits == "f" else y.int(v, 0, (1 << bits) - 1, what) for v, bits in zip(vals, spec))


def _track_from(y: _Y, node, name: str, where: str):
    vec = name != "fov"
    what = f"{where} {name}"
    codec = y.int(y.get(node, "codec", what), 0, 255, f"{what} codec")
    y.only(node, _TRACK_KEYS if vec else _FLOAT_KEYS, what)
    if vec and codec not in VEC_KEY and codec not in VEC_KEYLESS:
        raise y.err(f"{what}: codec {codec} is not one the camera evaluator knows", node)
    if not vec and codec not in FLT_KEY and codec not in FLT_KEYLESS:
        raise y.err(f"{what}: float codec {codec} is not one the camera evaluator knows", node)
    knode = y.get(node, "keys", what)
    keyed = (VEC_KEY if vec else FLT_KEY).get(codec) is not None
    keys = [_key_from(y, k, codec, vec, f"{what} key") for k in y.seq(knode, None, f"{what} keys")] if keyed else []
    if not keyed and y.seq(knode, None, f"{what} keys"):
        raise y.err(f"{what}: codec {codec} holds no keys", knode)
    if keyed and not keys:
        raise y.err(f"{what}: a codec {codec} track needs at least one key", knode)
    if not vec:
        return FloatTrack(codec, y.int(y.get(node, "unk", what), 0, (1 << 24) - 1, f"{what} unk"),
                          y.f32(y.get(node, "offset", what), f"{what} offset"),
                          y.f32(y.get(node, "scale", what), f"{what} scale"), keys)
    ext = node.get("extremes")
    extremes = None
    if ext is not None:
        y.only(ext, ("scale", "offset"), f"{what} extremes")
        extremes = (y.fvec(y.get(ext, "scale", f"{what} extremes"), 4, f"{what} extremes scale")
                    + y.fvec(y.get(ext, "offset", f"{what} extremes"), 4, f"{what} extremes offset"))
    elif codec in BILINEAR:
        raise y.err(f"{what}: codec {codec} is decoded through extremes: give 'extremes: {{scale, offset}}'", node)
    return Track(codec, y.int(y.get(node, "usage", what), 0, 255, f"{what} usage"),
                 y.int(y.get(node, "unk2", what), 0, 255, f"{what} unk2"),
                 y.int(y.get(node, "unk3", what), 0, 255, f"{what} unk3"),
                 y.f32(y.get(node, "weight", what), f"{what} weight"),
                 y.fvec(y.get(node, "reference", what), 4, f"{what} reference"), extremes, keys)


def from_yaml(text: str, source: str | None = None) -> CameraList:
    from . import yamlish
    from .yamlish import Map, Scalar

    y = _Y(source)
    doc = yamlish.parse(text, source)
    tag = doc.get("riftstone") if isinstance(doc, Map) else None
    if not isinstance(tag, Scalar) or tag.text != TAG:
        raise ParamError(f"not a Riftstone camera list (expected 'riftstone: {TAG}')", 1, 1, source)
    y.only(doc, ("riftstone", "resource", "version", "camera_num", "cameras"), "the file")
    version = y.int(y.get(doc, "version", "the file"), 0, 0xFFFF, "version")
    if version not in VERSIONS:
        raise y.err(f"version {version}: camera lists are 3 (DDDA) or 5 (Dragon's Dogma Online)", doc.get("version"))
    num = y.int(y.get(doc, "camera_num", "the file"), 0, 0xFFFF, "camera_num")
    cl = CameraList(version, num)
    for cn in y.seq(y.get(doc, "cameras", "the file"), None, "cameras"):
        slot = y.int(y.get(cn, "slot", "a camera"), 0, num - 1, "slot")
        where = f"slot {slot}"
        if slot in cl.cameras:
            raise y.err(f"two cameras use {where}", cn)
        y.only(cn, _CAM3_KEYS if version == VERSION_DDDA else _CAM5_KEYS, where)
        ud_n = 4 if version == VERSION_DDDA else 8
        userdata = tuple(y.int(v, -(1 << 31), (1 << 31) - 1, f"{where} userdata")
                         for v in y.seq(y.get(cn, "userdata", where), ud_n, f"{where} userdata"))
        fovtype = y.int(y.get(cn, "fovtype", where), 0, 0xFFFFFFFF, f"{where} fovtype")
        aspect = y.f32(y.get(cn, "aspect", where), f"{where} aspect")
        if version == VERSION_DDDA:
            frames = []
            for k, fr in enumerate(y.seq(y.get(cn, "frames", where), None, f"{where} frames")):
                w = f"{where} frame {k}"
                y.only(fr, ("pos", "target", "quat", "fov"), w)
                frames.append((y.fvec(y.get(fr, "pos", w), 3, f"{w} pos"), y.fvec(y.get(fr, "target", w), 3, f"{w} target"),
                               y.fvec(y.get(fr, "quat", w), 4, f"{w} quat"), y.f32(y.get(fr, "fov", w), f"{w} fov")))
            cl.cameras[slot] = Camera3(userdata, fovtype, aspect, frames)
        else:
            frame_num = y.int(y.get(cn, "frame_num", where), 0, 0xFFFFFFFF, f"{where} frame_num")
            tracks = [_track_from(y, y.get(cn, n, where), n, where) for n in (*TRACKS, "fov")]
            cl.cameras[slot] = Camera5(userdata, frame_num, fovtype, aspect, *tracks)
    try:
        build(cl)
    except FormatError as e:
        raise ParamError(str(e), None, None, source) from None
    return cl


def yaml_to_bytes(text: str, source: str | None = None) -> bytes:
    return build(from_yaml(text, source))

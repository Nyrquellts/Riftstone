"""rFacialAnimation ``.fca``: the face curves of lip sync, byte-exact, every key decoded, with YAML.

Dragon's Dogma: Dark Arisen ships 10,133 distinct files, all ``etc\\lipsynch\\<n>`` (Dragon's Dogma
Online has no .fca). The loader (PS3 ``rFacialAnimation::load`` 0x0105B984, named, with the types of
``rFacialAnimation``; PC DDDA.exe 0x011398E0 and its per-track reader 0x01139770) reads a 60-byte
header, refuses any other magic or version, then for each of ``TrackNum`` tracks reads a key count, a
float and that many 36-byte keys, which it copies verbatim into an ``MtFCurve::DescKey`` array.

Header (``rFacialAnimation::Header``, names the engine's)::

  0x00 u32 Magic "FCA\\0"          0x04 u32 Version 0x77CEF6CC
  0x08 u32 TrackNum                0x0C u32 FrameNum
  0x10 s32 EyeJointNoRight         0x14 s32 EyeJointNoLeft
  0x18 bool FlgEnableExtendEye     0x19 3 padding bytes (kept as they are)
  0x1C s32[8] ExtendEyeTrackNo     (indexed by EYE_EXTEND_TRACK: left Y+, left Y-, left X+, left X-,
                                    right Y+, right Y-, right X+, right X-)

Track (``rFacialAnimation::TrackParam``; ``MtFCurve``'s properties are mKeys and mDefaultValue)::

  u32 key count   f32 default value   count x key

Key (``MtFCurve::DescKey``: the fields of ``MtFCurve::Key``, whose property names come from its PS3
createProperty 0x0112A418)::

  s32 frame   u32 interpolation   f32 value   f32 rtany   f32 ltany   f32 right   f32 left
  f32 rtanx   f32 ltanx

``rFacialAnimation::getValue`` (PS3 0x0105BE5C) finds the key at or before the frame and hands it and
the next key to ``MtFCurve::getValue`` (0x0112A008), which interpolates by the first key's
``interpolation``: 0 the track's default value, 1 step (the key's value), 2 linear, 3 Bezier
((rtanx, rtany) of the key and (ltanx, ltany) of the next are handle offsets from each key), 4 Hermite
(rtany / ltany scaled by the frame span); any other value gives a constant. Past the last key the
last key's value holds. ``right`` and ``left`` are not read there (meaning UNKNOWN; in every vanilla
key they equal ``value``).

Proved on all 10,133 files (``check_corpus --only fca``): parse -> build and the YAML round trip
reproduce every file byte-for-byte; 1,678,543 keys. Measured on every file: 14 tracks, eye joints
81 / 88, FlgEnableExtendEye false with extend tracks (17, 16, 19, 18, 12, 11, 14, 13), each track's
default 0.0, every key linear (2) with zero tangents, frames strictly ascending, values in 0..1.
Which face control each of the 14 tracks drives is set by the face's rFacialPattern (``.fcp``, not
decoded here): UNKNOWN.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .errors import FormatError, ParamError

MAGIC = b"FCA\0"
VERSION = 0x77CEF6CC
TAG = "fca/1"
_HEAD = struct.Struct("<4sIIIiiB3s8i")
HEADER_SIZE = _HEAD.size            # 0x3C
_TRACK = struct.Struct("<II")        # key count, default value (f32 bits)
_KEY = struct.Struct("<iI7I")        # frame, interpolation, 7 x f32 bits
KEY_SIZE = _KEY.size                # 36
KEY_FLOATS = ("value", "rtany", "ltany", "right", "left", "rtanx", "ltanx")
INTERPOLATION = {0: "default value", 1: "step", 2: "linear", 3: "bezier", 4: "hermite"}
EYE_EXTEND_TRACK = ("LEFT_Y_PLUS", "LEFT_Y_MINUS", "LEFT_X_PLUS", "LEFT_X_MINUS",
                    "RIGHT_Y_PLUS", "RIGHT_Y_MINUS", "RIGHT_X_PLUS", "RIGHT_X_MINUS")


@dataclass
class Key:
    frame: int
    interpolation: int = 2
    value: int = 0          # the seven floats are kept as their exact 32-bit patterns
    rtany: int = 0
    ltany: int = 0
    right: int = 0
    left: int = 0
    rtanx: int = 0
    ltanx: int = 0

    def floats(self) -> tuple:
        return tuple(getattr(self, n) for n in KEY_FLOATS)


@dataclass
class Track:
    default: int = 0        # mDefaultValue, f32 bits
    keys: list[Key] = field(default_factory=list)


@dataclass
class Fca:
    frame_num: int
    eye_joint_right: int = 81
    eye_joint_left: int = 88
    extend_eye: int = 0                  # FlgEnableExtendEye (a bool byte)
    extend_eye_tracks: tuple = (17, 16, 19, 18, 12, 11, 14, 13)
    tracks: list[Track] = field(default_factory=list)
    pad: bytes = b"\0\0\0"               # the 3 bytes after the bool
    version: int = VERSION


def parse(data: bytes) -> Fca:
    data = bytes(data)
    if data[:4] != MAGIC:
        raise FormatError("fca", f"not a facial animation (magic {data[:4]!r}, expected {MAGIC!r})", 0)
    if len(data) < HEADER_SIZE:
        raise FormatError("fca", f"the header needs {HEADER_SIZE} bytes, the file has {len(data)}", 0)
    (_, version, track_num, frame_num, eye_r, eye_l, flag, pad, *ext) = _HEAD.unpack_from(data, 0)
    if version != VERSION:
        raise FormatError("fca", f"version 0x{version:08x}; the game loads only 0x{VERSION:08x}", 4)
    if track_num > (len(data) - HEADER_SIZE) // _TRACK.size:
        raise FormatError("fca", f"{track_num} tracks cannot fit in {len(data)} bytes", 8)
    p = HEADER_SIZE
    tracks = []
    for t in range(track_num):
        if p + _TRACK.size > len(data):
            raise FormatError("fca", f"the file ends before track {t}", p)
        count, default = _TRACK.unpack_from(data, p)
        p += _TRACK.size
        if count == 0:
            raise FormatError("fca", f"track {t} has no keys (the game reads its last key)", p - 8)
        if count > (len(data) - p) // KEY_SIZE:
            raise FormatError("fca", f"track {t}: {count} keys run past the end of the file", p - 8)
        keys = [Key(*k) for k in _KEY.iter_unpack(data[p:p + count * KEY_SIZE])]
        p += count * KEY_SIZE
        tracks.append(Track(default, keys))
    if p != len(data):
        raise FormatError("fca", f"{len(data) - p} byte(s) after the last track", p)
    return Fca(frame_num, eye_r, eye_l, flag, tuple(ext), tracks, pad, version)


def build(f: Fca) -> bytes:
    if f.version != VERSION:
        raise FormatError("fca", f"version 0x{f.version:08x} is not the one the game loads")
    try:
        if len(f.pad) != 3:
            raise FormatError("fca", "the padding after FlgEnableExtendEye is 3 bytes")
        if len(f.extend_eye_tracks) != 8:
            raise FormatError("fca", "ExtendEyeTrackNo holds 8 track numbers")
        out = bytearray(_HEAD.pack(MAGIC, f.version, len(f.tracks), f.frame_num, f.eye_joint_right,
                                   f.eye_joint_left, f.extend_eye, bytes(f.pad), *f.extend_eye_tracks))
        for t, tr in enumerate(f.tracks):
            if not tr.keys:
                raise FormatError("fca", f"track {t} has no keys (the game reads its last key)")
            out += _TRACK.pack(len(tr.keys), tr.default)
            for k in tr.keys:
                out += _KEY.pack(k.frame, k.interpolation, *k.floats())
    except (struct.error, TypeError, ValueError, AttributeError) as e:
        raise FormatError("fca", f"a value does not fit its field ({e})") from None
    return bytes(out)


def f32(bits: int) -> float:
    return struct.unpack("<f", struct.pack("<I", bits & 0xFFFFFFFF))[0]


def value_at(track: Track, frame: float) -> float:
    """The track's value at a frame the way the engine picks keys (getKeyIndex: the last key at or
    before the frame, else the first; past the last key its value holds). Default, step and linear
    keys are exact; Bezier and Hermite keys (no vanilla file has one) are evaluated as linear here."""
    keys = track.keys
    if not keys:
        return f32(track.default)
    i = 0
    while i < len(keys) and keys[i].frame <= frame:
        i += 1
    i = max(i - 1, 0)
    if i + 1 >= len(keys):
        return f32(keys[-1].value)
    k0, k1 = keys[i], keys[i + 1]
    if k0.interpolation == 0:
        return f32(track.default)
    if k0.interpolation == 1:
        return f32(k0.value)
    span = k1.frame - k0.frame
    t = (frame - k0.frame) / span if span else 0.0
    return f32(k0.value) + (f32(k1.value) - f32(k0.value)) * t


def info(f: Fca) -> str:
    keys = sum(len(t.keys) for t in f.tracks)
    kinds = sorted({k.interpolation for t in f.tracks for k in t.keys})
    how = ", ".join(INTERPOLATION.get(i, f"interpolation {i}") for i in kinds) or "none"
    return (f"rFacialAnimation: {len(f.tracks)} tracks, {f.frame_num} frames, {keys} keys ({how}); "
            f"eye joints {f.eye_joint_right} / {f.eye_joint_left}"
            + (", extended eye tracks on" if f.extend_eye else ""))


# -- YAML ------------------------------------------------------------------------------------------
def _ft(bits: int) -> str:
    from .params import f32_bits_text
    return f32_bits_text(bits)


def _num(v: int, text: str) -> str:
    """A number for a message: in decimal, or its text cut short past 64 bits (int -> str refuses over
    4,300 digits, and a hex number has no such limit)."""
    return str(v) if v.bit_length() <= 64 else repr(text.strip()[:16] + "...")


def to_yaml(f: Fca, name: str | None = None) -> str:
    from . import yamlish
    from .yamlish import Map, Scalar, Seq

    shown = " -- " + "".join(c if c.isprintable() else " " for c in name) if name else ""   # stays in its comment
    head = ["Riftstone facial animation (.fca, rFacialAnimation: lip sync)" + shown,
            f"{len(f.tracks)} tracks x keys over {f.frame_num} frames. A key is MtFCurve's: frame, interpolation",
            "(0 track default, 1 step, 2 linear, 3 bezier with the tangents, 4 hermite), value, tangents,",
            "right/left (UNKNOWN; they equal value in the game's files). A track needs at least one key;",
            "keys stay in frame order. Rebuilds byte-for-byte when untouched."]
    items = [(Scalar("riftstone"), Scalar(TAG))]
    if name:
        items.append((Scalar("resource"), Scalar(name, "double")))
    items += [(Scalar("FrameNum"), Scalar(str(f.frame_num))),
              (Scalar("EyeJointNoRight"), Scalar(str(f.eye_joint_right))),
              (Scalar("EyeJointNoLeft"), Scalar(str(f.eye_joint_left))),
              (Scalar("FlgEnableExtendEye"), Scalar(str(f.extend_eye))),
              (Scalar("ExtendEyeTrackNo"), Seq([Scalar(str(v)) for v in f.extend_eye_tracks], flow=True,
                                               comment="L Y+, L Y-, L X+, L X-, R Y+, R Y-, R X+, R X-"))]
    if f.pad != b"\0\0\0":
        items.append((Scalar("pad"), Scalar(f.pad.hex(), "double")))
    tracks = []
    for i, t in enumerate(f.tracks):
        keys = [Map([(Scalar("frame"), Scalar(str(k.frame))), (Scalar("interpolation"), Scalar(str(k.interpolation))),
                     *((Scalar(n), Scalar(_ft(getattr(k, n)))) for n in KEY_FLOATS)], flow=True) for k in t.keys]
        tracks.append(Map([(Scalar("track"), Scalar(str(i))), (Scalar("default"), Scalar(_ft(t.default))),
                           (Scalar("keys"), Seq(keys))]))
    items.append((Scalar("tracks"), Seq(tracks, flow=not tracks)))
    return yamlish.emit(Map(items), head)


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

    def only(self, m, allowed, what):
        from .yamlish import Map
        if isinstance(m, Map):
            for k, _ in m.items:
                if k.text not in allowed:
                    raise self.err(f"{what}: unexpected field '{k.text}'", k)


_S32 = (-(1 << 31), (1 << 31) - 1)
_U32 = (0, 0xFFFFFFFF)
_TOP = ("riftstone", "resource", "FrameNum", "EyeJointNoRight", "EyeJointNoLeft", "FlgEnableExtendEye",
        "ExtendEyeTrackNo", "pad", "tracks")
_KEY_FIELDS = ("frame", "interpolation", *KEY_FLOATS)


def from_yaml(text: str, source: str | None = None) -> Fca:
    from . import yamlish
    from .yamlish import Map, Scalar

    y = _Y(source)
    doc = yamlish.parse(text, source)
    tag = doc.get("riftstone") if isinstance(doc, Map) else None
    if not isinstance(tag, Scalar) or tag.text != TAG:
        raise ParamError(f"not a Riftstone facial animation (expected 'riftstone: {TAG}')", 1, 1, source)
    y.only(doc, _TOP, "the file")
    f = Fca(y.int(y.get(doc, "FrameNum", "the file"), *_U32, "FrameNum"),
            y.int(y.get(doc, "EyeJointNoRight", "the file"), *_S32, "EyeJointNoRight"),
            y.int(y.get(doc, "EyeJointNoLeft", "the file"), *_S32, "EyeJointNoLeft"),
            y.int(y.get(doc, "FlgEnableExtendEye", "the file"), 0, 255, "FlgEnableExtendEye"),
            tuple(y.int(v, *_S32, "ExtendEyeTrackNo")
                  for v in y.seq(y.get(doc, "ExtendEyeTrackNo", "the file"), 8, "ExtendEyeTrackNo")))
    pad = doc.get("pad")
    if pad is not None:
        try:
            f.pad = bytes.fromhex(pad.text)
        except (AttributeError, ValueError):
            raise y.err("pad must be hex bytes", pad) from None
        if len(f.pad) != 3:
            raise y.err("pad is 3 bytes (6 hex digits)", pad)
    for i, tn in enumerate(y.seq(y.get(doc, "tracks", "the file"), None, "tracks")):
        where = f"track {i}"
        y.only(tn, ("track", "default", "keys"), where)
        num = tn.get("track") if isinstance(tn, Map) else None
        if num is not None and y.int(num, 0, _U32[1], f"{where} number") != i:
            raise y.err(f"{where}: tracks are numbered in order (this one is {i})", num)
        tr = Track(y.f32(y.get(tn, "default", where), f"{where} default"))
        for k, kn in enumerate(y.seq(y.get(tn, "keys", where), None, f"{where} keys")):
            w = f"{where} key {k}"
            y.only(kn, _KEY_FIELDS, w)
            tr.keys.append(Key(y.int(y.get(kn, "frame", w), *_S32, f"{w} frame"),
                               y.int(y.get(kn, "interpolation", w), *_U32, f"{w} interpolation"),
                               *(y.f32(y.get(kn, n, w), f"{w} {n}") for n in KEY_FLOATS)))
        if not tr.keys:
            raise y.err(f"{where} needs at least one key (the game reads its last key)", tn)
        f.tracks.append(tr)
    try:
        build(f)
    except FormatError as e:
        raise ParamError(str(e), None, None, source) from None
    return f


def yaml_to_bytes(text: str, source: str | None = None) -> bytes:
    return build(from_yaml(text, source))

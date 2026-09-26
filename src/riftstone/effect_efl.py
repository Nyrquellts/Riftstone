"""rEffectList ``.efl``: the effect definitions themselves -- emitters (generators), particles, their
motion and lifetime, joints -- and the textures, models and sounds they use.

File (little-endian): a 0x30-byte header, then ``mParamBuffSize`` bytes the engine copies verbatim
into memory (``rEffectList::load``: PS3 0x010291F4, DDDA.exe 0x01147790) and walks by offset
(``setupResourceInfo`` / ``ResourceInfo::create*Resources``):

  0x00 "EFL\\0"  0x04 version (DDDA 0x20110318, DDO 0x20120306)  0x08 mParamBuffSize (= file size - 0x30)
  0x0C f32 mBaseFps  0x10 u16 mListNum  0x12 u16 mJointNum
  0x14 u32 bits 0-3 mUnitGeneratorType, 4-7 mUnitMoveType, 8-11 mJointShare (the loader keeps bits 0-11)
  0x18, 0x1C u32 (0 in every file; not read)
  0x20 mUnitGeneratorParamOffset  0x24 mUnitMoveParamOffset  0x28 mUnitJointParamOffset  0x2C mUnitParamOffset

Param buffer (offsets are from its start):
  mListNum x 16-byte entries, one per effect unit:
     u32 JointNo:8 | GeneratorOffset:24        (JointNo < mJointNum in every file)
     u32 ParticleType:8 | ParticleOffset:24
     u32 LifeUnk:4 | LifeType:4 | LifeOffset:24  (offset 0 = none)
     u32 MoveType:4 | MoveUnk:4 | MoveOffset:24
  mJointNum x u32 joint offsets, zero-padded to a multiple of 16
  then the structures those offsets name.  Each one's own sub-blocks (keyframes, culling and
  collision parameters, name strings) follow it and are found through u16 offsets relative to its
  start, so every structure owns the bytes up to the next referenced offset: a *region*.

Riftstone keeps each region's bytes and rebuilds the tables from the region order, so parse -> build
is byte-exact by construction and regions can be edited in place.  On top of the bytes it decodes the
head of each generator and particle into named fields (``region_fields``) and lists every resource
the effect loads by name (``resources``).

Names and layouts.  Header, EFL_GENERATOR (0xC0 bytes) and the particle heads EFL_PARTICLE_COMMON
(0x40), _DRAW_COMMON (0x50), _PAT_COMMON (0x60), _PRIM_COMMON (0x170: 3 texture paths + an anim path)
and _CUSTOM (0x200) are the PS3 build's (uEfCam.cpp / uEffectExt.cpp; the
rEffectList code itself has no debug info).  Bit fields are packed from the least significant bit on
PC (proved on the header: the 63 DDDA / 51 DDO files whose bits 0-3 are non-zero are exactly the ones
with a unit generator).  Kind names come from the engine's code: ``cParticleGenerator::initParticle``
dispatches the particle type to initParticle<Kind> and the move/life types to initParticleMove<Kind> /
initParticleLife<Kind>; ``ResourceInfo::createParticleResources`` says which type holds which path
(rTexture, rEffectAnim, rModel, rGrassWind), ``createGeneratorResources`` names the generator's
rEffectStrip / rVibration / rSoundRequest strings, ``createMoveResources`` the move's collision
block (two rEffectList strings) and a PathStrip's rEffectStrip path, and ``setupResourceInfo`` the
unit parameter's serial rEffectList.  The entry-word field names are descriptive (the struct is not in
the PS3 build).

Proof (2026-09-25, every distinct file): parse -> build and the YAML round trip are byte-exact on DDDA
4,514/4,514 and DDO 5,754/5,754 (tests/test_effect.py re-checks bytes on all, YAML on every 20th).
Share of all .efl bytes held in named fields (header, tables, generator and particle heads):
DDDA 65.0% (22,725,744 of 34,958,096), DDO 60.6% (29,295,296 of 48,314,408) -- ``coverage``.  The rest
stays opaque bytes: particle bodies past the heads (type-specific fields, keyframe blocks), EFL_JOINT
(0x70, no member names), life frames, move bodies, unit parameters.

UNKNOWN: EFL_JOINT, EFL_LIFE_FRAME and EFL_MOVE_* members; LifeUnk (bits 0-3 of entry word 2: 1 or 2
in the games) and MoveUnk (bits 4-7 of word 3: 0 or 1); particle type 25 (DDDA 5, DDO 38 uses; the PS3
build has no kind for it) and life types 7/8 (DDO only).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .errors import FormatError, ParamError

MAGIC = b"EFL\0"
VERSION_DDDA = 0x20110318
VERSION_DDO = 0x20120306
VERSIONS = (VERSION_DDDA, VERSION_DDO)
HEADER = 0x30
TAG = "efl/1"
_U32 = struct.Struct("<I")
_HEAD = struct.Struct("<4sIIIHHIII4I")
MAX_OFFSET = 0xFFFFFF        # 24-bit offsets in the entry table

PARTICLE_KINDS = {
    0: "Billboard", 1: "Polyline", 2: "Polygon", 3: "Texline", 4: "Line", 5: "Model", 6: "PrimModel",
    7: "LensFlare", 8: "MassBillboard", 9: "Filter", 10: "Light", 11: "Hit", 12: "Polyline", 13: "Texline",
    14: "Line", 15: "PolygonStrip", 16: "Custom", 17: "ClothPolygon", 18: "Adhesion", 19: "BillboardStrip",
    20: "SizeBillboard", 21: "LightShaft", 22: "Point", 23: "AxisPolygon", 24: "Force",
}
MOVE_KINDS = {0: "None", 1: "Add", 2: "Mul", 3: "PathStrip", 4: "PathChain", 5: "PathKeyframe", 6: "PathLine",
              7: "Custom", 8: "Mul+Spin", 9: "AddFast", 10: "MulFast", 11: "MulFast+Spin"}
LIFE_KINDS = {1: "Frame", 2: "Frame", 3: "Keyframe", 4: "Keyframe", 5: "Hideframe", 6: "Hideframe"}
# particle types whose struct derives from EFL_PARTICLE_PRIM_COMMON (createParticleResources loads
# their three textures and anim path); 16 is EFL_PARTICLE_CUSTOM
PRIM_TYPES = frozenset({0, 1, 2, 3, 6, 12, 13, 15, 16, 17, 18, 19, 20, 21, 23})


def particle_kind(t: int) -> str:
    return PARTICLE_KINDS.get(t, f"type {t} (UNKNOWN)")


def move_kind(t: int) -> str:
    return MOVE_KINDS.get(t, f"type {t} (UNKNOWN)")


def life_kind(t: int) -> str:
    return LIFE_KINDS.get(t, "constant" if t == 0 else f"type {t} (UNKNOWN)")


# -- head schemas ------------------------------------------------------------------------------------
# An item is (name, type) or (name, type, count).  Types: u32 x32 s32 f32 u16 / bits (a u32 of named
# bit fields, packed from bit 0: the third element lists (name, width)) / rangef {s, r: f32} /
# rangeu16 {s, r: 16-bit} / range {s: s32, r: u32} / point {x, y: s32} / ease {p1, p2: f32} /
# char64 (a 64-byte path) / raw<n> (n bytes kept as they are).
def _bits(*fields):
    return ("", "bits", fields)


COMMON = (
    _bits(("DrawMode", 8), ("EntryType", 8), ("CullingFlag", 8), ("BlendState", 8)),
    ("ParticleOptionFlag", "x32"), ("LightGroupFlag", "x32"), ("Zofs", "s32"), ("OtDepthBias", "f32"),
    _bits(("FixOtDepth", 16), ("VolumeBlendRate", 8), ("VolumeBlendRateRange", 8)),
    _bits(("ColorCorrectType", 4), ("GpuParticleType", 4), ("ShadeLightType", 4), ("ShaderType", 4),
          ("SynchroUnitFlag", 1), ("OtDepthBiasFlag", 1), ("SynchroUnitLimitFlag", 1), ("PCommon051a", 5),
          ("RotOptionFlag", 8)),
    ("ScaleAddCoef", "f32"), ("Intensity", "rangef"), ("Scale", "rangef"), ("ScaleAdd", "rangef"),
    _bits(("KeyframeIntensityParamOffset", 16), ("KeyframeScaleParamOffset", 16)),
    _bits(("LevelCorrectionParamOffset", 16), ("CullingParamOffset", 16)),
)
DRAW = (
    _bits(("ColorFlag", 8), ("KeyframePatSpeedParamFlag", 1), ("PDrawCommon0741", 7), ("KeyframeColorParamOffset", 16)),
    _bits(("KeyframePatNoParamOffset", 16), ("PDrawCommon1646", 16)),
    ("Color", "x32", 2),
)
PAT = (
    _bits(("AnimFlag", 16), ("SeqNoMin", 8), ("SeqNoRange", 8)),
    _bits(("PatNoMin", 16), ("PatNoRange", 16)),
    ("PatSpeed", "f32"), ("PatNoMax", "f32"),
)
PRIM = (
    ("PatCenter", "point"),
    _bits(("FresnelFactorFix", 16), ("FresnelBiasFix", 16)),
    ("FresnelExponent", "f32"), ("TexturePath", "char64", 3), ("AnimPath", "char64"),
)
CUSTOM = (
    _bits(("CustomType", 8), ("PCustom08161", 8), ("PCustom16162", 16)),
    ("CustomFlag", "x32"), ("CustomCurve", "ease"), ("CustomParamS", "s32", 8), ("CustomParamF", "f32", 8),
    ("CustomRangeParamS", "range", 4), ("CustomRangeParamF", "rangef", 4),
)
GENERATOR = (
    ("GroupFlag", "x32"), ("MaterialFlag", "x32"), ("GeneratorOptionFlag", "x32"),
    _bits(("ParticleNum", 16), ("UnitEndType", 8), ("RandomNoNum", 8)),
    ("RandomNo", "s32", 8),
    _bits(("AxisType", 4), ("LODType", 4), ("VibReqType", 8), ("VibPad", 8), ("VibCamera", 8)),
    ("VibPriority", "u32"),
    _bits(("VibListNo", 16), ("VibOptionFlag", 16)),
    _bits(("SeReqNo", 16), ("SeOptionFlag", 16)),
    ("ParticleScale", "rangef"), ("SetNum", "rangeu16"), ("LoopNum", "rangeu16"), ("SetFrame", "rangeu16"),
    ("IntervalFrame", "rangeu16"), ("SetFrameDist", "f32"), ("IntervalFrameDist", "f32"), ("Range", "rangef", 3),
    _bits(("RangeType", 8), ("RangeDirType", 8), ("RangeOptionFlag", 8), ("RangeDisperseType", 8)),
    _bits(("RangeStripType", 8), ("RangeStripFlag", 8), ("RangeStripPartsNo", 16)),
    ("RangeDirBlendRate", "rangef"), ("RangeScaleX", "rangef"), ("RangeScaleY", "rangef"), ("RangeScaleZ", "rangef"),
    _bits(("RangeDivideNum", 16), ("BoundaryFlag", 4), ("Generator04a2", 4), ("Generator08a3", 8)),
    ("RangeCollisionDist", "f32"), ("RangeCollCorrectDist", "f32"), ("RevivalFrame", "rangeu16"),
    ("SetNumCorrectCoef", "f32"),
    _bits(("KeyframeSetNumParamOffset", 16), ("KeyframeRangeParamOffset", 16)),
    _bits(("RangeStripPathOffset", 16), ("ExtVibrationPathOffset", 16)),
    _bits(("SoundRequestPathOffset", 16), ("BoundaryParamOffset", 16)),
)
# Paths createParticleResources reads from the non-PRIM kinds (descriptive names: the class loaded)
_EXTRA = {
    5: (("raw40", "raw16"), ("ModelPath", "char64")),                                     # rModel
    7: (("raw40", "raw96"), ("AnimPath", "char64"), ("TexturePath", "char64")),           # LensFlare
    8: (("raw40", "raw32"), ("AnimPath", "char64"), ("TexturePath", "char64")),           # MassBillboard
    25: (("raw40", "raw32"), ("AnimPath", "char64"), ("TexturePath", "char64")),
    24: (("GrassWindPath", "char64"),),                                                   # Force: rGrassWind
}
_FILTER_TEX = (("raw40", "raw96"), ("TexturePath", "char64"))   # Filter, used when byte 0x40 is 0

_SIZE = {"u32": 4, "x32": 4, "s32": 4, "f32": 4, "u16": 2, "bits": 4, "rangef": 8, "rangeu16": 4, "range": 8,
         "point": 8, "ease": 8, "char64": 64}


def _item_size(item) -> int:
    t = item[1]
    n = item[2] if len(item) > 2 and t != "bits" else 1
    if t.startswith("raw"):
        return int(t[3:])
    return _SIZE[t] * n


def schema_size(schema) -> int:
    return sum(_item_size(i) for i in schema)


def particle_schema(ptype: int, data: bytes) -> tuple:
    """The head schema Riftstone decodes for a particle of this type (the rest of the region stays
    bytes).  Needs the region's bytes for the Filter kind, whose texture slot depends on byte 0x40."""
    if ptype == 16:
        return COMMON + DRAW + PAT + PRIM + CUSTOM
    if ptype in PRIM_TYPES:
        return COMMON + DRAW + PAT + PRIM
    if ptype == 9 and len(data) > 0x40 and data[0x40] == 0:
        return COMMON + _FILTER_TEX
    return COMMON + _EXTRA.get(ptype, ())


def _one(t: str, data: bytes, p: int):
    """(value, size) of one item at p."""
    if t in ("u32", "x32", "f32"):
        return _U32.unpack_from(data, p)[0], 4
    if t == "s32":
        return struct.unpack_from("<i", data, p)[0], 4
    if t == "u16":
        return struct.unpack_from("<H", data, p)[0], 2
    if t in ("rangef", "ease"):
        a, b = struct.unpack_from("<II", data, p)
        return ({"s": a, "r": b} if t == "rangef" else {"p1": a, "p2": b}), 8
    if t == "rangeu16":
        a, b = struct.unpack_from("<HH", data, p)
        return {"s": a, "r": b}, 4
    if t == "range":
        a, b = struct.unpack_from("<iI", data, p)
        return {"s": a, "r": b}, 8
    if t == "point":
        a, b = struct.unpack_from("<ii", data, p)
        return {"x": a, "y": b}, 8
    if t == "char64":
        raw = data[p:p + 64]
        z = raw.find(b"\0")
        text = raw[:z] if z >= 0 else None
        if text is not None and not any(raw[z:]) and all(0x20 <= c < 0x7F for c in text):
            return text.decode("ascii"), 64
        return bytes(raw), 64                      # not a clean path: kept as bytes
    if t.startswith("raw"):
        n = int(t[3:])
        return bytes(data[p:p + n]), n
    raise AssertionError(t)  # pragma: no cover


def decode(schema, data: bytes, at: int = 0) -> dict:
    """Named fields of a head.  FormatError when the bytes are shorter than the schema."""
    if at + schema_size(schema) > len(data):
        raise FormatError("efl", f"a {schema_size(schema)}-byte head does not fit in {len(data) - at} bytes", at)
    out = {}
    p = at
    for item in schema:
        name, t = item[0], item[1]
        if t == "bits":
            w = _U32.unpack_from(data, p)[0]
            shift = 0
            for fname, width in item[2]:
                out[fname] = (w >> shift) & ((1 << width) - 1)
                shift += width
            p += 4
            continue
        if len(item) > 2:
            vals = []
            for _ in range(item[2]):
                v, n = _one(t, data, p)
                vals.append(v)
                p += n
            out[name] = vals
        else:
            out[name], n = _one(t, data, p)
            p += n
    return out


def _enc_one(t: str, v, where: str) -> bytes:
    try:
        if t in ("u32", "x32", "f32"):
            return _U32.pack(v)
        if t == "s32":
            return struct.pack("<i", v)
        if t == "u16":
            return struct.pack("<H", v)
        if t in ("rangef", "ease"):
            k = ("s", "r") if t == "rangef" else ("p1", "p2")
            return struct.pack("<II", v[k[0]], v[k[1]])
        if t == "rangeu16":
            return struct.pack("<HH", v["s"], v["r"])
        if t == "range":
            return struct.pack("<iI", v["s"], v["r"])
        if t == "point":
            return struct.pack("<ii", v["x"], v["y"])
        if t == "char64":
            if isinstance(v, (bytes, bytearray)):
                if len(v) != 64:
                    raise FormatError("efl", f"{where}: a raw path slot is 64 bytes, not {len(v)}")
                return bytes(v)
            b = v.encode("ascii")
            if len(b) > 63 or b"\0" in b:
                raise FormatError("efl", f"{where}: a path holds at most 63 characters and no NUL")
            return b + bytes(64 - len(b))
        if t.startswith("raw"):
            n = int(t[3:])
            if len(v) != n:
                raise FormatError("efl", f"{where}: {n} bytes expected, not {len(v)}")
            return bytes(v)
    except (struct.error, KeyError, TypeError):
        raise FormatError("efl", f"{where}: {v!r} does not fit a {t}") from None
    except UnicodeEncodeError:
        raise FormatError("efl", f"{where}: a path is plain ASCII") from None
    raise AssertionError(t)  # pragma: no cover


def encode(schema, values: dict) -> bytes:
    out = bytearray()
    for item in schema:
        name, t = item[0], item[1]
        if t == "bits":
            w = shift = 0
            for fname, width in item[2]:
                v = values.get(fname)
                if not isinstance(v, int) or not 0 <= v < (1 << width):
                    raise FormatError("efl", f"{fname} = {v!r} does not fit its {width} bit(s)")
                w |= v << shift
                shift += width
            out += _U32.pack(w)
            continue
        if name not in values:
            raise FormatError("efl", f"'{name}' is missing")
        v = values[name]
        if len(item) > 2:
            if not isinstance(v, list) or len(v) != item[2]:
                raise FormatError("efl", f"'{name}' holds {item[2]} values")
            for i, x in enumerate(v):
                out += _enc_one(t, x, f"{name}[{i}]")
        else:
            out += _enc_one(t, v, name)
    return bytes(out)


# -- model -------------------------------------------------------------------------------------------
@dataclass
class Entry:
    """One effect unit of the list: indices into Efl.regions (None where the offset is 0)."""
    joint: int                       # JointNo: index into Efl.joints
    generator: int | None
    particle_type: int
    particle: int | None
    life_type: int
    life_unk: int
    life: int | None
    move_type: int
    move_unk: int
    move: int | None


@dataclass
class Region:
    data: bytes
    roles: list = field(default_factory=list)     # ("generator"|"particle"|"life"|"move"|"joint"|"unit-*", type)


@dataclass
class Efl:
    version: int = VERSION_DDDA
    base_fps: int = 0x41F00000                      # f32 bits (30.0)
    flags: int = 0x100                              # the u32 at 0x14
    reserved: tuple = (0, 0)                        # the u32s at 0x18 and 0x1C
    entries: list[Entry] = field(default_factory=list)
    joints: list = field(default_factory=list)      # region index (None: offset 0) per joint
    unit: list = field(default_factory=lambda: [None, None, None, None])   # gen, move, joint, param
    joint_pad: bytes = b""
    regions: list[Region] = field(default_factory=list)

    @property
    def unit_generator_type(self) -> int:
        return self.flags & 0xF

    @property
    def unit_move_type(self) -> int:
        return (self.flags >> 4) & 0xF

    @property
    def joint_share(self) -> int:
        return (self.flags >> 8) & 0xF


UNIT_ROLES = ("unit-generator", "unit-move", "unit-joint", "unit-param")


def _tables_size(n_entries: int, n_joints: int) -> int:
    return n_entries * 16 + (n_joints * 4 + 15) // 16 * 16


def parse(data: bytes) -> Efl:
    data = bytes(data)
    if data[:4] != MAGIC:
        raise FormatError("efl", f"not an .efl file (magic {data[:4]!r}, expected {MAGIC!r})", 0)
    if len(data) < HEADER:
        raise FormatError("efl", f"truncated header ({len(data)} bytes)", 0)
    (_, version, size, fps, ln, jn, flags, r18, r1c, *unit_offs) = _HEAD.unpack_from(data, 0)
    if version not in VERSIONS:
        raise FormatError("efl", f"version 0x{version:08x} is not one the games use (0x{VERSION_DDDA:08x} "
                                 f"Dark Arisen, 0x{VERSION_DDO:08x} Online)", 4)
    buf = data[HEADER:]
    if size != len(buf):
        raise FormatError("efl", f"mParamBuffSize {size} != {len(buf)} bytes after the header", 8)
    tab = _tables_size(ln, jn)
    if tab > len(buf):
        raise FormatError("efl", f"{ln} entries and {jn} joints need {tab} bytes; the buffer has {len(buf)}", 0x10)
    words = [struct.unpack_from("<4I", buf, i * 16) for i in range(ln)]
    joint_offs = list(struct.unpack_from(f"<{jn}I", buf, ln * 16))
    pad = buf[ln * 16 + jn * 4:tab]
    refs: dict[int, list] = {}

    def ref(off: int, role: str, typ, where: int) -> None:
        if off == 0:
            return
        if not tab <= off < len(buf):
            raise FormatError("efl", f"{role} offset 0x{off:x} is outside the data area "
                                     f"(0x{tab:x}..0x{len(buf):x})", HEADER + where)
        refs.setdefault(off, [])
        if (role, typ) not in refs[off]:
            refs[off].append((role, typ))

    for i, w in enumerate(words):
        at = i * 16
        ref(w[0] >> 8, "generator", None, at)
        ref(w[1] >> 8, "particle", w[1] & 0xFF, at + 4)
        ref(w[2] >> 8, "life", (w[2] >> 4) & 0xF, at + 8)
        ref(w[3] >> 8, "move", w[3] & 0xF, at + 12)
    for j, off in enumerate(joint_offs):
        ref(off, "joint", None, ln * 16 + j * 4)
    for k, off in enumerate(unit_offs):
        typ = {0: flags & 0xF, 1: (flags >> 4) & 0xF}.get(k)
        if off:
            ref(off, UNIT_ROLES[k], typ, 0x20 + 4 * k - HEADER)
    starts = sorted(refs)
    if len(buf) > tab and (not starts or starts[0] != tab):
        starts.insert(0, tab)                 # bytes nothing names (never in the games) stay a region
        refs.setdefault(tab, [])
    index = {off: k for k, off in enumerate(starts)}
    regions = [Region(buf[off:(starts[k + 1] if k + 1 < len(starts) else len(buf))], refs[off])
               for k, off in enumerate(starts)]

    def rix(off):
        return index[off] if off else None

    entries = [Entry(joint=w[0] & 0xFF, generator=rix(w[0] >> 8), particle_type=w[1] & 0xFF, particle=rix(w[1] >> 8),
                     life_type=(w[2] >> 4) & 0xF, life_unk=w[2] & 0xF, life=rix(w[2] >> 8),
                     move_type=w[3] & 0xF, move_unk=(w[3] >> 4) & 0xF, move=rix(w[3] >> 8)) for w in words]
    return Efl(version, fps, flags, (r18, r1c), entries, [rix(o) for o in joint_offs],
               [rix(o) for o in unit_offs], bytes(pad), regions)


def build(efl: Efl) -> bytes:
    if efl.version not in VERSIONS:
        raise FormatError("efl", f"version 0x{efl.version:08x} is not one the games use")
    ln, jn = len(efl.entries), len(efl.joints)
    if ln > 0xFFFF or jn > 0xFFFF:
        raise FormatError("efl", "at most 65535 entries and 65535 joints")
    tab = _tables_size(ln, jn)
    if len(efl.joint_pad) != tab - ln * 16 - jn * 4:
        raise FormatError("efl", f"the joint table pads to 16 bytes with {tab - ln * 16 - jn * 4} bytes, "
                                 f"not {len(efl.joint_pad)}")
    offsets = []
    p = tab
    for r in efl.regions:
        offsets.append(p)
        p += len(r.data)

    def off(i, what):
        if i is None:
            return 0
        if not isinstance(i, int) or not 0 <= i < len(efl.regions):
            raise FormatError("efl", f"{what} names region {i!r}; there are {len(efl.regions)}")
        if not efl.regions[i].data:
            raise FormatError("efl", f"{what} names region {i}, which is empty")
        return offsets[i]

    def word(low: int, i, what) -> int:
        o = off(i, what)
        if o > MAX_OFFSET:
            raise FormatError("efl", f"{what}: offset 0x{o:x} does not fit 24 bits")
        return (o << 8) | low

    out = bytearray()
    for n, e in enumerate(efl.entries):
        for name, v, hi in (("joint", e.joint, 0xFF), ("particle_type", e.particle_type, 0xFF),
                            ("life_type", e.life_type, 0xF), ("life_unk", e.life_unk, 0xF),
                            ("move_type", e.move_type, 0xF), ("move_unk", e.move_unk, 0xF)):
            if not isinstance(v, int) or not 0 <= v <= hi:
                raise FormatError("efl", f"entry {n}: {name} = {v!r} is out of range (0..{hi})")
        out += struct.pack("<4I", word(e.joint, e.generator, f"entry {n} generator"),
                           word(e.particle_type, e.particle, f"entry {n} particle"),
                           word(e.life_unk | e.life_type << 4, e.life, f"entry {n} life"),
                           word(e.move_type | e.move_unk << 4, e.move, f"entry {n} move"))
    for j, i in enumerate(efl.joints):
        out += _U32.pack(off(i, f"joint {j}"))
    out += efl.joint_pad
    for r in efl.regions:
        out += r.data
    if len(efl.unit) != 4:
        raise FormatError("efl", "the unit block has 4 offsets (generator, move, joint, param)")
    try:
        head = _HEAD.pack(MAGIC, efl.version, len(out), efl.base_fps, ln, jn, efl.flags, *efl.reserved,
                          *(off(i, UNIT_ROLES[k]) for k, i in enumerate(efl.unit)))
    except struct.error as e:
        raise FormatError("efl", f"a header value does not fit: {e}") from None
    return head + bytes(out)


# -- reading it --------------------------------------------------------------------------------------
def region_schema(efl: Efl, i: int):
    """The named head Riftstone decodes for region i, or None (bytes only).  A region every entry uses
    the same way gets that head; one used in two ways (none in the games) is left as bytes."""
    r = efl.regions[i]
    kinds = {(role, typ) for role, typ in r.roles}
    if len(kinds) != 1:
        return None
    role, typ = next(iter(kinds))
    if role in ("generator", "unit-generator"):
        schema = GENERATOR
    elif role == "particle":
        schema = particle_schema(typ, r.data)
    else:
        return None
    return schema if schema_size(schema) <= len(r.data) else None


def region_fields(efl: Efl, i: int) -> dict | None:
    s = region_schema(efl, i)
    return decode(s, efl.regions[i].data) if s else None


def set_region_fields(efl: Efl, i: int, values: dict) -> None:
    """Write named head fields back into region i (the bytes after the head are kept)."""
    s = region_schema(efl, i)
    if s is None:
        raise FormatError("efl", f"region {i} has no named head")
    cur = decode(s, efl.regions[i].data)
    cur.update(values)
    head = encode(s, cur)
    efl.regions[i].data = head + efl.regions[i].data[len(head):]


def _cstr(data: bytes, at: int) -> str | None:
    if not 0 <= at < len(data):
        return None
    z = data.find(b"\0", at)
    if z < 0:
        return None
    return data[at:z].decode("latin-1")


def resources(efl: Efl) -> list[tuple[int, str, str, str]]:
    """Every resource the effect loads by name: (region, slot, class, path), in region order.
    Empty slots are left out."""
    out = []
    for i, r in enumerate(efl.regions):
        d = r.data
        for role, typ in r.roles:
            if role == "particle":
                f = region_fields(efl, i) or {}
                for k, cls in (("TexturePath", "rTexture"), ("AnimPath", "rEffectAnim"), ("ModelPath", "rModel"),
                               ("GrassWindPath", "rGrassWind")):
                    v = f.get(k)
                    for n, p in enumerate(v if isinstance(v, list) else [v]):
                        if isinstance(p, str) and p:
                            out.append((i, f"{k}[{n}]" if isinstance(v, list) else k, cls, p))
            elif role in ("generator", "unit-generator") and len(d) >= 0xC0:
                for at, slot, cls in ((0xB8, "RangeStripPath", "rEffectStrip"),
                                      (0xBA, "ExtVibrationPath", "rVibration"),
                                      (0xBC, "SoundRequestPath", "rSoundRequest")):
                    rel = struct.unpack_from("<H", d, at)[0]
                    p = _cstr(d, rel) if rel else None
                    if p:
                        out.append((i, slot, cls, p))
            elif role in ("move", "unit-move") and len(d) >= 8:
                rel = struct.unpack_from("<H", d, 6)[0]           # the move's collision block
                if rel and rel + 0x20 <= len(d):
                    for k in (0x1C, 0x1E):
                        sub = struct.unpack_from("<H", d, rel + k)[0]
                        p = _cstr(d, rel + sub) if sub else None
                        if p:
                            out.append((i, f"CollEffect@{k:#x}", "rEffectList", p))
                if typ == 3 and len(d) >= 0xC0:                   # PathStrip
                    p = _cstr(d[:0xC0], 0x80)
                    if p:
                        out.append((i, "StripPath", "rEffectStrip", p))
            elif role == "unit-param" and len(d) >= 0xC:
                rel = struct.unpack_from("<H", d, 0xA)[0]
                p = _cstr(d, rel) if rel else None
                if p:
                    out.append((i, "SerialEffect", "rEffectList", p))
    return out


def coverage(efl: Efl) -> dict:
    """Bytes held in named fields (header, tables, decoded heads minus raw gaps) vs kept as bytes."""
    raw = build(efl)
    named = HEADER + _tables_size(len(efl.entries), len(efl.joints))
    for i in range(len(efl.regions)):
        s = region_schema(efl, i)
        if s:
            named += schema_size(s) - sum(_item_size(it) for it in s if it[1].startswith("raw"))
    return {"bytes": len(raw), "named": named, "opaque": len(raw) - named}


def info(efl: Efl, limit: int = 16) -> str:
    game = "Dark Arisen" if efl.version == VERSION_DDDA else "Online"
    cov = coverage(efl)
    lines = [f"rEffectList 0x{efl.version:08x} ({game}): {len(efl.entries)} units, {len(efl.joints)} joints, "
             f"{len(efl.regions)} structures, base fps {struct.unpack('<f', _U32.pack(efl.base_fps))[0]:g}"
             + (f", unit generator type {efl.unit_generator_type}" if efl.unit_generator_type else ""),
             f"named fields hold {cov['named']} of {cov['bytes']} bytes ({cov['named'] / max(cov['bytes'], 1):.0%})"]
    for n, e in enumerate(efl.entries[:limit]):
        lines.append(f"  unit {n:<3} joint {e.joint:<3} {particle_kind(e.particle_type):<15} "
                     f"move {move_kind(e.move_type):<12} life {life_kind(e.life_type) if e.life is not None else '-'}")
    if len(efl.entries) > limit:
        lines.append(f"  ... {len(efl.entries) - limit} more units")
    res = resources(efl)
    if res:
        lines.append("resources:")
        seen = set()
        for _, slot, cls, p in res:
            if (cls, p) not in seen:
                seen.add((cls, p))
                lines.append(f"  {cls:<14} {p}")
    return "\n".join(lines)


# -- YAML --------------------------------------------------------------------------------------------
_HEX_LINE = 32


def _hex_node(b: bytes):
    from .yamlish import Scalar, Seq

    return Seq([Scalar(b[i:i + _HEX_LINE].hex()) for i in range(0, len(b), _HEX_LINE)])


def _val_node(t: str, v):
    from .params import f32_bits_text
    from .yamlish import Map, Scalar

    if t == "f32":
        return Scalar(f32_bits_text(v))
    if t == "x32":
        return Scalar(f"0x{v:08x}")
    if t in ("u32", "s32", "u16"):
        return Scalar(str(v))
    if t in ("rangef", "ease"):
        return Map([(Scalar(k), Scalar(f32_bits_text(x))) for k, x in v.items()], flow=True)
    if t in ("rangeu16", "range", "point"):
        return Map([(Scalar(k), Scalar(str(x))) for k, x in v.items()], flow=True)
    if t == "char64":
        if isinstance(v, bytes):
            return Map([(Scalar("hex"), Scalar(v.hex()))], flow=True)
        return Scalar(v, "double")
    if t.startswith("raw"):
        return Scalar(v.hex(), "double")
    raise AssertionError(t)  # pragma: no cover


def _fields_node(schema, values: dict):
    from .yamlish import Map, Scalar, Seq

    items = []
    for item in schema:
        name, t = item[0], item[1]
        if t == "bits":
            for fname, _w in item[2]:
                items.append((Scalar(fname), Scalar(str(values[fname]))))
        elif len(item) > 2:
            items.append((Scalar(name), Seq([_val_node(t, x) for x in values[name]], flow=t not in ("char64",))))
        else:
            items.append((Scalar(name), _val_node(t, values[name])))
    return Map(items)


def _role_text(role: str, typ) -> str:
    if role == "particle":
        return f"particle {particle_kind(typ)}"
    if role.endswith("move"):
        return f"{role} {move_kind(typ)}"
    if role == "life":
        return f"life {life_kind(typ)}"
    return role


def to_yaml(efl: Efl, name: str | None = None) -> str:
    from . import yamlish
    from .params import f32_bits_text
    from .yamlish import Map, Scalar, Seq

    game = "Dark Arisen" if efl.version == VERSION_DDDA else "Online"
    head = ["Riftstone effect list (.efl)" + (f" -- {name}" if name else ""),
            f"version 0x{efl.version:08x} ({game}). units: one emitter each -- its joint, and the structures",
            "(regions, by number) holding its generator, particle, life and move. Regions keep their bytes;",
            "generator and particle heads are shown as named fields (textures: TexturePath / AnimPath /",
            "ModelPath), the rest of a region is 'tail' hex. Offsets are rebuilt from the region order.",
            "Rebuilds byte-for-byte when untouched."]
    items = [(Scalar("riftstone"), Scalar(TAG))]
    if name:
        items.append((Scalar("resource"), Scalar(name, "double")))
    items += [(Scalar("version"), Scalar(f"0x{efl.version:08x}")),
              (Scalar("mBaseFps"), Scalar(f32_bits_text(efl.base_fps))),
              (Scalar("flags"), Scalar(f"0x{efl.flags:08x}",
                                       comment=f"unit generator {efl.unit_generator_type}, unit move "
                                               f"{efl.unit_move_type}, joint share {efl.joint_share}"))]
    if efl.reserved != (0, 0):
        items.append((Scalar("reserved"), Seq([Scalar(f"0x{x:08x}") for x in efl.reserved], flow=True)))
    if any(efl.joint_pad):
        items.append((Scalar("joint_pad"), Scalar(efl.joint_pad.hex(), "double")))
    if any(x is not None for x in efl.unit):
        items.append((Scalar("unit"), Map([(Scalar(k), Scalar("null" if v is None else str(v)))
                                           for k, v in zip(("generator", "move", "joint", "param"), efl.unit)],
                                          flow=True)))

    def rnode(v):
        return Scalar("null" if v is None else str(v))

    ents = []
    for e in efl.entries:
        ents.append(Map([(Scalar(k), rnode(v) if k in ("generator", "particle", "life", "move") else Scalar(str(v)))
                         for k, v in (("joint", e.joint), ("generator", e.generator),
                                      ("particle_type", e.particle_type), ("particle", e.particle),
                                      ("life_type", e.life_type), ("life_unk", e.life_unk),
                                      ("life", e.life), ("move_type", e.move_type), ("move_unk", e.move_unk),
                                      ("move", e.move))], flow=True))
    items.append((Scalar("units"), Seq(ents)))
    items.append((Scalar("joints"), Seq([rnode(j) for j in efl.joints], flow=True)))
    regs = []
    for i, r in enumerate(efl.regions):
        role = ", ".join(_role_text(ro, t) for ro, t in r.roles) or "unreferenced"
        s = region_schema(efl, i)
        kv = [(Scalar("region"), Scalar(str(i), comment=f"{role}, {len(r.data)} bytes"))]
        if s:
            kv.append((Scalar("fields"), _fields_node(s, decode(s, r.data))))
            tail = r.data[schema_size(s):]
        else:
            tail = r.data
        if tail:
            kv.append((Scalar("tail"), _hex_node(tail)))
        regs.append(Map(kv))
    items.append((Scalar("regions"), Seq(regs)))
    return yamlish.emit(Map(items), head)


def _w(node, source):
    return (getattr(node, "line", None), getattr(node, "col", None), source)


def _yint(node, what, source, lo=0, hi=0xFFFFFFFF, allow_null=False):
    from .yamlish import Scalar

    if not isinstance(node, Scalar):
        raise ParamError(f"'{what}' must be a single value", *_w(node, source))
    t = node.text.strip()
    if allow_null and t in ("null", "~", ""):
        return None
    try:
        v = int(t, 0)
    except ValueError:
        raise ParamError(f"'{what}': {node.text!r} is not a whole number", *_w(node, source)) from None
    if not lo <= v <= hi:
        raise ParamError(f"'{what}': {v} is out of range ({lo}..{hi})", *_w(node, source))
    return v


def _yf32(node, what, source) -> int:
    from .params import f32_bits
    from .yamlish import Scalar

    if not isinstance(node, Scalar):
        raise ParamError(f"'{what}' must be a single number", *_w(node, source))
    try:
        return f32_bits(node.text)
    except (ValueError, OverflowError):
        raise ParamError(f"'{what}': {node.text!r} is not a 32-bit float", *_w(node, source)) from None


def _yhex(node, what, source) -> bytes:
    from .yamlish import Scalar, Seq

    parts = node.items if isinstance(node, Seq) else [node]
    out = bytearray()
    for p in parts:
        if not isinstance(p, Scalar):
            raise ParamError(f"'{what}' is hex text", *_w(p, source))
        try:
            out += bytes.fromhex(p.text)
        except ValueError:
            raise ParamError(f"'{what}': not pairs of hex digits", *_w(p, source)) from None
    return bytes(out)


def _yval(t, node, what, source):
    from .yamlish import Map, Scalar

    if t == "f32":
        return _yf32(node, what, source)
    if t in ("u32", "x32"):
        return _yint(node, what, source)
    if t == "s32":
        return _yint(node, what, source, -0x80000000, 0x7FFFFFFF)
    if t == "u16":
        return _yint(node, what, source, 0, 0xFFFF)
    if t in ("rangef", "ease", "rangeu16", "range", "point"):
        keys = {"rangef": ("s", "r"), "ease": ("p1", "p2"), "rangeu16": ("s", "r"), "range": ("s", "r"),
                "point": ("x", "y")}[t]
        if not isinstance(node, Map):
            raise ParamError(f"'{what}' is {{{keys[0]}: .., {keys[1]}: ..}}", *_w(node, source))
        out = {}
        for k in keys:
            sub = node.get(k)
            if sub is None:
                raise ParamError(f"'{what}': '{k}' is missing", *_w(node, source))
            if t in ("rangef", "ease"):
                out[k] = _yf32(sub, what, source)
            elif t == "rangeu16":
                out[k] = _yint(sub, what, source, 0, 0xFFFF)
            elif k in ("s", "x", "y"):
                out[k] = _yint(sub, what, source, -0x80000000, 0x7FFFFFFF)
            else:
                out[k] = _yint(sub, what, source)
        return out
    if t == "char64":
        if isinstance(node, Map):
            return _yhex(node.get("hex"), what, source)
        if not isinstance(node, Scalar):
            raise ParamError(f"'{what}' is a path", *_w(node, source))
        return node.text
    if t.startswith("raw"):
        return _yhex(node, what, source)
    raise AssertionError(t)  # pragma: no cover


def _yfields(node, schema, what, source) -> dict:
    from .yamlish import Map, Seq

    if not isinstance(node, Map):
        raise ParamError(f"{what}: 'fields' is a mapping", *_w(node, source))
    out = {}
    for item in schema:
        name, t = item[0], item[1]
        if t == "bits":
            for fname, width in item[2]:
                v = node.get(fname)
                if v is None:
                    raise ParamError(f"{what}: '{fname}' is missing", *_w(node, source))
                out[fname] = _yint(v, fname, source, 0, (1 << width) - 1)
            continue
        v = node.get(name)
        if v is None:
            raise ParamError(f"{what}: '{name}' is missing", *_w(node, source))
        if len(item) > 2:
            if not isinstance(v, Seq) or len(v.items) != item[2]:
                raise ParamError(f"{what}: '{name}' holds {item[2]} values", *_w(v, source))
            out[name] = [_yval(t, x, name, source) for x in v.items]
        else:
            out[name] = _yval(t, v, name, source)
    return out


def from_yaml(text: str, source: str | None = None) -> Efl:
    from . import yamlish
    from .yamlish import Map, Scalar, Seq

    doc = yamlish.parse(text, source)
    if not isinstance(doc, Map) or not isinstance(doc.get("riftstone"), Scalar) or doc.get("riftstone").text != TAG:
        raise ParamError(f"not a Riftstone effect list (expected 'riftstone: {TAG}')", 1, 1, source)
    version = _yint(doc.get("version") or doc, "version", source)
    if version not in VERSIONS:
        raise ParamError(f"version must be 0x{VERSION_DDDA:08x} or 0x{VERSION_DDO:08x}",
                         *_w(doc.get("version"), source))
    efl = Efl(version, _yf32(doc.get("mBaseFps") or doc, "mBaseFps", source),
              _yint(doc.get("flags") or doc, "flags", source))
    res = doc.get("reserved")
    if res is not None:
        if not isinstance(res, Seq) or len(res.items) != 2:
            raise ParamError("'reserved' is two numbers", *_w(res, source))
        efl.reserved = tuple(_yint(x, "reserved", source) for x in res.items)
    pad = doc.get("joint_pad")
    unit = doc.get("unit")
    if unit is not None:
        if not isinstance(unit, Map):
            raise ParamError("'unit' is {generator, move, joint, param}", *_w(unit, source))
        efl.unit = [_yint(unit.get(k) or unit, f"unit {k}", source, allow_null=True)
                    for k in ("generator", "move", "joint", "param")]
    units = doc.get("units")
    for n, u in enumerate(units.items if isinstance(units, Seq) else []):
        if not isinstance(u, Map):
            raise ParamError("each unit is a mapping", *_w(u, source))

        def g(k, hi=0xFF, null=False):
            v = u.get(k)
            if v is None:
                raise ParamError(f"unit {n}: '{k}' is missing", *_w(u, source))
            return _yint(v, k, source, 0, hi, allow_null=null)
        efl.entries.append(Entry(g("joint"), g("generator", 0xFFFFFF, True), g("particle_type"),
                                 g("particle", 0xFFFFFF, True), g("life_type", 0xF), g("life_unk", 0xF),
                                 g("life", 0xFFFFFF, True), g("move_type", 0xF), g("move_unk", 0xF),
                                 g("move", 0xFFFFFF, True)))
    joints = doc.get("joints")
    efl.joints = [_yint(j, "joint", source, 0, 0xFFFFFF, allow_null=True)
                  for j in (joints.items if isinstance(joints, Seq) else [])]
    need = _tables_size(len(efl.entries), len(efl.joints)) - len(efl.entries) * 16 - len(efl.joints) * 4
    efl.joint_pad = _yhex(pad, "joint_pad", source) if pad is not None else bytes(need)
    regs = doc.get("regions")
    nodes = regs.items if isinstance(regs, Seq) else []
    for n, e in enumerate(efl.entries):
        for what, i in (("generator", e.generator), ("particle", e.particle), ("life", e.life), ("move", e.move)):
            if i is not None and i >= len(nodes):
                raise ParamError(f"unit {n}: {what} names region {i}; there are {len(nodes)}",
                                 *_w(units.items[n], source))
    for what, i in [(f"joint {k}", j) for k, j in enumerate(efl.joints)] + list(zip(UNIT_ROLES, efl.unit)):
        if i is not None and i >= len(nodes):
            raise ParamError(f"{what} names region {i}; there are {len(nodes)}", *_w(regs, source))
    # roles come from the tables (they choose each region's head); fill them before decoding fields
    efl.regions = [Region(b"") for _ in nodes]
    for e in efl.entries:
        for role, i, t in (("generator", e.generator, None), ("particle", e.particle, e.particle_type),
                           ("life", e.life, e.life_type), ("move", e.move, e.move_type)):
            if i is not None and 0 <= i < len(efl.regions) and (role, t) not in efl.regions[i].roles:
                efl.regions[i].roles.append((role, t))
    for i in efl.joints:
        if i is not None and 0 <= i < len(efl.regions) and ("joint", None) not in efl.regions[i].roles:
            efl.regions[i].roles.append(("joint", None))
    for k, i in enumerate(efl.unit):
        t = {0: efl.unit_generator_type, 1: efl.unit_move_type}.get(k)
        if i is not None and 0 <= i < len(efl.regions) and (UNIT_ROLES[k], t) not in efl.regions[i].roles:
            efl.regions[i].roles.append((UNIT_ROLES[k], t))
    for i, rn in enumerate(nodes):
        if not isinstance(rn, Map):
            raise ParamError("each region is a mapping", *_w(rn, source))
        tail = _yhex(rn.get("tail"), "tail", source) if rn.get("tail") is not None else b""
        fn = rn.get("fields")
        if fn is None:
            efl.regions[i].data = tail
            continue
        # the head schema: for a Filter it depends on byte 0x40, which is the first raw byte after COMMON
        roles = {(ro, t) for ro, t in efl.regions[i].roles}
        if len(roles) != 1:
            raise ParamError(f"region {i}: named fields need a region used one way", *_w(rn, source))
        role, typ = next(iter(roles))
        if role in ("generator", "unit-generator"):
            schema = GENERATOR
        elif role == "particle":
            schema = COMMON + _FILTER_TEX if typ == 9 and isinstance(fn, Map) and fn.get("raw40") is not None \
                else particle_schema(typ, b"")
        else:
            raise ParamError(f"region {i}: a {role} has no named fields; give its bytes as 'tail'", *_w(rn, source))
        try:
            efl.regions[i].data = encode(schema, _yfields(fn, schema, f"region {i}", source)) + tail
        except FormatError as e:
            raise ParamError(f"region {i}: {e}", *_w(rn, source)) from None
    try:
        build(efl)
    except FormatError as e:
        raise ParamError(str(e), None, None, source) from None
    return efl


def yaml_to_bytes(text: str, source: str | None = None) -> bytes:
    return build(from_yaml(text, source))

"""rEffectProvider ``.epv``: which effects a character, weapon or skill spawns, and where.

An .epv is the link between an actor and its particle effects.  It holds a list of *effect indices*;
each index is a list of *elements*, and an element names up to eight rEffectList (``.efl``) files plus
one rEffect2D (``.e2d``) and says how to place them: joint (``mJointNo``), offset (``mPosition``),
direction, scale, colour, how the effect is set and ended, LOD.  Two tables follow: the *motion sync
list* (motion number + frame window -> index/element: the "which effect plays during which motion"
link) and the *event list* (event work number -> index/element).  Dragon's Dogma Online's job skills
point at .epv files (jobcustomNN.jcp), so this is also the skill -> effect link there.

Grammar (read from the loaders, byte-exact on every file of both games; all numbers little-endian):

  "epv\\0"  u32 version (DDDA 0, DDO 22)
  u32 mEffectIndexNum, then per index: u32 mEffectElementNum, then per element:
      [DDO: u32 mUnk00]  8 x NUL-terminated rEffectList path  1 x rEffect2D path
      u32 mResourceType  u32 mEffectType  EffectParam (fields below, in file order)
      [DDDA: 3 x EffectLODParam, 8 x u32 each]
  u32 mMotSyncListNum, then per entry: EffectMotSyncParam (DDDA 12 x 4 bytes, DDO 9 x 4)
  u32 mEventListNum,   then per entry: EffectEventParam (3 x u32)

Sources.  DDDA (version 0): ``rEffectProvider::load`` of the PS3 build (PPC, 0x00AD23BC) and
DDDA.exe (0x00CB6CF0) read exactly this, value by value; the field names and struct layouts come from
the PS3 build (``rEffectProvider::EffectParam``, ``EffectLODParam``, ``EffectMotSyncParam``,
``EffectEventParam``).  DDO (version 22): DDO.exe's loader (0x00A6D860, reader vtable slot 8 = u32,
0x24 = f32, 0x30 = 3 x f32, 0x38 = raw bytes, inline byte reads) -- no LOD block, one u32 read before
the names, one u32 inserted after ``mDefaultColorRate`` and one after ``mCustomFlag``, four bools
instead of eight, and ten new trailing fields.  DDO fields keep the DDDA names where the read order,
type and (shifted) struct offset match; the rest are ``mUnkXX`` after their DDO struct offset
(EffectParam-relative).  Several u32 values are narrowed by the engine on load (booleans become one
bit, ``mLoopFrame`` is kept only when 1..600, ``mAdhesionDivideMax`` only when <= 255, LOD values are
bit fields); the file holds whole u32s and Riftstone keeps them as stored.

Proof (2026-09-25, every distinct file): parse -> build and the YAML round trip are byte-exact on DDDA
599/599 and DDO 1,660/1,660 (15,866 + 22,695 elements, 8,960 + 7,855 motion-sync entries, 52 + 0
events).  tests/test_effect.py re-checks bytes on every file and YAML on every 20th.

UNKNOWN: which four of DDDA's eight EffectParam booleans DDO kept (``mUnk8C_0..3``, bits 0-3 of the
DDO word at +0x8C), and the meaning of the DDO-only fields.  ``mUnk00`` (DDO) is read before the names
and not stored; DDO.exe skips loading the named resources when it is above 0x3040 (measured values:
0, 2, 0x101, 0x200, 0x2020, 0x3000, 0x3020, 0x3035).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .errors import FormatError, ParamError

MAGIC = b"epv\0"
VERSION_DDDA = 0
VERSION_DDO = 22
VERSIONS = (VERSION_DDDA, VERSION_DDO)
TAG = "epv/1"
LIST_SLOTS = 8          # rEffectProvider::EFC_LIST_MAX
_U32 = struct.Struct("<I")

# Field types: u32 / x32 (u32 shown as hex) / s32 / f32 (kept as its 32-bit pattern) / v3 (3 x f32) /
# u8 / u32list (u32 count + count x u32) / names8 (8 paths) / name (1 path) / lod3 (3 x _LOD).
_LOD = (
    ("mDist", "u32"), ("mTraits", "u32"), ("mParticleVolume", "u32"), ("mFilterSampleVolume", "u32"),
    ("mLife", "u32"), ("mReflection", "u32"), ("mNormalMaskEnable", "u32"), ("mEnableVolumeBlend", "u32"),
)
_PLACE = (
    ("mResourceType", "u32"), ("mEffectType", "u32"), ("mSetType", "u32"), ("mEndType", "u32"),
    ("mGroupFlag", "x32"), ("mMaterialFlag", "x32"),
    ("mPosition", "v3"), ("mCamOfs", "v3"), ("mDirection", "v3"), ("mDirectionAmp", "v3"),
    ("mAxisType", "u32"), ("mOrder", "u32"), ("mParentNo", "u32"), ("mJointNo", "s32"),
    ("mPosType", "u32"), ("mRotType", "u32"), ("mDefaultColor", "x32"), ("mDefaultColorRate", "f32"),
)
ELEMENT = {
    VERSION_DDDA: (
        ("mpEffectList", "names8"), ("mpEffect2D", "name"), *_PLACE,
        ("mAlphaScale", "f32"), ("mLightPriority", "u32"),
        ("mScale", "f32"), ("mScaleAmp", "f32"), ("mDeltaTimeCoef", "f32"),
        ("mIncidenceAngleEnable", "u32"), ("mLandMaterialEnable", "u32"), ("mLandSetEnable", "u32"),
        ("mLandDirEnable", "u32"), ("mLandLengthScaleEnable", "u32"), ("mLandParentEnable", "u32"),
        ("mOMMaterialEnable", "u32"), ("mUseSystemLOD", "u32"),
        ("mZoneCorrectType", "u32"), ("mLoopFrame", "u32"), ("mE2DType", "u32"),
        ("mLandLength", "f32"), ("mCustomFlag", "x32"), ("mAdhesionDivideMax", "u32"),
        ("mLODParam", "lod3"),
    ),
    VERSION_DDO: (
        ("mUnk00", "x32"), ("mpEffectList", "names8"), ("mpEffect2D", "name"), *_PLACE,
        ("mUnk80", "u32"), ("mAlphaScale", "f32"), ("mLightPriority", "u32"),
        ("mScale", "f32"), ("mScaleAmp", "f32"), ("mDeltaTimeCoef", "f32"),
        ("mUnk8C_0", "u32"), ("mUnk8C_1", "u32"), ("mUnk8C_2", "u32"), ("mUnk8C_3", "u32"),
        ("mZoneCorrectType", "u32"), ("mLoopFrame", "u32"), ("mE2DType", "u32"),
        ("mLandLength", "f32"), ("mCustomFlag", "x32"), ("mUnk98", "x32"), ("mAdhesionDivideMax", "u32"),
        ("mUnkA0", "u32list"), ("mUnkA4", "x32"), ("mUnkA8", "u32"), ("mUnkAC", "u32"), ("mUnkB0", "f32"),
        ("mUnkB4", "u8"), ("mUnkB8", "f32"), ("mUnkC0", "v3"), ("mUnkD0", "u8"), ("mUnkD4", "f32"),
    ),
}
MOTSYNC = {
    VERSION_DDDA: (
        ("mMotionNo", "u32"), ("mBlendIndex", "u32"), ("mStartFrame", "f32"), ("mEndFrame", "f32"),
        ("mInterval", "f32"), ("mLeaveFlag", "u32"), ("mFreeSpeedFlag", "u32"), ("mFreeScaleFlag", "u32"),
        ("mFreeOffsetFlag", "u32"), ("mSetOnceFlag", "u32"), ("mEfcIndexNo", "u32"), ("mEfcElementNo", "u32"),
    ),
    VERSION_DDO: (
        ("mMotionNo", "u32"), ("mBlendIndex", "u32"), ("mStartFrame", "f32"), ("mEndFrame", "f32"),
        ("mInterval", "f32"), ("mLeaveFlag", "u32"), ("mFreeSpeedFlag", "u32"),
        ("mEfcIndexNo", "u32"), ("mEfcElementNo", "u32"),
    ),
}
EVENT = (("mEventWorkNo", "u32"), ("mEfcIndexNo", "u32"), ("mEfcElementNo", "u32"))

# The PS3 build's enums (rEffectProvider::EFC_TYPE, EFC_SET_TYPE, EFC_END_TYPE, POS_TYPE, ROT_TYPE,
# E2D_TYPE).  They are DDDA's; DDO uses values past their ends, so its YAML is not annotated.
_EFC_TYPE = (
    "DEFAULT DAMAGE EVENT BREAK ALWAYS MOTION FOOT_WALK FOOT_RUN SCR ENCHANT ST_ICE_BREAK ST_ICE_PRE_BREAK "
    "ST_THUNDER FATAL_DAMAGE ST_POISON ST_SLOW ST_BLIND HEAL_VITALITY HEAL_STAMINA ST_SILENCE ST_SLEEP ST_OIL "
    "ST_WET ST_CURSE ST_ENEMY ST_SEAL_SKILL HEAL_STATUS ST_FIRE ST_ICE ST_ICE_CRASH ST_STONE_CRASH ST_STONE "
    "ST_ATK_DOWN ST_DEF_DOWN ST_MAG_POW_DOWN ST_MAG_DEF_DOWN ST_ATK_UP ST_DEF_UP ST_MAG_POW_UP ST_MAG_DEF_UP "
    "ST_RUNNING_UP ST_HP_RECOVERY ST_STAMINA_UP ST_RESISTANT_DEFENSE ST_LUCK ST_ECONOMIC_FORTUNE ST_INVISIBILITY "
    "ST_QUIET MOTSEQ DAMAGE_EX DAMAGE_EX2 ARROW_CRITICAL WATER_SPLASH ST_FIRE_OIL").split()
ENUMS = {
    "mEffectType": {i: f"EFC_TYPE_{n}" for i, n in enumerate(_EFC_TYPE)},
    "mSetType": {0: "EFC_SET_TYPE_CONST", 1: "EFC_SET_TYPE_STAY", 2: "EFC_SET_TYPE_WORLD", 3: "EFC_SET_TYPE_CAMERA"},
    "mEndType": {0: "EFC_END_TYPE_LEAVE", 1: "EFC_END_TYPE_FINISH", 2: "EFC_END_TYPE_KILL"},
    "mPosType": {0: "POS_TYPE_JOINT", 1: "POS_TYPE_NULL"},
    "mRotType": {0: "ROT_TYPE_JOINT", 1: "ROT_TYPE_NULL"},
    "mE2DType": {0: "E2D_TYPE_NONE", 1: "E2D_TYPE_HITOMI_BODY", 2: "E2D_TYPE_HITOMI_EYE", 3: "E2D_TYPE_PL_BLIND",
                 4: "E2D_TYPE_ULDRAGON"},
}

_FIXED = {"u32": 4, "x32": 4, "s32": 4, "f32": 4, "v3": 12, "u8": 1, "u32list": 4, "names8": 8, "name": 1,
          "lod3": 3 * 4 * len(_LOD)}


def _min_size(schema) -> int:
    """The fewest bytes a record of this schema can take (bounds a count by the bytes left)."""
    return sum(_FIXED[t] for _, t in schema)


@dataclass
class Epv:
    version: int = VERSION_DDDA
    indices: list[list[dict]] = field(default_factory=list)   # each index: its elements (field -> value)
    motsync: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)


# -- bytes -------------------------------------------------------------------------------------------
class _Reader:
    def __init__(self, data: bytes):
        self.d = data
        self.p = 0

    def take(self, n: int) -> bytes:
        if self.p + n > len(self.d):
            raise FormatError("epv", f"file ends inside a record (needs {n} more bytes)", self.p)
        v = self.d[self.p:self.p + n]
        self.p += n
        return v

    def u32(self) -> int:
        return _U32.unpack(self.take(4))[0]

    def count(self, what: str, min_size: int) -> int:
        at = self.p
        n = self.u32()
        if n and n > (len(self.d) - self.p) // max(min_size, 1):
            raise FormatError("epv", f"{what} count {n} does not fit in the {len(self.d) - self.p} bytes left", at)
        return n

    def name(self) -> str:
        end = self.d.find(b"\0", self.p)
        if end < 0:
            raise FormatError("epv", "a resource path is not NUL-terminated", self.p)
        s = self.d[self.p:end].decode("latin-1")
        self.p = end + 1
        return s


def _read(r: _Reader, schema) -> dict:
    out = {}
    for name, t in schema:
        if t in ("u32", "x32"):
            out[name] = r.u32()
        elif t == "s32":
            out[name] = struct.unpack("<i", r.take(4))[0]
        elif t == "f32":
            out[name] = r.u32()
        elif t == "v3":
            out[name] = list(struct.unpack("<3I", r.take(12)))
        elif t == "u8":
            out[name] = r.take(1)[0]
        elif t == "u32list":
            n = r.count(name, 4)
            out[name] = list(struct.unpack(f"<{n}I", r.take(4 * n)))
        elif t == "names8":
            out[name] = [r.name() for _ in range(LIST_SLOTS)]
        elif t == "name":
            out[name] = r.name()
        elif t == "lod3":
            out[name] = [_read(r, _LOD) for _ in range(3)]
        else:  # pragma: no cover - schema typo
            raise AssertionError(t)
    return out


def parse(data: bytes) -> Epv:
    data = bytes(data)
    if data[:4] != MAGIC:
        raise FormatError("epv", f"not an .epv file (magic {data[:4]!r}, expected {MAGIC!r})", 0)
    r = _Reader(data)
    r.p = 4
    version = r.u32()
    if version not in VERSIONS:
        raise FormatError("epv", f"version {version} is not one the games use ({VERSION_DDDA} Dark Arisen, "
                                 f"{VERSION_DDO} Online)", 4)
    elem, mot = ELEMENT[version], MOTSYNC[version]
    epv = Epv(version)
    for _ in range(r.count("effect index", 4)):
        epv.indices.append([_read(r, elem) for _ in range(r.count("element", _min_size(elem)))])
    epv.motsync = [_read(r, mot) for _ in range(r.count("motion sync", _min_size(mot)))]
    epv.events = [_read(r, EVENT) for _ in range(r.count("event", _min_size(EVENT)))]
    if r.p != len(data):
        raise FormatError("epv", f"{len(data) - r.p} byte(s) after the event list", r.p)
    return epv


def _put(out: bytearray, schema, rec: dict, where: str) -> None:
    for name, t in schema:
        if name not in rec:
            raise FormatError("epv", f"{where}: '{name}' is missing")
        v = rec[name]
        try:
            if t in ("u32", "x32", "f32"):
                out += _U32.pack(v)
            elif t == "s32":
                out += struct.pack("<i", v)
            elif t == "v3":
                if len(v) != 3:
                    raise FormatError("epv", f"{where}: '{name}' needs 3 numbers")
                out += struct.pack("<3I", *v)
            elif t == "u8":
                out += struct.pack("<B", v)
            elif t == "u32list":
                out += struct.pack(f"<I{len(v)}I", len(v), *v)
            elif t in ("names8", "name"):
                names = v if t == "names8" else [v]
                if t == "names8" and len(names) != LIST_SLOTS:
                    raise FormatError("epv", f"{where}: '{name}' has {LIST_SLOTS} slots, not {len(names)}")
                for s in names:
                    b = s.encode("latin-1")
                    if b"\0" in b:
                        raise FormatError("epv", f"{where}: a path cannot contain a NUL")
                    out += b + b"\0"
            elif t == "lod3":
                if len(v) != 3:
                    raise FormatError("epv", f"{where}: '{name}' has 3 levels, not {len(v)}")
                for i, lod in enumerate(v):
                    _put(out, _LOD, lod, f"{where} {name}[{i}]")
        except (struct.error, TypeError):
            raise FormatError("epv", f"{where}: '{name}' = {v!r} does not fit a {t}") from None
        except UnicodeEncodeError:
            raise FormatError("epv", f"{where}: '{name}' has characters a resource path cannot hold") from None


def build(epv: Epv) -> bytes:
    if epv.version not in VERSIONS:
        raise FormatError("epv", f"version {epv.version} is not one the games use")
    elem, mot = ELEMENT[epv.version], MOTSYNC[epv.version]
    out = bytearray(MAGIC + _U32.pack(epv.version) + _U32.pack(len(epv.indices)))
    for i, idx in enumerate(epv.indices):
        out += _U32.pack(len(idx))
        for j, e in enumerate(idx):
            _put(out, elem, e, f"index {i} element {j}")
    out += _U32.pack(len(epv.motsync))
    for i, m in enumerate(epv.motsync):
        _put(out, mot, m, f"motion sync {i}")
    out += _U32.pack(len(epv.events))
    for i, ev in enumerate(epv.events):
        _put(out, EVENT, ev, f"event {i}")
    return bytes(out)


# -- reading it ----------------------------------------------------------------------------------------
def f32(bits: int) -> float:
    return struct.unpack("<f", _U32.pack(bits))[0]


def paths(epv: Epv) -> list[str]:
    """Every effect file an .epv names (rEffectList and rEffect2D paths), each once, in file order."""
    seen: dict[str, None] = {}
    for idx in epv.indices:
        for e in idx:
            for p in (*e["mpEffectList"], e["mpEffect2D"]):
                if p:
                    seen.setdefault(p, None)
    return list(seen)


def links(epv: Epv) -> list[dict]:
    """The motion -> effect link: for each motion-sync entry, the motion, its frame window and the
    element it spawns (its effect lists, joint and offset).  Index/element numbers out of range are
    reported as they are (the engine checks them at run time)."""
    rows = []
    for m in epv.motsync:
        i, j = m["mEfcIndexNo"], m["mEfcElementNo"]
        e = epv.indices[i][j] if i < len(epv.indices) and j < len(epv.indices[i]) else None
        rows.append({"motion": m["mMotionNo"], "frames": (f32(m["mStartFrame"]), f32(m["mEndFrame"])),
                     "index": i, "element": j,
                     "effects": [p for p in e["mpEffectList"] if p] if e else None,
                     "joint": e["mJointNo"] if e else None,
                     "position": tuple(round(f32(b), 4) for b in e["mPosition"]) if e else None})
    return rows


def info(epv: Epv, limit: int = 12) -> str:
    n_el = sum(len(i) for i in epv.indices)
    game = "Dark Arisen" if epv.version == VERSION_DDDA else "Online"
    lines = [f"rEffectProvider v{epv.version} ({game}): {len(epv.indices)} effect indices, {n_el} elements, "
             f"{len(epv.motsync)} motion-sync entries, {len(epv.events)} events",
             f"effect files named: {len(paths(epv))}"]
    for row in links(epv)[:limit]:
        a, b = row["frames"]
        fx = ", ".join(row["effects"]) if row["effects"] else "(no such element)"
        lines.append(f"  motion {row['motion']:<5} frames {a:g}-{b:g}  -> index {row['index']} element "
                     f"{row['element']}  joint {row['joint']}  {fx}")
    if len(epv.motsync) > limit:
        lines.append(f"  ... {len(epv.motsync) - limit} more motion-sync entries")
    return "\n".join(lines)


# -- YAML ----------------------------------------------------------------------------------------------
def _num_node(t: str, v):
    from .params import f32_bits_text
    from .yamlish import Scalar, Seq

    if t == "x32":
        return Scalar(f"0x{v:08x}")
    if t == "f32":
        return Scalar(f32_bits_text(v))
    if t == "v3":
        return Seq([Scalar(f32_bits_text(b)) for b in v], flow=True)
    if t == "u32list":
        return Seq([Scalar(f"0x{x:08x}" if x > 0xFFFF else str(x)) for x in v], flow=True)
    return Scalar(str(v))


def _map_node(schema, rec: dict, flow: bool = False, enums: bool = False):
    from .yamlish import Map, Scalar, Seq

    items = []
    for name, t in schema:
        v = rec[name]
        if t == "names8":
            last = max((i for i, s in enumerate(v) if s), default=-1)
            node = Seq([Scalar(s, "double") for s in v[:last + 1]], flow=True)
        elif t == "name":
            node = Scalar(v, "double")
        elif t == "lod3":
            node = Seq([_map_node(_LOD, lod, flow=True) for lod in v])
        else:
            node = _num_node(t, v)
            if enums and name in ENUMS and v in ENUMS[name]:
                node.comment = ENUMS[name][v]
        items.append((Scalar(name), node))
    return Map(items, flow=flow)


def to_yaml(epv: Epv, name: str | None = None) -> str:
    from . import yamlish
    from .yamlish import Map, Scalar, Seq

    head = ["Riftstone effect provider (.epv)" + (f" -- {name}" if name else ""),
            f"version {epv.version} ({'Dark Arisen' if epv.version == VERSION_DDDA else 'Online'}). An index is a "
            "list of elements; an element names up to 8 effect lists",
            "(mpEffectList, .efl paths) and one 2D effect, and places them: mJointNo (-1 = none), mPosition,",
            "mDirection, mScale... motsync: which motion (mMotionNo, frames mStartFrame..mEndFrame) spawns",
            "which element (mEfcIndexNo / mEfcElementNo). Floats are exact (nan:0x... keeps a NaN's bits).",
            "Rebuilds byte-for-byte when untouched."]
    items = [(Scalar("riftstone"), Scalar(TAG))]
    if name:
        items.append((Scalar("resource"), Scalar(name, "double")))
    items.append((Scalar("version"), Scalar(str(epv.version))))
    elem = ELEMENT[epv.version]
    enums = epv.version == VERSION_DDDA
    idx_nodes = [Map([(Scalar("elements"), Seq([_map_node(elem, e, enums=enums) for e in idx]))])
                 for idx in epv.indices]
    items.append((Scalar("indices"), Seq(idx_nodes)))
    items.append((Scalar("motsync"), Seq([_map_node(MOTSYNC[epv.version], m, flow=True) for m in epv.motsync])))
    items.append((Scalar("events"), Seq([_map_node(EVENT, ev, flow=True) for ev in epv.events])))
    return yamlish.emit(Map(items), head)


def _where(node, source):
    return (getattr(node, "line", None), getattr(node, "col", None), source)


def _int(node, name, lo, hi, source) -> int:
    from .yamlish import Scalar

    if not isinstance(node, Scalar):
        raise ParamError(f"'{name}' must be a single number", *_where(node, source))
    try:
        v = int(node.text.strip(), 0)
    except ValueError:
        raise ParamError(f"'{name}': {node.text!r} is not a whole number", *_where(node, source)) from None
    if not lo <= v <= hi:
        raise ParamError(f"'{name}': {v} is out of range ({lo}..{hi})", *_where(node, source))
    return v


def _fbits(node, name, source) -> int:
    from .params import f32_bits
    from .yamlish import Scalar

    if not isinstance(node, Scalar):
        raise ParamError(f"'{name}' must be a single number", *_where(node, source))
    try:
        return f32_bits(node.text)
    except (ValueError, OverflowError):
        raise ParamError(f"'{name}': {node.text!r} is not a 32-bit float", *_where(node, source)) from None


def _text(node, name, source) -> str:
    from .yamlish import Scalar

    if not isinstance(node, Scalar):
        raise ParamError(f"'{name}' must be a path (text)", *_where(node, source))
    try:
        node.text.encode("latin-1")
    except UnicodeEncodeError:
        raise ParamError(f"'{name}': a resource path is plain ASCII", *_where(node, source)) from None
    if "\0" in node.text:
        raise ParamError(f"'{name}': a path cannot contain a NUL", *_where(node, source))
    return node.text


def _seq(node, name, source):
    from .yamlish import Seq

    if node is None:
        return []
    if not isinstance(node, Seq):
        raise ParamError(f"'{name}' must be a list", *_where(node, source))
    return node.items


def _from_map(node, schema, what, source) -> dict:
    from .yamlish import Map

    if not isinstance(node, Map):
        raise ParamError(f"{what} must be a mapping of fields", *_where(node, source))
    known = {n for n, _ in schema}
    for k, _v in node.items:
        if k.text not in known:
            raise ParamError(f"{what}: unknown field '{k.text}'", k.line, k.col, source)
    out = {}
    for name, t in schema:
        v = node.get(name)
        if v is None:
            raise ParamError(f"{what}: '{name}' is missing", *_where(node, source))
        if t in ("u32", "x32"):
            out[name] = _int(v, name, 0, 0xFFFFFFFF, source)
        elif t == "s32":
            out[name] = _int(v, name, -0x80000000, 0x7FFFFFFF, source)
        elif t == "u8":
            out[name] = _int(v, name, 0, 0xFF, source)
        elif t == "f32":
            out[name] = _fbits(v, name, source)
        elif t == "v3":
            xs = _seq(v, name, source)
            if len(xs) != 3:
                raise ParamError(f"'{name}' is [x, y, z]", *_where(v, source))
            out[name] = [_fbits(x, name, source) for x in xs]
        elif t == "u32list":
            out[name] = [_int(x, name, 0, 0xFFFFFFFF, source) for x in _seq(v, name, source)]
        elif t == "names8":
            xs = _seq(v, name, source)
            if len(xs) > LIST_SLOTS:
                raise ParamError(f"'{name}' has at most {LIST_SLOTS} paths", *_where(v, source))
            out[name] = [_text(x, name, source) for x in xs] + [""] * (LIST_SLOTS - len(xs))
        elif t == "name":
            out[name] = _text(v, name, source)
        elif t == "lod3":
            xs = _seq(v, name, source)
            if len(xs) != 3:
                raise ParamError(f"'{name}' has 3 levels (low, middle, high)", *_where(v, source))
            out[name] = [_from_map(x, _LOD, f"{what} {name}", source) for x in xs]
    return out


def from_yaml(text: str, source: str | None = None) -> Epv:
    from . import yamlish
    from .yamlish import Map, Scalar

    doc = yamlish.parse(text, source)
    if not isinstance(doc, Map) or not isinstance(doc.get("riftstone"), Scalar) or doc.get("riftstone").text != TAG:
        raise ParamError(f"not a Riftstone effect provider (expected 'riftstone: {TAG}')", 1, 1, source)
    version = _int(doc.get("version"), "version", 0, 0xFFFFFFFF, source) if doc.get("version") is not None else None
    if version not in VERSIONS:
        raise ParamError(f"version must be {VERSION_DDDA} (Dark Arisen) or {VERSION_DDO} (Online)",
                         *_where(doc.get("version") or doc, source))
    epv = Epv(version)
    for i, idx in enumerate(_seq(doc.get("indices"), "indices", source)):
        if not isinstance(idx, Map):
            raise ParamError("each index is a mapping with 'elements'", *_where(idx, source))
        epv.indices.append([_from_map(e, ELEMENT[version], f"index {i} element {j}", source)
                            for j, e in enumerate(_seq(idx.get("elements"), "elements", source))])
    epv.motsync = [_from_map(m, MOTSYNC[version], f"motsync {i}", source)
                   for i, m in enumerate(_seq(doc.get("motsync"), "motsync", source))]
    epv.events = [_from_map(e, EVENT, f"event {i}", source)
                  for i, e in enumerate(_seq(doc.get("events"), "events", source))]
    try:
        build(epv)
    except FormatError as e:
        raise ParamError(str(e), None, None, source) from None
    return epv


def yaml_to_bytes(text: str, source: str | None = None) -> bytes:
    return build(from_yaml(text, source))

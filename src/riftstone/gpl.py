"""Enemy group placement (rLayoutGroupParamList, ``.gpl``): which enemies a stage spawns, in what
groups, how many, and where.  This is the format behind the "3 unit kinds per group" limit (the
community unit-expander patches the exe that reads ``mUnitKindList``) and the per-group spawn cap
(``mSetCountMax``).

Layout from Chris Purnell's dd-tools (`vendor/dd-tools-main/gpl2xml.c`), proved byte-exact on every
``.gpl`` in the game (`check_corpus --only gpl`):

  magic "gpl\\0"; version i32; mGroupList (u32 list); mSetBit (u32 list); mDLCNo i32;
  then a list of group records.  A group holds a packed 32-bit field (group id 9 bits, priority 18,
  a split flag, DLC 4), an ``mUnitKindList`` (enemy name + belong flag), an ``mLayoutIDArray``, a
  long run of scalar conditions, and three kinds of area shapes (hit / life / kill).

A shape is a tagged record (``type`` 1 box, 2 point, 3 capsule) with type-specific fields.

Every f32 is kept as its exact 32-bit bits so NaN payloads survive; strings are NUL-terminated.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .errors import FormatError, ParamError

# The game reads a group's unit-kind count from the file and loads that many kinds into three inline
# slots without checking it (cGroupParam::load 0x00CC4690: 0x30 bytes each from +0x9C; only the next
# loop has a fixed 3, 0x00CC4733), so a fourth kind overwrites the group's own fields and a seventh runs
# past the 0x1E8-byte object.  Every vanilla group lists 0 to 3, and Riftstone does not ship the
# third-party unit expander, so a group with more is read but never written.
UNIT_KINDS_MAX = 3

MAGIC = 0x006C7067
TAG = "gpl/1"
_I = struct.Struct("<i")
_U = struct.Struct("<I")
_H = struct.Struct("<h")

# the group's scalar conditions, in file order, between mLayoutIDArray and mAreaHitShapeList and after.
# Names are dd-tools' where ASCII; the Japanese-named ones are given stable ASCII names (order is what
# matters for the bytes).  (name, type) with type i32/i16/i8.
_COND_A = [
    ("Next_Normal", "i32"), ("Random_Division", "i32"), ("Next_Special", "i32"),
    ("HighPriorityEvenIfNotDrawn", "i32"), ("HeldItem", "i32"), ("Random_Pattern", "i32"),
    ("ExcludedSpecialPatternSpecify", "i32"), ("ExcludedSpecialPattern", "i32"), ("ReaperResetWait", "i32"),
    ("DoNotSetCorpse", "i32"), ("ForceCorpseSpawnEffect", "i32"), ("TopPriorityNoOtherGroupUnits", "i32"),
    ("mLoadCondition.mScenario", "i32"), ("mAppearBgn", "i32"), ("mAppearEnd", "i32"),
    ("mLoadCondition.mLotFlag", "i32"), ("mLoadCondition.mLotFlag2", "i32"),
    ("mDataLotFlag.mFlagNo", "i16"), ("mDataLotFlag.mFlagNo2", "i16"), ("mSetCondition.mTime", "i32"),
    ("mDataSetHour.mSetHourBgn", "i16"), ("mDataSetHour.mSetHourEnd", "i16"), ("mSetCondition.mTimeKA", "i32"),
    ("HeldItemNo", "i32"), ("mSetCondition.mAreaHit", "i32"),
]
_COND_B = [
    ("mSetCondition.mFsm", "i32"), ("mSetCondition.mRequest", "i32"), ("mSetCondition.mChArea", "i32"),
    ("mSetCondition.mSimpleEv", "i32"), ("mSetCountMax", "i32"), ("mSetCondition.mIsEmGroupLink", "i32"),
    ("mSetCondition.mLinkEmGroup", "i32"), ("mDeleteCondition.mLotFlag", "i32"),
    ("mDeleteCondition.mScenario", "i32"), ("mDeleteCondition.mTime", "i32"), ("mRspnCondition.mRspnType", "i32"),
    ("mRspnCondition.mRspnDay", "i32"), ("mRspnCondition.mRspnProb", "i32"), ("mRspnCondition.mRspnProbAdd", "i32"),
    ("mRspnCondition.mRspnForceRspn", "i32"),
]
_COND_C = [("ShareWanderArea", "i8"), ("SharedWanderAreaGroup", "i32"),
           ("ShareKillArea", "i8"), ("SharedKillAreaGroup", "i32")]


class _Reader:
    def __init__(self, data: bytes):
        self.d = data
        self.p = 0

    def _take(self, n):
        if self.p + n > len(self.d):
            raise FormatError("gpl", "file ends mid-record", self.p)
        v = self.d[self.p:self.p + n]
        self.p += n
        return v

    def i32(self):
        return _I.unpack(self._take(4))[0]

    def u32(self):
        return _U.unpack(self._take(4))[0]

    def i16(self):
        return _H.unpack(self._take(2))[0]

    def i8(self):
        return struct.unpack("<b", self._take(1))[0]

    def f32bits(self):
        return _U.unpack(self._take(4))[0]

    def count(self):
        n = _U.unpack(self._take(4))[0]      # a length is unsigned; an absurd one fails in _take, not silently
        if n > len(self.d):
            raise FormatError("gpl", f"array count {n} is larger than the file", self.p - 4)
        return n

    def scalar(self, tc):
        return {"i32": self.i32, "i16": self.i16, "i8": self.i8}[tc]()

    def string(self):
        end = self.d.find(b"\0", self.p)
        if end < 0:
            raise FormatError("gpl", "a string is not terminated", self.p)
        s = self.d[self.p:end].decode("utf-8", "surrogateescape")
        self.p = end + 1
        return s


class _Writer:
    def __init__(self):
        self.out = bytearray()

    def i32(self, v):
        self.out += _I.pack(v)

    def u32(self, v):
        self.out += _U.pack(v)            # out of range: struct.error, which build() reports (no silent wrap)

    def i16(self, v):
        self.out += _H.pack(v)

    def i8(self, v):
        self.out += struct.pack("<b", v)

    def f32bits(self, v):
        self.out += _U.pack(v & 0xFFFFFFFF)

    def scalar(self, tc, v):
        {"i32": self.i32, "i16": self.i16, "i8": self.i8}[tc](v)

    def string(self, s):
        self.out += s.encode("utf-8", "surrogateescape") + b"\0"


# -- shapes ---------------------------------------------------------------------------------
def _read_vector(r):
    return [r.f32bits() for _ in range(4)]


def _read_shape(r) -> dict:
    s = {"mName": r.string(), "mCheckAngle": r.f32bits(), "mCheckRange": r.f32bits(),
         "mCheckToward": r.f32bits(), "mAngleFlag": r.i8(), "mTowardFlag": r.i8(),
         "type": r.i32(), "mDecay": r.f32bits(), "mIsNativeData": r.i8()}
    t = s["type"]
    if t == 1:
        s["mHeight"] = r.f32bits()
        s["mBottom"] = r.f32bits()
        s["mVertex"] = [_read_vector(r) for _ in range(4)]
        s["mConcaveCrossPos"] = _read_vector(r)
        s["mFlgConvex"] = r.i8()
        s["mConcaveStatus"] = r.i32()
    elif t == 2:
        s["mVertex"] = _read_vector(r)
    elif t == 3:
        s["Position0"] = _read_vector(r)
        s["Position1"] = _read_vector(r)
        s["Radius"] = r.f32bits()
        s["pad"] = [r.u32(), r.u32(), r.u32()]
    else:
        raise FormatError("gpl", f"unknown area-shape type {t}", r.p)
    return s


def _write_shape(w, s):
    w.string(s["mName"])
    for k in ("mCheckAngle", "mCheckRange", "mCheckToward"):
        w.f32bits(s[k])
    w.i8(s["mAngleFlag"])
    w.i8(s["mTowardFlag"])
    w.i32(s["type"])
    w.f32bits(s["mDecay"])
    w.i8(s["mIsNativeData"])
    t = s["type"]
    if t == 1:
        w.f32bits(s["mHeight"])
        w.f32bits(s["mBottom"])
        for v in s["mVertex"]:
            for c in v:
                w.f32bits(c)
        for c in s["mConcaveCrossPos"]:
            w.f32bits(c)
        w.i8(s["mFlgConvex"])
        w.i32(s["mConcaveStatus"])
    elif t == 2:
        for c in s["mVertex"]:
            w.f32bits(c)
    elif t == 3:
        for c in s["Position0"]:
            w.f32bits(c)
        for c in s["Position1"]:
            w.f32bits(c)
        w.f32bits(s["Radius"])
        for x in s["pad"]:
            w.u32(x)
    else:
        raise ParamError(f"unknown area-shape type {t}")


# -- model -----------------------------------------------------------------------------------
@dataclass
class Gpl:
    version: int
    mGroupList: list
    mSetBit: list
    mDLCNo: int
    groups: list


def parse(data: bytes) -> Gpl:
    if len(data) < 4 or _U.unpack_from(data, 0)[0] != MAGIC:
        raise FormatError("gpl", "not a gpl file (magic)", 0)
    r = _Reader(data)
    r.p = 4
    version = r.i32()
    mgroups = [r.u32() for _ in range(r.count())]
    msetbit = [r.u32() for _ in range(r.count())]
    dlcno = r.i32()
    groups = []
    for _ in range(r.count()):
        g = {"mGroupClass": r.i32()}
        packed = r.u32()
        g["mGroup"] = packed & 0x1FF
        g["mPriority"] = (packed >> 9) & 0x3FFFF
        g["mIsDisableSplit"] = (packed >> 27) & 0x1
        g["mDLCNoBits"] = (packed >> 28) & 0xF
        g["mUnitKindList"] = [{"name": r.string(), "isBelong": r.i8()} for _ in range(r.count())]
        g["mLayoutIDArray"] = [{"mLayoutID": r.i32(), "mGroup": r.i32(), "mSplitX": r.i32(), "mSplitZ": r.i32()}
                               for _ in range(r.count())]
        for name, tc in _COND_A:
            g[name] = r.scalar(tc)
        g["mAreaHitShapeList"] = [_read_shape(r) for _ in range(r.count())]
        for name, tc in _COND_B:
            g[name] = r.scalar(tc)
        g["mLifeAreaArray"] = [[_read_shape(r) for _ in range(r.count())] for _ in range(r.count())]
        g["mKillAreaType"] = r.i32()
        g["mKillAreaList"] = [_read_shape(r) for _ in range(r.count())]
        for name, tc in _COND_C:
            g[name] = r.scalar(tc)
        groups.append(g)
    if r.p != len(data):
        raise FormatError("gpl", f"{len(data) - r.p} trailing byte(s)", r.p)
    return Gpl(version, mgroups, msetbit, dlcno, groups)


def build(gpl: Gpl) -> bytes:
    try:
        return _build(gpl)
    except (struct.error, OverflowError, KeyError, TypeError) as e:
        raise ParamError(f"a value does not fit its field: {e}") from None


def _build(gpl: Gpl) -> bytes:
    w = _Writer()
    w.u32(MAGIC)
    w.i32(gpl.version)
    w.i32(len(gpl.mGroupList))
    for v in gpl.mGroupList:
        w.u32(v)
    w.i32(len(gpl.mSetBit))
    for v in gpl.mSetBit:
        w.u32(v)
    w.i32(gpl.mDLCNo)
    w.i32(len(gpl.groups))
    for g in gpl.groups:
        w.i32(g["mGroupClass"])
        if not (0 <= g["mGroup"] <= 0x1FF and 0 <= g["mPriority"] <= 0x3FFFF
                and 0 <= g["mIsDisableSplit"] <= 1 and 0 <= g["mDLCNoBits"] <= 0xF):
            raise ParamError("a group field is out of range (mGroup 0..511, mPriority 0..262143, "
                             "mIsDisableSplit 0..1, mDLCNoBits 0..15)")
        w.u32(g["mGroup"] | (g["mPriority"] << 9) | (g["mIsDisableSplit"] << 27) | (g["mDLCNoBits"] << 28))
        if len(g["mUnitKindList"]) > UNIT_KINDS_MAX:
            raise ParamError(f"group {g['mGroup']} lists {len(g['mUnitKindList'])} unit kinds; the game holds "
                             f"{UNIT_KINDS_MAX} and loads more without checking, overwriting its memory")
        w.i32(len(g["mUnitKindList"]))
        for u in g["mUnitKindList"]:
            w.string(u["name"])
            w.i8(u["isBelong"])
        w.i32(len(g["mLayoutIDArray"]))
        for lay in g["mLayoutIDArray"]:
            for k in ("mLayoutID", "mGroup", "mSplitX", "mSplitZ"):
                w.i32(lay[k])
        for name, tc in _COND_A:
            w.scalar(tc, g[name])
        w.i32(len(g["mAreaHitShapeList"]))
        for sh in g["mAreaHitShapeList"]:
            _write_shape(w, sh)
        for name, tc in _COND_B:
            w.scalar(tc, g[name])
        w.i32(len(g["mLifeAreaArray"]))
        for inner in g["mLifeAreaArray"]:
            w.i32(len(inner))
            for sh in inner:
                _write_shape(w, sh)
        w.i32(g["mKillAreaType"])
        w.i32(len(g["mKillAreaList"]))
        for sh in g["mKillAreaList"]:
            _write_shape(w, sh)
        for name, tc in _COND_C:
            w.scalar(tc, g[name])
    return bytes(w.out)


# -- YAML ------------------------------------------------------------------------------------
def _y(value):
    from .params import f32_bits_text
    from .yamlish import Map, Scalar, Seq

    if isinstance(value, dict):
        return Map([(Scalar(k), _y(v)) for k, v in value.items()])
    if isinstance(value, list):
        flow = not value or not isinstance(value[0], (dict, list))
        return Seq([_y(v) for v in value], flow=flow)
    if isinstance(value, _F32):
        return Scalar(f32_bits_text(value.bits))
    if isinstance(value, str):
        return Scalar(value, "double")
    return Scalar(str(value))


class _F32:
    __slots__ = ("bits",)

    def __init__(self, bits):
        self.bits = bits


def _tag_floats(g: dict) -> dict:
    """Wrap the float-bit fields so YAML shows them as decimals, not raw ints."""
    out = {}
    for k, v in g.items():
        out[k] = _wrap(k, v)
    return out


_FLOAT_KEYS = {"mCheckAngle", "mCheckRange", "mCheckToward", "mDecay", "mHeight", "mBottom", "Radius"}


def _wrap(key, v):
    if isinstance(v, list):
        if key in ("mVertex", "mConcaveCrossPos", "Position0", "Position1") and v and isinstance(v[0], (int,)):
            return [_F32(x) for x in v]                   # a vector of 4 float-bits
        return [_wrap(key, x) for x in v]
    if isinstance(v, dict):
        return {k: _wrap(k, val) for k, val in v.items()}
    if key in _FLOAT_KEYS and isinstance(v, int):
        return _F32(v)
    return v


def to_yaml(gpl: Gpl, name: str | None = None) -> str:
    from . import yamlish
    from .yamlish import Map, Scalar, Seq

    head = ["Riftstone enemy group placement (.gpl)" + (f" -- {name}" if name else ""),
            f"{len(gpl.groups)} group(s). mUnitKindList is the enemies a group can spawn; mSetCountMax is",
            "its spawn cap. Vectors are [x, y, z, w]; area shapes are typed (1 box, 2 point, 3 capsule).",
            "Keep each shape's fields for its type. Rebuilds byte-for-byte when untouched."]
    items = [(Scalar("riftstone"), Scalar(TAG))]
    if name:
        items.append((Scalar("resource"), Scalar(name, "double")))
    items.append((Scalar("version"), Scalar(str(gpl.version))))
    items.append((Scalar("mGroupList"), Seq([Scalar(str(v)) for v in gpl.mGroupList], flow=True)))
    items.append((Scalar("mSetBit"), Seq([Scalar(str(v)) for v in gpl.mSetBit], flow=True)))
    items.append((Scalar("mDLCNo"), Scalar(str(gpl.mDLCNo))))
    items.append((Scalar("groups"), _y([_tag_floats(g) for g in gpl.groups])))
    return yamlish.emit(Map(items), head)


def _num(node, source, signed=True):
    from .yamlish import Scalar
    if not isinstance(node, Scalar):
        raise ParamError("expected a number", getattr(node, "line", None), None, source)
    try:
        return int(node.text.strip(), 0)
    except ValueError:
        raise ParamError(f"expected a whole number, not {node.text!r}", node.line, node.col, source) from None


def _u32(node, source):
    """An unsigned 32-bit field (mGroupList, mSetBit, pad): the writer would otherwise keep the low 32 bits."""
    v = _num(node, source)
    if not 0 <= v <= 0xFFFFFFFF:
        raise ParamError(f"{v} does not fit an unsigned 32-bit field (0..4294967295)", node.line, node.col, source)
    return v


_TOP_KEYS = ("riftstone", "resource", "version", "mGroupList", "mSetBit", "mDLCNo", "groups")
_LISTS = ("mUnitKindList", "mLayoutIDArray", "mAreaHitShapeList", "mLifeAreaArray", "mKillAreaList")
_GROUP_KEYS = frozenset(("mGroupClass", "mGroup", "mPriority", "mIsDisableSplit", "mDLCNoBits", "mKillAreaType", *_LISTS,
                         *(name for name, _tc in _COND_A + _COND_B + _COND_C)))
_UNIT_KEYS = ("name", "isBelong")
_LAYOUT_KEYS = ("mLayoutID", "mGroup", "mSplitX", "mSplitZ")
_SHAPE_KEYS = ("mName", "mCheckAngle", "mCheckRange", "mCheckToward", "mAngleFlag", "mTowardFlag", "type", "mDecay",
               "mIsNativeData")
_SHAPE_TYPE_KEYS = {1: ("mHeight", "mBottom", "mVertex", "mConcaveCrossPos", "mFlgConvex", "mConcaveStatus"),
                    2: ("mVertex",), 3: ("Position0", "Position1", "Radius", "pad")}


def _only(node, known, what, source):
    """Refuse a key the format does not have: a misspelled one was dropped without a word."""
    for k, _v in node.items:
        if k.text not in known:
            raise ParamError(f"{what} has no field {k.text!r}", k.line, k.col, source)


def _items(parent, key, source) -> list:
    """The items of a list every group holds (to_yaml writes each one, [] when empty)."""
    from .yamlish import Seq
    node = parent.get(key)
    if node is None:
        raise ParamError(f"{key} is missing (a list; [] when empty)", parent.line, parent.col, source)
    if not isinstance(node, Seq):
        raise ParamError(f"{key} is a list", getattr(node, "line", None), getattr(node, "col", None), source)
    return node.items


def _f32(node, source):
    from .params import f32_bits
    from .yamlish import Scalar
    if not isinstance(node, Scalar):
        raise ParamError("expected a number", getattr(node, "line", None), None, source)
    try:
        return f32_bits(node.text)
    except ValueError:
        raise ParamError(f"{node.text!r} is not a 32-bit float", node.line, node.col, source) from None


def _vec(node, source):
    from .yamlish import Seq
    if not isinstance(node, Seq) or len(node.items) != 4:
        raise ParamError("a vector is [x, y, z, w]", getattr(node, "line", None), None, source)
    return [_f32(x, source) for x in node.items]


def _shape_from(node, source):
    from .yamlish import Map
    if not isinstance(node, Map):
        raise ParamError("a shape is a block", getattr(node, "line", None), None, source)
    s = {"mName": _str(node.get("mName"), source), "mCheckAngle": _f32(node.get("mCheckAngle"), source),
         "mCheckRange": _f32(node.get("mCheckRange"), source), "mCheckToward": _f32(node.get("mCheckToward"), source),
         "mAngleFlag": _num(node.get("mAngleFlag"), source), "mTowardFlag": _num(node.get("mTowardFlag"), source),
         "type": _num(node.get("type"), source), "mDecay": _f32(node.get("mDecay"), source),
         "mIsNativeData": _num(node.get("mIsNativeData"), source)}
    t = s["type"]
    if t in _SHAPE_TYPE_KEYS:
        _only(node, _SHAPE_KEYS + _SHAPE_TYPE_KEYS[t], f"a type {t} area shape", source)
    if t == 1:
        s["mHeight"] = _f32(node.get("mHeight"), source)
        s["mBottom"] = _f32(node.get("mBottom"), source)
        vs = node.get("mVertex")
        from .yamlish import Seq
        if not isinstance(vs, Seq) or len(vs.items) != 4:
            raise ParamError("mVertex is 4 vectors", getattr(vs, "line", None), None, source)
        s["mVertex"] = [_vec(v, source) for v in vs.items]
        s["mConcaveCrossPos"] = _vec(node.get("mConcaveCrossPos"), source)
        s["mFlgConvex"] = _num(node.get("mFlgConvex"), source)
        s["mConcaveStatus"] = _num(node.get("mConcaveStatus"), source)
    elif t == 2:
        s["mVertex"] = _vec(node.get("mVertex"), source)
    elif t == 3:
        s["Position0"] = _vec(node.get("Position0"), source)
        s["Position1"] = _vec(node.get("Position1"), source)
        s["Radius"] = _f32(node.get("Radius"), source)
        pad = node.get("pad")
        from .yamlish import Seq
        if not isinstance(pad, Seq) or len(pad.items) != 3:
            raise ParamError("pad is 3 numbers", getattr(pad, "line", None), None, source)
        s["pad"] = [_u32(x, source) for x in pad.items]
    else:
        raise ParamError(f"unknown area-shape type {t}", getattr(node, "line", None), None, source)
    return s


def _str(node, source):
    from .yamlish import Scalar
    if not isinstance(node, Scalar):
        raise ParamError("expected a string", getattr(node, "line", None), None, source)
    if "\0" in node.text:
        raise ParamError("a name cannot contain a NUL", node.line, node.col, source)
    try:
        node.text.encode("utf-8", "surrogateescape")
    except UnicodeEncodeError:        # a lone surrogate outside \udc80-\udcff (the escapes of stored bytes)
        raise ParamError("a name holds a character that is not valid text (a lone surrogate)", node.line, node.col,
                         source) from None
    return node.text


def _maps(parent, key, what, known, source):
    """The items of one of a group's lists, each a block of exactly the fields ``known``."""
    from .yamlish import Map
    items = _items(parent, key, source)
    for it in items:
        if not isinstance(it, Map):
            raise ParamError(f"{what} must be a block of fields", getattr(it, "line", None), None, source)
        _only(it, known, what, source)
    return items


def from_yaml(text: str, source: str | None = None) -> Gpl:
    from . import yamlish
    from .yamlish import Map, Scalar, Seq

    doc = yamlish.parse(text, source)
    tag = doc.get("riftstone") if isinstance(doc, Map) else None
    if not isinstance(tag, Scalar) or tag.text != TAG:
        raise ParamError(f"not a Riftstone gpl file (expected 'riftstone: {TAG}')", 1, 1, source)
    _only(doc, _TOP_KEYS, "a group list", source)

    def uints(key):
        node = doc.get(key)
        if not isinstance(node, Seq):
            raise ParamError(f"{key} is a list", getattr(node, "line", None), None, source)
        return [_u32(x, source) for x in node.items]

    gpl = Gpl(_num(doc.get("version"), source), uints("mGroupList"), uints("mSetBit"),
              _num(doc.get("mDLCNo"), source), [])
    groups = doc.get("groups")
    if not isinstance(groups, Seq):
        raise ParamError("groups is a list", getattr(groups, "line", None), None, source)
    for gn in groups.items:
        if not isinstance(gn, Map):
            raise ParamError("each group is a block", getattr(gn, "line", None), None, source)
        _only(gn, _GROUP_KEYS, "a group", source)
        g = {"mGroupClass": _num(gn.get("mGroupClass"), source),
             "mGroup": _num(gn.get("mGroup"), source), "mPriority": _num(gn.get("mPriority"), source),
             "mIsDisableSplit": _num(gn.get("mIsDisableSplit"), source), "mDLCNoBits": _num(gn.get("mDLCNoBits"), source)}
        g["mUnitKindList"] = [{"name": _str(u.get("name"), source), "isBelong": _num(u.get("isBelong"), source)}
                              for u in _maps(gn, "mUnitKindList", "a unit kind", _UNIT_KEYS, source)]
        g["mLayoutIDArray"] = [{k: _num(la.get(k), source) for k in _LAYOUT_KEYS}
                               for la in _maps(gn, "mLayoutIDArray", "a layout entry", _LAYOUT_KEYS, source)]
        for name, _tc in _COND_A:
            g[name] = _num(gn.get(name), source)
        g["mAreaHitShapeList"] = [_shape_from(s, source) for s in _items(gn, "mAreaHitShapeList", source)]
        for name, _tc in _COND_B:
            g[name] = _num(gn.get(name), source)
        life = []
        for inner in _items(gn, "mLifeAreaArray", source):
            if not isinstance(inner, Seq):
                raise ParamError("each entry of mLifeAreaArray is a list of shapes ([] when empty)",
                                 getattr(inner, "line", None), getattr(inner, "col", None), source)
            life.append([_shape_from(s, source) for s in inner.items])
        g["mLifeAreaArray"] = life
        g["mKillAreaType"] = _num(gn.get("mKillAreaType"), source)
        g["mKillAreaList"] = [_shape_from(s, source) for s in _items(gn, "mKillAreaList", source)]
        for name, _tc in _COND_C:
            g[name] = _num(gn.get(name), source)
        gpl.groups.append(g)
    return gpl


def yaml_to_bytes(text: str, source: str | None = None) -> bytes:
    return build(from_yaml(text, source))

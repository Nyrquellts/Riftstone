"""Message sets (rMsgSet ``.mss``) and message serial lists (rMsgSerial ``.msl``): which text lines an NPC
says together, in what order, with which motion and face, and under which conditions. Byte-exact, with YAML.

**Dragon's Dogma: Dark Arisen rMsgSet** (830 distinct files, ``id\\npc_wind\\...`` and ``id\\message\\...``).
The loader (PC DDDA.exe 0x00CC95F0; PS3 ``rMsgSet::load`` 0x00AEFCA0, named) refuses any magic but
``mss\\0`` and any version but 0x20, then reads ``count`` records field by field into ``rMsgSet::cParam``
(0x1C8 bytes in memory; every name below is a cParam property, DDDA.exe createProperty 0x00CC8F60)::

  "mss\\0"  u32 version 0x20  u32 count  count x 250-byte cParam:
    s32 mNo   s16 mNpcId   s32 mSelMsgTbl[6]   s16 mSelMsgJump[6]   s32 mMsgTbl[15]
    s16 mMsgMotTbl[15]   s8 mMsgFaceTbl[15]   s16 mGfsFlgNo_msg[15]   s16 mQuestNo_msg[15]
    s16 mQuestFlgNo_msg[15]   s8 mSeason   s8 mSeasonSequence   s8 mSeasonSequenceType
    s16 mQuestNo   s16 mQuestFlgNo   s16 mFriend   s16 mFriendMax   s16 mGsfFlgNo

A cParam is one conversation, found by its ``mNo`` (``rMsgSet::getMsgNo`` 0x00AF07AC): up to 15 lines
(``mMsgTbl``, -1 ends the list; the loader counts them into mMsgNum), each with a motion
(``mMsgMotTbl``) and a face (``mMsgFaceTbl``) and a condition -- a GSF flag (``mGfsFlgNo_msg``) or a
quest flag (``mQuestNo_msg`` + ``mQuestFlgNo_msg``) that must be set for the line to play -- and up to
6 choices (``mSelMsgTbl``, -1 ends; ``mSelMsgJump`` is the conversation each choice jumps to).
Read in the loader, both builds: the third 15-entry array is stored over ``mQuestNo_msg`` (PC
0x00CC97A4 writes to +0x130 twice), so ``mQuestFlgNo_msg`` keeps the constructor's -1 and getMsgNo skips
every quest-flag condition. 8 of the 8,676 vanilla conversations set one (2 use GSF flags); whether
those lines misbehave in game is UNKNOWN.

**Dragon's Dogma Online rMsgSet** (3,187 distinct files): a new layout, magic ``mgst`` (DDO.exe loader
0x00AB1E50, group reader 0x00AB1D20, line reader 0x00AB1C90; names are DDO.exe's own properties)::

  "mgst"  u16 version 3  u32 group count  u32 line count (all groups')
  group slot x count:  u8 present (0 = empty slot)  then, if present:
    u32 mGroupSerial  u32 mNpcId  u32 mGroupNameSerial  u32 mGroupType  u8 mNameDispOff
    u32 line slots, per slot: u8 present, then if present (cMsgData):
      u32 mMsgSerial  u32 mGmdIndex  u32 mMsgType  u32 mJumpGroupSerial  u32 mDispType
      u32 mDispTime  u32 mSetMotion  s32 mVoiceReqNo  u8 mTalkFaceType

The loader allocates the header's line count and fails when the lines outrun it; every vanilla file
states exactly the lines it holds, and no vanilla slot is empty (both are kept and checked here).

**Dragon's Dogma: Dark Arisen rMsgSerial** (752 distinct files, beside the NPC message sets; PC
0x00CC8CE0, PS3 0x00AEF60C)::

  "msl\\0"  u32 version 0x10  u32 count  count x u16 mNo (rMsgSerial::cParam; getSerial(i) returns it)

What the serial numbers index is UNKNOWN (they are not the mNo of the .mss beside them).

Proved on every distinct file (``check_corpus --only msgset``): DDDA 830 .mss and 752 .msl, DDO 3,187 .mss;
parse -> build and the YAML round trip byte-for-byte.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .errors import FormatError, ParamError
from .flat import A, L, S, _read_block, _write_block

MAGIC_MSS = b"mss\0"
MAGIC_MSL = b"msl\0"
MAGIC_DDO = b"mgst"
VERSION_MSS = 0x20
VERSION_MSL = 0x10
VERSION_DDO = 3
TAG_MSS = "mss/1"
TAG_DDO = "mss-ddo/1"
TAG_MSL = "msl/1"

# rMsgSet::cParam in file order (the loader's reads); names are the class's properties.
PARAM = [S("mNo", "i32"), S("mNpcId", "s16"), A("mSelMsgTbl", "i32", 6), A("mSelMsgJump", "s16", 6),
         A("mMsgTbl", "i32", 15), A("mMsgMotTbl", "s16", 15), A("mMsgFaceTbl", "i8", 15),
         A("mGfsFlgNo_msg", "s16", 15), A("mQuestNo_msg", "s16", 15), A("mQuestFlgNo_msg", "s16", 15),
         S("mSeason", "i8"), S("mSeasonSequence", "i8"), S("mSeasonSequenceType", "i8"),
         S("mQuestNo", "s16"), S("mQuestFlgNo", "s16"), S("mFriend", "s16"), S("mFriendMax", "s16"),
         S("mGsfFlgNo", "s16")]
PARAM_SIZE = 250
_MSS = [L("mpParam", PARAM)]
_MSL = [L("mpParam", "u16")]

# Dragon's Dogma Online
GROUP_FIELDS = (("mGroupSerial", "u32"), ("mNpcId", "u32"), ("mGroupNameSerial", "u32"), ("mGroupType", "u32"),
                ("mNameDispOff", "u8"))
DATA_FIELDS = (("mMsgSerial", "u32"), ("mGmdIndex", "u32"), ("mMsgType", "u32"), ("mJumpGroupSerial", "u32"),
               ("mDispType", "u32"), ("mDispTime", "u32"), ("mSetMotion", "u32"), ("mVoiceReqNo", "s32"),
               ("mTalkFaceType", "u8"))
_GROUP = struct.Struct("<IIIIB")
_DATA = struct.Struct("<IIIIIIIiB")
_RANGE = {"i8": (-128, 127), "u8": (0, 255), "s16": (-32768, 32767), "u16": (0, 65535),
          "i32": (-(1 << 31), (1 << 31) - 1), "s32": (-(1 << 31), (1 << 31) - 1), "u32": (0, 0xFFFFFFFF)}


@dataclass
class MsgSet:
    """DDDA rMsgSet: the cParam records (dicts keyed by the PARAM field names)."""
    params: list = field(default_factory=list)
    version: int = VERSION_MSS


@dataclass
class MsgSerial:
    """DDDA rMsgSerial: the mNo list."""
    serials: list = field(default_factory=list)
    version: int = VERSION_MSL


@dataclass
class MsgGroup:
    mGroupSerial: int = 0
    mNpcId: int = 0
    mGroupNameSerial: int = 0
    mGroupType: int = 0
    mNameDispOff: int = 0
    data: list = field(default_factory=list)     # dicts keyed by DATA_FIELDS, or None for an empty slot


@dataclass
class MsgSetDdo:
    """Dragon's Dogma Online rMsgSet: group slots (MsgGroup or None for an empty slot)."""
    groups: list = field(default_factory=list)
    version: int = VERSION_DDO


# -- binary ----------------------------------------------------------------------------------------
def _flat(data: bytes, schema, what: str) -> dict:
    try:
        obj, end = _read_block(data, 8, schema)
    except (struct.error, IndexError) as e:
        raise FormatError(what, f"a record runs past the end of the file ({e})") from None
    except FormatError as e:
        raise FormatError(what, "a record runs past the end of the file", e.offset) from None
    if end != len(data):
        raise FormatError(what, f"{len(data) - end} byte(s) after the last record", end)
    return obj


def parse(data: bytes):
    """A .mss (either game) or .msl, by its magic."""
    data = bytes(data)
    magic = data[:4]
    if magic == MAGIC_DDO:
        return _parse_ddo(data)
    if magic not in (MAGIC_MSS, MAGIC_MSL):
        raise FormatError("msgset", f"not a message set or serial list (magic {magic!r})", 0)
    what = "mss" if magic == MAGIC_MSS else "msl"
    if len(data) < 12:
        raise FormatError(what, f"the header needs 12 bytes, the file has {len(data)}", 0)
    version, count = struct.unpack_from("<II", data, 4)
    want = VERSION_MSS if what == "mss" else VERSION_MSL
    if version != want:
        raise FormatError(what, f"version 0x{version:x}; the game loads only 0x{want:x}", 4)
    size = PARAM_SIZE if what == "mss" else 2
    if count > (len(data) - 12) // size:
        raise FormatError(what, f"{count} records cannot fit in {len(data)} bytes", 8)
    if what == "mss":
        return MsgSet(_flat(data, _MSS, what)["mpParam"])
    return MsgSerial(_flat(data, _MSL, what)["mpParam"])


def _parse_ddo(data: bytes) -> MsgSetDdo:
    if len(data) < 14:
        raise FormatError("mss", f"the header needs 14 bytes, the file has {len(data)}", 0)
    version, ngroups, ndata = struct.unpack_from("<HII", data, 4)
    if version != VERSION_DDO:
        raise FormatError("mss", f"Dragon's Dogma Online message set version {version}; the game loads only 3", 4)
    p = 14
    if ngroups > len(data) - p:
        raise FormatError("mss", f"{ngroups} group slots cannot fit in {len(data)} bytes", 6)

    def flag(where: str) -> int:
        nonlocal p
        if p >= len(data):
            raise FormatError("mss", f"the file ends before {where}", p)
        v = data[p]
        if v not in (0, 1):
            raise FormatError("mss", f"{where}: presence byte {v} (the game's files write 0 or 1)", p)
        p += 1
        return v

    groups = []
    lines = 0
    for g in range(ngroups):
        if not flag(f"group slot {g}"):
            groups.append(None)
            continue
        if p + _GROUP.size + 4 > len(data):
            raise FormatError("mss", f"group {g} runs past the end of the file", p)
        grp = MsgGroup(*_GROUP.unpack_from(data, p))
        p += _GROUP.size
        (n,) = struct.unpack_from("<I", data, p)
        p += 4
        if n > len(data) - p:
            raise FormatError("mss", f"group {g}: {n} line slots cannot fit in the file", p - 4)
        for k in range(n):
            if not flag(f"group {g} line slot {k}"):
                grp.data.append(None)
                continue
            if p + _DATA.size > len(data):
                raise FormatError("mss", f"group {g} line {k} runs past the end of the file", p)
            grp.data.append(dict(zip((f for f, _ in DATA_FIELDS), _DATA.unpack_from(data, p))))
            p += _DATA.size
            lines += 1
        groups.append(grp)
    if p != len(data):
        raise FormatError("mss", f"{len(data) - p} byte(s) after the last group", p)
    if ndata != lines:
        raise FormatError("mss", f"the header counts {ndata} lines, the groups hold {lines}", 10)
    return MsgSetDdo(groups, version)


def build(m) -> bytes:
    if isinstance(m, MsgSetDdo):
        return _build_ddo(m)
    if isinstance(m, MsgSet):
        if m.version != VERSION_MSS:
            raise FormatError("mss", f"version 0x{m.version:x} is not the one the game loads (0x20)")
        head, schema, data = MAGIC_MSS + struct.pack("<I", m.version), _MSS, {"mpParam": m.params}
    elif isinstance(m, MsgSerial):
        if m.version != VERSION_MSL:
            raise FormatError("msl", f"version 0x{m.version:x} is not the one the game loads (0x10)")
        head, schema, data = MAGIC_MSL + struct.pack("<I", m.version), _MSL, {"mpParam": m.serials}
    else:
        raise FormatError("msgset", f"cannot build a {type(m).__name__}")
    out = bytearray(head)
    try:
        _write_block(out, schema, data)
    except (struct.error, KeyError, TypeError, ValueError, AttributeError, ParamError) as e:
        raise FormatError("msgset", f"a value does not fit its field ({e})") from None
    return bytes(out)


def _build_ddo(m: MsgSetDdo) -> bytes:
    if m.version != VERSION_DDO:
        raise FormatError("mss", f"version {m.version} is not the one the game loads (3)")
    body = bytearray()
    lines = 0
    try:
        for grp in m.groups:
            if grp is None:
                body += b"\0"
                continue
            body += b"\1" + _GROUP.pack(grp.mGroupSerial, grp.mNpcId, grp.mGroupNameSerial, grp.mGroupType,
                                       grp.mNameDispOff) + struct.pack("<I", len(grp.data))
            for d in grp.data:
                if d is None:
                    body += b"\0"
                    continue
                body += b"\1" + _DATA.pack(*(d[f] for f, _ in DATA_FIELDS))
                lines += 1
    except (struct.error, KeyError, TypeError, ValueError, AttributeError) as e:
        raise FormatError("mss", f"a value does not fit its field ({e})") from None
    return MAGIC_DDO + struct.pack("<HII", m.version, len(m.groups), lines) + bytes(body)


def info(m) -> str:
    if isinstance(m, MsgSet):
        lines = sum(sum(1 for v in p["mMsgTbl"] if v != -1) for p in m.params)
        return f"rMsgSet: {len(m.params)} conversation(s), {lines} line(s)"
    if isinstance(m, MsgSerial):
        return f"rMsgSerial: {len(m.serials)} serial number(s)"
    lines = sum(len([d for d in g.data if d is not None]) for g in m.groups if g is not None)
    return f"rMsgSet (Dragon's Dogma Online): {len(m.groups)} group(s), {lines} line(s)"


# -- YAML ------------------------------------------------------------------------------------------
def _yv(elem, v):
    from .yamlish import Map, Scalar, Seq
    if isinstance(elem, str):
        return Scalar(str(v))
    return Map([(Scalar(f[1]), _yf(f, v[f[1]])) for f in elem])


def _yf(f, v):
    from .yamlish import Seq
    if f[0] == "s":
        return _yv(f[2], v)
    return Seq([_yv(f[2], x) for x in v], flow=isinstance(f[2], str))


def _num(v: int, text: str) -> str:
    """A number for a message: in decimal, or its text cut short past 64 bits (int -> str refuses over
    4,300 digits, and a hex number has no such limit)."""
    return str(v) if v.bit_length() <= 64 else repr(text.strip()[:16] + "...")


class _Y:
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

    def int(self, node, t, what):
        from .yamlish import Scalar
        if not isinstance(node, Scalar):
            raise self.err(f"{what} must be a whole number", node)
        try:
            v = int(node.text.strip(), 0)
        except ValueError:
            raise self.err(f"{what}: {node.text!r} is not a whole number", node) from None
        lo, hi = _RANGE[t]
        if not lo <= v <= hi:
            raise self.err(f"{what}: {_num(v, node.text)} is outside {lo}..{hi} ({t})", node)
        return v

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


def _from_fields(y: _Y, node, fields, what: str) -> dict:
    y.only(node, [f[1] for f in fields], what)
    out = {}
    for f in fields:
        v = y.get(node, f[1], what)
        if f[0] == "s":
            out[f[1]] = y.int(v, f[2], f"{what} {f[1]}")
        else:
            out[f[1]] = [y.int(x, f[2], f"{what} {f[1]}") for x in y.seq(v, f[3], f"{what} {f[1]}")]
    return out


def to_yaml(m, name: str | None = None) -> str:
    from . import yamlish
    from .yamlish import Map, Scalar, Seq

    items = []
    shown = " -- " + "".join(c if c.isprintable() else " " for c in name) if name else ""   # stays in its comment
    if isinstance(m, MsgSet):
        tag = TAG_MSS
        head = ["Riftstone message set (.mss, rMsgSet)" + shown,
                f"{len(m.params)} conversation(s) (cParam), found by mNo. mMsgTbl: the lines in order (-1 ends),",
                "each with a motion, face and condition (GSF flag, or quest + flag; -1 = none); mSelMsgTbl the",
                "choices (-1 ends) and mSelMsgJump the conversation each leads to. Fixed-size lists keep their",
                "length. Rebuilds byte-for-byte when untouched."]
        body = [(Scalar("mpParam"), Seq([_yv(PARAM, p) for p in m.params], flow=not m.params))]
    elif isinstance(m, MsgSerial):
        tag = TAG_MSL
        head = ["Riftstone message serial list (.msl, rMsgSerial)" + shown,
                f"{len(m.serials)} serial number(s) (mNo, 0..65535). Rebuilds byte-for-byte when untouched."]
        body = [(Scalar("mpParam"), Seq([Scalar(str(v)) for v in m.serials], flow=True))]
    elif isinstance(m, MsgSetDdo):
        tag = TAG_DDO
        head = ["Riftstone message set (.mss, rMsgSet, Dragon's Dogma Online)" + shown,
                f"{len(m.groups)} group(s) of lines (cMsgGroup / cMsgData, the engine's names); an empty slot is",
                "null. Rebuilds byte-for-byte when untouched."]
        groups = []
        for g in m.groups:
            if g is None:
                groups.append(Scalar("null"))
                continue
            data = [Scalar("null") if d is None else
                    Map([(Scalar(f), Scalar(str(d[f]))) for f, _ in DATA_FIELDS], flow=True) for d in g.data]
            groups.append(Map([*((Scalar(f), Scalar(str(getattr(g, f)))) for f, _ in GROUP_FIELDS),
                               (Scalar("mMsgData"), Seq(data, flow=not data))]))
        body = [(Scalar("mArray"), Seq(groups, flow=not groups))]
    else:
        raise FormatError("msgset", f"cannot write a {type(m).__name__}")
    items.append((Scalar("riftstone"), Scalar(tag)))
    if name:
        items.append((Scalar("resource"), Scalar(name, "double")))
    return yamlish.emit(Map(items + body), head)


def from_yaml(text: str, source: str | None = None):
    from . import yamlish
    from .yamlish import Map, Scalar

    y = _Y(source)
    doc = yamlish.parse(text, source)
    tag = doc.get("riftstone") if isinstance(doc, Map) else None
    tag = tag.text if isinstance(tag, Scalar) else None
    if tag not in (TAG_MSS, TAG_MSL, TAG_DDO):
        raise ParamError(f"not a Riftstone message set (expected 'riftstone: {TAG_MSS}', '{TAG_DDO}' or '{TAG_MSL}')",
                         1, 1, source)
    if tag == TAG_DDO:
        y.only(doc, ("riftstone", "resource", "mArray"), "the file")
        m = MsgSetDdo()
        for i, gn in enumerate(y.seq(y.get(doc, "mArray", "the file"), None, "mArray")):
            if isinstance(gn, Scalar) and gn.text == "null":
                m.groups.append(None)
                continue
            w = f"group {i}"
            y.only(gn, [f for f, _ in GROUP_FIELDS] + ["mMsgData"], w)
            g = MsgGroup(*(y.int(y.get(gn, f, w), t, f"{w} {f}") for f, t in GROUP_FIELDS))
            for k, dn in enumerate(y.seq(y.get(gn, "mMsgData", w), None, f"{w} mMsgData")):
                if isinstance(dn, Scalar) and dn.text == "null":
                    g.data.append(None)
                    continue
                wk = f"{w} line {k}"
                y.only(dn, [f for f, _ in DATA_FIELDS], wk)
                g.data.append({f: y.int(y.get(dn, f, wk), t, f"{wk} {f}") for f, t in DATA_FIELDS})
            m.groups.append(g)
    else:
        y.only(doc, ("riftstone", "resource", "mpParam"), "the file")
        node = y.get(doc, "mpParam", "the file")
        if tag == TAG_MSL:
            m = MsgSerial([y.int(v, "u16", "mpParam") for v in y.seq(node, None, "mpParam")])
        else:
            m = MsgSet([_from_fields(y, p, PARAM, f"conversation {i}") for i, p in enumerate(y.seq(node, None, "mpParam"))])
    try:
        build(m)
    except FormatError as e:
        raise ParamError(str(e), None, None, source) from None
    return m


def yaml_to_bytes(text: str, source: str | None = None) -> bytes:
    return build(from_yaml(text, source))

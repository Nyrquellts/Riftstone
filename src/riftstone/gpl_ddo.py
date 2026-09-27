"""Dragon's Dogma Online group lists (rLayoutGroupParamList ``.gpl`` version 70): every group, every
area shape, byte-exact.

A stage's ``scr\\st<S>\\etc\\st<S>_<k>.gpl`` (k = e, n, p, t as in Dark Arisen, and DDO's new ``s``) lists
its groups: the layout cells each spans, when it loads (LOT / quest / layout / set-management flags),
its spawn cap and its area shapes.  DDO keeps no enemy list here -- enemies come from the server -- so
Dark Arisen's ``mUnitKindList`` (and its 3-kind limit) does not exist in this format.

The grammar is DDO.exe's own (03.04.003, read with a disassembler; nothing guessed):

  rLayoutGroupParamList::load 0x00AA66C0
    "gpl\\0"  u32 version (70; DDO.exe refuses any other -- Dark Arisen's are 158)
    u32 n (512) + n x u32 mGroupList      read into a fixed 512-slot table without checking n (so any
                                          other n is refused); slot N = 0x80000000 | N when group N is
                                          in the file (bit 31 is what the loader tests), else N or 0
    u32 group count                       = the slots marked (DDO.exe fails with fewer and leaves extras
                                          unloaded, so any other count is refused); 0 ends the file
    9 x u32 pool sizes                    one allocation for everything below, in element counts:
                                          cID, cLifeArea, AreaHitShape, then the zone shapes Area,
                                          Sphere, Cylinder, Cone, AABB, OBB (12/24/64/112/32/64/64/64/112
                                          bytes each; + 640 per group).  Riftstone derives it from the
                                          records: it is their exact usage in every vanilla file.
    one cGroupParam per marked slot, in slot order (cGroupParam::load 0x00AA5E20, vtable slot 9):
      u32  mGroup:9 | DisableSplit << 9 | SetMarkerPos << 11 | ForceOmGroupAllHardware << 13
      u32 n + n x cID {u32 Area, Group, SplitX, SplitZ}           mLayoutIDArray (kept as u16 each)
      u32 LotFlag  u32 FlagNo(u16) | mUnk26(u16)  u32 QuestLayoutFlag  u32 QuestNo  u32 LayoutFlagNo
      u32 SetManageLayoutFlag  u8 SetManageFlagType  u32 SetManageFlagNo  u32 RandomDungeonOnly
      u32 StageSpecified  u32 StageNo  u32 AreaHit
      u32 n + n x shape                                           mAreaHitShapeList
      u32 StrongestInSimpleEvent  s32 MaxCount  u32 LotFlagCleared  u32 LayoutFlagCleared
      u32 SetManageFlagCleared
      u32 n + n x (u32 m + m x shape)                             mLifeAreaArray (cLifeArea.mShapeList)
      s32 KillAreaType
      u32 n + n x shape                                           mKillAreaList
  shape (AreaHitShape::load 0x00425500): str Name (NUL-terminated, Shift-JIS; the game keeps 255 bytes)
      f32 CheckAngle  f32 CheckRange  f32 CheckToward  u8 AngleFlag  u8 TowardFlag  s32 type; type 0: no zone,
      else f32 mDecay  u8 mIsNativeData  and the zone class's loadBinary (vtable slot 13):
        1 nZone::ShapeInfoArea      f32 mHeight, f32 mBottom, 4 x v4 mVertex, v4 mConcaveCrossPos,
                                    u8 mFlgConvex, u32 mConcaveStatus
        2 nZone::ShapeInfoSphere    v4 mSphere (x, y, z, radius)
        3 nZone::ShapeInfoCylinder  v4 Position0, v4 Position1, f32 Radius, 12 bytes pad
        6 nZone::ShapeInfoCone      f32 mHeight, f32 mTopRadius, v4 mPos, f32 mBottomRadius
        8 nZone::ShapeInfoAABB      v4 mAABB.minpos, v4 mAABB.maxpos, f32 mDecayY, f32 mDecayZ,
                                    u8 mIsEnableExtendedDecay
        9 nZone::ShapeInfoOBB       4 x v4 mOBB.coord, v4 mOBB.extent, f32 mDecayY, f32 mDecayZ,
                                    u8 mIsEnableExtendedDecay
      (the jump table at 0x00425860 has no zone for 4, 5, 7: the load fails, so they are refused)

Names: the engine's where DDO.exe registers one -- mGroup, mLayoutIDArray (cID Area/Group/SplitX/SplitZ),
mAreaHitShapeList, mLifeAreaArray/mShapeList, mKillAreaList, AreaHitShape's Name/Check*/…Flag, the zone
classes' members.  The rest are registered with Japanese names (cGroupParam::createPropertyCmn/Set/Kill,
0x00AA43C0/0x00AA47E0/0x00AA46A0), matched to what the loader stores through each property's getter;
they are given ASCII translations (``JAPANESE`` below).  ``MaxCount`` (最大数, "max count") sits where
Dark Arisen keeps its spawn cap mSetCountMax (right after the simple-event flag in both); DDO.exe keeps
only its low 8 bits (bits 2..9 of the word at +0x40), and what it limits in DDO, where the server spawns
enemies, is UNKNOWN in game.  ``FlagNo`` is a u16 property: the loader stores the whole u32,
whose high half (``mUnk26``) is 0xCDCD -- MSVC's uninitialised fill -- in every vanilla group, as is the
cylinder's 12-byte ``pad``.  Bools are stored as the file has them (u32 or u8), so any value survives.

Proved on all 2,210 distinct files in the client (``check_corpus --game ddo --only gpl``): parse -> build
and -> YAML -> back byte-for-byte; 19,376 groups, 25,682 layout-cell links, 14,384 shapes (169 hit, 5,412
in 3,841 life areas, 8,803 kill; 14,147 area, 237 cylinder).  UNKNOWN: what the unnamed bits of the first
word (``mUnk1C``, 0 in vanilla) and ``mUnk26`` hold; the meaning of SetManageFlagType and KillAreaType
values; the other zone types' use in game (only 1 and 3 occur in vanilla).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .errors import FormatError, ParamError
from .params import shown, shown_value

MAGIC = b"gpl\0"
VERSION = 70
SLOTS = 512
IN_FILE = 0x80000000
TAG = "gpl-ddo/1"

# the first word of a group: (name, first bit, width); the other bits are kept whole as mUnk1C
_BITS = (("mGroup", 0, 9), ("DisableSplit", 9, 1), ("SetMarkerPos", 11, 1), ("ForceOmGroupAllHardware", 13, 1))
_KNOWN = 0x1FF | 1 << 9 | 1 << 11 | 1 << 13
_CID = ("Area", "Group", "SplitX", "SplitZ")
_GROUP_A = (("LotFlag", "u32"), ("FlagNo", "u16"), ("mUnk26", "u16"), ("QuestLayoutFlag", "u32"),
            ("QuestNo", "u32"), ("LayoutFlagNo", "u32"), ("SetManageLayoutFlag", "u32"),
            ("SetManageFlagType", "u8"), ("SetManageFlagNo", "u32"), ("RandomDungeonOnly", "u32"),
            ("StageSpecified", "u32"), ("StageNo", "u32"), ("AreaHit", "u32"))
_GROUP_B = (("StrongestInSimpleEvent", "u32"), ("MaxCount", "s32"), ("LotFlagCleared", "u32"),
            ("LayoutFlagCleared", "u32"), ("SetManageFlagCleared", "u32"))
_SHAPE_HEAD = (("CheckAngle", "f32"), ("CheckRange", "f32"), ("CheckToward", "f32"), ("AngleFlag", "u8"),
               ("TowardFlag", "u8"))
_ZONE_HEAD = (("mDecay", "f32"), ("mIsNativeData", "u8"))
ZONES = {
    1: ("nZone::ShapeInfoArea", (("mHeight", "f32"), ("mBottom", "f32"), ("mVertex", "v4x4"),
                                 ("mConcaveCrossPos", "v4"), ("mFlgConvex", "u8"), ("mConcaveStatus", "u32"))),
    2: ("nZone::ShapeInfoSphere", (("mSphere", "v4"),)),
    3: ("nZone::ShapeInfoCylinder", (("Position0", "v4"), ("Position1", "v4"), ("Radius", "f32"), ("pad", "u32x3"))),
    6: ("nZone::ShapeInfoCone", (("mHeight", "f32"), ("mTopRadius", "f32"), ("mPos", "v4"), ("mBottomRadius", "f32"))),
    8: ("nZone::ShapeInfoAABB", (("mAABB.minpos", "v4"), ("mAABB.maxpos", "v4"), ("mDecayY", "f32"),
                                 ("mDecayZ", "f32"), ("mIsEnableExtendedDecay", "u8"))),
    9: ("nZone::ShapeInfoOBB", (("mOBB.coord", "v4x4"), ("mOBB.extent", "v4"), ("mDecayY", "f32"),
                                ("mDecayZ", "f32"), ("mIsEnableExtendedDecay", "u8"))),
}
# the pool header, in file order: what each count allocates
POOLS = ("cGroupParam::cID", "cGroupParam::cLifeArea", "AreaHitShape", "nZone::ShapeInfoArea",
         "nZone::ShapeInfoSphere", "nZone::ShapeInfoCylinder", "nZone::ShapeInfoCone", "nZone::ShapeInfoAABB",
         "nZone::ShapeInfoOBB")
_ZONE_POOL = {1: 3, 2: 4, 3: 5, 6: 6, 8: 7, 9: 8}
# the translated names and DDO.exe's own
JAPANESE = {
    "DisableSplit": "分割を無効にする", "SetMarkerPos": "マーカー座標を設定する",
    "ForceOmGroupAllHardware": "OMグループを全ハードに強制セット（前提は↑「分割を無効にする」がON）",
    "LotFlag": "LOTフラグ", "FlagNo": "フラグNo.", "QuestLayoutFlag": "クエストレイアウトフラグ",
    "QuestNo": "クエストNo.", "LayoutFlagNo": "レイアウトフラグNo.", "SetManageLayoutFlag": "セット管理レイアウトフラグ",
    "SetManageFlagType": "セット管理フラグ種類", "SetManageFlagNo": "セット管理フラグNo.",
    "RandomDungeonOnly": "ランダムダンジョンのみ", "StageSpecified": "ステージ指定", "StageNo": "ステージNo.",
    "AreaHit": "エリアヒット", "StrongestInSimpleEvent": "シンプルイベント中最強", "MaxCount": "最大数",
    "LotFlagCleared": "LOTフラグが消えた", "LayoutFlagCleared": "レイアウトフラグが消えた",
    "SetManageFlagCleared": "セット管理フラグが消えた", "KillAreaType": "KILLエリアタイプ",
}
_SIZE = {"u8": 1, "u16": 2, "u32": 4, "s32": 4, "f32": 4, "v4": 16, "v4x4": 64, "u32x3": 12}
_FMT = {"u8": "<B", "u16": "<H", "u32": "<I", "s32": "<i", "f32": "<I"}
_RANGE = {"u8": (0, 0xFF), "u16": (0, 0xFFFF), "u32": (0, 0xFFFFFFFF), "s32": (-0x80000000, 0x7FFFFFFF),
          "f32": (0, 0xFFFFFFFF)}
_SHAPE_MIN = 1 + 12 + 2 + 4       # an empty name, three floats, two flags, the type
_HEX = {"mUnk1C", "mUnk26", "pad"}
_GROUP_KEYS = (*(k for k, _, _ in _BITS), "mUnk1C", "mLayoutIDArray", *(k for k, _ in _GROUP_A), "mAreaHitShapeList",
               *(k for k, _ in _GROUP_B), "mLifeAreaArray", "KillAreaType", "mKillAreaList")


@dataclass
class GplDdo:
    mGroupList: list = field(default_factory=lambda: [0] * SLOTS)
    groups: list = field(default_factory=list)


def is_ddo_gpl(data: bytes) -> bool:
    return len(data) >= 8 and bytes(data[:4]) == MAGIC and struct.unpack_from("<I", data, 4)[0] == VERSION


def _text(b: bytes):
    """A name: text when Shift-JIS decodes and re-encodes it exactly, else the raw bytes."""
    try:
        t = b.decode("cp932")
        if t.encode("cp932") == b:
            return t
    except UnicodeError:
        pass
    return bytes(b)


# -- binary --------------------------------------------------------------------------------------
class _Reader:
    __slots__ = ("d", "p")

    def __init__(self, data: bytes):
        self.d = data
        self.p = 0

    def take(self, n: int) -> bytes:
        if self.p + n > len(self.d):
            raise FormatError("gpl", "the file ends inside a record", self.p)
        v = self.d[self.p:self.p + n]
        self.p += n
        return v

    def u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def count(self, least: int) -> int:
        """A list length whose items take at least `least` bytes each: bounded by the bytes left."""
        at = self.p
        n = self.u32()
        if n * least > len(self.d) - self.p:
            raise FormatError("gpl", f"a list of {n} is longer than the rest of the file", at)
        return n

    def value(self, t: str):
        if t in _FMT:
            return struct.unpack(_FMT[t], self.take(_SIZE[t]))[0]
        if t == "v4":
            return list(struct.unpack("<4I", self.take(16)))
        if t == "v4x4":
            return [list(struct.unpack("<4I", self.take(16))) for _ in range(4)]
        return list(struct.unpack("<3I", self.take(12)))        # u32x3

    def string(self):
        end = self.d.find(b"\0", self.p)
        if end < 0:
            raise FormatError("gpl", "a name is not terminated", self.p)
        s = _text(self.d[self.p:end])
        self.p = end + 1
        return s


def _read_shape(r: _Reader) -> dict:
    s = {"Name": r.string()}
    for name, t in _SHAPE_HEAD:
        s[name] = r.value(t)
    at = r.p
    s["type"] = kind = r.value("s32")
    if kind:
        if kind not in ZONES:
            raise FormatError("gpl", f"area-shape type {kind} is not one DDO.exe loads "
                                     f"(0 none, {', '.join(f'{k} {c[0][16:]}' for k, c in ZONES.items())})", at)
        for name, t in _ZONE_HEAD + ZONES[kind][1]:
            s[name] = r.value(t)
    return s


def _read_group(r: _Reader) -> dict:
    word = r.u32()
    g = {name: (word >> lo) & ((1 << width) - 1) for name, lo, width in _BITS}
    g["mUnk1C"] = word & ~_KNOWN & 0xFFFFFFFF
    g["mLayoutIDArray"] = [dict(zip(_CID, struct.unpack("<4I", r.take(16)))) for _ in range(r.count(16))]
    for name, t in _GROUP_A:
        g[name] = r.value(t)
    g["mAreaHitShapeList"] = [_read_shape(r) for _ in range(r.count(_SHAPE_MIN))]
    for name, t in _GROUP_B:
        g[name] = r.value(t)
    g["mLifeAreaArray"] = [{"mShapeList": [_read_shape(r) for _ in range(r.count(_SHAPE_MIN))]}
                           for _ in range(r.count(4))]
    g["KillAreaType"] = r.value("s32")
    g["mKillAreaList"] = [_read_shape(r) for _ in range(r.count(_SHAPE_MIN))]
    return g


def group_shapes(g: dict):
    yield from g["mAreaHitShapeList"]
    for life in g["mLifeAreaArray"]:
        yield from life["mShapeList"]
    yield from g["mKillAreaList"]


def pool_counts(groups: list) -> list[int]:
    """The 9-count pool header the groups need (POOLS order): what DDO.exe allocates before loading."""
    n = [0] * len(POOLS)
    for g in groups:
        n[0] += len(g["mLayoutIDArray"])
        n[1] += len(g["mLifeAreaArray"])
        for s in group_shapes(g):
            n[2] += 1
            if s["type"] in _ZONE_POOL:
                n[_ZONE_POOL[s["type"]]] += 1
    return n


def parse(data: bytes) -> GplDdo:
    data = bytes(data)
    if len(data) < 8 or data[:4] != MAGIC:
        raise FormatError("gpl", "not a gpl file (magic)", 0)
    version = struct.unpack_from("<I", data, 4)[0]
    if version != VERSION:
        raise FormatError("gpl", f"version {version}; Dragon's Dogma Online group lists are {VERSION} "
                                 f"(Dark Arisen's are 158)", 4)
    r = _Reader(data)
    r.p = 8
    n = r.u32()
    if n != SLOTS:
        raise FormatError("gpl", f"mGroupList has {n} slots; DDO.exe's table has {SLOTS}", 8)
    glist = list(struct.unpack(f"<{SLOTS}I", r.take(4 * SLOTS)))
    at = r.p
    count = r.u32()
    marked = sum(1 for v in glist if v & IN_FILE)
    if count != marked:
        raise FormatError("gpl", f"the file says {count} group(s) but mGroupList marks {marked}", at)
    groups = []
    if count:
        at = r.p
        header = list(struct.unpack("<9I", r.take(36)))
        groups = [_read_group(r) for _ in range(count)]
        used = pool_counts(groups)
        if header != used:
            raise FormatError("gpl", f"the pool header {header} is not what the records use {used}", at)
    if r.p != len(data):
        raise FormatError("gpl", f"{len(data) - r.p} byte(s) after the last group", r.p)
    return GplDdo(glist, groups)


def _pack(out: bytearray, t: str, v, name: str) -> None:
    try:
        if t in _FMT:
            out += struct.pack(_FMT[t], v)
        elif t == "v4":
            if len(v) != 4:
                raise ParamError(f"{name}: a vector is 4 numbers, not {len(v)}")
            out += struct.pack("<4I", *v)
        elif t == "v4x4":
            if len(v) != 4:
                raise ParamError(f"{name}: 4 vectors, not {len(v)}")
            for row in v:
                _pack(out, "v4", row, name)
        else:
            if len(v) != 3:
                raise ParamError(f"{name}: 3 numbers, not {len(v)}")
            out += struct.pack("<3I", *v)
    except struct.error:
        raise ParamError(f"{name} {shown_value(v)} does not fit its field ({t})") from None


def _name_bytes(v) -> bytes:
    if isinstance(v, (bytes, bytearray)):
        b = bytes(v)
    else:
        try:
            b = v.encode("cp932")
        except UnicodeError:
            raise ParamError(f"the name {v!r} has characters the game's encoding (Shift-JIS) lacks") from None
    if b"\0" in b:
        raise ParamError("a name cannot contain a NUL")
    return b


def _write_shape(out: bytearray, s: dict) -> None:
    out += _name_bytes(s["Name"]) + b"\0"
    for name, t in _SHAPE_HEAD:
        _pack(out, t, s[name], name)
    kind = s["type"]
    _pack(out, "s32", kind, "type")
    if kind:
        if kind not in ZONES:
            raise ParamError(f"area-shape type {shown_value(kind)} is not one DDO.exe loads "
                             f"({', '.join(map(str, [0, *ZONES]))})")
        for name, t in _ZONE_HEAD + ZONES[kind][1]:
            _pack(out, t, s[name], name)


def _write_group(out: bytearray, g: dict) -> None:
    word = g["mUnk1C"]
    if not 0 <= word <= 0xFFFFFFFF or word & _KNOWN:
        raise ParamError(f"mUnk1C 0x{word:x} may only hold the bits the named fields do not "
                         f"(mask 0x{~_KNOWN & 0xFFFFFFFF:08x})")
    for name, lo, width in _BITS:
        v = g[name]
        if not 0 <= v < 1 << width:
            raise ParamError(f"{name} {shown_value(v)} does not fit its {width} bit(s) (0..{(1 << width) - 1})")
        word |= v << lo
    out += struct.pack("<II", word, len(g["mLayoutIDArray"]))
    for c in g["mLayoutIDArray"]:
        for k in _CID:
            _pack(out, "u32", c[k], f"mLayoutIDArray {k}")
    for name, t in _GROUP_A:
        _pack(out, t, g[name], name)
    out += struct.pack("<I", len(g["mAreaHitShapeList"]))
    for s in g["mAreaHitShapeList"]:
        _write_shape(out, s)
    for name, t in _GROUP_B:
        _pack(out, t, g[name], name)
    out += struct.pack("<I", len(g["mLifeAreaArray"]))
    for life in g["mLifeAreaArray"]:
        out += struct.pack("<I", len(life["mShapeList"]))
        for s in life["mShapeList"]:
            _write_shape(out, s)
    _pack(out, "s32", g["KillAreaType"], "KillAreaType")
    out += struct.pack("<I", len(g["mKillAreaList"]))
    for s in g["mKillAreaList"]:
        _write_shape(out, s)


def build(gpl: GplDdo) -> bytes:
    try:
        return _build(gpl)
    except (struct.error, OverflowError, KeyError, TypeError, AttributeError) as e:
        raise ParamError(f"a value does not fit its field: {e}") from None


def _build(gpl: GplDdo) -> bytes:
    if len(gpl.mGroupList) != SLOTS:
        raise ParamError(f"mGroupList has {len(gpl.mGroupList)} slots; DDO.exe's table has {SLOTS}")
    marked = sum(1 for v in gpl.mGroupList if v & IN_FILE)
    if marked != len(gpl.groups):
        raise ParamError(f"mGroupList marks {marked} group(s) but there are {len(gpl.groups)}: slot N is "
                         f"0x80000000 + N for each group N in the file")
    out = bytearray(MAGIC)
    try:
        out += struct.pack(f"<II{SLOTS}II", VERSION, SLOTS, *gpl.mGroupList, len(gpl.groups))
    except struct.error:
        bad = next((v for v in gpl.mGroupList if not (isinstance(v, int) and 0 <= v <= 0xFFFFFFFF)), None)
        raise ParamError(f"the mGroupList slot {shown_value(bad)} does not fit a u32") from None
    if gpl.groups:
        out += struct.pack("<9I", *pool_counts(gpl.groups))
        for g in gpl.groups:
            _write_group(out, g)
    return bytes(out)


# -- YAML ------------------------------------------------------------------------------------------
def _node(t: str, v, hexed: bool = False):
    from .params import f32_bits_text
    from .yamlish import Scalar, Seq

    if t == "f32":
        return Scalar(f32_bits_text(v))
    if t == "v4":
        return Seq([Scalar(f32_bits_text(x)) for x in v], flow=True)
    if t == "v4x4":
        return Seq([_node("v4", row) for row in v])
    if t == "u32x3":
        return Seq([Scalar(f"0x{x:08x}") for x in v], flow=True)
    return Scalar(f"0x{v:x}" if hexed else str(v))


def _name_node(v):
    from .yamlish import Map, Scalar

    if isinstance(v, (bytes, bytearray)):
        return Map([(Scalar("hex"), Scalar(bytes(v).hex(), "double"))], flow=True)
    return Scalar(v, "double")


def _shape_node(s: dict):
    from .yamlish import Map, Scalar

    items = [(Scalar("Name"), _name_node(s["Name"]))]
    items += [(Scalar(k), _node(t, s[k])) for k, t in _SHAPE_HEAD]
    kind = s["type"]
    items.append((Scalar("type"), Scalar(str(kind), comment=ZONES[kind][0] if kind in ZONES else "no zone")))
    if kind in ZONES:
        items += [(Scalar(k), _node(t, s[k], k in _HEX)) for k, t in _ZONE_HEAD + ZONES[kind][1]]
    return Map(items)


def _group_node(g: dict):
    from .yamlish import Map, Scalar, Seq

    items = [(Scalar(k), _node("u32", g[k])) for k, _, _ in _BITS]
    items.append((Scalar("mUnk1C"), _node("u32", g["mUnk1C"], True)))
    items.append((Scalar("mLayoutIDArray"), Seq([Map([(Scalar(k), Scalar(str(c[k]))) for k in _CID], flow=True)
                                                 for c in g["mLayoutIDArray"]])))
    items += [(Scalar(k), _node(t, g[k], k in _HEX)) for k, t in _GROUP_A]
    items.append((Scalar("mAreaHitShapeList"), Seq([_shape_node(s) for s in g["mAreaHitShapeList"]])))
    items += [(Scalar(k), _node(t, g[k])) for k, t in _GROUP_B]
    items.append((Scalar("mLifeAreaArray"), Seq([Map([(Scalar("mShapeList"),
                                                       Seq([_shape_node(s) for s in life["mShapeList"]]))])
                                                 for life in g["mLifeAreaArray"]])))
    items.append((Scalar("KillAreaType"), _node("s32", g["KillAreaType"])))
    items.append((Scalar("mKillAreaList"), Seq([_shape_node(s) for s in g["mKillAreaList"]])))
    return Map(items)


def to_yaml(gpl: GplDdo, name: str | None = None) -> str:
    from . import yamlish
    from .yamlish import Map, Scalar, Seq

    head = ["Riftstone group list (.gpl, Dragon's Dogma Online)" + (f" -- {name}" if name else ""),
            f"{len(gpl.groups)} group(s). DDO lists no enemies here (the server spawns them); a group has its",
            "layout cells, load conditions, MaxCount (max count, where Dark Arisen keeps its spawn cap) and",
            "area shapes (hit, life, kill). mGroupList slot N is 0x80000000 + N when group N is below, in slot order.",
            "Translated names: see JAPANESE in riftstone/gpl_ddo.py. Vectors are [x, y, z, w]; a shape's fields",
            "follow its type. Rebuilds byte-for-byte when untouched."]
    items = [(Scalar("riftstone"), Scalar(TAG))]
    if name:
        items.append((Scalar("resource"), Scalar(name, "double")))
    items.append((Scalar("mGroupList"), Seq([Scalar(f"0x{v:x}") for v in gpl.mGroupList], flow=True)))
    items.append((Scalar("groups"), Seq([_group_node(g) for g in gpl.groups])))
    return yamlish.emit(Map(items), head)


class _Yaml:
    """Typed reads from the parsed YAML tree, with line numbers in every error."""

    def __init__(self, source: str | None):
        self.source = source

    def err(self, msg: str, node=None) -> ParamError:
        return ParamError(msg, getattr(node, "line", None), getattr(node, "col", None), self.source)

    def get(self, m, key: str):
        v = m.get(key)
        if v is None:
            raise self.err(f"'{key}' is missing", m)
        return v

    def scalar(self, node, key: str):
        from .yamlish import Scalar
        if not isinstance(node, Scalar):
            raise self.err(f"'{key}' holds a list or mapping where a single value belongs", node)
        return node

    def int(self, node, key: str, t: str) -> int:
        sc = self.scalar(node, key)
        try:
            v = int(sc.text.strip().replace("_", ""), 0)
        except ValueError:
            raise self.err(f"'{key}' must be a whole number, not {sc.text!r}", sc) from None
        lo, hi = _RANGE[t]
        if not lo <= v <= hi:
            raise self.err(f"'{key}' {shown(sc.text)} does not fit a {t} ({lo}..{hi})", sc)
        return v

    def f32(self, node, key: str) -> int:
        from .params import f32_bits
        sc = self.scalar(node, key)
        try:
            return f32_bits(sc.text)
        except (ValueError, OverflowError):
            raise self.err(f"'{key}' {sc.text!r} is not a 32-bit float", sc) from None

    def seq(self, node, key: str, n: int | None = None) -> list:
        from .yamlish import Seq
        if not isinstance(node, Seq):
            raise self.err(f"'{key}' must be a list", node)
        if n is not None and len(node.items) != n:
            raise self.err(f"'{key}' needs {n} item(s), not {len(node.items)}", node)
        return node.items

    def block(self, node, what: str, allowed=None):
        """A mapping; with `allowed`, a key the format has no place for is refused (a typo would vanish)."""
        from .yamlish import Map
        if not isinstance(node, Map):
            raise self.err(f"{what} must be a block of fields", node)
        for k, _v in node.items if allowed is not None else ():
            if k.text not in allowed:
                raise self.err(f"{what} has no field '{k.text}'", k)
        return node

    def value(self, m, key: str, t: str):
        node = self.get(m, key)
        if t == "f32":
            return self.f32(node, key)
        if t == "v4":
            return [self.f32(x, key) for x in self.seq(node, key, 4)]
        if t == "v4x4":
            return [[self.f32(x, key) for x in self.seq(row, key, 4)] for row in self.seq(node, key, 4)]
        if t == "u32x3":
            return [self.int(x, key, "u32") for x in self.seq(node, key, 3)]
        return self.int(node, key, t)

    def name(self, m):
        from .yamlish import Map
        node = self.get(m, "Name")
        if isinstance(node, Map):
            self.block(node, "'Name'", ("hex",))
            h = node.get("hex")
            try:
                return bytes.fromhex(self.scalar(h, "Name").text)
            except (ValueError, ParamError):
                raise self.err("'Name': hex must be pairs of hex digits", node) from None
        text = self.scalar(node, "Name").text
        if "\0" in text:
            raise self.err("a name cannot contain a NUL", node)
        try:
            text.encode("cp932")
        except UnicodeError:
            raise self.err(f"the name {text!r} has characters the game's encoding (Shift-JIS) lacks", node) from None
        return text

    def shape(self, node) -> dict:
        m = self.block(node, "an area shape")
        kind = self.value(m, "type", "s32")
        if kind and kind not in ZONES:
            raise self.err(f"area-shape type {kind} is not one DDO.exe loads "
                           f"(0 none, {', '.join(f'{k} {c[0][16:]}' for k, c in ZONES.items())})", m.get("type"))
        zone = _ZONE_HEAD + ZONES[kind][1] if kind else ()
        self.block(m, f"a type {kind} ({ZONES[kind][0] if kind else 'no zone'}) shape",
                   {"Name", "type", *(k for k, _ in _SHAPE_HEAD + zone)})
        s = {"Name": self.name(m)}
        for k, t in _SHAPE_HEAD:
            s[k] = self.value(m, k, t)
        s["type"] = kind
        for k, t in zone:
            s[k] = self.value(m, k, t)
        return s

    def group(self, node) -> dict:
        m = self.block(node, "a group", _GROUP_KEYS)
        g = {}
        for k, _lo, width in _BITS:
            g[k] = self.int(self.get(m, k), k, "u32")
            if g[k] >= 1 << width:
                raise self.err(f"'{k}' {g[k]} does not fit its {width} bit(s) (0..{(1 << width) - 1})", m.get(k))
        g["mUnk1C"] = self.int(self.get(m, "mUnk1C"), "mUnk1C", "u32")
        if g["mUnk1C"] & _KNOWN:
            raise self.err(f"'mUnk1C' may only hold the bits the named fields do not "
                           f"(mask 0x{~_KNOWN & 0xFFFFFFFF:08x})", m.get("mUnk1C"))
        g["mLayoutIDArray"] = [{k: self.int(self.get(c, k), k, "u32") for k in _CID}
                               for c in (self.block(x, "a layout cell", _CID) for x in
                                         self.seq(self.get(m, "mLayoutIDArray"), "mLayoutIDArray"))]
        for k, t in _GROUP_A:
            g[k] = self.value(m, k, t)
        g["mAreaHitShapeList"] = [self.shape(x)
                                  for x in self.seq(self.get(m, "mAreaHitShapeList"), "mAreaHitShapeList")]
        for k, t in _GROUP_B:
            g[k] = self.value(m, k, t)
        g["mLifeAreaArray"] = [
            {"mShapeList": [self.shape(x) for x in self.seq(self.get(life, "mShapeList"), "mShapeList")]}
            for life in (self.block(x, "a life area", ("mShapeList",)) for x in
                         self.seq(self.get(m, "mLifeAreaArray"), "mLifeAreaArray"))]
        g["KillAreaType"] = self.value(m, "KillAreaType", "s32")
        g["mKillAreaList"] = [self.shape(x) for x in self.seq(self.get(m, "mKillAreaList"), "mKillAreaList")]
        return g


def from_yaml(text: str, source: str | None = None) -> GplDdo:
    from . import yamlish
    from .yamlish import Map, Scalar

    doc = yamlish.parse(text, source)
    tag = doc.get("riftstone") if isinstance(doc, Map) else None
    if not isinstance(tag, Scalar) or tag.text != TAG:
        raise ParamError(f"not a Riftstone DDO group list (expected 'riftstone: {TAG}')", 1, 1, source)
    y = _Yaml(source)
    y.block(doc, "the file", ("riftstone", "resource", "mGroupList", "groups"))
    glist = [y.int(x, "mGroupList", "u32") for x in y.seq(y.get(doc, "mGroupList"), "mGroupList", SLOTS)]
    groups = [y.group(x) for x in y.seq(y.get(doc, "groups"), "groups")]
    gpl = GplDdo(glist, groups)
    marked = sum(1 for v in glist if v & IN_FILE)
    if marked != len(groups):
        raise ParamError(f"mGroupList marks {marked} group(s) but 'groups' has {len(groups)}: slot N is "
                         f"0x80000000 + N for each group N", doc.get("groups").line, None, source)
    return gpl


def yaml_to_bytes(text: str, source: str | None = None) -> bytes:
    return build(from_yaml(text, source))


# -- summary -------------------------------------------------------------------------------------
def summary(gpl: GplDdo, limit: int = 40) -> str:
    """Groups at a glance (for inspect): spawn cap, layout cells, shapes, and what they wait for."""
    shapes = sum(1 for g in gpl.groups for _ in group_shapes(g))
    cells = sum(len(g["mLayoutIDArray"]) for g in gpl.groups)
    lines = [f"{len(gpl.groups)} group(s), {cells} layout cell(s), {shapes} area shape(s); "
             f"enemies come from the server, so no unit list:"]
    for g in gpl.groups[:limit]:
        why = []
        if g["QuestNo"]:
            why.append(f"quest {g['QuestNo']}")
        if g["LotFlag"]:
            why.append(f"LOT flag {g['FlagNo']}")
        if g["LayoutFlagNo"]:
            why.append(f"layout flag {g['LayoutFlagNo']}")
        if g["SetManageFlagType"] or g["SetManageFlagNo"]:
            why.append(f"set flag {g['SetManageFlagType']}:{g['SetManageFlagNo']}")
        life = sum(len(x["mShapeList"]) for x in g["mLifeAreaArray"])
        lines.append(f"  group {g['mGroup']:<4} max {g['MaxCount']:<4} cells {len(g['mLayoutIDArray']):<3} "
                     f"shapes hit {len(g['mAreaHitShapeList'])} life {life} kill {len(g['mKillAreaList'])}"
                     + (f"  ({', '.join(why)})" if why else ""))
    if len(gpl.groups) > limit:
        lines.append(f"  ... and {len(gpl.groups) - limit} more")
    return "\n".join(lines)

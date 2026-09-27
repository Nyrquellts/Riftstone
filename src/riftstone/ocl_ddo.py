"""Dragon's Dogma Online object collision (rObjCollision ``.ocl``, magic ``COL\\0``): hit shapes and
attack tables, every field, byte-exact.

Dark Arisen's ``.ocl`` (``ocl.py``, magic 0x20121225) is a different format.  DDO's is three of its
table resources (DDO.exe's rTbl2Base family; the symbol map calls the base rJobBaseParam) in one
stream.  The grammar is DDO.exe's own (client 03.04.003), read from its loaders:

  rObjCollision::load 0x00ABA5F0   "COL\\0", u16 version 0x72, u16 read and ignored (rObjCollision::save
                                   0x00ABA860 writes 0 there); then three tables, each "u32 version
                                   0x72" (read by 0x00AD1FE0, compared with the class's slot 21) and
                                   its body (slot 22):
  rCollIndex   (mpCollIndex)       u32 count, count x cCollIndex (19 bytes; reader 0x00A60B20)
  rCollNode    (mpCollNode)        u32 mUID, u32 count, count x cCollNode (reader 0x00A612A0): the node's
                                   own rCollGeom table -- u32 version 0x72, u32 count, count x cCollGeom
                                   (73 bytes; reader 0x00A600E0) -- then mAttr u32, mNodeID u16, mIndex
                                   u16, mColNodeFlag u32, mHitCollisionFlag u32
  rAttackParam (mpAttackParam)     u32 mUID, u32 count, count x cAttackParam (275 bytes; reader 0x00A50940)

Records, in file order (member names from DDO.exe's createProperty, ``re/out/props.json``):

  cCollIndex    mNode mAttack mLinkID mNode2 mAttack2 mLinkID2 mNode3 mAttack3 mLinkID3 (s16), mLinkTop (bool)
  cCollGeom     mShape mOption mScaleOption mPriority mLayer mLayer2 (u8), mIndex (u16), mJnt0 mJnt1 (s16),
                mRegionNo (u16), mRangeCheckBaseJnt (s16), mRadius (f32), mAngle0 mAngle1 (2 x f32),
                mOffset0 mOffset1 mExtent (3 x f32), mIsUseData (bool)
  cAttackParam  94 fields of u8/bool/u16/u32/u64/f32.  DDO.exe registers no property for this class
                (its createProperty is the empty default), so each is ``mUnkXX`` = its offset in the
                loaded 0x120-byte record, which the reader writes in a scrambled order.

mShape: 0 sphere, 1 capsule, 2 oriented box.  The cHitGeom update (0x004F1C00, from its mpSrcGeom)
switches on it and fills its geometry with 0x004F1BD0 (a centre and radius), 0x004F1AA0 (two points and
a radius) or 0x004F1B40 (a 4x4 matrix and mExtent) -- the layouts of DDO.exe's MtGeomSphere, MtGeomCapsule
and MtGeomOBB (constructors 0x0095F804, 0x0095F660, 0x0095F750).  The corpus agrees: 22,612 spheres
(mJnt1 = -1 in 97%), 20,023 capsules, 23 boxes (none keeps the default 20,20,20 mExtent; 20 have radius 0).

A bool is the byte the engine tests for non-zero (kept as the byte); an f32 is kept as its exact 32-bit
pattern.  mUID (rCollNode and rAttackParam, u32 at +0x70 of each) is the key sObjCollision registers
the two tables under (0x00BBB970 calls 0x00BBB870 / 0x00BBB770 with it; a key already registered only
has its count raised); in every vanilla file both equal the jamcrc (inverted CRC-32) of the resource
path (``path_uid``).

Proved on all 1,176 distinct ``.ocl`` of the client (tests/test_ocl_ddo.py reads them all): 42,726
index entries, 40,858 nodes (18,588 without shapes), 42,658 hit shapes, 21,293 attack params; parse ->
build and the YAML round trip reproduce every file byte for byte, and every file ends exactly where its
last table does.  Measured invariants: a node's and a shape's mIndex equal their position (all 40,858
and 42,658), and so does an attack's mUnk04 (21,293 of 21,293).

UNKNOWN: what mNode / mAttack number (mNode is below the file's node count in 23,236 of 23,256
references, mAttack below its attack count in only 10,717 of 13,372, so they are not simply positions in
this file's tables); every cAttackParam field's meaning; what the game does with two files registering
the same mUID; any in-game effect of an edit.  The engine tolerates a short or long stream (reads past
the end return 0, trailing bytes are ignored); Riftstone refuses both, and any version other than 0x72
(the engine rejects the file, or skips or abandons that table and reads the rest out of step).
Dark Arisen's rObjCollision (DDDA.exe props, PS3 build) holds the same roles in one class -- Group/Node
shapes (mShape, mRadius, mJoint0/1, mOffset0/1, mRegionId), Attack and SeqIndex -- none of DDO's four
record classes exists in the PS3 build.
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field

from .errors import FormatError, ParamError

MAGIC = b"COL\0"
VERSION = 0x72          # the header's u16 and every table's u32
TAG = "ocl-ddo/1"
_FMT = "ocl"
SHAPES = {0: "sphere", 1: "capsule", 2: "oriented box"}     # cCollGeom.mShape (see above)

# (name, type, offset of the member in the loaded record), in file order.
INDEX_FIELDS = (
    ("mNode", "s16", 0x04), ("mAttack", "s16", 0x06), ("mLinkID", "s16", 0x08),
    ("mNode2", "s16", 0x0C), ("mAttack2", "s16", 0x0E), ("mLinkID2", "s16", 0x10),
    ("mNode3", "s16", 0x12), ("mAttack3", "s16", 0x14), ("mLinkID3", "s16", 0x16),
    ("mLinkTop", "bool", 0x0A),
)
GEOM_FIELDS = (
    ("mShape", "u8", 0x04), ("mOption", "u8", 0x05), ("mScaleOption", "u8", 0x06), ("mPriority", "u8", 0x07),
    ("mLayer", "u8", 0x08), ("mLayer2", "u8", 0x09), ("mIndex", "u16", 0x0A), ("mJnt0", "s16", 0x0C),
    ("mJnt1", "s16", 0x0E), ("mRegionNo", "u16", 0x10), ("mRangeCheckBaseJnt", "s16", 0x12),
    ("mRadius", "f32", 0x14), ("mAngle0", "v2", 0x18), ("mAngle1", "v2", 0x20),
    ("mOffset0", "v3", 0x30), ("mOffset1", "v3", 0x40), ("mExtent", "v3", 0x50),
    ("mIsUseData", "bool", 0x60),
)
# cCollNode's own members, read after its rCollGeom table
NODE_FIELDS = (
    ("mAttr", "u32", 0x14), ("mNodeID", "u16", 0x08), ("mIndex", "u16", 0x0A),
    ("mColNodeFlag", "u32", 0x0C), ("mHitCollisionFlag", "u32", 0x10),
)
# cAttackParam, in the order 0x00A50940 reads it: (offset in the loaded record, type)
_ATTACK_ORDER = (
    (0x04, "u16"), (0x06, "u16"), (0x08, "f32"), (0x0C, "u8"), (0x10, "f32"), (0x119, "bool"), (0x0E, "u16"),
    (0x14, "f32"), (0x0D, "u8"), (0x18, "f32"), (0x1C, "f32"), (0x20, "u8"), (0x24, "f32"), (0x21, "u8"),
    (0x28, "f32"), (0x2C, "f32"), (0x22, "u8"), (0x30, "f32"), (0x34, "f32"), (0x38, "f32"), (0x3C, "f32"),
    (0x40, "f32"), (0x44, "f32"), (0x48, "f32"), (0x4C, "f32"), (0x50, "f32"), (0x23, "u8"), (0x54, "u8"),
    (0x58, "f32"), (0x5C, "f32"), (0x60, "f32"), (0x64, "f32"), (0x55, "u8"), (0x6A, "u16"), (0x68, "u16"),
    (0x6C, "f32"), (0x70, "f32"), (0x57, "u8"), (0x74, "f32"), (0x78, "u32"), (0x56, "u8"), (0x7C, "f32"),
    (0x80, "f32"), (0x84, "f32"), (0x88, "f32"), (0x8C, "u8"), (0x90, "f32"), (0x94, "f32"), (0x98, "f32"),
    (0x9C, "f32"), (0x8D, "u8"), (0xA0, "f32"), (0xA4, "f32"), (0xA8, "f32"), (0xAC, "f32"), (0x8E, "u8"),
    (0xB0, "f32"), (0xB4, "f32"), (0xB8, "f32"), (0xBC, "f32"), (0xC0, "u32"), (0xDA, "u16"), (0xC4, "u32"),
    (0xD8, "u16"), (0xC8, "u32"), (0xDC, "u16"), (0xCC, "u32"), (0xDE, "u16"), (0xD0, "u32"), (0xE0, "u16"),
    (0xD4, "u32"), (0x110, "u64"), (0xE8, "u32"), (0xE4, "u8"), (0xEC, "f32"), (0xE2, "u16"), (0xE5, "u8"),
    (0x11A, "bool"), (0xE6, "u16"), (0xF0, "u16"), (0xF4, "u8"), (0xF5, "u8"), (0xF2, "u16"), (0xF6, "u8"),
    (0xF7, "u8"), (0xF8, "u8"), (0xFA, "u16"), (0x100, "u64"), (0xF9, "u8"), (0x8F, "u8"), (0x108, "u16"),
    (0x10C, "u32"), (0x118, "u8"), (0x10A, "u16"),
)
ATTACK_FIELDS = tuple((f"mUnk{off:02X}", t, off) for off, t in _ATTACK_ORDER)

_CODE = {"u8": "B", "bool": "B", "u16": "H", "s16": "h", "u32": "I", "u64": "Q", "f32": "I", "v2": "2I", "v3": "3I"}
_RANGE = {"u8": (0, 0xFF), "bool": (0, 0xFF), "u16": (0, 0xFFFF), "s16": (-0x8000, 0x7FFF),
          "u32": (0, 0xFFFFFFFF), "u64": (0, 0xFFFFFFFFFFFFFFFF), "f32": (0, 0xFFFFFFFF)}
_WIDTH = {"v2": 2, "v3": 3}
_U32 = struct.Struct("<I")


class _Record:
    """One fixed-size record class: file order, a compiled struct, dict <-> bytes."""

    def __init__(self, cls: str, fields):
        self.cls = cls
        self.fields = tuple(fields)
        self.names = tuple(n for n, _, _ in self.fields)
        self.st = struct.Struct("<" + "".join(_CODE[t] for _, t, _ in self.fields))
        self.size = self.st.size
        self.flat = not any(t in _WIDTH for _, t, _ in self.fields)

    def unpack(self, data, p: int) -> dict:
        vals = self.st.unpack_from(data, p)
        if self.flat:
            return dict(zip(self.names, vals))
        out, i = {}, 0
        for name, t, _ in self.fields:
            w = _WIDTH.get(t)
            if w:
                out[name] = vals[i:i + w]
                i += w
            else:
                out[name] = vals[i]
                i += 1
        return out

    def pack(self, rec, where: str) -> bytes:
        try:
            if self.flat:
                return self.st.pack(*[rec[n] for n in self.names])
            vals = []
            for name, t, _ in self.fields:
                v = rec[name]
                if t in _WIDTH:
                    if len(v) != _WIDTH[t]:
                        raise ValueError
                    vals.extend(v)
                else:
                    vals.append(v)
            return self.st.pack(*vals)
        except (KeyError, TypeError, ValueError, struct.error, OverflowError):
            raise FormatError(_FMT, f"{where}: {self._fault(rec)}") from None

    def _fault(self, rec) -> str:
        if not isinstance(rec, dict):
            return f"a {self.cls} record is a mapping of its fields"
        for name, t, _ in self.fields:
            if name not in rec:
                return f"{self.cls}.{name} is missing"
            v = rec[name]
            if t in _WIDTH:
                n = _WIDTH[t]
                if not isinstance(v, (tuple, list)) or len(v) != n:
                    return f"{self.cls}.{name} needs {n} numbers (32-bit float patterns)"
                bad = [x for x in v if not isinstance(x, int) or not 0 <= x <= 0xFFFFFFFF]
                if bad:
                    return f"{self.cls}.{name}: {bad[0]!r} is not a 32-bit float pattern"
                continue
            lo, hi = _RANGE[t]
            if not isinstance(v, int) or not lo <= v <= hi:
                what = "a 32-bit float pattern" if t == "f32" else f"a {t} ({lo}..{hi})"
                return f"{self.cls}.{name}: {v!r} is not {what}"
        return f"a {self.cls} record does not pack"


INDEX = _Record("cCollIndex", INDEX_FIELDS)
GEOM = _Record("cCollGeom", GEOM_FIELDS)
NODE = _Record("cCollNode", NODE_FIELDS)
ATTACK = _Record("cAttackParam", ATTACK_FIELDS)
_NODE_MIN = 8 + NODE.size          # an rCollGeom header (version, count) and the node's own members


@dataclass
class OclDdo:
    """The three tables.  A record is a dict of its fields (f32 as its 32-bit pattern, a vector as a tuple
    of them); a node dict also holds ``"geoms"``, its list of cCollGeom hit shapes."""

    index: list[dict] = field(default_factory=list)      # rCollIndex: cCollIndex records
    nodes: list[dict] = field(default_factory=list)      # rCollNode: cCollNode records (+ "geoms")
    attacks: list[dict] = field(default_factory=list)    # rAttackParam: cAttackParam records
    node_uid: int = 0                                    # rCollNode mUID
    attack_uid: int = 0                                  # rAttackParam mUID
    pad: int = 0                                         # the header's second u16 (0 in every file)

    @property
    def shape_count(self) -> int:
        return sum(len(n["geoms"]) for n in self.nodes)

    def counts(self, kinds: bool = False) -> str:
        """'3 index entries, 2 nodes with 4 hit shapes, 1 attack param' (kinds: the shapes by kind too)."""
        shapes = _n(self.shape_count, "hit shape")
        if kinds and self.shape_count:
            from collections import Counter
            per = Counter(g["mShape"] for n in self.nodes for g in n["geoms"])
            shapes += " (" + ", ".join(f"{v} {SHAPES.get(k, f'mShape {k}')}" for k, v in sorted(per.items())) + ")"
        return (f"{_n(len(self.index), 'index entry', 'index entries')}, {_n(len(self.nodes), 'node')} with "
                f"{shapes}, {_n(len(self.attacks), 'attack param')}")


def _n(k: int, one: str, many: str | None = None) -> str:
    return f"{k:,} {one if k == 1 else (many or one + 's')}"


# -- binary -----------------------------------------------------------------------------------------
class _Reader:
    def __init__(self, data: bytes, p: int):
        self.d = data
        self.p = p

    def u32(self, what: str) -> int:
        if self.p + 4 > len(self.d):
            raise FormatError(_FMT, f"the file ends inside {what}", self.p)
        (v,) = _U32.unpack_from(self.d, self.p)
        self.p += 4
        return v

    def version(self, table: str) -> None:
        at = self.p
        v = self.u32(f"the {table} version")
        if v != VERSION:
            raise FormatError(_FMT, f"{table} version {v:#x}; DDO.exe loads only {VERSION:#x}", at)

    def count(self, size: int, what: str) -> int:
        at = self.p
        n = self.u32(f"the {what} count")
        if n * size > len(self.d) - self.p:
            raise FormatError(_FMT, f"{n} {what} cannot fit in the {len(self.d) - self.p} bytes left", at)
        return n

    def records(self, rec: _Record, n: int) -> list[dict]:
        out = [rec.unpack(self.d, self.p + i * rec.size) for i in range(n)]
        self.p += n * rec.size
        return out


def parse(data: bytes) -> OclDdo:
    data = bytes(data)
    if len(data) < 8 or data[:4] != MAGIC:
        raise FormatError(_FMT, "not a Dragon's Dogma Online collision file (magic COL\\0)", 0)
    version, pad = struct.unpack_from("<HH", data, 4)
    if version != VERSION:
        raise FormatError(_FMT, f"version {version:#x}; DDO.exe loads only {VERSION:#x}", 4)
    r = _Reader(data, 8)
    o = OclDdo(pad=pad)
    r.version("rCollIndex")
    o.index = r.records(INDEX, r.count(INDEX.size, "index entries"))
    r.version("rCollNode")
    o.node_uid = r.u32("the rCollNode mUID")
    for _ in range(r.count(_NODE_MIN, "nodes")):
        r.version("rCollGeom")
        geoms = r.records(GEOM, r.count(GEOM.size, "hit shapes"))
        if r.p + NODE.size > len(data):
            raise FormatError(_FMT, f"the file ends inside node {len(o.nodes)}", r.p)
        node = NODE.unpack(data, r.p)
        r.p += NODE.size
        node["geoms"] = geoms
        o.nodes.append(node)
    r.version("rAttackParam")
    o.attack_uid = r.u32("the rAttackParam mUID")
    o.attacks = r.records(ATTACK, r.count(ATTACK.size, "attack params"))
    if r.p != len(data):
        raise FormatError(_FMT, f"{len(data) - r.p} bytes after the last table", r.p)
    return o


def build(o: OclDdo) -> bytes:
    for what, v, hi in (("pad", o.pad, 0xFFFF), ("rCollNode mUID", o.node_uid, 0xFFFFFFFF),
                        ("rAttackParam mUID", o.attack_uid, 0xFFFFFFFF)):
        if not isinstance(v, int) or not 0 <= v <= hi:
            raise FormatError(_FMT, f"{what}: {v!r} is out of range (0..{hi})")
    out = bytearray(MAGIC + struct.pack("<HHII", VERSION, o.pad, VERSION, len(o.index)))
    for i, rec in enumerate(o.index):
        out += INDEX.pack(rec, f"index entry {i}")
    out += struct.pack("<III", VERSION, o.node_uid, len(o.nodes))
    for i, node in enumerate(o.nodes):
        geoms = node.get("geoms") if isinstance(node, dict) else None
        if not isinstance(geoms, list):
            raise FormatError(_FMT, f"node {i}: 'geoms' must be a list of hit shapes")
        out += struct.pack("<II", VERSION, len(geoms))
        for j, g in enumerate(geoms):
            out += GEOM.pack(g, f"node {i} shape {j}")
        out += NODE.pack(node, f"node {i}")
    out += struct.pack("<III", VERSION, o.attack_uid, len(o.attacks))
    for i, rec in enumerate(o.attacks):
        out += ATTACK.pack(rec, f"attack param {i}")
    return bytes(out)


def read(path) -> OclDdo:
    from pathlib import Path
    return parse(Path(path).read_bytes())


def is_ddo_ocl(data: bytes) -> bool:
    return bytes(data[:4]) == MAGIC


def path_uid(name: str) -> int:
    """The mUID vanilla files carry: the inverted CRC-32 of the resource path (no extension)."""
    n = name[:-4] if name.lower().endswith(".ocl") else name
    return zlib.crc32(n.encode("latin-1", "replace")) ^ 0xFFFFFFFF


def f32(bits: int) -> float:
    return struct.unpack("<f", _U32.pack(bits & 0xFFFFFFFF))[0]


def f32_bits(value: float) -> int:
    """The pattern the game would store for `value` (ValueError if it does not fit a float32)."""
    from .params import f32_bits as _bits
    return _bits(repr(float(value)))


def _vec(v) -> str:
    return "[" + ", ".join(f"{f32(b):g}" for b in v) + "]"


def _shape_text(g: dict) -> str:
    """One hit shape in a line: its kind, then the fields that kind uses."""
    k = g["mShape"]
    if k == 1:
        return (f"capsule       r={f32(g['mRadius']):g}  joints {g['mJnt0']}/{g['mJnt1']}  "
                f"{_vec(g['mOffset0'])} to {_vec(g['mOffset1'])}")
    if k == 2:
        return f"oriented box  extent {_vec(g['mExtent'])}  joint {g['mJnt0']}  at {_vec(g['mOffset0'])}"
    kind = "sphere" if k == 0 else f"mShape {k}"
    return f"{kind:<13} r={f32(g['mRadius']):g}  joint {g['mJnt0']}  at {_vec(g['mOffset0'])}"


def summary(o: OclDdo, limit: int = 40) -> str:
    """A few lines for ``inspect``: the counts, then the hit shapes."""
    lines = [o.counts(kinds=True)]
    shown = 0
    for i, n in enumerate(o.nodes):
        for j, g in enumerate(n["geoms"]):
            if shown == limit:
                lines.append(f"  ... {o.shape_count - limit} more")
                return "\n".join(lines)
            lines.append(f"  node {i:<4} geom {j:<2} {_shape_text(g)}")
            shown += 1
    return "\n".join(lines)


# -- YAML -------------------------------------------------------------------------------------------
_HEX = {"mAttr", "mColNodeFlag", "mHitCollisionFlag"}


def _node_value(name: str, t: str, v):
    from .params import f32_bits_text
    from .yamlish import Scalar, Seq

    if t == "f32":
        return Scalar(f32_bits_text(v))
    if t in _WIDTH:
        return Seq([Scalar(f32_bits_text(b)) for b in v], flow=True)
    if name in _HEX:
        return Scalar(f"0x{v:x}")
    if name == "mShape":
        return Scalar(str(v), comment=SHAPES.get(v, "not a shape DDO.exe handles"))
    return Scalar(str(v))


def _rec_map(rec: _Record, r: dict, flow: bool = False):
    from .yamlish import Map, Scalar
    return Map([(Scalar(n), _node_value(n, t, r[n])) for n, t, _ in rec.fields], flow=flow)


def to_yaml(o: OclDdo, name: str | None = None) -> str:
    from . import yamlish
    from .yamlish import Map, Scalar, Seq

    label = "".join(c if c.isprintable() else "?" for c in name) if name else ""   # a comment line stays one line
    head = ["Riftstone object collision (.ocl, Dragon's Dogma Online)" + (f" -- {label}" if name else ""),
            f"{o.counts()}. Field names are DDO.exe's own.",
            "A node's geoms are its hit shapes: mShape (0 sphere, 1 capsule, 2 oriented box), mJnt0/mJnt1,",
            "mOffset0/mOffset1 [x, y, z], mRadius, mExtent.",
            "cAttackParam fields have no names in DDO.exe: mUnkXX is the offset in the loaded record.",
            "mIndex (and an attack's mUnk04) equals the record's position in every vanilla file; keep it so",
            "when you add or remove records. Floats are exact; an untouched file rebuilds byte-for-byte."]

    def uid(v: int):
        note = "jamcrc of the resource path, as in every vanilla file"
        if name:
            want = path_uid(name)
            note = note if v == want else f"vanilla files use the jamcrc of the path: 0x{want:08x}"
        return Scalar(f"0x{v:08x}", comment=note)

    items = [(Scalar("riftstone"), Scalar(TAG))]
    if name:
        items.append((Scalar("resource"), Scalar(name, "double")))
    if o.pad:
        items.append((Scalar("pad"), Scalar(str(o.pad))))
    items.append((Scalar("rCollIndex"), Seq([_rec_map(INDEX, r, flow=True) for r in o.index], flow=not o.index)))
    nodes = []
    for n in o.nodes:
        m = _rec_map(NODE, n)
        m.items.append((Scalar("geoms"), Seq([_rec_map(GEOM, g) for g in n["geoms"]], flow=not n["geoms"])))
        nodes.append(m)
    items.append((Scalar("rCollNode"), Map([(Scalar("mUID"), uid(o.node_uid)),
                                             (Scalar("nodes"), Seq(nodes, flow=not nodes))])))
    attacks = [_rec_map(ATTACK, a) for a in o.attacks]
    items.append((Scalar("rAttackParam"), Map([(Scalar("mUID"), uid(o.attack_uid)),
                                                (Scalar("attacks"), Seq(attacks, flow=not attacks))])))
    return yamlish.emit(Map(items), head)


def _where(node, source):
    return getattr(node, "line", None), getattr(node, "col", None), source


def _scalar(node, what: str, where):
    from .yamlish import Scalar
    if not isinstance(node, Scalar):
        raise ParamError(f"{what} must be a single value, not a list or mapping", *where)
    return node.text.strip()


def _int(node, what: str, t: str, source) -> int:
    from .params import shown
    where = _where(node, source)
    text = _scalar(node, what, where)
    try:
        v = int(text, 0)
    except ValueError:
        raise ParamError(f"{what} must be a whole number, not {shown(text)!r}", *where) from None
    lo, hi = _RANGE[t]
    if not lo <= v <= hi:
        raise ParamError(f"{what}: {shown(text)} is out of range for a {t} ({lo}..{hi})", *where)
    return v


def _float(node, what: str, source) -> int:
    from .params import f32_bits
    where = _where(node, source)
    text = _scalar(node, what, where)
    try:
        return f32_bits(text)
    except (ValueError, OverflowError):
        raise ParamError(f"{what}: {text!r} is not a 32-bit float", *where) from None


def _record(rec: _Record, node, what: str, source, extra: tuple = ()) -> dict:
    from .yamlish import Map, Seq
    if not isinstance(node, Map):
        raise ParamError(f"{what} is a mapping of the {rec.cls} fields", *_where(node, source))
    known = set(rec.names) | set(extra)
    for k, _ in node.items:
        if k.text not in known:
            raise ParamError(f"{what}: '{k.text}' is not a {rec.cls} field", k.line, k.col, source)
    out = {}
    for name, t, _ in rec.fields:
        v = node.get(name)
        label = f"{what} {name}"
        if v is None:
            raise ParamError(f"{what}: '{name}' is missing", *_where(node, source))
        if t in _WIDTH:
            n = _WIDTH[t]
            if not isinstance(v, Seq) or len(v.items) != n:
                raise ParamError(f"{label} needs {n} numbers, like [0.0, 0.0" + ", 0.0" * (n - 2) + "]",
                                 *_where(v, source))
            out[name] = tuple(_float(x, label, source) for x in v.items)
        elif t == "f32":
            out[name] = _float(v, label, source)
        else:
            out[name] = _int(v, label, t, source)
    return out


def _list(node, what: str, source) -> list:
    from .yamlish import Seq
    if not isinstance(node, Seq):
        raise ParamError(f"'{what}' must be a list", *_where(node, source))
    return node.items


def _table(doc, key: str, list_key: str, source):
    from .yamlish import Map
    t = doc.get(key)
    if not isinstance(t, Map):
        raise ParamError(f"'{key}' is missing or not a mapping", *_where(t, source))
    for k, _ in t.items:
        if k.text not in ("mUID", list_key):
            raise ParamError(f"'{key}' holds only mUID and {list_key}, not '{k.text}'", k.line, k.col, source)
    u = t.get("mUID")
    if u is None:
        raise ParamError(f"'{key}' needs its mUID", *_where(t, source))
    items = t.get(list_key)
    if items is None:
        raise ParamError(f"'{key}' needs its {list_key} list", *_where(t, source))
    return _int(u, f"{key} mUID", "u32", source), _list(items, list_key, source)


def from_yaml(text: str, source: str | None = None) -> OclDdo:
    from . import yamlish
    from .yamlish import Map, Scalar

    doc = yamlish.parse(text, source)
    tag = doc.get("riftstone") if isinstance(doc, Map) else None
    if not isinstance(tag, Scalar) or tag.text != TAG:
        raise ParamError(f"not a Riftstone DDO collision file (expected 'riftstone: {TAG}')", 1, 1, source)
    for k, _ in doc.items:
        if k.text not in ("riftstone", "resource", "pad", "rCollIndex", "rCollNode", "rAttackParam"):
            raise ParamError(f"'{k.text}' is not part of a DDO collision file", k.line, k.col, source)
    o = OclDdo()
    pad = doc.get("pad")
    if pad is not None:
        o.pad = _int(pad, "pad", "u16", source)
    idx = doc.get("rCollIndex")
    if idx is None:
        raise ParamError("'rCollIndex' is missing (use [] for none)", None, None, source)
    o.index = [_record(INDEX, n, f"index entry {i}", source) for i, n in enumerate(_list(idx, "rCollIndex", source))]
    o.node_uid, nodes = _table(doc, "rCollNode", "nodes", source)
    for i, n in enumerate(nodes):
        node = _record(NODE, n, f"node {i}", source, extra=("geoms",))
        g = n.get("geoms")
        if g is None:
            raise ParamError(f"node {i}: 'geoms' is missing (use [] for none)", *_where(n, source))
        node["geoms"] = [_record(GEOM, s, f"node {i} shape {j}", source)
                         for j, s in enumerate(_list(g, "geoms", source))]
        o.nodes.append(node)
    o.attack_uid, attacks = _table(doc, "rAttackParam", "attacks", source)
    o.attacks = [_record(ATTACK, a, f"attack param {i}", source) for i, a in enumerate(attacks)]
    return o


def yaml_to_bytes(text: str, source: str | None = None) -> bytes:
    o = from_yaml(text, source)
    try:
        return build(o)
    except FormatError as e:          # every value is range-checked above; kept as a guard
        raise ParamError(str(e), None, None, source) from None

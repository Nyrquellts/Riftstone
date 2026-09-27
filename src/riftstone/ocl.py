"""Dark Arisen's object collision (rObjCollision, ``.ocl``): every field, read with DDDA.exe's own loader.

rObjCollision::load (0x00CCB980) reads the file as a stream of 32-bit words (0x00CFEB40 a u32, 0x00CFEB70 an f32):

    u32 mVersion 0x20121225 (any other and the loader gives up on the file), u32 mResourceID, u32 group count
    Group (0x58 bytes in memory): mNo, mKind, the nine flags mIsAttack mIsDamage mIsScrAdj mIsObjAdj mIsHang
        mIsHanged mIsCheck mIsNotice mIsNotice2 (the engine tests each for non-zero), u32 node count
    Group::Node (0x90): mNo, mShape, mRadius (f32), mJoint0, mOffset0 (3 x f32), mJoint1, mOffset1 (3 x f32),
        mRegionId, mHitPrio, mNodeAttr, mNodeEffectAttr, mNodeFreeWork
    u32 attack count
    Attack (0x1B4): mNo -- 0x7FFFFFFF marks an empty slot and nothing else of it follows -- then the 83 values of
        ``ATTACK`` in that order (not the class's), then two words whose bits are the 57 flags of ``FLAGS``
        (mIsUseData, +0x1A9, is not in the file; bits 25-31 of the second word are read by nothing)
    u32 sequence count; SeqIndex (0x84): mNo, mModelID, mGroupNo, mAttackNo, mRelation

Names are the PS3 build's, and DDDA.exe's own property list for the flag Dark
Arisen added (mIsNoCalcEnemyDefence, +0x1B1).  rObjCollision::save (0x00CCC660) writes the same stream.  Proved on
all 378 distinct files of the game: each ends where its last table does, and parse -> build and the YAML round trip
reproduce every one byte for byte (6,166 groups, 7,309 nodes, 5,330 attacks and 2,159 empty slots, 6,592 sequence
entries).  The engine reads 0 past the end of a short file and ignores bytes after the last table; Riftstone refuses
both.  What the game does with an edit: UNKNOWN until seen in game.

A node's shape, as the game builds it: a hit record copies the node (0x0076FC0D: mShape to +0x198) and the entry
node's update (0x0077388A) switches on it.  Point 0 is mOffset0 on joint mJoint0, point 1 mOffset1 on mJoint1; a joint
is the model's joint id, and 0x00CCDE30 / 0x00CCE190 place a point on -1 the model's own frame, -2 the world's axes
at the world origin, -3..-6 halfway between joints 14 and 18, 15 and 19, 16 and 20, 17 and 21 (a joint the model does
not have: the world origin too); the radius is mRadius, which follows the model's scale.  mShape 0 is a capsule from
point 0 to point 1 (its round ends reach past them by the radius), 1 a sphere at point 0, 4 a capsule pulled in by
the radius at both ends so it stops at the two points (points closer than twice the radius: a sphere one radius from
point 0 towards point 1).  That update makes nothing of 2 and 3; one monster family's own code (uEm5500, 0x00779C3E)
reads 2 as a box with corners at the two points and 3 as a box on mJoint0.  The game's files use 0 (3,587 nodes),
1 (3,473) and 4 (249).  The earlier model called 0 a sphere and 1 a capsule, which is DDO's numbering, not Dark
Arisen's.

The earlier model (``ocl/1`` YAML: a 20-byte "header", the rest as hex, and "112-byte primitives" found by a scan)
still loads: its primitive was a group holding one node, seen 8 bytes in, and its header was mVersion, mResourceID,
the group count and the first group's mNo and mKind.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .errors import FormatError, ParamError

MAGIC = 0x20121225                  # mVersion; rObjCollision::load compares it at 0x00CCB9BB
EMPTY = 0x7FFFFFFF                  # an attack slot's mNo when the slot is empty (0x00CCBD3F)
TAG = "ocl/2"
LEGACY_TAG = "ocl/1"
SHAPES = {0: "capsule", 1: "sphere", 2: "box", 3: "oriented box", 4: "inset capsule"}
SHAPE_NOTES = {0: "capsule from point 0 to point 1", 1: "sphere at point 0",
               2: "box between the points; the hit update makes no shape of it",
               3: "box on mJoint0; the hit update makes no shape of it",
               4: "capsule whose round ends stop at point 0 and point 1"}

GROUP = ("mNo", "mKind", "mIsAttack", "mIsDamage", "mIsScrAdj", "mIsObjAdj", "mIsHang", "mIsHanged", "mIsCheck",
         "mIsNotice", "mIsNotice2")
NODE = (("mNo", "u32"), ("mShape", "u32"), ("mRadius", "f32"), ("mJoint0", "s32"), ("mOffset0", "v3"),
        ("mJoint1", "s32"), ("mOffset1", "v3"), ("mRegionId", "u32"), ("mHitPrio", "u32"), ("mNodeAttr", "u32"),
        ("mNodeEffectAttr", "u32"), ("mNodeFreeWork", "s32"))
# the order rObjCollision::load reads them (0x00CCBD88..0x00CCC21C)
ATTACK = (
    ("mAttack", "f32"), ("mAttackRate", "f32"), ("mSwordRate", "f32"), ("mHitRate", "f32"),
    ("mMgcAttack", "f32"), ("mMgcAttackRate", "f32"), ("mElement", "u32"), ("mElementRate", "f32"),
    ("mShrinkType", "u32"), ("mBlowType", "u32"), ("mShrinkAttack", "f32"), ("mBlowAttack", "f32"),
    ("mRangeType", "u32"), ("mCritical", "f32"), ("mFaint", "f32"), ("mHitStopTime", "f32"),
    ("mHitSlowRate", "f32"), ("mAttackID", "u32"), ("mWork", "s32"), ("mEffectType", "u32"),
    ("mEffectLevel", "u32"), ("mEffectBreakLevel", "u32"), ("mEffectAttr", "u32"), ("mStaminaAttack", "f32"),
    ("mResetTime", "f32"), ("mPoison", "f32"), ("mSlow", "f32"), ("mBlind", "f32"),
    ("mSilence", "f32"), ("mSleep", "f32"), ("mOil", "f32"), ("mWet", "f32"),
    ("mCurse", "f32"), ("mEnemy", "f32"), ("mSeal", "f32"), ("mStone", "f32"),
    ("mAtkDown", "f32"), ("mDefDown", "f32"), ("mMgcAtkDown", "f32"), ("mMgcDefDown", "f32"),
    ("mWpPhyAtkRate", "f32"), ("mWpMgcAtkRate", "f32"), ("mWpHitRate", "f32"), ("mWpSwdRate", "f32"),
    ("mWpCriticalRate", "f32"), ("mAngle", "f32"), ("mAngleRand", "f32"), ("mShrinkDistanceZ", "f32"),
    ("mShrinkGravityZ", "f32"), ("mShrinkAirDistanceZ", "f32"), ("mShrinkAirGravityZ", "f32"),
    ("mShrinkGroundHeightY", "f32"), ("mShrinkGroundGravityY", "f32"), ("mShrinkAirHeightY", "f32"),
    ("mShrinkAirGravityY", "f32"), ("mShrinkDistanceZRand", "f32"), ("mShrinkGravityZRand", "f32"),
    ("mShrinkAirDistanceZRand", "f32"), ("mShrinkAirGravityZRand", "f32"), ("mShrinkGroundHeightYRand", "f32"),
    ("mShrinkGroundGravityYRand", "f32"), ("mShrinkAirHeightYRand", "f32"), ("mShrinkAirGravityYRand", "f32"),
    ("mDistanceZ", "f32"), ("mGravityZ", "f32"), ("mAirDistanceZ", "f32"), ("mAirGravityZ", "f32"),
    ("mGroundHeightY", "f32"), ("mGroundGravityY", "f32"), ("mAirHeightY", "f32"), ("mAirGravityY", "f32"),
    ("mDistanceZRand", "f32"), ("mGravityZRand", "f32"), ("mAirDistanceZRand", "f32"), ("mAirGravityZRand", "f32"),
    ("mGroundHeightYRand", "f32"), ("mGroundGravityYRand", "f32"), ("mAirHeightYRand", "f32"),
    ("mAirGravityYRand", "f32"), ("mSeType", "u32"), ("mSeAttr", "u32"), ("mGetGold", "s32"), ("mExpRate", "f32"),
)
# (word, bit, name): word 1 bits 0-31 land at +0x178..+0x197, word 2 bits 0-16 at +0x198..+0x1A8 and 17-24 at
# +0x1AA..+0x1B1 (0x00CCC22F..0x00CCC545)
FLAGS = tuple([(1, b, n) for b, n in enumerate((
    "mMultiHit", "mIsUseShrinkParam", "mIsAllCritical", "mIsGuardBreak", "mIsNoGuard", "mIsFriendHitDamage1",
    "mIsFriendHitDamage1_2", "mIsFriendHitDamage1_4", "mIsFriendHitDamage0", "mIsPlNoHit", "mIsEmNoHit",
    "mIsNpcNoHit", "mIsEtcNoHit", "mIsTurnAttacker", "mIsOugi", "mIsMakikomi", "mIsVerticalReflect", "mIsNoDamagePT",
    "mIsNoCritical", "mIsNoHitFly", "mIsPushOff", "mIsPushOffFly", "mIsNoShlReflect", "mIsElementNG", "mIsBigOMBreak",
    "mIsBarrelBomb", "mIsOwnHit", "mIsArrow", "mIsClimbAttack", "mIsNoEnchant", "mIsNoDeath", "mIsForceShrink"))]
    + [(2, b, n) for b, n in enumerate(
        ("mIsForceBlow", "mIsForceShrink2", "mIsForceBlow2", "mIsNoBattle")
        + tuple(f"mIsVSEnemy{k:02d}" for k in range(1, 14))
        + ("mIsAllGuard", "mIsNoDamageNoResetShrink", "mIsNoDamageNoResetBlow", "mIsSameFrameNoHit00",
           "mIsSameFrameNoHit01", "mIsSameFrameNoHit02", "mIsSameFrameNoHit03", "mIsNoCalcEnemyDefence"))])
FLAG_BITS = {n: (w, b) for w, b, n in FLAGS}
UNREAD = 0xFE000000                 # the second flag word's bits 25-31
SEQ = ("mNo", "mModelID", "mGroupNo", "mAttackNo", "mRelation")
# the fewest words a record takes, to refuse a count the file cannot hold before reading it
_MIN_WORDS = {"group": len(GROUP) + 1, "node": 16, "attack": 1, "sequence": len(SEQ)}


@dataclass
class Ocl:
    """Every value is kept as the 32-bit word it is in the file: a float as its bits, a signed field as its u32."""
    version: int = MAGIC
    resource_id: int = 0
    groups: list = field(default_factory=list)      # {"mNo": w, ..., "nodes": [{"mNo": w, ..., "mOffset0": [x, y, z]}]}
    attacks: list = field(default_factory=list)     # {"mNo": w, <ATTACK>: w, "flags1": w, "flags2": w}, or None (empty)
    seqs: list = field(default_factory=list)        # {"mNo": w, "mModelID": w, ...}

    @property
    def nodes(self) -> list[dict]:
        """Every collision shape (node) of every group, in file order."""
        return [n for g in self.groups for n in g["nodes"]]

    def counts(self) -> str:
        shapes: dict[str, int] = {}
        for n in self.nodes:
            k = SHAPES.get(n["mShape"], f"mShape {n['mShape']}")
            shapes[k] = shapes.get(k, 0) + 1
        kinds = ", ".join(f"{v} {k}" for k, v in sorted(shapes.items()))
        used = sum(1 for a in self.attacks if a is not None)
        return (f"{_n(len(self.groups), 'group')}, {_n(len(self.nodes), 'shape')}" + (f" ({kinds})" if kinds else "")
                + f", {_n(used, 'attack')} and {_n(len(self.attacks) - used, 'empty attack slot')}, "
                + _n(len(self.seqs), "sequence entry", "sequence entries"))


def _n(k: int, one: str, many: str | None = None) -> str:
    return f"{k:,} {one if k == 1 else many or one + 's'}"


class _Reader:
    def __init__(self, data: bytes):
        self.d = data
        self.p = 0

    def u32(self) -> int:
        if self.p + 4 > len(self.d):
            raise FormatError("OCL", "the file ends inside a record", self.p)
        v = struct.unpack_from("<I", self.d, self.p)[0]
        self.p += 4
        return v

    def count(self, what: str) -> int:
        at = self.p
        n = self.u32()
        if n * _MIN_WORDS[what] * 4 > len(self.d) - self.p:
            raise FormatError("OCL", f"{what} count {n} is more than the rest of the file holds", at)
        return n

    def words(self, schema) -> dict:
        out = {}
        for name, t in schema:
            out[name] = [self.u32(), self.u32(), self.u32()] if t == "v3" else self.u32()
        return out


def parse(data: bytes) -> Ocl:
    data = bytes(data)
    if len(data) < 4 or struct.unpack_from("<I", data)[0] != MAGIC:
        raise FormatError("OCL", f"not a Dark Arisen collision file (mVersion {MAGIC:#010x})", 0)
    r = _Reader(data)
    o = Ocl(r.u32(), r.u32())
    for _ in range(r.count("group")):
        g = {k: r.u32() for k in GROUP}
        g["nodes"] = [r.words(NODE) for _ in range(r.count("node"))]
        o.groups.append(g)
    for _ in range(r.count("attack")):
        no = r.u32()
        if no == EMPTY:
            o.attacks.append(None)
            continue
        a = {"mNo": no, **r.words(ATTACK)}
        a["flags1"], a["flags2"] = r.u32(), r.u32()
        o.attacks.append(a)
    for _ in range(r.count("sequence")):
        o.seqs.append({k: r.u32() for k in SEQ})
    if r.p != len(data):
        raise FormatError("OCL", f"{len(data) - r.p} byte(s) after the last table", r.p)
    return o


def build(o: Ocl) -> bytes:
    words = [o.version, o.resource_id, len(o.groups)]
    for g in o.groups:
        words += [g[k] for k in GROUP]
        words.append(len(g["nodes"]))
        for n in g["nodes"]:
            for name, t in NODE:
                words += n[name] if t == "v3" else [n[name]]
    words.append(len(o.attacks))
    for a in o.attacks:
        if a is None:
            words.append(EMPTY)
            continue
        if a["mNo"] == EMPTY:
            raise ParamError(f"an attack's mNo cannot be {EMPTY:#x}: the game reads that as an empty slot")
        words.append(a["mNo"])
        words += [a[name] for name, _ in ATTACK]
        words += [a["flags1"], a["flags2"]]
    words.append(len(o.seqs))
    for s in o.seqs:
        words += [s[k] for k in SEQ]
    try:
        return struct.pack(f"<{len(words)}I", *words)
    except struct.error as e:
        raise ParamError(f"a value does not fit its 32-bit word ({e})") from None


def read(path) -> Ocl:
    from pathlib import Path
    return parse(Path(path).read_bytes())


def f32(bits: int) -> float:
    return struct.unpack("<f", struct.pack("<I", bits & 0xFFFFFFFF))[0]


def s32(word: int) -> int:
    return word - (1 << 32) if word >= 1 << 31 else word


def _vec(v) -> str:
    return "[" + ", ".join(f"{f32(b):g}" for b in v) + "]"


def shape_text(n: dict) -> str:
    """One collision shape in a line: its kind, radius, joints and offsets."""
    k = n["mShape"]
    j0, j1 = s32(n["mJoint0"]), s32(n["mJoint1"])
    kind = SHAPES.get(k, f"mShape {k}")
    if k == 1:
        return f"{kind:<13} r={f32(n['mRadius']):g}  joint {j0}  at {_vec(n['mOffset0'])}"
    return (f"{kind:<13} r={f32(n['mRadius']):g}  joints {j0}/{j1}  "
            f"{_vec(n['mOffset0'])} to {_vec(n['mOffset1'])}")


def summary(o: Ocl, limit: int = 40) -> str:
    """A few lines for ``inspect``: the counts, then the shapes group by group."""
    lines = [o.counts()]
    shown = 0
    for i, g in enumerate(o.groups):
        for j, n in enumerate(g["nodes"]):
            if shown == limit:
                lines.append(f"  ... {len(o.nodes) - limit} more")
                return "\n".join(lines)
            lines.append(f"  group {g['mNo']:<4} node {j:<2} {shape_text(n)}")
            shown += 1
    return "\n".join(lines)


# -- YAML -------------------------------------------------------------------------------------------------------------
def _value(t: str, v, name: str = ""):
    from .params import f32_bits_text
    from .yamlish import Scalar, Seq
    if t == "v3":
        return Seq([Scalar(f32_bits_text(x)) for x in v], flow=True)
    if t == "f32":
        return Scalar(f32_bits_text(v))
    if t == "s32":
        return Scalar(str(s32(v)))
    if name == "mShape":
        return Scalar(str(v), comment=SHAPE_NOTES.get(v, "not a shape DDDA.exe builds"))
    return Scalar(str(v))


def to_yaml(o: Ocl, name: str | None = None) -> str:
    from . import yamlish
    from .yamlish import Map, Scalar, Seq

    label = "".join(c if c.isprintable() else "?" for c in name) if name else ""   # a comment line stays one line
    head = ["Riftstone object collision (.ocl, Dark Arisen)" + (f" -- {label}" if name else ""),
            o.counts() + ".",
            "Every field by its engine name, in the order DDDA.exe's loader reads it.",
            "A group's nodes are its collision shapes: point 0 is mOffset0 [x, y, z] on joint mJoint0, point 1",
            "mOffset1 on mJoint1 (-1 the model's own frame, -2 world coordinates, -3..-6 halfway between two",
            "joints); mRadius the radius. mShape 0 is a capsule from point 0 to point 1, 1 a sphere at point 0,",
            "4 a capsule whose round ends stop at the two points.",
            "An attack is what a hit does; 'mNo: 0x7fffffff' alone is an empty slot, and 'flags' lists the",
            "attack's flags that are on. Floats are exact; an untouched file rebuilds byte for byte."]
    items = [(Scalar("riftstone"), Scalar(TAG))]
    if name:
        items.append((Scalar("resource"), Scalar(name, "double")))
    items += [(Scalar("mVersion"), Scalar(f"{o.version:#010x}")), (Scalar("mResourceID"), Scalar(str(o.resource_id)))]
    groups = []
    for g in o.groups:
        fields = [(Scalar(k), Scalar(str(g[k]))) for k in GROUP]
        nodes = [Map([(Scalar(k), _value(t, n[k], k)) for k, t in NODE]) for n in g["nodes"]]
        fields.append((Scalar("nodes"), Seq(nodes, flow=not nodes)))
        groups.append(Map(fields))
    items.append((Scalar("groups"), Seq(groups, flow=not groups)))
    attacks = []
    for a in o.attacks:
        if a is None:
            attacks.append(Map([(Scalar("mNo"), Scalar(f"{EMPTY:#x}", comment="empty slot"))]))
            continue
        fields = [(Scalar("mNo"), Scalar(str(a["mNo"])))] + [(Scalar(k), _value(t, a[k])) for k, t in ATTACK]
        on = [n for w, b, n in FLAGS if (a["flags1"] if w == 1 else a["flags2"]) >> b & 1]
        fields.append((Scalar("flags"), Seq([Scalar(n) for n in on], flow=True)))
        if a["flags2"] & UNREAD:
            fields.append((Scalar("flagsUnread"), Scalar(f"{a['flags2'] & UNREAD:#010x}",
                                                         comment="second flag word, bits 25-31: read by nothing")))
        attacks.append(Map(fields))
    items.append((Scalar("attacks"), Seq(attacks, flow=not attacks)))
    seqs = [Map([(Scalar(k), Scalar(str(s[k]))) for k in SEQ], flow=True) for s in o.seqs]
    items.append((Scalar("seqIndex"), Seq(seqs, flow=not seqs)))
    return yamlish.emit(Map(items), head)


def _where(node, source):
    return getattr(node, "line", None), getattr(node, "col", None), source


def _word(node, what: str, t: str, source) -> int:
    from .params import f32_bits, shown
    from .yamlish import Scalar
    if not isinstance(node, Scalar):
        raise ParamError(f"{what} must be a single value, not a list or mapping", *_where(node, source))
    text = node.text.strip()
    if t == "f32":
        try:
            return f32_bits(text)
        except (ValueError, OverflowError):
            raise ParamError(f"{what}: {shown(text)!r} is not a 32-bit float", *_where(node, source)) from None
    try:
        v = int(text, 0)
    except ValueError:
        raise ParamError(f"{what} must be a whole number, not {shown(text)!r}", *_where(node, source)) from None
    lo, hi = (-(1 << 31), (1 << 31) - 1) if t == "s32" else (0, 0xFFFFFFFF)
    if not lo <= v <= hi:
        # the value as written: a hex one past 4,300 decimal digits has no decimal form (int -> str refuses)
        raise ParamError(f"{what}: {shown(text)} is out of range for a {t} ({lo}..{hi})", *_where(node, source))
    return v & 0xFFFFFFFF


def _fields(node, what: str, source, known) -> None:
    from .yamlish import Map
    if not isinstance(node, Map):
        raise ParamError(f"{what} is a mapping of fields", *_where(node, source))
    for k, _ in node.items:
        if k.text not in known:
            raise ParamError(f"{what}: '{k.text}' is not one of its fields", k.line, k.col, source)


def _need(node, key: str, what: str, source):
    v = node.get(key)
    if v is None:
        raise ParamError(f"{what}: '{key}' is missing", *_where(node, source))
    return v


def _list(node, what: str, source) -> list:
    from .yamlish import Seq
    if not isinstance(node, Seq):
        raise ParamError(f"'{what}' must be a list (use [] for none)", *_where(node, source))
    return node.items


_TOP = ("riftstone", "resource", "mVersion", "mResourceID", "groups", "attacks", "seqIndex")
_NODE_NAMES = {k for k, _ in NODE}
_ATTACK_NAMES = {"mNo", "flags", "flagsUnread"} | {k for k, _ in ATTACK}


def _node(nn, what: str, source) -> dict:
    from .yamlish import Seq
    _fields(nn, what, source, _NODE_NAMES)
    out = {}
    for k, t in NODE:
        v = _need(nn, k, what, source)
        if t == "v3":
            if not isinstance(v, Seq) or len(v.items) != 3:
                raise ParamError(f"{what} {k} needs 3 numbers, like [0.0, 0.0, 0.0]", *_where(v, source))
            out[k] = [_word(x, f"{what} {k}", "f32", source) for x in v.items]
        else:
            out[k] = _word(v, f"{what} {k}", t, source)
    return out


def _attack(an, what: str, source):
    from .yamlish import Scalar
    _fields(an, what, source, _ATTACK_NAMES)
    no = _word(_need(an, "mNo", what, source), f"{what} mNo", "u32", source)
    if no == EMPTY:
        if len(an.items) != 1:
            raise ParamError(f"{what}: an empty slot (mNo {EMPTY:#x}) holds nothing else", *_where(an, source))
        return None
    a = {"mNo": no}
    for k, t in ATTACK:
        a[k] = _word(_need(an, k, what, source), f"{what} {k}", t, source)
    words = [0, 0]
    for fl in _list(_need(an, "flags", what, source), f"{what} flags", source):
        if not isinstance(fl, Scalar) or fl.text.strip() not in FLAG_BITS:
            raise ParamError(f"{what}: {getattr(fl, 'text', '(a list or mapping)')!r} is not an attack flag",
                             *_where(fl, source))
        w, b = FLAG_BITS[fl.text.strip()]
        words[w - 1] |= 1 << b
    rest = an.get("flagsUnread")
    if rest is not None:
        v = _word(rest, f"{what} flagsUnread", "u32", source)
        if v & ~UNREAD:
            raise ParamError(f"{what}: flagsUnread holds only the bits {UNREAD:#010x}", *_where(rest, source))
        words[1] |= v
    a["flags1"], a["flags2"] = words
    return a


def from_yaml(text: str, source: str | None = None) -> Ocl:
    from . import yamlish
    from .yamlish import Map, Scalar

    doc = yamlish.parse(text, source)
    tag = doc.get("riftstone") if isinstance(doc, Map) else None
    if isinstance(tag, Scalar) and tag.text == LEGACY_TAG:
        return parse(_legacy_bytes(doc, source))
    if not isinstance(tag, Scalar) or tag.text != TAG:
        raise ParamError(f"not a Riftstone collision file (expected 'riftstone: {TAG}')", 1, 1, source)
    for k, _ in doc.items:
        if k.text not in _TOP:
            raise ParamError(f"'{k.text}' is not part of a collision file", k.line, k.col, source)
    version = _word(_need(doc, "mVersion", "the file", source), "mVersion", "u32", source)
    if version != MAGIC:
        raise ParamError(f"mVersion must be {MAGIC:#010x}: the game reads no other", *_where(doc.get("mVersion"), source))
    o = Ocl(version, _word(_need(doc, "mResourceID", "the file", source), "mResourceID", "u32", source))
    for gi, gn in enumerate(_list(_need(doc, "groups", "the file", source), "groups", source)):
        what = f"group {gi}"
        _fields(gn, what, source, set(GROUP) | {"nodes"})
        g = {k: _word(_need(gn, k, what, source), f"{what} {k}", "u32", source) for k in GROUP}
        g["nodes"] = [_node(nn, f"{what} node {ni}", source)
                      for ni, nn in enumerate(_list(_need(gn, "nodes", what, source), "nodes", source))]
        o.groups.append(g)
    for ai, an in enumerate(_list(_need(doc, "attacks", "the file", source), "attacks", source)):
        o.attacks.append(_attack(an, f"attack {ai}", source))
    for si, sn in enumerate(_list(_need(doc, "seqIndex", "the file", source), "seqIndex", source)):
        what = f"sequence entry {si}"
        _fields(sn, what, source, set(SEQ))
        o.seqs.append({k: _word(_need(sn, k, what, source), f"{what} {k}", "u32", source) for k in SEQ})
    return o


def yaml_to_bytes(text: str, source: str | None = None) -> bytes:
    return build(from_yaml(text, source))


# -- ocl/1, the earlier model: still loads ------------------------------------------------------------------------------
_LEGACY_SHAPES = {"sphere": 0, "capsule": 1, **{f"shape{k}": k for k in range(2, 7)}}   # its (wrong) names, as it wrote them
_LEGACY_FLOATS = {"radius": 0x30, "extent": 0x4C}     # a group's node mRadius and mOffset1's y, seen 8 bytes in
_LEGACY_SHAPE, _LEGACY_POS, _LEGACY_ELEM = 0x2C, 0x38, 112


def _legacy_bytes(doc, source) -> bytes:
    """An ``ocl/1`` file's bytes: its header and body (hex) with its primitives' fields written where the earlier
    model wrote them.  The result must be a file the game's loader reads."""
    from .yamlish import Map, Scalar, Seq

    def hexbytes(key):
        node = doc.get(key)
        if not isinstance(node, Scalar):
            raise ParamError(f"{key} must be hex", *_where(node, source))
        try:
            return bytes.fromhex(node.text)
        except ValueError:
            raise ParamError(f"{key} is not valid hex", *_where(node, source)) from None

    header = hexbytes("magic_header")
    if len(header) != 20:
        raise ParamError("magic_header must be 20 bytes", *_where(doc.get("magic_header"), source))
    body = bytearray(hexbytes("body"))
    prims = doc.get("primitives")
    for i, pm in enumerate(prims.items if isinstance(prims, Seq) else []):
        what = f"primitive {i}"
        if not isinstance(pm, Map):
            raise ParamError(f"{what} is a mapping of fields", *_where(pm, source))
        off = _word(_need(pm, "offset", what, source), f"{what} offset", "u32", source)
        if off + _LEGACY_ELEM > len(body):
            raise ParamError(f"{what}: offset {off} is outside the body", *_where(pm, source))
        sh = pm.get("shape")
        if sh is not None:
            v = (_LEGACY_SHAPES[sh.text] if isinstance(sh, Scalar) and sh.text in _LEGACY_SHAPES
                 else _word(sh, f"{what} shape", "u32", source))
            struct.pack_into("<I", body, off + _LEGACY_SHAPE, v)
        for key, at in _LEGACY_FLOATS.items():
            if pm.get(key) is not None:
                struct.pack_into("<I", body, off + at, _word(pm.get(key), f"{what} {key}", "f32", source))
        pos = pm.get("position")
        if pos is not None:
            if not isinstance(pos, Seq) or len(pos.items) != 3:
                raise ParamError(f"{what} position needs 3 numbers, like [0.0, 0.0, 0.0]", *_where(pos, source))
            for k, x in enumerate(pos.items):
                struct.pack_into("<I", body, off + _LEGACY_POS + 4 * k, _word(x, f"{what} position", "f32", source))
    data = header + bytes(body)
    try:
        parse(data)
    except FormatError as e:
        raise ParamError(f"this ocl/1 file does not make a collision file the game reads ({e})", None, None,
                         source) from None
    return data

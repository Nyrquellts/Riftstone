"""The item list (rItemList, ``.itl``): every item's record -- price, weight and the rest.

Layout, measured on the game's one item list (``etc/item/itemList``, 1,901 items):

  0x00  "ITL2"
  0x04  u32 0x01330611 (a stamp; kept)
  0x08  u32 item count
  0x0C  u32 0
  then  count x 128-byte records; record N is item id N (true for all 1,901)

A record is ``rItemList::PARAMETER`` (128 bytes).  Every field is named (``FIELDS``) by its engine name, from the PS3
build; the id, weight and prices keep their short names:

  +0x3C  bits 0-12   item id (``id``)
  +0x44  f32         weight
  +0x48  u32         buy price
  +0x4C  u32         sell price (40% of the buy price for most items)

The PC layout of each field is checked where DDDA.exe reads it or on the game's data (``CHECKED``): the enhancement
lookup (0x0045B620) and the stat getters that call it fix +0x00..+0x38 (the PC build widens mAttack and mMagicAttack
to 11 bits, where the PS3 build has 10); the flags at +0x58 match the items they are set on (mArrow on the arrows,
mLantern on the lanterns, mGold on the coin purses, mUnused on the 209 placeholders); mAlterItemNo is the item it
decays into (Scrag of Beast -> Sour -> Rotten; -1 none).  The other fields keep the PS3 build's layout, which the
checked fields around them agree with.  Bits no field names stay in ``raw``: +0x04/+0x08 bits 27-31, +0x0C/+0x10
bits 30-31, +0x24, +0x3C bits 26-31, +0x58 bits 29-31 (set on 73 items: flags added after the PS3 build), +0x6F,
+0x7F.

An item's name and description are message [id] of ``id/message/item/itemName_<lang>.gmd``
and ``itemInfo_<lang>.gmd``.  209 ids are placeholders named "Unknown Item" (price 0,
weight 0.01): slots a new item can take without growing the list, which matters because
the game's item icons are known to break for ids past 1,901.

Equipment enhancement (``LvParamWepon/Armor/Accessory.itemlv``, flat.py) is found by mLevelUpType, the row of the
table mKind chooses (``level_table``); ``itemstats.py`` computes an item's stats at each level the way the game does.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .errors import FormatError, ParamError

MAGIC = b"ITL2"
HEADER = struct.Struct("<4sIII")
RECORD = 128
ID_MASK = 0x1FFF
TAG = "itl/2"
TAGS = ("itl/1", TAG)          # itl/1: id, weight, buy, sell and raw only (still read)


@dataclass(frozen=True)
class Field:
    """A named part of a record: ``bits`` bits from bit ``bit`` of the u32 at ``word`` (``f32``: the whole word as a
    float)."""
    name: str
    word: int
    bit: int
    bits: int
    signed: bool = False
    f32: bool = False
    default: int = 0            # the value most items hold; YAML shows a field only when it differs

    def get(self, rec) -> int | float:
        w = struct.unpack_from("<I", rec, self.word)[0]
        if self.f32:
            return w
        v = (w >> self.bit) & ((1 << self.bits) - 1)
        return v - (1 << self.bits) if self.signed and v >> (self.bits - 1) else v

    def set(self, rec: bytearray, value: int) -> None:
        w = struct.unpack_from("<I", rec, self.word)[0]
        if self.f32:
            struct.pack_into("<I", rec, self.word, value & 0xFFFFFFFF)
            return
        mask = ((1 << self.bits) - 1) << self.bit
        struct.pack_into("<I", rec, self.word, (w & ~mask) | ((value << self.bit) & mask))

    @property
    def range(self) -> tuple[int, int]:
        return (-(1 << (self.bits - 1)), (1 << (self.bits - 1)) - 1) if self.signed else (0, (1 << self.bits) - 1)


def _b(name, word, bit, bits, signed=False, default=0):
    return Field(name, word, bit, bits, signed, False, default)


def _s8s(word, *names):
    return [_b(n, word, 8 * i, 8, True) for i, n in enumerate(names) if n]


FIELDS: tuple[Field, ...] = tuple(
    [_b("mAttack", 0x00, 0, 11), _b("mMagicAttack", 0x00, 11, 11), _b("mElementAttack", 0x00, 22, 10),
     _b("mDefense", 0x04, 0, 10), _b("mMagicDefense", 0x04, 10, 10), _b("mCritialRate", 0x04, 20, 7),
     _b("mShrink", 0x08, 0, 10), _b("mBlow", 0x08, 10, 10), _b("mShieldStaminaReduceRate", 0x08, 20, 7),
     _b("mNokeGuard", 0x0C, 0, 10), _b("mBlowGuard", 0x0C, 10, 10), _b("mPoison", 0x0C, 20, 10),
     _b("mSlow", 0x10, 0, 10), _b("mOil", 0x10, 10, 10), _b("mBlind", 0x10, 20, 10)]
    + _s8s(0x14, "mSwordAttackRate", "mHitAttackRate", "mSwordDefenseRate", "mHitDefenseRate")
    + _s8s(0x18, "mFireDefenseRate", "mIceDefenseRate", "mThunderDefenseRate", "mSaintDefenseRate")
    + _s8s(0x1C, "mDarkDefenseRate", "mFireCut", "mIceCut", "mThunderCut")
    + _s8s(0x20, "mSaintCut", "mDarkCut", "mBlowDefenseRate", "mShrinkDefenseRate")
    + _s8s(0x24, None, "mPoisonCut", "mSlowCut", "mBlindCut")
    + _s8s(0x28, "mSleepCut", "mWetCut", "mOilCut", "mEnemyCut")
    + _s8s(0x2C, "mSilenceCut", "mSealCut", "mCurseCut", "mStoneCut")
    + _s8s(0x30, "mAttackDownCut", "mDefenseDownCut", "mMagicAttackDownCut", "mMagicDefenseDownCut")
    + [_b("mLevelUpType", 0x34, 0, 16), _b("mEnableEquipJob", 0x34, 16, 12), _b("mEquipKind", 0x34, 28, 4),
       _b("mKind", 0x38, 0, 5), _b("mCategoryType", 0x38, 5, 5), _b("mCategoryKind", 0x38, 10, 2),
       _b("mUseType", 0x38, 12, 3), _b("mUseMot", 0x38, 15, 4), _b("mElementType", 0x38, 19, 3),
       _b("mSilence", 0x38, 22, 10),
       _b("mAlterItemNo", 0x3C, 13, 13, True, -1),
       _b("mEquipModelNo", 0x40, 0, 32, True), _b("mPri", 0x50, 0, 32, True),
       Field("mHp", 0x54, 0, 32, f32=True)]
    + [_b("m" + n, 0x58, i, 1) for i, n in enumerate(
        [f"Enemy{k:02d}BigDamage" for k in range(1, 14)]
        + ["Arrow", "Mix", "Visor", "EnableThrow", "Fake", "NoEffect", "RimShop", "EquipItem", "Lantern", "InfinitUse",
           "HoldActionItem", "EquipActionItem", "Gold", "RimPoint", "Unused", "KusariItem"])]
    + [_b("mSeType", 0x5C, 0, 32), _b("mLife", 0x60, 0, 16), _b("mFriendPoint", 0x60, 16, 16, True),
       _b("mFrindCategory", 0x64, 0, 16), _b("mObtainingLv", 0x64, 16, 16)]
    + _s8s(0x68, "mStsAttackUp", "mStsMagicAttackUp", "mStsDefenseUp", "mStsMagicDefenseUp")
    + _s8s(0x6C, "mStsFortune", "mStsEconomicFortune", "mTolerantDefense")
    + [_b("mAddHp", 0x70, 0, 16, True), _b("mAddAp", 0x70, 16, 16, True),
       Field("mUpHp", 0x74, 0, 32, f32=True), Field("mUpAp", 0x78, 0, 32, f32=True),
       _b("mOmId", 0x7C, 0, 16, True), _b("mOmColor", 0x7C, 16, 8)])
BY_NAME = {f.name: f for f in FIELDS}
# where the PC layout of a field is read in DDDA.exe's code ("code") or checked on the game's data ("data"); the rest
# keep the PS3 build's layout
CHECKED = {**{n: "code" for n in (
    "mAttack", "mMagicAttack", "mElementAttack", "mDefense", "mMagicDefense", "mShrink", "mBlow",
    "mShieldStaminaReduceRate", "mNokeGuard", "mBlowGuard", "mPoison", "mSlow", "mOil", "mBlind",
    "mSwordDefenseRate", "mHitDefenseRate", "mFireDefenseRate", "mIceDefenseRate", "mThunderDefenseRate",
    "mSaintDefenseRate", "mDarkDefenseRate", "mFireCut", "mIceCut", "mThunderCut", "mSaintCut", "mDarkCut",
    "mBlowDefenseRate", "mShrinkDefenseRate", "mPoisonCut", "mSlowCut", "mBlindCut", "mSleepCut", "mEnemyCut",
    "mSilenceCut", "mSealCut", "mCurseCut", "mStoneCut", "mAttackDownCut", "mDefenseDownCut", "mMagicAttackDownCut",
    "mMagicDefenseDownCut", "mLevelUpType", "mKind", "mSilence")},
    **{n: "data" for n in ("mAlterItemNo", "mEnableEquipJob", "mArrow", "mMix", "mVisor", "mEnableThrow", "mFake", "mRimShop",
                           "mEquipItem", "mLantern", "mInfinitUse", "mHoldActionItem", "mEquipActionItem", "mGold",
                           "mRimPoint", "mUnused", "mKusariItem")}}


def level_table(kind: int) -> str | None:
    """The enhancement table an item of ``mKind`` uses (DDDA.exe 0x0045B667: kinds 7-18 weapons, 19-24 and 27
    armour, 25 accessories); None: no enhancement."""
    if 7 <= kind <= 18:
        return "LvParamWepon"
    if 19 <= kind <= 24 or kind == 27:
        return "LvParamArmor"
    if kind == 25:
        return "LvParamAccessory"
    return None


@dataclass
class ItemList:
    stamp: int = 0x01330611
    reserved: int = 0
    records: list[bytearray] = field(default_factory=list)

    @staticmethod
    def item_id(rec) -> int:
        return struct.unpack_from("<I", rec, 0x3C)[0] & ID_MASK

    @staticmethod
    def set_id(rec: bytearray, item_id: int) -> None:
        word = struct.unpack_from("<I", rec, 0x3C)[0]
        struct.pack_into("<I", rec, 0x3C, (word & ~ID_MASK) | (item_id & ID_MASK))

    @staticmethod
    def weight(rec) -> float:
        return struct.unpack_from("<f", rec, 0x44)[0]

    @staticmethod
    def prices(rec) -> tuple[int, int]:
        return struct.unpack_from("<II", rec, 0x48)


def parse(data: bytes) -> ItemList:
    if len(data) < HEADER.size:
        raise FormatError("ITL", "file is shorter than the header", 0)
    magic, stamp, count, reserved = HEADER.unpack_from(data, 0)
    if magic != MAGIC:
        raise FormatError("ITL", "not an item list (magic)", 0)
    if HEADER.size + count * RECORD != len(data):
        raise FormatError("ITL", f"{count} items need {HEADER.size + count * RECORD} bytes, the file has {len(data)}", 8)
    recs = [bytearray(data[HEADER.size + i * RECORD: HEADER.size + (i + 1) * RECORD]) for i in range(count)]
    for i, r in enumerate(recs):
        if ItemList.item_id(r) != i:
            raise FormatError("ITL", f"record {i} carries item id {ItemList.item_id(r)}: record N must be item N",
                              HEADER.size + i * RECORD + 0x3C)
    return ItemList(stamp, reserved, recs)


def build(t: ItemList) -> bytes:
    if len(t.records) > ID_MASK + 1:
        raise ParamError(f"an item list holds at most {ID_MASK + 1} items (13-bit ids)")
    for i, r in enumerate(t.records):
        if len(r) != RECORD:
            raise ParamError(f"item {i}: a record is {RECORD} bytes")
        if ItemList.item_id(r) != i:
            raise ParamError(f"item {i}: its record carries id {ItemList.item_id(r)}")
    return HEADER.pack(MAGIC, t.stamp, len(t.records), t.reserved) + b"".join(bytes(r) for r in t.records)


# -- YAML ------------------------------------------------------------------------------

def to_yaml(t: ItemList, name: str | None = None, names: list[str] | None = None) -> str:
    """names: item names by id (from itemName_eng), written as a read-only 'name' for orientation."""
    from . import yamlish
    from .params import f32_bits_text
    from .yamlish import Map, Scalar, Seq

    head = ["Riftstone item list (.itl)" + (f" -- {name}" if name else ""),
            f"{len(t.records)} items. Each shows its fields by engine name where they differ from the usual value",
            "(0; mAlterItemNo -1): mAttack, mDefense, the resistances, mKind, mLevelUpType ... Any field can be added",
            "by name ('riftstone learn itl' lists them). 'raw' is the whole 128-byte record in hex: the named",
            "fields win over the same bits in raw, and raw keeps the bits no field names. 'name' is for orientation",
            "only: item names and descriptions live in id/message/item/itemName_*.gmd / itemInfo_*.gmd.",
            "Add an item with 'riftstone items new', which fills an unused 'Unknown Item' slot;",
            "'riftstone items stats <item>' shows an item's stats at each enhancement level."]
    items = [(Scalar("riftstone"), Scalar(TAG))]
    if name:
        items.append((Scalar("resource"), Scalar(name, "double")))
    items.append((Scalar("stamp"), Scalar(f"{t.stamp:#010x}")))
    if t.reserved:
        items.append((Scalar("reserved"), Scalar(str(t.reserved))))
    rows = []
    for i, r in enumerate(t.records):
        buy, sell = ItemList.prices(r)
        f = [(Scalar("id"), Scalar(str(ItemList.item_id(r))))]
        if names is not None and i < len(names):
            f.append((Scalar("name"), Scalar(names[i], "double")))
        f += [(Scalar("weight"), Scalar(f32_bits_text(struct.unpack_from("<I", r, 0x44)[0]))), (Scalar("buy"), Scalar(str(buy))),
              (Scalar("sell"), Scalar(str(sell)))]
        for fd in FIELDS:
            v = fd.get(r)
            if v != fd.default:
                f.append((Scalar(fd.name), Scalar(f32_bits_text(v) if fd.f32 else str(v))))
        f.append((Scalar("raw"), Scalar(bytes(r).hex())))
        rows.append(Map(f))
    items.append((Scalar("items"), Seq(rows)))
    return yamlish.emit(Map(items), head)


def _u32(node, what: str, source: str | None) -> int:
    from .yamlish import Scalar

    if not isinstance(node, Scalar):
        raise ParamError(f"{what} is a whole number", getattr(node, "line", None), getattr(node, "col", None), source)
    try:
        v = int(node.text.strip(), 0)
    except ValueError:
        raise ParamError(f"{what} is a whole number, not {node.text!r}", node.line, node.col, source) from None
    if not 0 <= v <= 0xFFFFFFFF:
        raise ParamError(f"{what} must be between 0 and 4294967295", node.line, node.col, source)
    return v


def _f32(node, what: str, source: str | None) -> int:
    from .params import f32_bits
    from .yamlish import Scalar

    if not isinstance(node, Scalar):
        raise ParamError(f"{what} is a number", getattr(node, "line", None), getattr(node, "col", None), source)
    try:
        return f32_bits(node.text)
    except ValueError as e:
        msg = str(e) if "NaN" in str(e) or "32-bit" in str(e) else f"{what} is a number, not {node.text!r}"
        raise ParamError(msg, node.line, node.col, source) from None


def from_yaml(text: str, source: str | None = None) -> ItemList:
    from . import yamlish
    from .yamlish import Map, Scalar, Seq

    doc = yamlish.parse(text, source)
    if not isinstance(doc, Map) or not isinstance(doc.get("riftstone"), Scalar) or doc.get("riftstone").text not in TAGS:
        raise ParamError(f"not a Riftstone item list (expected 'riftstone: {TAG}')", 1, 1, source)
    for k, _ in doc.items:                  # was: a misspelled stamp or reserved silently took the default
        if k.text not in ("riftstone", "resource", "stamp", "reserved", "items"):
            raise ParamError(f"an item list has riftstone, resource, stamp, reserved and items, not {k.text!r}",
                             k.line, k.col, source)
    t = ItemList()
    if doc.get("stamp") is not None:
        t.stamp = _u32(doc.get("stamp"), "stamp", source)
    if doc.get("reserved") is not None:
        t.reserved = _u32(doc.get("reserved"), "reserved", source)
    rows = doc.get("items")
    if not isinstance(rows, Seq):
        raise ParamError("items is a list of '- id: ...' entries", getattr(rows, "line", None), None, source)
    for i, row in enumerate(rows.items):
        if not isinstance(row, Map):
            raise ParamError("each item is a block of fields", getattr(row, "line", None), None, source)
        for k, _ in row.items:
            if k.text not in ("id", "name", "weight", "buy", "sell", "raw") and k.text not in BY_NAME:
                raise ParamError(f"an item has id, name, weight, buy, sell, raw and the record's fields by engine name "
                                 f"(mAttack, mDefense, mKind ...; riftstone learn itl), not {k.text!r}",
                                 k.line, k.col, source)
        raw = row.get("raw")
        if not isinstance(raw, Scalar):
            raise ParamError("each item needs its raw record", row.line, row.col, source)
        try:
            rec = bytearray(bytes.fromhex(raw.text))
        except ValueError:
            raise ParamError("raw is the record in hex", raw.line, raw.col, source) from None
        if len(rec) != RECORD:
            raise ParamError(f"raw is {RECORD} bytes ({RECORD * 2} hex digits), not {len(rec)}", raw.line, raw.col, source)
        iid = row.get("id")
        if iid is not None:
            n = _u32(iid, "id", source)
            if n != i:
                raise ParamError(f"this is item {i}, but its id says {n}. An item's id is its place in the list: "
                                 "other data finds items by id, so items must not move", iid.line, iid.col, source)
        if i > ID_MASK:
            raise ParamError(f"an item list holds at most {ID_MASK + 1} items (13-bit ids)", row.line, row.col, source)
        ItemList.set_id(rec, i)
        if row.get("weight") is not None:
            struct.pack_into("<I", rec, 0x44, _f32(row.get("weight"), "weight", source))
        for key, off in (("buy", 0x48), ("sell", 0x4C)):
            if row.get(key) is not None:
                struct.pack_into("<I", rec, off, _u32(row.get(key), key, source))
        for k, node in row.items:
            fd = BY_NAME.get(k.text)
            if fd is not None:
                fd.set(rec, _f32(node, fd.name, source) if fd.f32 else _ranged(node, fd, source))
        t.records.append(rec)
    return t


def _ranged(node, fd: Field, source: str | None) -> int:
    from .yamlish import Scalar

    if not isinstance(node, Scalar):
        raise ParamError(f"{fd.name} is a whole number", getattr(node, "line", None), getattr(node, "col", None), source)
    try:
        v = int(node.text.strip(), 0)
    except ValueError:
        raise ParamError(f"{fd.name} is a whole number, not {node.text!r}", node.line, node.col, source) from None
    lo, hi = fd.range
    if not lo <= v <= hi:
        raise ParamError(f"{fd.name} must be between {lo} and {hi} ({fd.bits} bits{', signed' if fd.signed else ''})",
                         node.line, node.col, source)
    return v


def yaml_to_bytes(text: str, source: str | None = None) -> bytes:
    return build(from_yaml(text, source))

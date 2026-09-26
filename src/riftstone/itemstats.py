"""An item's stats at each enhancement level, computed the way the game computes them; and named edits of an item.

How the game does it (DDDA.exe, build 2364871): every stat an enhancement can change has a getter (the "RealValue"
properties of ``sItemManager::cItemParam``: 重量, のけぞり値, 斬耐性 ...) that reads the stat's base value from the item's
record (``itl.FIELDS``) and calls the lookup at 0x0045B620 with the item's mKind, the stat's kind number and the
item's enhancement level.  The lookup picks the table mKind names (``itl.level_table``: ``etc/item/LvParamWepon``,
``LvParamArmor``, ``LvParamAccessory``, ``.itemlv``, loaded by sItemManager into +0x12C/+0x130/+0x134), takes row
mLevelUpType (none past the table's end), and walks levels 1 up to the item's level, each with its 2, 4, 6, 8, 8, 8
entries (``mUpParamLv<n>`` the stat kind, ``mUpRate<n>``, ``mIsDirectValue<n>``).  An entry for the stat sets the value
to base x rate, or base + rate when the entry is direct; the last such entry wins, so each level's entry holds the
whole change from the base (the Iron Sword's Strength 50 gets +16, +32, +48 at levels 1-3, +160 at 4).  No entry: the
base.  Levels 1-3 are the stars; the game's own names for 4-6 are not read here.

``KINDS`` (stat kind -> the field it scales) is read from the getters' calls; 0x29 scales both knockdown and stagger
resistance.  The tables use kinds -1 (an empty entry), 0, 1, 3, 4 and 6-41 but 40.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

from . import flat, itl, modfiles, typemap
from .errors import RiftError

ITEMLV = typemap.BY_EXT["itemlv"]
KINDS: dict[int, tuple[str, ...]] = {
    0: ("mAttack",), 1: ("mMagicAttack",), 2: ("mElementAttack",), 3: ("mShrink",), 4: ("mBlow",),
    5: ("mShieldStaminaReduceRate",), 6: ("mNokeGuard",), 7: ("mBlowGuard",), 8: ("mPoison",), 9: ("mSlow",),
    10: ("mOil",), 11: ("mBlind",), 12: ("mSilence",), 13: ("mDefense",), 14: ("mMagicDefense",),
    15: ("mSwordDefenseRate",), 16: ("mHitDefenseRate",), 17: ("mFireDefenseRate",), 18: ("mIceDefenseRate",),
    19: ("mThunderDefenseRate",), 20: ("mSaintDefenseRate",), 21: ("mDarkDefenseRate",), 22: ("mFireCut",),
    23: ("mIceCut",), 24: ("mThunderCut",), 25: ("mSaintCut",), 26: ("mDarkCut",), 27: ("mPoisonCut",),
    28: ("mSlowCut",), 29: ("mBlindCut",), 30: ("mSleepCut",), 31: ("mEnemyCut",), 32: ("mSilenceCut",),
    33: ("mSealCut",), 34: ("mCurseCut",), 35: ("mStoneCut",), 36: ("mAttackDownCut",),
    37: ("mMagicAttackDownCut",), 38: ("mDefenseDownCut",), 39: ("mMagicDefenseDownCut",),
    41: ("mBlowDefenseRate", "mShrinkDefenseRate")}
FIELD_KIND = {name: k for k, names in KINDS.items() for name in names}
# mKind of the equipment kinds, as every item of the kind is (measured on the item list's names: 7 the Iron Sword and
# Cutlass, 13 Throatcutters, 21 caps and helms ...); the other kinds hold tools, key items, food, materials, rings
KIND_NAMES = {7: "sword", 8: "mace", 9: "shield", 10: "magick shield", 11: "longsword", 12: "warhammer",
              13: "daggers", 14: "shortbow", 15: "longbow", 16: "magick bow", 17: "staff", 18: "archistaff",
              19: "chest clothing", 20: "leg clothing", 21: "head armour", 22: "chest armour", 23: "arm armour",
              24: "leg armour", 25: "cloak", 27: "outfit set"}
# mEnableEquipJob bits 1-9: the vocations in the game's order, as every weapon kind shows them (swords: Fighter, Mystic
# Knight, Assassin; maces and magick shields: Mystic Knight; archistaffs: Sorcerer ...); bits 10 and 11 are set on
# every item, bit 0 on none
JOBS = ("Fighter", "Strider", "Mage", "Mystic Knight", "Assassin", "Magick Archer", "Warrior", "Ranger", "Sorcerer")


def jobs_of(mask: int) -> list[str]:
    """The vocations mEnableEquipJob lets equip an item."""
    return [name for bit, name in enumerate(JOBS, 1) if mask >> bit & 1]
ENTRIES = (2, 4, 6, 8, 8, 8)          # entries per level (0x0045B6D8's table)
LEVELS = len(ENTRIES)
# what the engine's field names mean, for people reading the stats (the getters' Japanese names where there is one)
GLOSS = {
    "mAttack": "Strength", "mMagicAttack": "Magick", "mElementAttack": "element attack",
    "mDefense": "Defense", "mMagicDefense": "Magick Defense", "mCritialRate": "critical rate",
    "mShrink": "stagger power", "mBlow": "knockdown power", "mShieldStaminaReduceRate": "stamina reduction (shield)",
    "mNokeGuard": "stagger guard", "mBlowGuard": "knockdown guard", "mPoison": "poison build-up",
    "mSlow": "torpor build-up", "mOil": "tarring build-up", "mBlind": "blind build-up", "mSilence": "silence build-up",
    "mSwordAttackRate": "slash attack rate", "mHitAttackRate": "strike attack rate",
    "mSwordDefenseRate": "slash resistance", "mHitDefenseRate": "strike resistance",
    "mFireDefenseRate": "fire resistance", "mIceDefenseRate": "ice resistance",
    "mThunderDefenseRate": "thunder resistance", "mSaintDefenseRate": "holy resistance",
    "mDarkDefenseRate": "dark resistance", "mFireCut": "burn resistance", "mIceCut": "frozen resistance",
    "mThunderCut": "shock resistance", "mSaintCut": "drain resistance", "mDarkCut": "multi-hit resistance",
    "mBlowDefenseRate": "knockdown resistance", "mShrinkDefenseRate": "stagger resistance",
    "mPoisonCut": "poison resistance", "mSlowCut": "torpor resistance", "mBlindCut": "blind resistance",
    "mSleepCut": "sleep resistance", "mWetCut": "wet resistance", "mOilCut": "tar resistance",
    "mEnemyCut": "possession resistance", "mSilenceCut": "silence resistance", "mSealCut": "skill-seal resistance",
    "mCurseCut": "curse resistance", "mStoneCut": "petrification resistance",
    "mAttackDownCut": "Strength-down resistance", "mDefenseDownCut": "Defense-down resistance",
    "mMagicAttackDownCut": "Magick-down resistance", "mMagicDefenseDownCut": "Magick-Defense-down resistance",
}


def table_name(table: str) -> bytes:
    return f"etc\\item\\{table}".encode("latin-1")


def load_table(game, idx, mod_root: Path | None, table: str) -> list[dict]:
    """A level table's rows, as the mod has it, else the game's."""
    data, _ = modfiles.load(game, idx, mod_root, table_name(table), ITEMLV)
    return flat.parse(data, "itemlv").data["mpArray"]


def _f32(bits: int) -> float:
    return struct.unpack("<f", struct.pack("<I", bits & 0xFFFFFFFF))[0]


def at_level(base: float, row: dict, kind: int, level: int) -> float:
    """One stat at one level, as 0x0045B620 computes it: the last entry for ``kind`` at levels 1..level sets
    base x rate (base + rate when direct); none: the base."""
    value = base
    for lv in range(1, min(level, LEVELS) + 1):
        kinds = row[f"mUpParamLv{lv}"]
        rates = row[f"mUpRate{lv}"]
        direct = row[f"mIsDirectValue{lv}"]
        for j in range(ENTRIES[lv - 1]):
            if kinds[j] == kind:
                rate = _f32(rates[j])
                value = base + rate if direct[j] else base * rate
    return value


@dataclass
class Stats:
    item: int
    kind: int                     # mKind
    table: str | None             # the level table it uses, or None (no enhancement)
    row: int | None               # mLevelUpType when the table has that row
    base: dict[str, int] = field(default_factory=dict)            # every stat an enhancement can scale
    levels: list[dict[str, float]] = field(default_factory=list)  # levels 0..6: the same stats
    note: str = ""
    jobs: list[str] = field(default_factory=list)                  # the vocations that can equip it


def stats(record: bytes | bytearray, item: int, tables: dict[str, list[dict]]) -> Stats:
    """An item's scaled stats at levels 0..6 from its record and the level tables (by name)."""
    kind = itl.BY_NAME["mKind"].get(record)
    table = itl.level_table(kind)
    row_no = itl.BY_NAME["mLevelUpType"].get(record)
    s = Stats(item, kind, table, None, jobs=jobs_of(itl.BY_NAME["mEnableEquipJob"].get(record)))
    for name in FIELD_KIND:
        s.base[name] = itl.BY_NAME[name].get(record)
    rows = tables.get(table) if table else None
    if table is None:
        s.note = f"items of kind {kind} have no enhancement levels"
    elif rows is None:
        s.note = f"{table} is not loaded"
    elif row_no >= len(rows):
        s.note = f"row {row_no} is past the end of {table} ({len(rows)} rows): no enhancement"
    else:
        s.row = row_no
    for level in range(LEVELS + 1):
        if s.row is None or level == 0:
            s.levels.append({n: float(v) for n, v in s.base.items()})
        else:
            s.levels.append({n: at_level(float(v), rows[s.row], FIELD_KIND[n], level) for n, v in s.base.items()})
    return s


def item_stats(game, idx, mod_root: Path | None, key: str) -> tuple[Stats, str]:
    """(stats, item name) of an item by id or name, as the mod has the item list and the level tables."""
    from . import items as itemsmod

    t, _ = itemsmod.load_list(game, idx, mod_root)
    it = itemsmod.find(itemsmod.listing(game, idx, mod_root), key)
    table = itl.level_table(itl.BY_NAME["mKind"].get(t.records[it.id]))
    tables = {table: load_table(game, idx, mod_root, table)} if table else {}
    return stats(t.records[it.id], it.id, tables), it.name


def set_fields(game, idx, mod_root: Path, key: str, values: dict[str, str]) -> tuple[int, str, dict, Path]:
    """Set named fields of one item in the mod's item list (YAML kept as YAML).  ``values``: field name -> text as
    typed.  Returns (item id, item name, {field: (old, new)}, the file written)."""
    from . import arcfolder
    from . import items as itemsmod

    if not values:
        raise RiftError("say which fields to set: mAttack=120 mDefense=30 ...")
    t, out = itemsmod.load_list(game, idx, mod_root)
    listing = itemsmod.listing(game, idx, mod_root)
    it = itemsmod.find(listing, key)
    rec = bytearray(t.records[it.id])
    changed = {}
    for name, text in values.items():
        fd = itl.BY_NAME.get(name)
        if fd is None:
            close = [n for n in itl.BY_NAME if n.lower() == name.lower() or n.lower() == "m" + name.lower()]
            raise RiftError(f"no field {name!r}" + (f"; did you mean {close[0]}?" if close else
                                                   " (riftstone learn itl lists the fields)"))
        old = fd.get(rec)
        if fd.f32:
            from .params import f32_bits
            try:
                new = f32_bits(text)
            except ValueError as e:
                raise RiftError(f"{name}: {e}") from None
        else:
            try:
                new = int(str(text).strip(), 0)
            except ValueError:
                raise RiftError(f"{name} is a whole number, not {text!r}") from None
            lo, hi = fd.range
            if not lo <= new <= hi:
                raise RiftError(f"{name} must be between {lo} and {hi} ({fd.bits} bits{', signed' if fd.signed else ''})")
        fd.set(rec, new)
        changed[name] = (old, fd.get(rec))
    t.records[it.id] = rec
    payload = (itl.to_yaml(t, itemsmod.LIST.decode("latin-1"), [x.name for x in listing]).encode("utf-8")
               if out.suffix == ".yaml" else itl.build(t))
    arcfolder.write_file(out, payload)
    return it.id, it.name, changed, out


__all__ = ["FIELD_KIND", "GLOSS", "JOBS", "KINDS", "KIND_NAMES", "LEVELS", "Stats", "at_level", "item_stats",
           "jobs_of", "load_table", "set_fields", "stats"]

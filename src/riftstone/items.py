"""Items: list the game's items, and add new ones in unused slots.

An item is a record in ``etc/item/itemList.itl`` (see itl.py) plus message [id] of
``itemName_<lang>`` and ``itemInfo_<lang>`` in ``id/message/item/``.  ``new`` copies an
existing item's record (so the new item behaves like that one) into an unused
"Unknown Item" slot, sets its price and weight, and writes its name and description
into all 7 languages -- everything goes into the mod, never the game.

``shop`` puts an item on sale: a shop (``etc/shop/n###ShopList.shp``, XFS) lists its
stock as ``mShopLineup`` entries (``rShopList::cLineupData``: mItemNo, mItemNum,
mItemRearrival, and two lists of conditions).  The new entry copies the shop's first one
with every condition cleared (``mCommand: 0``, as the game's unused condition slots are).

A new item keeps its template's record: its enhancement row (mLevelUpType, itemstats.py) and its model
(mEquipModelNo: for every armour piece and accessory an mArmorId of ``model\\pl\\parts\\m_parts.atr``), so it
enhances and looks like the template.  In game UNKNOWN: a new model entry, and that a lineup entry with no conditions
is always on sale.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

from . import arc, arcfolder, fsmap, gmd, itl, params, text, typemap, xfs
from .errors import RiftError

ITL = typemap.BY_EXT["itl"]
SHP = typemap.BY_EXT["shp"]
LIST = b"etc\\item\\itemList"
NAMES = "id/message/item/itemName_eng.gmd"
INFOS = "id/message/item/itemInfo_eng.gmd"
PLACEHOLDER = "Unknown Item"


@dataclass
class Item:
    id: int
    name: str
    weight: float
    buy: int
    sell: int

    @property
    def free(self) -> bool:
        return self.name == PLACEHOLDER and self.buy == 0


def load_list(game, idx, mod_root: Path | None) -> tuple[itl.ItemList, Path | None]:
    """The item list as the mod has it (YAML or binary), else the game's; with the path the mod keeps it at."""
    rel = fsmap.encode_name(LIST, ITL)
    if mod_root is not None:
        as_yaml, as_bin = mod_root / "files" / (rel + ".yaml"), mod_root / "files" / rel
        if as_yaml.is_file() and as_bin.is_file():
            raise RiftError(f"the mod has both {rel} and {rel}.yaml; keep one")
        if as_yaml.is_file():
            return itl.from_yaml(params.decode_text(as_yaml.read_bytes(), str(as_yaml)), str(as_yaml)), as_yaml
        if as_bin.is_file():
            return itl.parse(as_bin.read_bytes()), as_bin
    arcs = idx.archives_with(LIST, ITL)
    e = arc.Archive.read(game.vanilla_arc(arcs[0])).find(LIST, ITL) if arcs else None
    if e is None:
        raise RiftError("the game has no item list (etc/item/itemList.itl)")
    return itl.parse(e.data()), (mod_root / "files" / (rel + ".yaml") if mod_root is not None else None)


def listing(game, idx, mod_root: Path | None = None) -> list[Item]:
    t, _ = load_list(game, idx, mod_root)
    names = [m.text for m in text.load(game, idx, mod_root, text.resolve(NAMES))[0].messages]
    out = []
    for i, r in enumerate(t.records):
        buy, sell = itl.ItemList.prices(r)
        out.append(Item(i, names[i] if i < len(names) else "", itl.ItemList.weight(r), buy, sell))
    return out


def find(items: list[Item], key: str) -> Item:
    """An item by id or by name (exact, ignoring case; else the only name that contains it)."""
    k = key.strip()
    if re.fullmatch(r"[0-9]{1,9}", k):          # an id; str.isdigit() also takes '²', which int() refuses
        n = int(k)
        if n < len(items):
            return items[n]
        raise RiftError(f"there is no item {n} (the list has {len(items)})")
    exact = [it for it in items if it.name.casefold() == k.casefold() and not it.free]
    if exact:
        return exact[0]
    near = [it for it in items if k.casefold() in it.name.casefold() and not it.free]
    if len(near) == 1:
        return near[0]
    if not near:
        raise RiftError(f"no item is called {key!r}")
    raise RiftError(f"{key!r} matches {len(near)} items: " + ", ".join(f"{it.name} ({it.id})" for it in near[:8])
                    + (" ..." if len(near) > 8 else "") + "; use the id")


@dataclass
class NewItem:
    id: int
    like: Item
    files: list[Path] = field(default_factory=list)


def new(game, idx, mod_root: Path, name: str, like: str, description: str = "", slot: int | None = None,
        buy: int | None = None, sell: int | None = None, weight: float | None = None) -> NewItem:
    if not name.strip():
        raise RiftError("give the new item a name")
    for what, v in (("buy", buy), ("sell", sell)):
        if v is not None and not 0 <= v <= 0xFFFFFFFF:
            raise RiftError(f"{what} must be between 0 and 4294967295 gold")
    t, out = load_list(game, idx, mod_root)
    items = listing(game, idx, mod_root)
    template = find(items, like)
    if slot is None:
        free = [it.id for it in items if it.free]
        if not free:
            raise RiftError("no unused 'Unknown Item' slot is left in this mod's item list")
        slot = free[0]
    elif not (0 <= slot < len(items)) or not items[slot].free:
        raise RiftError(f"item {slot} is not an unused slot ({items[slot].name if 0 <= slot < len(items) else 'no such id'})")
    # every text version must have line [slot] before anything is written
    for res in (NAMES, INFOS):
        for v in text.variants(idx, text.resolve(res)):
            g, _ = text.load(game, idx, mod_root, v)
            if slot >= len(g.messages):
                raise RiftError(f"{fsmap.encode_name(v, text.GMD)} has no line {slot}")
    for s in (name, description):
        gmd.build(gmd.Gmd(messages=[gmd.Message(s)]))   # a NUL cannot be written
    rec = bytearray(t.records[template.id])
    itl.ItemList.set_id(rec, slot)
    if weight is not None:
        from .params import to_f32
        try:
            struct.pack_into("<f", rec, 0x44, to_f32(float(weight)))
        except ValueError:
            raise RiftError(f"weight {weight} is not a usable number") from None
    if buy is not None:
        struct.pack_into("<I", rec, 0x48, buy)
        if sell is None:
            sell = buy * 2 // 5                   # the game's usual: sells for 40%
    if sell is not None:
        struct.pack_into("<I", rec, 0x4C, sell)
    t.records[slot] = rec
    done = NewItem(slot, template)
    done.files += [a.path for a in text.set_line(game, idx, mod_root, NAMES, slot, name)]
    done.files += [a.path for a in text.set_line(game, idx, mod_root, INFOS, slot, description)]
    names = [it.name for it in items]
    names[slot] = name
    payload = (itl.to_yaml(t, LIST.decode("latin-1"), names).encode("utf-8") if out.suffix == ".yaml"
               else itl.build(t))
    arcfolder.write_file(out, payload)
    done.files.append(out)
    return done


# -- shops ---------------------------------------------------------------------------------

def find_shop(idx, key: str) -> bytes:
    """A shop by engine path (etc/shop/n007ShopList.shp) or by name (n007ShopList, n007)."""
    k = key.replace("\\", "/").strip()
    if k.lower().endswith(".yaml"):
        k = k[:-5]
    if "/" in k:
        name, tid = fsmap.decode_path(k if k.lower().endswith(".shp") else k + ".shp")
        if tid != SHP or not idx.archives_with(name, SHP):
            raise RiftError(f"the game has no shop {k}")
        return name
    stem = k[:-4] if k.lower().endswith(".shp") else k
    if not stem.lower().endswith("shoplist"):
        stem += "ShopList"
    hits = sorted({r["name"] for r in idx.search(stem, SHP, 50)
                   if r["name"].decode("latin-1").rsplit("\\", 1)[-1].lower() == stem.lower()})
    if not hits:
        raise RiftError(f"no shop is called {key!r} (riftstone find ShopList --type shp lists them)")
    if len(hits) > 1:
        raise RiftError(f"{key!r} is more than one shop: " + ", ".join(fsmap.encode_name(h, SHP) for h in hits)
                        + "; give the full path")
    return hits[0]


def _get(x: xfs.Xfs, o: xfs.Obj, name: str) -> list:
    for p, vals in zip(x.classes[o.cls].props, o.fields):
        if p.name == name:
            return vals
    raise RiftError(f"a {x.classes[o.cls].name} has no {name}; this shop is not laid out like the game's")


def _set(x: xfs.Xfs, o: xfs.Obj, name: str, value) -> None:
    vals = _get(x, o, name)
    vals[:] = [value]


def _clone(o):
    if not isinstance(o, xfs.Obj):
        return o
    return xfs.Obj(o.cls, [[_clone(v) for v in vals] for vals in o.fields])


def shop(game, idx, mod_root: Path, shop_key: str, item: int, stock: int = 3, restock: int = 5) -> Path:
    """Add ``item`` to a shop's lineup inside the mod; returns the shop file in the mod."""
    for what, v in (("item", item), ("stock", stock), ("restock", restock)):   # all three are s32 fields
        if not 0 <= v <= 0x7FFFFFFF:
            raise RiftError(f"{what} must be between 0 and 2147483647")
    name = find_shop(idx, shop_key)
    rel = fsmap.encode_name(name, SHP)
    as_yaml, as_bin = mod_root / "files" / (rel + ".yaml"), mod_root / "files" / rel
    if as_yaml.is_file() and as_bin.is_file():
        raise RiftError(f"the mod has both {rel} and {rel}.yaml; keep one")
    if as_yaml.is_file():
        x = params.from_yaml(params.decode_text(as_yaml.read_bytes(), str(as_yaml)), str(as_yaml))
        out = as_yaml
    elif as_bin.is_file():
        x, out = xfs.parse(as_bin.read_bytes()), as_bin
    else:
        arcs = idx.archives_with(name, SHP)
        x, out = xfs.parse(arc.Archive.read(game.vanilla_arc(arcs[0])).find(name, SHP).data()), as_yaml
    if x.root_class.name != "rShopList":
        raise RiftError(f"{rel} is not a shop list")
    lineup = _get(x, x.root, "mShopLineup")[0]
    entries = _get(x, lineup, "mpArray") if isinstance(lineup, xfs.Obj) else None
    if not entries or not isinstance(entries[0], xfs.Obj):
        raise RiftError(f"{rel} has no lineup entry to copy")
    if any(isinstance(e, xfs.Obj) and _get(x, e, "mItemNo") == [item] for e in entries):
        raise RiftError(f"{rel} already sells item {item}")
    e = _clone(entries[0])
    _set(x, e, "mItemNo", item)
    _set(x, e, "mItemNum", stock)
    _set(x, e, "mItemRearrival", restock)
    for cond_list in ("mLineeupJAnd", "mLineeupJOr"):
        for c in _get(x, e, cond_list):
            if isinstance(c, xfs.Obj):
                for p, vals in zip(x.classes[c.cls].props, c.fields):
                    vals[:] = [0 for _ in vals]
    entries.append(e)
    raw = xfs.build(x)
    payload = params.xfs_to_yaml_bytes(raw, name.decode("latin-1"), SHP).encode("utf-8") if out.suffix == ".yaml" else raw
    arcfolder.write_file(out, payload)
    return out


# -- recipes and drop sets -----------------------------------------------------------------

RECIPES = b"etc\\item\\itemMix"
SET_TABLES = {"enemy": b"etc\\item\\ItemEmListSetTbl", "reward": b"etc\\item\\itemSetTbl"}


def recipe(game, idx, mod_root: Path, a: int, b: int, makes: int, count: int = 1) -> Path:
    """A new crafting recipe: a + b makes ``count`` of ``makes``.  Refuses a pair that already has one."""
    from . import modfiles, tables

    for what, v in (("an ingredient", a), ("an ingredient", b), ("the result", makes), ("count", count)):
        if not 0 <= v <= 0xFFFFFFFF:
            raise RiftError(f"{what} must be between 0 and 4294967295")
    if count < 1:
        raise RiftError("a recipe makes at least 1")
    imx = typemap.BY_EXT["imx"]
    data, out = modfiles.load(game, idx, mod_root, RECIPES, imx)
    t = tables.parse(data)
    for r in t.rows:
        if {r[0], r[1]} == {a, b}:
            raise RiftError(f"{a} + {b} already makes {r[2]} (x{r[3]}); change that recipe in {out.name} instead")
    t.rows.append((a, b, makes, count))
    modfiles.save(out, tables.build(t), RECIPES, imx)
    return out


def _set_table(game, idx, mod_root, table: str):
    from . import modfiles, tables

    if table not in SET_TABLES:
        raise RiftError(f"the table is {' or '.join(SET_TABLES)}, not {table!r}")
    ist = typemap.BY_EXT["ist"]
    data, out = modfiles.load(game, idx, mod_root, SET_TABLES[table], ist)
    return tables.parse(data), out, ist


def sets_with(game, idx, mod_root: Path | None, item: int, table: str = "enemy") -> list[tuple]:
    """The sets (rows) of a drop/reward table that hold ``item``."""
    t, _, _ = _set_table(game, idx, mod_root, table)
    return [r for r in t.rows if item in r[4:12]]


def drop(game, idx, mod_root: Path, item: int, set_id: int, weight: int, table: str = "enemy") -> tuple[Path, int]:
    """Put ``item`` in set ``set_id`` with ``weight`` (percent), taken from the set's chance of nothing.
    Returns the file and the set's new total."""
    from . import modfiles, tables

    if not 1 <= weight <= 100:
        raise RiftError("weight is a percent, 1 to 100")
    if not 0 <= item < tables.NONE:
        raise RiftError(f"there is no item {item}")
    t, out, ist = _set_table(game, idx, mod_root, table)
    hits = [i for i, r in enumerate(t.rows) if r[0] == set_id]
    if not hits:
        raise RiftError(f"the {table} table has no set {set_id} (items sets <item> finds sets by what they drop)")
    if len(hits) > 1:
        raise RiftError(f"the {table} table has {len(hits)} sets with id {set_id}; edit {out.name} by hand")
    row = list(t.rows[hits[0]])
    items, weights = row[4:12], row[12:20]
    if item in items:
        raise RiftError(f"set {set_id} already holds item {item}")
    nothing = [s for s in range(8) if items[s] == tables.NONE and weights[s] > 0]
    free = [s for s in range(8) if items[s] == tables.NONE and weights[s] == 0]
    if nothing and weights[nothing[0]] >= weight:
        weights[nothing[0]] -= weight                     # the new item's chance comes out of "nothing"
        slot = nothing[0] if weights[nothing[0]] == 0 else (free[0] if free else None)
        if slot is None:
            raise RiftError(f"set {set_id} has no free slot left (8 used)")
    elif free:
        slot = free[0]
    else:
        raise RiftError(f"set {set_id} has no free slot left (8 used)")
    items[slot], weights[slot] = item, weight
    row[4:12], row[12:20] = items, weights
    t.rows[hits[0]] = tuple(row)
    modfiles.save(out, tables.build(t), SET_TABLES[table], ist)
    return out, sum(weights)

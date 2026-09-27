"""The local Dragon's Dogma Online server's tables that several Online mods change, merged when they are installed
together: its spawn table (``EnemySpawn.json``) and its shops (``Shop.json``).

Online's spawns, drops and shops are the server's (``docs/ddo-bridge.md``).  An encounter adds rows to the spawn
table, a drop edit adds an item to one of its drop tables and a shop edit adds goods to a shop, and every such mod
carries the whole file, so as whole files the later mod's copy would win and the other's rows, drops or goods would
vanish.  Installed together, the copies are merged against the server's own file (the one install keeps aside):

* **Spawn rows** (``enemies``, lists read by ``schemas``) have no ids.  Each mod's copy is compared with the server's
  as a sequence of rows: rows a mod adds go in where it put them (after the same original row), rows it removes or
  changes are removed or changed.  Rows several mods add at one place all go in, the earlier mod's first (once when
  they are the same rows).  Where two mods change the same original rows differently the later mod's version of them
  wins and the other's change there is reported.
* **Drop tables** (``dropsTables``, by ``id``) and **shops** (by ``ShopId``) merge one by one: a table or shop only
  one mod adds, changes or removes is taken from it; one several mods change merges field by field, and its items
  (``[ItemId, ...]`` rows) or goods (``{"ItemId": ...}``) merge by item id.  A shop's goods keep ``Index`` = their
  place, as every shop the server ships numbers them.  A field or an item two mods set differently goes to the later
  mod, reported.
* The copies must agree on ``schemas`` (the server reads each row by it); files that do not have this shape are not
  merged (the later mod's copy wins whole, as for any other file).

The merged file is written in the server's own style (``ddo.dumps_style``).  In game UNKNOWN, as for every server
change: the merged file holds only rows, tables and goods the mods and the server already have.
"""
from __future__ import annotations

import difflib
import json

from . import ddo
from .errors import RiftError
from .merging import NONE, MergeError, pick

SPAWN_FILE, SHOP_FILE = ddo.SPAWN_FILE, ddo.SHOP_FILE
FILES = (SPAWN_FILE, SHOP_FILE)


def _fields(base: dict | None, copies: list[tuple[str, dict]], what: str, fights: list, special: dict) -> dict:
    """A dict merged key by key; ``special`` maps a key to its own merge (base value, [(mod, value)]) -> value.
    A key a mod drops counts as a change to 'missing'."""
    b = base if base is not None else {}
    keys = list(b) + [k for _, d in copies for k in d if k not in b]
    out = {}
    for k in dict.fromkeys(keys):
        vals = [(m, d.get(k, NONE)) for m, d in copies]
        old = b.get(k, NONE)
        if k in special and old is not NONE and all(v is not NONE for _, v in vals):
            v = special[k](old, vals)
        else:
            v = pick(old, vals, f"{what}: {k}", fights)
        if v is not NONE:
            out[k] = v
    return out


def _keyed(base: list, copies: list[tuple[str, list]], key, what: str, fights: list, merge_one) -> list:
    """A list of records with ids merged record by record: the server's order, then the ones mods add (in plan order).
    ``merge_one(base record or None, [(mod, record)], label)`` merges one id several mods changed."""
    def index(items, who):
        if not isinstance(items, list):
            raise MergeError(f"{who}: {what} is not a list")
        out = {}
        for it in items:
            try:
                k = key(it)
                hash(k)
            except (TypeError, KeyError, IndexError):
                raise MergeError(f"{who}: {what} has a record without an id") from None
            if k in out:
                raise MergeError(f"{who}: {what} has id {k!r} twice")
            out[k] = it
        return out
    b = index(base, "the server's file")
    mine = [(m, index(items, m)) for m, items in copies]
    order = list(b) + [k for _, d in mine for k in d if k not in b]
    out = []
    for k in dict.fromkeys(order):
        old = b.get(k)
        vals = [(m, d.get(k)) for m, d in mine]
        changed = [(m, v) for m, v in vals if v != old]
        if not changed:
            v = old
        elif all(v == changed[-1][1] for _, v in changed):
            v = changed[-1][1]
        elif old is not None and all(v is not None for _, v in changed):
            v = merge_one(old, changed, f"{what} {k}")
        else:                       # removed by one mod and changed by another, or added by several
            win, v = changed[-1]
            fights.extend((m, win, f"{what} {k}") for m, x in changed[:-1] if x != v)
        if v is not None:
            out.append(v)
    return out


def _whole(old, changed: list[tuple[str, object]], label: str, fights: list):
    win, v = changed[-1]
    fights.extend((m, win, label) for m, x in changed[:-1] if x != v)
    return v


def _rows(base: list, copies: list[tuple[str, list]], fights: list) -> list:
    """Spawn rows merged as sequences against the server's (see the module notes)."""
    def keys(rows):
        return [json.dumps(r, separators=(",", ":"), ensure_ascii=False) for r in rows]
    bk = keys(base)
    hunks = []                                    # (start, end, plan position, mod, rows)
    for pos, (m, rows) in enumerate(copies):
        sm = difflib.SequenceMatcher(None, bk, keys(rows), autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag != "equal":
                hunks.append((i1, i2, pos, m, rows[j1:j2]))
    hunks.sort(key=lambda h: (h[0], h[1], h[2]))
    out, at, k = [], 0, 0
    while k < len(hunks):
        lo, hi = hunks[k][0], hunks[k][1]
        group = [hunks[k]]
        k += 1
        while k < len(hunks):
            s, e = hunks[k][0], hunks[k][1]
            if s < hi or (s == e == lo == hi):    # inside the rows changed, or another insertion at the same place
                group.append(hunks[k])
                hi = max(hi, e)
                k += 1
            else:
                break
        out += base[at:lo]
        at = hi
        if lo == hi:                              # rows added at one place: all of them, in plan order
            seen = []
            for *_, rows in group:
                if rows not in seen:
                    seen.append(rows)
                    out += rows
            continue
        mods = {h[3] for h in group}
        if len(mods) == 1 or all((h[0], h[1], h[4]) == (group[0][0], group[0][1], group[0][4]) for h in group):
            mine = group if len(mods) == 1 else group[:1]
        else:
            win = max(group, key=lambda h: h[2])[3]
            mine = [h for h in group if h[3] == win]
            for m in dict.fromkeys(h[3] for h in group if h[3] != win):
                fights.append((m, win, f"spawn rows {lo + 1}-{hi} of the server's table"))
        p = lo
        for s, e, _pos, _m, rows in sorted(mine, key=lambda h: (h[0], h[1])):
            out += base[p:s] + rows
            p = e
        out += base[p:hi]
    return out + base[at:]


def _spawn(base, docs, fights):
    for who, d in [("the server's file", base)] + docs:
        if not (isinstance(d, dict) and isinstance(d.get("enemies"), list) and isinstance(d.get("schemas"), dict)
                and isinstance(d.get("dropsTables", []), list)
                and all(isinstance(r, list) for r in d["enemies"])):
            raise MergeError(f"{who}: not the server's spawn table (schemas, enemies, dropsTables)")
    if any(d["schemas"] != base["schemas"] for _, d in docs):
        raise MergeError("the copies' schemas differ from the server's; the server reads every row by it")

    def items(old, vals):
        return _keyed(old, vals, lambda it: it[0], "drop items", fights,
                      lambda o, ch, label: _whole(o, ch, label, fights))

    def table(old, changed, label):
        return _fields(old, changed, label, fights, {"items": items})

    return _fields(base, docs, "spawn table", fights, {
        "enemies": lambda old, vals: _rows(old, vals, fights),
        "dropsTables": lambda old, vals: _keyed(old, vals, lambda t: t["id"], "drop table", fights, table)})


def _shops(base, docs, fights):
    for who, d in [("the server's file", base)] + docs:
        if not (isinstance(d, list) and all(isinstance(s, dict) and isinstance(s.get("Data"), dict)
                                            and isinstance(s["Data"].get("GoodsParamList"), list) for s in d)):
            raise MergeError(f"{who}: not the server's shop list (ShopId, Data.GoodsParamList)")
    numbered = all(g.get("Index") == i for s in base for i, g in enumerate(s["Data"]["GoodsParamList"])
                   if isinstance(g, dict))

    def goods(old, vals):
        def one(o, changed, label):
            return _fields(o, changed, label, fights, {})
        if not numbered:
            return _keyed(old, vals, lambda g: g["ItemId"], "goods", fights, one)

        # every shipped shop numbers its goods 0, 1, 2 ... (Index first): the place is not merged but renumbered,
        # so two mods removing different goods do not disagree about the Index of the ones after them
        def plain(gs):
            return [{k: v for k, v in g.items() if k != "Index"} if isinstance(g, dict) else g
                    for g in (gs if isinstance(gs, list) else [])] if isinstance(gs, list) else gs
        out = _keyed(plain(old), [(m, plain(v)) for m, v in vals], lambda g: g["ItemId"], "goods", fights, one)
        return [{"Index": i, **g} for i, g in enumerate(out)]

    def data(old, vals):
        return _fields(old, vals, "shop data", fights, {"GoodsParamList": goods})

    return _keyed(base, docs, lambda s: s["ShopId"], "shop", fights,
                  lambda old, changed, label: _fields(old, changed, label, fights, {"Data": data}))


def merge(rel: str, base: bytes, versions: list[tuple[str, bytes]]) -> tuple[bytes, list[tuple[str, str, str]]]:
    """Several mods' copies of one server file (``(mod, bytes)`` in plan order) merged against the server's own;
    returns the merged file in the server's style and the disagreements, each ``(loser, winner, what)``.  Raises
    MergeError for a file it does not merge or copies that are not the file's shape."""
    if rel not in FILES:
        raise MergeError(f"{rel} is not a file whose copies Riftstone merges")
    if len(versions) < 2:
        raise MergeError("nothing to merge")
    try:
        b, style = ddo.parse_json(base, f"the server's {rel}")
        docs = [(m, ddo.parse_json(data, f"{m}'s {rel}")[0]) for m, data in versions]
    except RiftError as e:
        raise MergeError(str(e)) from None
    fights: list[tuple[str, str, str]] = []
    try:
        out = _spawn(b, docs, fights) if rel == SPAWN_FILE else _shops(b, docs, fights)
        return ddo.dumps_style(out, style), fights
    except RecursionError:
        raise MergeError(f"{rel} is nested too deeply to merge") from None
    except RiftError as e:                        # text the style cannot write back (a lone surrogate escape)
        raise MergeError(str(e)) from None


__all__ = ["FILES", "MergeError", "merge"]

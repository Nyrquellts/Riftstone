"""The game's own placements changed from the world map (Studio's World tab): one moved or turned, given another
enemy, made bigger or tougher, removed, or put back as the game has it -- always inside a mod.

A layout the mod does not hold yet is copied from the game into its ``files/`` (as ``riftstone spawns`` does); one
it holds (in ``files/``, or per archive in ``archives/``, as an encounter writes its new layout) is changed in every
copy, each checked to hold the record the map showed, by number and id.  Every change records what each file held
before and after, so Studio can undo it.  Nothing here installs or touches the game; how a change plays: UNKNOWN
until it is played.  Dark Arisen only (``lot.py``; Online's layouts are ``lot_ddo.py``)."""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from pathlib import Path

from . import encounter, gpl, lot, modfiles, typemap
from .errors import RiftError

LOT, GPL = typemap.BY_EXT["lot"], typemap.BY_EXT["gpl"]
OPS = ("move", "enemy", "set", "remove", "revert", "restore")
OP_WORDS = {"move": "moved", "enemy": "another enemy", "set": "size and health", "remove": "removed",
            "revert": "put back as the game has it", "restore": "a removed one put back"}
UNDO_MAX = 100                   # changes Studio keeps to undo
COORD_MAX = 1e7                  # cm: no stage reaches this far (the open field spans -500,000..+500,000)
SIZE = (0.1, 10.0)               # a size the editor sets, against the setup's own scale
HEALTH = (0.1, 100.0)            # an HP multiplier the editor sets
SNAP = 5000.0                    # cm over and under the asked height searched for ground when a move lands on it
HP_FLAG, HP_RATE = "HP倍率設定の有無", "HPの倍率"     # the placement's HP multiplier (lot.py; docs/enemy-hp.md)
NAME_MAX = 200
FIELD = 100                      # the open field: no navigation mesh, its ground is the cells' collision


def _f32(v: float) -> int:
    return struct.unpack("<I", struct.pack("<f", v))[0]


def _float(bits: int) -> float:
    return struct.unpack("<f", struct.pack("<I", bits & 0xFFFFFFFF))[0]


def layout_name(text) -> tuple[str, lot.LayoutName]:
    """A layout's engine name as the map sends it (``scr\\st320\\etc\\st320_00m00n_e03``; '/' and a ``.lot`` or
    ``.lot.yaml`` ending are taken too), and what the name says."""
    if not isinstance(text, str) or not text or len(text) > NAME_MAX:
        raise RiftError("layout is a layout's engine name, e.g. scr\\st320\\etc\\st320_00m00n_e03")
    name = text.replace("/", "\\")
    for end in (".lot.yaml", ".lot"):
        if name.lower().endswith(end):
            name = name[:-len(end)]
            break
    ln = lot.parse_name(name)
    if ln is None:
        raise RiftError(f"{text!r} is not a layout's name (scr\\st<S>\\etc\\st<S>_<X>m<Z>n_<type><N>)")
    return name, ln


def _whole(v, what: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise RiftError(f"{what} is a whole number")
    return v


def _number(v, what: str, lo: float, hi: float) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise RiftError(f"{what} is a number")
    try:
        f = float(v)
    except OverflowError:
        raise RiftError(f"{what} is far too large") from None
    if not math.isfinite(f) or not lo <= f <= hi:
        raise RiftError(f"{what} is a number from {lo:g} to {hi:g}")
    return f


def _point(v) -> tuple[float, float, float]:
    if not isinstance(v, (list, tuple)) or len(v) != 3:
        raise RiftError("at is [x, y, z]")
    return tuple(_number(c, "at", -COORD_MAX, COORD_MAX) for c in v)


# -- the mod's copies --------------------------------------------------------------------------------------------
@dataclass
class Copy:
    path: Path                   # where it is (or, from the game, where the mod will keep it)
    lot: lot.Lot


def held(mod_root: Path | None, name: str | None = None) -> dict[str, list[Path]]:
    """The layouts the mod holds (``files/`` and every ``archives/`` copy), by engine name; only ``name``'s when
    given."""
    out: dict[str, list[Path]] = {}
    for n, f in modfiles.resources(mod_root, LOT):
        if name is None or n == name:
            out.setdefault(n, []).append(f)
    return out


_READ: dict[str, tuple] = {}          # a mod file's path -> (its size and time, the layout): YAML is slow to read


def _parsed(f: Path) -> lot.Lot:
    st = f.stat()
    key, stamp = str(f), (st.st_size, st.st_mtime_ns)
    got = _READ.get(key)
    if got is None or got[0] != stamp:
        if len(_READ) > 4096:
            _READ.clear()
        got = _READ[key] = (stamp, lot.parse(modfiles.read(f)))
    return got[1]


def stage_layouts(mod_root: Path | None, stage: int) -> dict[str, lot.Lot]:
    """Every layout of the stage the mod holds, as the map shows it (its first copy); a copy that does not read is
    left out (Studio's file view names it).  Read again only when a file changed."""
    out = {}
    for n, files in sorted(held(mod_root).items()):
        ln = lot.parse_name(n)
        if ln is None or ln.stage != stage:
            continue
        try:
            out[n] = _parsed(files[0])
        except (RiftError, OSError):
            continue
    return out


def _copies(game, idx, mod_root: Path, name: str) -> tuple[list[Copy], bool]:
    """Every copy of the layout the mod holds, else the game's to be written into the mod's ``files/`` (and whether
    it is the game's)."""
    files = held(mod_root, name).get(name, [])
    if sum(1 for f in files if f.relative_to(mod_root).parts[0] == "files") > 1:
        raise RiftError(f"the mod has {name} twice in files/ (as YAML and binary); keep one")
    out = []
    for f in files:
        try:
            out.append(Copy(f, lot.parse(modfiles.read(f))))
        except RiftError as e:
            raise RiftError(f"{f.relative_to(mod_root).as_posix()}: {e}") from None
    if out:
        return out, False
    data, path = modfiles.load(game, idx, mod_root, name.encode("latin-1"), LOT)
    return [Copy(path, lot.parse(data))], True


def _game_layout(game, idx, name: str) -> lot.Lot | None:
    try:
        data, _ = modfiles.load(game, idx, None, name.encode("latin-1"), LOT)
    except RiftError:
        return None
    return lot.parse(data)


_GAME: dict[tuple, lot.Lot | None] = {}       # (game folder, layout) -> the game's own layout: the game's files stay stock


def changed(game, idx, layouts: dict[str, lot.Lot]) -> dict[str, set[int]]:
    """For each layout a mod holds (stage_layouts), the ids of its records that are not the game's own: added, or
    any field different (a size or health change too, not only a move)."""
    out = {}
    for name, lt in layouts.items():
        key = (str(game.root), name)
        if key not in _GAME:
            if len(_GAME) > 4096:
                _GAME.clear()
            try:
                _GAME[key] = _game_layout(game, idx, name)
            except RiftError:
                _GAME[key] = None
        g = _GAME[key]
        mine = {r.id: r for r in g.records} if g is not None else {}
        out[name] = {r.id for r in lt.records
                     if r.id not in mine or (mine[r.id].kind, mine[r.id].fields) != (r.kind, r.fields)}
    return out


def _check(c: Copy, number: int, rid: int, name: str) -> lot.Record:
    recs = c.lot.records
    if not 0 <= number < len(recs) or recs[number].id != rid:
        raise RiftError(f"{name} has changed since the map was drawn (its record {number} is not id {rid}); "
                        "reload the stage")
    return recs[number]


def _targets(copies: list[Copy], number: int, rid: int, name: str, root: Path) -> list[int]:
    """The record's number in each copy: the first copy (the one the map shows) by the number and id the map sent,
    the others by the id (a copy per archive)."""
    _check(copies[0], number, rid, name)
    out = []
    for c in copies:
        at = [i for i, r in enumerate(c.lot.records) if r.id == rid]
        if len(at) != 1:
            raise RiftError(f"{c.path.relative_to(root).as_posix()} holds {len(at)} records of id {rid}, not one"
                            + (", so the copies of this layout disagree" if len(copies) > 1 else "")
                            + "; fix that file first")
        out.append(at[0])
    return out


# -- what a record is ------------------------------------------------------------------------------------------
def family(rec: lot.Record) -> str | None:
    """'enemy' for an enemy placement, 'human' for a hostile human placed as an NPC, else None: what the editor
    can give another enemy."""
    if rec.cls.startswith("cSetInfoEnemy"):
        return "enemy"
    if rec.cls == "cSetInfoNpc" and (rec.name or "").lower().startswith("em"):
        return "human"
    return None


def sizable(rec: lot.Record) -> bool:
    """The game sizes this placement by mScale alone (``multiply._sizable``: an enemy, not a wolf choosing its own
    size or a person, never scaled)."""
    from .multiply import _sizable

    return _sizable(rec)


def size_of(rec: lot.Record) -> float | None:
    s = rec.vec("mScale") if sizable(rec) else None
    return round(s[0], 4) if s else None


def health_of(rec: lot.Record) -> float | None:
    if HP_FLAG not in rec.fields:
        return None
    if not rec.fields[HP_FLAG]:
        return 1.0
    v = _float(rec.fields[HP_RATE])
    return round(v, 4) if math.isfinite(v) else None


def facing_of(rec: lot.Record) -> float | None:
    """The heading in degrees, (-180, 180]: 0 faces +Z, 90 faces +X (``docs/arrange.md``)."""
    a = rec.vec("mAngle")
    if a is None or not math.isfinite(a[1]):
        return None
    d = math.degrees(a[1]) % 360.0
    return round(d - 360.0 if d > 180.0 else d, 2)


def describe_record(rec: lot.Record, number: int, w=None) -> dict:
    p = rec.vec()
    em = rec.name or ""
    life = rec.fields.get("mLifePointGroup")
    return {"number": number, "id": rec.id, "class": rec.cls, "name": em,
            "enemy_name": (w.enemies.get(em, {}).get("name", "") if w is not None else ""),
            "family": family(rec), "at": [round(v, 2) for v in p] if p else None, "facing": facing_of(rec),
            "size": size_of(rec), "health": health_of(rec),
            "script": bool(rec.fields.get("mFsmFilePath")), "boss": bool(rec.fields.get("mBossFlag")),
            "life_group": None if life in (None, encounter.NO_LIFE_GROUP) else life}


# -- the group behind an enemy layout ------------------------------------------------------------------------------
def _group(game, idx, w, mod_root, ln: lot.LayoutName):
    """(the group list's engine name, its parsed copy, the group, where the mod keeps the list) for the layout's
    group, as the mod holds the list, else the game's; None when no list of the stage has it."""
    if ln.type == "s":
        return None
    names = [ln.group_list]
    if ln.type == "e":
        names += [n for n in encounter.group_list_names(w, ln.stage) if n not in names]
    for n in names:
        try:
            data, out = modfiles.load(game, idx, mod_root, n.encode("latin-1"), GPL)
        except RiftError:
            continue
        doc = gpl.parse(data)
        for g in doc.groups:
            if g["mGroup"] == ln.number:
                return n, doc, g, out
    return None


def _group_notes(g: dict | None) -> list[str]:
    if g is None:
        return []
    out = []
    if any(g.get(k) for k in encounter.SCRIPTED):
        out.append("its group waits for a state machine's order (a quest or the story sets it up), so a change here "
                   "can matter to that piece of the game")
    if g.get("mLoadCondition.mLotFlag") or g.get("mLoadCondition.mLotFlag2"):
        out.append(f"its group loads only while lot flag {g.get('mDataLotFlag.mFlagNo', 0)} is set (what sets it: "
                   "UNKNOWN here)")
    return out


def describe(game, idx, w, mod_root: Path | None, layout, number, rid) -> dict:
    """One placement for the map's inspector: the record as the mod holds it (else the game's), what its group is,
    and whether the game has a record of that id to put back."""
    name, ln = layout_name(layout)
    number, rid = _whole(number, "number"), _whole(rid, "id")
    files = held(mod_root, name).get(name, []) if mod_root is not None else []
    if files:
        c = Copy(files[0], lot.parse(modfiles.read(files[0])))
    else:
        g = _game_layout(game, idx, name)
        if g is None:
            raise RiftError(f"the game has no layout {name}")
        c = Copy(Path(), g)
    rec = _check(c, number, rid, name)
    game_lot = _game_layout(game, idx, name)
    found = _group(game, idx, w, mod_root, ln)
    out = describe_record(rec, number, w)
    out.update({"layout": name, "stage": ln.stage, "type": ln.type, "group": None if ln.type == "s" else ln.number,
                "held": bool(files), "copies": len(files),
                "in_game": game_lot is not None and any(r.id == rid for r in game_lot.records),
                "group_units": [u["name"] for u in found[2]["mUnitKindList"]] if found else [],
                "notes": _group_notes(found[2] if found else None)})
    return out


# -- the ground a moved placement stands on ------------------------------------------------------------------------
def stage_ground(game, idx, stage: int, mod_root: Path | None = None):
    """``f(x, y, z) -> height or None``: the stage's walkable ground under a point -- its navigation mesh (the
    floor nearest the asked height's window first), else the open field's walkable collision -- or None for a
    stage with neither."""
    from . import nav, terrain

    mesh = nav.stage_mesh(game, idx, stage, mod_root)
    if mesh is not None:
        def on_mesh(x, y, z):
            s = mesh.locate((x, y, z)) or mesh.locate((x, y, z), SNAP, SNAP)
            return s.point[1] if s is not None else None
        return on_mesh
    if stage == FIELD:
        sbc_type = typemap.BY_EXT["sbc"]
        field_ = terrain.Ground(lambda n: nav.game_resource(game, idx, n.encode("latin-1"), sbc_type))

        def on_field(x, y, z):
            got = field_.under((x, y, z), SNAP, SNAP)
            return got[0] if got is not None else None
        return on_field
    return None


# -- a change ----------------------------------------------------------------------------------------------------
@dataclass
class Change:
    layout: str
    op: str
    number: int | None                         # the record's number after the change; None once removed
    record: dict | None
    notes: list[str] = field(default_factory=list)
    files: list[tuple[Path, bytes | None, bytes]] = field(default_factory=list)   # (file, before or None, after)


def _save(change: Change, path: Path, data: bytes, name: str, tid: int) -> None:
    before = path.read_bytes() if path.is_file() else None
    modfiles.save(path, data, name.encode("latin-1"), tid)
    change.files.append((path, before, path.read_bytes()))


def _swap(game, idx, w, rec: lot.Record, em: str, stage: int) -> tuple[lot.Record, dict]:
    """Record ``rec`` as enemy ``em``: the game's commonest ordinary setup of it (``encounter._source_records``) of
    the same family, standing where ``rec`` stands, facing its way, with its id."""
    fam = family(rec)
    if fam is None:
        raise RiftError(f"record {rec.id} is a {rec.cls}, not an enemy: only enemies and hostile humans take "
                        "another enemy here")
    rows = [(p, r, n) for p, r, n in encounter._source_records(game, idx, w, em, stage, limit=64) if family(r) == fam]
    if not rows:
        other = "a hostile human (an NPC layout)" if fam == "enemy" else "a monster (an enemy layout)"
        raise RiftError(f"{em} is placed only as {other} in the game, so it cannot stand in this "
                        f"{'enemy' if fam == 'enemy' else 'NPC'} layout; add it as an encounter instead")
    src_row, src, uses = rows[0]
    new = src.copy()
    new.id = rec.id
    for k in ("mPosition", "mAngle"):
        if k in rec.fields and k in new.fields:
            new.fields[k] = rec.fields[k]
    return new, {"layout": src_row[0], "number": src_row[1], "uses": uses}


def edit(game, idx, w, mod_root: Path, body: dict, ground=None) -> Change:
    """One change to a placement of the map, written into ``mod_root``.  ``body``: ``layout``, ``number`` and ``id``
    (the record as the map showed it), ``op`` (``OPS``) and its values -- move: ``at`` [x, y, z], ``facing``
    (degrees, optional), ``ground`` (true: the height from ``ground(x, y, z)``, the stage's walkable ground, when
    it has some there); enemy: ``enemy`` (an id or name); set: ``size`` and/or ``health``; remove; revert (the
    record as the game has it)."""
    if game.is_ddo:
        raise RiftError("the map's placement editor is for Dark Arisen's layouts; Online's: riftstone spawns")
    op = body.get("op")
    if op not in OPS:
        raise RiftError("op is one of " + ", ".join(OPS))
    name, ln = layout_name(body.get("layout"))
    rid = _whole(body.get("id"), "id")
    if op == "restore":
        return _restore(game, idx, w, mod_root, name, rid)
    number = _whole(body.get("number"), "number")
    copies, from_game = _copies(game, idx, mod_root, name)
    if from_game and op == "revert":
        raise RiftError(f"the mod does not change {name}: it is already as the game has it")
    at_ = _targets(copies, number, rid, name, mod_root)
    recs = [c.lot.records[i] for c, i in zip(copies, at_)]
    change = Change(name, op, number, None)
    if from_game:
        change.notes.append(f"the mod did not have this layout yet: it now keeps its own copy "
                            f"({copies[0].path.relative_to(mod_root).as_posix()})")
    elif len(copies) > 1:
        change.notes.append(f"changed in all {len(copies)} copies the mod keeps (one per archive)")

    if op == "move":
        at = _point(body.get("at"))
        facing = body.get("facing")
        facing = None if facing is None else _number(facing, "facing", -1e6, 1e6)
        snap = body.get("ground")
        if snap is not None and not isinstance(snap, bool):
            raise RiftError("ground is true (stand on the stage's ground) or false (exactly at the height given)")
        if snap:
            h = ground(*at) if ground is not None else None
            if h is None:
                change.notes.append("no walkable ground was found under that point, so it keeps the height given")
            else:
                at = (at[0], h, at[2])
        for r in recs:
            if "mPosition" not in r.fields:
                raise RiftError(f"record {rid} ({r.cls}) has no position")
            r.set_vec("mPosition", at)
            if facing is not None and "mAngle" in r.fields:
                a = r.vec("mAngle")
                y = math.radians(facing) % (2.0 * math.pi)
                r.set_vec("mAngle", (a[0], y - 2.0 * math.pi if y > math.pi else y, a[2]))
    elif op == "enemy":
        em = w.find_enemy(str(body.get("enemy", "")))
        new, src = _swap(game, idx, w, recs[0], em, ln.stage)
        for c, i in zip(copies, at_):
            c.lot.records[i] = new.copy()
        change.notes.append(f"{em} takes the game's commonest ordinary setup for it ({src['uses']} placement"
                            f"{'s' if src['uses'] != 1 else ''} use it, e.g. record {src['number']} of "
                            f"{src['layout']})")
        if em not in w.stage(ln.stage).get("units", {}):
            change.notes.append(f"stage {ln.stage} never uses {em}: installing the mod puts it in the stage_enemies "
                                "feature's list, which must be on for it to load there (in game UNKNOWN)")
        found = _group(game, idx, w, mod_root, ln)
        if found is not None:
            gname, doc, g, gout = found
            units = [u["name"] for u in g["mUnitKindList"]]
            if em not in units:
                if len(units) < gpl.UNIT_KINDS_MAX:
                    g["mUnitKindList"].append({"name": em, "isBelong": encounter._unit_belong(w, em)})
                    _save(change, gout, gpl.build(doc), gname, GPL)
                    change.notes.append(f"{em} added to group {ln.number}'s kinds of unit ({gname}.gpl)")
                else:
                    change.notes.append(f"group {ln.number} already lists {gpl.UNIT_KINDS_MAX} kinds of unit, the most "
                                        f"the game reads, so {em} is not listed there (whether it appears: UNKNOWN)")
    elif op == "set":
        size, health = body.get("size"), body.get("health")
        if size is None and health is None:
            raise RiftError("say the size, the health, or both")
        size = None if size is None else _number(size, "size", *SIZE)
        health = None if health is None else _number(health, "health", *HEALTH)
        for r in recs:
            if size is not None:
                cur = r.vec("mScale") if sizable(r) else None
                if not cur or cur[0] == 0.0:
                    raise RiftError(f"the game sizes {r.name or r.cls} itself (a wolf picks its own size; people "
                                    "are never scaled), so its size cannot be set here")
                k = size / cur[0]
                r.set_vec("mScale", [v * k for v in cur])
            if health is not None:
                if HP_FLAG not in r.fields:
                    raise RiftError(f"a {r.cls} has no health multiplier")
                if health == 1.0:
                    r.fields[HP_FLAG] = 0
                else:
                    r.fields[HP_FLAG], r.fields[HP_RATE] = 1, _f32(health)
    elif op == "remove":
        change.notes += _remove_notes(recs[0])
        found = _group(game, idx, w, mod_root, ln)
        change.notes += _group_notes(found[2] if found else None)
        for c, i in zip(copies, at_):
            c.lot = lot.remove(c.lot, i)
        change.number = None
    else:                                                                       # revert
        g = _game_layout(game, idx, name)
        orig = next((r for r in (g.records if g else []) if r.id == rid), None)
        if orig is None:
            raise RiftError(f"the game has no record id {rid} in {name} (the mod added it): remove it instead")
        for c, i in zip(copies, at_):
            c.lot.records[i] = orig.copy()

    for c in copies:
        _save(change, c.path, lot.build(c.lot), name, LOT)
    if change.number is not None:
        change.record = describe_record(copies[0].lot.records[number], number, w)
    return change


def _restore(game, idx, w, mod_root: Path, name: str, rid: int) -> Change:
    """A placement the mod removed put back: the game's record of that id, in every copy the mod keeps, after the
    records the game has before it."""
    copies = [Copy(f, lot.parse(modfiles.read(f))) for f in held(mod_root, name).get(name, [])]
    if not copies:
        raise RiftError(f"the mod does not change {name}: nothing of it was removed")
    g = _game_layout(game, idx, name)
    order = [r.id for r in (g.records if g else [])]
    if rid not in order:
        raise RiftError(f"the game has no record id {rid} in {name}")
    orig = g.records[order.index(rid)]
    earlier = set(order[:order.index(rid)])
    change = Change(name, "restore", None, None)
    for c in copies:
        if any(r.id == rid for r in c.lot.records):
            raise RiftError(f"{c.path.relative_to(mod_root).as_posix()} still has record id {rid}: there is "
                            "nothing to put back")
        at = max((i + 1 for i, r in enumerate(c.lot.records) if r.id in earlier), default=0)
        c.lot = lot.Lot([r.copy() for r in c.lot.records[:at]] + [orig.copy()] +
                        [r.copy() for r in c.lot.records[at:]], c.lot.version)
        if c is copies[0]:
            change.number = at
    for c in copies:
        _save(change, c.path, lot.build(c.lot), name, LOT)
    change.record = describe_record(copies[0].lot.records[change.number], change.number, w)
    return change


def removed(w, name: str, held_lot: lot.Lot) -> list:
    """The game's placements of layout ``name`` the mod's copy no longer has, as the world map lists them
    (``[layout, number, id, kind, name, position, ...]``)."""
    have = {r.id for r in held_lot.records}
    return [p for p in w.placements_in(name) if p[2] not in have]


def _remove_notes(rec: lot.Record) -> list[str]:
    out = []
    if rec.fields.get("mFsmFilePath"):
        out.append("it ran an AI script of its own (a quest's or a cut-scene's): that piece of the game may wait for "
                   "it")
    if rec.fields.get("mBossFlag"):
        out.append("it was a big monster (its boss flag was set)")
    life = rec.fields.get("mLifePointGroup")
    if life not in (None, encounter.NO_LIFE_GROUP):
        out.append(f"it shared life point group {life} with other placements")
    return out


def undo(files: list[tuple[Path, bytes | None, bytes]]) -> list[Path]:
    """Put back what a change replaced, last file first; refuses (changing nothing) when any of them changed since."""
    for path, _before, after in files:
        now = path.read_bytes() if path.is_file() else None
        if now != after:
            raise RiftError(f"{path.name} changed since (saved elsewhere?), so the change is not undone")
    done = []
    for path, before, _after in reversed(files):
        if before is None:
            path.unlink()
        else:
            path.write_bytes(before)
        done.append(path)
    return done

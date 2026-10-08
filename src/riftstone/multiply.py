"""Every spawn multiplied: ``riftstone multiply 2`` gives Dark Arisen twice as many enemies wherever it places them.

The game spawns an enemy for each placement of a group (``docs/spawn-system.md``), so N times as many enemies is
N - 1 copies of every placement, written into the group's own layouts: the copies appear when their group does, by
the same lot flag, hours and story window, and count in the same kill record.  ``plan`` reads every enemy layout of
the game as the game ships it and makes a mod of the changed ones (and of the group lists whose spawn cap grows);
``write`` puts it into a mod, which installs like any other and merges with other mods' layouts record by record
(``lotmerge.py``).  How many more a group gets, and what happens to its cap, is ``rules/multiply.nyr``.

What a copy keeps and what it does not:

* its id is a free one under 32 that no layout of its group uses: the kill record is one bit an id (``lot.KILL_BITS``).
  A group whose ids run out gets the copies that fit, and the report counts the rest;
* it stands on the ground beside its original.  In a stage with a navigation mesh that is a point of the mesh
  reached on foot from the original, with as much room to the mesh's edge as the original has; in the open field
  (no mesh) a point of the cell's walkable collision (``terrain.Ground``) no steeper than about 45 degrees with
  ground between it and the original.  A placement off the ground (flyers, wall crawlers), or in a stage with
  neither, gets its copies close beside it at its own height;
* placements with their own AI script are left alone (a quest's), and so are big monsters (``mBossFlag``) unless
  asked for (``bosses``); the Dragon, the Ur-Dragon and Daimon never get a copy: their fights are the engine's own
  sequences (``docs/fsm-grigori-and-waves.md``);
* unless ``plain``, a copy faces a little another way and is a little smaller or larger than its original (the
  game sizes placements itself: 37 of its own carry a scale, 0.6 to 2.5), and a pack of three or more gets one
  champion: the size of the game's hobgoblin leaders, with twice the health through the placement's own HP
  multiplier.  People (human enemies) keep their size.

Nothing here is random: the same game and options give the same bytes.  In game: UNKNOWN until played.
"""
from __future__ import annotations

import hashlib
import json
import math
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from . import arcfolder, gpl, lot, modfiles, params, typemap
from .errors import ParamError, RiftError
from .rules import multiply as _rule

RECORD = "riftstone-multiply.json"      # in the mod's root: what this command wrote there, so a re-run replaces it
SCHEMA = "riftstone-multiply/1"
MOD_NAME = "Spawn Multiplier"           # the mod the command line writes when none is named
FACTOR_MIN, FACTOR_MAX = 2, 10
SPREAD = 150.0                          # cm between a copy and its original (the game's own neighbours: median 374,
SPREAD_MIN, SPREAD_MAX = 60.0, 1000.0   # a quarter of them within 204)
BESIDE = 90.0                           # cm, where the ground is not known: close beside the original
RINGS = 4                               # candidate rings around the original: 6, 12, 18 and 24 spots
RISE = 250.0                            # cm a copy's ground may lie over or under its original's
ROOM = 150.0                            # cm to the mesh's edge a copy asks for at most (bestiary.ROOM_FLOOR)
OFF_GROUND = 100.0                      # cm over the field's ground: beyond it a placement is not standing on it
BIG_SPREAD = 3.0                        # a big monster's copies stand this many times as far away
TURN = 0.45                             # radians a copy may face away from its original's heading, either way
SIZES = (0.92, 1.12)                    # a copy's size against its original's
CHAMPION_PACK = 3                       # a group with this many ordinary copies gets one champion
CHAMPION_SIZE = 1.3                     # the game's own: its three scaled hobgoblin leaders are 1.3
CHAMPION_HP = 2.0
STORY = ("em580", "em700")              # the Dragon and the Ur-Dragon, Daimon: never copied
PEOPLE = "em1"                          # human enemies: built from equipment, the game never scales one
NO_SCRIPT = (None, b"")
_HP_FLAG, _HP_RATE = "HP倍率設定の有無", "HPの倍率"


@dataclass
class Plan:
    factor: int
    options: dict
    files: dict = field(default_factory=dict)       # engine name -> (type id, the resource's new bytes)
    stages: dict = field(default_factory=dict)      # stage -> [placements the game has, copies made]
    counts: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)

    @property
    def copies(self) -> int:
        return self.counts.get("copies", 0)

    def summary(self) -> list[str]:
        c = self.counts
        n = lambda k: c.get(k, 0)                                                     # noqa: E731
        s = lambda v, one, many=None: f"{v:,} {one if v == 1 else many or one + 's'}"  # noqa: E731
        out = [f"x{self.factor}: {s(n('copies'), 'more enemy placement')} in {s(n('groups'), 'group')} of "
               f"{s(sum(1 for v in self.stages.values() if v[1]), 'stage')} (the game places {n('placed'):,} there)"]
        where = [(n("on_mesh"), "on a navigation mesh"), (n("on_field"), "on the open field's ground"),
                 (n("beside"), "close beside their original (off the ground, or no ground known)")]
        if n("copies"):
            out.append("  where they stand: " + "; ".join(f"{v:,} {t}" for v, t in where if v))
        left = [(n("scripted"), "with their own AI script"), (n("story"), "of the Dragon, the Ur-Dragon or Daimon"),
                (n("big"), "big monsters (--bosses multiplies them too)"), (n("other_enemy"), "other enemies"),
                (n("unlisted"), "in a group no group list has")]
        if any(v for v, _ in left):
            out.append("  left as the game has them: " + "; ".join(f"{v:,} {t}" for v, t in left if v))
        if n("short"):
            out.append(f"  {s(n('short'), 'copy', 'copies')} did not fit: a group's kill record has {lot.KILL_BITS} "
                       f"placement ids ({s(n('short_groups'), 'group')} full)")
        if n("caps"):
            out.append(f"  spawn cap multiplied in {s(n('caps'), 'group')} (hordes and picked sets)")
        if n("champions"):
            out.append(f"  {s(n('champions'), 'champion')}: one in each pack of {CHAMPION_PACK} or more copies, "
                       f"{CHAMPION_SIZE:g} times the size with {CHAMPION_HP:g} times the health")
        return out + self.notes


# -- which placements --------------------------------------------------------------------------------------
def _script(rec: lot.Record):
    return rec.fields.get("FSMPath" if rec.cls == "cSetInfoNpc" else "mFsmFilePath")


def _why_not(rec: lot.Record, bosses: bool, enemies) -> str | None:
    """None when the placement gets copies, else the count it is left under."""
    name = rec.name
    if not (rec.cls.startswith("cSetInfoEnemy") or rec.cls == "cSetInfoNpc") or not name or "mPosition" not in rec.fields:
        return "not_enemy"
    if enemies is not None and name.lower() not in enemies:
        return "other_enemy"
    if _script(rec) not in NO_SCRIPT:
        return "scripted"
    if name.lower().startswith(STORY):
        return "story"
    if rec.fields.get("mBossFlag") and not bosses:
        return "big"
    return None if all(math.isfinite(v) for v in rec.vec()) else "not_enemy"


def _sizable(rec: lot.Record) -> bool:
    """The game sizes this kind of placement by mScale alone: an enemy class (wolves pick their own size, people
    are never scaled) whose scale is three ordinary numbers."""
    if not rec.cls.startswith("cSetInfoEnemy") or "mIsRandamScale" in rec.fields \
            or (rec.name or "").lower().startswith(PEOPLE):
        return False
    s = rec.vec("mScale")
    return s is not None and all(math.isfinite(v) and 0.05 <= abs(v) <= 20.0 for v in s)


def _unit(key: str) -> float:
    """A number in [0, 1) that is this key's own: the same placement gets the same copy on every run."""
    return zlib.crc32(key.encode("latin-1", "replace")) / 4294967296.0


def _f32(v: float) -> int:
    return struct.unpack("<I", struct.pack("<f", v))[0]


def _vary(rec: lot.Record, key: str) -> None:
    a = rec.vec("mAngle")
    if a is not None and all(math.isfinite(v) for v in a):
        y = a[1] + (_unit(key + "/turn") * 2.0 - 1.0) * TURN
        rec.set_vec("mAngle", (a[0], (y + math.pi) % (2.0 * math.pi) - math.pi, a[2]))
    if _sizable(rec):
        k = SIZES[0] + (SIZES[1] - SIZES[0]) * _unit(key + "/size")
        rec.set_vec("mScale", [v * k for v in rec.vec("mScale")])


def _champion(rec: lot.Record, was: lot.Record) -> None:
    """The pack's champion: the size of the game's hobgoblin leaders against its original, and its health doubled
    through the placement's own HP multiplier (on top of one the game gave it)."""
    rec.set_vec("mScale", [v * CHAMPION_SIZE for v in was.vec("mScale")])
    rate = CHAMPION_HP
    if was.fields.get(_HP_FLAG):
        own = struct.unpack("<f", struct.pack("<I", was.fields[_HP_RATE] & 0xFFFFFFFF))[0]
        if math.isfinite(own) and 0.0 < own < 1000.0:
            rate = own * CHAMPION_HP
    rec.fields[_HP_FLAG], rec.fields[_HP_RATE] = 1, _f32(rate)


# -- where a copy stands -----------------------------------------------------------------------------------
class _Points:
    """The placements of a stage, to keep copies apart from every enemy already there."""

    CELL = 300.0

    def __init__(self):
        self._grid: dict = {}

    def _key(self, p):
        return math.floor(p[0] / self.CELL), math.floor(p[2] / self.CELL)

    def add(self, p) -> None:
        self._grid.setdefault(self._key(p), []).append(p)

    def near(self, p, r: float) -> bool:
        gx, gz = self._key(p)
        reach = int(r // self.CELL) + 1
        for x in range(gx - reach, gx + reach + 1):
            for z in range(gz - reach, gz + reach + 1):
                for q in self._grid.get((x, z), ()):
                    if math.dist(p, q) < r:
                        return True
        return False


def _slots(p, step: float, key: str, nth: int):
    """Spots around p: rings of 6, 12, 18... at step, 2 x step..., turned by the placement's own number, and the
    nth copy starting on another side of the ring than the one before it."""
    phase = _unit(key) * 2.0 * math.pi
    for ring in range(1, RINGS + 1):
        k = 6 * ring
        first = round(k * ((nth * 0.618034) % 1.0))
        for i in range(k):
            a = phase + 2.0 * math.pi * ((first + i) % k) / k
            yield (p[0] + step * ring * math.sin(a), p[1], p[2] + step * ring * math.cos(a))


class _Ground:
    """One stage's ground, as far as the game's files say where it is."""

    def __init__(self, game, idx, stage: int):
        from . import nav, terrain

        self.points = _Points()
        self.mesh = nav.stage_mesh(game, idx, stage)
        self.field = None
        if self.mesh is None and stage == 100:
            sbc_type = typemap.BY_EXT["sbc"]
            self.field = terrain.Ground(lambda name: nav.game_resource(game, idx, name.encode("latin-1"), sbc_type))
        self._reach: dict = {}

    def place(self, p, spread: float, key: str, nth: int, big: bool):
        """(where the nth copy of the placement at p stands, how it was found)."""
        wide = BIG_SPREAD if big else 1.0
        got = None
        if self.mesh is not None:
            got, how = self._on_mesh(p, spread * wide, key, nth), "on_mesh"
        elif self.field is not None:
            got, how = self._on_field(p, spread * wide, key, nth), "on_field"
        if got is None:
            got, how = self._beside(p, min(BESIDE, spread) * wide, key, nth), "beside"
        self.points.add(got)
        return got, how

    def _on_mesh(self, p, step: float, key: str, nth: int):
        mesh = self.mesh
        start = mesh.locate(p)
        if start is None:
            return None
        t = (start.triangle, step)
        if t not in self._reach:
            if len(self._reach) > 256:
                self._reach.clear()
            self._reach[t] = mesh.distances([start.triangle], limit=2.0 * step * RINGS / 100.0 + 5.0)
        reach = self._reach[t]
        room = min(ROOM, mesh.clearance(start.point, limit=ROOM))
        first = None
        for c in _slots(start.point, step, key, nth):
            spot = mesh.nearest(c, step * 0.5, above=RISE, below=RISE, within=reach)
            if spot is None:
                continue
            if reach.get(spot.triangle, math.inf) > 2.0 * math.dist(spot.point, start.point) / 100.0 + 5.0:
                continue                                  # behind a wall or across a drop
            if room > 1.0 and mesh.clearance(spot.point, limit=room) < room - 0.5:
                continue
            if first is None:
                first = spot.point
            if not self.points.near(spot.point, 0.6 * step):
                return spot.point
        return first

    def _on_field(self, p, step: float, key: str, nth: int):
        from . import terrain

        g0 = self.field.under(p)
        if g0 is None or abs(p[1] - g0[0]) > OFF_GROUND:
            return None
        foot = (p[0], g0[0], p[2])
        first = None
        for c in _slots(foot, step, key, nth):
            g = self.field.under(c, above=RISE, below=RISE)
            if g is None or g[1] < terrain.GROUND_SLOPE:
                continue
            mid = ((foot[0] + c[0]) / 2.0, (foot[1] + g[0]) / 2.0, (foot[2] + c[2]) / 2.0)
            if self.field.under(mid, above=RISE, below=RISE) is None:
                continue                                  # a gap between the two
            q = (c[0], g[0] + min(max(p[1] - g0[0], 0.0), 35.0), c[2])
            if first is None:
                first = q
            if not self.points.near(q, 0.6 * step):
                return q
        return first

    def _beside(self, p, step: float, key: str, nth: int):
        first = None
        for c in _slots(p, step, key, nth):
            if first is None:
                first = c
            if not self.points.near(c, 0.6 * step):
                return c
        return first


# -- the plan ----------------------------------------------------------------------------------------------
def _number(value, name: str, lo, hi, kind=int):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or (kind is int and not isinstance(value, int)) \
            or not math.isfinite(value) or not lo <= value <= hi:
        raise ParamError(f"{name} is a {'whole ' if kind is int else ''}number from {lo:g} to {hi:g}, not {value!r}")
    return kind(value)


def _rule_for(factor: int, eligible: int, cap: int, free: int) -> dict:
    got = _rule.evaluate({"factor": factor, "eligible": eligible, "cap": cap, "free": free})
    return {u.name: int(u.value) for u in got.updates}


def plan(game, idx, w, factor, *, stages=None, enemies=None, bosses: bool = False, spread: float = SPREAD,
         plain: bool = False, champions: bool = True, progress=None) -> Plan:
    """Every enemy group of the game (or of ``stages``) with ``factor`` times its placements.  ``enemies`` (ids like
    em0100) limits it to those; ``progress(stage, done, of)`` is called as the stages go by."""
    if getattr(game, "kind", "ddda") != "ddda":
        raise RiftError("riftstone multiply changes Dark Arisen's layouts. Dragon's Dogma Online's enemies come from "
                        "the server's spawn table: 'riftstone ddo' edits that")
    factor = _number(factor, "the factor", FACTOR_MIN, FACTOR_MAX)
    spread = _number(spread, "--spread", SPREAD_MIN, SPREAD_MAX, float)
    if enemies is not None:
        enemies = {str(e).lower() for e in enemies}
        unknown = sorted(e for e in enemies if e not in {k.lower() for k in w.enemies})
        if unknown:
            raise ParamError(f"{', '.join(unknown)}: not an enemy of this game (riftstone world enemies lists them)")
    by_stage: dict = {}
    for name, lay in w.layouts.items():
        if lay["type"] == "e":
            by_stage.setdefault(lay["stage"], {}).setdefault(lay["number"], []).append(name)
    if stages is not None:
        stages = sorted({_number(s, "a stage", 0, 999) for s in stages})
        for s in stages:
            w.stage(s)
    LOT, GPL = typemap.BY_EXT["lot"], typemap.BY_EXT["gpl"]
    p = Plan(factor, {"bosses": bool(bosses), "spread": spread, "plain": bool(plain),
                      "champions": bool(champions) and not plain,
                      "stages": stages, "enemies": sorted(enemies) if enemies is not None else None})
    c = p.counts
    caps: dict = {}                                       # group list -> {group number: its new cap}

    def bump(k: str, by: int = 1) -> None:
        c[k] = c.get(k, 0) + by

    todo = [s for s in sorted(by_stage) if stages is None or s in stages]
    for done, stage in enumerate(todo):
        if progress is not None:
            progress(stage, done, len(todo))
        ground = None
        lots: dict = {}
        for number in sorted(by_stage[stage]):
            for name in sorted(by_stage[stage][number]):
                data, _ = modfiles.load(game, idx, None, name.encode("latin-1"), LOT)
                lots[name] = lot.parse(data)
        changed = set()
        tally = p.stages.setdefault(stage, [0, 0])
        for number in sorted(by_stage[stage]):
            names = sorted(by_stage[stage][number])
            row = w.group(stage, "e", number)
            used = {r.id for n in names for r in lots[n].records}
            eligible = []
            for n in names:
                for r in lots[n].records:
                    why = _why_not(r, bosses, enemies)
                    if why == "not_enemy":
                        continue
                    tally[0] += 1
                    bump("placed")
                    if why is None and row is None:
                        why = "unlisted"
                    if why is None:
                        eligible.append((n, r))
                    else:
                        bump(why)
            if not eligible:
                continue
            free = sum(1 for i in range(lot.KILL_BITS) if i not in used)
            out = _rule_for(factor, len(eligible), int(row["count_max"]), free)
            make = max(0, min(out.get("copies", 0), free))
            wanted = len(eligible) * (factor - 1)
            if make < wanted:
                bump("short", wanted - make)
                bump("short_groups")
            if ground is None and make:
                ground = _Ground(game, idx, stage)
                for L in lots.values():
                    for r in L.records:
                        if "mPosition" in r.fields and all(math.isfinite(v) for v in r.vec()):
                            ground.points.add(r.vec())
            ordinary = []
            for j in range(make):
                n, was = eligible[j % len(eligible)]
                nth = j // len(eligible)
                key = f"{n}#{was.id}#{nth}"
                rec = was.copy()
                rec.id = lot.free_id(lots[n], used, lot.KILL_BITS)
                if rec.id >= lot.KILL_BITS or rec.id in used:
                    raise RiftError(f"stage {stage} group {number}: no placement id is free under {lot.KILL_BITS}")
                big = bool(was.fields.get("mBossFlag"))
                at, how = ground.place(was.vec(), spread, key, nth, big)
                rec.set_vec("mPosition", at)
                if not plain:
                    _vary(rec, key)
                    if not big and _sizable(was):
                        ordinary.append((key, rec, was))
                lots[n].records.append(rec)
                used.add(rec.id)
                changed.add(n)
                bump("copies")
                bump(how)
                tally[1] += 1
            if make:
                bump("groups")
            if p.options["champions"] and len(ordinary) >= CHAMPION_PACK:
                _key, rec, was = ordinary[int(_unit(f"{stage}/{number}/champion") * len(ordinary))]
                _champion(rec, was)
                bump("champions")
            cap = out.get("set_count_max")
            if cap is not None and cap != row["count_max"]:
                caps.setdefault(row["list"], {})[number] = cap
                bump("caps")
        for n in sorted(changed):
            lot.check_ids(lots[n])
            p.files[n] = (LOT, lot.build(lots[n]))
    for listname in sorted(caps):
        data, _ = modfiles.load(game, idx, None, listname.encode("latin-1"), GPL)
        g = gpl.parse(data)
        for grp in g.groups:
            if grp["mGroup"] in caps[listname]:
                grp["mSetCountMax"] = caps[listname][grp["mGroup"]]
        p.files[listname] = (GPL, gpl.build(g))
    if not p.copies:
        p.notes.append("  nothing to multiply: no enemy placement here matches (see what was left above)")
    return p


def footprint(game, idx, p: Plan) -> tuple[int, int]:
    """(the game's archives that hold what the plan changes, their size together in bytes): what install rebuilds
    into the loader's overlay.  A layout of the open field sits in three archives of its cell, two of them the
    cell's models, and a dungeon's in its stage archive, so the whole game is thousands of megabytes."""
    arcs = set()
    for name, (tid, _data) in p.files.items():
        arcs.update(idx.archives_with(name.encode("latin-1"), tid))
    size = 0
    for a in arcs:
        try:
            size += Path(game.vanilla_arc(a)).stat().st_size
        except OSError:
            pass
    return len(arcs), size


# -- into a mod --------------------------------------------------------------------------------------------
def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_record(mod_root: Path) -> dict | None:
    """What an earlier run wrote into this mod ({"factor", "options", "files": {path in the mod: SHA-256}}), None
    when there was none; a file of that name that is not this command's record is refused, not overwritten."""
    f = Path(mod_root) / RECORD
    if not f.is_file():
        return None
    try:
        rec = json.loads(f.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, RecursionError):
        rec = None
    files = rec.get("files") if isinstance(rec, dict) else None
    if not isinstance(rec, dict) or rec.get("schema") != SCHEMA or not isinstance(files, dict) or not all(
            isinstance(k, str) and isinstance(v, str) and not Path(k).is_absolute() and ".." not in Path(k).parts
            and Path(k).parts[:1] == ("files",) for k, v in files.items()):
        raise RiftError(f"{f} is not a record 'riftstone multiply' wrote (schema {SCHEMA}); move it away, or give the "
                        "multiplier a mod of its own with --mod")
    return rec


def write(p: Plan, mod_root: Path) -> list[Path]:
    """Put the plan into a mod, as YAML under files/.  What an earlier run wrote there and nobody changed since is
    replaced (a new factor does not pile onto the old one: every run starts from the game's own layouts); a layout
    or group list the mod holds from anything else is refused before a file is written."""
    mod_root = Path(mod_root).resolve()
    old = read_record(mod_root)
    mine = {}
    for rel, sha in (old or {}).get("files", {}).items():
        f = mod_root / rel
        if f.is_file() and _sha(f.read_bytes()) == sha:
            mine[f.resolve()] = rel
    outs: dict = {}
    for name in sorted(p.files):
        tid, data = p.files[name]
        text = params.resource_to_yaml(data, name, tid)
        if text is None:
            raise RiftError(f"{name}: this resource has no YAML form")
        outs[name.lower()] = (modfiles.paths(mod_root, name.encode("latin-1"), tid)[0], text.encode("utf-8"))
    foreign = []
    for tid in sorted({t for t, _ in p.files.values()}):
        for name, f in modfiles.resources(mod_root, tid):
            want = outs.get(name.lower())
            if want is None or f.resolve() in mine:
                continue
            if f.resolve() == want[0].resolve() and f.read_bytes() == want[1]:
                continue                                  # a run that stopped before its record was written
            foreign.append(f)
    if foreign:
        shown = ", ".join(str(f.relative_to(mod_root)) for f in foreign[:3])
        raise RiftError(f"{mod_root.name} already holds {len(foreign):,} of the layouts and group lists this would "
                        f"write, from something else or changed since ({shown}{' ...' if len(foreign) > 3 else ''}). "
                        "Give the multiplier a mod of its own (--mod NAME): installed together, the two mods' "
                        "layouts merge")
    written, record = [], {}
    keep = {out.resolve() for out, _ in outs.values()}
    for f, rel in sorted(mine.items(), key=lambda kv: kv[1]):
        if f not in keep:
            f.unlink()
            for d in f.parents:
                if d in (mod_root, mod_root / "files"):
                    break
                try:
                    d.rmdir()
                except OSError:
                    break
    for out, text in outs.values():
        arcfolder.write_file(out, text)
        written.append(out)
        record[out.relative_to(mod_root).as_posix()] = _sha(text)
    for rel in (old or {}).get("files", {}):
        f = mod_root / rel
        if f.is_file() and f.resolve() not in keep and f.resolve() not in mine:
            p.notes.append(f"  kept {rel}: changed since an earlier run wrote it")
    arcfolder.write_file(mod_root / RECORD, (json.dumps(
        {"schema": SCHEMA, "factor": p.factor, "options": p.options, "copies": p.copies,
         "files": dict(sorted(record.items()))}, indent=1) + "\n").encode("utf-8"))
    return written

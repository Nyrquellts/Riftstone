"""Encounters: put N of an enemy somewhere -- "100 goblins at the Encampment" -- as a new enemy group
built only from mechanisms the game itself uses.

How a stage spawns enemies (measured on build 2364871, docs/world-map.md):

* ``scr\\st<S>\\etc\\st<S>_e.gpl`` lists the stage's enemy groups.  A group's unit-kind list is
  taken to make the engine load an enemy's archive (cLayoutSetEnemy::addArcLoadTbl loads per unit from
  the layout set's cLayoutSetCharaBase::cUnitData; the link to the group's list is assumed, not traced);
  its areas (hit / life / kill shapes) are where it acts; its scenario window, story flags and hours say
  when it exists.
* The group's placements are ``st<S>_<X>m<Z>n_e<N>.lot``: the name the engine builds from the group's
  number and a map cell (0x01562268).
* **Hordes are a group setting.**  A group with a spawn cap (``mSetCountMax``) and respawn type 5
  produces that many units in total, reusing its placements as spawn points: stage 330's group 35 sends
  100 goblins and hobgoblins from 7 points, stage 706's group 3 sends 50 goblins from 6.  Every capped
  enemy group in the game but one uses type 5.  So "100 goblins" is one group: 100 total, from as many
  spawn points as should be on screen at once.
* The game keeps at most 10 enemies active at once (sSetManager's enemy slots, docs/re-enemy-cap.md);
  spawn points default to 10 for that reason.  The enemy_cap plugin raises the limit (default 30).

An encounter therefore:
  1. takes the group whose enemies stand nearest the spot (its areas, story window and load flags are
     the ones that work there) and copies it under a free group number (0..294, the engine's table);
  2. gives the copy your enemy as its only unit kind and -- for more enemies than spawn points -- the
     game's horde settings (respawn type 5, spawn cap = the total), and the story window asked for: both
     are NYR-Lang rules (rules/horde.nyr, compiled into rules/horde.py);
  3. writes the copy's layout in that group's map cell: spawn points around the spot, each a copy of a
     vanilla placement of the same enemy (its class, equipment, AI script and settings);
  4. writes both into a mod: the group list replaces the stage's in every archive that holds it, and the
     layout is added to every archive that holds the stage's layouts for that cell.

Where the spawn points stand: in a stage with a navigation mesh (every stage but the open field, 501 and
703; nav.py), an enemy the game itself places on the mesh (bestiary.py: its walkers) gets every spawn point
on the mesh -- the first on the ground under the spot (or the nearest ground within 10 m), the others on
rings around it, each reached on foot from the first (by the mesh, at most twice the straight distance plus
5 m: not behind a wall or across a drop), with room to stand and ``spread`` * 0.6 apart.  Flyers, and every
enemy in stages without a mesh, keep flat rings at the spot's height, as before.

Nothing here touches the game; ``riftstone install`` does, as for any mod.  In-game behaviour stays
UNKNOWN until someone plays it: the files are valid and use only the game's own mechanisms.
"""
from __future__ import annotations

import copy as _copy
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import arcfolder, fsmap, gpl, lot, modfiles, skins, typemap
from .errors import RiftError
from .rules import horde as horde_rules
from .world import GROUP_SLOTS, World

GROUP_BIT = 0x80000000        # mGroupList[n] for a group that exists (3,925 of 3,925 in the game)
HORDE_RESPAWN = int(horde_rules.CONSTANTS["HordeRespawn"])   # 5: every capped enemy group's but one
# rules/horde.nyr's outputs -> the group list fields they set
HORDE_FIELDS = {"set_count_max": "mSetCountMax", "rspn_type": "mRspnCondition.mRspnType",
                "rspn_day": "mRspnCondition.mRspnDay", "rspn_prob": "mRspnCondition.mRspnProb",
                "rspn_prob_add": "mRspnCondition.mRspnProbAdd",
                "rspn_force_rspn": "mRspnCondition.mRspnForceRspn", "appear_bgn": "mAppearBgn",
                "appear_end": "mAppearEnd"}
STORY_CODES = {"any": 1, "pre": 2, "post": 3}          # rules/horde.nyr's story input (0: keep the copy's)
POOL = 10                     # active-enemy slots (sSetManager's pool)
MAX_POINTS = 31               # the most placements any vanilla enemy layout holds
FAR = 6000.0                  # a template group farther than this from the spot is reported
GROUND_SNAP = 1000.0          # cm: how far from the spot the nearest walkable ground may be
GROUND_RISE = 400.0           # cm: how far above or below the spot the ground may lie
GROUND_RINGS = 100            # rings of candidate spawn points searched at most (6 x ring each): with any spread of
                              # 40 cm or more they reach the 40 m walk limit; a smaller spread no longer searches
                              # 3 x (4000 / spread)^2 candidates (0.01 cm: hours) in a spot too tight for them


@dataclass
class Encounter:
    stage: int
    enemy: str
    enemy_name: str
    total: int
    points: int
    at: tuple
    group: int
    template_group: int
    template_layout: str
    template_distance: float
    source_record: tuple            # (layout, index) of the vanilla placement copied
    cell: tuple
    gpl_name: str
    layout_name: str
    layout_archives: list
    gpl_data: bytes = b""
    layout_data: bytes = b""
    horde: bool = False
    skin: int | None = None
    notes: list = field(default_factory=list)
    hours: tuple | None = None                  # (first, last) whole hours set on the group; None: the template's
    ground: str = ""                            # how the spawn points were put on the walkable ground ("" flat rings)
    positions: list = field(default_factory=list)   # every spawn point, in the layout's order


def parse_stage(text) -> int:
    m = re.fullmatch(r"(?:st|stage)?\s*(\d{1,3})", str(text).strip().lower())
    if not m:
        raise RiftError(f"{text!r} is not a stage (e.g. 424 or st424; riftstone world stages lists them)")
    return int(m.group(1))


def parse_at(text: str, w: World, stage: int) -> tuple:
    """'x,y,z' or 'group:N' (the middle of enemy group N's placements in this stage)."""
    t = text.strip().lower()
    if t.startswith("group:"):
        try:
            n = int(t[6:])
        except ValueError:
            raise RiftError("--at group:N takes a group number") from None
        pts = [p[5] for name in w.layouts_of(stage, "e", n) for p in w.placements_in(name) if p[5]]
        if not pts:
            raise RiftError(f"stage {stage} has no placements for enemy group {n}")
        return tuple(sum(c) / len(pts) for c in zip(*pts))
    try:
        xyz = tuple(float(v) for v in t.split(","))
    except ValueError:
        xyz = ()
    if len(xyz) != 3 or not all(math.isfinite(v) and abs(v) < 1e6 for v in xyz):
        raise RiftError("--at is x,y,z (e.g. --at 1200,-1340,-3700) or group:N")
    return xyz


def _unit_belong(w: World, em: str) -> int:
    """isBelong for this enemy as the game's groups most often list it (1 when never listed)."""
    votes = {0: 0, 1: 0}
    for g in w.groups:
        for name, belong in zip(g["units"], g.get("belong", [])):
            if name == em and belong in votes:
                votes[belong] += 1
    return 1 if votes[1] >= votes[0] else 0


_PLACE_FIELDS = ("mPosition", "mAngle")       # where a copy stands; every other field is the enemy's setup


def _setup_key(rec) -> tuple:
    return tuple(sorted((k, v) for k, v in rec.fields.items() if k not in _PLACE_FIELDS))


NO_LIFE_GROUP = 0xFFFFFFFF    # mLifePointGroup of a placement whose health is its own
# What makes a vanilla placement ordinary, most important first; each narrows the choice only while some
# placements pass it.  Its own AI script (mFsmFilePath: one quest's or cut-scene's state machine), a life
# point group (health shared with, or kept for, other placements of that group) and a boss flag all tie a
# placement to one piece of the game; a copy should act the way the enemy does in the open world.
_ORDINARY = (lambda r: not r.fields.get("mFsmFilePath"),
             lambda r: r.fields.get("mLifePointGroup", NO_LIFE_GROUP) == NO_LIFE_GROUP,
             lambda r: not r.fields.get("mBossFlag"))


def _source_records(game, idx, w: World, em: str, stage: int, limit: int = 6) -> list[tuple]:
    """The vanilla setups a copy may take, most common first: [(placement row, record, how many use it)].

    From this stage's placements of the enemy if it has any, else the whole game's, narrowed to the
    ordinary ones (``_ORDINARY``): stage 100's first goblin placement runs quest 7's ox-hunting script (4 of
    its 355 goblins do), and one of its chimeras belongs to life point group 48.  Up to ``limit`` distinct
    setups -- a goblin's weapons are its ``mEquipType`` -- so a crowd is as mixed as the game's own."""
    rows = [p for p in w.placements if p[4] == em and lot.KINDS[p[3]][0].startswith(("cSetInfoEnemy", "cSetInfoNpc"))]
    if not rows:
        raise RiftError(f"{em} is never placed by any layout in the game, so there is no placement to copy")
    here = [p for p in rows if w.layouts[p[0]]["stage"] == stage] or rows
    cache: dict[str, list] = {}
    found = []
    for p in here:
        if p[0] not in cache:
            data, _ = modfiles.load(game, idx, None, p[0].encode("latin-1"), typemap.BY_EXT["lot"])
            cache[p[0]] = lot.parse(data).records
        found.append((p, cache[p[0]][p[1]]))
    pool = found
    for ordinary in _ORDINARY:
        pool = [(p, r) for p, r in pool if ordinary(r)] or pool
    groups: dict[tuple, list] = {}
    for p, r in pool:
        groups.setdefault(_setup_key(r), []).append((p, r))
    ranked = sorted(groups.values(), key=lambda g: (-len(g), g[0][0][0], g[0][0][1]))
    return [(g[0][0], g[0][1], len(g)) for g in ranked[:limit]]


def _mix(weights: list[int], n: int) -> list[int]:
    """n picks spread over the setups in proportion to ``weights`` (largest remainder), interleaved so
    neighbouring spawn points differ: [0, 1, 0, 2, 0, 1, ...]."""
    from fractions import Fraction

    total = sum(weights)
    quota = [Fraction(w * n, total) for w in weights]           # exact, so ties break by order, not rounding
    counts = [int(q) for q in quota]
    for i in sorted(range(len(weights)), key=lambda i: (counts[i] - quota[i], i))[: n - sum(counts)]:
        counts[i] += 1
    slots = sorted((Fraction(2 * j + 1, 2 * c), i) for i, c in enumerate(counts) for j in range(c))
    return [i for _, i in slots]


def parse_skins(value) -> list[int] | None:
    """--skin: one number (every placement wears it) or several, "1,2" (placements take them in turn)."""
    if value is None or value == "" or value == []:
        return None
    if isinstance(value, bool):
        raise RiftError("--skin is a skin number or numbers, e.g. 1 or 1,2")
    if isinstance(value, int):
        return [value]
    items = list(value) if isinstance(value, (list, tuple)) else str(value).replace(" ", "").split(",")
    out = []
    for v in items:
        if isinstance(v, bool) or isinstance(v, float):
            raise RiftError("--skin is a skin number or numbers, e.g. 1 or 1,2")
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            raise RiftError(f"--skin: {v!r} is not a skin number (e.g. 1 or 1,2)") from None
    if not out or len(out) > MAX_POINTS:
        raise RiftError(f"--skin takes 1 to {MAX_POINTS} numbers")
    return out


def _spawn_points(at: tuple, n: int, spread: float) -> list[tuple]:
    """n points: the first at the spot, the rest on rings around it, ``spread`` apart."""
    pts = [at]
    ring = 1
    while len(pts) < n:
        r = spread * ring
        k = max(6 * ring, 1)
        for i in range(k):
            if len(pts) >= n:
                break
            a = 2 * math.pi * i / k
            pts.append((at[0] + r * math.sin(a), at[1], at[2] + r * math.cos(a)))
        ring += 1
    return pts


def _ground_points(mesh, at: tuple, n: int, spread: float, room: float):
    """Up to n spawn points on the navigation mesh around ``at`` (see the module's text): (points, the start's
    Spot, the farthest walk in metres), or None when no walkable ground lies within GROUND_SNAP of ``at``."""
    start = mesh.locate(at) or mesh.nearest(at, GROUND_SNAP, above=GROUND_RISE, below=GROUND_RISE)
    if start is None:
        return None
    limit = max(40.0, spread * 12 / 100.0)
    reach = mesh.distances([start.triangle], limit=limit)
    within = set(reach)
    pts, far, ring = [start.point], 0.0, 1
    while len(pts) < n and ring <= GROUND_RINGS and spread * ring <= limit * 100.0:
        r, k = spread * ring, 6 * ring
        for i in range(k):
            if len(pts) >= n:
                break
            a = 2 * math.pi * i / k
            c = (start.point[0] + r * math.sin(a), start.point[1], start.point[2] + r * math.cos(a))
            spot = mesh.nearest(c, spread * 0.5, above=GROUND_RISE, below=GROUND_RISE, within=within)
            if spot is None:
                continue
            walk = reach.get(spot.triangle, math.inf)
            if walk > 2.0 * math.dist(spot.point, start.point) / 100.0 + 5.0:
                continue                                  # behind a wall or across a drop
            if any(math.dist(spot.point, q) < spread * 0.6 for q in pts) or mesh.clearance(spot.point) < room:
                continue
            pts.append(spot.point)
            far = max(far, walk)
        ring += 1
    return pts, start, far


def _placement(game, idx, w: World, mod_root, s: int, em: str, at: tuple, points: int, spread: float, ground):
    """(spawn points on the stage's walkable ground or None for the flat rings, a note saying how, warnings)."""
    if ground is False:
        return None, "", []
    from . import bestiary, nav

    mesh = nav.stage_mesh(game, idx, s, mod_root)
    if mesh is None:
        if ground:
            raise RiftError(f"stage {s} has no navigation mesh (the open field and stages 501 and 703 have none), so "
                            "its spawn points cannot be put on walkable ground")
        return None, "", []
    b = bestiary.load(game, idx, w)
    walks = b.walks(em)
    e = b.get(em) or {"on_mesh": 0, "measured": 0}
    if walks is False and not ground:
        return None, "", [f"{em} keeps off the ground where the game places it ({e['on_mesh']} of {e['measured']} "
                          "placements on a navigation mesh), so its spawn points stay at the spot's height"]
    room = b.room(em) if walks else bestiary.ROOM_FLOOR
    got = _ground_points(mesh, at, points, spread, room)
    where = "[" + ", ".join(f"{v:.0f}" for v in at) + "]"
    if got is None:
        raise RiftError(f"no walkable ground of stage {s} lies within {GROUND_SNAP / 100:.0f} m of {where} (its "
                        "navigation mesh); pick a spot where the stage's enemies stand (--at group:N), or keep flat "
                        "rings at that height with --no-ground")
    pts, start, far = got
    ns = nav.nav_stage(s)
    note = (f"{len(pts)} spawn point{'s' if len(pts) != 1 else ''} on stage {ns}'s navigation mesh"
            + (f" (stage {s} loads stage {ns}'s)" if ns != s else "")
            + (f", each reached on foot from the first (the farthest {far:.0f} m by the mesh)" if len(pts) > 1 else ""))
    warn = []
    moved = math.dist(at, start.point)
    if moved > 50.0:
        warn.append(f"the spot {where} is {moved / 100:.1f} m from walkable ground; the group stands at "
                    + "[" + ", ".join(f"{v:.0f}" for v in start.point) + "] instead")
    if len(pts) < points:
        warn.append(f"only {len(pts)} of {points} spawn points fit on walkable ground reached from the spot (each "
                    f"with {room / 100:.1f} m of room and {spread * 0.6 / 100:.1f} m apart); the group has {len(pts)}")
    here = mesh.clearance(start.point)
    if walks and here < room:
        warn.append(f"the game gives {em} at least {room / 100:.1f} m of open ground; this spot has {here / 100:.1f} m")
    if walks is None:
        warn.append(f"{em} has too few placements in stages with a navigation mesh to say whether it walks; its spawn "
                    "points were put on the ground")
    return pts, note, warn


def group_list_names(w: World, stage: int) -> list[str]:
    """The stage's enemy group lists: its own first (an encounter's group goes into it; write() rewrites it), then
    the others sharing its group numbers (st443_e_dlc01).  They are what plan() reads from the mod, so what an
    encounter plan's dry run copies into its scratch mod (encounter_plan.apply)."""
    own = f"scr\\st{stage:03d}\\etc\\st{stage:03d}_e"
    return [own] + sorted({g["list"] for g in w.groups_of(stage, "e")} - {own})


def parse_hours(text) -> tuple:
    """'20,3' -> (20, 3): the first and the last whole hour the group exists (it may wrap past midnight)."""
    parts = str(text).replace(" ", "").split(",")
    # ASCII digits: str.isdigit() also takes '²', which int() refuses
    if len(parts) != 2 or not all(re.fullmatch(r"[0-9]{1,2}", p) and int(p) <= 23 for p in parts):
        raise RiftError("--hours is the first and the last whole hour, 0..23, e.g. 4,19 or 20,3 (past midnight)")
    return int(parts[0]), int(parts[1])


def plan(game, idx, w: World, mod_root: Path, stage, enemy: str, total: int, at: str, points: int | None = None,
         spread: float = 250.0, group: int | None = None, story: str | None = None, like: int | None = None,
         skin=None, hours: tuple | None = None, ground: bool | None = None, always: bool = False) -> Encounter:
    """Work out an encounter (nothing is written): the group, its layout, and where both go.

    ``like`` names the group to copy (its areas, story window and load conditions -- e.g. a lot flag, so
    the new group appears exactly when that one does); by default the group standing nearest the spot.
    ``skin`` makes the placements wear enemy skins (skins.py; needs the enemy_skins plugin): one number for
    all of them, or a list the placements take in turn ([1, 2]: a White and a Shadow Chimera).
    ``hours`` = (first, last) sets the group's mDataSetHour window, both ends whole hours 0..23 as the game's
    own groups hold them (3,673 of 3,925 hold 0..23; day and night pairs such as 4..20 / 20..4); by default
    the copied group's hours stay.
    ``ground``: None puts a walker's spawn points on the stage's navigation mesh when it has one (the module's
    text); True insists on it (an error when the stage has no mesh or the spot no walkable ground near it);
    False keeps the flat rings at the spot's height.
    ``always`` clears the copied group's lot-flag load condition (``mLoadCondition.mLotFlag``/``mLotFlag2``), so
    the new group loads whenever the stage does, as the game's groups without one do (28 of stage 300's 36, for
    one); by default it keeps it and loads only while that lot flag is set (all 397 enemy groups of stages
    420-447 have one; what sets each flag is UNKNOWN here)."""
    GPL, LOT = typemap.BY_EXT["gpl"], typemap.BY_EXT["lot"]
    s = parse_stage(stage)
    w.stage(s)
    em = w.find_enemy(enemy)
    if not isinstance(total, int) or not 1 <= total <= 9999:
        raise RiftError("--count is how many enemies in total, 1 to 9999")
    xyz = parse_at(at, w, s)
    if points is None:
        points = min(total, POOL)
    if not 1 <= points <= min(total, MAX_POINTS):
        raise RiftError(f"--at-once is 1 to {min(total, MAX_POINTS)} (no more than --count; the game's enemy layouts "
                        f"hold at most {MAX_POINTS} placements)")
    if not (math.isfinite(spread) and 0 < spread <= 5000):
        raise RiftError("--spread is the distance between spawn points, more than 0 and at most 5000")
    if hours is not None:
        if (not isinstance(hours, (tuple, list)) or len(hours) != 2
                or not all(isinstance(h, int) and not isinstance(h, bool) and 0 <= h <= 23 for h in hours)):
            raise RiftError("--hours is the first and the last whole hour, 0..23, e.g. 4,19 or 20,3 (past midnight)")
        hours = (hours[0], hours[1])

    fam = None
    wear = parse_skins(skin)
    if wear is not None:
        fam = skins.family_of_enemy(em)
        if fam is None:
            raise RiftError(f"{em} has no skin family yet; skins exist for: "
                            + ", ".join(f"{f.enemy} ({f.key})" for f in skins.FAMILIES.values()))
        for n in wear:
            skins.check_number(n)
        skin = wear[0] if len(wear) == 1 else wear

    # where the spawn points stand (before the group: fewer may fit on the ground than were asked for)
    placed, ground_note, ground_warn = _placement(game, idx, w, mod_root, s, em, xyz, points, spread, ground)
    if placed is None:
        pts = _spawn_points(xyz, points, spread)
    else:
        pts = placed
        xyz = pts[0]
        points = len(pts)

    if like is None:
        near = w.nearest(s, xyz, "e")
        if near is None:
            raise RiftError(f"stage {s} has no enemy placements to take a group's areas and conditions from")
        dist, row = near
    else:
        if not isinstance(like, int) or not 0 <= like < GROUP_SLOTS:
            raise RiftError(f"--like is an enemy group number 0..{GROUP_SLOTS - 1}")
        rows = [p for name in w.layouts_of(s, "e", like) for p in w.placements_in(name) if p[5]]
        if not rows:
            raise RiftError(f"stage {s} has no placements for enemy group {like} to copy (riftstone world stage {s})")
        dist, row = min((math.dist(xyz, p[5]), p) for p in rows)
    tlay = w.layouts[row[0]]
    tnum, cell = tlay["number"], (tlay["x"], tlay["z"])

    gpl_name, *other_lists = group_list_names(w, s)
    gname = gpl_name.encode("latin-1")
    # "no group list" only when neither the mod nor the game has one; a list the mod holds but that cannot be
    # read (both forms kept, YAML that does not parse) says what is wrong with it, in its own words
    in_mod = mod_root is not None and any(p.is_file() for p in modfiles.paths(mod_root, gname, GPL))
    if not in_mod and not idx.archives_with(gname, GPL):
        raise RiftError(f"stage {s} has no enemy group list ({gpl_name}.gpl)")
    try:
        gdata, _gout = modfiles.load(game, idx, mod_root, gname, GPL)
    except RiftError as e:
        raise RiftError(f"stage {s}'s enemy group list: {e}") from None
    doc = gpl.parse(gdata)
    by = {g["mGroup"]: g for g in doc.groups}
    # A stage can have more than one enemy group list (st443 has st443_e and the DLC's st443_e_dlc01);
    # their groups share one number space, and a group's layout name is built from its number alone.  The
    # group to copy may be in any of them (Everfall's groups 20-22 are the DLC list's); the copy goes into
    # the stage's own list.
    elsewhere = {}
    for name in other_lists:
        try:
            data, _ = modfiles.load(game, idx, mod_root, name.encode("latin-1"), GPL)
        except RiftError as e:
            raise RiftError(f"stage {s}'s enemy group list {name}.gpl: {e}") from None
        for g in gpl.parse(data).groups:
            elsewhere.setdefault(g["mGroup"], (name, g))
    tmpl_list, tmpl = (gpl_name, by[tnum]) if tnum in by else elsewhere.get(tnum, (None, None))
    if tmpl is None:
        raise RiftError(f"group {tnum} of stage {s} is not in its group lists")
    used = set(by) | set(elsewhere) | {g["number"] for g in w.groups_of(s, "e")}
    if group is None:
        free = [n for n in range(GROUP_SLOTS) if n not in used]
        if not free:
            raise RiftError(f"stage {s} uses all {GROUP_SLOTS} enemy group slots; add the enemies to an existing "
                            "group's layout instead (riftstone spawns copy)")
        group = free[0]
    elif not 0 <= group < GROUP_SLOTS or group in used:
        raise RiftError(f"--group must be a free number 0..{GROUP_SLOTS - 1} (group {group} "
                        + ("exists" if group in used else "is out of range") + ")")
    if len(doc.mGroupList) != GROUP_SLOTS:
        raise RiftError(f"{gpl_name}.gpl lists {len(doc.mGroupList)} group slots, not {GROUP_SLOTS}; refusing to guess")

    # 1-2: the new group
    new = _copy.deepcopy(tmpl)
    new["mGroup"] = group
    new["mUnitKindList"] = [{"name": em, "isBelong": _unit_belong(w, em)}]
    lays = []
    for la in tmpl["mLayoutIDArray"]:          # mLayoutID is the stage id in every vanilla entry: kept
        la = dict(la)
        la["mGroup"] = group
        lays.append(la)
    # A layout's name carries its cell as <mSplitZ>m<mSplitX>n: all 504 of the game's enemy layouts whose two
    # numbers differ read that way against their group's mLayoutIDArray (none the other way), so the entry
    # for the layout's cell is (mSplitX, mSplitZ) = (the name's second number, its first).
    split = (cell[1], cell[0])
    if not any((la["mSplitX"], la["mSplitZ"]) == split for la in lays):
        lays.append({"mLayoutID": s, "mGroup": group, "mSplitX": split[0], "mSplitZ": split[1]})
    new["mLayoutIDArray"] = lays
    if story and story not in STORY_CODES:
        raise RiftError("--story is any, pre (before the Dragon) or post (after it)")
    ruled = horde_rules.evaluate({"total": total, "points": points, "story": STORY_CODES.get(story, 0)})
    for u in ruled.updates:                       # the horde setting and the story window
        new[HORDE_FIELDS[u.name]] = int(u.value)
    horde = any(s.id == horde_rules.CONSTANTS["Horde"] for s in ruled.signals)
    new["mSetCondition.mIsEmGroupLink"] = 0
    new["mSetCondition.mLinkEmGroup"] = 0
    lot_flag = None
    if tmpl.get("mLoadCondition.mLotFlag") or tmpl.get("mLoadCondition.mLotFlag2"):
        lot_flag = tmpl.get("mDataLotFlag.mFlagNo", 0)
        if always:
            for k in ("mLoadCondition.mLotFlag", "mLoadCondition.mLotFlag2"):
                if k in new:
                    new[k] = 0
    if hours is not None:
        new["mDataSetHour.mSetHourBgn"], new["mDataSetHour.mSetHourEnd"] = hours
    doc.groups.append(new)
    doc.mGroupList[group] = GROUP_BIT
    gpl_bytes = gpl.build(doc)
    gpl.parse(gpl_bytes)                          # it must read back

    # 3: the layout: each spawn point a copy of one of the enemy's ordinary vanilla setups, mixed as the game mixes them
    setups = _source_records(game, idx, w, em, s)
    src = setups[0][0]
    order = _mix([n for _, _, n in setups], points)
    recs = []
    for i, p in enumerate(pts):
        r = setups[order[i]][1].copy()
        r.id = i
        r.set_vec("mPosition", p)
        if fam is not None:
            skins.mark(r, wear[i % len(wear)], fam)
        recs.append(r)
    layout = lot.Lot(recs)
    lot.check_ids(layout)
    layout_bytes = lot.build(layout)
    layout_name = lot.layout_name(s, cell[0], cell[1], "e", group)
    if lot.parse_name(layout_name) is None:
        raise RiftError(f"{layout_name} would not be read back as a layout name")
    if layout_name in w.layouts:
        raise RiftError(f"{layout_name} already exists in {', '.join(w.layouts[layout_name]['archives'])}; "
                        f"pick another --group")
    arcs = sorted({a for name, lay in w.layouts.items() if (lay["stage"], lay["x"], lay["z"]) == (s, *cell)
                   for a in lay["archives"]})
    if not arcs:
        raise RiftError(f"no archive holds stage {s}'s layouts for cell {cell[0]:02d}m{cell[1]:02d}n")

    enc = Encounter(s, em, w.enemies.get(em, {}).get("name", ""), total, points, xyz, group, tnum, row[0], dist,
                    (src[0], src[1]), cell, gpl_name, layout_name, arcs, gpl_bytes, layout_bytes, horde, skin,
                    hours=hours, ground=ground_note, positions=[tuple(r.vec()) for r in recs])
    if ground_note:
        enc.notes.append(ground_note)
    enc.notes.extend(ground_warn)
    if hours is not None:
        enc.notes.append(f"hours {hours[0]}..{hours[1]}{' (past midnight)' if hours[0] > hours[1] else ''}: both ends "
                         "as the game's own groups hold them; whether the game counts the last hour is UNKNOWN until "
                         "seen in game.")
    if like is not None:
        enc.notes.append(f"copies group {like}'s conditions (load flags, story window, areas): it appears when "
                         f"group {like} does." if lot_flag is None or not always else
                         f"copies group {like}'s story window and areas.")
    if lot_flag is not None:
        enc.notes.append(f"group {tnum} loads only while lot flag {lot_flag} is set; the new group "
                         + ("loads whenever the stage does (the condition is cleared, as groups without one have it)"
                            if always else "does too (--always clears that condition)"))
    if tmpl_list != gpl_name:
        enc.notes.append(f"group {tnum} is in {tmpl_list}.gpl; its copy, conditions and all, goes into {gpl_name}.gpl. "
                         "In game UNKNOWN until played.")
    if len(setups) > 1:
        enc.notes.append(f"the placements copy {len(setups)} of {em}'s ordinary vanilla setups (e.g. its weapons), "
                         f"in the proportions the game uses them")
    if wear is not None:
        which = f"skin {wear[0]} (model\\em\\...\\s{wear[0]:02d})" if len(wear) == 1 else \
            "skins " + ", ".join(str(n) for n in wear) + " in turn"
        enc.notes.append(f"the placements wear {fam.key} {which}; that needs the enemy_skins plugin and the skins' "
                         "files in an enabled mod (riftstone skin list).")
    if horde:
        enc.notes.append(f"{total} in total from {points} spawn points: the game's own horde setting (respawn type "
                         f"{HORDE_RESPAWN} with a spawn cap), as stage 330's 100-goblin group 35 uses.")
    if points > POOL:
        enc.notes.append(f"{points} spawn points, but only {POOL} enemies are active at once unless the enemy_cap "
                         "plugin raises the limit (riftstone loader plugin add native\\plugins\\enemy_cap\\out\\"
                         "enemy_cap.asi; docs/re-enemy-cap.md).")
    if dist > FAR:
        enc.notes.append(f"the nearest enemy group ({tnum}) stands {dist:.0f} units from the spot; its areas may not "
                         "cover it, so the new group could idle or leave. Pick a spot near existing enemies.")
    if not any(w.layouts[p[0]]["stage"] == s for p in w.placements if p[4] == em):
        enc.notes.append(f"{em} is not placed in stage {s} in the game. Its model is loaded from the group's unit list "
                         "(the same path the game's groups use); in game this is UNKNOWN until played.")
    return enc


def write(enc: Encounter, mod_root: Path) -> list[Path]:
    """Put an encounter into a mod: the group list (every archive that has it) and the new layout (every
    archive holding the stage's layouts for that cell).  Returns the files written."""
    GPL, LOT = typemap.BY_EXT["gpl"], typemap.BY_EXT["lot"]
    name = enc.gpl_name.encode("latin-1")
    as_yaml, as_bin = modfiles.paths(mod_root, name, GPL)
    gout = as_bin if as_bin.is_file() and not as_yaml.is_file() else as_yaml   # keep the form the mod uses
    modfiles.save(gout, enc.gpl_data, name, GPL)
    written = [gout]
    text = lot.to_yaml(lot.parse(enc.layout_data), enc.layout_name).encode("utf-8")
    rel = fsmap.encode_name(enc.layout_name.encode("latin-1"), LOT) + ".yaml"
    for a in enc.layout_archives:
        out = mod_root / "archives" / (a + ".arc") / rel
        arcfolder.write_file(out, text)
        written.append(out)
    return written

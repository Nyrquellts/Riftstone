"""Level synthesis for Dark Arisen: a dungeon's whole population, from a mission, laid on the stage's own
walkable ground and checked before anything is written.

The steps, each a claim this module can check:

1. **Space** (``space``): the stage's navigation mesh (nav.py; eleven stages load another
   stage's), its doors (the stage's start positions, ``.stp``, each on the mesh), the region they open into
   (the triangles joined to them by the mesh's own links) and how deep every triangle lies (metres by the
   mesh from the nearest door).  The main path runs from a door to the deepest place with room for a boss;
   sites are laid along it every ``spacing`` metres, then spread over the rest of the region by farthest-point
   sampling (each new site the triangle farthest, by the mesh, from every site and door so far) until no
   place is ``spacing`` from one.  A site lies within 60 m (``ANCHOR``) of an enemy the stage already places,
   since a new group copies the nearest group's areas, and is reachable on foot from a door by construction:
   it lies in the doors' region.  Each site knows its depth, its room (distance to the mesh's edge), how far off the main
   path it lies (main / beside / side) and, where the stage names its places (``.spn``), its room's name.
2. **Mission** (mission.py): a sequence of beats -- Fight, Ambush, Horde, Guardian, Boss, and side beats --
   from a grammar in the manner of Dormans' mission grammars.
3. **Places** (a constraint problem, wfc.py): each beat gets a site of the right kind (main beats on the main
   path, an ambush beside it, side beats off it, the boss deepest), no two the same or closer than half the
   ``spacing``, each main beat at least 30% of the spacing deeper than the one before, side beats within a
   spacing of the depths of the main beats around them, every site with the room its beat needs
   (rules/beats.nyr).
4. **Enemies** (a second constraint problem): each beat an enemy of the tiers its depth calls for
   (rules/beats.nyr, rules/tiers.nyr) from the pool (by default the enemies the game itself places in this
   stage and the stages sharing its mesh), one the game places on the mesh (bestiary.py: its walkers) and
   gives no more room than the site has; neighbouring beats differ, and with the whole game as the pool they
   must be companions (the game places both in some stage).
5. **Plan** (``plan``): a ``riftstone-encounters/1`` plan, one encounter per beat at its site -- the same
   file ``riftstone encounters`` writes; encounter.py puts each encounter's spawn points on the mesh.
6. **Check** (``check``): every spawn point of every planned encounter re-read from its layout bytes and
   found on the mesh, in the doors' region, with its walking depth.

What it cannot do, measured rather than assumed: new rooms or corridors (a stage's geometry is its models and
collision; Riftstone moves them but does not build new ones), a new stage (it would need a native plugin to
register), locks and keys (DDDA has no data-side door locks this could set).  The open field (stage 100) has
no navigation mesh: its AI uses waypoint cells (``.way``), not decoded here.  How the result plays is UNKNOWN
until played: the files are valid, the placements stand where the game's own walkers stand.
"""
from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass, field

from . import bestiary as _bestiary
from . import encounter, mission, nav, wfc
from .errors import RiftError
from .rules import beats as beat_rules

FORMAT = "riftstone-encounters/1"
SPACING = 15.0          # metres between sites
DOOR_CLEAR = 12.0       # metres (by the mesh) no site comes closer to a door than
MAX_SITES = 96
MAIN_OFF = 8.0          # metres from the main path: a main site; up to SIDE_OFF: beside it; beyond: a side site
SIDE_OFF = 25.0
ANCHOR = 6000.0         # cm: a site farther than this from every enemy the stage already places is left out
SCAN = 400              # the deepest places searched for a boss's room (x4), then for the roomiest
MISSION_TRIES = 12      # missions tried from one seed before the stage is said not to hold any
POOL_MAX_POINTS = 10    # spawn points per encounter (sSetManager's 10 active slots without enemy_cap)


@dataclass
class Site:
    id: int
    triangle: int
    point: tuple
    depth: float            # metres from the nearest door, by the mesh
    room: float             # centimetres to the mesh's edge
    off: float = 0.0        # metres from the main path, by the mesh
    role: str = "main"      # main / beside / side
    place: str = ""         # the room's name, where the stage names its places


@dataclass
class Space:
    stage: int
    nav_stage: int
    mesh: nav.Mesh
    doors: list             # nav.Spot
    region: int
    depth: dict             # triangle -> metres
    path: list              # triangles from a door to the deepest roomy place
    sites: list             # Site
    deepest: float          # metres to the end of the main path
    notes: list = field(default_factory=list)
    groups: dict = field(default_factory=dict)   # the stage's enemy groups: number -> (lot flag, positions)


@dataclass
class Placed:
    beat: mission.Beat
    site: Site
    enemy: str
    tier: int
    total: int
    points: int
    spread: float
    like: int | None = None     # the vanilla group the encounter copies (its areas and conditions)
    name: str = ""
    lot_flag: int | None = None  # that group's lot flag, when it loads only under one
    always: bool = False         # the copy's lot-flag condition cleared (it loads whenever the stage does)


@dataclass
class Dungeon:
    stage: int
    seed: int
    beats: list
    space: Space
    placed: list            # Placed, in mission order
    pool: list
    notes: list = field(default_factory=list)


# -- 1. space --------------------------------------------------------------------------------------------
def _float(v) -> float:
    return struct.unpack("<f", struct.pack("<I", v))[0] if isinstance(v, int) else float(v)


def _places(game, idx, stage: int) -> list:
    """The stage's named places: [(name, x, y, z, radius)], from its ``.spn`` and the game's place list."""
    from . import flat, typemap, world

    data = nav.game_resource(game, idx, f"scr\\st{stage:03d}\\etc\\st{stage:03d}".encode("latin-1"),
                             typemap.BY_EXT["spn"])
    if data is None:
        return []
    names = world.place_names(game, idx)
    out = []
    for r in flat.parse(data, "spn").data["mpPlace"]:
        pid = r["mPlaceNameId"]
        if 0 <= pid < len(names) and names[pid]:
            out.append((names[pid], _float(r["mPosX"]), _float(r["mPosY"]), _float(r["mPosZ"]), _float(r["mRadius"])))
    return out


def _place_of(places, p) -> str:
    best = None
    for name, x, y, z, r in places:
        d = math.hypot(p[0] - x, p[2] - z)
        if d <= max(r, 3000.0) and (best is None or d - r < best[0]):
            best = (d - r, name)
    return best[1] if best else ""


def _spacing(spacing) -> float:
    """5..100 metres, checked without turning it into a float first (float(10 ** 400) overflows; nan fails both
    comparisons)."""
    if isinstance(spacing, bool) or not isinstance(spacing, (int, float)) or not 5.0 <= spacing <= 100.0:
        raise RiftError("spacing is 5..100 metres between places")
    return float(spacing)


def space(game, idx, w, stage: int, spacing: float = SPACING, door_clear: float = DOOR_CLEAR,
          max_sites: int = MAX_SITES, mod_root=None) -> Space:
    """The stage's walkable ground as the director uses it (see the module's text): the navigation mesh the game
    loads with the mod (its own copy of the mesh when it has one, as encounter.plan and the check read it)."""
    spacing = _spacing(spacing)
    mesh = nav.stage_mesh(game, idx, stage, mod_root)
    if mesh is None:
        raise RiftError(f"stage {stage} has no navigation mesh, so there is no walkable ground to lay a dungeon on "
                        "(every stage has one but the open field, 100, and stages 501 and 703)")
    doors = []
    for p in nav.start_positions(game, idx, stage):
        s = mesh.locate(p) or mesh.nearest(p, 600.0)
        if s is not None:
            doors.append(s)
    notes = []
    if not doors:
        raise RiftError(f"none of stage {stage}'s start positions lies on its navigation mesh, so the director cannot "
                        "tell where the player comes in")
    sizes = mesh.component_sizes()
    region = max({mesh.component(d.triangle) for d in doors}, key=lambda c: (sizes[c], -c))
    shut = [d for d in doors if mesh.component(d.triangle) != region]
    doors = [d for d in doors if mesh.component(d.triangle) == region]
    if shut:
        notes.append(f"{len(shut)} of the stage's doors open onto ground not joined to the main region; the dungeon "
                     "uses the region the others open into")
    depth, parent = mesh.tree([d.triangle for d in doors])
    room_cache: dict = {}

    def room(t: int) -> float:
        if t not in room_cache:
            room_cache[t] = mesh.clearance(mesh.centroid(t))
        return room_cache[t]

    # anchors: where the stage's enemy groups stand, with a lot flag or not (the director clears a copied lot
    # flag by default, and _template still prefers a group with none)
    groups = _groups(game, idx, w, stage)
    anchors: dict = {}
    for _, pts in groups.values():
        for q in pts:
            anchors.setdefault((math.floor(q[0] / ANCHOR), math.floor(q[2] / ANCHOR)), []).append(q)
    anchor_cache: dict = {}

    def anchored(t: int) -> bool:
        """Within ANCHOR of an enemy group of the stage: the new group copies the nearest one's areas and
        conditions."""
        if not anchors:
            return True
        if t not in anchor_cache:
            c = mesh.centroid(t)
            gx, gz = math.floor(c[0] / ANCHOR), math.floor(c[2] / ANCHOR)
            anchor_cache[t] = any(math.dist(c, a) <= ANCHOR for dx in (-1, 0, 1) for dz in (-1, 0, 1)
                                  for a in anchors.get((gx + dx, gz + dz), ()))
        return anchor_cache[t]

    boss_room = _beat_rule(mission.Beat("Boss", False, 0), 1.0)["room"]
    order = sorted(depth, key=lambda t: (-depth[t], t))
    held = [t for t in order if anchored(t)] or order       # deepest first
    target = next((t for t in held[:SCAN * 4] if room(t) >= boss_room), None)
    if target is None:
        target = max(held[:SCAN], key=lambda t: (room(t), -t))
        notes.append(f"no place deep in the stage has {boss_room / 100:.0f} m of room; the main path ends at the "
                     f"roomiest of the deepest ({room(target) / 100:.1f} m)")
    path = []
    t = target
    while t != -1:
        path.append(t)
        t = parent[t]
    path.reverse()
    floor = _bestiary.ROOM_FLOOR
    sites: list[Site] = []
    taken: set = set()

    def add(t: int) -> None:
        taken.add(t)
        sites.append(Site(len(sites), t, mesh.centroid(t), depth[t], room(t)))

    mark = door_clear
    for t in path:                              # candidates every half spacing along the main path
        if depth[t] < mark:
            continue
        near = [u for u in mesh.distances([t], limit=8.0) if depth.get(u, 0.0) >= door_clear]
        if not near:
            continue
        pick = max(near, key=lambda u: (room(u) >= floor and anchored(u), room(u), -u))
        if room(pick) >= floor and anchored(pick) and pick not in taken:
            add(pick)
        mark = depth[t] + spacing / 2
    # the path's end is a place like any other: 12 m from the doors and 1.5 m of room (it was added regardless,
    # 5.8 m from a door in stage 601, 6.1 in 250, 7.7 in 400)
    if target not in taken and depth[target] >= door_clear and room(target) >= floor:
        if sites and depth[target] - sites[-1].depth < spacing * 0.5:
            taken.discard(sites[-1].triangle)
            sites.pop()
        add(target)
    # the rest of the region: farthest-point sampling by the mesh from the doors, the path and the sites
    dmin = mesh.distances([d.triangle for d in doors] + path)
    for s in sites:
        _relax(mesh, s.triangle, dmin)
    while len(sites) < max_sites:
        cand = sorted((t for t in dmin if dmin[t] >= spacing and depth.get(t, 0.0) >= door_clear),
                      key=lambda t: (-dmin[t], t))
        pick = next((t for t in cand if anchored(t) and room(t) >= floor), None)
        if pick is None:
            break
        add(pick)
        _relax(mesh, pick, dmin)
    off = mesh.distances(path)
    places = _places(game, idx, stage)
    for s in sites:
        s.off = off.get(s.triangle, math.inf)
        s.role = "main" if s.off <= MAIN_OFF else ("beside" if s.off <= SIDE_OFF else "side")
        s.place = _place_of(places, s.point)
    sp = Space(stage, nav.nav_stage(stage), mesh, doors, region, depth, path, sites, depth[target], notes)
    sp.groups = groups
    return sp


def _groups(game, idx, w, stage: int) -> dict:
    """{group number: (lot flag number or None, [positions])} of the stage's enemy groups: a group whose
    ``mLoadCondition.mLotFlag`` is set loads only while its lot flag (``mDataLotFlag.mFlagNo``) is -- one of the
    stage's random sets, as every group of stages 420-447 is -- so a new group copying it would appear
    only then."""
    from . import gpl, typemap

    flags = {}
    # the base list only (st<S>_e, what tools/nav_proof.py measured): encounter.plan can also copy a group of a DLC
    # list sharing its numbers (st443_e_dlc01's 20-22, which have no lot flag), which the director does not choose
    data = nav.game_resource(game, idx, f"scr\\st{stage:03d}\\etc\\st{stage:03d}_e".encode("latin-1"),
                             typemap.BY_EXT["gpl"])
    for g in (gpl.parse(data).groups if data is not None else []):
        lot = g.get("mLoadCondition.mLotFlag", 0) or g.get("mLoadCondition.mLotFlag2", 0)
        flags[g["mGroup"]] = g.get("mDataLotFlag.mFlagNo", 0) if lot else None
    out = {}
    for n, flag in flags.items():
        pts = [p[5] for name in w.layouts_of(stage, "e", n) for p in w.placements_in(name) if p[5]]
        if pts:
            out[n] = (flag, pts)
    return out


def _template(groups: dict, point) -> tuple:
    """(group, its lot flag number or None, distance) of the nearest group with no lot flag within ANCHOR of the
    point, else of the nearest group; (None, None, inf) when the stage has none."""
    best = {True: None, False: None}
    for n, (flag, pts) in groups.items():
        d = min(math.dist(point, q) for q in pts)
        key = flag is None
        if best[key] is None or (d, n) < best[key][:2]:
            best[key] = (d, n, flag)
    if best[True] is not None and best[True][0] <= ANCHOR:
        d, n, flag = best[True]
    else:
        found = [b for b in best.values() if b is not None]
        if not found:
            return None, None, math.inf
        d, n, flag = min(found, key=lambda b: (b[0], b[1]))
    return n, flag, d


def _relax(mesh: nav.Mesh, source: int, dmin: dict) -> None:
    """Lower dmin (metres to the nearest chosen place) with the distances from a new place."""
    import heapq

    heap = [(0.0, source)]
    while heap:
        d, t = heapq.heappop(heap)
        if d >= dmin.get(t, math.inf):
            continue
        dmin[t] = d
        for u, c in mesh.adj[t]:
            if d + c < dmin.get(u, math.inf):
                heapq.heappush(heap, (d + c, u))


# -- 2..4. mission, places, enemies ----------------------------------------------------------------------
def _beat_rule(beat: mission.Beat, depth: float) -> dict:
    out = {"tier_lo": 1, "tier_hi": 1, "total": 1, "points": 1, "spread": 250.0, "room": 0.0}
    for u in beat_rules.evaluate({"beat": beat.code, "depth": max(0.0, min(1.0, depth))}).updates:
        out[u.name] = u.value
    return out


def pool(w, b: _bestiary.Bestiary, stage: int, which: str = "stage", only=None, exclude=()) -> list:
    """The enemies the director may choose: named, measured walkers the game places in this stage or in a
    stage sharing its mesh (``which="stage"``), or anywhere (``"game"``); ``only`` / ``exclude`` narrow it."""
    if which not in ("stage", "game"):
        raise RiftError("the pool is 'stage' (the enemies of this stage and those sharing its mesh) or 'game'")
    ns = nav.nav_stage(stage)
    family = {s for s in w.stages if nav.nav_stage(s) == ns}
    out = []
    for em, e in sorted(b.enemies.items()):
        if only is not None and em not in only:
            continue
        if em in exclude:
            continue
        if only is None and (not e["name"] or not b.walks(em) or b.critter(em)):
            continue
        if b.walks(em) is False:
            continue
        if which == "stage" and only is None and not family & set(e["stages"]):
            continue
        out.append(em)
    return out


def _frequencies(w, stage: int) -> dict:
    """How often the game places each enemy in this stage and those sharing its mesh (the weights that make a
    dungeon's mix resemble the stage's own)."""
    ns = nav.nav_stage(stage)
    out: dict = {}
    for p in w.placements:
        if p[4].startswith("em") and nav.nav_stage(w.layouts[p[0]]["stage"]) == ns:
            out[p[4]] = out.get(p[4], 0) + 1
    return out


def direct(game, idx, w, stage: int, seed: int = 0, grammar: mission.Grammar | None = None, which: str = "stage",
           only=None, exclude=(), spacing: float = SPACING, max_points: int = POOL_MAX_POINTS,
           b: _bestiary.Bestiary | None = None, sp: Space | None = None, keep_lots: bool = False,
           mod_root=None) -> Dungeon:
    """A whole dungeon for the stage (nothing is written): its mission, where each beat stands, which enemy.
    Each encounter copies the nearest of the stage's groups with no lot flag (within 60 m), else the nearest;
    a copied lot-flag load condition is cleared unless ``keep_lots`` (encounter.plan's ``always``).
    ``mod_root``: the mod the dungeon goes into, whose own navigation mesh (if it has one) is the ground."""
    if not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed < 2 ** 32:
        raise RiftError("the seed is a whole number 0..4294967295")
    if not isinstance(max_points, int) or isinstance(max_points, bool) or not 1 <= max_points <= encounter.MAX_POINTS:
        raise RiftError(f"at most 1..{encounter.MAX_POINTS} spawn points per encounter")
    spacing = _spacing(spacing)             # before spacing * 0.75 below, which overflowed for a huge whole number
    if not (sp.groups if sp is not None else _groups(game, idx, w, stage)):
        raise RiftError(f"stage {stage} places no enemy group of its own, so a new group would have none to copy its "
                        "areas and conditions from (encounter.py); the director needs a stage the game puts enemies in")
    b = b or _bestiary.load(game, idx, w)
    enemies = pool(w, b, stage, which, only, exclude)
    if not enemies:
        raise RiftError(f"no enemy is left to choose from for stage {stage} (the pool is empty; --pool game "
                        "chooses from the whole game)")
    # the mission this seed makes, or -- when the stage cannot hold it -- the next of the seed's missions that
    # fits; then the same with places closer together (a small stage), unless the space was given
    last, fitted = None, None
    spacings = [spacing] if sp is not None else [spacing] + [s for s in (spacing * 0.75, spacing * 0.55) if s >= 5.0]
    for gap in spacings:
        here = sp or space(game, idx, w, stage, gap, mod_root=mod_root)
        notes = list(here.notes)
        if not here.sites:
            last = RiftError(f"stage {stage} has no place {DOOR_CLEAR:.0f} m on foot from its doors with "
                             f"{_bestiary.ROOM_FLOOR / 100:.1f} m of room near the enemies it places: too small for a "
                             "dungeon")
            continue
        for attempt in range(MISSION_TRIES):
            mseed = (seed + attempt * 7919) % 2 ** 32
            beats = mission.expand(grammar or mission.DEFAULT_GRAMMAR, mseed)
            try:
                fitted = (here, beats, _place_beats(here, beats, gap, mseed, notes), attempt, gap)
                break
            except RiftError as e:
                last = e
        if fitted:
            break
    if fitted is None:
        raise RiftError(f"{last} (tried {MISSION_TRIES} missions from seed {seed} at {len(spacings)} spacings)")
    sp, beats, sites_of, attempt, used = fitted
    if attempt:
        notes.append(f"the seed's first {attempt} mission{'s' if attempt != 1 else ''} did not fit stage {stage}; "
                     f"this is its mission {attempt + 1}")
    if used != spacing:
        notes.append(f"the places are {used:.0f} m apart: at {spacing:.0f} m no mission fits stage {stage}")
    chosen = _choose_enemies(w, b, sp, beats, sites_of, enemies, stage, which, seed, notes)
    placed = []
    for i, bt in enumerate(beats):
        site = sp.sites[sites_of[i]]
        r = _beat_rule(bt, site.depth / sp.deepest if sp.deepest > 0 else 0.0)
        total = max(1, int(r["total"]))
        points = max(1, min(int(r["points"]), total, max_points))
        like, flag, far = _template(sp.groups, site.point)
        name = (b.get(chosen[i]) or {}).get("name", "")
        placed.append(Placed(bt, site, chosen[i], b.tier(chosen[i]), total, points, float(r["spread"]), like, name,
                             flag, flag is not None and not keep_lots))
    flagged = [p for p in placed if p.lot_flag is not None]
    if flagged:
        flags = ", ".join(str(f) for f in sorted({p.lot_flag for p in flagged}))
        notes.append(f"{len(flagged)} of the {len(placed)} encounters copy groups that load only under a lot flag "
                     f"({flags}; what sets each is UNKNOWN here) -- "
                     + ("their copies keep it and appear only while it is set" if keep_lots else
                        "their copies load whenever the stage does (--keep-lot-flags, or Studio's Lot flags box, keeps "
                        "the flags)"))
    return Dungeon(stage, seed, beats, sp, placed, enemies, notes)


def _place_beats(sp: Space, beats: list, spacing: float, seed: int, notes: list) -> dict:
    deepest = sp.deepest or 1.0
    mains = [i for i, bt in enumerate(beats) if not bt.side]
    n_main = len(mains)
    # which places each kind of beat may take, and how much it prefers each role
    prefer = {"Boss": {"main": 1.0, "beside": 0.3}, "side": {"side": 1.0, "beside": 0.3, "main": 0.02},
              "Ambush": {"beside": 1.0, "main": 0.3, "side": 0.2}, "main": {"main": 1.0, "beside": 0.5, "side": 0.15}}
    domains, weights = {}, {}
    for i, bt in enumerate(beats):
        roles = prefer["Boss" if bt.kind == "Boss" else "side" if bt.side else
                       "Ambush" if bt.kind == "Ambush" else "main"]
        cand = [s for s in sp.sites if s.role in roles and s.room >= _beat_rule(bt, s.depth / deepest)["room"]]
        if bt.kind == "Boss":
            deep = [s for s in cand if s.depth >= 0.75 * deepest]
            cand = deep or sorted(cand, key=lambda s: -s.depth)[:3]
        if not cand:
            raise RiftError(f"no place of stage {sp.stage} fits the mission's {'side ' if bt.side else ''}{bt.kind} "
                            f"(its role and room); try another --seed or a smaller --spacing")
        k = mains.index(i) if not bt.side else bt.after       # a side beat: the main beat it follows
        f = (k + 1) / n_main if not bt.side else min(1.0, (max(k, 0) + 1.5) / n_main)
        domains[i] = [s.id for s in cand]
        weights[i] = {s.id: roles[s.role] * (math.exp(-((min(1.0, s.depth / deepest) - f) / 0.22) ** 2) + 0.02)
                      for s in cand}
    if len({s for dom in domains.values() for s in dom}) < len(beats):
        raise RiftError(f"stage {sp.stage} has fewer places than the mission has beats ({len(beats)})")
    p = wfc.Problem(list(range(len(beats))), domains, weights)
    site = {s.id: s for s in sp.sites}
    p.all_different(range(len(beats)))
    gap = spacing * 100.0 * 0.5
    for a in range(len(beats)):
        for c in range(a + 1, len(beats)):
            p.constrain(a, c, lambda x, y: math.dist(site[x].point, site[y].point) >= gap)
    for a, c in zip(mains, mains[1:]):
        p.constrain(a, c, lambda x, y: site[y].depth >= site[x].depth + spacing * 0.3)
    for i, bt in enumerate(beats):
        if not bt.side:
            continue
        before = mains[bt.after] if bt.after >= 0 else None
        after = mains[bt.after + 1] if bt.after + 1 < len(mains) else None
        if before is not None:
            p.constrain(before, i, lambda x, y: site[y].depth >= site[x].depth - spacing)
        if after is not None:
            p.constrain(i, after, lambda x, y: site[x].depth <= site[y].depth + spacing)
    try:
        got = wfc.solve(p, seed=seed, budget=4000, max_checks=400_000).assignment
        on_path = sum(1 for i, bt in enumerate(beats) if bt.side and site[got[i]].role == "main")
        if on_path:
            notes.append(f"{on_path} side beat{'s' if on_path != 1 else ''} found no place off the main path and "
                         f"stand{'' if on_path != 1 else 's'} on it")
        return got
    except wfc.Unsatisfiable:
        raise RiftError(f"stage {sp.stage} has no arrangement of places for this mission "
                        f"({mission.describe(beats)}): too few places far enough apart, deep enough in order; try a "
                        "smaller --spacing or another --seed") from None
    except wfc.OutOfBudget:
        raise RiftError("the places for this mission were not found within the search budget; try another --seed") \
            from None


def _choose_enemies(w, b, sp: Space, beats: list, sites_of: dict, enemies: list, stage: int, which: str,
                    seed: int, notes: list) -> dict:
    deepest = sp.deepest or 1.0
    freq = _frequencies(w, stage)
    domains, weights = {}, {}
    for i, bt in enumerate(beats):
        site = sp.sites[sites_of[i]]
        r = _beat_rule(bt, site.depth / deepest)
        lo, hi = int(r["tier_lo"]), int(r["tier_hi"])
        fits = [em for em in enemies if b.room(em) <= site.room]
        dom = [em for em in fits if lo <= b.tier(em) <= hi]
        # a weaker enemy may stand in (down to tier 1), a stronger one at most one tier up: three hydras are
        # not a "fight"
        wide = (lo, hi)
        while not dom and (wide[0] > 1 or wide[1] < min(4, hi + 1)):
            wide = (max(1, wide[0] - 1), min(4, hi + 1, wide[1] + (1 if wide[0] == 1 else 0)))
            dom = [em for em in fits if wide[0] <= b.tier(em) <= wide[1]]
        if not dom:
            raise RiftError(f"no enemy of the pool fits the {bt.kind} (tier {lo}{'' if lo == hi else f'-{hi}'}, "
                            f"{site.room / 100:.1f} m of room at its place); widen the pool (--pool game) or try "
                            "another --seed")
        if wide != (lo, hi):
            notes.append(f"beat {i + 1} ({bt.kind}) asks for tier {lo}{'' if lo == hi else f'-{hi}'}; the pool has "
                         f"none with room there, so it takes tier {wide[0]}{'' if wide[0] == wide[1] else f'-{wide[1]}'}")
        domains[i] = dom
        weights[i] = {em: 1.0 + freq.get(em, 0) for em in dom}
    mains = [i for i, bt in enumerate(beats) if not bt.side]
    soft = [("differ", "neighbouring beats on the main path have different enemies")]
    if which == "game":
        soft.insert(0, ("companions", "neighbouring beats hold enemies the game places together"))
    while True:
        p = wfc.Problem(list(range(len(beats))), {k: list(v) for k, v in domains.items()}, weights)
        kinds = {k for k, _ in soft}
        for a, c in zip(mains, mains[1:]):
            if "differ" in kinds:
                p.constrain(a, c, lambda x, y: x != y)
            if "companions" in kinds:
                p.constrain(a, c, lambda x, y: x == y or b.companions(x, y) > 0)
        try:
            return wfc.solve(p, seed=seed, budget=4000, max_checks=400_000).assignment
        except (wfc.Unsatisfiable, wfc.OutOfBudget):
            if not soft:
                raise RiftError("no choice of enemies fits this mission") from None
            dropped = soft.pop(0)
            notes.append(f"relaxed: {dropped[1]} (the pool is too small for it here)")


# -- 5. the plan, 6. the check ---------------------------------------------------------------------------
def plan(d: Dungeon, story: str | None = None, hours: tuple | None = None) -> dict:
    """The dungeon as an encounter plan (``riftstone encounters PLAN --mod M`` writes it)."""
    entries = []
    for pl in d.placed:
        e = {"stage": d.stage, "enemy": pl.enemy, "total": pl.total,
             "at": [round(v, 2) for v in pl.site.point], "points": pl.points, "spread": pl.spread}
        if pl.like is not None:
            e["like"] = pl.like
        if pl.always:
            e["always"] = True
        if story:
            e["story"] = story
        if hours is not None:
            e["hours"] = list(hours)
        entries.append(e)
    return {"format": FORMAT, "game": "ddda", "encounters": entries}


def plan_text(d: Dungeon, story: str | None = None, hours: tuple | None = None) -> str:
    return json.dumps(plan(d, story, hours), indent=1)


@dataclass
class Proof:
    points: int
    on_mesh: int
    in_region: int
    farthest: float          # metres from a door, by the mesh
    problems: list


def check(sp: Space, encounters: list) -> Proof:
    """Every spawn point of the planned encounters, re-read from its layout bytes: on the mesh, in the doors'
    region, and how far from a door on foot."""
    return check_layouts(sp, [(enc.layout_name, enc.layout_data) for enc in encounters])


def check_layouts(sp: Space, layouts: list) -> Proof:
    """The same check over layouts given as bytes, or as (name, bytes)."""
    from . import lot

    problems, points, on, inside, far = [], 0, 0, 0, 0.0
    for item in layouts:
        name, data = item if isinstance(item, tuple) else ("layout", item)
        for r in lot.parse(data).records:
            p = r.vec()
            points += 1
            spot = sp.mesh.locate(p) if p else None
            if spot is None:
                problems.append(f"{name} #{r.id} {p} is not on the navigation mesh")
                continue
            on += 1
            if sp.mesh.component(spot.triangle) != sp.region:
                problems.append(f"{name} #{r.id} stands on ground not joined to the doors")
                continue
            inside += 1
            far = max(far, sp.depth.get(spot.triangle, math.inf))
    return Proof(points, on, inside, far, problems)


def describe(d: Dungeon) -> list[str]:
    sp = d.space
    lines = [f"stage {d.stage}{f' (navigation mesh of stage {sp.nav_stage})' if sp.nav_stage != d.stage else ''}: "
             f"{len(sp.doors)} door(s), {len(sp.sites)} places over {len(sp.depth)} triangles; the main path runs "
             f"{sp.deepest:.0f} m by the mesh",
             f"mission (seed {d.seed}): {mission.describe(d.beats)}"]
    for i, pl in enumerate(d.placed):
        s = pl.site
        where = f" in {s.place}" if s.place else ""
        lines.append(f"{i + 1:2}. {'side ' if pl.beat.side else ''}{pl.beat.kind:8} {pl.enemy}"
                     f"{f' {pl.name}' if pl.name else ''} (tier {pl.tier}) x{pl.total}"
                     f"{'' if pl.points == pl.total else f' from {pl.points} points'} at "
                     f"[{s.point[0]:.0f}, {s.point[1]:.0f}, {s.point[2]:.0f}]{where}: {s.depth:.0f} m in, "
                     f"{s.room / 100:.1f} m of room, {s.role}")
    return lines

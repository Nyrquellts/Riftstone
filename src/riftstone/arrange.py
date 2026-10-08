"""Enemies pasted in one spot set out on the ground in a shape: ``riftstone arrange``.

A layout made by hand often holds a stack: several placements pasted at one spot to try an encounter.  ``arrange``
finds them (placements within STACK of each other, at about the same height, are one stack; ``records`` names them
instead; a placement the game's own layout of that name holds unchanged stays where it is, ``Arranger.games_own``) and stands them in a shape around the stack's middle, on the stage's ground the way ``riftstone encounter``
and ``multiply`` stand theirs, each facing the way the shape says.  Only a placement's position and its heading
(``mAngle`` y) change: its id, enemy, every other field, and the records nobody named, keep their bytes.

Which way a placement faces (``tools/facing_proof.py``, measured on the installed game 2026-10-08): a model faces +Z
in its own space (the Arisen's toes stand about 12 cm ahead of its ankles along +Z in the rest pose), and mAngle y
turns that to (sin y, 0, cos y).  Over the game's 6,339 placements in 1,002 enemy and NPC layouts of three or more,
the angle between mAngle y and atan2(dx, dz) toward the layout's middle bunches at -3.6 degrees (mean resultant
0.211); read mirrored, atan2(-dx, dz), it does not bunch at all (0.008), and the readings that turn +Z a quarter
are ruled out by the toes.  The game's own groups lean toward their middle: 28% face within 30 degrees of it (17%
by chance).  So here a heading h faces (sin h, cos h) in x, z,
``--heading`` takes the game's own angle in degrees, and ``--toward x,z`` faces that point.

The shapes (the biggest enemy takes the first spot: the middle, the front or the tip):

* ``scatter`` (the default): a sunflower spiral one spacing apart, each spot nudged a little, each enemy facing its
  middle give or take 90 degrees (a third face within 30 degrees of it, near the game's own 28%);
* ``ring``: a circle, everyone facing out (a guard post); ``camp``: a circle facing in, with the biggest in the
  middle when there are five or more;
* ``line``: ranks of up to eight facing the heading, every other rank staggered; ``wedge``: a point facing it;
* ``flank``: an ambush across the heading's path, half on each side facing the path, the biggest waiting at the
  far end of it (three or more).

Where each one stands, tried at its spot in the shape and then on rings around it until it fits:

* a stage with a navigation mesh: the mesh under it, reached on foot from the stack's middle (no longer than twice
  the straight line plus 5 m: not behind a wall or across a drop), with as much room to the mesh's edge as the game
  gives that enemy (``bestiary.room``, at most ROOM) or the middle has;
* the open field (stage 100, no mesh): the cells' walkable collision (``terrain.Ground``) no steeper than about 45
  degrees, with ground under the line from the middle;
* elsewhere, for a stack off the ground, and for enemies the game keeps off it (``bestiary.walks`` False: flyers,
  wall crawlers): the spot at the placement's own height.

Every pair stands at least APART of the two enemies' spacings apart.  One whose spot has no room near it takes
the free ground nearest the stack's middle, or the best spot it can find at CROWD of that; when there is none it
stays in the pile where it was (``kept``, and said so), never put on top of another at a new spot nor in the air.  An enemy's spacing is the game's own: the
median distance from one of its placements to the nearest other of the same enemy in the same layout (``spacing``),
or twice its room for a big monster the game never places two of; ``spread`` sets one for all.

Nothing here is random: the same layout and options give the same bytes.  In game: UNKNOWN until played.
"""
from __future__ import annotations

import math
import re
import statistics
import zlib
from collections import defaultdict
from dataclasses import dataclass, field

from . import lot
from .errors import RiftError

SHAPES = ("scatter", "ring", "camp", "line", "wedge", "flank")
STACK = 100.0               # cm: placements this close (and within RISE in height) are one stack
SPREAD_MIN, SPREAD_MAX = 60.0, 3000.0
SPACE_MIN, SPACE_MAX = 150.0, 1500.0     # cm: an enemy's own spacing, as the game's layouts give it, kept within
SPACE_FLOOR = 30.0          # cm: two of the game's placements closer than this are a pasted pair, not a spacing
GROUND_SNAP = 1000.0        # cm: how far from the stack's middle the walkable ground may be
RISE = 250.0                # cm a spot's ground may lie over or under the middle's (plus SLOPE of the distance)
SLOPE = 0.6
ROOM = 300.0                # cm to the mesh's edge asked for at most
OFF_GROUND = 100.0          # cm over the field's ground: beyond it the stack is not standing on it
FIELD_STAND = 35.0          # cm over the field's ground a placement may stand (multiply's own)
APART = 0.75                # of the two enemies' spacings (each half of it), at least, between any two
RINGS = 6                   # rings of spots tried around a shape's spot
WIDE = 18                   # then rings around the stack's middle, for one whose spot has no room near it
CROWD = 0.5                 # of APART: when no spot keeps APART, the best may keep this much; closer, it stays put
STEP = 0.35                 # of the spacing, between those rings
JITTER = 0.12               # of the spacing: how far a scatter's spot is nudged
SCATTER_TURN = math.pi / 2  # radians either way from the middle a scattered enemy may face
FORMED_TURN = 0.15          # radians either way, in the formed shapes
RANK = 8                    # a line's rank holds at most this many
FLANK_MIN = 600.0           # cm from the path to a flank's side, at least (else twice the spacing)
GOLDEN = math.pi * (3.0 - math.sqrt(5.0))
_STAGE = re.compile(r"st(\d{3})_(\d{2,})m(\d{2,})n_([spent])(\d{2,})", re.I)


def _unit(key: str) -> float:
    """A number in [0, 1) that is this key's own (multiply's): the same layout gets the same shape on every run."""
    return zlib.crc32(key.encode("latin-1", "replace")) / 4294967296.0


def _wrap(a: float) -> float:
    return (a + math.pi) % (2.0 * math.pi) - math.pi


def layout_resource(text: str) -> str | None:
    """The game's resource name of the layout a label or file name says (``st424_00m00n_e09``, ``.lot`` or
    ``.lot.yaml``, or ``scr\\st424\\etc\\st424_00m00n_e09``): ``scr\\st424\\etc\\st424_00m00n_e09``, else None."""
    base = text.replace("\\", "/").rsplit("/", 1)[-1].lower()
    for ext in (".lot.yaml", ".lot"):
        if base.endswith(ext):
            base = base[:-len(ext)]
            break
    m = _STAGE.fullmatch(base)
    return f"scr\\st{m.group(1)}\\etc\\{base}" if m else None


def stage_of(text: str) -> int | None:
    """The stage a layout's name or file name says (``st100_43m55n_e67``, ``.lot`` or ``.lot.yaml``), else None."""
    m = _STAGE.search(text.replace("\\", "/").rsplit("/", 1)[-1])
    return int(m.group(1)) if m else None


def parse_point(text) -> tuple:
    """``x,z`` or ``x,y,z`` (centimetres, as the layout holds them) -> (x, z)."""
    try:
        v = [float(t) for t in str(text).replace(" ", "").split(",")]
    except ValueError:
        v = []
    if len(v) not in (2, 3) or not all(math.isfinite(t) and abs(t) < 1e7 for t in v):
        raise RiftError("--toward is x,z or x,y,z in centimetres, e.g. --toward 58600,-45360")
    return (v[0], v[-1])


def parse_records(text) -> list[int]:
    """``3,4,5`` or ``3-8`` (the numbers 'riftstone spawns list' shows) -> [3, 4, 5]."""
    out: list[int] = []
    for part in str(text).replace(" ", "").split(","):
        a, dash, b = part.partition("-")
        try:
            lo, hi = int(a), int(b) if dash else int(a)
        except ValueError:
            raise RiftError(f"--records takes record numbers like 3,4,5 or 3-8 ('riftstone spawns list' shows "
                            f"them), not {part!r}") from None
        if lo < 0 or hi < lo or hi - lo > lot.MAX_ID:
            raise RiftError(f"--records: {part} is not a range of record numbers")
        out.extend(n for n in range(lo, hi + 1) if n not in out)
    return out


# -- the shapes ----------------------------------------------------------------------------------------------
def formation(shape: str, n: int, s: float, key: str = "") -> list[tuple[float, float, float]]:
    """n spots of a shape, one spacing ``s`` apart: (across, ahead, facing) in the frame of the heading -- across
    to its right, ahead along it, facing in radians from it (0 the heading itself, pi/2 its right).  The first is
    the key spot (the biggest enemy's)."""
    if shape not in SHAPES:
        raise RiftError(f"--shape is one of {', '.join(SHAPES)}, not {shape!r}")
    if n <= 0:
        return []
    if n == 1:
        return [(0.0, 0.0, 0.0)]
    return globals()["_" + shape](n, s, key)


def _towards_middle(u: float, v: float) -> float:
    return math.atan2(-u, -v)


def _scatter(n: int, s: float, key: str):
    out, phase = [], _unit(key + "/phase") * 2.0 * math.pi
    for i in range(n):
        r, a = s * math.sqrt(i), phase + i * GOLDEN
        u, v = r * math.sin(a), r * math.cos(a)
        if i:
            ja, jr = _unit(f"{key}/{i}/ja") * 2.0 * math.pi, _unit(f"{key}/{i}/jr") * JITTER * s
            u, v = u + jr * math.sin(ja), v + jr * math.cos(ja)
            face = _towards_middle(u, v) + (_unit(f"{key}/{i}/turn") * 2.0 - 1.0) * SCATTER_TURN
        else:
            face = 0.0
        out.append((u, v, face))
    return out


def _circle(n: int, s: float, outward: bool, first: float = 0.0):
    r = max(s, s / (2.0 * math.sin(math.pi / n))) if n > 1 else 0.0       # neighbours one spacing apart
    out = []
    for k in range(n):
        a = first + 2.0 * math.pi * k / n
        out.append((r * math.sin(a), r * math.cos(a), a if outward else a + math.pi))
    return out


def _ring(n: int, s: float, key: str):
    return _circle(n, s, True)


def _camp(n: int, s: float, key: str):
    if n >= 5:
        return [(0.0, 0.0, 0.0)] + _circle(n - 1, s, False)
    return _circle(n, s, False)


def _middle_out(m: int) -> list[int]:
    """0..m-1 from the middle outward: 5 -> 2, 3, 1, 4, 0."""
    return sorted(range(m), key=lambda i: (abs(i - (m - 1) / 2.0), -i))


def _line(n: int, s: float, key: str):
    out, rank = [], 0
    while len(out) < n:
        m = min(RANK, n - len(out))
        shift = s * 0.5 if rank % 2 else 0.0
        for i in _middle_out(m):
            out.append(((i - (m - 1) / 2.0) * s + shift, -rank * s, 0.0))
        rank += 1
    return out


def _wedge(n: int, s: float, key: str):
    out, row = [], 0
    while len(out) < n:
        for i in _middle_out(row + 1)[:n - len(out)]:     # a short last row: its middle spots
            out.append(((i - row / 2.0) * s, -row * s * 0.866, 0.0))
        row += 1
    return out


def _flank(n: int, s: float, key: str):
    side = max(2.0 * s, FLANK_MIN)
    out = []
    if n >= 3:
        out.append((0.0, -side, 0.0))                     # the far end of the path, facing down it
    rest = n - len(out)
    halves = ((rest + 1) // 2, rest // 2)
    for sign, m in ((1.0, halves[0]), (-1.0, halves[1])):
        files = 2 if m > 4 else 1
        per = (m + files - 1) // files
        for j in range(m):
            f, k = j % files, j // files
            out.append((sign * (side + f * s * 0.866), (k - (per - 1) / 2.0 + f * 0.5) * s, -sign * math.pi / 2.0))  # the second file staggered
    return out


# -- the game's spacing ------------------------------------------------------------------------------------------
def spacing(w) -> dict[str, float]:
    """Each enemy's own spacing in the game's layouts: the median distance (x, z) from one of its placements to the
    nearest other of the same enemy in the same layout (pairs closer than SPACE_FLOOR left out), for enemies with
    three such distances or more."""
    by = defaultdict(lambda: defaultdict(list))
    for row in w.placements:
        name, em, p = row[0], row[4], row[5]
        lay = w.layouts.get(name)
        if p is None or not em or lay is None or lay["type"] not in ("e", "n"):
            continue
        by[name][em].append((p[0], p[2]))
    near = defaultdict(list)
    for ems in by.values():
        for em, pts in ems.items():
            for i, p in enumerate(pts):
                d = [math.dist(p, q) for j, q in enumerate(pts) if j != i]
                d = [x for x in d if x >= SPACE_FLOOR]
                if d:
                    near[em].append(min(d))
    return {em: statistics.median(ds) for em, ds in near.items() if len(ds) >= 3}


# -- the stage's ground ------------------------------------------------------------------------------------------
class _Ground:
    """One stage's ground as far as its files say: its navigation mesh, else the open field's collision, else
    nothing known."""

    def __init__(self, game, idx, stage: int | None, mod_root=None):
        from . import nav, terrain, typemap

        self.stage, self.mesh, self.field = stage, None, None
        if stage is None:
            return
        self.mesh = nav.stage_mesh(game, idx, stage, mod_root)
        if self.mesh is None and stage == 100:
            sbc = typemap.BY_EXT["sbc"]
            self.field = terrain.Ground(lambda name: nav.game_resource(game, idx, name.encode("latin-1"), sbc))

    @property
    def kind(self) -> str:
        return "on_mesh" if self.mesh is not None else "on_field" if self.field is not None else "flat"


class _Unit:
    """A placement being set out: its spacing, the room it asks for, whether it walks, where it comes in the shape
    (big monsters first, then by tier and size, then by its number)."""

    def __init__(self, number: int, rec: lot.Record, space: float, room: float, walks, order: tuple):
        self.number, self.rec, self.space, self.room, self.walks, self.order = number, rec, space, room, walks, order


@dataclass
class Placed:
    number: int             # the record's number in its layout ('riftstone spawns list')
    id: int
    enemy: str
    was: tuple
    now: tuple
    heading: float          # radians, the game's mAngle y
    how: str                # on_mesh, on_field, flat, kept (no room for it: where it was)


@dataclass
class Stack:
    middle: tuple
    shape: str
    spacing: float
    heading: float
    placed: list[Placed] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        ems = defaultdict(int)
        for p in self.placed:
            ems[p.enemy] += 1
        hows = defaultdict(int)
        for p in self.placed:
            hows[p.how] += 1
        where = ", ".join(f"{c} {_HOW[h]}" for h, c in sorted(hows.items()))
        return (f"{len(self.placed)} at [{self.middle[0]:.0f}, {self.middle[1]:.0f}, {self.middle[2]:.0f}] ("
                + ", ".join(f"{em} x{c}" if c > 1 else em for em, c in sorted(ems.items()))
                + f") -> {self.shape}, {self.spacing / 100:.1f} m apart, facing {round(math.degrees(self.heading))} "
                  f"degrees: {where}")


_HOW = {"on_mesh": "on the navigation mesh", "on_field": "on the field's ground", "flat": "at their own height",
        "kept": "where they were (no room)"}


@dataclass
class Arranged:
    label: str
    stage: int | None
    data: bytes             # the layout's new bytes (its old ones when nothing moved)
    stacks: list[Stack] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def moved(self) -> int:
        return sum(len(s.placed) for s in self.stacks)


def _enemyish(rec: lot.Record) -> bool:
    return (rec.cls.startswith("cSetInfoEnemy") or rec.cls == "cSetInfoNpc") and _pos(rec) is not None


def _pos(rec: lot.Record):
    p = rec.vec() if "mPosition" in rec.fields else None
    return p if p is not None and all(math.isfinite(v) and abs(v) < 1e7 for v in p) else None


def stacks(records: list[lot.Record]) -> list[list[int]]:
    """The layout's stacks: enemy placements within STACK of another (x, z) and RISE of its height, joined, two
    or more to a stack; record numbers in order."""
    pts = [(i, _pos(r)) for i, r in enumerate(records) if _enemyish(r)]
    parent = {i: i for i, _ in pts}

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    grid = defaultdict(list)
    for i, p in pts:
        grid[(math.floor(p[0] / STACK), math.floor(p[2] / STACK))].append((i, p))
    for i, p in pts:
        gx, gz = math.floor(p[0] / STACK), math.floor(p[2] / STACK)
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for j, q in grid.get((gx + dx, gz + dz), ()):
                    if j > i and math.hypot(p[0] - q[0], p[2] - q[2]) <= STACK and abs(p[1] - q[1]) <= RISE:
                        parent[root(j)] = root(i)
    out = defaultdict(list)
    for i, _ in pts:
        out[root(i)].append(i)
    return sorted((sorted(m) for m in out.values() if len(m) >= 2), key=lambda m: m[0])


class Arranger:
    """Arranges layouts of one game: the world map, the bestiary, the spacings and each stage's ground, read once."""

    def __init__(self, game, idx, w, mod_root=None):
        if getattr(game, "kind", "ddda") != "ddda":
            raise RiftError("riftstone arrange sets out Dark Arisen's layouts; Dragon's Dogma Online's enemies "
                            "stand where the server's spawn table puts them ('riftstone ddo')")
        self.game, self.idx, self.w, self.mod_root = game, idx, w, mod_root
        self._bestiary = None
        self._spacing = None
        self._grounds: dict = {}

    @property
    def bestiary(self):
        if self._bestiary is None:
            from . import bestiary

            self._bestiary = bestiary.load(self.game, self.idx, self.w)
        return self._bestiary

    def space(self, em: str) -> float:
        """An enemy's own spacing in centimetres (the module's text)."""
        if self._spacing is None:
            self._spacing = spacing(self.w)
            vals = sorted(self._spacing.values())
            self._usual = statistics.median(vals) if vals else 374.0
        v = self._spacing.get(em)
        if v is None:
            e = self.bestiary.get(em)
            v = 2.0 * self.bestiary.room(em) if e and e.get("boss", 0) >= 0.5 else self._usual
        return min(max(v, SPACE_MIN), SPACE_MAX)

    def ground(self, stage: int | None) -> _Ground:
        if stage not in self._grounds:
            self._grounds[stage] = _Ground(self.game, self.idx, stage, self.mod_root)
        return self._grounds[stage]

    def _unit_of(self, number: int, rec: lot.Record, spread) -> _Unit:
        em = (rec.name or "").lower()
        e = self.bestiary.get(em) if em else None
        walks = self.bestiary.walks(em) if e else None
        room = min(self.bestiary.room(em), ROOM) if walks else 0.0
        big = 1 if rec.fields.get("mBossFlag") or (e and e.get("boss", 0) >= 0.5) else 0
        sc = rec.vec("mScale") if "mScale" in rec.fields else None
        size = max((abs(v) for v in sc if math.isfinite(v)), default=1.0) if sc else 1.0
        tier = self.bestiary.tier(em) if e else 1
        space = float(spread) if spread is not None else self.space(em)
        return _Unit(number, rec, space, room, walks, (-big, -tier, -min(size, 20.0), number))

    def games_own(self, label: str, records) -> set[int]:
        """The numbers of the records the game's own layout of this name holds unchanged (the same id, kind and
        position, to 0.01 cm): the game's own, which a stack leaves where they are (``records`` can still name
        them).  The game stands placements together itself (174 stacks of 501 placements in 117 of its layouts,
        some at 0, 0, 0), so only what a modder pasted or moved is set out."""
        name = layout_resource(label)
        if name is None or name not in self.w.layouts:
            return set()
        from . import modfiles, typemap

        try:
            data, _ = modfiles.load(self.game, self.idx, None, name.encode("latin-1"), typemap.BY_EXT["lot"])
            game = lot.parse(data).records
        except (RiftError, OSError):
            return set()

        def key(r):
            p = _pos(r)
            return None if p is None else (r.id, r.kind) + tuple(round(v, 2) for v in p)

        own = {key(r) for r in game} - {None}
        return {i for i, r in enumerate(records) if key(r) in own}

    def layout(self, data: bytes, label: str, stage: int | None = None, *, shape: str = "scatter",
               spread: float | None = None, heading: float | None = None, toward=None, records=None) -> Arranged:
        """The layout's stacks (or the ``records`` named) set out; ``heading`` in degrees (the game's mAngle y),
        ``toward`` an (x, z) to face, else each stack keeps the way its placements face."""
        if shape not in SHAPES:
            raise RiftError(f"--shape is one of {', '.join(SHAPES)}, not {shape!r}")
        if spread is not None:
            spread = float(spread)
            if not (math.isfinite(spread) and SPREAD_MIN <= spread <= SPREAD_MAX):
                raise RiftError(f"--spread is {SPREAD_MIN:.0f} to {SPREAD_MAX:.0f} centimetres")
        if heading is not None:
            heading = float(heading)
            if not math.isfinite(heading):
                raise RiftError("--heading is an angle in degrees")
            heading = _wrap(math.radians(heading))
        if stage is None:
            stage = stage_of(label)
        lt = lot.parse(data)
        recs = lt.records
        out = Arranged(label, stage, data)
        if records is not None:
            bad = [n for n in records if n >= len(recs) or _pos(recs[n]) is None]
            if bad:
                raise RiftError(f"{label}: record{'s' if len(bad) > 1 else ''} {', '.join(map(str, bad))} "
                                f"{'are' if len(bad) > 1 else 'is'} not a placement of this layout (it has "
                                f"{len(recs)} records; 'riftstone spawns list' shows them)")
            groups = [sorted(set(records))] if records else []
        else:
            found = stacks(recs)
            own = self.games_own(label, recs) if found else set()
            groups = [[n for n in g if n not in own] for g in found]
            left = sum(len(g) - len(m) for g, m in zip(found, groups) if m)
            alone = sum(len(g) for g, m in zip(found, groups) if not m)
            groups = [m for m in groups if m]
            if left:
                one = left == 1
                out.notes.append(f"{label}: {left} of the game's own placements in "
                                 f"{'those stacks' if len(groups) > 1 else 'the stack'} {'stays' if one else 'stay'} "
                                 f"where the game has {'it' if one else 'them'}; the others are set out around "
                                 f"{'it' if one else 'them'}")
            if not groups and alone:
                out.notes.append(f"{label}: no stack but the game's own ({alone} placements it stands together "
                                 "itself, left as they are; --records moves them)")
                return out
        if not groups:
            out.notes.append(f"{label}: no stack (no two enemies stand within {STACK / 100:.0f} m of each other); "
                             "name the ones to set out with --records")
            return out
        ground = self.ground(stage)
        if stage is None:
            out.notes.append(f"{label}: no stage in its name, so its ground is not known: every placement keeps "
                             "the height it has (say --stage)")
        elif ground.kind == "flat":
            out.notes.append(f"{label}: stage {stage} has no navigation mesh and is not the open field: every "
                             "placement keeps the height it has")
        moving = {n for g in groups for n in g}
        taken = [(_pos(r), self.space((r.name or "").lower()) / 2.0) for i, r in enumerate(recs)
                 if i not in moving and _enemyish(r)]
        for g in groups:
            st = self._stack(recs, g, ground, label, shape, spread, heading, toward, taken)
            out.stacks.append(st)
        lot.check_ids(lt)
        out.data = lot.build(lt)
        return out

    def _stack(self, recs, members, ground, label, shape, spread, heading, toward, taken) -> Stack:
        pts = [_pos(recs[n]) for n in members]
        mid = tuple(sum(p[k] for p in pts) / len(pts) for k in range(3))
        units = sorted((self._unit_of(n, recs[n], spread) for n in members), key=lambda u: u.order)
        s = statistics.median(u.space for u in units)
        if toward is not None:
            if math.hypot(toward[0] - mid[0], toward[1] - mid[2]) < 1.0:
                raise RiftError("--toward is the stack's own middle; give a point it faces")
            h = math.atan2(toward[0] - mid[0], toward[1] - mid[2])
        elif heading is not None:
            h = heading
        else:
            c = sum(math.cos(recs[n].vec("mAngle")[1]) for n in members if _angle(recs[n]) is not None)
            si = sum(math.sin(recs[n].vec("mAngle")[1]) for n in members if _angle(recs[n]) is not None)
            h = math.atan2(si, c) if math.hypot(c, si) > 1e-6 else 0.0
        key = f"{label}#{recs[members[0]].id}"
        st = Stack(mid, shape, s, h)
        spots = formation(shape, len(units), s, key)
        fly = all(u.walks is False for u in units)
        if fly and ground.kind != "flat":
            st.notes.append("the game keeps " + ", ".join(sorted({u.rec.name or "?" for u in units}))
                            + " off the ground (bestiary): each keeps its own height")
        fit = _Fit(ground, mid, s, spots, st.notes, fly)
        right, ahead = (math.cos(h), -math.sin(h)), (math.sin(h), math.cos(h))
        turn = SCATTER_TURN if shape == "scatter" else FORMED_TURN
        for i, (u, (a, b, face)) in enumerate(zip(units, spots)):
            target = (mid[0] + a * right[0] + b * ahead[0], mid[2] + a * right[1] + b * ahead[1])
            was = _pos(u.rec)
            now, how = fit.place(u, target, was, f"{key}/{i}", taken)
            taken.append((now, u.space / 2.0))
            if shape != "scatter" and len(units) > 1:
                face += (_unit(f"{key}/{i}/turn") * 2.0 - 1.0) * turn
            y = _wrap(h + face)
            u.rec.set_vec("mPosition", now)
            ang = _angle(u.rec)
            if ang is not None:
                u.rec.set_vec("mAngle", (ang[0] if math.isfinite(ang[0]) else 0.0, y,
                                         ang[2] if math.isfinite(ang[2]) else 0.0))
            st.placed.append(Placed(u.number, u.rec.id, u.rec.name or "", was, now, y, how))
        kept = sum(p.how == "kept" for p in st.placed)
        if kept:
            st.notes.append(f"{kept} found no free ground {APART * CROWD:.2g} of a spacing from the others near their "
                            "spot or the middle: they stay where they were (a smaller --spread, or fewer, fits)")
        return st


def _angle(rec: lot.Record):
    return rec.vec("mAngle") if "mAngle" in rec.fields else None


class _Fit:
    """Stands one stack's enemies on its stage's ground (the module's text)."""

    def __init__(self, ground: _Ground, mid: tuple, s: float, spots, notes: list, fly: bool = False):
        self.g, self.mid, self.s = ground, mid, s
        self.kind = "flat" if fly else ground.kind
        self.start = self.reach = None
        self.room0 = 0.0
        step = max(STEP * s, 50.0)
        far = max(max((math.hypot(a, b) for a, b, _ in spots), default=0.0) + RINGS * step * 2.0, WIDE * step)
        if self.kind == "on_mesh":
            m = ground.mesh
            self.start = m.locate(mid) or m.nearest(mid, GROUND_SNAP, above=RISE, below=RISE)
            if self.start is None:
                notes.append(f"no walkable ground lies within {GROUND_SNAP / 100:.0f} m of the stack's middle: "
                             "they keep its height")
                self.kind = "flat"
            else:
                self.reach = m.distances([self.start.triangle], limit=2.0 * far / 100.0 + 5.0)
                self.room0 = m.clearance(self.start.point, limit=ROOM)
                if math.dist(self.start.point, mid) > 50.0:
                    notes.append(f"the stack's middle is {math.dist(self.start.point, mid) / 100:.1f} m from "
                                 "walkable ground; the shape stands around the nearest")
        elif self.kind == "on_field":
            g0 = ground.field.under(mid)
            if g0 is None or abs(mid[1] - g0[0]) > OFF_GROUND:
                notes.append("the stack does not stand on the field's ground (over it or off it): they keep its "
                             "height")
                self.kind = "flat"
            else:
                self.foot = (mid[0], g0[0], mid[2])
                self.lift = min(max(mid[1] - g0[0], 0.0), FIELD_STAND)

    def _rings(self, t, key: str, rings: int):
        yield t
        phase = _unit(key + "/ring") * 2.0 * math.pi
        step = max(STEP * self.s, 50.0)
        for ring in range(1, rings + 1):
            k = 6 * ring
            for i in range(k):
                a = phase + 2.0 * math.pi * i / k
                yield (t[0] + step * ring * math.sin(a), t[1] + step * ring * math.cos(a))

    def place(self, u: _Unit, target, was, key: str, taken):
        """(where it stands, how it was found): the first spot on the rings around its spot in the shape that keeps
        APART from every other, else the first on WIDE rings around the stack's middle (on its ground: the room
        nearest the middle); else the best of them when it keeps CROWD of APART; else where it was ("kept": in the
        pile it was pasted in), never on top of another at a new spot nor in the air off the ground."""
        best = best_score = None
        how = self.kind if (self.kind == "flat" or u.walks is not False) else "flat"
        for t, rings, k in ((target, RINGS, key), ((self.mid[0], self.mid[2]), WIDE, key + "/wide")):
            for c in self._rings(t, k, rings):
                p = self._ground(c, u, was) if how != "flat" else (c[0], was[1], c[1])
                if p is None:
                    continue
                score = min((math.dist(p, q) / (APART * (u.space / 2.0 + r)) for q, r in taken),
                            default=math.inf)
                if score >= 1.0:
                    return p, how
                if best_score is None or score > best_score:
                    best, best_score = p, score
        if best is not None and best_score >= CROWD:
            return best, how
        return tuple(was), "kept"

    def _ground(self, c, u: _Unit, was):
        d = math.hypot(c[0] - self.mid[0], c[1] - self.mid[2])
        rise = RISE + SLOPE * d
        if self.kind == "on_mesh":
            m, start = self.g.mesh, self.start
            spot = m.nearest((c[0], start.point[1], c[1]), max(STEP * self.s, 50.0), above=rise, below=rise,
                             within=self.reach)
            if spot is None:
                return None
            if self.reach.get(spot.triangle, math.inf) > 2.0 * math.dist(spot.point, start.point) / 100.0 + 5.0:
                return None                                   # behind a wall or across a drop
            room = min(u.room, self.room0)
            if room > 1.0 and m.clearance(spot.point, limit=room) < room - 0.5:
                return None
            return spot.point
        from . import terrain

        f, foot = self.g.field, self.foot
        g = f.under((c[0], foot[1], c[1]), above=rise, below=rise)
        if g is None or g[1] < terrain.GROUND_SLOPE:
            return None
        steps = max(1, math.ceil(d / 200.0))
        for k in range(1, steps):
            t = k / steps
            q = (foot[0] + (c[0] - foot[0]) * t, foot[1] + (g[0] - foot[1]) * t, foot[2] + (c[1] - foot[2]) * t)
            if f.under(q, above=RISE, below=RISE) is None:
                return None                                   # a gap between the middle and the spot
        return (c[0], g[0] + self.lift, c[1])

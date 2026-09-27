"""What the game itself shows about each enemy, measured from its own placements, navigation meshes and
parameter files -- the facts a level director needs to choose enemies that fit a place:

* **walks**: the share of its placements (in the stages that have a navigation mesh) that stand on the
  mesh.  The game's walkers stand on it at median 0 cm (Cyclopes 29 of 29, Garm 42 of 42, Goblins 100 of
  121); its flyers mostly do not (Giant Bats 14 of 160, Harpies 5 of 25);
* **room**: how far from the mesh's edge the game places it (the 10th percentile of the distance, across
  the ground, from its on-mesh placements to the nearest edge of the walkable surface), so a big monster
  goes only where the game gives big monsters room;
* **exp**, **large**: ``経験値`` and ``大きさ`` of ``charparam\\em\\<id>_cmn.prp`` (Wolves 50, Goblins 65,
  Cyclopes 4,000, Chimeras 7,700, Garm 29,000);
* **boss**: the share of its placements with ``mBossFlag`` (the game's big-monster flag: Cyclopes 40 of 42,
  Chimeras 19 of 20, every drake and wyrm);
* **stages**: every stage that places it; two enemies the game places in the same stage are companions.

Built from the installed game (Riftstone ships none of it) in a few seconds and cached beside the world map
in ``%LOCALAPPDATA%\\Riftstone\\bestiary-*.json``.
"""
from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from pathlib import Path

from . import lot, nav, world
from .errors import RiftError

SCHEMA = 1
MIN_MEASURED = 5            # an enemy is "measured" with at least this many placements in stages that have a mesh
WALKER = 0.5                # it walks when at least half of those stand on the mesh
ROOM_FLOOR = 150.0          # cm: nothing is placed closer than this to a mesh edge by the director
_CMN = re.compile(r"^charparam\\em\\(em\d{4}[a-z0-9]*)_cmn$", re.IGNORECASE)


def cache_path(game) -> Path:
    return world.cache_path(game).with_name(world.cache_path(game).name.replace("world-", "bestiary-"))


def _parameters(game, idx) -> dict:
    """{enemy id: (exp, large)} from every ``charparam\\em\\em####_cmn`` parameter file."""
    from . import prp, typemap, xfs

    tid = typemap.BY_EXT["prp"]
    out = {}
    for (name,) in idx.db.execute("SELECT DISTINCT name FROM res WHERE type=?", (tid,)):
        m = _CMN.match(name.decode("latin-1"))
        if not m:
            continue
        try:                                # a damaged entry (FormatError) is passed over like one that does not parse
            data = nav.game_resource(game, idx, name, tid)
            if data is None:
                continue
            doc = prp.parse(data)
        except RiftError:
            continue
        exp = large = None
        for obj in xfs.walk(doc.root):
            for p, vals in zip(doc.classes[obj.cls].props, obj.fields):
                if p.name == "経験値" and vals and exp is None:
                    exp = vals[0]
                elif p.name == "大きさ" and vals and large is None:
                    large = vals[0]
        out[m.group(1).lower()] = (int(exp or 0), int(large or 0))
    return out


def _quantile(values: list, q: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(q * (len(s) - 1) + 0.5))]


def build(game, idx, w: world.World) -> dict:
    t0 = time.time()
    params = _parameters(game, idx)
    placed = defaultdict(lambda: {"placements": 0, "boss": 0, "measured": 0, "on_mesh": 0, "room": [],
                                  "stages": set()})
    meshes = {}
    for st in sorted(w.stages):
        try:                                # one stage's damaged mesh does not stop every other stage's measure
            meshes[st] = nav.stage_mesh(game, idx, st)
        except RiftError:
            meshes[st] = None
    for row in w.placements:
        em = row[4]
        if not em.startswith("em") or not lot.KINDS[row[3]][0].startswith(("cSetInfoEnemy", "cSetInfoNpc")):
            continue
        lay = w.layouts[row[0]]
        if lay["type"] != "e":
            continue
        e = placed[em]
        e["placements"] += 1
        e["stages"].add(lay["stage"])
        extra = row[6] if len(row) > 6 else {}
        e["boss"] += 1 if extra.get("boss") else 0
        mesh, pos = meshes.get(lay["stage"]), row[5]
        if mesh is None or not pos:
            continue
        e["measured"] += 1
        spot = mesh.locate(pos)
        if spot is not None:
            e["on_mesh"] += 1
            e["room"].append(round(mesh.clearance(spot.point), 1))
    enemies = {}
    for em, e in sorted(placed.items()):
        exp, large = params.get(em.lower(), (0, 0))
        enemies[em] = {
            "name": w.enemies.get(em, {}).get("name", ""), "placements": e["placements"],
            "boss": round(e["boss"] / e["placements"], 3), "exp": exp, "large": large,
            "measured": e["measured"], "on_mesh": e["on_mesh"],
            "room": round(_quantile(e["room"], 0.1), 1) if e["room"] else None,
            "room_median": round(_quantile(e["room"], 0.5), 1) if e["room"] else None,
            "stages": sorted(e["stages"])}
    return {"schema": SCHEMA, "signature": world._signature(idx), "built": round(time.time() - t0, 1),
            "nav_stages": sorted(s for s, m in meshes.items() if m is not None), "enemies": enemies}


class Bestiary:
    def __init__(self, data: dict):
        self.data = data
        self.enemies = data["enemies"]
        self.nav_stages = set(data["nav_stages"])
        self._stages = {em: set(e["stages"]) for em, e in self.enemies.items()}

    def get(self, em: str) -> dict | None:
        return self.enemies.get(em)

    def measured(self, em: str) -> bool:
        e = self.enemies.get(em)
        return bool(e) and e["measured"] >= MIN_MEASURED

    def walks(self, em: str) -> bool | None:
        """True when the game places it on the mesh, False when mostly off it (flyers, wall crawlers), None
        when too few of its placements are in stages with a mesh to say."""
        e = self.enemies.get(em)
        if not e or e["measured"] < MIN_MEASURED:
            return None
        return e["on_mesh"] / e["measured"] >= WALKER

    def room(self, em: str) -> float:
        """Centimetres of open ground the game gives it at the 10th percentile (at least ROOM_FLOOR)."""
        e = self.enemies.get(em)
        return max(ROOM_FLOOR, e["room"]) if e and e["room"] is not None else ROOM_FLOOR

    def companions(self, a: str, b: str) -> int:
        """In how many stages the game places both."""
        return len(self._stages.get(a, set()) & self._stages.get(b, set()))

    def _tiers(self, em: str) -> dict:
        from .rules import tiers

        e = self.enemies.get(em) or {"exp": 0, "boss": 0.0}
        out = {"tier": 1, "critter": 0}
        for u in tiers.evaluate({"exp": e["exp"], "boss": 1 if e["boss"] >= 0.5 else 0}).updates:
            out[u.name] = int(u.value)
        return out

    def tier(self, em: str) -> int:
        """1 common .. 4 apex, from the game's own numbers (rules/tiers.nyr)."""
        return self._tiers(em)["tier"]

    def critter(self, em: str) -> bool:
        """Wildlife worth less EXP than a wolf (rules/tiers.nyr): rats, spiders, snakes, boars, deer."""
        return bool(self._tiers(em)["critter"])


def load(game, idx, w: world.World | None = None, rebuild: bool = False) -> Bestiary:
    path = cache_path(game)
    sig = world._signature(idx)
    if not rebuild and path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            # a cache that is not this module's object (JSON null, a list: data.get raised AttributeError) is rebuilt
            if isinstance(data, dict) and data.get("schema") == SCHEMA and data.get("signature") == sig:
                return Bestiary(data)
        except (OSError, ValueError, RecursionError, KeyError, TypeError, AttributeError):
            pass                                            # ...and so is one whose fields are not what build wrote
    data = build(game, idx, w if w is not None else world.load(game, idx))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)
    return Bestiary(data)


def describe(b: Bestiary, em: str) -> str:
    e = b.get(em)
    if e is None:
        return f"{em}: never placed in an enemy layout"
    walks = b.walks(em)
    how = ("walks" if walks else "keeps off the ground") if walks is not None else "not measured on a mesh"
    room = f", room {e['room'] / 100:.1f} m" if e["room"] is not None else ""
    return (f"{em} {e['name'] or '(no name)'}: tier {b.tier(em)} (EXP {e['exp']}"
            f"{', large' if e['large'] else ''}{', big-monster flag' if e['boss'] >= 0.5 else ''}), {how} "
            f"({e['on_mesh']} of {e['measured']} measured placements on the mesh{room}), "
            f"{e['placements']} placements in {len(e['stages'])} stage{'s' if len(e['stages']) != 1 else ''}")


if __name__ == "__main__":      # pragma: no cover - a quick look: python -m riftstone.bestiary [enemy]
    import sys

    from .game import find_game
    from .index import Index

    g = find_game()
    ix = Index(g)
    bb = load(g, ix, rebuild="--rebuild" in sys.argv)
    for key in [a for a in sys.argv[1:] if not a.startswith("--")] or sorted(bb.enemies):
        print(describe(bb, key))

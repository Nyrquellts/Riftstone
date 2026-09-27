"""Proof of the Gransys cell rule (and of Dragon's Dogma Online's world-space field), from the games.

    python tools/terrain_proof.py [--ddda PATH] [--ddo PATH]

Dark Arisen: a terrain cell model st100_<m>m<n>n holds X/Z from its cell's corner and the engine adds
(10000*n - 500000, 0, 10000*m - 500000).  Three measurements, each against controls that must lose:

  1. the static layouts (_s00, world space) stand on their cell's terrain under the rule;
  2. the cell terrain moved by the rule meets the world-space area LOD models (area_index*);
  3. every vanilla cell model (and 3b. cell collision) passes terrain.check, a world-space copy of it fails,
     localize brings worldize's copy back to the original within float32 rounding (every moved value within
     half a float32 step of its moved value plus half a step of the value moved back; the largest change is
     printed), and moving it into the world a second time gives the same bytes.

Dragon's Dogma Online: stage 0100's layouts stand on its field terrain (rom/scr/fd/sdl) with no transform.
Reads the games only, each archive as shipped (Riftstone's backup when an install replaced it).  Exit 0 when
every measurement comes out as documented (docs/terrain.md).
"""
from __future__ import annotations

import argparse
import collections
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.append(str(Path(__file__).resolve().parent))                       # check_corpus

from riftstone import arc, lot, lot_ddo, sbc, terrain, typemap  # noqa: E402
from riftstone.errors import RiftError  # noqa: E402
from riftstone.game import find_game  # noqa: E402

MOD, LOT = typemap.BY_EXT["mod"], typemap.BY_EXT["lot"]
BUCKET = 400.0


def moved_floats(data: bytes) -> list[tuple[int, int]]:
    """(offset, axis) of every float terrain.translate moves: a collision's (sbc.moved_floats), or a model's
    bounds, group spheres, envelope volumes and vertex positions."""
    if data[:4] == sbc.MAGIC:
        return sbc.moved_floats(data)
    p = terrain._parts(data)
    return [(off + 4 * a, a) for off in p.bounds + p.groups + p.envelopes + p.vertices for a in range(3)]


def round_trip(data: bytes, cell: terrain.Cell) -> tuple[bool, bool, float, bool]:
    """A cell piece moved into the world and back: (localize restored every moved value within float32
    rounding, a second worldize gives worldize's bytes again, the largest change of a value in cm, localize
    gave the original back byte for byte)."""
    from check_corpus import moved_back

    w = terrain.worldize(data, cell)
    back = terrain.localize(w, cell)
    worst, _, stray = moved_back(data, w, back, moved_floats(data))
    return stray is None, terrain.worldize(back, cell) == w, worst, back == data


def _moved_and_back(data: bytes, cell: terrain.Cell, counts: collections.Counter) -> float:
    """round_trip, counted (a key starting in capitals is a failure); the largest change."""
    restored, stable, worst, exact = round_trip(data, cell)
    counts["localize restores it within float32 rounding" if restored else "LOCALIZE STRAYS PAST ROUNDING"] += 1
    counts["localize gives it back byte for byte"] += exact
    counts["moving twice gives the same bytes" if stable else "MOVING TWICE CHANGES BYTES"] += 1
    return worst


def _read(game, name: str) -> arc.Archive:
    """An archive as the game shipped it (Riftstone's backup when an install replaced it)."""
    return arc.Archive.read(game.vanilla_arc(name))


class Ground:
    """Terrain heights by nearest vertex, bucketed in (x, z)."""

    def __init__(self, points, size=BUCKET):
        self.size = size
        self.b = collections.defaultdict(list)
        for p in points:
            self.b[(int(p[0] // size), int(p[2] // size))].append(p)

    def height(self, x, z):
        bx, bz = int(x // self.size), int(z // self.size)
        best = None
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for p in self.b.get((bx + dx, bz + dz), ()):
                    d = (p[0] - x) ** 2 + (p[2] - z) ** 2
                    if best is None or d < best[0]:
                        best = (d, p[1])
        return None if best is None else best[1]


def summary(label, gaps, total, within=100.0):
    got = sorted(g for g in gaps if g is not None)
    if not got:
        print(f"  {label:<34} nothing to compare")
        return None
    med = statistics.median(got)
    print(f"  {label:<34} n={len(got):>6}/{total:<6} median {med:8.1f} cm   within {within / 100:.0f} m: "
          f"{sum(g < within for g in got) / total:6.1%}")
    return med


def models_of(game, archives, want=None):
    out = {}
    for a in archives:
        try:
            ar = _read(game, a)
        except (OSError, RiftError):
            continue
        for e in ar.entries:
            if e.type_id == MOD and (want is None or want(e.name.decode("latin-1"))) and e.name not in out:
                out[e.name] = e.data()
    return out


def ddda(game) -> bool:
    ok = True
    names = [a for a in game.archives()]
    rel = [game.arc_name(p) for p in names]
    stage = [a for a in rel if a.lower().startswith("rom/stage/stage100/")]
    print(f"Dark Arisen ({game.root})")
    statics = collections.defaultdict(list)
    seen = set()
    for a in stage:
        if "/lot/" not in a.lower() and "/split" not in a.lower():
            continue
        for e in _read(game, a).entries:
            if e.type_id != LOT or e.name in seen:
                continue
            seen.add(e.name)
            ln = lot.parse_name(e.name.decode("latin-1"))
            if ln is None or ln.type != "s":
                continue
            for r in lot.parse(e.data()).placements:
                statics[terrain.Cell(ln.x, ln.z)].append(r.vec())
    cells = sorted(statics, key=lambda c: -len(statics[c]))[:60]
    tests = {
        "cell rule": lambda c, x, z: (x - c.offset[0], z - c.offset[2]),
        "control: axes swapped": lambda c, x, z: (z - c.offset[2], x - c.offset[0]),
        "control: mirrored x": lambda c, x, z: (terrain.CELL - (x - c.offset[0]), z - c.offset[2]),
        "control: one cell off in x": lambda c, x, z: (x - c.offset[0] - terrain.CELL, z - c.offset[2]),
    }
    gaps = {k: [] for k in tests}
    total = 0
    for c in cells:
        e = _read(game, c.archive).find(c.model.encode(), MOD)
        if e is None:
            continue
        ground = Ground(terrain.positions(e.data()))
        for (x, y, z) in statics[c]:
            total += 1
            for k, f in tests.items():
                u, v = f(c, x, z)
                h = ground.height(u, v)
                gaps[k].append(None if h is None else abs(h - y))
    print(f" 1. static placements on their cell's terrain ({len(cells)} cells)")
    meds = {k: summary(k, g, total) for k, g in gaps.items()}
    ok &= meds["cell rule"] is not None and meds["cell rule"] < 100
    ok &= all(m is None or m > 300 for k, m in meds.items() if k != "cell rule")

    area = models_of(game, [a for a in stage if "/area/" in a.lower()],
                     lambda n: n.lower().startswith("scr\\st100\\model\\st100_area") and n.lower().endswith("_h"))
    pts = [p for d in area.values() for p in terrain.positions(d)]
    ground = Ground(pts, 1000.0)
    gaps = {"cell rule": [], "control: one cell off in x": [], "control: axes swapped": []}
    total = 0
    for i, c in enumerate(terrain.cells()):
        if i % 4:
            continue
        e = _read(game, c.archive).find(c.model.encode(), MOD) if c.archive in rel else None
        if e is None:
            continue
        dx, _, dz = c.offset
        for (u, y, v) in terrain.positions(e.data())[::7]:
            total += 1
            for k, (x, z) in (("cell rule", (u + dx, v + dz)),
                              ("control: one cell off in x", (u + dx + terrain.CELL, v + dz)),
                              ("control: axes swapped", (v + dx, u + dz))):
                h = ground.height(x, z)
                gaps[k].append(None if h is None else abs(h - y))
    print(f" 2. cell terrain moved by the rule vs the world-space area LOD ({len(area)} models)")
    meds = {k: summary(k, g, total, 200.0) for k, g in gaps.items()}
    ok &= meds["cell rule"] is not None and meds["cell rule"] < 100
    ok &= all(m is None or m > 1000 for k, m in meds.items() if k != "cell rule")

    counts = collections.Counter()
    worst = 0.0
    for c in terrain.cells():
        e = _read(game, c.archive).find(c.model.encode(), MOD) if c.archive in rel else None
        if e is None:
            counts["split archive without its own terrain model"] += 1
            continue
        d = e.data()
        errors, notes = terrain.check(d, c)
        counts["vanilla passes check" if not errors and not notes else "VANILLA FLAGGED"] += 1
        w = terrain.worldize(d, c)
        if c.offset[0] or c.offset[2]:
            flagged = any("world coordinates" in x for x in terrain.check(w, c)[0])
            counts["world copy caught" if flagged else "WORLD COPY MISSED"] += 1
        worst = max(worst, _moved_and_back(d, c, counts))
    print(" 3. every vanilla cell model")
    for k, v in sorted(counts.items()):
        print(f"  {k:<44} {v}")
    print(f"  largest change after worldize + localize      {worst:.5f} cm")
    ok &= not any(k.isupper() or k.split()[0].isupper() for k in counts)

    SBC = typemap.BY_EXT["sbc"]
    counts = collections.Counter()
    worst = 0.0
    gaps, ctrl, total = [], [], 0
    for c in terrain.cells():
        if c.archive not in rel:
            continue
        ar = _read(game, c.archive)
        me = ar.find(c.model.encode(), MOD)
        ground = Ground(terrain.positions(me.data())) if me else None
        for name in c.collisions:
            e = ar.find(name.encode(), SBC)
            if e is None:
                counts["COLLISION MISSING"] += 1
                continue
            d = e.data()
            errors, notes = terrain.check(d, c)
            if errors:
                counts["VANILLA FAILS CHECK"] += 1
            else:
                counts["vanilla passes (no errors)"] += 1
                if notes:
                    counts["vanilla gets the both-frames note"] += 1
            w = terrain.worldize(d, c)
            if c.offset[0] or c.offset[2]:
                werr, wnote = terrain.check(w, c)
                if any("world coordinates" in x for x in werr):
                    counts["world copy caught"] += 1
                elif any("both in the cell's frame and in the world" in x for x in wnote):
                    counts["world copy reported as undecidable"] += 1
                else:
                    counts["WORLD COPY MISSED"] += 1
            worst = max(worst, _moved_and_back(d, c, counts))
            if ground is not None and "\\st100e_" in name:        # walkable collision against the terrain
                for (x, y, z) in sbc.positions(d)[::5]:
                    if 0 <= x <= terrain.CELL and 0 <= z <= terrain.CELL:
                        total += 1
                        h = ground.height(x, z)
                        gaps.append(None if h is None else abs(h - y))
                        h2 = ground.height(z, x)
                        ctrl.append(None if h2 is None else abs(h2 - y))
    print(" 3b. every vanilla cell collision (.sbc, the cell's h and e meshes)")
    for k, v in sorted(counts.items()):
        print(f"  {k:<44} {v}")
    print(f"  largest change after worldize + localize      {worst:.5f} cm")
    med = summary("e-mesh vertices on the cell's terrain", gaps, total)
    cmed = summary("control: axes swapped", ctrl, total)
    ok &= not any(k.split()[0].isupper() for k in counts)
    ok &= med is not None and med < 100 and (cmed is None or cmed > 300)
    return ok


def ddo(game) -> bool:
    rel = [game.arc_name(p) for p in game.archives()]
    print(f"Dragon's Dogma Online ({game.root})")
    field = models_of(game, [a for a in rel if a.lower().startswith("rom/scr/fd/sdl/")],
                      lambda n: n.lower().startswith("scr\\fd\\model\\"))
    pts, skipped = [], 0
    for d in field.values():
        try:
            pts += terrain.positions(d)
        except RiftError:
            skipped += 1
    ground = Ground(pts)
    placements, seen = [], set()
    for a in rel:
        if not a.lower().startswith("rom/stage/st0100/"):
            continue
        for e in arc.Archive.read(game.arc_path(a)).entries:
            if e.name in seen:
                continue
            data = e.data() if e.type_id == LOT else b""
            if not lot_ddo.is_ddo_lot(data):
                continue
            seen.add(e.name)
            for r in lot_ddo.parse(data).records:
                p = r.get("mPosition")
                if p is not None:
                    placements.append(tuple(lot_ddo.f32(b) for b in p[:3]))
    print(f" 4. stage 0100's {len(placements)} placements on its field terrain ({len(field)} models, "
          f"{skipped} not readable)")
    meds = {}
    for label, (dx, dz) in (("no transform (world space)", (0.0, 0.0)), ("control: moved 100 m in x", (1e4, 0.0)),
                            ("control: moved 100 m in z", (0.0, 1e4))):
        gaps = [None if (h := ground.height(x + dx, z + dz)) is None else abs(h - y) for (x, y, z) in placements]
        meds[label] = summary(label, gaps, len(placements))
    first = meds["no transform (world space)"]
    return first is not None and first < 100 and all(m is None or m > 300 for k, m in meds.items()
                                                       if k.startswith("control"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ddda", default=None)
    ap.add_argument("--ddo", default=None)
    args = ap.parse_args()
    results = []
    for kind, path, fn in (("ddda", args.ddda, ddda), ("ddo", args.ddo, ddo)):
        try:
            game = find_game(path or kind)
        except RiftError as e:
            print(f"{kind}: skipped ({e})")
            continue
        results.append(fn(game))
    print("TERRAIN PROOF " + ("PASSED" if results and all(results) else "FAILED"))
    return 0 if results and all(results) else 1


if __name__ == "__main__":
    sys.exit(main())

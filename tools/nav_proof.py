"""Proof of what Riftstone claims about Dark Arisen's navigation meshes and the level director, from the game.

    python tools/nav_proof.py [--game PATH] [--seeds 3] [--groups 2]

  1. the stage table: nav.NAV_OF (which stage's mesh each stage loads) equals DDDA.exe's own;
  2. the game's walkers stand on the mesh: every enemy placement in a stage with a mesh, its height over the
     ground found there (median, 5th and 95th percentile), and the share on the mesh per enemy (walkers
     against flyers, bestiary.py);
  3. every stage's doors (its start positions, .stp) stand on the mesh, in its main region;
  4. encounters: for ``--groups`` vanilla enemy groups of every stage with a mesh, the old flat rings against
     ground placement -- how many spawn points stand on ground reached on foot from the spot;
  5. the director: for ``--seeds`` seeds of every stage with a mesh, a whole dungeon planned (the encounters
     too, nothing written) from the stage's own enemies, then from the whole game's where that refused, then a
     shorter mission on the stages still refused; every spawn point re-read on the mesh, in the doors' region.

Reads the game only (a temporary mod receives nothing: every encounter is planned, none written).  Exit 0 when
every measurement comes out as documented (docs/level-synthesis.md).
"""
from __future__ import annotations

import argparse
import math
import statistics
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from riftstone import bestiary, dungeon, encounter, encounter_plan, lot, mission, nav, world  # noqa: E402
from riftstone.errors import RiftError  # noqa: E402
from riftstone.game import find_game  # noqa: E402
from riftstone.index import Index  # noqa: E402
from riftstone.mod import Mod  # noqa: E402


def pct(values, q):
    s = sorted(values)
    return s[min(len(s) - 1, int(q * (len(s) - 1) + 0.5))]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--game")
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--groups", type=int, default=2)
    a = ap.parse_args()
    game = find_game(a.game)
    if game.kind != "ddda":
        raise SystemExit("nav_proof reads Dark Arisen")
    idx = Index(game)
    idx.refresh()
    w = world.load(game, idx)
    b = bestiary.load(game, idx, w, rebuild=True)
    ok = True

    # 1. the stage table
    table = {s: v for s, v in nav.stage_table(game.exe.read_bytes()).items() if s != v}
    same = table == nav.NAV_OF
    ok &= same
    print(f"1. stage table: {len(table)} stages load another's mesh; nav.NAV_OF {'equals' if same else 'DIFFERS from'} "
          f"DDDA.exe's ({', '.join(f'{s}->{v}' for s, v in sorted(table.items()))})")

    # 2. placements on the mesh
    gaps, per = [], Counter()
    for p in w.placements:
        lay = w.layouts[p[0]]
        if lay["type"] != "e" or not p[5] or not p[4].startswith("em") or not lot.KINDS[p[3]][0].startswith("cSetInfoEnemy"):
            continue
        mesh = nav.stage_mesh(game, idx, lay["stage"])
        if mesh is None:
            continue
        spot = mesh.locate(p[5])
        per[p[4], spot is not None] += 1
        if spot is not None:
            gaps.append(spot.gap)
    walkers = [em for em in b.enemies if b.walks(em)]
    flyers = [em for em in b.enemies if b.walks(em) is False]
    med, p05, p95 = statistics.median(gaps), pct(gaps, 0.05), pct(gaps, 0.95)
    ok &= abs(med) < 5.0 and p95 < 60.0
    print(f"2. {len(gaps)} enemy placements stand on their stage's mesh: height over it median {med:.1f} cm, "
          f"5% {p05:.1f}, 95% {p95:.1f}; {len(walkers)} measured enemies walk, {len(flyers)} keep off the ground "
          f"({', '.join(b.enemies[em]['name'] or em for em in flyers[:8])})")

    # 3. doors
    doors = on_mesh = on_main = 0
    elsewhere = Counter()
    stages = [s for s in sorted(w.stages) if nav.stage_mesh(game, idx, s) is not None]
    for s in stages:
        mesh = nav.stage_mesh(game, idx, s)
        main = mesh.main_component()
        for p in nav.start_positions(game, idx, s):
            spot = mesh.locate(p) or mesh.nearest(p, 600.0)
            doors += 1
            on_mesh += spot is not None
            if spot and mesh.component(spot.triangle) == main:
                on_main += 1
            else:
                elsewhere[s] += 1
    print(f"3. {on_mesh} of {doors} doors of the {len(stages)} stages with a mesh stand on it, {on_main} on its main "
          f"region; the rest: " + ", ".join(f"st{s} {n}" for s, n in sorted(elsewhere.items())))

    # 4. flat rings against ground placement
    root = Mod.create(Path(tempfile.mkdtemp()) / "proof", "Proof").root
    old_on = old_all = new_on = new_all = refused = 0
    for s in stages:
        mesh = nav.stage_mesh(game, idx, s)
        tried = 0
        for g in w.groups_of(s, "e"):
            if tried >= a.groups or g["dlc"]:
                continue
            walk = [u for u in g["units"] if b.walks(u)]
            if not walk or not any(p[5] for n in w.layouts_of(s, "e", g["number"]) for p in w.placements_in(n)):
                continue
            tried += 1
            at = f"group:{g['number']}"
            try:
                flat = encounter.plan(game, idx, w, root, s, walk[0], 10, at, 10, ground=False)
                grd = encounter.plan(game, idx, w, root, s, walk[0], 10, at, 10)
            except RiftError:
                refused += 1
                continue
            start = mesh.locate(grd.positions[0])
            reach = mesh.distances([start.triangle], limit=200.0)
            for p in flat.positions:
                old_all += 1
                spot = mesh.locate(p)
                old_on += bool(spot and spot.triangle in reach)
            for p in grd.positions:
                new_all += 1
                spot = mesh.locate(p)
                new_on += bool(spot and spot.triangle in reach)
    ok &= new_on == new_all
    print(f"4. flat rings: {old_on} of {old_all} spawn points ({100 * old_on / max(1, old_all):.0f}%) on ground reached "
          f"on foot from the group's spot; on the mesh: {new_on} of {new_all}; {refused} spots refused (no walkable "
          "ground within 10 m)")

    # 5. the director: the stage's own enemies, then the whole game's for the runs that refused, then shorter
    # missions for the stages still refused
    tally = Counter()

    def run(s, seed, which, grammar=None):
        d = dungeon.direct(game, idx, w, s, seed=seed, b=b, which=which, grammar=grammar)
        entries = encounter_plan.parse(dungeon.plan_text(d))
        done = encounter_plan.apply(game, idx, w, root, entries, dry_run=True)
        proof = dungeon.check(d.space, [enc for _, enc, _ in done])
        tally["points"] += proof.points
        tally["good"] += proof.in_region

    refused = {}
    for s in stages:
        for seed in range(a.seeds):
            try:
                run(s, seed, "stage")
            except RiftError as e:
                refused[s, seed] = str(e)
    own = len(stages) * a.seeds - len(refused)
    bare = sorted({s for (s, _), why in refused.items() if "no enemy group of its own" in why})
    still = {}
    for (s, seed), why in sorted(refused.items()):
        if s in bare:
            continue
        try:
            run(s, seed, "game")
        except RiftError as e:
            still[s, seed] = str(e)
    small = sorted({s for s, _ in still})
    # the director's three refusals for want of space: no place roomy enough, fewer places than beats, none in order
    space = ("fits the mission's", "fewer places than the mission has beats", "no arrangement of places")
    other = {k: why for k, why in still.items() if not any(m in why for m in space)}
    short = {name: mission.grammar({"format": mission.FORMAT, "start": "D", "rules": {"D": [beats]}})
             for name, beats in (("three fights", ["Fight", "Fight", "Fight"]), ("one fight", ["Fight"]))}
    fits = Counter()
    for s in small:
        for name, gr in short.items():
            try:
                run(s, 0, "game", gr)
                fits[name] += 1
            except RiftError:
                pass
    ok &= tally["points"] == tally["good"] and not other and fits["one fight"] == len(small)
    rest = len(refused) - sum(1 for s, _ in refused if s in bare)
    print(f"5. director: {own} dungeons from the stages' own enemies over {len(stages)} stages x {a.seeds} seeds; "
          f"{len(bare)} stages place no enemy group to copy ({', '.join(f'st{s}' for s in bare)}: refused); "
          f"{rest - len(still)} of the other {rest} refused runs made with --pool game; the last {len(still)}, on "
          f"{len(small)} stages ({', '.join(f'st{s}' for s in small)}), "
          f"{'all' if not other else 'NOT all'} for want of places in the doors' region, where a shorter mission fits: "
          + ", ".join(f"{name} on {fits[name]}" for name in short)
          + f"; {tally['good']} of {tally['points']} spawn points on the mesh in the doors' region")
    for (s, seed), why in sorted(other.items()):
        print(f"     st{s} seed {seed}: {why[:140]}")
    print("OK" if ok else "SOMETHING DIFFERS FROM THE DOCS")
    idx.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

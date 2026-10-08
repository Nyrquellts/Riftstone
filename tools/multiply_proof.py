"""What 'riftstone multiply' stands on, measured again from the game (docs/spawn-multiplier.md).

    python tools/multiply_proof.py [--quick]

Dark Arisen, each archive as shipped.  Five measurements:

  1. every enemy layout and the archives that hold it: how many archives, and that their copies are the same
     bytes (so one file in a mod's files/ is the right form for a changed layout);
  2. what the enemy placements are: how far the nearest other placement of a layout stands, the ids a group
     uses (once across its layouts, all under the kill record's 32), life point groups, placements with an AI
     script of their own, big monsters, hostile humans placed as NPCs and their mNpcId;
  3. the open field's walkable collision (st100e_<m>m<n>n_mrg00.sbc): a triangle's three vertex numbers count
     from its part's first vertex (read that way every stored normal is its corners' own; read as file-wide
     numbers they are not), and how far stage 100's enemy placements stand from that ground;
  4. the whole game multiplied by 2, by 3, and by 2 with the big monsters: copies, groups, stages, where the
     copies stand, what is left alone, what did not fit; every changed group's ids used once and under 32;
  5. what that costs installed: the game's archives that hold the changed layouts, and their size.

--quick skips 4 and 5 for x3 and the big monsters.  Reads the game only; writes nothing.  Exit 0 when every
measurement comes out as documented (EXPECT below); 0 with a note when no game is installed.
"""
from __future__ import annotations

import argparse
import math
import struct
import sys
import time
import zlib
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from riftstone import lot, modfiles, multiply, nav, sbc, terrain, typemap, world  # noqa: E402
from riftstone.errors import RiftError  # noqa: E402
from riftstone.game import find_game  # noqa: E402
from riftstone.index import Index  # noqa: E402

# Steam build 2364871, measured 2026-10-04.
EXPECT = {
    "holders": {1: 896, 3: 523},
    "holders differ": 0,
    "ids past the kill record": 0,
    "ids used twice in a group": 0,
    "life point groups": (36, 552),
    "people": (555, 1, 4),                  # cSetInfoNpc placements, distinct mNpcId (-1), with an FSMPath
    "x2": {"copies": 5347, "groups": 854, "placed": 6606, "on_mesh": 2551, "on_field": 1868, "beside": 928,
           "scripted": 178, "story": 8, "big": 425, "short": 648, "short_groups": 57, "caps": 21, "champions": 595},
    "x2 files": (1038, 43),                 # files, stages
    "x3": {"copies": 9654, "on_mesh": 4800, "on_field": 3104, "beside": 1750, "short": 2336, "short_groups": 118,
           "champions": 650},
    "x2 bosses": {"copies": 5772, "groups": 1102},
    "x2 bosses files": (1287, 45),
    "x2 archives": 576,
    "x2 bosses archives": 590,
}


def _said(label: str, got, key: str) -> bool:
    want = EXPECT[key]
    same = got == want
    print(f"   {label}: {got}" + ("" if same else f"   DIFFERS: documented {want}"))
    return same


def _quantile(sorted_values, f: float):
    return sorted_values[min(len(sorted_values) - 1, int(f * len(sorted_values)))]


def layouts(game, idx, w) -> bool:
    LOT = typemap.BY_EXT["lot"]
    holders, differ = Counter(), 0
    for name, lay in sorted(w.layouts.items()):
        if lay["type"] != "e":
            continue
        raw = name.encode("latin-1")
        arcs = idx.archives_with(raw, LOT)
        holders[len(arcs)] += 1
        if len(arcs) > 1:
            differ += len({zlib.crc32(modfiles._entry(game.vanilla_arc(a), raw, LOT).data()) for a in arcs}) > 1
    print(f"1. {sum(holders.values()):,} enemy layouts")
    ok = _said("archives a layout sits in -> layouts", dict(sorted(holders.items())), "holders")
    return _said("layouts whose archives hold different bytes", differ, "holders differ") and ok


def placements(game, idx, w) -> tuple[bool, list]:
    LOT = typemap.BY_EXT["lot"]
    gaps, ids, life, field = [], defaultdict(list), defaultdict(int), []
    npc, npc_ids, npc_fsm, scripted, big, total = 0, set(), 0, 0, 0, 0
    for name, lay in sorted(w.layouts.items()):
        if lay["type"] != "e":
            continue
        records = lot.parse(modfiles.load(game, idx, None, name.encode("latin-1"), LOT)[0]).records
        pts = []
        for r in records:
            ids[(lay["stage"], lay["number"])].append(r.id)
            if multiply._why_not(r, True, None) == "not_enemy":
                continue
            total += 1
            pts.append(r.vec())
            if lay["stage"] == 100:
                field.append(r.vec())
            v = r.fields.get("mLifePointGroup", 0xFFFFFFFF)
            if v != 0xFFFFFFFF:
                life[v] += 1
            scripted += multiply._script(r) not in multiply.NO_SCRIPT
            big += bool(r.fields.get("mBossFlag"))
            if r.cls == "cSetInfoNpc":
                npc += 1
                npc_ids.add(r.fields.get("mNpcId"))
                npc_fsm += bool(r.fields.get("FSMPath"))
        for i, p in enumerate(pts):
            d = min((math.dist(p, q) for j, q in enumerate(pts) if j != i), default=None)
            if d is not None:
                gaps.append(d)
    gaps.sort()
    print(f"2. {total:,} enemy placements in {len(ids):,} groups")
    print(f"   nearest other placement of the layout: a quarter within {_quantile(gaps, 0.25):.0f} cm, "
          f"half within {_quantile(gaps, 0.5):.0f} cm ({len(gaps):,} placements with a neighbour)")
    ok = _said("ids past the kill record's 32", sum(1 for v in ids.values() for i in v if i >= lot.KILL_BITS),
               "ids past the kill record")
    ok = _said("ids a group uses twice", sum(len(v) - len(set(v)) for v in ids.values()), "ids used twice in a group") and ok
    ok = _said("life point groups (values, placements)", (len(life), sum(life.values())), "life point groups") and ok
    ok = _said("hostile humans placed as NPCs (placements, distinct mNpcId, with a script)",
               (npc, len(npc_ids), npc_fsm), "people") and ok
    print(f"   with an AI script of their own: {scripted:,}; big monsters (mBossFlag): {big:,}")
    return ok, field


def field_ground(game, idx, field: list) -> bool:
    SBC = typemap.BY_EXT["sbc"]
    cells = {terrain.cell_at(p[0], p[2]) for p in field}
    tris = agree = agree_filewide = files = 0
    for cell in sorted(cells, key=lambda c: (c.m, c.n)):
        data = nav.game_resource(game, idx, cell.collisions[1].encode("latin-1"), SBC)
        if data is None:
            continue
        files += 1
        s = sbc.parse(data)
        verts = sbc.positions(data)
        mine = sbc.triangles(data)                    # refuses a vertex number past its part
        tris += len(mine)

        def own_normal(a, b, c, n) -> bool:
            u, v = [b[i] - a[i] for i in range(3)], [c[i] - a[i] for i in range(3)]
            cr = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
            size = math.sqrt(sum(x * x for x in cr))
            return size > 1e-6 and abs(sum(cr[i] / size * n[i] for i in range(3))) >= 0.99

        agree += sum(own_normal(*t) for t in mine)
        for k in range(s.triangles):                  # the same triangles, their numbers read as the file's
            nx, ny, nz, a, b, c = struct.unpack_from("<3f3H", data, s.triangle_offset + k * sbc.TRIANGLE)
            agree_filewide += max(a, b, c) < len(verts) and own_normal(verts[a], verts[b], verts[c], (nx, ny, nz))
    ground = terrain.Ground(lambda name: nav.game_resource(game, idx, name.encode("latin-1"), SBC))
    heights = [(p, ground.under(p)) for p in field]
    over = sorted(abs(p[1] - g[0]) for p, g in heights if g is not None)
    print(f"3. the open field: {files} cells' walkable collision under {len(field):,} enemy placements")
    print(f"   {tris:,} triangles; the stored normal is the corners' own for {agree:,} with part-relative vertex numbers "
          f"({100.0 * agree / max(tris, 1):.2f}%), for {agree_filewide:,} with file-wide ones "
          f"({100.0 * agree_filewide / max(tris, 1):.2f}%)")
    print(f"   ground under {len(over):,} of {len(field):,} placements; within {multiply.OFF_GROUND:g} cm of it: "
          f"{sum(1 for d in over if d <= multiply.OFF_GROUND):,}; half within {_quantile(over, 0.5):.0f} cm")
    return agree > 0.99 * tris and agree_filewide < 0.9 * tris and len(over) > 0.9 * len(field)


def whole_game(game, idx, w, factor: int, key: str, **kw) -> bool:
    LOT = typemap.BY_EXT["lot"]
    t0 = time.time()
    p = multiply.plan(game, idx, w, factor, **kw)
    print(f"4. x{factor}{' with the big monsters' if kw.get('bosses') else ''}, planned in {time.time() - t0:.1f} s")
    for line in p.summary():
        print("   " + line.strip())
    ok = _said("counts", {k: p.counts.get(k, 0) for k in EXPECT[key]}, key)
    if key + " files" in EXPECT:
        ok = _said("files, stages", (len(p.files), sum(1 for v in p.stages.values() if v[1])), key + " files") and ok
    ids, past = defaultdict(list), 0
    changed = {n for n, (tid, _d) in p.files.items() if tid == LOT}
    for name, lay in w.layouts.items():
        if lay["type"] != "e":
            continue
        group = (lay["stage"], lay["number"])
        if name in changed:
            got = [r.id for r in lot.parse(p.files[name][1]).records]
        else:
            got = [r.id for r in lot.parse(modfiles.load(game, idx, None, name.encode("latin-1"), LOT)[0]).records]
        ids[group] += got
        past += sum(1 for i in got if i >= lot.KILL_BITS)
    twice = sum(len(v) - len(set(v)) for v in ids.values())
    print(f"   every group as multiplied: ids past 32: {past}; ids used twice: {twice}")
    ok = ok and past == 0 and twice == 0
    if key + " archives" in EXPECT:
        arcs, size = multiply.footprint(game, idx, p)
        new = sum(len(d) for _t, d in p.files.values())
        print(f"5. installed: {size / 1e6:,.0f} MB of the game's archives rebuilt in the overlay "
              f"({new / 1e6:.1f} MB of them the changed layouts and group lists)")
        sizes = {}
        for name, (tid, _data) in p.files.items():
            for a in idx.archives_with(name.encode("latin-1"), tid):
                sizes[a] = Path(game.vanilla_arc(a)).stat().st_size
        by: Counter = Counter()
        for a, n in sizes.items():
            parts = a.replace("\\", "/").split("/")
            field = "stage100" in parts
            by["/".join(parts[:4 if field and len(parts) > 4 else 3 if field else 2]) + ("" if field else "/...")] += n
        for k, n in sorted(by.items(), key=lambda kv: -kv[1]):
            print(f"      {n / 1e6:>8,.1f} MB  {k}")
        ok = _said("archives", arcs, key + " archives") and ok
    return ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--quick", action="store_true", help="x2 only")
    args = ap.parse_args(argv)
    try:
        game = find_game("ddda")
    except RiftError as e:
        print(f"no Dark Arisen to measure ({e})")
        return 0
    idx = Index(game)
    idx.refresh(None)
    w = world.load(game, idx)
    try:
        ok = layouts(game, idx, w)
        said, field = placements(game, idx, w)
        ok = said and ok
        ok = field_ground(game, idx, field) and ok
        ok = whole_game(game, idx, w, 2, "x2") and ok
        if not args.quick:
            ok = whole_game(game, idx, w, 3, "x3") and ok
            ok = whole_game(game, idx, w, 2, "x2 bosses", bosses=True) and ok
    finally:
        idx.close()
    print("as documented" if ok else "NOT as documented")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

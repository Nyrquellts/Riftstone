"""Which way a placement faces, and how far apart the game stands its enemies, measured again from the game
(src/riftstone/arrange.py, docs/arrange.md).

    python tools/facing_proof.py

Dark Arisen, each archive as shipped (reads only).  Three measurements:

  1. the rest pose of the Arisen's body (model\\pl\\m\\m_base\\m000\\m000): its lowest joints (the toes) stand
     ahead of their parents (the ankles) along +Z, so a model faces +Z in its own space;
  2. every enemy and NPC layout with three placements or more: the angle between each placement's mAngle y and
     the way to the layout's middle, under four readings of a heading (atan2(dx, dz), the mirrored atan2(-dx, dz),
     the swapped atan2(dz, dx) and atan2(-dz, dx)).  A model facing +Z leaves the two that give +Z heading 0; the
     game's is the one whose angles bunch (the mean resultant length), and where they bunch says that the game's
     groups lean toward their middle; the share within 30 degrees of it against 60/360 by chance;
  3. each enemy's spacing: the median distance (x, z) from one of its placements to the nearest other of the same
     enemy in the same layout (pairs closer than 30 cm left out), over the enemies with three such distances;
  4. the game's own stacks (placements it stands together itself, arrange.stacks) in every layout, and that
     arranging every layout as the game ships it moves none of them (arrange.Arranger.games_own).

Exit 0 when the measurements come out as documented; 0 with a note when no game is installed.
"""
from __future__ import annotations

import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from riftstone import arrange, lot, modfiles, retarget, typemap, world  # noqa: E402
from riftstone.errors import RiftError  # noqa: E402
from riftstone.game import find_game  # noqa: E402
from riftstone.index import Index  # noqa: E402

BODY = "model\\pl\\m\\m_base\\m000\\m000"
READINGS = {
    "atan2(dx, dz)": lambda dx, dz: math.atan2(dx, dz),
    "atan2(-dx, dz)": lambda dx, dz: math.atan2(-dx, dz),
    "atan2(dz, dx)": lambda dx, dz: math.atan2(dz, dx),
    "atan2(-dz, dx)": lambda dx, dz: math.atan2(-dz, dx),
}
NEAR = 100.0            # cm: a placement this close to its layout's middle has no way to it


def toes(game, idx) -> list[tuple]:
    """(joint, parent, dx, dy, dz) of the six lowest joints with a parent, each from its parent, in the rest pose."""
    data, _ = modfiles.load(game, idx, None, BODY.encode("latin-1"), typemap.BY_EXT["mod"])
    b = retarget.body(data)
    wp: dict = {}
    for j in b.order:
        jt = b.joints[j]
        m = (jt.rotation, jt.position)
        wp[j] = retarget.compose(wp[jt.parent], m) if jt.parent is not None and jt.parent in wp else m
    pos = {j: m[1] for j, m in wp.items()}
    low = sorted((p[1], j) for j, p in pos.items() if b.joints[j].parent is not None)[:6]
    rows = []
    for _, j in low:
        par = b.joints[j].parent
        d = tuple(a - c for a, c in zip(pos[j], pos[par]))
        if math.hypot(d[0], d[2]) > 1.0:
            rows.append((j, par, d[0], d[1], d[2]))
    return rows


def headings(game, idx, w) -> tuple[int, dict, float]:
    """(layouts, reading -> (placements, resultant, mean degrees), share within 30 degrees under the game's)."""
    LOT = typemap.BY_EXT["lot"]
    sums = {k: [0.0, 0.0, 0] for k in READINGS}
    near = layouts = 0
    for name, lay in sorted(w.layouts.items()):
        if lay["type"] not in ("e", "n") or lay["records"] < 3:
            continue
        data, _ = modfiles.load(game, idx, None, name.encode("latin-1"), LOT)
        recs = [r for r in lot.parse(data).records if "mPosition" in r.fields and "mAngle" in r.fields]
        if len(recs) < 3:
            continue
        layouts += 1
        cx = sum(r.vec()[0] for r in recs) / len(recs)
        cz = sum(r.vec()[2] for r in recs) / len(recs)
        for r in recs:
            x, _, z = r.vec()
            dx, dz = cx - x, cz - z
            if math.hypot(dx, dz) < NEAR:
                continue
            y = r.vec("mAngle")[1]
            for k, f in READINGS.items():
                d = (y - f(dx, dz) + math.pi) % (2 * math.pi) - math.pi
                s = sums[k]
                s[0] += math.cos(d)
                s[1] += math.sin(d)
                s[2] += 1
                if k == "atan2(dx, dz)" and abs(d) <= math.radians(30.0):
                    near += 1
    out = {k: (n, math.hypot(c, s) / n, math.degrees(math.atan2(s, c))) for k, (c, s, n) in sums.items() if n}
    return layouts, out, near / out["atan2(dx, dz)"][0]


def own_stacks(game, idx, w) -> tuple[int, int, int, int, int]:
    """(layouts, layouts with a stack, stacks, placements in them, placements arrange moved in the game as shipped)."""
    LOT = typemap.BY_EXT["lot"]
    ar = arrange.Arranger(game, idx, w)
    layouts = holding = count = placed = moved = 0
    for name in sorted(w.layouts):
        data, _ = modfiles.load(game, idx, None, name.encode("latin-1"), LOT)
        found = arrange.stacks(lot.parse(data).records)
        layouts += 1
        if found:
            holding += 1
            count += len(found)
            placed += sum(len(g) for g in found)
            moved += ar.layout(data, name).moved
    return layouts, holding, count, placed, moved


def main() -> int:
    try:
        game = find_game("ddda")
    except RiftError as e:
        print(f"no Dark Arisen here ({e}); nothing measured")
        return 0
    idx = Index(game)
    try:
        w = world.load(game, idx)
        ok = True
        rows = toes(game, idx)
        print(f"1. the Arisen's lowest joints from their parents, rest pose ({BODY}):")
        for j, par, dx, dy, dz in rows:
            print(f"   joint {j:>3} from {par:>3}: dx {dx:7.1f}  dy {dy:7.1f}  dz {dz:7.1f} cm")
        ahead = [r for r in rows if abs(r[4]) > abs(r[2])]
        ok &= bool(ahead) and all(r[4] > 0 for r in ahead)
        print(f"   {'every' if ok else 'NOT every'} toe ahead along +Z: a model faces +Z")
        layouts, got, share = headings(game, idx, w)
        print(f"2. mAngle y against the way to the layout's middle, {layouts:,} enemy and NPC layouts of 3 or more:")
        for k, (n, res, mean) in got.items():
            print(f"   {k:<15} {n:,} placements, mean resultant {res:.3f}, bunched at {mean:6.1f} degrees")
        # a model facing +Z faces heading 0 at y = 0: the readings that give 0 for +Z are the two candidates
        # (the swapped ones give +Z a quarter turn); of them the game's bunches, the mirrored one does not
        plus_z = [k for k in got if abs(READINGS[k](0.0, 1.0)) < 1e-9]
        best = max(plus_z, key=lambda k: got[k][1])
        game_res, game_mean = got["atan2(dx, dz)"][1:]
        ok &= best == "atan2(dx, dz)" and abs(game_mean) < 30.0
        ok &= all(got[k][1] < game_res / 4.0 for k in plus_z if k != best)
        turned = got["atan2(-dz, dx)"]
        print(f"   atan2(-dz, dx) is atan2(dx, dz) a quarter turn on: it bunches as much, at {turned[2]:.0f} degrees "
              "(side-on), which a model facing +Z rules out")
        print(f"   the game's reading: {best}; {share:.0%} face within 30 degrees of the middle "
              f"({60 / 360:.0%} by chance)")
        table = arrange.spacing(w)
        vals = sorted(table.values())
        print(f"3. spacing: {len(table)} enemies, median of their medians {statistics.median(vals):.0f} cm "
              f"(least {vals[0]:.0f}, most {vals[-1]:.0f}); goblins em0100 {table.get('em0100', 0):.0f} cm")
        ok &= bool(vals)
        layouts, holding, count, placed, moved = own_stacks(game, idx, w)
        print(f"4. the game's own stacks: {count} of {placed} placements in {holding} of its {layouts:,} layouts; "
              f"arranging every layout as shipped moves {moved}")
        ok &= count > 0 and moved == 0
        print("as documented" if ok else "NOT as documented")
        return 0 if ok else 1
    finally:
        idx.close()


if __name__ == "__main__":
    raise SystemExit(main())

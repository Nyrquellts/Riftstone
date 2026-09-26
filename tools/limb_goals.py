"""Measure what Online's wrist and ankle position tracks are: goals in the model's own space.

Online's player motions often key no rotation for the elbows and knees (joints 7, 11, 15, 19) and a
position for the wrists and ankles (8, 12, 16, 20). Read as local offsets (how a position track is read
everywhere else) those positions should keep the bone's own length; read as goals in the model's space
they must stay within reach of the limb, measured from the shoulder or hip the motion places. This
measures both readings on every player motion list of Online (``obj\\pl\\pl000000\\motion``), every
third frame, on Online's own body (``obj\\pl\\pl000000\\model\\pl000000_00``), and prints JSON.

    python tools\\limb_goals.py [--game PATH]

Needs Online installed (``find_game('ddo')``); reads only.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from riftstone import compat_pack, lmt, retarget, typemap  # noqa: E402
from riftstone.game import find_game  # noqa: E402
from riftstone.index import Index  # noqa: E402

BODY = "obj\\pl\\pl000000\\model\\pl000000_00"
CHAINS = {"left arm": (6, 7, 8), "right arm": (10, 11, 12), "left leg": (14, 15, 16), "right leg": (18, 19, 20)}
STEP = 3            # every third frame
SLACK = 1.0         # cm: a goal this far past full reach still counts as reachable


def measure(game: str | None = None) -> dict:
    t0 = time.time()
    g = find_game(game or "ddo")
    idx = Index(g)
    src = compat_pack.Source(g, idx)
    body = retarget.body(src.read(BODY, "mod"))
    lengths = {k: (math.dist(body.joints[m].position, (0, 0, 0)), math.dist(body.joints[e].position, (0, 0, 0)))
               for k, (_u, m, e) in CHAINS.items()}
    rows = idx.search("pl000000", typemap.BY_EXT["lmt"], limit=100000)
    names = sorted({r["name"] for r in rows if b"\\motion\\" in r["name"]})
    out = {k: {"reach_cm": round(sum(lengths[k]), 2), "frames": 0, "model_space_fits": 0, "local_offset_fits": 0,
               "track_lists_with_goal": 0, "worst_model_space_x_reach": 0.0} for k in CHAINS}
    mid_keyed = {str(m): 0 for (_u, m, _e) in CHAINS.values()}
    lists = track_lists = 0
    for name in names:
        ml = lmt.parse(src.read(name.decode("ascii"), "lmt"))
        lists += 1
        seen: set[int] = set()
        for mo in ml.motions:
            if mo is None or mo.tracks is None or id(mo.tracks) in seen:
                continue
            seen.add(id(mo.tracks))
            track_lists += 1
            tracks = mo.tracks.tracks
            goals = {t.bone: t for t in tracks if t.usage == retarget.LOCAL_POSITION}
            rotated = {t.bone for t in tracks if t.usage == retarget.LOCAL_ROTATION}
            for (_u, m, _e) in CHAINS.values():
                mid_keyed[str(m)] += m in rotated
            times = list(range(0, max(1, mo.frames), STEP))
            fk = None
            for k, (u, _m, e) in CHAINS.items():
                if e not in goals:
                    continue
                if fk is None:
                    fk = retarget.pose(body, tracks, times, [u for (u, _m, _e) in CHAINS.values()])
                o = out[k]
                o["track_lists_with_goal"] += 1
                reach = sum(lengths[k])
                for f, gv in enumerate(retarget.sample(retarget.keys_of(goals[e]), times, False)):
                    d = math.dist(gv, fk[u][f][1])
                    o["frames"] += 1
                    o["model_space_fits"] += d <= reach + SLACK
                    o["local_offset_fits"] += abs(math.dist(gv, (0, 0, 0)) - lengths[k][1]) <= SLACK
                    o["worst_model_space_x_reach"] = max(o["worst_model_space_x_reach"], d / reach)
    for o in out.values():
        n = o["frames"] or 1
        o["model_space_fits_pct"] = round(100 * o.pop("model_space_fits") / n, 2)
        o["local_offset_fits_pct"] = round(100 * o.pop("local_offset_fits") / n, 2)
        o["worst_model_space_x_reach"] = round(o["worst_model_space_x_reach"], 3)
    return {"motion_lists": lists, "track_lists": track_lists, "chains": out,
            "track_lists_keying_the_mid_joint": mid_keyed, "seconds": round(time.time() - t0, 1),
            "claim": "Online's wrist and ankle position tracks are goals in the model's space (within the limb's "
                     "reach from the shoulder or hip the motion places), not local offsets; so Online bends the "
                     "elbows and knees itself (static, on the data; its solver UNKNOWN)"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--game", help="Online's folder (default: found automatically)")
    a = ap.parse_args(argv)
    print(json.dumps(measure(a.game), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

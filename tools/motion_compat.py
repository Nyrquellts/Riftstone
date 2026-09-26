"""How well Dragon's Dogma Online's player motions fit Dark Arisen's player skeleton, joint by joint.

    python tools/motion_compat.py [--out report.json] [--tolerance 0.5]

Motion tracks address joints by id (lmt.py). For every DDO player motion list
(``obj\\pl\\pl000000\\motion\\m00NN\\...``: NN = the vocation, 00 = the shared human set) this counts
each track by what the same joint id is in DDDA's player body (``model\\pl\\m\\m_base\\m000\\m000``)
compared with DDO's (``obj\\pl\\pl000000\\model\\pl000000_00``):

  same        same parent joint, offset from the parent within --tolerance (cm)
  offset      same parent, different offset (rest pose / bone length differs)
  reparented  a different parent joint
  absent      DDDA's body has no such joint
  root        joint 255: the motion's root / scene track (not a body joint)

Rotation tracks on "offset" joints still bend the right bone; position tracks there would move the
joint to DDO's rest offset. It also counts the joints DDO's player hit shapes attach to (``mJnt0`` /
``mJnt1`` of every ``obj\\pl\\pl000000\\collision\\...`` file) by the same classes. Reads both games only;
writes nothing but the report.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from riftstone import arc, corpus, lmt, ocl_ddo, port, typemap  # noqa: E402
from riftstone.game import find_game  # noqa: E402

DDO_BODY = b"obj\\pl\\pl000000\\model\\pl000000_00"
DDDA_BODY = b"model\\pl\\m\\m_base\\m000\\m000"
MOTIONS = re.compile(rb"^obj\\pl\\pl000000\\motion\\m00(\d\d)")
ROTATION = (0, 3)


def model(game, name: bytes) -> bytes:
    tid = typemap.BY_EXT["mod"]
    for path in game.archives():
        for n, t, *_ in corpus.directory(path):
            if n == name and t == tid:
                return arc.Archive.read(path).find(name, tid).data()
    raise SystemExit(f"{name.decode()} not found in {game.title}")


def classify(src: dict, dst: dict, tolerance: float) -> dict[int, str]:
    out = {}
    for j, a in src.items():
        b = dst.get(j)
        if b is None:
            out[j] = "absent"
        elif a.parent != b.parent:
            out[j] = "reparented"
        elif max(abs(p - q) for p, q in zip(a.offset, b.offset)) > tolerance:
            out[j] = "offset"
        else:
            out[j] = "same"
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out")
    ap.add_argument("--tolerance", type=float, default=0.5)
    a = ap.parse_args()
    ddo, ddda = find_game("ddo"), find_game("ddda")
    src_sk = port.skeleton(model(ddo, DDO_BODY))
    dst_sk = port.skeleton(model(ddda, DDDA_BODY))
    kinds = classify(src_sk, dst_sk, a.tolerance)
    per_job: dict[str, Counter] = defaultdict(Counter)
    joints_used: dict[str, set] = defaultdict(set)
    files: dict[str, int] = Counter()
    seen: set[bytes] = set()
    for r in corpus.resources(ddo, [typemap.BY_EXT["lmt"]]):
        m = MOTIONS.match(r.name)
        if not m or r.name in seen:
            continue
        seen.add(r.name)
        job = "shared" if m.group(1) == b"00" else f"job{m.group(1).decode()}"
        files[job] += 1
        ml = lmt.parse(r.data)
        done = set()
        for mo in ml.motions:
            if mo is None or id(mo.tracks) in done:
                continue
            done.add(id(mo.tracks))
            for t in mo.tracks.tracks:
                kind = "root" if t.bone == 255 else kinds.get(t.bone, "absent_in_ddo_body")
                per_job[job][(kind, "rotation" if t.usage in ROTATION else "other")] += 1
                if t.bone != 255:
                    joints_used[job].add(t.bone)
    # hitboxes: the player collision files' hit shapes attach to joints (mJnt0 / mJnt1, -1 = none)
    hit_jobs: dict[str, Counter] = defaultdict(Counter)
    hit_seen: set[bytes] = set()
    for r in corpus.resources(ddo, [typemap.BY_EXT["ocl"]]):
        m = re.match(rb"^obj\\pl\\pl000000\\collision\\(job(\d\d)|[^\\]+)", r.name)
        if not m or r.name in hit_seen:
            continue
        hit_seen.add(r.name)
        job = f"job{m.group(2).decode()}" if m.group(2) else "shared"
        for n in ocl_ddo.parse(r.data).nodes:
            for g in n["geoms"]:
                for j in (g["mJnt0"], g["mJnt1"]):
                    if j < 0:
                        continue
                    kind = kinds.get(j, "absent_in_ddo_body") if j in src_sk else "not_a_ddo_joint_id"
                    hit_jobs[job][kind] += 1
    all_used = set().union(*joints_used.values()) if joints_used else set()
    report = {
        "schema": "riftstone.motion-compat/1", "tolerance_cm": a.tolerance,
        "ddo_body": DDO_BODY.decode(), "ddda_body": DDDA_BODY.decode(),
        "ddo_joints": len(src_sk), "ddda_joints": len(dst_sk),
        "joints_driven_by_ddo_player_motions": len(all_used),
        "driven_joints_by_kind": dict(Counter(kinds.get(j, "absent_in_ddo_body") for j in all_used)),
        "driven_joints": {k: sorted(j for j in all_used if kinds.get(j, "absent_in_ddo_body") == k)
                          for k in ("same", "offset", "reparented", "absent", "absent_in_ddo_body")},
        "jobs": {job: {"motion_lists": files[job],
                       "tracks": {f"{k}/{u}": n for (k, u), n in sorted(c.items())},
                       "tracks_total": sum(c.values()),
                       "tracks_on_same_or_root": sum(n for (k, _), n in c.items() if k in ("same", "root")),
                       "rotation_tracks_on_offset_joints": c[("offset", "rotation")]}
                 for job, c in sorted(per_job.items())},
        "hit_shape_joints": {job: dict(c) for job, c in sorted(hit_jobs.items())},
    }
    text = json.dumps(report, indent=1)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    print(f"DDO body {len(src_sk)} joints, DDDA body {len(dst_sk)} joints; DDO player motions drive "
          f"{len(all_used)} joints: {report['driven_joints_by_kind']}")
    for k, js in report["driven_joints"].items():
        if js:
            print(f"  {k:18} {js}")
    print(f"{'job':8} {'lists':>5} {'tracks':>7} {'same+root':>9} {'%':>6}  offset(rot/other) reparented absent")
    for job, j in report["jobs"].items():
        t = j["tracks"]
        pct = 100 * j["tracks_on_same_or_root"] / max(1, j["tracks_total"])
        print(f"{job:8} {j['motion_lists']:>5} {j['tracks_total']:>7} {j['tracks_on_same_or_root']:>9} {pct:>5.1f}%  "
              f"{t.get('offset/rotation', 0):>6}/{t.get('offset/other', 0):<6} "
              f"{t.get('reparented/rotation', 0) + t.get('reparented/other', 0):>10} "
              f"{t.get('absent/rotation', 0) + t.get('absent/other', 0) + t.get('absent_in_ddo_body/rotation', 0) + t.get('absent_in_ddo_body/other', 0):>6}")
    print("\nhit shapes (player collision, mJnt0/mJnt1) by what the joint is in DDDA's body:")
    for job, c in report["hit_shape_joints"].items():
        tot = sum(c.values())
        print(f"  {job:8} {tot:>6} joint refs: " + ", ".join(f"{k} {v}" for k, v in sorted(c.items()))
              + f"  ({100 * c.get('same', 0) / max(1, tot):.1f}% same)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

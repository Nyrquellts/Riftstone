"""Prove the weapon-joint rebake (``riftstone.retarget``) on every Dragon's Dogma Online player motion list.

    python tools/retarget_check.py [--out report.json] [--only m0001] [--skip-conventions]

For each DDO player motion list (``obj\\pl\\pl000000\\motion\\m00NN\\...``, every name once; NN = the
vocation, 00 = the shared human set) and every motion and frame, the world transforms of the joints the
rebake moves -- evaluated on Dark Arisen's body (``model\\pl\\m\\m_base\\m000\\m000``) after
``retarget.rebake`` and ``port.convert_lmt(..., "ddo", "ddda")``, decoded back -- against the same
joints on DDO's body (``obj\\pl\\pl000000\\model\\pl000000_00``) under the original motion: largest and
99th-percentile position error (cm) and angle error (degrees). The same numbers without the rebake
(``convert_lmt`` alone, today's port) for contrast. Then the hit shapes: every shape of a vocation's
collision files (``obj\\pl\\pl000000\\collision\\jobNN\\...``) that hangs on a weapon joint, its attach
points (the joint's world transform applied to mOffset0 / mOffset1) over every frame of that vocation's
motions, with and without the rebake.

Also re-measured here: the conventions the FK rests on (every model's local matrices against its
inverse binds, the quaternion convention against rest rotations, which codecs either game keys), the
sensitivity to what stays UNKNOWN (rotation interpolation, fractional frames, whether a parent's scale
reaches its children), every rebaked file parsing and rebuilding byte for byte, and file sizes.

Reads both games only; writes nothing but the JSON report (--out).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import struct
import sys
import time
from array import array
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from riftstone import corpus, lmt, ocl_ddo, port, retarget, typemap  # noqa: E402
from riftstone.game import find_game  # noqa: E402

DDO_BODY = b"obj\\pl\\pl000000\\model\\pl000000_00"
DDDA_BODY = b"model\\pl\\m\\m_base\\m000\\m000"
MOTIONS = re.compile(rb"^obj\\pl\\pl000000\\motion\\m00(\d\d)\\")
COLLISION = re.compile(rb"^obj\\pl\\pl000000\\collision\\job(\d\d)\\")
DDDA_MOTIONS = b"motion\\pl\\m\\m00\\"
WEAPON = (150, 151, 152, 153, 154)
WATCH = WEAPON + (55,)
VOCATIONS = {"00": "shared", "01": "Fighter", "02": "Seeker", "03": "Hunter", "04": "Priest",
             "05": "Shield Sage", "06": "Sorcerer", "07": "Warrior", "08": "Element Archer",
             "09": "Alchemist", "10": "Spirit Lancer", "11": "High Scepter"}


# -- statistics ----------------------------------------------------------------------------------------
class Stat:
    """Samples of one error measure: count, largest (with where), 99th percentile."""

    def __init__(self):
        self.v = array("d")
        self.worst = (0.0, None)

    def add(self, x: float, where=None) -> None:
        self.v.append(x)
        if x > self.worst[0]:
            self.worst = (x, where)

    def merge(self, o: "Stat") -> None:
        self.v.extend(o.v)
        if o.worst[0] > self.worst[0]:
            self.worst = o.worst

    def out(self, digits: int = 6) -> dict:
        if not self.v:
            return {"samples": 0}
        s = sorted(self.v)
        p99 = s[min(len(s) - 1, math.ceil(0.99 * len(s)) - 1)]
        d = {"samples": len(s), "max": round(s[-1], digits), "p99": round(p99, digits),
             "median": round(s[len(s) // 2], digits)}
        if self.worst[1] is not None:
            d["max_at"] = self.worst[1]
        return d


def dist(a, b) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def _bucket(step: float) -> str:
    """How far the point moves (DDO, world) between the two frames around a half frame."""
    return "step<=10cm" if step <= 10 else "step<=50cm" if step <= 50 else "step>50cm"


# -- a full-matrix FK (a parent's scale reaches its children) for the sensitivity check ------------------
def affine_pose(b: retarget.Body, tracks, times, joints):
    """joint -> [(3x3 rows, translation)] per time, with world = S R T x parent world (whole matrices)."""
    cv = retarget.curves(tracks, times)
    n = len(times)
    out = {}
    for j in b.chain(joints):
        jt = b.joints[j]
        c = cv.get(j, {})
        rots = c.get(retarget.LOCAL_ROTATION) or [jt.rotation] * n
        poss = c.get(retarget.LOCAL_POSITION) or [jt.position] * n
        scs = c.get(retarget.LOCAL_SCALE) or [jt.scale] * n
        par = out.get(jt.parent) if jt.parent is not None else None
        row = []
        for f in range(n):
            r = retarget.rows_from_quat(retarget.qnorm(rots[f]))
            s = scs[f]
            m = tuple(tuple(x * s[i] for x in r[i]) for i in range(3))
            t = tuple(poss[f])
            if par is not None:
                pm, pt = par[f]
                m = tuple(tuple(sum(m[i][k] * pm[k][c] for k in range(3)) for c in range(3)) for i in range(3))
                t = tuple(sum(t[k] * pm[k][c] for k in range(3)) + pt[c] for c in range(3))
            row.append((m, t))
        out[j] = row
    return out


def affine_point(mt, off):
    m, t = mt
    return tuple(sum(off[k] * m[k][c] for k in range(3)) + t[c] for c in range(3))


# -- reading the games ---------------------------------------------------------------------------------
def entries(game, tid: int, want):
    """(name, data) of every entry of type tid whose name `want` accepts, decoding only those."""
    for path in game.archives():
        rows = [r for r in corpus.directory(path) if r[1] == tid and want(r[0])]
        if not rows:
            continue
        enc = corpus.encrypted(path)
        with open(path, "rb") as fh:
            for name, _tid, zs, _size, off in rows:
                fh.seek(off)
                yield name, corpus.payload(fh.read(zs), enc)


def model(game, name: bytes) -> bytes:
    for _n, data in entries(game, typemap.BY_EXT["mod"], lambda n: n == name):
        return data
    raise SystemExit(f"{name.decode()} not found in {game.title}")


def player_lists(ddo, only: str | None):
    """{name: data} for every DDO player motion list, each name once."""
    out = {}
    for name, data in entries(ddo, typemap.BY_EXT["lmt"], lambda n: MOTIONS.match(n) is not None):
        m = MOTIONS.match(name)
        if name not in out and (not only or f"m00{m.group(1).decode()}" == only):
            out[name] = data
    return dict(sorted(out.items()))


def hit_points(ddo):
    """vocation 'NN' -> {(joint, offset xyz): refs} for hit shapes on weapon joints, and per-vocation counts."""
    points: dict[str, Counter] = defaultdict(Counter)
    shapes: dict[str, Counter] = defaultdict(Counter)
    seen = set()
    for name, data in entries(ddo, typemap.BY_EXT["ocl"], lambda n: COLLISION.match(n) is not None):
        m = COLLISION.match(name)
        if name in seen:
            continue
        seen.add(name)
        job = m.group(1).decode()
        shapes[job]["files"] += 1
        for n in ocl_ddo.parse(data).nodes:
            for g in n["geoms"]:
                shapes[job]["shapes"] += 1
                for jk, ok in (("mJnt0", "mOffset0"), ("mJnt1", "mOffset1")):
                    j = g[jk]
                    if j in WATCH:
                        off = tuple(ocl_ddo.f32(b) for b in g[ok])
                        points[job][(j, off)] += 1
                        shapes[job][f"refs on joint {j} ({ocl_ddo.SHAPES.get(g['mShape'], g['mShape'])})"] += 1
    return points, shapes


# -- conventions -----------------------------------------------------------------------------------------
def conventions(ddo, ddda) -> dict:
    out = {}
    mod_t, lmt_t = typemap.BY_EXT["mod"], typemap.BY_EXT["lmt"]
    for kind, g in (("ddda", ddda), ("ddo", ddo)):
        t0 = time.time()
        c = Counter()
        worst = {"child_x_parent": [0.0, 0.0]}
        for r in corpus.resources(g, [mod_t]):
            try:
                b = retarget.body(r.data)
            except Exception:
                c["models without a usable bone section (no bones or another layout)"] += 1
                continue
            k = retarget.check_body(b)
            a = k["child_x_parent"]
            p = k["parent_x_child"]
            fit_a = a["rotation"] < 1e-3 and a["translation"] < 0.01
            fit_b = p["rotation"] < 1e-3 and p["translation"] < 0.01
            rot = "with a rotated rest" if k["rotated_rest"] else "without a rotated rest"
            c[f"models {rot}: child x parent {'fits' if fit_a else 'FAILS'}, "
              f"parent x child {'fits' if fit_b else 'fails'}"] += 1
            worst["child_x_parent"][0] = max(worst["child_x_parent"][0], a["rotation"])
            worst["child_x_parent"][1] = max(worst["child_x_parent"][1], a["translation"])
        out[f"{kind}_matrices"] = {"counts": dict(sorted(c.items())),
                                   "child_x_parent_max_error": {"rotation": worst["child_x_parent"][0],
                                                                "translation": worst["child_x_parent"][1]},
                                   "seconds": round(time.time() - t0)}
        # quaternion convention: constant rotation tracks vs the rest rotation of the same joint in a model of
        # the same archive (each distinct motion list / model pair once); codecs: each distinct motion list once
        t0 = time.time()
        q = Counter()
        codecs = Counter()
        seen_lmt: set[bytes] = set()
        seen_pair: set[tuple[bytes, bytes]] = set()
        for path in g.archives():
            rows = corpus.directory(path)
            if not any(x[1] == lmt_t for x in rows):
                continue
            enc = corpus.encrypted(path)
            mods, lmts = [], []
            with open(path, "rb") as fh:
                for name, tid, zs, size, off in rows:
                    if tid in (mod_t, lmt_t):
                        fh.seek(off)
                        (mods if tid == mod_t else lmts).append(corpus.payload(fh.read(zs), enc))
            rests = []
            for d in mods:
                try:
                    b = retarget.body(d)
                except Exception:
                    continue
                rest = {"_sha": hashlib.sha1(d).digest()}
                for j, jt in b.joints.items():
                    m = b.local[j]
                    rows = [m[0:3], m[4:7], m[8:11]]
                    lens = [math.sqrt(sum(x * x for x in r)) for r in rows]
                    if min(lens) > 1e-6 and retarget.angle(jt.rotation, retarget.IDENTITY) > 0.05:
                        rest[j] = tuple(tuple(x / n for x in r) for r, n in zip(rows, lens))
                rests.append(rest)
            for d in lmts:
                ml = lmt.parse(d)
                sha = hashlib.sha1(d).digest()
                first = sha not in seen_lmt
                seen_lmt.add(sha)
                pairs = [r for r in rests if (sha, r["_sha"]) not in seen_pair]
                seen_pair.update((sha, r["_sha"]) for r in pairs)
                done = set()
                for mo in ml.motions:
                    if mo is None or id(mo.tracks) in done:
                        continue
                    done.add(id(mo.tracks))
                    for t in mo.tracks.tracks:
                        if first:
                            codecs[(t.codec, "keyed" if t.buffer is not None else "constant",
                                    "extremes" if t.extremes is not None else "no extremes")] += 1
                        if t.usage != 0 or t.buffer is not None or not pairs:
                            continue
                        v = struct.unpack("<4f", t.reference)
                        if not any(v):
                            continue
                        qq = retarget.qnorm(v)
                        h = retarget.rows_from_quat(qq)                            # H(q)^T
                        ht = tuple(tuple(h[c][r] for c in range(3)) for r in range(3))  # H(q)
                        if max(abs(h[r][c] - ht[r][c]) for r in range(3) for c in range(3)) < 1e-3:
                            q["undecidable (H(q) symmetric)"] += 1
                            continue
                        for rest in pairs:
                            want = rest.get(t.bone)
                            if want is None:
                                continue
                            e_t = max(abs(h[r][c] - want[r][c]) for r in range(3) for c in range(3))
                            e_h = max(abs(ht[r][c] - want[r][c]) for r in range(3) for c in range(3))
                            if e_t < 1e-3 and e_h >= 1e-3:
                                q["rest 3x3 == H(q)^T"] += 1
                            elif e_h < 1e-3 and e_t >= 1e-3:
                                q["rest 3x3 == H(q)"] += 1
                            elif retarget.angle(qq, retarget.IDENTITY) < 0.05:
                                q["identity (a track relative to the rest would be)"] += 1
                            else:
                                q["neither (another pose)"] += 1
        out[f"{kind}_quaternions"] = {"constant_rotation_tracks_on_rotated_rest_joints": dict(sorted(q.items())),
                                      "seconds": round(time.time() - t0)}
        out[f"{kind}_codecs_distinct_lists"] = {f"codec {c} {k}, {e}": n for (c, k, e), n in sorted(codecs.items())}
        out[f"{kind}_distinct_motion_lists"] = len(seen_lmt)
    return out


# -- one motion list -------------------------------------------------------------------------------------
def check_list(name: bytes, data: bytes, src: retarget.Body, dst: retarget.Body, ddo_mod: bytes,
               ddda_mod: bytes, points: dict, acc: dict) -> dict:
    job = MOTIONS.match(name).group(1).decode()
    nm = name.decode()
    res = retarget.rebake_ex(data, "ddo", ddo_mod, ddda_mod, dst_game="ddda")
    info = {"bytes": len(data), "rebaked_bytes": len(res.data), "joints": res.joints,
            "skipped": sorted(res.skipped), "scaled_parents": res.scaled_parents}
    exact = lmt.build(lmt.parse(res.data)) == res.data
    with_conv = port.convert_lmt(res.data, "ddo", "ddda").data
    without_conv = port.convert_lmt(data, "ddo", "ddda").data
    exact_conv = lmt.build(lmt.parse(with_conv)) == with_conv
    info.update(rebuild_exact=exact, converted_rebuild_exact=exact_conv, converted_bytes=len(with_conv),
                converted_version=lmt.parse(with_conv).version)
    a, w, n = lmt.parse(data), lmt.parse(with_conv), lmt.parse(without_conv)
    pts = points.get(job, {})
    seen: set[int] = set()
    motions = 0
    for slot, mo in enumerate(a.motions):
        if mo is None:
            continue
        motions += 1
        if id(mo.tracks) in seen:
            continue
        seen.add(id(mo.tracks))
        keyed = {t.bone for t in mo.tracks.tracks if t.usage in (0, 1)}
        times = list(range(mo.frames))
        S = retarget.pose(src, mo.tracks.tracks, times, WATCH)
        DW = retarget.pose(dst, w.motions[slot].tracks.tracks, times, WATCH)
        DN = retarget.pose(dst, n.motions[slot].tracks.tracks, times, WATCH)
        rebaked = [j for j in WEAPON if j in res.by_slot.get(slot, ())]
        # keys reproduced exactly on key frames, for the tracks this check evaluates
        for t in mo.tracks.tracks:
            if t.bone in S and t.usage in (0, 1, 2) and t.buffer is not None:
                ks = retarget.keys_of(t)
                got = retarget.sample(ks, [f for f, _ in ks], t.usage == 0)
                acc["keys"]["checked"] += len(ks)
                acc["keys"]["differ"] += sum(tuple(v[:len(g)]) != g for (_, v), g in zip(ks, got))
        for j in WATCH:
            if j == 55 and j not in res.by_slot.get(slot, ()):
                cat = "joint55_keyed_not_rebaked" if j in keyed else "joint55_unkeyed"
            elif j in rebaked or j in res.by_slot.get(slot, ()):
                cat = "rebaked"
            elif j in keyed:
                cat = "keyed_not_rebaked"
            else:
                cat = "unkeyed"
            for f in times:
                where = (nm, slot, f, j)
                for label, D in (("with", DW), ("without", DN)):
                    (qs, ts, _), (qd, td, _) = S[j][f], D[j][f]
                    acc["joints"][(job, cat, label, "pos")].add(dist(ts, td), where)
                    acc["joints"][(job, cat, label, "ang")].add(retarget.angle(qs, qd), where)
                    if cat == "rebaked":
                        acc["per_joint"][(j, label, "pos")].add(dist(ts, td), where)
                        acc["per_joint"][(j, label, "ang")].add(retarget.angle(qs, qd), where)
            for (pj, off), _refs in pts.items():
                if pj != j:
                    continue
                kcat = "joint keyed" if j in keyed else "joint unkeyed"
                for f in times:
                    ps = retarget.attach(S[j][f][:2], S[j][f][2], off)
                    for label, D in (("with", DW), ("without", DN)):
                        pd = retarget.attach(D[j][f][:2], D[j][f][2], off)
                        acc["hits"][(job, kcat, label)].add(dist(ps, pd), (nm, slot, f, j, off))
        if not rebaked:
            continue
        # sensitivity: rotation interpolation, fractional frames, a parent's scale reaching children
        s2 = retarget.pose(src, mo.tracks.tracks, times, rebaked, "slerp")
        d2 = retarget.pose(dst, w.motions[slot].tracks.tracks, times, rebaked, "slerp")
        half = [f + 0.5 for f in times[:-1]]
        s3 = retarget.pose(src, mo.tracks.tracks, half, rebaked) if half else {}
        d3 = retarget.pose(dst, w.motions[slot].tracks.tracks, half, rebaked) if half else {}
        sa = affine_pose(src, mo.tracks.tracks, times, rebaked)
        da = affine_pose(dst, w.motions[slot].tracks.tracks, times, rebaked)
        dcv = retarget.curves(w.motions[slot].tracks.tracks, times)
        for j in rebaked:
            # does this motion scale a destination ancestor of j (where the scale model matters)?
            a_ = dst.joints[j].parent
            scaled = False
            while a_ is not None:
                sc = dcv.get(a_, {}).get(retarget.LOCAL_SCALE)
                if sc and any(abs(c - 1.0) > 1e-4 for s in sc for c in s):
                    scaled = True
                    break
                a_ = dst.joints[a_].parent
            if scaled:
                acc["scaled_motions"].add((nm, slot, j))
            for f in times:
                acc["sens"][("slerp", "pos")].add(dist(s2[j][f][1], d2[j][f][1]), (nm, slot, f, j))
                acc["sens"][("slerp", "ang")].add(retarget.angle(s2[j][f][0], d2[j][f][0]), (nm, slot, f, j))
                acc["sens"][("scale_inherits " + ("scaled-parent motions" if scaled else "other motions"), "pos")].add(
                    dist(sa[j][f][1], da[j][f][1]), (nm, slot, f, j))
            for f in range(len(half)):
                step = dist(S[j][f][1], S[j][f + 1][1])
                acc["sens"][(f"half_frames joint {j} {_bucket(step)}", "pos")].add(
                    dist(s3[j][f][1], d3[j][f][1]), (nm, slot, f + 0.5, j))
                acc["sens"][(f"half_frames joint {j} {_bucket(step)}", "ang")].add(
                    retarget.angle(s3[j][f][0], d3[j][f][0]))
            for (pj, off), _refs in pts.items():
                if pj == j:
                    for f in times:
                        acc["sens"][("scale_inherits hit points", "hit")].add(
                            dist(affine_point(sa[j][f], off), affine_point(da[j][f], off)), (nm, slot, f, j, off))
                    for f in range(len(half)):
                        p0 = retarget.attach(S[j][f][:2], S[j][f][2], off)
                        p1 = retarget.attach(S[j][f + 1][:2], S[j][f + 1][2], off)
                        acc["sens"][(f"half_frames {VOCATIONS[job]} {_bucket(dist(p0, p1))}", "hit")].add(
                            dist(retarget.attach(s3[j][f][:2], s3[j][f][2], off),
                                 retarget.attach(d3[j][f][:2], d3[j][f][2], off)), (nm, slot, f + 0.5, j, off))
    info["motions"] = motions
    info["track_lists"] = len(seen)
    return info


def reverse_check(ddda, ddo_mod: bytes, ddda_mod: bytes) -> dict:
    """The other direction: every DDDA player motion list (``motion\\pl\\m\\m00\\...``) rebaked onto DDO's body
    and converted to DDO, the rebaked joints against DDDA's own FK."""
    src, dst = retarget.body(ddda_mod), retarget.body(ddo_mod)
    stats: dict = defaultdict(Stat)
    lists = exact = 0
    size = [0, 0]
    named: dict[bytes, bytes] = {}
    for name, data in entries(ddda, typemap.BY_EXT["lmt"], lambda n: n.startswith(DDDA_MOTIONS)):
        named.setdefault(name, data)                 # each name once (the game repeats them across archives)
    for name, data in sorted(named.items()):
        lists += 1
        res = retarget.rebake_ex(data, "ddda", ddda_mod, ddo_mod, dst_game="ddo")
        exact += lmt.build(lmt.parse(res.data)) == res.data
        size[0] += len(data)
        size[1] += len(res.data)
        w = lmt.parse(port.convert_lmt(res.data, "ddda", "ddo").data)
        n = lmt.parse(port.convert_lmt(data, "ddda", "ddo").data)
        a = lmt.parse(data)
        seen: set[int] = set()
        for slot, mo in enumerate(a.motions):
            if mo is None or id(mo.tracks) in seen:
                continue
            seen.add(id(mo.tracks))
            js = res.by_slot.get(slot, [])
            if not js:
                continue
            times = list(range(mo.frames))
            S = retarget.pose(src, mo.tracks.tracks, times, js)
            for label, other in (("with", w), ("without", n)):
                D = retarget.pose(dst, other.motions[slot].tracks.tracks, times, js)
                for j in js:
                    for f in times:
                        stats[(label, "pos")].add(dist(S[j][f][1], D[j][f][1]), (name.decode(), slot, f, j))
                        stats[(label, "ang")].add(retarget.angle(S[j][f][0], D[j][f][0]), (name.decode(), slot, f, j))
    return {"lists": lists, "rebuild_exact": exact, "bytes": size[0], "rebaked_bytes": size[1],
            **{f"rebaked {k[0]} {'position_cm' if k[1] == 'pos' else 'angle_deg'}": v.out()
               for k, v in sorted(stats.items())}}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", help="write the JSON report here")
    ap.add_argument("--only", help="one folder, e.g. m0001")
    ap.add_argument("--skip-conventions", action="store_true")
    ap.add_argument("--reverse", action="store_true", help="also DDDA's player motion lists onto DDO's body")
    a = ap.parse_args()
    t0 = time.time()
    ddo, ddda = find_game("ddo"), find_game("ddda")
    ddo_mod, ddda_mod = model(ddo, DDO_BODY), model(ddda, DDDA_BODY)
    src, dst = retarget.body(ddo_mod), retarget.body(ddda_mod)
    report: dict = {"schema": "riftstone.retarget-check/1", "ddo_body": DDO_BODY.decode(),
                    "ddda_body": DDDA_BODY.decode()}
    report["bodies"] = {"ddo": retarget.check_body(src), "ddda": retarget.check_body(dst)}
    report["reparented_joints"] = retarget.reparented(src, dst)
    report["rest_world"] = {}
    for kind, b in (("ddo", src), ("ddda", dst)):
        wp = retarget.pose(b, [], [0], WATCH)
        report["rest_world"][kind] = {j: {"parent": b.joints[j].parent,
                                          "position": [round(x, 3) + 0.0 for x in wp[j][0][1]]} for j in WATCH}
    points, shapes = hit_points(ddo)
    report["hit_shapes"] = {job: {"counts": dict(shapes[job]),
                                  "attach_points": [{"joint": j, "offset": [round(x, 4) for x in off], "refs": n}
                                                    for (j, off), n in sorted(points[job].items())]}
                            for job in sorted(shapes)}
    lists = player_lists(ddo, a.only)
    print(f"{len(lists)} DDO player motion lists; bodies: DDO {len(src.joints)} joints, DDDA {len(dst.joints)}")
    acc = {"joints": defaultdict(Stat), "per_joint": defaultdict(Stat), "hits": defaultdict(Stat),
           "sens": defaultdict(Stat), "keys": Counter(), "scaled_motions": set()}
    per_list = {}
    totals = Counter()
    for i, (name, data) in enumerate(lists.items()):
        info = check_list(name, data, src, dst, ddo_mod, ddda_mod, points, acc)
        per_list[name.decode()] = info
        for k in ("bytes", "rebaked_bytes", "converted_bytes", "motions", "track_lists"):
            totals[k] += info[k]
        totals["rebuild_exact"] += info["rebuild_exact"]
        totals["converted_rebuild_exact"] += info["converted_rebuild_exact"]
        if (i + 1) % 20 == 0:
            print(f"  {i + 1}/{len(lists)} lists, {time.time() - t0:.0f}s")
    joints_total = Counter()
    for info in per_list.values():
        for j, n in info["joints"].items():
            joints_total[j] += n
    report["lists"] = {"count": len(lists), **totals,
                       "rebaked_track_lists_per_joint": dict(sorted(joints_total.items())),
                       "sha1_distinct_payloads": len({hashlib.sha1(d).hexdigest() for d in lists.values()})}
    report["keys_reproduced_on_key_frames"] = dict(acc["keys"])

    def table(stats, keyf):
        out = {}
        for key, st in sorted(stats.items(), key=lambda kv: tuple(map(str, kv[0]))):
            out[keyf(key)] = st.out()
        return out

    def unit(what: str) -> str:
        return "position_cm" if what in ("pos", "hit") else "angle_deg"

    # per vocation and overall
    joints = defaultdict(dict)
    overall: dict = defaultdict(Stat)
    for (job, cat, label, what), st in acc["joints"].items():
        joints[f"{job} {VOCATIONS[job]}"][f"{cat} {label} {unit(what)}"] = st.out()
        overall[(cat, label, what)].merge(st)
    report["joints_by_vocation"] = dict(sorted(joints.items()))
    report["joints_overall"] = table(overall, lambda k: f"{k[0]} {k[1]} {unit(k[2])}")
    report["rebaked_per_joint"] = table(acc["per_joint"], lambda k: f"joint {k[0]} {k[1]} {unit(k[2])}")
    report["hit_points_by_vocation"] = table(acc["hits"], lambda k: f"{k[0]} {VOCATIONS[k[0]]} {k[1]} {k[2]} cm")
    report["sensitivity"] = table(acc["sens"],
                                  lambda k: f"{k[0]} {unit(k[1])}" + (" hit points" if k[1] == "hit" else ""))
    report["scaled_parent_motions"] = {
        "note": "motions that scale a destination ancestor of a rebaked joint (only 150, the DDDA parent of 151/152): "
                "rebaked as if a parent's scale does not reach its children; whether it does in game is UNKNOWN",
        "motions": sorted({(nm, slot) for nm, slot, _j in acc["scaled_motions"]}),
        "joints": sorted({j for _nm, _slot, j in acc["scaled_motions"]})}
    report["per_list"] = per_list
    if a.reverse:
        print("DDDA player motion lists onto DDO's body ...")
        report["ddda_to_ddo"] = reverse_check(ddda, ddo_mod, ddda_mod)
    if not a.skip_conventions:
        print("conventions (every model and motion list of both games) ...")
        report["conventions"] = conventions(ddo, ddda)
    report["seconds"] = round(time.time() - t0)
    text = json.dumps(report, indent=1, default=str)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    # summary
    print(f"\nlists {len(lists)}, motions {totals['motions']}, track lists {totals['track_lists']}; rebuild "
          f"byte-exact {totals['rebuild_exact']}/{len(lists)} (after convert_lmt "
          f"{totals['converted_rebuild_exact']}/{len(lists)}); "
          f"bytes {totals['bytes']:,} -> {totals['rebaked_bytes']:,}")
    print(f"rebaked track lists per joint: {dict(sorted(joints_total.items()))}")
    print(f"keys reproduced on key frames: {dict(acc['keys'])}")
    print(f"\n{'vocation':22} {'category':26} {'label':8} {'pos max cm':>11} {'pos p99 cm':>11} {'max deg':>10} "
          f"{'p99 deg':>10} samples")
    for job in sorted({k[0] for k in acc["joints"]}):
        for cat in ("rebaked", "keyed_not_rebaked", "unkeyed", "joint55_keyed_not_rebaked"):
            for label in ("with", "without"):
                p, g = acc["joints"].get((job, cat, label, "pos")), acc["joints"].get((job, cat, label, "ang"))
                if not p or not p.v:
                    continue
                po, go = p.out(), g.out()
                print(f"{job + ' ' + VOCATIONS[job]:22} {cat:26} {label:8} {po['max']:>11.6f} {po['p99']:>11.6f} "
                      f"{go['max']:>10.5f} {go['p99']:>10.5f} {po['samples']}")
    print("\nhit shapes on weapon joints (attach points, cm):")
    for (job, kcat, label), st in sorted(acc["hits"].items()):
        o = st.out()
        print(f"  {job} {VOCATIONS[job]:15} {kcat:14} {label:8} max {o['max']:>11.6f}  p99 {o['p99']:>11.6f}  "
              f"samples {o['samples']}")
    print("\nsensitivity (rebaked joints):")
    for k, st in sorted(acc["sens"].items()):
        o = st.out()
        print(f"  {k[0]:46} {k[1]:4} max {o['max']:12.6f} p99 {o['p99']:12.6f} median {o['median']:10.6f} "
              f"samples {o['samples']}")
    print(f"motions with a scaled destination parent: {len(report['scaled_parent_motions']['motions'])} "
          f"(joints {report['scaled_parent_motions']['joints']})")
    if "ddda_to_ddo" in report:
        print(f"\nDDDA -> DDO body: {json.dumps(report['ddda_to_ddo'])[:900]}")
    if "conventions" in report:
        for k, v in report["conventions"].items():
            print(f"  {k}: {json.dumps(v)[:600]}")
    print(f"\n{report['seconds']}s" + (f"; report -> {a.out}" if a.out else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

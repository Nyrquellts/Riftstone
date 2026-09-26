"""Every Dragon's Dogma Online custom skill, followed from its record to the files that make it.

    python tools/skill_chain.py [--job N] [--skill M] [--json out.json]

``jobcustomNN.jcp`` names 17 resources per custom skill by (inverted CRC-32 of the path, type):
motion list, alternate motion list, motion params, collision, 10 attack params (one per skill level),
sound request, effect provider, extra sound request. This resolves each against the client's resource
index and follows the chain one step further:

  name         custom_skill_name_NN.gmd, message ``mMsgIndex`` of the skill's .csd record
  motion list  motions, frames, the joints it drives, whether it keys the weapon joints 150-154
  collision    hit shapes by kind and the joints they attach to (150-152 are the weapon joints, which
               DDO and DDDA parent differently: docs/animation.md)
  attack       level 1's hits: element (mUnk00D, appears to be: 1 none, 2 fire, 3 ice, 4 thunder,
               5 holy, 6 dark) and the two rates mUnk008 / mUnk014 (appear to be physical / magick)
  effects      the provider's effect lists and, through them, the textures and other files they load

Reads the client only (Riftstone's resource index is built if missing, under %LOCALAPPDATA%).
The output holds the game's own text (skill names): keep a --json file outside git.
"""
from __future__ import annotations

import argparse
import json
import re
import struct
import sys
import zlib
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from riftstone import arc, effect, effect_efl, flat, gmd, lmt, ocl_ddo, typemap  # noqa: E402
from riftstone.errors import RiftError  # noqa: E402
from riftstone.game import find_game  # noqa: E402
from riftstone.index import Index  # noqa: E402

SLOTS = (["motion list", "alternate motion list", "motion params", "collision"]
         + [f"attack L{i}" for i in range(1, 11)] + ["sound request", "effect provider", "extra sound request"])
ELEMENTS = {1: "none", 2: "fire", 3: "ice", 4: "thunder", 5: "holy", 6: "dark"}
WEAPON_JOINTS = range(150, 155)


def path_crc(name: bytes) -> int:
    return zlib.crc32(name) ^ 0xFFFFFFFF


def f32(bits: int) -> float:
    return struct.unpack("<f", struct.pack("<I", bits & 0xFFFFFFFF))[0]


class Client:
    def __init__(self, game):
        self.game = game
        self.idx = Index(game)
        if self.idx.pending():
            self.idx.refresh()
        self.by_crc: dict[tuple[int, int], bytes] = {}
        for name, tid in self.idx.db.execute("SELECT DISTINCT name, type FROM res"):
            self.by_crc[(path_crc(name), tid)] = name
        self._cache: dict[tuple[bytes, int], bytes] = {}

    def read(self, name: bytes, tid: int) -> bytes:
        key = (name, tid)
        if key not in self._cache:
            arcs = self.idx.archives_with(name, tid)
            e = arc.Archive.read(self.game.vanilla_arc(arcs[0])).find(name, tid) if arcs else None
            if e is None:
                raise RiftError(f"the client has no {name.decode('latin-1')}.{typemap.extension(tid)}")
            self._cache[key] = e.data()
        return self._cache[key]

    def find(self, pattern: str, ext: str) -> bytes | None:
        rows = self.idx.search(pattern, typemap.BY_EXT[ext], limit=5)
        return rows[0]["name"] if rows else None


def skill_names(c: Client, job: int) -> dict[int, str]:
    """mSkillNo -> the skill's name, through the .csd's message index."""
    csd_name = c.find(f"costom_skill_data_{job:02d}", "csd")
    gmd_name = c.find(f"custom_skill_name_{job:02d}", "gmd")
    if csd_name is None or gmd_name is None:
        return {}
    texts = gmd.parse(c.read(gmd_name, typemap.BY_EXT["gmd"])).messages
    out = {}
    for rec in flat.parse(c.read(csd_name, typemap.BY_EXT["csd"]), "csd").data["mpArray"]:
        i = rec["mMsgIndex"]
        if i < len(texts):
            out[rec["mSkillNo"]] = texts[i].text
    return out


def motion_summary(data: bytes) -> dict:
    ml = lmt.parse(data)
    bones, frames, n = set(), 0, 0
    seen = set()
    for mo in ml.motions:
        if mo is None:
            continue
        n += 1
        frames += mo.frames
        if id(mo.tracks) in seen:
            continue
        seen.add(id(mo.tracks))
        bones.update(t.bone for t in mo.tracks.tracks if t.bone != 255)
    return {"motions": n, "frames": frames, "joints": len(bones),
            "weapon_joints": sorted(b for b in bones if b in WEAPON_JOINTS)}


def collision_summary(data: bytes) -> dict:
    o = ocl_ddo.parse(data)
    shapes, joints = Counter(), Counter()
    for node in o.nodes:
        for g in node["geoms"]:
            shapes[ocl_ddo.SHAPES.get(g["mShape"], str(g["mShape"]))] += 1
            for j in (g["mJnt0"], g["mJnt1"]):
                if j >= 0:
                    joints[j] += 1
    return {"hit_shapes": dict(shapes), "joints": dict(sorted(joints.items())),
            "on_weapon_joints": sum(v for j, v in joints.items() if j in WEAPON_JOINTS)}


def attack_summary(data: bytes) -> dict:
    hits = flat.parse(data, "atk").data["mpArray"]
    return {"hits": len(hits),
            "elements": sorted({ELEMENTS.get(h["mUnk00D"], str(h["mUnk00D"])) for h in hits}),
            "rate_008": sorted({round(f32(h["mUnk008"]), 3) for h in hits}),
            "rate_014": sorted({round(f32(h["mUnk014"]), 3) for h in hits})}


def effect_summary(c: Client, data: bytes) -> dict:
    epv = effect.parse(data)
    lists = list(dict.fromkeys(p for idx in epv.indices for e in idx for p in e["mpEffectList"] if p))
    loads: Counter = Counter()
    textures: set = set()
    efl_id = typemap.BY_EXT["efl"]
    for p in lists:
        name = p.replace("/", "\\").encode("latin-1", "replace")
        name = name[:-4] if name.lower().endswith(b".efl") else name
        try:
            efl = effect_efl.parse(c.read(name, efl_id))
        except RiftError:
            loads["(missing effect list)"] += 1
            continue
        for _region, _slot, cls, path in effect_efl.resources(efl):
            loads[cls] += 1
            if cls == "rTexture":
                textures.add(path)
    return {"effect_lists": len(lists), "motion_links": len(effect.links(epv)), "loads": dict(loads),
            "textures": len(textures)}


def follow(c: Client, rec: dict) -> dict:
    out = {}
    for slot, ref in zip(SLOTS, rec["mResource"]):
        crc, tid = ref["mPathCrc"], ref["mType"]
        if crc == 0 and tid == 0:
            continue
        name = c.by_crc.get((crc, tid))
        entry = {"type": typemap.extension(tid), "path": name.decode("latin-1") if name else None}
        if name is None:
            entry["missing"] = f"crc {crc:08x} names no {typemap.extension(tid)} in the client"
            out[slot] = entry
            continue
        try:
            data = c.read(name, tid)
            if slot == "motion list":
                entry.update(motion_summary(data))
            elif slot == "collision":
                entry.update(collision_summary(data))
            elif slot == "attack L1":
                entry.update(attack_summary(data))
            elif slot == "effect provider":
                entry.update(effect_summary(c, data))
        except RiftError as e:
            entry["error"] = str(e)
        out[slot] = entry
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game", default="ddo")
    ap.add_argument("--job", type=int, help="1..11 (default: all)")
    ap.add_argument("--skill", type=int, help="the skill number (1.. base, 101.. / 201.. EX)")
    ap.add_argument("--json", help="write everything here (holds game text: keep it outside git)")
    a = ap.parse_args()
    c = Client(find_game(a.game))
    jobs = [a.job] if a.job else list(range(1, 12))
    report = {"schema": "riftstone.skill-chain/1", "jobs": {}}
    resolved = missing = 0
    for job in jobs:
        jcp = c.find(f"jobcustom{job:02d}", "jcp")
        if jcp is None:
            print(f"job {job:02d}: no jobcustom{job:02d}.jcp")
            continue
        names = skill_names(c, job)
        skills = {}
        for rec in flat.parse(c.read(jcp, typemap.BY_EXT["jcp"]), "jcp").data["mpArray"]:
            no = rec["mSkillNo"]
            if a.skill is not None and no != a.skill:
                continue
            chain = follow(c, rec)
            resolved += sum(1 for v in chain.values() if v.get("path"))
            missing += sum(1 for v in chain.values() if not v.get("path"))
            skills[no] = {"name": names.get(no), "resources": chain}
            _print_skill(job, no, names.get(no), chain)
        report["jobs"][f"{job:02d}"] = skills
    print(f"\nresources resolved {resolved:,}, not in the client {missing:,}")
    if a.json:
        Path(a.json).write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    return 0


def _print_skill(job: int, no: int, name: str | None, chain: dict) -> None:
    print(f"\njob {job:02d} skill {no:3d}  {name or '(no name)'}")
    for slot, v in chain.items():
        if re.match(r"attack L([2-9]|10)$", slot):
            continue
        path = v.get("path") or v.get("missing")
        extra = ""
        if "motions" in v:
            extra = f"  {v['motions']} motions, {v['frames']} frames, {v['joints']} joints" + \
                    (f", weapon joints {v['weapon_joints']}" if v["weapon_joints"] else "")
        elif "hit_shapes" in v:
            extra = f"  {v['hit_shapes']}, joints {v['joints']}"
        elif "hits" in v:
            extra = f"  {v['hits']} hits, element {'/'.join(v['elements'])}"
        elif "effect_lists" in v:
            extra = f"  {v['effect_lists']} effect lists, {v['motion_links']} motion links, {v['textures']} textures"
        if "error" in v:
            extra += f"  ({v['error']})"
        print(f"  {slot:21} {path}{extra}")


if __name__ == "__main__":
    raise SystemExit(main())

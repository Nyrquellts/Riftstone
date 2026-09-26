"""Survey every distinct model (.mod) of the installed game: LOD meshes, switch distances, screen size.

    python tools/lod_survey.py [--game PATH] [--height 1440] [--pop 24] [--scale auto] [--json out.json]

Reads the game only.  For each rModel: the header's MODEL_INFO {middist, lowdist} (file +0x70,
engine units = cm; rModel::load copies it to the model at +0xE0), the bounding sphere (+0x40) and
the LOD mask of every primitive (48-byte records at the header's primitive offset, mask byte +7:
1 HIGH, 2 MEDIUM, 4 LOW, 0xFF every level).  It prints how tall a model still is on screen when
the game switches it (720p and the given height, 40-degree vertical view, the engine's default
camera) and previews what the lod_tuner plugin would set with the given settings, using the
plugin's rule: scenery keeps HIGH until about --pop pixels tall and at least --scale x vanilla,
capped at 10 km and never below vanilla.  docs/lod.md quotes this tool's output.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import struct
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from riftstone import typemap  # noqa: E402
from riftstone.corpus import resources  # noqa: E402
from riftstone.game import find_game  # noqa: E402

RMODEL = typemap.jamcrc("rModel")
PRIMITIVE = 48
FOV = 40.0
CAP = 1_000_000


def parse(data: bytes) -> dict | None:
    if len(data) < 0x80 or data[:4] != b"MOD\0":
        return None
    version, _rev, joints, prims = struct.unpack_from("<BBHH", data, 4)
    prim_ofs = struct.unpack_from("<I", data, 0x30)[0]
    radius = struct.unpack_from("<f", data, 0x4C)[0]
    mid, low = struct.unpack_from("<ii", data, 0x70)
    masks: Counter = Counter()
    if prim_ofs and prim_ofs + prims * PRIMITIVE <= len(data):
        for i in range(prims):
            masks[data[prim_ofs + i * PRIMITIVE + 7]] += 1
    return {"version": version, "joints": joints, "radius": radius, "mid": mid, "low": low, "masks": masks}


def scenery(name: str) -> bool:
    n = name.replace("/", "\\").lower()
    return n.startswith("scr\\") or n.startswith("model\\om\\")


def pixels(radius: float, dist: float, height: float) -> float:
    return radius * height / (dist * math.tan(math.radians(FOV / 2)))


def far_only(m: dict) -> bool:
    """Nothing drawn at HIGH: a stand-in shown only from afar; the plugin leaves it vanilla."""
    return bool(m["masks"]) and not any(k & 1 for k in m["masks"])


def plugin(m: dict, height: float, pop: float, scale: float) -> tuple[float, int, int]:
    s = scale
    if pop > 0 and m["mid"] > 0 and m["radius"] > 0:
        s = max(s, height / (pop * math.tan(math.radians(FOV / 2))) * m["radius"] / m["mid"])
    if far_only(m):
        s = 1.0

    def one(d: int) -> int:
        return int(math.floor(max(min(d * s, CAP), d) + 0.5)) if d > 0 else d
    return s, one(m["mid"]), one(m["low"])


def deciles(xs: list[float]) -> list[float]:
    return [round(x, 1) for x in statistics.quantiles(xs, n=10)] if len(xs) > 1 else xs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game")
    ap.add_argument("--height", type=float, default=1440)
    ap.add_argument("--pop", type=float, default=24)
    ap.add_argument("--scale", default="auto")
    ap.add_argument("--json")
    a = ap.parse_args()
    scale = a.height / 720 if a.scale == "auto" else float(a.scale)
    scale = min(max(scale, 1.0), 16.0)
    game = find_game(a.game)
    rows = []
    for r in resources(game, [RMODEL], unique=True):
        m = parse(r.data)
        if m is None:
            continue
        m["name"] = r.name.decode("latin-1")
        m["lod"] = any(k != 0xFF for k in m["masks"])
        m["scenery"] = scenery(m["name"])
        rows.append(m)
    lod = [m for m in rows if m["lod"]]
    env = [m for m in lod if m["scenery"]]
    print(f"distinct models: {len(rows)}; versions {dict(Counter(m['version'] for m in rows))}")
    print(f"with LOD meshes: {len(lod)} ({len(env)} scenery, {len(lod) - len(env)} characters/other)")
    masks: Counter = Counter()
    for m in rows:
        masks.update(m["masks"])
    print("primitive LOD masks: " + ", ".join(f"0x{k:02X} x{v}" for k, v in masks.most_common()))
    pairs = Counter((m["mid"], m["low"]) for m in lod)
    print("LOD models' (middist, lowdist) in cm, most common: " + ", ".join(f"{p} x{c}" for p, c in pairs.most_common(8)))
    print(f"all models on the engine default (1000, 3000): {sum(1 for m in rows if (m['mid'], m['low']) == (1000, 3000))}")
    for label, key in (("MEDIUM (first change)", "mid"), ("LOW", "low")):
        at720 = [pixels(m["radius"], m[key], 720) for m in env]
        print(f"scenery height on screen at the {label} switch, 720p: median {statistics.median(at720):.0f} px, "
              f"deciles {deciles(at720)}")
    preview = [plugin(m, a.height, a.pop, scale) for m in env]
    mults = [p[0] for p in preview]
    small = [p[0] for p, m in zip(preview, env) if m["radius"] < 150]
    print(f"lod_tuner at {a.height:.0f} px, PopPixels {a.pop:g}, Scale x{scale:.2f}: scenery multiplier median "
          f"x{statistics.median(mults):.1f}, deciles {deciles(mults)}; small scenery (radius < 1.5 m, {len(small)}) "
          f"median x{statistics.median(small):.1f}; capped at 10 km: {sum(1 for p in preview if p[1] == CAP)}; "
          f"far-only kept vanilla: {', '.join(m['name'] for m in env if far_only(m)) or 'none'}")
    if a.json:
        out = [{k: (dict(v) if isinstance(v, Counter) else v) for k, v in m.items()} for m in rows]
        Path(a.json).write_text(json.dumps(out, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

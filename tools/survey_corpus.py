"""Survey every archive of the installed game: type ids, class names, payload magics.

    python tools/survey_corpus.py [--game PATH] --out survey.json

Reads archive directories (and a sample of payloads per type) only.  Each type
id is matched to the DDDA.exe identifier string whose JAMCRC equals it.  The
output feeds tools/gen_typemap.py.
"""
from __future__ import annotations

import argparse
import collections
import json
import random
import re
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from riftstone.corpus import directory  # noqa: E402
from riftstone.game import find_game  # noqa: E402

SAMPLES = 60


def jamcrc(s: bytes) -> int:
    return (~zlib.crc32(s)) & 0x7FFFFFFF


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    game = find_game(a.game)
    rng = random.Random(1)
    types: dict[int, dict] = {}
    occurrences: dict[int, list] = collections.defaultdict(list)
    for path in game.archives():
        for name, tid, zs, size, off in directory(path):
            t = types.setdefault(tid, {"count": 0, "bytes": 0, "arcs": set(), "names": []})
            t["count"] += 1
            t["bytes"] += size
            t["arcs"].add(game.arc_name(path))
            if len(t["names"]) < 6:
                t["names"].append(name.decode("latin-1"))
            occurrences[tid].append((path, off, zs))
    for tid, occ in occurrences.items():
        magics = collections.Counter()
        for path, off, zs in (occ if len(occ) <= SAMPLES else rng.sample(occ, SAMPLES)):
            with open(path, "rb") as fh:
                fh.seek(off)
                head = zlib.decompressobj().decompress(fh.read(min(zs, 4096)), 16)
            magics[head[:4].hex()] += 1
        types[tid]["magics"] = magics.most_common(5)
    table = collections.defaultdict(set)
    for m in re.finditer(rb"[A-Za-z_][A-Za-z0-9_:]{2,80}", game.exe.read_bytes()):
        table[jamcrc(m.group(0))].add(m.group(0).decode())
    rows = [{"type_id": f"{tid:08x}", "class": sorted(table.get(tid, [])), "count": t["count"],
             "archives": len(t["arcs"]), "bytes": t["bytes"], "magics": t["magics"], "names": t["names"]}
            for tid, t in sorted(types.items(), key=lambda kv: -kv[1]["count"])]
    Path(a.out).write_text(json.dumps({"archives": len(game.archives()), "types": rows}, indent=1), encoding="utf-8")
    print(f"{len(rows)} types, {sum(1 for r in rows if r['class'])} named -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

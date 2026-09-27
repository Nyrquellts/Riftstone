"""Generate src/riftstone/data/ddo_shell_classes.json: the classes Dragon's Dogma Online's player shell
lists use (every obj\\pl\\pl000000\\param\\shellparam\\job*.shl), each with its parent chain, from the
MtDTI registry map of DDO.exe (<path>, the ddon toolkit's static RE output).

compat_pack re-tags an object of a class Dark Arisen does not have as its nearest ancestor that Dark
Arisen's XFS schema knows; this table is the only DDO knowledge it needs for that.

    python tools/gen_ddo_shell_classes.py [--classes <path>]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from riftstone import arc, typemap, xfs  # noqa: E402
from riftstone.game import find_game  # noqa: E402
from riftstone.index import Index  # noqa: E402

OUT = ROOT / "src" / "riftstone" / "data" / "ddo_shell_classes.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--classes", default=r"<path>")
    args = ap.parse_args()
    registry = json.loads(Path(args.classes).read_text(encoding="utf-8"))
    by_id = {int(c["id"], 16): c for c in registry}
    by_name = {c["name"]: c for c in registry}
    game = find_game("ddo")
    idx = Index(game)
    tid = typemap.BY_EXT["shl"]
    names = sorted({r["name"] for r in idx.search("shellparam", tid, limit=100) if b"\\shellparam\\job" in r["name"]})
    used: set[int] = set()
    for n in names:
        arcs = idx.archives_with(n, tid)
        data = arc.Archive.read(game.arc_path(arcs[0])).find(n, tid).data()
        used |= {c.type_id for c in xfs.parse(data).classes}
    out: dict[str, dict] = {}
    for cid in sorted(used):
        c = by_id.get(cid)
        while c is not None:
            key = f"0x{int(c['id'], 16):08X}"
            if key in out:
                break
            out[key] = {"name": c["name"], "parent": c.get("parent")}
            c = by_name.get(c.get("parent") or "")
    missing = sorted(f"0x{cid:08X}" for cid in used if cid not in by_id)
    OUT.write_text(json.dumps({"source": "DDO.exe MtDTI registry (client 03.04.003)", "shell_lists": len(names),
                               "unknown_ids": missing, "classes": out}, indent=1) + "\n", encoding="utf-8")
    print(f"{len(names)} shell lists, {len(used)} classes used, {len(out)} with ancestors -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

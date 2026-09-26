"""Count every resource type in a game's archives: entries, distinct payloads, bytes.  Reads only.

    python tools/type_census.py [--game ddda|ddo|PATH] [--out census.json] [--workers N]

For each type id: its engine class and extension (typemap), how many archive entries
carry it, how many distinct payloads there are (SHA-1 of the stored bytes, the same
notion of "distinct" the corpus gates use), their decoded size, and which Riftstone
codec claims it (`CODECS`).  The report holds names, counts and hashes only -- no
game data -- so it may be kept in git as a measurement.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from riftstone import corpus, typemap  # noqa: E402
from riftstone.game import find_game  # noqa: E402

# Which module round-trips a type byte-exact, by extension (the gate that proves it in brackets).
# XFS types are added from typemap.  Anything absent is "none": Riftstone moves it only as opaque bytes.
CODECS = {
    "arc": "arcref (arcs)", "gmd": "gmd (gmd)", "lot": "lot / lot_ddo (lot)", "ocl": "ocl (ocl)",
    "itl": "itl (itl)", "ist": "tables (tables)", "imx": "tables (tables)", "gpl": "gpl (gpl)",
    "tex": "tex (tex)", "mrl": "mrl (mrl)", "prp": "prp (prp)", "ean": "ean (ean)",
    "lmt": "lmt + lmtcodec (lmt)",
}


def _scan(path: str) -> tuple[str, list[tuple[int, bytes, int]], str | None]:
    """(archive, [(type id, sha1 of stored bytes, decoded size)], error)."""
    try:
        rows = corpus.directory(Path(path))
        out = []
        with open(path, "rb") as fh:
            for _name, tid, zs, size, off in rows:
                fh.seek(off)
                out.append((tid, hashlib.sha1(fh.read(zs)).digest(), size))
        return path, out, None
    except Exception as e:  # noqa: BLE001 - reported per archive
        return path, [], f"{type(e).__name__}: {e}"


def codec_for(ext: str, tid: int) -> str:
    from riftstone import flat
    if ext in CODECS:
        return CODECS[ext]
    if ext in flat.SCHEMAS:
        return "flat (flat)"
    if typemap.is_xfs(tid):
        return "xfs (xfs, yaml)"
    return "none"


def census(game, workers: int) -> dict:
    t0 = time.time()
    archives = [str(p) for p in game.archives()]
    entries: dict[int, int] = defaultdict(int)
    distinct: dict[int, dict[bytes, int]] = defaultdict(dict)
    errors = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for path, rows, err in pool.map(_scan, archives, chunksize=16):
            if err:
                errors.append({"arc": path, "why": err})
            for tid, digest, size in rows:
                entries[tid] += 1
                distinct[tid].setdefault(digest, size)
    types = []
    for tid in sorted(entries, key=lambda t: (-len(distinct[t]), t)):
        ext = typemap.extension(tid)
        types.append({"id": f"{tid:08x}", "class": typemap.class_name(tid), "ext": ext,
                      "xfs": typemap.is_xfs(tid), "entries": entries[tid], "distinct": len(distinct[tid]),
                      "bytes": sum(distinct[tid].values()), "codec": codec_for(ext.lower(), tid)})
    covered = [t for t in types if t["codec"] != "none"]
    return {"schema": "riftstone.type-census/1", "game": game.kind, "root": str(game.root),
            "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "archives": len(archives),
            "entries": sum(entries.values()), "distinct": sum(len(d) for d in distinct.values()),
            "types": len(types), "types_with_codec": len(covered),
            "distinct_with_codec": sum(t["distinct"] for t in covered),
            "bytes_with_codec": sum(t["bytes"] for t in covered),
            "bytes": sum(t["bytes"] for t in types), "errors": errors,
            "seconds": round(time.time() - t0, 1), "by_type": types}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game")
    ap.add_argument("--out")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    a = ap.parse_args()
    rep = census(find_game(a.game), a.workers)
    text = json.dumps(rep, indent=1)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    print(f"{rep['game']}: {rep['archives']} archives, {rep['entries']} entries, {rep['distinct']} distinct, "
          f"{rep['types']} types ({rep['types_with_codec']} with a codec), {rep['seconds']} s, "
          f"{len(rep['errors'])} errors")
    print(f"{'ext':>14} {'class':<32} {'entries':>8} {'distinct':>8} {'MiB':>9}  codec")
    for t in rep["by_type"]:
        print(f"{t['ext']:>14} {t['class'][:32]:<32} {t['entries']:>8} {t['distinct']:>8} "
              f"{t['bytes'] / 2**20:>9.1f}  {t['codec']}")
    return 0 if not rep["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

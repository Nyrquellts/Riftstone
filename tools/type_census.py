"""Count every resource type in a game's archives: entries, distinct payloads, bytes.  Reads only.

    python tools/type_census.py [--game ddda|ddo|PATH] [--out census.json] [--workers N]

For each type id: its engine class and extension (typemap), how many archive entries
carry it, how many distinct payloads there are (SHA-1 of the stored bytes, the same
notion of "distinct" the corpus gates use), their decoded size, and which Riftstone
codec claims it (`codecs()`, read from the codecs' own registries).  The archives are
read as the game shipped them (corpus.source: Riftstone's backup of an archive an
install replaced).  The report holds names, counts and hashes only -- no game data --
so it may be kept in git as a measurement.
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.append(str(Path(__file__).resolve().parent))                       # check_corpus

from riftstone import corpus, typemap  # noqa: E402
from riftstone.game import find_game  # noqa: E402

# The codecs without a YAML form (params does not list them): extension -> (module, gate, the game it is
# proved for, None for both).  Everything else is read from the registries in codecs().
BINARY = {"arc": ("arcref", "arcs", None), "tex": ("tex", "tex", None), "mrl": ("mrl", "mrl", None),
          "ean": ("ean", "ean", None), "lmt": ("lmt + lmtcodec", "lmt", None), "sbc": ("sbc", "sbc", None),
          "efs": ("effect_efs", "effect", None), "nav": ("nav", "nav", None), "spc": ("audio_bank", "check_audio_corpus", "ddda")}
# A layout and a group list pick their module by version (params._format_of): Online's revisions.
REVISIONS = {"lot": "lot_ddo", "gpl": "gpl_ddo"}
# The check_corpus check that proves a module's files, where it is not the extension's own check.
GATES = {"ocl_ddo": "ocl", "tables": "tables", "weather": "weather", "camera": "lcm", "sound": "sound",
         "effect": "effect", "effect_efl": "effect", "effect_e2d": "effect", "facial": "fca", "msgset": "msgset",
         "ddo_params": "ddo_params", "flat": "flat"}


@functools.cache
def codecs() -> dict[str, tuple[tuple[str, ...], tuple[str, ...], str | None]]:
    """extension -> (modules that round-trip it byte-exact, the gates that prove it, the one game it is proved
    for or None), from params.FORMATS and TYPED, ddo_params.KINDS, flat.SCHEMAS, REVISIONS and BINARY."""
    from riftstone import ddo_params, flat, params

    found: dict[str, tuple[list[str], list[str], str | None]] = {}

    def add(ext: str, module: str, gate: str, game: str | None = None) -> None:
        mods, gates, _ = found.setdefault(ext, ([], [], game))
        if module not in mods:
            mods.append(module)
        if gate not in gates:
            gates.append(gate)

    for module, tag in params.FORMATS.values():
        ext = tag.removesuffix("-ddo")
        add(ext, module, GATES.get(module, ext))
    for ext, module in params.TYPED.items():
        add(ext, module, GATES.get(module, ext))
    for ext in ddo_params.KINDS:
        add(ext, "ddo_params", "ddo_params")
        if ext == "ndp":
            add(ext, "ddo_params", "ndp")
    for ext in flat.SCHEMAS:
        add(ext, "flat", "flat")
    for ext, module in REVISIONS.items():
        add(ext, module, ext)
    for ext, (module, gate, game) in BINARY.items():
        add(ext, module, gate, game)
    return {ext: (tuple(m), tuple(g), game) for ext, (m, g, game) in found.items()}


def codec_for(ext: str, tid: int, kind: str | None = None) -> str:
    """'module (gate)' for the codec that round-trips this type byte-exact, or 'none': Riftstone moves it only
    as opaque bytes.  With ``kind`` ('ddda'/'ddo'), 'none' too where that game's revision is not decoded
    (check_corpus.NOT_DECODED) or the codec is proved for the other game only."""
    import check_corpus

    ext = ext.lower()
    if kind is not None and ext in check_corpus.NOT_DECODED.get(kind, {}):
        return "none"
    c = codecs().get(ext)
    if c is not None and (kind is None or c[2] in (None, kind)):
        return f"{' / '.join(c[0])} ({', '.join(c[1])})"
    if typemap.is_xfs(tid):
        return "xfs (xfs, yaml)"
    return "none"


def _scan(job: tuple[str, str]) -> tuple[str, list[tuple[int, bytes, int]], str | None]:
    """(archive, [(type id, sha1 of stored bytes, decoded size)], error), reading the copy at ``job[1]`` (the
    archive as shipped) of the archive ``job[0]``."""
    path, src = job
    try:
        rows = corpus.directory(Path(src))
        out = []
        with open(src, "rb") as fh:
            for _name, tid, zs, size, off in rows:
                fh.seek(off)
                out.append((tid, hashlib.sha1(fh.read(zs)).digest(), size))
        return path, out, None
    except Exception as e:  # noqa: BLE001 - reported per archive
        return path, [], f"{type(e).__name__}: {e}"


def census(game, workers: int) -> dict:
    t0 = time.time()
    kept = corpus.backups(game)
    jobs = [(str(p), str(corpus.source(game, p, kept))) for p in game.archives()]
    entries: dict[int, int] = defaultdict(int)
    distinct: dict[int, dict[bytes, int]] = defaultdict(dict)
    errors = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for path, rows, err in pool.map(_scan, jobs, chunksize=16):
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
                      "bytes": sum(distinct[tid].values()), "codec": codec_for(ext.lower(), tid, game.kind)})
    covered = [t for t in types if t["codec"] != "none"]
    return {"schema": "riftstone.type-census/1", "game": game.kind, "root": str(game.root),
            "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "archives": len(jobs),
            "read_from_backup": sum(p != s for p, s in jobs),
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

"""Regenerate src/riftstone/data/typemap_ddo.json: every resource type in Dragon's Dogma Online.

Same method as tools/gen_typemap.py for DDDA: survey every type id in the installed DDO
archives, name each by matching it against the JAMCRC of identifier strings in the
executable, then choose an extension. DDO.exe ships Themida-encrypted, so the strings come
from an unpacked dump (the one in <path>*; pass another with
--exe). Extensions: the community's (Arrowgene's ArcArchive.cs register list, when given),
else DDDA's for the same class, else the payload magic, else the class name; every extension
is unique across both games (typemap.py merges this table where DDDA has no entry).

usage: python tools/gen_typemap_ddo.py [--game DIR] [--exe UNPACKED_DDO.exe] [--arrowgene ArcArchive.cs]
Holds engine names, hashes and counts only -- no game data.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import zlib
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from riftstone import typemap  # noqa: E402
from riftstone.corpus import directory, encrypted  # noqa: E402
from riftstone.game import find_game  # noqa: E402

DEFAULT_EXE = Path(r"<path>")
DEFAULT_ARROWGENE = Path(r"<path>")


def jamcrc(name: bytes) -> int:
    return (~zlib.crc32(name)) & 0x7FFFFFFF


def exe_names(exe: Path) -> dict[int, str]:
    data = exe.read_bytes()
    out: dict[int, str] = {}
    for m in re.finditer(rb"[A-Za-z_][A-Za-z0-9_:]{2,90}(?=\0)", data):
        out.setdefault(jamcrc(m.group(0)), m.group(0).decode("ascii"))
    return out


def arrowgene_exts(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    return dict(re.findall(r'Register\("([^"]+)",\s*"([^"]*)"\)', path.read_text(encoding="utf-8")))


def magic_ext(head: bytes) -> str | None:
    t = head.rstrip(b"\0 ").decode("latin-1").lower()
    return t if 2 <= len(t) <= 4 and t.isalnum() else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="ddo")
    ap.add_argument("--exe", default=str(DEFAULT_EXE))
    ap.add_argument("--arrowgene", default=str(DEFAULT_ARROWGENE))
    a = ap.parse_args()
    game = find_game(a.game)
    names = exe_names(Path(a.exe))
    community = arrowgene_exts(Path(a.arrowgene))
    counts: Counter[int] = Counter()
    magics: dict[int, bytes] = {}
    for path in game.archives():
        rows = directory(path)
        for _n, tid, _zs, _size, _off in rows:
            counts[tid] += 1
        want = [r for r in rows if r[1] not in magics]
        if not want:
            continue
        enc = encrypted(path)
        with open(path, "rb") as fh:
            for _n, tid, zs, _size, off in want:
                fh.seek(off)
                comp = fh.read(zs)
                if enc:
                    from riftstone.cipher import arc_cipher
                    comp = arc_cipher().decrypt(comp)
                d = zlib.decompressobj()
                magics[tid] = d.decompress(comp, 4)
    ddda_by_class = {c: e for c, e, _x, _k in typemap.TYPES_BY_CLASS}
    used = {e.lower() for _c, e, _x, _k in typemap.TYPES_BY_CLASS}
    rows, unnamed = [], []
    for tid in sorted(counts, key=lambda t: names.get(t, "~").lower()):
        cls = names.get(tid)
        if cls is None:
            unnamed.append(f"{tid:08x}")
            continue
        head = magics.get(tid, b"")
        xfs = head == b"XFS\0"
        if cls in ddda_by_class:
            ext = ddda_by_class[cls]          # shared class: same extension as DDDA
        else:
            ext = None
            for cand in (community.get(cls), magic_ext(head), cls.split("::")[-1].lstrip("rcu").lower()):
                if cand and re.fullmatch(r"[a-z0-9_.]{1,24}", cand.lower()) and cand.lower() not in used:
                    ext = cand
                    break
            if ext is None:
                base = cls.split("::")[-1].lower()
                ext, n = base, 2
                while ext in used:
                    ext, n = f"{base}{n}", n + 1
        used.add(ext.lower())
        rows.append([cls, ext, xfs, counts[tid]])
    out = Path(__file__).resolve().parents[1] / "src" / "riftstone" / "data" / "typemap_ddo.json"
    doc = {"schema": "riftstone.typemap-ddo/1",
           "source": {"archives": len(game.archives()), "resources": sum(counts.values()),
                      "exe": Path(a.exe).name, "unnamed_type_ids": unnamed},
           "types": rows}
    out.write_text(json.dumps(doc, indent=0) + "\n", encoding="utf-8")
    print(f"wrote {len(rows)} types ({len(unnamed)} unnamed) to {out}")
    return 0 if not unnamed else 1


if __name__ == "__main__":
    raise SystemExit(main())

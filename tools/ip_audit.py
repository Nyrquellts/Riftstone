"""Audit what would be published for what must never be: a file of either game, Online's archive key, Capcom's
copyright notation, third-party code that is not Riftstone's, links to client downloads (src/riftstone/ipaudit.py;
docs/legal.md).  Reads only.

    python tools/ip_audit.py                       the files git tracks here
    python tools/ip_audit.py dist\\x.zip folder\\   a release, a package or a folder (every file in it)

Exit status 0 when nothing is found, 1 otherwise.
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from riftstone import ipaudit  # noqa: E402


def main(argv: list[str]) -> int:
    found = []
    targets = [Path(a) for a in argv] or [ROOT]
    for t in targets:
        if t.is_file() and zipfile.is_zipfile(t):
            found += [f"{t.name}: {f}" for f in ipaudit.scan_zip(t)]
        elif t == ROOT:
            found += ipaudit.scan_tree(ROOT)
        elif t.is_dir():
            found += ipaudit.scan_tree(t, [p.relative_to(t).as_posix() for p in sorted(t.rglob("*")) if p.is_file()])
        elif t.is_file():
            found += ipaudit.scan_bytes(t.name, t.read_bytes())
        else:
            print(f"{t}: not found", file=sys.stderr)
            return 2
    for f in found:
        print(f)
    print(f"{len(found)} finding(s) in {', '.join(str(t) for t in targets)}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

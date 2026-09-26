"""Write src/riftstone/data/vanilla_archives_ddo.json.gz: every DDO archive's CRC32 as the
distribution RAR recorded it (7-Zip technical listing), so installs refuse to back up a changed
archive as 'vanilla'.  The RAR's own record is the provenance; a sample of live files is
re-checked so the name mapping is proven, not assumed.

    python tools/gen_vanilla_ddo.py "<Dragon's Dogma Online 16.04.2025.rar>" [--game <ddo folder>]
"""

from __future__ import annotations

import argparse
import gzip
import json
import random
import shutil
import subprocess
import sys
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from riftstone.game import DDO_BUILD_SHA256, find_game  # noqa: E402

OUT = ROOT / "src" / "riftstone" / "data" / "vanilla_archives_ddo.json.gz"


def seven_zip() -> str:
    for p in (r"C:\Program Files\7-Zip\7z.exe", r"C:\Program Files (x86)\7-Zip\7z.exe"):
        if Path(p).is_file():
            return p
    w = shutil.which("7z")
    if not w:
        raise SystemExit("7-Zip (7z.exe) not found")
    return w


def listing(rar: Path) -> dict[str, tuple[int, int]]:
    out = subprocess.run([seven_zip(), "l", "-slt", "-sccUTF-8", "--", str(rar)], capture_output=True,
                         check=True).stdout.decode("utf-8", "replace").replace("\r\n", "\n")
    body = out.split("\n----------\n", 1)
    if len(body) != 2:
        raise SystemExit("unexpected 7-Zip listing")
    files = {}
    for block in body[1].split("\n\n"):
        f = dict(line.split(" = ", 1) for line in block.split("\n") if " = " in line)
        if f.get("Path", "").lower().endswith(".arc") and f.get("CRC"):
            files[f["Path"].replace("\\", "/")] = (int(f["Size"]), int(f["CRC"], 16))
    return files


def crc32(p: Path) -> int:
    c = 0
    with open(p, "rb") as fh:
        while b := fh.read(8 << 20):
            c = zlib.crc32(b, c)
    return c


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("rar", type=Path)
    ap.add_argument("--game", default="ddo")
    ap.add_argument("--sample", type=int, default=300, help="live archives re-checked by CRC32")
    a = ap.parse_args()
    game = find_game(a.game)
    rows = listing(a.rar)
    archives: dict[str, str] = {}
    for path, (size, crc) in rows.items():
        low = path.lower()
        i = low.find("/nativepc/")
        if i < 0:
            continue
        name = path[i + len("/nativePC/"):-4]
        archives[name] = f"crc32:{crc:08x}"
        rows[path] = (size, crc, name)
    live = {game.arc_name(p).lower(): p for p in game.archives()}
    missing = sorted(set(k.lower() for k in archives) - set(live))
    extra = sorted(set(live) - set(k.lower() for k in archives))
    rng = random.Random(0x0DD0)
    sample = rng.sample(sorted(archives), min(a.sample, len(archives)))
    bad = [n for n in sample if n.lower() in live and f"crc32:{crc32(live[n.lower()]):08x}" != archives[n]]
    print(f"{len(archives):,} archives recorded; {len(missing)} missing on disk, {len(extra)} extra on disk; "
          f"{len(sample) - len(bad)}/{len(sample)} sampled live archives match")
    if bad or missing:
        print("differs:", (bad + missing)[:10])
        return 1
    doc = {
        "schema": "riftstone.vanilla-archives/1",
        "game": "ddo",
        "hash": "crc32",
        "build": {"client": "03.04.003", "exe_sha256": DDO_BUILD_SHA256},
        "provenance": {"rar": a.rar.name, "rar_size": a.rar.stat().st_size,
                       "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                       "note": "CRC32 of every .arc as recorded by the distribution RAR; "
                               f"{len(sample)} live archives re-checked"},
        "archives": dict(sorted(archives.items(), key=lambda kv: kv[0].lower())),
    }
    raw = json.dumps(doc, indent=0, ensure_ascii=False).encode("utf-8")
    OUT.write_bytes(gzip.compress(raw, 9, mtime=0))
    print(f"wrote {OUT} ({OUT.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

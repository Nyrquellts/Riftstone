"""Regenerate src/riftstone/data/vanilla_archives.json from a verified install manifest.

The source is the preservation manifest of a pristine Steam install (build
2364871): every file SHA-256-hashed and matched against Steam's cached depot
records.  Only archive hashes and sizes are kept -- no game bytes.  Riftstone
uses them to refuse backing up an archive another tool already modified, and
for 'riftstone doctor --verify'.

usage: python tools/gen_vanilla_hashes.py MANIFEST.json
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def main() -> int:
    src = Path(sys.argv[1])
    raw = src.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if src.stem != digest:
        raise SystemExit(f"{src.name} does not match its own content hash ({digest}); refusing")
    manifest = json.loads(raw)
    arcs = {}
    exe = None
    for f in manifest["files"]:
        p = f["path"]
        if p == "DDDA.exe":
            exe = f["sha256"]
        if p.lower().startswith("nativepc/") and p.lower().endswith(".arc"):
            arcs[p[len("nativePC/"):-4]] = f["sha256"]
    out = {"schema": "riftstone.vanilla-archives/1",
           "build": {"steam_app": 367500, "build_id": 2364871, "exe_sha256": exe},
           "provenance": {"manifest_sha256": digest, "created_utc": manifest.get("created_utc"),
                          "note": "pristine install hashed file by file and matched to Steam's cached depot records"},
           "archives": dict(sorted(arcs.items(), key=lambda kv: kv[0].lower()))}
    target = Path(__file__).resolve().parents[1] / "src" / "riftstone" / "data" / "vanilla_archives.json"
    target.write_text(json.dumps(out, indent=0, separators=(",", ":")) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(arcs)} archives -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

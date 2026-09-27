"""Write one of Dragon's Dogma Online's chimera variants as four texture files that ``riftstone skin make``
turns into a DDDA skin -- from this PC's own Online client, into a folder on this PC.

    python tools/ddo_skins.py white  <out folder>     # em015202 White Chimera: DDO's own albedo maps
    python tools/ddo_skins.py shadow <out folder>     # em015203 Shadow Chimera: baked
    python tools/ddo_skins.py blaze  <out folder>     # em015204 Blaze Chimera: baked

The work is src/riftstone/ddoskins.py (``riftstone monster convert "White Chimera" --into chimera --as-skin N``
does it in one step).  The folder also gets ``riftstone-source.json``, so ``skin make --textures`` records
where the maps came from and a mod package carries that recipe instead of the textures.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from riftstone import ddoskins  # noqa: E402
from riftstone.errors import RiftError  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("variant", choices=sorted(ddoskins.VARIANTS))
    ap.add_argument("out", type=Path)
    a = ap.parse_args()
    _, title = ddoskins.VARIANTS[a.variant]
    try:
        maps = ddoskins.build(a.variant, lambda s: print(s, flush=True))
    except RiftError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    a.out.mkdir(parents=True, exist_ok=True)
    for dd, data in maps.items():
        (a.out / f"{dd}.tex").write_bytes(data)
    (a.out / "source.txt").write_text(title + "\n", encoding="utf-8")
    (a.out / "riftstone-source.json").write_text(json.dumps({"kind": "ddo-skin", "variant": a.variant}) + "\n",
                                                 encoding="utf-8")
    print(f"wrote {a.out}: riftstone skin make chimera <n> --textures \"{a.out}\" --title \"{title.split(' (')[0]}\" "
          f"--source \"{title}\" --mod <mod>")
    return 0


if __name__ == "__main__":
    sys.exit(main())

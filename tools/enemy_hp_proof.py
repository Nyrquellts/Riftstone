"""Where a creature's health is, measured again from the game (docs/enemy-hp.md).

    python tools/enemy_hp_proof.py [--ddda EXE]

Dark Arisen, each archive as shipped.  Three measurements:

  1. every rRegionStatus (.rst) parses and rebuilds byte for byte, has a type 0 region, and the first element
     of that region (mHPMax) is the creature's base health: the count, the least, the middle and the most;
  2. the executable's per-enemy resource lists ({name, class, 0} entries in its data) name an rRegionStatus
     for exactly the .rst resources the archives hold, and name the .prp of every enemy but the human ones;
  3. the human enemies' .prp files (em10xx, the ones no list names) each carry 人間敵 HP.

The class records (0x019A909C rRegionStatus, 0x018D2C40 rPropParam) are DDDA.exe build 2364871's.  Reads the
game only.  Exit 0 when every measurement comes out as documented; 0 with a note when no game is installed.
"""
from __future__ import annotations

import argparse
import re
import statistics
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.append(str(Path(__file__).resolve().parent))                       # doc_claims

from doc_claims import Image  # noqa: E402
from riftstone import flat, names, nav, prp, typemap  # noqa: E402
from riftstone.errors import RiftError  # noqa: E402
from riftstone.game import find_game  # noqa: E402
from riftstone.index import Index  # noqa: E402

RST_CLASS, PRP_CLASS = 0x019A909C, 0x018D2C40
HUMAN = re.compile(r"charparam\\em\\em10\d\d")
HUMAN_HP = re.compile(r"^\s*人間敵 HP: ([-0-9.e+]+)", re.M)


def f32(bits: int) -> float:
    return struct.unpack("<f", struct.pack("<I", bits & 0xFFFFFFFF))[0]


def listed_names(img: Image, cls: int) -> set[str]:
    """Lower-cased names of every {name pointer, class pointer, 0} entry whose class is `cls`."""
    pat, out = struct.pack("<I", cls), set()
    for _, _, rptr, rsz in img.sections:
        blob = img.data[rptr:rptr + rsz]
        i = blob.find(pat)
        while i != -1:
            if i >= 4 and i % 4 == 0 and i + 8 <= len(blob) and struct.unpack_from("<I", blob, i + 4)[0] == 0:
                raw = img.read(struct.unpack_from("<I", blob, i - 4)[0], 80)
                text = raw.split(b"\0")[0] if raw else b""
                if text.lower().startswith(b"charparam\\"):
                    out.add(text.decode("latin-1").lower())
            i = blob.find(pat, i + 1)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ddda", help="DDDA.exe to read the resource lists from (default: the installed game's)")
    args = ap.parse_args(argv)
    try:
        game = find_game("ddda")
    except RiftError as e:
        print(f"no Dark Arisen to measure ({e})")
        return 0
    ix = Index(game)
    t_rst, t_prp = typemap.BY_EXT["rst"], typemap.BY_EXT["prp"]

    def archive_names(t: int) -> set[str]:
        return {n.decode("latin-1").lower() for (n,) in ix.db.execute("SELECT DISTINCT name FROM res WHERE type=?", (t,))}

    rst, prps = archive_names(t_rst), archive_names(t_prp)
    ok = True

    print(f"1. {len(rst)} .rst resources")
    healths, exact, no_zero = [], 0, []
    for nm in sorted(rst):
        data = nav.game_resource(game, ix, nm.encode("latin-1"), t_rst)
        f = flat.parse(data, "rst")
        exact += flat.build(f) == data
        zero = [r for r in f.data["mRegionStatusList"] if r["mType"] == 0]
        if zero:
            healths.append(f32(zero[0]["mElementList"][0]["mHPMax"]))
        else:
            no_zero.append(nm)
    print(f"   rebuild byte for byte: {exact} of {len(rst)}; without a type 0 region: {len(no_zero)}")
    print(f"   first element of the first type 0 region: least {min(healths):,.10g}, middle "
          f"{statistics.median(healths):,.10g}, most {max(healths):,.10g}")
    ok &= exact == len(rst) and not no_zero

    table = names.Names.load(game, ix)
    have = {e for e in table.enemies if f"charparam\\em\\{e}" in rst or any(x.startswith(f"charparam\\em\\{e}_") for x in rst)}
    print(f"   families in the game's name tables: {len(table.enemies)}; with an .rst: {len(have)}; "
          f"without: {', '.join(sorted(set(table.enemies) - have))}")

    exe = Image(args.ddda or game.exe)
    in_rst = listed_names(exe, RST_CLASS)
    in_prp = {x for x in listed_names(exe, PRP_CLASS) if x.startswith("charparam\\em\\em")}
    prp_em = {x for x in prps if x.startswith("charparam\\em\\em")}
    print(f"2. the executable's resource lists name {len(in_rst)} rRegionStatus; the archives hold {len(rst)}; "
          f"in both {len(in_rst & rst)}")
    only_arc, only_exe = sorted(rst - in_rst), sorted(in_rst - rst)
    print(f"   .rst in the archives but in no list: {only_arc}; in a list but in no archive: {only_exe}")
    unlisted = sorted(prp_em - in_prp)
    humans = sorted(x for x in prp_em if HUMAN.match(x))
    print(f"   enemy .prp: {len(prp_em)} in the archives, {len(prp_em & in_prp)} named by a list; "
          f"unlisted {len(unlisted)}, all human (em10xx): {unlisted == humans}")
    ok &= not only_arc and not only_exe and unlisted == humans and not (in_prp - prps)

    values = []
    for nm in humans:
        data = nav.game_resource(game, ix, nm.encode("latin-1"), t_prp)
        m = HUMAN_HP.search(prp.to_yaml(prp.parse(data), nm)) if data else None
        if m:
            values.append(float(m.group(1)))
    print(f"3. the human enemies' .prp: {len(humans)}, with a human-enemy HP: {len(values)}"
          + (f", {min(values):,.10g} to {max(values):,.10g}, {len(set(values))} distinct" if values else ""))
    ok &= len(values) == len(humans) and bool(humans)

    print("OK: as documented in docs/enemy-hp.md" if ok else "NOT as documented in docs/enemy-hp.md")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

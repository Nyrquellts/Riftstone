"""Regenerate docs/stage-map.md: the measured stage -> room-name table.

Source of truth: rStagePlaceName (.spn, decoded byte-exact by flat.py) whose mPlaceNameId
indexes id/DDN/message/common/map_placelist. No guessing -- every name comes from the game.
A .spn that does not read is named on stderr and in the table's footer, and the exit code is 1.

    python tools/gen_stage_map.py [--game PATH] [--lang eng] [--out FILE]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from riftstone import corpus, flat, gmd, typemap  # noqa: E402
from riftstone.errors import RiftError  # noqa: E402
from riftstone.game import find_game  # noqa: E402

SEP = chr(92)


def base(label: str) -> str:
    return label.rsplit(SEP, 1)[-1]


def rooms(spn: bytes, place: list[str]) -> list[str]:
    """The named places of one .spn in file order, each once.  FormatError when it is not a .spn."""
    seen, names = set(), []
    for rec in flat.parse(spn, "spn").data["mpPlace"]:
        pid = rec["mPlaceNameId"]
        if 0 <= pid < len(place) and place[pid] and pid not in seen:
            seen.add(pid)
            names.append(place[pid])
    return names


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game")
    ap.add_argument("--lang", default="eng")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "docs" / "stage-map.md"))
    a = ap.parse_args(argv)
    try:
        game = find_game(a.game)
    except RiftError as e:
        print(e, file=sys.stderr)
        return 1

    place: list[str] = []
    for r in corpus.resources(game, [typemap.BY_EXT["gmd"]]):
        if r.label.endswith(f"map_placelist_{a.lang}"):
            try:
                place = [m.text or "" for m in gmd.parse(r.data).messages]
            except RiftError as e:
                print(f"{r.label}: {e}", file=sys.stderr)
                return 1
            break
    if not place:
        print(f"map_placelist_{a.lang} not found", file=sys.stderr)
        return 1

    rows, bad = [], []
    for r in corpus.resources(game, [typemap.BY_EXT["spn"]]):
        try:
            rows.append((base(r.label), rooms(r.data, place)))
        except RiftError as e:          # reported and left out, not a traceback that loses the whole table
            bad.append(f"{r.label}: {e}")
            print(f"skipped {r.label}: {e}", file=sys.stderr)

    out = ["# Stage -> room-name map (measured)", "",
           "Every stage's named sub-areas, from `rStagePlaceName` (`.spn`, decoded byte-exact by",
           "`flat.py`) whose `mPlaceNameId` indexes `id/DDN/message/common/map_placelist`. This is the",
           "definitive stage<->place mapping -- no guessing. Regenerate with `tools/gen_stage_map.py`.",
           "", f"{len(rows)} stages have a `.spn`; stages without one (e.g. many combat rooms) are not listed.",
           "", "| Stage | Named areas (map_placelist) |", "|---|---|"]
    for st, names in sorted(rows):
        out.append(f"| `{st}` | {', '.join(names) if names else '(no named sub-areas)'} |")
    if bad:
        out += ["", f"{len(bad)} `.spn` could not be read and are not listed: " + "; ".join(bad)]
    Path(a.out).write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"wrote {a.out} ({len(rows)} stages" + (f", {len(bad)} .spn skipped" if bad else "") + ")")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())

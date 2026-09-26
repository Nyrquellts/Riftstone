"""The monster census: which Dragon's Dogma Online enemy is which Dark Arisen enemy, and what converts.

    python tools/monster_census.py [--out reports/monster-census.json] [--no-textures] [--markdown FILE]

Reads both installed games through Riftstone's own readers (``src/riftstone/monsters.py``): every enemy
archive (DDDA ``rom/enemy``, DDO ``rom/EM``), its body model (joints by id, the joints its meshes are bound
to, vertex formats, material names), the family's motion lists (the joint ids they drive) and the textures
its material binds.  Every family is compared with every family of the other game; its counterpart gets
a verdict (same body / same skeleton / partial / none, the rules in monsters.py) and, with textures, the
texture check (the same sheet of both games at 64 px against the mirrored picture).

Writes the whole report as JSON (``reports/`` is git-ignored: the report holds skeletons and names read
from the owner's games), refreshes the cache ``riftstone monster list`` reads, and prints a summary.
``--markdown`` writes the matrix of derived facts (ids, names, counts, verdicts) that docs/monsters.md
carries.  Reads the games only.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from riftstone import monsters  # noqa: E402
from riftstone.game import find_game  # noqa: E402
from riftstone.index import Index  # noqa: E402


def _short(model: str) -> str:
    return model.rsplit("\\", 1)[-1]


def row(c: dict, kind: str, key: str) -> dict:
    """One family's line of the matrix: derived facts only."""
    f = c[kind]["families"][key]
    e = c["counterparts"][kind][key]
    b = c[kind]["bodies"][f["body"]]
    p = e["pair"] or {}
    target = e["counterpart"] or e.get("closest")
    g = c[monsters.other(kind)]["families"].get(target or "", {})
    back = c["counterparts"][monsters.other(kind)].get(target or "", {})
    return {"family": key, "name": f["name"], "enemies": [c[kind]["enemies"][a]["id"] for a in f["enemies"]],
            "names": f["names"], "joints": len(b["joints"]), "skinned": len(b["skinned"]),
            "counterpart": e["counterpart"], "counterpart_name": g.get("name", "") if e["counterpart"] else "",
            "verdict": p.get("verdict", "none") if e["counterpart"] else "none",
            "back": (back.get("pair") or {}).get("verdict") if back.get("counterpart") == key else None,
            "same": len(p.get("same", [])), "moved": len(p.get("moved", [])),
            "missing": len(p.get("missing", [])), "reparented": len(p.get("reparented", [])),
            "why": p.get("why", []), "textures": (e.get("counterpart_textures") or {}).get("verdict"),
            "namesake": e.get("namesake"), "namesake_verdict": (e.get("namesake_pair") or {}).get("verdict"),
            "closest": e.get("closest")}


def differs(c: dict, kind: str, key: str) -> str:
    """What differs, short: missing / re-parented joint ids, moved joints, foreign formats."""
    e = c["counterparts"][kind][key]
    p = e["pair"] or {}
    if not p:
        return "-"
    out = []
    if p.get("missing"):
        parts = p.get("missing_in_parts") or {}
        inp = sum(len(v) for v in parts.values())
        out.append(f"missing {monsters._ids(p['missing'])}"
                   + (f" ({inp} in its part models {', '.join(_short(k) for k in parts)})" if inp else ""))
    if p.get("reparented"):
        out.append(f"re-parented {monsters._ids(p['reparented'])}")
    if p.get("moved"):
        known = [m[1] for m in p["moved"] if m[1] is not None]
        out.append(f"{len(p['moved'])} moved" + (f" (up to {max(known):g} cm)" if known else ""))
    if p.get("formats_foreign"):
        out.append("vertex formats " + ", ".join(p["formats_foreign"]))
    if p.get("texture_formats_foreign"):
        out.append("texture formats " + ", ".join(map(str, p["texture_formats_foreign"])))
    if not e["counterpart"] and p.get("skinned", 0) < monsters.MIN_RIG:
        out.append(f"rigid ({p.get('skinned', 0)} bound joint(s))")
    elif not e["counterpart"]:
        out.append(f"{len(p.get('same', [])) + len(p.get('moved', []))} of {p.get('skinned', 0)} bound joints fit")
    return "; ".join(out) or "-"


def markdown(c: dict) -> str:
    out = []
    for kind in ("ddo", "ddda"):
        other = monsters.other(kind)
        out.append(f"### {monsters.title(kind)} -> {monsters.title(other)}\n")
        out.append("| Family | Enemy ids | Joints (bound) | Counterpart | Verdict | Back | Textures | What differs |")
        out.append("|---|---|---|---|---|---|---|---|")
        for key in sorted(c[kind]["families"]):
            r = row(c, kind, key)
            ids = ", ".join(r["enemies"][:3]) + (f" +{len(r['enemies']) - 3}" if len(r["enemies"]) > 3 else "")
            more = len([n for n in r["names"] if n != r["name"]])
            who = (r["name"] or "(no name)") + (f" +{more}" if more else "")
            cp = f"{r['counterpart']} {r['counterpart_name']}" if r["counterpart"] else f"- (closest {r['closest']})"
            if r["namesake"]:
                cp += f"; namesake {r['namesake']} {r['namesake_verdict']}"
            out.append(f"| {key} {who} | {ids} | {r['joints']} ({r['skinned']}) | {cp} | {r['verdict']} | "
                       f"{r['back'] or '-'} | {r['textures'] or '-'} | {differs(c, kind, key).replace('|', '/')} |")
        out.append("")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=HERE.parent / "reports" / "monster-census.json")
    ap.add_argument("--no-textures", action="store_true", help="skip the texture check (a few seconds instead of ~30)")
    ap.add_argument("--markdown", type=Path, help="also write the matrix of derived facts as Markdown here")
    ap.add_argument("--lang", default="eng", help="Dark Arisen's name language (default eng)")
    a = ap.parse_args()
    games = {k: find_game(k) for k in ("ddda", "ddo")}
    idxs = {}
    for k, g in games.items():
        idx = Index(g)
        if idx.pending():
            print(f"indexing {g.title} ...", flush=True)
            idx.refresh()
        idxs[k] = idx
    t0 = time.time()
    c = monsters.census(games, idxs, a.lang, monsters.TOLERANCE, not a.no_textures)
    monsters.save(c, monsters.cache_path(games))
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(c, ensure_ascii=False, indent=1), encoding="utf-8")
    for idx in idxs.values():
        idx.close()
    print(f"monster census in {time.time() - t0:.1f}s -> {a.out}")
    for kind in ("ddo", "ddda"):
        s = c["summary"][kind]
        print(f"\n{monsters.title(kind)}: {s['families']} families from {s['enemies']} enemy archives "
              f"({s['enemies_without_body']} hold no enemy body)")
        print("  " + ", ".join(f"{v}: {n}" for v, n in s["verdicts"].items()))
        if c.get("textures"):
            tx = Counter((e.get("counterpart_textures") or {}).get("verdict", "no counterpart")
                         for e in c["counterparts"][kind].values())
            print("  texture sheets of the counterpart pairs: " + ", ".join(f"{k} {n}" for k, n in tx.most_common()))
    both = []
    for key in sorted(c["ddo"]["families"]):
        r = row(c, "ddo", key)
        if r["verdict"] == "same body" and r["back"] == "same body":
            both.append(r)
    print(f"\nsame body both ways ({len(both)} pairs; each is the other's counterpart):")
    for r in both:
        print(f"  {r['family']:<9} {r['name'][:24]:<24} <-> {r['counterpart']:<6} {r['counterpart_name'][:22]:<22} "
              f"{r['skinned']:>3} bound joints, textures {r['textures'] or '-'}")
    if a.markdown:
        a.markdown.parent.mkdir(parents=True, exist_ok=True)
        a.markdown.write_text(markdown(c), encoding="utf-8")
        print(f"\nmatrix -> {a.markdown}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Read-only evidence for registration premises; never loads or starts the game."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from riftstone.game import find_game
from riftstone.index import home


def audit():
    game = find_game("ddda")
    data = game.exe.read_bytes()
    key = hashlib.sha1(str(game.root.resolve()).lower().encode()).hexdigest()[:12]
    index = home() / f"index-{key}.sqlite"
    db = sqlite3.connect(index.as_uri() + "?mode=ro", uri=True)
    names = ["item_parameter", "enemy_param", "quest_data", "itemlist", "itemname_", "iteminfo_", "charparam\\em"]
    resources = {}
    for name in names:
        rows = db.execute("SELECT DISTINCT arc,name,type FROM res WHERE name_lc LIKE ? ORDER BY arc,name", ("%"+name+"%",)).fetchall()
        resources[name] = {"matches": len(rows), "examples": [{"archive": a, "name": n.decode("latin-1"), "type": t} for a,n,t in rows[:20]]}
    literal = ["mEventActor", "uCharacterCutscene", "uCharacterBase", "uPlayer", "uNpc", "uNpcHuman"]
    strings = {name: [m.start() for m in re.finditer(re.escape(name.encode()+b"\0"), data)] for name in literal}
    candidates = sorted({m.group().decode("ascii") for m in re.finditer(rb"[A-Za-z_][A-Za-z_0-9:]{4,90}", data)
                         if any(w in m.group().lower() for w in (b"cutscene", b"eventactor", b"playercopy"))})[:50]
    return {"schema": "riftstone.registration-audit/1", "exe_sha256": hashlib.sha256(data).hexdigest(),
            "index": str(index), "resources": resources, "exact_nul_terminated_strings_file_offsets": strings,
            "candidate_strings": candidates, "claim": "static inventory only; strings do not verify an ABI, hook, or engine lookup capacity", "game_started": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(); report = audit()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as stream: json.dump(report, stream, indent=2)
    print(json.dumps({"resources": {k:v["matches"] for k,v in report["resources"].items()},
                      "strings": {k:len(v) for k,v in report["exact_nul_terminated_strings_file_offsets"].items()}, "out": str(args.out)}))

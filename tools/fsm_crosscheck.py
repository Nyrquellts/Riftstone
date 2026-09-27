"""Check fsmcheck's reachability with NYR-Lang's model checker on every state machine of a game.

    python tools/fsm_crosscheck.py [--game ddda|ddo|PATH] [--max-states 400] [--out report.json]

For every machine (the root machine and every sub-machine) with a start state, fsmcheck.model writes it
as a NYR-Lang formal FSM model and NYR-Lang's explicit checker (<path>, or $NYRLANG;
formal/fsm.py) explores it.  The states it PROVES are never entered must be exactly the states
fsmcheck.reachable leaves out.  Both start from fsmcheck's reading of the transition rules (the model
is built from it), so this checks the graph search and the model export, not the rules themselves:
those are checked against the game's own code by native/fsm_exec.  Reads the game only.  Exit 0 when
every compared machine agrees, 1 otherwise, 2 when NYR-Lang is not there.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from riftstone import corpus, fsmcheck, typemap, xfs  # noqa: E402
from riftstone.game import find_game  # noqa: E402


def nyrlang():
    root = Path(os.environ.get("NYRLANG", r"<path>"))
    if not (root / "formal" / "fsm.py").is_file():
        return None
    sys.path.insert(0, str(root))
    from formal import fsm as formal_fsm
    return formal_fsm


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game")
    ap.add_argument("--max-states", type=int, default=400, help="larger machines are counted, not explored")
    ap.add_argument("--out")
    a = ap.parse_args()
    formal = nyrlang()
    if formal is None:
        print("NYR-Lang's formal/fsm.py is not at <path> (set NYRLANG)")
        return 2
    game = find_game(a.game)
    t0 = time.time()
    counts = Counter()
    disagreements = []
    for r in corpus.resources(game, [typemap.BY_EXT["fsm"]]):
        counts["files"] += 1
        m = fsmcheck.read(xfs.parse(r.data))
        for lv in m.levels:
            g = fsmcheck._graph(m, lv)
            if g.start is None:
                counts["no start"] += 1
                continue
            if len(lv.states) > a.max_states:
                counts["too large"] += 1
                continue
            doc = fsmcheck.model(m, lv.index)
            doc["timeout_ms"] = 60000
            report = formal.check(doc)
            if not report.get("fixed_point"):
                counts["nyrlang " + report.get("status", "?")] += 1
                continue
            proved = {i for i, p in enumerate(report["properties"]) if p["status"] == "PROVED"}
            never = set(range(len(lv.states))) - fsmcheck.reachable(g)
            counts["machines compared"] += 1
            counts["states compared"] += len(lv.states)
            counts["never entered"] += len(never)
            if proved != never:
                disagreements.append({"resource": r.label, "machine": lv.index,
                                      "nyrlang_never": sorted(proved), "fsmcheck_never": sorted(never)})
    out = {"schema": "riftstone.fsm-crosscheck/1", "game": str(game.root), "counts": dict(counts),
           "disagreements": disagreements[:50], "seconds": round(time.time() - t0, 1),
           "claim": "NYR-Lang's explicit checker and fsmcheck agree on which states each machine's own "
                    "transitions never reach (static; in game UNKNOWN)"}
    text = json.dumps(out, indent=1, ensure_ascii=False)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "disagreements"}, indent=1, ensure_ascii=False))
    for d in disagreements[:10]:
        print("DISAGREE", json.dumps(d, ensure_ascii=False)[:400])
    return 1 if disagreements else 0


if __name__ == "__main__":
    raise SystemExit(main())

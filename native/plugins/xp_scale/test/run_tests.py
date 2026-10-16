"""Run the xp_scale harness against the owner's DDDA.exe (read-only; the game is not launched).

    python native/plugins/xp_scale/test/run_tests.py

xp_stub.exe maps DDDA.exe's image into its own process, xp_harness_core.dll loads the built plugin,
which verifies and patches that copy, and then runs the game's own sGameSys::addExp on a fake party
(the Arisen, the main pawn, two hired pawns; a kill and a quest's reward, Hard Mode, the level spread,
the experience ability, sGameSys's lock, the caps): each call through the patched entry must leave the
same state as the game's own code given the plugin's amount.  The level table is read from the
player's archives (param\\pl\\level\\exp.plexp, never stored here) and the game's getNextExp, run in the
harness, must give the costs this file's model gives for every level, and docs/re-xp.md's numbers.
Seven profiles: the shipped xp_scale.ini, kills only, the most, the game's own (nothing patched), two
bad values and a tampered exe.  Needs native\\plugins\\xp_scale\\build.cmd to have run; without the
build or without the game it reports a skip.  Exit status 0 = passed or skipped, 1 = failed.
"""
from __future__ import annotations

import math
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent
OUT = PLUGIN / "out"
sys.path.insert(0, str(HERE.parents[3] / "src"))

# profile -> (ini text, or None for the shipped one; kills, quests (per mille), level cost; what the log says)
PROFILES = {
    "shipped": (None, 1500, 1500, 125000, "xp_scale: Kills x1.500, Quests x1.500, LevelCost 125000 (harness)"),
    "kills_only": ("[xp]\nKills = 0.1\nQuests = 1.0\n[curve]\nLevelCost = 0\n", 100, 1000, 0,
                   "xp_scale: Kills x0.100, Quests x1.000, LevelCost 0 (harness)"),
    "max": ("[xp]\nKills = 2.0\nQuests = 2\n[curve]\nLevelCost = 1000\n", 2000, 2000, 1000,
            "xp_scale: Kills x2.000, Quests x2.000, LevelCost 1000 (harness)"),
    "vanilla": ("[xp]\nKills = 1.0\nQuests = 1\n[curve]\nLevelCost = 0\n", 1000, 1000, 0,
                "xp_scale: Kills = 1.0, Quests = 1.0, LevelCost = 0: nothing patched"),
    "bogus": ("[xp]\nKills = 3\n", 1000, 1000, 0, "Kills = 3 is not a number from 0.1 to 2.0; nothing is patched"),
    "bogus_cost": ("[curve]\nLevelCost = 500\n", 1000, 1000, 0,
                   "LevelCost = 500 is not 0 or a whole number from 1000 to 10000000; nothing is patched"),
    "tampered": (None, 1500, 1500, 125000, "refused: a kill's reward: raw = 0 at 0x00857514"),
}
# Lines the plugin writes once: the first kill, the first quest, the first reward LevelCost raised.
# A second copy of a patching plugin finds the entry patched (logged by that copy).
SECOND = "refused: sGameSys::addExp: its start"
ONCE = {
    "shipped": ("a kill's reward: ", "a quest's reward: ", "more than LevelCost 125000"),
    "kills_only": ("a kill's reward: ", "a quest's reward: "),
    "max": ("a kill's reward: ", "a quest's reward: ", "more than LevelCost 1000"),
}
# docs/re-xp.md: what the next level costs with the game's own table (exp.plexp as shipped).
DOC_COSTS = {1: 200, 2: 485, 10: 4640, 50: 80100, 51: 80980, 100: 124100, 101: 127400, 150: 289100, 151: 293500,
             199: 504700}
BUILT = ("xp_stub.exe", "xp_harness_core.dll", "xp_scale.asi")


def level_table() -> list[tuple[int, float]]:
    """(mLv, mRate) of every row of the game's param\\pl\\level\\exp.plexp, as the player's archives hold it."""
    from riftstone import modfiles, params
    from riftstone.game import find_game
    from riftstone.index import Index

    game = find_game("ddda")
    name = "param\\pl\\level\\exp"
    data, _ = modfiles.load(game, Index(game), None, name.encode(), 0x86B80F)
    text = params.resource_to_yaml(data, name, 0x86B80F)
    lv = [int(m) for m in re.findall(r"^\s*mLv:\s*(\d+)\s*$", text, re.M)]
    rate = [float(m) for m in re.findall(r"^\s*mRate:\s*([-0-9.eE+]+)\s*$", text, re.M)]
    if not lv or len(lv) != len(rate):
        raise ValueError("exp.plexp: no rows read")
    return list(zip(lv, rate))


def f32(x: float) -> float:
    return struct.unpack("<f", struct.pack("<f", x))[0]


def next_costs(rows: list[tuple[int, float]], top: int = 199) -> dict[int, int]:
    """getNextExp (0x00B5C080): level 1 costs row 0's level; each level after adds row 1's level x the rate of
    its band, truncated; the band moves on one row when the level reaches the next row's level.  The products
    and sums are exact in the x87's 53 and 64 bits, so whole fractions model it exactly."""
    rows = rows + [rows[-1]] * 40
    costs = {1: rows[0][0]}
    cost, base, band, nxt = rows[0][0], rows[1][0], 2, 3
    for level in range(2, top + 1):
        if level >= rows[nxt][0]:
            band, nxt = nxt, nxt + 1
        cost = math.floor(Fraction(f32(rows[band][1])) * base + cost)
        costs[level] = cost
    return costs


def run_profile(exe: Path, name: str, spec: tuple, table: str) -> int:
    ini, kills, quests, level_cost, says = spec
    work = Path(tempfile.mkdtemp(prefix=f"rs-xp-{name}-"))
    try:
        for n in BUILT:
            shutil.copy(OUT / n, work / n)
        if ini is None:
            shutil.copy(PLUGIN / "xp_scale.ini", work / "xp_scale.ini")
        else:
            (work / "xp_scale.ini").write_text(ini, encoding="utf-8")
        args = [str(work / "xp_stub.exe"), str(exe), str(work / "xp_scale.asi"), name, str(kills), str(quests),
                str(level_cost)]
        if table:
            args.append(table)
        r = subprocess.run(args, cwd=work, capture_output=True, text=True, timeout=120,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = r.stdout.rstrip()
        costs = {int(a): int(b) for a, b in re.findall(r"^cost (\d+) (\d+)$", out, re.M)}
        print("\n".join(line for line in out.splitlines() if not line.startswith("cost ")))
        log = work / "riftstone" / "logs" / "xp_scale.log"
        text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
        for line in text.strip().splitlines():
            print("  plugin log: " + line)
        if r.returncode not in (0, 1, 2):
            print(f"  FAIL  the harness ended with {r.returncode:#x}")
            return 1
        if r.returncode == 2:
            return 2
        failed = r.returncode != 0
        for want in (says, *ONCE.get(name, ())):
            if want not in text:
                print(f"  FAIL  the plugin log says {want!r}")
                failed = True
        if name in ONCE and SECOND not in text:
            print(f"  FAIL  the plugin log says {SECOND!r} (the second copy)")
            failed = True
        for line in ONCE.get(name, ()):
            if text.count(line) != 1:
                print(f"  FAIL  {line!r} is in the log {text.count(line)} times, not once")
                failed = True
        if table:
            if len(costs) != 199:
                print(f"  FAIL  the harness printed {len(costs)} level costs, not 199")
                failed = True
            else:
                model = next_costs([(int(a), float(b)) for a, b in (p.split(":") for p in table.split(","))])
                wrong = [lv for lv in costs if costs[lv] != model[lv]]
                print(f"  {'pass' if not wrong else 'FAIL'}  the game's getNextExp gives this file's model's cost "
                      f"at all 199 levels" + (f" (not at {wrong[:5]})" if wrong else ""))
                failed |= bool(wrong)
                if name == "shipped":
                    doc = [lv for lv, c in DOC_COSTS.items() if costs[lv] != c]
                    if doc:
                        print(f"  note  the table is not the one docs/re-xp.md measured (levels {doc} differ)")
                    else:
                        print("  pass  docs/re-xp.md's costs: " +
                              ", ".join(f"{lv} {c:,}" for lv, c in DOC_COSTS.items()))
        return 1 if failed else 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    if not all((OUT / n).is_file() for n in BUILT):
        print("xp_scale not built; run native\\plugins\\xp_scale\\build.cmd to include it")
        return 0
    try:
        from riftstone.game import find_game
        exe = find_game().root / "DDDA.exe"
    except Exception as e:  # noqa: BLE001 -- no game on this machine
        print(f"xp_scale harness skipped: {e}")
        return 0
    if not exe.is_file():
        print("xp_scale harness skipped: DDDA.exe not found")
        return 0
    try:
        rows = level_table()
        table = ",".join(f"{lv}:{rate!r}" for lv, rate in rows)
    except Exception as e:  # noqa: BLE001 -- archives unreadable here: the stand-in table
        print(f"the game's level table could not be read ({e}); the harness's stand-in table is used")
        table = ""
    print("xp_scale harness (the real DDDA.exe code, mapped read-only; no game launched)")
    failed = False
    for name, spec in PROFILES.items():
        print(f"\n[{name}]")
        code = run_profile(exe, name, spec, table)
        if code == 2:
            print("xp_scale harness skipped: the image could not be mapped in this process")
            return 0
        failed |= code != 0
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

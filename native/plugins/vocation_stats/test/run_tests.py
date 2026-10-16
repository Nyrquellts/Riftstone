"""Run the vocation_stats harness against the owner's DDDA.exe (read-only; the game is not launched).

    python native/plugins/vocation_stats/test/run_tests.py

vocation_stats_stub.exe maps DDDA.exe's image into its own process, vocation_stats_harness_core.dll loads
the built plugin, which verifies and patches that copy, and then rests at an inn the way uInn's state
0x00736740 does, through both rest calls, on a fake party: the Arisen, the main pawn and two hired pawns
whose stats grew in another vocation.  The game's own setLevel, level-up and getNextExp run; the rest
routines and three helpers are stand-ins.  The level tables are read from the player's archives
(param\\pl\\level\\Lv<vocation>.lvl and exp.plexp, never stored here), and the numbers the game's code
gives for every vocation at the levels where its bands change must be this file's model's (the base row,
then each level's band, HP capped at 9999), and docs/vocation-stats.md's.  Eight profiles: the shipped
vocation_stats.ini, the Arisen only, the main pawn only, off, two bad values, a tampered exe, and tables
with enough HP growth to reach the cap.  Needs native\\plugins\\vocation_stats\\build.cmd to have run;
without the build or without the game it reports a skip.  Exit status 0 = passed or skipped, 1 = failed.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent
OUT = PLUGIN / "out"
sys.path.insert(0, str(HERE.parents[3] / "src"))

# The level tables by the game's vocation number (sPlayerManager +0x86C + 4 x the number).
TABLES = ("Fighter", "Strider", "MagicUser", "Paladin", "Assassin", "Trickster", "Battler", "Hunter", "Wizard")
LV_TYPE, EXP_TYPE = 0x354284E7, 0x86B80F
HP_CAP = 9999

# profile -> (ini text, or None for the shipped one; patched, who; what the log says; the tables' HP x)
PROFILES = {
    "shipped": (None, 1, "party", "vocation_stats: Mode = current, Who = party (harness)", 1),
    "arisen": ("[vocation_stats]\nMode = current\nWho = arisen\n", 1, "arisen",
               "vocation_stats: Mode = current, Who = arisen (harness)", 1),
    "pawn": ("[vocation_stats]\nMode = Current\nWho = pawn\n", 1, "pawn",
             "vocation_stats: Mode = current, Who = pawn (harness)", 1),
    "off": ("[vocation_stats]\nMode = off\n", 0, "party", "vocation_stats: Mode = off: nothing patched", 1),
    "bogus": ("[vocation_stats]\nMode = sometimes\n", 0, "party",
              "Mode = sometimes is not current or off; nothing is patched", 1),
    "bogus_who": ("[vocation_stats]\nWho = everyone\n", 0, "party",
                  "Who = everyone is not party, arisen or pawn; nothing is patched", 1),
    "tampered": (None, 0, "party", "refused: level-up: its refusals at 0x00B5BD4C", 1),
    "edge": (None, 1, "party", "vocation_stats: Mode = current, Who = party (harness)", 8),
}
SECOND = "refused: uInn's rest state: the two rest calls at 0x0073674E"
# Lines a recalculating profile's log holds: a recalculation, and each kind of character left alone.
LEFT = ("left as it is: setLevel leaves it (flag 8 at +0x206D)", "left as it is: the level-up refuses it (+0x31EA)",
        "left as it is: no cPlayerInfo", "left as it is: no vocation", "left as it is: a level outside 1..200",
        "left as it is: no level table for its vocation", "left as it is: a level table without its rows",
        "left as it is: no character in the slot", "rest: no player manager; nothing changed")
# docs/vocation-stats.md: a character that gained every level in one vocation, with the game's own tables
# (HP, stamina, attack, defence, magick attack, magick defence).
DOC = {(7, 200): (4820, 2580, 766, 577, 258, 168), (9, 200): (3048, 2530, 258, 177, 866, 657),
       (5, 200): (3135, 4155, 937, 377, 277, 178), (1, 100): (4050, 1530, 476, 467, 258, 168),
       (3, 10): (608, 180, 78, 87, 116, 107)}
BUILT = ("vocation_stats_stub.exe", "vocation_stats_harness_core.dll", "vocation_stats.asi")


def read_tables() -> tuple[dict[int, list[tuple[int, ...]]], list[tuple[int, float]]]:
    """The nine level tables {vocation: [(mLevel, HP, stamina, attack, defence, m. attack, m. defence)]} and
    the experience table [(mLv, mRate)], as the player's archives hold them."""
    from riftstone import modfiles, params
    from riftstone.game import find_game
    from riftstone.index import Index

    game = find_game("ddda")
    index = Index(game)
    fields = ("mLevel", "mHPMax", "mStaminaMax", "mAttack", "mDefend", "mMgcAttack", "mMgcDefend")
    row = re.compile(r"\s+".join(rf"{f}:\s*(\d+)" for f in fields))
    levels = {}
    for job, name in enumerate(TABLES, 1):
        res = f"param\\pl\\level\\Lv{name}"
        data, _ = modfiles.load(game, index, None, res.encode(), LV_TYPE)
        rows = [tuple(int(v) for v in m) for m in row.findall(params.resource_to_yaml(data, res, LV_TYPE))]
        if len(rows) < 3:
            raise ValueError(f"Lv{name}: {len(rows)} rows read")
        levels[job] = rows
    res = "param\\pl\\level\\exp"
    data, _ = modfiles.load(game, index, None, res.encode(), EXP_TYPE)
    text = params.resource_to_yaml(data, res, EXP_TYPE)
    lv = [int(m) for m in re.findall(r"^\s*mLv:\s*(\d+)\s*$", text, re.M)]
    rate = [float(m) for m in re.findall(r"^\s*mRate:\s*([-0-9.eE+]+)\s*$", text, re.M)]
    if len(lv) < 4 or len(lv) != len(rate):
        raise ValueError("exp.plexp: no rows read")
    return levels, list(zip(lv, rate))


def model(rows: list[tuple[int, ...]], level: int) -> tuple[int, ...]:
    """A character that gained every level in this vocation: the base row (level 1), then for each level
    from 2 the growth of the last row whose first level is at most it; HP stops at 9999."""
    stats = list(rows[1][1:])
    for lv in range(2, level + 1):
        band = [r for r in rows[1:] if r[0] <= lv][-1]
        stats[0] = min(stats[0] + band[1], HP_CAP)
        for k in range(1, 6):
            stats[k] += band[k + 1]
    return tuple(stats)


def scaled(levels: dict[int, list[tuple[int, ...]]], hp: int) -> dict[int, list[tuple[int, ...]]]:
    """The tables with every band's HP growth (not the base row's) times hp."""
    return {j: [r if i < 2 else (r[0], r[1] * hp, *r[2:]) for i, r in enumerate(rows)] for j, rows in levels.items()}


def write_tables(path: Path, levels, exp) -> None:
    lines = [f"lv {j} " + " ".join(str(v) for v in r) for j, rows in levels.items() for r in rows]
    lines += [f"exp {lv} {rate!r}" for lv, rate in exp]
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


def run_profile(exe: Path, name: str, spec: tuple, tables) -> int:
    ini, patched, who, says, hp = spec
    work = Path(tempfile.mkdtemp(prefix=f"rs-voc-{name}-"))
    try:
        for n in BUILT:
            shutil.copy(OUT / n, work / n)
        if ini is None:
            shutil.copy(PLUGIN / "vocation_stats.ini", work / "vocation_stats.ini")
        else:
            (work / "vocation_stats.ini").write_text(ini, encoding="utf-8")
        args = [str(work / "vocation_stats_stub.exe"), str(exe), str(work / "vocation_stats.asi"), name,
                str(patched), who]
        levels = None
        if tables:
            levels = scaled(tables[0], hp) if hp != 1 else tables[0]
            write_tables(work / "tables.txt", levels, tables[1])
            args.append(str(work / "tables.txt"))
        try:
            r = subprocess.run(args, cwd=work, capture_output=True, text=True, timeout=180,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            print("  FAIL  the harness did not finish in 180 s (setLevel looping on a character it should have left?)")
            return 1
        out = r.stdout.rstrip()
        stats = {(int(m[0]), int(m[1])): tuple(int(v) for v in m[2:])
                 for m in re.findall(r"^stats (\d+) (\d+) (\d+) (\d+) (\d+) (\d+) (\d+) (\d+)$", out, re.M)}
        print("\n".join(line for line in out.splitlines() if not line.startswith("stats ")))
        log = work / "riftstone" / "logs" / "vocation_stats.log"
        text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
        lines = text.strip().splitlines()
        for line in lines[:12]:
            print("  plugin log: " + line)
        if len(lines) > 12:
            print(f"  plugin log: ... ({len(lines) - 12} more lines)")
        if r.returncode not in (0, 1, 2):
            print(f"  FAIL  the harness ended with {r.returncode:#x}")
            return 1
        if r.returncode == 2:
            return 2
        failed = r.returncode != 0
        wants = [says]
        if patched:
            wants += [SECOND, "rest (A): the Arisen, Warrior level 120: HP " if who != "pawn" else
                      "rest (A): the main pawn, Mage level 118: HP "]
            if who == "party":
                wants += LEFT
        for want in wants:
            if want not in text:
                print(f"  FAIL  the plugin log says {want!r}")
                failed = True
        if not patched and "rest (" in text:
            print("  FAIL  the plugin logged a rest although it patched nothing")
            failed = True
        if levels is not None:
            expect_lines = 90 if patched and who != "pawn" else 0
            if len(stats) != expect_lines:
                print(f"  FAIL  the harness printed {len(stats)} vocation/level numbers, not {expect_lines}")
                failed = True
            wrong = [k for k, v in stats.items() if model(levels[k[0]], k[1]) != v]
            if stats:
                print(f"  {'pass' if not wrong else 'FAIL'}  the game's code gives this file's model's stats for all "
                      f"{len(stats)} vocation/level pairs" + (f" (not for {wrong[:4]})" if wrong else ""))
            failed |= bool(wrong)
            if stats and hp != 1:
                capped = [k for k, v in stats.items() if v[0] == HP_CAP]
                print(f"  {'pass' if capped else 'FAIL'}  HP stops at 9999 ({len(capped)} pairs reach it)")
                failed |= not capped
            if stats and name == "shipped":
                doc = [k for k, v in DOC.items() if stats.get(k) != v]
                if doc:
                    print(f"  note  the tables are not the ones docs/vocation-stats.md measured ({doc} differ)")
                else:
                    print("  pass  docs/vocation-stats.md's numbers: " +
                          "; ".join(f"vocation {j} level {lv} {v}" for (j, lv), v in DOC.items()))
        return 1 if failed else 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    if not all((OUT / n).is_file() for n in BUILT):
        print("vocation_stats not built; run native\\plugins\\vocation_stats\\build.cmd to include it")
        return 0
    try:
        from riftstone.game import find_game
        exe = find_game().root / "DDDA.exe"
    except Exception as e:  # noqa: BLE001 -- no game on this machine
        print(f"vocation_stats harness skipped: {e}")
        return 0
    if not exe.is_file():
        print("vocation_stats harness skipped: DDDA.exe not found")
        return 0
    try:
        tables = read_tables()
    except Exception as e:  # noqa: BLE001 -- archives unreadable here: the stand-in tables
        print(f"the game's level tables could not be read ({e}); the harness's stand-in tables are used")
        tables = None
    print("vocation_stats harness (the real DDDA.exe code, mapped read-only; no game launched)")
    failed = False
    for name, spec in PROFILES.items():
        print(f"\n[{name}]")
        code = run_profile(exe, name, spec, tables)
        if code == 2:
            print("vocation_stats harness skipped: the image could not be mapped in this process")
            return 0
        failed |= code != 0
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

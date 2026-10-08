r"""Run the pool_cap harness against the owner's DDDA.exe (read-only; the game is not launched).

    python native/plugins/pool_cap/test/run_tests.py

pool_cap_stub.exe maps DDDA.exe's image into its own process, pool_cap_harness_core.dll loads the built
plugin, which verifies and patches that copy, and then runs the game's own start-up pool builder (0x00740590)
and its allocator: with the shipped pool_cap.ini, with the game's own sizes, with the Unit pool at its most
(1024 MiB), with values below and above the allowed ones (each clamped), with sizes that add up to too much
(the defaults are used), with a process that cannot get more than 128 MiB at once (the Unit pool is made at the
game's size), once with an altered instruction and once with the pools already made (loaded too late), both
of which the plugin must refuse.  Needs native\plugins\pool_cap\build.cmd to have run; without the build or
without the game it reports a skip.  Exit status 0 = passed or skipped.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "out"
sys.path.insert(0, str(HERE.parents[3] / "src"))

KEYS = ("temp", "system", "unit", "effect", "gui", "array_string", "collision", "physics")
GAME = dict(temp=64, system=64, unit=64, effect=5, gui=5, array_string=6, collision=24, physics=12)
DEFAULT = dict(temp=64, system=64, unit=256, effect=20, gui=5, array_string=24, collision=96, physics=48)

# (name, ini values (None = the shipped pool_cap.ini), the sizes the pools must get, mode, what the log must say)
CASES = [
    ("shipped ini", None, DEFAULT, "", ["pool_cap: Unit 256 MiB (the game's 64), Physics 48 (12), Collision 96 (24)"]),
    ("the game's sizes", GAME, GAME, "", ["pool_cap: Unit 64 MiB (the game's 64)", "0 more than the game's"]),
    ("Unit at its most", dict(GAME, unit=1024), dict(GAME, unit=1024), "", ["pool_cap: Unit 1024 MiB"]),
    ("Unit below the game's", {"unit": 10}, dict(DEFAULT, unit=64), "", ["unit = 10 is outside 64..1024 (MiB); using 64"]),
    ("Unit above its most", dict(GAME, unit=5000), dict(GAME, unit=1024), "",
     ["unit = 5000 is outside 64..1024 (MiB); using 1024"]),
    ("too much in all", dict(GAME, unit=1024, collision=512), DEFAULT, "",
     ["the pools add up to 1692 MiB, more than 1280; using the defaults"]),
    ("no 256 MiB to be had", None, dict(DEFAULT, unit=64), "nomem",
     ["the Unit pool could not get 256 MiB (VirtualAlloc refused", "made at the game's 64 MiB instead"]),
    ("an altered instruction", None, DEFAULT, "tamper",
     ["refused: the Unit pool's size at 0x00740669 is not build 2364871's"]),
    ("loaded too late", None, DEFAULT, "late", ["refused: the game's memory pools already exist"]),
]


def log_problems(log: str, wants: list[str], sizes: dict, mode: str) -> list[str]:
    """What the plugin's log should say in this case, and does not."""
    problems = [f"missing from the log: {w!r}" for w in wants if w not in log]
    if mode in ("tamper", "late"):
        if "pool_cap: Unit" in log:
            problems.append("the log says the plugin patched")
        return problems
    if "at exit" not in log:
        problems.append("no summary at exit")
    unit = f"Unit         {sizes['unit']:4d} MiB, requests reach"
    if unit not in log:
        problems.append(f"the summary has no {unit!r}")
    elif "7 refused" not in log.split(unit, 1)[1].splitlines()[0]:
        problems.append("the summary does not count the Unit pool's seven refusals")
    # The real ragdoll setup, refused at its first array (1,200,000 bytes, the call returning to 0x0108333B), at
    # its third (768,000 bytes, 0x010833E1) and, as a goblin's ragdoll, at its first (80 bytes): each named as the
    # ragdoll setup and counted.
    for size, at in ((1200000, "0x0108333B"), (768000, "0x010833E1"), (80, "0x0108333B")):
        named = f"refused {size} bytes (refusal"
        if not any(named in ln and f"asked by {at}" in ln and "the ragdoll setup, so a ragdoll has no body data" in ln
                   for ln in log.splitlines()):
            problems.append(f"the log names no refusal of {size} bytes as the ragdoll setup's at {at}")
    if "the ragdoll setup was refused 3 requests" not in log:
        problems.append("the summary does not count the ragdoll setup's three refusals")
    return problems


def main() -> int:
    need = [OUT / n for n in ("pool_cap_stub.exe", "pool_cap_harness_core.dll", "pool_cap.asi", "pool_cap.ini")]
    if not all(p.is_file() for p in need):
        print(r"pool_cap not built; run native\plugins\pool_cap\build.cmd to include it")
        return 0
    try:
        from riftstone.game import find_game
        exe = find_game().root / "DDDA.exe"
    except Exception as e:  # noqa: BLE001 -- no game on this machine
        print(f"pool_cap harness skipped: {e}")
        return 0
    if not exe.is_file():
        print("pool_cap harness skipped: DDDA.exe not found")
        return 0
    print("pool_cap harness (the real DDDA.exe code, mapped read-only; no game launched)")
    failed = 0
    for name, values, sizes, mode, wants in CASES:
        work = Path(tempfile.mkdtemp(prefix="rs-poolcap-"))
        try:
            for p in need:
                shutil.copy(p, work / p.name)
            if values is not None:
                text = "[pool_cap]\n" + "".join(f"{k} = {v}\n" for k, v in values.items())
                (work / "pool_cap.ini").write_text(text, encoding="ascii")
            expect = ",".join(str(sizes[k]) for k in KEYS)
            args = [str(work / "pool_cap_stub.exe"), str(exe), str(work / "pool_cap.asi"), mode or "normal", expect]
            r = subprocess.run(args, cwd=work, capture_output=True, text=True, timeout=300,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            print(f"-- {name}{' (' + mode + ')' if mode else ''}: {expect}")
            print(r.stdout.rstrip())
            if r.stderr.strip():
                print(r.stderr.rstrip())
            path = work / "riftstone" / "logs" / "pool_cap.log"
            log = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
            for line in log.strip().splitlines():
                print("  plugin log: " + line)
            if r.returncode == 2:
                print("pool_cap harness skipped: the image could not be mapped in this process")
                return 0
            if r.returncode not in (0, 1):
                print(f"  FAIL  the harness ended with code {r.returncode:#x}")
            problems = log_problems(log, wants, sizes, mode)
            for p in problems:
                print("  FAIL  " + p)
            if not problems:
                print("  pass  the log says what was done")
            failed += (r.returncode != 0) or bool(problems)
        finally:
            shutil.rmtree(work, ignore_errors=True)
    print("pool_cap harness: " + ("all cases passed" if not failed else f"{failed} case(s) FAILED"))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())

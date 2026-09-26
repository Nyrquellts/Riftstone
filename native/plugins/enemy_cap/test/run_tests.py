"""Run the enemy_cap harness against the owner's DDDA.exe (read-only; the game is not launched).

    python native/plugins/enemy_cap/test/run_tests.py

cap_stub.exe maps DDDA.exe's image into its own process, cap_harness_core.dll loads the built plugin,
which verifies and patches that copy, and then runs the patched game code on a fake spawn manager --
with 30 slots (the default), 12, 64 (the most allowed) and 5 (below the minimum: 10 are used), once
with the slot record off, once with an altered instruction and once with the game's spawn manager
already built (loaded too late), both of which the plugin must refuse.  After
each run the plugin's log must hold what the slot record saw (peaks, all slots in use, the stage) and
the line it writes when the process exits.  Needs native\\plugins\\enemy_cap\\build.cmd to have run;
without the build or without the game it reports a skip.  Exit status 0 = passed or skipped.
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

CASES = [(30, 30, ""), (12, 12, ""), (64, 64, ""), (5, 10, ""), (30, 30, "norecord"), (30, 30, "tamper"),
         (30, 30, "late")]


def record_problems(log: str, slots: int, mode: str) -> list[str]:
    """What the slot record should have written in this case, and is missing (or should not be there)."""
    if mode in ("tamper", "late"):
        problems = ["a refused plugin wrote a slot record"] if "exiting normally" in log else []
        if mode == "late" and "refused: the game's spawn manager already exists" not in log:
            problems.append("the log does not say why it refused")
        return problems
    if mode == "norecord":
        return [f"record = 0, yet the log has {w!r}" for w in ("peak:", "exiting normally") if w in log]
    want = ["slot record on",
            f"0 of {slots} slots in use (0 with a unit), stage 100",
            f"peak: 3 of {slots} slots in use (1 with a unit), stage 100",
            f"peak: {slots} of {slots} slots in use (1 with a unit), stage 100",
            f"all {slots} slots in use, stage 100: more enemies wait for a free slot",
            "the game is exiting normally",
            f"{slots - 1} of {slots} slots in use (1 with a unit), stage 100. Peak {slots} at"]
    return [f"missing from the log: {w!r}" for w in want if w not in log]


def main() -> int:
    need = [OUT / n for n in ("cap_stub.exe", "cap_harness_core.dll", "enemy_cap.asi")]
    if not all(p.is_file() for p in need):
        print("enemy_cap not built; run native\\plugins\\enemy_cap\\build.cmd to include it")
        return 0
    try:
        from riftstone.game import find_game
        exe = find_game().root / "DDDA.exe"
    except Exception as e:  # noqa: BLE001 -- no game on this machine
        print(f"enemy_cap harness skipped: {e}")
        return 0
    if not exe.is_file():
        print("enemy_cap harness skipped: DDDA.exe not found")
        return 0
    print("enemy_cap harness (the real DDDA.exe code, mapped read-only; no game launched)")
    failed = 0
    for slots, expect, mode in CASES:
        work = Path(tempfile.mkdtemp(prefix="rs-cap-"))
        try:
            for p in need:
                shutil.copy(p, work / p.name)
            record = "record = 0\n" if mode == "norecord" else ""
            (work / "enemy_cap.ini").write_text(f"[enemy_cap]\nslots = {slots}\n{record}", encoding="ascii")
            args = [str(work / "cap_stub.exe"), str(exe), str(work / "enemy_cap.asi"), str(expect)] + ([mode] if mode else [])
            r = subprocess.run(args, cwd=work, capture_output=True, text=True, timeout=120,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            print(f"-- slots = {slots}{' (' + mode + ')' if mode else ''}")
            print(r.stdout.rstrip())
            path = work / "riftstone" / "logs" / "enemy_cap.log"
            log = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
            for line in log.strip().splitlines():
                print("  plugin log: " + line)
            if r.returncode == 2:
                print("enemy_cap harness skipped: the image could not be mapped in this process")
                return 0
            problems = record_problems(log, expect, mode)
            for p in problems:
                print("  FAIL  " + p)
            if not problems and mode not in ("tamper", "late"):
                print("  pass  the log holds the slot record" + (" (none: record = 0)" if mode == "norecord" else
                                                                 " and the exit line"))
            failed += (r.returncode != 0) or bool(problems)
        finally:
            shutil.rmtree(work, ignore_errors=True)
    print("enemy_cap harness: " + ("all cases passed" if not failed else f"{failed} case(s) FAILED"))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())

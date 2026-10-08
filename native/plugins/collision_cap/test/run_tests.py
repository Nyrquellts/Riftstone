"""Run the collision_cap harness against the owner's DDDA.exe (read-only; the game is not launched).

    python native/plugins/collision_cap/test/run_tests.py

collision_cap_stub.exe maps DDDA.exe's image into its own process, collision_cap_harness_core.dll loads the
built plugin, which verifies and patches that copy, and then runs the patched game code on a fake collision
manager -- with 4096 entry nodes (the default), 800 (the game's own number: only the sweep clamp changes
anything), 1024, 16384 (the most allowed), 100 (below the minimum: 800 are used) and 99999 (above: 16384),
once with an altered instruction and once with the game's manager already built (loaded too late), both of
which the plugin must refuse.  Needs native\\plugins\\collision_cap\\build.cmd to have run; without the build
or without the game it reports a skip.  Exit status 0 = passed or skipped.
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

CASES = [(4096, 4096, ""), (800, 800, ""), (1024, 1024, ""), (16384, 16384, ""), (100, 800, ""), (99999, 16384, ""),
         (4096, 4096, "tamper"), (4096, 4096, "late")]


def log_problems(log: str, nodes: int, mode: str) -> list[str]:
    """What the plugin's log should say in this case, and does not."""
    if mode == "tamper":
        return [] if "refused: the instruction at 0x00478A5D is not build 2364871's" in log else ["the log does not name the altered instruction"]
    if mode == "late":
        return [] if "refused: the game's collision manager already exists" in log else ["the log does not say why it refused"]
    want = f"collision_cap: {nodes} collision entry nodes a frame (the game's table holds 800)"
    return [] if want in log else [f"missing from the log: {want!r}"]


def main() -> int:
    need = [OUT / n for n in ("collision_cap_stub.exe", "collision_cap_harness_core.dll", "collision_cap.asi")]
    if not all(p.is_file() for p in need):
        print("collision_cap not built; run native\\plugins\\collision_cap\\build.cmd to include it")
        return 0
    try:
        from riftstone.game import find_game
        exe = find_game().root / "DDDA.exe"
    except Exception as e:  # noqa: BLE001 -- no game on this machine
        print(f"collision_cap harness skipped: {e}")
        return 0
    if not exe.is_file():
        print("collision_cap harness skipped: DDDA.exe not found")
        return 0
    print("collision_cap harness (the real DDDA.exe code, mapped read-only; no game launched)")
    failed = 0
    for nodes, expect, mode in CASES:
        work = Path(tempfile.mkdtemp(prefix="rs-colcap-"))
        try:
            for p in need:
                shutil.copy(p, work / p.name)
            (work / "collision_cap.ini").write_text(f"[collision_cap]\nentry_nodes = {nodes}\n", encoding="ascii")
            args = [str(work / "collision_cap_stub.exe"), str(exe), str(work / "collision_cap.asi"), str(expect)] + ([mode] if mode else [])
            r = subprocess.run(args, cwd=work, capture_output=True, text=True, timeout=300,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            print(f"-- entry_nodes = {nodes}{' (' + mode + ')' if mode else ''}")
            print(r.stdout.rstrip())
            path = work / "riftstone" / "logs" / "collision_cap.log"
            log = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
            for line in log.strip().splitlines():
                print("  plugin log: " + line)
            if r.returncode == 2:
                print("collision_cap harness skipped: the image could not be mapped in this process")
                return 0
            problems = log_problems(log, expect, mode)
            for p in problems:
                print("  FAIL  " + p)
            if not problems:
                print("  pass  the log says what was done")
            failed += (r.returncode != 0) or bool(problems)
        finally:
            shutil.rmtree(work, ignore_errors=True)
    print("collision_cap harness: " + ("all cases passed" if not failed else f"{failed} case(s) FAILED"))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())

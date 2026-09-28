"""Run the stage_enemies harness against the owner's DDDA.exe (read-only; the game is not launched).

    python native/plugins/stage_enemies/test/run_tests.py

stage_enemies_stub.exe maps DDDA.exe's image into its own process, stage_enemies_harness_core.dll loads
the built plugin (which reads its ini, resolves the enemies to archive tags through the exe's own table,
verifies the stage loader and patches that copy), then checks tag resolution against the real archive
table, the dispatch through a recorder, and the real thunk entered at the stage loader with a fake frame.
Two profiles: the shipped stage_enemies.ini (370 = em5301) and Enabled = 0.  Needs
native\\plugins\\stage_enemies\\build.cmd to have run; without the build or without the game it reports a
skip.  Exit status 0 = passed or skipped, 1 = failed.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent
OUT = PLUGIN / "out"
sys.path.insert(0, str(HERE.parents[3] / "src"))

PROFILES = {
    "on": None,  # the shipped stage_enemies.ini (370 = em5301)
    "off": "[stage_enemies]\nEnabled = 0\n370 = em5301\n",
}


def run_profile(exe: Path, name: str, ini: str | None) -> int:
    work = Path(tempfile.mkdtemp(prefix=f"rs-stage-enemies-{name}-"))
    try:
        for n in ("stage_enemies_stub.exe", "stage_enemies_harness_core.dll", "stage_enemies.asi"):
            shutil.copy(OUT / n, work / n)
        if ini is None:
            shutil.copy(PLUGIN / "stage_enemies.ini", work / "stage_enemies.ini")
        else:
            (work / "stage_enemies.ini").write_text(ini, encoding="utf-8")
        r = subprocess.run([str(work / "stage_enemies_stub.exe"), str(exe), str(work / "stage_enemies.asi"), name],
                           cwd=work, capture_output=True, text=True, timeout=120,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        print(r.stdout.rstrip())
        log = work / "riftstone" / "logs" / "stage_enemies.log"
        if log.is_file():
            for line in log.read_text(encoding="utf-8", errors="replace").strip().splitlines():
                print("  plugin log: " + line)
        return r.returncode
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    need = [OUT / n for n in ("stage_enemies_stub.exe", "stage_enemies_harness_core.dll", "stage_enemies.asi")]
    if not all(p.is_file() for p in need):
        print("stage_enemies not built; run native\\plugins\\stage_enemies\\build.cmd to include it")
        return 0
    try:
        from riftstone.game import find_game
        exe = find_game().root / "DDDA.exe"
    except Exception as e:  # noqa: BLE001 -- no game on this machine
        print(f"stage_enemies harness skipped: {e}")
        return 0
    if not exe.is_file():
        print("stage_enemies harness skipped: DDDA.exe not found")
        return 0
    print("stage_enemies harness (the real DDDA.exe code, mapped read-only; no game launched)")
    failed = False
    for name, ini in PROFILES.items():
        print(f"\n[{name}]")
        code = run_profile(exe, name, ini)
        if code == 2:
            print("stage_enemies harness skipped: the image could not be mapped in this process")
            return 0
        failed |= code != 0
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

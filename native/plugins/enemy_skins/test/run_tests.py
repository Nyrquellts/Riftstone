"""Run the enemy_skins harness against the owner's DDDA.exe (read-only; the game is not launched).

    python native/plugins/enemy_skins/test/run_tests.py

skins_stub.exe maps DDDA.exe's image into its own process, skins_harness_core.dll loads the built
plugin, which verifies and patches that copy, and then drives the real game code at every hooked
site with fake chimeras.  Needs native\\plugins\\enemy_skins\\build.cmd to have run; without the build
or without the game it reports a skip.  Exit status 0 = passed or skipped, 1 = failed.
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


def main() -> int:
    need = [OUT / n for n in ("skins_stub.exe", "skins_harness_core.dll", "enemy_skins.asi")]
    if not all(p.is_file() for p in need):
        print("enemy_skins not built; run native\\plugins\\enemy_skins\\build.cmd to include it")
        return 0
    try:
        from riftstone.game import find_game
        exe = find_game().root / "DDDA.exe"
    except Exception as e:  # noqa: BLE001 -- no game on this machine
        print(f"enemy_skins harness skipped: {e}")
        return 0
    if not exe.is_file():
        print("enemy_skins harness skipped: DDDA.exe not found")
        return 0
    work = Path(tempfile.mkdtemp(prefix="rs-skins-"))
    try:
        for p in need:
            shutil.copy(p, work / p.name)
        r = subprocess.run([str(work / "skins_stub.exe"), str(exe), str(work / "enemy_skins.asi")], cwd=work,
                           capture_output=True, text=True, timeout=120,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        print("enemy_skins harness (the real DDDA.exe code, mapped read-only; no game launched)")
        print(r.stdout.rstrip())
        log = work / "riftstone" / "logs" / "enemy_skins.log"
        if log.is_file():
            print("  plugin log: " + log.read_text(encoding="utf-8", errors="replace").strip())
        if r.returncode == 2:
            print("enemy_skins harness skipped: the image could not be mapped in this process")
            return 0
        return 0 if r.returncode == 0 else 1
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

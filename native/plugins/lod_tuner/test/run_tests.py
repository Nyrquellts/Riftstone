"""Run the lod_tuner harness against the owner's DDDA.exe (read-only; the game is not launched).

    python native/plugins/lod_tuner/test/run_tests.py

lod_stub.exe maps DDDA.exe's image into its own process, lod_harness_core.dll loads the built plugin,
which verifies and patches that copy, and then drives rModel::load's patched store and two of the
game's LOD readers with fake models.  Three profiles: the shipped lod_tuner.ini against a stand-in
game config.ini (2560x1440, ViewRange FARTHEST), a flat multiplier with a low cap, and Enabled=0.
The plugin never sees the owner's real config.ini here: LOCALAPPDATA points at the temp folder.
Needs native\\plugins\\lod_tuner\\build.cmd to have run; without the build or without the game it
reports a skip.  Exit status 0 = passed or skipped, 1 = failed.
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

GAME_CONFIG = """[GRAPHICS]
CameraFov=0.000000
ViewRange=FARTHEST
[DISPLAY]
Resolution=2560x1440
"""

PROFILES = {
    "on": None,  # the shipped lod_tuner.ini
    "flat": "[lod]\nEnabled = 1\nScale = 3\nPopPixels = 0\nCharacters = 1.5\nScreenHeight = 1080\nMaxDistance = 100\n",
    "off": "[lod]\nEnabled = 0\n",
}


def run_profile(exe: Path, name: str, ini: str | None) -> int:
    work = Path(tempfile.mkdtemp(prefix=f"rs-lod-{name}-"))
    try:
        for n in ("lod_stub.exe", "lod_harness_core.dll", "lod_tuner.asi"):
            shutil.copy(OUT / n, work / n)
        if ini is None:
            shutil.copy(PLUGIN / "lod_tuner.ini", work / "lod_tuner.ini")
        else:
            (work / "lod_tuner.ini").write_text(ini, encoding="utf-8")
        cfg = work / "CAPCOM" / "DRAGONS DOGMA DARK ARISEN"
        cfg.mkdir(parents=True)
        (cfg / "config.ini").write_text(GAME_CONFIG, encoding="utf-8")
        env = dict(os.environ, LOCALAPPDATA=str(work))
        r = subprocess.run([str(work / "lod_stub.exe"), str(exe), str(work / "lod_tuner.asi"), name], cwd=work,
                           capture_output=True, text=True, timeout=120, env=env,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        print(r.stdout.rstrip())
        log = work / "riftstone" / "logs" / "lod_tuner.log"
        if log.is_file():
            for line in log.read_text(encoding="utf-8", errors="replace").strip().splitlines():
                print("  plugin log: " + line)
        return r.returncode
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    need = [OUT / n for n in ("lod_stub.exe", "lod_harness_core.dll", "lod_tuner.asi")]
    if not all(p.is_file() for p in need):
        print("lod_tuner not built; run native\\plugins\\lod_tuner\\build.cmd to include it")
        return 0
    try:
        from riftstone.game import find_game
        exe = find_game().root / "DDDA.exe"
    except Exception as e:  # noqa: BLE001 -- no game on this machine
        print(f"lod_tuner harness skipped: {e}")
        return 0
    if not exe.is_file():
        print("lod_tuner harness skipped: DDDA.exe not found")
        return 0
    print("lod_tuner harness (the real DDDA.exe code, mapped read-only; no game launched)")
    failed = False
    for name, ini in PROFILES.items():
        print(f"\n[{name}]")
        code = run_profile(exe, name, ini)
        if code == 2:
            print("lod_tuner harness skipped: the image could not be mapped in this process")
            return 0
        failed |= code != 0
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

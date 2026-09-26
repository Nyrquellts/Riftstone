"""Run the draw_distance harness against the owner's DDDA.exe (read-only; the game is not launched).

    python native/plugins/draw_distance/test/run_tests.py

dd_stub.exe maps DDDA.exe's image into its own process, dd_harness_core.dll loads the built plugin,
which verifies and patches that copy, and then runs the game's own code on fake objects at ViewRange
NORMAL, FAR and FARTHEST: the display radii (setNoMoveDistance, the sGameSys constructor), the radius
each object takes and the tests that hide it, enemy and human-enemy distances and move modes, and
the grass fade.  Seven profiles: the shipped draw_distance.ini, two others, clamped and unreadable
values, every setting 0, Enabled = 0, and game code that differs by one byte.  The plugin never sees
the owner's real config.ini here: LOCALAPPDATA points at the temp folder.
Needs native\\plugins\\draw_distance\\build.cmd to have run; without the build or without the game it
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
ViewRange=FAR
[DISPLAY]
Resolution=2560x1440
"""

# profile: (draw_distance.ini, or None for the shipped one; what the plugin's log must say)
PROFILES = {
    "shipped": (None, ["patched (harness), 6 writes; at every ViewRange objects x3, grass x3, enemies the game's, human enemies the game's",
                       "config.ini ViewRange=FAR: the game's own multiplier is 2",
                       "refused: setNoMoveDistance: the multiplier by ViewRange at 0x0044D874"]),
    "people": ("[draw]\nEnabled = 1\nObjects = 0\nGrass = 0\nEnemies = 4\nHumanEnemies = 250\n",
               ["patched (harness), 3 writes; at every ViewRange objects the game's, grass the game's, enemies x4, human enemies 250 m"]),
    "all": ("[draw]\nEnabled = 1\nObjects = 8\nGrass = 2\nEnemies = 6\nHumanEnemies = 400\nObjectsNeverHide = 1\n"
            "EnemiesAlwaysActive = 1\n",
            ["patched (harness), 12 writes; at every ViewRange objects x8, grass x2, enemies x6, human enemies 400 m",
             "objects never hidden: yes; enemies always active: yes"]),
    "clamp": ("[draw]\nObjects = 500\nGrass = many\nEnemies = 0.5\nHumanEnemies = 99999\n",
              ["Grass = many is not a number of 0 or more",
               "patched (harness), 7 writes; at every ViewRange objects x20, grass the game's, enemies x1, human enemies 2000 m"]),
    "zero": ("[draw]\nEnabled = 1\nObjects = 0\nGrass = 0\nEnemies = 0\nHumanEnemies = 0\nObjectsNeverHide = 0\n"
             "EnemiesAlwaysActive = 0\n", ["every setting is the game's own, nothing patched"]),
    "off": ("[draw]\nEnabled = 0\nObjects = 8\n", ["Enabled = 0, nothing patched"]),
    "tampered": (None, ["refused: setNoMoveDistance: the multiplier by ViewRange at 0x0044D874"]),
}
BUILT = ("dd_stub.exe", "dd_harness_core.dll", "draw_distance.asi")


def run_profile(exe: Path, name: str, ini: str | None, says: list[str]) -> int:
    work = Path(tempfile.mkdtemp(prefix=f"rs-draw-{name}-"))
    try:
        for n in BUILT:
            shutil.copy(OUT / n, work / n)
        if ini is None:
            shutil.copy(PLUGIN / "draw_distance.ini", work / "draw_distance.ini")
        else:
            (work / "draw_distance.ini").write_text(ini, encoding="utf-8")
        cfg = work / "CAPCOM" / "DRAGONS DOGMA DARK ARISEN"
        cfg.mkdir(parents=True)
        (cfg / "config.ini").write_text(GAME_CONFIG, encoding="utf-8")
        env = dict(os.environ, LOCALAPPDATA=str(work))
        env.pop("RIFTSTONE_DRAW_DISTANCE_LOG", None)
        r = subprocess.run([str(work / "dd_stub.exe"), str(exe), str(work / "draw_distance.asi"), name], cwd=work,
                           capture_output=True, text=True, timeout=120, env=env,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        print(r.stdout.rstrip())
        log = work / "riftstone" / "logs" / "draw_distance.log"
        text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
        for line in text.strip().splitlines():
            print("  plugin log: " + line)
        if r.returncode in (0, 1):
            for want in says:
                if want not in text:
                    print(f"  FAIL  the plugin log says {want!r}")
                    return 1
        return r.returncode
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    if not all((OUT / n).is_file() for n in BUILT):
        print("draw_distance not built; run native\\plugins\\draw_distance\\build.cmd to include it")
        return 0
    try:
        from riftstone.game import find_game
        exe = find_game().root / "DDDA.exe"
    except Exception as e:  # noqa: BLE001 -- no game on this machine
        print(f"draw_distance harness skipped: {e}")
        return 0
    if not exe.is_file():
        print("draw_distance harness skipped: DDDA.exe not found")
        return 0
    print("draw_distance harness (the real DDDA.exe code, mapped read-only; no game launched)")
    failed = False
    for name, (ini, says) in PROFILES.items():
        print(f"\n[{name}]")
        code = run_profile(exe, name, ini, says)
        if code == 2:
            print("draw_distance harness skipped: the image could not be mapped in this process")
            return 0
        failed |= code != 0
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

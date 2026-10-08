"""Run the stage_enemies harness against the owner's DDDA.exe (read-only; the game is not launched).

    python native/plugins/stage_enemies/test/run_tests.py

stage_enemies_stub.exe maps DDDA.exe's image into its own process, stage_enemies_harness_core.dll loads
the built plugin (which reads its ini, resolves the enemies to archive tags through the exe's own table,
verifies the stage loader and patches that copy), then checks tag resolution against the real archive
table, the dispatch through a recorder, and the real thunk entered at the stage loader with a fake frame.
Profiles: one stage listed; the Archydra + Chimera + Drake mix with a duplicate, the limit of 16 a stage
and a line too long; the lines riftstone install writes, after 6,000 characters of comments; the shipped
stage_enemies.ini (no stage); Enabled = 0.  Needs
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
    "on": "[stage_enemies]\nEnabled = 1\n370 = em5301\n",   # a stage listed (the shipped ini lists none)
    # the Archydra with a Chimera and Drakes in the Tower (a comment after the list, the Archydra twice), 17
    # tags for one stage (the 17th past the limit of 16), and a line longer than the plugin's 255 characters
    "mix": ("[stage_enemies]\nEnabled = 1\n370 = em5301, em5200, em5900   ; the Archydra, a Chimera, Drakes\n"
            "370 = em5301\n600 = " + ", ".join(str(200 + i) for i in range(17)) + "\n"
            "601 = " + ", ".join(["em5200"] * 40) + "\n"),
    "install": "install",  # the shipped ini as riftstone install leaves it (install_ini)
    "shipped": None,  # the shipped stage_enemies.ini: no stage listed, so nothing is patched
    "off": "[stage_enemies]\nEnabled = 0\n370 = em5301\n",
}


def install_ini() -> bytes:
    """The shipped ini with 6,000 characters of a player's comments (past the 4,096 the plugin once read of its
    section) and then the block riftstone install writes for an Archydra, a Chimera and Drakes in the Tower."""
    from riftstone import stage_enemies

    shipped = (PLUGIN / "stage_enemies.ini").read_bytes()
    nl = b"\r\n" if b"\r\n" in shipped else b"\n"
    notes = b"".join(b"; a player's note %02d " % i + b"." * 80 + nl for i in range(60))
    rows = [{"stage": 370, "enemies": ["em5200", "em5301", "em5900"], "over": [], "no_archive": [],
             "mods": ["Tower Trio"]}]
    return stage_enemies.write_block(shipped.rstrip(b"\r\n") + nl + notes, rows)


def run_profile(exe: Path, name: str, ini: str | None) -> int:
    work = Path(tempfile.mkdtemp(prefix=f"rs-stage-enemies-{name}-"))
    try:
        for n in ("stage_enemies_stub.exe", "stage_enemies_harness_core.dll", "stage_enemies.asi"):
            shutil.copy(OUT / n, work / n)
        if ini is None:
            shutil.copy(PLUGIN / "stage_enemies.ini", work / "stage_enemies.ini")
        elif ini == "install":
            (work / "stage_enemies.ini").write_bytes(install_ini())
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

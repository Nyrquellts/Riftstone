"""Run the inclination_lock harness against the owner's DDDA.exe (read-only; the game is not launched).

    python native/plugins/inclination_lock/test/run_tests.py

incl_stub.exe maps DDDA.exe's image into its own process, incl_harness_core.dll loads the built
plugin, which verifies and patches that copy, and then runs the game's own inclination code on fake
objects: after()'s add, calcProtection, calcCuriosity and calcPrudent's order step.  Four profiles:
the shipped inclination_lock.ini (freeze), commands, off, and an unknown mode.
Needs native\\plugins\\inclination_lock\\build.cmd to have run; without the build or without the game
it reports a skip.  Exit status 0 = passed or skipped, 1 = failed.
"""
from __future__ import annotations

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
    "freeze": (None, "Mode = freeze (harness)"),  # the shipped inclination_lock.ini
    "commands": ("[lock]\nMode = commands\n", "Mode = commands (harness)"),
    "off": ("[lock]\nMode = off\n", "Mode = off, nothing patched"),
    "bogus": ("[lock]\nMode = sometimes\n", "is not freeze, commands or off; nothing is patched"),
}
BUILT = ("incl_stub.exe", "incl_harness_core.dll", "inclination_lock.asi")


def run_profile(exe: Path, name: str, ini: str | None, says: str) -> int:
    work = Path(tempfile.mkdtemp(prefix=f"rs-incl-{name}-"))
    try:
        for n in BUILT:
            shutil.copy(OUT / n, work / n)
        if ini is None:
            shutil.copy(PLUGIN / "inclination_lock.ini", work / "inclination_lock.ini")
        else:
            (work / "inclination_lock.ini").write_text(ini, encoding="utf-8")
        r = subprocess.run([str(work / "incl_stub.exe"), str(exe), str(work / "inclination_lock.asi"), name], cwd=work,
                           capture_output=True, text=True, timeout=120,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        print(r.stdout.rstrip())
        log = work / "riftstone" / "logs" / "inclination_lock.log"
        text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
        for line in text.strip().splitlines():
            print("  plugin log: " + line)
        if r.returncode in (0, 1) and says not in text:
            print(f"  FAIL  the plugin log says {says!r}")
            return 1
        return r.returncode
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    if not all((OUT / n).is_file() for n in BUILT):
        print("inclination_lock not built; run native\\plugins\\inclination_lock\\build.cmd to include it")
        return 0
    try:
        from riftstone.game import find_game
        exe = find_game().root / "DDDA.exe"
    except Exception as e:  # noqa: BLE001 -- no game on this machine
        print(f"inclination_lock harness skipped: {e}")
        return 0
    if not exe.is_file():
        print("inclination_lock harness skipped: DDDA.exe not found")
        return 0
    print("inclination_lock harness (the real DDDA.exe code, mapped read-only; no game launched)")
    failed = False
    for name, (ini, says) in PROFILES.items():
        print(f"\n[{name}]")
        code = run_profile(exe, name, ini, says)
        if code == 2:
            print("inclination_lock harness skipped: the image could not be mapped in this process")
            return 0
        failed |= code != 0
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

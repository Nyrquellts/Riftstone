"""Run the free_sprint harness against the owner's DDDA.exe (read-only; the game is not launched).

    python native/plugins/free_sprint/test/run_tests.py

sprint_stub.exe maps DDDA.exe's image into its own process, sprint_harness_core.dll loads the built
plugin, which verifies and patches that copy, and then runs the game's own calcStaminaConsume on a
fake player, directly and through updateStamina's call site: sprinting and other actions, in and out
of battle, the Arisen and a pawn, Assassin's own table, and a game state the plugin cannot read.
Six profiles: the shipped free_sprint.ini (out_of_battle, party), always, arisen, off, an unknown
mode, and a tampered exe.  Needs native\\plugins\\free_sprint\\build.cmd to have run; without the
build or without the game it reports a skip.  Exit status 0 = passed or skipped, 1 = failed.
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

# profile -> (ini text, or None for the shipped one; what the plugin's log must say)
PROFILES = {
    "out_of_battle": (None, "Mode = out_of_battle, Who = party (harness)"),
    "always": ("[sprint]\nMode = always\nWho = party\n", "Mode = always, Who = party (harness)"),
    "arisen": ("[sprint]\nMode = out_of_battle\nWho = arisen\n", "Mode = out_of_battle, Who = arisen (harness)"),
    "off": ("[sprint]\nMode = off\n", "Mode = off, nothing patched"),
    "bogus": ("[sprint]\nMode = sometimes\n", "is not out_of_battle, always or off; nothing is patched"),
    "tampered": (None, "refused: calcStaminaConsume: the switch on the player's action"),
}
# Lines the plugin writes once, the first time it lets a sprint go free / charges one in battle.
ONCE = {
    "out_of_battle": ("sprinting freely (logged once)", "in battle: sprinting costs stamina as usual (logged once)"),
    "always": ("sprinting freely (logged once)",),
    "arisen": ("sprinting freely (logged once)", "in battle: sprinting costs stamina as usual (logged once)"),
}
BUILT = ("sprint_stub.exe", "sprint_harness_core.dll", "free_sprint.asi")


def run_profile(exe: Path, name: str, ini: str | None, says: str) -> int:
    work = Path(tempfile.mkdtemp(prefix=f"rs-sprint-{name}-"))
    try:
        for n in BUILT:
            shutil.copy(OUT / n, work / n)
        if ini is None:
            shutil.copy(PLUGIN / "free_sprint.ini", work / "free_sprint.ini")
        else:
            (work / "free_sprint.ini").write_text(ini, encoding="utf-8")
        r = subprocess.run([str(work / "sprint_stub.exe"), str(exe), str(work / "free_sprint.asi"), name], cwd=work,
                           capture_output=True, text=True, timeout=120,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        print(r.stdout.rstrip())
        log = work / "riftstone" / "logs" / "free_sprint.log"
        text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
        for line in text.strip().splitlines():
            print("  plugin log: " + line)
        if r.returncode not in (0, 1, 2):
            print(f"  FAIL  the harness ended with {r.returncode:#x}")
            return 1
        if r.returncode in (0, 1):
            for want in (says, *ONCE.get(name, ())):
                if want not in text:
                    print(f"  FAIL  the plugin log says {want!r}")
                    return 1
            for line in ONCE.get(name, ()):
                if text.count(line) != 1:
                    print(f"  FAIL  {line!r} is in the log {text.count(line)} times, not once")
                    return 1
        return r.returncode
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    if not all((OUT / n).is_file() for n in BUILT):
        print("free_sprint not built; run native\\plugins\\free_sprint\\build.cmd to include it")
        return 0
    try:
        from riftstone.game import find_game
        exe = find_game().root / "DDDA.exe"
    except Exception as e:  # noqa: BLE001 -- no game on this machine
        print(f"free_sprint harness skipped: {e}")
        return 0
    if not exe.is_file():
        print("free_sprint harness skipped: DDDA.exe not found")
        return 0
    print("free_sprint harness (the real DDDA.exe code, mapped read-only; no game launched)")
    failed = False
    for name, (ini, says) in PROFILES.items():
        print(f"\n[{name}]")
        code = run_profile(exe, name, ini, says)
        if code == 2:
            print("free_sprint harness skipped: the image could not be mapped in this process")
            return 0
        failed |= code != 0
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

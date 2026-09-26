"""Run the six_skill_warrior harness against the owner's DDDA.exe (read-only; the game is not launched).

    python native/plugins/six_skill_warrior/test/run_tests.py

ssw_stub.exe maps DDDA.exe's image into its own process, ssw_harness_core.dll loads the built plugin,
which verifies and patches that copy, and then runs every patched compare from its first byte with
each value, and the game's own code on fake objects: setSkillFromEquipWeapon, removeIllegalCstmSkill,
removeJobMismatchCstmSkill, getNextAction (which action each skill button starts), initMainWpnMotion
and initSubWpnMotion (which skill motion lists load and stay), the skill-archive loader (which archive
each slot asks for), and the skill menu's own counts.  Four profiles: the shipped six_skill_warrior.ini
(six), off, an unknown mode, and a tampered exe.  Needs native\\plugins\\six_skill_warrior\\build.cmd
to have run; without the build or without the game it reports a skip.  Exit status 0 = passed or
skipped, 1 = failed.
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
    "six": (None, "Mode = six (harness)"),
    "off": ("[warrior]\nMode = off\n", "Mode = off, nothing patched"),
    "bogus": ("[warrior]\nMode = sometimes\n", "is not six or off; nothing is patched"),
    "tampered": (None, "refused: skill menu: the cursor over six slots (a slot chosen) at 0x007039CC"),
}
BUILT = ("ssw_stub.exe", "ssw_harness_core.dll", "six_skill_warrior.asi")


def run_profile(exe: Path, name: str, ini: str | None, says: str) -> int:
    work = Path(tempfile.mkdtemp(prefix=f"rs-ssw-{name}-"))
    try:
        for n in BUILT:
            shutil.copy(OUT / n, work / n)
        if ini is None:
            shutil.copy(PLUGIN / "six_skill_warrior.ini", work / "six_skill_warrior.ini")
        else:
            (work / "six_skill_warrior.ini").write_text(ini, encoding="utf-8")
        r = subprocess.run([str(work / "ssw_stub.exe"), str(exe), str(work / "six_skill_warrior.asi"), name],
                           cwd=work, capture_output=True, text=True, timeout=300,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        print(r.stdout.rstrip())
        log = work / "riftstone" / "logs" / "six_skill_warrior.log"
        text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
        for line in text.strip().splitlines():
            print("  plugin log: " + line)
        if r.returncode not in (0, 1, 2):
            print(f"  FAIL  the harness ended with {r.returncode:#x}")
            return 1
        if r.returncode in (0, 1) and says not in text:
            print(f"  FAIL  the plugin log says {says!r}")
            return 1
        return r.returncode
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    if not all((OUT / n).is_file() for n in BUILT):
        print("six_skill_warrior not built; run native\\plugins\\six_skill_warrior\\build.cmd to include it")
        return 0
    try:
        from riftstone.game import find_game
        exe = find_game().root / "DDDA.exe"
    except Exception as e:  # noqa: BLE001 -- no game on this machine
        print(f"six_skill_warrior harness skipped: {e}")
        return 0
    if not exe.is_file():
        print("six_skill_warrior harness skipped: DDDA.exe not found")
        return 0
    print("six_skill_warrior harness (the real DDDA.exe code, mapped read-only; no game launched)")
    failed = False
    for name, (ini, says) in PROFILES.items():
        print(f"\n[{name}]")
        code = run_profile(exe, name, ini, says)
        if code == 2:
            print("six_skill_warrior harness skipped: the image could not be mapped in this process")
            return 0
        failed |= code != 0
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

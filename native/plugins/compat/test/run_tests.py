"""Run the compat harness against the owner's DDDA.exe (read-only; the game is not launched).

    python native/plugins/compat/test/run_tests.py

compat_stub.exe maps DDDA.exe's image into its own process, compat_harness_core.dll loads the built
plugin from a scratch folder that stands in for the game folder (a compat.ini, a test program and the
files it names), and drives the real game code: setActionExDTI, the action manager's commit,
cPlAction's constructor, setup, init, move, final and destructor, calcSequence, setMotionList,
setShotCoord and uPlayer::checkAction.  Needs native\\plugins\\compat\\build.cmd to have run; without the
build or without the game it reports a skip.  Exit status 0 = passed or skipped, 1 = failed.
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

SKILLS = """\
# harness programs
skill test_wave
  motions   compat\\test\\m_test
  epv       9 compat\\test\\p_test
  shells    compat\\test\\shells
  collision compat\\test\\col_lv%02d
  require   compat\\test\\extra.efl
  play      0x764 -1 0
  arm       1 0 1 22
  wait      counter 1 90
  arm       0 0 1 21
  wait      counter 0 90
  wave      7 2 3 5 200 15
  shot      8 0 1 150
  play      0x765 5 0
  wait      end 120
end

skill missing_files
  motions   compat\\test\\nowhere
  play      0x764 -1 0
  wait      end 60
end
"""

EXTRA = """\
# a mod's programs in riftstone\\overlay\\compat: one new, one already defined next to the plugin
skill test_wave
  motions   compat\\test\\m_test
  play      0x700 -1 0
end
skill overlay_only
  motions   compat\\test\\m_test
  play      0x764 -1 0
  wait      end 30
end
"""

INI = """\
[compat]
enabled=1
skills=compat.skills
trace=1
[slots]
main1=test_wave
main2=missing_files
[levels]
unlearned=1
learned=3
level2=6
level3=10
[test]
key=0
"""

FILES = ["m_test.lmt", "p_test.epv", "shells.shl", "extra.efl"] + [f"col_lv{n:02d}.ocl" for n in range(1, 11)]


def main() -> int:
    need = [OUT / n for n in ("compat_stub.exe", "compat_harness_core.dll", "compat.asi")]
    if not all(p.is_file() for p in need):
        print("compat not built; run native\\plugins\\compat\\build.cmd to include it")
        return 0
    try:
        from riftstone.game import find_game
        exe = find_game().root / "DDDA.exe"
    except Exception as e:  # noqa: BLE001 -- no game on this machine
        print(f"compat harness skipped: {e}")
        return 0
    if not exe.is_file():
        print("compat harness skipped: DDDA.exe not found")
        return 0
    work = Path(tempfile.mkdtemp(prefix="rs-compat-"))
    try:
        for p in need:
            shutil.copy(p, work / p.name)
        (work / "compat.skills").write_text(SKILLS, encoding="utf-8")
        (work / "compat.ini").write_text(INI, encoding="utf-8")
        data = work / "riftstone" / "overlay" / "compat" / "test"
        data.mkdir(parents=True)
        (data.parent / "extra.skills").write_text(EXTRA, encoding="utf-8")
        for name in FILES:
            (data / name).write_bytes(b"stand-in")
        r = subprocess.run([str(work / "compat_stub.exe"), str(exe), str(work)], cwd=work,
                           capture_output=True, text=True, timeout=120,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        print("compat harness (the real DDDA.exe code, mapped read-only; no game launched)")
        print(r.stdout.rstrip())
        log = work / "riftstone" / "logs" / "compat.log"
        text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
        for line in text.splitlines():
            print("  plugin log: " + line)
        if r.returncode == 2:
            print("compat harness skipped: the image could not be mapped in this process")
            return 0
        ok = r.returncode == 0
        if "skill test_wave is already defined" not in text:
            print("  FAIL  a second definition of a program name is dropped with a note")
            ok = False
        else:
            print("  pass  a second definition of a program name is dropped with a note")
        if "skill missing_files is off" not in text:
            print("  FAIL  a program whose files are missing is switched off")
            ok = False
        else:
            print("  pass  a program whose files are missing is switched off")
        if r.returncode not in (0, 1, 2):
            print(f"  FAIL  the harness stopped with exit code {r.returncode:#x}")
        return 0 if ok else 1
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

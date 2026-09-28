"""Run the portcrystals harness against the owner's DDDA.exe (read-only; the game is not launched).

    python native/plugins/portcrystals/test/run_tests.py

pc_stub.exe maps DDDA.exe's image into its own process, pc_harness_core.dll loads the built plugin, which verifies and
patches that copy, and then runs the patched game code on a fake sGameSys, fake save data and a fake map: with 15
slots (the default), 32 (the most), 10 (the game's own), 5 (below the minimum: 10) and 40 (above: 32), once with an
altered instruction and once with sGameSys already built (loaded too late), both of which the plugin must refuse.
After each run that kept crystals past ten, the sidecar file must read back (riftstone.portcrystals) with the record
the run wrote, and a second process ("reload") must bring the same crystals back from that file.  Needs
native\\plugins\\portcrystals\\build.cmd to have run; without the build or without the game it reports a skip.
Exit status 0 = passed or skipped.
"""
from __future__ import annotations

import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "out"
sys.path.insert(0, str(HERE.parents[3] / "src"))

CASES = [(15, 15, ""), (32, 32, ""), (10, 10, ""), (5, 10, ""), (40, 32, ""), (15, 15, "tamper"), (15, 15, "late")]


def sidecar_problems(work: Path, slots: int) -> list[str]:
    """The sidecar the run left: one record for the ten of FillTen(500) with slots 11..N (area 200 + i, x 7000 + i)."""
    from riftstone import portcrystals

    path = work / "riftstone" / "portcrystals.bin"
    if slots <= 10:
        return ["slots = 10 wrote a sidecar"] if path.is_file() else []
    if not path.is_file():
        return ["no sidecar was written"]
    try:
        records = portcrystals.parse(path.read_bytes())
    except Exception as e:  # noqa: BLE001 -- reported as a failure
        return [f"the sidecar does not read back: {e}"]
    ten = [(100, 500.0 + i, 3000.0 + i, -500.0 - i) for i in range(10)]
    fp = portcrystals.fingerprint(ten)
    rec = next((r for r in records if r.fingerprint == fp), None)
    if rec is None:
        return [f"no record for the saved ten ({fp:016x}); records: {[hex(r.fingerprint) for r in records]}"]
    want = [(200 + i, portcrystals.bits(7000.0 + i), portcrystals.bits(9.0), portcrystals.bits(70.0)) for i in range(10, slots)]
    if rec.slots != want:
        return [f"the record keeps {rec.slots[:3]}..., not {want[:3]}..."]
    if records[0] is not rec:
        return ["the latest record is not first"]
    if portcrystals.build(records) != path.read_bytes():
        return ["the sidecar does not rebuild byte for byte"]
    return []


def run(work: Path, exe: Path, expect: int, mode: str) -> tuple[int, str]:
    args = [str(work / "pc_stub.exe"), str(exe), str(work / "portcrystals.asi"), str(expect)] + ([mode] if mode else [])
    r = subprocess.run(args, cwd=work, capture_output=True, text=True, timeout=120,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return r.returncode, r.stdout.rstrip()


def main() -> int:
    need = [OUT / n for n in ("pc_stub.exe", "pc_harness_core.dll", "portcrystals.asi")]
    if not all(p.is_file() for p in need):
        print("portcrystals not built; run native\\plugins\\portcrystals\\build.cmd to include it")
        return 0
    try:
        from riftstone.game import find_game
        exe = find_game().root / "DDDA.exe"
    except Exception as e:  # noqa: BLE001 -- no game on this machine
        print(f"portcrystals harness skipped: {e}")
        return 0
    if not exe.is_file():
        print("portcrystals harness skipped: DDDA.exe not found")
        return 0
    print("portcrystals harness (the real DDDA.exe code, mapped read-only; no game launched)")
    failed = 0
    for slots, expect, mode in CASES:
        work = Path(tempfile.mkdtemp(prefix="rs-pc-"))
        try:
            for p in need:
                shutil.copy(p, work / p.name)
            # one named crystal: (1539, 3911.5, 1593) is message 277 of map_placelist
            (work / "portcrystals.ini").write_text(f"[portcrystals]\nslots = {slots}\n[names]\n"
                                                   "44C06000,45747800,44C72000 = 277\n", encoding="ascii")
            code, out = run(work, exe, expect, mode)
            print(f"-- slots = {slots}{' (' + mode + ')' if mode else ''}")
            print(out)
            log_path = work / "riftstone" / "logs" / "portcrystals.log"
            log = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
            for line in log.strip().splitlines():
                print("  plugin log: " + line)
            if code == 2:
                print("portcrystals harness skipped: the image could not be mapped in this process")
                return 0
            problems = []
            if mode in ("tamper", "late"):
                if "refused" not in log:
                    problems.append("the log does not say why it refused")
            else:
                problems += sidecar_problems(work, expect)
                if expect > 10:
                    code2, out2 = run(work, exe, expect, "reload")
                    print("  -- reload (a new process)")
                    print("  " + out2.replace("\n", "\n  "))
                    if code2 != 0:
                        problems.append("the reload run failed")
                if "saved:" not in log and expect > 10:
                    problems.append("the log does not record the save")
            for p in problems:
                print("  FAIL  " + p)
            if not problems and mode not in ("tamper", "late"):
                print("  pass  the sidecar reads back" + (" and a new process brings the crystals back"
                                                          if expect > 10 else " (none: slots = 10)"))
            failed += (code != 0) or bool(problems)
        finally:
            shutil.rmtree(work, ignore_errors=True)
    print("portcrystals harness: " + ("all cases passed" if not failed else f"{failed} case(s) FAILED"))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())

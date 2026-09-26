"""Run save_backup in a stand-in game process against fake Steam saves (no game, no real save touched).

    python native/plugins/save_backup/test/run_tests.py

backup_host.exe loads the built plugin the way the loader does.  The test points the plugin at a
temporary Steam folder (RIFTSTONE_STEAM_ROOT) and a temporary Riftstone home (RIFTSTONE_HOME), writes
saves the way the game would and reads the copies back with riftstone.saves, over three game
sessions: every new save is copied once; a half-written or unchanged save is not; the newest Keep
copies and each of the last KeepSessions session starts stay; nothing is written next to the save.
Then Enabled = 0 copies nothing.  Needs native\\plugins\\save_backup\\build.cmd to have run; without
the build it reports a skip.  Exit status 0 = passed or skipped, 1 = failed.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent
OUT = PLUGIN / "out"
sys.path.insert(0, str(HERE.parents[3] / "src"))

from riftstone import saves  # noqa: E402
from riftstone.errors import RiftError  # noqa: E402

BUILT = ("backup_host.exe", "save_backup.asi")
XML = b'<?xml version="1.0" encoding="utf-8"?>\n<class name="dd_savedata" type="sSave::saveWork">\n'
INI = "[backup]\nKeep = 3\nKeepSessions = 2\nCheckSeconds = 0.1\n"

failures = 0


def check(ok: bool, what: str) -> None:
    global failures
    print(f"  {'pass' if ok else 'FAIL'}  {what}")
    if not ok:
        failures += 1


def wait_for(cond, timeout: float = 6.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return cond()


def make(tag: str) -> bytes:
    return saves.pack(XML + f"<tag>{tag}</tag>\n</class>\n".encode())


def tag_of(data: bytes) -> str:
    xml = saves.unpack(data)
    return xml.split(b"<tag>")[1].split(b"</tag>")[0].decode()


class Game:
    """One game session: the host process with the plugin loaded."""

    def __init__(self, work: Path, env: dict):
        self.p = subprocess.Popen([str(work / "backup_host.exe"), str(work / "save_backup.asi")], cwd=work, env=env,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.loaded = self.p.stdout.readline().strip() == "loaded"

    def close(self) -> None:
        self.p.stdin.close()
        self.p.wait(timeout=20)


def scenario(work: Path) -> None:
    steam, home = work / "steam", work / "home"
    main = steam / "userdata" / "111" / "367500" / "remote" / "DDDA.sav"
    other = steam / "userdata" / "333" / "367500" / "remote" / "DDDA.sav"
    (steam / "userdata" / "222").mkdir(parents=True)  # an account that never played
    main.parent.mkdir(parents=True)
    other.parent.mkdir(parents=True)
    root = home / "saves"
    clock = [int(time.time()) - 3600]

    def game_saves(path: Path, data: bytes, same_time: bool = False) -> None:
        if not same_time:
            clock[0] += 60
        path.write_bytes(data)
        os.utime(path, (clock[0], clock[0]))

    # The plugin prunes while these read, so a copy can vanish mid-listing: that look just retries.
    def tags(account: str = "111") -> list[str]:
        try:
            return [tag_of(c.path.read_bytes()) for c in saves.copies(root, account)]  # newest first
        except (OSError, RiftError):
            return ["?"]

    def starts(account: str = "111") -> list[str]:
        try:
            return [tag_of((root / account / n).read_bytes()) for n in saves.session_starts(root / account)
                    if (root / account / n).is_file()]
        except (OSError, RiftError):
            return ["?"]

    env = dict(os.environ, RIFTSTONE_HOME=str(home), RIFTSTONE_STEAM_ROOT=str(steam),
               RIFTSTONE_SAVE_BACKUP_HARNESS="1")
    env.pop("RIFTSTONE_SAVE_BACKUP_RUNNING", None)
    (work / "save_backup.ini").write_text(INI, encoding="utf-8")
    game_saves(main, make("A"))
    game_saves(other, make("Z"))

    print("\n[session 1]")
    g = Game(work, env)
    check(g.loaded, "the plugin loads in the stand-in game")
    check(wait_for(lambda: tags() == ["A"] and tags("333") == ["Z"]),
          f"the save each account starts from is copied: {tags()} / {tags('333')}")
    check(starts() == ["A"], f"sessions.txt names it: {starts()}")
    game_saves(main, make("B"))
    check(wait_for(lambda: tags()[:1] == ["B"]), f"a new save is copied: {tags()}")
    torn = bytearray(make("C"))
    torn[40] ^= 0xFF
    game_saves(main, bytes(torn))
    time.sleep(0.8)
    check(tags() == ["B", "A"], f"a half-written save is not copied: {tags()}")
    game_saves(main, make("C"), same_time=True)
    check(wait_for(lambda: tags()[:1] == ["C"]), f"once complete it is: {tags()}")
    game_saves(main, make("C"))
    time.sleep(0.8)
    check(tags() == ["C", "B", "A"], f"the same bytes at a later time make no new copy: {tags()}")
    for t in ("D", "E"):
        game_saves(main, make(t))
        wait_for(lambda t=t: tags()[:1] == [t])
    check(wait_for(lambda: tags() == ["E", "D", "C", "A"]),
          f"the newest 3 stay, and the session start: {tags()}")
    g.close()
    log = (work / "riftstone" / "logs" / "save_backup.log").read_text(encoding="utf-8", errors="replace")
    check("saved a copy" in log and "not a complete save yet" in log,
          "the log says what it copied and what it waited for")
    for line in log.strip().splitlines()[:4]:
        print("  plugin log: " + line)

    print("\n[session 2]")
    g = Game(work, env)
    check(wait_for(lambda: starts() == ["A", "E"]), f"a new session starts from the newest copy: {starts()}")
    for t in ("F", "G", "H"):
        game_saves(main, make(t))
        wait_for(lambda t=t: tags()[:1] == [t])
    check(wait_for(lambda: tags() == ["H", "G", "F", "E", "A"]),
          f"the newest 3 and both session starts stay: {tags()}")
    g.close()

    print("\n[session 3]")
    g = Game(work, env)
    check(wait_for(lambda: len(saves.session_starts(root / "111")) == 3 and tags() == ["H", "G", "F", "E"]),
          f"only the last 2 session starts are kept: A's copy goes, {tags()} stay")
    check(starts() == ["E", "H"], f"the copies of the sessions still kept: {starts()}")
    check(tags("333") == ["Z"], f"an account whose save never changed keeps its one copy: {tags('333')}")
    g.close()

    print("\n[the files]")
    left = sorted(p for p in steam.rglob("*") if p.is_file())
    check(left == sorted([main, other]), "nothing is written next to the saves (Steam Cloud never sees the copies)")
    check(not list(root.rglob("*.tmp")), "no half-written copies are left behind")
    ok = True
    for c in saves.copies(root):
        data = c.path.read_bytes()
        saves.check(data, deep=True)
        ok &= c.time is not None and saves.NAME.fullmatch(c.name) is not None
    check(ok, "every copy is a complete save, named DDDA_<date>_<time>.sav")

    print("\n[Enabled = 0]")
    (work / "save_backup.ini").write_text("[backup]\nEnabled = 0\nCheckSeconds = 0.1\n", encoding="utf-8")
    shutil.rmtree(root)
    g = Game(work, env)
    time.sleep(0.8)
    g.close()
    check(not root.exists(), "Enabled = 0 copies nothing")


def main() -> int:
    if not all((OUT / n).is_file() for n in BUILT):
        print("save_backup not built; run native\\plugins\\save_backup\\build.cmd to include it")
        return 0
    print("save_backup harness (a stand-in game process and fake Steam saves; no game, no real save touched)")
    work = Path(tempfile.mkdtemp(prefix="rs-savebackup-"))
    try:
        for n in BUILT:
            shutil.copy(OUT / n, work / n)
        scenario(work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print("\nall checks passed" if not failures else f"\n{failures} check(s) FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

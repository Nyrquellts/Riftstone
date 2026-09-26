"""One-command deploy of Ninput into a Dragon's Dogma: Dark Arisen install.

    python native/ninput/deploy.py install     [--game "C:\\path\\to\\DDDA"]
    python native/ninput/deploy.py uninstall    [--game ...]
    python native/ninput/deploy.py status       [--game ...]

This writes into the live game folder, so YOU run it -- running it is your authorization for that
install. It refuses while the game is running, backs up any existing xinput1_3.dll, is reversible
with `uninstall`, and never touches the game's own files (only adds Ninput's).

Build first:  native\\ninput\\build_msvc.cmd
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent           # native/ninput
ROOT = HERE.parents[1]                            # worktree root (has src/riftstone)
OUT = HERE / "out-msvc" / "RelWithDebInfo"        # build_msvc.cmd output
PLUGINS_SRC = HERE.parent / "plugins"
sys.path.insert(0, str(ROOT / "src"))

PROXY = "xinput1_3.dll"
BACKUP = "xinput1_3.dll.ninput-backup"
# (built .asi in OUT, its default .ini beside the plugin source)
PLUGINS = [("enemy_cap.asi", PLUGINS_SRC / "enemy_cap" / "enemy_cap.ini"),
           ("lod_tuner.asi", PLUGINS_SRC / "lod_tuner" / "lod_tuner.ini")]


def game_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    from riftstone.game import find_game
    # Riftstone serves both games (RIFTSTONE_GAME may say ddo); Ninput and its plugins are Dark Arisen's
    return find_game("ddda").root


def game_running() -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq DDDA.exe", "/NH", "/FO", "CSV"],
                             capture_output=True, text=True).stdout
        return "DDDA.exe" in out
    except Exception:
        return False


def missing_artifacts() -> list[str]:
    return [n for n in [PROXY, "enemy_cap.asi", "lod_tuner.asi"] if not (OUT / n).is_file()]


def install(root: Path) -> int:
    miss = missing_artifacts()
    if miss:
        print(f"not built: {', '.join(miss)}\n  run native\\ninput\\build_msvc.cmd first")
        return 2
    if not (root / "DDDA.exe").is_file():
        print(f"not a Dark Arisen folder (no DDDA.exe): {root}")
        return 2
    if game_running():
        print("DDDA.exe is running -- close the game first, then re-run.")
        return 2

    print(f"Installing Ninput into: {root}\n")
    dst = root / PROXY
    bak = root / BACKUP
    if dst.is_file():
        if bak.is_file():
            print(f"  replacing an existing {PROXY} (a previous Ninput deploy)")
            dst.unlink()
        else:
            dst.replace(bak)
            print(f"  backed up your existing {PROXY} -> {BACKUP}")
    _copy(OUT / PROXY, dst)
    print(f"  installed {PROXY} (the Ninput proxy the game loads)")

    pdir = root / "ninput" / "plugins"
    pdir.mkdir(parents=True, exist_ok=True)
    for asi, ini in PLUGINS:
        _copy(OUT / asi, pdir / asi)
        print(f"  installed ninput\\plugins\\{asi}")
        if ini.is_file() and not (pdir / ini.name).is_file():
            _copy(ini, pdir / ini.name)
            print(f"    + default {ini.name}")

    # Double-load guard: the Riftstone loader (dinput8.dll) also loads riftstone\plugins\*.asi.
    loader_plugins = root / "riftstone" / "plugins"
    dupes = [asi for asi, _ in PLUGINS if (loader_plugins / asi).is_file()]
    print()
    if dupes:
        print("  WARNING: these also exist in riftstone\\plugins (loaded by the Riftstone loader):")
        for d in dupes:
            print(f"    - {d}")
        print("  Remove them from riftstone\\plugins so only Ninput hosts them -- otherwise they load")
        print("  twice and the second copy refuses (the cap/LOD is already patched). enemy_skins and")
        print("  any other loader plugins there are fine to leave.")
    else:
        print("  (no conflicting copies in riftstone\\plugins)")

    print("\nDone. The Riftstone loader (dinput8.dll) keeps working alongside Ninput (xinput1_3.dll).")
    print(f"Logs after you launch:  {root}\\ninput\\ninput.log  and  {root}\\riftstone\\logs\\")
    print("Then walk through native\\ninput\\docs\\in-game-verification.md.")
    return 0


def uninstall(root: Path) -> int:
    if game_running():
        print("DDDA.exe is running -- close the game first, then re-run.")
        return 2
    print(f"Removing Ninput from: {root}\n")
    dst = root / PROXY
    bak = root / BACKUP
    if dst.is_file():
        dst.unlink()
        print(f"  removed {PROXY}")
    if bak.is_file():
        bak.replace(dst)
        print(f"  restored your original {PROXY} from {BACKUP}")
    pdir = root / "ninput" / "plugins"
    for asi, _ in PLUGINS:
        p = pdir / asi
        if p.is_file():
            p.unlink()
            print(f"  removed ninput\\plugins\\{asi}")
    print("\nLeft in place: the ninput\\ folder (its logs and any .ini you edited). Delete it by hand")
    print("if you want it gone. The Riftstone loader and its plugins are untouched.")
    return 0


def status(root: Path) -> int:
    dst = root / PROXY
    print(f"Game folder: {root}")
    print(f"  {PROXY:<28} {'installed' if dst.is_file() else 'not installed'}")
    print(f"  {BACKUP:<28} {'present (an original is backed up)' if (root / BACKUP).is_file() else 'none'}")
    pdir = root / "ninput" / "plugins"
    for asi, _ in PLUGINS:
        print(f"  ninput\\plugins\\{asi:<18} {'present' if (pdir / asi).is_file() else 'absent'}")
    log = root / "ninput" / "ninput.log"
    print(f"  ninput\\ninput.log            {'exists (the game has run with Ninput)' if log.is_file() else 'none yet'}")
    miss = missing_artifacts()
    print(f"  build output                 {'ready' if not miss else 'missing: ' + ', '.join(miss)}")
    return 0


def _copy(src: Path, dst: Path) -> None:
    dst.write_bytes(src.read_bytes())


def main() -> int:
    ap = argparse.ArgumentParser(description="Deploy Ninput into a Dark Arisen install.")
    ap.add_argument("action", choices=["install", "uninstall", "status"])
    ap.add_argument("--game", help="path to the DDDA folder (default: auto-detect via Steam / RIFTSTONE_GAME)")
    a = ap.parse_args()
    try:
        root = game_root(a.game)
    except Exception as e:  # noqa: BLE001 -- no game found
        print(f"could not find the game: {e}\n  pass --game \"C:\\path\\to\\DDDA\"")
        return 2
    return {"install": install, "uninstall": uninstall, "status": status}[a.action](root)


if __name__ == "__main__":
    sys.exit(main())

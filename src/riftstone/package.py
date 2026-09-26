"""A mod package for players without Riftstone: unzip it into the game folder and play.

    riftstone package "mods\\DDO Chimeras" "mods\\Gran Soren Horde" --plugin <file.asi> ... --out dist\\x.zip

The archives are built exactly as ``riftstone install`` builds them for the loader's overlay (the
game's own archives with the mods' changes, in priority order), from this PC's copy of the game.  The
zip holds, at the paths they take in the game folder:

    dinput8.dll                  the Riftstone loader: serves riftstone\\overlay instead of nativePC
    riftstone_loader.ini         its settings
    riftstone\\plugins\\*.asi      native plugins (and their .ini)
    riftstone\\overlay\\rom\\...    the changed archives; the game's own files are never touched
    riftstone\\package.json       what is inside, with each file's SHA-256 and the game build
    README - <name>.txt          what it is, how to install and remove it (Windows and Steam Deck)

Nothing needs installing: the loader and the plugins link their runtime statically.  The player's
game must be the Steam version the archives came from (build 2364871); the plugins check the game's
code themselves and patch nothing on any other build.  Writes one local file; publishes nothing.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import zipfile
from pathlib import Path

from . import __version__, loader
from . import mod as modlib
from .errors import RiftError
from .game import Game

PLUGIN_SUFFIXES = (".asi", ".dll")
RESERVED = ("dinput8.dll", "riftstone_loader.dll")
# Riftstone's own plugins (native/plugins, MIT); any other plugin in a package keeps its author's terms.
OWN_PLUGINS = ("enemy_cap", "enemy_skins", "lod_tuner", "inclination_lock", "save_backup", "free_sprint",
               "draw_distance", "six_skill_warrior")


def _storable(name: str) -> bool:
    """A file name a zip keeps as it is: UTF-8, no control characters (zipfile cuts a name at NUL)."""
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return all(c >= " " for c in name)


def _file_title(title: str) -> str:
    """The title as part of a file name that works on Windows and in a zip."""
    t = "".join(c for c in title.encode("utf-8", "replace").decode("utf-8")
                if c >= " " and c not in '\\/:*?"<>|' and c != "�")
    return t.strip(" .")[:80].strip(" .") or "mods"


def _build_of(game: Game) -> str | None:
    """"2364871" when this game's DDDA.exe is that build (the one the plugins and archives are for)."""
    from .game import KNOWN_BUILD_SHA256
    from .install import sha256_file
    try:
        return "2364871" if sha256_file(game.exe) == KNOWN_BUILD_SHA256 else None
    except OSError:
        return None


def _loader_ini() -> bytes:
    here = Path(__file__).resolve().parents[2]
    for t in (here / "loader" / "riftstone_loader.ini", here / "native" / "loader" / "riftstone_loader.ini"):
        if t.is_file():
            return t.read_bytes()
    return b"[loader]\r\noverlay = 1\r\ncrash_reports = 1\r\nplugins = 1\r\nchain =\r\n"


def _about(plugin: Path) -> str:
    """What a plugin does, from the first comment line of its .ini ("; name -- what it does")."""
    ini = plugin.with_suffix(".ini")
    try:
        first = next((ln.strip() for ln in ini.read_text(encoding="utf-8", errors="replace").splitlines()
                      if ln.strip()), "")
    except OSError:
        return ""
    if first.startswith(";") and " -- " in first:
        return first.split(" -- ", 1)[1].strip()[:160]
    return ""


def _readme(name: str, mods: list, plugins: list[tuple[str, str]], archives: list[str], build: str | None) -> str:
    lines = [f"{name}", "=" * len(name), ""]
    lines += ["Mods for Dragon's Dogma: Dark Arisen (Steam), made with Riftstone. You do not need Riftstone;",
              "everything is in this zip.", "", "What is inside"]
    for m in mods:
        by = f" by {m.author}" if m.author else ""
        lines.append(f"  * {m.name} {m.version}{by}" + (f" -- {m.description}" if m.description else ""))
    for p, about in plugins:
        lines.append(f"  * plugin {p}" + (f" -- {about}" if about else ""))
    lines += ["", "Install (Windows)",
              "  1. Close the game.",
              "  2. Find the game folder: in Steam, right-click Dragon's Dogma: Dark Arisen > Manage > Browse local files",
              "     (the folder with DDDA.exe in it).",
              "  3. Unzip everything here into that folder, keeping the folders. dinput8.dll and",
              "     riftstone_loader.ini go next to DDDA.exe; the riftstone folder goes there too.",
              "  4. Play. Nothing in the game's own files is changed: the loader (dinput8.dll) hands the game",
              "     the archives in riftstone\\overlay instead of its own.",
              "",
              "  Already have a dinput8.dll there (for example DDDA Tweak)? Before step 3, rename yours to",
              "  dinput8_chain.dll, then open riftstone_loader.ini and change the last line to",
              "  chain = dinput8_chain.dll  -- both keep working.",
              "",
              "Steam Deck / Linux (Proton)",
              "  Do the same, then in Steam: the game's Properties > General > Launch Options, enter",
              "      WINEDLLOVERRIDES=\"dinput8=n,b\" %command%",
              "  so Proton uses the dinput8.dll from the game folder.",
              "",
              "Remove",
              "  Delete dinput8.dll, riftstone_loader.ini and the riftstone folder from the game folder",
              "  (if you renamed your own dinput8.dll to dinput8_chain.dll, rename it back). The game is as it was.",
              "",
              "Good to know",
              f"  * Made for the Steam version of the game (build {build or '2364871'}). The plugins check the game's",
              "    code when it starts and do nothing on any other version (their reason is in riftstone\\logs).",
              "  * If the game crashes, riftstone\\logs has a crash report (crash-*.txt) to send to the mod's author.",
              "  * Another mod that replaces the same archives as this one will be hidden by this one while",
              "    it is installed. The archives it replaces:"]
    for a in archives:
        lines.append(f"      nativePC\\{a.replace('/', chr(92))}.arc")
    lines += ["  * These archives were rebuilt from the game's own files; share them only with people who own",
              "    Dragon's Dogma: Dark Arisen. Dragon's Dogma is a trademark of Capcom; this is a fan-made mod,",
              "    not affiliated with or endorsed by Capcom.",
              "  * The loader (dinput8.dll) and Riftstone's own plugins are free software under the MIT License",
              "    (riftstone\\LICENSE-Riftstone.txt)."]
    others = [p for p, _ in plugins if Path(p).stem.lower() not in OWN_PLUGINS]
    if others:
        lines += [f"  * Not Riftstone's: {', '.join(others)}. Their authors' own terms apply to them."]
    lines += ["", f"Made with Riftstone {__version__} on {time.strftime('%Y-%m-%d')}."]
    return "\r\n".join(lines) + "\r\n"


def _license() -> bytes:
    here = Path(__file__).resolve().parents[2]
    for p in (here / "LICENSE", here / "LICENSE.txt"):
        if p.is_file():
            return p.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    return b"MIT License\r\n\r\nCopyright (c) 2026 NryQ\r\n"


def build(game: Game, index, mod_roots: list[Path], plugins: list[Path], out: Path, name: str | None = None,
          loader_dir: Path | None = None, progress=None) -> dict:
    """Write the package; returns what went in."""
    out = Path(out)
    if out.suffix.lower() != ".zip":
        raise RiftError("the package is a .zip file: give --out a name ending in .zip")
    if any(c < " " or c in '<>"|?*' for c in str(out)):
        raise RiftError(f"{str(out)!r} cannot be a file name on Windows; choose another --out")
    if not mod_roots and not plugins:
        raise RiftError("name at least one mod to package, or pass plugins for a loader+plugins package")
    mods = [modlib.Mod.load(Path(r)) for r in mod_roots]
    names = [m.root.resolve() for m in mods]
    if len(set(names)) != len(names):
        raise RiftError("a mod is listed twice")
    for m in mods:
        loose = modlib.collect_loose(m)
        if any(rel.startswith(f"{modlib.PROGRAMS_DIR}/") and rel.endswith(".skills") for rel in loose):
            raise RiftError(f"{m.name} was made by 'riftstone compat pack' from this computer's own copy of Dragon's "
                            "Dogma Online: its files are converted from Capcom's, for this computer only. A player "
                            "makes their own with 'riftstone compat pack' (the compat plugin itself can be packaged)")
        if loose:
            raise RiftError(f"{m.name} has loose files (loose/), which a package does not carry yet")
    plugin_files = []
    for p in plugins:
        p = Path(p)
        if not _storable(p.name):
            raise RiftError(f"{p.name!r}: that file name cannot be stored in a zip; rename the plugin")
        if p.suffix.lower() not in PLUGIN_SUFFIXES or not p.is_file():
            raise RiftError(f"{p}: a plugin is an existing .asi or .dll file")
        if p.name.lower() in RESERVED:
            raise RiftError(f"{p.name} is the loader's own name; it cannot be a plugin")
        plugin_files.append(p)
    if len({p.name.lower() for p in plugin_files}) != len(plugin_files):
        raise RiftError("two plugins have the same file name")
    ldir = Path(loader_dir) if loader_dir else loader.built_loader()
    dll = ldir / "dinput8.dll"
    if not dll.is_file() or not loader.is_ours(dll):
        raise RiftError(f"{dll} is not the Riftstone loader; build it with native\\loader\\build.cmd")

    p = modlib.plan(game, index, mods) if mods else None
    if p is not None:
        modlib.check_plan(p)
    title = name or (" + ".join(m.name for m in mods) if mods else "Riftstone plugins")
    entries: dict[str, str] = {}
    archives = []
    tmp = out.with_name(out.name + ".riftstone-tmp")
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise RiftError(f"cannot make the folder for {out}: {e.strerror or e}") from None
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            def put(path: str, data: bytes, stored: bool = False) -> None:
                z.writestr(zipfile.ZipInfo(path, time.localtime()[:6]), data,
                           zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED)
                entries[path] = hashlib.sha256(data).hexdigest()

            if p is not None:
                for arc_name in sorted(p.archives):
                    built = modlib.build_archive(game, arc_name, p.archives[arc_name])
                    if not built.replaced and not built.added:
                        continue                               # the mods' files equal the originals here
                    rel = game.arc_path(arc_name).relative_to(game.native).as_posix()
                    put(f"riftstone/overlay/{rel}", built.data, stored=True)   # archives are zlib inside already
                    archives.append(arc_name)
                    if progress:
                        progress.advance(1, arc_name)
                if not archives:
                    raise RiftError("these mods change nothing in the game's archives; there is nothing to package")
            put("dinput8.dll", dll.read_bytes())
            put("riftstone_loader.ini", _loader_ini())
            put("riftstone/LICENSE-Riftstone.txt", _license())
            for f in plugin_files:
                put(f"riftstone/plugins/{f.name}", f.read_bytes())
                ini = f.with_suffix(".ini")
                if ini.is_file():
                    put(f"riftstone/plugins/{ini.name}", ini.read_bytes())
            build_id = _build_of(game)
            readme = f"README - {_file_title(title)}.txt"
            text = _readme(title, mods, [(f.name, _about(f)) for f in plugin_files], archives, build_id)
            put(readme, text.encode("utf-8", "replace"))
            manifest = {"name": title, "made_with": f"Riftstone {__version__}", "game_build": build_id,
                        "made": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "mods": [{"name": m.name, "version": m.version, "author": m.author} for m in mods],
                        "plugins": [f.name for f in plugin_files], "archives": archives,
                        "files": dict(sorted(entries.items()))}
            put("riftstone/package.json", json.dumps(manifest, indent=1).encode("utf-8"))
        os.replace(tmp, out)
    finally:
        if tmp.exists():
            tmp.unlink()
    return {"out": str(out), "bytes": out.stat().st_size, "archives": archives,
            "plugins": [f.name for f in plugin_files], "files": len(entries), "readme": readme}

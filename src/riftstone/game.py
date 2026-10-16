"""Find the game: Dragon's Dogma: Dark Arisen (Steam app 367500) or Dragon's Dogma Online. Reads only.

Both are MT Framework PC games with the same `<root>/nativePC/rom/**.arc` layout. A
`Game` knows which one it is (`kind`); DDO's archives are `ARCC` (see arc.py).
"""
from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from .errors import RiftError

STEAM_APP_ID = "367500"
_ARC_PART = re.compile(r"[A-Za-z0-9_\-()&+'](?:[A-Za-z0-9_\-. ()&+']{0,126}[A-Za-z0-9_\-()&+'])?")
KNOWN_BUILD_SHA256 = "19facc2642f79a09d14ce55eace5f228161f2f8bab60ec67eb3c6196cc6277b8"  # build 2364871
DDO_BUILD_SHA256 = "01dc41a962ea93a66b28bde36a9c61eec18d38e9d8d91cdb3139a0cb24c784f6"  # client 03.04.003

KINDS = {
    "ddda": {"exe": "DDDA.exe", "title": "Dragon's Dogma: Dark Arisen", "sha256": KNOWN_BUILD_SHA256,
             "build": "Steam build 2364871"},
    "ddo": {"exe": "DDO.exe", "title": "Dragon's Dogma Online", "sha256": DDO_BUILD_SHA256,
            "build": "client 03.04.003 (2019)"},
}


def _arc_parts(arc: str) -> list[str]:
    """The folder names and file stem an archive name stands for, each checked (``Game.arc_path``'s rule, which
    raises RiftError for a name that is not a plain relative one)."""
    if not isinstance(arc, str):
        raise RiftError(f"bad archive name {arc!r}")
    rel = arc.replace("\\", "/")
    if rel.lower().endswith(".arc"):
        rel = rel[:-4]
    parts = rel.split("/")
    if not parts or any(p in (".", "..") or not _ARC_PART.fullmatch(p) for p in parts):
        raise RiftError(f"bad archive name {arc!r}")
    return parts


def _arc_entries(top: Path) -> list[tuple[str, os.DirEntry]]:
    """Every ``.arc`` file under ``top`` as (its path below ``top``, with backslashes; the directory entry), by one
    scandir walk with os.walk's rules (a link to a folder is not followed, an unreadable folder is skipped).  The
    entry's stat comes with the listing, so a whole install is read in one pass instead of a stat per archive."""
    out: list[tuple[str, os.DirEntry]] = []
    stack = [("", str(top))]
    while stack:
        prefix, folder = stack.pop()
        try:
            it = os.scandir(folder)
        except OSError:
            continue
        with it:
            for e in it:
                try:
                    is_dir = e.is_dir()
                except OSError:
                    is_dir = False
                if is_dir:
                    try:
                        linked = e.is_symlink()
                    except OSError:
                        linked = True
                    if not linked:
                        stack.append((prefix + e.name + "\\", e.path))
                elif e.name.lower().endswith(".arc"):
                    out.append((prefix + e.name, e))
    return out


STAT_THREADS = 8
STAT_CHUNK = 256


def _stat_each(paths: list[str]) -> list[os.stat_result]:
    """``os.stat`` of each path, in order, a chunk at a time on a few threads (a stat waits on the disk with the GIL
    released: 8,536 archives, 0.32 s one after another, 0.09 s on 8 threads).  The first error is the first path's, as
    one stat after another would raise it."""
    if len(paths) <= STAT_CHUNK:
        return [os.stat(p) for p in paths]
    chunks = [paths[i:i + STAT_CHUNK] for i in range(0, len(paths), STAT_CHUNK)]
    with ThreadPoolExecutor(max_workers=min(STAT_THREADS, len(chunks))) as pool:
        return [st for part in pool.map(lambda chunk: [os.stat(p) for p in chunk], chunks) for st in part]


class ArcFile(NamedTuple):
    """One archive of the install: ``Game.archive_names()``'s name and path, the file that holds the bytes the game
    shipped (``Game.vanilla_arc``), and that file's size and modification time as ``Index`` keeps them."""
    name: str
    path: Path
    src: Path
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class Game:
    root: Path
    kind: str = "ddda"

    @property
    def exe(self) -> Path:
        return self.root / KINDS[self.kind]["exe"]

    @property
    def title(self) -> str:
        return KINDS[self.kind]["title"]

    @property
    def known_build_sha256(self) -> str:
        return KINDS[self.kind]["sha256"]

    @property
    def is_ddo(self) -> bool:
        return self.kind == "ddo"

    @property
    def native(self) -> Path:
        return self.root / "nativePC"

    @property
    def rom(self) -> Path:
        return self.native / "rom"

    def arc_path(self, arc: str) -> Path:
        """'rom/enemy/em0100' or 'rom\\enemy\\em0100.arc' -> absolute path under nativePC.

        Archive names come from state files and user input, so every part must be a
        plain name: no drive letters, no '..', nothing Windows would reinterpret.
        """
        return self.native.joinpath(*_arc_parts(arc)).with_suffix(".arc")

    def arc_name(self, path: Path) -> str:
        """Absolute archive path -> 'rom/enemy/em0100' (no extension, forward slashes)."""
        rel = Path(path).resolve().relative_to(self.native.resolve())
        return rel.with_suffix("").as_posix()

    def archives(self):
        """Every .arc under nativePC, sorted, as absolute paths."""
        out = []
        for root, _, files in os.walk(self.native):
            for f in files:
                if f.lower().endswith(".arc"):
                    out.append(Path(root) / f)
        out.sort(key=lambda p: str(p).lower())
        return out

    def archive_names(self) -> list[tuple[str, Path]]:
        """Every .arc under nativePC as ('rom/enemy/em0100', its path), in archives() order.  The names are what
        arc_name gives (the same for all 8,536 of the game's), read off the walk: arc_name resolves each path on
        disk, about 0.2 ms apiece, which made the whole-install passes (index check, index refresh, a mod plan)
        take seconds each."""
        base = str(self.native)
        return [(os.path.relpath(p, base)[:-4].replace("\\", "/"), p) for p in self.archives()]

    def archive_files(self) -> list[ArcFile]:
        """Every .arc under nativePC in ``archives()`` order, each with the file that holds the bytes the game shipped
        (Riftstone's backup in ``vanilla_dir`` wins over the file in nativePC, as ``vanilla_arc`` has it) and that
        file's size and modification time.  One walk of nativePC and one of ``vanilla_dir`` find the files; the size
        and time are then ``os.stat``'s, taken on a few threads, not the directory listing's: the listing is stale for
        a file that is open for writing (an installer, the game), and ``vanilla_arc`` and a stat per archive one after
        another cost three stat calls each, a second for Dark Arisen's 8,536 archives and eight on a busy disk.  An
        archive whose name ``arc_path`` refuses is refused here too (RiftError)."""
        kept = {}
        for rel, e in _arc_entries(self.vanilla_dir):
            kept[rel.replace("\\", "/").lower()] = e
        found = []
        for rel, e in _arc_entries(self.native):
            name = rel[:-4].replace("\\", "/")
            _arc_parts(name)
            b = kept.get(name.lower() + ".arc")
            try:
                use = b if b is not None and b.is_file() else e
            except OSError:
                use = e
            found.append((name, e, use))
        stats = _stat_each([use.path for _, _, use in found])
        out = [ArcFile(name, Path(e.path), Path(use.path), st.st_size, st.st_mtime_ns)
               for (name, e, use), st in zip(found, stats)]
        out.sort(key=lambda f: str(f.path).lower())
        return out

    # -- Riftstone's own folder inside the install ---------------------------
    @property
    def state_dir(self) -> Path:
        return self.root / "riftstone"

    @property
    def vanilla_dir(self) -> Path:
        """Verified copies of every original archive Riftstone has replaced."""
        return self.state_dir / "vanilla"

    @property
    def overlay_dir(self) -> Path:
        """Archives the Riftstone loader serves instead of nativePC's, when it is installed."""
        return self.state_dir / "overlay"

    def other(self) -> "Game | None":
        """The other game of the pair, if it is on this machine (for cross-game commands)."""
        try:
            return find_game("ddda" if self.kind == "ddo" else "ddo")
        except RiftError:
            return None

    def vanilla_arc(self, arc: str) -> Path:
        """The original bytes of an archive: the backup if Riftstone replaced it, else the live file."""
        live = self.arc_path(arc)
        backup = self.vanilla_dir / live.relative_to(self.native)
        return backup if backup.is_file() else live

    def loader_installed(self) -> bool:
        if self.kind != "ddda":
            return False  # the Riftstone loader is built for DDDA.exe only
        dll = self.root / "dinput8.dll"
        if not dll.is_file() or not (self.root / "riftstone_loader.ini").is_file():
            return False
        try:
            return "Riftstone loader".encode("utf-16-le") in dll.read_bytes()
        except OSError:
            return False


def detect_kind(path: Path) -> str | None:
    """'ddda' or 'ddo' when path is that game's folder (exe + nativePC/rom), else None."""
    if not (path / "nativePC" / "rom").is_dir():
        return None
    for kind, info in KINDS.items():
        if (path / info["exe"]).is_file():
            return kind
    return None


def _ddo_candidates() -> list[Path]:
    """$RIFTSTONE_DDO, then client folders under <path> (the ddon toolkit's home)."""
    out: list[Path] = []
    if os.environ.get("RIFTSTONE_DDO"):
        out.append(Path(os.environ["RIFTSTONE_DDO"]))
    base = Path(os.environ.get("DDON_HOME", r"<path>"))
    if base.is_dir():
        out += sorted(p for p in base.iterdir() if p.is_dir() and (p / "DDO.exe").is_file())
    return out


def steam_roots() -> list[Path]:
    """Steam's install folders, as the registry names them (read only), then the default one."""
    roots: list[Path] = []
    try:
        import winreg

        for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                          (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")):
            try:
                with winreg.OpenKey(hive, key) as k:
                    for value in ("SteamPath", "InstallPath"):
                        try:
                            roots.append(Path(winreg.QueryValueEx(k, value)[0]))
                        except OSError:
                            pass
            except OSError:
                pass
    except ImportError:
        pass
    roots.append(Path(r"C:\Program Files (x86)\Steam"))
    return roots


def _steam_libraries() -> list[Path]:
    roots = steam_roots()
    libraries: list[Path] = []
    for root in roots:
        vdf = root / "steamapps" / "libraryfolders.vdf"
        libraries.append(root)
        if vdf.is_file():
            text = vdf.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r'"path"\s+"([^"]+)"', text):
                libraries.append(Path(m.group(1).replace("\\\\", "\\")))
    seen, unique = set(), []
    for lib in libraries:
        k = str(lib).lower()
        if k not in seen:
            seen.add(k)
            unique.append(lib)
    return unique


KEYWORDS = {"ddda": "ddda", "dd": "ddda", "da": "ddda", "ddo": "ddo", "online": "ddo"}


def find_game(explicit: str | os.PathLike | None = None) -> Game:
    """The install to use. `explicit` (or $RIFTSTONE_GAME) is a folder, or a game keyword:
    'ddda' (default; found through Steam) or 'ddo' ($RIFTSTONE_DDO, else <path>*).  A keyword takes the
    folder $RIFTSTONE_GAME names first when that folder is that game: a copy outside Steam, or the one of two
    copies meant (it was passed over whenever a keyword, e.g. a mod's game, was given)."""
    env = os.environ.get("RIFTSTONE_GAME", "")
    want = str(explicit if explicit else env or "ddda")
    named = [Path(env)] if env and env.lower() not in KEYWORDS else []
    candidates: list[Path] = []
    if want.lower() in ("ddo", "online"):
        candidates = [p for p in named if detect_kind(p) == "ddo"] + _ddo_candidates()
        title = "Dragon's Dogma Online"
    elif want.lower() in ("ddda", "dd", "da"):
        candidates = [p for p in named if detect_kind(p) == "ddda"]
        for lib in _steam_libraries():
            candidates.append(lib / "steamapps" / "common" / "DDDA")
        title = "Dragon's Dogma: Dark Arisen"
    else:
        candidates.append(Path(want))
        title = "Dragon's Dogma"
    for c in candidates:
        kind = detect_kind(c)
        if kind:
            return Game(c, kind)
    tried = ", ".join(str(c) for c in candidates) or "(nothing)"
    raise RiftError(f"{title} was not found (looked in {tried}). Pass --game \"C:\\path\\to\\game\" "
                    "(or --game ddo / --game ddda), or set RIFTSTONE_GAME / RIFTSTONE_DDO.")

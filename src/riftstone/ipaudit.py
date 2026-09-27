"""What must never be in Riftstone's repository, its releases or a mod package (docs/legal.md), found by content:

* a file of either game: a resource or archive by its magic at the start (ARC, ARCC, TEX, MOD, MRL, XFS,
  GMD, LMT, SBC, SPAC), or either game's executable (by name or its known SHA-256);
* Dragon's Dogma Online's archive key (cipher.key_in);
* Capcom's copyright notation (a (c) or copyright sign before Capcom's name: Capcom asks fans not to use it);
* third-party code that is not Riftstone's to ship (the unlicensed unit expander, anything from vendor/);
* links to file hosts a game client could be downloaded from.

``scan_bytes`` checks one file, ``scan_tree`` a folder (by default the files git tracks), ``scan_zip`` a zip.
Each finding is one line naming the file.  tools/ip_audit.py runs them; the unit tests run it on the tree.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import zipfile
from pathlib import Path

GAME_MAGICS = {b"ARC\0": "an archive", b"ARCC": "an Online archive", b"TEX\0": "a texture", b"MOD\0": "a model",
               b"MRL\0": "a material list", b"XFS\0": "an XFS resource", b"GMD\0": "a text file",
               b"LMT\0": "a motion list", b"SBC\xff": "a collision mesh", b"SPAC": "a sound bank"}
GAME_EXES = ("ddda.exe", "ddo.exe")
RULE_FILES = ("src/riftstone/ipaudit.py", "tests/test_ip_audit.py")
TEXT_SUFFIXES = (".py", ".md", ".txt", ".html", ".json", ".cmd", ".bat", ".ini", ".cpp", ".h", ".inc", ".js",
                 ".css", ".yaml", ".yml", ".toml", ".nyr", ".def", ".rc", ".cs", ".lua", ".java", ".svg", ".hlsl")
COPYRIGHT = re.compile(r"(?:©|\(c\)|copyright)\s*capcom", re.IGNORECASE)
FILE_HOSTS = re.compile(r"mega[.]nz|mediafire[.]com|drive[.]google[.]com|magnet:[?]|[.]torrent\b|rapidgator|zippyshare",
                        re.IGNORECASE)


def _exe_hashes() -> set[str]:
    from .game import KINDS
    return {k["sha256"] for k in KINDS.values()}


def scan_bytes(name: str, data: bytes, allow_copyright_rule: bool = False) -> list[str]:
    """Findings for one file (its path as the tree or zip names it, and its bytes)."""
    from .cipher import key_in
    out = []
    low = name.replace("\\", "/").lower()
    base = low.rsplit("/", 1)[-1]
    if low.startswith("vendor/") or "unit_expander" in low:
        out.append(f"{name}: third-party code that is not Riftstone's to ship")
    if base in GAME_EXES or (data[:2] == b"MZ" and hashlib.sha256(data).hexdigest() in _exe_hashes()):
        out.append(f"{name}: a game executable")
    is_text = base.endswith(TEXT_SUFFIXES)
    if not is_text:
        for magic, what in GAME_MAGICS.items():
            if data.startswith(magic):
                out.append(f"{name}: {what} of the game (starts {magic!r})")
                break
    if key_in(data) is not None:
        out.append(f"{name}: Dragon's Dogma Online's archive key")
    if is_text and not low.endswith(RULE_FILES):          # the files that hold the rules name what they look for
        text = data.decode("utf-8", "replace")
        if COPYRIGHT.search(text):
            out.append(f"{name}: Capcom's copyright notation (use the notice in legal.py instead)")
        m = FILE_HOSTS.search(text)
        if m:
            out.append(f"{name}: a link to a file host ({m.group(0)})")
    return out


def tracked_files(root: Path) -> list[str]:
    """The files git tracks under root (a working tree), as posix paths."""
    r = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, timeout=60,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "replace").strip() or "git ls-files failed")
    return [p for p in r.stdout.decode("utf-8").split("\0") if p]


def scan_tree(root: Path, files: list[str] | None = None) -> list[str]:
    root = Path(root)
    out = []
    for rel in files if files is not None else tracked_files(root):
        p = root / rel
        if p.is_file():
            out += scan_bytes(rel, p.read_bytes())
    return out


def scan_zip(path: Path) -> list[str]:
    out = []
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            if not info.is_dir():
                out += scan_bytes(info.filename, z.read(info.filename))
    return out

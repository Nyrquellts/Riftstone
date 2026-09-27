"""The byte and string evidence the docs quote, checked against the games' own executables; and the
facts in research notes that the repository does not record yet.

    python tools/doc_claims.py check [--ddda EXE] [--ddo EXE] [FILE ...]
    python tools/doc_claims.py list [FILE ...]
    python tools/doc_claims.py orphans [--ddda EXE] [--ddo EXE] NOTE_OR_FOLDER ...

A page states evidence in one of three forms (backticks included):

    `8B 81 E8 00 00 00` at `0x00DA97D0`      bytes at a virtual address
    `0x004A658D` `83 F8 0A 72 E6`            the same, address first (AGENTS.md's form)
    `"Fatal error."` at `0x0142C0B0`         a C string (with its NUL; \\n \\t \\\\ \\" escapes)

`check` reads every such claim in the tracked Markdown files (or the FILEs given) and compares it
with DDDA.exe, or with DDO.exe when the claim's line names Online (DDO) and not Dark Arisen, or the
page's name starts with `ddo`.  Addresses are virtual addresses in the file's own image (bytes past
a section's raw data read as zero, as the loader maps them).  So a fact written with its bytes is
re-proved on every gate run and cannot drift from the exe it describes.  Only PC bytes belong in
these forms; PS3 addresses stay prose.  Exit 0 when every claim holds, 1 when one does not or a
tracked page is missing from the worktree, 0 with a note when neither game is installed, and 2 when
the check cannot be made: an executable named (or the installed game's) that is not a readable PE
file, a FILE that is not there, git unable to list the pages, or no page at all.

`orphans` lists the addresses written in notes -- a scratch file, a lane's uncommitted page -- that
fall in either executable's sections (Online's image runs to 0x045A3C00, past Dark Arisen's) and
that no tracked file of the repository mentions: research that would be lost with the note.  Reads
the executables and git only.
"""
from __future__ import annotations

import argparse
import re
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

HEXB = r"`((?:[0-9A-F]{2} ){1,31}[0-9A-F]{2})`"
ADDR = r"`(0x[0-9A-Fa-f]{8})`"
STR = r'`"((?:[^"\\\n]|\\.){1,200})"`'
FORMS = (
    ("bytes", re.compile(HEXB + r"\s+at\s+" + ADDR)),
    ("bytes", re.compile(ADDR + r":?\s+" + HEXB)),
    ("string", re.compile(STR + r"\s+at\s+" + ADDR)),
)
ONLINE = re.compile(r"\bDDO\b|\bOnline\b")
ARISEN = re.compile(r"\bDDDA\b|Dark Arisen")
ORPHAN_ADDR = re.compile(r"\b0x0*([0-9A-Fa-f]{6,8})\b")
# with neither executable to read, orphans counts both images' extent (DDDA.exe build 2364871 ends at
# 0x01A0BD70, DDO.exe 03.04.003 at 0x045A3C00: its second .text starts at 0x0240F000)
LOW, HIGH = 0x00401000, 0x045A3C00


class GitFailed(Exception):
    """git could not answer: the pages, or the addresses the repository records, are unknown."""


class Image:
    """A PE file's sections, read the way the loader maps them."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = self.path.read_bytes()
        try:
            pe = struct.unpack_from("<I", self.data, 0x3C)[0]
            if self.data[pe:pe + 4] != b"PE\0\0":
                raise ValueError
            nsec = struct.unpack_from("<H", self.data, pe + 6)[0]
            optsz = struct.unpack_from("<H", self.data, pe + 20)[0]
            self.base = struct.unpack_from("<I", self.data, pe + 0x34)[0]
            self.sections = []
            for i in range(nsec):
                o = pe + 24 + optsz + i * 40
                vsz, va, rsz, rptr = struct.unpack_from("<IIII", self.data, o + 8)
                self.sections.append((self.base + va, max(vsz, rsz), rptr, rsz))
        except (ValueError, struct.error):      # no PE signature, or headers past the end of the file
            raise ValueError(f"{path} is not a PE file") from None

    def read(self, va: int, n: int) -> bytes | None:
        for start, size, rptr, rsz in self.sections:
            if start <= va and va + n <= start + size:
                rel = va - start
                raw = self.data[rptr + rel: rptr + min(rel + n, rsz)] if rel < rsz else b""
                return raw + b"\0" * (n - len(raw))
        return None


def unescape(s: str) -> bytes:
    out, i = bytearray(), 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            out += {"n": b"\n", "t": b"\t", "r": b"\r", "0": b"\0", "\\": b"\\", '"': b'"'}.get(
                s[i + 1], ("\\" + s[i + 1]).encode("latin-1"))
            i += 2
        else:
            out += c.encode("latin-1", "replace")
            i += 1
    return bytes(out)


def claims(path: Path, text: str | None = None):
    """(line, kind, address, expected bytes, game) for every claim in a page."""
    text = path.read_text(encoding="utf-8", errors="replace") if text is None else text
    ddo_page = path.name.lower().startswith("ddo")
    found = []
    for kind, rx in FORMS:
        for m in rx.finditer(text):
            if kind == "string":
                expected, addr = unescape(m.group(1)) + b"\0", int(m.group(2), 16)
            elif m.group(1).startswith("0x"):
                addr, expected = int(m.group(1), 16), bytes.fromhex(m.group(2))
            else:
                expected, addr = bytes.fromhex(m.group(1)), int(m.group(2), 16)
            start = text.rfind("\n", 0, m.start()) + 1
            end = text.find("\n", m.end())
            line = text[start:end if end >= 0 else len(text)]
            online, arisen = bool(ONLINE.search(line)), bool(ARISEN.search(line))
            game = "ddo" if (online and not arisen) or (ddo_page and not arisen) else "ddda"
            found.append((text.count("\n", 0, m.start()) + 1, kind, addr, expected, game))
    return sorted(set(found))


def tracked_pages() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "-z", "*.md"], cwd=ROOT, capture_output=True)
    if out.returncode != 0:
        raise GitFailed(f"git could not list the pages in {ROOT}: {out.stderr.decode('utf-8', 'replace').strip()}")
    names = [n for n in out.stdout.decode("utf-8", "replace").split("\0") if n]
    return [ROOT / n for n in names if not n.startswith("vendor/")]


def images(ddda: str | None, ddo: str | None) -> dict[str, Image]:
    """The executables to read: the ones named, else the installed games'.  A game that is not installed
    is left out; an executable named or installed that is not a readable PE file raises OSError or
    ValueError, since claims it would have checked cannot count as holding."""
    from riftstone.errors import RiftError
    from riftstone.game import find_game

    out = {}
    for kind, explicit in (("ddda", ddda), ("ddo", ddo)):
        if explicit:
            out[kind] = Image(Path(explicit))
            continue
        try:
            exe = find_game(kind).exe
        except RiftError:                   # not installed: its claims are not checked
            continue
        out[kind] = Image(exe)
    return out


def shown(page: Path) -> Path:
    return page.relative_to(ROOT) if page.is_relative_to(ROOT) else page


def check(files: list[Path], exes: dict[str, Image]) -> int:
    total = bad = skipped = gone = 0
    for page in files:
        if not page.is_file():
            gone += 1
            print(f"{shown(page).as_posix()}: tracked, but not in the worktree (deleted and not committed?)")
            continue
        for line, kind, addr, expected, game in claims(page):
            img = exes.get(game)
            if img is None:
                skipped += 1
                continue
            total += 1
            got = img.read(addr, len(expected))
            if got != expected:
                bad += 1
                held = "outside the image" if got is None else got.hex(" ").upper()
                print(f"{shown(page)}:{line}: {game} {kind} at 0x{addr:08X}: doc {expected.hex(' ').upper()}, exe {held}")
    print(f"{total - bad} of {total} claims hold" + (f"; {skipped} skipped (game not installed)" if skipped else "")
          + (f"; {gone} page(s) missing" if gone else ""))
    return 1 if bad or gone else 0


def known_addresses() -> set[int]:
    r = subprocess.run(["git", "grep", "-h", "-o", "-I", "-i", "-E", r"0x0*[0-9a-f]{6,8}", "HEAD"],
                       cwd=ROOT, capture_output=True)
    if r.returncode not in (0, 1):          # 1: nothing matched
        raise GitFailed(f"git could not search {ROOT}: {r.stderr.decode('utf-8', 'replace').strip()}")
    return {int(m, 16) for m in re.findall(r"0x([0-9a-fA-F]+)", r.stdout.decode("utf-8", "replace"))}


def orphans(paths: list[str], exes: dict[str, Image]) -> int:
    known = known_addresses()
    ranges = [(start, start + size) for img in exes.values() for start, size, _rptr, _rsz in img.sections]
    if not ranges:
        print(f"neither DDDA.exe nor DDO.exe was found: counting 0x{LOW:08X}..0x{HIGH - 1:08X}, both images' extent")
        ranges = [(LOW, HIGH)]
    notes = []
    for p in map(Path, paths):
        notes += sorted(q for q in p.rglob("*") if q.suffix.lower() in (".md", ".txt") and q.is_file()) \
            if p.is_dir() else [p]
    for note in notes:
        text = note.read_text(encoding="utf-8", errors="replace")
        addrs = sorted({a for a in (int(h, 16) for h in ORPHAN_ADDR.findall(text))
                        if any(lo <= a < hi for lo, hi in ranges)})
        if not addrs:
            continue
        missing = [a for a in addrs if a not in known]
        head = " ".join(f"0x{a:08X}" for a in missing[:24]) + (" ..." if len(missing) > 24 else "")
        print(f"{len(missing):4d} of {len(addrs):4d} addresses not in the repository  {note}"
              + (f"\n      {head}" if missing else ""))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("check", "list", "orphans"))
    ap.add_argument("files", nargs="*")
    ap.add_argument("--ddda", help="DDDA.exe to check against (default: the installed game)")
    ap.add_argument("--ddo", help="DDO.exe to check against (default: the installed game)")
    a = ap.parse_args(argv)
    if a.command == "orphans" and not a.files:
        ap.error("orphans needs the notes (files or folders) to read")
    # a page (or a note) named must be there; a folder holds notes, not a page
    unusable = [f for f in a.files if not (Path(f).exists() if a.command == "orphans" else Path(f).is_file())]
    if unusable:
        print(f"not {'there' if a.command == 'orphans' else 'a page'}: {', '.join(unusable)}", file=sys.stderr)
        return 2
    try:
        if a.command == "orphans":
            return orphans(a.files, images(a.ddda, a.ddo))
        files = [Path(f).resolve() for f in a.files] or tracked_pages()
        if not files:
            print(f"no page to check: git lists no Markdown file in {ROOT}", file=sys.stderr)
            return 2
        if a.command == "list":
            for page in files:
                if not page.is_file():
                    print(f"{shown(page).as_posix()}: tracked, but not in the worktree")
                    continue
                for line, kind, addr, expected, game in claims(page):
                    held = expected.hex(" ").upper() if kind == "bytes" else repr(expected[:-1].decode("latin-1"))
                    print(f"{page.name}:{line}  {game}  0x{addr:08X}  {held}")
            return 0
        exes = images(a.ddda, a.ddo)
    except (GitFailed, OSError, ValueError) as e:
        print(f"the claims were not checked: {e}", file=sys.stderr)
        return 2
    if not exes:
        print("neither DDDA.exe nor DDO.exe was found: the docs' byte claims were not checked")
        return 0
    return check(files, exes)


if __name__ == "__main__":
    sys.exit(main())

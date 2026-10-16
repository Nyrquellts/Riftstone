"""Walk the installed game's resources for tests, fuzz seeds and indexing.  Reads only.

The bytes come from each archive as the game shipped it: Riftstone's verified backup in
``<game>/riftstone/vanilla`` when an install (direct mode, no loader) replaced the file, else the file in
nativePC (``source``), so the proofs never read Riftstone's own output.
"""
from __future__ import annotations

import hashlib
import os
import struct
import zlib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from .arc import ENTRY, HEADER, MAGIC, MAGIC_ENC, SIZE_MASK, decrypt_table
from .errors import RiftError
from .game import Game


def encrypted(path: Path) -> bool:
    with open(path, "rb") as fh:
        return fh.read(4) == MAGIC_ENC


@dataclass(frozen=True)
class Resource:
    arc: Path           # the archive in nativePC (its name and label), whichever copy the bytes came from
    name: bytes
    type_id: int
    data: bytes
    place: int = 0      # where it stands in the pass that gave it: 1 for the first resource counted, then 2, ...

    @property
    def label(self) -> str:
        return f"{self.arc.name}:{self.name.decode('latin-1')}"


def _read_directory(fh, path: Path) -> tuple[list[tuple[bytes, int, int, int, int]], bool]:
    """``directory`` of an archive already open at its start: (the rows, whether the archive is encrypted)."""
    head = fh.read(HEADER.size)
    if len(head) < HEADER.size:
        return [], head == MAGIC_ENC
    magic, _, count = HEADER.unpack(head)
    if magic not in (MAGIC, MAGIC_ENC):
        return [], False
    table = fh.read(count * ENTRY.size)
    if magic == MAGIC_ENC:
        from .cipher import note_path
        note_path(path)
        table = decrypt_table(table[:len(table) // ENTRY.size * ENTRY.size])
    out = []
    for i in range(len(table) // ENTRY.size):
        raw, tid, zs, sf, off = ENTRY.unpack_from(table, i * ENTRY.size)
        out.append((raw.split(b"\0", 1)[0], tid, zs, sf & SIZE_MASK, off))
    return out, magic == MAGIC_ENC


def directory(path: Path) -> list[tuple[bytes, int, int, int, int]]:
    """(name, type id, stored size, decoded size, offset) for each entry, reading only the table."""
    with open(path, "rb") as fh:
        return _read_directory(fh, path)[0]


def payload(stored: bytes, encrypted: bool) -> bytes:
    """One entry's stored bytes -> the decoded resource (ARCC payloads are decrypted first;
    their zero padding after the zlib stream is ignored)."""
    if encrypted:
        from .cipher import arc_cipher

        stored = arc_cipher().decrypt(stored[:len(stored) // 8 * 8])
    return zlib.decompressobj().decompress(stored)


def backups(game: Game) -> dict[str, Path]:
    """Riftstone's verified copies of the originals it replaced (``Game.vanilla_dir``), keyed by their path
    under nativePC in lower case: one walk of that small folder instead of a lookup per archive."""
    out: dict[str, Path] = {}
    for root, _, files in os.walk(game.vanilla_dir):
        for f in files:
            if f.lower().endswith(".arc"):
                p = Path(root) / f
                out[p.relative_to(game.vanilla_dir).as_posix().lower()] = p
    return out


def source(game: Game, path: Path, kept: dict[str, Path] | None = None) -> Path:
    """Where an archive of ``game.archives()`` keeps the bytes the game shipped: Riftstone's backup when an
    install replaced the file in nativePC, else the file itself -- what ``Game.vanilla_arc(Game.arc_name(
    path))`` answers, without resolving every path.  ``kept``: ``backups(game)``, when the caller has it."""
    kept = backups(game) if kept is None else kept
    if not kept:
        return Path(path)
    try:
        rel = Path(path).relative_to(game.native).as_posix().lower()
    except ValueError:
        try:
            rel = game.arc_path(game.arc_name(path)).relative_to(game.native).as_posix().lower()
        except (RiftError, ValueError):     # not an archive of this game: nothing was replaced
            return Path(path)
    return kept.get(rel, Path(path))


class Walk:
    """What one pass of ``resources`` counted: how many places, and a SHA-256 over what stands at each one, in order.
    The shards of one pass (``resources(shard=...)``) must agree on both, or they did not read the same game."""

    __slots__ = ("places", "_hash")

    def __init__(self) -> None:
        self.places = 0
        self._hash = hashlib.sha256()

    def add(self, key: bytes) -> None:
        self.places += 1
        self._hash.update(key)

    @property
    def digest(self) -> str:
        return self._hash.hexdigest()


def _archives_holding(game: Game, want: set[int] | None, indexed: bool = True) -> Iterator[tuple[Path, Path]]:
    """(the archive in nativePC, where the game's original bytes are) for each archive that may hold a resource of
    a wanted type, in ``game.archives()`` order.  Every archive when ``want`` is None, ``indexed`` is False or no
    index proves which ones hold the types: a pass reads each archive's table, 0.5 ms apiece, and Dragon's Dogma
    Online has 25,639 of them, 13 s for a pass that returns 52 resources.  The game's index
    (``index.indexed_archives``) names the archives that hold the types when it is current for these very files;
    one that is missing, of another schema, damaged or behind an archive's size or modification time is not used."""
    if want is not None and indexed:
        from .index import indexed_archives

        found = indexed_archives(game, want)
        if found is not None:
            names, files = found
            for f in files:
                if f.name in names:
                    yield f.path, f.src
            return
    kept = backups(game)
    for path in game.archives():
        yield path, source(game, path, kept)


def resources(game: Game, types: Iterable[int] | None = None, unique: bool = True,
              limit_per_type: int | None = None, shard: tuple[int, int] | None = None,
              indexed: bool = True, walk: Walk | None = None) -> Iterator[Resource]:
    """Decoded resources of the given types, as the game shipped them (``source``); with unique=True each
    distinct payload once.  ``shard=(i, n)``: only the resources whose place in the sequence this would otherwise
    give (after the uniqueness and ``limit_per_type`` rules) is ``i`` modulo ``n`` -- so shards 0..n-1 together
    are exactly the unsharded sequence, in its order, each resource in one shard -- and the others are not
    decompressed or decrypted.  ``walk``: filled with every place of the sequence, the shard's own or not.

    The game's index picks the archives to open when it is current (``_archives_holding``), which is the index's own
    idea of current: every archive's size and modification time as it recorded them.  An archive swapped for another
    of the same size that keeps its time cannot be told from the original without reading it, so a proof or a
    generator that must see every table passes ``indexed=False`` and reads every archive, as every pass once did."""
    want = set(types) if types is not None else None
    seen: set[bytes] = set()
    per_type: dict[int, int] = {}
    place = 0
    for path, src in _archives_holding(game, want, indexed):
        with open(src, "rb") as fh:                 # one open: the table, whether it is encrypted, the payloads
            table, enc = _read_directory(fh, src)
            rows = [r for r in table if want is None or r[1] in want]
            for name, tid, zs, size, off in rows:
                if limit_per_type is not None and per_type.get(tid, 0) >= limit_per_type:
                    continue
                fh.seek(off)
                comp = fh.read(zs)
                if unique:
                    h = hashlib.sha1(comp).digest()
                    if h in seen:
                        continue
                    seen.add(h)
                per_type[tid] = per_type.get(tid, 0) + 1
                place += 1
                if walk is not None:
                    walk.add(b"%s|%s|%d|%d|%d|%s;" % (str(path).encode("utf-8", "surrogateescape"), name, tid, zs, off,
                                                      h if unique else b""))
                if shard is not None and (place - 1) % shard[1] != shard[0]:
                    continue
                yield Resource(path, name, tid, payload(comp, enc), place)


def xfs_type_ids() -> list[int]:
    """Every XFS resource type of either game (DDDA's table plus Dragon's Dogma Online's)."""
    from . import typemap
    return sorted({typemap.jamcrc(c) for c, _, x, _ in typemap.TYPES_BY_CLASS + typemap.DDO_TYPES if x})

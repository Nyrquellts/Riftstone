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

    @property
    def label(self) -> str:
        return f"{self.arc.name}:{self.name.decode('latin-1')}"


def directory(path: Path) -> list[tuple[bytes, int, int, int, int]]:
    """(name, type id, stored size, decoded size, offset) for each entry, reading only the table."""
    with open(path, "rb") as fh:
        head = fh.read(HEADER.size)
        if len(head) < HEADER.size:
            return []
        magic, _, count = HEADER.unpack(head)
        if magic not in (MAGIC, MAGIC_ENC):
            return []
        table = fh.read(count * ENTRY.size)
    if magic == MAGIC_ENC:
        from .cipher import note_path
        note_path(path)
        table = decrypt_table(table[:len(table) // ENTRY.size * ENTRY.size])
    out = []
    for i in range(len(table) // ENTRY.size):
        raw, tid, zs, sf, off = ENTRY.unpack_from(table, i * ENTRY.size)
        out.append((raw.split(b"\0", 1)[0], tid, zs, sf & SIZE_MASK, off))
    return out


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


def resources(game: Game, types: Iterable[int] | None = None, unique: bool = True,
              limit_per_type: int | None = None) -> Iterator[Resource]:
    """Decoded resources of the given types, as the game shipped them (``source``); with unique=True each
    distinct payload once."""
    want = set(types) if types is not None else None
    seen: set[bytes] = set()
    per_type: dict[int, int] = {}
    kept = backups(game)
    for path in game.archives():
        src = source(game, path, kept)
        rows = [r for r in directory(src) if want is None or r[1] in want]
        if not rows:
            continue
        enc = encrypted(src)
        with open(src, "rb") as fh:
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
                yield Resource(path, name, tid, payload(comp, enc))


def xfs_type_ids() -> list[int]:
    """Every XFS resource type of either game (DDDA's table plus Dragon's Dogma Online's)."""
    from . import typemap
    return sorted({typemap.jamcrc(c) for c, _, x, _ in typemap.TYPES_BY_CLASS + typemap.DDO_TYPES if x})

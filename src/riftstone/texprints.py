"""Fingerprints of every texture of a game, to recognise one of its textures in a mod package.

A package must not carry the other game's textures (package.py), however they got into the mod: a port
copied by hand, a DDS exported from the other game and turned back into a texture.  Converting a texture
between the games rewrites only its first word (the revision; tex.attr1_for), so a texture is recognised by
its decoded size and the bytes after that word.

    key = "<decoded size>:<sha256 of bytes 8..4096>"      (only this much of each texture is read)

The table of a game (key -> archive and name) is built once from the archives (a few thousand small reads)
and cached beside the resource index, keyed by the index's view of the archives, so it is rebuilt when the
game changes.  ``find`` confirms a hit on the whole texture.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import struct
import zlib
from pathlib import Path

from . import typemap
from .errors import RiftError

PREFIX = 4096
SCHEMA = "riftstone.texprints/1"


def key(data: bytes) -> str:
    return f"{len(data)}:{hashlib.sha256(bytes(data[8:PREFIX])).hexdigest()[:32]}"


def _signature(game, idx) -> str:
    rows = idx.db.execute("SELECT arc, size, mtime FROM arcs ORDER BY arc").fetchall()
    h = hashlib.sha256(str(Path(game.root).resolve()).lower().encode())
    for r in rows:
        h.update(repr(r).encode())
    return h.hexdigest()[:24]


def _head(fh, offset: int, stored: int, size: int, encrypted: bool) -> bytes:
    """The first PREFIX bytes (or all) of an entry, inflating only what that needs."""
    want = min(PREFIX, size)
    take = min(stored, 8192)
    while True:
        fh.seek(offset)
        chunk = fh.read(take)
        if encrypted:
            from .cipher import arc_cipher
            chunk = arc_cipher().decrypt(chunk[:len(chunk) // 8 * 8])
        try:
            out = zlib.decompressobj().decompress(chunk, want)
        except zlib.error:
            return b""
        if len(out) >= want or take >= stored:
            return out
        take = min(stored, take * 4)


def build(game, idx, progress=None) -> dict[str, list[str]]:
    """key -> ["<archive>|<name>", ...] for every texture of the game."""
    from .corpus import directory
    TEX = typemap.BY_EXT["tex"]
    arcs = [r[0] for r in idx.db.execute("SELECT DISTINCT arc FROM res WHERE type=? ORDER BY arc", (TEX,))]
    out: dict[str, list[str]] = {}
    for a in arcs:
        path = game.vanilla_arc(a)
        try:
            with open(path, "rb") as fh:
                encrypted = fh.read(4) == b"ARCC"
                for name, tid, stored, size, offset in directory(path):
                    if tid != TEX:
                        continue
                    head = _head(fh, offset, stored, size, encrypted)
                    if head:
                        out.setdefault(f"{size}:{hashlib.sha256(head[8:PREFIX]).hexdigest()[:32]}", []).append(
                            f"{a}|{name.decode('latin-1')}")
        except (OSError, struct.error, RiftError):
            continue
        if progress:
            progress.advance(1, a)
    return out


class Prints:
    """One game's fingerprints, from the cache or built when first needed."""

    def __init__(self, game, idx):
        self.game, self.idx = game, idx
        self._table: dict[str, list[str]] | None = None

    def cache_path(self) -> Path:
        from .index import home
        return home() / f"texprints-{self.game.kind}-{_signature(self.game, self.idx)}.json.gz"

    def table(self, note=None) -> dict[str, list[str]]:
        if self._table is None:
            p = self.cache_path()
            try:
                data = json.loads(gzip.decompress(p.read_bytes()))
                if data.get("schema") == SCHEMA:
                    self._table = data["prints"]
            except (OSError, ValueError, KeyError, EOFError):
                self._table = None
            if self._table is None:
                if note:
                    note(f"reading every texture of {self.game.title} once, to recognise its textures in a package "
                         "(cached for next time)")
                self._table = build(self.game, self.idx)
                try:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    tmp = p.with_name(p.name + ".tmp")
                    tmp.write_bytes(gzip.compress(json.dumps({"schema": SCHEMA, "prints": self._table}).encode()))
                    tmp.replace(p)
                except OSError:
                    pass
        return self._table

    def find(self, data: bytes, note=None) -> tuple[str, bytes, bytes] | None:
        """(archive, name, the game's texture) when data is one of this game's textures (apart from the
        revision word), else None."""
        from . import arc
        TEX = typemap.BY_EXT["tex"]
        for hit in self.table(note).get(key(data), [])[:32]:
            a, _, name = hit.partition("|")
            try:
                e = arc.Archive.read(self.game.vanilla_arc(a)).find(name.encode("latin-1"), TEX)
                full = e.data() if e is not None else None
            except (OSError, RiftError):
                continue
            if full is not None and full[8:] == bytes(data[8:]):
                return a, name.encode("latin-1"), full
        return None

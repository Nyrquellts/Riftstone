"""ARC v7 archives (Dragon's Dogma: Dark Arisen and Dragon's Dogma Online, PC).

Dragon's Dogma Online uses the same layout with magic "ARCC": every 80-byte
directory entry is Blowfish-ECB encrypted on its own and every payload is a
zlib stream zero-padded to 8 bytes and encrypted (cipher.py; measured on all
25,637 DDO archives, 411,644 resources). Stored payloads are kept verbatim, so
an unedited DDO archive rebuilds byte for byte even where the English patch
recompressed entries with another zlib encoder (header 78 01).

DDDA layout, measured over all 8,536 vanilla archives (358,431 resources):

    0x00  "ARC\\0"   u16 version = 7   u16 count
    0x08  count x 80-byte entries: name[64] (NUL-terminated, zero-filled),
          u32 type id, u32 stored size, u32 decoded size | flags << 29,
          u32 payload offset
    zero padding up to the next 0x8000 boundary
    payloads, contiguous, in directory order; each a zlib (level 6) stream

Every vanilla entry has flags == 2, and zlib level 6 reproduces every stored
payload byte for byte, so recompressing untouched resources rebuilds the
original archive exactly.  Resource identity is the exact name bytes plus the
type id: names are engine identifiers (some carry trailing spaces or doubled
separators) and are never normalised here; see fsmap for file paths.
"""
from __future__ import annotations

import hashlib
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from . import typemap
from .errors import FormatError

MAGIC = b"ARC\0"
MAGIC_ENC = b"ARCC"   # Dragon's Dogma Online: Blowfish-encrypted directory and payloads
VERSION = 7
ENTRY = struct.Struct("<64sIIII")
HEADER = struct.Struct("<4sHH")
ALIGN = 0x8000
DEFAULT_FLAGS = 2
ZLIB_LEVEL = 6
MAX_DECODED = 256 * 1024 * 1024       # largest vanilla resource is ~21 MiB
MAX_ARCHIVE = 2 * 1024 * 1024 * 1024  # refuse to read anything bigger
SIZE_MASK = 0x1FFFFFFF


def _align(n: int) -> int:
    return (n + ALIGN - 1) // ALIGN * ALIGN


@dataclass
class Entry:
    name: bytes
    type_id: int
    size: int
    payload: bytes          # the stored bytes: a zlib stream (encrypted + padded in ARCC archives)
    flags: int = DEFAULT_FLAGS
    encrypted: bool = False

    @classmethod
    def from_data(cls, name: bytes, type_id: int, data: bytes, flags: int = DEFAULT_FLAGS,
                  encrypted: bool = False) -> "Entry":
        check_name(name)
        if len(data) > MAX_DECODED:
            raise FormatError("ARC", f"{name!r} is {len(data)} bytes; the limit is {MAX_DECODED}")
        z = zlib.compress(data, ZLIB_LEVEL)
        if encrypted:
            from .cipher import arc_cipher
            z = arc_cipher().encrypt(z)  # pads to 8 with zeros, as the game's own archives are
        return cls(name, type_id, len(data), z, flags, encrypted)

    @property
    def key(self) -> tuple[bytes, int]:
        return self.name, self.type_id

    @property
    def label(self) -> str:
        return f"{self.name.decode('latin-1')}.{typemap.extension(self.type_id)}"

    def data(self) -> bytes:
        """Decoded resource bytes; the zlib stream must end exactly at the declared size
        (in ARCC archives only the zero padding up to the 8-byte block may follow it)."""
        if self.size > MAX_DECODED:
            raise FormatError("ARC", f"{self.label}: declared size {self.size} is over the {MAX_DECODED} limit")
        stream = self.payload
        if self.encrypted:
            if len(stream) % 8:
                raise FormatError("ARC", f"{self.label}: encrypted payload is not whole 8-byte blocks")
            from .cipher import arc_cipher
            stream = arc_cipher().decrypt(stream)
        d = zlib.decompressobj()
        try:
            raw = d.decompress(stream, self.size + 1)
        except zlib.error as e:
            raise FormatError("ARC", f"{self.label}: damaged zlib stream ({e})") from None
        tail_ok = not d.unused_data or (self.encrypted and len(d.unused_data) < 8 and not d.unused_data.strip(b"\0"))
        if len(raw) != self.size or not d.eof or not tail_ok or d.unconsumed_tail:
            raise FormatError("ARC", f"{self.label}: zlib stream does not match its declared size {self.size}")
        return raw


def check_name(name: bytes) -> None:
    if not name:
        raise FormatError("ARC", "empty resource name")
    if len(name) > 63:
        raise FormatError("ARC", f"resource name is {len(name)} bytes; the archive holds at most 63")
    if b"\0" in name:
        raise FormatError("ARC", "resource name contains a NUL byte")


def decrypt_table(table: bytes) -> bytes:
    """An ARCC directory (whole 80-byte entries) in the clear."""
    from .cipher import arc_cipher
    return arc_cipher().decrypt(table)


@dataclass
class Archive:
    entries: list[Entry] = field(default_factory=list)
    version: int = VERSION
    encrypted: bool = False   # "ARCC" (Dragon's Dogma Online)

    # -- reading ---------------------------------------------------------
    @classmethod
    def parse(cls, data: bytes | memoryview) -> "Archive":
        n = len(data)
        if n < HEADER.size:
            raise FormatError("ARC", "file is shorter than the 8-byte header", 0)
        magic, version, count = HEADER.unpack_from(data, 0)
        if magic not in (MAGIC, MAGIC_ENC):
            raise FormatError("ARC", f"not an ARC file (magic {bytes(magic)!r})", 0)
        encrypted = magic == MAGIC_ENC
        if version != VERSION:
            raise FormatError("ARC", f"version {version}; Dragon's Dogma PC archives are version 7", 4)
        table_end = HEADER.size + count * ENTRY.size
        if table_end > n:
            raise FormatError("ARC", f"directory of {count} entries runs past the end of the file", HEADER.size)
        table = bytes(data[HEADER.size:table_end])
        if encrypted:
            table = decrypt_table(table)
        entries: list[Entry] = []
        seen: set[tuple[bytes, int]] = set()
        spans: list[tuple[int, int, int]] = []
        for i in range(count):
            at = HEADER.size + i * ENTRY.size
            raw_name, type_id, zsize, size_flags, offset = ENTRY.unpack_from(table, i * ENTRY.size)
            nul = raw_name.find(b"\0")
            if nul < 0:
                raise FormatError("ARC", f"entry {i}: name is not NUL-terminated", at)
            if raw_name[nul:].strip(b"\0"):
                raise FormatError("ARC", f"entry {i}: bytes after the name terminator are not zero", at)
            name = raw_name[:nul]
            if not name:
                raise FormatError("ARC", f"entry {i}: empty name", at)
            if zsize == 0:
                raise FormatError("ARC", f"entry {i} ({name!r}): zero stored size", at)
            if encrypted and zsize % 8:
                raise FormatError("ARC", f"entry {i} ({name!r}): encrypted payload is not whole 8-byte blocks", at)
            if offset < table_end or offset + zsize > n:
                raise FormatError("ARC", f"entry {i} ({name!r}): payload outside the file", at)
            key = (name, type_id)
            if key in seen:
                raise FormatError("ARC", f"entry {i}: duplicate resource {name!r} type {type_id:08x}", at)
            seen.add(key)
            spans.append((offset, offset + zsize, i))
            entries.append(Entry(name, type_id, size_flags & SIZE_MASK, bytes(data[offset:offset + zsize]),
                                 size_flags >> 29, encrypted))
        spans.sort()
        for (s1, e1, i1), (s2, e2, i2) in zip(spans, spans[1:]):
            if s2 < e1:
                raise FormatError("ARC", f"entries {i1} and {i2} overlap", s2)
        return cls(entries, version, encrypted)

    @classmethod
    def read(cls, path: str | Path) -> "Archive":
        path = Path(path)
        size = path.stat().st_size
        if size > MAX_ARCHIVE:
            raise FormatError("ARC", f"{path.name} is {size} bytes; refusing archives over {MAX_ARCHIVE}")
        return cls.parse(path.read_bytes())

    # -- writing ---------------------------------------------------------
    def build(self) -> bytes:
        count = len(self.entries)
        if count > 0xFFFF:
            raise FormatError("ARC", f"{count} entries; an archive holds at most 65535")
        seen: set[tuple[bytes, int]] = set()
        table_end = HEADER.size + count * ENTRY.size
        data_start = _align(table_end)
        out = bytearray(HEADER.pack(MAGIC_ENC if self.encrypted else MAGIC, self.version, count))
        table = bytearray()
        offset = data_start
        for e in self.entries:
            check_name(e.name)
            if e.key in seen:
                raise FormatError("ARC", f"duplicate resource {e.label}")
            seen.add(e.key)
            if not 0 <= e.size <= SIZE_MASK or not 0 <= e.flags <= 7:
                raise FormatError("ARC", f"{e.label}: size or flags out of range")
            if e.encrypted != self.encrypted:
                raise FormatError("ARC", f"{e.label}: {'encrypted' if e.encrypted else 'plain'} entry in a "
                                         f"{'encrypted' if self.encrypted else 'plain'} archive")
            if offset + len(e.payload) > 0xFFFFFFFF:
                raise FormatError("ARC", "archive would exceed 4 GiB")
            table += ENTRY.pack(e.name, e.type_id, len(e.payload), e.size | (e.flags << 29), offset)
            offset += len(e.payload)
        if self.encrypted:
            from .cipher import arc_cipher
            table = arc_cipher().encrypt(bytes(table))
        out += table
        out += bytes(data_start - table_end)
        for e in self.entries:
            out += e.payload
        return bytes(out)

    # -- editing ---------------------------------------------------------
    def index(self) -> dict[tuple[bytes, int], int]:
        return {e.key: i for i, e in enumerate(self.entries)}

    def find(self, name: bytes, type_id: int) -> Entry | None:
        for e in self.entries:
            if e.name == name and e.type_id == type_id:
                return e
        return None

    def put(self, name: bytes, type_id: int, data: bytes) -> str:
        """Replace a resource's data, or append it; returns 'replaced', 'unchanged' or 'added'."""
        for i, e in enumerate(self.entries):
            if e.name == name and e.type_id == type_id:
                if e.data() == data:
                    return "unchanged"
                self.entries[i] = Entry.from_data(name, type_id, data, e.flags, self.encrypted)
                return "replaced"
        self.entries.append(Entry.from_data(name, type_id, data, encrypted=self.encrypted))
        return "added"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_build(built: bytes, expected: dict[tuple[bytes, int], str]) -> Archive:
    """Reparse a built archive and compare every resource hash with what was intended."""
    arc = Archive.parse(built)
    got = {e.key: sha256(e.data()) for e in arc.entries}
    if got != expected:
        missing = [k for k in expected if k not in got]
        extra = [k for k in got if k not in expected]
        changed = [k for k in expected if k in got and got[k] != expected[k]]
        raise FormatError("ARC", f"rebuilt archive failed verification: {len(missing)} missing, "
                                 f"{len(extra)} unexpected, {len(changed)} with different content")
    return arc

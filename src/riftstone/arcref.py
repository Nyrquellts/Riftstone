"""Archive references (rArchive, ``ARCS``): the list one archive keeps of another archive it pulls in.

An enemy archive that fires projectiles holds ``rom\\shell\\shellhellhound`` (type rArchive); a stage's
object pack holds one per object archive (``rom\\om\\f07\\om3521``); the character creator's pack holds
the equipment it shows.  The resource's name is the referenced archive's path, and its body lists what
that archive contains:

  0x00  "ARCS"
  0x04  u16 version (7)   u16 count
  0x08  count x { u32 JAMCRC(resource name), u32 type id }   -- the referenced archive's directory, in order

Measured (``check_corpus --only arcs``): all 1,038 distinct references rebuild byte for byte, and 1,034
list their archive's directory exactly, by name hash and type, in order.  The game itself ships two
that do not: some stage packs keep an older list for ``om8505`` (without 7 quest-text files added
later) and ``om11001``'s hashes a differently spelled effect name -- so an out-of-date list is
something the game already lives with.  A mod that adds or removes resources in a referenced archive
makes the lists that point at it out of date too; ``for_names`` rebuilds one from a directory.
What the engine uses the list for: UNKNOWN.
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field

from .errors import FormatError

MAGIC = b"ARCS"
VERSION = 7
_HEAD = struct.Struct("<4sHH")
_ENTRY = struct.Struct("<II")


@dataclass
class ArcRef:
    entries: list[tuple[int, int]] = field(default_factory=list)     # (name hash, type id)
    version: int = VERSION


def parse(data: bytes) -> ArcRef:
    if len(data) < _HEAD.size:
        raise FormatError("ARCS", "shorter than the header", 0)
    magic, version, count = _HEAD.unpack_from(data, 0)
    if magic != MAGIC:
        raise FormatError("ARCS", "not an archive reference (magic)", 0)
    if len(data) != _HEAD.size + count * _ENTRY.size:
        raise FormatError("ARCS", f"{count} entries need {_HEAD.size + count * _ENTRY.size} bytes, the file has "
                                  f"{len(data)}", 4)
    return ArcRef([_ENTRY.unpack_from(data, _HEAD.size + i * _ENTRY.size) for i in range(count)], version)


def build(ref: ArcRef) -> bytes:
    if not 0 <= len(ref.entries) <= 0xFFFF or not 0 <= ref.version <= 0xFFFF:
        raise FormatError("ARCS", "a reference holds at most 65535 entries")
    return _HEAD.pack(MAGIC, ref.version, len(ref.entries)) + b"".join(_ENTRY.pack(h, t) for h, t in ref.entries)


def name_hash(name: bytes) -> int:
    """The hash an entry uses: the inverted CRC-32 (JAMCRC) of the resource's name as the archive spells
    it, all 32 bits (type ids clear the top bit; these do not)."""
    return ~zlib.crc32(name) & 0xFFFFFFFF


def for_names(directory: list[tuple[bytes, int]]) -> ArcRef:
    """The reference an archive with this directory (name, type id, in order) gets."""
    return ArcRef([(name_hash(n), t) for n, t in directory])


def target(name: bytes) -> str:
    """The referenced archive, as an archive name: rom\\shell\\shellhellhound -> rom/shell/shellhellhound."""
    return name.decode("latin-1").replace("\\", "/")


def matches(ref: ArcRef, directory: list[tuple[bytes, int]]) -> bool:
    return ref.entries == for_names(directory).entries

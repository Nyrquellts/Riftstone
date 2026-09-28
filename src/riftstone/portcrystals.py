"""The portcrystals plugin's sidecar, ``<game>\\riftstone\\portcrystals.bin``: the Portcrystals placed past the ten a
save holds, one record per save state (native/plugins/portcrystals, docs/re-portcrystals.md).

The save keeps its ten exactly as the game writes them.  When the game builds save data the plugin writes the slots
past ten into a record keyed by a fingerprint of those ten (area, x, y, z each, FNV-1a 64); when the game loads a
save, the record with the same fingerprint comes back.  Newest record first, at most 32.

    "RSPC"  u32 version (1)  u32 count
    count x { u64 fingerprint, u64 written (FILETIME), u32 n, n x { u32 area, f32 x, f32 y, f32 z } }

Every size must add up to the file's length exactly; the plugin reads the same bytes the same way.
"""
from __future__ import annotations

import datetime as _dt
import math
import re
import struct
from dataclasses import dataclass, field

from .errors import FormatError

MAGIC = b"RSPC"
VERSION = 1
MAX_RECORDS = 32
MAX_EXTRA = 22                         # 32 slots at most, less the save's ten
_HEAD = struct.Struct("<4sII")
_REC = struct.Struct("<QQI")
_SLOT = struct.Struct("<IIII")          # area, then x, y, z as the bits of their floats


@dataclass
class Record:
    fingerprint: int
    written: int                       # FILETIME: 100 ns since 1601-01-01 UTC
    # (area, x, y, z) past the ten; x, y and z as the bits of their floats, which the plugin copies and never computes
    # with (a float would not keep a signalling NaN's bits: fuzz, portcrystals)
    slots: list[tuple[int, int, int, int]] = field(default_factory=list)

    @property
    def placed(self) -> int:
        """The crystals this record keeps (a slot whose area is not 0)."""
        return sum(1 for s in self.slots if s[0])

    def position(self, k: int) -> tuple[float, float, float]:
        """Slot k's position (past the ten) as floats."""
        return tuple(struct.unpack("<f", struct.pack("<I", b))[0] for b in self.slots[k][1:])

    @property
    def when(self) -> _dt.datetime | None:
        try:
            return _dt.datetime(1601, 1, 1, tzinfo=_dt.timezone.utc) + _dt.timedelta(microseconds=self.written // 10)
        except OverflowError:
            return None


def parse(data: bytes) -> list[Record]:
    if len(data) < _HEAD.size:
        raise FormatError("portcrystals", "shorter than its header")
    magic, version, count = _HEAD.unpack_from(data, 0)
    if magic != MAGIC:
        raise FormatError("portcrystals", "not a portcrystals sidecar (no RSPC)")
    if version != VERSION:
        raise FormatError("portcrystals", f"version {version}; this Riftstone reads {VERSION}")
    if count > MAX_RECORDS:
        raise FormatError("portcrystals", f"{count} records; at most {MAX_RECORDS}")
    at = _HEAD.size
    out = []
    for _ in range(count):
        if len(data) - at < _REC.size:
            raise FormatError("portcrystals", "a record runs past the end")
        fp, written, n = _REC.unpack_from(data, at)
        at += _REC.size
        if n > MAX_EXTRA:
            raise FormatError("portcrystals", f"a record keeps {n} slots; at most {MAX_EXTRA}")
        if len(data) - at < n * _SLOT.size:
            raise FormatError("portcrystals", "a record's slots run past the end")
        slots = [_SLOT.unpack_from(data, at + k * _SLOT.size) for k in range(n)]
        at += n * _SLOT.size
        out.append(Record(fp, written, slots))
    if at != len(data):
        raise FormatError("portcrystals", f"{len(data) - at} bytes past the last record")
    return out


def build(records: list[Record]) -> bytes:
    if len(records) > MAX_RECORDS:
        raise FormatError("portcrystals", f"{len(records)} records; at most {MAX_RECORDS}")
    out = bytearray(_HEAD.pack(MAGIC, VERSION, len(records)))
    for r in records:
        if len(r.slots) > MAX_EXTRA:
            raise FormatError("portcrystals", f"a record keeps {len(r.slots)} slots; at most {MAX_EXTRA}")
        out += _REC.pack(r.fingerprint, r.written, len(r.slots))
        for area, x, y, z in r.slots:
            out += _SLOT.pack(area, x, y, z)
    return bytes(out)


def _fnv(h: int, v: int) -> int:
    for k in range(4):
        h ^= (v >> (8 * k)) & 0xFF
        h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


def bits(f: float) -> int:
    """A float32's bits, as the save and the sidecar keep a position."""
    return struct.unpack("<I", struct.pack("<f", f))[0]


def fingerprint(ten: list[tuple[int, float, float, float]]) -> int:
    """The key a record is stored under: the save's ten slots (area, x, y, z as floats), their bits in order."""
    if len(ten) != 10:
        raise ValueError("a save holds ten slots")
    h = 0xCBF29CE484222325
    for area, x, y, z in ten:
        h = _fnv(h, area & 0xFFFFFFFF)
        for f in (x, y, z):
            h = _fnv(h, bits(f))
    return h


# ---- a save's ten, and putting a crystal past them --------------------------------------------------------------
_AREAS = re.compile(rb'<array name="Anchor_Area" type="u32" count="10">(.*?)</array>', re.S)
_POSITIONS = re.compile(rb'<array name="Anchor_Pos" type="vector3" count="10">(.*?)</array>', re.S)
_U32 = re.compile(rb'<u32 value="(\d{1,10})"/>')
_VEC = re.compile(rb'<vector3 x="([^"]{1,32})" y="([^"]{1,32})" z="([^"]{1,32})"/>')


def save_slots(xml: bytes) -> list[tuple[int, float, float, float]]:
    """The ten slots a save holds (area, x, y, z): its first ``mPl``'s Anchor_Area and Anchor_Pos, as DDDA.sav's
    XML keeps them (saves.unpack).  Its fingerprint is the key the plugin keeps the crystals past ten under."""
    a, p = _AREAS.search(xml), _POSITIONS.search(xml)
    if not a or not p:
        raise FormatError("portcrystals", "the save has no Anchor_Area and Anchor_Pos arrays of ten")
    areas = [int(v) for v in _U32.findall(a.group(1))]
    try:
        pos = [tuple(float(c) for c in v) for v in _VEC.findall(p.group(1))]
    except ValueError as e:
        raise FormatError("portcrystals", f"a Portcrystal position is not a number ({e})") from e
    if len(areas) != 10 or len(pos) != 10 or any(v > 0xFFFFFFFF for v in areas):
        raise FormatError("portcrystals", f"the save's Portcrystal arrays hold {len(areas)} areas and {len(pos)} "
                                          "positions, not ten each")
    if any(not math.isfinite(c) or abs(c) > 3.4e38 for v in pos for c in v):
        raise FormatError("portcrystals", "a Portcrystal position is not a float the game can hold")
    return [(areas[i], *pos[i]) for i in range(10)]


def place(records: list[Record], fp: int, slots: int, stage: int, x: float, y: float, z: float,
          slot: int | None = None, written: int = 0) -> tuple[list[Record], int]:
    """``records`` with a crystal at (stage, x, y, z) past the ten of the save ``fp``: in ``slot`` (11..slots, as the
    game counts them) or the first free one.  The save's record comes first afterwards, as the plugin keeps the newest.
    Returns (records, the slot used)."""
    if not 11 <= slots <= 10 + MAX_EXTRA:
        raise FormatError("portcrystals", f"slots = {slots}: a crystal past ten needs 11..{10 + MAX_EXTRA}")
    if not 1 <= stage <= 0xFFFFFFFF:
        raise FormatError("portcrystals", f"stage {stage} is not a stage")
    for c in (x, y, z):
        if not math.isfinite(c) or abs(c) > 3.4e38:
            raise FormatError("portcrystals", f"{c} is not a position the game can hold")
    old = next((r for r in records if r.fingerprint == fp), None)
    rec = Record(fp, written, list(old.slots) if old else [])
    while len(rec.slots) < slots - 10:
        rec.slots.append((0, 0, 0, 0))
    if slot is None:
        free = [k for k in range(slots - 10) if rec.slots[k][0] == 0]
        if not free:
            raise FormatError("portcrystals", f"slots 11-{slots} are all taken for this save")
        k = free[0]
    else:
        if not 11 <= slot <= slots:
            raise FormatError("portcrystals", f"slot {slot} is not past the ten and within slots = {slots}")
        k = slot - 11
    rec.slots[k] = (stage, bits(x), bits(y), bits(z))
    rest = [r for r in records if r.fingerprint != fp]
    return ([rec] + rest)[:MAX_RECORDS], k + 11


# A crystal's name in the Ferrystone's list and on the map: the plugin's [names] section in portcrystals.ini, one
# "XXXXXXXX,YYYYYYYY,ZZZZZZZZ = message" per crystal (its position's float bits; a message number of the game's place
# list, id/DDN/message/common/map_placelist_<language>.gmd).  The game names every other crystal by where it stands.
PLACE_LIST = "id/DDN/message/common/map_placelist_eng.gmd"
_NAME = re.compile(r"^([0-9A-Fa-f]{1,8}),([0-9A-Fa-f]{1,8}),([0-9A-Fa-f]{1,8})[ \t]*=[ \t]*([0-9]{1,5})[ \t]*$")
_SECTION = re.compile(r"^[ \t]*\[([^\]]*)\]")


def name_key(x: float, y: float, z: float) -> str:
    """The [names] key of a position, as the plugin compares it: the bits of its three float32s."""
    return ",".join(f"{bits(c):08X}" for c in (x, y, z))


def slot_key(slot: tuple[int, int, int, int]) -> str:
    """The [names] key of a sidecar slot (area, x, y, z bits)."""
    return ",".join(f"{b:08X}" for b in slot[1:])


def read_names(text: str) -> dict[str, int]:
    """The [names] the plugin takes from portcrystals.ini's text: key -> message (lines it would skip are left out)."""
    names: dict[str, int] = {}
    section = None
    for line in text.splitlines():
        m = _SECTION.match(line)
        if m:
            section = m.group(1).strip().lower()
            continue
        m = _NAME.match(line.strip()) if section == "names" else None
        if m and int(m.group(4)) <= 0xFFFF:
            names[",".join(f"{int(g, 16):08X}" for g in m.groups()[:3])] = int(m.group(4))
    return names


def write_names(text: str, changes: dict[str, int | None]) -> str:
    """portcrystals.ini's text with [names] entries set (key -> message) or taken out (key -> None); every other line
    kept as it was, the section added at the end when missing."""
    for key, message in changes.items():
        if not re.fullmatch(r"[0-9A-F]{8},[0-9A-F]{8},[0-9A-F]{8}", key):
            raise FormatError("portcrystals", f"{key!r} is not a [names] key (XXXXXXXX,YYYYYYYY,ZZZZZZZZ)")
        if message is not None and not 0 <= message <= 0xFFFF:
            raise FormatError("portcrystals", f"message {message} is not 0..65535")
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    out: list[str] = []
    section = None
    start = None
    for line in lines:
        m = _SECTION.match(line)
        if m:
            section = m.group(1).strip().lower()
            out.append(line)
            if section == "names" and start is None:
                start = len(out)
            continue
        n = _NAME.match(line.strip()) if section == "names" else None
        if n and ",".join(f"{int(g, 16):08X}" for g in n.groups()[:3]) in changes:
            continue                                   # replaced (or taken out) below
        out.append(line)
    new = [f"{k} = {v}" for k, v in changes.items() if v is not None]
    if start is None:
        if new:
            if out and out[-1].strip():
                out.append("")
            out += ["[names]"] + new
    else:
        out[start:start] = new
    return nl.join(out) + (nl if out else "")

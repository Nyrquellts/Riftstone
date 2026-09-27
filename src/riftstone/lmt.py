"""rMotionList ``.lmt``: a list of skeletal animations (motions), byte-exact.

Dragon's Dogma: Dark Arisen ships version 66 (1,002 distinct files), Dragon's Dogma Online
version 67 (1,402). Both are the 32-bit MT Framework 2 layout; the record layouts below were
measured on every file of both games (``check_corpus --only lmt``), with RevilLib (GPL-3,
read for field names only, nothing copied) as a cross-check.

File::

  0x00  "LMT\\0"   u16 version (66 | 67)   u16 count
  0x08  count x u32 motion offset (0 = empty slot)

Motion header, 60 bytes (each starts on a 16-byte boundary)::

  +0x00 u32 tracks offset    +0x04 u32 track count     +0x08 u32 frame count
  +0x0C s32 loop frame       +0x10 f32[4] end-frame additive scene position
  +0x20 f32[4] end-frame additive scene rotation (quaternion)
  +0x30 u32 flags            +0x34 u32 events offset   +0x38 u32 float tracks offset

  flags 0x800000 = the motion has an event block (every vanilla motion does);
  (flags >> 16) & 0x1F = the number of float-track groups (bits worth 1, 2, 4, 8 and 16:
  DDDA uses 4, 1 and 16, DDO 8 and 8+2; both loaders compute it this way);
  0x1000000 / 0x2000000 / 0x4000000 = this motion's tracks / events / floats were already
  relocated by an earlier motion that shares them (``SHARED_*``; computed by ``build``);
  bit 0 is set in 19,597 of DDO's 19,685 motions and in no DDDA motion (meaning UNKNOWN;
  neither loader reads it).

The loaders (DDDA ``rMotionList::load`` 0x00E9D330, DDO 0x015A4B80) accept only their own
version (66, 67). DDDA points codec 11-13 tracks' extremes at the track's own reference
value; DDO relocates real extremes for codecs 4, 5, 7 and 11-15 only.

Bone track, 36 bytes::

  +0x00 u8 codec (buffer type)  +0x01 u8 usage  +0x02 u8 bone type  +0x03 u8 bone id
  +0x04 f32 weight   +0x08 u32 buffer size   +0x0C u32 buffer offset
  +0x10 f32[4] reference value (the pose used when the buffer is empty)
  +0x20 u32 extremes offset (0 = none): 32 bytes, f32[4] scale then f32[4] offset,
        which dequantise the packed codecs

  usage: 0 local rotation, 1 local position, 2 local scale, 3 absolute rotation,
  4 absolute position, 5 (measured, meaning UNKNOWN). Codecs (``CODECS``): 1 vector3,
  2 rotation quat3, 3 linear vector3, 4/5 bilinear vector3 16/8-bit, 6 linear quat 14-bit,
  7 bilinear quat 7-bit, 11/12/13 bilinear quat XW/YW/ZW 14-bit, 14/15 bilinear quat
  11/9-bit. Buffers are kept verbatim here; ``codecs`` decodes them.

Event block, 4 groups x 72 bytes: u16[32] remap, u32 count, u32 offset -> count x 8 bytes
(u32 run-event bits, u32 frame count). Float block, (flags >> 16) & 0x1F groups x 12 bytes:
u8[4] component remap, u32 count, u32 offset -> count x 16 bytes (u32 packed: component
count | frame << 8 | pad byte << 24, then f32[3]). An empty group's offset is where the
next data would go.

Layout the game's writer produces (and ``build`` reproduces): header and table; motion
headers in slot order, each on a 16-byte boundary; then per motion in slot order: its track
array (4-aligned), the tracks' extremes (from a 16-byte boundary, 32 bytes each), the
tracks' buffers (each 4-aligned), the event block and its lists, the float block and its
frames. Blocks shared by reference (two motions pointing at one track array, two tracks at
one buffer or extremes block) are written once, where they are first reached, and shared
again when rebuilt; identical content that the file does not share stays separate. So the
blocks a file's motions use fit in the file once; ``parse`` refuses blocks that overlap past
that (they would be copied once per reference).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .errors import FormatError

MAGIC = b"LMT\0"
VERSIONS = (66, 67)
_HEAD = struct.Struct("<4sHH")
_MOTION = struct.Struct("<IIIi16s16sIII")
MOTION_SIZE = _MOTION.size          # 60
_TRACK = struct.Struct("<BBBB4sII16sI")
TRACK_SIZE = _TRACK.size            # 36
EXTREMES_SIZE = 32
EVENT_GROUPS = 4
_EVGROUP = struct.Struct("<64sII")  # u16[32] remap, count, offset
EVENT_SIZE = 8
_FLGROUP = struct.Struct("<4sII")   # u8[4] component remap, count, offset
FLOAT_FRAME_SIZE = 16
FLAG_EVENTS = 0x800000
# rMotionList::load (DDDA 0x00E9D330, DDO 0x015A4B80) turns offsets into pointers in place and
# skips a block these bits mark as already done by an earlier motion. They must match the
# sharing exactly (vanilla: bit 24 on 447/447 DDDA and 2,988/2,988 DDO motions that reuse an
# earlier motion's track array, never elsewhere; bits 25/26 never set), so parse refuses a
# mismatch and build computes them.
SHARED_TRACKS = 0x1000000
SHARED_EVENTS = 0x2000000
SHARED_FLOATS = 0x4000000
MAX_COUNT = 1 << 20                 # any count past this cannot fit a 32-bit motion list

CODECS = {
    1: ("vector3", 12), 2: ("rotation_quat3", 12), 3: ("linear_vector3", 16),
    4: ("bilinear_vector3_16bit", 8), 5: ("bilinear_vector3_8bit", 4),
    6: ("linear_quat4_14bit", 8), 7: ("bilinear_quat4_7bit", 4),
    11: ("bilinear_quatxw_14bit", 4), 12: ("bilinear_quatyw_14bit", 4),
    13: ("bilinear_quatzw_14bit", 4), 14: ("bilinear_quat4_11bit", 6), 15: ("bilinear_quat4_9bit", 5),
}
USAGES = {0: "local_rotation", 1: "local_position", 2: "local_scale", 3: "absolute_rotation",
          4: "absolute_position", 5: "usage5"}


def float_groups(flags: int) -> int:
    return (flags >> 16) & 0x1F


class Blob:
    """A byte block that may be shared by reference (buffers, extremes). Identity, not
    content, decides sharing: two tracks share one Blob only if the file shared the block."""
    __slots__ = ("data",)

    def __init__(self, data: bytes):
        self.data = bytes(data)

    def __repr__(self) -> str:
        return f"Blob({len(self.data)} bytes)"


@dataclass(eq=False)
class Track:
    codec: int
    usage: int
    bone_type: int
    bone: int
    weight: bytes            # f32, kept as its exact 4 bytes
    reference: bytes         # f32[4], exact bytes
    buffer: Blob | None = None
    extremes: Blob | None = None

    def key(self):
        return (self.codec, self.usage, self.bone_type, self.bone, self.weight, self.reference,
                None if self.buffer is None else self.buffer.data,
                None if self.extremes is None else self.extremes.data)


@dataclass(eq=False)
class TrackList:
    """A motion's track array; shared between motions when the file shares it."""
    tracks: list[Track] = field(default_factory=list)


@dataclass
class EventGroup:
    remap: bytes                                 # u16[32], exact bytes
    events: list[tuple[int, int]] = field(default_factory=list)   # (run-event bits, frames)


@dataclass
class FloatGroup:
    remap: bytes                                 # u8[4]
    frames: list[tuple[int, bytes]] = field(default_factory=list)  # (packed u32, f32[3] bytes)


@dataclass(eq=False)
class Motion:
    tracks: TrackList
    frames: int
    loop: int
    end_position: bytes      # f32[4] exact bytes
    end_rotation: bytes      # f32[4] exact bytes
    flags: int
    events: list[EventGroup] | None = None
    floats: list[FloatGroup] | None = None


@dataclass
class Lmt:
    version: int
    motions: list[Motion | None]

    @property
    def count(self) -> int:
        return sum(m is not None for m in self.motions)


# -- reading -------------------------------------------------------------------------------------

def _span(data: bytes, off: int, size: int, what: str) -> None:
    if off < 0 or size < 0 or off + size > len(data):
        raise FormatError("lmt", f"{what} at 0x{off:x} (+{size}) runs past the end of the file", off)


def _count(n: int, what: str, off: int) -> int:
    if n > MAX_COUNT:
        raise FormatError("lmt", f"{what} count {n} is not plausible", off)
    return n


def parse(data: bytes) -> Lmt:
    data = bytes(data)
    if len(data) < _HEAD.size:
        raise FormatError("lmt", f"truncated header ({len(data)} bytes)", 0)
    magic, version, n = _HEAD.unpack_from(data, 0)
    if magic != MAGIC:
        raise FormatError("lmt", f"not a motion list (magic {magic!r}, expected {MAGIC!r})", 0)
    if version not in VERSIONS:
        raise FormatError("lmt", f"version {version}; Dragon's Dogma uses {VERSIONS[0]} (DDDA) and "
                                 f"{VERSIONS[1]} (DDO)", 4)
    _span(data, 8, 4 * n, "motion table")
    offsets = struct.unpack_from(f"<{n}I", data, 8)
    lists: dict[int, TrackList] = {}
    blobs: dict[tuple[int, int, str], Blob] = {}
    # The writer puts each block in the file once -- motion headers, track arrays, extremes, buffers,
    # event and float blocks and their lists; a block two motions or two tracks share is written once
    # and shared by reference -- and build writes exactly the blocks read here, so a file that rebuilds
    # byte for byte (every vanilla one) holds them all. Blocks that overlap, e.g. every slot pointing
    # at one header or buffers running to the end from every offset, are refused instead of being
    # copied once per reference.
    room = [len(data)]

    def spend(n: int, what: str, where: int) -> None:
        room[0] -= n
        if room[0] < 0:
            raise FormatError("lmt", f"{what} overlaps other blocks (together they need more than the file's "
                                     f"{len(data):,} bytes)", where)

    def blob(off: int, size: int, what: str) -> Blob:
        key = (off, size, what)
        b = blobs.get(key)
        if b is None:
            _span(data, off, size, what)
            spend(size, f"a {what}", off)
            b = blobs[key] = Blob(data[off:off + size])
        return b

    motions: list[Motion | None] = []
    for i, off in enumerate(offsets):
        if not off:
            motions.append(None)
            continue
        _span(data, off, MOTION_SIZE, f"motion {i}")
        spend(MOTION_SIZE, f"motion {i}'s header", off)
        tr_off, nt, frames, loop, end_pos, end_rot, flags, ev_off, fl_off = _MOTION.unpack_from(data, off)
        _count(nt, f"motion {i} track", off)
        key = tr_off if nt else -1
        tl = lists.get(key) if nt else None
        if bool(flags & SHARED_TRACKS) != (tl is not None):
            raise FormatError("lmt", f"motion {i}: the shared-tracks flag (0x1000000) does not match whether its "
                                     "track array was already used by an earlier motion", off + 0x30)
        if flags & (SHARED_EVENTS | SHARED_FLOATS):
            raise FormatError("lmt", f"motion {i}: flags mark shared events or float tracks, which the game's "
                                     "files never use", off + 0x30)
        if tl is None:
            tl = TrackList()
            if nt:
                _span(data, tr_off, nt * TRACK_SIZE, f"motion {i} tracks")
                spend(nt * TRACK_SIZE, f"motion {i}'s track array", tr_off)
                for t in range(nt):
                    codec, usage, btype, bone, weight, bsize, boff, ref, ext = \
                        _TRACK.unpack_from(data, tr_off + t * TRACK_SIZE)
                    if bsize == 0 and boff != 0:
                        raise FormatError("lmt", f"motion {i} track {t}: empty buffer with an offset",
                                          tr_off + t * TRACK_SIZE)
                    buf = blob(boff, bsize, "buffer") if bsize else None
                    extremes = blob(ext, EXTREMES_SIZE, "extremes") if ext else None
                    tl.tracks.append(Track(codec, usage, btype, bone, weight, ref, buf, extremes))
                lists[key] = tl
        elif len(tl.tracks) != nt:
            raise FormatError("lmt", f"motion {i} shares a track array with a different track count", off)
        events = None
        if ev_off:
            _span(data, ev_off, EVENT_GROUPS * _EVGROUP.size, f"motion {i} events")
            spend(EVENT_GROUPS * _EVGROUP.size, f"motion {i}'s event block", ev_off)
            events = []
            for g in range(EVENT_GROUPS):
                remap, ne, lo = _EVGROUP.unpack_from(data, ev_off + g * _EVGROUP.size)
                _count(ne, f"motion {i} event group {g}", ev_off)
                if ne:
                    _span(data, lo, ne * EVENT_SIZE, f"motion {i} event list {g}")
                    spend(ne * EVENT_SIZE, f"motion {i}'s event list {g}", lo)
                events.append(EventGroup(remap, [struct.unpack_from("<II", data, lo + k * EVENT_SIZE)
                                                 for k in range(ne)]))
        floats = None
        if fl_off:
            ng = float_groups(flags)
            _span(data, fl_off, ng * _FLGROUP.size, f"motion {i} float tracks")
            spend(ng * _FLGROUP.size, f"motion {i}'s float block", fl_off)
            floats = []
            for g in range(ng):
                remap, nf, fo = _FLGROUP.unpack_from(data, fl_off + g * _FLGROUP.size)
                _count(nf, f"motion {i} float group {g}", fl_off)
                if nf:
                    _span(data, fo, nf * FLOAT_FRAME_SIZE, f"motion {i} float frames {g}")
                    spend(nf * FLOAT_FRAME_SIZE, f"motion {i}'s float frames {g}", fo)
                floats.append(FloatGroup(remap, [(struct.unpack_from("<I", data, fo + k * 16)[0],
                                                  data[fo + k * 16 + 4:fo + k * 16 + 16]) for k in range(nf)]))
        elif float_groups(flags):
            raise FormatError("lmt", f"motion {i}: flags name float tracks but there is no float block", off)
        motions.append(Motion(tl, frames, loop, end_pos, end_rot, flags, events, floats))
    return Lmt(version, motions)


# -- writing -------------------------------------------------------------------------------------

class _Out:
    def __init__(self):
        self.b = bytearray()

    def align(self, n: int) -> int:
        self.b += bytes(-len(self.b) % n)
        return len(self.b)

    def put(self, data: bytes) -> int:
        at = len(self.b)
        self.b += data
        return at


def build(lmt: Lmt) -> bytes:
    if lmt.version not in VERSIONS:
        raise FormatError("lmt", f"version {lmt.version} is not one Dragon's Dogma uses")
    n = len(lmt.motions)
    if n > 0xFFFF:
        raise FormatError("lmt", f"{n} motion slots; a motion list holds at most 65535")
    out = _Out()
    out.put(_HEAD.pack(MAGIC, lmt.version, n) + bytes(4 * n))
    heads: dict[int, int] = {}
    for i, m in enumerate(lmt.motions):
        if m is not None:
            heads[i] = out.align(16)
            out.put(bytes(MOTION_SIZE))
    placed_lists: dict[int, int] = {}
    placed_blobs: dict[int, int] = {}
    for i, m in enumerate(lmt.motions):
        if m is None:
            continue
        tl = m.tracks
        nt = len(tl.tracks)
        later = bool(nt) and id(tl) in placed_lists
        if nt and id(tl) not in placed_lists:
            tr_off = out.align(4)
            out.put(bytes(nt * TRACK_SIZE))
            placed_lists[id(tl)] = tr_off
            if any(t.extremes is not None and id(t.extremes) not in placed_blobs for t in tl.tracks):
                out.align(16)
                for t in tl.tracks:
                    if t.extremes is not None and id(t.extremes) not in placed_blobs:
                        if len(t.extremes.data) != EXTREMES_SIZE:
                            raise FormatError("lmt", f"motion {i}: extremes are {len(t.extremes.data)} bytes, not 32")
                        placed_blobs[id(t.extremes)] = out.put(t.extremes.data)
            for t in tl.tracks:
                if t.buffer is not None and id(t.buffer) not in placed_blobs:
                    out.align(4)
                    placed_blobs[id(t.buffer)] = out.put(t.buffer.data)
            for k, t in enumerate(tl.tracks):
                if t.buffer is not None and not t.buffer.data:
                    raise FormatError("lmt", f"motion {i} track {k}: an empty buffer must be None")
                _check_track(t, i, k)
                struct.pack_into("<BBBB4sII16sI", out.b, tr_off + k * TRACK_SIZE, t.codec, t.usage,
                                 t.bone_type, t.bone, t.weight,
                                 0 if t.buffer is None else len(t.buffer.data),
                                 0 if t.buffer is None else placed_blobs[id(t.buffer)], t.reference,
                                 0 if t.extremes is None else placed_blobs[id(t.extremes)])
        ev_off = 0
        if m.events is not None:
            if len(m.events) != EVENT_GROUPS:
                raise FormatError("lmt", f"motion {i}: {len(m.events)} event groups; the block holds {EVENT_GROUPS}")
            ev_off = out.align(4)
            out.put(bytes(EVENT_GROUPS * _EVGROUP.size))
            for g, grp in enumerate(m.events):
                if len(grp.remap) != 64:
                    raise FormatError("lmt", f"motion {i} event group {g}: remap must be 64 bytes")
                lo = len(out.b)
                for bits, frames in grp.events:
                    out.put(struct.pack("<II", bits, frames))
                _EVGROUP.pack_into(out.b, ev_off + g * _EVGROUP.size, grp.remap, len(grp.events), lo)
        fl_off = 0
        if m.floats is not None:
            if len(m.floats) != float_groups(m.flags):
                raise FormatError("lmt", f"motion {i}: {len(m.floats)} float groups but the flags say "
                                         f"{float_groups(m.flags)}")
            fl_off = out.align(4)
            out.put(bytes(len(m.floats) * _FLGROUP.size))
            for g, grp in enumerate(m.floats):
                if len(grp.remap) != 4:
                    raise FormatError("lmt", f"motion {i} float group {g}: remap must be 4 bytes")
                fo = len(out.b)
                for packed, value in grp.frames:
                    if len(value) != 12:
                        raise FormatError("lmt", f"motion {i} float group {g}: a frame value is 3 floats")
                    out.put(struct.pack("<I", packed) + value)
                _FLGROUP.pack_into(out.b, fl_off + g * _FLGROUP.size, grp.remap, len(grp.frames), fo)
        elif float_groups(m.flags):
            raise FormatError("lmt", f"motion {i}: flags name float tracks but there are none")
        if len(m.end_position) != 16 or len(m.end_rotation) != 16:
            raise FormatError("lmt", f"motion {i}: end position/rotation are 16 bytes each")
        flags = (m.flags & ~(SHARED_TRACKS | SHARED_EVENTS | SHARED_FLOATS)) | (SHARED_TRACKS if later else 0)
        _MOTION.pack_into(out.b, heads[i], placed_lists.get(id(tl), 0) if nt else 0, nt, m.frames, m.loop,
                          m.end_position, m.end_rotation, flags, ev_off, fl_off)
        struct.pack_into("<I", out.b, 8 + 4 * i, heads[i])
    return bytes(out.b)


def _check_track(t: Track, i: int, k: int) -> None:
    for name in ("codec", "usage", "bone_type", "bone"):
        v = getattr(t, name)
        if not isinstance(v, int) or not 0 <= v <= 0xFF:
            raise FormatError("lmt", f"motion {i} track {k}: {name} {v!r} is not a byte")
    if len(t.weight) != 4 or len(t.reference) != 16:
        raise FormatError("lmt", f"motion {i} track {k}: weight is 4 bytes and reference 16")


# -- reading helpers -----------------------------------------------------------------------------

def bones(lmt: Lmt) -> dict[int, int]:
    """Bone id -> number of tracks that drive it, over every motion (shared arrays once)."""
    seen: set[int] = set()
    out: dict[int, int] = {}
    for m in lmt.motions:
        if m is None or id(m.tracks) in seen:
            continue
        seen.add(id(m.tracks))
        for t in m.tracks.tracks:
            out[t.bone] = out.get(t.bone, 0) + 1
    return out


def info(lmt: Lmt) -> str:
    live = [m for m in lmt.motions if m is not None]
    tracks = sum(len(m.tracks.tracks) for m in live)
    frames = sum(m.frames for m in live)
    return (f"rMotionList v{lmt.version}: {len(live)} motions in {len(lmt.motions)} slots, "
            f"{tracks} bone tracks, {frames} frames, {len(bones(lmt))} bones driven")


def table(lmt: Lmt, shown: int = 60) -> list[str]:
    """One line per motion: slot, frames, loop, tracks (animated / constant), codecs, events."""
    out = [f"{'slot':>5} {'frames':>6} {'loop':>5} {'tracks':>7} {'anim':>5}  codecs            events floats"]
    live = [(i, m) for i, m in enumerate(lmt.motions) if m is not None]
    for i, m in live[:shown]:
        ts = m.tracks.tracks
        anim = sum(t.buffer is not None for t in ts)
        codecs = ",".join(str(c) for c in sorted({t.codec for t in ts}))
        ev = sum(len(g.events) for g in m.events) if m.events else 0
        fl = sum(len(g.frames) for g in m.floats) if m.floats else 0
        out.append(f"{i:>5} {m.frames:>6} {m.loop:>5} {len(ts):>7} {anim:>5}  {codecs:17} {ev:>6} {fl:>6}")
    if len(live) > shown:
        out.append(f"  ... and {len(live) - shown} more motions")
    return out

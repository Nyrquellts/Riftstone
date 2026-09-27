"""Binary deltas: a file described as copies of files the receiver already has, plus the bytes that are new.

A mod package ships deltas, not resources (package.py): the player's Riftstone rebuilds each resource from
the resources of *their own* copy of the game (the "bases") and the new bytes the delta carries, so the
only content a package holds is what its author wrote.

    ops = make(target, [base0, base1, ...])
    blob = encode(ops, len(target))
    assert apply(decode(blob), [base0, base1, ...]) == target

Encoding ``RSD1``: the magic, then LEB128 numbers: the target's size, the number of operations, and per
operation a tag --

    0  COPY  base index, offset, length       bytes a base already has
    1  ADD   length, then that many bytes      new bytes, the only content a delta carries

``decode`` checks everything: tags, base indexes, lengths (none empty), the total against the size, no
trailing bytes.  ``apply`` checks every copy against its base.  Matching is greedy (a block index over the
bases, extended both ways with slice compares), so a delta is small, not minimal; matches of 16 bytes or
more are found wherever they are, shorter ones only next to other matches.
"""
from __future__ import annotations

from .errors import RiftError

MAGIC = b"RSD1"
COPY, ADD = 0, 1
KEY = 16                    # the shortest match looked up in the index
MAX_SIZE = 1 << 28          # the largest target a delta describes (arc.MAX_DECODED is smaller)


class DeltaError(RiftError):
    """A delta that is damaged or does not fit its bases."""


# -- matching ------------------------------------------------------------------------------------
def _prefix(a: bytes, i: int, b: bytes, j: int, limit: int) -> int:
    """How many bytes a[i:] and b[j:] have in common, at most limit."""
    n, step = 0, 32
    while n < limit:
        m = min(step, limit - n)
        if a[i + n:i + n + m] == b[j + n:j + n + m]:
            n += m
            step = min(step * 2, 1 << 16)
            continue
        lo, hi = 0, m                       # a prefix of lo matches, one of hi does not
        while lo + 1 < hi:
            mid = (lo + hi) // 2
            if a[i + n:i + n + mid] == b[j + n:j + n + mid]:
                lo = mid
            else:
                hi = mid
        return n + lo
    return n


def _suffix(a: bytes, i: int, b: bytes, j: int, limit: int) -> int:
    """How many bytes a[:i] and b[:j] have in common at their ends, at most limit."""
    n, step = 0, 32
    while n < limit:
        m = min(step, limit - n)
        if a[i - n - m:i - n] == b[j - n - m:j - n]:
            n += m
            step = min(step * 2, 1 << 16)
            continue
        lo, hi = 0, m
        while lo + 1 < hi:
            mid = (lo + hi) // 2
            if a[i - n - mid:i - n] == b[j - n - mid:j - n]:
                lo = mid
            else:
                hi = mid
        return n + lo
    return n


def _step(total: int) -> int:
    """How far apart the index samples the bases: close for small files, sparser for big ones (a match
    of KEY + step - 1 bytes always contains a sample, so it is always found)."""
    for limit, step in ((1 << 18, 2), (1 << 20, 4), (1 << 22, 8), (1 << 24, 16)):
        if total <= limit:
            return step
    return 32


def make(target: bytes, bases: list[bytes]) -> list[tuple]:
    """Operations that rebuild target from the bases: COPY where a base has the bytes, ADD for the rest."""
    target = bytes(target)
    bases = [bytes(b) for b in bases]
    n = len(target)
    if n > MAX_SIZE:
        raise DeltaError(f"{n} bytes is more than a delta describes ({MAX_SIZE})")
    step = _step(sum(len(b) for b in bases))
    index: dict[int, tuple[int, int]] = {}
    for bi, b in enumerate(bases):
        for p in range(0, len(b) - KEY + 1, step):
            index.setdefault(hash(b[p:p + KEY]), (bi, p))
    ops: list[tuple] = []
    lit = t = 0
    last = None                             # (base, offset) right after the last copy
    while t + KEY <= n:
        seed = target[t:t + KEY]
        hit = None
        if last is not None and t - lit <= 256:
            bi, end = last
            b = bases[bi]
            for p in (end + (t - lit), end):        # the same number of bytes replaced / bytes inserted
                if p + KEY <= len(b) and b[p:p + KEY] == seed:
                    hit = (bi, p)
                    break
        if hit is None:
            h = index.get(hash(seed))
            if h is not None and bases[h[0]][h[1]:h[1] + KEY] == seed:
                hit = h
        if hit is None:
            t += 1
            continue
        bi, p = hit
        b = bases[bi]
        back = _suffix(target, t, b, p, min(t - lit, p))
        t, p = t - back, p - back
        length = _prefix(target, t, b, p, min(n - t, len(b) - p))
        if t > lit:
            ops.append((ADD, target[lit:t]))
        ops.append((COPY, bi, p, length))
        t += length
        lit = t
        last = (bi, p + length)
    if lit < n:
        ops.append((ADD, target[lit:]))
    return ops


def apply(ops: list[tuple], bases: list[bytes], size: int | None = None) -> bytes:
    """The bytes the operations describe; every copy is checked against its base."""
    out = bytearray()
    for op in ops:
        if op[0] == COPY:
            _, bi, off, length = op
            if not 0 <= bi < len(bases):
                raise DeltaError(f"a copy names base {bi}; there are {len(bases)}")
            b = bases[bi]
            if off < 0 or length <= 0 or off + length > len(b):
                raise DeltaError(f"a copy of {length} bytes at {off} does not fit base {bi} ({len(b)} bytes)")
            out += b[off:off + length]
        elif op[0] == ADD:
            if not op[1]:
                raise DeltaError("an empty ADD")
            out += op[1]
        else:
            raise DeltaError(f"unknown operation {op[0]!r}")
        if len(out) > MAX_SIZE:
            raise DeltaError("the delta describes more than it may")
    if size is not None and len(out) != size:
        raise DeltaError(f"the delta gives {len(out)} bytes, not {size}")
    return bytes(out)


def new_bytes(ops: list[tuple]) -> int:
    """How many bytes the delta itself carries (its ADDs)."""
    return sum(len(op[1]) for op in ops if op[0] == ADD)


def bases_used(ops: list[tuple]) -> set[int]:
    return {op[1] for op in ops if op[0] == COPY}


# -- encoding ------------------------------------------------------------------------------------
def _varint(v: int) -> bytes:
    if v < 0:
        raise DeltaError("a negative number cannot be encoded")
    out = bytearray()
    while True:
        byte = v & 0x7F
        v >>= 7
        if v:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def encode(ops: list[tuple], size: int) -> bytes:
    out = bytearray(MAGIC)
    out += _varint(size)
    out += _varint(len(ops))
    total = 0
    for op in ops:
        if op[0] == COPY:
            _, bi, off, length = op
            if length <= 0:
                raise DeltaError("an empty COPY")
            out += b"\x00" + _varint(bi) + _varint(off) + _varint(length)
            total += length
        elif op[0] == ADD:
            if not op[1]:
                raise DeltaError("an empty ADD")
            out += b"\x01" + _varint(len(op[1])) + bytes(op[1])
            total += len(op[1])
        else:
            raise DeltaError(f"unknown operation {op[0]!r}")
    if total != size:
        raise DeltaError(f"the operations give {total} bytes, not {size}")
    return bytes(out)


class _Reader:
    def __init__(self, data: bytes):
        self.data, self.pos = data, 0

    def varint(self) -> int:
        v = shift = 0
        while True:
            if self.pos >= len(self.data):
                raise DeltaError("the delta ends in the middle of a number")
            byte = self.data[self.pos]
            self.pos += 1
            v |= (byte & 0x7F) << shift
            if not byte & 0x80:
                if byte == 0 and shift:
                    raise DeltaError("a number is not in its shortest form")
                return v
            shift += 7
            if shift > 35:
                raise DeltaError("a number in the delta is too large")

    def take(self, n: int) -> bytes:
        if n > len(self.data) - self.pos:
            raise DeltaError("the delta ends early")
        out = self.data[self.pos:self.pos + n]
        self.pos += n
        return out


def decode(blob: bytes) -> tuple[list[tuple], int]:
    """(operations, target size) of an encoded delta; anything malformed raises DeltaError."""
    blob = bytes(blob)
    if blob[:4] != MAGIC:
        raise DeltaError("not a Riftstone delta (RSD1)")
    r = _Reader(blob)
    r.pos = 4
    size = r.varint()
    if size > MAX_SIZE:
        raise DeltaError(f"the delta describes {size} bytes; at most {MAX_SIZE}")
    count = r.varint()
    if count > len(blob):                    # every operation takes at least one byte
        raise DeltaError("the delta claims more operations than it has bytes")
    ops: list[tuple] = []
    total = 0
    for _ in range(count):
        tag = r.take(1)[0]
        if tag == COPY:
            bi, off, length = r.varint(), r.varint(), r.varint()
            if length == 0:
                raise DeltaError("an empty COPY")
            ops.append((COPY, bi, off, length))
            total += length
        elif tag == ADD:
            length = r.varint()
            if length == 0:
                raise DeltaError("an empty ADD")
            ops.append((ADD, r.take(length)))
            total += length
        else:
            raise DeltaError(f"unknown operation tag {tag}")
        if total > size:
            raise DeltaError("the operations give more bytes than the delta's size")
    if r.pos != len(blob):
        raise DeltaError(f"{len(blob) - r.pos} bytes follow the last operation")
    if total != size:
        raise DeltaError(f"the operations give {total} bytes, not {size}")
    return ops, size

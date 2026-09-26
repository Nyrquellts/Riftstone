"""Bounded little-endian reading: every read checks the buffer end first."""
from __future__ import annotations

import struct

from .errors import FormatError

_U8 = struct.Struct("<B")
_U16 = struct.Struct("<H")
_U32 = struct.Struct("<I")
_S32 = struct.Struct("<i")


class Reader:
    __slots__ = ("data", "pos", "end", "fmt")

    def __init__(self, data: bytes | memoryview, fmt: str, pos: int = 0, end: int | None = None):
        self.data = data
        self.pos = pos
        self.end = len(data) if end is None else end
        self.fmt = fmt
        if not 0 <= self.pos <= self.end <= len(data):
            raise FormatError(fmt, "window outside the buffer", pos)

    def need(self, n: int) -> None:
        if n < 0 or self.pos + n > self.end:
            raise FormatError(self.fmt, f"needs {n} more bytes but only {self.end - self.pos} remain", self.pos)

    def u8(self) -> int:
        self.need(1)
        v = self.data[self.pos]
        self.pos += 1
        return v

    def u16(self) -> int:
        self.need(2)
        v = _U16.unpack_from(self.data, self.pos)[0]
        self.pos += 2
        return v

    def u32(self) -> int:
        self.need(4)
        v = _U32.unpack_from(self.data, self.pos)[0]
        self.pos += 4
        return v

    def s32(self) -> int:
        self.need(4)
        v = _S32.unpack_from(self.data, self.pos)[0]
        self.pos += 4
        return v

    def take(self, n: int) -> bytes:
        self.need(n)
        v = bytes(self.data[self.pos:self.pos + n])
        self.pos += n
        return v

    def unpack(self, st: struct.Struct) -> tuple:
        self.need(st.size)
        v = st.unpack_from(self.data, self.pos)
        self.pos += st.size
        return v

    def cstring(self, limit: int = 4096) -> bytes:
        """Bytes up to (not including) a NUL; consumes the NUL."""
        stop = min(self.end, self.pos + limit)
        idx = bytes(self.data[self.pos:stop]).find(b"\0")
        if idx < 0:
            raise FormatError(self.fmt, f"unterminated string (limit {limit})", self.pos)
        v = bytes(self.data[self.pos:self.pos + idx])
        self.pos += idx + 1
        return v

    def remaining(self) -> int:
        return self.end - self.pos


def cstring_at(data: bytes, offset: int, fmt: str, limit: int = 4096) -> bytes:
    if not 0 <= offset < len(data):
        raise FormatError(fmt, "string offset outside the buffer", offset)
    stop = min(len(data), offset + limit)
    idx = data.find(b"\0", offset, stop)
    if idx < 0:
        raise FormatError(fmt, "unterminated string", offset)
    return data[offset:idx]

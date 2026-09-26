"""Windows executables and DLLs (PE files), as far as Riftstone needs them.

What the header says (the machine, DLL or program, IMAGE_FILE_LARGE_ADDRESS_AWARE), the names a DLL
exports, and the header checksum Windows' CheckSumMappedFile computes.  Everything reads bytes and refuses
what is not a PE file with a FormatError; :func:`with_large_address_aware` returns new bytes, and
:func:`write_large_address_aware_copy` is the one function that writes a file: a copy, never the file it
read.

Why it matters here: without the large-address-aware flag Windows gives a 32-bit program 2 GB of address
space instead of 4 GB.  Steam's DDDA.exe (build 2364871) has it; the loader logs it at every start, and a
copy of an exe that lacks it can be written with the flag set (``Riftstone.cmd laa``).
"""
from __future__ import annotations

import os
import struct
import sys
from array import array
from dataclasses import dataclass
from pathlib import Path

from .errors import FormatError, RiftError

IMAGE_FILE_LARGE_ADDRESS_AWARE = 0x0020
IMAGE_FILE_DLL = 0x2000
MACHINES = {0x014C: "x86", 0x8664: "x64", 0xAA64: "arm64", 0x01C4: "arm"}
_MAX_NAMES = 1 << 16


@dataclass(frozen=True)
class Header:
    machine: int
    characteristics: int
    timestamp: int
    checksum: int
    pe32plus: bool
    offset: int                # where "PE\\0\\0" starts
    optional_size: int         # SizeOfOptionalHeader
    sections: tuple            # (virtual address, span, raw offset, raw size) each

    @property
    def machine_name(self) -> str:
        return MACHINES.get(self.machine, f"machine 0x{self.machine:04x}")

    @property
    def is_dll(self) -> bool:
        return bool(self.characteristics & IMAGE_FILE_DLL)

    @property
    def large_address_aware(self) -> bool:
        return bool(self.characteristics & IMAGE_FILE_LARGE_ADDRESS_AWARE)

    @property
    def characteristics_offset(self) -> int:
        return self.offset + 4 + 18

    @property
    def checksum_offset(self) -> int:
        return self.offset + 24 + 64          # OptionalHeader.CheckSum, the same place in PE32 and PE32+

    @property
    def optional_offset(self) -> int:
        return self.offset + 24


def header(data: bytes) -> Header:
    """The PE header of ``data``; FormatError when it is not a Windows executable or DLL."""
    if len(data) < 0x40 or data[:2] != b"MZ":
        raise FormatError("pe", "not a Windows executable (no MZ header)")
    at = struct.unpack_from("<I", data, 0x3C)[0]
    if at < 0x40 or at + 24 > len(data) or data[at:at + 4] != b"PE\0\0":
        raise FormatError("pe", "not a Windows executable (no PE header)", 0x3C)
    machine, nsec, stamp, _, _, optsz, chars = struct.unpack_from("<HHIIIHH", data, at + 4)
    opt = at + 24
    if optsz < 68 or opt + optsz > len(data):
        raise FormatError("pe", "the optional header is cut short", opt)
    magic = struct.unpack_from("<H", data, opt)[0]
    if magic not in (0x10B, 0x20B):
        raise FormatError("pe", f"unknown optional header magic 0x{magic:x}", opt)
    checksum = struct.unpack_from("<I", data, opt + 64)[0]
    first = opt + optsz
    if nsec > 96 or first + nsec * 40 > len(data):
        raise FormatError("pe", "the section table is cut short", first)
    sections = []
    for i in range(nsec):
        vsize, va, rsize, raw = struct.unpack_from("<IIII", data, first + i * 40 + 8)
        sections.append((va, max(vsize, rsize), raw, rsize))
    return Header(machine, chars, stamp, checksum, magic == 0x20B, at, optsz, tuple(sections))


def _offset(data: bytes, h: Header, rva: int, n: int = 1) -> int:
    for va, span, raw, rsize in h.sections:
        if va <= rva and rva + n <= va + span:
            off = raw + rva - va
            if rva - va + n > rsize or off + n > len(data):
                break
            return off
    raise FormatError("pe", f"address 0x{rva:x} is not in the file")


def _cstr(data: bytes, off: int, limit: int = 512) -> str:
    end = data.find(b"\0", off, off + limit)
    if end < 0:
        raise FormatError("pe", "an export name has no end", off)
    return data[off:end].decode("ascii", "replace")


def exports(data: bytes) -> list[str]:
    """The names a DLL exports, in the export table's order ([] when it has none)."""
    h = header(data)
    first = 112 if h.pe32plus else 96              # the data directories, after NumberOfRvaAndSizes
    if h.optional_size < first + 8:
        return []
    count = struct.unpack_from("<I", data, h.optional_offset + first - 4)[0]
    directory = h.optional_offset + first
    if count < 1:
        return []
    rva, size = struct.unpack_from("<II", data, directory)
    if not rva or not size:
        return []
    base = _offset(data, h, rva, 40)
    names, table = struct.unpack_from("<II", data, base + 24)[0], struct.unpack_from("<I", data, base + 32)[0]
    if names > _MAX_NAMES:
        raise FormatError("pe", f"{names} export names", base + 24)
    if not names:
        return []
    at = _offset(data, h, table, 4 * names)
    return [_cstr(data, _offset(data, h, struct.unpack_from("<I", data, at + 4 * i)[0])) for i in range(names)]


def checksum(data: bytes) -> int:
    """The header checksum as Windows' CheckSumMappedFile computes it (the field itself counted as zero)."""
    h = header(data)
    buf = bytearray(data)
    buf[h.checksum_offset:h.checksum_offset + 4] = b"\0\0\0\0"
    if len(buf) % 2:
        buf.append(0)
    words = array("H")
    words.frombytes(bytes(buf))
    if sys.byteorder == "big":
        words.byteswap()
    total = sum(words)
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (total + len(data)) & 0xFFFFFFFF


def with_large_address_aware(data: bytes) -> bytes:
    """``data`` with IMAGE_FILE_LARGE_ADDRESS_AWARE set and the header checksum redone; unchanged when the
    flag is already set.  Only those two fields differ."""
    h = header(data)
    if h.large_address_aware:
        return bytes(data)
    out = bytearray(data)
    struct.pack_into("<H", out, h.characteristics_offset, h.characteristics | IMAGE_FILE_LARGE_ADDRESS_AWARE)
    struct.pack_into("<I", out, h.checksum_offset, checksum(bytes(out)))
    return bytes(out)


def describe(path: Path) -> dict:
    """What an exe or DLL on disk is: machine, DLL or program, large-address aware, the address space a 32-bit
    one gets on 64-bit Windows."""
    data = Path(path).read_bytes()
    h = header(data)
    bits = 64 if h.pe32plus else 32
    space = None
    if bits == 32:
        space = 4096 if h.large_address_aware else 2048
    return {"path": Path(path), "machine": h.machine_name, "bits": bits, "dll": h.is_dll,
            "large_address_aware": h.large_address_aware, "address_space_mb": space, "timestamp": h.timestamp,
            "checksum": h.checksum}


def write_large_address_aware_copy(src: Path, dst: Path) -> dict:
    """Write ``dst``: ``src`` with the large-address-aware flag set (and its checksum redone).  Never writes
    ``src`` itself.  Returns {"changed": the flag was not set before, "checksum": the new header checksum}."""
    src, dst = Path(src), Path(dst)
    if dst.exists() and src.exists() and os.path.samefile(src, dst):
        raise RiftError(f"{dst} is the file to copy; write the copy somewhere else (the original is never changed)")
    try:
        data = src.read_bytes()
    except OSError as e:
        raise RiftError(f"{src} cannot be read: {e.strerror or e}") from e
    before = header(data)
    out = with_large_address_aware(data)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".riftstone-tmp")
    tmp.write_bytes(out)
    os.replace(tmp, dst)
    return {"changed": not before.large_address_aware, "checksum": header(out).checksum}

"""What every thread of a 32-bit game was doing, from a minidump the loader wrote (hang-*.dmp, snapshot-*.dmp,
crash-*.dmp).  Stdlib only; every offset is checked, so a damaged dump is refused, never misread.

``threads(data)``: each thread's id, where it is (module+offset) and the return addresses on its stack that point
into a module other than Windows' own (the likely callers, newest first).  ``summary(threads)``: the threads grouped by
what they are in (the game, Direct3D or DXVK, the graphics driver, an overlay, a plugin), for a report in words.

A minidump written by the loader keeps each stack in its memory list, not beside the thread (the thread's own
location descriptor is 0), so a stack is found by its address there.
"""
from __future__ import annotations

import bisect
import struct
from dataclasses import dataclass, field

from .errors import FormatError

MAGIC = b"MDMP"
THREAD_LIST, MODULE_LIST, MEMORY_LIST = 3, 4, 5
X86_CONTEXT_EIP, X86_CONTEXT_ESP = 0xB8, 0xC4
STACK_SCAN = 64 << 10         # bytes of each stack read for callers (the loader's own report reads 1 KB)
MAX_FRAMES = 12
# Windows' own modules: a return address into one of these says less than the first one past them
SYSTEM = ("ntdll.dll", "kernelbase.dll", "kernel32.dll", "win32u.dll", "user32.dll", "ucrtbase.dll", "msvcrt.dll",
          "combase.dll", "rpcrt4.dll", "gdi32.dll", "gdi32full.dll", "cryptbase.dll", "sechost.dll")
KINDS = (   # (what a module is, by name) for the summary; first match wins
    ("the game", ("ddda.exe", "ddo.exe")),
    ("Direct3D 9 (DXVK or Windows')", ("d3d9.dll", "d3d9on12.dll")),
    ("the graphics driver", ("nvoglv32.dll", "nvgpucomp32.dll", "nvd3dum.dll", "nvwgf2um.dll", "atiumdag.dll",
                             "amdvlk32.dll", "igvk32.dll", "nvapi_impl.dll", "vulkan-1.dll")),
    ("Steam's overlay or shader cache", ("gameoverlayrenderer.dll", "steamclient.dll", "vklayer_steam_fossilize.dll",
                                         "tier0_s.dll")),
    ("OBS's game capture", ("graphics-hook32.dll",)),
    ("another overlay or recorder", ("game_detour_32.dll", "discordhook.dll", "rtsshooks.dll", "medal")),
    ("the Riftstone loader", ("dinput8.dll", "riftstone_loader.dll")),
    ("sound", ("xaudio2_7.dll", "audioses.dll", "dsound.dll")),
    ("the dump being written", ("dbgcore.dll", "dbghelp.dll")),
)


@dataclass
class Thread:
    id: int
    eip: int
    where: str                       # module+0xoffset, or 0x... outside every module
    frames: list[str] = field(default_factory=list)


def _u32(d: bytes, at: int) -> int:
    if at < 0 or at + 4 > len(d):
        raise FormatError("minidump", f"the minidump ends before offset {at:#x}")
    return struct.unpack_from("<I", d, at)[0]


def _u64(d: bytes, at: int) -> int:
    if at < 0 or at + 8 > len(d):
        raise FormatError("minidump", f"the minidump ends before offset {at:#x}")
    return struct.unpack_from("<Q", d, at)[0]


def _name(d: bytes, at: int) -> str:
    n = _u32(d, at)
    if n > 2048 or at + 4 + n > len(d):
        raise FormatError("minidump", "a module name runs past the minidump")
    return d[at + 4: at + 4 + n].decode("utf-16-le", "replace")


def _streams(d: bytes) -> dict[int, tuple[int, int]]:
    if d[:4] != MAGIC:
        raise FormatError("minidump", "not a minidump (no MDMP)")
    n, rva = _u32(d, 8), _u32(d, 12)
    if n > 4096:
        raise FormatError("minidump", "the minidump names too many streams")
    out = {}
    for i in range(n):
        kind, size, at = struct.unpack_from("<III", d, rva + 12 * i) if rva + 12 * i + 12 <= len(d) else (None,) * 3
        if kind is None:
            raise FormatError("minidump", "the stream directory runs past the minidump")
        if at + size > len(d):
            raise FormatError("minidump", f"stream {kind} runs past the minidump")
        out.setdefault(kind, (at, size))
    return out


def modules(d: bytes) -> list[tuple[int, int, str]]:
    """(base, size, file name) of every module."""
    st = _streams(d)
    if MODULE_LIST not in st:
        return []
    at, size = st[MODULE_LIST]
    n = _u32(d, at)
    if 4 + 108 * n > size:
        raise FormatError("minidump", "the module list is longer than its stream")
    out = []
    for i in range(n):
        e = at + 4 + 108 * i
        base, msize, name_rva = _u64(d, e), _u32(d, e + 8), _u32(d, e + 20)
        out.append((base, msize, _name(d, name_rva).replace("/", "\\").split("\\")[-1]))
    return out


def threads(d: bytes) -> list[Thread]:
    """Every thread: where it is and the likely callers on its stack (newest first, Windows' own left out)."""
    st = _streams(d)
    mods = sorted(modules(d))
    bases = [m[0] for m in mods]

    def where(a: int) -> str | None:
        i = bisect.bisect_right(bases, a) - 1
        for j in range(i, max(-1, i - 64), -1):     # entries that overlap (a damaged list): the nearest holding it
            base, size, name = mods[j]
            if base <= a < base + size:
                return f"{name}+0x{a - base:x}"
        return None

    mem = []
    if MEMORY_LIST in st:
        at, size = st[MEMORY_LIST]
        n = _u32(d, at)
        if 4 + 16 * n > size:
            raise FormatError("minidump", "the memory list is longer than its stream")
        for i in range(n):
            e = at + 4 + 16 * i
            start, dsize, drva = _u64(d, e), _u32(d, e + 8), _u32(d, e + 12)
            if drva + dsize > len(d):
                raise FormatError("minidump", "a memory range runs past the minidump")
            mem.append((start, drva, dsize))
    if THREAD_LIST not in st:
        return []
    at, size = st[THREAD_LIST]
    n = _u32(d, at)
    if 4 + 48 * n > size:
        raise FormatError("minidump", "the thread list is longer than its stream")
    out = []
    for i in range(n):
        e = at + 4 + 48 * i
        tid = _u32(d, e)
        st_start, st_size, st_rva = _u64(d, e + 24), _u32(d, e + 32), _u32(d, e + 36)
        ctx_size, ctx_rva = _u32(d, e + 40), _u32(d, e + 44)
        eip = _u32(d, ctx_rva + X86_CONTEXT_EIP) if ctx_size >= X86_CONTEXT_EIP + 4 else 0
        stack = b""
        if st_rva and st_rva + st_size <= len(d):
            stack = d[st_rva: st_rva + st_size]
        else:
            for start, drva, dsize in mem:
                if start <= st_start < start + dsize:
                    off = st_start - start
                    stack = d[drva + off: drva + min(dsize, off + st_size)]
                    break
        frames: list[str] = []
        stack = stack[:STACK_SCAN]
        for off in range(0, len(stack) - 3, 4):
            w = where(struct.unpack_from("<I", stack, off)[0])
            # a word in a module's first page (its headers) is data, such as the module's own base, not a return
            if w and int(w.rsplit("+0x", 1)[1], 16) < 0x1000:
                continue
            if w and w.split("+")[0].lower() not in SYSTEM and (not frames or frames[-1] != w):
                frames.append(w)
                if len(frames) >= MAX_FRAMES:
                    break
        out.append(Thread(tid, eip, where(eip) or f"0x{eip:08x}", frames))
    return out


def kind_of(module: str) -> str:
    low = module.lower()
    for what, names in KINDS:
        if any(low == n or (not n.endswith((".dll", ".exe")) and n in low) for n in names):
            return what
    if low.endswith(".asi"):
        return f"plugin {module}"
    return module


def summary(ts: list[Thread]) -> list[str]:
    """One line per kind of thread: how many, and what they are in (the first module past Windows' own)."""
    groups: dict[str, list[Thread]] = {}
    for t in ts:
        first = t.frames[0].split("+")[0] if t.frames else t.where.split("+")[0]
        groups.setdefault(kind_of(first), []).append(t)
    lines = []
    for what, members in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        tops = sorted({m.frames[0] for m in members if m.frames})[:3]
        lines.append(f"{len(members)} thread(s) in {what}" + (f" (at {', '.join(tops)})" if tops else ""))
    return lines

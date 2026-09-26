"""Read-only probe of a running DDDA.exe: find engine objects by their vtable and watch their memory.

DEV-ONLY, stdlib (ctypes), Windows.  It opens the game with PROCESS_QUERY_INFORMATION | PROCESS_VM_READ
only: it never writes memory, injects code, attaches a debugger or touches the window, so the game runs
as normal while it samples.  It does not start the game.  The owner runs it, while playing; no gate,
test or assistant runs it against the game without the owner's word for that session.

    python tools/live_probe.py find 0x015623D4                 # objects whose vtable is sSetManager's
    python tools/live_probe.py watch 0x015623D4 --from 0x700 --to 0xA00 --seconds 300 --out probe.log
    python tools/live_probe.py read 0x018FA4B0 --deref --len 0x880   # a singleton pointer, then its object
    python tools/live_probe.py read 0x018D1578 --deref --len 0xF00 --floats   # sShadow (docs/native-dynamic-recipes.md 4)

`watch` logs, every --every seconds, which dwords of each found object changed and to what; a fight
that fills and empties an array shows its offset, stride and the count that bounds it.  `read --floats`
prints each dword as a float too.
"""
from __future__ import annotations

import argparse
import ctypes
import struct
import sys
import time
from ctypes import wintypes

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
MEM_COMMIT = 0x1000
MEM_PRIVATE = 0x20000
PAGE_READABLE = 0x02 | 0x04 | 0x20 | 0x40 | 0x08 | 0x80    # R, RW, RX, RWX, WRITECOPY, EXECUTE_WRITECOPY
PAGE_GUARD = 0x100

if sys.platform != "win32":  # pragma: no cover -- the game and this tool are Windows only
    raise ImportError("live_probe reads a Windows process")
k32 = ctypes.WinDLL("kernel32", use_last_error=True)


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", wintypes.DWORD), ("PartitionId", wintypes.WORD),
                ("RegionSize", ctypes.c_size_t), ("State", wintypes.DWORD), ("Protect", wintypes.DWORD),
                ("Type", wintypes.DWORD)]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]


k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
k32.OpenProcess.restype = wintypes.HANDLE
k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
k32.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                  ctypes.POINTER(ctypes.c_size_t)]
k32.VirtualQueryEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(MEMORY_BASIC_INFORMATION),
                               ctypes.c_size_t]
k32.VirtualQueryEx.restype = ctypes.c_size_t
k32.CloseHandle.argtypes = [wintypes.HANDLE]
k32.IsWow64Process.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]


def find_pid(exe: str = "ddda.exe") -> int | None:
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    try:
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.lower() == exe:
                return entry.th32ProcessID
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return None


class Process:
    """A read-only handle on another process."""

    def __init__(self, pid: int):
        self.pid = pid
        self.h = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
        if not self.h:
            raise OSError(ctypes.get_last_error(), f"cannot open process {pid} for reading")

    def close(self) -> None:
        k32.CloseHandle(self.h)

    def read(self, addr: int, n: int) -> bytes | None:
        buf = ctypes.create_string_buffer(n)
        got = ctypes.c_size_t()
        if not k32.ReadProcessMemory(self.h, ctypes.c_void_p(addr), buf, n, ctypes.byref(got)) or got.value != n:
            return None
        return buf.raw

    def u32(self, addr: int) -> int | None:
        b = self.read(addr, 4)
        return struct.unpack("<I", b)[0] if b else None

    def top(self) -> int:
        """End of the target's user address space: 4 GB for a 32-bit process (DDDA.exe is large-address-aware,
        so its heap reaches past 2 GB), 128 TB for a 64-bit one."""
        wow = wintypes.BOOL()
        k32.IsWow64Process(self.h, ctypes.byref(wow))
        return 0xFFFF0000 if wow.value else 0x7FFFFFFF0000

    def regions(self, private_only: bool = True):
        """Committed, readable regions (heap objects live in private memory)."""
        mbi = MEMORY_BASIC_INFORMATION()
        addr, top = 0, self.top()
        while addr < top and k32.VirtualQueryEx(self.h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
            base, size = mbi.BaseAddress or 0, mbi.RegionSize
            if (mbi.State == MEM_COMMIT and mbi.Protect & PAGE_READABLE and not mbi.Protect & PAGE_GUARD
                    and (mbi.Type == MEM_PRIVATE or not private_only)):
                yield base, size
            addr = base + size

    def scan_u32(self, value: int, private_only: bool = True, limit: int = 64) -> list[int]:
        """Addresses (4-byte aligned) holding value: for a vtable VA, the objects of that class."""
        needle = struct.pack("<I", value)
        hits: list[int] = []
        for base, size in self.regions(private_only):
            for off in range(0, size, 1 << 22):
                chunk = self.read(base + off, min(1 << 22, size - off))
                if not chunk:
                    continue
                i = chunk.find(needle)
                while i != -1:
                    if (base + off + i) % 4 == 0:
                        hits.append(base + off + i)
                        if len(hits) >= limit:
                            return hits
                    i = chunk.find(needle, i + 1)
        return hits


def attach() -> Process:
    pid = find_pid()
    if pid is None:
        raise SystemExit("DDDA.exe is not running; start the game first (this tool never starts it)")
    return Process(pid)


def cmd_find(args) -> int:
    p = attach()
    try:
        hits = p.scan_u32(args.vtable)
        print(f"{len(hits)} object(s) with vtable {args.vtable:#010x}: " + ", ".join(f"{h:#010x}" for h in hits))
    finally:
        p.close()
    return 0


def _dwords(b: bytes) -> list[int]:
    return list(struct.unpack(f"<{len(b) // 4}I", b[:len(b) // 4 * 4]))


def dump_lines(addr: int, data: bytes, floats: bool = False) -> list[str]:
    """16 bytes a line: the address, the offset, the dwords (and each as a float with `floats`)."""
    out = []
    for i in range(0, len(data), 16):
        words = _dwords(data[i:i + 16])
        line = f"{addr + i:#010x}  +{i:#06x}  " + " ".join(f"{v:08x}" for v in words)
        if floats:
            line += "   " + " ".join(f"{struct.unpack('<f', struct.pack('<I', v))[0]:.6g}" for v in words)
        out.append(line)
    return out


def cmd_watch(args) -> int:
    p = attach()
    out = open(args.out, "a", encoding="utf-8") if args.out else sys.stdout
    try:
        objs = p.scan_u32(args.vtable)
        if not objs:
            raise SystemExit(f"no object with vtable {args.vtable:#010x} found")
        print(f"# {time.strftime('%H:%M:%S')} watching {len(objs)} object(s) {[hex(o) for o in objs]} "
              f"[{args.start:#x}, {args.end:#x}) every {args.every}s", file=out, flush=True)
        last = {o: p.read(o + args.start, args.end - args.start) for o in objs}
        for o, b in last.items():
            print(f"# {o:#010x} start: " + " ".join(f"{v:08x}" for v in _dwords(b or b"")), file=out, flush=True)
        t_end = time.time() + args.seconds
        while time.time() < t_end:
            time.sleep(args.every)
            for o in objs:
                cur = p.read(o + args.start, args.end - args.start)
                if cur is None:
                    print(f"{time.strftime('%H:%M:%S')} {o:#010x} unreadable (freed?)", file=out, flush=True)
                    continue
                prev = last[o] or cur
                changed = [(args.start + 4 * i, a, b) for i, (a, b) in enumerate(zip(_dwords(prev), _dwords(cur))) if a != b]
                if changed:
                    print(f"{time.strftime('%H:%M:%S')} {o:#010x} " +
                          " ".join(f"+{off:#x}:{a:08x}->{b:08x}" for off, a, b in changed[:40]) +
                          (f" (+{len(changed) - 40} more)" if len(changed) > 40 else ""), file=out, flush=True)
                last[o] = cur
    finally:
        p.close()
        if out is not sys.stdout:
            out.close()
    return 0


def cmd_read(args) -> int:
    p = attach()
    try:
        addr = args.addr
        if args.deref:
            ptr = p.u32(addr)
            print(f"[{addr:#010x}] = {ptr:#010x}" if ptr is not None else f"[{addr:#010x}] unreadable")
            if not ptr:
                return 1
            addr = ptr
        b = p.read(addr, args.len)
        if b is None:
            print(f"{addr:#010x}: unreadable")
            return 1
        for line in dump_lines(addr, b, args.floats):
            print(line)
    finally:
        p.close()
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("find")
    f.add_argument("vtable", type=lambda s: int(s, 0))
    w = sub.add_parser("watch")
    w.add_argument("vtable", type=lambda s: int(s, 0))
    w.add_argument("--from", dest="start", type=lambda s: int(s, 0), default=0)
    w.add_argument("--to", dest="end", type=lambda s: int(s, 0), default=0x1000)
    w.add_argument("--every", type=float, default=0.5)
    w.add_argument("--seconds", type=float, default=120)
    w.add_argument("--out")
    r = sub.add_parser("read")
    r.add_argument("addr", type=lambda s: int(s, 0))
    r.add_argument("--deref", action="store_true", help="addr holds a pointer: read what it points at")
    r.add_argument("--len", type=lambda s: int(s, 0), default=0x100)
    r.add_argument("--floats", action="store_true", help="print each dword as a float too")
    args = ap.parse_args(argv)
    if args.cmd == "watch" and not (0 <= args.start < args.end <= 0x100000 and args.every > 0 and args.seconds >= 0):
        ap.error("watch needs 0 <= --from < --to <= 0x100000, --every above 0 and --seconds of 0 or more")
    if args.cmd == "read" and not 0 < args.len <= 0x100000:
        ap.error("--len is 1 to 0x100000 bytes")
    return {"find": cmd_find, "watch": cmd_watch, "read": cmd_read}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())

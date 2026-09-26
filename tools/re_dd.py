"""Static RE helper for DDDA.exe (build 2364871, base 0x400000, .text plaintext).

DEV-ONLY tool, NOT part of the stdlib-only runtime: needs `pip install capstone`.
Run from the repo root; it finds the game via riftstone.game.find_game and reads the exe
read-only. See docs/re-enemy-cap.md for how it was used to locate the enemy managers.

Quick use:
    import tools.re_dd as R   # or add tools/ to path
    R.show(0x00433BC0, n=0x40)                 # disassemble
    R.find_refs(0x018FA4B0)                    # who references this VA (4-byte LE) in .text
    R.find_str("sEnemyManager")               # a rodata/data string's VA
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from riftstone.game import find_game  # noqa: E402

try:
    from capstone import CS_ARCH_X86, CS_MODE_32, Cs
except ImportError:  # pragma: no cover
    raise SystemExit("re_dd needs capstone: pip install capstone")

EXE = Path(find_game().exe).read_bytes()
_pe = struct.unpack_from("<I", EXE, 0x3C)[0]
_nsec = struct.unpack_from("<H", EXE, _pe + 6)[0]
_optsz = struct.unpack_from("<H", EXE, _pe + 20)[0]
BASE = struct.unpack_from("<I", EXE, _pe + 0x34)[0]
_s0 = _pe + 24 + _optsz
SECS = []
for _i in range(_nsec):
    _o = _s0 + _i * 40
    _nm = EXE[_o:_o + 8].rstrip(b"\0").decode("latin-1", "replace")
    _vsz, _va, _rsz, _rptr = struct.unpack_from("<IIII", EXE, _o + 8)
    SECS.append((_nm, _va, _vsz, _rptr, _rsz))


def va2off(va):
    rva = va - BASE
    for _nm, va0, vsz, rptr, rsz in SECS:
        if va0 <= rva < va0 + max(vsz, rsz):
            return rptr + (rva - va0)
    return None


def off2va(off):
    for _nm, va0, vsz, rptr, rsz in SECS:
        if rptr <= off < rptr + rsz:
            return BASE + va0 + (off - rptr)
    return None


def sec_of(va):
    rva = va - BASE
    for nm, va0, vsz, rptr, rsz in SECS:
        if va0 <= rva < va0 + max(vsz, rsz):
            return nm
    return None


def read(va, n):
    o = va2off(va)
    return EXE[o:o + n] if o is not None else b""


_md = Cs(CS_ARCH_X86, CS_MODE_32)
_md.detail = True


def dis(va, n=40, count=None):
    """Disassemble n bytes (or `count` instructions) starting at VA."""
    o = va2off(va)
    code = EXE[o:o + (n if count is None else count * 16)]
    out = []
    for ins in _md.disasm(code, va):
        out.append(ins)
        if count and len(out) >= count:
            break
        if count is None and ins.address + ins.size >= va + n:
            break
    return out


def show(va, n=40, count=None):
    for ins in dis(va, n, count):
        print(f"  0x{ins.address:08X}  {ins.bytes.hex():<18} {ins.mnemonic} {ins.op_str}")


def find_refs(target_va, sections=(".text",)):
    """VAs of 4-byte little-endian pointers to target_va within the given sections."""
    key = struct.pack("<I", target_va)
    hits = []
    for nm, va0, vsz, rptr, rsz in SECS:
        if nm in sections:
            blob = EXE[rptr:rptr + rsz]
            i = blob.find(key)
            while i != -1:
                hits.append(BASE + va0 + i)
                i = blob.find(key, i + 1)
    return hits


def find_str(s):
    b = s.encode() + b"\0"
    i = EXE.find(b)
    while i != -1:
        va = off2va(i)
        if va and sec_of(va) in (".rdata", ".data"):
            return va
        i = EXE.find(b, i + 1)
    return None


def func_start(va, back=0x600):
    """Nearest `push ebp; mov ebp, esp` prologue at or before va, else None."""
    o = va2off(va)
    blob = EXE[o - back:o]
    idx = blob.rfind(bytes([0x55, 0x8B, 0xEC]))
    return off2va(o - back + idx) if idx != -1 else None


def dis_func(va, cap=0xA00):
    """Disassemble until a ret followed by int3 padding, or `cap` bytes."""
    out = []
    for ins in dis(va, n=cap):
        out.append(ins)
        if ins.mnemonic.startswith("ret") and read(ins.address + ins.size, 1) == b"\xcc":
            break
    return out


if __name__ == "__main__":
    print(f"DDDA.exe base 0x{BASE:X}, {len(SECS)} sections:")
    for nm, va, vsz, rptr, rsz in SECS:
        print(f"  {nm:8} VA 0x{BASE + va:08X} vsz 0x{vsz:06X}")

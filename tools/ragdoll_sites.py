"""Generate the loader's ragdoll body-count guard table from DDDA.exe (build 2364871).

    python tools/ragdoll_sites.py [--exe DDDA.exe] [--check]

Dev-only (needs capstone: pip install capstone).  Writes native/loader/ragdoll_sites.inc; with --check it only
compares a fresh table with the one on disk.  A ragdoll's bodies live in a container whose body data is at
+0x38; the packed count is [bodydata+0x68] >> 8, and the game's own accessor (0x010805D0) answers 0 while the
body data is not there.  The compiler inlined that read all over the enemy, physics and character code without
the accessor's null check, and each copy faults on a ragdoll whose bodies are not set up yet (the owner's
crashes: 0x00794942 and 0x007945B4 on 2026-09-27, 0x00794AA2 at Devil's Firegrove on 2026-10-06).

Every instruction in .text that reads [R+0x68] is followed back (linear order, straight-line code only) to the
instruction that last wrote R.  When that is `mov R, [X+0x38]` with no `test R, R` in between, and the value read
is the packed count, the read is one of:

  T  test dword [R+0x68], 0xFFFFFF00      the "any bodies?" test before a walk (7 bytes)
  C  mov R2, [R+0x68] ; shr R2, 8          the count read before a walk (6 bytes)
  M  test dword [R+0x68], M                the same test with the mask in a register (3 bytes, too short for a
                                           jump: the patch starts at the straight-line instructions before it,
                                           which the stub runs first)
  a loop's re-read                         mov R2, [R+0x68] ... shr R2, 8 ; cmp i, R2 ; jb back: the bottom of a
                                           walk whose entry is one of the above (not patched, counted)

The four walks fixes.cpp guards by hand (0x00794930, 0x007949E0 whole; the inline reads at 0x008CF2D8 and
0x00C2BAE0) are left out.  No branch in .text and no address stored anywhere in the image may land inside a
patched span past its first byte; the generator refuses otherwise.  docs/stability-membrane.md explains it.
"""
from __future__ import annotations

import argparse
import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "native" / "loader" / "ragdoll_sites.inc"

BASE = 0x400000
MASK = 0xFFFFFF00
ACCESSOR = 0x010805D0                          # the game's own count: 0 without body data
# fixes.cpp's four hand-written sites: two whole functions (each ends in `ret 4`) and two inline spans
BESPOKE_FUNCTIONS = ((0x00794930, 0x007949E0), (0x007949E0, 0x00794A90))
BESPOKE_SPANS = ((0x008CF2D8, 14), (0x00C2BAE0, 18))
WANT = {"T": 48, "C": 10, "M": 4}              # docs/stability-membrane.md quotes these counts
CRASHES = {0x007945B4: "2026-09-27, a goblin horde at Gran Soren",
           0x00794AA2: "2026-10-06 22:33, a dying goblin at Devil's Firegrove"}
CONDITIONAL = {"ja", "jae", "jb", "jbe", "je", "jne", "jg", "jge", "jl", "jle", "jo", "jno", "js", "jns", "jp",
               "jnp", "jecxz", "loop", "loope", "loopne"}


def load(exe: Path):
    data = exe.read_bytes()
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    n = struct.unpack_from("<H", data, pe + 6)[0]
    opt = struct.unpack_from("<H", data, pe + 20)[0]
    secs = [struct.unpack_from("<8sIIII", data, pe + 24 + opt + 40 * i) for i in range(n)]
    text = next(s for s in secs if s[0].rstrip(b"\0") == b".text")
    return data, secs, BASE + text[2], data[text[4]:text[4] + min(text[1], text[3])]


def scan(exe: Path):
    from capstone import CS_ARCH_X86, CS_MODE_32, Cs
    from capstone.x86 import X86_OP_IMM, X86_OP_MEM, X86_OP_REG

    data, secs, text_va, text = load(exe)
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    md.detail = True
    functions: dict[int, list] = {}

    def padded_start(va):
        j = va - text_va
        while j > 1 and not (text[j - 1] == 0xCC and text[j - 2] == 0xCC):
            j -= 1
        return text_va + j

    def covering(va):
        """(the function's instructions, the index of the one at va or None, the function's start)."""
        fs = padded_start(va)
        while fs <= va:
            if fs not in functions:
                body = []
                for ins in md.disasm(text[fs - text_va:fs - text_va + 0x8000], fs):
                    if ins.mnemonic == "int3":
                        break
                    body.append(ins)
                functions[fs] = body
            body = functions[fs]
            for k, ins in enumerate(body):
                if ins.address <= va < ins.address + ins.size:
                    return body, (k if ins.address == va else None), fs
            end = body[-1].address + body[-1].size if body else fs
            j = end - text_va
            while j < len(text) and text[j] == 0xCC:
                j += 1
            if text_va + j == fs:
                break
            fs = text_va + j
        return None, None, fs

    def written(ins):
        try:
            return {ins.reg_name(r) for r in ins.regs_access()[1]}
        except Exception:  # noqa: BLE001 -- capstone has no access list for a few instructions
            return set()

    def target(ins):
        if ins.mnemonic in CONDITIONAL or ins.mnemonic in ("jmp", "call"):
            op = ins.operands[0] if ins.operands else None
            if op is not None and op.type == X86_OP_IMM:
                return op.imm
        return None

    def bespoke(va):
        return any(a <= va < b for a, b in BESPOKE_FUNCTIONS) or any(a <= va < a + n for a, n in BESPOKE_SPANS)

    for a, b in BESPOKE_FUNCTIONS:
        if text[b - 3 - text_va:b - text_va] != b"\xC2\x04\x00":
            raise SystemExit(f"0x{a:08X}: the hand-guarded function does not end in ret 4 at 0x{b - 3:08X}")
    if text[ACCESSOR - text_va:ACCESSOR - text_va + 14] != bytes.fromhex("8b413885c074078b4068c1e808c3"):
        raise SystemExit("the game's own count accessor is not at 0x010805D0: not build 2364871")

    sites, reread, other = [], [], []
    for m in re.finditer(rb"[\x40-\x43\x45-\x7F]\x68", text):   # ModRM [base+disp8] (no SIB) and disp8 0x68
        start = text_va + m.start() - 1
        if text[m.start() - 1] not in (0x8B, 0x85, 0xF7) or bespoke(start):
            continue
        body, k, fs = covering(start)
        if k is None:
            continue
        use = body[k]
        mem = [o for o in use.operands if o.type == X86_OP_MEM]
        if not mem or mem[0].mem.disp != 0x68 or mem[0].mem.index or mem[0].size != 4:
            continue
        r = use.reg_name(mem[0].mem.base)
        # where R came from: the last write before the read, in straight-line code
        defn, checked, straight = None, False, True
        for j in range(k - 1, max(-1, k - 64), -1):
            ins = body[j]
            if ins.mnemonic in ("test", "cmp") and ins.op_str in (f"{r}, {r}", f"{r}, 0"):
                checked = True
            if target(ins) is not None or ins.mnemonic in ("ret", "jmp"):
                straight = False
            if r in written(ins) or (ins.mnemonic == "call" and r in ("eax", "ecx", "edx")):
                defn = ins
                break
        if not (defn and defn.mnemonic == "mov" and defn.op_str.endswith(" + 0x38]")) or checked:
            continue
        lo, hi = defn.address, use.address
        if any(lo < t <= hi for ins in body for t in [target(ins)] if t is not None):
            straight = False
        # what the read is
        kind, pre = None, 0
        if use.mnemonic == "test" and use.operands[1].type == X86_OP_IMM and use.operands[1].imm & 0xFFFFFFFF == MASK:
            kind = "T"
        elif use.mnemonic == "test" and use.operands[1].type == X86_OP_REG:
            mreg = use.reg_name(use.operands[1].reg)
            for j in range(k - 1, -1, -1):
                ins = body[j]
                if mreg in written(ins) or (ins.mnemonic == "call" and mreg in ("eax", "ecx", "edx")):
                    if ins.mnemonic == "mov" and ins.operands[1].type == X86_OP_IMM and \
                            ins.operands[1].imm & 0xFFFFFFFF == MASK:
                        kind = "M"
                    break
        elif use.mnemonic == "mov":
            r2 = use.reg_name(use.operands[0].reg)
            after = body[k + 1:k + 4]
            if after and after[0].mnemonic == "shr" and after[0].op_str == f"{r2}, 8":
                kind = "C"
            else:
                for j, ins in enumerate(after):
                    if ins.mnemonic == "shr" and ins.op_str == f"{r2}, 8":
                        tail = body[k + 2 + j:k + 5 + j]
                        if len(tail) >= 2 and tail[0].mnemonic == "cmp" and r2 in tail[0].op_str and \
                                target(tail[1]) is not None and target(tail[1]) < use.address:
                            reread.append(use.address)
                            kind = ""
                        break
                    if r2 in written(ins):
                        break
        if kind is None:
            continue                            # not the packed count (no shift by 8, no 0xFFFFFF00 mask)
        if kind == "":
            continue
        if not straight:
            other.append(f"0x{use.address:08X}: R from 0x{defn.address:08X} across a branch")
            continue
        span_start = use.address
        if kind == "M":                         # take the straight-line instructions before it into the span
            j = k
            while use.address + use.size - span_start < 5:
                j -= 1
                ins = body[j]
                if ins.mnemonic not in ("mov", "xor", "lea") or ins.address < defn.address:
                    raise SystemExit(f"0x{use.address:08X}: no room for a jump before the masked test")
                span_start = ins.address
            pre = use.address - span_start
        length = use.address + use.size - span_start + (3 if kind == "C" else 0)
        sites.append({"va": span_start, "kind": kind, "len": length, "pre": pre, "read": use.address,
                      "fn": fs, "def": f"{defn.mnemonic} {defn.op_str}",
                      "bytes": text[span_start - text_va:span_start - text_va + length]})

    if other:
        raise SystemExit("reads that need a look:\n  " + "\n  ".join(other))
    # nothing may land inside a patched span past its first byte: relative branches (checked by aligned
    # disassembly of the function holding them) and addresses stored anywhere in the image (jump tables)
    inner, called = {}, set()
    for s in sites:
        for a in range(s["va"] + 1, s["va"] + s["len"]):
            inner[a] = s["va"]
    for j in range(len(text) - 6):
        b = text[j]
        if b in (0xE8, 0xE9):
            tgt, n = text_va + j + 5 + struct.unpack_from("<i", text, j + 1)[0], 5
            if b == 0xE8:
                called.add(tgt)
        elif b == 0x0F and 0x80 <= text[j + 1] <= 0x8F:
            tgt, n = text_va + j + 6 + struct.unpack_from("<i", text, j + 2)[0], 6
        elif b == 0xEB or 0x70 <= b <= 0x7F or 0xE0 <= b <= 0xE3:
            tgt, n = text_va + j + 2 + struct.unpack_from("<b", text, j + 1)[0], 2
        else:
            continue
        if tgt in inner:
            body, k, _ = covering(text_va + j)
            if k is not None and target(body[k]) == tgt:
                raise SystemExit(f"0x{text_va + j:08X} branches into the patched span at 0x{inner[tgt]:08X}")
    for name, vsize, vaddr, rsize, rptr in secs:
        raw = data[rptr:rptr + min(vsize, rsize)]
        for j in range(0, len(raw) - 3):
            v = struct.unpack_from("<I", raw, j)[0]
            if v in inner:
                raise SystemExit(f"{name.rstrip(bytes(1)).decode()} +0x{j:X} holds 0x{v:08X}, inside the patched "
                                 f"span at 0x{inner[v]:08X}")
    # name each site's function by the nearest call target at or before it in the same padded block (functions
    # the compiler laid out back to back share one block)
    for s in sites:
        body, _, fs = covering(s["read"])
        starts = [fs] + [ins.address for ins in body if ins.address <= s["read"] and ins.address in called]
        s["fn"] = max(starts)
    sites.sort(key=lambda s: s["va"])
    return sites, sorted(reread)


def render(sites, reread) -> str:
    kinds = {k: sum(1 for s in sites if s["kind"] == k) for k in WANT}
    lines = [
        "// Generated by tools/ragdoll_sites.py from DDDA.exe build 2364871; do not edit.  fixes.cpp, the family",
        "// guard: every inlined read of a ragdoll's packed body count [bodydata+0x68] >> 8 through",
        "// bodydata = [holder+0x38] with no null check, outside the four sites fixes.cpp guards by hand.",
        f"//   {len(sites)} sites: {kinds['T']} T (test [R+0x68], 0xFFFFFF00), {kinds['C']} C (mov R2,[R+0x68]; shr R2,8),",
        f"//   {kinds['M']} M (test [R+0x68], M with M = 0xFFFFFF00; the patch starts `pre` bytes before the read).",
        f"//   {len(reread)} loop re-reads (the bottom of a walk whose entry is guarded) are left as they are.",
        "// {va, kind, len, pre, the game's bytes}   // the function, where R came from",
        "static const FamilySite FAMILY_SITES[] = {",
    ]
    for s in sites:
        b = ", ".join(f"0x{x:02X}" for x in s["bytes"])
        note = f"fn 0x{s['fn']:08X}, {s['def']}"
        if s["read"] in CRASHES:
            note += f"; the crash of {CRASHES[s['read']]}"
        lines.append(f"    {{0x{s['va']:08X}, '{s['kind']}', {s['len']}, {s['pre']}, {{{b}}}}},  // {note}")
    lines.append("};")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--exe")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    try:
        import capstone  # noqa: F401
    except ImportError:
        print("needs capstone: pip install capstone")
        return 2
    if args.exe:
        exe = Path(args.exe)
    else:
        sys.path.insert(0, str(ROOT / "src"))
        from riftstone.game import find_game
        exe = find_game().root / "DDDA.exe"
    sites, reread = scan(exe)
    kinds = {k: sum(1 for s in sites if s["kind"] == k) for k in WANT}
    print(f"{len(sites)} sites {kinds}, {len(reread)} loop re-reads left alone")
    if kinds != WANT:
        print(f"expected {WANT}: a different build, or the docs need the new counts")
        return 1
    for read in CRASHES:
        if not any(s["read"] == read for s in sites):
            print(f"the crash site 0x{read:08X} is not in the table")
            return 1
    text = render(sites, reread)
    if args.check:
        have = OUT.read_text(encoding="utf-8").replace("\r\n", "\n") if OUT.is_file() else ""
        if have != text:
            print(f"{OUT.relative_to(ROOT)} differs from a fresh table: run python tools/ragdoll_sites.py")
            return 1
        print("the table on disk is current")
        return 0
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Generate the collision_cap plugin's patch table from DDDA.exe (build 2364871).

    python tools/collision_cap_sites.py [--exe DDDA.exe] [--check]

Dev-only (needs capstone: pip install capstone).  Writes native/plugins/collision_cap/src/sites.inc; with
--check it only compares a fresh table with the one on disk.  docs/re-collision-cap.md explains every
entry: sObjCollision's 800 per-frame entry nodes (0x320 bytes each, from +0x40) become N in place, so every
field after the table shifts by DELTA = (N - 800) x 0x320; each allocator's bound of 800 becomes N; the
constructor's and destructor's loop counts become N - 1; the three allocations grow by DELTA; and the sweep
job's two count checks jump to the plugin, which clamps the sweep at N (the allocators count refused requests
too, so the count can pass the table: the crash this plugin is for).
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "native" / "plugins" / "collision_cap" / "src" / "sites.inc"

BASE = 0x400000
NODES = 800
NODE_SIZE = 0x320
NODES_AT = 0x40
TAIL = NODES_AT + NODES * NODE_SIZE          # 0x9C440: the first field after the table
SIZE = 0x9C550                               # sObjCollision's size (its DTI registration)
MANAGER = 0x018FA4E4                         # the sObjCollision instance
IAT_INC = 0x0139D0D0                         # the import slot of kernel32's InterlockedIncrement
GAMESYS = 0x018FA4BC                         # sGameSys is bigger (0xBE470): a function using both gets a look
CLUSTER = (0x00478600, 0x0047A000)           # sObjCollision's own methods (ctor 0x00478600 .. the job's queuer)
# Functions that reach the manager another way than its global (read by hand).
KNOWN = {0x0041DD60: "sMain's frame end: sObjCollision from sMain's table ([ebp+0x1C238]; the count reset at 0x0041DF8A)"}

COUNT_M1 = {0x0047862B: "sObjCollision ctor: the record loop's count (800 - 1)",
            0x00478A05: "~sObjCollision: the record loop's count (800 - 1)"}      # 0x31F -> N - 1
ALLOC_NOTE = {0x0041C57F: "the game's sObjCollision", 0x004785DB: "sObjCollision::DTI::newInstance",
              0x0132C466: "sObjCollision's DTI (registered size)"}               # push 0x9C550
# The sweep job (entryNodeUpdate, 0x00479C10): "cmp eax, [esi+0x34]; jge end" before the loop and
# "cmp eax, [esi+0x34]; jl loop" after it, each five bytes, each replaced by a jump to the plugin's clamp.
BLOCKS = {"sweep_first": (0x00479C24, 0x00479C29, "3b46347d7a"), "sweep_next": (0x00479C9D, 0x00479CA2, "3b46347c8e")}
# Kept on purpose: the job's own record stride and address (0x00479C30 imul, 0x00479C36 lea), every
# allocator's stride (imul eax, eax, 0x320 after its bound), and the per-frame reset of the count.
KEEP = {0x00479C30: "the job: record stride", 0x00479C36: "the job: record address",
        0x0041DF8A: "frame end: the count goes back to 0", 0x0041DF7E: "frame end: the peak"}

KINDS = ("K_TAIL", "K_TAIL_IMM", "K_BOUND", "K_COUNT_M1", "K_ALLOC")
WANT = {"K_TAIL": 169, "K_TAIL_IMM": 4, "K_BOUND": 97, "K_COUNT_M1": 2, "K_ALLOC": 3}


def load(exe: Path):
    data = exe.read_bytes()
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    n = struct.unpack_from("<H", data, pe + 6)[0]
    opt = struct.unpack_from("<H", data, pe + 20)[0]
    secs = [struct.unpack_from("<8sIIII", data, pe + 24 + opt + 40 * i) for i in range(n)]
    text = next(s for s in secs if s[0].rstrip(b"\0") == b".text")
    text_va, text_bytes = BASE + text[2], data[text[4]:text[4] + min(text[1], text[3])]

    def at(va, size):
        rva = va - BASE
        for _, vsize, vaddr, rsize, rptr in secs:
            if vaddr <= rva < vaddr + max(vsize, rsize):
                return data[rptr + rva - vaddr:rptr + rva - vaddr + size]
        raise SystemExit(f"0x{va:08X} is outside the image")
    return at, text_va, text_bytes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--exe")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    try:
        from capstone import CS_ARCH_X86, CS_MODE_32, Cs
        from capstone.x86 import X86_OP_IMM, X86_OP_MEM, X86_OP_REG, X86_REG_EAX, X86_REG_ESP
    except ImportError:
        print("needs capstone: pip install capstone")
        return 2
    if args.exe:
        exe = Path(args.exe)
    else:
        sys.path.insert(0, str(ROOT / "src"))
        from riftstone.game import find_game
        exe = find_game().root / "DDDA.exe"
    at, text_va, text = load(exe)
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    md.detail = True
    padding = bytes([0xCC, 0xCC])

    def one(va):
        return next(md.disasm(at(va, 16), va))

    def padded_start(va):
        """The start of the padding-delimited block holding va: right after a run of two or more int3."""
        j = va - text_va
        while j > 1 and not (text[j - 1] == 0xCC and text[j - 2] == 0xCC):
            j -= 1
        return text_va + j

    functions = {}                               # function start -> its instructions (linear disassembly)

    def covering(va):
        """The instruction that holds byte va and the start of its function.

        Linear disassembly from the padded block's start; a run of int3 (the compiler pads with one or more,
        so two in a row are not the only function boundary) ends a function and the next byte starts one.
        """
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
            for ins in body:
                if ins.address <= va < ins.address + ins.size:
                    return ins, fs
            end = body[-1].address + body[-1].size if body else fs
            j = end - text_va
            while j < len(text) and text[j] == 0xCC:
                j += 1
            if text_va + j == fs:
                break
            fs = text_va + j
        return None, fs

    def body_of(fs):
        body = functions[fs]
        return text[fs - text_va:body[-1].address + body[-1].size - text_va] if body else b""

    def takes_manager(fs):
        """The function holds the manager's global, is one of sObjCollision's own methods, or is known."""
        return struct.pack("<I", MANAGER) in body_of(fs) or CLUSTER[0] <= fs < CLUSTER[1] or fs in KNOWN

    sites = []                                   # (va, len, bytes, field, size, kind, note)
    both = set()

    def add(ins, field, size, kind, note):
        assert ins.size <= 10, hex(ins.address)
        sites.append((ins.address, ins.size, bytes(ins.bytes), field, size, kind, note))

    # 1. Every field after the table: a displacement or an immediate in [TAIL, SIZE).  The two high bytes of
    #    such a dword are 09 00 and the third C4 or C5: find those, then the instruction around each.
    seen = set()
    for needle in (bytes([0xC4, 0x09, 0x00]), bytes([0xC5, 0x09, 0x00])):
        i = 0
        while (j := text.find(needle, i)) >= 0:
            i = j + 1
            if j < 1:
                continue
            value = struct.unpack_from("<I", text, j - 1)[0]
            if not TAIL <= value < SIZE:
                continue
            va = text_va + j - 1
            ins, fs = covering(va)
            if ins is None or va in seen:
                continue
            mem = [op for op in ins.operands if op.type == X86_OP_MEM and (op.mem.disp & 0xFFFFFFFF) == value]
            imm = [op for op in ins.operands if op.type == X86_OP_IMM and op.imm == value]
            if mem and ins.disp_size == 4 and ins.address + ins.disp_offset == va:
                assert mem[0].mem.base != X86_REG_ESP, f"stack displacement at 0x{va:08X}"
                assert takes_manager(fs), f"0x{va:08X}: its function 0x{fs:08X} does not take sObjCollision"
                if struct.pack("<I", GAMESYS) in body_of(fs):
                    both.add(fs)
                seen.add(va)
                add(ins, ins.disp_offset, 4, "K_TAIL", f"sObjCollision+0x{value:X} (function 0x{fs:08X})")
            elif imm and ins.imm_size == 4 and ins.address + ins.imm_offset == va:
                assert ins.mnemonic == "add", f"0x{va:08X}: {ins.mnemonic} with a tail field as an immediate"
                assert takes_manager(fs), f"0x{va:08X}: its function 0x{fs:08X} does not take sObjCollision"
                seen.add(va)
                add(ins, ins.imm_offset, 4, "K_TAIL_IMM", f"the address of sObjCollision+0x{value:X} (function 0x{fs:08X})")
            # else: the dword is inside another operand or a longer immediate: not a field of the tail
    # 2. Every allocator's bound: InterlockedIncrement(&count) then "cmp eax, 800".  The count's address is
    #    formed as "+0x34" (add eax, 0x34 / lea reg, [reg+0x34]) and pushed, the call goes through the import
    #    slot (or a register the function loaded from it), and the record's address follows as
    #    "imul reg, eax, 0x320" either straight after or at the jl's target.  The other "cmp eax, 0x320" are
    #    compares of the stage number (800 = the Everfall's entrance, stage 800): no call before them.
    bounds, others = [], []
    cmp_eax_800 = bytes([0x3D, 0x20, 0x03, 0x00, 0x00])
    i = 0
    while (j := text.find(cmp_eax_800, i)) >= 0:
        i = j + 1
        va = text_va + j
        ins, fs = covering(va)
        if ins is None or ins.address != va:
            assert ins is not None, f"0x{va:08X}: cmp eax, 800 outside the read code"
            continue                             # the bytes inside another instruction
        body = functions[fs]
        k = next(n for n, x in enumerate(body) if x.address == va)
        before = body[max(0, k - 6):k]
        calls = [x for x in body[max(0, k - 3):k] if x.mnemonic == "call"]
        if not calls:
            others.append(va)
            continue
        call = calls[-1]
        if call.operands[0].type == X86_OP_MEM:
            incremented = call.operands[0].mem.disp == IAT_INC
        else:
            reg = call.operands[0].reg
            incremented = any(x.mnemonic == "mov" and x.operands[0].type == X86_OP_REG and x.operands[0].reg == reg and
                              x.operands[1].type == X86_OP_MEM and x.operands[1].mem.disp == IAT_INC for x in body[:k])
        def counted_in(part):
            return any((x.mnemonic == "add" and x.operands[0].reg == X86_REG_EAX and x.operands[1].type == X86_OP_IMM and
                        x.operands[1].imm == 0x34) or
                       (x.mnemonic == "lea" and x.operands[1].type == X86_OP_MEM and x.operands[1].mem.disp == 0x34)
                       for x in part)
        # The count's address is formed right before the call, or (one site, 0x00BD8DA2) in a block that jumps
        # to the call: then anywhere in the function.
        counted = counted_in(before) or counted_in(body[:k])
        pushed = any(x.mnemonic == "push" for x in before) or any(x.mnemonic == "push" for x in body[:k])
        if not incremented:
            others.append(va)                    # a stage compare after some other call
            continue
        assert counted and pushed, f"0x{va:08X}: the count's increment without its address"
        branch = body[k + 1]
        assert branch.mnemonic in ("jl", "jge"), f"0x{va:08X}: {branch.mnemonic} after the bound"
        start = k + 2 if branch.mnemonic == "jge" else next(n for n, x in enumerate(body) if x.address == branch.operands[0].imm)
        stride = any(x.mnemonic == "imul" and x.operands[-1].type == X86_OP_IMM and x.operands[-1].imm == NODE_SIZE
                     for x in body[start:start + 12])
        assert stride, f"0x{va:08X}: no record stride after the bound"
        bounds.append(va)
        add(ins, ins.imm_offset, 4, "K_BOUND", f"getEntryNode: 800 -> N (function 0x{fs:08X})")
    assert all(abs(a - b) > 0x10 for a in bounds for b in others), "a stage compare next to an allocator"
    # 3. The two loop counts, the three allocations, the two sweep checks.
    for va, note in COUNT_M1.items():
        ins = one(va)
        imms = [op.imm for op in ins.operands if op.type == X86_OP_IMM]
        assert imms == [NODES - 1], (hex(va), imms)
        add(ins, ins.imm_offset, 4, "K_COUNT_M1", note)
    allocs = []
    push_size = bytes([0x68]) + struct.pack("<I", SIZE)
    i = 0
    while (j := text.find(push_size, i)) >= 0:
        i = j + 1
        ins, _ = covering(text_va + j)
        if ins is not None and ins.address == text_va + j:
            allocs.append(ins.address)
            add(ins, ins.imm_offset, 4, "K_ALLOC", ALLOC_NOTE[ins.address])
    assert sorted(allocs) == sorted(ALLOC_NOTE), [hex(a) for a in allocs]
    blocks = {}
    for name, (lo, hi, want) in BLOCKS.items():
        code = at(lo, hi - lo)
        assert code.hex() == want, name
        blocks[name] = (lo, hi, code)
    for va in KEEP:
        one(va)                                  # still decodes: the list names real instructions

    sites.sort()
    assert len({s[0] for s in sites}) == len(sites), "a site is listed twice"
    for fs in sorted(both):
        print(f"note: function 0x{fs:08X} holds both sObjCollision's and sGameSys's globals (its tail sites were read by hand)")
    counts = {k: sum(1 for s in sites if s[5] == k) for k in KINDS}
    assert counts == WANT, counts
    lines = ["// Generated by tools/collision_cap_sites.py from DDDA.exe build 2364871 -- do not edit.",
             f"// {len(sites)} sites: " + ", ".join(f"{k[2:].lower()} {counts[k]}" for k in KINDS) +
             f"; {len(blocks)} blocks.",
             f"constexpr uint32_t NODES_OLD = {NODES}, NODE_SIZE = 0x{NODE_SIZE:X}, NODES_AT = 0x{NODES_AT:X}, "
             f"TAIL_OLD = 0x{TAIL:X}, MANAGER_SIZE = 0x{SIZE:X};",
             f"constexpr uintptr_t MANAGER = 0x{MANAGER:08X}, SWEEP_LOOP = 0x00479C30, SWEEP_BODY = 0x00479C29, "
             "SWEEP_DONE = 0x00479CA2, SWEEP_END = 0x00479CA3;",
             "const Site SITES[] = {"]
    for va, n, code, field, size, kind, note in sites:
        b = ", ".join(f"0x{x:02X}" for x in code)
        lines.append(f"    {{0x{va:08X}, {n}, {field}, {size}, {kind}, {{{b}}}}},  // {note}")
    lines.append("};")
    for name, (lo, hi, code) in blocks.items():
        lines.append(f"// {name}: 0x{lo:08X}..0x{hi:08X}")
        lines.append(f"const uint8_t BLOCK_{name.upper()}[{hi - lo}] = {{" + ", ".join(f"0x{x:02X}" for x in code) + "};")
    lines.append("const Block BLOCKS[] = {")
    for name, (lo, hi, code) in blocks.items():
        lines.append(f"    {{\"{name}\", 0x{lo:08X}, 0x{hi:08X}, BLOCK_{name.upper()}}},")
    lines.append("};")
    text_out = "\n".join(lines) + "\n"
    if args.check:
        same = OUT.is_file() and OUT.read_text(encoding="utf-8") == text_out
        print("sites.inc is current" if same else "sites.inc differs from a fresh table")
        return 0 if same else 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text_out, encoding="utf-8", newline="\n")
    print(f"wrote {OUT} ({len(sites)} sites, {len(blocks)} blocks)")
    for k in KINDS:
        print(f"  {k}: {counts[k]}")
    print(f"  stage-800 compares left alone: {len(others)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

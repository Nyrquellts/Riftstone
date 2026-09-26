"""Generate the enemy_cap plugin's patch table from DDDA.exe (build 2364871).

    python tools/enemy_cap_sites.py [--exe DDDA.exe] [--check]

Dev-only (needs capstone: pip install capstone).  Writes native/plugins/enemy_cap/src/sites.inc; with
--check it only compares a fresh table with the one on disk.  docs/re-enemy-cap.md explains every
entry: the enemy slot array (sSetManager+0x844, 10 x 0x20) moves to the tail of the enlarged manager
(+0x1B950), so every instruction that addresses a slot shifts by DELTA; loop counts become N; the
four unrolled runs are replaced by calls; the manager's size grows by N x 0x20.
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "native" / "plugins" / "enemy_cap" / "src" / "sites.inc"

BASE = 0x400000
OLD, END, NEW = 0x844, 0x984, 0x1B950          # slot array [OLD, END); the manager's size, the new home
DELTA = NEW - OLD
SIZE = 0x1B950

# Functions whose every slot displacement (disp in [OLD, END)) moves.
MOVE_ALL = {
    0x004A58C0: "sSetManager::changeUnitNumEnemy (stage 230)",
    0x004A6490: "sSetManager::killEnemyFieldPrio",
    0x004A6570: "sSetManager::addEnemyPriority",
    0x004A6830: "sSetManager::registerEmData",
    0x004A9120: "sSetManager unit scans",
    0x0075F3E0: "cLayoutSetCharaBase slot helper",
    0x0075FC60: "cLayoutSetCharaBase::updateUnitPtrArray",
    0x007601D0: "cLayoutSetEnemy slot helper",
    0x00760450: "cLayoutSetEnemy::move/finish",
    0x00761060: "cLayoutSetEnemy::syncArcLoadSetUnit",
}
# Single slot references in functions that also address the object and NPC slot arrays.
MOVE_ONE = {
    0x004A0735: "sSetManager::save (records)",
    0x004A077E: "sSetManager::save (stage-100 scan)",
    0x004A0D53: "sSetManager::load (records)",
    0x004A426C: "sSetManager::move (per-slot update)",
    0x004A44E9: "sSetManager::move (number priority)",
    0x004A45DE: "sSetManager::move (reservation scan)",
}
NEG = {0x004A5953: "changeUnitNumEnemy", 0x004A595E: "changeUnitNumEnemy", 0x004A5969: "changeUnitNumEnemy",
       0x004A6AE9: "registerEmData"}                                     # mov reg, -(slot field)
COUNT = {0x004A0788: "save (stage-100 scan)", 0x004A4272: "move (per-slot update)",
         0x004A44EF: "move (number priority)", 0x004A45E4: "move (reservation scan)",
         0x004A6499: "killEnemyFieldPrio", 0x004A658D: "addEnemyPriority", 0x004A9166: "unit scan",
         0x004A91B1: "unit scan"}                                         # 10 -> N
COUNT_M1 = {0x004A0554: "~sSetManager (slot destructor loop)"}           # 9 -> N - 1
END_PTR = {0x004A054E: "~sSetManager (slot destructor loop)"}            # lea [this + 0x984] -> new end
USABLE = {0x004A235C: "createSet: mUnitNumEnemy = 10"}                    # -> N
ALLOC = {0x0041C4C2: "the game's sSetManager", 0x0049F86B: "sSetManager::DTI::newInstance",
         0x0132D026: "sSetManager's DTI (registered size)"}               # push 0x1B950
BLOCKS = {"construct": (0x0049F9DD, 0x0049FBBD), "clear": (0x004A54AA, 0x004A555E),
          "reset": (0x004A571F, 0x004A57D3), "final": (0x004A629A, 0x004A634E)}
# Kept on purpose (listed so the proof can name them): the save file holds 10 enemy records; the stage-230
# routine's own 10-slot logic; the distance-priority weight; the object- and NPC-slot references.
KEEP = {0x004A073C: "save records: 10 (the save file's size)", 0x004A0D5A: "load records: 10",
        0x0049FE10: "constructor: usable count 10 and the priority weight (createSet sets N per stage)",
        0x004A056C: "~sSetManager: end of the object slots", 0x004A5988: "stage 230: its own 10 slots",
        0x004A5A4C: "stage 230: its own 10 slots", 0x004A6079: "stage 230: its local copy"}

KINDS = ("K_MOVE", "K_NEG", "K_COUNT", "K_COUNT_M1", "K_END", "K_USABLE", "K_ALLOC")


def load(exe: Path):
    data = exe.read_bytes()
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    n = struct.unpack_from("<H", data, pe + 6)[0]
    opt = struct.unpack_from("<H", data, pe + 20)[0]
    secs = [struct.unpack_from("<8sIIII", data, pe + 24 + opt + 40 * i) for i in range(n)]

    def at(va, size):
        rva = va - BASE
        for _, vsize, vaddr, rsize, rptr in secs:
            if vaddr <= rva < vaddr + max(vsize, rsize):
                return data[rptr + rva - vaddr:rptr + rva - vaddr + size]
        raise SystemExit(f"0x{va:08X} is outside the image")
    return at


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--exe")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    try:
        from capstone import CS_ARCH_X86, CS_MODE_32, Cs
        from capstone.x86 import X86_OP_IMM, X86_OP_MEM, X86_REG_ESP
    except ImportError:
        print("needs capstone: pip install capstone")
        return 2
    if args.exe:
        exe = Path(args.exe)
    else:
        sys.path.insert(0, str(ROOT / "src"))
        from riftstone.game import find_game
        exe = find_game().root / "DDDA.exe"
    at = load(exe)
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    md.detail = True

    def one(va):
        return next(md.disasm(at(va, 16), va))

    def func(va):
        out = []
        for ins in md.disasm(at(va, 0x6000), va):
            if ins.mnemonic == "int3":
                break
            out.append(ins)
        return out

    sites = []                                   # (va, len, bytes, field, size, kind, note)

    def add(ins, field, size, kind, note):
        assert ins.size <= 10, hex(ins.address)
        sites.append((ins.address, ins.size, bytes(ins.bytes), field, size, kind, note))

    def slot_disp(ins):
        for op in ins.operands:
            if op.type == X86_OP_MEM and OLD <= (op.mem.disp & 0xFFFFFFFF) < END:
                assert op.mem.base != X86_REG_ESP, f"stack displacement at {ins.address:08X}"
                return True
        return False

    for fn, note in MOVE_ALL.items():
        for ins in func(fn):
            if slot_disp(ins):
                assert ins.disp_size == 4, hex(ins.address)
                add(ins, ins.disp_offset, 4, "K_MOVE", note)
    for va, note in MOVE_ONE.items():
        ins = one(va)
        assert slot_disp(ins) and ins.disp_size == 4, hex(va)
        add(ins, ins.disp_offset, 4, "K_MOVE", note)
    for va, note in NEG.items():
        ins = one(va)
        v = ins.operands[1].imm & 0xFFFFFFFF
        assert OLD - 0x40 <= (-v & 0xFFFFFFFF) < END, hex(va)
        add(ins, ins.imm_offset, 4, "K_NEG", note)
    for table, kind, want in ((COUNT, "K_COUNT", 10), (COUNT_M1, "K_COUNT_M1", 9)):
        for va, note in table.items():
            ins = one(va)
            imms = [op.imm for op in ins.operands if op.type == X86_OP_IMM]
            assert imms == [want], (hex(va), imms)
            add(ins, ins.imm_offset, ins.imm_size, kind, note)
    for va, note in END_PTR.items():
        ins = one(va)
        assert ins.mnemonic == "lea" and ins.operands[1].mem.disp == END, hex(va)
        add(ins, ins.disp_offset, 4, "K_END", note)
    for va, note in USABLE.items():
        ins = one(va)
        assert ins.bytes.hex() == "c786d0b801000a000000", hex(va)
        add(ins, ins.imm_offset, 4, "K_USABLE", note)
    for va, note in ALLOC.items():
        ins = one(va)
        assert ins.mnemonic == "push" and ins.operands[0].imm == SIZE, hex(va)
        add(ins, ins.imm_offset, 4, "K_ALLOC", note)
    blocks = {}
    for name, (lo, hi) in BLOCKS.items():
        code = at(lo, hi - lo)
        ins = list(md.disasm(code, lo))
        assert sum(i.size for i in ins) == hi - lo and all(i.mnemonic == "mov" for i in ins), name
        blocks[name] = (lo, hi, code, len(ins))
    for va in KEEP:
        one(va)                                  # still decodes: the list names real instructions

    sites.sort()
    assert len({s[0] for s in sites}) == len(sites), "a site is listed twice"
    lines = ["// Generated by tools/enemy_cap_sites.py from DDDA.exe build 2364871 -- do not edit.",
             f"// {len(sites)} sites: " + ", ".join(f"{k[2:].lower()} {sum(1 for s in sites if s[5] == k)}"
                                                    for k in KINDS) + f"; {len(blocks)} blocks.",
             f"constexpr uint32_t SLOTS_OLD = 0x{OLD:X}, SLOTS_END = 0x{END:X}, SLOTS_NEW = 0x{NEW:X}, "
             f"DELTA = 0x{DELTA:X}, MANAGER_SIZE = 0x{SIZE:X};",
             "const Site SITES[] = {"]
    for va, n, code, field, size, kind, note in sites:
        b = ", ".join(f"0x{x:02X}" for x in code)
        lines.append(f"    {{0x{va:08X}, {n}, {field}, {size}, {kind}, {{{b}}}}},  // {note}")
    lines.append("};")
    for name, (lo, hi, code, count) in blocks.items():
        lines.append(f"// {name}: {count} stores, 0x{lo:08X}..0x{hi:08X}")
        lines.append(f"const uint8_t BLOCK_{name.upper()}[{hi - lo}] = {{")
        for i in range(0, len(code), 16):
            lines.append("    " + ", ".join(f"0x{x:02X}" for x in code[i:i + 16]) + ",")
        lines.append("};")
    lines.append("const Block BLOCKS[] = {")
    for name, (lo, hi, code, count) in blocks.items():
        lines.append(f"    {{\"{name}\", 0x{lo:08X}, 0x{hi:08X}, BLOCK_{name.upper()}}},")
    lines.append("};")
    text = "\n".join(lines) + "\n"
    if args.check:
        same = OUT.is_file() and OUT.read_text(encoding="utf-8") == text
        print("sites.inc is current" if same else "sites.inc differs from a fresh table")
        return 0 if same else 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {OUT} ({len(sites)} sites, {len(blocks)} blocks)")
    for k in KINDS:
        print(f"  {k}: {sum(1 for s in sites if s[5] == k)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Generate the portcrystals plugin's patch table from DDDA.exe (build 2364871).

    python tools/portcrystal_sites.py [--exe DDDA.exe] [--check]

Dev-only (needs capstone: pip install capstone).  Writes native/plugins/portcrystals/src/sites.inc; with --check it
only compares a fresh table with the one on disk.  docs/re-portcrystals.md explains every entry: sGameSys's
Portcrystal list (areas +0xBE378, 10 x 4 bytes; positions +0xBE3A0, 10 x 16) moves to the tail of the enlarged object
(areas +0xBE470, positions +0xBE4F0, room for 32 each), so every instruction that addresses it shifts; the limit
compares become N; the unrolled count, clear and constructor runs and the map's icon run become calls; the map's
icon block (uGUIMap +0x3AC, 10 pointers) moves to the map object's tail (+0x960); three hooks keep the slots past
ten in a sidecar file when the game saves and bring them back when it loads.
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "native" / "plugins" / "portcrystals" / "src" / "sites.inc"

BASE = 0x400000
MAX_SLOTS = 32
AREAS_OLD, POS_OLD, LIST_END = 0xBE378, 0xBE3A0, 0xBE440          # sGameSys's list, and the field after it
GS_SIZE = 0xBE470                                                  # sGameSys's size; the new home is its tail
AREAS_NEW, POS_NEW = GS_SIZE, GS_SIZE + 4 * MAX_SLOTS              # 0xBE470, 0xBE4F0
GS_NEW_SIZE = POS_NEW + 16 * MAX_SLOTS                             # 0xBE6F0
IDX_OLD, IDX_NEW = POS_OLD // 16, POS_NEW // 16                    # (i + 0xBE3A) << 4 -> (i + 0xBE4F) << 4
MAP_SIZE, ICONS_OLD = 0x960, 0x3AC                                 # uGUIMap, its icon block (10 pointers)
ICONS_NEW = MAP_SIZE
MAP_NEW_SIZE = MAP_SIZE + 4 * MAX_SLOTS                            # 0x9E0

AREA = {0x00493D9F: "save copy (game -> save): area[i]",
        0x00494BCA: "load copy (save -> game): area[i]",
        0x00494E1A: "second load copy: area[i]",
        0x004FBD9B: "Ferrystone jump: the destination crystal's stage",
        0x0068FF22: "destination list: entry -> slot (a placed slot)",
        0x0068FFA3: "destination position: overworld?",
        0x00690000: "destination position: the slot's stage",
        0x00B12C9F: "placing: the first area (the free-slot search)",
        0x00B12CCE: "placing: claim the slot",
        0x00B130BF: "picking up: free the slot"}
POS = {0x00501A5A: "stage load: slot 0's x", 0x00501A62: "stage load: slot 0's y",
       0x00501A6A: "stage load: slot 0's z"}
AREA_IMM = {0x00503029: "stage load: the area walk's first offset"}
POS_IMM = {0x00503024: "stage load: the position walk's first offset"}
INDEX = {0x0044D10E: "position of slot i (get)", 0x0044D149: "position of slot i (set)",
         0x00493DCA: "save copy: position[i]", 0x00494BD7: "load copy: position[i]",
         0x00494E27: "second load copy: position[i]", 0x0068FFAD: "destination position: position[i]",
         0x00B130CE: "picking up: position[i]"}
COUNT = {0x0044D0D0: "position of slot i (get): i < 10", 0x0044D140: "position of slot i (set): i < 10",
         0x004FBD79: "Ferrystone jump: slot < 10", 0x0068FF1A: "destination list: slot < 10",
         0x0068FF37: "destination list: the slot walk", 0x0068FF9C: "destination position: slot < 10",
         0x0068FFF2: "destination position: slot < 10 (stage)", 0x00B12CAF: "placing: the free-slot walk",
         0x00B12CBF: "placing: a slot was found", 0x00B130B2: "picking up: slot < 10",
         0x00503031: "stage load: slot < 10 (area)", 0x0050305C: "stage load: slot < 10 (position)",
         0x005031B8: "stage load: the slot walk", 0x0066CDB7: "item menu: every slot placed",
         0x00B12B3F: "placing: every slot placed (before the action)"}
COUNT32 = {0x00B12CB4: "placing: no free slot (index = the slot count)"}
GS_ALLOC = {0x0041C04E: "the game's sGameSys", 0x00437B1B: "sGameSys::DTI::newInstance",
            0x0132BA76: "sGameSys's DTI (registered size)"}
MAP_ALLOC = {0x0067FC8B: "uGUIMap::DTI::newInstance", 0x0067FCBB: "uGUIMap::DTI::newInstance (the other)",
             0x0133AFE6: "uGUIMap's DTI (registered size)"}
ICONS = {0x0068FB2F: "uGUIMap: making the crystal icons", 0x0068FDB9: "uGUIMap: placing the crystal icons"}
# Replaced by a jump to the plugin: (first byte, the instruction after the run, what the run is)
BLOCKS = {"construct": (0x0043847F, 0x004384CF, "sGameSys's constructor: each position's w = 0 (10 stores)"),
          "clear": (0x0043CA89, 0x0043CB6D, "the clear: slots 0-5's stores (slots 6-9 follow an unrelated load and "
                                            "stay, writing the old list)"),
          "count": (0x0044D170, 0x0044D1E1, "the count of placed crystals, unrolled for ten (the whole function)"),
          "map": (0x00680085, 0x006800C1, "uGUIMap's constructor: the ten icon pointers = edx")}
# Hooks: a jump to the plugin, which then runs the instruction(s) it replaced and comes back.
HOOKS = {"save": (0x00493D7D, "8D9FF4C10000", "save copy's start: lea ebx, [edi + 0xC1F4] (edi the save data, esi "
                                                "sGameSys)"),
         "load": (0x00494BAB, "0F57C033C9", "load copy's start: xorps xmm0, xmm0; xor ecx, ecx (esi the save data, "
                                              "edi sGameSys)"),
         "load2": (0x00494DFD, "0F57C033C9", "second load copy's start: xorps; xor ecx (ebx the save data, esi "
                                               "sGameSys)"),
         "placename": (0x004541B0, "81EC40010000", "the place name of a position (ecx; a map_placelist message number "
                                                   "in eax): sub esp, 0x140")}
# Checked, not patched: the place-name function answers -1 (no name) while its manager's place data (+0x2B3C) is
# not loaded; a named crystal follows the same test, read from this instruction.
GUARD = (0x004545B7, "83BA3C2B000000", "the place-name function's own test: cmp dword ptr [edx + 0x2B3C], 0")
# Kept on purpose (listed so the proof can name them): the save keeps ten; three stage fallbacks are a stage
# number, not a slot count.
KEEP = {0x00493D90: "save copy: i < 10 (the save's ten)", 0x00493DA8: "save copy: i < 10",
        0x00493DFC: "save copy: its ten", 0x00494BC0: "load copy: i < 10", 0x00494BFA: "load copy: its ten",
        0x00494E10: "second load copy: i < 10", 0x00494E4A: "second load copy: its ten",
        0x004FBD7E: "Ferrystone jump: stage 10 when the slot is out of range",
        0x0068FFF6: "destination position: stage 10 when out of range",
        0x00503038: "stage load: stage 10 when out of range"}

KINDS = ("K_AREA", "K_POS", "K_AREA_IMM", "K_POS_IMM", "K_INDEX", "K_COUNT", "K_COUNT32", "K_GS_ALLOC",
         "K_MAP_ALLOC", "K_ICONS")


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
        from capstone.x86 import X86_OP_IMM, X86_OP_MEM
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

    sites = []                                   # (va, len, bytes, field, size, kind, note)

    def add(ins, field, size, kind, note):
        assert ins.size <= 10, hex(ins.address)
        sites.append((ins.address, ins.size, bytes(ins.bytes), field, size, kind, note))

    def disp_site(table, lo, hi, kind):
        for va, note in table.items():
            ins = one(va)
            mems = [op for op in ins.operands if op.type == X86_OP_MEM]
            assert mems and lo <= (mems[0].mem.disp & 0xFFFFFFFF) < hi and ins.disp_size == 4, (hex(va), kind)
            add(ins, ins.disp_offset, 4, kind, note)

    def imm_site(table, want, kind, size=None):
        for va, note in table.items():
            ins = one(va)
            imms = [op.imm & 0xFFFFFFFF for op in ins.operands if op.type == X86_OP_IMM]
            if imms == [want] and (size is None or ins.imm_size == size):
                add(ins, ins.imm_offset, ins.imm_size, kind, note)
                continue
            # lea eax, [ecx + 0xBE3A]: the index base as a displacement
            mems = [op for op in ins.operands if op.type == X86_OP_MEM]
            assert kind == "K_INDEX" and mems and (mems[0].mem.disp & 0xFFFFFFFF) == want and ins.disp_size == 4, \
                (hex(va), imms, kind)
            add(ins, ins.disp_offset, 4, kind, note)

    disp_site(AREA, AREAS_OLD, POS_OLD, "K_AREA")
    disp_site(POS, POS_OLD, LIST_END, "K_POS")
    imm_site(AREA_IMM, AREAS_OLD, "K_AREA_IMM", 4)
    imm_site(POS_IMM, POS_OLD, "K_POS_IMM", 4)
    imm_site(INDEX, IDX_OLD, "K_INDEX")
    imm_site(COUNT, 10, "K_COUNT", 1)
    imm_site(COUNT32, 10, "K_COUNT32", 4)
    imm_site(GS_ALLOC, GS_SIZE, "K_GS_ALLOC", 4)
    imm_site(MAP_ALLOC, MAP_SIZE, "K_MAP_ALLOC", 4)
    disp_site(ICONS, ICONS_OLD, ICONS_OLD + 1, "K_ICONS")

    blocks = {}
    for name, (lo, hi, note) in BLOCKS.items():
        code = at(lo, hi - lo)
        ins = list(md.disasm(code, lo))
        assert sum(i.size for i in ins) == hi - lo, name
        if name == "count":
            assert ins[-1].mnemonic == "ret" and all(i.mnemonic in ("xor", "cmp", "je", "mov", "inc", "ret") for i in ins), name
        else:
            assert all(i.mnemonic in ("mov", "movss") for i in ins), name
        blocks[name] = (lo, hi, code, len(ins), note)
    hooks = {}
    for name, (va, hexcode, note) in HOOKS.items():
        code = at(va, len(hexcode) // 2)
        assert code.hex().upper() == hexcode, (name, code.hex())
        hooks[name] = (va, code, note)
    guard = at(GUARD[0], len(GUARD[1]) // 2)
    assert guard.hex().upper() == GUARD[1], ("guard", guard.hex())
    for va in KEEP:
        ins = one(va)
        assert 10 in [op.imm for op in ins.operands if op.type == X86_OP_IMM], hex(va)

    sites.sort()
    assert len({s[0] for s in sites}) == len(sites), "a site is listed twice"
    for s in sites:                               # no site overlaps a replaced run or a hook
        for lo, hi, *_ in blocks.values():
            assert not (lo <= s[0] < hi), hex(s[0])
        for va, code, _ in hooks.values():
            assert not (va <= s[0] < va + len(code)), hex(s[0])
    lines = ["// Generated by tools/portcrystal_sites.py from DDDA.exe build 2364871 -- do not edit.",
             f"// {len(sites)} sites: " + ", ".join(f"{k[2:].lower()} {sum(1 for s in sites if s[5] == k)}" for k in KINDS)
             + f"; {len(blocks)} replaced runs; {len(hooks)} hooks.",
             f"constexpr uint32_t AREAS_OLD = 0x{AREAS_OLD:X}, POS_OLD = 0x{POS_OLD:X}, LIST_END = 0x{LIST_END:X};",
             f"constexpr uint32_t GS_SIZE = 0x{GS_SIZE:X}, AREAS_NEW = 0x{AREAS_NEW:X}, POS_NEW = 0x{POS_NEW:X}, "
             f"GS_NEW_SIZE = 0x{GS_NEW_SIZE:X};",
             f"constexpr uint32_t IDX_OLD = 0x{IDX_OLD:X}, IDX_NEW = 0x{IDX_NEW:X};",
             f"constexpr uint32_t MAP_SIZE = 0x{MAP_SIZE:X}, ICONS_OLD = 0x{ICONS_OLD:X}, ICONS_NEW = 0x{ICONS_NEW:X}, "
             f"MAP_NEW_SIZE = 0x{MAP_NEW_SIZE:X};",
             f"constexpr int MAX_SLOTS = {MAX_SLOTS};",
             "const Site SITES[] = {"]
    for va, n, code, field, size, kind, note in sites:
        b = ", ".join(f"0x{x:02X}" for x in code)
        lines.append(f"    {{0x{va:08X}, {n}, {field}, {size}, {kind}, {{{b}}}}},  // {note}")
    lines.append("};")
    for name, (lo, hi, code, count, note) in blocks.items():
        lines.append(f"// {name}: {note}; {count} instructions, 0x{lo:08X}..0x{hi:08X}")
        lines.append(f"const uint8_t BLOCK_{name.upper()}[{hi - lo}] = {{")
        for i in range(0, len(code), 16):
            lines.append("    " + ", ".join(f"0x{x:02X}" for x in code[i:i + 16]) + ",")
        lines.append("};")
    lines.append("const Block BLOCKS[] = {")
    for name, (lo, hi, code, count, note) in blocks.items():
        lines.append(f"    {{\"{name}\", 0x{lo:08X}, 0x{hi:08X}, BLOCK_{name.upper()}}},")
    lines.append("};")
    lines.append("const Hook HOOKS[] = {")
    for name, (va, code, note) in hooks.items():
        b = ", ".join(f"0x{x:02X}" for x in code)
        lines.append(f"    {{\"{name}\", 0x{va:08X}, {len(code)}, {{{b}}}}},  // {note}")
    lines.append("};")
    b = ", ".join(f"0x{x:02X}" for x in guard)
    lines.append(f"// {GUARD[2]} (checked, not patched)")
    lines.append(f"constexpr uint32_t PLACE_READY = 0x{struct.unpack_from('<I', guard, 2)[0]:X};")
    lines.append(f"const Hook GUARD = {{\"placename's guard\", 0x{GUARD[0]:08X}, {len(guard)}, {{{b}}}}};")
    text = "\n".join(lines) + "\n"
    if args.check:
        same = OUT.is_file() and OUT.read_text(encoding="utf-8") == text
        print("sites.inc is current" if same else "sites.inc differs from a fresh table")
        return 0 if same else 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {OUT} ({len(sites)} sites, {len(blocks)} runs, {len(hooks)} hooks)")
    for k in KINDS:
        print(f"  {k}: {sum(1 for s in sites if s[5] == k)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

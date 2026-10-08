"""Which memory pool each engine class of DDDA.exe (build 2364871) is allocated from, read from the exe.

    python tools/pool_census.py [--exe DDDA.exe] [--classes REGEX]

Dev-only (needs capstone: pip install capstone).  docs/re-memory-pools.md quotes what it prints.

Every MtObject is allocated from the allocator its MtDTI names: bits 23..28 of DTI+0x18 index a table of 64
allocators at 0x01876628 (getAllocator 0x00CF5E20).  Three things decide the index, and this tool reads each the
way the game runs it:

  the pools    WinMain's 0x00740590 builds eight MtScalableAllocator pools (operator new 0x00D0F2B0, ctor
               0x0041B050, init = vtable +0x30 with six pushes: name, type, size, ...) and stores each in its
               slot; its tail copies slots into others (aliases).  Read here instruction by instruction: pushes,
               the init call, `mov [slot], reg` and `mov reg, [slot]`.  A slot never written keeps the static
               MtDefaultAllocator the CRT initialiser (0x0137E090) put in all 64.
  the map      0x00740860: `push index; push name; call 0x00CF5D60` adds a class name and its slot to a list,
               254 times, and `call 0x00CF6220` (0x007423E5) applies it.
  the walk     0x00CF6220 gives the root (the DTI at 0x01876CB4) its name's slot, then 0x00CF60B0 visits each
               class: its own name in the map (the first entry with that name), else each parent's name below
               the root, else its name with the last `::` scope stripped (0x00CF5EA0), looked up as a name and
               through the DTI of that name (0x00CF5870) and its parents, until a scope is left.  A match sets the
               class's index and copies it to every class below (0x00CF5750) before those are visited; a class
               with no match keeps what it had (its DTI constructor's push, or a parent's).

The classes are the DTI constructions in .text (`mov ecx, DTI; call 0x00CF5780` after six pushes: index,
attributes, id, size, parent, name).
"""
from __future__ import annotations

import argparse
import re
import struct
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = 0x400000
TABLE = 0x01876628                     # the 64 allocators
BUILD_POOLS, BUILD_POOLS_END = 0x00740590, 0x00740851
CLASS_MAP, APPLY_MAP_CALL = 0x00740860, 0x007423E5
MAP_ADD, APPLY_MAP, DTI_CTOR = 0x00CF5D60, 0x00CF6220, 0x00CF5780
DTI_ROOT = 0x01876CB4
NOT_FOUND = 0x7FFFFFFF
# Later (0x0041D014..0x0041D037): an MtScalableAllocator handed 1 MiB, initialised through vtable +0x2C with the
# name "AIWork" (0x0155A328), replaces slot 25.
AI_WORK_STORE, AI_WORK_NAME = 0x0041D031, 0x0155A328
AI_WORK_CODE = bytes.fromhex("6A 06 68 00 00 10 00 57 68 28 A3 55 01 8B CE FF D2 89 35 8C 66 87 01")
# What docs/re-memory-pools.md quotes; a different build or a changed reading fails here.
WANT_POOLS = {"Temp": (64, [5]), "System": (64, [11, 16]), "Unit": (64, [12, 17, 25]), "Effect": (5, [18]),
              "GUI": (5, [19]), "Array/String": (6, [2, 3, 13]), "Collision": (24, [4]), "Physics": (12, [15])}
WANT_CLASSES = {"uRigidBody": 12, "uRagdoll": 12, "uRagdollExt": 12, "uEnemy": 12, "uHumanEnemy": 12,
                "nPhysics::RigidBody": 15, "cUnit": 12}
SHOWN = r"^(uEm\d{4}|uHumanEnemy|uEnemy|uPlayer|uCharacterBase|uModel|uRigidBody(Ext)?|uRagdoll(Ext)?|" \
        r"uRagdoll::\w+|uCnsRagdoll|cRigidBody|cEm0100ActRagdoll|nPhysics::(RigidBody|System))$"


def load(exe: Path):
    data = exe.read_bytes()
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    n = struct.unpack_from("<H", data, pe + 6)[0]
    opt = struct.unpack_from("<H", data, pe + 20)[0]
    secs = [struct.unpack_from("<8sIIII", data, pe + 24 + opt + 40 * i) for i in range(n)]

    def off(va):
        for _, vsize, rva, rsize, ptr in secs:
            if BASE + rva <= va < BASE + rva + min(vsize, rsize):
                return ptr + va - BASE - rva
        return None

    text = next(s for s in secs if s[0].rstrip(b"\0") == b".text")
    return data, off, BASE + text[2], data[text[4]:text[4] + min(text[1], text[3])]


def cstr(data, off, va) -> str | None:
    o = off(va)
    return data[o:o + 160].split(b"\0")[0].decode("latin-1") if o is not None else None


def slot_of(op_str: str) -> int | None:
    m = re.fullmatch(r"dword ptr \[(0x[0-9a-f]+)\]", op_str)
    if not m:
        return None
    a = int(m.group(1), 16)
    return (a - TABLE) // 4 if TABLE <= a < TABLE + 256 and (a - TABLE) % 4 == 0 else None


def pools(md, data, off, text_va, text):
    """[(name, MiB, type, [slots])] in the order 0x00740590 builds them, read the way it runs."""
    code = text[BUILD_POOLS - text_va:BUILD_POOLS_END - text_va]
    table: dict[int, object] = {}
    regs: dict[str, object] = {}
    pushes: list[str] = []
    built = []
    for ins in md.disasm(code, BUILD_POOLS):
        m, ops = ins.mnemonic, ins.op_str
        if m == "push":
            pushes.append(ops)
        elif m == "call" and ops == "edx":                 # init, vtable +0x30: name, type, size, ...
            name, kind, size = (int(x, 0) for x in pushes[-1:-4:-1])
            built.append([cstr(data, off, name).strip('"'), size // (1 << 20), kind])
            regs["esi"] = len(built) - 1
            pushes.clear()
        elif m == "mov":
            dst, src = (s.strip() for s in ops.split(",", 1))
            if slot_of(dst) is not None and src in regs:
                table[slot_of(dst)] = regs[src]
            elif slot_of(src) is not None:
                regs[dst] = table.get(slot_of(src), "default")
            elif dst in regs and dst != "esi":
                regs.pop(dst)
    return [(name, mib, kind, sorted(s for s, v in table.items() if v == i)) for i, (name, mib, kind) in
            enumerate(built)], sorted(s for s, v in table.items() if v == "default")


def class_map(md, data, off, text_va, text):
    """[(name, slot)] as 0x00740860 adds them, in order."""
    code = text[CLASS_MAP - text_va:APPLY_MAP_CALL - text_va + 5]
    entries, pushes = [], []
    for ins in md.disasm(code, CLASS_MAP):
        if ins.mnemonic == "push":
            pushes.append(ins.op_str)
        elif ins.mnemonic == "call":
            target = int(ins.op_str, 0)
            if target == MAP_ADD:
                entries.append((cstr(data, off, int(pushes[-1], 0)), int(pushes[-2], 0)))
            elif target == APPLY_MAP:
                return entries
            pushes.clear()
    raise SystemExit("0x00740860 does not end in the map's apply call: a different build")


def classes(md, data, off, text_va, text):
    """{DTI: dict(name, parent, size, index)} from every constructor call in .text, and the calls not read."""
    out, unread = {}, []
    i = text.find(b"\xb9")
    while i != -1:
        if i + 10 <= len(text) and text[i + 5] == 0xE8 and \
                text_va + i + 10 + struct.unpack_from("<i", text, i + 6)[0] == DTI_CTOR:
            dti = struct.unpack_from("<I", text, i + 1)[0]
            for k in range(12, 70):
                ws = list(md.disasm(text[i - k:i], text_va + i - k))
                if ws and ws[-1].address + ws[-1].size == text_va + i and len(ws) >= 6 and \
                        all(w.mnemonic == "push" for w in ws[-6:]):
                    try:
                        index, _attr, _id, size, parent, name = (int(w.op_str, 0) for w in ws[-6:])
                    except ValueError:
                        break
                    out[dti] = dict(name=cstr(data, off, name), parent=parent, size=size, index=index)
                    break
            if dti not in out:
                unread.append(text_va + i)
        i = text.find(b"\xb9", i + 1)
    return out, unread


def direct_uses(md, text_va, text, slot):
    """Code that reads an allocator slot itself (`mov reg, [slot]`) and calls it: {'alloc' | 'free' | other:
    [instruction addresses]}; 'alloc' is vtable +0x20 (size, align), 'free' +0x24."""
    at = struct.pack("<I", TABLE + 4 * slot)
    uses: dict[str, list[int]] = {}
    i = text.find(at)
    while i != -1:
        start = i - 1 if text[i - 1] == 0xA1 else i - 2 if text[i - 2] == 0x8B and text[i - 1] & 0xC7 == 0x05 else None
        if start is not None:
            kind = "other"
            for w in list(md.disasm(text[start:start + 0x60], text_va + start))[1:20]:
                m = re.fullmatch(r"\w+, dword ptr \[\w+ \+ (0x[0-9a-f]+)\]", w.op_str)
                if w.mnemonic == "mov" and m:
                    kind = {0x20: "alloc", 0x24: "free"}.get(int(m.group(1), 16), f"vtable +{m.group(1)}")
                if w.mnemonic == "call":
                    break
            uses.setdefault(kind, []).append(text_va + start)
        i = text.find(at, i + 1)
    return uses


def walk(info, entries):
    """Each class's slot after 0x00CF6220 has applied the map (see the module's docstring)."""
    first: dict[str, int] = {}
    for name, slot in entries:
        first.setdefault(name, slot)
    find = lambda n: first.get(n, NOT_FOUND)  # noqa: E731
    by_name: dict[str, int] = {}
    children: dict[int, list[int]] = {}
    for d, v in info.items():
        by_name.setdefault(v["name"], d)
        children.setdefault(v["parent"], []).append(d)
    slot = {d: v["index"] & 0x3F for d, v in info.items()}

    def give(d, s):
        for c in children.get(d, []):
            slot[c] = s
            give(c, s)

    def strip(s):                                        # the last `::` scope off, outside template brackets
        depth = 0
        for k in range(len(s) - 1, 0, -1):
            depth += {">": 1, "<": -1}.get(s[k], 0)
            if depth == 0 and s[k] == ":":
                return s[:k - 1]
        return None

    def visit(d):
        v = info[d]
        found, up = find(v["name"]), v["parent"]
        while up != DTI_ROOT and up in info and found == NOT_FOUND:
            found, up = find(info[up]["name"]), info[up]["parent"]
        b = strip(v["name"]) if found == NOT_FOUND else None
        while b is not None and found == NOT_FOUND:
            found = find(b)
            dd = by_name.get(b)
            while found == NOT_FOUND and dd and dd != DTI_ROOT and dd in info:
                found, dd = find(info[dd]["name"]), info[dd]["parent"]
            b = strip(b)
        if found != NOT_FOUND:
            slot[d] = found & 0x3F
            give(d, slot[d])
        for c in children.get(d, []):
            visit(c)

    root = info.get(DTI_ROOT)
    if root and find(root["name"]) != NOT_FOUND:
        give(DTI_ROOT, find(root["name"]) & 0x3F)
    for c in children.get(DTI_ROOT, []):
        visit(c)
    return slot


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--exe")
    ap.add_argument("--classes", default=SHOWN, help="a regular expression of class names to list")
    args = ap.parse_args()
    try:
        from capstone import CS_ARCH_X86, CS_MODE_32, Cs
    except ImportError:
        print("needs capstone: pip install capstone")
        return 2
    if args.exe:
        exe = Path(args.exe)
    else:
        sys.path.insert(0, str(ROOT / "src"))
        from riftstone.game import find_game
        exe = find_game().root / "DDDA.exe"
    data, off, text_va, text = load(exe)
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    built, default = pools(md, data, off, text_va, text)
    entries = class_map(md, data, off, text_va, text)
    info, unread = classes(md, data, off, text_va, text)
    slot = walk(info, entries)
    per_slot = Counter(slot.values())
    pool_of = {s: name for name, _, _, slots in built for s in slots}

    print(f"the pools 0x00740590 builds ({sum(m for _, m, _, _ in built)} MiB):")
    for name, mib, kind, slots in built:
        n = sum(per_slot[s] for s in slots)
        print(f"  {name:<13} {mib:3d} MiB  type {kind}  slots {', '.join(map(str, slots)):<9} {n:5d} classes")
    print(f"  slots never given a pool keep MtDefaultAllocator; of them the tail copies {default} "
          f"({sum(per_slot[s] for s in range(64) if s not in pool_of)} classes on the default)")
    o = off(AI_WORK_STORE - len(AI_WORK_CODE) + 6)
    ai_work = o is not None and data[o:o + len(AI_WORK_CODE)] == AI_WORK_CODE and \
        cstr(data, off, AI_WORK_NAME) == "AIWork"
    print(f"  later, slot 25 ({per_slot[25]} classes: path finding) gets its own 1 MiB allocator \"AIWork\" "
          f"(0x{AI_WORK_STORE:08X}): " + ("as read" if ai_work else "NOT as this build's"))
    shown = " ".join(f"0x{a:08X}" for a in unread[:8])
    uses = direct_uses(md, text_va, text, 12)
    print("  code that takes the Unit slot (12) itself: " + ", ".join(f"{len(v)} {k}" for k, v in sorted(uses.items()))
          + f" (alloc: vtable +0x20, a size computed at run time; first {' '.join(f'0x{a:08X}' for a in uses.get('alloc', [])[:4])})")
    print(f"the class map: {len(entries)} entries; classes (DTI constructions read): {len(info)}, "
          f"{len(unread)} not read{': ' + shown if unread else ''}")
    print("classes per slot: " + ", ".join(f"{s}: {n}" for s, n in sorted(per_slot.items())))
    pattern = re.compile(args.classes)
    rows = sorted(((v["name"], v["size"], slot[d]) for d, v in info.items() if pattern.search(v["name"] or "")),
                  key=lambda r: (-r[1], r[0]))
    em = [r for r in rows if re.fullmatch(r"uEm\d{4}", r[0])]
    if em:
        sizes = sorted(r[1] for r in em)
        print(f"  {len(em)} enemy classes uEm####: {sizes[0]:,}..{sizes[-1]:,} bytes (median {sizes[len(sizes) // 2]:,}), "
              f"slots {sorted(set(r[2] for r in em))}")
    for name, size, s in rows:
        if not re.fullmatch(r"uEm\d{4}", name):
            print(f"  {name:<28} {size:7,} bytes  slot {s:2d} ({pool_of.get(s, 'MtDefaultAllocator')})")

    problems = [] if ai_work else [f"the AIWork allocator's store at 0x{AI_WORK_STORE:08X} is not build 2364871's"]
    got = {name: (mib, slots) for name, mib, _, slots in built}
    if got != WANT_POOLS:
        problems.append(f"the pools are {got}, the docs say {WANT_POOLS}")
    by_name = {v["name"]: d for d, v in info.items()}
    for name, want in WANT_CLASSES.items():
        if name not in by_name or slot[by_name[name]] != want:
            problems.append(f"{name} is in slot {slot.get(by_name.get(name))}, the docs say {want}")
    for p in problems:
        print("MISMATCH: " + p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

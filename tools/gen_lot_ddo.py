"""Write src/riftstone/data/lot_ddo.json: Dragon's Dogma Online's layout (.lot v138) grammar, read from
DDO.exe's own loaders -- no field is guessed.

  kind -> class   the table at 0x02139A20 (kinds 0..34) and sOmManager's registry, filled at start-up
                  by register(kind, DTI) calls (0x00BC6620); the registry is consulted first
  class -> fields each class's load (virtual slot 6) read linearly: reader slots +0x04 u16, +0x08 u32,
                  +0x0C u64, +0x14 s16, +0x18 s32, +0x24 f32, +0x30 v3, +0x34 v4, +0x38 a block whose
                  size is the count just read (list<n>), readString 0x013BBCE0, inline byte reads (each
                  starts with the bounds check against the reader's +0x10); fixed loops repeat their body;
                  a parent's load is followed wherever it is called
  names           the member each value is stored to, looked up in that class's MtDTI properties

Dev-only: needs capstone, the unpacked client exe and the DDO toolkit's symbol map and property dump:
    python tools/gen_lot_ddo.py [--exe DDO.exe] [--re C:\\Dev\\DDO\\re\\out] [--check]
--check parses every distinct layout in the client with the result (22,983 of 22,983 on 03.04.003).
"""
from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

READ_STRING, REFILL, TABLE, REGISTER = 0x013BBCE0, 0x013BC000, 0x02139A20, 0x00BC6620
SLOT = {0x04: "u16", 0x08: "u32", 0x0C: "u64", 0x14: "s16", 0x18: "s32", 0x24: "f32", 0x30: "v3", 0x34: "v4"}
LOCAL = (0x009C0000, 0x00A00000)   # cSetInfo* code: unnamed helpers here are followed on call
EXTRA_KINDS = {200: "cSetInfoEnemyLinked"}   # used by one layout (st1202 e00); not in the static tables


class Exe:
    def __init__(self, path: Path, re_dir: Path):
        import capstone

        sys.path.insert(0, str(Path(r"<path>")))
        from ddon.re.pe import Image

        self.img = Image(path)
        self.md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
        self.sym, self.va = {}, {}
        for line in open(re_dir / "symbols.tsv", encoding="utf-8"):
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 3 and not parts[0].startswith("#"):
                self.sym[int(parts[0], 16)] = parts[2]
                self.va[parts[2]] = int(parts[0], 16)
        self.props = json.load(open(re_dir / "props.json", encoding="utf-8"))
        self.primary: dict[str, int] = {}

    def dis(self, va: int, n: int = 0x3000):
        out = []
        for x in self.md.disasm(self.img.read(va, n), va):
            out.append(x)
            if x.mnemonic == "int3":
                break
        return out

    def primary_vtable(self, cls: str) -> int | None:
        """A class with an embedded omLOT has its sub-object's table under its name; the destructor
        stores the primary table into [this] first."""
        vt = self.va.get(cls + "::vftable")
        for slot in range(8) if vt else ():
            fn = self.img.u32(vt + 4 * slot)
            for x in self.md.disasm(self.img.read(fn, 48), fn) if fn else ():
                m = re.fullmatch(r"dword ptr \[e(?:si|ax|cx|di)\], (0x[0-9a-f]+)", x.op_str) if x.mnemonic == "mov" else None
                if m and int(m.group(1), 16) != vt:
                    return int(m.group(1), 16)
                if x.mnemonic == "ret":
                    break
        return None

    def loader(self, cls: str) -> int | None:
        va = None
        if cls not in self.primary:
            for suffix in ("::load", "::vf06"):
                if cls + suffix in self.va:
                    va = self.va[cls + suffix]
                    break
        if va is None:
            vt = self.primary.get(cls) or self.va.get(cls + "::vftable")
            va = self.img.u32(vt + 24) if vt else None
        while va is not None:   # thunks
            first = next(self.md.disasm(self.img.read(va, 16), va), None)
            if first is not None and first.mnemonic == "jmp" and first.op_str.startswith("0x"):
                va = int(first.op_str, 16)
                continue
            break
        return va

    def kinds(self) -> dict[int, str]:
        out = {}
        for k in range(0x23):
            w = self.img.u32(TABLE + 4 * k)
            if w:
                out[k] = self.sym.get(w, hex(w)).removesuffix("::DTI")
        import capstone

        sweep = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
        sweep.skipdata = True   # a linear sweep of .text meets data; skip it instead of stopping
        for s in self.img.sections:   # register(kind, DTI) calls: two pushes right before the call
            if not s.executable:
                continue
            last = []
            for x in sweep.disasm(self.img.data[s.off:s.off + s.rsize], s.va):
                if x.mnemonic == "push":
                    last = (last + [x.op_str])[-2:]
                elif x.mnemonic == "call" and x.op_str == hex(REGISTER):
                    if len(last) == 2 and all(re.fullmatch(r"0x[0-9a-f]+|\d+", v) for v in last):
                        dti, kind = int(last[0], 0), int(last[1], 0)
                        out[kind] = self.sym.get(dti, hex(dti)).removesuffix("::DTI")
                    last = []
                elif x.mnemonic not in ("mov",):
                    last = []
        out.update(EXTRA_KINDS)
        return out


def _store(ins, j, span=12, readers=()):
    for y in ins[j:j + span]:
        o = y.op_str
        if y.mnemonic == "movss" and o.endswith("dword ptr [eax]"):
            return None
        if y.mnemonic in ("mov", "movss") and "ptr [" in o and o.split(", ")[-1] in ("eax", "ax", "al", "dl", "xmm0"):
            if any(f"[{r} " in o or f"[{r}]" in o for r in readers):
                continue   # the reader's own cursor, not a member
            return o.split(", ")[0]
        if y.mnemonic == "fstp":
            return o
        if y.mnemonic == "call" and o != hex(REFILL):   # the refill inside a byte read is not a boundary
            return None
    return None


def trace(exe: Exe, va: int, cls: str, seen: set, notes: list) -> list:
    """-> steps: ("f", type, class, store) | ("repeat", n, steps)."""
    ins = exe.dis(va)
    readers, steps, depth = set(), [], 0
    addr_steps = []   # (address, step) for loop folding
    for i, x in enumerate(ins):
        m, o = x.mnemonic, x.op_str
        if m == "ret":
            break
        g = re.fullmatch(r"(e\w\w), dword ptr \[esp \+ (0x[0-9a-f]+|\d+)\]", o) if m == "mov" else None
        if g and int(g.group(2), 0) == depth + 4:
            readers.add(g.group(1))
        elif m == "mov" and re.fullmatch(r"(e\w\w), dword ptr \[ebp \+ 8\]", o):
            readers.add(o.split(",")[0])
        elif m == "mov" and re.fullmatch(r"(e\w\w), (e\w\w)", o) and o.split(", ")[1] in readers:
            readers.add(o.split(", ")[0])
        elif m in ("mov", "lea", "pop", "xor") and re.match(r"(e\w\w)(,|$)", o) and o.split(",")[0] in readers:
            readers.discard(o.split(",")[0])
        if x.address < va + 0x40:
            if m == "push":
                depth += 4
            elif m == "sub" and o.startswith("esp, "):
                depth += int(o.split(", ")[1], 0)
        off, vreg = None, "eax"
        mm = re.fullmatch(r"dword ptr \[(e\w\w) \+ (0x[0-9a-f]+|\d+)\]", o) if m == "call" else None
        if mm:
            vreg, off = mm.group(1), int(mm.group(2), 0)
        elif m == "call" and o == "eax":
            for y in reversed(ins[max(0, i - 5):i]):
                if y.mnemonic == "mov" and y.op_str.startswith("eax, dword ptr [eax + "):
                    off = int(y.op_str[len("eax, dword ptr [eax + "):-1], 16)
                    break
        if off is not None:
            back = " ".join(y.op_str for y in ins[max(0, i - 8):i])
            if any(f"{vreg}, dword ptr [{r}]" in back for r in readers):
                if off == 0x38:
                    scale = 1
                    for y in ins[max(0, i - 8):i]:
                        if y.mnemonic == "shl" and y.op_str.startswith("eax, "):
                            scale = 1 << int(y.op_str.split(", ")[1], 0)
                    if addr_steps and addr_steps[-1][1][0] == "f" and addr_steps[-1][1][1] in ("u32", "s32"):
                        a, st = addr_steps[-1]
                        addr_steps[-1] = (a, ("f", f"list{scale}", st[2], st[3]))
                    else:
                        notes.append(f"{cls}: a block read at {x.address:#x} has no count before it")
                    continue
                if off not in SLOT:
                    notes.append(f"{cls}: unknown reader slot +{off:#x} at {x.address:#x}")
                addr_steps.append((x.address, ("f", SLOT.get(off, "?"), cls, _store(ins, i + 1, 12, readers))))
            continue
        if m in ("call", "jmp") and o.startswith("0x"):
            tgt = int(o, 16)
            name = exe.sym.get(tgt, "")
            if tgt == READ_STRING:
                addr_steps.append((x.address, ("f", "str", cls, _string_member(ins, i))))
            elif tgt == REFILL:
                pass
            elif (name.endswith(("::load", "::vf06")) or (m == "call" and not name and LOCAL[0] <= tgt < LOCAL[1])) \
                    and tgt not in seen:
                seen.add(tgt)
                owner = name.rsplit("::", 1)[0] if name else cls
                for st in trace(exe, tgt, owner, seen, notes):
                    addr_steps.append((x.address, st))
                if m == "jmp":
                    break
        if m.startswith("j") and m != "jmp" and o.startswith("0x"):
            tgt = int(o, 16)
            if va <= tgt < x.address and any(a >= tgt for a, _ in addr_steps):
                n = _loop_count(ins, i, tgt)
                if n is None:
                    notes.append(f"{cls}: loop at {x.address:#x} with an unknown count")
                body = [st for a, st in addr_steps if a >= tgt]
                addr_steps = [(a, st) for a, st in addr_steps if a < tgt] + [(tgt, ("repeat", n, body))]
        mb = re.fullmatch(r"e\w\w, dword ptr \[(e\w\w) \+ 0x10\]", o) if m == "cmp" else None
        if mb and mb.group(1) in readers:
            addr_steps.append((x.address, ("f", "u8", cls, _store(ins, i + 1, 22, readers))))
    return [st for _, st in addr_steps]


def _string_member(ins, i):
    for y in ins[i + 1:i + 40]:
        if y.mnemonic == "lea" and re.fullmatch(r"ecx, \[e\w\w \+ 0x[0-9a-f]+\]", y.op_str):
            return "dword ptr [" + y.op_str.split("[")[1]
        if y.mnemonic == "call" and y.op_str == "0x403f80":
            break
    return None


def _loop_count(ins, j, tgt):
    regs = {y.op_str.split(",")[0] for y in ins if tgt <= y.address <= ins[j].address and y.mnemonic in ("dec", "sub")}
    for y in reversed([y for y in ins if y.address < tgt][-12:]):
        m = re.fullmatch(r"(e\w\w), (0x[0-9a-f]+|\d+)", y.op_str) if y.mnemonic == "mov" else None
        if m and m.group(1) in regs:
            return int(m.group(2), 0)
    for y in ins[max(0, j - 3):j]:
        m = re.fullmatch(r"e\w\w, (0x[0-9a-f]+|\d+)", y.op_str) if y.mnemonic == "cmp" else None
        if m:
            return int(m.group(1), 0)
    return None


COORD_ORDER = ("mName", "mUnitID", "mPosition", "mAngle", "mScale", "mAreaHitNo")


def name_fields(exe: Exe, steps: list, used: Counter, coord_i: list | None = None) -> list:
    coord_i = coord_i if coord_i is not None else [0]
    out = []
    for st in steps:
        if st[0] == "repeat":
            out.append(["repeat", st[1], name_fields(exe, st[2], used, coord_i)])
            continue
        _, typ, cls, store = st
        name = None
        m = re.search(r"\[e(?:di|si|bx|bp) \+ (0x[0-9a-f]+|\d+)\]", store or "")
        if m and ("edi" in store or "esi" in store):
            off = int(m.group(1), 0)
            for p in exe.props.get(cls, {}).get("props", []):
                if p.get("offset") and int(p["offset"], 16) == off:
                    name = p["name"]
                    break
        if name is None and cls == "cSetInfoCoord":   # stored through temporaries; read in property order
            name = COORD_ORDER[coord_i[0]] if coord_i[0] < len(COORD_ORDER) else None
        if cls == "cSetInfoCoord":
            coord_i[0] += 1
        name = name or f"{cls.removeprefix('cSetInfo')}_{typ}"
        used[name] += 1
        if used[name] > 1:
            name = f"{name}_{used[name]}"
        out.append([name, typ])
    return out


def build(exe_path: Path, re_dir: Path) -> tuple[dict, list[str]]:
    exe = Exe(exe_path, re_dir)
    notes: list[str] = []
    kinds = exe.kinds()
    out = {}
    for k, cls in sorted(kinds.items()):
        va = exe.loader(cls)
        steps = trace(exe, va, cls, {va}, notes) if va else []
        if not any(s[0] in ("f", "repeat") for s in steps):
            pv = exe.primary_vtable(cls)
            if pv:
                exe.primary[cls] = pv
                va = exe.loader(cls)
                steps = trace(exe, va, cls, {va}, notes)
        out[str(k)] = {"class": cls, "loader": f"{va:#010x}" if va else None,
                       "fields": name_fields(exe, steps, Counter())}
    return out, notes


def check(grammar: dict) -> Counter:
    from riftstone import corpus, lot_ddo, typemap
    from riftstone.game import find_game

    res = Counter()
    lot_ddo.use_grammar(grammar)
    try:
        for r in corpus.resources(find_game("ddo"), [typemap.type_for_extension("lot")], unique=True):
            try:
                ok = lot_ddo.build(lot_ddo.parse(r.data)) == r.data
            except Exception:  # noqa: BLE001 - counted
                ok = False
            res["exact" if ok else "failed"] += 1
    finally:
        lot_ddo.use_grammar(None)
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", type=Path, default=Path(r"<path>"))
    ap.add_argument("--re", dest="re_dir", type=Path, default=Path(r"<path>"))
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    kinds, notes = build(a.exe, a.re_dir)
    for n in notes:
        print("note:", n)
    doc = {"schema": "riftstone.lot-ddo/1", "version": 138,
           "source": {"exe": a.exe.name, "table": f"{TABLE:#010x}", "register": f"{REGISTER:#010x}"},
           "kinds": kinds}
    out = ROOT / "src" / "riftstone" / "data" / "lot_ddo.json"
    out.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(kinds)} kinds -> {out}")
    if a.check:
        print(dict(check(doc["kinds"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Measure again everything docs/ddo-dye.md says about Online's equipment dye.  Reads the Online client (and
Dark Arisen for the comparison); writes nothing but its report.

    python tools/ddo_dye_proof.py [--only shaders,materials,montages,items,ddda] [--dump PATH]

shaders    every pixel shader in sc\\DX9\\root that samples tColorMaskMap, disassembled with Windows'
           d3dcompiler_47 (D3DDisassemble; skipped elsewhere) and run from its colour-map sample to its
           detail-map sample on random inputs: the colour-mask factors must equal ddodye.ColourMask.factors
materials  every material that binds a colour mask: its class, CBColorMask's rate and threshold, the maps'
           formats and sizes
montages   every .dmt: version, kind, colour numbers in use, the mean colour of 10-15 (the dyes)
items      the item list and the model table rebuilt byte for byte; each weapon and armour's colour number
           against its model's montage
ddda       Dark Arisen's own equipment maps that are the same art as an Online colour map (32x32 structure),
           and how close Online's formula comes to their colours with the item's colours vs undyed
--dump     an unpacked DDO.exe (the retail one is encrypted on disk): checks the bytes of the montage's colour
           routine that docs/ddo-dye.md quotes
"""
from __future__ import annotations

import argparse
import collections
import colorsys
import ctypes
import math
import random
import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from riftstone import arc, ddodye, mrl, port, tex, texcodec, typemap  # noqa: E402
from riftstone.game import find_game  # noqa: E402
from riftstone.index import Index  # noqa: E402

TEX, MRL, MOD = typemap.BY_EXT["tex"], typemap.BY_EXT["mrl"], typemap.BY_EXT["mod"]
DMT, SPKG = typemap.type_for_extension("dmt"), typemap.type_for_extension("spkg")


class Client:
    def __init__(self, kind: str):
        self.game = find_game(kind)
        self.idx = Index(self.game)
        self._arcs: dict = {}

    def names(self, type_id: int, like: str | None = None) -> list[tuple[bytes, str]]:
        q = "SELECT name, MIN(arc) FROM res WHERE type=?" + (" AND arc LIKE ?" if like else "") + " GROUP BY name"
        return [(bytes(n), a) for n, a in self.idx.db.execute(q, (type_id, like) if like else (type_id,))]

    def load(self, name: bytes, type_id: int, where: str | None = None) -> bytes:
        a = where or self.idx.archives_with(name, type_id)[0]
        if a not in self._arcs:
            if len(self._arcs) > 48:
                self._arcs.clear()
            self._arcs[a] = arc.Archive.read(self.game.vanilla_arc(a))
        return self._arcs[a].find(name, type_id).data()


# -- shaders ----------------------------------------------------------------------------------------
def _walk(data: bytes, start: int) -> int | None:
    p = start + 4
    while p + 4 <= len(data):
        tok = struct.unpack_from("<I", data, p)[0]
        if tok == 0x0000FFFF:
            return p + 4
        if tok & 0xFFFF == 0xFFFE:
            p += 4 + 4 * ((tok >> 16) & 0x7FFF)
        elif tok & 0x80000000:
            return None
        else:
            p += 4 + 4 * ((tok >> 24) & 0xF)
    return None


def pixel_shaders(pkg: bytes):
    """(offset, bytecode, constant names) of every ps_3_0 shader with a constant table in a shader package."""
    pos = 0
    while True:
        i = pkg.find(struct.pack("<I", 0xFFFF0300), pos)
        if i < 0:
            return
        pos = i + 1
        if pkg[i + 8:i + 12] != b"CTAB":
            continue
        end = _walk(pkg, i)
        if end is None:
            continue
        sh = pkg[i:end]
        c = sh[12:]
        n, info = struct.unpack_from("<II", c, 12)
        names = []
        for k in range(n):
            o = struct.unpack_from("<I", c, info + 20 * k)[0]
            names.append(c[o:c.index(b"\0", o)].decode("latin-1"))
        yield i, sh, names


def disassemble(sh: bytes) -> str:
    d3d = ctypes.WinDLL("d3dcompiler_47.dll")
    d3d.D3DDisassemble.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint, ctypes.c_char_p,
                                   ctypes.POINTER(ctypes.c_void_p)]
    blob = ctypes.c_void_p()
    buf = ctypes.create_string_buffer(sh, len(sh))
    if d3d.D3DDisassemble(buf, len(sh), 0, None, ctypes.byref(blob)) != 0:
        raise RuntimeError("D3DDisassemble failed")
    vt = ctypes.cast(ctypes.cast(blob, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
    ptr = ctypes.WINFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p)(vt[3])(blob)
    size = ctypes.WINFUNCTYPE(ctypes.c_size_t, ctypes.c_void_p)(vt[4])(blob)
    text = ctypes.string_at(ptr, size).decode("latin-1").rstrip("\0")
    ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vt[2])(blob)
    return text


SW = "xyzw"


def _operand(tok: str):
    neg = tok.startswith("-")
    m = re.match(r"-?([a-zA-Z]+\d*)(_abs)?(?:\.([xyzw]+))?$", tok)
    sw = m.group(3) or "xyzw"
    return neg, bool(m.group(2)), m.group(1), sw + sw[-1] * (4 - len(sw))


def run_slice(lines, regs, consts, samples, rnd):
    """Interpret D3D9 assembly (the arithmetic subset these shaders use) over registers of four floats."""
    def get(name):
        store = consts if name.startswith("c") else regs
        return store.setdefault(name, [rnd.uniform(-1, 1) for _ in range(4)])

    def src(tok):
        neg, ab, name, sw = _operand(tok)
        v = [get(name)[SW.index(ch)] for ch in sw]
        v = [abs(x) for x in v] if ab else v
        return [-x for x in v] if neg else v

    for ln in lines:
        ln = ln.strip()
        if not ln or ln.startswith(("//", "def", "dcl")):
            continue
        op, *args = re.split(r"[ ,]+", ln)
        sat = op.endswith("_sat")
        op = op.replace("_sat", "")
        if op == "texld":
            val = list(samples[args[2]])
        else:
            s = [src(a) for a in args[1:]]
            if op == "mov":
                val = s[0]
            elif op == "add":
                val = [a + b for a, b in zip(s[0], s[1])]
            elif op == "mul":
                val = [a * b for a, b in zip(s[0], s[1])]
            elif op == "mad":
                val = [a * b + c for a, b, c in zip(*s)]
            elif op == "cmp":
                val = [b if a >= 0 else c for a, b, c in zip(*s)]
            elif op == "lrp":
                val = [a * (b - c) + c for a, b, c in zip(*s)]
            elif op in ("dp3", "dp4"):
                n = 3 if op == "dp3" else 4
                val = [sum(a * b for a, b in zip(s[0][:n], s[1][:n]))] * 4
            elif op in ("rsq", "rcp"):
                x = abs(s[0][0]) if op == "rsq" else s[0][0]
                val = [(x ** -0.5 if op == "rsq" else 1 / x) if x else float("inf")] * 4
            elif op == "nrm":
                n = math.sqrt(sum(a * a for a in s[0][:3])) or 1
                val = [a / n for a in s[0][:3]] + [s[0][3]]
            elif op in ("max", "min"):
                val = [(max if op == "max" else min)(a, b) for a, b in zip(s[0], s[1])]
            elif op in ("log", "exp", "pow"):
                val = [0.5] * 4
            else:
                raise RuntimeError(f"unexpected {op} in the colour slice")
        if sat:
            val = [min(1.0, max(0.0, v)) for v in val]
        m = re.match(r"(r\d+)(?:\.([xyzw]+))?$", args[0])
        cur = get(m.group(1))
        for ch in m.group(2) or "xyzw":
            cur[SW.index(ch)] = val[SW.index(ch)]


def check_shader(text: str, trials: int = 200) -> bool:
    regs = dict(re.findall(r"//\s+(\w+)\s+([cs]\d+)\s+\d+", text))
    body = text.split("ps_3_0", 1)[1].splitlines()
    alb, blend, cmm, ex = (regs[f"SSAlbedoMap__{n}"] for n in ("tAlbedoMap", "tAlbedoBlendMap", "tColorMaskMap",
                                                                "tDDMaterialAlbedoMapEx"))
    start = next(i for i, ln in enumerate(body) if re.search(rf"texld \w+, [^,]+, {alb}\b", ln))
    end = next(i for i, ln in enumerate(body) if re.search(rf"texld \w+, [^,]+, {ex}\b", ln))
    rnd = random.Random(7)
    for _ in range(trials):
        consts = {m.group(1): [float(x) for x in m.group(2).split(",")]
                  for m in (re.match(r"\s*def (c\d+), (.*)", ln) for ln in body) if m}
        A, B = [rnd.random() for _ in range(4)], [rnd.random() for _ in range(4)]
        M = [rnd.choice((0.0, 1.0, rnd.random())) for _ in range(4)]
        cm = ddodye.ColourMask(tuple(rnd.choice((1.0, rnd.random() * 1.5)) for _ in range(3)),
                               tuple(rnd.choice((1.0, 0.9, rnd.random())) for _ in range(3)),
                               tuple(tuple(rnd.random() for _ in range(3)) for _ in range(3)))
        g0, bc = [rnd.random() for _ in range(4)], [rnd.random() for _ in range(4)]
        for name, v in (("fColorMaskRate", (*cm.rate, 0)), ("fColorMaskThreshold", (*cm.threshold, 0)),
                        ("fColorMaskColor1", (*cm.colours[0], 1)), ("fColorMaskColor2", (*cm.colours[1], 1)),
                        ("fColorMaskColor3", (*cm.colours[2], 1)), ("Globals__packed0", g0), ("fAlbedoBlendColor", bc)):
            consts[regs[name]] = list(v)
        regs_: dict = {}
        depth = 0
        for ln in body[:start]:                   # constants a shader loads into registers before the slice
            t = ln.strip()
            depth += t.startswith("if") - t.startswith("endif")
            if depth == 0 and re.match(r"mov r\d+(\.[xyzw]+)?, c\d+(\.[xyzw]+)?$", t):
                run_slice([t], regs_, consts, {}, rnd)
        run_slice(body[start:end], regs_, consts, {alb: A, blend: B, cmm: M}, rnd)
        f = cm.factors(M[0], M[1], M[2])
        want = [(A[i] * A[i] * g0[1 + i] + B[i] * B[i] * bc[i] * bc[3] * B[3]) * f[i] for i in range(3)]
        if not any(all(abs(v[SW.index(c)] - w) <= 1e-9 * max(1, abs(w)) for c, w in zip(comps, want))
                   for v in regs_.values() for comps in ("xyz", "xzw", "yzw", "xyw")):
            return False
    return True


def shaders(ddo: Client) -> None:
    pkg = ddo.load(b"sc\\DX9\\root", SPKG)
    found = [(o, sh) for o, sh, names in pixel_shaders(pkg) if "SSAlbedoMap__tColorMaskMap" in names]
    total = sum(1 for _ in pixel_shaders(pkg))
    print(f"shaders: {total} ps_3_0 shaders with a constant table in sc\\DX9\\root; {len(found)} sample tColorMaskMap")
    if sys.platform != "win32":
        print("  (D3DDisassemble needs Windows' d3dcompiler_47: not checked here)")
        return
    ok = sum(check_shader(disassemble(sh)) for _, sh in found)
    print(f"  {ok} of {len(found)} compute ColourMask.factors on the squared colour map (200 random draws each)")
    squared = used = 0
    for _, sh, names in pixel_shaders(pkg):
        if "SSAlbedoMap__tAlbedoMap" not in names:
            continue
        text = disassemble(sh)
        s = dict(re.findall(r"//\s+(\w+)\s+([cs]\d+)\s+\d+", text)).get("SSAlbedoMap__tAlbedoMap")
        body = [ln.strip() for ln in text.split("ps_3_0", 1)[1].splitlines()]
        at = next((i for i, ln in enumerate(body) if re.match(rf"texld (r\d+), [^,]+, {s}\b", ln)), None)
        if at is None:
            continue
        r = re.match(r"texld (r\d+)", body[at]).group(1)
        for ln in body[at + 1:]:
            if re.match(rf"mul r\d+(\.\w+)?, {r}(\.\w+)?, {r}(\.\w+)?$", ln):
                squared += 1
                used += 1
                break
            if re.match(rf"\w+ {r}(\.[xyzw]*[xyz][xyzw]*)?,", ln):      # rgb overwritten: was it read before?
                used += any(re.search(rf"\b{r}\.[xyzw]*[xyz]", x) for x in body[at + 1:body.index(ln)])
                break
    print(f"  the colour map squared (linear light) in {squared} of the {used} shaders that read its colour "
          "(by pattern: a mul of the sample by itself)")


# -- materials ------------------------------------------------------------------------------------------
def materials(ddo: Client) -> None:
    n_files = n_mat = 0
    classes, rates, thr, folders = collections.Counter(), collections.Counter(), collections.Counter(), collections.Counter()
    pairs = set()
    for name, a in ddo.names(MRL):
        raw = ddo.load(name, MRL, a)
        try:
            m = mrl.parse(raw)
            masked = ddodye.masked_materials(raw)
        except Exception:  # noqa: BLE001 - a census
            continue
        n_files += 1
        n_mat += len(m.materials)
        for x in m.materials:
            mm = masked.get(x.material_hash)
            if mm is None:
                continue
            classes[port.MATERIAL_CLASSES.get(x.shader, hex(x.shader))] += 1
            folders["\\".join(name.decode("latin-1").split("\\")[:2])] += 1
            rates[mm.own.rate] += 1
            thr[tuple(round(v, 4) for v in mm.own.threshold)] += 1
            pairs.add((mm.albedo, mm.mask))
    print(f"materials: {sum(classes.values())} of {n_mat} materials in {n_files} distinct .mrl bind a colour mask")
    print(f"  classes {dict(classes)}; rate {dict(rates)}; threshold {dict(thr)}")
    print(f"  folders {dict(folders.most_common(12))}")
    fa, fm, ratio = collections.Counter(), collections.Counter(), collections.Counter()
    for albedo, mask in sorted(pairs):
        ta, tm = tex.parse(ddo.load(albedo.encode("latin-1"), TEX)), tex.parse(ddo.load(mask.encode("latin-1"), TEX))
        fa[ta.fmt] += 1
        fm[tm.fmt] += 1
        ratio[f"{ta.width // max(1, tm.width)}x"] += 1
    print(f"  {len(pairs)} (map, mask) pairs; map formats {dict(fa)}; mask formats {dict(fm)}; map/mask size "
          f"{dict(ratio.most_common(5))}")


# -- montages -----------------------------------------------------------------------------------------
def montages(ddo: Client) -> None:
    files, kinds, used, bad = 0, collections.Counter(), collections.Counter(), 0
    dye = collections.defaultdict(list)
    for name, a in ddo.names(DMT):
        try:
            m = ddodye.parse_montage(ddo.load(name, DMT, a))
        except Exception:  # noqa: BLE001
            bad += 1
            continue
        files += 1
        kinds[m.kind] += 1
        u = m.used()
        used.update(u)
        if m.variants == 16:
            for v in range(10, 16):
                cols = [c for e in m.variant(v) if not e.empty for c in e.colours]
                if cols:
                    dye[v].append([sum(c[k] for c in cols) / len(cols) for k in range(3)])
    print(f"montages: {files} read, {bad} refused; kinds {dict(sorted(kinds.items()))}")
    print(f"  colour numbers in use (files): {dict(sorted(used.items()))}")
    beside = filled = unmasked = outside = own_is_one = shared_two_ways = 0
    for name, a in ddo.names(DMT):
        try:
            mdl, mat = ddo.load(name, MOD), ddo.load(name, MRL)
        except (IndexError, AttributeError):
            continue
        beside += 1
        m = ddodye.parse_montage(ddo.load(name, DMT, a))
        names = port.model_info(mdl).material_names
        masked = ddodye.masked_materials(mat)
        hashes = [ddodye.jamcrc(n) for n in names]
        for e in m.entries:
            if e.empty:
                continue
            if e.material >= len(names):
                outside += 1
            elif hashes[e.material] in masked:
                filled += 1
            else:
                unmasked += 1
        close = lambda a, b: all(abs(x - y) <= 1e-4 for c, d in zip(a, b) for x, y in zip(c, d))  # noqa: E731
        two_ways = matches_own = False
        for v in m.used():
            ents = [e for e in m.variant(v) if not e.empty and e.material < len(names) and hashes[e.material] in masked]
            if ents and all(close(e.colours, masked[hashes[e.material]].own.colours) for e in ents):
                matches_own = True
            by_map = collections.defaultdict(list)
            for e in ents:
                mm = masked[hashes[e.material]]
                by_map[(mm.albedo, mm.mask)].append(e.colours)
            two_ways |= any(any(not close(c, cs[0]) for c in cs) for cs in by_map.values())
        shared_two_ways += two_ways
        own_is_one += matches_own
    print(f"  {beside} montages beside their model and material: {filled} filled entries name a material with "
          f"CBColorMask, {unmasked} one without, {outside} a material index past the model's; the material file's "
          f"own colours are one of the montage's in {own_is_one}; {shared_two_ways} models dye one map two ways in "
          "some colour")
    for v, name in zip(range(10, 16), ("red", "green", "blue", "yellow", "pink", "black")):
        xs = dye[v]
        if xs:
            r, g, b = (sum(x[k] for x in xs) / len(xs) for k in range(3))
            h, s, val = colorsys.rgb_to_hsv(r, g, b)
            print(f"  {v} ({name}): {len(xs)} montages of 16, mean ({r:.3f}, {g:.3f}, {b:.3f}), hue {h * 360:.0f}, "
                  f"value {val:.2f}")


# -- items ----------------------------------------------------------------------------------------------
def items(ddo: Client) -> None:
    raw = ddo.load(ddodye.ITEM_LIST, ddodye.ITL_TYPE)
    il = ddodye.read_itemlist(raw)
    same = ddodye.build_itemlist(il) == raw
    counts = {k: len(v) for k, v in il.records.items()}
    print(f"items: etc\\itemlist {len(raw):,} bytes, rebuilt byte for byte: {same}; records {counts}")
    raw = ddodye._client(ddo.game, ddo.idx, ddodye.RES_TABLE, ddodye.WRT_TYPE)
    version, table = ddodye.read_restable(raw)
    print(f"  etc\\wepResTable version {version}, {len(table)} records, rebuilt byte for byte: "
          f"{ddodye.build_restable(version, table) == raw}")
    cat = ddodye.load_catalog(ddo.game, ddo.idx)
    names = {n: None for n, _ in ddo.names(DMT)}
    by_hash = {ddodye.jamcrc(n): n for n in names}
    cache, st = {}, collections.Counter()
    for e in cat.equipment():
        st[e.kind] += 1
        for r in cat.entries(e.tag):
            dh = ddodye._refs(r, DMT)
            d = by_hash.get(dh) if dh is not None else None
            if d is None:
                st["no montage"] += 1
                continue
            if d not in cache:
                cache[d] = ddodye.parse_montage(ddo.load(d, DMT))
            m = cache[d]
            if not m.variants:
                st["montage without colours"] += 1
            elif e.colour % m.variants in m.used():
                st["colour number hits colours"] += 1
            else:
                st["colour number hits an empty variant"] += 1
    print(f"  equipment {dict(st)}; default colour numbers "
          f"{dict(sorted(collections.Counter(e.colour for e in cat.equipment()).items()))}")


# -- Dark Arisen's own art -------------------------------------------------------------------------------
def _thumb(data: bytes, side: int):
    t = tex.parse(data)
    if t.width != t.height or t.width < side:
        return None
    lv = min(int(math.log2(t.width // side)), t.mip_count - 1)
    try:
        w, h, px = texcodec.decode(t, lv)
    except Exception:  # noqa: BLE001
        return None
    return (w, h, px) if w == side else None


def _structure(px: bytes) -> list:
    lum = [0.2126 * px[i] + 0.7152 * px[i + 1] + 0.0722 * px[i + 2] for i in range(0, len(px), 4)]
    mean = sum(lum) / len(lum)
    sd = math.sqrt(sum((v - mean) ** 2 for v in lum) / len(lum)) or 1
    return [(v - mean) / sd for v in lum]


def ddda(ddo: Client) -> None:
    dd = Client("ddda")
    nuki = [(n, a) for n, a in dd.names(TEX, "rom/eq/%") if n.decode("latin-1").upper().endswith("_NUKI")]
    lum = []
    for n, a in random.Random(5).sample(nuki, min(150, len(nuki))):
        t = tex.parse(dd.load(n, TEX, a))
        _, _, px = texcodec.decode(t, min(3, t.mip_count - 1))
        v = [0.2126 * px[i] + 0.7152 * px[i + 1] + 0.0722 * px[i + 2] for i in range(0, len(px), 4) if px[i + 3] >= 128]
        lum.append(sum(v) / max(1, len(v)))
    print(f"ddda: {len(nuki)} _NUKI colour maps in rom/eq; mean luminance of 150: {sum(lum) / len(lum):.1f} of 255")
    slots, classes = collections.Counter(), collections.Counter()
    for n, a in dd.names(MRL, "rom/eq/%"):
        raw = dd.load(n, MRL, a)
        m = mrl.parse(raw)
        for x in m.materials:
            for b in mrl.bindings(raw, x):
                if b.kind == mrl.SET_TEXTURE and 0 < b.value <= len(m.textures) and \
                        m.textures[b.value - 1].name.upper().endswith("_CMM"):
                    slots[mrl.SLOT_NAMES.get(b.slot, "tEmissionMap" if b.slot == mrl._jam20("tEmissionMap")
                                             else f"0x{b.slot:05x}")] += 1
                    classes[port.MATERIAL_CLASSES.get(x.shader, hex(x.shader))] += 1
    pkgs = mentions = 0
    for n, a in dd.names(SPKG):
        pkgs += 1
        mentions += bool(re.search(rb"ColorMask|ColourMask", dd.load(n, SPKG, a)))
    print(f"  its _CMM maps are bound as {dict(slots)} by {dict(classes)}; {mentions} of its {pkgs} shader "
          "packages name a colour mask")
    theirs = {}
    for n, a in nuki:
        th = _thumb(dd.load(n, TEX, a), 32)
        if th:
            theirs[n.decode("latin-1")] = _structure(th[2])
    by_map = collections.defaultdict(list)
    for dn, _ in ddo.names(DMT):
        try:
            mdl, mat = ddo.load(dn, MOD), ddo.load(dn, MRL)
        except (IndexError, AttributeError):
            continue
        mon = ddodye.parse_montage(ddo.load(dn, DMT))
        names = port.model_info(mdl).material_names
        for h, mm in ddodye.masked_materials(mat).items():
            by_map[mm.albedo].append((mm, mon, names))
    pairs = []
    for albedo in sorted(by_map):
        if "model_hmem" in albedo:
            continue
        th = _thumb(ddo.load(albedo.encode("latin-1"), TEX), 32)
        if th is None:
            continue
        z = _structure(th[2])
        best = max(((sum(a * b for a, b in zip(z, z2)) / len(z), n) for n, z2 in theirs.items()), default=None)
        if best and best[0] > 0.9:
            pairs.append((best[0], albedo, best[1]))
    pairs.sort(reverse=True)
    print(f"  {len(pairs)} Online colour maps are Dark Arisen art (32x32 structure correlation > 0.9)")
    results = []
    for _, albedo, theirs_name in pairs[:40]:
        users = by_map[albedo]
        tw, th_, target = _thumb(dd.load(theirs_name.encode("latin-1"), TEX), 64) or (0, 0, b"")
        mine = _thumb(ddo.load(albedo.encode("latin-1"), TEX), 64)
        if not target or mine is None:
            continue
        mask_tex = tex.parse(ddo.load(users[0][0].mask.encode("latin-1"), TEX))
        side = max(1, 64 * mask_tex.width // tex.parse(ddo.load(albedo.encode("latin-1"), TEX)).width)
        mw, mh, mask = texcodec.decode(mask_tex, min(mask_tex.mip_count - 1, max(0, int(math.log2(mask_tex.width // side)))))
        cands = {"undyed": ddodye.ColourMask((0, 0, 0), (1, 1, 1), ((1, 1, 1),) * 3)}
        for mm, mon, names in users:
            cands[f"own {id(mm)}"] = mm.own
            for v in mon.used():
                for e in mon.variant(v):
                    if not e.empty and e.material < len(names) and ddodye.jamcrc(names[e.material]) == mm.material_hash:
                        cands[f"{v} {id(mm)}"] = ddodye.ColourMask(mm.own.rate, mm.own.threshold, e.colours)
        errs, bright = {}, {}
        for label, cm in cands.items():
            out, _ = ddodye.bake_rgba(64, 64, mine[2], mw, mh, mask, cm)
            keep = [i for i in range(0, len(out), 4) if target[i + 3] >= 128 and out[i + 3] >= 128]
            d = [sum(abs(out[i + k] - target[i + k]) for k in range(3)) / 3 for i in keep]
            errs[label] = sum(d) / max(1, len(d))
            bright[label] = sum(0.2126 * out[i] + 0.7152 * out[i + 1] + 0.0722 * out[i + 2] for i in keep) / max(1, len(keep))
        keep = [i for i in range(0, len(target), 4) if target[i + 3] >= 128]
        theirs_lum = sum(0.2126 * target[i] + 0.7152 * target[i + 1] + 0.0722 * target[i + 2] for i in keep) / max(1, len(keep))
        pick = min(errs, key=errs.get)
        results.append((errs[pick], errs["undyed"], theirs_lum, bright["undyed"], bright[pick]))
    col = lambda k: sorted(r[k] for r in results)[len(results) // 2]  # noqa: E731
    print(f"  {len(results)} compared at 64x64: mean absolute error with the closest of the item's colours median "
          f"{col(0):.1f}, undyed median {col(1):.1f} (of 255); luminance medians: Dark Arisen's map {col(2):.1f}, "
          f"Online's undyed {col(3):.1f}, baked with that colour {col(4):.1f}")


# -- the exe ------------------------------------------------------------------------------------------------
DUMP_CLAIMS = (  # (address, bytes) in the unpacked DDO.exe: rDDOModelMontage's colour routine
    (0x00A6A668, "81 38 44 4D 54 00"),            # cmp dword [eax], 'DMT\0' (the loader)
    (0x00A6A678, "83 78 04 11"),                  # cmp dword [eax+4], 0x11
    (0x00A69AC3, "68 6E 21 2C 7B"),               # push 0x7B2C216E (constant buffer 0x7B2C2)
    (0x00A69ACF, "68 0E 12 80 6C"),               # push 0x6C80120E (CBMaterial)
    (0x00A69ADD, "68 31 63 01 6F"),               # push 0x6F016331 (CBColorMask)
    (0x00A69AF4, "89 8D A0 00 00 00"),            # mov [ebp+0xA0], ecx (row 0 -> row 10 of 0x7B2C2)
    (0x00A69B12, "89 43 10"),                     # mov [ebx+0x10], eax (row 1 -> CBMaterial row 1)
    (0x00A69B28, "89 42 20"),                     # mov [edx+0x20], eax (rows 2-4 -> CBColorMask + 0x20)
    (0x00A69814, "83 7F 08 09"),                  # cmp dword [edi+8], 9 (kind 9: only when forced)
    (0x00A6982A, "F7 F6"),                        # div esi: the colour number modulo the variant count
)


def dump(path: str) -> None:
    sys.path.insert(0, str(ROOT / "tools"))
    from doc_claims import Image

    img = Image(Path(path))
    ok = 0
    for va, text in DUMP_CLAIMS:
        want = bytes.fromhex(text)
        got = img.read(va, len(want))
        ok += got == want
        if got != want:
            print(f"  0x{va:08X}: want {text}, the dump has {got.hex(' ').upper() if got else 'nothing'}")
    print(f"dump: {ok} of {len(DUMP_CLAIMS)} quoted instructions hold in {path}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", default="shaders,materials,montages,items,ddda")
    ap.add_argument("--dump", help="an unpacked DDO.exe to check the montage routine's quoted bytes in")
    a = ap.parse_args(argv)
    ddo = Client("ddo")
    for part in [p.strip() for p in a.only.split(",") if p.strip()]:
        {"shaders": shaders, "materials": materials, "montages": montages, "items": items, "ddda": ddda}[part](ddo)
    if a.dump:
        dump(a.dump)
    return 0


if __name__ == "__main__":
    sys.exit(main())

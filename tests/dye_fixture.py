"""Stand-in games for the dye tests (and the dye fuzz target's seeds): an Online client with one armour in the
shape the real ones have -- a DDMrlStdEstObj material binding a colour map, a colour mask and CBColorMask,
a montage of colours, the item list, the model table, the item names -- and a Dark Arisen install with an
armour to replace.

  Online   rom/armor/ab219999_00   obj\\ab\\ab219999\\model\\ab219999_00 (.mod .mrl .dmt), maps _0_NUKI/_0_CMM
           three materials: High__1 and Mid__2 share one colour map and mask, Plain has no mask
           items 900/901 "Test Plate" (colour 0, either sex), 902 "Dawn Test Plate" (colour 2, women only)
  Online   rom/base, rom/wep_res_table, rom/ui: the item list, the model table, the item names
  DDDA     rom/eq/test/m_armor         model\\pl\\m\\m_wst_b\\m_wst_b999\\m_wst_b999 (.mod .mrl, its _NUKI)
"""
from __future__ import annotations

import struct
from pathlib import Path

import helpers  # noqa: F401 (sys.path)
import monster_fixture as mf
from riftstone import ddodye, gmd, mrl, tex, texcodec, typemap
from riftstone.game import Game

MOD, MRL, TEX, GMD = (typemap.BY_EXT[e] for e in ("mod", "mrl", "tex", "gmd"))
DMT, ITL, WRT = (typemap.type_for_extension(e) for e in ("dmt", "itl", "wrt"))
FOLDER = "obj\\ab\\ab219999\\model"
MODEL = FOLDER + "\\ab219999_00"
FEMALE = FOLDER + "\\ab219999_01"
ALBEDO = FOLDER + "\\ab219999_0_NUKI"
MASK = FOLDER + "\\ab219999_0_CMM"
NAMES = (b"OBJ_EQ_High__1", b"OBJ_EQ_Mid__2", b"OBJ_EQ_Plain")
DDDA_MODEL = "model\\pl\\m\\m_wst_b\\m_wst_b999\\m_wst_b999"
DDDA_MAP = DDDA_MODEL + "_NUKI"
SIDE = 16
ROOT_JOINT = [(0, None, (0.0, 0.0, 0.0))]   # a joint keeps the model's name table clear of the
#                                              bone remap (monster_fixture.model fills it for bone-less models)

RATE, THRESHOLD = (1.0, 1.0, 1.0), (1.0, 1.0, 0.9)
OWN = ((0.5, 0.5, 0.5), (0.6, 0.4, 0.3), (0.3, 0.3, 0.3))                  # CBColorMask of the material file
VARIANT0 = [((0.2, 0.2, 0.2), (0.5, 0.4, 0.3), (0.25, 0.3, 0.35)),        # High__1
            ((0.9, 0.9, 0.9), (0.5, 0.4, 0.3), (0.6, 0.5, 0.4))]           # Mid__2: other colours, same map
RED = [((0.4, 0.4, 0.4), (0.5, 0.3, 0.2), (0.55, 0.06, 0.06))] * 2
VARIANT2 = [((0.3, 0.3, 0.3), (0.5, 0.4, 0.3), (0.7, 0.62, 0.1))] * 2


def _block(albedo: int, mask: int, cm_rows: tuple, spec=(0.8, 0.8, 0.8), env=(0.1, 0.1, 0.1)) -> tuple[bytes, int]:
    """A DDMrlStdEstObj command block: albedo, mask, CBColorMask, CBMaterial and the 22-row buffer 0x7B2C2."""
    binds = [(mrl.SET_TEXTURE, ddodye.SLOT_ALBEDO, albedo), (mrl.SET_TEXTURE, ddodye.SLOT_MASK, mask),
             (1, ddodye.CB_COLOR_MASK, None), (1, ddodye.CB_MATERIAL, None), (1, ddodye.CB_GLOBALS, None)]
    head = len(binds) * mrl.CMD.size
    cbs = {ddodye.CB_COLOR_MASK: struct.pack("<20f", *RATE, 0.0, *THRESHOLD, 0.0, *cm_rows[0], 1.0, *cm_rows[1], 1.0,
                                             *cm_rows[2], 1.0),
           ddodye.CB_MATERIAL: struct.pack("<32f", 1, 1, 1, 1, *env, 10.0, *[0.0] * 24),
           ddodye.CB_GLOBALS: struct.pack("<88f", 0, 1, 1, 1, *[1.0] * 36, *spec, 30.0, *[1.0] * 44)}
    data, offsets = b"", {}
    for slot, blob in cbs.items():
        offsets[slot] = head + len(data)
        data += blob
    cmd = b""
    for kind, slot, value in binds:
        v = offsets[slot] if kind == 1 else value
        cmd += mrl.CMD.pack(kind | (0xDCDC << 4), v, slot << 12)
    return cmd + data, len(binds)


def material(textures: list[str], blocks: list[tuple[bytes, int, int]], version: int = 0x22) -> bytes:
    """blocks: (command block, binding count, material hash)."""
    texs = []
    for p in textures:
        t = mrl.Texture(mrl.TEX_TYPE_ID, 0, 0, b"")
        t.set_name(p)
        texs.append(t)
    recs = [mrl.Material(ddodye.MODEL_CLASS, h, [len(cmd), 0, 0, 0, n, 0, 0, 0, 0, 0, 0, 0, 0]) for cmd, n, h in blocks]
    return mrl.assemble(version, 0x4363CDF4 if version == 0x22 else 0xB46006D5, texs, recs,
                        [(cmd, b"") for cmd, _, _ in blocks])


def plain_block(albedo: int) -> tuple[bytes, int]:
    return mrl.CMD.pack(mrl.SET_TEXTURE | (0xDCDC << 4), albedo, ddodye.SLOT_ALBEDO << 12), 1


def albedo_pixels(side: int = SIDE) -> bytes:
    """A colour map: grey ramps, a transparent corner (the cut-out)."""
    px = bytearray()
    for y in range(side):
        for x in range(side):
            v = 60 + (x * 150) // side
            a = 0 if (x < 4 and y < 4) else 255
            px += bytes((v, v - 10 if v > 10 else v, v + 20 if v < 235 else v, a))
    return bytes(px)


def mask_pixels(side: int = SIDE // 2) -> bytes:
    """A mask half the map's size: the three top rows and the last one white (no dye), a band with green off
    (colour 2), then blue off (colour 3) with a red-off column (colour 1)."""
    px = bytearray()
    for y in range(side):
        for x in range(side):
            if y < 3 or y == side - 1:
                c = (255, 255, 255)
            elif y < 5:
                c = (255, 0, 255)
            else:
                c = (0 if x >= side - 2 else 255, 255, 0)
            px += bytes((*c, 255))
    return bytes(px)


def albedo_tex(version: int = tex.VERSION_DDO) -> bytes:
    return texcodec.encode(SIDE, SIDE, albedo_pixels(), tex.Tex(tex.attr1_for(0x20000, version), version, 1, SIDE,
                                                                 SIDE, 1, 20, 1, b""), cutout=True)


def mask_tex() -> bytes:
    side = SIDE // 2
    return texcodec.encode(side, side, mask_pixels(), tex.Tex(0x20002, tex.VERSION_DDO, 1, side, side, 1, 19, 1, b""),
                           cutout=False)


def entries(rows_by_material: list, per_variant: int = 3) -> list:
    out = []
    for k in range(per_variant):
        if k < len(rows_by_material):
            c1, c2, c3 = rows_by_material[k]
            rows = (0.8, 0.8, 0.8, 0.0, 0.1, 0.1, 0.1, 0.0, *c1, 1.0, *c2, 1.0, *c3, 1.0)
        else:
            rows = (0.0,) * 20
        out.append(ddodye.Entry(k, rows))
    return out


def montage(kind: int = 1) -> bytes:
    """16 colour numbers: 0 and 2 an item's own, 3 empty, 10-15 the dyes (red is the only one filled in)."""
    table = []
    for v in range(16):
        if v == 0:
            table += entries(VARIANT0)
        elif v == 2:
            table += entries(VARIANT2)
        elif 10 <= v <= 15:
            table += entries(RED)
        else:
            table += entries([])
    return ddodye.build_montage(kind, 16, 3, table)


def itemlist() -> bytes:
    """Two armour groups on model tag 5 (colour 0 either sex; colour 2 women only), three items, one weapon."""
    rec = {name: [] for name, _, _ in ddodye.IPA_CLASSES}
    rec["material"].append(((8135, 3, 2, 3, 28, 541, 1, 3916, 3713, 100, 99), [(81, 3, 0, 0)]))   # Red Dye
    rec["weapon"].append(((62, 1, 3, 4168, 18132, 0, 121, 0, 80, 1, 0), [(84, 3, 25), (67, 1, 7), (5, 2, -3)]))
    rec["weapon group"].append(((6, 0, 0, 4, 1, 818, 2049, 1, 5, 1, 1, 1, 0), None))
    for iid, g in ((900, 0), (901, 0), (902, 1)):
        rec["armour"].append(((iid, 1, 3, 1, 1, 30, 0, 0, 13, 21, 36, 0, g), [(1, 7, None)]))
    rec["armour group"].append(((5, 0, 0, 1, 1, 246, 13315, 3, 5, 1), None))
    rec["armour group"].append(((5, 0, 2, 2, 1, 247, 13315, 3, 5, 3), None))
    rec["npc"].append(((25022, 9, 3, 5, 0, 0, 1, 12, 0), None))
    return ddodye.build_itemlist(ddodye.ItemList(0x44, 1, 8, [1, 2, 3], rec))


def restable() -> bytes:
    ref = lambda t, n: (t << 32) | ddodye.jamcrc(n.encode("latin-1"))  # noqa: E731
    e = [ddodye.ResEntry(0, "", 0, (0,) * 7),
         ddodye.ResEntry(5, "ab219999_00", 1, (ref(MOD, MODEL), 0, 0, 0, 0, ref(DMT, MODEL), 0)),
         ddodye.ResEntry(5, "ab219999_01", 2, (ref(MOD, FEMALE), 0, 0, 0, 0, ref(DMT, FEMALE), 0)),
         ddodye.ResEntry(6, "wp300000", 0, (0,) * 7)]
    return ddodye.build_restable(11, e)


def ddo_material() -> bytes:
    h = [ddodye.jamcrc(n) for n in NAMES]
    return material([ALBEDO, MASK, "obj\\textures\\obj_b_BM"],
                    [(*_block(1, 2, OWN), h[0]), (*_block(1, 2, OWN), h[1]), (*plain_block(1), h[2])])


def make_ddo(root: Path) -> Game:
    rom = root / "nativePC" / "rom"
    rom.mkdir(parents=True)
    (root / "DDO.exe").write_bytes(b"stub")
    model = mf.model(ROOT_JOINT, [0], 0xD2, materials=NAMES, fmts=(mf.FMT,) * 3)
    parts = [(ALBEDO, TEX, albedo_tex()), (MASK, TEX, mask_tex()),
             ("obj\\textures\\obj_b_BM", TEX, mf.sheet("b", tex.VERSION_DDO, 8))]
    for name in (MODEL, FEMALE):
        parts += [(name, MOD, model), (name, MRL, ddo_material()), (name, DMT, montage())]
    mf._arc(rom / "armor" / "ab219999_00.arc", parts, True)
    mf._arc(rom / "base.arc", [("etc\\itemlist", ITL, itemlist())], True)
    mf._arc(rom / "wep_res_table.arc", [("etc\\wepResTable", WRT, restable())], True)
    names = ["", "Test Plate", "Dawn Test Plate", "", "Bronze Sword"]
    msgs = [gmd.Message(t, f"ITEM_NAME_{i}") for i, t in enumerate(names)]
    mf._arc(rom / "ui.arc", [("ui\\00_message\\common\\item_name", GMD,
                              gmd.build(gmd.Gmd(0, "TextWeb", msgs, version=gmd.VERSION_DDO)))], True)
    return Game(root, "ddo")


def make_ddda(root: Path) -> Game:
    rom = root / "nativePC" / "rom"
    rom.mkdir(parents=True)
    (root / "DDDA.exe").write_bytes(b"stub")
    sheet = mf.sheet("a", tex.VERSION, 16)
    mat = mf.material(0x20, [DDDA_MAP], names=(b"body",))
    mf._arc(rom / "eq" / "test" / "m_armor.arc",
            [(DDDA_MAP, TEX, sheet), (DDDA_MODEL, MRL, mat),
             (DDDA_MODEL, MOD, mf.model(ROOT_JOINT, [0], 0xD4, materials=(b"body",)))], False)
    return Game(root, "ddda")


def make(base: Path) -> dict:
    return {"ddda": make_ddda(base / "ddda"), "ddo": make_ddo(base / "ddo")}

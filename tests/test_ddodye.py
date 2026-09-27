"""Online's equipment dye baked into colour maps (ddodye.py, `riftstone ddo dye`, `riftstone port --dye`):
the shader's formula, baking pixels and textures, the montage, the item list and the model table read
byte-exact, what people type, porting an armour into a Dark Arisen mod with its colours -- on stand-in games
(dye_fixture.py) -- and one real item when the Online client is on this PC."""
import contextlib
import io
import math
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401 (sys.path)
import dye_fixture as df
from riftstone import arc, cli, ddodye, mod, mrl, port, tex, texcodec, typemap
from riftstone.errors import FormatError, RiftError
from riftstone.index import Index

setUpModule, tearDownModule = helpers.module_env("RIFTSTONE_HOME")

TEX, MRL = typemap.BY_EXT["tex"], typemap.BY_EXT["mrl"]
CM = ddodye.ColourMask((1.0, 1.0, 1.0), (1.0, 1.0, 0.9), ((0.5, 0.5, 0.5), (0.6, 0.4, 0.3), (0.3, 0.2, 0.1)))


def reference(m, cm):
    """The shader's arithmetic written out once more, straight from the disassembly (docs/ddo-dye.md)."""
    w = [(1 - m[i]) * cm.rate[i] for i in range(3)]
    out = []
    for c in range(3):
        a = 1 + w[0] * (cm.colours[0][c] - 1)
        if cm.threshold[0] - w[0] >= 0:
            a *= 1 + w[1] * (cm.colours[1][c] - 1)
        if cm.threshold[1] - w[0] >= 0 and cm.threshold[2] - w[1] >= 0:
            a *= 1 + w[2] * (cm.colours[2][c] - 1)
        out.append(a)
    return out


class FormulaTest(unittest.TestCase):
    def test_factors(self):
        self.assertEqual(CM.factors(1, 1, 1), (1.0, 1.0, 1.0))                  # a white mask dyes nothing
        self.assertEqual(CM.factors(0, 1, 1), (0.5, 0.5, 0.5))                  # red off: colour 1 only
        for m in ((0, 0, 0), (1, 0, 1), (1, 1, 0), (0.5, 0.25, 0.75), (1, 0.05, 0), (1, 0.2, 0)):
            self.assertEqual(list(CM.factors(*m)), reference(m, CM), m)
        # the third colour needs w.g <= threshold.z (0.9): a green channel under 0.1 keeps it off
        self.assertEqual(CM.factors(1, 0.05, 0)[0], 1 + 0.95 * (0.6 - 1))
        self.assertAlmostEqual(CM.factors(1, 0.2, 0)[0], (1 + 0.8 * (0.6 - 1)) * 0.3)
        zero = ddodye.ColourMask((0, 0, 0), (1, 1, 0.9), CM.colours)
        self.assertEqual(zero.factors(0, 0, 0), (1.0, 1.0, 1.0))               # rate 0: no dye anywhere

    def test_check(self):
        for bad in (ddodye.ColourMask((math.nan, 1, 1), (1, 1, 1), CM.colours),
                    ddodye.ColourMask((1, 1, 1), (1, 1, 1), ((math.inf, 0, 0), (0, 0, 0), (0, 0, 0))),
                    ddodye.ColourMask((1, 1), (1, 1, 1), CM.colours)):
            with self.assertRaises(RiftError):
                bad.check()

    def test_hex(self):
        self.assertEqual(ddodye.colour_of_hex("#ff0000"), (1.0, 0.0, 0.0))
        self.assertAlmostEqual(ddodye.colour_of_hex("808080")[0], (128 / 255) ** 2)
        self.assertEqual(ddodye.hex_of(ddodye.colour_of_hex("#3a7bd5")), "#3a7bd5")    # round trip
        self.assertEqual(ddodye.hex_of((2.0, -1.0, 0.25)), "#ff0080")                  # clamped for showing
        for bad in ("", "#12345", "red", "#gg0000", "#1234567"):
            with self.assertRaises(RiftError):
                ddodye.colour_of_hex(bad)


class BakeRgbaTest(unittest.TestCase):
    def test_pixels(self):
        px, mask = df.albedo_pixels(), df.mask_pixels()
        out, changed = ddodye.bake_rgba(16, 16, px, 8, 8, mask, CM)
        self.assertEqual(len(out), len(px))
        self.assertEqual(out[3::4], px[3::4])                                    # alpha stays
        for y in range(16):
            for x in range(16):
                o = 4 * (y * 16 + x)
                self.assertEqual(changed[y * 16 + x], int(out[o:o + 3] != px[o:o + 3]), (x, y))
        # map rows 0-4 and 15 read only white mask texels: the bilinear taps of a row y are mask rows
        # floor(y/2 - 0.25) and the next, wrapping (row 0 reads mask rows 7 and 0)
        for y in (0, 1, 2, 3, 4, 15):
            self.assertEqual(out[y * 64:(y + 1) * 64], px[y * 64:(y + 1) * 64], y)
        self.assertNotEqual(out[5 * 64:6 * 64], px[5 * 64:6 * 64])
        wrapped = bytearray(df.mask_pixels())
        wrapped[7 * 32:8 * 32] = bytes((255, 0, 255, 255)) * 8                  # the last mask row dyed:
        out2, _ = ddodye.bake_rgba(16, 16, px, 8, 8, bytes(wrapped), CM)        # the first map row sees it
        self.assertNotEqual(out2[:64], px[:64])
        # a texel whose taps are all the same mask value: texel * sqrt(factor), rounded
        y, x = 12, 4                                                             # mask (255, 255, 0): colour 3
        o = 4 * (y * 16 + x)
        s = [math.sqrt(f) for f in CM.factors(1, 1, 0)]
        self.assertEqual(list(out[o:o + 3]), [min(255, int(px[o + k] * s[k] + 0.5)) for k in range(3)])

    def test_nothing_to_do(self):
        px, mask = df.albedo_pixels(), df.mask_pixels()
        for cm in (ddodye.ColourMask((0, 0, 0), (1, 1, 0.9), CM.colours),
                   ddodye.ColourMask((1, 1, 1), (1, 1, 0.9), ((1, 1, 1),) * 3)):
            out, changed = ddodye.bake_rgba(16, 16, px, 8, 8, mask, cm)
            self.assertEqual((out, sum(changed)), (px, 0))
        white = bytes([255]) * (8 * 8 * 4)
        self.assertEqual(ddodye.bake_rgba(16, 16, px, 8, 8, white, CM)[0], px)

    def test_negative_and_bright(self):
        px = bytes((100, 100, 100, 255)) * 4
        black = bytes((0, 0, 0, 255))
        neg = ddodye.ColourMask((1, 1, 1), (1, 1, 1), ((-3, 2, 9), (1, 1, 1), (1, 1, 1)))
        out, _ = ddodye.bake_rgba(2, 2, px, 1, 1, black, neg)
        self.assertEqual(out[:4], bytes((0, 141, 255, 255)))                   # negative light is none; 255 caps

    def test_sizes(self):
        with self.assertRaises(RiftError):
            ddodye.bake_rgba(2, 2, bytes(15), 1, 1, bytes(4), CM)
        with self.assertRaises(RiftError):
            ddodye.bake_rgba(2, 2, bytes(16), 1, 1, bytes(3), CM)
        out, _ = ddodye.bake_rgba(3, 1, bytes((90, 90, 90, 255)) * 3, 5, 2, bytes((0, 255, 255, 255)) * 10, CM)
        self.assertEqual(out[:3], bytes((64, 64, 64)))                          # 90 * sqrt(0.5)


class BakeTextureTest(unittest.TestCase):
    def test_keeps_the_map(self):
        src, mask = df.albedo_tex(), df.mask_tex()
        out, n = ddodye.bake_texture(src, mask, CM)
        a, b = tex.parse(src), tex.parse(out)
        self.assertGreater(n, 0)
        self.assertEqual((b.fmt, b.width, b.height, b.mip_count, b.attr1, b.version, b.attr3),
                         (a.fmt, a.width, a.height, a.mip_count, a.attr1, a.version, a.attr3))
        self.assertIsNotNone(tex._pixels_contiguous(b))
        self.assertEqual(texcodec.decode(b, 0)[2][3::4], texcodec.decode(a, 0)[2][3::4])    # the cut-out stays
        # the top rows of 4x4 blocks see only white mask: their blocks keep their bytes, at every mip too
        top = 4 * a.mip_count
        row = 4 * 8                                                           # 4 blocks of 8 bytes
        self.assertEqual(b.body[top:top + row], a.body[top:top + row])

    def test_revision_and_nothing(self):
        src, mask = df.albedo_tex(), df.mask_tex()
        out, _ = ddodye.bake_texture(src, mask, CM, tex.VERSION)
        t = tex.parse(out)
        self.assertEqual((t.version, t.attr1), (tex.VERSION, tex.attr1_for(tex.parse(src).attr1, tex.VERSION)))
        same, n = ddodye.bake_texture(src, mask, ddodye.ColourMask((0, 0, 0), (1, 1, 1), CM.colours))
        self.assertEqual((same, n), (src, 0))                                   # nothing to dye: the input back
        moved, n = ddodye.bake_texture(src, mask, ddodye.ColourMask((0, 0, 0), (1, 1, 1), CM.colours), tex.VERSION)
        self.assertEqual(tex.parse(moved).body, tex.parse(src).body)

    def test_bc3_and_refusals(self):
        bc3 = texcodec.encode(8, 8, df.albedo_pixels(8), tex.Tex(0x20002, tex.VERSION_DDO, 1, 8, 8, 1, 24, 1, b""))
        out, n = ddodye.bake_texture(bc3, df.mask_tex(), CM)
        self.assertEqual(tex.parse(out).fmt, 24)
        self.assertGreater(n, 0)
        normal = texcodec.encode(8, 8, df.albedo_pixels(8), tex.Tex(0x20002, tex.VERSION_DDO, 1, 8, 8, 1, 24, 1, b""))
        bc5 = tex.build(tex.Tex(0x20002, tex.VERSION_DDO, 1, 4, 4, 1, 31, 1, struct.pack("<I", 20) + bytes(16)))
        for bad in (bc5, b"TEX\0" + bytes(12), b"nope"):
            with self.assertRaises(RiftError):
                ddodye.bake_texture(bad, df.mask_tex(), CM)
        with self.assertRaises(RiftError):
            ddodye.bake_texture(normal, df.mask_tex(), CM, 0x123)

    def test_dyed_name(self):
        a = ddodye.dyed_name(df.ALBEDO, df.MASK, CM)
        self.assertTrue(a.startswith(df.ALBEDO + "_d") and len(a) == len(df.ALBEDO) + 10)
        self.assertEqual(a, ddodye.dyed_name(df.ALBEDO, df.MASK, CM))
        other = ddodye.ColourMask(CM.rate, CM.threshold, ((0.5, 0.5, 0.5), (0.6, 0.4, 0.3), (0.3, 0.2, 0.2)))
        self.assertNotEqual(a, ddodye.dyed_name(df.ALBEDO, df.MASK, other))


class MontageTest(unittest.TestCase):
    def test_round_trip_and_variants(self):
        m = ddodye.parse_montage(df.montage())
        self.assertEqual((m.kind, m.variants, m.per_variant, len(m.entries)), (1, 16, 3, 48))
        self.assertEqual(m.used(), [0, 2, 10, 11, 12, 13, 14, 15])
        self.assertEqual(m.variant(16), m.variant(0))                           # colour numbers wrap, as DDO.exe
        self.assertEqual(m.variant(0)[1].colours, tuple(tuple(struct.unpack("<3f", struct.pack("<3f", *c)))
                                                        for c in df.VARIANT0[1]))
        self.assertEqual(ddodye.parse_montage(ddodye.build_montage(1, 16, 3, m.entries)).entries, m.entries)
        self.assertEqual(ddodye.parse_montage(ddodye.build_montage(9, 0, 0, [])).variant(3), [])
        # fuzz finding (dye_tables, slow): 50 million variants of no entries walked every variant in used()
        import time
        t0 = time.perf_counter()
        empty = ddodye.parse_montage(ddodye.build_montage(9, 50331648, 0, []))
        self.assertEqual((empty.used(), empty.variant(12345)), ([], []))
        self.assertLess(time.perf_counter() - t0, 1.0)

    def test_refusals(self):
        good = df.montage()
        for bad in (b"", good[:40], b"XMT\0" + good[4:], good[:4] + struct.pack("<I", 0x10) + good[8:],
                    good[:-1], good[:0x30] + struct.pack("<I", len(good) + 1) + good[0x34:]):
            with self.assertRaises(FormatError):
                ddodye.parse_montage(bad)
        with self.assertRaises(RiftError):
            ddodye.build_montage(1, 2, 2, [])


class TablesTest(unittest.TestCase):
    def test_itemlist(self):
        raw = df.itemlist()
        il = ddodye.read_itemlist(raw)
        self.assertEqual(ddodye.build_itemlist(il), raw)
        self.assertEqual([f[0] for f, _ in il.records["armour"]], [900, 901, 902])
        self.assertEqual(il.records["weapon"][0][1], [(84, 3, 25), (67, 1, 7), (5, 2, -3)])
        self.assertEqual(il.groups("armour")[1][0][2], 2)                       # the second group's colour
        for bad in (raw[:-1], raw + b"\0", raw[:60], b"ipa\0" + bytes(60), raw[:4] + struct.pack("<I", 0x43) + raw[8:],
                    raw[:8] + struct.pack("<I", 0) + raw[12:]):                  # the dye's parameter has no slot
            with self.assertRaises(FormatError):
                ddodye.read_itemlist(bad)

    def test_restable(self):
        raw = df.restable()
        version, entries = ddodye.read_restable(raw)
        self.assertEqual((version, len(entries), entries[1].arc, entries[2].sex), (11, 4, "ab219999_00", 2))
        self.assertEqual(ddodye.build_restable(version, entries), raw)
        for bad in (raw[:-1], raw + b"x", raw[:7], raw[:4] + struct.pack("<I", 9999) + raw[8:]):
            with self.assertRaises(FormatError):
                ddodye.read_restable(bad)


class SpecTest(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(ddodye.parse_spec(None).kind, "default")
        self.assertEqual(ddodye.parse_spec(" Default ").kind, "default")
        self.assertEqual(ddodye.parse_spec("material").kind, "material")
        self.assertEqual((ddodye.parse_spec("Black").kind, ddodye.parse_spec("Black").variant), ("variant", 15))
        self.assertEqual(ddodye.parse_spec("23").variant, 23)
        one = ddodye.parse_spec("#ff8000")
        self.assertEqual((one.kind, one.colours[0], one.colours[2]), ("rgb", (1.0, (128 / 255) ** 2, 0.0),
                                                                          (1.0, (128 / 255) ** 2, 0.0)))
        three = ddodye.parse_spec("#ff0000, #00ff00,#0000ff")
        self.assertEqual(three.colours, ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)))
        for bad in ("rainbow", "restorer", "256", "#12", "#ff0000,#00ff00", "purple", "1.5"):
            with self.assertRaises(RiftError, msg=bad):
                ddodye.parse_spec(bad)


class PlanTest(unittest.TestCase):
    def setUp(self):
        self.masked = ddodye.masked_materials(df.ddo_material())
        self.mon = ddodye.parse_montage(df.montage())

    def test_masked(self):
        h = [ddodye.jamcrc(n) for n in df.NAMES]
        self.assertEqual(sorted(self.masked), sorted(h[:2]))                    # Plain has no mask
        mm = self.masked[h[0]]
        self.assertEqual((mm.albedo, mm.mask, mm.own.rate, mm.own.threshold), (df.ALBEDO, df.MASK, df.RATE,
                                                                                 tuple(struct.unpack("<3f", struct.pack("<3f", *df.THRESHOLD)))))
        self.assertAlmostEqual(mm.specular[0], 0.8, places=6)
        self.assertAlmostEqual(mm.env[0], 0.1, places=6)
        self.assertEqual(ddodye.masked_materials(mf_material_without_mask()), {})

    def test_specs(self):
        names = list(df.NAMES)
        h = [ddodye.jamcrc(n) for n in names]
        cols, notes = ddodye.plan_colours(self.masked, names, self.mon, ddodye.parse_spec("default"), 2)
        self.assertEqual(cols[h[0]].colours[2][:1], struct.unpack("<f", struct.pack("<f", 0.7)))
        cols, _ = ddodye.plan_colours(self.masked, names, self.mon, ddodye.parse_spec("default"), None)
        self.assertNotEqual(cols[h[0]].colours, cols[h[1]].colours)            # colour 0: two materials differ
        cols, notes = ddodye.plan_colours(self.masked, names, self.mon, ddodye.parse_spec("default"), 3)
        self.assertTrue(any("empty" in n for n in notes))                       # an empty colour: the file's own
        self.assertEqual(cols[h[0]].colours, self.masked[h[0]].own.colours)
        cols, _ = ddodye.plan_colours(self.masked, names, self.mon, ddodye.parse_spec("red"))
        self.assertEqual(cols[h[0]], cols[h[1]])
        cols, _ = ddodye.plan_colours(self.masked, names, self.mon, ddodye.parse_spec("#102030"))
        self.assertEqual(cols[h[0]].rate, df.RATE)
        cols, _ = ddodye.plan_colours(self.masked, names, None, ddodye.parse_spec("default"))
        self.assertEqual(cols[h[1]], self.masked[h[1]].own)
        with self.assertRaisesRegex(RiftError, "empty"):
            ddodye.plan_colours(self.masked, names, self.mon, ddodye.parse_spec("3"))
        with self.assertRaises(RiftError):
            ddodye.plan_colours(self.masked, names, None, ddodye.parse_spec("red"))
        forced = ddodye.parse_montage(df.montage(kind=9))
        cols, notes = ddodye.plan_colours(self.masked, names, forced, ddodye.parse_spec("default"), 0)
        self.assertTrue(any("kind 9" in n for n in notes))
        self.assertEqual(cols[h[0]], self.masked[h[0]].own)
        self.assertEqual(ddodye.plan_colours({}, names, self.mon, ddodye.parse_spec("red"))[0], {})

    def test_repoint(self):
        raw = df.ddo_material()
        h = [ddodye.jamcrc(n) for n in df.NAMES]
        out = ddodye.repoint(raw, {h[1]: "ddo\\x_d00000001"})
        m = mrl.parse(out)
        shown = {x.material_hash: [m.textures[b.value - 1].name for b in mrl.bindings(out, x)
                                   if b.kind == mrl.SET_TEXTURE and b.slot == ddodye.SLOT_ALBEDO] for x in m.materials}
        self.assertEqual(shown, {h[0]: [df.ALBEDO], h[1]: ["ddo\\x_d00000001"], h[2]: [df.ALBEDO]})
        # the table keeps what some binding uses, renumbered: the unbound sheen map goes, the mask stays bound
        self.assertEqual([t.name for t in m.textures], [df.ALBEDO, df.MASK, "ddo\\x_d00000001"])
        self.assertEqual(ddodye.masked_materials(out)[h[0]].mask, df.MASK)
        self.assertEqual(ddodye.masked_materials(out)[h[0]].own, ddodye.masked_materials(raw)[h[0]].own)
        self.assertEqual(ddodye.repoint(raw, {}), mrl.rebuild(raw))
        gone = ddodye.repoint(raw, {h[0]: "ddo\\y", h[1]: "ddo\\y", h[2]: "ddo\\y"})
        self.assertEqual(port.used_textures(gone), [df.MASK, "ddo\\y"])        # ALBEDO no longer bound: dropped
        self.assertEqual({t.name for t in mrl.parse(gone).textures}, {df.MASK, "ddo\\y"})


def mf_material_without_mask() -> bytes:
    return df.material(["a\\b_NUKI"], [(*df.plain_block(1), 7)])


class StandIn(unittest.TestCase):
    """Both stand-in games, indexed."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        base = Path(cls.tmp.name)
        cls.old_home = os.environ.get("RIFTSTONE_HOME")
        os.environ["RIFTSTONE_HOME"] = str(base / "home")
        cls.base = base
        cls.games = df.make(base)
        cls.idxs = {k: Index(g) for k, g in cls.games.items()}
        for i in cls.idxs.values():
            i.refresh()

    @classmethod
    def tearDownClass(cls):
        for i in cls.idxs.values():
            i.close()
        if cls.old_home is None:
            os.environ.pop("RIFTSTONE_HOME", None)
        else:
            os.environ["RIFTSTONE_HOME"] = cls.old_home
        cls.tmp.cleanup()

    @property
    def g(self):
        return self.games["ddo"]

    @property
    def i(self):
        return self.idxs["ddo"]


class ResolveTest(StandIn):
    def test_catalog(self):
        cat = ddodye.load_catalog(self.g, self.i)
        eq = {e.id: e for e in cat.equipment()}
        self.assertEqual(sorted(eq), [62, 900, 901, 902])
        self.assertEqual((eq[900].name, eq[900].tag, eq[900].colour, eq[902].colour, eq[902].sex, eq[62].kind),
                         ("Test Plate", 5, 0, 2, 3, "weapon"))

    def test_by_item_and_path(self):
        t = ddodye.resolve(self.g, self.i, "test plate")
        self.assertEqual((t.model, t.montage, t.default, t.item.id), (df.MODEL.encode(), df.MODEL.encode(), 0, 900))
        self.assertEqual(ddodye.resolve(self.g, self.i, "Test Plate", "female").model, df.FEMALE.encode())
        dawn = ddodye.resolve(self.g, self.i, "902")                            # women only: the female model
        self.assertEqual((dawn.model, dawn.default), (df.FEMALE.encode(), 2))
        with self.assertRaisesRegex(RiftError, "women only"):
            ddodye.resolve(self.g, self.i, "902", "male")
        p = ddodye.resolve(self.g, self.i, "obj/ab/ab219999/model/ab219999_00.mod")
        self.assertEqual((p.model, p.montage, p.item, p.default, len(p.users)), (df.MODEL.encode(), df.MODEL.encode(),
                                                                                  None, 0, 3))
        for bad, words in (("", "which"), ("Plate", "different looks"), ("no such thing", "no Online"),
                           ("Bronze Sword", "not in this client"), ("obj/ab/nothing.mod", "not in"),
                           ("test plate", "sex")):
            with self.assertRaisesRegex(RiftError, words, msg=bad):
                ddodye.resolve(self.g, self.i, bad, "other" if words == "sex" else None)

    def test_describe_and_folder(self):
        look = ddodye.load_look(self.g, self.i, ddodye.resolve(self.g, self.i, "901"))
        text = "\n".join(ddodye.describe(look))
        for words in ("item 901 Test Plate: worn in colour 0", "2 of 3 material(s)", "10 red dye", "montage kind 1"):
            self.assertIn(words, text)
        out = self.base / "maps"
        baked, notes = ddodye.bake_look(self.g, self.i, look, ddodye.parse_spec("default"))
        self.assertEqual(len(baked), 2)                                         # one map, two ways
        files = ddodye.write_folder(out, baked, look, "tex")
        self.assertEqual(sorted(p.name for p in files), ["ab219999_0_NUKI_OBJ_EQ_High__1.tex",
                                                         "ab219999_0_NUKI_OBJ_EQ_Mid__2.tex"])
        t = tex.parse(files[0].read_bytes())
        self.assertEqual((t.version, t.fmt, t.width), (tex.VERSION, 20, df.SIDE))
        red, _ = ddodye.bake_look(self.g, self.i, look, ddodye.parse_spec("red"))
        self.assertEqual(len(red), 1)
        self.assertEqual([p.suffix for p in ddodye.write_folder(out / "png", red, look, "png")], [".png"])
        self.assertEqual(ddodye.write_folder(out / "dds", red, look, "dds")[0].read_bytes()[:4], b"DDS ")
        with self.assertRaises(RiftError):
            ddodye.write_folder(out, red, look, "bmp")


class PortTest(StandIn):
    def port(self, name, colour, **kw):
        root = mod.Mod.create(self.base / "mods" / name, name, game="ddda").root
        spec, look = ddodye.port_plan("obj/ab/ab219999/model/ab219999_00.mod", colour, self.g, self.i, "ddda")
        before = ddodye.snapshot(root)
        res = port.into_mod(root, self.g, self.games["ddda"], self.i, self.idxs["ddda"],
                            "obj/ab/ab219999/model/ab219999_00.mod",
                            as_="model/pl/m/m_wst_b/m_wst_b999/m_wst_b999.mod", **kw)
        done = ddodye.dye_port(root, look, spec, ddodye.changed_since(root, before), self.g, self.i)
        return root, res, done, look

    def material(self, root):
        raw = (root / "files" / "model/pl/m/m_wst_b/m_wst_b999/m_wst_b999.mrl").read_bytes()
        m = mrl.parse(raw)
        return {x.material_hash: m.textures[b.value - 1].name for x in m.materials for b in mrl.bindings(raw, x)
                if b.kind == mrl.SET_TEXTURE and b.slot == ddodye.SLOT_ALBEDO}

    def test_default_two_ways(self):
        root, res, done, look = self.port("Dyed Default", "default")
        h = [ddodye.jamcrc(n) for n in df.NAMES]
        shown = self.material(root)
        self.assertNotEqual(shown[h[0]], shown[h[1]])                           # one map, two colour sets
        self.assertTrue(shown[h[0]].startswith("ddo\\" + df.ALBEDO + "_d"))
        self.assertEqual(shown[h[2]], "ddo\\" + df.ALBEDO)                      # no mask: untouched
        folder = root / "archives" / "rom/eq/test/m_armor.arc"
        undyed = folder / "ddo/obj/ab/ab219999/model/ab219999_0_NUKI.tex"
        self.assertTrue(undyed.is_file())                                       # Plain still uses it
        self.assertEqual(done.removed, [])
        cols, _ = ddodye.plan_colours(look.masked, look.material_names, look.montage, ddodye.parse_spec("default"),
                                      0)
        for k in (0, 1):
            p = folder / (shown[h[k]].replace("\\", "/") + ".tex")
            want, _ = ddodye.bake_texture(df.albedo_tex(), df.mask_tex(), cols[h[k]], tex.VERSION)
            self.assertEqual(p.read_bytes(), want)
        built = self.built(root)
        names = {e.name.decode("latin-1") for e in built["rom/eq/test/m_armor"].entries}
        self.assertTrue({shown[h[0]], shown[h[1]], "ddo\\" + df.ALBEDO} <= names)

    def test_the_recipe_carries_the_colour_and_a_replay_makes_the_same_files(self):
        """A package ships the port as a recipe (sources.py): it names the colour, and replaying it on the player's
        own games makes every dyed file byte for byte (without the colour it would re-port the armour undyed)."""
        from riftstone import sources

        root = mod.Mod.create(self.base / "mods" / "Dyed Recipe", "Dyed Recipe", game="ddda").root
        res = port.into_mod(root, self.g, self.games["ddda"], self.i, self.idxs["ddda"],
                            "obj/ab/ab219999/model/ab219999_00.mod",
                            as_="model/pl/m/m_wst_b/m_wst_b999/m_wst_b999.mod", dye="red")
        self.assertTrue(res.dyed is not None and res.dyed.written and res.dye_label)
        (recipe,) = [r for r in sources.load(root)["recipes"] if r["kind"] == "port"]
        self.assertEqual(recipe["args"]["dye"], "red")
        have = sources._mod_files(root)
        self.assertEqual(set(recipe["files"]), set(have), "the recipe lists exactly the files the mod holds")
        self.assertTrue(any("_d" in f for f in recipe["files"]), "the dyed copies are among them")

        games, idxs = self.games, self.idxs

        class Here:
            def game(self, kind):
                return games[kind]

            def index(self, kind):
                return idxs[kind]

        made = sources.replay([recipe], Here())
        self.assertEqual(set(made), set(have))
        for rel, data in have.items():
            self.assertEqual(made[rel], data, rel)
        plain = mod.Mod.create(self.base / "mods" / "Plain Recipe", "Plain Recipe", game="ddda").root
        port.into_mod(plain, self.g, self.games["ddda"], self.i, self.idxs["ddda"], "obj/ab/ab219999/model/ab219999_00.mod",
                      as_="model/pl/m/m_wst_b/m_wst_b999/m_wst_b999.mod")
        (undyed,) = [r for r in sources.load(plain)["recipes"] if r["kind"] == "port"]
        self.assertNotIn("dye", undyed["args"])                  # an undyed port's recipe is what it always was

    def test_red_shared_and_undyed_removed(self):
        root = mod.Mod.create(self.base / "mods" / "Red", "Red", game="ddda").root
        spec, look = ddodye.port_plan("obj/ab/ab219999/model/ab219999_00.mod", "red", self.g, self.i, "ddda")
        look.masked[ddodye.jamcrc(df.NAMES[2])] = look.masked[ddodye.jamcrc(df.NAMES[0])]   # Plain dyed too
        before = ddodye.snapshot(root)
        port.into_mod(root, self.g, self.games["ddda"], self.i, self.idxs["ddda"], "obj/ab/ab219999/model/ab219999_00.mod",
                      as_="model/pl/m/m_wst_b/m_wst_b999/m_wst_b999.mod")
        done = ddodye.dye_port(root, look, spec, ddodye.changed_since(root, before), self.g, self.i)
        shown = self.material(root)
        self.assertEqual(len(set(shown.values())), 2)                           # red: High and Mid share one copy
        self.assertEqual(len(done.removed), 1)                                  # the undyed map is not used now
        self.assertFalse(any("NUKI.tex" == p.name[-8:] for p in done.written if p.suffix == ".tex"))
        m = mrl.parse((root / "files" / "model/pl/m/m_wst_b/m_wst_b999/m_wst_b999.mrl").read_bytes())
        self.assertNotIn("ddo\\" + df.ALBEDO, [t.name for t in m.textures])    # nothing loads the removed map
        red_copy = {p for p in done.written if p.suffix == ".tex"}
        # porting again in another colour: the first colour's copies go, no material uses them any more
        before = ddodye.snapshot(root)
        port.into_mod(root, self.g, self.games["ddda"], self.i, self.idxs["ddda"], "obj/ab/ab219999/model/ab219999_00.mod",
                      as_="model/pl/m/m_wst_b/m_wst_b999/m_wst_b999.mod")
        again = ddodye.dye_port(root, look, ddodye.parse_spec("#406080"), ddodye.changed_since(root, before),
                                self.g, self.i)
        self.assertTrue(red_copy and red_copy <= set(again.removed))
        self.assertTrue(all(p.is_file() for p in again.written))
        self.assertTrue(all(not p.is_file() for p in red_copy))

    def test_refusals(self):
        with self.assertRaisesRegex(RiftError, "Dark Arisen mod"):
            ddodye.port_plan("obj/ab/ab219999/model/ab219999_00.mod", "red", self.games["ddda"], self.idxs["ddda"],
                             "ddo")
        with self.assertRaisesRegex(RiftError, "model-only"):
            ddodye.port_plan("obj/ab/ab219999/model/ab219999_00.mod", "red", self.g, self.i, "ddda", True)
        with self.assertRaisesRegex(RiftError, "goes with a model"):
            ddodye.port_plan(df.ALBEDO.replace("\\", "/") + ".tex", "red", self.g, self.i, "ddda")
        with self.assertRaises(RiftError):
            ddodye.port_plan("obj/ab/ab219999/model/ab219999_00.mod", "purple", self.g, self.i, "ddda")
        spec, look = ddodye.port_plan("obj/ab/ab219999/model/ab219999_00.mod", "red", self.g, self.i, "ddda")
        with self.assertRaisesRegex(RiftError, "no material"):
            ddodye.dye_port(self.base, look, spec, [], self.g, self.i)

    def built(self, root):
        g, i = self.games["ddda"], self.idxs["ddda"]
        p = mod.plan(g, i, [mod.Mod.load(root)])
        mod.check_plan(p)
        return {a: arc.Archive.parse(mod.build_archive(g, a, ch).data) for a, ch in p.archives.items()}


class CliTest(StandIn):
    def run_cli(self, *argv):
        out, opened = io.StringIO(), []

        def index(game, quiet=False):                   # the CLI's own indexes, closed after the command
            opened.append(Index(game))
            return opened[-1]

        with mock.patch.object(cli, "find_game", lambda k=None: self.games[k] if k in self.games else
                               self.games["ddo"]), mock.patch.object(cli, "_index", index), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(list(argv))
        for i in opened:
            i.close()
        return code, out.getvalue()

    def test_ddo_dye(self):
        code, text = self.run_cli("ddo", "dye", "Test Plate")
        self.assertEqual(code, 0)
        self.assertIn("10 red dye", text)
        folder = self.base / "cli-maps"
        code, text = self.run_cli("ddo", "dye", "900", "--colour", "#4060a0", "--out", str(folder), "--as", "png")
        self.assertEqual(code, 0)
        self.assertEqual([p.suffix for p in folder.iterdir()], [".png"])
        code, _ = self.run_cli("ddo", "dye")
        self.assertNotEqual(code, 0)

    def test_port_dye(self):
        root = mod.Mod.create(self.base / "mods" / "Cli", "Cli", game="ddda").root
        code, text = self.run_cli("port", "obj/ab/ab219999/model/ab219999_00.mod", "--mod", str(root), "--as",
                                  "model/pl/m/m_wst_b/m_wst_b999/m_wst_b999.mod", "--dye", "red")
        self.assertEqual(code, 0, text)
        self.assertIn("dyed: red", text)
        folder = root / "archives" / "rom/eq/test/m_armor.arc/ddo/obj/ab/ab219999/model"
        self.assertEqual(len([p for p in folder.iterdir() if "_d" in p.name]), 1)


def ddo_found() -> bool:
    from riftstone.game import find_game

    if os.environ.get("RIFTSTONE_SKIP_GAME"):
        return False
    try:
        find_game("ddo")
    except RiftError:
        return False
    return helpers.ddo_key_present()


@unittest.skipUnless(ddo_found(), "Dragon's Dogma Online not found")
class ClientTest(unittest.TestCase):
    """One real armour: the tables read byte-exact, and its colour maps bake."""

    def test_real_item(self):
        from riftstone.game import find_game

        g = find_game("ddo")
        idx = Index(g)
        try:
            idx.refresh()          # reads only archives that changed since the index was last built
            raw = ddodye._client(g, idx, ddodye.ITEM_LIST, ddodye.ITL_TYPE)
            self.assertEqual(ddodye.build_itemlist(ddodye.read_itemlist(raw)), raw)
            raw = ddodye._client(g, idx, ddodye.RES_TABLE, ddodye.WRT_TYPE)
            self.assertEqual(ddodye.build_restable(*ddodye.read_restable(raw)), raw)
            t = ddodye.resolve(g, idx, "obj\\ab\\ab210004\\model\\ab210004_00")
            look = ddodye.load_look(g, idx, t)
            self.assertEqual(len(look.masked), 3)
            self.assertIn(10, look.montage.used())
            baked, _ = ddodye.bake_look(g, idx, look, ddodye.parse_spec("red"))
            for b in baked:
                src = tex.parse(ddodye._client(g, idx, b.albedo.encode("latin-1"), TEX))
                out = tex.parse(b.data)
                self.assertGreater(b.changed, 0)
                self.assertEqual((out.version, out.fmt, out.width, out.height, out.mip_count),
                                 (tex.VERSION, src.fmt, src.width, src.height, src.mip_count))
                self.assertEqual(texcodec.decode(out, 0)[2][3::4], texcodec.decode(src, 0)[2][3::4])
        finally:
            idx.close()


if __name__ == "__main__":
    unittest.main()

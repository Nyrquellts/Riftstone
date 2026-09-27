"""Effect formats: rEffectProvider (.epv), rEffectList (.efl), rEffect2D (.e2d), rEffectStrip (.efs)."""
import os
import re
import struct
import unittest

import helpers  # noqa: F401 (sys.path)
from riftstone import corpus, effect, effect_e2d, effect_efl, effect_efs, typemap
from riftstone.errors import FormatError, ParamError, RiftError


def f(x):
    return struct.unpack("<I", struct.pack("<f", x))[0]


def game(which):
    """The installed game for corpus tests, or None (tests skip)."""
    from riftstone.game import find_game
    if os.environ.get("RIFTSTONE_SKIP_GAME"):
        return None
    if which == "ddo" and not helpers.ddo_key_present():
        return None
    try:
        return find_game(which)
    except RiftError:
        return None


# -- .epv --------------------------------------------------------------------------------------------
def _defaults(schema):
    out = {}
    for name, t in schema:
        out[name] = {"u32": 1, "x32": 0xFFFFFFFF, "s32": -1, "f32": f(1.0), "v3": [f(0.0), f(-2.0), f(3.5)], "u8": 1,
                     "u32list": [0xFFFFFFFF], "names8": ["effect\\efl\\cm\\l_cm000_40", "effect\\efl\\x"] + [""] * 6,
                     "name": "", "lod3": None}[t]
        if t == "lod3":
            out[name] = [dict.fromkeys((n for n, _ in effect._LOD), 2) for _ in range(3)]
    return out


def epv_sample(version):
    elem = _defaults(effect.ELEMENT[version])
    other = dict(elem, mJointNo=12, mpEffectList=["effect\\efl\\wp\\l_arr005_00"] + [""] * 7)
    mot = dict(_defaults(effect.MOTSYNC[version]), mMotionNo=35, mEfcIndexNo=1, mEfcElementNo=0,
               mStartFrame=f(26.0), mEndFrame=f(0.0))
    event = dict.fromkeys(("mEventWorkNo", "mEfcIndexNo", "mEfcElementNo"), 0)
    return effect.Epv(version, [[elem], [other, elem]], [mot], [event])


class EpvTest(unittest.TestCase):
    def test_round_trip_both_versions(self):
        for version in effect.VERSIONS:
            raw = effect.build(epv_sample(version))
            self.assertEqual(raw[:8], b"epv\0" + struct.pack("<I", version))
            e = effect.parse(raw)
            self.assertEqual(effect.build(e), raw)
            self.assertEqual(effect.yaml_to_bytes(effect.to_yaml(e, "effect\\epv\\x")), raw)

    def test_layout_is_the_loaders(self):
        # DDDA element: 9 paths, 2 + 43 words of EffectParam, 3 x 8 LOD words; DDO: 1 word first, no LOD block
        e = effect.Epv(0, [[_defaults(effect.ELEMENT[0])]])
        names = len("effect\\efl\\cm\\l_cm000_40") + 1 + len("effect\\efl\\x") + 1 + 7
        self.assertEqual(len(effect.build(e)), 12 + 4 + names + 4 * (2 + 43 + 24) + 8)
        d = effect.Epv(22, [[_defaults(effect.ELEMENT[22])]])
        self.assertEqual(len(effect.build(d)), 12 + 4 + 4 + names + 4 * (2 + 4 + 12 + 6 + 5 + 3 + 4 + 3 + 4) +
                         (4 + 4) + 4 * 3 + 4 + 1 + 4 + 12 + 1 + 4 + 8)

    def test_yaml_edit(self):
        text = effect.to_yaml(effect.parse(effect.build(epv_sample(0))))
        self.assertIn('mpEffectList: ["effect\\\\efl\\\\cm\\\\l_cm000_40", "effect\\\\efl\\\\x"]', text)
        self.assertIn("mJointNo: 12", text)
        e = effect.parse(effect.yaml_to_bytes(text.replace("mJointNo: 12", "mJointNo: 7")
                                              .replace("effect\\\\efl\\\\wp\\\\l_arr005_00", "effect\\\\efl\\\\my_fx")))
        self.assertEqual(e.indices[1][0]["mJointNo"], 7)
        self.assertEqual(e.indices[1][0]["mpEffectList"][0], "effect\\efl\\my_fx")
        self.assertEqual(len(e.indices[1][0]["mpEffectList"]), 8)

    def test_enum_comments(self):
        e = epv_sample(0)
        e.indices[0][0].update(mEffectType=5, mSetType=2, mE2DType=3)
        text = effect.to_yaml(e)
        self.assertIn("mEffectType: 5  # EFC_TYPE_MOTION", text)
        self.assertIn("mSetType: 2  # EFC_SET_TYPE_WORLD", text)
        self.assertIn("mE2DType: 3  # E2D_TYPE_PL_BLIND", text)
        self.assertEqual(effect.yaml_to_bytes(text), effect.build(e))
        self.assertNotIn("# EFC_TYPE", effect.to_yaml(epv_sample(22)))      # DDO's enums run past DDDA's

    def test_links_and_info(self):
        e = epv_sample(0)
        row = effect.links(e)[0]
        self.assertEqual((row["motion"], row["index"], row["element"], row["joint"]), (35, 1, 0, 12))
        self.assertEqual(row["effects"], ["effect\\efl\\wp\\l_arr005_00"])
        self.assertIn("motion 35", effect.info(e))
        self.assertEqual(effect.paths(e)[:2], ["effect\\efl\\cm\\l_cm000_40", "effect\\efl\\x"])

    def test_refusals(self):
        raw = effect.build(epv_sample(22))
        for bad in (b"EPV\0" + raw[4:], raw[:4] + struct.pack("<I", 5) + raw[8:], raw[:-1], raw + b"\0",
                    raw[:8] + struct.pack("<I", 0x7FFFFFFF) + raw[12:], raw[:6], b"",
                    raw[:8] + struct.pack("<I", 1) + struct.pack("<I", 1) + b"\0" * 4 + b"abc"):
            with self.assertRaises(FormatError):
                effect.parse(bad)
        e = epv_sample(0)
        e.indices[0][0]["mpEffectList"] = ["x"] * 9
        with self.assertRaises(FormatError):
            effect.build(e)
        e = epv_sample(0)
        e.indices[0][0]["mJointNo"] = 1 << 40
        with self.assertRaises(FormatError):
            effect.build(e)
        with self.assertRaises(FormatError):
            effect.build(effect.Epv(3))

    def test_bad_yaml(self):
        text = effect.to_yaml(effect.parse(effect.build(epv_sample(0))))
        for bad in (text.replace("riftstone: epv/1", "riftstone: gpl/1"), text.replace("version: 0", "version: 7"),
                    text.replace("mJointNo: 12", "mJointNo: [1]"), text.replace("mJointNo: 12", "mJointNo: x"),
                    text.replace("mScale: 1.0", "mScale: big"),
                    text.replace("        mOrder: 1\n", "        mOrder: 1\n        mNew: 2\n", 1),
                    text.replace('["effect\\\\efl\\\\wp\\\\l_arr005_00"]', '["a","b","c","d","e","f","g","h","i"]'),
                    re.sub(r"\n        mEndType:[^\n]*", "", text, count=1),
                    text.replace("mGroupFlag: 0xffffffff", "mGroupFlag: 0x1ffffffff", 1),
                    text.replace('mpEffect2D: ""', 'mpEffect2D: "日本"', 1)):
            with self.assertRaises(ParamError):
                effect.yaml_to_bytes(bad)

    def test_yaml_sections_are_not_dropped(self):
        # the reader never checked the top-level or index keys and took a missing section as empty:
        # 'motSync:' built with no motion-sync entries, 'indexes:' with no indices
        text = effect.to_yaml(effect.parse(effect.build(epv_sample(0))))
        for bad in (text.replace("motsync:", "motSync:"), text.replace("indices:", "indexes:"),
                    text.replace("  - elements:", "  - element:", 1),
                    text.replace("  - elements:", "  - count: 1\n    elements:", 1),
                    text[:text.index("events:")],                                  # a section left out
                    text.replace("motsync:\n", "motsync: 3\nx:\n")):
            self.assertNotEqual(bad, text)
            with self.assertRaises(ParamError):
                effect.from_yaml(bad)

    def test_yaml_name_stays_in_its_comment(self):
        # the resource name went into the header comment as it was: a newline in it began a YAML line
        # of its own (here a second 'riftstone:' key, which also fooled params' tag detection)
        from riftstone import params
        for mod, m in ((effect, epv_sample(22)), (effect_efl, efl_sample()), (effect_e2d, e2d_sample())):
            raw = mod.build(m)
            y = mod.to_yaml(m, "x\nriftstone: xfs/1\r\t\"#")
            self.assertEqual(mod.yaml_to_bytes(y), raw, mod.__name__)
            self.assertEqual(params.yaml_to_resource(y), raw, mod.__name__)

    def test_yaml_long_hex_number(self):
        # base 16 has no digit limit, but 3,572 hex digits are over 4,300 decimal ones: the range message
        # printed the number and leaked int -> str's ValueError
        big = "0x" + "f" * 3572
        text = effect.to_yaml(effect.parse(effect.build(epv_sample(0))))
        for bad in (text.replace("version: 0", "version: " + big), text.replace("mJointNo: 12", "mJointNo: -" + big)):
            self.assertNotEqual(bad, text)
            with self.assertRaises(ParamError) as cm:
                effect.from_yaml(bad)
            self.assertLess(len(str(cm.exception)), 200)


# -- .efl --------------------------------------------------------------------------------------------
def _head(schema, **values):
    v = effect_efl.decode(schema, bytes(effect_efl.schema_size(schema)))
    v.update(values)
    return effect_efl.encode(schema, v)


def efl_sample():
    E = effect_efl
    gen = _head(E.GENERATOR, ParticleNum=4, ParticleScale={"s": f(1.0), "r": f(0.5)}, RandomNo=[-1] * 8)
    prim = E.COMMON + E.DRAW + E.PAT + E.PRIM
    par = _head(prim, DrawMode=1, BlendState=1, Color=[0x64C8FE9F, 0xFFFFFFFF],
                TexturePath=["effect\\tex\\cm\\t_a_GM", "", ""], AnimPath="effect\\tex\\cm\\t_a") + bytes(range(48))
    life = struct.pack("<4I", 0, 0, 10, 1)
    coll = bytearray(0x20)
    struct.pack_into("<H", coll, 0x1E, 0x20)                             # collision block -> its string
    move = struct.pack("<HHHHII", 0, 0, 0, 0x10, 0, 0) + bytes(coll) + b"effect\\efl\\hit\0\0"
    joint = bytes(range(0x70))
    unit = bytearray(0x10)
    struct.pack_into("<H", unit, 0xA, 0x10)
    unit += b"effect\\efl\\next\0"
    regions = [E.Region(x) for x in (gen, par, life, move, joint, bytes(unit))]
    ent = E.Entry(joint=0, generator=0, particle_type=0, particle=1, life_type=1, life_unk=1, life=2, move_type=1,
                  move_unk=0, move=3)
    return E.Efl(E.VERSION_DDDA, f(30.0), 0x100, (0, 0), [ent], [4], [None, None, None, 5], bytes(12), regions)


class EflTest(unittest.TestCase):
    def test_round_trip(self):
        raw = effect_efl.build(efl_sample())
        self.assertEqual(raw[:8], b"EFL\0" + struct.pack("<I", 0x20110318))
        self.assertEqual(struct.unpack_from("<I", raw, 8)[0], len(raw) - 0x30)
        e = effect_efl.parse(raw)
        self.assertEqual(effect_efl.build(e), raw)
        self.assertEqual(len(e.regions), 6)
        self.assertEqual(e.regions[1].roles, [("particle", 0)])
        self.assertEqual(effect_efl.yaml_to_bytes(effect_efl.to_yaml(e, "effect\\efl\\x")), raw)
        e.version = effect_efl.VERSION_DDO
        raw2 = effect_efl.build(e)
        self.assertEqual(effect_efl.build(effect_efl.parse(raw2)), raw2)

    def test_tables(self):
        raw = effect_efl.build(efl_sample())
        buf = raw[0x30:]
        w = struct.unpack_from("<4I", buf, 0)
        self.assertEqual(w[0], 0x20 << 8 | 0)                  # generator right after 16 + 16 bytes of tables
        self.assertEqual(w[1] & 0xFF, 0)                       # Billboard
        self.assertEqual(w[2] & 0xFF, 0x11)                    # life type 1 in bits 4-7, LifeUnk 1 in bits 0-3
        self.assertEqual(struct.unpack_from("<I", buf, 16)[0], 0x20 + 0xC0 + 0x1A0 + 0x10 + 0x40)   # joint region

    def test_named_fields_and_resources(self):
        e = effect_efl.parse(effect_efl.build(efl_sample()))
        g = effect_efl.region_fields(e, 0)
        self.assertEqual((g["ParticleNum"], g["ParticleScale"]["r"]), (4, f(0.5)))
        p = effect_efl.region_fields(e, 1)
        self.assertEqual(p["TexturePath"][0], "effect\\tex\\cm\\t_a_GM")
        self.assertEqual(p["Color"][0], 0x64C8FE9F)
        self.assertIsNone(effect_efl.region_fields(e, 4))       # EFL_JOINT has no member names
        res = {(slot, cls, path) for _, slot, cls, path in effect_efl.resources(e)}
        self.assertIn(("TexturePath[0]", "rTexture", "effect\\tex\\cm\\t_a_GM"), res)
        self.assertIn(("AnimPath", "rEffectAnim", "effect\\tex\\cm\\t_a"), res)
        self.assertIn(("CollEffect@0x1e", "rEffectList", "effect\\efl\\hit"), res)
        self.assertIn(("SerialEffect", "rEffectList", "effect\\efl\\next"), res)
        cov = effect_efl.coverage(e)
        self.assertEqual(cov["named"], 0x30 + 0x20 + 0xC0 + 0x170)
        self.assertIn("Billboard", effect_efl.info(e))

    def test_edit_texture(self):
        e = effect_efl.parse(effect_efl.build(efl_sample()))
        effect_efl.set_region_fields(e, 1, {"TexturePath": ["effect\\tex\\my\\t_b", "", ""]})
        e2 = effect_efl.parse(effect_efl.build(e))
        self.assertEqual(effect_efl.region_fields(e2, 1)["TexturePath"][0], "effect\\tex\\my\\t_b")
        self.assertEqual(e2.regions[1].data[0x170:], bytes(range(48)))           # the body is kept
        text = effect_efl.to_yaml(e2).replace("effect\\\\tex\\\\my\\\\t_b", "effect\\\\tex\\\\my\\\\t_c")
        e3 = effect_efl.parse(effect_efl.yaml_to_bytes(text))
        self.assertEqual(effect_efl.region_fields(e3, 1)["TexturePath"][0], "effect\\tex\\my\\t_c")
        with self.assertRaises(FormatError):
            effect_efl.set_region_fields(e, 1, {"TexturePath": ["x" * 64, "", ""]})
        with self.assertRaises(FormatError):
            effect_efl.set_region_fields(e, 1, {"DrawMode": 256})

    def test_refusals(self):
        raw = effect_efl.build(efl_sample())
        bad_list = [b"EFX\0" + raw[4:], raw[:4] + struct.pack("<I", 0x20100101) + raw[8:], raw[:-1], raw + b"\0",
                    raw[:0x20],
                    raw[:0x10] + struct.pack("<H", 0x3000) + raw[0x12:],             # tables past the end
                    raw[:0x30] + struct.pack("<I", 0x100000 << 8) + raw[0x34:],     # offset outside
                    raw[:0x30] + struct.pack("<I", 8 << 8) + raw[0x34:]]             # offset inside the tables
        for bad in bad_list:
            with self.assertRaises(FormatError):
                effect_efl.parse(bad)
        e = efl_sample()
        e.entries[0].particle = 9
        with self.assertRaises(FormatError):
            effect_efl.build(e)
        e = efl_sample()
        e.joint_pad = b""
        with self.assertRaises(FormatError):
            effect_efl.build(e)
        e = efl_sample()
        e.entries[0].life_type = 16
        with self.assertRaises(FormatError):
            effect_efl.build(e)

    def test_bad_yaml(self):
        text = effect_efl.to_yaml(effect_efl.parse(effect_efl.build(efl_sample())))
        for bad in (text.replace("riftstone: efl/1", "riftstone: epv/1"),
                    text.replace("version: 0x20110318", "version: 1"),
                    text.replace("particle: 1,", "particle: 99,"), text.replace("ParticleNum: 4", "ParticleNum: 70000"),
                    text.replace("DrawMode: 1", "DrawMode: x"), text.replace("      - 000102", "      - 0g0102"),
                    text.replace("      ParticleNum: 4\n", "")):
            with self.assertRaises(ParamError):
                effect_efl.yaml_to_bytes(bad)

    def test_yaml_fields_are_not_dropped(self):
        # units / joints / regions were read as '.items if a list else []' and other top-level keys were
        # ignored: 'joints: 4' or 'joint: [4]' built with no joints. Keys a unit, region, head or range does
        # not have were ignored too; a region's number was never checked. (.e2d reads the same way.)
        text = effect_efl.to_yaml(effect_efl.parse(effect_efl.build(efl_sample())))
        tex = '"effect\\\\tex\\\\cm\\\\t_a_GM"'
        raw_tex = '{hex: "' + (b"effect\\tex\\cm\\t_a_GM".ljust(64, b"\0")).hex() + '"'
        self.assertEqual(effect_efl.yaml_to_bytes(text.replace(tex, raw_tex + "}")), effect_efl.build(efl_sample()))
        cases = [(effect_efl, text, a, b) for a, b in (
            ("joints: [4]", "joints: 4"), ("joints: [4]", "joint: [4]"), ("units:\n", "units: 4\nx:\n"),
            ("regions:\n", "units2: []\nregions:\n"), ("move_unk: 0, move: 3}", "move_unk: 0, move: 3, moves: 1}"),
            ("param: 5}", "param: 5, params: 6}"), ("  - region: 2  #", "  - region: 7  #"),
            ("  - region: 2  # life Frame, 16 bytes\n    tail:", "  - region: 2\n    tails: []\n    tail:"),
            ("      ParticleNum: 4\n", "      ParticleNum: 4\n      ParticleNumber: 5\n"),
            ("ParticleScale: {s: 1.0, r: 0.5}", "ParticleScale: {s: 1.0, r: 0.5, t: 2.0}"),
            (tex, raw_tex + ", text: x}"))]
        t2 = effect_e2d.to_yaml(e2d_sample())
        cases += [(effect_e2d, t2, a, b) for a, b in (
            ("units:\n", "units: 4\nx:\n"), ("regions:\n", "regionz:\n"), ("  - region: 1  #", "  - region: 0  #"),
            ("move_type: 2, move: 3}", "move_type: 2, move: 3, moves: 1}"),
            ("  RTTexturePath:\n", "  RTTexturePaths: []\n  RTTexturePath:\n"))]
        for mod, good, a, b in cases:
            bad = good.replace(a, b, 1)
            self.assertNotEqual(bad, good, a)
            with self.assertRaises(ParamError, msg=b):
                mod.from_yaml(bad)

    def test_region_at_offset_zero_refused(self):
        # with no units and no joints the tables take no bytes, so region 0 starts at offset 0, which the
        # format reads as none: unit [0, None, None, None] built, and parsed back as [None] * 4
        E = effect_efl
        e = E.Efl(E.VERSION_DDDA, f(30.0), 0x100, (0, 0), [], [], [0, None, None, None], b"",
                  [E.Region(bytes(0xC0))])
        with self.assertRaises(FormatError):
            E.build(e)
        e.regions.insert(0, E.Region(bytes(16)))                        # another region first: it has a place
        e.unit = [1, None, None, None]
        raw = E.build(e)
        self.assertEqual(E.parse(raw).unit, [1, None, None, None])
        text = ("riftstone: efl/1\nversion: 0x20110318\nmBaseFps: 30.0\nflags: 0x100\nunit: {generator: 0, "
                "move: null, joint: null, param: null}\nunits: []\njoints: []\nregions:\n  - region: 0\n    tail: ["
                + "00" * 0xC0 + "]\n")
        with self.assertRaises(ParamError):
            E.from_yaml(text)

    def test_yaml_long_hex_number(self):
        # base 16 has no digit limit, but 3,572 hex digits are over 4,300 decimal ones: the range message
        # printed the number and leaked int -> str's ValueError (.e2d reads its numbers the same way)
        big = "0x" + "f" * 3572
        text = effect_efl.to_yaml(effect_efl.parse(effect_efl.build(efl_sample())))
        t2 = effect_e2d.to_yaml(e2d_sample())
        for mod, bad in ((effect_efl, text.replace("particle: 1,", "particle: " + big + ",")),
                         (effect_efl, text.replace("ParticleNum: 4", "ParticleNum: " + big)),
                         (effect_e2d, t2.replace("version: 0x20110314", "version: " + big))):
            self.assertNotIn(bad, (text, t2))
            with self.assertRaises(ParamError) as cm:
                mod.from_yaml(bad)
            self.assertLess(len(str(cm.exception)), 200)

    def test_kind_names(self):
        self.assertEqual(effect_efl.particle_kind(5), "Model")
        self.assertEqual(effect_efl.move_kind(3), "PathStrip")
        self.assertEqual(effect_efl.life_kind(5), "Hideframe")
        self.assertIn("UNKNOWN", effect_efl.particle_kind(25))


# -- .e2d / .efs -------------------------------------------------------------------------------------
def e2d_sample():
    D = effect_e2d
    prefix = bytearray(D.TABLE)
    prefix[0x40:0x40 + 10] = b"effect\\rtt"
    par = _head(D._PARTICLE[0], TexturePath=["effect\\tex\\t_x", "", ""], AnimPath="") + bytes(64)
    regions = [effect_efl.Region(x) for x in (bytes(0x70), par, bytes(0x10), bytes(0x30))]
    return D.E2d(D.VERSION_DDDA, f(30.0), (0, 0, 0), bytes(prefix),
                 [D.Entry(0, 0, 0, 1, 1, 2, 2, 3), D.Entry(1, 0, 0, 1, 0, None, 0, None)], regions)


class E2dEfsTest(unittest.TestCase):
    def test_e2d_round_trip(self):
        raw = effect_e2d.build(e2d_sample())
        self.assertEqual(raw[:4], b"E2D\0")
        e = effect_e2d.parse(raw)
        self.assertEqual(effect_e2d.build(e), raw)
        self.assertEqual(effect_e2d.yaml_to_bytes(effect_e2d.to_yaml(e, "x")), raw)
        res = {(cls, p) for _, _, cls, p in effect_e2d.resources(e)}
        self.assertEqual(res, {("rRenderTargetTexture", "effect\\rtt"), ("rTexture", "effect\\tex\\t_x")})
        self.assertIn("Sprite", effect_e2d.info(e))
        for bad in (raw[:-1], b"E2X\0" + raw[4:], raw[:0xC] + struct.pack("<I", 0x10000) + raw[0x10:]):
            with self.assertRaises(FormatError):
                effect_e2d.parse(bad)

    def test_efs_round_trip(self):
        parts = [effect_efs.Part([bytes(range(32))] * 3, [bytes(8)] * 2), effect_efs.Part([bytes(32)], [])]
        s = effect_efs.Efs(6, 0, parts)
        raw = effect_efs.build(s)
        self.assertEqual(struct.unpack_from("<4I", raw, 0x10), (2, 6, 4, 2))
        self.assertEqual(struct.unpack_from("<2I", raw, 0x20), (8, 8 + 8 + 3 * 32 + 2 * 8))
        self.assertEqual(effect_efs.build(effect_efs.parse(raw)), raw)
        self.assertIn("2 part(s)", effect_efs.info(effect_efs.parse(raw)))
        for bad in (raw[:-1], raw + b"\0", raw[:0x18] + struct.pack("<I", 9) + raw[0x1C:], b"EFS\0" + bytes(8),
                    raw[:0x20] + struct.pack("<I", 12) + raw[0x24:]):
            with self.assertRaises(FormatError):
                effect_efs.parse(bad)
        with self.assertRaises(FormatError):
            effect_efs.build(effect_efs.Efs(0, 0, [effect_efs.Part([b"x"])]))


# -- the games --------------------------------------------------------------------------------------
MODULES = {"epv": effect, "efl": effect_efl, "e2d": effect_e2d, "efs": effect_efs}
YAML_EVERY = 20       # the YAML round trip on every 20th file (check_corpus runs it on all)


class CorpusTest(unittest.TestCase):
    def _corpus(self, which):
        g = game(which)
        if g is None:
            self.skipTest(f"no {which} install")
        counts = {}
        for ext, mod in MODULES.items():
            n = ok = 0
            for r in corpus.resources(g, [typemap.BY_EXT[ext]]):
                x = mod.parse(r.data)
                self.assertEqual(mod.build(x), r.data, r.label)
                if hasattr(mod, "to_yaml") and n % YAML_EVERY == 0:
                    self.assertEqual(mod.yaml_to_bytes(mod.to_yaml(x, r.name.decode("latin-1"))), r.data, r.label)
                n += 1
                ok += 1
            counts[ext] = ok
        return counts

    def test_ddda(self):
        counts = self._corpus("ddda")
        self.assertEqual(counts, {"epv": 599, "efl": 4514, "e2d": 156, "efs": 25})

    def test_ddo(self):
        counts = self._corpus("ddo")
        self.assertEqual(counts, {"epv": 1660, "efl": 5754, "e2d": 52, "efs": 6})


if __name__ == "__main__":
    unittest.main()

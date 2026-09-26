import struct
import unittest

import helpers
from riftstone import corpus, inspect, params, prp, typemap, xfs
from riftstone.errors import FormatError


def sample_prp() -> bytes:
    return prp.build(helpers.sample_xfs())


class PrpTest(unittest.TestCase):
    def test_round_trip(self):
        raw = sample_prp()
        self.assertEqual(raw[:4], b"PRPZ")
        self.assertEqual(prp.build(prp.parse(raw)), raw)

    def test_header_is_derived_from_the_body(self):
        # the wrapper carries no data of its own: marker is constant, class == XFS root class
        doc = helpers.sample_xfs()
        _, marker, dti = struct.unpack_from("<4sII", prp.build(doc), 0)
        self.assertEqual(marker, prp.MARKER)
        self.assertEqual(dti, doc.root_class.type_id)

    def test_yaml_round_trip_via_dispatch(self):
        raw = sample_prp()
        y = params.resource_to_yaml(raw, "em9999", typemap.BY_EXT["prp"])
        self.assertIsNotNone(y)
        self.assertIn("riftstone: prp/1", y)
        self.assertEqual(params.yaml_to_resource(y), raw)

    def test_refusals(self):
        raw = sample_prp()
        with self.assertRaises(FormatError):
            prp.parse(b"XFS\0" + raw[4:])                       # wrong magic
        with self.assertRaises(FormatError):
            prp.parse(raw[:8])                                  # truncated header
        bad_marker = raw[:4] + struct.pack("<I", 0xDEADBEEF) + raw[8:]
        with self.assertRaises(FormatError):
            prp.parse(bad_marker)                               # wrong marker
        wrong_class = raw[:8] + struct.pack("<I", 0x12345678) + raw[12:]
        with self.assertRaises(FormatError):
            prp.parse(wrong_class)                              # class hash != XFS root class

    def test_inspect_shows_class(self):
        rep = inspect.describe(sample_prp(), typemap.BY_EXT["prp"])
        out = rep.text("x")
        self.assertIn("rPropParam", out)
        self.assertTrue(rep.editable)

    def test_yaml_writes_the_english_name_beside_each_known_field(self):
        cls = xfs.ClassDef(typemap.jamcrc("rTestPrp"), 0x10, (
            helpers.prop("攻撃力", "f32"), helpers.prop("耐久聖", "f32", 0x20), helpers.prop("mOther", "u32")))
        raw = prp.build(xfs.Xfs(4, [cls], xfs.Obj(0, [[250.0], [1.0, 2.0], [7]])))
        y = params.resource_to_yaml(raw, "charparam\\em\\em9999_cmn", typemap.BY_EXT["prp"])
        self.assertIn("攻撃力: 250.0  # Attack\n", y)
        self.assertIn("耐久聖: [1.0, 2.0]  # Holy endurance\n", y)
        self.assertIn("mOther: 7\n", y)                         # no label, no comment
        self.assertEqual(params.yaml_to_resource(y), raw)       # the labels are comments: the bytes are the same
        edited = y.replace("攻撃力: 250.0", "攻撃力: 400.0")
        self.assertEqual(prp.parse(params.yaml_to_resource(edited)).root.fields[0], [400.0])

    def test_gloss_is_read_only_reference(self):
        # every glossed key is a plausible parameter name string, values are English
        self.assertIn("スケール値", prp.GLOSS)
        self.assertEqual(prp.GLOSS["人間敵 HP"], "Human-enemy HP")

    def test_gloss_names_the_stat_fields(self):
        # the fields a monster rebalance touches, common class first, then single enemies' own
        for jp, en in (("攻撃力", "Attack"), ("魔法防御力", "Magick defense"), ("のけぞりガード", "Flinch guard"),
                       ("ぶっとびガード", "Knockdown guard"), ("体重", "Weight"), ("経験値", "EXP"),
                       ("暴走時の攻撃力係数", "Berserk attack multiplier"),
                       ("宿営地用魔法防御力", "Magick defense (Encampment)")):
            self.assertEqual(prp.GLOSS[jp], en)
        self.assertTrue(all(v.isascii() and v for v in prp.GLOSS.values()))

    @unittest.skipUnless(helpers.game_root(), "no game")
    def test_every_gloss_names_a_real_field(self):
        # a gloss for a name no vanilla .prp carries would label nothing: each key must occur
        from riftstone.game import find_game
        names = set()
        for r in corpus.resources(find_game(), [typemap.BY_EXT["prp"]]):
            for c in prp.parse(r.data).classes:
                names.update(p.name for p in c.props)
        self.assertEqual(sorted(set(prp.GLOSS) - names), [])

    def test_non_canonical_yaml_gives_canonical_bytes(self):
        # regression for a fuzz finding: a non-canonical .prp (here, a dropped unused class) still
        # round-trips through parse->build byte-exact, but the YAML path canonicalises it -- the same
        # contract every XFS resource has.  Vanilla files are already canonical (test_corpus proves it).
        doc = helpers.sample_xfs()
        unused = xfs.ClassDef(typemap.jamcrc("rTestUnused"), 0x10, (helpers.prop("mX", "u32"),))
        raw = prp.build(xfs.Xfs(doc.minor, doc.classes + [unused], doc.root))
        parsed = prp.parse(raw)
        self.assertFalse(xfs.is_canonical(parsed))
        self.assertEqual(prp.build(parsed), raw)                 # parse -> build stays byte-exact
        y = params.resource_to_yaml(raw, "x", typemap.BY_EXT["prp"])
        self.assertEqual(params.yaml_to_resource(y), prp.build(xfs.canonical(parsed)))

    @unittest.skipUnless(helpers.game_root(), "no game")
    def test_corpus_byte_exact(self):
        from riftstone.game import find_game
        g = find_game()
        n = ok = yok = 0
        for r in corpus.resources(g, [typemap.BY_EXT["prp"]]):
            n += 1
            doc = prp.parse(r.data)
            if prp.build(doc) == r.data:
                ok += 1
            y = params.resource_to_yaml(r.data, r.label.split(":")[-1], typemap.BY_EXT["prp"])
            if params.yaml_to_resource(y) == r.data:
                yok += 1
        self.assertGreater(n, 0)
        self.assertEqual(ok, n)
        self.assertEqual(yok, n)


if __name__ == "__main__":
    unittest.main()

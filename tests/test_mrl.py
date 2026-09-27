import struct
import time
import unittest

import helpers  # noqa: F401 (sys.path)
from riftstone import inspect, mrl, typemap
from riftstone.errors import FormatError


def make(mats, texs, tail=b"\xcd\xcd\xcd\xcd"):
    textures = [mrl.Texture(mrl.TEX_TYPE_ID, 0, 0, (n.encode() + b"\0").ljust(mrl.NAME_LEN, b"\xcd"))
                for n in texs]
    materials = [mrl.Material(sh, mh, list(range(13))) for sh, mh in mats]
    return mrl.Mrl(mrl.VERSION, 0xB46006D5, textures, materials, tail)


class MrlTest(unittest.TestCase):
    def test_round_trip(self):
        for m in (make([], []),
                  make([(0x7D2B31B3, 0xAABBCCDD)], ["tex/a"]),
                  make([(1, 2), (1, 3), (4, 5)], ["x", "y"], tail=bytes(range(64)))):
            raw = mrl.build(m)
            self.assertEqual(raw[:4], b"MRL\0")
            self.assertEqual(mrl.build(mrl.parse(raw)), raw)

    def test_reads_shaders_and_textures(self):
        m = mrl.parse(mrl.build(make([(0x1111, 0x2222)], ["tex/wolf_BM", "tex/wolf_NM"])))
        self.assertEqual([t.name for t in m.textures], ["tex/wolf_BM", "tex/wolf_NM"])
        self.assertEqual(m.materials[0].shader, 0x1111)

    def test_retex_is_byte_stable(self):
        m = make([(1, 2)], ["tex/old"])
        raw = mrl.build(m)
        m2 = mrl.parse(raw)
        m2.textures[0].set_name("tex/new")
        back = mrl.parse(mrl.build(m2))
        self.assertEqual(back.textures[0].name, "tex/new")
        # only the name changed: materials and the opaque tail are preserved
        self.assertEqual(back.materials[0].shader, m2.materials[0].shader)
        self.assertEqual(back.tail, m2.tail)

    def test_refusals(self):
        raw = mrl.build(make([(1, 2)], ["a"]))
        with self.assertRaises(FormatError):
            mrl.parse(b"xxx\0" + raw[4:])                                  # wrong magic
        with self.assertRaises(FormatError):
            mrl.parse(struct.pack("<IIIIIII", mrl.MAGIC, 0x99, 0, 0, 0, 0x1c, 0x1c))  # wrong version
        with self.assertRaises(FormatError):
            mrl.parse(raw[:10])                                            # truncated
        # a name that will not fit its fixed field
        m = mrl.parse(raw)
        with self.assertRaises(FormatError):
            m.textures[0].set_name("x" * 64)

    def test_a_name_the_field_cannot_hold_is_refused(self):
        # set_name encoded as Latin-1 unchecked: "tex\wp\€" raised UnicodeEncodeError (riftstone mrl retex),
        # and "a\0b" was stored, then read back as "a"
        m = mrl.parse(mrl.build(make([(1, 2)], ["a"])))
        for bad in ("tex\\wp\\€", "a\0b", "テスト"):
            with self.subTest(name=bad), self.assertRaises(FormatError):
                m.textures[0].set_name(bad)
        self.assertEqual(m.textures[0].name, "a")
        m.textures[0].set_name("tex\\wp\\é")               # names are read as Latin-1
        self.assertEqual(mrl.parse(mrl.build(m)).textures[0].name, "tex\\wp\\é")

    def test_many_blocks_split_quickly(self):
        # each block's end was found by scanning every block start: quadratic in materials (20,000 took seconds)
        n = 20000
        table_end = mrl._HDR.size + n * mrl.MAT_ENTRY
        base = table_end + -table_end % 16                  # the games' layout: blocks from the next 16 bytes
        mats = [mrl.Material(1, i, [0] * 11 + [base + 16 * i, 0]) for i in range(n)]
        raw = mrl.build(mrl.Mrl(mrl.VERSION, 0, [], mats, bytes(base - table_end + 16 * n)))
        m = mrl.parse(raw)
        t = time.perf_counter()
        parts = mrl.blocks(raw, m)
        self.assertLess(time.perf_counter() - t, 0.5)
        self.assertEqual({(len(cmd), anim) for cmd, anim in parts}, {(16, b"")})
        self.assertEqual(mrl.rebuild(raw), raw)

    def test_inspect(self):
        rep = inspect.describe(mrl.build(make([(0x1234, 0)], ["tex/rock"])), typemap.BY_EXT["mrl"])
        out = rep.text("x")
        self.assertIn("material list", out)
        self.assertIn("tex/rock", out)
        self.assertIn("0x00001234", out)


if __name__ == "__main__":
    unittest.main()

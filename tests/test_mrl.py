import struct
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

    def test_inspect(self):
        rep = inspect.describe(mrl.build(make([(0x1234, 0)], ["tex/rock"])), typemap.BY_EXT["mrl"])
        out = rep.text("x")
        self.assertIn("material list", out)
        self.assertIn("tex/rock", out)
        self.assertIn("0x00001234", out)


if __name__ == "__main__":
    unittest.main()

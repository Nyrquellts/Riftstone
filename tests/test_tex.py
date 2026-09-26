import struct
import unittest

import helpers  # noqa: F401 (sys.path)
from riftstone import inspect, tex, typemap
from riftstone.errors import FormatError


def make(fmt, w, h, mips, depth=1, attr1=0x20000):
    """A texture with a correct, contiguous, largest-first mip layout and deterministic pixels."""
    offs, off, pixels = [], 16 + 4 * mips * depth, bytearray()
    for _ in range(depth):
        for m in range(mips):
            sz = tex._mip_size(w, h, m, fmt)
            offs.append(off)
            off += sz
            pixels += bytes((i * 7 + m) & 0xFF for i in range(sz))
    body = struct.pack("<%dI" % (mips * depth), *offs) + bytes(pixels)
    return tex.Tex(attr1, tex.VERSION, mips, w, h, depth, fmt, 1, body)


class TexTest(unittest.TestCase):
    def test_header_round_trip(self):
        for fmt in (19, 20, 24, 25, 31, 40):
            raw = tex.build(make(fmt, 64, 32, 4))
            self.assertEqual(raw[:4], b"TEX\0")
            self.assertEqual(tex.build(tex.parse(raw)), raw)
            self.assertEqual(struct.unpack_from("<I", raw, 4)[0] & 0xFFF, tex.VERSION)

    def test_dds_round_trip_is_exact(self):
        for fmt in (19, 20, 24, 25, 31, 40):
            raw = tex.build(make(fmt, 128, 64, 5))
            t = tex.parse(raw)
            dds = tex.to_dds(t)
            self.assertEqual(dds[:4], b"DDS ")
            self.assertEqual(tex.build(tex.dds_to_tex(dds, template=t)), raw)

    def test_non_power_of_two_and_single_mip(self):
        for fmt in (20, 24, 40):
            raw = tex.build(make(fmt, 40, 24, 1))
            t = tex.parse(raw)
            self.assertEqual(tex.build(tex.dds_to_tex(tex.to_dds(t), template=t)), raw)

    def test_cube_round_trips_but_refuses_dds(self):
        raw = tex.build(make(20, 16, 16, 4, depth=6, attr1=0x60000))
        t = tex.parse(raw)
        self.assertTrue(t.is_cube)
        self.assertEqual(tex.build(t), raw)          # header round-trip still exact
        with self.assertRaises(FormatError):
            tex.to_dds(t)

    def test_new_texture_from_dds_without_template(self):
        dds = tex.to_dds(make(20, 32, 32, 3))        # DXT1 -> format id 20
        t = tex.dds_to_tex(dds)
        self.assertEqual((t.width, t.height, t.mip_count, t.fmt, t.depth), (32, 32, 3, 20, 1))
        dds5 = tex.to_dds(make(24, 32, 32, 3))       # DXT5 -> 24
        self.assertEqual(tex.dds_to_tex(dds5).fmt, 24)
        ati = tex.to_dds(make(31, 32, 32, 3))        # ATI2 -> 31 (normal map)
        self.assertEqual(tex.dds_to_tex(ati).fmt, 31)

    def test_parse_refusals(self):
        raw = tex.build(make(20, 32, 32, 3))
        with self.assertRaises(FormatError):
            tex.parse(b"xxx\0" + raw[4:])                       # wrong magic
        with self.assertRaises(FormatError):
            tex.parse(struct.pack("<IIII", tex.MAGIC, 0x20000123, 0, 0x101))  # wrong revision
        with self.assertRaises(FormatError):
            tex.parse(raw[:8])                                  # too short for the header
        # zero mip levels
        w1, w2, w3 = struct.unpack_from("<III", raw, 4)
        with self.assertRaises(FormatError):
            tex.parse(struct.pack("<IIII", tex.MAGIC, w1, w2 & ~0x3F, w3))

    def test_dds_refusals(self):
        good = tex.to_dds(make(20, 32, 32, 3))
        with self.assertRaises(FormatError):
            tex.dds_to_tex(b"nope" + bytes(200))               # not a DDS
        with self.assertRaises(FormatError):
            tex.dds_to_tex(good[:64])                           # truncated header
        dx10 = bytearray(good)
        dx10[84:88] = b"DX10"
        dx10[80:84] = struct.pack("<I", tex._DDPF_FOURCC)
        with self.assertRaises(FormatError):
            tex.dds_to_tex(bytes(dx10))                         # DX10 extended header
        with self.assertRaises(FormatError):
            tex.dds_to_tex(good[:200])                          # pixel data too short for the dims
        huge_mip = bytearray(good)
        struct.pack_into("<I", huge_mip, 28, 0xFFFFFFFF)        # 4-billion mip count must not hang
        with self.assertRaises(FormatError):
            tex.dds_to_tex(bytes(huge_mip))
        huge_dim = bytearray(good)
        struct.pack_into("<I", huge_dim, 16, 100000)           # width out of a texture's 13-bit range
        with self.assertRaises(FormatError):
            tex.dds_to_tex(bytes(huge_dim))

    def test_inspect(self):
        rep = inspect.describe(tex.build(make(24, 256, 128, 4)), typemap.BY_EXT["tex"])
        out = rep.text("x")
        self.assertIn("256x128", out)
        self.assertIn("mip", out)
        self.assertIn("tex to-dds", out)

    def test_inspect_bad_bytes_does_not_crash(self):
        # bytes that claim to be a texture but do not parse must give a read-out, not an exception
        rep = inspect.describe(b"TEX\0" + bytes(20), typemap.BY_EXT["tex"])
        self.assertFalse(rep.editable)
        self.assertIsInstance(rep.text("x"), str)


if __name__ == "__main__":
    unittest.main()

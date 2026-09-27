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

    def test_a_dds_must_hold_the_textures_own_format(self):
        # a DXT5 (or uncompressed) .dds given for a DXT1 texture was taken and its first bytes read as DXT1
        # blocks; only a .dds too short for the texture was refused
        cases = [(20, 24, "this .dds is DXT5, the texture is DXT1"),
                 (20, 40, "this .dds is uncompressed 32-bit, the texture is DXT1"),
                 (24, 20, "this .dds is DXT1, the texture is DXT5"),
                 (31, 24, "this .dds is DXT5, the texture is ATI2"),
                 (40, 20, "this .dds is DXT1, the texture is uncompressed 32-bit")]
        for tmpl, fmt, said in cases:
            with self.subTest(template=tmpl, dds=fmt), self.assertRaises(FormatError) as cm:
                tex.dds_to_tex(tex.to_dds(make(fmt, 8, 8, 1)), template=make(tmpl, 8, 8, 1))
            self.assertIn(said, str(cm.exception))
        # one family is one .dds format: the texture keeps its own id
        self.assertEqual(tex.dds_to_tex(tex.to_dds(make(20, 8, 8, 1)), template=make(25, 8, 8, 1)).fmt, 25)
        # the pixels must be exactly what the header announces (more is another surface, or not this format)
        with self.assertRaises(FormatError):
            tex.dds_to_tex(tex.to_dds(make(20, 8, 8, 1)) + bytes(8), template=make(20, 8, 8, 1))
        from riftstone import skins                     # Studio's texture drop and tex from-dds --like
        bc1 = tex.build(tex.Tex(0x20000, tex.VERSION, 1, 8, 8, 1, 20, 1, struct.pack("<I", 20) + bytes(32)))
        dxt5 = tex.to_dds(tex.Tex(0x20000, tex.VERSION, 1, 8, 8, 1, 24, 1, struct.pack("<I", 20) + bytes(range(64))))
        with self.assertRaises(FormatError):
            skins.texture_like(dxt5, bc1)

    def test_uncompressed_dds_layouts(self):
        # a .dds without a four-cc was taken as B, G, R, A bytes whatever its masks said: A8B8G8R8 came in
        # with red and blue swapped, X8R8G8B8 took alpha from its padding; DXT3 was read as DXT5
        def dds(masks, flags, bits=32):
            hdr = bytearray(tex.to_dds(make(40, 2, 2, 1))[:128])
            struct.pack_into("<I", hdr, 80, flags)
            struct.pack_into("<5I", hdr, 88, bits, *masks)
            return bytes(hdr) + bytes([10, 20, 30, 40]) * 4
        bgra, rgba = (0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000), (0x000000FF, 0x0000FF00, 0x00FF0000, 0xFF000000)
        rgb, alpha = tex._DDPF_RGB, tex._DDPF_ALPHAPIXELS
        cases = [(bgra, rgb | alpha, [10, 20, 30, 40]),                 # A8R8G8B8: the game's own byte order
                 (rgba, rgb | alpha, [30, 20, 10, 40]),                 # A8B8G8R8
                 (bgra[:3] + (0,), rgb, [10, 20, 30, 255]),             # X8R8G8B8: opaque
                 (rgba, rgb, [30, 20, 10, 255])]                        # X8B8G8R8 (an alpha mask without the flag)
        for masks, flags, want in cases:
            for template in (None, make(40, 2, 2, 1)):
                with self.subTest(masks=masks, flags=flags, template=template is not None):
                    t = tex.dds_to_tex(dds(masks, flags), template)
                    self.assertEqual((t.fmt, t.body[4:]), (40, bytes(want) * 4))
        refused = [(bgra, rgb | alpha, 24), ((0xF800, 0x07E0, 0x001F, 0), rgb, 32), (bgra, 0x20000, 32),
                   ((0x3FF00000, 0x000FFC00, 0x000003FF, 0xC0000000), rgb | alpha, 32), (bgra[:3] + (0xFF,), rgb | alpha, 32)]
        for masks, flags, bits in refused:
            with self.subTest(masks=masks, flags=flags, bits=bits), self.assertRaises(FormatError):
                tex.dds_to_tex(dds(masks, flags, bits))
        dxt3 = bytearray(tex.to_dds(make(24, 8, 8, 1)))
        dxt3[84:88] = b"DXT3"
        for template in (None, make(24, 8, 8, 1)):
            with self.assertRaises(FormatError) as cm:
                tex.dds_to_tex(bytes(dxt3), template)
            self.assertIn("save it as DXT5", str(cm.exception))

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

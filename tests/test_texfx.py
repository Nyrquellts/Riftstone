import unittest

import helpers  # noqa: F401 (sys.path)
from riftstone import tex, texcodec, texfx
from riftstone.errors import RiftError


def texture(fmt=20, side=16, alpha=255, version=tex.VERSION):
    """A synthetic colour texture: a gradient, in the given format, through the real encoder."""
    px = bytearray()
    for y in range(side):
        for x in range(side):
            px += bytes((x * 255 // side, y * 255 // side, 128, alpha if (x + y) % 2 else 255))
    template = tex.Tex(0x20000, tex.VERSION, 1, side, side, 1, fmt, 1, bytes(4))
    t = tex.parse(texcodec.encode(side, side, bytes(px), template, cutout=False))
    t.version = version
    return tex.build(t)


class TexFxTest(unittest.TestCase):
    def test_every_preset_keeps_the_texture_shape(self):
        for fmt in (20, 24):                                             # BC1 and BC3
            src = texture(fmt)
            a = tex.parse(src)
            for p in texfx.PRESETS:
                out = texfx.apply(src, p)
                b = tex.parse(out)
                self.assertEqual((b.fmt, b.width, b.height, b.mip_count, b.attr1, b.version),
                                 (a.fmt, a.width, a.height, a.mip_count, a.attr1, a.version), p)
                self.assertNotEqual(out, src, p)
                self.assertEqual(texfx.apply(src, p), out, "presets are deterministic")

    def test_alpha_kept(self):
        src = texture(24, alpha=40)
        _, _, before = texcodec.decode(tex.parse(src))
        _, _, after = texcodec.decode(tex.parse(texfx.apply(src, "frost")))
        self.assertEqual(before[3::4], after[3::4])

    def test_colours_move_the_right_way(self):
        w = h = 8
        grey = bytes((128, 128, 128, 255)) * (w * h)
        mean = lambda px, c: sum(px[c::4]) / (w * h)                    # noqa: E731
        frost = texfx.apply_rgba(w, h, grey, "frost")
        self.assertGreater(mean(frost, 2), mean(frost, 0))               # bluer than red
        abyss = texfx.apply_rgba(w, h, grey, "abyssal")
        self.assertLess(mean(abyss, 1), 128)                             # darker
        self.assertEqual(texfx.apply_rgba(w, h, grey, "lava", strength=0.0), grey)

    def test_online_revision_kept(self):
        out = texfx.apply(texture(20, version=tex.VERSION_DDO), "weathered")
        self.assertEqual(tex.parse(out).version, tex.VERSION_DDO)

    def test_refusals(self):
        with self.assertRaises(RiftError):
            texfx.apply(texture(), "sparkly")
        with self.assertRaises(RiftError):
            texfx.apply(texture(), "frost", strength=2.0)
        t = tex.parse(texture(24))
        t.fmt = 31                                                       # a normal map
        with self.assertRaises(RiftError):
            texfx.apply(tex.build(t), "frost")

    def test_noise_tiles(self):
        n = texfx._noise_field(32, 32, 3)
        self.assertEqual(len(n), 32 * 32)
        self.assertTrue(all(0.0 <= v <= 1.0 for v in n))
        # the pattern wraps: the last column continues into the first (smooth across the seam)
        seam = max(abs(n[y * 32 + 31] - n[y * 32]) for y in range(32))
        inner = max(abs(n[y * 32 + 16] - n[y * 32 + 15]) for y in range(32))
        self.assertLess(seam, 3 * inner + 0.05)


if __name__ == "__main__":
    unittest.main()

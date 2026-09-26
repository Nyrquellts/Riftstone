"""texcodec: texture pixels in and out -- BC1/BC3/BC5/RGBA8 decoding, BC1/BC3 encoding with mips, and
PNG writing and reading (every colour type, both depths, every row filter)."""
import random
import struct
import unittest
import zlib

import helpers  # noqa: F401 (sys.path)
import world_fixture
from riftstone import tex, texcodec
from riftstone.errors import RiftError

SIG = b"\x89PNG\r\n\x1a\n"


def chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def paeth(a, b, c):
    pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
    return a if pa <= pb and pa <= pc else b if pb <= pc else c


def filtered(row: bytes, prev: bytes, bpp: int, ft: int) -> bytes:
    """One scanline as a PNG encoder writes it with row filter ft."""
    out = bytearray([ft])
    for i, x in enumerate(row):
        a = row[i - bpp] if i >= bpp else 0
        b, c = prev[i], prev[i - bpp] if i >= bpp else 0
        pred = (0, a, b, (a + b) >> 1, paeth(a, b, c))[ft]
        out.append((x - pred) & 0xFF)
    return bytes(out)


def make_png(w, h, ctype, depth, samples: bytes, plte=None, trns=None, interlace=0) -> bytes:
    """A PNG of raw samples (big-endian for 16 bit), cycling the row filters 0..4."""
    chans = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    stride = w * chans * depth // 8
    bpp = max(1, chans * depth // 8)
    rows, prev = bytearray(), bytes(stride)
    for y in range(h):
        row = samples[y * stride:(y + 1) * stride]
        rows += filtered(row, prev, bpp, y % 5)
        prev = row
    body = chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, depth, ctype, 0, 0, interlace))
    if plte is not None:
        body += chunk(b"PLTE", plte)
    if trns is not None:
        body += chunk(b"tRNS", trns)
    return SIG + body + chunk(b"IDAT", zlib.compress(bytes(rows))) + chunk(b"IEND", b"")


class PngTest(unittest.TestCase):
    def setUp(self):
        self.rng = random.Random(5)

    def rand(self, n):
        return bytes(self.rng.randrange(256) for _ in range(n))

    def test_round_trip(self):
        px = self.rand(37 * 23 * 4)
        self.assertEqual(texcodec.read_png(texcodec.png(37, 23, px)), (37, 23, px))

    def test_every_colour_type_depth_and_filter(self):
        w, h = 9, 11
        for ctype, chans in ((0, 1), (2, 3), (4, 2), (6, 4)):
            for depth in (8, 16):
                raw = self.rand(w * h * chans * depth // 8)
                hi = raw[0::2] if depth == 16 else raw                 # the byte that is kept
                want = bytearray()
                for i in range(w * h):
                    s = hi[i * chans:(i + 1) * chans]
                    if ctype == 0:
                        want += bytes([s[0]] * 3 + [255])
                    elif ctype == 2:
                        want += bytes(s) + b"\xff"
                    elif ctype == 4:
                        want += bytes([s[0]] * 3 + [s[1]])
                    else:
                        want += bytes(s)
                got = texcodec.read_png(make_png(w, h, ctype, depth, raw))
                self.assertEqual(got, (w, h, bytes(want)), (ctype, depth))

    def test_palette_and_transparency(self):
        plte = self.rand(3 * 20)
        trns = bytes([0, 128, 255])
        idx = bytes(self.rng.randrange(20) for _ in range(7 * 5))
        want = b"".join(plte[3 * k:3 * k + 3] + bytes([trns[k] if k < len(trns) else 255]) for k in idx)
        self.assertEqual(texcodec.read_png(make_png(7, 5, 3, 8, idx, plte, trns)), (7, 5, want))

    def test_refusals(self):
        good = make_png(4, 4, 6, 8, self.rand(64))
        ihdr = lambda w, h, d, c, i=0: SIG + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, d, c, 0, 0, i))
        bad = {
            "not a png": b"GIF89a" + good[6:],
            "empty": b"",
            "no header": SIG + chunk(b"IEND", b""),
            "a chunk past the end": good[:40],
            "no image data": ihdr(4, 4, 8, 6) + chunk(b"IEND", b""),
            "damaged image data": ihdr(4, 4, 8, 6) + chunk(b"IDAT", b"garbage!") + chunk(b"IEND", b""),
            "too much image data": ihdr(1, 1, 8, 6) + chunk(b"IDAT", zlib.compress(bytes(64))) + chunk(b"IEND", b""),
            "interlaced": make_png(4, 4, 6, 8, self.rand(64), interlace=1),
            "1-bit": ihdr(4, 4, 1, 0) + chunk(b"IDAT", zlib.compress(bytes(8))) + chunk(b"IEND", b""),
            "16-bit palette": ihdr(4, 4, 16, 3) + chunk(b"IEND", b""),
            "zero width": ihdr(0, 4, 8, 6) + chunk(b"IEND", b""),
            "too wide": ihdr(texcodec.MAX_SIDE + 1, 1, 8, 6) + chunk(b"IEND", b""),
            "unknown filter": ihdr(1, 1, 8, 6) + chunk(b"IDAT", zlib.compress(b"\x09" + bytes(4))) + chunk(b"IEND", b""),
            "palette index out of range": make_png(2, 1, 3, 8, bytes([0, 5]), plte=bytes(6)),
            "palette missing": make_png(2, 1, 3, 8, bytes([0, 0])),
        }
        for why, data in bad.items():
            with self.assertRaises(RiftError, msg=why):
                texcodec.read_png(data)
        with self.assertRaises(RiftError):
            texcodec.read_png(good, max_pixels=15)


class CodecTest(unittest.TestCase):
    def template(self, fmt):
        t = tex.parse(world_fixture.chimera_texture())
        t.fmt = fmt
        return t

    def gradient(self, w, h):
        return bytes(v for y in range(h) for x in range(w)
                     for v in (x * 255 // max(1, w - 1), y * 255 // max(1, h - 1), 96, 255 if x < w // 2 else 40))

    def test_encode_and_decode(self):
        w, h = 32, 16
        px = self.gradient(w, h)
        for fmt in (19, 24):                                   # BC1 (with its cut-out alpha) and BC3
            out = texcodec.encode(w, h, px, self.template(fmt), cutout=fmt == 19)
            t = tex.parse(out)
            # mips until the longer side is 2 px: 32x16, 16x8, 8x4, 4x2, 2x1
            self.assertEqual((t.width, t.height, t.fmt, t.mip_count, t.version), (w, h, fmt, 5, tex.VERSION))
            self.assertEqual(tex.build(t), out)
            gw, gh, back = texcodec.decode(t, 0)
            self.assertEqual((gw, gh), (w, h))
            opaque = [i for i in range(0, len(px), 4) if px[i + 3] >= 128]
            rgb_err = sum(abs(px[i + k] - back[i + k]) for i in opaque for k in range(3)) / (3 * len(opaque))
            self.assertLess(rgb_err, 10, fmt)
            if fmt == 24:                                      # BC3 keeps the alpha
                self.assertLess(max(abs(px[i] - back[i]) for i in range(3, len(px), 4)), 12)
            else:                                              # BC1 alpha is a cut-out: opaque or transparent black
                self.assertEqual({back[i + 3] for i in opaque}, {255})
                self.assertEqual({bytes(back[i:i + 4]) for i in range(0, len(px), 4) if px[i + 3] < 128}, {bytes(4)})
            for level in range(t.mip_count):                   # every mip decodes to its size
                mw, mh, mp = texcodec.decode(t, level)
                self.assertEqual((mw, mh, len(mp)), (max(1, w >> level), max(1, h >> level), 4 * mw * mh))
            dds = tex.to_dds(t)                                 # and the layout converts to .dds
            self.assertEqual(dds[:4], b"DDS ")

    def test_bc1_alpha_follows_the_game_map(self):
        w = h = 16
        px = self.gradient(w, h)                               # the right half is transparent
        opaque_map = tex.parse(texcodec.encode(w, h, bytes(16 * 16 * 4), self.template(19), cutout=False))
        cutout_map = tex.parse(texcodec.encode(w, h, px, self.template(19), cutout=True))
        self.assertEqual((texcodec.has_cutout(opaque_map), texcodec.has_cutout(cutout_map)), (False, True))
        # a map the game draws opaque stays opaque, whatever alpha the picture has
        _, _, back = texcodec.decode(tex.parse(texcodec.encode(w, h, px, opaque_map)), 0)
        self.assertEqual(set(back[3::4]), {255})
        # a map with holes keeps them where the picture is transparent
        _, _, back = texcodec.decode(tex.parse(texcodec.encode(w, h, px, cutout_map)), 0)
        self.assertEqual(set(back[3::4]), {0, 255})
        self.assertFalse(texcodec.has_cutout(tex.parse(world_fixture.chimera_texture())))   # BC3: no

    def test_encode_refusals(self):
        px = self.gradient(16, 16)
        with self.assertRaises(RiftError):
            texcodec.encode(16, 16, px, self.template(31))     # BC5 is not written from pixels
        with self.assertRaises(RiftError):
            texcodec.encode(12, 16, bytes(12 * 16 * 4), self.template(24))
        with self.assertRaises(RiftError):
            texcodec.encode(16, 16, px[:-4], self.template(24))

    def test_names_and_preview(self):
        self.assertEqual([texcodec.codec(f) for f in (19, 24, 31, 40, 7)],
                         ["BC1 (DXT1)", "BC3 (DXT5)", "BC5 (normal map)", "RGBA8", "format 7"])
        self.assertEqual([texcodec.writable(f) for f in (20, 47, 31, 40)], [True, True, False, False])
        t = tex.parse(texcodec.encode(64, 64, self.gradient(64, 64), self.template(24)))
        png, w, h = texcodec.preview(t, 20)
        self.assertEqual((w, h), (16, 16))                      # the largest mip no bigger than 20
        self.assertEqual(texcodec.read_png(png)[:2], (16, 16))


if __name__ == "__main__":
    unittest.main()

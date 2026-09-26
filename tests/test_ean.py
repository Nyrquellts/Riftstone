import struct
import unittest

import helpers  # noqa: F401 (sys.path)
from riftstone import corpus, ean, inspect, typemap
from riftstone.errors import FormatError


def sample(count=2, frame_bytes=None):
    payload = frame_bytes if frame_bytes is not None else bytes(count * (ean.FRAME_A + ean.FRAME_B))
    return ean.build(ean.Ean(count, payload))


class EanTest(unittest.TestCase):
    def test_round_trip(self):
        for raw in (sample(1), sample(3), sample(0, b""), sample(2, bytes(range(50)))):
            self.assertEqual(raw[:4], b"EAN\0")
            self.assertEqual(ean.build(ean.parse(raw)), raw)

    def test_header_fields(self):
        raw = sample(5, bytes(200))
        magic, ver, size, count = struct.unpack_from("<4sIII", raw, 0)
        self.assertEqual((magic, ver, size, count), (b"EAN\0", ean.VERSION, 200, 5))

    def test_refusals(self):
        raw = sample(2)
        with self.assertRaises(FormatError):
            ean.parse(b"XXX\0" + raw[4:])                              # wrong magic
        with self.assertRaises(FormatError):
            ean.parse(raw[:8])                                        # truncated header
        bad_ver = raw[:4] + struct.pack("<I", 0xDEAD) + raw[8:]
        with self.assertRaises(FormatError):
            ean.parse(bad_ver)                                        # wrong version
        bad_size = raw[:8] + struct.pack("<I", 9999) + raw[12:]
        with self.assertRaises(FormatError):
            ean.parse(bad_size)                                       # size != payload

    def test_ddo_version(self):
        raw = ean.build(ean.Ean(2, bytes(1600), ean.VERSION_DDO))    # DDO frames are not always 56 bytes
        e = ean.parse(raw)
        self.assertEqual(e.version, ean.VERSION_DDO)
        self.assertEqual(ean.build(e), raw)
        with self.assertRaises(FormatError):
            ean.build(ean.Ean(1, b"", 0x1234))

    def test_inspect(self):
        rep = inspect.describe(sample(4, bytes(4 * 56)), typemap.BY_EXT["ean"])
        out = rep.text("x")
        self.assertIn("rEffectAnim", out)
        self.assertIn("4 frames", out)

    @unittest.skipUnless(helpers.game_root(), "no game")
    def test_corpus_byte_exact(self):
        from riftstone.game import find_game
        g = find_game()
        n = ok = 0
        for r in corpus.resources(g, [typemap.BY_EXT["ean"]]):
            n += 1
            if ean.build(ean.parse(r.data)) == r.data:
                ok += 1
        self.assertGreater(n, 0)
        self.assertEqual(ok, n)


if __name__ == "__main__":
    unittest.main()

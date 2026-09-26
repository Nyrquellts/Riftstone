import struct
import unittest
import zlib

import helpers  # noqa: F401
from riftstone import arc, typemap
from riftstone.errors import FormatError

TEX = typemap.BY_EXT["tex"]
MOD = typemap.BY_EXT["mod"]


def sample() -> arc.Archive:
    return arc.Archive([
        arc.Entry.from_data(b"model\\em\\e01\\e0100\\e0100", TEX, b"TEX\0" + bytes(200)),
        arc.Entry.from_data(b"model\\em\\e01\\e0100\\e0100", MOD, b"MOD\0" + b"x" * 50),
        arc.Entry.from_data(b"odd\\trailing ", TEX, b""),
    ])


class ArcTest(unittest.TestCase):
    def test_layout_matches_vanilla_rules(self):
        raw = sample().build()
        magic, ver, count = struct.unpack_from("<4sHH", raw)
        self.assertEqual((magic, ver, count), (b"ARC\0", 7, 3))
        offsets = [struct.unpack_from("<64sIIII", raw, 8 + i * 80)[4] for i in range(3)]
        self.assertEqual(offsets[0], 0x8000)                   # data starts on a 32 KiB boundary
        self.assertEqual(raw[8 + 3 * 80:0x8000], bytes(0x8000 - 8 - 240))
        for i in range(3):
            _, _, zsize, size_flags, off = struct.unpack_from("<64sIIII", raw, 8 + i * 80)
            self.assertEqual(size_flags >> 29, 2)
            if i < 2:
                self.assertEqual(offsets[i + 1], off + zsize)  # contiguous, directory order

    def test_roundtrip(self):
        a = sample()
        raw = a.build()
        b = arc.Archive.parse(raw)
        self.assertEqual([(e.name, e.type_id, e.data()) for e in a.entries],
                         [(e.name, e.type_id, e.data()) for e in b.entries])
        self.assertEqual(b.build(), raw)

    def test_same_name_different_type_is_two_resources(self):
        a = sample()
        self.assertEqual(len(a.index()), 3)

    def test_size_above_24_bits_survives(self):
        # ArisenTools read 24 bits and silently truncated four vanilla shader archives.
        big = bytes(21_021_368)
        a = arc.Archive([arc.Entry.from_data(b"sa\\big", TEX, big)])
        b = arc.Archive.parse(a.build())
        self.assertEqual(b.entries[0].size, len(big))
        self.assertEqual(len(b.entries[0].data()), len(big))

    def test_put(self):
        a = sample()
        self.assertEqual(a.put(b"model\\em\\e01\\e0100\\e0100", TEX, b"TEX\0" + bytes(200)), "unchanged")
        self.assertEqual(a.put(b"model\\em\\e01\\e0100\\e0100", TEX, b"TEX\0new"), "replaced")
        self.assertEqual(a.put(b"new\\thing", TEX, b"TEX\0"), "added")
        self.assertEqual(a.find(b"model\\em\\e01\\e0100\\e0100", TEX).data(), b"TEX\0new")
        self.assertEqual(a.entries[-1].name, b"new\\thing")

    def test_verify_build(self):
        a = sample()
        raw = a.build()
        expected = {e.key: arc.sha256(e.data()) for e in a.entries}
        arc.verify_build(raw, expected)
        expected[(b"model\\em\\e01\\e0100\\e0100", TEX)] = "0" * 64
        with self.assertRaises(FormatError):
            arc.verify_build(raw, expected)

    def bad(self, raw: bytes, needle: str):
        with self.assertRaises(FormatError) as cm:
            arc.Archive.parse(raw)
        self.assertIn(needle, str(cm.exception))

    def test_rejects(self):
        raw = bytearray(sample().build())
        self.bad(b"ARC", "shorter")
        self.bad(b"ARCX" + raw[4:], "not an ARC")
        self.bad(raw[:4] + struct.pack("<H", 8) + raw[6:], "version 8")
        self.bad(raw[:8 + 100], "directory")
        r = bytearray(raw)
        struct.pack_into("<I", r, 8 + 68, 0)                # zero stored size
        self.bad(bytes(r), "zero stored size")
        r = bytearray(raw)
        struct.pack_into("<I", r, 8 + 76, len(raw))          # payload beyond EOF
        self.bad(bytes(r), "outside the file")
        r = bytearray(raw)
        struct.pack_into("<I", r, 8 + 80 + 76, struct.unpack_from("<I", r, 8 + 76)[0])  # overlap
        self.bad(bytes(r), "overlap")
        r = bytearray(raw)
        r[8:8 + 64] = b"A" * 64                               # no terminator
        self.bad(bytes(r), "NUL-terminated")
        r = bytearray(raw)
        r[8 + 60] = 1                                         # junk after the terminator
        self.bad(bytes(r), "not zero")
        r = bytearray(raw)
        r[8 + 80:8 + 80 + 64] = r[8:8 + 64]
        struct.pack_into("<I", r, 8 + 80 + 64, TEX)           # duplicate identity
        self.bad(bytes(r), "duplicate")

    def test_decode_rejects_bad_streams(self):
        e = arc.Entry(b"x", TEX, 10, zlib.compress(b"123456789"))
        with self.assertRaises(FormatError):
            e.data()
        e = arc.Entry(b"x", TEX, 3, zlib.compress(b"abc") + b"junk")
        with self.assertRaises(FormatError):
            e.data()
        e = arc.Entry(b"x", TEX, 3, b"not zlib")
        with self.assertRaises(FormatError):
            e.data()
        e = arc.Entry(b"x", TEX, arc.MAX_DECODED + 1, zlib.compress(b"a"))
        with self.assertRaises(FormatError):
            e.data()

    def test_build_refuses(self):
        with self.assertRaises(FormatError):
            arc.Archive([arc.Entry(b"x" * 64, TEX, 1, b"x")]).build()
        with self.assertRaises(FormatError):
            e = arc.Entry.from_data(b"x", TEX, b"a")
            arc.Archive([e, e]).build()
        with self.assertRaises(FormatError):
            arc.Entry.from_data(b"", TEX, b"a")


if __name__ == "__main__":
    unittest.main()

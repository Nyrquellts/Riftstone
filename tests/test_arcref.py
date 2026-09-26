import struct
import unittest
import zlib

import helpers  # noqa: F401 (sys.path)
from riftstone import arcref, inspect, typemap
from riftstone.errors import FormatError

DIRECTORY = [(b"model\\om\\om3521", typemap.BY_EXT["mod"]), (b"model\\om\\om3521_BM", typemap.BY_EXT["tex"]),
             (b"effect\\efl\\om\\om3521_00", typemap.BY_EXT["efl"])]


class ArcRefTest(unittest.TestCase):
    def test_a_reference_is_the_archives_directory(self):
        ref = arcref.for_names(DIRECTORY)
        raw = arcref.build(ref)
        self.assertEqual(raw[:8], b"ARCS" + struct.pack("<HH", 7, 3))
        self.assertEqual(struct.unpack_from("<II", raw, 8), (~zlib.crc32(b"model\\om\\om3521") & 0xFFFFFFFF,
                                                            typemap.BY_EXT["mod"]))
        self.assertEqual(arcref.build(arcref.parse(raw)), raw)
        self.assertTrue(arcref.matches(arcref.parse(raw), DIRECTORY))
        self.assertFalse(arcref.matches(arcref.parse(raw), DIRECTORY[:2]))
        self.assertEqual(arcref.target(b"rom\\om\\f07\\om3521"), "rom/om/f07/om3521")

    def test_the_hash_keeps_the_top_bit(self):
        # type ids clear the top bit; reference hashes do not
        name = next(n for n in (b"a%d" % i for i in range(200)) if ~zlib.crc32(n) & 0x80000000)
        self.assertTrue(arcref.name_hash(name) & 0x80000000)

    def test_rejects(self):
        raw = arcref.build(arcref.for_names(DIRECTORY))
        for bad in (raw[:6], b"ARCZ" + raw[4:], raw[:-1], raw + b"\0", raw[:6] + struct.pack("<H", 9) + raw[8:]):
            with self.assertRaises(FormatError):
                arcref.parse(bad)

    def test_inspect(self):
        rep = inspect.describe(arcref.build(arcref.for_names(DIRECTORY)), typemap.BY_EXT["arc"])
        self.assertIn("3 resource(s) of the referenced archive", rep.text("x"))


if __name__ == "__main__":
    unittest.main()

import struct
import unittest

import helpers
from riftstone import xfs
from riftstone.errors import FormatError


class XfsTest(unittest.TestCase):
    def test_roundtrip_every_type(self):
        x = helpers.sample_xfs()
        raw = xfs.build(x)
        y = xfs.parse(raw)
        self.assertEqual(y.root, x.root)
        self.assertEqual(y.classes, x.classes)
        self.assertEqual(y.minor, 4)
        self.assertEqual(xfs.build(y), raw)
        self.assertEqual(xfs.build_definitions(y.classes), y.extra["def_raw"])

    def test_header_and_numbering(self):
        raw = xfs.build(helpers.sample_xfs())
        magic, ver, minor, count, ncls, defsize = struct.unpack_from("<4sHHIII", raw)
        self.assertEqual((magic, ver, minor, ncls), (b"XFS\0", 0x109, 4, 2))
        self.assertEqual(count, len(list(xfs.walk(xfs.parse(raw).root))))
        self.assertEqual(defsize % 4, 0)

    def test_walk_is_preorder(self):
        x = helpers.sample_xfs()
        values = [o.fields[0][0] for o in xfs.walk(x.root) if o.cls == 1]
        self.assertEqual(values, [3.25, -0.0, 1.0, 2.0])

    def test_nan_bits_survive(self):
        x = helpers.sample_xfs()
        x.root.fields[9] = [xfs.F32Bits(0x7FA00001)]
        y = xfs.parse(xfs.build(x))
        self.assertEqual(y.root.fields[9], [xfs.F32Bits(0x7FA00001)])

    def bad(self, raw: bytes, needle: str):
        with self.assertRaises(FormatError) as cm:
            xfs.parse(raw)
        self.assertIn(needle, str(cm.exception))

    def test_rejects(self):
        raw = bytearray(xfs.build(helpers.sample_xfs()))
        self.bad(b"XFS", "shorter")
        self.bad(b"XFZ\0" + raw[4:], "not an XFS")
        self.bad(raw[:4] + b"\x0a\x01" + raw[6:], "version")
        r = bytearray(raw)
        struct.pack_into("<I", r, 0x0C, 0)
        self.bad(bytes(r), "class count")
        r = bytearray(raw)
        struct.pack_into("<I", r, 0x10, len(raw))
        self.bad(bytes(r), "definition block")
        r = bytearray(raw)
        struct.pack_into("<I", r, 0x08, 99)
        self.bad(bytes(r), "header says 99")
        self.bad(bytes(raw) + b"\0", "after the root")
        self.bad(bytes(raw[:-3]), "")

    def test_unknown_property_type_refused(self):
        x = helpers.sample_xfs()
        raw = bytearray(xfs.build(x))
        # first property of class 0 lives at 0x14 + 8 (two offsets) + 12; its type byte is +4
        raw[0x14 + 8 + 12 + 4] = 0x55
        self.bad(bytes(raw), "type 0x55")

    def test_depth_limit(self):
        c = xfs.ClassDef(1, 4, (helpers.prop("mNext", "classref"),))
        node = xfs.Obj(0, [[None]])
        for _ in range(xfs.MAX_DEPTH + 5):
            node = xfs.Obj(0, [[node]])
        with self.assertRaises(FormatError):
            xfs.build(xfs.Xfs(0, [c], node))

    def test_duplicate_class_declaration_refused(self):
        x = helpers.sample_xfs()
        x.classes = [helpers.ROOT, helpers.ROOT]
        x.root = xfs.Obj(0, x.root.fields)
        x.root.fields[27] = [None]
        x.root.fields[29] = []
        self.bad(xfs.build(xfs.Xfs(4, [helpers.ROOT, helpers.ROOT], x.root)), "declared twice")

    def test_canonical_orders_classes_by_first_use(self):
        x = helpers.sample_xfs()
        raw = xfs.build(x)
        self.assertTrue(xfs.is_canonical(xfs.parse(raw)))
        swapped = xfs.Xfs(4, [helpers.CHILD, helpers.ROOT], _remap(x.root, {0: 1, 1: 0}))
        y = xfs.parse(xfs.build(swapped))
        self.assertFalse(xfs.is_canonical(y))
        self.assertEqual(xfs.build(xfs.canonical(y)), raw)

    def test_build_refuses_bad_models(self):
        x = helpers.sample_xfs()
        x.root.fields[11] = [b"a\0b"]
        with self.assertRaises(FormatError):
            xfs.build(x)
        x = helpers.sample_xfs()
        x.root.fields.pop()
        with self.assertRaises(FormatError):
            xfs.build(x)


def _remap(o: xfs.Obj, m: dict) -> xfs.Obj:
    return xfs.Obj(m[o.cls], [[_remap(v, m) if isinstance(v, xfs.Obj) else v for v in vals] for vals in o.fields])


if __name__ == "__main__":
    unittest.main()

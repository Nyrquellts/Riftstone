import struct
import unittest

import helpers
from riftstone import inspect as inspector, typemap, xfs


class InspectTest(unittest.TestCase):
    def test_xfs_is_editable(self):
        data = xfs.build(helpers.sample_xfs())
        rep = inspector.describe(data, 0x215896C2)   # rStatusParam id, but sample uses test classes
        self.assertTrue(rep.editable)
        self.assertTrue(rep.xfs)
        self.assertIn("objects", " ".join(rep.summary))
        self.assertIn("editable as YAML", rep.text("x"))

    def test_binary_gets_readonly_scan(self):
        # a fake .ocl-ish blob with a string then an aligned vec3
        blob = b"%\x12\x12 " + b"COL_BODY\0\0\0\0" + struct.pack("<3f", 1.5, -30.0, 20.0) + b"\0" * 16
        self.assertEqual(len(b"%\x12\x12 " + b"COL_BODY\0\0\0\0") % 4, 0)  # vec3 is 4-aligned
        rep = inspector.describe(blob, typemap.BY_EXT["ocl"])
        self.assertFalse(rep.editable)
        self.assertIn("collision", rep.note)
        self.assertTrue(any(f.value == b"COL_BODY" for f in rep.strings))
        self.assertTrue(any(f.kind == "vec3" for f in rep.vectors))

    def test_ocl_with_shapes_is_editable(self):
        from test_ocl import make_simple
        rep = inspector.describe(make_simple(2), typemap.BY_EXT["ocl"])
        self.assertTrue(rep.editable)
        self.assertIn("editable as YAML", rep.text("x"))

    def test_lot_placements(self):
        from riftstone import lot
        rec = lot.blank(4, 0, mName="em0100", mPosition=(100.0, -50.0, 200.0), mAngle=(0.0, 1.5, 0.0))
        data = lot.build(lot.Lot([rec]))
        placements = inspector._lot_placements(data)
        self.assertEqual(placements[0][0], "em0100")
        self.assertEqual(tuple(round(x) for x in placements[0][1]), (100, -50, 200))
        rep = inspector.describe(data, typemap.BY_EXT["lot"])
        self.assertTrue(rep.editable)
        self.assertIn("turn 86 deg", rep.text("x"))

    def test_unknown_type_still_opens(self):
        rep = inspector.describe(b"\x01\x02\x03\x04" + b"hello_world\0" + b"\0" * 20, 0)
        self.assertFalse(rep.editable)
        self.assertIn("hello_world", [f.value.decode() if isinstance(f.value, bytes) else "" for f in rep.strings])
        self.assertIn("read-only", rep.text())


if __name__ == "__main__":
    unittest.main()

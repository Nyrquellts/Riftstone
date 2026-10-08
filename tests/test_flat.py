import struct
import unittest

import helpers  # noqa: F401 (sys.path)
from riftstone import flat, params, typemap
from riftstone.errors import FormatError, ParamError


class FlatTest(unittest.TestCase):
    def rt(self, f):
        """A Flat round-trips through bytes and through YAML."""
        raw = flat.build(f)
        self.assertEqual(flat.build(flat.parse(raw, f.ext)), raw)
        self.assertEqual(flat.yaml_to_bytes(flat.to_yaml(f, "x")), raw)
        return raw

    def test_scalar_and_list(self):
        f = flat.Flat("ajp", 0, {"version": 513, "mpArray": [30.0, -40.0, 0.03]})
        raw = self.rt(f)
        self.assertEqual(raw[:4], b"ajp\0")
        self.assertEqual(struct.unpack_from("<I", raw, 8)[0], 3)      # count follows the magic+version
        self.assertIn("mpArray: [30.0, -40.0, 0.03", flat.to_yaml(f))

    def test_magicless_format(self):
        f = flat.Flat("fed", 0, {"version": 1, "mpMarkerArray": [{"x": 1.0, "y": 2.0, "z": 3.0}]})
        raw = self.rt(f)
        self.assertEqual(raw[:4], struct.pack("<i", 1))              # no magic; starts with version
        y = flat.parse(raw, "fed").data["mpMarkerArray"][0]["y"]     # f32 is kept as exact bits
        self.assertEqual(struct.unpack("<f", struct.pack("<I", y))[0], 2.0)

    def test_strings_and_fixed_arrays(self):
        f = flat.Flat("edp", 0, flat.parse(_edp_bytes(), "edp").data)
        raw = self.rt(f)
        y = flat.to_yaml(f)
        self.assertIn('mprHumanEdit: "pl/human"', y)
        self.assertIn("mColorHair: [", y)
        with self.assertRaises(ParamError):                          # a NUL cannot go in a string
            flat.yaml_to_bytes(y.replace('"pl/human"', '"pl\\u0000x"'), "e.yaml")
        with self.assertRaises(ParamError):                          # a fixed group must keep its length
            flat.yaml_to_bytes(y.replace("mArmorId: [0, 0, 0, 0, 0]", "mArmorId: [0, 0]"), "e.yaml")

    def test_count_by_reference(self):
        f = flat.Flat("amr", 0, {"version": 1, "mArrayInfoNum": 0, "mArrayBlendNum": 0,
                                 "mArrayInfo": [{"mModelId": 5, "mType": 1, "mFname": "a"},
                                                {"mModelId": 6, "mType": 2, "mFname": "bb"}],
                                 "mArrayBlend": [{"mModelId": 7, "mRate": 0.5}]})
        raw = self.rt(f)
        g = flat.parse(raw, "amr")
        self.assertEqual((g.data["mArrayInfoNum"], g.data["mArrayBlendNum"]), (2, 1))   # counts follow the lists
        # fuzz finding flat-invariant-3836503ce42f: a negative (signed) count parsed as an empty list and
        # rebuilt as 0; it is refused now
        bad = bytearray(raw)
        struct.pack_into("<i", bad, 12, -65536)                                          # mArrayBlendNum
        with self.assertRaises(FormatError):
            flat.parse(bytes(bad), "amr")

    def test_float_bits_are_exact(self):
        # a float with many digits still rebuilds to the same bytes
        f = flat.Flat("ajp", 0, {"version": 1, "mpArray": [0.1, 3.4028235e38, -0.0]})
        raw = flat.build(f)
        self.assertEqual(flat.yaml_to_bytes(flat.to_yaml(f)), raw)

    def test_refusals(self):
        good = flat.to_yaml(flat.Flat("ajp", 0, {"version": 1, "mpArray": [1.0]}))
        for bad in (good.replace("version: 1", "version: 4294967296"),   # out of i32 range
                    good.replace("version: 1", "version: notnum"),
                    good.replace("mpArray: [1.0]", "mpArray: 5"),        # list expected
                    good.replace("riftstone: ajp/1", "riftstone: nope/1"),
                    good.replace("version: 1\n", "", 1)):                # missing field
            with self.assertRaises(ParamError, msg=bad):
                flat.yaml_to_bytes(bad, "a.yaml")
        with self.assertRaises(FormatError):
            flat.parse(b"ajp\0" + struct.pack("<ii", 1, 1), "ajp")       # count 1 but no element -> short
        with self.assertRaises(FormatError):
            flat.parse(b"ajp\0" + struct.pack("<iiff", 1, 1, 2.0, 3.0), "ajp")  # trailing bytes

    def test_huge_numbers_are_refused_in_a_short_message(self):
        # a hex number thousands of digits long parses; writing it in decimal for the message raised
        # ValueError ("Exceeds the limit (4300 digits)")
        for value in ("0x" + "F" * 3600, "9" * 5000):
            with self.assertRaises(ParamError) as cm:
                flat.yaml_to_bytes(f"riftstone: spn/1\nmConst: {value}\nmpPlace: []\n", "s.yaml")
            self.assertLess(len(str(cm.exception)), 200)

    def test_text_utf8_cannot_hold_is_refused(self):
        # a lone surrogate in a string field crashed the build with UnicodeEncodeError
        rec = {"mCategory": 1, "mComment": "sword", "mEpvCrc": 2, "mEpvType": 3, "mSrqCrc": 4, "mSrqType": 5}
        good = flat.to_yaml(flat.Flat("wcrt", 0, {"version": 1, "mpArray": [rec]}))
        for bad in ("\\ud800", "\\udc41"):
            with self.assertRaises(ParamError) as cm:
                flat.yaml_to_bytes(good.replace('"sword"', f'"a{bad}b"'), "w.yaml")
            self.assertIn("cannot be stored as UTF-8", str(cm.exception))
            self.assertIsNotNone(cm.exception.line)
        # a byte that is not UTF-8 is shown as \udcXX and comes back as that byte
        self.assertIn(b"a\xffb\0", flat.yaml_to_bytes(good.replace('"sword"', '"a\\udcffb"')))

    def test_a_list_is_refused_when_its_count_field_cannot_hold_it(self):
        # eap/sap count their records in an s16: 32,768 overflowed it and the build crashed (struct.error)
        d = _sample(flat.SCHEMAS["eap"][1])
        one = flat.to_yaml(flat.Flat("eap", flat.SCHEMAS["eap"][0], d))
        head, rec = one.split("mpStudyDisableAttrAdrs:\n")
        with self.assertRaises(ParamError) as cm:
            flat.yaml_to_bytes(head + "mpStudyDisableAttrAdrs:\n" + rec * 32768, "e.yaml")
        self.assertIn("mpStudyDisableAttrAdrs holds 32768 items; its count studyDisableNum (s16) holds at most 32767",
                      str(cm.exception))

    def test_new_ai_and_quest_formats(self):
        # eap/sap/map/qct/rst use nested records and inline/counted lists; a synthetic sample with one
        # record at every level must parse->build and YAML round-trip byte-for-byte.
        for ext in ("eap", "sap", "map", "qct", "rst", "spn"):
            raw = flat.build(flat.Flat(ext, flat.SCHEMAS[ext][0] or 0, _sample(flat.SCHEMAS[ext][1])))
            f = flat.parse(raw, ext)                                   # normalised (f32 as bits)
            self.assertEqual(flat.build(f), raw, ext)
            self.assertEqual(flat.yaml_to_bytes(flat.to_yaml(f, "x")), raw, ext)
            self.assertEqual(params.yaml_to_resource(params.resource_to_yaml(raw, "x", typemap.BY_EXT[ext])), raw, ext)

    def test_region_status_is_a_creatures_health(self):
        # rst (rRegionStatus, charparam\em\em<id>.rst): mHPMax is the base health (docs/enemy-hp.md).  A type 0
        # region of two elements, a type 10 region of one; the first element's health is 1234.5.
        el = {"mNo": 0, "mId": 0, "mHPMax": 1234.5, "mDPMax": 100.0, "mDPSpeed": 0.3, "mBPMax": 100.0,
              "mBPSpeed": 0.3, "mDamageAdj": 1.0, "mHitStopAdj": 1.0, "mSurface": 1, "mSeSurface": 0, "mAttr": 280,
              "mDPResetTimerMax": 75.0, "mBPResetTimerMax": 30.0}
        d = {"mRegionStatusList": [{"mNo": 0, "mType": 0, "mElementList": [el, dict(el, mNo=1, mId=1)]},
                                   {"mNo": 1, "mType": 10, "mElementList": [dict(el, mHPMax=99.0)]}]}
        f = flat.Flat("rst", flat.SCHEMAS["rst"][0], d)
        raw = self.rt(f)
        self.assertEqual(len(raw), 8 + (12 + 2 * 56) + (12 + 56))          # 56 bytes an element
        self.assertEqual(struct.unpack_from("<II", raw, 0), (0x20110930, 2))
        self.assertEqual(struct.unpack_from("<f", raw, 8 + 12 + 8)[0], 1234.5)      # region 0, element 0, +8
        y = flat.to_yaml(flat.parse(raw, "rst"), "charparam/em/em0100.rst")
        self.assertIn("riftstone: rst/2", y)
        self.assertIn("mHPMax: 1234.5", y)
        self.assertIn("mType: 10", y)
        # an edit of the health goes through the YAML and nothing else moves
        new = flat.yaml_to_bytes(y.replace("mHPMax: 1234.5", "mHPMax: 4321.0", 1))
        self.assertEqual(struct.unpack_from("<f", new, 8 + 12 + 8)[0], 4321.0)
        self.assertEqual(new[:20 + 8] + new[20 + 12:], raw[:20 + 8] + raw[20 + 12:])

    def test_region_status_yaml_written_with_the_old_names_still_loads(self):
        # until 2026-10-02 this format's fields were mpRegion, mUnkA, at0c and so on (rst/1); such a file
        # still loads, and a file that says rst/2 must use today's names
        old = ("riftstone: rst/1\nmpRegion:\n  - mUnkA: 0\n    mUnkB: 0\n    mpSub:\n"
               "      - at04: 0\n        at08: 0\n        at0c: 1234.5\n        at10: 100.0\n        at14: 0.3\n"
               "        at18: 100.0\n        at1c: 0.3\n        at20: 1.0\n        at24: 1.0\n        at2c: 1\n"
               "        at30: 0\n        at28: 280\n        at58: 75.0\n        at5c: 30.0\n")
        want = (struct.pack("<II", 0x20110930, 1) + struct.pack("<III", 0, 0, 1)
                + struct.pack("<IIfffffffIIIff", 0, 0, 1234.5, 100.0, 0.3, 100.0, 0.3, 1.0, 1.0, 1, 0, 280, 75.0, 30.0))
        self.assertEqual(flat.yaml_to_bytes(old), want)
        self.assertEqual(params.yaml_to_resource(old), want)
        again = flat.to_yaml(flat.parse(want, "rst"))
        self.assertIn("mElementList:", again)
        self.assertNotIn("mpRegion", again)
        self.assertEqual(flat.yaml_to_bytes(again), want)
        with self.assertRaises(ParamError) as cm:
            flat.yaml_to_bytes(old.replace("rst/1", "rst/2"), "m.yaml")
        self.assertIn("unexpected field 'mpRegion'", str(cm.exception))

    def test_dispatch(self):
        raw = flat.build(flat.Flat("ajp", 0, {"version": 1, "mpArray": [1.0]}))
        self.assertTrue(params.is_editable_resource(raw))              # by magic
        self.assertIsNotNone(params.resource_to_yaml(raw, "x", typemap.BY_EXT["ajp"]))
        fed = flat.build(flat.Flat("fed", 0, {"version": 1, "mpMarkerArray": []}))
        self.assertFalse(params.is_editable_resource(fed))             # magic-less: not editable without a type
        self.assertTrue(params.is_editable_resource(fed, typemap.BY_EXT["fed"]))
        self.assertEqual(params.yaml_to_resource(flat.to_yaml(flat.parse(fed, "fed"))), fed)


def _elem(elem):
    if isinstance(elem, list):
        return _sample(elem)
    if elem == "string":
        return "x"
    if elem in ("f32", "f64"):
        return 1.5                          # build accepts a plain float for an f32 field
    return 7


def _sample(schema):
    """A dict for a flat schema with one record at every list level (for round-trip tests)."""
    out = {}
    for f in schema:
        kind, name = f[0], f[1]
        if kind == "s":
            out[name] = _elem(f[2])
        elif kind == "a":
            out[name] = [_elem(f[2]) for _ in range(f[3])]
        else:                               # "l" and "ln" -- one record; build syncs any count field
            out[name] = [_elem(f[2])]
    return out


def _edp_bytes():
    out = bytearray(struct.pack("<I", 0x00706465))          # magic edp
    out += struct.pack("<i", 1)                              # version
    out += struct.pack("<4h", 0, 1, 2, 3)                    # mModelType..mJobPl
    out += struct.pack("<2f", 0.5, 0.25)                     # mManWoman, mStoop
    for s in ("pl/human", "pl/body", "pl/face"):
        out += s.encode() + b"\0"
    out += struct.pack("<4i", 100, 101, 102, 103)           # main/sub/quiver/inner
    out += struct.pack("<2i", 0, 0) + struct.pack("<5i", 0, 0, 0, 0, 0)   # wear[2], armor[5]
    out += struct.pack("<2i", 200, 201)                     # hair, beard
    out += struct.pack("<16f", *[0.0] * 16)                 # 4 colours
    out += struct.pack("<2f", 0.0, 0.0)                     # wrinkles
    out += struct.pack("<4f", 0.0, 0.0, 1.0, 1.0)           # eyebrow ofs/scl
    out += struct.pack("<3i", 0, 0, 0)                      # eyebrowTex, goodsR, goodsL
    out += struct.pack("<bbh", 1, 0, 5)                     # chain, hairswing, attackMot
    return bytes(out)


if __name__ == "__main__":
    unittest.main()

import struct
import unittest
from unittest import mock

import helpers
from riftstone import params, typemap, xfs, yamlish
from riftstone.errors import FormatError, ParamError


class ParamsTest(unittest.TestCase):
    def setUp(self):
        self.x = helpers.sample_xfs()
        self.raw = xfs.build(self.x)
        self.text = params.to_yaml(self.x, name="param\\test", type_id=0x215896C2)

    def test_roundtrip_is_exact(self):
        self.assertEqual(xfs.build(params.from_yaml(self.text)), self.raw)

    def test_custom_classes_carry_a_schema(self):
        # classes the game does not have keep their hash as a name and travel with their layout
        self.assertIn("schema:", self.text)
        self.assertIn(f'_class: "0x{helpers.CHILD.type_id:08x}"', self.text)
        self.assertIn("- [mValue, f32, 0x00, 4]", self.text)

    def test_vanilla_classes_need_no_schema(self):
        db = params.schema_db()
        x = xfs.Xfs(256, [db["rStatusParam"], db["cStatusParam"]], xfs.Obj(0, [[2], [
            xfs.Obj(1, [[1], [0], [1], [180.0], [5.0], [1.0]])]]))
        text = params.to_yaml(x, "param\\status\\enemy", 0x215896C2)
        self.assertNotIn("schema:", text)
        self.assertIn("_class: rStatusParam", text)
        self.assertEqual(xfs.build(params.from_yaml(text)), xfs.build(x))

    def test_duplicate_property_names(self):
        self.assertIn("mDup: 7", self.text)
        self.assertIn("mDup#2: 8", self.text)

    def test_readable_values(self):
        for needle in ("mFloat: 0.5", "mFlag: true", "mInt: 3735928559", "mName: hello world",
                       "mTag: テスト", 'mNames:\n    - ""\n    - "a: b # c"\n    - "null"', "mNone: null",
                       "mFloats: [0.125, -2.5, 3.4028235e38]", "mRange16:\n    - [1, 65535]\n    - [0, 7]",
                       "type: rSoundRequest", "path: sound\\se\\em\\e07\\e0700\\e0700", "Japanese 名前: 9"):
            self.assertIn(needle, self.text)

    def edit(self, old, new):
        self.assertIn(old, self.text)
        return params.from_yaml(self.text.replace(old, new, 1), "t.yaml")

    def test_edit_values(self):
        x = self.edit("mFloat: 0.5", "mFloat: 12.75")
        self.assertEqual(x.root.fields[9], [12.75])
        x = self.edit("mFloat: 0.5", "mFloat: 3")          # integers are fine in float fields
        self.assertEqual(x.root.fields[9], [3.0])
        x = self.edit("mInt: 3735928559", "mInt: 0x10")
        self.assertEqual(x.root.fields[3], [16])
        x = self.edit("mFlag: true", "mFlag: false")
        self.assertEqual(x.root.fields[0], [0])
        raw = xfs.build(self.edit("mFloat: 0.5", "mFloat: 0.1"))
        self.assertIn(struct.pack("<f", 0.1), raw)
        self.assertIn(b"hello world\0", raw)

    def test_add_and_remove_list_items(self):
        x = self.edit("mFloats: [0.125, -2.5, 3.4028235e38]", "mFloats: [1.0]")
        self.assertEqual(x.root.fields[30], [1.0])
        x = self.edit("mFloats: [0.125, -2.5, 3.4028235e38]", "mFloats: []")
        self.assertEqual(x.root.fields[30], [])
        y = xfs.parse(xfs.build(x))                           # object numbering is recomputed
        self.assertEqual(y.root.fields[30], [])

    def err(self, old, new, needle, line_check=True):
        with self.assertRaises(ParamError) as cm:
            self.edit(old, new)
        self.assertIn(needle, str(cm.exception))
        if line_check:
            self.assertIsNotNone(cm.exception.line, str(cm.exception))

    def test_errors_explain_themselves(self):
        self.err("mByte: 200", "mByte: 256", "between 0 and 255")
        self.err("mSByte: -100", "mSByte: -129", "between -128 and 127")
        self.err("mFloat: 0.5", "mFloat: 1e39", "too large for a 32-bit float")
        self.err("mFloat: 0.5", "mFloat: fast", "must be a number")
        self.err("mInt: 3735928559", "mInt: 1.5", "whole number")
        self.err("mFloat: 0.5", "mFlaot: 0.5", "Did you mean mFloat")
        self.err("mFloats: [0.125, -2.5, 3.4028235e38]", "mFloats: 1.0", "is a list")
        self.err("mPos: [1.0, 2.0, 3.0, 0.0]", "mPos: [1.0, 2.0]", "takes 4 numbers")
        self.err("mFlag: true", "mFlag: maybe", "true/false")
        self.err(f'_class: "0x{helpers.ROOT.type_id:08x}"\n', "_class: cStatusParamm\n",
                 "unknown class 'cStatusParamm'. Did you mean cStatusParam")
        self.err("mName: hello world", 'mName: "a\\0b"', "NUL")
        self.err("riftstone: xfs/1", "riftstone: xfs/9", "not a Riftstone parameter file")
        self.err("version: 4", "version: -1", "between 0 and 65535")

    def test_huge_numbers_are_refused_in_a_short_message(self):
        # a hex number thousands of digits long parses (base 16 has no digit limit), and writing it in
        # decimal for the message raised ValueError ("Exceeds the limit (4300 digits)") instead
        huge = "0x" + "F" * 3600
        for old, new in (("mInt: 3735928559", f"mInt: {huge}"), ("mInt: 3735928559", "mInt: " + "9" * 5000),
                         ("mRange16:\n    - [1, 65535]", f"mRange16:\n    - [1, {huge}]"),
                         ("version: 4", f"version: {huge}")):
            with self.subTest(new=new[:20]):
                with self.assertRaises(ParamError) as cm:
                    self.edit(old, new)
                self.assertLess(len(str(cm.exception)), 200)

    def test_a_resource_name_in_any_script(self):
        # the name (riftstone param passes the file's stem) was encoded as Latin-1 and read back as UTF-8:
        # "テスト.stm" crashed with UnicodeEncodeError and "é" came out as \udce9
        raw = xfs.build(helpers.sample_xfs())
        ext = typemap.extension(0x215896C2)
        for name in ("テスト", "é", "param\\pl\\x", "a: b # c"):
            with self.subTest(name=name):
                text = params.resource_to_yaml(raw, name, 0x215896C2)
                self.assertEqual(yamlish.parse(text).get("resource").text, f"{name}.{ext}")
                self.assertEqual(xfs.build(params.from_yaml(text)), raw)
        self.assertIn(f"resource: param\\pl\\x.{ext}\n", params.resource_to_yaml(raw, "param\\pl\\x", 0x215896C2))

    def test_missing_property(self):
        with self.assertRaises(ParamError) as cm:
            params.from_yaml(self.text.replace("  mShort: 65000\n", ""), "t.yaml")
        self.assertIn("missing 'mShort'", str(cm.exception))

    def test_float_formatting(self):
        for v in (0.0, -0.0, 0.5, 500.0, 1 / 3, 1e-45, 3.4028234663852886e38, 123456.79, float("inf")):
            f32 = struct.unpack("<f", struct.pack("<f", v))[0]
            text = params.fmt_f32(f32)
            back = params._parse_float(params.Scalar(text), True, "v", lambda m, n: AssertionError(m))
            self.assertEqual(struct.pack("<f", back), struct.pack("<f", f32), text)

    def test_schema_section_is_validated(self):
        # fuzzer finding: attr 300 reached the binary writer and crashed it
        for old, new, needle in (("- [mFlag, bool, 0x00, 1]", "- [mFlag, bool, 300, 1]", "attr"),
                                 ("- [mFlag, bool, 0x00, 1]", "- [mFlag, bool, 0x00, 4]", "1 bytes"),
                                 ("- [mFlag, bool, 0x00, 1]", "- [mFlag, bool, 0x00, 70000]", "size"),
                                 ("engine_value: 120", "engine_value: 4294967296", "engine_value"),
                                 ("- [mFlag, bool, 0x00, 1]", "- [mFlag, boolean, 0x00, 1]", "unknown property type")):
            with self.assertRaises(ParamError, msg=new) as cm:
                params.from_yaml(self.text.replace(old, new, 1))
            self.assertIn(needle, str(cm.exception))

    def test_strings_keep_edge_whitespace(self):
        # fuzzer finding: "$" in the plain-scalar regex let a trailing newline through unquoted
        for s in (b"hello\n", b"\n", b"a\nb", b" lead", b"trail ", b"tab\t", b"\r", "トレーニング\n".encode()):
            x = helpers.sample_xfs()
            x.root.fields[11] = [s]
            raw = xfs.build(x)
            self.assertEqual(xfs.build(params.from_yaml(params.to_yaml(xfs.parse(raw)))), raw, s)

    def test_a_number_too_large_for_a_float_is_not_infinity(self):
        # f32_bits (the YAML of flat, DDO collision and the other binary formats) stored "1e400" as +inf,
        # though "1e39" was refused and XFS files refused both; infinity is written .inf
        for text in ("1e400", "-1e400", "1e39", "infinity"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                params.f32_bits(text)
        for text, bits in ((".inf", 0x7F800000), ("-.inf", 0xFF800000), ("+inf", 0x7F800000), ("-inf", 0xFF800000)):
            self.assertEqual(params.f32_bits(text), bits)
        from riftstone import flat
        good = flat.to_yaml(flat.Flat("ajp", 0, {"version": 1, "mpArray": [1.0]}))
        with self.assertRaises(ParamError):
            flat.yaml_to_bytes(good.replace("mpArray: [1.0]", "mpArray: [1e400]"), "a.yaml")
        self.err("mFloat: 0.5", "mFloat: 1" + "0" * 5000, "too large")      # a long number is quoted short
        with self.assertRaises(ParamError) as cm:
            self.edit("mFloat: 0.5", "mFloat: 1" + "0" * 5000)
        self.assertLess(len(str(cm.exception)), 200)

    def test_nan_literals_are_validated(self):
        for bad in ("nan:0x7fa00000-0.0", "nan:0x3f800000", "nan:0x1ffffffff", "nan:0xzz"):
            with self.assertRaises(ParamError, msg=bad):
                self.edit("mFloat: 0.5", f"mFloat: {bad}")
        x = self.edit("mFloat: 0.5", "mFloat: nan:0x7fa00001")
        self.assertEqual(x.root.fields[9], [xfs.F32Bits(0x7FA00001)])

    def test_schema_class_names_must_be_ascii(self):
        text = self.text.replace(f'"0x{helpers.CHILD.type_id:08x}":\n', '"クラス":\n')
        text = text.replace(f'_class: "0x{helpers.CHILD.type_id:08x}"', '_class: "クラス"')
        with self.assertRaises(ParamError) as cm:
            params.from_yaml(text)
        self.assertIn("ASCII", str(cm.exception))

    def test_parsed_floats_are_float32_values(self):
        x = self.edit("mFloat: 0.5", "mFloat: 0.1")
        self.assertEqual(x.root.fields[9], [struct.unpack("<f", struct.pack("<f", 0.1))[0]])

    def test_non_utf8_text_is_a_clear_error(self):
        with self.assertRaises(ParamError) as cm:
            params.decode_text(b"a: 1\nb: \xfe\n", "x.yaml")
        self.assertEqual(cm.exception.line, 2)

    def test_shipped_schema_loads(self):
        db = params.schema_db()
        self.assertGreaterEqual(len(db), 540)
        self.assertIn("rAIFSM", db)
        self.assertIn("rStatusParam", db)


def _cls(name, *props):
    return xfs.ClassDef(typemap.jamcrc(name), 0x10, tuple(props))


class EveryParsedTreeConvertsTest(unittest.TestCase):
    """What xfs.parse accepts, to_yaml writes and from_yaml reads back: riftstone param's output compiles."""

    def round_trip(self, x: xfs.Xfs) -> str:
        raw = xfs.build(x)
        text = params.to_yaml(xfs.parse(raw), "t")
        self.assertEqual(xfs.build(params.from_yaml(text, "t.yaml")), raw)
        return text

    def test_the_deepest_tree(self):
        # parse took objects 256 deep and YAML holds 200 levels: a 200-deep chain converted, then from_yaml
        # refused it.  An object in a list takes two levels, and so does a list of resource references.
        node = _cls("rDeep", helpers.prop("mNext", "classref"), helpers.prop("mKids", "classref", 0xA0),
                    helpers.prop("mRefs", "resource", 0xA0))
        ref = xfs.ResourceRef(b"rTexture", b"a\\b")
        for in_list in (False, True):
            with self.subTest(in_list=in_list):
                o = xfs.Obj(0, [[None], [], [ref, ref]])
                for _ in range(xfs.MAX_DEPTH):
                    o = xfs.Obj(0, [[None], [o], []] if in_list else [[o], [], []])
                self.round_trip(xfs.Xfs(1, [node], o))
                with mock.patch.object(xfs, "MAX_DEPTH", xfs.MAX_DEPTH + 1):   # one level more: parse refuses
                    raw = xfs.build(xfs.Xfs(1, [node], xfs.Obj(0, [[o], [], []])))
                with self.assertRaises(FormatError):
                    xfs.parse(raw)

    def test_properties_named_like_a_key(self):
        # a property named _class was written beside the object's own _class line (a duplicate key), and a
        # repeated name could take the key of a property named name#N
        odd = _cls("rOdd", helpers.prop("_class", "u32"), helpers.prop("a", "u32"), helpers.prop("a#2", "u32"),
                   helpers.prop("a", "u32"), helpers.prop("a", "u32"), helpers.prop("_class", "u32"))
        text = self.round_trip(xfs.Xfs(1, [odd], xfs.Obj(0, [[1], [2], [3], [4], [5], [6]])))
        self.assertIn("_class#2: 1\n", text)
        self.assertEqual(params.prop_keys(odd), ["_class#2", "a", "a#2", "a#3", "a#4", "_class#3"])

    def test_the_most_objects(self):
        # parse numbers objects 0..65535 (65,536 of them), but build and from_yaml stopped at 65,535
        many = _cls("rMany", helpers.prop("mKids", "classref", 0xA0))
        x = xfs.Xfs(1, [many, _cls("rLeaf")], xfs.Obj(0, [[xfs.Obj(1, []) for _ in range(0xFFFF)]]))
        text = self.round_trip(x)
        leaf = next(line for line in text.splitlines(True) if line.lstrip().startswith("- _class:"))
        with self.assertRaises(ParamError) as cm:           # one more cannot be numbered
            params.from_yaml(text.replace(leaf, leaf * 2, 1), "t.yaml")
        self.assertIn("more than 65536 objects", str(cm.exception))
        x.root.fields[0].append(xfs.Obj(1, []))
        with self.assertRaises(FormatError):
            xfs.build(x)


if __name__ == "__main__":
    unittest.main()

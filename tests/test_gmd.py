import struct
import unittest

import helpers  # noqa: F401 (sys.path)
from riftstone import gmd, inspect, params
from riftstone.errors import FormatError, ParamError


def make_gmd(messages, labels=(), lang=1, name=b"TextWeb", base=0x3B2F0DE0) -> bytes:
    """The game's layout, written independently of gmd.build so the tests check the parser against it."""
    lblock = b"".join(lab + b"\0" for _, lab in labels)
    mblock = b"".join(m + b"\0" for m in messages)
    out = struct.pack("<4sII8sIIIII", b"GMD\0", 0x10201, lang, bytes(8), len(labels), len(messages),
                      len(lblock), len(mblock), len(name)) + name + b"\0"
    off = 0
    for index, lab in labels:
        out += struct.pack("<II", index, base + off)
        off += len(lab) + 1
    return out + lblock + mblock


GRIGORI = make_gmd([b"What is it, Arisen?", b"", b"I shall meet you,\r\njoined by my companion.",
                    "Erweckte{Herr}{Herrin} \u00e9".encode()], labels=[(0, b"0"), (2, b"2")])


class GmdTest(unittest.TestCase):
    def test_parse_build_exact(self):
        for raw in (GRIGORI, make_gmd([]), make_gmd([b"Courez !"], lang=2), make_gmd([b"a", b"b"], labels=[(1, b"x")])):
            g = gmd.parse(raw)
            self.assertEqual(gmd.build(g), raw)
        g = gmd.parse(GRIGORI)
        self.assertEqual([m.label for m in g.messages], ["0", None, "2", None])
        self.assertEqual(g.messages[2].text, "I shall meet you,\r\njoined by my companion.")
        self.assertEqual(g.label_base, 0x3B2F0DE0)
        self.assertEqual(g.language_name, "english")

    def test_yaml_roundtrip_exact(self):
        for raw in (GRIGORI, make_gmd([]), make_gmd([b"x\ny\rz"], lang=7)):
            text = params.resource_to_yaml(raw, "message\\test_eng")
            self.assertIn("riftstone: gmd/1", text)
            self.assertEqual(params.yaml_to_resource(text, "t.gmd.yaml"), raw)

    def test_edit_and_add_at_the_end(self):
        text = gmd.to_yaml(gmd.parse(GRIGORI), "t")
        text = text.replace('"What is it, Arisen?"', '"Well met, Arisen."')
        text += '  - text: "A brand new line."\n  - label: "new"\n    text: "Labelled too."\n'
        g = gmd.parse(gmd.yaml_to_bytes(text, "t"))
        self.assertEqual(g.messages[0].text, "Well met, Arisen.")
        self.assertEqual(g.messages[4].text, "A brand new line.")
        self.assertEqual((g.messages[5].label, g.messages[5].text), ("new", "Labelled too."))
        self.assertEqual(g.messages[1].text, "")        # untouched messages stay put
        self.assertEqual(g.label_base, 0x3B2F0DE0)      # pointers stay consistent with the original base

    def test_moved_ids_are_refused(self):
        text = gmd.to_yaml(gmd.parse(GRIGORI), "t")
        # delete message 1 in the middle: everything after it would be renumbered
        lines = text.split("\n")
        i = lines.index("  - id: 1")
        del lines[i:i + 2]
        with self.assertRaises(ParamError) as cm:
            gmd.yaml_to_bytes("\n".join(lines), "t.yaml")
        self.assertIn("Ids must not move", str(cm.exception))
        self.assertIsNotNone(cm.exception.line)

    def test_language_by_name_or_number(self):
        text = gmd.to_yaml(gmd.parse(GRIGORI), "t")
        self.assertEqual(gmd.parse(gmd.yaml_to_bytes(text.replace("language: english", "language: german"))).language, 4)
        self.assertEqual(gmd.parse(gmd.yaml_to_bytes(text.replace("language: english", "language: 6"))).language, 6)
        with self.assertRaises(ParamError):
            gmd.yaml_to_bytes(text.replace("language: english", "language: klingon"))

    def test_text_that_cannot_be_written_is_a_clean_error(self):
        base = 'riftstone: gmd/1\nlanguage: english\nmessages:\n'
        for bad in ('  - text: "a\\0b"\n', '  - text: "\\ud800"\n', '  - label: "x\\0"\n    text: "t"\n',
                    '  - text: [1, 2]\n', '  - {id: x, text: "t"}\n', '  - junk: 1\n'):
            with self.assertRaises(ParamError, msg=bad) as cm:
                gmd.yaml_to_bytes(base + bad, "t.yaml")
            self.assertIn("t.yaml", str(cm.exception))

    def test_malformed_binaries_are_refused(self):
        good = GRIGORI
        cases = {
            "short": good[:20],
            "magic": b"GMX\0" + good[4:],
            "version": good[:4] + struct.pack("<I", 0x10202) + good[8:],
            "size": good + b"\0",
            "labels out of order": make_gmd([b"a", b"b"], labels=[(1, b"x"), (0, b"y")]),
            "label past the end": make_gmd([b"a"], labels=[(3, b"x")]),
            "not utf-8": make_gmd([b"\xff\xfe"]),
            "unterminated name": good[:0x28] + b"TextWebX" + good[0x30:],
        }
        broken_ptr = bytearray(make_gmd([b"a", b"b"], labels=[(0, b"x"), (1, b"y")]))
        struct.pack_into("<I", broken_ptr, 0x30 + 12, 0x1234)
        cases["pointer"] = bytes(broken_ptr)
        for what, raw in cases.items():
            with self.assertRaises(FormatError, msg=what):
                gmd.parse(raw)

    def test_language_of_suffix(self):
        self.assertEqual(gmd.language_of("message\\event\\st610ev12_eng"), 1)
        self.assertEqual(gmd.language_of("x_zht"), 7)
        self.assertIsNone(gmd.language_of("TextWeb"))

    def test_tag_line_decides_format(self):
        text = gmd.to_yaml(gmd.parse(GRIGORI), "t")
        self.assertEqual(params.yaml_tag(text), "gmd/1")
        # a comment that mentions another tag cannot redirect the file
        self.assertEqual(params.yaml_tag("# riftstone: ocl/1\nriftstone: gmd/1\n"), "gmd/1")
        self.assertEqual(params.yaml_tag("root: {}\n"), params.TAG)

    def test_inspect_lists_messages(self):
        rep = inspect.describe(GRIGORI, 0)
        self.assertTrue(rep.editable)
        out = rep.text("t")
        self.assertIn("4 message(s) in english", out)
        self.assertIn("[2]  I shall meet you, / joined by my companion.", out)


if __name__ == "__main__":
    unittest.main()

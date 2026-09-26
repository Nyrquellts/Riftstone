import random
import unittest

import helpers  # noqa: F401
from riftstone import yamlish
from riftstone.errors import ParamError
from riftstone.yamlish import Map, Scalar, Seq


def plain(node):
    """Node tree -> python values (scalars as text) for easy comparison."""
    if isinstance(node, Scalar):
        return node.text
    if isinstance(node, Seq):
        return [plain(x) for x in node.items]
    return {k.text: plain(v) for k, v in node.items}


class ParseTest(unittest.TestCase):
    def p(self, text):
        return plain(yamlish.parse(text))

    def test_block_structures(self):
        text = """# header
---
a: 1
b:
  c: two words
  d: 'quoted '' single'
  e: "esc \\t \\x41 \\u00e9"
list:
  - x
  - y: 1
    z: [1, 2.5, -3]
  -
    nested: true
same_indent_list:
- a
- b
"""
        self.assertEqual(self.p(text), {
            "a": "1", "b": {"c": "two words", "d": "quoted ' single", "e": "esc \t A é"},
            "list": ["x", {"y": "1", "z": ["1", "2.5", "-3"]}, {"nested": "true"}],
            "same_indent_list": ["a", "b"]})

    def test_comments_and_quotes(self):
        self.assertEqual(self.p("a: 'x # not a comment' # comment\nb: c#d\n"), {"a": "x # not a comment", "b": "c#d"})
        self.assertEqual(self.p("a: Dragon's Dogma\n"), {"a": "Dragon's Dogma"})
        self.assertEqual(self.p("\"key: odd\": 1\n'k2': 2\n"), {"key: odd": "1", "k2": "2"})

    def test_flow(self):
        self.assertEqual(self.p("a: [[1, 2], [3, 4]]\nb: []\nc: {type: rX, path: 'a\\b'}\nd: {}\n"),
                         {"a": [["1", "2"], ["3", "4"]], "b": [], "c": {"type": "rX", "path": "a\\b"}, "d": {}})
        self.assertEqual(self.p("a: [1,\n  2,\n  3]\nb: 4\n"), {"a": ["1", "2", "3"], "b": "4"})
        self.assertEqual(self.p("a: [x: y]\n"), {"a": ["x: y"]})

    def test_nested_compact_seq(self):
        self.assertEqual(self.p("- - a\n  - b\n- c\n"), [["a", "b"], "c"])

    def test_styles_kept(self):
        doc = yamlish.parse("a: null\nb: 'null'\nc: \"null\"\n")
        self.assertEqual([v.style for _, v in doc.items], ["plain", "single", "double"])

    def test_positions(self):
        doc = yamlish.parse("a:\n  b: 1\n  c: [1, 2]\n")
        b = doc.get("a").get("b")
        self.assertEqual((b.line, b.col), (2, 6))

    def err(self, text, line=None, needle=""):
        with self.assertRaises(ParamError) as cm:
            yamlish.parse(text, "t.yaml")
        if line is not None:
            self.assertEqual(cm.exception.line, line, str(cm.exception))
        self.assertIn(needle, str(cm.exception))

    def test_errors(self):
        self.err("", needle="empty")
        self.err("a: 1\n\tb: 2\n", 2, "tab")
        self.err("a: 1\na: 2\n", 2, "duplicate")
        self.err("a: &x 1\n", 1, "anchors")
        self.err("a: *x\n", 1, "aliases")
        self.err("a: !tag 1\n", 1, "tags")
        self.err("a: |\n  text\n", 1, "block scalars")
        self.err("a: 1\n  b: 2\n", 2)
        self.err("a: [1, 2\n", 1, "unclosed")
        self.err("a: 'open\n", 1, "unterminated")
        self.err("a: \"\\q\"\n", 1, "escape")
        self.err("a: one\n   two\n", 2, "continue")
        self.err("a:\nb: 1\n", 1, "no value")
        self.err("  a: 1\n", 1, "indented")
        self.err("a: 1\n---\nb: 2\n", 2, "one document")
        self.err("a: [1 2] x\n", 1)
        self.err("-\n", 1, "empty list item")

    def test_depth_limit(self):
        self.err("a: " + "[" * 300 + "]" * 300 + "\n", needle="deeper")
        deep = "".join(" " * (2 * i) + f"k{i}:\n" for i in range(yamlish.MAX_DEPTH + 5)) + " " * (2 * (yamlish.MAX_DEPTH + 5)) + "v: 1\n"
        self.err(deep, needle="deeper")

    def test_unclosed_flow_is_linear(self):
        # a huge unclosed flow list used to re-scan the whole buffer each line (O(n^2), seconds);
        # now it is refused quickly.  Correctness, not timing: an unclosed bracket is a clean error.
        import time
        big = "a: [" + "1,\n" * 8000
        t = time.perf_counter()
        self.err(big, needle="unclosed")
        self.assertLess(time.perf_counter() - t, 1.0)
        # a large *valid* flow list still round-trips
        node = yamlish.parse("a: [" + ", ".join(str(i) for i in range(4000)) + "]\n", "x")
        self.assertEqual(len(node.get("a").items), 4000)


class EmitTest(unittest.TestCase):
    def test_quote_rules(self):
        for text in ("null", "true", "no", "1", "1.5", "-2e3", "0x1f", ".inf", "", " lead", "trail ", "a: b",
                     "a #b", "- x", "-", "#x", "'q'", '"d"', "[x]", "{x}", "&a", "*a", "!t", "|", ">", "%p",
                     "@x", "`x", "tab\tin", "new\nline", "\x00nul", "ok:"):
            quoted = yamlish.quote(text)
            doc = yamlish.parse(f"k: {quoted}\n")
            self.assertEqual(doc.get("k").text, text, quoted)
            if quoted == text:
                self.assertEqual(doc.get("k").style, "plain")
        self.assertEqual(yamlish.quote("rSoundRequest"), "rSoundRequest")
        self.assertEqual(yamlish.quote("sound\\se\\em"), "sound\\se\\em")
        self.assertEqual(yamlish.quote("性格数"), "性格数")
        self.assertNotEqual(yamlish.quote("a,b", flow=True), "a,b")
        # fuzz finding yaml-invariant-6afd4f754f58: a key starting with a BOM was written plain; as the
        # first line of the emitted file the reader dropped the BOM and saw a comment.  Now quoted.
        key = "﻿# Numbers"
        self.assertEqual(yamlish.quote(key), '"\\ufeff# Numbers"')
        out = yamlish.emit(yamlish.Map([(yamlish.Scalar(key, "double"), yamlish.Scalar("x", "plain"))]))
        self.assertEqual(yamlish.parse(out).items[0][0].text, key)

    def test_random_trees_roundtrip(self):
        rng = random.Random(7)
        words = ["a", "b c", "null", "1", "x: y", "é", "#", "'", '"', "\\", "- z", "[", "}", "", " s ", "ok"]

        def scalar():
            t = rng.choice(words) + rng.choice(words)
            q = yamlish.quote(t)
            return Scalar(t, "plain" if q == t else "double")

        def tree(d):
            r = rng.random()
            if d > 4 or r < 0.3:
                return scalar()
            if r < 0.55:
                return Seq([scalar() for _ in range(rng.randint(0, 4))], flow=True)
            if r < 0.75:
                return Seq([tree(d + 1) for _ in range(rng.randint(1, 4))])
            keys, items = set(), []
            for _ in range(rng.randint(1, 4)):
                k = scalar()
                if k.text in keys:
                    continue
                keys.add(k.text)
                items.append((k, tree(d + 1)))
            return Map(items)

        for _ in range(400):
            root = Map([(Scalar("root"), tree(0))])
            text = yamlish.emit(root, ["header"])
            self.assertEqual(plain(yamlish.parse(text)), plain(root), text)


if __name__ == "__main__":
    unittest.main()

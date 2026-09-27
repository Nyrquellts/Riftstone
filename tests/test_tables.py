import struct
import unittest

import helpers  # noqa: F401 (sys.path)
from riftstone import inspect, params, tables, typemap
from riftstone.errors import FormatError, ParamError

N = tables.NONE


def sets(*rows):
    return b"ist\0" + struct.pack("<II", 0, len(rows)) + b"".join(struct.pack("<20H", *r) for r in rows)


def mixes(*rows):
    return b"imx\0" + struct.pack("<II", 4, len(rows)) + b"".join(struct.pack("<4I", *r) for r in rows)


FISHING = (0, N, 2, 1, 10, 1207, 1482, N, N, N, N, N, 60, 38, 2, 0, 0, 0, 0, 0)
ENEMY = (7, N, 0, 1, 1227, 1228, N, N, N, N, N, 283, 30, 8, 35, 0, 0, 0, 0, 27)   # 35% nothing
SETS = sets(FISHING, ENEMY)
MIX = mixes((1740, 60, 131, 30), (1740, 67, 93, 1))


class TablesTest(unittest.TestCase):
    def test_round_trips(self):
        for raw in (SETS, MIX, sets(), mixes()):
            self.assertEqual(tables.build(tables.parse(raw)), raw)
            self.assertEqual(params.yaml_to_resource(params.resource_to_yaml(raw, "t")), raw)
        names = {10: "Small Fish", 1207: "Fishing Bob", 131: "Poison Arrow"}
        text = tables.to_yaml(tables.parse(SETS), "t", [names.get(i, f"item {i}") for i in range(2000)])
        self.assertIn('[10, 60, "Small Fish"]', text)
        self.assertIn("[none, 35]", text)
        self.assertEqual(tables.yaml_to_bytes(text), SETS)                 # the names are only for reading

    def test_edit_a_drop(self):
        text = tables.to_yaml(tables.parse(SETS))
        text = text.replace("[none, 35]", "[1300, 20]", 1)          # part of the 35% "nothing" now drops item 1300
        self.assertIn("[1300, 20]", text)
        t = tables.parse(tables.yaml_to_bytes(text))
        self.assertEqual(t.rows[1][6], 1300)
        self.assertEqual(t.rows[1][14], 20)

    def test_recipes(self):
        text = tables.to_yaml(tables.parse(MIX), "m", [f"i{i}" for i in range(2000)])
        self.assertIn("count: 30", text)
        self.assertIn('makes: [131, "i131"]', text)
        text = text.replace("count: 30", "count: 45")
        self.assertEqual(tables.parse(tables.yaml_to_bytes(text)).rows[0], (1740, 60, 131, 45))
        self.assertEqual(tables.yaml_to_bytes(text + '  - mix: [1, 2]\n    makes: [3]\n    count: 2\n')[-16:],
                         struct.pack("<4I", 1, 2, 3, 2))

    def test_refusals(self):
        with self.assertRaises(FormatError):
            tables.parse(SETS[:-1])
        with self.assertRaises(FormatError):
            tables.parse(b"xyz\0" + SETS[4:])
        good = tables.to_yaml(tables.parse(SETS))
        for bad in (good.replace("[10, 60]", "[10, none]"), good.replace("[10, 60]", "[10, -1]"),
                    good.replace("[10, 60]", "[10, 70000]"), good.replace("[10, 60]", "[10]"),
                    good.replace("[10, 60]", "[x, 60]"), good.replace("f04: 2", "f04: two"),
                    good.replace("    f06: 1\n", "    f06: 1\n    colour: red\n", 1),
                    good.replace(", [none, 0]]", "]", 1)):                          # only 7 slots
            with self.assertRaises(ParamError, msg=bad[-300:]):
                tables.yaml_to_bytes(bad, "t.yaml")
        m = tables.to_yaml(tables.parse(MIX))
        for bad in (m.replace("count: 30", "count: -1"), m.replace("makes: [131]", "makes: [131, 1]")):
            with self.assertRaises(ParamError):
                tables.yaml_to_bytes(bad, "m.yaml")

    def test_unknown_top_level_keys_are_refused(self):
        # was: passed over, so a misspelled version wrote version 0 without a word
        m = tables.to_yaml(tables.parse(MIX))
        self.assertIn("version: 4\n", m)
        for bad in (m.replace("version: 4\n", "verison: 4\n"), m.replace("version: 4\n", "version: 4\ncolour: red\n"),
                    tables.to_yaml(tables.parse(SETS)).replace("sets:", "recipes: []\nsets:")):
            with self.assertRaises(ParamError) as e:
                tables.yaml_to_bytes(bad, "m.yaml")
            self.assertIn("m.yaml: line ", str(e.exception))

    def test_inspect(self):
        out = inspect.describe(SETS, typemap.BY_EXT["ist"]).text("x")
        self.assertIn("set     7  1227:30, 1228:8, none:35, 283:27", out)
        self.assertIn("1740 + 60    makes   131 x30", inspect.describe(MIX, typemap.BY_EXT["imx"]).text("x"))


if __name__ == "__main__":
    unittest.main()

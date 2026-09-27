"""The item record's named fields (itl.FIELDS) and an item's stats at each enhancement level, computed as DDDA.exe's
0x0045B620 does (itemstats.py)."""
import os
import struct
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401 (sys.path)
import test_items
from riftstone import arc, cli, flat, itemstats, itl, typemap
from riftstone.errors import ParamError, RiftError
from riftstone.game import Game
from riftstone.index import Index
from riftstone.mod import Mod

setUpModule, tearDownModule = helpers.module_env("RIFTSTONE_HOME")

ITEMLV = typemap.BY_EXT["itemlv"]
F32 = lambda x: struct.unpack("<I", struct.pack("<f", x))[0]  # noqa: E731


def lv_row(entries: dict) -> dict:
    """A level-table row: {level: [(kind, rate, direct), ...]}; the rest empty as the game's are (-1, 2.0, 0)."""
    row = {}
    for lv, n in enumerate(itemstats.ENTRIES, 1):
        got = entries.get(lv, [])
        row[f"mUpParamLv{lv}"] = [k for k, _, _ in got] + [-1] * (n - len(got))
        row[f"mUpRate{lv}"] = [F32(r) for _, r, _ in got] + [F32(2.0)] * (n - len(got))
        row[f"mIsDirectValue{lv}"] = [int(d) for _, _, d in got] + [0] * (n - len(got))
    order =[f"mUpParamLv{i}" for i in range(1, 7)] + [f"mUpRate{i}" for i in range(1, 7)] + \
        [f"mIsDirectValue{i}" for i in range(1, 7)]
    return {k: row[k] for k in order}


def lv_table(rows) -> bytes:
    return flat.build(flat.Flat("itemlv", flat.SCHEMAS["itemlv"][0], {"version": 0x1400, "mpArray": list(rows)}))


def weapon(item_id, attack=50, row=1, kind=7, shrink=100):
    r = test_items.record(item_id)
    itl.BY_NAME["mAttack"].set(r, attack)
    itl.BY_NAME["mShrink"].set(r, shrink)
    itl.BY_NAME["mBlow"].set(r, shrink)
    itl.BY_NAME["mKind"].set(r, kind)
    itl.BY_NAME["mLevelUpType"].set(r, row)
    itl.BY_NAME["mEnableEquipJob"].set(r, 0b110000110010)       # as the game's swords: Fighter, Mystic Knight, Assassin
    return r


class FieldsTest(unittest.TestCase):
    def test_every_field_owns_only_its_bits(self):
        base = bytearray(os.urandom(128))
        for fd in itl.FIELDS:
            for value in ((fd.range[0], fd.range[1], 0) if not fd.f32 else (0, 0x7FC00001, F32(-1.5))):
                r = bytearray(base)
                fd.set(r, value)
                self.assertEqual(fd.get(r), value, fd.name)
                others = [(o.name, o.get(base)) for o in itl.FIELDS if o is not fd]
                self.assertEqual([(o.name, o.get(r)) for o in itl.FIELDS if o is not fd], others, fd.name)
                changed = [i for i in range(128) if r[i] != base[i]]
                self.assertTrue(all(fd.word <= i < fd.word + 4 for i in changed), fd.name)
        # no two fields share a bit, and the unnamed bits are the ones the docstring lists
        owned = {}
        for fd in itl.FIELDS:
            for b in range(fd.bit, fd.bit + fd.bits):
                key = (fd.word, b)
                self.assertNotIn(key, owned, f"{fd.name} overlaps {owned.get(key)}")
                owned[key] = fd.name
        free = {(w, b) for w in range(0, 128, 4) for b in range(32)} - owned.keys()
        id_bits = {(0x3C, b) for b in range(13)}
        named_short = {(0x44, b) for b in range(32)} | {(0x48, b) for b in range(32)} | {(0x4C, b) for b in range(32)}
        unnamed = free - id_bits - named_short
        want = ({(0x04, b) for b in range(27, 32)} | {(0x08, b) for b in range(27, 32)}
                | {(0x0C, b) for b in range(30, 32)} | {(0x10, b) for b in range(30, 32)} | {(0x24, b) for b in range(8)}
                | {(0x3C, b) for b in range(26, 32)} | {(0x58, b) for b in range(29, 32)}
                | {(0x6C, b) for b in range(24, 32)} | {(0x7C, b) for b in range(24, 32)})
        self.assertEqual(unnamed, want)

    def test_level_tables_by_kind(self):
        self.assertEqual([itl.level_table(k) for k in (6, 7, 18, 19, 24, 25, 26, 27, 31)],
                         [None, "LvParamWepon", "LvParamWepon", "LvParamArmor", "LvParamArmor", "LvParamAccessory",
                          None, "LvParamArmor", None])


class YamlTest(unittest.TestCase):
    def setUp(self):
        self.raw = itl.build(itl.ItemList(0x01330611, 0, [weapon(0), test_items.record(1), weapon(2, 90, 2, 19)]))

    def test_named_fields_round_trip_and_edit(self):
        text = itl.to_yaml(itl.parse(self.raw))
        self.assertIn("riftstone: itl/2", text)
        self.assertIn("mAttack: 50", text)
        self.assertIn("mKind: 19", text)
        self.assertNotIn("mMagicAttack", text)                     # zero: not shown
        self.assertEqual(itl.yaml_to_bytes(text), self.raw)
        edited = itl.parse(itl.yaml_to_bytes(text.replace("mAttack: 50", "mAttack: 2047", 1)))
        self.assertEqual((itl.BY_NAME["mAttack"].get(edited.records[0]), itl.BY_NAME["mShrink"].get(edited.records[0])),
                         (2047, 100))
        added = text.replace("    raw:", "    mFireDefenseRate: -20\n    mHp: 1.5\n    raw:", 1)
        r = itl.parse(itl.yaml_to_bytes(added)).records[0]
        self.assertEqual((itl.BY_NAME["mFireDefenseRate"].get(r), struct.unpack("<f", r[0x54:0x58])[0]), (-20, 1.5))

    def test_old_files_and_refusals(self):
        old = itl.to_yaml(itl.parse(self.raw)).replace("itl/2", "itl/1")
        self.assertEqual(itl.yaml_to_bytes(old), self.raw)          # itl/1 (raw only) still reads
        text = itl.to_yaml(itl.parse(self.raw))
        for bad, why in (("mAttack: 2048", "between 0 and 2047"), ("mAttack: -1", "between 0 and 2047"),
                         ("mAttack: lots", "whole number"), ("mAttack: [1]", "whole number")):
            with self.assertRaisesRegex(ParamError, why):
                itl.yaml_to_bytes(text.replace("mAttack: 50", bad, 1))
        with self.assertRaisesRegex(ParamError, "engine name"):
            itl.yaml_to_bytes(text.replace("mAttack: 50", "mStrength: 50", 1))
        with self.assertRaises(ParamError):
            itl.yaml_to_bytes(text.replace("    raw:", "    mFireDefenseRate: 128\n    raw:", 1))


class LevelTest(unittest.TestCase):
    def test_the_last_entry_up_to_the_level_sets_the_value(self):
        row = lv_row({1: [(0, 16.0, True)], 2: [(0, 32.0, True), (3, 1.25, False)], 4: [(0, 160.0, True)]})
        self.assertEqual([itemstats.at_level(50.0, row, 0, lv) for lv in range(7)], [50, 66, 82, 82, 210, 210, 210])
        self.assertEqual([itemstats.at_level(100.0, row, 3, lv) for lv in range(4)], [100, 100, 125, 125])
        self.assertEqual(itemstats.at_level(7.0, row, 13, 6), 7.0)             # no entry: the base
        twice = lv_row({1: [(0, 10.0, True), (0, 2.0, False)]})
        self.assertEqual(itemstats.at_level(50.0, twice, 0, 1), 100.0)         # the later entry of a level wins

    def test_stats_of_an_item(self):
        tables = {"LvParamWepon": [lv_row({}), lv_row({1: [(0, 16.0, True)], 2: [(3, 1.5, False), (4, 1.5, False)]})]}
        s = itemstats.stats(weapon(5), 5, tables)
        self.assertEqual((s.table, s.row), ("LvParamWepon", 1))
        self.assertEqual([lv["mAttack"] for lv in s.levels], [50, 66, 66, 66, 66, 66, 66])
        self.assertEqual([lv["mShrink"] for lv in s.levels][:3], [100, 100, 150])
        past = itemstats.stats(weapon(5, row=9), 5, tables)
        self.assertEqual((past.row, [lv["mAttack"] for lv in past.levels][-1]), (None, 50))
        self.assertIn("past the end", past.note)
        none = itemstats.stats(weapon(5, kind=26), 5, tables)
        self.assertEqual((none.table, none.row), (None, None))
        self.assertIn("no enhancement", none.note)
        self.assertEqual(s.jobs, ["Fighter", "Mystic Knight", "Assassin"])
        self.assertEqual(itemstats.jobs_of(0b111111111110), list(itemstats.JOBS))
        self.assertEqual((itemstats.KIND_NAMES[7], itemstats.KIND_NAMES[18], itemstats.KIND_NAMES.get(26)),
                         ("sword", "archistaff", None))
        self.assertTrue(all(itl.level_table(k) for k in itemstats.KIND_NAMES))   # every named kind enhances
        # kind 0x29 scales both knockdown and stagger resistance
        self.assertEqual(itemstats.KINDS[41], ("mBlowDefenseRate", "mShrinkDefenseRate"))
        self.assertTrue(all(n in itl.BY_NAME for names in itemstats.KINDS.values() for n in names))


class CliTest(unittest.TestCase):
    """items stats / items set on a stand-in game with an item list and a weapon level table."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self._home = os.environ.get("RIFTSTONE_HOME")
        os.environ["RIFTSTONE_HOME"] = str(base / "home")
        rom = base / "game" / "nativePC" / "rom"
        rom.mkdir(parents=True)
        (base / "game" / "DDDA.exe").write_bytes(b"stub")
        recs = [weapon(i) if i == 2 else test_items.record(i) for i in range(5)]
        GMD, ITL = typemap.BY_EXT["gmd"], typemap.BY_EXT["itl"]
        entries = [arc.Entry.from_data(b"etc\\item\\itemList", ITL, itl.build(itl.ItemList(0x01330611, 0, recs))),
                   arc.Entry.from_data(b"etc\\item\\LvParamWepon", ITEMLV,
                                       lv_table([lv_row({}), lv_row({1: [(0, 16.0, True)], 2: [(0, 32.0, True)]})]))]
        for lang, suf in ((1, b"eng"), (2, b"fre")):
            for kind in (b"itemName_", b"itemInfo_"):
                entries.append(arc.Entry.from_data(b"id\\message\\item\\" + kind + suf, GMD, test_items.names(lang)))
        (rom / "bbs_rpg.arc").write_bytes(arc.Archive(entries).build())
        self.game = Game(base / "game")
        idx = Index(self.game)
        idx.refresh()
        idx.close()
        self.mod = Mod.create(base / "mods" / "M", "M").root

    def tearDown(self):
        if self._home is None:
            os.environ.pop("RIFTSTONE_HOME", None)
        else:
            os.environ["RIFTSTONE_HOME"] = self._home
        self.tmp.cleanup()

    def run_cli(self, *args):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = cli.main(list(args) + ["--game", str(self.game.root)])
        return code, out.getvalue()

    def test_stats_and_set(self):
        code, out = self.run_cli("items", "stats", "2")
        self.assertEqual(code, 0, out)
        self.assertIn("row 1 of LvParamWepon", out)
        self.assertIn("sword (mKind 7)", out)
        self.assertIn("equipped by Fighter, Mystic Knight, Assassin", out)
        self.assertRegex(out, r"mAttack\s+Strength\s+50\s+66\s+82\s+82")
        code, out = self.run_cli("items", "set", "2", "mAttack=80", "mFireDefenseRate=-5", "--mod", str(self.mod))
        self.assertEqual(code, 0, out)
        self.assertIn("mAttack 50 -> 80", out)
        code, out = self.run_cli("items", "stats", "2", "--mod", str(self.mod))
        self.assertRegex(out, r"mAttack\s+Strength\s+80\s+96\s+112")
        text = (self.mod / "files" / "etc" / "item" / "itemList.itl.yaml").read_text(encoding="utf-8")
        self.assertIn("mFireDefenseRate: -5", text)
        for bad in (["mAttack=2048"], ["mStrength=5"], ["attack=5"], ["mAttack=x"], ["mHp=big"], ["mAttack"]):
            code, out = self.run_cli("items", "set", "2", *bad, "--mod", str(self.mod))
            self.assertNotEqual(code, 0, bad)
        self.assertIn("did you mean mAttack", self.run_cli("items", "set", "2", "attack=5", "--mod", str(self.mod))[1])
        code, out = self.run_cli("items", "set", "2", "mAttack=1")          # no --mod
        self.assertNotEqual(code, 0)


if __name__ == "__main__":
    unittest.main()

"""The local DDO server's spawn table and shops that several Online mods change, merged at install
(servermerge.py): every mod's rows, drops and goods kept, disagreements to the later mod and reported."""
import copy
import json
import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import helpers  # noqa: F401 (sys.path)
from riftstone import ddo, install, servermerge
from riftstone.errors import RiftError
from riftstone.game import Game
from riftstone.mod import Mod

SCHEMA = ["StageId", "LayerNo", "GroupId", "PositionIndex", "EnemyId", "Lv", "DropsTableId"]


def row(stage, group, pos, enemy="0x010100", lv=3, drops=2):
    return [stage, 0, group, pos, enemy, lv, drops]


def spawn_table() -> dict:
    return {"schemas": {"enemies": SCHEMA, "dropsTables.items": ["ItemId", "ItemNum", "MaxItemNum", "Quality",
                                                                   "IsHidden", "DropChance"]},
            "dropsTables": [{"id": 2, "name": "Goblin", "mdlType": 0, "items": [[7750, 1, 1, 0, False, 0.8]]},
                            {"id": 3, "name": "Wolf", "mdlType": 0, "items": [[7751, 1, 1, 0, False, 0.5]]}],
            "enemies": [row(5, 1, 0), row(5, 1, 1), row(5, 2, 0, "0x010200"), row(7, 1, 0), row(7, 1, 1)]}


def shops() -> list:
    def good(i, item, price):
        return {"Index": i, "ItemId": item, "Price": price, "Stock": 255, "Unk4": False, "Unk5": 0, "Unk6": 0, "Unk7": []}
    return [{"ShopId": 223, "Data": {"Unk0": 0, "Unk1": 0, "WalletType": 11,
                                     "GoodsParamList": [good(0, 34, 7), good(1, 35, 25), good(2, 36, 40)]}},
            {"ShopId": 52, "Data": {"Unk0": 0, "Unk1": 0, "WalletType": 1, "GoodsParamList": [good(0, 40, 5)]}}]


SPAWN_STYLE, SHOP_STYLE = (2, ""), (4, "\n")


def spawn_bytes(doc) -> bytes:
    return ddo.dumps_style(doc, SPAWN_STYLE)


def shop_bytes(doc) -> bytes:
    return ddo.dumps_style(doc, SHOP_STYLE)


class SpawnMergeTest(unittest.TestCase):
    def merge(self, *docs):
        out, fights = servermerge.merge("EnemySpawn.json", spawn_bytes(spawn_table()),
                                        [(f"M{i}", spawn_bytes(d)) for i, d in enumerate(docs)])
        return json.loads(out), fights, out

    def test_encounters_in_two_mods_both_arrive_where_they_were_put(self):
        a, b = spawn_table(), spawn_table()
        a["enemies"][2:2] = [row(5, 1, 2, "0x015850"), row(5, 1, 3, "0x015850")]    # after stage 5 group 1
        b["enemies"][5:5] = [row(7, 1, 2, "0x010300")]                             # after stage 7 group 1
        ddo.drop_add(b, 2, 35, 0.25)
        doc, fights, raw = self.merge(a, b)
        want = spawn_table()["enemies"]
        want[2:2] = a["enemies"][2:4]
        want.append(row(7, 1, 2, "0x010300"))
        self.assertEqual((doc["enemies"], fights), (want, []))
        self.assertEqual(doc["dropsTables"][0]["items"], [[7750, 1, 1, 0, False, 0.8], [35, 1, 1, 0, False, 0.25]])
        self.assertFalse(raw.endswith(b"\n"))                                     # the server's own style

    def test_rows_added_at_one_place_all_go_in(self):
        a, b, c = spawn_table(), spawn_table(), spawn_table()
        a["enemies"][2:2] = [row(5, 1, 2, "0x015850")]
        b["enemies"][2:2] = [row(5, 1, 2, "0x010300")]
        c["enemies"][2:2] = [row(5, 1, 2, "0x015850")]                            # the same as a's: once
        doc, fights, _ = self.merge(a, b, c)
        self.assertEqual(doc["enemies"][2:4], [row(5, 1, 2, "0x015850"), row(5, 1, 2, "0x010300")])
        self.assertEqual((len(doc["enemies"]), fights), (7, []))

    def test_one_row_changed_two_ways_goes_to_the_later_mod(self):
        a, b = spawn_table(), spawn_table()
        a["enemies"][3][5] = 20
        b["enemies"][3][5] = 30
        b["enemies"][0][5] = 9                                                    # and something only b changes
        doc, fights, _ = self.merge(a, b)
        self.assertEqual((doc["enemies"][3][5], doc["enemies"][0][5]), (30, 9))
        self.assertEqual(fights, [("M0", "M1", "spawn rows 4-4 of the server's table")])

    def test_a_removed_row_and_additions_elsewhere(self):
        a, b = spawn_table(), spawn_table()
        del a["enemies"][4]
        b["enemies"].insert(1, row(5, 1, 9, "0x010300"))
        doc, fights, _ = self.merge(a, b)
        self.assertEqual(doc["enemies"], [row(5, 1, 0), row(5, 1, 9, "0x010300"), row(5, 1, 1), row(5, 2, 0, "0x010200"),
                                          row(7, 1, 0)])
        self.assertEqual(fights, [])

    def test_drop_tables_merge_by_id_and_item(self):
        a, b = spawn_table(), spawn_table()
        ddo.drop_add(a, 3, 90, 0.1)
        ddo.drop_add(b, 3, 91, 0.2)
        a["dropsTables"][0]["items"][0][5] = 0.5                                  # both change goblin's 7750
        b["dropsTables"][0]["items"][0][5] = 0.6
        a["dropsTables"].append({"id": 9, "name": "New", "mdlType": 0, "items": []})
        b["dropsTables"][1]["name"] = "Wolves"
        doc, fights, _ = self.merge(a, b)
        by = {t["id"]: t for t in doc["dropsTables"]}
        self.assertEqual([it[0] for it in by[3]["items"]], [7751, 90, 91])
        self.assertEqual((by[3]["name"], by[2]["items"][0][5], [t["id"] for t in doc["dropsTables"]]),
                         ("Wolves", 0.6, [2, 3, 9]))
        self.assertEqual(fights, [("M0", "M1", "drop items 7750")])

    def test_what_is_not_merged(self):
        base = spawn_bytes(spawn_table())
        other = spawn_table()
        other["schemas"]["enemies"] = SCHEMA[::-1]
        twice = spawn_table()
        twice["dropsTables"].append(dict(twice["dropsTables"][0]))
        for bad in (b"[]", b"not json", spawn_bytes(other), spawn_bytes(twice), spawn_bytes({"enemies": 5, "schemas": {}})):
            with self.assertRaises(servermerge.MergeError, msg=bad[:40]):
                servermerge.merge("EnemySpawn.json", base, [("A", base), ("B", bad)])
        # a drop table both change, one of them into something that is not a table: the merge has to read it
        a = spawn_table()
        ddo.drop_add(a, 2, 35, 0.25)
        with self.assertRaises(servermerge.MergeError):
            servermerge.merge("EnemySpawn.json", base, [("A", spawn_bytes(a)),
                                                        ("B", spawn_bytes(dict(spawn_table(), dropsTables=[
                                                            {"id": 2, "items": 7}])))])
        for rel in ("quests/q1.json", "named_param.ndp.json"):
            with self.assertRaises(servermerge.MergeError):
                servermerge.merge(rel, base, [("A", base), ("B", base)])
        self.assertTrue(issubclass(servermerge.MergeError, RiftError))

    def test_one_changed_copy_comes_back_as_it_is(self):
        a = spawn_table()
        a["enemies"][2:2] = [row(5, 1, 2, "0x015850")]
        _, fights, raw = self.merge(a, spawn_table())
        self.assertEqual((raw, fights), (spawn_bytes(a), []))


class ShopMergeTest(unittest.TestCase):
    def merge(self, *docs):
        out, fights = servermerge.merge("Shop.json", shop_bytes(shops()),
                                        [(f"M{i}", shop_bytes(d)) for i, d in enumerate(docs)])
        return json.loads(out), fights, out

    def test_goods_two_mods_add_both_arrive_numbered(self):
        a, b = shops(), shops()
        ddo.shop_add(a, 223, 50, 100, 5)
        ddo.shop_add(b, 223, 51, 200, 5)
        ddo.shop_add(b, 52, 60, 10, 1)
        doc, fights, raw = self.merge(a, b)
        goods = doc[0]["Data"]["GoodsParamList"]
        self.assertEqual([(g["Index"], g["ItemId"]) for g in goods], [(0, 34), (1, 35), (2, 36), (3, 50), (4, 51)])
        self.assertEqual(list(goods[4])[0], "Index")                                # Index first, as shipped
        self.assertEqual([g["ItemId"] for g in doc[1]["Data"]["GoodsParamList"]], [40, 60])
        self.assertEqual(fights, [])
        self.assertTrue(raw.endswith(b"\n"))

    def test_removing_one_good_and_changing_another_do_not_disagree(self):
        a, b = shops(), shops()
        del a[0]["Data"]["GoodsParamList"][0]                                    # shifts the others' Index
        for i, g in enumerate(a[0]["Data"]["GoodsParamList"]):
            g["Index"] = i
        b[0]["Data"]["GoodsParamList"][2]["Price"] = 99
        doc, fights, _ = self.merge(a, b)
        self.assertEqual([(g["Index"], g["ItemId"], g["Price"]) for g in doc[0]["Data"]["GoodsParamList"]],
                         [(0, 35, 25), (1, 36, 99)])
        self.assertEqual(fights, [])

    def test_one_price_two_ways(self):
        a, b = shops(), shops()
        a[0]["Data"]["GoodsParamList"][1]["Price"] = 1
        b[0]["Data"]["GoodsParamList"][1]["Price"] = 2
        b[1]["Data"]["WalletType"] = 4
        doc, fights, _ = self.merge(a, b)
        self.assertEqual((doc[0]["Data"]["GoodsParamList"][1]["Price"], doc[1]["Data"]["WalletType"]), (2, 4))
        self.assertEqual(fights, [("M0", "M1", "goods 35: Price")])


class InstallTest(unittest.TestCase):
    """Two Online mods changing the spawn table, installed together on a stand-in server."""

    def test_install_merges_and_restore_returns_the_servers_file(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "ddo"
            (root / "nativePC" / "rom").mkdir(parents=True)
            (root / "DDO.exe").write_bytes(b"")
            assets = Path(d) / "assets"
            assets.mkdir()
            original = spawn_bytes(spawn_table())
            (assets / "EnemySpawn.json").write_bytes(original)
            mods = []
            for name, at, new in (("Goblins", 2, row(5, 1, 2, "0x015850")), ("Wolves", 5, row(7, 1, 2, "0x010300"))):
                m = Mod.create(Path(d) / name, name, game="ddo")
                doc = spawn_table()
                doc["enemies"].insert(at, new)
                (m.root / "server" / "EnemySpawn.json").write_bytes(spawn_bytes(doc))
                mods.append(m.root)
            game = Game(root, "ddo")
            old = os.environ.get("RIFTSTONE_DDO_ASSETS")
            os.environ["RIFTSTONE_DDO_ASSETS"] = str(assets)
            running = mock.patch.object(install, "game_running", lambda g: False)   # an Online client open here
            running.start()
            try:
                rep = install.apply(game, None, mods)
                self.assertEqual((rep.merged, rep.conflicts), ([{"archive": "server", "resource": "EnemySpawn.json",
                                                                 "mods": ["Goblins", "Wolves"]}], []))
                rows = json.loads((assets / "EnemySpawn.json").read_bytes())["enemies"]
                self.assertEqual(len(rows), 7)
                self.assertIn(row(5, 1, 2, "0x015850"), rows)
                self.assertIn(row(7, 1, 2, "0x010300"), rows)
                self.assertEqual(install.apply(game, None, mods).server_written, [])     # up to date
                # merged again against the server's own file, kept aside, not the merged one now in place
                rep = install.apply(game, None, mods[:1])
                self.assertEqual(json.loads((assets / "EnemySpawn.json").read_bytes())["enemies"],
                                 json.loads((mods[0] / "server" / "EnemySpawn.json").read_bytes())["enemies"])
                rep = install.apply(game, None, mods)
                self.assertEqual(len(json.loads((assets / "EnemySpawn.json").read_bytes())["enemies"]), 7)
                self.assertEqual(install.status(game)["drift"], [])
                install.restore_all(game)
                self.assertEqual((assets / "EnemySpawn.json").read_bytes(), original)
                # a copy that does not read: the later mod's wins whole, and why is said
                (mods[1] / "server" / "EnemySpawn.json").write_bytes(b'{"enemies": []}')
                rep = install.apply(game, None, mods)
                self.assertEqual(rep.merged, [])
                self.assertTrue(rep.conflicts[0]["detail"].startswith("not merged: "))
                self.assertEqual((assets / "EnemySpawn.json").read_bytes(), b'{"enemies": []}')
                install.restore_all(game)
            finally:
                running.stop()
                if old is None:
                    os.environ.pop("RIFTSTONE_DDO_ASSETS", None)
                else:
                    os.environ["RIFTSTONE_DDO_ASSETS"] = old


if __name__ == "__main__":
    unittest.main()

import os
import struct
import tempfile
import unittest
from pathlib import Path

from helpers import module_env, prop
from riftstone import arc, gmd, items, itl, params, typemap, xfs
from riftstone.errors import FormatError, ParamError, RiftError
from riftstone.game import Game
from riftstone.index import Index
from riftstone.mod import Mod

setUpModule, tearDownModule = module_env("RIFTSTONE_HOME")

GMD = typemap.BY_EXT["gmd"]
ITL = typemap.BY_EXT["itl"]


def record(item_id, weight=0.5, buy=100, sell=40, flags=0xE000, tail=b"\x40\x29"):
    r = bytearray(128)
    struct.pack_into("<I", r, 0x3C, (flags & ~0x1FFF) | item_id)
    struct.pack_into("<fII", r, 0x44, weight, buy, sell)
    r[0x7C:0x7E] = tail                      # bytes the model does not name must survive
    return r


def item_list(n=5, free=(3,)):
    recs = [record(i, 0.01, 0, 0) if i in free else record(i, 0.1 * (i + 1), 70 * (i + 1), 28 * (i + 1))
            for i in range(n)]
    return itl.build(itl.ItemList(0x01330611, 0, recs))


def names(lang, n=5, free=(3,)):
    msgs = [gmd.Message("Unknown Item" if i in free else f"Thing {i}") for i in range(n)]
    msgs[1].text = "Greenwarish"
    return gmd.build(gmd.Gmd(lang, "TextWeb", msgs))


def shop_list(item_nos=(10, 11)) -> bytes:
    def cls(name, *props):
        return xfs.ClassDef(typemap.jamcrc(name), 0x10, tuple(props))
    classes = [cls("rShopList", prop("mShopType", "u32"), prop("mShopLineup", "classref")),
               cls("MtArray", prop("mAutoDelete", "bool"), prop("mpArray", "classref", 0xA0)),
               cls("rShopList::cLineupData", prop("mItemNo", "s32"), prop("mItemNum", "s32"),
                   prop("mItemRearrival", "s32"), prop("mLineeupJAnd", "classref", 0xA0),
                   prop("mLineeupJOr", "classref", 0xA0)),
               cls("rShopList::cShopListJudgment", prop("mCommand", "s32"), prop("mParam00", "s32"))]
    judge = lambda c, p: xfs.Obj(3, [[c], [p]])          # noqa: E731
    lineup = [xfs.Obj(2, [[n], [2], [8], [judge(1, 80), judge(0, 0)], [judge(4, 20)]]) for n in item_nos]
    return xfs.build(xfs.Xfs(5, classes, xfs.Obj(0, [[4], [xfs.Obj(1, [[1], lineup])]])))


class ItlTest(unittest.TestCase):
    def test_round_trip(self):
        raw = item_list()
        t = itl.parse(raw)
        self.assertEqual(itl.build(t), raw)
        self.assertEqual(params.yaml_to_resource(params.resource_to_yaml(raw, "etc\\item\\itemList")), raw)
        text = itl.to_yaml(t, "l", ["a", "b", "c", "d", "e"])
        self.assertIn('name: "b"', text)
        self.assertEqual(itl.yaml_to_bytes(text), raw)

    def test_nan_weights_keep_their_bits(self):
        # fuzz finding: a weight whose bits are a NaN with a payload came back as the canonical NaN
        for bits in (0xFFFFFFFF, 0x7F800001, 0xFFC00001, 0x7FC00000):
            recs = [record(0)]
            struct.pack_into("<I", recs[0], 0x44, bits)
            raw = itl.build(itl.ItemList(1, 0, recs))
            text = itl.to_yaml(itl.parse(raw))
            self.assertIn(f"weight: nan:0x{bits:08x}", text)
            self.assertEqual(itl.yaml_to_bytes(text), raw)
        good = itl.to_yaml(itl.parse(item_list()))
        for bad in ("nan:0x7f800000", "nan:0x3f800000", "nan:0x1ffffffff", "nan:0xzz", "nan:0x-5"):   # not NaN bits / junk
            with self.assertRaises(ParamError, msg=bad):
                itl.yaml_to_bytes(good.replace("weight: 0.2", f"weight: {bad}"))

    def test_named_fields_edit_only_their_bytes(self):
        raw = item_list()
        text = itl.to_yaml(itl.parse(raw)).replace("buy: 140", "buy: 999", 1)
        new = itl.yaml_to_bytes(text)
        self.assertEqual(itl.ItemList.prices(itl.parse(new).records[1]), (999, 56))
        diff = [i for i in range(len(raw)) if raw[i] != new[i]]
        self.assertTrue(all(16 + 128 + 0x48 <= i < 16 + 128 + 0x4C for i in diff), diff)

    def test_refusals(self):
        raw = item_list()
        with self.assertRaises(FormatError):
            itl.parse(raw[:-1])
        with self.assertRaises(FormatError):
            itl.parse(b"ITL3" + raw[4:])
        swapped = bytearray(raw)
        struct.pack_into("<I", swapped, 16 + 0x3C, 7)   # record 0 claims to be item 7
        with self.assertRaises(FormatError):
            itl.parse(bytes(swapped))
        text = itl.to_yaml(itl.parse(raw))
        for bad in (text.replace("id: 2", "id: 9"), text.replace("buy: 140", "buy: -1"),
                    text.replace("buy: 140", "buy: 99999999999"), text.replace("weight: 0.2", "weight: heavy"),
                    text.replace("sell: 56", "sell: [1]"), text + "  - id: 5\n    raw: abcd\n",
                    text.replace("    buy: 140", "    colour: red")):
            with self.assertRaises(ParamError, msg=bad[-200:]):
                itl.yaml_to_bytes(bad, "t.yaml")


class ItemsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        os.environ["RIFTSTONE_HOME"] = str(base / "home")
        rom = base / "game" / "nativePC" / "rom"
        rom.mkdir(parents=True)
        (base / "game" / "DDDA.exe").write_bytes(b"stub")
        stem = b"id\\message\\item\\"
        shp = typemap.BY_EXT["shp"]
        from test_tables import MIX, sets
        n = 0xFFFF
        drops = sets((3, n, 0, 1, 1, 2, n, n, n, n, n, n, 30, 30, 40, 0, 0, 0, 0, 0),     # 40% nothing, 100 in all
                     (4, n, 0, 1, 1, 2, 4, 0, 0, 0, 0, 0, 50, 20, 10, 5, 5, 5, 5, 0))     # every slot used
        entries = [arc.Entry.from_data(b"etc\\item\\itemList", ITL, item_list()),
                   arc.Entry.from_data(b"etc\\shop\\n007ShopList", shp, shop_list()),
                   arc.Entry.from_data(b"etc\\shop\\n156ShopList", shp, shop_list()),
                   arc.Entry.from_data(b"dl1\\etc\\shop\\n156ShopList", shp, shop_list()),
                   arc.Entry.from_data(b"etc\\item\\itemMix", typemap.BY_EXT["imx"], MIX),
                   arc.Entry.from_data(b"etc\\item\\ItemEmListSetTbl", typemap.BY_EXT["ist"], drops)]
        for lang, suf in ((1, b"eng"), (2, b"fre")):
            entries.append(arc.Entry.from_data(stem + b"itemName_" + suf, GMD, names(lang)))
            entries.append(arc.Entry.from_data(stem + b"itemInfo_" + suf, GMD, names(lang)))
        (rom / "bbs_rpg.arc").write_bytes(arc.Archive(entries).build())
        self.game = Game(base / "game")
        self.idx = Index(self.game)
        self.idx.refresh()
        self.mod = Mod.create(base / "mods" / "M", "M")

    def tearDown(self):
        self.idx.close()
        self.tmp.cleanup()

    def test_listing_and_find(self):
        lst = items.listing(self.game, self.idx)
        self.assertEqual([it.id for it in lst if it.free], [3])
        self.assertEqual(items.find(lst, "greenwarish").id, 1)
        self.assertEqual(items.find(lst, "4").id, 4)
        with self.assertRaises(RiftError):
            items.find(lst, "Thing")          # several match
        with self.assertRaises(RiftError):
            items.find(lst, "Unknown Item")   # placeholders are not templates
        for odd in ("²", "9" * 5000):     # was: ValueError from int() ('²' passed str.isdigit())
            with self.assertRaises(RiftError, msg=odd[:8]):
                items.find(lst, odd)

    def test_new_item(self):
        made = items.new(self.game, self.idx, self.mod.root, "Rift Tonic", "greenwarish", "Heals a little.",
                         buy=500, weight=0.25)
        self.assertEqual((made.id, made.like.id), (3, 1))
        lst = items.listing(self.game, self.idx, self.mod.root)       # now read through the mod
        self.assertEqual((lst[3].name, lst[3].buy, lst[3].sell, round(lst[3].weight, 2)), ("Rift Tonic", 500, 200, 0.25))
        t, path = items.load_list(self.game, self.idx, self.mod.root)
        self.assertEqual(path.name, "itemList.itl.yaml")
        self.assertEqual(t.records[3][0x7C:0x7E], b"\x40\x29")         # the template's other bytes came along
        fre = gmd.from_yaml((self.mod.root / "files/id/message/item/itemInfo_fre.gmd.yaml").read_text("utf-8"))
        self.assertEqual(fre.messages[3].text, "Heals a little.")
        with self.assertRaises(RiftError):                              # no free slot is left
            items.new(self.game, self.idx, self.mod.root, "Another", "1")

    def test_shop(self):
        out = items.shop(self.game, self.idx, self.mod.root, "n007", 3, stock=5, restock=2)
        self.assertEqual(out.name, "n007ShopList.shp.yaml")
        x = params.from_yaml(out.read_text(encoding="utf-8"))
        lineup = x.root.fields[1][0].fields[1]
        self.assertEqual([e.fields[0][0] for e in lineup], [10, 11, 3])
        added = lineup[-1]
        self.assertEqual((added.fields[1][0], added.fields[2][0]), (5, 2))
        conds = added.fields[3] + added.fields[4]
        self.assertTrue(conds and all(c.fields == [[0], [0]] for c in conds))     # every condition cleared
        self.assertEqual(lineup[0].fields[3][0].fields, [[1], [80]])              # the others are untouched
        items.shop(self.game, self.idx, self.mod.root, "etc/shop/n007ShopList.shp", 4)   # builds on the mod's copy
        with self.assertRaises(RiftError):
            items.shop(self.game, self.idx, self.mod.root, "n007ShopList", 3)      # already sold there

    def test_find_shop(self):
        self.assertEqual(items.find_shop(self.idx, "n007"), b"etc\\shop\\n007ShopList")
        self.assertEqual(items.find_shop(self.idx, "dl1/etc/shop/n156ShopList.shp"), b"dl1\\etc\\shop\\n156ShopList")
        for bad in ("n156", "n999", "etc/shop/n999ShopList", "etc/item/itemList.itl"):
            with self.assertRaises(RiftError, msg=bad):
                items.find_shop(self.idx, bad)
        with self.assertRaises(RiftError):
            items.shop(self.game, self.idx, self.mod.root, "n007", 3, stock=-1)
        for bad_item in (-1, 2**31, 2**40):       # fuzz finding: an item id past s32 crashed the XFS writer
            with self.assertRaises(RiftError):
                items.shop(self.game, self.idx, self.mod.root, "n007", bad_item)

    def test_recipe(self):
        from riftstone import tables
        out = items.recipe(self.game, self.idx, self.mod.root, 1, 4, 3, 2)
        self.assertEqual(out.name, "itemMix.imx.yaml")
        rows = tables.parse(params.yaml_to_resource(out.read_text(encoding="utf-8"))).rows
        self.assertEqual(rows[-1], (1, 4, 3, 2))
        for a, b in ((1, 4), (4, 1), (1740, 60)):                     # the pair already has a recipe, either order
            with self.assertRaises(RiftError):
                items.recipe(self.game, self.idx, self.mod.root, a, b, 2)
        with self.assertRaises(RiftError):
            items.recipe(self.game, self.idx, self.mod.root, 5, 6, 3, 0)

    def test_drop_and_sets(self):
        self.assertEqual([r[0] for r in items.sets_with(self.game, self.idx, None, 2)], [3, 4])
        out, total = items.drop(self.game, self.idx, self.mod.root, 3, 3, 10)
        self.assertEqual(total, 100)                                    # 10 came out of the 40% "nothing"
        row = items.sets_with(self.game, self.idx, self.mod.root, 3)[0]
        self.assertEqual((row[4:8], row[12:16]), ((1, 2, 0xFFFF, 3), (30, 30, 30, 10)))
        items.drop(self.game, self.idx, self.mod.root, 4, 3, 30)         # takes the rest of "nothing"
        row = items.sets_with(self.game, self.idx, self.mod.root, 4)[0]
        self.assertEqual((row[6], row[14]), (4, 30))                     # the emptied slot holds the item
        for args in ((3, 3, 5), (1, 99, 5), (5, 4, 5), (5, 3, 0), (5, 3, 101)):   # there, no set, full, bad %
            with self.assertRaises(RiftError, msg=args):
                items.drop(self.game, self.idx, self.mod.root, *args)
        with self.assertRaises(RiftError):
            items.drop(self.game, self.idx, self.mod.root, 5, 3, 5, table="bosses")

    def test_new_item_refusals(self):
        for kwargs in ({"name": " ", "like": "1"}, {"name": "x", "like": "nothing"}, {"name": "x", "like": "1", "slot": 2},
                       {"name": "x", "like": "1", "slot": 99}, {"name": "x\0", "like": "1"},
                       {"name": "x", "like": "1", "buy": -5}):
            with self.assertRaises(RiftError, msg=kwargs):
                items.new(self.game, self.idx, self.mod.root, **kwargs)
        self.assertFalse((self.mod.root / "files" / "etc").exists())   # nothing was written


if __name__ == "__main__":
    unittest.main()

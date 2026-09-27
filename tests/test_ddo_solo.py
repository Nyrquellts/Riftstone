"""Solo Balance (ddo_solo): twins, names, the server's copy, the script, the settings, the mod, an install."""
import json
import os
import re
import struct
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

import helpers  # noqa: F401 (sys.path)
from test_ddo_params import ddo_found, ndp_record
from riftstone import arc, ddo, ddo_params, ddo_solo, gmd, install, mod, typemap
from riftstone.ddo_params import NDP_RATES, DdoParams
from riftstone.ddo_solo import Setting, Tier
from riftstone.errors import RiftError
from riftstone.game import Game

NDP, GMD = typemap.BY_EXT["ndp"], typemap.BY_EXT["gmd"]


def table(*records) -> DdoParams:
    return DdoParams("ndp", {"mpArray": list(records)})


def small_table() -> DdoParams:
    return table(ndp_record(47, 2, 250, mExperience=0, mAttackBasePhys=80, mHpSub=120),
                 ndp_record(53, 2, 150, mAttackWepMagic=0),
                 ndp_record(2298),
                 ndp_record(3250, 4, 1000, mExperience=500, mAttackBasePhys=3000, mDefenceBasePhys=60))


def names_file(ids=(47, 53, 2298, 3250), texts=None) -> gmd.Gmd:
    texts = texts or {47: " Training ", 53: " Vigilant ", 2298: "", 3250: " Lord of Ruin "}
    msgs = [gmd.Message("----", "namedparam_0")] + [gmd.Message(texts[i], f"namedparam_{i}") for i in ids]
    return gmd.Gmd(0, "TextWeb", msgs, version=gmd.VERSION_DDO)


JSON_NAME = {47: "Training", 53: "Vigilant", 2298: "", 3250: "Lord of Ruin"}
TYPE_NAME = {1: "NAMED_TYPE_NONE", 2: "NAMED_TYPE_PREFIX", 3: "NAMED_TYPE_SUFFIX", 4: "NAMED_TYPE_REPLACE"}


def server_doc(t: DdoParams) -> dict:
    """The server's named_param.ndp.json for a table, as DDON-Tools writes it."""
    lst = []
    for r in t.data["mpArray"]:
        e = {"ID": r["mID"], "Name": JSON_NAME.get(r["mID"], ""), "Type": r["mType"], "TypeName": TYPE_NAME[r["mType"]],
             "HpRate": r["mHpRate"]}
        e.update({k[1:]: r[k] for k in NDP_RATES})
        lst.append(e)
    return {"@class": "org.sehkah.ddon.tools.extractor.lib.logic.resource.entity.season3.game_common.NamedParamList",
            "fileHeader": {"magicBytesLength": 0, "versionNumber": 5, "versionBytesLength": 4},
            "namedParamList": lst, "fileSize": len(ddo_params.build(t))}


GS_TEMPLATE = (
    "/*\n * Settings file for Server customization.\n * This file supports hotloading.\n */\n\r\n"
    "/// <summary>\r\n/// Global modifier for enemy exp calculations to scale up or down.\r\n/// </summary>\r\n"
    "double EnemyExpModifier = 1;\r\n\r\ndouble QuestExpModifier = 1;\r\n\r\ndouble PpModifier = 1;\r\n\r\n"
    "double GoldModifier = 1;\r\n\r\ndouble RiftModifier = 1;\r\n\r\ndouble BoModifier = 1;\r\n\r\n"
    "double HoModifier = 1;\r\n\r\ndouble JpModifier = 1;\r\n\r\ndouble ApModifier = 1;\r\n\r\n"
    "bool EnableMainPartyPawnsQuestRewards = false;\r\n\r\n"
    "double AdditionalProductionSpeedFactor = 1;\r\n\r\n"
    'string UrlDomain = "http://localhost:52099";\r\n\r\n'
    "var WeatherStatistics = new List<(uint MeanLength, uint Weight)>\r\n{\r\n    (60 * 30, 1), // Fair\r\n};\r\n\r\n"
    "uint JobLevelMax = 120;\r\n\r\n")
PM_TEMPLATE = (
    "/*\n * Settings file for Server customization.\n * This file supports hotloading.\n */\n\r\n"
    "bool EnableAdjustPartyEnemyExp = true;\r\n\r\n"
    "var AdjustPartyEnemyExpTiers = new List<(uint MinLv, uint MaxLv, double ExpMultiplier)>()\r\n{\r\n"
    "    // MinLv, MaxLv, ExpMultiplier\r\n"
    "    (      0,     2,           1.0),\r\n    (      3,     4,           0.9),\r\n"
    "    (      9,    10,           0.5),\r\n};\r\n\r\n"
    "double PawnCatchupMultiplier = 1.5;\r\n\r\nuint PawnCatchupLvDiff = 5;\r\n\r\n")


def sources(t: DdoParams | None = None) -> ddo_solo.Sources:
    t = t or small_table()
    return ddo_solo.Sources(ddo_params.build(t), gmd.build(names_file()),
                            ddo.dumps_style(server_doc(t), ("jackson", "")),
                            {"GameServerSettings": (GS_TEMPLATE, "the server's template GameServerSettings.csx", None),
                             "PointModifierSettings": (PM_TEMPLATE, "the server's template PointModifierSettings.csx",
                                                       None)})


class ScaleTest(unittest.TestCase):
    def test_rounding(self):
        s = ddo_solo.scaled
        self.assertEqual(s(250, 0.85, 0xFFFF), 213)             # 212.5 rounds up
        self.assertEqual(s(150, 0.55, 0xFFFF), 83)              # 82.5 rounds up
        self.assertEqual(s(3000, 0.85, 0xFFFF), 2550)
        self.assertEqual(s(0, 0.4, 0xFFFF), 0)                  # 0 stays 0 (0% attack stays 0%)
        self.assertEqual(s(1, 0.4, 0xFFFF), 1)                  # never down to 0 from something
        self.assertEqual(s(10, 0.05, 0xFFFF), 1)
        self.assertEqual(s(60000, 5, 0xFFFF), 0xFFFF)           # capped at the field's width
        self.assertEqual(s(7, 1.0, 0xFFFF), 7)
        self.assertEqual(s(100, Fraction(1, 3), 0xFFFF), 33)
        self.assertEqual(s(0xFFFFFFFF, 0.5, 0xFFFFFFFF), 0x80000000)

    def test_tier_defaults_and_overrides(self):
        self.assertEqual([t.name for t in ddo_solo.TIERS], ["field", "boss", "exm"])
        self.assertEqual([(t.hp, t.attack) for t in ddo_solo.TIERS], [(0.85, 1.0), (0.55, 0.85), (0.40, 0.80)])
        t = ddo_solo.tier("boss", hp=0.6)
        self.assertEqual((t.offset, t.hp, t.attack), (8000, 0.6, 0.85))
        self.assertFalse(Tier("field", 4000, 1.0, 1.0).active)
        with self.assertRaises(RiftError):
            ddo_solo.tier("raid")

    def test_command_line_tiers(self):
        self.assertEqual(ddo_solo.tiers_from_options(), list(ddo_solo.TIERS))
        t = ddo_solo.tiers_from_options("boss, EXM", "1000,2000,3000", boss_hp="0.6", exm_attack=0.9)
        self.assertEqual([(x.name, x.offset, x.hp, x.attack) for x in t], [("boss", 2000, 0.6, 0.85),
                                                                           ("exm", 3000, 0.4, 0.9)])
        self.assertEqual(ddo_solo.tiers_from_options(field_hp="17/20")[0].hp, 0.85)
        for bad in (dict(names="raid"), dict(names=""), dict(names=" , "), dict(offsets="1,2"),
                    dict(offsets="1,2,²"), dict(offsets="4000,8000,1234567"), dict(offsets="-1,2,3"),
                    dict(field_hp="x"), dict(field_hp=True), dict(field_hp="nan"), dict(field_hp="1/0"),
                    dict(nope=1), dict(names="field", field_hp=1.0), dict(boss_attack=9)):
            with self.assertRaises(RiftError, msg=bad):
                ddo_solo.tiers_from_options(**bad)

    def test_a_huge_factor_is_refused_not_overflowed(self):
        """Fuzz finding solo-crash-7c9bbb4ca01b: '0.85e02000' reached float() and overflowed; an exponent of
        a billion would have made Fraction build a billion-digit number first."""
        for bad in ("0.85e02000", "1e999999999", "9" * 400, "1e308", "5.0001", "0.0499", "-1", "1_0"):
            with self.assertRaises(RiftError, msg=bad):
                ddo_solo.tiers_from_options(boss_hp=bad)
        self.assertEqual(ddo_solo.tiers_from_options(boss_hp="5e-1")[1].hp, 0.5)
        self.assertEqual(ddo_solo.tiers_from_options(boss_hp=" 0.05 ")[1].hp, 0.05)


class TwinTest(unittest.TestCase):
    def test_twins_of_every_record(self):
        t = small_table()
        new, pairs = ddo_solo.make_twins(t)
        recs = new.data["mpArray"]
        self.assertEqual(len(recs), 16)
        self.assertEqual(recs[:4], t.data["mpArray"])                   # the originals first, unchanged
        self.assertEqual([r["mID"] for r in recs],
                         [47, 53, 2298, 3250, 4047, 4053, 6298, 7250, 8047, 8053, 10298, 11250, 12047, 12053,
                          14298, 15250])
        self.assertEqual([r["mID"] for r in recs], sorted(r["mID"] for r in recs))
        self.assertEqual(pairs[0], (47, "field", 4047))
        self.assertEqual(pairs[-1], (3250, "exm", 15250))
        by = {r["mID"]: r for r in recs}
        self.assertEqual((by[4047]["mHpRate"], by[4047]["mHpSub"], by[4047]["mAttackBasePhys"]), (213, 102, 80))
        self.assertEqual((by[8047]["mHpRate"], by[8047]["mHpSub"], by[8047]["mAttackBasePhys"]), (138, 66, 68))
        self.assertEqual((by[12047]["mHpRate"], by[12047]["mHpSub"], by[12047]["mAttackBasePhys"]), (100, 48, 64))
        self.assertEqual(by[10298]["mAttackWepMagic"], 85)
        self.assertEqual(by[8053]["mAttackWepMagic"], 0)                 # 0% attack stays 0
        self.assertEqual(by[11250]["mAttackBasePhys"], 2550)
        for tw, orig in ((by[p[2]], by[p[0]]) for p in pairs):
            for k in ("mType", *NDP_RATES):
                if k not in ddo_solo.HP and k not in ddo_solo.ATTACK:
                    self.assertEqual(tw[k], orig[k], (tw["mID"], k))    # EXP, defence, endurance ... unchanged
        self.assertEqual(by[4047]["mExperience"], 0)
        raw = ddo_params.build(new)
        self.assertEqual(ddo_params.parse(raw, "ndp"), new)
        self.assertEqual(len(raw), 8 + 16 * 54)

    def test_part_hp_kept_and_tiers_left_out(self):
        t = small_table()
        new, pairs = ddo_solo.make_twins(t, [Tier("field", 4000, 0.85), Tier("boss", 8000, 1.0, 1.0),
                                             Tier("exm", 12000, 0.5, 1.0)], parts=False)
        by = {r["mID"]: r for r in new.data["mpArray"]}
        self.assertEqual(len(pairs), 8)                                  # boss changes nothing: no twins
        self.assertNotIn(8047, by)
        self.assertEqual((by[4047]["mHpRate"], by[4047]["mHpSub"]), (213, 120))
        self.assertEqual(by[12047]["mAttackBasePhys"], 80)               # attack factor 1: unchanged

    def test_refusals(self):
        t = small_table()
        cases = [
            (table(ndp_record(47), ndp_record(47)), ddo_solo.TIERS, "repeats id 47"),
            (table(ndp_record(47), ndp_record(3252)), ddo_solo.TIERS, "multiple of 4"),
            (t, [Tier("field", 3000, 0.8)], "would meet the originals"),          # 3047..6250 vs 47..3250
            (t, [Tier("field", 4000, 0.8), Tier("boss", 6000, 0.5)], "would meet the field twins"),
            (t, [Tier("field", 64000, 0.8)], "at most 65535"),
            (t, [Tier("field", 4000, 1.0, 1.0)], "nothing to make"),
            (t, [Tier("field", 4000, 0.0)], "between 0.05 and 5"),
            (t, [Tier("field", 4000, 6)], "between 0.05 and 5"),
            (t, [Tier("field", 4000, "x")], "not a number"),
            (t, [Tier("field", 0, 0.8)], "offset"),
            (t, [Tier("field", 4000, 0.8), Tier("field", 8000, 0.8)], "each tier once"),
            (t, [Tier("raid", 4000, 0.8)], "unknown tier"),
            (table(), ddo_solo.TIERS, "empty"),
            (DdoParams("cpe", {}), ddo_solo.TIERS, "not cpe"),
        ]
        for i, (tab, tiers, why) in enumerate(cases):
            with self.assertRaises(RiftError, msg=i) as cm:
                ddo_solo.make_twins(tab, tiers)
            self.assertIn(why, str(cm.exception), i)
        already, _ = ddo_solo.make_twins(t)                              # twins of twins would collide
        with self.assertRaises(RiftError) as cm:
            ddo_solo.make_twins(already)
        self.assertIn("already hold twins", str(cm.exception))
        many = table(*[ndp_record(i) for i in range(47, 47 + 8200)])      # 4 x 8200 > 0x7FFF records
        with self.assertRaises(RiftError) as cm:
            ddo_solo.make_twins(many, [Tier("field", 20000, 0.8), Tier("boss", 30000, 0.5), Tier("exm", 40000, 0.4)])
        self.assertIn("at most 32767", str(cm.exception))


class NamesTest(unittest.TestCase):
    def test_twins_share_their_originals_text(self):
        _, pairs = ddo_solo.make_twins(small_table())
        g, missing = ddo_solo.twin_names(names_file(), pairs)
        self.assertEqual(missing, 0)
        self.assertEqual(len(g.messages), 5 + 12)
        texts = {m.label: m.text for m in g.messages}
        self.assertEqual(texts["namedparam_4053"], " Vigilant ")
        self.assertEqual(texts["namedparam_14298"], "")
        self.assertEqual(texts["namedparam_15250"], " Lord of Ruin ")
        raw = gmd.build(g)
        back = gmd.parse(raw)                                            # the key tables follow the game's rule
        self.assertEqual([m.label for m in back.messages], [m.label for m in g.messages])
        # the two hash words the game compares (0x014F9E02) are the ones the file carries
        nkeys = len(back.messages)
        head = gmd.HEADER.size + len(back.name) + 1
        recs = [gmd.KEYREC.unpack_from(raw, head + i * gmd.KEYREC.size) for i in range(nkeys)]
        for i, m in enumerate(back.messages):
            bucket, h2, h3 = ddo_solo._gmd_key(m.label)
            self.assertEqual((recs[i][1], recs[i][2]), (h2, h3))

    def test_missing_original_label(self):
        g = names_file(ids=(47, 53, 2298))                              # 3250 has no label
        _, pairs = ddo_solo.make_twins(small_table())
        out, missing = ddo_solo.twin_names(g, pairs)
        self.assertEqual(missing, 3)
        self.assertNotIn("namedparam_7250", {m.label for m in out.messages})

    def test_refusals(self):
        _, pairs = ddo_solo.make_twins(small_table())
        g = names_file()
        g.messages.append(gmd.Message("unlabelled"))
        with self.assertRaises(RiftError):
            ddo_solo.twin_names(g, pairs)
        g = names_file()
        g.messages.append(gmd.Message("x", "namedparam_4047"))
        with self.assertRaises(RiftError):
            ddo_solo.twin_names(g, pairs)
        g = names_file()
        g.version = gmd.VERSION
        with self.assertRaises(RiftError):
            ddo_solo.twin_names(g, pairs)


class ServerJsonTest(unittest.TestCase):
    def test_twins_in_the_servers_copy(self):
        t = small_table()
        doc = server_doc(t)
        new, originals = ddo_solo.twin_server_json(doc, ddo_solo.TIERS, True, 1234)
        self.assertEqual(sorted(originals), [47, 53, 2298, 3250])
        lst = new["namedParamList"]
        self.assertEqual(len(lst), 16)
        self.assertEqual(lst[:4], doc["namedParamList"])
        self.assertEqual(new["fileSize"], 1234)
        tw = next(e for e in lst if e["ID"] == 8053)
        self.assertEqual((tw["Name"], tw["TypeName"], tw["HpRate"], tw["AttackWepMagic"]),
                         ("Vigilant", "NAMED_TYPE_PREFIX", 83, 0))
        self.assertEqual(list(tw), list(doc["namedParamList"][1]))       # the same keys, in the same order
        ndp_twins, _ = ddo_solo.make_twins(t)                            # the client's and the server's twins agree
        for r in ndp_twins.data["mpArray"]:
            e = next(x for x in lst if x["ID"] == r["mID"])
            self.assertEqual({k: e[j] for k, j in ddo_solo.JSON_KEYS.items()}, r)

    def test_refusals(self):
        doc = server_doc(small_table())
        bad = [{}, {"namedParamList": []}, {"namedParamList": [1]},
               {"namedParamList": [dict(doc["namedParamList"][0], HpRate="x")]},
               {"namedParamList": [dict(doc["namedParamList"][0], Power=-1)]},
               {"namedParamList": [dict(doc["namedParamList"][0])] * 2},
               {"namedParamList": [{k: v for k, v in doc["namedParamList"][0].items() if k != "HpSub"}]},
               {"namedParamList": doc["namedParamList"] + [dict(doc["namedParamList"][0], ID=4047)]}]
        for i, d in enumerate(bad):
            with self.assertRaises(RiftError, msg=i):
                ddo_solo.twin_server_json(d)

    def test_jackson_style_round_trip(self):
        doc = server_doc(small_table())
        doc["extra"] = {"empty": {}, "list": [], "ctl": "a\"b\\c\n\u0001\u001f", "jp": "？？？", "n": None, "t": True}
        text = ddo._jackson(doc)
        self.assertTrue(text.startswith('{\n  "@class" : "org.sehkah'))
        self.assertIn('"namedParamList" : [ {\n    "ID" : 47,', text)
        self.assertIn('\n  }, {\n    "ID" : 53,', text)
        self.assertIn('"empty" : { }', text)
        self.assertIn('"list" : [ ]', text)
        self.assertIn('"ctl" : "a\\"b\\\\c\\n\\u0001\\u001F"', text)     # Jackson's upper-case escapes
        self.assertEqual(json.loads(text), doc)
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "named_param.ndp.json"
            f.write_bytes(text.encode("utf-8"))
            back, style = ddo.read_json(f)
            self.assertEqual(style, ("jackson", ""))
            self.assertEqual(ddo.dumps_style(back, style), f.read_bytes())
        # fuzz finding solo-invariant-11135c2bcee9: a lone value reads as the first style that writes it
        for raw in (b"true", b"1", b"9.5", b'"x"', b"[ ]", b"{ }"):
            doc, style = ddo.parse_json(raw)
            self.assertEqual(ddo.dumps_style(doc, style), raw, raw)
        with self.assertRaises(RiftError):
            ddo.parse_json(b"{")
        with self.assertRaises(RiftError):
            ddo.dumps_style({"a": "\ud800"}, (2, ""))                   # a lone surrogate cannot be written


class ScriptTest(unittest.TestCase):
    def test_script(self):
        s = ddo_solo.solo_script(ddo_solo.TIERS, 47, 3250)
        self.assertTrue(s.lstrip().startswith("// Solo Balance"))
        self.assertIn('#load "libs.csx"', s)
        self.assertIn("public class SoloBalance : IInstanceEnemyPropertyGenerator", s)
        self.assertIn("public override uint ScriptRank => 1000;", s)
        self.assertIn("party.Clients.Count != 1", s)
        self.assertIn("private const uint FieldOffset = 4000;", s)
        self.assertIn("private const uint BossOffset = 8000;", s)
        self.assertIn("private const uint ExmOffset = 12000;", s)
        self.assertIn("private const uint FirstOriginal = 47;", s)
        self.assertIn("private const uint LastOriginal = 3250;", s)
        self.assertIn("QuestManager.IsExmQuest(enemy.QuestScheduleId)", s)
        self.assertIn("enemy.IsBossGauge || enemy.IsAreaBoss", s)
        self.assertIn("LibDdon.Assets.NamedParamAsset.TryGetValue(original.Id + offset, out NamedParam twin)", s)
        self.assertTrue(s.rstrip().endswith("return new SoloBalance();"))
        self.assertEqual(s.count("{"), s.count("}"))
        self.assertEqual(s.count("("), s.count(")"))
        checked = re.findall(r"twin\.(\w+) == original\.(\w+)", s)
        self.assertTrue(all(a == b for a, b in checked))
        self.assertEqual([a for a, _ in checked], list(ddo_solo._KEPT))
        self.assertNotIn("HpRate", [a for a, _ in checked])
        self.assertNotIn("HpSub", [a for a, _ in checked])
        self.assertNotIn("AttackBasePhys", [a for a, _ in checked])
        self.assertIn("Experience", [a for a, _ in checked])            # EXP must match: the server pays by it
        kept = ddo_solo.solo_script([Tier("field", 4000, 0.85), Tier("boss", 8000, 1, 1)], 47, 3250, parts=False)
        self.assertIn("private const uint BossOffset = 0;", kept)
        self.assertIn("twin.HpSub == original.HpSub", kept)


class SettingsTest(unittest.TestCase):
    def test_only_the_values_change(self):
        out = ddo_solo.set_settings(GS_TEMPLATE, {"EnemyExpModifier": 1.5, "RiftModifier": 2,
                                                  "EnableMainPartyPawnsQuestRewards": True})
        a, b = GS_TEMPLATE.splitlines(keepends=True), out.splitlines(keepends=True)
        self.assertEqual(len(a), len(b))
        diff = [(x, y) for x, y in zip(a, b) if x != y]
        self.assertEqual(diff, [("double EnemyExpModifier = 1;\r\n", "double EnemyExpModifier = 1.5;\r\n"),
                                ("double RiftModifier = 1;\r\n", "double RiftModifier = 2;\r\n"),
                                ("bool EnableMainPartyPawnsQuestRewards = false;\r\n",
                                 "bool EnableMainPartyPawnsQuestRewards = true;\r\n")])
        self.assertEqual(ddo_solo.set_settings(out, {"EnemyExpModifier": 1.5}), out)       # a fixed point

    def test_rows(self):
        rows = [(0, 4, 1.0), (5, 8, 0.9), (9, 12, 0.8), (13, 16, 0.7), (17, 20, 0.6)]
        out = ddo_solo.set_settings(PM_TEMPLATE, {"AdjustPartyEnemyExpTiers": rows})
        self.assertIn("    // MinLv, MaxLv, ExpMultiplier\r\n    (      0,     4,           1.0),\r\n", out)
        self.assertIn("    (     17,    20,           0.6),\r\n};\r\n", out)
        self.assertNotIn("0.5", out)
        self.assertEqual(out.count("(     "), 5)
        self.assertTrue(out.startswith("/*\n * Settings file"))                        # the LF lines stay LF
        for bad in ([], [(1, 2)], [(1, 2, "x")], [(-1, 2, 1.0)], [(1.5, 2, 1.0)], 5):
            with self.assertRaises(RiftError, msg=bad):
                ddo_solo.set_settings(PM_TEMPLATE, {"AdjustPartyEnemyExpTiers": bad})
        # values wider than the template's columns widen them, and a second run writes the same text
        wide = ddo_solo.set_settings(PM_TEMPLATE, {"AdjustPartyEnemyExpTiers": [(123456789, 2, 0.000001), (1, 2, 3.5)]})
        self.assertIn("    (123456789,     2,      0.000001),\r\n    (        1,     2,           3.5),\r\n", wide)
        self.assertEqual(ddo_solo.set_settings(wide, {"AdjustPartyEnemyExpTiers": [(123456789, 2, 0.000001),
                                                                                    (1, 2, 3.5)]}), wide)

    def test_list_value_on_a_one_line_declaration(self):
        # fuzz finding solo-invariant-19dc46f68d5c: 'var X = new List<...' cut short by a ';' on its own line was
        # read as a one-line declaration, and rows further down were replaced anyway
        text = PM_TEMPLATE.replace("new List<(uint MinLv, uint MaxLv, double ExpMultiplier)>()\r\n",
                                   "new List<(uint MinLv, uint = 120;\r\n")
        with self.assertRaises(RiftError) as cm:
            ddo_solo.set_settings(text, {"AdjustPartyEnemyExpTiers": [(0, 4, 1.0)]})
        self.assertIn("one line", str(cm.exception))

    def test_only_newlines_end_lines(self):
        # fuzz finding solo-invariant-d76aaa387447: str.splitlines broke a row line at a form feed, so the new
        # rows were written on one line
        text = PM_TEMPLATE.replace("0.9),\r\n", "0.9),\x0c\r\n")
        self.assertEqual(ddo_solo._lines("a\x0cb\r\nc\x1cd\n\ne"), ["a\x0cb\r\n", "c\x1cd\n", "\n", "e"])
        out = ddo_solo.set_settings(text, {"AdjustPartyEnemyExpTiers": [(0, 4, 1.0), (5, 8, 0.9), (9, 10, 0.5)]})
        self.assertIn("    (      0,     4,           1.0),\r\n    (      5,     8,           0.9),\r\n"
                      "    (      9,    10,           0.5),\r\n};", out)

    def test_literals(self):
        lit = ddo_solo._cs_literal
        self.assertEqual([lit(True), lit(3), lit(1.5), lit(2.0), lit(1e-06), lit(1e20)],
                         ["true", "3", "1.5", "2.0", "0.000001", "100000000000000000000.0"])
        for v in (1.5, 0.000001, 123456789.12345679, 7, True, 0.1):
            self.assertEqual(ddo_solo.parse_value(lit(v)), v)
        with self.assertRaises(RiftError):
            lit(float("inf"))

    def test_refusals(self):
        cases = [({"NoSuchSetting": 1}, "declared 0 times"),
                 ({"EnableMainPartyPawnsQuestRewards": 1}, "true/false"),
                 ({"JobLevelMax": 1.5}, "whole number"),
                 ({"JobLevelMax": -1}, "does not fit"),
                 ({"EnemyExpModifier": True}, "a number"),
                 ({"EnemyExpModifier": -2.0}, "negative"),
                 ({"EnemyExpModifier": float("nan")}, "not a number"),
                 ({"UrlDomain": 1}, "only numbers and true/false"),
                 ({"WeatherStatistics": 1}, "is a list"),
                 ({"bad name": 1}, "not a setting name")]
        for change, why in cases:
            with self.assertRaises(RiftError, msg=change) as cm:
                ddo_solo.set_settings(GS_TEMPLATE, change)
            self.assertIn(why, str(cm.exception), change)
        twice = GS_TEMPLATE + "double EnemyExpModifier = 3;\r\n"
        with self.assertRaises(RiftError):
            ddo_solo.set_settings(twice, {"EnemyExpModifier": 1.5})

    def test_owner_file_keeps_its_values(self):
        own = "// my settings\r\ndouble EnemyExpModifier = 3;\r\nuint JobLevelMax = 100;"
        out = ddo_solo.set_settings(own, {"EnemyExpModifier": 1.5, "GoldModifier": 2.0}, GS_TEMPLATE)
        self.assertIn("double EnemyExpModifier = 1.5;\r\n", out)
        self.assertIn("uint JobLevelMax = 100;\n", out)                  # kept (a newline added at the end)
        self.assertTrue(out.rstrip().endswith("double GoldModifier = 2.0;"))
        with self.assertRaises(RiftError):
            ddo_solo.set_settings(own, {"GoldModifier": 2.0})            # not declared and no template

    def test_values_and_overrides(self):
        self.assertEqual([ddo_solo.parse_value(v) for v in ("true", "False", "2", "1.5", ".5")],
                         [True, False, 2, 1.5, 0.5])
        for bad in ("", "-1", "1e5", "abc", "1,5", "nan", "9" * 20):
            with self.assertRaises(RiftError, msg=bad):
                ddo_solo.parse_value(bad)
        src = sources()
        s = ddo_solo.with_overrides(src, ddo_solo.SETTINGS, ["EnemyExpModifier=2", "HoModifier=1.25",
                                                              "PawnCatchupLvDiff=4"])
        by = {x.name: x for x in s}
        self.assertEqual((by["EnemyExpModifier"].value, by["EnemyExpModifier"].file), (2, "GameServerSettings"))
        self.assertEqual([x.name for x in s].index("EnemyExpModifier"), 0)   # replaced where it was
        self.assertEqual(by["HoModifier"].value, 1.25)
        self.assertEqual(by["PawnCatchupLvDiff"].file, "PointModifierSettings")
        for bad in (["Nope=1"], ["EnemyExpModifier"], ["=1"], ["EnemyExpModifier=x"]):
            with self.assertRaises(RiftError, msg=bad):
                ddo_solo.with_overrides(src, ddo_solo.SETTINGS, bad)


class ModTest(unittest.TestCase):
    def test_plan_write_and_collect(self):
        p = ddo_solo.plan(sources())
        self.assertEqual(sorted(p.files), sorted(ddo_solo.OUTPUTS))
        self.assertIn("4 named-param entries -> 16 (12 solo twins", p.notes[0])
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "Solo Balance"
            written = ddo_solo.write(root, p)
            self.assertEqual(sorted(written), sorted(p.files))
            m = mod.Mod.load(root)
            self.assertEqual((m.name, m.game), ("Solo Balance", "ddo"))
            changes = {(c.name, c.type_id): c for c in mod.collect(m)}
            self.assertEqual(set(changes), {(ddo_solo.NDP_NAME, NDP), (ddo_solo.GMD_NAME, GMD)})
            self.assertEqual(changes[(ddo_solo.NDP_NAME, NDP)].data, p.files[ddo_solo.NDP_FILE])
            server = mod.collect_server(m)
            self.assertEqual(sorted(server), sorted(r[len("server/"):] for r in p.files if r.startswith("server/")))
            self.assertEqual(json.loads(server["named_param.ndp.json"])["fileSize"], len(p.files[ddo_solo.NDP_FILE]))
            rec = json.loads((root / ddo_solo.RECORD).read_text(encoding="utf-8"))
            self.assertEqual((rec["generator"], rec["twins"], rec["ids"]), (ddo_solo.GENERATOR, 12, [47, 3250]))
            self.assertEqual(rec["settings"]["GameServerSettings"]["values"]["RiftModifier"], 2)
            gs = server["scripts/settings/GameServerSettings.csx"].decode()
            self.assertTrue(gs.startswith("// Solo Balance (Riftstone, riftstone ddo solo): the server's template"))
            self.assertIn("double JpModifier = 1.5;\r\n", gs)
            self.assertIn("double HoModifier = 1;\r\n", gs)
            self.assertIn("double AdditionalProductionSpeedFactor = 0.0;\r\n", gs)  # crafts finish at once
            # a second run without settings takes its own settings files out, and nothing else
            (root / "server" / "mine.json").write_text("{}", encoding="utf-8")
            rec["files"]["../outside.txt"] = "x"                          # a tampered record reaches nothing
            (root / ddo_solo.RECORD).write_text(json.dumps(rec), encoding="utf-8")
            (Path(d) / "outside.txt").write_text("keep", encoding="utf-8")
            ddo_solo.write(root, ddo_solo.plan(sources(), settings=()))
            self.assertFalse((root / "server" / "scripts" / "settings" / "GameServerSettings.csx").exists())
            self.assertTrue((root / "server" / "mine.json").exists())
            self.assertTrue((Path(d) / "outside.txt").exists())
            self.assertTrue((root / "server" / ddo_solo.SCRIPT).exists())

    def test_a_hand_edited_record_is_read_as_far_as_it_goes(self):
        # was: TypeError ({"files": 5} or null), an unhashable list ({"files": [["a"]]}) and RecursionError (deep
        # nesting) out of write, before anything was written
        settings = "server/scripts/settings/GameServerSettings.csx"
        deep = 100_000
        for bad in ('{"files": 5}', '{"files": null}', '{"files": [["a"]]}', '[["a"]]', '"files"', "7",
                    "[" * deep + "]" * deep, '{"files": ' + "[" * deep + "]" * deep + "}",
                    json.dumps({"files": [["a"], {"b": 1}, 5, settings]})):
            with self.subTest(bad=bad[:40]), tempfile.TemporaryDirectory() as d:
                root = Path(d) / "Solo Balance"
                ddo_solo.write(root, ddo_solo.plan(sources()))
                (root / ddo_solo.RECORD).write_text(bad, encoding="utf-8")
                p = ddo_solo.plan(sources(), settings=())
                self.assertEqual(sorted(ddo_solo.write(root, p)), sorted(p.files))
                rec = json.loads((root / ddo_solo.RECORD).read_text(encoding="utf-8"))
                self.assertEqual(sorted(rec["files"]), sorted(p.files))
                # the one path the record still names is this generator's own output: taken out as usual
                self.assertEqual((root / settings).exists(), settings not in bad)

    def test_refuses_a_dark_arisen_mod(self):
        with tempfile.TemporaryDirectory() as d:
            m = mod.Mod.create(Path(d) / "M", "M", game="ddda")
            with self.assertRaises(RiftError):
                ddo_solo.write(m.root, ddo_solo.plan(sources()))

    def test_missing_template(self):
        src = sources()
        del src.settings["PointModifierSettings"]
        with self.assertRaises(RiftError) as cm:
            ddo_solo.plan(src)
        self.assertIn("start it once", str(cm.exception))
        self.assertEqual(len(ddo_solo.plan(src, settings=[s for s in ddo_solo.SETTINGS
                                                          if s.file == "GameServerSettings"]).files), 5)
        with self.assertRaises(RiftError):
            ddo_solo.plan(src, settings=[Setting("DebugSettings", "X", 1, "")])


class _Idx:
    def archives_with(self, name, tid):
        return {(ddo_solo.NDP_NAME, NDP): ["rom/game_common"], (ddo_solo.GMD_NAME, GMD): ["rom/ui/gui_cmn"]}.get(
            (name, tid), [])


class InstallTest(unittest.TestCase):
    """The whole path on a stand-in client and server: generate, install, regenerate while installed,
    restore."""

    def test_generate_install_restore(self):
        t = small_table()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "ddo"
            (root / "nativePC" / "rom" / "ui").mkdir(parents=True)
            (root / "DDO.exe").write_bytes(b"")
            other = arc.Entry.from_data(b"param\\other", typemap.BY_EXT["gmd"], b"keep me", encrypted=True)
            common = arc.Archive([other, arc.Entry.from_data(ddo_solo.NDP_NAME, NDP, ddo_params.build(t),
                                                             encrypted=True)], encrypted=True).build()
            ui = arc.Archive([arc.Entry.from_data(ddo_solo.GMD_NAME, GMD, gmd.build(names_file()), encrypted=True)],
                             encrypted=True).build()
            (root / "nativePC" / "rom" / "game_common.arc").write_bytes(common)
            (root / "nativePC" / "rom" / "ui" / "gui_cmn.arc").write_bytes(ui)
            assets = Path(d) / "assets"
            (assets / "scripts" / "settings" / "templates").mkdir(parents=True)
            json_raw = ddo.dumps_style(server_doc(t), ("jackson", ""))
            (assets / "named_param.ndp.json").write_bytes(json_raw)
            (assets / "EnemySpawn.json").write_bytes(b'{"schemas": {"enemies": []}, "enemies": []}')
            (assets / "scripts" / "settings" / "templates" / "GameServerSettings.csx").write_text(GS_TEMPLATE, "utf-8",
                                                                                                  newline="")
            (assets / "scripts" / "settings" / "templates" / "PointModifierSettings.csx").write_text(PM_TEMPLATE,
                                                                                                     "utf-8", newline="")
            game = Game(root, "ddo")
            old = os.environ.get("RIFTSTONE_DDO_ASSETS")
            os.environ["RIFTSTONE_DDO_ASSETS"] = str(assets)
            try:
                modroot = Path(d) / "mods" / "Solo Balance"
                p, written = ddo_solo.generate(game, _Idx(), modroot)
                self.assertEqual(len(written), 6)
                rep = install.apply(game, _Idx(), [modroot])
                self.assertEqual(sorted(w["archive"] for w in rep.written), ["rom/game_common", "rom/ui/gui_cmn"])
                self.assertEqual(sorted(rep.server_written), sorted(r[7:] for r in p.files if r.startswith("server/")))
                live = arc.Archive.read(root / "nativePC" / "rom" / "game_common.arc")
                self.assertEqual(live.find(ddo_solo.NDP_NAME, NDP).data(), p.files[ddo_solo.NDP_FILE])
                self.assertEqual(live.find(b"param\\other", GMD).data(), b"keep me")
                self.assertEqual((assets / "named_param.ndp.json").read_bytes(), p.files["server/named_param.ndp.json"])
                self.assertTrue((assets / "scripts" / "enemies" / "instance_properties" / "solo_balance.csx").is_file())
                self.assertEqual(install.status(game)["drift"], [])
                # while installed, a new run still starts from the game's and the server's own files
                again, _ = ddo_solo.generate(game, _Idx(), modroot, tiers=[ddo_solo.tier("field", hp=0.9)])
                self.assertEqual(again.record["sources"]["server_json"],
                                 str(game.state_dir / "server-vanilla" / "named_param.ndp.json"))
                self.assertEqual(again.record["source_sha256"]["server_json"], ddo_solo._sha(json_raw))
                self.assertEqual(len(ddo_params.parse(again.files[ddo_solo.NDP_FILE], "ndp").data["mpArray"]), 8)
                done = install.restore_all(game)
                self.assertEqual(len(done), 2 + 4)
                self.assertEqual((root / "nativePC" / "rom" / "game_common.arc").read_bytes(), common)
                self.assertEqual((root / "nativePC" / "rom" / "ui" / "gui_cmn.arc").read_bytes(), ui)
                self.assertEqual((assets / "named_param.ndp.json").read_bytes(), json_raw)
                self.assertFalse((assets / "scripts" / "enemies").exists() and
                                 any((assets / "scripts" / "enemies").rglob("*.csx")))
                self.assertFalse((assets / "scripts" / "settings" / "GameServerSettings.csx").exists())
            finally:
                if old is None:
                    os.environ.pop("RIFTSTONE_DDO_ASSETS", None)
                else:
                    os.environ["RIFTSTONE_DDO_ASSETS"] = old

    def test_needs_online(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "nativePC" / "rom").mkdir(parents=True)
            with self.assertRaises(RiftError):
                ddo_solo.sources(Game(Path(d), "ddda"), _Idx())


@unittest.skipUnless(ddo_found(), "Dragon's Dogma Online not found")
class CorpusTest(unittest.TestCase):
    """The real client file against the local server's copy, and the mod made from them."""

    @classmethod
    def setUpClass(cls):
        from riftstone.game import find_game

        if not helpers.ddo_key_present():
            raise unittest.SkipTest("set RIFTSTONE_DDO_KEY (or %LOCALAPPDATA%\\Riftstone\\ddo.key) to run the DDO corpus")
        cls.game = find_game("ddo")
        try:
            ddo.need_assets(cls.game)
            cls.src = ddo_solo.sources(cls.game, _Idx())
        except RiftError as e:
            raise unittest.SkipTest(str(e))

    def test_client_and_server_tables_agree(self):
        m = ddo_params.parse(self.src.ndp, "ndp")
        self.assertEqual(ddo_params.build(m), self.src.ndp)
        doc = json.loads(self.src.server_json.decode("utf-8-sig"))
        recs = m.data["mpArray"]
        self.assertEqual(len(recs), len(doc["namedParamList"]))
        for r, e in zip(recs, doc["namedParamList"]):
            self.assertEqual(r, {k: e[j] for k, j in ddo_solo.JSON_KEYS.items()})
        self.assertEqual(doc["fileSize"], len(self.src.ndp))
        g = gmd.parse(self.src.gmd)
        labels = {x.label for x in g.messages}
        self.assertTrue(all(f"namedparam_{r['mID']}" in labels for r in recs))   # every id has its name label

    def test_the_mod_from_the_real_files(self):
        p = ddo_solo.plan(self.src)
        m = ddo_params.parse(p.files[ddo_solo.NDP_FILE], "ndp")
        ids = [r["mID"] for r in m.data["mpArray"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertLess(len(ids), 0x8000)
        self.assertNotEqual(max(ids) % 4, 0)
        g = gmd.parse(p.files[ddo_solo.GMD_FILE])
        self.assertLess(len(g.messages), 0x8000)
        labels = {x.label for x in g.messages}
        self.assertTrue(all(f"namedparam_{i}" in labels for i in ids))
        doc = json.loads(p.files["server/named_param.ndp.json"])
        self.assertEqual([e["ID"] for e in doc["namedParamList"]], ids)
        for r, e in zip(m.data["mpArray"], doc["namedParamList"]):
            self.assertEqual(r, {k: e[j] for k, j in ddo_solo.JSON_KEYS.items()})


if __name__ == "__main__":
    unittest.main()

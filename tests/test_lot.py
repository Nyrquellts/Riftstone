import struct
import unittest

import helpers  # noqa: F401 (sys.path)
from riftstone import inspect, lot, params, typemap
from riftstone.errors import FormatError, ParamError


def goblin(rid=0, pos=(1.0, 2.0, 3.0), **kw):
    return lot.blank(4, rid, mName="em0100", mPosition=pos, mAngle=(0.0, 1.5, 0.0), mOrder=4,
                     mFsmFilePath="ai\\em0100.fsm", mEquipType=2, **kw)


def stage_action_target(rid=0):
    fields = {name: {"f32": 0, "v3": (0, 0, 0), "s16list": []}.get(t, 0)
              for name, t in lot.SENSORS["cAISensorTargetStageAction"]}
    fields["mActParamIdx"] = [3, -1, 7]
    fields["ITEM_NO"] = 0xFFFF
    return lot.Record(rid, 54, {"mpTarget": lot.Sensor("cAISensorTargetStageAction", fields)})


def sample() -> lot.Lot:
    npc = lot.blank(47, 5, mName="em1000", FSMPath="ai\\npc.fsm", mHumanEnemyKind=3, mPosition=(9.0, 0.0, -4.5))
    door = lot.blank(53, 9, mName="om7055", mLockMsgNo=12, mCheckRange=150.0, mPosition=(0.5, 0.25, 0.125))
    return lot.Lot([goblin(0), npc, door, stage_action_target(11)])


SAMPLE = lot.build(sample())


class LotTest(unittest.TestCase):
    def test_every_field_round_trips(self):
        lt = lot.parse(SAMPLE)
        self.assertEqual([r.cls for r in lt.records],
                         ["cSetInfoEnemy0100", "cSetInfoNpc", "cSetInfoOmDoorMsg", "cSetInfoSensorTarget"])
        self.assertEqual([r.id for r in lt.records], [0, 5, 9, 11])
        self.assertEqual(lot.build(lt), SAMPLE)
        text = params.resource_to_yaml(SAMPLE, "scr\\st424\\etc\\st424_00m00n_e36")
        self.assertIn("riftstone: lot/2", text)
        self.assertIn("Group 36 of stage 424's enemies", text)
        self.assertIn("HPの倍率: 0.0  # HP multiplier", text)
        self.assertEqual(params.yaml_to_resource(text), SAMPLE)
        self.assertEqual(lt.records[3].fields["mpTarget"].fields["mActParamIdx"], [3, -1, 7])
        self.assertEqual(len(lt.placements), 3)                 # the sensor target has no position

    def test_the_record_layout_is_the_loaders(self):
        # a goblin: its own fields, then cSetInfoEnemy's, cSetInfoPawn's, and cSetInfoCoord's last
        data = lot.build(lot.Lot([goblin(7)]))
        rid, kind, equip = struct.unpack_from("<iII", data, 12)
        self.assertEqual((rid, kind, equip), (7, 4, 2))
        tail = struct.unpack("<3f3f3ffB", data[-41:])
        self.assertEqual(tail[:3], (1.0, 2.0, 3.0))
        self.assertEqual(tail[6:10], (1.0, 1.0, 1.0, -1.0))
        self.assertIn(b"em0100\0\x04\0\0\0", data)             # name, then mOrder

    def test_yaml_edits(self):
        text = lot.to_yaml(lot.parse(SAMPLE))
        text = text.replace("mPosition: [1.0, 2.0, 3.0]", "mPosition: [10.5, 2.0, -3.25]", 1)
        text = text.replace("HP倍率設定の有無: 0", "HP倍率設定の有無: 1", 1).replace("HPの倍率: 0.0", "HPの倍率: 2.5", 1)
        new = lot.parse(lot.yaml_to_bytes(text))
        g = new.records[0]
        self.assertEqual(g.vec(), (10.5, 2.0, -3.25))
        self.assertEqual((g.fields["HP倍率設定の有無"], struct.unpack("<f", struct.pack("<I", g.fields["HPの倍率"]))[0]),
                         (1, 2.5))

    def test_bad_yaml_is_refused(self):
        text = lot.to_yaml(lot.parse(SAMPLE))
        for bad in (text.replace("class: cSetInfoEnemy0100", "class: cSetInfoDragon"),
                    text.replace("    mEquipType: 2\n", "", 1),                          # a field left out
                    text.replace("mEquipType: 2", "mEquipType: 2\n    mColour: 3", 1),   # a field that is not there
                    text.replace("mEquipType: 2", "mEquipType: -1", 1),                  # u32 out of range
                    text.replace("mPosition: [1.0, 2.0, 3.0]", "mPosition: [1.0, 2.0]", 1),
                    text.replace("mPosition: [1.0, 2.0, 3.0]", "mPosition: [nan:0x-5, 2.0, 3.0]", 1),
                    text.replace('mFsmFilePath: "ai\\\\em0100.fsm"', 'mFsmFilePath: "' + "a" * 64 + '"', 1),
                    text.replace("mActParamIdx: [3, -1, 7]", "mActParamIdx: [" + ", ".join(["1"] * 17) + "]"),
                    text.replace("class: cAISensorTargetStageAction", "class: cAISensorTargetDragon"),
                    text.replace("version: 16", "version: 15"),
                    text.replace("  - id: 5\n", "  - ident: 5\n", 1)):
            self.assertNotEqual(bad, text)
            with self.assertRaises(ParamError, msg=bad[:200]):
                lot.yaml_to_bytes(bad, "t.yaml")

    def test_copy_and_remove(self):
        lt = lot.parse(SAMPLE)
        c = lot.copy(lt, 0, (5.0, 6.0, 7.0))
        self.assertEqual((c.count, c.records[-1].id, c.records[-1].name), (5, 12, "em0100"))   # id: largest + 1
        self.assertEqual(c.records[-1].vec(), (5.0, 6.0, 7.0))
        self.assertEqual(lot.build(lot.remove(c, 4)), SAMPLE)                               # copy then remove: exact
        r = lot.remove(lt, 0)
        self.assertEqual([x.id for x in r.records], [5, 9, 11])                             # ids are never renumbered
        self.assertEqual(lot.build(lt), SAMPLE)                                             # the source is untouched
        with self.assertRaises(ParamError):
            lot.copy(lt, 9)
        with self.assertRaises(ParamError):
            lot.copy(lt, 3, (1.0, 2.0, 3.0))                                                # a sensor has no position
        with self.assertRaises(ParamError):
            lot.copy(lt, 0, (float("inf"), 0.0, 0.0))

    def test_a_coordinate_past_a_float_is_refused(self):
        # was: OverflowError converting a 400-digit integer to float (Studio's lot copy with "at")
        from riftstone.studio import Studio

        lt = lot.parse(SAMPLE)
        for at in ((10 ** 400, 0, 0), (0.0, -10 ** 400, 0.0)):
            with self.assertRaises(ParamError):
                lot.copy(lt, 0, at)
            with self.assertRaises(ParamError):
                goblin().set_vec("mPosition", at)
            with self.assertRaises(ParamError):
                Studio.lot_edit({"text": lot.to_yaml(lt), "op": "copy", "number": 0, "at": list(at)})
        self.assertEqual(lot.copy(lt, 0, (10 ** 6, 0, 0)).records[-1].vec(), (1e6, 0.0, 0.0))

    def test_text_that_cannot_be_written_is_refused_on_its_line(self):
        # was: UnicodeEncodeError ('surrogates not allowed'; surrogateescape covers only \udc80-\udcff), which
        # Studio's validate answered with a 500 and a build with a traceback
        text = lot.to_yaml(lot.parse(SAMPLE))
        for field, old in (("mFsmFilePath", '"ai\\\\em0100.fsm"'), ("mName", '"em0100"')):
            line = next(i for i, x in enumerate(text.splitlines(), 1) if x.strip() == f"{field}: {old}")
            bad = text.replace(f"{field}: {old}", f'{field}: "a\\ud800b"', 1)
            with self.assertRaises(ParamError) as e:
                lot.yaml_to_bytes(bad, "t.yaml")
            self.assertEqual(e.exception.line, line, field)
            self.assertIn(field, str(e.exception))
        kept = text.replace('mName: "em0100"', 'mName: "em\\udc800100"', 1)      # a byte that is not UTF-8
        self.assertEqual(lot.parse(lot.yaml_to_bytes(kept)).records[0].fields["mName"], b"em\x800100")

    def test_a_copy_keeps_clear_of_its_group(self):
        """Reserved ids (the group's other layouts, the game's own) are never taken; for an enemy group (below=32,
        the kill record's bits) an id under 32 comes first when the largest + 1 is not under it."""
        lt = lot.parse(SAMPLE)
        top = max(r.id for r in lt.records)
        self.assertEqual(lot.free_id(lt, reserved={top + 1, top + 2}), top + 3)
        self.assertEqual(lot.copy(lt, 0, None, {top + 1}).records[-1].id, top + 2)
        self.assertEqual(lot.free_id(lot.Lot([goblin(3)]), reserved={0, 1, 2, 4}, below=32), 5)   # largest + 1
        self.assertEqual(lot.free_id(lot.Lot([goblin(31)]), below=32), 0)          # 32 would share 0's bit
        self.assertEqual(lot.free_id(lot.Lot([goblin(31)]), reserved={0, 1}, below=32), 2)
        self.assertEqual(lot.free_id(lot.Lot([goblin(i) for i in range(32)]), below=32), 32)   # none under 32
        self.assertEqual(lot.free_id(lot.Lot([goblin(31)])), 32)                   # other groups: largest + 1

    def test_ids_stay_in_the_games_table(self):
        full = lot.Lot([goblin(1023)])
        self.assertEqual(lot.free_id(full), 0)                      # past 1023: the smallest unused id
        lot.check_ids(full)
        with self.assertRaises(ParamError):
            lot.check_ids(lot.Lot([goblin(1024)]))
        with self.assertRaises(ParamError):
            lot.check_ids(lot.Lot([goblin(3), goblin(3)]))

    def test_rejects(self):
        for data in (b"lot\0" + b"\0" * 4,                                      # short header
                     b"LOT\0" + SAMPLE[4:],                                     # magic
                     SAMPLE[:4] + struct.pack("<I", 15) + SAMPLE[8:],           # version
                     SAMPLE + b"\0",                                            # trailing byte
                     SAMPLE[:-1],                                               # cut short
                     SAMPLE[:8] + struct.pack("<I", 0x7FFFFFFF) + SAMPLE[12:],  # absurd count
                     SAMPLE[:12] + struct.pack("<iI", 0, 99) + SAMPLE[20:]):    # unknown kind
            with self.assertRaises(FormatError):
                lot.parse(data)

    def test_legacy_yaml_still_loads(self):
        # a lot/1 file (mods made before the full decode): the file in hex plus the placements found
        body = SAMPLE.hex()
        o = SAMPLE.index(b"em0100\0") + 7 + 4               # after the name and mOrder: the position
        old = ("riftstone: lot/1\nplacements:\n  - name: \"em0100\"\n    offset: " + str(o - 4) + "\n"
               "    position: [8.0, 2.0, 3.0]\nbody: " + body + "\n")
        new = lot.parse(lot.yaml_to_bytes(old))
        self.assertEqual(new.records[0].vec(), (8.0, 2.0, 3.0))
        self.assertEqual(lot.parse(lot.yaml_to_bytes("riftstone: lot/1\nbody: " + body + "\n")).count, 4)
        # a huge hex offset: the message wrote it in decimal, a ValueError past 4,300 digits
        with self.assertRaises(ParamError) as e:
            lot.yaml_to_bytes(old.replace(f"offset: {o - 4}", "offset: 0x" + "f" * 3572), "t.yaml")
        self.assertLess(len(str(e.exception)), 200)

    def test_names(self):
        n = lot.parse_name("scr\\st100\\etc\\st100_43m55n_e67")
        self.assertEqual((n.stage, n.x, n.z, n.type, n.number, n.group_list), (100, 43, 55, "e", 67, "scr\\st100\\etc\\st100_e"))
        self.assertEqual(str(n), "scr\\st100\\etc\\st100_43m55n_e67")
        self.assertEqual(lot.layout_name(424, 0, 0, "e", 5), "scr\\st424\\etc\\st424_00m00n_e05")
        self.assertIsNone(lot.parse_name("scr\\st100\\etc\\st101_43m55n_e67"))
        self.assertIsNone(lot.parse_name("scr\\st100\\etc\\st100_e"))

    def test_inspect(self):
        rep = inspect.describe(SAMPLE, typemap.BY_EXT["lot"])
        self.assertTrue(rep.editable)
        self.assertIn("4 record(s), every field editable", rep.text("x"))


if __name__ == "__main__":
    unittest.main()

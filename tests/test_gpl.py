import struct
import unittest

import helpers  # noqa: F401 (sys.path)
from riftstone import gpl, inspect, params, typemap
from riftstone.errors import FormatError, ParamError


def f(x):
    return struct.unpack("<I", struct.pack("<f", x))[0]        # float -> its 32-bit bits


def vec(a, b, c, d):
    return [f(a), f(b), f(c), f(d)]


def shape(t):
    s = {"mName": "hit", "mCheckAngle": f(1.0), "mCheckRange": f(2.0), "mCheckToward": f(0.0),
         "mAngleFlag": 0, "mTowardFlag": 1, "type": t, "mDecay": f(0.5), "mIsNativeData": 0}
    if t == 1:
        s.update(mHeight=f(3.0), mBottom=f(-1.0), mVertex=[vec(1, 0, 0, 0) for _ in range(4)],
                 mConcaveCrossPos=vec(0, 0, 0, 0), mFlgConvex=1, mConcaveStatus=0)
    elif t == 2:
        s["mVertex"] = vec(5, 6, 7, 0)
    else:
        s.update(Position0=vec(0, 0, 0, 0), Position1=vec(1, 1, 1, 0), Radius=f(2.5), pad=[0, 0, 0])
    return s


def group():
    g = {"mGroupClass": 1, "mGroup": 7, "mPriority": 100, "mIsDisableSplit": 0, "mDLCNoBits": 0,
         "mUnitKindList": [{"name": "em0100", "isBelong": 1}, {"name": "", "isBelong": 0}],
         "mLayoutIDArray": [{"mLayoutID": 3, "mGroup": 7, "mSplitX": 1, "mSplitZ": 1}]}
    for name, _tc in gpl._COND_A + gpl._COND_B + gpl._COND_C:
        g[name] = 0
    g["mSetCountMax"] = 5
    g["mAreaHitShapeList"] = [shape(2)]
    g["mLifeAreaArray"] = [[shape(1)], []]
    g["mKillAreaType"] = 0
    g["mKillAreaList"] = [shape(3)]
    return g


SAMPLE = gpl.Gpl(version=1, mGroupList=[0x10, 0x20], mSetBit=[0xFF], mDLCNo=0, groups=[group(), group()])


class GplTest(unittest.TestCase):
    def test_round_trip(self):
        raw = gpl.build(SAMPLE)
        self.assertEqual(raw[:4], b"gpl\0")
        self.assertEqual(gpl.build(gpl.parse(raw)), raw)
        self.assertEqual(params.yaml_to_resource(params.resource_to_yaml(raw, "g")), raw)

    def test_edit_unit_and_cap(self):
        raw = gpl.build(SAMPLE)
        text = gpl.to_yaml(gpl.parse(raw))
        self.assertIn('mSetCountMax: 5', text)
        self.assertIn('name: "em0100"', text)
        text2 = text.replace("mSetCountMax: 5", "mSetCountMax: 30")   # bigger fight
        g = gpl.parse(gpl.yaml_to_bytes(text2))
        self.assertEqual(g.groups[0]["mSetCountMax"], 30)
        # add an enemy kind to the first group
        text3 = text.replace('      - name: "em0100"\n        isBelong: 1\n',
                             '      - name: "em0100"\n        isBelong: 1\n      - name: "em0200"\n        isBelong: 1\n', 1)
        g = gpl.parse(gpl.yaml_to_bytes(text3))
        self.assertEqual([u["name"] for u in g.groups[0]["mUnitKindList"]], ["em0100", "em0200", ""])

    def test_shape_types_round_trip(self):
        for t in (1, 2, 3):
            g = gpl.Gpl(1, [], [], 0, [dict(group(), mAreaHitShapeList=[shape(t)])])
            self.assertEqual(gpl.build(gpl.parse(gpl.build(g))), gpl.build(g))

    def test_refusals(self):
        raw = gpl.build(SAMPLE)
        with self.assertRaises(FormatError):
            gpl.parse(b"xxx\0" + raw[4:])
        with self.assertRaises(FormatError):
            gpl.parse(raw[:-1])
        text = gpl.to_yaml(gpl.parse(raw))
        for bad in (text.replace("mPriority: 100", "mPriority: 999999999"),   # 18-bit field
                    text.replace("mGroup: 7", "mGroup: 99999"),               # 9-bit field
                    text.replace('type: 2', 'type: 9'),                       # unknown shape
                    text.replace("riftstone: gpl/1", "riftstone: nope")):
            with self.assertRaises(ParamError, msg=bad[:200]):
                gpl.yaml_to_bytes(bad, "g.yaml")

    def test_unit_kinds_stop_at_what_the_game_holds(self):
        # cGroupParam::load (0x00CC4690) loads the file's count of kinds into 3 inline slots unchecked,
        # so a 4th would overwrite the game's memory: read, but refused on write, from binary and YAML
        kinds = [{"name": f"em{i:04d}", "isBelong": 1} for i in range(gpl.UNIT_KINDS_MAX)]
        ok = gpl.build(gpl.Gpl(1, [], [], 0, [dict(group(), mUnitKindList=kinds)]))
        self.assertEqual(len(gpl.parse(ok).groups[0]["mUnitKindList"]), 3)
        four = kinds + [{"name": "em9999", "isBelong": 1}]
        with self.assertRaises(ParamError) as cm:
            gpl.build(gpl.Gpl(1, [], [], 0, [dict(group(), mUnitKindList=four)]))
        self.assertIn("the game holds 3", str(cm.exception))
        text = gpl.to_yaml(gpl.parse(ok))
        more = text.replace('      - name: "em0000"\n', '      - name: "em9999"\n        isBelong: 1\n'
                                                         '      - name: "em0000"\n', 1)
        self.assertNotEqual(more, text)
        with self.assertRaises(ParamError):
            gpl.yaml_to_bytes(more, "g.yaml")
        # a file the game would choke on is still read, so it can be inspected and fixed
        body = ok.replace(struct.pack("<i", 3) + b"em0000\0", struct.pack("<i", 4) + b"em9999\0\1em0000\0", 1)
        self.assertEqual(len(gpl.parse(body).groups[0]["mUnitKindList"]), 4)
        self.assertIn("UNSAFE: group(s) 7", inspect.describe(body, typemap.BY_EXT["gpl"]).text("x"))
        self.assertNotIn("UNSAFE", inspect.describe(ok, typemap.BY_EXT["gpl"]).text("x"))

    def test_inspect(self):
        rep = inspect.describe(gpl.build(SAMPLE), typemap.BY_EXT["gpl"])
        self.assertTrue(rep.editable)
        out = rep.text("g")
        self.assertIn("2 group(s)", out)
        self.assertIn("cap 5", out)
        self.assertIn("em0100", out)


if __name__ == "__main__":
    unittest.main()

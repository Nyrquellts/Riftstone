import struct
import unittest

import helpers  # noqa: F401 (sys.path)
from riftstone import flat, params, typemap
from riftstone.errors import FormatError


def rt(case, ext, data):
    """A DDO table round-trips through bytes and YAML, and keeps its revision key."""
    f = flat.Flat(ext, 0, data)
    raw = flat.build(f)
    g = flat.parse(raw, ext)
    case.assertEqual(g.ext, ext)
    case.assertEqual(flat.build(g), raw)
    case.assertEqual(flat.yaml_to_bytes(flat.to_yaml(g, "x")), raw)
    return raw


class DdoVocationTablesTest(unittest.TestCase):
    def test_jcp(self):
        refs = [{"mPathCrc": 0xB005ED14 + i, "mType": 0x139EE51D if i == 0 else 0} for i in range(17)]
        raw = rt(self, "jcp", {"version": 1, "mpArray": [{"mSkillNo": 1, "mResource": refs}]})
        self.assertEqual(len(raw), 8 + 140)                                   # 140-byte records

    def test_csd_levels(self):
        rec = {"mSkillNo": 101, "mMsgIndex": 3, "mUnk04": 20, "mBaseSkillNo": 1, "mUnk08": 0,
               "mpLevel": [{"mLevel": 0, "mJobLevel": 0, "mJobPoint": 0}, {"mLevel": 1, "mJobLevel": 3, "mJobPoint": 300}]}
        raw = rt(self, "csd", {"version": 3, "mpArray": [rec]})
        self.assertEqual(len(raw), 8 + 13 + 8 * 2)

    def test_nsd_record_is_15_bytes(self):
        rec = {"mJobPoint": 700, "mJobLevel": 9, "mLearnOrder": 1, "mUnk08": 20, "mSkillNo": 2, "mMsgIndex": 1,
               "mUnk0C": 1, "mBaseSkillNo": 0, "mUnk0E": 1}
        self.assertEqual(len(rt(self, "nsd", {"version": 5, "mpArray": [rec, rec]})), 8 + 2 * 15)

    def test_small_tables(self):
        rt(self, "jmc", {"version": 256, "mpArray": [dict.fromkeys(
            ("mJobId", "mStartJobLevel", "mFirstTalkGrpSerial", "mTraningTalkGrpSerial", "mFirstOrderTalkGrpSerial",
             "mJobTutorialQuestId", "mJobMasterTutorialQuestId", "mAreaId", "mAreaRank"), 1)]})
        rt(self, "eir", {"version": 2, "mpJobBits": [0x0FFE, 0x2]})
        rt(self, "wcrt", {"version": 1, "mpArray": [{"mCategory": 1, "mComment": "wep300", "mEpvCrc": 5,
                                                     "mEpvType": 6, "mSrqCrc": 7, "mSrqType": 8}]})
        rt(self, "dja", {"version": 9, "mpArray": [{"mJobType": 1, "mRate": [1.0] * 15}]})
        rt(self, "jlt2", {"version": 1, "mpArray": [{"mValue": [9, 40, 30, 20, 20]}]})
        raw = rt(self, "jtq", {"version": 1, "mQuestId": [60000003, 60000005]})
        self.assertEqual(raw[:4], b"JTQ\0")
        self.assertEqual(len(raw), 4 + 2 + 4 + 8)

    def test_ajp_revision(self):
        ddo = struct.pack("<II", 0x100, 2) + struct.pack("<2f", 0.55, 1.1)
        f = flat.parse(ddo, "ajp")                                          # asked as ajp, read as DDO's revision
        self.assertEqual(f.ext, "ajp-ddo")
        self.assertEqual(flat.build(f), ddo)
        y = flat.to_yaml(f)
        self.assertIn("riftstone: ajp-ddo/1", y)
        self.assertEqual(params.yaml_to_resource(y), ddo)                   # the YAML tag routes back
        self.assertEqual(params.resource_to_yaml(ddo, "x", typemap.BY_EXT["ajp"]), flat.to_yaml(f, "x"))
        ddda = flat.build(flat.Flat("ajp", 0, {"version": 513, "mpArray": [1.0]}))
        self.assertEqual(flat.parse(ddda, "ajp").ext, "ajp")                # the magic still wins
        with self.assertRaises(FormatError):
            flat.parse(struct.pack("<II", 0x100, 5) + bytes(4), "ajp")      # neither layout fits

    def test_types_known(self):
        for ext in ("jcp", "csd", "nsd", "jmc", "eir", "wcrt", "dja", "jlt2", "jtq"):
            self.assertIn(ext, typemap.BY_EXT, ext)                         # DDO's type table names them


if __name__ == "__main__":
    unittest.main()

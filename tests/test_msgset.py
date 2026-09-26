import os
import struct
import unittest

import helpers  # noqa: F401 (sys.path)
from riftstone import msgset
from riftstone.errors import FormatError, ParamError, RiftError
from riftstone.msgset import MsgGroup, MsgSerial, MsgSet, MsgSetDdo


def param(no, lines, face=-1) -> dict:
    tbl = lines + [-1] * (15 - len(lines))
    return {"mNo": no, "mNpcId": -1, "mSelMsgTbl": [3, -1, -1, -1, -1, -1], "mSelMsgJump": [2, -1, -1, -1, -1, -1],
            "mMsgTbl": tbl, "mMsgMotTbl": [5] * 15, "mMsgFaceTbl": [face] * 15, "mGfsFlgNo_msg": [-1] * 15,
            "mQuestNo_msg": [-1] * 15, "mQuestFlgNo_msg": [-1] * 15, "mSeason": 0, "mSeasonSequence": -1,
            "mSeasonSequenceType": 0, "mQuestNo": 12, "mQuestFlgNo": 3, "mFriend": -1, "mFriendMax": 100,
            "mGsfFlgNo": -1}


def data(serial, gmd, voice=-1) -> dict:
    return {"mMsgSerial": serial, "mGmdIndex": gmd, "mMsgType": 0, "mJumpGroupSerial": 0, "mDispType": 1,
            "mDispTime": 0, "mSetMotion": 7, "mVoiceReqNo": voice, "mTalkFaceType": 1}


def ddo_sample() -> MsgSetDdo:
    return MsgSetDdo([MsgGroup(20230, 5119, 0, 68, 0, [data(537273, 0), None, data(537274, 1, 12)]),
                      None,
                      MsgGroup(20231, 5119, 7, 69, 1, [])])


def game_found(kind: str) -> bool:
    from riftstone.game import find_game
    if os.environ.get("RIFTSTONE_SKIP_GAME"):
        return False
    if kind == "ddo" and not helpers.ddo_key_present():
        return False
    try:
        return find_game(kind).kind == kind
    except RiftError:
        return False


class MsgSetTest(unittest.TestCase):
    def test_ddda_round_trip_and_layout(self):
        m = MsgSet([param(1, [0, 1, 2]), param(2, [6], face=3)])
        raw = msgset.build(m)
        self.assertEqual(struct.unpack_from("<4sII", raw, 0), (b"mss\0", 0x20, 2))
        self.assertEqual(len(raw), 12 + 2 * msgset.PARAM_SIZE)
        self.assertEqual(struct.unpack_from("<ih", raw, 12), (1, -1))                  # mNo, mNpcId
        self.assertEqual(struct.unpack_from("<3i", raw, 12 + 6 + 24 + 12), (0, 1, 2))  # mMsgTbl
        self.assertEqual(msgset.parse(raw), m)
        y = msgset.to_yaml(m, "id\\npc_wind\\npc_base_talk\\n001")
        self.assertIn("riftstone: mss/1", y)
        self.assertEqual(msgset.yaml_to_bytes(y), raw)
        self.assertIn("2 conversation(s), 4 line(s)", msgset.info(m))

    def test_msl_round_trip(self):
        m = MsgSerial([713, 714, 10823, 0, 65535])
        raw = msgset.build(m)
        self.assertEqual(raw, b"msl\0" + struct.pack("<II5H", 0x10, 5, 713, 714, 10823, 0, 65535))
        self.assertEqual(msgset.parse(raw), m)
        self.assertEqual(msgset.yaml_to_bytes(msgset.to_yaml(m)), raw)
        self.assertEqual(msgset.build(msgset.parse(b"msl\0" + struct.pack("<II", 0x10, 0))), b"msl\0" + struct.pack("<II", 0x10, 0))

    def test_ddo_round_trip_and_layout(self):
        m = ddo_sample()
        raw = msgset.build(m)
        self.assertEqual(struct.unpack_from("<4sHII", raw, 0), (b"mgst", 3, 3, 2))    # 3 group slots, 2 lines
        self.assertEqual(raw[14], 1)                                                  # group 0 present
        self.assertEqual(struct.unpack_from("<IIIIBI", raw, 15), (20230, 5119, 0, 68, 0, 3))
        self.assertEqual(msgset.parse(raw), m)
        y = msgset.to_yaml(m, "ui\\00_message\\x")
        self.assertIn("riftstone: mss-ddo/1", y)
        self.assertEqual(msgset.yaml_to_bytes(y), raw)
        self.assertIn("3 group(s), 2 line(s)", msgset.info(m))

    def test_refusals(self):
        raw = msgset.build(MsgSet([param(1, [0])]))
        for bad in (b"msx\0" + raw[4:], raw[:10], raw[:4] + struct.pack("<I", 0x21) + raw[8:],
                    raw[:8] + struct.pack("<I", 2) + raw[12:], raw[:-1], raw + b"\0",
                    raw[:8] + struct.pack("<I", 0xFFFFFFFF) + raw[12:]):
            with self.assertRaises(FormatError):
                msgset.parse(bad)
        msl = msgset.build(MsgSerial([1, 2]))
        for bad in (msl[:-1], msl + b"\0", msl[:4] + struct.pack("<I", 0x11) + msl[8:]):
            with self.assertRaises(FormatError):
                msgset.parse(bad)
        ddo = msgset.build(ddo_sample())
        for bad in (ddo[:4] + struct.pack("<H", 4) + ddo[6:],                 # version
                    ddo[:10] + struct.pack("<I", 3) + ddo[14:],               # line count disagrees
                    ddo[:14] + b"\2" + ddo[15:],                              # presence byte 2
                    ddo[:6] + struct.pack("<I", 1 << 30) + ddo[10:],          # absurd group count
                    ddo[:-1], ddo + b"\0"):
            with self.assertRaises(FormatError):
                msgset.parse(bad)
        wide = MsgSet([param(1, [0])])
        wide.params[0]["mNpcId"] = 40000                                      # s16
        with self.assertRaises(FormatError):
            msgset.build(wide)
        short = MsgSet([param(1, [0])])
        short.params[0]["mSelMsgTbl"] = [1, 2]
        with self.assertRaises((FormatError, ParamError)):
            msgset.build(short)

    def test_yaml_refusals(self):
        y = msgset.to_yaml(MsgSet([param(1, [0, 1])]))
        for bad in (y.replace("mNpcId: -1", "mNpcId: 40000"),
                    y.replace("mSelMsgTbl: [3, -1, -1, -1, -1, -1]", "mSelMsgTbl: [3, -1]"),
                    y.replace("mSeason: 0", "mSeason: 0\n    mWinter: 1"),
                    y.replace("mQuestNo: 12", "mQuestNo: x"),
                    y.replace("riftstone: mss/1", "riftstone: msx/1")):
            self.assertNotEqual(bad, y)
            with self.assertRaises(ParamError):
                msgset.from_yaml(bad)
        yd = msgset.to_yaml(ddo_sample())
        for bad in (yd.replace("mNameDispOff: 1", "mNameDispOff: 256"),
                    yd.replace("mVoiceReqNo: 12", "mVoiceReqNo: 2147483648"),
                    yd.replace("mGroupType: 68", "mGroupKind: 68")):
            self.assertNotEqual(bad, yd)
            with self.assertRaises(ParamError):
                msgset.from_yaml(bad)

    def _corpus(self, kind: str, exts: tuple, want: dict):
        from riftstone import corpus, typemap
        from riftstone.game import find_game
        ids = {typemap.BY_EXT[e]: e for e in exts}
        seen = dict.fromkeys(exts, 0)
        for r in corpus.resources(find_game(kind), list(ids)):
            m = msgset.parse(r.data)
            self.assertEqual(msgset.build(m), r.data, r.label)
            self.assertEqual(msgset.yaml_to_bytes(msgset.to_yaml(m, r.name.decode("latin-1")), r.label), r.data, r.label)
            seen[ids[r.type_id]] += 1
        for ext, n in want.items():
            self.assertGreaterEqual(seen[ext], n, ext)

    @unittest.skipUnless(game_found("ddda"), "Dragon's Dogma: Dark Arisen not found")
    def test_corpus_ddda(self):
        self._corpus("ddda", ("mss", "msl"), {"mss": 830, "msl": 752})

    @unittest.skipUnless(game_found("ddo"), "Dragon's Dogma Online not found")
    def test_corpus_ddo(self):
        self._corpus("ddo", ("mss",), {"mss": 3187})


if __name__ == "__main__":
    unittest.main()

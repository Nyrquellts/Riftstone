import os
import struct
import unittest
from pathlib import Path

import helpers  # noqa: F401 (sys.path)
from riftstone import compat, ocl, ocl_ddo
from riftstone.errors import ParamError, RiftError


def bits(x: float) -> int:
    return struct.unpack("<I", struct.pack("<f", x))[0]


def ddo_collision() -> ocl_ddo.OclDdo:
    geo = {n: 0 for n, _, _ in ocl_ddo.GEOM_FIELDS}
    geo.update(mShape=0, mPriority=7, mIndex=0, mJnt0=4, mJnt1=-1, mRegionNo=3, mRadius=bits(20.0),
               mAngle0=(0, 0), mAngle1=(0, 0), mOffset0=(bits(1.0), bits(2.0), bits(3.0)), mOffset1=(0, 0, 0),
               mExtent=(0, 0, 0), mIsUseData=1)
    cap = dict(geo, mShape=1, mIndex=1, mJnt1=13)
    o = ocl_ddo.OclDdo()
    o.nodes = [{"mAttr": 0x1 | 0x40 | 0x200, "mNodeID": 0, "mIndex": 0, "mColNodeFlag": 0, "mHitCollisionFlag": 0,
                "geoms": [geo, cap]}]
    row = {n: -1 for n, _, _ in ocl_ddo.INDEX_FIELDS}
    row.update(mNode=0, mAttack=0, mLinkID=5, mLinkID2=0, mLinkID3=0, mLinkTop=1)
    row2 = dict(row, mNode2=0, mAttack2=0)
    o.index = [row, row2]
    return o


def ddo_attack(**kw) -> dict:
    r = {f"mUnk{off:02X}": 0 for off, _t in ocl_ddo._ATTACK_ORDER}
    r.update({"mUnk04": 0, "mUnk0D": 3, "mUnk08": bits(1.5), "mUnk14": bits(0.5), "mUnk6C": bits(4.0),
              "mUnkC0": 0x1004, "mUnkDA": 75, "mUnkC4": 0x100B, "mUnkD8": 10})
    r.update(kw)
    return r


class CompatTest(unittest.TestCase):
    def test_resource_ids(self):
        self.assertEqual(compat.resource_id("job09_atk"), 19)
        self.assertEqual(compat.resource_id("cs03"), 22)
        self.assertEqual(compat.resource_id("cs03_ex01"), 43)
        self.assertEqual(compat.resource_id("cs12_ex02"), 62)
        for bad in ("cs3", "cs16_ex02", "job09", "cs01_ex03"):
            with self.assertRaises(ParamError, msg=bad):
                compat.resource_id(bad)

    def test_motion_slots(self):
        self.assertEqual(compat.motion_slots("obj\\pl\\pl000000\\motion\\m0009\\m0009_at\\m0009_at"), {1: 19})
        self.assertEqual(compat.motion_slots("m0009_cs03"), {1: 19, 2: 22})
        self.assertEqual(compat.motion_slots("x\\m0009_cs05_ex02"), {1: 19, 2: 55})

    def test_collision_groups(self):
        out, rep = compat.convert_collision(ddo_collision(), 22)
        data = ocl.build(out)
        back = ocl.parse(data)
        self.assertEqual(back.resource_id, 22)
        g = back.groups[0]
        self.assertEqual((g["mIsAttack"], g["mIsCheck"], g["mIsDamage"]), (1, 1, 0))
        self.assertEqual([n["mShape"] for n in g["nodes"]], [1, 0])        # sphere -> 1, capsule -> 0
        self.assertEqual(g["nodes"][0]["mRadius"], bits(20.0))
        self.assertEqual(g["nodes"][0]["mRegionId"], 3)
        self.assertEqual(g["nodes"][1]["mJoint1"], 13)
        self.assertEqual(g["nodes"][0]["mOffset0"], [bits(1.0), bits(2.0), bits(3.0)])
        self.assertEqual(back.seqs[0], {"mNo": 0, "mModelID": 0, "mGroupNo": 0, "mAttackNo": 0, "mRelation": 5})
        self.assertEqual(rep.counts["second or third (node, attack) pairs without a Dark Arisen slot"], 1)
        self.assertEqual(rep.counts["hit flags DDO has and Dark Arisen does not (dropped)"], 1)

    def test_a_row_naming_a_missing_node_is_cleared(self):
        """Fuzz finding (compat, 2026-09-26): a row naming node 5 of a one-node file kept group 5, which Dark
        Arisen would read past its group array."""
        o = ddo_collision()
        o.index[1]["mNode"] = 5
        out, rep = compat.convert_collision(o, 22)
        self.assertEqual(out.seqs[1]["mGroupNo"], 0xFFFFFFFF)
        self.assertEqual(out.seqs[0]["mGroupNo"], 0)
        self.assertEqual(rep.counts["seq rows naming a node the file does not have (cleared)"], 1)

    def test_oriented_boxes_are_refused(self):
        o = ddo_collision()
        o.nodes[0]["geoms"][0]["mShape"] = 2
        with self.assertRaises(RiftError):
            compat.convert_collision(o, 22)

    def test_attacks(self):
        out, rep = compat.convert_attacks([ddo_attack(), ddo_attack(mUnk04=1, mUnk0D=1)], 22)
        back = ocl.parse(ocl.build(out))
        a0, a1 = back.attacks
        self.assertEqual((a0["mNo"], a1["mNo"]), (0, 1))
        self.assertEqual((a0["mElement"], a1["mElement"]), (2, 0))       # DDO 3 (ice) -> Dark Arisen 2
        self.assertEqual(a0["mAttackRate"], bits(1.5))
        self.assertEqual(a0["mMgcAttackRate"], bits(0.5))
        self.assertEqual(ocl.f32(a0["mFaint"]), 75.0)                    # stun 0x1004
        self.assertEqual(a0["mBlowType"], 3)                              # constructor default kept
        self.assertEqual(rep.counts["debilitation 0x100B without a Dark Arisen ailment"], 2)
        self.assertEqual(rep.counts["attacks"], 2)

    def test_attack_field_spellings_agree(self):
        """ocl_ddo names members mUnk0D; the flat .atk reader mUnk00D -- the same record either way."""
        a = ddo_attack()
        spelled = {f"mUnk{int(k[4:], 16):03X}" if k.startswith("mUnk") else k: v for k, v in a.items()}
        x, _ = compat.convert_attacks([a], 20)
        y, _ = compat.convert_attacks([spelled], 20)
        self.assertEqual(ocl.build(x), ocl.build(y))

    def test_ids_out_of_range(self):
        with self.assertRaises(ParamError):
            compat.convert_collision(ddo_collision(), 66)
        with self.assertRaises(ParamError):
            compat.convert_attacks([ddo_attack()], -1)


@unittest.skipIf(os.environ.get("RIFTSTONE_SKIP_GAME"), "RIFTSTONE_SKIP_GAME is set")
class CompatGameTest(unittest.TestCase):
    def test_the_alchemist_converts(self):
        if not helpers.ddo_key_present():
            self.skipTest("needs the DDO archive key (set RIFTSTONE_DDO_KEY)")
        try:
            from riftstone import arc, lmt, port, typemap
            from riftstone.game import find_game
            from riftstone.index import Index
            ddo, ddda = find_game("ddo"), find_game("ddda")
        except Exception as e:  # noqa: BLE001
            self.skipTest(f"needs both games: {e}")
        io, ia = Index(ddo), Index(ddda)
        self.addCleanup(io.close)
        self.addCleanup(ia.close)

        def read(name, ext, idx=io, game=ddo):
            t = typemap.BY_EXT[ext]
            arcs = idx.archives_with(name.encode("latin-1"), t)
            return arc.Archive.read(game.arc_path(arcs[0])).find(name.encode("latin-1"), t).data()

        src = ocl_ddo.parse(read("obj\\pl\\pl000000\\collision\\job09\\cs03\\cs03", "ocl"))
        out, _ = compat.convert_collision(src, compat.resource_id("cs03"))
        self.assertEqual(ocl.parse(ocl.build(out)).resource_id, 22)
        name = "obj\\pl\\pl000000\\motion\\m0009\\m0009_at\\m0009_cs03"
        done = port.convert_lmt(read(name, "lmt"), "ddo", "ddda")
        ml = lmt.parse(done.data)
        rep = compat.translate_events(ml, compat.motion_slots(name))
        data = lmt.build(ml)
        self.assertEqual(lmt.parse(data).version, 66)
        self.assertGreater(rep.counts.get("collision windows renumbered", 0), 0)
        for mo in ml.motions:
            for page, grp in enumerate(mo.events if mo else []):
                remap = struct.unpack("<32H", grp.remap)
                used = 0
                for b, _f in grp.events:
                    used |= b
                if page == 0:
                    for b in range(16):
                        if used >> b & 1:
                            self.assertIn(remap[b] // 1000, (0, 19, 22))   # rid 0 whole body, or ours
                if page == 1:                                              # only the moved timing bits
                    self.assertEqual(used & ~0x1FE00000, 0)
                if page == 2:                                              # trails and DDO's job-logic bits
                    self.assertEqual(used & ~(0x3 | 0xC00), 0)
        # the Alchemist's two sequence counters (DDO page 1 bits 16/17) arrive as bits 21/22
        moved = 0
        for mo in ml.motions:
            if mo and len(mo.events) > 1:
                for b, _f in mo.events[1].events:
                    moved |= b
        self.assertTrue(moved & (1 << 21) or moved & (1 << 22))


if __name__ == "__main__":
    unittest.main()

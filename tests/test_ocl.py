import struct
import unittest

import helpers  # noqa: F401 (sys.path)
from riftstone import ocl
from riftstone.errors import FormatError, ParamError


def F(v: float) -> int:
    return struct.unpack("<I", struct.pack("<f", v))[0]


def node(no=0, shape=1, radius=35.0, j0=-1, off0=(1.0, 2.0, 3.0), j1=-1, off1=(0.0, 0.0, 0.0)) -> list:
    return [no, shape, F(radius), j0 & 0xFFFFFFFF, *map(F, off0), j1 & 0xFFFFFFFF, *map(F, off1), 0, 1, 2, 3,
            0xFFFFFFFF]


def attack(no=0, value=12.5, flags1=0, flags2=0) -> list:
    words = [no]
    for name, t in ocl.ATTACK:
        words.append(F(value) if name == "mAttack" else F(1.0) if t == "f32" else 0xFFFFFFFF if t == "s32" else 3)
    return words + [flags1, flags2]


def make_simple(n=2, attacks=1, empty=1, seqs=1, flags=(0b101, 1 << 24)) -> bytes:
    """n groups of one node each (shapes 0, 1, 0, ...), then attacks, empty slots and sequence entries."""
    w = [ocl.MAGIC, 7, n]
    for i in range(n):
        w += [i, 1, 1, 0, 0, 1, 0, 0, 0, 0, 0, 1] + node(no=i, shape=i % 2, radius=10.0 + i, off0=(i, -i, 2 * i))
    w.append(attacks + empty)
    for i in range(attacks):
        w += attack(no=i, flags1=flags[0], flags2=flags[1])
    w += [ocl.EMPTY] * empty
    w.append(seqs)
    for i in range(seqs):
        w += [i, 0, i, 0, 0]
    return struct.pack(f"<{len(w)}I", *w)


NODE0 = 12 + 48          # the first node: after the file's three words and its group's twelve


class OclTest(unittest.TestCase):
    def test_round_trip(self):
        raw = make_simple(3)
        o = ocl.parse(raw)
        self.assertEqual((len(o.groups), len(o.nodes), len(o.attacks), len(o.seqs)), (3, 3, 2, 1))
        self.assertIsNone(o.attacks[1])
        self.assertEqual(o.resource_id, 7)
        self.assertEqual(ocl.build(o), raw)
        self.assertEqual(ocl.build(ocl.parse(make_simple(0, 0, 0, 0))), make_simple(0, 0, 0, 0))

    def test_yaml_round_trip_names_everything(self):
        raw = make_simple(2)
        text = ocl.to_yaml(ocl.parse(raw), "collision\\x\\y")
        self.assertEqual(ocl.yaml_to_bytes(text), raw)
        self.assertIn("riftstone: ocl/2", text)
        self.assertIn("flags: [mMultiHit, mIsAllCritical, mIsNoCalcEnemyDefence]", text)
        self.assertIn("mNo: 0x7fffffff", text)
        self.assertIn("mJoint0: -1", text)
        self.assertIn("mShape: 0  # capsule from point 0 to point 1", text)
        self.assertIn("mShape: 1  # sphere at point 0", text)
        self.assertNotIn("flagsUnread", text)

    def test_yaml_edit_changes_only_that_field(self):
        raw = make_simple(2)
        text = ocl.to_yaml(ocl.parse(raw)).replace("mRadius: 10.0", "mRadius: 55.5", 1)
        out = ocl.yaml_to_bytes(text)
        diff = [i for i in range(len(raw)) if raw[i] != out[i]]
        self.assertTrue(diff)
        self.assertTrue(all(NODE0 + 8 <= i < NODE0 + 12 for i in diff), diff)
        self.assertEqual(ocl.from_yaml(text).nodes[0]["mRadius"], F(55.5))

    def test_signed_fields(self):
        text = ocl.to_yaml(ocl.parse(make_simple(1))).replace("mJoint0: -1", "mJoint0: -5", 1)
        self.assertEqual(ocl.from_yaml(text).nodes[0]["mJoint0"], 0xFFFFFFFB)
        with self.assertRaises(ParamError):
            ocl.from_yaml(text.replace("mJoint0: -5", "mJoint0: 3000000000", 1))

    def test_flags_by_name(self):
        text = ocl.to_yaml(ocl.parse(make_simple(1)))
        a = ocl.from_yaml(text.replace("flags: [mMultiHit, mIsAllCritical, mIsNoCalcEnemyDefence]",
                                       "flags: [mIsArrow, mIsVSEnemy13]", 1)).attacks[0]
        self.assertEqual((a["flags1"], a["flags2"]), (1 << 27, 1 << 16))
        with self.assertRaises(ParamError):
            ocl.from_yaml(text.replace("mIsAllCritical", "mIsNotAFlag", 1))

    def test_unread_flag_bits_are_kept(self):
        raw = make_simple(1, flags=(0, 0x80000000 | 1 << 24))
        text = ocl.to_yaml(ocl.parse(raw))
        self.assertIn("flagsUnread: 0x80000000", text)
        self.assertEqual(ocl.yaml_to_bytes(text), raw)
        with self.assertRaises(ParamError):
            ocl.from_yaml(text.replace("flagsUnread: 0x80000000", "flagsUnread: 0x80000001", 1))

    def test_empty_attack_slot(self):
        text = ocl.to_yaml(ocl.parse(make_simple(1)))
        with self.assertRaises(ParamError):     # an empty slot holds nothing else
            ocl.from_yaml(text.replace("- mNo: 0x7fffffff", "- mNo: 0x7fffffff\n    mAttack: 1.0", 1))
        o = ocl.parse(make_simple(1))
        o.attacks[0]["mNo"] = ocl.EMPTY
        with self.assertRaises(ParamError):
            ocl.build(o)

    def test_parse_refusals(self):
        raw = make_simple(2)
        for bad in (b"XX", b"\x00" * 20, raw[:-2], raw[:-4], raw + b"\x00" * 4,
                    struct.pack("<3I", ocl.MAGIC, 0, 0xFFFFFFFF)):
            with self.assertRaises(FormatError, msg=bad[:16]):
                ocl.parse(bad)

    def test_yaml_refusals(self):
        text = ocl.to_yaml(ocl.parse(make_simple(1)))
        for old, new in (("riftstone: ocl/2", "riftstone: xfs/1"), ("mVersion: 0x20121225", "mVersion: 0x20121226"),
                         ("mResourceID: 7", "mResourceID: 7\nextra: 1"), ("mRadius: 10.0", "mRadius: 1e40"),
                         ("mRadius: 10.0", "mRadius: [1.0]"), ("mNodeFreeWork: -1", "mNodeFreeWork: -1\n        mColour: 3"),
                         ("seqIndex:", "seqIndexes:"), ("mIsCheck: 0", "mIsCheck: -1")):
            self.assertIn(old, text)
            with self.assertRaises(ParamError, msg=new):
                ocl.from_yaml(text.replace(old, new, 1))
        offset0 = text[text.index("mOffset0:"):].split("\n")[0]
        with self.assertRaises(ParamError):
            ocl.from_yaml(text.replace(offset0, "mOffset0: [0.0, 0.0]", 1))

    def test_shapes_by_the_games_numbers(self):
        o = ocl.parse(make_simple(2))
        self.assertEqual([ocl.SHAPES[n["mShape"]] for n in o.nodes], ["capsule", "sphere"])
        text = ocl.summary(o)
        self.assertIn("capsule", text)
        self.assertIn("sphere", text)
        self.assertIn("2 groups, 2 shapes (1 capsule, 1 sphere), 1 attack and 1 empty attack slot", text)

    def test_ocl1_still_loads(self):
        # the earlier model's YAML: a 20-byte header, the rest as hex, primitives seen 8 bytes into a group
        raw = make_simple(2)
        old = "\n".join([
            "riftstone: ocl/1", "form: primitive", f"magic_header: {raw[:20].hex()}", f"body: {raw[20:].hex()}",
            "primitives:", "- offset: 0", "  shape: sphere", "  radius: 99.0", "  position: [4.0, 5.0, 6.0]",
            "  extent: 7.0", ""])
        want = bytearray(raw)
        struct.pack_into("<I", want, NODE0 + 4, 0)                        # "sphere" was its name for 0
        struct.pack_into("<I", want, NODE0 + 8, F(99.0))                  # mRadius
        struct.pack_into("<3I", want, NODE0 + 16, F(4.0), F(5.0), F(6.0))  # mOffset0
        struct.pack_into("<I", want, NODE0 + 36, F(7.0))                  # mOffset1's y
        self.assertEqual(ocl.yaml_to_bytes(old), bytes(want))
        self.assertEqual(ocl.from_yaml(old).nodes[0]["mRadius"], F(99.0))
        # an edit that would leave a file the game reads out of step is refused
        grp1_count = 12 + 112 + 44 - 20                                     # group 1's node count, in the body
        bad = old.replace("- offset: 0", f"- offset: {grp1_count - 0x2C}").replace("shape: sphere", "shape: 5")
        with self.assertRaises(ParamError):
            ocl.yaml_to_bytes(bad)
        with self.assertRaises(ParamError):
            ocl.yaml_to_bytes(old.replace("- offset: 0", "- offset: 99999"))


if __name__ == "__main__":
    unittest.main()

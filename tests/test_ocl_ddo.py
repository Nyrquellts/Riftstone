import os
import random
import struct
import time
import unittest

import helpers  # noqa: F401 (sys.path)
from riftstone import ocl_ddo
from riftstone.errors import FormatError, ParamError, RiftError

V = 0x72


def bits(x: float) -> int:
    return struct.unpack("<I", struct.pack("<f", x))[0]


def geom(**kw) -> dict:
    g = {n: 0 for n, _, _ in ocl_ddo.GEOM_FIELDS}
    g.update(mShape=1, mPriority=8, mJnt0=4, mJnt1=13, mRangeCheckBaseJnt=-1, mRadius=bits(35.0),
             mAngle0=(bits(-90.0), bits(90.0)), mAngle1=(bits(-90.0), bits(90.0)),
             mOffset0=(bits(1.0), bits(2.0), bits(3.0)), mOffset1=(bits(0.0), bits(-5.5), bits(10.0)),
             mExtent=(bits(20.0),) * 3, mIsUseData=1)
    g.update(kw)
    return g


def node(geoms, **kw) -> dict:
    n = {"mAttr": 0x4C, "mNodeID": 7, "mIndex": 0, "mColNodeFlag": 0x10, "mHitCollisionFlag": 0x2, "geoms": geoms}
    n.update(kw)
    return n


def attack(i: int) -> dict:
    a = {}
    for k, (name, t, _) in enumerate(ocl_ddo.ATTACK_FIELDS):
        a[name] = {"u8": k % 7, "bool": 1, "u16": 1000 + k, "u32": 0x1000 + k, "u64": (1 << 40) + k,
                   "f32": bits(k / 4)}[t]
    a["mUnk04"] = i
    return a


def index_entry(n: int, a: int) -> dict:
    e = {name: -1 for name, _, _ in ocl_ddo.INDEX_FIELDS}
    e.update(mNode=n, mAttack=a, mLinkID=0, mLinkID2=0, mLinkID3=0, mLinkTop=1)
    return e


def ddo_found() -> bool:
    """The DDO client for the corpus test (skipped without it, or with RIFTSTONE_SKIP_GAME set)."""
    from riftstone.game import find_game
    if os.environ.get("RIFTSTONE_SKIP_GAME"):
        return False
    try:
        find_game("ddo")
    except RiftError:
        return False
    return helpers.ddo_key_present()


def sample() -> ocl_ddo.OclDdo:
    shapes = [geom(), geom(mShape=0, mIndex=1, mJnt1=-1, mRadius=bits(-0.0)),
              geom(mShape=2, mIndex=2, mRadius=0x7FC00123, mExtent=(bits(400.0), bits(800.0), 0x00000001))]
    return ocl_ddo.OclDdo(index=[index_entry(0, -1), index_entry(1, 0)],
                          nodes=[node([], mIndex=0, mAttr=0), node(shapes, mIndex=1)],
                          attacks=[attack(0), attack(1)],
                          node_uid=0x5915DE48, attack_uid=0x5915DE48)


class OclDdoTest(unittest.TestCase):
    def test_layout_by_hand(self):
        # one index entry, a node with one capsule, an empty node, one attack param -- packed by hand
        idx = struct.pack("<9hB", 3, -1, 0, -1, -1, 0, -1, -1, 0, 1)
        shape = (struct.pack("<6BHhhHh", 1, 16, 0, 9, 42, 0, 0, 4, 13, 5, -1) + struct.pack("<f", 35.0)
                 + struct.pack("<4f", -90, 90, -90, 90) + struct.pack("<9f", 1, 2, 3, 4, 5, 6, 20, 20, 20) + b"\x01")
        self.assertEqual(len(idx), 19)
        self.assertEqual(len(shape), 73)
        atk = bytearray(275)
        struct.pack_into("<H", atk, 0, 0)            # mUnk04: first u16
        atk[13] = 1                                   # mUnk119: the 6th field (u16 u16 f32 u8 f32 bool)
        struct.pack_into("<H", atk, 14, 777)          # mUnk0E
        struct.pack_into("<H", atk, 266, 450)         # ... mUnk108 u16, mUnk10C u32, mUnk118 u8, mUnk10A u16
        struct.pack_into("<I", atk, 268, 0x40000)
        atk[272] = 8
        struct.pack_into("<H", atk, 273, 300)
        raw = (b"COL\0" + struct.pack("<HH", V, 0) + struct.pack("<II", V, 1) + idx
               + struct.pack("<III", V, 0xAABBCCDD, 2)
               + struct.pack("<II", V, 1) + shape + struct.pack("<IHHII", 0x80, 8, 0, 0x6, 0x15)
               + struct.pack("<II", V, 0) + struct.pack("<IHHII", 0, 0, 1, 0, 0)
               + struct.pack("<III", V, 0x11223344, 1) + bytes(atk))
        o = ocl_ddo.parse(raw)
        self.assertEqual(o.index[0]["mNode"], 3)
        self.assertEqual(o.index[0]["mLinkTop"], 1)
        self.assertEqual((o.node_uid, o.attack_uid), (0xAABBCCDD, 0x11223344))
        n0 = o.nodes[0]
        self.assertEqual((n0["mAttr"], n0["mNodeID"], n0["mIndex"], n0["mColNodeFlag"], n0["mHitCollisionFlag"]),
                         (0x80, 8, 0, 0x6, 0x15))
        g = n0["geoms"][0]
        self.assertEqual((g["mShape"], g["mOption"], g["mPriority"], g["mLayer"]), (1, 16, 9, 42))
        self.assertEqual((g["mJnt0"], g["mJnt1"], g["mRegionNo"], g["mRangeCheckBaseJnt"]), (4, 13, 5, -1))
        self.assertEqual(ocl_ddo.f32(g["mRadius"]), 35.0)
        self.assertEqual([ocl_ddo.f32(b) for b in g["mOffset1"]], [4.0, 5.0, 6.0])
        self.assertEqual(g["mIsUseData"], 1)
        self.assertEqual(o.nodes[1]["geoms"], [])
        a = o.attacks[0]
        self.assertEqual((a["mUnk119"], a["mUnk0E"], a["mUnk108"], a["mUnk10C"], a["mUnk118"], a["mUnk10A"]),
                         (1, 777, 450, 0x40000, 8, 300))
        self.assertEqual(ocl_ddo.build(o), raw)

    def test_sizes(self):
        self.assertEqual((ocl_ddo.INDEX.size, ocl_ddo.GEOM.size, ocl_ddo.NODE.size, ocl_ddo.ATTACK.size),
                         (19, 73, 16, 275))
        self.assertEqual(len(ocl_ddo.ATTACK_FIELDS), 94)
        self.assertEqual(len({n for n, _, _ in ocl_ddo.ATTACK_FIELDS}), 94)
        offs = sorted(o for _, _, o in ocl_ddo.ATTACK_FIELDS)
        self.assertLess(offs[-1], 0x120)               # inside the loaded 0x120-byte record

    def test_round_trip(self):
        m = sample()
        raw = ocl_ddo.build(m)
        self.assertEqual(raw[:8], b"COL\0" + struct.pack("<HH", V, 0))
        expect = 8 + (8 + 2 * 19) + (12 + (8 + 16) + (8 + 3 * 73 + 16)) + (12 + 2 * 275)
        self.assertEqual(len(raw), expect)
        o = ocl_ddo.parse(raw)
        self.assertEqual(o, m)
        self.assertEqual(ocl_ddo.build(o), raw)
        self.assertEqual(o.shape_count, 3)
        empty = ocl_ddo.build(ocl_ddo.OclDdo())
        self.assertEqual(len(empty), 40)               # the smallest vanilla file is this size
        self.assertEqual(ocl_ddo.build(ocl_ddo.parse(empty)), empty)
        padded = ocl_ddo.build(ocl_ddo.OclDdo(pad=7))
        self.assertEqual(ocl_ddo.parse(padded).pad, 7)

    def test_refusals(self):
        raw = ocl_ddo.build(sample())
        bad = [b"", b"COL", b"COL\0\x72\0", b"XOL\0" + raw[4:], raw[:4] + struct.pack("<H", 0x71) + raw[6:],
               raw + b"\0"]
        # every table version, found by walking the file
        at = [8]                                                        # rCollIndex
        p = 8 + 8 + 2 * 19
        at.append(p)                                                    # rCollNode
        at.append(p + 12)                                               # the first node's rCollGeom
        at.append(len(raw) - 12 - 2 * 275)                              # rAttackParam
        for off in at:
            self.assertEqual(struct.unpack_from("<I", raw, off)[0], V)
            bad.append(raw[:off] + struct.pack("<I", 0x71) + raw[off + 4:])
        for b in bad:
            with self.assertRaises(FormatError, msg=b[:16]):
                ocl_ddo.parse(b)

    def test_every_truncation_refused(self):
        raw = ocl_ddo.build(sample())
        for n in range(len(raw)):
            with self.assertRaises(FormatError, msg=n):
                ocl_ddo.parse(raw[:n])

    def test_absurd_counts_refused_fast(self):
        raw = ocl_ddo.build(sample())
        p = 8 + 8 + 2 * 19
        counts = [12, p + 8, p + 16, len(raw) - 2 * 275 - 4]         # index, node, geom (first node), attack
        for off in counts:
            for n in (0xFFFFFFFF, 0x7FFFFFFF, 1_000_000):
                t0 = time.perf_counter()
                with self.assertRaises(FormatError):
                    ocl_ddo.parse(raw[:off] + struct.pack("<I", n) + raw[off + 4:])
                self.assertLess(time.perf_counter() - t0, 0.5)

    def test_hostile_mutations(self):
        raw = ocl_ddo.build(sample())
        rng = random.Random(0x0C1)
        for _ in range(3000):
            b = bytearray(raw)
            for _ in range(rng.randint(1, 4)):
                i = rng.randrange(len(b))
                b[i] = rng.choice((0, 0xFF, rng.randrange(256), b[i] ^ (1 << rng.randrange(8))))
            if rng.random() < 0.2:
                del b[rng.randrange(len(b)):]
            try:
                o = ocl_ddo.parse(bytes(b))
            except FormatError:
                continue
            self.assertEqual(ocl_ddo.build(o), bytes(b))   # whatever parses rebuilds exactly

    def test_build_refuses_bad_values(self):
        cases = [lambda m: m.nodes[1]["geoms"][0].update(mShape=256),
                 lambda m: m.nodes[1]["geoms"][0].update(mJnt0=40000),
                 lambda m: m.nodes[1]["geoms"][0].update(mRadius=-1),
                 lambda m: m.nodes[1]["geoms"][0].update(mOffset0=(0, 0)),
                 lambda m: m.nodes[1]["geoms"][0].update(mExtent=(0, 0, 1 << 32)),
                 lambda m: m.nodes[1]["geoms"][0].pop("mIsUseData"),
                 lambda m: m.nodes[1].update(geoms=None),
                 lambda m: m.nodes[0].update(mAttr=1 << 32),
                 lambda m: m.index[0].update(mNode=1.5),
                 lambda m: m.attacks[1].update(mUnk110=-1),
                 lambda m: setattr(m, "node_uid", 1 << 32),
                 lambda m: setattr(m, "pad", -1)]
        for i, change in enumerate(cases):
            m = sample()
            change(m)
            with self.assertRaises(FormatError, msg=i):
                ocl_ddo.build(m)

    def test_yaml_round_trip(self):
        m = sample()
        raw = ocl_ddo.build(m)
        text = ocl_ddo.to_yaml(m, "obj\\em\\em010100\\collision\\em010100_atk")
        self.assertIn("riftstone: ocl-ddo/1", text)
        self.assertIn("# capsule", text)
        self.assertIn("jamcrc of the resource path", text)
        self.assertIn("nan:0x7fc00123", text)          # a NaN keeps its payload
        self.assertIn("mRadius: -0.0", text)
        self.assertEqual(ocl_ddo.yaml_to_bytes(text, "t.yaml"), raw)
        self.assertEqual(ocl_ddo.from_yaml(text), m)
        for o in (ocl_ddo.OclDdo(), ocl_ddo.OclDdo(pad=3, node_uid=1, attack_uid=2)):
            self.assertEqual(ocl_ddo.yaml_to_bytes(ocl_ddo.to_yaml(o)), ocl_ddo.build(o))
        other = ocl_ddo.to_yaml(m, "somewhere\\else")
        self.assertIn("vanilla files use the jamcrc of the path: 0x", other)
        odd = "x\nriftstone: xfs/1\r\t\"#"                  # a name cannot break out of its comment line
        self.assertEqual(ocl_ddo.yaml_to_bytes(ocl_ddo.to_yaml(m, odd)), raw)

    def test_yaml_edit_changes_only_that_field(self):
        m = sample()
        raw = ocl_ddo.build(m)
        text = ocl_ddo.to_yaml(m)
        self.assertEqual(text.count("mRadius: 35.0"), 1)
        out = ocl_ddo.yaml_to_bytes(text.replace("mRadius: 35.0", "mRadius: 50.25"))
        diff = [i for i in range(len(raw)) if raw[i] != out[i]]
        first_shape = 8 + 8 + 2 * 19 + 12 + 24 + 8
        self.assertTrue(diff)
        self.assertTrue(all(first_shape + 16 <= i < first_shape + 20 for i in diff))
        self.assertEqual(ocl_ddo.f32(ocl_ddo.parse(out).nodes[1]["geoms"][0]["mRadius"]), 50.25)

    def test_yaml_structure_edits(self):
        m = sample()
        m.nodes[0]["geoms"].append(geom(mShape=0))
        m.attacks.pop()
        m.index.append(index_entry(1, 1))
        text = ocl_ddo.to_yaml(m)
        o = ocl_ddo.parse(ocl_ddo.yaml_to_bytes(text))
        self.assertEqual((len(o.index), len(o.nodes[0]["geoms"]), len(o.attacks)), (3, 1, 1))
        self.assertEqual(o, m)

    def test_yaml_refusals(self):
        good = ocl_ddo.to_yaml(sample())
        u64 = f"mUnk110: {sample().attacks[0]['mUnk110']}"
        bad = [
            "riftstone: xfs/1\n",
            good.replace("riftstone: ocl-ddo/1", "riftstone: ocl/1"),
            good + "extra: 1\n",
            good.replace("mRadius: 35.0", "mRadius: 1e40", 1),                 # too big for a float32
            good.replace("mRadius: 35.0", "mRadius: wide", 1),
            good.replace("mRadius: 35.0", "mRadius: [1, 2]", 1),
            good.replace("mShape: 1", "mShape: 256", 1),
            good.replace("mJnt0: 4", "mJnt0: 4.5", 1),
            good.replace("mJnt0: 4", "mJnt0: -40000", 1),
            good.replace("mJnt0: 4", "mJnt0x: 4", 1),                         # unknown and missing
            good.replace("mOffset0: [1.0, 2.0, 3.0]", "mOffset0: [1.0, 2.0]", 1),
            good.replace("mOffset0: [1.0, 2.0, 3.0]", "mOffset0: 1.0", 1),
            good.replace("mAttr: 0x4c", "mAttr: 0x100000000", 1),
            good.replace(u64, "mUnk110: -1", 1),
            good.replace(u64, "mUnk110: 0x10000000000000000", 1),
            good.replace("mUID: 0x5915de48", "mUID: 0x1ffffffff", 1),
            good.replace("mUID: 0x5915de48", "mUUID: 0x5915de48", 1),
            good.replace("geoms: []", "geoms: {}", 1),
            good.replace("geoms: []", "shapes: []", 1),
            good.replace("rCollIndex:", "rCollIdx:", 1),
            good.replace("  - {mNode: 0,", "  - {mNode: 0, mNode: 1,", 1),        # duplicate key
            good.split("rAttackParam:")[0],                                      # a table missing
        ]
        self.assertIn(u64, good)
        for i, t in enumerate(bad):
            with self.assertRaises(ParamError, msg=i):
                ocl_ddo.yaml_to_bytes(t, "bad.yaml")

    def test_huge_numbers_are_refused_in_a_short_message(self):
        # a hex number thousands of digits long parses; writing it in decimal for the message raised
        # ValueError ("Exceeds the limit (4300 digits)")
        good = ocl_ddo.to_yaml(sample())
        for value in ("0x" + "F" * 3600, "9" * 5000):
            for text in (f"riftstone: ocl-ddo/1\npad: {value}\n", good.replace("mJnt0: 4", f"mJnt0: {value}", 1)):
                with self.assertRaises(ParamError) as cm:
                    ocl_ddo.yaml_to_bytes(text, "t.yaml")
                self.assertLess(len(str(cm.exception)), 200)

    def test_yaml_hostile_text(self):
        good = ocl_ddo.to_yaml(sample())
        rng = random.Random(7)
        for _ in range(400):
            t = list(good)
            for _ in range(rng.randint(1, 3)):
                i = rng.randrange(len(t))
                t[i] = rng.choice("0123456789-.:[]{}#x \n")
            try:
                raw = ocl_ddo.yaml_to_bytes("".join(t), "fuzz.yaml")
            except RiftError:
                continue
            self.assertEqual(ocl_ddo.build(ocl_ddo.parse(raw)), raw)

    def test_path_uid_and_helpers(self):
        self.assertEqual(ocl_ddo.path_uid("obj\\em\\em010100\\collision\\em010100_atk"), 0x5915DE48)
        self.assertEqual(ocl_ddo.path_uid("obj\\em\\em010100\\collision\\em010100_atk.ocl"), 0x5915DE48)
        self.assertTrue(ocl_ddo.is_ddo_ocl(ocl_ddo.build(sample())))
        self.assertFalse(ocl_ddo.is_ddo_ocl(struct.pack("<I", 0x20121225)))
        self.assertEqual(ocl_ddo.f32(ocl_ddo.f32_bits(1.5)), 1.5)
        s = ocl_ddo.summary(sample())
        self.assertIn("2 index entries, 2 nodes with 3 hit shapes (1 sphere, 1 capsule, 1 oriented box)", s)
        self.assertIn("2 attack params", s)
        self.assertEqual(ocl_ddo.SHAPES[2], "oriented box")

    @unittest.skipUnless(ddo_found(), "Dragon's Dogma Online not found")
    def test_corpus_byte_exact(self):
        from riftstone import corpus, typemap
        from riftstone.game import find_game

        g = find_game("ddo")
        n = ok = yaml_ok = yaml_n = shapes = 0
        failures = []
        for r in corpus.resources(g, [typemap.BY_EXT["ocl"]]):
            n += 1
            name = r.name.decode("latin-1")
            try:
                o = ocl_ddo.parse(r.data)
                self.assertEqual(ocl_ddo.build(o), r.data)
                self.assertEqual((o.node_uid, o.attack_uid), (ocl_ddo.path_uid(name),) * 2)
                shapes += o.shape_count
                ok += 1
                if n % 8 == 1:
                    yaml_n += 1
                    self.assertEqual(ocl_ddo.yaml_to_bytes(ocl_ddo.to_yaml(o, name), name), r.data)
                    yaml_ok += 1
            except (AssertionError, RiftError) as e:
                failures.append(f"{r.label}: {e}")
        self.assertGreater(n, 0)
        self.assertEqual(failures, [])
        self.assertEqual((ok, yaml_ok), (n, yaml_n))
        self.assertGreater(shapes, 0)


if __name__ == "__main__":
    unittest.main()

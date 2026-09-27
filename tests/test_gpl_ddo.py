import os
import random
import struct
import unittest

import helpers  # noqa: F401 (sys.path)
from riftstone import gpl_ddo
from riftstone.errors import FormatError, ParamError, RiftError


def f(x):
    return struct.unpack("<I", struct.pack("<f", x))[0]        # float -> its 32-bit bits


def vec(a, b, c, d=0.0):
    return [f(a), f(b), f(c), f(d)]


def shape(kind, name="hit"):
    s = {"Name": name, "CheckAngle": f(45.0), "CheckRange": f(-0.0), "CheckToward": 0x7FC00123,   # a NaN payload
         "AngleFlag": 1, "TowardFlag": 2, "type": kind}
    if kind:
        s.update(mDecay=f(1.0), mIsNativeData=3)
    if kind == 1:
        s.update(mHeight=f(2000.0), mBottom=f(-5.5), mVertex=[vec(i, 0, -i) for i in range(4)],
                 mConcaveCrossPos=vec(1, 2, 3), mFlgConvex=1, mConcaveStatus=7)
    elif kind == 2:
        s["mSphere"] = vec(1, 2, 3, 50.0)
    elif kind == 3:
        s.update(Position0=vec(0, 0, 0), Position1=vec(0, 10, 0), Radius=f(2.5), pad=[0xCDCDCDCD] * 3)
    elif kind == 6:
        s.update(mHeight=f(4.0), mTopRadius=f(0.5), mPos=vec(9, 8, 7), mBottomRadius=0x00000001)   # a denormal
    elif kind == 8:
        s.update(**{"mAABB.minpos": vec(-1, -1, -1), "mAABB.maxpos": vec(1, 1, 1), "mDecayY": f(0.25),
                    "mDecayZ": 0x7F800000, "mIsEnableExtendedDecay": 1})                        # +inf
    elif kind == 9:
        s.update(**{"mOBB.coord": [vec(1, 0, 0), vec(0, 1, 0), vec(0, 0, 1), vec(5, 6, 7, 1)],
                    "mOBB.extent": vec(3, 4, 5), "mDecayY": f(1.0), "mDecayZ": f(2.0), "mIsEnableExtendedDecay": 0})
    return s


def group(n, hit=(), life=(), kill=()):
    g = {"mGroup": n, "DisableSplit": 1, "SetMarkerPos": 0, "ForceOmGroupAllHardware": 1, "mUnk1C": 0,
         "mLayoutIDArray": [{"Area": 100, "Group": n, "SplitX": 3, "SplitZ": 4}]}
    for i, (k, _t) in enumerate(gpl_ddo._GROUP_A + gpl_ddo._GROUP_B):
        g[k] = i
    g.update(mUnk26=0xCDCD, MaxCount=10, KillAreaType=-1, mAreaHitShapeList=[shape(k) for k in hit],
             mLifeAreaArray=[{"mShapeList": [shape(k) for k in ks]} for ks in life],
             mKillAreaList=[shape(k) for k in kill])
    return g


def sample() -> gpl_ddo.GplDdo:
    groups = [group(0, hit=[1], life=[[1, 3], []], kill=[2]),
              group(5, kill=[6, 8, 9, 0]),
              group(300)]
    groups[0]["mAreaHitShapeList"][0]["Name"] = "上空用１"              # Shift-JIS text
    groups[1]["mKillAreaList"][0]["Name"] = b"\x81"                   # not Shift-JIS: kept as bytes
    glist = list(range(gpl_ddo.SLOTS))
    for g in groups:
        glist[g["mGroup"]] |= gpl_ddo.IN_FILE
    glist[506] = 0x7EF                                                # vanilla has junk like this
    return gpl_ddo.GplDdo(glist, groups)


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


class GplDdoTest(unittest.TestCase):
    def test_round_trip_and_header(self):
        m = sample()
        raw = gpl_ddo.build(m)
        self.assertEqual(raw[:4], b"gpl\0")
        self.assertEqual(struct.unpack_from("<II", raw, 4), (70, 512))
        self.assertEqual(list(struct.unpack_from("<512I", raw, 12)), m.mGroupList)
        self.assertEqual(struct.unpack_from("<I", raw, 12 + 2048)[0], 3)                     # group count
        # pool header: cID, cLifeArea, AreaHitShape, then Area, Sphere, Cylinder, Cone, AABB, OBB
        self.assertEqual(list(struct.unpack_from("<9I", raw, 16 + 2048)), [3, 2, 8, 2, 1, 1, 1, 1, 1])
        self.assertEqual(struct.unpack_from("<I", raw, 16 + 2048 + 36)[0], 0 | 1 << 9 | 1 << 13)   # group 0's word
        back = gpl_ddo.parse(raw)
        self.assertEqual(back, m)
        self.assertEqual(gpl_ddo.build(back), raw)
        self.assertTrue(gpl_ddo.is_ddo_gpl(raw))
        self.assertFalse(gpl_ddo.is_ddo_gpl(raw[:4] + struct.pack("<I", 158) + raw[8:]))

    def test_empty_list(self):
        raw = gpl_ddo.build(gpl_ddo.GplDdo())
        self.assertEqual(len(raw), 2064)                    # the size of the 5 vanilla files without groups
        self.assertEqual(gpl_ddo.build(gpl_ddo.parse(raw)), raw)
        self.assertEqual(gpl_ddo.yaml_to_bytes(gpl_ddo.to_yaml(gpl_ddo.parse(raw))), raw)

    def test_every_zone_type(self):
        for kind in (0, 1, 2, 3, 6, 8, 9):
            glist = [0] * 512
            glist[7] = gpl_ddo.IN_FILE | 7
            m = gpl_ddo.GplDdo(glist, [group(7, hit=[kind], life=[[kind]], kill=[kind])])
            raw = gpl_ddo.build(m)
            self.assertEqual(gpl_ddo.parse(raw), m, kind)
            self.assertEqual(gpl_ddo.yaml_to_bytes(gpl_ddo.to_yaml(m, "t")), raw, kind)
            pools = struct.unpack_from("<9I", raw, 16 + 2048)
            self.assertEqual(pools[:3], (1, 1, 3))
            self.assertEqual(sum(pools[3:]), 3 if kind else 0)

    def test_yaml_round_trip(self):
        raw = gpl_ddo.build(sample())
        text = gpl_ddo.to_yaml(gpl_ddo.parse(raw), "scr\\st0100\\etc\\st0100_e")
        for bit in ("riftstone: gpl-ddo/1", 'resource: "scr\\\\st0100\\\\etc\\\\st0100_e"',
                    "type: 3  # nZone::ShapeInfoCylinder", "CheckToward: nan:0x7fc00123", "CheckRange: -0.0",
                    'Name: {hex: "81"}', 'Name: "上空用１"', "mUnk26: 0xcdcd", "MaxCount: 10",
                    "0x8000012c", "{Area: 100, Group: 5, SplitX: 3, SplitZ: 4}"):
            self.assertIn(bit, text)
        self.assertEqual(gpl_ddo.yaml_to_bytes(text, "g.yaml"), raw)

    def test_yaml_edits(self):
        text = gpl_ddo.to_yaml(sample())
        g = gpl_ddo.parse(gpl_ddo.yaml_to_bytes(text.replace("MaxCount: 10", "MaxCount: 30", 1)))
        self.assertEqual([x["MaxCount"] for x in g.groups], [30, 10, 10])      # the spawn cap
        cell = "      - {Area: 100, Group: 5, SplitX: 3, SplitZ: 4}\n"
        more = cell + "      - {Area: 101, Group: 5, SplitX: 0, SplitZ: 0}\n"
        g = gpl_ddo.parse(gpl_ddo.yaml_to_bytes(text.replace(cell, more)))
        self.assertEqual([c["Area"] for c in g.groups[1]["mLayoutIDArray"]], [100, 101])
        # a second hit shape in group 300: the pool header grows with it
        hit = gpl_ddo.to_yaml(gpl_ddo.GplDdo([0] * 512, [group(1, hit=[2])])).split("mAreaHitShapeList:\n", 1)[1]
        hit = hit.split("    StrongestInSimpleEvent", 1)[0]
        tail = text.rsplit("mAreaHitShapeList: []", 1)
        raw = gpl_ddo.yaml_to_bytes(tail[0] + "mAreaHitShapeList:\n" + hit.rstrip("\n") + tail[1])
        g = gpl_ddo.parse(raw)
        self.assertEqual(g.groups[2]["mAreaHitShapeList"][0]["type"], 2)
        self.assertEqual(struct.unpack_from("<9I", raw, 16 + 2048)[2:5], (9, 2, 2))

    def test_binary_refusals(self):
        raw = gpl_ddo.build(sample())
        bad = [b"xxx\0" + raw[4:],                                               # magic
               raw[:4] + struct.pack("<I", 158) + raw[8:],                       # a Dark Arisen version
               raw[:8] + struct.pack("<I", 511) + raw[12:],                      # not DDO.exe's 512 slots
               raw[:16] + struct.pack("<I", 0x80000001) + raw[20:],              # marks 4 groups, says 3
               raw + b"\0",                                                      # trailing byte
               raw[:2064] + struct.pack("<I", 4) + raw[2068:]]                   # pool header off by one
        empty = gpl_ddo.build(gpl_ddo.GplDdo())
        bad.append(empty[:2060] + struct.pack("<I", 1))                          # a count, no marks
        for b in bad:
            with self.assertRaises(FormatError, msg=b[:16]):
                gpl_ddo.parse(b)
        with self.assertRaisesRegex(FormatError, "Dark Arisen"):
            gpl_ddo.parse(bad[1])
        for n in range(len(raw)):                                                # every truncation
            with self.assertRaises(FormatError, msg=n):
                gpl_ddo.parse(raw[:n])

    def test_hostile_counts_and_types(self):
        glist = [0] * 512
        glist[0] = gpl_ddo.IN_FILE
        one = gpl_ddo.build(gpl_ddo.GplDdo(glist, [group(0, hit=[2])]))
        body = 2064 + 36                                                         # the group's first word
        for at in (body + 4, body + 4 + 4 + 16 + 45):                            # mLayoutIDArray, then the hit list
            b = one[:at] + struct.pack("<I", 0xFFFFFFFF) + one[at + 4:]
            with self.assertRaisesRegex(FormatError, "longer than the rest"):
                gpl_ddo.parse(b)
        name = one.index(b"hit\0")
        type_at = name + 4 + 12 + 2
        self.assertEqual(struct.unpack_from("<i", one, type_at)[0], 2)
        for kind in (4, 5, 7, 10, -1, 0x7FFFFFFF):                               # no zone class in DDO.exe
            with self.assertRaisesRegex(FormatError, "not one DDO.exe loads"):
                gpl_ddo.parse(one[:type_at] + struct.pack("<i", kind) + one[type_at + 4:])
        with self.assertRaisesRegex(FormatError, "not terminated"):
            gpl_ddo.parse(one[:name] + b"h" * (len(one) - name))

    def test_mutations_refuse_or_round_trip(self):
        """What the fuzz target checks: a mutated file is refused (FormatError) or rebuilds exactly."""
        rng = random.Random(70)
        seed = gpl_ddo.build(sample())
        accepted = 0
        for _ in range(1500):
            b = bytearray(seed)
            for _ in range(rng.randint(1, 4)):
                op, at = rng.randrange(4), rng.randrange(len(b))
                if op == 0:
                    b[at] = rng.randrange(256)
                elif op == 1:
                    v = rng.choice((0, 1, 0x7FFFFFFF, 0xFFFFFFFF, rng.getrandbits(32)))
                    struct.pack_into("<I", b, min(at, len(b) - 4), v)
                elif op == 2:
                    del b[at:at + rng.randint(1, 8)]
                else:
                    b[at:at] = bytes(rng.randrange(256) for _ in range(rng.randint(1, 8)))
            try:
                m = gpl_ddo.parse(bytes(b))
            except FormatError:
                continue
            accepted += 1
            self.assertEqual(gpl_ddo.build(m), bytes(b))
            self.assertEqual(gpl_ddo.yaml_to_bytes(gpl_ddo.to_yaml(m, "m")), bytes(b))
        self.assertGreater(accepted, 0)

    def test_build_and_yaml_refusals(self):
        def with_(fn):
            m = sample()
            fn(m)
            return m

        for m in (with_(lambda m: m.groups[0].update(mGroup=512)),
                  with_(lambda m: m.groups[0].update(DisableSplit=2)),
                  with_(lambda m: m.groups[0].update(mUnk1C=0x200)),                # a named bit
                  with_(lambda m: m.groups[0].update(FlagNo=0x10000)),
                  with_(lambda m: m.groups[0].update(SetManageFlagType=256)),
                  with_(lambda m: m.groups[0].update(MaxCount=2 ** 31)),
                  with_(lambda m: m.mGroupList.pop()),
                  with_(lambda m: m.groups.pop()),                                  # marks no longer match
                  with_(lambda m: m.groups[0]["mAreaHitShapeList"][0].update(Name="a\0b")),
                  with_(lambda m: m.groups[0]["mAreaHitShapeList"][0].update(Name="\U0001F600")),
                  with_(lambda m: m.groups[0]["mAreaHitShapeList"][0].update(mConcaveCrossPos=[0, 0, 0])),
                  with_(lambda m: m.groups[0]["mAreaHitShapeList"][0].update(type=4)),
                  with_(lambda m: m.groups[0].pop("QuestNo"))):
            with self.assertRaises(ParamError):
                gpl_ddo.build(m)
        text = gpl_ddo.to_yaml(sample())
        for bad in (text.replace("riftstone: gpl-ddo/1", "riftstone: gpl/1"),
                    text.replace("mGroup: 5\n", "mGroup: 512\n", 1),
                    text.replace("mUnk1C: 0x0", "mUnk1C: 0x800", 1),
                    text.replace("FlagNo: 1\n", "FlagNo: 65536\n", 1),
                    text.replace("MaxCount: 10", "MaxCount: ten", 1),
                    text.replace("type: 2  #", "type: 5  #", 1),
                    text.replace("mSphere: [1.0, 2.0, 3.0, 50.0]", "mSphere: [1.0, 2.0, 3.0]", 1),
                    text.replace("mSphere: [1.0, 2.0, 3.0, 50.0]", "mSphere: 1.0", 1),
                    text.replace("Radius: 2.5", "Radius: big", 1),
                    text.replace("    QuestNo: 4\n", "", 1),                          # a field left out
                    text.replace("[0x80000000, ", "[", 1),                             # 511 slots
                    text.replace("0x80000005", "0x5", 1),                               # unmarked, still listed
                    text.replace('Name: "hit"', 'Name: "\\u3000\\U0001F600"', 1),
                    text.replace('Name: {hex: "81"}', 'Name: {hex: "zz"}', 1),
                    text.replace("    MaxCount: 10\n", "    MaxCount: 10\n    MaxCont: 30\n", 1),     # a typo
                    text.replace("type: 2  #", "type: 0  #", 1),                        # the sphere's fields stay
                    text.replace("riftstone: gpl-ddo/1\n", "riftstone: gpl-ddo/1\nversion: 70\n", 1)):
            with self.assertRaises(ParamError, msg=bad[:300]):
                gpl_ddo.yaml_to_bytes(bad, "g.yaml")
        with self.assertRaisesRegex(ParamError, "g.yaml: line"):
            gpl_ddo.yaml_to_bytes(text.replace("MaxCount: 10", "MaxCount: 99999999999", 1), "g.yaml")

    def test_huge_numbers_are_refused_in_a_short_message(self):
        # base 16 has no digit limit, but the range message wrote the number in decimal: ValueError ("Exceeds
        # the limit (4300 digits)") instead of a refusal
        big = "0x" + "f" * 3572
        text = gpl_ddo.to_yaml(sample())
        for bad in (text.replace("MaxCount: 10", f"MaxCount: {big}", 1), text.replace("0x80000005", big, 1)):
            with self.assertRaises(ParamError) as e:
                gpl_ddo.yaml_to_bytes(bad, "g.yaml")
            self.assertLess(len(str(e.exception)), 200)
        for fn in (lambda m: m.groups[0].update(MaxCount=int(big, 16)), lambda m: m.groups[0].update(FlagNo=int(big, 16)),
                   lambda m: m.groups[0]["mAreaHitShapeList"][0].update(type=int(big, 16)),
                   lambda m: m.mGroupList.__setitem__(1, int(big, 16))):
            m = sample()
            fn(m)
            with self.assertRaises(ParamError) as e:
                gpl_ddo.build(m)
            self.assertLess(len(str(e.exception)), 200)

    def test_summary_and_names(self):
        s = gpl_ddo.summary(sample())
        self.assertIn("3 group(s), 3 layout cell(s), 8 area shape(s)", s)
        self.assertIn("group 5    max 10", s)
        self.assertIn("quest 4", s)
        named = {k for k, _ in gpl_ddo._GROUP_A + gpl_ddo._GROUP_B} | {k for k, _, _ in gpl_ddo._BITS}
        self.assertEqual(set(gpl_ddo.JAPANESE) - {"KillAreaType"}, named - {"mGroup", "mUnk26"})
        self.assertEqual(gpl_ddo.POOLS[3:], tuple(c for c, _ in gpl_ddo.ZONES.values()))

    @unittest.skipUnless(ddo_found(), "Dragon's Dogma Online not found")
    def test_corpus_byte_exact(self):
        from riftstone import corpus, typemap
        from riftstone.game import find_game

        n = ok = yaml_n = groups = 0
        failures = []
        for r in corpus.resources(find_game("ddo"), [typemap.BY_EXT["gpl"]]):
            n += 1
            try:
                x = gpl_ddo.parse(r.data)
                self.assertEqual(gpl_ddo.build(x), r.data)
                groups += len(x.groups)
                ok += 1
                cylinder = any(s["type"] == 3 for g in x.groups for s in gpl_ddo.group_shapes(g))
                if n % 4 == 1 or cylinder:
                    yaml_n += 1
                    self.assertEqual(gpl_ddo.yaml_to_bytes(gpl_ddo.to_yaml(x, r.name.decode("latin-1")), r.label),
                                     r.data)
            except (AssertionError, RiftError) as e:
                failures.append(f"{r.label}: {e}")
        self.assertEqual(failures[:5], [])
        self.assertEqual(ok, n)
        self.assertGreater(n, 2000)
        self.assertGreater(groups, n)
        self.assertGreater(yaml_n, n // 4)


if __name__ == "__main__":
    unittest.main()

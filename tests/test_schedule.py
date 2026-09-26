import os
import struct
import unittest

import helpers  # noqa: F401 (sys.path)
from riftstone import schedule, xfs
from riftstone.errors import FormatError, ParamError, RiftError
from riftstone.schedule import Grid, Layout, Scheduler, Track, Zone, ZoneGroup


def fb(v: float) -> int:
    return struct.unpack("<I", struct.pack("<f", v))[0]


def vec(*v) -> tuple:
    return tuple(fb(x) for x in v)


RES = 0x6D5AE854          # rEffectList


def sdl_ddda() -> Scheduler:
    t = [Track(1, name="Root"),
         Track(2, parent=25, name="HemiSphereLight", index=0x60C10879),
         Track(6, 6, 1, "mGroup", keys=[(0, 0)], values=[193]),
         Track(6, 10, 1, "mPriorityBias", keys=[(0, 0), (4, 3)], values=[0xFFFFFFFF, 7]),
         Track(7, 20, 1, "mColor", keys=[(0, 3), (30, 3)], values=[vec(0.1, 0.2, 0.3, 0), vec(1, 1, 1, 0)]),
         Track(8, 12, 1, "mScale", keys=[(0, 5), (10, 5), (20, 1)], values=[fb(1.0), fb(0.5), fb(2.0)]),
         Track(9, 3, 1, "Draw", keys=[(0, 0), (5, 2), (9, 0)], values=[1, 0, 1]),
         Track(4),
         Track(3, name="EffectExt", index=0x4FC592F6),
         Track(5, 1, 8, "mAdjustParamList", 1),
         Track(9, 3, 9, "Draw", keys=[(0, 0)], values=[1]),
         Track(11, 128, 8, "mpEffectList", keys=[(0, 0), (1, 2), (2, 2)],
               values=[(RES, "effect\\efl\\ev\\x"), (RES, "effect\\efl\\ev\\x"), None]),
         Track(12, 14, 8, "MessageResourcePath", keys=[(0, 0), (3, 0)], values=["x", None]),
         Track(13, 24, 8, "Finish", keys=[(885, 2)], values=[0]),
         Track(14, 19, 8, "mEffMat", keys=[(0, 3)], values=[tuple(fb(1.0 if i % 5 == 0 else 0.0) for i in range(16))]),
         Track(10, 2, 8, "mpTarget", keys=[(0, 0)], values=[1]),
         Track(6, 6, 8, "mCount")]
    return Scheduler(19, 5000, 0, 0, 0, schedule.UNK08[19], t)


def sdl_ddo() -> Scheduler:
    t = [Track(1, name="Root"),
         Track(2, parent=4, name="pl_000", index=0x47AF33FA),
         Track(8, 20, 1, "mPos", keys=[(0, 3)], values=[vec(1, 2, 3, 0)]),
         Track(9, 12, 1, "mFrame", keys=[(0, 1)], values=[fb(0.0)]),
         Track(11, 3, 1, "Draw", keys=[(0, 0), (2, 0)], values=[1, 1], extra=(1, 2)),
         Track(12, 2, 1, "mpTarget", keys=[(0, 0)], values=[1]),
         Track(13, 128, 1, "mpModel", keys=[(0, 0)], values=[(RES, "obj\\a")]),
         Track(14, 14, 1, "IndexName", keys=[(0, 0)], values=["idx"]),
         Track(15, 28, 1, "AllOn", keys=[(0, 2)], values=[1]),
         Track(16, 57, 1, "mCurve", keys=[(0, 3)], values=[tuple(range(16))])]
    return Scheduler(22, 1200, 1, 0, 0, schedule.UNK08[22], t)


def shape_values(shape: int, ddo: bool) -> dict:
    out = {"mPriority": -3, "mContentsPoolID": 0, "mContentsPoolGroupID": -1, "mIsEnable": 1, "mIndex": 0,
           "mLayoutGroupIndex": 0, "mIndexOfLayoutGroup": 0, "mUniqueID": 0, "mGroupID": 2}
    for f in schedule.shape_fields(shape, ddo):
        if f[0] == "s":
            out[f[1]] = fb(1.5) if f[2] == "f32" else 1
        else:
            out[f[1]] = [fb(float(i)) for i in range(f[3])]
    return out


def lay(shape: int, uid: int, ddo: bool = False, extend: bytes | None = None) -> Layout:
    v = shape_values(shape, ddo)
    v["mUniqueID"] = v["mIndex"] = uid
    return Layout(shape, v, extend)


def xfs_blob(ddo: bool = False) -> bytes:
    x = helpers.sample_xfs()
    if ddo:
        x.version = xfs.VERSION_DDO
    return xfs.build(x)


def grid(n: int) -> Grid:
    return Grid(vec(-1, -1, -1, 0, 1, 1, 1, 0), 2, 1, 0, [(1, 0), (n - 1, 32)], list(range(n)))


def box(i: int) -> tuple:
    return vec(-i, -i, -i, i, i, i)


def zon_type2(ddo: bool = False) -> Zone:
    blob = xfs_blob(ddo)
    lays = [lay(4, 0, ddo), lay(2, 1, ddo, extend=blob), lay(1, 2, ddo), lay(3, 3, ddo), lay(10, 4, ddo),
            lay(11, 5, ddo), lay(9, 6, ddo), lay(8, 7, ddo), lay(7, 8, ddo), lay(6, 9, ddo), lay(5, 10, ddo),
            lay(0, 11, ddo)]
    z = Zone(schedule.ZON_DDO if ddo else schedule.ZON_DDDA, 2, "SoundGenerator", 0x5B8C5731, 0x157FF, 3, blob, lays,
             [ZoneGroup(5, -1, list(range(12)), [0, 1])], grid(3), [box(i) for i in range(12)], list(range(12)),
             [9, 8] if ddo else [])
    return z


def zon_type1() -> Zone:
    blob = xfs_blob()
    return Zone(schedule.ZON_DDDA, 1, "OM", 0x16A7FD63, 0x197FF, 1, blob, [lay(4, 1), lay(9, 0)],
                [ZoneGroup(1, 0, [0, 1], [1], grid(2), [box(0), box(1)]), ZoneGroup(2, 3, [1], [])],
                unique_index=[1, 0])


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


class SchedulerTest(unittest.TestCase):
    def test_ddda_round_trip_and_layout(self):
        sc = sdl_ddda()
        raw = schedule.build(sc)
        magic, ver, n, unk, word, base, sbase = struct.unpack_from("<4sHHIIII", raw, 0)
        self.assertEqual((magic, ver, n, unk, word & 0xFFFFFF), (b"SDL\0", 19, len(sc.tracks), 0xEE4828D3, 5000))
        self.assertEqual(sbase % 4, 0)
        self.assertEqual(raw[sbase:sbase + 5], b"Root\0")
        kind, ptype, cnt, par, name, idx, k, v = struct.unpack_from("<BBHIIIII", raw, 0x18 + 2 * 24)
        self.assertEqual((kind, ptype, cnt, par, k % 4, v % 16), (6, 6, 1, 1, 0, 0))
        self.assertEqual(struct.unpack_from("<I", raw, v)[0], 193)
        # the writer's string table: two resource values in a row get two copies; "x" reuses the path's tail
        self.assertEqual(raw.count(b"effect\\efl\\ev\\x\0"), 2)
        self.assertEqual(raw[sbase:].count(b"\0x\0"), 0)
        back = schedule.parse(raw)
        self.assertEqual(back, sc)
        self.assertEqual(schedule.build(back), raw)
        self.assertIn("17 tracks", schedule.info(back))

    def test_ddo_round_trip(self):
        sc = sdl_ddo()
        raw = schedule.build(sc)
        self.assertEqual(struct.unpack_from("<4sHH", raw, 0), (b"SDL\0", 22, len(sc.tracks)))
        self.assertEqual(struct.unpack_from("<I", raw, 0x0C)[0], 1200 | 1 << 24)     # FloorFrame
        self.assertEqual(struct.unpack_from("<II", raw, 0x18 + 4 * 32 + 16), (1, 2))  # mUnk10, mUnk14
        self.assertEqual(schedule.parse(raw), sc)
        bad = sdl_ddo()
        bad.tracks.append(Track(7, 0, 1, "mNew", keys=[(0, 0)], values=[0]))            # value size UNKNOWN
        with self.assertRaises(FormatError):
            schedule.build(bad)

    def test_yaml_round_trip(self):
        for sc in (sdl_ddda(), sdl_ddo()):
            raw = schedule.build(sc)
            y = schedule.to_yaml(schedule.parse(raw), "event\\x")
            self.assertIn("riftstone: sdl/1", y)
            self.assertEqual(schedule.yaml_to_bytes(y), raw)
        y = schedule.to_yaml(sdl_ddda())
        self.assertIn("value: -1", y)                                   # s32 property shown signed
        self.assertIn("type: rEffectList", y)
        moved = schedule.from_yaml(y.replace("name: HemiSphereLight", "name: HemiSphereLight2"))
        self.assertEqual(moved.tracks[1].name, "HemiSphereLight2")      # names are text; the table is rebuilt
        schedule.build(moved)

    def test_refusals(self):
        raw = schedule.build(sdl_ddda())
        k, v = struct.unpack_from("<II", raw, 0x18 + 2 * 24 + 16)                      # mGroup: 1 key, then values
        self.assertGreater(v, k + 4)                                                    # padding up to 16
        for bad in (b"SDM\0" + raw[4:], raw[:0x10], raw[:4] + struct.pack("<H", 20) + raw[6:],
                    raw[:6] + struct.pack("<H", 0xFFFF) + raw[8:],                    # tracks past the end
                    raw[:0x14] + struct.pack("<I", len(raw) + 4) + raw[0x18:],       # string table outside
                    raw[:k + 4] + b"\1" + raw[k + 5:],                                # padding the writer never leaves
                    raw + b"\0",                                                      # trailing byte
                    raw[:-2]):
            with self.assertRaises(FormatError):
                schedule.parse(bad)
        keyed_root = sdl_ddda()
        keyed_root.tracks[0].keys, keyed_root.tracks[0].values = [(0, 0)], [1]
        with self.assertRaises(FormatError):
            schedule.build(keyed_root)
        first = sdl_ddda()
        first.tracks[12].values = ["Root", None]                        # would land on offset 0 = none
        with self.assertRaises(FormatError):
            schedule.build(first)

    def test_yaml_refusals(self):
        y = schedule.to_yaml(sdl_ddda())
        for bad in (y.replace("FrameMax: 5000", "FrameMax: 16777216"),
                    y.replace("version: 19", "version: 20"),
                    y.replace("{frame: 885, mode: 2, value: 0}", "{frame: 885, mode: 2}"),
                    y.replace("{frame: 885, mode: 2, value: 0}", "{frame: 885, mode: 256, value: 0}"),
                    y.replace("type: rEffectList", "type: rNoSuchClass"),
                    y.replace("kind: 4", "kind: 4\n    name: Spacer"),
                    # fuzz finding schedule_yaml-invariant-cd274414ddc2: DDDA's track records have no
                    # mUnk10 / mUnk14; the values were dropped silently, now refused
                    y.replace("kind: 4", "kind: 4\n    mUnk10: 1"),
                    y.replace("BaseTrack: 0", "BaseTrack: 0\nBaseTracks: 1")):
            self.assertNotEqual(bad, y)
            with self.assertRaises(ParamError):
                schedule.from_yaml(bad)

    def _corpus(self, kind: str, want: dict):
        from riftstone import corpus, typemap
        from riftstone.game import find_game
        ids = {typemap.BY_EXT["sdl"]: "sdl", typemap.BY_EXT["zon"]: "zon"}
        seen = dict.fromkeys(ids.values(), 0)
        for r in corpus.resources(find_game(kind), list(ids)):
            m = schedule.parse(r.data)
            self.assertEqual(schedule.build(m), r.data, r.label)
            ext = ids[r.type_id]
            seen[ext] += 1
            if seen[ext] % 5 == 1:                          # the proof script covers every file
                self.assertEqual(schedule.yaml_to_bytes(schedule.to_yaml(m, r.name.decode("latin-1"))), r.data, r.label)
        for ext, n in want.items():
            self.assertGreaterEqual(seen[ext], n, ext)

    @unittest.skipUnless(game_found("ddda"), "Dragon's Dogma: Dark Arisen not found")
    def test_corpus_ddda(self):
        self._corpus("ddda", {"sdl": 624, "zon": 477})

    @unittest.skipUnless(game_found("ddo"), "Dragon's Dogma Online not found")
    def test_corpus_ddo(self):
        self._corpus("ddo", {"sdl": 1481, "zon": 1698})


class ZoneTest(unittest.TestCase):
    def test_round_trips(self):
        for z in (zon_type2(), zon_type2(ddo=True), zon_type1(),
                  Zone(schedule.ZON_DDDA, 0, "SoundOcclusion", 0x3616A710, 0x15200, 1, xfs_blob(), [lay(8, 0)],
                       unique_index=[0])):
            raw = schedule.build(z)
            self.assertEqual(raw[:4], b"zon\0")
            back = schedule.parse(raw)
            self.assertEqual(back, z)
            y = schedule.to_yaml(back, "sound\\x")
            self.assertIn("riftstone: zon/1", y)
            self.assertEqual(schedule.yaml_to_bytes(y), raw)
            self.assertIn("rZone", schedule.info(back))

    def test_layout(self):
        z = zon_type1()
        raw = schedule.build(z)
        ver, ztype, n = struct.unpack_from("<III", raw, 4)
        self.assertEqual((ver, ztype, raw[16:16 + n]), (schedule.ZON_DDDA, 1, b"OM"))
        p = 16 + n + 8
        self.assertEqual(struct.unpack_from("<III", raw, p), (2, 4, 9))              # two layouts: Sphere, Panel
        self.assertEqual(struct.unpack_from("<5I", raw, p + 12), (2, 2, 1, 2, 2))    # group 0: 2, 1 + grid 2, 2
        self.assertEqual(struct.unpack_from("<4I", raw, p + 32), (1, 0, 0, 0))       # group 1: no grid
        self.assertEqual(struct.unpack_from("<II", raw, p + 48), (1, 2))             # ContentsNum, unique ids
        self.assertEqual(raw[p + 56:p + 60], b"XFS\0")
        self.assertIn(b"grco", raw)
        ddo = schedule.build(zon_type2(ddo=True))
        self.assertGreater(len(ddo), len(schedule.build(zon_type2())))              # AABB's extended decay, tables
        labels = [label for label, x in schedule.zone_xfs(schedule.parse(raw))]
        self.assertEqual(labels, ["contents"])
        self.assertIsInstance(schedule.zone_xfs(zon_type2())[1][1], xfs.Xfs)

    def test_zone_without_grid(self):
        z = zon_type2()
        z.grid = None
        raw = schedule.build(z)
        back = schedule.parse(raw)
        self.assertIsNone(back.grid)
        self.assertEqual(schedule.build(back), raw)
        z.layout_bounds = None
        raw = schedule.build(z)
        self.assertIsNone(schedule.parse(raw).layout_bounds)
        z.layouts, z.groups, z.unique_index = [], [], [0x6F637267]                 # would read back as a grid
        with self.assertRaises(FormatError):
            schedule.build(z)
        # fuzz finding schedule_yaml-invariant-c7e795420c4b: without layouts, "no bounds" and "zero bounds"
        # are the same bytes; parse reads [] and the YAML reader must give the same model
        z.unique_index = []
        text = schedule.to_yaml(z)
        self.assertNotIn("layout_bounds", text)
        m = schedule.from_yaml(text)
        self.assertEqual(m.layout_bounds, [])
        self.assertEqual(schedule.parse(schedule.build(m)), m)

    def test_refusals(self):
        raw = schedule.build(zon_type1())
        n = raw[12]
        grid_at = raw.index(b"grco")
        bounds_at = grid_at + 46 + 2 * 8 + 4 + 2 * 4
        for bad in (b"zoo\0" + raw[4:], raw[:6], raw[:4] + struct.pack("<I", 1) + raw[8:],
                    raw[:8] + struct.pack("<I", 3) + raw[12:],                       # zone type 3
                    raw[:16 + n + 12] + struct.pack("<I", 12) + raw[16 + n + 16:],    # shape 12
                    raw[:grid_at + 45] + b"\0" + raw[grid_at + 46:],                  # unpacked grid
                    raw[:bounds_at + 12] + struct.pack("<I", 5) + raw[bounds_at + 16:],   # box numbered 5
                    raw[:grid_at] + b"grcx" + raw[grid_at + 4:],                      # grid missing
                    raw + b"\0", raw[:-3],
                    raw[:16 + n + 16] + struct.pack("<I", 1 << 30) + raw[16 + n + 20:]):
            with self.assertRaises(FormatError):
                schedule.parse(bad)
        flag = zon_type2()
        raw2 = schedule.build(flag)
        at = raw2.index(b"XFS\0", raw2.index(b"XFS\0") + 4) - 1                      # layout 1's mpExtendObj flag
        with self.assertRaises(FormatError):
            schedule.parse(raw2[:at] + b"\2" + raw2[at + 1:])
        uneven = zon_type1()
        uneven.groups[0].bounds = [box(0)]
        with self.assertRaises(FormatError):
            schedule.build(uneven)
        noxfs = zon_type1()
        noxfs.contents = b"XFS\0" + b"\0" * 8
        with self.assertRaises(FormatError):
            schedule.build(noxfs)
        tail = zon_type1()
        tail.tail = [1]
        with self.assertRaises(FormatError):
            schedule.build(tail)

    def test_yaml_refusals(self):
        y = schedule.to_yaml(zon_type1())
        for bad in (y.replace("ZoneType: 1", "ZoneType: 3"),
                    y.replace("shape: 4", "shape: 12"),
                    y.replace("mGroupID: 2", "mGroupID: 2\n    mGroupId: 3", 1),
                    y.replace("nx: 2", "nx: 3"),
                    y.replace("mUnk0C: 3", "mUnk0C: 2147483648"),
                    y.replace("riftstone: zon/1", "riftstone: zon/2"),
                    y.replace("mUnk64: 0x000197ff", "mUnk64: 0x000197ff\nmUnkTable: [1]")):
            self.assertNotEqual(bad, y)
            with self.assertRaises(ParamError):
                schedule.from_yaml(bad)
        with self.assertRaises(ParamError):
            schedule.from_yaml("riftstone: xyz/1\n")


if __name__ == "__main__":
    unittest.main()

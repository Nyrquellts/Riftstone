import math
import os
import random
import struct
import unittest

import helpers  # noqa: F401 (sys.path)
from riftstone import lmt, lmtcodec, port, retarget
from riftstone.errors import RiftError

F1 = struct.pack("<f", 1.0)
ZERO16 = bytes(16)
Z180 = (0.0, 0.0, 1.0, 0.0)                     # 180 degrees about z: exact in codec 6 once normalised


def q14(v):
    n = round(v * 16383 / 4)
    return n if n >= 0 else 16383 + n


def rot_keys(quats):
    """A codec 6 buffer with one key per frame (the last key lasts 0 frames)."""
    n = len(quats)
    return lmt.Blob(lmtcodec.pack(6, [(1 if i < n - 1 else 0, tuple(q14(c) for c in q)) for i, q in enumerate(quats)]))


def const(usage, bone, value):
    codec = 2 if usage == 0 else 1
    return lmt.Track(codec, usage, 0, bone, F1, struct.pack("<4f", *value))


def motion_list(tracks, frames=3, version=67, shared=False):
    flags = 0x800000 | (1 if version == 67 else 0)
    tl = lmt.TrackList(list(tracks))
    ev = [lmt.EventGroup(bytes(64)) for _ in range(4)]
    motions = [lmt.Motion(tl, frames, -1, ZERO16, ZERO16, flags, ev, None)]
    if shared:
        ev2 = [lmt.EventGroup(bytes(64)) for _ in range(4)]
        motions.append(lmt.Motion(tl, frames, 0, ZERO16, ZERO16, flags, ev2, None))
    return lmt.build(lmt.Lmt(version, motions))


def mat_mul(a, b):
    return tuple(sum(a[r * 4 + k] * b[k * 4 + c] for k in range(4)) for r in range(4) for c in range(4))


def local_matrix(q, t):
    r = retarget.rows_from_quat(q)
    return (*r[0], 0.0, *r[1], 0.0, *r[2], 0.0, *t, 1.0)


def rigged(joints, version=0xD2, k=250.0, bmin=(-50.0, 0.0, -10.0)):
    """A model with bones only: joints = [(id, parent index or 255, offset, rest rotation)], with real local
    matrices and the second block written as the games do (D x inverse bind)."""
    nb = len(joints)
    bones = 0x84
    mats = bones + nb * (24 + 64 + 64) + 0x100
    head = port.MOD_HEADER.pack(b"MOD\0", version, nb, 0, 0, 0, 0, 0, 0, 0, 0, bones, 0, mats, mats, mats, mats, mats)
    out = bytearray(head + bytes(0x84 - len(head)))
    struct.pack_into("<3f", out, 0x50, *bmin)
    locs, worlds = [], []
    for jid, parent, off, rot in joints:
        out += struct.pack("<BBBBff3f", jid, parent, 255, 0, 0.0, math.sqrt(sum(c * c for c in off)), *off)
        loc = local_matrix(rot, off)
        locs.append(loc)
        worlds.append(loc if parent == 255 else mat_mul(loc, worlds[parent]))
    for loc in locs:
        out += struct.pack("<16f", *loc)
    d = (k, 0, 0, 0, 0, k, 0, 0, 0, 0, k, 0, *bmin, 1)
    for w in worlds:
        out += struct.pack("<16f", *mat_mul(d, retarget._inverse(w)))
    out += bytes(0x100)
    return bytes(out)


def rand_quat(rng):
    return retarget.qnorm(tuple(rng.uniform(-1, 1) for _ in range(4)))


class ConventionTest(unittest.TestCase):
    def test_quaternion_matrix(self):
        # the engine's rows (DDDA.exe 0x01051B60) are H(q)^T: a row vector times them = q v q*
        s = math.sqrt(0.5)
        r = retarget.rows_from_quat((0.0, 0.0, s, s))                # 90 degrees about z
        want = ((0, 1, 0), (-1, 0, 0), (0, 0, 1))
        for a, b in zip(r, want):
            for x, y in zip(a, b):
                self.assertAlmostEqual(x, y, places=12)
        self.assertEqual(tuple(round(c, 12) + 0.0 for c in retarget.qrot((0.0, 0.0, s, s), (1.0, 0.0, 0.0))),
                         (0.0, 1.0, 0.0))
        rng = random.Random(7)
        for _ in range(200):
            q = rand_quat(rng)
            v = tuple(rng.uniform(-100, 100) for _ in range(3))
            rows = retarget.rows_from_quat(q)
            row_times = tuple(sum(v[k] * rows[k][c] for k in range(3)) for c in range(3))
            for a, b in zip(row_times, retarget.qrot(q, v)):
                self.assertAlmostEqual(a, b, places=9)
            back = retarget.quat_from_rows(rows)                      # every branch of the conversion
            self.assertLess(retarget.angle(back, q), 1e-6)
            self.assertGreaterEqual(back[3], 0)

    def test_compose_is_child_times_parent(self):
        rng = random.Random(3)
        for _ in range(100):
            p = (rand_quat(rng), tuple(rng.uniform(-50, 50) for _ in range(3)))
            loc = (rand_quat(rng), tuple(rng.uniform(-50, 50) for _ in range(3)))
            q, t = retarget.compose(p, loc)
            m = mat_mul(local_matrix(*loc), local_matrix(*p))    # row vectors: child x parent
            want = local_matrix(q, t)
            for a, b in zip(m, want):
                self.assertAlmostEqual(a, b, places=9)
            back = retarget.relative(p, (q, t))
            self.assertLess(retarget.angle(back[0], loc[0]), 1e-6)
            for a, b in zip(back[1], loc[1]):
                self.assertAlmostEqual(a, b, places=9)

    def test_angle(self):
        s = math.sqrt(0.5)
        self.assertAlmostEqual(retarget.angle(retarget.IDENTITY, (0.0, 0.0, s, s)), 90.0, places=9)
        self.assertAlmostEqual(retarget.angle((0.0, 0.0, s, s), (0.0, 0.0, -s, -s)), 0.0, places=9)   # q and -q

    def test_body_and_check(self):
        s = math.sqrt(0.5)
        joints = [(0, 255, (0.0, 100.0, 0.0), retarget.IDENTITY),
                  (3, 0, (0.0, 10.0, 0.0), (0.0, 0.0, s, s)),          # a rotated rest
                  (7, 1, (5.0, 0.0, 0.0), retarget.IDENTITY)]
        b = retarget.body(rigged(joints))
        self.assertEqual(b.order, [0, 3, 7])
        self.assertEqual((b.joints[0].parent, b.joints[3].parent, b.joints[7].parent), (None, 0, 3))
        self.assertLess(retarget.angle(b.joints[3].rotation, (0.0, 0.0, s, s)), 1e-4)
        self.assertEqual(b.joints[7].position, (5.0, 0.0, 0.0))
        w = retarget.pose(b, [], [0], [7])
        for a, c in zip(w[7][0][1], (0.0, 115.0, 0.0)):                 # (5,0,0) turned 90 degrees about z
            self.assertAlmostEqual(a, c, places=4)
        k = retarget.check_body(b)
        self.assertLess(k["child_x_parent"]["translation"], 1e-3)
        self.assertLess(k["child_x_parent"]["rotation"], 1e-5)
        self.assertGreater(k["parent_x_child"]["translation"], 1.0)     # the other order does not fit
        self.assertEqual(k["rotated_rest"], 1)
        with self.assertRaises(RiftError):
            retarget.body(b"MOD\0" + bytes(200))

    def test_sample_reproduces_keys(self):
        vec = [(0, (1.0, 2.0, 3.0)), (4, (5.0, 2.0, -1.0)), (6, (5.0, 2.0, -1.0))]
        got = retarget.sample(vec, range(7), False)
        self.assertEqual(got[0], (1.0, 2.0, 3.0))
        self.assertEqual(got[4], (5.0, 2.0, -1.0))
        self.assertEqual(got[2], (3.0, 2.0, 1.0))                        # linear between keys
        self.assertEqual(retarget.sample(vec, [9], False), [(5.0, 2.0, -1.0)])   # held after the last key
        a, b = (0.1, 0.2, 0.3, 0.9), (-0.1, -0.2, -0.3, -0.9)             # the same rotation, opposite signs
        rot = [(0, a), (2, b), (3, (0.0, 0.0, 0.0, 1.0))]
        for mode in ("nlerp", "slerp"):
            got = retarget.sample(rot, [0, 1, 2, 3], True, mode)
            self.assertEqual(got[0], a)                                  # key frames: the keys themselves
            self.assertEqual(got[2], b)
            self.assertLess(retarget.angle(retarget.qnorm(got[1]), retarget.qnorm(a)), 1e-6)   # the short way


class RebakeTest(unittest.TestCase):
    """A 3-joint chain: joint 2 hangs off joint 1 in the source body and off joint 0 in the destination."""

    src = rigged([(0, 255, (0.0, 100.0, 0.0), retarget.IDENTITY), (1, 0, (0.0, 10.0, 0.0), retarget.IDENTITY),
                  (2, 1, (5.0, 0.0, 0.0), retarget.IDENTITY)])
    dst = rigged([(0, 255, (0.0, 100.0, 0.0), retarget.IDENTITY), (1, 0, (0.0, 10.0, 0.0), retarget.IDENTITY),
                  (2, 0, (0.0, 0.0, 0.0), retarget.IDENTITY)], version=0xD4)

    def tracks(self):
        # joint 1 turns from the identity (frame 0) to 180 degrees about z (frame 2): 90 degrees at frame 1;
        # joint 2 sits 5 cm along its parent's x, turned 180 degrees about z itself
        return [lmt.Track(6, 0, 0, 1, F1, ZERO16, rot_keys([retarget.IDENTITY, (0, 0, 0.5, 0.5), Z180]), None),
                const(1, 1, (0.0, 10.0, 0.0, 1.0)),
                const(0, 2, Z180),
                const(1, 2, (5.0, 0.0, 0.0, 1.0)),
                const(2, 2, (1.0, 1.0, 1.0, 0.0))]

    def test_chain_reparented_exact(self):
        data = motion_list(self.tracks())
        notes = []
        out = retarget.rebake(data, "ddo", self.src, self.dst, notes=notes)
        self.assertEqual(lmt.build(lmt.parse(out)), out)                     # byte-exact rebuild
        m = lmt.parse(out)
        ts = {(t.bone, t.usage): t for t in m.motions[0].tracks.tracks}
        self.assertEqual([t.bone for t in m.motions[0].tracks.tracks], [1, 1, 2, 2, 2])   # same places
        rt, pt = ts[(2, 0)], ts[(2, 1)]
        self.assertEqual((rt.codec, pt.codec, rt.extremes, pt.extremes), (6, 3, None, None))
        self.assertIs(ts[(2, 2)].buffer, None)                              # the scale track is kept as it is
        # exact values: world of joint 2 = (0,110,0) + joint 1's turn applied to (5,0,0); rotation 1 (x) 2
        want_pos = [(5.0, 10.0, 0.0), (0.0, 15.0, 0.0), (-5.0, 10.0, 0.0)]
        s = math.sqrt(0.5)
        want_rot = [Z180, (0.0, 0.0, s, -s), retarget.IDENTITY]
        pos = [v for _, v in lmtcodec.values(3, pt.buffer.data)]
        rot = [retarget.qnorm(v) for _, v in lmtcodec.values(6, rt.buffer.data)]
        for f in range(3):
            for a, b in zip(pos[f], want_pos[f]):
                self.assertAlmostEqual(a, b, places=5)
            self.assertLess(retarget.angle(rot[f], want_rot[f]), 1e-6)
        # the destination body now puts joint 2 where the source body did, every frame
        s_body, d_body = retarget.body(self.src), retarget.body(self.dst)
        before = retarget.pose(s_body, lmt.parse(data).motions[0].tracks.tracks, [0, 1, 2], [2])
        after = retarget.pose(d_body, m.motions[0].tracks.tracks, [0, 1, 2], [2])
        plain = retarget.pose(d_body, lmt.parse(data).motions[0].tracks.tracks, [0, 1, 2], [2])
        for f in range(3):
            self.assertLess(max(abs(a - b) for a, b in zip(before[2][f][1], after[2][f][1])), 1e-5)
            self.assertLess(retarget.angle(before[2][f][0], after[2][f][0]), 1e-5)
        self.assertGreater(max(abs(a - b) for a, b in zip(before[2][1][1], plain[2][1][1])), 4.0)   # not as it was
        self.assertTrue(any("joint 2: 1 track list(s) rebaked under parent 0 (was 1)" in n for n in notes))

    def test_converted_and_shared(self):
        data = motion_list(self.tracks(), shared=True)
        out = retarget.rebake(data, "ddo", self.src, self.dst, dst_game="ddda")
        m = lmt.parse(out)
        self.assertEqual(m.version, 67)                                      # same game out as in
        self.assertIs(m.motions[0].tracks, m.motions[1].tracks)              # shared list stays shared
        self.assertTrue(m.motions[1].flags & lmt.SHARED_TRACKS)
        conv = port.convert_lmt(out, "ddo", "ddda").data
        self.assertEqual(lmt.build(lmt.parse(conv)), conv)
        t = [t for t in lmt.parse(conv).motions[0].tracks.tracks if t.bone == 2 and t.usage in (0, 1)]
        self.assertEqual([x.codec for x in t], [6, 3])                        # both games read these alike

    def test_rotation_only_joint_left_unless_named(self):
        tracks = [x for x in self.tracks() if not (x.bone == 2 and x.usage == 1)]
        data = motion_list(tracks)
        res = retarget.rebake_ex(data, "ddo", self.src, self.dst)
        self.assertEqual(res.joints, {})
        self.assertIn(2, res.skipped)
        self.assertEqual(lmt.parse(res.data).motions[0].tracks.tracks[2].codec, 2)
        forced = retarget.rebake_ex(data, "ddo", self.src, self.dst, joints={2})
        ts = [(t.bone, t.usage, t.codec) for t in lmt.parse(forced.data).motions[0].tracks.tracks]
        self.assertEqual(ts, [(1, 0, 6), (1, 1, 1), (2, 0, 6), (2, 1, 3), (2, 2, 1)])   # position added

    def test_position_only_joint_gets_a_rotation(self):
        # the motion places joint 2 but leaves its turn to the rest pose (the identity under joint 1): the
        # rebake adds a rotation track, so its world rotation (joint 1's turn) carries over too
        tracks = [x for x in self.tracks() if not (x.bone == 2 and x.usage == 0)]
        data = motion_list(tracks)
        res = retarget.rebake_ex(data, "ddo", self.src, self.dst)
        self.assertEqual(res.joints, {2: 1})
        self.assertEqual(res.by_slot, {0: [2]})
        m = lmt.parse(res.data)
        ts = [(t.bone, t.usage, t.codec) for t in m.motions[0].tracks.tracks]
        self.assertEqual(ts, [(1, 0, 6), (1, 1, 1), (2, 0, 6), (2, 1, 3), (2, 2, 1)])    # rotation added before
        s_body, d_body = retarget.body(self.src), retarget.body(self.dst)
        before = retarget.pose(s_body, lmt.parse(data).motions[0].tracks.tracks, [0, 1, 2], [2])
        after = retarget.pose(d_body, m.motions[0].tracks.tracks, [0, 1, 2], [2])
        for f in range(3):
            self.assertLess(max(abs(a - b) for a, b in zip(before[2][f][1], after[2][f][1])), 1e-5)
            self.assertLess(retarget.angle(before[2][f][0], after[2][f][0]), 1e-5)

    def test_refusals(self):
        data = motion_list(self.tracks())
        with self.assertRaises(RiftError):
            retarget.rebake(data, "ddda", self.src, self.dst)               # a DDO file called DDDA's
        with self.assertRaises(RiftError):
            retarget.rebake(data, "ddo", self.src, self.dst, joints={9})    # no such joint
        with self.assertRaises(RiftError):
            retarget.rebake(data, "ddo", self.src, self.dst, mode="cubic")

    def test_hostile_inputs(self):
        # a motion claiming more frames than any game file (65,536): refused before any per-frame work
        with self.assertRaises(RiftError):
            retarget.rebake(motion_list(self.tracks(), frames=0x10000), "ddo", self.src, self.dst)
        # a rotation that is not a number
        tracks = self.tracks()
        tracks[2] = const(0, 2, (float("nan"), 0.0, 0.0, 1.0))
        with self.assertRaises(RiftError):
            retarget.rebake(motion_list(tracks), "ddo", self.src, self.dst)
        # fuzz: a rotation track in a vector codec (3 components) crashed the interpolation (IndexError)
        tracks = self.tracks()
        tracks[0] = lmt.Track(5, 0, 0, 1, F1, ZERO16, lmt.Blob(bytes([1, 2, 3, 2, 4, 5, 6, 0])), lmt.Blob(bytes(32)))
        with self.assertRaises(RiftError):
            retarget.rebake(motion_list(tracks), "ddo", self.src, self.dst)
        # a model whose parents loop
        bad = bytearray(self.src)
        bad[0x84 + 1] = 2                                                  # joint 0's parent: the last bone
        with self.assertRaises(RiftError):
            retarget.body(bytes(bad))

    def test_work_is_bounded(self):
        # review: MAX_FRAMES bounded one motion: 16 track lists claiming 65,535 frames in a 2 KB file took 13 s
        # and wrote 25 MB (a 140 KB file ~14 minutes, 1.5 GB). Now a file is refused before any per-frame work
        # when its lists need more than a rebake computes -- long motions, or many lists of vanilla length.
        import time

        def lists(n, frames, extra=()):
            ms = [lmt.Motion(lmt.TrackList([const(0, 2, Z180), const(1, 2, (5.0, 0.0, 0.0, 1.0)), *extra]), frames,
                             -1, ZERO16, ZERO16, 0x800001, [lmt.EventGroup(bytes(64)) for _ in range(4)], None)
                  for _ in range(n)]
            return lmt.build(lmt.Lmt(67, ms))

        for label, data in (("16 x 65,535 frames", lists(16, 0xFFFF)), ("600 x 1,800 frames", lists(600, 1800))):
            with self.subTest(label):
                t0 = time.perf_counter()
                with self.assertRaises(RiftError):
                    retarget.rebake(data, "ddo", self.src, self.dst)
                self.assertLess(time.perf_counter() - t0, 2.0)
        # tracks of joints neither body's chain needs are not sampled at every frame (each of 3,000 was, twice),
        # and of duplicate tracks only the one that counts (the last)
        others = [const(1, 9, (1.0, 2.0, 3.0, 1.0)) for _ in range(1500)] + \
                 [const(0, 1, retarget.IDENTITY) for _ in range(1500)]
        data = lists(1, 1800, others)
        sampled = []
        real = retarget.sample
        retarget.sample = lambda keys, times, rotation, mode="nlerp": sampled.append(len(times)) or \
            real(keys, times, rotation, mode)
        try:
            out = lmt.parse(retarget.rebake(data, "ddo", self.src, self.dst))
        finally:
            retarget.sample = real
        self.assertEqual(sampled, [1800] * 5)          # joint 1's turn and joint 2's two tracks; joint 2's again
        rt = next(t for t in out.motions[0].tracks.tracks if t.bone == 2 and t.usage == 0)
        pt = next(t for t in out.motions[0].tracks.tracks if t.bone == 2 and t.usage == 1)
        self.assertEqual((rt.codec, pt.codec, len(lmtcodec.keys(3, pt.buffer.data))), (6, 3, 1800))
        for _, v in lmtcodec.values(3, pt.buffer.data):            # joint 1 unturned: 5 cm along x, 10 up
            self.assertEqual(v, (5.0, 10.0, 0.0))
        # the tracks of a rebaked joint were each rebuilt by searching the whole list again: 16,000 more rotation
        # tracks on joint 2 (a 576 KB file) took ~6 s, 100,000 minutes
        data = lists(1, 10, [const(0, 2, Z180) for _ in range(16000)])
        t0 = time.perf_counter()
        out = lmt.parse(retarget.rebake(data, "ddo", self.src, self.dst))
        self.assertLess(time.perf_counter() - t0, 1.5)
        self.assertEqual(len(out.motions[0].tracks.tracks), 16002)

    def test_parent_scale_that_is_no_number_is_noted(self):
        # a scale that is not a number compared false with 1 +- 1e-4, so a destination parent the motion scales
        # so was left out of the note that the rebake does not carry parents' scale to children
        for scale in ((2.0, 1.0, 1.0, 0.0), (float("nan"), 1.0, 1.0, 0.0)):
            with self.subTest(scale=scale):
                res = retarget.rebake_ex(motion_list(self.tracks() + [const(2, 0, scale)]), "ddo", self.src, self.dst)
                self.assertEqual(res.scaled_parents, {0: 1})
        res = retarget.rebake_ex(motion_list(self.tracks() + [const(2, 0, (1.0, 1.0, 1.0, 0.0))]), "ddo", self.src,
                                 self.dst)
        self.assertEqual(res.scaled_parents, {})

    def test_unkeyed_and_untouched(self):
        data = motion_list([const(0, 1, retarget.IDENTITY)])
        self.assertEqual(retarget.rebake(data, "ddo", self.src, self.dst), data)   # nothing to do: same bytes


def _game_resource(kind, tid, name):
    from riftstone import corpus
    from riftstone.game import find_game
    g = find_game(kind)
    for path in g.archives():
        rows = [r for r in corpus.directory(path) if r[1] == tid and r[0] == name]
        if rows:
            with open(path, "rb") as fh:
                fh.seek(rows[0][4])
                return corpus.payload(fh.read(rows[0][2]), corpus.encrypted(path))
    return None


def both_games() -> bool:
    if os.environ.get("RIFTSTONE_SKIP_GAME"):
        return False
    if not helpers.ddo_key_present():
        return False
    from riftstone.game import find_game
    try:
        find_game("ddo")
        find_game("ddda")
        return True
    except RiftError:
        return False


class GameTest(unittest.TestCase):
    @unittest.skipUnless(both_games(), "needs Dragon's Dogma Online and Dark Arisen (and RIFTSTONE_SKIP_GAME unset)")
    def test_fighter_attacks_on_ddda_body(self):
        from riftstone import typemap
        ddo_body = _game_resource("ddo", typemap.BY_EXT["mod"], b"obj\\pl\\pl000000\\model\\pl000000_00")
        ddda_body = _game_resource("ddda", typemap.BY_EXT["mod"], b"model\\pl\\m\\m_base\\m000\\m000")
        data = _game_resource("ddo", typemap.BY_EXT["lmt"], b"obj\\pl\\pl000000\\motion\\m0001\\m0001_at\\m0001_at")
        self.assertIsNotNone(data)
        res = retarget.rebake_ex(data, "ddo", ddo_body, ddda_body, dst_game="ddda")
        self.assertEqual(lmt.build(lmt.parse(res.data)), res.data)
        self.assertIn(150, res.joints)
        self.assertNotIn(55, res.joints)                        # keyed by rotation only: left as it is
        conv = port.convert_lmt(res.data, "ddo", "ddda").data
        plain = port.convert_lmt(data, "ddo", "ddda").data
        self.assertEqual(lmt.build(lmt.parse(conv)), conv)
        src, dst = retarget.body(ddo_body), retarget.body(ddda_body)
        a, w, n = lmt.parse(data), lmt.parse(conv), lmt.parse(plain)
        worst = {"with": [0.0, 0.0], "without": [0.0, 0.0]}
        for slot, mo in enumerate(a.motions[:40]):
            if mo is None or 150 not in {t.bone for t in mo.tracks.tracks}:
                continue
            times = list(range(mo.frames))
            s = retarget.pose(src, mo.tracks.tracks, times, [150])
            for label, other in (("with", w), ("without", n)):
                d = retarget.pose(dst, other.motions[slot].tracks.tracks, times, [150])
                for f in times:
                    worst[label][0] = max(worst[label][0], max(abs(x - y) for x, y in zip(s[150][f][1], d[150][f][1])))
                    worst[label][1] = max(worst[label][1], retarget.angle(s[150][f][0], d[150][f][0]))
        self.assertLess(worst["with"][0], 0.001)                # cm
        self.assertLess(worst["with"][1], 0.05)                 # degrees (codec 6 steps)
        self.assertGreater(worst["without"][0], 50.0)           # today's port: the weapon is elsewhere


if __name__ == "__main__":
    unittest.main()

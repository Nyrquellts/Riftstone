import struct
import unittest

import helpers  # noqa: F401 (sys.path)
from riftstone import lmt, lmtcodec, port
from riftstone.errors import RiftError

F1 = struct.pack("<f", 1.0)
ZERO16 = bytes(16)


def motion_list(version, track, flags=0x800000):
    tl = lmt.TrackList([track, lmt.Track(1, 1, 0, 0, F1, ZERO16)])
    ev = [lmt.EventGroup(bytes(64)) for _ in range(4)]
    return lmt.build(lmt.Lmt(version, [lmt.Motion(tl, 3, -1, ZERO16, ZERO16, flags, ev, None)]))


def lmt_tid():
    from riftstone import typemap
    return typemap.BY_EXT["lmt"]


def xw14(pairs_with_delta):
    return b"".join(struct.pack("<I", d << 28 | w << 14 | a) for a, w, d in pairs_with_delta)


class LmtPortTest(unittest.TestCase):
    def test_ddo_to_ddda(self):
        # DDO: codec 11 through extremes (scale 1, offset 0 on x/w; offset 0.1 on y), flag bit 0 set
        ext = lmt.Blob(struct.pack("<8f", 1, 1, 1, 1, 0, 0.1, 0, 0))
        buf = lmt.Blob(xw14([(0, 16383, 1), (8000, 12000, 1), (16383, 0, 0)]))
        src = motion_list(67, lmt.Track(11, 0, 0, 7, F1, ZERO16, buf, ext), 0x800001)
        before = lmtcodec.values(11, buf.data, ext.data, ZERO16)
        done = port.convert_lmt(src, "ddo", "ddda")
        m = lmt.parse(done.data)
        self.assertEqual(m.version, 66)
        mo = m.motions[0]
        self.assertFalse(mo.flags & 1)
        t = mo.tracks.tracks[0]
        self.assertEqual((t.codec, t.bone, t.extremes), (6, 7, None))
        after = lmtcodec.values(6, t.buffer.data)
        self.assertEqual([f for f, _ in before], [f for f, _ in after])
        for (_, a), (_, b) in zip(before, after):
            for x, y in zip(a, b):
                self.assertLessEqual(abs(x - y), 0.000123)
        self.assertTrue(any("re-encoded as codec 6" in n for n in done.notes))

    def test_ddda_to_ddo_uses_reference_axes(self):
        ref = struct.pack("<4f", 0.0, 0.2, -0.1, 0.9)
        buf = lmt.Blob(xw14([(1000, 3900, 2), (16383 - 500, 4000, 0)]))
        src = motion_list(66, lmt.Track(11, 0, 0, 3, F1, ref, buf, None))
        done = port.convert_lmt(src, "ddda", "ddo")
        t = lmt.parse(done.data).motions[0].tracks.tracks[0]
        self.assertEqual(t.codec, 6)
        after = lmtcodec.values(6, t.buffer.data)
        self.assertAlmostEqual(after[0][1][1], 0.2, places=3)                 # y from the reference
        self.assertAlmostEqual(after[0][1][2], -0.1, places=3)
        self.assertLess(after[1][1][0], 0)                                    # x negative in the source

    def test_shared_buffer_keeps_each_tracks_axis(self):
        # review: the re-encode cache left the codec out of its key, so a codec 12 (Y) track sharing a codec 11
        # (X) track's buffer came out turned about X, and its change was never measured (0.000000 noted)
        ref = struct.pack("<4f", 0.0, 0.0, 0.0, 1.0)
        buf = lmt.Blob(xw14([(2048, 3547, 1), (1000, 16000, 0)]))
        tl = lmt.TrackList([lmt.Track(11, 0, 0, 5, F1, ref, buf), lmt.Track(12, 0, 0, 6, F1, ref, buf),
                            lmt.Track(11, 0, 0, 7, F1, ref, buf)])
        ev = [lmt.EventGroup(bytes(64)) for _ in range(4)]
        src = lmt.build(lmt.Lmt(66, [lmt.Motion(tl, 2, 0, ZERO16, ref, 0x800000, ev, None)]))
        before = lmt.parse(src).motions[0].tracks.tracks
        after = lmt.parse(port.convert_lmt(src, "ddda", "ddo").data).motions[0].tracks.tracks
        for a, b in zip(before, after):
            self.assertEqual((b.bone, b.codec), (a.bone, 6))
            for (_, x), (_, y) in zip(lmtcodec.values(a.codec, a.buffer.data, None, a.reference),
                                      lmtcodec.values(6, b.buffer.data)):
                for p, q in zip(x, y):
                    self.assertLessEqual(abs(p - q), 0.000123, (a.bone, x, y))
        self.assertIs(after[0].buffer, after[2].buffer)        # same codec: still one buffer
        self.assertIsNot(after[0].buffer, after[1].buffer)

    def test_buffer_shared_under_many_references_refused(self):
        # one buffer read with a different reference by each track decodes differently for each, so each got its
        # own codec 6 copy: 100 tracks on one 4 KB buffer made 800 KB out of 8 KB (grows with the square of the
        # file). No game file shares a single-axis buffer so; the copies may not need more than the file holds.
        n = 1000
        buf = lmt.Blob(xw14([(k, 4000, 1 if k < n - 1 else 0) for k in range(n)]))
        tl = lmt.TrackList([lmt.Track(11, 0, 0, j, F1, struct.pack("<4f", 0, j / 1000, 0, 1), buf)
                            for j in range(100)])
        ev = [lmt.EventGroup(bytes(64)) for _ in range(4)]
        src = lmt.build(lmt.Lmt(66, [lmt.Motion(tl, n, 0, ZERO16, ZERO16, 0x800000, ev, None)]))
        self.assertLess(len(src), 9000)
        with self.assertRaises(RiftError):
            port.convert_lmt(src, "ddda", "ddo")
        tl.tracks = tl.tracks[:1]                             # one reading of it is fine
        src = lmt.build(lmt.Lmt(66, [lmt.Motion(tl, n, 0, ZERO16, ZERO16, 0x800000, ev, None)]))
        self.assertEqual(lmt.parse(port.convert_lmt(src, "ddda", "ddo").data).motions[0].tracks.tracks[0].codec, 6)

    def test_codec6_extremes_dropped_for_ddo(self):
        buf = lmt.Blob(struct.pack("<Q", 1 << 56 | 4095))
        src = motion_list(66, lmt.Track(6, 0, 0, 1, F1, ZERO16, buf, lmt.Blob(bytes(32))))
        t = lmt.parse(port.convert_lmt(src, "ddda", "ddo").data).motions[0].tracks.tracks[0]
        self.assertIsNone(t.extremes)

    def test_refusals(self):
        with self.assertRaises(RiftError):   # version says DDDA but asked to port from DDO
            port.convert_lmt(motion_list(66, lmt.Track(1, 1, 0, 0, F1, ZERO16)), "ddo", "ddda")
        bad = motion_list(66, lmt.Track(11, 0, 0, 1, F1, ZERO16, lmt.Blob(xw14([(0, 1, 0)])),
                                        lmt.Blob(bytes(32))))
        with self.assertRaises(RiftError):   # DDDA never gives codec 11-13 extremes
            port.convert_lmt(bad, "ddda", "ddo")

    def test_fuzz_findings(self):
        # fuzz (port_lmt): a single-axis track with no keys crashed (AttributeError) ...
        empty = motion_list(67, lmt.Track(12, 0, 0, 1, F1, ZERO16, None, lmt.Blob(bytes(32))), 0x800001)
        with self.assertRaises(RiftError):
            port.convert_lmt(empty, "ddo", "ddda")
        # ... and NaN / infinite extremes reached the quantiser (ValueError, OverflowError)
        for bad in (float("nan"), float("inf")):
            ext = lmt.Blob(struct.pack("<8f", bad, 1, 1, 1, 0, 0, 0, 0))
            src = motion_list(67, lmt.Track(11, 0, 0, 1, F1, ZERO16, lmt.Blob(xw14([(5, 9, 0)])), ext), 0x800001)
            with self.assertRaises(RiftError):
                port.convert_lmt(src, "ddo", "ddda")
        ref = struct.pack("<4f", float("nan"), 0, 0, 1)
        src = motion_list(66, lmt.Track(12, 0, 0, 1, F1, ref, lmt.Blob(xw14([(5, 9, 0)])), None))
        with self.assertRaises(RiftError):
            port.convert_lmt(src, "ddda", "ddo")

    def test_drop_bones(self):
        self.assertEqual(port.parse_bones("55,150-152"), {55, 150, 151, 152})
        for bad in ("x", "3-1", "300", "1-"):
            with self.assertRaises(RiftError):
                port.parse_bones(bad)
        src = motion_list(67, lmt.Track(1, 1, 0, 150, F1, ZERO16), 0x800001)
        done = port.convert_lmt(src, "ddo", "ddda", drop_bones={150})
        self.assertEqual([t.bone for t in lmt.parse(done.data).motions[0].tracks.tracks], [0])
        self.assertTrue(any("1 track(s)" in n for n in done.notes))

    def test_convert_dispatch(self):
        from riftstone import typemap
        src = motion_list(67, lmt.Track(1, 1, 0, 0, F1, ZERO16), 0x800001)
        out = port.convert(src, typemap.BY_EXT["lmt"], "ddo", "ddda")
        self.assertEqual(lmt.parse(out.data).version, 66)
        with self.assertRaises(RiftError):                      # rebaking is for motion lists only
            port.convert(b"TEX\0", typemap.BY_EXT["tex"], "ddo", "ddda", rebake=(b"", b""))

    def test_player_motion_paths(self):
        # Studio's Port and `riftstone port` rebake these by default (the weapon joints)
        for path, want in (("obj/pl/pl000000/motion/m0001/m0001_at/m0001_at.lmt", True),
                           ("obj\\pl\\pl000000\\motion\\m0010\\a.lmt", True),
                           ("motion/pl/m/m00/m0004_at/m0004_at.lmt", True),
                           ("motion/em/em0100/em0100.lmt", False),
                           ("obj/pl/pl000000/motion/readme.tex", False)):
            self.assertEqual(port.is_player_motion(path), want, path)


class IntoModTest(unittest.TestCase):
    """What Studio's Port button and `riftstone port` do for a motion list: stand-in DDO and DDDA
    installs, the DDO motion list replacing a DDDA one inside a DDDA mod."""

    def test_port_motion_list_into_a_ddda_mod(self):
        import tempfile
        from pathlib import Path

        from riftstone import arc, typemap
        from riftstone.game import Game
        from riftstone.index import Index
        from riftstone.mod import Mod

        tid = typemap.BY_EXT["lmt"]
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            games = {}
            for kind, exe, arc_name, name, version, flags in (
                    ("ddo", "DDO.exe", "rom/job09", b"obj\\pl\\pl000000\\motion\\m0009\\m0009_at\\m0009_at", 67, 0x800001),
                    ("ddda", "DDDA.exe", "rom/game_main", b"motion\\pl\\m\\m00\\m0004_at\\m0004_at", 66, 0x800000)):
                root = d / kind
                (root / "nativePC" / "rom").mkdir(parents=True)
                (root / exe).write_bytes(b"")
                g = Game(root, kind)
                data = motion_list(version, lmt.Track(1, 1, 0, 0, F1, ZERO16), flags)
                entry = arc.Entry.from_data(name, tid, data, encrypted=(kind == "ddo"))
                g.arc_path(arc_name).write_bytes(arc.Archive([entry], encrypted=(kind == "ddo")).build())
                idx = Index(g, d / f"index-{kind}.sqlite")
                idx.refresh()
                games[kind] = (g, idx)
            m = Mod.create(d / "mod", "Vocation Port", game="ddda")
            (src, si), (dst, di) = games["ddo"], games["ddda"]
            res = port.into_mod(m.root, src, dst, si, di, "obj/pl/pl000000/motion/m0009/m0009_at/m0009_at.lmt",
                                as_="motion/pl/m/m00/m0004_at/m0004_at.lmt")
            si.close()
            di.close()
            self.assertEqual(len(res.written), 1)
            out = Path(res.written[0])
            self.assertTrue(out.is_file())
            self.assertEqual(out.relative_to(m.root).parts[0], "files")
            ported = lmt.parse(out.read_bytes())
            self.assertEqual(ported.version, 66)
            self.assertFalse(ported.motions[0].flags & 1)
            self.assertTrue(any("67 -> 66" in n for n in res.notes))

    @staticmethod
    def stand_ins(d):
        """A source game whose one archive holds motion/em/t.lmt (Dark Arisen's) and a destination without it:
        into(**kw) ports it into the mod d/mods/M."""
        from riftstone import arc, typemap

        data = lmt.build(lmt.Lmt(66, [lmt.Motion(lmt.TrackList(), 1, 0, ZERO16, ZERO16, 0)]))
        (d / "src.arc").write_bytes(arc.Archive([arc.Entry.from_data(b"motion\\em\\t", typemap.BY_EXT["lmt"],
                                                                     data)]).build())

        class Game:
            def __init__(self, kind):
                self.kind = self.title = kind

            def vanilla_arc(self, name):
                return d / "src.arc"

        class Index:
            def __init__(self, holders):
                self.holders = holders

            def archives_with(self, name, tid):
                return self.holders

        def into(**kw):
            return port.into_mod(d / "mods" / "M", Game("ddda"), Game("ddo"), Index(["rom/x"]), Index([]),
                                 "motion/em/t.lmt", **kw)
        return into

    def test_archive_and_names_checked(self):
        # review: --arc went into the written path unchecked, so '../../../escaped' wrote beside the mods folder
        # and an absolute name left the mod altogether (the game's own folder too); Studio's body values that
        # are not text (5, ["x"]) crashed (AttributeError) instead of being refused
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            into = self.stand_ins(d)
            mod = d / "mods" / "M"
            for bad in ("../../../escaped", "..", "rom/../../x", str(d / "abs"), "C:/x", "/rom/x", "rom//x",
                        "rom/x/"):
                with self.subTest(arc=bad), self.assertRaises(RiftError):
                    into(arc_name=bad)
            for key in ("as_", "like", "arc_name", "from_arc", "material"):
                for bad in (5, ["rom/x"], {"x": 1}):
                    with self.subTest(key=key, value=bad), self.assertRaises(RiftError):
                        into(**{key: bad})
            self.assertEqual([p.name for p in d.rglob("*")], ["src.arc"])          # nothing written anywhere
            res = into(arc_name="rom\\enemy\\em0100.arc")                        # the name as the game writes it
            self.assertEqual(res.written, [mod / "archives" / "rom" / "enemy" / "em0100.arc" / "motion" / "em" /
                                           "t.lmt"])
            self.assertTrue(res.written[0].is_file())

    def test_destination_names_windows_cannot_hold(self):
        # --as was written under the path as typed: a part Windows cannot hold as a name ('a:b', 'x ' with its
        # trailing space) crashed with OSError / FileNotFoundError. The resource goes where a mod keeps that
        # engine name (fsmap's file name), which the mod's build reads back as the same name.
        import tempfile
        from pathlib import Path

        from riftstone import fsmap

        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            into = self.stand_ins(d)
            for as_, name in (("motion/a:b.lmt", b"motion\\a:b"), ("motion/x /t.lmt", b"motion\\x \\t"),
                              ("motion\\em\\T.lmt.yaml", b"motion\\em\\T")):
                with self.subTest(as_=as_):
                    w = into(as_=as_, arc_name="rom/x").written[0]
                    self.assertTrue(w.is_file())
                    rel = w.relative_to(d / "mods" / "M" / "archives" / "rom" / "x.arc").as_posix()
                    self.assertEqual(fsmap.decode_path(rel), (name, lmt_tid()))


def rigged(joints, version=0xD4):
    """A model with bones only: joints = [(id, parent index or 255, (x, y, z))]."""
    nb = len(joints)
    bones = 0x84
    mats = bones + nb * (24 + 64 + 64) + 0x100
    meshes = vb = mats
    ib = vb
    end = ib
    head = port.MOD_HEADER.pack(b"MOD\0", version, nb, 0, 0, 0, 0, 0, 0, 0, 0, bones, 0, mats, meshes, vb, ib, end)
    out = bytearray(head + bytes(0x84 - len(head)))
    for jid, parent, (x, y, z) in joints:
        out += struct.pack("<BBBBff3f", jid, parent, 255, 0, 0.0, (x * x + y * y + z * z) ** 0.5, x, y, z)
    out += bytes(nb * 128 + 0x100)
    return bytes(out)


class SkeletonTest(unittest.TestCase):
    def test_skeleton_and_diff(self):
        a = rigged([(0, 255, (0, 100, 0)), (1, 0, (0, 10, 0)), (5, 1, (3, 0, 0))])
        b = rigged([(0, 255, (0, 100, 0)), (1, 0, (0, 12, 0)), (6, 1, (3, 0, 0))], 0xD2)
        sk = port.skeleton(a)
        self.assertEqual(sorted(sk), [0, 1, 5])
        self.assertIsNone(sk[0].parent)
        self.assertEqual(sk[5].parent, 1)
        d = port.skeleton_diff(a, b)
        self.assertEqual(d["common"], 2)
        self.assertEqual(d["only_first"], [5])
        self.assertEqual(d["only_second"], [6])
        self.assertEqual(d["offset_differs"], [(1, 2.0)])
        self.assertEqual(d["parent_differs"], [])

    def test_diff_offsets_that_are_not_numbers(self):
        # review: max(abs(p - q)) with a NaN is NaN (or skips it) and NaN > tolerance is False, so a joint at
        # (nan, 10, 0) matched one at (0, 10, 0) while one at (0, 99, 0) was reported; like monsters.compare,
        # an offset that is not a finite number is no known place
        nan, inf = float("nan"), float("inf")
        good = rigged([(0, 255, (0, 100, 0)), (1, 0, (0, 10, 0))])
        far = rigged([(0, 255, (0, 100, 0)), (1, 0, (0, 99, 0))])
        self.assertEqual(port.skeleton_diff(far, good)["offset_differs"], [(1, 89.0)])
        for off in ((nan, 10, 0), (0, 10, nan), (inf, 10, 0)):
            bad = rigged([(0, 255, (0, 100, 0)), (1, 0, off)])
            for x, y in ((bad, good), (good, bad), (bad, bad)):
                self.assertEqual(port.skeleton_diff(x, y)["offset_differs"], [(1, None)], off)
        for tolerance in (nan, inf, -1.0):     # riftstone skeleton --tolerance nan made every joint match
            with self.assertRaises(RiftError):
                port.skeleton_diff(far, good, tolerance)


if __name__ == "__main__":
    unittest.main()

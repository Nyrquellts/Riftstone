import math
import random
import struct
import unittest

import helpers  # noqa: F401 (sys.path)
from riftstone import corpus, inspect, lmt, lmtcodec, typemap
from riftstone.errors import FormatError

F1 = struct.pack("<f", 1.0)
REF = struct.pack("<4f", 0.0, 0.0, 0.0, 1.0)
ZERO16 = bytes(16)


def quat7(x, y, z, w, delta):
    return struct.pack("<I", delta << 28 | x << 21 | y << 14 | z << 7 | w)


def long_buffers(n):
    """One motion whose n tracks' buffers all run from 0x10 + 4t to the end of the file: n distinct
    buffers of nearly the whole file (36 KB for n = 1,000 held 34 MB of copies)."""
    end = 0x4C + n * lmt.TRACK_SIZE + 64
    b = bytearray(b"LMT\0" + struct.pack("<HHI", 66, 1, 0x10) + bytes(4))
    b += struct.pack("<IIIi16s16sIII", 0x4C, n, 1, 0, ZERO16, ZERO16, 0, 0, 0)
    for t in range(n):
        off = 0x10 + 4 * t
        b += struct.pack("<BBBB4sII16sI", 1, 1, 0, 0, bytes(4), (end - off) // 12 * 12, off, ZERO16, 0)
    return bytes(b + bytes(end - len(b)))


def overlapping_arrays(motions, nt):
    """`motions` motions whose nt-track arrays start 36 bytes apart, so they overlap: each is a distinct
    array (77 KB for 50 x 2,000 held 100,000 tracks)."""
    head = 8 + 4 * motions
    head += -head % 16
    tracks = head + 64 * motions
    b = bytearray(tracks + lmt.TRACK_SIZE * (nt + motions))
    b[:8] = b"LMT\0" + struct.pack("<HH", 66, motions)
    for i in range(motions):
        struct.pack_into("<I", b, 8 + 4 * i, head + 64 * i)
        struct.pack_into("<IIIi16s16sIII", b, head + 64 * i, tracks + lmt.TRACK_SIZE * i, nt, 1, 0, ZERO16,
                         ZERO16, 0, 0, 0)
    return bytes(b)


def one_header(slots):
    """Every slot pointing at one trackless header with an event block and 31 float groups: each slot
    became its own motion (9 KB for 2,000 slots rebuilt to 1.4 MB)."""
    head = 8 + 4 * slots
    head += -head % 16
    ev = head + 64
    fl = ev + lmt.EVENT_GROUPS * 72
    b = bytearray(fl + 31 * 12)
    b[:8] = b"LMT\0" + struct.pack("<HH", 66, slots)
    struct.pack_into(f"<{slots}I", b, 8, *([head] * slots))
    struct.pack_into("<IIIi16s16sIII", b, head, 0, 0, 1, 0, ZERO16, ZERO16, lmt.FLAG_EVENTS | 31 << 16, ev, fl)
    for g in range(lmt.EVENT_GROUPS):
        struct.pack_into("<I", b, ev + 72 * g + 68, fl)
    for g in range(31):
        struct.pack_into("<I", b, fl + 12 * g + 8, len(b))
    return bytes(b)


def sample(version=66, share_tracks=True, floats=True):
    """Two motions in three slots (slot 1 empty); motion 2 shares motion 0's track array; two tracks
    share one buffer; motion 0 has events and (optionally) one group of float frames."""
    ext = lmt.Blob(struct.pack("<8f", 1, 1, 1, 1, 0, 0, 0, 0))
    shared = lmt.Blob(quat7(127, 0, 0, 0, 3) + quat7(0, 127, 0, 0, 0))
    tl = lmt.TrackList([
        lmt.Track(7, 0, 0, 1, F1, REF, shared, ext),
        lmt.Track(7, 0, 0, 2, F1, REF, shared, ext),
        lmt.Track(1, 1, 0, 0xFF, F1, REF),
        lmt.Track(5, 1, 0, 3, F1, REF, lmt.Blob(bytes([1, 2, 3, 3])), lmt.Blob(bytes(32))),
    ])
    events = [lmt.EventGroup(bytes(64), [(1, 2), (4, 1)] if g == 0 else []) for g in range(4)]
    flags = 0x800000 | (0x10000 if floats else 0)
    fl = [lmt.FloatGroup(b"\x01\x00\x00\x00", [(0xCD000001, struct.pack("<3f", 1, 0, 0))])] if floats else None
    m0 = lmt.Motion(tl, 4, -1, ZERO16, REF, flags, events, fl)
    m2 = lmt.Motion(tl if share_tracks else lmt.TrackList(list(tl.tracks)), 4, 0, ZERO16, REF, 0x800000,
                    [lmt.EventGroup(bytes(64)) for _ in range(4)], None)
    return lmt.Lmt(version, [m0, None, m2])


class LmtTest(unittest.TestCase):
    def test_round_trip_and_sharing(self):
        for version in (66, 67):
            raw = lmt.build(sample(version))
            self.assertEqual(raw[:4], b"LMT\0")
            self.assertEqual(struct.unpack_from("<HH", raw, 4), (version, 3))
            m = lmt.parse(raw)
            self.assertIsNone(m.motions[1])
            self.assertIs(m.motions[0].tracks, m.motions[2].tracks)            # shared array kept
            t = m.motions[0].tracks.tracks
            self.assertIs(t[0].buffer, t[1].buffer)                            # shared buffer kept
            self.assertIs(t[0].extremes, t[1].extremes)
            self.assertEqual(lmt.build(m), raw)

    def test_layout(self):
        raw = lmt.build(sample())
        offs = struct.unpack_from("<3I", raw, 8)
        self.assertEqual(offs[1], 0)
        self.assertEqual(offs[0] % 16, 0)
        self.assertEqual(offs[2], offs[0] + 64)                               # 16-byte stride
        tr0 = struct.unpack_from("<I", raw, offs[0])[0]
        tr2 = struct.unpack_from("<I", raw, offs[2])[0]
        self.assertEqual(tr0, tr2)                                            # written once
        self.assertEqual(tr0, offs[2] + lmt.MOTION_SIZE)                      # right after the headers
        # unshared, the second motion gets its own copy after the first motion's data
        raw2 = lmt.build(sample(share_tracks=False))
        o = struct.unpack_from("<3I", raw2, 8)
        self.assertNotEqual(struct.unpack_from("<I", raw2, o[0])[0], struct.unpack_from("<I", raw2, o[2])[0])
        self.assertEqual(lmt.build(lmt.parse(raw2)), raw2)

    def test_shared_flags(self):
        raw = lmt.build(sample())
        o = struct.unpack_from("<3I", raw, 8)
        f0 = struct.unpack_from("<I", raw, o[0] + 0x30)[0]
        f2 = struct.unpack_from("<I", raw, o[2] + 0x30)[0]
        self.assertFalse(f0 & lmt.SHARED_TRACKS)                             # first user: relocate
        self.assertTrue(f2 & lmt.SHARED_TRACKS)                              # reuser: already relocated
        m = lmt.parse(raw)
        m.motions[2].tracks = lmt.TrackList(list(m.motions[0].tracks.tracks))   # unshare
        m.motions[0].flags |= lmt.SHARED_EVENTS                              # a stale bit
        out = lmt.build(m)
        o = struct.unpack_from("<3I", out, 8)
        self.assertFalse(struct.unpack_from("<I", out, o[2] + 0x30)[0] & lmt.SHARED_TRACKS)
        self.assertFalse(struct.unpack_from("<I", out, o[0] + 0x30)[0] & lmt.SHARED_EVENTS)
        for off, bit in ((o[0], lmt.SHARED_TRACKS), (o[2], lmt.SHARED_TRACKS), (o[0], lmt.SHARED_FLOATS)):
            bad = bytearray(out)
            struct.pack_into("<I", bad, off + 0x30, struct.unpack_from("<I", out, off + 0x30)[0] | bit)
            with self.assertRaises(FormatError):
                lmt.parse(bytes(bad))

    def test_float_groups_follow_flags(self):
        self.assertEqual(lmt.float_groups(0x840000), 4)
        self.assertEqual(lmt.float_groups(0x8A0001), 10)
        self.assertEqual(lmt.float_groups(0x900000), 16)
        m = sample()
        m.motions[0].flags |= 0x20000                 # now 3 groups declared, 1 given
        with self.assertRaises(FormatError):
            lmt.build(m)

    def test_refusals(self):
        raw = lmt.build(sample())
        with self.assertRaises(FormatError):
            lmt.parse(b"LMX\0" + raw[4:])
        with self.assertRaises(FormatError):
            lmt.parse(raw[:4] + struct.pack("<H", 68) + raw[6:])
        with self.assertRaises(FormatError):
            lmt.parse(raw[:6] + struct.pack("<H", 0xFFFF))                   # table past the end
        bad = bytearray(raw)
        off = struct.unpack_from("<I", raw, 8)[0]
        struct.pack_into("<I", bad, off, len(raw) - 8)                        # tracks past the end
        with self.assertRaises(FormatError):
            lmt.parse(bytes(bad))
        bad = bytearray(raw)
        struct.pack_into("<I", bad, off + 0x38, 0)                            # flags name floats, none given
        with self.assertRaises(FormatError):
            lmt.parse(bytes(bad))
        for n in range(0, len(raw), 7):
            try:
                lmt.parse(raw[:n])
            except FormatError:
                pass

    def test_overlapping_blocks_refused(self):
        # review: parse copied each distinct buffer and parsed each distinct track array or header however
        # much they overlapped, so memory grew with the square of the file (288 KB of long_buffers(8000)
        # took ~2 GiB). A file holds each of its blocks once: together they cannot need more bytes than it has.
        import tracemalloc

        for label, data in (("buffers", long_buffers(1000)), ("track arrays", overlapping_arrays(50, 2000)),
                            ("headers", one_header(2000))):
            with self.subTest(label):
                tracemalloc.start()
                try:
                    with self.assertRaises(FormatError) as e:
                        lmt.parse(data)
                    peak = tracemalloc.get_traced_memory()[1]
                finally:
                    tracemalloc.stop()
                self.assertIn("overlap", str(e.exception))
                self.assertLess(peak, 4 * len(data) + (1 << 20))     # nothing copied once per reference
        # what the game's writer shares stays shared and readable: one array for two motions, one buffer and
        # one extremes block for two tracks, and every rebuilt file (sample) is read back
        for m in (sample(), sample(share_tracks=False), sample(floats=False)):
            raw = lmt.build(m)
            self.assertEqual(lmt.build(lmt.parse(raw)), raw)

    def test_codecs_round_trip(self):
        rnd = random.Random(7)
        widths = {3: (32, 32), 4: (16, 16), 5: (8, 8), 6: (14, 8), 7: (7, 4), 11: (14, 4), 12: (14, 4),
                  13: (14, 4), 14: (11, 4), 15: (9, 4)}
        comps = {3: 3, 4: 3, 5: 3, 6: 4, 7: 4, 11: 2, 12: 2, 13: 2, 14: 4, 15: 4}
        for codec, (cb, db) in widths.items():
            ks = [(rnd.randrange(1 << db), tuple(rnd.randrange(1 << cb) for _ in range(comps[codec])))
                  for _ in range(50)]
            buf = lmtcodec.pack(codec, ks)
            self.assertEqual(len(buf), 50 * lmtcodec.KEY_SIZE[codec])
            self.assertEqual(lmtcodec.keys(codec, buf), ks, codec)
            self.assertEqual(lmtcodec.pack(codec, lmtcodec.keys(codec, buf)), buf)
        with self.assertRaises(FormatError):
            lmtcodec.pack(15, [(16, (0, 0, 0, 0))])                          # delta is 4 bits
        with self.assertRaises(FormatError):
            lmtcodec.keys(14, bytes(7))                                       # not whole keys
        with self.assertRaises(FormatError):
            lmtcodec.keys(9, bytes(4))                                        # no such codec

    def test_values(self):
        ext = struct.pack("<8f", 1, 1, 1, 1, 0, 0, 0, 0)                     # scale 1, offset 0
        v = lmtcodec.values(7, quat7(0, 0, 0, 127, 5) + quat7(127, 0, 0, 0, 0), ext)
        self.assertEqual([f for f, _ in v], [0, 5])
        self.assertEqual(v[0][1], (0.0, 0.0, 0.0, 1.0))
        # DDDA single-axis quaternion: no extremes, signed 14-bit * 4 / 16383
        q = lmtcodec.values(11, struct.pack("<I", 4096 << 14 | 0), None)[0][1]
        self.assertAlmostEqual(math.sqrt(sum(c * c for c in q)), 1.0, places=3)
        neg = lmtcodec.values(11, struct.pack("<I", 4095 << 14 | (16383 - 72)), None)[0][1]
        self.assertLess(neg[0], 0)
        x = lmtcodec.values(2, struct.pack("<3f", 0.6, 0.0, 0.0), None)[0][1]
        self.assertAlmostEqual(x[3], 0.8, places=5)
        with self.assertRaises(FormatError):
            lmtcodec.values(7, quat7(0, 0, 0, 0, 0), None)                   # bilinear needs extremes

    def test_inspect(self):
        rep = inspect.describe(lmt.build(sample()), typemap.BY_EXT["lmt"])
        out = rep.text("x")
        self.assertIn("rMotionList v66", out)
        self.assertIn("2 motions in 3 slots", out)

    @unittest.skipUnless(helpers.game_root(), "no game")
    def test_corpus_sample_byte_exact(self):
        from riftstone.game import find_game
        n = 0
        for r in corpus.resources(find_game(), [typemap.BY_EXT["lmt"]], limit_per_type=40):
            m = lmt.parse(r.data)
            self.assertEqual(lmt.build(m), r.data, r.label)
            for mo in m.motions:
                for t in (mo.tracks.tracks if mo else ()):
                    if t.buffer is not None:
                        self.assertEqual(lmtcodec.pack(t.codec, lmtcodec.keys(t.codec, t.buffer.data)),
                                         t.buffer.data)
                        self.assertEqual(lmtcodec.span(t.codec, t.buffer.data), mo.frames - 1)
            n += 1
        self.assertGreater(n, 0)


if __name__ == "__main__":
    unittest.main()

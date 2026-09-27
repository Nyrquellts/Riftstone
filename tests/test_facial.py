import os
import struct
import unittest

import helpers  # noqa: F401 (sys.path)
from riftstone import facial
from riftstone.errors import FormatError, ParamError, RiftError
from riftstone.facial import Fca, Key, Track


def fb(v: float) -> int:
    return struct.unpack("<I", struct.pack("<f", v))[0]


def key(frame, value, mode=2):
    return Key(frame, mode, fb(value), 0, 0, fb(value), fb(value), 0, 0)


def sample() -> Fca:
    tracks = [Track(0, [key(0, 0.0), key(5, 0.5), key(9, 1.0)]),
              Track(fb(0.25), [key(0, 1.0, 1), Key(4, 3, fb(0.5), fb(1.0), fb(-1.0), 0, 0, fb(2.0), fb(-2.0))]),
              Track(0, [key(3, 0.75)])]
    return Fca(10, 81, 88, 0, (17, 16, 19, 18, 12, 11, 14, 13), tracks)


def ddda_found() -> bool:
    from riftstone.game import find_game
    if os.environ.get("RIFTSTONE_SKIP_GAME"):
        return False
    try:
        return find_game("ddda").kind == "ddda"
    except RiftError:
        return False


class FacialTest(unittest.TestCase):
    def test_round_trip_and_layout(self):
        f = sample()
        raw = facial.build(f)
        self.assertEqual(raw[:4], b"FCA\0")
        magic, ver, tracks, frames, eye_r, eye_l, flag = struct.unpack_from("<4sIIIiiB", raw, 0)
        self.assertEqual((ver, tracks, frames, eye_r, eye_l, flag), (facial.VERSION, 3, 10, 81, 88, 0))
        self.assertEqual(struct.unpack_from("<8i", raw, 0x1C), (17, 16, 19, 18, 12, 11, 14, 13))
        self.assertEqual(struct.unpack_from("<II", raw, 0x3C), (3, 0))                 # key count, default value
        self.assertEqual(struct.unpack_from("<iI", raw, 0x44), (0, 2))                 # first key: frame, linear
        self.assertEqual(len(raw), 0x3C + 3 * 8 + 6 * facial.KEY_SIZE)
        self.assertEqual(facial.parse(raw), f)
        self.assertEqual(facial.build(facial.parse(raw)), raw)

    def test_yaml_round_trip(self):
        raw = facial.build(sample())
        y = facial.to_yaml(facial.parse(raw), "etc\\lipsynch\\1")
        self.assertIn("riftstone: fca/1", y)
        self.assertIn("interpolation: 2", y)
        self.assertEqual(facial.yaml_to_bytes(y), raw)
        edited = facial.from_yaml(y.replace("FrameNum: 10", "FrameNum: 12"))
        self.assertEqual(edited.frame_num, 12)
        odd = sample()
        odd.pad = b"\1\2\3"                                        # bytes the engine never reads still survive
        raw2 = facial.build(odd)
        self.assertEqual(facial.yaml_to_bytes(facial.to_yaml(facial.parse(raw2))), raw2)

    def test_yaml_name_stays_in_its_comment(self):
        # the resource name went into the header comment as it was: a newline in it began a YAML line
        # of its own (here a second 'riftstone:' key, which also fooled params' tag detection)
        from riftstone import params
        raw = facial.build(sample())
        y = facial.to_yaml(sample(), "x\nriftstone: xfs/1\r\t\"#")
        self.assertEqual(facial.yaml_to_bytes(y), raw)
        self.assertEqual(params.yaml_to_resource(y), raw)

    def test_value_at(self):
        t = sample().tracks[0]
        self.assertAlmostEqual(facial.value_at(t, 0), 0.0)
        self.assertAlmostEqual(facial.value_at(t, 2.5), 0.25)       # linear between keys 0 and 1
        self.assertAlmostEqual(facial.value_at(t, 7), 0.75)
        self.assertAlmostEqual(facial.value_at(t, 100), 1.0)        # the last key holds
        step = sample().tracks[1]
        self.assertAlmostEqual(facial.value_at(step, 2), 1.0)       # step keeps the key's value
        self.assertIn("3 tracks", facial.info(sample()))

    def test_refusals(self):
        raw = facial.build(sample())
        for bad in (b"FCB\0" + raw[4:],                            # magic
                    raw[:0x20],                                    # header cut short
                    raw[:4] + struct.pack("<I", 0x12345678) + raw[8:],   # version
                    raw[:8] + struct.pack("<I", 1000) + raw[12:],  # more tracks than fit
                    raw[:-1],                                      # last key cut short
                    raw + b"\0",                                   # trailing byte
                    raw[:0x3C] + struct.pack("<I", 0) + raw[0x40:]):   # a track without keys
            with self.assertRaises(FormatError):
                facial.parse(bad)
        empty = sample()
        empty.tracks[2].keys = []
        with self.assertRaises(FormatError):
            facial.build(empty)
        big = sample()
        big.tracks[0].keys[0].frame = 1 << 31
        with self.assertRaises(FormatError):
            facial.build(big)

    def test_yaml_refusals(self):
        y = facial.to_yaml(sample())
        for bad in (y.replace("riftstone: fca/1", "riftstone: fcb/1"),
                    y.replace("FrameNum: 10", "FrameNum: -1"),
                    y.replace("FrameNum: 10", "FrameNum: 10\nFrameCount: 3"),        # unknown field
                    y.replace("ExtendEyeTrackNo: [17, 16,", "ExtendEyeTrackNo: [16,"),
                    y.replace("{frame: 3, interpolation: 2, value: 0.75", "{frame: 3, interpolation: 2, value: x"),
                    y.replace("- track: 1", "- track: 5"),
                    y.replace("interpolation: 1,", "interp: 1,")):
            self.assertNotEqual(bad, y)
            with self.assertRaises(ParamError):
                facial.from_yaml(bad)

    def test_yaml_long_hex_number(self):
        # base 16 has no digit limit, but 3,572 hex digits are over 4,300 decimal ones: the range message
        # printed the number and leaked int -> str's ValueError
        big = "0x" + "f" * 3572
        y = facial.to_yaml(sample())
        for bad in (y.replace("FrameNum: 10", "FrameNum: " + big), y.replace("- track: 1", "- track: " + big),
                    y.replace("{frame: 3,", "{frame: -" + big + ",")):
            self.assertNotEqual(bad, y)
            with self.assertRaises(ParamError) as cm:
                facial.from_yaml(bad)
            self.assertLess(len(str(cm.exception)), 200)

    @unittest.skipUnless(ddda_found(), "Dragon's Dogma: Dark Arisen not found")
    def test_corpus_byte_exact(self):
        from riftstone import corpus, typemap
        from riftstone.game import find_game
        n = keys = yaml_n = 0
        for r in corpus.resources(find_game("ddda"), [typemap.BY_EXT["fca"]]):
            n += 1
            f = facial.parse(r.data)
            self.assertEqual(facial.build(f), r.data, r.label)
            keys += sum(len(t.keys) for t in f.tracks)
            if n % 50 == 1:                                 # the proof script covers every file
                yaml_n += 1
                self.assertEqual(facial.yaml_to_bytes(facial.to_yaml(f, r.name.decode("latin-1"))), r.data, r.label)
        self.assertGreaterEqual(n, 10133)
        self.assertGreaterEqual(keys, 1678543)
        self.assertGreater(yaml_n, 100)


if __name__ == "__main__":
    unittest.main()

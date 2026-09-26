import os
import random
import struct
import unittest

import helpers  # noqa: F401 (sys.path)
from riftstone import camera
from riftstone.camera import Camera3, Camera5, CameraList, FloatTrack, Track
from riftstone.errors import FormatError, ParamError, RiftError


def fb(v: float) -> int:
    return struct.unpack("<I", struct.pack("<f", v))[0]


def ddda_sample() -> CameraList:
    frames = [((fb(1.0), fb(2.0), fb(3.0)), (fb(0.0), fb(25.0), fb(0.0)), (fb(0.0), fb(0.0), fb(0.0), fb(1.0)), fb(45.0)),
              ((fb(1.5), fb(2.5), fb(3.5)), (fb(0.5), fb(25.5), fb(0.5)), (fb(0.0), fb(0.6), fb(0.0), fb(0.8)), fb(40.0))]
    return CameraList(3, 256, {0: Camera3((0, 0, 0, 0), 0, fb(16 / 9), frames),
                               7: Camera3((1, -2, 3, -4), 1, fb(4 / 3), [])})


def ext(scale, offset):
    return tuple(fb(v) for v in scale) + tuple(fb(v) for v in offset)


def ddo_sample() -> CameraList:
    pos = Track(4, 1, 0, 0, 0, (fb(10.0), fb(20.0), fb(30.0), 0), ext((100, 50, 10, 0), (0, 0, 0, 0)),
                [(8, 65528, 32768, 3), (100, 200, 300, 2), (65528, 8, 8, 0)])
    target = Track(3, 1, 0, 0, 0, (0, 0, 0, 0), None, [(fb(1.0), fb(2.0), fb(3.0), 5), (fb(4.0), fb(5.0), fb(6.0), 0)])
    rot = Track(15, 0, 0, 0, 0, (0, 0, 0, fb(1.0)), ext((1, 1, 1, 1), (-0.5, -0.5, -0.5, 0)), [(256, 256, 256, 504, 5)])
    fov = FloatTrack(5, 0, fb(30.0), fb(20.0), [(8, 3), (248, 2), (128, 0)])
    still = Camera5((0,) * 8, 1, 0, fb(16 / 9), Track(1, 1, reference=(fb(1.0), fb(2.0), fb(3.0), 0)),
                    Track(1, 1), Track(2, 0, reference=(0, 0, 0, fb(1.0))), FloatTrack(2, 0, fb(50.0), 0))
    return CameraList(5, 256, {2: Camera5((0,) * 8, 6, 1, fb(16 / 9), pos, target, rot, fov), 9: still})


def ddda_found() -> bool:
    from riftstone.game import find_game
    if os.environ.get("RIFTSTONE_SKIP_GAME"):
        return False
    try:
        return find_game("ddda").kind == "ddda"
    except RiftError:
        return False


def ddo_found() -> bool:
    from riftstone.game import find_game
    if os.environ.get("RIFTSTONE_SKIP_GAME"):
        return False
    try:
        find_game("ddo")
    except RiftError:
        return False
    return helpers.ddo_key_present()


class DddaCameraTest(unittest.TestCase):
    def test_round_trip_and_layout(self):
        cl = ddda_sample()
        raw = camera.build(cl)
        self.assertEqual(raw[:8], b"LCM\0" + struct.pack("<HH", 3, 256))
        table = struct.unpack_from("<256I", raw, 8)
        self.assertEqual((table[0], table[7]), (0x408, 0x408 + 0x2C))       # blocks right after the table
        self.assertEqual(sum(1 for t in table if t), 2)
        self.assertEqual(len(raw), 0x408 + 2 * 0x2C + 2 * 44)               # 44 bytes of arrays per frame
        n, fovtype, _aspect, p_pos, p_tgt, p_quat, p_fov = struct.unpack_from("<III4I", raw, 0x408 + 0x10)
        self.assertEqual((n, fovtype), (2, 0))
        self.assertEqual((p_tgt - p_pos, p_quat - p_tgt, p_fov - p_quat), (24, 24, 32))
        back = camera.parse(raw)
        self.assertEqual(back, cl)
        self.assertEqual(camera.build(back), raw)
        y = camera.to_yaml(back, "event\\test\\camera")
        self.assertIn("riftstone: lcm/1", y)
        self.assertIn("fovtype: 0  # FOV_V", y)
        self.assertIn("{pos: [1.0, 2.0, 3.0], target: [0.0, 25.0, 0.0], quat: [0.0, 0.0, 0.0, 1.0], fov: 45.0}", y)
        self.assertEqual(camera.yaml_to_bytes(y), raw)
        self.assertIn("2 camera(s)", camera.info(back))

    def test_yaml_edit_changes_path_length(self):
        y = camera.to_yaml(ddda_sample())
        row = "      - {pos: [1.0, 2.0, 3.0], target: [0.0, 25.0, 0.0], quat: [0.0, 0.0, 0.0, 1.0], fov: 45.0}\n"
        edited = y.replace(row, row + row.replace("45.0}", "60.0}"))
        cl = camera.from_yaml(edited)
        self.assertEqual(len(cl.cameras[0].frames), 3)
        self.assertEqual(cl.cameras[0].frames[1][3], fb(60.0))
        self.assertEqual(camera.parse(camera.build(cl)), cl)

    def test_refusals(self):
        raw = camera.build(ddda_sample())
        bad = [b"XCM\0" + raw[4:], raw[:6], raw[:4] + struct.pack("<H", 4) + raw[6:],
               raw[:0x200],                                                  # table cut short
               raw + b"\0",                                                  # trailing byte
               raw[:8] + struct.pack("<I", 0x40C) + raw[12:],                 # block not where the game puts it
               raw[:0x408 + 0x10] + struct.pack("<I", 10 ** 6) + raw[0x408 + 0x14:],   # frames past the end
               raw[:0x408 + 0x20] + struct.pack("<I", 0) + raw[0x408 + 0x24:]]         # a zero array pointer
        for b in bad:
            with self.assertRaises(FormatError):
                camera.parse(b)
        with self.assertRaises(FormatError):
            camera.build(CameraList(3, 4, {4: Camera3()}))                   # slot outside the table
        with self.assertRaises(FormatError):
            camera.build(CameraList(3, 256, {0: Camera3(frames=[((1, 2), (1, 2, 3), (1, 2, 3, 4), 0)])}))
        with self.assertRaises(FormatError):
            camera.build(CameraList(3, 256, {0: ddo_sample().cameras[2]}))   # a DDO camera in a DDDA list
        y = camera.to_yaml(ddda_sample())
        for broken in (y.replace("riftstone: lcm/1", "riftstone: lcm/2"), y.replace("version: 3", "version: 4"),
                       y.replace("fov: 45.0}", "fov: warm}", 1), y.replace("quat: [0.0, 0.0, 0.0, 1.0]", "quat: [1.0]", 1),
                       y.replace("slot: 7", "slot: 0"), y.replace("slot: 7", "slot: 256"),
                       y.replace("fov: 45.0}", "fov: 45.0, roll: 0.0}", 1),            # a field frames do not have
                       y.replace("camera_num: 256", "camera_num: 256\ncameras_num: 3")):
            self.assertNotEqual(broken, y)
            with self.assertRaises(ParamError):
                camera.from_yaml(broken)

    @unittest.skipUnless(ddda_found(), "Dragon's Dogma: Dark Arisen not found")
    def test_corpus_byte_exact(self):
        from riftstone import corpus, typemap
        from riftstone.game import find_game
        n = ok = yaml_n = cams = 0
        for r in corpus.resources(find_game("ddda"), [typemap.BY_EXT["lcm"]]):
            n += 1
            cl = camera.parse(r.data)
            self.assertEqual(cl.version, 3)
            self.assertEqual(camera.build(cl), r.data, r.label)
            cams += len(cl.cameras)
            ok += 1
            if n % 4 == 1:                                  # the proof script covers every file
                yaml_n += 1
                self.assertEqual(camera.yaml_to_bytes(camera.to_yaml(cl, r.name.decode("latin-1"))), r.data, r.label)
        self.assertEqual(ok, n)
        self.assertGreaterEqual(n, 99)
        self.assertGreaterEqual(cams, 999)
        self.assertGreater(yaml_n, 20)


class DdoCameraTest(unittest.TestCase):
    def test_round_trip_and_layout(self):
        cl = ddo_sample()
        raw = camera.build(cl)
        self.assertEqual(raw[:8], b"LCM\0" + struct.pack("<HH", 5, 256))
        table = struct.unpack_from("<256I", raw, 8)
        self.assertEqual((table[2], table[9]), (0x408, 0x408 + 0xAC))
        blocks_end = 0x408 + 2 * 0xAC
        first_ext = struct.unpack_from("<I", raw, 0x408 + 0x2C + 0x20)[0]
        self.assertEqual(first_ext, (blocks_end + 15) & ~15)                 # extremes from a 16-byte boundary
        self.assertEqual(raw[blocks_end:first_ext], bytes(first_ext - blocks_end))
        for base in (0x2C, 0x50, 0x74):
            buf = struct.unpack_from("<I", raw, 0x408 + base + 0xC)[0]
            self.assertEqual(buf % 16, 0)
        self.assertEqual(struct.unpack_from("<5I", raw, 0x4B4 + 0x98), (2, 0, fb(50.0), 0, 0))  # constant fov
        back = camera.parse(raw)
        self.assertEqual(back, cl)
        self.assertEqual(camera.build(back), raw)
        y = camera.to_yaml(back, "x")
        self.assertIn("  # frame 0: (0, 50, 5)", y)                          # (q - 8) / 65520 through the extremes
        self.assertEqual(camera.yaml_to_bytes(y), raw)
        self.assertIn("2 camera(s) in 256 slots, 7 frames as 9 compressed keys", camera.info(back))

    def test_every_codec_round_trips(self):
        rng = random.Random(5)
        for codec, spec in camera.VEC_FIELDS.items():
            keys = [tuple(fb(rng.uniform(-9, 9)) if b == "f" else rng.randrange(1 << b) for b in spec) for _ in range(4)]
            for k in keys:
                self.assertEqual(camera._split(codec, camera._pack(codec, k, True), True), k)
                self.assertEqual(len(camera._pack(codec, k, True)), camera.VEC_KEY[codec])
            extremes = ext((1, 1, 1, 1), (0, 0, 0, 0)) if codec in camera.BILINEAR else None
            cl = ddo_sample()
            cl.cameras[2].target = Track(codec, 1, extremes=extremes, keys=keys)
            raw = camera.build(cl)
            self.assertEqual(camera.parse(raw), cl, codec)
            self.assertEqual(camera.yaml_to_bytes(camera.to_yaml(cl)), raw, codec)
        for codec, spec in camera.FLT_FIELDS.items():
            keys = [tuple(fb(rng.uniform(1, 90)) if b == "f" else rng.randrange(1 << b) for b in spec) for _ in range(3)]
            cl = ddo_sample()
            cl.cameras[9].fov = FloatTrack(codec, 0, fb(10.0), fb(5.0), keys)
            raw = camera.build(cl)
            self.assertEqual(camera.parse(raw), cl, codec)
            self.assertEqual(camera.yaml_to_bytes(camera.to_yaml(cl)), raw, codec)

    def test_decoders(self):
        t = Track(4, extremes=ext((100, 50, 10, 0), (1, 2, 3, 0)))
        self.assertEqual(camera.decode_key(t, (8, 65528, 32768, 1)), (1.0, 52.0, 3 + 10 * 32760 / 65520))
        t5 = Track(5, extremes=ext((240, 240, 240, 0), (0, 0, 0, 0)))
        self.assertEqual(camera.decode_key(t5, (8, 248, 128, 1)), (0.0, 240.0, 120.0))
        q = camera.decode_key(Track(6), (0x3FFF, 0, 0, 4096, 1))           # 14-bit two's complement, normalised
        self.assertAlmostEqual(q[0], -1 / 4096 / ((1 / 4096) ** 2 + 1) ** 0.5)
        self.assertAlmostEqual(q[3], 1 / ((1 / 4096) ** 2 + 1) ** 0.5)
        q11 = camera.decode_key(Track(11, extremes=ext((1, 1, 1, 1), (0, 0.6, 0, 0))), (8, 16376, 1))
        self.assertAlmostEqual(q11[1] ** 2 + q11[3] ** 2, 1.0)             # y comes from the offset, x from the key
        self.assertAlmostEqual(q11[0], 0.0)
        f = FloatTrack(4, 0, fb(30.0), fb(20.0), [(8, 2), (65528, 0)])
        self.assertEqual(camera.values(f), [(0, 30.0), (2, 50.0)])
        self.assertEqual(camera.values(FloatTrack(2, 0, fb(45.0), 0)), [(0, 45.0)])
        self.assertEqual(camera.values(Track(1, reference=(fb(1), fb(2), fb(3), 0))), [(0, (1.0, 2.0, 3.0, 0.0))])
        self.assertEqual([fr for fr, _ in camera.values(Track(9, keys=[(0, 0, 0, fb(1))] * 3))], [0, 1, 2])

    def test_refusals(self):
        raw = camera.build(ddo_sample())
        blk = 0x408
        pos_ext = blk + 0x2C + 0x20
        no_ext = raw[:pos_ext] + bytes(4) + raw[pos_ext + 4:]                 # bilinear codec without extremes
        bad_codec = raw[:blk + 0x2C] + bytes([16]) + raw[blk + 0x2D:]         # a codec the evaluator lacks
        keyless_buf = raw[:blk + 0x50] + bytes([1]) + raw[blk + 0x51:]        # codec 1 pointing at a buffer
        bad_fov = raw[:blk + 0x98] + bytes([6]) + raw[blk + 0x99:]
        odd_size = raw[:blk + 0x2C + 8] + struct.pack("<I", 23) + raw[blk + 0x2C + 12:]
        for b in (no_ext, bad_codec, keyless_buf, bad_fov, odd_size, raw[:-1], raw + b"\0", raw[:0x500]):
            with self.assertRaises(FormatError):
                camera.parse(b)
        cl = ddo_sample()
        cl.cameras[2].pos.extremes = None
        with self.assertRaises(FormatError):
            camera.build(cl)                                                  # codec 4 needs extremes
        cl = ddo_sample()
        cl.cameras[2].pos.keys = [(70000, 0, 0, 1)]
        with self.assertRaises(FormatError):
            camera.build(cl)                                                  # 16-bit field
        cl = ddo_sample()
        cl.cameras[9].pos.keys = [(1, 2, 3, 4)]
        with self.assertRaises(FormatError):
            camera.build(cl)                                                  # a constant track holds no keys
        cl = ddo_sample()
        cl.cameras[2].rot.keys = []
        with self.assertRaises(FormatError):
            camera.build(cl)                                                  # a keyed codec needs a key
        y = camera.to_yaml(ddo_sample())
        for broken in (y.replace("        - [256, 256, 256, 504, 5]", "        - [256, 256, 504, 5]"),
                       y.replace("codec: 15", "codec: 16"),
                       y.replace("      extremes: {scale: [100.0, 50.0, 10.0, 0.0], offset: [0.0, 0.0, 0.0, 0.0]}\n", ""),
                       y.replace("frame_num: 6", "frame_num: -1"),
                       y.replace("      usage: 1\n", "      usage: 1\n      speed: 2\n", 1),
                       y.replace("offset: [0.0, 0.0, 0.0, 0.0]}", "offset: [0.0, 0.0, 0.0, 0.0], bias: [1.0]}", 1)):
            self.assertNotEqual(broken, y)
            with self.assertRaises(ParamError):
                camera.from_yaml(broken)

    @unittest.skipUnless(ddo_found(), "Dragon's Dogma Online not found")
    def test_corpus_byte_exact(self):
        from riftstone import corpus, typemap
        from riftstone.game import find_game
        n = ok = yaml_n = cams = 0
        for r in corpus.resources(find_game("ddo"), [typemap.BY_EXT["lcm"]]):
            n += 1
            cl = camera.parse(r.data)
            self.assertEqual(cl.version, 5)
            self.assertEqual(camera.build(cl), r.data, r.label)
            cams += len(cl.cameras)
            for c in cl.cameras.values():                   # every keyed track spans frame_num - 1 frames
                for t in (c.pos, c.target, c.rot):
                    if t.keys:
                        self.assertEqual(sum(camera.key_frames(t.codec, k) for k in t.keys), c.frame_num - 1)
            ok += 1
            if n % 4 == 1:
                yaml_n += 1
                self.assertEqual(camera.yaml_to_bytes(camera.to_yaml(cl, r.name.decode("latin-1"))), r.data, r.label)
        self.assertEqual(ok, n)
        self.assertGreaterEqual(n, 52)
        self.assertGreaterEqual(cams, 1101)
        self.assertGreater(yaml_n, 10)


if __name__ == "__main__":
    unittest.main()

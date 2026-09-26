import os
import random
import struct
import unittest

import helpers  # noqa: F401 (sys.path)
from riftstone import sound
from riftstone.errors import FormatError, ParamError, RiftError


def rec(r, **over) -> dict:
    """A record of the _Rec `r` with every field 0, then `over`."""
    d = {n: [0] * c[2] if isinstance(c, tuple) else 0 for n, c in r.schema}
    for k, v in over.items():
        if k not in d:
            raise KeyError(k)
        d[k] = v
    return d


F = {"1.0": 0x3F800000, "-8.0": 0xC1000000, "-96.0": 0xC2C00000, "350.0": 0x43AF0000, "0.5": 0x3F000000}


def sections(**over) -> dict:
    d = {k: [] for k, _ in sound._SECTIONS}
    d.update(over)
    return d


def srq_ddda() -> sound.Sound:
    el = sound._SRQ_ELEMENT
    return sound.Sound("srq", {
        "elements": [rec(el, mReqNo=25, pad_00=0xCDCD, mCommand=1, mPrioMode=1, mLink=-1, mPan=-1, mVol=F["-8.0"],
                         mPitchShift=-300, mLFESend=F["-96.0"], mPacFileNameTableIndex=0, mSpeakerSetIndex=0,
                         mpPackage=0x1F19A590),
                     rec(el, mReqNo=26, mCommand=4, mRandomReqNo=3, mPacFileNameTableIndex=-1, mSpeakerSetIndex=-1)],
        "packages": [b"sound\\se\\om\\om1520\\om1520", b"sound\\se\\om\\om1520\\om1520_b"],
        "random": b"sound\\se\\om\\om1520\\om1520_rnd",
        **sections(speakerSets=[rec(sound._SPEAKER_SET, mCoordinate=2, mSpeakerSetting=1, mSpeakerIndex=0)],
                   speakers=[rec(sound._SPEAKER, mSpeakerPosition=[F["350.0"], 0, 0], mDirectionalCurveIndex=0)],
                   directionalCurves=[rec(sound._CURVE, mEnable=1, mZeroRadianValue=F["1.0"], mElementIndex=0)],
                   directionalCurveElements=[rec(sound._CURVE_ELEMENT, mAngle=F["0.5"]),
                                             rec(sound._CURVE_ELEMENT, mValue=F["1.0"])])})


def srq_ddo() -> sound.Sound:
    return sound.Sound("srq-ddo", {
        "elements": [rec(sound._SRQ_DDO_ELEMENT, mReqNo=3, mUnk02=255, mUnk03=0x7F, mCommand=1, mBankIndex=0,
                         mProgramNo=100, mVol=F["-8.0"], mLFESend=F["-96.0"], mSpeakerSetIndex=-1)],
        "banks": [b"sound\\se\\em\\em010306\\vo\\em010306_vo"], **sections()})


def stq_ddda() -> sound.Sound:
    return sound.Sound("stq", {
        "sources": [{"path": b"pwn\\f0\\wave26\\pwn_26415_f0",
                     **rec(sound._STQ_SOURCE, mStreamLength=26785, mSampleNum=98889, mChannelNum=1, mLoopStart=-1,
                           mLoopEnd=-1)}],
        "elements": [rec(sound._STQ_ELEMENT, mReqNo=0, mCategory=3, mCommand=1, mReadType=1, mDiskLocation=1,
                         mVol=F["-8.0"], mSrcFileNameTableIndex=0, mSpeakerSetIndex=-1),
                     rec(sound._STQ_ELEMENT, mReqNo=1, mSrcFileNameTableIndex=-1, mSpeakerSetIndex=-1)],
        "random": None, **sections()})


def stq_ddo() -> sound.Sound:
    src = sound._STQ_DDO_SOURCE
    return sound.Sound("stq-ddo", {
        "sources": [{"path": b"sound\\stream\\ev\\a", **rec(src, mSampleNum=153988, mChannelNum=1, mSampleRate=48000,
                                                           mLoopStart=-1, mLoopEnd=-1, mUnk1C=0x255D51CD, mUnk20=3)},
                    {"path": b"sound\\stream\\ev\\bb", **rec(src, mChannelNum=2)}],
        "elements": [rec(sound._STQ_DDO_ELEMENT, mReqNo=7, mSrcFileNameTableIndex=1, mDiskLocation=1,
                         mSpeakerSetIndex=-1)],
        **sections(speakerSets=[rec(sound._SPEAKER_SET, mSpeakerIndex=-1)])})


def srd() -> sound.Sound:
    picks = [rec(sound._PICK, mReqNo=11 + i, mRate=r) for i, r in enumerate((33, 33, 34))]
    picks += [rec(sound._PICK) for _ in range(16 - len(picks))]
    return sound.Sound("srd", {"elements": [{"mRandomNo": 3, "mTable": picks, "mAllowRepeat": 0, "pad_85": 0xCD,
                                             "pad_86": 0xCD, "pad_87": 0xCD, "mLastReqNo": -1}]})


def smx(fmt="smx") -> sound.Sound:
    return sound.Sound(fmt, {"mActive": 1, "mPriority": 0x78, "mEQPreset": [-1] * 9, "mReverbPreset": [-1] * 4,
                             "mFaders": [rec(sound._FADER, mFaderID=1, mSendID=-1, mVol=0xC0A00000, mEqNo=-2,
                                             mEqEffectNo=-2, mEffectNo=-2, mTransitionTime=200, mCurve=-1,
                                             mSustainTime=2000, mReleaseTime=300),
                                         rec(sound._FADER, mFaderID=0x51, mAbs=1)]})


def spl() -> sound.Sound:
    return sound.Sound("spl", {"mUnk0C": 0, "mUnk10": 0, "mIndexTbl": [-1] * 64,
                               "paths": [b"sound\\se\\om\\om1510\\om1510"]})


def sbkr() -> sound.Sound:
    wave = sound._BANK_WAVE
    return sound.Sound("sbkr", {
        "programs": [rec(sound._BANK_PROGRAM, mProgramNo=100, mUnk02=2, mWaveIndex=1)],
        "waves": [{"path": b"", **rec(wave)},
                  {"path": b"sound\\se\\om\\om513056\\wave\\om513056_box_open",
                   **rec(wave, mType=0x724DF879, mUnk05=0x17, mUnk09=0x5A, mUnk10=-100, mUnk1C=0x3EAAAA3B)}],
        "records": [rec(sound._BANK_C, mUnk00=F["1.0"], mUnk05=0xCD)]})


def sar() -> sound.Sound:
    d = {}
    for key, node in sound._SAR_FIELDS:
        if isinstance(node, str):
            d[key] = 0
        elif node[0] == "array":
            d[key] = [0x44960000, 0x44FA0000, 0x452F0000, 0x457A0000]
        else:
            d[key] = None
    d.update(mStageNo=101, NoramlBgm=20, DayBgm=0xFFFFFFFF, IsSoil=1,
             zone0=(b"rZone", b"sound\\se\\st\\pd003\\occ_pd003"),
             SoilRequest=(b"rSoundRequest", b"sound\\se\\mt\\mt_soil"),
             resource1=(b"rSoundStreamRequest", b"sound\\stream\\bgm\\bgm_field01\\bgm_field01"))
    return sound.Sound("sar", d)


SAMPLES = (srq_ddda, srq_ddo, stq_ddda, stq_ddo, srd, smx, lambda: smx("smx-ddo"), spl, sbkr, sar)


class SoundTest(unittest.TestCase):
    def test_round_trips(self):
        for make in SAMPLES:
            s = make()
            raw = sound.build(s)
            with self.subTest(fmt=s.fmt):
                self.assertEqual(sound.format_of(raw), s.fmt)
                self.assertTrue(sound.is_sound(raw))
                back = sound.parse(raw)
                self.assertEqual(back.fmt, s.fmt)
                self.assertEqual(back.data, s.data)
                self.assertEqual(sound.build(back), raw)
                y = sound.to_yaml(back, "x\\y")
                self.assertIn(f"riftstone: {s.fmt}/1", y)
                self.assertEqual(sound.yaml_to_bytes(y), raw)
                self.assertEqual(sound.from_yaml(y).data, s.data)
                self.assertTrue(sound.info(back))

    def test_srq_layout(self):
        raw = sound.build(srq_ddda())
        magic, ver, ne, nss, nsp, ndc, ndce, tbl, rnd = struct.unpack_from("<4s8I", raw, 0)
        self.assertEqual((magic, ver, ne, nss, nsp, ndc, ndce), (b"SREQ", 0x13, 2, 1, 1, 1, 2))
        self.assertEqual(tbl, 0x34 + 2 * 0x90)                        # the name table follows the elements
        first, second = struct.unpack_from("<2I", raw, tbl)
        self.assertEqual(first, tbl + 8)
        self.assertEqual(raw[first:raw.index(b"\0", first)], b"sound\\se\\om\\om1520\\om1520")
        self.assertEqual(raw[rnd:raw.index(b"\0", rnd)], b"sound\\se\\om\\om1520\\om1520_rnd")
        offs = struct.unpack_from("<4I", raw, 0x24)
        self.assertEqual(offs[0] % 16, 0)                              # speaker data is 16-aligned
        self.assertEqual(list(offs), [offs[0], offs[0] + 12, offs[0] + 12 + 28, offs[0] + 12 + 28 + 16])
        self.assertEqual(len(raw), offs[3] + 2 * 8)
        ddo = sound.build(srq_ddo())
        self.assertEqual(ddo[:8], b"SRQR\x03\0\0\0")
        self.assertEqual(struct.unpack_from("<2I", ddo, 0x1C), (0, 0x34 + 0x84))

    def test_stq_layout(self):
        raw = sound.build(stq_ddda())
        self.assertEqual(struct.unpack_from("<3I", raw, 0x20), (0x3C, 0x3C + 0x18, 0xFFFFFFFF))
        self.assertTrue(raw.endswith(b"pwn\\f0\\wave26\\pwn_26415_f0\0"))   # stored without sound\stream\
        ddo = sound.build(stq_ddo())
        self.assertEqual(struct.unpack_from("<2I", ddo, 0x20), (0x38, 0x38 + 2 * 0x24))
        self.assertTrue(ddo.endswith(b"sound\\stream\\ev\\a\0sound\\stream\\ev\\bb\0"))

    def test_other_layouts(self):
        self.assertEqual(sound.build(srd())[:16], b"DNRS" + struct.pack("<3I", 2, 1, 0xFFFFFFFF))
        self.assertEqual(len(sound.build(srd())), 16 + 0x8C)
        m = sound.build(smx())
        self.assertEqual(m[:12], b"SMX\0\x03\0\0\0\x01\x78\0\0")
        self.assertEqual(len(m), 0x44 + 2 * 20)
        self.assertEqual(sound.build(smx("smx-ddo"))[:8], b"SMXR\x01\0\0\0")
        self.assertEqual(len(sound.build(spl())), 0x114 + 26)
        b = sound.build(sbkr())
        self.assertEqual(len(b), 0x14 + 12 + (1 + 0x50) + (len(b"sound\\se\\om\\om513056\\wave\\om513056_box_open")
                                                             + 1 + 0x50) + 8)
        a = sound.build(sar())
        self.assertEqual(a[:12], b"sar\0\x11\0\0\0" + struct.pack("<i", 101))

    def test_refusals(self):
        with self.assertRaises(FormatError):
            sound.parse(b"XXXX\x13\0\0\0")
        with self.assertRaises(FormatError):
            sound.parse(b"SREQ")                                        # no version
        with self.assertRaises(FormatError):
            sound.parse(b"SREQ\x14\0\0\0" + bytes(0x40))                # another version
        self.assertIsNone(sound.format_of(b"SREQ\x14\0\0\0"))
        raw = bytearray(sound.build(srq_ddda()))
        with self.assertRaises(FormatError):
            sound.parse(bytes(raw) + b"\0")                             # trailing byte
        tbl = struct.unpack_from("<I", raw, 0x1C)[0]
        bad = bytearray(raw)
        struct.pack_into("<I", bad, 0x1C, tbl + 4)
        with self.assertRaises(FormatError):
            sound.parse(bytes(bad))                                     # table not after the elements
        padded = bytearray(sound.build(sound.Sound("srq", {**srq_ddda().data, "random": None})))
        offs = struct.unpack_from("<I", padded, 0x24)[0]
        self.assertEqual(padded[offs - 14:offs], bytes(14))              # names end at 0x192, speakers at 0x1a0
        padded[offs - 1] = 1                                            # the zero padding before the speakers
        with self.assertRaises(FormatError):
            sound.parse(bytes(padded))
        bad = bytearray(raw)
        struct.pack_into("<I", bad, 8, 0x7FFFFFFF)                      # an absurd count: refused, not allocated
        with self.assertRaises(FormatError):
            sound.parse(bytes(bad))
        smxb = bytearray(sound.build(smx()))
        smxb[0x4F] = 1                                                  # a fader's pad byte
        with self.assertRaises(FormatError):
            sound.parse(bytes(smxb))
        with self.assertRaises(FormatError):
            sound.parse(sound.build(sar())[:-1])                        # unterminated text

    def test_every_truncation_refused(self):
        for make in SAMPLES:
            raw = sound.build(make())
            for n in range(len(raw)):
                with self.subTest(fmt=make().fmt, n=n):
                    with self.assertRaises(FormatError):
                        sound.parse(raw[:n])

    def test_mutations_refused_or_exact(self):
        rng = random.Random(0x50D)
        for make in SAMPLES:
            raw = sound.build(make())
            for _ in range(300):
                b = bytearray(raw)
                for _ in range(rng.randint(1, 3)):
                    b[rng.randrange(len(b))] = rng.randrange(256)
                try:
                    s = sound.parse(bytes(b))
                except FormatError:
                    continue
                self.assertEqual(sound.build(s), bytes(b))               # a successful parse always rebuilds

    def test_build_refusals(self):
        s = srq_ddda()
        s.data["packages"][0] = b"bad\0name"
        with self.assertRaises(FormatError):
            sound.build(s)
        s = srq_ddda()
        s.data["elements"][0]["mReqNo"] = 70000
        with self.assertRaises(FormatError):
            sound.build(s)
        s = srq_ddda()
        del s.data["elements"][0]["mVol"]
        with self.assertRaises(FormatError):
            sound.build(s)
        s = srd()
        s.data["elements"][0]["mTable"].pop()
        with self.assertRaises(FormatError):
            sound.build(s)
        s = sar()
        s.data["zone1"] = (b"", b"x")
        with self.assertRaises(FormatError):
            sound.build(s)
        with self.assertRaises(FormatError):
            sound.build(sound.Sound("nope", {}))
        with self.assertRaises(FormatError):
            sound.build(sound.Sound("srq", {}))

    def test_yaml_refusals(self):
        y = sound.to_yaml(srq_ddda())
        with self.assertRaises(ParamError):
            sound.from_yaml(y.replace("riftstone: srq/1", "riftstone: srq/9"))
        with self.assertRaises(ParamError):
            sound.from_yaml(y.replace("mReqNo: 25", "mReqNo: 70000"))
        with self.assertRaises(ParamError):
            sound.from_yaml(y.replace("mReqNo: 25", "mReqNo: twenty"))
        with self.assertRaises(ParamError):
            sound.from_yaml(y.replace("mReqNo: 25", "mReqNoo: 25"))       # unknown field (and a missing one)
        with self.assertRaises(ParamError):
            sound.from_yaml(y.replace("mVol: -8.0", "mVol: 1e40"))       # does not fit a float32
        with self.assertRaises(ParamError):
            sound.from_yaml(y + "extra: 1\n")
        with self.assertRaises(ParamError):
            sound.from_yaml(y.replace('random: "sound', 'random: {hex: "zz"} #'))
        yr = sound.to_yaml(sar())
        with self.assertRaises(ParamError):
            sound.from_yaml(yr.replace("zone0: {type: \"rZone\"", "zone0: {type: \"\""))
        with self.assertRaises(ParamError):
            sound.from_yaml(yr.replace("mStageNo: 101", "mStageNo: [1]"))
        ys = sound.to_yaml(srd())
        with self.assertRaises(ParamError):                             # mTable holds exactly 16 picks
            sound.from_yaml(ys.replace("      - {mReqNo: 0, mRate: 0}\n", "", 1))

    def test_yaml_edit(self):
        y = sound.to_yaml(srq_ddda())
        self.assertIn("mPacFileNameTableIndex: 0  # sound\\se\\om\\om1520\\om1520\n", y)   # cues name their package
        self.assertIn("mSrcFileNameTableIndex: 1  # sound\\stream\\ev\\bb\n", sound.to_yaml(stq_ddo()))
        y2 = y.replace("mVol: -8.0", "mVol: -3.5").replace('"sound\\\\se\\\\om\\\\om1520\\\\om1520_b"', '"a\\\\b"')
        s = sound.from_yaml(y2)
        self.assertEqual(s.data["elements"][0]["mVol"], struct.unpack("<I", struct.pack("<f", -3.5))[0])
        self.assertEqual(s.data["packages"][1], b"a\\b")
        again = sound.parse(sound.build(s))                             # the edit rebuilds a valid file
        self.assertEqual(again.data, s.data)
        yn = sound.to_yaml(sound.Sound("srq", {**srq_ddda().data, "packages": [b"\x81\x7f"]}))
        self.assertIn("{hex: ", yn)                                      # bytes that are not text travel as hex
        self.assertEqual(sound.from_yaml(yn).data["packages"], [b"\x81\x7f"])


def game_found(kind: str) -> bool:
    from riftstone.game import find_game
    if os.environ.get("RIFTSTONE_SKIP_GAME"):
        return False
    if kind == "ddo" and not helpers.ddo_key_present():
        return False
    try:
        find_game(kind)
    except RiftError:
        return False
    return True


def corpus_check(test, kind, exts, minimum):
    from riftstone import corpus, typemap
    from riftstone.game import find_game

    ids = [typemap.BY_EXT[e] for e in exts]
    n = {e: 0 for e in exts}
    failures = []
    for i, r in enumerate(corpus.resources(find_game(kind), ids)):
        ext = typemap.extension(r.type_id)
        n[ext] += 1
        try:
            s = sound.parse(r.data)
            test.assertEqual(sound.build(s), r.data)
            if i % 8 == 0:
                test.assertEqual(sound.yaml_to_bytes(sound.to_yaml(s, r.name.decode("latin-1")), r.label), r.data)
        except (AssertionError, RiftError) as e:
            failures.append(f"{r.label}: {e}")
    test.assertEqual(failures[:5], [])
    for e, m in minimum.items():
        test.assertGreaterEqual(n[e], m, e)


class SoundCorpusTest(unittest.TestCase):
    @unittest.skipUnless(game_found("ddda"), "Dragon's Dogma: Dark Arisen not found")
    def test_ddda_corpus(self):
        corpus_check(self, "ddda", ("srq", "stq", "srd", "smx", "spl"),
                     {"srq": 1600, "stq": 2632, "srd": 256, "smx": 49, "spl": 29})

    @unittest.skipUnless(game_found("ddo"), "Dragon's Dogma Online not found")
    def test_ddo_corpus(self):
        corpus_check(self, "ddo", ("srq", "stq", "smx", "sbkr", "sar"),
                     {"srq": 2399, "stq": 1132, "smx": 35, "sbkr": 1734, "sar": 684})


if __name__ == "__main__":
    unittest.main()

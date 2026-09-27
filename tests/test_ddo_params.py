import copy
import os
import random
import struct
import time
import unittest

import helpers  # noqa: F401 (sys.path)
from riftstone import ddo_params, typemap
from riftstone.ddo_params import KINDS, DdoParams
from riftstone.errors import FormatError, ParamError, RiftError


def bits(x: float) -> int:
    return struct.unpack("<I", struct.pack("<f", x))[0]


def blank(fields) -> dict:
    """Zero values for a schema (lists empty, references None, fixed arrays full)."""
    out = {}
    for f in fields:
        if f.kind == "k":
            continue
        if f.kind == "z":
            for g in f.fields:
                out[g.name] = [blank_value(g) for _ in range(f.n)]
        elif f.kind == "if":
            out[f.name] = None
        else:
            out[f.name] = blank_value(f)
    return out


def blank_value(f):
    if f.kind == "s":
        return [0, 0, 0] if f.t == "v3" else 0
    if f.kind == "str":
        return ""
    if f.kind == "ref":
        return None
    if f.kind == "b":
        return blank(f.fields)
    if f.kind == "a":
        return [blank_value(f.item) for _ in range(f.n)]
    return []


def fly_block() -> dict:
    return {"ホバリング速度": bits(2.5), "ホバリング高度": [bits(50.0), bits(150.0), bits(400.0), bits(0.0)],
            "ホバリング高度段階数": 3, "ホバリング到達判定幅": 25, "飛行速度": bits(-0.0), "飛行高度": bits(300.0)}


def sample(kind: str, fly: bool = False) -> DdoParams:
    """A small but full resource of each kind: every field kind set to something non-trivial."""
    d = blank(KINDS[kind].fields)
    if kind == "cpe":
        d["基礎物理攻撃力"] = bits(40.0)
        d["重量"] = bits(10.5)
        # a dict, not keywords: Python would fold the full-width Ｙ of Ｙ初速 into Y (NFKC)
        d["mJumpAttackSpeed"][1].update({"有効フラグ": 1, "前方速度": bits(10.0), "Ｙ初速": bits(35.0), "重力": bits(-3.0)})
        d["mGuardCounter"][9].update({"ガード回数(以下)": 3, "反撃確率(％以下で反撃)": 50})
        d["mUnk12C"] = [bits(1.0)] * 10
        d["男性の場合・女性の場合"] = [bits(1.0), bits(0.5)]
        d["エンチャントタイプ"] = 1
        d["mUnk114"] = 0x7FC00123                             # a NaN keeps its payload
        if fly:
            d["mFlgEnemyFly"] = 1
            d["cCharParamEnemyFly"] = fly_block()
    elif kind == "pep":
        d.update(mUnk70=bits(600.0), mUnk74=bits(50.0), mUnk78=1)
        rec = blank(KINDS["pep"].fields[4].item.fields)
        rec.update(mUnk04=0xFFFFFFFF, mUnk08=[0xFFFFFE, 0x80400, 0, 0], mUnk18=[0] * 4, mUnk28=[1, 0], mUnk34=-1,
                   mUnk30=13, mUnk38=[0], mUnk3D=1, mUnk40=-1, mUnk44=-1, mUnk48=3)
        d["mpArray"] = [rec, blank(KINDS["pep"].fields[4].item.fields)]
    elif kind == "prs":
        item = KINDS["prs"].fields[1].item
        rec = blank(item.fields)
        rec.update(mUnk04=1, mUnk0C=1, mUnk10=25, mUnk60=1000, mUnk20=bits(900.0), mUnk54=1, mUnk58=0xFFFFFFFF)
        rec["mRegionBreakInfo"]["mpArray"] = [{"mUnk08": 2, "mUnk04": -1, "mUnk0C": -1},
                                              {"mUnk08": 5, "mUnk04": 0, "mUnk0C": -1}]
        d["mpArray"] = [rec, blank(item.fields)]
    elif kind == "osp":
        rec = blank(KINDS["osp"].fields[1].item.fields)
        rec.update({"異常名称": 7, "異常有無": 1, "耐性値": bits(100.0), "有効時間": bits(30.0)})
        d["mpArray"] = [rec] * 3
    elif kind == "sti":
        d["mMdlSdlPath"] = "scr\\fd\\sdl\\fd000_m00"
        d["mUnk250"] = ["rStartPos", "scr\\st0100\\etc\\st0100"]
        d["mUnk450"][0] = ["rZone", "effect\\zon\\st\\c_st0100_00"]
        d["mScrSbcPathArr"] = ["scr\\a", "", "scr\\c"]
        d["mEffSbcPathArr"] = ["", "eff\\b", ""]
        d["mPos"] = [bits(1.0), bits(-2.0), bits(3.5)]
        d["mEpvIndexNight"] = -1
        d["EQLength"] = [bits(1200.0), bits(2000.0), bits(2800.0), bits(4000.0)]
        d["mAnotherMapName"] = "あいう"                        # Shift-JIS text
        d["mPerformanceFlag"] = 65391
    elif kind == "sal":
        d["mUnk90"] = 100
        d["mAdjoinInfo"] = [{"mIndex": [0, 1], "mDestinationStageNo": 200, "mNextStageNo": 200, "mPriority": 0},
                            {"mIndex": [], "mDestinationStageNo": 1, "mNextStageNo": 2, "mPriority": 255}]
        d["mJumpPosition"] = [{"mPos": [bits(1.0), bits(2.0), bits(3.0)], "mQuestId": 5, "mFlagId": 9}]
    elif kind == "evtr":
        d["mpArray"] = [{"mUnk04": "RES_ID_CAMERA", "mUnk08": 0x12C3BFA7BFF4E139},
                        {"mUnk04": "n0008.fsm.xml", "mUnk08": 0x66B45610F31319A8},
                        {"mUnk04": b"\x81\xff raw", "mUnk08": 0}]      # not Shift-JIS: kept as bytes
    elif kind == "ndp":
        d["mpArray"] = ndp_records()
    return DdoParams(kind, d)


def ndp_record(rid: int, typ: int = 2, hp: int = 100, **rates) -> dict:
    """A named-param record: every rate 100 unless given."""
    return {"mID": rid, "mType": typ, "mHpRate": hp, **{k: rates.get(k, 100) for k in ddo_params.NDP_RATES}}


def ndp_records() -> list[dict]:
    """The game's first record (Training), the neutral 2298, and one at every field's limit."""
    return [ndp_record(47, 2, 250, mExperience=0, mAttackBasePhys=80, mDefenceBaseMagic=75),
            ndp_record(2298),
            ndp_record(3250, 4, 0xFFFFFFFF, **{k: 0xFFFF for k in ddo_params.NDP_RATES})]


def samples():
    for kind in KINDS:
        yield sample(kind)
    yield sample("cpe", fly=True)


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


class DdoParamsTest(unittest.TestCase):
    def test_kinds_and_type_ids(self):
        for ext, spec in KINDS.items():
            self.assertEqual(spec.type_id, typemap.BY_EXT[ext])
            self.assertEqual(ddo_params.kind_for_type(spec.type_id), ext)
        self.assertIsNone(ddo_params.kind_for_type(0x12345678))
        self.assertEqual(ddo_params.kind_of(b"cpe\0\x0b\0\0\0"), "cpe")
        self.assertEqual(ddo_params.kind_of(b"sti\0"), "sti")
        self.assertEqual(ddo_params.kind_of(b"SAL\0"), "sal")
        self.assertIsNone(ddo_params.kind_of(b"\x11\0\0\0"))

    def test_cpe_layout_by_hand(self):
        raw = ddo_params.build(sample("cpe"))
        self.assertEqual(len(raw), 293)                   # the size of every ground enemy's file
        self.assertEqual(raw[:9], b"cpe\0\x0b\0\0\0\0")   # magic, version 0x0B, mFlgEnemyFly 0
        self.assertEqual(struct.unpack_from("<f", raw, 9)[0], 40.0)
        self.assertEqual(struct.unpack_from("<f", raw, 9 + 9 * 4)[0], 10.5)           # 重量, 10th float
        at = 9 + 13 * 4 + 5 * 4 + 4                       # after the jump attack speeds start
        self.assertEqual(raw[at + 13], 1)                 # mJumpAttackSpeed[1] 有効フラグ
        self.assertEqual(struct.unpack_from("<I", raw, 201)[0], 10)                   # mUnk12C count
        self.assertEqual(struct.unpack_from("<I", raw, 245)[0], 2)                    # the male/female pair
        fly = ddo_params.build(sample("cpe", fly=True))
        self.assertEqual(len(fly), 333)                   # the flying enemies' size
        self.assertEqual(fly[8], 1)
        self.assertEqual(fly[:8], raw[:8])
        self.assertEqual(fly[9:281], raw[9:281])          # same up to the flying fields
        self.assertEqual(fly[-12:], raw[-12:])            # and the three that follow them

    def test_ndp_layout_by_hand(self):
        raw = ddo_params.build(sample("ndp"))
        self.assertEqual(len(raw), 8 + 3 * 54)                       # u32 5, u32 count, 54-byte records
        self.assertEqual(raw[:8], struct.pack("<II", 5, 3))
        # the game's own first record, as it is in param/named_param.ndp
        self.assertEqual(raw[8:30].hex(), "2f00000002000000fa00000000005000640064006400")
        self.assertEqual(struct.unpack_from("<H", raw, 8 + 26)[0], 75)     # mDefenceBaseMagic: 12 + 7 x 2
        self.assertEqual(struct.unpack_from("<IIIH", raw, 8 + 2 * 54), (3250, 4, 0xFFFFFFFF, 0xFFFF))
        m = ddo_params.parse(raw, "ndp")
        self.assertEqual(m.data["mpArray"], ndp_records())
        self.assertEqual(list(m.data["mpArray"][0])[:4], ["mID", "mType", "mHpRate", "mExperience"])
        s = ddo_params.summary(m)
        self.assertIn("3 records, ids 47..3250", s)
        self.assertIn("1 leave every stat at 100%", s)
        four = ddo_params.summary(DdoParams("ndp", {"mpArray": [ndp_record(48), ndp_record(48)]}))
        self.assertIn("1 repeated", four)
        self.assertIn("multiple of 4", four)
        self.assertEqual(ddo_params.summary(DdoParams("ndp", {"mpArray": []})).split(": ")[1], "no records")
        text = ddo_params.to_yaml(m, "param\\named_param")
        self.assertIn("riftstone: ndp-ddo/1", text)
        self.assertIn("  - mID: 47  # the id the server sends as NamedEnemyParamsId", text)
        with self.assertRaises(FormatError):                          # mHpRate is a u32; the rates u16
            ddo_params.build(DdoParams("ndp", {"mpArray": [ndp_record(1, mExperience=0x10000)]}))

    def test_round_trip(self):
        for m in samples():
            raw = ddo_params.build(m)
            back = ddo_params.parse(raw, m.kind)
            self.assertEqual(back, m, m.kind)
            self.assertEqual(ddo_params.build(back), raw)
            if m.kind in ("cpe", "sti", "sal"):
                self.assertEqual(ddo_params.parse(raw), back)          # found by its magic
        for kind in KINDS:                                            # the blank form of each kind
            raw = ddo_params.build(DdoParams(kind, blank(KINDS[kind].fields)))
            self.assertEqual(ddo_params.build(ddo_params.parse(raw, kind)), raw)

    def test_version_and_magic_refused(self):
        for m in samples():
            raw = ddo_params.build(m)
            at = 4 if m.kind in ("cpe", "sti", "sal") else 0
            for word in (0, 0x12345678, 0xFFFFFFFF):
                bad = raw[:at] + struct.pack("<I", word) + raw[at + 4:]
                with self.assertRaises(FormatError, msg=(m.kind, word)):
                    ddo_params.parse(bad, m.kind)
            if at:
                with self.assertRaises(FormatError):
                    ddo_params.parse(b"XXX\0" + raw[4:], m.kind)
        with self.assertRaises(FormatError):
            ddo_params.parse(ddo_params.build(sample("pep")))          # no magic: the kind is needed
        with self.assertRaises(FormatError):
            ddo_params.parse(b"", "nope")
        prs = ddo_params.build(sample("prs"))
        rbi = 8 + 4 + 4 + 1 + 4 + 20 + 52 + 1 + 4                   # the first record's rRegionBreakInfo
        self.assertEqual(struct.unpack_from("<I", prs, rbi)[0], 2)
        with self.assertRaises(FormatError):
            ddo_params.parse(prs[:rbi] + struct.pack("<I", 3) + prs[rbi + 4:], "prs")

    def test_fly_flag_must_agree(self):
        ground, fly = ddo_params.build(sample("cpe")), ddo_params.build(sample("cpe", fly=True))
        for raw, flag in ((ground, 1), (ground, 2), (fly, 0)):
            with self.assertRaises(FormatError) as cm:
                ddo_params.parse(raw[:8] + bytes([flag]) + raw[9:])
            self.assertIn("mFlgEnemyFly", str(cm.exception))
        odd = fly[:8] + b"\x07" + fly[9:]                   # any non-zero byte flies, as in the game
        m = ddo_params.parse(odd)
        self.assertEqual(m.data["mFlgEnemyFly"], 7)
        self.assertEqual(ddo_params.build(m), odd)
        for flag, block in ((1, None), (0, fly_block())):
            m = sample("cpe")
            m.data["mFlgEnemyFly"], m.data["cCharParamEnemyFly"] = flag, block
            with self.assertRaises(FormatError) as cm:
                ddo_params.build(m)
            self.assertIn("mFlgEnemyFly", str(cm.exception))
        text = ddo_params.to_yaml(sample("cpe", fly=True))
        with self.assertRaises(ParamError):
            ddo_params.yaml_to_bytes(text.replace("mFlgEnemyFly: 1", "mFlgEnemyFly: 0"))
        cut = text.split("cCharParamEnemyFly:")[0] + "GUI" + text.split("\nGUI", 1)[1]
        with self.assertRaises(ParamError):
            ddo_params.yaml_to_bytes(cut)                                   # flag 1, block removed
        self.assertEqual(ddo_params.yaml_to_bytes(cut.replace("mFlgEnemyFly: 1", "mFlgEnemyFly: 0")),
                         ddo_params.build(sample("cpe")))                  # a ground enemy again

    def test_every_truncation_refused(self):
        for m in samples():
            raw = ddo_params.build(m)
            for n in range(len(raw)):
                with self.assertRaises(FormatError, msg=(m.kind, n)):
                    ddo_params.parse(raw[:n], m.kind)
            with self.assertRaises(FormatError):
                ddo_params.parse(raw + b"\0", m.kind)                      # a byte too many

    def test_member_capacity_refused(self):
        m = sample("cpe")
        m.data["mUnk12C"] = [0] * 12
        with self.assertRaises(FormatError):
            ddo_params.build(m)
        raw = ddo_params.build(sample("cpe"))
        with self.assertRaises(FormatError):                                # a count of 12 in the file
            ddo_params.parse(raw[:201] + struct.pack("<I", 12) + raw[205:] + bytes(8))
        m = sample("cpe", fly=True)
        m.data["cCharParamEnemyFly"]["ホバリング高度"] = [0] * 5
        with self.assertRaises(FormatError):
            ddo_params.build(m)
        m = sample("pep")
        m.data["mpArray"][0]["mUnk38"] = [1, 2]
        with self.assertRaises(FormatError):
            ddo_params.build(m)
        m = sample("cpe")                                                   # up to the member's size is fine
        m.data["mUnk12C"] = [bits(2.0)] * 11
        self.assertEqual(ddo_params.parse(ddo_params.build(m)), m)

    def test_absurd_counts_refused_fast(self):
        cases = [("pep", 16), ("prs", 4), ("osp", 4), ("evtr", 4), ("sal", 10), ("cpe", 201), ("ndp", 4)]
        for kind, at in cases:
            raw = ddo_params.build(sample(kind))
            for n in (0xFFFFFFFF, 0x7FFFFFFF, 1_000_000):
                t0 = time.perf_counter()
                with self.assertRaises(FormatError, msg=kind):
                    ddo_params.parse(raw[:at] + struct.pack("<I", n) + raw[at + 4:], kind)
                self.assertLess(time.perf_counter() - t0, 0.5)

    def test_strings_and_references(self):
        m = sample("sti")
        raw = ddo_params.build(m)
        self.assertIn(b"\0rStartPos\0scr\\st0100\\etc\\st0100\0", raw)
        m.data["mMdlSdlPath"] = "x" * 63                                   # the game keeps 63 bytes
        self.assertEqual(ddo_params.parse(ddo_params.build(m)).data["mMdlSdlPath"], "x" * 63)
        for bad in ("x" * 64, "a\0b", "€"):                           # too long, NUL, no Shift-JIS
            m.data["mMdlSdlPath"] = bad
            with self.assertRaises(FormatError, msg=bad):
                ddo_params.build(m)
        at = raw.index(b"scr\\fd\\sdl")
        with self.assertRaises(FormatError):                                # 64 bytes of text in a file
            ddo_params.parse(raw[:at] + b"y" * 64 + raw[at + 19:])
        with self.assertRaises(FormatError):
            ddo_params.parse(raw[:-1].replace(b"\0", b"_"))                # no terminator anywhere
        m = sample("sti")
        m.data["mAnotherMapName"] = "0123456789abcdef"                    # its buffer is 16 bytes
        with self.assertRaises(FormatError):
            ddo_params.build(m)
        for ref in (["", "x"], ["rZone"], "rZone", ["rZone", "p" * 64]):
            m = sample("sti")
            m.data["mUnk0AC"] = ref
            with self.assertRaises(FormatError, msg=ref):
                ddo_params.build(m)

    def test_hostile_mutations(self):
        rng = random.Random(0xDD0)
        for m in samples():
            raw = ddo_params.build(m)
            for _ in range(1500):
                b = bytearray(raw)
                for _ in range(rng.randint(1, 4)):
                    i = rng.randrange(len(b))
                    b[i] = rng.choice((0, 0xFF, rng.randrange(256), b[i] ^ (1 << rng.randrange(8))))
                if rng.random() < 0.2:
                    del b[rng.randrange(len(b)):]
                try:
                    p = ddo_params.parse(bytes(b), m.kind)
                except FormatError:
                    continue
                self.assertEqual(ddo_params.build(p), bytes(b), m.kind)   # whatever parses rebuilds exactly

    def test_build_refuses_bad_values(self):
        cases = [("cpe", lambda d: d.update(武器の種類=1 << 32)),
                 ("cpe", lambda d: d.update(基礎物理攻撃力=-1)),
                 ("cpe", lambda d: d.update(押さえ付け効き易さ=256)),
                 ("cpe", lambda d: d.update(筋力=1.5e39)),                    # a float too big for 32 bits
                 ("cpe", lambda d: d.update(筋力="strong")),
                 ("cpe", lambda d: d.update(筋力=True)),
                 ("cpe", lambda d: d["mJumpAttackSpeed"].pop()),
                 ("cpe", lambda d: d["mGuardCounter"][0].pop("ガード回数(以下)")),
                 ("cpe", lambda d: d.update(extra=1)),
                 ("cpe", lambda d: d.pop("重量")),
                 ("cpe", lambda d: d.update(mUnk12C=None)),
                 ("pep", lambda d: d["mpArray"][0].update(mUnk34=1 << 31)),
                 ("prs", lambda d: d["mpArray"][0]["mRegionBreakInfo"].update(mpArray=[{}])),
                 ("sal", lambda d: d["mAdjoinInfo"][0].update(mIndex=[65536])),
                 ("sal", lambda d: d["mJumpPosition"][0].update(mPos=[0, 0])),
                 ("sti", lambda d: d.update(mScrSbcPathArr=["a", "b"])),
                 ("sti", lambda d: d.update(mEpvPath=5)),
                 ("evtr", lambda d: d["mpArray"][0].update(mUnk08=-1))]
        for i, (kind, change) in enumerate(cases):
            m = copy.deepcopy(sample(kind))
            change(m.data)
            with self.assertRaises(FormatError, msg=i):
                ddo_params.build(m)
        with self.assertRaises(FormatError):
            ddo_params.build(DdoParams("nope", {}))
        with self.assertRaises(FormatError):
            ddo_params.build(DdoParams("cpe", []))
        m = sample("cpe")
        m.data["筋力"] = 12.5                                               # a plain float is stored as float32
        self.assertEqual(ddo_params.parse(ddo_params.build(m)).data["筋力"], bits(12.5))

    def test_yaml_round_trip(self):
        for m in samples():
            raw = ddo_params.build(m)
            text = ddo_params.to_yaml(m, "obj\\em\\em010100\\params\\em010100")
            self.assertIn(f"riftstone: {m.kind}-ddo/1", text)
            self.assertEqual(ddo_params.yaml_to_bytes(text, "t.yaml"), raw, m.kind)
            self.assertEqual(ddo_params.from_yaml(text), m)
        text = ddo_params.to_yaml(sample("cpe", fly=True))
        for line in ("基礎物理攻撃力: 40.0  # base physical attack", "mUnk114: nan:0x7fc00123",
                     "男性の場合・女性の場合: [1.0, 0.5]", "ホバリング高度: [50.0, 150.0, 400.0, 0.0]",
                     "飛行速度: -0.0", "cCharParamEnemyFly:", "反撃確率(％以下で反撃): 50"):
            self.assertIn(line, text)
        sti = ddo_params.to_yaml(sample("sti"))
        for line in ('mUnk250: ["rStartPos", "scr\\\\st0100\\\\etc\\\\st0100"]', "mUnk0AC: []",
                     'mScrSbcPathArr: ["scr\\\\a", "", "scr\\\\c"]', 'mAnotherMapName: "あいう"',
                     "mPos: [1.0, -2.0, 3.5]"):
            self.assertIn(line, sti)
        evtr = ddo_params.to_yaml(sample("evtr"))
        self.assertIn("mUnk08: 0x66b45610f31319a8  # rAIFSM", evtr)
        self.assertIn('mUnk04: {hex: "81ff20726177"}', evtr)
        self.assertIn("mUnk08: 0x0x".replace("0x0x", "0x0000000000000000"), evtr)
        odd = "x\nriftstone: xfs/1\r\t\"#"                  # a name cannot break out of its comment line
        self.assertEqual(ddo_params.yaml_to_bytes(ddo_params.to_yaml(sample("sal"), odd)),
                         ddo_params.build(sample("sal")))
        # fuzz finding ddo_params_yaml-invariant-a3d01b8f23a3: {hex: ...} holding bytes that are valid
        # Shift-JIS stayed bytes in the model, while parse gives text; both must now be the same model
        hexed = evtr.replace('mUnk04: {hex: "81ff20726177"}', 'mUnk04: {hex: "1ff255"}')
        m = ddo_params.from_yaml(hexed)
        self.assertEqual(m.data["mpArray"][2]["mUnk04"], "\x1f" + chr(0xE18D))   # cp932 1F F2 55
        self.assertEqual(ddo_params.parse(ddo_params.build(m), "evtr"), m)

    def test_yaml_edit_changes_only_that_field(self):
        m = sample("cpe")
        raw = ddo_params.build(m)
        text = ddo_params.to_yaml(m)
        out = ddo_params.yaml_to_bytes(text.replace("重量: 10.5", "重量: 99.25"))
        diff = [i for i in range(len(raw)) if raw[i] != out[i]]
        self.assertTrue(diff)
        self.assertTrue(all(9 + 36 <= i < 9 + 40 for i in diff))
        self.assertEqual(struct.unpack_from("<f", out, 45)[0], 99.25)
        more = ddo_params.yaml_to_bytes(text.replace("mUnk12C: [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]",
                                                     "mUnk12C: [2.0]"))
        self.assertEqual(len(more), len(raw) - 36)
        self.assertEqual(ddo_params.parse(more).data["mUnk12C"], [bits(2.0)])
        on = ddo_params.yaml_to_bytes(text.replace("暗い時のリンクする範囲を設定: 0", "暗い時のリンクする範囲を設定: true"))
        self.assertEqual(ddo_params.parse(on).data["暗い時のリンクする範囲を設定"], 1)

    def test_yaml_refusals(self):
        good = ddo_params.to_yaml(sample("cpe", fly=True))
        sti = ddo_params.to_yaml(sample("sti"))
        sal = ddo_params.to_yaml(sample("sal"))
        bad = [
            "riftstone: xfs/1\n",
            good.replace("riftstone: cpe-ddo/1", "riftstone: cpe/1"),
            good + "extra: 1\n",
            good.replace("重量: 10.5", "重量: 1e40"),
            good.replace("重量: 10.5", "重量: heavy"),
            good.replace("重量: 10.5", "重量: [1, 2]"),
            good.replace("重量: 10.5", "重さ: 10.5"),                          # unknown and missing
            good.replace("エンチャントタイプ: 1", "エンチャントタイプ: -1"),
            good.replace("エンチャントタイプ: 1", "エンチャントタイプ: 1.5"),
            good.replace("エンチャントタイプ: 1", "エンチャントタイプ: 0x100000000"),
            good.replace("押さえ付け効き易さ: 0", "押さえ付け効き易さ: 256"),
            good.replace("mUnk12C: [", "mUnk12C: [1.0, 1.0, "),                  # 12 > the member's 11
            good.replace("ホバリング高度: [", "ホバリング高度: [1.0, "),
            good.replace("  - 有効フラグ: 0  # enabled\n", "  - 有効フラグx: 0\n", 1),
            good.replace("mJumpAttackSpeed:", "mJumpAttackSpeed: []\nmJumpAttack:"),
            good.replace("cCharParamEnemyFly:", "cCharParamEnemyFly: 1\nunused:"),
            sti.replace("mUnk0AC: []", 'mUnk0AC: ["rZone"]'),
            sti.replace("mUnk0AC: []", 'mUnk0AC: ["", "path"]'),
            sti.replace("mUnk0AC: []", "mUnk0AC: rZone"),
            sti.replace('mAnotherMapName: "あいう"', 'mAnotherMapName: "' + "x" * 16 + '"'),
            sti.replace('mAnotherMapName: "あいう"', 'mAnotherMapName: "€"'),
            sti.replace('mAnotherMapName: "あいう"', "mAnotherMapName: {hex: zz}"),
            sti.replace('mScrSbcPathArr: ["scr\\\\a", "", "scr\\\\c"]', 'mScrSbcPathArr: ["a"]'),
            sti.replace("mPos: [1.0, -2.0, 3.5]", "mPos: [1.0, -2.0]"),
            sal.replace("mIndex: [0, 1]", "mIndex: [0, 65536]"),
            sal.replace("mAdjoinInfo:", "mAdjoinInfo: {}\nx:"),
        ]
        for i, t in enumerate(bad):
            self.assertNotEqual(t, good if i < 16 else (sti if i < 24 else sal), msg=i)
            with self.assertRaises(ParamError, msg=i):
                ddo_params.yaml_to_bytes(t, "bad.yaml")

    def test_yaml_long_hex_number(self):
        # base 16 has no digit limit, but 3,572 hex digits are over 4,300 decimal ones: the range message
        # printed the number and leaked int -> str's ValueError
        big = "0x" + "f" * 3572
        good = ddo_params.to_yaml(sample("cpe", fly=True))
        for bad in (good.replace("エンチャントタイプ: 1", "エンチャントタイプ: " + big),
                    good.replace("エンチャントタイプ: 1", "エンチャントタイプ: -" + big)):
            self.assertNotEqual(bad, good)
            with self.assertRaises(ParamError) as cm:
                ddo_params.from_yaml(bad)
            self.assertLess(len(str(cm.exception)), 200)

    def test_yaml_hostile_text(self):
        rng = random.Random(11)
        for m in (sample("cpe", fly=True), sample("sti"), sample("sal")):
            good = ddo_params.to_yaml(m)
            for _ in range(300):
                t = list(good)
                for _ in range(rng.randint(1, 3)):
                    i = rng.randrange(len(t))
                    t[i] = rng.choice("0123456789-.:[]{}#x \n\"")
                try:
                    raw = ddo_params.yaml_to_bytes("".join(t), "fuzz.yaml")
                except RiftError:
                    continue
                self.assertEqual(ddo_params.build(ddo_params.parse(raw, m.kind)), raw)

    def test_summary(self):
        s = ddo_params.summary(sample("cpe", fly=True))
        self.assertIn("a flying enemy", s)
        self.assertIn("weight 10.5", s)
        self.assertIn("hover altitudes [50.0, 150.0, 400.0, 0.0]", s)
        self.assertIn("a ground enemy", ddo_params.summary(sample("cpe")))
        self.assertIn("2 regions, 2 break entries", ddo_params.summary(sample("prs")))
        self.assertIn("rAIFSM x1", ddo_params.summary(sample("evtr")))
        self.assertIn("stage 100, 2 adjoin entries, 1 jump positions", ddo_params.summary(sample("sal")))
        self.assertIn("mUnk250=rStartPos", ddo_params.summary(sample("sti")))
        self.assertIn("3 records", ddo_params.summary(sample("osp")))

    @unittest.skipUnless(ddo_found(), "Dragon's Dogma Online not found")
    def test_corpus_byte_exact(self):
        from riftstone import corpus
        from riftstone.game import find_game

        g = find_game("ddo")
        tids = {spec.type_id: ext for ext, spec in KINDS.items()}
        n = {ext: 0 for ext in KINDS}
        fly = 0
        for r in corpus.resources(g, list(tids)):
            ext = tids[r.type_id]
            m = ddo_params.parse(r.data, ext)
            self.assertEqual(ddo_params.build(m), r.data, r.label)
            self.assertEqual(ddo_params.yaml_to_bytes(ddo_params.to_yaml(m, r.name.decode("latin-1"))), r.data,
                             r.label)
            n[ext] += 1
            fly += ext == "cpe" and m.data["cCharParamEnemyFly"] is not None
        self.assertTrue(all(n.values()), n)
        self.assertGreater(fly, 0)


if __name__ == "__main__":
    unittest.main()

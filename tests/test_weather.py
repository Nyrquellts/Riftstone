import os
import struct
import unittest
import zlib

import helpers  # noqa: F401 (sys.path)
from riftstone import typemap, weather
from riftstone.errors import FormatError, ParamError, RiftError
from riftstone.weather import Weather


def fb(v: float) -> int:
    return struct.unpack("<I", struct.pack("<f", v))[0]


def vec(*vals):
    return tuple(fb(v) for v in vals)


def ecp(hour, minute=0, color=0xFFFF9A00):
    return {"mHour": hour, "mMinute": minute, "mColor": color, "mColorBlend": fb(0.2), "mIntensity": fb(0.0),
            "mIntensityBlend": fb(0.98), "mEnvMapPowerScale": fb(0.1), "mShadowColor": color,
            "mShadowColorBlend": fb(0.2), "mShadowIntensity": fb(0.0), "mShadowIntensityBlend": fb(0.98)}


def ecp_ddo(ms):
    r = ecp(0)
    del r["mHour"], r["mMinute"]
    return {"mTime": ms, **r}


def res(cls, path):
    return (cls.encode(), path.encode())


def cmd_base(stages=(100, 103)):
    return {"mUnk04": fb(0.0), "mUnk08": fb(0.0), "mUnk0C": list(stages) + [0] * (8 - len(stages)), "mUnk2C": [0] * 8}


def samples() -> dict[str, Weather]:
    sound = b"sound\\se\\st\\weather\\sunny\\bg_weather_sunny"
    sound_id = typemap.jamcrc("rSoundRequest") << 32 | (~zlib.crc32(sound) & 0xFFFFFFFF)
    info = {"mWeatherId": 1, "mUnk08": fb(200.0), "mUnk0C": fb(1.0), "mUnkB8": [0],
            "mCmds0": {"list0": [], "list1": [], "list2": []},
            "mCmds1": {"list0": [("cWSCSound", {**cmd_base(), "mSoundPath": sound, "mSoundId": sound_id,
                                                "mUnk68": 100, "mUnk6C": 0})],
                       "list1": [("cWSCSoundRnd", {**cmd_base(), "mSoundId": sound_id, "mUnk60": fb(300.0),
                                                   "mUnk64": [1, 5, 10, 15]}),
                                 ("cWSCTimer", cmd_base((7,)))],
                       "list2": [("cWSCEpv", {**cmd_base(), "mEpvId": typemap.jamcrc("rEffectProvider") << 32 | 0x5891B19B,
                                              "mUnk60": 0, "mUnk64": 1}),
                                 ("cWSCSoundVolume", {**cmd_base(), "mUnk4C": fb(0.5)})]}}
    param = {"mWeatherParam": {"mUnk10": vec(23.04, 23.94, 25.6), "mMieScattering": vec(7.55, 7.55, 7.55),
                               "mMieDensity": fb(1.0), "mCloudHeight": fb(3000.0), "mCloudiness": fb(0.25),
                               "mCloudThickness": fb(100.0), "mCloudScattering": fb(0.0), "mCloudEccentricity": fb(0.4),
                               "mEnvMapBaseScale": fb(1.0), "mFogDensity": fb(1.0), "mMoonLuminanceRate": fb(0.9),
                               "mSunIntensityRate": fb(1.0)},
             "mWeatherId": 1, "mUnk74": fb(1.0), "mUnk78": fb(30.0), "mUnk7C": fb(3.0), "mUnk80": fb(1.0),
             "mUnk90": vec(0.21, 0.3, 0.39), "mUnkA0": vec(0.15, 0.3, 0.45), "mUnkB0": vec(0.1, 0.15, 0.2),
             "mFogInfo": res("rWeatherFogInfo", "scr\\sky\\fog_def_f"), "mUnkC4": None,
             "mCloudModel": [{"mModel": res("rModel", "scr\\sky\\sky_fineday"), "mUnk08": 0, "mUnk0C": fb(0.225),
                              "mUnk10": fb(0.2), "mUnk14": fb(1.0), "mUnk18": fb(1.0), "mUnk1C": fb(1.0), "mUnk20": 0}]}
    sky = {name: (vec(1, 2, 3, 0) if t == "vec4" else [vec(0.1, 0.2, 0.3, 0)] * 4 if isinstance(t, tuple) and t[0] == "arr"
                  else b"scr\\sky\\sun" if name == "mpSunTexture" else b"" if isinstance(t, tuple)
                  else 90 if t == "u32" else fb(1.5)) for name, t in weather.SKY}
    return {
        "wep": Weather("wep", {"mParamList": {"CORRECT_TYPE_01": [ecp(2), ecp(19, 51)], "CORRECT_TYPE_04": [],
                                              "CORRECT_TYPE_06": [ecp(6)], "CORRECT_TYPE_07": [], "CORRECT_TYPE_08": [],
                                              "CORRECT_TYPE_09": [ecp(24)]}}),
        "wep-ddo": Weather("wep-ddo", {"mParamList": {f"list{i}": [ecp_ddo(7200000 * i)] * (i % 3) for i in range(7)}}),
        "wfp": Weather("wfp", {"mParamList": [{"mHour": 4, "mMinute": 30, "mDensity": fb(0.9), "mExponentDensity": fb(15.0),
                                              "mStart": fb(1000.0), "mEnd": fb(110000.0), "mColor": vec(0.02, 0.02, 0.017)}]}),
        "sky": Weather("sky", sky),
        "wtf": Weather("wtf", {"mFogInfo": [{"mTime": 14400000, "mStart": fb(1000.0), "mEnd": fb(70000.0),
                                            "mExponentDensity": fb(15.0), "mColor": vec(0.0164, 0.014, 0.026)}]}),
        "wte": Weather("wte", {"mEfcInfo": [{"mWeatherId": 1, "mEffect": res("rWeatherEffectParam", "effect\\wep\\w_cm_f")},
                                           {"mWeatherId": 3, "mEffect": None}]}),
        "wtl": Weather("wtl", {"mParamInfo": [param]}),
        "wsi": Weather("wsi", {"mSky0": res("rSky", "scr\\sky\\st_all_sky_F"), "mSky1": None, "mScheduler": None,
                               "mModel": None, "mTexture": res("rTexture", "scr\\sky\\hoshi2_BM"), "mStarCatalog": None,
                               "mStarSize": fb(4.0), "mStarrySkyIntensity": fb(3.0), "mStarTwinkleAmplitude": fb(0.4),
                               "mEnvMapBaseColor": [vec(0.3, 0.3, 0.3), vec(1, 1, 1)],
                               "mEnvMapBlendColorScale": [fb(0.8), fb(0.05)]}),
        "wta": Weather("wta", {"mWeatherInfo": [info]}),
    }


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


class WeatherTest(unittest.TestCase):
    def test_every_kind_round_trips(self):
        for key, w in samples().items():
            k = weather.KINDS[key]
            raw = weather.build(w)
            back = weather.parse(raw, k.ext)
            self.assertEqual(back, w, key)
            self.assertEqual(weather.build(back), raw, key)
            if k.magic:
                self.assertEqual(raw[:8], k.magic + struct.pack("<I", k.version), key)
                self.assertEqual(weather.parse(raw), back, key)          # found by its magic alone
            else:
                self.assertEqual(struct.unpack_from("<I", raw)[0], k.version, key)
            y = weather.to_yaml(back, "x")
            self.assertIn(f"riftstone: {k.ext}/1", y)
            self.assertEqual(weather.yaml_to_bytes(y), raw, key)
            self.assertIn(k.cls, weather.info(back))

    def test_layouts(self):
        s = samples()
        raw = weather.build(s["wep"])                                     # DDDA: all six counts first
        self.assertEqual(struct.unpack_from("<6I", raw, 8), (2, 0, 1, 0, 0, 1))
        self.assertEqual(len(raw), 8 + 24 + 4 * 44)
        self.assertEqual(struct.unpack_from("<3I", raw, 32), (2, 0, 0xFFFF9A00))
        raw = weather.build(s["wep-ddo"])                                 # DDO: each list's count before it
        self.assertEqual(struct.unpack_from("<2I", raw, 8), (0, 1))
        self.assertEqual(len(raw), 8 + 7 * 4 + sum(i % 3 for i in range(7)) * 40)
        self.assertEqual(len(weather.build(s["wfp"])), 12 + 36)
        raw = weather.build(s["sky"])
        self.assertEqual(len(raw), 8 + 4 * 16 + 4 * 16 + 25 * 4 + len(b"scr\\sky\\sun") + 2)    # 249, as DDDA's
        raw = weather.build(s["wsi"])
        self.assertEqual(raw[8:13], b"rSky\0")
        self.assertEqual(len(raw), 8 + 5 + 21 + 1 + 1 + 1 + 9 + 18 + 1 + 3 * 4 + 24 + 8)

    def test_yaml_forms(self):
        s = samples()
        y = weather.to_yaml(s["wep"])
        self.assertIn("mColor: [0, 154, 255, 255]", y)                    # MtColor bytes r, g, b, a
        self.assertIn("mMinute: 51", y)
        yd = weather.to_yaml(s["wep-ddo"])
        self.assertIn("mTime: 7200000  # 02:00", yd)
        ya = weather.to_yaml(s["wta"])
        self.assertIn("mSoundId: 0x1bcc49668a64f811  # rSoundRequest, path JAMCRC 0x8a64f811", ya)
        self.assertIn("- class: cWSCSoundRnd", ya)
        self.assertIn("mUnkC4: {}", weather.to_yaml(s["wtl"]))
        edited = y.replace("mColor: [0, 154, 255, 255]", "mColor: [255, 0, 0, 128]", 1)
        w = weather.from_yaml(edited)
        self.assertEqual(w.data["mParamList"]["CORRECT_TYPE_01"][0]["mColor"], 0x800000FF)
        fog = weather.to_yaml(s["wfp"]).replace("mEnd: 110000.0", "mEnd: 80000.0")
        self.assertEqual(weather.from_yaml(fog).data["mParamList"][0]["mEnd"], fb(80000.0))

    def test_refusals(self):
        s = samples()
        raw = weather.build(s["wep"])
        for b in (b"", b"wep\0", raw[:4] + struct.pack("<I", 2) + raw[8:], raw + b"\0", raw[:-3],
                  raw[:8] + struct.pack("<I", 0x7FFFFFFF) + raw[12:]):       # a count far past the end
            with self.assertRaises(FormatError):
                weather.parse(b)
        with self.assertRaises(FormatError):
            weather.parse(struct.pack("<II", 3, 0))                         # magic-less: needs the extension
        with self.assertRaises(FormatError):
            weather.parse(struct.pack("<II", 4, 0), "wtf")                  # wrong version
        self.assertEqual(weather.parse(struct.pack("<II", 3, 0), "wtf").data, {"mFogInfo": []})
        sky = weather.build(s["sky"])
        long_path = sky.replace(b"scr\\sky\\sun\0", b"x" * 300 + b"\0")
        with self.assertRaises(FormatError):
            weather.parse(long_path)                                        # longer than the engine keeps
        wsi = weather.build(s["wsi"])
        with self.assertRaises(FormatError):
            weather.parse(wsi.replace(b"rSky\0", b"r" * 70 + b"\0"))
        wta = weather.build(s["wta"])
        bad_class = wta.replace(struct.pack("<I", typemap.jamcrc("cWSCTimer")), struct.pack("<I", 0x12345678))
        with self.assertRaises(FormatError):
            weather.parse(bad_class, "wta")                                 # not a weather-script command
        at = 8 + 2 + 4 + 4                                                   # the first row's mUnkB8 count
        self.assertEqual(struct.unpack_from("<II", wta, at), (1, 0))
        two = wta[:at] + struct.pack("<III", 2, 0, 0) + wta[at + 8:]
        with self.assertRaises(FormatError):
            weather.parse(two, "wta")                                       # the loader has room for one
        w = samples()["wte"]
        w.data["mEfcInfo"][1]["mEffect"] = (b"", b"effect\\x")
        with self.assertRaises(FormatError):
            weather.build(w)
        w = samples()["wep"]
        w.data["mParamList"]["CORRECT_TYPE_01"][0]["mHour"] = -1
        with self.assertRaises(FormatError):
            weather.build(w)
        y = weather.to_yaml(s["wep"])
        for broken in (y.replace("version: 1", "version: 2"), y.replace("riftstone: wep/1", "riftstone: wep/9"),
                       y.replace("mColor: [0, 154, 255, 255]", "mColor: [0, 154, 256, 255]", 1),
                       y.replace("mColor: [0, 154, 255, 255]", "mColor: [0, 154, 255]", 1),
                       y.replace("mMinute: 51", "mMinute: 51\n      mSecond: 3"), y.replace("mMinute: 51\n", "")):
            self.assertNotEqual(broken, y)
            with self.assertRaises(ParamError):
                weather.from_yaml(broken)
        ya = weather.to_yaml(s["wta"])
        with self.assertRaises(ParamError):
            weather.from_yaml(ya.replace("class: cWSCTimer", "class: cWSCRain"))
        with self.assertRaises(ParamError):
            weather.from_yaml(ya.replace("mUnkB8: [0]", "mUnkB8: [0, 1]"))

    def test_yaml_hex_text_has_no_other_field(self):
        # {hex: ...} took its hex and ignored any other field beside it
        y = weather.to_yaml(samples()["sky"])
        self.assertEqual(weather.from_yaml(y.replace('"scr\\\\sky\\\\sun"', '{hex: "41"}')).data["mpSunTexture"], b"A")
        for bad in (y.replace('"scr\\\\sky\\\\sun"', '{hex: "41", text: B}'), y.replace('"scr\\\\sky\\\\sun"', "{hexx: 41}")):
            self.assertNotEqual(bad, y)
            with self.assertRaises(ParamError):
                weather.from_yaml(bad)

    def test_yaml_name_stays_in_its_comment(self):
        # the resource name went into the header comment as it was: a newline in it began a YAML line
        # of its own (here a second 'riftstone:' key, which also fooled params' tag detection)
        from riftstone import params
        for key, w in samples().items():
            raw = weather.build(w)
            y = weather.to_yaml(w, "x\nriftstone: xfs/1\r\t\"#")
            self.assertEqual(weather.yaml_to_bytes(y), raw, key)
            self.assertEqual(params.yaml_to_resource(y), raw, key)

    def test_yaml_long_hex_number(self):
        # base 16 has no digit limit, but 3,572 hex digits are over 4,300 decimal ones: the range message
        # printed the number and leaked int -> str's ValueError
        big = "0x" + "f" * 3572
        y = weather.to_yaml(samples()["wep"])
        for bad in (y.replace("version: 1", "version: " + big), y.replace("mMinute: 51", "mMinute: -" + big),
                    y.replace("mColor: [0, 154, 255, 255]", "mColor: [0, 154, " + big + ", 255]", 1)):
            self.assertNotEqual(bad, y)
            with self.assertRaises(ParamError) as cm:
                weather.from_yaml(bad)
            self.assertLess(len(str(cm.exception)), 200)

    def test_kind_detection(self):
        s = samples()
        for key in ("wep", "wep-ddo", "wfp", "sky", "wsi"):
            self.assertEqual(weather.kind_of(weather.build(s[key])), key)
        self.assertIsNone(weather.kind_of(weather.build(s["wtf"])))
        self.assertEqual(weather.kind_of(weather.build(s["wtf"]), "wtf"), "wtf")
        for ext in weather.EXTS:
            self.assertIn(ext, typemap.BY_EXT, ext)                         # both games' type tables name them

    def _corpus(self, kind: str, exts: tuple, want: dict):
        from riftstone import corpus
        from riftstone.game import find_game
        ids = {typemap.BY_EXT[e]: e for e in exts}
        seen = dict.fromkeys(exts, 0)
        for r in corpus.resources(find_game(kind), list(ids)):
            ext = ids[r.type_id]
            w = weather.parse(r.data, ext)
            self.assertEqual(weather.build(w), r.data, r.label)
            y = weather.to_yaml(w, r.name.decode("latin-1"))
            self.assertEqual(weather.yaml_to_bytes(y, r.label), r.data, r.label)
            seen[ext] += 1
        for ext, n in want.items():
            self.assertGreaterEqual(seen[ext], n, ext)

    @unittest.skipUnless(game_found("ddda"), "Dragon's Dogma: Dark Arisen not found")
    def test_corpus_ddda(self):
        self._corpus("ddda", ("wep", "wfp", "sky"), {"wep": 57, "wfp": 12, "sky": 2})

    @unittest.skipUnless(game_found("ddo"), "Dragon's Dogma Online not found")
    def test_corpus_ddo(self):
        self._corpus("ddo", ("wep", "sky", "wtf", "wte", "wtl", "wsi", "wta"),
                     {"wep": 83, "sky": 1, "wtf": 25, "wte": 19, "wtl": 15, "wsi": 7, "wta": 1})


if __name__ == "__main__":
    unittest.main()

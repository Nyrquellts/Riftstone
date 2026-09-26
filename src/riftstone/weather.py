"""Weather, fog and sky resources of both games, byte-exact, with YAML: time-of-day effect colours, fog
distances and colours, the physical sky, and Dragon's Dogma Online's weather tables.

Each format is a schema (``KINDS``) run by one small engine; every grammar was read from the game's
own loader (x86 DDDA.exe / DDO.exe, cross-checked with the PS3 build where it has one):

``wep``  rWeatherEffectParam, "wep\\0".  DDDA version 1 (``load`` 0x00CEA2B0, PS3 0x00B263A0): u32
         count[6], then six lists of cEffectCorrectParam (mpParamList[6] indexed by
         CORRECT_TYPE_01/04/06/07/08/09), 44 bytes each: mHour, mMinute, mColor (MtColor, bytes r g b
         a), mColorBlend, mIntensity, mIntensityBlend, mEnvMapPowerScale, mShadowColor,
         mShadowColorBlend, mShadowIntensity, mShadowIntensityBlend.  Dragon's Dogma Online version 3
         (0x00B0D3B0): seven lists, each its u32 count then its records, 40 bytes: the hour/minute pair
         is one u32 ``mTime``, milliseconds since midnight (every value is a whole minute, 0..23:59),
         the rest as DDDA (DDO.exe registers no names for it: they are DDDA's, by position).  The
         effect code looks a list up by type and blends the rows by time of day.
``wfp``  rWeatherFogParam, "wfp\\0" version 1, DDDA (0x00CEAA80, PS3 0x00B27214): u32 count, then
         cFogCorrectParam rows in the loader's order mHour, mMinute, mDensity, mExponentDensity,
         mStart, mEnd (fog distances), mColor (x y z).  sWeatherManager holds one per weather
         (setFogResource_F/E/R/T/G: fine, cloudy, very cloudy, darkness, G).
``sky``  rSky, "SKY " version 6, both games (DDDA 0x00E2C350, DDO 0x016C3D60 -- the same reads;
         DDO's object is 16 bytes longer before them).  Names from the PS3 build (sBbsRpgMain.cpp):
         the atmosphere and sun/moon model -- wavelengths ``mRamda`` (665/555/455 nm), Earth radius
         6,367, sun radius/distance 1,392,000 / 149,597,870, moon 1,737 / 384,400, axial tilt
         ``mObliquity`` 23.4, the sun and moon texture paths.  MtVector3 fields are 16 bytes on disk
         (x, y, z and the pad, kept).
DDO tables (DDO.exe's shared loader: u32 version == the class's own, then its body):
``wtf``  rWeatherFogInfo v3: u32 count, rows of mTime (ms), mStart, mEnd, mExponentDensity, mColor.
         DDO.exe registers no names; these follow DDDA's cFogCorrectParam and the order of DDO's
         cCustomWeatherParam properties FogStart, FogEnd, FogExponentDensity, FogColor (values
         1000 / 70000 / 15 match DDDA's start / end / exponent density).
``wte``  rWeatherParamEfcInfo v1: u32 count, rows of mWeatherId (s32; 1..3) and the weather effect
         (a resource reference to an rWeatherEffectParam).
``wtl``  rWeatherParamInfoTbl v12: u32 count, rows of cWeatherParam (named from DDO's cDarkSkyParam
         properties mWTBD.mWP.*, matched by offset: mMieScattering, mMieDensity, mCloudHeight,
         mCloudiness, mCloudThickness, mCloudScattering, mCloudEccentricity, mEnvMapBaseScale,
         mFogDensity, the moon luminance rate (Japanese name in the exe), mSunIntensityRate; the first
         vector is unnamed), mWeatherId, four floats and three vectors (UNKNOWN), the fog info
         (rWeatherFogInfo), a second reference (empty in every file), then a list of
         cWeatherCloudModel (an rModel reference and seven numbers, UNKNOWN).
``wsi``  rWeatherStageInfo, "WSI_" version 7 (0x00B0F970): six resource references (rSky, rSky,
         rScheduler, rModel, rTexture, rStarCatalog in the files) and the star/env-map settings
         (names from DDO.exe's properties).
``wta``  rWeatherInfoTbl v17: u32 count, rows of u16 mWeatherId, two floats, a u32 list of at most one
         value (the loader copies it into a 4-byte field), and two command sets (cWeatherScriptCmds,
         three lists each) of weather-script commands: each a u32 class id (the DTI hash) and that
         class's fields -- cWSCSound (a sound path), cWSCSoundRnd, cWSCSoundVolume, cWSCEpv, cWSCTimer
         (readers 0x00B11060 / 0x00B11110 / 0x00B11160 / 0x00B11020 / 0x00B11180).  mSoundId / mEpvId
         are hashed references: (class DTI id << 32) | JAMCRC(path) -- all 54 cWSCSound ids are
         rSoundRequest's id and the JAMCRC of their own mSoundPath; cWSCEpv ids name rEffectProvider.

A resource reference is a class name (the engine keeps 63 bytes) and, when that is not empty, a
path; strings are NUL-terminated and longer ones than the engine's buffer are refused (it would cut
them).  Floats travel as their exact 32-bit patterns.

Proof: every distinct resource of both games parses, rebuilds byte-for-byte, and rebuilds from its
YAML byte-for-byte: DDDA wep 57, wfp 12, sky 2; DDO wep 83, sky 1, wtf 25, wte 19, wtl 15, wsi 7,
wta 1 (``tests/test_weather.py``, scratch ``weather_camera/prove.py``); 222,000 mutations of those
files were each refused with FormatError or rebuilt exactly.

UNKNOWN: what CORRECT_TYPE_01..09 (and DDO's seven lists) select; the wtl/wta/cloud-model/script
fields named mUnkXX (a command's mUnk0C holds up to eight numbers, 27 of whose 29 distinct values are
stage numbers of the client -- likely the stages it applies to, not confirmed in code).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from .errors import FormatError, ParamError

_S = {"u16": struct.Struct("<H"), "s32": struct.Struct("<i"), "u32": struct.Struct("<I"),
      "u64": struct.Struct("<Q"), "f32": struct.Struct("<I"), "col": struct.Struct("<I"), "rid": struct.Struct("<Q")}
_RANGE = {"u16": (0, 0xFFFF), "s32": (-(1 << 31), (1 << 31) - 1), "u32": (0, 0xFFFFFFFF),
          "u64": (0, (1 << 64) - 1), "rid": (0, (1 << 64) - 1)}
RES_BUF = 0x40                                  # the engine's buffer for class names and paths


def _rec(*fields):
    return ("rec", list(fields))


# -- schemas -----------------------------------------------------------------------------------
CORRECT_TYPES = ("CORRECT_TYPE_01", "CORRECT_TYPE_04", "CORRECT_TYPE_06", "CORRECT_TYPE_07",
                 "CORRECT_TYPE_08", "CORRECT_TYPE_09")          # rWeatherEffectParam::CORRECT_TYPE (PS3 build)
_ECP_TAIL = [("mColor", "col"), ("mColorBlend", "f32"), ("mIntensity", "f32"), ("mIntensityBlend", "f32"),
             ("mEnvMapPowerScale", "f32"), ("mShadowColor", "col"), ("mShadowColorBlend", "f32"),
             ("mShadowIntensity", "f32"), ("mShadowIntensityBlend", "f32")]
ECP = _rec(("mHour", "u32"), ("mMinute", "u32"), *_ECP_TAIL)           # cEffectCorrectParam, 44 bytes
ECP_DDO = _rec(("mTime", "u32"), *_ECP_TAIL)                          # DDO, 40 bytes
FCP = _rec(("mHour", "u32"), ("mMinute", "u32"), ("mDensity", "f32"), ("mExponentDensity", "f32"),
           ("mStart", "f32"), ("mEnd", "f32"), ("mColor", "vec3"))      # cFogCorrectParam, disk order
SKY = [("mSunColor", "vec4"), ("mRayleighScatteringBase", "f32"), ("mRamda", "vec4"),
       ("mStarryAmbientColor", "vec4"), ("mObliquity", "f32"), ("mSpinDirection", "u32"),
       ("mEclipticLongitude", "u32"), ("mLatitude", "f32"), ("mSunMultiplier", "f32"), ("mEarthRadius", "f32"),
       ("mAtmosphereHeight", "f32"), ("mAtmosphereAverageDensityHeight", "f32"), ("mAerosolHeight", "f32"),
       ("mAerosolAverageDensityHeight", "f32"), ("mSecondaryScattering", "f32"), ("mSunBodySize", "f32"),
       ("mSunRadius", "f32"), ("mSunDistance", "f32"), ("mpSunTexture", ("str", 0x103)),
       ("mStarrySkyColor", ("arr", "vec4", 4)), ("mStarrySkyColorT1", "f32"), ("mStarrySkyColorT2", "f32"),
       ("mMoonAge", "f32"), ("mMoonFluctuationAmplitude", "f32"), ("mMoonFluctuationPhase", "f32"),
       ("mMoonColor", "vec4"), ("mMoonMultiplier", "f32"), ("mMoonBodySize", "f32"), ("mMoonRadius", "f32"),
       ("mMoonDistance", "f32"), ("mEarthShine", "f32"), ("mpMoonTexture", ("str", 0x103))]
WEATHER_PARAM = _rec(("mUnk10", "vec3"), ("mMieScattering", "vec3"), ("mMieDensity", "f32"),
                     ("mCloudHeight", "f32"), ("mCloudiness", "f32"), ("mCloudThickness", "f32"),
                     ("mCloudScattering", "f32"), ("mCloudEccentricity", "f32"), ("mEnvMapBaseScale", "f32"),
                     ("mFogDensity", "f32"), ("mMoonLuminanceRate", "f32"), ("mSunIntensityRate", "f32"))
CLOUD_MODEL = _rec(("mModel", "res"), ("mUnk08", "u32"), ("mUnk0C", "f32"), ("mUnk10", "f32"), ("mUnk14", "f32"),
                   ("mUnk18", "f32"), ("mUnk1C", "f32"), ("mUnk20", "u32"))
PARAM_INFO = _rec(("mWeatherParam", WEATHER_PARAM), ("mWeatherId", "s32"), ("mUnk74", "f32"), ("mUnk78", "f32"),
                  ("mUnk7C", "f32"), ("mUnk80", "f32"), ("mUnk90", "vec3"), ("mUnkA0", "vec3"), ("mUnkB0", "vec3"),
                  ("mFogInfo", "res"), ("mUnkC4", "res"), ("mCloudModel", ("list", CLOUD_MODEL)))
_CMD_BASE = [("mUnk04", "f32"), ("mUnk08", "f32"), ("mUnk0C", ("arr", "u32", 8)), ("mUnk2C", ("arr", "u32", 8))]
# weather-script command classes the wta loader instantiates by DTI id, with their readers' fields
COMMANDS: dict[str, list] = {
    "cWeatherScriptCmd": _CMD_BASE,
    "cWSCTimer": _CMD_BASE,
    "cWSCSoundVolume": _CMD_BASE + [("mUnk4C", "f32")],
    "cWSCEpv": _CMD_BASE + [("mEpvId", "rid"), ("mUnk60", "s32"), ("mUnk64", "s32")],
    "cWSCSound": _CMD_BASE + [("mSoundPath", ("str", 0x100)), ("mSoundId", "rid"), ("mUnk68", "u32"),
                              ("mUnk6C", "u32")],
    "cWSCSoundRnd": _CMD_BASE + [("mSoundId", "rid"), ("mUnk60", "f32"), ("mUnk64", ("arr", "u32", 4))],
}
SCRIPT_CMDS = _rec(("list0", ("cmds",)), ("list1", ("cmds",)), ("list2", ("cmds",)))
WEATHER_INFO = _rec(("mWeatherId", "u16"), ("mUnk08", "f32"), ("mUnk0C", "f32"), ("mUnkB8", ("maxlist", "u32", 1)),
                    ("mCmds0", SCRIPT_CMDS), ("mCmds1", SCRIPT_CMDS))


@dataclass(frozen=True)
class Kind:
    key: str
    ext: str
    cls: str
    game: str
    magic: bytes | None
    version: int
    fields: tuple


KINDS: dict[str, Kind] = {k.key: k for k in (
    Kind("wep", "wep", "rWeatherEffectParam", "ddda", b"wep\0", 1, (("mParamList", ("first", CORRECT_TYPES, ECP)),)),
    Kind("wep-ddo", "wep", "rWeatherEffectParam", "ddo", b"wep\0", 3,
         (("mParamList", ("each", tuple(f"list{i}" for i in range(7)), ECP_DDO)),)),
    Kind("wfp", "wfp", "rWeatherFogParam", "ddda", b"wfp\0", 1, (("mParamList", ("list", FCP)),)),
    Kind("sky", "sky", "rSky", "both", b"SKY ", 6, tuple(SKY)),
    Kind("wtf", "wtf", "rWeatherFogInfo", "ddo", None, 3,
         (("mFogInfo", ("list", _rec(("mTime", "u32"), ("mStart", "f32"), ("mEnd", "f32"),
                                     ("mExponentDensity", "f32"), ("mColor", "vec3")))),)),
    Kind("wte", "wte", "rWeatherParamEfcInfo", "ddo", None, 1,
         (("mEfcInfo", ("list", _rec(("mWeatherId", "s32"), ("mEffect", "res")))),)),
    Kind("wtl", "wtl", "rWeatherParamInfoTbl", "ddo", None, 12, (("mParamInfo", ("list", PARAM_INFO)),)),
    Kind("wsi", "wsi", "rWeatherStageInfo", "ddo", b"WSI_", 7,
         (("mSky0", "res"), ("mSky1", "res"), ("mScheduler", "res"), ("mModel", "res"), ("mTexture", "res"),
          ("mStarCatalog", "res"), ("mStarSize", "f32"), ("mStarrySkyIntensity", "f32"),
          ("mStarTwinkleAmplitude", "f32"), ("mEnvMapBaseColor", ("arr", "vec3", 2)),
          ("mEnvMapBlendColorScale", ("arr", "f32", 2)))),
    Kind("wta", "wta", "rWeatherInfoTbl", "ddo", None, 17, (("mWeatherInfo", ("list", WEATHER_INFO)),)),
)}
EXTS = tuple(dict.fromkeys(k.ext for k in KINDS.values()))
BY_MAGIC = {(k.magic, k.version): k.key for k in KINDS.values() if k.magic}


def _jam(name: str) -> int:
    from .typemap import jamcrc
    return jamcrc(name)


_CMD_BY_ID = {_jam(n): n for n in COMMANDS}


@dataclass
class Weather:
    kind: str
    data: dict


# -- reading ------------------------------------------------------------------------------------
class _R:
    def __init__(self, data: bytes, fmt: str):
        self.d = data
        self.p = 0
        self.fmt = fmt

    def need(self, n: int, what: str) -> None:
        if self.p + n > len(self.d):
            raise FormatError(self.fmt, f"{what}: the file ends", self.p)

    def num(self, t: str, what: str) -> int:
        st = _S[t]
        self.need(st.size, what)
        v = st.unpack_from(self.d, self.p)[0]
        self.p += st.size
        return v

    def cstr(self, buf: int, what: str) -> bytes:
        e = self.d.find(b"\0", self.p)
        if e < 0:
            raise FormatError(self.fmt, f"{what}: a string is not terminated", self.p)
        if e - self.p > buf - 1:
            raise FormatError(self.fmt, f"{what}: a {e - self.p}-byte string; the game keeps {buf - 1}", self.p)
        s = self.d[self.p:e]
        self.p = e + 1
        return s

    def count(self, elem, what: str, limit: int | None = None) -> int:
        n = self.num("u32", f"{what} count")
        if limit is not None and n > limit:
            raise FormatError(self.fmt, f"{what}: {n} values; the game has room for {limit}", self.p - 4)
        if n * _min(elem) > len(self.d) - self.p:
            raise FormatError(self.fmt, f"{what}: {n} entries do not fit in the file", self.p - 4)
        return n


def _min(t) -> int:
    """The fewest bytes a value of this type can take (bounds counts by the bytes left)."""
    if isinstance(t, str):
        return {"vec3": 12, "vec4": 16, "res": 1}.get(t, _S[t].size if t in _S else 1)
    k = t[0]
    if k == "str":
        return 1
    if k == "arr":
        return _min(t[1]) * t[2]
    if k in ("list", "maxlist", "cmds"):
        return 4
    if k == "rec":
        return sum(_min(ft) for _, ft in t[1])
    if k == "first":
        return 4 * len(t[1])
    if k == "each":
        return 4 * len(t[1])
    raise AssertionError(t)


def _read(r: _R, t, what: str):
    if isinstance(t, str):
        if t in ("vec3", "vec4"):
            n = 3 if t == "vec3" else 4
            r.need(4 * n, what)
            v = struct.unpack_from(f"<{n}I", r.d, r.p)
            r.p += 4 * n
            return v
        if t == "res":
            cls = r.cstr(RES_BUF, f"{what} class")
            return None if not cls else (cls, r.cstr(RES_BUF, f"{what} path"))
        return r.num(t, what)
    k = t[0]
    if k == "str":
        return r.cstr(t[1], what)
    if k == "arr":
        return [_read(r, t[1], f"{what}[{i}]") for i in range(t[2])]
    if k in ("list", "maxlist"):
        n = r.count(t[1], what, t[2] if k == "maxlist" else None)
        return [_read(r, t[1], f"{what}[{i}]") for i in range(n)]
    if k == "rec":
        return {name: _read(r, ft, f"{what}.{name}") for name, ft in t[1]}
    if k == "first":                                # all counts, then all lists (DDDA wep)
        ns = [r.count(t[2], f"{what}.{name}") for name in t[1]]
        return {name: [_read(r, t[2], f"{what}.{name}[{i}]") for i in range(n)] for name, n in zip(t[1], ns)}
    if k == "each":                                 # each list's count right before it (DDO wep)
        out = {}
        for name in t[1]:
            n = r.count(t[2], f"{what}.{name}")
            out[name] = [_read(r, t[2], f"{what}.{name}[{i}]") for i in range(n)]
        return out
    if k == "cmds":
        n = r.count("u32", what)
        out = []
        for i in range(n):
            cid = r.num("u32", f"{what}[{i}] class id")
            cname = _CMD_BY_ID.get(cid)
            if cname is None:
                raise FormatError(r.fmt, f"{what}[{i}]: class id 0x{cid:08x} is not a weather-script command", r.p - 4)
            out.append((cname, {nm: _read(r, ft, f"{what}[{i}].{nm}") for nm, ft in COMMANDS[cname]}))
        return out
    raise AssertionError(t)


# -- writing ------------------------------------------------------------------------------------
def _bad(fmt: str, what: str, v, t) -> FormatError:
    return FormatError(fmt, f"{what}: {v!r} is not a valid {t}")


def _int(fmt, v, t, what):
    lo, hi = _RANGE.get(t, (0, 0xFFFFFFFF))
    if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
        raise _bad(fmt, what, v, t)
    return v


def _bytes(fmt, v, buf, what) -> bytes:
    if isinstance(v, str):
        try:
            v = v.encode("cp932")
        except UnicodeError:
            raise FormatError(fmt, f"{what}: the text has characters the game's encoding (Shift-JIS) lacks") from None
    if not isinstance(v, (bytes, bytearray)):
        raise _bad(fmt, what, v, "string")
    if b"\0" in v:
        raise FormatError(fmt, f"{what}: a NUL inside a string")
    if len(v) > buf - 1:
        raise FormatError(fmt, f"{what}: {len(v)} bytes; the game keeps {buf - 1}")
    return bytes(v)


def _write(out: bytearray, t, v, fmt: str, what: str) -> None:
    if isinstance(t, str):
        if t in ("vec3", "vec4"):
            n = 3 if t == "vec3" else 4
            if not isinstance(v, (tuple, list)) or len(v) != n:
                raise _bad(fmt, what, v, f"{n}-float vector")
            out += struct.pack(f"<{n}I", *(_int(fmt, x, "u32", what) for x in v))
        elif t == "res":
            if v is None:
                out += b"\0"
            else:
                if not isinstance(v, (tuple, list)) or len(v) != 2:
                    raise _bad(fmt, what, v, "resource reference (class, path)")
                cls = _bytes(fmt, v[0], RES_BUF, f"{what} class")
                if not cls:
                    raise FormatError(fmt, f"{what}: a reference with a path needs a class name")
                out += cls + b"\0" + _bytes(fmt, v[1], RES_BUF, f"{what} path") + b"\0"
        else:
            out += _S[t].pack(_int(fmt, v, "u32" if t in ("f32", "col") else t, what))
        return
    k = t[0]
    if k == "str":
        out += _bytes(fmt, v, t[1], what) + b"\0"
    elif k == "arr":
        if not isinstance(v, (tuple, list)) or len(v) != t[2]:
            raise FormatError(fmt, f"{what} holds {t[2]} values")
        for i, x in enumerate(v):
            _write(out, t[1], x, fmt, f"{what}[{i}]")
    elif k in ("list", "maxlist"):
        if not isinstance(v, (tuple, list)):
            raise _bad(fmt, what, v, "list")
        if k == "maxlist" and len(v) > t[2]:
            raise FormatError(fmt, f"{what}: {len(v)} values; the game has room for {t[2]}")
        out += _S["u32"].pack(len(v))
        for i, x in enumerate(v):
            _write(out, t[1], x, fmt, f"{what}[{i}]")
    elif k == "rec":
        if not isinstance(v, dict):
            raise _bad(fmt, what, v, "record")
        for name, ft in t[1]:
            if name not in v:
                raise FormatError(fmt, f"{what}: '{name}' is missing")
            _write(out, ft, v[name], fmt, f"{what}.{name}")
    elif k in ("first", "each"):
        if not isinstance(v, dict) or any(not isinstance(v.get(n), (list, tuple)) for n in t[1]):
            raise FormatError(fmt, f"{what}: needs the lists {', '.join(t[1])}")
        if k == "first":
            for name in t[1]:
                out += _S["u32"].pack(len(v[name]))
        for name in t[1]:
            if k == "each":
                out += _S["u32"].pack(len(v[name]))
            for i, x in enumerate(v[name]):
                _write(out, t[2], x, fmt, f"{what}.{name}[{i}]")
    elif k == "cmds":
        if not isinstance(v, (tuple, list)):
            raise _bad(fmt, what, v, "command list")
        out += _S["u32"].pack(len(v))
        for i, cmd in enumerate(v):
            if not isinstance(cmd, (tuple, list)) or len(cmd) != 2 or cmd[0] not in COMMANDS:
                raise FormatError(fmt, f"{what}[{i}]: a command is (class, fields) with class one of {', '.join(COMMANDS)}")
            out += _S["u32"].pack(_jam(cmd[0]))
            _write(out, _rec(*COMMANDS[cmd[0]]), cmd[1], fmt, f"{what}[{i}]")
    else:
        raise AssertionError(t)


# -- parse / build ------------------------------------------------------------------------------
def kind_of(data: bytes, ext: str | None = None) -> str | None:
    """The format key for these bytes: by magic and version, or for the magic-less DDO tables by the
    extension (type) they came with."""
    head = bytes(data[:8])
    if len(head) == 8:
        key = BY_MAGIC.get((head[:4], _S["u32"].unpack_from(head, 4)[0]))
        if key:
            return key
    if ext is not None:
        e = ext.lower().lstrip(".")
        for k in KINDS.values():
            if k.ext == e and k.magic is None:
                return k.key
    return None


def parse(data: bytes, ext: str | None = None) -> Weather:
    """Parse a weather/fog/sky resource; ``ext`` (wtf, wte, wtl, wta) is needed for the magic-less DDO tables."""
    data = bytes(data)
    key = kind_of(data, ext)
    if key is None:
        same = [k for k in KINDS.values() if k.magic and k.magic == data[:4]]
        if same and len(data) >= 8:
            raise FormatError(same[0].ext, f"version {_S['u32'].unpack_from(data, 4)[0]}; {same[0].cls} files are "
                              + " or ".join(f"version {k.version} ({k.game})" for k in same), 4)
        if ext is not None and ext.lower().lstrip(".") in EXTS:
            e = ext.lower().lstrip(".")
            want = [k for k in KINDS.values() if k.ext == e]
            raise FormatError(e, "not a " + " / ".join(f"{k.cls} version {k.version} ({k.game})" for k in want)
                              + f" file (starts {data[:8].hex()})", 0)
        raise FormatError("weather", f"not a weather, fog or sky resource (starts {data[:8].hex()})", 0)
    k = KINDS[key]
    r = _R(data, k.ext)
    if k.magic:
        r.p = 8
    else:
        v = r.num("u32", "version")
        if v != k.version:
            raise FormatError(k.ext, f"version {v}; {k.cls} files are version {k.version}", 0)
    out = {name: _read(r, t, name) for name, t in k.fields}
    if r.p != len(data):
        raise FormatError(k.ext, f"{len(data) - r.p} byte(s) after the last field", r.p)
    return Weather(key, out)


def build(w: Weather) -> bytes:
    k = KINDS.get(w.kind)
    if k is None:
        raise FormatError("weather", f"unknown kind {w.kind!r} (one of {', '.join(KINDS)})")
    out = bytearray((k.magic or b"") + _S["u32"].pack(k.version))
    if not isinstance(w.data, dict):
        raise FormatError(k.ext, "the data is a record of fields")
    for name, t in k.fields:
        if name not in w.data:
            raise FormatError(k.ext, f"'{name}' is missing")
        _write(out, t, w.data[name], k.ext, name)
    return bytes(out)


def info(w: Weather) -> str:
    k = KINDS[w.kind]
    game = {"ddda": "Dragon's Dogma: Dark Arisen", "ddo": "Dragon's Dogma Online", "both": "both games"}[k.game]
    parts = []
    for name, t in k.fields:
        v = w.data[name]
        if isinstance(t, tuple) and t[0] in ("first", "each"):
            parts.append("rows per list " + ", ".join(str(len(v[n])) for n in t[1]))
        elif isinstance(t, tuple) and t[0] == "list":
            parts.append(f"{len(v)} row(s)")
    if w.kind == "sky":
        parts.append(f"sun texture {_shown(w.data['mpSunTexture']) or '(none)'}, "
                     f"moon texture {_shown(w.data['mpMoonTexture']) or '(none)'}")
    return f"{k.cls} version {k.version} ({game})" + (": " + "; ".join(parts) if parts else "")


def _shown(b) -> str:
    try:
        return b.decode("cp932")
    except UnicodeError:
        return "0x" + b.hex()


# -- YAML ---------------------------------------------------------------------------------------
def _ftext(bits: int) -> str:
    from .params import f32_bits_text
    return f32_bits_text(bits)


def _text_node(b: bytes):
    from .yamlish import Map, Scalar
    try:
        s = b.decode("cp932")
        if s.encode("cp932") == b:
            return Scalar(s, "double")
    except UnicodeError:
        pass
    return Map([(Scalar("hex"), Scalar(b.hex(), "double"))], flow=True)


def _hhmm(ms: int) -> str | None:
    if ms % 60000:
        return None
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}"


def _node(t, v, name: str | None = None):
    from .yamlish import Map, Scalar, Seq
    if isinstance(t, str):
        if t in ("vec3", "vec4"):
            return Seq([Scalar(_ftext(x)) for x in v], flow=True)
        if t == "f32":
            return Scalar(_ftext(v))
        if t == "col":
            return Seq([Scalar(str((v >> s) & 0xFF)) for s in (0, 8, 16, 24)], flow=True)
        if t == "res":
            if v is None:
                return Map([], flow=True)
            return Map([(Scalar("class"), _text_node(v[0])), (Scalar("path"), _text_node(v[1]))], flow=True)
        if t == "rid":
            from .typemap import BY_ID
            cls = BY_ID.get(v >> 32, (None,))[0]
            return Scalar(f"0x{v:016x}", comment=f"{cls}, path JAMCRC 0x{v & 0xFFFFFFFF:08x}" if cls else None)
        return Scalar(str(v), comment=_hhmm(v) if name == "mTime" else None)
    k = t[0]
    if k == "str":
        return _text_node(v)
    if k in ("arr", "list", "maxlist"):
        items = [_node(t[1], x) for x in v]
        scalar = isinstance(t[1], str) and t[1] not in ("vec3", "vec4", "res")
        return Seq(items, flow=scalar or not items)
    if k == "rec":
        return Map([(Scalar(nm), _node(ft, v[nm], nm)) for nm, ft in t[1]])
    if k in ("first", "each"):
        return Map([(Scalar(nm), Seq([_node(t[2], x) for x in v[nm]], flow=not v[nm])) for nm in t[1]])
    if k == "cmds":
        rows = []
        for cname, fields in v:
            rows.append(Map([(Scalar("class"), Scalar(cname))]
                            + [(Scalar(nm), _node(ft, fields[nm], nm)) for nm, ft in COMMANDS[cname]]))
        return Seq(rows, flow=not rows)
    raise AssertionError(t)


_HEAD_NOTES = {
    "wep": ["Effect colour correction by time of day: one list per CORRECT_TYPE, rows blended by mHour/mMinute.",
            "Colours are [r, g, b, a] bytes; *Blend, intensities and scales are floats."],
    "wep-ddo": ["Effect colour correction by time of day (Dragon's Dogma Online): seven lists; mTime is",
                "milliseconds since midnight (the comment shows HH:MM). Colours are [r, g, b, a] bytes."],
    "wfp": ["Fog by time of day: mStart/mEnd are the fog distances, mDensity and mExponentDensity its",
            "strength, mColor [r, g, b] floats. Rows are blended by mHour/mMinute."],
    "sky": ["The physical sky: atmosphere, sun and moon (engine names). Vectors are [x, y, z, pad]."],
    "wtf": ["Fog by time of day (Dragon's Dogma Online): mTime in milliseconds (comment HH:MM), fog",
            "distances mStart/mEnd, mExponentDensity, mColor [r, g, b]."],
    "wte": ["Which weather effect (rWeatherEffectParam) each weather id uses. {} = none."],
    "wtl": ["Weather parameter sets (clouds, scattering, fog, the fog info and cloud models) per weather id."],
    "wsi": ["The stage's sky, scheduler, star model/texture/catalog and star settings. {} = none."],
    "wta": ["The weather table: per weather id, two sets of weather-script command lists (sounds,",
            "effects, timers). Each command names its class; keep a command's fields for its class."],
}


def to_yaml(w: Weather, name: str | None = None) -> str:
    from . import yamlish
    from .yamlish import Map, Scalar
    k = KINDS[w.kind]
    head = [f"Riftstone {k.cls} (.{k.ext}, version {k.version})" + (f" -- {name}" if name else "")]
    head += _HEAD_NOTES.get(w.kind, [])
    head.append("Floats are exact; rebuilds byte-for-byte when untouched.")
    items = [(Scalar("riftstone"), Scalar(f"{k.ext}/1"))]
    if name:
        items.append((Scalar("resource"), Scalar(name, "double")))
    items.append((Scalar("version"), Scalar(str(k.version))))
    for nm, t in k.fields:
        items.append((Scalar(nm), _node(t, w.data[nm], nm)))
    return yamlish.emit(Map(items), head)


class _Y:
    def __init__(self, source):
        self.source = source

    def err(self, msg, node=None):
        return ParamError(msg, getattr(node, "line", None), getattr(node, "col", None), self.source)

    def field(self, m, key, what):
        from .yamlish import Map
        if not isinstance(m, Map):
            raise self.err(f"{what} must be a block of fields", m)
        v = m.get(key)
        if v is None:
            raise self.err(f"{what}: '{key}' is missing", m)
        return v

    def scalar(self, node, what):
        from .yamlish import Scalar
        if not isinstance(node, Scalar):
            raise self.err(f"{what} must be a single value", node)
        return node.text

    def int(self, node, t, what):
        txt = self.scalar(node, what).strip()
        try:
            v = int(txt, 0)
        except ValueError:
            raise self.err(f"{what}: {txt!r} is not a whole number", node) from None
        lo, hi = _RANGE[t]
        if not lo <= v <= hi:
            raise self.err(f"{what}: {v} is outside {lo}..{hi}", node)
        return v

    def f32(self, node, what):
        from .params import f32_bits
        txt = self.scalar(node, what)
        try:
            return f32_bits(txt)
        except ValueError:
            raise self.err(f"{what}: {txt!r} is not a 32-bit float", node) from None

    def seq(self, node, n, what):
        from .yamlish import Seq
        if not isinstance(node, Seq) or (n is not None and len(node.items) != n):
            raise self.err(f"{what} is a list of {n} values" if n is not None else f"{what} is a list", node)
        return node.items

    def text(self, node, what):
        from .yamlish import Map, Scalar
        if isinstance(node, Map):
            h = node.get("hex")
            try:
                return bytes.fromhex(h.text)
            except (AttributeError, ValueError):
                raise self.err(f"{what}: hex must be pairs of hex digits", node) from None
        if not isinstance(node, Scalar):
            raise self.err(f"{what} must be text", node)
        try:
            return node.text.encode("cp932")
        except UnicodeError:
            raise self.err(f"{what}: the text has characters the game's encoding (Shift-JIS) lacks", node) from None

    def keys(self, m, allowed, what):
        from .yamlish import Map
        if isinstance(m, Map):
            for key, _ in m.items:
                if key.text not in allowed:
                    raise self.err(f"{what}: unexpected field '{key.text}'", key)


def _from(y: _Y, t, node, what: str):
    from .yamlish import Map
    if isinstance(t, str):
        if t in ("vec3", "vec4"):
            n = 3 if t == "vec3" else 4
            return tuple(y.f32(x, what) for x in y.seq(node, n, what))
        if t == "f32":
            return y.f32(node, what)
        if t == "col":
            b = [y.int(x, "u16", what) for x in y.seq(node, 4, f"{what} ([r, g, b, a])")]
            if any(c > 255 for c in b):
                raise y.err(f"{what}: colour bytes are 0..255", node)
            return b[0] | b[1] << 8 | b[2] << 16 | b[3] << 24
        if t == "res":
            if not isinstance(node, Map):
                raise y.err(f"{what} is {{}} or {{class: ..., path: ...}}", node)
            if not node.items:
                return None
            y.keys(node, ("class", "path"), what)
            cls = y.text(y.field(node, "class", what), f"{what} class")
            if not cls:
                raise y.err(f"{what}: an empty class means no reference: write {{}}", node)
            return (cls, y.text(y.field(node, "path", what), f"{what} path"))
        return y.int(node, t, what)
    k = t[0]
    if k == "str":
        return y.text(node, what)
    if k == "arr":
        return [_from(y, t[1], x, f"{what}[{i}]") for i, x in enumerate(y.seq(node, t[2], what))]
    if k in ("list", "maxlist"):
        return [_from(y, t[1], x, f"{what}[{i}]") for i, x in enumerate(y.seq(node, None, what))]
    if k == "rec":
        y.keys(node, [nm for nm, _ in t[1]], what)
        return {nm: _from(y, ft, y.field(node, nm, what), f"{what}.{nm}") for nm, ft in t[1]}
    if k in ("first", "each"):
        y.keys(node, t[1], what)
        return {nm: [_from(y, t[2], x, f"{what}.{nm}[{i}]")
                     for i, x in enumerate(y.seq(y.field(node, nm, what), None, f"{what}.{nm}"))] for nm in t[1]}
    if k == "cmds":
        out = []
        for i, c in enumerate(y.seq(node, None, what)):
            w = f"{what}[{i}]"
            cname = y.scalar(y.field(c, "class", w), f"{w} class")
            if cname not in COMMANDS:
                raise y.err(f"{w}: class {cname!r} is not one of {', '.join(COMMANDS)}", c)
            y.keys(c, ["class"] + [nm for nm, _ in COMMANDS[cname]], w)
            out.append((cname, {nm: _from(y, ft, y.field(c, nm, w), f"{w}.{nm}") for nm, ft in COMMANDS[cname]}))
        return out
    raise AssertionError(t)


def from_yaml(text: str, source: str | None = None) -> Weather:
    from . import yamlish
    from .yamlish import Map, Scalar
    y = _Y(source)
    doc = yamlish.parse(text, source)
    tag = doc.get("riftstone") if isinstance(doc, Map) else None
    ext = tag.text.split("/")[0] if isinstance(tag, Scalar) and tag.text.endswith("/1") else None
    if ext not in EXTS:
        raise ParamError("not a Riftstone weather/fog/sky file (expected 'riftstone: <" + "|".join(EXTS) + ">/1')",
                         1, 1, source)
    vnode = y.field(doc, "version", "the file")
    version = y.int(vnode, "u32", "version")
    kinds = [k for k in KINDS.values() if k.ext == ext]
    k = next((k for k in kinds if k.version == version), None)
    if k is None:
        raise y.err(f"version {version}: {ext} files are version " + " or ".join(str(x.version) for x in kinds), vnode)
    y.keys(doc, ["riftstone", "resource", "version"] + [nm for nm, _ in k.fields], "the file")
    w = Weather(k.key, {nm: _from(y, t, y.field(doc, nm, "the file"), nm) for nm, t in k.fields})
    try:
        build(w)
    except FormatError as e:
        raise ParamError(str(e), None, None, source) from None
    return w


def yaml_to_bytes(text: str, source: str | None = None) -> bytes:
    return build(from_yaml(text, source))

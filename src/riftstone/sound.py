"""Sound cue formats of both games, byte-exact, with YAML forms: which sounds a thing plays and how.

Ten binary forms, one module (``parse`` dispatches by magic and version; ``build`` writes the same bytes):

  fmt / YAML tag   magic  ver         class                 game  what it holds
  srq / srq-ddo    SREQ   0x13        rSoundRequest         DDDA  sound-effect cues -> rSoundPackage (.spc)
                   SRQR   3                                 DDO   sound-effect cues -> rSoundBank (.sbkr)
  stq / stq-ddo    STRQ   0x1b        rSoundStreamRequest   DDDA  streamed cues (music, voice) -> wave files
                   STQR   2                                 DDO
  srd              SRND   2           rSoundRandom          DDDA  weighted random picks between cues
  smx / smx-ddo    SMX    3 / SMXR 1  rSoundSubMixer        both  mixer fader settings
  spl              SPL    0x77ddbe00  rSoundPhysicsList     DDDA  physics-sound lists (.spr paths)
  sbkr             SBKR   4           rSoundBank            DDO   programs -> waves (.xsew paths)
  sar              sar    0x11        rSoundAreaInfo        DDO   per-area music numbers, zones, surface sounds

How each was read (every grammar is the game's own loader; the save functions fixed the byte order):

* rSoundRequest / rSoundStreamRequest are memory images.  The loader reads the whole file and turns
  offsets into pointers (DDDA.exe load 0x00F73F10 / setup 0x00F735B0, save 0x00F73780; stream load
  0x00DC0EF0, save 0x00DC04F0; DDO.exe 0x015AF030 / 0x015AF4F0 / 0x015AF110 and 0x014921B0 /
  0x01492700).  Layout: a header (counts, then offsets), the source table (streams), the elements
  (one per cue), the name table and names (request: package/bank paths; stream: after the speaker
  data), DDDA's rSoundRandom path, zero padding to 16, then four speaker sections (speaker sets 12
  bytes, speakers 28, directional curves 16, curve elements 8; their fields carry the createProperty
  names of the tool's rSoundSpeakerSetXml / rSoundDirectionalCurveXml in DDO.exe, whose types and
  order the save writes -- DDO.exe 0x01488A0F branches on a set's mCoordinate).  Every offset, count
  and pad byte is derived again by ``build``.  DDDA's stream paths are stored without their
  ``sound\\stream\\`` prefix (the save strips it; SoundSource::open 0x00DBFFE0 puts it back unless
  the path holds it); DDO's are whole.  The stream source table is what the loader hands each
  SoundSource (DDDA 0x00DC00B0 copies mStreamLength, mSampleNum, mChannelNum, mLoopStart, mLoopEnd;
  without the table they come from the wave's descriptor); DDO (0x01492630) takes the same values from
  the same descriptor fields plus mSampleRate (descriptive: 48000 in the files) and two words
  (mUnk1C, mUnk20: UNKNOWN).
* Element field names: DDDA's are the PS3 build's (rSoundRequest::Element 0x90 bytes,
  rSoundStreamRequest::Element 0x9c, rSoundStreamRequest::SoundSource for the stream source table),
  including pad_* and the runtime pointers the tool saved (mpPackage holds the tool's heap address).
  DDO's elements (0x84 / 0x8c) were named from DDO.exe's code, matched to DDDA's where DDDA.exe does
  the same thing with the PS3-named field: getElement (0x015AEFA0 / 0x01492100) keys on mReqNo;
  the request walk (0x014790F0, like DDDA's 0x00DDB340) switches on mCommand and follows mLink; the
  bank play (0x01478430) passes mProgramNo to rSoundBank's program lookup; the mFreeArea accessors
  (0x0147C3F0 / 0x0147CA10) read 8 nibbles, 4 bytes, 4 shorts; the SE voice setup (0x014768E0) reads
  mGlobal, mID_1..3, mPriority, mPrioMode and mLimit with the same per-call overrides as DDDA's
  0x00DD8DD0; both voice setups (SE 0x014768E0, stream 0x01478540) copy the pan/pitch pair together,
  the three curve ids into the same voice slots (+0xa4..+0xac) as DDDA's 0x00DD8DD0, the directional
  curve id and mEqNo/mEqEffectNo/mEffectNo in DDDA's order, and turn mVol, mEffectSend and mLFESend
  (in that order, as DDDA's 0x00DD95F6 does) into gain with 10^(dB/20), at or below -96 dB silent
  (DDDA.exe 0x004AC0A0 is the same).  The stream loader names mReadType, mDiskLocation and
  mSrcFileNameTableIndex.  The bank pick (below) reads the SE cue's mUnk02 and mUnk03.  Every other DDO
  element field is mUnkXX (its offset): UNKNOWN.  By value range and position they are probably
  DDDA's mDelayTimer (+0x30), mBookingTimer (+0x34, read as u16), mTime / mKillTime (stream +0x44 /
  +0x48), mCenterVolume (+0x68), mInteriorDistance / mDopplerScaler (SE +0x74 / +0x78, stream +0x78 /
  +0x7c) -- not proven.
* Measured on the corpus: every package, bank and source index is -1 or in range; DDDA's mCommand 4
  cues sit in files with a random table (20,918 of 20,945) and 9,638 of the 9,648 resolvable
  mRandomReqNo values are an mRandomNo of that table; 29,656 DDO cue -> bank -> mProgramNo links
  resolve (1,312 name a program the bank lacks).
* rSoundRandom (DDDA.exe 0x00E30370; PS3 sSound::extractRequestSe 0x00F94050 picks with it): elements
  of 16 (request number, weight) pairs; a roll 1..100 walks the cumulative weights.  mAllowRepeat 1
  lets the same request play twice in a row; otherwise the engine re-rolls and stores the pick in
  mLastReqNo (-1 in every file).  A cue plays it when its mCommand is 4 (PS3 0x00F93F18).
* rSoundSubMixer (DDDA 0x00CE4C90, DDO 0x00AFEEF0) is read field by field; names from the fader's
  createProperty (props.json) and the PS3 build.  rSoundPhysicsList (0x01117810): 0x104 bytes kept as read
  (mUnk10 then the 64-entry table getIndex reads), then rSoundPhysicsRigidBody paths.  rSoundBank
  (DDO 0x0169BDA0): programs (getProgram 0x0169BB80 matches the u16 program number), waves (a path
  and 0x50 bytes; the first word is the wave's type id, rSoundSourceMSADPCM), and 8-byte records
  (UNKNOWN).  The bank's pick (0x0169B820): a program uses (mUnk02 >> 1) & 0x3ff waves from
  mWaveIndex on and draws one weighted by each wave's mUnk08 (the program's mUnk08 & 0xffffff is
  their total), skipping waves whose mUnk4C..mUnk4D range misses the cue's mUnk02 (255 = any; the
  cue's mUnk03 is passed along capped at 127).  Measured on all 1,734 banks: every program's waves are
  in range, together they cover every wave, and every total equals the sum of its waves' weights.
  rSoundAreaInfo (DDO 0x00AF0860): names from createProperty (the two Japanese names are
  given ASCII names: 戦闘 -> BattleBgm, アウトロ -> OutroBgm); resource references are a type name and
  a path (zone0..5, <surface>Request, resource0..3 are descriptive).  Measured: zone0/1/2 hold the
  occ_/seg_/tri_ rZone files, every set NoramlBgm/DayBgm/NightBgm is a request number of resource1's
  stream request and every set BattleBgm/OutroBgm one of resource3's.

Proof (``tools/check_corpus.py``-style run over every distinct resource; parse -> build and the YAML
round trip are byte-for-byte): DDDA srq 1,600, stq 2,632, srd 256, smx 49, spl 29; DDO srq 2,399,
stq 1,132, smx 35, sbkr 1,734, sar 684.  ``parse`` refuses (FormatError) anything ``build`` would not
reproduce, so a successful parse always rebuilds the same bytes.

UNKNOWN: what the speaker set's mCoordinate / mSpeakerSetting values mean (DDDA's 131 sets: 2 and 1;
DDO's 377: 3 or 2, and 1; no file of either game has a directional curve);
the DDO element fields named mUnkXX; rSoundBank's other wave bytes and its 8-byte records;
rSoundPhysicsList's mUnk0C / mUnk10; what the sound zones do.  Whether an edit sounds right in game:
UNKNOWN until heard.  The wave containers (DDDA .spc rSoundPackage, DDO .xsew rSoundSourceMSADPCM)
are not decoded here.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from .errors import FormatError, ParamError

# -- field codes -----------------------------------------------------------------------------------
# u/s: unsigned/signed decimal; x: unsigned shown as hex (pads, pointers, bit fields); f32: a float kept
# as its exact 32-bit pattern; z: zero padding the loader skips (not shown; the rebuild check refuses
# anything else there).
_FMT = {"u8": "B", "s8": "b", "x8": "B", "u16": "H", "s16": "h", "x16": "H", "u32": "I", "s32": "i",
        "x32": "I", "f32": "I", "z8": "B", "z16": "H", "z32": "I"}
_RANGE = {"u8": (0, 0xFF), "s8": (-0x80, 0x7F), "x8": (0, 0xFF), "u16": (0, 0xFFFF), "s16": (-0x8000, 0x7FFF),
          "x16": (0, 0xFFFF), "u32": (0, 0xFFFFFFFF), "s32": (-0x80000000, 0x7FFFFFFF),
          "x32": (0, 0xFFFFFFFF), "f32": (0, 0xFFFFFFFF)}
_HEXW = {"x8": 2, "x16": 4, "x32": 8}
_U32 = struct.Struct("<I")
_S32 = struct.Struct("<i")


class _Rec:
    """A fixed-size record: (name, code) pairs in file order; "f32*3" is a run of 3 values (a list)."""

    def __init__(self, name: str, fields: tuple):
        self.name = name
        self.fields = tuple((n, c.partition("*")[0], int(c.partition("*")[2] or 0)) for n, c in fields)
        self.st = struct.Struct("<" + "".join(f"{k or ''}{_FMT[b]}" for _, b, k in self.fields))
        self.size = self.st.size
        self.schema = tuple((n, ("array", b, k) if k else b) for n, b, k in self.fields if b[0] != "z")
        self._plain = all(not k for _, _, k in self.fields)

    def unpack(self, data, off: int) -> dict:
        vals = self.st.unpack_from(data, off)
        if self._plain:
            return {n: v for (n, b, _), v in zip(self.fields, vals) if b[0] != "z"}
        out, i = {}, 0
        for n, b, k in self.fields:
            if b[0] != "z":
                out[n] = list(vals[i:i + k]) if k else vals[i]
            i += k or 1
        return out

    def pack(self, d: dict, fmt: str) -> bytes:
        try:
            if self._plain:
                return self.st.pack(*(0 if b[0] == "z" else d[n] for n, b, _ in self.fields))
            vals = []
            for n, b, k in self.fields:
                v = [0] * (k or 1) if b[0] == "z" else d[n] if k else [d[n]]
                if not isinstance(v, list) or len(v) != (k or 1):
                    raise FormatError(fmt, f"{self.name}.{n} holds {k} values")
                vals += v
            return self.st.pack(*vals)
        except KeyError as e:
            raise FormatError(fmt, f"a {self.name} record has no {e.args[0]}") from None
        except (struct.error, TypeError):
            for n, b, k in self.fields:
                if b[0] != "z":
                    for v in (d.get(n) if k else [d.get(n)]):
                        _check(fmt, f"{self.name}.{n}", b, v)
            raise FormatError(fmt, f"a {self.name} record does not fit its fields") from None


def _check(fmt, what, code, v):
    lo, hi = _RANGE[code]
    if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
        raise FormatError(fmt, f"{what} = {v!r} is not a {code} ({lo}..{hi})")


class _R:
    """Bounds-checked reader: every failure is a FormatError naming the offset."""

    __slots__ = ("d", "p", "fmt")

    def __init__(self, data, fmt: str, p: int = 0):
        self.d = data
        self.p = p
        self.fmt = fmt

    def err(self, msg: str) -> FormatError:
        return FormatError(self.fmt, msg, self.p)

    def need(self, n: int) -> int:
        if self.p + n > len(self.d):
            raise self.err(f"file ends early ({n} bytes needed, {len(self.d) - self.p} left)")
        p = self.p
        self.p += n
        return p

    def u8(self) -> int:
        return self.d[self.need(1)]

    def u32(self) -> int:
        return _U32.unpack_from(self.d, self.need(4))[0]

    def s32(self) -> int:
        return _S32.unpack_from(self.d, self.need(4))[0]

    def rec(self, rec: _Rec) -> dict:
        return rec.unpack(self.d, self.need(rec.size))

    def recs(self, rec: _Rec, n: int) -> list:
        if n > (len(self.d) - self.p) // rec.size:
            raise self.err(f"{n} {rec.name} records ({rec.size} bytes each) do not fit in the file")
        return [self.rec(rec) for _ in range(n)]

    def cstr(self) -> bytes:
        e = self.d.find(b"\0", self.p)
        if e < 0:
            raise self.err("a text is not terminated")
        s = bytes(self.d[self.p:e])
        self.p = e + 1
        return s

    def at(self, off: int, what: str) -> None:
        if off != self.p:
            raise self.err(f"{what} is at 0x{off:x}; the layout puts it at 0x{self.p:x}")

    def skip_to(self, align: int) -> None:
        self.need(-self.p % align)

    def end(self) -> None:
        if self.p != len(self.d):
            raise self.err(f"{len(self.d) - self.p} byte(s) after the last field")


def _align(n: int, a: int) -> int:
    return n + (-n % a)


def _path(fmt: str, what: str, v) -> bytes:
    if not isinstance(v, (bytes, bytearray)):
        raise FormatError(fmt, f"{what} must be bytes, not {type(v).__name__}")
    if b"\0" in v:
        raise FormatError(fmt, f"{what} holds a NUL byte")
    return bytes(v)


# -- the model ---------------------------------------------------------------------------------------
@dataclass
class Sound:
    """One sound resource: ``fmt`` names the binary form (FORMATS), ``data`` its content by that form's
    schema (records are dicts of whole numbers; f32 fields hold the exact 32-bit pattern; paths are bytes)."""
    fmt: str
    data: dict

    @property
    def ext(self) -> str:
        return FORMATS[self.fmt].ext

    @property
    def cls(self) -> str:
        return FORMATS[self.fmt].cls


@dataclass(frozen=True)
class Format:
    fmt: str
    ext: str
    cls: str
    game: str
    magic: bytes
    version: int
    schema: tuple


# -- speaker sections (all four request forms) -------------------------------------------------------
# The compiled form of the tool's rSoundSpeakerSetXml and rSoundDirectionalCurveXml: their createProperty
# names (DDO props.json), which have the same types in the same order as what the save writes here.  The
# index fields are the save's pointer -> index conversions.
_SPEAKER_SET = _Rec("speaker set", (("mCoordinate", "u32"), ("mSpeakerSetting", "u32"), ("mSpeakerIndex", "s32")))
_SPEAKER = _Rec("speaker", (("mSpeakerPosition", "f32*3"), ("mSpeakerOrientation", "f32*3"),
                            ("mDirectionalCurveIndex", "s32")))
_CURVE = _Rec("directional curve", (("mEnable", "u32"), ("mZeroRadianValue", "f32"), ("mPiRadianValue", "f32"),
                                    ("mElementIndex", "s32")))
_CURVE_ELEMENT = _Rec("directional curve element", (("mAngle", "f32"), ("mValue", "f32")))
_SECTIONS = (("speakerSets", _SPEAKER_SET), ("speakers", _SPEAKER), ("directionalCurves", _CURVE),
             ("directionalCurveElements", _CURVE_ELEMENT))
_SECTION_SCHEMA = tuple((k, ("list", rec.schema)) for k, rec in _SECTIONS)


def _read_sections(r: _R, counts, offsets) -> dict:
    out = {}
    for (key, rec), n, off in zip(_SECTIONS, counts, offsets):
        r.at(off, key)
        out[key] = r.recs(rec, n)
    return out


def _section_counts(d: dict) -> list:
    return [len(d[k]) for k, _ in _SECTIONS]


def _write_sections(out: bytearray, d: dict, fmt: str) -> None:
    for key, rec in _SECTIONS:
        for item in d[key]:
            out += rec.pack(item, fmt)


def _section_offsets(start: int, d: dict) -> list:
    offs = []
    p = start
    for key, rec in _SECTIONS:
        offs.append(p)
        p += len(d[key]) * rec.size
    return offs


# -- rSoundRequest -------------------------------------------------------------------------------------
_REQ_HEAD = (("mReqNo", "u16"),)
_SRQ_ELEMENT = _Rec("rSoundRequest element", (
    ("mReqNo", "u16"), ("pad_00", "x16"), ("mCategory", "u32"), ("mCommand", "u32"), ("mGlobal", "u8"),
    ("pad_01", "x8"), ("mID_1", "s16"), ("mID_2", "s16"), ("mID_3", "s16"), ("mPriority", "u8"),
    ("mPrioMode", "u8"), ("pad_02", "x16"), ("mLimit", "u32"), ("mLink", "s16"), ("mProgramNo", "s16"),
    ("mSplitNo", "s16"), ("mPan", "s16"), ("mVol", "f32"), ("mPitchShift", "s32"), ("mEffectSend", "f32"),
    ("mLFESend", "f32"), ("mRandomReqNo", "u32"), ("mDelayTimer", "u32"), ("mBookingTimer", "u32"),
    ("mCenterVolume", "u32"), ("mVolumeCurveID", "s32"), ("mEffectCurveID", "s32"), ("mLFECurveID", "s32"),
    ("mDirectionalCurveID", "s32"), ("mEqNo", "s16"), ("mEqEffectNo", "s16"), ("mEffectNo", "s16"),
    ("pad_03", "x16"), ("mInteriorDistance", "f32"), ("mDopplerScaler", "f32"), ("mFreeArea00_07", "x32"),
    ("mFreeArea08", "u8"), ("mFreeArea09", "u8"), ("mFreeArea10", "u8"), ("mFreeArea11", "u8"),
    ("mFreeArea12", "s16"), ("mFreeArea13", "s16"), ("mFreeArea14", "s16"), ("mFreeArea15", "s16"),
    ("mRandomVolumeMax", "f32"), ("mRandomVolumeMin", "f32"), ("mRandomPitchMax", "s16"),
    ("mRandomPitchMin", "s16"), ("mPacFileNameTableIndex", "s32"), ("mSpeakerSetIndex", "s32"),
    ("mpPackage", "x32"), ("mpSpeakerSet", "x32")))
_SRQ_DDO_ELEMENT = _Rec("rSoundRequest element", (
    ("mReqNo", "u16"), ("mUnk02", "u8"), ("mUnk03", "u8"), ("mCategory", "u32"), ("mCommand", "u32"),
    ("mGlobal", "u8"), ("pad_01", "x8"), ("mID_1", "s16"), ("mID_2", "s16"), ("mID_3", "s16"),
    ("mPriority", "u8"), ("mPrioMode", "u8"), ("pad_02", "x16"), ("mLimit", "u32"), ("mLink", "s16"),
    ("mProgramNo", "s16"), ("mPan", "s16"), ("mUnk22", "s16"), ("mVol", "f32"), ("mEffectSend", "f32"),
    ("mPitchShift", "s32"), ("mUnk30", "u32"), ("mUnk34", "u32"), ("mVolumeCurveID", "s32"),
    ("mEffectCurveID", "s32"), ("mLFECurveID", "s32"), ("mFreeArea00_07", "x32"), ("mFreeArea08", "u8"),
    ("mFreeArea09", "u8"), ("mFreeArea10", "u8"), ("mFreeArea11", "u8"), ("mFreeArea12", "s16"),
    ("mFreeArea13", "s16"), ("mFreeArea14", "s16"), ("mFreeArea15", "s16"), ("mBankIndex", "s32"),
    ("mpBank", "x32"), ("mUnk5C", "x32"), ("mLFESend", "f32"), ("mDirectionalCurveID", "s32"), ("mUnk68", "u32"),
    ("mEqNo", "s16"), ("mEqEffectNo", "s16"), ("mEffectNo", "s16"), ("pad_03", "x16"), ("mUnk74", "f32"),
    ("mUnk78", "f32"), ("mSpeakerSetIndex", "s32"), ("mpSpeakerSet", "x32")))
_SRQ_HEADER = 0x34
_NULL = 0xFFFFFFFF


def _names_count(r: _R) -> list:
    """Candidate lengths of a name table at r.p: the first entry points just past the table; 0 last."""
    cands = []
    p = r.p
    if p + 4 <= len(r.d):
        first = _U32.unpack_from(r.d, p)[0]
        if p < first <= len(r.d) and (first - p) % 4 == 0:
            cands.append((first - p) // 4)
    cands.append(0)
    return cands


def _read_names(r: _R, n: int) -> list:
    if n > (len(r.d) - r.p) // 4:
        raise r.err(f"a name table of {n} entries does not fit")
    offs = [r.u32() for _ in range(n)]
    names = []
    for i, off in enumerate(offs):
        r.at(off, f"name {i}")
        names.append(r.cstr())
    return names


def _parse_srq(data, fmt: str) -> dict:
    ddo = fmt == "srq-ddo"
    el = _SRQ_DDO_ELEMENT if ddo else _SRQ_ELEMENT
    r = _R(data, fmt, 8)
    ne, nss, nsp, ndc, ndce = (r.u32() for _ in range(5))
    if ddo:
        if r.u32() != 0:
            raise r.err("the word at 0x1c is not 0")
        tbl, rnd = r.u32(), _NULL
    else:
        tbl, rnd = r.u32(), r.u32()
    offs = [r.u32() for _ in range(4)]
    elements = r.recs(el, ne)
    r.at(tbl, "the name table")
    last = None
    for n in _names_count(r):
        rr = _R(data, fmt, r.p)
        try:
            names = _read_names(rr, n)
            random = None
            if rnd != _NULL:
                rr.at(rnd, "the rSoundRandom path")
                random = rr.cstr()
            if any(data[rr.p:_align(rr.p, 16)]):
                raise rr.err("the padding before the speaker data is not zero")
            rr.skip_to(16)
            sections = _read_sections(rr, (nss, nsp, ndc, ndce), offs)
            rr.end()
        except FormatError as e:
            last = e
            continue
        out = {"elements": elements, ("banks" if ddo else "packages"): names}
        if not ddo:
            out["random"] = random
        out.update(sections)
        return out
    raise last


def _build_srq(d: dict, fmt: str) -> bytes:
    ddo = fmt == "srq-ddo"
    el = _SRQ_DDO_ELEMENT if ddo else _SRQ_ELEMENT
    names = [_path(fmt, "a bank path" if ddo else "a package path", s) for s in d["banks" if ddo else "packages"]]
    random = None if ddo else d["random"]
    ne = len(d["elements"])
    tbl = _SRQ_HEADER + ne * el.size
    p = tbl + 4 * len(names)
    name_offs = []
    for s in names:
        name_offs.append(p)
        p += len(s) + 1
    rnd = _NULL
    if random is not None:
        rnd = p
        p += len(_path(fmt, "the rSoundRandom path", random)) + 1
    body_end = p
    offs = _section_offsets(_align(p, 16), d)
    out = bytearray(FORMATS[fmt].magic + _U32.pack(FORMATS[fmt].version))
    out += struct.pack("<5I", ne, *_section_counts(d))
    out += struct.pack("<2I", 0, tbl) if ddo else struct.pack("<2I", tbl, rnd)
    out += struct.pack("<4I", *offs)
    for e in d["elements"]:
        out += el.pack(e, fmt)
    out += b"".join(_U32.pack(o) for o in name_offs)
    for s in names:
        out += s + b"\0"
    if random is not None:
        out += random + b"\0"
    out += bytes(_align(body_end, 16) - body_end)
    _write_sections(out, d, fmt)
    return bytes(out)


# -- rSoundStreamRequest -------------------------------------------------------------------------------
_STQ_ELEMENT = _Rec("rSoundStreamRequest element", (
    ("mReqNo", "u16"), ("pad_0", "x16"), ("mCategory", "u32"), ("mCommand", "u32"), ("mReadType", "u32"),
    ("mDiskLocation", "u32"), ("mGlobal", "u8"), ("pad_01", "x8"), ("mID_1", "s16"), ("mID_2", "s16"),
    ("mID_3", "s16"), ("mPriority", "u8"), ("mPrioMode", "u8"), ("pad_02", "x16"), ("mLimit", "u32"),
    ("mLink", "s16"), ("mPan", "s16"), ("mVol", "f32"), ("mPitchShift", "s32"), ("mEffectSend", "f32"),
    ("mLFESend", "f32"), ("mRandomReqNo", "u32"), ("mDelayTimer", "u32"), ("mBookingTimer", "u32"),
    ("mCenterVolume", "u32"), ("mVolumeCurveID", "s32"), ("mEffectCurveID", "s32"), ("mLFECurveID", "s32"),
    ("mDirectionalCurveID", "s32"), ("mEqNo", "s16"), ("mEqEffectNo", "s16"), ("mEffectNo", "s16"),
    ("pad_03", "x16"), ("mInteriorDistance", "f32"), ("mDopplerScaler", "f32"), ("mTime", "u32"),
    ("mKillTime", "u32"), ("mFreeArea00_07", "x32"), ("mFreeArea08", "u8"), ("mFreeArea09", "u8"),
    ("mFreeArea10", "u8"), ("mFreeArea11", "u8"), ("mFreeArea12", "s16"), ("mFreeArea13", "s16"),
    ("mFreeArea14", "s16"), ("mFreeArea15", "s16"), ("mRandomVolumeMax", "f32"), ("mRandomVolumeMin", "f32"),
    ("mRandomPitchMax", "s16"), ("mRandomPitchMin", "s16"), ("mSrcFileNameTableIndex", "s32"),
    ("mSpeakerSetIndex", "s32"), ("mpSource", "x32"), ("mpSpeakerSet", "x32")))
_STQ_DDO_ELEMENT = _Rec("rSoundStreamRequest element", (
    ("mReqNo", "u16"), ("pad_0", "x16"), ("mCategory", "u32"), ("mCommand", "u32"), ("mReadType", "u32"),
    ("mGlobal", "u8"), ("pad_01", "x8"), ("mID_1", "s16"), ("mID_2", "s16"), ("mID_3", "s16"),
    ("mPriority", "u8"), ("mPrioMode", "u8"), ("pad_02", "x16"), ("mLimit", "u32"), ("mLink", "s16"),
    ("mPan", "s16"), ("mVol", "f32"), ("mEffectSend", "f32"), ("mPitchShift", "s32"), ("mUnk30", "u32"),
    ("mUnk34", "u32"), ("mVolumeCurveID", "s32"), ("mEffectCurveID", "s32"), ("mLFECurveID", "s32"),
    ("mUnk44", "u32"), ("mUnk48", "u32"), ("mFreeArea00_07", "x32"), ("mFreeArea08", "u8"), ("mFreeArea09", "u8"),
    ("mFreeArea10", "u8"), ("mFreeArea11", "u8"), ("mFreeArea12", "s16"), ("mFreeArea13", "s16"),
    ("mFreeArea14", "s16"), ("mFreeArea15", "s16"), ("mSrcFileNameTableIndex", "s32"),
    ("mDiskLocation", "u32"), ("mLFESend", "f32"), ("mUnk68", "u32"), ("mDirectionalCurveID", "s32"),
    ("mEqNo", "s16"), ("mEqEffectNo", "s16"), ("mEffectNo", "s16"), ("pad_03", "x16"), ("mUnk78", "f32"),
    ("mUnk7C", "f32"), ("mSpeakerSetIndex", "s32"), ("mpSpeakerSet", "x32"), ("mpSource", "x32")))
# The source table: the path's offset, then what the loader copies into each SoundSource (DDDA's names are
# rSoundStreamRequest::SoundSource's; DDO.exe copies the same values from the same descriptor fields plus
# three more).
_STQ_SOURCE = _Rec("stream source", (("mStreamLength", "u32"), ("mSampleNum", "u32"), ("mChannelNum", "u32"),
                                     ("mLoopStart", "s32"), ("mLoopEnd", "s32")))
_STQ_DDO_SOURCE = _Rec("stream source", (("mStreamLength", "u32"), ("mSampleNum", "u32"), ("mChannelNum", "u32"),
                                         ("mSampleRate", "u32"), ("mLoopStart", "s32"), ("mLoopEnd", "s32"),
                                         ("mUnk1C", "x32"), ("mUnk20", "u32")))
_STQ_HEADER = {"stq": 0x3C, "stq-ddo": 0x38}


def _parse_stq(data, fmt: str) -> dict:
    ddo = fmt == "stq-ddo"
    el, src = (_STQ_DDO_ELEMENT, _STQ_DDO_SOURCE) if ddo else (_STQ_ELEMENT, _STQ_SOURCE)
    r = _R(data, fmt, 8)
    nsrc, ne, nss, nsp, ndc, ndce = (r.u32() for _ in range(6))
    src_tbl, elo = r.u32(), r.u32()
    rnd = _NULL if ddo else r.u32()
    offs = [r.u32() for _ in range(4)]
    r.at(src_tbl, "the source table")
    if nsrc > (len(data) - r.p) // (src.size + 4):
        raise r.err(f"{nsrc} stream sources do not fit in the file")
    sources = []
    for _ in range(nsrc):
        off = r.u32()
        sources.append((off, r.rec(src)))
    r.at(elo, "the elements")
    elements = r.recs(el, ne)
    random = None
    if rnd != _NULL:
        r.at(rnd, "the rSoundRandom path")
        random = r.cstr()
    if any(data[r.p:_align(r.p, 16)]):
        raise r.err("the padding before the speaker data is not zero")
    r.skip_to(16)
    sections = _read_sections(r, (nss, nsp, ndc, ndce), offs)
    out_sources = []
    for i, (off, fields) in enumerate(sources):
        r.at(off, f"source {i}'s path")
        out_sources.append({"path": r.cstr(), **fields})
    r.end()
    out = {"sources": out_sources, "elements": elements}
    if not ddo:
        out["random"] = random
    out.update(sections)
    return out


def _build_stq(d: dict, fmt: str) -> bytes:
    ddo = fmt == "stq-ddo"
    el, src = (_STQ_DDO_ELEMENT, _STQ_DDO_SOURCE) if ddo else (_STQ_ELEMENT, _STQ_SOURCE)
    head = _STQ_HEADER[fmt]
    sources = d["sources"]
    paths = [_path(fmt, "a stream path", s.get("path") if isinstance(s, dict) else None) for s in sources]
    ne = len(d["elements"])
    elo = head + len(sources) * (src.size + 4)
    p = elo + ne * el.size
    random = None if ddo else d["random"]
    rnd = _NULL
    if random is not None:
        rnd = p
        p += len(_path(fmt, "the rSoundRandom path", random)) + 1
    body_end = p
    offs = _section_offsets(_align(p, 16), d)
    p = offs[-1] + len(d["directionalCurveElements"]) * _CURVE_ELEMENT.size
    path_offs = []
    for s in paths:
        path_offs.append(p)
        p += len(s) + 1
    out = bytearray(FORMATS[fmt].magic + _U32.pack(FORMATS[fmt].version))
    out += struct.pack("<6I", len(sources), ne, *_section_counts(d))
    out += struct.pack("<2I", head, elo)
    if not ddo:
        out += _U32.pack(rnd)
    out += struct.pack("<4I", *offs)
    for off, s in zip(path_offs, sources):
        out += _U32.pack(off) + src.pack(s, fmt)
    for e in d["elements"]:
        out += el.pack(e, fmt)
    if random is not None:
        out += random + b"\0"
    out += bytes(_align(body_end, 16) - body_end)
    _write_sections(out, d, fmt)
    for s in paths:
        out += s + b"\0"
    return bytes(out)


# -- rSoundRandom ----------------------------------------------------------------------------------------
_PICK = _Rec("random pick", (("mReqNo", "u32"), ("mRate", "u32")))
_SRD_PICKS = 16
_SRD_TAIL = _Rec("random element tail", (("mAllowRepeat", "u8"), ("pad_85", "x8"), ("pad_86", "x8"),
                                         ("pad_87", "x8"), ("mLastReqNo", "s32")))
_SRD_ELEMENT_SIZE = 4 + _SRD_PICKS * _PICK.size + _SRD_TAIL.size          # 0x8c


def _parse_srd(data, fmt: str) -> dict:
    r = _R(data, fmt, 8)
    n = r.u32()
    if r.u32() != _NULL:
        raise r.err("the word at 0x0c is not 0xffffffff")
    if n > (len(data) - r.p) // _SRD_ELEMENT_SIZE:
        raise r.err(f"{n} random elements do not fit in the file")
    elements = []
    for _ in range(n):
        e = {"mRandomNo": r.u32(), "mTable": [r.rec(_PICK) for _ in range(_SRD_PICKS)]}
        e.update(r.rec(_SRD_TAIL))
        elements.append(e)
    r.end()
    return {"elements": elements}


def _build_srd(d: dict, fmt: str) -> bytes:
    out = bytearray(FORMATS[fmt].magic + struct.pack("<3I", FORMATS[fmt].version, len(d["elements"]), _NULL))
    for e in d["elements"]:
        _check(fmt, "mRandomNo", "u32", e.get("mRandomNo"))
        table = e.get("mTable")
        if not isinstance(table, list) or len(table) != _SRD_PICKS:
            raise FormatError(fmt, f"mTable holds {_SRD_PICKS} picks")
        out += _U32.pack(e["mRandomNo"])
        for pick in table:
            out += _PICK.pack(pick, fmt)
        out += _SRD_TAIL.pack(e, fmt)
    return bytes(out)


# -- rSoundSubMixer ----------------------------------------------------------------------------------------
_SMX_HEAD = _Rec("sub-mixer header", (("mActive", "u8"), ("mPriority", "s8"), ("pad", "z16")))
_FADER = _Rec("fader", (("mFaderID", "u8"), ("mSendID", "s16"), ("mVol", "f32"), ("mEqNo", "s8"),
                        ("mEqEffectNo", "s8"), ("mEffectNo", "s8"), ("mAbs", "u8"), ("pad", "z8"),
                        ("mTransitionTime", "u16"), ("mCurve", "s16"), ("mSustainTime", "u16"),
                        ("mReleaseTime", "u16")))


def _parse_smx(data, fmt: str) -> dict:
    r = _R(data, fmt, 8)
    out = r.rec(_SMX_HEAD)
    out["mEQPreset"] = [r.s32() for _ in range(9)]
    out["mReverbPreset"] = [r.s32() for _ in range(4)]
    out["mFaders"] = r.recs(_FADER, r.u32())
    r.end()
    return out


def _build_smx(d: dict, fmt: str) -> bytes:
    out = bytearray(FORMATS[fmt].magic + _U32.pack(FORMATS[fmt].version))
    out += _SMX_HEAD.pack(d, fmt)
    for key, n in (("mEQPreset", 9), ("mReverbPreset", 4)):
        vals = d[key]
        if not isinstance(vals, list) or len(vals) != n:
            raise FormatError(fmt, f"{key} holds {n} values")
        for v in vals:
            _check(fmt, key, "s32", v)
            out += _S32.pack(v)
    out += _U32.pack(len(d["mFaders"]))
    for f in d["mFaders"]:
        out += _FADER.pack(f, fmt)
    return bytes(out)


# -- rSoundPhysicsList ------------------------------------------------------------------------------------
_SPL_INDEX = 64


def _parse_spl(data, fmt: str) -> dict:
    r = _R(data, fmt, 8)
    n = r.u32()
    out = {"mUnk0C": r.u32(), "mUnk10": r.u32(), "mIndexTbl": [r.s32() for _ in range(_SPL_INDEX)]}
    if n > len(data) - r.p:
        raise r.err(f"{n} paths do not fit in the file")
    out["paths"] = [r.cstr() for _ in range(n)]
    r.end()
    return out


def _build_spl(d: dict, fmt: str) -> bytes:
    paths = [_path(fmt, "a physics path", p) for p in d["paths"]]
    _check(fmt, "mUnk0C", "u32", d.get("mUnk0C"))
    _check(fmt, "mUnk10", "u32", d.get("mUnk10"))
    tbl = d["mIndexTbl"]
    if not isinstance(tbl, list) or len(tbl) != _SPL_INDEX:
        raise FormatError(fmt, f"mIndexTbl holds {_SPL_INDEX} values")
    for v in tbl:
        _check(fmt, "mIndexTbl", "s32", v)
    out = bytearray(FORMATS[fmt].magic + struct.pack("<4I", FORMATS[fmt].version, len(paths), d["mUnk0C"],
                                                    d["mUnk10"]))
    out += struct.pack(f"<{_SPL_INDEX}i", *tbl)
    for p in paths:
        out += p + b"\0"
    return bytes(out)


# -- rSoundBank -----------------------------------------------------------------------------------------------
_BANK_PROGRAM = _Rec("bank program", (("mProgramNo", "u16"), ("mUnk02", "x16"), ("mWaveIndex", "u32"),
                                      ("mUnk08", "x32")))
_BANK_WAVE = _Rec("bank wave", (
    ("mType", "x32"), ("mUnk04", "u8"), ("mUnk05", "x8"), ("mUnk06", "x8"), ("mUnk07", "u8"), ("mUnk08", "u8"),
    ("mUnk09", "u8"), ("mUnk0A", "u8"), ("mUnk0B", "u8"), ("mUnk0C", "u8"), ("mUnk0D", "u8"), ("mUnk0E", "u8"),
    ("mUnk0F", "u8"), ("mUnk10", "s16"), ("mUnk12", "s16"), ("mUnk14", "u8"), ("mUnk15", "u8"), ("mUnk16", "u8"),
    ("mUnk17", "u8"), ("mUnk18", "u8"), ("mUnk19", "u8"), ("mUnk1A", "u8"), ("mUnk1B", "u8"), ("mUnk1C", "f32"),
    ("mUnk20", "f32"), ("mUnk24", "u16"), ("mUnk26", "u16"), ("mUnk28", "u16"), ("mUnk2A", "u16"),
    ("mUnk2C", "u32"), ("mUnk30", "u16"), ("mUnk32", "u16"), ("mUnk34", "u8"), ("mUnk35", "u8"),
    ("mUnk36", "u8"), ("mUnk37", "u8"), ("mUnk38", "u32"), ("mUnk3C", "u8"), ("mUnk3D", "u8"),
    ("mUnk3E", "u8"), ("mUnk3F", "u8"), ("mUnk40", "s16"), ("mUnk42", "u8"), ("mUnk43", "u8"),
    ("mUnk44", "u16"), ("mUnk46", "u16"), ("mUnk48", "u8"), ("mUnk49", "u8"), ("mUnk4A", "u8"),
    ("mUnk4B", "u8"), ("mUnk4C", "u8"), ("mUnk4D", "u8"), ("mUnk4E", "u8"), ("mUnk4F", "u8")))
_BANK_C = _Rec("bank record", (("mUnk00", "f32"), ("mUnk04", "u8"), ("mUnk05", "x8"), ("mUnk06", "s16")))


def _parse_sbkr(data, fmt: str) -> dict:
    r = _R(data, fmt, 8)
    na, nb, nc = r.u32(), r.u32(), r.u32()
    programs = r.recs(_BANK_PROGRAM, na)
    if nb > (len(data) - r.p) // (_BANK_WAVE.size + 1):
        raise r.err(f"{nb} bank waves do not fit in the file")
    waves = []
    for _ in range(nb):
        path = r.cstr()
        waves.append({"path": path, **r.rec(_BANK_WAVE)})
    records = r.recs(_BANK_C, nc)
    r.end()
    return {"programs": programs, "waves": waves, "records": records}


def _build_sbkr(d: dict, fmt: str) -> bytes:
    out = bytearray(FORMATS[fmt].magic + struct.pack("<4I", FORMATS[fmt].version, len(d["programs"]),
                                                    len(d["waves"]), len(d["records"])))
    for p in d["programs"]:
        out += _BANK_PROGRAM.pack(p, fmt)
    for w in d["waves"]:
        out += _path(fmt, "a wave path", w.get("path") if isinstance(w, dict) else None) + b"\0"
        out += _BANK_WAVE.pack(w, fmt)
    for c in d["records"]:
        out += _BANK_C.pack(c, fmt)
    return bytes(out)


# -- rSoundAreaInfo --------------------------------------------------------------------------------------------
_SURFACES = ("Soil", "Sand", "Bog", "Stone", "Wood", "Iron", "Grass", "Cloth", "Tree", "Carpe", "Rock", "DeadLeaf",
             "Bone", "Grave", "Mud", "Water", "Straw", "RoofTile", "MagicSpa", "PoisonBog", "Tar", "DeepBog",
             "DeepWater", "DeepMagicSpa", "DeepPoisonBog", "DeepTar", "Extra01", "Extra02", "Extra03", "Extra04",
             "Extra05", "Extra06")
_SAR_FIELDS = ((("mStageNo", "s32"), ("mAreaNo", "s32"), ("mIsAmb", "u8"), ("NoramlBgm", "u32"), ("DayBgm", "u32"),
                ("NightBgm", "u32"), ("mIsSecond", "u8"), ("mIsAmb2", "u8"), ("NoramlBgm2", "u32"),
                ("DayBgm2", "u32"), ("NightBgm2", "u32"), ("mIsCycle", "u8"), ("CycleBgm", "u32"),
                ("BattleBgm", "u32"), ("OutroBgm", "u32"), ("EqLength", ("array", "f32", 4)))
               + tuple((f"zone{i}", ("ref",)) for i in range(6))
               + tuple(x for s in _SURFACES for x in ((f"Is{s}", "u8"), (f"{s}Request", ("ref",))))
               + tuple((f"resource{i}", ("ref",)) for i in range(4)))


def _read_ref(r: _R):
    t = r.cstr()
    if not t:
        return None
    return (t, r.cstr())


def _parse_sar(data, fmt: str) -> dict:
    r = _R(data, fmt, 8)
    out = {}
    for key, node in _SAR_FIELDS:
        if node == "u8":
            out[key] = r.u8()
        elif node == "u32":
            out[key] = r.u32()
        elif node == "s32":
            out[key] = r.s32()
        elif node[0] == "array":
            out[key] = [r.u32() for _ in range(node[2])]
        else:
            out[key] = _read_ref(r)
    r.end()
    return out


def _build_sar(d: dict, fmt: str) -> bytes:
    out = bytearray(FORMATS[fmt].magic + _U32.pack(FORMATS[fmt].version))
    for key, node in _SAR_FIELDS:
        v = d.get(key)
        if isinstance(node, str):
            _check(fmt, key, node, v)
            out += struct.pack("<" + _FMT[node], v)
        elif node[0] == "array":
            if not isinstance(v, list) or len(v) != node[2]:
                raise FormatError(fmt, f"{key} holds {node[2]} values")
            for x in v:
                _check(fmt, key, node[1], x)
            out += struct.pack(f"<{node[2]}I", *v)
        elif v is None:
            out += b"\0"
        else:
            if not isinstance(v, tuple) or len(v) != 2 or not v[0]:
                raise FormatError(fmt, f"{key} is a (type name, path) pair or None")
            out += _path(fmt, f"{key}'s type", v[0]) + b"\0" + _path(fmt, f"{key}'s path", v[1]) + b"\0"
    return bytes(out)


# -- the forms --------------------------------------------------------------------------------------------------
def _srq_schema(ddo: bool) -> tuple:
    el = _SRQ_DDO_ELEMENT if ddo else _SRQ_ELEMENT
    names = (("banks", ("list", ("path",))),) if ddo else (("packages", ("list", ("path",))),
                                                          ("random", ("opt_path",)))
    return (("elements", ("list", el.schema)),) + names + _SECTION_SCHEMA


def _stq_schema(ddo: bool) -> tuple:
    el, src = (_STQ_DDO_ELEMENT, _STQ_DDO_SOURCE) if ddo else (_STQ_ELEMENT, _STQ_SOURCE)
    rnd = () if ddo else (("random", ("opt_path",)),)
    return ((("sources", ("list", (("path", ("path",)),) + src.schema)), ("elements", ("list", el.schema)))
            + rnd + _SECTION_SCHEMA)


_SRD_SCHEMA = (("elements", ("list", (("mRandomNo", "u32"), ("mTable", ("fixed", _PICK.schema, _SRD_PICKS)))
                              + _SRD_TAIL.schema)),)
_SMX_SCHEMA = (_SMX_HEAD.schema + (("mEQPreset", ("array", "s32", 9)), ("mReverbPreset", ("array", "s32", 4)),
                                   ("mFaders", ("list", _FADER.schema))))
_SPL_SCHEMA = (("mUnk0C", "u32"), ("mUnk10", "u32"), ("mIndexTbl", ("array", "s32", _SPL_INDEX)),
               ("paths", ("list", ("path",))))
_SBKR_SCHEMA = (("programs", ("list", _BANK_PROGRAM.schema)),
                ("waves", ("list", (("path", ("path",)),) + _BANK_WAVE.schema)),
                ("records", ("list", _BANK_C.schema)))

FORMATS: dict[str, Format] = {f.fmt: f for f in (
    Format("srq", "srq", "rSoundRequest", "ddda", b"SREQ", 0x13, _srq_schema(False)),
    Format("srq-ddo", "srq", "rSoundRequest", "ddo", b"SRQR", 3, _srq_schema(True)),
    Format("stq", "stq", "rSoundStreamRequest", "ddda", b"STRQ", 0x1B, _stq_schema(False)),
    Format("stq-ddo", "stq", "rSoundStreamRequest", "ddo", b"STQR", 2, _stq_schema(True)),
    Format("srd", "srd", "rSoundRandom", "ddda", b"DNRS", 2, _SRD_SCHEMA),
    Format("smx", "smx", "rSoundSubMixer", "ddda", b"SMX\0", 3, _SMX_SCHEMA),
    Format("smx-ddo", "smx", "rSoundSubMixer", "ddo", b"SMXR", 1, _SMX_SCHEMA),
    Format("spl", "spl", "rSoundPhysicsList", "ddda", b"SPL\0", 0x77DDBE00, _SPL_SCHEMA),
    Format("sbkr", "sbkr", "rSoundBank", "ddo", b"SBKR", 4, _SBKR_SCHEMA),
    Format("sar", "sar", "rSoundAreaInfo", "ddo", b"sar\0", 0x11, _SAR_FIELDS),
)}
_BY_MAGIC = {f.magic: f for f in FORMATS.values()}
_CODEC = {"srq": (_parse_srq, _build_srq), "srq-ddo": (_parse_srq, _build_srq),
          "stq": (_parse_stq, _build_stq), "stq-ddo": (_parse_stq, _build_stq),
          "srd": (_parse_srd, _build_srd), "smx": (_parse_smx, _build_smx), "smx-ddo": (_parse_smx, _build_smx),
          "spl": (_parse_spl, _build_spl), "sbkr": (_parse_sbkr, _build_sbkr), "sar": (_parse_sar, _build_sar)}
MAGICS = tuple(_BY_MAGIC)


def format_of(data: bytes) -> str | None:
    """The form (FORMATS key) this resource's magic and version name, or None."""
    f = _BY_MAGIC.get(bytes(data[:4]))
    if f is None or len(data) < 8 or _U32.unpack_from(data, 4)[0] != f.version:
        return None
    return f.fmt


def is_sound(data: bytes) -> bool:
    return format_of(data) is not None


def parse(data: bytes) -> Sound:
    """Decode a sound cue resource.  Refuses (FormatError) anything build() would not reproduce exactly."""
    f = _BY_MAGIC.get(bytes(data[:4]))
    if f is None:
        raise FormatError("sound", f"not a sound cue resource (magic {bytes(data[:4])!r})", 0)
    if len(data) < 8:
        raise FormatError(f.fmt, f"truncated header ({len(data)} bytes)", 0)
    ver = _U32.unpack_from(data, 4)[0]
    if ver != f.version:
        raise FormatError(f.fmt, f"version 0x{ver:x}; {f.cls} files here are 0x{f.version:x}", 4)
    try:
        s = Sound(f.fmt, _CODEC[f.fmt][0](data, f.fmt))
    except (struct.error, IndexError, ValueError, MemoryError, OverflowError) as e:
        raise FormatError(f.fmt, f"malformed ({type(e).__name__})") from None
    again = build(s)
    if again != data:
        at = next((i for i, (a, b) in enumerate(zip(again, data)) if a != b), min(len(again), len(data)))
        raise FormatError(f.fmt, "the bytes are not in the layout the game's tools write "
                                 "(so they would not rebuild the same)", at)
    return s


def build(s: Sound) -> bytes:
    if not isinstance(s, Sound) or s.fmt not in FORMATS:
        raise FormatError("sound", f"not a sound model ({getattr(s, 'fmt', None)!r})")
    try:
        return _CODEC[s.fmt][1](s.data, s.fmt)
    except (KeyError, TypeError, AttributeError) as e:
        raise FormatError(s.fmt, f"the model is missing or mistypes a field ({type(e).__name__}: {e})") from None


# -- YAML ------------------------------------------------------------------------------------------------------
def _is_struct(node) -> bool:
    return isinstance(node, tuple) and bool(node) and isinstance(node[0], tuple)


def _path_node(b: bytes):
    from .yamlish import Map, Scalar
    try:
        t = b.decode("cp932")
        if t.encode("cp932") == b and all(" " <= c for c in t):
            return Scalar(t, "double")
    except UnicodeError:
        pass
    return Map([(Scalar("hex"), Scalar(b.hex(), "double"))], flow=True)


def _to_node(node, v):
    from .params import f32_bits_text
    from .yamlish import Map, Scalar, Seq
    if isinstance(node, str):
        if node == "f32":
            return Scalar(f32_bits_text(v))
        if node in _HEXW:
            return Scalar(f"0x{v:0{_HEXW[node]}x}")
        return Scalar(str(v))
    if _is_struct(node):
        small = len(node) <= 4 and all(isinstance(n, str) for _, n in node)
        return Map([(Scalar(k), _to_node(n, v[k])) for k, n in node], flow=small)
    kind = node[0]
    if kind == "path":
        return _path_node(v)
    if kind == "opt_path":
        return Scalar("null") if v is None else _path_node(v)
    if kind == "ref":
        if v is None:
            return Scalar("null")
        return Map([(Scalar("type"), _path_node(v[0])), (Scalar("path"), _path_node(v[1]))], flow=True)
    if kind == "array":
        return Seq([_to_node(node[1], x) for x in v], flow=True)
    return Seq([_to_node(node[1], x) for x in v])


_DB = "mVol/mEffectSend/mLFESend are decibels (gain 10^(dB/20); -96 or less is silent)."
_HEADS = {
    "srq": ["Sound-effect cues. Each element answers one request number (mReqNo) the game asks for:",
            "mPacFileNameTableIndex picks packages[n] (a .spc of waves; -1 none), mProgramNo/mSplitNo name",
            "the sound inside it. mCommand 4 plays a pick from the random table (random:, a .srd) whose",
            "mRandomNo is mRandomReqNo. mLink starts another request with this one (-1 none).",
            _DB, "pad_*/mp* hold what the game's tool saved; leave them."],
    "srq-ddo": ["Sound-effect cues. Each element answers one request number (mReqNo) the game asks for:",
                "mBankIndex picks banks[n] (a .sbkr; -1 none) and mProgramNo a program of that bank, which",
                "draws one of its .xsew waves (only waves whose range holds mUnk02; 255 = any).",
                "mLink starts another request with this one (-1 none).", _DB,
                "Fields named mUnkXX are not identified yet. pad_*/mp* hold what the game's tool saved."],
    "stq": ["Streamed cues (music, voice). Each element answers one request number (mReqNo):",
            "mSrcFileNameTableIndex picks sources[n], whose path is relative to sound\\stream\\ (-1 none).",
            "mLink starts another request with this one (-1 none).",
            _DB, "pad_*/mp* hold what the game's tool saved; leave them."],
    "stq-ddo": ["Streamed cues (music, voice). Each element answers one request number (mReqNo):",
                "mSrcFileNameTableIndex picks sources[n] (-1 none). mLink starts another request (-1 none).",
                _DB, "Fields named mUnkXX are not identified yet. pad_*/mp*: leave them."],
    "srd": ["Random tables. A request with mCommand 4 and mRandomReqNo = mRandomNo plays one of mTable's",
            "requests, weighted by mRate (a roll of 1..100 walks the running total). mAllowRepeat 0",
            "re-rolls a repeat of the last pick (kept in mLastReqNo, -1 in the files)."],
    "smx": ["Sub-mixer: the header and fader fields are the engine's (rSoundSubMixer, its Fader)."],
    "smx-ddo": ["Sub-mixer: the header and fader fields are the engine's (rSoundSubMixer, its Fader)."],
    "spl": ["Physics sound list: the rSoundPhysicsRigidBody (.spr) paths it loads, and the table",
            "getIndex(n) reads (n = 0..63; -1 none)."],
    "sbkr": ["Sound bank: programs (mProgramNo, which rSoundRequest's mProgramNo selects) each use",
             "(mUnk02 >> 1) & 0x3ff waves from waves[mWaveIndex] on; the game picks one at random weighted",
             "by the waves' mUnk08, whose total is mUnk08 & 0xffffff of the program (keep them in step),",
             "among waves whose mUnk4C..mUnk4D holds the cue's mUnk02 (255 = any). A wave is a .xsew path",
             "and its settings (mType is its type id, 0 without a path). Other mUnkXX: not identified."],
    "sar": ["Sound area: music request numbers, sound zones, one rSoundRequest per ground surface (IsX",
            "turns it on) and four references. In every file NoramlBgm/DayBgm/NightBgm are request numbers",
            "of resource1 (a stream request: the area's music), BattleBgm/OutroBgm of resource3.",
            "A reference is {type: <class>, path: <path>} or null; 4294967295 (0xffffffff) = no number."],
}


def to_yaml(s: Sound, name: str | None = None) -> str:
    from . import yamlish
    from .yamlish import Map, Scalar
    f = FORMATS[s.fmt]
    game = "Dragon's Dogma Online" if f.game == "ddo" else "Dragon's Dogma: Dark Arisen"
    head = [f"Riftstone {f.cls} (.{f.ext}, {game})" + (f" -- {name}" if name else ""),
            *_HEADS[s.fmt], "Rebuilds byte-for-byte when untouched."]
    items = [(Scalar("riftstone"), Scalar(f"{s.fmt}/1"))]
    if name:
        items.append((Scalar("resource"), Scalar(name, "double")))
    for key, node in f.schema:
        items.append((Scalar(key), _to_node(node, s.data[key])))
    if f.ext in ("srq", "stq"):             # name each cue's package / bank / source next to its index
        names = s.data["sources"] if f.ext == "stq" else s.data["banks" if s.fmt == "srq-ddo" else "packages"]
        names = [x["path"] if isinstance(x, dict) else x for x in names]
        shown = [_shown(p) for p in names]
        for el in next(v for k, v in items if k.text == "elements").items:
            for k, v in el.items:
                if k.text in _INDEX_FIELDS and 0 <= int(v.text) < len(names) and shown[int(v.text)].isprintable():
                    v.comment = shown[int(v.text)]
    return yamlish.emit(Map(items), head)


_INDEX_FIELDS = ("mPacFileNameTableIndex", "mBankIndex", "mSrcFileNameTableIndex")


def _where(n, source):
    return (getattr(n, "line", None), getattr(n, "col", None), source)


def _from_node(node, n, what: str, source):
    from .params import f32_bits
    from .yamlish import Map, Scalar, Seq
    if isinstance(node, str):
        if not isinstance(n, Scalar):
            raise ParamError(f"{what} is a single value", *_where(n, source))
        t = n.text.strip()
        if node == "f32":
            try:
                return f32_bits(t)
            except ValueError:
                raise ParamError(f"{what}: {t!r} is not a number that fits a 32-bit float",
                                 *_where(n, source)) from None
        try:
            v = int(t, 0)
        except ValueError:
            raise ParamError(f"{what}: expected a whole number, not {t!r}", *_where(n, source)) from None
        lo, hi = _RANGE[node]
        if not lo <= v <= hi:
            raise ParamError(f"{what}: {v} is out of range for {node} ({lo}..{hi})", *_where(n, source))
        return v
    if _is_struct(node):
        if not isinstance(n, Map):
            raise ParamError(f"{what} is a block of fields", *_where(n, source))
        known = {k for k, _ in node}
        for k, _ in n.items:
            if k.text not in known:
                raise ParamError(f"{what}: unexpected field {k.text!r}", k.line, k.col, source)
        out = {}
        for k, sub in node:
            v = n.get(k)
            if v is None:
                raise ParamError(f"{what}: {k} is missing", *_where(n, source))
            out[k] = _from_node(sub, v, f"{what}.{k}", source)
        return out
    kind = node[0]
    if kind in ("path", "opt_path"):
        if kind == "opt_path" and isinstance(n, Scalar) and n.style == "plain" and n.text.strip() in ("null", "~"):
            return None
        return _from_path(n, what, source)
    if kind == "ref":
        if isinstance(n, Scalar) and n.style == "plain" and n.text.strip() in ("null", "~"):
            return None
        if not isinstance(n, Map) or n.get("type") is None or n.get("path") is None or len(n.items) != 2:
            raise ParamError(f"{what} is null or {{type: <class>, path: <path>}}", *_where(n, source))
        t = _from_path(n.get("type"), f"{what}.type", source)
        if not t:
            raise ParamError(f"{what}.type is empty (write null for no reference)", *_where(n, source))
        return (t, _from_path(n.get("path"), f"{what}.path", source))
    if not isinstance(n, Seq):
        raise ParamError(f"{what} is a list", *_where(n, source))
    if kind in ("array", "fixed") and len(n.items) != node[2]:
        raise ParamError(f"{what} holds {node[2]} values, not {len(n.items)}", *_where(n, source))
    return [_from_node(node[1], x, f"{what}[{i}]", source) for i, x in enumerate(n.items)]


def _from_path(n, what, source) -> bytes:
    from .yamlish import Map, Scalar
    if isinstance(n, Map):
        h = n.get("hex")
        if not isinstance(h, Scalar) or len(n.items) != 1:
            raise ParamError(f"{what} is text or {{hex: ...}}", *_where(n, source))
        try:
            b = bytes.fromhex(h.text)
        except ValueError:
            raise ParamError(f"{what}: hex must be pairs of hex digits", *_where(h, source)) from None
    elif isinstance(n, Scalar):
        try:
            b = n.text.encode("cp932")
        except UnicodeError:
            raise ParamError(f"{what}: the text has characters the game's encoding (Shift-JIS) lacks",
                             *_where(n, source)) from None
    else:
        raise ParamError(f"{what} is text", *_where(n, source))
    if b"\0" in b:
        raise ParamError(f"{what} holds a NUL", *_where(n, source))
    return b


def from_yaml(text: str, source: str | None = None) -> Sound:
    from . import yamlish
    from .yamlish import Map, Scalar
    doc = yamlish.parse(text, source)
    tag = doc.get("riftstone") if isinstance(doc, Map) else None
    fmt = tag.text.split("/")[0] if isinstance(tag, Scalar) and tag.text.endswith("/1") else None
    if fmt not in FORMATS:
        raise ParamError("not a Riftstone sound file (expected 'riftstone: srq/1', 'stq-ddo/1', ...)", 1, 1, source)
    f = FORMATS[fmt]
    known = {k for k, _ in f.schema} | {"riftstone", "resource"}
    for k, _ in doc.items:
        if k.text not in known:
            raise ParamError(f"unexpected field {k.text!r}", k.line, k.col, source)
    data = {}
    for key, node in f.schema:
        n = doc.get(key)
        if n is None:
            raise ParamError(f"{key} is missing", None, None, source)
        data[key] = _from_node(node, n, key, source)
    s = Sound(fmt, data)
    try:
        build(s)
    except FormatError as e:
        raise ParamError(str(e), None, None, source) from None
    return s


def yaml_to_bytes(text: str, source: str | None = None) -> bytes:
    return build(from_yaml(text, source))


# -- summaries ------------------------------------------------------------------------------------------------
def _shown(b) -> str:
    return b.decode("cp932", "replace") if isinstance(b, (bytes, bytearray)) else str(b)


def info(s: Sound, limit: int = 12) -> str:
    """A few lines for `inspect`: counts and the paths the resource points at."""
    f = FORMATS[s.fmt]
    d = s.data
    game = "DDO" if f.game == "ddo" else "DDDA"
    if f.ext in ("srq", "stq"):
        els = d["elements"]
        nos = sorted(e["mReqNo"] for e in els)
        span = f", request numbers {nos[0]}..{nos[-1]}" if nos else ""
        names = d.get("packages", d.get("banks")) if f.ext == "srq" else [x["path"] for x in d["sources"]]
        if s.fmt == "stq":        # the game opens sound\stream\<path> unless the path holds that text (strstr)
            names = [p if b"sound\\stream\\" in p else b"sound\\stream\\" + p for p in names]
        what = {"srq": "package(s)", "srq-ddo": "bank(s)"}.get(s.fmt, "stream source(s)")
        lines = [f"{f.cls} ({game}): {len(els)} cue(s){span}; {len(names)} {what}"]
        if d.get("random") is not None:
            lines.append(f"  random table: {_shown(d['random'])}")
        spk = sum(len(d[k]) for k, _ in _SECTIONS)
        if spk:
            lines.append(f"  speaker data: " + ", ".join(f"{len(d[k])} {k}" for k, _ in _SECTIONS))
        lines += [f"  {_shown(p)}" for p in names[:limit]]
        if len(names) > limit:
            lines.append(f"  ... {len(names) - limit} more")
        return "\n".join(lines)
    if s.fmt == "srd":
        return f"rSoundRandom (DDDA): {len(d['elements'])} random table(s)"
    if f.ext == "smx":
        return f"rSoundSubMixer ({game}): {len(d['mFaders'])} fader(s), priority {d['mPriority']}"
    if s.fmt == "spl":
        return "\n".join([f"rSoundPhysicsList (DDDA): {len(d['paths'])} physics sound(s)"]
                         + [f"  {_shown(p)}" for p in d["paths"][:limit]])
    if s.fmt == "sbkr":
        named = [w["path"] for w in d["waves"] if w["path"]]
        return "\n".join([f"rSoundBank (DDO): {len(d['programs'])} program(s), {len(d['waves'])} wave(s), "
                          f"{len(d['records'])} record(s)"] + [f"  {_shown(p)}" for p in named[:limit]]
                         + ([f"  ... {len(named) - limit} more"] if len(named) > limit else []))
    refs = [(k, v) for k, v in d.items() if isinstance(v, tuple)]
    return "\n".join([f"rSoundAreaInfo (DDO): stage {d['mStageNo']} area {d['mAreaNo']}, {len(refs)} reference(s)"]
                     + [f"  {k}: {_shown(v[0])} {_shown(v[1])}" for k, v in refs[:limit]])

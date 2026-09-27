"""Schedulers (rScheduler ``.sdl``) and zones (rZone ``.zon``), both games, byte-exact, with YAML.

rScheduler -- ``.sdl``: timelines of events, lights, fades and scenery
=====================================================================

Dragon's Dogma: Dark Arisen ships 624 distinct files (version 19), Dragon's Dogma Online 1,481
(version 22). The loaders (DDDA.exe 0x00DB8E50, PS3 ``rScheduler::load`` 0x0100DE28, DDO.exe 0x014FE6B0)
read the file whole, check the magic and their own version, and turn the stored offsets into
pointers; ``uScheduler`` (PS3 ``applyTrack`` 0x00CE4180, ``updateTrack`` 0x00CE4C90) plays it. MT
Framework's scheduler classes carry no names in the PS3 build, so the names below describe what the code does::

  0x00 "SDL\\0"  u16 version (19 | 22)  u16 track count
  0x08 u32 mUnk08: 0xEE4828D3 in every DDDA file, 0x9942DD10 in every DDO file (the PS3 loader
       compares it with a value of the running game; PC loaders do not read it)
  0x0C u32 FrameMax (low 24 bits; getFrameMax) | FloorFrame (bit 24; isFloorFrame) | 7 bits (0)
  0x10 u32 BaseTrack (getBaseTrack: the track whose keys are the scheduler's markers)
  0x14 u32 offset of the string table
  0x18 track x count (24 bytes in DDDA, 32 in DDO):
    +0 u8 kind   +1 u8 type (MtProperty type of the property it drives)   +2 u16 key count
    +4 u32 parent: a unit track's sUnit move line (updateTrack -> sUnit::addBottom); a property
       track's owning track (always earlier)
    +8 u32 name (string table offset)   +C u32 unit tracks: the unit's class (MtDTI id);
       property tracks: the property's array index (MtProperty::setIndex)
    DDO only: +10 u32 mUnk10, +14 u32 mUnk14 (0 in every file)
    then u32 keys offset, u32 values offset (file offsets; 0 when there are no keys)

Kinds (the loader's relocation switch, and applyTrack's): 1 root, 2 unit, 3 system object (a
singleton such as CameraExt), 4 spacer (no name), 5 sub-object (a class property), then the keyed
kinds with their value size -- DDDA: 6 integer 4, 7 vector 16, 8 float 4, 9 bool 1, 10 unit
reference 4 (a track number), 11 resource 4, 12 string 4, 13 event 4, 14 matrix/curve 64; DDO
shifted them: 6 integer, 8 vector, 9 float, 11 bool, 12 unit reference, 13 resource, 14 string,
15 event, 16 matrix/curve (its kinds 7 and 10 are relocated too but no file has one; their value
size is UNKNOWN, so keys on them are refused). A key is u32 frame (low 24 bits) | mode << 24;
applyTrack: mode 0 hold the value, 2 the same but only when the playhead crosses the key, 3 linear
to the next key, 5 a curve through the neighbouring keys (vectors, floats; integers linear),
1 and 4 the value plus the frames since the key. A resource value is a string-table offset of
{u32 type id, path}; a string value a string-table offset; 0 is none.

Layout of every file: header, tracks; then per keyed track in order its keys (4-aligned) and
values (16-aligned), zero padding; then the string table from the next 4-byte boundary to the end
of the file. The string table is written by a search-or-append whose search skips the entry it
appended last (measured, and reproduced exactly on every file of both games): every track's name
in track order, then every resource and string value in order; a text already in the table --
also as the tail of a longer entry or inside a resource entry -- is reused unless that entry is
the newest, else it is appended. ``build`` repeats it, so names and paths are edited as text.
``parse`` checks that layout before it reads any key, reads a text once per string-table offset however
many tracks name it, and checks the table by writing it again the writer's way. The writer (_Strings)
answers each search from an index of where the texts it is given first occur, so a search costs the
text, not the table (131,070 distinct strings: build 0.7 s, parse 0.8 s, where the plain search took 7.2
and 16.9 s). Measured: all the references of a file together name at most 0.37 of it (DDDA 0.37, DDO
0.33; the longest text is 51 bytes); its YAML, which writes every reference's text in full, is at most
15.5 times the file (overall 3.2). Texts adding up to more than TEXT_LIMIT (16) times the file are
refused by ``parse`` and ``build`` (4,000 references to one 20 KB path would be 80 MB of YAML from 72 KB).

Proved on every distinct file (``check_corpus --only sdl``): DDDA 624, DDO 1,481; parse -> build and
the YAML round trip byte-for-byte.

rZone -- ``.zon``: sound, effect, light and object zones of a stage
===================================================================

Dragon's Dogma: Dark Arisen ships 477 distinct files (version 0x77DF25C4), Dragon's Dogma Online 1,698
(0x782A0684). Read with the loaders' own grammar: DDDA.exe ``rZone::load`` 0x01005E20 (PS3 0x01015560,
named), ``cMemoryHeader::load`` 0x010051C0, ``divideMemory`` 0x01005710, ``nZone::cLayoutElement::
loadBinary`` 0x00FC6FF0, the shapes' ``loadBinary`` (vtable slot 13), ``rZone::cGroupManager::
loadBinary`` 0x01005D40, ``cGridCollision::load`` 0x00FD9EB0; DDO.exe 0x017BEC30, 0x017BEA80, 0x015231B0,
0x017BEED0, 0x015AF680. Names are the classes' properties (DDO.exe props, PS3 createProperty) where
they have one, else descriptive::

  "zon\\0"  u32 version  u32 ZoneType (0 no grids, 1 a grid per group, 2 one grid for the zone)
  u32 n + n bytes Name (no NUL)   u32 ContentsClass (MtDTI id of the zone's contents class, e.g.
  nSoundOcclusion::cBaseContents)   u32 mUnk64 (one value per kind of zone: 0x157FF, 0x15200 ...)
  cMemoryHeader: u32 layout count, a u32 shape per layout; u32 group count, per group u32 layout
    count, u32 global layout count (type 1: + u32 grid index count, u32 grid cell count); u32
    ContentsNum; (DDO: u32 mUnkTable count); u32 unique-id table count; (type 2: u32 grid index
    count, u32 grid cell count)
  cContentsPool: an XFS object tree (MtSerializer::deserializeBinary)
  layout (nZone::cLayoutElement): s32 mPriority, the shape, s32 mContentsPoolID, s32
    mContentsPoolGroupID, u8 mIsEnable, u32 mIndex, u32 mLayoutGroupIndex, u32 mIndexOfLayoutGroup,
    u32 mUniqueID, s32 mGroupID, u8 1 + an XFS object (mpExtendObj) or u8 0
  group (rZone::cGroupManager): u32 mUnk08, s32 mUnk0C, u32[] GroupLayoutIndex, u32[]
    GroupGlobalLayoutIndex (getGroupLayoutIndex / getGroupGlobalLayoutIndex); type 1 with a grid:
    the grid, then a box per GroupLayoutIndex entry
  type 2: the zone grid, then a box per layout
  (DDO: u32[] mUnkTable)  u32[] LayoutIndexFromUniqueID (rZone::getLayoutIndexFromUniqueID)

A shape (ShapeInfo*): f32 mDecay, u8 mIsNativeData, then by the header's shape number -- 0 Base, 11
Global: nothing; 1 Area: f32 mHeight, f32 mBottom, f32[16] mVertex (4 vectors), f32[4]
mConcaveCrossPos, u8 mFlgConvex, u32 mConcaveStatus; 2 AABB: f32[8] mAABB (DDO: + f32 mDecayY, f32
mDecayZ, u8 mIsEnableExtendedDecay); 3 OBB: f32[20] mOBB (coord matrix, extent), f32 mDecayY, f32
mDecayZ, u8 mIsEnableExtendedDecay; 4 Sphere: f32[4] mSphere; 5 Capsule / 6 Cylinder: f32[12]; 7
Point: f32[4] mPos; 8 Line: f32[4] Position0, f32[4] Position1; 9 Panel: f32[16] mVertex; 10 Cone:
f32 mHeight, f32 mTopRadius, f32[4] mPos, f32 mBottomRadius. (Area's mHeight / mBottom are named by
the loader's use -- it sets every vertex's y to mBottom and flips a negative mHeight; Cone's mHeight is
the one getter-only property left for its first float.)
A grid (cGridCollision): "grco", u32 0x77C09C94, f32[8] box, u16 nx, u16 nz, u8 index type (0 u32,
1 u16), u8 packed (1 in every file; the unpacked form is refused), nx*nz cells of two u32 (at least one
in every file; a header's cell count 0 means no grid, so a grid without cells is refused), u32 index
count, the indices. A box: f32 min x y z, u32 its own number, f32 max x y z (the number equals the
box's position in every file; ``build`` writes it).

Measured on every file: the header's grid sizes equal the grids' (``build`` computes them), the
first u32 of the cells adds up to the index count (the second is UNKNOWN), and
LayoutIndexFromUniqueID[mUniqueID] is the layout's own index for all 12,647 DDDA and 17,898 DDO
layouts. Seven type 2 zones (1 DDDA, 6 DDO) have no zone grid and a header saying 0, 0; the loader
would read their next bytes as a grid (its save writes a grid only when it has one). The XFS objects
stay exact bytes (hex in YAML): ``riftstone.xfs`` decodes 7,965 of the 8,022 in DDDA's zones and
8,799 of 9,019 in DDO's (``zone_xfs``); the rest use property type 0x0F (color), which xfs.py does
not read yet. UNKNOWN: mUnk64, ContentsNum (not the number of pool objects), the groups' mUnk08 and
mUnk0C, the cells' second number, DDO's mUnkTable.

Proved on every distinct file (``check_corpus --only zon``): DDDA 477, DDO 1,698; parse -> build and
the YAML round trip byte-for-byte.
"""
from __future__ import annotations

import secrets
import struct
from dataclasses import dataclass, field

from .errors import FormatError, ParamError

SDL_MAGIC = b"SDL\0"
SDL_DDDA = 19
SDL_DDO = 22
SDL_TAG = "sdl/1"
_SDL_HEAD = struct.Struct("<4sHHIIII")
_TRACK_DDDA = struct.Struct("<BBHIIIII")
_TRACK_DDO = struct.Struct("<BBHIIIIIII")
UNK08 = {SDL_DDDA: 0xEE4828D3, SDL_DDO: 0x9942DD10}

# value size per keyed kind, and which kind means what, per version
_KINDS = {
    SDL_DDDA: {6: ("int", 4), 7: ("vector", 16), 8: ("float", 4), 9: ("bool", 1), 10: ("unitref", 4),
               11: ("resource", 4), 12: ("string", 4), 13: ("event", 4), 14: ("matrix", 64)},
    SDL_DDO: {6: ("int", 4), 7: ("unknown", None), 8: ("vector", 16), 9: ("float", 4), 10: ("unknown", None),
              11: ("bool", 1), 12: ("unitref", 4), 13: ("resource", 4), 14: ("string", 4), 15: ("event", 4),
              16: ("matrix", 64)},
}
KIND_NAMES = {1: "root", 2: "unit", 3: "system", 4: "spacer", 5: "object"}
# The classes the games' schedulers and zones name by MtDTI id (JAMCRC of the name; the names are the
# two executables' own DTI registry entries, and every id in every vanilla file resolves to one).
UNIT_CLASSES = (
    'sCameraExt', 'sEffectExt', 'sGameSys', 'sShadow', 'sVibrationExt', 'sWeatherManager', 'uActorModel',
    'uActorModelEm', 'uActorModelExt', 'uActorModelPl', 'uAmbientLight', 'uAmbientOcclusionFilter', 'uBaseModel',
    'uBigEyeColorFog', 'uBlinkPointLight', 'uBlinkSpotLight', 'uBloomFilter', 'uCameraBase', 'uCameraEffect',
    'uColorCorrectFilter', 'uColorCorrectFilterEm021004', 'uColorCorrectFilterExt', 'uColorFog', 'uColorFogExt',
    'uCuboidLight', 'uDDMCModel', 'uDDOActorModel', 'uDDOActorModelEm', 'uDDOActorModelPl',
    'uDNInfLightStateTransSpritDragon', 'uDNPointLightSetManager', 'uDNSpotLightDarkSky',
    'uDNSpotLightStateTransSpritDragon', 'uDOFFilter', 'uDOFFilterAfterlife', 'uDOFFilterEm021004',
    'uDOFFilterEvilEye', 'uDayNightColorFog', 'uDayNightColorFogEm021004', 'uDayNightHemiSphereLight',
    'uDayNightInfiniteLight', 'uDayNightPointLight', 'uDayNightPointLightOmConst', 'uDayNightSpotLight',
    'uDungeonColorFog', 'uDungeonHemiSphereLight', 'uDungeonInfiniteLight', 'uEdgeAntiAliasingFilter', 'uEfCam',
    'uEfCamNoPause', 'uEffect', 'uEffect2D', 'uEffect2DExt', 'uEffectEmitter', 'uEffectExt', 'uEffectExtNoPause',
    'uEffectExtWorldOffset', 'uEndCreditBook', 'uEnvMap', 'uEventModel', 'uFSMFlagControl', 'uFreeCamera', 'uGUI',
    'uGUIBadEndCredit', 'uGUIEndCredit', 'uGUIEndCredit2', 'uGUIEndCreditDDN', 'uGUIPrologue', 'uGUIScheduler',
    'uGUISubtitlesExt', 'uGUISubtitlesExt2', 'uGUITelop', 'uGodRaysFilter', 'uGrassReceiver', 'uGrassWindDirection',
    'uHazeFilter', 'uHazeFilterEm021004', 'uHazeFilterExt', 'uHemiSphereLight', 'uHemiSphereLightExt',
    'uImagePlaneFilter', 'uInfiniteLight', 'uInfiniteLightExt', 'uInfiniteLightExtEm021004',
    'uInfiniteLightNoPause', 'uLightScatteringFog', 'uModel', 'uMotionBlurFilter', 'uMovie', 'uNightPointLight',
    'uOfsMotionCamera', 'uPointLight', 'uPointLightExt', 'uPointShadow', 'uPointShadowExt', 'uRadialBlurFilter',
    'uReflectionMap', 'uResetFlag', 'uScrBaseModel', 'uScrBaseModelDayNight', 'uScrBaseModelHQ', 'uScrModel',
    'uScreenSpace', 'uSkyCloudModel', 'uSkyColorFog', 'uSkyDungeonPointLight', 'uSkyDungeonSpotLight',
    'uSkyGodRaysFilter', 'uSkyGrass', 'uSkyHemiSphereLight', 'uSkyInfiniteLight', 'uSkySpotLight',
    'uSkyStableShadow', 'uSkyWaterModel', 'uSkyWaterModelEx', 'uSoundSe', 'uSoundStream', 'uSpotLight',
    'uSpotLightExt', 'uSpotShadow', 'uSpotShadowExt', 'uStableShadow', 'uStableShadowExt', 'uSwingModel',
    'uTimeCtrlGodRaysFilter', 'uWaterReflectionMap')
ZONE_CLASSES = (
    'nSoundGenerator::cBaseContents', 'nSoundOcclusion::cBaseContents', 'nSoundTrigger::cBaseContents',
    'cZCEFLControl', 'cZCEffectColorControl', 'cZCEffectColorControl_01', 'cZCEffectControl', 'cZCGodRaysControl',
    'cZoneCategoryCharStaus', 'cZoneCategoryColorFog', 'cZoneCategoryLight', 'cZoneCategoryOmBasic',
    'cZoneCategoryStatusBase', 'cZoneCategoryStrongWind', 'cZoneCategoryUnitCtrlBase', 'cZoneIndoorGrassOff')
NAMED = {1, 2, 3, 5}
MODES = {0: "hold", 1: "count up", 2: "trigger", 3: "linear", 4: "count up", 5: "curve"}
SIGNED_TYPES = {8, 9, 10}      # MtProperty s8, s16, s32


@dataclass
class Track:
    kind: int
    type: int = 0                   # MtProperty type of the driven property
    parent: int = 0
    name: object = None             # str (or bytes when it is not text); None for kinds without a name
    index: int = 0                  # DTI id (unit/system tracks) or array index
    keys: list = field(default_factory=list)      # (frame, mode)
    values: list = field(default_factory=list)    # one per key, by kind
    raw_name: int = 0               # the name field of a kind that has none (0 in every file)
    extra: tuple = (0, 0)           # DDO +0x10, +0x14


@dataclass
class Scheduler:
    version: int
    frame_max: int = 0
    floor_frame: int = 0
    flags: int = 0                  # the other 7 bits of the 0x0C word
    base_track: int = 0
    unk08: int = 0
    tracks: list = field(default_factory=list)

    @property
    def is_ddo(self) -> bool:
        return self.version == SDL_DDO


def _kinds(version: int) -> dict:
    return _KINDS[version]


def _kind_of(version: int, kind: int) -> str | None:
    """'int', 'vector', ... for a keyed kind; None for the others."""
    k = _kinds(version).get(kind)
    return k[0] if k else None


def _named(version: int, kind: int) -> bool:
    return kind in NAMED or kind in _kinds(version)


def _text(b: bytes):
    try:
        t = b.decode("cp932")
        if t.encode("cp932") == b:
            return t
    except UnicodeError:
        pass
    return bytes(b)


def _raw(v) -> bytes:
    if isinstance(v, bytes):
        return v
    try:
        return v.encode("cp932")
    except UnicodeEncodeError:
        raise FormatError("schedule", f"{v!r} has characters the game's encoding (Shift-JIS) cannot store") from None


def parse_sdl(data: bytes) -> Scheduler:
    data = bytes(data)
    if data[:4] != SDL_MAGIC:
        raise FormatError("sdl", f"not a scheduler (magic {data[:4]!r})", 0)
    if len(data) < _SDL_HEAD.size:
        raise FormatError("sdl", f"the header needs {_SDL_HEAD.size} bytes, the file has {len(data)}", 0)
    _, version, ntracks, unk08, word0c, base_track, sbase = _SDL_HEAD.unpack_from(data, 0)
    if version not in _KINDS:
        raise FormatError("sdl", f"version {version}; DDDA loads 19, Dragon's Dogma Online 22", 4)
    rec = _TRACK_DDO if version == SDL_DDO else _TRACK_DDDA
    table_end = _SDL_HEAD.size + ntracks * rec.size
    if table_end > len(data):
        raise FormatError("sdl", f"{ntracks} tracks do not fit in {len(data)} bytes", 6)
    if not table_end <= sbase <= len(data):
        raise FormatError("sdl", f"the string table offset 0x{sbase:x} is outside the file", 0x14)
    kinds = _kinds(version)
    rows = [rec.unpack_from(data, _SDL_HEAD.size + i * rec.size) for i in range(ntracks)]
    if version != SDL_DDO:
        rows = [r[:6] + (0, 0) + r[6:] for r in rows]           # DDDA's records have no mUnk10 / mUnk14
    # the layout first, as _build_sdl makes it: each keyed track's keys (4-aligned), then its values
    # (16-aligned), in track order, then the string table. Nothing is read before its place is checked, so
    # tracks cannot name the same bytes over and over.
    pos = table_end
    for i, (kind, _, count, _, _, _, _, _, koff, voff) in enumerate(rows):
        o = _SDL_HEAD.size + i * rec.size
        kv = kinds.get(kind)
        if kv is None:
            if count or koff or voff:
                raise FormatError("sdl", f"track {i} (kind {kind}) has keys; only kinds {min(kinds)}.."
                                         f"{max(kinds)} are keyed", o)
            continue
        if not count:
            if koff or voff:
                raise FormatError("sdl", f"track {i} has no keys but key/value offsets", o)
            continue
        if kv[1] is None:
            raise FormatError("sdl", f"track {i}: kind {kind} has keys, but its value size is UNKNOWN "
                                     "(no game file has one)", o)
        k = (pos + 3) & ~3
        v = (k + 4 * count + 15) & ~15
        pos = v + kv[1] * count
        if (koff, voff) != (k, v):
            raise FormatError("sdl", f"track {i}: keys at 0x{koff:x}, values at 0x{voff:x}; the game's writer "
                                     f"puts them at 0x{k:x}, 0x{v:x}", o + rec.size - 8)
        if pos > sbase:
            raise FormatError("sdl", f"track {i}: {count} keys run into the string table (0x{sbase:x})", o + 2)
    if sbase != (pos + 3) & ~3:
        raise FormatError("sdl", f"the string table is at 0x{sbase:x}; the game's writer puts it at "
                                 f"0x{(pos + 3) & ~3:x}", 0x14)
    sc = Scheduler(version, word0c & 0xFFFFFF, (word0c >> 24) & 1, word0c >> 25, base_track, unk08)
    texts = _Texts(data, sbase)
    for i, (kind, ptype, count, parent, name, index, x1, x2, koff, voff) in enumerate(rows):
        tr = Track(kind, ptype, parent, None, index, extra=(x1, x2))
        if _named(version, kind):
            tr.name = texts.get(name, False, f"track {i}'s name", _SDL_HEAD.size + i * rec.size + 8)
        else:
            tr.raw_name = name
        if count:
            what, size = kinds[kind]
            tr.keys = [(k & 0xFFFFFF, k >> 24) for k in struct.unpack_from(f"<{count}I", data, koff)]
            tr.values = _read_values(data, what, size, count, voff)
            if what in ("resource", "string"):
                tr.values = [None if off == 0 else texts.get(off, what == "resource", f"track {i}'s value {j}",
                                                             voff + 4 * j) for j, off in enumerate(tr.values)]
        sc.tracks.append(tr)
    # the string table (and every padding byte) is checked by writing it again the writer's way
    if build_sdl(sc) != data:
        raise FormatError("sdl", "the file is not laid out the way the game's writer lays it out "
                                 "(padding, alignment or string table)")
    return sc


def _read_values(data, what, size, count, voff) -> list:
    """A keyed track's values; a resource or string value is still its string-table offset (0 = none)."""
    raw = data[voff:voff + size * count]
    if what == "bool":
        return list(raw)
    if what == "vector":
        return [struct.unpack_from("<4I", raw, 16 * j) for j in range(count)]
    if what == "matrix":
        return [struct.unpack_from("<16I", raw, 64 * j) for j in range(count)]
    return list(struct.unpack(f"<{count}I", raw))


# The YAML writes every reference's text in full, and the model holds a text per string-table offset. In the
# game's files every reference together names at most 0.37 of the file (its longest text: 51 bytes); a file
# naming more than TEXT_LIMIT times itself (a key per frame could name a 120-byte path) is refused.
TEXT_LIMIT = 16                     # times the file


class _Texts:
    """The texts the tracks name: each read once per string-table offset and shared by every track naming
    it, all the references together held to TEXT_LIMIT times the file."""

    def __init__(self, data: bytes, sbase: int):
        self.data = data
        self.sbase = sbase
        self.seen = {}              # (offset, resource?) -> (bytes, the model's value)
        self.total = 0              # bytes of text every reference so far names

    def get(self, off: int, resource: bool, what: str, at: int):
        got = self.seen.get((off, resource))
        if got is None:
            p = self.sbase + off
            e = self.data.find(b"\0", p + 4 if resource else p) if p < len(self.data) else -1
            if e < 0:
                raise FormatError("sdl", f"{what} (string 0x{off:x}) is not a text of the string table", at)
            b = self.data[p:e]
            got = self.seen[(off, resource)] = (len(b), (struct.unpack_from("<I", b)[0], _text(b[4:])) if resource
                                                else _text(b))
        self.total += got[0]
        if self.total > TEXT_LIMIT * len(self.data):
            raise FormatError("sdl", f"{what}: the texts the tracks name add up to more than {TEXT_LIMIT} times the "
                                     "file", at)
        return got[1]


_PRIME: list = []                   # the modulus of _Strings' hash: a random 61-bit prime, chosen once


def _hash_prime() -> int:
    """A random prime, so no text can be chosen to collide with another on purpose."""
    while not _PRIME:
        n = secrets.randbits(61) | 1 << 60 | 1
        if _is_prime(n):
            _PRIME.append(n)
    return _PRIME[0]


def _is_prime(n: int) -> bool:
    """Miller-Rabin with the first 12 primes as bases: exact for every n below 3.3e24."""
    bases = (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37)
    if n < 2 or any(n % q == 0 for q in bases):
        return n in bases
    d, s = n - 1, 0
    while not d & 1:
        d, s = d >> 1, s + 1
    for a in bases:
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(s - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


class _Strings:
    """The game's writer: search the table (minus the entry appended last) for the bytes + NUL, else append.

    Given every text it will be asked for, it answers the search from an index instead of reading the table
    again: when an entry stops being the newest, the first place each text asked for occurs there is noted.
    A text without a NUL occurs as the tail of a run of bytes between two NULs (found by a rolling hash of
    the run's tails -- the tail as a number modulo a random prime -- then compared); one with a NUL (a type
    id with a zero byte) ends with a whole run, and is compared with the run and the bytes before it. So a
    search costs its text, not the table, and gives what table.find(text + NUL, 0, last) gives (tests compare
    the two; the corpus proves the offsets)."""

    def __init__(self, texts=()):
        self.table = bytearray()
        self.last = 0                                   # the newest entry starts here
        self.first = {}                                 # text -> where it first occurs, ending before `last`
        self.asked = frozenset(texts)
        self.prime = _hash_prime()
        self.plain = {}                                 # hash -> texts without a NUL not found yet
        self.longest = 0
        self.empty = b"" in self.asked                  # the empty text: found at the first NUL
        self.wide = {}                                  # texts with a NUL not found yet -> None
        for t in self.asked:
            if b"\0" in t:
                self.wide[t] = None
            elif t:
                self.plain.setdefault(int.from_bytes(t, "big") % self.prime, []).append(t)
                self.longest = max(self.longest, len(t))
        self.wide_heads = sorted({t.rindex(0) + 1 for t in self.wide})    # bytes up to a wide text's last NUL
        self.wide_sizes = {len(t) for t in self.wide}

    def add(self, b: bytes) -> int:
        f = self.first.get(b)
        if f is not None:
            return f
        if b not in self.asked:                         # a text nobody declared: the plain search
            f = self.table.find(b + b"\0", 0, self.last)
            if f >= 0:
                return f
        at = len(self.table)
        self.table += b + b"\0"
        self._searchable(self.last, at)                 # the entry that was the newest
        self.last = at
        return at

    def _searchable(self, start: int, end: int) -> None:
        tb, p = self.table, start
        while p < end:
            q = tb.index(0, p, end)                     # every entry ends with a NUL
            self._run(p, q)
            p = q + 1

    def _run(self, r: int, p: int) -> None:
        """Note the texts first occurring at the run table[r:p] and the NUL at p."""
        tb, first = self.table, self.first
        if self.empty:
            first[b""] = p
            self.empty = False
        if self.plain:
            h, pw, prime, plain = 0, 1, self.prime, self.plain
            for m in range(1, min(p - r, self.longest) + 1):
                h = (tb[p - m] * pw + h) % prime        # the run's last m bytes as a number, modulo the prime
                pw = pw * 256 % prime
                ts = plain.get(h)
                if ts:
                    t = next((t for t in ts if len(t) == m and tb[p - m:p] == t), None)
                    if t is not None:
                        first[t] = p - m
                        ts.remove(t)
                        if not ts:
                            del plain[h]
        if self.wide:
            for k in self.wide_heads:
                if k <= r and p - r + k in self.wide_sizes:
                    t = bytes(tb[r - k:p])
                    if t in self.wide:
                        first[t] = r - k
                        del self.wide[t]


def build_sdl(sc: Scheduler) -> bytes:
    try:
        return _build_sdl(sc)
    except (struct.error, TypeError, ValueError, KeyError, AttributeError) as e:
        raise FormatError("sdl", f"a value does not fit its field ({e})") from None


def _build_sdl(sc: Scheduler) -> bytes:
    if sc.version not in _KINDS:
        raise FormatError("sdl", f"version {sc.version} is not one the games load (19, 22)")
    ddo = sc.version == SDL_DDO
    rec = _TRACK_DDO if ddo else _TRACK_DDDA
    kinds = _kinds(sc.version)
    n = len(sc.tracks)
    if n > 0xFFFF:
        raise FormatError("sdl", f"{n} tracks; the count is 16-bit")
    # data layout
    pos = _SDL_HEAD.size + n * rec.size
    offs = []
    for i, tr in enumerate(sc.tracks):
        kv = kinds.get(tr.kind)
        if tr.keys and kv is None:
            raise FormatError("sdl", f"track {i}: kind {tr.kind} holds no keys")
        if tr.keys and kv[1] is None:
            raise FormatError("sdl", f"track {i}: kind {tr.kind}'s value size is UNKNOWN; it cannot hold keys")
        if len(tr.values) != len(tr.keys):
            raise FormatError("sdl", f"track {i}: {len(tr.keys)} keys but {len(tr.values)} values")
        if len(tr.keys) > 0xFFFF:
            raise FormatError("sdl", f"track {i}: {len(tr.keys)} keys; the count is 16-bit")
        if tr.keys:
            k = (pos + 3) & ~3
            v = (k + 4 * len(tr.keys) + 15) & ~15
            offs.append((k, v))
            pos = v + kv[1] * len(tr.keys)
        else:
            offs.append((0, 0))
    sbase = (pos + 3) & ~3
    # strings: every text first (the writer is told them all), then the table in the writer's order -- every
    # name, then every resource and string value
    made = {}                       # one bytes object per text

    def raw(v, i: int, where: str) -> bytes:
        b = made.get(v)
        if b is None:
            b = _raw(v)
            if b"\0" in b:
                raise FormatError("sdl", f"track {i}: a NUL inside {where}")
            made[v] = b
        return b

    name_texts = []                 # per track: its name's bytes, or None (the field is raw_name)
    for i, tr in enumerate(sc.tracks):
        if sc.version != SDL_DDO and tuple(tr.extra) != (0, 0):
            # DDDA's 24-byte track records have no such words: the values would be lost (fuzz finding
            # schedule_yaml-invariant-cd274414ddc2)
            raise FormatError("sdl", f"track {i}: mUnk10 / mUnk14 exist only in Dragon's Dogma Online's schedulers")
        if _named(sc.version, tr.kind):
            if tr.name is None:
                raise FormatError("sdl", f"track {i} (kind {tr.kind}) needs a name")
            if tr.raw_name:             # the field holds the name's offset: the value would be lost
                raise FormatError("sdl", f"track {i} (kind {tr.kind}) has a name; raw_name is the name field "
                                         "of a kind without one")
            name_texts.append(raw(tr.name, i, "the name"))
        elif tr.name is not None:
            raise FormatError("sdl", f"track {i}: kind {tr.kind} has no name (the field is raw_name)")
        else:
            name_texts.append(None)
    value_texts = []                # per track: a resource / string track's value bytes (None: none), or None
    for i, tr in enumerate(sc.tracks):
        what = _kind_of(sc.version, tr.kind)
        if what in ("resource", "string") and tr.keys:
            vt = []
            for v in tr.values:
                if v is None:
                    vt.append(None)
                elif what == "resource":
                    tid, path = v
                    b = made.get((tid, path))
                    if b is None:
                        b = made[(tid, path)] = struct.pack("<I", tid) + raw(path, i, "a resource path")
                    vt.append(b)
                else:
                    vt.append(raw(v, i, "a string"))
            value_texts.append(vt)
        else:
            value_texts.append(None)
    texts = [b for b in name_texts if b is not None] + [b for vt in value_texts if vt for b in vt if b is not None]
    st = _Strings(texts)
    names = [tr.raw_name if b is None else st.add(b) for tr, b in zip(sc.tracks, name_texts)]
    words = []
    for i, vt in enumerate(value_texts):
        if vt is None:
            words.append(None)
            continue
        w = []
        for b in vt:
            off = 0 if b is None else st.add(b)
            if b is not None and off == 0:
                raise FormatError("sdl", f"track {i}: a value that lands on the table's first string "
                                         "reads back as none; the game cannot store it")
            w.append(off)
        words.append(w)
    out = bytearray()
    try:
        out += _SDL_HEAD.pack(SDL_MAGIC, sc.version, n, sc.unk08,
                              (sc.frame_max & 0xFFFFFF) | (sc.floor_frame & 1) << 24 | sc.flags << 25,
                              sc.base_track, sbase)
        if not 0 <= sc.frame_max <= 0xFFFFFF or sc.floor_frame not in (0, 1) or not 0 <= sc.flags <= 0x7F:
            raise FormatError("sdl", "FrameMax is 24 bits, FloorFrame 0 or 1, the other flags 7 bits")
        for tr, (k, v), nm in zip(sc.tracks, offs, names):
            if ddo:
                out += rec.pack(tr.kind, tr.type, len(tr.keys), tr.parent, nm, tr.index, *tr.extra, k, v)
            else:
                out += rec.pack(tr.kind, tr.type, len(tr.keys), tr.parent, nm, tr.index, k, v)
        for i, (tr, (k, v), w) in enumerate(zip(sc.tracks, offs, words)):
            if not tr.keys:
                continue
            out += bytes(k - len(out))
            for frame, mode in tr.keys:
                if not 0 <= frame <= 0xFFFFFF or not 0 <= mode <= 0xFF:
                    raise FormatError("sdl", f"track {i}: a key's frame is 24 bits and its mode 8 bits")
                out += struct.pack("<I", frame | mode << 24)
            out += bytes(v - len(out))
            out += _pack_values(_kind_of(sc.version, tr.kind), tr.values, w, i)
        out += bytes(sbase - len(out))
    except struct.error as e:
        raise FormatError("sdl", f"a value does not fit its field ({e})") from None
    out += st.table
    if sum(len(b) for b in texts) > TEXT_LIMIT * len(out):
        raise FormatError("sdl", f"the texts the tracks name add up to more than {TEXT_LIMIT} times the file: "
                                 "parse would refuse it")
    return bytes(out)


def _pack_values(what, values, words, i) -> bytes:
    if words is not None:
        return struct.pack(f"<{len(words)}I", *words)
    if what == "bool":
        return bytes(values)
    if what == "vector":
        return b"".join(struct.pack("<4I", *v) for v in values)
    if what == "matrix":
        return b"".join(struct.pack("<16I", *v) for v in values)
    return struct.pack(f"<{len(values)}I", *(v & 0xFFFFFFFF if isinstance(v, int) else v for v in values))


def sdl_info(sc: Scheduler) -> str:
    keys = sum(len(t.keys) for t in sc.tracks)
    units = sum(1 for t in sc.tracks if t.kind in (2, 3))
    return (f"rScheduler v{sc.version}: {len(sc.tracks)} tracks ({units} units / objects), {keys} keys, "
            f"{sc.frame_max} frames")


# -- shared YAML helpers ---------------------------------------------------------------------------
def _ft(bits: int) -> str:
    from .params import f32_bits_text
    return f32_bits_text(bits)


def _txt_node(v, flow: bool = False):
    """Text as a plain scalar when that reads back unchanged (quoted otherwise); bytes that are not text
    as {hex: ...}; None as null."""
    from .yamlish import Map, Scalar, quote
    if v is None:
        return Scalar("null")
    if isinstance(v, bytes):
        return Map([(Scalar("hex"), Scalar(v.hex(), "double"))], flow=True)
    return Scalar(v, "plain" if v and quote(v, flow) == v and v != "null" else "double")


def _one_line(text: str) -> str:
    """Text for the header comment: what is not printable (a newline would end the comment) as a space."""
    return "".join(c if c.isprintable() else " " for c in text)


def _type_text(tid: int) -> str:
    from . import typemap
    known = typemap.BY_ID.get(tid)
    return known[0] if known and typemap.jamcrc(known[0]) == tid else f"0x{tid:08x}"


_CLASS_BY_ID: dict[int, str] = {}


def _class_note(tid: int) -> str | None:
    """The engine class a DTI id names, when Riftstone knows it (a comment in the YAML, never a value)."""
    from . import typemap, xfs
    if not _CLASS_BY_ID:
        _CLASS_BY_ID.update({typemap.jamcrc(n): n for n in UNIT_CLASSES + ZONE_CLASSES})
    return _CLASS_BY_ID.get(tid) or xfs.CLASS_NAMES.get(tid) or (typemap.BY_ID.get(tid) or (None,))[0]


def _num(v: int, text: str) -> str:
    """A number for a message: in decimal, or its text cut short past 64 bits (int -> str refuses over
    4,300 digits, and a hex number has no such limit)."""
    return str(v) if v.bit_length() <= 64 else repr(text.strip()[:16] + "...")


class _Y:
    """Typed reads from yamlish nodes with line-numbered errors."""

    def __init__(self, source):
        self.source = source

    def err(self, msg, node=None):
        return ParamError(msg, getattr(node, "line", None), getattr(node, "col", None), self.source)

    def get(self, m, key, what):
        from .yamlish import Map
        if not isinstance(m, Map):
            raise self.err(f"{what} must be a block of fields", m)
        v = m.get(key)
        if v is None:
            raise self.err(f"{what}: '{key}' is missing", m)
        return v

    def int(self, node, lo, hi, what, default=None):
        from .yamlish import Scalar
        if node is None and default is not None:
            return default
        if not isinstance(node, Scalar):
            raise self.err(f"{what} must be a whole number", node)
        try:
            v = int(node.text.strip(), 0)
        except ValueError:
            raise self.err(f"{what}: {node.text!r} is not a whole number", node) from None
        if not lo <= v <= hi:
            raise self.err(f"{what}: {_num(v, node.text)} is outside {lo}..{hi}", node)
        return v

    def f32(self, node, what):
        from .params import f32_bits
        from .yamlish import Scalar
        if not isinstance(node, Scalar):
            raise self.err(f"{what} must be a number", node)
        try:
            return f32_bits(node.text)
        except ValueError:
            raise self.err(f"{what}: {node.text!r} is not a 32-bit float", node) from None

    def seq(self, node, n, what):
        from .yamlish import Seq
        if not isinstance(node, Seq) or (n is not None and len(node.items) != n):
            raise self.err(f"{what} is a list of {n} values" if n is not None else f"{what} is a list", node)
        return node.items

    def fvec(self, node, n, what):
        return tuple(self.f32(x, what) for x in self.seq(node, n, what))

    def text(self, node, what, allow_null=False, allow_nul=False):
        from .yamlish import Map, Scalar
        if isinstance(node, Scalar):
            if allow_null and node.text == "null" and node.style == "plain":
                return None
            if "\0" in node.text and not allow_nul:
                raise self.err(f"{what}: a NUL inside the text", node)
            try:
                b = node.text.encode("cp932")
            except UnicodeEncodeError:
                raise self.err(f"{what}: characters the game's encoding (Shift-JIS) cannot store; "
                               "write {hex: ...} for raw bytes", node) from None
        elif isinstance(node, Map) and node.get("hex") is not None:
            try:
                if len(node.items) != 1:
                    raise ValueError
                b = bytes.fromhex(node.get("hex").text)
            except (ValueError, AttributeError):
                raise self.err(f'{what}: raw bytes are {{hex: "..."}}, pairs of hex digits', node) from None
        else:
            raise self.err(f"{what} must be text", node)
        # the form parse gives these bytes, so YAML -> model -> bytes -> model is stable: {hex: ...} of text
        # is text, and text stored as other characters' bytes ('¬' as 81 CA) is what those bytes read as ('￢')
        return _text(b)

    def type_id(self, node, what):
        from . import typemap
        from .yamlish import Scalar
        if not isinstance(node, Scalar):
            raise self.err(f"{what} must be a resource type name or 0x... id", node)
        t = node.text.strip()
        if t.lower().startswith("0x"):
            return self.int(node, 0, 0xFFFFFFFF, what)
        tid = typemap.jamcrc(t) if t.isascii() else None
        if tid is None or tid not in typemap.BY_ID:
            raise self.err(f"{what}: {t!r} is not a resource type Riftstone knows; write its id as 0x...", node)
        return tid

    def only(self, m, allowed, what):
        from .yamlish import Map
        if isinstance(m, Map):
            for k, _ in m.items:
                if k.text not in allowed:
                    raise self.err(f"{what}: unexpected field '{k.text}'", k)


def _hex(v: int) -> str:
    return f"0x{v:08x}"


# -- scheduler YAML ----------------------------------------------------------------------------------
def _sdl_value(what: str, ptype: int, v):
    from .yamlish import Map, Scalar, Seq
    if what == "float":
        return Scalar(_ft(v))
    if what in ("vector", "matrix"):
        return Seq([Scalar(_ft(x)) for x in v], flow=True)
    if what == "resource":
        if v is None:
            return Scalar("null")
        return Map([(Scalar("type"), Scalar(_type_text(v[0]))), (Scalar("path"), _txt_node(v[1], True))], flow=True)
    if what == "string":
        return _txt_node(v, True)
    if what == "int" and ptype in SIGNED_TYPES:
        return Scalar(str(v - (1 << 32) if v >= 1 << 31 else v))
    return Scalar(str(v))


def sdl_to_yaml(sc: Scheduler, name: str | None = None) -> str:
    from . import yamlish
    from .yamlish import Map, Scalar, Seq

    ddo = sc.is_ddo
    kinds = "6 int, 8 vector, 9 float, 11 bool, 12 unit ref, 13 resource, 14 string, 15 event, 16 matrix" if ddo \
        else "6 int, 7 vector, 8 float, 9 bool, 10 unit ref, 11 resource, 12 string, 13 event, 14 matrix"
    head = ["Riftstone scheduler (.sdl, rScheduler" + (", Dragon's Dogma Online)" if ddo else ")")
            + (f" -- {_one_line(name)}" if name else ""),
            f"{len(sc.tracks)} tracks. kind: 1 root, 2 unit, 3 system object, 4 spacer, 5 sub-object; keyed: {kinds}.",
            "type is the driven property's MtProperty type; parent is a unit's move line, or the track that",
            "owns a property; index is a unit's class id or a property's array index. A key is {frame, mode,",
            "value}; mode 0 hold, 1/4 count up, 2 trigger, 3 linear, 5 curve. Absent numbers are 0.",
            "Names and paths are text; the string table is rebuilt the game's way. Rebuilds byte-for-byte."]
    items = [(Scalar("riftstone"), Scalar(SDL_TAG))]
    if name:
        items.append((Scalar("resource"), Scalar(name, "double")))
    items += [(Scalar("version"), Scalar(str(sc.version))), (Scalar("FrameMax"), Scalar(str(sc.frame_max))),
              (Scalar("FloorFrame"), Scalar(str(sc.floor_frame)))]
    if sc.flags:
        items.append((Scalar("flags"), Scalar(str(sc.flags))))
    items += [(Scalar("BaseTrack"), Scalar(str(sc.base_track))), (Scalar("mUnk08"), Scalar(_hex(sc.unk08)))]
    tracks = []
    for i, tr in enumerate(sc.tracks):
        f = [(Scalar("kind"), Scalar(str(tr.kind), comment=f"#{i} " + (KIND_NAMES.get(tr.kind)
                                                                       or _kind_of(sc.version, tr.kind) or "?")))]
        if tr.type:
            f.append((Scalar("type"), Scalar(str(tr.type))))
        if tr.parent:
            f.append((Scalar("parent"), Scalar(str(tr.parent))))
        if tr.name is not None:
            f.append((Scalar("name"), _txt_node(tr.name)))
        if tr.raw_name:
            f.append((Scalar("raw_name"), Scalar(str(tr.raw_name))))
        if tr.index:
            if tr.kind in (2, 3):
                f.append((Scalar("index"), Scalar(_hex(tr.index), comment=_class_note(tr.index))))
            else:
                f.append((Scalar("index"), Scalar(str(tr.index))))
        if tr.extra != (0, 0):
            f += [(Scalar("mUnk10"), Scalar(str(tr.extra[0]))), (Scalar("mUnk14"), Scalar(str(tr.extra[1])))]
        what = _kind_of(sc.version, tr.kind)
        if what is not None:
            keys = [Map([(Scalar("frame"), Scalar(str(fr))), (Scalar("mode"), Scalar(str(md))),
                         (Scalar("value"), _sdl_value(what, tr.type, v))], flow=True)
                    for (fr, md), v in zip(tr.keys, tr.values)]
            f.append((Scalar("keys"), Seq(keys, flow=not keys)))
        tracks.append(Map(f))
    items.append((Scalar("tracks"), Seq(tracks, flow=not tracks)))
    return yamlish.emit(Map(items), head)


_TRACK_KEYS = ("kind", "type", "parent", "name", "raw_name", "index", "mUnk10", "mUnk14", "keys")


def _sdl_value_from(y: _Y, what: str, ptype: int, node, where: str):
    from .yamlish import Scalar
    if what == "float":
        return y.f32(node, where)
    if what == "vector":
        return y.fvec(node, 4, where)
    if what == "matrix":
        return y.fvec(node, 16, where)
    if what == "resource":
        if isinstance(node, Scalar) and node.text == "null" and node.style == "plain":
            return None
        y.only(node, ("type", "path"), where)
        return (y.type_id(y.get(node, "type", where), f"{where} type"), y.text(y.get(node, "path", where), f"{where} path"))
    if what == "string":
        return y.text(node, where, allow_null=True)
    if what == "bool":
        return y.int(node, 0, 255, where)
    if what == "int" and ptype in SIGNED_TYPES:
        return y.int(node, -(1 << 31), (1 << 31) - 1, where) & 0xFFFFFFFF
    return y.int(node, 0, 0xFFFFFFFF, where)


def sdl_from_yaml(text: str, source: str | None = None) -> Scheduler:
    from . import yamlish
    from .yamlish import Map, Scalar

    y = _Y(source)
    doc = yamlish.parse(text, source)
    tag = doc.get("riftstone") if isinstance(doc, Map) else None
    if not isinstance(tag, Scalar) or tag.text != SDL_TAG:
        raise ParamError(f"not a Riftstone scheduler (expected 'riftstone: {SDL_TAG}')", 1, 1, source)
    y.only(doc, ("riftstone", "resource", "version", "FrameMax", "FloorFrame", "flags", "BaseTrack", "mUnk08",
                 "tracks"), "the file")
    version = y.int(y.get(doc, "version", "the file"), 0, 0xFFFF, "version")
    if version not in _KINDS:
        raise y.err(f"version {version}: schedulers are 19 (DDDA) or 22 (Dragon's Dogma Online)", doc.get("version"))
    sc = Scheduler(version, y.int(y.get(doc, "FrameMax", "the file"), 0, 0xFFFFFF, "FrameMax"),
                   y.int(y.get(doc, "FloorFrame", "the file"), 0, 1, "FloorFrame"),
                   y.int(doc.get("flags"), 0, 0x7F, "flags", 0),
                   y.int(y.get(doc, "BaseTrack", "the file"), 0, 0xFFFFFFFF, "BaseTrack"),
                   y.int(y.get(doc, "mUnk08", "the file"), 0, 0xFFFFFFFF, "mUnk08"))
    for i, tn in enumerate(y.seq(y.get(doc, "tracks", "the file"), None, "tracks")):
        where = f"track {i}"
        y.only(tn, _TRACK_KEYS, where)
        kind = y.int(y.get(tn, "kind", where), 0, 255, f"{where} kind")
        tr = Track(kind, y.int(tn.get("type"), 0, 255, f"{where} type", 0),
                   y.int(tn.get("parent"), 0, 0xFFFFFFFF, f"{where} parent", 0), None,
                   y.int(tn.get("index"), 0, 0xFFFFFFFF, f"{where} index", 0),
                   raw_name=y.int(tn.get("raw_name"), 0, 0xFFFFFFFF, f"{where} raw_name", 0),
                   extra=(y.int(tn.get("mUnk10"), 0, 0xFFFFFFFF, f"{where} mUnk10", 0),
                          y.int(tn.get("mUnk14"), 0, 0xFFFFFFFF, f"{where} mUnk14", 0)))
        if _named(version, kind):
            tr.name = y.text(y.get(tn, "name", where), f"{where} name")
            if tn.get("raw_name") is not None:
                raise y.err(f"{where}: kind {kind} has a name; raw_name is the name field of a kind without one",
                            tn.get("raw_name"))
        elif tn.get("name") is not None:
            raise y.err(f"{where}: kind {kind} has no name", tn.get("name"))
        what = _kind_of(version, kind)
        kn = tn.get("keys")
        if what is None:
            if kn is not None and y.seq(kn, None, f"{where} keys"):
                raise y.err(f"{where}: kind {kind} holds no keys", kn)
        elif kn is not None:
            for k, key in enumerate(y.seq(kn, None, f"{where} keys")):
                w = f"{where} key {k}"
                y.only(key, ("frame", "mode", "value"), w)
                tr.keys.append((y.int(y.get(key, "frame", w), 0, 0xFFFFFF, f"{w} frame"),
                                y.int(y.get(key, "mode", w), 0, 255, f"{w} mode")))
                tr.values.append(_sdl_value_from(y, what, tr.type, y.get(key, "value", w), f"{w} value"))
        sc.tracks.append(tr)
    try:
        build_sdl(sc)
    except FormatError as e:
        raise ParamError(str(e), None, None, source) from None
    return sc


# ====================================================================================================
# rZone -- ``.zon``
# ====================================================================================================
ZON_MAGIC = b"zon\0"
ZON_DDDA = 0x77DF25C4
ZON_DDO = 0x782A0684
ZON_TAG = "zon/1"
GRID_MAGIC = b"grco"
GRID_VERSION = 0x77C09C94
SHAPE_NAMES = ("Base", "Area", "AABB", "OBB", "Sphere", "Capsule", "Cylinder", "Point", "Line", "Panel", "Cone",
               "Global")
_SHAPE_BASE = [("s", "mDecay", "f32"), ("s", "mIsNativeData", "u8")]
_SHAPES = {
    0: [], 11: [],
    1: [("s", "mHeight", "f32"), ("s", "mBottom", "f32"), ("a", "mVertex", "f32", 16),
        ("a", "mConcaveCrossPos", "f32", 4), ("s", "mFlgConvex", "u8"), ("s", "mConcaveStatus", "u32")],
    2: [("a", "mAABB", "f32", 8)],
    3: [("a", "mOBB", "f32", 20), ("s", "mDecayY", "f32"), ("s", "mDecayZ", "f32"),
        ("s", "mIsEnableExtendedDecay", "u8")],
    4: [("a", "mSphere", "f32", 4)],
    5: [("a", "mCapsule", "f32", 12)],
    6: [("a", "mCylinder", "f32", 12)],
    7: [("a", "mPos", "f32", 4)],
    8: [("a", "Position0", "f32", 4), ("a", "Position1", "f32", 4)],
    9: [("a", "mVertex", "f32", 16)],
    10: [("s", "mHeight", "f32"), ("s", "mTopRadius", "f32"), ("a", "mPos", "f32", 4), ("s", "mBottomRadius", "f32")],
}
_AABB_DDO = [("s", "mDecayY", "f32"), ("s", "mDecayZ", "f32"), ("s", "mIsEnableExtendedDecay", "u8")]
_LAYOUT_HEAD = [("s", "mPriority", "i32")]
_LAYOUT_TAIL = [("s", "mContentsPoolID", "i32"), ("s", "mContentsPoolGroupID", "i32"), ("s", "mIsEnable", "u8"),
                ("s", "mIndex", "u32"), ("s", "mLayoutGroupIndex", "u32"), ("s", "mIndexOfLayoutGroup", "u32"),
                ("s", "mUniqueID", "u32"), ("s", "mGroupID", "i32")]
_ST = {"u8": struct.Struct("<B"), "u32": struct.Struct("<I"), "i32": struct.Struct("<i"), "f32": struct.Struct("<I")}
_I32 = (-(1 << 31), (1 << 31) - 1)
_U32R = (0, 0xFFFFFFFF)
_RANGES = {"u8": (0, 255), "u32": _U32R, "i32": _I32}
BOUNDS_SIZE = 28          # a box per layout of a grid: f32 min x y z, u32 its number, f32 max x y z


def shape_fields(kind: int, ddo: bool) -> list:
    """The fields of a layout's shape, in file order (every shape starts with mDecay, mIsNativeData)."""
    extra = _AABB_DDO if ddo and kind == 2 else []
    return _SHAPE_BASE + _SHAPES[kind] + extra


@dataclass
class Grid:
    """cGridCollision (the packed form, the only one the games' files use)."""
    aabb: tuple = (0,) * 8            # f32 bits: min x y z w, max x y z w
    nx: int = 0
    nz: int = 0
    index_type: int = 0               # 0: u32 indices, 1: u16 indices
    cells: list = field(default_factory=list)      # nx * nz pairs of u32
    indices: list = field(default_factory=list)


@dataclass
class Layout:
    shape: int                        # index into SHAPE_NAMES
    fields: dict = field(default_factory=dict)     # mPriority, the shape's fields, mContentsPoolID ... mGroupID
    extend: bytes | None = None       # mpExtendObj: an XFS object, or None


@dataclass
class ZoneGroup:
    mUnk08: int = 0
    mUnk0C: int = 0
    layout_index: list = field(default_factory=list)          # getGroupLayoutIndex
    global_layout_index: list = field(default_factory=list)   # getGroupGlobalLayoutIndex
    grid: Grid | None = None          # type 1 zones
    bounds: list = field(default_factory=list)                # per layout_index entry, with a grid


@dataclass
class Zone:
    version: int
    zone_type: int = 0
    name: object = ""
    contents_class: int = 0
    mUnk64: int = 0
    contents_num: int = 0
    contents: bytes = b""             # cContentsPool: an XFS object tree
    layouts: list = field(default_factory=list)
    groups: list = field(default_factory=list)
    grid: Grid | None = None          # type 2 zones
    layout_bounds: list | None = None # type 2 zones, one per layout (None when the file has none)
    unique_index: list = field(default_factory=list)          # getLayoutIndexFromUniqueID's table
    tail: list = field(default_factory=list)                  # Dragon's Dogma Online's first table (UNKNOWN)

    @property
    def is_ddo(self) -> bool:
        return self.version == ZON_DDO


class _R:
    def __init__(self, data: bytes, p: int = 0):
        self.d = data
        self.p = p

    def take(self, n: int, what: str) -> bytes:
        if n < 0 or self.p + n > len(self.d):
            raise FormatError("zon", f"{what} runs past the end of the file", self.p)
        v = self.d[self.p:self.p + n]
        self.p += n
        return v

    def u32(self, what: str) -> int:
        return struct.unpack("<I", self.take(4, what))[0]

    def count(self, what: str, size: int) -> int:
        n = self.u32(what)
        if size and n > (len(self.d) - self.p) // size:
            raise FormatError("zon", f"{what}: {n} items cannot fit in the file", self.p - 4)
        return n

    def u32s(self, n: int, what: str) -> list:
        return list(struct.unpack(f"<{n}I", self.take(4 * n, what)))

    def fields(self, spec, what: str) -> dict:
        out = {}
        for f in spec:
            st = _ST[f[2]]
            if f[0] == "s":
                out[f[1]] = st.unpack(self.take(st.size, f"{what} {f[1]}"))[0]
            else:
                out[f[1]] = list(struct.unpack(f"<{f[3]}{st.format[-1]}", self.take(st.size * f[3], f"{what} {f[1]}")))
        return out

    def xfs(self, what: str, version: int) -> bytes:
        d, p = self.d, self.p
        if d[p:p + 4] != b"XFS\0":
            raise FormatError("zon", f"{what}: no XFS object here", p)
        if p + 0x18 > len(d):
            raise FormatError("zon", f"{what}: the XFS header runs past the end", p)
        ver = struct.unpack_from("<H", d, p + 4)[0]
        want = 0x0109 if version == ZON_DDDA else 0x000F
        if ver != want:
            raise FormatError("zon", f"{what}: XFS version 0x{ver:04x}, expected 0x{want:04x}", p + 4)
        head = 0x14 if ver == 0x0109 else 0x18
        (defsize,) = struct.unpack_from("<I", d, p + head - 4)
        root = p + head + defsize
        if root + 8 > len(d):
            raise FormatError("zon", f"{what}: the XFS definitions run past the end", p)
        (size,) = struct.unpack_from("<I", d, root + 4)
        if size < 4:
            raise FormatError("zon", f"{what}: the XFS root object claims {size} bytes", root + 4)
        return self.take(root + 4 + size - p, what)


def _write_fields(out: bytearray, spec, values: dict, what: str) -> None:
    for f in spec:
        st = _ST[f[2]]
        v = values.get(f[1])
        if v is None:
            raise FormatError("zon", f"{what}: '{f[1]}' is missing")
        if f[0] == "s":
            out += st.pack(v)
        else:
            if len(v) != f[3]:
                raise FormatError("zon", f"{what}: {f[1]} holds {f[3]} values, not {len(v)}")
            out += struct.pack(f"<{f[3]}{st.format[-1]}", *v)


def _read_grid(r: _R, what: str) -> Grid:
    r.take(4, what)
    ver = r.u32(what)
    if ver != GRID_VERSION:
        raise FormatError("zon", f"{what}: grid version 0x{ver:08x}, the games load 0x{GRID_VERSION:08x}", r.p - 4)
    g = Grid(tuple(struct.unpack("<8I", r.take(32, what))))
    g.nx, g.nz = struct.unpack("<HH", r.take(4, what))
    g.index_type, packed = r.take(2, what)
    if packed != 1:
        raise FormatError("zon", f"{what}: packed flag {packed}; every game file writes 1 (the unpacked form's "
                                 "layout is UNKNOWN)", r.p - 1)
    if g.index_type not in (0, 1):
        raise FormatError("zon", f"{what}: grid index type {g.index_type} (0 u32, 1 u16 are known)", r.p - 2)
    ncell = g.nx * g.nz
    if not ncell:           # a header's 0 cells mean no grid; build refuses one without cells
        raise FormatError("zon", f"{what}: a grid of {g.nx} x {g.nz} cells (every game file's grids have cells)",
                          r.p - 6)
    if ncell * 8 > len(r.d) - r.p:
        raise FormatError("zon", f"{what}: {ncell} grid cells cannot fit in the file", r.p)
    cells = struct.unpack(f"<{2 * ncell}I", r.take(8 * ncell, what))
    g.cells = [(cells[2 * i], cells[2 * i + 1]) for i in range(ncell)]
    size = 4 if g.index_type == 0 else 2
    n = r.count(f"{what} index count", size)
    g.indices = list(struct.unpack(f"<{n}{'I' if size == 4 else 'H'}", r.take(size * n, what)))
    return g


def _write_grid(out: bytearray, g: Grid, what: str) -> None:
    if len(g.aabb) != 8:
        raise FormatError("zon", f"{what}: the grid's aabb is 8 floats")
    if g.index_type not in (0, 1):
        raise FormatError("zon", f"{what}: grid index type {g.index_type} (0 u32, 1 u16)")
    if len(g.cells) != g.nx * g.nz:
        raise FormatError("zon", f"{what}: {g.nx} x {g.nz} grid needs {g.nx * g.nz} cells, not {len(g.cells)}")
    out += GRID_MAGIC + struct.pack("<I8IHHBB", GRID_VERSION, *g.aabb, g.nx, g.nz, g.index_type, 1)
    for a, b in g.cells:
        out += struct.pack("<II", a, b)
    out += struct.pack("<I", len(g.indices))
    out += struct.pack(f"<{len(g.indices)}{'I' if g.index_type == 0 else 'H'}", *g.indices)


def _read_bounds(r: _R, n: int, what: str) -> list:
    """n boxes: f32 min x y z, u32 its own position, f32 max x y z (the position is checked, not stored)."""
    raw = r.take(BOUNDS_SIZE * n, what)
    out = []
    for i in range(n):
        v = struct.unpack_from("<7I", raw, BOUNDS_SIZE * i)
        if v[3] != i:
            raise FormatError("zon", f"{what} {i}: its number is {v[3]} (every game file numbers them in order)",
                              r.p - BOUNDS_SIZE * (n - i) + 12)
        out.append(v[:3] + v[4:])
    return out


def _write_bounds(out: bytearray, bounds: list, what: str) -> None:
    for i, b6 in enumerate(bounds):
        if len(b6) != 6:
            raise FormatError("zon", f"{what} {i}: a box is min x y z, max x y z")
        out += struct.pack("<7I", *b6[:3], i, *b6[3:])


def parse_zon(data: bytes) -> Zone:
    data = bytes(data)
    if data[:4] != ZON_MAGIC:
        raise FormatError("zon", f"not a zone (magic {data[:4]!r})", 0)
    r = _R(data, 4)
    version = r.u32("the header")
    if version not in (ZON_DDDA, ZON_DDO):
        raise FormatError("zon", f"version 0x{version:08x}; DDDA loads 0x{ZON_DDDA:08x}, Online 0x{ZON_DDO:08x}", 4)
    ddo = version == ZON_DDO
    z = Zone(version, r.u32("the zone type"))
    if z.zone_type not in (0, 1, 2):
        raise FormatError("zon", f"zone type {z.zone_type} (the loaders know 0, 1 and 2)", 8)
    nlen = r.count("the name", 1)
    z.name = _text(r.take(nlen, "the name"))
    z.contents_class, z.mUnk64 = r.u32("the header"), r.u32("the header")
    # cMemoryHeader
    nl = r.count("the layout list", 4)
    shapes = r.u32s(nl, "the layout shapes")
    for i, s in enumerate(shapes):
        if s >= len(SHAPE_NAMES):
            raise FormatError("zon", f"layout {i}: shape {s} (the loader knows 0..11)", r.p - 4 * (nl - i))
    per = 16 if z.zone_type == 1 else 8
    ng = r.count("the group list", per)
    ginfo = [r.u32s(per // 4, "a group's sizes") for _ in range(ng)]
    z.contents_num = r.u32("the header")
    n_tail = r.u32("the header") if ddo else 0          # Online: the UNKNOWN table comes first
    n_unique = r.u32("the header")
    grid_sizes = r.u32s(2, "the header") if z.zone_type == 2 else None
    z.contents = r.xfs("the contents pool", version)
    for i, s in enumerate(shapes):
        w = f"layout {i}"
        lay = Layout(s, r.fields(_LAYOUT_HEAD + shape_fields(s, ddo) + _LAYOUT_TAIL, w))
        flag = r.take(1, w)[0]
        if flag not in (0, 1):
            raise FormatError("zon", f"{w}: mpExtendObj flag {flag} (the game's files write 0 or 1)", r.p - 1)
        if flag:
            lay.extend = r.xfs(f"{w} mpExtendObj", version)
        z.layouts.append(lay)
    for i, gi in enumerate(ginfo):
        w = f"group {i}"
        g = ZoneGroup(r.u32(w), struct.unpack("<i", r.take(4, w))[0])
        if gi[0] > (len(data) - r.p) // 4 or gi[1] > (len(data) - r.p) // 4:
            raise FormatError("zon", f"{w}: its layout lists cannot fit in the file", r.p)
        g.layout_index = r.u32s(gi[0], w)
        g.global_layout_index = r.u32s(gi[1], w)
        if z.zone_type == 1:
            if gi[3]:
                if data[r.p:r.p + 4] != GRID_MAGIC:
                    raise FormatError("zon", f"{w}: its grid is missing", r.p)
                g.grid = _read_grid(r, f"{w} grid")
                if (len(g.grid.indices), len(g.grid.cells)) != (gi[2], gi[3]):
                    raise FormatError("zon", f"{w}: the header's grid sizes {gi[2:]} do not match the grid", r.p)
                if len(g.layout_index) * BOUNDS_SIZE > len(data) - r.p:
                    raise FormatError("zon", f"{w}: its bounds cannot fit in the file", r.p)
                g.bounds = _read_bounds(r, len(g.layout_index), f"{w} bounds")
            elif gi[2]:
                raise FormatError("zon", f"{w}: grid sizes {gi[2:]} without a grid", r.p)
        z.groups.append(g)
    if z.zone_type == 2:
        if data[r.p:r.p + 4] == GRID_MAGIC:
            z.grid = _read_grid(r, "the zone grid")
            if [len(z.grid.indices), len(z.grid.cells)] != grid_sizes:
                raise FormatError("zon", f"the header's grid sizes {grid_sizes} do not match the grid", r.p)
        elif grid_sizes != [0, 0]:
            raise FormatError("zon", f"the header states a grid {grid_sizes} but none follows", r.p)
    if n_unique + n_tail > (len(data) - r.p) // 4:
        raise FormatError("zon", f"the tables ({n_tail} + {n_unique} entries) run past the end", r.p)
    if z.zone_type == 2:
        left = len(data) - r.p - 4 * (n_unique + n_tail)
        if left == BOUNDS_SIZE * nl:
            z.layout_bounds = _read_bounds(r, nl, "the layout bounds")
        elif left != 0:
            raise FormatError("zon", f"{left} bytes before the tables fit neither 0 nor {nl} layout bounds", r.p)
    z.tail = r.u32s(n_tail, "the first table")
    z.unique_index = r.u32s(n_unique, "the unique-id table")
    if r.p != len(data):
        raise FormatError("zon", f"{len(data) - r.p} byte(s) after the last table", r.p)
    return z


def build_zon(z: Zone) -> bytes:
    try:
        return _build_zon(z)
    except (struct.error, TypeError, ValueError, KeyError, AttributeError) as e:
        raise FormatError("zon", f"a value does not fit its field ({e})") from None


def _build_zon(z: Zone) -> bytes:
    if z.version not in (ZON_DDDA, ZON_DDO):
        raise FormatError("zon", f"version 0x{z.version:08x} is not one the games load")
    if z.zone_type not in (0, 1, 2):
        raise FormatError("zon", f"zone type {z.zone_type} (0, 1 or 2)")
    ddo = z.is_ddo
    if not ddo and z.tail:
        raise FormatError("zon", "only Dragon's Dogma Online zones have the extra table (mUnkTable)")
    for what, blob in [("the contents pool", z.contents)] + [(f"layout {i} mpExtendObj", lay.extend)
                                                             for i, lay in enumerate(z.layouts) if lay.extend]:
        if _R(bytes(blob)).xfs(what, z.version) != blob:
            raise FormatError("zon", f"{what}: bytes after the XFS object")
    out = bytearray()
    try:
        name = _raw(z.name)
        out += ZON_MAGIC + struct.pack("<III", z.version, z.zone_type, len(name)) + name
        out += struct.pack("<II", z.contents_class, z.mUnk64)
        out += struct.pack("<I", len(z.layouts))
        for i, lay in enumerate(z.layouts):
            if not 0 <= lay.shape < len(SHAPE_NAMES):
                raise FormatError("zon", f"layout {i}: shape {lay.shape} (0..11)")
            out += struct.pack("<I", lay.shape)
        out += struct.pack("<I", len(z.groups))
        for i, g in enumerate(z.groups):
            out += struct.pack("<II", len(g.layout_index), len(g.global_layout_index))
            if z.zone_type == 1:
                if g.grid is not None and not g.grid.cells:
                    raise FormatError("zon", f"group {i}: a grid needs at least one cell")
                if g.grid is not None and len(g.bounds) != len(g.layout_index):
                    raise FormatError("zon", f"group {i}: a grid needs one bounds entry per GroupLayoutIndex entry")
                if g.grid is None and g.bounds:
                    raise FormatError("zon", f"group {i}: bounds come with a grid")
                out += struct.pack("<II", *((len(g.grid.indices), len(g.grid.cells)) if g.grid else (0, 0)))
            elif g.grid is not None or g.bounds:
                raise FormatError("zon", f"group {i}: only type 1 zones have group grids")
        out += struct.pack("<I", z.contents_num)
        if ddo:
            out += struct.pack("<I", len(z.tail))
        out += struct.pack("<I", len(z.unique_index))
        if z.zone_type == 2:
            if z.grid is not None and not z.grid.cells:
                raise FormatError("zon", "the zone grid needs at least one cell")
            out += struct.pack("<II", *((len(z.grid.indices), len(z.grid.cells)) if z.grid else (0, 0)))
        elif z.grid is not None or z.layout_bounds is not None:
            raise FormatError("zon", "only type 2 zones have a zone grid and layout bounds")
        out += z.contents
        for i, lay in enumerate(z.layouts):
            _write_fields(out, _LAYOUT_HEAD + shape_fields(lay.shape, ddo) + _LAYOUT_TAIL, lay.fields, f"layout {i}")
            out += b"\1" + lay.extend if lay.extend else b"\0"
        for i, g in enumerate(z.groups):
            out += struct.pack("<Ii", g.mUnk08, g.mUnk0C)
            out += struct.pack(f"<{len(g.layout_index)}I", *g.layout_index)
            out += struct.pack(f"<{len(g.global_layout_index)}I", *g.global_layout_index)
            if g.grid is not None:
                _write_grid(out, g.grid, f"group {i} grid")
                _write_bounds(out, g.bounds, f"group {i} bounds")
        if z.zone_type == 2:
            if z.grid is not None:
                _write_grid(out, z.grid, "the zone grid")
            tail_start = len(out)
            if z.layout_bounds is not None:
                if len(z.layout_bounds) != len(z.layouts):
                    raise FormatError("zon", "layout_bounds needs one entry per layout")
                _write_bounds(out, z.layout_bounds, "layout bounds")
            after = bytes(out[tail_start:tail_start + 4]) or (struct.pack("<I", (z.tail + z.unique_index)[0])
                                                                if z.tail + z.unique_index else b"")
            if z.grid is None and after == GRID_MAGIC:
                raise FormatError("zon", "without a zone grid the data after the groups cannot start with 'grco'")
        out += struct.pack(f"<{len(z.tail)}I", *z.tail)
        out += struct.pack(f"<{len(z.unique_index)}I", *z.unique_index)
    except struct.error as e:
        raise FormatError("zon", f"a value does not fit its field ({e})") from None
    except (TypeError, KeyError) as e:
        raise FormatError("zon", f"a field is malformed ({e})") from None
    return bytes(out)


def zon_info(z: Zone) -> str:
    from collections import Counter
    shapes = Counter(SHAPE_NAMES[lay.shape] for lay in z.layouts)
    grids = sum(1 for g in z.groups if g.grid) + (1 if z.grid else 0)
    return (f"rZone '{_raw(z.name).decode('latin-1')}' type {z.zone_type}: {len(z.layouts)} layouts ("
            + ", ".join(f"{k} x{v}" for k, v in shapes.most_common()) + f"), {len(z.groups)} groups, {grids} grid(s)")


def zone_xfs(z: Zone) -> list:
    """(label, Xfs or FormatError) for the zone's embedded XFS objects: the contents pool and each layout's
    mpExtendObj. riftstone.xfs decodes all but those with a 'color' (0x0F) property."""
    from . import xfs
    out = []
    for label, blob in [("contents", z.contents)] + [(f"layout {i}", lay.extend) for i, lay in enumerate(z.layouts)
                                                    if lay.extend]:
        try:
            out.append((label, xfs.parse(blob)))
        except FormatError as e:
            out.append((label, e))
    return out


# -- zone YAML ---------------------------------------------------------------------------------------
_HEX_CHUNK = 48


def _hex_node(blob: bytes):
    from .yamlish import Scalar, Seq
    return Seq([Scalar(blob[i:i + _HEX_CHUNK].hex(), "double") for i in range(0, len(blob), _HEX_CHUNK)])


def _hex_from(y: _Y, node, what: str) -> bytes:
    from .yamlish import Scalar
    parts = y.seq(node, None, what)
    if not all(isinstance(p, Scalar) for p in parts):
        raise y.err(f"{what}: a list of hex strings", node)
    try:
        return b"".join(bytes.fromhex(p.text) for p in parts)
    except ValueError:
        raise y.err(f"{what}: hex chunks must be pairs of hex digits", node) from None


def _field_nodes(spec, values: dict) -> list:
    from .yamlish import Scalar, Seq
    items = []
    for f in spec:
        v = values[f[1]]
        if f[0] == "s":
            items.append((Scalar(f[1]), Scalar(_ft(v) if f[2] == "f32" else str(v))))
        else:
            items.append((Scalar(f[1]), Seq([Scalar(_ft(x) if f[2] == "f32" else str(x)) for x in v], flow=True)))
    return items


def _fields_from(y: _Y, node, spec, what: str) -> dict:
    out = {}
    for f in spec:
        v = y.get(node, f[1], what)
        if f[2] == "f32":
            out[f[1]] = y.f32(v, f"{what} {f[1]}") if f[0] == "s" else list(y.fvec(v, f[3], f"{what} {f[1]}"))
        else:
            lo, hi = _RANGES[f[2]]
            out[f[1]] = (y.int(v, lo, hi, f"{what} {f[1]}") if f[0] == "s"
                         else [y.int(x, lo, hi, f"{what} {f[1]}") for x in y.seq(v, f[3], f"{what} {f[1]}")])
    return out


def _grid_node(g: Grid):
    from .yamlish import Map, Scalar, Seq
    return Map([(Scalar("aabb"), Seq([Scalar(_ft(b)) for b in g.aabb], flow=True, comment="min x y z w, max x y z w")),
                (Scalar("nx"), Scalar(str(g.nx))), (Scalar("nz"), Scalar(str(g.nz))),
                (Scalar("index_type"), Scalar(str(g.index_type), comment="0 u32, 1 u16")),
                (Scalar("cells"), Seq([Seq([Scalar(str(a)), Scalar(str(b))], flow=True) for a, b in g.cells],
                                      flow=True)),
                (Scalar("indices"), Seq([Scalar(str(i)) for i in g.indices], flow=True))])


def _grid_from(y: _Y, node, what: str) -> Grid:
    y.only(node, ("aabb", "nx", "nz", "index_type", "cells", "indices"), what)
    g = Grid(y.fvec(y.get(node, "aabb", what), 8, f"{what} aabb"),
             y.int(y.get(node, "nx", what), 0, 0xFFFF, f"{what} nx"),
             y.int(y.get(node, "nz", what), 0, 0xFFFF, f"{what} nz"),
             y.int(y.get(node, "index_type", what), 0, 1, f"{what} index_type"))
    for c in y.seq(y.get(node, "cells", what), None, f"{what} cells"):
        a, b = y.seq(c, 2, f"{what} cell")
        g.cells.append((y.int(a, *_U32R, f"{what} cell"), y.int(b, *_U32R, f"{what} cell")))
    top = 0xFFFFFFFF if g.index_type == 0 else 0xFFFF
    g.indices = [y.int(i, 0, top, f"{what} index") for i in y.seq(y.get(node, "indices", what), None, f"{what} indices")]
    return g


def _bounds_node(bounds: list):
    from .yamlish import Scalar, Seq
    return Seq([Seq([Scalar(_ft(x)) for x in b6], flow=True) for b6 in bounds], flow=not bounds,
               comment="min x y z, max x y z" if bounds else None)


def _bounds_from(y: _Y, node, what: str) -> list:
    return [y.fvec(b, 6, what) for b in y.seq(node, None, what)]


def zon_to_yaml(z: Zone, name: str | None = None) -> str:
    from . import yamlish
    from .yamlish import Map, Scalar, Seq

    ddo = z.is_ddo
    head = ["Riftstone zone (.zon, rZone" + (", Dragon's Dogma Online)" if ddo else ")")
            + (f" -- {_one_line(name)}" if name else ""),
            f"'{_one_line(_raw(z.name).decode('latin-1'))}': {len(z.layouts)} layout(s) (a shape plus "
            "nZone::cLayoutElement's fields),",
            f"{len(z.groups)} group(s). Shapes: " + ", ".join(f"{i} {n}" for i, n in enumerate(SHAPE_NAMES)) + ".",
            "contents and mpExtendObj are XFS objects kept as exact bytes (hex). A grid is cGridCollision.",
            "Floats are exact 32-bit values. Rebuilds byte-for-byte when untouched."]
    items = [(Scalar("riftstone"), Scalar(ZON_TAG))]
    if name:
        items.append((Scalar("resource"), Scalar(name, "double")))
    items += [(Scalar("version"), Scalar(_hex(z.version), comment="Dragon's Dogma Online" if ddo else "DDDA")),
              (Scalar("ZoneType"), Scalar(str(z.zone_type), comment={0: "no grids", 1: "a grid per group",
                                                                     2: "one grid for the zone"}[z.zone_type])),
              (Scalar("Name"), _txt_node(z.name)),
              (Scalar("ContentsClass"), Scalar(_hex(z.contents_class), comment=_class_note(z.contents_class))),
              (Scalar("mUnk64"), Scalar(_hex(z.mUnk64))),
              (Scalar("ContentsNum"), Scalar(str(z.contents_num))),
              (Scalar("contents"), _hex_node(z.contents))]
    lays = []
    for i, lay in enumerate(z.layouts):
        f = [(Scalar("shape"), Scalar(str(lay.shape), comment=f"#{i} {SHAPE_NAMES[lay.shape]}"))]
        f += _field_nodes(_LAYOUT_HEAD + shape_fields(lay.shape, ddo) + _LAYOUT_TAIL, lay.fields)
        if lay.extend:
            f.append((Scalar("mpExtendObj"), _hex_node(lay.extend)))
        lays.append(Map(f))
    items.append((Scalar("layouts"), Seq(lays, flow=not lays)))
    groups = []
    for g in z.groups:
        f = [(Scalar("mUnk08"), Scalar(str(g.mUnk08))), (Scalar("mUnk0C"), Scalar(str(g.mUnk0C))),
             (Scalar("GroupLayoutIndex"), Seq([Scalar(str(v)) for v in g.layout_index], flow=True)),
             (Scalar("GroupGlobalLayoutIndex"), Seq([Scalar(str(v)) for v in g.global_layout_index], flow=True))]
        if g.grid is not None:
            f += [(Scalar("grid"), _grid_node(g.grid)), (Scalar("bounds"), _bounds_node(g.bounds))]
        groups.append(Map(f))
    items.append((Scalar("groups"), Seq(groups, flow=not groups)))
    if z.grid is not None:
        items.append((Scalar("grid"), _grid_node(z.grid)))
    if z.layout_bounds is not None:
        items.append((Scalar("layout_bounds"), _bounds_node(z.layout_bounds)))
    if ddo:
        items.append((Scalar("mUnkTable"), Seq([Scalar(str(v)) for v in z.tail], flow=True)))
    items.append((Scalar("LayoutIndexFromUniqueID"), Seq([Scalar(str(v)) for v in z.unique_index], flow=True)))
    return yamlish.emit(Map(items), head)


_ZON_TOP = ("riftstone", "resource", "version", "ZoneType", "Name", "ContentsClass", "mUnk64", "ContentsNum",
            "contents", "layouts", "groups", "grid", "layout_bounds", "LayoutIndexFromUniqueID", "mUnkTable")


def zon_from_yaml(text: str, source: str | None = None) -> Zone:
    from . import yamlish
    from .yamlish import Map, Scalar

    y = _Y(source)
    doc = yamlish.parse(text, source)
    tag = doc.get("riftstone") if isinstance(doc, Map) else None
    if not isinstance(tag, Scalar) or tag.text != ZON_TAG:
        raise ParamError(f"not a Riftstone zone (expected 'riftstone: {ZON_TAG}')", 1, 1, source)
    y.only(doc, _ZON_TOP, "the file")
    version = y.int(y.get(doc, "version", "the file"), *_U32R, "version")
    if version not in (ZON_DDDA, ZON_DDO):
        raise y.err(f"version: zones are 0x{ZON_DDDA:08x} (DDDA) or 0x{ZON_DDO:08x} (Online)", doc.get("version"))
    ddo = version == ZON_DDO
    z = Zone(version, y.int(y.get(doc, "ZoneType", "the file"), 0, 2, "ZoneType"),
             y.text(y.get(doc, "Name", "the file"), "Name", allow_nul=True),     # length-prefixed, not a C string
             y.int(y.get(doc, "ContentsClass", "the file"), *_U32R, "ContentsClass"),
             y.int(y.get(doc, "mUnk64", "the file"), *_U32R, "mUnk64"),
             y.int(y.get(doc, "ContentsNum", "the file"), *_U32R, "ContentsNum"),
             _hex_from(y, y.get(doc, "contents", "the file"), "contents"))
    for i, ln in enumerate(y.seq(y.get(doc, "layouts", "the file"), None, "layouts")):
        w = f"layout {i}"
        shape = y.int(y.get(ln, "shape", w), 0, len(SHAPE_NAMES) - 1, f"{w} shape")
        spec = _LAYOUT_HEAD + shape_fields(shape, ddo) + _LAYOUT_TAIL
        y.only(ln, ["shape", "mpExtendObj"] + [f[1] for f in spec], w)
        lay = Layout(shape, _fields_from(y, ln, spec, w))
        if ln.get("mpExtendObj") is not None:
            lay.extend = _hex_from(y, ln.get("mpExtendObj"), f"{w} mpExtendObj") or None
        z.layouts.append(lay)
    for i, gn in enumerate(y.seq(y.get(doc, "groups", "the file"), None, "groups")):
        w = f"group {i}"
        y.only(gn, ("mUnk08", "mUnk0C", "GroupLayoutIndex", "GroupGlobalLayoutIndex", "grid", "bounds"), w)
        g = ZoneGroup(y.int(y.get(gn, "mUnk08", w), *_U32R, f"{w} mUnk08"),
                      y.int(y.get(gn, "mUnk0C", w), *_I32, f"{w} mUnk0C"),
                      [y.int(v, *_U32R, w) for v in y.seq(y.get(gn, "GroupLayoutIndex", w), None, w)],
                      [y.int(v, *_U32R, w) for v in y.seq(y.get(gn, "GroupGlobalLayoutIndex", w), None, w)])
        if gn.get("grid") is not None:
            g.grid = _grid_from(y, gn.get("grid"), f"{w} grid")
            g.bounds = _bounds_from(y, y.get(gn, "bounds", w), f"{w} bounds")
        elif gn.get("bounds") is not None:
            raise y.err(f"{w}: bounds come with a grid (a box per GroupLayoutIndex entry)", gn.get("bounds"))
        z.groups.append(g)
    if doc.get("grid") is not None:
        z.grid = _grid_from(y, doc.get("grid"), "grid")
    if doc.get("layout_bounds") is not None:
        z.layout_bounds = _bounds_from(y, doc.get("layout_bounds"), "layout_bounds")
    if z.zone_type == 2 and z.layout_bounds is None and not z.layouts:
        # no layouts: "no bounds" and "zero bounds" are the same bytes, and parse reads them as [] (fuzz
        # finding schedule_yaml-invariant-c7e795420c4b: the model must re-read the same)
        z.layout_bounds = []
    if ddo:
        z.tail = [y.int(v, *_U32R, "mUnkTable") for v in y.seq(y.get(doc, "mUnkTable", "the file"), None, "mUnkTable")]
    elif doc.get("mUnkTable") is not None:
        raise y.err("mUnkTable: only Dragon's Dogma Online zones have it", doc.get("mUnkTable"))
    z.unique_index = [y.int(v, *_U32R, "LayoutIndexFromUniqueID")
                      for v in y.seq(y.get(doc, "LayoutIndexFromUniqueID", "the file"), None, "LayoutIndexFromUniqueID")]
    try:
        build_zon(z)
    except FormatError as e:
        raise ParamError(str(e), None, None, source) from None
    return z


# -- dispatch (by magic / tag) ---------------------------------------------------------------------
def parse(data: bytes):
    """A .sdl or .zon, by its magic."""
    magic = bytes(data[:4])
    if magic == SDL_MAGIC:
        return parse_sdl(data)
    if magic == ZON_MAGIC:
        return parse_zon(data)
    raise FormatError("schedule", f"not a scheduler or zone (magic {magic!r})", 0)


def build(m) -> bytes:
    if isinstance(m, Scheduler):
        return build_sdl(m)
    if isinstance(m, Zone):
        return build_zon(m)
    raise FormatError("schedule", f"cannot build a {type(m).__name__}")


def to_yaml(m, name: str | None = None) -> str:
    if isinstance(m, Scheduler):
        return sdl_to_yaml(m, name)
    if isinstance(m, Zone):
        return zon_to_yaml(m, name)
    raise FormatError("schedule", f"cannot write a {type(m).__name__}")


def from_yaml(text: str, source: str | None = None):
    from .params import yaml_tag
    tag = yaml_tag(text)
    if tag == SDL_TAG:
        return sdl_from_yaml(text, source)
    if tag == ZON_TAG:
        return zon_from_yaml(text, source)
    raise ParamError(f"not a Riftstone scheduler or zone (expected 'riftstone: {SDL_TAG}' or '{ZON_TAG}')",
                     1, 1, source)


def yaml_to_bytes(text: str, source: str | None = None) -> bytes:
    return build(from_yaml(text, source))


def info(m) -> str:
    if isinstance(m, Scheduler):
        return sdl_info(m)
    if isinstance(m, Zone):
        return zon_info(m)
    return type(m).__name__

"""Dragon's Dogma Online enemy and stage parameter files, byte-exact, with YAML forms.

Seven resource types of the DDO client (03.04.003) that are not XFS.  Every field's order, width and
member is DDO.exe's own, read from each class's loader (addresses below); names are the developers'
(createProperty in DDO.exe, the Japanese ones kept as they are, like .prp) wherever the class
registers the member, else ``mUnkXXX`` after the member offset the loader stores the value to
(not its place in the file, which differs).  English glosses of the Japanese names are YAML comments.

  ext   class                     loader (DDO.exe)          layout
  cpe   rCharParamEnemy           vf10 0x00A5E6D0           "cpe\\0" u32 0x0B, u8 mFlgEnemyFly, then
                                                            cCharParamEnemy's fields; the flying ones
                                                            (cCharParamEnemyFly) only when the flag is set
  pep   rAIPawnEmParam            vf22 0x00A48920, vf15     u32 0x11, f32 f32 u32, u32 count, records
                                  0x00A48960
  prs   rParentRegionStatusParam  vf15 0x00AEC860           u32 0x41, u32 count, records; each record ends
                                                            with an embedded rRegionBreakInfo (u32 2, u32
                                                            count, 3 x s32 records)
  osp   rOcdStatusParamRes        vf15 0x00AD2700           u32 0x25, u32 count, cOcdStatusParamRes records
  sti   rStageInfo                vf10 0x00B01270           "sti\\0" u32 0x109, strings, resource
                                                            references, stage settings
  sal   rStageAdjoinList          vf10 0x00AF5070           "SAL\\0" u32 4, u16, cAdjoinInfo list (each with
                                                            a cIndex list), cJumpPosition list
  evtr  rEventResTable            vf15 0x00A80630           u32 1, u32 count, (string, u64) records
  ndp   rNamedParam               vf10 0x00AB3D50, vf22     u32 5, u32 count, 54-byte cNamedParam records
                                  0x00AB3DC0, vf15          (u32 mID, u32 mType, u32 mHpRate, 21 x u16 rates;
                                  0x00AB3E20                members +0x04..+0x38 of a 0x3C object)

The magic-less formats (pep prs osp evtr ndp) check a version the class returns from its slot 21
(0x11, 0x41, 0x25, 1, 5); the array formats read a count with 0x00AD1FE0 and one record per
slot-15 call.

ndp, the named enemy parameters (param\\named_param, one file in rom\\game_common): each record
multiplies an enemy's stats in percent (cpEmParamCtrl::vf00: HP x mHpRate x 0.01f at 0x0090404A,
parts x mHpSub, base physical attack x mAttackBasePhys x 0.01f at 0x0090461D ...); the local server
sends only the id (NamedEnemyParamsId).  The reader turns a stored mHpRate of 0 into 1 (0x00AB3FE5).
The client finds a record by id through a table sSetManager builds in the game's setup (0x00BFCEA0,
called from aGame::vf06 at 0x00406529 among other managers' setup calls): (max id + 3) & ~3 slots at
sSetManager+0x434 (count +0x438), each {s16 record
index, 0xFFFF = none; s16 message index of the label "namedparam_<id>" in
ui\\00_message\\named\\named_param.gmd}; the lookup 0x00BFC0B0 returns nothing for an id past the
slots, an index with bit 15 set, or one past the record count.  So a record is found by its id
wherever it stands in the file, a new id needs its own label for its name (else message 0, "----"),
and the table has room for every id only when the largest id is not a multiple of 4 (the loader
writes one slot past the end otherwise).  ddo_solo.py builds on this.  Reader widths: slots +04 u16, +08 u32, +0C u64, +14 s16, +18 s32, +24 f32, +30
vector3 (12 bytes), +38 copies bytes into a member (after a u32 count c, 4c bytes: a list read
into a fixed member array), 0x013BBCE0 a NUL-terminated string (the game keeps cap - 1 bytes;
longer text is cut, so it is refused here), inline bytes (a bool when the loader stores
``byte != 0``: kept as the raw byte so every file rebuilds, shown as a number).  A resource
reference (sti) is a class name and, only when the name is not empty, a path (0x00AF0CE0:
MtDTI::find, then the resource manager).

Conditional part: cpe's cCharParamEnemyFly fields (hover speed, hover altitudes, steps, arrival
tolerance, flight speed and altitude) are read only when mFlgEnemyFly is not 0 (the loader then
builds the larger cCharParamEnemyFly, 0x1A8 bytes, instead of cCharParamEnemy, 0x184).  parse
follows the flag; build and the YAML refuse a flag that disagrees with the presence of those fields.

Counts are bounded by the bytes left and, where the loader copies into a fixed member array, by that
array's size: cpe mUnk12C 11 floats (0x12C..0x157; DDO.exe's one reader of it, 0x0065E1B0, indexes
it 0..10), 男性の場合・女性の場合 2 (0x158, 0x15C), ホバリング高度 4 (0x188..0x197); pep lists 4, 4, 2,
1 (members 08, 18, 28, 38: the next member follows each).  A larger count would overrun the object
in the game, so it is refused.

Proof, client 03.04.003: every distinct file parses, rebuilds byte-for-byte, and its YAML rebuilds
byte-for-byte -- cpe 288/288, pep 273/273, prs 269/269, osp 236/236, sti 479/479, sal 457/457,
evtr 362/362, ndp 1/1 (tests/test_ddo_params.py, the corpus test).  Each schema was also compared with
a trace of its loader in DDO.exe, read for read (type, member, constants, loops, the conditional
block): cpe 95 reads, pep 21, prs 29, osp 11, sti 58, sal 13, evtr 4, ndp 26 -- all equal.  ndp's
2,366 records also equal the local server's named_param.ndp.json field for field
(tools/check_corpus.py --game ddo --only ndp).

From the loaders (not stored in the files): sti's mStageNo (+0x298) is the number after "st" in the
resource's own name (0x00A3FF90 takes the name after the last backslash, then strtol base 10); prs
combines mUnk60..mUnk70 into one 64-bit value at +0x18, (((70 * 10^4 + 6C) * 10^4 + 68) * 10^4 + 64)
* 10^4 + 60.

Measured (not proven by code):
  cpe  63 of 288 files fly (all 333 bytes; the ground ones 293).  Every file has 10 mUnk12C values,
       2 in 男性の場合・女性の場合 and (flying) 4 hover altitudes.  mUnk114 (1.0 in every file) and
       mUnk12C are not properties; the constructor sets both to 1.0.  0x0065E1B0 multiplies a value
       by mUnk12C[v - 1], v (1..11) a byte of the attacking character's cContextInstHm; entries 2,
       3, 5 and 7 are the ones most often above 1.0 (34, 34, 44, 23 files).  That v is the
       vocation (DDO has 11) is a guess: UNKNOWN.
  prs  mUnk04 is the record's position in 783 of 813 records; 194 break entries in all.
  osp  異常名称 counts up from 1 (1, 2, 3 ...) in the most common layout (75 records, 118 files).
  evtr mUnk08 is (type id << 32) | JAMCRC(resource path): 5,213 of 7,474 records name an archive
       entry of that type and path (rAIFSM, rScheduler, rCameraParamList, rModel ...); the other
       2,261 have type 0x31897EE8, a class DDO.exe does not register (UNKNOWN).
  sal  mUnk90 is the stage number of the file's own name in 453 of 457 (st0100_adjoin -> 100; the
       other 4 hold 0); 114,305 of 115,121 mDestinationStageNo name a stage with an .sti.
  sti  every resource reference names one class per slot: mUnk0AC/mUnk44C rScheduler, mUnk248
       rNavigationMesh, mUnk24C rOccluderEx, mUnk250 rStartPos, mUnk3C0 rLocationData, the rest rZone.
  Every bool byte in every file is 0 or 1.
UNKNOWN: the meaning of every mUnk field; pep's lists hold what look like bit masks (u32, in hex).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from functools import lru_cache

from .errors import FormatError, ParamError


# -- schema ------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Field:
    kind: str                   # k const, s scalar, str string, ref resource, b block, a array, l list,
    #                             z interleaved arrays, if conditional block
    name: str
    t: str | None = None        # scalar type: u8 bool u16 s16 u32 x32 s32 u64 f32 v3
    off: object = None          # the member the loader stores to (int offset, or "setter 0x...")
    gloss: str | None = None    # English, for the YAML comment
    n: int | None = None        # a / z: fixed count
    cap: int | None = None      # str: buffer bytes; l: most items the member holds (None: any)
    item: Field | None = None   # a / l: one element
    fields: tuple = ()          # b / z / if
    flag: str | None = None     # if: the earlier field whose value (non-zero) switches the block on
    value: int | None = None    # k: the only value DDO.exe accepts


def K(name, t, value):
    return Field("k", name, t, value=value)


def S(name, t, off=None, gloss=None):
    return Field("s", name, t, off, gloss)


def STR(name, cap, off=None, gloss=None):
    return Field("str", name, None, off, gloss, cap=cap)


def REF(name, off=None, gloss=None):
    return Field("ref", name, None, off, gloss)


def B(name, fields, off=None, gloss=None):
    return Field("b", name, None, off, gloss, fields=tuple(fields))


def A(name, item, n, off=None, gloss=None):
    return Field("a", name, None, off, gloss, n=n, item=item)


def L(name, item, off=None, gloss=None, cap=None):
    return Field("l", name, None, off, gloss, cap=cap, item=item)


def Z(fields, n):
    """Arrays the loader reads interleaved (a[0] b[0] a[1] b[1] ...): each field becomes a list."""
    return Field("z", "+".join(f.name for f in fields), n=n, fields=tuple(fields))


def IF(name, flag, fields, gloss=None):
    return Field("if", name, None, None, gloss, fields=tuple(fields), flag=flag)


_ST = {"u8": struct.Struct("<B"), "bool": struct.Struct("<B"), "u16": struct.Struct("<H"),
       "s16": struct.Struct("<h"), "u32": struct.Struct("<I"), "x32": struct.Struct("<I"),
       "s32": struct.Struct("<i"), "u64": struct.Struct("<Q"), "f32": struct.Struct("<I")}
_RANGE = {"u8": (0, 0xFF), "bool": (0, 0xFF), "u16": (0, 0xFFFF), "s16": (-0x8000, 0x7FFF),
          "u32": (0, 0xFFFFFFFF), "x32": (0, 0xFFFFFFFF), "s32": (-0x80000000, 0x7FFFFFFF),
          "u64": (0, 0xFFFFFFFFFFFFFFFF), "f32": (0, 0xFFFFFFFF)}
REF_CAP = 0x40      # the loader's buffer for a reference's class name and path

F32 = "f32"
_CPE_FLY = [
    S("ホバリング速度", F32, 0x184, "hover speed"),
    L("ホバリング高度", S("", F32), 0x188, "hover altitudes (a count, then up to 4)", cap=4),
    S("ホバリング高度段階数", "s32", 0x198, "number of hover altitude steps"),
    S("ホバリング到達判定幅", "s32", 0x1A4, "hover arrival tolerance"),
    S("飛行速度", F32, 0x19C, "flight speed"),
    S("飛行高度", F32, 0x1A0, "flight altitude"),
]
_CPE = [
    K("magic", "u32", 0x00657063), K("version", "u32", 0x0B),
    S("mFlgEnemyFly", "bool", "rCharParamEnemy+0x68", "flying enemy: 1 with a cCharParamEnemyFly block, 0 without"),
    S("基礎物理攻撃力", F32, 0x08, "base physical attack"),
    S("基礎魔法攻撃力", F32, 0x0C, "base magick attack"),
    S("装備物理攻撃力", F32, 0x10, "equipment physical attack"),
    S("装備魔法攻撃力", F32, 0x14, "equipment magick attack"),
    S("基礎物理防御力", F32, 0x18, "base physical defense"),
    S("基礎魔法防御力", F32, 0x1C, "base magick defense"),
    S("装備物理防御力", F32, 0x20, "equipment physical defense"),
    S("装備魔法防御力", F32, 0x24, "equipment magick defense"),
    S("筋力", F32, 0x28, "strength"),
    S("重量", F32, 0x2C, "weight"),
    S("ガード基礎攻撃力", F32, 0x30, "guard base attack"),
    S("ガード基礎防御力", F32, 0x34, "guard base defense"),
    S("ガード装備防御力", F32, 0x38, "guard equipment defense"),
    S("武器の種類", "u32", 0x3C, "weapon type"),
    S("体のサイズ", "u32", 0x40, "body size"),
    S("プッシュグループ", "u32", 0x44, "push group"),
    S("SCR当たり属性", "u32", 0x48, "SCR (stage) hit attribute"),
    S("SCR当たりサイズ", "u32", 0x4C, "SCR (stage) hit size"),
    S("怒り時の揺さぶり値回復倍率", F32, 0x50, "shake-value recovery multiplier when angry"),
    A("mJumpAttackSpeed", B("cJumpAttackSpeed", [
        S("有効フラグ", "bool", 0x04, "enabled"),
        S("前方速度", F32, 0x08, "forward speed"),
        S("Ｙ初速", F32, 0x0C, "initial Y speed"),
        S("重力", F32, 0x10, "gravity"),
    ]), 4, 0x54, "jump attack speed (4 x cJumpAttackSpeed, 0x14 bytes each)"),
    S("ダメージを受ける高さ", F32, 0xA4, "height at which damage is taken"),
    S("ブレンド数", "u32", 0xA8, "blend count"),
    S("履歴ブレンド使用", "bool", 0xAC, "use history blending"),
    S("履歴ブレンド数", "u32", 0xB0, "history blend count"),
    S("リターンテリトリー継続タイム", F32, 0xB4, "return-to-territory duration"),
    S("押さえ付け効き易さ", "u8", 0xB8, "how easily it is pinned down"),
    A("mGuardCounter", B("cGuardCounter", [
        S("ガード回数(以下)", "u8", 0x04, "guard count (at most)"),
        S("反撃確率(％以下で反撃)", "u8", 0x05, "counterattack chance (counters at or below this %)"),
    ]), 10, 0xBC, "guard counter (10 x cGuardCounter, 8 bytes each)"),
    S("ガードリアクションチェック", "u32", 0x10C, "guard reaction check"),
    S("モデルスケール値", F32, 0x110, "model scale"),
    S("mUnk114", F32, 0x114, "not a property; the constructor sets 1.0"),
    S("揺さぶられアクションを取る", "bool", 0x118, "takes the being-shaken action"),
    S("敵思考テーブルのスケール値", F32, 0x11C, "scale of the enemy thinking table"),
    S("明るいときのリンクする範囲", F32, 0x120, "link range when bright"),
    S("暗い時のリンクする範囲を設定", "bool", 0x124, "set a link range for the dark"),
    S("暗い時のリンクする範囲", F32, 0x128, "link range in the dark"),
    L("mUnk12C", S("", F32), 0x12C, "not a property; a count, then up to 11 multipliers (1.0 each by default)",
      cap=11),
    L("男性の場合・女性の場合", S("", F32), 0x158, "for a male, for a female (a count, then up to 2)", cap=2),
    S("チャンスダウン時の演出をしない", "bool", 0x160, "no presentation on a chance down"),
    S("揺さぶりゲージにダウン値使用", "bool", 0x161, "use the down value for the shake gauge"),
    S("優勢曲をコールしない", "bool", 0x162, "do not call the winning music"),
    S("エンチャント使う？", "bool", 0x163, "uses enchantment?"),
    S("エンチャントタイプ", "u32", 0x164, "enchantment type"),
    S("ダメージの特殊設定タイプ", "u32", 0x168, "special damage setting type"),
    S("ダメージボーナスフラグ", "u32", 0x16C, "damage bonus flags"),
    S("のけぞり時に疲れ値が減る割合", F32, 0x170, "rate the fatigue value drops while staggered"),
    S("シーケンス有効時のゆさぶり補正倍率", F32, 0x174, "shake correction multiplier while a sequence is active"),
    IF("cCharParamEnemyFly", "mFlgEnemyFly", _CPE_FLY, "the flying fields: present exactly when mFlgEnemyFly is not 0"),
    S("GUIの表示領域倍率", F32, 0x178, "GUI display-area multiplier"),
    S("汎用ダメージボーナスタイプ", "u32", 0x17C, "general damage bonus type"),
    S("部位破壊のゆさぶり蓄積値割合", F32, 0x180, "shake accumulation rate on a part break"),
]

_PEP = [
    K("version", "u32", 0x11),
    S("mUnk70", F32, 0x70), S("mUnk74", F32, 0x74), S("mUnk78", "u32", 0x78),
    L("mpArray", B("record", [
        S("mUnk04", "u32", 0x04),
        L("mUnk08", S("", "x32"), 0x08, "a count, then up to 4", cap=4),
        L("mUnk18", S("", "x32"), 0x18, "a count, then up to 4", cap=4),
        L("mUnk28", S("", "x32"), 0x28, "a count, then up to 2", cap=2),
        S("mUnk34", "s32", 0x34), S("mUnk30", "s32", 0x30),
        L("mUnk38", S("", "x32"), 0x38, "a count, then up to 1", cap=1),
        S("mUnk3C", "bool", 0x3C), S("mUnk3D", "bool", 0x3D),
        S("mUnk40", "s32", 0x40), S("mUnk44", "s32", 0x44), S("mUnk48", "s32", 0x48),
    ]), "records 0x50"),
]

_PRS = [
    K("version", "u32", 0x41),
    L("mpArray", B("cParentRegionStatusParam", [
        S("mUnk04", "u32", 0x04), S("mUnk08", "u32", 0x08), S("mUnk0C", "bool", 0x0C), S("mUnk10", "u32", 0x10),
        S("mUnk60", "u32", 0x60), S("mUnk64", "u32", 0x64), S("mUnk68", "u32", 0x68), S("mUnk6C", "u32", 0x6C),
        S("mUnk70", "u32", 0x70),
        *[S(f"mUnk{off:02X}", F32, off) for off in range(0x20, 0x54, 4)],
        S("mUnk54", "bool", 0x54), S("mUnk58", "u32", 0x58),
        B("mRegionBreakInfo", [
            K("version", "u32", 2),
            L("mpArray", B("cRegionBreakInfo", [S("mUnk08", "s32", 0x08), S("mUnk04", "s32", 0x04),
                                                S("mUnk0C", "s32", 0x0C)]), "records 0x10"),
        ], 0x5C, "the record's rRegionBreakInfo (version 2, its own count and records)"),
    ]), "records 0x78"),
]

_OSP = [
    K("version", "u32", 0x25),
    L("mpArray", B("cOcdStatusParamRes", [
        S("異常名称", "u32", 0x04, "ailment name (id)"),
        S("異常有無", "bool", 0x08, "ailment on/off"),
        S("耐性値", F32, "setter 0x008B0820", "resistance value"),
        S("時間経過で治る", "bool", 0x10, "cured over time"),
        S("有効時間", F32, "setter 0x008B0700", "active time"),
        S("回復待機時間", F32, 0x18, "recovery wait time"),
        S("回復量", F32, "setter 0x008B0790", "recovery amount"),
        S("汎用パラメータ0", F32, "setter 0x008B08B0", "general parameter 0"),
        S("汎用パラメータ1", F32, "setter 0x008B0940", "general parameter 1"),
    ]), "records 0x3C"),
]

_STI = [
    K("magic", "u32", 0x00697473), K("version", "u32", 0x109),
    STR("mMdlSdlPath", 0x40, 0x6C),
    REF("mUnk0AC", 0xAC, "a resource reference [class, path] or []; rScheduler where set (measured)"),
    Z([STR("mScrSbcPathArr", 0x40, 0xC8), STR("mEffSbcPathArr", 0x40, 0x188)], 3),
    REF("mUnk248", 0x248, "rNavigationMesh where set (measured)"),
    REF("mUnk24C", 0x24C, "rOccluderEx where set (measured)"),
    REF("mUnk250", 0x250, "rStartPos where set (measured)"),
    STR("mCmrPrmLstEvtPath", 0x40, 0x258),
    S("mPos", "v3", 0x2A0), S("mAng", F32, 0x2B0), S("mSceLoadFlag", "u32", 0x2C0),
    S("mFlag", "u32", 0x2B4, "also registered as SystemFlag"),
    STR("mWeatherStageInfoPath", 0x40, 0x3CC), STR("mWeatherParamInfoTblPath", 0x40, 0x40C),
    STR("mSkyWepPath", 0x40, 0x470), STR("mRoomWepPath", 0x40, 0x4B0),
    REF("mUnk44C", 0x44C, "rScheduler where set (measured)"),
    STR("mEpvPath", 0x40, 0x4F4),
    S("mEpvIndexAlways", "s32", 0x534), S("mEpvIndexDay", "s32", 0x538), S("mEpvIndexNight", "s32", 0x53C),
    A("mUnk450", REF(""), 4, 0x450, "rZone where set (measured)"),
    REF("mUnk460", 0x460, "rZone where set (measured)"), REF("mUnk464", 0x464, "rZone where set (measured)"),
    S("mDayNightLightChgFrame", F32, 0x2B8), S("mDayNightFogChgFrame", F32, 0x2BC),
    S("mSkyInfiniteLightGroupType", "s32", 0x540),
    A("mUnk544", REF(""), 3, 0x544, "rZone where set (measured)"),
    A("mUnk550", REF(""), 3, 0x550, "rZone where set (measured)"),
    REF("mUnk55C", 0x55C, "rZone where set (measured)"),
    A("EQLength", S("", F32), 4, 0x2D0),
    STR("mSoundAreaInfoPath", 0x40, 0x2E4), STR("mEffectSdlPath", 0x40, 0x340), STR("mLanternSdlPath", 0x40, 0x380),
    S("mIsCraftStage", "bool", 0x324),
    REF("mUnk3C0", 0x3C0, "rLocationData where set (measured)"),
    S("mGrassVisiblePercentMulValue", F32, 0x2C4), S("mGrassFadeBeginDistance", F32, 0x2C8),
    S("mGrassFadeEndDistance", F32, 0x2CC),
    S("mPerformanceFlag", "u16", 0x336),
    STR("mAnotherMapName", 0x10, 0x325),
    S("mShowOnlyCurrentFloorMinimap", "bool", 0x335),
]

_SAL = [
    K("magic", "u32", 0x004C4153), K("version", "u32", 4),
    S("mUnk90", "u16", 0x90, "the stage number of the file's name in 453 of 457 files (measured)"),
    L("mAdjoinInfo", B("cAdjoinInfo", [
        L("mIndex", S("", "u16", 0x04), 0x04, "cIndex list: one u16 mIndex each"),
        S("mDestinationStageNo", "u16", 0x18),
        S("mNextStageNo", "u16", 0x1A),
        S("mPriority", "u8", 0x1C),
    ]), 0x68, "rStageAdjoinList::cAdjoinInfo list"),
    L("mJumpPosition", B("cJumpPosition", [
        S("mPos", "v3", 0x10), S("mQuestId", "u32", 0x20), S("mFlagId", "u32", 0x24),
    ]), 0x7C, "rStageAdjoinList::cJumpPosition list"),
]

_EVTR = [
    K("version", "u32", 1),
    L("mpArray", B("cEventResTable", [
        STR("mUnk04", 0x100, 0x04, "resource label"),
        S("mUnk08", "u64", 0x08, "type id << 32 | JAMCRC of the resource path (measured)"),
    ]), "records 0x10"),
]

# The rates in percent (100 = unchanged), in the order the loader reads them (the member order).
NDP_RATES = ("mExperience", "mAttackBasePhys", "mAttackWepPhys", "mDefenceBasePhys", "mDefenceWepPhys",
             "mAttackBaseMagic", "mAttackWepMagic", "mDefenceBaseMagic", "mDefenceWepMagic", "mPower",
             "mGuardDefenceBase", "mGuardDefenceWep", "mShrinkEnduranceMain", "mBlowEnduranceMain",
             "mDownEnduranceMain", "mShakeEnduranceMain", "mHpSub", "mShrinkEnduranceSub", "mBlowEnduranceSub",
             "mOcdEndurance", "mAilmentDamage")
_NDP_GLOSS = {
    "mExperience": "EXP, percent (the server: exp * (this / 100), whole-number division)",
    "mAttackBasePhys": "base physical attack, percent",
    "mAttackWepPhys": "weapon physical attack, percent",
    "mAttackBaseMagic": "base magick attack, percent",
    "mAttackWepMagic": "weapon magick attack, percent",
    "mHpSub": "HP of the body parts, percent",
}
_NDP = [
    K("version", "u32", 5),
    L("mpArray", B("cNamedParam", [
        S("mID", "u32", 0x04, "the id the server sends as NamedEnemyParamsId"),
        S("mType", "u32", 0x08, "the name: 1 none, 2 prefix, 3 suffix, 4 replaces the enemy's name"),
        S("mHpRate", "u32", 0x0C, "HP, percent (0 loads as 1)"),
        *[S(n, "u16", 0x10 + 2 * i, _NDP_GLOSS.get(n, "percent")) for i, n in enumerate(NDP_RATES)],
    ]), "records 0x3C (a vtable, then these members)"),
]


@dataclass(frozen=True)
class Kind:
    ext: str
    cls: str
    fields: tuple
    title: str
    notes: tuple = ()

    @property
    def tag(self) -> str:
        return f"{self.ext}-ddo/1"

    @property
    def type_id(self) -> int:
        from .typemap import jamcrc
        return jamcrc(self.cls)


KINDS: dict[str, Kind] = {k.ext: k for k in (
    Kind("cpe", "rCharParamEnemy", tuple(_CPE), "enemy parameters", (
        "Keys are the developers' names (DDO.exe cCharParamEnemy); comments translate them.",
        "mFlgEnemyFly 1 needs the cCharParamEnemyFly block (hover and flight), 0 must not have it.")),
    Kind("pep", "rAIPawnEmParam", tuple(_PEP), "pawn AI enemy parameters", (
        "DDO.exe names none of these fields: mUnkXX is the member offset the loader stores to.",)),
    Kind("prs", "rParentRegionStatusParam", tuple(_PRS), "parent region status", (
        "DDO.exe names none of these fields: mUnkXX is the member offset the loader stores to.",
        "Each record carries its own rRegionBreakInfo list (records of three s32).")),
    Kind("osp", "rOcdStatusParamRes", tuple(_OSP), "status ailment parameters", (
        "Keys are the developers' names (DDO.exe cOcdStatusParamRes); comments translate them.",)),
    Kind("sti", "rStageInfo", tuple(_STI), "stage info", (
        "Names are DDO.exe's (rStageInfo); mUnkXXX are unnamed members. A resource reference is",
        "[class, path], or [] for none. Text longer than the game's buffer (63 bytes) is refused.")),
    Kind("sal", "rStageAdjoinList", tuple(_SAL), "stage adjoin list", (
        "Names are DDO.exe's (cAdjoinInfo, cIndex, cJumpPosition). Positions are x, y, z.",)),
    Kind("evtr", "rEventResTable", tuple(_EVTR), "event resource table", (
        "mUnk08 is written in hex: the type id, then the JAMCRC of the resource's path.",)),
    Kind("ndp", "rNamedParam", tuple(_NDP), "named enemy parameters", (
        "Names are DDO.exe's (cNamedParam). Rates are percent: 100 leaves a stat as it is.",
        "The client finds a record by mID; its name is the label namedparam_<mID> in",
        "ui/00_message/named/named_param.gmd. Keep the largest mID off a multiple of 4.")),
)}
_BY_MAGIC = {b"cpe\0": "cpe", b"sti\0": "sti", b"SAL\0": "sal"}


@lru_cache(maxsize=1)
def _by_type() -> dict[int, str]:
    return {k.type_id: k.ext for k in KINDS.values()}


def kind_for_type(type_id: int | None) -> str | None:
    """The kind (extension) for a resource type id, or None: the way to dispatch these (four have no magic)."""
    return _by_type().get(type_id)


def kind_of(data: bytes) -> str | None:
    """The kind a resource's magic names (cpe, sti, sal); the other four have no magic."""
    return _BY_MAGIC.get(bytes(data[:4]))


@dataclass
class DdoParams:
    kind: str                           # the extension: cpe pep prs osp sti sal evtr ndp
    data: dict = field(default_factory=dict)


# -- binary ------------------------------------------------------------------------------------------
def _text(b: bytes):
    """Text when Shift-JIS decodes and re-encodes it exactly, else the raw bytes."""
    try:
        t = b.decode("cp932")
        if t.encode("cp932") == b:
            return t
    except UnicodeError:
        pass
    return bytes(b)


@lru_cache(maxsize=None)
def _min_size(f: Field) -> int:
    """The fewest bytes one value of f takes in a file (bounds a count by the bytes left)."""
    if f.kind in ("s", "k"):
        return 12 if f.t == "v3" else _ST[f.t].size
    if f.kind in ("str", "ref"):
        return 1
    if f.kind == "l":
        return 4
    if f.kind == "a":
        return f.n * _min_size(f.item)
    if f.kind in ("b", "z"):
        return sum(_min_size(g) for g in f.fields) * (f.n if f.kind == "z" else 1)
    return 0                                                     # if: may be absent


class _Reader:
    __slots__ = ("kind", "d", "p")

    def __init__(self, kind: str, data: bytes):
        self.kind = kind
        self.d = data
        self.p = 0

    def fail(self, msg: str, at: int | None = None):
        raise FormatError(self.kind, msg, self.p if at is None else at)

    def scalar(self, t: str, what: str):
        if t == "v3":
            return [self.scalar("f32", what) for _ in range(3)]
        st = _ST[t]
        if self.p + st.size > len(self.d):
            self.fail(f"{what}: the file ends inside it")
        v = st.unpack_from(self.d, self.p)[0]
        self.p += st.size
        return v

    def string(self, cap: int, what: str):
        end = self.d.find(b"\0", self.p)
        if end < 0:
            self.fail(f"{what}: the text has no terminating NUL")
        if end - self.p > cap - 1:
            self.fail(f"{what}: {end - self.p} bytes of text; the game keeps at most {cap - 1}")
        v = _text(bytes(self.d[self.p:end]))
        self.p = end + 1
        return v

    def count(self, item: Field, cap: int | None, what: str) -> int:
        at = self.p
        n = self.scalar("u32", what + " count")
        if cap is not None and n > cap:
            self.fail(f"{what}: {n} items; the game's member holds at most {cap}", at)
        least = max(1, _min_size(item))
        if n * least > len(self.d) - self.p:
            self.fail(f"{what}: {n} items do not fit in the {len(self.d) - self.p} bytes left", at)
        return n


def _read_value(r: _Reader, f: Field, what: str):
    k = f.kind
    if k == "s":
        return r.scalar(f.t, what)
    if k == "str":
        return r.string(f.cap, what)
    if k == "ref":
        cls = r.string(REF_CAP, what + " class")
        if cls == "" or cls == b"":
            return None
        return [cls, r.string(REF_CAP, what + " path")]
    if k == "b":
        return _read_block(r, f.fields, what + ".")
    if k == "a":
        return [_read_value(r, f.item, f"{what}[{i}]") for i in range(f.n)]
    if k == "l":
        n = r.count(f.item, f.cap, what)
        return [_read_value(r, f.item, f"{what}[{i}]") for i in range(n)]
    raise AssertionError(k)


def _read_block(r: _Reader, fields, where: str = "") -> dict:
    out: dict = {}
    for f in fields:
        what = where + f.name
        if f.kind == "k":
            at = r.p
            v = r.scalar(f.t, what)
            if v != f.value:
                r.fail(f"{what} is {v:#x}; DDO.exe loads only {f.value:#x}", at)
        elif f.kind == "z":
            cols: dict = {g.name: [] for g in f.fields}
            for i in range(f.n):
                for g in f.fields:
                    cols[g.name].append(_read_value(r, g, f"{where}{g.name}[{i}]"))
            out.update(cols)
        elif f.kind == "if":
            out[f.name] = _read_block(r, f.fields, what + ".") if out[f.flag] else None
        else:
            out[f.name] = _read_value(r, f, what)
    return out


def parse(data: bytes, kind: str | None = None) -> DdoParams:
    """Parse a resource of one of the KINDS; `kind` (the extension) is required for pep prs osp evtr."""
    ext = kind or kind_of(data)
    if ext not in KINDS:
        raise FormatError("ddo-params", f"unknown kind {kind!r} (one of {', '.join(KINDS)})" if kind else
                          "no cpe/sti/SAL magic; the magic-less kinds (pep prs osp evtr) need their kind")
    spec = KINDS[ext]
    data = bytes(data)
    r = _Reader(ext, data)
    # a cpe whose flag disagrees with its data fails as too short or too long: say which way the flag went
    fly = "" if ext != "cpe" or data[:8] != b"cpe\0\x0b\0\0\0" or len(data) < 9 else \
        f" (mFlgEnemyFly is {data[8]}: the flying fields are {'read' if data[8] else 'not read'})"
    try:
        d = _read_block(r, spec.fields)
    except FormatError as e:
        if not fly:
            raise
        msg = str(e)[len(ext) + 2:]
        if e.offset is not None:
            msg = msg.removesuffix(f" at byte 0x{e.offset:x}")
        raise FormatError(ext, msg + fly, e.offset) from None
    if r.p != len(data):
        raise FormatError(ext, f"{len(data) - r.p} bytes after the last field{fly}", r.p)
    return DdoParams(ext, d)


def _pack(out: bytearray, t: str, v, what: str, kind: str) -> None:
    if t == "v3":
        if not isinstance(v, (list, tuple)) or len(v) != 3:
            raise FormatError(kind, f"{what}: a vector needs 3 numbers")
        for x in v:
            _pack(out, "f32", x, what, kind)
        return
    if t == "f32" and isinstance(v, float):
        try:
            v = struct.unpack("<I", struct.pack("<f", v))[0]
        except (OverflowError, struct.error):
            raise FormatError(kind, f"{what}: {v!r} does not fit a 32-bit float") from None
    if isinstance(v, bool) or not isinstance(v, int):
        raise FormatError(kind, f"{what}: {v!r} is not a whole number")
    lo, hi = _RANGE[t]
    if not lo <= v <= hi:
        raise FormatError(kind, f"{what}: {v} is out of range for {t} ({lo}..{hi})")
    out += _ST[t].pack(v)


def _pack_text(out: bytearray, v, cap: int, what: str, kind: str) -> None:
    if isinstance(v, (bytes, bytearray)):
        b = bytes(v)
    elif isinstance(v, str):
        try:
            b = v.encode("cp932")
        except UnicodeError:
            raise FormatError(kind, f"{what}: the text has characters the game's encoding (Shift-JIS) lacks") from None
    else:
        raise FormatError(kind, f"{what}: {v!r} is not text")
    if b"\0" in b:
        raise FormatError(kind, f"{what}: a NUL inside the text")
    if len(b) > cap - 1:
        raise FormatError(kind, f"{what}: {len(b)} bytes of text; the game keeps at most {cap - 1}")
    out += b + b"\0"


def _write_value(out: bytearray, f: Field, v, what: str, kind: str) -> None:
    k = f.kind
    if k == "s":
        _pack(out, f.t, v, what, kind)
    elif k == "str":
        _pack_text(out, v, f.cap, what, kind)
    elif k == "ref":
        if v is None:
            out += b"\0"
            return
        if not isinstance(v, (list, tuple)) or len(v) != 2:
            raise FormatError(kind, f"{what}: a resource reference is [class, path] or None")
        if v[0] in ("", b""):
            raise FormatError(kind, f"{what}: a reference without a class name is None (the game reads no path)")
        _pack_text(out, v[0], REF_CAP, what + " class", kind)
        _pack_text(out, v[1], REF_CAP, what + " path", kind)
    elif k == "b":
        if not isinstance(v, dict):
            raise FormatError(kind, f"{what}: expected a block of fields")
        _write_block(out, f.fields, v, what + ".", kind)
    elif k in ("a", "l"):
        if not isinstance(v, (list, tuple)):
            raise FormatError(kind, f"{what}: expected a list")
        if k == "a" and len(v) != f.n:
            raise FormatError(kind, f"{what}: holds {f.n} values, not {len(v)}")
        if k == "l":
            if f.cap is not None and len(v) > f.cap:
                raise FormatError(kind, f"{what}: {len(v)} items; the game's member holds at most {f.cap}")
            out += struct.pack("<I", len(v))
        for i, x in enumerate(v):
            _write_value(out, f.item, x, f"{what}[{i}]", kind)
    else:
        raise AssertionError(k)


@lru_cache(maxsize=None)
def _names(fields: tuple) -> tuple[str, ...]:
    """The keys a block of these fields has (constants have none; interleaved arrays one each)."""
    out: list[str] = []
    for f in fields:
        if f.kind == "z":
            out += [g.name for g in f.fields]
        elif f.kind != "k":
            out.append(f.name)
    return tuple(out)


def _write_block(out: bytearray, fields, data: dict, where: str, kind: str) -> None:
    names = _names(fields)
    missing = [n for n in names if n not in data]
    if missing:
        raise FormatError(kind, f"{where}{missing[0]} is missing")
    if len(data) != len(names):
        extra = [n for n in data if n not in names]
        raise FormatError(kind, f"{where}{extra[0]!s} is not a field of this format")
    for f in fields:
        what = where + f.name
        if f.kind == "k":
            out += _ST[f.t].pack(f.value)
        elif f.kind == "z":
            for g in f.fields:
                col = data[g.name]
                if not isinstance(col, (list, tuple)) or len(col) != f.n:
                    raise FormatError(kind, f"{where}{g.name}: holds {f.n} values")
            for i in range(f.n):
                for g in f.fields:
                    _write_value(out, g, data[g.name][i], f"{where}{g.name}[{i}]", kind)
        elif f.kind == "if":
            on, block = data[f.flag], data[f.name]
            if on and block is None:
                raise FormatError(kind, f"{where}{f.flag} is {on} but the {f.name} fields are missing")
            if not on and block is not None:
                raise FormatError(kind, f"{where}the {f.name} fields are present but {f.flag} is 0")
            if block is not None:
                if not isinstance(block, dict):
                    raise FormatError(kind, f"{what}: expected a block of fields")
                _write_block(out, f.fields, block, what + ".", kind)
        else:
            _write_value(out, f, data[f.name], what, kind)


def build(m: DdoParams) -> bytes:
    if m.kind not in KINDS:
        raise FormatError("ddo-params", f"unknown kind {m.kind!r}")
    if not isinstance(m.data, dict):
        raise FormatError(m.kind, "the data must be a mapping of fields")
    out = bytearray()
    _write_block(out, KINDS[m.kind].fields, m.data, "", m.kind)
    return bytes(out)


# -- YAML --------------------------------------------------------------------------------------------
def _key(name: str):
    from . import yamlish
    from .yamlish import Scalar

    return Scalar(name, "plain" if yamlish.quote(name) == name else "double")


def _type_note(v: int) -> str | None:
    from .typemap import BY_ID
    known = BY_ID.get(v >> 32)
    return known[0] if known else None


def _scalar_node(t: str, v, gloss=None, note=None):
    from .params import f32_bits_text
    from .yamlish import Scalar, Seq

    if t == "v3":
        return Seq([Scalar(f32_bits_text(x)) for x in v], flow=True, comment=gloss)
    if t == "f32":
        return Scalar(f32_bits_text(v), comment=gloss)
    if t == "x32":
        return Scalar(f"0x{v:x}", comment=gloss)
    if t == "u64":
        return Scalar(f"0x{v:016x}", comment=note or gloss)
    return Scalar(str(v), comment=gloss)


def _text_node(v, gloss=None, flow=False):
    from .yamlish import Map, Scalar

    if isinstance(v, (bytes, bytearray)):
        return Map([(Scalar("hex"), Scalar(bytes(v).hex(), "double"))], flow=True)
    return Scalar(v, "double", comment=None if flow else gloss)


def _node(f: Field, v, gloss=True):
    from .yamlish import Map, Seq

    g = f.gloss if gloss else None
    k = f.kind
    if k == "s":
        return _scalar_node(f.t, v, g, _type_note(v) if f.t == "u64" else None)
    if k == "str":
        return _text_node(v, g)
    if k == "ref":
        return Seq([] if v is None else [_text_node(v[0], flow=True), _text_node(v[1], flow=True)], flow=True,
                   comment=g)
    if k == "b":
        return Map(_block_items(f.fields, v, gloss))
    if k in ("a", "l"):
        it = f.item
        if it.kind == "s" and it.t != "v3" or it.kind == "str":
            return Seq([_node(it, x, False) for x in v], flow=True, comment=g)
        nodes = [_node(it, x, gloss and i == 0) for i, x in enumerate(v)]    # glosses on the first item only
        if g and nodes and hasattr(nodes[0], "comment") and not nodes[0].comment:
            nodes[0].comment = g
        return Seq(nodes)
    raise AssertionError(k)


def _block_items(fields, data: dict, gloss: bool = True) -> list:
    items = []
    for f in fields:
        if f.kind == "k":
            continue
        if f.kind == "z":
            for g in f.fields:
                items.append((_key(g.name), _node(L(g.name, g), data[g.name], gloss)))
        elif f.kind == "if":
            if data[f.name] is not None:
                from .yamlish import Map
                items.append((_key(f.name), Map(_block_items(f.fields, data[f.name], gloss))))
        else:
            items.append((_key(f.name), _node(f, data[f.name], gloss)))
    return items


def to_yaml(m: DdoParams, name: str | None = None) -> str:
    from . import yamlish
    from .yamlish import Map, Scalar

    spec = KINDS[m.kind]
    shown = " -- " + "".join(c if c.isprintable() else " " for c in name) if name else ""   # stays in its comment
    head = [f"Riftstone {spec.title} ({spec.cls} .{spec.ext}, Dragon's Dogma Online){shown}",
            *spec.notes,
            "Numbers are the stored values (floats exactly, bools as 0/1). Rebuilds byte-for-byte when untouched."]
    items = [(Scalar("riftstone"), Scalar(spec.tag))]
    if name:
        items.append((Scalar("resource"), Scalar(name, "double")))
    items += _block_items(spec.fields, m.data)
    return yamlish.emit(Map(items), head)


def _where(node, source):
    return (getattr(node, "line", None), getattr(node, "col", None), source)


def _one(node, what: str, source):
    from .yamlish import Scalar

    if not isinstance(node, Scalar):
        raise ParamError(f"'{what}' holds a list or mapping where a single value belongs", *_where(node, source))
    return node.text.strip()


def _int_text(t: str) -> int:
    """A whole number: decimal or 0x hex, one optional sign (ValueError otherwise)."""
    t = t.replace("_", "")
    neg = t.startswith("-")
    body = t[1:] if t[:1] in "+-" else t
    if body[:1] in ("+", "-") or body.lower().startswith("0x") and body[2:3] in ("+", "-"):
        raise ValueError(t)
    v = int(body, 16) if body.lower().startswith("0x") else int(body, 10)
    return -v if neg else v


def _from_scalar(t: str, node, what: str, source):
    from .params import f32_bits
    from .yamlish import Seq

    if t == "v3":
        if not isinstance(node, Seq) or len(node.items) != 3:
            raise ParamError(f"'{what}' needs 3 numbers [x, y, z]", *_where(node, source))
        return [_from_scalar("f32", x, what, source) for x in node.items]
    text = _one(node, what, source)
    if t == "f32":
        try:
            return f32_bits(text)
        except ValueError:
            raise ParamError(f"'{what}': {text!r} is not a number that fits a 32-bit float",
                             *_where(node, source)) from None
    if t == "bool" and text.lower() in ("true", "false"):
        return int(text.lower() == "true")
    try:
        v = _int_text(text)
    except ValueError:
        raise ParamError(f"'{what}' must be a whole number, not {text!r}", *_where(node, source)) from None
    lo, hi = _RANGE[t]
    if not lo <= v <= hi:
        raise ParamError(f"'{what}': {v} is out of range ({lo}..{hi})", *_where(node, source))
    return v


def _from_text(node, cap: int, what: str, source):
    from .yamlish import Map, Scalar

    if isinstance(node, Map):
        h = node.get("hex")
        try:
            if len(node.items) != 1:
                raise ValueError
            b = bytes.fromhex(h.text)
        except (AttributeError, ValueError):
            raise ParamError(f"'{what}': raw text is {{hex: \"...\"}}, pairs of hex digits", *_where(node, source)) \
                from None
    elif isinstance(node, Scalar):
        b = node.text
    else:
        raise ParamError(f"'{what}' must be text", *_where(node, source))
    try:
        _pack_text(bytearray(), b, cap, what, "yaml")
    except FormatError as e:
        raise ParamError(str(e).split(": ", 1)[1], *_where(node, source)) from None
    # The form parse gives for these bytes, so YAML -> model -> bytes -> model is stable (fuzz finding
    # ddo_params_yaml-invariant-a3d01b8f23a3: {hex: ...} of Shift-JIS text stayed bytes, parse gave text).
    return _text(b.encode("cp932") if isinstance(b, str) else bytes(b))


def _from_node(f: Field, node, what: str, source):
    from .yamlish import Map, Seq

    k = f.kind
    if k == "s":
        return _from_scalar(f.t, node, what, source)
    if k == "str":
        return _from_text(node, f.cap, what, source)
    if k == "ref":
        if not isinstance(node, Seq) or len(node.items) not in (0, 2):
            raise ParamError(f"'{what}' is a resource reference: [class, path], or [] for none", *_where(node, source))
        if not node.items:
            return None
        cls = _from_text(node.items[0], REF_CAP, what + " class", source)
        if cls in ("", b""):
            raise ParamError(f"'{what}': a reference needs a class name; write [] for none", *_where(node, source))
        return [cls, _from_text(node.items[1], REF_CAP, what + " path", source)]
    if k == "b":
        if not isinstance(node, Map):
            raise ParamError(f"'{what}' must be a block of fields", *_where(node, source))
        return _from_block(f.fields, node, what + ".", source)
    if k in ("a", "l"):
        if not isinstance(node, Seq):
            raise ParamError(f"'{what}' must be a list", *_where(node, source))
        if k == "a" and len(node.items) != f.n:
            raise ParamError(f"'{what}' holds {f.n} values, not {len(node.items)}", *_where(node, source))
        if k == "l" and f.cap is not None and len(node.items) > f.cap:
            raise ParamError(f"'{what}': {len(node.items)} items; the game's member holds at most {f.cap}",
                             *_where(node, source))
        return [_from_node(f.item, x, f"{what}[{i}]", source) for i, x in enumerate(node.items)]
    raise AssertionError(k)


def _from_block(fields, node, where: str, source, top: bool = False) -> dict:
    from .yamlish import Map

    known = set(_names(fields)) | ({"riftstone", "resource"} if top else set())
    for k, _ in node.items:
        if k.text not in known:
            raise ParamError(f"'{where}{k.text}' is not a field of this format", k.line, k.col, source)
    out: dict = {}
    for f in fields:
        if f.kind == "k":
            continue
        if f.kind == "z":
            for g in f.fields:
                v = node.get(g.name)
                if v is None:
                    raise ParamError(f"'{where}{g.name}' is missing", *_where(node, source))
                out[g.name] = _from_node(A(g.name, g, f.n), v, where + g.name, source)
            continue
        v = node.get(f.name)
        if f.kind == "if":
            on = out[f.flag]
            if v is not None and not isinstance(v, Map):
                raise ParamError(f"'{where}{f.name}' must be a block of fields", *_where(v, source))
            if on and v is None:
                raise ParamError(f"'{where}{f.flag}' is {on} but the '{f.name}' block is missing",
                                 *_where(node, source))
            if not on and v is not None:
                raise ParamError(f"'{where}{f.name}' is present but '{f.flag}' is 0 (set it to 1, or remove the "
                                 f"block)", *_where(v, source))
            out[f.name] = _from_block(f.fields, v, where + f.name + ".", source) if v is not None else None
            continue
        if v is None:
            raise ParamError(f"'{where}{f.name}' is missing", *_where(node, source))
        out[f.name] = _from_node(f, v, where + f.name, source)
    return out


def from_yaml(text: str, source: str | None = None) -> DdoParams:
    from . import yamlish
    from .yamlish import Map, Scalar

    doc = yamlish.parse(text, source)
    tag = doc.get("riftstone") if isinstance(doc, Map) else None
    spec = next((k for k in KINDS.values() if isinstance(tag, Scalar) and tag.text == k.tag), None)
    if spec is None:
        raise ParamError("not a Riftstone DDO parameter file (expected 'riftstone: " +
                         "' or '".join(k.tag for k in KINDS.values()) + "')", 1, 1, source)
    m = DdoParams(spec.ext, _from_block(spec.fields, doc, "", source, top=True))
    try:
        build(m)
    except FormatError as e:
        raise ParamError(str(e), None, None, source) from None
    return m


def yaml_to_bytes(text: str, source: str | None = None) -> bytes:
    return build(from_yaml(text, source))


# -- reading aids ------------------------------------------------------------------------------------
def _f(bits: int) -> float:
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def summary(m: DdoParams) -> str:
    """A few lines for inspect."""
    d = m.data
    spec = KINDS[m.kind]
    head = f"{spec.cls} ({spec.title})"
    if m.kind == "cpe":
        fly = d["cCharParamEnemyFly"]
        lines = [f"{head}: {'a flying enemy (cCharParamEnemyFly)' if fly else 'a ground enemy'}",
                 "  attack phys/magick {:g}/{:g}, defense {:g}/{:g}, weight {:g}, model scale {:g}".format(
                     *(_f(d[k]) for k in ("基礎物理攻撃力", "基礎魔法攻撃力", "基礎物理防御力", "基礎魔法防御力",
                                          "重量", "モデルスケール値")))]
        if fly:
            lines.append("  hover speed {:g}, hover altitudes {}, flight speed {:g}, altitude {:g}".format(
                _f(fly["ホバリング速度"]), [round(_f(x), 3) for x in fly["ホバリング高度"]],
                _f(fly["飛行速度"]), _f(fly["飛行高度"])))
        return "\n".join(lines)
    if m.kind == "sti":
        refs = []
        for f in spec.fields:
            if f.kind == "ref" and d[f.name]:
                refs.append(f"{f.name}={d[f.name][0]!s}")
            elif f.kind == "a" and f.item.kind == "ref":
                refs += [f"{f.name}[{i}]={r[0]!s}" for i, r in enumerate(d[f.name]) if r]
        return "\n".join([f"{head}: model {d['mMdlSdlPath']!s}, effects {d['mEpvPath']!s}",
                          f"  start at [{', '.join(f'{_f(b):.1f}' for b in d['mPos'])}], angle {_f(d['mAng']):g}",
                          "  resources: " + (", ".join(refs) if refs else "none")])
    if m.kind == "sal":
        return (f"{head}: stage {d['mUnk90']}, {len(d['mAdjoinInfo'])} adjoin entries, "
                f"{len(d['mJumpPosition'])} jump positions")
    rows = d["mpArray"]
    if m.kind == "evtr":
        from collections import Counter
        per = Counter(_type_note(r["mUnk08"]) or f"type {r['mUnk08'] >> 32:08x}" for r in rows)
        return f"{head}: {len(rows)} entries: " + ", ".join(f"{c} x{n}" for c, n in per.most_common())
    if m.kind == "prs":
        return f"{head}: {len(rows)} regions, {sum(len(r['mRegionBreakInfo']['mpArray']) for r in rows)} break entries"
    if m.kind == "ndp":
        if not rows:
            return f"{head}: no records"
        ids = [r["mID"] for r in rows]
        neutral = sum(1 for r in rows if r["mHpRate"] == 100 and all(r[k] == 100 for k in NDP_RATES))
        dup = len(ids) - len(set(ids))
        return "\n".join([f"{head}: {len(rows)} records, ids {min(ids)}..{max(ids)}"
                          + (f", {dup} repeated (the later one wins in the game's id table)" if dup else ""),
                          f"  {neutral} leave every stat at 100%; HP {min(r['mHpRate'] for r in rows)}.."
                          f"{max(r['mHpRate'] for r in rows)}%"
                          + ("; the largest id is a multiple of 4: the game's id table has no slot for it"
                             if max(ids) % 4 == 0 else "")])
    return f"{head}: {len(rows)} records"

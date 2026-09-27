"""Spawn and object layouts (rLayout, ``.lot``): every record a stage places -- enemies, NPCs and
hostile humans, objects, doors, static models, AI sensor targets -- decoded field by field.

The grammar is DDDA.exe's own (Steam build 2364871), read from its loaders rather than guessed:

  rLayout::load            0x00CC1790   "lot\\0", u32 version (16), u32 record count, the records
  rLayout::SetInfo::load   0x00CC1F60   per record: s32 id, u32 kind (the loader refuses kind >= 75),
                                        then the class that kind names loads itself
  nLayout set-info table   0x017EE988   kind -> class (74 entries; 0..54 as on PS3, 55..73 added in DA)

A class's loader reads its own fields first and then calls its parent's, so a record is the most
derived class's fields, then the parent's, ..., and cSetInfoCoord's (name and transform) last.
Field names are the engine's, from each class's createProperty (a few are the developers'
Japanese; ``GLOSS`` translates them).  Proved on all 6,209 distinct layouts in the game: every byte
of every record is a named field and parse -> build is byte-for-byte (``check_corpus --only lot``).

Which file is which: ``scr\\st<S>\\etc\\st<S>_<X>m<Z>n_<t><N>`` holds the placements of group N of
stage S's group list ``st<S>_<t>.gpl`` in map cell (X, Z) -- ``t`` is e (enemies), n (NPCs and
hostile humans), p (objects), t (AI sensor targets); ``s00`` files hold a cell's static models and
belong to no group.  The engine builds exactly this name (``scr\\st%03d\\etc\\st%03d_%02dm%02dn%s%02d``,
0x01562268) when it loads a group.

Limits the engine imposes (and Riftstone enforces):
  * record ids are indexes into a 1024-entry table the loader fills (0x00CC18B7): 0..1023;
  * ``mFsmFilePath`` is read into a 64-byte buffer (63 characters), other strings into 256;
  * a sensor target's ``mActParamIdx`` / ``mActParamQuestNo`` are copied into 16-entry buffers.
"""
from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass, field

from .errors import FormatError, ParamError

MAGIC = b"lot\0"
VERSION = 16
HEADER = struct.Struct("<4sII")
TAG = "lot/2"
LEGACY_TAG = "lot/1"
MAX_ID = 1023           # the loader's id -> index table has 1024 entries
MAX_KIND = 74           # rLayout::SetInfo::load refuses kind >= 0x4B

# -- the classes ------------------------------------------------------------------------------
# Field types: u8 s16 u16 s32 u32 (integers), f32 (kept as its exact 32-bit pattern), v3 (three
# f32), str (NUL-terminated), sensor (an optional polymorphic cAISensorTarget), s16list (u32 count,
# then that many s16).  A u8 the engine reads as a flag is still kept as the stored byte.
COORD = [("mSetID", "s32"), ("mName", "str"), ("mOrder", "u32"), ("mPosition", "v3"), ("mAngle", "v3"),
         ("mScale", "v3"), ("mDrawDistance", "f32"), ("mIsOnSplitAreaIgnore", "u8")]
PAWN = [("mAreaHitNo", "s32"),
        ("HP倍率設定の有無", "u8"), ("HPの倍率", "f32"),
        ("攻撃力倍率設定の有無", "u8"), ("攻撃力の変化倍率", "f32"),
        ("防御力倍率設定の有無", "u8"), ("防御力の変化倍率", "f32"),
        ("魔法力倍率設定の有無", "u8"), ("魔法力の変化倍率", "f32"),
        ("魔法防御力倍率設定の有無", "u8"), ("魔法防御力の変化倍率", "f32"),
        ("mAIKnowledgeFlag", "u32"), ("mAIKind", "u32"),
        ("mIsAINoTarget", "u8"), ("mIsUseItemEveryone", "u8"), ("mIsDragonBallDrop", "u8")] + COORD
ENEMY = [("mLifePointGroup", "u32"), ("mUseFirstSetThinkTbl", "u8"), ("mStartTableNo", "s32"),
         ("mBossFlag", "u8"), ("mBgmBossFlag", "u8"), ("mEmItemFlag", "s32"), ("mEmItemTable", "s32"),
         ("mDieSet", "u32"), ("死体速攻消滅フラグ", "u8"), ("死亡消滅時間延長", "u8"), ("mRandomSetIgnore", "u8"),
         ("mExperienceOW", "u32"), ("mFsmFilePath", "str"), ("センサー半径倍率を使用する", "u8"),
         ("センサー半径倍率", "f32")] + PAWN
_EM0100 = [("mEquipType", "u32"), ("mIsLeader", "u8"), ("mIsWeaponHandStart", "u8"), ("mIsBallistaLicense", "u8")]
_EM0101 = [("mEquipType", "u32"), ("mIsLeader", "u8"), ("mIsWeaponHandStart", "u8")]
_EM0103 = _EM0100 + [("mType", "s32")]
_EM0200 = [("mWolfType", "u32"), ("mIsRandamScale", "u8"), ("mWolfScale", "f32")]
_EM0400 = [("壁スタート", "u8"), ("光学迷彩", "u8")]
_EM0500 = [("mPartsVariationNo", "u32")]
_EM0503 = [("mEquipType", "s32")] + _EM0500
_EM0600 = [("mHoverMainMode", "u8"), ("mbOmBreakCheck", "u8"), ("mOmBreakGroup", "u32"), ("mOmBreakID", "u32"),
           ("mHoverActionStart", "u8")]
_EM0700 = [("mPowerUpLv", "u32")]
_EM5000 = [("mType", "u32"), ("mWeaponType", "u32"), ("mTuskType", "u32"), ("西部サイクロ", "u8"),
           ("覚者の証サイクロ", "u8"), ("覚者の証サイクロのときの経験値", "u32"),
           ("覚者の証サイクロのときの残りHPの倍率", "f32"), ("覚者の証サイクロのときの足ののけぞり値", "f32"),
           ("覚者の証サイクロのときの足のぶっとび値", "f32")]
_EM5001 = [("mType", "u32"), ("mIsWeaponInitRemove", "u8"), ("mWeaponInitPos", "v3"), ("mWeaponInitAngle", "v3")]
_EM5100 = [("mInitMediumBreak", "u32")]
_EM5101 = [("mGolemNo", "u32")]
_EM5101_00 = [("mGolemNo", "u32"), ("mMediumRegionNo", "u32")]
_EM5200 = [("死亡演出カメラをする", "u8")]
_EM5300 = [("mStartWaitTime", "f32")]
_EM5500B = [("触手が魔法出来る数", "u32"), ("触手がワープ出来る数", "u32"), ("触手がワープ出来る範囲", "v3")]
_EM5800 = [("mSeqButtleSts", "u32")]
_DIESET = [("mIsDieSet", "u8")]
_EM5900 = [("mSetType", "u32"), ("飛ばない", "u8"), ("固有喋り", "u8"), ("リッチひょうい", "u8"), ("旋回飛行しない", "u8")]
_EM6000 = [("死神特別設置か？", "u8"), ("死神特別設置フレーム", "f32")]
_EM9807 = [("動作時間(秒)", "f32"), ("サウンドOFF", "u8")]
INSMODEL = [("mLightGroup", "u32"), ("mOverwriteLightGroup", "u8"), ("mTransMode", "u32"),
            ("mOverwriteTransMode", "u8")] + PAWN
OMMODEL = [("mSetTableID", "s16"), ("mSetTableIDNight", "s16"), ("mSetItemNo", "s16"), ("mSetItemNoNight", "s16"),
           ("mStartHour", "s32"), ("mEndHour", "s32"), ("mFree00", "u32"), ("mFree01", "u32"), ("mAngleType", "u32"),
           ("mFlag", "u32")] + PAWN
_SEMAP = [("mSeGroupId", "s16"), ("mSePriority", "s16"), ("mMapIconType", "s16")]                  # cOmSeMapParam
_DOORMSG = [("mLockMsgNo", "s16"), ("mOutMsgNo", "s16"), ("mOpenMsgNo", "s16"), ("mCheckAngle", "f32"),
            ("mCheckRange", "f32")]                                                               # cOmDoorMsgParam
_ANGLE = [("mCheckAngle", "f32"), ("mCheckRange", "f32")]                                         # cOmAngleParam
NPC = [("mNpcPriority", "s32"), ("FSMPath", "str"), ("mNpcId", "s32"), ("mScrAdjustOff", "u8"),
       ("mObjAdjustOff", "u8"), ("mPriority", "s32"), ("mGoodsOff", "u8"), ("mSimpleModelType", "s32"),
       ("mPartsVariationNo", "u32"), ("mClothType", "u32"), ("mUseMouseJoint", "u8"), ("mHumanEnemyKind", "u32"),
       ("mHumanEnemyID", "u32"), ("mRank", "u32"), ("mMultiNpcKind", "u32"), ("mUse24Schedule", "u8"),
       ("mIsHumanEnemyLeader", "u8"), ("mExperienceOW", "u32"), ("mpTarget", "sensor"), ("外部指定", "u8"),
       ("待機行動タイプ", "s32"), ("内部フラグ", "s32"), ("待機行動番号", "s32"), ("基本待機時間", "f32"),
       ("ランダム待機時間", "f32"), ("しぐさ行動番号", "s32"), ("基本しぐさ時間", "f32"),
       ("ランダムしぐさ時間", "f32")] + PAWN

# cAISensorTarget and its subclasses (loaded by class name; each loader reads its fields, then the base's)
_SENSOR_BASE = [("mTypeBit", "u32"), ("mSndLv", "s32"), ("mStatusFlag", "u32"), ("mPos", "v3"), ("mDir", "v3"),
                ("mRange", "f32"), ("mObjectID", "s32"), ("mAttr", "u32"), ("mSphereRange", "f32"), ("mJntNo", "s32")]
SENSORS = {
    "cAISensorTarget": _SENSOR_BASE,
    "cAISensorTargetGeneralPoint": [("mGroup", "u32"), ("mId", "u32"), ("mActType", "u32"), ("mIsAllDay", "u8"),
                                    ("mSTime", "u32"), ("mETime", "u32"), ("mShape", "s32"),
                                    ("mSize", "v3")] + _SENSOR_BASE,
    "cAISensorTargetNpc": [("mEnable", "u8"), ("mActType", "u32"), ("mMotType", "u32"), ("mActFrame", "f32"),
                           ("mNearFlag", "u8"), ("mRotFlag", "u8"), ("mIsAllDay", "u8"), ("mStartTime", "u32"),
                           ("mEndTime", "u32"), ("mFix", "u8"), ("mMessQuestNo", "s32"), ("mMessId", "s32"),
                           ("mMessDispSec", "f32"), ("mNpcId", "s32")] + _SENSOR_BASE,
    "cAISensorTargetStageAction": [("mStageActionType", "u32"), ("mPawnID", "s32"), ("mOnOffKind", "u32"),
                                   ("mResetTime", "f32"), ("mWeight", "u32"), ("CHK_TYPE", "u8"),
                                   ("ITEM_NO", "u16"), ("ITEM_NUM", "u8"), ("mActParamIdx", "s16list"),
                                   ("mActParamQuestNo", "s16list")] + _SENSOR_BASE,
    "cAISensorTargetUnit": [("mOffsetPos", "v3"), ("mOffsetDir", "v3")] + _SENSOR_BASE,
}
S16LIST_MAX = 16

# kind -> (class, fields).  Kind 0 (cSetInfo) is abstract; the loader would create it but it has no
# fields, and no layout uses it.  55 repeats cSetInfoEnemy0102 in the exe's table.
_E = ENEMY
KINDS: dict[int, tuple[str, list]] = {
    1: ("cSetInfoCoord", COORD), 2: ("cSetInfoPawn", PAWN), 3: ("cSetInfoEnemy", _E),
    4: ("cSetInfoEnemy0100", _EM0100 + _E), 5: ("cSetInfoEnemy0101", _EM0101 + _E),
    6: ("cSetInfoEnemy0102", _EM0101 + _E), 7: ("cSetInfoEnemy0200", _EM0200 + _E),
    8: ("cSetInfoEnemy0201", _EM0200 + _E), 9: ("cSetInfoEnemy0202", _EM0200 + _E),
    10: ("cSetInfoEnemy0400", _EM0400 + _E), 11: ("cSetInfoEnemy0401", _EM0400 + _E),
    12: ("cSetInfoEnemy0402", _EM0400 + _E), 13: ("cSetInfoEnemy0403", _EM0400 + _E),
    14: ("cSetInfoEnemy0500", _EM0500 + _E), 15: ("cSetInfoEnemy0501", _EM0500 + _E),
    16: ("cSetInfoEnemy0502", _EM0500 + _E), 17: ("cSetInfoEnemy0503", _EM0503 + _E),
    18: ("cSetInfoEnemy0504", _EM0500 + _E), 19: ("cSetInfoEnemy0600", _EM0600 + _E),
    20: ("cSetInfoEnemy0700", _EM0700 + _E), 21: ("cSetInfoEnemy0900", _E),
    22: ("cSetInfoEnemy2000", _EM0500 + _E), 23: ("cSetInfoEnemy5000", _EM5000 + _E),
    24: ("cSetInfoEnemy5100", _EM5100 + _E), 25: ("cSetInfoEnemy5101", _EM5101 + _E),
    26: ("cSetInfoEnemy5101_00", _EM5101_00 + _E), 27: ("cSetInfoEnemy5200", _EM5200 + _E),
    28: ("cSetInfoEnemy5300", _EM5300 + _E), 29: ("cSetInfoEnemy5500", _E),
    30: ("cSetInfoEnemy5500B", _EM5500B + _E), 31: ("cSetInfoEnemy5501", _E),
    32: ("cSetInfoEnemy5800", _EM5800 + _E), 33: ("cSetInfoEnemy5801", _DIESET + _E),
    34: ("cSetInfoEnemy5900", _EM5900 + _E), 35: ("cSetInfoEnemy6000", _EM6000 + _E),
    36: ("cSetInfoEnemy8000", _E), 37: ("cSetInfoEnemy8100", _E), 38: ("cSetInfoEnemy8200", _E),
    39: ("cSetInfoEnemy8300", _E), 40: ("cSetInfoEnemy8500", _E), 41: ("cSetInfoEnemy8600", _E),
    42: ("cSetInfoEnemy8700", _E), 43: ("cSetInfoEnemy8900", _E), 44: ("cSetInfoEnemy9000", _E),
    45: ("cSetInfoEnemy9807", _EM9807 + _E), 46: ("cSetInfoInsModel", INSMODEL), 47: ("cSetInfoNpc", NPC),
    48: ("cSetInfoOmModel", OMMODEL), 49: ("cSetInfoOmFSM", [("FSMPath", "str")] + OMMODEL),
    50: ("cSetInfoOmFsmPlusSM", _SEMAP + [("FSMPath", "str")] + OMMODEL),
    51: ("cSetInfoOmPlusAng", _ANGLE + OMMODEL), 52: ("cSetInfoOmPlusSM", _SEMAP + OMMODEL),
    53: ("cSetInfoOmDoorMsg", _DOORMSG + _SEMAP + OMMODEL), 54: ("cSetInfoSensorTarget", [("mpTarget", "sensor")]),
    55: ("cSetInfoEnemy0102", _EM0101 + _E), 56: ("cSetInfoEnemy0203", _EM0200 + _E),
    57: ("cSetInfoEnemy0204", _EM0200 + _E), 58: ("cSetInfoEnemy0404", _EM0400 + _E),
    59: ("cSetInfoEnemy0405", _EM0400 + _E), 60: ("cSetInfoEnemy0406", _EM0400 + _E),
    61: ("cSetInfoEnemy0407", _EM0400 + _E), 62: ("cSetInfoEnemy0408", _EM0400 + _E),
    63: ("cSetInfoEnemy0505", _EM0500 + _E), 64: ("cSetInfoEnemy0506", _EM0500 + _E),
    65: ("cSetInfoEnemy0507", _EM0500 + _E), 66: ("cSetInfoEnemy5001", _EM5001 + _E),
    67: ("cSetInfoEnemy7000", _DIESET + _E), 68: ("cSetInfoEnemy9100", _E), 69: ("cSetInfoEnemy0103", _EM0103 + _E),
    70: ("cSetInfoEnemy5500C", _EM5500B + _E), 71: ("cSetInfoEnemy7001", _E), 72: ("cSetInfoEnemy5400", _E),
    73: ("cSetInfoEnemy5401", _E),
}
KIND_OF_CLASS: dict[str, int] = {}
for _k, (_c, _f) in sorted(KINDS.items()):
    KIND_OF_CLASS.setdefault(_c, _k)

# What each class places, for people reading a layout
ROLES = {"cSetInfoCoord": "a bare position", "cSetInfoPawn": "a generic unit", "cSetInfoInsModel": "static model",
         "cSetInfoNpc": "NPC or hostile human (bandits, soldiers)", "cSetInfoOmModel": "object",
         "cSetInfoOmFSM": "object with a script", "cSetInfoOmFsmPlusSM": "object with a script and map icon",
         "cSetInfoOmPlusAng": "object used from an angle", "cSetInfoOmPlusSM": "object with sound / map icon",
         "cSetInfoOmDoorMsg": "door", "cSetInfoSensorTarget": "AI sensor target (a point pawns react to)"}


def role(cls: str) -> str:
    return ROLES.get(cls) or ("enemy" if cls.startswith("cSetInfoEnemy") else "")


# English for the developers' Japanese field names (and a few terse English ones)
GLOSS = {
    "HP倍率設定の有無": "use the HP multiplier", "HPの倍率": "HP multiplier",
    "攻撃力倍率設定の有無": "use the attack multiplier", "攻撃力の変化倍率": "attack multiplier",
    "防御力倍率設定の有無": "use the defence multiplier", "防御力の変化倍率": "defence multiplier",
    "魔法力倍率設定の有無": "use the magick multiplier", "魔法力の変化倍率": "magick multiplier",
    "魔法防御力倍率設定の有無": "use the magick defence multiplier", "魔法防御力の変化倍率": "magick defence multiplier",
    "死体速攻消滅フラグ": "corpse vanishes at once", "死亡消滅時間延長": "corpse stays longer",
    "センサー半径倍率を使用する": "use the sensor radius multiplier", "センサー半径倍率": "sensor radius multiplier",
    "壁スタート": "starts on a wall", "光学迷彩": "starts camouflaged (invisible)",
    "西部サイクロ": "western cyclops", "覚者の証サイクロ": "cyclops of the Arisen's proof",
    "覚者の証サイクロのときの経験値": "EXP as the proof cyclops",
    "覚者の証サイクロのときの残りHPの倍率": "remaining-HP multiplier as the proof cyclops",
    "覚者の証サイクロのときの足ののけぞり値": "leg flinch value as the proof cyclops",
    "覚者の証サイクロのときの足のぶっとび値": "leg knock-down value as the proof cyclops",
    "死亡演出カメラをする": "play the death camera",
    "触手が魔法出来る数": "tentacles that cast magick", "触手がワープ出来る数": "tentacle warps",
    "触手がワープ出来る範囲": "tentacle warp range",
    "飛ばない": "does not fly", "固有喋り": "own dialogue", "リッチひょうい": "possessed by a lich",
    "旋回飛行しない": "no circling flight", "死神特別設置か？": "special Death placement",
    "死神特別設置フレーム": "special Death placement frame", "動作時間(秒)": "active time (seconds)",
    "サウンドOFF": "sound off", "外部指定": "set from outside", "待機行動タイプ": "idle action type",
    "内部フラグ": "internal flag", "待機行動番号": "idle action number", "基本待機時間": "base idle time",
    "ランダム待機時間": "random idle time", "しぐさ行動番号": "gesture action number",
    "基本しぐさ時間": "base gesture time", "ランダムしぐさ時間": "random gesture time",
    "mEmItemTable": "drop set (row of etc/item/ItemEmListSetTbl)", "mExperienceOW": "EXP override (0: the enemy's own)",
    "mDrawDistance": "-1: default", "mAngle": "radians", "mOrder": "",
}

# An enemy skin (skins.py, the enemy_skins plugin) is named by the magick-defence multiplier's value
# with its flag off: the bits are SKIN_MARK_TAG | skin number.
SKIN_FLAG, SKIN_VALUE = "魔法防御力倍率設定の有無", "魔法防御力の変化倍率"
SKIN_MARK_TAG, SKIN_MARK_MASK = 0x534B0000, 0xFFFF0000

_INT = {"u8": ("<B", 0, 0xFF), "s16": ("<h", -0x8000, 0x7FFF), "u16": ("<H", 0, 0xFFFF),
        "s32": ("<i", -0x80000000, 0x7FFFFFFF), "u32": ("<I", 0, 0xFFFFFFFF)}
_STR_MAX = {"mFsmFilePath": 63}      # read into a 64-byte buffer; every other string into 256
STR_MAX = 255


# -- model -------------------------------------------------------------------------------------
@dataclass
class Sensor:
    cls: str
    fields: dict
    present: int = 1


@dataclass
class Record:
    id: int
    kind: int
    fields: dict

    @property
    def cls(self) -> str:
        return KINDS[self.kind][0]

    @property
    def name(self) -> str | None:
        v = self.fields.get("mName")
        return v.decode("utf-8", "surrogateescape") if isinstance(v, bytes) else None

    def vec(self, key: str = "mPosition") -> tuple[float, float, float] | None:
        v = self.fields.get(key)
        return struct.unpack("<3f", struct.pack("<3I", *v)) if v is not None else None

    def set_vec(self, key: str, xyz) -> None:
        if key not in self.fields:
            raise ParamError(f"a {self.cls} record has no {key}")
        try:
            vals = [float(v) for v in xyz]
        except (TypeError, ValueError):
            raise ParamError(f"{key} is three numbers") from None
        except OverflowError:                             # an integer past a float's range
            raise ParamError(f"{key} is three finite numbers") from None
        if len(vals) != 3 or not all(math.isfinite(v) and abs(v) < 3.4e38 for v in vals):
            raise ParamError(f"{key} is three finite numbers")
        self.fields[key] = struct.unpack("<3I", struct.pack("<3f", *vals))

    def copy(self) -> "Record":
        def dup(v):
            if isinstance(v, Sensor):
                return Sensor(v.cls, {k: dup(x) for k, x in v.fields.items()}, v.present)
            if isinstance(v, list):
                return list(v)
            return v
        return Record(self.id, self.kind, {k: dup(v) for k, v in self.fields.items()})


@dataclass
class Lot:
    records: list[Record] = field(default_factory=list)
    version: int = VERSION

    @property
    def count(self) -> int:
        return len(self.records)

    @property
    def placements(self) -> list[Record]:
        """The records that have a position (everything but AI sensor targets)."""
        return [r for r in self.records if "mPosition" in r.fields]


def schema(kind: int) -> list:
    if kind not in KINDS:
        raise ParamError(f"kind {kind} is not a layout record class")
    return KINDS[kind][1]


_F1, _FM1 = 0x3F800000, 0xBF800000         # 1.0 and -1.0 as stored bits


def blank(kind: int, rid: int = 0, **values) -> Record:
    """A record of this kind with every field zero, except a unit scale and the default draw distance
    (-1); ``values`` override fields by engine name (floats and xyz tuples are converted)."""
    fields = {}
    for name, t in schema(kind):
        fields[name] = {"f32": 0, "v3": (0, 0, 0), "str": b"", "s16list": [], "sensor": None}.get(t, 0)
    if "mScale" in fields:
        fields["mScale"] = (_F1, _F1, _F1)
    if "mDrawDistance" in fields:
        fields["mDrawDistance"] = _FM1
    rec = Record(rid, kind, fields)
    types = dict(schema(kind))
    for name, v in values.items():
        if name not in types:
            raise ParamError(f"a {rec.cls} record has no field {name}")
        t = types[name]
        if t == "v3" and all(isinstance(x, float) for x in v):
            rec.set_vec(name, v)
        elif t == "f32" and isinstance(v, float):
            rec.fields[name] = struct.unpack("<I", struct.pack("<f", v))[0]
        elif t == "str" and isinstance(v, str):
            rec.fields[name] = v.encode("utf-8", "surrogateescape")
        else:
            rec.fields[name] = v
    return rec


# -- binary ------------------------------------------------------------------------------------
class _Reader:
    def __init__(self, data, pos: int):
        self.d, self.p = data, pos

    def take(self, fmt: str):
        n = struct.calcsize(fmt)
        if self.p + n > len(self.d):
            raise FormatError("LOT", "the file ends inside a record", self.p)
        v = struct.unpack_from(fmt, self.d, self.p)
        self.p += n
        return v

    def string(self) -> bytes:
        end = self.d.find(b"\0", self.p)
        if end < 0:
            raise FormatError("LOT", "a string is not terminated", self.p)
        s = bytes(self.d[self.p:end])
        self.p = end + 1
        return s


def _read_fields(r: _Reader, fields: list) -> dict:
    out = {}
    for name, t in fields:
        if t in _INT:
            out[name] = r.take(_INT[t][0])[0]
        elif t == "f32":
            out[name] = r.take("<I")[0]
        elif t == "v3":
            out[name] = r.take("<3I")
        elif t == "str":
            at = r.p
            s = out[name] = r.string()
            limit = _STR_MAX.get(name, STR_MAX)
            if len(s) > limit:
                raise FormatError("LOT", f"{name} is {len(s)} bytes; the game reads at most {limit}", at)
        elif t == "s16list":
            n = r.take("<I")[0]
            if n > S16LIST_MAX:
                raise FormatError("LOT", f"{name} holds {n} entries; the game copies at most {S16LIST_MAX}", r.p - 4)
            out[name] = list(r.take(f"<{n}h")) if n else []
        elif t == "sensor":
            present = r.take("<I")[0]
            if not present:
                out[name] = None
                continue
            at = r.p
            cls = r.string().decode("latin-1")
            if cls not in SENSORS:
                raise FormatError("LOT", f"unknown sensor target class {cls!r}", at)
            out[name] = Sensor(cls, _read_fields(r, SENSORS[cls]), present)
        else:   # pragma: no cover - the schemas only use the types above
            raise AssertionError(t)
    return out


def parse(data: bytes) -> Lot:
    if len(data) < HEADER.size:
        raise FormatError("LOT", "file is shorter than the header", 0)
    magic, version, count = HEADER.unpack_from(data, 0)
    if magic != MAGIC:
        raise FormatError("LOT", "not a layout file (magic)", 0)
    if version != VERSION:
        raise FormatError("LOT", f"version {version}; the game loads version {VERSION} only", 4)
    if count > (len(data) - HEADER.size) // 8:
        raise FormatError("LOT", f"{count} records cannot fit in the file", 8)
    r = _Reader(data, HEADER.size)
    recs = []
    for i in range(count):
        at = r.p
        rid, kind = r.take("<iI")
        if kind not in KINDS:
            raise FormatError("LOT", f"record {i}: kind {kind} is not a record class the game loads", at + 4)
        recs.append(Record(rid, kind, _read_fields(r, KINDS[kind][1])))
    if r.p != len(data):
        raise FormatError("LOT", f"{len(data) - r.p} byte(s) after the last record", r.p)
    return Lot(recs, version)


def _write_fields(out: bytearray, fields: list, values: dict, where: str) -> None:
    for name, t in fields:
        if name not in values:
            raise ParamError(f"{where}: {name} is missing")
        v = values[name]
        try:
            if t in _INT:
                fmt, lo, hi = _INT[t]
                if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
                    raise ParamError(f"{where}: {name} must be a whole number from {lo} to {hi}")
                out += struct.pack(fmt, v)
            elif t == "f32":
                out += struct.pack("<I", v)
            elif t == "v3":
                if len(v) != 3:
                    raise ParamError(f"{where}: {name} is three numbers")
                out += struct.pack("<3I", *v)
            elif t == "str":
                if not isinstance(v, bytes) or b"\0" in v:
                    raise ParamError(f"{where}: {name} is text without a NUL")
                limit = _STR_MAX.get(name, STR_MAX)
                if len(v) > limit:
                    raise ParamError(f"{where}: {name} is {len(v)} bytes; the game reads at most {limit}")
                out += v + b"\0"
            elif t == "s16list":
                if len(v) > S16LIST_MAX:
                    raise ParamError(f"{where}: {name} holds {len(v)} entries; the game copies at most {S16LIST_MAX}")
                out += struct.pack(f"<I{len(v)}h", len(v), *v)
            elif t == "sensor":
                if v is None:
                    out += struct.pack("<I", 0)
                else:
                    if not isinstance(v, Sensor) or v.cls not in SENSORS or not 1 <= v.present <= 0xFFFFFFFF:
                        raise ParamError(f"{where}: {name} is none or a sensor target class")
                    out += struct.pack("<I", v.present) + v.cls.encode("latin-1") + b"\0"
                    _write_fields(out, SENSORS[v.cls], v.fields, f"{where}: {name}")
        except struct.error as e:
            raise ParamError(f"{where}: {name} does not fit its field ({e})") from None


def build(lot: Lot) -> bytes:
    if lot.version != VERSION:
        raise ParamError(f"version must be {VERSION} (the only one the game loads)")
    out = bytearray(HEADER.pack(MAGIC, lot.version, len(lot.records)))
    for i, rec in enumerate(lot.records):
        where = f"record {i}"
        if rec.kind not in KINDS:
            raise ParamError(f"{where}: kind {rec.kind} is not a record class the game loads")
        if isinstance(rec.id, bool) or not isinstance(rec.id, int) or not -0x80000000 <= rec.id <= 0x7FFFFFFF:
            raise ParamError(f"{where}: id must be a whole number")
        out += struct.pack("<iI", rec.id, rec.kind)
        _write_fields(out, KINDS[rec.kind][1], rec.fields, where)
    return bytes(out)


def check_ids(lot: Lot) -> None:
    """The ids the game can index: 0..1023 (the loader's table), each at most once.  Vanilla layouts
    satisfy this except one that repeats an id; edits that add records are held to it."""
    seen = set()
    for i, r in enumerate(lot.records):
        if not 0 <= r.id <= MAX_ID:
            raise ParamError(f"record {i}: id {r.id} is outside 0..{MAX_ID}, the range of the game's id table "
                             "(an id past it would overwrite memory next to the table)")
        if r.id in seen:
            raise ParamError(f"record {i}: id {r.id} is used twice")
        seen.add(r.id)


# -- editing -----------------------------------------------------------------------------------
KILL_BITS = 32      # an enemy group's kill record: one bit a placement id, past 31 the bit wraps (0x004A653D)


def free_id(lot: Lot, reserved=(), below: int | None = None) -> int:
    """A new record id, within the game's 0..1023: the largest + 1 of the layout's ids and ``reserved`` (ids the
    record's group uses in its other layouts, and ones the game's own copy of the layout has), else the smallest
    unused one.  With ``below`` (``KILL_BITS`` for an enemy group) the smallest unused id under it comes first when
    the largest + 1 is not under it."""
    ids = {r.id for r in lot.records} | {i for i in reserved if isinstance(i, int) and not isinstance(i, bool)}
    nxt = max(ids) + 1 if ids else 0
    if below is not None and nxt >= below:
        low = next((i for i in range(min(below, MAX_ID + 1)) if i not in ids), None)
        if low is not None:
            return low
    if 0 <= nxt <= MAX_ID:
        return nxt
    for i in range(MAX_ID + 1):
        if i not in ids:
            return i
    raise ParamError(f"the layout's ids are used up (the game indexes 0..{MAX_ID})")


def copy(lot: Lot, number: int, position: tuple[float, float, float] | None = None, reserved=(),
         below: int | None = None) -> Lot:
    """A new record: record ``number`` copied to the end with a new id (``free_id``), optionally moved."""
    if isinstance(number, bool) or not isinstance(number, int) or not 0 <= number < len(lot.records):
        raise ParamError(f"there is no record {number} (the layout has {len(lot.records)})")
    rec = lot.records[number].copy()
    rec.id = free_id(lot, reserved, below)
    if position is not None:
        if "mPosition" not in rec.fields:
            raise ParamError(f"record {number} ({rec.cls}) has no position")
        rec.set_vec("mPosition", position)
    return Lot([r.copy() for r in lot.records] + [rec], lot.version)


def remove(lot: Lot, number: int) -> Lot:
    """Record ``number`` removed.  The other records keep their ids (other data may refer to them)."""
    if isinstance(number, bool) or not isinstance(number, int) or not 0 <= number < len(lot.records):
        raise ParamError(f"there is no record {number} (the layout has {len(lot.records)})")
    return Lot([r.copy() for i, r in enumerate(lot.records) if i != number], lot.version)


def records(lot: Lot) -> list[Record]:
    return lot.records


# -- names -------------------------------------------------------------------------------------
TYPES = {"s": "static models", "p": "objects", "e": "enemies", "n": "NPCs and hostile humans",
         "t": "AI sensor targets"}
_LAYOUT_NAME = re.compile(r"^scr\\st(\d{3})\\etc\\st(\d{3})_(\d{2,})m(\d{2,})n_([spent])(\d{2,})$", re.I)


@dataclass(frozen=True)
class LayoutName:
    stage: int
    x: int
    z: int
    type: str           # s p e n t
    number: int         # the group of st<S>_<t>.gpl (s: always 0, no group)

    def __str__(self) -> str:
        return layout_name(self.stage, self.x, self.z, self.type, self.number)

    @property
    def group_list(self) -> str | None:
        return None if self.type == "s" else f"scr\\st{self.stage:03d}\\etc\\st{self.stage:03d}_{self.type}"


def parse_name(name: str) -> LayoutName | None:
    """``scr\\st100\\etc\\st100_43m55n_e67`` -> stage 100, cell (43, 55), enemies, group 67."""
    m = _LAYOUT_NAME.match(name.replace("/", "\\"))
    if not m or m.group(1) != m.group(2):
        return None
    return LayoutName(int(m.group(1)), int(m.group(3)), int(m.group(4)), m.group(5).lower(), int(m.group(6)))


def layout_name(stage: int, x: int, z: int, type_: str, number: int) -> str:
    """The resource name the engine builds for a group's layout in a cell (0x01562268)."""
    return f"scr\\st{stage:03d}\\etc\\st{stage:03d}_{x:02d}m{z:02d}n_{type_}{number:02d}"


# -- YAML --------------------------------------------------------------------------------------
def _text(b: bytes) -> str:
    return b.decode("utf-8", "surrogateescape")


def _node(t: str, v):
    from .params import f32_bits_text
    from .yamlish import Scalar, Seq

    if t in _INT:
        return Scalar(str(v))
    if t == "f32":
        return Scalar(f32_bits_text(v))
    if t == "v3":
        return Seq([Scalar(f32_bits_text(x)) for x in v], flow=True)
    if t == "str":
        return Scalar(_text(v), "double")
    if t == "s16list":
        return Seq([Scalar(str(x)) for x in v], flow=True)
    raise AssertionError(t)


def _fields_map(fields: list, values: dict, lead: list) -> "Map":
    from .yamlish import Map, Scalar

    items = list(lead)
    for name, t in fields:
        v = values[name]
        if t == "sensor":
            if v is None:
                node = Scalar("null")
            else:
                head = [(Scalar("class"), Scalar(v.cls))]
                if v.present != 1:
                    head.append((Scalar("present"), Scalar(str(v.present))))
                node = _fields_map(SENSORS[v.cls], v.fields, head)
        else:
            node = _node(t, v)
            gloss = GLOSS.get(name)
            if name == SKIN_VALUE and values.get(SKIN_FLAG) == 0 and isinstance(v, int) \
                    and (v & SKIN_MARK_MASK) == SKIN_MARK_TAG and 1 <= v & 0xFFFF <= 99:
                gloss = f"enemy skin {v & 0xFFFF}: an enemy_skins marker; the multiplier is off, so no stat changes"
            if gloss and hasattr(node, "comment"):
                node.comment = gloss
        items.append((Scalar(name), node))
    return Map(items)


def to_yaml(lot: Lot, name: str | None = None) -> str:
    from . import yamlish
    from .yamlish import Map, Scalar, Seq

    head = ["Riftstone layout file (.lot)" + (f" -- {name}" if name else "")]
    ln = parse_name(name) if name else None
    if ln is not None:
        cell = f"map cell {ln.x:02d}m{ln.z:02d}n"
        if ln.type == "s":
            head.append(f"The static models of stage {ln.stage}, {cell}.")
        else:
            head.append(f"Group {ln.number} of stage {ln.stage}'s {TYPES[ln.type]} (st{ln.stage:03d}_{ln.type}.gpl), {cell}.")
    head += [f"{len(lot.records)} record(s). Each is one thing the game places: its id (unique, 0..{MAX_ID}),",
             "its class, then every field the game's loader reads, by the engine's own names.",
             "Positions are [x, y, z]; mAngle is radians. Rebuilds byte-for-byte when untouched.",
             "Add a record by copying one (give it a new id); remove one by deleting its block."]
    items = [(Scalar("riftstone"), Scalar(TAG))]
    if name:
        items.append((Scalar("resource"), Scalar(name, "double")))
    items.append((Scalar("version"), Scalar(str(lot.version))))
    rows = []
    for r in lot.records:
        cls_node = Scalar(r.cls)
        what = role(r.cls)
        cls_node.comment = f"kind {r.kind}" + (f", {what}" if what else "")
        lead = [(Scalar("id"), Scalar(str(r.id))), (Scalar("class"), cls_node)]
        if KIND_OF_CLASS[r.cls] != r.kind:
            lead.append((Scalar("kind"), Scalar(str(r.kind))))
        rows.append(_fields_map(KINDS[r.kind][1], r.fields, lead))
    items.append((Scalar("records"), Seq(rows)))
    return yamlish.emit(Map(items), head)


def _where(node):
    return getattr(node, "line", None), getattr(node, "col", None)


def _value(t: str, node, name: str, source):
    from .params import f32_bits
    from .yamlish import Scalar, Seq

    if t in _INT:
        _fmt, lo, hi = _INT[t]
        if not isinstance(node, Scalar):
            raise ParamError(f"{name} is a whole number", *_where(node), source)
        try:
            v = int(node.text.strip(), 0)
        except ValueError:
            raise ParamError(f"{name}: {node.text!r} is not a whole number", node.line, node.col, source) from None
        if not lo <= v <= hi:
            raise ParamError(f"{name} must be from {lo} to {hi}", node.line, node.col, source)
        return v
    if t == "f32":
        if not isinstance(node, Scalar):
            raise ParamError(f"{name} is a number", *_where(node), source)
        try:
            return f32_bits(node.text)
        except (ValueError, OverflowError):
            raise ParamError(f"{name}: {node.text!r} is not a number that fits a 32-bit float", node.line, node.col,
                             source) from None
    if t == "v3":
        if not isinstance(node, Seq) or len(node.items) != 3:
            raise ParamError(f"{name} is [x, y, z]", *_where(node), source)
        return tuple(_value("f32", x, name, source) for x in node.items)
    if t == "str":
        if not isinstance(node, Scalar):
            raise ParamError(f"{name} is text", *_where(node), source)
        if "\0" in node.text:
            raise ParamError(f"{name} cannot contain a NUL", node.line, node.col, source)
        try:
            b = node.text.encode("utf-8", "surrogateescape")
        except UnicodeEncodeError:        # a lone surrogate outside \udc80-\udcff (the escapes of stored bytes)
            raise ParamError(f"{name} holds a character that is not valid text (a lone surrogate)", node.line,
                             node.col, source) from None
        limit = _STR_MAX.get(name, STR_MAX)
        if len(b) > limit:
            raise ParamError(f"{name} is {len(b)} bytes; the game reads at most {limit}", node.line, node.col, source)
        return b
    if t == "s16list":
        if not isinstance(node, Seq):
            raise ParamError(f"{name} is a list of numbers", *_where(node), source)
        if len(node.items) > S16LIST_MAX:
            raise ParamError(f"{name} holds {len(node.items)} entries; the game copies at most {S16LIST_MAX}",
                             *_where(node), source)
        return [_value("s16", x, name, source) for x in node.items]
    raise AssertionError(t)


def _fields_from(fields: list, node, skip: set, what: str, source) -> dict:
    from .yamlish import Map, Scalar

    if not isinstance(node, Map):
        raise ParamError(f"{what} is a block of fields", *_where(node), source)
    known = {n for n, _ in fields} | skip
    for k, _v in node.items:
        if k.text not in known:
            raise ParamError(f"{what} has no field {k.text!r}", k.line, k.col, source)
    out = {}
    for name, t in fields:
        v = node.get(name)
        if v is None:
            raise ParamError(f"{what}: {name} is missing", *_where(node), source)
        if t == "sensor":
            if isinstance(v, Scalar) and v.style == "plain" and v.text in ("null", "~", "none"):
                out[name] = None
                continue
            if not isinstance(v, Map):
                raise ParamError(f"{name} is null or a block with 'class:'", *_where(v), source)
            cls = v.get("class")
            if not isinstance(cls, Scalar) or cls.text not in SENSORS:
                raise ParamError(f"{name}: class is one of {', '.join(SENSORS)}", *_where(cls or v), source)
            present = 1
            pn = v.get("present")
            if pn is not None:
                present = _value("u32", pn, "present", source)
                if not present:
                    raise ParamError("present is 1 or more (write null for no sensor target)", *_where(pn), source)
            out[name] = Sensor(cls.text, _fields_from(SENSORS[cls.text], v, {"class", "present"},
                                                      f"{what}: {name}", source), present)
        else:
            out[name] = _value(t, v, name, source)
    return out


def from_yaml(text: str, source: str | None = None) -> Lot:
    from . import yamlish
    from .yamlish import Map, Scalar, Seq

    doc = yamlish.parse(text, source)
    tag = doc.get("riftstone") if isinstance(doc, Map) else None
    if isinstance(tag, Scalar) and tag.text == LEGACY_TAG:
        return parse(_legacy_bytes(doc, source))
    if not isinstance(tag, Scalar) or tag.text != TAG:
        raise ParamError(f"not a Riftstone layout file (expected 'riftstone: {TAG}')", 1, 1, source)
    for k, _v in doc.items:
        if k.text not in ("riftstone", "resource", "version", "records"):
            raise ParamError(f"unknown top-level key {k.text!r}", k.line, k.col, source)
    ver = doc.get("version")
    version = _value("u32", ver, "version", source) if ver is not None else VERSION
    if version != VERSION:
        raise ParamError(f"version must be {VERSION} (the only one the game loads)", *_where(ver), source)
    rows = doc.get("records")
    if rows is None:
        rows = Seq([])
    if not isinstance(rows, Seq) or rows.flow and rows.items:
        raise ParamError("records is a list of blocks", *_where(rows), source)
    out = []
    for i, row in enumerate(rows.items):
        if not isinstance(row, Map):
            raise ParamError("each record is a block of fields", *_where(row), source)
        cls = row.get("class")
        if not isinstance(cls, Scalar) or cls.text not in KIND_OF_CLASS:
            raise ParamError("class is the record's class (cSetInfoEnemy0100, cSetInfoOmModel, ...)",
                             *_where(cls or row), source)
        kind = KIND_OF_CLASS[cls.text]
        kn = row.get("kind")
        if kn is not None:
            kind = _value("u32", kn, "kind", source)
            if KINDS.get(kind, ("",))[0] != cls.text:
                raise ParamError(f"kind {kind} is not class {cls.text}", *_where(kn), source)
        idn = row.get("id")
        if idn is None:
            raise ParamError(f"record {i}: id is missing", *_where(row), source)
        rid = _value("s32", idn, "id", source)
        out.append(Record(rid, kind, _fields_from(KINDS[kind][1], row, {"id", "class", "kind"}, f"record {i}", source)))
    return Lot(out, version)


def yaml_to_bytes(text: str, source: str | None = None) -> bytes:
    return build(from_yaml(text, source))


# -- the earlier YAML form (riftstone: lot/1) ---------------------------------------------------
# Mods made before the full decode hold layouts as the file in hex plus the placements Riftstone
# could find (name + a 45-byte transform block).  They still load: the edits are applied to the
# bytes the way that version did, then the result is read with the full grammar.
_BLOCK = 45
_F10 = struct.Struct("<10f")


def _legacy_ok(lead: int, f: tuple) -> bool:
    pos, rot, scl, extra = f[0:3], f[3:6], f[6:9], f[9]
    return (lead in (0, 4) and all(math.isfinite(x) for x in f)
            and (extra == -1.0 or 1.0 <= extra <= 1e8)
            and all(abs(x) < 1e6 for x in pos) and all(abs(x) <= 7.0 for x in rot)
            and all(0.001 <= abs(x) <= 1000.0 for x in scl))


def _legacy_bytes(doc, source) -> bytes:
    from .params import f32_bits
    from .yamlish import Map, Scalar, Seq

    body = doc.get("body")
    if not isinstance(body, Scalar):
        raise ParamError("body (the file in hex) is missing", None, None, source)
    try:
        data = bytearray(bytes.fromhex(body.text))
    except ValueError:
        raise ParamError("body is not valid hex", body.line, body.col, source) from None
    if len(data) < HEADER.size or data[:4] != MAGIC:
        raise ParamError("body does not start with a layout header", body.line, body.col, source)
    rows = doc.get("placements")
    if rows is None:
        return bytes(data)
    if not isinstance(rows, Seq):
        raise ParamError("placements is a list", *_where(rows), source)
    seen = set()
    for row in rows.items:
        if not isinstance(row, Map):
            raise ParamError("each placement is a block of fields", *_where(row), source)
        for k, _ in row.items:
            if k.text not in ("name", "offset", "position", "rotation", "scale", "f40"):
                raise ParamError(f"a placement has name, offset, position, rotation, scale and f40, not {k.text!r}",
                                 k.line, k.col, source)
        off, nm = row.get("offset"), row.get("name")
        if not isinstance(off, Scalar) or not isinstance(nm, Scalar):
            raise ParamError("each placement needs its name and offset", *_where(row), source)
        try:
            o = int(off.text, 0)
        except ValueError:
            raise ParamError("offset is a number", off.line, off.col, source) from None
        raw_name = nm.text.encode("ascii", "replace") + b"\0"
        if not (HEADER.size + len(raw_name) <= o <= len(data) - _BLOCK) or data[o - len(raw_name):o] != raw_name:
            from .params import shown           # the offset as written: a huge hex one has no decimal form
            raise ParamError(f"no placement named {shown(nm.text)!r} sits at offset {shown(off.text)}; name and "
                             "offset must stay as they were", off.line, off.col, source)
        if o in seen:
            raise ParamError(f"offset {o} is listed twice", off.line, off.col, source)
        seen.add(o)
        values = list(struct.unpack_from("<10I", data, o + 4))
        for key, start, n in (("position", 0, 3), ("rotation", 3, 3), ("scale", 6, 3), ("f40", 9, 1)):
            node = row.get(key)
            if node is None:
                continue
            if key == "f40":
                node = Seq([node], flow=True) if isinstance(node, Scalar) else node
            if not isinstance(node, Seq) or len(node.items) != n or not all(isinstance(x, Scalar) for x in node.items):
                raise ParamError(f"{key} is [{', '.join('xyz'[:n])}]", *_where(node), source)
            for j, x in enumerate(node.items):
                try:
                    values[start + j] = f32_bits(x.text)
                except ValueError:
                    raise ParamError(f"{key}: {x.text!r} is not a number that fits a 32-bit float", x.line, x.col,
                                     source) from None
        f = struct.unpack("<10f", struct.pack("<10I", *values))
        if not _legacy_ok(struct.unpack_from("<I", data, o)[0], f):
            raise ParamError(f"{nm.text}: keep positions within +-1e6, rotations within +-7 radians, scales between "
                             "0.001 and 1000, and f40 at -1 or 1..1e8 (the ranges the game uses)", *_where(row), source)
        struct.pack_into("<10I", data, o + 4, *values)
    return bytes(data)

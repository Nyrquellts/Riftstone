"""Flat MT-Framework parameter formats: one schema-driven engine for the ~20 small binary
resource types (enemy adjust, character-creator edits, quest tables, equipment stats ...).

Each is the same shape -- a 4-byte magic, then scalars and length-prefixed record arrays -- so a
format here is just a *schema* (SCHEMAS below), not a module.  The layouts were read from Chris
Purnell's dd-tools (`vendor/dd-tools-main`) and each one is proved byte-exact against every instance
in the game (`check_corpus --only flat`); a wrong schema shows up immediately as a size or byte
mismatch, so the corpus is the check.

Schema fields (helpers S / A / L below):
  S(name, type)        one value           type in TYPES: i8 u8 s16 u16 i32 u32 s64 f32 f64
  A(name, elem, n)     n values            elem is a type, or a block (list of fields) for records
  L(name, elem)        u32 count, then that many values

The YAML shows scalars as numbers, arrays as lists, records as blocks.  Floats travel as their
exact 32-bit value (params.fmt_f32), so an untouched file rebuilds byte-for-byte.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from .errors import FormatError, ParamError

TYPES = {"i8": "<b", "u8": "<B", "s16": "<h", "u16": "<H", "i32": "<i", "u32": "<I",
         "s64": "<q", "u64": "<Q", "f32": "<f", "f64": "<d"}
_ST = {k: struct.Struct(v) for k, v in TYPES.items()}
FLOAT = {"f32", "f64"}


def S(name, tc):
    return ("s", name, tc)


def A(name, elem, n):
    return ("a", name, elem, n)


def L(name, elem):
    return ("l", name, elem)


def LN(name, elem, count_field):
    # a list whose length is a separate scalar field read earlier (not an inline prefix)
    return ("ln", name, elem, count_field)


def V(name):
    return A(name, "f32", 3)            # a 3-float vector


def C(name):
    return A(name, "f32", 4)            # a 4-float colour


@dataclass
class Flat:
    ext: str
    magic: int
    data: dict


# a quest-flag reference used inside AI action params (eap/sap): FlagType, QuestNo, FlagNo
_FLAG = [S("FlagType", "i8"), S("QuestNo", "i8"), S("FlagNo", "s16")]
# the AI ActionParam record shared by eap and sap.  FreeF32 is shown as float (its named intent);
# the bytes are the same whether read as i32 or f32, so the round trip stays byte-exact.
_ACTION_PARAM = [
    A("StudyID", "i32", 8), A("NoStudyID", "i32", 8), S("Goal", "i32"), S("Weight", "i32"), S("Rate", "i32"),
    S("ElementAttr", "i32"), S("NoElementAttr", "i32"), S("AtkAttr", "i32"), S("NoAtkAttr", "i32"),
    S("UseAttr", "i32"), S("NoUseAttr", "i32"), S("Angle", "i32"), S("CameraID", "i32"), S("MessageID", "i32"),
    S("MessageType", "i32"), S("GetStudyID", "i32"), S("StaminaCheckType", "i32"), S("JobBit", "i32"),
    S("ParamFlag", "i32"), A("FreeS32", "i32", 4), A("FreeF32", "f32", 4),
    A("SetOnFlag", _FLAG, 2), A("SetffFlag", _FLAG, 2),
]
# a magic-shot control block used three times per level in a magic act (map)
_SHL_PARAM = [S("shlGroup", "i32"), S("shlNo", "i32"), S("setType", "i32"), S("shotJointNo", "i32"),
              V("shotOffset"), V("shlScale"), S("shlShotNum", "i32"), S("shlSetWait", "f32")]


# -- schemas ---------------------------------------------------------------------------------
SCHEMAS: dict[str, tuple[int, list]] = {
    # ext: (magic u32, schema).  magic bytes spell the extension; e.g. "ajp\0" = 0x00706a61.
    "ajp": (0x00706A61, [S("version", "i32"), L("mpArray", "f32")]),
    "irp": (0x00707269, [S("version", "i32"),
                         L("mpArray", [S("type", "i32"), S("min1", "i32"), S("max1", "i32"),
                                       S("min2", "i32"), S("max2", "i32"), S("min3", "i32"), S("max3", "i32")])]),
    "itemlv": (0x00657469, [S("version", "i32"),
                            L("mpArray", [A("mUpParamLv1", "i32", 2), A("mUpParamLv2", "i32", 4),
                                          A("mUpParamLv3", "i32", 6), A("mUpParamLv4", "i32", 8),
                                          A("mUpParamLv5", "i32", 8), A("mUpParamLv6", "i32", 8),
                                          A("mUpRate1", "f32", 2), A("mUpRate2", "f32", 4),
                                          A("mUpRate3", "f32", 6), A("mUpRate4", "f32", 8),
                                          A("mUpRate5", "f32", 8), A("mUpRate6", "f32", 8),
                                          A("mIsDirectValue1", "i32", 2), A("mIsDirectValue2", "i32", 4),
                                          A("mIsDirectValue3", "i32", 6), A("mIsDirectValue4", "i32", 8),
                                          A("mIsDirectValue5", "i32", 8), A("mIsDirectValue6", "i32", 8)])]),
    # character creator (rBodyEdit/rFaceEdit/rHumanEdit/rHumanPartsEdit/rEditPawn)
    "bed": (None, [S("version", "i32"), V("mPointer"), S("mLimitOff", "i32"),
                   L("mpMarkerArray", [S("x", "f32"), S("y", "f32"), S("z", "f32")])]),
    "fed": (None, [S("version", "i32"), L("mpMarkerArray", [S("x", "f32"), S("y", "f32"), S("z", "f32")])]),
    "hed": (None, [S("version", "i32"), S("mHeightScale", "f32"), S("mShoulderScale", "f32"),
                   S("mWaistScale", "f32"), S("mLegScale", "f32"), S("mAnkleScale", "f32"), S("mArmScale", "f32"),
                   S("mArmUpperScale", "f32"), S("mArmLowerScale", "f32"), S("mWeaponNullScale", "f32"),
                   S("mLimitOff", "i32"), L("mpMarkerArray", [V("mScale"), V("mTrans")])]),
    "hpe": (None, [S("version", "i32"), S("mPartsType", "i32"), L("mpMarkerArray", [V("mScale"), V("mTrans")])]),
    "edp": (0x00706465, [S("version", "i32"), S("mModelType", "s16"), S("mNpcType", "s16"), S("mJobType", "s16"),
                         S("mJobPl", "s16"), S("mManWoman", "f32"), S("mStoop", "f32"), S("mprHumanEdit", "string"),
                         S("mprBodyEdit", "string"), S("mprFaceEdit", "string"), S("mMainWpnId", "i32"),
                         S("mSubWpnId", "i32"), S("mQuiverId", "i32"), S("mInnerId", "i32"), A("mWearId", "i32", 2),
                         A("mArmorId", "i32", 5), S("mHairId", "i32"), S("mBeardId", "i32"), C("mColorEyeR"),
                         C("mColorEyeL"), C("mColorHair"), C("mColorBody"), S("mWrinkleFace", "f32"),
                         S("mWrinkleBody", "f32"), A("mEyebrowOfs", "f32", 2), A("mEyebrowScl", "f32", 2),
                         S("mEyebrowTex", "i32"), S("mGoodsIdR", "i32"), S("mGoodsIdL", "i32"), S("mChainFlag", "i8"),
                         S("mHairSwingFlag", "i8"), S("mAttackMotType", "s16")]),
    # small tables
    "cit": (0x00746963, [S("version", "i32"),
                         L("mpArray", [S("mLevel", "s16"), A("mGetItemNo", "u16", 20), A("mGetItemRate", "u16", 20)])]),
    "skl": (0x006C6B73, [S("version", "i32"),
                         L("mpNormalSkill", [S("mSkillId", "i32"), S("mJobPoint", "i32"), S("mJobLevel", "i32")]),
                         L("mpCustomSkill", [S("mSkillId", "i32"), S("mSkillLv", "i32"), S("mJobPoint", "i32"),
                                             S("mJobLevel", "i32")]),
                         L("mpAbility", [S("mAbilityId", "i32"), S("mJobPoint", "i32"), S("mJobLevel", "i32")])]),
    "nnl": (7106158, [S("version", "i32"),
                         L("mpArray", [S("mNo", "s16"), S("mStageNo", "s16"), S("mInitmFriendPoint", "s16"),
                                       S("mModelType", "s16"), S("mIndex", "s16"), S("mStrayId", "s16"),
                                       S("mShopType", "s16"), S("mSeType", "s16"), S("mFlag", "i8"), S("mChild", "i8"),
                                       S("mTP", "i8"), S("mTPType", "i8"), S("mLikeItem", "i8"), S("mCivilian", "i8"),
                                       S("mName", "string"), S("mNameJ", "string")])]),
    "amr": (0x00726D61, [S("version", "i32"), S("mArrayInfoNum", "i32"), S("mArrayBlendNum", "i32"),
                         LN("mArrayInfo", [S("mModelId", "i32"), S("mType", "i32"), S("mFname", "string")],
                            "mArrayInfoNum"),
                         LN("mArrayBlend", [S("mModelId", "i32"), S("mRate", "f32")], "mArrayBlendNum")]),
    "aor": (7499617, [S("version", "i32"), S("mArrayNum", "i32"), S("allocate", "i32"),
                      LN("mpArray", [S("mModelId", "i32"),
                                     L("mpPartslList", [S("mChkModelId", "s16"), S("mPartsNo", "s16")])],
                         "mArrayNum")]),
    "atr": (7500897, [S("version", "i32"), S("mArrayNum", "i32"), S("allocate", "i32"),
                      LN("mpArray", [S("mArmorId", "i32"), S("mSoundPri", "i32"), S("mSoundType", "i32"),
                                     L("mpModelList", "i32")], "mArrayNum")]),
    "qlv": (7761009, [S("version", "i32"),
                      L("mpArray", [S("mItemNo", "u16"), S("mPad", "s16"), S("useGold0", "i32"),
                                    S("useGold1", "i32"), S("item1", "u16"), S("num1", "s16"),
                                    S("useGold2", "i32"), S("item2", "u16"), S("num2", "s16"),
                                    S("useGold3", "i32"), S("item3", "u16"), S("num3", "s16"),
                                    S("useGold4", "i32"), S("item4", "u16"), S("num4", "s16")])]),
    "gfd": (4474439, [S("mVersion", "i32"), S("mAttr", "i32"), S("mSuffix", "i32"), S("mFontType", "i32"),
                      S("mFontSize", "i32"), S("mTextureNum", "i32"), S("mCharNum", "i32"), S("mDescentNum", "i32"),
                      S("mMaxAscent", "f32"), S("mMaxDescent", "f32"), LN("mpDescent", "f32", "mDescentNum"),
                      S("strlen", "i32"), S("mpTexturePath", "string"),
                      LN("mpChar", [S("texture", "i8"), S("decent", "i8"), S("advance", "s16"), S("x", "s16"),
                                    S("y", "s16"), S("size_bits", "u32"), S("codepoint", "i32")], "mCharNum")]),
    # enemy/stage AI action params (rActionParam ".eap"/".sap"): which AI action fires under which
    # conditions.  Both carry the same ActionParam record; sap adds scene/hour gates instead of the
    # study tables.  Layouts from dd-tools' eap2xml.c / sap2xml.c, proved byte-exact on the corpus.
    "eap": (1599095109, [S("version", "s16"), S("paramNum", "s16"), S("studyDisableNum", "s16"),
                         A("unitTargetNum", "i8", 30),
                         LN("param", [S("ActionStatus", "i32"), S("BaseStatus", "i32"), S("StatusBadGood", "s64"),
                                      S("NotStatusBadGood", "s64"), S("EtcStatus", "i32"), S("PositionBit", "i32"),
                                      S("PositionID", "i32"), S("CmcAction", "i32"), S("CharacterInfoType", "i32"),
                                      S("NotCharacterInfoType", "i32"), S("CmcOrderTag", "i32"), *_ACTION_PARAM],
                            "paramNum"),
                         LN("mpStudyDisableAttrAdrs",
                            [S("ActionStatus", "i32"), S("BaseStatus", "i32"), S("StudyLvMin", "i32"),
                             S("StudyLvMax", "i32"), S("DisableElement", "i32"), S("DisableAtkAttr", "i32"),
                             S("DisableUse", "i32")], "studyDisableNum")]),
    "sap": (1599095123, [S("version", "s16"), S("paramNum", "s16"),
                         LN("param", [S("Id", "i32"), S("OnOffKind", "i32"), S("SceNoStart", "i32"),
                                      S("SceNoEnd", "i32"), S("StartHour", "i32"), S("EndHour", "i32"),
                                      A("OnFlag", _FLAG, 4), A("OffFlag", _FLAG, 4), *_ACTION_PARAM], "paramNum")]),
    # magic act (rMagicAct ".map"): per-spell motion/effect timing and per-level shot control.
    "map": (7364973, [S("version", "i32"),
                      L("mpArray", [S("efctType", "i32"), S("manualType", "i32"), S("seType", "i32"),
                                    S("motNoChantBgn", "i32"), S("blendChatBgn", "f32"), S("motNoChantLoop", "i32"),
                                    S("blendChantLoop", "f32"), S("motNoShot", "i32"), S("blendShot", "f32"),
                                    S("motNoEnd", "i32"), S("blendEnd", "f32"), S("is4Move", "i8"),
                                    S("mot4MoveWait", "i32"), S("mot4MoveWaitBgnFrame", "i32"), S("mot4MoveBlend", "f32"),
                                    S("motShlCtrlPlWait", "i32"), S("motShlCtrlPlWaitBgnFrame", "i32"),
                                    S("motShlCtrlPlMove", "i32"), S("motShlCtrlPlBlend", "i32"),
                                    S("motShlCtrlPlShot", "i32"), S("motShlCtrlPlShotBlend", "i32"),
                                    S("motNoShlHit", "i32"), S("motBlendShlHit", "f32"), S("motNoShlHitEnd", "i32"),
                                    S("motBlendShlHitEnd", "f32"), S("motNoShlHitEnd2", "i32"),
                                    S("motBlendShlHitEnd2", "f32"), S("motNoEx", "i32"), S("motBlendEx", "f32"),
                                    S("optionFlag", "i32"), S("chantLoopFrame", "f32"), S("shotLoopFrame", "f32"),
                                    S("chantBallJoint", "i32"), V("chantBallOffset"),
                                    L("lvParams", [S("chantFrame", "f32"), S("stamina", "f32"), S("staminaLoop", "f32"),
                                                   S("motSpeed", "f32"), S("lockOnNum", "i32"), S("lockOnBgnDist", "f32"),
                                                   S("lockOnEndDist", "f32"), S("multiSetType", "i32"),
                                                   S("multiLockShlWait", "f32"), S("multiMotLoopFrame", "i32"),
                                                   S("multiMotLoopBlend", "i32"), S("cameraNo", "i32"),
                                                   A("shl", _SHL_PARAM, 3)]),
                                    S("exeType", "i32"), S("shlCtrlType", "i32"), S("lockOnFlag", "i32"),
                                    S("efPosType", "i32"), S("shotLoopEndFlag", "i32"), S("cameraType", "i32"),
                                    S("cameraLookType", "i32"), S("cameraNoTps", "i32"),
                                    S("chantCmpCantShotFrame", "f32"), S("lvUpCantShotFrame", "f32"),
                                    S("noticeType", "i32"), S("noticeRadius", "f32")])]),
    # quest control table (rQuestControl ".qct"): judgment/result command rows per sheet.
    "qct": (7627633, [S("version", "i32"), S("unknown", "i32"),
                      L("mpArray", [L("mpParamTbl",
                                      [L("mpQuestTblJudgment", [S("mCommand", "i32"), S("mParam00", "i32"),
                                                                S("mParam01", "i32"), S("mParam02", "i32"),
                                                                S("mParam03", "i32")]),
                                       L("mpQuestTblResult", [S("mCommand", "i32"), S("mParam00", "i32"),
                                                              S("mParam01", "i32"), S("mParam02", "i32"),
                                                              S("mParam03", "i32"), S("mParam04", "i32"),
                                                              S("mParam05", "i32"), S("mParam06", "i32"),
                                                              S("mParam07", "i32"), S("mParam08", "i32"),
                                                              S("mParam09", "i32")])]),
                                    S("mSheetName", "string")]),
                      S("mUid", "i32")]),
    # rRegionStatus (".rst"): per-creature status regions (grab/climb candidates).  Magic is a
    # date-version 0x20110930, then a list of regions, each carrying a list of 56-byte sub-records.
    # Layout reversed from PS3 rRegionStatus::load (0x00B07918, named); field names are the
    # sub-record's engine struct offsets (disk order differs from struct order), meanings not decoded.
    "rst": (0x20110930, [L("mpRegion", [S("mUnkA", "u32"), S("mUnkB", "u32"),
                                        L("mpSub", [S("at04", "u32"), S("at08", "u32"),
                                                    S("at0c", "f32"), S("at10", "f32"), S("at14", "f32"),
                                                    S("at18", "f32"), S("at1c", "f32"), S("at20", "f32"),
                                                    S("at24", "f32"), S("at2c", "u32"), S("at30", "u32"),
                                                    S("at28", "u32"), S("at58", "f32"), S("at5c", "f32")])])]),
    # rStagePlaceName (".spn"): per-stage table of named sub-areas.  Magic "spn\0", a constant
    # (loader requires 0x30), then a list of 30-byte region records.  Reversed from PS3
    # rStagePlaceName::load (0x00B20DA4).  mPlaceNameId indexes id/DDN/message/common/map_placelist:
    # this is the definitive stage->room-name map for the 23 stages that carry one (st210 ->
    # Enlistment Corps Base, Command Headquarters, Training Grounds, Notice Board; docs/stage-map.md).
    "spn": (0x006E7073, [S("mConst", "u32"),
                         L("mpPlace", [S("mPosX", "f32"), S("mPosY", "f32"), S("mPosZ", "f32"),
                                       S("mRadius", "f32"), S("mPlaceNameId", "u16"),
                                       S("mUnk26", "s16"), S("mUnk28", "s16"),
                                       S("mUnk2C", "f32"), S("mUnk30", "f32")])]),
    # -- Dragon's Dogma Online vocation (job) tables, client 03.04.003 (docs/ddo-vocations.md) --------
    # Measured on every file (check_corpus --game ddo --only flat); none has a content magic but jtq.
    # Names: cJobMasterCtrl's are DDO.exe's own (createProperty); the rest describe what the data shows.
    # rJobCustomParam jobcustomNN.jcp: per custom skill, 17 resource references (JAMCRC of the path,
    # type id): motion list, alternate motion list, motion params, collision, 10 attack params, sound
    # request, effect provider, extra sound request (6,079 of 6,083 resolve; 4 name absent .epv files).
    "jcp": (None, [S("version", "u32"),
                   L("mpArray", [S("mSkillNo", "u32"),
                                 A("mResource", [S("mPathCrc", "u32"), S("mType", "u32")], 17)])]),
    # rAcquirement::rCustomSkillData costom_skill_data_NN.csd: skill number, message index into
    # custom_skill_name_NN, base skill (EX variants), and a level table (job level, job points).
    "csd": (None, [S("version", "u32"),
                   L("mpArray", [S("mSkillNo", "u16"), S("mMsgIndex", "u16"), S("mUnk04", "u16"),
                                 S("mBaseSkillNo", "u16"), S("mUnk08", "u8"),
                                 L("mpLevel", [S("mLevel", "u16"), S("mJobLevel", "u16"),
                                               S("mJobPoint", "u32")])])]),
    # rAcquirement::rNormalSkillData normal_skill_data_NN.nsd: core skills and what they cost.
    "nsd": (None, [S("version", "u32"),
                   L("mpArray", [S("mJobPoint", "u32"), S("mJobLevel", "u16"), S("mLearnOrder", "u16"),
                                 S("mUnk08", "u16"), S("mSkillNo", "u8"), S("mMsgIndex", "u8"), S("mUnk0C", "u8"),
                                 S("mBaseSkillNo", "u8"), S("mUnk0E", "u8")])]),
    # rJobMasterCtrl jobMasterCtrl.jmc: one row per vocation (field names from DDO.exe).
    "jmc": (None, [S("version", "u32"),
                   L("mpArray", [S("mJobId", "u32"), S("mStartJobLevel", "u32"), S("mFirstTalkGrpSerial", "u32"),
                                 S("mTraningTalkGrpSerial", "u32"), S("mFirstOrderTalkGrpSerial", "u32"),
                                 S("mJobTutorialQuestId", "u32"), S("mJobMasterTutorialQuestId", "u32"),
                                 S("mAreaId", "u32"), S("mAreaRank", "u32")])]),
    # rItemEquipJobInfoList itemEquipJobList.eir: which vocations may equip (bit n = vocation n).
    "eir": (None, [S("version", "u32"), L("mpJobBits", "u32")]),
    # rWepCateResTbl wepCateResTbl.wcrt: per weapon category, a name and the effect provider and sound
    # request it uses (JAMCRC of the path, type id).
    "wcrt": (None, [S("version", "u32"),
                    L("mpArray", [S("mCategory", "u32"), S("mComment", "string"), S("mEpvCrc", "u32"),
                                  S("mEpvType", "u32"), S("mSrqCrc", "u32"), S("mSrqType", "u32")])]),
    # rDmJobAdjParam dmJobAdj.dja: per vocation (job type 1-11), 15 damage multipliers (meanings UNKNOWN).
    "dja": (None, [S("version", "u32"), L("mpArray", [S("mJobType", "u32"), A("mRate", "f32", 15)])]),
    # rJobLevelUpTbl2 jobNN.jlt2: one row per job level, five whole numbers (meanings UNKNOWN).
    "jlt2": (None, [S("version", "u32"), L("mpArray", [A("mValue", "u32", 5)])]),
    # rJobTutorialQuestList jobTutorialQuestList.jtq: "JTQ\0", u16 version, the tutorial quest ids.
    "jtq": (0x0051544A, [S("version", "u16"), L("mQuestId", "u32")]),
    # rAdjustParam in Dragon's Dogma Online: no "ajp\0" magic, u32 version 0x100, then the floats
    # (all 65 files; enemy params and the player's baseStatus).
    "ajp-ddo": (None, [S("version", "u32"), L("mpArray", "f32")]),
    # -- Dragon's Dogma Online combat / vocation parameters, client 03.04.003 -------------------------
    # Every field's order and width is DDO.exe's own: read from each class's loader (resource vtable
    # slot 10 reads the version and refuses any other, slot 22 the header and count, slot 15 one record;
    # reader slots +04 u16, +08 u32, +0C u64, +14 s16, +18 s32, +24 f32, +34 vector4, inline bytes u8).
    # None of these classes has MtDTI properties, so unknown fields are named mUnkXX after the member
    # offset the loader stores them to (the offset code that uses the field reads) -- not their place
    # in the file, which differs.  Fields read as a byte and kept as bool (nonzero = true) are u8 here.
    # rAttackParam .atk (1,469 files): the per-hit attack data of every skill and enemy attack;
    # jobcustomNN.jcp names ten per custom skill, one per skill level (csNN_01..csNN_10).  Loader
    # 0x00A50940, 275 bytes a record, cAttackParam 0x120.  version 0x72; mPathCrc is JAMCRC of the
    # file's own path (1,469 of 1,469); mIndex equals the record's position (22,553 of 22,553).
    # Measured, not proven by code: 008 appears to be the physical and 014 the magick attack rate
    # (Fighter/Seeker/Hunter/Warrior/Alchemist/Spirit Lancer records fill 008, Priest/Sorcerer/Element
    # Archer 014, High Scepter both); they and 010/01C/024/028/02C rise with skill level (median level 10
    # / level 1 = 1.5).  00D appears to be the element (1 none, 2 fire, 3 ice, 4 thunder, 5 holy, 6 dark:
    # it matches every Sorcerer and Element Archer skill's name) and 00C the kind of hit (0 on blades,
    # 1 on Alchemist staves and shield bashes, 2 on arrows -- Hunter's and Element Archer's -- 3 on most
    # spells).  The five (u32, u16) pairs 0C0/0DA,
    # 0C4/0D8, 0C8/0DC, 0CC/0DE, 0D0/0E0 appear to be debilitations (id 0x1000+n, amount): Enfeebling Bow
    # carries 0x1016 and 0x1018, Crippling Bow 0x1017 and 0x1019.  The four (u8, 4 x f32) groups in file
    # order 056/07C, 08C/090, 08D/0A0, 08E/0B0 appear to be knock-back (distance, gravity, height,
    # gravity); the constructor defaults them to (5, -0.1, 0, -2), (5, 0, 5, -2), (20, -1, 0, -2),
    # (20, -1, 20, -2).  Bools: 119, 11A.
    "atk": (None, [S("version", "u32"), S("mPathCrc", "u32"),
                   L("mpArray", [S("mIndex", "u16"), S("mUnk006", "u16"), S("mUnk008", "f32"), S("mUnk00C", "u8"),
                                 S("mUnk010", "f32"), S("mUnk119", "u8"), S("mUnk00E", "u16"), S("mUnk014", "f32"),
                                 S("mUnk00D", "u8"), S("mUnk018", "f32"), S("mUnk01C", "f32"), S("mUnk020", "u8"),
                                 S("mUnk024", "f32"), S("mUnk021", "u8"), S("mUnk028", "f32"), S("mUnk02C", "f32"),
                                 S("mUnk022", "u8"), S("mUnk030", "f32"), S("mUnk034", "f32"), S("mUnk038", "f32"),
                                 S("mUnk03C", "f32"), S("mUnk040", "f32"), S("mUnk044", "f32"), S("mUnk048", "f32"),
                                 S("mUnk04C", "f32"), S("mUnk050", "f32"), S("mUnk023", "u8"), S("mUnk054", "u8"),
                                 S("mUnk058", "f32"), S("mUnk05C", "f32"), S("mUnk060", "f32"), S("mUnk064", "f32"),
                                 S("mUnk055", "u8"), S("mUnk06A", "u16"), S("mUnk068", "u16"), S("mUnk06C", "f32"),
                                 S("mUnk070", "f32"), S("mUnk057", "u8"), S("mUnk074", "f32"), S("mUnk078", "u32"),
                                 S("mUnk056", "u8"), S("mUnk07C", "f32"), S("mUnk080", "f32"), S("mUnk084", "f32"),
                                 S("mUnk088", "f32"), S("mUnk08C", "u8"), S("mUnk090", "f32"), S("mUnk094", "f32"),
                                 S("mUnk098", "f32"), S("mUnk09C", "f32"), S("mUnk08D", "u8"), S("mUnk0A0", "f32"),
                                 S("mUnk0A4", "f32"), S("mUnk0A8", "f32"), S("mUnk0AC", "f32"), S("mUnk08E", "u8"),
                                 S("mUnk0B0", "f32"), S("mUnk0B4", "f32"), S("mUnk0B8", "f32"), S("mUnk0BC", "f32"),
                                 S("mUnk0C0", "u32"), S("mUnk0DA", "u16"), S("mUnk0C4", "u32"), S("mUnk0D8", "u16"),
                                 S("mUnk0C8", "u32"), S("mUnk0DC", "u16"), S("mUnk0CC", "u32"), S("mUnk0DE", "u16"),
                                 S("mUnk0D0", "u32"), S("mUnk0E0", "u16"), S("mUnk0D4", "u32"), S("mUnk110", "u64"),
                                 S("mUnk0E8", "u32"), S("mUnk0E4", "u8"), S("mUnk0EC", "f32"), S("mUnk0E2", "u16"),
                                 S("mUnk0E5", "u8"), S("mUnk11A", "u8"), S("mUnk0E6", "u16"), S("mUnk0F0", "u16"),
                                 S("mUnk0F4", "u8"), S("mUnk0F5", "u8"), S("mUnk0F2", "u16"), S("mUnk0F6", "u8"),
                                 S("mUnk0F7", "u8"), S("mUnk0F8", "u8"), S("mUnk0FA", "u16"), S("mUnk100", "u64"),
                                 S("mUnk0F9", "u8"), S("mUnk08F", "u8"), S("mUnk108", "u16"), S("mUnk10C", "u32"),
                                 S("mUnk118", "u8"), S("mUnk10A", "u16")])]),
    # rActionParamList .acp (85 files; jobNN_com/_cs, hm_common, the emActionParam lists): one row per
    # action.  Loader 0x00A46440, 50 bytes a record, cActionParamList 0x44.  version 4.  mActClassId is
    # the action class's MtDTI id -- the loader resolves it with MtDTI::find (0x013B5A00); all 3,128 name
    # a DDO class (cHumanAct*, cEmAct*, cHmMagicChant* ...).  The constructor places a cActParamRes
    # (0x2C bytes) at +10 and a cActNetParamRes (8 bytes) at +3C; their fields are named by their own
    # offsets.  Bool: none.
    "acp": (None, [S("version", "u32"),
                   L("mpArray", [S("mActClassId", "u32"), S("mUnk08", "u16"),
                                 S("mActParamRes", [S("mUnk04", "u32"), S("mUnk08", "u32"), S("mUnk0C", "u32"),
                                                    S("mUnk10", "u32"), S("mUnk14", "f32"), S("mUnk18", "f32"),
                                                    S("mUnk1C", "f32"), S("mUnk20", "f32"), S("mUnk24", "u16"),
                                                    S("mUnk26", "u16"), S("mUnk28", "s16"), S("mUnk2A", "u8"),
                                                    S("mUnk2B", "u8")]),
                                 S("mActNetParamRes", [S("mUnk04", "u8"), S("mUnk05", "u8"), S("mUnk06", "u8"),
                                                       S("mUnk07", "u8")])])]),
    # rKeyCommand .kcm (55 files; JobNN{Base,Guard}ActCommand, BaseLCommand, CustomSkillCommand): the
    # input commands of a vocation.  Loader 0x00A9D0F0, 61 bytes a record, cKeyCommand 0x48.  version 1.
    # Measured: 18 and 1C (0xFFFFFFFF = none) appear to be action ids, list << 12 | row -- in the 37
    # JobNN files all 1,356 fall inside their list: 0 hm_common.acp, 1 jobNN_cs.acp, 2 jobNN_com.acp; in
    # CustomSkillCommand 40 holds the custom skill numbers (1-14 and the EX 1xx/2xx ones).  Bool: 44.
    "kcm": (None, [S("version", "u32"),
                   L("mpArray", [S("mUnk08", "u64"), S("mUnk10", "u64"), S("mUnk18", "u32"), S("mUnk1C", "u32"),
                                 S("mUnk20", "u32"), S("mUnk24", "u32"), S("mUnk28", "u32"), S("mUnk2C", "u32"),
                                 S("mUnk30", "u32"), S("mUnk34", "u32"), S("mUnk38", "u32"), S("mUnk3C", "u32"),
                                 S("mUnk40", "i32"), S("mUnk44", "u8")])]),
    # rMotionParam .motparam (54 files; beside motion lists, e.g. m0001_csNN.motparam): rows appear to
    # be the motion slots of the list beside it (52 files hold 256).  Loader 0x00AB1110, 13 bytes a
    # record, cMotionParam 0x14.  version 0x15.  Most rows are (-1.0, 0, 4, 4).  Bool: 08.
    "motparam": (None, [S("version", "u32"),
                        L("mpArray", [S("mUnk04", "f32"), S("mUnk08", "u8"), S("mUnk0C", "u32"), S("mUnk10", "u32")])]),
    # rKeyCustomParam .kcp (10 files; keycustom\key_jobNN_preset00, key_common_preset00,
    # key_custom_preset00): key configuration presets.  Loader 0x00A9DE10, 26 bytes a record,
    # cKeyCustomParam 0x20.  version 0x15.  Measured: 0E appears to be a Windows virtual-key code (the
    # common preset binds W, S, A, D, E, Space, arrows; Shift 0x10, Ctrl 0x11), 10 mouse buttons
    # (1, 2, 4 ...) and 18 an XInput button mask.
    "kcp": (None, [S("version", "u32"),
                   L("mpArray", [S("mUnk04", "u16"), S("mUnk06", "u16"), S("mUnk08", "u32"), S("mUnk0C", "u16"),
                                 S("mUnk0E", "u16"), S("mUnk10", "u16"), S("mUnk12", "u16"), S("mUnk18", "u32"),
                                 S("mUnk14", "u16"), S("mUnk1C", "u32")])]),
    # rMagicChantParam .chant (7 files: obj\pl\pl000000\param\magicchantparam\job04/05/06, the human
    # enemies' obj\hmem\magic_chant\job04/05/06 and em011041): one row per chanted spell.  Loader
    # 0x00AAC050, 370 bytes a record, cMagicChantParam 0x1A0.  version 0x11.  mUnk060/0A0/0E0 are 4x4
    # matrices (four vector4 rows each; the last element is 1.0 in every record); mUnk020 is ten floats
    # read in a loop (where set they mostly fall across the ten, e.g. 150, 140 ... 60: they appear to be
    # per skill level).  008, 018 and 020 are stored through setters.  Bools: 010, 01C, 04C, 128, 130,
    # 15C, 164, 170, 18C, 198.
    "chant": (None, [S("version", "u32"),
                     L("mpArray", [S("mUnk004", "u32"), S("mUnk008", "f32"), S("mUnk00C", "u32"), S("mUnk010", "u8"),
                                   S("mUnk014", "u32"), S("mUnk018", "f32"), S("mUnk048", "u32"), S("mUnk04C", "u8"),
                                   S("mUnk050", "i32"), S("mUnk120", "u32"), A("mUnk060", "f32", 16),
                                   A("mUnk0A0", "f32", 16), A("mUnk0E0", "f32", 16), S("mUnk124", "u32"),
                                   S("mUnk128", "u8"), S("mUnk12C", "f32"), S("mUnk130", "u8"), S("mUnk134", "f32"),
                                   S("mUnk138", "u32"), S("mUnk13C", "u32"), S("mUnk140", "u32"), S("mUnk144", "u32"),
                                   S("mUnk148", "f32"), S("mUnk14C", "u32"), S("mUnk150", "u32"), S("mUnk154", "u32"),
                                   S("mUnk158", "u32"), S("mUnk15C", "u8"), S("mUnk160", "u32"), S("mUnk164", "u8"),
                                   S("mUnk168", "i32"), S("mUnk16C", "i32"), S("mUnk170", "u8"), S("mUnk174", "f32"),
                                   S("mUnk178", "f32"), S("mUnk17C", "i32"), S("mUnk180", "i32"), S("mUnk184", "f32"),
                                   S("mUnk188", "f32"), S("mUnk01C", "u8"), A("mUnk020", "f32", 10),
                                   S("mUnk18C", "u8"), S("mUnk190", "f32"), S("mUnk194", "f32"), S("mUnk198", "u8"),
                                   S("mUnk19C", "f32")])]),
    # -- Dragon's Dogma Online markers and situation messages (2026-09-25) ------------------------------
    # Loaders read the magic, refuse any other version, then a counted list; the record order is each
    # record's writer (vf06), the names DDO.exe's own properties.
    # rQuestMarkerInfo "QMI\0" (1,744 files; loader 0x00AEACC0, record writer 0x00AEAE30): quest markers
    # of a stage -- mPos, then mGroupNo (201.. = the stage's object groups), mUniqueId.
    "qmi": (0x00494D51, [S("version", "u32"), S("mStageNo", "u32"),
                         L("mInfoList", [A("mPos", "f32", 3), S("mGroupNo", "u32"), S("mUniqueId", "u32")])]),
    # rFieldAreaMarkerInfo "FMI\0" (355; loader 0x00A84750, writer 0x00A848A0). The header u32 has no
    # property name; the 26 files named FieldAreaNNN all hold NNN, so Riftstone calls it mFieldAreaId.
    "fmi": (0x00494D46, [S("version", "u32"), S("mFieldAreaId", "u32"),
                         L("mInfoList", [A("mPos", "f32", 3), S("mStageNo", "i32"), S("mGroupNo", "u32"),
                                         S("mUniqueId", "u32")])]),
    # rSituationMsgCtrl "SMC\0" (908; loader 0x00AC91C0, list writer 0x00AC8B70): which message group
    # shows for which quest window (cSituationData, 22 bytes a record).
    "smc": (0x00434D53, [S("version", "u32"),
                         L("mArray", [S("mGroupSerial", "u32"), S("mStartQuestId", "u32"),
                                      S("mIsStartQuestIdStart", "u8"), S("mEndQuestId", "u32"),
                                      S("mIsEndQuestIdStart", "u8"), S("mRangeStartQuestFlag", "u32"),
                                      S("mRangeEndQuestFlag", "u32")])]),
}

# Another game's revision of a format, tried in order when a resource does not start with the format's
# own magic. The parsed Flat then carries the revision's key, so its YAML tag and rebuild follow it.
REVISIONS: dict[str, tuple[str, ...]] = {"ajp": ("ajp-ddo",)}


# -- engine ----------------------------------------------------------------------------------
def _read(buf, off, elem):
    if isinstance(elem, str):
        if elem == "string":               # bytes up to and including a NUL
            end = buf.find(b"\0", off)
            if end < 0:
                raise FormatError("flat", "a string is not terminated", off)
            return buf[off:end].decode("utf-8", "surrogateescape"), end + 1
        if elem == "f32":                  # kept as exact 32-bit bits so NaN payloads survive
            return _ST["u32"].unpack_from(buf, off)[0], off + 4
        st = _ST[elem]
        return st.unpack_from(buf, off)[0], off + st.size
    return _read_block(buf, off, elem)


def _read_block(buf, off, fields):
    out = {}
    for f in fields:
        kind, name = f[0], f[1]
        if kind == "s":
            out[name], off = _read(buf, off, f[2])
        elif kind == "a":
            elem, n = f[2], f[3]
            vals = []
            for _ in range(n):
                v, off = _read(buf, off, elem)
                vals.append(v)
            out[name] = vals
        elif kind == "ln":
            elem, cf = f[2], f[3]
            if out[cf] < 0:                # a signed count field: the rebuild could not write it back
                raise FormatError("flat", f"{name}: its count {cf} is negative ({out[cf]})", off)
            vals = []
            for _ in range(out[cf]):
                v, off = _read(buf, off, elem)
                vals.append(v)
            out[name] = vals
        else:  # "l" -- inline u32 length prefix
            if off + 4 > len(buf):
                raise FormatError("flat", f"{name}: file ends before its count", off)
            n = _ST["u32"].unpack_from(buf, off)[0]
            off += 4
            elem = f[2]
            size = _ST[elem].size if isinstance(elem, str) and elem in _ST else None
            if size is not None and off + size * n > len(buf):
                raise FormatError("flat", f"{name}: {n} items do not fit in the file", off)
            vals = []
            for _ in range(n):
                v, off = _read(buf, off, elem)
                vals.append(v)
            out[name] = vals
    return out, off


def _write(out: bytearray, elem, value):
    if isinstance(elem, str):
        if elem == "string":
            out += value.encode("utf-8", "surrogateescape") + b"\0"
        elif elem == "f32":                # value is the 32-bit float's exact bits (int), or a plain float
            out += _ST["f32"].pack(value) if isinstance(value, float) else _ST["u32"].pack(value & 0xFFFFFFFF)
        else:
            if elem == "f64" and isinstance(value, int):
                value = float(value)
            out += _ST[elem].pack(value)
    else:
        _write_block(out, elem, value)


def _write_block(out: bytearray, fields, data: dict):
    for f in fields:                       # a count-by-reference field follows its list's length
        if f[0] == "ln":
            data[f[3]] = len(data[f[1]])
    for f in fields:
        kind, name = f[0], f[1]
        v = data[name]
        if kind == "s":
            _write(out, f[2], v)
        elif kind == "a":
            elem, n = f[2], f[3]
            if len(v) != n:
                raise ParamError(f"{name} holds {n} values, not {len(v)}")
            for item in v:
                _write(out, elem, item)
        elif kind == "ln":
            for item in v:
                _write(out, f[2], item)
        else:
            out += _ST["u32"].pack(len(v))
            for item in v:
                _write(out, f[2], item)


def parse(data: bytes, ext: str | None = None) -> Flat:
    """Parse a flat resource.  ``ext`` is required for the magic-less formats (bed/fed/hed/hpe);
    otherwise it is detected from the magic."""
    if ext is None:
        magic = _ST["u32"].unpack_from(data, 0)[0] if len(data) >= 4 else -1
        ext = _by_magic(magic)
        if ext is None:
            raise FormatError("flat", f"unknown magic {magic:#010x}", 0)
    if ext not in SCHEMAS:
        raise FormatError("flat", f"unknown flat format {ext!r}", 0)
    magic, schema = SCHEMAS[ext]
    start = 0
    if magic is not None:
        if len(data) < 4 or _ST["u32"].unpack_from(data, 0)[0] != magic:
            for alt in REVISIONS.get(ext, ()):
                try:
                    return parse(data, alt)
                except FormatError:
                    pass
            raise FormatError("flat", f"{ext}: wrong magic", 0)
        start = 4
    try:
        obj, end = _read_block(data, start, schema)
    except (struct.error, IndexError, UnicodeDecodeError) as e:   # malformed bytes: a clean refusal, not a crash
        raise FormatError("flat", f"{ext}: {e}", start) from None
    if end != len(data):
        raise FormatError("flat", f"{ext}: {len(data) - end} trailing byte(s) the schema did not read", end)
    return Flat(ext, magic if magic is not None else 0, obj)


def build(f: Flat) -> bytes:
    magic, schema = SCHEMAS[f.ext]
    out = bytearray(_ST["u32"].pack(magic)) if magic is not None else bytearray()
    _write_block(out, schema, f.data)
    return bytes(out)


def _by_magic(magic: int) -> str | None:
    for ext, (m, _) in SCHEMAS.items():
        if m == magic:
            return ext
    return None


def magic_ext(data: bytes) -> str | None:
    """The flat format whose magic ``data`` starts with, if any (magic-less formats never match)."""
    if len(data) >= 4:
        return _by_magic(_ST["u32"].unpack_from(data, 0)[0])
    return None


# -- YAML ------------------------------------------------------------------------------------
def _yaml_value(elem, value):
    from .yamlish import Map, Scalar

    if isinstance(elem, str):
        if elem == "f32":
            from .params import f32_bits_text
            bits = struct.unpack("<I", _ST["f32"].pack(value))[0] if isinstance(value, float) else value
            return Scalar(f32_bits_text(bits))
        if elem == "f64":
            from .params import fmt_f64
            return Scalar(fmt_f64(value))
        if elem == "string":
            return Scalar(value, "double")
        return Scalar(str(value))
    return Map([(Scalar(k), _yaml_field(elem, k, value[k])) for k in (fld[1] for fld in elem)])


def _yaml_field(fields, name, value):
    from .yamlish import Scalar, Seq

    f = next(x for x in fields if x[1] == name)
    kind = f[0]
    if kind == "s":
        return _yaml_value(f[2], value)
    elem = f[2]
    scalar_elem = isinstance(elem, str)
    items = [_yaml_value(elem, v) for v in value]
    return Seq(items, flow=scalar_elem)


def to_yaml(f: Flat, name: str | None = None) -> str:
    from . import yamlish
    from .yamlish import Map, Scalar

    fields = SCHEMAS[f.ext][1]
    head = [f"Riftstone {f.ext} parameter file" + (f" -- {name}" if name else ""),
            "A flat MT Framework table. Edit the numbers; keep the structure. Arrays grow and shrink;",
            "fixed-size groups must keep their length. Rebuilds byte-for-byte when untouched."]
    items = [(Scalar("riftstone"), Scalar(f.ext + "/1"))]
    if name:
        items.append((Scalar("resource"), Scalar(name, "double")))
    for fld in fields:
        items.append((Scalar(fld[1]), _yaml_field(fields, fld[1], f.data[fld[1]])))
    return yamlish.emit(Map(items), head)


def _from_value(elem, node, source):
    from .yamlish import Map, Scalar

    if isinstance(elem, str):
        if not isinstance(node, Scalar):
            raise ParamError("expected a value", getattr(node, "line", None), None, source)
        if elem == "string":
            if "\0" in node.text:
                raise ParamError("a string cannot contain a NUL", node.line, node.col, source)
            return node.text
        if elem == "f32":
            from .params import f32_bits
            try:
                return f32_bits(node.text)
            except ValueError:
                raise ParamError(f"{node.text!r} is not a number that fits a 32-bit float",
                                 node.line, node.col, source) from None
        t = node.text.strip()
        if elem == "f64":
            return _float(t, node, source)
        try:
            v = int(t, 0)
        except ValueError:
            raise ParamError(f"expected a whole number, not {t!r}", node.line, node.col, source) from None
        lo, hi = _RANGE[elem]
        if not lo <= v <= hi:
            raise ParamError(f"{v} is out of range for {elem} ({lo}..{hi})", node.line, node.col, source)
        return v
    if not isinstance(node, Map):
        raise ParamError("expected a block of fields", getattr(node, "line", None), None, source)
    return _from_block(elem, node, source)


def _float(t: str, node, source):
    from .params import to_f32
    try:
        return to_f32(float(t))
    except ValueError:
        raise ParamError(f"{t} is not a number that fits a 32-bit float", node.line, node.col, source) from None


def _from_block(fields, node, source):
    from .yamlish import Map, Scalar, Seq

    out = {}
    known = {f[1] for f in fields}
    for k, _ in node.items:
        if k.text not in known and k.text not in ("riftstone", "resource"):
            raise ParamError(f"unexpected field {k.text!r}", k.line, k.col, source)
    for f in fields:
        kind, name = f[0], f[1]
        v = node.get(name)
        if v is None:
            raise ParamError(f"{name} is missing", getattr(node, "line", None), None, source)
        if kind == "s":
            out[name] = _from_value(f[2], v, source)
        else:
            if not isinstance(v, Seq):
                raise ParamError(f"{name} is a list", v.line, v.col, source)
            if kind == "a" and len(v.items) != f[3]:
                raise ParamError(f"{name} holds {f[3]} values, not {len(v.items)}", v.line, v.col, source)
            out[name] = [_from_value(f[2], it, source) for it in v.items]
    return out


_RANGE = {"i8": (-128, 127), "u8": (0, 255), "s16": (-32768, 32767), "u16": (0, 65535),
          "i32": (-(2**31), 2**31 - 1), "u32": (0, 2**32 - 1), "s64": (-(2**63), 2**63 - 1),
          "u64": (0, 2**64 - 1)}


def from_yaml(text: str, source: str | None = None) -> Flat:
    from . import yamlish
    from .yamlish import Map, Scalar

    doc = yamlish.parse(text, source)
    tag = doc.get("riftstone") if isinstance(doc, Map) else None
    if not isinstance(tag, Scalar) or "/" not in tag.text:
        raise ParamError("not a Riftstone flat parameter file (missing 'riftstone:' tag)", 1, 1, source)
    ext = tag.text.split("/")[0]
    if ext not in SCHEMAS:
        raise ParamError(f"unknown flat format {ext!r}", 1, 1, source)
    return Flat(ext, SCHEMAS[ext][0], _from_block(SCHEMAS[ext][1], doc, source))


def yaml_to_bytes(text: str, source: str | None = None) -> bytes:
    return build(from_yaml(text, source))

// engine.h -- the Dark Arisen engine calls the compatibility layer makes (DDDA.exe build 2364871).
//
// Every address below was read from the exe (disassembly, the PS3 build's names) and is
// byte-checked at start-up before anything is patched or called (compat.cpp Verify).  "custom" functions
// use MSVC's whole-program register conventions; each goes through a naked thunk that sets the registers
// and calls through a pointer held in memory, so no register the callee reads is clobbered.
#pragma once
#include <stdint.h>

namespace eng {

// ---- globals ------------------------------------------------------------------------------------
constexpr uintptr_t PLAYER_MANAGER = 0x018FA4EC;   // sPlayerManager*; +0x99C the Arisen (uPlayer*)
constexpr uint32_t ARISEN = 0x99C;
constexpr uintptr_t RESOURCE_MANAGER = 0x018D0AA0; // sResource*; vtable +0x30 create(type, path, flags)
constexpr uintptr_t SHELL_MANAGER = 0x018FA508;    // sShlManager*
constexpr uintptr_t SKILL_TABLE = 0x014F3658;      // [430] x 0x20; +4 action number (byte +7: 1 main, 2 sub)
constexpr uint32_t SKILL_COUNT = 430;
constexpr uintptr_t ALLOCATORS = 0x01876628;       // MtAllocator* [64], picked by MtDTI bits 23..28

// ---- class descriptors and vtables ------------------------------------------------------------------
constexpr uintptr_t PLACTION_DTI = 0x019A1F28;     // cPlAction::DTI (built before WinMain)
constexpr uintptr_t PLACTION_DTI_VT = 0x015E043C;  // its vtable (8 slots; [1] newInstance 0x00AC3320)
constexpr uintptr_t PLACTION_NEW = 0x00AC3320;
constexpr uintptr_t PLACTION_VT = 0x01568A6C;      // cPlAction's vtable (14 slots)
constexpr uint32_t PLACTION_SLOTS = 14;
constexpr uintptr_t PLAYER_VT = 0x015E90D0;        // uPlayer's vtable
constexpr uint32_t PLACTION_SIZE = 0x74;
constexpr uint32_t DTI_VT_SLOTS = 8;

// ---- resource types (MtDTI*) ------------------------------------------------------------------------
constexpr uintptr_t DTI_MOTION_LIST = 0x018D19DC;
constexpr uintptr_t DTI_EFFECT_PROVIDER = 0x019A8364;
constexpr uintptr_t DTI_OBJ_COLLISION = 0x019A8BB4;
constexpr uintptr_t DTI_SHL_PARAM_LIST = 0x019A917C;

// ---- uPlayer offsets (PC) -------------------------------------------------------------------------
constexpr uint32_t P_FRAME_STEP = 0x14;            // f32 frame step of this update (timers)
constexpr uint32_t P_POS = 0x40;                   // uCoord::mPos
constexpr uint32_t P_WMAT = 0xB0;                  // uCoord::mWmat (rows x, y, z, w; 16 bytes each)
constexpr uint32_t P_MODEL = 0xF0C;                // uModel* whose motion layers the player plays (itself)
constexpr uint32_t P_STAT = 0x273C, P_OLD_STAT = 0x2740;  // mActStat / mOldActStat
constexpr uint32_t P_ACT_MGR = 0x2DB8;             // cActionManager mActMgr
constexpr uint32_t P_ACTION = 0x2DC8;              // mpAction
constexpr uint32_t P_REQ_DTI = 0x2DCC;             // mpReqDtiAction (a pending action made from a DTI)
constexpr uint32_t P_IS_UPDATE = 0x2DD0;           // bool mIsUpdateAction, +1 bool mIsActExFlag
constexpr uint32_t P_ACTION_NO = 0x2DD4;           // s32 mActionNo (-2: made from a DTI)
constexpr uint32_t P_ACTION_REQ = 0x2DD8;          // s32 mActionNoReq (-1 none, -2 DTI)
constexpr uint32_t P_PLAYER_INFO = 0x3DEC;         // cPlayerInfo*; +0x270 mMainSkill[3], +0x27C mSubSkill[3]
constexpr uint32_t P_MAIN_WEAPON = 0x3554;         // s32 main weapon id (category = id >> 24)
constexpr uint32_t INFO_MAIN_SKILL = 0x270, INFO_SUB_SKILL = 0x27C, INFO_LEARNED = 0x72C;

// motion layer L of the player's model: [P+0xF0C] + 0x380 + L*0x140
constexpr uint32_t MODEL_LAYERS = 0x380, LAYER_SIZE = 0x140;
constexpr uint32_t LAYER_NO = 0x04;                // u16 mMotionNo (bank << 8 | index)
constexpr uint32_t LAYER_FRAME = 0x4C;             // f32 mFrame

// cActionBase / cPlAction members
constexpr uint32_t ACT_U32 = 0x04, ACT_S32 = 0x14, ACT_F32 = 0x24, ACT_OWNER = 0x44, ACT_END = 0x50;
constexpr uint32_t ACT_PLAYER = 0x58;              // cPlAction::mpOwnerPl

// uShlBase members
constexpr uint32_t SHL_COLLISION = 0x1D1C;         // cObjCollision mpResource[0] (rObjCollision*)

// ---- functions ------------------------------------------------------------------------------------
constexpr uintptr_t F_CALC_SEQUENCE = 0x01057060;  // thiscall(layer, page, f0, f1) -> bits, ret 0xC
constexpr uintptr_t F_IS_END = 0x01057100;         // thiscall(layer) -> bool
constexpr uintptr_t F_SET_MOTION_LIST = 0x01057670;// thiscall(model, rMotionList*, bank), ret 8
constexpr uintptr_t F_RES_CREATE = 0x00DBC170;     // sResource vtable +0x30
constexpr uintptr_t F_RES_ADDREF = 0x00DE0B80;     // thiscall(cResource*)
constexpr uintptr_t F_RES_RELEASE = 0x00DE0B90;    // thiscall(cResource*)
constexpr uintptr_t F_CREATE_SHL = 0x004AB970;     // custom: EAX group; (mgr, list, index, owner, parent, line), ret 0x18
constexpr uintptr_t F_SET_SHOT_COORD = 0x00BD6D20; // fastcall-shaped: ECX &dir, EDX &pos; (shell), ret 4
constexpr uintptr_t F_SET_EPV_RESOURCE = 0x00B572E0;// custom: EAX path; (model, slot), ret 8
constexpr uintptr_t F_SKILL_LEVEL = 0x00780BA0;    // custom: ESI skill id, EDI cPlayerInfo+0x72C; (bool), ret 4
constexpr uintptr_t F_WEAPON_INTERP = 0x00B86010;  // thiscall(player, bool), ret 4
constexpr uintptr_t F_PLACTION_CTOR = 0x004CAC70;  // custom: EAX memory -> EAX
constexpr uintptr_t F_PLACTION_INIT = 0x00AC4F70;  // thiscall
constexpr uintptr_t F_PLACTION_MOVE = 0x00AC5100;  // thiscall
constexpr uintptr_t F_PLACTION_FINAL = 0x00AC8A10; // thiscall
constexpr uintptr_t F_PLACTION_SETUP = 0x00AC4F40; // thiscall(owner, param, listParam), ret 0xC
constexpr uintptr_t F_PLACTION_DTOR = 0x004CACF0;  // thiscall(flags), ret 4
constexpr uintptr_t F_PLACTION_GETDTI = 0x004CAC60;
constexpr uintptr_t F_GET_ALLOCATOR = 0x00CF5E20;  // cdecl(const MtDTI*) -> allocator; vtable +0x1C alloc(size, align, tag)
constexpr uintptr_t F_SET_ACTION_EX_DTI = 0x004C73D0;
constexpr uintptr_t F_CHECK_ACTION = 0x00B564E0;   // uPlayer::checkAction
constexpr uintptr_t F_PLAY_BASE_MOTION = 0x00B800C0;
constexpr uint32_t VT_PLAY_BASE_MOTION = 0x90;     // uPlayer: thiscall(motNo, forcible, attr, hokan, frame, speed)
constexpr uint32_t VT_SET_ACTION_EX_DTI = 0x21C;   // uPlayer: thiscall(dti, u32 x4, s32 x4, f32 x8)
constexpr uint32_t VT_CHECK_ACTION = 0x220;        // uPlayer: thiscall()

}  // namespace eng

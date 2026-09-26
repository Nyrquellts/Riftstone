// six_skill_warrior -- Riftstone native plugin: six skills for the Warrior, three on the main-weapon
// skill button and three on the secondary-weapon skill button, the way a Mage's staff already has six.
//
// What the game does (DDDA.exe build 2364871; PS3 build names, docs/six-skill-warrior.md)
//   A player keeps six custom-skill slots for every weapon category: cPlayerInfo::mWeaponSkill
//   (+0x120, s32[13][6]); the save keeps the same rows (cSAVE_DATA_PARAM mWeaponSkill s16[6] per
//   category).  setSkillFromEquipWeapon (0x00780350) copies the main weapon's row into the two
//   palettes, slots 0-2 into mMainSkill (+0x270) and 3-5 into mSubSkill (+0x27C), then a secondary
//   weapon's slots 0-2 over mSubSkill.  initJob (0x00B5EF90) turns both palettes into the action
//   numbers the combat code matches (uPlayerBase +0x35F4 and +0x3600), and the button layer
//   (0x00B52A00) sets a "secondary slot 1-3" command bit whenever the secondary-weapon skill button
//   (STG_SUB_WEP) is held with a skill button, whatever the vocation.
//   Only the staff (weapon categories 5 and 6; skill-menu categories 3 and 4) uses slots 3-5 of its
//   own row.  Everywhere the game tells a staff from other weapons it compares those numbers:
//     removeIllegalCstmSkill (0x00780590) keeps a staff's secondary palette and empties any other
//       weapon's when there is no secondary weapon;
//     the skill-archive loader (0x0078916C) and initMainWpnMotion (0x00B5B4EE) load the secondary
//       palette's archives and motion lists from the main weapon only for a staff;
//       initSubWpnMotion (0x00B5B903) releases them for anything else;
//     the action tables mark a staff skill "either palette" (type 5) and a longsword or warhammer
//       skill "main palette only" (type 3; cPlActCheckTbl::mCheckActSkillGSwordTbl, 0x014F7538), so
//       checkCmdType (0x00ABA810) never fires a Warrior skill from the secondary palette;
//     the skill menu (uGUISkillLearn and uGUISkillBase, 19 places) shows six slots only for the two
//       staff categories; the Warrior's category is 1 (longsword and warhammer, weapon bits 0x88).
//
// What this plugin does (six_skill_warrior.ini, next to this file)
//   Mode = six   the Warrior takes the staff's path at every one of those places:
//     - the 19 skill-menu compares also accept category 1: six slots to equip (for you and your
//       pawns), written into the longsword and warhammer rows the game already saves;
//     - the 5 weapon compares also accept categories 3 and 7 (longsword, warhammer) for the party
//       (the Arisen's and the pawns' cPlayerInfo in sGameSys); everyone else stays vanilla;
//     - checkCmdType treats a longsword/warhammer skill as "either palette" for the party: the jump
//       table entry for type 3 (0x00ABA9D8) leads to a thunk that sends those entries to the game's
//       own type-5 code and everything else to the type-3 code as before.
//   Mode = off   nothing is patched.
//   Each compare site's first instruction pair (cmp REG, imm8; je) becomes a jmp to a small thunk
//   that repeats it, adds the Warrior's value and continues at the original second compare.  Nothing
//   runs per frame except those compares; the save's arrays keep their size.
//
// Safety
//   DDDA.exe build 2364871 only.  Every site's bytes, the jump table, the tables the design relies
//   on and the code that places the party's cPlayerInfo are compared first; on any difference nothing
//   is patched and riftstone\logs\six_skill_warrior.log says why.  All sites or none.  Original code;
//   no third-party source.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

namespace {

// ---- build 2364871 --------------------------------------------------------------------------
constexpr uintptr_t IMAGE_BASE = 0x00400000;
constexpr uintptr_t GAME_SYS = 0x018FA4BC;       // sGameSys::mpInstance
constexpr uint32_t OFF_PL_INFO = 0xA76D0;         // sGameSys: the Arisen's cPlayerInfo
constexpr uint32_t OFF_CMC_INFO = 0xA7EC0;        // sGameSys: the pawns' cPlayerInfo, 3 of them
constexpr uint32_t CMC_INFO_SIZE = 0x1660;
constexpr uint32_t OFF_PLAYER_INFO = 0x3DEC;      // uPlayerBase: its cPlayerInfo

constexpr uint8_t UI_WARRIOR = 1;                 // skill-menu category: longsword and warhammer
constexpr uint8_t WPN_GSWORD = 3, WPN_HAMMER = 7; // nWeapon categories of the Warrior's weapons

constexpr uintptr_t GSWORD_SKILLS = 0x014F7538;   // cPlActCheckTbl::mCheckActSkillGSwordTbl
constexpr uint32_t GSWORD_SKILL_COUNT = 10;       // 0x20 bytes each, then the -1 terminator
constexpr uintptr_t CMD_TABLE = 0x00ABA9D0;       // checkCmdType's jump table by type - 1
constexpr uintptr_t CMD_TYPE3 = 0x00ABA8B2;       // type 3: main palette only
constexpr uintptr_t CMD_TYPE5 = 0x00ABA91F;       // type 5: either palette (the staff's)

enum Reg : uint8_t { EAX = 0, ECX = 1, EDX = 2, EBX = 3, ESP = 4, EBP = 5, ESI = 6, EDI = 7 };

// Where a weapon site finds the cPlayerInfo whose party membership decides.
enum Info : uint8_t {
    INFO_NONE,        // skill-menu sites: no guard
    INFO_ECX,         // removeIllegalCstmSkill: this
    INFO_EDI,         // initMainWpnMotion: the player's cPlayerInfo
    INFO_STACK_38,    // the archive loader: its cPlayerInfo argument, [esp+0x38] at the site
    INFO_PLAYER_EBP,  // initSubWpnMotion: [uPlayerBase(ebp) +0x3DEC]
};

struct Site {
    const char* what;
    uintptr_t at;
    Reg reg;
    uint8_t len;       // bytes of the first cmp/je pair: 5 (short je) or 9 (near je)
    uintptr_t wand;    // that je's target: the staff's path
    Info info;         // INFO_NONE: a skill-menu category site (adds 1); else a weapon site (adds 3, 7)
    uint8_t bytes[18]; // the original compare pair(s), checked before patching
    uint8_t n;
};

const Site SITES[] = {
    // ---- weapon categories 5/6 (staff) -> also 3/7 for the party ----
    {"removeIllegalCstmSkill: keep the secondary palette of a staff with no secondary weapon", 0x007805E9, EAX, 5,
     0x007805F7, INFO_ECX, {0x83, 0xF8, 0x05, 0x74, 0x09, 0x83, 0xF8, 0x06, 0x0F, 0x85, 0x30, 0x01, 0x00, 0x00}, 14},
    {"removeIllegalCstmSkill: a staff keeps it, anything else without a secondary weapon empties it", 0x007806EF,
     EAX, 5, 0x00780723, INFO_ECX, {0x83, 0xF8, 0x05, 0x74, 0x2F, 0x83, 0xF8, 0x06, 0x74, 0x2A}, 10},
    {"skill archives: slots 4-6 load with the main weapon for a staff", 0x0078916C, ESI, 5, 0x00789176,
     INFO_STACK_38, {0x83, 0xFE, 0x05, 0x74, 0x05, 0x83, 0xFE, 0x06, 0x75, 0x09}, 10},
    {"initMainWpnMotion: a staff loads the secondary palette's motion lists", 0x00B5B4EE, EDX, 5, 0x00B5B4FC,
     INFO_EDI, {0x83, 0xFA, 0x05, 0x74, 0x09, 0x83, 0xFA, 0x06, 0x0F, 0x85, 0x8A, 0x01, 0x00, 0x00}, 14},
    {"initSubWpnMotion: a staff keeps them, anything else releases them", 0x00B5B903, EBX, 5, 0x00B5B93A,
     INFO_PLAYER_EBP, {0x83, 0xFB, 0x05, 0x74, 0x32, 0x83, 0xFB, 0x06, 0x74, 0x2D}, 10},
    // ---- skill-menu categories 3/4 (staff, archistaff) -> also 1 (Warrior) ----
    {"skill menu: six slots to choose from (makeEquipLineup)", 0x006F9CDE, ESI, 5, 0x006F9CFA, INFO_NONE,
     {0x83, 0xFE, 0x03, 0x74, 0x17, 0x83, 0xFE, 0x04, 0x74, 0x12}, 10},
    {"skill menu: a skill counts as equipped in any of six slots", 0x006FA9B5, ECX, 5, 0x006FA9C4, INFO_NONE,
     {0x83, 0xF9, 0x03, 0x74, 0x0A, 0xBA, 0x03, 0x00, 0x00, 0x00, 0x83, 0xF9, 0x04, 0x75, 0x05}, 15},
    {"skill menu: six slots (a copy of makeEquipLineup)", 0x006F8695, EDI, 5, 0x006F86AB, INFO_NONE,
     {0x83, 0xFF, 0x03, 0x74, 0x11, 0x83, 0xFF, 0x04, 0x74, 0x0C}, 10},
    {"skill menu: six slots (a copy of makeEquipLineup)", 0x006F8F05, EDI, 5, 0x006F8F1B, INFO_NONE,
     {0x83, 0xFF, 0x03, 0x74, 0x11, 0x83, 0xFF, 0x04, 0x74, 0x0C}, 10},
    {"skill menu: six slots (a copy of makeEquipLineup)", 0x006F9288, EDI, 5, 0x006F929E, INFO_NONE,
     {0x83, 0xFF, 0x03, 0x74, 0x11, 0x83, 0xFF, 0x04, 0x74, 0x0C}, 10},
    {"skill menu: six slots (a copy of makeEquipLineup)", 0x006F93C8, EDI, 5, 0x006F93DE, INFO_NONE,
     {0x83, 0xFF, 0x03, 0x74, 0x11, 0x83, 0xFF, 0x04, 0x74, 0x0C}, 10},
    {"skill menu: the six-slot layout when it opens on equipping (setup)", 0x006FB858, EAX, 5, 0x006FB87C, INFO_NONE,
     {0x83, 0xF8, 0x03, 0x74, 0x1F, 0x83, 0xF8, 0x04, 0x74, 0x1A}, 10},
    {"skill menu: the six slot frames every frame (update)", 0x006FC023, EAX, 5, 0x006FC064, INFO_NONE,
     {0x83, 0xF8, 0x03, 0x74, 0x3C, 0x83, 0xF8, 0x04, 0x74, 0x37}, 10},
    {"skill menu: the six-slot layout on entering the slots", 0x006FCC37, EAX, 5, 0x006FCC5A, INFO_NONE,
     {0x83, 0xF8, 0x03, 0x74, 0x1E, 0x83, 0xF8, 0x04, 0x74, 0x19}, 10},
    {"skill menu: the six-slot layout on leaving the slots", 0x006FD0F1, EAX, 5, 0x006FD11F, INFO_NONE,
     {0x83, 0xF8, 0x03, 0x74, 0x29, 0x83, 0xF8, 0x04, 0x74, 0x24}, 10},
    {"skill menu: the six-slot display (dispEquipSlot)", 0x006FF5EE, EAX, 5, 0x006FF601, INFO_NONE,
     {0x83, 0xF8, 0x03, 0x74, 0x0E, 0x83, 0xF8, 0x04, 0x74, 0x09}, 10},
    {"skill menu: the cursor over six slots (a slot chosen)", 0x007039CC, EAX, 9, 0x00703AB4, INFO_NONE,
     {0x83, 0xF8, 0x03, 0x0F, 0x84, 0xDF, 0x00, 0x00, 0x00, 0x83, 0xF8, 0x04, 0x0F, 0x84, 0xD6, 0x00, 0x00, 0x00}, 18},
    {"skill menu: six slots (a copy of makeEquipLineup)", 0x00703F8F, EDI, 5, 0x00703FA5, INFO_NONE,
     {0x83, 0xFF, 0x03, 0x74, 0x11, 0x83, 0xFF, 0x04, 0x74, 0x0C}, 10},
    {"skill menu: six slots (a copy of makeEquipLineup)", 0x0070412E, EDI, 5, 0x00704144, INFO_NONE,
     {0x83, 0xFF, 0x03, 0x74, 0x11, 0x83, 0xFF, 0x04, 0x74, 0x0C}, 10},
    {"skill menu: six slots (a copy of makeEquipLineup)", 0x007042D0, EDI, 5, 0x007042E6, INFO_NONE,
     {0x83, 0xFF, 0x03, 0x74, 0x11, 0x83, 0xFF, 0x04, 0x74, 0x0C}, 10},
    {"skill menu: six slots after a skill is equipped (a copy of makeEquipLineup)", 0x00704746, EDI, 5, 0x0070475C,
     INFO_NONE, {0x83, 0xFF, 0x03, 0x74, 0x11, 0x83, 0xFF, 0x04, 0x74, 0x0C}, 10},
    {"skill menu: the cursor after a skill is equipped", 0x00704A70, EAX, 5, 0x00704ACE, INFO_NONE,
     {0x83, 0xF8, 0x03, 0x74, 0x59, 0x83, 0xF8, 0x04, 0x74, 0x54}, 10},
    {"skill menu: six slots after a skill is equipped (a copy of makeEquipLineup)", 0x00704D05, EDI, 5, 0x00704D1B,
     INFO_NONE, {0x83, 0xFF, 0x03, 0x74, 0x11, 0x83, 0xFF, 0x04, 0x74, 0x0C}, 10},
    {"skill menu: the highlight over six slots", 0x007057AF, ECX, 5, 0x007057E3, INFO_NONE,
     {0x83, 0xF9, 0x03, 0x74, 0x2F, 0x83, 0xF9, 0x04, 0x74, 0x2A}, 10},
};
constexpr size_t SITE_COUNT = sizeof SITES / sizeof SITES[0];

struct Expect {
    const char* what;
    uintptr_t at;
    uint8_t bytes[32];
    uint8_t len;
};

// What the design relies on besides the sites themselves.
const Expect CONTEXT[] = {
    {"checkCmdType: the switch on the entry's type", 0x00ABA88E,
     {0x8B, 0x45, 0x08, 0x48, 0x83, 0xF8, 0x0B, 0x0F, 0x87, 0x09, 0x01, 0x00, 0x00, 0xFF, 0x24, 0x85, 0xD0, 0xA9,
      0xAB, 0x00}, 20},
    {"checkCmdType: type 3 (main palette: the main-weapon skill button held)", CMD_TYPE3,
     {0xF6, 0x86, 0xE4, 0x32, 0x00, 0x00, 0x04}, 7},
    {"checkCmdType: type 5 (either palette)", CMD_TYPE5,
     {0x85, 0xF6, 0x74, 0x23, 0xF6, 0x86, 0x6D, 0x20, 0x00, 0x00, 0x02}, 11},
    {"setupPlayerInfo: the Arisen's cPlayerInfo is sGameSys +0xA76D0", 0x00B5A833,
     {0x8B, 0x0D, 0xBC, 0xA4, 0x8F, 0x01, 0x81, 0xC1, 0xD0, 0x76, 0x0A, 0x00}, 12},
    {"setupPlayerInfo: a pawn's is sGameSys +0xA7EC0 + 0x1660 x its index", 0x00B5A846,
     {0x8B, 0x88, 0x54, 0x58, 0x00, 0x00, 0x69, 0xC9, 0x60, 0x16, 0x00, 0x00, 0x56, 0x8B, 0x35, 0xBC, 0xA4, 0x8F, 0x01,
      0x8D, 0x8C, 0x31, 0xC0, 0x7E, 0x0A, 0x00}, 26},
    {"setSkillFromEquipWeapon: the main weapon's slot 4 goes to the secondary palette", 0x007803C9,
     {0x8B, 0x94, 0x88, 0x2C, 0x01, 0x00, 0x00, 0x3B, 0xD6, 0x74, 0x06, 0x89, 0x90, 0x7C, 0x02, 0x00, 0x00}, 17},
    {"the skill menu's categories: the Warrior (vocation 7) has only category 1", 0x01516E1C,
     {0x01, 0x00, 0x00, 0x00, 0xFF, 0xFF, 0xFF, 0xFF}, 8},
    {"the skill menu's weapon bits: category 1 is longsword and warhammer (0x88)", 0x01516BDC,
     {0x88, 0x00, 0x00, 0x00}, 4},
    {"the custom skills of longsword (3) and warhammer (7) start at skill 100", 0x014F6C8C,
     {0x64, 0x00, 0x00, 0x00}, 4},
    {"(warhammer)", 0x014F6C9C, {0x64, 0x00, 0x00, 0x00}, 4},
};

wchar_t g_logPath[MAX_PATH];

void Log(const char* fmt, ...) {
    char line[768];
    va_list ap;
    va_start(ap, fmt);
    int n = _vsnprintf_s(line, sizeof line - 2, _TRUNCATE, fmt, ap);
    va_end(ap);
    if (n < 0) n = (int)strlen(line);
    line[n++] = '\r';
    line[n++] = '\n';
    HANDLE h = CreateFileW(g_logPath, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_ALWAYS,
                           FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) return;
    DWORD w;
    WriteFile(h, line, (DWORD)n, &w, nullptr);
    CloseHandle(h);
}

bool Readable(uintptr_t at, size_t n) {
    MEMORY_BASIC_INFORMATION mbi;
    for (uintptr_t p = at; p < at + n;) {
        if (!VirtualQuery((const void*)p, &mbi, sizeof mbi) || mbi.State != MEM_COMMIT ||
            (mbi.Protect & (PAGE_NOACCESS | PAGE_GUARD)))
            return false;
        p = (uintptr_t)mbi.BaseAddress + mbi.RegionSize;
    }
    return true;
}

bool Same(uintptr_t at, const uint8_t* bytes, size_t n) { return Readable(at, n) && memcmp((const void*)at, bytes, n) == 0; }

bool Verify() {
    for (size_t i = 0; i < SITE_COUNT; i++) {
        const Site& s = SITES[i];
        if (!Same(s.at, s.bytes, s.n))
            return Log("refused: %s at 0x%08X is not the expected code (another build, or already patched)", s.what,
                       (unsigned)s.at),
                   false;
        // The je's own target must be the staff path the table names.
        uintptr_t target = s.len == 5 ? s.at + 5 + (int8_t)s.bytes[4] : s.at + 9 + *(const int32_t*)(s.bytes + 5);
        if (target != s.wand)
            return Log("refused: the table's target for 0x%08X is wrong (0x%08X)", (unsigned)s.at, (unsigned)target), false;
    }
    for (const Expect& e : CONTEXT) {
        if (!Same(e.at, e.bytes, e.len))
            return Log("refused: %s at 0x%08X is not the expected data or code", e.what, (unsigned)e.at), false;
    }
    if (!Readable(CMD_TABLE, 12 * 4)) return Log("refused: checkCmdType's jump table is not readable"), false;
    const uint32_t* table = (const uint32_t*)CMD_TABLE;
    if (table[2] != CMD_TYPE3 || table[4] != CMD_TYPE5)
        return Log("refused: checkCmdType's jump table does not lead types 3 and 5 to 0x%08X and 0x%08X (already "
                   "patched?)", (unsigned)CMD_TYPE3, (unsigned)CMD_TYPE5),
               false;
    // The longsword/warhammer skill table: ten main-palette skills (type 3), then the end.
    if (!Readable(GSWORD_SKILLS, (GSWORD_SKILL_COUNT + 1) * 0x20)) return Log("refused: the skill table is unreadable"), false;
    for (uint32_t i = 0; i < GSWORD_SKILL_COUNT; i++) {
        const int32_t* e = (const int32_t*)(GSWORD_SKILLS + 0x20 * i);
        if (e[0] != (int32_t)(0x01030028 + i) || e[1] != -1 || e[2] != 3 || e[3] != 0)
            return Log("refused: entry %u of the longsword skill table (0x%08X) is not skill action 0x%08X, type 3", i,
                       (unsigned)(GSWORD_SKILLS + 0x20 * i), (unsigned)(0x01030028 + i)),
                   false;
    }
    if (*(const int32_t*)(GSWORD_SKILLS + 0x20 * GSWORD_SKILL_COUNT) != -1)
        return Log("refused: the longsword skill table does not end after ten entries"), false;
    return true;
}

// ---- who gets six skills: the party's cPlayerInfo (the Arisen's and the three pawn records) ------
BOOL __stdcall IsPartyInfo(uint32_t info) {
    uint32_t sys = *(const volatile uint32_t*)GAME_SYS;
    if (!sys || !info) return FALSE;
    if (info == sys + OFF_PL_INFO) return TRUE;
    uint32_t d = info - (sys + OFF_CMC_INFO);
    return d == 0 || d == CMC_INFO_SIZE || d == 2 * CMC_INFO_SIZE;
}

// ---- thunk code, written once into one executable page ------------------------------------------
struct Emit {
    uint8_t* p;
    uint8_t* base;
    size_t cap;
    bool ok = true;
    void byte(uint8_t b) {
        if ((size_t)(p - base) >= cap) ok = false;
        else *p++ = b;
    }
    void dword(uint32_t v) {
        for (int i = 0; i < 4; i++) byte((uint8_t)(v >> (8 * i)));
    }
    uintptr_t here() const { return (uintptr_t)p; }
    void rel32(uintptr_t target) { dword((uint32_t)(target - (here() + 4))); }
    void cmp_imm8(Reg r, uint8_t v) { byte(0x83), byte((uint8_t)(0xF8 | r)), byte(v); }
    void je(uintptr_t t) { byte(0x0F), byte(0x84), rel32(t); }
    void jne(uintptr_t t) { byte(0x0F), byte(0x85), rel32(t); }
    void jb(uintptr_t t) { byte(0x0F), byte(0x82), rel32(t); }
    void jae(uintptr_t t) { byte(0x0F), byte(0x83), rel32(t); }
    void jmp(uintptr_t t) { byte(0xE9), rel32(t); }
    void call(uintptr_t t) { byte(0xE8), rel32(t); }
    // push eax/ecx/edx; push <info>; call IsPartyInfo; test al, al; pop edx/ecx/eax  (flags survive the pops)
    void party(Info info) {
        byte(0x50), byte(0x51), byte(0x52);
        switch (info) {
            case INFO_ECX: byte(0x51); break;                                              // push ecx
            case INFO_EDI: byte(0x57); break;                                              // push edi
            case INFO_STACK_38: byte(0xFF), byte(0x74), byte(0x24), byte(0x38 + 12); break; // push [esp+0x44]
            case INFO_PLAYER_EBP: byte(0xFF), byte(0xB5), dword(OFF_PLAYER_INFO); break;   // push [ebp+0x3DEC]
            case INFO_NONE: byte(0x6A), byte(0x00); break;
        }
        call((uintptr_t)&IsPartyInfo);
        byte(0x84), byte(0xC0);  // test al, al
        byte(0x5A), byte(0x59), byte(0x58);
    }
};

// cmp REG, 3; je wand; cmp REG, 1; je wand; jmp resume          (skill menu)
// cmp REG, 5; je wand; cmp REG, 3; je party; cmp REG, 7; jne resume;
//   party: <IsPartyInfo(info)>; jnz wand; jmp resume              (weapons)
uintptr_t EmitSite(Emit& e, const Site& s) {
    uintptr_t start = e.here();
    uintptr_t resume = s.at + s.len;
    if (s.info == INFO_NONE) {
        e.cmp_imm8(s.reg, 3), e.je(s.wand);
        e.cmp_imm8(s.reg, UI_WARRIOR), e.je(s.wand);
        e.jmp(resume);
    } else {
        e.cmp_imm8(s.reg, 5), e.je(s.wand);
        e.cmp_imm8(s.reg, WPN_GSWORD);
        uint8_t* jeParty = e.p;
        e.je(0);  // patched below
        e.cmp_imm8(s.reg, WPN_HAMMER), e.jne(resume);
        uintptr_t party = e.here();
        if (e.ok) {
            uint8_t* save = e.p;
            e.p = jeParty;
            e.je(party);
            e.p = save;
        }
        e.party(s.info);
        e.jne(s.wand);
        e.jmp(resume);
    }
    return start;
}

// Entered from checkCmdType's jump table for a type-3 entry, with the entry in ebp and the player in esi.
// test esi, esi; je t3; cmp ebp, table; jb t3; cmp ebp, table end; jae t3; <IsPartyInfo([esi+0x3DEC])>;
// jnz t5; t3: jmp type3; t5: jmp type5
uintptr_t EmitDispatch(Emit& e) {
    uintptr_t start = e.here();
    uint8_t* fix[3];
    e.byte(0x85), e.byte(0xF6);  // test esi, esi
    fix[0] = e.p, e.je(0);
    e.byte(0x81), e.byte(0xFD), e.dword((uint32_t)GSWORD_SKILLS);  // cmp ebp, imm32
    fix[1] = e.p, e.jb(0);
    e.byte(0x81), e.byte(0xFD), e.dword((uint32_t)(GSWORD_SKILLS + 0x20 * GSWORD_SKILL_COUNT));
    fix[2] = e.p, e.jae(0);
    e.byte(0x50), e.byte(0x51), e.byte(0x52);
    e.byte(0xFF), e.byte(0xB6), e.dword(OFF_PLAYER_INFO);  // push [esi+0x3DEC]
    e.call((uintptr_t)&IsPartyInfo);
    e.byte(0x84), e.byte(0xC0);
    e.byte(0x5A), e.byte(0x59), e.byte(0x58);
    uint8_t* toType5 = e.p;
    e.jne(0);
    uintptr_t type3 = e.here();
    e.jmp(CMD_TYPE3);
    uintptr_t type5 = e.here();
    e.jmp(CMD_TYPE5);
    if (e.ok) {
        uint8_t* save = e.p;
        e.p = fix[0], e.je(type3);
        e.p = fix[1], e.jb(type3);
        e.p = fix[2], e.jae(type3);
        e.p = toType5, e.jne(type5);
        e.p = save;
    }
    return start;
}

struct Write {
    uintptr_t at;
    uint8_t len;
    uint8_t now[9];
    uint8_t was[9];
};

bool Poke(uintptr_t at, const uint8_t* bytes, size_t n) {
    DWORD old;
    if (!VirtualProtect((void*)at, n, PAGE_EXECUTE_READWRITE, &old)) return false;
    memcpy((void*)at, bytes, n);
    VirtualProtect((void*)at, n, old, &old);
    FlushInstructionCache(GetCurrentProcess(), (void*)at, n);
    return true;
}

// [warrior] Mode from the ini next to this file; six when the file or the key is missing.
bool LoadMode(HMODULE self, bool& six) {
    wchar_t ini[MAX_PATH];
    GetModuleFileNameW(self, ini, MAX_PATH);
    wchar_t* dot = wcsrchr(ini, L'.');
    wchar_t* slash = wcsrchr(ini, L'\\');
    if (dot && (!slash || dot > slash)) *dot = 0;
    wcscat_s(ini, L".ini");
    wchar_t v[32];
    GetPrivateProfileStringW(L"warrior", L"Mode", L"six", v, 32, ini);
    wchar_t* s = v;
    while (*s == L' ' || *s == L'\t') s++;
    size_t n = wcslen(s);
    while (n && (s[n - 1] == L' ' || s[n - 1] == L'\t')) s[--n] = 0;
    if (_wcsicmp(s, L"six") == 0) return six = true, true;
    if (_wcsicmp(s, L"off") == 0) return six = false, true;
    Log("six_skill_warrior.ini: Mode = %S is not six or off; nothing is patched", s);
    return false;
}

void Start(HMODULE self) {
    wchar_t root[MAX_PATH];
    GetModuleFileNameW(nullptr, root, MAX_PATH);
    wchar_t* slash = wcsrchr(root, L'\\');
    if (slash) *slash = 0;
    wchar_t dir[MAX_PATH];
    _snwprintf_s(dir, MAX_PATH, _TRUNCATE, L"%s\\riftstone", root);
    CreateDirectoryW(dir, nullptr);
    _snwprintf_s(dir, MAX_PATH, _TRUNCATE, L"%s\\riftstone\\logs", root);
    CreateDirectoryW(dir, nullptr);
    _snwprintf_s(g_logPath, MAX_PATH, _TRUNCATE, L"%s\\riftstone\\logs\\six_skill_warrior.log", root);
    // A fresh log per game session; a second copy of the plugin in the same process appends to it.
    wchar_t probe[8];
    if (GetEnvironmentVariableW(L"RIFTSTONE_SIX_SKILL_WARRIOR_LOG", probe, 8) == 0) {
        DeleteFileW(g_logPath);
        SetEnvironmentVariableW(L"RIFTSTONE_SIX_SKILL_WARRIOR_LOG", L"1");
    }
    // The game is a fixed-base image; a test harness that maps DDDA.exe itself sets the variable.
    bool harness = GetEnvironmentVariableW(L"RIFTSTONE_SIX_SKILL_WARRIOR_HARNESS", probe, 8) > 0;
    if (!harness && (uintptr_t)GetModuleHandleW(nullptr) != IMAGE_BASE) {
        Log("refused: the host is not DDDA.exe at 0x00400000");
        return;
    }
    bool six;
    if (!LoadMode(self, six)) return;
    if (!six) {
        Log("six_skill_warrior: Mode = off, nothing patched");
        return;
    }
    if (!Verify()) return;

    uint8_t* page = (uint8_t*)VirtualAlloc(nullptr, 0x1000, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!page) return Log("failed: no memory for the thunks (%lu); nothing is patched", GetLastError());
    Emit e{page, page, 0x1000};
    Write writes[SITE_COUNT + 1];
    for (size_t i = 0; i < SITE_COUNT; i++) {
        const Site& s = SITES[i];
        uintptr_t thunk = EmitSite(e, s);
        Write& w = writes[i];
        w.at = s.at;
        w.len = s.len;
        memcpy(w.was, s.bytes, s.len);
        w.now[0] = 0xE9;
        int32_t rel = (int32_t)(thunk - (s.at + 5));
        memcpy(w.now + 1, &rel, 4);
        for (uint8_t k = 5; k < s.len; k++) w.now[k] = 0x90;  // never reached: the jmp leaves first
    }
    uintptr_t dispatch = EmitDispatch(e);
    Write& jt = writes[SITE_COUNT];
    jt.at = CMD_TABLE + 2 * 4;
    jt.len = 4;
    memcpy(jt.was, &CMD_TYPE3, 4);
    memcpy(jt.now, &dispatch, 4);
    DWORD old;
    if (!e.ok || !VirtualProtect(page, 0x1000, PAGE_EXECUTE_READ, &old)) {
        VirtualFree(page, 0, MEM_RELEASE);
        return Log("failed: the thunks could not be written; nothing is patched");
    }
    FlushInstructionCache(GetCurrentProcess(), page, 0x1000);

    // All of them or none: a site that cannot be written puts the earlier ones back.
    for (size_t i = 0; i < SITE_COUNT + 1; i++) {
        if (!Poke(writes[i].at, writes[i].now, writes[i].len)) {
            Log("failed: VirtualProtect at 0x%08X (%lu); nothing is patched", (unsigned)writes[i].at, GetLastError());
            while (i--) Poke(writes[i].at, writes[i].was, writes[i].len);
            return;
        }
    }
    Log("six_skill_warrior: Mode = six (%s): Warriors in your party get six skill slots, the second row on the "
        "secondary-weapon skill button", harness ? "harness" : "game");
    for (size_t i = 0; i < SITE_COUNT; i++) Log("  0x%08X  %s", (unsigned)SITES[i].at, SITES[i].what);
    Log("  0x%08X  checkCmdType, type 3: longsword/warhammer skills of the party fire from either palette (type 5)",
        (unsigned)jt.at);
    Log("thunks at 0x%08X (%u bytes)", (unsigned)(uintptr_t)page, (unsigned)(e.p - page));
}

}  // namespace

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(module);
        Start(module);
    }
    return TRUE;
}

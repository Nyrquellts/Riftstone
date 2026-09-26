// ssw_harness_core -- runs six_skill_warrior inside the real game code, without the game.
//
//   ssw_stub.exe <DDDA.exe> <six_skill_warrior.asi> <six|off|bogus|tampered>
//
// Maps DDDA.exe's image at its fixed base (0x00400000) over the stub's image, loads the plugin (which
// verifies and patches the mapped code), then:
//   * every patched compare (24) and checkCmdType's type-3 dispatch run from their first byte with
//     each category value, for a party member and for anyone else; captures at the two places each can
//     go say which it took, and every register must come back as it went in;
//   * the game's own functions run on fake objects:
//       setSkillFromEquipWeapon    a Warrior's six-slot row fills both palettes (unchanged code);
//       removeIllegalCstmSkill     what survives of the secondary palette, by weapon and by owner;
//       removeJobMismatchCstmSkill the Warrior's rows are never cut (unchanged code);
//       getNextAction              which action the secondary-weapon skill button starts;
//       initMainWpnMotion / initSubWpnMotion   which skill motion lists load and stay;
//       the skill-archive loader   which archive each of the six slots asks for;
//       the skill menu's "is it equipped" test and makeEquipLineup: how many slots it counts.
// Profiles (run_tests.py writes the matching six_skill_warrior.ini):
//   six       the shipped ini: everything above as the plugin describes
//   off       Mode = off: nothing patched, all vanilla
//   bogus     Mode = sometimes: an unknown mode patches nothing
//   tampered  the shipped ini, but one byte of a skill-menu compare differs: the plugin refuses
// Prints "pass"/"FAIL" lines; exits 0 when everything passed, 1 on a failure, 2 when the image cannot
// be mapped here (reported as a skip by run_tests.py).
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shellapi.h>

#include <setjmp.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include <string>
#include <vector>

namespace {
constexpr uintptr_t BASE = 0x00400000;
constexpr uintptr_t GAME_SYS = 0x018FA4BC;       // sGameSys::mpInstance
constexpr uintptr_t S_RESOURCE = 0x018D0AA0;     // sResource::mpInstance
constexpr uintptr_t RES_LOCKING = 0x018D0BB0;    // a byte sResource's addRef tests before locking
constexpr uint32_t OFF_PL_INFO = 0xA76D0, OFF_CMC_INFO = 0xA7EC0, CMC_INFO_SIZE = 0x1660;
constexpr uint32_t OFF_SYS_FLAGS = 0xB8844;      // sGameSys flags checkCmdType and removeJobMismatch test

// The game's functions this harness calls.
constexpr uintptr_t SET_SKILL_FROM_WEAPON = 0x00780350;   // eax = cPlayerInfo
constexpr uintptr_t REMOVE_ILLEGAL = 0x00780590;          // ecx = cPlayerInfo
constexpr uintptr_t REMOVE_JOB_MISMATCH = 0x00780840;     // edi = cPlayerInfo
constexpr uintptr_t GET_NEXT_ACTION = 0x00ABA690;         // eax = cPlActCheckTbl, esi = the result
constexpr uintptr_t INIT_MAIN_MOTION = 0x00B5B400;        // stdcall(uPlayerBase)
constexpr uintptr_t INIT_SUB_MOTION_TAIL = 0x00B5B8EA;    // initSubWpnMotion with no secondary weapon
constexpr uintptr_t ARC_LOADER = 0x00789010;              // stdcall(loader, cPlayerInfo, what)
constexpr uintptr_t IS_EQUIPPED = 0x006FA980;             // eax = menu, ecx = category, edi = skill
constexpr uintptr_t MAKE_EQUIP_LINEUP = 0x006F9C50;       // stdcall(menu, bool)
constexpr uintptr_t CMD_DISPATCH = 0x00ABA89B;            // jmp [eax*4 + 0x00ABA9D0]
constexpr uintptr_t CMD_TABLE = 0x00ABA9D0, CMD_TYPE3 = 0x00ABA8B2, CMD_TYPE5 = 0x00ABA91F;
constexpr uintptr_t GSWORD_SKILLS = 0x014F7538, SWORD_SKILLS = 0x014F7298;

// cPlayerInfo (PC)
constexpr uint32_t PI_INDEX = 0x08, PI_JOB = 0x10, PI_MAIN = 0x14, PI_SUB = 0x18, PI_ROWS = 0x120, PI_MAIN_SKILL = 0x270,
                   PI_SUB_SKILL = 0x27C, PI_STAMINA = 0x2A8, PI_SKILL_INFO = 0x72C;
// uPlayerBase (PC)
constexpr uint32_t PL_FLAGS = 0x206D, PL_STATUS = 0x26A0, PL_CANCEL = 0x2588, PL_ATTR = 0x273C, PL_ACTION = 0x2DD4,
                   PL_TRIGGER = 0x32E0, PL_HELD = 0x32E4, PL_JOB = 0x354C, PL_MAIN_ID = 0x3554, PL_SUB_ID = 0x3558,
                   PL_MAIN_PAL = 0x35F4, PL_SUB_PAL = 0x3600, PL_INFO = 0x3DEC, PL_WPN_MOT = 0x4C28,
                   PL_CSTM_MOT = 0x4C2C, PL_SKILL_LISTS = 0x4C38, PL_IN_USE = 0x4C84;

constexpr uint32_t GSWORD = 0x03000001, HAMMER = 0x07000001, WAND = 0x05000001, SWORD = 0x01000001,
                   SHIELD = 0x08000001, NONE = 0xFFFFFFFFu;
constexpr uint32_t ACT_GSWORD_SKILL0 = 0x01030028;  // skill 100's action; skill 100 + i is + i
constexpr uint32_t ACT_WAND_SKILL0 = 0x01070000;    // skill 210's action
// Pad command bits (the button layer at 0x00B52A00): Square/Triangle/Circle 1/2/0x1000; with the
// main-weapon skill button held +0x10/0x20/0x40, with the secondary-weapon one +0x80/0x100/0x200.
constexpr uint32_t HELD_MAIN = 4, HELD_SUB = 8;

bool g_six = false;
int g_fails = 0, g_checks = 0;
void Check(bool ok, const std::string& what) {
    g_checks++;
    printf("  %s  %s\n", ok ? "pass" : "FAIL", what.c_str());
    if (!ok) g_fails++;
}
std::string F(const char* fmt, ...) {
    char s[400];
    va_list ap;
    va_start(ap, fmt);
    _vsnprintf_s(s, sizeof s, _TRUNCATE, fmt, ap);
    va_end(ap);
    return s;
}
template <class T>
T& At(uintptr_t base, uint32_t off) { return *(T*)(base + off); }

uint8_t* Alloc(size_t n) { return (uint8_t*)VirtualAlloc(nullptr, n, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE); }

// ---- mapping DDDA.exe --------------------------------------------------------------------------------
bool MapImage(const wchar_t* exe) {
    HANDLE f = CreateFileW(exe, GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, 0, nullptr);
    if (f == INVALID_HANDLE_VALUE) return printf("cannot open the exe\n"), false;
    DWORD size = GetFileSize(f, nullptr), got = 0;
    std::vector<uint8_t> file(size);
    ReadFile(f, file.data(), size, &got, nullptr);
    CloseHandle(f);
    auto* dos = (IMAGE_DOS_HEADER*)file.data();
    auto* nt = (IMAGE_NT_HEADERS32*)(file.data() + dos->e_lfanew);
    if (nt->OptionalHeader.ImageBase != BASE) return printf("unexpected image base\n"), false;
    auto* host = (const uint8_t*)GetModuleHandleW(nullptr);
    auto* hostNt = (const IMAGE_NT_HEADERS32*)(host + ((const IMAGE_DOS_HEADER*)host)->e_lfanew);
    DWORD need = nt->OptionalHeader.SizeOfImage;
    if ((uintptr_t)host != BASE || hostNt->OptionalHeader.SizeOfImage < need)
        return printf("SKIP: run me through ssw_stub.exe (host at %p, 0x%X bytes)\n", (void*)host,
                      (unsigned)hostNt->OptionalHeader.SizeOfImage),
               false;
    void* at = (void*)BASE;
    DWORD old;
    if (!VirtualProtect(at, need, PAGE_EXECUTE_READWRITE, &old)) return printf("cannot unprotect the host image\n"), false;
    memset(at, 0, need);
    memcpy(at, file.data(), nt->OptionalHeader.SizeOfHeaders);
    auto* sec = IMAGE_FIRST_SECTION(nt);
    for (int i = 0; i < nt->FileHeader.NumberOfSections; i++, sec++) {
        DWORD n = min(sec->SizeOfRawData, sec->Misc.VirtualSize ? sec->Misc.VirtualSize : sec->SizeOfRawData);
        if (n && sec->PointerToRawData + n <= size)
            memcpy((uint8_t*)at + sec->VirtualAddress, file.data() + sec->PointerToRawData, n);
    }
    return true;
}

// ---- the sites ------------------------------------------------------------------------------------
enum Kind { UI, WPN_ECX, WPN_EDI, WPN_STACK, WPN_PLAYER };
struct Site {
    const char* name;
    uintptr_t at;
    int reg;          // 0 eax 1 ecx 2 edx 3 ebx 5 ebp 6 esi 7 edi
    Kind kind;
    uintptr_t wand;   // capture: the staff's path
    uintptr_t other;  // capture: the other path (0: U2, one capture that reads edx)
};
const Site SITES[] = {
    {"removeIllegalCstmSkill (keep)", 0x007805E9, 0, WPN_ECX, 0x007805F7, 0x00780727},
    {"removeIllegalCstmSkill (empty)", 0x007806EF, 0, WPN_ECX, 0x00780723, 0x007806F9},
    {"skill archives", 0x0078916C, 6, WPN_STACK, 0x00789176, 0x0078917F},
    {"initMainWpnMotion", 0x00B5B4EE, 2, WPN_EDI, 0x00B5B4FC, 0x00B5B686},
    {"initSubWpnMotion", 0x00B5B903, 3, WPN_PLAYER, 0x00B5B93A, 0x00B5B90D},
    {"makeEquipLineup", 0x006F9CDE, 6, UI, 0x006F9CFA, 0x006F9CE8},
    {"is-equipped count", 0x006FA9B5, 1, UI, 0x006FA9C9, 0},
    {"lineup copy 1", 0x006F8695, 7, UI, 0x006F86AB, 0x006F869F},
    {"lineup copy 2", 0x006F8F05, 7, UI, 0x006F8F1B, 0x006F8F0F},
    {"lineup copy 3", 0x006F9288, 7, UI, 0x006F929E, 0x006F9292},
    {"lineup copy 4", 0x006F93C8, 7, UI, 0x006F93DE, 0x006F93D2},
    {"setup layout", 0x006FB858, 0, UI, 0x006FB87C, 0x006FB862},
    {"update frames", 0x006FC023, 0, UI, 0x006FC064, 0x006FC02D},
    {"enter layout", 0x006FCC37, 0, UI, 0x006FCC5A, 0x006FCC41},
    {"leave layout", 0x006FD0F1, 0, UI, 0x006FD11F, 0x006FD0FB},
    {"dispEquipSlot", 0x006FF5EE, 0, UI, 0x006FF601, 0x006FF5F8},
    {"slot cursor", 0x007039CC, 0, UI, 0x00703AB4, 0x007039DE},
    {"lineup copy 5", 0x00703F8F, 7, UI, 0x00703FA5, 0x00703F99},
    {"lineup copy 6", 0x0070412E, 7, UI, 0x00704144, 0x00704138},
    {"lineup copy 7", 0x007042D0, 7, UI, 0x007042E6, 0x007042DA},
    {"lineup copy 8", 0x00704746, 7, UI, 0x0070475C, 0x00704750},
    {"cursor after equipping", 0x00704A70, 0, UI, 0x00704ACE, 0x00704A7A},
    {"lineup copy 9", 0x00704D05, 7, UI, 0x00704D1B, 0x00704D0F},
    {"slot highlight", 0x007057AF, 1, UI, 0x007057E3, 0x007057B9},
};

std::string SiteState() {  // one character per site: '.' vanilla, 'j' a jmp, '?' neither
    std::string s;
    for (const Site& x : SITES) {
        uint8_t b = *(const uint8_t*)x.at;
        s += b == 0xE9 ? 'j' : b == 0x83 ? '.' : '?';
    }
    uint32_t t3 = ((const uint32_t*)CMD_TABLE)[2];
    s += t3 == CMD_TYPE3 ? '.' : 'j';
    return s;
}

// ---- running from an address and catching where it goes ---------------------------------------------
jmp_buf g_back;
uint32_t g_in[8];   // eax ecx edx ebx esp(unused) ebp esi edi
uint32_t g_out[8];
uint32_t g_target, g_stack38, g_entryEsp, g_which;

void __cdecl BackC() { longjmp(g_back, 1); }

__declspec(naked) void CaptureA() {
    __asm {
        mov dword ptr [g_out + 0], eax
        mov dword ptr [g_out + 4], ecx
        mov dword ptr [g_out + 8], edx
        mov dword ptr [g_out + 12], ebx
        mov dword ptr [g_out + 16], esp
        mov dword ptr [g_out + 20], ebp
        mov dword ptr [g_out + 24], esi
        mov dword ptr [g_out + 28], edi
        mov g_which, 1
        call BackC
    }
}
__declspec(naked) void CaptureB() {
    __asm {
        mov dword ptr [g_out + 0], eax
        mov dword ptr [g_out + 4], ecx
        mov dword ptr [g_out + 8], edx
        mov dword ptr [g_out + 12], ebx
        mov dword ptr [g_out + 16], esp
        mov dword ptr [g_out + 20], ebp
        mov dword ptr [g_out + 24], esi
        mov dword ptr [g_out + 28], edi
        mov g_which, 2
        call BackC
    }
}

__declspec(naked) void JumpIn() {
    __asm {
        sub esp, 0x200
        mov eax, g_stack38
        mov dword ptr [esp + 0x38], eax
        mov g_entryEsp, esp
        mov eax, dword ptr [g_in + 0]
        mov ecx, dword ptr [g_in + 4]
        mov edx, dword ptr [g_in + 8]
        mov ebx, dword ptr [g_in + 12]
        mov ebp, dword ptr [g_in + 20]
        mov esi, dword ptr [g_in + 24]
        mov edi, dword ptr [g_in + 28]
        jmp dword ptr [g_target]
    }
}

#pragma warning(push)
#pragma warning(disable : 4611)
void Enter() {
    g_which = 0;
    if (setjmp(g_back) == 0) JumpIn();
}
#pragma warning(pop)

struct Hook {
    uintptr_t at;
    uint8_t saved[5];
};
void Place(Hook& h, uintptr_t at, void (*to)()) {
    h.at = at;
    memcpy(h.saved, (void*)at, 5);
    uint8_t* p = (uint8_t*)at;
    int32_t rel = (int32_t)((uintptr_t)to - (at + 5));
    p[0] = 0xE9;
    memcpy(p + 1, &rel, 4);
}
void Remove(Hook& h) { memcpy((void*)h.at, h.saved, 5); }

bool RegsKept(int except) {
    for (int r = 0; r < 8; r++) {
        if (r == 4) continue;
        if (r == except) continue;
        if (g_out[r] != g_in[r]) return false;
    }
    return g_out[4] == g_entryEsp;
}

// ---- the fake world ------------------------------------------------------------------------------------
uintptr_t g_sys, g_arisen, g_pawn, g_stranger;  // sGameSys; the party's cPlayerInfo; somebody else's
uint8_t* g_vtable;                              // a uPlayerBase / menu vtable: every slot returns 1

__declspec(naked) void RetTrue4() {  // thiscall, one argument
    __asm {
        mov eax, 1
        ret 4
    }
}

void SetupWorld() {
    g_sys = (uintptr_t)Alloc(0xC0000);
    At<uint32_t>(GAME_SYS, 0) = (uint32_t)g_sys;
    g_arisen = g_sys + OFF_PL_INFO;
    g_pawn = g_sys + OFF_CMC_INFO;
    g_stranger = (uintptr_t)Alloc(0x2000);
    g_vtable = Alloc(0x1000);
    for (int i = 0; i < 0x400; i++) ((uint32_t*)g_vtable)[i] = (uint32_t)(uintptr_t)&RetTrue4;
}

void SetBit(uintptr_t info, uint32_t skill) {
    uintptr_t si = info + PI_SKILL_INFO;
    At<uint32_t>(si, 4 + 4 * (skill >> 5)) |= 1u << (skill & 31);     // level 1
    At<uint32_t>(si, 0x3C + 4 * (skill >> 5)) |= 1u << (skill & 31);  // level 2
}

// A fresh cPlayerInfo: vocation, weapons, every weapon row empty, both palettes empty.
void ResetInfo(uintptr_t info, uint32_t index, uint32_t job, uint32_t main, uint32_t sub) {
    memset((void*)info, 0, 0x800);
    At<uint32_t>(info, PI_INDEX) = index;
    At<uint32_t>(info, PI_JOB) = job;
    At<uint32_t>(info, PI_MAIN) = main;
    At<uint32_t>(info, PI_SUB) = sub;
    for (int i = 0; i < 13 * 6; i++) At<int32_t>(info, PI_ROWS + 4 * i) = -1;
    for (int i = 0; i < 6; i++) At<int32_t>(info, PI_MAIN_SKILL + 4 * i) = -1;
    At<float>(info, PI_STAMINA) = 300.0f;
}
void Row(uintptr_t info, int category, const int* skills) {
    for (int i = 0; i < 6; i++) At<int32_t>(info, PI_ROWS + 0x18 * category + 4 * i) = skills[i];
}
void Palettes(uintptr_t info, const int* main3, const int* sub3) {
    for (int i = 0; i < 3; i++) {
        At<int32_t>(info, PI_MAIN_SKILL + 4 * i) = main3[i];
        At<int32_t>(info, PI_SUB_SKILL + 4 * i) = sub3[i];
    }
}
std::string Pal(uintptr_t info, uint32_t off) {
    return F("{%d, %d, %d}", At<int32_t>(info, off), At<int32_t>(info, off + 4), At<int32_t>(info, off + 8));
}

// ---- calling the game's functions --------------------------------------------------------------------------
uint32_t g_fn, g_a, g_b, g_c, g_ret;

__declspec(naked) void CallRegsAsm() {  // eax = g_a, ecx = g_b, edi = g_c, esi = g_c for getNextAction
    __asm {
        push ebx
        push esi
        push edi
        push ebp
        mov eax, g_a
        mov ecx, g_b
        mov edi, g_c
        mov esi, g_c
        call dword ptr [g_fn]
        mov g_ret, eax
        pop ebp
        pop edi
        pop esi
        pop ebx
        ret
    }
}
void CallRegs(uintptr_t fn, uint32_t a, uint32_t b, uint32_t c) {
    g_fn = (uint32_t)fn, g_a = a, g_b = b, g_c = c;
    CallRegsAsm();
}

// getNextAction: eax = cPlActCheckTbl, esi = the result; edi is the table the game keeps in edi too.
__declspec(naked) void CallNextActionAsm() {
    __asm {
        push ebx
        push esi
        push edi
        push ebp
        mov eax, g_a
        mov esi, g_c
        call dword ptr [g_fn]
        mov g_ret, eax
        pop ebp
        pop edi
        pop esi
        pop ebx
        ret
    }
}

__declspec(naked) void CallStd1Asm() {
    __asm {
        push ebx
        push esi
        push edi
        push ebp
        push g_a
        call dword ptr [g_fn]
        mov g_ret, eax
        pop ebp
        pop edi
        pop esi
        pop ebx
        ret
    }
}
__declspec(naked) void CallStd2Asm() {
    __asm {
        push ebx
        push esi
        push edi
        push ebp
        push g_b
        push g_a
        call dword ptr [g_fn]
        mov g_ret, eax
        pop ebp
        pop edi
        pop esi
        pop ebx
        ret
    }
}
__declspec(naked) void CallStd3Asm() {
    __asm {
        push ebx
        push esi
        push edi
        push ebp
        push g_c
        push g_b
        push g_a
        call dword ptr [g_fn]
        mov g_ret, eax
        pop ebp
        pop edi
        pop esi
        pop ebx
        ret
    }
}

// initSubWpnMotion from 0x00B5B8EA, where a player with no secondary weapon has had its default motion
// list loaded: ebp = the player, ebx = the main weapon's category, edi = 0.  The frame is the one the
// function built (esi, edi, ebp, ebx, ecx pushed, then the return address and the player argument); its
// own epilogue (pop x5; ret 4) returns here.
uint32_t g_subCat;
__declspec(naked) void CallSubTailAsm() {
    __asm {
        push ebx
        push esi
        push edi
        push ebp
        push g_a            // the argument
        call go_in          // the return address
        pop ebp
        pop edi
        pop esi
        pop ebx
        ret
    go_in:
        push ecx
        push ebx
        push ebp
        push edi
        push esi
        mov ebp, g_a
        mov ebx, g_subCat
        xor edi, edi
        xor esi, esi
        jmp dword ptr [g_fn]
    }
}

// ---- sResource, as initMainWpnMotion uses it: create (+0x30), release (+0x38) ----------------------------
struct FakeRes {
    uint8_t pad[0x48];
    int32_t refs;
    const char* path;
    uint8_t rest[0x40];
};
FakeRes g_res[64];
int g_resUsed, g_released;
uint8_t* g_sres;

FakeRes* __stdcall CreateC(uint32_t, const char* path, uint32_t) {
    FakeRes* r = &g_res[g_resUsed++ % 64];
    memset(r, 0, sizeof *r);
    r->refs = 1;
    r->path = path;
    return r;
}
__declspec(naked) void CreateStub() {  // thiscall (dti, path, flags), ret 0xC
    __asm {
        push dword ptr [esp + 12]
        push dword ptr [esp + 12]
        push dword ptr [esp + 12]
        call CreateC
        ret 12
    }
}
void __stdcall ReleaseC(FakeRes* r) {
    if (r) r->refs--;
    g_released++;
}
__declspec(naked) void ReleaseStub() {  // thiscall (resource), ret 4
    __asm {
        push dword ptr [esp + 4]
        call ReleaseC
        ret 4
    }
}

void SetupResources() {
    g_sres = Alloc(0x1000);
    uint32_t* vt = (uint32_t*)Alloc(0x1000);
    for (int i = 0; i < 0x40; i++) vt[i] = (uint32_t)(uintptr_t)&RetTrue4;
    vt[0x30 / 4] = (uint32_t)(uintptr_t)&CreateStub;
    vt[0x38 / 4] = (uint32_t)(uintptr_t)&ReleaseStub;
    At<uint32_t>((uintptr_t)g_sres, 0) = (uint32_t)(uintptr_t)vt;
    At<uint32_t>(S_RESOURCE, 0) = (uint32_t)(uintptr_t)g_sres;
    At<uint8_t>(RES_LOCKING, 0) = 0;
}

// ---- the skill-archive loader's slots: +0x14 of its vtable gives slot N; the tag is in esi then ----------
uint32_t g_tags[16];
uint8_t* g_loader;
__declspec(naked) void SlotStub() {  // thiscall (slot), ret 4; records esi (the archive tag) for slots 6..11
    __asm {
        mov eax, dword ptr [esp + 4]
        sub eax, 6
        cmp eax, 6
        jae skip
        mov dword ptr [g_tags + eax * 4], esi
    skip:
        xor eax, eax
        ret 4
    }
}
void SetupLoader() {
    g_loader = Alloc(0x1000);
    uint32_t* vt = (uint32_t*)Alloc(0x1000);
    for (int i = 0; i < 0x40; i++) vt[i] = (uint32_t)(uintptr_t)&RetTrue4;
    vt[0x14 / 4] = (uint32_t)(uintptr_t)&SlotStub;
    At<uint32_t>((uintptr_t)g_loader, 0) = (uint32_t)(uintptr_t)vt;
}

// ======================================================================================================
void TestSites() {
    printf("every patched compare, from its first byte, with each value (party member / anyone else)\n");
    for (const Site& s : SITES) {
        const bool ui = s.kind == UI;
        const int values[] = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12};
        std::string wrong;
        int ran = 0, kept = 0, total = 0;
        for (int owner = 0; owner < (ui ? 1 : 2); owner++) {
            uintptr_t info = owner == 0 ? g_arisen : g_stranger;
            for (int v : values) {
                for (int r = 0; r < 8; r++) g_in[r] = 0x11110000u + 0x1111u * r;
                g_stack38 = 0x5A5A5A5A;
                static uint8_t player[0x4000];
                At<uint32_t>((uintptr_t)player, PL_INFO) = (uint32_t)info;
                switch (s.kind) {
                    case WPN_ECX: g_in[1] = (uint32_t)info; break;
                    case WPN_EDI: g_in[7] = (uint32_t)info; break;
                    case WPN_STACK: g_stack38 = (uint32_t)info; break;
                    case WPN_PLAYER: g_in[5] = (uint32_t)(uintptr_t)player; break;
                    default: break;
                }
                g_in[s.reg] = (uint32_t)v;
                Hook a{}, b{};
                Place(a, s.wand, CaptureA);
                if (s.other) Place(b, s.other, CaptureB);
                g_target = (uint32_t)s.at;
                Enter();
                if (s.other) Remove(b);
                Remove(a);
                bool wand;
                if (s.other) wand = g_which == 1;
                else wand = g_out[2] == 6;  // the is-equipped count: edx = 6 or 3
                bool want = ui ? (v == 3 || v == 4 || (g_six && v == 1))
                               : (v == 5 || v == 6 || (g_six && owner == 0 && (v == 3 || v == 7)));
                total++;
                if (g_which == 0) wrong += F(" %d:none", v);
                else if (wand != want) wrong += F(" %s%d:%s", owner ? "other " : "", v, wand ? "staff" : "other");
                else ran++;
                if (RegsKept(s.other ? -1 : 2)) kept++;
            }
        }
        Check(ran == total, F("0x%08X %-30s staff path for %s%s", (unsigned)s.at, s.name,
                              ui ? (g_six ? "3, 4 and 1" : "3 and 4") : (g_six ? "5, 6; 3 and 7 for the party" : "5 and 6"),
                              wrong.empty() ? "" : (" -- wrong:" + wrong).c_str()));
        Check(kept == total, F("0x%08X   registers and stack come back unchanged (%d of %d runs)", (unsigned)s.at, kept, total));
    }

    printf("checkCmdType's dispatch by entry type (0x00ABA89B: jmp [eax*4 + 0x00ABA9D0])\n");
    struct D {
        const char* what;
        uint32_t type;
        uintptr_t entry;
        uintptr_t info;
        bool type5;
    } cases[] = {
        {"a longsword skill (type 3), the Arisen", 3, GSWORD_SKILLS + 0x20 * 3, g_arisen, g_six},
        {"a longsword skill (type 3), a pawn", 3, GSWORD_SKILLS + 0x20 * 9, g_pawn + CMC_INFO_SIZE, g_six},
        {"a longsword skill (type 3), anyone else", 3, GSWORD_SKILLS, g_stranger, false},
        {"a sword skill (type 3), the Arisen", 3, SWORD_SKILLS, g_arisen, false},
        {"the end of the longsword table, the Arisen", 3, GSWORD_SKILLS + 0x20 * 10, g_arisen, false},
        {"a staff skill (type 5), anyone", 5, 0x014F7958, g_stranger, true},
    };
    static uint8_t player[0x4000];
    for (const D& d : cases) {
        for (int r = 0; r < 8; r++) g_in[r] = 0x22220000u + 0x1111u * r;
        At<uint32_t>((uintptr_t)player, PL_INFO) = (uint32_t)d.info;
        g_in[0] = d.type - 1;
        g_in[5] = (uint32_t)d.entry;
        g_in[6] = (uint32_t)(uintptr_t)player;
        Hook a{}, b{};
        Place(a, CMD_TYPE5, CaptureA);
        Place(b, CMD_TYPE3, CaptureB);
        g_target = CMD_DISPATCH;
        Enter();
        Remove(b);
        Remove(a);
        bool five = g_which == 1;
        Check(g_which != 0 && five == d.type5 && RegsKept(-1),
              F("%s: runs the type-%d code%s", d.what, d.type5 ? 5 : 3, RegsKept(-1) ? "" : " (registers changed!)"));
    }
}

void TestPalettes() {
    const int gsRow[6] = {100, 101, 102, 103, 104, 105};
    printf("setSkillFromEquipWeapon (0x00780350): the main weapon's six slots fill both palettes\n");
    ResetInfo(g_arisen, 0, 7, GSWORD, NONE);
    Row(g_arisen, 3, gsRow);
    CallRegs(SET_SKILL_FROM_WEAPON, (uint32_t)g_arisen, 0, 0);
    Check(Pal(g_arisen, PI_MAIN_SKILL) == "{100, 101, 102}" && Pal(g_arisen, PI_SUB_SKILL) == "{103, 104, 105}",
          "longsword row {100..105} -> main " + Pal(g_arisen, PI_MAIN_SKILL) + ", secondary " + Pal(g_arisen, PI_SUB_SKILL));

    printf("removeIllegalCstmSkill (0x00780590): what stays in the secondary palette\n");
    struct C {
        const char* what;
        uintptr_t info;
        uint32_t job, main, sub;
        int m[3], s[3];
        const char* sixWant;
        const char* vanillaWant;
    } cases[] = {
        {"Warrior, longsword, the Arisen", g_arisen, 7, GSWORD, NONE, {100, 101, 102}, {103, 104, 105},
         "{103, 104, 105}", "{-1, -1, -1}"},
        {"Warrior, warhammer, a pawn", g_pawn, 7, HAMMER, NONE, {100, 101, 102}, {107, 108, 109},
         "{107, 108, 109}", "{-1, -1, -1}"},
        {"Warrior, longsword, anyone else", g_stranger, 7, GSWORD, NONE, {100, 101, 102}, {103, 104, 105},
         "{-1, -1, -1}", "{-1, -1, -1}"},
        {"Warrior with a dagger skill in slot 5 (not a longsword skill)", g_arisen, 7, GSWORD, NONE, {100, 101, 102},
         {103, 150, 105}, "{103, -1, 105}", "{-1, -1, -1}"},
        {"Mage, staff (vanilla keeps six)", g_arisen, 3, WAND, NONE, {210, 211, 212}, {213, 214, 215},
         "{213, 214, 215}", "{213, 214, 215}"},
        {"Fighter, sword and shield", g_arisen, 1, SWORD, SHIELD, {40, 41, 42}, {270, 271, 272}, "{270, 271, 272}",
         "{270, 271, 272}"},
    };
    for (const C& c : cases) {
        ResetInfo(c.info, c.info == g_pawn ? 1 : 0, c.job, c.main, c.sub);
        Palettes(c.info, c.m, c.s);
        CallRegs(REMOVE_ILLEGAL, 0, (uint32_t)c.info, 0);
        std::string got = Pal(c.info, PI_SUB_SKILL), want = g_six ? c.sixWant : c.vanillaWant;
        Check(got == want && Pal(c.info, PI_MAIN_SKILL) == F("{%d, %d, %d}", c.m[0], c.m[1], c.m[2]),
              F("%s: secondary %s (want %s), main kept", c.what, got.c_str(), want.c_str()));
    }

    printf("removeJobMismatchCstmSkill (0x00780840): the Warrior's rows are never cut\n");
    ResetInfo(g_arisen, 0, 7, GSWORD, NONE);
    Row(g_arisen, 3, gsRow);
    Row(g_arisen, 7, gsRow);
    CallRegs(REMOVE_JOB_MISMATCH, 0, 0, (uint32_t)g_arisen);
    bool whole = true;
    for (int c : {3, 7})
        for (int i = 0; i < 6; i++) whole &= At<int32_t>(g_arisen, PI_ROWS + 0x18 * c + 4 * i) == gsRow[i];
    Check(whole, "Warrior: the longsword and warhammer rows keep all six skills");
    const int mkRow[6] = {210, 211, 212, 213, 214, 215};
    ResetInfo(g_arisen, 0, 4, WAND, NONE);
    Row(g_arisen, 5, mkRow);
    CallRegs(REMOVE_JOB_MISMATCH, 0, 0, (uint32_t)g_arisen);
    Check(At<int32_t>(g_arisen, PI_ROWS + 0x18 * 5 + 4 * 3) == -1 && At<int32_t>(g_arisen, PI_ROWS + 0x18 * 5) == 210,
          "Mystic Knight with a staff: the game itself empties staff slots 4-6 (vanilla, for comparison)");
}

// A party Warrior (or the stranger) wielding a longsword, both palettes set, ready for getNextAction.
uint8_t* g_player;
uint8_t* g_checkTbl;
void ReadyPlayer(uintptr_t info, uint8_t flags, uint32_t main, const uint32_t* mainActs, const uint32_t* subActs) {
    memset(g_player, 0, 0x6000);
    At<uint32_t>((uintptr_t)g_player, 0) = (uint32_t)(uintptr_t)g_vtable;
    At<uint8_t>((uintptr_t)g_player, PL_FLAGS) = flags;
    At<uint32_t>((uintptr_t)g_player, PL_ATTR) = 1;           // on the ground
    for (int t = 0; t < 12; t++) At<uint32_t>((uintptr_t)g_player, PL_CANCEL + (t + 1) * 0x1C) = 1;  // idle: every cancel class open
    At<uint32_t>((uintptr_t)g_player, PL_ACTION) = 0;
    At<uint32_t>((uintptr_t)g_player, PL_JOB) = At<uint32_t>(info, PI_JOB);
    At<uint32_t>((uintptr_t)g_player, PL_MAIN_ID) = main;
    At<uint32_t>((uintptr_t)g_player, PL_SUB_ID) = NONE;
    At<uint32_t>((uintptr_t)g_player, PL_INFO) = (uint32_t)info;
    for (int i = 0; i < 3; i++) {
        At<uint32_t>((uintptr_t)g_player, PL_MAIN_PAL + 4 * i) = mainActs[i];
        At<uint32_t>((uintptr_t)g_player, PL_SUB_PAL + 4 * i) = subActs[i];
    }
    for (int i = 0; i < 6; i++) At<int32_t>((uintptr_t)g_player, PL_IN_USE + 4 * i) = -1;
    memset(g_checkTbl, 0, 0x40);
    At<uint32_t>((uintptr_t)g_checkTbl, 4) = (uint32_t)(uintptr_t)g_player;
}
uint32_t NextAction(uint32_t trigger, uint32_t held) {
    At<uint32_t>((uintptr_t)g_player, PL_TRIGGER) = trigger;
    At<uint32_t>((uintptr_t)g_player, PL_HELD) = held;
    static uint32_t result[2];
    result[0] = result[1] = 0xDEADBEEF;
    g_fn = GET_NEXT_ACTION, g_a = (uint32_t)(uintptr_t)g_checkTbl, g_c = (uint32_t)(uintptr_t)result;
    CallNextActionAsm();
    return result[0];
}

void TestCombat() {
    printf("getNextAction (0x00ABA690): what the buttons start (the real action tables and checks)\n");
    g_player = Alloc(0x6000);
    g_checkTbl = Alloc(0x100);
    At<uint32_t>(g_sys, OFF_SYS_FLAGS) = 0;
    const uint32_t mainActs[3] = {ACT_GSWORD_SKILL0, ACT_GSWORD_SKILL0 + 1, ACT_GSWORD_SKILL0 + 2};
    const uint32_t subActs[3] = {ACT_GSWORD_SKILL0 + 3, ACT_GSWORD_SKILL0 + 4, ACT_GSWORD_SKILL0 + 5};
    struct Who {
        const char* name;
        uintptr_t info;
        uint8_t flags;  // uPlayerBase +0x206D: 2 the Arisen, 4 a pawn
        bool party;
    } whos[] = {{"the Arisen", g_arisen, 2, true}, {"a pawn", g_pawn, 4, true}, {"anyone else", g_stranger, 0, false}};
    for (const Who& w : whos) {
        ResetInfo(w.info, w.info == g_pawn ? 1 : 0, 7, GSWORD, NONE);
        for (int s = 100; s <= 105; s++) SetBit(w.info, s);
        ReadyPlayer(w.info, w.flags, GSWORD, mainActs, subActs);
        struct B {
            const char* name;
            uint32_t trigger, held, skillAct;  // skillAct: the skill it starts with six slots
            bool fromSub;
        } buttons[] = {
            {"main-weapon skill button + Square", 0x1 | 0x10, HELD_MAIN, ACT_GSWORD_SKILL0, false},
            {"main-weapon skill button + Circle", 0x1000 | 0x40, HELD_MAIN, ACT_GSWORD_SKILL0 + 2, false},
            {"secondary-weapon skill button + Square", 0x1 | 0x80, HELD_SUB, ACT_GSWORD_SKILL0 + 3, true},
            {"secondary-weapon skill button + Triangle", 0x2 | 0x100, HELD_SUB, ACT_GSWORD_SKILL0 + 4, true},
            {"secondary-weapon skill button + Circle", 0x1000 | 0x200, HELD_SUB, ACT_GSWORD_SKILL0 + 5, true},
        };
        for (const B& b : buttons) {
            uint32_t got = NextAction(b.trigger, b.held);
            bool skill = got == b.skillAct;
            bool want = !b.fromSub || (g_six && w.party);
            bool isSkill = got >= ACT_GSWORD_SKILL0 && got <= ACT_GSWORD_SKILL0 + 9;
            Check(want ? skill : !isSkill,
                  F("Warrior (%s), %s: action 0x%08X %s", w.name, b.name, got,
                    want ? F("(skill %d)", 100 + (int)(b.skillAct - ACT_GSWORD_SKILL0)).c_str()
                         : "(no longsword skill: the game's own attack)"));
        }
    }
    // A Mage's staff: its secondary palette works without the plugin; the plugin leaves it alone.
    ResetInfo(g_arisen, 0, 3, WAND, NONE);
    for (int s = 210; s <= 215; s++) SetBit(g_arisen, s);
    const uint32_t wm[3] = {ACT_WAND_SKILL0, ACT_WAND_SKILL0 + 1, ACT_WAND_SKILL0 + 2};
    const uint32_t ws[3] = {ACT_WAND_SKILL0 + 3, ACT_WAND_SKILL0 + 4, ACT_WAND_SKILL0 + 5};
    ReadyPlayer(g_arisen, 2, WAND, wm, ws);
    uint32_t got = NextAction(0x1 | 0x80, HELD_SUB);
    Check(got == ACT_WAND_SKILL0 + 3, F("Mage (staff), secondary-weapon skill button + Square: action 0x%08X (spell 213)", got));
}

void TestMotions() {
    printf("initMainWpnMotion (0x00B5B400) and initSubWpnMotion (from 0x00B5B8EA): skill motion lists\n");
    SetupResources();
    static const char* names[30];
    static char text[30][24];
    for (int i = 0; i < 30; i++) {
        _snprintf_s(text[i], sizeof text[i], _TRUNCATE, "skill motion %d", i);
        names[i] = text[i];
    }
    // The custom-skill motion tables by weapon category: records of {?, path}.
    static uint32_t gs[10][2], wand[30][2], weaponMotions[13], cstm[13];
    for (int i = 0; i < 10; i++) gs[i][0] = 0, gs[i][1] = (uint32_t)(uintptr_t)names[i];
    for (int i = 0; i < 30; i++) wand[i][0] = 0, wand[i][1] = (uint32_t)(uintptr_t)names[i];
    cstm[3] = cstm[7] = (uint32_t)(uintptr_t)gs;
    cstm[5] = cstm[6] = (uint32_t)(uintptr_t)wand;
    struct M {
        const char* what;
        uintptr_t info;
        uint32_t job, main;
        int m[3], s[3];
        int base;       // the category's first custom skill
        bool sixLoads;  // all six load and stay
    } cases[] = {
        {"Warrior, longsword, the Arisen", g_arisen, 7, GSWORD, {100, 101, 102}, {103, 104, 105}, 100, g_six},
        {"Warrior, warhammer, a pawn", g_pawn, 7, HAMMER, {100, 101, 102}, {107, 108, 109}, 100, g_six},
        {"Warrior, longsword, anyone else", g_stranger, 7, GSWORD, {100, 101, 102}, {103, 104, 105}, 100, false},
        {"Mage, staff", g_arisen, 3, WAND, {210, 211, 212}, {213, 214, 215}, 210, true},
    };
    uint8_t* player = Alloc(0x6000);
    for (const M& c : cases) {
        ResetInfo(c.info, c.info == g_pawn ? 1 : 0, c.job, c.main, NONE);
        Palettes(c.info, c.m, c.s);
        memset(player, 0, 0x6000);
        At<uint32_t>((uintptr_t)player, PL_INFO) = (uint32_t)c.info;
        At<uint32_t>((uintptr_t)player, PL_MAIN_ID) = c.main;
        At<uint32_t>((uintptr_t)player, PL_SUB_ID) = NONE;
        At<uint32_t>((uintptr_t)player, PL_JOB) = c.job;
        At<uint32_t>((uintptr_t)player, PL_WPN_MOT) = (uint32_t)(uintptr_t)weaponMotions;
        At<uint32_t>((uintptr_t)player, PL_CSTM_MOT) = (uint32_t)(uintptr_t)cstm;
        g_resUsed = g_released = 0;
        g_fn = INIT_MAIN_MOTION, g_a = (uint32_t)(uintptr_t)player;
        CallStd1Asm();
        auto slots = [&]() {
            std::string s;
            for (int i = 0; i < 6; i++) {
                FakeRes* r = At<FakeRes*>((uintptr_t)player, PL_SKILL_LISTS + 4 * i);
                s += r ? F("%s%d", i ? " " : "", (int)(strrchr(r->path, ' ') ? atoi(strrchr(r->path, ' ') + 1) : -9))
                       : F("%s-", i ? " " : "");
            }
            return s;
        };
        std::string all = F("%d %d %d %d %d %d", c.m[0] - c.base, c.m[1] - c.base, c.m[2] - c.base, c.s[0] - c.base,
                            c.s[1] - c.base, c.s[2] - c.base);
        std::string mainOnly = F("%d %d %d - - -", c.m[0] - c.base, c.m[1] - c.base, c.m[2] - c.base);
        std::string after = slots();
        Check(after == (c.sixLoads ? all : mainOnly),
              F("%s: loads motion lists [%s] (want [%s])", c.what, after.c_str(), (c.sixLoads ? all : mainOnly).c_str()));
        g_subCat = (uint32_t)(c.main >> 24);
        g_fn = INIT_SUB_MOTION_TAIL, g_a = (uint32_t)(uintptr_t)player;
        CallSubTailAsm();
        std::string kept = slots();
        Check(kept == (c.sixLoads ? all : mainOnly),
              F("%s: after initSubWpnMotion [%s] (want [%s])", c.what, kept.c_str(), (c.sixLoads ? all : mainOnly).c_str()));
    }
}

void TestArchives() {
    printf("the skill-archive loader (0x00789010, skills only): the archive each slot asks for\n");
    SetupLoader();
    struct A {
        const char* what;
        uintptr_t info;
        uint32_t job, main;
        int m[3], s[3];
        uint32_t firstTag;  // the category's first skill archive tag
        int base;
        bool six;
    } cases[] = {
        {"Warrior, longsword, the Arisen", g_arisen, 7, GSWORD, {100, 101, 102}, {103, 104, 105}, 0x41E3, 100, g_six},
        {"Warrior, longsword, anyone else", g_stranger, 7, GSWORD, {100, 101, 102}, {103, 104, 105}, 0x41E3, 100, false},
        {"Mage, staff", g_arisen, 3, WAND, {210, 211, 212}, {213, 214, 215}, 0x4201, 210, true},
    };
    for (const A& c : cases) {
        ResetInfo(c.info, 0, c.job, c.main, NONE);
        Palettes(c.info, c.m, c.s);
        memset(g_tags, 0, sizeof g_tags);
        g_fn = ARC_LOADER, g_a = (uint32_t)(uintptr_t)g_loader, g_b = (uint32_t)c.info, g_c = 8;
        CallStd3Asm();
        std::string got, want;
        for (int i = 0; i < 6; i++) {
            int skill = i < 3 ? c.m[i] : c.s[i - 3];
            uint32_t t = (i < 3 || c.six) ? c.firstTag + (uint32_t)(skill - c.base) : 0x848A;
            got += F("%s%04X", i ? " " : "", g_tags[i]);
            want += F("%s%04X", i ? " " : "", t);
        }
        Check(got == want, F("%s: archive tags [%s] (want [%s]; 848A = none)", c.what, got.c_str(), want.c_str()));
    }
}

void TestMenu() {
    printf("the skill menu: how many slots it counts\n");
    ResetInfo(g_arisen, 0, 7, GSWORD, NONE);
    const int gsRow[6] = {100, 101, 102, 103, 104, 105};
    Row(g_arisen, 3, gsRow);
    Row(g_arisen, 7, gsRow);
    uint8_t* menu = Alloc(0x1000);
    At<uint32_t>((uintptr_t)menu, 0x270) = 0;  // the Arisen's tab
    struct E {
        const char* what;
        uint32_t category, skill;
        bool six, vanilla;
    } eq[] = {
        {"longsword skill 104 in slot 5, category 1 (Warrior)", 1, 104, true, false},
        {"longsword skill 101 in slot 2, category 1 (Warrior)", 1, 101, true, true},
        {"longsword skill 104, category 0 (sword/mace)", 0, 104, false, false},
    };
    for (const E& e : eq) {
        CallRegs(IS_EQUIPPED, (uint32_t)(uintptr_t)menu, e.category, e.skill);
        bool got = (g_ret & 0xFF) != 0, want = g_six ? e.six : e.vanilla;
        Check(got == want, F("is it equipped (0x006FA980): %s -> %s", e.what, got ? "yes" : "no"));
    }
    // makeEquipLineup(menu, false): the category control (+0x298), the slot control (+0x29C) gets its count.
    uint8_t* cat = Alloc(0x1000);
    uint8_t* slots = Alloc(0x1000);
    memset(menu, 0, 0x1000);
    At<uint32_t>((uintptr_t)menu, 0) = (uint32_t)(uintptr_t)g_vtable;
    At<uint32_t>((uintptr_t)menu, 0x298) = (uint32_t)(uintptr_t)cat;
    At<uint32_t>((uintptr_t)menu, 0x29C) = (uint32_t)(uintptr_t)slots;
    struct L {
        uint32_t category;
        uint32_t six, vanilla;
    } ls[] = {{1, 6, 3}, {3, 6, 6}, {4, 6, 6}, {0, 3, 3}, {9, 3, 3}};
    for (const L& l : ls) {
        memset(cat, 0, 0x100);
        memset(slots, 0, 0x100);
        At<uint32_t>((uintptr_t)cat, 0x9C) = l.category;
        g_fn = MAKE_EQUIP_LINEUP, g_a = (uint32_t)(uintptr_t)menu, g_b = 0;
        CallStd2Asm();
        uint32_t n = At<uint32_t>((uintptr_t)slots, 0xA8);
        Check(n == (g_six ? l.six : l.vanilla), F("makeEquipLineup (0x006F9C50): category %u -> %u slots", l.category, n));
    }
}
}  // namespace

static int Run(int argc, wchar_t** argv);

// Called by ssw_stub.exe.  Never returns: the stub's code is gone once DDDA.exe is mapped.
extern "C" __declspec(dllexport) void HarnessMain() {
    int argc = 0;
    wchar_t** argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    int code = Run(argc, argv);
    fflush(stdout);
    ExitProcess((UINT)code);
}

static int Run(int argc, wchar_t** argv) {
    if (argc < 4) {
        printf("usage: ssw_stub <DDDA.exe> <six_skill_warrior.asi> <six|off|bogus|tampered>\n");
        return 1;
    }
    const std::wstring profile = argv[3];
    if (!MapImage(argv[1])) return 2;
    const std::string vanilla(sizeof SITES / sizeof SITES[0] + 1, '.');
    Check(SiteState() == vanilla, "the mapped game code has all 24 compares and the type-3 table entry as shipped");
    if (profile == L"tampered") *(uint8_t*)0x007039D7 = 0x05;  // the slot cursor's second compare: cmp eax, 5

    SetEnvironmentVariableW(L"RIFTSTONE_SIX_SKILL_WARRIOR_HARNESS", L"1");
    HMODULE plugin = LoadLibraryW(argv[2]);
    printf("plugin (profile %S)\n", profile.c_str());
    Check(plugin != nullptr, "the plugin loads");
    g_six = profile == L"six";
    const std::string patched = std::string(sizeof SITES / sizeof SITES[0] + 1, 'j');
    const std::string want = g_six ? patched : vanilla;
    Check(SiteState() == want, "patched exactly " + std::string(g_six ? "every site" : "nothing") + ": " + SiteState());
    if (g_six) {
        wchar_t copy[MAX_PATH];
        wcscpy_s(copy, argv[2]);
        wchar_t* dot = wcsrchr(copy, L'.');
        if (dot) *dot = 0;
        wcscat_s(copy, L"_copy.asi");
        if (CopyFileW(argv[2], copy, FALSE)) {
            HMODULE second = LoadLibraryW(copy);
            Check(second != nullptr && SiteState() == want, "a second copy refuses the patched code and changes nothing");
        }
    }
    if (profile == L"tampered") *(uint8_t*)0x007039D7 = 0x04;
    if (g_fails) return 1;

    SetupWorld();
    TestSites();
    TestPalettes();
    TestCombat();
    TestMotions();
    TestArchives();
    TestMenu();

    printf(g_fails ? "\n%d of %d check(s) FAILED\n" : "\nall %d checks passed\n", g_fails ? g_fails : g_checks, g_checks);
    return g_fails ? 1 : 0;
}

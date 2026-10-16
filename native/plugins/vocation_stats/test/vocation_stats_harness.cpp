// vocation_stats_harness_core -- runs vocation_stats inside the real game code, without the game.
//
//   vocation_stats_stub.exe <DDDA.exe> <vocation_stats.asi> <profile> <on 0|1> <who: party|arisen|pawn>
//                           [<tables.txt>]
//
// Maps DDDA.exe's image at its fixed base (0x00400000) over the stub's image, loads the plugin (which
// verifies and patches the mapped code), then runs an inn's rest the way uInn's state 0x00736740 does:
// the call at 0x00736757 (the inn pushed) or at 0x0073675E (the inn in esi), into whatever the call now
// targets.  The rest routines themselves (0x00737580, 0x007377D0) and three helpers the level-up calls
// (0x00782040, the HP setter 0x00888DA0, the parameter update 0x00B807F0) are stand-ins that record
// what reaches them; setLevel (0x00B5CCB0), the level-up (0x00B5BD20) and getNextExp (0x00B5C080) are
// the game's own code.  The party is fake: sPlayerManager with the experience table, the nine level
// tables and four uPlayers (the Arisen, the main pawn, two hired pawns), each with its cPlayerInfo in a
// fake sGameSys.
//
// For every case the whole fake world after the rest must be the world before with only the
// recalculated characters' six stats changed, to the numbers this file's model gives (the base row,
// then each level's band, HP capped at 9999); the rest routine must run once, with every register,
// flag and xmm0-7 as the call site left them and the new HP already in place.  A character the level-up
// would refuse must be left alone (the game's setLevel would loop for ever on it).
//
// <tables.txt> holds the game's tables (read by run_tests.py from the player's archives, never stored
// here): "lv JOB LEVEL HP STAMINA ATTACK DEFENCE MATTACK MDEFENCE" and "exp LEVEL RATE" lines.  Without it
// a stand-in table of the same shape is used.  The harness prints "stats JOB LEVEL ..." lines, the game
// code's numbers, for run_tests.py to hold against its own model of the archive tables.
//
// Prints "pass"/"FAIL" lines and exits 0 when everything passed, 1 on a failure, 2 when the image
// cannot be mapped here (reported as a skip by run_tests.py).
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shellapi.h>

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <string>
#include <vector>

namespace {
constexpr uintptr_t BASE = 0x00400000;
constexpr uintptr_t SITE_A = 0x00736757, REST_A = 0x00737580;   // call 0x00737580 (the inn pushed)
constexpr uintptr_t SITE_B = 0x0073675E, REST_B = 0x007377D0;   // call 0x007377D0 (the inn in esi)
constexpr uintptr_t MAX_HP_OF = 0x00782040;        // (cPlayerInfo*) -> xmm0, ret 4
constexpr uintptr_t SET_HP = 0x00888DA0;           // eax = the uPlayer, (hp, 0, 1), ret 0xC
constexpr uintptr_t UPDATE = 0x00B807F0;           // eax = the uPlayer, ret
constexpr uintptr_t NEXT_EXP = 0x00B5C080;         // getNextExp(level), stdcall
constexpr uintptr_t GAME_SYS = 0x018FA4BC;         // sGameSys::mpInstance
constexpr uintptr_t PLAYERS = 0x018FA4EC;          // sPlayerManager::mpInstance
constexpr uintptr_t MARKS = 0x018D0CC4;            // the object whose +0x5A74 bits mark levels 100 and 200
constexpr uintptr_t TAMPER = 0x00B5BD4F;           // the 09 of the level-up's first refusal: the tampered profile
constexpr uint32_t SYS_SIZE = 0xC0000, PLAYER_SIZE = 0x6000, MARK_SIZE = 0x6000, MGR_SIZE = 0x1000;
constexpr uint32_t SLOT[4] = {0xA76D0, 0xA7EC0, 0xA9520, 0xAAB80};   // each uPlayer's cPlayerInfo in sGameSys
constexpr uint32_t MGR_SLOT[4] = {0x99C, 0x9A0, 0x9A4, 0x9A8};
constexpr uint32_t P_FLAGS = 0x206D, P_HELD = 0x31EA, P_STATE = 0x273C, P_INFO = 0x3DEC, P_HPBAR = 0x1D0C;
constexpr uint32_t I_INDEX = 8, I_JOB = 0x10, I_EXP = 0x2C4, I_COST = 0x2C8, I_LEVEL = 0x700;
constexpr uint32_t STAT_OFF[6] = {0x2A0, 0x2B0, 0x2B4, 0x2B8, 0x2BC, 0x2C0};   // HP, stamina, attack, defence, m.
constexpr uint32_t HP_CAP = 9999;

int g_fails = 0;
void Check(bool ok, const std::string& what) {
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

// ---- the tables ---------------------------------------------------------------------------------
#pragma pack(push, 1)
struct LvRow {       // an rPlLevel row as setLevel and the level-up read it: +4 the band's first level, +6.. growth
    uint32_t vtable;
    uint16_t level, hp, stamina, attack, defence, mattack, mdefence, next;
    uint32_t pad;
};
#pragma pack(pop)
struct ExpRow {      // a cPlExp row as getNextExp reads it: +4 the band's rate, +8 its level
    uint32_t vtable;
    float rate;
    uint32_t level;
};
struct Stats {
    uint32_t v[6];   // HP, stamina, attack, defence, magick attack, magick defence
};

std::vector<LvRow> g_lv[10];
std::vector<ExpRow> g_exp;

bool ReadTables(const wchar_t* path) {
    FILE* f = nullptr;
    if (_wfopen_s(&f, path, L"r") || !f) return false;
    char line[256];
    while (fgets(line, sizeof line, f)) {
        unsigned j, lv, a[7];
        float rate;
        if (sscanf_s(line, "lv %u %u %u %u %u %u %u %u", &j, &lv, &a[0], &a[1], &a[2], &a[3], &a[4], &a[5]) == 8 &&
            j >= 1 && j <= 9) {
            LvRow r = {};
            r.level = (uint16_t)lv;
            r.hp = (uint16_t)a[0], r.stamina = (uint16_t)a[1], r.attack = (uint16_t)a[2], r.defence = (uint16_t)a[3];
            r.mattack = (uint16_t)a[4], r.mdefence = (uint16_t)a[5];
            g_lv[j].push_back(r);
        } else if (sscanf_s(line, "exp %u %f", &lv, &rate) == 2) {
            g_exp.push_back(ExpRow{0, rate, lv});
        }
    }
    fclose(f);
    for (int j = 1; j <= 9; j++)
        if (g_lv[j].size() < 3) return false;
    return g_exp.size() >= 4;
}

void StandInTables() {
    for (int j = 1; j <= 9; j++) {
        g_lv[j] = {{0, 0, 0, 0, 0, 0, 0, 0, 0, 0},
                   {0, 1, (uint16_t)(400 + 10 * j), 0, (uint16_t)(60 + j), (uint16_t)(70 - j), 60, 60, 0, 0},
                   {0, 2, (uint16_t)(20 + j), 20, 3, 3, 2, (uint16_t)(j % 3), 0, 0},
                   {0, 11, 25, (uint16_t)(10 + j), (uint16_t)(j & 3), 3, 2, 2, 0, 0},
                   {0, 101, 10, 10, 1, 1, (uint16_t)(j % 2), 1, 0, 0},
                   {0, 201, 0, 0, 0, 0, 0, 0, 0, 0}};
    }
    g_exp = {{0, 1.0f, 300}, {0, 1.0f, 250}, {0, 1.5f, 2}, {0, 4.0f, 20}, {0, 9.0f, 90}, {0, 0.0f, 200}};
}

// The stats a character of vocation `job` has at `level` when it gained every level in it: the base row
// (level 1), then the row whose band holds each level from 2, HP capped at 9999 after each.
Stats Model(uint32_t job, uint32_t level) {
    const std::vector<LvRow>& t = g_lv[job];
    Stats s = {{t[1].hp, t[1].stamina, t[1].attack, t[1].defence, t[1].mattack, t[1].mdefence}};
    for (uint32_t lv = 2; lv <= level; lv++) {
        size_t band = 1;
        for (size_t i = 1; i < t.size(); i++)
            if (t[i].level <= lv) band = i;
        const LvRow& r = t[band];
        s.v[0] += r.hp;
        if (s.v[0] > HP_CAP) s.v[0] = HP_CAP;
        s.v[1] += r.stamina, s.v[2] += r.attack, s.v[3] += r.defence, s.v[4] += r.mattack, s.v[5] += r.mdefence;
    }
    return s;
}

// ---- the fake world ------------------------------------------------------------------------------
struct LvTable {
    uint8_t head[0x80];      // +0x64 the row count, +0x70 the row pointers
};
struct World {
    uint8_t* sys;
    uint8_t* marks;
    uint8_t* players[4];
    uint8_t mgr[MGR_SIZE];
    LvTable lv[10];
    LvTable exp;
    LvRow lvRows[10][64];
    LvRow* lvPtrs[10][64];
    ExpRow expRows[40];
    ExpRow* expPtrs[40];
    uint32_t vtable[0x80];
};
World* w;

__declspec(naked) void MaxHpVirtual() {   // uPlayer's virtual at +0x148 (thiscall, no arguments): the max HP on st0
    __asm {
        mov eax, dword ptr [ecx + 0x3DEC]
        fld dword ptr [eax + 0x2A0]
        ret
    }
}

void SetupWorld() {
    w = (World*)VirtualAlloc(nullptr, sizeof(World), MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    w->sys = (uint8_t*)VirtualAlloc(nullptr, SYS_SIZE, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    w->marks = (uint8_t*)VirtualAlloc(nullptr, MARK_SIZE, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    for (uint8_t*& p : w->players) p = (uint8_t*)VirtualAlloc(nullptr, PLAYER_SIZE, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    for (uint32_t& s : w->vtable) s = 0;
    w->vtable[0x148 / 4] = (uint32_t)(uintptr_t)MaxHpVirtual;
    for (int j = 1; j <= 9; j++) {
        const size_t n = g_lv[j].size() < 64 ? g_lv[j].size() : 64;
        for (size_t i = 0; i < n; i++) {
            w->lvRows[j][i] = g_lv[j][i];
            w->lvPtrs[j][i] = &w->lvRows[j][i];
        }
        *(uint32_t*)(w->lv[j].head + 0x64) = (uint32_t)n;
        *(uint32_t*)(w->lv[j].head + 0x70) = (uint32_t)(uintptr_t)w->lvPtrs[j];
        *(uint32_t*)(w->mgr + 0x86C + 4 * j) = (uint32_t)(uintptr_t)&w->lv[j];
    }
    for (size_t i = 0; i < 40; i++) {
        w->expRows[i] = g_exp[i < g_exp.size() ? i : g_exp.size() - 1];
        w->expPtrs[i] = &w->expRows[i];
    }
    *(uint32_t*)(w->exp.head + 0x64) = 40;
    *(uint32_t*)(w->exp.head + 0x70) = (uint32_t)(uintptr_t)w->expPtrs;
    *(uint32_t*)(w->mgr + 0x868) = (uint32_t)(uintptr_t)&w->exp;
    *(uint32_t*)GAME_SYS = (uint32_t)(uintptr_t)w->sys;
    *(uint32_t*)PLAYERS = (uint32_t)(uintptr_t)w->mgr;
    *(uint32_t*)MARKS = (uint32_t)(uintptr_t)w->marks;
}

uint32_t NextCost(uint32_t level) { return ((uint32_t(__stdcall*)(uint32_t))NEXT_EXP)(level); }

// ---- the stand-ins ---------------------------------------------------------------------------------
struct Regs {
    uint32_t eax, ecx, edx, ebx, esi, edi, ebp, esp, efl, arg;
    uint8_t xmm[0x80];
};
Regs g_entry;
int g_restCalls[2];
uint32_t g_restHp[2];          // the Arisen's and the main pawn's HP bits when the rest ran
int g_setHp[4], g_update[4];   // per uPlayer: the level-up's HP setter, setLevel's parameter update
uint32_t g_setHpArgs[4][3];

int Who(uint32_t player) {
    for (int i = 0; i < 4; i++)
        if (player == (uint32_t)(uintptr_t)w->players[i]) return i;
    return -1;
}
void __cdecl NoteRest(int which) {
    g_restCalls[which]++;
    for (int s = 0; s < 2; s++) g_restHp[s] = *(uint32_t*)(w->sys + SLOT[s] + 0x2A0);
}
void __cdecl NoteSetHp(uint32_t player, uint32_t hp, uint32_t a2, uint32_t a3) {
    int i = Who(player);
    if (i < 0) return;
    g_setHp[i]++;
    g_setHpArgs[i][0] = hp, g_setHpArgs[i][1] = a2, g_setHpArgs[i][2] = a3;
}
void __cdecl NoteUpdate(uint32_t player) {
    int i = Who(player);
    if (i >= 0) g_update[i]++;
}

#define RECORD_ENTRY                          \
    __asm pushfd                              \
    __asm pop dword ptr [g_entry.efl]         \
    __asm mov dword ptr [g_entry.eax], eax    \
    __asm mov dword ptr [g_entry.ecx], ecx    \
    __asm mov dword ptr [g_entry.edx], edx    \
    __asm mov dword ptr [g_entry.ebx], ebx    \
    __asm mov dword ptr [g_entry.esi], esi    \
    __asm mov dword ptr [g_entry.edi], edi    \
    __asm mov dword ptr [g_entry.ebp], ebp    \
    __asm mov dword ptr [g_entry.esp], esp    \
    __asm movups [g_entry.xmm + 0x00], xmm0   \
    __asm movups [g_entry.xmm + 0x10], xmm1   \
    __asm movups [g_entry.xmm + 0x20], xmm2   \
    __asm movups [g_entry.xmm + 0x30], xmm3   \
    __asm movups [g_entry.xmm + 0x40], xmm4   \
    __asm movups [g_entry.xmm + 0x50], xmm5   \
    __asm movups [g_entry.xmm + 0x60], xmm6   \
    __asm movups [g_entry.xmm + 0x70], xmm7

__declspec(naked) void RestStandA() {   // 0x00737580: the inn at [esp+4], ret 4
    RECORD_ENTRY
    __asm {
        mov eax, dword ptr [esp + 4]
        mov dword ptr [g_entry.arg], eax
        push 0
        call NoteRest
        add esp, 4
        ret 4
    }
}
__declspec(naked) void RestStandB() {   // 0x007377D0: the inn in esi, ret
    RECORD_ENTRY
    __asm {
        mov dword ptr [g_entry.arg], esi
        push 1
        call NoteRest
        add esp, 4
        ret
    }
}
__declspec(naked) void MaxHpStand() {   // 0x00782040: the cPlayerInfo's HP, ret 4
    __asm {
        mov eax, dword ptr [esp + 4]
        movss xmm0, dword ptr [eax + 0x2A0]
        ret 4
    }
}
__declspec(naked) void SetHpStand() {   // 0x00888DA0: eax = the uPlayer, (hp, 0, 1), ret 0xC
    __asm {
        push dword ptr [esp + 0x0C]
        push dword ptr [esp + 0x0C]
        push dword ptr [esp + 0x0C]
        push eax
        call NoteSetHp
        add esp, 0x10
        ret 0x0C
    }
}
__declspec(naked) void UpdateStand() {  // 0x00B807F0: eax = the uPlayer, ret
    __asm {
        push eax
        call NoteUpdate
        add esp, 4
        ret
    }
}

void Jump(uintptr_t at, void* to) {
    uint8_t* p = (uint8_t*)at;
    int32_t rel = (int32_t)((uintptr_t)to - (at + 5));
    p[0] = 0xE9;
    memcpy(p + 1, &rel, 4);
    FlushInstructionCache(GetCurrentProcess(), p, 5);
}

// ---- one rest, the way the state at 0x00736740 makes it ---------------------------------------------
uint32_t g_savedEsp, g_callEsp, g_afterEsp, g_innArg, g_esiArg, g_viaB;
uint32_t g_eflSeed = 0x8C3;   // CF, ZF, SF and OF set: the thunk must hand them on
uintptr_t g_target;
alignas(16) uint8_t g_seed[0x80];

__declspec(naked) void InvokeAsm() {
    __asm {
        pushad
        mov g_savedEsp, esp
        movups xmm0, [g_seed + 0x00]
        movups xmm1, [g_seed + 0x10]
        movups xmm2, [g_seed + 0x20]
        movups xmm3, [g_seed + 0x30]
        movups xmm4, [g_seed + 0x40]
        movups xmm5, [g_seed + 0x50]
        movups xmm6, [g_seed + 0x60]
        movups xmm7, [g_seed + 0x70]
        mov eax, 0x55555555
        mov ecx, 0x66666666
        mov edx, 0x77777777
        mov ebx, 0x11111111
        mov esi, g_esiArg
        mov edi, 0x33333333
        cmp g_viaB, 0
        jne via_b
        push g_innArg
        push g_eflSeed
        popfd
        mov ebp, 0x44444444
        mov g_callEsp, esp
        call dword ptr [g_target]
        jmp done
    via_b:
        push g_eflSeed
        popfd
        mov ebp, 0x44444444
        mov g_callEsp, esp
        call dword ptr [g_target]
    done:
        mov g_afterEsp, esp
        mov esp, g_savedEsp
        popad
        ret
    }
}

uintptr_t CallTarget(uintptr_t site) {
    const uint8_t* p = (const uint8_t*)site;
    if (p[0] != 0xE8) return 0;
    int32_t rel;
    memcpy(&rel, p + 1, 4);
    return site + 5 + rel;
}

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
        return printf("SKIP: run me through vocation_stats_stub.exe (host at %p, 0x%X bytes)\n", (void*)host,
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

// ---- the cases -----------------------------------------------------------------------------------
struct Char {
    bool present;      // a uPlayer in the slot
    uint32_t job, level;
    uint8_t flags;     // +0x206D: 2 the Arisen's, 4 a pawn's, 8 setLevel's own refusal
    uint8_t held;      // +0x31EA
    uint32_t state;    // +0x273C
    bool noInfo;
    uint32_t pastJob;  // the vocation its stats grew in (0: a mix of numbers)
};
struct Case {
    const char* what;
    Char c[2];
    bool viaB;
    bool nullMgr;
    uint32_t tableHole;   // a vocation whose level table is missing
    uint32_t shortTable;  // a vocation whose level table has two rows
    bool marks;           // the level-100/200 marks are armed
    bool sweep;           // print the Arisen's numbers as "stats" lines
};

const Char HIRED = {true, 3, 50, 4, 0, 0, false, 9};
bool g_on, g_who[2];
uint32_t g_number;

struct Snapshot {
    std::vector<uint8_t> sys, marks, mgr, players[4];
};
Snapshot Take() {
    Snapshot s{std::vector<uint8_t>(w->sys, w->sys + SYS_SIZE), std::vector<uint8_t>(w->marks, w->marks + MARK_SIZE),
               std::vector<uint8_t>(w->mgr, w->mgr + MGR_SIZE), {}};
    for (int i = 0; i < 4; i++) s.players[i].assign(w->players[i], w->players[i] + PLAYER_SIZE);
    return s;
}
std::string Diff(const std::vector<uint8_t>& a, const std::vector<uint8_t>& b, const char* name) {
    for (size_t i = 0; i < a.size(); i++)
        if (a[i] != b[i]) return F("%s +0x%X: %02X, not %02X", name, (unsigned)i, b[i], a[i]);
    return "";
}

void PutStats(uint8_t* info, const Stats& s) {
    for (int k = 0; k < 6; k++) *(float*)(info + STAT_OFF[k]) = (float)s.v[k];
}

bool Fit(const Case& c, int s) {
    const Char& ch = c.c[s];
    if (c.nullMgr || !ch.present || ch.noInfo || (ch.flags & 8) || ((ch.flags & 2) && ch.held) ||
        ((ch.flags & 4) && (ch.state & 0x8000)))
        return false;
    if (ch.job < 1 || ch.job > 9 || ch.level < 1 || ch.level > 200) return false;
    return ch.job != c.tableHole && ch.job != c.shortTable;
}

void RunCase(const Case& c) {
    g_number++;
    memset(w->sys, 0, SYS_SIZE);
    memset(w->marks, 0, MARK_SIZE);
    if (c.marks) {
        w->marks[0x5A36] = 1;
        w->sys[0xBE442] = 1;
    }
    for (int i = 0; i < 4; i++) {
        memset(w->players[i], 0, PLAYER_SIZE);
        *(uint32_t*)(w->mgr + MGR_SLOT[i]) = 0;
    }
    for (int i = 0; i < 4; i++) {
        const Char& ch = i < 2 ? c.c[i] : HIRED;
        if (!ch.present) continue;
        uint8_t* p = w->players[i];
        uint8_t* info = w->sys + SLOT[i];
        *(uint32_t*)(w->mgr + MGR_SLOT[i]) = (uint32_t)(uintptr_t)p;
        *(uint32_t*)p = (uint32_t)(uintptr_t)w->vtable;
        p[P_FLAGS] = ch.flags;
        p[P_HELD] = ch.held;
        *(uint32_t*)(p + P_STATE) = ch.state;
        *(uint32_t*)(p + P_HPBAR) = 0;
        *(uint32_t*)(p + P_INFO) = ch.noInfo ? 0 : (uint32_t)(uintptr_t)info;
        *(uint32_t*)(info + I_INDEX) = (uint32_t)i;
        *(uint32_t*)(info + I_JOB) = ch.job;
        *(uint32_t*)(info + I_LEVEL) = ch.level;
        const uint32_t lv = ch.level < 1 ? 1 : ch.level > 200 ? 200 : ch.level;
        if (ch.pastJob) PutStats(info, Model(ch.pastJob, lv));
        else PutStats(info, Stats{{1234 + 7 * (uint32_t)i, 456, 321, 222, 111, 99}});
        *(uint32_t*)(info + I_EXP) = 12345 + 1000 * (uint32_t)i;
        *(uint32_t*)(info + I_COST) = lv >= 200 ? 0 : NextCost(lv);   // the level-up leaves 0 at 200
        *(uint32_t*)(info + 0x2A4) = 0xA5A5A5A5;   // beside the stats: must not move
    }
    const uint32_t keepHole = c.tableHole ? *(uint32_t*)(w->mgr + 0x86C + 4 * c.tableHole) : 0;
    if (c.tableHole) *(uint32_t*)(w->mgr + 0x86C + 4 * c.tableHole) = 0;
    if (c.shortTable) *(uint32_t*)(w->lv[c.shortTable].head + 0x64) = 2;
    *(uint32_t*)PLAYERS = c.nullMgr ? 0 : (uint32_t)(uintptr_t)w->mgr;

    // what the rest must leave: the world as it is, the fit characters' six stats as their vocation's
    Snapshot want = Take();
    bool changed[2] = {false, false};
    Stats expect[2] = {};
    for (int s = 0; s < 2; s++) {
        if (!(g_on && g_who[s] && Fit(c, s))) continue;
        changed[s] = true;
        expect[s] = Model(c.c[s].job, c.c[s].level);
        uint8_t* info = want.sys.data() + SLOT[s];
        PutStats(info, expect[s]);
        if (c.marks && (c.c[s].flags & 2)) {
            uint32_t* bits = (uint32_t*)(want.marks.data() + 0x5A74);
            if (c.c[s].level >= 100) *bits |= 0x800000;
            if (c.c[s].level >= 200) *bits |= 0x1000000;
        }
    }

    memset(g_restCalls, 0, sizeof g_restCalls);
    memset(g_setHp, 0, sizeof g_setHp);
    memset(g_update, 0, sizeof g_update);
    memset(&g_entry, 0, sizeof g_entry);
    const uint32_t inn = 0x0BADC0DE;
    g_viaB = c.viaB ? 1 : 0;
    g_innArg = inn;
    g_esiArg = c.viaB ? inn : 0x22222222;
    g_target = CallTarget(c.viaB ? SITE_B : SITE_A);
    for (int i = 0; i < 0x80; i++) g_seed[i] = (uint8_t)(0x5A ^ (i * 37) ^ g_number);
    InvokeAsm();

    *(uint32_t*)PLAYERS = (uint32_t)(uintptr_t)w->mgr;
    if (c.tableHole) *(uint32_t*)(w->mgr + 0x86C + 4 * c.tableHole) = keepHole;
    if (c.shortTable) *(uint32_t*)(w->lv[c.shortTable].head + 0x64) = (uint32_t)g_lv[c.shortTable].size();
    Snapshot got = Take();
    if (c.tableHole) *(uint32_t*)(want.mgr.data() + 0x86C + 4 * c.tableHole) = keepHole;

    printf("%s (%s)\n", c.what, c.viaB ? "the rest at 0x0073675E" : "the rest at 0x00736757");
    std::string where = Diff(want.sys, got.sys, "sGameSys");
    if (where.empty()) where = Diff(want.marks, got.marks, "the level marks");
    if (where.empty()) where = Diff(want.mgr, got.mgr, "sPlayerManager");
    for (int i = 0; i < 4 && where.empty(); i++) where = Diff(want.players[i], got.players[i], F("uPlayer %d", i).c_str());
    std::string what;
    for (int s = 0; s < 2; s++) {
        if (!c.c[s].present) continue;
        what += F("%s%s %s", what.empty() ? "" : "; ", s ? "the main pawn" : "the Arisen",
                  changed[s] ? F("recalculated as vocation %u level %u (HP %u)", c.c[s].job, c.c[s].level,
                                 expect[s].v[0]).c_str()
                             : "left as it was");
    }
    Check(where.empty(), "  the world after the rest: " + what +
                             "; the experience, the next level's cost, the hired pawns and everything else as before" +
                             (where.empty() ? std::string() : " (differs at " + where + ")"));
    const int which = c.viaB ? 1 : 0;
    const uint32_t efl = 0x8D5;   // CF PF AF ZF SF OF
    Check(g_restCalls[which] == 1 && g_restCalls[1 - which] == 0 && g_entry.arg == inn,
          F("  the rest routine ran once, with the inn (%d, %d calls)", g_restCalls[0], g_restCalls[1]));
    Check(g_entry.eax == 0x55555555 && g_entry.ecx == 0x66666666 && g_entry.edx == 0x77777777 &&
              g_entry.ebx == 0x11111111 && g_entry.esi == g_esiArg && g_entry.edi == 0x33333333 &&
              g_entry.ebp == 0x44444444 && g_entry.esp == g_callEsp - 4 && (g_entry.efl & efl) == (g_eflSeed & efl) &&
              memcmp(g_entry.xmm, g_seed, sizeof g_seed) == 0 && g_afterEsp == g_callEsp + (c.viaB ? 0 : 4),
          "  it saw every register, the flags, xmm0-7 and the stack as the call site left them");
    for (int s = 0; s < 2; s++) {
        if (!changed[s]) {
            Check(g_setHp[s] == 0 && g_update[s] == 0, F("  %s: no game code ran for it", s ? "the main pawn" : "the Arisen"));
            continue;
        }
        const float hp = (float)expect[s].v[0];
        uint32_t bits;
        memcpy(&bits, &hp, 4);
        Check(g_restHp[s] == bits, F("  %s: the new HP was in place when the rest ran", s ? "the main pawn" : "the Arisen"));
        Check(g_setHp[s] == (int)c.c[s].level - 1 && g_update[s] == 1 &&
                  (c.c[s].level == 1 || (g_setHpArgs[s][0] == bits && g_setHpArgs[s][1] == 0 && g_setHpArgs[s][2] == 1)),
              F("  %s: %u level-ups with no notice and one parameter update (%d, %d)", s ? "the main pawn" : "the Arisen",
                c.c[s].level - 1, g_setHp[s], g_update[s]));
    }
    for (int k = 2; k < 4; k++) Check(g_setHp[k] == 0 && g_update[k] == 0, F("  hired pawn %d: no game code ran for it", k - 1));
    if (c.sweep && changed[0]) {
        const uint8_t* info = w->sys + SLOT[0];
        uint32_t v[6];
        for (int k = 0; k < 6; k++) v[k] = (uint32_t)*(const float*)(info + STAT_OFF[k]);
        printf("stats %u %u %u %u %u %u %u %u\n", c.c[0].job, c.c[0].level, v[0], v[1], v[2], v[3], v[4], v[5]);
    }
}
}  // namespace

static int Run(int argc, wchar_t** argv);

// Called by vocation_stats_stub.exe.  Never returns: the stub's code is gone once DDDA.exe is mapped.
extern "C" __declspec(dllexport) void HarnessMain() {
    int argc = 0;
    wchar_t** argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    int code = Run(argc, argv);
    fflush(stdout);
    ExitProcess((UINT)code);
}

static int Run(int argc, wchar_t** argv) {
    setvbuf(stdout, nullptr, _IONBF, 0);  // a fault in the game code must not take the lines before it along
    if (argc < 6) {
        printf("usage: vocation_stats_stub <DDDA.exe> <vocation_stats.asi> <profile> <on 0|1> <party|arisen|pawn> "
               "[<tables.txt>]\n");
        return 1;
    }
    const std::wstring profile = argv[3], who = argv[5];
    g_on = _wtoi(argv[4]) != 0;
    g_who[0] = who != L"pawn";
    g_who[1] = who != L"arisen";
    const bool tables = argc > 6 && ReadTables(argv[6]);
    if (!tables) {
        for (auto& t : g_lv) t.clear();
        g_exp.clear();
        StandInTables();
        printf("the stand-in level tables are used\n");
    }
    if (!MapImage(argv[1])) return 2;

    Check(CallTarget(SITE_A) == REST_A && CallTarget(SITE_B) == REST_B,
          "the mapped game code: uInn's rest state calls 0x00737580 at 0x00736757 and 0x007377D0 at 0x0073675E");
    uint8_t shippedA[5], shippedB[5];
    memcpy(shippedA, (const void*)SITE_A, 5);
    memcpy(shippedB, (const void*)SITE_B, 5);
    if (profile == L"tampered") *(uint8_t*)TAMPER = 0x0A;

    SetEnvironmentVariableW(L"RIFTSTONE_VOCATION_STATS_HARNESS", L"1");
    HMODULE plugin = LoadLibraryW(argv[2]);
    printf("plugin (profile %S)\n", profile.c_str());
    Check(plugin != nullptr, "the plugin loads");
    if (profile == L"tampered") *(uint8_t*)TAMPER = 0x09;
    uintptr_t lo = (uintptr_t)plugin, hi = lo;
    if (plugin) {
        auto* nt = (const IMAGE_NT_HEADERS32*)(lo + ((const IMAGE_DOS_HEADER*)lo)->e_lfanew);
        hi = lo + nt->OptionalHeader.SizeOfImage;
    }
    if (g_on) {
        const uintptr_t a = CallTarget(SITE_A), b = CallTarget(SITE_B);
        Check(a >= lo && a < hi && b >= lo && b < hi && a != b, "both rest calls now go into the plugin");
        uint8_t patchedA[5], patchedB[5];
        memcpy(patchedA, (const void*)SITE_A, 5);
        memcpy(patchedB, (const void*)SITE_B, 5);
        // A second copy of the plugin (its own ini, the same settings) must see the patched calls and change nothing.
        wchar_t copy[MAX_PATH], ini[MAX_PATH], copyIni[MAX_PATH];
        wcscpy_s(copy, argv[2]);
        wchar_t* dot = wcsrchr(copy, L'.');
        if (dot) *dot = 0;
        wcscpy_s(ini, copy);
        wcscat_s(ini, L".ini");
        wcscpy_s(copyIni, copy);
        wcscat_s(copyIni, L"_copy.ini");
        wcscat_s(copy, L"_copy.asi");
        if (CopyFileW(argv[2], copy, FALSE) && CopyFileW(ini, copyIni, FALSE)) {
            HMODULE second = LoadLibraryW(copy);
            Check(second != nullptr && !memcmp((const void*)SITE_A, patchedA, 5) && !memcmp((const void*)SITE_B, patchedB, 5),
                  "a second copy refuses the patched code and changes nothing");
        }
    } else {
        Check(!memcmp((const void*)SITE_A, shippedA, 5) && !memcmp((const void*)SITE_B, shippedB, 5),
              "the rest calls are as shipped: nothing patched");
    }
    if (g_fails) return 1;

    // The rest routines and the helpers the level-up calls, stood in (none is code the plugin checks).
    Jump(REST_A, RestStandA);
    Jump(REST_B, RestStandB);
    Jump(MAX_HP_OF, MaxHpStand);
    Jump(SET_HP, SetHpStand);
    Jump(UPDATE, UpdateStand);
    SetupWorld();
    printf("an inn's rest on a fake party (the game's setLevel, level-up and getNextExp)\n");

    const Char none = {false, 0, 0, 0, 0, 0, false, 0};
    std::vector<Case> cases = {
        {"a Warrior at level 120 whose stats grew as a Sorcerer, a Mage pawn at 118 grown as a Fighter",
         {{true, 7, 120, 2, 0, 0, false, 9}, {true, 3, 118, 4, 0, 0, false, 1}}, false, false, 0, 0, false, false},
        {"the same through the other rest call",
         {{true, 7, 120, 2, 0, 0, false, 9}, {true, 3, 118, 4, 0, 0, false, 1}}, true, false, 0, 0, false, false},
        {"a mix of numbers at level 1 and 2",
         {{true, 5, 1, 2, 0, 0, false, 0}, {true, 8, 2, 4, 0, 0, false, 0}}, false, false, 0, 0, false, false},
        {"the Arisen at level 200 with the level marks armed (the game sets them again)",
         {{true, 1, 200, 2, 0, 0, false, 9}, {true, 9, 200, 4, 0, 0, false, 2}}, true, false, 0, 0, true, false},
        {"setLevel's own refusal (flag 8): the Arisen left",
         {{true, 7, 80, 2 | 8, 0, 0, false, 9}, {true, 3, 80, 4, 0, 0, false, 1}}, false, false, 0, 0, false, false},
        {"a refusal of the level-up (+0x31EA): the Arisen left, setLevel never runs on it",
         {{true, 7, 80, 2, 1, 0, false, 9}, {true, 3, 80, 4, 0, 0, false, 1}}, true, false, 0, 0, false, false},
        {"a refusal of the level-up (+0x273C bit 0x8000): the pawn left",
         {{true, 7, 80, 2, 0, 0, false, 9}, {true, 3, 80, 4, 0, 0x8000, false, 1}}, false, false, 0, 0, false, false},
        {"no cPlayerInfo, no vocation",
         {{true, 7, 80, 2, 0, 0, true, 9}, {true, 0, 80, 4, 0, 0, false, 1}}, false, false, 0, 0, false, false},
        {"vocation 10, level 0", {{true, 10, 80, 2, 0, 0, false, 9}, {true, 3, 0, 4, 0, 0, false, 1}}, true, false, 0,
         0, false, false},
        {"level 201, no main pawn", {{true, 7, 201, 2, 0, 0, false, 9}, none}, false, false, 0, 0, false, false},
        {"the Warrior's level table missing, the Mage's with two rows",
         {{true, 7, 60, 2, 0, 0, false, 9}, {true, 3, 60, 4, 0, 0, false, 1}}, false, false, 7, 3, false, false},
        {"no player manager", {{true, 7, 60, 2, 0, 0, false, 9}, {true, 3, 60, 4, 0, 0, false, 1}}, true, true, 0, 0,
         false, false},
    };
    // every vocation at the levels where its bands change
    static char names[9 * 10][64];
    int k = 0;
    for (uint32_t job = 1; job <= 9; job++)
        for (uint32_t level : {1u, 2u, 10u, 11u, 50u, 100u, 101u, 150u, 199u, 200u}) {
            sprintf_s(names[k], "vocation %u at level %u", job, level);
            cases.push_back({names[k], {{true, job, level, 2, 0, 0, false, job % 9 + 1}, {true, 10 - job, level, 4, 0, 0, false, job}},
                             (k & 1) != 0, false, 0, 0, false, true});
            k++;
        }
    for (const Case& c : cases) RunCase(c);
    printf(g_fails ? "\n%d check(s) FAILED\n" : "\nall checks passed\n", g_fails);
    return g_fails ? 1 : 0;
}

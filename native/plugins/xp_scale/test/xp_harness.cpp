// xp_harness_core -- runs xp_scale inside the real game code, without the game.
//
//   xp_stub.exe <DDDA.exe> <xp_scale.asi> <profile> <kills> <quests> <levelcost> [<lv:rate,...>]
//
// Maps DDDA.exe's image at its fixed base (0x00400000) over the stub's image, loads the plugin (which
// verifies and patches the mapped code), then runs the game's own sGameSys::addExp (0x00443470) on a fake
// party: the Arisen, the main pawn and two hired pawns, each a cPlayerInfo with its level, its experience
// toward the next level (+0x2C4) and that level's cost (+0x2C8), the item and ability tables addExp asks.
// For every case it runs the call twice from the same state: through the patched entry with the amount
// E, and with the original five bytes put back and the amount the plugin is meant to hand on.  The two
// runs must leave every byte of the fake world and every register the caller keeps the same, and the
// Arisen's experience must have grown.  The plugin's amount is also checked against floating point
// (within one point, or the cap).
//
// <lv:rate,...> is the game's own level table (param\pl\level\exp.plexp, read by run_tests.py from the
// player's archives, never stored here); with it the harness also prints the game's getNextExp
// (0x00B5C080) for levels 1..199 as "cost L N" lines for run_tests.py to compare.
//
// Prints "pass"/"FAIL" lines and exits 0 when everything passed, 1 on a failure, 2 when the image
// cannot be mapped here (reported as a skip by run_tests.py).
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shellapi.h>

#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <string>
#include <vector>

namespace {
constexpr uintptr_t BASE = 0x00400000;
constexpr uintptr_t ADD_EXP = 0x00443470;          // sGameSys::addExp(sGameSys*, u32 exp, f32 rate, bool raw)
constexpr uintptr_t NEXT_EXP = 0x00B5C080;         // getNextExp(u32 level), stdcall
constexpr uintptr_t GAME_SYS = 0x018FA4BC;         // sGameSys::mpInstance
constexpr uintptr_t HARD_MODE = 0x018FA4F8;        // the object whose +0x20E77B bit 2 is Hard Mode
constexpr uintptr_t ITEMS = 0x018FA4D4;            // sItemManager::mpInstance (equipment per party slot)
constexpr uintptr_t PLAYERS = 0x018FA4EC;          // sPlayerManager::mpInstance (+0x868 levels, +0x8C8 abilities)
constexpr uintptr_t COCKPIT = 0x018FA4CC;          // the HUD's experience notice; none here
constexpr uintptr_t LOCKING = 0x018D0BB0;          // a byte: addExp takes sGameSys's lock when set
constexpr uintptr_t IAT_ENTER = 0x0139D160, IAT_LEAVE = 0x0139D164;
constexpr uintptr_t KILL_CALL = 0x00857522;        // a kill's reward: call sGameSys::addExp
constexpr uintptr_t QUEST_CALLS[] = {0x0078FFC2, 0x007905E4, 0x00791827, 0x0051292D};
constexpr uintptr_t TAMPER = 0x00857515;           // push 0 of the kill site: the byte the tampered profile changes
constexpr uint32_t SYS_SIZE = 0xC0000, HARD_SIZE = 0x210000, ITEM_SIZE = 0x6000;
constexpr uint32_t SLOT[4] = {0xA76D0, 0xA7EC0, 0xA9520, 0xAAB80};   // the party's cPlayerInfo in sGameSys
constexpr uint32_t P_ACTIVE = 4, P_INDEX = 8, P_JOB = 0x10, P_EXP = 0x2C4, P_COST = 0x2C8, P_ABILITY = 0x258,
                   P_LEVEL = 0x700, P_RIM = 0x34C;
constexpr uint32_t EXP_CAP = 999999999;          // the most one reward hands on

int g_fails = 0;
void Check(bool ok, const std::string& what) {
    printf("  %s  %s\n", ok ? "pass" : "FAIL", what.c_str());
    if (!ok) g_fails++;
}
std::string F(const char* fmt, ...) {
    char s[320];
    va_list ap;
    va_start(ap, fmt);
    _vsnprintf_s(s, sizeof s, _TRUNCATE, fmt, ap);
    va_end(ap);
    return s;
}

// ---- the fake world ------------------------------------------------------------------------------
struct Row {        // a cPlExp as getNextExp reads it: +4 the band's rate, +8 its level
    uint32_t vtable;
    float rate;
    uint32_t level;
};
struct Ability {    // an ability table entry as getAbilityBonus's case 0 reads it: +4 with it, +8 without
    uint32_t pad;
    float with, without;
    uint32_t count;
};
struct World {
    uint8_t* sys;
    uint8_t* hard;
    uint8_t* items;
    uint8_t players[0x1000];
    uint8_t levels[0x100];
    uint8_t abilities[0x100];
    Row rows[40];
    Row* rowPtrs[40];
    Ability expAbility;
    Ability* abilityPtrs[0x80];
    uint32_t vtable[0x40];
    uint8_t dti[0x40];
    CRITICAL_SECTION lock;
};
World* w;

uint8_t* g_dti;

// cPlayerInfo's virtual at +0x10 (thiscall): its DTI, which is not the class addExp tests for.
__declspec(naked) void GetDti() {
    __asm {
        mov eax, g_dti
        ret
    }
}

void SetupWorld(const std::vector<Row>& table) {
    w = (World*)VirtualAlloc(nullptr, sizeof(World), MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    w->sys = (uint8_t*)VirtualAlloc(nullptr, SYS_SIZE, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    w->hard = (uint8_t*)VirtualAlloc(nullptr, HARD_SIZE, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    w->items = (uint8_t*)VirtualAlloc(nullptr, ITEM_SIZE, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    for (uint32_t& s : w->vtable) s = (uint32_t)(uintptr_t)GetDti;
    g_dti = w->dti;
    *(uint32_t*)(w->dti + 4) = 0x7777;   // not the DTI at [0x0199315C] (zero here); +0x10 no parent
    // The level table: the game's rows when run_tests.py passed them, else a stand-in of the same shape
    // (row 0: level 1's cost, row 1: the base, rows 2..: bands {level from, rate}).  The last row repeats.
    std::vector<Row> rows = table;
    if (rows.empty()) rows = {{0, 1.0f, 300}, {0, 1.0f, 250}, {0, 1.5f, 2}, {0, 4.0f, 20}, {0, 9.0f, 90}, {0, 0.0f, 200}};
    for (size_t i = 0; i < 40; i++) {
        w->rows[i] = rows[i < rows.size() ? i : rows.size() - 1];
        w->rowPtrs[i] = &w->rows[i];
    }
    *(uint32_t*)(w->levels + 0x70) = (uint32_t)(uintptr_t)w->rowPtrs;
    w->expAbility = {0, 1.25f, 1.0f, 0};
    for (Ability*& a : w->abilityPtrs) a = nullptr;
    w->abilityPtrs[0x5D] = &w->expAbility;
    *(uint32_t*)(w->abilities + 0x70) = (uint32_t)(uintptr_t)w->abilityPtrs;
    *(uint32_t*)(w->players + 0x868) = (uint32_t)(uintptr_t)w->levels;
    *(uint32_t*)(w->players + 0x8C8) = (uint32_t)(uintptr_t)w->abilities;
    InitializeCriticalSection(&w->lock);
    *(uint32_t*)GAME_SYS = (uint32_t)(uintptr_t)w->sys;
    *(uint32_t*)HARD_MODE = (uint32_t)(uintptr_t)w->hard;
    *(uint32_t*)ITEMS = (uint32_t)(uintptr_t)w->items;
    *(uint32_t*)PLAYERS = (uint32_t)(uintptr_t)w->players;
    *(uint32_t*)COCKPIT = 0;
    *(uint32_t*)IAT_ENTER = (uint32_t)(uintptr_t)&EnterCriticalSection;
    *(uint32_t*)IAT_LEAVE = (uint32_t)(uintptr_t)&LeaveCriticalSection;
}

uint32_t NextCost(uint32_t level) { return ((uint32_t(__stdcall*)(uint32_t))NEXT_EXP)(level); }

struct Party {
    uint32_t level[4];
    bool active[4];
    uint32_t job;
    bool ability;     // the Arisen has ability 0x5D
    bool hard, locking;
    uint32_t stage;
};

void SetParty(const Party& p) {
    memset(w->sys, 0, SYS_SIZE);
    memset(w->hard, 0, HARD_SIZE);
    memset(w->items, 0, ITEM_SIZE);
    *(uint32_t*)(w->sys + 0x34) = p.stage;
    if (p.hard) w->hard[0x20E77B] = 4;
    *(uint8_t*)LOCKING = p.locking ? 1 : 0;
    if (p.locking) {
        w->sys[0x1C] = 1;
        memcpy(w->sys + 4, &w->lock, sizeof(CRITICAL_SECTION));
    }
    for (int i = 0; i < 4; i++) {
        uint8_t* info = w->sys + SLOT[i];
        *(uint32_t*)info = (uint32_t)(uintptr_t)w->vtable;
        info[P_ACTIVE] = p.active[i] ? 1 : 0;
        *(uint32_t*)(info + P_INDEX) = (uint32_t)i;
        *(uint32_t*)(info + P_JOB) = p.job;
        *(uint32_t*)(info + P_LEVEL) = p.level[i];
        *(uint32_t*)(info + P_COST) = NextCost(p.level[i]);
        *(uint32_t*)(info + P_EXP) = 1000;
    }
    if (p.ability) *(uint32_t*)(w->sys + SLOT[0] + P_ABILITY) = 0x5D;
}

// ---- one call, every register read after ----------------------------------------------------------
struct Regs {
    uint32_t eax, ecx, edx, ebx, esi, edi, ebp, esp;
    alignas(16) uint8_t xmm[0x80];
};
uint32_t g_savedEsp, g_callEsp, g_sysArg, g_expArg, g_rawArg;
float g_rateArg;
uintptr_t g_target = ADD_EXP;
alignas(16) uint8_t g_seed[0x80];
Regs g_out;

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
        mov ebx, 0x11111111
        mov esi, 0x22222222
        mov edi, 0x33333333
        mov ebp, 0x44444444
        push g_rawArg
        push g_rateArg
        push g_expArg
        push g_sysArg
        mov g_callEsp, esp
        call dword ptr [g_target]
        mov g_out.eax, eax
        mov g_out.ecx, ecx
        mov g_out.edx, edx
        mov g_out.ebx, ebx
        mov g_out.esi, esi
        mov g_out.edi, edi
        mov g_out.ebp, ebp
        mov g_out.esp, esp
        movups [g_out.xmm + 0x00], xmm0
        movups [g_out.xmm + 0x10], xmm1
        movups [g_out.xmm + 0x20], xmm2
        movups [g_out.xmm + 0x30], xmm3
        movups [g_out.xmm + 0x40], xmm4
        movups [g_out.xmm + 0x50], xmm5
        movups [g_out.xmm + 0x60], xmm6
        movups [g_out.xmm + 0x70], xmm7
        mov esp, g_savedEsp
        popad
        ret
    }
}

Regs Invoke(uint32_t exp, float rate, bool raw, uint32_t seed) {
    g_sysArg = (uint32_t)(uintptr_t)w->sys;
    g_expArg = exp;
    g_rateArg = rate;
    g_rawArg = raw ? 1 : 0;
    for (int i = 0; i < 0x80; i++) g_seed[i] = (uint8_t)(0x5A ^ (i * 37) ^ seed);
    memset(&g_out, 0, sizeof g_out);
    InvokeAsm();
    return g_out;
}

uint8_t g_patched[5], g_original[5] = {0x83, 0xEC, 0x24, 0x53, 0x55};
void PutEntry(const uint8_t* bytes) {
    memcpy((void*)ADD_EXP, bytes, 5);
    FlushInstructionCache(GetCurrentProcess(), (void*)ADD_EXP, 5);
}

struct Snapshot {
    std::vector<uint8_t> sys, hard, items;
};
Snapshot Take() {
    return {std::vector<uint8_t>(w->sys, w->sys + SYS_SIZE), std::vector<uint8_t>(w->hard, w->hard + HARD_SIZE),
            std::vector<uint8_t>(w->items, w->items + ITEM_SIZE)};
}
bool Same(const Snapshot& a, const Snapshot& b, std::string& where) {
    for (size_t i = 0; i < a.sys.size(); i++)
        if (a.sys[i] != b.sys[i]) return where = F("sGameSys +0x%X: %02X vs %02X", (unsigned)i, a.sys[i], b.sys[i]), false;
    if (a.hard != b.hard) return where = "the Hard Mode object", false;
    if (a.items != b.items) return where = "the item manager", false;
    return true;
}

// The plugin's amount: the rule docs/re-xp.md states, in whole numbers.
uint32_t Expected(uint32_t exp, bool raw, uint32_t kills, uint32_t quests, uint32_t levelCost, uint32_t cost) {
    if (exp == 0) return 0;
    uint64_t v = (uint64_t)exp * (raw ? quests : kills);
    const uint64_t top = (uint64_t)EXP_CAP * 1000;
    if (v > top) v = top;
    if (levelCost && cost > levelCost) {
        v = v / levelCost * cost + v % levelCost * cost / levelCost;
        if (v > top) v = top;
    }
    uint64_t out = (v + 500) / 1000;
    if (out == 0) out = 1;
    return out > EXP_CAP ? EXP_CAP : (uint32_t)out;
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
        return printf("SKIP: run me through xp_stub.exe (host at %p, 0x%X bytes)\n", (void*)host,
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

std::vector<Row> ParseTable(const wchar_t* s) {
    std::vector<Row> rows;
    while (s && *s) {
        wchar_t* end;
        unsigned long lv = wcstoul(s, &end, 10);
        if (*end != L':') return {};
        double rate = wcstod(end + 1, &end);
        rows.push_back(Row{0, (float)rate, (uint32_t)lv});
        if (*end == L',') end++;
        else if (*end) return {};
        s = end;
    }
    return rows;
}

struct Case {
    const char* what;
    uint32_t exp;
    float rate;
    bool raw;
    Party party;
};
}  // namespace

static int Run(int argc, wchar_t** argv);

// Called by xp_stub.exe.  Never returns: the stub's code is gone once DDDA.exe is mapped.
extern "C" __declspec(dllexport) void HarnessMain() {
    int argc = 0;
    wchar_t** argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    int code = Run(argc, argv);
    fflush(stdout);
    ExitProcess((UINT)code);
}

static int Run(int argc, wchar_t** argv) {
    setvbuf(stdout, nullptr, _IONBF, 0);  // a fault in the game code must not take the lines before it along
    if (argc < 7) {
        printf("usage: xp_stub <DDDA.exe> <xp_scale.asi> <profile> <kills> <quests> <levelcost> [<lv:rate,...>]\n");
        return 1;
    }
    const std::wstring profile = argv[3];
    const uint32_t kills = (uint32_t)_wtoi(argv[4]), quests = (uint32_t)_wtoi(argv[5]),
                   levelCost = (uint32_t)_wtoi(argv[6]);   // per mille, per mille, points
    const std::vector<Row> table = argc > 7 ? ParseTable(argv[7]) : std::vector<Row>{};
    const bool patched = profile != L"vanilla" && profile.rfind(L"bogus", 0) != 0 && profile != L"tampered";
    if (!MapImage(argv[1])) return 2;

    bool sitesOk = CallTarget(KILL_CALL) == ADD_EXP;
    for (uintptr_t q : QUEST_CALLS) sitesOk &= CallTarget(q) == ADD_EXP;
    Check(sitesOk, "the mapped game code calls sGameSys::addExp (0x00443470) from the kill and the four reward sites");
    Check(memcmp((const void*)ADD_EXP, g_original, 5) == 0, "addExp starts with sub esp, 0x24; push ebx; push ebp");
    if (profile == L"tampered") *(uint8_t*)TAMPER = 0x02;

    SetEnvironmentVariableW(L"RIFTSTONE_XP_SCALE_HARNESS", L"1");
    HMODULE plugin = LoadLibraryW(argv[2]);
    printf("plugin (profile %S)\n", profile.c_str());
    Check(plugin != nullptr, "the plugin loads");
    if (profile == L"tampered") *(uint8_t*)TAMPER = 0x00;
    memcpy(g_patched, (const void*)ADD_EXP, 5);
    uintptr_t lo = (uintptr_t)plugin, hi = lo;
    if (plugin) {
        auto* nt = (const IMAGE_NT_HEADERS32*)(lo + ((const IMAGE_DOS_HEADER*)lo)->e_lfanew);
        hi = lo + nt->OptionalHeader.SizeOfImage;
    }
    if (patched) {
        int32_t rel;
        memcpy(&rel, g_patched + 1, 4);
        uintptr_t to = ADD_EXP + 5 + rel;
        Check(g_patched[0] == 0xE9 && to >= lo && to < hi, "addExp's first five bytes jump into the plugin");
        // A second copy of the plugin must see a patched entry and change nothing.
        // (with the same settings: its own ini, xp_scale_copy.ini)
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
            Check(second != nullptr && memcmp((const void*)ADD_EXP, g_patched, 5) == 0,
                  "a second copy refuses the patched code and changes nothing");
        }
    } else {
        Check(memcmp((const void*)ADD_EXP, g_original, 5) == 0, "addExp is as shipped: nothing patched");
    }
    if (g_fails) return 1;

    SetupWorld(table);
    if (!table.empty()) {
        // The game's own level costs, for run_tests.py to hold against the rows it read.
        for (uint32_t lv = 1; lv < 200; lv++) printf("cost %u %u\n", lv, NextCost(lv));
    }

    const Party solo = {{12, 1, 1, 1}, {true, false, false, false}, 1, false, false, false, 100};
    const Party party = {{20, 18, 25, 9}, {true, true, true, true}, 3, false, false, false, 100};
    const Party high = {{150, 149, 160, 120}, {true, true, true, true}, 7, true, false, false, 100};
    const Party top = {{199, 199, 199, 199}, {true, true, true, true}, 2, false, true, true, 100};
    const Party hard = {{60, 55, 61, 70}, {true, true, false, true}, 5, false, true, false, 100};
    const Case cases[] = {
        {"a goblin's kill, alone at level 12", 9, 1.0f, false, solo},
        {"a kill of 1 point, alone", 1, 1.0f, false, solo},
        {"a kill, the whole party", 1200, 1.0f, false, party},
        {"a kill at a rate of 0.5, the whole party", 1200, 0.5f, false, party},
        {"a quest's reward, the whole party", 15000, 1.0f, true, party},
        {"a kill at level 150 (LevelCost, the ability)", 4000, 1.0f, false, high},
        {"a quest at level 150 (LevelCost)", 60000, 1.0f, true, high},
        {"a kill on Hard Mode, sGameSys locked", 2500, 1.0f, false, top},
        {"a kill on Hard Mode, one hired pawn away", 777, 1.0f, false, hard},
        {"a quest on Hard Mode (raw: no double)", 30000, 1.0f, true, hard},
        {"no experience", 0, 1.0f, false, party},
        {"a huge kill (the cap)", 0xFFFFFFF0u, 1.0f, false, party},
        {"a huge quest at level 199 (the cap)", 900000000u, 1.0f, true, top},
    };
    printf("sGameSys::addExp on a fake party: the patched call vs. the game's with the plugin's amount\n");
    uint32_t number = 0;
    for (const Case& c : cases) {
        number++;
        SetParty(c.party);
        const uint32_t cost = *(uint32_t*)(w->sys + SLOT[0] + P_COST);
        const uint32_t want = patched ? Expected(c.exp, c.raw, kills, quests, levelCost, cost) : c.exp;
        // floating point, independently: the multiplier, LevelCost's factor, the cap
        double f = (c.raw ? quests : kills) / 1000.0;
        if (patched && levelCost && cost > levelCost) f *= (double)cost / levelCost;
        double ideal = patched ? (double)c.exp * f : (double)c.exp;
        bool fits = !patched ? want == c.exp
                  : c.exp == 0 ? want == 0
                  : ideal >= EXP_CAP ? want == EXP_CAP
                  : ideal < 1 ? want == 1
                  : fabs((double)want - ideal) <= 1.0 + ideal * 1e-6;
        Check(fits, std::string(c.what) + F(": %u becomes %u (%.3f exactly)", c.exp, want, ideal));

        const uint32_t before = *(uint32_t*)(w->sys + SLOT[0] + P_EXP);
        Regs via = Invoke(c.exp, c.rate, c.raw, number);
        Snapshot a = Take();
        uint32_t after = *(uint32_t*)(w->sys + SLOT[0] + P_EXP);
        SetParty(c.party);
        PutEntry(g_original);
        Regs vanilla = Invoke(want, c.rate, c.raw, number);
        PutEntry(g_patched);
        Snapshot b = Take();
        std::string where;
        Check(Same(a, b, where), "  the game's state as after the game's own call with that amount" +
                                     (where.empty() ? std::string() : " (differs at " + where + ")"));
        Check(via.eax == vanilla.eax && via.ecx == vanilla.ecx && via.edx == vanilla.edx && via.ebx == 0x11111111 &&
                  via.esi == 0x22222222 && via.edi == 0x33333333 && via.ebp == 0x44444444 &&
                  vanilla.ebx == 0x11111111 && via.esp == vanilla.esp && via.esp == g_callEsp + 0x10 &&
                  memcmp(via.xmm, vanilla.xmm, sizeof via.xmm) == 0,
              "  every register and xmm0-7 as the game's own call left them (ebx, esi, edi, ebp kept, four "
              "arguments taken off)");
        if (want && want < 0x80000000u)
            Check(after > before, F("  the Arisen's experience grew: %u -> %u", before, after));
    }

    printf(g_fails ? "\n%d check(s) FAILED\n" : "\nall checks passed\n", g_fails);
    return g_fails ? 1 : 0;
}

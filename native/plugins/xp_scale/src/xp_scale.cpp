// xp_scale -- Riftstone native plugin: more or less experience from kills and from quests, and a flatter
// level curve.
//
// What the game does (DDDA.exe build 2364871; PS3 build names; docs/re-xp.md)
//   Every experience the party gets goes through sGameSys::addExp (0x00443470, four arguments on the
//   stack: sGameSys, the amount, a rate, raw).  It hands the amount to the four party slots: the Arisen
//   and the main pawn through cPlayerInfo::addExp (0x00782D10: the experience toward the next level at
//   +0x2C4, capped at 1,000,000,000, and the vocation's discipline from it), the two hired pawns a tenth
//   of it as Rift Crystals (+0x34C, capped at 99,999,999).  With raw false it first multiplies the amount by the party's level spread and the
//   rate, and doubles it on Hard Mode.  Five places call it:
//     0x00857522  a kill's reward (the PS3 build's uCharacterBase::setExperience): raw = 0
//     0x0078FFC2, 0x007905E4, 0x00791827  a quest's reward (cQuestCtrl::QuestTblResultProc): raw = 1
//     0x0051292D  a reward of the game's mode dispatcher (Dark Arisen only, not in the PS3 build): raw = 1
//   A level's cost is getNextExp (0x00B5C080) of param\pl\level\exp.plexp (rPlExp); level-up
//   (0x00B5BD69) stores it at +0x2C8 and subtracts it from +0x2C4.  Vanilla: 4,640 at level 10, 80,100 at
//   50, 124,100 at 100, 289,100 at 150, 504,700 at 199.
//
// What this plugin does (xp_scale.ini, next to this file)
//   The first five bytes of sGameSys::addExp (sub esp, 0x24; push ebx; push ebp) become a jump to a
//   thunk that changes the amount, runs those five bytes and goes on at 0x00443475:
//     Kills      multiplies an amount whose raw is 0 (0.1 to 2.0),
//     Quests     multiplies an amount whose raw is 1 (0.1 to 2.0),
//     LevelCost  (0 = off, else 1000 to 10,000,000) while the Arisen's next level costs more than it,
//                multiplies the amount by that cost / LevelCost, so no level takes more killing than a
//                level of LevelCost does.
//   The result is rounded to the nearest whole point, at least 1 when the game gave at least 1, and at
//   most 999,999,999 (so the game's own sums stay below 2^32 and its caps hold).  Every register and flag comes back as it went in.  Kills = 1,
//   Quests = 1 and LevelCost = 0 patch nothing.
//
// Safety
//   DDDA.exe build 2364871 only.  The entry, the raw tests, the Arisen's slot, the cap and level-up's
//   compare, and the five call sites' arguments are compared first; on any difference nothing is patched
//   and riftstone\logs\xp_scale.log says why.  The Arisen's level cost is read under SEH: a fault there
//   leaves LevelCost out of that one reward.  Integer arithmetic only, so the game's x87 and SSE state are
//   never touched.  Original code; no third-party source.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <wchar.h>

namespace {

// ---- build 2364871 --------------------------------------------------------------------------
constexpr uintptr_t IMAGE_BASE = 0x00400000;
constexpr uintptr_t ADD_EXP = 0x00443470;            // sGameSys::addExp
constexpr uintptr_t RESUME = 0x00443475;             // after the five bytes the jump replaces
constexpr uintptr_t GAME_SYS = 0x018FA4BC;           // sGameSys::mpInstance
constexpr uint32_t OFF_ARISEN = 0xA76D0;             // sGameSys: the Arisen's cPlayerInfo
constexpr uint32_t OFF_NEXT_COST = 0x2C8;            // cPlayerInfo: what the next level costs
constexpr uint32_t EXP_CAP = 999999999;              // the most one reward hands on

struct Expect {
    const char* what;
    uintptr_t at;
    uint8_t bytes[36];
    uint8_t len;
};

// The code the patch relies on: a second copy of the plugin, another patch or another build shows up
// here as a difference.
const Expect CONTEXT[] = {
    {"sGameSys::addExp: its start (sub esp, 0x24; push ebx; push ebp; mov ebp, [esp+0x30])", ADD_EXP,
     {0x83, 0xEC, 0x24, 0x53, 0x55, 0x8B, 0x6C, 0x24, 0x30, 0x80, 0x7D, 0x1C, 0x00, 0x56, 0x57, 0x75}, 16},
    {"sGameSys::addExp: raw (its fourth argument) decides Hard Mode's double", 0x004434BB,
     {0x80, 0x7C, 0x24, 0x44, 0x00, 0x75, 0x3A, 0x8B, 0x44, 0x24, 0x3C, 0xDB, 0x44, 0x24, 0x3C}, 15},
    {"sGameSys::addExp: raw decides the level-spread rate", 0x00443545,
     {0x80, 0x7C, 0x24, 0x44, 0x00, 0x75, 0x75}, 7},
    {"sGameSys::addExp: slot 0 is the Arisen (+0xA76D0)", 0x00443511,
     {0x8B, 0x35, 0xBC, 0xA4, 0x8F, 0x01, 0x81, 0xC6, 0xD0, 0x76, 0x0A, 0x00}, 12},
    {"sGameSys::addExp: the Arisen's and the main pawn's calls to cPlayerInfo::addExp", 0x0044362F,
     {0xE8, 0xDC, 0xF6, 0x33, 0x00}, 5},
    {"sGameSys::addExp: the main pawn's call to cPlayerInfo::addExp", 0x00443743,
     {0xE8, 0xC8, 0xF5, 0x33, 0x00}, 5},
    {"cPlayerInfo::addExp: the cap of +0x2C4 at 1,000,000,000", 0x00782F0D,
     {0xB8, 0x00, 0xCA, 0x9A, 0x3B, 0x39, 0x87, 0xC4, 0x02, 0x00, 0x00, 0x72, 0x06, 0x89, 0x87, 0xC4, 0x02, 0x00,
      0x00}, 19},
    {"level-up: +0x2C4 against the next level's cost at +0x2C8", 0x00B5BD69,
     {0x8B, 0xB3, 0xEC, 0x3D, 0x00, 0x00, 0x8B, 0x8E, 0x00, 0x07, 0x00, 0x00, 0x81, 0xF9, 0xC8, 0x00, 0x00, 0x00,
      0x73, 0xC4, 0x8B, 0x86, 0xC4, 0x02, 0x00, 0x00, 0x8B, 0x96, 0xC8, 0x02, 0x00, 0x00, 0x3B, 0xC2}, 34},
    {"level-up: stores getNextExp's cost at +0x2C8", 0x00B5BF87,
     {0x57, 0xE8, 0xF3, 0x00, 0x00, 0x00, 0x56, 0x89, 0x86, 0xC8, 0x02, 0x00, 0x00}, 13},
    {"a kill's reward: raw = 0", 0x00857514,
     {0x6A, 0x00, 0x51, 0x8B, 0x4C, 0x24, 0x14, 0xF3, 0x0F, 0x11, 0x04, 0x24, 0x51, 0x52, 0xE8, 0x49, 0xBF, 0xBE,
      0xFF}, 19},
    {"a quest's reward: raw = 1", 0x0078FFB2,
     {0x6A, 0x01, 0x51, 0x8B, 0x0D, 0xBC, 0xA4, 0x8F, 0x01, 0xF3, 0x0F, 0x11, 0x04, 0x24, 0x50, 0x51, 0xE8, 0xA9,
      0x34, 0xCB, 0xFF}, 21},
    {"a quest's reward after its gold: raw = 1", 0x007905D5,
     {0x6A, 0x01, 0x51, 0xF3, 0x0F, 0x11, 0x04, 0x24, 0x50, 0xA1, 0xBC, 0xA4, 0x8F, 0x01, 0x50, 0xE8, 0x87, 0x2E,
      0xCB, 0xFF}, 20},
    {"a quest's reward by its kind: raw = 1", 0x00791817,
     {0x6A, 0x01, 0x51, 0x8B, 0x0D, 0xBC, 0xA4, 0x8F, 0x01, 0xF3, 0x0F, 0x11, 0x04, 0x24, 0x50, 0x51, 0xE8, 0x44,
      0x1C, 0xCB, 0xFF}, 21},
    {"the mode dispatcher's reward: raw = 1", 0x0051291D,
     {0x6A, 0x01, 0x51, 0x8B, 0x0D, 0xBC, 0xA4, 0x8F, 0x01, 0xF3, 0x0F, 0x11, 0x04, 0x24, 0x50, 0x51, 0xE8, 0x3E,
      0x0B, 0xF3, 0xFF}, 21},
};

wchar_t g_logPath[MAX_PATH];
volatile LONG g_loggedKill = 0, g_loggedQuest = 0, g_loggedCost = 0;

void Log(const char* fmt, ...) {
    char line[640];
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

bool Verify() {
    for (const Expect& e : CONTEXT) {
        if (!Readable(e.at, e.len) || memcmp((const void*)e.at, e.bytes, e.len) != 0)
            return Log("refused: %s at 0x%08X is not the expected code (another build, or already patched)", e.what,
                       (unsigned)e.at),
                   false;
    }
    return true;
}

// ---- the amount the thunk hands on ----------------------------------------------------------------
uint32_t g_kills = 1000, g_quests = 1000;   // per mille
uint32_t g_levelCost = 0;                   // 0 = off

// The Arisen's next level cost, or 0 when it cannot be read.
uint32_t NextCost() {
    __try {
        uint32_t sys = *(const uint32_t*)GAME_SYS;
        if (!sys) return 0;
        return *(const uint32_t*)(sys + OFF_ARISEN + OFF_NEXT_COST);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return 0;
    }
}

uint32_t __cdecl ScaleExp(uint32_t exp, uint32_t raw) {
    if (exp == 0) return 0;
    const bool quest = (raw & 0xFF) != 0;
    // exp x per-mille: below 2^43, then at most EXP_CAP x 1000 (below 2^40) before LevelCost multiplies it.
    uint64_t v = (uint64_t)exp * (quest ? g_quests : g_kills);
    const uint64_t top = (uint64_t)EXP_CAP * 1000;
    if (v > top) v = top;
    uint32_t cost = 0;
    if (g_levelCost) {
        cost = NextCost();
        if (cost > g_levelCost) {
            // v * cost / LevelCost exactly: v / LevelCost is below 2^30 (LevelCost is at least 1000), the
            // remainder below 2^24, so neither product reaches 2^64.
            const uint64_t q = v / g_levelCost, r = v % g_levelCost;
            v = q * cost + r * cost / g_levelCost;
            if (v > top) v = top;
        }
    }
    uint64_t out = (v + 500) / 1000;
    if (out == 0) out = 1;
    if (out > EXP_CAP) out = EXP_CAP;
    if (quest) {
        if (!g_loggedQuest && !InterlockedExchange(&g_loggedQuest, 1))
            Log("a quest's reward: %u experience became %u (logged once)", exp, (uint32_t)out);
    } else if (!g_loggedKill && !InterlockedExchange(&g_loggedKill, 1)) {
        Log("a kill's reward: %u experience became %u (logged once)", exp, (uint32_t)out);
    }
    if (cost > g_levelCost && g_levelCost && !g_loggedCost && !InterlockedExchange(&g_loggedCost, 1))
        Log("the next level costs %u, more than LevelCost %u: rewards x%u.%03u (logged once)", cost, g_levelCost,
            (unsigned)((uint64_t)cost * 1000 / g_levelCost / 1000), (unsigned)((uint64_t)cost * 1000 / g_levelCost % 1000));
    return (uint32_t)out;
}

uint32_t g_resume = RESUME;

// In place of sGameSys::addExp's first five bytes.  On entry [esp] is the caller's return address, then
// sGameSys, the amount, the rate and raw.  Only the amount changes.
__declspec(naked) void AddExpThunk() {
    __asm {
        pushfd
        pushad
        sub esp, 0x80
        movups [esp + 0x00], xmm0
        movups [esp + 0x10], xmm1
        movups [esp + 0x20], xmm2
        movups [esp + 0x30], xmm3
        movups [esp + 0x40], xmm4
        movups [esp + 0x50], xmm5
        movups [esp + 0x60], xmm6
        movups [esp + 0x70], xmm7
        // 0x80 of xmm, 0x20 of pushad, 4 of flags: the return address is at esp + 0xA4
        mov eax, dword ptr [esp + 0xA4 + 0x10]   // raw
        push eax
        mov eax, dword ptr [esp + 0xA4 + 0x08 + 4]   // the amount
        push eax
        call ScaleExp
        add esp, 8
        mov dword ptr [esp + 0xA4 + 0x08], eax
        movups xmm0, [esp + 0x00]
        movups xmm1, [esp + 0x10]
        movups xmm2, [esp + 0x20]
        movups xmm3, [esp + 0x30]
        movups xmm4, [esp + 0x40]
        movups xmm5, [esp + 0x50]
        movups xmm6, [esp + 0x60]
        movups xmm7, [esp + 0x70]
        add esp, 0x80
        popad
        popfd
        sub esp, 0x24          // the five bytes the jump replaced
        push ebx
        push ebp
        jmp dword ptr [g_resume]
    }
}

bool WriteJump(uintptr_t at, uintptr_t target) {
    uint8_t code[5] = {0xE9};
    int32_t rel = (int32_t)(target - (at + 5));
    memcpy(code + 1, &rel, 4);
    DWORD old;
    if (!VirtualProtect((void*)at, 5, PAGE_EXECUTE_READWRITE, &old)) return false;
    memcpy((void*)at, code, 5);
    VirtualProtect((void*)at, 5, old, &old);
    FlushInstructionCache(GetCurrentProcess(), (void*)at, 5);
    return true;
}

// "1.5" -> 1500 per mille.  Digits and at most one point; decimals past the third are dropped; lo..hi per mille.
bool ParseMille(const wchar_t* s, uint32_t lo, uint32_t hi, uint32_t& out) {
    uint64_t whole = 0, frac = 0;
    int fracDigits = 0, digits = 0;
    bool point = false;
    for (const wchar_t* p = s; *p; p++) {
        if (*p == L'.' && !point) {
            point = true;
            continue;
        }
        if (*p < L'0' || *p > L'9') return false;
        digits++;
        if (point) {
            if (fracDigits == 3) continue;   // finer than a thousandth
            frac = frac * 10 + (uint64_t)(*p - L'0');
            fracDigits++;
        } else {
            whole = whole * 10 + (uint64_t)(*p - L'0');
            if (whole > 100000) return false;
        }
    }
    if (!digits) return false;
    while (fracDigits < 3) frac *= 10, fracDigits++;
    uint64_t v = whole * 1000 + frac;
    if (v < lo || v > hi) return false;
    out = (uint32_t)v;
    return true;
}

bool ParseInt(const wchar_t* s, uint32_t& out) {
    if (!*s) return false;
    uint64_t v = 0;
    for (const wchar_t* p = s; *p; p++) {
        if (*p < L'0' || *p > L'9') return false;
        v = v * 10 + (uint64_t)(*p - L'0');
        if (v > 0xFFFFFFFFull) return false;
    }
    out = (uint32_t)v;
    return true;
}

// [xp] Kills, Quests and [curve] LevelCost from the ini next to this file; 1.0, 1.0 and 0 when missing.
bool LoadSettings(HMODULE self, uint32_t& kills, uint32_t& quests, uint32_t& levelCost) {
    wchar_t ini[MAX_PATH];
    GetModuleFileNameW(self, ini, MAX_PATH);
    wchar_t* dot = wcsrchr(ini, L'.');
    wchar_t* slash = wcsrchr(ini, L'\\');
    if (dot && (!slash || dot > slash)) *dot = 0;
    wcscat_s(ini, L".ini");
    auto read = [&](const wchar_t* section, const wchar_t* key, const wchar_t* fallback, wchar_t* v) {
        GetPrivateProfileStringW(section, key, fallback, v, 32, ini);
        wchar_t* s = v;
        while (*s == L' ' || *s == L'\t') s++;
        size_t n = wcslen(s);
        while (n && (s[n - 1] == L' ' || s[n - 1] == L'\t')) s[--n] = 0;
        if (s != v) memmove(v, s, (n + 1) * sizeof(wchar_t));
    };
    wchar_t k[32], q[32], c[32];
    read(L"xp", L"Kills", L"1.0", k);
    read(L"xp", L"Quests", L"1.0", q);
    read(L"curve", L"LevelCost", L"0", c);
    if (!ParseMille(k, 100, 2000, kills))
        return Log("xp_scale.ini: Kills = %S is not a number from 0.1 to 2.0; nothing is patched", k), false;
    if (!ParseMille(q, 100, 2000, quests))
        return Log("xp_scale.ini: Quests = %S is not a number from 0.1 to 2.0; nothing is patched", q), false;
    if (!ParseInt(c, levelCost) || (levelCost != 0 && (levelCost < 1000 || levelCost > 10000000)))
        return Log("xp_scale.ini: LevelCost = %S is not 0 or a whole number from 1000 to 10000000; nothing is patched",
                   c),
               false;
    return true;
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
    _snwprintf_s(g_logPath, MAX_PATH, _TRUNCATE, L"%s\\riftstone\\logs\\xp_scale.log", root);
    // A fresh log per game session; a second copy of the plugin in the same process appends to it.
    wchar_t probe[8];
    if (GetEnvironmentVariableW(L"RIFTSTONE_XP_SCALE_LOG", probe, 8) == 0) {
        DeleteFileW(g_logPath);
        SetEnvironmentVariableW(L"RIFTSTONE_XP_SCALE_LOG", L"1");
    }

    // The game is a fixed-base image; a test harness that maps DDDA.exe itself sets the variable.
    bool harness = GetEnvironmentVariableW(L"RIFTSTONE_XP_SCALE_HARNESS", probe, 8) > 0;
    if (!harness && (uintptr_t)GetModuleHandleW(nullptr) != IMAGE_BASE) {
        Log("refused: the host is not DDDA.exe at 0x00400000");
        return;
    }
    uint32_t kills, quests, levelCost;
    if (!LoadSettings(self, kills, quests, levelCost)) return;
    if (kills == 1000 && quests == 1000 && levelCost == 0) {
        Log("xp_scale: Kills = 1.0, Quests = 1.0, LevelCost = 0: nothing patched");
        return;
    }
    if (!Verify()) return;
    g_kills = kills;
    g_quests = quests;
    g_levelCost = levelCost;
    if (!WriteJump(ADD_EXP, (uintptr_t)&AddExpThunk)) {
        Log("failed: VirtualProtect at 0x%08X (%lu); nothing is patched", (unsigned)ADD_EXP, GetLastError());
        return;
    }
    Log("xp_scale: Kills x%u.%03u, Quests x%u.%03u, LevelCost %u (%s)", kills / 1000, kills % 1000, quests / 1000,
        quests % 1000, levelCost, harness ? "harness" : "game");
    Log("  0x%08X  sGameSys::addExp goes through the plugin, then on at 0x%08X", (unsigned)ADD_EXP, (unsigned)RESUME);
}

}  // namespace

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(module);
        Start(module);
    }
    return TRUE;
}

// free_sprint -- Riftstone native plugin: sprinting costs no stamina while you are out of battle.
//
// What the game does (DDDA.exe build 2364871; PS3 build names)
//   Every frame uPlayerBase::before runs updateStamina (0x00B81450) for you and each pawn.  It asks
//   calcStaminaRecover (0x00B81810) for the frame's recovery and calcStaminaConsume (0x00B81920) for
//   its drain, and adds both through addStamina (0x00B81E30).  calcStaminaConsume takes the player in
//   edi (a whole-program register convention) and returns the drain, negated, in xmm0.  It switches on
//   the player's action (+0x2DD4): action 5 (cPlActDash) and 0x7D (cPlActDashBegin) share one branch
//   (0x00B81AD5) that charges the sprint rate of the PlStaminaDash tables; walking, running and waiting
//   charge nothing there, climbing and carrying have branches of their own.
//   The game's battle state is one byte of sGameSys ([0x018FA4BC] +0xBE2CC): calcEnableBattleMode
//   (0x00441650) sets it when an enemy close by is fighting, and about ninety places read it (the battle
//   music, the pawns' inclinations and their talk, whether a manual save is allowed).
//
// What this plugin does (free_sprint.ini, next to this file)
//   The call to calcStaminaConsume at 0x00B8147E goes to a thunk instead.  The thunk calls the game's
//   own calcStaminaConsume, then, when the player is sprinting (action 5 or 0x7D) and:
//     Mode = out_of_battle   the battle byte is 0,
//     Mode = always          always,
//   returns no drain.  Every register comes back exactly as the game's function left it except xmm0,
//   the drain itself.  Who = party frees you and your pawns, Who = arisen only you.  Mode = off
//   patches nothing.  A jump from a sprint (PlStaminaDashJump) is charged once by the jump itself and
//   still costs stamina; climbing a monster, carrying, and everything in battle cost what they always
//   did.
//
// Safety
//   DDDA.exe build 2364871 only.  The call site, the two functions around it, the switch, the action
//   table and the battle byte's own code are compared first; on any difference nothing is patched and
//   riftstone\logs\free_sprint.log says why.  The check itself reads memory under SEH: a fault there
//   charges the game's own drain.  Original code; no third-party source.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

namespace {

// ---- build 2364871 --------------------------------------------------------------------------
constexpr uintptr_t IMAGE_BASE = 0x00400000;
constexpr uintptr_t CALL_SITE = 0x00B8147E;          // updateStamina: call calcStaminaConsume
constexpr uintptr_t CONSUME = 0x00B81920;            // uPlayerBase::calcStaminaConsume
constexpr uintptr_t GAME_SYS = 0x018FA4BC;           // sGameSys::mpInstance
constexpr uint32_t OFF_BATTLE = 0xBE2CC;             // sGameSys: the battle state (a byte)
constexpr uintptr_t PLAYER_MANAGER = 0x018FA4EC;     // sPlayerManager::mpInstance
constexpr uint32_t OFF_ARISEN = 0x99C;               // sPlayerManager: the Arisen (the pawns follow)
constexpr uint32_t OFF_ACTION = 0x2DD4;              // uPlayerBase: the current action
constexpr uint32_t ACT_DASH = 5, ACT_DASH_BEGIN = 0x7D;
constexpr uintptr_t ACTION_TABLE = 0x01866C48;       // uPlayerBase's action table, 0x44 bytes an entry
constexpr uint32_t ACTION_ENTRY = 0x44;
constexpr uint32_t DTI_DASH = 0x019A1EA8, DTI_DASH_BEGIN = 0x019A2348;   // cPlActDash, cPlActDashBegin
constexpr uintptr_t SWITCH_INDEX = 0x00B81D4C, SWITCH_TABLE = 0x00B81D1C, DASH_BRANCH = 0x00B81AD5;

struct Expect {
    const char* what;
    uintptr_t at;
    uint8_t bytes[34];
    uint8_t len;
};

// The code the patch relies on: a second copy of the plugin, another patch or another build shows up
// here as a difference.
const Expect CONTEXT[] = {
    {"updateStamina: its start", 0x00B81450,
     {0x83, 0xEC, 0x0C, 0x56, 0x8B, 0xF0, 0x83, 0xBE, 0xEC, 0x3D, 0x00, 0x00, 0x00, 0x57}, 14},
    {"updateStamina: recovery, the player into edi, the drain (call 0x00B81920), then its use", 0x00B81471,
     {0xE8, 0x9A, 0x03, 0x00, 0x00, 0x8B, 0xFE, 0xF3, 0x0F, 0x11, 0x44, 0x24, 0x0C, 0xE8, 0x9D, 0x04, 0x00, 0x00,
      0x0F, 0x57, 0xC9, 0xF3, 0x0F, 0x11, 0x44, 0x24, 0x10}, 27},
    {"calcStaminaConsume: its start", 0x00B81920,
     {0x83, 0xEC, 0x14, 0xF6, 0x87, 0x3C, 0x27, 0x00, 0x00, 0x80, 0x0F, 0x57, 0xDB}, 13},
    {"calcStaminaConsume: the switch on the player's action (+0x2DD4)", 0x00B819FC,
     {0x8B, 0x8F, 0xD4, 0x2D, 0x00, 0x00, 0x49, 0x81, 0xF9, 0xE2, 0x00, 0x00, 0x00, 0x0F, 0x87, 0xC2, 0x01, 0x00,
      0x00, 0x0F, 0xB6, 0x89, 0x4C, 0x1D, 0xB8, 0x00, 0xFF, 0x24, 0x8D, 0x1C, 0x1D, 0xB8, 0x00}, 33},
    {"calcStaminaConsume: the sprint branch", 0x00B81AD5, {0x6A, 0x0A, 0x8B, 0xC7, 0xE8, 0x32, 0xEF, 0xFF, 0xFF}, 9},
    {"calcStaminaConsume: the negated drain returned in xmm0", 0x00B81BD1,
     {0xF3, 0x0F, 0x10, 0x44, 0x24, 0x10, 0xF3, 0x0F, 0x11, 0x87, 0xE4, 0x0E, 0x00, 0x00, 0x5E, 0x0F, 0x28, 0xC1,
      0x0F, 0x57, 0x05, 0x80, 0xC2, 0x61, 0x01, 0x5B, 0x83, 0xC4, 0x14, 0xC3}, 30},
    {"calcEnableBattleMode: reads the battle byte (+0xBE2CC)", 0x00441659, {0x80, 0xBF, 0xCC, 0xE2, 0x0B, 0x00, 0x00}, 7},
    {"calcEnableBattleMode: sets the battle byte", 0x00441688, {0x66, 0xC7, 0x87, 0xCC, 0xE2, 0x0B, 0x00, 0x01, 0x00}, 9},
    {"calcEnableBattleMode: the Arisen ([0x018FA4EC] +0x99C)", 0x0044169F,
     {0xA1, 0xEC, 0xA4, 0x8F, 0x01, 0x8B, 0x80, 0x9C, 0x09, 0x00, 0x00}, 11},
};

enum class Mode { OutOfBattle, Always, Off };

wchar_t g_logPath[MAX_PATH];
volatile LONG g_loggedFree = 0, g_loggedBattle = 0;

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
    // Both sprint actions go to the sprint branch, and they are the classes the action table names.
    const uint32_t sprints[] = {ACT_DASH, ACT_DASH_BEGIN};
    for (uint32_t action : sprints) {
        if (!Readable(SWITCH_INDEX + action - 1, 1)) return Log("refused: the switch's index table is not readable"), false;
        uint8_t k = *(const uint8_t*)(SWITCH_INDEX + action - 1);
        if (!Readable(SWITCH_TABLE + 4u * k, 4) || *(const uint32_t*)(SWITCH_TABLE + 4u * k) != DASH_BRANCH)
            return Log("refused: action 0x%X does not go to the sprint branch (0x%08X)", action, (unsigned)DASH_BRANCH),
                   false;
    }
    const uint32_t want[2][2] = {{ACT_DASH, DTI_DASH}, {ACT_DASH_BEGIN, DTI_DASH_BEGIN}};
    for (const auto& a : want) {
        uintptr_t entry = ACTION_TABLE + a[0] * ACTION_ENTRY;
        if (!Readable(entry, 4) || *(const uint32_t*)entry != a[1])
            return Log("refused: action 0x%X is not %s in the action table", a[0],
                       a[0] == ACT_DASH ? "cPlActDash" : "cPlActDashBegin"),
                   false;
    }
    return true;
}

// ---- the check the thunk runs after the game's calcStaminaConsume ------------------------------
Mode g_mode = Mode::Off;
bool g_arisenOnly = false;

int __cdecl FreeSprintHere(uint32_t player) {
    __try {
        uint32_t action = *(const uint32_t*)(player + OFF_ACTION);
        if (action != ACT_DASH && action != ACT_DASH_BEGIN) return 0;
        if (g_arisenOnly) {
            uint32_t manager = *(const uint32_t*)PLAYER_MANAGER;
            if (!manager || *(const uint32_t*)(manager + OFF_ARISEN) != player) return 0;
        }
        if (g_mode == Mode::OutOfBattle) {
            uint32_t sys = *(const uint32_t*)GAME_SYS;
            if (!sys) return 0;
            if (*(const uint8_t*)(sys + OFF_BATTLE) != 0) {
                if (!g_loggedBattle && !InterlockedExchange(&g_loggedBattle, 1))
                    Log("in battle: sprinting costs stamina as usual (logged once)");
                return 0;
            }
        }
        if (!g_loggedFree && !InterlockedExchange(&g_loggedFree, 1)) Log("sprinting freely (logged once)");
        return 1;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return 0;
    }
}

uint32_t g_consume = CONSUME;
// The game negates the drain with the sign mask at 0x0161C280, so an action that costs nothing comes
// back as -0.0f; a free sprint returns the same.
uint32_t NO_DRAIN = 0x80000000u;

// Called in place of calcStaminaConsume, with the player in edi as the game passes it.  The game's
// function runs first; every register it returns with is kept, and only xmm0 (the drain) may become 0.
__declspec(naked) void ConsumeThunk() {
    __asm {
        call dword ptr [g_consume]
        pushfd
        push eax
        push ecx
        push edx
        sub esp, 0x80
        movups [esp + 0x00], xmm0
        movups [esp + 0x10], xmm1
        movups [esp + 0x20], xmm2
        movups [esp + 0x30], xmm3
        movups [esp + 0x40], xmm4
        movups [esp + 0x50], xmm5
        movups [esp + 0x60], xmm6
        movups [esp + 0x70], xmm7
        push edi
        call FreeSprintHere
        add esp, 4
        test eax, eax
        movups xmm0, [esp + 0x00]
        movups xmm1, [esp + 0x10]
        movups xmm2, [esp + 0x20]
        movups xmm3, [esp + 0x30]
        movups xmm4, [esp + 0x40]
        movups xmm5, [esp + 0x50]
        movups xmm6, [esp + 0x60]
        movups xmm7, [esp + 0x70]
        jz keep
        movss xmm0, dword ptr [NO_DRAIN]     // -0.0f: what the game's own function returns for no drain
    keep:
        add esp, 0x80
        pop edx
        pop ecx
        pop eax
        popfd
        ret
    }
}

bool WriteCall(uintptr_t at, uintptr_t target) {
    uint8_t code[5] = {0xE8};
    int32_t rel = (int32_t)(target - (at + 5));
    memcpy(code + 1, &rel, 4);
    DWORD old;
    if (!VirtualProtect((void*)at, 5, PAGE_EXECUTE_READWRITE, &old)) return false;
    memcpy((void*)at, code, 5);
    VirtualProtect((void*)at, 5, old, &old);
    FlushInstructionCache(GetCurrentProcess(), (void*)at, 5);
    return true;
}

// [sprint] Mode and Who from the ini next to this file; out_of_battle and party when missing.
bool LoadSettings(HMODULE self, Mode& mode, bool& arisenOnly) {
    wchar_t ini[MAX_PATH];
    GetModuleFileNameW(self, ini, MAX_PATH);
    wchar_t* dot = wcsrchr(ini, L'.');
    wchar_t* slash = wcsrchr(ini, L'\\');
    if (dot && (!slash || dot > slash)) *dot = 0;
    wcscat_s(ini, L".ini");
    auto read = [&](const wchar_t* key, const wchar_t* fallback, wchar_t* v) {
        GetPrivateProfileStringW(L"sprint", key, fallback, v, 32, ini);
        wchar_t* s = v;
        while (*s == L' ' || *s == L'\t') s++;
        size_t n = wcslen(s);
        while (n && (s[n - 1] == L' ' || s[n - 1] == L'\t')) s[--n] = 0;
        if (s != v) memmove(v, s, (n + 1) * sizeof(wchar_t));
    };
    wchar_t m[32], w[32];
    read(L"Mode", L"out_of_battle", m);
    read(L"Who", L"party", w);
    if (_wcsicmp(m, L"out_of_battle") == 0) mode = Mode::OutOfBattle;
    else if (_wcsicmp(m, L"always") == 0) mode = Mode::Always;
    else if (_wcsicmp(m, L"off") == 0) mode = Mode::Off;
    else return Log("free_sprint.ini: Mode = %S is not out_of_battle, always or off; nothing is patched", m), false;
    if (_wcsicmp(w, L"party") == 0) arisenOnly = false;
    else if (_wcsicmp(w, L"arisen") == 0) arisenOnly = true;
    else return Log("free_sprint.ini: Who = %S is not party or arisen; nothing is patched", w), false;
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
    _snwprintf_s(g_logPath, MAX_PATH, _TRUNCATE, L"%s\\riftstone\\logs\\free_sprint.log", root);
    // A fresh log per game session; a second copy of the plugin in the same process appends to it.
    wchar_t probe[8];
    if (GetEnvironmentVariableW(L"RIFTSTONE_FREE_SPRINT_LOG", probe, 8) == 0) {
        DeleteFileW(g_logPath);
        SetEnvironmentVariableW(L"RIFTSTONE_FREE_SPRINT_LOG", L"1");
    }

    // The game is a fixed-base image; a test harness that maps DDDA.exe itself sets the variable.
    bool harness = GetEnvironmentVariableW(L"RIFTSTONE_FREE_SPRINT_HARNESS", probe, 8) > 0;
    if (!harness && (uintptr_t)GetModuleHandleW(nullptr) != IMAGE_BASE) {
        Log("refused: the host is not DDDA.exe at 0x00400000");
        return;
    }
    Mode mode;
    bool arisenOnly;
    if (!LoadSettings(self, mode, arisenOnly)) return;
    if (mode == Mode::Off) {
        Log("free_sprint: Mode = off, nothing patched");
        return;
    }
    if (!Verify()) return;
    g_mode = mode;
    g_arisenOnly = arisenOnly;
    if (!WriteCall(CALL_SITE, (uintptr_t)&ConsumeThunk)) {
        Log("failed: VirtualProtect at 0x%08X (%lu); nothing is patched", (unsigned)CALL_SITE, GetLastError());
        return;
    }
    Log("free_sprint: Mode = %s, Who = %s (%s): sprinting costs no stamina %s", mode == Mode::Always ? "always" :
        "out_of_battle", arisenOnly ? "arisen" : "party", harness ? "harness" : "game",
        mode == Mode::Always ? "at any time" : "while the game is not in battle");
    Log("  0x%08X  updateStamina's call to calcStaminaConsume (0x%08X) goes through the plugin", (unsigned)CALL_SITE,
        (unsigned)CONSUME);
}

}  // namespace

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(module);
        Start(module);
    }
    return TRUE;
}

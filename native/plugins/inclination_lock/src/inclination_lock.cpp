// inclination_lock -- Riftstone native plugin: your pawn's inclinations stay where you set them.
//
// What the game does
//   A pawn has nine inclinations (Scather, Medicant, Mitigator, Challenger, Utilitarian, Guardian,
//   Nexus, Pioneer, Acquisitor), each a value the game keeps at most 1000; the two highest are the
//   primary and secondary.  While your main pawn is out, sAICharacterInfo::move (0x00410B80, slot 6
//   of the vtable at 0x01559AE8) runs eleven calc* steps that add up a change for each inclination
//   (+0x24..+0x44), then after() (0x00410C90) adds each change to the pawn's value, limits it and
//   clears it.  That loop is the only place the values drift: the elixirs and the Pawn Guild set
//   them through other code (cAICharacterInfo::setInfo / returnInfo).
//   Three steps react to the Arisen's pawn order (uPlayer +0x4ABC: 9, 10, 11 in the HUD's order
//   Go!, Help!, Come!; 8 = none), each at most once per cooldown:
//     calcProtection (0x004117F0)  Guardian -4.0 for Go!, +2.67 for Help!, +4.0 for Come!
//     calcPrudent    (0x00411030)  Medicant +2.0 for Help!
//     calcCuriosity  (0x00411C90)  Pioneer +20 for Go!
//   Nexus does not follow orders.
//
// What this plugin does (inclination_lock.ini, next to this file)
//   Mode = freeze    after() skips the add (the je at 0x00410D6B becomes a jmp): no inclination
//                    drifts.  Elixirs and the Pawn Guild still change them.
//   Mode = commands  the five order checks never match (jne -> jmp at 0x004118B1, 0x004118FC,
//                    0x00411950, 0x004112AE, 0x00411D87): orders change nothing, everything else
//                    drifts as in vanilla.
//   Mode = off       nothing is patched.
//   One byte per site, written once at start-up; nothing runs per frame.
//
// Safety
//   DDDA.exe build 2364871 only.  Every site, the code around it, the vtable slot and move()'s calls
//   are compared first; on any difference nothing is patched and riftstone\logs\inclination_lock.log
//   says why.  Original code; no third-party source.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

namespace {

// ---- build 2364871 --------------------------------------------------------------------------
constexpr uintptr_t IMAGE_BASE = 0x00400000;

struct Site {
    const char* what;
    uintptr_t at;
    uint8_t from, to;
};

// Mode = freeze: after() jumps over the add of each change to the pawn's value.
const Site FREEZE[] = {
    {"after(): add each change to the pawn's inclination", 0x00410D6B, 0x74, 0xEB},
};

// Mode = commands: each order check behaves as if no order was given.
const Site COMMANDS[] = {
    {"calcProtection: Guardian for Come!", 0x004118B1, 0x75, 0xEB},
    {"calcProtection: Guardian for Help!", 0x004118FC, 0x75, 0xEB},
    {"calcProtection: Guardian for Go!", 0x00411950, 0x75, 0xEB},
    {"calcPrudent: Medicant for Help!", 0x004112AE, 0x75, 0xEB},
    {"calcCuriosity: Pioneer for Go!", 0x00411D87, 0x75, 0xEB},
};

struct Expect {
    const char* what;
    uintptr_t at;
    uint8_t bytes[16];
    uint8_t len;
};

// The code around every site, in both modes: a second copy of the plugin, another patch or
// another build shows up here as a difference.
const Expect CONTEXT[] = {
    {"after(): the pawn's value + the change (je over it when there is no value)", 0x00410D66,
     {0x8B, 0x45, 0x00, 0x85, 0xC0, 0x74, 0x2E, 0xF3, 0x0F, 0x10, 0x07, 0xF3, 0x0F, 0x58, 0x40, 0x04}, 16},
    {"after(): the store of the new value", 0x00410D96, {0xF3, 0x0F, 0x11, 0x40, 0x04}, 5},
    {"after(): the loop over nine inclinations", 0x00410DB2, {0x83, 0xFE, 0x09, 0x72, 0x99}, 5},
    {"move(): call after()", 0x00410C85, {0xE8, 0x06, 0x00, 0x00, 0x00}, 5},
    {"move(): call calcPrudent", 0x00410C24, {0xE8, 0x07, 0x04, 0x00, 0x00}, 5},
    {"move(): call calcProtection", 0x00410C3C, {0xE8, 0xAF, 0x0B, 0x00, 0x00}, 5},
    {"move(): call calcCuriosity", 0x00410C48, {0xE8, 0x43, 0x10, 0x00, 0x00}, 5},
    {"calcProtection reads the Arisen", 0x00411864, {0x8B, 0x8A, 0x9C, 0x09, 0x00, 0x00}, 6},
    {"calcProtection reads the Arisen's order", 0x00411872, {0x8B, 0x89, 0xBC, 0x4A, 0x00, 0x00}, 6},
    {"calcProtection: Come! (order 11)", 0x004118AE, {0x83, 0xF9, 0x0B, 0x75, 0x1C, 0xF3, 0x0F, 0x10, 0x48, 0x38}, 10},
    {"calcProtection: Help! (order 10)", 0x004118F9, {0x83, 0xF9, 0x0A, 0x75, 0x1C, 0xF3, 0x0F, 0x10, 0x40, 0x38}, 10},
    {"calcProtection: Go! (order 9)", 0x0041194D, {0x83, 0xF9, 0x09, 0x75, 0xF8, 0xF3, 0x0F, 0x10, 0x40, 0x38}, 10},
    {"calcPrudent: Help! (order 10)", 0x004112A7,
     {0x83, 0xBD, 0xBC, 0x4A, 0x00, 0x00, 0x0A, 0x75, 0xF0, 0xF3, 0x0F, 0x10, 0x46, 0x28}, 14},
    {"calcCuriosity: Go! (order 9)", 0x00411D84,
     {0x83, 0xF9, 0x09, 0x75, 0x5C, 0x80, 0xB8, 0x98, 0x01, 0x00, 0x00, 0x00}, 12},
    {"constructor: Guardian change for Help! (2.67)", 0x004106E8, {0xF3, 0x0F, 0x10, 0x3D, 0xC4, 0xC7, 0x61, 0x01}, 8},
};

constexpr uintptr_t VTABLE = 0x01559AE8;  // sAICharacterInfo
constexpr uint32_t SLOT_MOVE = 6;
constexpr uintptr_t MOVE = 0x00410B80;

enum class Mode { Freeze, Commands, Off };

wchar_t g_logPath[MAX_PATH];

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
    if (!Readable(VTABLE, (SLOT_MOVE + 1) * 4) || ((const uint32_t*)VTABLE)[SLOT_MOVE] != MOVE)
        return Log("refused: sAICharacterInfo's vtable does not name move() at 0x%08X", (unsigned)MOVE), false;
    return true;
}

bool Write(uintptr_t at, uint8_t value) {
    DWORD old;
    if (!VirtualProtect((void*)at, 1, PAGE_EXECUTE_READWRITE, &old)) return false;
    *(volatile uint8_t*)at = value;
    VirtualProtect((void*)at, 1, old, &old);
    FlushInstructionCache(GetCurrentProcess(), (void*)at, 1);
    return true;
}

// All of a mode's sites or none: a site that cannot be written puts the earlier ones back.
bool Patch(const Site* sites, size_t count) {
    for (size_t i = 0; i < count; i++) {
        if (!Write(sites[i].at, sites[i].to)) {
            Log("failed: VirtualProtect at 0x%08X (%lu); nothing is patched", (unsigned)sites[i].at, GetLastError());
            while (i--) Write(sites[i].at, sites[i].from);
            return false;
        }
    }
    for (size_t i = 0; i < count; i++) Log("  0x%08X  %s", (unsigned)sites[i].at, sites[i].what);
    return true;
}

// [lock] Mode from the ini next to this file; freeze when the file or the key is missing.
Mode LoadMode(HMODULE self, bool& known) {
    wchar_t ini[MAX_PATH];
    GetModuleFileNameW(self, ini, MAX_PATH);
    wchar_t* dot = wcsrchr(ini, L'.');
    wchar_t* slash = wcsrchr(ini, L'\\');
    if (dot && (!slash || dot > slash)) *dot = 0;
    wcscat_s(ini, L".ini");
    wchar_t v[32];
    GetPrivateProfileStringW(L"lock", L"Mode", L"freeze", v, 32, ini);
    wchar_t* s = v;
    while (*s == L' ' || *s == L'\t') s++;
    size_t n = wcslen(s);
    while (n && (s[n - 1] == L' ' || s[n - 1] == L'\t')) s[--n] = 0;
    known = true;
    if (_wcsicmp(s, L"freeze") == 0) return Mode::Freeze;
    if (_wcsicmp(s, L"commands") == 0) return Mode::Commands;
    if (_wcsicmp(s, L"off") != 0) {
        known = false;
        Log("inclination_lock.ini: Mode = %S is not freeze, commands or off; nothing is patched", s);
    }
    return Mode::Off;
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
    _snwprintf_s(g_logPath, MAX_PATH, _TRUNCATE, L"%s\\riftstone\\logs\\inclination_lock.log", root);
    // A fresh log per game session; a second copy of the plugin in the same process appends to it.
    wchar_t probe[8];
    if (GetEnvironmentVariableW(L"RIFTSTONE_INCLINATION_LOCK_LOG", probe, 8) == 0) {
        DeleteFileW(g_logPath);
        SetEnvironmentVariableW(L"RIFTSTONE_INCLINATION_LOCK_LOG", L"1");
    }

    // The game is a fixed-base image; a test harness that maps DDDA.exe itself sets the variable.
    bool harness = GetEnvironmentVariableW(L"RIFTSTONE_INCLINATION_HARNESS", probe, 8) > 0;
    if (!harness && (uintptr_t)GetModuleHandleW(nullptr) != IMAGE_BASE) {
        Log("refused: the host is not DDDA.exe at 0x00400000");
        return;
    }
    bool known;
    Mode mode = LoadMode(self, known);
    if (mode == Mode::Off) {
        if (known) Log("inclination_lock: Mode = off, nothing patched");
        return;
    }
    if (!Verify()) return;
    const char* where = harness ? "harness" : "game";
    if (mode == Mode::Freeze) {
        Log("inclination_lock: Mode = freeze (%s): your pawn's inclinations no longer drift; elixirs and the Pawn "
            "Guild still change them",
            where);
        Patch(FREEZE, sizeof FREEZE / sizeof FREEZE[0]);
    } else {
        Log("inclination_lock: Mode = commands (%s): Go!, Help! and Come! no longer move inclinations; the rest "
            "drifts as in vanilla",
            where);
        Patch(COMMANDS, sizeof COMMANDS / sizeof COMMANDS[0]);
    }
}

}  // namespace

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(module);
        Start(module);
    }
    return TRUE;
}

// draw_distance -- Riftstone native plugin: how far out the game shows and runs the world around you.
//
// What the game does (DDDA.exe build 2364871; docs/draw-distance.md has the evidence)
//   config.ini ViewRange (NORMAL 1, FAR 2, FARTHEST 3, kept at [0x018D1D20]+0x38) multiplies several
//   distances when they are set.  Each place picks its multiplier the same way: it loads 1.0, then
//   `cmp ViewRange, 3 / jne` over a load of the shared 3.0 at 0x01520140 (234 readers), else
//   `cmp ViewRange, 2 / jne` over a load of 2.0.
//   * Objects: sGameSys::setNoMoveDistance (0x0044D770, called by aStage::init with the stage) sets
//     mObjNoMoveDistance[3] -- on the overworld 1500 / 2500 / 100000 cm -- times the multiplier; the
//     sGameSys constructor sets the same values first.  Every object takes one of the three as its
//     display radius (mDispRadius; uOmObjBase::after and uOmSetBase::after, by a flag and its size).
//     On the overworld (stage 100) an object farther than that from the Arisen is neither drawn nor
//     updated: uOmObjBase::setRestrictDrawAndMove (0x00C51E60) and uOmSetBase::move (0x00C55380).
//   * Grass: aStage::init forces sGrass's fade band to 1700 .. 7000 cm times the multiplier.
//   * Enemies: uEnemy::setup sets mDrawDistance to its kind's distance (20 to 300 m) times the
//     multiplier, and mManageDistance 200 m beyond.  uCharacterBase::checkMoveMode gives a character
//     move mode 0 (fully active) within mDrawDistance, 1 within mManageDistance, 2 beyond; uEnemy::move
//     does not update an enemy in mode 2 (it stands frozen), and an enemy is drawn in every mode.
//   * Human enemies (uHumanEnemy) keep the uCharacterBase constructor's mDrawDistance, 10000 cm at
//     every ViewRange, unless their placement sets one; in mode 2 uNpc::move hides and stops them.
//     Only enemies and human enemies test the distance at all (mCheckMoveModeType 1).
//
// What this plugin does (draw_distance.ini, next to this file)
//   Objects = N         the display radii use N at every ViewRange: the jne before the 3.0 load
//                       becomes two nops, and that load reads the plugin's N (setNoMoveDistance and
//                       the constructor)
//   Grass = N           the same in aStage::init's grass block; N at most 3, FARTHEST's own
//   Enemies = N         the same in uEnemy::setup
//   HumanEnemies = m    metres: the constructor's load of 10000.0 reads the plugin's value instead
//   ObjectsNeverHide = 1     both display-radius tests always pass (jbe -> jmp)
//   EnemiesAlwaysActive = 1  checkMoveMode's first test always passes: mode 0 at any distance
//   0 leaves a setting to the game.  Bytes are written once at start-up; nothing runs per frame.
//
// Safety
//   DDDA.exe build 2364871 only.  Every site, the code around it, three vtable slots, three calls and
//   the four shared constants are compared first, whatever the settings; on any difference nothing
//   is patched and riftstone\logs\draw_distance.log says why (a second copy of the plugin sees its
//   own patches and refuses).  Once patched, the plugin pins itself in memory: the game's code reads
//   its numbers.  Original code; no third-party source.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <math.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define DRAW_DISTANCE_VERSION "1.0.0"

namespace {

// ---- build 2364871 --------------------------------------------------------------------------
constexpr uintptr_t IMAGE_BASE = 0x00400000;

struct Expect {
    const char* what;
    uintptr_t at;
    uint8_t len;
    uint8_t bytes[96];
};

// Checked before anything is written, whatever the settings: another build, another mod's patch in
// the same code, or a second copy of this plugin shows up here as a difference.
const Expect CONTEXT[] = {
    {"setNoMoveDistance: the multiplier by ViewRange", 0x0044D874, 49,
     {0x8B, 0x0D, 0x20, 0x1D, 0x8D, 0x01, 0x8B, 0x49, 0x38, 0xF3, 0x0F, 0x10, 0x05, 0xB8, 0x19, 0x53, 0x01,
      0x83, 0xF9, 0x03, 0x75, 0x0A, 0xF3, 0x0F, 0x10, 0x05, 0x40, 0x01, 0x52, 0x01, 0xEB, 0x0D, 0x83, 0xF9,
      0x02, 0x75, 0x08, 0xF3, 0x0F, 0x10, 0x05, 0x04, 0x9E, 0x4F, 0x01, 0xF3, 0x0F, 0x10, 0x08}},
    {"setNoMoveDistance: the store of mObjNoMoveDistance[0]", 0x0044D8B2, 8,
     {0xF3, 0x0F, 0x11, 0x8A, 0x54, 0xE4, 0x0B, 0x00}},
    {"sGameSys constructor: the multiplier by ViewRange", 0x004387BD, 47,
     {0xA1, 0x20, 0x1D, 0x8D, 0x01, 0x8B, 0x40, 0x38, 0xF3, 0x0F, 0x10, 0x05, 0xB8, 0x19, 0x53, 0x01, 0x83,
      0xF8, 0x03, 0x75, 0x0A, 0xF3, 0x0F, 0x10, 0x05, 0x40, 0x01, 0x52, 0x01, 0xEB, 0x0D, 0x83, 0xF8, 0x02,
      0x75, 0x08, 0xF3, 0x0F, 0x10, 0x05, 0x04, 0x9E, 0x4F, 0x01, 0x0F, 0x28, 0xC8}},
    {"sGameSys constructor: the store of mObjNoMoveDistance[0]", 0x004387F4, 8,
     {0xF3, 0x0F, 0x11, 0x8D, 0x54, 0xE4, 0x0B, 0x00}},
    {"aStage::init: the call of setNoMoveDistance", 0x005011B9, 17,
     {0x8B, 0x15, 0xBC, 0xA4, 0x8F, 0x01, 0x8B, 0x83, 0x24, 0x07, 0x00, 0x00, 0xE8, 0xA6, 0xC5, 0xF4, 0xFF}},
    {"aStage::init: the grass fade by ViewRange", 0x005038DB, 81,
     {0x8B, 0x15, 0x20, 0x1D, 0x8D, 0x01, 0xC6, 0x40, 0x50, 0x01, 0x8B, 0x4A, 0x38, 0xF3, 0x0F, 0x10, 0x05,
      0xB8, 0x19, 0x53, 0x01, 0x0F, 0x28, 0xC8, 0x83, 0xF9, 0x03, 0x75, 0x0A, 0xF3, 0x0F, 0x10, 0x0D, 0x40,
      0x01, 0x52, 0x01, 0xEB, 0x0D, 0x83, 0xF9, 0x02, 0x75, 0x08, 0xF3, 0x0F, 0x10, 0x0D, 0x04, 0x9E, 0x4F,
      0x01, 0x0F, 0x28, 0xD1, 0xF3, 0x0F, 0x59, 0x0D, 0x04, 0xE2, 0x52, 0x01, 0xF3, 0x0F, 0x59, 0x15, 0x64,
      0xDA, 0x61, 0x01, 0xF3, 0x0F, 0x11, 0x48, 0x58, 0xF3, 0x0F, 0x11, 0x50, 0x54}},
    {"uEnemy::setup: the type's distance by ViewRange", 0x00AA8A54, 86,
     {0xA1, 0x20, 0x1D, 0x8D, 0x01, 0x0F, 0xB6, 0x4D, 0x2D, 0xBA, 0x01, 0x00, 0x00, 0x00, 0x66, 0x89, 0x55,
      0x3A, 0x8B, 0x40, 0x38, 0xF3, 0x0F, 0x10, 0x05, 0xB8, 0x19, 0x53, 0x01, 0x83, 0xF8, 0x03, 0x75, 0x0A,
      0xF3, 0x0F, 0x10, 0x05, 0x40, 0x01, 0x52, 0x01, 0xEB, 0x0D, 0x83, 0xF8, 0x02, 0x75, 0x08, 0xF3, 0x0F,
      0x10, 0x05, 0x04, 0x9E, 0x4F, 0x01, 0xF3, 0x0F, 0x10, 0x0C, 0x8D, 0x68, 0x97, 0x4F, 0x01, 0xF3, 0x0F,
      0x59, 0xC8, 0xF3, 0x0F, 0x10, 0x85, 0x94, 0x29, 0x00, 0x00, 0xF3, 0x0F, 0x11, 0x8D, 0x84, 0x29, 0x00,
      0x00}},
    {"uCharacterBase constructor: the default mDrawDistance", 0x0084A841, 16,
     {0xF3, 0x0F, 0x10, 0x0D, 0xA8, 0x0B, 0x52, 0x01, 0xF3, 0x0F, 0x11, 0x8B, 0x84, 0x29, 0x00, 0x00}},
    {"uCharacterBase::checkMoveMode: the draw distance test", 0x00853F12, 25,
     {0xF3, 0x0F, 0x10, 0x8E, 0x84, 0x29, 0x00, 0x00, 0x0F, 0x2F, 0xC8, 0xF3, 0x0F, 0x11, 0x86, 0x8C, 0x29,
      0x00, 0x00, 0x76, 0x04, 0x33, 0xC0, 0xEB, 0x17}},
    {"uEnemy::move: the call of checkMoveMode", 0x00AA94F3, 7, {0x8B, 0xF5, 0xE8, 0xC6, 0xA8, 0xDA, 0xFF}},
    {"uOmObjBase::move: the call of setRestrictDrawAndMove", 0x00C51DBA, 7, {0x8B, 0xC6, 0xE8, 0x9F, 0x00, 0x00, 0x00}},
    {"uOmObjBase::setRestrictDrawAndMove: the display radius test", 0x00C51EE6, 44,
     {0xF3, 0x0F, 0x10, 0x9E, 0x28, 0x29, 0x00, 0x00, 0xF3, 0x0F, 0x59, 0xC9, 0xF3, 0x0F, 0x59, 0xC0, 0xF3,
      0x0F, 0x58, 0xC8, 0xF3, 0x0F, 0x59, 0xD2, 0xF3, 0x0F, 0x58, 0xCA, 0xF3, 0x0F, 0x59, 0xDB, 0x0F, 0x2F,
      0xCB, 0x76, 0x07, 0x80, 0xA6, 0x27, 0x26, 0x00, 0x00, 0xFD}},
    {"uOmSetBase::move: the display radius test", 0x00C55476, 49,
     {0xF3, 0x0F, 0x10, 0x9E, 0x30, 0x04, 0x00, 0x00, 0xF3, 0x0F, 0x59, 0xC9, 0xF3, 0x0F, 0x59, 0xC0, 0xF3,
      0x0F, 0x58, 0xC8, 0xF3, 0x0F, 0x59, 0xD2, 0xF3, 0x0F, 0x58, 0xCA, 0xF3, 0x0F, 0x59, 0xDB, 0x0F, 0x2F,
      0xCB, 0x76, 0x0C, 0x8A, 0x44, 0x24, 0x0F, 0x24, 0xFD, 0x88, 0x86, 0x48, 0x04, 0x00, 0x00}},
    // The shared constants the sites read (never written): 1.0, 2.0, 3.0 and 10000.0.
    {"the constant 1.0", 0x015319B8, 4, {0x00, 0x00, 0x80, 0x3F}},
    {"the constant 2.0", 0x014F9E04, 4, {0x00, 0x00, 0x00, 0x40}},
    {"the constant 3.0", 0x01520140, 4, {0x00, 0x00, 0x40, 0x40}},
    {"the constant 10000.0", 0x01520BA8, 4, {0x00, 0x40, 0x1C, 0x46}},
};

struct Slot {
    const char* what;
    uintptr_t vtable;
    uint32_t slot;
    uintptr_t fn;
};
const Slot SLOTS[] = {
    {"uOmObjBase's vtable names move()", 0x0160A498, 8, 0x00C51D90},
    {"uOmSetBase's vtable names move()", 0x0160A910, 8, 0x00C55380},
    {"uEnemy's vtable names setup()", 0x015DF2A8, 5, 0x00AA8830},
};

// A multiplier site: the jne after `cmp ViewRange, 3`, and the absolute operand of the 3.0 load it
// jumps over.  The jne becomes two nops, so the load runs at every ViewRange and reads our number.
struct Multiplier {
    const char* what;
    uintptr_t jne, operand;
};
const Multiplier OBJECT_SITES[] = {
    {"sGameSys::setNoMoveDistance", 0x0044D888, 0x0044D88E},
    {"the sGameSys constructor", 0x004387D0, 0x004387D6},
};
const Multiplier GRASS_SITE = {"aStage::init (grass)", 0x005038F6, 0x005038FC};
const Multiplier ENEMY_SITE = {"uEnemy::setup", 0x00AA8A74, 0x00AA8A7A};
constexpr uintptr_t HUMAN_OPERAND = 0x0084A845;  // movss xmm1, [0x01520BA8]: the constructor's 10000.0
const uint8_t JNE_3[2] = {0x75, 0x0A};
constexpr uint32_t THREE = 0x01520140, TEN_THOUSAND = 0x01520BA8;

struct Branch {
    const char* what;
    uintptr_t at;
    uint8_t from[2], to[2];
};
const Branch NEVER_HIDE[] = {
    {"uOmObjBase::setRestrictDrawAndMove", 0x00C51F09, {0x76, 0x07}, {0xEB, 0x07}},
    {"uOmSetBase::move", 0x00C55499, {0x76, 0x0C}, {0xEB, 0x0C}},
};
const Branch ALWAYS_ACTIVE = {"uCharacterBase::checkMoveMode", 0x00853F25, {0x76, 0x04}, {0x90, 0x90}};

constexpr double OBJECTS_MAX = 20, GRASS_MAX = 3, ENEMIES_MAX = 20, HUMAN_MIN_M = 100, HUMAN_MAX_M = 2000;

// ---- the numbers the patched instructions read (their addresses go into the code) --------------
float g_objects = 3.0f, g_grass = 3.0f, g_enemies = 1.0f, g_humanCm = 10000.0f;

struct Settings {
    bool enabled = true;
    double objects = 3, grass = 3, enemies = 0, humanMeters = 0;  // 0: the game's own
    bool objectsNeverHide = false, enemiesAlwaysActive = false;
};

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
            return Log("refused: %s at 0x%08X is not build 2364871's code (another build, another mod, or "
                       "already patched); nothing is patched",
                       e.what, (unsigned)e.at),
                   false;
    }
    for (const Slot& s : SLOTS) {
        uintptr_t at = s.vtable + 4 * s.slot;
        if (!Readable(at, 4) || *(const uint32_t*)at != s.fn)
            return Log("refused: %s at 0x%08X is not so; nothing is patched", s.what, (unsigned)s.fn), false;
    }
    return true;
}

// ---- the plan: every write this configuration makes, all or none ------------------------------
struct Write {
    uintptr_t at;
    uint8_t len;
    uint8_t from[4], to[4];
};

struct Plan {
    Write w[16];
    int n = 0;
    void add(uintptr_t at, const uint8_t* from, const uint8_t* to, uint8_t len) {
        Write& x = w[n++];
        x.at = at;
        x.len = len;
        memcpy(x.from, from, len);
        memcpy(x.to, to, len);
    }
    void multiplier(const Multiplier& m, const float* value) {
        const uint8_t nops[2] = {0x90, 0x90};
        add(m.jne, JNE_3, nops, 2);
        operand(m.operand, THREE, value);
    }
    void operand(uintptr_t at, uint32_t old, const float* value) {
        uint32_t neu = (uint32_t)(uintptr_t)value;
        add(at, (const uint8_t*)&old, (const uint8_t*)&neu, 4);
    }
    void branch(const Branch& b) { add(b.at, b.from, b.to, 2); }
};

bool WriteBytes(uintptr_t at, const uint8_t* bytes, size_t n) {
    DWORD old;
    if (!VirtualProtect((void*)at, n, PAGE_EXECUTE_READWRITE, &old)) return false;
    memcpy((void*)at, bytes, n);
    VirtualProtect((void*)at, n, old, &old);
    FlushInstructionCache(GetCurrentProcess(), (void*)at, n);
    return true;
}

bool Apply(const Plan& p) {
    for (int i = 0; i < p.n; i++) {
        if (!WriteBytes(p.w[i].at, p.w[i].to, p.w[i].len)) {
            Log("failed: VirtualProtect at 0x%08X (%lu); nothing is patched", (unsigned)p.w[i].at, GetLastError());
            while (i--) WriteBytes(p.w[i].at, p.w[i].from, p.w[i].len);
            return false;
        }
    }
    return true;
}

// The number in [draw] key: `def` when the key is missing or empty; 0 (the game's own) when it is not
// a number, with a line in the log.
double ReadNumber(const wchar_t* ini, const wchar_t* key, double def) {
    wchar_t buf[64];
    GetPrivateProfileStringW(L"draw", key, L"", buf, 64, ini);
    wchar_t* s = buf;
    while (*s == L' ' || *s == L'\t') s++;
    size_t n = wcslen(s);
    while (n && (s[n - 1] == L' ' || s[n - 1] == L'\t')) s[--n] = 0;
    if (!*s) return def;
    wchar_t* end = nullptr;
    double v = wcstod(s, &end);
    if (end == s || *end || !isfinite(v) || v < 0) {
        Log("draw_distance.ini: %S = %S is not a number of 0 or more; that setting is left to the game", key, s);
        return 0;
    }
    return v;
}

bool ReadSwitch(const wchar_t* ini, const wchar_t* key) {
    return GetPrivateProfileIntW(L"draw", key, 0, ini) != 0;
}

double Clamp(double v, double lo, double hi) { return v < lo ? lo : v > hi ? hi : v; }
double Multiple(double v, double hi) { return v <= 0 ? 0 : Clamp(v, 1, hi); }  // 0 stays "the game's own"

Settings LoadSettings(HMODULE self, bool& haveIni) {
    wchar_t ini[MAX_PATH];
    GetModuleFileNameW(self, ini, MAX_PATH);
    wchar_t* dot = wcsrchr(ini, L'.');
    wchar_t* slash = wcsrchr(ini, L'\\');
    if (dot && (!slash || dot > slash)) *dot = 0;
    wcscat_s(ini, L".ini");
    haveIni = GetFileAttributesW(ini) != INVALID_FILE_ATTRIBUTES;
    Settings s;
    s.enabled = GetPrivateProfileIntW(L"draw", L"Enabled", 1, ini) != 0;
    s.objects = Multiple(ReadNumber(ini, L"Objects", 3), OBJECTS_MAX);
    s.grass = Multiple(ReadNumber(ini, L"Grass", 3), GRASS_MAX);
    s.enemies = Multiple(ReadNumber(ini, L"Enemies", 0), ENEMIES_MAX);
    double human = ReadNumber(ini, L"HumanEnemies", 0);
    s.humanMeters = human <= 0 ? 0 : Clamp(human, HUMAN_MIN_M, HUMAN_MAX_M);
    s.objectsNeverHide = ReadSwitch(ini, L"ObjectsNeverHide");
    s.enemiesAlwaysActive = ReadSwitch(ini, L"EnemiesAlwaysActive");
    return s;
}

// config.ini's ViewRange, for the log only: %LOCALAPPDATA%\CAPCOM\DRAGONS DOGMA DARK ARISEN\config.ini.
void LogViewRange() {
    wchar_t local[MAX_PATH], ini[MAX_PATH], vr[32] = L"";
    if (!GetEnvironmentVariableW(L"LOCALAPPDATA", local, MAX_PATH)) return;
    _snwprintf_s(ini, MAX_PATH, _TRUNCATE, L"%s\\CAPCOM\\DRAGONS DOGMA DARK ARISEN\\config.ini", local);
    GetPrivateProfileStringW(L"GRAPHICS", L"ViewRange", L"", vr, 32, ini);
    if (!vr[0]) return;
    int m = _wcsicmp(vr, L"FARTHEST") == 0 ? 3 : _wcsicmp(vr, L"FAR") == 0 ? 2 : _wcsicmp(vr, L"NORMAL") == 0 ? 1 : 0;
    if (m)
        Log("config.ini ViewRange=%S: the game's own multiplier is %d; a setting of 0 keeps it", vr, m);
    else
        Log("config.ini ViewRange=%S", vr);
}

void Describe(char* out, size_t cap, const char* name, double v, const char* unit) {
    if (v > 0)
        _snprintf_s(out, cap, _TRUNCATE, "%s %s%.4g%s", name, unit[0] ? "" : "x", v, unit);
    else
        _snprintf_s(out, cap, _TRUNCATE, "%s the game's", name);
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
    _snwprintf_s(g_logPath, MAX_PATH, _TRUNCATE, L"%s\\riftstone\\logs\\draw_distance.log", root);
    // A fresh log per game session; a second copy of the plugin in the same process appends to it.
    wchar_t probe[8];
    if (GetEnvironmentVariableW(L"RIFTSTONE_DRAW_DISTANCE_LOG", probe, 8) == 0) {
        DeleteFileW(g_logPath);
        SetEnvironmentVariableW(L"RIFTSTONE_DRAW_DISTANCE_LOG", L"1");
    }
    Log("Riftstone draw_distance %s", DRAW_DISTANCE_VERSION);

    // The game is a fixed-base image; a test harness that maps DDDA.exe itself sets the variable.
    bool harness = GetEnvironmentVariableW(L"RIFTSTONE_DRAW_HARNESS", probe, 8) > 0;
    if (!harness && (uintptr_t)GetModuleHandleW(nullptr) != IMAGE_BASE) {
        Log("refused: the host is not DDDA.exe at 0x00400000");
        return;
    }
    bool haveIni = false;
    Settings s = LoadSettings(self, haveIni);
    if (!haveIni) Log("no draw_distance.ini next to the plugin: the defaults");
    if (!s.enabled) {
        Log("draw_distance: Enabled = 0, nothing patched");
        return;
    }
    LogViewRange();

    g_objects = (float)s.objects;
    g_grass = (float)s.grass;
    g_enemies = (float)s.enemies;
    g_humanCm = (float)(s.humanMeters * 100.0);
    Plan plan;
    if (s.objects > 0)
        for (const Multiplier& m : OBJECT_SITES) plan.multiplier(m, &g_objects);
    if (s.grass > 0) plan.multiplier(GRASS_SITE, &g_grass);
    if (s.enemies > 0) plan.multiplier(ENEMY_SITE, &g_enemies);
    if (s.humanMeters > 0) plan.operand(HUMAN_OPERAND, TEN_THOUSAND, &g_humanCm);
    if (s.objectsNeverHide)
        for (const Branch& b : NEVER_HIDE) plan.branch(b);
    if (s.enemiesAlwaysActive) plan.branch(ALWAYS_ACTIVE);
    if (plan.n == 0) {
        Log("draw_distance: every setting is the game's own, nothing patched");
        return;
    }
    if (!Verify() || !Apply(plan)) return;
    // The game's instructions now read this module's numbers: it must never be unloaded.
    HMODULE pinned;
    GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_PIN, (LPCWSTR)&g_objects,
                       &pinned);

    char a[64], b[64], c[64], d[64];
    Describe(a, sizeof a, "objects", s.objects, "");
    Describe(b, sizeof b, "grass", s.grass, "");
    Describe(c, sizeof c, "enemies", s.enemies, "");
    Describe(d, sizeof d, "human enemies", s.humanMeters, " m");
    Log("draw_distance: patched (%s), %d writes; at every ViewRange %s, %s, %s, %s; objects never hidden: %s; "
        "enemies always active: %s",
        harness ? "harness" : "game", plan.n, a, b, c, d, s.objectsNeverHide ? "yes" : "no",
        s.enemiesAlwaysActive ? "yes" : "no");
    for (int i = 0; i < plan.n; i++) {
        const Write& w = plan.w[i];
        if (w.len == 2)
            Log("  0x%08X  %02X %02X -> %02X %02X", (unsigned)w.at, w.from[0], w.from[1], w.to[0], w.to[1]);
        else
            Log("  0x%08X  reads 0x%08X instead of 0x%08X", (unsigned)w.at, *(const uint32_t*)w.to,
                *(const uint32_t*)w.from);
    }
}

}  // namespace

// For tests and a live view: the numbers the patched instructions read.
extern "C" __declspec(dllexport) void DrawDistance_Values(float* out4) {
    out4[0] = g_objects;
    out4[1] = g_grass;
    out4[2] = g_enemies;
    out4[3] = g_humanCm;
}

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(module);
        Start(module);
    }
    return TRUE;
}

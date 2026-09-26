// enemy_cap -- Riftstone native plugin: more enemies at once (the game allows 10).
//
// How the limit works (DDDA.exe build 2364871; docs/re-enemy-cap.md)
//   Every enemy the world places takes one of sSetManager's enemy slots (cUnitData, 0x20 bytes: its
//   vtable, the unit, its state, two priorities, a reservation id and its group).  The slots are a
//   fixed array of 10 at sSetManager+0x844, stage set-up makes 10 of them usable (mUnitNumEnemy,
//   +0x1B8D0), and the compiler unrolled most loops over the array, so no single number raises it.
//
// What the plugin does (sites.inc, generated from the exe by tools/enemy_cap_sites.py)
//   * The manager is allocated 0x1B950 + N x 0x20 bytes instead of 0x1B950, and the slot array moves
//     to the new tail (+0x1B950), N entries long.  Every instruction that addresses a slot (146) and
//     the four that compute a slot number from a moved pointer shift by the same 0x1B10C.
//   * Loops over the array count to N instead of 10 (8), the destructor walks N entries, and stage
//     set-up makes N slots usable.
//   * The four unrolled runs of stores -- construct, clear, reset, final -- become one call each that
//     does the same for all N slots (and, harmlessly, for the ten vanilla ones).
//   * Kept at 10 on purpose: the save file's 10 enemy records (saves stay compatible both ways), the
//     routine that limits post-Dragon Gran Soren (stage 230) to 5, and the distance-priority weight.
//
// The slot record
//   Once a second a thread reads the slots (reads only; every fault is caught) and enemy_cap.log keeps
//   what is needed after a session ends badly: each new peak, when every usable slot is taken, a
//   status line a minute when something changed, and at a normal exit the last sample.  [enemy_cap]
//   record = 0 turns it off.
//
// Safety
//   Build 2364871 only.  All 164 instructions and the four runs are compared byte for byte first, and
//   the spawn manager must not exist yet (it is built at the patched size); otherwise nothing is
//   patched and riftstone\logs\enemy_cap.log says why.  Once patched, the plugin pins itself in
//   memory: the game's code jumps into it.  Settings: riftstone\plugins\enemy_cap.ini, [enemy_cap]
//   slots = 10..64 (default 30), record = 0/1 (default 1).  Original code.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "ninput.h"  // optional Ninput host integration; this plugin still runs standalone

namespace {

constexpr uintptr_t IMAGE_BASE = 0x00400000;
constexpr uint32_t VT_UNIT_DATA = 0x01562414;           // sSetManager::cUnitData
constexpr uint32_t SLOT_SIZE = 0x20;
constexpr int VANILLA_SLOTS = 10, MIN_SLOTS = 10, MAX_SLOTS = 64, DEFAULT_SLOTS = 30;
constexpr uintptr_t SET_MANAGER = 0x018FA504;           // the sSetManager instance (0 until the game builds it)
constexpr uint32_t UNIT_NUM_ENEMY = 0x1B8D0;            // mUnitNumEnemy: the slots this stage makes usable
constexpr uintptr_t S_AREA = 0x018D099C;                // sArea; the stage as the game's reader 0x005BAF40 finds it

enum Kind { K_MOVE, K_NEG, K_COUNT, K_COUNT_M1, K_END, K_USABLE, K_ALLOC };
struct Site {
    uint32_t at;
    uint8_t len, field, size, kind;
    uint8_t code[10];
};
struct Block {
    const char* name;
    uint32_t at, back;
    const uint8_t* code;
};
#include "sites.inc"

int g_slots = DEFAULT_SLOTS;
bool g_patched = false;
wchar_t g_logPath[MAX_PATH];

void VLog(const char* fmt, va_list ap) {
    char line[512];
    int n = _vsnprintf_s(line, sizeof line, _TRUNCATE, fmt, ap);
    if (n < 0) n = (int)strlen(line);
    // Shared for writing too: a thread stopped mid-line at exit must not lock the exit line out.
    HANDLE h = CreateFileW(g_logPath, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_ALWAYS,
                           FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) return;
    DWORD w;
    WriteFile(h, line, (DWORD)n, &w, nullptr);
    WriteFile(h, "\r\n", 2, &w, nullptr);
    CloseHandle(h);
}

void Log(const char* fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    VLog(fmt, ap);
    va_end(ap);
}

void InitSlot(uint8_t* s) {                              // what the constructor writes per slot
    *(uint32_t*)s = VT_UNIT_DATA;
    memset(s + 4, 0, SLOT_SIZE - 4);
}

void ClearSlot(uint8_t* s) {                             // what clear/reset/final write per slot
    *(uint32_t*)(s + 0x04) = 0;                          // mpUnit
    *(uint32_t*)(s + 0x1C) = 0;                          // mpGroupParam
    *(uint32_t*)(s + 0x08) = 0;                          // mState
}

}  // namespace

extern "C" void __stdcall EnemyCap_Construct(uint8_t* self) {
    for (int i = 0; i < VANILLA_SLOTS; i++) InitSlot(self + SLOTS_OLD + i * SLOT_SIZE);
    for (int i = 0; i < g_slots; i++) InitSlot(self + SLOTS_NEW + i * SLOT_SIZE);
}

extern "C" void __stdcall EnemyCap_Clear(uint8_t* self) {
    for (int i = 0; i < VANILLA_SLOTS; i++) ClearSlot(self + SLOTS_OLD + i * SLOT_SIZE);
    for (int i = 0; i < g_slots; i++) ClearSlot(self + SLOTS_NEW + i * SLOT_SIZE);
}

// Each run is replaced by a jump here; the thunk keeps every register and the flags, calls the
// function with the manager (edi in the constructor, esi in the others) and resumes after the run.
uintptr_t g_backConstruct, g_backClear, g_backReset, g_backFinal;

__declspec(naked) static void ThunkConstruct() {
    __asm {
        pushfd
        pushad
        push edi
        call EnemyCap_Construct
        popad
        popfd
        jmp dword ptr [g_backConstruct]
    }
}
__declspec(naked) static void ThunkClear() {
    __asm {
        pushfd
        pushad
        push esi
        call EnemyCap_Clear
        popad
        popfd
        jmp dword ptr [g_backClear]
    }
}
__declspec(naked) static void ThunkReset() {
    __asm {
        pushfd
        pushad
        push esi
        call EnemyCap_Clear
        popad
        popfd
        jmp dword ptr [g_backReset]
    }
}
__declspec(naked) static void ThunkFinal() {
    __asm {
        pushfd
        pushad
        push esi
        call EnemyCap_Clear
        popad
        popfd
        jmp dword ptr [g_backFinal]
    }
}

namespace {

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
    for (const Site& s : SITES) {
        if (!Readable(s.at, s.len) || memcmp((const void*)(uintptr_t)s.at, s.code, s.len) != 0)
            return Log("refused: the instruction at 0x%08X is not build 2364871's (another build, or already patched)",
                       (unsigned)s.at), false;
    }
    for (const Block& b : BLOCKS) {
        if (!Readable(b.at, b.back - b.at) || memcmp((const void*)(uintptr_t)b.at, b.code, b.back - b.at) != 0)
            return Log("refused: the %s run at 0x%08X is not build 2364871's", b.name, (unsigned)b.at), false;
    }
    return true;
}

uint32_t NewValue(const Site& s) {
    uint32_t old = s.size == 1 ? *(const uint8_t*)(uintptr_t)(s.at + s.field)
                               : *(const uint32_t*)(uintptr_t)(s.at + s.field);
    switch (s.kind) {
        case K_MOVE: return old + DELTA;
        case K_NEG: return old - DELTA;
        case K_COUNT: case K_USABLE: return (uint32_t)g_slots;
        case K_COUNT_M1: return (uint32_t)g_slots - 1;
        case K_END: return SLOTS_NEW + (uint32_t)g_slots * SLOT_SIZE;
        case K_ALLOC: return MANAGER_SIZE + (uint32_t)g_slots * SLOT_SIZE;
    }
    return old;
}

bool Patch() {
    g_backConstruct = BLOCKS[0].back;
    g_backClear = BLOCKS[1].back;
    g_backReset = BLOCKS[2].back;
    g_backFinal = BLOCKS[3].back;
    void* const thunks[4] = {(void*)ThunkConstruct, (void*)ThunkClear, (void*)ThunkReset, (void*)ThunkFinal};
    // One protection change over the whole span, so a failure leaves the code untouched.
    uintptr_t lo = ~(uintptr_t)0, hi = 0;
    for (const Site& s : SITES) lo = min(lo, (uintptr_t)s.at), hi = max(hi, (uintptr_t)s.at + s.len);
    for (const Block& b : BLOCKS) lo = min(lo, (uintptr_t)b.at), hi = max(hi, (uintptr_t)b.back);
    DWORD old;
    if (!VirtualProtect((void*)lo, hi - lo, PAGE_EXECUTE_READWRITE, &old))
        return Log("failed: VirtualProtect 0x%08X..0x%08X (%lu); nothing patched", (unsigned)lo, (unsigned)hi,
                   GetLastError()), false;
    for (const Site& s : SITES) {
        uint32_t v = NewValue(s);
        if (s.size == 1) *(uint8_t*)(uintptr_t)(s.at + s.field) = (uint8_t)v;
        else *(uint32_t*)(uintptr_t)(s.at + s.field) = v;
    }
    for (int i = 0; i < 4; i++) {
        uint8_t* p = (uint8_t*)(uintptr_t)BLOCKS[i].at;
        p[0] = 0xE9;
        *(int32_t*)(p + 1) = (int32_t)((uintptr_t)thunks[i] - (BLOCKS[i].at + 5));
        memset(p + 5, 0xCC, BLOCKS[i].back - BLOCKS[i].at - 5);   // never reached
    }
    VirtualProtect((void*)lo, hi - lo, old, &old);
    FlushInstructionCache(GetCurrentProcess(), (void*)lo, hi - lo);
    return true;
}

// ---- the slot record ----------------------------------------------------------------------------
// A slot is in use when its state is set (the test the game's own registerEmData makes for a free
// slot, 0x004A6997) and has a unit once the enemy exists; the stage is read the way 0x005BAF40 does.
struct Sample {
    int inUse, units, usable, stage;
};
bool g_record = true;
Sample g_last = {-1, -1, -1, -1}, g_status = {-1, -1, -1, -1};
char g_lastAt[16] = "", g_peakAt[16] = "";
int g_peak = 0, g_peakStage = -1, g_lines = 0;
bool g_full = false;
DWORD g_fullSince = 0, g_fullMs = 0, g_fullLineAt = 0, g_statusAt = 0;
constexpr int MAX_LINES = 3000;                          // a session's record stays small

void Stamp(char* out, size_t cap) {
    SYSTEMTIME t;
    GetLocalTime(&t);
    _snprintf_s(out, cap, _TRUNCATE, "%02u:%02u:%02u", (unsigned)t.wHour, (unsigned)t.wMinute, (unsigned)t.wSecond);
}

void Record(const char* fmt, ...) {
    if (++g_lines > MAX_LINES) return;
    va_list ap;
    va_start(ap, fmt);
    VLog(fmt, ap);
    va_end(ap);
}

bool ReadSlotsUnsafe(Sample* s) {
    const uintptr_t mgr = *(const uintptr_t*)SET_MANAGER;
    if (!mgr || !Readable(mgr + UNIT_NUM_ENEMY, 4) || !Readable(mgr + SLOTS_NEW, (size_t)g_slots * SLOT_SIZE))
        return false;
    int inUse = 0, units = 0;
    for (int i = 0; i < g_slots; i++) {
        const uint8_t* p = (const uint8_t*)(mgr + SLOTS_NEW + (uint32_t)i * SLOT_SIZE);
        if (*(const uint32_t*)p != VT_UNIT_DATA) return false;   // not a manager built at the patched size
        if (*(const uint32_t*)(p + 8)) inUse++;
        if (*(const uint32_t*)(p + 4)) units++;
    }
    s->inUse = inUse;
    s->units = units;
    s->usable = *(const int*)(mgr + UNIT_NUM_ENEMY);
    s->stage = -1;
    const uintptr_t area = *(const uintptr_t*)S_AREA;
    if (area && Readable(area + 0x3834, 4)) {
        const uintptr_t now = *(const uintptr_t*)(area + 0x3834);
        if (now && Readable(now, 0x728) && *(const uint8_t*)(now + 0x20)) s->stage = *(const int*)(now + 0x724);
    }
    return true;
}

// Reads only; a manager freed between two reads is a missed sample, never a fault in the game.
bool ReadSlots(Sample* s) {
    if (!Readable(SET_MANAGER, 4) || !Readable(S_AREA, 4)) return false;
    __try {
        return ReadSlotsUnsafe(s);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return false;
    }
}

// One sample: the slots in use, or -1 when there is no manager to read.
int Tick() {
    Sample s;
    if (!ReadSlots(&s)) return -1;
    char at[16];
    Stamp(at, sizeof at);
    const DWORD now = GetTickCount();
    if (s.inUse > g_peak) {
        g_peak = s.inUse;
        g_peakStage = s.stage;
        strcpy_s(g_peakAt, at);
        Record("%s  peak: %d of %d slots in use (%d with a unit), stage %d", at, s.inUse, s.usable, s.units, s.stage);
    }
    const bool full = s.usable > 0 && s.inUse >= s.usable;
    if (full && !g_full) {
        g_fullSince = now;
        if (!g_fullLineAt || now - g_fullLineAt >= 30000) {
            g_fullLineAt = now;
            Record("%s  all %d slots in use, stage %d: more enemies wait for a free slot", at, s.usable, s.stage);
        }
    } else if (!full && g_full) {
        const DWORD spell = now - g_fullSince;
        g_fullMs += spell;
        if (spell >= 5000) Record("%s  a slot is free again after %lu s with all in use", at, spell / 1000);
    }
    g_full = full;
    if ((!g_statusAt || now - g_statusAt >= 60000) && (s.inUse != g_status.inUse || s.stage != g_status.stage)) {
        g_statusAt = now;
        g_status = s;
        Record("%s  %d of %d slots in use (%d with a unit), stage %d", at, s.inUse, s.usable, s.units, s.stage);
    }
    g_last = s;
    strcpy_s(g_lastAt, at);
    return s.inUse;
}

DWORD WINAPI Recorder(LPVOID) {
    for (;;) {
        Sleep(1000);
        Tick();
    }
}

// At a normal exit (a quit, a closed window, or the game's own fatal-error box, which ends in
// exit(1)); a crash never gets here, and the loader's crash report covers that.
void ExitLine() {
    char at[16];
    Stamp(at, sizeof at);
    if (g_full) g_fullMs += GetTickCount() - g_fullSince;
    if (g_last.inUse < 0) {
        Log("%s  the game is exiting normally (a quit, a closed window or a fatal-error box; not a crash); "
            "no enemy slots were read this session", at);
        return;
    }
    Log("%s  the game is exiting normally (a quit, a closed window or a fatal-error box; not a crash). "
        "Last sample %s: %d of %d slots in use (%d with a unit), stage %d. Peak %d at %s, stage %d. "
        "All slots in use for %lu s in total",
        at, g_lastAt, g_last.inUse, g_last.usable, g_last.units, g_last.stage, g_peak, g_peakAt, g_peakStage,
        g_fullMs / 1000);
}

void ReadSettings(HMODULE self) {
    wchar_t ini[MAX_PATH];
    GetModuleFileNameW(self, ini, MAX_PATH);
    wchar_t* dot = wcsrchr(ini, L'.');
    if (dot) wcscpy_s(dot, MAX_PATH - (dot - ini), L".ini");
    int n = (int)GetPrivateProfileIntW(L"enemy_cap", L"slots", DEFAULT_SLOTS, ini);
    if (n < MIN_SLOTS || n > MAX_SLOTS) {
        Log("slots = %d is outside %d..%d; using %d", n, MIN_SLOTS, MAX_SLOTS, n < MIN_SLOTS ? MIN_SLOTS : MAX_SLOTS);
        n = n < MIN_SLOTS ? MIN_SLOTS : MAX_SLOTS;
    }
    g_slots = n;
    g_record = GetPrivateProfileIntW(L"enemy_cap", L"record", 1, ini) != 0;
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
    _snwprintf_s(g_logPath, MAX_PATH, _TRUNCATE, L"%s\\riftstone\\logs\\enemy_cap.log", root);
    DeleteFileW(g_logPath);

    // The game is a fixed-base image; a test harness that maps DDDA.exe itself sets the variable.
    wchar_t probe[8];
    bool harness = GetEnvironmentVariableW(L"RIFTSTONE_CAP_HARNESS", probe, 8) > 0;
    if (!harness && (uintptr_t)GetModuleHandleW(nullptr) != IMAGE_BASE) {
        Log("refused: the host is not DDDA.exe at 0x00400000");
        return;
    }
    ReadSettings(self);
    if (!Verify()) return;
    // The manager is allocated once, at start-up; one built before the patches has only ten slots,
    // and the moved references would run past it.  Loaded by Riftstone's loader, it never exists yet.
    if (!Readable(SET_MANAGER, 4) || *(const uint32_t*)SET_MANAGER != 0) {
        Log("refused: the game's spawn manager already exists (the plugin was loaded too late); nothing patched");
        return;
    }
    if (!Patch()) return;
    g_patched = true;
    HMODULE pinned;                                      // the game's code now jumps into this module
    GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_PIN,
                       (LPCWSTR)(void*)&EnemyCap_Construct, &pinned);
    Log("enemy_cap: %d enemies at once (the game's limit is %d); %u sites and %u runs patched (%s); slot record %s",
        g_slots, VANILLA_SLOTS, (unsigned)(sizeof SITES / sizeof SITES[0]), (unsigned)(sizeof BLOCKS / sizeof BLOCKS[0]),
        harness ? "harness" : "game", g_record ? "on" : "off");
    // The harness takes samples itself (EnemyCap_Sample), so its results do not depend on timing.
    if (g_record && !harness) {
        if (HANDLE t = CreateThread(nullptr, 0, Recorder, nullptr, 0, nullptr)) CloseHandle(t);
    }
}

}  // namespace

// For the harness and the loader's live view: the slot count in effect (0 when the plugin refused).
extern "C" __declspec(dllexport) int EnemyCap_Slots() { return g_patched ? g_slots : 0; }

// One sample of the slot record now: the slots in use, or -1 (not patched, record off, no manager).
extern "C" __declspec(dllexport) int EnemyCap_Sample() { return g_patched && g_record ? Tick() : -1; }

// ---- Ninput engine provider (optional) ----------------------------------------------------------
// When Ninput hosts this plugin it calls Ninput_Initialize, and nyr->engine->set_enemy_cap then
// routes here. enemy_cap rewrites code once, so a change after it has applied cannot be undone: the
// same value is accepted idempotently, a different one is refused (ALREADY_APPLIED). Standalone (no
// Ninput) the cap is applied from the ini at DllMain, exactly as before; this only adds the route.
extern "C" int NinputProvide_EnemyCap(int slots) {
    if (g_patched) return slots == g_slots ? NINPUT_OK : NINPUT_ERR_ALREADY_APPLIED;
    if (slots < MIN_SLOTS || slots > MAX_SLOTS) return NINPUT_ERR_BADARG;
    g_slots = slots;
    if (!Verify()) return NINPUT_ERR_HOOK_FAILED;
    if (!Readable(SET_MANAGER, 4) || *(const uint32_t*)SET_MANAGER != 0) {
        Log("refused: the game's spawn manager already exists (Ninput set_enemy_cap called too late)");
        return NINPUT_ERR_HOOK_FAILED;
    }
    if (!Patch()) return NINPUT_ERR_HOOK_FAILED;
    g_patched = true;
    HMODULE pinned;
    GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_PIN,
                       (LPCWSTR)(void*)&EnemyCap_Construct, &pinned);
    Log("enemy_cap: %d enemies at once via Ninput set_enemy_cap", g_slots);
    if (g_record) {
        if (HANDLE t = CreateThread(nullptr, 0, Recorder, nullptr, 0, nullptr)) CloseHandle(t);
    }
    return NINPUT_OK;
}

extern "C" __declspec(dllexport) int Ninput_Initialize(const NinputInterface* nyr) {
    if (!nyr || nyr->abi_version != NINPUT_ABI_VERSION || !nyr->engine) return 0;
    nyr->engine->provide_enemy_cap(NinputProvide_EnemyCap);  // DllMain may already have applied the ini value
    return 1;
}

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID reserved) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(module);
        Start(module);
    } else if (reason == DLL_PROCESS_DETACH && reserved != nullptr && g_patched && g_record) {
        ExitLine();                                      // the process is ending through ExitProcess
    }
    return TRUE;
}

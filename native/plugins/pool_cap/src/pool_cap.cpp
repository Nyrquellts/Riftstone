// pool_cap -- Riftstone native plugin: bigger memory pools for the game's units, physics, collision, effects
// and arrays, and a count of every request a pool refused.
//
// How the limit works (DDDA.exe build 2364871; docs/re-memory-pools.md)
//   Every engine object is allocated from the allocator its class's MtDTI names: an index (DTI+0x18 bits
//   23..28) into a table of 64 allocators at 0x01876628 (getAllocator 0x00CF5E20).  A CRT initialiser
//   (0x0137E090) points all 64 at the static MtDefaultAllocator; WinMain (0x00407A87) then builds eight
//   MtScalableAllocator pools (0x00740590), each one VirtualAlloc(MEM_COMMIT) of a fixed size taken once,
//   and maps class names to slots (0x00740860).  "Unit", 64 MiB, serves slot 12 (cUnit and every class
//   below it: each enemy, player, model, rigid body, ragdoll) and slot 17 (nAI: every AI class).  A pool
//   never grows: a request it cannot place is refused (NULL).  The ragdoll setup (0x01083260) takes its
//   four body arrays from the Unit pool and leaves the ragdoll with no body data when one is refused; in
//   the owner's session of 2026-10-06 (62 enemies at once, enemy_cap at 64) the loader's ragdoll guard
//   answered about 19,000 walks with no body data in 7 s.  Whether the pool was full then is UNKNOWN: the
//   log this plugin writes says it.
//
// What the plugin does
//   * The eight "push size" instructions of 0x00740590 get the sizes of pool_cap.ini (in MiB; default
//     four times the game's for the five pools whose contents grow with the enemies, the game's own for
//     Temp, System and GUI).  Each is compared with the pool's name and type first.
//   * The pools' vtable (0x0155AD4C): its init (slot 12) is wrapped, so a pool that cannot get its larger
//     size (VirtualAlloc refused) is made again at the game's size instead of empty; its two allocation
//     entries (slots 6 and 13: every request of every MtScalableAllocator goes through them) are wrapped
//     to count what each pool refused, the largest refusal, and how deep into the pool the requests it
//     gave reach.  riftstone\logs\pool_cap.log gets the first refusals of each pool with the game code
//     that asked, a line each time a pool's count doubles or its depth passes a quarter, and a summary at
//     exit.  A refusal whose request came from the ragdoll setup is also counted as the setup's (each one a
//     ragdoll left with no body data).
//
// Safety
//   Build 2364871 only.  Every site is compared byte for byte first (the eight size pushes with the pool
//   names and types beside them, the three vtable entries and the code they point to), and the pools must
//   not exist yet (the table's Unit slot is empty or still the default allocator); otherwise nothing is
//   patched and the log says why.  Once patched, the plugin pins itself in memory: the game calls into it.
//   Settings: riftstone\plugins\pool_cap.ini, [pool_cap].  Original code.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <intrin.h>

#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

namespace {

constexpr uintptr_t IMAGE_BASE = 0x00400000;
constexpr uint32_t MIB = 1024 * 1024;
constexpr uint32_t TOTAL_MAX_MIB = 1280;                 // all eight pools together, at most

struct Pool {
    const char* key;          // the ini key
    const char* name;         // the game's own name for it (its allocator's, which has quotes: "Unit")
    uint32_t push;            // the "push size" instruction of 0x00740590
    uint32_t nameAt;          // the name string it pushes
    uint8_t type;             // the type it pushes (0 or 3)
    uint32_t gameMiB, defaultMiB, maxMiB;
};
// In the order 0x00740590 builds them (slot: the allocator table's index that gets the pool).
const Pool POOLS[] = {
    {"temp", "Temp", 0x007405CB, 0x0158B6CC, 0, 64, 64, 256},                   // slot 5
    {"system", "System", 0x0074061A, 0x0158B6D4, 0, 64, 64, 256},               // slots 11, 16
    {"unit", "Unit", 0x00740669, 0x0158B6E0, 3, 64, 256, 1024},                 // slots 12, 17 (and 25 until AIWork)
    {"effect", "Effect", 0x007406B8, 0x0158B6E8, 3, 5, 20, 128},                // slot 18
    {"gui", "GUI", 0x00740707, 0x0158B6F4, 3, 5, 5, 64},                        // slot 19
    {"array_string", "Array/String", 0x00740756, 0x0158B6FC, 3, 6, 24, 128},    // slots 3, 2, 13
    {"collision", "Collision", 0x007407A5, 0x0158B70C, 3, 24, 96, 512},         // slot 4
    {"physics", "Physics", 0x007407F4, 0x0158B718, 3, 12, 48, 256},             // slot 15
};
constexpr int NPOOLS = sizeof POOLS / sizeof POOLS[0];

constexpr uintptr_t TABLE_UNIT = 0x01876658;             // the allocator table's slot 12
constexpr uint32_t DEFAULT_ALLOCATOR = 0x01876840;       // the static MtDefaultAllocator the CRT puts in every slot
constexpr uintptr_t VTABLE = 0x0155AD4C;                 // MtScalableAllocator's
constexpr uintptr_t VT_ALLOC = VTABLE + 6 * 4, VT_INIT = VTABLE + 12 * 4, VT_ALLOC_IN = VTABLE + 13 * 4;
constexpr uint32_t FN_ALLOC = 0x00D10190, FN_INIT = 0x00D0FA50, FN_ALLOC_IN = 0x00D10340;
constexpr uint32_t POOL_BASE = 0x5C, POOL_END = 0x60, POOL_NAME = 0x11;   // MtScalableAllocator fields
constexpr int FIRST_LOGGED = 8;                          // refusals logged one by one, per pool
constexpr uint32_t TEXT_LO = 0x00401000, TEXT_HI = 0x0139CDFB;    // DDDA.exe's .text

// The code the vtable entries point to, as build 2364871 has it (their first instructions).
const uint8_t CODE_ALLOC[] = {0x56, 0x8B, 0xF1, 0x83, 0x7E, 0x5C, 0x00, 0x75, 0x06, 0x33, 0xC0, 0x5E, 0xC2, 0x10, 0x00};
const uint8_t CODE_ALLOC_IN[] = {0x56, 0x8B, 0xF1, 0x83, 0x7E, 0x5C, 0x00, 0x75, 0x06, 0x33, 0xC0, 0x5E, 0xC2, 0x14, 0x00};
const uint8_t CODE_INIT[] = {0x83, 0xEC, 0x24, 0x8B, 0x44, 0x24, 0x34, 0x8B, 0x54, 0x24, 0x28, 0x56, 0x57};

struct State {
    void* self;               // the pool's allocator, once the game made it
    uint32_t base, end;       // its memory
    uint32_t mib;             // the size it got (MiB)
    volatile LONG refused, logged;
    volatile LONG largest;    // the largest request it refused (bytes)
    volatile LONG deepest;    // how far into the pool the requests it gave reach (bytes)
    volatile LONG quarter;    // the last quarter of the pool reported
};
State g_state[NPOOLS];
uint32_t g_mib[NPOOLS];
volatile LONG g_otherRefused = 0;
volatile LONG g_ragdollRefused = 0;     // refusals the ragdoll setup was given (any pool)
bool g_patched = false;
ULONGLONG g_start = 0;
wchar_t g_logPath[MAX_PATH];

typedef void*(__fastcall* AllocFn)(void* self, void* edx, uint32_t size, uint32_t a, uint32_t b, uint32_t c);
typedef void*(__fastcall* AllocInFn)(void* self, void* edx, uint32_t size, uint32_t heap, uint32_t a, uint32_t b,
                                     uint32_t c);
typedef void(__fastcall* InitFn)(void* self, void* edx, const char* name, uint32_t type, uint32_t size, uint32_t opt,
                                 uint32_t grain, uint32_t extra);
AllocFn g_alloc = (AllocFn)(uintptr_t)FN_ALLOC;
AllocInFn g_allocIn = (AllocInFn)(uintptr_t)FN_ALLOC_IN;
InitFn g_init = (InitFn)(uintptr_t)FN_INIT;
typedef LPVOID(WINAPI* VirtualAllocFn)(LPVOID at, SIZE_T size, DWORD type, DWORD protect);
constexpr uintptr_t IAT_VIRTUAL_ALLOC = 0x0139D1B4;       // DDDA.exe's import of kernel32!VirtualAlloc

void VLog(const char* fmt, va_list ap) {
    char line[640];
    int n = _vsnprintf_s(line, sizeof line, _TRUNCATE, fmt, ap);
    if (n < 0) n = (int)strlen(line);
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

double Seconds() { return (double)(GetTickCount64() - g_start) / 1000.0; }
double MiB(uint32_t bytes) { return (double)bytes / MIB; }

int FindPool(const void* self) {
    for (int i = 0; i < NPOOLS; i++)
        if (g_state[i].self == self) return i;
    return -1;
}

void RaiseTo(volatile LONG* at, LONG value) {          // *at = max(*at, value), from any thread
    LONG seen = *at;
    while (value > seen) {
        LONG was = InterlockedCompareExchange(at, value, seen);
        if (was == seen) return;
        seen = was;
    }
}

// A value on the stack that is a return address into the game's code: the instruction before it is a call.
bool AfterCall(uint32_t a) {
    if (a < TEXT_LO + 7 || a >= TEXT_HI) return false;
    const uint8_t* p = (const uint8_t*)(uintptr_t)a;
    if (p[-5] == 0xE8) return true;                                                  // call rel32
    if (p[-6] == 0xFF && (p[-5] == 0x15 || (p[-5] & 0xF8) == 0x90)) return true;     // call [abs] / [reg+disp32]
    if (p[-7] == 0xFF && p[-6] == 0x94) return true;                                 // call [sib+disp32]
    if (p[-3] == 0xFF && (p[-2] & 0xF8) == 0x50) return true;                        // call [reg+disp8]
    if (p[-4] == 0xFF && p[-3] == 0x54) return true;                                 // call [sib+disp8]
    if (p[-2] == 0xFF && ((p[-1] & 0xF8) == 0xD0 || ((p[-1] & 0xF8) == 0x10 && (p[-1] & 7) != 4 && (p[-1] & 7) != 5)))
        return true;                                                                 // call reg / [reg]
    return false;
}

// The game code that asked, as the stack shows it: the return addresses into DDDA.exe's code from the
// wrapper's own return address up (likely callers, as a stack scan finds them).  The entries that only pass
// a request on are left out: MtAllocator's alloc(size, align) 0x00CF53E0 and alloc(size, align, tag)
// 0x00CF5400 (vtable slots 8 and 7, both calling slot 6) and operator new 0x00D0F2B0.  The first one left
// is the requester.
bool PassesOn(uint32_t a) { return (a >= 0x00CF53E0 && a < 0x00CF5420) || (a >= 0x00D0F2B0 && a < 0x00D0F2D8); }

uint32_t Callers(const uint32_t* from, char* out, size_t cap) {
    const uint32_t* top = (const uint32_t*)(uintptr_t)__readfsdword(4);              // the thread's stack base
    size_t used = 0;
    int shown = 0;
    uint32_t seen[5], requester = 0;
    out[0] = 0;
    for (int k = 0; k < 160 && from + k < top && shown < 5; k++) {
        const uint32_t a = from[k];
        if (!AfterCall(a) || PassesOn(a)) continue;
        bool again = false;
        for (int j = 0; j < shown; j++) again |= seen[j] == a;
        if (again) continue;
        if (!requester) requester = a;
        int w = _snprintf_s(out + used, cap - used, _TRUNCATE, "%s0x%08X", shown ? " " : "", (unsigned)a);
        if (w < 0) break;
        used += (size_t)w;
        seen[shown++] = a;
    }
    if (!shown) strcpy_s(out, cap, "(no return address into the game's code)");
    return requester;
}

// The ragdoll setup (0x01083260..0x0108350A): its four body arrays come from the allocator uRigidBody's DTI
// names (the Unit pool); when one is refused it frees the others and leaves the ragdoll with no body data.
constexpr uint32_t RAGDOLL_SETUP = 0x01083260, RAGDOLL_SETUP_END = 0x0108350A;
bool FromRagdollSetup(uint32_t requester) { return requester >= RAGDOLL_SETUP && requester < RAGDOLL_SETUP_END; }

void Refused(void* self, uint32_t size, int heap, const uint32_t* from) {
    const int i = FindPool(self);
    if (i < 0) {                                   // another MtScalableAllocator (AIWork, ...): counted only
        LONG n = InterlockedIncrement(&g_otherRefused);
        if (n <= FIRST_LOGGED) {
            char name[0x20];
            memcpy(name, (const char*)self + POOL_NAME, sizeof name);
            name[sizeof name - 1] = 0;
            Log("+%.1f s: the allocator %s refused %u bytes (refusal %ld of pools pool_cap does not size)",
                Seconds(), name, (unsigned)size, n);
        }
        return;
    }
    State& s = g_state[i];
    const LONG n = InterlockedIncrement(&s.refused);
    RaiseTo(&s.largest, (LONG)size);
    char callers[96];
    const uint32_t requester = Callers(from, callers, sizeof callers);
    const bool ragdoll = FromRagdollSetup(requester);
    const LONG r = ragdoll ? InterlockedIncrement(&g_ragdollRefused) : 0;
    if (InterlockedIncrement(&s.logged) <= FIRST_LOGGED) {
        Log("+%.1f s: the %s pool (%u MiB) refused %u bytes%s (refusal %ld); asked by %s%s", Seconds(), POOLS[i].name,
            (unsigned)s.mib, (unsigned)size, heap >= 0 ? " for a chosen heap" : "", n, callers,
            ragdoll ? ": the ragdoll setup, so a ragdoll has no body data" : "");
    } else if ((n & (n - 1)) == 0) {
        Log("+%.1f s: the %s pool (%u MiB) has refused %ld requests (the largest %ld bytes)", Seconds(), POOLS[i].name,
            (unsigned)s.mib, n, s.largest);
    }
    if (ragdoll && r > 1 && (r & (r - 1)) == 0)
        Log("+%.1f s: the ragdoll setup has been refused %ld times (each a ragdoll left with no body data)", Seconds(), r);
}

void Given(void* self, void* p, uint32_t size) {
    const int i = FindPool(self);
    if (i < 0) return;
    State& s = g_state[i];
    const uint32_t at = (uint32_t)(uintptr_t)p;
    if (at < s.base || at >= s.end) return;
    const uint32_t reach = (at - s.base) + size;
    if ((LONG)reach <= s.deepest) return;
    RaiseTo(&s.deepest, (LONG)reach);
    const uint32_t span = s.end - s.base;
    const LONG quarter = span ? (LONG)((uint64_t)reach * 4 / span) : 0;   // 1..4: past 25 %, 50 %, 75 %, the end
    LONG seen = s.quarter;
    while (quarter > seen) {
        LONG was = InterlockedCompareExchange(&s.quarter, quarter, seen);
        if (was == seen) {
            Log("+%.1f s: the %s pool's requests reach %.1f of its %u MiB", Seconds(), POOLS[i].name, MiB(reach),
                (unsigned)s.mib);
            break;
        }
        seen = was;
    }
}

// MtScalableAllocator's two allocation entries, wrapped (both __thiscall, the callee cleans the stack).
void* __fastcall WrapAlloc(void* self, void* edx, uint32_t size, uint32_t a, uint32_t b, uint32_t c) {
    void* p = g_alloc(self, edx, size, a, b, c);
    if (p) Given(self, p, size);
    else Refused(self, size, -1, (const uint32_t*)_AddressOfReturnAddress());
    return p;
}
void* __fastcall WrapAllocIn(void* self, void* edx, uint32_t size, uint32_t heap, uint32_t a, uint32_t b, uint32_t c) {
    void* p = g_allocIn(self, edx, size, heap, a, b, c);
    if (p) Given(self, p, size);
    else Refused(self, size, (int)heap, (const uint32_t*)_AddressOfReturnAddress());
    return p;
}

// Its init, wrapped: the eight pools are told apart by the name the game passes, and a pool the process
// cannot give its larger size is made at the game's own.  The init registers the allocator (0x00CF5450,
// one of 64 ids), so it must run once: the size is tried first, committed and freed again.
void __fastcall WrapInit(void* self, void* edx, const char* name, uint32_t type, uint32_t size, uint32_t opt,
                         uint32_t grain, uint32_t extra) {
    int i = -1;
    for (int k = 0; k < NPOOLS; k++)
        if ((uint32_t)(uintptr_t)name == POOLS[k].nameAt && size == g_mib[k] * MIB) i = k;
    uint32_t mib = i >= 0 ? g_mib[i] : 0;
    if (i >= 0 && mib != POOLS[i].gameMiB) {
        // Asked the way the game's own getmem (0x00CF5530) will ask, through the exe's import.
        const VirtualAllocFn virtualAlloc = *(const VirtualAllocFn*)IAT_VIRTUAL_ALLOC;
        void* probe = virtualAlloc(nullptr, size, MEM_COMMIT, PAGE_READWRITE);
        if (probe) {
            VirtualFree(probe, 0, MEM_RELEASE);
        } else {
            Log("the %s pool could not get %u MiB (VirtualAlloc refused, %lu); made at the game's %u MiB instead",
                POOLS[i].name, (unsigned)mib, GetLastError(), (unsigned)POOLS[i].gameMiB);
            mib = POOLS[i].gameMiB;
            size = mib * MIB;
        }
    }
    g_init(self, edx, name, type, size, opt, grain, extra);
    if (i < 0) return;
    const uint8_t* me = (const uint8_t*)self;
    State& s = g_state[i];
    s.base = *(const uint32_t*)(me + POOL_BASE);
    s.end = *(const uint32_t*)(me + POOL_END);
    s.mib = s.base ? mib : 0;
    s.self = self;
    if (!s.base) Log("the %s pool got no memory at all (VirtualAlloc refused %u MiB)", POOLS[i].name, (unsigned)mib);
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

bool Same(uintptr_t at, const uint8_t* code, size_t n) { return Readable(at, n) && memcmp((const void*)at, code, n) == 0; }

bool Verify() {
    for (const Pool& p : POOLS) {
        // push size (68 imm32), push type (6A imm8), push name (68 imm32)
        uint8_t code[12] = {0x68, 0, 0, 0, 0, 0x6A, p.type, 0x68, 0, 0, 0, 0};
        const uint32_t bytes = p.gameMiB * MIB;
        memcpy(code + 1, &bytes, 4);
        memcpy(code + 8, &p.nameAt, 4);
        if (!Same(p.push, code, sizeof code))
            return Log("refused: the %s pool's size at 0x%08X is not build 2364871's (another build, or already patched)",
                       p.name, (unsigned)p.push), false;
        char quoted[24];                           // the game's names carry their quotes: "Unit"
        _snprintf_s(quoted, sizeof quoted, _TRUNCATE, "\"%s\"", p.name);
        if (!Readable(p.nameAt, 16) || strcmp((const char*)(uintptr_t)p.nameAt, quoted) != 0)
            return Log("refused: the name at 0x%08X is not %s", (unsigned)p.nameAt, quoted), false;
    }
    const uint32_t slots[3][2] = {{VT_ALLOC, FN_ALLOC}, {VT_INIT, FN_INIT}, {VT_ALLOC_IN, FN_ALLOC_IN}};
    for (const auto& v : slots) {
        if (!Readable(v[0], 4) || *(const uint32_t*)(uintptr_t)v[0] != v[1])
            return Log("refused: the allocator's vtable entry at 0x%08X does not point to 0x%08X (another build, or "
                       "already patched)", (unsigned)v[0], (unsigned)v[1]), false;
    }
    if (!Same(FN_ALLOC, CODE_ALLOC, sizeof CODE_ALLOC) || !Same(FN_ALLOC_IN, CODE_ALLOC_IN, sizeof CODE_ALLOC_IN) ||
        !Same(FN_INIT, CODE_INIT, sizeof CODE_INIT))
        return Log("refused: the allocator's code at 0x%08X / 0x%08X / 0x%08X is not build 2364871's", FN_ALLOC,
                   FN_INIT, FN_ALLOC_IN), false;
    return true;
}

bool Write32(uintptr_t at, uint32_t value) {
    DWORD old;
    if (!VirtualProtect((void*)at, 4, PAGE_EXECUTE_READWRITE, &old)) return false;
    *(uint32_t*)at = value;
    VirtualProtect((void*)at, 4, old, &old);
    return true;
}

bool Patch() {
    // The size pushes lie in one function: one protection change over it, so a failure leaves it untouched.
    const uintptr_t lo = POOLS[0].push, hi = POOLS[NPOOLS - 1].push + 5;
    DWORD old;
    if (!VirtualProtect((void*)lo, hi - lo, PAGE_EXECUTE_READWRITE, &old))
        return Log("failed: VirtualProtect 0x%08X..0x%08X (%lu); nothing patched", (unsigned)lo, (unsigned)hi,
                   GetLastError()), false;
    for (int i = 0; i < NPOOLS; i++) *(uint32_t*)(uintptr_t)(POOLS[i].push + 1) = g_mib[i] * MIB;
    VirtualProtect((void*)lo, hi - lo, old, &old);
    FlushInstructionCache(GetCurrentProcess(), (void*)lo, hi - lo);
    // The vtable (read-only data): the init first, so no pool exists unregistered.
    if (!Write32(VT_INIT, (uint32_t)(uintptr_t)&WrapInit) || !Write32(VT_ALLOC, (uint32_t)(uintptr_t)&WrapAlloc) ||
        !Write32(VT_ALLOC_IN, (uint32_t)(uintptr_t)&WrapAllocIn)) {
        Write32(VT_INIT, FN_INIT), Write32(VT_ALLOC, FN_ALLOC), Write32(VT_ALLOC_IN, FN_ALLOC_IN);
        for (int i = 0; i < NPOOLS; i++) Write32(POOLS[i].push + 1, POOLS[i].gameMiB * MIB);
        return Log("failed: the allocator's vtable could not be written (%lu); nothing patched", GetLastError()), false;
    }
    return true;
}

void ReadSettings(HMODULE self) {
    wchar_t ini[MAX_PATH];
    GetModuleFileNameW(self, ini, MAX_PATH);
    wchar_t* dot = wcsrchr(ini, L'.');
    if (dot) wcscpy_s(dot, MAX_PATH - (dot - ini), L".ini");
    uint32_t total = 0;
    for (int i = 0; i < NPOOLS; i++) {
        const Pool& p = POOLS[i];
        wchar_t key[32];
        _snwprintf_s(key, 32, _TRUNCATE, L"%hs", p.key);
        int n = (int)GetPrivateProfileIntW(L"pool_cap", key, (int)p.defaultMiB, ini);
        if (n < (int)p.gameMiB || n > (int)p.maxMiB) {
            const int to = n < (int)p.gameMiB ? (int)p.gameMiB : (int)p.maxMiB;
            Log("%s = %d is outside %u..%u (MiB); using %d", p.key, n, (unsigned)p.gameMiB, (unsigned)p.maxMiB, to);
            n = to;
        }
        g_mib[i] = (uint32_t)n;
        total += (uint32_t)n;
    }
    if (total > TOTAL_MAX_MIB) {
        Log("the pools add up to %u MiB, more than %u; using the defaults", (unsigned)total, (unsigned)TOTAL_MAX_MIB);
        for (int i = 0; i < NPOOLS; i++) g_mib[i] = POOLS[i].defaultMiB;
    }
}

void Summary() {
    if (!g_patched) return;
    Log("at exit (+%.1f s):", Seconds());
    for (int i = 0; i < NPOOLS; i++) {
        const State& s = g_state[i];
        if (!s.self) {
            Log("  %-12s never made", POOLS[i].name);
            continue;
        }
        Log("  %-12s %4u MiB, requests reach %.1f MiB, %ld refused (the largest %ld bytes)", POOLS[i].name,
            (unsigned)s.mib, MiB((uint32_t)s.deepest), s.refused, s.largest);
    }
    Log("  the ragdoll setup was refused %ld requests (each a ragdoll left with no body data)", g_ragdollRefused);
    if (g_otherRefused) Log("  other allocators refused %ld requests", g_otherRefused);
}

void Start(HMODULE self) {
    g_start = GetTickCount64();
    wchar_t root[MAX_PATH];
    GetModuleFileNameW(nullptr, root, MAX_PATH);
    wchar_t* slash = wcsrchr(root, L'\\');
    if (slash) *slash = 0;
    wchar_t dir[MAX_PATH];
    _snwprintf_s(dir, MAX_PATH, _TRUNCATE, L"%s\\riftstone", root);
    CreateDirectoryW(dir, nullptr);
    _snwprintf_s(dir, MAX_PATH, _TRUNCATE, L"%s\\riftstone\\logs", root);
    CreateDirectoryW(dir, nullptr);
    _snwprintf_s(g_logPath, MAX_PATH, _TRUNCATE, L"%s\\riftstone\\logs\\pool_cap.log", root);
    DeleteFileW(g_logPath);

    // The game is a fixed-base image; a test harness that maps DDDA.exe itself sets the variable.
    wchar_t probe[8];
    const bool harness = GetEnvironmentVariableW(L"RIFTSTONE_POOL_HARNESS", probe, 8) > 0;
    if (!harness && (uintptr_t)GetModuleHandleW(nullptr) != IMAGE_BASE) {
        Log("refused: the host is not DDDA.exe at 0x00400000");
        return;
    }
    ReadSettings(self);
    if (!Verify()) return;
    // The pools are made once, in WinMain; a plugin loaded after that would change nothing but the vtable.
    if (!Readable(TABLE_UNIT, 4) || (*(const uint32_t*)TABLE_UNIT != 0 && *(const uint32_t*)TABLE_UNIT != DEFAULT_ALLOCATOR)) {
        Log("refused: the game's memory pools already exist (the plugin was loaded too late); nothing patched");
        return;
    }
    if (!Patch()) return;
    g_patched = true;
    HMODULE pinned;                                      // the game now calls into this module
    GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_PIN, (LPCWSTR)(void*)&WrapAlloc,
                       &pinned);
    uint32_t total = 0, game = 0;
    for (int i = 0; i < NPOOLS; i++) total += g_mib[i], game += POOLS[i].gameMiB;
    Log("pool_cap: Unit %u MiB (the game's 64), Physics %u (12), Collision %u (24), Effect %u (5), Array/String %u (6), "
        "GUI %u (5), Temp %u (64), System %u (64): %u MiB in all, %u more than the game's; refusals counted (%s)",
        (unsigned)g_mib[2], (unsigned)g_mib[7], (unsigned)g_mib[6], (unsigned)g_mib[3], (unsigned)g_mib[5],
        (unsigned)g_mib[4], (unsigned)g_mib[0], (unsigned)g_mib[1], (unsigned)total, (unsigned)(total - game),
        harness ? "harness" : "game");
}

}  // namespace

// For the harness and the loader's live view.  A pool by its ini key: the MiB it was given (0 when the plugin
// refused or the pool was not made yet), what it refused, and how deep its requests reach (bytes).
extern "C" __declspec(dllexport) int PoolCap_Size(const char* key) {
    if (!g_patched) return 0;
    for (int i = 0; i < NPOOLS; i++)
        if (strcmp(POOLS[i].key, key) == 0) return g_state[i].self ? (int)g_state[i].mib : (int)g_mib[i];
    return -1;
}
extern "C" __declspec(dllexport) int PoolCap_Refused(const char* key) {
    for (int i = 0; i < NPOOLS; i++)
        if (strcmp(POOLS[i].key, key) == 0) return (int)g_state[i].refused;
    if (strcmp(key, "ragdoll") == 0) return (int)g_ragdollRefused;   // the ragdoll setup's, in any pool
    return strcmp(key, "other") == 0 ? (int)g_otherRefused : -1;
}
extern "C" __declspec(dllexport) int PoolCap_Deepest(const char* key) {
    for (int i = 0; i < NPOOLS; i++)
        if (strcmp(POOLS[i].key, key) == 0) return (int)g_state[i].deepest;
    return -1;
}
extern "C" __declspec(dllexport) void PoolCap_Summary() { Summary(); }

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(module);
        Start(module);
    } else if (reason == DLL_PROCESS_DETACH) {
        Summary();
    }
    return TRUE;
}

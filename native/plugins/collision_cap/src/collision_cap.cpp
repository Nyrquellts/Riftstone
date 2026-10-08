// collision_cap -- Riftstone native plugin: more collision entry nodes per frame (the game's table holds
// 800), and the sweep that read past the table stopped at its end.
//
// How the limit works (DDDA.exe build 2364871; docs/re-collision-cap.md)
//   sObjCollision (0x9C550 bytes; the instance at [0x018FA4E4]) keeps every hit shape the frame's objects
//   enter as an entry node (cObjCollision::EntryNode, 0x320 bytes): a fixed table of 800 from +0x40, the
//   count at +0x34 (mEntryNodeCtr; the frame end sets it back to 0 at 0x0041DF8A) and its peak at +0x38.
//   Each of the 97 allocators (sObjCollision::getEntryNode, 0x007708B0, inlined 96 times more) calls
//   InterlockedIncrement on the count and only then compares the result with 800: a request the table
//   cannot take is refused (NULL) but has raised the count all the same.  The sweep job (0x00479C10,
//   queued on every worker thread by 0x00479CB0) walks the records up to that count, so a frame that asks
//   for more than 800 nodes sends it past the table: the owner's four stage-220 crashes (ACCESS_VIOLATION
//   at 0x00479C44, the record swept 801..823; six Archydras in Gran Soren).
//
// What the plugin does (sites.inc, generated from the exe by tools/collision_cap_sites.py)
//   * The manager is allocated 0x9C550 + (N - 800) x 0x320 bytes (three sites); the table stays at +0x40,
//     N records long, and every instruction that addresses a field after it (169 displacements, 4 address
//     computations) shifts by that DELTA.
//   * Every allocator's bound of 800 becomes N (97 sites); the constructor's and destructor's record loops
//     run N times (two counts, 800 - 1 -> N - 1).
//   * The sweep job's two count checks become jumps into the plugin, which repeats the check and also stops
//     the sweep at N: the overshoot above can never run past the table again, at any N.
//
// Safety
//   Build 2364871 only.  All 275 instructions and the two blocks are compared byte for byte first, and the
//   manager must not exist yet (it is built at the patched size); otherwise nothing is patched and
//   riftstone\logs\collision_cap.log says why.  Once patched, the plugin pins itself in memory: the game's
//   code jumps into it.  Settings: riftstone\plugins\collision_cap.ini, [collision_cap]
//   entry_nodes = 800..16384 (default 4096).  Original code.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

namespace {

constexpr uintptr_t IMAGE_BASE = 0x00400000;
constexpr int MIN_NODES = 800, MAX_NODES = 16384, DEFAULT_NODES = 4096;

enum Kind { K_TAIL, K_TAIL_IMM, K_BOUND, K_COUNT_M1, K_ALLOC };
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

int g_nodes = DEFAULT_NODES;
uint32_t g_nodesU = DEFAULT_NODES;                       // N as the sweep clamp compares it
uint32_t g_delta = 0;                                    // (N - 800) x 0x320: how far the tail fields move
bool g_patched = false;
wchar_t g_logPath[MAX_PATH];

void VLog(const char* fmt, va_list ap) {
    char line[512];
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

// The sweep job's two count checks, each "cmp eax, [esi+0x34]" (eax = the record the job took with
// InterlockedIncrement on its cursor, esi = the manager) followed by a conditional jump, are replaced by
// jumps here.  Each repeats the game's check and adds the table's end: a record number at or past N ends
// the sweep.  The code after each jump sets the flags itself (xor, imul, pop), so none need keeping.
uintptr_t g_sweepBody = SWEEP_BODY, g_sweepEnd = SWEEP_END, g_sweepLoop = SWEEP_LOOP, g_sweepDone = SWEEP_DONE;

__declspec(naked) static void ThunkSweepFirst() {     // before the loop: "jge end"
    __asm {
        cmp eax, dword ptr [esi + 0x34]
        jge first_end
        cmp eax, g_nodesU
        jge first_end
        jmp dword ptr [g_sweepBody]
    first_end:
        jmp dword ptr [g_sweepEnd]
    }
}
__declspec(naked) static void ThunkSweepNext() {      // after a record: "jl loop"
    __asm {
        cmp eax, dword ptr [esi + 0x34]
        jge next_done
        cmp eax, g_nodesU
        jge next_done
        jmp dword ptr [g_sweepLoop]
    next_done:
        jmp dword ptr [g_sweepDone]
    }
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
    for (const Site& s : SITES) {
        if (!Readable(s.at, s.len) || memcmp((const void*)(uintptr_t)s.at, s.code, s.len) != 0)
            return Log("refused: the instruction at 0x%08X is not build 2364871's (another build, or already patched)",
                       (unsigned)s.at), false;
    }
    for (const Block& b : BLOCKS) {
        if (!Readable(b.at, b.back - b.at) || memcmp((const void*)(uintptr_t)b.at, b.code, b.back - b.at) != 0)
            return Log("refused: the %s check at 0x%08X is not build 2364871's", b.name, (unsigned)b.at), false;
    }
    return true;
}

uint32_t NewValue(const Site& s) {
    const uint32_t old = *(const uint32_t*)(uintptr_t)(s.at + s.field);
    switch (s.kind) {
        case K_TAIL: case K_TAIL_IMM: case K_ALLOC: return old + g_delta;
        case K_BOUND: return (uint32_t)g_nodes;
        case K_COUNT_M1: return (uint32_t)g_nodes - 1;
    }
    return old;
}

bool Patch() {
    void* const thunks[2] = {(void*)ThunkSweepFirst, (void*)ThunkSweepNext};
    // One protection change over the whole span, so a failure leaves the code untouched.
    uintptr_t lo = ~(uintptr_t)0, hi = 0;
    for (const Site& s : SITES) lo = min(lo, (uintptr_t)s.at), hi = max(hi, (uintptr_t)s.at + s.len);
    for (const Block& b : BLOCKS) lo = min(lo, (uintptr_t)b.at), hi = max(hi, (uintptr_t)b.back);
    DWORD old;
    if (!VirtualProtect((void*)lo, hi - lo, PAGE_EXECUTE_READWRITE, &old))
        return Log("failed: VirtualProtect 0x%08X..0x%08X (%lu); nothing patched", (unsigned)lo, (unsigned)hi,
                   GetLastError()), false;
    for (const Site& s : SITES) *(uint32_t*)(uintptr_t)(s.at + s.field) = NewValue(s);
    for (int i = 0; i < 2; i++) {
        uint8_t* p = (uint8_t*)(uintptr_t)BLOCKS[i].at;
        p[0] = 0xE9;
        *(int32_t*)(p + 1) = (int32_t)((uintptr_t)thunks[i] - (BLOCKS[i].at + 5));
        memset(p + 5, 0xCC, BLOCKS[i].back - BLOCKS[i].at - 5);   // never reached (the blocks are five bytes)
    }
    VirtualProtect((void*)lo, hi - lo, old, &old);
    FlushInstructionCache(GetCurrentProcess(), (void*)lo, hi - lo);
    return true;
}

void ReadSettings(HMODULE self) {
    wchar_t ini[MAX_PATH];
    GetModuleFileNameW(self, ini, MAX_PATH);
    wchar_t* dot = wcsrchr(ini, L'.');
    if (dot) wcscpy_s(dot, MAX_PATH - (dot - ini), L".ini");
    int n = (int)GetPrivateProfileIntW(L"collision_cap", L"entry_nodes", DEFAULT_NODES, ini);
    if (n < MIN_NODES || n > MAX_NODES) {
        Log("entry_nodes = %d is outside %d..%d; using %d", n, MIN_NODES, MAX_NODES, n < MIN_NODES ? MIN_NODES : MAX_NODES);
        n = n < MIN_NODES ? MIN_NODES : MAX_NODES;
    }
    g_nodes = n;
    g_nodesU = (uint32_t)n;
    g_delta = ((uint32_t)n - NODES_OLD) * NODE_SIZE;
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
    _snwprintf_s(g_logPath, MAX_PATH, _TRUNCATE, L"%s\\riftstone\\logs\\collision_cap.log", root);
    DeleteFileW(g_logPath);

    // The game is a fixed-base image; a test harness that maps DDDA.exe itself sets the variable.
    wchar_t probe[8];
    const bool harness = GetEnvironmentVariableW(L"RIFTSTONE_COLLISION_HARNESS", probe, 8) > 0;
    if (!harness && (uintptr_t)GetModuleHandleW(nullptr) != IMAGE_BASE) {
        Log("refused: the host is not DDDA.exe at 0x00400000");
        return;
    }
    ReadSettings(self);
    if (!Verify()) return;
    // The manager is allocated once, at start-up; one built before the patches holds 800 records, and the
    // moved fields would lie past it.  Loaded by Riftstone's loader, it never exists yet.
    if (!Readable(MANAGER, 4) || *(const uint32_t*)MANAGER != 0) {
        Log("refused: the game's collision manager already exists (the plugin was loaded too late); nothing patched");
        return;
    }
    if (!Patch()) return;
    g_patched = true;
    HMODULE pinned;                                      // the game's code now jumps into this module
    GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_PIN,
                       (LPCWSTR)(void*)&ThunkSweepFirst, &pinned);
    Log("collision_cap: %d collision entry nodes a frame (the game's table holds %u); the manager grows by %u bytes; "
        "%u sites and %u checks patched; the sweep stops at the table's end (%s)",
        g_nodes, (unsigned)NODES_OLD, (unsigned)g_delta, (unsigned)(sizeof SITES / sizeof SITES[0]),
        (unsigned)(sizeof BLOCKS / sizeof BLOCKS[0]), harness ? "harness" : "game");
}

}  // namespace

// For the harness and the loader's live view: the node count in effect (0 when the plugin refused).
extern "C" __declspec(dllexport) int CollisionCap_Nodes() { return g_patched ? g_nodes : 0; }

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(module);
        Start(module);
    }
    return TRUE;
}

// pool_cap_harness_core -- runs the game's own memory-pool code with the pool_cap patches, without the game.
//
//   pool_cap_stub.exe <DDDA.exe> <pool_cap.asi> <case> <expected MiB: temp,system,unit,effect,gui,array,collision,physics>
//
// Maps DDDA.exe's image at its fixed base (0x00400000) over the stub's image, which was sized to reserve that
// range, loads the plugin (which verifies and patches the mapped code) and then does what the game does at
// start-up: every slot of the allocator table gets the default allocator (here a stand-in that takes memory
// from the harness's heap: the CRT initialiser 0x0137E090 is not run), and WinMain's pool builder 0x00740590
// runs -- the real code: operator new (0x00D0F2B0), MtScalableAllocator's constructor (0x0041B050), its init
// through the vtable (0x00D0FA50, wrapped by the plugin), the registration (0x00CF5450) and the memory it
// commits through the exe's VirtualAlloc import, which the harness records.  The CRT's two formatting
// functions the init calls (_snprintf_s 0x012ED991, sprintf 0x012ED8EF) are replaced by the harness's own.
// Then it checks:
//   * every pool: the table slot it is put in, its name, its vtable, its size (+0x68, +0x0C), its memory
//     (+0x5C..+0x60) and the VirtualAlloc(MEM_COMMIT) the game asked for, at the expected size; the slots
//     that share a pool (2, 13 = 3; 16 = 11; 17, 25 = 12) and the ones left to the default allocator;
//   * the Unit pool filled through the real allocator (vtable slot 7 0x00CF5400 -> slot 6, wrapped -> the
//     real 0x00D10190): 1 MiB requests are given until the pool is full, never past its memory, and the bytes
//     given are within 2 MiB of its size; the refusal that ends it is counted by the plugin, and the depth the
//     plugin reports is inside the pool and at least the bytes given; small requests (uRigidBody's 368 bytes)
//     come from the same pool; the other entry (slot 13, a chosen heap) is counted as well;
//   * an allocator made apart from the eight (as AIWork is, with memory it is handed: init slot 11) refusing a
//     request is counted as "other", and leaves the eight's counts alone;
//   * "tamper": one size instruction altered first, "late": the pools already exist -- the plugin refuses and
//     patches nothing (the size pushes and the vtable as the game has them);
//   * "nomem": VirtualAlloc refuses more than 128 MiB at once: the pool asked for more is made at the game's
//     size, the others at theirs.
// Prints "pass"/"FAIL" lines and exits 0 when everything passed, 1 on a failure, 2 when the image cannot be
// mapped here (reported as a skip by run_tests.py).
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shellapi.h>

#include <malloc.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include <string>
#include <vector>

namespace {
constexpr uintptr_t BASE = 0x00400000;
constexpr uint32_t MIB = 1024 * 1024;

constexpr uintptr_t TABLE = 0x01876628;                       // the 64 allocators, by DTI index
constexpr uintptr_t BUILD_POOLS = 0x00740590;                 // WinMain's pool builder (called at 0x00407A87)
constexpr uintptr_t CRT_SNPRINTF_S = 0x012ED991, CRT_SPRINTF = 0x012ED8EF;
constexpr uintptr_t IAT_INIT_CS = 0x0139D16C, IAT_THREAD_ID = 0x0139D09C, IAT_VIRTUAL_ALLOC = 0x0139D1B4,
                    IAT_ENTER_CS = 0x0139D160, IAT_LEAVE_CS = 0x0139D164, IAT_INC = 0x0139D0D0,
                    IAT_XADD = 0x0139D0A8, IAT_DELETE_CS = 0x0139D098, IAT_VIRTUAL_FREE = 0x0139D1B8,
                    IAT_XCHG = 0x0139D178;
constexpr uintptr_t REGISTRY_COUNT = 0x01876834;              // the allocators registered by 0x00CF5450
constexpr uint32_t VTABLE = 0x0155AD4C;
constexpr uintptr_t VT_ALLOC = VTABLE + 6 * 4, VT_INIT = VTABLE + 12 * 4, VT_ALLOC_IN = VTABLE + 13 * 4;
constexpr uint32_t FN_ALLOC = 0x00D10190, FN_INIT = 0x00D0FA50, FN_ALLOC_IN = 0x00D10340;
constexpr uintptr_t SCALABLE_CTOR = 0x0041B050;
constexpr uint32_t OBJECT_SIZE = 0x4D0;

struct PoolInfo {
    const char* key;
    const char* name;
    int slot;                 // the table slot 0x00740590 stores it in
    uint32_t push;            // its size push
    uint32_t game;            // the game's size, MiB
};
const PoolInfo POOLS[] = {
    {"temp", "Temp", 5, 0x007405CB, 64},          {"system", "System", 11, 0x0074061A, 64},
    {"unit", "Unit", 12, 0x00740669, 64},         {"effect", "Effect", 18, 0x007406B8, 5},
    {"gui", "GUI", 19, 0x00740707, 5},            {"array_string", "Array/String", 3, 0x00740756, 6},
    {"collision", "Collision", 4, 0x007407A5, 24}, {"physics", "Physics", 15, 0x007407F4, 12},
};
constexpr int NPOOLS = 8;

int g_fails = 0;
void Check(bool ok, const std::string& what) {
    printf("  %s  %s\n", ok ? "pass" : "FAIL", what.c_str());
    if (!ok) g_fails++;
}
std::string F(const char* fmt, ...) {
    char b[320];
    va_list ap;
    va_start(ap, fmt);
    _vsnprintf_s(b, sizeof b, _TRUNCATE, fmt, ap);
    va_end(ap);
    return b;
}
uint32_t U32(uintptr_t a) { return *(const uint32_t*)a; }
void Put(uintptr_t a, uint32_t v) { *(uint32_t*)a = v; }
uint32_t Slot(int i) { return U32(TABLE + 4 * (uintptr_t)i); }

// ---- what the tested code calls -------------------------------------------------------------------------
// The exe's VirtualAlloc import: recorded, and refused above a limit in "nomem".
struct Commit {
    uint32_t at, size, type, protect;
};
std::vector<Commit> g_commits;
uint32_t g_vaLimit = 0xFFFFFFFF;
LPVOID WINAPI RecordVirtualAlloc(LPVOID at, SIZE_T size, DWORD type, DWORD protect) {
    if (size > g_vaLimit) {
        SetLastError(ERROR_NOT_ENOUGH_MEMORY);
        return nullptr;
    }
    LPVOID p = VirtualAlloc(at, size, type, protect);
    if (p) g_commits.push_back({(uint32_t)(uintptr_t)p, (uint32_t)size, type, protect});
    return p;
}
int __cdecl HarnessSnprintfS(char* buf, size_t size, size_t count, const char* fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    int n = _vsnprintf_s(buf, size, count, fmt, ap);
    va_end(ap);
    return n;
}
int __cdecl HarnessSprintf(char* buf, const char* fmt, ...) {      // "Manager-%d" into a sub-heap's name
    va_list ap;
    va_start(ap, fmt);
    int n = _vsnprintf_s(buf, 32, _TRUNCATE, fmt, ap);
    va_end(ap);
    return n;
}
void JumpTo(uintptr_t at, const void* to) {
    DWORD old;
    VirtualProtect((void*)at, 5, PAGE_EXECUTE_READWRITE, &old);
    *(uint8_t*)at = 0xE9;
    *(int32_t*)(at + 1) = (int32_t)((uintptr_t)to - (at + 5));
}

// The default allocator the CRT puts in every slot (a stand-in: MtAllocator's slot 7, alloc(size, align, tag)).
void* __fastcall DefaultNew(void*, void*, uint32_t size, uint32_t align, uint32_t) {
    return _aligned_malloc(size ? size : 1, align < 16 ? 16 : align);
}
void* g_defaultVt[16];
struct {
    void** vt;
} g_default = {g_defaultVt};

// ---- the image -----------------------------------------------------------------------------------------
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
        return printf("SKIP: run me through pool_cap_stub.exe (host at %p, 0x%X bytes)\n", (void*)host,
                      (unsigned)hostNt->OptionalHeader.SizeOfImage), false;
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
    // Every kernel32 import of the exe gets kernel32's own function (the allocator's code uses ten of them:
    // critical sections, interlocked operations, the thread id, VirtualAlloc/VirtualFree); VirtualAlloc is
    // then the recorder.  Other DLLs' imports stay unresolved: nothing tested calls them.
    HMODULE k32 = GetModuleHandleW(L"kernel32.dll");
    const IMAGE_DATA_DIRECTORY& dir = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
    int resolved = 0;
    for (auto* d = (const IMAGE_IMPORT_DESCRIPTOR*)(BASE + dir.VirtualAddress); d->Name; d++) {
        if (_stricmp((const char*)(BASE + d->Name), "KERNEL32.dll") != 0) continue;
        auto* names = (const uint32_t*)(BASE + (d->OriginalFirstThunk ? d->OriginalFirstThunk : d->FirstThunk));
        auto* slots = (uint32_t*)(BASE + d->FirstThunk);
        for (int k = 0; names[k]; k++) {
            if (names[k] & 0x80000000) continue;
            const char* name = (const char*)(BASE + names[k] + 2);
            if (FARPROC fn = GetProcAddress(k32, name)) slots[k] = (uint32_t)(uintptr_t)fn, resolved++;
        }
    }
    if (U32(IAT_INIT_CS) < 0x10000000 || U32(IAT_XCHG) < 0x10000000 || U32(IAT_THREAD_ID) < 0x10000000)
        return printf("the exe's kernel32 imports were not where expected (%d resolved)\n", resolved), false;
    Put(IAT_VIRTUAL_ALLOC, (uint32_t)(uintptr_t)&RecordVirtualAlloc);
    JumpTo(CRT_SNPRINTF_S, (const void*)&HarnessSnprintfS);
    JumpTo(CRT_SPRINTF, (const void*)&HarnessSprintf);
    for (int i = 0; i < 16; i++) g_defaultVt[i] = nullptr;
    g_defaultVt[7] = (void*)&DefaultNew;
    return true;
}

// ---- calling the game's allocator ----------------------------------------------------------------------
typedef void*(__fastcall* Alloc7)(void* self, void* edx, uint32_t size, uint32_t align, uint32_t tag);
typedef void*(__fastcall* AllocIn)(void* self, void* edx, uint32_t size, uint32_t heap, uint32_t align, uint32_t tag,
                                   uint32_t flag);
typedef void(__fastcall* Init11)(void* self, void* edx, const char* name, void* memory, uint32_t size, uint32_t opt,
                                 uint32_t grain, uint32_t extra);
void* Alloc(uint32_t allocator, uint32_t size, uint32_t align = 16) {
    void** vt = *(void***)(uintptr_t)allocator;
    return ((Alloc7)vt[7])((void*)(uintptr_t)allocator, nullptr, size, align, 0);
}

// The ragdoll setup and what it calls outside the pools.
constexpr uintptr_t RAGDOLL_SETUP = 0x01083260, RIGID_BODY_DTI = 0x018D3978, GET_ALLOCATOR = 0x00CF5E20;
constexpr uintptr_t RES_ADDREF = 0x00DE0B80, RES_RELEASE = 0x00DE0B90, UNREGISTER = 0x00E76560;
int g_addRefs = 0, g_releases = 0;
void __fastcall HarnessAddRef(void*) { g_addRefs++; }                    // ecx = the resource, no stack arguments
void __fastcall HarnessRelease(void*) { g_releases++; }
void __fastcall HarnessUnregister(void*, void*, void*, void*) {}        // (object, callback), ret 8
void __fastcall HarnessNop(void*) {}                                    // the body object's vtable entries 0x50, 0x54
void* g_bodyVt[32];

typedef int (*KeyFn)(const char*);
typedef void (*VoidFn)();
KeyFn g_size, g_refused, g_deepest;
}  // namespace

static int Run(int argc, wchar_t** argv);

// A fault in the tested code: where, and the registers, before the process ends.
static LONG CALLBACK Fault(EXCEPTION_POINTERS* e) {
    const DWORD code = e->ExceptionRecord->ExceptionCode;
    if (code != EXCEPTION_ACCESS_VIOLATION && code != EXCEPTION_ILLEGAL_INSTRUCTION && code != EXCEPTION_INT_DIVIDE_BY_ZERO &&
        code != EXCEPTION_STACK_OVERFLOW && code != EXCEPTION_PRIV_INSTRUCTION)
        return EXCEPTION_CONTINUE_SEARCH;
    const CONTEXT* c = e->ContextRecord;
    printf("  FAIL  exception 0x%08lX at 0x%08lX (reading/writing 0x%08lX): eax %08lX ebx %08lX ecx %08lX edx %08lX "
           "esi %08lX edi %08lX ebp %08lX esp %08lX\n",
           code, (unsigned long)(uintptr_t)e->ExceptionRecord->ExceptionAddress,
           e->ExceptionRecord->NumberParameters > 1 ? (unsigned long)e->ExceptionRecord->ExceptionInformation[1] : 0ul,
           c->Eax, c->Ebx, c->Ecx, c->Edx, c->Esi, c->Edi, c->Ebp, c->Esp);
    const uint32_t* sp = (const uint32_t*)(uintptr_t)c->Esp;
    printf("        stack:");
    for (int k = 0; k < 12; k++) printf(" %08X", sp[k]);
    printf("\n");
    fflush(stdout);
    ExitProcess(1);
}

// Called by pool_cap_stub.exe.  Never returns: the stub's code is gone once DDDA.exe is mapped.
extern "C" __declspec(dllexport) void HarnessMain() {
    setvbuf(stdout, nullptr, _IONBF, 0);
    AddVectoredExceptionHandler(1, Fault);
    int argc = 0;
    wchar_t** argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    int code = Run(argc, argv);
    fflush(stdout);
    ExitProcess((UINT)code);
}

static int Run(int argc, wchar_t** argv) {
    if (argc < 5) {
        printf("usage: pool_cap_stub <DDDA.exe> <pool_cap.asi> <case> <MiB,MiB,...>\n");
        return 1;
    }
    if (!MapImage(argv[1])) return 2;
    const std::wstring mode = argv[3];
    uint32_t want[NPOOLS];
    {
        const wchar_t* p = argv[4];
        for (int i = 0; i < NPOOLS; i++) {
            want[i] = (uint32_t)wcstoul(p, (wchar_t**)&p, 10);
            if (*p == L',') p++;
        }
    }
    const bool refuse = mode == L"tamper" || mode == L"late";
    if (mode == L"tamper") *(uint8_t*)(uintptr_t)(POOLS[2].push + 3) ^= 0x01;   // the Unit pool's size is not 64 MiB
    if (mode == L"late") Put(TABLE + 12 * 4, 0x12345678);                      // the game made its pools first
    if (mode == L"nomem") g_vaLimit = 128 * MIB;

    SetEnvironmentVariableW(L"RIFTSTONE_POOL_HARNESS", L"1");
    HMODULE plugin = LoadLibraryW(argv[2]);
    g_size = plugin ? (KeyFn)GetProcAddress(plugin, "PoolCap_Size") : nullptr;
    g_refused = plugin ? (KeyFn)GetProcAddress(plugin, "PoolCap_Refused") : nullptr;
    g_deepest = plugin ? (KeyFn)GetProcAddress(plugin, "PoolCap_Deepest") : nullptr;
    auto summary = plugin ? (VoidFn)GetProcAddress(plugin, "PoolCap_Summary") : nullptr;
    printf("plugin (case %ls)\n", mode.c_str());
    Check(plugin && g_size && g_refused && g_deepest && summary, "the plugin loads");
    if (g_fails) return 1;

    if (refuse) {
        Check(g_size("unit") == 0, mode == L"tamper" ? "one altered size instruction: the plugin refuses"
                                                    : "the pools already exist: the plugin refuses");
        bool untouched = U32(VT_ALLOC) == FN_ALLOC && U32(VT_INIT) == FN_INIT && U32(VT_ALLOC_IN) == FN_ALLOC_IN;
        for (int i = 0; i < NPOOLS; i++) {
            uint32_t expect = POOLS[i].game * MIB;
            if (mode == L"tamper" && i == 2) expect ^= 1u << 16;
            untouched &= U32(POOLS[i].push + 1) == expect;
        }
        Check(untouched, "  and nothing is patched: the eight sizes and the three vtable entries are the game's");
        printf(g_fails ? "\n%d check(s) FAILED\n" : "\nall checks passed\n", g_fails);
        return g_fails ? 1 : 0;
    }
    Check(U32(VT_ALLOC) != FN_ALLOC && U32(VT_INIT) != FN_INIT && U32(VT_ALLOC_IN) != FN_ALLOC_IN,
          "the allocator's init and both allocation entries go through the plugin");
    {
        // In "nomem" a pool the process cannot give its size is pushed at that size and made at the game's.
        bool sizes = true;
        for (int i = 0; i < NPOOLS; i++)
            sizes &= U32(POOLS[i].push + 1) == want[i] * MIB ||
                     (mode == L"nomem" && want[i] == POOLS[i].game && U32(POOLS[i].push + 1) > g_vaLimit);
        Check(sizes, "the eight size pushes of 0x00740590 hold the sizes asked for");
    }

    // ---- start-up, as the game does it ----
    for (int i = 0; i < 64; i++) Put(TABLE + 4 * (uintptr_t)i, (uint32_t)(uintptr_t)&g_default);
    const uint32_t registered = U32(REGISTRY_COUNT);
    ((void(__cdecl*)())BUILD_POOLS)();
    printf("the game's pool builder (0x00740590) ran\n");
    const uint32_t def = (uint32_t)(uintptr_t)&g_default;
    for (int i = 0; i < NPOOLS; i++) {
        const PoolInfo& p = POOLS[i];
        const uint32_t a = Slot(p.slot);
        const uint32_t size = want[i] * MIB;
        bool ok = a && a != def && U32(a) == VTABLE && strcmp((const char*)(uintptr_t)(a + 0x11), (std::string("\"") + p.name + "\"").c_str()) == 0;
        ok &= U32(a + 0x68) == size && U32(a + 0x0C) == size && U32(a + 0x5C) && U32(a + 0x60) - U32(a + 0x5C) == size;
        bool committed = false;
        for (const Commit& c : g_commits)
            committed |= c.at == U32(a + 0x5C) && c.size == size && c.type == MEM_COMMIT && c.protect == PAGE_READWRITE;
        Check(ok && committed, F("%-12s slot %2d: %u MiB (the game's %u), committed with VirtualAlloc(MEM_COMMIT) at that size",
                                 p.name, p.slot, (unsigned)want[i], (unsigned)p.game));
        Check(g_size(p.key) == (int)want[i], F("  the plugin reports %d MiB", g_size(p.key)));
    }
    Check(Slot(2) == Slot(3) && Slot(13) == Slot(3) && Slot(16) == Slot(11) && Slot(17) == Slot(12) && Slot(25) == Slot(12),
          "the slots that share a pool: 2 and 13 Array/String, 16 System, 17 (nAI) and 25 Unit");
    {
        bool rest = true;
        for (int i : {0, 1, 6, 7, 8, 9, 10, 14, 20, 21, 22, 23, 24, 26, 63}) rest &= Slot(i) == def;
        Check(rest, "the other slots keep the default allocator (1, 14 and 23 copy slot 10's)");
    }
    Check(U32(REGISTRY_COUNT) == registered + 8,
          F("each pool registered once (0x00CF5450: %u allocators, %u before)", (unsigned)U32(REGISTRY_COUNT),
            (unsigned)registered));
    {
        // Each pool commits once; the plugin tries each enlarged size first (committed and freed again).
        int enlarged = 0;
        for (int i = 0; i < NPOOLS; i++) enlarged += want[i] != POOLS[i].game;
        Check((int)g_commits.size() == NPOOLS + enlarged,
              F("the exe's VirtualAlloc import gave %d commits: one a pool, and the plugin's trial of the %d enlarged",
                (int)g_commits.size(), enlarged));
    }
    if (g_fails) {
        printf("\n%d check(s) FAILED\n", g_fails);
        return 1;
    }

    // ---- the Unit pool, filled through the real allocator ----
    printf("the Unit pool, through the real allocator\n");
    const uint32_t unit = Slot(12), base = U32(unit + 0x5C), end = U32(unit + 0x60);
    {
        std::vector<uint8_t*> small;
        bool inside = true;
        for (int k = 0; k < 2000; k++) {
            uint8_t* p = (uint8_t*)Alloc(unit, 368);
            if (!p) break;
            inside &= (uint32_t)(uintptr_t)p >= base && (uint32_t)(uintptr_t)p + 368 <= end && ((uintptr_t)p & 15) == 0;
            memset(p, 0xA5, 368);
            small.push_back(p);
        }
        Check(small.size() == 2000 && inside, "2000 requests of 368 bytes (a uRigidBody) come from the pool, 16-byte aligned");
    }
    uint64_t given = 0;
    int refusedAt = -1;
    std::vector<uint8_t*> blocks;
    for (int k = 0; k < 2048; k++) {
        uint8_t* p = (uint8_t*)Alloc(unit, MIB);
        if (!p) {
            refusedAt = k;
            break;
        }
        blocks.push_back(p);
        const uint32_t a = (uint32_t)(uintptr_t)p;
        if (a < base || a + MIB > end) {
            Check(false, F("a request was given 0x%08X, outside the pool 0x%08X..0x%08X", a, base, end));
            break;
        }
        p[0] = 1, p[MIB - 1] = 2;
        given += MIB;
    }
    const uint32_t size = end - base;
    // A request over 64 KiB takes a chunk of the pool rounded up to its grain (+0x4C8, in 16-byte units; the
    // chunk search 0x00D0FCA0): its size, the block header and the chunk's own (8 units in all), rounded up.
    const uint32_t grain = U32(unit + 0x4C8);
    const uint32_t cost = grain ? (MIB / 16 + 8 + grain - 1) / grain * grain * 16 : MIB;
    const uint32_t fit = size / cost;
    Check(refusedAt > 0 && given <= size && (given / MIB == fit || given / MIB + 1 == fit),
          F("1 MiB requests are given until the pool is full: %.0f MiB of %u (each takes %u bytes at its grain of %u "
            "bytes: %u fit), then refused", (double)given / MIB, size / MIB, cost, grain * 16, fit));
    Check(g_refused("unit") == 1, F("  the plugin counted the refusal (%d)", g_refused("unit")));
    const int deepest = g_deepest("unit");
    Check(deepest > 0 && (uint32_t)deepest <= size && (uint64_t)deepest >= given,
          F("  and how deep the requests reach: %.1f MiB, inside the pool and at least the bytes given", (double)deepest / MIB));
    {
        void** vt = *(void***)(uintptr_t)unit;
        void* p = ((AllocIn)vt[13])((void*)(uintptr_t)unit, nullptr, 4 * MIB, 7, 16, 0, 0);
        Check(!p && g_refused("unit") == 2, "the other entry (slot 13, a chosen heap) refuses past the end and is counted");
        p = ((AllocIn)vt[13])((void*)(uintptr_t)unit, nullptr, 64, 0, 16, 0, 0);
        Check(p == nullptr || ((uint32_t)(uintptr_t)p >= base && (uint32_t)(uintptr_t)p < end),
              "  a small request into heap 0 is given from the pool or refused, never from elsewhere");
    }
    Check(g_refused("physics") == 0 && g_refused("temp") == 0, "the other pools' counts are untouched");

    // ---- the ragdoll setup (0x01083260), the real code, refused by the full Unit pool ----
    printf("the ragdoll setup (0x01083260) with the Unit pool full\n");
    {
        // uRigidBody's DTI as the start-up remap (0x00740860) leaves it: index 12 (the CRT's DTI constructors,
        // which give it 0 first, are not run here).
        Put(RIGID_BODY_DTI + 0x18, (U32(RIGID_BODY_DTI + 0x18) & ~(0x3Fu << 23)) | (12u << 23));
        Check(((uint32_t(__cdecl*)(uint32_t))GET_ALLOCATOR)(RIGID_BODY_DTI) == unit,
              "uRigidBody's DTI (index 12) names the Unit pool (getAllocator 0x00CF5E20)");
        // What the setup calls outside the pools: the resource manager's reference count (0x00DE0B80 /
        // 0x00DE0B90, both through sResource [0x018D0AA0]) and an unregistration in another manager (0x00E76560,
        // from the teardown it starts with) are counted stand-ins; the setup, its teardown and the allocator are real.
        JumpTo(RES_ADDREF, (const void*)&HarnessAddRef);
        JumpTo(RES_RELEASE, (const void*)&HarnessRelease);
        JumpTo(UNREGISTER, (const void*)&HarnessUnregister);
        for (int i = 0; i < 32; i++) g_bodyVt[i] = (void*)&HarnessNop;
        static uint8_t body[0x200], container[0x100], resource[0x100];
        *(void***)body = g_bodyVt;
        auto setup = [&](uint32_t bodies) {
            memset(container, 0, sizeof container);
            memset(resource, 0, sizeof resource);
            *(uint32_t*)(container + 0x30) = (uint32_t)(uintptr_t)body;          // what the setup reads first
            *(uint32_t*)(resource + 0x68) = bodies << 8;                         // the resource's body count
            return ((uint8_t(__fastcall*)(void*, void*, void*, float))RAGDOLL_SETUP)(container, nullptr, resource, 1.0f);
        };
        auto bodyData = [&] { return U32((uintptr_t)container + 0x38); };
        auto arrays = [&] {
            return U32((uintptr_t)container + 0x4C) | U32((uintptr_t)container + 0x50) | U32((uintptr_t)container + 0x54) |
                   U32((uintptr_t)container + 0x58);
        };
        // 300,000 bodies: the first array (1,200,000 bytes) cannot fit in what the full pool has left.
        // What the fill left at the pool's end (it depends on the pool's size) is taken by the smallest requests the
        // large heap serves (0x10001 bytes, two grains each), so less than two grains is left anywhere but the hole
        // made below, and each refusal comes at the array this test names.
        int tail = 0;
        while (Alloc(unit, 0x10001)) tail++;
        Check(g_refused("unit") == 3, F("the pool's end used up (%d requests of 0x10001 bytes), the last refused", tail));
        const int before = g_refused("unit");
        uint8_t ok = setup(300000);
        Check(!ok && bodyData() == 0 && arrays() == 0 && g_addRefs == 1 && g_releases == 1,
              F("refused its first array: it answers false, the body data (+0x38) is cleared, no array is kept, and "
                "the resource's reference is given back (%d taken, %d released)", g_addRefs, g_releases));
        Check(g_refused("unit") == before + 1 && g_refused("ragdoll") == 1,
              F("  the plugin counted it as the Unit pool's and as the ragdoll setup's (%d)", g_refused("ragdoll")));
        // One 1 MiB block given back: 48,000 bodies fit two arrays of 192,000 bytes there, not the third
        // (768,000): it frees the two it got and clears the body data again.
        const uint32_t hole = (uint32_t)(uintptr_t)blocks[blocks.size() / 2];
        void** vt = *(void***)(uintptr_t)unit;
        ((void(__fastcall*)(void*, void*, void*))vt[9])((void*)(uintptr_t)unit, nullptr, blocks[blocks.size() / 2]);
        ok = setup(48000);
        Check(!ok && bodyData() == 0 && arrays() == 0 && g_addRefs == 2 && g_releases == 2,
              F("refused its third array in a 1 MiB hole: false, no body data, no array kept (%d taken, %d released)",
                g_addRefs, g_releases));
        Check(g_refused("unit") == before + 2 && g_refused("ragdoll") == 2,
              F("  counted again (%d refusals of the ragdoll setup)", g_refused("ragdoll")));
        uint8_t* again = (uint8_t*)Alloc(unit, MIB);
        Check(again && (uint32_t)(uintptr_t)again == hole,
              "  the two arrays it got went back to the pool: the 1 MiB hole is whole again");
        // A goblin's ragdoll as the game ships it (model\em\e01\e0100\collision\e0100.rdd: 19 bodies, so arrays of
        // 80, 80, 304 and 304 bytes, small-heap requests) once the small heap that serves 80 bytes is full too.
        int small = 0;
        while (small < 1000000 && Alloc(unit, 80)) small++;
        Check(g_refused("unit") == before + 3, F("the small heap full (%d requests of 80 bytes, the last refused)", small));
        ok = setup(19);
        Check(!ok && bodyData() == 0 && arrays() == 0 && g_addRefs == 3 && g_releases == 3,
              F("a goblin's ragdoll (19 bodies) refused its first array (80 bytes): false, no body data (%d taken, "
                "%d released)", g_addRefs, g_releases));
        Check(g_refused("unit") == before + 4 && g_refused("ragdoll") == 3,
              F("  counted (%d refusals of the ragdoll setup)", g_refused("ragdoll")));
    }

    // ---- an allocator made apart from the eight, with memory it is handed (as AIWork is) ----
    {
        uint8_t* object = (uint8_t*)_aligned_malloc(OBJECT_SIZE, 16);
        void* memory = _aligned_malloc(MIB, 16);
        ((void(__fastcall*)(void*, void*))SCALABLE_CTOR)(object, nullptr);
        void** vt = *(void***)object;
        ((Init11)vt[11])(object, nullptr, "\"HarnessWork\"", memory, MIB, 6, 0, 0);
        void* p = ((Alloc7)vt[7])(object, nullptr, 2 * MIB, 16, 0);
        Check(!p && g_refused("other") == 1 && g_refused("unit") == 7,
              "an allocator apart from the eight (1 MiB it was handed) refusing 2 MiB is counted as other");
    }
    // The plugin's summary is written when the process ends (DLL_PROCESS_DETACH); run_tests.py reads it.
    printf(g_fails ? "\n%d check(s) FAILED\n" : "\nall checks passed\n", g_fails);
    return g_fails ? 1 : 0;
}

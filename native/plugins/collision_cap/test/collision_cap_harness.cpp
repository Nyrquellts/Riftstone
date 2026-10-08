// collision_cap_harness_core -- runs the collision_cap patches inside the real game code, without the game.
//
//   collision_cap_stub.exe <DDDA.exe> <collision_cap.asi> <expected nodes> [tamper|late]
//
// Maps DDDA.exe's image at its fixed base (0x00400000) over the stub's image, which was sized to reserve
// that range, loads the plugin (which verifies and patches the mapped code) and checks:
//   * the patched values: every site of sites.inc holds what its kind asks for at N (the three allocations,
//     the 173 moved tail fields, the 97 bounds, the two loop counts) with the rest of each instruction
//     untouched, and the two sweep checks jump into the plugin;
//   * the constructor's record loop (the real code, from the vtable store to the tail fields) on a fake
//     manager: all N records are built as the game builds its 800 (the loop's stores and the node
//     initialiser 0x007735C0), nothing past them is written, and the loop ran exactly N times.  A record's
//     sub-object constructor (0x01106DA0, an engine object that registers itself) is stubbed to return;
//   * the allocator (sObjCollision::getEntryNode, 0x007708B0, the real code with the manager in its global)
//     called N + 20 times on an empty manager: records 1..N-1 come back initialised, every call after that
//     is refused, and the count stands at N + 20 -- the overshoot the sweep clamp is for;
//   * the sweep job (0x00479C10, the real code) on a manager whose count is N + 50, with records laid out
//     past the table too: records 1..N-1 are swept (a held object whose kind is 1 or 2 is kept, any other
//     dropped), record 0 and every record past the table are untouched, the cursor stops at N; then with
//     the count at 5, and at 0;
//   * the destructor's record loop (the real code; the sub-object destructor 0x01106E00 stubbed): all N
//     records are torn down, nothing before or after them;
//   * the manager's reset (0x00478A50, the real code with the list clear 0x00479770): the count, the peak
//     and the moved tail fields are cleared, the records untouched;
//   * with "tamper", one site is altered first: the plugin must refuse and patch nothing;
//   * with "late", the manager already exists (built at the old size): refuse, patch nothing.
// Prints "pass"/"FAIL" lines and exits 0 when everything passed, 1 on a failure, 2 when the image cannot be
// mapped here (reported as a skip by run_tests.py).
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shellapi.h>

#include <setjmp.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include <string>
#include <vector>

namespace {
constexpr uintptr_t BASE = 0x00400000;
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

constexpr uintptr_t IAT_INTERLOCKED_INCREMENT = 0x0139D0D0;        // what every allocator and the job call
constexpr uintptr_t ALLOC_GAME = 0x0041C57F, BOUND_GETNODE = 0x007708C2, COUNT_CTOR = 0x0047862B, COUNT_DTOR = 0x00478A05;
constexpr uintptr_t RESET_HITINFO = 0x00478A5D;                    // reset: mov [esi+0x9C440], ebx
constexpr uintptr_t CTOR_RUN = 0x0047860D, CTOR_END = 0x004786BB;  // the vtable store .. the tail fields
constexpr uintptr_t SUB_CTOR = 0x01106DA0, SUB_DTOR = 0x01106E00;  // a record's engine sub-object
constexpr uintptr_t GET_NODE = 0x007708B0, SWEEP_JOB = 0x00479C10, RESET = 0x00478A50;
constexpr uintptr_t DTOR_LOOP = 0x004789FF, DTOR_END = 0x00478A37;
constexpr uint32_t VT_MANAGER = 0x0155EE3C, VT_RESOURCES = 0x01558298;
constexpr uint32_t VT_NODE = 0x01594A78, VT_NODE_A = 0x015793D0, VT_NODE_B = 0x015792E8, VT_MTOBJECT = 0x01558134;
constexpr uint32_t COUNT = 0x34, PEAK = 0x38;
constexpr uint32_t HITINFO_CTR = 0x9C440, HITINFO_MAX = 0x9C444, LISTS = 0x9C454, CURSOR = 0x9C4F4;   // vanilla offsets
constexpr uint32_t FIELD_510 = 0x9C510, LIST_COUNT = 0x9C518, LIST_DATA = 0x9C524;
constexpr uint32_t SENTINEL_EDI = 0x22222222, SENTINEL_EBX = 0x33333333;
constexpr uint32_t REC_A = 0x14C, REC_B = 0x154, REC_C = 0x158;    // the held objects the sweep looks at

int g_fails = 0;
void Check(bool ok, const std::string& what) {
    printf("  %s  %s\n", ok ? "pass" : "FAIL", what.c_str());
    if (!ok) g_fails++;
}
std::string F(const char* fmt, ...) {
    char b[256];
    va_list ap;
    va_start(ap, fmt);
    _vsnprintf_s(b, sizeof b, _TRUNCATE, fmt, ap);
    va_end(ap);
    return b;
}
uint32_t U32(uintptr_t a) { return *(const uint32_t*)a; }
void Put(uintptr_t a, uint32_t v) { *(uint32_t*)a = v; }

// ---- entering game code and stopping at an address ----------------------------------------------
jmp_buf g_back;
uint32_t g_jumpTo, g_setEdi, g_setEsi, g_setEcx, g_setEbx;
uint32_t g_trapEdi, g_trapEsi, g_trapEcx, g_trapEbx, g_trapEbp;

void TrapC() { longjmp(g_back, 1); }
__declspec(naked) void Trap() {
    __asm {
        mov g_trapEdi, edi
        mov g_trapEsi, esi
        mov g_trapEcx, ecx
        mov g_trapEbx, ebx
        mov g_trapEbp, ebp
        jmp TrapC
    }
}
__declspec(naked) void JumpIn() {
    __asm {
        sub esp, 512
        mov edi, g_setEdi
        mov esi, g_setEsi
        mov ecx, g_setEcx
        mov ebx, g_setEbx
        mov ebp, 0x44444444
        jmp dword ptr [g_jumpTo]
    }
}
void RunTo(uintptr_t from, uintptr_t stop) {
    uint8_t saved[5];
    DWORD old;
    VirtualProtect((void*)stop, 5, PAGE_EXECUTE_READWRITE, &old);
    memcpy(saved, (void*)stop, 5);
    *(uint8_t*)stop = 0xE9;
    *(int32_t*)(stop + 1) = (int32_t)((uintptr_t)Trap - (stop + 5));
    g_jumpTo = (uint32_t)from;
    if (setjmp(g_back) == 0) JumpIn();
    memcpy((void*)stop, saved, 5);
}

// A function in the image replaced by a return while a test runs (an engine sub-object's constructor or
// destructor, not what is tested), and put back after.
struct Stub {
    uintptr_t at;
    uint8_t saved[3];
    Stub(uintptr_t where, const uint8_t* code, size_t n) : at(where) {
        DWORD old;
        VirtualProtect((void*)at, 3, PAGE_EXECUTE_READWRITE, &old);
        memcpy(saved, (void*)at, 3);
        memcpy((void*)at, code, n);
    }
    ~Stub() { memcpy((void*)at, saved, 3); }
};
const uint8_t RET_10[3] = {0xC2, 0x10, 0x00}, RET[1] = {0xC3};

// ---- calling a function with eax and ecx ---------------------------------------------------------
uint32_t g_callTarget, g_inEax, g_inEcx, g_outEax;
__declspec(naked) void CallRegs() {
    __asm {
        push ebx
        push esi
        push edi
        push ebp
        mov eax, g_inEax
        mov ecx, g_inEcx
        call dword ptr [g_callTarget]
        mov g_outEax, eax
        pop ebp
        pop edi
        pop esi
        pop ebx
        ret
    }
}
uint32_t Call(uintptr_t fn, uint32_t eax, uint32_t ecx) {
    g_callTarget = (uint32_t)fn;
    g_inEax = eax;
    g_inEcx = ecx;
    CallRegs();
    return g_outEax;
}

// ---- the fake manager ---------------------------------------------------------------------------
struct Manager {
    uint8_t* p;
    uint32_t n, delta, size, bytes;
    explicit Manager(uint32_t nodes, uint32_t extraRecords = 0) : n(nodes) {
        delta = (nodes - NODES_OLD) * NODE_SIZE;
        size = MANAGER_SIZE + delta;
        bytes = size + extraRecords * NODE_SIZE + 0x1000;
        p = (uint8_t*)VirtualAlloc(nullptr, bytes, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
        memset(p, 0xCC, bytes);
    }
    ~Manager() { VirtualFree(p, 0, MEM_RELEASE); }
    uint8_t* rec(uint32_t i) { return p + NODES_AT + i * NODE_SIZE; }
    uintptr_t recAt(uint32_t i) { return (uintptr_t)rec(i); }
    uintptr_t tail(uint32_t vanillaOffset) { return (uintptr_t)(p + vanillaOffset + delta); }
    uint32_t addr() { return (uint32_t)(uintptr_t)p; }
    bool untouched(uint32_t from, uint32_t to) {
        for (uint32_t k = from; k < to; k++)
            if (p[k] != 0xCC) return false;
        return true;
    }
    bool tailUntouched() { return untouched(NODES_AT + n * NODE_SIZE, size); }   // the fields after the table
    bool afterUntouched() { return untouched(size, bytes); }                      // past the manager
};

bool NodeInited(const uint8_t* r) {           // what 0x007735C0 writes (the parts a test can read back)
    for (uint32_t k = 0x140; k <= 0x158; k += 4)
        if (U32((uintptr_t)r + k) != 0) return false;
    // +0x318: "and 0xF1" first, then "(x & 0x0E) | 0x40" at the end: 0x40 whatever the byte held
    return U32((uintptr_t)r + 0x194) == 0x64 && U32((uintptr_t)r + 0x198) == 1 && U32((uintptr_t)r + 0x1EC) == 3 &&
           U32((uintptr_t)r + 0x1F4) == 1 && U32((uintptr_t)r + 0x178) == 0xFFFFFFFF && r[0x318] == 0x40;
}
bool RecordBuilt(const uint8_t* r) {          // the constructor loop's stores, then the node initialiser
    return U32((uintptr_t)r) == VT_NODE && U32((uintptr_t)r + 0xA0) == VT_NODE_A && U32((uintptr_t)r + 0xA4) == 6 &&
           U32((uintptr_t)r + 0xE0) == VT_NODE_B && U32((uintptr_t)r + 0xE4) == 5 && U32((uintptr_t)r + 0xBC) == 0 &&
           U32((uintptr_t)r + 0xCC) == 0 && U32((uintptr_t)r + 0x10C) == 0 && U32((uintptr_t)r + 0x13C) == 0 &&
           NodeInited(r);
}
bool RecordTornDown(const uint8_t* r) {       // the destructor loop's stores, the rest untouched
    if (U32((uintptr_t)r) != VT_NODE || U32((uintptr_t)r + 0xA0) != VT_MTOBJECT || U32((uintptr_t)r + 0xE0) != VT_MTOBJECT)
        return false;
    for (uint32_t k = 4; k < NODE_SIZE; k++)
        if (k != 0xA0 && k != 0xA1 && k != 0xA2 && k != 0xA3 && k != 0xE0 && k != 0xE1 && k != 0xE2 && k != 0xE3 &&
            r[k] != 0xCC)
            return false;
    return true;
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
        return printf("SKIP: run me through collision_cap_stub.exe (host at %p, 0x%X bytes)\n", (void*)host,
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
    // What the tested code calls: InterlockedIncrement through the exe's import slot.
    FARPROC inc = GetProcAddress(GetModuleHandleW(L"kernel32.dll"), "InterlockedIncrement");
    if (!inc) return printf("kernel32 has no InterlockedIncrement export\n"), false;
    Put(IAT_INTERLOCKED_INCREMENT, (uint32_t)(uintptr_t)inc);
    Put(MANAGER, 0);
    return true;
}
}  // namespace

static int Run(int argc, wchar_t** argv);

// Called by collision_cap_stub.exe.  Never returns: the stub's code is gone once DDDA.exe is mapped.
extern "C" __declspec(dllexport) void HarnessMain() {
    int argc = 0;
    wchar_t** argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    int code = Run(argc, argv);
    fflush(stdout);
    ExitProcess((UINT)code);
}

static int Run(int argc, wchar_t** argv) {
    if (argc < 4) {
        printf("usage: collision_cap_stub <DDDA.exe> <collision_cap.asi> <expected nodes> [tamper|late]\n");
        return 1;
    }
    if (!MapImage(argv[1])) return 2;
    const uint32_t want = (uint32_t)_wtoi(argv[3]);
    const bool tamper = argc > 4 && wcscmp(argv[4], L"tamper") == 0;
    const bool late = argc > 4 && wcscmp(argv[4], L"late") == 0;
    if (late) Put(MANAGER, 0x12345678);               // the game built its manager before the plugin came
    if (tamper) *(uint8_t*)RESET_HITINFO ^= 0x01;      // one tail instruction is not the expected code

    SetEnvironmentVariableW(L"RIFTSTONE_COLLISION_HARNESS", L"1");
    HMODULE plugin = LoadLibraryW(argv[2]);
    auto nodesFn = plugin ? (int (*)())GetProcAddress(plugin, "CollisionCap_Nodes") : nullptr;
    const uint32_t n = nodesFn ? (uint32_t)nodesFn() : 0;
    printf("plugin (%u entry nodes expected)\n", want);
    Check(plugin != nullptr && nodesFn != nullptr, "the plugin loads");
    if (tamper || late) {
        Check(n == 0, tamper ? "one altered instruction: the plugin refuses"
                             : "the collision manager already exists: the plugin refuses");
        Check(U32(ALLOC_GAME + 1) == MANAGER_SIZE && U32(BOUND_GETNODE + 1) == NODES_OLD &&
              *(uint8_t*)BLOCKS[0].at == 0x3B && *(uint8_t*)BLOCKS[1].at == 0x3B && U32(COUNT_CTOR + 4) == NODES_OLD - 1,
              "  and nothing is patched");
        printf(g_fails ? "\n%d check(s) FAILED\n" : "\nall checks passed\n", g_fails);
        return g_fails ? 1 : 0;
    }
    Check(n == want, F("%u collision entry nodes a frame", n));
    if (g_fails) return 1;
    const uint32_t delta = (n - NODES_OLD) * NODE_SIZE;

    printf("patched values\n");
    {
        int bad = 0, counts[5] = {0, 0, 0, 0, 0};
        for (const Site& s : SITES) {
            uint32_t old;
            memcpy(&old, s.code + s.field, 4);
            uint32_t expect = s.kind == K_BOUND ? n : s.kind == K_COUNT_M1 ? n - 1 : old + delta;
            if (U32(s.at + s.field) != expect) bad++;
            for (uint32_t k = 0; k < s.len; k++)
                if ((k < s.field || k >= s.field + 4u) && *(const uint8_t*)(s.at + k) != s.code[k]) bad++;
            counts[s.kind]++;
        }
        Check(bad == 0, F("all %u sites hold their new values (%d tail fields moved by 0x%X, %d bounds = N, %d loop counts = N - 1, "
                          "%d allocations), the rest of each instruction untouched",
                          (unsigned)(sizeof SITES / sizeof SITES[0]), counts[K_TAIL] + counts[K_TAIL_IMM], delta,
                          counts[K_BOUND], counts[K_COUNT_M1], counts[K_ALLOC]));
        Check(U32(ALLOC_GAME + 1) == MANAGER_SIZE + delta, F("the manager is allocated 0x%X bytes (was 0x%X)", MANAGER_SIZE + delta, MANAGER_SIZE));
        Check(U32(BOUND_GETNODE + 1) == n, "getEntryNode's bound is N");
        Check(U32(COUNT_CTOR + 4) == n - 1 && U32(COUNT_DTOR + 1) == n - 1, "the constructor's and destructor's loops count N");
        Check(U32(RESET_HITINFO + 2) == HITINFO_CTR + delta, "reset clears the moved mNodeHitInfoCtr");
        bool jumps = true;
        for (const Block& b : BLOCKS) {
            HMODULE owner = nullptr;
            uintptr_t target = b.at + 5 + (uintptr_t)*(const int32_t*)(b.at + 1);
            GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                               (LPCWSTR)target, &owner);
            jumps &= *(const uint8_t*)b.at == 0xE9 && owner == plugin;
        }
        Check(jumps, "the sweep job's two count checks jump into the plugin");
    }

    printf("constructor (the real record loop; the records' sub-object constructor stubbed)\n");
    {
        Manager m(n);
        Stub stub(SUB_CTOR, RET_10, 3);
        g_setEsi = m.addr(); g_setEdi = SENTINEL_EDI; g_setEcx = 0; g_setEbx = SENTINEL_EBX;
        RunTo(CTOR_RUN, CTOR_END);
        Check(U32((uintptr_t)m.p) == VT_MANAGER && U32((uintptr_t)m.p + 0x20) == VT_RESOURCES && U32((uintptr_t)m.p + 0x24) == 0 &&
              U32((uintptr_t)m.p + 0x28) == 0 && m.p[0x2C] == 0 && U32((uintptr_t)m.p + 0x30) == 0,
              "the manager's vtable and its resource list are set");
        bool all = true;
        for (uint32_t i = 0; i < n; i++) all &= RecordBuilt(m.rec(i));
        Check(all, F("all %u records are built as the game builds its 800", n));
        Check(m.tailUntouched() && m.afterUntouched(), "  nothing past the Nth record is written");
        Check(g_trapEsi == m.addr() && g_trapEbp == m.addr() + NODES_AT + n * NODE_SIZE && g_trapEdi == g_trapEbp + 0xCC,
              "  the loop ran exactly N times and the manager register survives");
    }

    printf("the allocator (the real getEntryNode on an empty manager, N + 20 requests)\n");
    {
        Manager m(n);
        Put(MANAGER, m.addr());
        Put((uintptr_t)m.p + COUNT, 0);
        bool given = true, refused = true;
        for (uint32_t c = 1; c <= n + 20; c++) {
            uint32_t r = Call(GET_NODE, 0, 0);
            if (c < n) given &= r == m.recAt(c) && NodeInited(m.rec(c));
            else refused &= r == 0;
        }
        Check(given, F("records 1..%u are handed out, each initialised", n - 1));
        Check(refused, "every request after that is refused (NULL)");
        Check(U32((uintptr_t)m.p + COUNT) == n + 20, F("the count stands at %u: the refused requests raised it too (the overshoot)", n + 20));
        Check(m.tailUntouched() && m.afterUntouched(), "nothing past the table is written");
        Put(MANAGER, 0);
    }

    printf("the sweep job (the real code on a manager whose count passed the table)\n");
    {
        uint32_t keep1[4] = {0, 1, 0, 0}, keep2[4] = {0, 0x12, 0, 0}, drop[4] = {0, 3, 0, 0};
        const uint32_t past = 50;
        Manager m(n, past);
        auto lay = [&](uint32_t i, const uint32_t* a, const uint32_t* b, const uint32_t* c) {
            Put(m.recAt(i) + REC_A, (uint32_t)(uintptr_t)a);
            Put(m.recAt(i) + REC_B, (uint32_t)(uintptr_t)b);
            Put(m.recAt(i) + REC_C, (uint32_t)(uintptr_t)c);
        };
        auto layAll = [&]() {
            lay(0, drop, drop, drop);
            for (uint32_t i = 1; i < n; i++) lay(i, i % 2 ? keep1 : drop, i % 3 ? keep2 : drop, drop);
            for (uint32_t i = n; i < n + past; i++) lay(i, drop, drop, drop);
        };
        auto swept = [&](uint32_t i) {
            return U32(m.recAt(i) + REC_A) == (i % 2 ? (uint32_t)(uintptr_t)keep1 : 0) &&
                   U32(m.recAt(i) + REC_B) == (i % 3 ? (uint32_t)(uintptr_t)keep2 : 0) && U32(m.recAt(i) + REC_C) == 0;
        };
        auto asLaid = [&](uint32_t i) {
            return U32(m.recAt(i) + REC_A) == (uint32_t)(uintptr_t)drop && U32(m.recAt(i) + REC_B) == (uint32_t)(uintptr_t)drop &&
                   U32(m.recAt(i) + REC_C) == (uint32_t)(uintptr_t)drop;
        };
        layAll();
        Put((uintptr_t)m.p + COUNT, n + past);
        Put(m.tail(CURSOR), 0);
        Call(SWEEP_JOB, 0, m.addr());
        bool all = true;
        for (uint32_t i = 1; i < n; i++) all &= swept(i);
        Check(all, F("count %u: records 1..%u are swept (kinds 1 and 2 kept, others dropped)", n + past, n - 1));
        Check(asLaid(0), "  record 0 is never swept (the cursor starts at 1, as in the game)");
        bool beyond = true;
        for (uint32_t i = n; i < n + past; i++) beyond &= asLaid(i);
        Check(beyond, F("  the %u records past the table are untouched: the sweep stopped at N", past));
        Check(U32(m.tail(CURSOR)) == n, F("  the cursor stopped at %u", n));

        layAll();
        Put((uintptr_t)m.p + COUNT, 5);
        Put(m.tail(CURSOR), 0);
        Call(SWEEP_JOB, 0, m.addr());
        all = true;
        for (uint32_t i = 1; i < 5; i++) all &= swept(i);
        for (uint32_t i = 5; i < n + past; i++) all &= i % 2 == 0 ? U32(m.recAt(i) + REC_C) == (uint32_t)(uintptr_t)drop
                                                                    : U32(m.recAt(i) + REC_C) == (uint32_t)(uintptr_t)drop;
        Check(all && U32(m.tail(CURSOR)) == 5, "count 5: records 1..4 swept, the rest untouched, the cursor at 5");

        layAll();
        Put((uintptr_t)m.p + COUNT, 0);
        Put(m.tail(CURSOR), 0);
        Call(SWEEP_JOB, 0, m.addr());
        all = true;
        for (uint32_t i = 1; i < n + past; i++) all &= U32(m.recAt(i) + REC_C) == (uint32_t)(uintptr_t)drop;
        Check(all && U32(m.tail(CURSOR)) == 1, "count 0: nothing swept, the cursor at 1");
    }

    printf("destructor (the real record loop; the records' sub-object destructor stubbed)\n");
    {
        Manager m(n);
        Stub stub(SUB_DTOR, RET, 1);
        g_setEsi = m.addr(); g_setEdi = SENTINEL_EDI; g_setEcx = 0; g_setEbx = SENTINEL_EBX;
        RunTo(DTOR_LOOP, DTOR_END);
        bool all = true;
        for (uint32_t i = 0; i < n; i++) all &= RecordTornDown(m.rec(i));
        Check(all, F("all %u records are torn down as the game tears down its 800", n));
        Check(m.untouched(0, NODES_AT) && m.tailUntouched() && m.afterUntouched(), "  nothing before or after them");
        Check(g_trapEsi == m.addr() && g_trapEdi == m.addr() + NODES_AT && g_trapEbx == 0xFFFFFFFF,
              "  the loop walked down to the first record and stopped");
    }

    printf("reset (the real code, with the list clear)\n");
    {
        Manager m(n);
        Put((uintptr_t)m.p + COUNT, 0x11);
        Put((uintptr_t)m.p + PEAK, 0x22);
        Put(m.tail(HITINFO_CTR), 1);
        Put(m.tail(HITINFO_MAX), 1);
        Put(m.tail(FIELD_510), 1);
        Put(m.tail(LIST_COUNT), 0);
        Put(m.tail(LIST_DATA), 0);
        for (uint32_t k = 0; k < 8; k++) {
            Put(m.tail(LISTS) + k * 0x14 + 4, 0);
            Put(m.tail(LISTS) + k * 0x14 + 0x10, 0);
        }
        Call(RESET, 0, m.addr());
        Check(U32((uintptr_t)m.p + COUNT) == 0 && U32((uintptr_t)m.p + PEAK) == 0, "the count and its peak are cleared");
        Check(U32(m.tail(HITINFO_CTR)) == 0 && U32(m.tail(HITINFO_MAX)) == 0 && U32(m.tail(FIELD_510)) == 0 &&
              U32(m.tail(LIST_COUNT)) == 0, "  the moved tail fields are cleared at their new places");
        bool lists = true;
        for (uint32_t k = 0; k < 8; k++) lists &= U32(m.tail(LISTS) + k * 0x14 + 4) == 0;
        Check(lists, "  the eight lists are emptied");
        Check(m.untouched(NODES_AT, NODES_AT + n * NODE_SIZE) && m.afterUntouched(), "  the records and what lies past the manager are untouched");
    }

    printf(g_fails ? "\n%d check(s) FAILED\n" : "\nall checks passed\n", g_fails);
    return g_fails ? 1 : 0;
}

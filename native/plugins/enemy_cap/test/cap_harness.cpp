// cap_harness_core -- runs the enemy_cap patches inside the real game code, without the game.
//
//   cap_stub.exe <DDDA.exe> <enemy_cap.asi> <expected slots> [tamper]
//
// Maps DDDA.exe's image at its fixed base (0x00400000) over the stub's image, which was sized to
// reserve that range, loads the plugin (which verifies and patches the mapped code) and checks:
//   * the patched values: the manager's size, the usable count, moved references, loop counts;
//   * the four replaced runs (construct, clear, reset, final): the real code is entered at each run
//     with a fake manager and stopped where the run ends; all N slots at the new home are written
//     exactly as the vanilla run wrote its ten, nothing past them, and every register survives;
//   * the destructor's slot loop, run the same way: it walks all N moved slots;
//   * three real functions called on the fake manager with only the last slot (N - 1) occupied:
//     addEnemyPriority, a unit scan and killEnemyFieldPrio each reach it, and addEnemyPriority
//     returns N when no slot matches (its loop bound);
//   * the slot record: the plugin's reader on a fake manager and a fake stage (run_tests.py then
//     reads the lines it wrote, the exit line included); with "norecord" (record = 0) it takes none;
//   * with "tamper", one site is altered first: the plugin must refuse and patch nothing;
//   * with "late", the spawn manager already exists (built at the old size): refuse, patch nothing.
// Prints "pass"/"FAIL" lines and exits 0 when everything passed, 1 on a failure, 2 when the image
// cannot be mapped here (reported as a skip by run_tests.py).
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
constexpr uint32_t OLD = 0x844, END = 0x984, NEW = 0x1B950, DELTA = 0x1B10C, MSIZE = 0x1B950, SLOT = 0x20;
constexpr uint32_t VT_UNIT_DATA = 0x01562414, VT_MTOBJECT = 0x01558134;
constexpr uintptr_t IAT_ENTER = 0x0139D160, IAT_LEAVE = 0x0139D164, UNIT_CS = 0x018D0A68, UNIT_TABLE = 0x018D0A44;
constexpr uintptr_t ALLOC_GAME = 0x0041C4C2, ALLOC_DTI = 0x0049F86B, ALLOC_REG = 0x0132D026, USABLE = 0x004A235C;
constexpr uintptr_t RUN_CONSTRUCT = 0x0049F9DD, END_CONSTRUCT = 0x0049FBBD, RUN_CLEAR = 0x004A54AA, END_CLEAR = 0x004A555E;
constexpr uintptr_t RUN_RESET = 0x004A571F, END_RESET = 0x004A57D3, RUN_FINAL = 0x004A629A, END_FINAL = 0x004A634E;
constexpr uintptr_t DTOR_LOOP = 0x004A054E, DTOR_LOOP_END = 0x004A056C;
constexpr uintptr_t ADD_PRIORITY = 0x004A6570, UNIT_SCAN = 0x004A9170, KILL_FIELD_PRIO = 0x004A6490;
constexpr uintptr_t SET_MANAGER = 0x018FA504, S_AREA = 0x018D099C;
constexpr uint32_t UNIT_NUM_ENEMY = 0x1B8D0;
constexpr uint32_t SENTINEL_ESI = 0x11111111, SENTINEL_EDI = 0x22222222, SENTINEL_EBP = 0x44444444;

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

// ---- calling a register-argument function -------------------------------------------------------
uint32_t g_callTarget, g_inEax, g_inEdx, g_outEax;
__declspec(naked) void CallRegs() {
    __asm {
        push ebx
        push esi
        push edi
        push ebp
        mov eax, g_inEax
        mov edx, g_inEdx
        call dword ptr [g_callTarget]
        mov g_outEax, eax
        pop ebp
        pop edi
        pop esi
        pop ebx
        ret
    }
}
uint32_t Call(uintptr_t fn, uint32_t eax, uint32_t edx) {
    g_callTarget = (uint32_t)fn;
    g_inEax = eax;
    g_inEdx = edx;
    CallRegs();
    return g_outEax;
}

// ---- the fake manager ---------------------------------------------------------------------------
struct Manager {
    uint8_t* p;
    uint32_t n, bytes;
    explicit Manager(uint32_t slots) : n(slots), bytes(MSIZE + slots * SLOT) {
        p = (uint8_t*)VirtualAlloc(nullptr, bytes + 0x1000, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
        memset(p, 0xCC, bytes + 0x1000);
    }
    ~Manager() { VirtualFree(p, 0, MEM_RELEASE); }
    uint8_t* slot(uint32_t i) { return p + NEW + i * SLOT; }
    uint8_t* old(uint32_t i) { return p + OLD + i * SLOT; }
    uint32_t addr() { return (uint32_t)(uintptr_t)p; }
    bool guards() {   // the bytes around the two slot ranges are as the test left them
        for (uint32_t k = 0; k < 0x20; k++)
            if (p[OLD - 1 - k] != 0xCC || p[END + k] != 0xCC || p[NEW + n * SLOT + k] != 0xCC) return false;
        return true;
    }
    void fillSlots(uint8_t v) {
        memset(p + OLD, v, END - OLD);
        memset(p + NEW, v, n * SLOT);
    }
};

bool IsInit(const uint8_t* s) {
    if (U32((uintptr_t)s) != VT_UNIT_DATA) return false;
    for (int k = 4; k < 0x20; k++)
        if (s[k]) return false;
    return true;
}
bool IsCleared(const uint8_t* s, uint8_t fill) {     // +4, +8, +0x1C zero; the rest untouched
    for (int k = 0; k < 0x20; k++) {
        bool zero = (k >= 4 && k < 8) || (k >= 8 && k < 12) || k >= 0x1C;
        if (zero ? s[k] != 0 : s[k] != fill) return false;
    }
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
        return printf("SKIP: run me through cap_stub.exe (host at %p, 0x%X bytes)\n", (void*)host,
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
    // What the tested code calls: the unit-table lock (0x004010B0 takes it) and an empty unit table.
    *(uint32_t*)IAT_ENTER = (uint32_t)(uintptr_t)&EnterCriticalSection;
    *(uint32_t*)IAT_LEAVE = (uint32_t)(uintptr_t)&LeaveCriticalSection;
    InitializeCriticalSection((CRITICAL_SECTION*)UNIT_CS);
    *(uint32_t*)UNIT_TABLE = 0;
    return true;
}
}  // namespace

static int Run(int argc, wchar_t** argv);

// Called by cap_stub.exe.  Never returns: the stub's code is gone once DDDA.exe is mapped.
extern "C" __declspec(dllexport) void HarnessMain() {
    int argc = 0;
    wchar_t** argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    int code = Run(argc, argv);
    fflush(stdout);
    ExitProcess((UINT)code);
}

static int Run(int argc, wchar_t** argv) {
    if (argc < 4) {
        printf("usage: cap_stub <DDDA.exe> <enemy_cap.asi> <expected slots> [tamper]\n");
        return 1;
    }
    if (!MapImage(argv[1])) return 2;
    const uint32_t want = (uint32_t)_wtoi(argv[3]);
    const bool tamper = argc > 4 && wcscmp(argv[4], L"tamper") == 0;
    const bool norecord = argc > 4 && wcscmp(argv[4], L"norecord") == 0;
    const bool late = argc > 4 && wcscmp(argv[4], L"late") == 0;
    if (late) *(uint32_t*)SET_MANAGER = 0x12345678;   // the game built its manager before the plugin came
    const uint32_t vanillaMove = U32(0x004A6A31 + 3);
    if (tamper) *(uint8_t*)0x004A6A31 ^= 0x01;          // one moved instruction is not the expected code

    SetEnvironmentVariableW(L"RIFTSTONE_CAP_HARNESS", L"1");
    HMODULE plugin = LoadLibraryW(argv[2]);
    auto slotsFn = plugin ? (int (*)())GetProcAddress(plugin, "EnemyCap_Slots") : nullptr;
    const uint32_t n = slotsFn ? (uint32_t)slotsFn() : 0;
    printf("plugin (%u slots expected)\n", want);
    Check(plugin != nullptr && slotsFn != nullptr, "the plugin loads");
    if (tamper || late) {
        Check(n == 0, tamper ? "one altered instruction: the plugin refuses"
                             : "the spawn manager already exists: the plugin refuses");
        Check(U32(USABLE + 6) == 10 && U32(ALLOC_GAME + 1) == MSIZE && *(uint8_t*)RUN_CONSTRUCT == 0x89 &&
              U32(0x004A6AEA) == 0xFFFFF7B0, "  and nothing is patched");
        printf(g_fails ? "\n%d check(s) FAILED\n" : "\nall checks passed\n", g_fails);
        return g_fails ? 1 : 0;
    }
    Check(n == want, F("%u enemies at once", n));
    if (g_fails) return 1;

    printf("patched values\n");
    Check(U32(ALLOC_GAME + 1) == MSIZE + n * SLOT && U32(ALLOC_DTI + 1) == MSIZE + n * SLOT && U32(ALLOC_REG + 1) == MSIZE + n * SLOT,
          F("the manager is allocated 0x%X bytes (was 0x%X)", MSIZE + n * SLOT, MSIZE));
    Check(U32(USABLE + 6) == n, F("stage set-up makes %u slots usable", n));
    Check(vanillaMove == 0x848 && U32(0x004A6A31 + 3) == 0x848 + DELTA, "registerEmData reads mpUnit at the new home");
    Check(U32(0x004A6AEA) == 0xFFFFF7B0u - DELTA, "registerEmData's slot-number constant moves with it");
    Check(*(uint8_t*)(0x004A658D + 2) == n && U32(0x004A4272 + 1) == n, "loop counts are N");
    Check(*(uint8_t*)RUN_CONSTRUCT == 0xE9 && *(uint8_t*)RUN_CLEAR == 0xE9 && *(uint8_t*)RUN_RESET == 0xE9 &&
          *(uint8_t*)RUN_FINAL == 0xE9, "the four runs jump to the plugin");

    printf("construct (the real constructor from its slot run to the NPC slots)\n");
    {
        Manager m(n);
        g_setEdi = m.addr(); g_setEsi = SENTINEL_ESI; g_setEcx = VT_UNIT_DATA; g_setEbx = 0;
        RunTo(RUN_CONSTRUCT, END_CONSTRUCT);
        bool all = true;
        for (uint32_t i = 0; i < n; i++) all &= IsInit(m.slot(i));
        for (uint32_t i = 0; i < 10; i++) all &= IsInit(m.old(i));
        Check(all, F("all %u slots at +0x%X (and the ten vanilla ones) are built as the game builds them", n, NEW));
        Check(m.guards(), "  nothing past them is written");
        Check(g_trapEdi == m.addr() && g_trapEsi == SENTINEL_ESI && g_trapEcx == VT_UNIT_DATA && g_trapEbx == 0 &&
              g_trapEbp == SENTINEL_EBP, "  every register survives");
    }
    const struct { const char* name; uintptr_t at, stop; } runs[] = {
        {"clear", RUN_CLEAR, END_CLEAR}, {"reset", RUN_RESET, END_RESET}, {"final", RUN_FINAL, END_FINAL}};
    for (auto& r : runs) {
        printf("%s (the real code from its slot run onward)\n", r.name);
        Manager m(n);
        m.fillSlots(0x55);
        g_setEdi = SENTINEL_EDI; g_setEsi = m.addr(); g_setEcx = 0x12345678; g_setEbx = 0;
        RunTo(r.at, r.stop);
        bool all = true;
        for (uint32_t i = 0; i < n; i++) all &= IsCleared(m.slot(i), 0x55);
        Check(all, F("all %u slots: unit, state and group cleared, the rest kept", n));
        Check(m.guards(), "  nothing past them is written");
        Check(g_trapEsi == m.addr() && g_trapEdi == SENTINEL_EDI && g_trapEcx == 0x12345678 && g_trapEbp == SENTINEL_EBP,
              "  every register survives");
    }

    printf("destructor (its slot loop, the real code)\n");
    {
        Manager m(n);
        for (uint32_t i = 0; i < n; i++) *(uint32_t*)m.slot(i) = VT_UNIT_DATA;
        for (uint32_t i = 0; i < 10; i++) *(uint32_t*)m.old(i) = VT_UNIT_DATA;
        g_setEdi = SENTINEL_EDI; g_setEsi = m.addr(); g_setEcx = 0; g_setEbx = 0;
        RunTo(DTOR_LOOP, DTOR_LOOP_END);
        bool all = true;
        for (uint32_t i = 0; i < n; i++) all &= U32((uintptr_t)m.slot(i)) == VT_MTOBJECT;
        Check(all, F("walks all %u moved slots", n));
        Check(m.guards(), "  and stops there");
    }

    printf("real functions on a manager whose only occupied slot is the last (%u)\n", n - 1);
    {
        Manager m(n);
        for (uint32_t i = 0; i < n; i++) memset(m.slot(i), 0, SLOT);
        Check(Call(ADD_PRIORITY, 0, m.addr()) == n, F("addEnemyPriority, nothing to raise: its loop runs %u times", n));
        uint8_t* last = m.slot(n - 1);
        *(uint32_t*)(last + 4) = 0x0BADF00D;   // mpUnit
        *(uint32_t*)(last + 8) = 1;            // mState
        *(uint32_t*)(last + 0xC) = 5;          // mPrio
        uint32_t r = Call(ADD_PRIORITY, 0, m.addr());
        Check(r == (uint32_t)(uintptr_t)(last + 0xC) && U32((uintptr_t)last + 0xC) == 5 + 0xF,
              "addEnemyPriority raises slot N-1's priority by 15 and returns it");

        std::vector<uint8_t> unit(0x6000, 0);
        unit[0x206C] = 0x80;
        unit[0x2D] = 0xB6;
        memset(last, 0, SLOT);
        *(uint32_t*)(last + 4) = (uint32_t)(uintptr_t)unit.data();
        *(uint32_t*)(last + 8) = 1;
        Check(Call(UNIT_SCAN, m.addr(), 0) == (uint32_t)(uintptr_t)unit.data(), "the unit scan finds the unit in slot N-1");
        *(uint32_t*)(last + 8) = 0;
        Check(Call(UNIT_SCAN, m.addr(), 0) == 0, "  and nothing once that slot is idle");

        memset(last, 0, SLOT);
        *(uint32_t*)(last + 4) = (uint32_t)(uintptr_t)unit.data();
        *(uint32_t*)(last + 0xC) = 3;          // below 0x5F: a field-priority enemy
        *(uint32_t*)(last + 0x1C) = 0x77;      // mpGroupParam
        uint8_t* keep = m.slot(n - 2);
        *(uint32_t*)(keep + 4) = (uint32_t)(uintptr_t)unit.data();
        *(uint32_t*)(keep + 0xC) = 0x60;       // not field priority: kept
        Call(KILL_FIELD_PRIO, m.addr(), 0);
        Check(U32((uintptr_t)last + 4) == 0 && U32((uintptr_t)last + 8) == 0 && U32((uintptr_t)last + 0x1C) == 0,
              "killEnemyFieldPrio releases slot N-1");
        Check(U32((uintptr_t)keep + 4) == (uint32_t)(uintptr_t)unit.data(), "  and keeps the enemy that is not field priority");
        Check(m.guards(), "  nothing outside the slots is written");
    }

    printf("the slot record (the plugin's reader on a fake manager in stage 100)\n");
    {
        auto sample = plugin ? (int (*)())GetProcAddress(plugin, "EnemyCap_Sample") : nullptr;
        Check(sample != nullptr, "EnemyCap_Sample is exported");
        if (sample && norecord) {
            Check(sample() == -1, "record = 0: no samples");
        } else if (sample) {
            Check(sample() == -1, "no manager yet: no sample");
            Manager m(n);
            for (uint32_t i = 0; i < n; i++) {
                memset(m.slot(i), 0, SLOT);
                *(uint32_t*)m.slot(i) = VT_UNIT_DATA;
            }
            *(uint32_t*)(m.p + UNIT_NUM_ENEMY) = n;
            std::vector<uint8_t> area(0x3838, 0), stage(0x728, 0);
            *(uint32_t*)(area.data() + 0x3834) = (uint32_t)(uintptr_t)stage.data();
            stage[0x20] = 1;
            *(int32_t*)(stage.data() + 0x724) = 100;
            *(uint32_t*)S_AREA = (uint32_t)(uintptr_t)area.data();
            *(uint32_t*)SET_MANAGER = m.addr();
            Check(sample() == 0, "an empty manager: none in use");
            for (uint32_t i = 0; i < 3; i++) *(uint32_t*)(m.slot(i) + 8) = 1;   // mState
            *(uint32_t*)(m.slot(0) + 4) = 0x0BADF00D;                            // mpUnit
            Check(sample() == 3, "three slots in use");
            for (uint32_t i = 0; i < n; i++) *(uint32_t*)(m.slot(i) + 8) = 1;
            Check(sample() == (int)n, F("all %u in use", n));
            *(uint32_t*)(m.slot(1) + 8) = 0;
            Check(sample() == (int)n - 1, "one free again");
            *(uint32_t*)m.slot(n - 1) = VT_MTOBJECT;      // the destructor has been through: not read
            Check(sample() == -1, "a manager being destroyed: no sample");
            *(uint32_t*)SET_MANAGER = 0x00000010;          // unreadable
            Check(sample() == -1, "an unreadable manager pointer: no sample, and no fault");
            *(uint32_t*)SET_MANAGER = 0;
            *(uint32_t*)S_AREA = 0;
        }
    }

    printf(g_fails ? "\n%d check(s) FAILED\n" : "\nall checks passed\n", g_fails);
    return g_fails ? 1 : 0;
}

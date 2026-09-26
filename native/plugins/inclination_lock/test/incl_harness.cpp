// incl_harness_core -- runs inclination_lock inside the real game code, without the game.
//
//   incl_stub.exe <DDDA.exe> <inclination_lock.asi> <freeze|commands|off|bogus>
//
// Maps DDDA.exe's image at its fixed base (0x00400000) over the stub's image, loads the plugin (which
// verifies and patches the mapped code), then runs the game's own inclination code on fake objects:
//   * after()'s add (0x00410D66 up to calcHitInfo at 0x00410D9B): a pawn's value and a pending change
//     go in, the real instruction stream runs (through the plugin's jmp when frozen), the value comes out;
//   * calcProtection (0x004117F0), whole: the Guardian change for Go!, Help!, Come! and no order, and
//     the cooldown after one;
//   * calcCuriosity (0x00411C90), whole: the Pioneer change for each order;
//   * calcPrudent's order step (0x004112A7 to its ret, with its frame): the Medicant change for each order.
// The changes and cooldowns are the constructor's own constants, read from the mapped image.
// Profiles (run_tests.py writes the matching inclination_lock.ini):
//   freeze    the shipped ini: values never move; the steps still count orders (after() drops the sums)
//   commands  orders change nothing and start no cooldown; after() adds as in vanilla
//   off       Mode = off: nothing patched, all vanilla
//   bogus     Mode = sometimes: an unknown mode patches nothing
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
constexpr uintptr_t PLAYER_MANAGER = 0x018FA4EC;  // sPlayerManager::mpInstance: +0x99C Arisen, +0x9A0 main pawn
constexpr uintptr_t GAME_STATE = 0x018FA4BC;      // +0xBE2CC: a byte both order steps test
constexpr uintptr_t FRAME = 0x018D0B28;           // +0x70: the frame's time step
constexpr uintptr_t VALUE_MAX = 0x0182E0D0;       // after(): the upper limit of a value (1000)

constexpr uintptr_t ADD_ENTER = 0x00410D66, ADD_NEXT = 0x00410D9B;
constexpr uintptr_t CALC_PROTECTION = 0x004117F0, CALC_CURIOSITY = 0x00411C90, PRUDENT_ORDER = 0x004112A7;
constexpr uintptr_t FREEZE_SITE = 0x00410D6B;
const uintptr_t COMMAND_SITES[5] = {0x004118B1, 0x004118FC, 0x00411950, 0x004112AE, 0x00411D87};

// The constructor's constants (sAICharacterInfo, 0x00410470..): .rdata addresses.
constexpr uintptr_t K_GUARD_COME = 0x01518668, K_GUARD_HELP = 0x0161C7C4, K_GUARD_GO = 0x0161C270,
                    K_COOLDOWN = 0x014F0840, K_PIONEER_GO = 0x015184E0, K_PIONEER_EVERY = 0x0161C63C,
                    K_PIONEER_PASSIVE = 0x0161C7B8, K_PIONEER_HIGH_COOLDOWN = 0x014F8E08,
                    K_PIONEER_HIGH_EVERY = 0x0161C7B4, K_PIONEER_HIGH_GO = 0x014EA574, K_MEDICANT_HELP = 0x014F9E04;

constexpr uint32_t GO = 9, HELP = 10, COME = 11, NONE = 8;

int g_fails = 0;
void Check(bool ok, const std::string& what) {
    printf("  %s  %s\n", ok ? "pass" : "FAIL", what.c_str());
    if (!ok) g_fails++;
}

float K(uintptr_t at) { return *(const float*)at; }
std::string F(const char* fmt, double a = 0, double b = 0, double c = 0) {
    char s[240];
    _snprintf_s(s, sizeof s, _TRUNCATE, fmt, a, b, c);
    return s;
}

// ---- the fake world the order steps read ---------------------------------------------------------
struct World {
    alignas(16) uint8_t manager[0x1000];
    alignas(16) uint8_t arisen[0x5000];  // +0x4ABC: the pawn order
    alignas(16) uint8_t pawn[0x3000];    // +0x2E64: its AI object
    alignas(16) uint8_t ai[0x400];       // +0x2B2 active, +0x5C order state, +0x88 cAICharacterInfo
    alignas(16) uint8_t orderState[0x600];
    alignas(16) uint8_t pioneer[0x10];   // a cInfo: +4 the pawn's Pioneer value
    alignas(16) uint8_t frame[0x100];
    alignas(16) uint8_t self[0x400];     // the sAICharacterInfo
    uint8_t* state;                      // 0xC0000 bytes
};
World* w;

void SetupWorld() {
    w = (World*)VirtualAlloc(nullptr, sizeof(World), MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    w->state = (uint8_t*)VirtualAlloc(nullptr, 0xC0000, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    *(uint32_t*)(w->manager + 0x99C) = (uint32_t)(uintptr_t)w->arisen;
    *(uint32_t*)(w->manager + 0x9A0) = (uint32_t)(uintptr_t)w->pawn;
    *(uint32_t*)(w->pawn + 0x2E64) = (uint32_t)(uintptr_t)w->ai;
    w->ai[0x2B2] = 1;
    *(uint32_t*)(w->ai + 0x5C) = (uint32_t)(uintptr_t)w->orderState;
    *(uint32_t*)(w->ai + 0x88 + 0x48) = (uint32_t)(uintptr_t)w->pioneer;  // the Pioneer cInfo
    *(float*)(w->pioneer + 4) = 500.0f;                                    // between the 300 and 700 bands
    *(float*)(w->frame + 0x70) = 1.0f;
    *(uint32_t*)PLAYER_MANAGER = (uint32_t)(uintptr_t)w->manager;
    *(uint32_t*)GAME_STATE = (uint32_t)(uintptr_t)w->state;
    *(uint32_t*)FRAME = (uint32_t)(uintptr_t)w->frame;
}

// sAICharacterInfo as the constructor leaves it (the fields the three order steps use).
void ResetSelf() {
    uint8_t* s = w->self;
    memset(s, 0, sizeof w->self);
    for (uint32_t o : {0x128u, 0x134u, 0x140u, 0x19Cu}) *(float*)(s + o) = K(K_COOLDOWN);
    *(float*)(s + 0x148) = K(K_GUARD_COME);
    *(float*)(s + 0x14C) = K(K_GUARD_HELP);
    *(float*)(s + 0x150) = K(K_GUARD_GO);
    *(float*)(s + 0x1A4) = K(K_PIONEER_EVERY);
    *(float*)(s + 0x1AC) = K(K_PIONEER_GO);
    *(float*)(s + 0x1B0) = K(K_PIONEER_PASSIVE);
    *(float*)(s + 0x1B4) = K(K_PIONEER_HIGH_COOLDOWN);
    *(float*)(s + 0x1B8) = K(K_PIONEER_HIGH_EVERY);
    *(float*)(s + 0x1BC) = K(K_PIONEER_HIGH_GO);
}

float Sum(uint32_t off) { return *(const float*)(w->self + off); }  // a pending change: +0x24 + 4 * inclination
constexpr uint32_t MEDICANT = 0x28, GUARDIAN = 0x38, PIONEER = 0x40;

// ---- calling the game's steps ------------------------------------------------------------------
uint32_t g_fn, g_self, g_arisen, g_site;
float g_amount;

// calcProtection and calcCuriosity take sAICharacterInfo in eax.
__declspec(naked) void CallWithEaxAsm() {
    __asm {
        push ebx
        push esi
        push edi
        push ebp
        mov eax, g_self
        call dword ptr [g_fn]
        pop ebp
        pop edi
        pop esi
        pop ebx
        ret
    }
}
void CallWithEax(uintptr_t fn) {
    g_fn = (uint32_t)fn;
    g_self = (uint32_t)(uintptr_t)w->self;
    CallWithEaxAsm();
}

// calcPrudent's order step, entered at 0x004112A7 as its body reaches it: esi = sAICharacterInfo,
// ebp = the Arisen, xmm5 = the Medicant change, xmm3 = 0, and the frame its two exits unwind
// (pop edi / pop ebp / pop ebx / add esp, 8 / ret), which returns here.
__declspec(naked) void RunPrudentOrderAsm() {
    __asm {
        push ebx
        push esi
        push edi
        push ebp
        call step_in
        pop ebp
        pop edi
        pop esi
        pop ebx
        ret
    step_in:
        sub esp, 8
        push ebx
        push ebp
        push edi
        mov ebp, g_arisen
        mov esi, g_self
        movss xmm5, dword ptr [g_amount]
        xorps xmm3, xmm3
        jmp dword ptr [g_site]
    }
}
void RunPrudentOrder() {
    g_self = (uint32_t)(uintptr_t)w->self;
    g_arisen = (uint32_t)(uintptr_t)w->arisen;
    g_amount = K(K_MEDICANT_HELP);
    g_site = PRUDENT_ORDER;
    RunPrudentOrderAsm();
}

void SetOrder(uint32_t order) { *(uint32_t*)(w->arisen + 0x4ABC) = order; }

// ---- after()'s add, entered at 0x00410D66 ----------------------------------------------------------
jmp_buf g_back;
uint32_t g_slot, g_change, g_addSite = ADD_ENTER, g_entryEsp;
uint32_t g_capEax, g_capEbx, g_capEsi, g_capEdi, g_capEbp, g_capEsp;

void __cdecl CaptureC() { longjmp(g_back, 1); }

// Placed by the harness at 0x00410D9B, the next step (push 1 / call calcHitInfo).
__declspec(naked) void Capture() {
    __asm {
        mov g_capEax, eax
        mov g_capEbx, ebx
        mov g_capEsi, esi
        mov g_capEdi, edi
        mov g_capEbp, ebp
        mov g_capEsp, esp
        call CaptureC
    }
}

// The loop's state at the add: ebp -> the slot holding the pawn's cInfo, edi -> the pending change.
__declspec(naked) void JumpToAdd() {
    __asm {
        sub esp, 0x100
        mov ebp, g_slot
        mov edi, g_change
        mov ebx, 0x33333333
        mov esi, 0x22222222
        mov eax, 0x55555555
        mov g_entryEsp, esp
        jmp dword ptr [g_addSite]
    }
}

#pragma warning(push)
#pragma warning(disable : 4611)
void EnterAdd() {
    if (setjmp(g_back) == 0) JumpToAdd();
}
#pragma warning(pop)

// The pawn's value after one pass of after()'s add; regs = whether the loop's registers came through.
float RunAdd(float value, float change, bool& regs) {
    alignas(16) static uint8_t info[0x10];
    static uint32_t slot;
    static float pending;
    memset(info, 0, sizeof info);
    *(float*)(info + 4) = value;
    slot = (uint32_t)(uintptr_t)info;
    pending = change;
    g_slot = (uint32_t)(uintptr_t)&slot;
    g_change = (uint32_t)(uintptr_t)&pending;
    g_capEsp = 0;
    EnterAdd();
    regs = g_capEax == slot && g_capEbx == 0x33333333 && g_capEsi == 0x22222222 && g_capEdi == g_change &&
           g_capEbp == g_slot && g_capEsp == g_entryEsp;
    return *(const float*)(info + 4);
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
        return printf("SKIP: run me through incl_stub.exe (host at %p, 0x%X bytes)\n", (void*)host,
                      (unsigned)hostNt->OptionalHeader.SizeOfImage),
               false;
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
    return true;
}

void PlaceCapture() {
    uint8_t* p = (uint8_t*)ADD_NEXT;
    int32_t rel = (int32_t)((uintptr_t)Capture - (ADD_NEXT + 5));
    p[0] = 0xE9;
    memcpy(p + 1, &rel, 4);
}

// 'f' when the freeze site is patched, then one character per command site: '.' vanilla, 'j' jmp.
std::string Sites() {
    std::string s = *(const uint8_t*)FREEZE_SITE == 0xEB ? "f" : *(const uint8_t*)FREEZE_SITE == 0x74 ? "-" : "?";
    for (uintptr_t a : COMMAND_SITES) s += *(const uint8_t*)a == 0xEB ? 'j' : *(const uint8_t*)a == 0x75 ? '.' : '?';
    return s;
}
}  // namespace

static int Run(int argc, wchar_t** argv);

// Called by incl_stub.exe.  Never returns: the stub's code is gone once DDDA.exe is mapped.
extern "C" __declspec(dllexport) void HarnessMain() {
    int argc = 0;
    wchar_t** argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    int code = Run(argc, argv);
    fflush(stdout);
    ExitProcess((UINT)code);
}

static int Run(int argc, wchar_t** argv) {
    if (argc < 4) {
        printf("usage: incl_stub <DDDA.exe> <inclination_lock.asi> <freeze|commands|off|bogus>\n");
        return 1;
    }
    const std::wstring profile = argv[3];
    const bool freeze = profile == L"freeze", commands = profile == L"commands";
    if (!MapImage(argv[1])) return 2;

    Check(Sites() == "-.....", "the mapped game code has all six sites as shipped (je at 0x00410D6B, five jne)");
    Check(K(K_GUARD_COME) == 4.0f && K(K_GUARD_HELP) == 2.67f && K(K_GUARD_GO) == -4.0f && K(K_COOLDOWN) == 900.0f,
          "the constructor's Guardian changes are +4 (Come!), +2.67 (Help!), -4 (Go!), cooldown 900");
    Check(K(K_PIONEER_GO) == 20.0f && K(K_MEDICANT_HELP) == 2.0f && K(VALUE_MAX) == 1000.0f,
          "Pioneer +20 for Go!, Medicant +2 for Help!, values at most 1000");

    SetEnvironmentVariableW(L"RIFTSTONE_INCLINATION_HARNESS", L"1");
    HMODULE plugin = LoadLibraryW(argv[2]);
    printf("plugin (profile %S)\n", profile.c_str());
    Check(plugin != nullptr, "the plugin loads");
    const std::string want = freeze ? "f....." : commands ? "-jjjjj" : "-.....";
    Check(Sites() == want, "patched exactly this mode's sites: " + Sites() + " (want " + want + ")");
    if (freeze || commands) {
        // A second copy of the plugin (no ini: default freeze) must see a patched site and change nothing.
        wchar_t copy[MAX_PATH];
        wcscpy_s(copy, argv[2]);
        wchar_t* dot = wcsrchr(copy, L'.');
        if (dot) *dot = 0;
        wcscat_s(copy, L"_copy.asi");
        if (CopyFileW(argv[2], copy, FALSE)) {
            HMODULE second = LoadLibraryW(copy);
            Check(second != nullptr && Sites() == want, "a second copy refuses the patched code and changes nothing");
        }
    }
    if (g_fails) return 1;

    SetupWorld();
    PlaceCapture();

    printf("after(): the pawn's value + a pending change, the real instruction stream\n");
    struct AddCase {
        const char* what;
        float value, change, vanilla;
    } adds[] = {
        {"a change of +4", 500.0f, 4.0f, 504.0f},
        {"a change of -4", 500.0f, -4.0f, 496.0f},
        {"at the upper limit", 999.0f, 4.0f, 1000.0f},
    };
    for (const AddCase& c : adds) {
        bool regs = false;
        float got = RunAdd(c.value, c.change, regs);
        float expect = freeze ? c.value : c.vanilla;
        Check(got == expect, std::string(c.what) + F(": %.2f -> %.2f", c.value, got) + (freeze ? " (frozen)" : ""));
        Check(regs, "  the loop's registers (cInfo in eax, ebx, esi, edi, ebp, esp) came through");
    }

    printf("calcProtection: Guardian for each order (0x004117F0, the whole function)\n");
    w->state[0xBE2CC] = 1;
    struct OrderCase {
        const char* name;
        uint32_t order;
        uintptr_t k;  // the vanilla Guardian change, 0 = none
    } orders[] = {{"Go!", GO, K_GUARD_GO}, {"Help!", HELP, K_GUARD_HELP}, {"Come!", COME, K_GUARD_COME}, {"no order", NONE, 0}};
    for (const OrderCase& o : orders) {
        ResetSelf();
        SetOrder(o.order);
        CallWithEax(CALC_PROTECTION);
        float vanilla = o.k ? K(o.k) : 0.0f;
        float expect = commands ? 0.0f : vanilla;
        Check(Sum(GUARDIAN) == expect, std::string(o.name) + F(": Guardian change %.2f (vanilla %.2f)", Sum(GUARDIAN), vanilla));
    }
    ResetSelf();
    SetOrder(COME);
    CallWithEax(CALC_PROTECTION);
    bool cooling = w->self[0x124] != 0;
    CallWithEax(CALC_PROTECTION);
    Check(cooling == !commands, commands ? "Come!: no cooldown starts (the order is not seen)" : "Come!: its cooldown starts");
    Check(Sum(GUARDIAN) == (commands ? 0.0f : K(K_GUARD_COME)),
          F("Come! twice within the cooldown: Guardian change %.2f in all", Sum(GUARDIAN)));

    printf("calcCuriosity: Pioneer for each order (0x00411C90, the whole function)\n");
    w->state[0xBE2CC] = 0;
    for (const OrderCase& o : orders) {
        ResetSelf();
        SetOrder(o.order);
        CallWithEax(CALC_CURIOSITY);
        float vanilla = o.order == GO ? K(K_PIONEER_GO) : 0.0f;
        float expect = commands ? 0.0f : vanilla;
        Check(Sum(PIONEER) == expect, std::string(o.name) + F(": Pioneer change %.2f (vanilla %.2f)", Sum(PIONEER), vanilla));
    }

    printf("calcPrudent's order step: Medicant for each order (0x004112A7 to its ret)\n");
    for (const OrderCase& o : orders) {
        ResetSelf();
        SetOrder(o.order);
        RunPrudentOrder();
        float vanilla = o.order == HELP ? K(K_MEDICANT_HELP) : 0.0f;
        float expect = commands ? 0.0f : vanilla;
        Check(Sum(MEDICANT) == expect, std::string(o.name) + F(": Medicant change %.2f (vanilla %.2f)", Sum(MEDICANT), vanilla));
    }

    printf(g_fails ? "\n%d check(s) FAILED\n" : "\nall checks passed\n", g_fails);
    return g_fails ? 1 : 0;
}

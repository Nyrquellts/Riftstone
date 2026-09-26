// sprint_harness_core -- runs free_sprint inside the real game code, without the game.
//
//   sprint_stub.exe <DDDA.exe> <free_sprint.asi> <out_of_battle|always|arisen|off|bogus|tampered>
//
// Maps DDDA.exe's image at its fixed base (0x00400000) over the stub's image, loads the plugin (which
// verifies and patches the mapped code), then runs the game's own uPlayerBase::calcStaminaConsume
// (0x00B81920) on a fake player twice for every case: called directly (the game as shipped) and
// through whatever updateStamina's call at 0x00B8147E now calls (the plugin's thunk when patched).
// The cases walk the sprint actions (5 cPlActDash, 0x7D cPlActDashBegin) and others, in and out of
// battle (sGameSys +0xBE2CC), for the Arisen and for a pawn, and with Assassin's own sprint table.
// For each: the drain the call returns in xmm0, and every other register (eax..ebp, esp, xmm1..xmm7),
// which must come back from the thunk exactly as from the game's function.
// Profiles (run_tests.py writes the matching free_sprint.ini):
//   out_of_battle  the shipped ini: sprinting is free out of battle, for the whole party
//   always         sprinting is always free
//   arisen         out_of_battle, Who = arisen: the pawns pay as in vanilla
//   off            Mode = off: nothing patched, all vanilla
//   bogus          Mode = sometimes: an unknown mode patches nothing
//   tampered       the shipped ini, but one byte of the switch differs: the plugin refuses
// Prints "pass"/"FAIL" lines and exits 0 when everything passed, 1 on a failure, 2 when the image
// cannot be mapped here (reported as a skip by run_tests.py).
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shellapi.h>

#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include <string>
#include <vector>

namespace {
constexpr uintptr_t BASE = 0x00400000;
constexpr uintptr_t CALL_SITE = 0x00B8147E;       // updateStamina: call calcStaminaConsume
constexpr uintptr_t CONSUME = 0x00B81920;         // uPlayerBase::calcStaminaConsume
constexpr uintptr_t GAME_SYS = 0x018FA4BC;        // sGameSys::mpInstance: +0xBE2CC the battle byte
constexpr uintptr_t PLAYER_MANAGER = 0x018FA4EC;  // sPlayerManager::mpInstance: +0x99C the Arisen
constexpr uintptr_t SWITCH_BYTE = 0x00B81A05;     // cmp ecx, 0xE2: the byte the tampered profile changes
constexpr uintptr_t ONE = 0x015319B8;             // the 1.0f the sprint branch multiplies by

// uPlayerBase fields calcStaminaConsume reads (PC offsets).
constexpr uint32_t P_FLAGS = 0x273C, P_ROW = 0x34CC, P_INFO = 0x3DEC, P_JOB = 0x354C, P_STATUS = 0x206D,
                   P_ACTION = 0x2DD4, P_DASH_TABLE = 0x3504, P_DASH_TABLE_ASSASSIN = 0x353C, P_STORE = 0x0EE4;

int g_fails = 0;
void Check(bool ok, const std::string& what) {
    printf("  %s  %s\n", ok ? "pass" : "FAIL", what.c_str());
    if (!ok) g_fails++;
}
std::string F(const char* fmt, double a = 0, double b = 0, double c = 0) {
    char s[240];
    _snprintf_s(s, sizeof s, _TRUNCATE, fmt, a, b, c);
    return s;
}

// ---- the fake world ------------------------------------------------------------------------------
struct Row {        // one row of an rPlStamina table, as the sprint branch reads it
    uint32_t pad;
    float numerator, denominator, recovery;
};
struct Table {      // an rPlStamina resource: +0x70 its rows
    uint8_t pad[0x70];
    Row** rows;
};
struct World {
    alignas(16) uint8_t arisen[0x6000];
    alignas(16) uint8_t pawn[0x6000];
    alignas(16) uint8_t manager[0x1000];
    uint32_t vtable[0x100];
    Row dashRow, assassinRow, otherRow;
    Row* dashRows[1];
    Row* assassinRows[1];
    Row* otherRows[8];   // the other branches index a row by the player's row plus up to 6
    Table dash, assassin, other;
    uint8_t* sys;   // 0xC0000 bytes
};
World* w;

// The player's virtual at vtable +0x2F8 (thiscall, no arguments): no flag 0x20 set.
__declspec(naked) void NoFlags() {
    __asm {
        xor eax, eax
        ret
    }
}

// The player's rPlStamina tables sit at +0x34FC..+0x3544 (walk, run, sprint, the carrying and climbing
// ones, Assassin's own); every slot gets a table, the two sprint slots their own.
constexpr uint32_t P_TABLES_FIRST = 0x34FC, P_TABLES_END = 0x3548;

void SetupPlayer(uint8_t* p) {
    memset(p, 0, 0x6000);
    *(uint32_t*)p = (uint32_t)(uintptr_t)w->vtable;
    for (uint32_t o = P_TABLES_FIRST; o < P_TABLES_END; o += 4) *(uint32_t*)(p + o) = (uint32_t)(uintptr_t)&w->other;
    *(uint32_t*)(p + P_DASH_TABLE) = (uint32_t)(uintptr_t)&w->dash;
    *(uint32_t*)(p + P_DASH_TABLE_ASSASSIN) = (uint32_t)(uintptr_t)&w->assassin;
}

void SetupWorld() {
    w = (World*)VirtualAlloc(nullptr, sizeof(World), MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    w->sys = (uint8_t*)VirtualAlloc(nullptr, 0xC0000, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    for (uint32_t& slot : w->vtable) slot = (uint32_t)(uintptr_t)NoFlags;
    w->dashRow = {0, 12.0f, 60.0f, 30.0f};       // a drain of 12/60 = 0.2 a frame step
    w->assassinRow = {0, 9.0f, 60.0f, 25.0f};    // Assassin's own sprint table: 0.15
    w->otherRow = {0, 6.0f, 60.0f, 20.0f};
    w->dashRows[0] = &w->dashRow;
    w->assassinRows[0] = &w->assassinRow;
    for (Row*& r : w->otherRows) r = &w->otherRow;
    w->dash.rows = w->dashRows;
    w->assassin.rows = w->assassinRows;
    w->other.rows = w->otherRows;
    SetupPlayer(w->arisen);
    SetupPlayer(w->pawn);
    *(uint32_t*)(w->manager + 0x99C) = (uint32_t)(uintptr_t)w->arisen;
    *(uint32_t*)GAME_SYS = (uint32_t)(uintptr_t)w->sys;
    *(uint32_t*)PLAYER_MANAGER = (uint32_t)(uintptr_t)w->manager;
}

// ---- one call, every register seeded before and read after -----------------------------------------
struct Regs {
    uint32_t eax, ebx, ecx, edx, esi, edi, ebp, esp;
    alignas(16) float xmm[8][4];
};
uint32_t g_target, g_player, g_savedEsp, g_callEsp;
alignas(16) float g_seed[8][4];
Regs g_out;

__declspec(naked) void InvokeAsm() {
    __asm {
        pushad
        mov g_savedEsp, esp
        movups xmm0, [g_seed + 0x00]
        movups xmm1, [g_seed + 0x10]
        movups xmm2, [g_seed + 0x20]
        movups xmm3, [g_seed + 0x30]
        movups xmm4, [g_seed + 0x40]
        movups xmm5, [g_seed + 0x50]
        movups xmm6, [g_seed + 0x60]
        movups xmm7, [g_seed + 0x70]
        mov edi, g_player
        mov ebx, 0x11111111
        mov esi, 0x22222222
        mov ebp, 0x33333333
        mov eax, 0x44444444
        mov ecx, 0x55555555
        mov edx, 0x66666666
        mov g_callEsp, esp
        call dword ptr [g_target]
        mov g_out.eax, eax
        mov g_out.ebx, ebx
        mov g_out.ecx, ecx
        mov g_out.edx, edx
        mov g_out.esi, esi
        mov g_out.edi, edi
        mov g_out.ebp, ebp
        mov g_out.esp, esp
        movups [g_out.xmm + 0x00], xmm0
        movups [g_out.xmm + 0x10], xmm1
        movups [g_out.xmm + 0x20], xmm2
        movups [g_out.xmm + 0x30], xmm3
        movups [g_out.xmm + 0x40], xmm4
        movups [g_out.xmm + 0x50], xmm5
        movups [g_out.xmm + 0x60], xmm6
        movups [g_out.xmm + 0x70], xmm7
        mov esp, g_savedEsp
        popad
        ret
    }
}

Regs Invoke(uintptr_t target, uint8_t* player) {
    for (int i = 0; i < 8; i++)
        for (int j = 0; j < 4; j++) g_seed[i][j] = 1000.0f * (float)(i + 1) + (float)j + 0.5f;
    g_target = (uint32_t)target;
    g_player = (uint32_t)(uintptr_t)player;
    memset(&g_out, 0, sizeof g_out);
    InvokeAsm();
    return g_out;
}

uint32_t Bits(float f) {
    uint32_t u;
    memcpy(&u, &f, 4);
    return u;
}

// Everything but xmm0 (the drain) matches.
bool SameOtherwise(const Regs& a, const Regs& b) {
    if (a.eax != b.eax || a.ebx != b.ebx || a.ecx != b.ecx || a.edx != b.edx || a.esi != b.esi || a.edi != b.edi ||
        a.ebp != b.ebp || a.esp != b.esp)
        return false;
    return memcmp(a.xmm[1], b.xmm[1], sizeof(float) * 4 * 7) == 0;
}

uintptr_t SiteTarget() {
    const uint8_t* p = (const uint8_t*)CALL_SITE;
    if (p[0] != 0xE8) return 0;
    int32_t rel;
    memcpy(&rel, p + 1, 4);
    return CALL_SITE + 5 + rel;
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
        return printf("SKIP: run me through sprint_stub.exe (host at %p, 0x%X bytes)\n", (void*)host,
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

struct Case {
    const char* what;
    uint8_t* player;
    uint32_t action, job;
    bool battle;
};
}  // namespace

static int Run(int argc, wchar_t** argv);

// Called by sprint_stub.exe.  Never returns: the stub's code is gone once DDDA.exe is mapped.
extern "C" __declspec(dllexport) void HarnessMain() {
    int argc = 0;
    wchar_t** argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    int code = Run(argc, argv);
    fflush(stdout);
    ExitProcess((UINT)code);
}

static int Run(int argc, wchar_t** argv) {
    setvbuf(stdout, nullptr, _IONBF, 0);  // a fault in the game code must not take the lines before it along
    if (argc < 4) {
        printf("usage: sprint_stub <DDDA.exe> <free_sprint.asi> <out_of_battle|always|arisen|off|bogus|tampered>\n");
        return 1;
    }
    const std::wstring profile = argv[3];
    const bool always = profile == L"always", arisenOnly = profile == L"arisen";
    const bool patched = profile == L"out_of_battle" || always || arisenOnly;
    if (!MapImage(argv[1])) return 2;

    Check(SiteTarget() == CONSUME, "the mapped game code calls calcStaminaConsume (0x00B81920) at 0x00B8147E");
    Check(*(const float*)ONE == 1.0f && *(const uint8_t*)SWITCH_BYTE == 0xE2,
          "the sprint branch's 1.0 and the switch's 0xE2 actions are as shipped");
    if (profile == L"tampered") *(uint8_t*)SWITCH_BYTE = 0xE3;

    SetEnvironmentVariableW(L"RIFTSTONE_FREE_SPRINT_HARNESS", L"1");
    HMODULE plugin = LoadLibraryW(argv[2]);
    printf("plugin (profile %S)\n", profile.c_str());
    Check(plugin != nullptr, "the plugin loads");
    uintptr_t target = SiteTarget();
    uintptr_t lo = (uintptr_t)plugin, hi = lo;
    if (plugin) {
        auto* nt = (const IMAGE_NT_HEADERS32*)(lo + ((const IMAGE_DOS_HEADER*)lo)->e_lfanew);
        hi = lo + nt->OptionalHeader.SizeOfImage;
    }
    if (patched)
        Check(target >= lo && target < hi, "the call at 0x00B8147E now goes into the plugin");
    else
        Check(target == CONSUME, "the call at 0x00B8147E still goes to calcStaminaConsume: nothing patched");
    if (patched) {
        // A second copy of the plugin (no ini: out_of_battle, party) must see a patched site and change nothing.
        wchar_t copy[MAX_PATH];
        wcscpy_s(copy, argv[2]);
        wchar_t* dot = wcsrchr(copy, L'.');
        if (dot) *dot = 0;
        wcscat_s(copy, L"_copy.asi");
        if (CopyFileW(argv[2], copy, FALSE)) {
            HMODULE second = LoadLibraryW(copy);
            Check(second != nullptr && SiteTarget() == target, "a second copy refuses the patched code and changes nothing");
        }
    }
    if (g_fails) return 1;

    SetupWorld();
    const Case cases[] = {
        {"the Arisen sprints (cPlActDash), out of battle", w->arisen, 5, 0, false},
        {"the Arisen starts a sprint (cPlActDashBegin), out of battle", w->arisen, 0x7D, 0, false},
        {"the Arisen sprints, in battle", w->arisen, 5, 0, true},
        {"the Arisen starts a sprint, in battle", w->arisen, 0x7D, 0, true},
        {"an Assassin sprints (its own table), out of battle", w->arisen, 5, 5, false},
        {"an Assassin sprints, in battle", w->arisen, 5, 5, true},
        {"a pawn sprints, out of battle", w->pawn, 5, 0, false},
        {"a pawn sprints, in battle", w->pawn, 5, 0, true},
        {"the Arisen walks (cPlActWalk)", w->arisen, 1, 0, false},
        {"the Arisen runs (cPlActRun)", w->arisen, 2, 0, false},
        {"the Arisen jumps from a sprint (cPlActDashJump)", w->arisen, 0xB, 0, false},
        {"the Arisen climbs a monster (cPlActEnemyClimb)", w->arisen, 0x73, 0, false},
        {"the Arisen carries (cPlActLiftGeneric)", w->arisen, 0x3D, 0, false},
    };
    printf("calcStaminaConsume on a fake player: the game's own call vs. the one at 0x00B8147E\n");
    for (const Case& c : cases) {
        *(uint32_t*)(c.player + P_ACTION) = c.action;
        *(uint32_t*)(c.player + P_JOB) = c.job;
        w->sys[0xBE2CC] = c.battle ? 1 : 0;
        Regs vanilla = Invoke(CONSUME, c.player);
        Regs via = Invoke(target, c.player);
        const bool sprint = c.action == 5 || c.action == 0x7D;
        const bool isArisen = c.player == w->arisen;
        const bool free = patched && sprint && (always || !c.battle) && (!arisenOnly || isArisen);
        float drain = vanilla.xmm[0][0], got = via.xmm[0][0];
        if (sprint) {
            float rate = c.job == 5 ? 9.0f / 60.0f : 12.0f / 60.0f;
            Check(drain == -rate, std::string(c.what) + F(": the game charges %.4f (the table's %.4f)", -drain, rate));
        }
        if (free)
            Check(Bits(got) == 0x80000000u, std::string(c.what) + F(": free (%.4f; the game's own no-drain -0.0)", -got));
        else
            Check(Bits(got) == Bits(drain), std::string(c.what) + F(": charged as in the game (%.4f)", -got));
        Check(SameOtherwise(vanilla, via), "  every other register as the game's function left it (eax..ebp, esp, xmm1..xmm7)");
        Check(vanilla.esp == g_callEsp && vanilla.edi == (uint32_t)(uintptr_t)c.player,
              "  the stack and the player (edi) came through");
        if (sprint)
            Check(*(const float*)(c.player + P_STORE) == (c.job == 5 ? 25.0f : 30.0f),
                  "  the game still stores the sprint row's recovery value at +0xEE4");
    }

    printf("a fault while the plugin looks at the game charges the game's own drain\n");
    if (patched) {
        // The battle byte (+0xBE2CC) on a page that is not there, while the two flags the game's own sprint
        // branch reads (+0xAC720, +0xB8844) stay readable; the Arisen's manager nowhere at all.
        auto* sys = (uint8_t*)VirtualAlloc(nullptr, 0xC0000, MEM_RESERVE, PAGE_NOACCESS);
        bool made = sys && VirtualAlloc(sys, 0xBE000, MEM_COMMIT, PAGE_READWRITE) != nullptr;
        void* hole = VirtualAlloc(nullptr, 0x10000, MEM_RESERVE, PAGE_NOACCESS);
        Check(made && hole, "a game state whose battle byte cannot be read, and a manager that is not there");
        uint32_t keep = *(uint32_t*)GAME_SYS, keepManager = *(uint32_t*)PLAYER_MANAGER;
        *(uint32_t*)GAME_SYS = (uint32_t)(uintptr_t)sys;
        *(uint32_t*)PLAYER_MANAGER = (uint32_t)(uintptr_t)hole;
        *(uint32_t*)(w->arisen + P_ACTION) = 5;
        *(uint32_t*)(w->arisen + P_JOB) = 0;
        Regs vanilla = Invoke(CONSUME, w->arisen);
        Regs via = Invoke(target, w->arisen);
        bool expectFree = always && !arisenOnly;   // always, party: reads nothing of the game, so no fault
        Check(expectFree ? Bits(via.xmm[0][0]) == 0x80000000u : Bits(via.xmm[0][0]) == Bits(vanilla.xmm[0][0]),
              expectFree ? "Mode = always reads nothing of the game's state: still free"
                         : F("the game's state cannot be read: charged as in the game (%.4f)", -via.xmm[0][0]));
        Check(SameOtherwise(vanilla, via), "  every other register as the game's function left it");
        *(uint32_t*)GAME_SYS = keep;
        *(uint32_t*)PLAYER_MANAGER = keepManager;
    } else {
        Check(true, "nothing patched: nothing to fault");
    }

    printf(g_fails ? "\n%d check(s) FAILED\n" : "\nall checks passed\n", g_fails);
    return g_fails ? 1 : 0;
}

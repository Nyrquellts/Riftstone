// pc_harness_core -- runs the portcrystals patches inside the real game code, without the game.
//
//   pc_stub.exe <DDDA.exe> <portcrystals.asi> <expected slots> [tamper | late | reload]
//
// Maps DDDA.exe's image at its fixed base (0x00400000) over the stub's image, which was sized to reserve that range,
// loads the plugin (which verifies and patches the mapped code) and checks, on a fake sGameSys, fake save data and
// a fake map:
//   * the patched values: sGameSys's and the map's sizes, moved references, index bases, the limit compares;
//   * the replaced runs, each entered in the real code and stopped where it ends: the constructor and the clear
//     leave all 32 slots of the moved list empty, the map's constructor all 32 icon pointers; nothing else written,
//     every register kept;
//   * the real count, position get and set, the free-slot search (slot 11 is claimed when the first ten are
//     taken; with every slot taken the placement is refused), the pick-up, the destination list and its position;
//   * the save copy through the plugin's hook: the save gets the first ten, the sidecar the ones past ten; then
//     both load copies: the ten come from the save, the rest from the sidecar record for that save, or empty when
//     there is none;
//   * "reload": a second process, the sidecar read from its file, brings the same crystals back;
//   * "tamper": one site altered: the plugin refuses and patches nothing; "late": sGameSys already exists: refuse.
// Prints "pass"/"FAIL" lines and exits 0 when everything passed, 1 on a failure, 2 when the image cannot be mapped
// here (reported as a skip by run_tests.py).
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
constexpr uint32_t AREAS_OLD = 0xBE378, POS_OLD = 0xBE3A0, GS_SIZE = 0xBE470, AREAS_NEW = 0xBE470, POS_NEW = 0xBE4F0,
                   GS_NEW = 0xBE6F0, MAP_SIZE = 0x960, MAP_NEW = 0x9E0, ICONS_OLD = 0x3AC, ICONS_NEW = 0x960;
constexpr int MAX = 32, VANILLA = 10;
constexpr uintptr_t SGAMESYS = 0x018FA4BC, DEFAULT_VEC = 0x018CFE80;
constexpr uint32_t PLACE_READY = 0x2B3C;          // 0x004545B7: cmp dword ptr [edx + 0x2B3C], 0 (the place data)
constexpr uint32_t SAVE_AREAS = 0xC1F4, SAVE_POS = 0xC220, SAVE_SIZE = 0xC2C0;
constexpr uintptr_t RUN_CONSTRUCT = 0x0043847F, END_CONSTRUCT = 0x004384CF, RUN_CLEAR = 0x0043CA89,
                    END_CLEAR = 0x0043CB6D, COUNT = 0x0044D170, GET_POS = 0x0044D0D0, SET_POS = 0x0044D140,
                    RUN_MAP = 0x00680085, END_MAP = 0x006800C1, PLACE_FROM = 0x00B12C94, PLACE_FOUND = 0x00B12CD7,
                    PLACE_NONE = 0x00B12CC4, PICK_FROM = 0x00B130A9, PICK_END = 0x00B130FE, DEST_SLOT = 0x0068FF00,
                    DEST_POS = 0x0068FF50, SAVE_FROM = 0x00493D7D, SAVE_END = 0x00493E01, LOAD_FROM = 0x00494BAB,
                    LOAD_END = 0x00494BFF, LOAD2_FROM = 0x00494DFD, LOAD2_END = 0x00494E4F;
constexpr uint32_t S_ESI = 0x11111111, S_EDI = 0x22222222, S_EBP = 0x44444444;

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

// ---- entering game code and stopping at one of two addresses -----------------------------------------------------
jmp_buf g_back;
uint32_t g_jumpTo, g_setEdi, g_setEsi, g_setEcx, g_setEbx, g_setEbp, g_setEdx, g_setEax;
uint32_t g_trapEdi, g_trapEsi, g_trapEcx, g_trapEbx, g_trapEbp, g_trapEax, g_trapEdx;
int g_stop;

void TrapC() { longjmp(g_back, 1); }
#define TRAP(name, n) __declspec(naked) void name() { __asm { \
    __asm mov g_trapEdi, edi __asm mov g_trapEsi, esi __asm mov g_trapEcx, ecx __asm mov g_trapEbx, ebx \
    __asm mov g_trapEbp, ebp __asm mov g_trapEax, eax __asm mov g_trapEdx, edx __asm mov g_stop, n __asm jmp TrapC } }
TRAP(Trap1, 1)
TRAP(Trap2, 2)
__declspec(naked) void JumpIn() {
    __asm {
        sub esp, 1024
        mov edi, g_setEdi
        mov esi, g_setEsi
        mov ecx, g_setEcx
        mov ebx, g_setEbx
        mov edx, g_setEdx
        mov eax, g_setEax
        mov ebp, g_setEbp
        jmp dword ptr [g_jumpTo]
    }
}
void Arm(uintptr_t at, void (*to)(), uint8_t saved[5]) {
    DWORD old;
    VirtualProtect((void*)at, 5, PAGE_EXECUTE_READWRITE, &old);
    memcpy(saved, (void*)at, 5);
    *(uint8_t*)at = 0xE9;
    *(int32_t*)(at + 1) = (int32_t)((uintptr_t)to - (at + 5));
}
int RunTo(uintptr_t from, uintptr_t stop, uintptr_t stop2 = 0) {
    uint8_t s1[5], s2[5];
    Arm(stop, Trap1, s1);
    if (stop2) Arm(stop2, Trap2, s2);
    g_jumpTo = (uint32_t)from;
    g_stop = 0;
    if (setjmp(g_back) == 0) JumpIn();
    memcpy((void*)stop, s1, 5);
    if (stop2) memcpy((void*)stop2, s2, 5);
    return g_stop;
}
void Regs(uint32_t eax, uint32_t ecx, uint32_t edx, uint32_t ebx, uint32_t esi, uint32_t edi, uint32_t ebp) {
    g_setEax = eax, g_setEcx = ecx, g_setEdx = edx, g_setEbx = ebx, g_setEsi = esi, g_setEdi = edi, g_setEbp = ebp;
}

// ---- calling game code with registers (and one stack argument) ---------------------------------------------------
uint32_t g_callTarget, g_inEax, g_inEcx, g_inEdx, g_inEsi, g_inEdi, g_arg, g_hasArg, g_outEax;
__declspec(naked) void CallRegs() {
    __asm {
        push ebx
        push esi
        push edi
        push ebp
        mov eax, g_inEax
        mov ecx, g_inEcx
        mov edx, g_inEdx
        mov esi, g_inEsi
        mov edi, g_inEdi
        cmp g_hasArg, 0
        je nopush
        push g_arg
    nopush:
        call dword ptr [g_callTarget]
        mov g_outEax, eax
        pop ebp
        pop edi
        pop esi
        pop ebx
        ret
    }
}
uint32_t Call(uintptr_t fn, uint32_t eax, uint32_t ecx, uint32_t edx = 0, uint32_t esi = 0, uint32_t edi = 0,
              bool hasArg = false, uint32_t arg = 0) {
    g_callTarget = (uint32_t)fn, g_inEax = eax, g_inEcx = ecx, g_inEdx = edx, g_inEsi = esi, g_inEdi = edi;
    g_hasArg = hasArg, g_arg = arg;
    CallRegs();
    return g_outEax;
}

// ---- the fakes ------------------------------------------------------------------------------------------------------
struct Mem {
    uint8_t* p;
    uint32_t bytes;
    Mem(uint32_t n, uint8_t fill) : bytes(n) {
        p = (uint8_t*)VirtualAlloc(nullptr, n + 0x1000, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
        memset(p, fill, n + 0x1000);
    }
    ~Mem() { VirtualFree(p, 0, MEM_RELEASE); }
    uint32_t addr() const { return (uint32_t)(uintptr_t)p; }
    uintptr_t at(uint32_t off) const { return (uintptr_t)p + off; }
    bool same(uint32_t from, uint32_t to, uint8_t v) const {
        for (uint32_t k = from; k < to; k++)
            if (p[k] != v) return false;
        return true;
    }
};
struct GameSys : Mem {
    explicit GameSys(uint8_t fill = 0xCC) : Mem(GS_NEW, fill) {}
    uintptr_t area(int i) const { return at(AREAS_NEW + 4 * i); }
    uintptr_t pos(int i) const { return at(POS_NEW + 16 * i); }
    void place(int i, uint32_t stage, float x, float y, float z) {
        Put(area(i), stage);
        float v[4] = {x, y, z, 0};
        memcpy((void*)pos(i), v, 16);
    }
    void empty() {
        memset(p + AREAS_NEW, 0, 4 * MAX);
        memset(p + POS_NEW, 0, 16 * MAX);
    }
};
bool PosIs(uintptr_t at, float x, float y, float z) {
    float v[3];
    memcpy(v, (void*)at, 12);
    return v[0] == x && v[1] == y && v[2] == z;
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
        return printf("SKIP: run me through pc_stub.exe (host at %p, 0x%X bytes)\n", (void*)host,
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
    *(uint32_t*)SGAMESYS = 0;           // the game has not built sGameSys yet
    return true;
}
}  // namespace

static int Run(int argc, wchar_t** argv);

// Called by pc_stub.exe.  Never returns: the stub's code is gone once DDDA.exe is mapped.
extern "C" __declspec(dllexport) void HarnessMain() {
    int argc = 0;
    wchar_t** argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    int code = Run(argc, argv);
    fflush(stdout);
    ExitProcess((UINT)code);
}

// The ten slots a save holds (and the ones past ten a sidecar record keeps for it): stage 100, distinct positions.
static void FillTen(GameSys& g, float base) {
    for (int i = 0; i < VANILLA; i++) g.place(i, 100, base + i, 3000.0f + i, -base - i);
}

static int Run(int argc, wchar_t** argv) {
    if (argc < 4) {
        printf("usage: pc_stub <DDDA.exe> <portcrystals.asi> <expected slots> [tamper|late|reload]\n");
        return 1;
    }
    if (!MapImage(argv[1])) return 2;
    const int want = _wtoi(argv[3]);
    const wchar_t* mode = argc > 4 ? argv[4] : L"";
    const bool tamper = wcscmp(mode, L"tamper") == 0, late = wcscmp(mode, L"late") == 0,
               reload = wcscmp(mode, L"reload") == 0;
    if (late) *(uint32_t*)SGAMESYS = 0x12345678;    // the game built sGameSys before the plugin came
    if (tamper) *(uint8_t*)(0x00B12CAF + 2) ^= 0x01;  // the limit compare is not the expected code

    SetEnvironmentVariableW(L"RIFTSTONE_PORTCRYSTALS_HARNESS", L"1");
    HMODULE plugin = LoadLibraryW(argv[2]);
    auto slotsFn = plugin ? (int (*)())GetProcAddress(plugin, "Portcrystals_Slots") : nullptr;
    auto recordsFn = plugin ? (int (*)())GetProcAddress(plugin, "Portcrystals_Records") : nullptr;
    const int n = slotsFn ? slotsFn() : 0;
    printf("plugin (%d slots expected)\n", want);
    Check(plugin && slotsFn && recordsFn, "the plugin loads");
    if (tamper || late) {
        Check(n == 0, tamper ? "one altered instruction: the plugin refuses" : "sGameSys already exists: the plugin refuses");
        Check(U32(0x0041C04E + 1) == GS_SIZE && *(uint8_t*)RUN_CONSTRUCT == 0xF3 && *(uint8_t*)COUNT == 0x32 &&
              U32(0x00B12C9F + 2) == AREAS_OLD, "  and nothing is patched");
        printf(g_fails ? "\n%d check(s) FAILED\n" : "\nall checks passed\n", g_fails);
        return g_fails ? 1 : 0;
    }
    Check(n == want, F("%d Portcrystals placed at once", n));
    if (g_fails) return 1;

    const std::string tag = F("slot %d", n);
    if (reload) {
        printf("reload (a new process: the sidecar read from its file)\n");
        Check(recordsFn() >= 1, F("the sidecar's records are read at start (%d)", recordsFn()));
        GameSys g;
        FillTen(g, 500.0f);
        Mem save(SAVE_SIZE + 0x100, 0x33);
        GameSys gs;                               // the save data the game read: the same ten as the first run
        (void)gs;
        for (int i = 0; i < VANILLA; i++) {
            Put(save.at(SAVE_AREAS + 4 * i), U32(g.area(i)));
            memcpy((void*)save.at(SAVE_POS + 16 * i), (void*)g.pos(i), 12);
        }
        GameSys after(0x77);
        Regs(0, 0x12345678, 0, 0, save.addr(), after.addr(), S_EBP);
        RunTo(LOAD_FROM, LOAD_END);
        bool back = true;
        for (int i = VANILLA; i < n; i++) back &= U32(after.area(i)) == 200u + i && PosIs(after.pos(i), 7000.0f + i, 9.0f, 70.0f);
        Check(back, F("the crystals past ten (slots 11-%d) come back from the file", n));
        printf(g_fails ? "\n%d check(s) FAILED\n" : "\nall checks passed\n", g_fails);
        return g_fails ? 1 : 0;
    }

    printf("patched values\n");
    Check(U32(0x0041C04E + 1) == GS_NEW && U32(0x00437B1B + 1) == GS_NEW && U32(0x0132BA76 + 1) == GS_NEW,
          F("sGameSys is allocated 0x%X bytes (was 0x%X)", GS_NEW, GS_SIZE));
    Check(U32(0x0067FC8B + 1) == MAP_NEW && U32(0x0067FCBB + 1) == MAP_NEW && U32(0x0133AFE6 + 1) == MAP_NEW,
          F("the map is allocated 0x%X bytes (was 0x%X)", MAP_NEW, MAP_SIZE));
    Check(U32(0x00B12C9F + 2) == AREAS_NEW && U32(0x00B12CCE + 3) == AREAS_NEW && U32(0x00503029 + 4) == AREAS_NEW,
          "placing and the stage load read the areas at the new home");
    Check(U32(0x00503024 + 1) == POS_NEW && U32(0x00501A5A + 4) == POS_NEW && U32(0x0044D10E + 2) == POS_NEW / 16,
          "the positions too (offsets and index bases)");
    Check(*(uint8_t*)(0x00B12CAF + 2) == n && U32(0x00B12CB4 + 1) == (uint32_t)n && *(uint8_t*)(0x005031B8 + 1) == n &&
          *(uint8_t*)(0x0066CDB7 + 1) == n, "the limit compares are N");
    Check(U32(0x00493DFC) == 0x720AFA83u && *(uint8_t*)(0x00494BFA + 2) == 10, "the save's own ten-slot loops keep 10");
    Check(U32(0x0068FB2F + 2) == ICONS_NEW && U32(0x0068FDB9 + 2) == ICONS_NEW, "the map's icons at its tail");
    Check(*(uint8_t*)RUN_CONSTRUCT == 0xE9 && *(uint8_t*)RUN_CLEAR == 0xE9 && *(uint8_t*)COUNT == 0xE9 &&
          *(uint8_t*)RUN_MAP == 0xE9 && *(uint8_t*)SAVE_FROM == 0xE9 && *(uint8_t*)LOAD_FROM == 0xE9 &&
          *(uint8_t*)LOAD2_FROM == 0xE9, "the four runs and the three hooks jump to the plugin");

    printf("constructor (the real code from its run to where it publishes sGameSys)\n");
    {
        GameSys g(0xCC);
        Regs(0, 0x12345678, 0, 0, S_ESI, S_EDI, g.addr());
        RunTo(RUN_CONSTRUCT, END_CONSTRUCT);
        Check(g.same(AREAS_NEW, GS_NEW, 0), "all 32 slots of the moved list are empty (area 0, position 0)");
        bool w = true;
        for (int i = 0; i < VANILLA; i++) w &= U32(g.at(POS_OLD + 12 + 16 * i)) == 0;
        Check(w, "  the old list's ten w are 0, as the vanilla run wrote them");
        Check(g.same(0xBE440, AREAS_NEW, 0xCC) && g.same(GS_NEW, GS_NEW + 0x40, 0xCC), "  nothing else is written");
        Check(g_trapEbp == g.addr() && g_trapEsi == S_ESI && g_trapEdi == S_EDI && g_trapEcx == 0x12345678,
              "  every register survives");
    }
    printf("clear (the real code from its run on)\n");
    {
        GameSys g(0x55);
        Regs(0, 0x12345678, 0, g.addr(), S_ESI, 0, S_EBP);
        RunTo(RUN_CLEAR, END_CLEAR);
        Check(g.same(AREAS_NEW, GS_NEW, 0), "all 32 slots of the moved list are empty");
        Check(g.same(AREAS_OLD + 24, POS_OLD, 0x55) && g.same(0xBE440, AREAS_NEW, 0x55) && g.same(GS_NEW, GS_NEW + 0x40, 0x55),
              "  nothing else is written (the old list's slots 6-9 are the vanilla code's, after the run)");
        Check(g_trapEbx == g.addr() && g_trapEsi == S_ESI && g_trapEdi == 0 && g_trapEcx == 0x12345678 && g_trapEbp == S_EBP,
              "  every register survives");
    }
    printf("the count, the position get and set (the real functions)\n");
    {
        GameSys g;
        g.empty();
        g.place(0, 100, 1, 2, 3);
        g.place(5, 100, 4, 5, 6);
        g.place(n - 1, 100, 7, 8, 9);
        uint32_t r = Call(COUNT, 0xABCDEF00, g.addr());
        Check((r & 0xFF) == 3 && (r >> 8) == 0xABCDEF, F("three placed of %d, counted up to slot %d (eax's high bytes kept)", n, n));
        float out[4] = {-1, -1, -1, -1};
        Call(GET_POS, (uint32_t)(uintptr_t)out, (uint32_t)(n - 1), 0, 0, 0, true, g.addr());
        Check(out[0] == 7 && out[1] == 8 && out[2] == 9, F("the position of slot %d", n));
        Call(GET_POS, (uint32_t)(uintptr_t)out, (uint32_t)n, 0, 0, 0, true, g.addr());
        Check(memcmp(out, (void*)DEFAULT_VEC, 16) == 0, F("slot %d is past the list: the game's default position", n + 1));
        float in[3] = {11, 12, 13};
        Call(SET_POS, (uint32_t)(n - 2), (uint32_t)(uintptr_t)in, 0, 0, 0, true, g.addr());
        Check(PosIs(g.pos(n - 2), 11, 12, 13) && U32(g.pos(n - 2) + 12) == 0, F("setting slot %d's position", n - 1));
        GameSys h(0x5A);
        Call(SET_POS, (uint32_t)n, (uint32_t)(uintptr_t)in, 0, 0, 0, true, h.addr());
        Check(h.same(AREAS_NEW, GS_NEW + 0x40, 0x5A), F("  and nothing for slot %d, past the list", n + 1));
    }
    printf("placing (the real free-slot search)\n");
    {
        GameSys g;
        g.empty();
        Put(g.at(0x34), 100);                    // the current stage
        Mem action(0x200, 0);
        *(uint32_t*)SGAMESYS = g.addr();
        for (int i = 0; i < VANILLA; i++) Put(g.area(i), 100);
        int stop = (Regs(0, 0, 0, 1, action.addr(), 0, S_EBP), RunTo(PLACE_FROM, PLACE_FOUND, PLACE_NONE));
        if (n > VANILLA)
            Check(stop == 1 && U32(action.at(0x94)) == 10 && U32(g.area(10)) == 100,
                  "the first ten taken: the eleventh crystal takes slot 11, in this stage");
        else
            Check(stop == 2 && U32(action.at(0x94)) == 10, "slots = 10 and the ten taken: the eleventh is refused, as in "
                                                            "the game");
        for (int i = 0; i < n - 1; i++) Put(g.area(i), 100);
        Put(g.area(n - 1), 0);
        stop = (Regs(0, 0, 0, 1, action.addr(), 0, S_EBP), RunTo(PLACE_FROM, PLACE_FOUND, PLACE_NONE));
        Check(stop == 1 && U32(action.at(0x94)) == (uint32_t)(n - 1) && U32(g.area(n - 1)) == 100, F("the last free slot, %d", n));
        stop = (Regs(0, 0, 0, 1, action.addr(), 0, S_EBP), RunTo(PLACE_FROM, PLACE_FOUND, PLACE_NONE));
        Check(stop == 2 && U32(action.at(0x94)) == (uint32_t)n, F("every one of the %d taken: the placement is refused", n));
        *(uint32_t*)SGAMESYS = 0;
    }
    printf("picking up (the real code)\n");
    {
        GameSys g;
        g.empty();
        g.place(n - 1, 100, 7, 8, 9);
        std::vector<uint8_t> crystal(0x2A00, 0);
        crystal[0x29A5] = (uint8_t)(n - 1);
        Mem action(0x200, 0);
        Put(action.at(0x74), (uint32_t)(uintptr_t)crystal.data());
        *(uint32_t*)SGAMESYS = g.addr();
        Regs(0, 0, 0, 1, action.addr(), 0, 0);
        RunTo(PICK_FROM, PICK_END);
        Check(U32(g.area(n - 1)) == 0 && memcmp((void*)g.pos(n - 1), (void*)DEFAULT_VEC, 12) == 0 && U32(g.pos(n - 1) + 12) == 0,
              F("the crystal in slot %d is picked up: its slot is free", n));
        *(uint32_t*)SGAMESYS = 0;
    }
    printf("the Ferrystone's destinations (the real list mapping and position)\n");
    {
        GameSys g;
        g.empty();
        for (int i = 0; i < n; i++) g.place(i, 100, 100.0f * i, 1, 2);
        Mem ui(0x900, 0);
        Put(ui.at(0x8AC), 3);                    // three fixed destinations first
        *(uint32_t*)SGAMESYS = g.addr();
        Check((Call(DEST_SLOT, 3 + (n - 1), ui.addr()) & 0xFF) == (uint32_t)(n - 1),
              F("list entry %d is slot %d", 3 + n, n));
        g.empty();
        g.place(2, 100, 1, 1, 1);
        g.place(n - 1, 100, 42, 43, 44);
        Check((Call(DEST_SLOT, 3 + 1, ui.addr()) & 0xFF) == (uint32_t)(n - 1), "  the second placed crystal, past the gaps");
        float out[4] = {0, 0, 0, 0};
        Call(DEST_POS, 3 + 1, 0, 0, (uint32_t)(uintptr_t)out, ui.addr());
        Check(out[0] == 42 && out[1] == 43 && out[2] == 44, "  and its position is the Ferrystone's destination");
        *(uint32_t*)SGAMESYS = 0;
    }
    printf("saving and loading (the real copies, through the plugin's hooks)\n");
    {
        const int before = recordsFn();
        GameSys g;
        g.empty();
        FillTen(g, 500.0f);
        for (int i = VANILLA; i < n; i++) g.place(i, 200 + i, 7000.0f + i, 9, 70);
        Mem save(SAVE_SIZE + 0x100, 0x33);
        Regs(0, 0, 0, 0, g.addr(), save.addr(), S_EBP);
        RunTo(SAVE_FROM, SAVE_END);
        bool ten = true;
        for (int i = 0; i < VANILLA; i++)
            ten &= U32(save.at(SAVE_AREAS + 4 * i)) == 100 && PosIs(save.at(SAVE_POS + 16 * i), 500.0f + i, 3000.0f + i, -500.0f - i);
        Check(ten, "the save gets the first ten, as the game writes them");
        Check(save.same(SAVE_SIZE, SAVE_SIZE + 0x40, 0x33), "  and nothing past them");
        Check(n == VANILLA ? recordsFn() == before : recordsFn() == before + 1,
              n == VANILLA ? "slots = 10: nothing to keep" : F("the crystals past ten (slots 11-%d) are kept in the sidecar", n));

        GameSys after(0x77);
        Regs(0, 0x12345678, 0, 0, save.addr(), after.addr(), S_EBP);
        RunTo(LOAD_FROM, LOAD_END);
        bool back = true;
        for (int i = 0; i < VANILLA; i++) back &= U32(after.area(i)) == 100 && PosIs(after.pos(i), 500.0f + i, 3000.0f + i, -500.0f - i);
        Check(back, "loading that save: the ten come from the save");
        back = true;
        for (int i = VANILLA; i < n; i++) back &= U32(after.area(i)) == 200u + i && PosIs(after.pos(i), 7000.0f + i, 9, 70);
        Check(back, F("  and slots 11-%d from the sidecar", n));
        Check(after.same(POS_NEW + 16 * n, GS_NEW + 0x40, 0x77) && after.same(AREAS_NEW + 4 * n, POS_NEW, 0x77),
              "  nothing past slot N is written");

        GameSys second(0x66);
        Regs(0, 0x12345678, 0, save.addr(), second.addr(), 0, S_EBP);
        RunTo(LOAD2_FROM, LOAD2_END);
        back = true;
        for (int i = 0; i < VANILLA; i++) back &= U32(second.area(i)) == 100;
        for (int i = VANILLA; i < n; i++) back &= U32(second.area(i)) == 200u + i;
        Check(back, "the second load copy brings back the same");

        Put(save.at(SAVE_AREAS), 0);                // another save: its ten differ, so there is no record
        GameSys other(0x77);
        Regs(0, 0x12345678, 0, 0, save.addr(), other.addr(), S_EBP);
        RunTo(LOAD_FROM, LOAD_END);
        bool emptyPast = true;
        for (int i = VANILLA; i < n; i++) emptyPast &= U32(other.area(i)) == 0 && PosIs(other.pos(i), 0, 0, 0);
        Check(emptyPast, F("a save the sidecar has no record for: slots 11-%d are empty", n));
    }
    printf("place names (the real 0x004541B0, which the Ferrystone's list and the map ask for each crystal)\n");
    {
        // run_tests.py names the crystal at (1539, 3911.5, 1593) with message 277 in [names]
        Check(*(uint8_t*)0x004541B0 == 0xE9, "the place-name function goes through the plugin");
        Mem manager(0x3000, 0);
        float named[4] = {1539.0f, 3911.5f, 1593.0f, 0}, other[4] = {1539.0f, 3911.5f, 1594.0f, 0};
        // the manager's place data not loaded: the game names nothing (-1), and the named crystal follows it
        uint32_t a0 = Call(0x004541B0, 0, (uint32_t)(uintptr_t)named, manager.addr());
        uint32_t b0 = Call(0x004541B0, 0, (uint32_t)(uintptr_t)other, manager.addr());
        Check(a0 == 0xFFFFFFFF && b0 == 0xFFFFFFFF,
              F("  no place data (+0x%X = 0): no name for either, as the game (%d, %d)", PLACE_READY, (int)a0, (int)b0));
        Put(manager.addr() + PLACE_READY, 0x1234);
        uint32_t a = Call(0x004541B0, 0, (uint32_t)(uintptr_t)named, manager.addr());
        uint32_t b = Call(0x004541B0, 0, (uint32_t)(uintptr_t)other, manager.addr());
        uint32_t c = Call(0x004541B0, 0, (uint32_t)(uintptr_t)other, manager.addr());
        Check(a == 277, F("the named crystal's position: message 277 (got %u)", a));
        Check(b != 277 && b < 277 && b == c, F("  any other position: the game's own answer, a place of its list (%u)", b));
    }
    printf("the map (the real constructor's icon run)\n");
    {
        Mem map(MAP_NEW, 0xCC);
        Regs(0, 0x12345678, 0, 0, map.addr(), S_EDI, S_EBP);
        RunTo(RUN_MAP, END_MAP);
        Check(map.same(ICONS_NEW, ICONS_NEW + 4 * MAX, 0) && map.same(ICONS_OLD, ICONS_OLD + 40, 0),
              "all 32 icon pointers at the map's tail are empty (and the old ten)");
        Check(map.same(ICONS_OLD + 40, ICONS_OLD + 44, 0xCC) && map.same(MAP_NEW, MAP_NEW + 0x40, 0xCC),
              "  nothing else is written");
        Check(g_trapEsi == map.addr() && g_trapEdi == S_EDI && g_trapEcx == 0x12345678 && g_trapEbp == S_EBP,
              "  every register survives");
    }
    // Leave a record for the "reload" run in a new process: the ten of FillTen(500), slots past ten as above.
    if (n > VANILLA) {
        GameSys g;
        g.empty();
        FillTen(g, 500.0f);
        for (int i = VANILLA; i < n; i++) g.place(i, 200 + i, 7000.0f + i, 9, 70);
        Mem save(SAVE_SIZE + 0x100, 0x33);
        Regs(0, 0, 0, 0, g.addr(), save.addr(), S_EBP);
        RunTo(SAVE_FROM, SAVE_END);
    }
    printf(g_fails ? "\n%d check(s) FAILED\n" : "\nall checks passed\n", g_fails);
    return g_fails ? 1 : 0;
}

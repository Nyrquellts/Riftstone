// skins_harness_core -- runs the enemy_skins hooks inside the real game code, without the game.
//
//   skins_stub.exe <DDDA.exe> <enemy_skins.asi>      (the stub loads this DLL and calls HarnessMain)
//
// Maps DDDA.exe's image at its fixed base (0x00400000) over the stub's image, which was sized to
// reserve that range, points the game's resource
// manager global at a fake one, loads the plugin (which verifies and patches the mapped code), then:
//   * enters each of the three material sites with a fake chimera / goat / snake and a fake
//     rResourceManager::get; the fake records the material path the game asks for, checks that
//     every register the game relies on survived the thunk, and jumps back here;
//   * calls each of the three damage-texture helpers as the game does (eax = variant, the unit on
//     the stack) with one fake material per table entry; the real helper walks the table the
//     thunk chose and asks the fake manager for every texture, which records them.
// Prints "pass"/"FAIL" lines and exits 0 when everything passed, 1 on a failure, 2 when the image
// cannot be mapped here (reported as a skip by run_tests.py).  Nothing is written anywhere but the
// log the plugin keeps next to this exe.
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
constexpr uintptr_t RES_MANAGER_GLOBAL = 0x018D0AA0;
constexpr uintptr_t RMATERIAL_DTI = 0x018D437C, RTEXTURE_DTI = 0x018D186C;
constexpr uintptr_t VT_BODY = 0x015C4E28, VT_GOAT = 0x015C5FD0, VT_SNAKE = 0x015C6C68;
constexpr uintptr_t SITE_BODY = 0x009A1428, SITE_GOAT = 0x009B17DD, SITE_SNAKE = 0x009B96CD;
constexpr uintptr_t HELPER_BODY = 0x009A1540, HELPER_GOAT = 0x009B19C0, HELPER_SNAKE = 0x009B99B0;
constexpr uintptr_t TABLE_BODY = 0x016FF548, TABLE_GOAT = 0x016F9C08, TABLE_SNAKE = 0x016F3D08;
constexpr uint32_t PARENT_GOAT = 0x7278, PARENT_SNAKE = 0x72C0, FLAG = 0x20BC, VALUE = 0x20C0;
constexpr uint32_t SENTINEL_ESI = 0x11111111, SENTINEL_EDI = 0x22222222, SENTINEL_EBX = 0x33333333;

int g_fails = 0;
void Check(bool ok, const std::string& what) {
    printf("  %s  %s\n", ok ? "pass" : "FAIL", what.c_str());
    if (!ok) g_fails++;
}

// ---- the fake resource manager ---------------------------------------------------------------
struct FakeManager { void** vtable; } g_manager;
void* g_managerVtable[64];

jmp_buf g_back;
bool g_jumpBack = false;
// Plain buffers: the fake may longjmp out of game code, so nothing here needs unwinding.
char g_rec[256][96];
int g_nrec = 0;
uint32_t g_lastDti, g_lastFlag, g_capEbx, g_capEbp, g_capEsi, g_capEdi, g_capEcx;

void* __fastcall FakeGetC(void* self, void*, uint32_t dti, const char* path, uint32_t flag) {
    g_lastDti = dti;
    g_lastFlag = flag;
    g_capEcx = (uint32_t)(uintptr_t)self;
    if (g_nrec < 256) strncpy_s(g_rec[g_nrec++], sizeof g_rec[0], path ? path : "(null)", _TRUNCATE);
    if (g_jumpBack) longjmp(g_back, 1);
    return nullptr;  // "not loaded": the game skips binding and moves on
}

std::vector<std::string> Recorded() {
    std::vector<std::string> out;
    for (int i = 0; i < g_nrec; i++) out.push_back(g_rec[i]);
    return out;
}

// rResourceManager::get is a thiscall with three stack arguments; __fastcall takes (ecx, edx) in
// registers and the rest from the stack, popping them itself, which is the same contract.
__declspec(naked) void FakeGet() {
    __asm {
        mov g_capEbx, ebx
        mov g_capEbp, ebp
        mov g_capEsi, esi
        mov g_capEdi, edi
        jmp FakeGetC
    }
}

// ---- entering a material site ------------------------------------------------------------------
uint32_t g_site, g_unit, g_unitInEbp, g_fakeGetAddr, g_managerAddr;

// Recreates the state the game has at the site: [esp] = 1 (the load flag), ecx = the manager, the
// getter in edx (body) and eax (parts), the unit in ebx or ebp; esi/edi/ebx carry sentinels.
__declspec(naked) void JumpToSite() {
    __asm {
        sub esp, 128
        push 1
        mov ecx, g_managerAddr
        mov edx, g_fakeGetAddr
        mov eax, edx
        mov ebx, g_unit
        mov esi, 0x11111111
        mov edi, 0x22222222
        cmp g_unitInEbp, 0
        je go
        mov ebp, ebx
        mov ebx, 0x33333333
    go:
        jmp dword ptr [g_site]
    }
}

void EnterRaw() {
    g_jumpBack = true;
    if (setjmp(g_back) == 0) JumpToSite();
    g_jumpBack = false;
}

std::string EnterSite(uintptr_t site, const uint8_t* unit, bool inEbp) {
    g_site = (uint32_t)site;
    g_unit = (uint32_t)(uintptr_t)unit;
    g_unitInEbp = inEbp ? 1 : 0;
    g_nrec = 0;
    EnterRaw();
    return g_nrec ? std::string(g_rec[g_nrec - 1]) : std::string();
}

// ---- calling a damage helper --------------------------------------------------------------------
uint32_t g_helper, g_variant, g_helperUnit;
__declspec(naked) void CallHelperAsm() {
    __asm {
        push ebx
        push esi
        push edi
        push ebp
        mov eax, g_variant
        push g_helperUnit
        call dword ptr [g_helper]
        pop ebp
        pop edi
        pop esi
        pop ebx
        ret
    }
}

// ---- fake units ------------------------------------------------------------------------------
struct Unit {
    std::vector<uint8_t> mem;
    std::vector<std::vector<uint8_t>> materials;
    std::vector<uint32_t> list;
    explicit Unit(uintptr_t vtable) : mem(0x8000, 0) { *(uint32_t*)mem.data() = (uint32_t)vtable; }
    uint8_t* p() { return mem.data(); }
    void mark(uint32_t skin) { mem[FLAG] = 0; *(uint32_t*)(p() + VALUE) = 0x534B0000u | skin; }
    void parent(uint32_t off, Unit& u) { *(uint32_t*)(p() + off) = (uint32_t)(uintptr_t)u.p(); }
    // One material per distinct index in the helper's table (variant block).
    void materialsFor(uintptr_t table, int entries, int words) {
        const int32_t* w = (const int32_t*)table;
        std::vector<int32_t> seen;
        for (int e = 0; e < entries; e++) {
            int32_t idx = w[e * words];
            if (idx < 0 || idx > 255) continue;  // the helper skips such entries
            bool dup = false;
            for (int32_t s : seen) dup |= s == idx;
            if (dup) continue;
            seen.push_back(idx);
            materials.emplace_back(size_t(0x40), uint8_t(0));
            *(uint32_t*)(materials.back().data() + 0x18) = (uint32_t)(idx & 0xFF) << 23;
        }
        for (auto& m : materials) list.push_back((uint32_t)(uintptr_t)m.data());
        *(uint32_t*)(p() + 0xF8) = (uint32_t)(uintptr_t)list.data();
        *(uint32_t*)(p() + 0xFC) = (uint32_t)list.size();
    }
};

std::vector<std::string> RunHelper(uintptr_t helper, uint32_t variant, Unit& unit) {
    g_helper = (uint32_t)helper;
    g_variant = variant;
    g_helperUnit = (uint32_t)(uintptr_t)unit.p();
    g_nrec = 0;
    g_jumpBack = false;
    CallHelperAsm();
    return Recorded();
}

// Paths the vanilla table would request, in the helper's order: per entry, per matching material
// (one here), textures 1, 2, 4, 3 for the body (it reads +4, +8, +0x10, +0xC), 1..3 for the parts.
std::vector<std::string> Expected(uintptr_t table, int entries, int words, const char* skin) {
    static const char* const BM[4] = {"e5200_skin_BM", "e5200_face_BM", "e5200_hebi_BM", "e5200_eye_BM"};
    std::vector<std::string> out;
    const uint32_t* w = (const uint32_t*)table;
    int order5[4] = {1, 2, 4, 3}, order4[3] = {1, 2, 3};
    for (int e = 0; e < entries; e++) {
        if ((int32_t)w[e * words] < 0 || w[e * words] > 255) continue;
        const int* order = words == 5 ? order5 : order4;
        int n = words == 5 ? 4 : 3;
        for (int k = 0; k < n; k++) {
            uint32_t pth = w[e * words + order[k]];
            if (!pth) continue;
            std::string s = (const char*)pth;
            std::string base = s.substr(s.rfind('\\') + 1);
            bool skinned = false;
            for (auto* b : BM) skinned |= base == b;
            if (skin && skinned) s = s.substr(0, s.rfind('\\') + 1) + skin + "\\" + base;
            out.push_back(s);
        }
    }
    return out;
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
    // The host exe (skins_stub) is based at 0x00400000 and sized to hold DDDA.exe's image.
    auto* host = (const uint8_t*)GetModuleHandleW(nullptr);
    auto* hostNt = (const IMAGE_NT_HEADERS32*)(host + ((const IMAGE_DOS_HEADER*)host)->e_lfanew);
    DWORD need = nt->OptionalHeader.SizeOfImage;
    if ((uintptr_t)host != BASE || hostNt->OptionalHeader.SizeOfImage < need)
        return printf("SKIP: run me through skins_stub.exe (host at %p, 0x%X bytes)\n", (void*)host,
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
    return true;
}
}  // namespace

static int Run(int argc, wchar_t** argv);

// Called by skins_stub.exe.  Never returns: the stub's code is gone once DDDA.exe is mapped.
extern "C" __declspec(dllexport) void HarnessMain() {
    int argc = 0;
    wchar_t** argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    int code = Run(argc, argv);
    fflush(stdout);
    ExitProcess((UINT)code);
}

static int Run(int argc, wchar_t** argv) {
    if (argc < 3) {
        printf("usage: skins_stub <DDDA.exe> <enemy_skins.asi>\n");
        return 1;
    }
    if (!MapImage(argv[1])) return 2;
    g_managerVtable[0x30 / 4] = (void*)FakeGet;
    g_manager.vtable = g_managerVtable;
    g_managerAddr = (uint32_t)(uintptr_t)&g_manager;
    g_fakeGetAddr = (uint32_t)(uintptr_t)&FakeGet;
    *(uint32_t*)RES_MANAGER_GLOBAL = g_managerAddr;

    const uint8_t before = *(const uint8_t*)SITE_BODY;
    SetEnvironmentVariableW(L"RIFTSTONE_SKINS_HARNESS", L"1");
    HMODULE plugin = LoadLibraryW(argv[2]);
    printf("plugin\n");
    Check(plugin != nullptr, "the plugin loads");
    Check(before == 0x68 && *(const uint8_t*)SITE_BODY == 0xE9, "the body material site is patched");
    Check(*(const uint8_t*)SITE_GOAT == 0xE9 && *(const uint8_t*)SITE_SNAKE == 0xE9, "the part material sites are patched");
    if (g_fails) return 1;

    const std::string V = "model\\em\\e52\\e5200\\";
    printf("material sites (the real code from each site up to rResourceManager::get)\n");
    Unit body(VT_BODY), goat(VT_GOAT), snake(VT_SNAKE);
    goat.parent(PARENT_GOAT, body);
    snake.parent(PARENT_SNAKE, body);
    auto regsKept = [](uint32_t unit, bool inEbp) {
        return g_capEsi == SENTINEL_ESI && g_capEdi == SENTINEL_EDI && g_capEcx == g_managerAddr &&
               (inEbp ? (g_capEbp == unit && g_capEbx == SENTINEL_EBX) : g_capEbx == unit) &&
               g_lastDti == RMATERIAL_DTI && g_lastFlag == 1;
    };
    Check(EnterSite(SITE_BODY, body.p(), false) == V + "e5200_a", "unmarked chimera: vanilla e5200_a");
    Check(regsKept(g_unit, false), "  registers, DTI and load flag reach the call intact");
    body.mark(1);
    Check(EnterSite(SITE_BODY, body.p(), false) == V + "s01\\e5200_a", "skin 1: s01\\e5200_a");
    Check(regsKept(g_unit, false), "  registers intact");
    Check(EnterSite(SITE_GOAT, goat.p(), false) == V + "s01\\e5200_00_a", "goat of a skin-1 chimera: s01\\e5200_00_a");
    Check(regsKept(g_unit, false), "  registers intact");
    Check(EnterSite(SITE_SNAKE, snake.p(), true) == V + "s01\\e5200_01_a", "snake of a skin-1 chimera (unit in ebp): s01\\e5200_01_a");
    Check(regsKept(g_unit, true), "  registers intact");
    body.mark(42);
    Check(EnterSite(SITE_BODY, body.p(), false) == V + "s42\\e5200_a", "skin 42: s42\\e5200_a");
    body.mem[FLAG] = 1;  // the multiplier really in use: not a marker
    Check(EnterSite(SITE_BODY, body.p(), false) == V + "e5200_a", "flag on: the value is a real multiplier, vanilla");
    Check(EnterSite(SITE_GOAT, goat.p(), false) == V + "e5200_00_a", "goat of that chimera: vanilla");
    body.mark(100);
    Check(EnterSite(SITE_BODY, body.p(), false) == V + "e5200_a", "skin 100 (beyond 99): vanilla");
    body.mark(0);
    Check(EnterSite(SITE_BODY, body.p(), false) == V + "e5200_a", "skin 0: vanilla");
    Unit other(0x015C4E2C);  // not uEm5200's vtable
    other.mark(3);
    Check(EnterSite(SITE_BODY, other.p(), false) == V + "e5200_a", "another class with the marker: vanilla");
    Unit orphan(VT_GOAT);
    Check(EnterSite(SITE_GOAT, orphan.p(), false) == V + "e5200_00_a", "goat with no parent: vanilla");

    printf("damage-texture helpers (the real helpers walk the table the thunk picked)\n");
    Unit b2(VT_BODY), g2(VT_GOAT), s2(VT_SNAKE);
    b2.materialsFor(TABLE_BODY, 11, 5);
    g2.materialsFor(TABLE_GOAT, 4, 4);
    s2.materialsFor(TABLE_SNAKE, 5, 4);
    g2.parent(PARENT_GOAT, b2);
    s2.parent(PARENT_SNAKE, b2);
    auto same = [](const std::vector<std::string>& a, const std::vector<std::string>& b) { return a == b && !a.empty(); };
    Check(same(RunHelper(HELPER_BODY, 0, b2), Expected(TABLE_BODY, 11, 5, nullptr)), "unmarked body: every vanilla texture");
    Check(same(RunHelper(HELPER_GOAT, 0, g2), Expected(TABLE_GOAT, 4, 4, nullptr)), "unmarked goat: vanilla");
    Check(same(RunHelper(HELPER_SNAKE, 0, s2), Expected(TABLE_SNAKE, 5, 4, nullptr)), "unmarked snake: vanilla");
    b2.mark(7);
    Check(same(RunHelper(HELPER_BODY, 0, b2), Expected(TABLE_BODY, 11, 5, "s07")), "skin 7 body: albedo maps from s07, the rest vanilla");
    Check(same(RunHelper(HELPER_GOAT, 0, g2), Expected(TABLE_GOAT, 4, 4, "s07")), "skin 7 goat: s07 albedo");
    Check(same(RunHelper(HELPER_SNAKE, 0, s2), Expected(TABLE_SNAKE, 5, 4, "s07")), "skin 7 snake: s07 albedo");
    Unit gore(VT_BODY);
    gore.materialsFor(TABLE_BODY + 0xDC, 11, 5);
    gore.mark(7);
    Check(same(RunHelper(HELPER_BODY, 1, gore), Expected(TABLE_BODY + 0xDC, 11, 5, nullptr)),
          "gorechimera block (variant 1) with a marker: untouched");

    printf(g_fails ? "\n%d check(s) FAILED\n" : "\nall checks passed\n", g_fails);
    return g_fails ? 1 : 0;
}

// lod_harness_core -- runs the lod_tuner hook inside the real game code, without the game.
//
//   lod_stub.exe <DDDA.exe> <lod_tuner.asi> <on|farlod|flat|off>   (the stub loads this DLL and calls HarnessMain)
//
// Maps DDDA.exe's image at its fixed base (0x00400000) over the stub's image, loads the plugin (which
// verifies and patches the mapped code), then:
//   * enters rModel::load right after its primitive loop (0x00FA9479) with fake models (path, joints,
//     bounding radius, MODEL_INFO, a primitive array with LOD masks), lets the real instruction stream
//     run through the thunk and stops at the next game instruction (0x00FA9480).  There it checks the
//     model's distances, that the two instructions the jmp covers still loaded ecx from [esp+0x2C] and
//     edx from [esi+0x78], and that eax, esi, edi, ebx, ebp, esp and xmm3 came through intact;
//   * hands a scaled model to two real readers: uSimSoftBody::getTargetLODLevel, which picks
//     HIGH/MEDIUM/LOW with the same ViewRange arithmetic the draw paths inline, and
//     cInstancingCulling::updateLODParam, which sets instanced vegetation's LOD and billboard distances.
// Profiles (run_tests.py writes the matching lod_tuner.ini and a stand-in game config.ini):
//   on     -- the shipped lod_tuner.ini; the game config says 2560x1440, CameraFov 0, ViewRange FARTHEST
//   farlod -- the same with Farthest = lod: each of the ten ViewRange tests' FARTHEST branch now lands on the
//             store that makes ViewRange the multiplier, and the cloth's LOD choice runs x3 at FARTHEST
//   flat   -- Scale=3, PopPixels=0, Characters=1.5, ScreenHeight=1080, MaxDistance=100 (m)
//   off    -- Enabled=0: nothing patched, the game's values reach the readers unchanged
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
constexpr uintptr_t SITE = 0x00FA9479, NEXT = 0x00FA9480;
constexpr uintptr_t CFG_GLOBAL = 0x018D1D20;       // the PC graphics settings; +0x38 = ViewRange
constexpr uintptr_t GET_TARGET_LOD = 0x0083D220;   // uSimSoftBody::getTargetLODLevel(int), thiscall
constexpr uintptr_t UPDATE_LOD_PARAM = 0x0113D430; // cInstancingCulling::updateLODParam(MODEL_INFO*), thiscall
constexpr uint32_t STACK_2C = 0x2C2C2C2C, MODEL_78 = 0x78787878;

int g_fails = 0;
void Check(bool ok, const std::string& what) {
    printf("  %s  %s\n", ok ? "pass" : "FAIL", what.c_str());
    if (!ok) g_fails++;
}

// ---- a fake rModel, as rModel::load has it after reading the primitives ------------------------
// masks: one 48-byte primitive per entry, LOD mask in byte +7 (1 HIGH, 2 MEDIUM, 4 LOW).
struct Model {
    alignas(16) uint8_t mem[0x240];
    std::vector<uint8_t> prims;
    int32_t mid0, low0;
    Model(const char* path, uint32_t joints, float radius, int32_t mid, int32_t low, std::vector<uint8_t> masks = {3, 4})
        : mid0(mid), low0(low) {
        memset(mem, 0, sizeof mem);
        if (path) strncpy_s((char*)mem + 8, 64, path, _TRUNCATE);
        *(uint32_t*)(mem + 0x64) = joints;
        *(float*)(mem + 0xBC) = radius;
        *(int32_t*)(mem + 0xE0) = mid;
        *(int32_t*)(mem + 0xE4) = low;
        *(uint32_t*)(mem + 0x78) = MODEL_78;
        prims.assign(masks.size() * 48 + 1, 0);
        for (size_t i = 0; i < masks.size(); i++) prims[i * 48 + 7] = masks[i];
        *(uint32_t*)(mem + 0x70) = masks.empty() ? 0 : (uint32_t)(uintptr_t)prims.data();
        *(uint32_t*)(mem + 0x74) = (uint32_t)masks.size();
    }
    int32_t mid() const { return *(const int32_t*)(mem + 0xE0); }
    int32_t low() const { return *(const int32_t*)(mem + 0xE4); }
};

// ---- entering rModel::load after the primitive loop ---------------------------------------------
jmp_buf g_back;
uint32_t g_model, g_site = SITE, g_entryEsp;
uint32_t g_capEsp, g_capEax, g_capEcx, g_capEdx, g_capEsi, g_capEdi, g_capEbx, g_capEbp;
alignas(16) uint32_t g_x3In[4] = {0x3F800000, 0x40000000, 0x40400000, 0x40800000};
alignas(16) uint32_t g_x3Out[4];

void __cdecl CaptureC() { longjmp(g_back, 1); }

// Placed by the harness at 0x00FA9480, the game instruction after the patched pair.
__declspec(naked) void Capture() {
    __asm {
        mov g_capEax, eax
        mov g_capEcx, ecx
        mov g_capEdx, edx
        mov g_capEsi, esi
        mov g_capEdi, edi
        mov g_capEbx, ebx
        mov g_capEbp, ebp
        mov g_capEsp, esp
        movdqu xmmword ptr g_x3Out, xmm3
        call CaptureC
    }
}

// The state rModel::load has at the site: the model in esi, its frame on the stack ([esp+0x2C] is
// read by the first covered instruction), sentinels everywhere else.
__declspec(naked) void JumpToSite() {
    __asm {
        sub esp, 0x200
        mov dword ptr [esp + 0x2C], 0x2C2C2C2C
        movdqu xmm3, xmmword ptr g_x3In
        mov esi, g_model
        mov eax, 0x55555555
        mov ecx, 0x66666666
        mov edx, 0x77777777
        mov edi, 0x22222222
        mov ebx, 0x33333333
        mov ebp, 0x44444444
        mov g_entryEsp, esp
        jmp dword ptr [g_site]
    }
}

// Nothing here needs unwinding: Capture longjmps out of game code.
#pragma warning(push)
#pragma warning(disable : 4611)
void EnterRaw() {
    if (setjmp(g_back) == 0) JumpToSite();
}
#pragma warning(pop)

bool RunSite(Model& m) {
    g_model = (uint32_t)(uintptr_t)m.mem;
    memset(g_x3Out, 0, sizeof g_x3Out);
    g_capEsp = 0;
    EnterRaw();
    return g_capEcx == STACK_2C && g_capEdx == MODEL_78 && g_capEax == 0x55555555 && g_capEsi == g_model &&
           g_capEdi == 0x22222222 && g_capEbx == 0x33333333 && g_capEbp == 0x44444444 && g_capEsp == g_entryEsp &&
           memcmp(g_x3In, g_x3Out, sizeof g_x3In) == 0;
}

// ---- calling a real reader ---------------------------------------------------------------------
uint32_t g_fn, g_self, g_arg, g_ret;
__declspec(naked) void CallThisAsm() {
    __asm {
        push ebx
        push esi
        push edi
        push ebp
        mov ecx, g_self
        push g_arg
        call dword ptr [g_fn]
        mov g_ret, eax
        pop ebp
        pop edi
        pop esi
        pop ebx
        ret
    }
}
uint32_t CallThis(uintptr_t fn, const void* self, uint32_t arg) {
    g_fn = (uint32_t)fn;
    g_self = (uint32_t)(uintptr_t)self;
    g_arg = arg;
    CallThisAsm();
    return g_ret;
}

struct Readers {
    alignas(16) uint8_t cfg[0x80] = {};
    alignas(16) uint8_t base[0x400] = {};  // uBaseModel: +0xF0 rModel*, +0x108 flags
    alignas(16) uint8_t soft[0x400] = {};  // uSimSoftBody: +0x1E0 target uBaseModel*
    explicit Readers(Model& m) {
        *(uint32_t*)CFG_GLOBAL = (uint32_t)(uintptr_t)cfg;
        *(uint32_t*)(base + 0xF0) = (uint32_t)(uintptr_t)m.mem;
        *(uint32_t*)(base + 0x108) = 0x1E00;  // mLODType = LOD_AUTO (-1)
        *(uint32_t*)(soft + 0x1E0) = (uint32_t)(uintptr_t)base;
    }
    uint32_t level(uint32_t viewRange, int32_t dist) {
        *(uint32_t*)(cfg + 0x38) = viewRange;
        return CallThis(GET_TARGET_LOD, soft, (uint32_t)dist);
    }
};

// The game's choice around both thresholds for ViewRange NORMAL (x1) and FAR (x2), and FARTHEST: always HIGH, or
// with Farthest = lod the same thresholds x3.
void CheckLevels(Model& m, int32_t mid, int32_t low, const char* what, bool farthestLod = false) {
    Readers r(m);
    bool ok = true;
    for (uint32_t vr = 1; vr <= (farthestLod ? 3u : 2u); vr++) {
        int32_t a = mid * (int32_t)vr, b = low * (int32_t)vr;
        ok &= r.level(vr, a) == 1 && r.level(vr, a + 1) == 2 && r.level(vr, b) == 2 && r.level(vr, b + 1) == 4;
    }
    ok &= r.level(3, 2000000000) == (farthestLod ? 4u : 1u);
    char msg[220];
    _snprintf_s(msg, sizeof msg, _TRUNCATE, "%s: the game picks HIGH to %d, MEDIUM to %d, LOW beyond (x2 at FAR; FARTHEST %s)",
                what, mid, low, farthestLod ? "x3" : "always HIGH");
    Check(ok, msg);
}

// The ten ViewRange tests (lod_tuner's FAR_SITES): the two bytes where FARTHEST branches, the game's own, and the
// store that makes ViewRange the multiplier.  With Farthest = lod each is a je/jmp that lands on that store.
struct FarBranch {
    uintptr_t at;
    uint8_t orig[2];
    uintptr_t store;
};
const FarBranch FAR_BRANCHES[] = {
    {0x0083D24D, {0x74, 0x33}, 0x0083D254}, {0x00C6E336, {0x74, 0x2F}, 0x00C6E33D}, {0x00FA77B9, {0x74, 0x33}, 0x00FA77C0},
    {0x00FFC51C, {0x74, 0x33}, 0x00FFC523}, {0x0084596E, {0xC7, 0x44}, 0x0084597D}, {0x00B898BE, {0xC7, 0x44}, 0x00B898CD},
    {0x00F1FE44, {0xC7, 0x44}, 0x00F1FE53}, {0x00EA5445, {0x89, 0x5C}, 0x00EA5450}, {0x00F61974, {0x89, 0x5C}, 0x00F6197F},
    {0x00FA604D, {0x89, 0x5C}, 0x00FA6058},
};

void CheckFarBranches(bool patched) {
    int good = 0;
    for (const FarBranch& b : FAR_BRANCHES) {
        const uint8_t* p = (const uint8_t*)b.at;
        bool lands = (p[0] == 0x74 || p[0] == 0xEB) && b.at + 2 + (int8_t)p[1] == b.store;
        // the store itself: mov eax, edx (8B C2) or mov [esp+m], eax (89 44 24 m)
        const uint8_t* s = (const uint8_t*)b.store;
        bool store = (s[0] == 0x8B && s[1] == 0xC2) || (s[0] == 0x89 && s[1] == 0x44 && s[2] == 0x24);
        good += patched ? (lands && store) : memcmp(p, b.orig, 2) == 0;
    }
    char msg[200];
    _snprintf_s(msg, sizeof msg, _TRUNCATE, patched ? "Farthest = lod: all ten ViewRange tests send FARTHEST to the multiplier's "
                                                      "store (%d of 10)"
                                                    : "the ten ViewRange tests are the game's own (%d of 10)", good);
    Check(good == 10, msg);
}

void CheckInstancing(Model& m, int32_t mid, int32_t low, const char* what) {
    alignas(16) uint8_t cull[0x200] = {};
    *(float*)(cull + 0x2C) = 1500.0f;  // mLodBillboardDistance
    *(float*)(cull + 0x08) = 123.0f;
    CallThis(UPDATE_LOD_PARAM, cull, (uint32_t)(uintptr_t)(m.mem + 0xE0));
    const float* d = (const float*)(cull + 0x08);
    bool ok = d[0] == 0.0f && d[1] == (float)mid && d[2] == (float)low && d[3] == (float)low + 1500.0f;
    char msg[200];
    _snprintf_s(msg, sizeof msg, _TRUNCATE, "%s: instanced LOD distances 0 / %d / %d, billboards from %d", what, mid, low,
                low + 1500);
    Check(ok, msg);
}

struct Case {
    const char* what;
    const char* path;
    uint32_t joints;
    float radius;
    int32_t mid0, low0, mid, low;
    std::vector<uint8_t> masks;
};

bool Near(int32_t a, int32_t b) { return a == b || a == b + 1 || a == b - 1; }

void RunCases(const std::vector<Case>& cases) {
    for (const Case& c : cases) {
        Model m(c.path, c.joints, c.radius, c.mid0, c.low0, c.masks);
        bool regs = RunSite(m);
        char msg[260];
        _snprintf_s(msg, sizeof msg, _TRUNCATE, "%s: %d/%d -> %d/%d (want %d/%d)", c.what, c.mid0, c.low0, m.mid(), m.low(),
                    c.mid, c.low);
        Check(Near(m.mid(), c.mid) && Near(m.low(), c.low), msg);
        Check(regs, "  ecx/edx loaded as the game's own two instructions do; eax, esi, edi, ebx, ebp, esp, xmm3 intact");
    }
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
        return printf("SKIP: run me through lod_stub.exe (host at %p, 0x%X bytes)\n", (void*)host,
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

void PlaceCapture() {
    uint8_t* p = (uint8_t*)NEXT;
    int32_t rel = (int32_t)((uintptr_t)Capture - (NEXT + 5));
    p[0] = 0xE9;
    memcpy(p + 1, &rel, 4);
}

uintptr_t JmpTarget(uintptr_t at) {
    const uint8_t* p = (const uint8_t*)at;
    return p[0] == 0xE9 ? at + 5 + *(const int32_t*)(p + 1) : 0;
}
}  // namespace

static int Run(int argc, wchar_t** argv);

// Called by lod_stub.exe.  Never returns: the stub's code is gone once DDDA.exe is mapped.
extern "C" __declspec(dllexport) void HarnessMain() {
    int argc = 0;
    wchar_t** argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    int code = Run(argc, argv);
    fflush(stdout);
    ExitProcess((UINT)code);
}

static int Run(int argc, wchar_t** argv) {
    if (argc < 4) {
        printf("usage: lod_stub <DDDA.exe> <lod_tuner.asi> <on|farlod|flat|off>\n");
        return 1;
    }
    const std::wstring profile = argv[3];
    if (!MapImage(argv[1])) return 2;
    const uint8_t before[7] = {0x8B, 0x4C, 0x24, 0x2C, 0x8B, 0x56, 0x78};
    Check(memcmp((const void*)SITE, before, 7) == 0, "the mapped rModel::load leaves its primitive loop at 0x00FA9479");

    SetEnvironmentVariableW(L"RIFTSTONE_LOD_HARNESS", L"1");
    HMODULE plugin = LoadLibraryW(argv[2]);
    printf("plugin (profile %S)\n", profile.c_str());
    Check(plugin != nullptr, "the plugin loads");
    if (profile == L"off") {
        Check(memcmp((const void*)SITE, before, 7) == 0, "Enabled=0: rModel::load is left as it is");
    } else {
        uintptr_t thunk = JmpTarget(SITE);
        Check(thunk != 0 && *(const uint8_t*)(SITE + 5) == 0x90 && *(const uint8_t*)(SITE + 6) == 0x90,
              "the site is a jmp to the plugin's thunk");
        // A second copy of the plugin must see the patched site and leave it alone.
        wchar_t copy[MAX_PATH];
        wcscpy_s(copy, argv[2]);
        wchar_t* dot = wcsrchr(copy, L'.');
        if (dot) *dot = 0;
        wcscat_s(copy, L"_copy.asi");
        if (CopyFileW(argv[2], copy, FALSE)) {
            HMODULE second = LoadLibraryW(copy);
            Check(second != nullptr && JmpTarget(SITE) == thunk, "a second copy refuses the patched site and changes nothing");
        }
    }
    if (g_fails) return 1;
    PlaceCapture();

    printf("rModel::load after its primitives, the real instruction stream through the thunk\n");
    if (profile == L"on") {
        // 2560x1440, 40 degrees, PopPixels 24: HIGH is kept to 164.85 * radius; at least x2 (1440 / 720).
        RunCases({
            {"small object (r 0.37 m, scenery), the pixel rule", "model\\om\\om5655\\model\\om5655", 0, 37.0f, 3000, 5000, 6099, 10166, {1}},
            {"tiny object (r 0.2 m), the x2 floor", "model\\om\\om5662\\model\\om5662", 0, 20.0f, 3000, 5000, 6000, 10000, {1}},
            {"stage piece (r 87 m), capped at 10 km", "scr\\st200\\model\\st200_md03", 0, 8704.0f, 1000, 3000, 1000000, 1000000, {255, 3, 4}},
            {"terrain (r 364 m, vanilla 4.5/6 km)", "scr\\st200\\model\\st200_md00", 0, 36352.0f, 450000, 600000, 1000000, 1000000, {255, 3}},
            {"vanilla beyond the cap is never lowered", "scr\\st100\\model\\st100_md01", 0, 100.0f, 2000000, 3000000, 2000000, 3000000},
            {"MEDIUM beyond LOW keeps its order", "model\\om\\om1000\\model\\om1001", 0, 100.0f, 3000, 2000, 16485, 10990},
            {"forward slashes and capitals", "SCR/ST300/model/st300_md05", 0, 37.0f, 3000, 5000, 6099, 10166},
            {"far-only stand-in (nothing drawn at HIGH): vanilla", "scr\\st380\\model\\st380_md12", 0, 783.0f, 1000, 4000, 1000, 4000, {4}},
            {"far-only with several LOW/MEDIUM parts: vanilla", "scr\\st380\\model\\st380_md99", 0, 783.0f, 1000, 4000, 1000, 4000, {4, 12, 6}},
            {"no LOD meshes at all (instancing billboards use the pair)", "model\\om\\om0500\\model\\om0501", 0, 37.0f, 3000, 5000, 6099, 10166, {255}},
            {"no primitives", "model\\om\\om0500\\model\\om0502", 0, 37.0f, 3000, 5000, 6099, 10166, {}},
            {"enemy (model\\em): Characters=1.0, vanilla", "model\\em\\e54\\e5400\\e5400", 60, 1244.0f, 1000, 3000, 1000, 3000},
            {"player armour (model\\pl): vanilla", "model\\pl\\f\\f_arm_a\\f_arm_a802\\f_arm_a802", 40, 78.0f, 1000, 3000, 1000, 3000},
            {"no path, no joints: scenery", nullptr, 0, 500.0f, 2000, 5000, 82424, 206061},
            {"unprintable path with joints: vanilla", "\x01\x02\x03", 12, 500.0f, 2000, 5000, 2000, 5000},
        });
        printf("the game's readers see the new distances\n");
        Model m("model\\om\\om5655\\model\\om5655", 0, 37.0f, 3000, 5000, {1});
        RunSite(m);
        CheckLevels(m, 6099, 10166, "draw-path LOD choice");
        CheckInstancing(m, 6099, 10166, "instanced vegetation");
        CheckFarBranches(false);
    } else if (profile == L"farlod") {
        RunCases({
            {"small object (r 0.37 m, scenery), the pixel rule", "model\\om\\om5655\\model\\om5655", 0, 37.0f, 3000, 5000, 6099, 10166, {1}},
            {"enemy (model\\em): Characters=1.0, vanilla", "model\\em\\e54\\e5400\\e5400", 60, 1244.0f, 1000, 3000, 1000, 3000},
        });
        CheckFarBranches(true);
        printf("the game's readers see the new distances, and FARTHEST has levels of detail\n");
        Model m("model\\om\\om5655\\model\\om5655", 0, 37.0f, 3000, 5000, {1});
        RunSite(m);
        CheckLevels(m, 6099, 10166, "cloth LOD choice", true);
        Model e("model\\em\\e54\\e5400\\e5400", 60, 1244.0f, 1000, 3000);
        RunSite(e);
        CheckLevels(e, 1000, 3000, "cloth LOD choice (enemy)", true);
    } else if (profile == L"flat") {
        // Scale 3, PopPixels 0, Characters 1.5, cap 100 m = 10000 units.
        RunCases({
            {"scenery x3", "model\\om\\om5655\\model\\om5655", 0, 37.0f, 3000, 5000, 9000, 10000},
            {"big scenery x3, no pixel rule", "scr\\st200\\model\\st200_md03", 0, 8704.0f, 1000, 3000, 3000, 9000},
            {"vanilla above the cap stays", "scr\\st200\\model\\st200_md00", 0, 36352.0f, 20000, 30000, 20000, 30000},
            {"far-only stand-in: vanilla", "scr\\st380\\model\\st380_md12", 0, 783.0f, 1000, 4000, 1000, 4000, {4}},
            {"enemy x1.5 (Characters)", "model\\em\\e54\\e5400\\e5400", 60, 1244.0f, 1000, 3000, 1500, 4500},
        });
        printf("the game's readers see the new distances\n");
        Model m("model\\em\\e54\\e5400\\e5400", 60, 1244.0f, 1000, 3000);
        RunSite(m);
        CheckLevels(m, 1500, 4500, "draw-path LOD choice (enemy)");
        CheckFarBranches(false);
    } else {
        RunCases({
            {"scenery untouched", "model\\om\\om5655\\model\\om5655", 0, 37.0f, 3000, 5000, 3000, 5000},
            {"stage piece untouched", "scr\\st200\\model\\st200_md03", 0, 8704.0f, 1000, 3000, 1000, 3000},
        });
        printf("the game's readers see vanilla\n");
        Model m("model\\om\\om5655\\model\\om5655", 0, 37.0f, 3000, 5000);
        RunSite(m);
        CheckLevels(m, 3000, 5000, "draw-path LOD choice");
        CheckInstancing(m, 3000, 5000, "instanced vegetation");
        CheckFarBranches(false);
    }

    printf(g_fails ? "\n%d check(s) FAILED\n" : "\nall checks passed\n", g_fails);
    return g_fails ? 1 : 0;
}

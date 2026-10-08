// engine_harness_core -- the runtime's engine-specific parts on the real DDDA.exe code, without the game.
//
//   DDDA.exe (engine_stub.exe) <real DDDA.exe> <classes.txt>
//
// Maps DDDA.exe's image at its fixed base over the stub (which reserved that range), then runs
// fixes.cpp (compiled in) against it:
//   * game detection: the name and the PE time stamp of build 2364871
//   * class names: for each class in classes.txt ("<vftable> <dti> <name VA> <name>"), a fake object
//     that carries the real vftable; the MtDTI's name pointer is set the way its constructor sets it
//     at start-up (+4), and ClassNameOf must read the name through the game's own getDTI code bytes;
//     objects that are not engine objects, or not memory at all, must give nothing and not fault
//   * the frame-rate ceiling: both "Variable" sites repointed at the setting, the shared 150.0
//     constant and its two other readers untouched; a tampered site patches nothing
//   * enemy slots: sSetManager's instance pointer aimed at a fake manager with filled slots
//   * resources: DDDA.exe's own sResource::registTable and ::release on a stand-in resource with two users;
//     the second release takes it out of the table and deletes it at once (nothing unused is kept)
//   * the game's exit: every site of exit_sites.h verified, sApp's quit flag read through its instance
//     pointer, and DDDA's own WM_CLOSE handler (in 0x00DF1550) run on a window of ours: it asks sMain,
//     runs the exit request (0x00DBD0F0), which marks sMain+0x34 and sends WM_DESTROY to sApp's window
// Prints "pass"/"FAIL" lines; exits 0 when everything passed, 1 on a failure, 2 when the image
// cannot be mapped here (reported as a skip).  Writes nothing but its own log lines to stdout.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shellapi.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include <string>
#include <vector>

// ---- what fixes.cpp needs from the rest of the loader -------------------------------------------
#include "../runtime.h"
wchar_t g_root[MAX_PATH], g_stateDir[MAX_PATH], g_logDir[MAX_PATH], g_ini[MAX_PATH];
HMODULE g_self;
DWORD g_mainThread;
ULONGLONG g_startTick;
volatile LONG g_redirects, g_missing, g_frames, g_fallbacks, g_fatals, g_missingNext, g_recentNext;
wchar_t g_missingRing[MISSING_RING][MAX_PATH];
wchar_t g_recent[RECENT][MAX_PATH];
PluginInfo g_pluginInfo[MAX_PLUGINS];
int g_pluginCount;
HWND g_gameWindow;
volatile LONG g_d3dWindowed = -1;
CreateFileW_t Real_CreateFileW = CreateFileW;
GetFileAttributesW_t Real_GetFileAttributesW = GetFileAttributesW;
static int g_maxFps = 0;
static std::string g_lastLog;
void LogLine(const wchar_t* fmt, ...) {
    wchar_t line[1024];
    va_list ap;
    va_start(ap, fmt);
    _vsnwprintf_s(line, _countof(line), _TRUNCATE, fmt, ap);
    va_end(ap);
    char utf8[2048];
    WideCharToMultiByte(CP_UTF8, 0, line, -1, utf8, sizeof utf8, NULL, NULL);
    g_lastLog += utf8;                             // accumulate: one FixesApplyPatches now logs several guards
    g_lastLog += '\n';
    printf("  log   %s\n", utf8);
}
static int g_shadow = 0;
static int g_ragdolls = 0;                        // [guard] ragdoll_bodies: off until TestRagdolls turns it on
static int g_guitext = 0;                         // [guard] gui_text: off until TestGuiText turns it on
static int g_widefoliage = 0;                      // [render] wide_foliage: off until TestStreamWindow turns it on
static int g_particles = 0;                        // [guard] particles: off until TestParticleGuard turns it on
static int g_shadowbuffers = 1;                    // [guard] shadow_buffers: on by default, like the game
int IniInt(const wchar_t* section, const wchar_t* key, int def) {
    if (wcscmp(section, L"fps") == 0 && wcscmp(key, L"max_fps") == 0) return g_maxFps;
    if (wcscmp(section, L"render") == 0 && wcscmp(key, L"shadow_map_size") == 0) return g_shadow;
    if (wcscmp(section, L"render") == 0 && wcscmp(key, L"wide_foliage") == 0) return g_widefoliage;
    if (wcscmp(section, L"guard") == 0 && wcscmp(key, L"ragdoll_bodies") == 0) return g_ragdolls;
    if (wcscmp(section, L"guard") == 0 && wcscmp(key, L"gui_text") == 0) return g_guitext;
    if (wcscmp(section, L"guard") == 0 && wcscmp(key, L"particles") == 0) return g_particles;
    if (wcscmp(section, L"guard") == 0 && wcscmp(key, L"shadow_buffers") == 0) return g_shadowbuffers;
    return def;
}
void IniStr(const wchar_t*, const wchar_t*, const wchar_t* def, wchar_t* out, DWORD cap) { wcsncpy_s(out, cap, def, _TRUNCATE); }
ULONGLONG UptimeMs() { return 0; }
void Stamp(wchar_t* out, size_t cap) { wcsncpy_s(out, cap, L"20260101-000000", _TRUNCATE); }
BOOL HookImport(const char*, const char*, void*, void**) { return FALSE; }
void LiveNote(const char*, const wchar_t*) {}

// resources.cpp is the loader's file side; the engine harness runs fixes.cpp's engine code only.
void ResourcesInit() {}
HANDLE ArchiveOpen(const wchar_t*, LPSECURITY_ATTRIBUTES, DWORD) { return INVALID_HANDLE_VALUE; }
#include "../fixes.cpp"

namespace {
constexpr uintptr_t BASE = 0x00400000;
int g_fails = 0;
void Check(bool ok, const std::string& what) {
    printf("  %s  %s\n", ok ? "pass" : "FAIL", what.c_str());
    if (!ok) g_fails++;
}

std::vector<uint8_t> g_file;

bool MapImage(const wchar_t* exe) {
    if (g_file.empty()) {
        HANDLE f = CreateFileW(exe, GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, 0, nullptr);
        if (f == INVALID_HANDLE_VALUE) return printf("cannot open the exe\n"), false;
        DWORD size = GetFileSize(f, nullptr), got = 0;
        g_file.resize(size);
        ReadFile(f, g_file.data(), size, &got, nullptr);
        CloseHandle(f);
    }
    auto* dos = (IMAGE_DOS_HEADER*)g_file.data();
    auto* nt = (IMAGE_NT_HEADERS32*)(g_file.data() + dos->e_lfanew);
    if (nt->OptionalHeader.ImageBase != BASE) return printf("unexpected image base\n"), false;
    auto* host = (const uint8_t*)GetModuleHandleW(nullptr);
    auto* hostNt = (const IMAGE_NT_HEADERS32*)(host + ((const IMAGE_DOS_HEADER*)host)->e_lfanew);
    DWORD need = nt->OptionalHeader.SizeOfImage;
    if ((uintptr_t)host != BASE || hostNt->OptionalHeader.SizeOfImage < need)
        return printf("SKIP: run me through engine_stub.exe (host at %p)\n", (void*)host), false;
    void* at = (void*)BASE;
    DWORD old;
    if (!VirtualProtect(at, need, PAGE_EXECUTE_READWRITE, &old)) return printf("cannot unprotect the host image\n"), false;
    memset(at, 0, need);
    memcpy(at, g_file.data(), nt->OptionalHeader.SizeOfHeaders);
    auto* sec = IMAGE_FIRST_SECTION(nt);
    for (int i = 0; i < nt->FileHeader.NumberOfSections; i++, sec++) {
        DWORD n = min(sec->SizeOfRawData, sec->Misc.VirtualSize ? sec->Misc.VirtualSize : sec->SizeOfRawData);
        if (n && sec->PointerToRawData + n <= g_file.size())
            memcpy((uint8_t*)at + sec->VirtualAddress, g_file.data() + sec->PointerToRawData, n);
    }
    return true;
}

struct ClassCase { uint32_t vtable, dti, nameVa; std::string name; };

std::vector<ClassCase> ReadCases(const wchar_t* path) {
    std::vector<ClassCase> out;
    FILE* f = nullptr;
    if (_wfopen_s(&f, path, L"r") || !f) return out;
    char line[512];
    while (fgets(line, sizeof line, f)) {
        ClassCase c;
        char name[256];
        if (sscanf_s(line, "%x %x %x %255s", &c.vtable, &c.dti, &c.nameVa, name, (unsigned)sizeof name) == 4) {
            c.name = name;
            out.push_back(c);
        }
    }
    fclose(f);
    return out;
}

void TestClassNames(const std::vector<ClassCase>& cases) {
    printf("class names from the engine's type info\n");
    int named = 0;
    for (const ClassCase& c : cases) {
        *(uint32_t*)(uintptr_t)(c.dti + 4) = c.nameVa;         // what MtDTI's constructor does at start-up
        uint32_t object[4] = {c.vtable, 0, 0, 0};
        char got[128] = "";
        BOOL ok = ClassNameOf(object, got, sizeof got);
        if (ok && c.name == got) named++;
        else printf("  miss  %s: got %s\n", c.name.c_str(), ok ? got : "(nothing)");
    }
    Check(!cases.empty() && named == (int)cases.size(),
          "ClassNameOf names " + std::to_string(named) + " of " + std::to_string(cases.size()) +
              " sample classes through their own vftable and getDTI");
    char got[128];
    Check(!ClassNameOf(nullptr, got, sizeof got), "a null pointer gives nothing");
    Check(!ClassNameOf((void*)0x10, got, sizeof got), "an address that is not memory gives nothing (and does not fault)");
    uint32_t heapObject[2] = {(uint32_t)(uintptr_t)&heapObject, 0};
    Check(!ClassNameOf(heapObject, got, sizeof got), "an object whose table is not in the game gives nothing");
    uint32_t codeObject[1] = {0x00401000};
    Check(!ClassNameOf(codeObject, got, sizeof got), "a table whose slot 4 is not getDTI gives nothing");
    if (!cases.empty()) {
        const ClassCase& c = cases[0];
        uint32_t saved = *(uint32_t*)(uintptr_t)(c.dti + 4);
        *(uint32_t*)(uintptr_t)(c.dti + 4) = (uint32_t)(uintptr_t)"not in the image";
        uint32_t object[1] = {c.vtable};
        Check(!ClassNameOf(object, got, sizeof got), "a type name outside the game's image gives nothing");
        *(uint32_t*)(uintptr_t)(c.dti + 4) = saved;
    }
}

void TestFps() {
    printf("frame-rate ceiling\n");
    const uint32_t CONST150 = 0x01433AA8;
    const uintptr_t OTHER_READERS[] = {0x00F5BD29, 0x00FA1975};
    g_maxFps = 165;
    FixesApplyPatches();
    uint32_t d1 = *(uint32_t*)0x00EDD4B1, d2 = *(uint32_t*)0x00EDDC8A;
    Check(d1 == d2 && d1 != CONST150 && *(float*)(uintptr_t)d1 == 165.0f,
          "both \"Variable\" sites now read the setting (165 fps)");
    Check(*(float*)(uintptr_t)CONST150 == 150.0f, "the shared 150.0 constant is left alone");
    bool others = true;
    for (uintptr_t r : OTHER_READERS) others = others && *(uint32_t*)r == CONST150;
    Check(others, "the constant's two other readers still read it");
    Check(*(uint32_t*)0x00EDD4BB == 0x01408A88 && *(uint32_t*)0x00EDD4C5 == 0x015F9C64,
          "the 60 and 30 fps cases are unchanged");
    // A build whose code differs: nothing is patched.
    MapImage(nullptr);
    *(uint8_t*)0x00EDDC86 = 0x90;
    g_lastLog.clear();
    FixesApplyPatches();
    Check(*(uint32_t*)0x00EDD4B1 == CONST150 && g_lastLog.find("not what build 2364871 has") != std::string::npos,
          "a changed site patches nothing and says so");
    MapImage(nullptr);
    g_maxFps = 1000;
    FixesApplyPatches();
    Check(*(uint32_t*)0x00EDD4B1 == CONST150, "a setting outside 30..360 patches nothing");
    g_maxFps = 0;
}

void TestStageAndResources() {
    printf("current stage and the resource table\n");
    static uint8_t area[0x4000], now[0x800], resource[0x40D8 + 16384 * 4];
    memset(area, 0, sizeof area);
    memset(now, 0, sizeof now);
    memset(resource, 0, sizeof resource);
    *(uint32_t*)0x018D099C = (uint32_t)(uintptr_t)area;
    *(uint32_t*)(area + 0x3834) = (uint32_t)(uintptr_t)now;
    *(int*)(now + 0x724) = 443;
    int stage = -1;
    Check(!CurrentStage(&stage), "a stage that is not set up yet (flag +0x20 clear) is unknown");
    now[0x20] = 1;
    Check(CurrentStage(&stage) && stage == 443, "the stage number is read the way the game reads it (443)");
    int game = ((int(__cdecl*)())0x005BAF40)();              // DDDA.exe's own reader, on the same memory
    Check(game == 443, "DDDA.exe's own stage reader returns the same number");
    *(uint32_t*)(area + 0x3834) = 0x10;
    Check(!CurrentStage(&stage), "a stage pointer that is not memory: unknown, no fault");
    *(uint32_t*)0x018D099C = 0;
    uint32_t* table = (uint32_t*)(resource + 0x40D8);
    for (int i = 0; i < 16384; i += 3) table[i] = 0x1000 + i;
    *(uint32_t*)0x018D0AA0 = (uint32_t)(uintptr_t)resource;
    int used = 0, slots = 0;
    Check(ResourceTable(&used, &slots) && used == 5462 && slots == 16384, "resource table use counted (5462 of 16384)");
    *(uint32_t*)0x018D0AA0 = 0;
    Check(!ResourceTable(&used, &slots), "no resource manager yet: unknown");
}

// sRender::getShadowMapSize is a thiscall with one stack argument (the shadow type), ret 4.
static uint32_t CallShadowSize(void* render, int kind) {
    uint32_t result;
    __asm {
        mov ecx, render
        push kind
        mov eax, 0x00DA97D0
        call eax
        mov result, eax
    }
    return result;
}

void TestShadows() {
    printf("shadow map size\n");
    MapImage(nullptr);
    const uint32_t* table = (const uint32_t*)0x014292BC;
    static uint8_t render[0x100];
    *(uint32_t*)(render + 0xE8) = 2;                          // sRender's ShadowQuality: HIGH
    Check(CallShadowSize(render, 0) == 2048 && CallShadowSize(render, 1) == 1024,
          "vanilla: the game's own getShadowMapSize gives 2048 (sun) and 1024 (lamps) on HIGH");
    g_shadow = 4096;
    FixesApplyPatches();
    Check(table[0] == 512 && table[1] == 1024 && table[2] == 4096, "HIGH now 4096; LOW and MEDIUM unchanged");
    Check(CallShadowSize(render, 0) == 4096 && CallShadowSize(render, 1) == 2048,
          "patched: the game's own getShadowMapSize gives 4096 (sun) and 2048 (lamps)");
    MapImage(nullptr);
    *(uint32_t*)0x014292C4 = 2049;
    g_lastLog.clear();
    FixesApplyPatches();
    Check(table[2] == 2049 && g_lastLog.find("not what build 2364871 has") != std::string::npos,
          "a table that differs is left alone and the log says so");
    MapImage(nullptr);
    g_shadow = 5000;
    FixesApplyPatches();
    Check(table[2] == 2048, "a size that is not a multiple of 32 is refused");

    // shadow_buffers: FixesDeviceCreated bounds a raised sun map to what the GPU can make, once the device exists
    MapImage(nullptr);
    g_shadow = 8192;
    FixesApplyPatches();
    Check(table[2] == 8192, "sun shadow raised to 8192 at start-up, before any device");
    LONG sb = GuardHits(GUARD_SHADOW_BUFFERS);
    FixesDeviceCreated(16384, 16384);
    Check(table[2] == 8192 && GuardHits(GUARD_SHADOW_BUFFERS) == sb,
          "a GPU that can make 8192 leaves the raised sun map alone");
    FixesDeviceCreated(4096, 4096);
    Check(table[2] == 4096 && CallShadowSize(render, 0) == 4096 && CallShadowSize(render, 1) == 2048 &&
          GuardHits(GUARD_SHADOW_BUFFERS) == sb + 1,
          "a 4096-px GPU bounds the sun map to 4096 (lamps 2048) and counts a shadow_buffers hit");
    FixesDeviceCreated(2048, 2048);
    Check(table[2] == 2048, "a smaller GPU bounds the sun map again");
    // off: [guard] shadow_buffers = 0 leaves even an over-large map (the owner opted out)
    MapImage(nullptr);
    g_shadow = 8192;
    FixesApplyPatches();
    g_shadowbuffers = 0;
    sb = GuardHits(GUARD_SHADOW_BUFFERS);
    FixesDeviceCreated(4096, 4096);
    Check(table[2] == 8192 && GuardHits(GUARD_SHADOW_BUFFERS) == sb,
          "[guard] shadow_buffers = 0: the raised map is left for the owner to own");
    g_shadowbuffers = 1;
    g_shadow = 0;
}

void TestEnemySlots() {
    printf("enemy slots\n");
    static uint8_t manager[0x1B950 + 64 * 0x20];
    memset(manager, 0, sizeof manager);
    *(int*)(manager + 0x1B8D0) = 10;
    for (int i : {0, 3, 7}) {
        *(uint32_t*)(manager + 0x844 + i * 0x20) = 0x01562414;
        *(uint32_t*)(manager + 0x844 + i * 0x20 + 4) = 0x12345678;
    }
    *(uint32_t*)(manager + 0x844 + 5 * 0x20) = 0x01562414;   // an empty slot: no unit
    *(uint32_t*)0x018FA504 = (uint32_t)(uintptr_t)manager;
    int active = -1, usable = -1, slots = -1;
    BOOL ok = EnemySlots(&active, &usable, &slots);
    Check(ok && active == 3 && usable == 10 && slots == 10, "3 of 10 slots in use, 10 usable (the vanilla layout)");
    *(uint32_t*)0x018FA504 = 0;
    Check(!EnemySlots(&active, &usable, &slots), "no manager yet: unknown");
    *(uint32_t*)0x018FA504 = 0x10;
    Check(!EnemySlots(&active, &usable, &slots), "a manager pointer that is not memory: unknown, no fault");
    // A manager over several regions (pages of different protection, all readable, as an image's written and
    // untouched pages are): a loader before 0.4.0 read only the first region and called the pool unknown.
    const SIZE_T size = 0x1C000, page = 0x1000;
    uint8_t* split = (uint8_t*)VirtualAlloc(NULL, size, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (split) {
        memcpy(split, manager, 0x1B8D4);
        DWORD old = 0;
        VirtualProtect(split + 0x10000, page, PAGE_READONLY, &old);
        *(uint32_t*)0x018FA504 = (uint32_t)(uintptr_t)split;
        active = usable = slots = -1;
        ok = EnemySlots(&active, &usable, &slots);
        Check(ok && active == 3 && usable == 10 && slots == 10,
              "a manager spanning regions of different protection, all readable: 3 of 10 slots, 10 usable");
        VirtualProtect(split + 0x10000, page, PAGE_NOACCESS, &old);
        Check(!EnemySlots(&active, &usable, &slots), "a manager with a page that cannot be read: unknown, no fault");
        *(uint32_t*)0x018FA504 = 0;
        VirtualFree(split, 0, MEM_RELEASE);
    } else {
        Check(false, "VirtualAlloc for the split manager");
    }
}

// sResource::registTable (0x00DB9B70, thiscall, ret 8) and sResource::release (0x00DBA940, thiscall, ret 4).
static void CallRegist(void* manager, void* resource, uint32_t probe) {
    __asm {
        mov ecx, manager
        push probe
        push resource
        mov eax, 0x00DB9B70
        call eax
    }
}

static void CallRelease(void* manager, void* resource) {
    __asm {
        mov ecx, manager
        push resource
        mov eax, 0x00DBA940
        call eax
    }
}

// A resource's deleting destructor (vtable slot 0, thiscall with one argument): recorded, nothing freed.
static volatile LONG g_deleted = 0;
static uint32_t g_deleteFlag = 0;
static void* g_deletedWho = nullptr;
static void __fastcall FakeDeletingDtor(void* self, void*, uint32_t flag) {
    g_deletedWho = self;
    g_deleteFlag = flag;
    InterlockedIncrement(&g_deleted);
}

void TestResourceRelease() {
    printf("resources: the engine keeps none that no one uses (sResource::release)\n");
    MapImage(nullptr);
    static uint8_t manager[0x40D8 + 2048 * 32 + 0x100];      // sResource: its lock flag at +0x1C, the table at +0x40D8
    static uint8_t resource[0x100];
    static void* table[4] = {(void*)&FakeDeletingDtor, nullptr, nullptr, nullptr};
    memset(manager, 0, sizeof manager);
    memset(resource, 0, sizeof resource);
    *(uint8_t*)(uintptr_t)0x018D0BB0 = 0;                      // the global lock switch: off (no critical section)
    *(void**)resource = table;
    *(uint32_t*)(resource + 0x48) = 2;                         // two users
    *(uint32_t*)(resource + 0x58) = 0x12345678;                // its 64-bit id, which picks the bucket
    *(uint32_t*)(resource + 0x5C) = 0x9ABCDEF0;
    auto registered = [&]() {
        const uint32_t* slots = (const uint32_t*)(manager + 0x40D8);
        for (int i = 0; i < 2048 * 8; i++)
            if (slots[i] == (uint32_t)(uintptr_t)resource) return true;
        return false;
    };
    CallRegist(manager, resource, 0);
    Check(registered(), "DDDA.exe's own registTable puts a resource in sResource's table");
    CallRelease(manager, resource);
    Check(*(uint32_t*)(resource + 0x48) == 1 && g_deleted == 0 && registered(),
          "one of two users lets go (DDDA.exe's own release): the count drops to 1, the resource stays registered");
    CallRelease(manager, resource);
    Check(*(uint32_t*)(resource + 0x48) == 0 && g_deleted == 1 && g_deleteFlag == 1 && g_deletedWho == resource &&
              !registered(),
          "the last user lets go: release takes it out of the table and calls its deleting destructor at once "
          "(flag 1), so no resource no one uses is kept for a cache to flush");
}

volatile LONG g_destroys = 0;
LRESULT CALLBACK StandInWindow(HWND w, UINT msg, WPARAM wp, LPARAM lp) {
    if (msg == WM_DESTROY) InterlockedIncrement(&g_destroys);
    return DefWindowProcW(w, msg, wp, lp);
}

void TestExit() {
    printf("the game's exit: the quit flag and the exit request\n");
    MapImage(nullptr);
    g_exitSites = -1;
    Check(ExitSitesVerified(), "every site of exit_sites.h holds build 2364871's bytes");
    static uint8_t app[0x2700], sMain[0x100];
    memset(app, 0, sizeof app);
    memset(sMain, 0, sizeof sMain);
    *(uint32_t*)app = DDDA_SAPP_VTABLE;
    Check(GameQuitFlag() == -1, "no sApp yet: unknown");
    *(uint32_t*)(uintptr_t)DDDA_SAPP = (uint32_t)(uintptr_t)app;
    Check(GameQuitFlag() == 0, "sApp's quit flag reads clear");
    app[DDDA_SAPP_QUIT] = 1;
    Check(GameQuitFlag() == 1, "sApp's quit flag reads set (+0x266C)");
    *(uint32_t*)app = DDDA_SMAIN_VTABLE;
    Check(GameQuitFlag() == -1, "an object with another class's table is not sApp: unknown");
    *(uint32_t*)app = DDDA_SAPP_VTABLE;
    app[DDDA_SAPP_QUIT] = 0;
    *(uint32_t*)(uintptr_t)DDDA_SAPP = 0x10;
    Check(GameQuitFlag() == -1, "an sApp pointer that is not memory: unknown, no fault");

    // DDDA's own WM_CLOSE handler on a window of ours, standing in as sApp's window (+0x12C).
    WNDCLASSEXW wc = {sizeof wc};
    wc.lpfnWndProc = StandInWindow;
    wc.hInstance = GetModuleHandleW(L"engine_harness_core.dll");
    wc.lpszClassName = L"RiftstoneEngineHarness";
    RegisterClassExW(&wc);
    HWND w = CreateWindowExW(0, wc.lpszClassName, L"harness", 0, 0, 0, 64, 64, NULL, NULL, wc.hInstance, NULL);
    *(uint32_t*)(uintptr_t)DDDA_SAPP = (uint32_t)(uintptr_t)app;
    *(uint32_t*)(app + 0x12C) = (uint32_t)(uintptr_t)w;
    *(uint32_t*)sMain = DDDA_SMAIN_VTABLE;
    *(uint32_t*)(uintptr_t)DDDA_SMAIN = (uint32_t)(uintptr_t)sMain;
    *(void**)0x0139D350 = (void*)&SendMessageA;               // the import the exit request calls through
    Check(GameExitRequested() == 0, "sMain's exit request has not run (+0x34 clear)");
    LRESULT r = ((WNDPROC)(uintptr_t)0x00DF1550)(w, WM_CLOSE, 0, 0);
    Check(w && r == 0 && sMain[DDDA_SMAIN_EXIT] == 1 && g_destroys == 1,
          "DDDA's WM_CLOSE handler asked sMain, then its exit request marked sMain+0x34 and sent WM_DESTROY to "
          "sApp's window");
    Check(GameExitRequested() == 1, "GameExitRequested reads the mark");
    if (w) DestroyWindow(w);

    MapImage(nullptr);
    g_exitSites = -1;
    *(uint8_t*)0x0072C4B8 = 0x00;                              // Exit Game would write 0 instead of 1
    *(uint32_t*)(uintptr_t)DDDA_SAPP = (uint32_t)(uintptr_t)app;
    app[DDDA_SAPP_QUIT] = 1;
    g_lastLog.clear();
    Check(!ExitSitesVerified() && GameQuitFlag() == -1 && g_lastLog.find("not what build 2364871 has") != std::string::npos,
          "a changed site: neither flag is read, and the log says so");
    MapImage(nullptr);
    g_exitSites = -1;
}

// ---- ragdolls ---------------------------------------------------------------------------------------------
// A container the way the game reads one: body data at +0x38 (the count at +0x68 >> 8), the body list at +0x4C;
// a body holds its part at +0x18, and the two functions write the value into the part at +8 or +0xC.
struct FakeContainer {
    uint8_t obj[0x60];
    uint8_t data[0x80];
    uint8_t* list[4];
    uint8_t bodies[4][0x40];
    uint8_t parts[4][0x20];
    void Ready(int n) {
        memset(this, 0, sizeof *this);
        *(uint32_t*)(data + 0x68) = (uint32_t)n << 8;
        *(uint8_t**)(obj + 0x38) = data;
        *(uint8_t***)(obj + 0x4C) = list;
        for (int i = 0; i < 4; i++) {
            list[i] = bodies[i];
            *(uint8_t**)(bodies[i] + 0x18) = parts[i];
        }
    }
    void NotReady() {                           // made, its bodies not set up yet
        memset(this, 0, sizeof *this);
    }
    uint32_t Part(int i, int field) const { return *(const uint32_t*)(parts[i] + field); }
};
struct FakeOwner {
    uint8_t obj[0x40];
    FakeContainer ragdoll, set;
    void Wire(bool withRagdoll, bool withSet) {
        memset(obj, 0, sizeof obj);
        *(uint8_t**)(obj + 0x24) = withRagdoll ? ragdoll.obj : nullptr;
        *(uint8_t**)(obj + 0x20) = withSet ? set.obj : nullptr;
    }
};

// The two functions take the object in edi and the value on the stack (ret 4); eax is what they leave.
__declspec(noinline) static uint32_t CallSetBodies(uintptr_t fn, void* self, uint32_t value) {
    uint32_t result;
    __asm {
        push edi
        mov edi, self
        push value
        mov eax, fn
        call eax
        pop edi
        mov result, eax
    }
    return result;
}
static int FaultFilter(EXCEPTION_POINTERS* e, uintptr_t* where) {
    *where = (uintptr_t)e->ExceptionRecord->ExceptionAddress;
    return e->ExceptionRecord->ExceptionCode == EXCEPTION_ACCESS_VIOLATION ? EXCEPTION_EXECUTE_HANDLER
                                                                           : EXCEPTION_CONTINUE_SEARCH;
}
static bool Faults(uintptr_t fn, void* self, uint32_t value, uintptr_t* where) {
    *where = 0;
    __try {
        CallSetBodies(fn, self, value);
        return false;
    } __except (FaultFilter(GetExceptionInformation(), where)) {
        return true;
    }
}

// The two inline walks: enter the check with eax = the container, and see which way it went.
static volatile int g_walkPath = 0;
__declspec(naked) static void WalkOn() {
    __asm {
        mov g_walkPath, 1
        ret
    }
}
__declspec(naked) static void WalkOff() {
    __asm {
        mov g_walkPath, 2
        ret
    }
}
__declspec(noinline) static int RunWalk(void* check, void* container, uint32_t* stored) {
    uint32_t kept = 0;
    g_walkPath = 0;
    __asm {
        push esi
        sub esp, 0x10                         // the second walk keeps the container at [esp + 0x0C] (its frame)
        mov dword ptr [esp + 8], 0
        mov eax, container
        call check
        mov eax, [esp + 8]
        mov kept, eax
        add esp, 0x10
        pop esi
    }
    *stored = kept;
    return g_walkPath;
}

void TestRagdolls() {
    printf("ragdolls (0x00794930, 0x007949E0 and two inline walks)\n");
    MapImage(nullptr);
    static FakeOwner o;
    const uint32_t VALUE = 7, FLAGS = 0x40000014;

    // the game's own code, before the guard: what it writes, what it leaves in eax, and where it stops
    o.ragdoll.Ready(3), o.set.Ready(3), o.Wire(true, true);
    uint32_t v8 = CallSetBodies(0x00794930, o.obj, VALUE), vC = CallSetBodies(0x007949E0, o.obj, FLAGS);
    bool vanillaWrote = true;
    for (int i = 0; i < 3; i++)
        vanillaWrote &= o.ragdoll.Part(i, 8) == VALUE && o.set.Part(i, 8) == VALUE && o.ragdoll.Part(i, 0x0C) == FLAGS &&
                        o.set.Part(i, 0x0C) == FLAGS;
    Check(vanillaWrote && v8 == 3 && vC == 3,
          "vanilla: every body of the ragdoll and of its set gets the value (+8, +0xC); eax = the set's count (3)");
    o.ragdoll.Ready(2), o.Wire(true, false);
    uint32_t vNoSet = CallSetBodies(0x00794930, o.obj, VALUE);
    Check(vNoSet == (uint32_t)(uintptr_t)o.ragdoll.obj && o.ragdoll.Part(1, 8) == VALUE,
          "vanilla: no set: the ragdoll's bodies get it and eax is the ragdoll container");
    uintptr_t where = 0;
    o.ragdoll.NotReady(), o.set.Ready(3), o.Wire(true, true);
    Check(Faults(0x00794930, o.obj, VALUE, &where) && where == 0x00794942,
          "vanilla: a ragdoll whose bodies are not set up yet stops the game at 0x00794942 (the crash of 2026-09-27)");
    MapImage(nullptr);                                         // the fault left the function half run

    g_ragdolls = 1;
    FixesApplyPatches();
    const uint8_t* s1 = (const uint8_t*)0x00794930;
    const uint8_t* s3 = (const uint8_t*)0x008CF2D8;
    const uint8_t* s4 = (const uint8_t*)0x00C2BAE0;
    Check(g_ragdollGuard && s1[0] == 0xE9 && *(const uint8_t*)0x007949E0 == 0xE9 && s3[0] == 0xE9 && s4[0] == 0xE9 &&
              s3[13] == 0x90 && s4[17] == 0x90 && *(const uint8_t*)0x008CF2E6 == 0x85,
          "the guard is on: a jump at each of the four sites, the rest of each inline read filled, the loop after it kept");

    // the same cases through the guard: the same writes and the same eax where the game did not stop
    o.ragdoll.Ready(3), o.set.Ready(3), o.Wire(true, true);
    uint32_t p8 = CallSetBodies(0x00794930, o.obj, VALUE), pC = CallSetBodies(0x007949E0, o.obj, FLAGS);
    bool patchedWrote = true;
    for (int i = 0; i < 3; i++)
        patchedWrote &= o.ragdoll.Part(i, 8) == VALUE && o.set.Part(i, 8) == VALUE && o.ragdoll.Part(i, 0x0C) == FLAGS &&
                        o.set.Part(i, 0x0C) == FLAGS;
    Check(patchedWrote && p8 == v8 && pC == vC && o.ragdoll.Part(3, 8) == 0,
          "guarded: the same bodies get the same values and eax is the same; the fourth, past the count, untouched");
    o.ragdoll.Ready(2), o.Wire(true, false);
    Check(CallSetBodies(0x00794930, o.obj, VALUE) == vNoSet && o.ragdoll.Part(1, 8) == VALUE,
          "guarded: no set: the same as the game");
    o.ragdoll.NotReady(), o.set.Ready(3), o.Wire(true, true);
    bool faulted = Faults(0x00794930, o.obj, VALUE, &where);
    uint32_t r = faulted ? 0 : CallSetBodies(0x007949E0, o.obj, FLAGS);
    Check(!faulted && r == 3 && o.set.Part(2, 8) == VALUE && o.set.Part(2, 0x0C) == FLAGS && o.ragdoll.Part(0, 8) == 0,
          "guarded: the ragdoll not set up yet has no bodies; the set's still get the value, and nothing stops");
    o.ragdoll.NotReady(), o.set.NotReady(), o.Wire(true, true);
    Check(!Faults(0x00794930, o.obj, VALUE, &where) && CallSetBodies(0x00794930, o.obj, VALUE) == 0,
          "guarded: neither set up: nothing to walk, eax 0 (the set's count, as the game's accessor gives it)");

    // the inline walks, their ways out pointed here
    DWORD_PTR on1 = g_ragdollOn1, off1 = g_ragdollOff1, on2 = g_ragdollOn2, off2 = g_ragdollOff2;
    g_ragdollOn1 = g_ragdollOn2 = (DWORD_PTR)WalkOn;
    g_ragdollOff1 = g_ragdollOff2 = (DWORD_PTR)WalkOff;
    uint32_t kept = 0;
    static FakeContainer c;
    c.Ready(3);
    bool ready = RunWalk((void*)RagdollWalk1, c.obj, &kept) == 1 && RunWalk((void*)RagdollWalk2, c.obj, &kept) == 1 &&
                 kept == (uint32_t)(uintptr_t)c.obj;
    c.Ready(0);
    bool empty = RunWalk((void*)RagdollWalk1, c.obj, &kept) == 2 && RunWalk((void*)RagdollWalk2, c.obj, &kept) == 2;
    c.NotReady();
    bool missing = RunWalk((void*)RagdollWalk1, c.obj, &kept) == 2 && RunWalk((void*)RagdollWalk2, c.obj, &kept) == 2 &&
                   kept == (uint32_t)(uintptr_t)c.obj;
    c.Ready(3);
    *(uint8_t***)(c.obj + 0x4C) = nullptr;
    bool noList = RunWalk((void*)RagdollWalk1, c.obj, &kept) == 2 && RunWalk((void*)RagdollWalk2, c.obj, &kept) == 2;
    g_ragdollOn1 = on1, g_ragdollOff1 = off1, g_ragdollOn2 = on2, g_ragdollOff2 = off2;
    Check(ready && empty && missing && noList,
          "the inline walks: a set-up ragdoll is walked (the second keeps it at [esp+0xC] as the game does); none "
          "without bodies, without its data or without its list");

    MapImage(nullptr);
    g_ragdolls = 0;
    FixesApplyPatches();
    Check(!g_ragdollGuard && *(const uint8_t*)0x00794930 == 0x8B, "[guard] ragdoll_bodies = 0: nothing patched");
    MapImage(nullptr);
    g_ragdolls = 1;
    *(uint8_t*)0x00C2BAEB = 0xFE;                              // one byte of the last inline read differs
    g_lastLog.clear();
    FixesApplyPatches();
    Check(!g_ragdollGuard && *(const uint8_t*)0x00794930 == 0x8B && g_lastLog.find("not what build 2364871 has") !=
              std::string::npos,
          "a site that differs: nothing patched at any site, and the log says so");
    MapImage(nullptr);
    g_ragdolls = 0;
}

// ---- the ragdoll body-count family --------------------------------------------------------------------------
// The inline copies tools/ragdoll_sites.py finds in the exe (FAMILY_SITES, ragdoll_sites.inc).  Three proofs:
// the stub emitter's paths run on a fake body-data pointer for each shape, every real site is patched to a
// stub that carries its own bytes, and three of the game's own walks run on fake ragdolls with and without it.
static volatile LONG g_familyHits = 0;
extern "C" void __stdcall TestFamilyHit(DWORD) { InterlockedIncrement(&g_familyHits); }
static volatile uint32_t g_landZF = 0, g_landReg = 0;
__declspec(naked) static void LandT() {
    __asm {
        mov g_landZF, 0
        jnz done
        mov g_landZF, 1
    done:
        ret
    }
}
__declspec(naked) static void LandC() {
    __asm {
        mov g_landReg, esi
        ret
    }
}
// Enter a stub with ecx = the (fake) body-data pointer, every other register kept.
__declspec(noinline) static void RunFamilyStub(void* stub, void* bodydata) {
    __asm {
        pushad
        mov ecx, bodydata
        mov eax, stub
        call eax
        popad
    }
}
// The 'M' shape: ecx = the body data, ebp = the mask the game keeps there, esi = what the stub's copy of the
// game's `xor esi, esi` must clear (returned).  Naked: ebp is not this function's frame while the stub runs.
static volatile uint32_t g_maskEsi = 0;
__declspec(naked) static uint32_t __stdcall RunMaskStub(void* /*stub*/, void* /*bodydata*/) {
    __asm {
        pushad
        mov eax, [esp + 0x24]
        mov ecx, [esp + 0x28]
        mov ebp, 0xFFFFFF00
        mov esi, 0x5A5A5A5A
        call eax
        mov g_maskEsi, esi
        popad
        mov eax, g_maskEsi
        ret 8
    }
}

// Three of the game's own walks, run on fake ragdolls (FakeOwner: the set at +0x20, the ragdoll at +0x24).
// 0x00794A90 (edi = the owner, one byte on the stack; ret 4): the dying enemy's walk the crash of 2026-10-06
// stopped in, called from the death action (cEmActActingDie) at 0x008A689A.
static bool FaultsCall(void (*run)(void*, uint32_t), void* owner, uint32_t arg, uintptr_t* where) {
    *where = 0;
    __try {
        run(owner, arg);
        return false;
    } __except (FaultFilter(GetExceptionInformation(), where)) {
        return true;
    }
}
static void RunDieWalk(void* owner, uint32_t flag) { CallSetBodies(0x00794A90, owner, flag); }
// 0x00794AF0 (the owner on the stack, the value's switch in dl; ret 4): every body's part (+0x2C) gets 0 or -1.
static void RunPartWalk(void* owner, uint32_t flag) {
    __asm {
        push owner
        mov edx, flag
        mov eax, 0x00794AF0
        call eax
    }
}
// 0x007943B0 (the owner and two more on the stack; ret 0xC): the set's and the ragdoll's bodies, two walks each,
// with 0xFFFFFF00 kept in ebp ('M' sites); it calls 0x0088D660 and 0x0088D2D0 first, each a 'T' site.
static void RunMaskWalk(void* owner, uint32_t) {
    __asm {
        push 0x3F800000
        push 0
        push owner
        mov eax, 0x007943B0
        call eax
    }
}

void TestRagdollFamily() {
    printf("ragdoll body-count family (%d scanned sites)\n", (int)_countof(FAMILY_SITES));

    // the emitter's two paths, on a fake body-data pointer (count at +0x68 >> 8), for both shapes
    BYTE* cave = (BYTE*)VirtualAlloc(nullptr, 256, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    static uint8_t bd[0x80];
    const BYTE origT[7] = {0xF7, 0x41, 0x68, 0x00, 0xFF, 0xFF, 0xFF};        // test [ecx+0x68], 0xFFFFFF00
    const BYTE origC[6] = {0x8B, 0x71, 0x68, 0xC1, 0xEE, 0x08};             // mov esi,[ecx+0x68]; shr esi,8
    BYTE* cur = cave;
    const BYTE origM[5] = {0x33, 0xF6, 0x85, 0x69, 0x68};                   // xor esi,esi; test [ecx+0x68],ebp
    BYTE* stubT = EmitFamilyStub(cur, 'T', origT, 7, 0, 0xABCD, (DWORD_PTR)LandT, (void*)TestFamilyHit);
    BYTE* stubC = EmitFamilyStub(cur, 'C', origC, 6, 0, 0xABCD, (DWORD_PTR)LandC, (void*)TestFamilyHit);
    BYTE* stubM = EmitFamilyStub(cur, 'M', origM, 5, 2, 0xABCD, (DWORD_PTR)LandT, (void*)TestFamilyHit);
    FlushInstructionCache(GetCurrentProcess(), nullptr, 0);

    g_familyHits = 0;
    *(uint32_t*)(bd + 0x68) = 3u << 8;
    RunFamilyStub(stubT, bd);
    bool tReady = g_landZF == 0 && g_familyHits == 0;                        // bodies present: real test, ZF clear
    *(uint32_t*)(bd + 0x68) = 0;
    RunFamilyStub(stubT, bd);
    bool tZero = g_landZF == 1 && g_familyHits == 0;                         // present but empty: real test, ZF set, no hit
    RunFamilyStub(stubT, nullptr);
    bool tNull = g_landZF == 1 && g_familyHits == 1;                         // not set up: guard sets ZF and counts
    Check(tReady && tZero && tNull,
          "'T' stub: real test when body data is present (empty reads as 0), and the game's ZF=count-0 answer when null");

    g_familyHits = 0;
    *(uint32_t*)(bd + 0x68) = 5u << 8;
    g_landReg = 0xDEAD;
    RunFamilyStub(stubC, bd);
    bool cReady = g_landReg == 5 && g_familyHits == 0;                       // bodies present: the real count
    g_landReg = 0xDEAD;
    RunFamilyStub(stubC, nullptr);
    bool cNull = g_landReg == 0 && g_familyHits == 1;                        // not set up: count 0, counted
    Check(cReady && cNull, "'C' stub: the real count when present, 0 when the body data is null, counted once");

    // 'M': the mask is in ebp, and the stub first runs the instruction before the read (xor esi, esi)
    g_familyHits = 0;
    *(uint32_t*)(bd + 0x68) = 3u << 8;
    bool mReady = RunMaskStub(stubM, bd) == 0 && g_landZF == 0 && g_familyHits == 0;
    *(uint32_t*)(bd + 0x68) = 0x7F;                                          // flag bits only: no bodies
    bool mZero = RunMaskStub(stubM, bd) == 0 && g_landZF == 1 && g_familyHits == 0;
    bool mNull = RunMaskStub(stubM, nullptr) == 0 && g_landZF == 1 && g_familyHits == 1;
    Check(mReady && mZero && mNull,
          "'M' stub: runs the instruction before the read (esi zeroed), then the masked test or, null, ZF=count-0");
    VirtualFree(cave, 0, MEM_RELEASE);

    // every real site patched to a stub that carries its own (verified) bytes
    MapImage(nullptr);
    g_ragdolls = 1;
    FixesApplyPatches();
    Check(g_ragFamilyCount == (int)_countof(FAMILY_SITES) && g_ragFamilySkipped == 0,
          "all family sites verified against build 2364871 and guarded");
    bool patched = true, carries = true, shapes = true, crash0927 = false, crash1006 = false;
    int kinds[3] = {0, 0, 0};
    for (const FamilySite& s : FAMILY_SITES) {
        const BYTE* at = (const BYTE*)s.va;
        int len = s.len, pre = s.pre;
        shapes &= (s.kind == 'T' && len == 7 && pre == 0) || (s.kind == 'C' && len == 6 && pre == 0) ||
                  (s.kind == 'M' && len == pre + 3 && len >= 5 && s.bytes[pre] == 0x85);
        kinds[s.kind == 'T' ? 0 : s.kind == 'C' ? 1 : 2]++;
        if (at[0] != 0xE9) { patched = false; continue; }
        for (int k = 5; k < len; k++) patched &= at[k] == 0x90;
        const BYTE* stub = at + 5 + *(const int32_t*)(at + 1);
        carries &= memcmp(stub, s.bytes, pre) == 0 &&                       // the stub's copy of the game's bytes
                   memcmp(stub + pre + 4, s.bytes + pre, len - pre) == 0;
        if (s.va == 0x007945B4) crash0927 = true;
        if (s.va == 0x00794AA2) crash1006 = true;
    }
    Check(shapes && kinds[0] == 48 && kinds[1] == 10 && kinds[2] == 4,
          "the table holds 48 'T', 10 'C' and 4 'M' sites, each the length its shape takes");
    Check(patched && carries, "each site jumps to a stub that keeps its own instruction bytes; the reads are filled");
    Check(crash0927 && crash1006,
          "the live crashes of 2026-09-27 (0x007945B4) and 2026-10-06 (0x00794AA2, Devil's Firegrove) are guarded sites");

    // the game's own walks on fake ragdolls, guarded (the image is patched here)
    static FakeOwner w;
    uintptr_t where = 0;
    LONG before = GuardHits(GUARD_RAGDOLL_BODIES);
    w.ragdoll.NotReady(), w.set.NotReady(), w.Wire(true, true);
    bool dieNull = !FaultsCall(RunDieWalk, w.obj, 1, &where) && GuardHits(GUARD_RAGDOLL_BODIES) == before + 1;
    w.ragdoll.Ready(3), w.Wire(true, true);
    for (int i = 0; i < 4; i++) w.ragdoll.list[i] = nullptr;               // bodies listed empty: walked, nothing called
    bool dieReady = !FaultsCall(RunDieWalk, w.obj, 1, &where) && GuardHits(GUARD_RAGDOLL_BODIES) == before + 1;
    Check(dieNull && dieReady,
          "guarded: 0x00794A90 on a dying enemy whose ragdoll has no bodies yet walks none (counted); a set-up one is walked");

    before = GuardHits(GUARD_RAGDOLL_BODIES);
    w.ragdoll.Ready(3), w.Wire(true, true);
    for (int i = 0; i < 4; i++) *(uint8_t**)(w.ragdoll.bodies[i] + 0x2C) = w.ragdoll.parts[i];
    bool partReady = !FaultsCall(RunPartWalk, w.obj, 1, &where) && w.ragdoll.Part(0, 8) == 0xFFFFFFFF &&
                     w.ragdoll.Part(2, 0x1C) == 0xFFFFFFFF && w.ragdoll.Part(3, 8) == 0;
    w.ragdoll.NotReady(), w.Wire(true, true);
    bool partNull = !FaultsCall(RunPartWalk, w.obj, 1, &where) && w.ragdoll.Part(0, 8) == 0 &&
                    GuardHits(GUARD_RAGDOLL_BODIES) == before + 1;
    Check(partReady && partNull,
          "guarded: 0x00794AF0 sets every body's part of a set-up ragdoll (3 of 3) and none of one not set up (counted)");

    before = GuardHits(GUARD_RAGDOLL_BODIES);
    w.ragdoll.NotReady(), w.set.NotReady(), w.Wire(true, true);
    bool maskNull = !FaultsCall(RunMaskWalk, w.obj, 0, &where) && GuardHits(GUARD_RAGDOLL_BODIES) == before + 6;
    w.ragdoll.Ready(0), w.set.Ready(0), w.Wire(true, true);
    bool maskEmpty = !FaultsCall(RunMaskWalk, w.obj, 0, &where) && GuardHits(GUARD_RAGDOLL_BODIES) == before + 6;
    Check(maskNull && maskEmpty,
          "guarded: 0x007943B0 with the set and the ragdoll not set up passes its four 'M' and two 'T' reads (6 counted)");

    // and the same walks without the guard: the crash
    MapImage(nullptr);
    g_ragdolls = 0;
    FixesApplyPatches();
    w.ragdoll.NotReady(), w.set.NotReady(), w.Wire(true, true);
    bool dieFaults = FaultsCall(RunDieWalk, w.obj, 1, &where) && where == 0x00794AA2;
    bool partFaults = FaultsCall(RunPartWalk, w.obj, 1, &where) && where == 0x00794B01;
    bool maskFaults = FaultsCall(RunMaskWalk, w.obj, 0, &where) && where == 0x0088D665;
    Check(dieFaults && partFaults && maskFaults,
          "vanilla: the same ragdolls stop the game at 0x00794AA2 (the crash of 2026-10-06), 0x00794B01 and 0x0088D665");

    // the 'M' sites are reached with no body data only once the 'T' reads in 0x0088D660 / 0x0088D2D0 before them
    // are guarded: everything guarded but the four 'M' sites' game bytes put back, the walk stops at the first
    MapImage(nullptr);
    g_ragdolls = 1;
    FixesApplyPatches();
    for (const FamilySite& s : FAMILY_SITES) {
        if (s.kind != 'M') continue;
        DWORD old;
        VirtualProtect((void*)s.va, s.len, PAGE_EXECUTE_READWRITE, &old);
        memcpy((void*)s.va, s.bytes, s.len);
        VirtualProtect((void*)s.va, s.len, old, &old);
    }
    FlushInstructionCache(GetCurrentProcess(), nullptr, 0);
    w.ragdoll.NotReady(), w.set.NotReady(), w.Wire(true, true);
    Check(FaultsCall(RunMaskWalk, w.obj, 0, &where) && where == 0x007943D3,
          "without its 'M' sites, 0x007943B0 gets past the guarded 'T' reads and stops at 0x007943D3");

    // a site that differs is left alone; the rest still go in
    MapImage(nullptr);
    *(uint8_t*)0x007945B4 = 0xFE;
    g_lastLog.clear();
    FixesApplyPatches();
    Check(g_ragFamilyCount == (int)_countof(FAMILY_SITES) - 1 && g_ragFamilySkipped == 1 &&
              *(const uint8_t*)0x007945B4 == 0xFE && g_lastLog.find("not what build 2364871 has") != std::string::npos,
          "a family site that differs is skipped (unpatched) while the other sites are still guarded");

    MapImage(nullptr);
    g_ragdolls = 0;
    FixesApplyPatches();
    Check(g_ragFamilyCount == 0 && *(const uint8_t*)0x007945B4 == 0xF7,
          "[guard] ragdoll_bodies = 0: no family site patched");
    MapImage(nullptr);
    g_ragdolls = 0;
}

// ---- the map's GUI text field (the null-string crash of 2026-09-27 22:56) ---------------------------------
// The guard replaces `mov ecx,eax; lea esi,[ecx+1]` at 0x00606621 with a thunk: a null string becomes the
// empty string; a non-null one is unchanged. Both paths continue at 0x00606626 (g_guiBack). We point g_guiBack
// at a landing that records ecx/esi, so the thunk's two paths can be run without the rest of 0x006065E0.
static volatile uint32_t g_guiEcx = 0, g_guiEsi = 0;
__declspec(naked) static void LandGui() {
    __asm {
        mov g_guiEcx, ecx
        mov g_guiEsi, esi
        ret
    }
}
__declspec(noinline) static void RunGuiThunk(void* stringVal) {
    __asm {
        push esi
        push ecx
        mov eax, stringVal
        call GuiTextThunk
        pop ecx
        pop esi
    }
}

void TestGuiText() {
    printf("map GUI text field (0x006065E0's null-string crash)\n");
    MapImage(nullptr);
    g_guitext = 1;
    FixesApplyPatches();
    Check(g_guiTextGuard && *(const uint8_t*)0x00606621 == 0xE9, "the guard is on: a jump at 0x00606621");

    DWORD_PTR back = g_guiBack;
    g_guiBack = (DWORD_PTR)LandGui;                // catch the thunk instead of running the rest of the function
    static char text[4] = "abc";
    LONG before = GuardHits(GUARD_GUI_TEXT);
    RunGuiThunk(text);
    bool ok = g_guiEcx == (uint32_t)(uintptr_t)text && g_guiEsi == (uint32_t)(uintptr_t)text + 1 &&
              GuardHits(GUARD_GUI_TEXT) == before;
    RunGuiThunk(nullptr);
    bool nul = g_guiEcx == (uint32_t)(uintptr_t)&g_guiEmpty && g_guiEsi == (uint32_t)(uintptr_t)&g_guiEmpty + 1 &&
               GuardHits(GUARD_GUI_TEXT) == before + 1 && g_guiEmpty[0] == 0;
    g_guiBack = back;
    Check(ok && nul,
          "a real string is walked unchanged; a null string becomes the empty string (the field shows nothing) and "
          "is counted once");

    MapImage(nullptr);
    *(uint8_t*)0x00606621 = 0x90;
    g_lastLog.clear();
    FixesApplyPatches();
    Check(!g_guiTextGuard && *(const uint8_t*)0x00606621 == 0x90 &&
              g_lastLog.find("not what build 2364871 has") != std::string::npos,
          "a site that differs: nothing patched, and the log says so");

    MapImage(nullptr);
    g_guitext = 0;
    FixesApplyPatches();
    Check(!g_guiTextGuard && *(const uint8_t*)0x00606621 == 0x8B, "[guard] gui_text = 0: nothing patched");
    MapImage(nullptr);
    g_guitext = 0;
}

// ---- the foliage/water/effects streaming window (3x3 -> 5x5) ---------------------------------------------
void TestStreamWindow() {
    printf("foliage streaming window ([render] wide_foliage)\n");
    MapImage(nullptr);
    g_widefoliage = 1;
    FixesApplyPatches();
    bool patched = g_wideFoliage;
    for (const StreamSite& s : STREAM_SITES) patched &= *(const DWORD*)(s.va + 2) == s.newDisp;
    Check(patched, "on: the four foliage/water/effect updaters read the 5x5 model counts (+0x7F0/+0x7F4)");
    bool lotKept = *(const DWORD*)(0x00C60542 + 2) == 0x800;
    Check(lotKept, "enemies and objects (updateLot) keep the 3x3 layout counts (+0x800)");

    MapImage(nullptr);
    *(uint8_t*)0x00C613FE = 0x90;
    g_lastLog.clear();
    FixesApplyPatches();
    Check(!g_wideFoliage && *(const DWORD*)(0x00C5E776 + 2) == 0x800 &&
              g_lastLog.find("is not what build 2364871 has") != std::string::npos,
          "a site that differs: the window stays 3x3 at every site, and the log says so");

    MapImage(nullptr);
    g_widefoliage = 0;
    FixesApplyPatches();
    Check(!g_wideFoliage && *(const DWORD*)(0x00C5E776 + 2) == 0x800, "[render] wide_foliage = 0: the window stays 3x3");
    MapImage(nullptr);
    g_widefoliage = 0;
}

// ---- broken loose textures ([guard] broken_textures) ----------------------------------------------------
static void WriteFileBytes(const wchar_t* path, const void* data, DWORD n) {
    HANDLE h = CreateFileW(path, GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    DWORD w = 0;
    if (h != INVALID_HANDLE_VALUE) { WriteFile(h, data, n, &w, nullptr); CloseHandle(h); }
}

void TestBrokenTextures() {
    printf("broken loose textures (TextureFileBroken)\n");
    wchar_t dir[MAX_PATH], path[MAX_PATH];
    GetTempPathW(_countof(dir), dir);
    _snwprintf_s(path, _countof(path), _TRUNCATE, L"%srs-tex-test.tex", dir);
    wchar_t why[160];

    // a valid 4x4 BC1 texture with 3 mips (52 bytes): revision 0x99, offsets 28/36/44 all inside the file
    uint32_t words[4] = {0x00584554u, 0x20000099u, 3u | (4u << 6) | (4u << 19), 1u | (20u << 8) | (1u << 16)};
    uint32_t offs[3] = {28, 36, 44};
    uint8_t valid[52];
    memcpy(valid, words, 16);
    memcpy(valid + 16, offs, 12);
    memset(valid + 28, 0x10, 24);
    WriteFileBytes(path, valid, sizeof valid);
    Check(!TextureFileBroken(path, why, _countof(why)), "a valid .tex is not called broken");

    WriteFileBytes(path, valid, 40);                            // cut so mip offset 44 is past the end
    Check(TextureFileBroken(path, why, _countof(why)), "a truncated .tex (a mip past the end) is broken");

    uint8_t badrev[52];
    memcpy(badrev, valid, 52);
    *(uint32_t*)(badrev + 4) = 0x20000055u;                     // revision 0x055, not 0x099/0x09D
    WriteFileBytes(path, badrev, 52);
    Check(TextureFileBroken(path, why, _countof(why)), "an unknown revision is broken");

    uint8_t garbage[52];
    memset(garbage, 0xAB, sizeof garbage);
    WriteFileBytes(path, garbage, sizeof garbage);
    Check(TextureFileBroken(path, why, _countof(why)), "a file with the wrong magic is broken");

    uint8_t tiny[8] = {0x54, 0x45, 0x58, 0x00, 0, 0, 0, 0};
    WriteFileBytes(path, tiny, sizeof tiny);
    Check(TextureFileBroken(path, why, _countof(why)), "a file too short for a header is broken");

    // a cube map (shape 0x60000, six faces): its mip offsets are not one flat run, so they are not range-checked;
    // the old check flagged valid cubemaps (DefaultCube_CM.tex in game) and swapped the reflections for the stand-in
    uint32_t cube[4] = {0x00584554u, 0x60000099u, 1u | (4u << 6) | (4u << 19), 6u};
    uint32_t coffs[6] = {28, 36, 44, 52, 60, 68};       // later faces sit past this deliberately tiny file
    uint8_t cubebuf[40];
    memcpy(cubebuf, cube, 16);
    memcpy(cubebuf + 16, coffs, 24);
    WriteFileBytes(path, cubebuf, sizeof cubebuf);
    Check(!TextureFileBroken(path, why, _countof(why)), "a cube map is not flagged broken (its faces lay out differently)");

    DeleteFileW(path);
    Check(!TextureFileBroken(path, why, _countof(why)), "a file that cannot be opened is not called broken (missing is handled elsewhere)");
}

// ---- the effect-system dispatch guard (crash 0x010CBFA4) --------------------------------------------------
// The game calls 0x010CBFA0 with a1/a2/a3 on the stack and ecx = [a2+4] (both callers set it).  Run the real
// function that way, so the reimplementation can be checked against it for every tag.
__declspec(noinline) static uint32_t CallOrigDispatch(uintptr_t fn, void* a1, void* a2, void* a3) {
    uint32_t r;
    __asm {
        mov ecx, a2
        mov ecx, [ecx + 4]
        push a3
        push a2
        push a1
        mov eax, fn
        call eax
        mov r, eax
    }
    return r;
}

void TestParticleGuard() {
    printf("effect dispatch guard (0x010CBFA4)\n");
    MapImage(nullptr);
    static uint8_t a2[0x100], ecxobj[0x200], pobj[0x200], a3[0x40], gobj[0x100], a1[8];
    memset(a2, 0, sizeof a2); memset(ecxobj, 0, sizeof ecxobj); memset(pobj, 0, sizeof pobj);
    memset(a3, 0, sizeof a3); memset(gobj, 0, sizeof gobj); memset(a1, 0, sizeof a1);
    *(void**)(a2 + 4) = ecxobj;                          // ecx = [a2+4]
    *(uint32_t*)(a2 + 0x88) = 0x1111;                    // tag 1
    *(uint32_t*)(ecxobj + 0x10C) = 0x2222;               // tag 2 (and tag 3 with no sub-pointer)
    *(void**)(ecxobj + 0x1D0) = pobj;                    // tag 3 sub-pointer
    *(uint32_t*)(pobj + 0x10C) = 0x3333;                 // tag 3 via the sub-pointer
    *(uint32_t*)(a3 + 0x14) = 0x4444;                    // default
    *(uint32_t*)(gobj + 0x74) = 0x5555;                  // tag 4
    DWORD old;
    if (VirtualProtect((void*)0x018D2818, 4, PAGE_READWRITE, &old)) {   // the game global tag 4 reads
        *(void**)0x018D2818 = gobj;
        VirtualProtect((void*)0x018D2818, 4, old, &old);
    }
    bool eq = true;
    for (int t = 0; t < 8; t++) {
        a1[3] = (uint8_t)t;
        eq &= CallOrigDispatch(0x010CBFA0, a1, a2, a3) == EffectDispatchImpl(a1, a2, a3);
    }
    Check(eq, "the reimplementation matches the game's dispatch for every tag");
    *(void**)(ecxobj + 0x1D0) = nullptr;                 // tag 3 with a null sub-pointer
    a1[3] = 3;
    Check(CallOrigDispatch(0x010CBFA0, a1, a2, a3) == EffectDispatchImpl(a1, a2, a3), "tag 3 with a null sub-pointer matches too");
    *(void**)(ecxobj + 0x1D0) = pobj;

    g_particles = 1;
    FixesApplyPatches();
    Check(g_particleGuard && *(const uint8_t*)0x010CBFA0 == 0xE9, "the guard is on: a jump at 0x010CBFA0");
    LONG before = GuardHits(GUARD_PARTICLES);
    a1[3] = 1;
    bool guarded = EffectDispatchGuarded((void*)0x4, a2, a3) == 0x4444 && GuardHits(GUARD_PARTICLES) == before + 1;
    Check(guarded, "a fault reading the parameter block is contained: the game's default is returned, and counted");
    Check(EffectDispatchGuarded(a1, a2, a3) == 0x1111, "a valid parameter block still dispatches (tag 1)");

    MapImage(nullptr);
    *(uint8_t*)0x010CBFA0 = 0x90;
    g_lastLog.clear();
    FixesApplyPatches();
    Check(!g_particleGuard && *(const uint8_t*)0x010CBFA0 == 0x90 &&
              g_lastLog.find("not what build 2364871 has") != std::string::npos,
          "a site that differs: nothing patched, and the log says so");
    MapImage(nullptr);
    g_particles = 0;
    FixesApplyPatches();
    Check(!g_particleGuard && *(const uint8_t*)0x010CBFA0 == 0x8B, "[guard] particles = 0: nothing patched");
    MapImage(nullptr);
    g_particles = 0;
}
}  // namespace

static int Run(int argc, wchar_t** argv) {
    if (argc < 3) {
        printf("usage: DDDA.exe <real DDDA.exe> <classes.txt>\n");
        return 1;
    }
    static const wchar_t* exePath = argv[1];
    if (!MapImage(exePath)) return 2;
    DetectGame();
    Check(g_game == GAME_DDDA && g_knownBuild, "game detection: DDDA.exe, build 2364871 (PE time stamp)");
    Check(g_largeAddressAware, "DDDA.exe is large-address aware (the flag the loader reads from the mapped header)");
    TestClassNames(ReadCases(argv[2]));
    TestFps();
    TestShadows();
    TestStageAndResources();
    TestEnemySlots();
    TestResourceRelease();
    TestExit();
    TestRagdolls();
    TestRagdollFamily();
    TestGuiText();
    TestStreamWindow();
    TestBrokenTextures();
    TestParticleGuard();
    printf("%s\n", g_fails ? "FAILED" : "ALL PASSED");
    return g_fails ? 1 : 0;
}

// Called by engine_stub.exe.  Never returns: the stub's code is gone once DDDA.exe is mapped.
extern "C" __declspec(dllexport) void HarnessMain() {
    int argc = 0;
    wchar_t** argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    int code = Run(argc, argv);
    fflush(stdout);
    ExitProcess((UINT)code);
}

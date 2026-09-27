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
    g_lastLog = utf8;
    printf("  log   %s\n", utf8);
}
static int g_shadow = 0;
int IniInt(const wchar_t* section, const wchar_t* key, int def) {
    if (wcscmp(section, L"fps") == 0 && wcscmp(key, L"max_fps") == 0) return g_maxFps;
    if (wcscmp(section, L"render") == 0 && wcscmp(key, L"shadow_map_size") == 0) return g_shadow;
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
    FixesApplyPatches();
    Check(table[2] == 2049 && g_lastLog.find("not what build 2364871 has") != std::string::npos,
          "a table that differs is left alone and the log says so");
    MapImage(nullptr);
    g_shadow = 5000;
    FixesApplyPatches();
    Check(table[2] == 2048, "a size that is not a multiple of 32 is refused");
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
    Check(!ExitSitesVerified() && GameQuitFlag() == -1 && g_lastLog.find("not what build 2364871 has") != std::string::npos,
          "a changed site: neither flag is read, and the log says so");
    MapImage(nullptr);
    g_exitSites = -1;
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

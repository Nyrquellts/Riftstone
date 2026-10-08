// Stands in for DDDA.exe / DDO.exe: imports DirectInput8Create from dinput8.dll (so Windows loads
// the Riftstone proxy from this folder), opens files the way the game does, shows the game's
// fatal-error box, draws frames with Direct3D 9 and makes a window.  run_tests.py copies it into a
// throwaway game folder under the name of the game it plays.  Each mode prints one line per
// check; the Python driver asserts on them.  Nothing here takes focus: windows are never shown.
//
//   files        overlay redirection, missing files, the missing-texture stand-in (and, with
//                chain_plugin.asi loaded, how many of the game's CreateFileW calls its own hook saw)
//   reset        the import table restored after start-up (what a DRM stub could do)
//   crash        installs its own crash filter, then faults in its own code
//   stackoverflow  installs a quiet crash filter, then recurses until the main thread's stack is gone
//   crashrace    four threads fault in the same instant; every report and minidump must be whole
//   chainfilter  a module sets its own crash filter directly, after the loader's, and passes crashes on
//                to the one it replaced; once the loader has taken it in, a fault
//   plugincrash [name]  faults inside riftstone\plugins\<name> (crash_plugin.asi)
//   plugindeep   runs out of stack inside riftstone\plugins\crash_plugin.asi
//   fatal        the game's "Fatal error: Failed open file" box (only once the hook is confirmed)
//   guardrace    32 threads open missing textures at the same moment
//   resources ...  open:<path> opens an archive as the game streams one; <label>=<path> reads a file whole
//                (the archive guard: resources the game asks for loose before their archives were read)
//   resourcerace <path>  16 threads read one such resource at the same moment
//   live [engine-cap]  Direct3D 9 frames, then waits for <root>\done so the driver can read live stats
//                (engine-cap, DDDA layout: a stand-in sSetManager whose slots enemy_cap moved to its tail)
//   hang         some frames, then none (the hang detector's case)
//   hanglost     some frames, then a Present that says the device is lost and only TestCooperativeLevel after it
//                (drawn through chain_d3d9.dll with its "lost" marker)
//   hanglock     some frames, then the main thread holds the loader lock for 6 s and draws nothing
//   crashhang    some frames, then a thread crashes and the game takes its time to end (as under Windows Error
//                Reporting): no frames, and no hang report; then frames again and a stop, which is a hang
//   window       creates the game window; prints its style and size, and whether focus loss reached it
//   addon        loads riftstone_loader.dll the way another loader would
//   close <how>  a window and a message loop like DDDA's, closed one way (see CloseMode below)
//   overlay ...  real Direct3D 9 frames under the loader's diagnostics panel, read back (OverlayMode below)
//   d3d9         textures and buffers in every Direct3D pool, for the loader's pool counters (D3d9Mode below)
//   d3d9mem <n>  n managed DXT5 textures of 2048x2048: the address space they cost under this Direct3D 9
//   pressure <MB> commit that much, for the loader's memory pressure watch (PressureMode below)
//
// Built a second time as harness_ddda.exe (HARNESS_DDDA_LAYOUT): fixed at 0x400000 with DDDA.exe's
// address range as its own image, where it lays out the bytes of exit_sites.h, a stand-in sApp and
// sMain; run_tests.py gives the copy build 2364871's time stamp, so the loader reads the quit flag.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#define DIRECTINPUT_VERSION 0x0800
#include <dinput.h>
#include <d3d9.h>
#include <psapi.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void ReadAll(HANDLE h, char* out, DWORD cap) {
    DWORD got = 0;
    out[0] = 0;
    if (h == INVALID_HANDLE_VALUE) { strcpy_s(out, cap, "<cannot open>"); return; }
    ReadFile(h, out, cap - 1, &got, NULL);
    out[got] = 0;
    CloseHandle(h);
}

static void CheckA(const char* label, const char* path) {
    char buf[256];
    ReadAll(CreateFileA(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL), buf, sizeof(buf));
    printf("%s %s\n", label, buf);
}

static void CheckW(const char* label, const wchar_t* path) {
    char buf[256];
    ReadAll(CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL), buf, sizeof(buf));
    printf("%s %s\n", label, buf);
}

// A binary file: "size=<n> magic=<first 3 bytes> word1=<second word>" or "<cannot open>".
static void CheckBinary(const char* label, const char* path) {
    HANDLE h = CreateFileA(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) {
        printf("%s <cannot open> error=%lu\n", label, GetLastError());
        return;
    }
    unsigned char buf[128] = {};
    DWORD got = 0;
    ReadFile(h, buf, sizeof buf, &got, NULL);
    CloseHandle(h);
    unsigned w1 = got >= 8 ? *(unsigned*)(buf + 4) : 0;
    printf("%s size=%lu magic=%.3s word1=%08x\n", label, got, got >= 3 ? (char*)buf : "", w1);
}

// A whole file: "size=<n> fnv=<its FNV-1a 64>", or "<cannot open> error=<n>".
static unsigned long long Fnv(HANDLE h, unsigned long* total) {
    unsigned long long fnv = 14695981039346656037ULL;
    BYTE buf[16384];
    DWORD got = 0;
    *total = 0;
    while (ReadFile(h, buf, sizeof buf, &got, NULL) && got) {
        for (DWORD i = 0; i < got; i++) {
            fnv ^= buf[i];
            fnv *= 1099511628211ULL;
        }
        *total += got;
    }
    return fnv;
}

static void CheckWhole(const char* label, const char* path) {
    HANDLE h = CreateFileA(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) {
        printf("%s <cannot open> error=%lu\n", label, GetLastError());
        return;
    }
    unsigned long total = 0;
    unsigned long long fnv = Fnv(h, &total);
    CloseHandle(h);
    printf("%s size=%lu fnv=%016llx\n", label, total, fnv);
}

static BYTE* ImportSlotOf(const char* dll, const char* name) {
    BYTE* base = (BYTE*)GetModuleHandleW(NULL);
    IMAGE_NT_HEADERS* nt = (IMAGE_NT_HEADERS*)(base + ((IMAGE_DOS_HEADER*)base)->e_lfanew);
    IMAGE_DATA_DIRECTORY dir = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
    for (IMAGE_IMPORT_DESCRIPTOR* imp = (IMAGE_IMPORT_DESCRIPTOR*)(base + dir.VirtualAddress); imp->Name; ++imp) {
        if (_stricmp((const char*)(base + imp->Name), dll) != 0) continue;
        IMAGE_THUNK_DATA* thunk = (IMAGE_THUNK_DATA*)(base + imp->FirstThunk);
        IMAGE_THUNK_DATA* names = (IMAGE_THUNK_DATA*)(base + imp->OriginalFirstThunk);
        for (; thunk->u1.Function; ++thunk, ++names) {
            if (IMAGE_SNAP_BY_ORDINAL(names->u1.Ordinal)) continue;
            IMAGE_IMPORT_BY_NAME* ibn = (IMAGE_IMPORT_BY_NAME*)(base + names->u1.AddressOfData);
            if (strcmp((const char*)ibn->Name, name) == 0) return (BYTE*)&thunk->u1.Function;
        }
    }
    return NULL;
}

// Put the real kernel32 address back into our own import slot, as a DRM start-up stub might.
static bool RestoreImport(const char* name) {
    BYTE* slot = ImportSlotOf("KERNEL32.dll", name);
    if (!slot) return false;
    FARPROC real = GetProcAddress(GetModuleHandleW(L"kernel32.dll"), name);
    DWORD old;
    VirtualProtect(slot, sizeof(void*), PAGE_READWRITE, &old);
    *(FARPROC*)slot = real;
    VirtualProtect(slot, sizeof(void*), old, &old);
    return true;
}

// Is dll!name in our import table pointing into the Riftstone proxy?
static bool HookedByLoader(const char* dll, const char* name) {
    BYTE* slot = ImportSlotOf(dll, name);
    if (!slot) return false;
    HMODULE owner = NULL;
    GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                       *(LPCWSTR*)slot, &owner);
    return owner && owner == GetModuleHandleW(L"dinput8.dll");
}

static LONG WINAPI GameFilter(EXCEPTION_POINTERS*) {
    printf("game-filter-called\n");
    fflush(stdout);
    return EXCEPTION_EXECUTE_HANDLER;
}

// A filter that recovers, the way an anti-tamper wrapper answers its own probes.
static LONG WINAPI RecoveringFilter(EXCEPTION_POINTERS* ep) {
    printf("recovering-filter 0x%08lx\n", ep->ExceptionRecord->ExceptionCode);
    fflush(stdout);
    return EXCEPTION_CONTINUE_EXECUTION;
}

// The game's crash filter for a crash that leaves almost no stack: no CRT, one WriteFile.
static LONG WINAPI QuietGameFilter(EXCEPTION_POINTERS*) {
    static const char line[] = "game-filter-called\n";
    DWORD w;
    WriteFile(GetStdHandle(STD_OUTPUT_HANDLE), line, sizeof line - 1, &w, NULL);
    return EXCEPTION_EXECUTE_HANDLER;
}

#pragma warning(push)
#pragma warning(disable : 4717)   // recursive on every path: it is meant to use up the stack
static __declspec(noinline) int Deep(volatile int* depth) {
    volatile char pad[256];
    pad[0] = (char)++*depth;
    return Deep(depth) + pad[0];
}
#pragma warning(pop)

// A module that sets its own crash filter directly (kernel32's, not through the game's import table)
// after the loader's, and passes a crash on to the filter it replaced: the loader's.
typedef LPTOP_LEVEL_EXCEPTION_FILTER(WINAPI* SetUEF_t)(LPTOP_LEVEL_EXCEPTION_FILTER);
static LPTOP_LEVEL_EXCEPTION_FILTER g_moduleNext;
static volatile LONG g_moduleCalls;
static LONG WINAPI ModuleFilter(EXCEPTION_POINTERS* ep) {
    LONG n = InterlockedIncrement(&g_moduleCalls);
    if (n <= 3) {                        // a loop through the two filters would print tens of thousands
        char line[48];
        int len = _snprintf_s(line, sizeof line, _TRUNCATE, "module-filter %ld\n", n);
        DWORD w;
        if (len > 0) WriteFile(GetStdHandle(STD_OUTPUT_HANDLE), line, (DWORD)len, &w, NULL);
    }
    if (g_moduleNext) g_moduleNext(ep);
    return EXCEPTION_EXECUTE_HANDLER;
}

// Many threads asking for missing textures at the same moment (the stand-in is made on first use).
static HANDLE g_raceGo;
static volatile LONG g_raceOk;
static const char* g_resRacePath;
static volatile LONG g_resRaceOpened;
static unsigned long long g_resRaceHash[16];
static unsigned long g_resRaceSize[16];
static DWORD WINAPI ResRaceOpen(LPVOID arg) {
    int i = (int)(INT_PTR)arg;
    WaitForSingleObject(g_raceGo, INFINITE);
    HANDLE h = CreateFileA(g_resRacePath, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return 0;
    g_resRaceHash[i] = Fnv(h, &g_resRaceSize[i]);
    CloseHandle(h);
    InterlockedIncrement(&g_resRaceOpened);
    return 0;
}
// A crash the game does not survive, but whose process lingers: kernel32's UnhandledExceptionFilter runs the
// top-level filter (the loader's, then the game's, which says to end), and this thread then lives on for a while
// instead of ending the process, as a game does while Windows Error Reporting holds it.
static DWORD WINAPI CrashLinger(LPVOID) {
    __try {
        volatile int* p = (int*)(INT_PTR)0x10;
        *p = 1;
    } __except (UnhandledExceptionFilter(GetExceptionInformation())) {
        printf("crash-handed-on\n");
        fflush(stdout);
    }
    return 0;
}

static DWORD WINAPI CrashRace(LPVOID arg) {
    WaitForSingleObject(g_raceGo, INFINITE);
    volatile int* p = (int*)(INT_PTR)(0x10 + 0x10 * (int)(INT_PTR)arg);
    *p = 1;                                                   // each thread its own address, all at once
    return 0;
}
static DWORD WINAPI RaceOpen(LPVOID arg) {
    char path[MAX_PATH];
    sprintf_s(path, "nativePC\\rom\\model\\race_%d_BM.tex", (int)(INT_PTR)arg);
    WaitForSingleObject(g_raceGo, INFINITE);
    HANDLE h = CreateFileA(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h != INVALID_HANDLE_VALUE) {
        char buf[128];
        DWORD got = 0;
        ReadFile(h, buf, sizeof buf, &got, NULL);
        CloseHandle(h);
        if (got == 52 && memcmp(buf, "TEX", 3) == 0) InterlockedIncrement(&g_raceOk);
    }
    return 0;
}

static volatile LONG g_activateSeen = 0;
static LRESULT CALLBACK GameProc(HWND w, UINT msg, WPARAM wp, LPARAM lp) {
    if (msg == WM_ACTIVATEAPP && wp == FALSE) InterlockedIncrement(&g_activateSeen);
    return DefWindowProcA(w, msg, wp, lp);
}

static HWND MakeWindow(DWORD style) {
    WNDCLASSEXA wc = {sizeof wc};
    wc.lpfnWndProc = GameProc;
    wc.hInstance = GetModuleHandleW(NULL);
    wc.lpszClassName = "RiftstoneHarness";
    RegisterClassExA(&wc);
    RECT r = {0, 0, 800, 600};
    AdjustWindowRect(&r, style, FALSE);
    return CreateWindowExA(0, "RiftstoneHarness", "harness", style, 10, 10, r.right - r.left, r.bottom - r.top,
                           NULL, NULL, GetModuleHandleW(NULL), NULL);
}

// Draw `frames` frames with a Direct3D 9 device on a hidden window.  Returns the device (or NULL).
static IDirect3DDevice9* Draw(HWND w, int frames, int sleepMs) {
    IDirect3D9* d3d = Direct3DCreate9(D3D_SDK_VERSION);
    if (!d3d) { printf("d3d unavailable\n"); return NULL; }
    D3DPRESENT_PARAMETERS pp = {};
    pp.Windowed = TRUE;
    pp.SwapEffect = D3DSWAPEFFECT_DISCARD;
    pp.BackBufferWidth = 320;
    pp.BackBufferHeight = 240;
    pp.BackBufferFormat = D3DFMT_X8R8G8B8;
    pp.hDeviceWindow = w;
    IDirect3DDevice9* dev = NULL;
    HRESULT hr = d3d->CreateDevice(D3DADAPTER_DEFAULT, D3DDEVTYPE_HAL, w, D3DCREATE_SOFTWARE_VERTEXPROCESSING, &pp, &dev);
    if (FAILED(hr) || !dev) {
        printf("d3d unavailable (CreateDevice 0x%08lx)\n", (unsigned long)hr);
        d3d->Release();
        return NULL;
    }
    for (int i = 0; i < frames; i++) {
        dev->Clear(0, NULL, D3DCLEAR_TARGET, D3DCOLOR_XRGB(i & 255, 0, 0), 1.0f, 0);
        dev->Present(NULL, NULL, NULL, NULL);
        if (sleepMs) Sleep(sleepMs);
    }
    printf("presented %d\n", frames);
    d3d->Release();
    return dev;
}

static void WaitForFile(const wchar_t* root, const wchar_t* name) {
    wchar_t path[MAX_PATH];
    swprintf_s(path, L"%s\\%s", root, name);
    for (int i = 0; i < 400 && GetFileAttributesW(path) == INVALID_FILE_ATTRIBUTES; i++) Sleep(50);
}

static void WaitForDone(const wchar_t* root) { WaitForFile(root, L"done"); }

// ---- close: the game's close paths (the loader's session.cpp watches them) ----------------------------

#ifdef HARNESS_DDDA_LAYOUT
#include "../exit_sites.h"
#pragma bss_seg(".ddda")
static char g_dddaRange[0x1700000];            // DDDA.exe's range, 0x00400000 + 0x160C000, is ours
#pragma bss_seg()
struct StandInApp { unsigned long table; char pad[0x266C - 4]; volatile unsigned char quit; };           // sApp
struct StandInMain { unsigned long table; char pad[0x34 - 4]; volatile unsigned char exitRequested; };   // sMain
static StandInApp g_sApp;
static StandInMain g_sMain;

static bool InRange(uintptr_t va, size_t n) {
    return va >= (uintptr_t)g_dddaRange && va + n <= (uintptr_t)g_dddaRange + sizeof g_dddaRange;
}

static bool LayOutDdda() {
    if (!InRange(DDDA_SAPP, 4) || !InRange(DDDA_SMAIN, 4)) return false;
    for (const ExitSite& s : EXIT_SITES) {
        if (!InRange(s.va, s.n)) return false;
        memcpy((void*)(uintptr_t)s.va, s.b, s.n);
    }
    g_sApp.table = DDDA_SAPP_VTABLE;
    g_sMain.table = DDDA_SMAIN_VTABLE;
    *(unsigned long*)(uintptr_t)DDDA_SAPP = (unsigned long)(uintptr_t)&g_sApp;
    *(unsigned long*)(uintptr_t)DDDA_SMAIN = (unsigned long)(uintptr_t)&g_sMain;
    return true;
}
#endif

static HWND g_closeWnd;
static volatile unsigned char g_quitStandIn;
static volatile unsigned char* g_quitFlag = &g_quitStandIn;   // sApp+0x266C in the DDDA layout

// sMain's exit request (0x00DBD0F0): mark it, then send WM_DESTROY to the game window.
static void ExitRequest(HWND w) {
#ifdef HARNESS_DDDA_LAYOUT
    g_sMain.exitRequested = 1;
#endif
    SendMessageA(w, WM_DESTROY, 0, 0);
}

// DDDA's window procedure (0x00DF1550) for the close path: Alt+F4 and SC_CLOSE go to DefWindowProcW,
// WM_CLOSE on the main window runs the exit request, WM_DESTROY posts WM_QUIT.
static LRESULT CALLBACK CloseProc(HWND w, UINT msg, WPARAM wp, LPARAM lp) {
    switch (msg) {
    case WM_SYSKEYDOWN:
        if (wp == VK_F4 && (lp & (1 << 29))) {
            // DefWindowProcW turns a real Alt+F4 key press into SC_CLOSE; it ignores a posted one
            // (measured), so the stand-in takes that one step itself.  Nothing is typed.
            PostMessageW(w, WM_SYSCOMMAND, SC_CLOSE, 0);
            return 0;
        }
        break;
    case WM_CLOSE:
        if (w == g_closeWnd) {
            ExitRequest(w);
            return 0;
        }
        break;
    case WM_DESTROY:
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProcW(w, msg, wp, lp);
}

// DDDA's loop (0x00DF0D20): every message, then a frame; it ends on WM_QUIT or the quit flag, and sets
// the flag itself as it ends.
static const char* GameLoop(int maxMs) {
    ULONGLONG end = GetTickCount64() + maxMs;
    bool quit = false;
    while (GetTickCount64() < end) {
        MSG m;
        while (!quit && PeekMessageA(&m, NULL, 0, 0, PM_REMOVE)) {
            TranslateMessage(&m);
            DispatchMessageW(&m);
            quit = m.message == WM_QUIT;
        }
        Sleep(5);                                              // the frame
        if (quit || *g_quitFlag) {
            const char* why = quit ? "quit" : "flag";
            *g_quitFlag = 1;
            return why;
        }
    }
    return "timeout";
}

// Until loader.log says `needle` (the loader has found the window), keeping the window's messages flowing.
static bool LogSays(const wchar_t* root, const char* needle, int ms) {
    wchar_t path[MAX_PATH];
    swprintf_s(path, L"%s\\riftstone\\logs\\loader.log", root);
    static char text[1 << 16];
    for (int waited = 0; waited <= ms; waited += 20) {
        HANDLE h = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, NULL,
                               OPEN_EXISTING, 0, NULL);
        if (h != INVALID_HANDLE_VALUE) {
            DWORD got = 0;
            ReadFile(h, text, sizeof text - 1, &got, NULL);
            CloseHandle(h);
            text[got] = 0;
            if (strstr(text, needle)) return true;
        }
        MSG m;
        while (PeekMessageA(&m, NULL, 0, 0, PM_REMOVE)) {
            TranslateMessage(&m);
            DispatchMessageW(&m);
        }
        Sleep(20);
    }
    return false;
}

// The window's messages for `ms` (the loader's watchdog reads the game's state every 50 ms meanwhile).
static void Pump(int ms) {
    for (ULONGLONG end = GetTickCount64() + ms; GetTickCount64() < end;) {
        MSG m;
        while (PeekMessageA(&m, NULL, 0, 0, PM_REMOVE)) {
            TranslateMessage(&m);
            DispatchMessageW(&m);
        }
        Sleep(10);
    }
}

// close <how>: altf4, button (the title bar's close button), menu (the window menu's Close by key),
// destroy (DestroyWindow, no close), none (the loop just ends), quitflag (Exit Game) and exitrequest
// (sMain's exit request with no WM_CLOSE; both DDDA layout), outside and keep (the driver closes it).
// startset-<how>: the quit flag already reads set while the game starts, and clears before its loop runs
// (seen in the real game, 2026-09-25), then <how>.
static int CloseMode(const wchar_t* root, const char* how) {
#ifdef HARNESS_DDDA_LAYOUT
    if (!LayOutDdda()) {
        printf("layout missing\n");
        return 6;
    }
    g_quitFlag = &g_sApp.quit;
    printf("layout ddda\n");
#endif
    bool startSet = strncmp(how, "startset-", 9) == 0;
    if (startSet) {
        how += 9;
        *g_quitFlag = 1;
    }
    WNDCLASSEXW wc = {sizeof wc};
    wc.lpfnWndProc = CloseProc;
    wc.hInstance = GetModuleHandleW(NULL);
    wc.lpszClassName = L"RiftstoneCloseHarness";
    RegisterClassExW(&wc);
    g_closeWnd = CreateWindowExW(0, wc.lpszClassName, L"harness", 0x00CA0000, 10, 10, 640, 480, NULL, NULL,
                                 wc.hInstance, NULL);                  // DDDA's windowed style; never shown
    printf("hwnd %p\n", (void*)g_closeWnd);
    printf("watched %s\n", LogSays(root, "exit     watching the game window", 5000) ? "yes" : "no");
    if (startSet) {
        Pump(400);                           // eight of the watchdog's reads with the flag set
        *g_quitFlag = 0;
        Pump(400);
    }
    printf("ready\n");
    fflush(stdout);
    bool driver = strcmp(how, "outside") == 0 || strcmp(how, "keep") == 0;
    if (strcmp(how, "altf4") == 0) PostMessageW(g_closeWnd, WM_SYSKEYDOWN, VK_F4, 0x203E0001);   // Alt held
    else if (strcmp(how, "button") == 0) SendMessageW(g_closeWnd, WM_SYSCOMMAND, SC_CLOSE, MAKELPARAM(620, 18));
    else if (strcmp(how, "menu") == 0) SendMessageW(g_closeWnd, WM_SYSCOMMAND, SC_CLOSE, 0);
    else if (strcmp(how, "destroy") == 0) DestroyWindow(g_closeWnd);
    else if (strcmp(how, "quitflag") == 0) *g_quitFlag = 1;
    else if (strcmp(how, "exitrequest") == 0) ExitRequest(g_closeWnd);
    const char* ended = GameLoop(driver ? 30000 : strcmp(how, "none") == 0 ? 300 : 5000);
    printf("close-ended %s\n", ended);
    fflush(stdout);
    Sleep(250);                              // the teardown (DDDA: 0x00DF1140), while sApp still stands
    return 0;
}

// ---- overlay: the loader's diagnostics panel over real Direct3D 9 frames ------------------------------
//
// overlay <W>x<H> [then=<W>x<H>] [critical] [engine]
//   Frames with the harness's own state bound at every Present: an offscreen render target and its own
//   depth-stencil surface, a texture on two stages, buffers, a viewport, a scissor rectangle, shader
//   constants, a transform, and render, stage and sampler states nobody leaves by accident.  After each
//   Present all of it must still be bound.  The back buffer is read back after a Present (the COPY swap
//   effect keeps it) into overlay-<name>.bmp for the driver.
//   then=WxH  a device Reset to that size with the panel showing, then more frames (overlay-reset.bmp)
//   critical  address space reserved until less than 400 MB is left (the loader's warning level) for
//             overlay-critical.bmp, then given back
//   engine    (DDDA layout) a stand-in sSetManager with 7, then 3 of its 10 enemy slots in use, and
//             sArea's current stage, 100

#ifdef HARNESS_DDDA_LAYOUT
// What fixes.cpp reads in build 2364871 (docs/re-enemy-cap.md; the game's stage reader 0x005BAF40), stood in.
// sSetManager: slots at +0x844, mUnitNumEnemy +0x1B8D0, and the slots enemy_cap moves to its tail (+0x1B950)
static unsigned char g_setManager[0x1B950 + 64 * 0x20];
static unsigned char g_area[0x3834 + 0x10];               // sArea: the current stage record at +0x3834
static unsigned char g_stageNow[0x724 + 0x10];            // set-up flag +0x20, stage number +0x724

static bool StandInEngine(int active, int stage) {
    const uintptr_t SET_MANAGER = 0x018FA504, S_AREA = 0x018D099C;
    if (!InRange(SET_MANAGER, 4) || !InRange(S_AREA, 4)) return false;
    *(unsigned long*)SET_MANAGER = (unsigned long)(uintptr_t)g_setManager;
    for (int i = 0; i < 10; i++) {
        unsigned char* s = g_setManager + 0x844 + i * 0x20;
        *(unsigned long*)s = 0x01562414;                                  // sSetManager::cUnitData's table
        *(unsigned long*)(s + 4) = i < active ? 0x1000u + i : 0;          // a unit in the slot, or none
    }
    *(int*)(g_setManager + 0x1B8D0) = 10;
    *(unsigned long*)S_AREA = (unsigned long)(uintptr_t)g_area;
    *(unsigned long*)(g_area + 0x3834) = (unsigned long)(uintptr_t)g_stageNow;
    g_stageNow[0x20] = 1;
    *(int*)(g_stageNow + 0x724) = stage;
    return true;
}

// What enemy_cap leaves: `slots` slots at the manager's tail (+0x1B950), `active` of them in use, and
// mUnitNumEnemy raised to the new count.  The vanilla slots keep what StandInEngine put there.
static bool StandInEngineMoved(int slots, int active) {
    if (slots > 64) return false;
    for (int i = 0; i < slots; i++) {
        unsigned char* s = g_setManager + 0x1B950 + i * 0x20;
        *(unsigned long*)s = 0x01562414;
        *(unsigned long*)(s + 4) = i < active ? 0x2000u + i : 0;
    }
    *(int*)(g_setManager + 0x1B8D0) = slots;
    return true;
}
#endif

static const D3DCOLOR OVERLAY_CLEAR = D3DCOLOR_XRGB(58, 74, 92);   // run_tests.py knows it

struct Rig {
    IDirect3DSurface9* backbuffer; IDirect3DSurface9* autoDs;       // the device's own
    IDirect3DSurface9* rt; IDirect3DSurface9* ds;                   // bound at Present instead
    IDirect3DTexture9* tex; IDirect3DVertexBuffer9* vb; IDirect3DIndexBuffer9* ib;
    IDirect3DStateBlock9* defaults;                                 // the state a new (or reset) device has
};

static const DWORD RIG_RS[][2] = {
    {D3DRS_ZENABLE, D3DZB_TRUE}, {D3DRS_ZFUNC, D3DCMP_GREATER}, {D3DRS_ZWRITEENABLE, FALSE},
    {D3DRS_CULLMODE, D3DCULL_CW}, {D3DRS_FILLMODE, D3DFILL_WIREFRAME}, {D3DRS_SHADEMODE, D3DSHADE_FLAT},
    {D3DRS_ALPHABLENDENABLE, FALSE}, {D3DRS_SRCBLEND, D3DBLEND_DESTCOLOR}, {D3DRS_DESTBLEND, D3DBLEND_SRCCOLOR},
    {D3DRS_BLENDOP, D3DBLENDOP_MAX}, {D3DRS_ALPHATESTENABLE, TRUE}, {D3DRS_ALPHAREF, 0x42},
    {D3DRS_FOGENABLE, TRUE}, {D3DRS_FOGCOLOR, 0x00ABCDEF}, {D3DRS_LIGHTING, TRUE}, {D3DRS_STENCILENABLE, TRUE},
    {D3DRS_SCISSORTESTENABLE, TRUE}, {D3DRS_COLORWRITEENABLE, 0x5}, {D3DRS_SRGBWRITEENABLE, TRUE},
    {D3DRS_TEXTUREFACTOR, 0x11223344}, {D3DRS_WRAP0, D3DWRAP_U}, {D3DRS_DITHERENABLE, TRUE},
    {D3DRS_CLIPPLANEENABLE, 0x3}, {D3DRS_MULTISAMPLEMASK, 0x0F0F0F0F}, {D3DRS_SEPARATEALPHABLENDENABLE, TRUE},
};
static const DWORD RIG_TSS[][3] = {
    {0, D3DTSS_COLOROP, D3DTOP_ADD}, {0, D3DTSS_COLORARG1, D3DTA_TFACTOR}, {0, D3DTSS_ALPHAOP, D3DTOP_SUBTRACT},
    {0, D3DTSS_TEXCOORDINDEX, 1}, {0, D3DTSS_TEXTURETRANSFORMFLAGS, D3DTTFF_COUNT2}, {0, D3DTSS_RESULTARG, D3DTA_TEMP},
    {1, D3DTSS_COLOROP, D3DTOP_MODULATE2X}, {1, D3DTSS_ALPHAOP, D3DTOP_SELECTARG2},
};
static const DWORD RIG_SAMP[][3] = {
    {0, D3DSAMP_ADDRESSU, D3DTADDRESS_MIRROR}, {0, D3DSAMP_ADDRESSV, D3DTADDRESS_BORDER},
    {0, D3DSAMP_MAGFILTER, D3DTEXF_LINEAR}, {0, D3DSAMP_MINFILTER, D3DTEXF_LINEAR}, {0, D3DSAMP_MIPFILTER, D3DTEXF_LINEAR},
    {0, D3DSAMP_SRGBTEXTURE, TRUE}, {0, D3DSAMP_MAXANISOTROPY, 4}, {1, D3DSAMP_MAGFILTER, D3DTEXF_LINEAR},
};
static const D3DVIEWPORT9 RIG_VP = {3, 5, 50, 40, 0.25f, 0.75f};
static const RECT RIG_SCISSOR = {1, 2, 33, 44};
static const float RIG_VSC[4] = {1.5f, -2.0f, 3.25f, 4.0f};
static const float RIG_PSC[4] = {0.125f, 0.5f, -1.0f, 8.0f};
static const D3DMATRIX RIG_WORLD = {{{2, 0, 0, 0, 0, 3, 0, 0, 0, 0, 4, 0, 5, 6, 7, 1}}};
static const DWORD RIG_FVF = D3DFVF_XYZ | D3DFVF_NORMAL;

static bool RigMake(IDirect3DDevice9* dev, Rig& r) {
    if (!r.defaults && FAILED(dev->CreateStateBlock(D3DSBT_ALL, &r.defaults))) return false;   // first: untouched
    if (!r.backbuffer && FAILED(dev->GetBackBuffer(0, 0, D3DBACKBUFFER_TYPE_MONO, &r.backbuffer))) return false;
    if (!r.autoDs && FAILED(dev->GetDepthStencilSurface(&r.autoDs))) return false;
    if (!r.rt && FAILED(dev->CreateRenderTarget(64, 64, D3DFMT_X8R8G8B8, D3DMULTISAMPLE_NONE, 0, FALSE, &r.rt, NULL)))
        return false;
    if (!r.ds && FAILED(dev->CreateDepthStencilSurface(64, 64, D3DFMT_D24S8, D3DMULTISAMPLE_NONE, 0, TRUE, &r.ds, NULL)))
        return false;
    if (!r.tex && FAILED(dev->CreateTexture(4, 4, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &r.tex, NULL))) return false;
    if (!r.vb && FAILED(dev->CreateVertexBuffer(24 * 4, 0, RIG_FVF, D3DPOOL_MANAGED, &r.vb, NULL))) return false;
    if (!r.ib && FAILED(dev->CreateIndexBuffer(12, 0, D3DFMT_INDEX16, D3DPOOL_MANAGED, &r.ib, NULL))) return false;
    return true;
}

template <class T> static void Drop(T*& p) {
    if (p) p->Release();
    p = NULL;
}

// Before a Reset nothing of the harness's may stay bound or held (all: the managed ones too, at the end).
static void RigLetGo(IDirect3DDevice9* dev, Rig& r, bool all) {
    dev->SetRenderTarget(0, r.backbuffer);
    dev->SetDepthStencilSurface(r.autoDs);
    dev->SetTexture(0, NULL);
    dev->SetTexture(1, NULL);
    dev->SetStreamSource(0, NULL, 0, 0);
    dev->SetIndices(NULL);
    Drop(r.rt);
    Drop(r.ds);
    Drop(r.defaults);
    Drop(r.backbuffer);
    Drop(r.autoDs);
    if (all) {
        Drop(r.tex);
        Drop(r.vb);
        Drop(r.ib);
    }
}

static void RigBind(IDirect3DDevice9* dev, Rig& r) {
    dev->SetRenderTarget(0, r.rt);                         // resets the viewport, so that comes after
    dev->SetDepthStencilSurface(r.ds);
    dev->SetViewport(&RIG_VP);
    dev->SetScissorRect(&RIG_SCISSOR);
    for (const auto& s : RIG_RS) dev->SetRenderState((D3DRENDERSTATETYPE)s[0], s[1]);
    for (const auto& s : RIG_TSS) dev->SetTextureStageState(s[0], (D3DTEXTURESTAGESTATETYPE)s[1], s[2]);
    for (const auto& s : RIG_SAMP) dev->SetSamplerState(s[0], (D3DSAMPLERSTATETYPE)s[1], s[2]);
    dev->SetTexture(0, r.tex);
    dev->SetTexture(1, r.tex);
    dev->SetFVF(RIG_FVF);
    dev->SetStreamSource(0, r.vb, 0, 24);
    dev->SetIndices(r.ib);
    dev->SetVertexShaderConstantF(3, RIG_VSC, 1);
    dev->SetPixelShaderConstantF(2, RIG_PSC, 1);
    dev->SetTransform(D3DTS_WORLD, &RIG_WORLD);
}

static bool Changed(char* why, size_t cap, const char* fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsprintf_s(why, cap, fmt, ap);
    va_end(ap);
    return false;
}

// Everything RigBind bound, still bound?  why: the first thing that is not.
static bool RigCheck(IDirect3DDevice9* dev, Rig& r, char* why, size_t cap) {
    IDirect3DSurface9* s = NULL;
    bool same = SUCCEEDED(dev->GetRenderTarget(0, &s)) && s == r.rt;
    Drop(s);
    if (!same) return Changed(why, cap, "render target 0");
    same = SUCCEEDED(dev->GetDepthStencilSurface(&s)) && s == r.ds;
    Drop(s);
    if (!same) return Changed(why, cap, "depth-stencil surface");
    D3DVIEWPORT9 vp = {};
    if (FAILED(dev->GetViewport(&vp)) || memcmp(&vp, &RIG_VP, sizeof vp) != 0) return Changed(why, cap, "viewport");
    RECT sc = {};
    if (FAILED(dev->GetScissorRect(&sc)) || !EqualRect(&sc, &RIG_SCISSOR)) return Changed(why, cap, "scissor rectangle");
    for (const auto& st : RIG_RS) {
        DWORD v = 0;
        if (FAILED(dev->GetRenderState((D3DRENDERSTATETYPE)st[0], &v)) || v != st[1])
            return Changed(why, cap, "render state %lu = 0x%lx (bound 0x%lx)", st[0], v, st[1]);
    }
    for (const auto& st : RIG_TSS) {
        DWORD v = 0;
        if (FAILED(dev->GetTextureStageState(st[0], (D3DTEXTURESTAGESTATETYPE)st[1], &v)) || v != st[2])
            return Changed(why, cap, "stage %lu state %lu = 0x%lx (bound 0x%lx)", st[0], st[1], v, st[2]);
    }
    for (const auto& st : RIG_SAMP) {
        DWORD v = 0;
        if (FAILED(dev->GetSamplerState(st[0], (D3DSAMPLERSTATETYPE)st[1], &v)) || v != st[2])
            return Changed(why, cap, "sampler %lu state %lu = 0x%lx (bound 0x%lx)", st[0], st[1], v, st[2]);
    }
    for (DWORD stage = 0; stage < 2; stage++) {
        IDirect3DBaseTexture9* t = NULL;
        same = SUCCEEDED(dev->GetTexture(stage, &t)) && t == (IDirect3DBaseTexture9*)r.tex;
        Drop(t);
        if (!same) return Changed(why, cap, "texture on stage %lu", stage);
    }
    DWORD fvf = 0;
    if (FAILED(dev->GetFVF(&fvf)) || fvf != RIG_FVF) return Changed(why, cap, "FVF 0x%lx", fvf);
    IDirect3DVertexBuffer9* vb = NULL;
    UINT offset = 1, stride = 0;
    same = SUCCEEDED(dev->GetStreamSource(0, &vb, &offset, &stride)) && vb == r.vb && offset == 0 && stride == 24;
    Drop(vb);
    if (!same) return Changed(why, cap, "stream source 0");
    IDirect3DIndexBuffer9* ib = NULL;
    same = SUCCEEDED(dev->GetIndices(&ib)) && ib == r.ib;
    Drop(ib);
    if (!same) return Changed(why, cap, "index buffer");
    float c[4] = {};
    if (FAILED(dev->GetVertexShaderConstantF(3, c, 1)) || memcmp(c, RIG_VSC, sizeof c) != 0)
        return Changed(why, cap, "vertex shader constant");
    if (FAILED(dev->GetPixelShaderConstantF(2, c, 1)) || memcmp(c, RIG_PSC, sizeof c) != 0)
        return Changed(why, cap, "pixel shader constant");
    D3DMATRIX m = {};
    if (FAILED(dev->GetTransform(D3DTS_WORLD, &m)) || memcmp(&m, &RIG_WORLD, sizeof m) != 0)
        return Changed(why, cap, "world transform");
    IDirect3DVertexShader9* vs = NULL;
    IDirect3DPixelShader9* ps = NULL;
    dev->GetVertexShader(&vs);
    dev->GetPixelShader(&ps);
    same = !vs && !ps;
    Drop(vs);
    Drop(ps);
    if (!same) return Changed(why, cap, "shaders");
    return true;
}

static int g_framesChecked = 0, g_framesKept = 0;
static char g_firstChange[256];

// One frame: cleared, the rig bound, presented (the panel draws inside Present), the rig checked.
static void OverlayFrame(IDirect3DDevice9* dev, Rig& r) {
    dev->SetRenderTarget(0, r.backbuffer);
    dev->SetDepthStencilSurface(r.autoDs);
    r.defaults->Apply();
    dev->Clear(0, NULL, D3DCLEAR_TARGET | D3DCLEAR_ZBUFFER | D3DCLEAR_STENCIL, OVERLAY_CLEAR, 1.0f, 0);
    RigBind(dev, r);
    HRESULT hr = dev->Present(NULL, NULL, NULL, NULL);
    char why[200] = "";
    g_framesChecked++;
    if (FAILED(hr)) sprintf_s(why, "Present failed 0x%08lx", (unsigned long)hr);
    else if (RigCheck(dev, r, why, sizeof why)) g_framesKept++;
    if (why[0] && !g_firstChange[0]) sprintf_s(g_firstChange, "frame %d: %s", g_framesChecked, why);
}

// The back buffer as it was presented, as a 32-bit top-down BMP.
static void SaveBmp(IDirect3DDevice9* dev, Rig& r, const char* name) {
    D3DSURFACE_DESC d = {};
    IDirect3DSurface9* sys = NULL;
    D3DLOCKED_RECT lr = {};
    bool ok = SUCCEEDED(r.backbuffer->GetDesc(&d)) &&
              SUCCEEDED(dev->CreateOffscreenPlainSurface(d.Width, d.Height, d.Format, D3DPOOL_SYSTEMMEM, &sys, NULL)) &&
              SUCCEEDED(dev->GetRenderTargetData(r.backbuffer, sys)) && SUCCEEDED(sys->LockRect(&lr, NULL, D3DLOCK_READONLY));
    if (ok) {
        FILE* f = NULL;
        ok = fopen_s(&f, name, "wb") == 0 && f;
        if (ok) {
            BITMAPINFOHEADER ih = {};
            ih.biSize = sizeof ih;
            ih.biWidth = (LONG)d.Width;
            ih.biHeight = -(LONG)d.Height;
            ih.biPlanes = 1;
            ih.biBitCount = 32;
            ih.biCompression = BI_RGB;
            ih.biSizeImage = d.Width * d.Height * 4;
            BITMAPFILEHEADER fh = {};
            fh.bfType = 0x4D42;
            fh.bfOffBits = sizeof fh + sizeof ih;
            fh.bfSize = fh.bfOffBits + ih.biSizeImage;
            fwrite(&fh, sizeof fh, 1, f);
            fwrite(&ih, sizeof ih, 1, f);
            for (UINT y = 0; y < d.Height; y++) fwrite((BYTE*)lr.pBits + (size_t)y * lr.Pitch, 4, d.Width, f);
            fclose(f);
        }
        sys->UnlockRect();
    }
    Drop(sys);
    printf("bmp %s %s %ux%u\n", name, ok ? "saved" : "failed", d.Width, d.Height);
    fflush(stdout);
}

static void* g_reserved[1024];
static int g_reservedCount = 0;

// Reserve (not commit) address space until only `leave` bytes of it are free.
static SIZE_T ReserveAddressSpace(ULONGLONG leave) {
    SIZE_T got = 0;
    for (SIZE_T chunk = (SIZE_T)256 << 20; chunk >= ((SIZE_T)1 << 20); chunk >>= 1) {
        for (;;) {
            MEMORYSTATUSEX ms = {sizeof ms};
            if (g_reservedCount >= 1024 || !GlobalMemoryStatusEx(&ms) || ms.ullAvailVirtual < leave + chunk) break;
            void* p = VirtualAlloc(NULL, chunk, MEM_RESERVE, PAGE_NOACCESS);
            if (!p) break;
            g_reserved[g_reservedCount++] = p;
            got += chunk;
        }
    }
    return got;
}

static int OverlayMode(int argc, char** argv) {
    UINT w = 1920, h = 1080, w2 = 0, h2 = 0;
    bool critical = false, engine = false, early = false;
    for (int i = 2; i < argc; i++) {
        unsigned a = 0, b = 0;
        if (sscanf_s(argv[i], "then=%ux%u", &a, &b) == 2) {
            w2 = a;
            h2 = b;
        } else if (sscanf_s(argv[i], "%ux%u", &a, &b) == 2) {
            w = a;
            h = b;
        } else if (strcmp(argv[i], "critical") == 0) critical = true;
        else if (strcmp(argv[i], "engine") == 0) engine = true;
        else if (strcmp(argv[i], "early") == 0) early = true;   // also save the 8th frame (overlay-early.bmp)
    }
    if (engine) {
#ifdef HARNESS_DDDA_LAYOUT
        printf("engine %s\n", LayOutDdda() && StandInEngine(7, 100) ? "stand-in" : "missing");
#else
        printf("engine unavailable\n");
#endif
    }
    HWND wnd = MakeWindow(WS_OVERLAPPEDWINDOW);
    IDirect3D9* d3d = Direct3DCreate9(D3D_SDK_VERSION);
    if (!d3d) {
        printf("d3d unavailable\n");
        return 0;
    }
    D3DPRESENT_PARAMETERS pp = {};
    pp.BackBufferWidth = w;
    pp.BackBufferHeight = h;
    pp.BackBufferFormat = D3DFMT_X8R8G8B8;
    pp.BackBufferCount = 1;
    pp.SwapEffect = D3DSWAPEFFECT_COPY;                  // the back buffer stays as presented: it can be read back
    pp.hDeviceWindow = wnd;
    pp.Windowed = TRUE;
    pp.EnableAutoDepthStencil = TRUE;
    pp.AutoDepthStencilFormat = D3DFMT_D24S8;
    pp.PresentationInterval = D3DPRESENT_INTERVAL_IMMEDIATE;
    // DDDA's own flags (its CreateDevice text, 0x01429A28), then its software fallback.
    DWORD flags = D3DCREATE_FPU_PRESERVE | D3DCREATE_HARDWARE_VERTEXPROCESSING | D3DCREATE_MULTITHREADED;
    IDirect3DDevice9* dev = NULL;
    D3DPRESENT_PARAMETERS asked = pp;
    HRESULT hr = d3d->CreateDevice(D3DADAPTER_DEFAULT, D3DDEVTYPE_HAL, wnd, flags, &pp, &dev);
    if (FAILED(hr)) {
        pp = asked;
        flags = D3DCREATE_FPU_PRESERVE | D3DCREATE_SOFTWARE_VERTEXPROCESSING | D3DCREATE_MULTITHREADED;
        hr = d3d->CreateDevice(D3DADAPTER_DEFAULT, D3DDEVTYPE_HAL, wnd, flags, &pp, &dev);
    }
    Rig r = {};
    if (FAILED(hr) || !dev || !RigMake(dev, r)) {
        printf("d3d unavailable (CreateDevice 0x%08lx)\n", (unsigned long)hr);
        if (dev) {
            RigLetGo(dev, r, true);
            dev->Release();
        }
        d3d->Release();
        DestroyWindow(wnd);
        return 0;
    }
    MEMORYSTATUSEX ms = {sizeof ms};
    GlobalMemoryStatusEx(&ms);
    printf("overlay-device %ux%u flags 0x%lx\n", w, h, flags);
    printf("va-total %llu\n", ms.ullTotalVirtual);
    fflush(stdout);
    for (int i = 0; i < 90; i++) {
#ifdef HARNESS_DDDA_LAYOUT
        if (engine && i == 45) StandInEngine(3, 100);   // the peak (7) stays
#endif
        OverlayFrame(dev, r);
        if (early && i == 8) SaveBmp(dev, r, "overlay-early.bmp");
        Sleep(15);
    }
    SaveBmp(dev, r, "overlay-normal.bmp");
    if (critical) {
        SIZE_T got = ReserveAddressSpace(250ull << 20);
        GlobalMemoryStatusEx(&ms);
        printf("reserved %lu MB, %llu MB left\n", (unsigned long)(got >> 20), ms.ullAvailVirtual >> 20);
        for (int i = 0; i < 110; i++) {                  // the live thread samples memory every second
            OverlayFrame(dev, r);
            Sleep(15);
        }
        SaveBmp(dev, r, "overlay-critical.bmp");
        while (g_reservedCount) VirtualFree(g_reserved[--g_reservedCount], 0, MEM_RELEASE);
        printf("released\n");
    }
    if (w2 && h2) {
        RigLetGo(dev, r, false);
        pp.BackBufferWidth = w2;
        pp.BackBufferHeight = h2;
        hr = dev->Reset(&pp);
        printf("reset %s %ux%u 0x%08lx\n", SUCCEEDED(hr) ? "ok" : "failed", w2, h2, (unsigned long)hr);
        if (SUCCEEDED(hr) && RigMake(dev, r)) {
            for (int i = 0; i < 60; i++) {
                OverlayFrame(dev, r);
                Sleep(15);
            }
            SaveBmp(dev, r, "overlay-reset.bmp");
        }
    }
    printf("states-kept %d/%d%s%s\n", g_framesKept, g_framesChecked, g_firstChange[0] ? " first-change " : "",
           g_firstChange);
    fflush(stdout);
    RigLetGo(dev, r, true);
    dev->Release();
    d3d->Release();
    DestroyWindow(wnd);
    return 0;
}

// ---- d3d9: what the game holds in each Direct3D pool (the loader's graphics.cpp counts it) ------------
//
// d3d9      a device with DDDA's flags, then textures (plain, block-compressed with all levels and some, a cube,
//           a volume, a render target, system-memory and scratch ones) and buffers, each size known to the
//           driver, which works the bytes out on its own; an AddRef/Release pair and a surface of a texture
//           that must change nothing.  "ready1"; after <root>\step four of them released, "ready2"; exits
//           after <root>\done.  "d3d-module" names the DLL the Direct3D object came from.

static void D3DModule(IDirect3D9* d3d) {
    HMODULE m = NULL;
    wchar_t path[MAX_PATH] = L"?";
    // Slot 3 (RegisterSoftwareDevice) stays the runtime's own; the loader patches CreateDevice (16).
    if (GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                           (LPCWSTR)(*(void***)d3d)[3], &m))
        GetModuleFileNameW(m, path, MAX_PATH);
    printf("d3d-module %ls\n", path);
}

static IDirect3DDevice9* DddaDevice(IDirect3D9* d3d, HWND wnd, UINT w, UINT h) {
    D3DPRESENT_PARAMETERS pp = {};
    pp.BackBufferWidth = w;
    pp.BackBufferHeight = h;
    pp.BackBufferFormat = D3DFMT_X8R8G8B8;
    pp.BackBufferCount = 1;
    pp.SwapEffect = D3DSWAPEFFECT_DISCARD;
    pp.hDeviceWindow = wnd;
    pp.Windowed = TRUE;
    pp.PresentationInterval = D3DPRESENT_INTERVAL_IMMEDIATE;
    D3DPRESENT_PARAMETERS asked = pp;
    IDirect3DDevice9* dev = NULL;
    if (FAILED(d3d->CreateDevice(D3DADAPTER_DEFAULT, D3DDEVTYPE_HAL, wnd,
                                 D3DCREATE_FPU_PRESERVE | D3DCREATE_HARDWARE_VERTEXPROCESSING | D3DCREATE_MULTITHREADED,
                                 &pp, &dev))) {
        pp = asked;
        dev = NULL;
        d3d->CreateDevice(D3DADAPTER_DEFAULT, D3DDEVTYPE_HAL, wnd,
                          D3DCREATE_FPU_PRESERVE | D3DCREATE_SOFTWARE_VERTEXPROCESSING | D3DCREATE_MULTITHREADED, &pp, &dev);
    }
    return dev;
}

template <class T> static void Let(T*& p) {
    if (p) p->Release();
    p = NULL;
}

static int D3d9Mode(const wchar_t* root) {
    HWND wnd = MakeWindow(WS_OVERLAPPEDWINDOW);
    IDirect3D9* d3d = Direct3DCreate9(D3D_SDK_VERSION);
    if (!d3d) {
        printf("d3d unavailable\n");
        return 0;
    }
    D3DModule(d3d);
    IDirect3DDevice9* dev = DddaDevice(d3d, wnd, 320, 240);
    if (!dev) {
        printf("d3d unavailable (CreateDevice)\n");
        d3d->Release();
        return 0;
    }
    IDirect3DTexture9 *plain = NULL, *dxt1 = NULL, *dxt5 = NULL, *target = NULL, *sys = NULL, *scratch = NULL;
    IDirect3DCubeTexture9* cube = NULL;
    IDirect3DVolumeTexture9* volume = NULL;
    IDirect3DVertexBuffer9 *vb = NULL, *dynamicVb = NULL;
    IDirect3DIndexBuffer9* ib = NULL;
    HRESULT hr[11] = {
        dev->CreateTexture(256, 256, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &plain, NULL),
        dev->CreateTexture(512, 512, 0, 0, D3DFMT_DXT1, D3DPOOL_MANAGED, &dxt1, NULL),
        dev->CreateTexture(128, 64, 3, 0, D3DFMT_DXT5, D3DPOOL_MANAGED, &dxt5, NULL),
        dev->CreateCubeTexture(64, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &cube, NULL),
        dev->CreateVolumeTexture(32, 32, 8, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &volume, NULL),
        dev->CreateVertexBuffer(65536, 0, 0, D3DPOOL_MANAGED, &vb, NULL),
        dev->CreateIndexBuffer(32768, 0, D3DFMT_INDEX16, D3DPOOL_MANAGED, &ib, NULL),
        dev->CreateTexture(128, 128, 1, D3DUSAGE_RENDERTARGET, D3DFMT_A8R8G8B8, D3DPOOL_DEFAULT, &target, NULL),
        dev->CreateVertexBuffer(16384, D3DUSAGE_DYNAMIC | D3DUSAGE_WRITEONLY, 0, D3DPOOL_DEFAULT, &dynamicVb, NULL),
        dev->CreateTexture(64, 64, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_SYSTEMMEM, &sys, NULL),
        dev->CreateTexture(32, 32, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_SCRATCH, &scratch, NULL),
    };
    int made = 0;
    for (int i = 0; i < 11; i++) {
        if (SUCCEEDED(hr[i])) made++;
        else printf("create %d failed 0x%08lx\n", i, (unsigned long)hr[i]);
    }
    printf("made %d of 11\n", made);
    printf("levels dxt1 %lu\n", dxt1 ? dxt1->GetLevelCount() : 0ul);
    if (plain) {                                   // references that come and go change nothing
        plain->AddRef();
        plain->Release();
        IDirect3DSurface9* level = NULL;
        if (SUCCEEDED(plain->GetSurfaceLevel(0, &level))) level->Release();
    }
    for (int i = 0; i < 10; i++) {
        dev->Clear(0, NULL, D3DCLEAR_TARGET, D3DCOLOR_XRGB(0, i * 20, 0), 1.0f, 0);
        dev->Present(NULL, NULL, NULL, NULL);
    }
    printf("ready1\n");
    fflush(stdout);
    WaitForFile(root, L"step");
    Let(dxt1);
    Let(cube);
    Let(vb);
    Let(target);
    printf("released 4\n");
    printf("ready2\n");
    fflush(stdout);
    WaitForDone(root);
    Let(plain);
    Let(dxt5);
    Let(volume);
    Let(ib);
    Let(dynamicVb);
    Let(sys);
    Let(scratch);
    dev->Release();
    d3d->Release();
    DestroyWindow(wnd);
    return 0;
}

// ---- d3d9bench: what a texture's Release costs with the pools counted, and the counters under many threads -------
//
// 64 managed textures kept; one thread's AddRef/Release pairs over them, timed ("single <ns a pair>"); then four
// threads' at once while a fifth makes and releases a texture over and over, so freed addresses come back while the
// others release ("multi <ns a pair, wall clock> churn <textures made>"); ten frames, "ready", exits after
// <root>\done.  The counters must then hold exactly the 64 kept textures.

static IDirect3DTexture9* g_benchTex[64];
static IDirect3DDevice9* g_benchDev = NULL;
static volatile LONG g_benchStop = 0;
static const int BENCH_PAIRS = 400000;

static DWORD WINAPI BenchPairs(LPVOID seed) {
    for (int i = 0; i < BENCH_PAIRS; i++) {
        IDirect3DTexture9* t = g_benchTex[(i * 7 + (int)(INT_PTR)seed) & 63];
        t->AddRef();
        t->Release();
    }
    return 0;
}

static DWORD WINAPI BenchChurn(LPVOID made) {
    LONG n = 0;
    while (!g_benchStop) {
        IDirect3DTexture9* t = NULL;
        if (SUCCEEDED(g_benchDev->CreateTexture(32, 32, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &t, NULL))) {
            t->Release();
            n++;
        }
    }
    *(LONG*)made = n;
    return 0;
}

static int D3d9BenchMode(const wchar_t* root) {
    HWND wnd = MakeWindow(WS_OVERLAPPEDWINDOW);
    IDirect3D9* d3d = Direct3DCreate9(D3D_SDK_VERSION);
    if (!d3d) {
        printf("d3d unavailable\n");
        return 0;
    }
    IDirect3DDevice9* dev = DddaDevice(d3d, wnd, 320, 240);
    if (!dev) {
        printf("d3d unavailable (CreateDevice)\n");
        d3d->Release();
        return 0;
    }
    g_benchDev = dev;
    int made = 0;
    for (auto& t : g_benchTex)
        if (SUCCEEDED(dev->CreateTexture(64, 64, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &t, NULL))) made++;
    printf("made %d\n", made);
    if (made == 64) {
        LARGE_INTEGER f, a, b;
        QueryPerformanceFrequency(&f);
        QueryPerformanceCounter(&a);
        BenchPairs(NULL);
        QueryPerformanceCounter(&b);
        printf("single %.1f\n", (double)(b.QuadPart - a.QuadPart) * 1e9 / (double)f.QuadPart / BENCH_PAIRS);
        LONG churned = 0;
        HANDLE th[5];
        QueryPerformanceCounter(&a);
        for (int i = 0; i < 4; i++) th[i] = CreateThread(NULL, 0, BenchPairs, (LPVOID)(INT_PTR)(i + 1), 0, NULL);
        th[4] = CreateThread(NULL, 0, BenchChurn, &churned, 0, NULL);
        WaitForMultipleObjects(4, th, TRUE, INFINITE);
        QueryPerformanceCounter(&b);
        InterlockedExchange(&g_benchStop, 1);
        WaitForSingleObject(th[4], INFINITE);
        for (HANDLE h : th) CloseHandle(h);
        printf("multi %.1f churn %ld\n", (double)(b.QuadPart - a.QuadPart) * 1e9 / (double)f.QuadPart / (4.0 * BENCH_PAIRS),
               churned);
        for (int i = 0; i < 10; i++) {
            dev->Clear(0, NULL, D3DCLEAR_TARGET, D3DCOLOR_XRGB(0, 0, i * 20), 1.0f, 0);
            dev->Present(NULL, NULL, NULL, NULL);
        }
    }
    printf("ready\n");
    fflush(stdout);
    WaitForDone(root);
    for (auto& t : g_benchTex) Let(t);
    dev->Release();
    d3d->Release();
    DestroyWindow(wnd);
    return 0;
}

// ---- d3d9race: textures and their surfaces released from several threads at once --------------------------
//
// A texture's surface passes its references on to the texture, and the multithreaded device takes its own
// lock around both.  Two threads release the 64 textures directly, two release surfaces of the same textures
// (GetSurfaceLevel + Release), one makes and releases textures, one binds them (SetTexture) and draws frames.
// "done <ms>" when every thread finished; "stuck" when they did not within 20 s (a deadlock), and the process
// ends itself so the test is not left waiting.  "ready", exits after <root>\done.

static volatile LONG g_raceLeft = 0;

static DWORD WINAPI RaceSurfaces(LPVOID seed) {
    for (int i = 0; i < BENCH_PAIRS / 4; i++) {
        IDirect3DSurface9* s = NULL;
        if (SUCCEEDED(g_benchTex[(i * 5 + (int)(INT_PTR)seed) & 63]->GetSurfaceLevel(0, &s)) && s) s->Release();
    }
    InterlockedDecrement(&g_raceLeft);
    return 0;
}

static DWORD WINAPI RacePairs(LPVOID seed) {
    for (int i = 0; i < BENCH_PAIRS / 4; i++) {
        IDirect3DTexture9* t = g_benchTex[(i * 7 + (int)(INT_PTR)seed) & 63];
        t->AddRef();
        t->Release();
    }
    InterlockedDecrement(&g_raceLeft);
    return 0;
}

static DWORD WINAPI RaceDraw(LPVOID) {
    for (int i = 0; !g_benchStop; i++) {
        g_benchDev->SetTexture(0, g_benchTex[i & 63]);
        g_benchDev->SetTexture(0, NULL);
        if ((i & 255) == 0) {
            g_benchDev->Clear(0, NULL, D3DCLEAR_TARGET, D3DCOLOR_XRGB(0, i & 255, 0), 1.0f, 0);
            g_benchDev->Present(NULL, NULL, NULL, NULL);
        }
    }
    return 0;
}

static int D3d9RaceMode(const wchar_t* root) {
    HWND wnd = MakeWindow(WS_OVERLAPPEDWINDOW);
    IDirect3D9* d3d = Direct3DCreate9(D3D_SDK_VERSION);
    if (!d3d) {
        printf("d3d unavailable\n");
        return 0;
    }
    IDirect3DDevice9* dev = DddaDevice(d3d, wnd, 320, 240);
    if (!dev) {
        printf("d3d unavailable (CreateDevice)\n");
        d3d->Release();
        return 0;
    }
    g_benchDev = dev;
    int made = 0;
    for (auto& t : g_benchTex)
        if (SUCCEEDED(dev->CreateTexture(64, 64, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &t, NULL))) made++;
    printf("made %d\n", made);
    fflush(stdout);
    if (made == 64) {
        LONG churned = 0;
        g_raceLeft = 4;
        HANDLE th[6];
        DWORD t0 = GetTickCount();
        th[0] = CreateThread(NULL, 0, RacePairs, (LPVOID)1, 0, NULL);
        th[1] = CreateThread(NULL, 0, RacePairs, (LPVOID)2, 0, NULL);
        th[2] = CreateThread(NULL, 0, RaceSurfaces, (LPVOID)3, 0, NULL);
        th[3] = CreateThread(NULL, 0, RaceSurfaces, (LPVOID)4, 0, NULL);
        th[4] = CreateThread(NULL, 0, BenchChurn, &churned, 0, NULL);
        th[5] = CreateThread(NULL, 0, RaceDraw, NULL, 0, NULL);
        while (g_raceLeft > 0 && GetTickCount() - t0 < 20000) Sleep(50);
        if (g_raceLeft > 0) {
            printf("stuck %ld of 4 threads after %lu ms\n", g_raceLeft, GetTickCount() - t0);
            fflush(stdout);
            TerminateProcess(GetCurrentProcess(), 3);   // the deadlocked threads cannot be joined
        }
        DWORD ms = GetTickCount() - t0;
        InterlockedExchange(&g_benchStop, 1);
        WaitForMultipleObjects(6, th, TRUE, INFINITE);
        for (HANDLE h : th) CloseHandle(h);
        printf("done %lu churn %ld\n", ms, churned);
        for (int i = 0; i < 10; i++) {
            dev->Clear(0, NULL, D3DCLEAR_TARGET, D3DCOLOR_XRGB(0, 0, i * 20), 1.0f, 0);
            dev->Present(NULL, NULL, NULL, NULL);
        }
    }
    printf("ready\n");
    fflush(stdout);
    WaitForDone(root);
    for (auto& t : g_benchTex) Let(t);
    dev->Release();
    d3d->Release();
    DestroyWindow(wnd);
    return 0;
}

// ---- d3d9mem <n>: what managed textures cost the address space under this Direct3D 9 ------------------
//
// n managed DXT5 textures of 2048x2048 with all 12 levels (5,592,432 bytes each), filled through LockRect as
// DDDA.exe fills its own and drawn once each; the address space and commit before and after
// ("mem-before <va MB> <commit MB>", "mem-after ..."), then "ready"; exits after <root>\done.

static void MemLine(const char* label) {
    MEMORYSTATUSEX ms = {sizeof ms};
    GlobalMemoryStatusEx(&ms);
    PROCESS_MEMORY_COUNTERS_EX pmc = {};
    pmc.cb = sizeof pmc;
    K32GetProcessMemoryInfo(GetCurrentProcess(), (PROCESS_MEMORY_COUNTERS*)&pmc, sizeof pmc);
    printf("%s %llu %llu\n", label, (ms.ullTotalVirtual - ms.ullAvailVirtual) >> 20,
           (unsigned long long)pmc.PrivateUsage >> 20);
}

static int D3d9MemMode(const wchar_t* root, int count) {
    HWND wnd = MakeWindow(WS_OVERLAPPEDWINDOW);
    IDirect3D9* d3d = Direct3DCreate9(D3D_SDK_VERSION);
    if (!d3d) {
        printf("d3d unavailable\n");
        return 0;
    }
    D3DModule(d3d);
    IDirect3DDevice9* dev = DddaDevice(d3d, wnd, 640, 480);
    if (!dev) {
        printf("d3d unavailable (CreateDevice)\n");
        d3d->Release();
        return 0;
    }
    for (int i = 0; i < 3; i++) {
        dev->Clear(0, NULL, D3DCLEAR_TARGET, 0, 1.0f, 0);
        dev->Present(NULL, NULL, NULL, NULL);
    }
    Sleep(300);
    MemLine("mem-before");
    IDirect3DTexture9** tex = (IDirect3DTexture9**)calloc((size_t)count, sizeof(void*));
    unsigned long long bytes = 0;
    int made = 0;
    for (int i = 0; tex && i < count; i++) {
        if (FAILED(dev->CreateTexture(2048, 2048, 0, 0, D3DFMT_DXT5, D3DPOOL_MANAGED, &tex[i], NULL))) break;
        made++;
        for (DWORD l = 0; l < tex[i]->GetLevelCount(); l++) {
            D3DSURFACE_DESC d;
            D3DLOCKED_RECT lr;
            tex[i]->GetLevelDesc(l, &d);
            if (FAILED(tex[i]->LockRect(l, &lr, NULL, 0))) continue;
            UINT rows = (d.Height + 3) / 4;
            for (UINT r = 0; r < rows; r++) memset((BYTE*)lr.pBits + (size_t)r * lr.Pitch, (i * 7 + (int)l) & 255, lr.Pitch);
            tex[i]->UnlockRect(l);
            bytes += (unsigned long long)((d.Width + 3) / 4) * ((d.Height + 3) / 4) * 16;
        }
    }
    struct V { float x, y, z, rhw, u, v; };
    V tri[3] = {{0, 0, 0, 1, 0, 0}, {640, 0, 0, 1, 1, 0}, {0, 480, 0, 1, 0, 1}};
    dev->SetFVF(D3DFVF_XYZRHW | D3DFVF_TEX1);
    for (int frame = 0; frame < 3; frame++) {
        dev->Clear(0, NULL, D3DCLEAR_TARGET, 0, 1.0f, 0);
        dev->BeginScene();
        for (int i = 0; i < made; i++) {
            dev->SetTexture(0, tex[i]);
            dev->DrawPrimitiveUP(D3DPT_TRIANGLELIST, 1, tri, sizeof(V));
        }
        dev->EndScene();
        dev->Present(NULL, NULL, NULL, NULL);
    }
    dev->SetTexture(0, NULL);
    Sleep(500);
    printf("made %d %llu\n", made, bytes);
    MemLine("mem-after");
    printf("ready\n");
    fflush(stdout);
    WaitForDone(root);
    for (int i = 0; i < made; i++) tex[i]->Release();
    free(tex);
    dev->Release();
    d3d->Release();
    DestroyWindow(wnd);
    return 0;
}

// ---- pressure <MB>: the loader's memory pressure watch -----------------------------------------------
//
// Commits memory (never touched, so it costs no RAM) until the process holds <MB> of private bytes, and waits
// for loader.log to say PRESSURE; "ready"; after <root>\step gives it all back and waits for "pressure eased";
// "ready2"; exits after <root>\done.

static int PressureMode(const wchar_t* root, int targetMb) {
    static void* blocks[256];
    int n = 0;
    PROCESS_MEMORY_COUNTERS_EX pmc = {};
    pmc.cb = sizeof pmc;
    while (n < 256) {
        K32GetProcessMemoryInfo(GetCurrentProcess(), (PROCESS_MEMORY_COUNTERS*)&pmc, sizeof pmc);
        if ((pmc.PrivateUsage >> 20) >= (SIZE_T)targetMb) break;
        void* b = VirtualAlloc(NULL, (SIZE_T)64 << 20, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
        if (!b) break;
        blocks[n++] = b;
    }
    K32GetProcessMemoryInfo(GetCurrentProcess(), (PROCESS_MEMORY_COUNTERS*)&pmc, sizeof pmc);
    printf("committed %d MB, private %llu MB\n", n * 64, (unsigned long long)pmc.PrivateUsage >> 20);
    printf("pressure-logged %s\n", LogSays(root, "memory   PRESSURE", 8000) ? "yes" : "no");
    Sleep(1300);                                    // a publish with the watch's state in it
    printf("ready\n");
    fflush(stdout);
    WaitForFile(root, L"step");
    while (n) VirtualFree(blocks[--n], 0, MEM_RELEASE);
    printf("eased-logged %s\n", LogSays(root, "memory   pressure eased", 8000) ? "yes" : "no");
    Sleep(1300);
    printf("ready2\n");
    fflush(stdout);
    WaitForDone(root);
    return 0;
}

int main(int argc, char** argv) {
    // A crash in a test never shows Windows' error box (run_tests.py sets the same for every stand-in).
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX);
    wchar_t exe[MAX_PATH];
    GetModuleFileNameW(NULL, exe, MAX_PATH);
    *wcsrchr(exe, L'\\') = 0;
    SetCurrentDirectoryW(exe);
    const char* mode = argc > 1 ? argv[1] : "files";

    if (strcmp(mode, "addon") == 0) {
        // The add-on build, loaded the way DDDA Tweak's "loadLibrary" setting would.
        printf("addon %s\n", LoadLibraryW(L"riftstone_loader.dll") ? "loaded" : "failed");
    }
    IDirectInput8W* di = NULL;
    HRESULT hr = DirectInput8Create(GetModuleHandleW(NULL), DIRECTINPUT_VERSION, IID_IDirectInput8W, (void**)&di, NULL);
    printf("dinput %s\n", SUCCEEDED(hr) && di ? "ok" : "failed");
    if (di) di->Release();
    printf("pid %lu\n", GetCurrentProcessId());
    fflush(stdout);

    if (strcmp(mode, "reset") == 0) {
        printf("restored %s\n", RestoreImport("CreateFileA") ? "yes" : "no");
        CheckA("right-after-reset", "nativePC\\rom\\enemy\\em0100.arc");
        Sleep(400);
        CheckA("after-watchdog", "nativePC\\rom\\enemy\\em0100.arc");
        return 0;
    }
    if (strcmp(mode, "crash") == 0) {
        CheckA("before-crash", "nativePC\\rom\\enemy\\em0100.arc");
        SetUnhandledExceptionFilter(GameFilter);
        fflush(stdout);
        volatile int* p = (int*)0x10;
        *p = 1;   // access violation: the loader must report, then call GameFilter
        return 3;
    }
    if (strcmp(mode, "recover") == 0) {
        SetUnhandledExceptionFilter(RecoveringFilter);
        RaiseException(EXCEPTION_BREAKPOINT, 0, 0, NULL);    // a debugger probe: passed on, no report
        RaiseException(0xE0001234, 0, 0, NULL);              // recovered further down: one report, and on we go
        printf("still-running\n");
        fflush(stdout);
        SetUnhandledExceptionFilter(GameFilter);
        volatile int* p = (int*)0x10;
        *p = 1;                                               // a real crash afterwards: its own report
        return 3;
    }
    if (strcmp(mode, "stackoverflow") == 0) {
        SetUnhandledExceptionFilter(QuietGameFilter);         // through the import table, as the game does
        printf("recursing\n");
        fflush(stdout);
        volatile int depth = 0;
        Deep(&depth);                                         // the loader must still report this one
        return 3;
    }
    if (strcmp(mode, "chainfilter") == 0) {
        SetUEF_t set = (SetUEF_t)GetProcAddress(GetModuleHandleW(L"kernel32.dll"), "SetUnhandledExceptionFilter");
        g_moduleNext = set ? set(ModuleFilter) : NULL;
        printf("module-filter-set %s\n", g_moduleNext ? "over-another" : "first");
        // The loader looks at the filter every 5 s; it logs when it has taken this one in.
        printf("adopted %s\n", LogSays(exe, "crash    another module", 8000) ? "yes" : "no");
        fflush(stdout);
        volatile int* p = (int*)0x10;
        *p = 1;
        return 3;
    }
    if (strcmp(mode, "plugincrash") == 0 || strcmp(mode, "plugindeep") == 0) {
        wchar_t name[MAX_PATH] = L"crash_plugin.asi";
        if (argc > 2) MultiByteToWideChar(CP_ACP, 0, argv[2], -1, name, MAX_PATH);
        HMODULE plugin = GetModuleHandleW(name);
        printf("crash-plugin %s\n", plugin ? "loaded" : "absent");
        fflush(stdout);
        if (!plugin) return 4;
        if (strcmp(mode, "plugindeep") == 0) {
            SetUnhandledExceptionFilter(QuietGameFilter);
            ((void (*)())GetProcAddress(plugin, "RiftstoneTestDeep"))();
            return 3;
        }
        SetUnhandledExceptionFilter(GameFilter);
        ((void (*)())GetProcAddress(plugin, "RiftstoneTestBoom"))();
        return 3;
    }
    if (strcmp(mode, "crashrace") == 0) {
        // Worker threads that fault in the same instant (the game's job threads at Gran Soren, 2026-10-06):
        // every report must be whole, minidump included, before any thread's crash ends the process.
        const int n = 4;
        HANDLE threads[n];
        SetUnhandledExceptionFilter(GameFilter);
        g_raceGo = CreateEventW(NULL, TRUE, FALSE, NULL);
        for (int i = 0; i < n; i++) threads[i] = CreateThread(NULL, 0, CrashRace, (LPVOID)(INT_PTR)i, 0, NULL);
        Sleep(200);                                           // every thread waits at the start line
        printf("crashing %d\n", n);
        fflush(stdout);
        SetEvent(g_raceGo);
        WaitForMultipleObjects(n, threads, TRUE, 60000);
        return 3;
    }
    if (strcmp(mode, "guardrace") == 0) {
        const int n = 32;
        HANDLE threads[n];
        g_raceGo = CreateEventW(NULL, TRUE, FALSE, NULL);
        for (int i = 0; i < n; i++) threads[i] = CreateThread(NULL, 0, RaceOpen, (LPVOID)(INT_PTR)i, 0, NULL);
        Sleep(200);                                           // every thread waits at the start line
        SetEvent(g_raceGo);
        WaitForMultipleObjects(n, threads, TRUE, 30000);
        printf("race-ok %ld/%d\n", g_raceOk, n);
        return 0;
    }
    if (strcmp(mode, "resources") == 0) {
        for (int i = 2; i < argc; i++) {
            if (strncmp(argv[i], "open:", 5) == 0) {           // as the game streams an archive: its first bytes
                HANDLE h = CreateFileA(argv[i] + 5, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING,
                                       FILE_ATTRIBUTE_NORMAL, NULL);
                char head[8];
                DWORD got = 0;
                if (h != INVALID_HANDLE_VALUE) {
                    ReadFile(h, head, sizeof head, &got, NULL);
                    CloseHandle(h);
                }
                continue;
            }
            const char* eq = strchr(argv[i], '=');
            char label[64];
            if (!eq || eq - argv[i] >= (int)sizeof label) continue;
            memcpy(label, argv[i], eq - argv[i]);
            label[eq - argv[i]] = 0;
            CheckWhole(label, eq + 1);
        }
        return 0;
    }
    if (strcmp(mode, "resourcerace") == 0 && argc > 2) {
        const int n = 16;
        HANDLE threads[n];
        g_resRacePath = argv[2];
        g_raceGo = CreateEventW(NULL, TRUE, FALSE, NULL);
        for (int i = 0; i < n; i++) threads[i] = CreateThread(NULL, 0, ResRaceOpen, (LPVOID)(INT_PTR)i, 0, NULL);
        Sleep(200);                                           // every thread waits at the start line
        SetEvent(g_raceGo);
        WaitForMultipleObjects(n, threads, TRUE, 60000);
        int same = 0;
        for (int i = 0; i < n; i++)
            same += g_resRaceSize[i] && g_resRaceSize[i] == g_resRaceSize[0] && g_resRaceHash[i] == g_resRaceHash[0];
        printf("resrace opened=%ld same=%d size=%lu fnv=%016llx\n", g_resRaceOpened, same, g_resRaceSize[0],
               g_resRaceHash[0]);
        return 0;
    }
    if (strcmp(mode, "fatal") == 0) {
        CheckA("before-fatal", "nativePC\\rom\\enemy\\em0100.arc");
        if (!HookedByLoader("USER32.dll", "MessageBoxA")) {
            printf("fatal-hook missing\n");   // never show a real box from a test
            return 5;
        }
        // The game's own words: DDDA.exe's "Failed open file. %s %d" (0x01408AD8), caption "Fatal error.".
        int r = MessageBoxA(NULL, "Failed open file. nativePC\\rom\\model\\missing_BM.tex 2", "Fatal error.", MB_OK);
        printf("fatal-returned %d\n", r);
        return 0;
    }
    if (strcmp(mode, "crashhang") == 0) {
        SetUnhandledExceptionFilter(GameFilter);
        HWND w = MakeWindow(WS_OVERLAPPEDWINDOW);
        IDirect3DDevice9* dev = Draw(w, 30, 5);
        HANDLE t = CreateThread(NULL, 0, CrashLinger, NULL, 0, NULL);
        if (t) {
            WaitForSingleObject(t, 60000);
            CloseHandle(t);
        }
        printf("ready\n");
        fflush(stdout);
        Sleep(4500);                                             // no frames while the game "ends"
        for (int i = 0; dev && i < 30; i++) {                    // ...then it lives on after all, and stops again
            dev->Present(NULL, NULL, NULL, NULL);
            Sleep(5);
        }
        printf("ready2\n");
        fflush(stdout);
        Sleep(4500);
        WaitForDone(exe);
        if (dev) dev->Release();
        DestroyWindow(w);
        return 3;
    }
    if (strcmp(mode, "hanglock") == 0) {
        // A main thread stopped while it holds the loader lock (a DLL's start-up stuck, or LoadLibrary waiting on a
        // thread that waits for it): no frames, and every call that takes the lock waits until it is let go.
        HWND w = MakeWindow(WS_OVERLAPPEDWINDOW);
        IDirect3DDevice9* dev = Draw(w, 30, 5);
        typedef LONG(NTAPI* LdrLockLoaderLock_t)(ULONG, ULONG*, ULONG_PTR*);
        typedef LONG(NTAPI* LdrUnlockLoaderLock_t)(ULONG, ULONG_PTR);
        HMODULE nt = GetModuleHandleW(L"ntdll.dll");
        LdrLockLoaderLock_t lock = (LdrLockLoaderLock_t)GetProcAddress(nt, "LdrLockLoaderLock");
        LdrUnlockLoaderLock_t unlock = (LdrUnlockLoaderLock_t)GetProcAddress(nt, "LdrUnlockLoaderLock");
        ULONG_PTR cookie = 0;
        BOOL held = lock && unlock && lock(0, NULL, &cookie) == 0;
        printf("loader-lock %s\n", held ? "held" : "not held");
        printf("ready\n");
        fflush(stdout);
        Sleep(6000);
        if (held) unlock(0, cookie);
        WaitForDone(exe);
        if (dev) dev->Release();
        DestroyWindow(w);
        return 0;
    }
    if (strcmp(mode, "hanglost") == 0) {
        // Drawn through chain_d3d9.dll with its "lost" marker: once RIFTSTONE_TEST_DEVICE_LOST is 1 its Present and
        // TestCooperativeLevel say D3DERR_DEVICELOST, under the loader's hooks.  Then the game's way (0x00DAEE55,
        // 0x00DAF834): a Present that says the device is lost sets a reset request, and from then on each pass of
        // the main loop asks TestCooperativeLevel and presents nothing.
        HWND w = MakeWindow(WS_OVERLAPPEDWINDOW);
        IDirect3DDevice9* dev = Draw(w, 30, 5);
        if (dev) {
            SetEnvironmentVariableW(L"RIFTSTONE_TEST_DEVICE_LOST", L"1");
            printf("present 0x%08lx\n", (unsigned long)dev->Present(NULL, NULL, NULL, NULL));
        }
        printf("ready\n");
        fflush(stdout);
        for (ULONGLONG until = GetTickCount64() + 4500; dev && GetTickCount64() < until;) {
            dev->TestCooperativeLevel();
            Sleep(16);
        }
        WaitForDone(exe);
        SetEnvironmentVariableW(L"RIFTSTONE_TEST_DEVICE_LOST", NULL);
        if (dev) dev->Release();
        DestroyWindow(w);
        return 0;
    }
    if (strcmp(mode, "live") == 0 || strcmp(mode, "hang") == 0 || strcmp(mode, "hangbehind") == 0) {
#ifdef HARNESS_DDDA_LAYOUT
        if (argc > 2 && strcmp(argv[2], "engine-cap") == 0)   // 7 of the vanilla ten in use; enemy_cap's 30, 17 in use
            printf("engine %s\n", LayOutDdda() && StandInEngine(7, 100) && StandInEngineMoved(30, 17) ? "stand-in" : "missing");
#endif
        CheckA("before-live", "nativePC\\rom\\enemy\\em0100.arc");
        HWND w = MakeWindow(WS_OVERLAPPEDWINDOW);
        IDirect3DDevice9* dev = Draw(w, strcmp(mode, "live") == 0 ? 60 : 30, 5);
        printf("ready\n");
        fflush(stdout);
        if (strcmp(mode, "hang") == 0) {                 // no frames: the detector's case
            if (dev) printf("tcl 0x%08lx\n", (unsigned long)dev->TestCooperativeLevel());
            fflush(stdout);
            Sleep(4500);
        }
        if (strcmp(mode, "hangbehind") == 0) {           // not in front, no frames, no messages: "not responding"
            ShowWindow(w, SW_MINIMIZE);
            Sleep(9000);
        }
        WaitForDone(exe);
        if (dev) dev->Release();
        DestroyWindow(w);
        return 0;
    }
    if (strcmp(mode, "close") == 0) return CloseMode(exe, argc > 2 ? argv[2] : "altf4");
    if (strcmp(mode, "overlay") == 0) return OverlayMode(argc, argv);
    if (strcmp(mode, "d3d9") == 0) return D3d9Mode(exe);
    if (strcmp(mode, "d3d9bench") == 0) return D3d9BenchMode(exe);
    if (strcmp(mode, "d3d9race") == 0) return D3d9RaceMode(exe);
    if (strcmp(mode, "d3d9mem") == 0) return D3d9MemMode(exe, argc > 2 ? atoi(argv[2]) : 64);
    if (strcmp(mode, "pressure") == 0) return PressureMode(exe, argc > 2 ? atoi(argv[2]) : 3450);
    if (strcmp(mode, "window") == 0) {
        HWND w = MakeWindow(WS_OVERLAPPEDWINDOW);
        LONG style = GetWindowLongA(w, GWL_STYLE);
        printf("window-caption %s\n", (style & WS_CAPTION) == WS_CAPTION ? "yes" : "no");
        SetWindowPos(w, NULL, 20, 20, 640, 480, SWP_NOZORDER | SWP_NOACTIVATE);
        RECT r;
        GetWindowRect(w, &r);
        HMONITOR m = MonitorFromWindow(w, MONITOR_DEFAULTTOPRIMARY);
        MONITORINFO mi = {sizeof mi};
        GetMonitorInfoW(m, &mi);
        printf("window-covers-monitor %s\n", EqualRect(&r, &mi.rcMonitor) ? "yes" : "no");
        printf("window-size %ldx%ld\n", r.right - r.left, r.bottom - r.top);
        SendMessageA(w, WM_ACTIVATEAPP, FALSE, 0);
        printf("activateapp-seen %s\n", g_activateSeen ? "yes" : "no");
        DestroyWindow(w);
        return 0;
    }

    CheckA("relative-A", "nativePC\\rom\\enemy\\em0100.arc");
    CheckW("relative-W", L"nativePC\\rom\\enemy\\em0100.arc");
    wchar_t abs[MAX_PATH];
    swprintf_s(abs, L"%s\\NATIVEPC\\ROM\\ENEMY\\EM0100.ARC", exe);
    CheckW("absolute-upper", abs);
    CheckA("dotted", "nativePC\\rom\\..\\rom\\enemy\\em0100.arc");
    CheckA("not-in-overlay", "nativePC\\rom\\game_main.arc");
    CheckA("overlay-only", "nativePC\\rom\\newthing.arc");
    CheckA("outside-nativepc", "other\\em0100.arc");
    DWORD a = GetFileAttributesA("nativePC\\rom\\newthing.arc");
    printf("attributes-overlay-only %s\n", a != INVALID_FILE_ATTRIBUTES ? "exists" : "missing");
    CheckBinary("missing-arc", "nativePC\\rom\\enemy\\em9999.arc");
    CheckBinary("missing-tex", "nativePC\\rom\\model\\missing_BM.tex");
    CheckBinary("missing-tex-outside", "other\\missing_BM.tex");
    CheckBinary("present-tex", "nativePC\\rom\\model\\present_BM.tex");

    // A write must never be redirected: it goes to the original path.
    HANDLE w = CreateFileA("nativePC\\rom\\enemy\\em0100.arc", GENERIC_READ | GENERIC_WRITE, 0, NULL, OPEN_EXISTING, 0, NULL);
    if (w != INVALID_HANDLE_VALUE) {
        char buf[64];
        DWORD got = 0;
        ReadFile(w, buf, sizeof(buf) - 1, &got, NULL);
        buf[got] = 0;
        printf("write-open %s\n", buf);
        CloseHandle(w);
    } else {
        printf("write-open <cannot open>\n");
    }
    // A plugin that hooked the game's CreateFileW itself (chain_plugin.asi): how many calls its hook passed on.
    if (HMODULE chain = GetModuleHandleW(L"chain_plugin.asi")) {
        typedef LONG (*Calls_t)();
        Calls_t calls = (Calls_t)GetProcAddress(chain, "ChainPlugin_Calls");
        printf("chain-calls %ld\n", calls ? calls() : -1L);
    }
    return 0;
}

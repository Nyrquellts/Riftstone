// Riftstone runtime: live stats for Riftstone Studio, frame timing, memory headroom, hangs.
//
// A 4 KB block of shared memory named Local\RiftstoneLive holds what the game is doing right now:
// address space used and the largest free block (a 32-bit game crashes when that runs out), frame
// times from Direct3D 9's Present, the loader's counters, the plugins, and (DDDA build 2364871)
// how many enemy slots are in use.  Studio and `Riftstone.cmd live` read it; nothing leaves the PC
// and no port is opened.  The layout is fixed and versioned (LiveBlock below; riftstone/runtime.py
// reads the same offsets).
//
// Frame timing: the game's import of d3d9!Direct3DCreate9 is hooked, then CreateDevice in the
// Direct3D object's function table, then Present and Reset in the device's.  No engine address;
// it works the same under DXVK or an overlay.  [live] frame_stats = 0 leaves Direct3D alone (and
// so the in-game panel, overlay.cpp, which draws from the same Present, and the pool counters of
// graphics.cpp), except that [d3d9] chain still takes the game's Direct3DCreate9.
//
// Memory: address space, commit and the largest free block every second, a verdict for the session
// (MemVerdict), and the pressure watch ([memory]): when the game nears the ceiling it logs what holds
// the memory and what would help.  It flushes nothing: the engine keeps no unused resources to let go
// of (sResource::release deletes a resource the moment its count reaches zero, docs/re-native.md).
//
// Hangs: no Present for [live] hang_seconds (20) while the game window is in front writes one
// hang report (stability.cpp).  At a normal exit a summary line goes to loader.log, ending with what
// closed the game (session.cpp).
#include "runtime.h"
#include <d3d9.h>
#include <psapi.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

volatile LONG g_frames = 0;
volatile LONG g_fallbacks = 0;
HWND g_gameWindow = NULL;

#pragma pack(push, 4)
struct LiveBlock {                       // offsets are the contract with riftstone/runtime.py
    char magic[8];                       // 0x000 "RSLIVE1"
    uint32_t version;                    // 0x008 1
    uint32_t size;                       // 0x00C sizeof(LiveBlock)
    uint32_t pid;                        // 0x010
    uint32_t game;                       // 0x014 0 other, 1 DDDA, 2 DDO
    uint32_t exeTimestamp;               // 0x018
    uint32_t flags;                      // 0x01C bit0 known build, bit1 safe mode, bit2 frame timing on,
                                         //       bit3 address space low, bit4 hang now, bit5 exited,
                                         //       bit6 large-address aware, bit7 memory pressure now,
                                         //       bit8 Direct3D textures and buffers counted
    volatile uint32_t seq;               // 0x020 odd while being written
    uint32_t uptimeMs;                   // 0x024
    uint64_t updateFiletime;             // 0x028 UTC
    uint64_t vaTotal, vaUsed, vaLargestFree, vaUsedPeak, vaLargestFreeMin;   // 0x030..0x057
    uint64_t privateBytes, workingSet;   // 0x058, 0x060
    uint32_t handles;                    // 0x068
    uint32_t frames;                     // 0x06C
    uint32_t frameUsLast, frameUsAvg, frameUsP99, frameUsMax;              // 0x070..0x07F
    uint32_t stutters;                   // 0x080 frames over 2.5x the median of the last 256
    uint32_t redirects, missing, fallbacks, fatals;                        // 0x084..0x093
    uint32_t pluginsLoaded, pluginsNotLoaded;                              // 0x094, 0x098
    int32_t enemiesActive, enemiesUsable, enemySlots;                      // 0x09C..0x0A7 (-1 unknown)
    uint32_t backbufferW, backbufferH, windowed, refreshHz;                // 0x0A8..0x0B7
    uint32_t ringPos;                    // 0x0B8 next write index into frameRing
    uint32_t vramAvailMb;                // 0x0BC Direct3D 9's own estimate (approximate)
    uint32_t frameRing[256];             // 0x0C0 microseconds per frame
    char loaderVersion[16];              // 0x4C0
    char lastFile[260];                  // 0x4D0
    char lastFallback[260];              // 0x5D4
    char plugins[512];                   // 0x6D8 "name=loaded;name=failed;..."
    char notes[256];                     // 0x8D8
    int32_t stage;                       // 0x9D8 the stage the player is in (-1 unknown)
    uint32_t resourcesUsed, resourceSlots;   // 0x9DC, 0x9E0 sResource's table (0 slots: unknown)
    uint64_t privateBytesPeak;           // 0x9E4 peak commit (PeakPagefileUsage): the 4 GB-budget high-water mark
    uint32_t memVerdict;                 // 0x9EC 0 unknown, 1 headroom, 2 tight, 3 bound (see MemVerdict below)
    uint64_t d3dManaged, d3dManagedPeak; // 0x9F0, 0x9F8 bytes the game holds in D3DPOOL_MANAGED (textures, buffers)
    uint64_t d3dDefault, d3dSystem;      // 0xA00, 0xA08 in D3DPOOL_DEFAULT; in SYSTEMMEM and SCRATCH
    uint32_t d3dObjects;                 // 0xA10 textures and buffers held (the counted ones)
    uint32_t d3dProvider;                // 0xA14 0 unknown, 1 Windows' own, 2 chained ([d3d9] chain),
                                         //       3 a d3d9.dll in the game folder, 4 another module
    uint32_t pressure;                   // 0xA18 1 while the pressure watch says the game is near the ceiling
    uint32_t pressureEpisodes;           // 0xA1C how often it began this session
    char d3dPath[160];                   // 0xA20 the module that made the game's Direct3D 9 (UTF-8)
    char reserved[0x1000 - 0xAC0];       // 0xAC0
};
#pragma pack(pop)
static_assert(sizeof(LiveBlock) == 0x1000, "LiveBlock is one page");
static_assert(offsetof(LiveBlock, frameRing) == 0xC0, "layout");
static_assert(offsetof(LiveBlock, notes) == 0x8D8, "layout");
static_assert(offsetof(LiveBlock, stage) == 0x9D8, "layout");
static_assert(offsetof(LiveBlock, privateBytesPeak) == 0x9E4, "layout");
static_assert(offsetof(LiveBlock, memVerdict) == 0x9EC, "layout");
static_assert(offsetof(LiveBlock, d3dManaged) == 0x9F0, "layout");
static_assert(offsetof(LiveBlock, d3dObjects) == 0xA10, "layout");
static_assert(offsetof(LiveBlock, pressureEpisodes) == 0xA1C, "layout");
static_assert(offsetof(LiveBlock, d3dPath) == 0xA20, "layout");

static LiveBlock* g_block = NULL;
static HANDLE g_mapping = NULL;
static HANDLE g_snapshotEvent = NULL;           // Local\RiftstoneSnapshot-<pid>: riftstone snapshot sets it
static CRITICAL_SECTION g_noteLock;
static BOOL g_noteReady = FALSE;
static char g_lastFallback[260], g_notes[256];

// Frame ring, written by the thread that presents (lock-free: a torn value only skews one sample).
static uint32_t g_ring[256];
static volatile LONG g_ringPos = 0;
static LARGE_INTEGER g_qpf, g_lastPresent;
static volatile DWORD g_presentThread = 0;
static volatile LONG g_vramMb = 0;
static volatile LONG g_bbW = 0, g_bbH = 0, g_refresh = 0;
volatile LONG g_d3dWindowed = -1;               // -1 unknown, 0 fullscreen, 1 windowed
static BOOL g_frameStats = TRUE;

static uint64_t g_vaUsedPeak = 0, g_vaFreeMin = ~0ULL, g_privatePeak = 0;
static uint64_t g_frameSumUs = 0;               // for the exit summary
static uint32_t g_stutters = 0;
static BOOL g_lowWarned = FALSE;
static MemInfo g_memLatest;                     // the live thread's last sample, for the in-game panel
static BOOL g_memHave = FALSE;                  // (under g_noteLock)
static volatile LONG g_enemyPeak = -1;
static volatile LONG g_pressure = 0;            // the pressure watch: near the ceiling now
static uint32_t g_pressureEpisodes = 0;

// ---------------------------------------------------------------------------
// memory

void SampleMemory(MemInfo* m, BOOL walk) {
    memset(m, 0, sizeof *m);
    MEMORYSTATUSEX ms = {sizeof ms};
    if (GlobalMemoryStatusEx(&ms)) {
        m->vaTotal = ms.ullTotalVirtual;
        m->vaUsed = ms.ullTotalVirtual - ms.ullAvailVirtual;
        m->vaFree = ms.ullAvailVirtual;
        m->physAvail = ms.ullAvailPhys;
        m->physTotal = ms.ullTotalPhys;
    }
    PROCESS_MEMORY_COUNTERS_EX pmc = {};
    pmc.cb = sizeof pmc;
    if (K32GetProcessMemoryInfo(GetCurrentProcess(), (PROCESS_MEMORY_COUNTERS*)&pmc, sizeof pmc)) {
        m->privateBytes = pmc.PrivateUsage;
        m->peakPrivate = pmc.PeakPagefileUsage;
        m->workingSet = pmc.WorkingSetSize;
        m->peakWorkingSet = pmc.PeakWorkingSetSize;
    }
    DWORD handles = 0;
    GetProcessHandleCount(GetCurrentProcess(), &handles);
    m->handles = handles;
    if (walk) {
        SYSTEM_INFO si;
        GetSystemInfo(&si);
        BYTE* p = (BYTE*)si.lpMinimumApplicationAddress;
        BYTE* end = (BYTE*)si.lpMaximumApplicationAddress;
        MEMORY_BASIC_INFORMATION mbi;
        while (p < end && VirtualQuery(p, &mbi, sizeof mbi)) {
            if (mbi.State == MEM_FREE && mbi.RegionSize > m->vaLargestFree) m->vaLargestFree = mbi.RegionSize;
            BYTE* next = (BYTE*)mbi.BaseAddress + mbi.RegionSize;
            if (next <= p) break;
            p = next;
        }
    }
}

// Within 400 MB of the ceiling, or no free block of 96 MB left: big allocations (a texture, a
// stage's archive) start to fail, and MT Framework treats a failed allocation as fatal.
BOOL AddressSpaceLow(const MemInfo* m) {
    if (!m->vaTotal) return FALSE;
    if (m->vaTotal - m->vaUsed < (400ULL << 20)) return TRUE;
    return m->vaLargestFree && m->vaLargestFree < (96ULL << 20);
}

// A plain verdict on how close this session came to the 32-bit process's ~4 GB ceiling, from the peak
// commit (private bytes), the smallest free block seen and the least address space left (vaLeftMin, 0 when
// not measured).  It decides whether any memory work can help this setup at all: raising internal pools
// only helps with real headroom; at the ceiling only fewer in-process texture bytes do (a lower
// TextureDetail, fewer HD texture mods, or DXVK through [d3d9] chain, which keeps Direct3D's copy of the
// managed textures out of the address space).  Address space counts on its own because under DXVK those
// copies are mapped views, not commit.  The thresholds match riftstone/runtime.py memory_verdict(); keep
// the two in step.
//   0 unknown  1 headroom  2 tight  3 bound
uint32_t MemVerdict(uint64_t peakPrivate, uint64_t freeMin, uint64_t vaLeftMin) {
    if (!peakPrivate) return 0;
    uint64_t peakMb = peakPrivate >> 20, freeMb = freeMin >> 20;
    if (peakMb >= 3400 || (freeMin && freeMb <= 128) || (vaLeftMin && vaLeftMin < (400ULL << 20))) return 3;
    if (peakMb >= 2800) return 2;
    return 1;
}

BOOL MemoryPressure() { return g_pressure != 0; }

static const wchar_t* MemVerdictWord(uint32_t v) {
    return v == 3 ? L"memory-bound" : v == 2 ? L"tight" : v == 1 ? L"headroom" : L"unknown";
}

BOOL LatestMemory(MemInfo* m) {
    if (!g_noteReady) return FALSE;
    EnterCriticalSection(&g_noteLock);
    BOOL have = g_memHave;
    if (have) *m = g_memLatest;
    LeaveCriticalSection(&g_noteLock);
    return have;
}

void NoteEnemyPeak(int active) {
    LONG seen = g_enemyPeak;
    while (active > seen) {
        LONG was = InterlockedCompareExchange(&g_enemyPeak, active, seen);
        if (was == seen) break;
        seen = was;
    }
}

int EnemyPeak() { return (int)g_enemyPeak; }

// ---------------------------------------------------------------------------
// Direct3D 9: frame timing

typedef HRESULT(STDMETHODCALLTYPE* CreateDevice_t)(IDirect3D9*, UINT, D3DDEVTYPE, HWND, DWORD, D3DPRESENT_PARAMETERS*,
                                                   IDirect3DDevice9**);
typedef HRESULT(STDMETHODCALLTYPE* Present_t)(IDirect3DDevice9*, const RECT*, const RECT*, HWND, const RGNDATA*);
typedef HRESULT(STDMETHODCALLTYPE* Reset_t)(IDirect3DDevice9*, D3DPRESENT_PARAMETERS*);
typedef UINT(STDMETHODCALLTYPE* GetAvailableTextureMem_t)(IDirect3DDevice9*);
typedef HRESULT(STDMETHODCALLTYPE* TestCooperativeLevel_t)(IDirect3DDevice9*);
static Direct3DCreate9_t Real_Direct3DCreate9;
static CreateDevice_t Real_CreateDevice;
static Present_t Real_Present;
static Reset_t Real_Reset;
static TestCooperativeLevel_t Real_TestCooperativeLevel;
static void** g_d3dVtable;       // the Direct3D object's table we patched
static void** g_devVtable;       // the device's table we patched

enum { VT_CREATE_DEVICE = 16, VT_RESET = 16, VT_PRESENT = 17, VT_TEXTURE_MEM = 4, VT_TEST_COOPERATIVE_LEVEL = 3 };

static BOOL PatchSlot(void** table, int index, void* hook, void** real) {
    if (table[index] == hook) return TRUE;
    DWORD old;
    if (!VirtualProtect(&table[index], sizeof(void*), PAGE_READWRITE, &old)) return FALSE;
    *real = table[index];
    table[index] = hook;
    VirtualProtect(&table[index], sizeof(void*), old, &old);
    return TRUE;
}

static void NoteParams(const D3DPRESENT_PARAMETERS* pp, HWND focus) {
    if (!pp) return;
    g_bbW = pp->BackBufferWidth;
    g_bbH = pp->BackBufferHeight;
    g_d3dWindowed = pp->Windowed ? 1 : 0;
    g_refresh = pp->FullScreen_RefreshRateInHz;
    HWND w = pp->hDeviceWindow ? pp->hDeviceWindow : focus;
    if (w) g_gameWindow = w;
}

// What the device last told the game, for a hang report.  A game whose Present returns D3DERR_DEVICELOST asks
// for a reset and runs its frames without presenting until TestCooperativeLevel says the device can be reset
// (DDDA: docs/stability-membrane.md, "The hang of 2026-10-06 14:42"), so "no frame" alone does not say whether
// the game is stuck or waiting for its device.  Written on the presenting thread, read by the hang watch.
static volatile LONG g_presentHr = 0, g_presentBad = 0;     // the last Present's result; failures in a row
static volatile DWORD g_presentBadSince = 0;                // GetTickCount when that run of failures began
static volatile LONG g_tclHr = 0, g_tclCalls = 0, g_tclBad = 0;
static volatile DWORD g_tclAt = 0, g_tclBadSince = 0;
static volatile LONG g_resets = 0, g_resetHr = 0;

static void NoteResult(HRESULT hr, volatile LONG* last, volatile LONG* bad, volatile DWORD* since) {
    *last = hr;
    if (SUCCEEDED(hr)) {
        *bad = 0;
    } else if (InterlockedIncrement(bad) == 1) {
        *since = GetTickCount();
    }
}

static HRESULT STDMETHODCALLTYPE Hook_TestCooperativeLevel(IDirect3DDevice9* dev) {
    HRESULT hr = Real_TestCooperativeLevel(dev);
    g_tclAt = GetTickCount();
    InterlockedIncrement(&g_tclCalls);
    NoteResult(hr, &g_tclHr, &g_tclBad, &g_tclBadSince);
    return hr;
}

const wchar_t* D3dResultName(LONG hr) {
    switch (hr) {
    case D3D_OK: return L"D3D_OK";
    case D3DERR_DEVICELOST: return L"D3DERR_DEVICELOST";
    case D3DERR_DEVICENOTRESET: return L"D3DERR_DEVICENOTRESET";
    case D3DERR_DEVICEREMOVED: return L"D3DERR_DEVICEREMOVED";
    case D3DERR_DEVICEHUNG: return L"D3DERR_DEVICEHUNG";
    case D3DERR_DRIVERINTERNALERROR: return L"D3DERR_DRIVERINTERNALERROR";
    case D3DERR_OUTOFVIDEOMEMORY: return L"D3DERR_OUTOFVIDEOMEMORY";
    case D3DERR_INVALIDCALL: return L"D3DERR_INVALIDCALL";
    case D3DERR_NOTAVAILABLE: return L"D3DERR_NOTAVAILABLE";
    case E_OUTOFMEMORY: return L"E_OUTOFMEMORY";
    case S_PRESENT_OCCLUDED: return L"S_PRESENT_OCCLUDED";
    case S_PRESENT_MODE_CHANGED: return L"S_PRESENT_MODE_CHANGED";
    default: return L"another result";
    }
}

BOOL GetDeviceState(DeviceState* d) {
    memset(d, 0, sizeof *d);
    if (!g_devVtable) return FALSE;
    DWORD now = GetTickCount();
    d->frames = g_frames;
    d->presentHr = g_presentHr;
    d->presentBad = g_presentBad;
    d->presentBadMs = d->presentBad ? now - g_presentBadSince : 0;
    d->tclHooked = Real_TestCooperativeLevel != NULL;
    d->tclCalls = g_tclCalls;
    d->tclHr = g_tclHr;
    d->tclAgoMs = d->tclCalls ? now - g_tclAt : 0;
    d->tclBad = g_tclBad;
    d->tclBadMs = d->tclBad ? now - g_tclBadSince : 0;
    d->resets = g_resets;
    d->resetHr = g_resetHr;
    return TRUE;
}

static HRESULT STDMETHODCALLTYPE Hook_Present(IDirect3DDevice9* dev, const RECT* src, const RECT* dst, HWND wnd, const RGNDATA* dirty) {
    LARGE_INTEGER now;
    QueryPerformanceCounter(&now);
    if (g_lastPresent.QuadPart) {
        uint64_t us = (uint64_t)(now.QuadPart - g_lastPresent.QuadPart) * 1000000ULL / (uint64_t)g_qpf.QuadPart;
        if (us > 0xFFFFFFFFULL) us = 0xFFFFFFFFULL;
        LONG i = InterlockedIncrement(&g_ringPos) - 1;
        g_ring[i & 255] = (uint32_t)us;
        g_frameSumUs += us;
    }
    g_lastPresent = now;
    g_presentThread = GetCurrentThreadId();
    LONG n = InterlockedIncrement(&g_frames);
    if ((n & 127) == 1) {   // the device is only safe to use on its own thread: here
        GetAvailableTextureMem_t mem = (GetAvailableTextureMem_t)(*(void***)dev)[VT_TEXTURE_MEM];
        g_vramMb = (LONG)(mem(dev) >> 20);
    }
    OverlayPresent(dev);    // the in-game panel, into the back buffer before it goes out
    HRESULT hr = Real_Present(dev, src, dst, wnd, dirty);
    NoteResult(hr, &g_presentHr, &g_presentBad, &g_presentBadSince);
    return hr;
}

static HRESULT STDMETHODCALLTYPE Hook_Reset(IDirect3DDevice9* dev, D3DPRESENT_PARAMETERS* pp) {
    OverlayBeforeReset(dev);
    HRESULT hr = Real_Reset(dev, pp);
    InterlockedIncrement(&g_resets);
    g_resetHr = hr;
    if (SUCCEEDED(hr)) NoteParams(pp, NULL);
    return hr;
}

static HRESULT STDMETHODCALLTYPE Hook_CreateDevice(IDirect3D9* d3d, UINT adapter, D3DDEVTYPE type, HWND focus, DWORD flags,
                                                   D3DPRESENT_PARAMETERS* pp, IDirect3DDevice9** out) {
    OverlayDeviceCreating();
    HRESULT hr = Real_CreateDevice(d3d, adapter, type, focus, flags, pp, out);
    if (SUCCEEDED(hr) && out && *out) {
        NoteParams(pp, focus);
        GraphicsDeviceCreated(*out);                    // shadow-map bound + the pool counters (graphics.cpp)
        void** table = *(void***)*out;
        if (g_frameStats && !g_devVtable) {             // frame timing only when [live] frame_stats is on
            BOOL ok = PatchSlot(table, VT_PRESENT, (void*)Hook_Present, (void**)&Real_Present) &&
                      PatchSlot(table, VT_RESET, (void*)Hook_Reset, (void**)&Real_Reset);
            if (ok) {
                // Only noted (the hang report's "graphics device"); the frame timing does not depend on it.
                if (!PatchSlot(table, VT_TEST_COOPERATIVE_LEVEL, (void*)Hook_TestCooperativeLevel,
                               (void**)&Real_TestCooperativeLevel))
                    Real_TestCooperativeLevel = NULL;
                g_devVtable = table;
            }
            LogLine(L"live     Direct3D device %lux%lu %s (flags 0x%lx): frame timing %s", pp ? pp->BackBufferWidth : 0,
                    pp ? pp->BackBufferHeight : 0, pp && pp->Windowed ? L"windowed" : L"fullscreen", flags,
                    ok ? L"on" : L"unavailable");
            if (ok) OverlayDeviceReady();
        } else if (g_frameStats && table != g_devVtable) {
            LogLine(L"live     a second kind of Direct3D device was created; frame timing stays on the first");
        }
    }
    return hr;
}

static IDirect3D9* WINAPI Hook_Direct3DCreate9(UINT sdk) {
    IDirect3D9* d3d = GraphicsCreate9(sdk, Real_Direct3DCreate9);   // [d3d9] chain, or the game's own
    if (d3d && (g_frameStats || FixesShadowSizeSet()) && !g_d3dVtable) {   // shadow_buffers needs the device too
        void** table = *(void***)d3d;
        if (PatchSlot(table, VT_CREATE_DEVICE, (void*)Hook_CreateDevice, (void**)&Real_CreateDevice)) g_d3dVtable = table;
    }
    return d3d;
}

void LiveInstallHooks() {
    QueryPerformanceFrequency(&g_qpf);
    InitializeCriticalSection(&g_noteLock);
    g_noteReady = TRUE;
    g_frameStats = IniInt(L"live", L"frame_stats", 1) != 0;
    GraphicsSettings(g_frameStats);                     // [d3d9]: the chain, and the pool counters
    if (!g_frameStats) {
        LogLine(L"live     frame timing off ([live] frame_stats = 0)");
        OverlaySettings(FALSE);
        if (GraphicsChainWanted() || FixesShadowSizeSet())   // the chain, or bounding a raised shadow map, still needs it
            HookImport("d3d9.dll", "Direct3DCreate9", (void*)Hook_Direct3DCreate9, (void**)&Real_Direct3DCreate9);
        return;
    }
    HookImport("d3d9.dll", "Direct3DCreate9", (void*)Hook_Direct3DCreate9, (void**)&Real_Direct3DCreate9);
    OverlaySettings(TRUE);
}

// The average time of the last `frames` frames (0 before any was timed).
uint32_t RecentFrameAverageUs(int frames) {
    LONG pos = g_ringPos;
    int n = pos < frames ? (int)pos : frames;
    if (n > 256) n = 256;
    if (n <= 0) return 0;
    uint64_t sum = 0;
    for (int i = 0; i < n; i++) sum += g_ring[(pos - n + i) & 255];
    return (uint32_t)(sum / (uint64_t)n);
}

// ---------------------------------------------------------------------------
// the shared block

void LiveNote(const char* key, const wchar_t* text) {
    char* dst = strcmp(key, "last_fallback") == 0 ? g_lastFallback : strcmp(key, "notes") == 0 ? g_notes : NULL;
    if (!dst || !g_noteReady) return;
    EnterCriticalSection(&g_noteLock);
    WideCharToMultiByte(CP_UTF8, 0, text, -1, dst, strcmp(key, "notes") == 0 ? (int)sizeof g_notes : (int)sizeof g_lastFallback, NULL, NULL);
    LeaveCriticalSection(&g_noteLock);
}

static int CompareU32(const void* a, const void* b) {
    uint32_t x = *(const uint32_t*)a, y = *(const uint32_t*)b;
    return x < y ? -1 : x > y;
}

static int g_resUsed = 0, g_resSlots = 0;
static BOOL g_resWarned = FALSE;

// The least address space the session had left (0: not measured yet).
static uint64_t VaLeftMin(uint64_t vaTotal) {
    if (!vaTotal || !g_vaUsedPeak) return 0;
    return g_vaUsedPeak < vaTotal ? vaTotal - g_vaUsedPeak : 1;
}

// A path as UTF-8 in a fixed field; one longer than the field keeps its end (the folder and the file).
static void Utf8Tail(const wchar_t* text, char* out, size_t cap) {
    char tmp[MAX_PATH * 4];
    int n = WideCharToMultiByte(CP_UTF8, 0, text, -1, tmp, (int)sizeof tmp, NULL, NULL);
    if (n <= 0 || cap < 8) {
        if (cap) out[0] = 0;
        return;
    }
    size_t len = (size_t)n - 1;
    if (len < cap) {
        memcpy(out, tmp, len + 1);
        return;
    }
    size_t keep = cap - 4;
    const char* tail = tmp + len - keep;
    while (keep && ((unsigned char)*tail & 0xC0) == 0x80) {   // start on a whole character
        tail++;
        keep--;
    }
    memcpy(out, "...", 3);
    memcpy(out + 3, tail, keep);
    out[3 + keep] = 0;
}

static void Publish(const MemInfo& m, BOOL hangNow) {
    LiveBlock* b = g_block;
    if (!b) return;
    int stage = -1;
    if (!CurrentStage(&stage)) stage = -1;
    // Frame statistics over the ring.
    uint32_t copy[256];
    LONG pos = g_ringPos;
    int n = pos < 256 ? (int)pos : 256;
    for (int i = 0; i < n; i++) copy[i] = g_ring[(pos - n + i) & 255];
    uint32_t last = n ? copy[n - 1] : 0, avg = 0, p99 = 0, mx = 0;
    if (n) {
        uint64_t sum = 0;
        for (int i = 0; i < n; i++) { sum += copy[i]; if (copy[i] > mx) mx = copy[i]; }
        avg = (uint32_t)(sum / n);
        uint32_t sorted[256];
        memcpy(sorted, copy, n * sizeof(uint32_t));
        qsort(sorted, n, sizeof(uint32_t), CompareU32);
        p99 = sorted[(n * 99) / 100 < n ? (n * 99) / 100 : n - 1];
    }
    int active = -1, usable = -1, slots = -1;
    if (EnemySlots(&active, &usable, &slots) && slots > 0) NoteEnemyPeak(active);   // the panel's peak
    // This thread is also the hang watch, so it never waits for the pool counters' lock: a busy lock keeps
    // the last numbers.
    static PoolStats s_pools;
    static BOOL s_counted = FALSE;
    static wchar_t s_d3dPath[MAX_PATH];
    PoolStats fresh;
    if (GraphicsPools(&fresh, FALSE)) {
        s_pools = fresh;
        s_counted = TRUE;
    }
    const PoolStats& pools = s_pools;
    BOOL counted = s_counted;
    wchar_t d3dPath[MAX_PATH];
    int provider = GraphicsProvider(d3dPath, _countof(d3dPath), FALSE);
    if (d3dPath[0]) wcscpy_s(s_d3dPath, d3dPath);
    else wcscpy_s(d3dPath, s_d3dPath);

    InterlockedIncrement((volatile LONG*)&b->seq);        // odd: writing
    MemoryBarrier();
    b->uptimeMs = (uint32_t)UptimeMs();
    FILETIME ft;
    GetSystemTimeAsFileTime(&ft);
    b->updateFiletime = ((uint64_t)ft.dwHighDateTime << 32) | ft.dwLowDateTime;
    b->flags = (g_knownBuild ? 1u : 0u) | (SafeModeActive() ? 2u : 0u) | (g_devVtable ? 4u : 0u) |
               (AddressSpaceLow(&m) ? 8u : 0u) | (hangNow ? 16u : 0u) | (g_largeAddressAware ? 64u : 0u) |
               (g_pressure ? 128u : 0u) | (counted ? 256u : 0u);
    if (m.vaTotal) {
        b->vaTotal = m.vaTotal;
        b->vaUsed = m.vaUsed;
        if (m.vaLargestFree) b->vaLargestFree = m.vaLargestFree;
        b->vaUsedPeak = g_vaUsedPeak;
        b->vaLargestFreeMin = g_vaFreeMin == ~0ULL ? 0 : g_vaFreeMin;
        b->privateBytes = m.privateBytes;
        b->workingSet = m.workingSet;
        b->handles = m.handles;
        if (m.peakPrivate > g_privatePeak) g_privatePeak = m.peakPrivate;
        b->privateBytesPeak = g_privatePeak;
        b->memVerdict = MemVerdict(g_privatePeak, g_vaFreeMin == ~0ULL ? 0 : g_vaFreeMin, VaLeftMin(m.vaTotal));
    }
    b->d3dManaged = pools.bytes[D3DPOOL_MANAGED];
    b->d3dManagedPeak = pools.peak[D3DPOOL_MANAGED];
    b->d3dDefault = pools.bytes[D3DPOOL_DEFAULT];
    b->d3dSystem = pools.bytes[D3DPOOL_SYSTEMMEM] + pools.bytes[D3DPOOL_SCRATCH];
    b->d3dObjects = pools.objects[0] + pools.objects[1] + pools.objects[2] + pools.objects[3];
    b->d3dProvider = (uint32_t)provider;
    b->pressure = g_pressure ? 1u : 0u;
    b->pressureEpisodes = g_pressureEpisodes;
    Utf8Tail(d3dPath, b->d3dPath, sizeof b->d3dPath);
    b->frames = (uint32_t)g_frames;
    b->frameUsLast = last;
    b->frameUsAvg = avg;
    b->frameUsP99 = p99;
    b->frameUsMax = mx;
    b->stutters = g_stutters;
    b->redirects = (uint32_t)g_redirects;
    b->missing = (uint32_t)g_missing;
    b->fallbacks = (uint32_t)g_fallbacks;
    b->fatals = (uint32_t)g_fatals;
    b->enemiesActive = active;
    b->enemiesUsable = usable;
    b->enemySlots = slots;
    b->backbufferW = (uint32_t)g_bbW;
    b->backbufferH = (uint32_t)g_bbH;
    b->windowed = g_d3dWindowed < 0 ? 0xFFFFFFFFu : (uint32_t)g_d3dWindowed;
    b->refreshHz = (uint32_t)g_refresh;
    b->vramAvailMb = (uint32_t)g_vramMb;
    b->ringPos = (uint32_t)(pos & 255);
    b->stage = stage;
    b->resourcesUsed = (uint32_t)g_resUsed;
    b->resourceSlots = (uint32_t)g_resSlots;
    memcpy(b->frameRing, g_ring, sizeof g_ring);
    LONG r = g_recentNext;
    if (r > 0) WideCharToMultiByte(CP_UTF8, 0, g_recent[(r - 1) % RECENT], -1, b->lastFile, sizeof b->lastFile, NULL, NULL);
    EnterCriticalSection(&g_noteLock);
    memcpy(b->lastFallback, g_lastFallback, sizeof b->lastFallback);
    memcpy(b->notes, g_notes, sizeof b->notes);
    LeaveCriticalSection(&g_noteLock);
    MemoryBarrier();
    InterlockedIncrement((volatile LONG*)&b->seq);        // even: consistent
}

static void FillStatic() {
    LiveBlock* b = g_block;
    memset(b, 0, sizeof *b);
    memcpy(b->magic, "RSLIVE1", 8);
    b->version = 1;
    b->size = sizeof(LiveBlock);
    b->pid = GetCurrentProcessId();
    b->game = (uint32_t)g_game;
    b->exeTimestamp = g_exeTimestamp;
    b->windowed = 0xFFFFFFFFu;
    b->enemiesActive = b->enemiesUsable = b->enemySlots = -1;
    b->stage = -1;
    WideCharToMultiByte(CP_UTF8, 0, RIFTSTONE_LOADER_VERSION, -1, b->loaderVersion, sizeof b->loaderVersion, NULL, NULL);
    char* p = b->plugins;
    size_t left = sizeof b->plugins;
    uint32_t loaded = 0, notLoaded = 0;
    for (int i = 0; i < g_pluginCount && left > 1; i++) {
        const PluginInfo& pi = g_pluginInfo[i];
        const char* st = pi.state == 1 ? "loaded" : pi.state == 0 ? "failed" : pi.state == -1 ? "quarantined" : "skipped";
        if (pi.state == 1) loaded++; else notLoaded++;
        char name[MAX_PATH];
        WideCharToMultiByte(CP_UTF8, 0, pi.name, -1, name, sizeof name, NULL, NULL);
        int w = _snprintf_s(p, left, _TRUNCATE, "%s%s=%s", i ? ";" : "", name, st);
        if (w < 0) break;
        p += w;
        left -= w;
    }
    b->pluginsLoaded = loaded;
    b->pluginsNotLoaded = notLoaded;
}

// Median of the ring's last 64 frames, for stutter counting.
static uint32_t RecentMedian() {
    LONG pos = g_ringPos;
    int n = pos < 64 ? (int)pos : 64;
    if (n < 16) return 0;
    uint32_t v[64];
    for (int i = 0; i < n; i++) v[i] = g_ring[(pos - n + i) & 255];
    qsort(v, n, sizeof(uint32_t), CompareU32);
    return v[n / 2];
}

// The pressure watch ([memory]): over pressure_mb of commit, or with the address space nearly used up
// (AddressSpaceLow), it logs once what holds the memory and what would help, until the game is back under
// relief_mb with room to spare.  It flushes nothing, because there is nothing safe to flush: the engine
// deletes a resource the moment its last user lets go (sResource::release, DDDA.exe 0x00DBA940), so every
// resource in memory is in use.
static void PressureTick(const MemInfo& seen, int pressureMb, int reliefMb, ULONGLONG* since) {
    if (pressureMb <= 0 || !seen.vaTotal) return;
    uint64_t commitMb = seen.privateBytes >> 20;
    BOOL low = AddressSpaceLow(&seen);
    if (!g_pressure) {
        if (commitMb < (uint64_t)pressureMb && !low) return;
        InterlockedExchange(&g_pressure, 1);
        g_pressureEpisodes++;
        *since = GetTickCount64();
        PoolStats pools;
        wchar_t path[MAX_PATH], what[768] = L"";
        int provider = GraphicsProvider(path, _countof(path), FALSE);
        uint64_t managedMb = GraphicsPools(&pools, FALSE) ? pools.bytes[D3DPOOL_MANAGED] >> 20 : 0;
        if (managedMb && provider == D3D_WINDOWS && managedMb >= 256)
            _snwprintf_s(what, _countof(what), _TRUNCATE, L" Direct3D 9 (Windows' own) holds %llu MB of managed textures "
                         L"and buffers, and Windows keeps a copy of those inside the game's address space: DXVK through "
                         L"[d3d9] chain keeps that copy out (docs/runtime.md).", managedMb);
        else if (managedMb)
            _snwprintf_s(what, _countof(what), _TRUNCATE, L" Direct3D 9 (%s) holds %llu MB of managed textures and "
                         L"buffers; fewer or smaller texture mods, or a lower TextureDetail, give the game room.",
                         GraphicsProviderName(provider), managedMb);
        else
            _snwprintf_s(what, _countof(what), _TRUNCATE, L" Fewer or smaller texture mods, or a lower TextureDetail, "
                         L"give the game room.");
        LogLine(L"memory   PRESSURE: commit %llu MB, address space %llu of %llu MB used, largest free block %llu MB (the "
                L"watch starts at %d MB of commit, or 400 MB before the end).%s The engine keeps no unused resources, so "
                L"there is nothing to flush.", commitMb, seen.vaUsed >> 20, seen.vaTotal >> 20, seen.vaLargestFree >> 20,
                pressureMb, what);
        LiveNote("notes", L"memory pressure: the game is near its address-space ceiling");
        return;
    }
    BOOL eased = commitMb < (uint64_t)reliefMb && !low && seen.vaTotal - seen.vaUsed >= (500ULL << 20) &&
                 (!seen.vaLargestFree || seen.vaLargestFree >= (128ULL << 20));
    if (!eased) return;
    InterlockedExchange(&g_pressure, 0);
    LogLine(L"memory   pressure eased after %llu s: commit %llu MB, address space %llu of %llu MB used, largest free "
            L"block %llu MB", (GetTickCount64() - *since) / 1000, commitMb, seen.vaUsed >> 20, seen.vaTotal >> 20,
            seen.vaLargestFree >> 20);
}

static DWORD WINAPI LiveThread(LPVOID) {
    int hangSeconds = IniInt(L"live", L"hang_seconds", 20);
    BOOL hangReports = IniInt(L"live", L"hang_reports", 1) != 0;
    BOOL needFront = IniInt(L"live", L"hang_needs_front", 1) != 0;   // 0 only for tests
    int pressureMb = IniInt(L"memory", L"pressure_mb", 3400);
    int reliefMb = IniInt(L"memory", L"relief_mb", 3200);
    if (pressureMb < 0 || pressureMb > 1 << 20) pressureMb = 3400;
    if (reliefMb <= 0 || reliefMb >= pressureMb) reliefMb = pressureMb > 200 ? pressureMb - 200 : pressureMb;
    if (pressureMb)
        LogLine(L"memory   pressure watch: over %d MB of commit, or 400 MB before the end of the address space "
                L"(eases under %d MB)", pressureMb, reliefMb);
    else
        LogLine(L"memory   pressure watch off ([memory] pressure_mb = 0)");
    ULONGLONG pressureSince = 0;
    LONG lastFrames = 0, countedPos = 0;
    ULONGLONG lastFrameChange = GetTickCount64();
    BOOL hangWritten = FALSE;
    MemInfo m = {};
    uint64_t lastWalk = 0;                                  // the largest free block the last walk found
    for (unsigned tick = 0;; tick++) {
        Sleep(250);
        if (tick % 4 == 0) {                                // every second
            SampleMemory(&m, tick % 8 == 0);                // the free-block walk every two
            if (m.vaUsed > g_vaUsedPeak) g_vaUsedPeak = m.vaUsed;
            if (m.vaLargestFree && m.vaLargestFree < g_vaFreeMin) g_vaFreeMin = m.vaLargestFree;
            if (m.peakPrivate > g_privatePeak) g_privatePeak = m.peakPrivate;   // peak commit (the 4 GB budget)
            EnterCriticalSection(&g_noteLock);              // for the in-game panel: this sample, with the
            uint64_t walked = g_memLatest.vaLargestFree;    // largest free block of the last walk
            g_memLatest = m;
            if (!m.vaLargestFree) g_memLatest.vaLargestFree = walked;
            g_memHave = TRUE;
            LeaveCriticalSection(&g_noteLock);
            if (tick % 8 == 0) {                             // sResource's table, every two seconds
                int used = 0, slots = 0;
                if (ResourceTable(&used, &slots)) {
                    g_resUsed = used;
                    g_resSlots = slots;
                    if (!g_resWarned && used * 10 >= slots * 9) {
                        g_resWarned = TRUE;
                        LogLine(L"memory   the game's resource table is %d of %d slots full; resources that do not fit "
                                L"are loaded again each time they are asked for", used, slots);
                    }
                }
            }
            // Judge (and report) with the last walk's largest free block: a sample without the walk has none,
            // and the line said "largest free block 0 MB".
            MemInfo seen = m;
            if (m.vaLargestFree) lastWalk = m.vaLargestFree;
            else seen.vaLargestFree = lastWalk;
            PressureTick(seen, pressureMb, reliefMb, &pressureSince);
            if (!g_lowWarned && AddressSpaceLow(&seen)) {
                g_lowWarned = TRUE;
                LogLine(L"memory   LOW: %llu MB of %llu MB address space used, largest free block %llu MB. A crash is "
                        L"likely soon; fewer or smaller texture mods, or a lower TextureDetail, give the game room.",
                        seen.vaUsed >> 20, seen.vaTotal >> 20, seen.vaLargestFree >> 20);
                LiveNote("notes", L"address space nearly used up: a crash is likely");
            }
        }
        // Stutters: frames over 2.5x the recent median, counted once each.
        LONG pos = g_ringPos;
        uint32_t med = RecentMedian();
        if (med) {
            for (LONG i = countedPos > pos - 256 ? countedPos : pos - 256; i < pos; i++)
                if (g_ring[i & 255] > med * 5 / 2 && g_ring[i & 255] > 20000) g_stutters++;
        }
        countedPos = pos;
        // Hangs: frames stopped while the game is in front, or while Windows calls its window not responding.
        // A frozen fullscreen game is usually behind the desktop by the time its player has got out of it
        // (2026-09-27: one was ended at shutdown, "terminated because it was hung", and no report was written).
        LONG frames = g_frames;
        ULONGLONG now = GetTickCount64();
        if (frames != lastFrames) {
            lastFrames = frames;
            lastFrameChange = now;
            hangWritten = FALSE;
        }
        BOOL front = !needFront || (g_gameWindow && !IsIconic(g_gameWindow) &&
                     GetAncestor(GetForegroundWindow(), GA_ROOTOWNER) == GetAncestor(g_gameWindow, GA_ROOTOWNER));
        BOOL notResponding = !front && g_gameWindow && IsHungAppWindow(g_gameWindow);
        BOOL hangNow = frames > 0 && (front || notResponding) && now - lastFrameChange >= (ULONGLONG)hangSeconds * 1000;
        if (hangNow && hangReports && !hangWritten) {
            hangWritten = TRUE;
            WriteHangReport(g_presentThread ? g_presentThread : g_mainThread, (DWORD)((now - lastFrameChange) / 1000),
                            notResponding);
        }
        Publish(m, hangNow);
        // A snapshot asked for from outside (riftstone snapshot sets the event): the same report as a hang, with
        // every thread, while the game goes on.
        if (g_snapshotEvent && WaitForSingleObject(g_snapshotEvent, 0) == WAIT_OBJECT_0)
            WriteSnapshotReport(g_presentThread ? g_presentThread : g_mainThread);
        GuardsReport();                                     // the stability membrane: new guard hits into riftstone_error.log
        if (tick % 20 == 0) CrashFilterInstall();           // stay first in line for crashes
        if (tick % 120 == 0) StabilityAlive();              // every 30 s
    }
}

void LiveStart() {
    if (!IniInt(L"live", L"enabled", 1)) {
        LogLine(L"live     off ([live] enabled = 0)");
        return;
    }
    const wchar_t* name = L"Local\\RiftstoneLive";
    g_mapping = CreateFileMappingW(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, sizeof(LiveBlock), name);
    if (g_mapping && GetLastError() == ERROR_ALREADY_EXISTS) {
        // Another copy of the game is running: keep its block, publish ours under our process id.
        CloseHandle(g_mapping);
        wchar_t own[64];
        _snwprintf_s(own, _countof(own), _TRUNCATE, L"Local\\RiftstoneLive-%lu", GetCurrentProcessId());
        g_mapping = CreateFileMappingW(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, sizeof(LiveBlock), own);
        LogLine(L"live     another game already publishes live stats; this one uses %s", own);
    }
    if (g_mapping) g_block = (LiveBlock*)MapViewOfFile(g_mapping, FILE_MAP_WRITE, 0, 0, sizeof(LiveBlock));
    if (!g_block) {
        LogLine(L"live     shared memory unavailable (error %lu); no live stats", GetLastError());
        return;
    }
    FillStatic();
    // Auto-reset: one set, one snapshot.  Named by the process id, so a second game has its own.
    wchar_t snap[64];
    _snwprintf_s(snap, _countof(snap), _TRUNCATE, L"Local\\RiftstoneSnapshot-%lu", GetCurrentProcessId());
    g_snapshotEvent = CreateEventW(NULL, FALSE, FALSE, snap);
    HANDLE t = CreateThread(NULL, 0, LiveThread, NULL, 0, NULL);
    if (t) CloseHandle(t);
    LogLine(L"live     publishing to Local\\RiftstoneLive (memory, frame times, counters) for Studio");
    if (g_snapshotEvent) LogLine(L"live     snapshots on request: %s (riftstone snapshot)", snap);
}

void LiveStop() {
    GuardsReport();                                         // flush any last guard hits to the error log
    MemInfo m;
    SampleMemory(&m, FALSE);
    if (m.vaUsed > g_vaUsedPeak) g_vaUsedPeak = m.vaUsed;
    if (m.peakPrivate > g_privatePeak) g_privatePeak = m.peakPrivate;
    LONG frames = g_frames;
    ULONGLONG up = UptimeMs();
    double avgMs = frames > 1 ? (double)g_frameSumUs / 1000.0 / (double)(frames - 1) : 0.0;
    const wchar_t* why = SessionEndDescription();
    uint64_t freeMin = g_vaFreeMin == ~0ULL ? 0 : g_vaFreeMin;
    uint32_t verdict = MemVerdict(g_privatePeak, freeMin, VaLeftMin(m.vaTotal));
    wchar_t d3d[160] = L"", pressure[64] = L"", path[MAX_PATH];
    PoolStats pools;
    // At process exit the other threads are gone and one may have died holding the counters' lock: never wait.
    int provider = GraphicsProvider(path, _countof(path), FALSE);
    if (GraphicsPools(&pools, FALSE))
        _snwprintf_s(d3d, _countof(d3d), _TRUNCATE, L"; Direct3D 9 %s, managed textures and buffers peak %llu MB",
                     GraphicsProviderName(provider), pools.peak[D3DPOOL_MANAGED] >> 20);
    else if (provider != D3D_UNKNOWN)
        _snwprintf_s(d3d, _countof(d3d), _TRUNCATE, L"; Direct3D 9 %s", GraphicsProviderName(provider));
    if (g_pressureEpisodes)
        _snwprintf_s(pressure, _countof(pressure), _TRUNCATE, L"; memory pressure %lu time%s", (unsigned long)g_pressureEpisodes,
                     g_pressureEpisodes == 1 ? L"" : L"s");
    // The stability membrane: every guard that caught something this session, with its count (riftstone_error.log has each).
    wchar_t guards[256] = L"";
    for (int i = 0, len = 0; i < GUARD_COUNT && len < 200; i++) {
        LONG h = GuardHits(i);
        if (h <= 0) continue;
        len += _snwprintf_s(guards + len, _countof(guards) - len, _TRUNCATE, L"%s%hs %ld", len ? L", " : L"; guards caught: ",
                            GuardKeyA(i), h);
    }
    LogLine(L"summary  ran %llu min %llu s; %ld frames (average %.2f ms, %.1f fps); %lu stutters; address space peak "
            L"%llu MB, peak commit %llu MB, smallest free block %llu MB (memory %s); %ld overlay redirects, %ld missing "
            L"files, %ld stand-ins, %ld fatal errors%s%s%s%s%s",
            up / 60000, (up / 1000) % 60, frames, avgMs, avgMs > 0 ? 1000.0 / avgMs : 0.0, g_stutters,
            g_vaUsedPeak >> 20, g_privatePeak >> 20, freeMin >> 20, MemVerdictWord(verdict), g_redirects, g_missing,
            g_fallbacks, g_fatals, d3d, pressure, guards, why[0] ? L"; ended by: " : L"", why);
    if (g_block) {
        InterlockedIncrement((volatile LONG*)&g_block->seq);
        g_block->flags |= 32u;   // exited
        InterlockedIncrement((volatile LONG*)&g_block->seq);
    }
}

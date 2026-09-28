// Riftstone runtime: what the loader's parts share.
//
// loader.cpp    start-up, settings, the log, import-table hooks, the overlay, plugins, the dinput8 proxy
// stability.cpp crash / fatal-error / hang reports, safe mode and plugin quarantine
// live.cpp      live stats in shared memory for Studio, frame timing at Present, memory headroom,
//               the exit summary
// fixes.cpp     game detection, engine class names from the type info, the missing-texture guard,
//               window fixes, the frame-rate ceiling, save backups
// session.cpp   why the game closed: its window's close paths and its own exit, for loader.log and
//               runtime-state.ini
// overlay.cpp   the in-game diagnostics panel (F10), drawn at Present
// graphics.cpp  Direct3D 9: the runtime the game gets (Windows' own, or one chained from the game folder
//               such as DXVK) and the textures and buffers it holds, by pool
#pragma once
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <stdint.h>

#define RIFTSTONE_VERSION_A "1.0.1"             // the one version: the log, reports, the live page, the panel
#define RIFTSTONE_LOADER_VERSION L"" RIFTSTONE_VERSION_A

// ---- loader.cpp -------------------------------------------------------------------------------
extern wchar_t g_root[MAX_PATH];        // game folder, no trailing slash
extern wchar_t g_stateDir[MAX_PATH];    // <root>\riftstone
extern wchar_t g_logDir[MAX_PATH];      // <root>\riftstone\logs
extern wchar_t g_ini[MAX_PATH];         // <root>\riftstone_loader.ini
extern HMODULE g_self;
extern DWORD g_mainThread;              // the thread that loaded us: the game's main thread
extern ULONGLONG g_startTick;
extern volatile LONG g_redirects;
extern volatile LONG g_missing;         // read-only opens under nativePC that found no file

void LogLine(const wchar_t* fmt, ...);
int IniInt(const wchar_t* section, const wchar_t* key, int def);
void IniStr(const wchar_t* section, const wchar_t* key, const wchar_t* def, wchar_t* out, DWORD cap);
ULONGLONG UptimeMs();
void Stamp(wchar_t* out, size_t cap);   // 20260925-142233 (local time)

// Register an import-table hook in the game executable.  Returns TRUE when the game imports it;
// *real always ends up callable (the game's current target, or the export itself).
BOOL HookImport(const char* dll, const char* func, void* hook, void** real);

typedef HANDLE(WINAPI* CreateFileW_t)(LPCWSTR, DWORD, DWORD, LPSECURITY_ATTRIBUTES, DWORD, DWORD, HANDLE);
typedef DWORD(WINAPI* GetFileAttributesW_t)(LPCWSTR);
extern CreateFileW_t Real_CreateFileW;          // never redirected: the loader's own file access
extern GetFileAttributesW_t Real_GetFileAttributesW;

// Last files the game opened, and the last ones it looked for and did not find (rings).
#define RECENT 32
extern wchar_t g_recent[RECENT][MAX_PATH];
extern volatile LONG g_recentNext;
#define MISSING_RING 16
extern wchar_t g_missingRing[MISSING_RING][MAX_PATH];
extern volatile LONG g_missingNext;

// Plugins the loader brought in, for reports and the live view.
#define MAX_PLUGINS 64
struct PluginInfo {
    wchar_t name[MAX_PATH];
    HMODULE module;
    DWORD size;           // image size, for "is this address inside the plugin"
    int state;            // 1 loaded, 0 failed, -1 quarantined, -2 skipped (safe mode)
};
extern PluginInfo g_pluginInfo[MAX_PLUGINS];
extern int g_pluginCount;

// ---- stability.cpp ----------------------------------------------------------------------------
void StabilityStart();                          // before plugins: rotate reports, read the last session
BOOL PluginAllowed(const wchar_t* name, const wchar_t* fullPath);   // quarantine + safe mode
BOOL OverlayAllowed();                          // FALSE in safe mode
BOOL SafeModeActive();
void StabilityAlive();                          // the live thread says the game is still running
void StabilityCleanExit();                      // from DllMain at process exit
// What closed the game (session.cpp), into runtime-state.ini [session] at once.
void StabilityRecordEnd(const wchar_t* code, const wchar_t* detail, ULONGLONG uptimeMs);
LONG WINAPI CrashFilter(EXCEPTION_POINTERS* ep);
void CrashFilterInstall();                      // ours first, the game's (and anyone's) chained after
void StabilityInstallHooks();                   // SetUnhandledExceptionFilter, MessageBoxA
void WriteHangReport(DWORD thread, DWORD seconds, BOOL notResponding);
void WriteSnapshotReport(DWORD thread);   // riftstone snapshot: Local\RiftstoneSnapshot-<pid>, set from outside
extern volatile LONG g_fatals;

// ---- live.cpp ---------------------------------------------------------------------------------
struct MemInfo {
    uint64_t vaTotal, vaUsed, vaFree, vaLargestFree;     // this process's address space
    uint64_t privateBytes, peakPrivate, workingSet, peakWorkingSet;
    uint64_t physAvail, physTotal;
    uint32_t handles;
};
void SampleMemory(MemInfo* m, BOOL walk);       // walk: find the largest free block (VirtualQuery)
BOOL AddressSpaceLow(const MemInfo* m);         // close enough to 4 GB that a crash is likely
// headroom / tight / bound (1..3, 0 unknown) from the session's peak commit, its smallest largest-free block
// and the least address space it had left (0: not measured); runtime.py memory_verdict() agrees.
uint32_t MemVerdict(uint64_t peakPrivate, uint64_t freeMin, uint64_t vaLeftMin);
BOOL MemoryPressure();                          // the pressure watch ([memory]) says the game is near the ceiling now
void LiveStart();                               // shared memory + the sampling thread
void LiveStop();                                // exit summary
void LiveInstallHooks();                        // Direct3DCreate9 -> Present timing
void LiveNote(const char* key, const wchar_t* text);   // "last_fallback" / "notes"
BOOL LatestMemory(MemInfo* m);                  // the live thread's last sample (FALSE: none yet); its largest
                                                // free block is from the last walk (every 2 s)
uint32_t RecentFrameAverageUs(int frames);      // the average of the last frames' times (0: none timed yet)
void NoteEnemyPeak(int active);                 // the most enemies at once this session
int EnemyPeak();                                // -1 unknown
extern volatile LONG g_frames;
extern volatile LONG g_fallbacks;
extern HWND g_gameWindow;
extern volatile LONG g_d3dWindowed;             // -1 unknown, 0 fullscreen, 1 windowed (from CreateDevice)

// ---- fixes.cpp --------------------------------------------------------------------------------
enum GameKind { GAME_OTHER = 0, GAME_DDDA = 1, GAME_DDO = 2 };
extern GameKind g_game;
extern BOOL g_knownBuild;                        // the exact build Riftstone's engine facts are for
extern BOOL g_largeAddressAware;                 // the exe's IMAGE_FILE_LARGE_ADDRESS_AWARE (4 GB, not 2 GB)
extern DWORD g_exeTimestamp;
extern wchar_t g_exeName[64];
void DetectGame();
const wchar_t* GameName();
BOOL RuntimeActive();                            // FALSE in other programs (a launcher): pass-through only
// The engine class of an object (MT Framework type info), or FALSE.  Reads only; never faults.
BOOL ClassNameOf(const void* object, char* out, size_t cap);
void FixesInstallHooks();                       // window fixes
void FixesApplyPatches();                       // byte-verified engine patches (known build only)
void BackupSaves();                             // before the game reads its save
// Enemies active / slots usable in sSetManager (DDDA build 2364871 only); FALSE when unknown.
BOOL EnemySlots(int* active, int* usable, int* slots);
// The current stage number, and sResource's table use (DDDA build 2364871 only); FALSE when unknown.
BOOL CurrentStage(int* stage);
BOOL ResourceTable(int* used, int* size);
// The guard, asked when a read-only open under nativePC finds no file: the resource's own bytes from its
// archive (resources.cpp), else a stand-in for a texture.
HANDLE GuardOpen(const wchar_t* fullPath, DWORD access, DWORD share, LPSECURITY_ATTRIBUTES sa,
                 DWORD disposition, DWORD flags, HANDLE templ);
BOOL InGameImage(DWORD_PTR v);                  // inside the game's executable image
// The stability membrane (docs/stability-membrane.md): every guard's hits, counted where the guard acts and
// written to riftstone\logs\riftstone_error.log by GuardsReport.  The order is the live page's contract with
// riftstone/runtime.py (GUARD_KEYS); append only.
enum GuardId {
    GUARD_MISSING_TEXTURES, GUARD_FROM_ARCHIVES, GUARD_RAGDOLL_BODIES, GUARD_PARTICLES, GUARD_SHADOW_BUFFERS,
    GUARD_BROKEN_TEXTURES, GUARD_GUI_TEXT, GUARD_COUNT
};
void GuardsInit();                              // after DetectGame: the [guard] keys, a fresh error log
BOOL GuardOn(int id);
void GuardHit(int id, DWORD_PTR where, const wchar_t* detail);  // detail may be NULL; cheap, any thread
LONG GuardHits(int id);
DWORD GuardLastWhere(int id);
const char* GuardKeyA(int id);
void GuardsReport();                            // new hits into the error log (the live thread, and at exit)
// A loose texture under nativePC or the overlay that does not fit its file (a truncated or garbled .tex):
// TRUE and why; the file hook then answers the open as if the file were missing.
BOOL TextureFileBroken(const wchar_t* path, wchar_t* why, size_t cap);
// The device the game created: its limits bound the shadow sizes (graphics.cpp calls it).
void FixesDeviceCreated(DWORD maxTextureWidth, DWORD maxTextureHeight);
// Whether [render] shadow_map_size was set (read from the ini): live.cpp then installs the device hook even with
// frame timing off, so FixesDeviceCreated can still bound a raised map to the GPU.
BOOL FixesShadowSizeSet();
// The game's exit (DDDA build 2364871, exit_sites.h): 1 set, 0 clear, -1 unknown or another build.
BOOL ExitSitesVerified();
int GameQuitFlag();                             // sApp+0x266C: the game's own exit (or its loop ending)
int GameExitRequested();                        // sMain+0x34: its exit request ran

// ---- resources.cpp ----------------------------------------------------------------------------
void ResourcesInit();                           // [guard] from_archives (Dark Arisen)
// A resource the game asks for loose under nativePC (fullPath, normalised) before its archive was read:
// a read-only handle to its own bytes, taken from that archive; INVALID_HANDLE_VALUE when none has it.
HANDLE ArchiveOpen(const wchar_t* fullPath, LPSECURITY_ATTRIBUTES sa, DWORD flags);
// loader.cpp, for it: <root>\nativePC\, and <root>\riftstone\overlay\ while the overlay serves files (else NULL).
const wchar_t* NativePrefix();
const wchar_t* OverlayRootIfOn();

// ---- session.cpp ------------------------------------------------------------------------------
void SessionStart();                            // [loader] exit_reason
BOOL SessionWatchTick();                        // every 50 ms from the watchdog: the window, the quit flag
                                                // (FALSE: the watch is off and needs no more ticks)
void SessionFinalize();                         // at a normal exit: the reason, when nothing named one yet
const wchar_t* SessionEndDescription();         // what closed the game, for the exit summary ("" none)
const wchar_t* SessionEndText(const wchar_t* code);   // a recorded reason's words ("" for an unknown code)

// ---- overlay.cpp ------------------------------------------------------------------------------
struct IDirect3DDevice9;
void OverlaySettings(BOOL direct3d);            // [overlay]; direct3d: Present is hooked ([live] frame_stats)
void OverlayDeviceCreating();                   // before CreateDevice: let go of the previous device's objects
void OverlayDeviceReady();                      // a device's Present is hooked: the panel is available
void OverlayPresent(IDirect3DDevice9* dev);     // before the real Present: the key, and the panel when shown
void OverlayBeforeReset(IDirect3DDevice9* dev); // before the real Reset: the state block goes

// ---- graphics.cpp -----------------------------------------------------------------------------
struct IDirect3D9;
typedef IDirect3D9*(WINAPI* Direct3DCreate9_t)(UINT);
enum D3DProvider { D3D_UNKNOWN = 0, D3D_WINDOWS = 1, D3D_CHAINED = 2, D3D_GAME_FOLDER = 3, D3D_OTHER = 4 };
struct PoolStats {                              // by D3DPOOL: 0 default, 1 managed, 2 system memory, 3 scratch
    uint64_t bytes[4], peak[4];
    uint32_t objects[4];
    uint32_t untracked;                         // objects the counters could not follow (not in bytes[])
};
void GraphicsSettings(BOOL counting);           // [d3d9] (counting: [live] frame_stats, which hooks the device)
BOOL GraphicsChainWanted();                     // [d3d9] chain names a DLL: Direct3DCreate9 needs its hook
IDirect3D9* GraphicsCreate9(UINT sdk, Direct3DCreate9_t real);   // the chained runtime's object, or the real one's
void GraphicsDeviceCreated(IDirect3DDevice9* dev);               // the pool counters' device hooks
// FALSE: not counting (off, or no device yet); wait FALSE never blocks (crash reports), and may say FALSE.
BOOL GraphicsPools(PoolStats* out, BOOL wait = TRUE);
int GraphicsProvider(wchar_t* path, size_t cap, BOOL wait = TRUE);   // D3DProvider, and the module's path
const wchar_t* GraphicsProviderName(int provider);

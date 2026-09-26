// Riftstone runtime: which game this is, and the fixes that make it easier to live with.
//
//   game detection     DDDA.exe / DDO.exe by name; the exact DDDA build by its PE time stamp.  Any
//                      other program that loads dinput8 from the game folder (DDO's launcher) gets
//                      DirectInput passed through and nothing else ([loader] any_program = 1 to
//                      change that).
//   class names        MT Framework objects carry their type: vtable slot 4 is getDTI, a
//                      `mov eax, <MtDTI>; ret`, and MtDTI +4 is the class name.  Crash and hang
//                      reports name the objects in registers and on the stack from that, reading
//                      only (both games share the layout).
//   missing textures   [guard] missing_textures: a texture the game looks for under nativePC and
//                      does not find gets a neutral 4x4 stand-in instead of "Fatal error: Failed
//                      open file", and loader.log names it.
//   window             [window] borderless: the game's windowed mode without a frame, covering the
//                      monitor.  [window] background_run: keep running when alt-tabbed (windowed or
//                      borderless only), with the cursor released while the game is behind.
//   frame-rate ceiling [fps] max_fps (DDDA build 2364871): the options menu's "Variable" frame rate
//                      means this many fps instead of 150.  Two byte-verified instructions are
//                      pointed at the setting; the game's shared 150.0 constant is left alone
//                      (two unrelated systems read it).
//   save backups       [saves] backup (DDDA): a copy of the save folder before the game reads it,
//                      in %LOCALAPPDATA%\Riftstone\saves, newest [saves] keep kept.
//   enemy slots        (DDDA build 2364871) how many of sSetManager's enemy slots are in use, for
//                      the live view.
//   stage, resources   (DDDA build 2364871) the current stage (the game's own reader) and how full
//                      sResource's 16,384-slot table is, for the live view and the reports.
//   shadow map size    [render] shadow_map_size (DDDA build 2364871): ShadowQuality=HIGH draws
//                      sun shadows at this size instead of 2048 (one table entry, byte-verified).
//   the game's exit    (DDDA build 2364871, byte-verified: exit_sites.h) sApp's quit flag and sMain's
//                      exit request, read for "why the game closed" (session.cpp).
#include "runtime.h"
#include "exit_sites.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

GameKind g_game = GAME_OTHER;
BOOL g_knownBuild = FALSE;
BOOL g_largeAddressAware = FALSE;
DWORD g_exeTimestamp = 0;
wchar_t g_exeName[64];
static DWORD_PTR g_imageBase, g_imageEnd;

static const DWORD DDDA_TIMESTAMP = 0x5A314C31;   // Steam build 2364871 (loader.log, 2026-09-24)

// ---------------------------------------------------------------------------
// game detection

void DetectGame() {
    HMODULE exe = GetModuleHandleW(NULL);
    IMAGE_NT_HEADERS* nt = (IMAGE_NT_HEADERS*)((BYTE*)exe + ((IMAGE_DOS_HEADER*)exe)->e_lfanew);
    g_exeTimestamp = nt->FileHeader.TimeDateStamp;
    // Without the flag Windows gives a 32-bit program 2 GB of address space instead of 4 GB (Steam's
    // DDDA.exe, build 2364871, has it).  Read from the header as it is mapped, so it is the running exe's.
    g_largeAddressAware = (nt->FileHeader.Characteristics & IMAGE_FILE_LARGE_ADDRESS_AWARE) != 0;
    g_imageBase = (DWORD_PTR)exe;
    g_imageEnd = g_imageBase + nt->OptionalHeader.SizeOfImage;
    wchar_t path[MAX_PATH];
    GetModuleFileNameW(NULL, path, MAX_PATH);
    const wchar_t* name = wcsrchr(path, L'\\');
    wcsncpy_s(g_exeName, _countof(g_exeName), name ? name + 1 : path, _TRUNCATE);
    if (_wcsicmp(g_exeName, L"DDDA.exe") == 0) g_game = GAME_DDDA;
    else if (_wcsicmp(g_exeName, L"DDO.exe") == 0) g_game = GAME_DDO;
    g_knownBuild = g_game == GAME_DDDA && g_exeTimestamp == DDDA_TIMESTAMP;
}

const wchar_t* GameName() {
    switch (g_game) {
    case GAME_DDDA: return L"Dragon's Dogma: Dark Arisen";
    case GAME_DDO: return L"Dragon's Dogma Online";
    default: return L"another program";
    }
}

BOOL RuntimeActive() { return g_game != GAME_OTHER || IniInt(L"loader", L"any_program", 0) != 0; }

// ---------------------------------------------------------------------------
// reading memory that may not be there

// Every region the range covers must be committed and readable: VirtualQuery can split one allocation into
// several regions (seen under load in the harness: a one-page region inside the stand-in sSetManager, which
// made the enemy count read as unknown while the whole range was readable).
static BOOL Readable(const void* p, size_t n) {
    const DWORD readable = PAGE_READONLY | PAGE_READWRITE | PAGE_WRITECOPY | PAGE_EXECUTE_READ |
                           PAGE_EXECUTE_READWRITE | PAGE_EXECUTE_WRITECOPY;
    const BYTE* at = (const BYTE*)p;
    const BYTE* end = at + n;
    if (!p || end < at) return FALSE;
    do {
        MEMORY_BASIC_INFORMATION mbi;
        if (!VirtualQuery(at, &mbi, sizeof mbi)) return FALSE;
        if (mbi.State != MEM_COMMIT || (mbi.Protect & (PAGE_NOACCESS | PAGE_GUARD)) || !(mbi.Protect & readable))
            return FALSE;
        at = (const BYTE*)mbi.BaseAddress + mbi.RegionSize;
    } while (at < end);
    return TRUE;
}

static BOOL InImage(DWORD_PTR v) { return v >= g_imageBase && v < g_imageEnd; }

BOOL InGameImage(DWORD_PTR v) { return InImage(v); }

static BOOL ClassNameUnsafe(const void* object, char* out, size_t cap) {
    if (!Readable(object, sizeof(DWORD_PTR))) return FALSE;
    DWORD_PTR vt = *(const DWORD_PTR*)object;
    if (!InImage(vt) || !Readable((const void*)vt, 5 * sizeof(DWORD_PTR))) return FALSE;
    DWORD_PTR fn = ((const DWORD_PTR*)vt)[4];                 // MtObject::getDTI
    if (!InImage(fn) || !Readable((const void*)fn, 6)) return FALSE;
    const BYTE* code = (const BYTE*)fn;
    if (code[0] != 0xB8 || code[5] != 0xC3) return FALSE;     // mov eax, imm32 ; ret
    DWORD_PTR dti = *(const DWORD_PTR*)(code + 1);
    if (!InImage(dti) || !Readable((const void*)dti, 8)) return FALSE;
    const char* name = *(const char* const*)(dti + 4);        // MtDTI::mName
    if (!InImage((DWORD_PTR)name) || !Readable(name, 1)) return FALSE;
    MEMORY_BASIC_INFORMATION mbi;
    VirtualQuery(name, &mbi, sizeof mbi);
    size_t room = (size_t)((const BYTE*)mbi.BaseAddress + mbi.RegionSize - (const BYTE*)name);
    size_t i = 0;
    for (; i + 1 < cap && i < room && name[i]; i++) {
        char c = name[i];
        if (c < 0x20 || c > 0x7E) return FALSE;
        out[i] = c;
    }
    out[i] = 0;
    return i >= 2 && (i < room);
}

BOOL ClassNameOf(const void* object, char* out, size_t cap) {
    if (g_game == GAME_OTHER || !cap) return FALSE;
    __try {
        return ClassNameUnsafe(object, out, cap);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return FALSE;
    }
}

// ---------------------------------------------------------------------------
// enemy slots (DDDA build 2364871; docs/re-enemy-cap.md)

static const DWORD_PTR SET_MANAGER = 0x018FA504;       // sSetManager instance pointer
static const DWORD_PTR VT_UNIT_DATA = 0x01562414;      // sSetManager::cUnitData
static const DWORD UNIT_NUM_ENEMY = 0x1B8D0, SLOTS_VANILLA = 0x844, SLOTS_MOVED = 0x1B950, SLOT_SIZE = 0x20;

static BOOL EnemySlotsUnsafe(DWORD base, int n, int* active, int* usable) {
    DWORD_PTR mgr = *(const DWORD_PTR*)SET_MANAGER;
    if (!mgr || !Readable((const void*)mgr, UNIT_NUM_ENEMY + 4)) return FALSE;
    if (!Readable((const void*)(mgr + base), (size_t)n * SLOT_SIZE)) return FALSE;
    int count = 0;
    for (int i = 0; i < n; i++) {
        const BYTE* s = (const BYTE*)mgr + base + (DWORD)i * SLOT_SIZE;
        if (*(const DWORD_PTR*)s == VT_UNIT_DATA && *(const DWORD_PTR*)(s + 4)) count++;
    }
    *active = count;
    *usable = *(const int*)(mgr + UNIT_NUM_ENEMY);
    return TRUE;
}

BOOL EnemySlots(int* active, int* usable, int* slots) {
    if (!g_knownBuild) return FALSE;
    int n = 10;
    DWORD base = SLOTS_VANILLA;
    if (HMODULE cap = GetModuleHandleW(L"enemy_cap.asi")) {
        // enemy_cap moves the slots to the manager's tail; it says how many through this export.
        typedef int (*Slots_t)();
        Slots_t f = (Slots_t)GetProcAddress(cap, "EnemyCap_Slots");
        if (!f) return FALSE;              // an enemy_cap without it: the layout is not known here
        int k = f();
        if (k > 0) {
            n = k;
            base = SLOTS_MOVED;
        }
    }
    if (!Readable((const void*)SET_MANAGER, sizeof(DWORD_PTR))) return FALSE;
    __try {
        if (!EnemySlotsUnsafe(base, n, active, usable)) return FALSE;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return FALSE;
    }
    *slots = n;
    return TRUE;
}

// ---------------------------------------------------------------------------
// the current stage and the resource table (DDDA build 2364871)

static const DWORD_PTR S_AREA = 0x018D099C;            // sArea instance pointer
static const DWORD_PTR S_RESOURCE = 0x018D0AA0;        // sResource instance pointer
static const DWORD RES_TABLE = 0x40D8, RES_SLOTS = 2048 * 8;   // sResource::registTable (0x00DB9B70)

static BOOL CurrentStageUnsafe(int* stage) {
    DWORD_PTR area = *(const DWORD_PTR*)S_AREA;
    if (!area || !Readable((const void*)(area + 0x3834), 4)) return FALSE;
    DWORD_PTR now = *(const DWORD_PTR*)(area + 0x3834);
    if (!now || !Readable((const void*)now, 0x728)) return FALSE;
    if (!*(const BYTE*)(now + 0x20)) return FALSE;              // not set up yet
    *stage = *(const int*)(now + 0x724);
    return TRUE;
}

// The stage the player is in, read the way the game's own reader (0x005BAF40) does.  Advisory: an
// area change can free the object between two reads, which the guard turns into "unknown".
BOOL CurrentStage(int* stage) {
    if (!g_knownBuild || !Readable((const void*)S_AREA, 4)) return FALSE;
    __try {
        return CurrentStageUnsafe(stage);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return FALSE;
    }
}

static BOOL ResourceTableUnsafe(int* used) {
    DWORD_PTR res = *(const DWORD_PTR*)S_RESOURCE;
    if (!res || !Readable((const void*)(res + RES_TABLE), RES_SLOTS * 4)) return FALSE;
    const DWORD_PTR* slot = (const DWORD_PTR*)(res + RES_TABLE);
    int n = 0;
    for (DWORD i = 0; i < RES_SLOTS; i++)
        if (slot[i]) n++;
    *used = n;
    return TRUE;
}

// How many of sResource's 16,384 table slots (2,048 buckets of 8) hold a resource.  When a bucket
// is full the game does not register the resource at all, so a later request loads it again.
BOOL ResourceTable(int* used, int* size) {
    if (!g_knownBuild || !Readable((const void*)S_RESOURCE, 4)) return FALSE;
    __try {
        if (!ResourceTableUnsafe(used)) return FALSE;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return FALSE;
    }
    *size = (int)RES_SLOTS;
    return TRUE;
}

// ---------------------------------------------------------------------------
// the game's exit (DDDA build 2364871; exit_sites.h)

static volatile LONG g_exitSites = -1;             // -1 not checked yet, 0 the code differs, 1 verified

// Every site of exit_sites.h holds the bytes build 2364871 has.  Checked once, at the first question.
BOOL ExitSitesVerified() {
    if (!g_knownBuild) return FALSE;
    LONG v = g_exitSites;
    if (v < 0) {
        v = 1;
        for (const ExitSite& s : EXIT_SITES) {
            const void* at = (const void*)(DWORD_PTR)s.va;
            if (!Readable(at, s.n) || memcmp(at, s.b, s.n) != 0) {
                LogLine(L"exit     the code at 0x%08lx (%S) is not what build 2364871 has; the game's own exit is not "
                        L"told apart", s.va, s.what);
                v = 0;
                break;
            }
        }
        InterlockedExchange(&g_exitSites, v);
    }
    return v == 1;
}

// A byte flag of the engine object whose instance pointer is at `holder`, while the object still
// carries its class's table: 1 set, 0 clear, -1 unknown (not built yet, or gone).
static int ObjectFlagUnsafe(DWORD_PTR holder, DWORD_PTR vtable, DWORD offset) {
    DWORD_PTR obj = *(const DWORD_PTR*)holder;
    // The two words that are read, each on its own: an object can span regions of different protection.
    if (!obj || !Readable((const void*)obj, sizeof(DWORD_PTR)) || *(const DWORD_PTR*)obj != vtable) return -1;
    if (!Readable((const void*)(obj + offset), 1)) return -1;
    return *(const BYTE*)(obj + offset) != 0;
}

static int ObjectFlag(DWORD_PTR holder, DWORD_PTR vtable, DWORD offset) {
    if (!ExitSitesVerified() || !Readable((const void*)holder, sizeof(DWORD_PTR))) return -1;
    __try {
        return ObjectFlagUnsafe(holder, vtable, offset);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return -1;
    }
}

// sApp+0x266C: the game's own exit set it (or the loop, as it ends).  sApp lives on WinMain's stack,
// so after WinMain returns the answer is "unknown".
int GameQuitFlag() { return ObjectFlag(DDDA_SAPP, DDDA_SAPP_VTABLE, DDDA_SAPP_QUIT); }

// sMain+0x34: set by the exit request (0x00DBD0F0) and nothing else of sMain's; clear from its constructor.
int GameExitRequested() { return ObjectFlag(DDDA_SMAIN, DDDA_SMAIN_VTABLE, DDDA_SMAIN_EXIT); }

// ---------------------------------------------------------------------------
// missing textures

// A 4x4 BC1 texture of middle grey with 3 mips (52 bytes).  Grey reads as a neutral colour and, in
// a normal map's red/green, as a flat surface.  DDDA's header is revision 0x99, DDO's 0x9D.
static BYTE g_standIn[52];
static void BuildStandIn() {
    const DWORD w1 = g_game == GAME_DDO ? 0x2000209Du : 0x20000099u;
    const DWORD words[4] = {0x00584554u /* TEX\0 */, w1, 3u | (4u << 6) | (4u << 19), 1u | (20u << 8) | (1u << 16)};
    memcpy(g_standIn, words, 16);
    const DWORD offsets[3] = {28, 36, 44};
    memcpy(g_standIn + 16, offsets, 12);
    const BYTE block[8] = {0x10, 0x84, 0x10, 0x84, 0, 0, 0, 0};   // colour0 = colour1 = RGB565 grey
    for (int i = 0; i < 3; i++) memcpy(g_standIn + 28 + 8 * i, block, 8);
}

static BOOL g_guard = FALSE;
static wchar_t g_standInPath[MAX_PATH];

static BOOL EnsureStandIn() {
    if (g_standInPath[0]) return TRUE;
    wchar_t dir[MAX_PATH], path[MAX_PATH];
    _snwprintf_s(dir, _countof(dir), _TRUNCATE, L"%s\\standin", g_stateDir);
    CreateDirectoryW(dir, NULL);
    _snwprintf_s(path, _countof(path), _TRUNCATE, L"%s\\missing-texture-%s.tex", dir, g_game == GAME_DDO ? L"ddo" : L"ddda");
    BYTE have[64];
    DWORD got = 0;
    HANDLE h = Real_CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    if (h != INVALID_HANDLE_VALUE) {
        ReadFile(h, have, sizeof have, &got, NULL);
        CloseHandle(h);
    }
    if (got != sizeof g_standIn || memcmp(have, g_standIn, sizeof g_standIn) != 0) {
        h = Real_CreateFileW(path, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
        if (h == INVALID_HANDLE_VALUE) return FALSE;
        DWORD w = 0;
        WriteFile(h, g_standIn, sizeof g_standIn, &w, NULL);
        CloseHandle(h);
        if (w != sizeof g_standIn) return FALSE;
    }
    wcscpy_s(g_standInPath, MAX_PATH, path);
    return TRUE;
}

HANDLE GuardOpen(const wchar_t* fullPath, DWORD access, DWORD share, LPSECURITY_ATTRIBUTES sa, DWORD disposition,
                 DWORD flags, HANDLE templ) {
    (void)share; (void)disposition; (void)templ; (void)access;
    if (!g_guard) return INVALID_HANDLE_VALUE;
    const wchar_t* dot = wcsrchr(fullPath, L'.');
    if (!dot || _wcsicmp(dot, L".tex") != 0) return INVALID_HANDLE_VALUE;
    if (!EnsureStandIn()) return INVALID_HANDLE_VALUE;
    HANDLE h = Real_CreateFileW(g_standInPath, GENERIC_READ, FILE_SHARE_READ, sa, OPEN_EXISTING,
                                flags & ~(DWORD)FILE_FLAG_DELETE_ON_CLOSE, NULL);
    if (h == INVALID_HANDLE_VALUE) return h;
    LONG k = InterlockedIncrement(&g_fallbacks);
    if (k <= 200) LogLine(L"guard    %s does not exist; the game gets a neutral stand-in texture instead of stopping", fullPath);
    LiveNote("last_fallback", fullPath);
    return h;
}

// ---------------------------------------------------------------------------
// window

typedef HWND(WINAPI* CreateWindowExA_t)(DWORD, LPCSTR, LPCSTR, DWORD, int, int, int, int, HWND, HMENU, HINSTANCE, LPVOID);
typedef HWND(WINAPI* CreateWindowExW_t)(DWORD, LPCWSTR, LPCWSTR, DWORD, int, int, int, int, HWND, HMENU, HINSTANCE, LPVOID);
typedef BOOL(WINAPI* AdjustWindowRect_t)(LPRECT, DWORD, BOOL);
typedef LONG(WINAPI* SetWindowLongA_t)(HWND, int, LONG);
typedef BOOL(WINAPI* SetWindowPos_t)(HWND, HWND, int, int, int, int, UINT);
typedef BOOL(WINAPI* ClipCursor_t)(const RECT*);
typedef BOOL(WINAPI* SetCursorPos_t)(int, int);
static CreateWindowExA_t Real_CreateWindowExA;
static CreateWindowExW_t Real_CreateWindowExW;
static AdjustWindowRect_t Real_AdjustWindowRect;
static SetWindowLongA_t Real_SetWindowLongA;
static SetWindowPos_t Real_SetWindowPos;
static ClipCursor_t Real_ClipCursor;
static SetCursorPos_t Real_SetCursorPos;

static BOOL g_borderless = FALSE, g_fill = TRUE, g_background = FALSE;
static HWND g_mainWnd = NULL;
static WNDPROC g_gameProc = NULL;
static BOOL g_unicodeProc = FALSE;

static const DWORD FRAME_STYLES = WS_CAPTION | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_BORDER | WS_DLGFRAME;
static const DWORD FRAME_EX = WS_EX_WINDOWEDGE | WS_EX_CLIENTEDGE | WS_EX_DLGMODALFRAME | WS_EX_STATICEDGE;

static BOOL TopLevel(HWND parent, DWORD style) { return parent == NULL && !(style & WS_CHILD); }

static void MonitorRect(HWND w, RECT* r) {
    HMONITOR m = w ? MonitorFromWindow(w, MONITOR_DEFAULTTOPRIMARY) : MonitorFromPoint(POINT{0, 0}, MONITOR_DEFAULTTOPRIMARY);
    MONITORINFO mi = {sizeof mi};
    if (m && GetMonitorInfoW(m, &mi)) *r = mi.rcMonitor;
    else SetRect(r, 0, 0, GetSystemMetrics(SM_CXSCREEN), GetSystemMetrics(SM_CYSCREEN));
}

static BOOL GameInFront() {
    if (!g_mainWnd) return TRUE;
    HWND f = GetForegroundWindow();
    return f && GetAncestor(f, GA_ROOTOWNER) == GetAncestor(g_mainWnd, GA_ROOTOWNER);
}

static LRESULT CALLBACK GameWindowProc(HWND w, UINT msg, WPARAM wp, LPARAM lp) {
    if (g_background && g_d3dWindowed != 0) {
        // The game pauses when it hears it lost focus; in a window it does not need to.
        if ((msg == WM_ACTIVATEAPP && wp == FALSE) || msg == WM_KILLFOCUS) {
            if (Real_ClipCursor) Real_ClipCursor(NULL);
            return 0;
        }
    }
    return g_unicodeProc ? CallWindowProcW(g_gameProc, w, msg, wp, lp) : CallWindowProcA(g_gameProc, w, msg, wp, lp);
}

static void AdoptWindow(HWND w, DWORD style) {
    g_mainWnd = w;
    if (!g_gameWindow) g_gameWindow = w;
    if (g_background) {
        g_unicodeProc = IsWindowUnicode(w);
        g_gameProc = (WNDPROC)(g_unicodeProc ? SetWindowLongPtrW(w, GWLP_WNDPROC, (LONG_PTR)GameWindowProc)
                                             : SetWindowLongPtrA(w, GWLP_WNDPROC, (LONG_PTR)GameWindowProc));
    }
    LogLine(L"window   game window 0x%p (%s)%s%s", w, (style & WS_CAPTION) == WS_CAPTION ? L"framed" : L"no frame",
            g_borderless ? L"; borderless" : L"", g_background ? L"; keeps running when alt-tabbed" : L"");
}

static void BorderlessCreate(DWORD* ex, DWORD* style, int* x, int* y, int* w, int* h) {
    *style = (*style & ~FRAME_STYLES) | WS_POPUP;
    *ex &= ~FRAME_EX;
    if (g_fill) {
        RECT r;
        MonitorRect(NULL, &r);
        *x = r.left;
        *y = r.top;
        *w = r.right - r.left;
        *h = r.bottom - r.top;
    }
}

static HWND WINAPI Hook_CreateWindowExA(DWORD ex, LPCSTR cls, LPCSTR title, DWORD style, int x, int y, int w, int h,
                                        HWND parent, HMENU menu, HINSTANCE inst, LPVOID param) {
    BOOL candidate = !g_mainWnd && TopLevel(parent, style) && (style & (WS_CAPTION | WS_POPUP));
    if (candidate && g_borderless && (style & WS_CAPTION) == WS_CAPTION) BorderlessCreate(&ex, &style, &x, &y, &w, &h);
    HWND wnd = Real_CreateWindowExA(ex, cls, title, style, x, y, w, h, parent, menu, inst, param);
    if (wnd && candidate) AdoptWindow(wnd, style);
    return wnd;
}

static HWND WINAPI Hook_CreateWindowExW(DWORD ex, LPCWSTR cls, LPCWSTR title, DWORD style, int x, int y, int w, int h,
                                        HWND parent, HMENU menu, HINSTANCE inst, LPVOID param) {
    BOOL candidate = !g_mainWnd && TopLevel(parent, style) && (style & (WS_CAPTION | WS_POPUP));
    if (candidate && g_borderless && (style & WS_CAPTION) == WS_CAPTION) BorderlessCreate(&ex, &style, &x, &y, &w, &h);
    HWND wnd = Real_CreateWindowExW(ex, cls, title, style, x, y, w, h, parent, menu, inst, param);
    if (wnd && candidate) AdoptWindow(wnd, style);
    return wnd;
}

// Borderless: a frame the window no longer has adds nothing to its size.
static BOOL WINAPI Hook_AdjustWindowRect(LPRECT r, DWORD style, BOOL menu) {
    if (g_borderless && (style & WS_CAPTION) == WS_CAPTION && !menu) return r != NULL;
    return Real_AdjustWindowRect(r, style, menu);
}

static LONG WINAPI Hook_SetWindowLongA(HWND w, int index, LONG value) {
    if (w && w == g_mainWnd) {
        if (index == GWL_STYLE && g_borderless && (value & WS_CAPTION) == WS_CAPTION) value = (value & ~FRAME_STYLES) | WS_POPUP;
        if (index == GWL_EXSTYLE && g_borderless) value &= ~FRAME_EX;
        if (index == GWLP_WNDPROC && g_gameProc) {
            // The game replaces its window procedure: keep ours in front of the new one.
            LONG prev = (LONG)(LONG_PTR)g_gameProc;
            g_gameProc = (WNDPROC)(LONG_PTR)value;
            return prev;
        }
    }
    return Real_SetWindowLongA(w, index, value);
}

static BOOL WINAPI Hook_SetWindowPos(HWND w, HWND after, int x, int y, int cx, int cy, UINT flags) {
    if (w && w == g_mainWnd && g_borderless && g_fill && !(flags & SWP_NOSIZE) && g_d3dWindowed != 0) {
        LONG style = GetWindowLongW(w, GWL_STYLE);
        if (!(style & WS_CAPTION)) {
            RECT r;
            MonitorRect(w, &r);
            x = r.left;
            y = r.top;
            cx = r.right - r.left;
            cy = r.bottom - r.top;
            flags &= ~(UINT)SWP_NOMOVE;
        }
    }
    return Real_SetWindowPos(w, after, x, y, cx, cy, flags);
}

static BOOL WINAPI Hook_ClipCursor(const RECT* r) {
    if (g_background && r && !GameInFront()) return Real_ClipCursor(NULL);
    return Real_ClipCursor(r);
}

static BOOL WINAPI Hook_SetCursorPos(int x, int y) {
    if (g_background && !GameInFront()) return TRUE;     // do not drag the cursor while it is elsewhere
    return Real_SetCursorPos(x, y);
}

void FixesInstallHooks() {
    BuildStandIn();
    g_guard = IniInt(L"guard", L"missing_textures", 1) != 0 && (g_game == GAME_DDDA || g_game == GAME_DDO ||
                                                                 IniInt(L"loader", L"any_program", 0));
    LogLine(L"guard    missing textures %s", g_guard ? L"get a neutral stand-in (riftstone\\standin)" : L"stop the game as usual");
    g_borderless = IniInt(L"window", L"borderless", 0) != 0;
    g_fill = IniInt(L"window", L"borderless_fill", 1) != 0;
    g_background = IniInt(L"window", L"background_run", 0) != 0;
    if (!g_borderless && !g_background) return;
    HookImport("USER32.dll", "CreateWindowExA", (void*)Hook_CreateWindowExA, (void**)&Real_CreateWindowExA);
    HookImport("USER32.dll", "CreateWindowExW", (void*)Hook_CreateWindowExW, (void**)&Real_CreateWindowExW);
    HookImport("USER32.dll", "SetWindowLongA", (void*)Hook_SetWindowLongA, (void**)&Real_SetWindowLongA);
    if (g_borderless) {
        HookImport("USER32.dll", "AdjustWindowRect", (void*)Hook_AdjustWindowRect, (void**)&Real_AdjustWindowRect);
        HookImport("USER32.dll", "SetWindowPos", (void*)Hook_SetWindowPos, (void**)&Real_SetWindowPos);
    }
    if (g_background) {
        HookImport("USER32.dll", "ClipCursor", (void*)Hook_ClipCursor, (void**)&Real_ClipCursor);
        HookImport("USER32.dll", "SetCursorPos", (void*)Hook_SetCursorPos, (void**)&Real_SetCursorPos);
    }
    if (!Real_ClipCursor) Real_ClipCursor = (ClipCursor_t)GetProcAddress(GetModuleHandleW(L"user32.dll"), "ClipCursor");
}

// ---------------------------------------------------------------------------
// frame-rate ceiling (DDDA build 2364871)

struct CodeSite {
    DWORD_PTR va;
    BYTE expect[8];
    const wchar_t* what;
};
// movss xmm0, dword ptr [0x01433AA8]  (150.0f): the "Variable" case of the frame-rate mode switch
static const CodeSite FPS_SITES[] = {
    {0x00EDD4AD, {0xF3, 0x0F, 0x10, 0x05, 0xA8, 0x3A, 0x43, 0x01}, L"options: apply"},
    {0x00EDDC86, {0xF3, 0x0F, 0x10, 0x05, 0xA8, 0x3A, 0x43, 0x01}, L"options: save to config.ini (MaxFPS)"},
};
static float g_fpsCeiling = 150.0f;

static BOOL SiteMatches(const CodeSite& s) {
    if (!Readable((const void*)s.va, sizeof s.expect)) return FALSE;
    return memcmp((const void*)s.va, s.expect, sizeof s.expect) == 0;
}

static void ApplyFpsCeiling() {
    int fps = IniInt(L"fps", L"max_fps", 0);
    if (fps == 0) return;
    if (!g_knownBuild) {
        LogLine(L"fps      max_fps = %d ignored: this is not DDDA build 2364871", fps);
        return;
    }
    if (fps < 30 || fps > 360) {
        LogLine(L"fps      max_fps = %d is outside 30..360; left alone", fps);
        return;
    }
    for (const CodeSite& s : FPS_SITES) {
        if (!SiteMatches(s)) {
            LogLine(L"fps      the code at 0x%08lx (%s) is not what build 2364871 has; nothing patched", (DWORD)s.va, s.what);
            return;
        }
    }
    g_fpsCeiling = (float)fps;
    DWORD_PTR target = (DWORD_PTR)&g_fpsCeiling;
    for (const CodeSite& s : FPS_SITES) {
        DWORD old;
        BYTE* disp = (BYTE*)s.va + 4;
        if (!VirtualProtect(disp, 4, PAGE_EXECUTE_READWRITE, &old)) continue;
        memcpy(disp, &target, 4);
        VirtualProtect(disp, 4, old, &old);
    }
    FlushInstructionCache(GetCurrentProcess(), NULL, 0);
    LogLine(L"fps      the options menu's \"Variable\" frame rate is %d fps (was 150); config.ini MaxFPS sets it at start", fps);
}

// ---------------------------------------------------------------------------
// shadow map size (DDDA build 2364871): sRender::getShadowMapSize (0x00DA97D0) returns
// table[ShadowQuality] from 0x014292BC {512, 1024, 2048} for the sun, half that for spot and point
// lights.  [render] shadow_map_size makes HIGH that size instead of 2048.

static const DWORD_PTR SHADOW_TABLE = 0x014292BC;
static const CodeSite SHADOW_READER = {0x00DA97DA, {0x8B, 0x04, 0x85, 0xBC, 0x92, 0x42, 0x01, 0x85}, L"getShadowMapSize"};

static void ApplyShadowSize() {
    int size = IniInt(L"render", L"shadow_map_size", 0);
    if (size == 0) return;
    if (!g_knownBuild) {
        LogLine(L"shadows  shadow_map_size = %d ignored: this is not DDDA build 2364871", size);
        return;
    }
    if (size < 1024 || size > 8192 || size % 32) {
        LogLine(L"shadows  shadow_map_size = %d must be 1024..8192 and a multiple of 32; left alone", size);
        return;
    }
    const DWORD expect[3] = {512, 1024, 2048};
    if (!SiteMatches(SHADOW_READER) || !Readable((const void*)SHADOW_TABLE, sizeof expect) ||
        memcmp((const void*)SHADOW_TABLE, expect, sizeof expect) != 0) {
        LogLine(L"shadows  the shadow-size table is not what build 2364871 has; nothing patched");
        return;
    }
    DWORD old;
    DWORD* high = (DWORD*)SHADOW_TABLE + 2;
    if (!VirtualProtect(high, 4, PAGE_READWRITE, &old)) return;
    *high = (DWORD)size;
    VirtualProtect(high, 4, old, &old);
    LogLine(L"shadows  ShadowQuality=HIGH now draws %d px sun shadows (%d px for lamps and torches); was 2048", size, size / 2);
}

void FixesApplyPatches() {
    ApplyFpsCeiling();
    ApplyShadowSize();
}

// ---------------------------------------------------------------------------
// save backups (DDDA: Steam Cloud keeps the save in <Steam>\userdata\<id>\367500\remote)

static uint32_t HashFile(const wchar_t* path, DWORD* size) {
    uint32_t h = 2166136261u;
    *size = 0;
    HANDLE f = Real_CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING, 0, NULL);
    if (f == INVALID_HANDLE_VALUE) return 0;
    static BYTE buf[65536];
    DWORD got;
    while (ReadFile(f, buf, sizeof buf, &got, NULL) && got) {
        for (DWORD i = 0; i < got; i++) { h ^= buf[i]; h *= 16777619u; }
        *size += got;
    }
    CloseHandle(f);
    return h;
}

// CreateDirectory for every missing component of an absolute path.
static void MakeDirs(const wchar_t* path) {
    wchar_t part[MAX_PATH];
    wcsncpy_s(part, MAX_PATH, path, _TRUNCATE);
    for (wchar_t* p = part + 3; *p; ++p) {       // past the drive, "C:" and its separator
        if (*p != L'\\') continue;
        *p = 0;
        CreateDirectoryW(part, NULL);
        *p = L'\\';
    }
    CreateDirectoryW(part, NULL);
}

static BOOL SteamRoot(wchar_t* out, DWORD cap) {
    DWORD bytes = cap * sizeof(wchar_t);
    if (RegGetValueW(HKEY_CURRENT_USER, L"Software\\Valve\\Steam", L"SteamPath", RRF_RT_REG_SZ, NULL, out, &bytes) != ERROR_SUCCESS)
        return FALSE;
    for (wchar_t* p = out; *p; ++p)
        if (*p == L'/') *p = L'\\';
    return TRUE;
}

// Copy every file of one remote folder into <target>\<stamp>, unless the newest copy already
// holds the same save.  Keep the newest `keep`.
static void BackupFolder(const wchar_t* remote, const wchar_t* target, int keep) {
    wchar_t sav[MAX_PATH];
    _snwprintf_s(sav, _countof(sav), _TRUNCATE, L"%s\\DDDA.sav", remote);
    DWORD size;
    uint32_t hash = HashFile(sav, &size);
    if (!size) return;
    MakeDirs(target);
    // The newest existing backup (names sort by time).
    wchar_t pattern[MAX_PATH], newest[64] = L"";
    _snwprintf_s(pattern, _countof(pattern), _TRUNCATE, L"%s\\*", target);
    static wchar_t names[256][64];
    int n = 0;
    WIN32_FIND_DATAW fd;
    HANDLE find = FindFirstFileW(pattern, &fd);
    if (find != INVALID_HANDLE_VALUE) {
        do {
            if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) || fd.cFileName[0] == L'.') continue;
            if (n < 256 && wcslen(fd.cFileName) < 64) wcscpy_s(names[n++], 64, fd.cFileName);
            if (_wcsicmp(fd.cFileName, newest) > 0) wcscpy_s(newest, 64, fd.cFileName);
        } while (FindNextFileW(find, &fd));
        FindClose(find);
    }
    if (newest[0]) {
        wchar_t last[MAX_PATH];
        _snwprintf_s(last, _countof(last), _TRUNCATE, L"%s\\%s\\DDDA.sav", target, newest);
        DWORD lsize;
        if (HashFile(last, &lsize) == hash && lsize == size) {
            LogLine(L"saves    the save has not changed since the backup %s", newest);
            return;
        }
    }
    wchar_t stamp[32], dest[MAX_PATH];
    Stamp(stamp, _countof(stamp));
    _snwprintf_s(dest, _countof(dest), _TRUNCATE, L"%s\\%s", target, stamp);
    if (!CreateDirectoryW(dest, NULL) && GetLastError() != ERROR_ALREADY_EXISTS) return;
    _snwprintf_s(pattern, _countof(pattern), _TRUNCATE, L"%s\\*", remote);
    int copied = 0;
    find = FindFirstFileW(pattern, &fd);
    if (find != INVALID_HANDLE_VALUE) {
        do {
            if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) continue;
            if (fd.nFileSizeHigh || fd.nFileSizeLow > 64u * 1024 * 1024) continue;
            wchar_t from[MAX_PATH], to[MAX_PATH];
            _snwprintf_s(from, _countof(from), _TRUNCATE, L"%s\\%s", remote, fd.cFileName);
            _snwprintf_s(to, _countof(to), _TRUNCATE, L"%s\\%s", dest, fd.cFileName);
            if (CopyFileW(from, to, TRUE)) copied++;
        } while (FindNextFileW(find, &fd));
        FindClose(find);
    }
    LogLine(L"saves    backed up %d file(s) of %s to %s", copied, remote, dest);
    // Keep the newest `keep` backups.
    if (n + 1 > keep) {
        qsort(names, n, sizeof names[0], [](const void* a, const void* b) { return _wcsicmp((const wchar_t*)a, (const wchar_t*)b); });
        for (int i = 0; i < n + 1 - keep && i < n; i++) {
            wchar_t old[MAX_PATH];
            _snwprintf_s(old, _countof(old), _TRUNCATE, L"%s\\%s", target, names[i]);
            wchar_t filePattern[MAX_PATH];
            _snwprintf_s(filePattern, _countof(filePattern), _TRUNCATE, L"%s\\*", old);
            HANDLE ff = FindFirstFileW(filePattern, &fd);
            if (ff != INVALID_HANDLE_VALUE) {
                do {
                    if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) continue;
                    wchar_t f[MAX_PATH];
                    _snwprintf_s(f, _countof(f), _TRUNCATE, L"%s\\%s", old, fd.cFileName);
                    DeleteFileW(f);
                } while (FindNextFileW(ff, &fd));
                FindClose(ff);
            }
            RemoveDirectoryW(old);
        }
    }
}

void BackupSaves() {
    if (g_game != GAME_DDDA || !IniInt(L"saves", L"backup", 1)) return;
    int keep = IniInt(L"saves", L"keep", 20);
    if (keep < 1) keep = 1;
    wchar_t target[MAX_PATH], source[MAX_PATH];
    IniStr(L"saves", L"target", L"", target, MAX_PATH);
    if (!target[0]) {
        wchar_t local[MAX_PATH];
        if (!GetEnvironmentVariableW(L"LOCALAPPDATA", local, MAX_PATH)) return;
        _snwprintf_s(target, _countof(target), _TRUNCATE, L"%s\\Riftstone\\saves\\DDDA", local);
    }
    IniStr(L"saves", L"source", L"", source, MAX_PATH);
    if (source[0]) {                                   // one explicit folder (tests, other stores)
        BackupFolder(source, target, keep);
        return;
    }
    wchar_t steam[MAX_PATH];
    if (!SteamRoot(steam, MAX_PATH)) {
        LogLine(L"saves    Steam's folder is not in the registry; no backup");
        return;
    }
    wchar_t pattern[MAX_PATH];
    _snwprintf_s(pattern, _countof(pattern), _TRUNCATE, L"%s\\userdata\\*", steam);
    WIN32_FIND_DATAW fd;
    HANDLE find = FindFirstFileW(pattern, &fd);
    if (find == INVALID_HANDLE_VALUE) return;
    do {
        if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) || fd.cFileName[0] == L'.') continue;
        wchar_t remote[MAX_PATH], dest[MAX_PATH];
        _snwprintf_s(remote, _countof(remote), _TRUNCATE, L"%s\\userdata\\%s\\367500\\remote", steam, fd.cFileName);
        if (Real_GetFileAttributesW(remote) == INVALID_FILE_ATTRIBUTES) continue;
        _snwprintf_s(dest, _countof(dest), _TRUNCATE, L"%s\\%s", target, fd.cFileName);
        BackupFolder(remote, dest, keep);
    } while (FindNextFileW(find, &fd));
    FindClose(find);
}

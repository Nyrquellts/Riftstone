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
//   ragdolls           [guard] ragdoll_bodies (DDDA build 2364871): the "walk a ragdoll's bodies" idiom
//                      reads the packed count [bodydata+0x68] (>>8) through bodydata = [holder+0x38] with no
//                      null-check; the game's own accessor 0x010805D0 answers 0 without data.  Four bespoke
//                      sites (two enemy setter functions + two inline walks), then the whole family
//                      tools/ragdoll_sites.py finds in the exe (62 more, ragdoll_sites.inc, byte-verified)
//                      trampolined to that same answer.  Live crashes: 0x00794942 (the four) and 0x007945B4
//                      (the family), goblin hordes 2026-09-27; 0x00794AA2 (a dying goblin's ragdoll at
//                      Devil's Firegrove, 2026-10-06, a copy the first scan missed).
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

// enemy_cap moves the slots to the manager's tail and says how many through its export EnemyCap_Slots.  It is
// looked for among the plugins the loader brought in, whatever their file names (01_enemy_cap.asi), then as a
// module named enemy_cap.asi that came in some other way.  *present: an enemy_cap is there (with or without
// the export).
typedef int (*EnemyCapSlots_t)();
static EnemyCapSlots_t EnemyCapExport(BOOL* present) {
    for (int i = 0; i < g_pluginCount && i < MAX_PLUGINS; i++) {
        const PluginInfo& p = g_pluginInfo[i];
        if (p.state != 1 || !p.module) continue;
        if (FARPROC f = GetProcAddress(p.module, "EnemyCap_Slots")) {
            *present = TRUE;
            return (EnemyCapSlots_t)f;
        }
    }
    HMODULE cap = GetModuleHandleW(L"enemy_cap.asi");
    *present = cap != NULL;
    return cap ? (EnemyCapSlots_t)GetProcAddress(cap, "EnemyCap_Slots") : NULL;
}

BOOL EnemySlots(int* active, int* usable, int* slots) {
    if (!g_knownBuild) return FALSE;
    int n = 10;
    DWORD base = SLOTS_VANILLA;
    BOOL present = FALSE;
    EnemyCapSlots_t f = EnemyCapExport(&present);
    if (present) {
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

// The main loop's reasons to run no frame (0x00DF0D20; docs/stability-membrane.md, "The hang of 2026-10-06
// 14:42"): it sleeps while sApp's active byte (+0x2566, cleared by WM_ACTIVATEAPP false) and +0x20 are both 0,
// and skips the frame while sRender+0x433E68 is set; sRender+0x433E54 holds the reset requests (0x10: a Present
// returned D3DERR_DEVICELOST).  sApp lives on WinMain's stack; [0x018D0F50] points at it.
static const DWORD_PTR S_APP = 0x018D0F50;
static const DWORD_PTR S_RENDER = 0x018D08F4;

static BOOL ReadFrameGatesUnsafe(FrameGates* g) {
    DWORD_PTR app = *(const DWORD_PTR*)S_APP;
    DWORD_PTR render = *(const DWORD_PTR*)S_RENDER;
    if (!app || !render || !Readable((const void*)app, 0x2567) || !Readable((const void*)(render + 0x433E54), 0x15))
        return FALSE;
    g->appActive = *(const BYTE*)(app + 0x2566);
    g->appForced = *(const BYTE*)(app + 0x20);
    g->resetBits = *(const DWORD*)(render + 0x433E54);
    g->deviceLost = *(const BYTE*)(render + 0x433E68);
    return TRUE;
}

BOOL ReadFrameGates(FrameGates* g) {
    memset(g, 0, sizeof *g);
    if (!g_knownBuild || !Readable((const void*)S_APP, 4) || !Readable((const void*)S_RENDER, 4)) return FALSE;
    __try {
        return ReadFrameGatesUnsafe(g);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return FALSE;
    }
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
// the stability membrane: one registry for every guard (docs/stability-membrane.md)
//
// A guard counts where it acts: an interlocked increment, the game's address, and for the rare ones a detail
// (a file name).  GuardsReport, run by the live thread four times a second and once at exit, writes the new hits
// to riftstone\logs\riftstone_error.log: at most one line per guard per report, and past 50 lines for a guard one
// every 10 s, so a guard acting every frame cannot fill the disk.  The file is made at the first hit of a session;
// the previous session's becomes riftstone_error.prev.log.

struct GuardState {
    const wchar_t* key;                          // [guard] key
    const char* keyA;
    const wchar_t* what;                         // what one hit means
    BOOL on;
    volatile LONG hits;
    volatile LONG where;                         // the game's address of the last hit (0: none known)
    LONG reported, lines;                        // GuardsReport's
    ULONGLONG lastLine;
    wchar_t detail[MAX_PATH];                    // the last hit's detail (under g_guardLock)
};
static GuardState g_guards[GUARD_COUNT] = {
    {L"missing_textures", "missing_textures", L"a texture that does not exist got the stand-in"},
    {L"from_archives", "from_archives", L"a resource asked for before its archive was read got its own bytes"},
    {L"ragdoll_bodies", "ragdoll_bodies", L"a ragdoll walked before its bodies were set up: none walked"},
    {L"particles", "particles", L"an effect generator's update faulted in the game's code: that generator is off"},
    {L"shadow_buffers", "shadow_buffers", L"a shadow map size past a safe bound was bounded"},
    {L"broken_textures", "broken_textures", L"a loose texture that does not fit its file was answered as missing"},
    {L"gui_text", "gui_text", L"a GUI text field whose looked-up string was missing showed empty instead of crashing"},
};
static CRITICAL_SECTION g_guardLock;             // the details and the error log
static BOOL g_guardLockReady = FALSE;
static wchar_t g_errorLog[MAX_PATH];
static BOOL g_errorLogStarted = FALSE;

BOOL GuardOn(int id) { return id >= 0 && id < GUARD_COUNT && g_guards[id].on; }
LONG GuardHits(int id) { return id >= 0 && id < GUARD_COUNT ? g_guards[id].hits : 0; }
DWORD GuardLastWhere(int id) { return id >= 0 && id < GUARD_COUNT ? (DWORD)g_guards[id].where : 0; }
const char* GuardKeyA(int id) { return id >= 0 && id < GUARD_COUNT ? g_guards[id].keyA : ""; }
static void GuardMark(int id, BOOL on) { if (id >= 0 && id < GUARD_COUNT) g_guards[id].on = on; }

void GuardHit(int id, DWORD_PTR where, const wchar_t* detail) {
    if (id < 0 || id >= GUARD_COUNT) return;
    GuardState& g = g_guards[id];
    InterlockedExchange(&g.where, (LONG)where);
    if (detail && g_guardLockReady) {
        EnterCriticalSection(&g_guardLock);
        wcsncpy_s(g.detail, _countof(g.detail), detail, _TRUNCATE);
        LeaveCriticalSection(&g_guardLock);
    }
    InterlockedIncrement(&g.hits);
}

void GuardsInit() {
    if (!g_guardLockReady) {
        InitializeCriticalSection(&g_guardLock);
        g_guardLockReady = TRUE;
    }
    for (int i = 0; i < GUARD_COUNT; i++) g_guards[i].on = IniInt(L"guard", g_guards[i].key, 1) != 0;
    if (!g_logDir[0]) return;
    _snwprintf_s(g_errorLog, _countof(g_errorLog), _TRUNCATE, L"%s\\riftstone_error.log", g_logDir);
    wchar_t prev[MAX_PATH];
    _snwprintf_s(prev, _countof(prev), _TRUNCATE, L"%s\\riftstone_error.prev.log", g_logDir);
    if (GetFileAttributesW(g_errorLog) != INVALID_FILE_ATTRIBUTES) MoveFileExW(g_errorLog, prev, MOVEFILE_REPLACE_EXISTING);
}

// One UTF-8 line appended to the error log (under g_guardLock).
static void ErrorLine(const wchar_t* fmt, ...) {
    if (!g_errorLog[0]) return;
    wchar_t line[1400];
    va_list ap;
    va_start(ap, fmt);
    _vsnwprintf_s(line, _countof(line), _TRUNCATE, fmt, ap);
    va_end(ap);
    char utf8[2800];
    int n = WideCharToMultiByte(CP_UTF8, 0, line, -1, utf8, sizeof utf8 - 2, NULL, NULL);
    if (n <= 0) return;
    utf8[n - 1] = '\r';
    utf8[n] = '\n';
    HANDLE h = Real_CreateFileW(g_errorLog, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_ALWAYS,
                                FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return;
    DWORD w = 0;
    WriteFile(h, utf8, (DWORD)n + 1, &w, NULL);
    CloseHandle(h);
}

void GuardsReport() {
    if (!g_guardLockReady) return;
    EnterCriticalSection(&g_guardLock);
    ULONGLONG now = GetTickCount64();
    for (int i = 0; i < GUARD_COUNT; i++) {
        GuardState& g = g_guards[i];
        LONG h = g.hits;
        if (h == g.reported || (g.lines >= 50 && now - g.lastLine < 10000)) continue;
        if (!g_errorLogStarted) {
            wchar_t stamp[32];
            Stamp(stamp, _countof(stamp));
            ErrorLine(L"Riftstone guards, session of %s (%s): what the loader caught instead of letting the game stop. "
                      L"Each line: time, guard, hits so far, where in the game, what it means, the last detail.",
                      stamp, g_exeName);
            g_errorLogStarted = TRUE;
        }
        SYSTEMTIME t;
        GetLocalTime(&t);
        DWORD where = (DWORD)g.where;
        wchar_t at[64] = L"";
        if (where && InImage(where))
            _snwprintf_s(at, _countof(at), _TRUNCATE, L" at 0x%08lx (%s+0x%lx)", where, g_exeName,
                         (unsigned long)(where - g_imageBase));
        else if (where)
            _snwprintf_s(at, _countof(at), _TRUNCATE, L" at 0x%08lx", where);
        ErrorLine(L"%02u:%02u:%02u  %-16s %ld%s: %s%s%s", t.wHour, t.wMinute, t.wSecond, g.key, h, at, g.what,
                  g.detail[0] ? L"; last: " : L"", g.detail);
        if (g.reported == 0) LogLine(L"guard    %s: %s (riftstone_error.log has each)", g.key, g.what);
        g.reported = h;
        g.lines++;
        g.lastLine = now;
    }
    LeaveCriticalSection(&g_guardLock);
}

// ---------------------------------------------------------------------------
// missing textures

// A 4x4 BC1 texture with 3 mips (52 bytes): middle grey (neutral, and a flat surface in a normal map's red/green),
// or magenta ([guard] missing_texture_look = magenta: easy to spot while making a mod).  DDDA's header is revision
// 0x99, DDO's 0x9D.
static BYTE g_standIn[52];
static BOOL g_standInMagenta = FALSE;
static void BuildStandIn() {
    const DWORD w1 = g_game == GAME_DDO ? 0x2000209Du : 0x20000099u;
    const DWORD words[4] = {0x00584554u /* TEX\0 */, w1, 3u | (4u << 6) | (4u << 19), 1u | (20u << 8) | (1u << 16)};
    memcpy(g_standIn, words, 16);
    const DWORD offsets[3] = {28, 36, 44};
    memcpy(g_standIn + 16, offsets, 12);
    const BYTE grey[8] = {0x10, 0x84, 0x10, 0x84, 0, 0, 0, 0};        // colour0 = colour1 = RGB565 grey
    const BYTE magenta[8] = {0x1F, 0xF8, 0x1F, 0xF8, 0, 0, 0, 0};     // RGB565 (31, 0, 31)
    for (int i = 0; i < 3; i++) memcpy(g_standIn + 28 + 8 * i, g_standInMagenta ? magenta : grey, 8);
}

static BOOL g_guard = FALSE;
static INIT_ONCE g_standInOnce = INIT_ONCE_STATIC_INIT;
static wchar_t g_standInPath[MAX_PATH];            // set once, inside g_standInOnce

// The stand-in on disk holds exactly g_standIn (read while others may have it open).
static BOOL StandInOnDisk(const wchar_t* path) {
    BYTE have[64];
    DWORD got = 0;
    HANDLE h = Real_CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, NULL,
                                OPEN_EXISTING, 0, NULL);
    if (h == INVALID_HANDLE_VALUE) return FALSE;
    ReadFile(h, have, sizeof have, &got, NULL);
    CloseHandle(h);
    return got == sizeof g_standIn && memcmp(have, g_standIn, sizeof g_standIn) == 0;
}

// Made once per process, whichever thread first misses a texture; the others wait for it.  Written under a
// name of its own and then moved into place, so no reader ever sees half a file (another copy of the game
// may be reading the stand-in).
static BOOL CALLBACK MakeStandIn(PINIT_ONCE, PVOID, PVOID*) {
    wchar_t dir[MAX_PATH], path[MAX_PATH], temp[MAX_PATH];
    _snwprintf_s(dir, _countof(dir), _TRUNCATE, L"%s\\standin", g_stateDir);
    CreateDirectoryW(dir, NULL);
    _snwprintf_s(path, _countof(path), _TRUNCATE, L"%s\\missing-texture-%s%s.tex", dir, g_game == GAME_DDO ? L"ddo" : L"ddda",
                 g_standInMagenta ? L"-magenta" : L"");
    if (!StandInOnDisk(path)) {
        _snwprintf_s(temp, _countof(temp), _TRUNCATE, L"%s.%lu.tmp", path, GetCurrentProcessId());
        HANDLE h = Real_CreateFileW(temp, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
        if (h == INVALID_HANDLE_VALUE) return FALSE;
        DWORD w = 0;
        WriteFile(h, g_standIn, sizeof g_standIn, &w, NULL);
        CloseHandle(h);
        BOOL placed = w == sizeof g_standIn && MoveFileExW(temp, path, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH);
        if (!placed) DeleteFileW(temp);
        if (!placed && !StandInOnDisk(path)) return FALSE;    // (another copy of the game may have just made it)
    }
    wcscpy_s(g_standInPath, MAX_PATH, path);
    return TRUE;
}

// FALSE when it could not be made (the next miss tries again).
static BOOL EnsureStandIn() { return InitOnceExecuteOnce(&g_standInOnce, MakeStandIn, NULL, NULL); }

HANDLE GuardOpen(const wchar_t* fullPath, DWORD access, DWORD share, LPSECURITY_ATTRIBUTES sa, DWORD disposition,
                 DWORD flags, HANDLE templ) {
    (void)share; (void)disposition; (void)templ; (void)access;
    HANDLE own = ArchiveOpen(fullPath, sa, flags);      // the resource's own bytes, when an archive holds it
    if (own != INVALID_HANDLE_VALUE) {
        GuardHit(GUARD_FROM_ARCHIVES, 0, fullPath);
        return own;
    }
    if (!g_guard) return INVALID_HANDLE_VALUE;
    const wchar_t* dot = wcsrchr(fullPath, L'.');
    if (!dot || _wcsicmp(dot, L".tex") != 0) return INVALID_HANDLE_VALUE;
    if (!EnsureStandIn()) return INVALID_HANDLE_VALUE;
    HANDLE h = Real_CreateFileW(g_standInPath, GENERIC_READ, FILE_SHARE_READ, sa, OPEN_EXISTING,
                                flags & ~(DWORD)FILE_FLAG_DELETE_ON_CLOSE, NULL);
    if (h == INVALID_HANDLE_VALUE) return h;
    LONG k = InterlockedIncrement(&g_fallbacks);
    GuardHit(GUARD_MISSING_TEXTURES, 0, fullPath);
    if (k <= 200) LogLine(L"guard    %s does not exist; the game gets a %s stand-in texture instead of stopping", fullPath,
                          g_standInMagenta ? L"magenta" : L"neutral");
    LiveNote("last_fallback", fullPath);
    return h;
}

// A loose .tex that does not fit its own file (docs/stability-membrane.md, [guard] broken_textures): only
// unambiguous breakage is caught, never a valid texture, so a good mod texture is never replaced.  rTexture's
// header (tex.py): u32 magic "TEX\0", u32 (revision in the low 12 bits, 0x099 Dark Arisen / 0x09D Online),
// u32 mip:6|width:13|height:13, u32 depth:8|..., then mip*depth absolute file offsets.  The file hook answers a
// broken one as if it were missing (the archive's own copy, else the stand-in).
BOOL TextureFileBroken(const wchar_t* path, wchar_t* why, size_t cap) {
    HANDLE h = Real_CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING, 0, NULL);
    if (h == INVALID_HANDLE_VALUE) return FALSE;                 // unreadable: not this guard's call
    LARGE_INTEGER size;
    BYTE hdr[16];
    DWORD got = 0;
    BOOL haveSize = GetFileSizeEx(h, &size);
    BOOL haveHdr = ReadFile(h, hdr, 16, &got, NULL) && got == 16;
    auto say = [&](const wchar_t* msg) { if (why && cap) wcsncpy_s(why, cap, msg, _TRUNCATE); CloseHandle(h); return TRUE; };
    if (!haveSize) { CloseHandle(h); return FALSE; }
    if (!haveHdr) return say(L"shorter than a texture header");
    DWORD magic = *(DWORD*)hdr, w1 = *(DWORD*)(hdr + 4), w2 = *(DWORD*)(hdr + 8), w3 = *(DWORD*)(hdr + 12);
    if (magic != 0x00584554u) return say(L"not a TEX texture");
    DWORD version = w1 & 0xFFF;
    if (version != 0x099 && version != 0x09D) return say(L"an unknown texture revision");
    DWORD mip = w2 & 0x3F, depth = w3 & 0xFF, width = (w2 >> 6) & 0x1FFF, height = (w2 >> 19) & 0x1FFF;
    if (mip == 0 || depth == 0 || width == 0 || height == 0) return say(L"zero mips, depth or size");
    uint64_t need = 16ULL + 4ULL * mip * depth;                 // the header and the offset table
    if ((uint64_t)size.QuadPart < need) return say(L"truncated: no room for its mip offsets");
    // Only a flat 2D texture (shape 0x20000) has its mips as one run of offsets that must sit inside the file;
    // a cube (0x60000) stores six faces and a volume (0x30000) lays its mips out differently, so tex.py checks
    // offsets only for flat textures.  Doing otherwise flags a valid cubemap as broken (seen in game 2026-09-28,
    // DefaultCube_CM.tex), which would swap the game's reflections for the stand-in.
    BOOL flat = ((w1 >> 12) & 0x70000) == 0x20000;
    DWORD n = mip * depth;
    if (flat && n <= 384) {
        DWORD offs[384];
        if (ReadFile(h, offs, 4 * n, &got, NULL) && got == 4 * n)
            for (DWORD i = 0; i < n; i++)
                if (offs[i] < 16 || (uint64_t)offs[i] > (uint64_t)size.QuadPart) return say(L"truncated: a mip is past the file's end");
    }
    CloseHandle(h);
    return FALSE;
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
    GuardsInit();
    wchar_t look[32];
    IniStr(L"guard", L"missing_texture_look", L"grey", look, _countof(look));
    g_standInMagenta = _wcsicmp(look, L"magenta") == 0;
    BuildStandIn();
    g_guard = IniInt(L"guard", L"missing_textures", 1) != 0 && (g_game == GAME_DDDA || g_game == GAME_DDO ||
                                                                 IniInt(L"loader", L"any_program", 0));
    GuardMark(GUARD_MISSING_TEXTURES, g_guard);
    LogLine(L"guard    missing textures %s", !g_guard ? L"stop the game as usual"
                                             : g_standInMagenta ? L"get a magenta stand-in (riftstone\\standin)"
                                                                : L"get a neutral stand-in (riftstone\\standin)");
    BOOL broken = IniInt(L"guard", L"broken_textures", 1) != 0 &&
                  (g_game == GAME_DDDA || g_game == GAME_DDO || IniInt(L"loader", L"any_program", 0));
    GuardMark(GUARD_BROKEN_TEXTURES, broken);
    if (broken) LogLine(L"guard    a loose texture that does not fit its file is answered as missing (a good copy or the stand-in)");
    ResourcesInit();
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
static DWORD g_shadowSet = 0;                    // the HIGH sun shadow size ApplyShadowSize wrote (0 = table untouched)

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
    g_shadowSet = (DWORD)size;                    // FixesDeviceCreated bounds this to the GPU once the device exists
    LogLine(L"shadows  ShadowQuality=HIGH now draws %d px sun shadows (%d px for lamps and torches); was 2048", size, size / 2);
}

// The device the game created (graphics.cpp calls this once, before the first frame, with the GPU's texture
// limits).  ApplyShadowSize raised the sun shadow map at start-up, before any device existed; a size past what
// this GPU can create fails the shadow buffer's CreateTexture and takes the frame's depth pass down.  [guard]
// shadow_buffers (on unless 0) bounds the raised size to the device's largest texture here -- the lamp maps are
// half the sun, so they follow.  Vanilla (2048) is under every real device's limit, so this only ever touches a
// size the loader itself raised, and never below what the GPU allows.
void FixesDeviceCreated(DWORD maxTextureWidth, DWORD maxTextureHeight) {
    if (g_shadowSet == 0 || !g_knownBuild) return;              // only a size we raised can exceed the GPU
    if (IniInt(L"guard", L"shadow_buffers", 1) == 0) return;
    DWORD maxDim = maxTextureWidth < maxTextureHeight ? maxTextureWidth : maxTextureHeight;
    if (maxDim == 0) return;                                    // limits unknown: leave the raised size alone
    if (!SiteMatches(SHADOW_READER) || !Readable((const void*)SHADOW_TABLE, 12)) return;
    DWORD* tbl = (DWORD*)SHADOW_TABLE;
    if (tbl[0] != 512 || tbl[1] != 1024 || tbl[2] != g_shadowSet) return;   // only our own, still-untouched raise
    if (tbl[2] <= maxDim) return;                              // the GPU can make it
    DWORD safe = maxDim & ~31u;                                // the largest multiple of 32 the GPU allows
    if (safe == 0 || safe >= tbl[2]) return;
    DWORD was = tbl[2], old;
    if (!VirtualProtect(tbl + 2, 4, PAGE_READWRITE, &old)) return;
    tbl[2] = safe;
    VirtualProtect(tbl + 2, 4, old, &old);
    g_shadowSet = safe;                                        // the bounded value is ours now
    GuardHit(GUARD_SHADOW_BUFFERS, SHADOW_TABLE, NULL);
    LogLine(L"shadows  this GPU's largest texture is %lu px; the %lu px sun shadow was bounded to %lu (lamps %lu)",
            maxDim, was, safe, safe / 2);
}

// Whether the owner asked for a raised sun shadow map ([render] shadow_map_size).  Read straight from the ini, so
// it is answerable before ApplyShadowSize runs -- live.cpp needs the device hook installed for the bound above.
BOOL FixesShadowSizeSet() { return IniInt(L"render", L"shadow_map_size", 0) != 0; }

// ---------------------------------------------------------------------------
// ragdolls (DDDA build 2364871): a ragdoll's bodies live in a container (uRagdollExt and its kin) whose body data
// (+0x38) holds the count at +0x68 >> 8, and whose +0x4C lists the bodies.  The game's own count (0x010805D0) is 0
// while that data is not there yet; four walks read the count inline without that check, after checking only that
// the container exists.  A goblin whose ragdoll was not set up yet stopped the game in the first of them (2026-09-27
// 20:53, 0x00794942 reading 0x68, a horde of 50 at Gran Soren).  [guard] ragdoll_bodies (on unless 0): such a ragdoll has
// no bodies, the game's own answer, so the walk sets nothing instead of stopping the game.
//   0x00794930, 0x007949E0  every body of an enemy's ragdoll and its collision set gets a value (+8, +0xC): replaced
//                           by the same walk that counts the game's way (and leaves in eax what the game's code does)
//   0x008CF2D8, 0x00C2BAE0  the same count read inline for a character's ragdoll (+0x1EFC, +0x1EF8): a jump to a
//                           check that skips the walk when the data or the body list is not there

struct GuardSite {
    DWORD_PTR va;
    BYTE expect[18];
    int len;                                     // bytes checked; the first ones are replaced by a jump
    const wchar_t* what;
};
static const GuardSite RAGDOLL_SITES[] = {
    {0x00794930, {0x8B, 0x47, 0x24, 0x53, 0x8B, 0x5C, 0x24, 0x08, 0x56, 0x85, 0xC0, 0x74, 0x4F, 0x8B, 0x48, 0x38, 0x33, 0xF6},
     18, L"an enemy's ragdoll: every body's +8"},
    {0x007949E0, {0x8B, 0x47, 0x24, 0x53, 0x8B, 0x5C, 0x24, 0x08, 0x56, 0x85, 0xC0, 0x74, 0x4F, 0x8B, 0x48, 0x38, 0x33, 0xF6},
     18, L"an enemy's ragdoll: every body's +0xC"},
    {0x008CF2D8, {0x8B, 0x48, 0x38, 0x33, 0xF6, 0xF7, 0x41, 0x68, 0x00, 0xFF, 0xFF, 0xFF, 0x76, 0x28, 0x85, 0xC0, 0x74, 0x0F},
     18, L"a character's ragdoll (+0x1EFC)"},
    {0x00C2BAE0, {0x8B, 0x48, 0x38, 0x33, 0xF6, 0xF7, 0x41, 0x68, 0x00, 0xFF, 0xFF, 0xFF, 0x89, 0x44, 0x24, 0x0C, 0x76, 0x1D},
     18, L"a character's ragdoll (+0x1EF8)"},
    // the game's own accessors, whose layout the walks below follow: the count (0 without data) and a body
    {0x010805D0, {0x8B, 0x41, 0x38, 0x85, 0xC0, 0x74, 0x07, 0x8B, 0x40, 0x68, 0xC1, 0xE8, 0x08, 0xC3},
     14, L"the ragdoll body count"},
    {0x01080610, {0x8B, 0x41, 0x4C, 0x8B, 0x4C, 0x24, 0x04, 0x8B, 0x04, 0x88, 0xC2, 0x04, 0x00},
     13, L"a ragdoll body"},
    // where the two inline walks go on (the loop) and where they go when there is nothing to walk
    {0x008CF30E, {0x80, 0xBB, 0x6C, 0x2D, 0x00, 0x00, 0x00}, 7, L"after a character's walk (+0x1EFC)"},
    {0x00C2BAF2, {0x8B, 0x50, 0x4C, 0x8B, 0x0C, 0xB2}, 6, L"a character's walk (+0x1EF8)"},
    {0x00C2BB0F, {0x8B, 0x8F, 0xF8, 0x1E, 0x00, 0x00}, 6, L"after a character's walk (+0x1EF8)"},
};
static const int RAGDOLL_JUMPS = 4;              // the first four sites get a jump; the rest are only checked

// The game's count (0x010805D0): 0 while the ragdoll's body data is not there.
static DWORD RagdollCount(const BYTE* c) {
    const BYTE* data = c ? *(BYTE* const*)(c + 0x38) : NULL;
    return data ? *(const DWORD*)(data + 0x68) >> 8 : 0;
}
// A body (0x01080610, for i below the count): NULL when the list is not there either.
static BYTE* RagdollBody(const BYTE* c, DWORD i) {
    BYTE* const* list = *(BYTE* const* const*)(c + 0x4C);
    return list ? list[i] : NULL;
}
// A container the game's own inline walks would read through a null pointer: no body data, or a count with no
// body list.  (One with data and no bodies is the game's own "none" and is not counted.)
static BOOL RagdollBroken(const BYTE* c) {
    const BYTE* data = *(BYTE* const*)(c + 0x38);
    return !data || ((*(const DWORD*)(data + 0x68) >> 8) && !*(BYTE* const* const*)(c + 0x4C));
}
static void RagdollGive(BYTE* body, DWORD field, DWORD value) {
    if (!body) return;
    BYTE* part = *(BYTE**)(body + 0x18);
    if (part) *(DWORD*)(part + field) = value;
}

// 0x00794930 / 0x007949E0 with the game's count: the ragdoll's bodies (with the set's alongside), then the set's
// (with the ragdoll's alongside), each index below its own container's count.  Returns what the game's code
// leaves in eax: the set's count, or the ragdoll container when there is no set.
extern "C" DWORD __stdcall RagdollSetBodies(BYTE* self, DWORD field, DWORD value) {
    BYTE* ragdoll = *(BYTE**)(self + 0x24);
    BYTE* set = *(BYTE**)(self + 0x20);
    if ((ragdoll && RagdollBroken(ragdoll)) || (set && RagdollBroken(set)))
        GuardHit(GUARD_RAGDOLL_BODIES, field == 8 ? 0x00794930 : 0x007949E0, NULL);
    DWORD n = RagdollCount(ragdoll), m = RagdollCount(set);
    for (DWORD i = 0; i < n; i++) {
        RagdollGive(RagdollBody(ragdoll, i), field, value);
        if (set && i < m) RagdollGive(RagdollBody(set, i), field, value);
    }
    if (!set) return (DWORD)(DWORD_PTR)ragdoll;
    for (DWORD i = 0; i < m; i++) {
        if (ragdoll && i < n) RagdollGive(RagdollBody(ragdoll, i), field, value);
        RagdollGive(RagdollBody(set, i), field, value);
    }
    return m;
}

// Entered in place of the two functions (edi = the object, [esp+4] = the value, ret 4).
__declspec(naked) static void RagdollSet08() {
    __asm {
        push dword ptr [esp + 4]
        push 8
        push edi
        call RagdollSetBodies
        ret 4
    }
}
__declspec(naked) static void RagdollSet0C() {
    __asm {
        push dword ptr [esp + 4]
        push 0x0C
        push edi
        call RagdollSetBodies
        ret 4
    }
}

// Entered in place of the inline count reads (eax = the ragdoll container, which the game checked or trusts): the
// game's own instructions, then the walk only when the data and the body list are there.
DWORD_PTR g_ragdollOn1 = 0x008CF2E6, g_ragdollOff1 = 0x008CF30E, g_ragdollOn2 = 0x00C2BAF2, g_ragdollOff2 = 0x00C2BB0F;
// A walk that would have read through a null pointer: counted, every register and flag kept.
extern "C" void __stdcall RagdollInlineHit(DWORD site) { GuardHit(GUARD_RAGDOLL_BODIES, site, NULL); }
__declspec(naked) static void RagdollWalk1() {   // 0x008CF2D8
    __asm {
        mov ecx, [eax + 0x38]
        xor esi, esi
        test ecx, ecx
        jz broken
        test dword ptr [ecx + 0x68], 0xFFFFFF00
        jbe nothing                             // no bodies: the game's own way out
        cmp dword ptr [eax + 0x4C], 0
        je broken
        jmp dword ptr [g_ragdollOn1]
    broken:
        pushfd
        pushad
        push 0x008CF2D8
        call RagdollInlineHit
        popad
        popfd
    nothing:
        jmp dword ptr [g_ragdollOff1]
    }
}
__declspec(naked) static void RagdollWalk2() {   // 0x00C2BAE0
    __asm {
        mov ecx, [eax + 0x38]
        xor esi, esi
        mov [esp + 0x0C], eax
        test ecx, ecx
        jz broken
        test dword ptr [ecx + 0x68], 0xFFFFFF00
        jbe nothing
        cmp dword ptr [eax + 0x4C], 0
        je broken
        jmp dword ptr [g_ragdollOn2]
    broken:
        pushfd
        pushad
        push 0x00C2BAE0
        call RagdollInlineHit
        popad
        popfd
    nothing:
        jmp dword ptr [g_ragdollOff2]
    }
}

static BOOL g_ragdollGuard = FALSE;              // for the reports

static void ApplyRagdollGuard() {
    g_ragdollGuard = FALSE;
    GuardMark(GUARD_RAGDOLL_BODIES, FALSE);
    if (IniInt(L"guard", L"ragdoll_bodies", 1) == 0 || g_game != GAME_DDDA) return;
    if (!g_knownBuild) {
        LogLine(L"ragdoll  guard not applied: this is not DDDA build 2364871");
        return;
    }
    for (const GuardSite& s : RAGDOLL_SITES) {
        if (!Readable((const void*)s.va, s.len) || memcmp((const void*)s.va, s.expect, s.len) != 0) {
            LogLine(L"ragdoll  the code at 0x%08lx (%s) is not what build 2364871 has; nothing patched", (DWORD)s.va, s.what);
            return;
        }
    }
    void* const to[RAGDOLL_JUMPS] = {(void*)RagdollSet08, (void*)RagdollSet0C, (void*)RagdollWalk1, (void*)RagdollWalk2};
    const int len[RAGDOLL_JUMPS] = {5, 5, 14, 18};     // the whole functions: a jump at the top; inline: the reads
    for (int i = 0; i < RAGDOLL_JUMPS; i++) {
        BYTE* at = (BYTE*)RAGDOLL_SITES[i].va;
        DWORD old;
        if (!VirtualProtect(at, len[i], PAGE_EXECUTE_READWRITE, &old)) {
            LogLine(L"ragdoll  0x%08lx could not be made writable; the guard is partly in", (DWORD)(DWORD_PTR)at);
            return;
        }
        at[0] = 0xE9;
        *(int32_t*)(at + 1) = (int32_t)((BYTE*)to[i] - (at + 5));
        for (int k = 5; k < len[i]; k++) at[k] = 0x90;
        VirtualProtect(at, len[i], old, &old);
    }
    FlushInstructionCache(GetCurrentProcess(), NULL, 0);
    g_ragdollGuard = TRUE;
    GuardMark(GUARD_RAGDOLL_BODIES, TRUE);
    LogLine(L"ragdoll  guard on: a ragdoll whose bodies are not set up yet has none to walk (4 sites), instead of "
            L"stopping the game");
}

// ---------------------------------------------------------------------------
// the ragdoll body-count family (DDDA build 2364871): the four sites above are hand-written because each
// does more than read the count (two set a field on every body, two are inline walks with their own loop).
// Every other inlined copy of the idiom -- read [bodydata+0x68] through bodydata = [holder+0x38] with no
// null-check -- is in ragdoll_sites.inc, which tools/ragdoll_sites.py writes from the exe by following each
// read of +0x68 back to the instruction that loaded its pointer (docs/stability-membrane.md).  Three shapes:
//   'T'  test dword [reg+0x68], 0xFFFFFF00     (7 bytes: the "any bodies?" test before a walk)
//   'C'  mov reg2,[reg+0x68] ; shr reg2, 8     (6 bytes: a count read feeding a loop)
//   'M'  test dword [reg+0x68], M              (3 bytes, M = 0xFFFFFF00 in a register: the span starts `pre`
//                                               bytes earlier, at straight-line instructions the stub runs first)
// Each site keeps its exact bytes, so the guard reproduces the game's own instruction when bodydata is set
// and its "0 without data" answer when it is null, without needing to know the site's skip target: for 'T'
// and 'M' the null case leaves the flags an all-zero count would (test reg,reg on a null reg == test 0,mask),
// so whatever branch follows takes its no-bodies path; for 'C' the null case leaves the count register 0.
struct FamilySite {
    DWORD_PTR va;                                // where the patch starts
    char kind;                                   // 'T', 'C' or 'M'
    BYTE len;                                    // bytes replaced: 7 for 'T', 6 for 'C', pre + 3 for 'M'
    BYTE pre;                                    // bytes before the read, run first in the stub (0 but for 'M')
    BYTE bytes[10];                              // the game's own instruction(s)
};
#include "ragdoll_sites.inc"

// A body walk that would have read through a null bodydata: counted (any thread, cheap), like the four above.
extern "C" void __stdcall RagdollFamilyHit(DWORD site) { GuardHit(GUARD_RAGDOLL_BODIES, site, NULL); }

// Emit one trampoline into `cur` (advanced) and return its entry.  `orig`/`len` are the site's own bytes, the
// first `pre` of them straight-line instructions before the read; `back` is where both paths continue (the site
// just past `orig`); `hit` is called only in the null case.
//   <pre> ; test R,R ; jz null ; <read> ; jmp back ; null: pushad;pushfd; push siteVA; call hit; popfd;popad;
//   ['C' only] xor reg2,reg2 ; jmp back
// R is the base of the read (orig[pre+1] & 7); for 'C' reg2 is the count register ((orig[pre+1] >> 3) & 7).
static BYTE* EmitFamilyStub(BYTE*& cur, char kind, const BYTE* orig, int len, int pre, DWORD_PTR siteVA,
                            DWORD_PTR back, void* hit) {
    BYTE R = orig[pre + 1] & 7;
    BYTE* entry = cur;
    memcpy(cur, orig, pre); cur += pre;                                       // the instructions before the read
    *cur++ = 0x85; *cur++ = (BYTE)(0xC0 + R * 9);                              // test R, R
    *cur++ = 0x74; BYTE* jz = cur++;                                          // jz null (rel8, filled below)
    memcpy(cur, orig + pre, len - pre); cur += len - pre;                     // the game's own read
    *cur++ = 0xE9; { int32_t r = (int32_t)(back - ((DWORD_PTR)cur + 4)); memcpy(cur, &r, 4); cur += 4; }  // jmp back
    *jz = (BYTE)(cur - (jz + 1));                                             // null:
    *cur++ = 0x60; *cur++ = 0x9C;                                             // pushad; pushfd
    *cur++ = 0x68; memcpy(cur, &siteVA, 4); cur += 4;                         // push siteVA
    *cur++ = 0xE8; { int32_t r = (int32_t)((DWORD_PTR)hit - ((DWORD_PTR)cur + 4)); memcpy(cur, &r, 4); cur += 4; }  // call hit
    *cur++ = 0x9D; *cur++ = 0x61;                                            // popfd; popad
    if (kind == 'C') { BYTE r2 = (orig[pre + 1] >> 3) & 7; *cur++ = 0x33; *cur++ = (BYTE)(0xC0 + r2 * 9); }  // xor reg2,reg2
    *cur++ = 0xE9; { int32_t r = (int32_t)(back - ((DWORD_PTR)cur + 4)); memcpy(cur, &r, 4); cur += 4; }  // jmp back
    return entry;
}

static const int RAG_CAVE_SIZE = 4096;           // 62 stubs of at most 36 bytes fit in one page
static BYTE* g_ragCave = NULL;
static int g_ragFamilyCount = 0, g_ragFamilySkipped = 0;

// Applied after the four bespoke sites, under the same [guard] ragdoll_bodies key.  Each site is verified
// byte-for-byte; a site that differs is left alone and the rest still go in (they are independent reads).
static void ApplyRagdollFamily() {
    g_ragFamilyCount = 0;
    g_ragFamilySkipped = 0;
    if (IniInt(L"guard", L"ragdoll_bodies", 1) == 0 || g_game != GAME_DDDA || !g_knownBuild) return;
    if (!g_ragCave) {
        g_ragCave = (BYTE*)VirtualAlloc(NULL, RAG_CAVE_SIZE, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
        if (!g_ragCave) {
            LogLine(L"ragdoll  family guard: no code cave available; the four-site guard still stands");
            return;
        }
    }
    BYTE* cur = g_ragCave;
    for (const FamilySite& s : FAMILY_SITES) {
        int len = s.len;
        BYTE* at = (BYTE*)s.va;
        if (!Readable(at, len) || memcmp(at, s.bytes, len) != 0) {
            g_ragFamilySkipped++;
            LogLine(L"ragdoll  family site 0x%08lx is not what build 2364871 has; left unguarded", (DWORD)s.va);
            continue;
        }
        if (cur + 64 > g_ragCave + RAG_CAVE_SIZE) {
            LogLine(L"ragdoll  family cave full after %d sites", g_ragFamilyCount);
            break;
        }
        BYTE* stub = EmitFamilyStub(cur, s.kind, s.bytes, len, s.pre, (DWORD_PTR)s.va, s.va + len,
                                     (void*)RagdollFamilyHit);
        DWORD old;
        if (!VirtualProtect(at, len, PAGE_EXECUTE_READWRITE, &old)) {
            g_ragFamilySkipped++;
            continue;
        }
        at[0] = 0xE9;
        *(int32_t*)(at + 1) = (int32_t)(stub - (at + 5));
        for (int k = 5; k < len; k++) at[k] = 0x90;
        VirtualProtect(at, len, old, &old);
        g_ragFamilyCount++;
    }
    FlushInstructionCache(GetCurrentProcess(), NULL, 0);
    LogLine(L"ragdoll  family guard: %d more body-count walks made safe%s; a ragdoll whose bodies are not set up "
            L"yet reads a count of 0 (the game's own answer at 0x010805D0)", g_ragFamilyCount,
            g_ragFamilySkipped ? L" (some sites differed and were left alone)" : L"");
}

// ---------------------------------------------------------------------------
// GUI text fields (DDDA build 2364871): the world map crashed reading a null string (the owner's session of
// 2026-09-27 22:56, at 0x00606628, with a Portcrystal count past the game's ten).  0x006065E0 sets a GUI text
// field from a looked-up string: it calls the string lookup 0x00607960 (which returns null when the string is
// not in sGameSys's table), then walks the result as a C string.  Its own guard tests the wrong pointer --
// 0x0060661F tests sGameSys+0xA76D0 (the Arisen's cPlayerInfo, an address that is never null) instead of the
// string -- so a null lookup walks address 0.  [guard] gui_text (on unless 0): a null string becomes the empty
// string, so the field shows nothing (an unlabelled map icon) instead of stopping the game.  This is the
// engine's own "no string" case; a mod with a Portcrystal or place name the game has no label for no longer
// takes the map down.
static char g_guiEmpty[1] = {0};                 // the empty string the null case reads instead of address 0
DWORD_PTR g_guiBack = 0x00606626;                // the je the vanilla code reaches after mov ecx,eax; lea esi
extern "C" void __stdcall GuiTextHit(DWORD site) { GuardHit(GUARD_GUI_TEXT, site, NULL); }

// Entered in place of `mov ecx, eax; lea esi, [ecx+1]` at 0x00606621 (eax = the looked-up string, maybe null):
// keep the game's two instructions, but when the string is null count it and use the empty string; leave the
// flags the vanilla code's own `test` did (ecx non-null now, so its je takes the same branch as ever).
__declspec(naked) static void GuiTextThunk() {
    __asm {
        test eax, eax
        jnz  have
        pushfd
        pushad
        push 0x006065E0
        call GuiTextHit
        popad
        popfd
        mov  eax, offset g_guiEmpty
    have:
        mov  ecx, eax
        lea  esi, [ecx + 1]
        test ecx, ecx
        jmp  dword ptr [g_guiBack]
    }
}

static const GuardSite GUI_TEXT_SITE = {0x00606621, {0x8B, 0xC8, 0x8D, 0x71, 0x01}, 5, L"the map's GUI text field"};
static BOOL g_guiTextGuard = FALSE;

static void ApplyGuiTextGuard() {
    g_guiTextGuard = FALSE;
    GuardMark(GUARD_GUI_TEXT, FALSE);
    if (IniInt(L"guard", L"gui_text", 1) == 0 || g_game != GAME_DDDA || !g_knownBuild) return;
    if (!Readable((const void*)GUI_TEXT_SITE.va, GUI_TEXT_SITE.len) ||
        memcmp((const void*)GUI_TEXT_SITE.va, GUI_TEXT_SITE.expect, GUI_TEXT_SITE.len) != 0) {
        LogLine(L"gui      the code at 0x%08lx (%s) is not what build 2364871 has; nothing patched", (DWORD)GUI_TEXT_SITE.va,
                GUI_TEXT_SITE.what);
        return;
    }
    BYTE* at = (BYTE*)GUI_TEXT_SITE.va;
    DWORD old;
    if (!VirtualProtect(at, 5, PAGE_EXECUTE_READWRITE, &old)) {
        LogLine(L"gui      0x%08lx could not be made writable; nothing patched", (DWORD)GUI_TEXT_SITE.va);
        return;
    }
    at[0] = 0xE9;
    *(int32_t*)(at + 1) = (int32_t)((BYTE*)GuiTextThunk - (at + 5));
    VirtualProtect(at, 5, old, &old);
    FlushInstructionCache(GetCurrentProcess(), NULL, 0);
    g_guiTextGuard = TRUE;
    GuardMark(GUARD_GUI_TEXT, TRUE);
    LogLine(L"gui      guard on: a GUI text field whose string is missing shows empty, instead of stopping the game "
            L"(the world map's place labels with extra Portcrystals)");
}

// ---------------------------------------------------------------------------
// foliage/water/effects streaming window (DDDA build 2364871, docs/re-engine-audit.md).  On stage 100,
// uStageSplitCtrl loads cells by a window: models and collision use 5x5 (aStage+0x7F0/+0x7F4), but the swaying
// foliage (updateFmMdl), the split_sub archives (updateArcSub), the effect providers (updateEpv) and the water
// (updateWater) read the narrower 3x3 layout window (aStage+0x800/+0x804), so they pop in at the 3x3 edge while
// the terrain is drawn well past it.  [render] wide_foliage (off unless 1) points those four updaters' reads at
// the 5x5 model counts instead, so they stream about as far as the terrain.  Enemies and objects (updateLot)
// keep 3x3.  Only the displacement in each `mov` changes (0x800 -> 0x7F0, 0x804 -> 0x7F4); no new code.  The
// memory cost of the wider window in game is UNKNOWN (5x5 is ~2.8x the cells of 3x3), so it is off by default.
struct StreamSite {
    DWORD_PTR va;
    BYTE expect[6];                              // mov reg, [aStage + 0x800 or 0x804]
    DWORD newDisp;                               // 0x7F0 or 0x7F4 (the 5x5 model counts)
    const wchar_t* what;
};
static const StreamSite STREAM_SITES[] = {
    {0x00C5E776, {0x8B, 0x82, 0x00, 0x08, 0x00, 0x00}, 0x7F0, L"split_sub archives (X)"},
    {0x00C5E77C, {0x8B, 0x92, 0x04, 0x08, 0x00, 0x00}, 0x7F4, L"split_sub archives (Z)"},
    {0x00C613FE, {0x8B, 0x82, 0x00, 0x08, 0x00, 0x00}, 0x7F0, L"swaying foliage (X)"},
    {0x00C61404, {0x8B, 0x9A, 0x04, 0x08, 0x00, 0x00}, 0x7F4, L"swaying foliage (Z)"},
    {0x00C623DF, {0x8B, 0x82, 0x00, 0x08, 0x00, 0x00}, 0x7F0, L"effect providers (X)"},
    {0x00C623E5, {0x8B, 0xB2, 0x04, 0x08, 0x00, 0x00}, 0x7F4, L"effect providers (Z)"},
    {0x00C63225, {0x8B, 0x87, 0x00, 0x08, 0x00, 0x00}, 0x7F0, L"water (X)"},
    {0x00C6322B, {0x8B, 0xBF, 0x04, 0x08, 0x00, 0x00}, 0x7F4, L"water (Z)"},
};
static BOOL g_wideFoliage = FALSE;

static void ApplyStreamWindow() {
    g_wideFoliage = FALSE;
    if (IniInt(L"render", L"wide_foliage", 0) == 0 || g_game != GAME_DDDA || !g_knownBuild) return;
    for (const StreamSite& s : STREAM_SITES)
        if (!Readable((const void*)s.va, 6) || memcmp((const void*)s.va, s.expect, 6) != 0) {
            LogLine(L"stream   0x%08lx (%s) is not what build 2364871 has; the window is left at 3x3", (DWORD)s.va, s.what);
            return;
        }
    for (const StreamSite& s : STREAM_SITES) {
        BYTE* disp = (BYTE*)s.va + 2;
        DWORD old;
        if (!VirtualProtect(disp, 4, PAGE_EXECUTE_READWRITE, &old)) continue;
        *(DWORD*)disp = s.newDisp;
        VirtualProtect(disp, 4, old, &old);
    }
    FlushInstructionCache(GetCurrentProcess(), NULL, 0);
    g_wideFoliage = TRUE;
    LogLine(L"stream   wide_foliage on: swaying foliage, water and effects stream in the 5x5 window (was 3x3); "
            L"enemies and objects keep 3x3");
}

// ---------------------------------------------------------------------------
// effect-system dispatch (DDDA build 2364871): 0x010CBFA0 reads a tag byte at [a1+3] and returns a field by it.
// When an effect's parameter block is laid out as Online lays it (the compat layer converts Online effects), a1
// points nowhere and [a1+3] faults (crash 0x010CBFA4, seen three times 2026-09-26).  a1 is not null but garbage,
// so a null-check would not catch it.  [guard] particles reimplements the function -- traced byte for byte from
// 0x010CBFA0, with ecx = [a2+4] as both callers set it (0x010CFDCD, 0x010CFE54) -- inside a structured exception
// frame: a fault in any of its reads is contained and the function returns the game's own default ([a3+0x14]),
// the same as an unrecognised tag, instead of taking the game down.  Only this one function's reads are wrapped;
// nothing else's exceptions are touched.
static uint32_t EffectDispatchImpl(void* a1, void* a2, void* a3) {
    BYTE tag = (BYTE)(*((BYTE*)a1 + 3)) & 7;                     // the read that faults on a bad a1
    char* c2 = (char*)a2;
    switch (tag) {
        case 1: return *(uint32_t*)(c2 + 0x88);
        case 2: { char* ecx = *(char**)(c2 + 4); return *(uint32_t*)(ecx + 0x10C); }
        case 3: {
            char* ecx = *(char**)(c2 + 4);
            char* p = *(char**)(ecx + 0x1D0);
            return p ? *(uint32_t*)(p + 0x10C) : *(uint32_t*)(ecx + 0x10C);
        }
        case 4: { char* g = *(char**)0x018D2818; return *(uint32_t*)(g + 0x74); }
        default: return *(uint32_t*)((char*)a3 + 0x14);
    }
}
extern "C" uint32_t __stdcall EffectDispatchGuarded(void* a1, void* a2, void* a3) {
    __try {
        return EffectDispatchImpl(a1, a2, a3);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        GuardHit(GUARD_PARTICLES, 0x010CBFA0, NULL);
        __try { return *(uint32_t*)((char*)a3 + 0x14); }        // the game's own default, itself guarded
        __except (EXCEPTION_EXECUTE_HANDLER) { return 0; }
    }
}
static const GuardSite PARTICLE_SITE = {0x010CBFA0,
    {0x8B, 0x44, 0x24, 0x04, 0x0F, 0xB6, 0x40, 0x03, 0x83, 0xE0, 0x07, 0x48}, 12, L"the effect dispatch"};
static BOOL g_particleGuard = FALSE;

static void ApplyParticleGuard() {
    g_particleGuard = FALSE;
    GuardMark(GUARD_PARTICLES, FALSE);
    if (IniInt(L"guard", L"particles", 1) == 0 || g_game != GAME_DDDA || !g_knownBuild) return;
    if (!Readable((const void*)PARTICLE_SITE.va, PARTICLE_SITE.len) ||
        memcmp((const void*)PARTICLE_SITE.va, PARTICLE_SITE.expect, PARTICLE_SITE.len) != 0) {
        LogLine(L"particle the code at 0x%08lx (%s) is not what build 2364871 has; nothing patched", (DWORD)PARTICLE_SITE.va,
                PARTICLE_SITE.what);
        return;
    }
    BYTE* at = (BYTE*)PARTICLE_SITE.va;
    DWORD old;
    if (!VirtualProtect(at, 5, PAGE_EXECUTE_READWRITE, &old)) {
        LogLine(L"particle 0x%08lx could not be made writable; nothing patched", (DWORD)PARTICLE_SITE.va);
        return;
    }
    at[0] = 0xE9;
    *(int32_t*)(at + 1) = (int32_t)((BYTE*)EffectDispatchGuarded - (at + 5));
    VirtualProtect(at, 5, old, &old);
    FlushInstructionCache(GetCurrentProcess(), NULL, 0);
    g_particleGuard = TRUE;
    GuardMark(GUARD_PARTICLES, TRUE);
    LogLine(L"particle guard on: a fault reading an effect's parameter block (a converted Online effect) is "
            L"contained; the dispatch returns the game's default instead of stopping the game");
}

void FixesApplyPatches() {
    ApplyFpsCeiling();
    ApplyShadowSize();
    ApplyRagdollGuard();
    ApplyRagdollFamily();
    ApplyGuiTextGuard();
    ApplyStreamWindow();
    ApplyParticleGuard();
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

// A backup folder's name: <YYYYMMDD-HHMMSS> as Stamp() makes it, or <YYYYMMDD-HHMMSS>-<n> when that second
// already has one (the loader's own, and runtime.backup_saves').  Nothing else in the folder was made by a
// backup: it is never taken for the newest backup, never counted and never pruned.
#define STAMP_CAP 32
static BOOL IsBackupStamp(const wchar_t* name) {
    for (int i = 0; i < 15; i++) {
        wchar_t c = name[i];
        if (i == 8 ? c != L'-' : (c < L'0' || c > L'9')) return FALSE;
    }
    if (!name[15]) return TRUE;
    if (name[15] != L'-' || !name[16]) return FALSE;
    for (int i = 16; name[i]; i++)
        if (i > 24 || name[i] < L'0' || name[i] > L'9') return FALSE;
    return TRUE;
}

// Time order of two backup names: the date and time, then -<n> (none first).
static int StampOrder(const void* a, const void* b) {
    const wchar_t* x = (const wchar_t*)a;
    const wchar_t* y = (const wchar_t*)b;
    int c = wcsncmp(x, y, 15);
    if (c) return c;
    long xn = x[15] ? wcstol(x + 16, NULL, 10) : 0, yn = y[15] ? wcstol(y + 16, NULL, 10) : 0;
    return xn < yn ? -1 : xn > yn;
}

// Copy every file of one remote folder into <target>\<stamp>, unless the newest backup already
// holds the same save.  Keep the newest `keep` backups; other folders in <target> are left alone.
static void BackupFolder(const wchar_t* remote, const wchar_t* target, int keep) {
    wchar_t sav[MAX_PATH];
    _snwprintf_s(sav, _countof(sav), _TRUNCATE, L"%s\\DDDA.sav", remote);
    DWORD size;
    uint32_t hash = HashFile(sav, &size);
    if (!size) return;
    MakeDirs(target);
    // The backups there, and the newest of them.  A junction or link is never one (pruning would follow it).
    wchar_t pattern[MAX_PATH], newest[STAMP_CAP] = L"";
    _snwprintf_s(pattern, _countof(pattern), _TRUNCATE, L"%s\\*", target);
    static wchar_t names[256][STAMP_CAP];
    int n = 0;
    WIN32_FIND_DATAW fd;
    HANDLE find = FindFirstFileW(pattern, &fd);
    if (find != INVALID_HANDLE_VALUE) {
        do {
            if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) || (fd.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) ||
                !IsBackupStamp(fd.cFileName))
                continue;
            if (!newest[0] || StampOrder(fd.cFileName, newest) > 0) wcscpy_s(newest, STAMP_CAP, fd.cFileName);
            if (n < 256) wcscpy_s(names[n++], STAMP_CAP, fd.cFileName);
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
    // A folder of its own: <stamp>, or <stamp>-<n> when a backup of this second exists.
    wchar_t stamp[32], dest[MAX_PATH];
    Stamp(stamp, _countof(stamp));
    BOOL made = FALSE;
    for (int k = 0; k < 100 && !made; k++) {
        if (k) _snwprintf_s(dest, _countof(dest), _TRUNCATE, L"%s\\%s-%d", target, stamp, k);
        else _snwprintf_s(dest, _countof(dest), _TRUNCATE, L"%s\\%s", target, stamp);
        made = CreateDirectoryW(dest, NULL);
        if (!made && GetLastError() != ERROR_ALREADY_EXISTS) return;
    }
    if (!made) return;
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
    // Keep the newest `keep` backups (the one just made among them); only backups are ever removed.
    if (n + 1 > keep) {
        qsort(names, n, sizeof names[0], StampOrder);
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

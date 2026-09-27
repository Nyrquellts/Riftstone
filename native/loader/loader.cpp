// Riftstone loader for Dragon's Dogma: Dark Arisen and Dragon's Dogma Online (32-bit).
//
// Built twice from these sources:
//   dinput8.dll           proxy: exports DirectInput8Create and forwards it to the
//                         system dinput8 (or to a chained DLL named in the ini)
//   riftstone_loader.dll  plain add-on (RIFTSTONE_NO_PROXY) for other loaders,
//                         e.g. DDDA Tweak's "loadLibrary = riftstone_loader.dll"
//
// What it does, all switchable in riftstone_loader.ini:
//   overlay   read-only opens of <game>\nativePC\<path> are served from
//             <game>\riftstone\overlay\<path> when that file exists, so mods
//             never overwrite the game's own archives
//   plugins   *.asi / *.dll from riftstone\plugins, in name order
//   crash     unhandled exceptions, the game's fatal-error boxes and hangs write reports to
//             riftstone\logs (stability.cpp); two start-up crashes in a row start the game
//             without mods and plugins (safe mode), a plugin that crashed start-up twice is
//             skipped until its file changes (quarantine)
//   live      memory headroom, frame times and the loader's counters in shared memory for
//             Riftstone Studio; the memory pressure watch (live.cpp)
//   d3d9      the game's Direct3D 9 from another DLL in the game folder, such as DXVK's d3d9.dll in
//             riftstone\dxvk, and its textures and buffers counted by pool ([d3d9], graphics.cpp)
//   exit      why the game closed: Alt+F4, its close button, another program, Windows ending the
//             session, its own exit menu (session.cpp)
//   panel     the in-game diagnostics panel, F10: enemy slots, address space, plugins, frame rate,
//             stage, drawn at Present ([overlay], overlay.cpp)
//   fixes     missing-texture guard, borderless window, keep running when alt-tabbed, the
//             frame-rate ceiling, save backups (fixes.cpp)
//
// Hooks patch the game executable's import table and the function tables of the Direct3D objects
// it creates; engine code is patched only by the byte-verified fixes for the one build they were
// measured on (fixes.cpp), and by plugins.  Every hook falls through to the original call on any doubt.

#include "runtime.h"
#include <unknwn.h>
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>

// ---------------------------------------------------------------------------
// state

wchar_t g_root[MAX_PATH];
wchar_t g_stateDir[MAX_PATH];
wchar_t g_logDir[MAX_PATH];
wchar_t g_ini[MAX_PATH];
HMODULE g_self;
DWORD g_mainThread;
ULONGLONG g_startTick;
volatile LONG g_redirects = 0;

static wchar_t g_nativePrefix[MAX_PATH];    // <root>\nativePC\  (for prefix match)
static size_t g_nativePrefixLen;
static wchar_t g_overlayRoot[MAX_PATH];     // <root>\riftstone\overlay\  .
static BOOL g_overlay = TRUE, g_logRedirects = TRUE, g_plugins = TRUE;
static BOOL g_active = FALSE;               // FALSE in programs that are not the game (pass-through)
static volatile LONG g_exiting = 0;         // DllMain is handling process exit
static HANDLE g_log = INVALID_HANDLE_VALUE;
static CRITICAL_SECTION g_logLock;
volatile LONG g_missing = 0;
wchar_t g_missingRing[MISSING_RING][MAX_PATH];
volatile LONG g_missingNext = 0;

typedef HANDLE(WINAPI* CreateFileA_t)(LPCSTR, DWORD, DWORD, LPSECURITY_ATTRIBUTES, DWORD, DWORD, HANDLE);
typedef DWORD(WINAPI* GetFileAttributesA_t)(LPCSTR);

CreateFileW_t Real_CreateFileW;
GetFileAttributesW_t Real_GetFileAttributesW;
static CreateFileA_t Real_CreateFileA;
static GetFileAttributesA_t Real_GetFileAttributesA;

wchar_t g_recent[RECENT][MAX_PATH];
volatile LONG g_recentNext = 0;

PluginInfo g_pluginInfo[MAX_PLUGINS];
int g_pluginCount = 0;

// ---------------------------------------------------------------------------
// small helpers

int IniInt(const wchar_t* section, const wchar_t* key, int def) {
    return (int)GetPrivateProfileIntW(section, key, def, g_ini);
}

void IniStr(const wchar_t* section, const wchar_t* key, const wchar_t* def, wchar_t* out, DWORD cap) {
    GetPrivateProfileStringW(section, key, def, out, cap, g_ini);
    // Trailing comments and spaces: "value   ; note" -> "value"
    wchar_t* semi = wcschr(out, L';');
    if (semi) *semi = 0;
    size_t n = wcslen(out);
    while (n && (out[n - 1] == L' ' || out[n - 1] == L'\t')) out[--n] = 0;
}

ULONGLONG UptimeMs() { return GetTickCount64() - g_startTick; }

void Stamp(wchar_t* out, size_t cap) {
    SYSTEMTIME t;
    GetLocalTime(&t);
    _snwprintf_s(out, cap, _TRUNCATE, L"%04u%02u%02u-%02u%02u%02u", t.wYear, t.wMonth, t.wDay, t.wHour, t.wMinute, t.wSecond);
}

// ---------------------------------------------------------------------------
// logging

void LogLine(const wchar_t* fmt, ...) {
    if (g_log == INVALID_HANDLE_VALUE) return;
    wchar_t line[1024];
    SYSTEMTIME t;
    GetLocalTime(&t);
    int n = _snwprintf_s(line, _countof(line), _TRUNCATE, L"%02u:%02u:%02u.%03u  ", t.wHour, t.wMinute, t.wSecond, t.wMilliseconds);
    if (n < 0) n = 0;
    va_list ap;
    va_start(ap, fmt);
    int m = _vsnwprintf_s(line + n, _countof(line) - n, _TRUNCATE, fmt, ap);
    va_end(ap);
    if (m < 0) m = (int)wcslen(line + n);
    n += m;
    if (n < (int)_countof(line) - 2) { line[n++] = L'\r'; line[n++] = L'\n'; }
    char utf8[3072];
    int bytes = WideCharToMultiByte(CP_UTF8, 0, line, n, utf8, sizeof(utf8), NULL, NULL);
    DWORD w;
    if (g_exiting) {
        // At process exit the other threads are gone; one may have died holding the lock.  Never wait.
        BOOL locked = TryEnterCriticalSection(&g_logLock);
        WriteFile(g_log, utf8, bytes, &w, NULL);
        if (locked) LeaveCriticalSection(&g_logLock);
        return;
    }
    EnterCriticalSection(&g_logLock);
    WriteFile(g_log, utf8, bytes, &w, NULL);
    LeaveCriticalSection(&g_logLock);
}

// ---------------------------------------------------------------------------
// overlay

static void RememberOpen(const wchar_t* path) {
    if (!path) return;
    LONG i = InterlockedIncrement(&g_recentNext) - 1;
    wcsncpy_s(g_recent[i % RECENT], MAX_PATH, path, _TRUNCATE);
}

static BOOL ReadOnlyOpen(DWORD access, DWORD disposition) {
    const DWORD writes = GENERIC_WRITE | GENERIC_ALL | FILE_WRITE_DATA | FILE_APPEND_DATA | FILE_WRITE_EA |
                         FILE_WRITE_ATTRIBUTES | DELETE | WRITE_DAC | WRITE_OWNER;
    return (access & writes) == 0 && disposition == OPEN_EXISTING;
}

const wchar_t* NativePrefix() { return g_nativePrefix; }
const wchar_t* OverlayRootIfOn() { return g_overlay ? g_overlayRoot : NULL; }

// The part of <path> after <root>\nativePC\, normalised into full; NULL when the path is elsewhere.
static const wchar_t* NativeRelative(const wchar_t* path, wchar_t* full, DWORD cap) {
    if (!path || !*path) return NULL;
    DWORD n = GetFullPathNameW(path, cap, full, NULL);
    if (n == 0 || n >= cap) return NULL;
    if (n <= g_nativePrefixLen || _wcsnicmp(full, g_nativePrefix, g_nativePrefixLen) != 0) return NULL;
    const wchar_t* rel = full + g_nativePrefixLen;
    if (wcsstr(rel, L"..") || wcschr(rel, L':')) return NULL;   // GetFullPathName normalised; stay defensive
    return rel;
}

// If <path> is under <root>\nativePC\ and the overlay has the same relative file,
// write the overlay path to out and return TRUE.
static BOOL OverlayFor(const wchar_t* path, wchar_t* out, DWORD cap) {
    if (!g_overlay) return FALSE;
    wchar_t full[MAX_PATH * 2];
    const wchar_t* rel = NativeRelative(path, full, _countof(full));
    if (!rel) return FALSE;
    if (_snwprintf_s(out, cap, _TRUNCATE, L"%s%s", g_overlayRoot, rel) < 0) return FALSE;
    DWORD a = Real_GetFileAttributesW(out);
    return a != INVALID_FILE_ATTRIBUTES && !(a & FILE_ATTRIBUTE_DIRECTORY);
}

static void NoteRedirect(const wchar_t* from, const wchar_t* to) {
    LONG k = InterlockedIncrement(&g_redirects);
    if (g_logRedirects && k <= 5000) LogLine(L"overlay  %s  ->  %s", from, to);
}

// A read-only open under nativePC failed because the file is not there: log it (the evidence a
// "Fatal error: Failed open file" report needs), then let the guard offer a stand-in.
static HANDLE MissingUnderNative(const wchar_t* path, DWORD access, DWORD share, LPSECURITY_ATTRIBUTES sa,
                                 DWORD disposition, DWORD flags, HANDLE templ, DWORD err) {
    wchar_t full[MAX_PATH * 2];
    if (!NativeRelative(path, full, _countof(full))) return INVALID_HANDLE_VALUE;
    // The engine looks for optional .pck files and treats a miss as "none" (MtFile::open, DDDA.exe
    // 0x00D0D0C0): not a missing file.
    for (const wchar_t* s = full; *s; ++s)
        if (_wcsnicmp(s, L".pck", 4) == 0) return INVALID_HANDLE_VALUE;
    LONG k = InterlockedIncrement(&g_missing);
    LONG slot = InterlockedIncrement(&g_missingNext) - 1;
    wcsncpy_s(g_missingRing[slot % MISSING_RING], MAX_PATH, full, _TRUNCATE);
    if (k <= 200) LogLine(L"missing  %s (error %lu)", full, err);
    else if (k == 201) LogLine(L"missing  (more missing files are counted, not logged)");
    return GuardOpen(full, access, share, sa, disposition, flags, templ);
}

static HANDLE WINAPI Hook_CreateFileW(LPCWSTR name, DWORD access, DWORD share, LPSECURITY_ATTRIBUTES sa,
                                      DWORD disposition, DWORD flags, HANDLE templ) {
    RememberOpen(name);
    BOOL readOnly = ReadOnlyOpen(access, disposition);
    wchar_t alt[MAX_PATH * 2];
    if (readOnly && OverlayFor(name, alt, _countof(alt))) {
        HANDLE h = Real_CreateFileW(alt, access, share, sa, disposition, flags, templ);
        if (h != INVALID_HANDLE_VALUE) {
            NoteRedirect(name, alt);
            return h;
        }
        LogLine(L"overlay  could not open %s (error %lu); using the original", alt, GetLastError());
    }
    HANDLE h = Real_CreateFileW(name, access, share, sa, disposition, flags, templ);
    if (h == INVALID_HANDLE_VALUE && readOnly) {
        DWORD err = GetLastError();
        if (err == ERROR_FILE_NOT_FOUND || err == ERROR_PATH_NOT_FOUND) {
            HANDLE g = MissingUnderNative(name, access, share, sa, disposition, flags, templ, err);
            if (g != INVALID_HANDLE_VALUE) return g;
        }
        SetLastError(err);
    }
    return h;
}

static BOOL Widen(LPCSTR s, wchar_t* out, int cap) {
    return s && MultiByteToWideChar(CP_ACP, 0, s, -1, out, cap) > 0;
}

static HANDLE WINAPI Hook_CreateFileA(LPCSTR name, DWORD access, DWORD share, LPSECURITY_ATTRIBUTES sa,
                                      DWORD disposition, DWORD flags, HANDLE templ) {
    wchar_t wide[MAX_PATH * 2];
    BOOL haveWide = Widen(name, wide, _countof(wide));
    BOOL readOnly = ReadOnlyOpen(access, disposition);
    if (haveWide) {
        RememberOpen(wide);
        wchar_t alt[MAX_PATH * 2];
        if (readOnly && OverlayFor(wide, alt, _countof(alt))) {
            HANDLE h = Real_CreateFileW(alt, access, share, sa, disposition, flags, templ);
            if (h != INVALID_HANDLE_VALUE) {
                NoteRedirect(wide, alt);
                return h;
            }
        }
    }
    HANDLE h = Real_CreateFileA(name, access, share, sa, disposition, flags, templ);
    if (h == INVALID_HANDLE_VALUE && readOnly && haveWide) {
        DWORD err = GetLastError();
        if (err == ERROR_FILE_NOT_FOUND || err == ERROR_PATH_NOT_FOUND) {
            HANDLE g = MissingUnderNative(wide, access, share, sa, disposition, flags, templ, err);
            if (g != INVALID_HANDLE_VALUE) return g;
        }
        SetLastError(err);
    }
    return h;
}

static DWORD WINAPI Hook_GetFileAttributesW(LPCWSTR name) {
    wchar_t alt[MAX_PATH * 2];
    if (OverlayFor(name, alt, _countof(alt))) return Real_GetFileAttributesW(alt);
    return Real_GetFileAttributesW(name);
}

static DWORD WINAPI Hook_GetFileAttributesA(LPCSTR name) {
    wchar_t wide[MAX_PATH * 2], alt[MAX_PATH * 2];
    if (Widen(name, wide, _countof(wide)) && OverlayFor(wide, alt, _countof(alt))) return Real_GetFileAttributesW(alt);
    return Real_GetFileAttributesA(name);
}

// ---------------------------------------------------------------------------
// import-table patching

// The game's import slot for dll!func (NULL if the game does not import it by name).
static void** ImportSlot(HMODULE mod, const char* dll, const char* func) {
    BYTE* base = (BYTE*)mod;
    IMAGE_DOS_HEADER* dos = (IMAGE_DOS_HEADER*)base;
    if (dos->e_magic != IMAGE_DOS_SIGNATURE) return NULL;
    IMAGE_NT_HEADERS* nt = (IMAGE_NT_HEADERS*)(base + dos->e_lfanew);
    IMAGE_DATA_DIRECTORY dir = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
    if (!dir.VirtualAddress) return NULL;
    for (IMAGE_IMPORT_DESCRIPTOR* imp = (IMAGE_IMPORT_DESCRIPTOR*)(base + dir.VirtualAddress); imp->Name; ++imp) {
        if (_stricmp((const char*)(base + imp->Name), dll) != 0 || !imp->OriginalFirstThunk) continue;
        IMAGE_THUNK_DATA* thunk = (IMAGE_THUNK_DATA*)(base + imp->FirstThunk);
        IMAGE_THUNK_DATA* names = (IMAGE_THUNK_DATA*)(base + imp->OriginalFirstThunk);
        for (; thunk->u1.Function; ++thunk, ++names) {
            if (IMAGE_SNAP_BY_ORDINAL(names->u1.Ordinal)) continue;
            IMAGE_IMPORT_BY_NAME* ibn = (IMAGE_IMPORT_BY_NAME*)(base + names->u1.AddressOfData);
            if (strcmp((const char*)ibn->Name, func) == 0) return (void**)&thunk->u1.Function;
        }
    }
    return NULL;
}

// One import hook: what the slot held when it was hooked (*real) and the export itself, the two values a
// restored table can hold; and the last other value seen in the slot (another module's hook, logged once).
struct HookSpec { const char* dll; const char* name; void* hook; void** real; void* exported; void* foreign; };
#define MAX_HOOKS 32
static HookSpec g_hooks[MAX_HOOKS];
static int g_hookCount = 0;
static CRITICAL_SECTION g_hookLock;

BOOL HookImport(const char* dll, const char* func, void* hook, void** real) {
    HMODULE game = GetModuleHandleW(NULL);
    HMODULE lib = GetModuleHandleA(dll);
    void* exported = lib ? (void*)GetProcAddress(lib, func) : NULL;
    if (!*real) *real = exported;
    void** slot = ImportSlot(game, dll, func);
    BOOL ok = FALSE;
    if (slot) {
        DWORD old;
        EnterCriticalSection(&g_hookLock);
        if (*slot != hook && VirtualProtect(slot, sizeof(void*), PAGE_READWRITE, &old)) {
            *real = *slot;
            *slot = hook;
            VirtualProtect(slot, sizeof(void*), old, &old);
            FlushInstructionCache(GetCurrentProcess(), NULL, 0);
            ok = TRUE;
        }
        if (ok && g_hookCount < MAX_HOOKS) g_hooks[g_hookCount++] = HookSpec{dll, func, hook, real, exported, NULL};
        LeaveCriticalSection(&g_hookLock);
    }
    LogLine(L"hook     %S!%S %s", dll, func, ok ? L"installed" : L"not imported by the game (left alone)");
    return ok;
}

// "name.dll" of the module holding an address, or "?".
static void ModuleNameAt(const void* addr, wchar_t* out, size_t cap) {
    HMODULE mod = NULL;
    wchar_t path[MAX_PATH];
    if (GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                           (LPCWSTR)addr, &mod) && mod && GetModuleFileNameW(mod, path, MAX_PATH)) {
        const wchar_t* base = wcsrchr(path, L'\\');
        wcsncpy_s(out, cap, base ? base + 1 : path, _TRUNCATE);
    } else {
        wcsncpy_s(out, cap, L"?", _TRUNCATE);
    }
}

// If start-up code (e.g. a DRM wrapper) restored the import table after we patched it, patch again.
// Only a slot that holds what it held before we hooked it, or the export itself, was restored.  Any other
// value is another module's hook put over ours (a plugin hooking the game's CreateFileW from its DllMain):
// it was handed ours as the function to call on, so taking it for the original and putting ours back in
// front would make the two call each other until the stack runs out.  It is left in place and logged once.
// Safe to call any time; logs only when it had to act.
static void EnsureHooks(const wchar_t* when) {
    struct Seen { const char* name; void* at; };
    Seen reset[MAX_HOOKS], foreign[MAX_HOOKS];
    int nReset = 0, nForeign = 0;
    HMODULE game = GetModuleHandleW(NULL);
    EnterCriticalSection(&g_hookLock);
    for (int i = 0; i < g_hookCount; i++) {
        HookSpec& h = g_hooks[i];
        void** slot = ImportSlot(game, h.dll, h.name);
        if (!slot || *slot == h.hook) continue;
        void* current = *slot;
        if (current != *h.real && current != h.exported) {
            if (h.foreign != current) {
                h.foreign = current;
                foreign[nForeign++] = Seen{h.name, current};
            }
            continue;
        }
        DWORD old;
        if (VirtualProtect(slot, sizeof(void*), PAGE_READWRITE, &old)) {
            *h.real = current;
            *slot = h.hook;
            VirtualProtect(slot, sizeof(void*), old, &old);
            reset[nReset++] = Seen{h.name, current};
        }
    }
    LeaveCriticalSection(&g_hookLock);
    for (int i = 0; i < nReset; i++) LogLine(L"hook     %S was reset %s; installed again", reset[i].name, when);
    for (int i = 0; i < nForeign; i++) {
        wchar_t owner[MAX_PATH];
        ModuleNameAt(foreign[i].at, owner, _countof(owner));
        LogLine(L"hook     %S goes to another module first now (%s, 0x%p), found %s; left in place: it was handed "
                L"ours to call on", foreign[i].name, owner, foreign[i].at, when);
    }
}

static DWORD WINAPI Watchdog(LPVOID) {
    for (unsigned i = 0;; i++) {
        Sleep(50);
        if (i < 400) EnsureHooks(L"during start-up");      // the first 20 seconds of the process
        if (!SessionWatchTick() && i >= 400) return 0;      // what closes the game (session.cpp)
    }
}

// ---------------------------------------------------------------------------
// plugins

static void DescribeError(DWORD err, wchar_t* out, DWORD cap) {
    out[0] = 0;
    if (!FormatMessageW(FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS, NULL, err, 0, out, cap, NULL))
        _snwprintf_s(out, cap, _TRUNCATE, L"error %lu", err);
    for (wchar_t* p = out; *p; ++p)
        if (*p == L'\r' || *p == L'\n') *p = L' ';
}

// Load native plugins from <game>\riftstone\plugins: *.asi and *.dll.  They hook the game
// themselves from their own DllMain; Riftstone only brings them into the process, in name
// order, and logs each result.  A plugin that fails to load is skipped, not fatal; one that
// crashed the game's start-up twice is skipped until its file changes (stability.cpp).
static void LoadPlugins() {
    if (!g_plugins) {
        LogLine(L"plugins  switched off in riftstone_loader.ini");
        return;
    }
    wchar_t dir[MAX_PATH];
    _snwprintf_s(dir, _countof(dir), _TRUNCATE, L"%s\\plugins", g_stateDir);
    if (Real_GetFileAttributesW(dir) == INVALID_FILE_ATTRIBUTES) {
        LogLine(L"plugins  %s absent (none to load)", dir);
        return;
    }
    // Collect names first so load order is deterministic (FindFirstFile order is not).
    wchar_t pattern[MAX_PATH];
    _snwprintf_s(pattern, _countof(pattern), _TRUNCATE, L"%s\\*", dir);
    static wchar_t names[MAX_PLUGINS][MAX_PATH];
    int n = 0;
    WIN32_FIND_DATAW fd;
    HANDLE find = FindFirstFileW(pattern, &fd);
    if (find != INVALID_HANDLE_VALUE) {
        do {
            if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) continue;
            const wchar_t* dot = wcsrchr(fd.cFileName, L'.');
            if (!dot || (_wcsicmp(dot, L".asi") != 0 && _wcsicmp(dot, L".dll") != 0)) continue;
            if (n < MAX_PLUGINS) wcscpy_s(names[n++], MAX_PATH, fd.cFileName);
        } while (FindNextFileW(find, &fd));
        FindClose(find);
    }
    for (int i = 1; i < n; i++) {  // insertion sort, case-insensitive
        wchar_t key[MAX_PATH];
        wcscpy_s(key, MAX_PATH, names[i]);
        int j = i - 1;
        while (j >= 0 && _wcsicmp(names[j], key) > 0) { wcscpy_s(names[j + 1], MAX_PATH, names[j]); j--; }
        wcscpy_s(names[j + 1], MAX_PATH, key);
    }
    LogLine(L"plugins  %d found in %s", n, dir);
    for (int i = 0; i < n; i++) {
        wchar_t full[MAX_PATH];
        _snwprintf_s(full, _countof(full), _TRUNCATE, L"%s\\%s", dir, names[i]);
        PluginInfo& p = g_pluginInfo[g_pluginCount++];
        wcscpy_s(p.name, MAX_PATH, names[i]);
        if (!PluginAllowed(names[i], full)) {
            p.state = SafeModeActive() ? -2 : -1;
            continue;
        }
        HMODULE m = LoadLibraryW(full);
        if (m) {
            p.module = m;
            p.state = 1;
            IMAGE_NT_HEADERS* nt = (IMAGE_NT_HEADERS*)((BYTE*)m + ((IMAGE_DOS_HEADER*)m)->e_lfanew);
            p.size = nt->OptionalHeader.SizeOfImage;
            LogLine(L"plugin   %s loaded at 0x%p", names[i], m);
        } else {
            wchar_t why[256];
            DWORD err = GetLastError();
            DescribeError(err, why, _countof(why));
            p.state = 0;
            LogLine(L"plugin   %s FAILED to load: %s%s", names[i], why,
                    err == ERROR_MOD_NOT_FOUND ? L" (a DLL it needs is missing, e.g. a Visual C++ runtime)" : L"");
        }
    }
}

// ---------------------------------------------------------------------------
// start-up

// [loader] test_stack_fill = 1, the harness's way in: the stack the next call from Init will use is filled
// with old data first (as earlier calls leave it), so a local that call forgets to set shows up.
static __declspec(noinline) void TestFillStack() {
    volatile wchar_t junk[8192];
    for (int i = 0; i < 8192; i++) junk[i] = (i & 63) == 63 ? 0 : L'Z';
}

static void OpenLog() {
    wchar_t logPath[MAX_PATH], prev[MAX_PATH];
    _snwprintf_s(logPath, _countof(logPath), _TRUNCATE, L"%s\\loader.log", g_logDir);
    _snwprintf_s(prev, _countof(prev), _TRUNCATE, L"%s\\loader.prev.log", g_logDir);
    MoveFileExW(logPath, prev, MOVEFILE_REPLACE_EXISTING);   // keep the last session's log
    g_log = Real_CreateFileW(logPath, GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_DELETE, NULL, CREATE_ALWAYS,
                             FILE_ATTRIBUTE_NORMAL, NULL);
}

static void Init() {
    InitializeCriticalSection(&g_hookLock);
    InitializeCriticalSection(&g_logLock);
    g_startTick = GetTickCount64();
    g_mainThread = GetCurrentThreadId();
    // Stay loaded: the import table points into this DLL for the life of the process.
    HMODULE pinned;
    GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_PIN, (LPCWSTR)&Init, &pinned);

    HMODULE k32 = GetModuleHandleW(L"kernel32.dll");
    // Unhooked originals for the loader's own use; replaced below by what the game's IAT held.
    Real_CreateFileW = (CreateFileW_t)GetProcAddress(k32, "CreateFileW");
    Real_CreateFileA = (CreateFileA_t)GetProcAddress(k32, "CreateFileA");
    Real_GetFileAttributesW = (GetFileAttributesW_t)GetProcAddress(k32, "GetFileAttributesW");
    Real_GetFileAttributesA = (GetFileAttributesA_t)GetProcAddress(k32, "GetFileAttributesA");

    GetModuleFileNameW(NULL, g_root, MAX_PATH);
    wchar_t* slash = wcsrchr(g_root, L'\\');
    if (slash) *slash = 0;
    _snwprintf_s(g_nativePrefix, _countof(g_nativePrefix), _TRUNCATE, L"%s\\nativePC\\", g_root);
    g_nativePrefixLen = wcslen(g_nativePrefix);
    _snwprintf_s(g_stateDir, _countof(g_stateDir), _TRUNCATE, L"%s\\riftstone", g_root);
    _snwprintf_s(g_overlayRoot, _countof(g_overlayRoot), _TRUNCATE, L"%s\\overlay\\", g_stateDir);
    _snwprintf_s(g_logDir, _countof(g_logDir), _TRUNCATE, L"%s\\logs", g_stateDir);
    _snwprintf_s(g_ini, _countof(g_ini), _TRUNCATE, L"%s\\riftstone_loader.ini", g_root);

    g_overlay = IniInt(L"loader", L"overlay", 1) != 0;
    g_logRedirects = IniInt(L"loader", L"log_redirects", 1) != 0;
    g_plugins = IniInt(L"loader", L"plugins", 1) != 0;

    DetectGame();
    if (!RuntimeActive()) {
        // Another program in the game folder that imports dinput8 (DDO's launcher does): pass
        // DirectInput through and touch nothing else, not even the game's log.
        return;
    }
    g_active = TRUE;
    CreateDirectoryW(g_stateDir, NULL);
    CreateDirectoryW(g_logDir, NULL);
    OpenLog();
    LogLine(L"Riftstone loader %s in %s", RIFTSTONE_LOADER_VERSION, g_root);
    LogLine(L"game     %s (%s, PE timestamp 0x%08lx)%s", GameName(), g_exeName, g_exeTimestamp,
            g_knownBuild ? L", the build Riftstone's engine fixes were measured on" : L"");
    MEMORYSTATUSEX ms = {sizeof ms};
    GlobalMemoryStatusEx(&ms);
    if (g_largeAddressAware)
        LogLine(L"memory   %s is large-address aware: %llu MB of address space", g_exeName, ms.ullTotalVirtual >> 20);
    else
        LogLine(L"memory   WARNING: %s is not large-address aware, so it has %llu MB of address space instead of 4096 MB "
                L"and runs out much sooner. Steam's DDDA.exe has the flag; 'Riftstone.cmd laa' checks an exe and writes a "
                L"copy with it set (the game's own file is never changed)", g_exeName, ms.ullTotalVirtual >> 20);

    if (IniInt(L"loader", L"test_stack_fill", 0)) TestFillStack();
    StabilityStart();                          // reads the last session; may start safe mode
    SessionStart();                            // why this one will close
    if (!OverlayAllowed()) g_overlay = FALSE;
    LogLine(L"settings overlay=%d log_redirects=%d plugins=%d", g_overlay, g_logRedirects, g_plugins);

    HookImport("KERNEL32.dll", "CreateFileW", (void*)Hook_CreateFileW, (void**)&Real_CreateFileW);
    HookImport("KERNEL32.dll", "CreateFileA", (void*)Hook_CreateFileA, (void**)&Real_CreateFileA);
    HookImport("KERNEL32.dll", "GetFileAttributesW", (void*)Hook_GetFileAttributesW, (void**)&Real_GetFileAttributesW);
    HookImport("KERNEL32.dll", "GetFileAttributesA", (void*)Hook_GetFileAttributesA, (void**)&Real_GetFileAttributesA);
    StabilityInstallHooks();                   // SetUnhandledExceptionFilter, MessageBoxA
    CrashFilterInstall();
    FixesInstallHooks();                       // window fixes
    LiveInstallHooks();                        // Direct3DCreate9 -> frame timing

    DWORD attrs = Real_GetFileAttributesW(g_overlayRoot);
    LogLine(L"overlay  %s %s", g_overlayRoot,
            !g_overlay ? L"off" : attrs != INVALID_FILE_ATTRIBUTES ? L"present" : L"absent (nothing to serve)");
    BackupSaves();                             // before the game reads its save
    LoadPlugins();
    FixesApplyPatches();                       // after plugins, so a plugin's own checks see the vanilla bytes
    LiveStart();
    HANDLE t = CreateThread(NULL, 0, Watchdog, NULL, 0, NULL);
    if (t) CloseHandle(t);
}

// Riftstone reads an installed loader's version from this tag without loading it (runtime.py).
extern "C" __declspec(dllexport) const char* RiftstoneLoaderVersion() {
    static const char tag[] = "RiftstoneLoaderVersion=" RIFTSTONE_VERSION_A;
    return tag + sizeof("RiftstoneLoaderVersion=") - 1;
}

#ifndef RIFTSTONE_NO_PROXY
typedef HRESULT(WINAPI* DirectInput8Create_t)(HINSTANCE, DWORD, REFIID, LPVOID*, LPUNKNOWN);
extern "C" HRESULT WINAPI DirectInput8Create(HINSTANCE inst, DWORD version, REFIID riid, LPVOID* out, LPUNKNOWN outer);

// The DirectInput8Create a chained dinput8 named in [loader] chain offers, or NULL.  One that is the loader
// itself (chain = dinput8.dll, the proxy's own name) or leads back to it (an export forwarded to
// dinput8.DirectInput8Create, which is this module) is refused: DirectInput8Create would call itself for ever.
static DirectInput8Create_t ChainedDirectInput() {
    wchar_t chain[MAX_PATH] = L"";
    IniStr(L"loader", L"chain", L"", chain, MAX_PATH);
    if (!chain[0]) return NULL;
    wchar_t full[MAX_PATH];
    _snwprintf_s(full, _countof(full), _TRUNCATE, L"%s\\%s", g_root, chain);
    HMODULE lib = LoadLibraryW(full);
    if (!lib) {
        LogLine(L"chain    %s could not be loaded; using the system dinput8", full);
        return NULL;
    }
    DirectInput8Create_t found = (DirectInput8Create_t)GetProcAddress(lib, "DirectInput8Create");
    if (lib == g_self || found == (DirectInput8Create_t)DirectInput8Create) {
        LogLine(L"chain    %s %s the Riftstone loader itself, so it would call itself; using the system dinput8", full,
                lib == g_self ? L"is" : L"leads back to");
        FreeLibrary(lib);                   // the count LoadLibrary added (the loader is pinned either way)
        return NULL;
    }
    LogLine(L"chain    %s %s", full, found ? L"loaded" : L"has no DirectInput8Create; using the system dinput8");
    return found;
}

extern "C" HRESULT WINAPI DirectInput8Create(HINSTANCE inst, DWORD version, REFIID riid, LPVOID* out, LPUNKNOWN outer) {
    static DirectInput8Create_t real = NULL;
    EnsureHooks(L"before input start-up");
    if (!real) {
        // A chained dinput8 (e.g. DDDA Tweak renamed to dinput8_tweak.dll) gets the call first.
        DirectInput8Create_t found = ChainedDirectInput();
        if (!found) {
            wchar_t sys[MAX_PATH];
            GetSystemDirectoryW(sys, MAX_PATH);
            wcscat_s(sys, L"\\dinput8.dll");
            HMODULE lib = LoadLibraryW(sys);
            if (lib) found = (DirectInput8Create_t)GetProcAddress(lib, "DirectInput8Create");
        }
        if (!found) {
            LogLine(L"dinput8  no DirectInput8Create found");
            return E_FAIL;
        }
        real = found;
    }
    return real(inst, version, riid, out, outer);
}
#endif

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID reserved) {
    if (reason == DLL_PROCESS_ATTACH) {
        g_self = module;
        DisableThreadLibraryCalls(module);
        Init();
    } else if (reason == DLL_PROCESS_DETACH && reserved != NULL && g_active) {
        // The process is ending normally (ExitProcess): a clean exit, with a summary that says what closed it.
        g_exiting = 1;
        SessionFinalize();
        LiveStop();
        StabilityCleanExit();
    }
    return TRUE;
}

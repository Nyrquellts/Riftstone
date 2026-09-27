// Riftstone runtime: reports when something goes wrong, and a start-up that heals itself.
//
//   crash-<time>.txt (+ .dmp)  an unhandled exception: the fault, registers, the engine class of the
//                              objects in registers and on the stack, the stack, memory headroom (a
//                              32-bit game that runs out of address space crashes), which plugin or
//                              module the fault is in, the last files the game opened
//   fatal-<time>.txt           the game's own fatal-error box (e.g. "Failed open file"): the message,
//                              the files that were missing, the last files opened; the box itself
//                              gains one line naming the report
//   hang-<time>.txt            no frame for [live] hang_seconds while the game is in front: the main
//                              thread's registers and stack (the game is not touched)
//
// Only the newest [loader] keep_reports of each kind are kept.
//
// Safe mode.  riftstone\runtime-state.ini remembers how each session ended.  A plugin whose code
// was at fault in two start-up crashes (the first two minutes) in a row is skipped until its file
// changes (quarantine).  Two start-up crashes in a row that no plugin explains start the game with
// no plugins and no overlay (vanilla) until the setup changes (a plugin, the overlay or the ini)
// or `Riftstone.cmd loader safe-mode off`.
//
// How the last session ended.  [session] holds this run: started, loader, clean (1 once it exited
// normally), alive_ms, and end / end_detail / end_uptime_ms (what closed it, session.cpp).  At the next
// start that, with the crash note, becomes [last_session] (end: a session.cpp code, or crash,
// fatal-error, not-clean) and one line in loader.log.
#include "runtime.h"
#include <dbghelp.h>
#include <tlhelp32.h>
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>

volatile LONG g_fatals = 0;
static BOOL g_crash = TRUE, g_minidump = TRUE;
static int g_keepReports = 10;

typedef LPTOP_LEVEL_EXCEPTION_FILTER(WINAPI* SetUEF_t)(LPTOP_LEVEL_EXCEPTION_FILTER);
typedef int(WINAPI* MessageBoxA_t)(HWND, LPCSTR, LPCSTR, UINT);
static SetUEF_t Real_SetUnhandledExceptionFilter;
static MessageBoxA_t Real_MessageBoxA;
static LPTOP_LEVEL_EXCEPTION_FILTER g_gameFilter;    // what the game asked for through its import
// Filters other modules set themselves (not through the game's import table), newest first: one set before
// ours, and any set over ours later, which ours takes the front back from.  Each is called after the report.
#define OTHER_FILTERS 4
static LPTOP_LEVEL_EXCEPTION_FILTER g_otherFilters[OTHER_FILTERS];
static volatile LONG g_otherCount = 0;
static DWORD g_inFilter = TLS_OUT_OF_INDEXES;        // this thread is inside CrashFilter (re-entry guard)
typedef VOID(WINAPI* StackLimits_t)(PULONG_PTR, PULONG_PTR);
static StackLimits_t g_stackLimits;                  // GetCurrentThreadStackLimits (Windows 8 and later)

static wchar_t g_stateIni[MAX_PATH];                 // riftstone\runtime-state.ini
static wchar_t g_marker[MAX_PATH];                   // riftstone\logs\last-crash.txt (crash handler)
static BOOL g_safeMode = FALSE;

#define EARLY_MS 120000ULL                           // a crash in the first two minutes is a start-up crash

// ---------------------------------------------------------------------------
// report writing (no heap: a crash may have damaged it)

static void Out(HANDLE f, const wchar_t* fmt, ...) {
    wchar_t buf[1024];
    va_list ap;
    va_start(ap, fmt);
    int n = _vsnwprintf_s(buf, _countof(buf), _TRUNCATE, fmt, ap);
    va_end(ap);
    if (n < 0) n = (int)wcslen(buf);
    char utf8[3072];
    int bytes = WideCharToMultiByte(CP_UTF8, 0, buf, n, utf8, sizeof(utf8), NULL, NULL);
    DWORD w;
    WriteFile(f, utf8, bytes, &w, NULL);
}

// <logs>\<kind>-<stamp>.txt, or <kind>-<stamp>-2.txt, -3 ... when a report of that second exists.
static HANDLE OpenReport(const wchar_t* kind, const wchar_t* stamp, const wchar_t* ext, wchar_t* path) {
    CreateDirectoryW(g_logDir, NULL);
    for (int n = 1; n < 100; n++) {
        if (n == 1) _snwprintf_s(path, MAX_PATH, _TRUNCATE, L"%s\\%s-%s.%s", g_logDir, kind, stamp, ext);
        else _snwprintf_s(path, MAX_PATH, _TRUNCATE, L"%s\\%s-%s-%d.%s", g_logDir, kind, stamp, n, ext);
        HANDLE h = Real_CreateFileW(path, GENERIC_WRITE, FILE_SHARE_READ, NULL, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, NULL);
        if (h != INVALID_HANDLE_VALUE || GetLastError() != ERROR_FILE_EXISTS) return h;
    }
    return INVALID_HANDLE_VALUE;
}

// The minidump that goes with a report: the same name, .dmp.
static HANDLE OpenDump(const wchar_t* report, wchar_t* path) {
    wcscpy_s(path, MAX_PATH, report);
    wchar_t* dot = wcsrchr(path, L'.');
    if (!dot) return INVALID_HANDLE_VALUE;
    wcscpy_s(dot, MAX_PATH - (dot - path), L".dmp");
    return Real_CreateFileW(path, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
}

// Report names in time order: "<kind>-<YYYYMMDD-HHMMSS>[-<n>].txt", a missing n being 1.
static int ReportOrder(const void* a, const void* b) {
    const wchar_t* x = (const wchar_t*)a;
    const wchar_t* y = (const wchar_t*)b;
    const wchar_t* xs = wcschr(x, L'-');
    const wchar_t* ys = wcschr(y, L'-');
    if (!xs || !ys) return _wcsicmp(x, y);
    int c = wcsncmp(xs, ys, 16);                  // "-YYYYMMDD-HHMMSS"
    if (c) return c;
    int xn = xs[16] == L'-' ? _wtoi(xs + 17) : 1, yn = ys[16] == L'-' ? _wtoi(ys + 17) : 1;
    return xn < yn ? -1 : xn > yn;
}

// The plugin an address is in, or -1.
static int PluginAt(DWORD_PTR addr) {
    for (int i = 0; i < g_pluginCount; i++) {
        PluginInfo& p = g_pluginInfo[i];
        if (p.module && addr >= (DWORD_PTR)p.module && addr < (DWORD_PTR)p.module + p.size) return i;
    }
    return -1;
}

// "DDDA.exe+0x1a2b3" for an address inside a loaded module.
static BOOL Where(DWORD_PTR addr, wchar_t* out, size_t cap, wchar_t* modPath = NULL) {
    HMODULE mod = NULL;
    if (GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                           (LPCWSTR)addr, &mod) && mod) {
        wchar_t name[MAX_PATH];
        if (GetModuleFileNameW(mod, name, MAX_PATH)) {
            const wchar_t* base = wcsrchr(name, L'\\');
            _snwprintf_s(out, cap, _TRUNCATE, L"%s+0x%lx", base ? base + 1 : name, (unsigned long)(addr - (DWORD_PTR)mod));
            if (modPath) wcscpy_s(modPath, MAX_PATH, name);
            return TRUE;
        }
    }
    _snwprintf_s(out, cap, _TRUNCATE, L"?");
    if (modPath) modPath[0] = 0;
    return FALSE;
}

static const wchar_t* ExceptionName(DWORD code) {
    switch (code) {
    case EXCEPTION_ACCESS_VIOLATION: return L"ACCESS_VIOLATION";
    case EXCEPTION_STACK_OVERFLOW: return L"STACK_OVERFLOW";
    case EXCEPTION_INT_DIVIDE_BY_ZERO: return L"INT_DIVIDE_BY_ZERO";
    case EXCEPTION_ILLEGAL_INSTRUCTION: return L"ILLEGAL_INSTRUCTION";
    case EXCEPTION_PRIV_INSTRUCTION: return L"PRIV_INSTRUCTION";
    case EXCEPTION_IN_PAGE_ERROR: return L"IN_PAGE_ERROR";
    case EXCEPTION_ARRAY_BOUNDS_EXCEEDED: return L"ARRAY_BOUNDS_EXCEEDED";
    case EXCEPTION_FLT_DIVIDE_BY_ZERO: return L"FLT_DIVIDE_BY_ZERO";
    case EXCEPTION_FLT_INVALID_OPERATION: return L"FLT_INVALID_OPERATION";
    case 0xC0000409: return L"STACK_BUFFER_OVERRUN (fail-fast)";
    case 0xC0000374: return L"HEAP_CORRUPTION";
    case 0xE06D7363: return L"C++ exception";
    default: return L"exception";
    }
}

static void Header(HANDLE f, const wchar_t* title) {
    SYSTEMTIME t;
    GetLocalTime(&t);
    wchar_t exe[MAX_PATH];
    GetModuleFileNameW(NULL, exe, MAX_PATH);
    ULONGLONG up = UptimeMs();
    Out(f, L"%s (Riftstone loader %s)\r\n", title, RIFTSTONE_LOADER_VERSION);
    // Who to send it to: the game ran modded, which Capcom's support does not cover (and this report
    // cannot tell by itself whether a mod or the game is at fault).
    Out(f, L"support     the game ran with mods and plugins through the Riftstone loader (listed below).\r\n"
           L"            Send this report to the mods' authors or to Riftstone, not to Capcom's support:\r\n"
           L"            Capcom does not support modded games. Safe mode, or the game without mods, shows\r\n"
           L"            whether a mod is involved.\r\n");
    Out(f, L"time        %04u-%02u-%02u %02u:%02u:%02u\r\n", t.wYear, t.wMonth, t.wDay, t.wHour, t.wMinute, t.wSecond);
    Out(f, L"program     %s\r\n", exe);
    Out(f, L"game        %s, PE timestamp 0x%08lx%s\r\n", GameName(), g_exeTimestamp,
        g_knownBuild ? L" (the build Riftstone's engine facts are for)" : L"");
    Out(f, L"uptime      %llu.%03llu s%s\r\n", up / 1000, up % 1000, up < EARLY_MS ? L" (start-up)" : L"");
    Out(f, L"safe mode   %s\r\n", g_safeMode ? L"ON (no plugins, no overlay)" : L"off");
    int stage;
    if (CurrentStage(&stage)) Out(f, L"stage       %d\r\n", stage);
}

static void MemorySection(HANDLE f) {
    MemInfo m;
    SampleMemory(&m, TRUE);
    Out(f, L"\r\nmemory\r\n");
    Out(f, L"  address space used   %llu MB of %llu MB\r\n", m.vaUsed >> 20, m.vaTotal >> 20);
    Out(f, L"  largest free block   %llu MB (free in all: %llu MB)\r\n", m.vaLargestFree >> 20, m.vaFree >> 20);
    Out(f, L"  private bytes        %llu MB (peak %llu MB)\r\n", m.privateBytes >> 20, m.peakPrivate >> 20);
    Out(f, L"  working set          %llu MB (peak %llu MB)\r\n", m.workingSet >> 20, m.peakWorkingSet >> 20);
    Out(f, L"  system RAM free      %llu MB of %llu MB\r\n", m.physAvail >> 20, m.physTotal >> 20);
    Out(f, L"  handles              %lu\r\n", m.handles);
    Out(f, L"  large-address aware  %s (%llu MB of address space)\r\n", g_largeAddressAware ? L"yes" : L"NO", m.vaTotal >> 20);
    // Never waits: this thread may be the one holding the counters' lock.
    wchar_t path[MAX_PATH];
    int provider = GraphicsProvider(path, _countof(path), FALSE);
    if (provider != D3D_UNKNOWN) Out(f, L"  Direct3D 9           %s, %s\r\n", GraphicsProviderName(provider), path[0] ? path : L"?");
    PoolStats pools;                                             // by D3DPOOL: 0 default, 1 managed, 2/3 system
    BOOL counted = GraphicsPools(&pools, FALSE);
    if (counted)
        Out(f, L"  Direct3D pools       managed %llu MB (peak %llu MB), default %llu MB, system %llu MB; %lu textures and "
               L"buffers\r\n", pools.bytes[1] >> 20, pools.peak[1] >> 20, pools.bytes[0] >> 20,
            (pools.bytes[2] + pools.bytes[3]) >> 20,
            (unsigned long)(pools.objects[0] + pools.objects[1] + pools.objects[2] + pools.objects[3]));
    if (AddressSpaceLow(&m)) {
        Out(f, L"  VERDICT              the game had nearly run out of address space: a 32-bit game has 4 GB,\r\n"
               L"                       and HD textures and more enemies use it up. This crash is most likely\r\n"
               L"                       an out-of-memory crash. Fewer or smaller texture mods, or a lower\r\n"
               L"                       TextureDetail in config.ini, give it room.\r\n");
        if (counted && provider == D3D_WINDOWS && pools.bytes[1] >= (256ULL << 20))
            Out(f, L"                       Windows' Direct3D 9 held %llu MB of managed textures and buffers, and\r\n"
                   L"                       keeps a copy of those in the game's own address space: DXVK through\r\n"
                   L"                       [d3d9] chain keeps that copy out (docs/runtime.md).\r\n", pools.bytes[1] >> 20);
    }
}

static void PluginsSection(HANDLE f) {
    Out(f, L"\r\nplugins\r\n");
    if (!g_pluginCount) Out(f, L"  (none)\r\n");
    for (int i = 0; i < g_pluginCount; i++) {
        PluginInfo& p = g_pluginInfo[i];
        const wchar_t* st = p.state == 1 ? L"loaded" : p.state == 0 ? L"failed to load" : p.state == -1 ? L"quarantined" : L"skipped (safe mode)";
        if (p.module) Out(f, L"  %-28s %s at 0x%p..0x%p\r\n", p.name, st, p.module, (BYTE*)p.module + p.size);
        else Out(f, L"  %-28s %s\r\n", p.name, st);
    }
}

static void FilesSection(HANDLE f) {
    Out(f, L"\r\nlast files opened (oldest first)\r\n");
    LONG next = g_recentNext;
    for (LONG k = next - RECENT; k < next; k++) {
        if (k < 0) continue;
        const wchar_t* p = g_recent[k % RECENT];
        if (p[0]) Out(f, L"  %s\r\n", p);
    }
    Out(f, L"\r\nfiles the game looked for under nativePC and did not find: %ld\r\n", g_missing);
    LONG mn = g_missingNext;
    for (LONG k = mn - MISSING_RING; k < mn; k++) {
        if (k < 0) continue;
        const wchar_t* p = g_missingRing[k % MISSING_RING];
        if (p[0]) Out(f, L"  %s\r\n", p);
    }
    Out(f, L"\r\noverlay redirects this session: %ld; missing textures given a stand-in: %ld\r\n", g_redirects, g_fallbacks);
}

static void ModulesSection(HANDLE f) {
    Out(f, L"\r\nmodules\r\n");
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPMODULE, GetCurrentProcessId());
    if (snap != INVALID_HANDLE_VALUE) {
        MODULEENTRY32W me = {sizeof(me)};
        for (BOOL ok = Module32FirstW(snap, &me); ok; ok = Module32NextW(snap, &me))
            Out(f, L"  0x%p  0x%08lx  %s\r\n", me.modBaseAddr, me.modBaseSize, me.szExePath);
        CloseHandle(snap);
    }
}

// Engine class names of the objects the registers point at (known builds only).
static void ObjectLine(HANDLE f, const wchar_t* label, DWORD_PTR v) {
    char cls[128];
    if (ClassNameOf((const void*)v, cls, sizeof cls)) Out(f, L"  %-5s 0x%p  -> %S object\r\n", label, (void*)v, cls);
}

static void StackWords(HANDLE f, DWORD_PTR sp, int count, BOOL classes) {
    wchar_t where[MAX_PATH];
    for (int i = 0; i < count; i++) {
        DWORD_PTR* slot = (DWORD_PTR*)sp + i;
        MEMORY_BASIC_INFORMATION mbi;
        if (!VirtualQuery(slot, &mbi, sizeof(mbi)) || mbi.State != MEM_COMMIT || (mbi.Protect & (PAGE_NOACCESS | PAGE_GUARD))) break;
        DWORD_PTR v = *slot;
        HMODULE m = NULL;
        if (GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT, (LPCWSTR)v, &m) && m) {
            Where(v, where, _countof(where));
            int p = PluginAt(v);
            Out(f, L"  [esp+0x%03x] 0x%p  %s%s%s\r\n", i * (int)sizeof(DWORD_PTR), (void*)v, where,
                p >= 0 ? L"  <- plugin " : L"", p >= 0 ? g_pluginInfo[p].name : L"");
        } else if (classes) {
            char cls[128];
            if (ClassNameOf((const void*)v, cls, sizeof cls))
                Out(f, L"  [esp+0x%03x] 0x%p  %S object\r\n", i * (int)sizeof(DWORD_PTR), (void*)v, cls);
        }
    }
}

// What last-crash.txt says for the next start: how long the game ran and whose code was at fault.
static volatile LONG g_markerFatal = 0;   // the note is for a fatal error (the game exits right after)

static void WriteMarker(const wchar_t* kind, const wchar_t* modulePath, const wchar_t* report) {
    InterlockedExchange(&g_markerFatal, wcscmp(kind, L"fatal") == 0);
    HANDLE m = Real_CreateFileW(g_marker, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (m == INVALID_HANDLE_VALUE) return;
    Out(m, L"kind=%s\r\nuptime_ms=%llu\r\nmodule=%s\r\nreport=%s\r\n", kind, UptimeMs(), modulePath ? modulePath : L"", report);
    CloseHandle(m);
}

typedef BOOL(WINAPI* MiniDumpWriteDump_t)(HANDLE, DWORD, HANDLE, MINIDUMP_TYPE, PMINIDUMP_EXCEPTION_INFORMATION,
                                          PMINIDUMP_USER_STREAM_INFORMATION, PMINIDUMP_CALLBACK_INFORMATION);
typedef BOOL(WINAPI* StackWalk64_t)(DWORD, HANDLE, HANDLE, LPSTACKFRAME64, PVOID, PREAD_PROCESS_MEMORY_ROUTINE64,
                                    PFUNCTION_TABLE_ACCESS_ROUTINE64, PGET_MODULE_BASE_ROUTINE64, PTRANSLATE_ADDRESS_ROUTINE64);
typedef BOOL(WINAPI* SymInitialize_t)(HANDLE, PCSTR, BOOL);

// The report of the exception `ep`, raised on thread `thread` (this one, or one that waits for this).
static void WriteCrashReport(EXCEPTION_POINTERS* ep, DWORD thread) {
    wchar_t stamp[32], path[MAX_PATH], where[MAX_PATH], faultModule[MAX_PATH];
    Stamp(stamp, _countof(stamp));
    HANDLE f = OpenReport(L"crash", stamp, L"txt", path);
    EXCEPTION_RECORD* er = ep->ExceptionRecord;
    CONTEXT* c = ep->ContextRecord;
    Where((DWORD_PTR)er->ExceptionAddress, where, _countof(where), faultModule);
    int plugin = PluginAt((DWORD_PTR)er->ExceptionAddress);
    WriteMarker(L"crash", faultModule, path);
    if (f == INVALID_HANDLE_VALUE) return;
    Header(f, L"Riftstone crash report");
    Out(f, L"\r\nexception   0x%08lx %s at 0x%p (%s)\r\n", er->ExceptionCode, ExceptionName(er->ExceptionCode), er->ExceptionAddress, where);
    if (er->ExceptionCode == EXCEPTION_ACCESS_VIOLATION && er->NumberParameters >= 2) {
        const wchar_t* op = er->ExceptionInformation[0] == 0 ? L"reading" : er->ExceptionInformation[0] == 1 ? L"writing" : L"executing";
        Out(f, L"            %s address 0x%p\r\n", op, (void*)er->ExceptionInformation[1]);
    }
    if (plugin >= 0) Out(f, L"fault in    plugin %s (riftstone\\plugins)\r\n", g_pluginInfo[plugin].name);
    else if (faultModule[0] && _wcsicmp(wcsrchr(faultModule, L'\\') ? wcsrchr(faultModule, L'\\') + 1 : faultModule, L"dinput8.dll") != 0)
        Out(f, L"fault in    %s\r\n", faultModule);
    else if (faultModule[0]) Out(f, L"fault in    %s (the Riftstone loader, or another dinput8.dll)\r\n", faultModule);
#ifdef _M_IX86
    Out(f, L"\r\nregisters   EAX=%08lx EBX=%08lx ECX=%08lx EDX=%08lx\r\n", c->Eax, c->Ebx, c->Ecx, c->Edx);
    Out(f, L"            ESI=%08lx EDI=%08lx EBP=%08lx ESP=%08lx\r\n", c->Esi, c->Edi, c->Ebp, c->Esp);
    Out(f, L"            EIP=%08lx EFLAGS=%08lx\r\n", c->Eip, c->EFlags);
    Out(f, L"\r\nobjects in registers (engine classes)\r\n");
    ObjectLine(f, L"ECX", c->Ecx); ObjectLine(f, L"ESI", c->Esi); ObjectLine(f, L"EDI", c->Edi);
    ObjectLine(f, L"EBX", c->Ebx); ObjectLine(f, L"EAX", c->Eax); ObjectLine(f, L"EDX", c->Edx); ObjectLine(f, L"EBP", c->Ebp);
    DWORD machine = IMAGE_FILE_MACHINE_I386;
    STACKFRAME64 sf = {};
    sf.AddrPC.Offset = c->Eip;
    sf.AddrFrame.Offset = c->Ebp;
    sf.AddrStack.Offset = c->Esp;
    DWORD_PTR sp = c->Esp;
#else
    Out(f, L"\r\nregisters   RAX=%016llx RBX=%016llx RCX=%016llx RDX=%016llx\r\n", c->Rax, c->Rbx, c->Rcx, c->Rdx);
    Out(f, L"            RSP=%016llx RBP=%016llx RIP=%016llx\r\n", c->Rsp, c->Rbp, c->Rip);
    DWORD machine = IMAGE_FILE_MACHINE_AMD64;
    STACKFRAME64 sf = {};
    sf.AddrPC.Offset = c->Rip;
    sf.AddrFrame.Offset = c->Rbp;
    sf.AddrStack.Offset = c->Rsp;
    DWORD_PTR sp = (DWORD_PTR)c->Rsp;
#endif
    sf.AddrPC.Mode = sf.AddrFrame.Mode = sf.AddrStack.Mode = AddrModeFlat;
    HMODULE dbg = LoadLibraryW(L"dbghelp.dll");
    StackWalk64_t walk = dbg ? (StackWalk64_t)GetProcAddress(dbg, "StackWalk64") : NULL;
    SymInitialize_t symInit = dbg ? (SymInitialize_t)GetProcAddress(dbg, "SymInitialize") : NULL;
    Out(f, L"\r\nstack\r\n");
    BOOL own = thread == GetCurrentThreadId();
    HANDLE faulting = own ? GetCurrentThread() : OpenThread(THREAD_GET_CONTEXT | THREAD_QUERY_INFORMATION, FALSE, thread);
    if (walk && symInit) {
        symInit(GetCurrentProcess(), NULL, TRUE);
        CONTEXT copy = *c;
        auto access = (PFUNCTION_TABLE_ACCESS_ROUTINE64)GetProcAddress(dbg, "SymFunctionTableAccess64");
        auto base = (PGET_MODULE_BASE_ROUTINE64)GetProcAddress(dbg, "SymGetModuleBase64");
        for (int i = 0; i < 48; i++) {
            if (!walk(machine, GetCurrentProcess(), faulting ? faulting : GetCurrentThread(), &sf, &copy, NULL, access,
                      base, NULL)) break;
            if (!sf.AddrPC.Offset) break;
            Where((DWORD_PTR)sf.AddrPC.Offset, where, _countof(where));
            int p = PluginAt((DWORD_PTR)sf.AddrPC.Offset);
            Out(f, L"  #%02d 0x%08llx  %s%s%s\r\n", i, sf.AddrPC.Offset, where, p >= 0 ? L"  <- plugin " : L"",
                p >= 0 ? g_pluginInfo[p].name : L"");
        }
    }
    if (faulting && !own) CloseHandle(faulting);
    Out(f, L"\r\nstack words that point into modules or at engine objects (from ESP)\r\n");
    StackWords(f, sp, 160, TRUE);
    MemorySection(f);
    PluginsSection(f);
    FilesSection(f);
    ModulesSection(f);
    CloseHandle(f);
    LogLine(L"crash    report written to %s", path);
    if (g_minidump && dbg) {
        auto dump = (MiniDumpWriteDump_t)GetProcAddress(dbg, "MiniDumpWriteDump");
        wchar_t report[MAX_PATH];
        wcscpy_s(report, MAX_PATH, path);
        HANDLE d = OpenDump(report, path);
        if (dump && d != INVALID_HANDLE_VALUE) {
            MINIDUMP_EXCEPTION_INFORMATION mei = {thread, ep, FALSE};
            dump(GetCurrentProcess(), GetCurrentProcessId(), d,
                 (MINIDUMP_TYPE)(MiniDumpWithIndirectlyReferencedMemory | MiniDumpScanMemory | MiniDumpWithThreadInfo),
                 &mei, NULL, NULL);
        }
        if (d != INVALID_HANDLE_VALUE) CloseHandle(d);
    }
}

// Exceptions that are not crashes: debugger traffic, and the probes anti-tamper wrappers raise on
// purpose and handle in their own filter (DDO.exe is Themida-packed; its community patch learned to
// ignore these).  They pass straight to the next filter without a report.
static BOOL NotACrash(const EXCEPTION_RECORD* er) {
    switch (er->ExceptionCode) {
    case EXCEPTION_BREAKPOINT:
    case EXCEPTION_SINGLE_STEP:
    case 0x40010006:          // DBG_PRINTEXCEPTION_C (OutputDebugString)
    case 0x4001000A:          // DBG_PRINTEXCEPTION_WIDE_C
    case 0x406D1388:          // the thread-naming exception
        return TRUE;
    case EXCEPTION_ACCESS_VIOLATION:
        return er->NumberParameters >= 2 && er->ExceptionInformation[0] == 0 && er->ExceptionInformation[1] == 0xFFFFFFFF;
    default:
        return FALSE;
    }
}

// ---- a crash that left little stack -------------------------------------------------------------------
//
// The report needs tens of KB of stack (Out alone 5 KB; the stack walk and the minidump more).  After a
// stack overflow the crashing thread has a few KB left, and writing the report there faults again and ends
// the process with nothing written.  So when that thread is short of stack, the note for the next start is
// written first with static buffers, and the report on a helper thread with a whole stack of its own while
// the crashing thread waits.

#define LOW_STACK (64 * 1024)                        // less than this left: the report goes to a helper thread
#define HELPER_WAIT_MS 30000                         // the longest the crashing thread waits for it

static SIZE_T StackLeft() {
    ULONG_PTR low = 0, high = 0;
    if (!g_stackLimits) return (SIZE_T)-1;           // Windows 7: only a stack overflow counts as low
    g_stackLimits(&low, &high);
    ULONG_PTR here = (ULONG_PTR)&low;
    return here > low ? (SIZE_T)(here - low) : 0;
}

// The crashing thread holds the loader lock (a crash inside a DllMain, e.g. a plugin's while the loader loads
// it): a new thread cannot start until that lock is free, so no helper can write the report.
static BOOL HoldsLoaderLock() {
#ifdef _M_IX86
    BYTE* peb = (BYTE*)__readfsdword(0x30);
    RTL_CRITICAL_SECTION* lock = *(RTL_CRITICAL_SECTION**)(peb + 0xA0);          // PEB.LoaderLock
#else
    BYTE* peb = (BYTE*)__readgsqword(0x60);
    RTL_CRITICAL_SECTION* lock = *(RTL_CRITICAL_SECTION**)(peb + 0x110);
#endif
    return lock && (DWORD)(DWORD_PTR)lock->OwningThread == GetCurrentThreadId();
}

// The crash note with a few hundred bytes of stack: static buffers, the number formatted by hand.  The report
// (when a helper writes it) replaces it with the full note.
static volatile LONG g_liteBusy = 0;
static wchar_t g_liteModule[MAX_PATH];
static char g_liteNote[128 + 3 * MAX_PATH];

static void WriteMarkerLite(const void* faultAddress) {
    if (InterlockedExchange(&g_liteBusy, 1)) return;             // another thread is writing one right now
    HMODULE mod = NULL;
    g_liteModule[0] = 0;
    if (GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                           (LPCWSTR)faultAddress, &mod) && mod)
        if (!GetModuleFileNameW(mod, g_liteModule, MAX_PATH)) g_liteModule[0] = 0;
    char* p = g_liteNote;
    const char* part = "kind=crash\r\nuptime_ms=";
    while (*part) *p++ = *part++;
    ULONGLONG up = UptimeMs();
    char digits[24];
    int nd = 0;
    do { digits[nd++] = (char)('0' + up % 10); up /= 10; } while (up && nd < 24);
    while (nd) *p++ = digits[--nd];
    part = "\r\nmodule=";
    while (*part) *p++ = *part++;
    int room = (int)(g_liteNote + sizeof g_liteNote - p) - 16;
    int bytes = g_liteModule[0] ? WideCharToMultiByte(CP_UTF8, 0, g_liteModule, -1, p, room, NULL, NULL) : 0;
    p += bytes > 0 ? bytes - 1 : 0;                              // without its terminating zero
    part = "\r\nreport=\r\n";
    while (*part) *p++ = *part++;
    InterlockedExchange(&g_markerFatal, 0);
    HANDLE m = Real_CreateFileW(g_marker, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (m != INVALID_HANDLE_VALUE) {
        DWORD w;
        WriteFile(m, g_liteNote, (DWORD)(p - g_liteNote), &w, NULL);
        CloseHandle(m);
    }
    InterlockedExchange(&g_liteBusy, 0);
}

struct CrashJob {
    EXCEPTION_POINTERS* ep;
    DWORD thread;
};

static DWORD WINAPI CrashReportThread(LPVOID arg) {
    CrashJob* job = (CrashJob*)arg;
    WriteCrashReport(job->ep, job->thread);
    return 0;
}

// Kept out of CrashFilter so that the filter itself needs next to no stack.
static __declspec(noinline) void ReportCrash(EXCEPTION_POINTERS* ep) {
    if (ep->ExceptionRecord->ExceptionCode != EXCEPTION_STACK_OVERFLOW && StackLeft() >= LOW_STACK) {
        WriteCrashReport(ep, GetCurrentThreadId());
        return;
    }
    WriteMarkerLite(ep->ExceptionRecord->ExceptionAddress);      // safe mode and quarantine need this much
    if (HoldsLoaderLock()) return;
    CrashJob job = {ep, GetCurrentThreadId()};
    HANDLE t = CreateThread(NULL, 1024 * 1024, CrashReportThread, &job, STACK_SIZE_PARAM_IS_A_RESERVATION, NULL);
    if (!t) return;
    WaitForSingleObject(t, HELPER_WAIT_MS);
    CloseHandle(t);
}

LONG WINAPI CrashFilter(EXCEPTION_POINTERS* ep) {
    // Re-entered on this thread: a filter this one called passed the crash back (it had ours as the filter
    // before it), or writing the report faulted.  Let the caller go on; one crash is one report.
    BOOL guard = g_inFilter != TLS_OUT_OF_INDEXES;
    if (guard && TlsGetValue(g_inFilter)) return EXCEPTION_CONTINUE_SEARCH;
    if (guard) TlsSetValue(g_inFilter, (LPVOID)1);
    // Up to three reports a session: a filter further down may recover (continue execution), and a
    // report must not use up the one a real crash later needs.
    static volatile LONG reports = 0;
    if (g_crash && !NotACrash(ep->ExceptionRecord) && InterlockedIncrement(&reports) <= 3) ReportCrash(ep);
    LONG r = EXCEPTION_CONTINUE_SEARCH;
    if (g_gameFilter && g_gameFilter != CrashFilter) r = g_gameFilter(ep);
    for (LONG i = g_otherCount - 1; r == EXCEPTION_CONTINUE_SEARCH && i >= 0; i--)    // newest first
        if (i < OTHER_FILTERS && g_otherFilters[i] && g_otherFilters[i] != CrashFilter) r = g_otherFilters[i](ep);
    if (guard) TlsSetValue(g_inFilter, NULL);
    return r;
}

// The game installs its own filter; keep ours first and call the game's after reporting.  With crash reports
// off ([loader] crash_reports = 0) the hook is not installed; this passes the call on, should it be reached.
static LPTOP_LEVEL_EXCEPTION_FILTER WINAPI Hook_SetUnhandledExceptionFilter(LPTOP_LEVEL_EXCEPTION_FILTER next) {
    if (!g_crash) return Real_SetUnhandledExceptionFilter ? Real_SetUnhandledExceptionFilter(next) : NULL;
    LPTOP_LEVEL_EXCEPTION_FILTER prev = g_gameFilter;
    g_gameFilter = next;
    return prev;
}

// Ours first.  Called at start-up and every few seconds by the live thread: a module that sets its
// own filter without going through the game's import table (an overlay, a DRM stub) is kept and
// called after the report.  One that sets it over ours got ours as the filter before it and may call
// it in turn; the re-entry guard in CrashFilter answers that call at once, so the two do not call each
// other until the stack runs out.
void CrashFilterInstall() {
    if (!g_crash || !Real_SetUnhandledExceptionFilter) return;
    static volatile LONG installs = 0;
    BOOL first = InterlockedIncrement(&installs) == 1;           // at start-up; later, the live thread's rounds
    LPTOP_LEVEL_EXCEPTION_FILTER prev = Real_SetUnhandledExceptionFilter(CrashFilter);
    if (!prev || prev == CrashFilter || prev == g_gameFilter) return;
    LONG n = g_otherCount;
    for (LONG i = 0; i < n && i < OTHER_FILTERS; i++)
        if (g_otherFilters[i] == prev) return;
    if (n >= OTHER_FILTERS) return;
    g_otherFilters[n] = prev;                                    // written before it is counted
    InterlockedExchange(&g_otherCount, n + 1);
    if (first) LogLine(L"crash    another module had set a crash filter (0x%p); ours reports first, then calls it", prev);
    else LogLine(L"crash    another module set a crash filter over ours (0x%p); ours is first again, reports, then "
                 L"calls it (and answers at once when it passes the crash back)", prev);
}

// ---------------------------------------------------------------------------
// the game's fatal-error box

static BOOL ContainsI(const char* s, const char* needle) {
    if (!s) return FALSE;
    size_t n = strlen(needle);
    for (; *s; ++s)
        if (_strnicmp(s, needle, n) == 0) return TRUE;
    return FALSE;
}

// "Fatal error: Failed open file <path>" -> <path> (up to the end of the line).
static BOOL MissingFromMessage(const char* text, char* out, size_t cap) {
    const char* p = NULL;
    for (const char* s = text; s && *s; ++s)
        if (_strnicmp(s, "open file", 9) == 0) { p = s + 9; break; }
    if (!p) return FALSE;
    // The game writes "Failed open file. <path> <error>".
    while (*p == ' ' || *p == ':' || *p == '.' || *p == '\t' || *p == '"' || *p == '[') ++p;
    size_t n = 0;
    while (p[n] && p[n] != '\r' && p[n] != '\n' && p[n] != '"' && p[n] != ']' && n + 1 < cap) { out[n] = p[n]; n++; }
    out[n] = 0;
    while (n && out[n - 1] == ' ') out[--n] = 0;
    size_t d = n;
    while (d && out[d - 1] >= '0' && out[d - 1] <= '9') d--;
    if (d < n && d > 1 && out[d - 1] == ' ') {                  // a trailing number: the error code
        n = d - 1;
        out[n] = 0;
    }
    return n > 0;
}

static void WriteFatalReport(const char* text, const char* caption, wchar_t* path) {
    wchar_t stamp[32];
    Stamp(stamp, _countof(stamp));
    HANDLE f = OpenReport(L"fatal", stamp, L"txt", path);
    WriteMarker(L"fatal", L"", path);
    if (f == INVALID_HANDLE_VALUE) { path[0] = 0; return; }
    Header(f, L"Riftstone fatal-error report");
    Out(f, L"\r\nthe game stopped with this message\r\n  caption: %S\r\n", caption ? caption : "");
    Out(f, L"  message: %S\r\n", text ? text : "");
    char missing[MAX_PATH];
    if (MissingFromMessage(text, missing, sizeof missing)) {
        Out(f, L"\r\nwhat it means\r\n");
        Out(f, L"  The game needed the file %S and could not open it. A material or model that a mod\r\n", missing);
        Out(f, L"  changed or added names a resource the game has not loaded, so it looked for a loose file\r\n");
        Out(f, L"  under nativePC. Usually the mod is missing the file, or its archive lists a material\r\n");
        Out(f, L"  before the textures it uses (Riftstone builds put textures first). `Riftstone.cmd crash`\r\n");
        Out(f, L"  names the mods that touch it. [guard] missing_textures = 1 lets the game use a neutral\r\n");
        Out(f, L"  stand-in for a missing texture instead of stopping.\r\n");
    }
    MemorySection(f);
    PluginsSection(f);
    FilesSection(f);
    CloseHandle(f);
    LogLine(L"fatal    the game showed \"%S\"; report written to %s", text ? text : "", path);
}

static BOOL LooksFatal(const char* text, const char* caption, UINT type) {
    return ContainsI(text, "fatal") || ContainsI(caption, "fatal") || ContainsI(text, "failed open") ||
           ((type & MB_ICONMASK) == MB_ICONHAND && (ContainsI(text, "error") || ContainsI(caption, "error")));
}

static int WINAPI Hook_MessageBoxA(HWND owner, LPCSTR text, LPCSTR caption, UINT type) {
    if (!LooksFatal(text, caption, type)) return Real_MessageBoxA(owner, text, caption, type);
    InterlockedIncrement(&g_fatals);
    wchar_t path[MAX_PATH];
    WriteFatalReport(text, caption, path);
    LiveNote("notes", L"the game showed a fatal-error message");
    if (!IniInt(L"loader", L"fatal_dialog", 1)) return IDOK;     // a front end is watching the log instead
    if (!path[0] || !IniInt(L"loader", L"fatal_hint", 1)) return Real_MessageBoxA(owner, text, caption, type);
    const wchar_t* name = wcsrchr(path, L'\\');
    char buf[3072];
    _snprintf_s(buf, sizeof buf, _TRUNCATE, "%s\n\n[Riftstone] What happened and which files were missing: riftstone\\logs\\%S"
                "\n[Riftstone] The game runs with mods: send that report to the mods' authors or Riftstone, not to "
                "Capcom's support.", text ? text : "", name ? name + 1 : path);
    return Real_MessageBoxA(owner, buf, caption, type);
}

// ---------------------------------------------------------------------------
// hangs

void WriteHangReport(DWORD thread, DWORD seconds) {
    wchar_t stamp[32], path[MAX_PATH], where[MAX_PATH];
    Stamp(stamp, _countof(stamp));
    // Open the file before the main thread is suspended: while it is stopped it may hold the heap
    // lock, and CreateFile allocates.  Only register and stack reads happen while it is stopped.
    HANDLE f = OpenReport(L"hang", stamp, L"txt", path);
    if (f == INVALID_HANDLE_VALUE) return;
    CONTEXT c = {};
    c.ContextFlags = CONTEXT_CONTROL | CONTEXT_INTEGER;
    static DWORD_PTR words[256];
    int nwords = 0;
    BOOL got = FALSE;
    HANDLE h = OpenThread(THREAD_SUSPEND_RESUME | THREAD_GET_CONTEXT | THREAD_QUERY_INFORMATION, FALSE, thread);
    if (h) {
        if (SuspendThread(h) != (DWORD)-1) {
            got = GetThreadContext(h, &c);
#ifdef _M_IX86
            DWORD_PTR sp = c.Esp;
#else
            DWORD_PTR sp = (DWORD_PTR)c.Rsp;
#endif
            for (; got && nwords < 256; nwords++) {
                MEMORY_BASIC_INFORMATION mbi;
                DWORD_PTR* slot = (DWORD_PTR*)sp + nwords;
                if (!VirtualQuery(slot, &mbi, sizeof(mbi)) || mbi.State != MEM_COMMIT || (mbi.Protect & (PAGE_NOACCESS | PAGE_GUARD))) break;
                words[nwords] = *slot;
            }
            ResumeThread(h);
        }
        CloseHandle(h);
    }
    Header(f, L"Riftstone hang report");
    Out(f, L"\r\nthe game drew no frame for %lu s while it was the window in front; this report does not\r\n", seconds);
    Out(f, L"stop or change the game. If it recovers, it was a long load; if not, this is where its main\r\n");
    Out(f, L"thread was waiting.\r\n");
    if (got) {
#ifdef _M_IX86
        Where(c.Eip, where, _countof(where));
        Out(f, L"\r\nmain thread at 0x%08lx (%s)\r\n", c.Eip, where);
        Out(f, L"registers   EAX=%08lx EBX=%08lx ECX=%08lx EDX=%08lx\r\n", c.Eax, c.Ebx, c.Ecx, c.Edx);
        Out(f, L"            ESI=%08lx EDI=%08lx EBP=%08lx ESP=%08lx\r\n", c.Esi, c.Edi, c.Ebp, c.Esp);
        Out(f, L"\r\nobjects in registers (engine classes)\r\n");
        ObjectLine(f, L"ECX", c.Ecx); ObjectLine(f, L"ESI", c.Esi); ObjectLine(f, L"EDI", c.Edi); ObjectLine(f, L"EBX", c.Ebx);
#endif
        Out(f, L"\r\nstack words that point into modules or at engine objects\r\n");
        for (int i = 0; i < nwords; i++) {
            DWORD_PTR v = words[i];
            HMODULE m = NULL;
            if (GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT, (LPCWSTR)v, &m) && m) {
                Where(v, where, _countof(where));
                Out(f, L"  [esp+0x%03x] 0x%p  %s\r\n", i * (int)sizeof(DWORD_PTR), (void*)v, where);
            } else {
                char cls[128];
                if (ClassNameOf((const void*)v, cls, sizeof cls))
                    Out(f, L"  [esp+0x%03x] 0x%p  %S object\r\n", i * (int)sizeof(DWORD_PTR), (void*)v, cls);
            }
        }
    } else {
        Out(f, L"\r\nthe main thread's registers could not be read\r\n");
    }
    MemorySection(f);
    PluginsSection(f);
    FilesSection(f);
    CloseHandle(f);
    LogLine(L"hang     no frame for %lu s; report written to %s", seconds, path);
}

// ---------------------------------------------------------------------------
// report rotation

static void Rotate(const wchar_t* kind) {
    wchar_t pattern[MAX_PATH];
    _snwprintf_s(pattern, _countof(pattern), _TRUNCATE, L"%s\\%s-*.txt", g_logDir, kind);
    static wchar_t names[512][64];
    int n = 0;
    WIN32_FIND_DATAW fd;
    HANDLE find = FindFirstFileW(pattern, &fd);
    if (find == INVALID_HANDLE_VALUE) return;
    do {
        if (n < 512 && wcslen(fd.cFileName) < 64) wcscpy_s(names[n++], 64, fd.cFileName);
    } while (FindNextFileW(find, &fd));
    FindClose(find);
    if (n <= g_keepReports) return;
    // Names carry a sortable time stamp: the oldest sort first.
    qsort(names, n, sizeof names[0], ReportOrder);
    int drop = n - g_keepReports;
    for (int i = 0; i < drop; i++) {
        wchar_t p[MAX_PATH];
        _snwprintf_s(p, _countof(p), _TRUNCATE, L"%s\\%s", g_logDir, names[i]);
        DeleteFileW(p);
        wchar_t* dot = wcsrchr(p, L'.');
        if (dot && wcscmp(dot, L".txt") == 0) {
            wcscpy_s(dot, 5, L".dmp");
            DeleteFileW(p);
        }
    }
    LogLine(L"reports  kept the newest %d %s reports, removed %d older", g_keepReports, kind, drop);
}

// ---------------------------------------------------------------------------
// safe mode and quarantine

static BOOL FileIdentity(const wchar_t* path, wchar_t* out, size_t cap) {
    WIN32_FILE_ATTRIBUTE_DATA a;
    if (!GetFileAttributesExW(path, GetFileExInfoStandard, &a)) return FALSE;
    _snwprintf_s(out, cap, _TRUNCATE, L"%lu-%08lx%08lx", a.nFileSizeLow, a.ftLastWriteTime.dwHighDateTime, a.ftLastWriteTime.dwLowDateTime);
    return TRUE;
}

static void Fnv(uint32_t& h, const void* data, size_t n) {
    const BYTE* p = (const BYTE*)data;
    for (size_t i = 0; i < n; i++) { h ^= p[i]; h *= 16777619u; }
}

static void FingerprintDir(uint32_t& h, const wchar_t* dir, int depth) {
    if (depth > 12) return;
    wchar_t pattern[MAX_PATH];
    _snwprintf_s(pattern, _countof(pattern), _TRUNCATE, L"%s\\*", dir);
    WIN32_FIND_DATAW fd;
    HANDLE find = FindFirstFileW(pattern, &fd);
    if (find == INVALID_HANDLE_VALUE) return;
    do {
        if (wcscmp(fd.cFileName, L".") == 0 || wcscmp(fd.cFileName, L"..") == 0) continue;
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
            wchar_t sub[MAX_PATH];
            _snwprintf_s(sub, _countof(sub), _TRUNCATE, L"%s\\%s", dir, fd.cFileName);
            FingerprintDir(h, sub, depth + 1);
            continue;
        }
        // Order-independent: XOR each file's own hash in.
        uint32_t one = 2166136261u;
        Fnv(one, fd.cFileName, wcslen(fd.cFileName) * sizeof(wchar_t));
        Fnv(one, &fd.nFileSizeLow, sizeof fd.nFileSizeLow);
        Fnv(one, &fd.ftLastWriteTime, sizeof fd.ftLastWriteTime);
        h ^= one;
    } while (FindNextFileW(find, &fd));
    FindClose(find);
}

// What the mods and plugins look like now; safe mode ends when this changes.  The DLL [d3d9] chain names
// counts too (a new DXVK is a change), and so does the ini itself.
static uint32_t SetupFingerprint() {
    uint32_t h = 0;
    wchar_t dir[MAX_PATH];
    _snwprintf_s(dir, _countof(dir), _TRUNCATE, L"%s\\plugins", g_stateDir);
    FingerprintDir(h, dir, 0);
    _snwprintf_s(dir, _countof(dir), _TRUNCATE, L"%s\\overlay", g_stateDir);
    FingerprintDir(h, dir, 0);
    wchar_t chain[MAX_PATH], chainPath[MAX_PATH * 2], id[64];
    IniStr(L"d3d9", L"chain", L"", chain, _countof(chain));
    const wchar_t* files[2] = {g_ini, NULL};
    if (chain[0] && _snwprintf_s(chainPath, _countof(chainPath), _TRUNCATE, L"%s\\%s", g_root, chain) > 0)
        files[1] = chainPath;
    for (const wchar_t* file : files) {
        if (!file || !FileIdentity(file, id, _countof(id))) continue;
        uint32_t one = 2166136261u;
        Fnv(one, id, wcslen(id) * sizeof(wchar_t));
        h ^= one;
    }
    return h;
}

static void StateWrite(const wchar_t* section, const wchar_t* key, const wchar_t* value) {
    WritePrivateProfileStringW(section, key, value, g_stateIni);
}

static void StateWriteInt(const wchar_t* section, const wchar_t* key, ULONGLONG v) {
    wchar_t b[32];
    _snwprintf_s(b, _countof(b), _TRUNCATE, L"%llu", v);
    WritePrivateProfileStringW(section, key, b, g_stateIni);
}

static int StateInt(const wchar_t* section, const wchar_t* key, int def) {
    return (int)GetPrivateProfileIntW(section, key, def, g_stateIni);
}

// A plugin's key in runtime-state.ini ([strikes], [strikes_file], [quarantine]).  A file name that is a
// plain ini key stays as it is (what Riftstone.cmd and Studio read).  Any other is "~" and the hex of its
// lower-case UTF-8: a name with '=' would be split there by the ini reader (its strikes never added up and
// each run appended another line), one starting with ';' '#' '[' or '~' or with spaces at either end would
// be read as something else, and one outside printable ASCII would turn into '?' in the ANSI file.
#define PLUGIN_KEY_CAP (2 + 6 * MAX_PATH)
static void PluginKey(const wchar_t* name, wchar_t* out, size_t cap) {
    size_t n = wcslen(name);
    BOOL plain = n > 0 && !wcschr(L";#[~ \t", name[0]) && name[n - 1] != L' ' && name[n - 1] != L'\t';
    for (size_t i = 0; plain && i < n; i++) plain = name[i] >= 0x20 && name[i] < 0x7F && name[i] != L'=';
    if (plain) {
        wcsncpy_s(out, cap, name, _TRUNCATE);
        return;
    }
    wchar_t lower[MAX_PATH];
    wcsncpy_s(lower, _countof(lower), name, _TRUNCATE);
    CharLowerBuffW(lower, (DWORD)wcslen(lower));
    char utf8[3 * MAX_PATH];
    int bytes = WideCharToMultiByte(CP_UTF8, 0, lower, -1, utf8, sizeof utf8, NULL, NULL);
    size_t k = 0;
    out[k++] = L'~';
    for (int i = 0; i + 1 < bytes && k + 3 <= cap; i++) {
        static const wchar_t hex[] = L"0123456789abcdef";
        out[k++] = hex[(BYTE)utf8[i] >> 4];
        out[k++] = hex[(BYTE)utf8[i] & 15];
    }
    out[k] = 0;
}

// A plugin's value in one section under its key.  An older loader wrote it under the bare file name: that
// entry, when the ini reader can find it (no '=' in the name), is read and moved to the key.
static void StatePluginGet(const wchar_t* section, const wchar_t* name, const wchar_t* key, wchar_t* out, DWORD cap) {
    GetPrivateProfileStringW(section, key, L"", out, cap, g_stateIni);
    if (out[0] || wcscmp(key, name) == 0 || wcschr(name, L'=')) return;
    GetPrivateProfileStringW(section, name, L"", out, cap, g_stateIni);
    if (!out[0]) return;
    StateWrite(section, key, out);
    StateWrite(section, name, NULL);
}

// Older loaders put a name with '=' in as its own key, and the ini reader splits the line at the first '=':
// "crash=plugin.asi=1" reads as the key "crash".  Those lines were never read back (another was added each
// run); remove them.
static void DropSplitLines(const wchar_t* name) {
    const wchar_t* eq = wcschr(name, L'=');
    wchar_t head[MAX_PATH], tail[MAX_PATH + 1], value[2 * MAX_PATH];
    if (!eq || eq == name || (size_t)(eq - name) >= _countof(head)) return;
    wcsncpy_s(head, _countof(head), name, (size_t)(eq - name));
    _snwprintf_s(tail, _countof(tail), _TRUNCATE, L"%s=", eq + 1);
    const wchar_t* sections[] = {L"strikes", L"strikes_file", L"quarantine"};
    for (const wchar_t* section : sections) {
        for (int i = 0; i < 256; i++) {
            GetPrivateProfileStringW(section, head, L"", value, _countof(value), g_stateIni);
            if (_wcsnicmp(value, tail, wcslen(tail)) != 0) break;
            StateWrite(section, head, NULL);
        }
    }
}

static DWORD WINAPI SafeModeNotice(LPVOID) {
    MessageBoxW(NULL,
                L"Dragon's Dogma crashed twice in a row while starting, with your mods and plugins in place.\n\n"
                L"This run starts without them (Riftstone safe mode), so you can play. They come back on their own "
                L"when you change a mod or plugin, or with:  Riftstone.cmd loader safe-mode off\n\n"
                L"What happened is in riftstone\\logs (Riftstone.cmd crash explains it).",
                L"Riftstone safe mode", MB_OK | MB_ICONWARNING | MB_TOPMOST | MB_SETFOREGROUND);
    return 0;
}

// Read last-crash.txt (if the last session crashed) into its parts; every part is set either way.
static BOOL ReadMarker(ULONGLONG* uptime, wchar_t* module, wchar_t* kind, wchar_t* report) {
    *uptime = ~0ULL;
    module[0] = kind[0] = report[0] = 0;
    HANDLE h = Real_CreateFileW(g_marker, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    if (h == INVALID_HANDLE_VALUE) return FALSE;
    char buf[4096];
    DWORD got = 0;
    ReadFile(h, buf, sizeof buf - 1, &got, NULL);
    CloseHandle(h);
    buf[got] = 0;
    char* ctx = NULL;
    for (char* line = strtok_s(buf, "\r\n", &ctx); line; line = strtok_s(NULL, "\r\n", &ctx)) {
        if (strncmp(line, "uptime_ms=", 10) == 0) *uptime = _strtoui64(line + 10, NULL, 10);
        else if (strncmp(line, "module=", 7) == 0 && !MultiByteToWideChar(CP_UTF8, 0, line + 7, -1, module, MAX_PATH)) module[0] = 0;
        else if (strncmp(line, "kind=", 5) == 0 && !MultiByteToWideChar(CP_UTF8, 0, line + 5, -1, kind, 16)) kind[0] = 0;
        else if (strncmp(line, "report=", 7) == 0 && !MultiByteToWideChar(CP_UTF8, 0, line + 7, -1, report, MAX_PATH)) report[0] = 0;
    }
    module[MAX_PATH - 1] = kind[15] = report[MAX_PATH - 1] = 0;
    return TRUE;
}

// How the last session ended, from what it left: the crash note, and [session]'s reason and clean flag.
// One line in loader.log, and [last_session] for Riftstone.cmd crash / doctor / Studio.
static void NoteLastSession(BOOL crashed, const wchar_t* kind, ULONGLONG crashUp, const wchar_t* report, int wasClean,
                            int alive) {
    wchar_t started[32], loader[32], end[32], detail[512];
    GetPrivateProfileStringW(L"session", L"started", L"", started, _countof(started), g_stateIni);
    if (!started[0]) return;                                   // the first run with this loader
    GetPrivateProfileStringW(L"session", L"loader", L"", loader, _countof(loader), g_stateIni);
    GetPrivateProfileStringW(L"session", L"end", L"", end, _countof(end), g_stateIni);
    GetPrivateProfileStringW(L"session", L"end_detail", L"", detail, _countof(detail), g_stateIni);
    int endUp = (int)GetPrivateProfileIntW(L"session", L"end_uptime_ms", -1, g_stateIni);
    ULONGLONG up = end[0] && endUp >= 0 ? (ULONGLONG)endUp : (ULONGLONG)(alive > 0 ? alive : 0);
    const wchar_t* how = end;
    const wchar_t* text = SessionEndText(end);
    if (crashed) {
        how = wcscmp(kind, L"fatal") == 0 ? L"fatal-error" : L"crash";
        if (crashUp != ~0ULL) up = crashUp;
        if (end[0] && wcscmp(end, how) != 0) LogLine(L"last run was closing when it stopped: %s", text[0] ? text : end);
    } else if (end[0] && wasClean) {
        LogLine(L"last run closed after %llu min %llu s, ended by: %s", up / 60000, (up / 1000) % 60, text[0] ? text : end);
    } else if (end[0] && wcscmp(end, L"session-end") == 0) {
        LogLine(L"last run was ended by Windows after %llu min %llu s (%s)", up / 60000, (up / 1000) % 60, detail);
    } else if (end[0]) {
        LogLine(L"last run was closing (%s) but did not finish by itself: it was ended from outside, or it died without "
                L"a crash report", text[0] ? text : end);
    } else if (wasClean) {
        how = L"unknown";
        LogLine(L"last run exited normally after about %llu min; what closed it was not recorded", up / 60000);
    } else {
        how = L"not-clean";
        LogLine(L"last run did not exit normally and left no report (alive %llu min or more): it was ended from outside "
                L"(Task Manager, Steam's Stop, another program), the PC lost power, or it died without a crash report",
                up / 60000);
    }
    WritePrivateProfileStringW(L"last_session", NULL, NULL, g_stateIni);     // this record replaces the last one
    StateWrite(L"last_session", L"started", started);
    StateWrite(L"last_session", L"end", how);
    if (crashed && end[0] && wcscmp(end, how) != 0) StateWrite(L"last_session", L"closing", end);
    if (detail[0]) StateWrite(L"last_session", L"detail", detail);
    StateWriteInt(L"last_session", L"uptime_ms", up);
    StateWrite(L"last_session", L"clean", wasClean ? L"1" : L"0");
    if (loader[0]) StateWrite(L"last_session", L"loader", loader);
    const wchar_t* name = report && report[0] ? wcsrchr(report, L'\\') : NULL;
    if (report && report[0]) StateWrite(L"last_session", L"report", name ? name + 1 : report);
}

void StabilityStart() {
    g_crash = IniInt(L"loader", L"crash_reports", 1) != 0;
    g_minidump = IniInt(L"loader", L"minidump", 1) != 0;
    g_keepReports = IniInt(L"loader", L"keep_reports", 10);
    if (g_keepReports < 1) g_keepReports = 1;
    _snwprintf_s(g_stateIni, _countof(g_stateIni), _TRUNCATE, L"%s\\runtime-state.ini", g_stateDir);
    _snwprintf_s(g_marker, _countof(g_marker), _TRUNCATE, L"%s\\last-crash.txt", g_logDir);
    if (g_inFilter == TLS_OUT_OF_INDEXES) g_inFilter = TlsAlloc();
    g_stackLimits = (StackLimits_t)GetProcAddress(GetModuleHandleW(L"kernel32.dll"), "GetCurrentThreadStackLimits");
    Rotate(L"crash");
    Rotate(L"fatal");
    Rotate(L"hang");

    BOOL automatic = IniInt(L"loader", L"safe_mode", 1) != 0;
    int early = StateInt(L"session", L"early_crashes", 0);
    int wasClean = StateInt(L"session", L"clean", 1);
    int alive = StateInt(L"session", L"alive_ms", 0);
    ULONGLONG up = ~0ULL;
    wchar_t module[MAX_PATH] = L"", kind[16] = L"", report[MAX_PATH] = L"";
    BOOL crashed = ReadMarker(&up, module, kind, report);
    if (crashed) {
        DeleteFileW(g_marker);
        BOOL startUp = up < EARLY_MS;
        LogLine(L"last run ended in a %s after %llu s%s", kind[0] ? kind : L"crash", up == ~0ULL ? 0 : up / 1000,
                startUp ? L" (during start-up)" : L"");
        const wchar_t* base = module[0] ? wcsrchr(module, L'\\') : NULL;
        wchar_t pluginsDir[MAX_PATH];
        _snwprintf_s(pluginsDir, _countof(pluginsDir), _TRUNCATE, L"%s\\plugins\\", g_stateDir);
        BOOL byPlugin = module[0] && _wcsnicmp(module, pluginsDir, wcslen(pluginsDir)) == 0 && base;
        if (startUp && byPlugin) {
            const wchar_t* name = base + 1;
            wchar_t id[64] = L"", prevId[64], count[32], key[PLUGIN_KEY_CAP];
            FileIdentity(module, id, _countof(id));
            PluginKey(name, key, _countof(key));
            DropSplitLines(name);
            StatePluginGet(L"strikes_file", name, key, prevId, _countof(prevId));
            StatePluginGet(L"strikes", name, key, count, _countof(count));
            int strikes = wcscmp(prevId, id) == 0 ? _wtoi(count) + 1 : 1;
            StateWriteInt(L"strikes", key, strikes);
            StateWrite(L"strikes_file", key, id);
            LogLine(L"plugin   %s was at fault in a start-up crash (%d in a row)", name, strikes);
            if (strikes >= 2) {
                StateWrite(L"quarantine", key, id);
                LogLine(L"plugin   %s QUARANTINED: it is skipped until its file changes "
                        L"(or Riftstone.cmd loader plugin release %s)", name, key);
            }
            early = 0;
        } else if (startUp) {
            early++;
        } else {
            early = 0;
        }
    } else if (wasClean || alive >= (int)EARLY_MS) {
        early = 0;
    }
    StateWriteInt(L"session", L"early_crashes", early);
    NoteLastSession(crashed, kind, up, report, wasClean, alive);

    // Safe mode: on after two unexplained start-up crashes in a row, off when the setup changes.
    uint32_t fp = SetupFingerprint();
    wchar_t fps[16];
    _snwprintf_s(fps, _countof(fps), _TRUNCATE, L"%08x", fp);
    int on = StateInt(L"safe_mode", L"on", 0);
    if (on) {
        wchar_t was[16];
        GetPrivateProfileStringW(L"safe_mode", L"setup", L"", was, _countof(was), g_stateIni);
        if (_wcsicmp(was, fps) != 0) {
            on = 0;
            StateWrite(L"safe_mode", L"on", L"0");
            LogLine(L"safe     mods or plugins changed since safe mode began; they are back on");
        }
    }
    if (!on && automatic && early >= 2) {
        on = 1;
        StateWrite(L"safe_mode", L"on", L"1");
        StateWrite(L"safe_mode", L"setup", fps);
        StateWrite(L"safe_mode", L"noticed", L"0");
        StateWriteInt(L"session", L"early_crashes", 0);
    }
    g_safeMode = on != 0;
    if (g_safeMode) {
        LogLine(L"safe     SAFE MODE: the game crashed twice in a row while starting; this run has no plugins "
                L"and no overlay. It ends when a mod or plugin changes, or: Riftstone.cmd loader safe-mode off");
        if (!StateInt(L"safe_mode", L"noticed", 0) && IniInt(L"loader", L"safe_mode_notice", 1)) {
            StateWrite(L"safe_mode", L"noticed", L"1");
            HANDLE t = CreateThread(NULL, 0, SafeModeNotice, NULL, 0, NULL);
            if (t) CloseHandle(t);
        }
    }
    StateWrite(L"session", L"clean", L"0");
    StateWrite(L"session", L"alive_ms", L"0");
    StateWrite(L"session", L"end", NULL);
    StateWrite(L"session", L"end_detail", NULL);
    StateWrite(L"session", L"end_uptime_ms", NULL);
    wchar_t started[32];
    Stamp(started, _countof(started));
    StateWrite(L"session", L"started", started);
    StateWrite(L"session", L"loader", RIFTSTONE_LOADER_VERSION);
}

BOOL SafeModeActive() { return g_safeMode; }
BOOL OverlayAllowed() { return !g_safeMode; }

BOOL PluginAllowed(const wchar_t* name, const wchar_t* fullPath) {
    if (g_safeMode) {
        LogLine(L"plugin   %s skipped (safe mode)", name);
        return FALSE;
    }
    wchar_t q[64], id[64], key[PLUGIN_KEY_CAP];
    PluginKey(name, key, _countof(key));
    StatePluginGet(L"quarantine", name, key, q, _countof(q));
    if (!q[0]) return TRUE;
    if (FileIdentity(fullPath, id, _countof(id)) && wcscmp(q, id) == 0) {
        LogLine(L"plugin   %s skipped: quarantined after two start-up crashes (a new version of the file is loaded again)", name);
        return FALSE;
    }
    StateWrite(L"quarantine", key, NULL);
    StateWrite(L"strikes", key, NULL);
    LogLine(L"plugin   %s changed since its quarantine; loading it again", name);
    return TRUE;
}

void StabilityAlive() { StateWriteInt(L"session", L"alive_ms", UptimeMs()); }

void StabilityRecordEnd(const wchar_t* code, const wchar_t* detail, ULONGLONG uptimeMs) {
    StateWrite(L"session", L"end", code);
    StateWrite(L"session", L"end_detail", detail && detail[0] ? detail : NULL);
    StateWriteInt(L"session", L"end_uptime_ms", uptimeMs);
}

void StabilityCleanExit() {
    // A crash the game recovered from (a filter further down continued it) is no reason for safe mode
    // once the game has ended normally.  A fatal error ends through exit(1), which is this path too,
    // and keeps its note.
    if (!g_markerFatal) DeleteFileW(g_marker);
    StateWrite(L"session", L"clean", L"1");
    StateWriteInt(L"session", L"alive_ms", UptimeMs());
}

void StabilityInstallHooks() {
    // Crash reports off: the game's own filter goes straight to Windows (the hook would only keep it for
    // CrashFilter, which is not installed then).
    if (g_crash)
        HookImport("KERNEL32.dll", "SetUnhandledExceptionFilter", (void*)Hook_SetUnhandledExceptionFilter,
                   (void**)&Real_SetUnhandledExceptionFilter);
    else
        LogLine(L"crash    reports off ([loader] crash_reports = 0): the game's own crash filter is left to Windows");
    if (!Real_SetUnhandledExceptionFilter)
        Real_SetUnhandledExceptionFilter = (SetUEF_t)GetProcAddress(GetModuleHandleW(L"kernel32.dll"), "SetUnhandledExceptionFilter");
    HookImport("USER32.dll", "MessageBoxA", (void*)Hook_MessageBoxA, (void**)&Real_MessageBoxA);
    if (!Real_MessageBoxA) {
        HMODULE u = LoadLibraryW(L"user32.dll");
        Real_MessageBoxA = u ? (MessageBoxA_t)GetProcAddress(u, "MessageBoxA") : NULL;
    }
}

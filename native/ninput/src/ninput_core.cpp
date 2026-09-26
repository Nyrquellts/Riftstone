#include "ninput_core.hpp"

#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cwchar>
#include <mutex>

#include "display_arbiter.hpp"
#include "hook_registry.hpp"
#include "ninput.h"

namespace ninput {
namespace {

wchar_t g_root[MAX_PATH];       // game folder (dir of the host exe), no trailing slash
wchar_t g_pluginsDir[MAX_PATH]; // <root>\ninput\plugins
wchar_t g_logPath[MAX_PATH];    // <root>\ninput\ninput.log (empty until core_attach runs)
bool g_attached = false;
const NinputInterface* g_interface = nullptr;

void log_sink(const char* message) { core_log("%s", message); }

int detect_game() {
    wchar_t exe[MAX_PATH];
    GetModuleFileNameW(nullptr, exe, MAX_PATH);
    const wchar_t* base = wcsrchr(exe, L'\\');
    base = base ? base + 1 : exe;

    HMODULE self = GetModuleHandleW(nullptr);
    auto* dos = reinterpret_cast<IMAGE_DOS_HEADER*>(self);
    auto* nt = reinterpret_cast<IMAGE_NT_HEADERS*>(reinterpret_cast<BYTE*>(self) + dos->e_lfanew);
    DWORD ts = nt->FileHeader.TimeDateStamp;

    if (_wcsicmp(base, L"DDDA.exe") == 0) {
        if (ts == 0x5A314C31) return NINPUT_GAME_DDDA_2364871;
        core_log("host is DDDA.exe but PE timestamp 0x%08lX is not the tested build 0x5A314C31; "
                 "engine calls stay UNIMPLEMENTED", ts);
        return NINPUT_GAME_UNKNOWN;
    }
    if (_wcsicmp(base, L"DDO.exe") == 0) return NINPUT_GAME_DDO;
    return NINPUT_GAME_UNKNOWN;
}

// Load *.dll/*.asi from the plugins folder in deterministic name order; each is a Ninput plugin
// that exports Ninput_Initialize(interface). This is separate from the Riftstone loader's own
// generic .asi loading -- these get the SDK handshake.
void load_plugins() {
    if (GetFileAttributesW(g_pluginsDir) == INVALID_FILE_ATTRIBUTES) {
        core_log("plugins  %ls absent (none to load)", g_pluginsDir);
        return;
    }
    wchar_t pattern[MAX_PATH];
    _snwprintf_s(pattern, _countof(pattern), _TRUNCATE, L"%ls\\*", g_pluginsDir);
    wchar_t names[128][MAX_PATH];
    int n = 0;
    WIN32_FIND_DATAW fd;
    HANDLE find = FindFirstFileW(pattern, &fd);
    if (find != INVALID_HANDLE_VALUE) {
        do {
            if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) continue;
            const wchar_t* dot = wcsrchr(fd.cFileName, L'.');
            if (!dot || (_wcsicmp(dot, L".asi") != 0 && _wcsicmp(dot, L".dll") != 0)) continue;
            if (n < 128) wcscpy_s(names[n++], MAX_PATH, fd.cFileName);
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
    core_log("plugins  %d found in %ls", n, g_pluginsDir);
    for (int i = 0; i < n; i++) {
        wchar_t full[MAX_PATH];
        _snwprintf_s(full, _countof(full), _TRUNCATE, L"%ls\\%ls", g_pluginsDir, names[i]);
        HMODULE m = LoadLibraryW(full);
        if (!m) {
            core_log("plugin   %ls FAILED to load (error %lu)", names[i], GetLastError());
            continue;
        }
        auto init = reinterpret_cast<Ninput_Initialize_t>(GetProcAddress(m, NINPUT_PLUGIN_INIT));
        if (!init) {
            core_log("plugin   %ls loaded but exports no %s (left running)", names[i], NINPUT_PLUGIN_INIT);
            continue;
        }
        int ok = init(g_interface);
        core_log("plugin   %ls %s", names[i], ok ? "initialised" : "declined (Ninput_Initialize returned 0)");
    }
}

}  // namespace

void core_log(const char* fmt, ...) {
    char body[900];
    va_list ap;
    va_start(ap, fmt);
    int n = _vsnprintf_s(body, sizeof body, _TRUNCATE, fmt, ap);
    va_end(ap);
    if (n < 0) n = (int)strlen(body);

    SYSTEMTIME t;
    GetLocalTime(&t);
    char line[1024];
    int m = _snprintf_s(line, sizeof line, _TRUNCATE, "%02u:%02u:%02u.%03u  %s\r\n", t.wHour, t.wMinute,
                        t.wSecond, t.wMilliseconds, body);
    if (m < 0) m = (int)strlen(line);

    // Also visible to a debugger, and safe before core_attach has set a log path (e.g. in tests).
    OutputDebugStringA(line);
    if (!g_logPath[0]) return;

    static std::mutex log_mutex;  // function-local: initialised on first use, no manual setup
    std::scoped_lock lk(log_mutex);
    HANDLE h = CreateFileW(g_logPath, FILE_APPEND_DATA, FILE_SHARE_READ, nullptr, OPEN_ALWAYS,
                           FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h != INVALID_HANDLE_VALUE) {
        DWORD w;
        WriteFile(h, line, (DWORD)m, &w, nullptr);
        CloseHandle(h);
    }
}

HMODULE load_system_dll(const wchar_t* name) {
    wchar_t path[MAX_PATH];
    UINT n = GetSystemDirectoryW(path, MAX_PATH);
    if (n == 0 || n >= MAX_PATH) return nullptr;
    _snwprintf_s(path + n, MAX_PATH - n, _TRUNCATE, L"\\%ls", name);
    return LoadLibraryW(path);
}

const wchar_t* core_root() { return g_root; }

void core_attach(HMODULE /*self*/) {
    static LONG once = 0;
    if (InterlockedExchange(&once, 1) != 0) return;

    GetModuleFileNameW(nullptr, g_root, MAX_PATH);
    if (wchar_t* slash = wcsrchr(g_root, L'\\')) *slash = 0;
    _snwprintf_s(g_pluginsDir, _countof(g_pluginsDir), _TRUNCATE, L"%ls\\ninput\\plugins", g_root);
    wchar_t dir[MAX_PATH];
    _snwprintf_s(dir, _countof(dir), _TRUNCATE, L"%ls\\ninput", g_root);
    CreateDirectoryW(dir, nullptr);
    _snwprintf_s(g_logPath, _countof(g_logPath), _TRUNCATE, L"%ls\\ninput\\ninput.log", g_root);
    DeleteFileW(g_logPath);

    int game = detect_game();
    auto base = reinterpret_cast<uintptr_t>(GetModuleHandleW(nullptr));
    g_interface = make_interface(game, base, log_sink);
    g_attached = true;

    const char* gn = game == NINPUT_GAME_DDDA_2364871 ? "Dragon's Dogma: Dark Arisen (build 2364871)"
                     : game == NINPUT_GAME_DDO         ? "Dragon's Dogma Online"
                                                       : "an unrecognised host";
    core_log("Ninput 0.1.0 attached to %s at image base 0x%08X", gn, (unsigned)base);
    display_arbiter_start();  // owns Reset before the game creates its device
    load_plugins();           // plugins register display callbacks in their Ninput_Initialize
}

}  // namespace ninput

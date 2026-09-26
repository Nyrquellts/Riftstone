// save_backup -- Riftstone native plugin: every save the game writes is copied, so an earlier one
// can be put back.
//
// What the game does
//   Dragon's Dogma: Dark Arisen keeps one save per Steam account, written through Steam's remote
//   storage (the exe imports SteamRemoteStorage) to Steam\userdata\<account>\367500\remote\DDDA.sav.
//   The file is always 524,288 bytes: a 32-byte header (version 21, the XML size, the compressed
//   size, three fixed words, the inverted CRC-32 of the compressed data), the save as zlib-compressed
//   XML, then zeros.  Every autosave, checkpoint and inn rest overwrites it, and Steam Cloud keeps
//   whatever is there.
//
// What this plugin does (save_backup.ini, next to this file)
//   A background thread looks at the save every CheckSeconds.  Once it has changed and then stayed
//   the same for one more look, and its header and checksum say it is complete, it is copied to
//   <Folder>\<account>\DDDA_<date>_<time>.sav (the save's own time; Folder defaults to
//   %LOCALAPPDATA%\Riftstone\saves), unless the newest copy already holds the same bytes.  The newest
//   Keep copies stay, plus the save each of the last KeepSessions game sessions started from
//   (sessions.txt).  Nothing is written next to the save, so Steam Cloud never sees the copies.
//   `riftstone saves list` / `riftstone saves restore` put one back.
//
// Safety
//   No game code is patched.  The save is read for a moment after the game has finished writing it
//   and is never written.  A copy is written under a .tmp name and renamed when complete.
//   riftstone\logs\save_backup.log says what it did.  Original code; no third-party source.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>

#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <algorithm>
#include <map>
#include <set>
#include <string>
#include <vector>

namespace {

constexpr uintptr_t IMAGE_BASE = 0x00400000;

// ---- the save file ----------------------------------------------------------------------------
constexpr DWORD SAVE_SIZE = 524288, HEADER = 32, VERSION = 21;
constexpr uint32_t MAGIC1 = 0x334D234D, MAGIC2 = 0, MAGIC3 = 0x334D4044, MAGIC4 = 0x40565235;

uint32_t g_crcTable[256];

void InitCrc() {
    for (uint32_t i = 0; i < 256; i++) {
        uint32_t c = i;
        for (int k = 0; k < 8; k++) c = (c & 1) ? 0xEDB88320u ^ (c >> 1) : c >> 1;
        g_crcTable[i] = c;
    }
}

uint32_t Crc32(const uint8_t* p, size_t n) {
    uint32_t c = 0xFFFFFFFFu;
    for (size_t i = 0; i < n; i++) c = g_crcTable[(c ^ p[i]) & 0xFF] ^ (c >> 8);
    return c ^ 0xFFFFFFFFu;
}

uint32_t U32(const uint8_t* p) {
    uint32_t v;
    memcpy(&v, p, 4);
    return v;
}

// Why these bytes are not a complete save, or nullptr when they are.
const char* WhyNotSave(const std::vector<uint8_t>& d) {
    if (d.size() != SAVE_SIZE) return "it is not 524,288 bytes";
    if (U32(&d[12]) != MAGIC1 || U32(&d[16]) != MAGIC2 || U32(&d[20]) != MAGIC3 || U32(&d[28]) != MAGIC4)
        return "the header is not a Dragon's Dogma save header";
    if (U32(&d[0]) != VERSION) return "the header is not version 21 (Dark Arisen)";
    uint32_t xml = U32(&d[4]), packed = U32(&d[8]);
    if (!xml || packed < 2 || packed > SAVE_SIZE - HEADER) return "its sizes are out of range";
    if (~Crc32(&d[HEADER], packed) != U32(&d[24])) return "its checksum does not match";
    return nullptr;
}

// ---- settings ---------------------------------------------------------------------------------
struct Settings {
    bool enabled = true;
    int keep = 20;
    int keepSessions = 10;
    std::wstring folder;    // resolved
    std::wstring saveFile;  // empty: Steam's saves
    DWORD intervalMs = 2000;
};
Settings g_set;
wchar_t g_logPath[MAX_PATH];

void Log(const char* fmt, ...) {
    char line[1024];
    va_list ap;
    va_start(ap, fmt);
    int n = _vsnprintf_s(line, sizeof line - 2, _TRUNCATE, fmt, ap);
    va_end(ap);
    if (n < 0) n = (int)strlen(line);
    line[n++] = '\r';
    line[n++] = '\n';
    HANDLE h = CreateFileW(g_logPath, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_ALWAYS,
                           FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) return;
    DWORD w;
    WriteFile(h, line, (DWORD)n, &w, nullptr);
    CloseHandle(h);
}

std::wstring Env(const wchar_t* name) {
    wchar_t buf[MAX_PATH];
    DWORD n = GetEnvironmentVariableW(name, buf, MAX_PATH);
    return n && n < MAX_PATH ? std::wstring(buf, n) : std::wstring();
}

std::wstring Expand(const std::wstring& s) {
    wchar_t buf[MAX_PATH];
    DWORD n = ExpandEnvironmentStringsW(s.c_str(), buf, MAX_PATH);
    return n && n <= MAX_PATH ? std::wstring(buf) : s;
}

std::wstring Trim(const wchar_t* s) {
    while (*s == L' ' || *s == L'\t') s++;
    std::wstring t(s);
    while (!t.empty() && (t.back() == L' ' || t.back() == L'\t')) t.pop_back();
    return t;
}

bool IsAuto(const std::wstring& s) { return s.empty() || _wcsicmp(s.c_str(), L"auto") == 0; }

double ReadNumber(const wchar_t* ini, const wchar_t* key, double def) {
    wchar_t buf[64];
    GetPrivateProfileStringW(L"backup", key, L"", buf, 64, ini);
    std::wstring s = Trim(buf);
    if (IsAuto(s)) return def;
    wchar_t* end = nullptr;
    double v = wcstod(s.c_str(), &end);
    return (end == s.c_str() || v != v) ? def : v;
}

void LoadSettings(HMODULE self) {
    wchar_t ini[MAX_PATH];
    GetModuleFileNameW(self, ini, MAX_PATH);
    wchar_t* dot = wcsrchr(ini, L'.');
    wchar_t* slash = wcsrchr(ini, L'\\');
    if (dot && (!slash || dot > slash)) *dot = 0;
    wcscat_s(ini, L".ini");
    bool haveIni = GetFileAttributesW(ini) != INVALID_FILE_ATTRIBUTES;

    Settings s;
    s.enabled = GetPrivateProfileIntW(L"backup", L"Enabled", 1, ini) != 0;
    s.keep = (int)std::min(std::max(ReadNumber(ini, L"Keep", 20), 1.0), 1000.0);
    s.keepSessions = (int)std::min(std::max(ReadNumber(ini, L"KeepSessions", 10), 0.0), 1000.0);
    double every = std::min(std::max(ReadNumber(ini, L"CheckSeconds", 2), 0.05), 3600.0);
    s.intervalMs = (DWORD)(every * 1000.0 + 0.5);
    wchar_t buf[MAX_PATH];
    GetPrivateProfileStringW(L"backup", L"Folder", L"auto", buf, MAX_PATH, ini);
    std::wstring folder = Trim(buf);
    if (IsAuto(folder)) {
        // Riftstone's home, as `riftstone saves` finds it: %RIFTSTONE_HOME%, else %LOCALAPPDATA%\Riftstone.
        std::wstring home = Env(L"RIFTSTONE_HOME");
        if (home.empty()) {
            std::wstring local = Env(L"LOCALAPPDATA");
            home = local.empty() ? L"" : local + L"\\Riftstone";
        }
        folder = home.empty() ? L"" : home + L"\\saves";
    } else {
        folder = Expand(folder);
    }
    while (folder.size() > 3 && (folder.back() == L'\\' || folder.back() == L'/')) folder.pop_back();
    s.folder = folder;
    GetPrivateProfileStringW(L"backup", L"SaveFile", L"auto", buf, MAX_PATH, ini);
    std::wstring file = Trim(buf);
    s.saveFile = IsAuto(file) ? L"" : Expand(file);
    g_set = s;
    Log("settings: %s; keep the newest %d and the start of the last %d session(s); look every %.2f s; copies in %S",
        haveIni ? "save_backup.ini" : "no save_backup.ini, defaults", s.keep, s.keepSessions, s.intervalMs / 1000.0,
        s.folder.empty() ? L"(nowhere: no LOCALAPPDATA)" : s.folder.c_str());
}

// ---- where the saves are ----------------------------------------------------------------------
struct Target {
    std::wstring path, account;
};

std::wstring RegString(HKEY hive, const wchar_t* key, const wchar_t* value) {
    wchar_t buf[MAX_PATH];
    DWORD size = sizeof buf;
    if (RegGetValueW(hive, key, value, RRF_RT_REG_SZ, nullptr, buf, &size) != ERROR_SUCCESS) return L"";
    std::wstring s(buf);
    std::replace(s.begin(), s.end(), L'/', L'\\');
    return s;
}

std::vector<std::wstring> SteamRoots() {
    std::vector<std::wstring> roots;
    std::wstring over = Env(L"RIFTSTONE_STEAM_ROOT");
    if (!over.empty()) return {over};
    for (const std::wstring& r : {RegString(HKEY_CURRENT_USER, L"Software\\Valve\\Steam", L"SteamPath"),
                                  RegString(HKEY_LOCAL_MACHINE, L"SOFTWARE\\WOW6432Node\\Valve\\Steam", L"InstallPath"),
                                  std::wstring(L"C:\\Program Files (x86)\\Steam")}) {
        if (r.empty()) continue;
        bool seen = false;
        for (const std::wstring& o : roots) seen |= _wcsicmp(o.c_str(), r.c_str()) == 0;
        if (!seen) roots.push_back(r);
    }
    return roots;
}

// Every Steam account's DDDA.sav (userdata\<account>\367500\remote), or the SaveFile setting.
std::vector<Target> FindSaves() {
    std::vector<Target> out;
    if (!g_set.saveFile.empty()) {
        out.push_back({g_set.saveFile, L"other"});
        return out;
    }
    std::set<std::wstring> accounts;
    for (const std::wstring& root : SteamRoots()) {
        WIN32_FIND_DATAW fd;
        HANDLE h = FindFirstFileW((root + L"\\userdata\\*").c_str(), &fd);
        if (h == INVALID_HANDLE_VALUE) continue;
        do {
            if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) || fd.cFileName[0] == L'.') continue;
            std::wstring account = fd.cFileName;
            if (account.find_first_not_of(L"0123456789") != std::wstring::npos || accounts.count(account)) continue;
            std::wstring path = root + L"\\userdata\\" + account + L"\\367500\\remote\\DDDA.sav";
            if (GetFileAttributesW(path.c_str()) == INVALID_FILE_ATTRIBUTES) continue;
            accounts.insert(account);
            out.push_back({path, account});
        } while (FindNextFileW(h, &fd));
        FindClose(h);
    }
    return out;
}

// ---- the copies -------------------------------------------------------------------------------
bool MakeDirs(const std::wstring& dir) {
    if (dir.empty()) return false;
    DWORD a = GetFileAttributesW(dir.c_str());
    if (a != INVALID_FILE_ATTRIBUTES) return (a & FILE_ATTRIBUTE_DIRECTORY) != 0;
    size_t cut = dir.find_last_of(L"\\/");
    if (cut != std::wstring::npos && cut > 2 && !MakeDirs(dir.substr(0, cut))) return false;
    return CreateDirectoryW(dir.c_str(), nullptr) || GetLastError() == ERROR_ALREADY_EXISTS;
}

bool ReadWhole(const std::wstring& path, std::vector<uint8_t>& data, DWORD limit) {
    // Every share mode, so the game (or Steam) is never refused while this is open.
    HANDLE h = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr,
                           OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL | FILE_FLAG_SEQUENTIAL_SCAN, nullptr);
    if (h == INVALID_HANDLE_VALUE) return false;
    LARGE_INTEGER size;
    bool ok = GetFileSizeEx(h, &size) && size.QuadPart <= limit;
    if (ok) {
        data.resize((size_t)size.QuadPart);
        DWORD got = 0;
        ok = data.empty() || (ReadFile(h, data.data(), (DWORD)data.size(), &got, nullptr) && got == data.size());
    }
    CloseHandle(h);
    return ok;
}

bool EndsWith(const std::wstring& s, const wchar_t* tail) {
    size_t n = wcslen(tail);
    return s.size() >= n && _wcsicmp(s.c_str() + s.size() - n, tail) == 0;
}

// The copies in a folder, oldest first (their names sort by the save's time).
std::vector<std::wstring> Copies(const std::wstring& dir) {
    std::vector<std::wstring> names;
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW((dir + L"\\DDDA_*.sav").c_str(), &fd);
    if (h == INVALID_HANDLE_VALUE) return names;
    do {
        // The pattern can also match through 8.3 short names; keep only real *.sav names.
        if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) && EndsWith(fd.cFileName, L".sav"))
            names.push_back(fd.cFileName);
    } while (FindNextFileW(h, &fd));
    FindClose(h);
    std::sort(names.begin(), names.end());
    return names;
}

std::wstring NameFor(const FILETIME& written, int n) {
    // Local time with the daylight-saving rule of that date, as `riftstone saves` names copies.
    SYSTEMTIME utc, t;
    FileTimeToSystemTime(&written, &utc);
    if (!SystemTimeToTzSpecificLocalTime(nullptr, &utc, &t)) t = utc;
    wchar_t name[64];
    if (n <= 1)
        _snwprintf_s(name, 64, _TRUNCATE, L"DDDA_%04u-%02u-%02u_%02u-%02u-%02u.sav", t.wYear, t.wMonth, t.wDay, t.wHour,
                     t.wMinute, t.wSecond);
    else
        _snwprintf_s(name, 64, _TRUNCATE, L"DDDA_%04u-%02u-%02u_%02u-%02u-%02u_%d.sav", t.wYear, t.wMonth, t.wDay, t.wHour,
                     t.wMinute, t.wSecond, n);
    return name;
}

bool SameBytes(const std::wstring& path, const std::vector<uint8_t>& data) {
    std::vector<uint8_t> other;
    return ReadWhole(path, other, SAVE_SIZE) && other == data;
}

// Writes the copy under a .tmp name, then renames it.  Returns 0, or the Windows error.
DWORD WriteCopy(const std::wstring& dir, const std::wstring& name, const std::vector<uint8_t>& data,
                const FILETIME& written) {
    std::wstring tmp = dir + L"\\" + name + L".tmp", final = dir + L"\\" + name;
    HANDLE h = CreateFileW(tmp.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) return GetLastError();
    DWORD w = 0, err = 0;
    if (!WriteFile(h, data.data(), (DWORD)data.size(), &w, nullptr) || w != data.size() || !FlushFileBuffers(h))
        err = GetLastError() ? GetLastError() : ERROR_WRITE_FAULT;
    SetFileTime(h, nullptr, nullptr, &written);  // the copy carries the save's own time
    CloseHandle(h);
    if (!err && !MoveFileExW(tmp.c_str(), final.c_str(), MOVEFILE_WRITE_THROUGH)) err = GetLastError();
    if (err) DeleteFileW(tmp.c_str());
    return err;
}

// The copies the last `count` sessions started from: the last word of each line of sessions.txt.
std::set<std::wstring> SessionStarts(const std::wstring& dir, int count) {
    std::vector<uint8_t> text;
    std::vector<std::wstring> names;
    if (count > 0 && ReadWhole(dir + L"\\sessions.txt", text, 1 << 20)) {
        size_t start = 0;
        for (size_t i = 0; i <= text.size(); i++) {
            if (i < text.size() && text[i] != '\n') continue;
            size_t end = i;
            while (end > start && (text[end - 1] == '\r' || text[end - 1] == ' ' || text[end - 1] == '\t')) end--;
            size_t word = end;
            while (word > start && text[word - 1] != ' ' && text[word - 1] != '\t') word--;
            if (word < end) names.push_back(std::wstring(text.begin() + word, text.begin() + end));
            start = i + 1;
        }
    }
    std::set<std::wstring> keep;
    for (size_t i = names.size() > (size_t)count ? names.size() - count : 0; i < names.size(); i++) keep.insert(names[i]);
    return keep;
}

void AppendSession(const std::wstring& dir, const std::wstring& name) {
    SYSTEMTIME t;
    GetLocalTime(&t);
    char line[160];
    int n = _snprintf_s(line, sizeof line, _TRUNCATE, "%04u-%02u-%02u %02u:%02u:%02u  %S\r\n", t.wYear, t.wMonth, t.wDay,
                        t.wHour, t.wMinute, t.wSecond, name.c_str());
    HANDLE h = CreateFileW((dir + L"\\sessions.txt").c_str(), FILE_APPEND_DATA, FILE_SHARE_READ, nullptr, OPEN_ALWAYS,
                           FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) return;
    DWORD w;
    WriteFile(h, line, (DWORD)n, &w, nullptr);
    CloseHandle(h);
}

void Prune(const std::wstring& dir) {
    std::vector<std::wstring> names = Copies(dir);
    std::set<std::wstring> keep = SessionStarts(dir, g_set.keepSessions);
    size_t first = names.size() > (size_t)g_set.keep ? names.size() - g_set.keep : 0;
    int removed = 0;
    for (size_t i = 0; i < first; i++) {
        if (keep.count(names[i])) continue;
        if (DeleteFileW((dir + L"\\" + names[i]).c_str())) removed++;
    }
    if (removed) Log("  removed %d older cop%s (keeping the newest %d and session starts)", removed, removed == 1 ? "y" : "ies", g_set.keep);
}

void RemoveTemps(const std::wstring& dir) {
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW((dir + L"\\DDDA_*.sav.tmp").c_str(), &fd);
    if (h == INVALID_HANDLE_VALUE) return;
    do DeleteFileW((dir + L"\\" + fd.cFileName).c_str());
    while (FindNextFileW(h, &fd));
    FindClose(h);
}

// ---- watching ---------------------------------------------------------------------------------
struct Watch {
    bool seen = false, done = false;
    ULONGLONG written = 0, size = 0;
    int tries = 0;
};
std::map<std::wstring, Watch> g_watch;
std::set<std::wstring> g_started;  // accounts whose session start is recorded
bool g_toldNoSave = false;

// One look at a save: a change is copied once it has stayed the same for a second look.
void Look(const Target& t) {
    Watch& w = g_watch[t.path];
    WIN32_FILE_ATTRIBUTE_DATA a;
    if (!GetFileAttributesExW(t.path.c_str(), GetFileExInfoStandard, &a)) return;
    ULONGLONG written = ((ULONGLONG)a.ftLastWriteTime.dwHighDateTime << 32) | a.ftLastWriteTime.dwLowDateTime;
    ULONGLONG size = ((ULONGLONG)a.nFileSizeHigh << 32) | a.nFileSizeLow;
    if (!w.seen || written != w.written || size != w.size) {
        w.seen = true;
        w.written = written;
        w.size = size;
        w.done = false;
        w.tries = 0;
        return;
    }
    if (w.done) return;

    std::vector<uint8_t> data;
    // Read a little more than a save can be, so a wrong size is reported as one.
    const char* why = ReadWhole(t.path, data, SAVE_SIZE * 2) ? WhyNotSave(data) : "it could not be read";
    if (why) {
        // A save in the middle of being written; the next look tries again, for a while.
        if (w.tries++ == 0) Log("%S: not a complete save yet (%s); looking again", t.path.c_str(), why);
        if (w.tries >= 50) {
            Log("%S: gave up on this version of the file (%s)", t.path.c_str(), why);
            w.done = true;
        }
        return;
    }
    std::wstring dir = g_set.folder + L"\\" + t.account;
    if (!MakeDirs(dir)) {
        if (w.tries++ == 0) Log("cannot create %S (%lu)", dir.c_str(), GetLastError());
        return;
    }
    bool first = !g_started.count(t.account);
    if (first) RemoveTemps(dir);
    std::vector<std::wstring> copies = Copies(dir);
    std::wstring name;
    if (!copies.empty() && SameBytes(dir + L"\\" + copies.back(), data)) {
        name = copies.back();
        if (first) Log("%S: the save is unchanged since %S", t.account.c_str(), name.c_str());
    } else {
        DWORD err = 0;
        for (int n = 1; n <= 99 && name.empty() && !err; n++) {
            std::wstring candidate = NameFor(a.ftLastWriteTime, n);
            std::wstring full = dir + L"\\" + candidate;
            if (GetFileAttributesW(full.c_str()) != INVALID_FILE_ATTRIBUTES) {
                if (SameBytes(full, data)) name = candidate;
                continue;
            }
            err = WriteCopy(dir, candidate, data, a.ftLastWriteTime);
            if (!err) {
                name = candidate;
                Log("%S: saved a copy, %S", t.account.c_str(), name.c_str());
            }
        }
        if (name.empty()) {
            if (w.tries++ == 0) Log("%S: cannot write a copy in %S (%lu); trying again", t.account.c_str(), dir.c_str(), err);
            if (w.tries >= 50) w.done = true;
            return;
        }
    }
    if (first) {
        AppendSession(dir, name);
        g_started.insert(t.account);
    }
    Prune(dir);
    w.done = true;
}

DWORD WINAPI Worker(LPVOID) {
    SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_BELOW_NORMAL);
    for (;;) {
        std::vector<Target> saves = FindSaves();
        if (saves.empty() && !g_toldNoSave) {
            Log(g_set.saveFile.empty() ? "no DDDA.sav under Steam's userdata yet; looking again as the game runs"
                                       : "SaveFile %S does not exist yet; looking again as the game runs",
                g_set.saveFile.c_str());
            g_toldNoSave = true;
        }
        for (const Target& t : saves) Look(t);
        Sleep(g_set.intervalMs);
    }
}

void Start(HMODULE self) {
    wchar_t root[MAX_PATH];
    GetModuleFileNameW(nullptr, root, MAX_PATH);
    wchar_t* slash = wcsrchr(root, L'\\');
    if (slash) *slash = 0;
    wchar_t dir[MAX_PATH];
    _snwprintf_s(dir, MAX_PATH, _TRUNCATE, L"%s\\riftstone", root);
    CreateDirectoryW(dir, nullptr);
    _snwprintf_s(dir, MAX_PATH, _TRUNCATE, L"%s\\riftstone\\logs", root);
    CreateDirectoryW(dir, nullptr);
    _snwprintf_s(g_logPath, MAX_PATH, _TRUNCATE, L"%s\\riftstone\\logs\\save_backup.log", root);
    // A fresh log per game session; a second copy of the plugin in the same process appends to it.
    wchar_t probe[8];
    if (GetEnvironmentVariableW(L"RIFTSTONE_SAVE_BACKUP_LOG", probe, 8) == 0) {
        DeleteFileW(g_logPath);
        SetEnvironmentVariableW(L"RIFTSTONE_SAVE_BACKUP_LOG", L"1");
    }
    // One copy per process: a second one would race the first for the same files.
    if (GetEnvironmentVariableW(L"RIFTSTONE_SAVE_BACKUP_RUNNING", probe, 8) > 0) {
        Log("refused: another copy of save_backup is already running in this game");
        return;
    }
    bool harness = GetEnvironmentVariableW(L"RIFTSTONE_SAVE_BACKUP_HARNESS", probe, 8) > 0;
    if (!harness && (uintptr_t)GetModuleHandleW(nullptr) != IMAGE_BASE) {
        Log("refused: the host is not DDDA.exe at 0x00400000");
        return;
    }
    LoadSettings(self);
    if (!g_set.enabled) {
        Log("save_backup: Enabled=0, nothing copied");
        return;
    }
    if (g_set.folder.empty()) {
        Log("refused: no folder for the copies (set Folder in save_backup.ini)");
        return;
    }
    InitCrc();
    SetEnvironmentVariableW(L"RIFTSTONE_SAVE_BACKUP_RUNNING", L"1");
    Log("save_backup: watching %s", harness ? "(harness)" : "the save while the game runs");
    // The thread starts once the loader has finished loading plugins; nothing here waits for it.
    HANDLE t = CreateThread(nullptr, 0, Worker, nullptr, 0, nullptr);
    if (!t) {
        Log("failed: cannot start the watcher thread (%lu)", GetLastError());
        return;
    }
    CloseHandle(t);
}

}  // namespace

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(module);
        Start(module);
    }
    return TRUE;
}

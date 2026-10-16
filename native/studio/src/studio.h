// Riftstone Studio (RiftstoneStudio.exe): the game folder, its features and their settings, overhaul profiles and the
// game's start, for players who have no Python. The rules it follows are the toolkit's (src/riftstone/plugins.py and
// profiles.py); catalog.inc is written from them by tools/gen_studio_catalog.py, and native/studio/test/run_tests.py
// compares this port with the toolkit.
#pragma once
#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>

#include <cstdint>
#include <string>
#include <utility>
#include <vector>

namespace studio {

using std::wstring;

enum RuleKind { RULE_INT, RULE_FLOAT, RULE_AUTO_FLOAT, RULE_AUTO_INT, RULE_INT_OR_ZERO, RULE_FLOAT_OR_ZERO, RULE_SHADOW,
                RULE_FKEY, RULE_CHOICE, RULE_TEXT /* no rule: any short line */ };

struct CatalogEntry {
    const wchar_t* stem;
    const wchar_t* title;
    const wchar_t* summary;
};

struct Rule {
    const wchar_t* stem;
    const wchar_t* section;
    const wchar_t* key;          // nullptr: every key of the section
    int kind;
    double lo, hi;
    const wchar_t* loText;       // the bounds as the toolkit's messages print them
    const wchar_t* hiText;
    const wchar_t* choices;      // '|'-joined, for RULE_CHOICE
};

extern const char* const kVersion;       // the toolkit's version this build follows (catalog.inc)
extern const wchar_t* const kGameTitle;  // "Dragon's Dogma: Dark Arisen"
extern const wchar_t* const kLoaderStem; // "loader": the loader's own settings, riftstone_loader.ini
// What the catalog carries from package.py, legal.py and loader.py (catalog.inc)
bool IsOwnPlugin(const wstring& stem);   // package.OWN_PLUGINS, any case
size_t OwnPluginCount();
const wchar_t* OwnPluginAt(size_t i);
const wchar_t* LicenseText();            // Riftstone's MIT licence, CRLF
const wchar_t* DisclaimerText();         // legal.DISCLAIMER
const wchar_t* FreeText();               // legal.FREE
const wchar_t* D3d9ChainPath();          // loader.D3D9_CHAIN: riftstone\dxvk\d3d9.dll

// What a value must be (plugins.rule_for): the rule for (stem, section, key), its section's, the 0/1 of a known
// switch, or RULE_TEXT.
struct Check {
    int kind = RULE_TEXT;
    double lo = 0, hi = 0;
    wstring loText, hiText;
    std::vector<wstring> choices;  // RULE_CHOICE: the choices; RULE_FKEY: F1..F12 and the named keys
    bool known() const { return kind != RULE_TEXT; }
};
Check CheckFor(const wstring& stem, const wstring& section, const wstring& key);
// plugins._valid: the value as it is written, or false and the toolkit's message.
bool Validate(const wstring& stem, const wstring& section, const wstring& key, const wstring& value, wstring& clean,
              wstring& error);
const CatalogEntry* CatalogFind(const wstring& stem);
size_t CatalogSize();
const CatalogEntry& CatalogAt(size_t i);

// -- ini files, every byte kept but the value's (plugins.read_settings / write_setting) ------------------------------
struct Setting {
    wstring section, key, value, help;
};
bool ReadSettings(const wstring& path, std::vector<Setting>& out);   // false: no such file (out empty)
bool WriteSetting(const wstring& path, const wstring& section, const wstring& key, const wstring& value,
                  wstring& error);

// -- the game folder -------------------------------------------------------------------------------------------------
struct Found {
    wstring root;    // the game folder, "" when none
    wstring how;     // "given", "next to Studio", "RIFTSTONE_GAME", "Steam library"
    std::vector<wstring> tried;
};
Found FindGame(const wstring& given, const wstring& exeDir);
bool IsGameFolder(const wstring& dir);   // DDDA.exe and nativePC\rom (game.detect_kind)
bool GameRunning();                      // a DDDA.exe process exists
wstring LoaderVersion(const wstring& dll);   // runtime.loader_version; "" when it is not Riftstone's loader
wstring StateDir(const wstring& game);       // <game>\riftstone
wstring PluginsDir(const wstring& game);
wstring OffDir(const wstring& game);
wstring LogsDir(const wstring& game);
wstring LoaderIni(const wstring& game);

struct Feature {
    wstring stem, file, dir;       // file: the .asi/.dll name on disk; dir: the folder it is in
    wstring title, summary;
    bool on = false, known = false;
    wstring path() const;
    wstring ini() const;           // its settings file (may not exist)
};
std::vector<Feature> ListFeatures(const wstring& game);   // plugins\ wins over plugins\off\ (plugins.describe)
bool Toggle(const wstring& game, const wstring& stem, bool on, bool& changed, wstring& error);   // plugins.toggle

struct SessionInfo {
    bool safeMode = false;
    wstring started, end, loader;  // the most recent session ([session] of runtime-state.ini)
    long long uptimeMs = -1;
    wstring newestReport;          // the newest crash-, fatal- or hang- report in riftstone\logs, or ""
};
SessionInfo ReadSession(const wstring& game);
bool EndSafeMode(const wstring& game, wstring& error);   // runtime.safe_mode_off
bool Launch(const wstring& game, DWORD& pid, wstring& error);

// -- overhaul profiles (profiles.py, riftstone-profile/1) ------------------------------------------------------------
struct ProfileSection {
    wstring section;
    std::vector<std::pair<wstring, wstring>> values;   // key, value
};
struct ProfileStem {
    wstring stem;
    std::vector<ProfileSection> sections;
};
struct Profile {
    wstring name, madeWith;
    std::vector<std::pair<wstring, bool>> features;
    std::vector<ProfileStem> settings;
    size_t valueCount() const;
};
bool ProfileFromGame(const wstring& game, const wstring& name, Profile& p, wstring& error);   // profiles.export
std::string ProfileToJson(const Profile& p);                                                // profiles.to_json, UTF-8
bool ProfileFromJson(const std::string& bytes, Profile& p, wstring& error);                // profiles.parse
struct ProfileWrite {
    wstring file, section, key, value;
};
struct ApplyReport {
    int switched = 0, set = 0, unchanged = 0;
    std::vector<wstring> skipped;
    std::vector<ProfileWrite> writes;
};
bool ApplyProfile(const wstring& game, const Profile& p, bool dryRun, ApplyReport& report, wstring& error);

// -- what the running game publishes (live.cpp; runtime.py parse_live, describe_live, memory_verdict) --------------
const wchar_t* const kLiveName = L"Local\\RiftstoneLive";
struct LiveStats {
    enum : uint32_t { KNOWN_BUILD = 1, SAFE_MODE = 2, FRAME_TIMING = 4, ADDRESS_SPACE_LOW = 8, HANG = 16, EXITED = 32,
                      LARGE_ADDRESS_AWARE = 64, MEMORY_PRESSURE = 128, D3D_COUNTED = 256 };
    uint32_t version = 0, size = 0, pid = 0, game = 0, exeTimestamp = 0, flags = 0, seq = 0, uptimeMs = 0;
    uint64_t updateFiletime = 0, vaTotal = 0, vaUsed = 0, vaLargestFree = 0, vaUsedPeak = 0, vaLargestFreeMin = 0;
    uint64_t privateBytes = 0, workingSet = 0;
    uint32_t handles = 0, frames = 0, frameUsLast = 0, frameUsAvg = 0, frameUsP99 = 0, frameUsMax = 0, stutters = 0;
    uint32_t redirects = 0, missing = 0, fallbacks = 0, fatals = 0, pluginsLoaded = 0, pluginsNotLoaded = 0;
    int32_t enemiesActive = -1, enemiesUsable = -1, enemySlots = -1, stage = -1;   // negative: not known
    uint32_t backbufferW = 0, backbufferH = 0, windowedRaw = 0, refreshHz = 0, ringPos = 0, vramAvailMb = 0;
    uint32_t resourcesUsed = 0, resourceSlots = 0, memVerdict = 0;
    uint64_t privateBytesPeak = 0, d3dManaged = 0, d3dManagedPeak = 0, d3dDefault = 0, d3dSystem = 0;
    uint32_t d3dObjects = 0, d3dProvider = 0, pressure = 0, pressureEpisodes = 0;
    wstring loaderVersion, lastFile, lastFallback, plugins, notes, d3dPath;
    std::vector<uint32_t> frameTimesUs;                          // oldest first, at most 256
    std::vector<std::pair<wstring, wstring>> pluginList;         // name, state
    bool Flag(uint32_t bit) const { return (flags & bit) != 0; }
    wstring GameName() const;
    const wchar_t* ProviderName() const;   // nullptr when the game has not created Direct3D yet
    wstring Fps() const;                   // round(1e6 / average, 1) as Python prints it, or "None"
};
bool ParseLive(const std::string& blob, LiveStats& s, wstring& error);
std::vector<wstring> DescribeLive(const LiveStats& s);
// Level 0 unknown, 1 headroom, 2 tight, 3 bound; the advice in words.
int MemoryVerdict(uint64_t peakMb, uint64_t freeMinMb, uint64_t vaLeftMb, bool offloaded, wstring& advice);
bool ReadLiveMapping(const wstring& name, std::string& blob);   // one page of shared memory, by the seqlock
bool ReadLive(DWORD pid, LiveStats& s, const wstring& name = L"");   // pid 0: whichever game publishes
bool RequestSnapshot(DWORD pid, wstring& error);
wstring DecodeUtf8Replace(const std::string& s);   // Python's decode("utf-8", "replace")
wstring Commas(uint64_t v);                        // Python's f"{v:,}"

// -- the Graphics page (graphics.cpp): the game's config.ini, ENB, ReShade, DXVK and enemy crowds -----------------
// An ini as graphics.ini_values reads it: lower-case names, the first block of a section and the first of a key.
struct IniSection {
    wstring name;
    std::vector<std::pair<wstring, wstring>> values;
};
using IniMap = std::vector<IniSection>;
IniMap IniValues(const wstring& text);
wstring IniLookup(const IniMap& m, const wstring& section, const wstring& key);   // "" when absent
// graphics.set_ini: each [section] key set in place, every other byte kept; the keys in the order the toolkit's
// values dict lists them (a new section starts where the section's spelling changes). False and the toolkit's
// words when a key is not in the file. before: {section: {key: old value}} in the order they were set.
struct IniValue {
    wstring section, key, value;
};
bool SetIniText(const wstring& text, const std::vector<IniValue>& values, const wstring& what, wstring& out,
                std::vector<IniValue>& before, wstring& error);
bool ReadOneByteIni(const wstring& path, wstring& text, wstring& error);   // a byte a character; UTF-16 refused
wstring GameConfigPath();   // %LOCALAPPDATA%\CAPCOM\DRAGONS DOGMA DARK ARISEN\config.ini (playtest.game_config)
struct GraphicsInfo {
    wstring config, configProblem;   // config.ini's path ("" without LOCALAPPDATA); why it cannot be read
    bool configFound = false;
    wstring fov, viewRange;          // [GRAPHICS] CameraFov and ViewRange as the file has them, "" when absent
    bool enb = false;                // enbseries.ini or enblocal.ini beside DDDA.exe
    wstring enbEffect;               // enbseries.ini [GLOBAL] UseEffect as written, "" when absent
    wstring enbKeys;                 // enblocal.ini [INPUT]: the key that turns the effects on and off
    wstring enbProxy;                // enblocal.ini [PROXY] ProxyLibrary when EnableProxyLibrary is true
    bool reshade = false;
    wstring reshadeEffects, reshadeOverlay;   // ReShade.ini [INPUT] KeyEffects and KeyOverlay, in words
    wstring folderD3d9;              // what the game folder's own d3d9.dll is ("" when none)
    wstring chain;                   // [d3d9] chain as the loader takes it
    bool loaderIni = false, dxvkHere = false;   // riftstone_loader.ini; riftstone\dxvk\d3d9.dll
    bool enemyCap = false, enemyCapOn = false, collisionCap = false, collisionCapOn = false;
    int slots = -1;                  // enemy_cap.ini slots, -1 when not read
};
GraphicsInfo ReadGraphics(const wstring& game);
wstring KeyName(int vk);   // a virtual-key code in words: 123 -> F12
bool SetGameConfig(const wstring& key, const wstring& value, wstring& written, wstring& error);
bool SetEnbEffect(const wstring& game, bool on, wstring& error);
bool SetDxvkChain(const wstring& game, bool on, wstring& error);
bool SetEnemyCrowd(const wstring& game, int slots, wstring& done, wstring& error);

// -- the mod pack (pack.cpp): this game folder's Riftstone setup as a zip for another player --------------------------
struct PackReport {
    wstring out, readme;
    uint64_t bytes = 0;
    std::vector<wstring> files, plugins, pluginsOff, leftOut;
};
bool ExportPack(const wstring& game, const wstring& name, const wstring& out, PackReport& r, wstring& error);
std::string Sha256Hex(const std::string& bytes);
uint32_t Crc32(const std::string& bytes);

// -- small helpers ---------------------------------------------------------------------------------------------------
wstring LowerAscii(const wstring& s);
wstring Lower(const wstring& s);         // simple Unicode lower case
bool IsPySpace(wchar_t c);
wstring Trim(const wstring& s);          // Python's str.strip()
size_t CodePoints(const wstring& s);
wstring PyRepr(const wstring& s);        // Python's repr of a str, for messages
bool FileExists(const wstring& path);
bool DirExists(const wstring& path);
bool PathExists(const wstring& path);
bool ReadFileBytes(const wstring& path, std::string& out, size_t limit = 64u << 20);
bool WriteFileAtomic(const wstring& path, const std::string& bytes, wstring& error);
bool MakeDirs(const wstring& path);
wstring FromUtf8(const std::string& s);
std::string ToUtf8(const wstring& s);
bool DecodeUtf8Strict(const std::string& s, wstring& out);
wstring JoinPath(const wstring& a, const wstring& b);
wstring NormalPath(const wstring& p);    // '/' as '\', no trailing '\' (as Python's Path prints it)
wstring FileName(const wstring& path);
wstring ParentDir(const wstring& path);
wstring ExeDir();
wstring Widen(const char* s);

}  // namespace studio

// The window (ui.cpp).
int RunStudioWindow(HINSTANCE inst, const std::wstring& game, int show);
// Tests: the window built and never shown, clicked through with mouse messages; 0 when every step did what it should.
int RunGuiSmoke(HINSTANCE inst, const std::wstring& game, const std::wstring& profileOut, std::wstring& log);
// A page drawn into a bitmap and saved as PNG, never shown on screen.
int RenderPage(HINSTANCE inst, const std::wstring& game, const std::wstring& page, const std::wstring& png, int width,
               int height, int dpi, std::wstring& log);

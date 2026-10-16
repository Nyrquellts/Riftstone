// RiftstoneStudio.exe: with no arguments the window; with arguments a command line that does what the window does
// (and the tests' batch mode). It writes to the console it was started from, or to the pipe a caller gave it.
#include "studio.h"

#include <shellapi.h>

#include <cstdio>
#include <cwchar>

using namespace studio;

namespace {

HANDLE gOut = nullptr, gErr = nullptr;
bool gOutConsole = false, gErrConsole = false;

void InitStd() {
    gOut = GetStdHandle(STD_OUTPUT_HANDLE);
    gErr = GetStdHandle(STD_ERROR_HANDLE);
    auto usable = [](HANDLE h) { return h && h != INVALID_HANDLE_VALUE && GetFileType(h) != FILE_TYPE_UNKNOWN; };
    if (!usable(gOut) && AttachConsole(ATTACH_PARENT_PROCESS)) {
        HANDLE con = CreateFileW(L"CONOUT$", GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr,
                                 OPEN_EXISTING, 0, nullptr);
        if (con != INVALID_HANDLE_VALUE) {
            gOut = con;
            if (!usable(gErr)) gErr = con;
        }
    }
    if (!usable(gErr)) gErr = gOut;
    DWORD mode;
    gOutConsole = usable(gOut) && GetConsoleMode(gOut, &mode);
    gErrConsole = usable(gErr) && GetConsoleMode(gErr, &mode);
}

void Put(HANDLE h, bool console, const wstring& s) {
    if (!h || h == INVALID_HANDLE_VALUE) return;
    DWORD done;
    if (console) {
        WriteConsoleW(h, s.data(), DWORD(s.size()), &done, nullptr);
    } else {
        std::string u = ToUtf8(s);
        WriteFile(h, u.data(), DWORD(u.size()), &done, nullptr);
    }
}

void Out(const wstring& s) { Put(gOut, gOutConsole, s + L"\n"); }
void Err(const wstring& s) { Put(gErr, gErrConsole, s + L"\n"); }

int Fail(const wstring& s) {
    Err(L"error: " + s);
    return 1;
}

// The game folder: --game, else the folder Studio was last pointed at, else FindGame's search.
wstring SavedGame() {
    wchar_t buf[MAX_PATH * 2] = L"";
    wchar_t local[MAX_PATH];
    if (!GetEnvironmentVariableW(L"LOCALAPPDATA", local, MAX_PATH)) return L"";
    GetPrivateProfileStringW(L"game", L"folder", L"", buf, DWORD(std::size(buf)),
                             JoinPath(local, L"Riftstone\\studio.ini").c_str());
    return IsGameFolder(buf) ? wstring(buf) : L"";
}

bool ResolveGame(const wstring& given, wstring& game, wstring& error) {
    wstring g = given;
    if (g.empty()) {   // the folder chosen in the window last time, while it still is the game's
        wstring saved = SavedGame();
        if (!saved.empty() && IsGameFolder(saved)) g = saved;
    }
    Found f = FindGame(g, ExeDir());
    if (f.root.empty()) {
        wstring tried;
        for (const auto& t : f.tried) tried += (tried.empty() ? L"" : L", ") + t;
        error = wstring(kGameTitle) + L" was not found (looked in " + (tried.empty() ? L"(nothing)" : tried) +
                L"). Pass --game \"C:\\path\\to\\game\".";
        return false;
    }
    game = f.root;
    return true;
}

// -- the tests' batch mode: one request a line, every text field "x" + its UTF-16LE (or raw) bytes in hex ------------

std::string Hex(const std::string& bytes) {
    static const char* d = "0123456789abcdef";
    std::string o = "x";
    for (char c : bytes) {
        o += d[uint8_t(c) >> 4];
        o += d[uint8_t(c) & 15];
    }
    return o;
}

std::string HexW(const wstring& s) {
    std::string b;
    for (wchar_t c : s) {
        b += char(c & 0xFF);
        b += char(c >> 8);
    }
    return Hex(b);
}

bool Unhex(const std::string& f, std::string& out) {
    out.clear();
    if (f.empty() || f[0] != 'x' || (f.size() - 1) % 2) return false;
    auto v = [](char c) { return c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : -1; };
    for (size_t i = 1; i < f.size(); i += 2) {
        int a = v(f[i]), b = v(f[i + 1]);
        if (a < 0 || b < 0) return false;
        out += char(a * 16 + b);
    }
    return true;
}

bool UnhexW(const std::string& f, wstring& out) {
    std::string b;
    if (!Unhex(f, b) || b.size() % 2) return false;
    out.clear();
    for (size_t i = 0; i < b.size(); i += 2) out += wchar_t(uint8_t(b[i]) | (uint8_t(b[i + 1]) << 8));
    return true;
}

const char* KindName(int kind) {
    switch (kind) {
    case RULE_INT: return "int";
    case RULE_FLOAT: return "float";
    case RULE_AUTO_FLOAT: return "auto_float";
    case RULE_AUTO_INT: return "auto_int";
    case RULE_INT_OR_ZERO: return "int_or_zero";
    case RULE_FLOAT_OR_ZERO: return "float_or_zero";
    case RULE_SHADOW: return "shadow";
    case RULE_FKEY: return "fkey";
    case RULE_CHOICE: return "choice";
    }
    return "none";
}

std::string BatchLine(const std::vector<std::string>& w) {
    const std::string& cmd = w[0];
    std::vector<wstring> a;
    for (size_t i = 1; i < w.size(); i++) {
        wstring s;
        if (cmd == "parse" || cmd == "live") break;
        if (!UnhexW(w[i], s)) return "bad field";
        a.push_back(s);
    }
    wstring clean, error;
    if (cmd == "validate" && a.size() == 4)
        return Validate(a[0], a[1], a[2], a[3], clean, error) ? "ok " + HexW(clean) : "err " + HexW(error);
    if (cmd == "rule" && a.size() == 3) return std::string("rule ") + KindName(CheckFor(a[0], a[1], a[2]).kind);
    if (cmd == "read" && a.size() == 1) {
        std::vector<Setting> s;
        if (!ReadSettings(a[0], s)) return "missing";
        std::string o = "ok " + std::to_string(s.size());
        for (const auto& v : s) o += " " + HexW(v.section) + " " + HexW(v.key) + " " + HexW(v.value) + " " + HexW(v.help);
        return o;
    }
    if (cmd == "write" && a.size() == 4)
        return WriteSetting(a[0], a[1], a[2], a[3], error) ? "ok" : "err " + HexW(error);
    if (cmd == "live" && w.size() == 2) {
        std::string raw;
        if (!Unhex(w[1], raw) && w[1] != "x") return "bad field";
        LiveStats s;
        if (!ParseLive(raw, s, error)) return "err " + HexW(error);
        auto lines = DescribeLive(s);
        std::string o = "ok " + std::to_string(lines.size());
        for (const auto& l : lines) o += " " + HexW(l);
        o += " " + std::to_string(s.frameTimesUs.size());
        for (uint32_t t : s.frameTimesUs) o += " " + std::to_string(t);
        o += " " + std::to_string(s.pluginList.size());
        for (const auto& p : s.pluginList) o += " " + HexW(p.first) + " " + HexW(p.second);
        const wchar_t* provider = s.ProviderName();
        o += " " + HexW(s.Fps()) + " " + HexW(s.GameName()) + " " + HexW(provider ? provider : L"None");
        return o;
    }
    if (cmd == "setini" && a.size() >= 2 && (a.size() - 2) % 3 == 0) {
        std::vector<IniValue> values, before;
        for (size_t i = 2; i < a.size(); i += 3) values.push_back({a[i], a[i + 1], a[i + 2]});
        wstring out;
        if (!SetIniText(a[0], values, a[1], out, before, error)) return "err " + HexW(error);
        std::string o = "ok " + HexW(out) + " " + std::to_string(before.size());
        for (const auto& b : before) o += " " + HexW(b.section) + " " + HexW(b.key) + " " + HexW(b.value);
        return o;
    }
    if (cmd == "iniv" && a.size() == 1) {
        IniMap m = IniValues(a[0]);
        std::string o = "ok " + std::to_string(m.size());
        for (const auto& sec : m) {
            o += " " + HexW(sec.name) + " " + std::to_string(sec.values.size());
            for (const auto& kv : sec.values) o += " " + HexW(kv.first) + " " + HexW(kv.second);
        }
        return o;
    }
    if (cmd == "parse" && w.size() == 2) {
        std::string raw;
        if (!Unhex(w[1], raw) && w[1] != "x") return "bad field";
        Profile p;
        if (!ProfileFromJson(raw, p, error)) return "err " + HexW(error);
        std::string json = ProfileToJson(p);
        Profile again;
        if (!ProfileFromJson(json, again, error) || ProfileToJson(again) != json) return "unstable";
        return "ok " + Hex(json);
    }
    return "bad request";
}

int Batch() {
    HANDLE in = GetStdHandle(STD_INPUT_HANDLE);
    std::string all, chunk(1 << 16, '\0');
    DWORD got;
    while (ReadFile(in, &chunk[0], DWORD(chunk.size()), &got, nullptr) && got) all.append(chunk.data(), got);
    std::string out;
    size_t start = 0;
    while (start < all.size()) {
        size_t end = all.find('\n', start);
        if (end == std::string::npos) end = all.size();
        std::string line = all.substr(start, end - start);
        start = end + 1;
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.empty()) continue;
        std::vector<std::string> w;
        size_t a = 0;
        for (;;) {
            size_t b = line.find(' ', a);
            w.push_back(line.substr(a, b == std::string::npos ? std::string::npos : b - a));
            if (b == std::string::npos) break;
            a = b + 1;
        }
        out += BatchLine(w) + "\n";
    }
    DWORD done;
    WriteFile(gOut, out.data(), DWORD(out.size()), &done, nullptr);
    return 0;
}

// -- commands ------------------------------------------------------------------------------------------------------

wstring IniFor(const wstring& game, const wstring& stem, wstring& rulesStem) {
    rulesStem = stem;
    if (Lower(stem) == kLoaderStem) {
        rulesStem = kLoaderStem;
        return LoaderIni(game);
    }
    for (const auto& f : ListFeatures(game))
        if (Lower(f.stem) == Lower(stem)) {
            rulesStem = f.stem;
            return f.ini();
        }
    return L"";
}

int CmdList(const wstring& game) {
    auto feats = ListFeatures(game);
    if (feats.empty()) Out(L"no features in " + PluginsDir(game));
    for (const auto& f : feats)
        Out(wstring(f.on ? L"on   " : L"off  ") + f.stem + (f.known ? L" -- " + f.title : L" (not one of Riftstone's)"));
    return 0;
}

int CmdSettings(const wstring& game, const wstring& stem) {
    wstring rules, ini = IniFor(game, stem, rules);
    if (ini.empty()) return Fail(L"no plugin named " + PyRepr(stem) + L" in " + PluginsDir(game));
    std::vector<Setting> s;
    if (!ReadSettings(ini, s)) return Fail(FileName(ini) + L" does not exist");
    for (const auto& v : s) Out(L"[" + v.section + L"] " + v.key + L" = " + v.value);
    return 0;
}

int CmdSet(const wstring& game, const wstring& stem, const wstring& section, const wstring& key, const wstring& value) {
    wstring rules, ini = IniFor(game, stem, rules);
    if (ini.empty()) return Fail(L"no plugin named " + PyRepr(stem) + L" in " + PluginsDir(game));
    wstring clean, error;
    if (!Validate(rules, section, key, value, clean, error)) return Fail(error);
    if (!WriteSetting(ini, section, key, clean, error)) return Fail(error);
    Out(FileName(ini) + L" [" + section + L"] " + key + L" = " + clean);
    return 0;
}

int CmdToggle(const wstring& game, const wstring& stem, bool on) {
    std::vector<wstring> stems;
    if (Lower(stem) == L"all") {
        for (const auto& f : ListFeatures(game)) stems.push_back(f.stem);
    } else stems.push_back(stem);
    for (const auto& s : stems) {
        bool changed = false;
        wstring error;
        if (!Toggle(game, s, on, changed, error)) return Fail(error);
        Out(s + (changed ? (on ? L": on" : L": off") : (on ? L": already on" : L": already off")));
    }
    return 0;
}

int CmdExport(const wstring& game, const wstring& file, const wstring& name) {
    Profile p;
    wstring error, n = name;
    if (n.empty()) {
        n = FileName(file);
        size_t dot = n.rfind(L'.');
        if (dot != wstring::npos && dot > 0) n = n.substr(0, dot);
    }
    if (!ProfileFromGame(game, n, p, error)) return Fail(error);
    if (!WriteFileAtomic(file, ProfileToJson(p), error)) return Fail(error);
    Out(L"wrote " + file + L": " + std::to_wstring(p.features.size()) + L" features, " +
        std::to_wstring(p.valueCount()) + L" settings");
    return 0;
}

int CmdImport(const wstring& game, const wstring& file, bool dry) {
    std::string raw;
    if (!ReadFileBytes(file, raw, 64u << 20)) return Fail(L"cannot read " + file);
    Profile p;
    ApplyReport r;
    wstring error;
    if (!ProfileFromJson(raw, p, error)) return Fail(error);
    if (!ApplyProfile(game, p, dry, r, error)) return Fail(error);
    Out(p.name + L": " + (dry ? L"would switch " : L"switched ") + std::to_wstring(r.switched) + L" features, " +
        (dry ? L"would set " : L"set ") + std::to_wstring(r.set) + L" settings, " + std::to_wstring(r.unchanged) +
        L" already so");
    for (const auto& w : r.writes) Out(w.file + L" [" + w.section + L"] " + w.key + L" = " + w.value);
    for (const auto& s : r.skipped) Out(L"left out: " + s);
    return 0;
}

int CmdGraphics(const wstring& game) {
    GraphicsInfo g = ReadGraphics(game);
    if (g.config.empty()) Out(L"config.ini: LOCALAPPDATA is not set");
    else if (!g.configProblem.empty()) Out(L"config.ini: " + g.configProblem);
    else if (!g.configFound) Out(L"config.ini: none at " + g.config + L" (the game writes it at its first start)");
    else {
        Out(L"config.ini: " + g.config);
        Out(L"  [GRAPHICS] CameraFov = " + (g.fov.empty() ? L"(not in the file)" : g.fov));
        Out(L"  [GRAPHICS] ViewRange = " + (g.viewRange.empty() ? L"(not in the file)" : g.viewRange));
    }
    if (g.enb)
        Out(L"ENB: effects " + (g.enbEffect.empty() ? wstring(L"(UseEffect not set)") : L"UseEffect = " + g.enbEffect) +
            (g.enbKeys.empty() ? L"" : L"; in game " + g.enbKeys + L" turns them on and off") +
            (g.enbProxy.empty() ? L"" : L"; it hands Direct3D on to " + g.enbProxy));
    else Out(L"ENB: not in the game folder");
    if (g.reshade)
        Out(L"ReShade: " + (g.reshadeEffects.empty() ? wstring(L"no effects key") : L"effects " + g.reshadeEffects) +
            (g.reshadeOverlay.empty() ? L"" : L", overlay " + g.reshadeOverlay));
    else Out(L"ReShade: not in the game folder");
    Out(L"the game folder's d3d9.dll: " + (g.folderD3d9.empty() ? wstring(L"none") : g.folderD3d9));
    Out(L"[d3d9] chain = " + (g.chain.empty() ? wstring(L"(none)") : g.chain) +
        (g.dxvkHere ? L"; DXVK is in riftstone\\dxvk" : L""));
    if (g.enemyCap)
        Out(wstring(L"enemy_cap: ") + (g.enemyCapOn ? L"on" : L"off") +
            (g.slots >= 0 ? L", " + std::to_wstring(g.slots) + L" enemies at once" : L""));
    else Out(L"enemy_cap: not installed");
    Out(wstring(L"collision_cap: ") + (g.collisionCap ? (g.collisionCapOn ? L"on" : L"off") : L"not installed"));
    return 0;
}

int CmdSetConfig(const wstring& key, const wstring& value) {
    wstring written, error;
    if (!SetGameConfig(key, value, written, error)) return Fail(error);
    Out(GameConfigPath() + L" [GRAPHICS] " + (Lower(key) == L"camerafov" ? L"CameraFov" : L"ViewRange") + L" = " +
        written);
    return 0;
}

int CmdCrowd(const wstring& game, const wstring& n) {
    wchar_t* end = nullptr;
    long v = wcstol(n.c_str(), &end, 10);
    if (n.empty() || !end || *end) return Fail(L"crowd takes a number of enemies, 10 to 64");
    wstring done, error;
    if (!SetEnemyCrowd(game, int(v), done, error)) return Fail(error);
    Out(done);
    return 0;
}

int CmdExportPack(const wstring& game, const wstring& file, const wstring& name) {
    PackReport r;
    wstring error;
    if (!ExportPack(game, name, file, r, error)) return Fail(error);
    Out(L"wrote " + r.out + L": " + std::to_wstring(r.files.size()) + L" files, " + Commas(r.bytes) + L" bytes");
    for (const auto& f : r.files) Out(L"  " + f);
    for (const auto& s : r.leftOut) Out(L"left out: " + s);
    return 0;
}

int CmdLive(const wstring& name, DWORD pid) {
    LiveStats s;
    if (!ReadLive(pid, s, name))
        return Fail(L"no game publishes live stats: it is not running, its loader is older than 0.3, or [live] is off");
    for (const auto& l : DescribeLive(s)) Out(l);
    return 0;
}

int CmdSnapshot(const wstring& name, DWORD pid) {
    if (!pid) {
        LiveStats s;
        if (!ReadLive(0, s, name)) return Fail(L"no running game publishes live stats, so there is none to ask");
        pid = s.pid;
    }
    wstring error;
    if (!RequestSnapshot(pid, error)) return Fail(error);
    Out(L"asked process " + std::to_wstring(pid) +
        L" for a snapshot: the loader writes snapshot-*.txt and its .dmp into riftstone\\logs in a few seconds");
    return 0;
}

int CmdLaunch(const wstring& game) {
    DWORD pid = 0;
    wstring error;
    if (!Launch(game, pid, error)) return Fail(error);
    Out(L"started DDDA.exe (process " + std::to_wstring(pid) + L")");
    return 0;
}

// -- selftest: a stand-in game folder, every part of Studio run against it -------------------------------------------

struct Steps {
    int failed = 0, passed = 0;
    // detail: why it failed (shown only then), or with shown, what was seen either way
    void Check(bool ok, const wstring& what, const wstring& detail = L"", bool shown = false) {
        (ok ? passed : failed)++;
        bool say = !detail.empty() && (shown || !ok);
        Out(wstring(ok ? L"ok    " : L"FAIL  ") + what + (say ? L": " + detail : L""));
    }
};

bool WriteText(const wstring& path, const std::string& text) {
    wstring e;
    return MakeDirs(ParentDir(path)) && WriteFileAtomic(path, text, e);
}

std::string ReadText(const wstring& path) {
    std::string s;
    ReadFileBytes(path, s);
    return s;
}

void RemoveTree(const wstring& dir) {
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(JoinPath(dir, L"*").c_str(), &fd);
    if (h != INVALID_HANDLE_VALUE) {
        do {
            wstring n = fd.cFileName;
            if (n == L"." || n == L"..") continue;
            wstring p = JoinPath(dir, n);
            if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) RemoveTree(p);
            else DeleteFileW(p.c_str());
        } while (FindNextFileW(h, &fd));
        FindClose(h);
    }
    RemoveDirectoryW(dir.c_str());
}

int Selftest(HINSTANCE inst, const wstring& standIn, bool keep) {
    Steps st;
    if (GameRunning())
        return Fail(wstring(kGameTitle) + L" is running: the selftest works in a stand-in folder, but Studio refuses "
                    L"to switch features while any DDDA.exe runs; close the game and run it again");
    wchar_t tmp[MAX_PATH];
    GetTempPathW(MAX_PATH, tmp);
    wstring root = JoinPath(tmp, L"RiftstoneStudio-selftest-" + std::to_wstring(GetCurrentProcessId()));
    RemoveTree(root);
    wstring game = JoinPath(root, L"DDDA");
    MakeDirs(JoinPath(game, L"nativePC\\rom"));
    wstring exe = standIn.empty() ? JoinPath(ExeDir(), L"fake_ddda.exe") : standIn;
    bool haveStandIn = FileExists(exe);
    if (haveStandIn) CopyFileW(exe.c_str(), JoinPath(game, L"DDDA.exe").c_str(), FALSE);
    else WriteText(JoinPath(game, L"DDDA.exe"), "not a program");
    WriteText(JoinPath(game, L"riftstone\\plugins\\enemy_cap.dll"), "stand-in");
    WriteText(JoinPath(game, L"riftstone\\plugins\\enemy_cap.ini"),
              "; Riftstone enemy cap\r\n[enemy_cap]\r\n; how many enemies at once (10 to 64)\r\nslots = 30\r\n");
    WriteText(JoinPath(game, L"riftstone\\plugins\\off\\free_sprint.dll"), "stand-in");
    WriteText(JoinPath(game, L"riftstone\\plugins\\off\\free_sprint.ini"), "[sprint]\nmode = out_of_battle\nwho = party\n");
    WriteText(JoinPath(game, L"riftstone_loader.ini"), "[overlay]\r\nenabled = 1\r\nkey = Insert\r\n");
    WriteText(JoinPath(game, L"riftstone\\runtime-state.ini"), "[safe_mode]\r\non=1\r\n[session]\r\nearly_crashes=2\r\n");

    Found f = FindGame(game, ExeDir());
    st.Check(f.root == NormalPath(game), L"the stand-in game folder is found", f.root, true);

    auto feats = ListFeatures(game);
    st.Check(feats.size() == 2 && feats[0].stem == L"enemy_cap" && feats[0].on && feats[1].stem == L"free_sprint" &&
                 !feats[1].on,
             L"features listed: enemy_cap on, free_sprint off");

    wstring clean, error;
    st.Check(Validate(L"enemy_cap", L"enemy_cap", L"slots", L" 64 ", clean, error) && clean == L"64",
             L"enemy_cap slots 64 is allowed");
    st.Check(!Validate(L"enemy_cap", L"enemy_cap", L"slots", L"65", clean, error) &&
                 error == L"slots is a number from 10 to 64",
             L"enemy_cap slots 65 is refused with the toolkit's words", error);

    wstring capIni = JoinPath(game, L"riftstone\\plugins\\enemy_cap.ini");
    st.Check(WriteSetting(capIni, L"enemy_cap", L"slots", L"64", error) &&
                 ReadText(capIni) ==
                     "; Riftstone enemy cap\r\n[enemy_cap]\r\n; how many enemies at once (10 to 64)\r\nslots = 64\r\n",
             L"a setting is written with every other byte kept", error);
    std::vector<Setting> s;
    st.Check(ReadSettings(capIni, s) && s.size() == 1 && s[0].help == L"how many enemies at once (10 to 64)",
             L"a setting's help is the comment above it");

    bool changed = false;
    st.Check(Toggle(game, L"free_sprint", true, changed, error) && changed &&
                 FileExists(JoinPath(game, L"riftstone\\plugins\\free_sprint.dll")) &&
                 FileExists(JoinPath(game, L"riftstone\\plugins\\free_sprint.ini")),
             L"free_sprint switched on with its settings file", error);

    // The window, never shown: the smoke clicks through the pages and exports a profile from the Profiles page.
    wstring guiProfile = JoinPath(root, L"from-the-window.json"), log;
    int gui = RunGuiSmoke(inst, game, guiProfile, log);
    st.Check(gui == 0, L"the window: pages, a switch, a slider and Export clicked through", log);
    std::string raw = ReadText(guiProfile);
    Profile fromGui;
    bool parsed = ProfileFromJson(raw, fromGui, error);
    bool capOff = false;
    for (const auto& kv : fromGui.features)
        if (kv.first == L"enemy_cap") capOff = !kv.second;
    st.Check(parsed && capOff, L"the profile the window exported reads back, with the switch it clicked", error);

    Profile p;
    st.Check(ProfileFromGame(game, L"Selftest", p, error) && p.features.size() == 2, L"a profile is exported", error);
    std::string json = ProfileToJson(p);
    Profile again;
    st.Check(ProfileFromJson(json, again, error) && ProfileToJson(again) == json, L"the profile reads back unchanged",
             error);

    // Import a changed copy: the dry run changes nothing, then the import does what it planned.
    for (auto& kv : again.features) kv.second = kv.first == L"enemy_cap";
    for (auto& stem : again.settings)
        for (auto& sec : stem.sections)
            for (auto& kv : sec.values)
                if (kv.first == L"slots") kv.second = L"48";
    auto iniOf = [&](const wchar_t* stem) {
        for (const auto& x : ListFeatures(game))
            if (x.stem == stem) return x.ini();
        return wstring();
    };
    wstring capNow = iniOf(L"enemy_cap");
    std::string before = ReadText(capNow);
    ApplyReport r;
    bool dry = ApplyProfile(game, again, true, r, error);
    st.Check(dry && r.switched == 2 && r.writes.size() == 1 && r.writes[0].value == L"48" && !before.empty() &&
                 ReadText(capNow) == before &&
                 FileExists(JoinPath(game, L"riftstone\\plugins\\free_sprint.dll")),
             L"importing a profile: the dry run plans and changes nothing", error);
    bool applied = ApplyProfile(game, again, false, r, error);
    std::vector<Setting> after;
    bool movedIni = ReadSettings(JoinPath(game, L"riftstone\\plugins\\enemy_cap.ini"), after);
    st.Check(applied && r.switched == 2 && FileExists(JoinPath(game, L"riftstone\\plugins\\off\\free_sprint.dll")) &&
                 movedIni && !after.empty() && after[0].value == L"48",
             L"importing a profile: features switched and the value written", error);

    st.Check(EndSafeMode(game, error) && ReadSession(game).safeMode == false, L"safe mode turned off", error);

    if (!haveStandIn) {
        Out(L"skip  launching the game: no stand-in (fake_ddda.exe) next to Studio");
    } else {
        DWORD pid = 0;
        bool started = Launch(game, pid, error);
        HANDLE h = started ? OpenProcess(SYNCHRONIZE, FALSE, pid) : nullptr;
        bool exited = h && WaitForSingleObject(h, 15000) == WAIT_OBJECT_0;
        if (h) CloseHandle(h);
        // a stand-in that already ended has no process left to open: then it is done once nothing named DDDA.exe runs
        for (int i = 0; started && !h && !exited && i < 150; i++) {
            exited = !GameRunning() && FileExists(JoinPath(game, L"stand-in-started.txt"));
            if (!exited) Sleep(100);
        }
        std::string marker = ReadText(JoinPath(game, L"stand-in-started.txt"));
        st.Check(started && exited && FromUtf8(marker) == NormalPath(game),
                 L"Launch starts DDDA.exe in its own folder", started ? FromUtf8(marker) : error, true);
    }
    if (!keep) RemoveTree(root);
    Out(std::to_wstring(st.passed) + L" passed, " + std::to_wstring(st.failed) + L" failed");
    return st.failed ? 1 : 0;
}

void Usage() {
    Out(L"Riftstone Studio " + Widen(kVersion) + L"\n"
        L"  RiftstoneStudio.exe                       the window\n"
        L"  RiftstoneStudio.exe [--game DIR] COMMAND\n"
        L"    find-game                               where Dark Arisen is\n"
        L"    list                                    the features and whether each is on\n"
        L"    on NAME|all, off NAME|all               switch features (the game closed)\n"
        L"    settings NAME                           a feature's settings (NAME loader: the loader's)\n"
        L"    set NAME SECTION KEY VALUE              change one, checked as the toolkit checks it\n"
        L"    export-profile FILE [--name NAME]       the features and settings, as a profile\n"
        L"    import-profile FILE [--dry-run]         put a profile into the game folder\n"
        L"    launch                                  start the game\n"
        L"    graphics                                config.ini's view, ENB, ReShade, DXVK and the enemy crowd\n"
        L"    set-config CameraFov|ViewRange VALUE    the game's own setting (0..15; NORMAL, FAR, FARTHEST)\n"
        L"    enb on|off, dxvk on|off                 ENB's effects; the loader's DXVK chain\n"
        L"    crowd N                                 enemies at once (10..64; past 40 collision_cap too)\n"
        L"    export-pack FILE [--name NAME]          this folder's loader, plugins and settings as a zip\n"
        L"    live [--pid N], snapshot [--pid N]      the running game's live stats; a snapshot of it\n"
        L"    safe-mode-off                           let the loader run its features again\n"
        L"    selftest [--stand-in EXE] [--keep]      every part of Studio on a stand-in game folder\n"
        L"    --version");
}

}  // namespace

int WINAPI wWinMain(HINSTANCE inst, HINSTANCE, PWSTR, int show) {
    int argc = 0;
    wchar_t** argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    std::vector<wstring> args(argv + 1, argv + argc);
    LocalFree(argv);
    wstring given;
    for (size_t i = 0; i + 1 < args.size(); i++)
        if (args[i] == L"--game") {
            given = args[i + 1];
            args.erase(args.begin() + long(i), args.begin() + long(i) + 2);
            break;
        }
    if (args.empty()) {
        wstring game, error;
        ResolveGame(given, game, error);
        return RunStudioWindow(inst, game, show);
    }
    InitStd();
    const wstring& cmd = args[0];
    auto opt = [&](const wchar_t* name) {
        for (size_t i = 1; i + 1 < args.size(); i++)
            if (args[i] == name) return args[i + 1];
        return wstring();
    };
    auto flag = [&](const wchar_t* name) {
        for (size_t i = 1; i < args.size(); i++)
            if (args[i] == name) return true;
        return false;
    };
    if (cmd == L"--version" || cmd == L"version") {
        Out(L"Riftstone Studio " + Widen(kVersion));
        return 0;
    }
    if (cmd == L"--help" || cmd == L"-h" || cmd == L"help" || cmd == L"/?") {
        Usage();
        return 0;
    }
    if (cmd == L"batch") return Batch();
    if (cmd == L"selftest") return Selftest(inst, opt(L"--stand-in"), flag(L"--keep"));
    wstring game, error;
    if (cmd == L"--gui-smoke") {
        if (given.empty() || args.size() < 2) return Fail(L"--gui-smoke needs --game DIR and a profile path");
        wstring log;
        int rc = RunGuiSmoke(inst, given, args[1], log);
        Out(log);
        return rc;
    }
    if (cmd == L"--screenshot") {
        if (args.size() < 3) return Fail(L"--screenshot FILE PAGE [--size WxH] [--dpi N]");
        ResolveGame(given, game, error);   // none found: the page as Studio shows it then
        int w = 1180, h = 760, dpi = 96;
        wstring size = opt(L"--size");
        if (!size.empty()) swscanf_s(size.c_str(), L"%dx%d", &w, &h);
        wstring d = opt(L"--dpi");
        if (!d.empty()) dpi = _wtoi(d.c_str());
        wstring log;
        int rc = RenderPage(inst, game, args[2], args[1], w, h, dpi, log);
        if (!log.empty()) Out(log);
        return rc;
    }
    if (cmd == L"live" || cmd == L"snapshot") {
        DWORD pid = DWORD(_wtoi(opt(L"--pid").c_str()));
        return cmd == L"live" ? CmdLive(opt(L"--name"), pid) : CmdSnapshot(opt(L"--name"), pid);
    }
    if (cmd == L"set-config" && args.size() == 3) return CmdSetConfig(args[1], args[2]);
    if (!ResolveGame(given, game, error)) return Fail(error);
    if (cmd == L"find-game") {
        Out(game);
        return 0;
    }
    if (cmd == L"list") return CmdList(game);
    if ((cmd == L"on" || cmd == L"off") && args.size() == 2) return CmdToggle(game, args[1], cmd == L"on");
    if (cmd == L"settings" && args.size() == 2) return CmdSettings(game, args[1]);
    if (cmd == L"set" && args.size() == 5) return CmdSet(game, args[1], args[2], args[3], args[4]);
    if (cmd == L"export-profile" && args.size() >= 2) return CmdExport(game, args[1], opt(L"--name"));
    if (cmd == L"import-profile" && args.size() >= 2) return CmdImport(game, args[1], flag(L"--dry-run"));
    if (cmd == L"launch") return CmdLaunch(game);
    if (cmd == L"graphics") return CmdGraphics(game);
    if ((cmd == L"enb" || cmd == L"dxvk") && args.size() == 2 && (args[1] == L"on" || args[1] == L"off")) {
        bool on = args[1] == L"on";
        if (!(cmd == L"enb" ? SetEnbEffect(game, on, error) : SetDxvkChain(game, on, error))) return Fail(error);
        Out(cmd == L"enb" ? (on ? L"ENB's effects on (enbseries.ini [GLOBAL] UseEffect = true)"
                                : L"ENB's effects off (enbseries.ini [GLOBAL] UseEffect = false)")
                          : (on ? L"[d3d9] chain = riftstone\\dxvk\\d3d9.dll: DXVK draws at the next start"
                                : L"[d3d9] chain cleared: Windows' Direct3D 9 at the next start"));
        return 0;
    }
    if (cmd == L"crowd" && args.size() == 2) return CmdCrowd(game, args[1]);
    if (cmd == L"export-pack" && args.size() >= 2) return CmdExportPack(game, args[1], opt(L"--name"));
    if (cmd == L"safe-mode-off") {
        if (!EndSafeMode(game, error)) return Fail(error);
        Out(L"safe mode is off: the loader runs its features at the next start");
        return 0;
    }
    Usage();
    return 2;
}

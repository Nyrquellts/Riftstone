// Riftstone Studio's Graphics page: the game's own config.ini (CameraFov, ViewRange), ENB's and ReShade's switches,
// DXVK behind the loader's [d3d9] chain, and how many enemies enemy_cap lets the game hold at once. A file is
// changed the way src/riftstone/graphics.py set_ini changes it (every byte kept but the value's), read one byte a
// character as graphics.decode_ini reads a file without a UTF-16 mark; native/studio/test/run_tests.py compares the
// two on the same texts.
#include "studio.h"

#include <cmath>
#include <set>

namespace studio {

namespace {

bool LineBreak(wchar_t c) {   // str.splitlines' line boundaries
    return c == L'\n' || c == L'\r' || c == 0x0B || c == 0x0C || c == 0x1C || c == 0x1D || c == 0x1E || c == 0x85 ||
           c == 0x2028 || c == 0x2029;
}

std::vector<wstring> SplitLinesKeep(const wstring& t) {   // str.splitlines(keepends=True)
    std::vector<wstring> out;
    size_t i = 0, n = t.size();
    while (i < n) {
        size_t j = i;
        while (j < n && !LineBreak(t[j])) j++;
        if (j < n) j += (t[j] == L'\r' && j + 1 < n && t[j + 1] == L'\n') ? 2 : 1;
        out.push_back(t.substr(i, j - i));
        i = j;
    }
    return out;
}

wstring RStripNewlines(const wstring& s) {   // str.rstrip("\r\n")
    size_t b = s.size();
    while (b > 0 && (s[b - 1] == L'\r' || s[b - 1] == L'\n')) b--;
    return s.substr(0, b);
}

wstring LStripSpace(const wstring& s) {   // str.lstrip()
    size_t a = 0;
    while (a < s.size() && IsPySpace(s[a])) a++;
    return s.substr(a);
}

bool IsSectionLine(const wstring& line) {   // line.startswith("[") and line.endswith("]")
    return !line.empty() && line.front() == L'[' && line.back() == L']';
}

struct WantKey {
    wstring key, value;
};
struct WantSection {
    wstring name;
    std::vector<WantKey> keys;
};

WantSection* FindSection(std::vector<WantSection>& want, const wstring& name) {
    for (auto& s : want)
        if (s.name == name) return &s;
    return nullptr;
}

WantKey* FindKey(WantSection& s, const wstring& key) {
    for (auto& k : s.keys)
        if (k.key == key) return &k;
    return nullptr;
}

wstring Fixed6(double v) {
    wchar_t buf[64];
    swprintf_s(buf, L"%.6f", v);
    return buf;
}

bool WriteOneByteIni(const wstring& path, const wstring& text, wstring& error) {
    std::string raw;
    raw.reserve(text.size());
    for (wchar_t c : text) {
        if (c > 0xFF) {
            error = L"a value does not fit " + FileName(path) + L"'s encoding (one byte a character)";
            return false;
        }
        raw += char(uint8_t(c));
    }
    return WriteFileAtomic(path, raw, error);
}

// One key of a one-byte ini set in place; the value it had in old.
bool SetIniFile(const wstring& path, const wstring& section, const wstring& key, const wstring& value, wstring& old,
                wstring& error) {
    wstring text, out;
    if (!ReadOneByteIni(path, text, error)) return false;
    std::vector<IniValue> before;
    if (!SetIniText(text, {{section, key, value}}, path, out, before, error)) return false;
    old = before.empty() ? L"" : before[0].value;
    return out == text || WriteOneByteIni(path, out, error);
}

const wchar_t* Running() { return L" is running"; }

int FirstNumber(const wstring& s) {   // ReShade's "145,0,0,0" and ENB's "123": the key's code
    wstring t = Trim(s);
    size_t comma = t.find(L',');
    if (comma != wstring::npos) t = Trim(t.substr(0, comma));
    if (t.empty() || t.size() > 4) return -1;
    for (wchar_t c : t)
        if (c < L'0' || c > L'9') return -1;
    return _wtoi(t.c_str());
}

wstring ReshadeKey(const wstring& value) {   // "145,0,0,0": the key, then ctrl, shift, alt
    int vk = FirstNumber(value);
    if (vk <= 0) return L"";
    std::vector<int> parts;
    size_t a = 0;
    wstring t = Trim(value);
    for (;;) {
        size_t b = t.find(L',', a);
        parts.push_back(_wtoi(Trim(t.substr(a, b == wstring::npos ? wstring::npos : b - a)).c_str()));
        if (b == wstring::npos) break;
        a = b + 1;
    }
    wstring o;
    if (parts.size() > 1 && parts[1]) o += L"Ctrl+";
    if (parts.size() > 2 && parts[2]) o += L"Shift+";
    if (parts.size() > 3 && parts[3]) o += L"Alt+";
    return o + KeyName(vk);
}

}  // namespace

IniMap IniValues(const wstring& text) {
    IniMap out;
    int current = -1;
    std::set<wstring> seen;
    for (const auto& raw : SplitLinesKeep(text)) {
        wstring line = Trim(raw);
        if (line.empty() || line[0] == L';' || line[0] == L'#') continue;
        if (IsSectionLine(line)) {
            wstring sec = Lower(Trim(line.substr(1, line.size() - 2)));
            if (seen.count(sec)) current = -1;
            else {
                current = -1;
                for (size_t i = 0; i < out.size(); i++)
                    if (out[i].name == sec) current = int(i);
                if (current < 0) {
                    out.push_back({sec, {}});
                    current = int(out.size()) - 1;
                }
            }
            seen.insert(sec);
        } else if (line.find(L'=') != wstring::npos && current >= 0) {
            size_t eq = line.find(L'=');
            wstring k = Lower(Trim(line.substr(0, eq))), v = Trim(line.substr(eq + 1));
            auto& vals = out[size_t(current)].values;
            bool have = false;
            for (const auto& kv : vals)
                if (kv.first == k) have = true;
            if (!have) vals.push_back({k, v});
        }
    }
    return out;
}

wstring IniLookup(const IniMap& m, const wstring& section, const wstring& key) {
    wstring s = Lower(section), k = Lower(key);
    for (const auto& sec : m)
        if (sec.name == s)
            for (const auto& kv : sec.values)
                if (kv.first == k) return kv.second;
    return L"";
}

bool SetIniText(const wstring& text, const std::vector<IniValue>& values, const wstring& what, wstring& out,
                std::vector<IniValue>& before, wstring& error) {
    // want = {s.lower(): {k.lower(): (k, v)}}: a section spelt again after another starts its keys over
    std::vector<WantSection> want;
    wstring spelt;
    bool first = true;
    WantSection* cur = nullptr;
    for (const auto& v : values) {
        if (first || v.section != spelt) {
            first = false;
            spelt = v.section;
            wstring name = Lower(v.section);
            cur = FindSection(want, name);
            if (cur) cur->keys.clear();
            else {
                want.push_back({name, {}});
                cur = &want.back();
            }
        }
        wstring key = Lower(v.key);
        if (WantKey* k = FindKey(*cur, key)) k->value = v.value;
        else cur->keys.push_back({key, v.value});
    }
    before.clear();
    std::vector<wstring> lines = SplitLinesKeep(text);
    std::set<std::pair<wstring, wstring>> done;
    std::set<wstring> seen;
    bool inSection = false;
    wstring section;
    for (auto& raw : lines) {
        wstring body = RStripNewlines(raw);
        wstring line = Trim(body);
        if (IsSectionLine(line)) {
            wstring name = Lower(Trim(line.substr(1, line.size() - 2)));
            inSection = !seen.count(name);
            section = name;
            seen.insert(name);
            continue;
        }
        WantSection* ws = inSection ? FindSection(want, section) : nullptr;
        if (!ws || line.empty() || line[0] == L';' || line[0] == L'#' || line.find(L'=') == wstring::npos) continue;
        size_t eq = body.find(L'=');
        wstring keyText = body.substr(0, eq), rest = body.substr(eq + 1);
        wstring key = Lower(Trim(keyText));
        WantKey* wk = FindKey(*ws, key);
        if (!wk || done.count({section, key})) continue;
        done.insert({section, key});
        wstring spaces = rest.substr(0, rest.size() - LStripSpace(rest).size());
        before.push_back({section, key, Trim(rest)});
        raw = keyText + L"=" + spaces + wk->value + raw.substr(body.size());
    }
    // before.setdefault(section, {})[key]: grouped by section in the order each section was first set
    std::vector<IniValue> grouped;
    for (const auto& b : before) {
        bool placed = false;
        for (const auto& g : grouped)
            if (g.section == b.section) placed = true;
        if (placed) continue;
        for (const auto& c : before)
            if (c.section == b.section) grouped.push_back(c);
    }
    before = grouped;
    wstring missing;
    for (const auto& s : want)
        for (const auto& k : s.keys)
            if (!done.count({s.name, k.key})) missing += (missing.empty() ? L"" : L", ") + (L"[" + s.name + L"] " + k.key);
    if (!missing.empty()) {
        error = what + L" has no " + missing + L" (a profile sets only keys the file already has)";
        return false;
    }
    out.clear();
    for (const auto& l : lines) out += l;
    return true;
}

bool ReadOneByteIni(const wstring& path, wstring& text, wstring& error) {
    std::string raw;
    if (!ReadFileBytes(path, raw, 16u << 20)) {
        error = L"cannot read " + path;
        return false;
    }
    if (raw.size() >= 2 && ((uint8_t(raw[0]) == 0xFF && uint8_t(raw[1]) == 0xFE) ||
                            (uint8_t(raw[0]) == 0xFE && uint8_t(raw[1]) == 0xFF))) {
        error = FileName(path) + L" is UTF-16: Studio changes only files of one byte a character, as the game and "
                                 L"ENB write them";
        return false;
    }
    text.clear();
    text.reserve(raw.size());
    for (char c : raw) text += wchar_t(uint8_t(c));
    return true;
}

wstring GameConfigPath() {
    wchar_t local[MAX_PATH];
    DWORD n = GetEnvironmentVariableW(L"LOCALAPPDATA", local, MAX_PATH);
    if (!n || n >= MAX_PATH) return L"";
    return JoinPath(JoinPath(JoinPath(local, L"CAPCOM"), L"DRAGONS DOGMA DARK ARISEN"), L"config.ini");
}

wstring KeyName(int vk) {
    if (vk >= 0x70 && vk <= 0x87) return L"F" + std::to_wstring(vk - 0x6F);
    if ((vk >= '0' && vk <= '9') || (vk >= 'A' && vk <= 'Z')) return wstring(1, wchar_t(vk));
    if (vk >= 0x60 && vk <= 0x69) return L"Num " + std::to_wstring(vk - 0x60);
    switch (vk) {
    case 0x08: return L"Backspace";
    case 0x09: return L"Tab";
    case 0x0D: return L"Enter";
    case 0x10: return L"Shift";
    case 0x11: return L"Ctrl";
    case 0x12: return L"Alt";
    case 0x13: return L"Pause";
    case 0x14: return L"Caps Lock";
    case 0x1B: return L"Esc";
    case 0x20: return L"Space";
    case 0x21: return L"Page Up";
    case 0x22: return L"Page Down";
    case 0x23: return L"End";
    case 0x24: return L"Home";
    case 0x25: return L"Left";
    case 0x26: return L"Up";
    case 0x27: return L"Right";
    case 0x28: return L"Down";
    case 0x2C: return L"Print Screen";
    case 0x2D: return L"Insert";
    case 0x2E: return L"Delete";
    case 0x6A: return L"Num *";
    case 0x6B: return L"Num +";
    case 0x6D: return L"Num -";
    case 0x6E: return L"Num .";
    case 0x6F: return L"Num /";
    case 0x90: return L"Num Lock";
    case 0x91: return L"Scroll Lock";
    case 0xC0: return L"`";
    }
    return L"key " + std::to_wstring(vk);
}

GraphicsInfo ReadGraphics(const wstring& game) {
    GraphicsInfo g;
    g.config = GameConfigPath();
    wstring text, error;
    if (!g.config.empty() && FileExists(g.config)) {
        if (ReadOneByteIni(g.config, text, error)) {
            g.configFound = true;
            IniMap m = IniValues(text);
            g.fov = IniLookup(m, L"graphics", L"camerafov");
            g.viewRange = IniLookup(m, L"graphics", L"viewrange");
        } else g.configProblem = error;
    }
    if (game.empty()) return g;
    wstring series = JoinPath(game, L"enbseries.ini"), local = JoinPath(game, L"enblocal.ini");
    g.enb = FileExists(series) || FileExists(local);
    if (FileExists(series) && ReadOneByteIni(series, text, error))
        g.enbEffect = IniLookup(IniValues(text), L"global", L"useeffect");
    if (FileExists(local) && ReadOneByteIni(local, text, error)) {
        IniMap m = IniValues(text);
        int use = FirstNumber(IniLookup(m, L"input", L"keyuseeffect"));
        int with = FirstNumber(IniLookup(m, L"input", L"keycombination"));
        if (use > 0) g.enbKeys = (with > 0 ? KeyName(with) + L"+" : L"") + KeyName(use);
        if (Lower(IniLookup(m, L"proxy", L"enableproxylibrary")) == L"true")
            g.enbProxy = IniLookup(m, L"proxy", L"proxylibrary");
    }
    wstring reshade = JoinPath(game, L"ReShade.ini");
    g.reshade = FileExists(reshade);
    if (g.reshade && ReadOneByteIni(reshade, text, error)) {
        IniMap m = IniValues(text);
        g.reshadeEffects = ReshadeKey(IniLookup(m, L"input", L"keyeffects"));
        g.reshadeOverlay = ReshadeKey(IniLookup(m, L"input", L"keyoverlay"));
    }
    wstring folderDll = JoinPath(game, L"d3d9.dll");
    if (FileExists(folderDll)) {
        std::string head;
        ReadFileBytes(folderDll, head, 64u << 20);
        if (g.enb) g.folderD3d9 = L"ENB";
        else if (g.reshade) g.folderD3d9 = L"ReShade";
        else if (head.find("DXVK_CONFIG_FILE") != std::string::npos) g.folderD3d9 = L"DXVK";
        else g.folderD3d9 = L"a Direct3D 9 wrapper";
    }
    g.dxvkHere = FileExists(JoinPath(game, D3d9ChainPath()));
    std::vector<Setting> loader;
    g.loaderIni = ReadSettings(LoaderIni(game), loader);
    for (const auto& s : loader)
        if (Lower(s.section) == L"d3d9" && Lower(s.key) == L"chain") {
            wstring v = s.value;
            size_t semi = v.find(L';');
            g.chain = Trim(semi == wstring::npos ? v : v.substr(0, semi));
            break;
        }
    for (const auto& f : ListFeatures(game)) {
        wstring s = Lower(f.stem);
        if (s == L"enemy_cap") {
            g.enemyCap = true;
            g.enemyCapOn = f.on;
            std::vector<Setting> set;
            if (ReadSettings(f.ini(), set))
                for (const auto& v : set)
                    if (Lower(v.key) == L"slots") {
                        g.slots = _wtoi(Trim(v.value).c_str());
                        break;
                    }
        } else if (s == L"collision_cap") {
            g.collisionCap = true;
            g.collisionCapOn = f.on;
        }
    }
    return g;
}

bool SetGameConfig(const wstring& key, const wstring& value, wstring& written, wstring& error) {
    if (GameRunning()) {
        error = wstring(kGameTitle) + Running() + L": it writes config.ini when it closes, over any change, so close "
                                                  L"the game first";
        return false;
    }
    wstring path = GameConfigPath();
    if (path.empty()) {
        error = L"LOCALAPPDATA is not set, so the game's config.ini cannot be found";
        return false;
    }
    if (!FileExists(path)) {
        error = L"no config.ini at " + path + L": start the game once and it writes its settings there";
        return false;
    }
    wstring k = Lower(key), v = Trim(value), name;
    if (k == L"camerafov") {
        name = L"CameraFov";
        wchar_t* end = nullptr;
        double d = v.empty() ? NAN : wcstod(v.c_str(), &end);
        if (!std::isfinite(d) || !end || *end || d < 0 || d > 15) {
            error = L"[GRAPHICS] CameraFov = " + v + L": the game reads 0 to 15";
            return false;
        }
        v = Fixed6(d);
    } else if (k == L"viewrange") {
        name = L"ViewRange";
        wstring u;
        for (wchar_t c : v) u += (c >= L'a' && c <= L'z') ? wchar_t(c - 32) : c;
        if (u != L"NORMAL" && u != L"FAR" && u != L"FARTHEST") {
            error = L"config: [GRAPHICS] ViewRange = " + v + L": the game reads only NORMAL, FAR, FARTHEST";
            return false;
        }
        v = u;
    } else {
        error = L"Studio sets only CameraFov and ViewRange in config.ini";
        return false;
    }
    wstring old;
    if (!SetIniFile(path, L"GRAPHICS", name, v, old, error)) return false;
    written = v;
    return true;
}

bool SetEnbEffect(const wstring& game, bool on, wstring& error) {
    if (GameRunning()) {
        error = wstring(kGameTitle) + Running() + L": ENB's editor writes enbseries.ini while it runs, so close the "
                                                  L"game first (in game, ENB's own key turns the effects on and off)";
        return false;
    }
    wstring path = JoinPath(game, L"enbseries.ini"), old;
    if (!FileExists(path)) {
        error = L"no enbseries.ini in " + game + L": ENB is not installed there";
        return false;
    }
    return SetIniFile(path, L"GLOBAL", L"UseEffect", on ? L"true" : L"false", old, error);
}

bool SetDxvkChain(const wstring& game, bool on, wstring& error) {
    if (GameRunning()) {
        error = wstring(kGameTitle) + Running() + L": the loader chains Direct3D 9 when the game starts, so close the "
                                                  L"game first";
        return false;
    }
    wstring value = on ? D3d9ChainPath() : L"";
    if (on && !FileExists(JoinPath(game, D3d9ChainPath()))) {
        error = wstring(D3d9ChainPath()) + L" does not exist: 'Riftstone.cmd loader d3d9 add <DXVK release>' puts "
                                           L"DXVK's 32-bit d3d9.dll there";
        return false;
    }
    if (on && FileExists(JoinPath(game, L"d3d9.dll"))) {
        error = wstring(D3d9ChainPath()) + L" is not loaded: the game folder's own d3d9.dll stays in charge (take "
                                           L"one of the two out)";
        return false;
    }
    // Off empties the line as loader.d3d9_remove does (an empty chain is no DLL, which the value check refuses).
    wstring clean;
    if (on && !Validate(kLoaderStem, L"d3d9", L"chain", value, clean, error)) return false;
    return WriteSetting(LoaderIni(game), L"d3d9", L"chain", clean, error);
}

bool SetEnemyCrowd(const wstring& game, int slots, wstring& done, wstring& error) {
    const Feature* cap = nullptr;
    const Feature* guard = nullptr;
    auto feats = ListFeatures(game);
    for (const auto& f : feats) {
        if (Lower(f.stem) == L"enemy_cap") cap = &f;
        if (Lower(f.stem) == L"collision_cap") guard = &f;
    }
    if (!cap) {
        error = L"enemy_cap is not installed: it comes with Riftstone's player zip (riftstone\\plugins\\off)";
        return false;
    }
    std::vector<Setting> set;
    wstring section;
    if (ReadSettings(cap->ini(), set))
        for (const auto& v : set)
            if (Lower(v.key) == L"slots") {
                section = v.section;
                break;
            }
    if (section.empty()) {
        error = FileName(cap->ini()) + L" has no slots setting";
        return false;
    }
    wstring clean;
    if (!Validate(cap->stem, section, L"slots", std::to_wstring(slots), clean, error)) return false;
    bool wantGuard = slots > 40 && guard && !guard->on;   // a crowd is what overran the 800 hit shapes
    if ((!cap->on || wantGuard) && GameRunning()) {
        error = wstring(kGameTitle) + Running() + L": plugins load at start, so close the game first";
        return false;
    }
    bool changed = false;
    wstring stem = cap->stem, ini = cap->ini();
    if (!cap->on) {
        if (!Toggle(game, stem, true, changed, error)) return false;
        for (const auto& f : ListFeatures(game))   // its .ini moved with it into riftstone\plugins
            if (Lower(f.stem) == L"enemy_cap") ini = f.ini();
    }
    if (wantGuard && !Toggle(game, guard->stem, true, changed, error)) return false;
    if (!WriteSetting(ini, section, L"slots", clean, error)) return false;
    done = L"enemy_cap on, " + clean + L" enemies at once";
    if (wantGuard) done += L"; collision_cap on (the crash guard for crowds)";
    else if (slots > 40 && !guard) done += L" (collision_cap, the crash guard for crowds, is not installed)";
    done += L". It takes effect at the game's next start.";
    return true;
}

}  // namespace studio

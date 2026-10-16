// Riftstone Studio's mod pack: this game folder's Riftstone setup as one zip another player unzips into their game
// folder -- the loader and its settings, Riftstone's own features where they are now (on in riftstone\plugins, off in
// riftstone\plugins\off) with their settings, the same switches and values as a profile, a README and
// riftstone/package.json, laid out as src/riftstone/package.py build_plugins lays out the player zip. Never the
// game's files, mods, logs, saves, DXVK, ENB, other authors' plugins or the experimental compat layer: those are
// named in the README instead. Every member goes through the checks src/riftstone/ipaudit.py makes (a game file by
// its magic or name, Capcom's copyright notation, links to file hosts) before it is written.
#include "studio.h"
#include "version.h"

#include <bcrypt.h>

#include <algorithm>
#include <map>

namespace studio {

namespace {

const uint32_t kCrcPoly = 0xEDB88320u;

struct Member {
    std::string name;   // UTF-8, '/'-separated
    std::string data;
};

void Put16(std::string& o, uint32_t v) {
    o += char(v & 0xFF);
    o += char((v >> 8) & 0xFF);
}

void Put32(std::string& o, uint32_t v) {
    Put16(o, v & 0xFFFF);
    Put16(o, v >> 16);
}

// A zip of stored members (method 0), names in UTF-8 (flag bit 11), the time each was written.
bool ZipStored(const std::vector<Member>& members, std::string& zip, wstring& error) {
    SYSTEMTIME t;
    GetLocalTime(&t);
    uint32_t dosTime = uint32_t(t.wHour) << 11 | uint32_t(t.wMinute) << 5 | uint32_t(t.wSecond / 2);
    uint32_t dosDate = uint32_t(std::max<int>(t.wYear - 1980, 0)) << 9 | uint32_t(t.wMonth) << 5 | uint32_t(t.wDay);
    std::string central;
    zip.clear();
    if (members.size() > 0xFFFF) {
        error = L"too many files for one zip";
        return false;
    }
    for (const auto& m : members) {
        if (m.data.size() >= 0xFFFFFFFFull || zip.size() >= 0xFFFFFFFFull - m.data.size() - 4096 ||
            m.name.size() > 0xFFFF) {
            error = L"the pack would be larger than a zip without its 64-bit extension holds";
            return false;
        }
        uint32_t crc = Crc32(m.data), size = uint32_t(m.data.size()), offset = uint32_t(zip.size());
        Put32(zip, 0x04034B50);
        Put16(zip, 20);
        Put16(zip, 0x0800);
        Put16(zip, 0);
        Put16(zip, dosTime);
        Put16(zip, dosDate);
        Put32(zip, crc);
        Put32(zip, size);
        Put32(zip, size);
        Put16(zip, uint32_t(m.name.size()));
        Put16(zip, 0);
        zip += m.name;
        zip += m.data;
        Put32(central, 0x02014B50);
        Put16(central, 20);
        Put16(central, 20);
        Put16(central, 0x0800);
        Put16(central, 0);
        Put16(central, dosTime);
        Put16(central, dosDate);
        Put32(central, crc);
        Put32(central, size);
        Put32(central, size);
        Put16(central, uint32_t(m.name.size()));
        Put16(central, 0);
        Put16(central, 0);
        Put16(central, 0);
        Put16(central, 0);
        Put32(central, 0);
        Put32(central, offset);
        central += m.name;
    }
    uint32_t at = uint32_t(zip.size());
    zip += central;
    Put32(zip, 0x06054B50);
    Put16(zip, 0);
    Put16(zip, 0);
    Put16(zip, uint32_t(members.size()));
    Put16(zip, uint32_t(members.size()));
    Put32(zip, uint32_t(central.size()));
    Put32(zip, at);
    Put16(zip, 0);
    return true;
}

std::string LowerA(std::string s) {
    for (auto& c : s)
        if (c >= 'A' && c <= 'Z') c = char(c + 32);
    return s;
}

bool EndsWith(const std::string& s, const char* tail) {
    size_t n = strlen(tail);
    return s.size() >= n && s.compare(s.size() - n, n, tail) == 0;
}

// ipaudit.scan_bytes: what a member must not be. "" when it may go in.
wstring Audit(const Member& m) {
    std::string low = LowerA(m.name);
    std::string base = low.substr(low.rfind('/') == std::string::npos ? 0 : low.rfind('/') + 1);
    wstring name = FromUtf8(m.name);
    if (low.rfind("vendor/", 0) == 0 || low.find("unit_expander") != std::string::npos)
        return name + L": third-party code that is not Riftstone's to ship";
    if (base == "ddda.exe" || base == "ddo.exe") return name + L": a game executable";
    static const char* const kText[] = {".py", ".md", ".txt", ".html", ".json", ".cmd", ".bat", ".ini", ".cpp", ".h",
                                        ".inc", ".js", ".css", ".yaml", ".yml", ".toml", ".nyr", ".def", ".rc", ".cs",
                                        ".lua", ".java", ".svg", ".hlsl"};
    bool text = false;
    for (const char* t : kText) text = text || EndsWith(base, t);
    if (!text) {
        static const char* const kMagic[] = {"ARC", "ARCC", "TEX", "MOD", "MRL", "XFS", "GMD", "LMT", "SPAC"};
        for (const char* mg : kMagic) {
            std::string magic(mg);
            if (magic.size() == 3) magic += '\0';
            if (m.data.compare(0, 4, magic) == 0) return name + L": a file of the game (starts " + Widen(mg) + L")";
        }
        if (m.data.compare(0, 4, std::string("SBC\xff", 4)) == 0) return name + L": a collision mesh of the game";
        return L"";
    }
    wstring body = DecodeUtf8Replace(m.data);
    wstring lowW = LowerAscii(body);
    for (size_t at = lowW.find(L"capcom"); at != wstring::npos; at = lowW.find(L"capcom", at + 1)) {
        size_t j = at;
        while (j > 0 && IsPySpace(lowW[j - 1])) j--;
        wstring before = lowW.substr(0, j);
        auto ends = [&](const wchar_t* t) {
            size_t n = wcslen(t);
            return before.size() >= n && before.compare(before.size() - n, n, t) == 0;
        };
        if (ends(L"\x00a9") || ends(L"(c)") || ends(L"copyright"))
            return name + L": Capcom's copyright notation (use the notice in legal.py instead)";
    }
    // each name split in two, so this file does not trip the tree audit (ipaudit.py writes them as patterns)
    static const wchar_t* const kHosts[] = {L"mega" L".nz", L"mediafire" L".com", L"drive" L".google.com",
                                            L"magnet" L":?", L"rapid" L"gator", L"zippy" L"share"};
    for (const wchar_t* h : kHosts)
        if (lowW.find(h) != wstring::npos) return name + L": a link to a file host (" + h + L")";
    const wchar_t* torrent = L"." L"torrent";
    for (size_t at = lowW.find(torrent); at != wstring::npos; at = lowW.find(torrent, at + 1)) {
        wchar_t next = at + 8 < lowW.size() ? lowW[at + 8] : L' ';
        if (!iswalnum(next) && next != L'_') return name + L": a link to a file host (" + torrent + L")";
    }
    return L"";
}

wstring FileTitle(const wstring& title) {   // package._file_title
    wstring t;
    for (wchar_t c : title)
        if (c >= L' ' && !wcschr(L"\\/:*?\"<>|", c) && c != 0xFFFD) t += c;
    auto strip = [](const wstring& s) {
        size_t a = 0, b = s.size();
        while (a < b && (s[a] == L' ' || s[a] == L'.')) a++;
        while (b > a && (s[b - 1] == L' ' || s[b - 1] == L'.')) b--;
        return s.substr(a, b - a);
    };
    t = strip(t);
    size_t cut = 0, points = 0;
    while (cut < t.size() && points < 80) {
        cut += (t[cut] >= 0xD800 && t[cut] <= 0xDBFF && cut + 1 < t.size()) ? 2 : 1;
        points++;
    }
    t = strip(t.substr(0, cut));
    return t.empty() ? L"mods" : t;
}

// textwrap.wrap: words greedily into lines of at most width, the first with first, the rest with rest
std::vector<wstring> Wrap(const wstring& text, size_t width, const wstring& first, const wstring& rest) {
    std::vector<wstring> lines, words;
    size_t a = 0;
    while (a < text.size()) {
        while (a < text.size() && text[a] == L' ') a++;
        size_t b = a;
        while (b < text.size() && text[b] != L' ') b++;
        if (b > a) words.push_back(text.substr(a, b - a));
        a = b;
    }
    wstring line = first;
    bool empty = true;
    for (const auto& w : words) {
        if (!empty && line.size() + 1 + w.size() > width) {
            lines.push_back(line);
            line = rest;
            empty = true;
        }
        line += (empty ? L"" : L" ") + w;
        empty = false;
    }
    if (!empty) lines.push_back(line);
    return lines;
}

void JsonStr(std::string& o, const wstring& s) {   // json.dumps of a str (ensure_ascii)
    static const char* d = "0123456789abcdef";
    o += '"';
    for (wchar_t c : s) {
        if (c == L'"') o += "\\\"";
        else if (c == L'\\') o += "\\\\";
        else if (c == L'\n') o += "\\n";
        else if (c == L'\r') o += "\\r";
        else if (c == L'\t') o += "\\t";
        else if (c == L'\b') o += "\\b";
        else if (c == L'\f') o += "\\f";
        else if (c < 0x20 || c > 0x7E) {
            o += "\\u";
            for (int sh = 12; sh >= 0; sh -= 4) o += d[(c >> sh) & 15];
        } else o += char(c);
    }
    o += '"';
}

void JsonList(std::string& o, const std::vector<wstring>& v, const char* indent) {
    if (v.empty()) {
        o += "[]";
        return;
    }
    o += "[\n";
    for (size_t i = 0; i < v.size(); i++) {
        o += indent;
        o += " ";
        JsonStr(o, v[i]);
        o += i + 1 < v.size() ? ",\n" : "\n";
    }
    o += indent;
    o += "]";
}

std::string Utf8Crlf(const std::vector<wstring>& lines) {
    wstring all;
    for (const auto& l : lines) all += l + L"\r\n";
    return ToUtf8(all);
}

bool DirHasFiles(const wstring& dir) {
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(JoinPath(dir, L"*").c_str(), &fd);
    if (h == INVALID_HANDLE_VALUE) return false;
    bool any = false;
    do {
        wstring n = fd.cFileName;
        if (n != L"." && n != L"..") any = true;
    } while (!any && FindNextFileW(h, &fd));
    FindClose(h);
    return any;
}

}  // namespace

uint32_t Crc32(const std::string& bytes) {
    static uint32_t table[256];
    static bool made = false;
    if (!made) {
        for (uint32_t i = 0; i < 256; i++) {
            uint32_t c = i;
            for (int k = 0; k < 8; k++) c = (c & 1) ? kCrcPoly ^ (c >> 1) : c >> 1;
            table[i] = c;
        }
        made = true;
    }
    uint32_t c = 0xFFFFFFFFu;
    for (char ch : bytes) c = table[(c ^ uint8_t(ch)) & 0xFF] ^ (c >> 8);
    return c ^ 0xFFFFFFFFu;
}

std::string Sha256Hex(const std::string& bytes) {
    BCRYPT_ALG_HANDLE alg = nullptr;
    BCRYPT_HASH_HANDLE hash = nullptr;
    uint8_t out[32] = {};
    bool ok = BCRYPT_SUCCESS(BCryptOpenAlgorithmProvider(&alg, BCRYPT_SHA256_ALGORITHM, nullptr, 0)) &&
              BCRYPT_SUCCESS(BCryptCreateHash(alg, &hash, nullptr, 0, nullptr, 0, 0));
    size_t done = 0;
    while (ok && done < bytes.size()) {
        ULONG n = ULONG(std::min<size_t>(bytes.size() - done, 1u << 24));
        ok = BCRYPT_SUCCESS(BCryptHashData(hash, PUCHAR(bytes.data() + done), n, 0));
        done += n;
    }
    ok = ok && BCRYPT_SUCCESS(BCryptFinishHash(hash, out, sizeof out, 0));
    if (hash) BCryptDestroyHash(hash);
    if (alg) BCryptCloseAlgorithmProvider(alg, 0);
    if (!ok) return "";
    static const char* d = "0123456789abcdef";
    std::string o;
    for (uint8_t b : out) {
        o += d[b >> 4];
        o += d[b & 15];
    }
    return o;
}

bool ExportPack(const wstring& game, const wstring& name, const wstring& out, PackReport& r, wstring& error) {
    r = PackReport();
    wstring title = Trim(name).empty() ? L"Riftstone setup" : Trim(name);
    Profile profile;
    if (!ProfileFromGame(game, title, profile, error)) return false;
    wstring dll = JoinPath(game, L"dinput8.dll"), loaderVer = LoaderVersion(dll);
    if (loaderVer.empty()) {
        error = L"the game folder has no Riftstone loader (dinput8.dll): a pack carries the loader, so unzip "
                L"Riftstone's player zip into the game first";
        return false;
    }
    std::vector<Member> members;
    std::map<std::string, std::string> shas;
    auto put = [&](const std::string& path, std::string data) -> bool {
        Member m{path, std::move(data)};
        wstring found = Audit(m);
        if (!found.empty()) {
            error = L"the pack would carry what it must not: " + found;
            return false;
        }
        shas[path] = Sha256Hex(m.data);
        r.files.push_back(FromUtf8(path));
        members.push_back(std::move(m));
        return true;
    };
    auto file = [&](const std::string& path, const wstring& from) -> bool {
        std::string data;
        if (!ReadFileBytes(from, data, 256u << 20)) {
            error = L"cannot read " + from;
            return false;
        }
        return put(path, std::move(data));
    };
    if (!file("dinput8.dll", dll)) return false;
    wstring loaderIni = LoaderIni(game);
    if (FileExists(loaderIni) && !file("riftstone_loader.ini", loaderIni)) return false;
    wstring lic = JoinPath(StateDir(game), L"LICENSE-Riftstone.txt");
    if (FileExists(lic) ? !file("riftstone/LICENSE-Riftstone.txt", lic)
                        : !put("riftstone/LICENSE-Riftstone.txt", ToUtf8(LicenseText())))
        return false;
    std::vector<std::pair<wstring, wstring>> carried;   // file, what it does (state)
    for (const auto& f : ListFeatures(game)) {
        if (!IsOwnPlugin(f.stem)) {
            r.leftOut.push_back(L"riftstone\\plugins" + wstring(f.on ? L"\\" : L"\\off\\") + f.file +
                                (Lower(f.stem) == L"compat" ? L" (the experimental compat layer, which never travels)"
                                                            : L" (not Riftstone's: its author shares it)"));
            continue;
        }
        std::string folder = f.on ? "riftstone/plugins/" : "riftstone/plugins/off/";
        if (!file(folder + ToUtf8(f.file), f.path())) return false;
        if (FileExists(f.ini()) && !file(folder + ToUtf8(FileName(f.ini())), f.ini())) return false;
        r.plugins.push_back(f.file);
        if (!f.on) r.pluginsOff.push_back(Lower(f.stem));
        carried.push_back({f.file, (f.on ? L"on" : L"off") + wstring(f.summary.empty() ? L"" : L" -- " + f.summary)});
    }
    std::sort(r.pluginsOff.begin(), r.pluginsOff.end());
    wstring startHere = JoinPath(game, L"Riftstone - Start Here.cmd");
    bool haveStart = FileExists(startHere);
    if (haveStart && !file("Riftstone - Start Here.cmd", startHere)) return false;
    if (!put("riftstone/profile.json", ProfileToJson(profile))) return false;

    // what stays behind, by name
    std::vector<Setting> loader;
    ReadSettings(loaderIni, loader);
    for (const auto& s : loader) {
        wstring sec = Lower(s.section), key = Lower(s.key), v = s.value;
        size_t semi = v.find(L';');
        v = Trim(semi == wstring::npos ? v : v.substr(0, semi));
        if (key == L"chain" && !v.empty() && (sec == L"loader" || sec == L"d3d9"))
            r.leftOut.push_back(L"[" + s.section + L"] chain = " + v + L" in riftstone_loader.ini: the DLL it names "
                                L"(the loader skips it, and says so in loader.log, where it is missing)");
    }
    if (DirHasFiles(JoinPath(StateDir(game), L"overlay")))
        r.leftOut.push_back(L"the installed mods (riftstone\\overlay): mods travel as Riftstone packages "
                            L"('Riftstone.cmd package'), which hold no game data");
    if (DirHasFiles(JoinPath(StateDir(game), L"dxvk"))) r.leftOut.push_back(L"DXVK (riftstone\\dxvk): its own project's");
    if (DirHasFiles(JoinPath(StateDir(game), L"legacy")))
        r.leftOut.push_back(L"older mods' DLLs (riftstone\\legacy): their authors'");
    if (FileExists(JoinPath(game, L"enbseries.ini")) || FileExists(JoinPath(game, L"enblocal.ini")))
        r.leftOut.push_back(L"ENB (d3d9.dll, enbseries.ini, enblocal.ini): from enbdev.com");
    else if (FileExists(JoinPath(game, L"d3d9.dll")))
        r.leftOut.push_back(L"the game folder's d3d9.dll (another project's)");
    r.leftOut.push_back(L"the game's own files, saves and logs");

    // the README
    wstring stamp;
    {
        SYSTEMTIME t;
        GetLocalTime(&t);
        wchar_t buf[64];
        swprintf_s(buf, L"%04u-%02u-%02u", unsigned(t.wYear), unsigned(t.wMonth), unsigned(t.wDay));
        stamp = buf;
    }
    std::vector<wstring> lines = {title, wstring(title.size(), L'='), L""};
    auto para = [&](const wstring& s, const wstring& a = L"", const wstring& b = L"") {
        for (const auto& l : Wrap(s, 100, a, b)) lines.push_back(l);
    };
    para(L"A Riftstone setup for Dragon's Dogma: Dark Arisen (Steam), exported with Riftstone Studio: the loader, "
         L"Riftstone's own features switched on and off as they were, and their settings. It changes none of the "
         L"game's own files, and this zip holds no game files and no mods. You do not need Python or anything else.");
    lines.insert(lines.end(),
                 {L"", L"Install (one minute)", L"  1. Close the game.",
                  L"  2. Open the game folder: in Steam, right-click Dragon's Dogma: Dark Arisen, then Manage, then",
                  L"     Browse local files. It is the folder with DDDA.exe in it.",
                  L"  3. Unzip everything from this zip into that folder, keeping the folders. If Windows asks to",
                  L"     replace files, say yes. (If it says a file named dinput8.dll is already there, another mod",
                  L"     owns that name: stop and read \"Already have a dinput8.dll?\" below first.)",
                  L"  4. Start the game. For about 12 seconds a small RUNNING notice shows in the top-right corner:",
                  L"     that is how you know Riftstone is in the game.", L"", L"What is in the zip",
                  L"  * dinput8.dll and riftstone_loader.ini -- the Riftstone loader " + loaderVer +
                      L", with this setup's loader settings"});
    if (haveStart) lines.push_back(L"  * Riftstone - Start Here.cmd -- the check and the on/off switch");
    for (const auto& c : carried) para(L"* feature " + c.first + L" (" + c.second + L")", L"  ", L"    ");
    para(L"* riftstone\\profile.json -- the same switches and settings as a Riftstone profile: Riftstone Studio's "
         L"Profiles page applies it to a game folder that already has Riftstone",
         L"  ", L"    ");
    lines.push_back(L"");
    lines.push_back(L"Not in the zip");
    for (const auto& l : r.leftOut) para(L"* " + l, L"  ", L"    ");
    lines.insert(lines.end(),
                 {L"", L"Already have a dinput8.dll?",
                  L"  Another mod in the game folder (DDDA Tweak, for example) may use that file name. Do this before",
                  L"  step 3 and both keep working:",
                  L"    a. Rename your dinput8.dll to  dinput8_chain.dll  (in the same folder).",
                  L"    b. Do step 3. Then open riftstone_loader.ini in Notepad. Under [loader], find the line  chain =",
                  L"       change it to  chain = dinput8_chain.dll  and save.",
                  L"", L"Remove",
                  L"  Delete dinput8.dll, riftstone_loader.ini, the riftstone folder and the Start Here file from the",
                  L"  game folder (if you renamed your own dinput8.dll to dinput8_chain.dll, rename it back).",
                  L"  The game is as it was.", L"", L"Good to know",
                  L"  * Made for the Steam version of the game (build 2364871). The features check the game's",
                  L"    code when it starts and do nothing on any other version (their reason is in riftstone\\logs).",
                  L"  * If the game crashes, riftstone\\logs has a crash report (crash-*.txt) to send to Riftstone's",
                  L"    author, not to Capcom's support.",
                  L"  * The loader (dinput8.dll) and Riftstone's own features are free software under the MIT License",
                  L"    (riftstone\\LICENSE-Riftstone.txt).",
                  L"", L"Legal"});
    para(DisclaimerText(), L"  ", L"  ");
    para(FreeText(), L"  ", L"  ");
    lines.push_back(L"");
    lines.push_back(L"Made with Riftstone Studio " + Widen(kVersion) + L" on " + stamp + L".");
    r.readme = L"README - " + FileTitle(title) + L".txt";
    if (!put(ToUtf8(r.readme), Utf8Crlf(lines))) return false;

    // riftstone/package.json, as build_plugins writes it (json.dumps(indent=1)), and what this pack adds
    SYSTEMTIME t;
    GetLocalTime(&t);
    char made[32];
    sprintf_s(made, "%04u-%02u-%02uT%02u:%02u:%02u", unsigned(t.wYear), unsigned(t.wMonth), unsigned(t.wDay),
              unsigned(t.wHour), unsigned(t.wMinute), unsigned(t.wSecond));
    std::string j = "{\n \"name\": ";
    JsonStr(j, title);
    j += ",\n \"made_with\": ";
    JsonStr(j, L"Riftstone Studio " + Widen(kVersion));
    j += ",\n \"game_build\": \"2364871\",\n \"made\": \"" + std::string(made) + "\",\n \"mods\": [],\n \"plugins\": ";
    JsonList(j, r.plugins, " ");
    j += ",\n \"plugins_off\": ";
    JsonList(j, r.pluginsOff, " ");
    j += ",\n \"archives\": [],\n \"optional\": [],\n \"files\": {";
    bool firstFile = true;
    for (const auto& kv : shas) {
        j += firstFile ? "\n  " : ",\n  ";
        firstFile = false;
        JsonStr(j, FromUtf8(kv.first));
        j += ": \"" + kv.second + "\"";
    }
    j += shas.empty() ? "}" : "\n }";
    j += ",\n \"profile\": \"riftstone/profile.json\",\n \"loader\": ";
    JsonStr(j, loaderVer);
    j += ",\n \"left_out\": ";
    JsonList(j, r.leftOut, " ");
    j += "\n}";
    if (!put("riftstone/package.json", j)) return false;

    std::string zip;
    if (!ZipStored(members, zip, error)) return false;
    wstring dir = ParentDir(out);
    if (!dir.empty() && !MakeDirs(dir)) {
        error = L"cannot make the folder for " + out;
        return false;
    }
    if (!WriteFileAtomic(out, zip, error)) return false;
    r.out = out;
    r.bytes = zip.size();
    return true;
}

}  // namespace studio

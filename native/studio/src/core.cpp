// Riftstone Studio's core: the toolkit's rules and files, ported (src/riftstone/plugins.py, profiles.py, game.py,
// runtime.py). Every message a check gives is the toolkit's own, so a player sees the same words in both.
#include "studio.h"
#include "version.h"

#include <tlhelp32.h>

#include <algorithm>
#include <charconv>
#include <climits>
#include <cmath>
#include <cstring>
#include <map>
#include <set>

namespace studio {

#include "catalog.inc"

const char* const kVersion = STUDIO_VERSION_A;
const wchar_t* const kGameTitle = L"Dragon's Dogma: Dark Arisen";
const wchar_t* const kLoaderStem = L"loader";

bool IsOwnPlugin(const wstring& stem) {
    wstring s = Lower(stem);
    for (const wchar_t* own : kOwnPlugins)
        if (s == own) return true;
    return false;
}
size_t OwnPluginCount() { return std::size(kOwnPlugins); }
const wchar_t* OwnPluginAt(size_t i) { return i < std::size(kOwnPlugins) ? kOwnPlugins[i] : L""; }
const wchar_t* LicenseText() { return kLicense; }
const wchar_t* DisclaimerText() { return kDisclaimer; }
const wchar_t* FreeText() { return kFree; }
const wchar_t* D3d9ChainPath() { return kD3d9ChainPath; }

// -- text --------------------------------------------------------------------------------------------------------------

wstring LowerAscii(const wstring& s) {
    wstring o(s);
    for (auto& c : o)
        if (c >= L'A' && c <= L'Z') c = wchar_t(c + 32);
    return o;
}

wstring Lower(const wstring& s) {
    bool ascii = true;
    for (wchar_t c : s)
        if (c >= 0x80) ascii = false;
    if (ascii || s.empty()) return LowerAscii(s);
    int n = LCMapStringEx(LOCALE_NAME_INVARIANT, LCMAP_LOWERCASE, s.c_str(), int(s.size()), nullptr, 0, nullptr,
                          nullptr, 0);
    if (n <= 0) return LowerAscii(s);
    wstring o(size_t(n), L'\0');
    LCMapStringEx(LOCALE_NAME_INVARIANT, LCMAP_LOWERCASE, s.c_str(), int(s.size()), &o[0], n, nullptr, nullptr, 0);
    return o;
}

bool IsPySpace(wchar_t c) {   // str.isspace
    return (c >= 0x09 && c <= 0x0D) || (c >= 0x1C && c <= 0x20) || c == 0x85 || c == 0xA0 || c == 0x1680 ||
           (c >= 0x2000 && c <= 0x200A) || c == 0x2028 || c == 0x2029 || c == 0x202F || c == 0x205F || c == 0x3000;
}

wstring Trim(const wstring& s) {
    size_t a = 0, b = s.size();
    while (a < b && IsPySpace(s[a])) a++;
    while (b > a && IsPySpace(s[b - 1])) b--;
    return s.substr(a, b - a);
}

static wstring Strip(const wstring& s, const wchar_t* chars, bool left, bool right) {
    size_t a = 0, b = s.size();
    if (left)
        while (a < b && wcschr(chars, s[a]) && s[a]) a++;
    if (right)
        while (b > a && wcschr(chars, s[b - 1]) && s[b - 1]) b--;
    return s.substr(a, b - a);
}

static bool IsHigh(wchar_t c) { return c >= 0xD800 && c <= 0xDBFF; }
static bool IsLow(wchar_t c) { return c >= 0xDC00 && c <= 0xDFFF; }

size_t CodePoints(const wstring& s) {
    size_t n = 0;
    for (size_t i = 0; i < s.size(); i++, n++)
        if (IsHigh(s[i]) && i + 1 < s.size() && IsLow(s[i + 1])) i++;
    return n;
}

static bool HasLoneSurrogate(const wstring& s) {
    for (size_t i = 0; i < s.size(); i++) {
        if (IsHigh(s[i]) && i + 1 < s.size() && IsLow(s[i + 1])) {
            i++;
            continue;
        }
        if (IsHigh(s[i]) || IsLow(s[i])) return true;
    }
    return false;
}

wstring PyRepr(const wstring& s) {
    bool single = s.find(L'\'') != wstring::npos, dbl = s.find(L'"') != wstring::npos;
    wchar_t q = (single && !dbl) ? L'"' : L'\'';
    wstring o(1, q);
    wchar_t buf[16];
    for (size_t i = 0; i < s.size(); i++) {
        wchar_t c = s[i];
        if (c == q || c == L'\\') {
            o += L'\\';
            o += c;
        } else if (c == L'\t') o += L"\\t";
        else if (c == L'\n') o += L"\\n";
        else if (c == L'\r') o += L"\\r";
        else if (c < 0x20 || (c >= 0x7F && c <= 0xA0) || c == 0xAD) {
            swprintf_s(buf, L"\\x%02x", unsigned(c));
            o += buf;
        } else if ((IsHigh(c) && !(i + 1 < s.size() && IsLow(s[i + 1]))) || (IsLow(c) && !(i > 0 && IsHigh(s[i - 1])))) {
            swprintf_s(buf, L"\\u%04x", unsigned(c));
            o += buf;
        } else o += c;
    }
    o += q;
    return o;
}

wstring Widen(const char* s) {
    wstring o;
    for (; *s; s++) o += wchar_t(uint8_t(*s));
    return o;
}

wstring FromUtf8(const std::string& s) {
    if (s.empty()) return {};
    int n = MultiByteToWideChar(CP_UTF8, 0, s.data(), int(s.size()), nullptr, 0);
    wstring o(size_t(n), L'\0');
    if (n) MultiByteToWideChar(CP_UTF8, 0, s.data(), int(s.size()), &o[0], n);
    return o;
}

std::string ToUtf8(const wstring& s) {
    if (s.empty()) return {};
    int n = WideCharToMultiByte(CP_UTF8, 0, s.data(), int(s.size()), nullptr, 0, nullptr, nullptr);
    std::string o(size_t(n), '\0');
    if (n) WideCharToMultiByte(CP_UTF8, 0, s.data(), int(s.size()), &o[0], n, nullptr, nullptr);
    return o;
}

// UTF-8 as Python's strict codec reads it: no overlong forms, no encoded surrogates, nothing past U+10FFFF.
bool DecodeUtf8Strict(const std::string& s, wstring& out) {
    out.clear();
    out.reserve(s.size());
    const auto* p = reinterpret_cast<const uint8_t*>(s.data());
    size_t n = s.size(), i = 0;
    while (i < n) {
        uint32_t c = p[i];
        if (c < 0x80) {
            out += wchar_t(c);
            i++;
            continue;
        }
        int len;
        uint32_t lo = 0x80, hi = 0xBF;
        if (c >= 0xC2 && c <= 0xDF) len = 2, c &= 0x1F;
        else if (c >= 0xE0 && c <= 0xEF) {
            len = 3;
            if (c == 0xE0) lo = 0xA0;
            if (c == 0xED) hi = 0x9F;
            c &= 0x0F;
        } else if (c >= 0xF0 && c <= 0xF4) {
            len = 4;
            if (c == 0xF0) lo = 0x90;
            if (c == 0xF4) hi = 0x8F;
            c &= 0x07;
        } else return false;
        if (i + size_t(len) > n) return false;
        for (int k = 1; k < len; k++) {
            uint32_t b = p[i + size_t(k)];
            if (k == 1 ? (b < lo || b > hi) : (b < 0x80 || b > 0xBF)) return false;
            c = (c << 6) | (b & 0x3F);
        }
        if (c >= 0x10000) {
            c -= 0x10000;
            out += wchar_t(0xD800 + (c >> 10));
            out += wchar_t(0xDC00 + (c & 0x3FF));
        } else out += wchar_t(c);
        i += size_t(len);
    }
    return true;
}

// -- files -------------------------------------------------------------------------------------------------------------

bool FileExists(const wstring& path) {
    DWORD a = GetFileAttributesW(path.c_str());
    return a != INVALID_FILE_ATTRIBUTES && !(a & FILE_ATTRIBUTE_DIRECTORY);
}

bool DirExists(const wstring& path) {
    DWORD a = GetFileAttributesW(path.c_str());
    return a != INVALID_FILE_ATTRIBUTES && (a & FILE_ATTRIBUTE_DIRECTORY);
}

bool PathExists(const wstring& path) { return GetFileAttributesW(path.c_str()) != INVALID_FILE_ATTRIBUTES; }

bool ReadFileBytes(const wstring& path, std::string& out, size_t limit) {
    out.clear();
    HANDLE h = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr,
                           OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) return false;
    LARGE_INTEGER size{};
    bool ok = GetFileSizeEx(h, &size) && size.QuadPart >= 0 && uint64_t(size.QuadPart) <= limit;
    if (ok) {
        out.resize(size_t(size.QuadPart));
        size_t done = 0;
        while (ok && done < out.size()) {
            DWORD got = 0, want = DWORD(std::min<size_t>(out.size() - done, 1u << 24));
            ok = ReadFile(h, &out[done], want, &got, nullptr) && got > 0;
            done += got;
        }
    }
    CloseHandle(h);
    if (!ok) out.clear();
    return ok;
}

static wstring LastError(DWORD code) {
    wchar_t* msg = nullptr;
    FormatMessageW(FORMAT_MESSAGE_ALLOCATE_BUFFER | FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS, nullptr,
                   code, 0, reinterpret_cast<wchar_t*>(&msg), 0, nullptr);
    wstring o = msg ? Trim(msg) : L"error " + std::to_wstring(code);
    if (msg) LocalFree(msg);
    return o;
}

bool WriteFileAtomic(const wstring& path, const std::string& bytes, wstring& error) {
    wstring tmp = path + L".riftstone-tmp";
    HANDLE h = CreateFileW(tmp.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) {
        error = FileName(path) + L": " + LastError(GetLastError());
        return false;
    }
    size_t done = 0;
    bool ok = true;
    while (ok && done < bytes.size()) {
        DWORD put = 0, want = DWORD(std::min<size_t>(bytes.size() - done, 1u << 24));
        ok = WriteFile(h, bytes.data() + done, want, &put, nullptr) && put == want;
        done += put;
    }
    DWORD code = ok ? 0 : GetLastError();
    CloseHandle(h);
    if (ok && !MoveFileExW(tmp.c_str(), path.c_str(), MOVEFILE_REPLACE_EXISTING)) {
        ok = false;
        code = GetLastError();
    }
    if (!ok) {
        DeleteFileW(tmp.c_str());
        error = FileName(path) + L": " + LastError(code);
    }
    return ok;
}

bool MakeDirs(const wstring& path) {
    if (path.empty() || DirExists(path)) return true;
    wstring parent = ParentDir(path);
    if (!parent.empty() && parent != path && !MakeDirs(parent)) return false;
    return CreateDirectoryW(path.c_str(), nullptr) || GetLastError() == ERROR_ALREADY_EXISTS;
}

wstring NormalPath(const wstring& p) {
    wstring o(p);
    std::replace(o.begin(), o.end(), L'/', L'\\');
    while (o.size() > 3 && o.back() == L'\\') o.pop_back();
    return o;
}

wstring JoinPath(const wstring& a, const wstring& b) {
    if (a.empty()) return b;
    return (a.back() == L'\\' || a.back() == L'/') ? a + b : a + L"\\" + b;
}

wstring FileName(const wstring& path) {
    size_t i = path.find_last_of(L"\\/");
    return i == wstring::npos ? path : path.substr(i + 1);
}

wstring ParentDir(const wstring& path) {
    size_t i = path.find_last_of(L"\\/");
    if (i == wstring::npos) return L"";
    if (i == 2 && path.size() > 1 && path[1] == L':') return path.substr(0, 3);
    return path.substr(0, i);
}

wstring ExeDir() {
    wchar_t buf[MAX_PATH * 4];
    DWORD n = GetModuleFileNameW(nullptr, buf, DWORD(std::size(buf)));
    return n ? ParentDir(wstring(buf, n)) : L".";
}

// Path.suffix / Path.stem of a file name.
static wstring Suffix(const wstring& name) {
    size_t i = name.rfind(L'.');
    return (i != wstring::npos && i > 0 && i + 1 < name.size()) ? name.substr(i) : L"";
}

static wstring StemOf(const wstring& name) {
    wstring s = Suffix(name);
    return name.substr(0, name.size() - s.size());
}

// -- the rules ---------------------------------------------------------------------------------------------------------

const CatalogEntry* CatalogFind(const wstring& stem) {
    wstring k = LowerAscii(stem);
    for (const auto& e : kCatalog)
        if (k == e.stem) return &e;
    return nullptr;
}

size_t CatalogSize() { return std::size(kCatalog); }
const CatalogEntry& CatalogAt(size_t i) { return kCatalog[i]; }

static std::vector<wstring> Split(const wstring& s, wchar_t sep) {
    std::vector<wstring> out;
    size_t a = 0;
    for (;;) {
        size_t b = s.find(sep, a);
        out.push_back(s.substr(a, b == wstring::npos ? wstring::npos : b - a));
        if (b == wstring::npos) break;
        a = b + 1;
    }
    return out;
}

Check CheckFor(const wstring& stem, const wstring& section, const wstring& key) {
    wstring st = Lower(stem), se = Lower(section), k = Lower(key);
    const Rule* rule = nullptr;
    for (const auto& r : kRules)
        if (st == r.stem && se == r.section && k == r.key) rule = &r;
    if (!rule)
        for (const auto& r : kSectionRules)
            if (st == r.stem && se == r.section) rule = &r;
    Check c;
    if (rule) {
        c.kind = rule->kind;
        c.lo = rule->lo;
        c.hi = rule->hi;
        c.loText = rule->loText;
        c.hiText = rule->hiText;
        if (rule->kind == RULE_CHOICE) c.choices = Split(rule->choices, L'|');
    } else {
        for (const wchar_t* b : kBoolKeys)
            if (k == b) {
                c.kind = RULE_INT;
                c.lo = 0;
                c.hi = 1;
                c.loText = L"0";
                c.hiText = L"1";
            }
    }
    if (c.kind == RULE_FKEY) {
        for (int i = 1; i <= 12; i++) c.choices.push_back(L"F" + std::to_wstring(i));
        for (const wchar_t* n : kPanelKeys) c.choices.push_back(n);
    }
    return c;
}

// Python's int() of a stripped text, ASCII digits only (Python takes every Unicode decimal digit; the exe is stricter).
static bool PyInt(const wstring& t, long long& v) {
    size_t i = 0;
    bool neg = false;
    if (i < t.size() && (t[i] == L'+' || t[i] == L'-')) neg = t[i++] == L'-';
    if (i >= t.size()) return false;
    long long acc = 0;
    bool big = false, last = false;
    for (; i < t.size(); i++) {
        wchar_t c = t[i];
        if (c >= L'0' && c <= L'9') {
            if (acc > (LLONG_MAX - 9) / 10) big = true;
            else acc = acc * 10 + (c - L'0');
            last = true;
        } else if (c == L'_' && last && i + 1 < t.size() && t[i + 1] >= L'0' && t[i + 1] <= L'9') {
            last = false;
        } else return false;
    }
    v = big ? (neg ? LLONG_MIN : LLONG_MAX) : (neg ? -acc : acc);
    return true;
}

// Python's float() of a stripped text (ASCII): digits with single underscores, inf, infinity, nan.
static bool PyFloat(const wstring& t, double& v) {
    size_t i = 0;
    bool neg = false;
    if (i < t.size() && (t[i] == L'+' || t[i] == L'-')) neg = t[i++] == L'-';
    wstring rest = LowerAscii(t.substr(i));
    if (rest == L"inf" || rest == L"infinity") {
        v = neg ? -HUGE_VAL : HUGE_VAL;
        return true;
    }
    if (rest == L"nan") {
        v = std::nan("");
        return true;
    }
    std::string clean;
    auto digits = [&](size_t& j) {   // digit (_? digit)*
        size_t start = j;
        while (j < rest.size()) {
            if (rest[j] >= L'0' && rest[j] <= L'9') clean += char(rest[j++]);
            else if (rest[j] == L'_' && j > start && rest[j - 1] != L'_' && j + 1 < rest.size() && rest[j + 1] >= L'0' &&
                     rest[j + 1] <= L'9') j++;
            else break;
        }
        return j > start;
    };
    size_t j = 0;
    bool whole = digits(j), frac = false;
    if (j < rest.size() && rest[j] == L'.') {
        clean += '.';
        j++;
        frac = digits(j);
    }
    if (!whole && !frac) return false;
    if (j < rest.size() && rest[j] == L'e') {
        clean += 'e';
        j++;
        if (j < rest.size() && (rest[j] == L'+' || rest[j] == L'-')) clean += char(rest[j++]);
        if (!digits(j)) return false;
    }
    if (j != rest.size()) return false;
    double d = 0;
    auto r = std::from_chars(clean.data(), clean.data() + clean.size(), d, std::chars_format::general);
    if (r.ec == std::errc::result_out_of_range) {
        // from_chars refuses an overflow or underflow Python rounds: past the largest double is inf, below the
        // smallest is 0 (either is out of every rule's range or 0, as Python's would be)
        size_t e = clean.find('e');
        bool tiny = e != std::string::npos && clean[e + 1] == '-';
        d = tiny ? 0.0 : HUGE_VAL;
    } else if (r.ec != std::errc() || r.ptr != clean.data() + clean.size()) return false;
    v = neg ? -d : d;
    return true;
}

// Python's repr of a float.
static wstring FloatRepr(double v) {
    if (std::isnan(v)) return L"nan";
    if (std::isinf(v)) return v < 0 ? L"-inf" : L"inf";
    char buf[64];
    auto r = std::to_chars(buf, buf + sizeof buf, v, std::chars_format::scientific);
    std::string s(buf, r.ptr);
    bool neg = !s.empty() && s[0] == '-';
    if (neg) s.erase(0, 1);
    size_t e = s.find('e');
    std::string mant = s.substr(0, e), digits;
    for (char c : mant)
        if (c != '.') digits += c;
    int exp = std::atoi(s.c_str() + e + 1);
    while (digits.size() > 1 && digits.back() == '0') digits.pop_back();
    int decpt = exp + 1;
    std::string o;
    if (decpt <= -4 || decpt > 16) {
        o = digits.substr(0, 1);
        if (digits.size() > 1) o += "." + digits.substr(1);
        char eb[16];
        snprintf(eb, sizeof eb, "e%c%02d", exp < 0 ? '-' : '+', exp < 0 ? -exp : exp);
        o += eb;
    } else if (decpt <= 0) {
        o = "0." + std::string(size_t(-decpt), '0') + digits;
    } else if (size_t(decpt) >= digits.size()) {
        o = digits + std::string(size_t(decpt) - digits.size(), '0') + ".0";
    } else {
        o = digits.substr(0, size_t(decpt)) + "." + digits.substr(size_t(decpt));
    }
    return (neg ? L"-" : L"") + Widen(o.c_str());
}

static bool Number(bool isFloat, const wstring& text, const wstring& key, const Check& c, wstring& out, wstring& error,
                   double* value = nullptr) {
    double n = 0;
    long long i = 0;
    bool ok = isFloat ? PyFloat(text, n) : PyInt(text, i);
    if (ok && !isFloat) n = double(i);
    if (!ok || !(c.lo <= n && n <= c.hi)) {
        error = key + L" is a number from " + c.loText + L" to " + c.hiText;
        return false;
    }
    out = isFloat ? FloatRepr(n) : std::to_wstring(i);
    if (value) *value = n;
    return true;
}

bool Validate(const wstring& stem, const wstring& section, const wstring& key, const wstring& value, wstring& clean,
              wstring& error) {
    wstring text = Trim(value);
    if (text.empty() || CodePoints(text) > 64 || text.find_first_of(L";=[]\r\n") != wstring::npos ||
        text.find(L'\0') != wstring::npos) {
        error = key + L": a short value without ; = [ ] or line breaks";
        return false;
    }
    Check c = CheckFor(stem, section, key);
    switch (c.kind) {
    case RULE_TEXT:
        clean = text;
        return true;
    case RULE_INT:
        return Number(false, text, key, c, clean, error);
    case RULE_FLOAT:
        return Number(true, text, key, c, clean, error);
    case RULE_AUTO_FLOAT:
    case RULE_AUTO_INT:
        if (Lower(text) == L"auto") {
            clean = L"auto";
            return true;
        }
        return Number(c.kind == RULE_AUTO_FLOAT, text, key, c, clean, error);
    case RULE_INT_OR_ZERO:
        if (text == L"0") {
            clean = L"0";
            return true;
        }
        return Number(false, text, key, c, clean, error);
    case RULE_FLOAT_OR_ZERO: {
        double z;
        if (PyFloat(text, z) && z == 0) {
            clean = L"0";
            return true;
        }
        double v = 0;
        if (!Number(true, text, key, c, clean, error, &v)) return false;
        if (std::floor(v) == v && std::isfinite(v)) {
            wchar_t buf[64];
            swprintf_s(buf, L"%.0f", v);
            clean = buf;
        }
        return true;
    }
    case RULE_SHADOW: {
        if (text == L"0") {
            clean = L"0";
            return true;
        }
        double v = 0;
        if (!Number(false, text, key, c, clean, error, &v)) return false;
        if (long long(v) % 32) {
            error = key + L" is a multiple of 32 (or 0 to leave it)";
            return false;
        }
        return true;
    }
    case RULE_FKEY: {
        if ((text.size() == 2 && (text[0] == L'F' || text[0] == L'f') && text[1] >= L'1' && text[1] <= L'9') ||
            (text.size() == 3 && (text[0] == L'F' || text[0] == L'f') && text[1] == L'1' && text[2] >= L'0' &&
             text[2] <= L'2')) {
            clean = L"F" + text.substr(1);
            return true;
        }
        wstring low = Lower(text), names;
        for (const wchar_t* n : kPanelKeys) {
            if (low == LowerAscii(n)) {
                clean = n;
                return true;
            }
            names += (names.empty() ? L"" : L", ") + wstring(n);
        }
        error = key + L" is one of F1 to F12, " + names;
        return false;
    }
    case RULE_CHOICE: {
        wstring low = Lower(text), all;
        for (const auto& ch : c.choices) {
            if (low == ch) {
                clean = low;
                return true;
            }
            all += (all.empty() ? L"" : L", ") + ch;
        }
        error = key + L" is one of: " + all;
        return false;
    }
    }
    clean = text;
    return true;
}

// -- ini files ---------------------------------------------------------------------------------------------------------

static bool Utf16Mark(const std::string& raw) {
    return raw.size() >= 2 && uint8_t(raw[0]) == 0xFF && uint8_t(raw[1]) == 0xFE;
}

// UTF-16LE bytes as text; strict: false on an odd byte or a lone surrogate; else each becomes U+FFFD (Python's
// "replace").
static bool Utf16Text(const std::string& bytes, wstring& out, bool strict) {
    out.clear();
    size_t n = bytes.size() / 2;
    out.reserve(n + 1);
    for (size_t i = 0; i < n; i++)
        out += wchar_t(uint8_t(bytes[2 * i]) | (uint8_t(bytes[2 * i + 1]) << 8));
    bool ok = true;
    for (size_t i = 0; i < out.size(); i++) {
        if (IsHigh(out[i]) && i + 1 < out.size() && IsLow(out[i + 1])) {
            i++;
            continue;
        }
        if (IsHigh(out[i]) || IsLow(out[i])) {
            ok = false;
            out[i] = 0xFFFD;
        }
    }
    if (bytes.size() % 2) {
        ok = false;
        out += wchar_t(0xFFFD);
    }
    return ok || !strict;
}

static wstring CodePageText(const char* p, size_t n) {
    if (!n) return {};
    int w = MultiByteToWideChar(CP_ACP, 0, p, int(n), nullptr, 0);
    wstring o(size_t(w), L'\0');
    if (w) MultiByteToWideChar(CP_ACP, 0, p, int(n), &o[0], w);
    return o;
}

// A line of a code-page ini: [start, body) is the line, [body, end) its line end (bytes.splitlines(keepends=True)).
struct Span {
    size_t start, body, end;
};

static std::vector<Span> ByteLines(const std::string& raw) {
    std::vector<Span> out;
    size_t start = 0, i = 0;
    while (i < raw.size()) {
        if (raw[i] == '\r' || raw[i] == '\n') {
            size_t body = i;
            if (raw[i] == '\r' && i + 1 < raw.size() && raw[i + 1] == '\n') i++;
            i++;
            out.push_back({start, body, i});
            start = i;
        } else i++;
    }
    if (start < raw.size()) out.push_back({start, raw.size(), raw.size()});
    return out;
}

// re.split(r"(\r\n|\r|\n)", text): lines[k] and the end after it (the last line has none).
static void TextLines(const wstring& text, std::vector<wstring>& lines, std::vector<wstring>& ends) {
    size_t start = 0;
    for (size_t i = 0; i < text.size(); i++) {
        if (text[i] == L'\r' || text[i] == L'\n') {
            lines.push_back(text.substr(start, i - start));
            if (text[i] == L'\r' && i + 1 < text.size() && text[i + 1] == L'\n') {
                ends.push_back(L"\r\n");
                i++;
            } else ends.push_back(wstring(1, text[i]));
            start = i + 1;
        }
    }
    lines.push_back(text.substr(start));
}

static std::vector<wstring> IniLines(const std::string& raw) {
    std::vector<wstring> lines;
    if (Utf16Mark(raw)) {
        wstring text;
        Utf16Text(raw.substr(2), text, false);
        std::vector<wstring> ends;
        TextLines(text, lines, ends);
    } else {
        for (const auto& s : ByteLines(raw)) lines.push_back(CodePageText(raw.data() + s.start, s.body - s.start));
    }
    return lines;
}

static bool IsSection(const wstring& line) {
    return !line.empty() && line.front() == L'[' && line.back() == L']' && line.size() >= 2;
}

bool ReadSettings(const wstring& path, std::vector<Setting>& out) {
    out.clear();
    std::string raw;
    if (!FileExists(path) || !ReadFileBytes(path, raw)) return false;
    wstring section;
    std::vector<wstring> notes, above;
    bool first = false;
    auto join = [](const std::vector<wstring>& v) {
        wstring o;
        for (size_t i = 0; i < v.size(); i++) o += (i ? L" " : L"") + v[i];
        return o;
    };
    for (const auto& text : IniLines(raw)) {
        wstring line = Trim(text);
        if (line.empty()) {
            notes.clear();
            continue;
        }
        if (line[0] == L';') {
            notes.push_back(Trim(Strip(line, L";", true, false)));
            continue;
        }
        if (IsSection(line)) {
            section = Trim(line.substr(1, line.size() - 2));
            above = notes;
            notes.clear();
            first = true;
            continue;
        }
        size_t eq = line.find(L'=');
        if (eq != wstring::npos) {
            Setting s;
            s.section = section;
            s.key = Trim(line.substr(0, eq));
            s.value = Trim(line.substr(eq + 1));
            s.help = join(!notes.empty() ? notes : (first ? above : std::vector<wstring>{}));
            out.push_back(s);
            first = false;
        }
        notes.clear();
    }
    return true;
}

static int FindSetting(const std::vector<wstring>& lines, const wstring& section, const wstring& key) {
    wstring current, sec = Lower(section), k = Lower(key);
    for (size_t i = 0; i < lines.size(); i++) {
        wstring line = Trim(lines[i]);
        if (IsSection(line)) current = Trim(line.substr(1, line.size() - 2));
        else if (!(line.size() && line[0] == L';') && line.find(L'=') != wstring::npos && Lower(current) == sec &&
                 Lower(Trim(line.substr(0, line.find(L'=')))) == k)
            return int(i);
    }
    return -1;
}

// runtime.ini_value: the value in the code page, refused when the code page lacks one of its characters.
static bool CodePageBytes(const wstring& value, const wstring& key, std::string& out, wstring& error) {
    out.clear();
    if (value.empty()) return true;
    UINT cp = GetACP();
    bool bad = HasLoneSurrogate(value);
    if (!bad) {
        BOOL used = FALSE;
        DWORD flags = cp == CP_UTF8 ? WC_ERR_INVALID_CHARS : WC_NO_BEST_FIT_CHARS;
        int n = WideCharToMultiByte(cp, flags, value.data(), int(value.size()), nullptr, 0, nullptr,
                                    cp == CP_UTF8 ? nullptr : &used);
        if (n <= 0 || used) bad = true;
        else {
            out.resize(size_t(n));
            WideCharToMultiByte(cp, flags, value.data(), int(value.size()), &out[0], n, nullptr,
                                cp == CP_UTF8 ? nullptr : &used);
            bad = used || CodePageText(out.data(), out.size()) != value;
        }
    }
    if (bad) {
        error = key + L" holds only characters of this PC's Windows code page, which the loader and its plugins read "
                      L"their settings in; " + PyRepr(value) + L" has others";
        return false;
    }
    return true;
}

bool WriteSetting(const wstring& path, const wstring& section, const wstring& key, const wstring& value,
                  wstring& error) {
    std::string raw;
    if (!FileExists(path) || !ReadFileBytes(path, raw)) {
        error = FileName(path) + L" does not exist";
        return false;
    }
    std::string data;
    if (Utf16Mark(raw)) {
        wstring text;
        if (!Utf16Text(raw.substr(2), text, true)) {
            error = FileName(path) + L" starts with a UTF-16 mark but does not read as UTF-16";
            return false;
        }
        std::vector<wstring> lines, ends;
        TextLines(text, lines, ends);
        int i = FindSetting(lines, section, key);
        if (i < 0) {
            error = FileName(path) + L" has no " + key + L" in [" + section + L"]";
            return false;
        }
        if (HasLoneSurrogate(value)) {
            error = key + L": " + PyRepr(value) + L" is not text that can be written";
            return false;
        }
        const wstring& line = lines[size_t(i)];
        lines[size_t(i)] = Strip(line.substr(0, line.find(L'=')), L" \t", false, true) + L" = " + value;
        wstring all;
        for (size_t k = 0; k < lines.size(); k++) all += lines[k] + (k < ends.size() ? ends[k] : L"");
        data = "\xFF\xFE";
        for (wchar_t c : all) {
            data += char(c & 0xFF);
            data += char(c >> 8);
        }
    } else {
        std::vector<Span> spans = ByteLines(raw);
        std::vector<wstring> lines;
        for (const auto& s : spans) lines.push_back(CodePageText(raw.data() + s.start, s.body - s.start));
        int i = FindSetting(lines, section, key);
        if (i < 0) {
            error = FileName(path) + L" has no " + key + L" in [" + section + L"]";
            return false;
        }
        std::string bytes;
        if (!CodePageBytes(value, key, bytes, error)) return false;
        const Span& s = spans[size_t(i)];
        std::string body = raw.substr(s.start, s.body - s.start);
        std::string head = body.substr(0, body.find('='));
        while (!head.empty() && (head.back() == ' ' || head.back() == '\t')) head.pop_back();
        data = raw.substr(0, s.start) + head + " = " + bytes + raw.substr(s.body);
    }
    return WriteFileAtomic(path, data, error);
}

// -- the game folder ---------------------------------------------------------------------------------------------------

bool IsGameFolder(const wstring& dir) {
    return !dir.empty() && FileExists(JoinPath(dir, L"DDDA.exe")) && DirExists(JoinPath(dir, L"nativePC\\rom"));
}

static wstring RegString(HKEY hive, const wchar_t* key, const wchar_t* value, DWORD flags) {
    wchar_t buf[2048];
    DWORD size = sizeof buf;
    if (RegGetValueW(hive, key, value, RRF_RT_REG_SZ | flags, nullptr, buf, &size) != ERROR_SUCCESS) return L"";
    return buf;
}

// game.steam_roots and _steam_libraries: the registry (read only), the default folder, libraryfolders.vdf.
static std::vector<wstring> SteamLibraries() {
    std::vector<wstring> roots;
    for (const wchar_t* v : {L"SteamPath", L"InstallPath"}) {
        wstring r = RegString(HKEY_CURRENT_USER, L"Software\\Valve\\Steam", v, 0);
        if (!r.empty()) roots.push_back(NormalPath(r));
    }
    for (const wchar_t* v : {L"SteamPath", L"InstallPath"}) {
        wstring r = RegString(HKEY_LOCAL_MACHINE, L"SOFTWARE\\Valve\\Steam", v, RRF_SUBKEY_WOW6432KEY);
        if (!r.empty()) roots.push_back(NormalPath(r));
    }
    roots.push_back(L"C:\\Program Files (x86)\\Steam");
    std::vector<wstring> libs;
    for (const auto& root : roots) {
        libs.push_back(root);
        std::string raw;
        if (!ReadFileBytes(JoinPath(root, L"steamapps\\libraryfolders.vdf"), raw, 4u << 20)) continue;
        wstring text = FromUtf8(raw);
        const wstring tag = L"\"path\"";
        for (size_t at = text.find(tag); at != wstring::npos; at = text.find(tag, at)) {
            size_t j = at + tag.size(), ws = j;
            while (j < text.size() && IsPySpace(text[j])) j++;
            if (j == ws || j >= text.size() || text[j] != L'"') {
                at = at + 1;
                continue;
            }
            size_t close = text.find(L'"', j + 1);
            if (close == wstring::npos || close == j + 1) {
                at = at + 1;
                continue;
            }
            wstring p = text.substr(j + 1, close - j - 1);
            for (size_t k = p.find(L"\\\\"); k != wstring::npos; k = p.find(L"\\\\", k + 1)) p.erase(k, 1);
            libs.push_back(NormalPath(p));
            at = close + 1;
        }
    }
    std::vector<wstring> unique;
    std::set<wstring> seen;
    for (const auto& l : libs)
        if (seen.insert(Lower(l)).second) unique.push_back(l);
    return unique;
}

Found FindGame(const wstring& given, const wstring& exeDir) {
    Found f;
    auto attempt = [&](const wstring& dir, const wchar_t* how) {
        wstring d = NormalPath(dir);
        if (LowerAscii(FileName(d)) == L"ddda.exe" && FileExists(d)) d = ParentDir(d);
        f.tried.push_back(d);
        if (IsGameFolder(d)) {
            f.root = d;
            f.how = how;
            return true;
        }
        return false;
    };
    if (!given.empty()) {
        attempt(given, L"given");
        return f;
    }
    if (attempt(exeDir, L"next to Studio")) return f;
    wchar_t env[1024];
    DWORD n = GetEnvironmentVariableW(L"RIFTSTONE_GAME", env, DWORD(std::size(env)));
    if (n && n < std::size(env)) {
        wstring e = LowerAscii(env);
        if (e != L"ddda" && e != L"dd" && e != L"da" && e != L"ddo" && e != L"online")
            if (attempt(env, L"RIFTSTONE_GAME")) return f;
    }
    for (const auto& lib : SteamLibraries())
        if (attempt(JoinPath(lib, L"steamapps\\common\\DDDA"), L"Steam library")) return f;
    return f;
}

bool GameRunning() {
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snap == INVALID_HANDLE_VALUE) return false;
    PROCESSENTRY32W pe{};
    pe.dwSize = sizeof pe;
    bool found = false;
    for (BOOL ok = Process32FirstW(snap, &pe); ok && !found; ok = Process32NextW(snap, &pe))
        found = _wcsicmp(pe.szExeFile, L"DDDA.exe") == 0;
    CloseHandle(snap);
    return found;
}

wstring LoaderVersion(const wstring& dll) {
    std::string data;
    if (!ReadFileBytes(dll, data, 64u << 20)) return L"";
    const std::string tag = "RiftstoneLoaderVersion=";
    for (size_t at = data.find(tag); at != std::string::npos; at = data.find(tag, at + 1)) {
        size_t j = at + tag.size(), k = j;
        while (k < data.size() && k - j < 33 &&
               (isalnum(uint8_t(data[k])) || data[k] == '.' || data[k] == '-' || data[k] == '+'))
            k++;
        if (k > j && k - j <= 32 && k < data.size() && data[k] == '\0') return Widen(data.substr(j, k - j).c_str());
    }
    std::string wide;
    for (char c : std::string("Riftstone loader")) {
        wide += c;
        wide += '\0';
    }
    return data.find(wide) != std::string::npos ? L"0.1" : L"";
}

wstring StateDir(const wstring& game) { return JoinPath(game, L"riftstone"); }
wstring PluginsDir(const wstring& game) { return JoinPath(StateDir(game), L"plugins"); }
wstring OffDir(const wstring& game) { return JoinPath(PluginsDir(game), L"off"); }
wstring LogsDir(const wstring& game) { return JoinPath(StateDir(game), L"logs"); }
wstring LoaderIni(const wstring& game) { return JoinPath(game, L"riftstone_loader.ini"); }

wstring Feature::path() const { return JoinPath(dir, file); }
wstring Feature::ini() const { return JoinPath(dir, stem + L".ini"); }

// plugins._files: the .asi/.dll files of a folder, by lower-case name.
static std::vector<wstring> PluginFiles(const wstring& folder) {
    std::vector<wstring> out;
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(JoinPath(folder, L"*").c_str(), &fd);
    if (h == INVALID_HANDLE_VALUE) return out;
    do {
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) continue;
        wstring suffix = Lower(Suffix(fd.cFileName));
        if (suffix == L".asi" || suffix == L".dll") out.push_back(fd.cFileName);
    } while (FindNextFileW(h, &fd));
    FindClose(h);
    std::sort(out.begin(), out.end(), [](const wstring& a, const wstring& b) { return Lower(a) < Lower(b); });
    return out;
}

std::vector<Feature> ListFeatures(const wstring& game) {
    std::vector<Feature> rows;
    std::set<wstring> seen;
    for (int pass = 0; pass < 2; pass++) {
        wstring folder = pass == 0 ? PluginsDir(game) : OffDir(game);
        for (const auto& name : PluginFiles(folder)) {
            Feature f;
            f.stem = StemOf(name);
            if (!seen.insert(Lower(f.stem)).second) continue;
            f.file = name;
            f.dir = folder;
            f.on = pass == 0;
            const CatalogEntry* e = CatalogFind(f.stem);
            f.known = e != nullptr;
            f.title = e ? e->title : f.stem;
            f.summary = e ? e->summary : L"";
            rows.push_back(f);
        }
    }
    // by name, as profiles.installed orders them, so a feature keeps its place when it is switched
    std::stable_sort(rows.begin(), rows.end(), [](const Feature& a, const Feature& b) { return Lower(a.stem) < Lower(b.stem); });
    return rows;
}

static bool PluginName(const wstring& name) {   // plugins._check_name
    if (name.empty() || name.size() > 80 || name == L"." || name == L"..") return false;
    for (wchar_t c : name)
        if (!((c >= L'A' && c <= L'Z') || (c >= L'a' && c <= L'z') || (c >= L'0' && c <= L'9') || c == L'_' ||
              c == L'.' || c == L'-' || c == L' '))
            return false;
    return true;
}

bool Toggle(const wstring& game, const wstring& stem, bool on, bool& changed, wstring& error) {
    changed = false;
    if (GameRunning()) {
        error = wstring(kGameTitle) + L" is running: plugins load at start, so close the game first";
        return false;
    }
    if (!PluginName(stem)) {
        error = L"bad plugin name " + PyRepr(stem);
        return false;
    }
    const Feature* hit = nullptr;
    std::vector<Feature> all;
    // plugins._locate: plugins\ first, then off\ (a stem in both is found where it runs)
    for (int pass = 0; pass < 2 && !hit; pass++) {
        wstring folder = pass == 0 ? PluginsDir(game) : OffDir(game);
        for (const auto& name : PluginFiles(folder)) {
            if (Lower(StemOf(name)) != Lower(stem)) continue;
            Feature f;
            f.stem = StemOf(name);
            f.file = name;
            f.dir = folder;
            f.on = pass == 0;
            all.push_back(f);
            hit = &all.back();
            break;
        }
    }
    if (!hit) {
        error = L"no plugin named " + PyRepr(stem) + L" in " + PluginsDir(game);
        return false;
    }
    if (hit->on == on) return true;
    wstring dest = on ? PluginsDir(game) : OffDir(game);
    std::vector<std::pair<wstring, wstring>> moves;
    for (const wstring& f : {hit->path(), hit->ini()})
        if (FileExists(f)) moves.push_back({f, JoinPath(dest, FileName(f))});
    for (const auto& m : moves)
        if (PathExists(m.second)) {
            error = m.second + L" already exists; sort the two copies out first";
            return false;
        }
    if (!MakeDirs(dest)) {
        error = dest + L": " + LastError(GetLastError());
        return false;
    }
    for (const auto& m : moves)
        if (!MoveFileExW(m.first.c_str(), m.second.c_str(), MOVEFILE_REPLACE_EXISTING)) {
            error = FileName(m.first) + L": " + LastError(GetLastError());
            return false;
        }
    changed = true;
    return true;
}

// -- the last session --------------------------------------------------------------------------------------------------

static wstring IniGet(const std::vector<Setting>& s, const wchar_t* section, const wchar_t* key) {
    for (const auto& v : s)
        if (Lower(v.section) == section && Lower(v.key) == key) return v.value;
    return L"";
}

SessionInfo ReadSession(const wstring& game) {
    SessionInfo info;
    std::vector<Setting> st;
    ReadSettings(JoinPath(StateDir(game), L"runtime-state.ini"), st);
    info.safeMode = IniGet(st, L"safe_mode", L"on") == L"1";
    info.started = IniGet(st, L"session", L"started");
    info.end = IniGet(st, L"session", L"end");
    info.loader = IniGet(st, L"session", L"loader");
    long long up = 0;
    wstring u = IniGet(st, L"session", L"end_uptime_ms");
    if (!u.empty() && u.size() <= 12 && PyInt(u, up) && up >= 0) info.uptimeMs = up;
    // runtime.list_reports: "<kind>-<YYYYMMDD-HHMMSS>[-N].txt", newest first; snapshots are not problems
    wstring best, bestKey;
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(JoinPath(LogsDir(game), L"*.txt").c_str(), &fd);
    if (h != INVALID_HANDLE_VALUE) {
        do {
            if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) continue;
            wstring name = fd.cFileName;
            size_t dash = name.find(L'-');
            if (dash == wstring::npos) continue;
            wstring kind = name.substr(0, dash);
            if (kind != L"crash" && kind != L"fatal" && kind != L"hang") continue;
            wstring rest = name.substr(dash + 1);
            if (rest.size() < 19 || rest.substr(rest.size() - 4) != L".txt") continue;
            wstring stamp = rest.substr(0, 15), tail = rest.substr(15, rest.size() - 19);
            bool ok = stamp[8] == L'-';
            for (size_t i = 0; i < 15 && ok; i++)
                if (i != 8 && !(stamp[i] >= L'0' && stamp[i] <= L'9')) ok = false;
            int num = 1;
            if (ok && !tail.empty()) {
                ok = tail[0] == L'-' && (tail.size() == 2 || tail.size() == 3);
                for (size_t i = 1; i < tail.size() && ok; i++)
                    if (!(tail[i] >= L'0' && tail[i] <= L'9')) ok = false;
                if (ok) num = _wtoi(tail.c_str() + 1);
            }
            if (!ok) continue;
            wchar_t key[64];
            swprintf_s(key, L"%s-%03d-%s", stamp.c_str(), num, kind.c_str());
            if (best.empty() || wcscmp(key, bestKey.c_str()) > 0) {
                best = name;
                bestKey = key;
            }
        } while (FindNextFileW(h, &fd));
        FindClose(h);
    }
    info.newestReport = best;
    return info;
}

bool EndSafeMode(const wstring& game, wstring& error) {
    wstring path = JoinPath(StateDir(game), L"runtime-state.ini");
    if (!MakeDirs(StateDir(game)) || !WritePrivateProfileStringW(L"safe_mode", L"on", L"0", path.c_str()) ||
        !WritePrivateProfileStringW(L"session", L"early_crashes", L"0", path.c_str())) {
        error = L"runtime-state.ini: " + LastError(GetLastError());
        return false;
    }
    return true;
}

bool Launch(const wstring& game, DWORD& pid, wstring& error) {
    pid = 0;
    if (GameRunning()) {
        error = wstring(kGameTitle) + L" is running already";
        return false;
    }
    wstring exe = JoinPath(game, L"DDDA.exe");
    if (!FileExists(exe)) {
        error = L"no DDDA.exe in " + game;
        return false;
    }
    wstring cmd = L"\"" + exe + L"\"";
    STARTUPINFOW si{};
    si.cb = sizeof si;
    PROCESS_INFORMATION pi{};
    if (!CreateProcessW(exe.c_str(), &cmd[0], nullptr, nullptr, FALSE, 0, nullptr, game.c_str(), &si, &pi)) {
        error = L"DDDA.exe did not start: " + LastError(GetLastError());
        return false;
    }
    pid = pi.dwProcessId;
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return true;
}

// -- profiles (profiles.py) --------------------------------------------------------------------------------------------

static const wchar_t* const kSchema = L"riftstone-profile/1";
static const size_t kMaxBytes = 1u << 20, kMaxFeatures = 128, kMaxValues = 4096, kNameMax = 80;

size_t Profile::valueCount() const {
    size_t n = 0;
    for (const auto& s : settings)
        for (const auto& sec : s.sections) n += sec.values.size();
    return n;
}

static bool IniName(const wstring& v) {   // profiles._ini_name
    if (v.empty() || v.size() > 64 || v.front() == L' ' || v.back() == L' ') return false;
    for (wchar_t c : v)
        if (c < 0x20 || c > 0x7E || c == L';' || c == L'=' || c == L'[' || c == L']') return false;
    return true;
}

static bool TextOk(const wstring& v, bool empty, const wchar_t* what, wstring& error) {   // profiles._text
    if (v.empty() && !empty) {
        error = wstring(what) + L" is empty";
        return false;
    }
    if (CodePoints(v) > kNameMax) {
        error = wstring(what) + L" is longer than " + std::to_wstring(kNameMax) + L" characters";
        return false;
    }
    for (wchar_t c : v)
        if (c < 0x20 || (c >= 0x7F && c <= 0x9F) || IsHigh(c) || IsLow(c)) {
            if ((IsHigh(c) || IsLow(c)) && !HasLoneSurrogate(v)) continue;   // a pair is one character
            error = wstring(what) + L" holds a control character";
            return false;
        }
    return true;
}

// profiles._carried
static std::vector<ProfileSection> CarriedValues(const wstring& stem, const wstring& ini) {
    std::vector<ProfileSection> out;
    std::vector<Setting> all;
    ReadSettings(ini, all);
    std::set<std::pair<wstring, wstring>> seen;
    for (const auto& s : all) {
        auto where = std::make_pair(Lower(s.section), Lower(s.key));
        if (!seen.insert(where).second) continue;
        if (!IniName(s.section) || !IniName(s.key)) continue;
        if (!CheckFor(stem, s.section, s.key).known()) continue;
        wstring clean, err;
        if (!Validate(stem, s.section, s.key, s.value, clean, err)) continue;
        ProfileSection* sec = nullptr;
        for (auto& x : out)
            if (Lower(x.section) == where.first) sec = &x;
        if (!sec) {
            out.push_back({s.section, {}});
            sec = &out.back();
        }
        sec->values.push_back({s.key, clean});
    }
    return out;
}

static std::vector<Feature> Installed(const wstring& game) {   // profiles.installed
    std::vector<Feature> rows;
    for (auto& f : ListFeatures(game))
        if (Lower(f.stem) != kLoaderStem && PluginName(f.stem)) rows.push_back(f);
    std::sort(rows.begin(), rows.end(), [](const Feature& a, const Feature& b) { return Lower(a.stem) < Lower(b.stem); });
    return rows;
}

bool ProfileFromGame(const wstring& game, const wstring& name, Profile& p, wstring& error) {
    p = Profile();
    if (!TextOk(name, false, L"name", error)) return false;
    p.name = name;
    p.madeWith = L"Riftstone Studio " + Widen(kVersion);
    for (const auto& f : Installed(game)) {
        p.features.push_back({f.stem, f.on});
        auto values = CarriedValues(f.stem, f.ini());
        if (!values.empty()) p.settings.push_back({f.stem, values});
    }
    auto loader = CarriedValues(kLoaderStem, LoaderIni(game));
    if (!loader.empty()) p.settings.push_back({kLoaderStem, loader});
    return true;
}

// json.dumps(..., ensure_ascii=False) of a string
static void JsonString(std::string& o, const wstring& s) {
    o += '"';
    std::string u = ToUtf8(s);
    for (char ch : u) {
        uint8_t c = uint8_t(ch);
        switch (c) {
        case '"': o += "\\\""; break;
        case '\\': o += "\\\\"; break;
        case '\n': o += "\\n"; break;
        case '\r': o += "\\r"; break;
        case '\t': o += "\\t"; break;
        case '\b': o += "\\b"; break;
        case '\f': o += "\\f"; break;
        default:
            if (c < 0x20) {
                char b[8];
                snprintf(b, sizeof b, "\\u%04x", c);
                o += b;
            } else o += ch;
        }
    }
    o += '"';
}

std::string ProfileToJson(const Profile& p) {
    std::string o = "{\n  \"schema\": ";
    JsonString(o, kSchema);
    o += ",\n  \"name\": ";
    JsonString(o, p.name);
    o += ",\n  \"made_with\": ";
    JsonString(o, p.madeWith);
    o += ",\n  \"features\": ";
    if (p.features.empty()) o += "{}";
    else {
        o += "{\n";
        for (size_t i = 0; i < p.features.size(); i++) {
            o += "    ";
            JsonString(o, p.features[i].first);
            o += p.features[i].second ? ": true" : ": false";
            o += i + 1 < p.features.size() ? ",\n" : "\n";
        }
        o += "  }";
    }
    o += ",\n  \"settings\": ";
    if (p.settings.empty()) o += "{}";
    else {
        o += "{\n";
        for (size_t i = 0; i < p.settings.size(); i++) {
            const auto& st = p.settings[i];
            o += "    ";
            JsonString(o, st.stem);
            o += ": ";
            if (st.sections.empty()) o += "{}";
            else {
                o += "{\n";
                for (size_t j = 0; j < st.sections.size(); j++) {
                    const auto& sec = st.sections[j];
                    o += "      ";
                    JsonString(o, sec.section);
                    o += ": ";
                    if (sec.values.empty()) o += "{}";
                    else {
                        o += "{\n";
                        for (size_t k = 0; k < sec.values.size(); k++) {
                            o += "        ";
                            JsonString(o, sec.values[k].first);
                            o += ": ";
                            JsonString(o, sec.values[k].second);
                            o += k + 1 < sec.values.size() ? ",\n" : "\n";
                        }
                        o += "      }";
                    }
                    o += j + 1 < st.sections.size() ? ",\n" : "\n";
                }
                o += "    }";
            }
            o += i + 1 < p.settings.size() ? ",\n" : "\n";
        }
        o += "  }";
    }
    o += "\n}\n";
    return o;
}

// A JSON value as a profile can hold one: an object, a string or true/false. Numbers, null and arrays never fit a
// profile anywhere, so they are refused where they appear (Python's json reads them and its checks refuse them).
struct JVal {
    enum Kind { OBJ, STR, BOOL } kind = STR;
    wstring s;
    bool b = false;
    std::vector<wstring> keys;
    std::vector<JVal> vals;
};

class JsonReader {
public:
    explicit JsonReader(const wstring& t) : t_(t) {}
    bool Parse(JVal& v, wstring& error) {
        Ws();
        if (!Value(v, 0)) {
            error = L"not a profile: " + err_;
            return false;
        }
        Ws();
        if (i_ != t_.size()) {
            error = L"not a profile: extra data at character " + std::to_wstring(i_);
            return false;
        }
        return true;
    }

private:
    const wstring& t_;
    size_t i_ = 0;
    wstring err_;
    bool Fail(const wchar_t* what) {
        err_ = wstring(what) + L" at character " + std::to_wstring(i_);
        return false;
    }
    void Ws() {
        while (i_ < t_.size() && (t_[i_] == L' ' || t_[i_] == L'\t' || t_[i_] == L'\n' || t_[i_] == L'\r')) i_++;
    }
    bool Word(const wchar_t* w) {
        size_t n = wcslen(w);
        if (t_.compare(i_, n, w) != 0) return false;
        i_ += n;
        return true;
    }
    bool Value(JVal& v, int depth) {
        if (i_ >= t_.size()) return Fail(L"expecting a value");
        wchar_t c = t_[i_];
        if (c == L'{') return Object(v, depth + 1);
        if (c == L'"') {
            v.kind = JVal::STR;
            return String(v.s);
        }
        if (Word(L"true")) {
            v.kind = JVal::BOOL;
            v.b = true;
            return true;
        }
        if (Word(L"false")) {
            v.kind = JVal::BOOL;
            v.b = false;
            return true;
        }
        return Fail(L"a value a profile cannot hold (only objects, text, true and false)");
    }
    bool Object(JVal& v, int depth) {
        if (depth > 8) return Fail(L"nested too deep");
        v.kind = JVal::OBJ;
        i_++;
        Ws();
        if (i_ < t_.size() && t_[i_] == L'}') {
            i_++;
            return true;
        }
        for (;;) {
            Ws();
            if (i_ >= t_.size() || t_[i_] != L'"') return Fail(L"expecting a name in double quotes");
            wstring key;
            if (!String(key)) return false;
            Ws();
            if (i_ >= t_.size() || t_[i_] != L':') return Fail(L"expecting ':'");
            i_++;
            Ws();
            JVal child;
            if (!Value(child, depth)) return false;
            v.keys.push_back(key);
            v.vals.push_back(std::move(child));
            Ws();
            if (i_ < t_.size() && t_[i_] == L',') {
                i_++;
                continue;
            }
            if (i_ < t_.size() && t_[i_] == L'}') {
                i_++;
                return true;
            }
            return Fail(L"expecting ',' or '}'");
        }
    }
    static int Hex(wchar_t c) {
        if (c >= L'0' && c <= L'9') return c - L'0';
        if (c >= L'a' && c <= L'f') return c - L'a' + 10;
        if (c >= L'A' && c <= L'F') return c - L'A' + 10;
        return -1;
    }
    bool U4(size_t at, unsigned& u) {
        if (at + 4 > t_.size()) return false;
        u = 0;
        for (size_t k = 0; k < 4; k++) {
            int h = Hex(t_[at + k]);
            if (h < 0) return false;
            u = u * 16 + unsigned(h);
        }
        return true;
    }
    bool String(wstring& out) {   // json's scanstring, strict: no control characters
        out.clear();
        i_++;
        for (;;) {
            if (i_ >= t_.size()) return Fail(L"unterminated string");
            wchar_t c = t_[i_];
            if (c == L'"') {
                i_++;
                return true;
            }
            if (c < 0x20) return Fail(L"a control character in a string");
            if (c != L'\\') {
                out += c;
                i_++;
                continue;
            }
            if (i_ + 1 >= t_.size()) return Fail(L"unterminated string");
            wchar_t e = t_[i_ + 1];
            i_ += 2;
            switch (e) {
            case L'"': out += L'"'; break;
            case L'\\': out += L'\\'; break;
            case L'/': out += L'/'; break;
            case L'b': out += L'\b'; break;
            case L'f': out += L'\f'; break;
            case L'n': out += L'\n'; break;
            case L'r': out += L'\r'; break;
            case L't': out += L'\t'; break;
            case L'u': {
                unsigned u;
                if (!U4(i_, u)) return Fail(L"invalid \\uXXXX escape");
                i_ += 4;
                unsigned u2;
                if (u >= 0xD800 && u <= 0xDBFF && i_ + 1 < t_.size() && t_[i_] == L'\\' && t_[i_ + 1] == L'u' &&
                    U4(i_ + 2, u2) && u2 >= 0xDC00 && u2 <= 0xDFFF) {
                    out += wchar_t(u);
                    out += wchar_t(u2);
                    i_ += 6;
                } else out += wchar_t(u);
                break;
            }
            default:
                i_ -= 1;
                return Fail(L"invalid escape");
            }
        }
    }
};

static bool Pairs(const JVal& v, const wstring& what, wstring& error) {   // profiles._pairs
    if (v.kind != JVal::OBJ) {
        error = what + L" is an object";
        return false;
    }
    std::set<wstring> seen;
    for (const auto& k : v.keys)
        if (!seen.insert(LowerAscii(k)).second) {
            error = what + L" names " + PyRepr(k) + L" twice";
            return false;
        }
    return true;
}

bool ProfileFromJson(const std::string& bytes, Profile& p, wstring& error) {
    p = Profile();
    if (bytes.size() > kMaxBytes) {
        error = L"a profile is at most " + std::to_wstring(kMaxBytes) + L" bytes";
        return false;
    }
    std::string raw = bytes;
    if (raw.size() >= 3 && raw.compare(0, 3, "\xEF\xBB\xBF") == 0) raw.erase(0, 3);
    wstring text;
    if (!DecodeUtf8Strict(raw, text)) {
        error = L"a profile is UTF-8 text";
        return false;
    }
    JVal doc;
    if (!JsonReader(text).Parse(doc, error)) return false;
    if (!Pairs(doc, L"a profile", error)) return false;
    const JVal *schema = nullptr, *name = nullptr, *made = nullptr, *features = nullptr, *settings = nullptr;
    for (size_t i = 0; i < doc.keys.size(); i++) {
        const wstring& k = doc.keys[i];
        const JVal* v = &doc.vals[i];
        if (k == L"schema") schema = v;
        else if (k == L"name") name = v;
        else if (k == L"made_with") made = v;
        else if (k == L"features") features = v;
        else if (k == L"settings") settings = v;
        else {
            error = L"a profile has no field " + PyRepr(k);
            return false;
        }
    }
    if (!schema || schema->kind != JVal::STR || schema->s != kSchema) {
        error = wstring(L"not a ") + kSchema + L" file";
        return false;
    }
    if (!name || name->kind != JVal::STR) {
        error = L"name is text";
        return false;
    }
    if (!TextOk(name->s, false, L"name", error)) return false;
    p.name = name->s;
    if (made) {
        if (made->kind != JVal::STR) {
            error = L"made_with is text";
            return false;
        }
        if (!TextOk(made->s, true, L"made_with", error)) return false;
        p.madeWith = made->s;
    }
    if (features) {
        if (!Pairs(*features, L"features", error)) return false;
        for (size_t i = 0; i < features->keys.size(); i++) {
            const wstring& stem = features->keys[i];
            if (!PluginName(stem)) {
                error = L"features: " + PyRepr(stem) + L" is not a feature's name";
                return false;
            }
            if (features->vals[i].kind != JVal::BOOL) {
                error = L"features: " + stem + L" is true or false";
                return false;
            }
            p.features.push_back({stem, features->vals[i].b});
        }
        if (p.features.size() > kMaxFeatures) {
            error = L"a profile names at most " + std::to_wstring(kMaxFeatures) + L" features";
            return false;
        }
    }
    size_t count = 0;
    if (settings) {
        if (!Pairs(*settings, L"settings", error)) return false;
        for (size_t i = 0; i < settings->keys.size(); i++) {
            const wstring& stem = settings->keys[i];
            if (!PluginName(stem)) {
                error = L"settings: " + PyRepr(stem) + L" is not a feature's name";
                return false;
            }
            const JVal& sections = settings->vals[i];
            if (!Pairs(sections, L"settings of " + stem, error)) return false;
            ProfileStem ps{stem, {}};
            for (size_t j = 0; j < sections.keys.size(); j++) {
                const wstring& section = sections.keys[j];
                if (!IniName(section)) {
                    error = L"settings of " + stem + L": " + PyRepr(section) +
                            L" is not a section or key name of a settings file";
                    return false;
                }
                const JVal& keys = sections.vals[j];
                wstring where = L"[" + section + L"] of " + stem;
                if (!Pairs(keys, where, error)) return false;
                ProfileSection sec{section, {}};
                for (size_t k = 0; k < keys.keys.size(); k++) {
                    const wstring& key = keys.keys[k];
                    if (!IniName(key)) {
                        error = where + L": " + PyRepr(key) + L" is not a section or key name of a settings file";
                        return false;
                    }
                    const JVal& value = keys.vals[k];
                    if (value.kind != JVal::STR || CodePoints(value.s) > 256 || HasLoneSurrogate(value.s)) {
                        error = stem + L" [" + section + L"] " + key + L": the value is a short text";
                        return false;
                    }
                    sec.values.push_back({key, value.s});
                    count++;
                }
                ps.sections.push_back(sec);
            }
            p.settings.push_back(ps);
        }
    }
    if (count > kMaxValues) {
        error = L"a profile holds at most " + std::to_wstring(kMaxValues) + L" values";
        return false;
    }
    return true;
}

static bool DecodesAsIni(const wstring& ini) {
    std::string raw;
    if (!ReadFileBytes(ini, raw)) return true;
    if (!Utf16Mark(raw)) return true;
    wstring text;
    return Utf16Text(raw.substr(2), text, true);
}

bool ApplyProfile(const wstring& game, const Profile& p, bool dryRun, ApplyReport& r, wstring& error) {
    r = ApplyReport();
    if (GameRunning()) {
        error = wstring(kGameTitle) + L" is running: close the game first, then import the profile";
        return false;
    }
    std::map<wstring, Feature> where;
    for (const auto& f : Installed(game)) where[Lower(f.stem)] = f;
    std::vector<std::pair<wstring, wstring>> moves;
    std::map<wstring, wstring> later;   // stem -> where its settings file will be
    for (const auto& feat : p.features) {
        auto it = where.find(Lower(feat.first));
        if (it == where.end()) {
            r.skipped.push_back(feat.first + L": not installed here");
            continue;
        }
        const Feature& f = it->second;
        if (f.on == feat.second) {
            r.unchanged++;
            continue;
        }
        wstring dest = feat.second ? PluginsDir(game) : OffDir(game);
        for (const wstring& file : {f.path(), f.ini()}) {
            if (!FileExists(file)) continue;
            wstring target = JoinPath(dest, FileName(file));
            bool taken = PathExists(target);
            for (const auto& m : moves)
                if (Lower(m.second) == Lower(target)) taken = true;
            if (taken) {
                error = target + L" already exists; sort the two copies out first";
                return false;
            }
            moves.push_back({file, target});
        }
        r.switched++;
        later[Lower(feat.first)] = JoinPath(dest, f.stem + L".ini");
    }
    struct Pending {
        wstring path, section, key, value;
    };
    std::vector<Pending> writes;
    for (const auto& st : p.settings) {
        wstring now, after, ruleStem;
        if (Lower(st.stem) == kLoaderStem) {
            now = after = LoaderIni(game);
            ruleStem = kLoaderStem;
        } else {
            auto it = where.find(Lower(st.stem));
            if (it == where.end()) {
                r.skipped.push_back(st.stem + L": not installed here");
                continue;
            }
            now = it->second.ini();
            auto l = later.find(Lower(st.stem));
            after = l != later.end() ? l->second : now;
            ruleStem = it->second.stem;
        }
        if (!FileExists(now)) {
            r.skipped.push_back(st.stem + L": has no settings file");
            continue;
        }
        if (!DecodesAsIni(now)) {
            r.skipped.push_back(st.stem + L": " + FileName(now) + L" starts with a UTF-16 mark but does not read as UTF-16");
            continue;
        }
        std::vector<Setting> cur;
        ReadSettings(now, cur);
        std::map<std::pair<wstring, wstring>, wstring> current;
        for (const auto& s : cur) current.insert({{Lower(s.section), Lower(s.key)}, s.value});
        for (const auto& sec : st.sections)
            for (const auto& kv : sec.values) {
                wstring named = st.stem + L" [" + sec.section + L"] " + kv.first;
                auto have = current.find({Lower(sec.section), Lower(kv.first)});
                if (have == current.end()) {
                    r.skipped.push_back(named + L": not in " + FileName(now));
                    continue;
                }
                if (!CheckFor(ruleStem, sec.section, kv.first).known()) {
                    r.skipped.push_back(named + L": not a setting a profile carries");
                    continue;
                }
                wstring clean, why;
                if (!Validate(ruleStem, sec.section, kv.first, kv.second, clean, why)) {
                    r.skipped.push_back(named + L": " + why);
                    continue;
                }
                if (have->second == clean) {
                    r.unchanged++;
                    continue;
                }
                writes.push_back({after, sec.section, kv.first, clean});
            }
    }
    for (const auto& w : writes) r.writes.push_back({FileName(w.path), w.section, w.key, w.value});
    r.set = int(writes.size());
    if (dryRun) return true;
    for (const auto& m : moves)
        if (!MakeDirs(ParentDir(m.second))) {
            error = ParentDir(m.second) + L": " + LastError(GetLastError());
            return false;
        }
    for (const auto& m : moves)
        if (!MoveFileExW(m.first.c_str(), m.second.c_str(), MOVEFILE_REPLACE_EXISTING)) {
            error = FileName(m.first) + L": " + LastError(GetLastError());
            return false;
        }
    for (const auto& w : writes)
        if (!WriteSetting(w.path, w.section, w.key, w.value, error)) return false;
    return true;
}

}  // namespace studio

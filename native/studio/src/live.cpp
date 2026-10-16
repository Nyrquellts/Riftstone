// What the running game is doing: the page the loader publishes as Local\RiftstoneLive (native/loader/live.cpp),
// read and put in words the way the toolkit does (src/riftstone/runtime.py parse_live, describe_live and
// memory_verdict; native/studio/test/run_tests.py compares the two on the same pages).
#include "studio.h"

#include <algorithm>
#include <cstdio>
#include <cstring>

namespace studio {

namespace {

const uint32_t kLiveSize = 0x1000;
const char kLiveMagic[8] = {'R', 'S', 'L', 'I', 'V', 'E', '1', '\0'};

uint32_t U32(const std::string& b, size_t o) {
    uint32_t v;
    memcpy(&v, b.data() + o, 4);
    return v;
}

int32_t I32(const std::string& b, size_t o) { return int32_t(U32(b, o)); }

uint64_t U64(const std::string& b, size_t o) {
    uint64_t v;
    memcpy(&v, b.data() + o, 8);
    return v;
}

// runtime._cstr: the field up to its first NUL, as UTF-8 with Python's "replace" (one U+FFFD for each longest
// start of a sequence that cannot go on).
wstring CStr(const std::string& b, size_t off, size_t size) {
    const char* p = b.data() + off;
    const void* nul = memchr(p, 0, size);
    return DecodeUtf8Replace(std::string(p, nul ? size_t(static_cast<const char*>(nul) - p) : size));
}

wstring Num(uint64_t v) { return std::to_wstring(v); }

wstring Fixed1(double v) {
    wchar_t buf[64];
    swprintf_s(buf, L"%.1f", v);
    return buf;
}

wstring OrNone(bool have, int64_t v) { return have ? std::to_wstring(v) : L"None"; }

}  // namespace

wstring DecodeUtf8Replace(const std::string& s) {
    wstring o;
    o.reserve(s.size());
    auto put = [&](uint32_t cp) {
        if (cp >= 0x10000) {
            cp -= 0x10000;
            o += wchar_t(0xD800 + (cp >> 10));
            o += wchar_t(0xDC00 + (cp & 0x3FF));
        } else o += wchar_t(cp);
    };
    size_t i = 0, n = s.size();
    while (i < n) {
        uint8_t c = uint8_t(s[i]);
        if (c < 0x80) {
            put(c);
            i++;
            continue;
        }
        int need;
        uint32_t cp;
        uint8_t lo = 0x80, hi = 0xBF;   // the first continuation byte's range (UTF-8's own limits)
        if (c >= 0xC2 && c <= 0xDF) {
            need = 1;
            cp = c & 0x1F;
        } else if (c >= 0xE0 && c <= 0xEF) {
            need = 2;
            cp = c & 0x0F;
            if (c == 0xE0) lo = 0xA0;
            if (c == 0xED) hi = 0x9F;
        } else if (c >= 0xF0 && c <= 0xF4) {
            need = 3;
            cp = c & 0x07;
            if (c == 0xF0) lo = 0x90;
            if (c == 0xF4) hi = 0x8F;
        } else {
            put(0xFFFD);
            i++;
            continue;
        }
        size_t j = i + 1;
        bool ok = true;
        for (int k = 0; k < need; k++, j++) {
            uint8_t l = k ? 0x80 : lo, h = k ? 0xBF : hi;
            if (j >= n || uint8_t(s[j]) < l || uint8_t(s[j]) > h) {
                ok = false;
                break;
            }
            cp = (cp << 6) | (uint8_t(s[j]) & 0x3F);
        }
        put(ok ? cp : 0xFFFD);
        i = j;
    }
    return o;
}

wstring Commas(uint64_t v) {
    wstring d = std::to_wstring(v), o;
    for (size_t i = 0; i < d.size(); i++) {
        if (i && (d.size() - i) % 3 == 0) o += L',';
        o += d[i];
    }
    return o;
}

bool ParseLive(const std::string& blob, LiveStats& s, wstring& error) {
    s = LiveStats();
    if (blob.size() < kLiveSize) {
        error = L"live: shorter than one live-stats page";
        return false;
    }
    if (memcmp(blob.data(), kLiveMagic, 8) != 0) {
        error = L"live: not a Riftstone live-stats page";
        return false;
    }
    s.version = U32(blob, 0x008);
    s.size = U32(blob, 0x00C);
    if (s.version != 1 || s.size != kLiveSize) {
        error = L"live: live-stats version " + Num(s.version) + L" (size " + Num(s.size) +
                L") is not the one this Riftstone reads";
        return false;
    }
    s.pid = U32(blob, 0x010);
    s.game = U32(blob, 0x014);
    s.exeTimestamp = U32(blob, 0x018);
    s.flags = U32(blob, 0x01C);
    s.seq = U32(blob, 0x020);
    s.uptimeMs = U32(blob, 0x024);
    s.updateFiletime = U64(blob, 0x028);
    s.vaTotal = U64(blob, 0x030);
    s.vaUsed = U64(blob, 0x038);
    s.vaLargestFree = U64(blob, 0x040);
    s.vaUsedPeak = U64(blob, 0x048);
    s.vaLargestFreeMin = U64(blob, 0x050);
    s.privateBytes = U64(blob, 0x058);
    s.workingSet = U64(blob, 0x060);
    s.handles = U32(blob, 0x068);
    s.frames = U32(blob, 0x06C);
    s.frameUsLast = U32(blob, 0x070);
    s.frameUsAvg = U32(blob, 0x074);
    s.frameUsP99 = U32(blob, 0x078);
    s.frameUsMax = U32(blob, 0x07C);
    s.stutters = U32(blob, 0x080);
    s.redirects = U32(blob, 0x084);
    s.missing = U32(blob, 0x088);
    s.fallbacks = U32(blob, 0x08C);
    s.fatals = U32(blob, 0x090);
    s.pluginsLoaded = U32(blob, 0x094);
    s.pluginsNotLoaded = U32(blob, 0x098);
    s.enemiesActive = I32(blob, 0x09C);
    s.enemiesUsable = I32(blob, 0x0A0);
    s.enemySlots = I32(blob, 0x0A4);
    s.backbufferW = U32(blob, 0x0A8);
    s.backbufferH = U32(blob, 0x0AC);
    s.windowedRaw = U32(blob, 0x0B0);
    s.refreshHz = U32(blob, 0x0B4);
    s.ringPos = U32(blob, 0x0B8);
    s.vramAvailMb = U32(blob, 0x0BC);
    s.stage = I32(blob, 0x9D8);
    s.resourcesUsed = U32(blob, 0x9DC);
    s.resourceSlots = U32(blob, 0x9E0);
    s.privateBytesPeak = U64(blob, 0x9E4);
    s.memVerdict = U32(blob, 0x9EC);
    s.d3dManaged = U64(blob, 0x9F0);
    s.d3dManagedPeak = U64(blob, 0x9F8);
    s.d3dDefault = U64(blob, 0xA00);
    s.d3dSystem = U64(blob, 0xA08);
    s.d3dObjects = U32(blob, 0xA10);
    s.d3dProvider = U32(blob, 0xA14);
    s.pressure = U32(blob, 0xA18);
    s.pressureEpisodes = U32(blob, 0xA1C);
    s.loaderVersion = CStr(blob, 0x4C0, 16);
    s.lastFile = CStr(blob, 0x4D0, 260);
    s.lastFallback = CStr(blob, 0x5D4, 260);
    s.plugins = CStr(blob, 0x6D8, 512);
    s.notes = CStr(blob, 0x8D8, 256);
    s.d3dPath = CStr(blob, 0xA20, 160);
    uint32_t pos = s.ringPos & 255, frames = std::min<uint32_t>(s.frames, 256);
    for (uint32_t i = 256 - frames; i < 256; i++) s.frameTimesUs.push_back(U32(blob, 0x0C0 + 4 * ((pos + i) & 255)));
    size_t start = 0;
    for (;;) {   // "name=state;..."
        size_t end = s.plugins.find(L';', start);
        wstring part = s.plugins.substr(start, end == wstring::npos ? wstring::npos : end - start);
        size_t eq = part.find(L'=');
        wstring name = part.substr(0, eq), state = eq == wstring::npos ? L"" : part.substr(eq + 1);
        if (!name.empty()) s.pluginList.push_back({name, state.empty() ? L"?" : state});
        if (end == wstring::npos) break;
        start = end + 1;
    }
    return true;
}

wstring LiveStats::GameName() const {
    switch (game) {
    case 0: return L"another program";
    case 1: return L"Dragon's Dogma: Dark Arisen";
    case 2: return L"Dragon's Dogma Online";
    }
    return L"game " + Num(game);
}

const wchar_t* LiveStats::ProviderName() const {
    switch (d3dProvider) {
    case 1: return L"Windows' own";
    case 2: return L"chained ([d3d9] chain)";
    case 3: return L"a d3d9.dll in the game folder";
    case 4: return L"another module";
    }
    return nullptr;
}

wstring LiveStats::Fps() const { return frameUsAvg ? Fixed1(1000000.0 / double(frameUsAvg)) : L"None"; }

int MemoryVerdict(uint64_t peakMb, uint64_t freeMinMb, uint64_t vaLeftMb, bool offloaded, wstring& advice) {
    if (!peakMb) {
        advice = L"Memory: not measured yet \x2014 play a memory-heavy setup for a while, then check again.";
        return 0;
    }
    bool vaShort = vaLeftMb && vaLeftMb < 400;
    if (peakMb >= 3400 || (freeMinMb && freeMinMb <= 128) || vaShort) {
        advice = L"Memory-bound: peak commit " + Commas(peakMb) + L" MB" +
                 (freeMinMb ? L" (smallest free block " + Commas(freeMinMb) + L" MB)" : L"") +
                 (vaShort ? L", " + Commas(vaLeftMb) + L" MB of address space left at the least" : L"") +
                 L" \x2014 at the ~4 GB a 32-bit process cannot exceed. Only fewer in-process texture bytes help: a "
                 L"lower TextureDetail, fewer or smaller HD texture mods" +
                 (offloaded ? L"" : L", or DXVK through [d3d9] chain ('riftstone loader d3d9'), which keeps "
                                    L"Direct3D's copy of the game's textures out of the address space") +
                 L". Raising internal pools would make it worse.";
        return 3;
    }
    if (peakMb >= 2800) {
        advice = L"Memory tight: peak commit " + Commas(peakMb) + L" MB \x2014 close to the ~4 GB ceiling. A heavier "
                 L"area could still crash; lowering TextureDetail or trimming texture mods buys room. Raising pools "
                 L"would not help.";
        return 2;
    }
    advice = L"Memory headroom: peak commit " + Commas(peakMb) + L" MB \x2014 well under the ~4 GB a 32-bit process "
             L"can address. Higher TextureDetail, more texture mods, or raised internal pools are safe here.";
    return 1;
}

std::vector<wstring> DescribeLive(const LiveStats& s) {
    const uint64_t mb = 1 << 20;
    std::vector<wstring> lines;
    lines.push_back(s.GameName() + L", process " + Num(s.pid) + L", up " + Num(s.uptimeMs / 60000) + L" min " +
                    Num((s.uptimeMs / 1000) % 60) + L" s (loader " + s.loaderVersion + L")");
    if (s.Flag(LiveStats::EXITED)) lines.push_back(L"the game has exited normally");
    if (s.Flag(LiveStats::SAFE_MODE))
        lines.push_back(L"SAFE MODE: running without plugins and mods (two start-up crashes in a row)");
    if (s.vaTotal) {
        lines.push_back(L"address space " + Num(s.vaUsed / mb) + L" MB of " + Num(s.vaTotal / mb) + L" MB used (peak " +
                        Num(s.vaUsedPeak / mb) + L" MB); largest free block " + Num(s.vaLargestFree / mb) + L" MB");
        if (s.Flag(LiveStats::ADDRESS_SPACE_LOW))
            lines.push_back(L"  LOW: the game is close to its 4 GB limit; a crash is likely soon");
        if (s.vaTotal < (3ull << 30))
            lines.push_back(L"  only " + Num(s.vaTotal / mb) +
                            L" MB of address space: the exe is not large-address aware ('riftstone laa' checks it)");
        uint64_t peakMb = s.privateBytesPeak >> 20;
        if (peakMb) {
            uint64_t left = s.vaUsedPeak && s.vaTotal >= s.vaUsedPeak ? (s.vaTotal - s.vaUsedPeak) >> 20 : 0;
            wstring advice;
            MemoryVerdict(peakMb, s.vaLargestFreeMin >> 20, left, s.d3dProvider == 2 || s.d3dProvider == 3, advice);
            lines.push_back(L"  " + advice);
        }
        if (s.Flag(LiveStats::MEMORY_PRESSURE))
            lines.push_back(L"  MEMORY PRESSURE now (began " + Num(s.pressureEpisodes) +
                            L" time(s) this session): loader.log says what holds the memory");
        else if (s.pressureEpisodes)
            lines.push_back(L"  memory pressure " + Num(s.pressureEpisodes) + L" time(s) this session, eased now");
    }
    bool counted = s.Flag(LiveStats::D3D_COUNTED);
    if (s.ProviderName() || counted) {
        wstring text = L"Direct3D 9: " + wstring(s.ProviderName() ? s.ProviderName() : L"not created yet");
        if (!s.d3dPath.empty()) text += L" (" + s.d3dPath + L")";
        if (counted)
            text += L"; managed textures and buffers " + Commas(s.d3dManaged / mb) + L" MB (peak " +
                    Commas(s.d3dManagedPeak / mb) + L" MB), video memory " + Commas(s.d3dDefault / mb) +
                    L" MB, system memory " + Commas(s.d3dSystem / mb) + L" MB; " + Commas(s.d3dObjects) + L" objects";
        lines.push_back(text);
        if (counted && s.d3dProvider == 1 && s.d3dManaged >= 256 * mb)
            lines.push_back(L"  Windows keeps a copy of the managed ones inside the game's address space; DXVK through "
                            L"[d3d9] chain keeps it out ('riftstone loader d3d9')");
    }
    if (s.Flag(LiveStats::FRAME_TIMING) && s.frames)
        lines.push_back(L"frames " + Num(s.frames) + L": " + s.Fps() + L" fps average, " +
                        Fixed1(s.frameUsAvg / 1000.0) + L" ms (99% under " + Fixed1(s.frameUsP99 / 1000.0) +
                        L" ms, worst " + Fixed1(s.frameUsMax / 1000.0) + L" ms), " + Num(s.stutters) + L" stutters");
    else if (s.Flag(LiveStats::FRAME_TIMING))
        lines.push_back(L"frames: none drawn yet");
    if (s.backbufferW) {
        const wchar_t* mode = s.windowedRaw == 0xFFFFFFFF ? L"?" : s.windowedRaw ? L"windowed" : L"fullscreen";
        lines.push_back(L"display " + Num(s.backbufferW) + L"x" + Num(s.backbufferH) + L" " + mode +
                        (s.refreshHz ? L", " + Num(s.refreshHz) + L" Hz" : L"") +
                        (s.vramAvailMb ? L"; Direct3D reports " + Num(s.vramAvailMb) + L" MB of texture memory free"
                                       : L""));
    }
    if (s.stage >= 0) lines.push_back(L"stage " + std::to_wstring(s.stage));
    if (s.resourceSlots) {
        uint64_t full = uint64_t(s.resourcesUsed) * 100 / s.resourceSlots;
        lines.push_back(L"resource table " + Commas(s.resourcesUsed) + L" of " + Commas(s.resourceSlots) + L" slots (" +
                        Num(full) + L"%)" + (full >= 90 ? L"; FULL buckets make the game load resources again" : L""));
    }
    if (s.enemiesActive >= 0)
        lines.push_back(L"enemies active " + std::to_wstring(s.enemiesActive) + L" of " +
                        OrNone(s.enemiesUsable >= 0, s.enemiesUsable) + L" usable (" +
                        OrNone(s.enemySlots >= 0, s.enemySlots) + L" slots)");
    lines.push_back(L"overlay redirects " + Num(s.redirects) + L", missing files " + Num(s.missing) +
                    L", texture stand-ins " + Num(s.fallbacks) + L", fatal errors " + Num(s.fatals));
    if (!s.pluginList.empty()) {
        wstring t = L"plugins: ";
        for (size_t i = 0; i < s.pluginList.size(); i++)
            t += (i ? L", " : L"") + s.pluginList[i].first + L" (" + s.pluginList[i].second + L")";
        lines.push_back(t);
    }
    if (s.Flag(LiveStats::HANG)) lines.push_back(L"HANG: no frame for a while; a hang report is in riftstone\\logs");
    if (!s.notes.empty()) lines.push_back(L"note: " + s.notes);
    if (!s.lastFallback.empty()) lines.push_back(L"last stand-in: " + s.lastFallback);
    return lines;
}

bool ReadLiveMapping(const wstring& name, std::string& blob) {
    HANDLE h = OpenFileMappingW(FILE_MAP_READ, FALSE, name.c_str());
    if (!h) return false;
    const volatile uint8_t* view = static_cast<const volatile uint8_t*>(MapViewOfFile(h, FILE_MAP_READ, 0, 0, kLiveSize));
    bool ok = false;
    if (view) {
        // A seqlock: the loader makes seq odd while it writes; a copy between two equal, even reads is whole.
        blob.assign(kLiveSize, '\0');
        auto seq = [&]() { return *reinterpret_cast<const volatile uint32_t*>(view + 0x20); };
        for (int tries = 0; tries < 50 && !ok; tries++) {
            uint32_t a = seq();
            if (a & 1) {
                Sleep(2);
                continue;
            }
            MemoryBarrier();
            for (uint32_t i = 0; i < kLiveSize; i++) blob[i] = char(view[i]);
            MemoryBarrier();
            ok = seq() == a;
        }
        if (!ok) {   // the writer never paused: the last copy, as the toolkit takes it
            for (uint32_t i = 0; i < kLiveSize; i++) blob[i] = char(view[i]);
            ok = true;
        }
        UnmapViewOfFile(const_cast<uint8_t*>(view));
    }
    CloseHandle(h);
    return ok;
}

bool ReadLive(DWORD pid, LiveStats& s, const wstring& name) {
    std::vector<wstring> names;
    wstring base = name.empty() ? kLiveName : name;
    if (pid) names.push_back(base + L"-" + std::to_wstring(pid));
    names.push_back(base);
    for (const auto& n : names) {
        std::string blob;
        wstring error;
        if (!ReadLiveMapping(n, blob) || !ParseLive(blob, s, error)) continue;
        if (pid && s.pid != pid) continue;
        return true;
    }
    s = LiveStats();
    return false;
}

bool RequestSnapshot(DWORD pid, wstring& error) {
    wstring name = L"Local\\RiftstoneSnapshot-" + std::to_wstring(pid);
    HANDLE e = OpenEventW(EVENT_MODIFY_STATE, FALSE, name.c_str());
    if (!e) {
        error = L"process " + std::to_wstring(pid) +
                L" takes no snapshot requests: its loader is older than snapshots (1.0.2), or its live stats are off "
                L"([live] enabled = 0)";
        return false;
    }
    BOOL ok = SetEvent(e);
    CloseHandle(e);
    if (!ok) error = L"the snapshot request could not be sent";
    return ok != FALSE;
}

}  // namespace studio

// resources.cpp -- a resource the game asks for as a loose file before its archive was read, given its own bytes
//
// Dark Arisen opens a resource as a loose file under nativePC when it is not in the resource table yet
// (MtFile::open, 0x00D0D0C0), and a loose file that is not there is fatal: "Failed open file. %s %d", a
// "Fatal error." box, exit(1) (docs/runtime.md).  That is how the game stops when a cutscene is skipped or
// a slow drive has not delivered an archive in time; players see it at the ending's cutscenes ("Failed open
// file. ...\nativePC\id\credit_02\credit2_01_99.gmd 3": that resource is in rom\stage\stage800\stage802.arc
// and never loose).
//
// [guard] from_archives = 1 answers such an open with the resource's own bytes, read from the archive that
// holds it: first the archives the game opened most recently (the one it is streaming), then every archive
// under nativePC, whose directories are read once and kept (a table of their names' hashes).  A mod's copy
// of an archive in riftstone\overlay is read instead of the game's, as the game itself would read it.  The
// bytes go to riftstone\standin\nativePC\<path> (reused while they are the same) and are opened read-only.
// Only a resource type the game files by extension (restypes.inc) is looked for, and never an .arc: an
// archive the game cannot find is not a resource inside another.  Dark Arisen only: Online's archives are
// encrypted.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>
#include <wctype.h>

#include "runtime.h"

static BOOL g_fromArchives = FALSE;
static SRWLOCK g_resLock = SRWLOCK_INIT;       // one miss at a time: they are rare, and the index is built once

// ---------------------------------------------------------------------------
// zlib (RFC 1950) around deflate (RFC 1951): every payload of a Dark Arisen archive is one such stream

struct Inflater {
    const BYTE* src;
    size_t srcLen, srcPos;
    BYTE* dst;
    size_t dstLen, dstPos;
    uint32_t bits;
    int count;
};

// The next n bits (n <= 16), least significant first; FALSE past the end of the input.
static BOOL Bits(Inflater& s, int n, uint32_t* out) {
    uint32_t v = s.bits;
    while (s.count < n) {
        if (s.srcPos >= s.srcLen) return FALSE;
        v |= (uint32_t)s.src[s.srcPos++] << s.count;
        s.count += 8;
    }
    s.bits = n ? v >> n : v;
    s.count -= n;
    *out = n ? v & ((1u << n) - 1) : 0;
    return TRUE;
}

struct Huffman {
    short count[16];                           // codes of each length
    short symbol[288];                         // symbols, ordered by code
};

// A canonical code from its lengths.  FALSE when it is over-subscribed; an incomplete code is kept (a
// code nobody sends fails in Decode).
static BOOL Build(Huffman& h, const BYTE* lengths, int n) {
    for (int i = 0; i < 16; i++) h.count[i] = 0;
    for (int i = 0; i < n; i++) h.count[lengths[i]]++;
    if (h.count[0] == n) return TRUE;
    int left = 1;
    for (int len = 1; len < 16; len++) {
        left = (left << 1) - h.count[len];
        if (left < 0) return FALSE;
    }
    short offs[16];
    offs[1] = 0;
    for (int len = 1; len < 15; len++) offs[len + 1] = (short)(offs[len] + h.count[len]);
    for (int i = 0; i < n; i++)
        if (lengths[i]) h.symbol[offs[lengths[i]]++] = (short)i;
    return TRUE;
}

static int Decode(Inflater& s, const Huffman& h) {
    int code = 0, first = 0, index = 0;
    for (int len = 1; len < 16; len++) {
        uint32_t bit;
        if (!Bits(s, 1, &bit)) return -1;
        code |= (int)bit;
        int n = h.count[len];
        if (code - n < first) return h.symbol[index + (code - first)];
        index += n;
        first = (first + n) << 1;
        code <<= 1;
    }
    return -1;
}

static const short LBASE[29] = {3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43, 51, 59, 67, 83, 99,
                                115, 131, 163, 195, 227, 258};
static const BYTE LEXTRA[29] = {0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0};
static const unsigned short DBASE[30] = {1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385, 513, 769,
                                         1025, 1537, 2049, 3073, 4097, 6145, 8193, 12289, 16385, 24577};
static const BYTE DEXTRA[30] = {0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13};

static BOOL Codes(Inflater& s, const Huffman& lit, const Huffman& dist) {
    for (;;) {
        int sym = Decode(s, lit);
        if (sym < 0) return FALSE;
        if (sym < 256) {
            if (s.dstPos >= s.dstLen) return FALSE;
            s.dst[s.dstPos++] = (BYTE)sym;
            continue;
        }
        if (sym == 256) return TRUE;
        sym -= 257;
        if (sym >= 29) return FALSE;
        uint32_t extra;
        if (!Bits(s, LEXTRA[sym], &extra)) return FALSE;
        size_t len = (size_t)LBASE[sym] + extra;
        int d = Decode(s, dist);
        if (d < 0 || d >= 30) return FALSE;
        if (!Bits(s, DEXTRA[d], &extra)) return FALSE;
        size_t back = (size_t)DBASE[d] + extra;
        if (back > s.dstPos || len > s.dstLen - s.dstPos) return FALSE;
        BYTE* to = s.dst + s.dstPos;
        const BYTE* from = to - back;
        for (size_t i = 0; i < len; i++) to[i] = from[i];      // may overlap: byte by byte
        s.dstPos += len;
    }
}

static BOOL Stored(Inflater& s) {
    s.bits = 0;                                // the rest of the current byte
    s.count = 0;
    if (s.srcLen - s.srcPos < 4) return FALSE;
    unsigned len = s.src[s.srcPos] | (s.src[s.srcPos + 1] << 8);
    unsigned nlen = s.src[s.srcPos + 2] | (s.src[s.srcPos + 3] << 8);
    s.srcPos += 4;
    if (len != (~nlen & 0xFFFFu)) return FALSE;
    if (s.srcLen - s.srcPos < len || s.dstLen - s.dstPos < len) return FALSE;
    memcpy(s.dst + s.dstPos, s.src + s.srcPos, len);
    s.srcPos += len;
    s.dstPos += len;
    return TRUE;
}

static BOOL Fixed(Inflater& s) {
    BYTE lengths[288];
    int i = 0;
    for (; i < 144; i++) lengths[i] = 8;
    for (; i < 256; i++) lengths[i] = 9;
    for (; i < 280; i++) lengths[i] = 7;
    for (; i < 288; i++) lengths[i] = 8;
    Huffman lit, dist;
    Build(lit, lengths, 288);
    for (i = 0; i < 30; i++) lengths[i] = 5;
    Build(dist, lengths, 30);
    return Codes(s, lit, dist);
}

static BOOL Dynamic(Inflater& s) {
    static const BYTE ORDER[19] = {16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15};
    uint32_t v;
    if (!Bits(s, 5, &v)) return FALSE;
    int nlen = (int)v + 257;
    if (!Bits(s, 5, &v)) return FALSE;
    int ndist = (int)v + 1;
    if (!Bits(s, 4, &v)) return FALSE;
    int ncode = (int)v + 4;
    if (nlen > 286 || ndist > 30) return FALSE;
    BYTE lengths[320] = {0};
    for (int i = 0; i < ncode; i++) {
        if (!Bits(s, 3, &v)) return FALSE;
        lengths[ORDER[i]] = (BYTE)v;
    }
    Huffman lencode;
    if (!Build(lencode, lengths, 19)) return FALSE;
    int index = 0;
    while (index < nlen + ndist) {
        int sym = Decode(s, lencode);
        if (sym < 0) return FALSE;
        if (sym < 16) {
            lengths[index++] = (BYTE)sym;
            continue;
        }
        BYTE len = 0;
        int repeat;
        if (sym == 16) {
            if (index == 0) return FALSE;
            len = lengths[index - 1];
            if (!Bits(s, 2, &v)) return FALSE;
            repeat = 3 + (int)v;
        } else if (sym == 17) {
            if (!Bits(s, 3, &v)) return FALSE;
            repeat = 3 + (int)v;
        } else {
            if (!Bits(s, 7, &v)) return FALSE;
            repeat = 11 + (int)v;
        }
        if (index + repeat > nlen + ndist) return FALSE;
        while (repeat--) lengths[index++] = len;
    }
    if (lengths[256] == 0) return FALSE;       // no end-of-block code
    Huffman lit, dist;
    if (!Build(lit, lengths, nlen) || !Build(dist, lengths + nlen, ndist)) return FALSE;
    return Codes(s, lit, dist);
}

static uint32_t Adler32(const BYTE* p, size_t n) {
    uint32_t a = 1, b = 0;
    while (n) {
        size_t k = n < 5552 ? n : 5552;
        n -= k;
        while (k--) {
            a += *p++;
            b += a;
        }
        a %= 65521;
        b %= 65521;
    }
    return (b << 16) | a;
}

// TRUE when src is one zlib stream that decodes to exactly dstLen bytes and its Adler-32 matches.
static BOOL ZlibInflate(const BYTE* src, size_t srcLen, BYTE* dst, size_t dstLen) {
    if (srcLen < 6) return FALSE;
    unsigned cmf = src[0], flg = src[1];
    if ((cmf & 0x0F) != 8 || (cmf >> 4) > 7 || ((cmf << 8) | flg) % 31 != 0 || (flg & 0x20)) return FALSE;
    Inflater s = {src, srcLen, 2, dst, dstLen, 0, 0, 0};
    uint32_t last = 0, type = 0;
    do {
        if (!Bits(s, 1, &last) || !Bits(s, 2, &type)) return FALSE;
        BOOL ok = type == 0 ? Stored(s) : type == 1 ? Fixed(s) : type == 2 ? Dynamic(s) : FALSE;
        if (!ok) return FALSE;
    } while (!last);
    if (s.dstPos != dstLen || srcLen - s.srcPos < 4) return FALSE;
    const BYTE* t = src + s.srcPos;                 // the bits left over pad the last byte read
    uint32_t want = ((uint32_t)t[0] << 24) | ((uint32_t)t[1] << 16) | ((uint32_t)t[2] << 8) | t[3];
    return Adler32(dst, dstLen) == want;
}

// ---------------------------------------------------------------------------
// archives: "ARC\0", u16 version 7, u16 count, count x 80-byte entries, payloads (src/riftstone/arc.py)

#pragma pack(push, 1)
struct ArcHeader {
    char magic[4];
    uint16_t version, count;
};
struct ArcEntry {
    char name[64];
    uint32_t type, stored, decoded, offset;
};
#pragma pack(pop)

static const uint32_t SIZE_MASK = 0x1FFFFFFF;
static const uint32_t MAX_BYTES = 256u * 1024 * 1024;    // the largest vanilla resource is ~21 MiB (arc.py)

static const struct {
    const wchar_t* ext;
    uint32_t type;
} TYPES[] = {
#include "restypes.inc"
};

// An archive's directory, read whole; NULL when the file is not a Dark Arisen archive.
static ArcEntry* ReadDirectory(const wchar_t* path, DWORD* count, LONGLONG* fileSize) {
    HANDLE h = Real_CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, NULL,
                                OPEN_EXISTING, FILE_FLAG_SEQUENTIAL_SCAN, NULL);
    if (h == INVALID_HANDLE_VALUE) return NULL;
    ArcEntry* es = NULL;
    ArcHeader hd;
    DWORD got = 0;
    LARGE_INTEGER size;
    if (GetFileSizeEx(h, &size) && ReadFile(h, &hd, sizeof hd, &got, NULL) && got == sizeof hd &&
        memcmp(hd.magic, "ARC\0", 4) == 0 && hd.version == 7 && hd.count &&
        (LONGLONG)sizeof hd + (LONGLONG)hd.count * (LONGLONG)sizeof(ArcEntry) <= size.QuadPart) {
        DWORD bytes = (DWORD)hd.count * (DWORD)sizeof(ArcEntry);
        es = (ArcEntry*)HeapAlloc(GetProcessHeap(), 0, bytes);
        if (es && !(ReadFile(h, es, bytes, &got, NULL) && got == bytes)) {
            HeapFree(GetProcessHeap(), 0, es);
            es = NULL;
        }
        *count = hd.count;
        *fileSize = size.QuadPart;
    }
    CloseHandle(h);
    return es;
}

static int FindEntry(const ArcEntry* es, DWORD count, const char* name, const uint32_t* types, int ntypes) {
    for (DWORD i = 0; i < count; i++) {
        if (es[i].name[63] != 0 || _stricmp(es[i].name, name) != 0) continue;
        for (int t = 0; t < ntypes; t++)
            if (es[i].type == types[t]) return (int)i;
    }
    return -1;
}

// The decoded bytes of entry e of the archive at path (HeapAlloc'd), or NULL.
static BYTE* ReadEntry(const wchar_t* path, const ArcEntry& e, LONGLONG fileSize, DWORD* len) {
    uint32_t decoded = e.decoded & SIZE_MASK;
    if (e.stored == 0 || e.stored > MAX_BYTES || decoded > MAX_BYTES || (LONGLONG)e.offset + e.stored > fileSize)
        return NULL;
    HANDLE h = Real_CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, NULL,
                                OPEN_EXISTING, 0, NULL);
    if (h == INVALID_HANDLE_VALUE) return NULL;
    BYTE* src = (BYTE*)HeapAlloc(GetProcessHeap(), 0, e.stored);
    BYTE* dst = (BYTE*)HeapAlloc(GetProcessHeap(), 0, decoded ? decoded : 1);
    DWORD got = 0;
    LARGE_INTEGER at;
    at.QuadPart = e.offset;
    BOOL ok = src && dst && SetFilePointerEx(h, at, NULL, FILE_BEGIN) && ReadFile(h, src, e.stored, &got, NULL) &&
              got == e.stored && ZlibInflate(src, e.stored, dst, decoded);
    CloseHandle(h);
    if (src) HeapFree(GetProcessHeap(), 0, src);
    if (!ok) {
        if (dst) HeapFree(GetProcessHeap(), 0, dst);
        return NULL;
    }
    *len = decoded;
    return dst;
}

// The file the game would read for an archive: the overlay's copy when the overlay serves one.
static void ArchiveSource(const wchar_t* nativeFull, wchar_t* out, DWORD cap) {
    const wchar_t* overlay = OverlayRootIfOn();
    const wchar_t* prefix = NativePrefix();
    size_t n = wcslen(prefix);
    if (overlay && _wcsnicmp(nativeFull, prefix, n) == 0 &&
        _snwprintf_s(out, cap, _TRUNCATE, L"%s%s", overlay, nativeFull + n) > 0) {
        DWORD a = GetFileAttributesW(out);
        if (a != INVALID_FILE_ATTRIBUTES && !(a & FILE_ATTRIBUTE_DIRECTORY)) return;
    }
    wcsncpy_s(out, cap, nativeFull, _TRUNCATE);
}

// ---- the index: every archive's names, hashed, built the first time the recent archives do not have it ----

struct Slot {
    uint32_t hash;
    uint32_t archive;
};

static wchar_t* g_paths;                       // every archive's source file, one after another
static DWORD* g_pathAt;                        // where each starts in g_paths
static DWORD g_archives, g_pathsCap, g_pathsUsed, g_pathAtCap;
static Slot* g_slots;
static DWORD g_slotCount, g_slotCap;
static BOOL g_indexed;

static uint32_t NameHash(const char* name, uint32_t type) {
    uint32_t h = 2166136261u;
    for (const char* p = name; *p; ++p) {
        char c = *p;
        if (c >= 'A' && c <= 'Z') c = (char)(c - 'A' + 'a');
        h = (h ^ (BYTE)c) * 16777619u;
    }
    for (int i = 0; i < 4; i++) h = (h ^ ((type >> (8 * i)) & 0xFF)) * 16777619u;
    return h;
}

static BOOL Grow(void** p, DWORD* cap, DWORD need, DWORD unit) {
    if (need <= *cap) return TRUE;
    DWORD c = *cap ? *cap : 1024;
    while (c < need) c *= 2;
    void* q = *p ? HeapReAlloc(GetProcessHeap(), 0, *p, (SIZE_T)c * unit) : HeapAlloc(GetProcessHeap(), 0, (SIZE_T)c * unit);
    if (!q) return FALSE;
    *p = q;
    *cap = c;
    return TRUE;
}

static void AddArchive(const wchar_t* source) {
    DWORD count = 0;
    LONGLONG size = 0;
    ArcEntry* es = ReadDirectory(source, &count, &size);
    if (!es) return;
    DWORD len = (DWORD)wcslen(source) + 1;
    if (Grow((void**)&g_paths, &g_pathsCap, g_pathsUsed + len, sizeof(wchar_t)) &&
        Grow((void**)&g_pathAt, &g_pathAtCap, g_archives + 1, sizeof(DWORD)) &&
        Grow((void**)&g_slots, &g_slotCap, g_slotCount + count, sizeof(Slot))) {
        memcpy(g_paths + g_pathsUsed, source, len * sizeof(wchar_t));
        g_pathAt[g_archives] = g_pathsUsed;
        g_pathsUsed += len;
        for (DWORD i = 0; i < count; i++) {
            if (es[i].name[63] != 0) continue;
            g_slots[g_slotCount].hash = NameHash(es[i].name, es[i].type);
            g_slots[g_slotCount].archive = g_archives;
            g_slotCount++;
        }
        g_archives++;
    }
    HeapFree(GetProcessHeap(), 0, es);
}

// Every *.arc under dir (recursively): the game's, each read from the overlay when it has a copy; then the
// archives only the overlay has.
static void Walk(const wchar_t* dir, BOOL overlayPass) {
    wchar_t pattern[MAX_PATH * 2];
    if (_snwprintf_s(pattern, _countof(pattern), _TRUNCATE, L"%s*", dir) < 0) return;
    WIN32_FIND_DATAW fd;
    HANDLE f = FindFirstFileExW(pattern, FindExInfoBasic, &fd, FindExSearchNameMatch, NULL, FIND_FIRST_EX_LARGE_FETCH);
    if (f == INVALID_HANDLE_VALUE) return;
    do {
        if (wcscmp(fd.cFileName, L".") == 0 || wcscmp(fd.cFileName, L"..") == 0) continue;
        wchar_t path[MAX_PATH * 2];
        if (_snwprintf_s(path, _countof(path), _TRUNCATE, L"%s%s", dir, fd.cFileName) < 0) continue;
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
            if (fd.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) continue;
            wcscat_s(path, _countof(path), L"\\");
            Walk(path, overlayPass);
            continue;
        }
        const wchar_t* dot = wcsrchr(fd.cFileName, L'.');
        if (!dot || _wcsicmp(dot, L".arc") != 0) continue;
        if (!overlayPass) {
            wchar_t source[MAX_PATH * 2];
            ArchiveSource(path, source, _countof(source));
            AddArchive(source);
        } else {
            // an archive only a mod has (no game copy under nativePC)
            const wchar_t* overlay = OverlayRootIfOn();
            wchar_t native[MAX_PATH * 2];
            if (!overlay || _snwprintf_s(native, _countof(native), _TRUNCATE, L"%s%s", NativePrefix(),
                                         path + wcslen(overlay)) < 0) continue;
            if (GetFileAttributesW(native) == INVALID_FILE_ATTRIBUTES) AddArchive(path);
        }
    } while (FindNextFileW(f, &fd));
    FindClose(f);
}

// By hash, then by the order the archives were found in: of two archives with one resource, the first.
static int CompareSlots(const void* a, const void* b) {
    const Slot* x = (const Slot*)a;
    const Slot* y = (const Slot*)b;
    if (x->hash != y->hash) return x->hash < y->hash ? -1 : 1;
    return x->archive < y->archive ? -1 : x->archive > y->archive ? 1 : 0;
}

static void BuildIndex() {
    if (g_indexed) return;
    g_indexed = TRUE;                          // once, whatever it finds
    DWORD t0 = GetTickCount();
    Walk(NativePrefix(), FALSE);
    if (const wchar_t* overlay = OverlayRootIfOn()) Walk(overlay, TRUE);
    if (g_slotCount) qsort(g_slots, g_slotCount, sizeof(Slot), CompareSlots);
    LogLine(L"guard    read the directories of %lu archives (%lu resources) in %lu ms, to find resources the game "
            L"asks for loose", g_archives, g_slotCount, GetTickCount() - t0);
}

// ---------------------------------------------------------------------------

static BOOL SameBytes(const wchar_t* path, const BYTE* data, DWORD len) {
    HANDLE h = Real_CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, NULL,
                                OPEN_EXISTING, 0, NULL);
    if (h == INVALID_HANDLE_VALUE) return FALSE;
    LARGE_INTEGER size;
    BOOL same = GetFileSizeEx(h, &size) && size.QuadPart == len;
    BYTE buf[65536];
    for (DWORD at = 0; same && at < len;) {
        DWORD want = len - at < sizeof buf ? len - at : (DWORD)sizeof buf, got = 0;
        same = ReadFile(h, buf, want, &got, NULL) && got == want && memcmp(buf, data + at, got) == 0;
        at += got;
    }
    CloseHandle(h);
    return same;
}

// riftstone\standin\nativePC\<rel> holding exactly these bytes (written under a name of its own, then moved
// into place, so no reader sees half a file); FALSE when it cannot be made.
static BOOL PlaceBytes(const wchar_t* rel, const BYTE* data, DWORD len, wchar_t* target, DWORD cap) {
    if (_snwprintf_s(target, cap, _TRUNCATE, L"%s\\standin\\nativePC\\%s", g_stateDir, rel) < 0) return FALSE;
    if (SameBytes(target, data, len)) return TRUE;
    for (wchar_t* p = target + wcslen(g_stateDir) + 1; (p = wcschr(p, L'\\')) != NULL; ++p) {
        *p = 0;
        CreateDirectoryW(target, NULL);
        *p = L'\\';
    }
    wchar_t temp[MAX_PATH * 2];
    if (_snwprintf_s(temp, _countof(temp), _TRUNCATE, L"%s.%lu.tmp", target, GetCurrentProcessId()) < 0) return FALSE;
    HANDLE h = Real_CreateFileW(temp, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return FALSE;
    DWORD w = 0;
    BOOL wrote = WriteFile(h, data, len, &w, NULL) && w == len;
    CloseHandle(h);
    BOOL placed = wrote && MoveFileExW(temp, target, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH);
    if (!placed) DeleteFileW(temp);
    return placed || SameBytes(target, data, len);      // (the game may hold an identical copy open)
}

void ResourcesInit() {
    g_fromArchives = IniInt(L"guard", L"from_archives", 1) != 0 && g_game == GAME_DDDA;
    LogLine(L"guard    a resource the game asks for loose before its archive is read %s",
            g_fromArchives ? L"gets its own bytes from that archive (riftstone\\standin\\nativePC)"
            : g_game == GAME_DDO ? L"stops the game as usual (Online's archives are encrypted)"
                                 : L"stops the game as usual");
}

HANDLE ArchiveOpen(const wchar_t* fullPath, LPSECURITY_ATTRIBUTES sa, DWORD flags) {
    if (!g_fromArchives || !fullPath) return INVALID_HANDLE_VALUE;
    const wchar_t* prefix = NativePrefix();
    size_t n = wcslen(prefix);
    if (!n || _wcsnicmp(fullPath, prefix, n) != 0) return INVALID_HANDLE_VALUE;
    const wchar_t* rel = fullPath + n;
    const wchar_t* dot = wcsrchr(rel, L'.');
    if (!dot || dot == rel || wcschr(dot, L'\\') || _wcsicmp(dot, L".arc") == 0 || wcsstr(rel, L".."))
        return INVALID_HANDLE_VALUE;
    uint32_t types[8];
    int ntypes = 0;
    for (size_t i = 0; i < _countof(TYPES) && ntypes < 8; i++)
        if (_wcsicmp(TYPES[i].ext, dot + 1) == 0) types[ntypes++] = TYPES[i].type;
    if (!ntypes) return INVALID_HANDLE_VALUE;
    char name[64];                             // the resource's name as its archive files it
    size_t len = (size_t)(dot - rel);
    if (len >= sizeof name) return INVALID_HANDLE_VALUE;
    for (size_t i = 0; i < len; i++) {
        if (rel[i] >= 0x100) return INVALID_HANDLE_VALUE;
        name[i] = (char)rel[i];
    }
    name[len] = 0;

    AcquireSRWLockExclusive(&g_resLock);
    HANDLE out = INVALID_HANDLE_VALUE;
    BYTE* data = NULL;
    DWORD dataLen = 0;
    wchar_t from[MAX_PATH * 2] = L"";
    // 1. the archives the game opened most recently, newest first: the one it is reading now
    LONG next = g_recentNext;
    uint32_t seen[RECENT];
    int nseen = 0;
    for (int k = 1; k <= RECENT && !data; k++) {
        wchar_t p[MAX_PATH], full[MAX_PATH * 2];
        memcpy(p, g_recent[(unsigned)(next - k) % RECENT], sizeof p);   // other threads write the ring
        p[MAX_PATH - 1] = 0;
        if (!p[0] || GetFullPathNameW(p, _countof(full), full, NULL) == 0) continue;
        const wchar_t* d = wcsrchr(full, L'.');
        if (!d || _wcsicmp(d, L".arc") != 0) continue;
        wchar_t source[MAX_PATH * 2];
        ArchiveSource(full, source, _countof(source));
        uint32_t id = 2166136261u;
        for (const wchar_t* c = source; *c; ++c) id = (id ^ (uint32_t)towlower(*c)) * 16777619u;
        BOOL again = FALSE;
        for (int j = 0; j < nseen; j++) again = again || seen[j] == id;
        if (again) continue;
        seen[nseen++] = id;
        DWORD count = 0;
        LONGLONG size = 0;
        ArcEntry* es = ReadDirectory(source, &count, &size);
        if (!es) continue;
        int i = FindEntry(es, count, name, types, ntypes);
        if (i >= 0 && (data = ReadEntry(source, es[i], size, &dataLen)) != NULL) wcsncpy_s(from, _countof(from), source, _TRUNCATE);
        HeapFree(GetProcessHeap(), 0, es);
    }
    // 2. every archive: the index names the candidates, each checked by name and type in its directory
    if (!data) {
        BuildIndex();
        for (int t = 0; t < ntypes && !data; t++) {
            uint32_t h = NameHash(name, types[t]);
            DWORD lo = 0, hi = g_slotCount;
            while (lo < hi) {
                DWORD mid = lo + (hi - lo) / 2;
                if (g_slots[mid].hash < h) lo = mid + 1;
                else hi = mid;
            }
            for (DWORD s = lo; s < g_slotCount && g_slots[s].hash == h && !data; s++) {
                const wchar_t* source = g_paths + g_pathAt[g_slots[s].archive];
                DWORD count = 0;
                LONGLONG size = 0;
                ArcEntry* es = ReadDirectory(source, &count, &size);
                if (!es) continue;
                int i = FindEntry(es, count, name, &types[t], 1);
                if (i >= 0 && (data = ReadEntry(source, es[i], size, &dataLen)) != NULL)
                    wcsncpy_s(from, _countof(from), source, _TRUNCATE);
                HeapFree(GetProcessHeap(), 0, es);
            }
        }
    }
    if (data) {
        wchar_t target[MAX_PATH * 2];
        if (PlaceBytes(rel, data, dataLen, target, _countof(target))) {
            out = Real_CreateFileW(target, GENERIC_READ, FILE_SHARE_READ, sa, OPEN_EXISTING,
                                   flags & ~(DWORD)FILE_FLAG_DELETE_ON_CLOSE, NULL);
        }
        if (out != INVALID_HANDLE_VALUE) {
            LONG k = InterlockedIncrement(&g_fallbacks);
            if (k <= 200)
                LogLine(L"guard    %s was not read in yet; the game gets its own bytes from %s (%lu bytes) instead of stopping",
                        fullPath, from, dataLen);
            LiveNote("last_fallback", fullPath);
        } else {
            LogLine(L"guard    %s is in %s, but its copy could not be written under riftstone\\standin (error %lu)",
                    fullPath, from, GetLastError());
        }
        HeapFree(GetProcessHeap(), 0, data);
    }
    ReleaseSRWLockExclusive(&g_resLock);
    return out;
}

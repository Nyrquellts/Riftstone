// portcrystals -- Riftstone native plugin: more Portcrystals placed at once (the game allows 10).
//
// How the limit works (DDDA.exe build 2364871; docs/re-portcrystals.md)
//   The placed crystals are a list in sGameSys: an area (stage) per slot at +0xBE378 and a position at +0xBE3A0,
//   ten of each, with another field right after them.  Placing one takes the first slot whose area is 0; ten
//   are also built into a count and a clear the compiler unrolled, the Ferrystone's destination list, the
//   stage load that puts the crystals in the world, the map's icon block (uGUIMap +0x3AC, ten pointers) and
//   the save (cSAVE_DATA_PL keeps ten).  So no single number raises it.
//
// What the plugin does (sites.inc, generated from the exe by tools/portcrystal_sites.py)
//   * sGameSys is allocated 0x280 bytes larger and the list moves to its tail: areas at +0xBE470, positions at
//     +0xBE4F0, room for 32 each.  Every instruction that addresses the list shifts with it, and every compare
//     with ten that bounds a slot becomes N (Slots).
//   * The count, the clear and the constructor's stores are replaced by calls that do the same for the new list.
//   * The map object is allocated 0x80 bytes larger and its icon block moves to its tail (+0x960).
//   * The save keeps its ten, exactly as the game writes them.  The slots past ten go to a sidecar file,
//     riftstone\portcrystals.bin, when the game builds its save data, keyed by the ten slots the save holds; when
//     the game loads a save, the matching record comes back (none: the slots past ten are empty).  So a save
//     made with the plugin is still a vanilla save, and without the plugin its crystals past ten are not there.
//
// Safety
//   Build 2364871 only.  All 46 instructions, the four replaced runs and the three hook sites are compared byte
//   for byte first, and sGameSys must not exist yet (it is built at the enlarged size); otherwise nothing is
//   patched and riftstone\logs\portcrystals.log says why.  Once patched, the plugin pins itself in memory: the
//   game's code calls into it.  Settings: riftstone\plugins\portcrystals.ini, [portcrystals] slots = 10..32
//   (default 15).  Original code.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

namespace {

constexpr uintptr_t IMAGE_BASE = 0x00400000;
constexpr uintptr_t SGAMESYS = 0x018FA4BC;            // the sGameSys instance (0 until its constructor has run)
constexpr int VANILLA = 10, MIN_SLOTS = 10, DEFAULT_SLOTS = 15;
constexpr uint32_t SAVE_AREAS = 0xC1F4, SAVE_POS = 0xC220;   // cSAVE_DATA_PL's Anchor_Area / Anchor_Pos
constexpr uint32_t OLD_W = 0xBE3AC;                    // the vanilla list's first w (the constructor zeroes ten)

enum Kind { K_AREA, K_POS, K_AREA_IMM, K_POS_IMM, K_INDEX, K_COUNT, K_COUNT32, K_GS_ALLOC, K_MAP_ALLOC, K_ICONS };
struct Site {
    uint32_t at;
    uint8_t len, field, size, kind;
    uint8_t code[10];
};
struct Block {
    const char* name;
    uint32_t at, back;
    const uint8_t* code;
};
struct Hook {
    const char* name;
    uint32_t at;
    uint8_t len;
    uint8_t code[8];
};
#include "sites.inc"

constexpr int EXTRA = MAX_SLOTS - VANILLA;              // the most slots a record keeps past the save's ten
constexpr int MAX_RECORDS = 32;
constexpr uint32_t SIDECAR_MAGIC = 0x43505352;         // "RSPC"
constexpr uint32_t SIDECAR_VERSION = 1;

int g_slots = DEFAULT_SLOTS;
bool g_patched = false;
wchar_t g_logPath[MAX_PATH], g_sidecar[MAX_PATH];
int g_lines = 0;
constexpr int MAX_LINES = 2000;                         // a session's log stays small

struct Slot {
    uint32_t area, x, y, z;                             // the position's floats as their bits: copied, never computed
};
struct Record {
    uint64_t fp, when;                                  // the save's ten slots (FNV-1a 64); FILETIME written
    uint32_t n;                                         // slots kept past the ten
    Slot s[EXTRA];
};
Record g_rec[MAX_RECORDS];
int g_nrec = 0;
CRITICAL_SECTION g_lock;

// Named crystals ([names] in portcrystals.ini: "XXXXXXXX,YYYYYYYY,ZZZZZZZZ = N", a position's float bits in hex and a
// message number of map_placelist, where riftstone portcrystals add --name puts the name): the Ferrystone's list and
// the map show that name for a crystal at exactly that position.
struct Name {
    uint32_t x, y, z, message;
};
Name g_names[MAX_SLOTS];
int g_nnames = 0;

void VLog(const char* fmt, va_list ap) {
    if (++g_lines > MAX_LINES) return;
    char line[512];
    int n = _vsnprintf_s(line, sizeof line, _TRUNCATE, fmt, ap);
    if (n < 0) n = (int)strlen(line);
    HANDLE h = CreateFileW(g_logPath, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_ALWAYS,
                           FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) return;
    DWORD w;
    WriteFile(h, line, (DWORD)n, &w, nullptr);
    WriteFile(h, "\r\n", 2, &w, nullptr);
    CloseHandle(h);
}

void Log(const char* fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    VLog(fmt, ap);
    va_end(ap);
}

void Stamp(char* out, size_t cap) {
    SYSTEMTIME t;
    GetLocalTime(&t);
    _snprintf_s(out, cap, _TRUNCATE, "%02u:%02u:%02u", (unsigned)t.wHour, (unsigned)t.wMinute, (unsigned)t.wSecond);
}

uint32_t U32(uintptr_t a) { return *(const uint32_t*)a; }
void Put(uintptr_t a, uint32_t v) { *(uint32_t*)a = v; }

// ---- the sidecar: RSPC, version, count, then count x {fp u64, when u64, n u32, n x {area, x, y, z}} ----------
uint64_t Fnv(uint64_t h, uint32_t v) {
    for (int k = 0; k < 4; k++) {
        h ^= (v >> (8 * k)) & 0xFF;
        h *= 0x100000001B3ull;
    }
    return h;
}

// The ten slots as the save keeps them (area, x, y, z); the save and the game's list hold the same bits.
uint64_t FingerprintSave(uintptr_t save) {
    uint64_t h = 0xCBF29CE484222325ull;
    for (int i = 0; i < VANILLA; i++) {
        h = Fnv(h, U32(save + SAVE_AREAS + 4 * i));
        for (int k = 0; k < 3; k++) h = Fnv(h, U32(save + SAVE_POS + 16 * i + 4 * k));
    }
    return h;
}

uint64_t FingerprintGame(uintptr_t gs) {
    uint64_t h = 0xCBF29CE484222325ull;
    for (int i = 0; i < VANILLA; i++) {
        h = Fnv(h, U32(gs + AREAS_NEW + 4 * i));
        for (int k = 0; k < 3; k++) h = Fnv(h, U32(gs + POS_NEW + 16 * i + 4 * k));
    }
    return h;
}

// Strict: any size or count that does not add up refuses the whole file (the plugin then starts empty and keeps
// the damaged file aside).  riftstone/portcrystals.py reads and writes the same bytes.
bool ParseSidecar(const uint8_t* d, size_t len, Record* out, int* count) {
    if (len < 12 || *(const uint32_t*)d != SIDECAR_MAGIC || *(const uint32_t*)(d + 4) != SIDECAR_VERSION) return false;
    uint32_t n = *(const uint32_t*)(d + 8);
    if (n > MAX_RECORDS) return false;
    size_t at = 12;
    for (uint32_t r = 0; r < n; r++) {
        if (len - at < 20) return false;
        Record& rec = out[r];
        memcpy(&rec.fp, d + at, 8);
        memcpy(&rec.when, d + at + 8, 8);
        memcpy(&rec.n, d + at + 16, 4);
        at += 20;
        if (rec.n > EXTRA || (len - at) / sizeof(Slot) < rec.n) return false;
        memcpy(rec.s, d + at, rec.n * sizeof(Slot));
        at += rec.n * sizeof(Slot);
    }
    if (at != len) return false;
    *count = (int)n;
    return true;
}

void LoadSidecar() {
    g_nrec = 0;
    HANDLE f = CreateFileW(g_sidecar, GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, 0, nullptr);
    if (f == INVALID_HANDLE_VALUE) return;
    static uint8_t buf[12 + MAX_RECORDS * (20 + EXTRA * sizeof(Slot)) + 1];
    DWORD got = 0;
    BOOL ok = ReadFile(f, buf, sizeof buf, &got, nullptr);
    CloseHandle(f);
    int n = 0;
    if (!ok || got == sizeof buf || !ParseSidecar(buf, got, g_rec, &n)) {
        wchar_t bad[MAX_PATH];
        _snwprintf_s(bad, MAX_PATH, _TRUNCATE, L"%s.bad", g_sidecar);
        MoveFileExW(g_sidecar, bad, MOVEFILE_REPLACE_EXISTING);
        Log("the sidecar riftstone\\portcrystals.bin is damaged; kept aside as portcrystals.bin.bad, starting empty");
        return;
    }
    g_nrec = n;
}

void SaveSidecar() {
    static uint8_t buf[12 + MAX_RECORDS * (20 + EXTRA * sizeof(Slot))];
    size_t at = 12;
    *(uint32_t*)buf = SIDECAR_MAGIC;
    *(uint32_t*)(buf + 4) = SIDECAR_VERSION;
    *(uint32_t*)(buf + 8) = (uint32_t)g_nrec;
    for (int r = 0; r < g_nrec; r++) {
        memcpy(buf + at, &g_rec[r].fp, 8);
        memcpy(buf + at + 8, &g_rec[r].when, 8);
        memcpy(buf + at + 16, &g_rec[r].n, 4);
        memcpy(buf + at + 20, g_rec[r].s, g_rec[r].n * sizeof(Slot));
        at += 20 + g_rec[r].n * sizeof(Slot);
    }
    wchar_t tmp[MAX_PATH];
    _snwprintf_s(tmp, MAX_PATH, _TRUNCATE, L"%s.tmp", g_sidecar);
    HANDLE f = CreateFileW(tmp, GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (f == INVALID_HANDLE_VALUE) {
        Log("could not write the sidecar (%lu): the crystals past ten are not kept for this save", GetLastError());
        return;
    }
    DWORD w = 0;
    BOOL ok = WriteFile(f, buf, (DWORD)at, &w, nullptr) && w == at && FlushFileBuffers(f);
    CloseHandle(f);
    if (!ok || !MoveFileExW(tmp, g_sidecar, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
        Log("could not write the sidecar (%lu): the crystals past ten are not kept for this save", GetLastError());
        DeleteFileW(tmp);
    }
}

}  // namespace

// ---- what the replaced runs and the hooks call ----------------------------------------------------------------
// The constructor: the vanilla run zeroed each of the ten positions' w; the new list starts empty.
extern "C" void __stdcall PC_Construct(uint8_t* gs) {
    for (int i = 0; i < VANILLA; i++) Put((uintptr_t)gs + OLD_W + 16 * i, 0);
    memset(gs + AREAS_NEW, 0, 4 * MAX_SLOTS);
    memset(gs + POS_NEW, 0, 16 * MAX_SLOTS);
}

// The clear (a new game, a return to the title...): every slot empty, area 0 and position 0, as the vanilla stores
// wrote with edi and xmm0 (both 0 there).  The replaced part covered slots 0-5 of the old list; slots 6-9's stores
// still run after it, on the old list, which nothing reads any more.
extern "C" void __stdcall PC_Clear(uint8_t* gs) {
    for (int i = 0; i < 6; i++) {
        Put((uintptr_t)gs + AREAS_OLD + 4 * i, 0);
        memset(gs + POS_OLD + 16 * i, 0, 16);
    }
    memset(gs + AREAS_NEW, 0, 4 * MAX_SLOTS);
    memset(gs + POS_NEW, 0, 16 * MAX_SLOTS);
}

// The count of placed crystals (a slot whose area is not 0), over N slots.
extern "C" uint32_t __stdcall PC_Count(uint8_t* gs) {
    uint32_t n = 0;
    for (int i = 0; i < g_slots; i++) n += U32((uintptr_t)gs + AREAS_NEW + 4 * i) != 0;
    return n;
}

// uGUIMap's constructor: the icon pointers = edx (0), the moved block and (harmlessly) the old one.
extern "C" void __stdcall PC_MapConstruct(uint8_t* map, uint32_t value) {
    for (int i = 0; i < VANILLA; i++) Put((uintptr_t)map + ICONS_OLD + 4 * i, value);
    for (int i = 0; i < MAX_SLOTS; i++) Put((uintptr_t)map + ICONS_NEW + 4 * i, value);
}

// The game is about to copy its ten slots into save data: keep the slots past ten, keyed by those ten.
extern "C" void __stdcall PC_Capture(uint8_t* save, uint8_t* gs) {
    (void)save;
    if (g_slots <= VANILLA) return;
    EnterCriticalSection(&g_lock);
    Record rec;
    rec.fp = FingerprintGame((uintptr_t)gs);
    FILETIME ft;
    GetSystemTimeAsFileTime(&ft);
    rec.when = ((uint64_t)ft.dwHighDateTime << 32) | ft.dwLowDateTime;
    rec.n = (uint32_t)(g_slots - VANILLA);
    int used = 0;
    for (uint32_t i = 0; i < rec.n; i++) {
        uintptr_t a = (uintptr_t)gs + AREAS_NEW + 4 * (VANILLA + i), p = (uintptr_t)gs + POS_NEW + 16 * (VANILLA + i);
        rec.s[i] = {U32(a), U32(p), U32(p + 4), U32(p + 8)};
        used += rec.s[i].area != 0;
    }
    int found = -1;
    for (int r = 0; r < g_nrec; r++)
        if (g_rec[r].fp == rec.fp) found = r;
    bool same = found >= 0 && g_rec[found].n == rec.n && memcmp(g_rec[found].s, rec.s, rec.n * sizeof(Slot)) == 0;
    // newest first; the oldest record goes when the table is full
    int from = found >= 0 ? found : (g_nrec < MAX_RECORDS ? g_nrec++ : MAX_RECORDS - 1);
    memmove(&g_rec[1], &g_rec[0], from * sizeof(Record));
    g_rec[0] = rec;
    SaveSidecar();
    LeaveCriticalSection(&g_lock);
    if (!same) {
        char at[16];
        Stamp(at, sizeof at);
        Log("%s  saved: %d crystal(s) placed past the save's ten (slots 11-%d), kept in the sidecar for this save "
            "(%016llx)", at, used, g_slots, (unsigned long long)rec.fp);
    }
}

// The game has read a save and is about to copy its ten slots into the list: the slots past ten come from the
// sidecar record for this save, else they are empty.
extern "C" void __stdcall PC_Restore(uint8_t* save, uint8_t* gs) {
    EnterCriticalSection(&g_lock);
    const uint64_t fp = FingerprintSave((uintptr_t)save);
    const Record* rec = nullptr;
    for (int r = 0; r < g_nrec && !rec; r++)
        if (g_rec[r].fp == fp) rec = &g_rec[r];
    int restored = 0, beyond = 0;
    for (int i = VANILLA; i < g_slots; i++) {
        uintptr_t a = (uintptr_t)gs + AREAS_NEW + 4 * i, p = (uintptr_t)gs + POS_NEW + 16 * i;
        const uint32_t k = (uint32_t)(i - VANILLA);
        if (rec && k < rec->n) {
            Put(a, rec->s[k].area);
            Put(p, rec->s[k].x);
            Put(p + 4, rec->s[k].y);
            Put(p + 8, rec->s[k].z);
            Put(p + 12, 0);
            restored += rec->s[k].area != 0;
        } else {
            Put(a, 0);
            memset((void*)p, 0, 16);
        }
    }
    if (rec)
        for (uint32_t k = (uint32_t)(g_slots - VANILLA); k < rec->n; k++) beyond += rec->s[k].area != 0;
    LeaveCriticalSection(&g_lock);
    char at[16];
    Stamp(at, sizeof at);
    if (rec)
        Log("%s  loaded: %d crystal(s) past the save's ten came back from the sidecar (%016llx)", at, restored,
            (unsigned long long)fp);
    else
        Log("%s  loaded: no sidecar record for this save (%016llx): slots 11-%d are empty", at,
            (unsigned long long)fp, g_slots);
    if (beyond)
        Log("%s  %d crystal(s) of this save are past slots = %d and stay out of the game; a higher slots brings them "
            "back (the next save forgets them)", at, beyond, g_slots);
}

// The place name of a position (the game's 0x004541B0, which the Ferrystone's list and the map ask for each crystal):
// a named crystal's message number, or -1 for the game's own answer.  While the manager's place data is not loaded
// the game names nothing (its own test, GUARD), and neither does a named crystal.
extern "C" int32_t __stdcall PC_PlaceName(const uint32_t* pos, uintptr_t manager) {
    if (!pos || !manager || !U32(manager + PLACE_READY)) return -1;
    for (int i = 0; i < g_nnames; i++)
        if (pos[0] == g_names[i].x && pos[1] == g_names[i].y && pos[2] == g_names[i].z) return (int32_t)g_names[i].message;
    return -1;
}

// The runs jump here; the thunks keep every register, the flags and xmm0-7 (the save copy keeps a value in xmm3
// across the hook), call the function and resume where the vanilla code went on.
uintptr_t g_backConstruct, g_backClear, g_backMap, g_backSave, g_backLoad, g_backLoad2, g_backPlaceName;

#define SAVE_ALL __asm pushfd __asm pushad __asm sub esp, 128 \
    __asm movdqu xmmword ptr [esp], xmm0 __asm movdqu xmmword ptr [esp + 16], xmm1 \
    __asm movdqu xmmword ptr [esp + 32], xmm2 __asm movdqu xmmword ptr [esp + 48], xmm3 \
    __asm movdqu xmmword ptr [esp + 64], xmm4 __asm movdqu xmmword ptr [esp + 80], xmm5 \
    __asm movdqu xmmword ptr [esp + 96], xmm6 __asm movdqu xmmword ptr [esp + 112], xmm7
#define LOAD_ALL __asm movdqu xmm0, xmmword ptr [esp] __asm movdqu xmm1, xmmword ptr [esp + 16] \
    __asm movdqu xmm2, xmmword ptr [esp + 32] __asm movdqu xmm3, xmmword ptr [esp + 48] \
    __asm movdqu xmm4, xmmword ptr [esp + 64] __asm movdqu xmm5, xmmword ptr [esp + 80] \
    __asm movdqu xmm6, xmmword ptr [esp + 96] __asm movdqu xmm7, xmmword ptr [esp + 112] \
    __asm add esp, 128 __asm popad __asm popfd

__declspec(naked) static void ThunkConstruct() {           // ebp = sGameSys
    SAVE_ALL
    __asm push ebp
    __asm call PC_Construct
    LOAD_ALL
    __asm jmp dword ptr [g_backConstruct]
}
__declspec(naked) static void ThunkClear() {               // ebx = sGameSys
    SAVE_ALL
    __asm push ebx
    __asm call PC_Clear
    LOAD_ALL
    __asm jmp dword ptr [g_backClear]
}
__declspec(naked) static void ThunkCount() {               // entered by call; ecx = sGameSys; the count in al
    SAVE_ALL
    __asm push ecx
    __asm call PC_Count
    __asm mov byte ptr [esp + 128 + 28], al                // pushad's eax
    LOAD_ALL
    __asm ret
}
__declspec(naked) static void ThunkMap() {                 // esi = uGUIMap, edx = the pointer value
    SAVE_ALL
    __asm push edx
    __asm push esi
    __asm call PC_MapConstruct
    LOAD_ALL
    __asm jmp dword ptr [g_backMap]
}
__declspec(naked) static void ThunkSave() {                // edi = the save data, esi = sGameSys
    SAVE_ALL
    __asm push esi
    __asm push edi
    __asm call PC_Capture
    LOAD_ALL
    __asm lea ebx, [edi + 0xC1F4]
    __asm jmp dword ptr [g_backSave]
}
__declspec(naked) static void ThunkLoad() {                // esi = the save data, edi = sGameSys
    SAVE_ALL
    __asm push edi
    __asm push esi
    __asm call PC_Restore
    LOAD_ALL
    __asm xorps xmm0, xmm0
    __asm xor ecx, ecx
    __asm jmp dword ptr [g_backLoad]
}
// Entered in place of 0x004541B0's first instruction (called with ecx = the position, edx = its manager): a named
// crystal's message number straight back, else the game's own function.  xmm registers are the caller's to lose
// across a call.
__declspec(naked) static void ThunkPlaceName() {
    __asm {
        pushfd
        pushad
        push edx
        push ecx
        call PC_PlaceName
        cmp eax, -1
        je game
        mov dword ptr [esp + 28], eax                      // pushad's eax: the answer
        popad
        popfd
        ret
    game:
        popad
        popfd
        sub esp, 0x140
        jmp dword ptr [g_backPlaceName]
    }
}

__declspec(naked) static void ThunkLoad2() {               // ebx = the save data, esi = sGameSys
    SAVE_ALL
    __asm push esi
    __asm push ebx
    __asm call PC_Restore
    LOAD_ALL
    __asm xorps xmm0, xmm0
    __asm xor ecx, ecx
    __asm jmp dword ptr [g_backLoad2]
}

namespace {

bool Readable(uintptr_t at, size_t n) {
    MEMORY_BASIC_INFORMATION mbi;
    for (uintptr_t p = at; p < at + n;) {
        if (!VirtualQuery((const void*)p, &mbi, sizeof mbi) || mbi.State != MEM_COMMIT ||
            (mbi.Protect & (PAGE_NOACCESS | PAGE_GUARD)))
            return false;
        p = (uintptr_t)mbi.BaseAddress + mbi.RegionSize;
    }
    return true;
}

bool Verify() {
    for (const Site& s : SITES)
        if (!Readable(s.at, s.len) || memcmp((const void*)(uintptr_t)s.at, s.code, s.len) != 0)
            return Log("refused: the instruction at 0x%08X is not build 2364871's (another build, or already patched)",
                       (unsigned)s.at), false;
    for (const Block& b : BLOCKS)
        if (!Readable(b.at, b.back - b.at) || memcmp((const void*)(uintptr_t)b.at, b.code, b.back - b.at) != 0)
            return Log("refused: the %s run at 0x%08X is not build 2364871's", b.name, (unsigned)b.at), false;
    for (const Hook& h : HOOKS)
        if (!Readable(h.at, h.len) || memcmp((const void*)(uintptr_t)h.at, h.code, h.len) != 0)
            return Log("refused: the %s hook's site at 0x%08X is not build 2364871's", h.name, (unsigned)h.at), false;
    if (!Readable(GUARD.at, GUARD.len) || memcmp((const void*)(uintptr_t)GUARD.at, GUARD.code, GUARD.len) != 0)
        return Log("refused: the %s at 0x%08X is not build 2364871's", GUARD.name, (unsigned)GUARD.at), false;
    return true;
}

uint32_t NewValue(const Site& s) {
    uint32_t old = s.size == 1 ? *(const uint8_t*)(uintptr_t)(s.at + s.field) : U32(s.at + s.field);
    switch (s.kind) {
        case K_AREA: case K_AREA_IMM: return old + (AREAS_NEW - AREAS_OLD);
        case K_POS: case K_POS_IMM: return old + (POS_NEW - POS_OLD);
        case K_INDEX: return old + (IDX_NEW - IDX_OLD);
        case K_COUNT: case K_COUNT32: return (uint32_t)g_slots;
        case K_GS_ALLOC: return GS_NEW_SIZE;
        case K_MAP_ALLOC: return MAP_NEW_SIZE;
        case K_ICONS: return ICONS_NEW;
    }
    return old;
}

void Jump(uintptr_t at, void* to, size_t span, uint8_t fill) {
    uint8_t* p = (uint8_t*)at;
    p[0] = 0xE9;
    *(int32_t*)(p + 1) = (int32_t)((uintptr_t)to - (at + 5));
    memset(p + 5, fill, span - 5);
}

bool Patch() {
    g_backConstruct = BLOCKS[0].back;
    g_backClear = BLOCKS[1].back;
    g_backMap = BLOCKS[3].back;
    g_backSave = HOOKS[0].at + HOOKS[0].len;
    g_backLoad = HOOKS[1].at + HOOKS[1].len;
    g_backLoad2 = HOOKS[2].at + HOOKS[2].len;
    g_backPlaceName = HOOKS[3].at + HOOKS[3].len;
    void* const runs[4] = {(void*)ThunkConstruct, (void*)ThunkClear, (void*)ThunkCount, (void*)ThunkMap};
    void* const hooks[4] = {(void*)ThunkSave, (void*)ThunkLoad, (void*)ThunkLoad2, (void*)ThunkPlaceName};
    // One protection change over the whole span, so a failure leaves the code untouched.
    uintptr_t lo = ~(uintptr_t)0, hi = 0;
    for (const Site& s : SITES) lo = min(lo, (uintptr_t)s.at), hi = max(hi, (uintptr_t)s.at + s.len);
    for (const Block& b : BLOCKS) lo = min(lo, (uintptr_t)b.at), hi = max(hi, (uintptr_t)b.back);
    for (const Hook& h : HOOKS) lo = min(lo, (uintptr_t)h.at), hi = max(hi, (uintptr_t)h.at + h.len);
    DWORD old;
    if (!VirtualProtect((void*)lo, hi - lo, PAGE_EXECUTE_READWRITE, &old))
        return Log("failed: VirtualProtect 0x%08X..0x%08X (%lu); nothing patched", (unsigned)lo, (unsigned)hi,
                   GetLastError()), false;
    for (const Site& s : SITES) {
        uint32_t v = NewValue(s);
        if (s.size == 1) *(uint8_t*)(uintptr_t)(s.at + s.field) = (uint8_t)v;
        else *(uint32_t*)(uintptr_t)(s.at + s.field) = v;
    }
    for (int i = 0; i < 4; i++) Jump(BLOCKS[i].at, runs[i], BLOCKS[i].back - BLOCKS[i].at, 0xCC);   // never reached
    for (int i = 0; i < 4; i++) Jump(HOOKS[i].at, hooks[i], HOOKS[i].len, 0x90);
    VirtualProtect((void*)lo, hi - lo, old, &old);
    FlushInstructionCache(GetCurrentProcess(), (void*)lo, hi - lo);
    return true;
}

void ReadSettings(HMODULE self) {
    wchar_t ini[MAX_PATH];
    GetModuleFileNameW(self, ini, MAX_PATH);
    wchar_t* dot = wcsrchr(ini, L'.');
    if (dot) wcscpy_s(dot, MAX_PATH - (dot - ini), L".ini");
    int n = (int)GetPrivateProfileIntW(L"portcrystals", L"slots", DEFAULT_SLOTS, ini);
    if (n < MIN_SLOTS || n > MAX_SLOTS) {
        Log("slots = %d is outside %d..%d; using %d", n, MIN_SLOTS, MAX_SLOTS, n < MIN_SLOTS ? MIN_SLOTS : MAX_SLOTS);
        n = n < MIN_SLOTS ? MIN_SLOTS : MAX_SLOTS;
    }
    g_slots = n;
    // [names]: "XXXXXXXX,YYYYYYYY,ZZZZZZZZ = message" per named crystal (riftstone portcrystals add --name)
    static wchar_t section[8192];
    DWORD got = GetPrivateProfileSectionW(L"names", section, _countof(section), ini);
    g_nnames = 0;
    for (const wchar_t* p = section; got && *p && g_nnames < MAX_SLOTS; p += wcslen(p) + 1) {
        unsigned long v[3] = {0, 0, 0};
        const wchar_t* q = p;
        wchar_t* end = nullptr;
        bool ok = true;
        for (int k = 0; k < 3 && ok; k++) {
            v[k] = wcstoul(q, &end, 16);
            ok = end != q && end - q <= 8 && (k == 2 || *end == L',');
            q = end + (k < 2 ? 1 : 0);
        }
        while (ok && (*q == L' ' || *q == L'\t')) q++;
        ok = ok && *q == L'=';
        if (ok) q++;
        while (ok && (*q == L' ' || *q == L'\t')) q++;
        unsigned long message = ok ? wcstoul(q, &end, 10) : 0;
        ok = ok && end != q;
        while (ok && (*end == L' ' || *end == L'\t')) end++;
        ok = ok && *end == 0 && message <= 0xFFFF;
        if (ok) g_names[g_nnames++] = {(uint32_t)v[0], (uint32_t)v[1], (uint32_t)v[2], (uint32_t)message};
        else Log("names: %S is not XXXXXXXX,YYYYYYYY,ZZZZZZZZ = message (0..65535); ignored", p);
    }
}

void Start(HMODULE self) {
    InitializeCriticalSection(&g_lock);
    wchar_t root[MAX_PATH];
    GetModuleFileNameW(nullptr, root, MAX_PATH);
    wchar_t* slash = wcsrchr(root, L'\\');
    if (slash) *slash = 0;
    wchar_t dir[MAX_PATH];
    _snwprintf_s(dir, MAX_PATH, _TRUNCATE, L"%s\\riftstone", root);
    CreateDirectoryW(dir, nullptr);
    _snwprintf_s(dir, MAX_PATH, _TRUNCATE, L"%s\\riftstone\\logs", root);
    CreateDirectoryW(dir, nullptr);
    _snwprintf_s(g_logPath, MAX_PATH, _TRUNCATE, L"%s\\riftstone\\logs\\portcrystals.log", root);
    _snwprintf_s(g_sidecar, MAX_PATH, _TRUNCATE, L"%s\\riftstone\\portcrystals.bin", root);
    DeleteFileW(g_logPath);

    // The game is a fixed-base image; a test harness that maps DDDA.exe itself sets the variable.
    wchar_t probe[8];
    bool harness = GetEnvironmentVariableW(L"RIFTSTONE_PORTCRYSTALS_HARNESS", probe, 8) > 0;
    if (!harness && (uintptr_t)GetModuleHandleW(nullptr) != IMAGE_BASE) {
        Log("refused: the host is not DDDA.exe at 0x00400000");
        return;
    }
    ReadSettings(self);
    if (!Verify()) return;
    // sGameSys is built once, at start-up; one built before the patches has room for ten, and the moved references
    // would run past it.  Loaded by Riftstone's loader, it never exists yet.
    if (!Readable(SGAMESYS, 4) || U32(SGAMESYS) != 0) {
        Log("refused: the game's sGameSys already exists (the plugin was loaded too late); nothing patched");
        return;
    }
    LoadSidecar();
    if (!Patch()) return;
    g_patched = true;
    HMODULE pinned;                                      // the game's code now calls into this module
    GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_PIN,
                       (LPCWSTR)(void*)&PC_Construct, &pinned);
    Log("portcrystals: %d Portcrystals placed at once (the game allows %d); %u sites, %u runs and %u hooks patched "
        "(%s); the sidecar keeps %d save(s)' crystals past ten; %d named crystal(s)", g_slots, VANILLA,
        (unsigned)(sizeof SITES / sizeof SITES[0]), (unsigned)(sizeof BLOCKS / sizeof BLOCKS[0]),
        (unsigned)(sizeof HOOKS / sizeof HOOKS[0]), harness ? "harness" : "game", g_nrec, g_nnames);
}

}  // namespace

// For the harness and the loader's live view: the slot count in effect (0 when the plugin refused).
extern "C" __declspec(dllexport) int Portcrystals_Slots() { return g_patched ? g_slots : 0; }

// For the harness: the records the sidecar holds now.
extern "C" __declspec(dllexport) int Portcrystals_Records() { return g_nrec; }

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(module);
        Start(module);
    }
    return TRUE;
}

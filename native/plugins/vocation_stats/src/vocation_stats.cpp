// vocation_stats -- Riftstone native plugin: a rest at an inn gives the Arisen and the main pawn the stats
// their current vocation would have given them at every level.
//
// What the game does (DDDA.exe build 2364871; docs/vocation-stats.md)
//   A character's base stats are six floats of its cPlayerInfo (the uPlayer's +0x3DEC): HP +0x2A0,
//   stamina +0x2B0, attack +0x2B4, defence +0x2B8, magick attack +0x2BC, magick defence +0x2C0.  Each
//   level-up (0x00B5BD20) adds the growth of the vocation the character has at that moment, from its level
//   table (param\pl\level\Lv<vocation>, sPlayerManager +0x86C + 4 x the vocation's number: rows {level
//   from, HP, stamina, attack, defence, magick attack, magick defence}), HP capped at 9999.  So the stats
//   remember every vocation the character levelled in.  The game's own setLevel (0x00B5CCB0, the
//   character in edi, the level on the stack) rebuilds them from scratch: level 1 from the table's base
//   row, then its own level-up up to the level, all in the current vocation, with no notice; it zeroes
//   the experience toward the next level (+0x2C4) at the end.
//   uInn (DTI 0x01992488) is the rest: its state 0x00736740 calls one of two rest routines
//   (0x00737580 with the inn pushed, or 0x007377D0 with it in esi), then goes on.
//
// What this plugin does (vocation_stats.ini, next to this file)
//   The two rest calls (0x00736757, 0x0073675E) go to thunks that run setLevel at the character's own
//   level for the Arisen and the main pawn (Who), put the experience toward the next level back, and
//   then go on to the rest routine as the game would, so the rest's heal fills the new HP.  Every
//   register, flag and xmm0-7 reaches the rest routine as the call site left them.  The hired pawns are
//   never touched.  Mode = off patches nothing.
//
// Safety
//   DDDA.exe build 2364871 only.  The rest calls, setLevel and the level-up's refusals and tables are
//   compared first; on any difference nothing is patched and riftstone\logs\vocation_stats.log says why.
//   A character the level-up would refuse (setLevel would then loop for ever), one with no level table
//   or a level outside 1..200 is left as it is.  If the game's code faults inside setLevel, that
//   character's level, stats and experience are put back as they were.  No floating point of its own:
//   the stats are logged from their bits.  Original code; no third-party source.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <wchar.h>

namespace {

// ---- build 2364871 --------------------------------------------------------------------------
constexpr uintptr_t IMAGE_BASE = 0x00400000;
constexpr uintptr_t SITE_A = 0x00736757;             // call 0x00737580 (the inn pushed)
constexpr uintptr_t REST_A = 0x00737580;
constexpr uintptr_t SITE_B = 0x0073675E;             // call 0x007377D0 (the inn in esi)
constexpr uintptr_t REST_B = 0x007377D0;
constexpr uintptr_t SET_LEVEL = 0x00B5CCB0;          // setLevel: edi = the uPlayer, [esp+4] = the level; ret 4
constexpr uintptr_t PLAYERS = 0x018FA4EC;            // sPlayerManager::mpInstance
constexpr uint32_t MGR_EXP = 0x868;                  // sPlayerManager: param\pl\level\exp
constexpr uint32_t MGR_LEVELS = 0x86C;               // + 4 x vocation: param\pl\level\Lv<vocation>
constexpr uint32_t MGR_SLOT[2] = {0x99C, 0x9A0};     // the Arisen, the main pawn
constexpr uint32_t P_FLAGS = 0x206D, P_HELD = 0x31EA, P_STATE = 0x273C, P_INFO = 0x3DEC;
constexpr uint32_t I_JOB = 0x10, I_HP = 0x2A0, I_STAMINA = 0x2B0, I_EXP = 0x2C4, I_COST = 0x2C8,
                   I_LEVEL = 0x700;
constexpr uint32_t T_COUNT = 0x64, T_ROWS = 0x70;    // a level table: its row count, its row pointers
constexpr uint32_t MAX_LEVEL = 200;
constexpr int MAX_LINES = 400;                       // log lines a session (rests are few)

struct Expect {
    const char* what;
    uintptr_t at;
    uint8_t bytes[36];
    uint8_t len;
};

// The code the patch relies on: a second copy of the plugin, another patch or another build shows up
// here as a difference.
const Expect CONTEXT[] = {
    {"uInn's rest state: the two rest calls", 0x0073674E,
     {0x38, 0x9E, 0x2C, 0x0A, 0x00, 0x00, 0x75, 0x08, 0x56, 0xE8, 0x24, 0x0E, 0x00, 0x00, 0xEB, 0x05, 0xE8, 0x6D,
      0x10, 0x00, 0x00}, 21},
    {"setLevel: its start (a dead character is left)", SET_LEVEL,
     {0x55, 0x8B, 0xEC, 0x83, 0xE4, 0xF8, 0xF6, 0x87, 0x6D, 0x20, 0x00, 0x00, 0x08, 0x53, 0x8B, 0x5D, 0x08, 0x56,
      0x0F, 0x85, 0x32, 0x01, 0x00, 0x00}, 24},
    {"setLevel: the cap at 200, the character's info, the vocation's table", 0x00B5CCC8,
     {0x81, 0xFB, 0xC8, 0x00, 0x00, 0x00, 0x72, 0x05, 0xBB, 0xC8, 0x00, 0x00, 0x00, 0x8B, 0xB7, 0xEC, 0x3D, 0x00,
      0x00, 0x8B, 0x46, 0x10, 0x8B, 0x0D, 0xEC, 0xA4, 0x8F, 0x01, 0x8B, 0x94, 0x81, 0x6C, 0x08, 0x00, 0x00}, 35},
    {"setLevel: level 1 from the table's base row", 0x00B5CCEB,
     {0x8B, 0x42, 0x70, 0x8B, 0x40, 0x04, 0xC7, 0x86, 0x00, 0x07, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x0F, 0xB7,
      0x50, 0x06}, 20},
    {"setLevel: the level-ups (no notice, forced) up to the level", 0x00B5CDA1,
     {0x76, 0x24, 0x6A, 0x01, 0x6A, 0x00, 0x57, 0xE8, 0x73, 0xEF, 0xFF, 0xFF, 0x39, 0x9E, 0x00, 0x07, 0x00, 0x00,
      0x73, 0x12, 0x6A, 0x01, 0x6A, 0x00, 0x57, 0xE8, 0x61, 0xEF, 0xFF, 0xFF, 0x39, 0x9E, 0x00, 0x07, 0x00, 0x00},
     36},
    {"setLevel: +0x2C4 zeroed", 0x00B5CDCF, {0xC7, 0x86, 0xC4, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00}, 10},
    {"setLevel: its return", 0x00B5CDFA, {0x5E, 0x5B, 0x8B, 0xE5, 0x5D, 0xC2, 0x04, 0x00}, 8},
    {"level-up: its start", 0x00B5BD20,
     {0x55, 0x8B, 0xEC, 0x83, 0xE4, 0xF0, 0x81, 0xEC, 0x94, 0x00, 0x00, 0x00, 0x53, 0x8B, 0x5D, 0x08, 0x8A, 0x83,
      0x6D, 0x20, 0x00, 0x00, 0x56, 0x57, 0xC6, 0x44, 0x24, 0x17, 0x00, 0xA8, 0x08, 0x74, 0x0B}, 33},
    {"level-up: its refusals", 0x00B5BD4C,
     {0xA8, 0x02, 0x74, 0x09, 0x80, 0xBB, 0xEA, 0x31, 0x00, 0x00, 0x00, 0x75, 0xE8, 0xA8, 0x04, 0x74, 0x0C, 0xF7,
      0x83, 0x3C, 0x27, 0x00, 0x00, 0x00, 0x80, 0x00, 0x00, 0x75, 0xD8}, 29},
    {"level-up: the info and the cap at 200", 0x00B5BD69,
     {0x8B, 0xB3, 0xEC, 0x3D, 0x00, 0x00, 0x8B, 0x8E, 0x00, 0x07, 0x00, 0x00, 0x81, 0xF9, 0xC8, 0x00, 0x00, 0x00,
      0x73, 0xC4}, 20},
    {"level-up: forced past the experience", 0x00B5BD8D,
     {0x80, 0x7D, 0x10, 0x00, 0x0F, 0x84, 0xDC, 0x02, 0x00, 0x00, 0x2B, 0xC2, 0x41, 0x89, 0x86, 0xC4, 0x02, 0x00,
      0x00}, 19},
    {"level-up: the vocation's table", 0x00B5BE61,
     {0x8B, 0x15, 0xEC, 0xA4, 0x8F, 0x01, 0x8B, 0x4E, 0x10, 0x8B, 0x8C, 0x8A, 0x6C, 0x08, 0x00, 0x00}, 16},
    {"level-up: HP capped at 9999", 0x00B5BED7,
     {0xF3, 0x0F, 0x58, 0x86, 0xA0, 0x02, 0x00, 0x00, 0x0F, 0x2F, 0xC1, 0xF3, 0x0F, 0x11, 0x86, 0xA0, 0x02, 0x00,
      0x00, 0x72, 0x08, 0xF3, 0x0F, 0x11, 0x8E, 0xA0, 0x02, 0x00, 0x00}, 29},
    {"level-up: no notice ends it after the HP", 0x00B5BFD7, {0x80, 0x7D, 0x0C, 0x00, 0x0F, 0x84, 0x92, 0x00, 0x00, 0x00},
     10},
    {"the HP cap, 9999.0", 0x0161CA24, {0x00, 0x3C, 0x1C, 0x46}, 4},
    {"the party: slot 0, the Arisen", 0x007820A0, {0x8B, 0x0D, 0xEC, 0xA4, 0x8F, 0x01, 0x8B, 0x81, 0x9C, 0x09, 0x00, 0x00},
     12},
    {"the party: slot 1, the main pawn", 0x007820B6,
     {0x8B, 0x15, 0xEC, 0xA4, 0x8F, 0x01, 0x8B, 0x84, 0x82, 0xA0, 0x09, 0x00, 0x00}, 13},
};

const char* const VOCATION[10] = {"?",       "Fighter", "Strider", "Mage",   "Mystic Knight",
                                  "Assassin", "Magick Archer", "Warrior", "Ranger", "Sorcerer"};
const char* const WHO[2] = {"the Arisen", "the main pawn"};

wchar_t g_logPath[MAX_PATH];
volatile LONG g_lines = 0;

void Log(const char* fmt, ...) {
    char line[640];
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

// A line about a rest, at most MAX_LINES a session.
template <typename... A>
void Note(const char* fmt, A... a) {
    LONG n = InterlockedIncrement(&g_lines);
    if (n < MAX_LINES) Log(fmt, a...);
    else if (n == MAX_LINES) Log("(%d lines: the rest of this session's rests are not logged)", MAX_LINES);
}

bool Readable(uintptr_t at, size_t n) {
    MEMORY_BASIC_INFORMATION mbi;
    if (!at || at + n < at) return false;
    for (uintptr_t p = at; p < at + n;) {
        if (!VirtualQuery((const void*)p, &mbi, sizeof mbi) || mbi.State != MEM_COMMIT ||
            (mbi.Protect & (PAGE_NOACCESS | PAGE_GUARD)))
            return false;
        p = (uintptr_t)mbi.BaseAddress + mbi.RegionSize;
    }
    return true;
}

bool Verify() {
    for (const Expect& e : CONTEXT) {
        if (!Readable(e.at, e.len) || memcmp((const void*)e.at, e.bytes, e.len) != 0)
            return Log("refused: %s at 0x%08X is not the expected code (another build, or already patched)", e.what,
                       (unsigned)e.at),
                   false;
    }
    return true;
}

uint32_t U32(uintptr_t at) { return *(const uint32_t*)at; }

// A stat's float as a whole number (they are whole numbers below 2^24), from its bits.
int Whole(uint32_t bits) {
    const int e = (int)((bits >> 23) & 0xFF);
    if (e < 127) return 0;
    const uint32_t m = (bits & 0x7FFFFF) | 0x800000;
    const int shift = e - 150;
    const uint32_t v = shift >= 0 ? (shift > 7 ? 0x7FFFFFFF : m << shift) : m >> -shift;
    return (bits >> 31) ? -(int)v : (int)v;
}

// ---- the recalculation --------------------------------------------------------------------------
bool g_who[2] = {true, true};
uintptr_t g_setLevel = SET_LEVEL;

// setLevel(level) on the uPlayer in edi, as the game calls it.  ebx, esi, edi and ebp are kept.
__declspec(naked) void __cdecl CallSetLevel(uint32_t /*player*/, uint32_t /*level*/) {
    __asm {
        push ebx
        push esi
        push edi
        push ebp
        mov edi, dword ptr [esp + 0x14]   // player
        mov eax, dword ptr [esp + 0x18]   // level
        push eax
        call dword ptr [g_setLevel]
        pop ebp
        pop edi
        pop esi
        pop ebx
        ret
    }
}

struct Saved {
    uint8_t stats[I_COST + 4 - I_HP];   // +0x2A0 .. +0x2CB: the stats, the experience and the next level's cost
    uint32_t level;
};

// setLevel under SEH: false (with everything put back) when the game's code faulted.
bool SetLevelGuarded(uint32_t player, uint32_t info, uint32_t level, const Saved& saved, DWORD& code) {
    __try {
        CallSetLevel(player, level);
        return true;
    } __except (code = GetExceptionCode(), EXCEPTION_EXECUTE_HANDLER) {
        memcpy((void*)(uintptr_t)(info + I_HP), saved.stats, sizeof saved.stats);
        *(uint32_t*)(uintptr_t)(info + I_LEVEL) = saved.level;
        return false;
    }
}

// What keeps a character as it is, or nullptr.
const char* Unfit(uint32_t mgr, uint32_t player, uint32_t& info, uint32_t& job, uint32_t& level) {
    if (!player || !Readable(player, P_INFO + 4)) return "no character in the slot";
    const uint8_t flags = *(const uint8_t*)(uintptr_t)(player + P_FLAGS);
    if (flags & 8) return "setLevel leaves it (flag 8 at +0x206D)";
    if ((flags & 2) && *(const uint8_t*)(uintptr_t)(player + P_HELD)) return "the level-up refuses it (+0x31EA)";
    if ((flags & 4) && (U32(player + P_STATE) & 0x8000)) return "the level-up refuses it (+0x273C)";
    info = U32(player + P_INFO);
    if (!info || !Readable(info, I_LEVEL + 4)) return "no cPlayerInfo";
    job = U32(info + I_JOB);
    level = U32(info + I_LEVEL);
    if (job < 1 || job > 9) return "no vocation";
    if (level < 1 || level > MAX_LEVEL) return "a level outside 1..200";
    const uint32_t table = U32(mgr + MGR_LEVELS + 4 * job);
    if (!table || !Readable(table, T_ROWS + 4)) return "no level table for its vocation";
    const uint32_t count = U32(table + T_COUNT), rows = U32(table + T_ROWS);
    if (count < 3 || count > 4096 || !Readable(rows, 4 * count)) return "a level table without its rows";
    for (uint32_t i = 0; i < count; i++)
        if (!Readable(U32(rows + 4 * i), 0x12)) return "a level table row that cannot be read";
    const uint32_t exp = U32(mgr + MGR_EXP);
    if (!exp || !Readable(exp, T_ROWS + 4) || !Readable(U32(exp + T_ROWS), 16)) return "no experience table";
    for (uint32_t i = 0; i < 4; i++)
        if (!Readable(U32(U32(exp + T_ROWS) + 4 * i), 12)) return "an experience table row that cannot be read";
    return nullptr;
}

void Recalc(int which) {
    uint32_t mgr = 0;
    if (!Readable(PLAYERS, 4) || !(mgr = U32(PLAYERS)) || !Readable(mgr, MGR_SLOT[1] + 4)) {
        Note("rest: no player manager; nothing changed");
        return;
    }
    for (int s = 0; s < 2; s++) {
        if (!g_who[s]) continue;
        const uint32_t player = U32(mgr + MGR_SLOT[s]);
        uint32_t info = 0, job = 0, level = 0;
        if (const char* why = Unfit(mgr, player, info, job, level)) {
            Note("rest (%c): %s left as it is: %s", which, WHO[s], why);
            continue;
        }
        Saved saved;
        memcpy(saved.stats, (const void*)(uintptr_t)(info + I_HP), sizeof saved.stats);
        saved.level = level;
        DWORD code = 0;
        if (!SetLevelGuarded(player, info, level, saved, code)) {
            Note("rest (%c): %s: the game's level code faulted (0x%08lX); its level, stats and experience are put "
                 "back", which, WHO[s], code);
            continue;
        }
        // setLevel zeroes the experience toward the next level; it is the player's.
        memcpy((void*)(uintptr_t)(info + I_EXP), saved.stats + (I_EXP - I_HP), 4);
        if (U32(info + I_LEVEL) != level) {
            memcpy((void*)(uintptr_t)(info + I_HP), saved.stats, sizeof saved.stats);
            *(uint32_t*)(uintptr_t)(info + I_LEVEL) = level;
            Note("rest (%c): %s: setLevel ended at level %u, not %u; put back", which, WHO[s], U32(info + I_LEVEL),
                 level);
            continue;
        }
        auto was = [&](uint32_t off) { return Whole(*(const uint32_t*)(saved.stats + (off - I_HP))); };
        auto now = [&](uint32_t off) { return Whole(U32(info + off)); };
        Note("rest (%c): %s, %s level %u: HP %d -> %d, stamina %d -> %d, attack %d -> %d, defence %d -> %d, "
             "magick attack %d -> %d, magick defence %d -> %d",
             which, WHO[s], VOCATION[job], level, was(I_HP), now(I_HP), was(I_STAMINA), now(I_STAMINA), was(0x2B4),
             now(0x2B4), was(0x2B8), now(0x2B8), was(0x2BC), now(0x2BC), was(0x2C0), now(0x2C0));
    }
}

void __cdecl RecalcA() { Recalc('A'); }
void __cdecl RecalcB() { Recalc('B'); }

uintptr_t g_restA = REST_A, g_restB = REST_B;

#define SAVE_STATE                     \
    __asm pushfd                       \
    __asm pushad                       \
    __asm sub esp, 0x80                \
    __asm movups [esp + 0x00], xmm0    \
    __asm movups [esp + 0x10], xmm1    \
    __asm movups [esp + 0x20], xmm2    \
    __asm movups [esp + 0x30], xmm3    \
    __asm movups [esp + 0x40], xmm4    \
    __asm movups [esp + 0x50], xmm5    \
    __asm movups [esp + 0x60], xmm6    \
    __asm movups [esp + 0x70], xmm7
#define RESTORE_STATE                  \
    __asm movups xmm0, [esp + 0x00]    \
    __asm movups xmm1, [esp + 0x10]    \
    __asm movups xmm2, [esp + 0x20]    \
    __asm movups xmm3, [esp + 0x30]    \
    __asm movups xmm4, [esp + 0x40]    \
    __asm movups xmm5, [esp + 0x50]    \
    __asm movups xmm6, [esp + 0x60]    \
    __asm movups xmm7, [esp + 0x70]    \
    __asm add esp, 0x80                \
    __asm popad                        \
    __asm popfd

// In place of `call 0x00737580` (the inn pushed): [esp] is the call's return address, [esp+4] the inn.
__declspec(naked) void RestThunkA() {
    SAVE_STATE
    __asm call RecalcA
    RESTORE_STATE
    __asm jmp dword ptr [g_restA]
}

// In place of `call 0x007377D0` (the inn in esi).
__declspec(naked) void RestThunkB() {
    SAVE_STATE
    __asm call RecalcB
    RESTORE_STATE
    __asm jmp dword ptr [g_restB]
}

bool WriteCall(uintptr_t at, uintptr_t target) {
    uint8_t code[5] = {0xE8};
    int32_t rel = (int32_t)(target - (at + 5));
    memcpy(code + 1, &rel, 4);
    DWORD old;
    if (!VirtualProtect((void*)at, 5, PAGE_EXECUTE_READWRITE, &old)) return false;
    memcpy((void*)at, code, 5);
    VirtualProtect((void*)at, 5, old, &old);
    FlushInstructionCache(GetCurrentProcess(), (void*)at, 5);
    return true;
}

// [vocation_stats] Mode and Who from the ini next to this file; current and party when missing.
bool LoadSettings(HMODULE self, bool& on, bool who[2]) {
    wchar_t ini[MAX_PATH];
    GetModuleFileNameW(self, ini, MAX_PATH);
    wchar_t* dot = wcsrchr(ini, L'.');
    wchar_t* slash = wcsrchr(ini, L'\\');
    if (dot && (!slash || dot > slash)) *dot = 0;
    wcscat_s(ini, L".ini");
    auto read = [&](const wchar_t* key, const wchar_t* fallback, wchar_t* v) {
        GetPrivateProfileStringW(L"vocation_stats", key, fallback, v, 32, ini);
        wchar_t* s = v;
        while (*s == L' ' || *s == L'\t') s++;
        size_t n = wcslen(s);
        while (n && (s[n - 1] == L' ' || s[n - 1] == L'\t')) s[--n] = 0;
        if (s != v) memmove(v, s, (n + 1) * sizeof(wchar_t));
    };
    wchar_t mode[32], whom[32];
    read(L"Mode", L"current", mode);
    read(L"Who", L"party", whom);
    if (!_wcsicmp(mode, L"current")) on = true;
    else if (!_wcsicmp(mode, L"off")) on = false;
    else return Log("vocation_stats.ini: Mode = %S is not current or off; nothing is patched", mode), false;
    if (!_wcsicmp(whom, L"party")) who[0] = who[1] = true;
    else if (!_wcsicmp(whom, L"arisen")) who[0] = true, who[1] = false;
    else if (!_wcsicmp(whom, L"pawn")) who[0] = false, who[1] = true;
    else return Log("vocation_stats.ini: Who = %S is not party, arisen or pawn; nothing is patched", whom), false;
    return true;
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
    _snwprintf_s(g_logPath, MAX_PATH, _TRUNCATE, L"%s\\riftstone\\logs\\vocation_stats.log", root);
    // A fresh log per game session; a second copy of the plugin in the same process appends to it.
    wchar_t probe[8];
    if (GetEnvironmentVariableW(L"RIFTSTONE_VOCATION_STATS_LOG", probe, 8) == 0) {
        DeleteFileW(g_logPath);
        SetEnvironmentVariableW(L"RIFTSTONE_VOCATION_STATS_LOG", L"1");
    }

    // The game is a fixed-base image; a test harness that maps DDDA.exe itself sets the variable.
    bool harness = GetEnvironmentVariableW(L"RIFTSTONE_VOCATION_STATS_HARNESS", probe, 8) > 0;
    if (!harness && (uintptr_t)GetModuleHandleW(nullptr) != IMAGE_BASE) {
        Log("refused: the host is not DDDA.exe at 0x00400000");
        return;
    }
    bool on = true, who[2] = {true, true};
    if (!LoadSettings(self, on, who)) return;
    if (!on) {
        Log("vocation_stats: Mode = off: nothing patched");
        return;
    }
    if (!Verify()) return;
    g_who[0] = who[0];
    g_who[1] = who[1];
    if (!WriteCall(SITE_A, (uintptr_t)&RestThunkA) || !WriteCall(SITE_B, (uintptr_t)&RestThunkB)) {
        Log("failed: VirtualProtect at the rest calls (%lu)", GetLastError());
        return;
    }
    Log("vocation_stats: Mode = current, Who = %s (%s)", who[0] && who[1] ? "party" : who[0] ? "arisen" : "pawn",
        harness ? "harness" : "game");
    Log("  0x%08X, 0x%08X  an inn's rest recalculates the stats by the current vocation, then rests",
        (unsigned)SITE_A, (unsigned)SITE_B);
}

}  // namespace

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(module);
        Start(module);
    }
    return TRUE;
}

// economy_harness_core -- runs economy inside the real game code, without the game.
//
//   economy_stub.exe <DDDA.exe> <economy.asi> <profile> <buy> <sell> <quest gold> <found gold>
//                    <quest crystals> <found crystals> <enemies> <gathering> <data folder | ->
//
// Maps DDDA.exe's image at its fixed base (0x00400000) over the stub's image, loads the plugin (which
// verifies and patches the mapped code), then runs the game's own code:
//   - the item tables' loader (0x0045D730) through the patched call, on a fake sItemManager whose
//     sResource hands it the game's item list and both drop tables (read by run_tests.py from the player's
//     archives into the data folder, never stored here); every price is held against the rule, the
//     registers against the loader run unpatched, and a second load of the same tables, a fresh load at
//     the same place and one at a new place against the first;
//   - both drop rolls (0x00460F60 enemies, 0x00460EC0 gathering) on every set before and after, for every
//     r = 1..100 (the game's generator set to give r - 1): the chance of an item must be the rule's, the
//     items keep their shares, and nothing new can drop;
//   - addGold (0x004431E0) and addRiftPoint (0x00443310) through each patched call, against the game's own
//     call with the plugin's amount: the same state and registers;
//   - the pickup (0x0045F250) of every coin purse and Rift Crystal item on the rescaled list.
// The digests ("items", "sets", "price", "pickup" lines) are for run_tests.py's own model.  Prints
// "pass"/"FAIL" lines and exits 0 when everything passed, 1 on a failure, 2 when the image cannot be
// mapped here (reported as a skip by run_tests.py).
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shellapi.h>

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <string>
#include <vector>

namespace {
constexpr uintptr_t BASE = 0x00400000;
constexpr uintptr_t LOAD_CALL = 0x004FA8C3, LOAD = 0x0045D730;
constexpr uintptr_t ADD_GOLD = 0x004431E0, ADD_RIFT = 0x00443310;
constexpr uintptr_t QUEST_GOLD[] = {0x0078FE6D, 0x007905C1, 0x007918FA};
constexpr uintptr_t HIT_GOLD = 0x0084E525;
constexpr uintptr_t QUEST_RIFT[] = {0x007905F8, 0x00791398};
constexpr uintptr_t PURSE_CALL = 0x0045F2D7, CRYSTAL_CALL = 0x0045F2F3;   // the pickup's own calls
constexpr uintptr_t PICKUP = 0x0045F250;           // sItemManager: use an item (ecx, eax = who, the id)
constexpr uintptr_t ENEMY_ROLL = 0x00460F60;       // eax = the table; the set's id, the 8 results
constexpr uintptr_t GATHER_ROLL = 0x00460EC0;      // ecx = sItemManager, eax = the set; the result
constexpr uintptr_t HUD_NOTICE = 0x006547C0;       // the HUD's "got N" message: a ret 4 here
constexpr uintptr_t RNG = 0x018D017C;              // the generator rand() (0x00D00690) steps
constexpr uintptr_t GAME_SYS = 0x018FA4BC, ITEMS = 0x018FA4D4, COCKPIT = 0x018FA4CC, RESOURCE = 0x018D0AA0;
constexpr uintptr_t TROPHIES = 0x018D0CC4;         // read once the gold earned passes 10,000,000
constexpr uintptr_t LOCKING = 0x018D0BB0;
constexpr uintptr_t IAT_ENTER = 0x0139D160, IAT_LEAVE = 0x0139D164;
constexpr uint32_t VT_ITEM_LIST = 0x01617218, VT_SET_TABLE = 0x01617380;   // the classes' own vtables
constexpr uint32_t DTI_ITEM_LIST = 0x019A85F4, DTI_SET_TABLE = 0x019A8654;
constexpr uintptr_t TAMPER = 0x00460FFF;           // the enemy roll's 100: the byte the tampered profile changes
constexpr uint32_t SYS_SIZE = 0xC0000, ARISEN = 0xA76D0, GOLD = 0x348, CRYSTALS = 0x34C;
constexpr uint32_t ITEM_SIZE = 0x80, SET_SIZE = 0x2C, FILE_SET = 0x28;
constexpr uint32_t CAP = 10000000;

int g_fails = 0;
void Check(bool ok, const std::string& what) {
    printf("  %s  %s\n", ok ? "pass" : "FAIL", what.c_str());
    if (!ok) g_fails++;
}
std::string F(const char* fmt, ...) {
    char s[400];
    va_list ap;
    va_start(ap, fmt);
    _vsnprintf_s(s, sizeof s, _TRUNCATE, fmt, ap);
    va_end(ap);
    return s;
}

// ---- the rule, written again here --------------------------------------------------------------
struct Mille {
    uint32_t buy, sell, questGold, foundGold, questRift, foundRift, enemies, gathering;
};
Mille m;

uint32_t ScaleV(uint32_t v, uint32_t mille) {
    if (v == 0 || mille == 1000) return v;
    uint64_t out = ((uint64_t)v * mille + 500) / 1000;
    if (out == 0) out = 1;
    uint32_t top = v > CAP ? v : CAP;
    return out > top ? top : (uint32_t)out;
}
bool TablesOn() {
    return m.buy != 1000 || m.sell != 1000 || m.foundGold != 1000 || m.foundRift != 1000 || m.enemies != 1000 ||
           m.gathering != 1000;
}
bool PricesOn() { return m.buy != 1000 || m.sell != 1000 || m.foundGold != 1000 || m.foundRift != 1000; }

// One item's prices after the plugin.
void ExpectPrices(const uint8_t* rec, uint32_t& buy, uint32_t& sell) {
    buy = *(const uint32_t*)(rec + 0x48);
    sell = *(const uint32_t*)(rec + 0x4C);
    if (!PricesOn()) return;
    const uint32_t flags = *(const uint32_t*)(rec + 0x58);
    if (flags & (1u << 25)) {
        sell = ScaleV(sell, m.foundGold);
    } else if (flags & (1u << 26)) {
        sell = ScaleV(sell, m.foundRift);
    } else {
        const uint32_t b = ScaleV(buy, m.buy), s = ScaleV(sell, m.sell);
        sell = sell <= buy && s > b ? b : s;
        buy = b;
    }
}

// ---- the fake world ------------------------------------------------------------------------------
struct Table {
    uint8_t obj[0x80];       // the resource: vtable, +0x60 records, +0x64 count
    std::vector<uint8_t> original, records;
    uint32_t count, size;
    void Make(uint32_t vtable, const std::vector<uint8_t>& recs, uint32_t recSize) {
        memset(obj, 0, sizeof obj);
        *(uint32_t*)obj = vtable;
        original = recs;
        size = recSize;
        count = (uint32_t)(recs.size() / recSize);
        records = recs;
        Point();
    }
    void Point() {
        *(uint32_t*)(obj + 0x60) = (uint32_t)(uintptr_t)records.data();
        *(uint32_t*)(obj + 0x64) = count;
    }
    void Restore() { memcpy(records.data(), original.data(), original.size()); }
    uint8_t* At(uint32_t i) { return records.data() + (size_t)i * size; }
    const uint8_t* Orig(uint32_t i) const { return original.data() + (size_t)i * size; }
};
Table g_list, g_sets, g_em;
uint8_t g_dummy[0x100];
uint8_t* g_sys;
uint8_t g_manager[0x200];
uint8_t g_cockpit[0x100];
uint8_t* g_trophies;
uint32_t g_resourceVt[0x20];
uint8_t g_resource[0x40];
uint32_t g_creates = 0, g_badDti = 0, g_releases = 0;
CRITICAL_SECTION g_lock;

uint32_t __fastcall FakeCreate(void*, void*, uint32_t dti, const char* name, uint32_t) {
    g_creates++;
    if (!strcmp(name, "etc\\item\\itemList")) {
        if (dti != DTI_ITEM_LIST) g_badDti++;
        return (uint32_t)(uintptr_t)g_list.obj;
    }
    if (!strcmp(name, "etc\\item\\itemSetTbl")) {
        if (dti != DTI_SET_TABLE) g_badDti++;
        return (uint32_t)(uintptr_t)g_sets.obj;
    }
    if (!strcmp(name, "etc\\item\\ItemEmListSetTbl")) {
        if (dti != DTI_SET_TABLE) g_badDti++;
        return (uint32_t)(uintptr_t)g_em.obj;
    }
    return (uint32_t)(uintptr_t)g_dummy;
}
void __fastcall FakeRelease(void*, void*, uint32_t) { g_releases++; }

void SetupWorld() {
    g_sys = (uint8_t*)VirtualAlloc(nullptr, SYS_SIZE, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    g_trophies = (uint8_t*)VirtualAlloc(nullptr, 0x6000, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    for (uint32_t& s : g_resourceVt) s = 0;
    g_resourceVt[0x30 / 4] = (uint32_t)(uintptr_t)&FakeCreate;
    g_resourceVt[0x38 / 4] = (uint32_t)(uintptr_t)&FakeRelease;
    *(uint32_t*)g_resource = (uint32_t)(uintptr_t)g_resourceVt;
    InitializeCriticalSection(&g_lock);
    *(uint32_t*)RESOURCE = (uint32_t)(uintptr_t)g_resource;
    *(uint32_t*)GAME_SYS = (uint32_t)(uintptr_t)g_sys;
    *(uint32_t*)ITEMS = (uint32_t)(uintptr_t)g_manager;
    *(uint32_t*)COCKPIT = (uint32_t)(uintptr_t)g_cockpit;
    *(uint32_t*)TROPHIES = (uint32_t)(uintptr_t)g_trophies;
    *(uint8_t*)LOCKING = 0;
    *(uint32_t*)IAT_ENTER = (uint32_t)(uintptr_t)&EnterCriticalSection;
    *(uint32_t*)IAT_LEAVE = (uint32_t)(uintptr_t)&LeaveCriticalSection;
    // The HUD's message for a pickup is not part of what is checked: it returns at once here.
    DWORD old;
    VirtualProtect((void*)HUD_NOTICE, 3, PAGE_EXECUTE_READWRITE, &old);
    memcpy((void*)HUD_NOTICE, "\xC2\x04\x00", 3);
}

void ResetManager() { memset(g_manager, 0, sizeof g_manager); }

// ---- calling the game ------------------------------------------------------------------------------
struct Regs {
    uint32_t eax, ecx, edx, ebx, esi, edi, ebp, esp;
    alignas(16) uint8_t xmm[0x80];
};
uint32_t g_savedEsp, g_callEsp, g_esi, g_edi, g_eax, g_arg1, g_arg2;
uintptr_t g_target;
alignas(16) uint8_t g_seed[0x80];
Regs g_out;

#define SEED_XMM                                \
    __asm movups xmm0, [g_seed + 0x00]          \
    __asm movups xmm1, [g_seed + 0x10]          \
    __asm movups xmm2, [g_seed + 0x20]          \
    __asm movups xmm3, [g_seed + 0x30]          \
    __asm movups xmm4, [g_seed + 0x40]          \
    __asm movups xmm5, [g_seed + 0x50]          \
    __asm movups xmm6, [g_seed + 0x60]          \
    __asm movups xmm7, [g_seed + 0x70]
#define SAVE_OUT                                \
    __asm mov g_out.eax, eax                    \
    __asm mov g_out.ecx, ecx                    \
    __asm mov g_out.edx, edx                    \
    __asm mov g_out.ebx, ebx                    \
    __asm mov g_out.esi, esi                    \
    __asm mov g_out.edi, edi                    \
    __asm mov g_out.ebp, ebp                    \
    __asm mov g_out.esp, esp                    \
    __asm movups [g_out.xmm + 0x00], xmm0       \
    __asm movups [g_out.xmm + 0x10], xmm1       \
    __asm movups [g_out.xmm + 0x20], xmm2       \
    __asm movups [g_out.xmm + 0x30], xmm3       \
    __asm movups [g_out.xmm + 0x40], xmm4       \
    __asm movups [g_out.xmm + 0x50], xmm5       \
    __asm movups [g_out.xmm + 0x60], xmm6       \
    __asm movups [g_out.xmm + 0x70], xmm7

// The loader's call: esi = sItemManager, nothing on the stack.
__declspec(naked) void InvokeLoadAsm() {
    __asm {
        pushad
        mov g_savedEsp, esp
    }
    SEED_XMM
    __asm {
        mov eax, 0x55555555
        mov ecx, 0x66666666
        mov edx, 0x77777777
        mov ebx, 0x11111111
        mov esi, g_esi
        mov edi, 0x33333333
        mov ebp, 0x44444444
        mov g_callEsp, esp
        call dword ptr [g_target]
    }
    SAVE_OUT
    __asm {
        mov esp, g_savedEsp
        popad
        ret
    }
}

// addGold's call: edi = the amount; sGameSys and notify on the stack.
__declspec(naked) void InvokeGoldAsm() {
    __asm {
        pushad
        mov g_savedEsp, esp
    }
    SEED_XMM
    __asm {
        mov eax, 0x55555555
        mov ecx, 0x66666666
        mov edx, 0x77777777
        mov ebx, 0x11111111
        mov esi, 0x22222222
        mov edi, g_edi
        mov ebp, 0x44444444
        push g_arg2
        push g_arg1
        mov g_callEsp, esp
        call dword ptr [g_target]
    }
    SAVE_OUT
    __asm {
        mov esp, g_savedEsp
        popad
        ret
    }
}

// addRiftPoint's call: eax = sGameSys; the amount and notify on the stack.
__declspec(naked) void InvokeRiftAsm() {
    __asm {
        pushad
        mov g_savedEsp, esp
    }
    SEED_XMM
    __asm {
        mov eax, g_eax
        mov ecx, 0x66666666
        mov edx, 0x77777777
        mov ebx, 0x11111111
        mov esi, 0x22222222
        mov edi, 0x33333333
        mov ebp, 0x44444444
        push g_arg2
        push g_arg1
        mov g_callEsp, esp
        call dword ptr [g_target]
    }
    SAVE_OUT
    __asm {
        mov esp, g_savedEsp
        popad
        ret
    }
}

void Seed(uint32_t n) {
    for (int i = 0; i < 0x80; i++) g_seed[i] = (uint8_t)(0x5A ^ (i * 37) ^ n);
    memset(&g_out, 0, sizeof g_out);
}
bool SameRegs(const Regs& a, const Regs& b) {
    return a.eax == b.eax && a.ecx == b.ecx && a.edx == b.edx && a.ebx == b.ebx && a.esi == b.esi && a.edi == b.edi &&
           a.ebp == b.ebp && a.esp == b.esp && memcmp(a.xmm, b.xmm, sizeof a.xmm) == 0;
}

uintptr_t CallTarget(uintptr_t site) {
    const uint8_t* p = (const uint8_t*)site;
    if (p[0] != 0xE8) return 0;
    int32_t rel;
    memcpy(&rel, p + 1, 4);
    return site + 5 + rel;
}

// The game's generator gives r - 1 for its first three draws from {0, 0, 0, r - 1}.
void SetRoll(uint32_t r) {
    uint32_t* s = (uint32_t*)RNG;
    s[0] = s[1] = s[2] = 0;
    s[3] = r - 1;
}

uintptr_t g_enemyRoll = ENEMY_ROLL, g_gatherRoll = GATHER_ROLL, g_pickup = PICKUP;

int EnemyRoll(uintptr_t table, int32_t id, int32_t* results) {
    int n;
    __asm {
        push results
        push id
        mov eax, table
        call dword ptr [g_enemyRoll]
        mov n, eax
    }
    return n;
}
uint8_t GatherRoll(uintptr_t manager, uint32_t index, int32_t* results) {
    uint8_t ok;
    __asm {
        push results
        mov eax, index
        mov ecx, manager
        call dword ptr [g_gatherRoll]
        mov ok, al
    }
    return ok;
}
uint8_t Pickup(uintptr_t manager, uint32_t who, uint32_t id) {
    uint8_t ok;
    __asm {
        push id
        mov eax, who
        mov ecx, manager
        call dword ptr [g_pickup]
        mov ok, al
    }
    return ok;
}

// What one set gives for r = 1..100, through the game's own roll: per r the id dropped, or -1.
void Outcomes(const uint8_t* rec, bool gathering, int32_t* got) {
    alignas(4) uint8_t one[SET_SIZE];
    memcpy(one, rec, SET_SIZE);
    *(int16_t*)(one + 0x0A) = 1;   // one roll a call
    uint8_t table[0x80] = {0};
    *(uint32_t*)table = VT_SET_TABLE;
    *(uint32_t*)(table + 0x60) = (uint32_t)(uintptr_t)one;
    *(uint32_t*)(table + 0x64) = 1;
    uint8_t manager[0x200] = {0};
    *(uint32_t*)(manager + 0x120) = (uint32_t)(uintptr_t)table;
    for (uint32_t r = 1; r <= 100; r++) {
        SetRoll(r);
        int32_t out[8];
        if (gathering) {
            out[0] = -1;
            const uint8_t ok = GatherRoll((uintptr_t)manager, 0, out);
            got[r - 1] = ok && out[0] > 0 ? out[0] : -1;   // its caller drops a negative id
        } else {
            const int n = EnemyRoll((uintptr_t)table, *(const int16_t*)(one + 4), out);
            got[r - 1] = n >= 1 ? out[0] : -1;
        }
    }
}

bool MapImage(const wchar_t* exe) {
    HANDLE f = CreateFileW(exe, GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, 0, nullptr);
    if (f == INVALID_HANDLE_VALUE) return printf("cannot open the exe\n"), false;
    DWORD size = GetFileSize(f, nullptr), got = 0;
    std::vector<uint8_t> file(size);
    ReadFile(f, file.data(), size, &got, nullptr);
    CloseHandle(f);
    auto* dos = (IMAGE_DOS_HEADER*)file.data();
    auto* nt = (IMAGE_NT_HEADERS32*)(file.data() + dos->e_lfanew);
    if (nt->OptionalHeader.ImageBase != BASE) return printf("unexpected image base\n"), false;
    auto* host = (const uint8_t*)GetModuleHandleW(nullptr);
    auto* hostNt = (const IMAGE_NT_HEADERS32*)(host + ((const IMAGE_DOS_HEADER*)host)->e_lfanew);
    DWORD need = nt->OptionalHeader.SizeOfImage;
    if ((uintptr_t)host != BASE || hostNt->OptionalHeader.SizeOfImage < need)
        return printf("SKIP: run me through economy_stub.exe (host at %p, 0x%X bytes)\n", (void*)host,
                      (unsigned)hostNt->OptionalHeader.SizeOfImage),
               false;
    void* at = (void*)BASE;
    DWORD old;
    if (!VirtualProtect(at, need, PAGE_EXECUTE_READWRITE, &old)) return printf("cannot unprotect the host image\n"), false;
    memset(at, 0, need);
    memcpy(at, file.data(), nt->OptionalHeader.SizeOfHeaders);
    auto* sec = IMAGE_FIRST_SECTION(nt);
    for (int i = 0; i < nt->FileHeader.NumberOfSections; i++, sec++) {
        DWORD n = min(sec->SizeOfRawData, sec->Misc.VirtualSize ? sec->Misc.VirtualSize : sec->SizeOfRawData);
        if (n && sec->PointerToRawData + n <= size)
            memcpy((uint8_t*)at + sec->VirtualAddress, file.data() + sec->PointerToRawData, n);
    }
    return true;
}

std::vector<uint8_t> ReadAll(const std::wstring& path) {
    std::vector<uint8_t> d;
    HANDLE f = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, 0, nullptr);
    if (f == INVALID_HANDLE_VALUE) return d;
    DWORD size = GetFileSize(f, nullptr), got = 0;
    d.resize(size);
    ReadFile(f, d.data(), size, &got, nullptr);
    CloseHandle(f);
    d.resize(got);
    return d;
}

// A drop set in memory: four bytes, then the file's record (id, -1, a count, rolls, ids[8], weights[8]).
void PutSet(std::vector<uint8_t>& out, int16_t id, int16_t rolls, const int16_t* ids, const int16_t* weights) {
    uint8_t rec[SET_SIZE] = {0};
    *(int16_t*)(rec + 4) = id;
    *(int16_t*)(rec + 6) = -1;
    *(int16_t*)(rec + 0x0A) = rolls;
    memcpy(rec + 0x0C, ids, 16);
    memcpy(rec + 0x1C, weights, 16);
    out.insert(out.end(), rec, rec + SET_SIZE);
}
std::vector<uint8_t> SetsFromFile(const std::vector<uint8_t>& file) {
    std::vector<uint8_t> out;
    for (size_t at = 0; at + FILE_SET <= file.size(); at += FILE_SET) {
        uint8_t rec[SET_SIZE] = {0};
        memcpy(rec + 4, file.data() + at, FILE_SET);
        out.insert(out.end(), rec, rec + SET_SIZE);
    }
    return out;
}
// Sets no shipped table has, added to both: a negative weight, nothing at all, eight 1 % items, one weight
// past 100, an id-0 band with nothing after it, and a -1 band ahead of the items.
void AddEdgeSets(std::vector<uint8_t>& out, int16_t base) {
    const int16_t a[8] = {5, 6, 7, 0, 0, 0, 0, 0}, wa[8] = {30, -4, 20, 0, 0, 0, 0, 0};
    const int16_t b[8] = {-1, -1, -1, -1, -1, -1, -1, -1}, wb[8] = {0, 0, 0, 0, 0, 0, 0, 0};
    const int16_t c[8] = {11, 12, 13, 14, 15, 16, 17, 18}, wc[8] = {1, 1, 1, 1, 1, 1, 1, 1};
    const int16_t d[8] = {21, -1, 0, 0, 0, 0, 0, 0}, wd[8] = {200, 10, 0, 0, 0, 0, 0, 0};
    const int16_t e[8] = {31, 0, 0, 0, 0, 0, 0, 0}, we[8] = {40, 30, 0, 0, 0, 0, 0, 0};
    const int16_t f[8] = {-1, 41, 42, 0, 0, 0, 0, 0}, wf[8] = {50, 10, 5, 0, 0, 0, 0, 0};
    PutSet(out, base + 0, 1, a, wa);
    PutSet(out, base + 1, 1, b, wb);
    PutSet(out, base + 2, 1, c, wc);
    PutSet(out, base + 3, 1, d, wd);
    PutSet(out, base + 4, 1, e, we);
    PutSet(out, base + 5, 1, f, wf);
}

// A stand-in item list when the game's could not be read: the loader's fixes need 0x567 records.
std::vector<uint8_t> StandInItems() {
    std::vector<uint8_t> out(0x600 * ITEM_SIZE, 0);
    for (uint32_t i = 0; i < 0x600; i++) {
        uint8_t* r = out.data() + i * ITEM_SIZE;
        *(uint32_t*)(r + 0x3C) = i;
        *(uint32_t*)(r + 0x48) = i * 25;
        *(uint32_t*)(r + 0x4C) = i * 10;
        if (i % 97 == 5) *(uint32_t*)(r + 0x58) = 1u << 25;
        if (i % 89 == 7) *(uint32_t*)(r + 0x58) = 1u << 26;
    }
    return out;
}
}  // namespace

static int Run(int argc, wchar_t** argv);

// Called by economy_stub.exe.  Never returns: the stub's code is gone once DDDA.exe is mapped.
extern "C" __declspec(dllexport) void HarnessMain() {
    int argc = 0;
    wchar_t** argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    int code = Run(argc, argv);
    fflush(stdout);
    ExitProcess((UINT)code);
}

namespace {
uint32_t g_number = 0;

// Every price of the list as it is now against the rule applied to the game's own (with the loader's fixes).
bool PricesAsRule(const std::vector<uint8_t>& fixed, std::string& where) {
    for (uint32_t i = 0; i < g_list.count; i++) {
        uint32_t buy, sell;
        ExpectPrices(fixed.data() + (size_t)i * ITEM_SIZE, buy, sell);
        const uint8_t* r = g_list.At(i);
        if (*(const uint32_t*)(r + 0x48) != buy || *(const uint32_t*)(r + 0x4C) != sell)
            return where = F("item %u: %u/%u, the rule says %u/%u", i, *(const uint32_t*)(r + 0x48),
                             *(const uint32_t*)(r + 0x4C), buy, sell),
                   false;
        if (memcmp(r, fixed.data() + (size_t)i * ITEM_SIZE, 0x48) ||
            memcmp(r + 0x50, fixed.data() + (size_t)i * ITEM_SIZE + 0x50, ITEM_SIZE - 0x50))
            return where = F("item %u: a byte besides the prices changed", i), false;
    }
    return true;
}

// The game's loader through the call at 0x004FA8C3 (whatever is there now) or unpatched.
Regs Load(bool viaPatch, uint32_t seed) {
    g_target = viaPatch ? CallTarget(LOAD_CALL) : LOAD;
    g_esi = (uint32_t)(uintptr_t)g_manager;
    Seed(seed);
    InvokeLoadAsm();
    g_out.esp -= g_callEsp;   // the stack as the call left it, from where the call was made
    return g_out;
}

struct SetStats {
    uint32_t scaled = 0, before = 0, after = 0, bad = 0;
};

// FNV-1a over a run of bytes: run_tests.py computes the same over its own model of the tables.
uint32_t Fnv(uint32_t h, const uint8_t* p, size_t n) {
    for (size_t i = 0; i < n; i++) h = (h ^ p[i]) * 16777619u;
    return h;
}
uint32_t SetsDigest(Table& t) {
    uint32_t h = 2166136261u;
    for (uint32_t i = 0; i < t.count; i++) h = Fnv(h, t.At(i) + 0x1C, 16);
    return h;
}

// Every set of a table, the game's own roll before and after.
SetStats CheckSets(Table& t, bool gathering, uint32_t mille, const char* what) {
    SetStats st;
    std::string first;
    for (uint32_t i = 0; i < t.count; i++) {
        const uint8_t* o = t.Orig(i);
        const uint8_t* p = t.At(i);
        int32_t a[100], b[100];
        Outcomes(o, gathering, a);
        Outcomes(p, gathering, b);
        uint32_t ia = 0, ib = 0;
        for (int r = 0; r < 100; r++) ia += a[r] >= 0, ib += b[r] >= 0;
        st.before += ia;
        st.after += ib;
        const bool same = memcmp(o, p, SET_SIZE) == 0;
        if (!same) st.scaled++;
        std::string why;
        if (memcmp(o, p, 0x1C)) why = "a byte besides the weights changed";
        uint32_t want = ia;
        if (mille != 1000 && ia > 0) {
            uint64_t v = ((uint64_t)ia * mille + 500) / 1000;
            want = v < 1 ? 1 : v > 100 ? 100 : (uint32_t)v;
        }
        if (why.empty() && (mille == 1000 || ia == 0) && !same) why = "changed though it is not to be";
        if (why.empty() && ib != want) why = F("%u of 100 rolls give an item, the rule says %u (it was %u)", ib, want, ia);
        // Each id: its share of the new chance, within one roll a slot it holds (and a roll for each slot a
        // weight of 1 was given to); nothing new; nothing lost while the chance has room for every slot.
        if (why.empty() && ia > 0) {
            const int16_t* ids = (const int16_t*)(o + 0x0C);
            const int16_t* ws = (const int16_t*)(o + 0x1C);
            int items = 0;
            for (int s = 0; s < 8; s++) items += ws[s] > 0 && (gathering ? ids[s] > 0 : ids[s] >= 0);
            for (int r = 0; r < 100 && why.empty(); r++) {
                if (b[r] < 0) continue;
                bool known = false;
                for (int k = 0; k < 100; k++) known |= a[k] == b[r];
                if (!known) why = F("id %d drops after but never before", b[r]);
            }
            for (int r = 0; r < 100 && why.empty(); r++) {
                if (a[r] < 0) continue;
                const int32_t id = a[r];
                int ca = 0, cb = 0, slots = 0;
                for (int k = 0; k < 100; k++) ca += a[k] == id, cb += b[k] == id;
                for (int s = 0; s < 8; s++) slots += ids[s] == id;
                const double share = (double)ca * ib / ia;
                if (cb < share - slots - 1 - items || cb > share + slots + 1)
                    why = F("id %d: %d rolls after, %d before (%.2f its share)", id, cb, ca, share);
                else if ((int)ib >= items && cb == 0)
                    why = F("id %d could drop before and cannot after", id);
            }
        }
        if (!why.empty()) {
            if (!st.bad) first = F("set %u (id %d): %s", i, *(const int16_t*)(o + 4), why.c_str());
            st.bad++;
        }
    }
    Check(st.bad == 0, F("%s: every set through the game's roll for r = 1..100 holds the rule", what) +
                           (st.bad ? F(" (%u sets do not; the first: %s)", st.bad, first.c_str()) : std::string()));
    return st;
}

// A reward through a call site against the game's own function with the plugin's amount.
void RewardCase(uintptr_t site, uintptr_t game, bool gold, uint32_t amount, uint32_t mille, bool patched,
                uint32_t start, const char* what) {
    g_number++;
    const uintptr_t via = CallTarget(site);
    const uint32_t want = patched ? ((int32_t)amount <= 0 ? amount : ScaleV(amount, mille)) : amount;
    uint8_t* arisen = g_sys + ARISEN;
    auto reset = [&]() {
        memset(g_sys, 0, SYS_SIZE);
        *(uint32_t*)(arisen + (gold ? GOLD : CRYSTALS)) = start;
    };
    auto call = [&](uintptr_t target, uint32_t value) {
        g_target = target;
        Seed(g_number);
        if (gold) {
            g_edi = value;
            g_arg1 = (uint32_t)(uintptr_t)g_sys;
            g_arg2 = 1;
            InvokeGoldAsm();
        } else {
            g_eax = (uint32_t)(uintptr_t)g_sys;
            g_arg1 = value;
            g_arg2 = 1;
            InvokeRiftAsm();
        }
        g_out.esp -= g_callEsp;
        return g_out;
    };
    reset();
    Regs a = call(via, amount);
    std::vector<uint8_t> sa(g_sys, g_sys + SYS_SIZE);
    const uint32_t got = *(uint32_t*)(arisen + (gold ? GOLD : CRYSTALS));
    reset();
    Regs b = call(game, want);
    std::vector<uint8_t> sb(g_sys, g_sys + SYS_SIZE);
    size_t diff = 0;
    while (diff < SYS_SIZE && sa[diff] == sb[diff]) diff++;
    Check(diff == SYS_SIZE && SameRegs(a, b) && a.esp == 8 && a.ebx == 0x11111111 && a.ebp == 0x44444444,
          F("%s at 0x%08X: %d becomes %d (%s now %u); the state and every register as the game's own call with it",
            what, (unsigned)site, (int32_t)amount, (int32_t)want, gold ? "gold" : "crystals", got) +
              (diff < SYS_SIZE ? F(" (sGameSys differs at +0x%X)", (unsigned)diff) : std::string()));
}
}  // namespace

static int Run(int argc, wchar_t** argv) {
    setvbuf(stdout, nullptr, _IONBF, 0);  // a fault in the game code must not take the lines before it along
    if (argc < 13) {
        printf("usage: economy_stub <DDDA.exe> <economy.asi> <profile> <buy> <sell> <quest gold> <found gold> "
               "<quest crystals> <found crystals> <enemies> <gathering> <data folder | ->\n");
        return 1;
    }
    const std::wstring profile = argv[3];
    uint32_t* fields[] = {&m.buy, &m.sell, &m.questGold, &m.foundGold, &m.questRift, &m.foundRift, &m.enemies,
                          &m.gathering};
    for (int i = 0; i < 8; i++) *fields[i] = (uint32_t)_wtoi(argv[4 + i]);
    const std::wstring data = argv[12];
    const bool refused = profile.rfind(L"bogus", 0) == 0 || profile == L"tampered";
    const bool late = profile == L"late";
    if (refused || profile == L"vanilla") m = {1000, 1000, 1000, 1000, 1000, 1000, 1000, 1000};
    if (!MapImage(argv[1])) return 2;

    bool sitesOk = CallTarget(LOAD_CALL) == LOAD && CallTarget(HIT_GOLD) == ADD_GOLD &&
                   CallTarget(PURSE_CALL) == ADD_GOLD && CallTarget(CRYSTAL_CALL) == ADD_RIFT;
    for (uintptr_t q : QUEST_GOLD) sitesOk &= CallTarget(q) == ADD_GOLD;
    for (uintptr_t q : QUEST_RIFT) sitesOk &= CallTarget(q) == ADD_RIFT;
    Check(sitesOk, "the mapped game code calls the loader, addGold and addRiftPoint from the sites the plugin uses");

    // The tables: the game's (from the data folder) or a stand-in, and the edge sets.
    std::vector<uint8_t> items, sets, em;
    if (data != L"-") {
        items = ReadAll(data + L"\\items.bin");
        sets = SetsFromFile(ReadAll(data + L"\\sets.bin"));
        em = SetsFromFile(ReadAll(data + L"\\emsets.bin"));
    }
    const bool standIn = items.size() < 0x567 * ITEM_SIZE || items.size() % ITEM_SIZE || sets.empty() || em.empty();
    if (standIn) {
        printf("  note  the stand-in tables are used\n");
        items = StandInItems();
        sets.clear();
        em.clear();
        const int16_t ids[8] = {1, 2, 0, -1, 3, 0, 0, 0}, w1[8] = {30, 30, 10, 20, 10, 0, 0, 0};
        PutSet(sets, 100, 1, ids, w1);
        const int16_t ids2[8] = {4, 5, -1, 0, 0, 0, 0, 0}, w2[8] = {10, 15, 30, 0, 0, 0, 0, 0};
        PutSet(em, 200, 2, ids2, w2);
    }
    AddEdgeSets(sets, 30000);
    AddEdgeSets(em, 30000);
    SetupWorld();
    g_list.Make(VT_ITEM_LIST, items, ITEM_SIZE);
    g_sets.Make(VT_SET_TABLE, sets, SET_SIZE);
    g_em.Make(VT_SET_TABLE, em, SET_SIZE);
    printf("tables: %u items, %u gathering sets, %u enemy sets (six of each added)\n", g_list.count, g_sets.count,
           g_em.count);

    // The game's own load, unpatched: the list with the loader's five fixes, and the registers it leaves.
    ResetManager();
    Regs vanilla = Load(false, 7);
    Check(g_creates == 10 && g_badDti == 0 && *(uint32_t*)(g_manager + 0x110) == (uint32_t)(uintptr_t)g_list.obj &&
              *(uint32_t*)(g_manager + 0x120) == (uint32_t)(uintptr_t)g_sets.obj &&
              *(uint32_t*)(g_manager + 0x124) == (uint32_t)(uintptr_t)g_em.obj,
          F("the game's loader asks sResource for its ten tables (%u) by their classes and keeps them", g_creates));
    const std::vector<uint8_t> fixed = g_list.records;   // the shipped list with the loader's fixes
    {
        static const uint32_t FIXES[5][2] = {{0x44, 10}, {0x45, 10}, {0x65, 50}, {0x565, 140}, {0x566, 140}};
        bool ok = true;
        for (auto& f : FIXES) ok &= *(const uint32_t*)(g_list.At(f[0]) + 0x4C) == f[1];
        Check(ok, "the loader fixes five sell prices (items 68, 69, 101, 1381, 1382: 10, 10, 50, 140, 140)");
    }
    if (!late) {
        g_list.Restore();
        ResetManager();
    }

    if (profile == L"tampered") *(uint8_t*)TAMPER = 0x65;
    SetEnvironmentVariableW(L"RIFTSTONE_ECONOMY_HARNESS", L"1");
    HMODULE plugin = LoadLibraryW(argv[2]);
    printf("plugin (profile %S)\n", profile.c_str());
    Check(plugin != nullptr, "the plugin loads");
    if (profile == L"tampered") *(uint8_t*)TAMPER = 0x64;
    uintptr_t lo = (uintptr_t)plugin, hi = lo;
    if (plugin) {
        auto* nt = (const IMAGE_NT_HEADERS32*)(lo + ((const IMAGE_DOS_HEADER*)lo)->e_lfanew);
        hi = lo + nt->OptionalHeader.SizeOfImage;
    }
    auto into = [&](uintptr_t site) {
        uintptr_t t = CallTarget(site);
        return t >= lo && t < hi;
    };
    const bool tables = TablesOn(), qGold = m.questGold != 1000, hGold = m.foundGold != 1000,
               qRift = m.questRift != 1000;
    bool shape = into(LOAD_CALL) == tables && into(HIT_GOLD) == hGold && CallTarget(PURSE_CALL) == ADD_GOLD &&
                 CallTarget(CRYSTAL_CALL) == ADD_RIFT;
    for (uintptr_t q : QUEST_GOLD) shape &= into(q) == qGold;
    for (uintptr_t q : QUEST_RIFT) shape &= into(q) == qRift;
    Check(shape, F("the calls go through the plugin exactly where a multiplier is not 1.0 (tables %d, quest gold %d, "
                   "hit gold %d, quest crystals %d)", tables, qGold, hGold, qRift));
    const bool any = tables || qGold || hGold || qRift;
    if (any) {
        // A second copy of the plugin must see the patched calls and change nothing.
        wchar_t copy[MAX_PATH], ini[MAX_PATH], copyIni[MAX_PATH];
        wcscpy_s(copy, argv[2]);
        wchar_t* dot = wcsrchr(copy, L'.');
        if (dot) *dot = 0;
        wcscpy_s(ini, copy);
        wcscat_s(ini, L".ini");
        wcscpy_s(copyIni, copy);
        wcscat_s(copyIni, L"_copy.ini");
        wcscat_s(copy, L"_copy.asi");
        uint8_t before[5 * 7];
        const uintptr_t all[7] = {LOAD_CALL, QUEST_GOLD[0], QUEST_GOLD[1], QUEST_GOLD[2], HIT_GOLD, QUEST_RIFT[0],
                                  QUEST_RIFT[1]};
        for (int i = 0; i < 7; i++) memcpy(before + 5 * i, (const void*)all[i], 5);
        if (CopyFileW(argv[2], copy, FALSE) && CopyFileW(ini, copyIni, FALSE)) {
            HMODULE second = LoadLibraryW(copy);
            bool same = true;
            for (int i = 0; i < 7; i++) same &= memcmp(before + 5 * i, (const void*)all[i], 5) == 0;
            Check(second != nullptr && same, "a second copy refuses the patched calls and changes nothing");
        }
    }
    if (g_fails) return 1;

    if (late) {
        std::string where;
        Check(PricesAsRule(fixed, where), "loaded after the tables: the list it found is rescaled at once" +
                                              (where.empty() ? std::string() : " (" + where + ")"));
        g_list.Restore();
        g_sets.Restore();
        g_em.Restore();
        ResetManager();
    }

    // Through the patch: the same registers as the game's own load, and the rule's prices.
    printf("the item tables' load through the call at 0x004FA8C3\n");
    Regs via = Load(true, 7);
    Check(SameRegs(via, vanilla) && via.esp == 0 && via.esi == (uint32_t)(uintptr_t)g_manager &&
              via.ebx == 0x11111111 && via.edi == 0x33333333 && via.ebp == 0x44444444,
          "every register and xmm0-7 as the game's own load left them");
    if (!SameRegs(via, vanilla) || via.esp != 0)
        printf("  regs  eax %08X/%08X ecx %08X/%08X edx %08X/%08X ebx %08X/%08X esi %08X/%08X edi %08X/%08X "
               "ebp %08X/%08X esp %+d/%+d xmm %s\n",
               via.eax, vanilla.eax, via.ecx, vanilla.ecx, via.edx, vanilla.edx, via.ebx, vanilla.ebx, via.esi,
               vanilla.esi, via.edi, vanilla.edi, via.ebp, vanilla.ebp, (int)via.esp, (int)vanilla.esp,
               memcmp(via.xmm, vanilla.xmm, sizeof via.xmm) ? "differ" : "same");
    std::string where;
    Check(PricesAsRule(fixed, where), "every price is the rule's on the game's own (and the loader's fixes)" +
                                          (where.empty() ? std::string() : " (" + where + ")"));
    const std::vector<uint8_t> listOnce = g_list.records, setsOnce = g_sets.records, emOnce = g_em.records;
    uint64_t buySum = 0, sellSum = 0;
    uint32_t changed = 0;
    for (uint32_t i = 0; i < g_list.count; i++) {
        const uint8_t* r = g_list.At(i);
        const uint8_t* f = fixed.data() + (size_t)i * ITEM_SIZE;
        buySum += *(const uint32_t*)(r + 0x48);
        sellSum += *(const uint32_t*)(r + 0x4C);
        changed += memcmp(r + 0x48, f + 0x48, 8) != 0;
    }
    uint32_t priceHash = 2166136261u;
    for (uint32_t i = 0; i < g_list.count; i++) priceHash = Fnv(priceHash, g_list.At(i) + 0x48, 8);
    printf("items %u changed %u buy %llu sell %llu fnv %08x\n", g_list.count, changed, buySum, sellSum, priceHash);
    uint32_t arbitrage = 0;
    for (uint32_t i = 0; i < g_list.count; i++) {
        const uint8_t* r = g_list.At(i);
        const uint8_t* f = fixed.data() + (size_t)i * ITEM_SIZE;
        const bool special = *(const uint32_t*)(f + 0x58) & (3u << 25);
        if (!special && *(const uint32_t*)(f + 0x4C) <= *(const uint32_t*)(f + 0x48) &&
            *(const uint32_t*)(r + 0x4C) > *(const uint32_t*)(r + 0x48))
            arbitrage++;
    }
    Check(arbitrage == 0, F("no item sells for more than it costs where the game's own prices kept it so (%u do)",
                            arbitrage));
    for (uint32_t id : {1u, 0x44u, 0x45u, 0x65u, 219u, 1223u, 1226u, 1381u, 1749u})
        if (id < g_list.count)
            printf("price %u %u %u\n", id, *(const uint32_t*)(g_list.At(id) + 0x48), *(const uint32_t*)(g_list.At(id) + 0x4C));

    // The drop sets, through the game's own rolls.
    printf("the drop sets through the game's own rolls\n");
    SetStats ss = CheckSets(g_sets, true, m.gathering, "gathering (0x00460EC0)");
    SetStats se = CheckSets(g_em, false, m.enemies, "enemies (0x00460F60)");
    printf("sets gathering %u scaled %u before %u after %u fnv %08x\n", g_sets.count, ss.scaled, ss.before, ss.after,
           SetsDigest(g_sets));
    printf("sets enemies %u scaled %u before %u after %u fnv %08x\n", g_em.count, se.scaled, se.before, se.after,
           SetsDigest(g_em));

    // The same tables handed back: not scaled again.  A fresh load in the same place, and one in a new place:
    // scaled once, as the first.
    Load(true, 8);
    Check(g_list.records == listOnce && g_sets.records == setsOnce && g_em.records == emOnce && g_releases >= 10,
          "the same tables handed back (released and asked for again) are not scaled twice");
    g_list.Restore();
    g_sets.Restore();
    g_em.Restore();
    Load(true, 9);
    Check(g_list.records == listOnce && g_sets.records == setsOnce && g_em.records == emOnce,
          "a fresh load in the same place is scaled once, as the first");
    {
        std::vector<uint8_t> moved = g_list.original;
        g_list.records.swap(moved);
        std::vector<uint8_t> movedSets = g_sets.original;
        g_sets.records.swap(movedSets);
        std::vector<uint8_t> movedEm = g_em.original;
        g_em.records.swap(movedEm);
        g_list.Point();
        g_sets.Point();
        g_em.Point();
        Load(true, 10);
        Check(g_list.records == listOnce && g_sets.records == setsOnce && g_em.records == emOnce,
              "a fresh load in a new place is scaled once, as the first");
    }

    // The pickup of every coin purse and Rift Crystal item on the rescaled list: the game's own code pays
    // the new amount.
    printf("the pickup (0x0045F250) of every coin purse and Rift Crystal item\n");
    uint32_t purses = 0, crystals = 0, bad = 0;
    std::string firstBad;
    for (uint32_t id = 0; id < g_list.count; id++) {
        const uint32_t flags = *(const uint32_t*)(fixed.data() + (size_t)id * ITEM_SIZE + 0x58);
        const bool purse = flags & (1u << 25), crystal = !purse && (flags & (1u << 26));
        if (!purse && !crystal) continue;
        uint32_t buy, sell;
        ExpectPrices(fixed.data() + (size_t)id * ITEM_SIZE, buy, sell);
        memset(g_sys, 0, SYS_SIZE);
        *(uint32_t*)(g_manager + 0x110) = (uint32_t)(uintptr_t)g_list.obj;
        const uint8_t ok = Pickup((uintptr_t)g_manager, 4, id);
        const uint32_t got = *(uint32_t*)(g_sys + ARISEN + (purse ? GOLD : CRYSTALS));
        printf("pickup %u %s %u\n", id, purse ? "gold" : "crystals", got);
        if (!ok || got != sell) {
            if (!bad) firstBad = F("item %u gave %u, the rule says %u", id, got, sell);
            bad++;
        }
        purses += purse;
        crystals += crystal;
    }
    Check(bad == 0 && (standIn || (purses == 4 && crystals == 10)),
          F("%u coin purses and %u Rift Crystal items pay the rescaled amount", purses, crystals) +
              (bad ? " (" + firstBad + ")" : std::string()));

    // Gold and crystals through each call the plugin can take.
    printf("addGold and addRiftPoint through the reward calls\n");
    const uint32_t amounts[] = {1, 9, 100, 3500, 15000, 250000, 9999999, 30000000, 0, 0xFFFFFF9Cu};
    for (uint32_t a : amounts) {
        for (uintptr_t s : QUEST_GOLD) RewardCase(s, ADD_GOLD, true, a, m.questGold, qGold, 1000, "a quest's gold");
        RewardCase(HIT_GOLD, ADD_GOLD, true, a, m.foundGold, hGold, 1000, "a hit's gold");
        for (uintptr_t s : QUEST_RIFT)
            RewardCase(s, ADD_RIFT, false, a, m.questRift, qRift, 1000, "a quest's Rift Crystals");
    }
    RewardCase(QUEST_GOLD[1], ADD_GOLD, true, 15000, m.questGold, qGold, 99990000, "a quest's gold at the purse's cap");
    RewardCase(QUEST_RIFT[0], ADD_RIFT, false, 15000, m.questRift, qRift, 99990000,
               "a quest's Rift Crystals at their cap");

    printf(g_fails ? "\n%d check(s) FAILED\n" : "\nall checks passed\n", g_fails);
    return g_fails ? 1 : 0;
}

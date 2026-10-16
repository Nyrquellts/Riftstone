// economy -- Riftstone native plugin: shop prices, gold, Rift Crystals and drop chances (docs/economy.md).
//
// What the game does (DDDA.exe build 2364871)
//   sItemManager ([0x018FA4D4]) loads its tables in one function (0x0045D730, called only from 0x004FA8C3
//   with esi = sItemManager): +0x110 etc\item\itemList (rItemList: 0x80-byte records at [+0x60], their
//   count at [+0x64]; +0x48 the buy price, +0x4C the sell price, +0x58 flags: bit 25 a coin purse, bit 26
//   a Rift Crystal item), +0x120 etc\item\itemSetTbl (gathering) and +0x124 etc\item\ItemEmListSetTbl
//   (enemies' drops), both rItemSetTbl: 0x2C-byte sets at [+0x60], their count at [+0x64]; a set's item
//   ids are s16[8] at +0x0C, their weights s16[8] at +0x1C.  It ends by fixing five sell prices.
//     - A shop charges the buy price and pays the sell price (the shop's own gold add and take,
//       0x006E08D0 and 0x006E0950).
//     - A coin purse gives its sell price in gold (0x0045F2AB, doubled by an ability at 0x0045F2CB), a
//       Rift Crystal item its sell price in crystals (0x0045F2E8).
//     - A drop roll draws r = rand() % 100 + 1 and takes the first weighted slot whose running sum of
//       weights reaches r: an enemy's (0x00460F60) gives that slot's id when it is not negative, a
//       gathering spot's (0x00460EC0) when it is not 0 (a weighted id-0 slot passes its band on to the
//       next weighted slot), and its caller drops a negative id (0x0049CEBC).
//   Gold goes through sGameSys's addGold (0x004431E0: the amount in edi), Rift Crystals through
//   addRiftPoint (0x00443310: the amount on the stack).  A quest's gold: 0x0078FE6D, 0x007905C1,
//   0x007918FA; gold from a hit: 0x0084E525; a quest's Rift Crystals: 0x007905F8, 0x00791398.
//
// What this plugin does (economy.ini, next to this file)
//   The call at 0x004FA8C3 goes through the plugin, which runs the game's loader, then rescales what it
//   loaded:
//     [shops] Buy, Sell        every item's buy and sell price (0.1 to 5.0); selling never pays more
//                              than buying costs where the game's own prices kept it so
//     [gold] Found             a coin purse's gold (0.1 to 10.0)
//     [crystals] Found         a Rift Crystal item's crystals (0.1 to 10.0)
//     [drops] Enemies,         the chance each roll of an enemy's drop set, or a gathering spot's,
//             Gathering        gives an item (0.1 to 5.0, at most every roll); the items keep their
//                              shares of that chance, and a slot that could drop keeps a weight of 1
//   and the calls that hand out gold or crystals go through it:
//     [gold] Quests, Found     a quest's gold and a hit's gold (0.1 to 10.0)
//     [crystals] Quests        a quest's Rift Crystals (0.1 to 10.0)
//   Whole numbers only, rounded to the nearest, at least 1 when the game gave at least 1, at most
//   10,000,000 (a price times the shop's 370 % stays below 2^32, and a reward stays exact through
//   addGold's float conversion).  A table rescaled once is not rescaled again when the game hands the
//   same one back; a fresh load is.  A multiplier of 1.0 leaves its part as the game has it; all 1.0
//   patches nothing.
//
// Safety
//   DDDA.exe build 2364871 only.  The loader's call and end, the tables' layouts (their loaders and
//   getters), the rolls, the pickup, addGold, addRiftPoint and the six reward calls are compared first;
//   on any difference nothing is patched and riftstone\logs\economy.log says why.  The tables are read and
//   written under SEH: a fault leaves them as they are.  Integer arithmetic only; every register, the
//   flags and xmm0-7 come back as they went in.  Original code; no third-party source.
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
constexpr uintptr_t ITEMS = 0x018FA4D4;              // sItemManager::mpInstance
constexpr uint32_t OFF_LIST = 0x110, OFF_SETS = 0x120, OFF_EM_SETS = 0x124;
constexpr uint32_t OFF_RECORDS = 0x60, OFF_COUNT = 0x64;
constexpr uint32_t ITEM_SIZE = 0x80, SET_SIZE = 0x2C;
constexpr uint32_t ITEM_BUY = 0x48, ITEM_SELL = 0x4C, ITEM_FLAGS = 0x58;
constexpr uint32_t FLAG_GOLD = 1u << 25, FLAG_CRYSTALS = 1u << 26;
constexpr uint32_t SET_IDS = 0x0C, SET_WEIGHTS = 0x1C;
constexpr uintptr_t LOAD_CALL = 0x004FA8C3;          // call the loader, esi = sItemManager
constexpr uintptr_t LOAD = 0x0045D730;               // sItemManager: load the item tables
constexpr uintptr_t ADD_GOLD = 0x004431E0;           // addGold: edi the amount; sGameSys, notify on the stack
constexpr uintptr_t ADD_RIFT = 0x00443310;           // addRiftPoint: eax sGameSys; the amount, notify on the stack
constexpr uintptr_t QUEST_GOLD[] = {0x0078FE6D, 0x007905C1, 0x007918FA};
constexpr uintptr_t HIT_GOLD = 0x0084E525;
constexpr uintptr_t QUEST_RIFT[] = {0x007905F8, 0x00791398};
constexpr uint32_t VALUE_CAP = 10000000;             // the most a price or a reward becomes
constexpr uint32_t MAX_RECORDS = 0x10000;

struct Expect {
    const char* what;
    uintptr_t at;
    uint8_t bytes[36];
    uint8_t len;
};

// The code the patch relies on: a second copy of the plugin, another patch or another build shows up
// here as a difference.
const Expect CONTEXT[] = {
    {"the item tables' load: esi = sItemManager, call 0x0045D730", 0x004FA8BD,
     {0x8B, 0x35, 0xD4, 0xA4, 0x8F, 0x01, 0xE8, 0x68, 0x2E, 0xF6, 0xFF}, 11},
    {"the loader: its start (the item list at +0x110)", LOAD,
     {0x8B, 0x8E, 0x10, 0x01, 0x00, 0x00, 0x57, 0x33, 0xFF, 0x3B, 0xCF, 0x74, 0x0B}, 13},
    {"the loader: its end fixes five sell prices (+0x4C) in the item list's records (+0x60)", 0x0045D93B,
     {0x8B, 0x86, 0x10, 0x01, 0x00, 0x00, 0x3B, 0xC7, 0x5F, 0x74, 0x67, 0x8B, 0x40, 0x60, 0x05, 0x00, 0x22, 0x00,
      0x00, 0xB9, 0x0A, 0x00, 0x00, 0x00, 0x74, 0x03, 0x89, 0x48, 0x4C}, 29},
    {"the loader: its last store and ret", 0x0045D9A8, {0x74, 0x03, 0x89, 0x48, 0x4C, 0xC3}, 6},
    {"rItemList's load: the count at +0x64", 0x00CBFB89, {0x89, 0x46, 0x64}, 3},
    {"rItemList's load: count x 0x80 bytes, the records at +0x60", 0x00CBFBB3,
     {0x8B, 0x4E, 0x64, 0xC1, 0xE1, 0x07, 0x51, 0x50, 0x8D, 0x4C, 0x24, 0x10, 0x89, 0x46, 0x60}, 15},
    {"rItemSetTbl's load: the sets at +0x60, their count at +0x64", 0x00CC061F,
     {0x89, 0x46, 0x60, 0x89, 0x7E, 0x64}, 6},
    {"rItemSetTbl's getter: set i at +0x60 + i x 0x2C", 0x00CC0246,
     {0x6B, 0xC0, 0x2C, 0x03, 0x46, 0x60, 0x5E, 0xC2, 0x04, 0x00}, 10},
    {"rItemSetTbl's count: +0x64", 0x0040E330, {0x8B, 0x41, 0x64, 0xC3}, 4},
    {"the gathering roll: r = rand() % 100 + 1 over the weights at +0x1C", 0x00460F04,
     {0x33, 0xD2, 0xB9, 0x64, 0x00, 0x00, 0x00, 0xF7, 0xF1, 0x8D, 0x43, 0x1C, 0x42}, 13},
    {"the gathering roll: the first weighted slot reaching r whose id (+0x0C) is not 0", 0x00460F13,
     {0x0F, 0xB7, 0x30, 0x66, 0x85, 0xF6, 0x7E, 0x12, 0x0F, 0xBF, 0xF6, 0x03, 0xF7, 0x3B, 0xD6, 0x7F, 0x07, 0x66,
      0x83, 0x78, 0xF0, 0x00, 0x75, 0x13, 0x8B, 0xFE}, 26},
    {"the enemy roll: r = rand() % 100 + 1 over the weights at +0x1C", 0x00460FFC,
     {0x33, 0xD2, 0xB9, 0x64, 0x00, 0x00, 0x00, 0xF7, 0xF1, 0x8D, 0x73, 0x1C, 0x42}, 13},
    {"the enemy roll: the first weighted slot reaching r; a negative id drops nothing", 0x00461010,
     {0x0F, 0xB7, 0x0E, 0x66, 0x85, 0xC9, 0x7E, 0x0B, 0x0F, 0xBF, 0xC9, 0x03, 0xCF, 0x3B, 0xD1, 0x7E, 0x0D, 0x8B,
      0xF9, 0x40, 0x83, 0xC6, 0x02, 0x83, 0xF8, 0x08, 0x72, 0xE4, 0xEB, 0x19, 0x0F, 0xBF, 0x44, 0x43, 0x0C, 0x85}, 36},
    {"the pickup: an item's record (id << 7) and flags (+0x58)", 0x0045F25F,
     {0x8B, 0xCD, 0xC1, 0xE1, 0x07, 0x03, 0x4A, 0x60, 0x56, 0x8B, 0x71, 0x58}, 12},
    {"the pickup: bit 25 is a coin purse", 0x0045F26E, {0x8B, 0xF8, 0xC1, 0xEA, 0x19, 0x32, 0xC0, 0xF6, 0xC2, 0x01, 0x74, 0x64}, 12},
    {"the pickup: a coin purse gives its sell price (+0x4C) through addGold", 0x0045F2AB,
     {0x8B, 0x49, 0x4C}, 3},
    {"the pickup: bit 26 is a Rift Crystal item, its sell price through addRiftPoint", 0x0045F2DE,
     {0x8B, 0xD6, 0xC1, 0xEA, 0x1A, 0xF6, 0xC2, 0x01, 0x74, 0x12, 0x8B, 0x41, 0x4C, 0x6A, 0x01, 0x50, 0xA1, 0xBC,
      0xA4, 0x8F, 0x01, 0xE8, 0x18, 0x40, 0xFE, 0xFF}, 26},
    {"addGold: its start (push ebx; push ebp; mov ebp, [esp+0xC])", ADD_GOLD,
     {0x53, 0x55, 0x8B, 0x6C, 0x24, 0x0C, 0x80, 0x7D, 0x1C, 0x00, 0x56}, 11},
    {"addGold: edi through float into the purse (+0x348), capped at 99,999,999", 0x0044320F,
     {0xF3, 0x0F, 0x2A, 0xC7, 0xF3, 0x0F, 0x2C, 0xD8}, 8},
    {"addGold: the purse and its cap", 0x00443236,
     {0x01, 0x9E, 0x48, 0x03, 0x00, 0x00, 0x81, 0xBE, 0x48, 0x03, 0x00, 0x00, 0xFF, 0xE0, 0xF5, 0x05}, 16},
    {"addRiftPoint: its start (push ebx; mov ebx, [esp+8])", ADD_RIFT,
     {0x53, 0x8B, 0x5C, 0x24, 0x08, 0x56, 0x8B, 0xF0, 0x80, 0x7E, 0x1C, 0x00, 0x57}, 13},
    {"addRiftPoint: the crystals (+0x34C) and their cap", 0x00443365,
     {0x01, 0x9F, 0x4C, 0x03, 0x00, 0x00, 0x81, 0xBF, 0x4C, 0x03, 0x00, 0x00, 0xFF, 0xE0, 0xF5, 0x05}, 16},
    {"a quest's gold by its kind: edi = the amount, call addGold", 0x0078FE62,
     {0xA1, 0xBC, 0xA4, 0x8F, 0x01, 0x8B, 0x7D, 0x08, 0x6A, 0x01, 0x50, 0xE8, 0x6E, 0x33, 0xCB, 0xFF}, 16},
    {"a quest's gold (cQuestCtrl::QuestTblResultProc): call addGold", 0x007905B6,
     {0x8B, 0x15, 0xBC, 0xA4, 0x8F, 0x01, 0x6A, 0x01, 0x52, 0x8B, 0xF8, 0xE8, 0x1A, 0x2C, 0xCB, 0xFF}, 16},
    {"a quest's gold: call addGold", 0x007918EF,
     {0x8B, 0x0D, 0xBC, 0xA4, 0x8F, 0x01, 0x6A, 0x01, 0x51, 0x8B, 0xF8, 0xE8, 0xE1, 0x18, 0xCB, 0xFF}, 16},
    {"a hit's gold (+0x224): call addGold", 0x0084E513,
     {0x8B, 0xBE, 0x24, 0x02, 0x00, 0x00, 0x85, 0xFF, 0x74, 0x2E, 0xA1, 0xBC, 0xA4, 0x8F, 0x01, 0x6A, 0x01, 0x50,
      0xE8, 0xB6, 0x4C, 0xBF, 0xFF}, 23},
    {"a quest's Rift Crystals (cQuestCtrl::QuestTblResultProc): call addRiftPoint", 0x007905E9,
     {0x8B, 0x43, 0x14, 0x85, 0xC0, 0x7E, 0x0D, 0x6A, 0x01, 0x50, 0xA1, 0xBC, 0xA4, 0x8F, 0x01, 0xE8, 0x13, 0x2D,
      0xCB, 0xFF}, 20},
    {"a quest's Rift Crystals by its kind: call addRiftPoint", 0x0079138D,
     {0x8B, 0x55, 0x08, 0xA1, 0xBC, 0xA4, 0x8F, 0x01, 0x6A, 0x01, 0x52, 0xE8, 0x73, 0x1F, 0xCB, 0xFF}, 16},
};

wchar_t g_logPath[MAX_PATH];

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
    for (const Expect& e : CONTEXT) {
        if (!Readable(e.at, e.len) || memcmp((const void*)e.at, e.bytes, e.len) != 0)
            return Log("refused: %s at 0x%08X is not the expected code (another build, or already patched)", e.what,
                       (unsigned)e.at),
                   false;
    }
    return true;
}

// ---- settings, per mille --------------------------------------------------------------------------
struct Settings {
    uint32_t buy, sell, questGold, foundGold, questRift, foundRift, enemies, gathering;
};
Settings g_set = {1000, 1000, 1000, 1000, 1000, 1000, 1000, 1000};

// v x mille / 1000, rounded to the nearest; at least 1 when v is; at most VALUE_CAP (or v, when the game's
// own value is already more).
uint32_t Scale(uint32_t v, uint32_t mille) {
    if (v == 0 || mille == 1000) return v;
    uint64_t out = ((uint64_t)v * mille + 500) / 1000;
    if (out == 0) out = 1;
    const uint32_t top = v > VALUE_CAP ? v : VALUE_CAP;
    return out > top ? top : (uint32_t)out;
}

// ---- the item list --------------------------------------------------------------------------------
// What the plugin wrote last, so a table the game hands back unchanged is not scaled twice.
struct Written {
    uintptr_t records = 0;
    uint32_t count = 0;
    uint32_t* values = nullptr;   // per record: the game's prices and the written ones, or the eight weights
    uint32_t perRecord = 0;
    uint32_t capacity = 0;
};
Written g_list, g_sets, g_emSets;

bool Prepare(Written& m, uintptr_t records, uint32_t count, uint32_t perRecord, bool& same) {
    same = m.records == records && m.count == count && m.values;
    if (!same) {
        if (m.capacity < count * perRecord) {
            uint32_t* grown = (uint32_t*)HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, count * perRecord * 4);
            if (!grown) return false;
            if (m.values) HeapFree(GetProcessHeap(), 0, m.values);
            m.values = grown;
            m.capacity = count * perRecord;
        }
        m.records = records;
        m.count = count;
        m.perRecord = perRecord;
    }
    return true;
}

struct Counts {
    uint32_t records, changed, kept;
};

Counts ScaleItems(uintptr_t list) {
    Counts c = {0, 0, 0};
    const uintptr_t records = *(const uint32_t*)(list + OFF_RECORDS);
    const uint32_t count = *(const uint32_t*)(list + OFF_COUNT);
    if (!records || !count || count > MAX_RECORDS || !Readable(records, (size_t)count * ITEM_SIZE)) return c;
    bool same;
    if (!Prepare(g_list, records, count, 4, same)) return c;
    c.records = count;
    for (uint32_t i = 0; i < count; i++) {
        uint8_t* rec = (uint8_t*)(records + (uintptr_t)i * ITEM_SIZE);
        uint32_t& buy = *(uint32_t*)(rec + ITEM_BUY);
        uint32_t& sell = *(uint32_t*)(rec + ITEM_SELL);
        uint32_t* w = g_list.values + 4 * i;   // the game's buy and sell, then what was written
        // A price equal to what was written last is that price scaled already (the same table handed back);
        // any other is the game's own (a fresh load, or the loader's fix of a sell price).
        const bool keptBuy = same && buy == w[2], keptSell = same && sell == w[3];
        if (keptBuy && keptSell) {
            c.kept++;
            continue;
        }
        const uint32_t ob = keptBuy ? w[0] : buy, os = keptSell ? w[1] : sell;
        const uint32_t flags = *(const uint32_t*)(rec + ITEM_FLAGS);
        uint32_t nb = ob, ns = os;
        if (flags & FLAG_GOLD) {
            ns = Scale(os, g_set.foundGold);
        } else if (flags & FLAG_CRYSTALS) {
            ns = Scale(os, g_set.foundRift);
        } else {
            nb = Scale(ob, g_set.buy);
            ns = Scale(os, g_set.sell);
            if (os <= ob && ns > nb) ns = nb;   // selling never pays more than buying costs
        }
        if (nb != ob || ns != os) c.changed++;
        buy = nb;
        sell = ns;
        w[0] = ob;
        w[1] = os;
        w[2] = nb;
        w[3] = ns;
    }
    return c;
}

// ---- drop sets ------------------------------------------------------------------------------------
// Hands `total` out over the slots in `pick` in proportion to share[i] (whose sum is `sum`), by the
// largest remainder, the lower slot first on a tie.
void Distribute(const int* share, const bool* pick, int sum, int total, int* out) {
    int rem[8], given = 0;
    for (int i = 0; i < 8; i++) {
        out[i] = 0;
        rem[i] = -1;
        if (!pick[i] || share[i] <= 0) continue;
        out[i] = share[i] * total / sum;
        rem[i] = share[i] * total % sum;
        given += out[i];
    }
    for (int left = total - given; left > 0; left--) {
        int best = -1;
        for (int i = 0; i < 8; i++)
            if (rem[i] >= 0 && (best < 0 || rem[i] > rem[best])) best = i;
        if (best < 0) break;
        out[best]++;
        rem[best] = -1;
    }
}

// One set (0x2C bytes): the chance a roll gives an item becomes that chance x mille / 1000 (at least 1 in
// 100, at most 100), shared by its item slots as before; the slots that give nothing shrink to fit.
// Returns true when its weights changed.
bool ScaleSet(uint8_t* rec, uint32_t mille, bool gathering) {
    const int16_t* ids = (const int16_t*)(rec + SET_IDS);
    int16_t* weights = (int16_t*)(rec + SET_WEIGHTS);
    int w[8];
    for (int i = 0; i < 8; i++) w[i] = weights[i];
    if (gathering) {
        // A weighted id-0 slot passes its band on to the next weighted slot whose id is not 0; a band no
        // such slot follows gives nothing, as an r no slot reaches does.
        int carry = 0;
        for (int i = 0; i < 8; i++) {
            if (w[i] <= 0) continue;
            if (ids[i] == 0) {
                carry += w[i];
                w[i] = 0;
            } else {
                w[i] += carry;
                carry = 0;
            }
        }
    }
    // What each slot gets of r = 1..100: the running sum stops at 100.
    int band[8] = {0}, sum = 0;
    for (int i = 0; i < 8; i++) {
        if (w[i] <= 0) continue;
        const int lo = sum < 100 ? sum : 100;
        sum += w[i];
        if (sum > 100000) sum = 100000;
        band[i] = (sum < 100 ? sum : 100) - lo;
    }
    bool item[8], none[8];
    int items = 0, nothing = 0, slots = 0;
    for (int i = 0; i < 8; i++) {
        item[i] = w[i] > 0 && (gathering ? ids[i] > 0 : ids[i] >= 0);
        none[i] = w[i] > 0 && !item[i];
        if (item[i]) items += band[i], slots += band[i] > 0;
        if (none[i]) nothing += band[i];
    }
    if (items == 0) return false;   // never gives an item: left as it is
    int target = (int)(((uint64_t)items * mille + 500) / 1000);
    if (target < 1) target = 1;
    if (target > 100) target = 100;
    int outItem[8], outNone[8];
    Distribute(band, item, items, target, outItem);
    // A slot that could drop keeps a weight of 1 while there is a weight to give it.
    if (target >= slots) {
        for (int i = 0; i < 8; i++) {
            if (!item[i] || band[i] <= 0 || outItem[i] > 0) continue;
            int donor = -1;
            for (int j = 0; j < 8; j++)
                if (outItem[j] > 1 && (donor < 0 || outItem[j] > outItem[donor])) donor = j;
            if (donor < 0) break;
            outItem[donor]--;
            outItem[i]++;
        }
    }
    int room = 100 - target;
    Distribute(band, none, nothing, nothing < room ? nothing : room, outNone);
    bool changed = false;
    for (int i = 0; i < 8; i++) {
        if (weights[i] <= 0) continue;   // a slot the roll skips stays as it is
        const int16_t v = (int16_t)(item[i] ? outItem[i] : none[i] ? outNone[i] : 0);
        if (v != weights[i]) changed = true;
        weights[i] = v;
    }
    return changed;
}

Counts ScaleSets(uintptr_t table, uint32_t mille, bool gathering, Written& m) {
    Counts c = {0, 0, 0};
    const uintptr_t records = *(const uint32_t*)(table + OFF_RECORDS);
    const uint32_t count = *(const uint32_t*)(table + OFF_COUNT);
    if (!records || !count || count > MAX_RECORDS || !Readable(records, (size_t)count * SET_SIZE)) return c;
    bool same;
    if (!Prepare(m, records, count, 4, same)) return c;
    c.records = count;
    for (uint32_t i = 0; i < count; i++) {
        uint8_t* rec = (uint8_t*)(records + (uintptr_t)i * SET_SIZE);
        uint32_t* w = m.values + 4 * i;
        if (same && memcmp(rec + SET_WEIGHTS, w, 16) == 0) {
            c.kept++;
            continue;
        }
        if (ScaleSet(rec, mille, gathering)) c.changed++;
        memcpy(w, rec + SET_WEIGHTS, 16);
    }
    return c;
}

volatile LONG g_loads = 0;

// After the game's loader: the tables sItemManager now holds.
void __cdecl Rescale(uintptr_t manager) {
    LONG load = 0;
    Counts items = {0, 0, 0}, sets = {0, 0, 0}, emSets = {0, 0, 0};
    __try {
        if (!manager) return;
        const uintptr_t list = *(const uint32_t*)(manager + OFF_LIST);
        const uintptr_t gather = *(const uint32_t*)(manager + OFF_SETS);
        const uintptr_t enemy = *(const uint32_t*)(manager + OFF_EM_SETS);
        if (!list && !gather && !enemy) return;   // nothing loaded yet
        load = InterlockedIncrement(&g_loads);
        const Settings& s = g_set;
        if (list && (s.buy != 1000 || s.sell != 1000 || s.foundGold != 1000 || s.foundRift != 1000))
            items = ScaleItems(list);
        if (gather && s.gathering != 1000) sets = ScaleSets(gather, s.gathering, true, g_sets);
        if (enemy && s.enemies != 1000) emSets = ScaleSets(enemy, s.enemies, false, g_emSets);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        Log("load %ld: a fault reading the item tables; what was not rescaled stays as the game has it", load);
        return;
    }
    if (load <= 8)
        Log("load %ld: %u items (%u prices changed, %u already), %u gathering sets (%u changed, %u already), "
            "%u enemy sets (%u changed, %u already)",
            load, items.records, items.changed, items.kept, sets.records, sets.changed, sets.kept, emSets.records,
            emSets.changed, emSets.kept);
}

// ---- rewards --------------------------------------------------------------------------------------
enum Kind : uint32_t { QUEST_GOLD_KIND, HIT_GOLD_KIND, QUEST_RIFT_KIND };
volatile LONG g_logged[3] = {0, 0, 0};
const char* const KIND_NAME[3] = {"a quest's gold", "a hit's gold", "a quest's Rift Crystals"};

uint32_t __cdecl ScaleReward(uint32_t amount, uint32_t kind) {
    const uint32_t mille = kind == QUEST_GOLD_KIND ? g_set.questGold
                         : kind == HIT_GOLD_KIND   ? g_set.foundGold
                                                   : g_set.questRift;
    if ((int32_t)amount <= 0) return amount;   // the game adds what it adds
    const uint32_t out = Scale(amount, mille);
    if (kind < 3 && !g_logged[kind] && !InterlockedExchange(&g_logged[kind], 1))
        Log("%s: %u became %u (logged once)", KIND_NAME[kind], amount, out);
    return out;
}

uint32_t g_load = LOAD, g_addGold = ADD_GOLD, g_addRift = ADD_RIFT;

// In place of the call at 0x004FA8C3: the game's loader (esi = sItemManager), then the rescale.
__declspec(naked) void LoadThunk() {
    __asm {
        call dword ptr [g_load]
        pushfd
        pushad
        sub esp, 0x80
        movups [esp + 0x00], xmm0
        movups [esp + 0x10], xmm1
        movups [esp + 0x20], xmm2
        movups [esp + 0x30], xmm3
        movups [esp + 0x40], xmm4
        movups [esp + 0x50], xmm5
        movups [esp + 0x60], xmm6
        movups [esp + 0x70], xmm7
        push esi
        call Rescale
        add esp, 4
        movups xmm0, [esp + 0x00]
        movups xmm1, [esp + 0x10]
        movups xmm2, [esp + 0x20]
        movups xmm3, [esp + 0x30]
        movups xmm4, [esp + 0x40]
        movups xmm5, [esp + 0x50]
        movups xmm6, [esp + 0x60]
        movups xmm7, [esp + 0x70]
        add esp, 0x80
        popad
        popfd
        ret
    }
}

// In place of a call to addGold: edi (the amount) scaled, then addGold with the caller's return address
// and arguments.  pushad keeps edi lowest, so after the xmm area it is at esp + 0x80.
#define GOLD_THUNK(name, kind)                       \
    __declspec(naked) void name() {                  \
        __asm pushfd                                 \
        __asm pushad                                 \
        __asm sub esp, 0x80                          \
        __asm movups [esp + 0x00], xmm0              \
        __asm movups [esp + 0x10], xmm1              \
        __asm movups [esp + 0x20], xmm2              \
        __asm movups [esp + 0x30], xmm3              \
        __asm movups [esp + 0x40], xmm4              \
        __asm movups [esp + 0x50], xmm5              \
        __asm movups [esp + 0x60], xmm6              \
        __asm movups [esp + 0x70], xmm7              \
        __asm push kind                              \
        __asm push edi                               \
        __asm call ScaleReward                       \
        __asm add esp, 8                             \
        __asm mov dword ptr [esp + 0x80], eax        \
        __asm movups xmm0, [esp + 0x00]              \
        __asm movups xmm1, [esp + 0x10]              \
        __asm movups xmm2, [esp + 0x20]              \
        __asm movups xmm3, [esp + 0x30]              \
        __asm movups xmm4, [esp + 0x40]              \
        __asm movups xmm5, [esp + 0x50]              \
        __asm movups xmm6, [esp + 0x60]              \
        __asm movups xmm7, [esp + 0x70]              \
        __asm add esp, 0x80                          \
        __asm popad                                  \
        __asm popfd                                  \
        __asm jmp dword ptr [g_addGold]              \
    }
GOLD_THUNK(QuestGoldThunk, 0)
GOLD_THUNK(HitGoldThunk, 1)

// In place of a call to addRiftPoint: the amount (the first stack argument, at esp + 0xA8 once the
// flags, the registers and the xmm area are saved) scaled, then addRiftPoint.
__declspec(naked) void QuestRiftThunk() {
    __asm {
        pushfd
        pushad
        sub esp, 0x80
        movups [esp + 0x00], xmm0
        movups [esp + 0x10], xmm1
        movups [esp + 0x20], xmm2
        movups [esp + 0x30], xmm3
        movups [esp + 0x40], xmm4
        movups [esp + 0x50], xmm5
        movups [esp + 0x60], xmm6
        movups [esp + 0x70], xmm7
        push 2
        mov eax, dword ptr [esp + 0xA8 + 4]
        push eax
        call ScaleReward
        add esp, 8
        mov dword ptr [esp + 0xA8], eax
        movups xmm0, [esp + 0x00]
        movups xmm1, [esp + 0x10]
        movups xmm2, [esp + 0x20]
        movups xmm3, [esp + 0x30]
        movups xmm4, [esp + 0x40]
        movups xmm5, [esp + 0x50]
        movups xmm6, [esp + 0x60]
        movups xmm7, [esp + 0x70]
        add esp, 0x80
        popad
        popfd
        jmp dword ptr [g_addRift]
    }
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

// "1.5" -> 1500 per mille.  Digits and at most one point; decimals past the third are dropped; lo..hi per mille.
bool ParseMille(const wchar_t* s, uint32_t lo, uint32_t hi, uint32_t& out) {
    uint64_t whole = 0, frac = 0;
    int fracDigits = 0, digits = 0;
    bool point = false;
    for (const wchar_t* p = s; *p; p++) {
        if (*p == L'.' && !point) {
            point = true;
            continue;
        }
        if (*p < L'0' || *p > L'9') return false;
        digits++;
        if (point) {
            if (fracDigits == 3) continue;   // finer than a thousandth
            frac = frac * 10 + (uint64_t)(*p - L'0');
            fracDigits++;
        } else {
            whole = whole * 10 + (uint64_t)(*p - L'0');
            if (whole > 100000) return false;
        }
    }
    if (!digits) return false;
    while (fracDigits < 3) frac *= 10, fracDigits++;
    uint64_t v = whole * 1000 + frac;
    if (v < lo || v > hi) return false;
    out = (uint32_t)v;
    return true;
}

struct Key {
    const wchar_t* section;
    const wchar_t* key;
    uint32_t lo, hi;
    uint32_t Settings::*field;
};
const Key KEYS[] = {
    {L"shops", L"Buy", 100, 5000, &Settings::buy},
    {L"shops", L"Sell", 100, 5000, &Settings::sell},
    {L"gold", L"Quests", 100, 10000, &Settings::questGold},
    {L"gold", L"Found", 100, 10000, &Settings::foundGold},
    {L"crystals", L"Quests", 100, 10000, &Settings::questRift},
    {L"crystals", L"Found", 100, 10000, &Settings::foundRift},
    {L"drops", L"Enemies", 100, 5000, &Settings::enemies},
    {L"drops", L"Gathering", 100, 5000, &Settings::gathering},
};

// Every key from the ini next to this file; 1.0 when missing.
bool LoadSettings(HMODULE self, Settings& s) {
    wchar_t ini[MAX_PATH];
    GetModuleFileNameW(self, ini, MAX_PATH);
    wchar_t* dot = wcsrchr(ini, L'.');
    wchar_t* slash = wcsrchr(ini, L'\\');
    if (dot && (!slash || dot > slash)) *dot = 0;
    wcscat_s(ini, L".ini");
    for (const Key& k : KEYS) {
        wchar_t v[32];
        GetPrivateProfileStringW(k.section, k.key, L"1.0", v, 32, ini);
        wchar_t* p = v;
        while (*p == L' ' || *p == L'\t') p++;
        size_t n = wcslen(p);
        while (n && (p[n - 1] == L' ' || p[n - 1] == L'\t')) p[--n] = 0;
        if (!ParseMille(p, k.lo, k.hi, s.*k.field))
            return Log("economy.ini: [%S] %S = %S is not a number from %u.%u to %u.%u; nothing is patched", k.section,
                       k.key, p, k.lo / 1000, k.lo % 1000 / 100, k.hi / 1000, k.hi % 1000 / 100),
                   false;
    }
    return true;
}

#define MILLE(v) (v) / 1000, (v) % 1000

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
    _snwprintf_s(g_logPath, MAX_PATH, _TRUNCATE, L"%s\\riftstone\\logs\\economy.log", root);
    // A fresh log per game session; a second copy of the plugin in the same process appends to it.
    wchar_t probe[8];
    if (GetEnvironmentVariableW(L"RIFTSTONE_ECONOMY_LOG", probe, 8) == 0) {
        DeleteFileW(g_logPath);
        SetEnvironmentVariableW(L"RIFTSTONE_ECONOMY_LOG", L"1");
    }

    // The game is a fixed-base image; a test harness that maps DDDA.exe itself sets the variable.
    bool harness = GetEnvironmentVariableW(L"RIFTSTONE_ECONOMY_HARNESS", probe, 8) > 0;
    if (!harness && (uintptr_t)GetModuleHandleW(nullptr) != IMAGE_BASE) {
        Log("refused: the host is not DDDA.exe at 0x00400000");
        return;
    }
    Settings s;
    if (!LoadSettings(self, s)) return;
    const bool tables = s.buy != 1000 || s.sell != 1000 || s.foundGold != 1000 || s.foundRift != 1000 ||
                        s.enemies != 1000 || s.gathering != 1000;
    const bool questGold = s.questGold != 1000, hitGold = s.foundGold != 1000, questRift = s.questRift != 1000;
    if (!tables && !questGold && !hitGold && !questRift) {
        Log("economy: every multiplier is 1.0: nothing patched");
        return;
    }
    if (!Verify()) return;
    g_set = s;
    bool ok = true;
    if (tables) ok &= WriteCall(LOAD_CALL, (uintptr_t)&LoadThunk);
    if (questGold)
        for (uintptr_t at : QUEST_GOLD) ok &= WriteCall(at, (uintptr_t)&QuestGoldThunk);
    if (hitGold) ok &= WriteCall(HIT_GOLD, (uintptr_t)&HitGoldThunk);
    if (questRift)
        for (uintptr_t at : QUEST_RIFT) ok &= WriteCall(at, (uintptr_t)&QuestRiftThunk);
    if (!ok) {
        Log("failed: VirtualProtect (%lu); some calls may be patched, the rest are not", GetLastError());
        return;
    }
    Log("economy: Buy x%u.%03u, Sell x%u.%03u, gold Quests x%u.%03u Found x%u.%03u, crystals Quests x%u.%03u "
        "Found x%u.%03u, drops Enemies x%u.%03u Gathering x%u.%03u (%s)",
        MILLE(s.buy), MILLE(s.sell), MILLE(s.questGold), MILLE(s.foundGold), MILLE(s.questRift), MILLE(s.foundRift),
        MILLE(s.enemies), MILLE(s.gathering), harness ? "harness" : "game");
    if (tables) Log("  0x%08X  the item tables' load goes through the plugin", (unsigned)LOAD_CALL);
    if (questGold) Log("  0x%08X 0x%08X 0x%08X  a quest's gold goes through the plugin", (unsigned)QUEST_GOLD[0],
                       (unsigned)QUEST_GOLD[1], (unsigned)QUEST_GOLD[2]);
    if (hitGold) Log("  0x%08X  a hit's gold goes through the plugin", (unsigned)HIT_GOLD);
    if (questRift) Log("  0x%08X 0x%08X  a quest's Rift Crystals go through the plugin", (unsigned)QUEST_RIFT[0],
                       (unsigned)QUEST_RIFT[1]);
    // Loaded after the game's tables (a late load): rescale what is there now.
    if (tables) {
        uintptr_t manager = 0;
        __try {
            manager = *(const uint32_t*)ITEMS;
        } __except (EXCEPTION_EXECUTE_HANDLER) {
            manager = 0;
        }
        if (manager) Rescale(manager);
    }
}

}  // namespace

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(module);
        Start(module);
    }
    return TRUE;
}

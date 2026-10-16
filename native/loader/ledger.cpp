// ledger.cpp: the patch ledger -- which module changed which bytes of the game executable's code and import table,
// and where two modules changed the same bytes.
//
// At start the loader copies every executable section of the game and its import address table.  After each step
// of its start-up (its own import hooks, each DLL of riftstone\legacy, each plugin, the engine fixes, a chained
// dinput8) whatever differs from the copy is that step's, and the copy moves on.  A late scan
// ([loader] patch_ledger_seconds after start) finds what changed since, and names each change by where it leads: the
// module a written jump or call (or an import slot) points into, through one trampoline outside any module.  Two
// modules that changed the same code bytes are a conflict: the later one's bytes are what runs, and the earlier
// one's change survives only if the later one copied it into a trampoline of its own.  An import slot two modules
// changed is a chain (the later one was handed the earlier one's function to call on).
//
// riftstone\logs\patches.txt holds it all (runtime.py, `riftstone loader patches`, docs/runtime.md "Patch ledger");
// a crash report names the module whose change it stopped in.  The ledger reads the game's memory and writes
// nothing into it.  The copy (DDDA.exe: 15.6 MB of .text) is freed after the late scan.
#include "runtime.h"
#include <stdarg.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <wchar.h>

#define MAX_REGIONS 12
#define MAX_OWNERS 160
#define MAX_TARGETS 128
#define MAX_RECORDS 4096
#define MAX_CONFLICTS 256
#define SHOWN 16              // bytes of a change kept, before and after
#define GAP 4                 // equal bytes inside one change
#define TARGET_NAME 64

struct Region { BYTE* at; DWORD size; DWORD copyAt; char name[12]; BOOL imports; };
struct Owner { char kind[8]; wchar_t module[TARGET_NAME]; wchar_t name[MAX_PATH]; };
struct Record { DWORD at, len; WORD owner, region, to; BYTE imports, shown; BYTE before[SHOWN], after[SHOWN]; };
struct Conflict { DWORD at, len; WORD first, then; BYTE imports; };

static CRITICAL_SECTION g_lock;
static BOOL g_on;
static BYTE* g_copy;          // the regions as of the last step, back to back; NULL after the late scan
static DWORD g_copySize;
static Region g_regions[MAX_REGIONS];
static int g_regionCount;
static Owner g_owners[MAX_OWNERS];
static int g_ownerCount;
static wchar_t g_targets[MAX_TARGETS][TARGET_NAME];   // 0 = leads nowhere known
static int g_targetCount = 1;
static Record g_records[MAX_RECORDS];
static volatile LONG g_recordCount;
static Conflict g_conflicts[MAX_CONFLICTS];
static int g_conflictCount;
static DWORD g_dropped;       // changes past MAX_RECORDS or MAX_CONFLICTS: counted, not kept
static int g_lateSeconds = 60;
static BOOL g_lateDone;
static DWORD g_lateChanges;
static wchar_t g_exe[64];
static BYTE* g_base;

static BOOL SafeRead(const void* at, void* out, SIZE_T n) {
    __try {
        memcpy(out, at, n);
        return TRUE;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return FALSE;
    }
}

// The file name of the module holding addr ("" when none).
static BOOL ModuleName(const void* addr, wchar_t* out, size_t cap) {
    HMODULE mod = NULL;
    wchar_t path[MAX_PATH];
    out[0] = 0;
    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                            (LPCWSTR)addr, &mod) || !mod || !GetModuleFileNameW(mod, path, MAX_PATH))
        return FALSE;
    const wchar_t* slash = wcsrchr(path, L'\\');
    wcsncpy_s(out, cap, slash ? slash + 1 : path, _TRUNCATE);
    return TRUE;
}

static WORD TargetIndex(const wchar_t* name) {
    for (int i = 1; i < g_targetCount; i++)
        if (_wcsicmp(g_targets[i], name) == 0) return (WORD)i;
    if (g_targetCount >= MAX_TARGETS) return 0;
    wcsncpy_s(g_targets[g_targetCount], TARGET_NAME, name, _TRUNCATE);
    return (WORD)g_targetCount++;
}

static WORD AddOwner(const char* kind, const wchar_t* module, const wchar_t* name) {
    for (int i = 0; i < g_ownerCount; i++)
        if (strcmp(g_owners[i].kind, kind) == 0 && _wcsicmp(g_owners[i].name, name) == 0) return (WORD)i;
    if (g_ownerCount >= MAX_OWNERS) return (WORD)(MAX_OWNERS - 1);   // the last one stands for the rest
    Owner& o = g_owners[g_ownerCount];
    strncpy_s(o.kind, sizeof o.kind, kind, _TRUNCATE);
    wcsncpy_s(o.module, TARGET_NAME, module, _TRUNCATE);
    wcsncpy_s(o.name, MAX_PATH, name, _TRUNCATE);
    return (WORD)g_ownerCount++;
}

// The module target is in; outside any module, the module the jump there leads to (hops more at most).
static WORD WhereTo(const BYTE* target, int hops) {
    wchar_t name[TARGET_NAME];
    if (!target) return 0;
    if (ModuleName(target, name, _countof(name))) return TargetIndex(name);
    BYTE b[6];
    if (hops > 0 && SafeRead(target, b, sizeof b)) {
        const BYTE* next = NULL;
        BYTE ret = 0;
        if (b[0] == 0xE9) {
            next = target + 5 + *(LONG*)(b + 1);
        } else if (b[0] == 0xFF && b[1] == 0x25) {
            if (!SafeRead(*(const BYTE**)(b + 2), &next, sizeof next)) next = NULL;
        } else if (b[0] == 0x68 && SafeRead(target + 5, &ret, 1) && ret == 0xC3) {
            next = *(const BYTE**)(b + 1);
        }
        if (next) {
            WORD t = WhereTo(next, hops - 1);
            if (t) return t;
        }
    }
    return TargetIndex(L"memory outside any module");
}

// Where a code change at..at+len leads: the target of a jump or call (E8/E9 rel32, FF 15/FF 25 [abs32],
// push imm32 + ret) whose bytes it changed; 0 when none of those covers it.
static WORD LeadsTo(const BYTE* at, DWORD len, const Region& g) {
    const BYTE* lo = g.at;
    const BYTE* hi = g.at + g.size;
    int order[16], n = 0;
    order[n++] = 0;
    for (int d = 1; d <= 5; d++) order[n++] = -d;
    for (int d = 1; d < (int)(len < 8 ? len : 8); d++) order[n++] = d;
    for (int k = 0; k < n; k++) {
        const BYTE* o = at + order[k];
        if (o < lo || o + 6 > hi) continue;
        BYTE b[7];
        if (!SafeRead(o, b, sizeof b)) continue;
        const BYTE* target = NULL;
        DWORD span = 0;
        if (b[0] == 0xE8 || b[0] == 0xE9) {
            span = 5;
            target = o + 5 + *(LONG*)(b + 1);
        } else if (b[0] == 0xFF && (b[1] == 0x15 || b[1] == 0x25)) {
            span = 6;
            if (!SafeRead(*(const BYTE**)(b + 2), &target, sizeof target)) target = NULL;
        } else if (b[0] == 0x68 && b[5] == 0xC3) {
            span = 6;
            target = *(const BYTE**)(b + 1);
        }
        if (target && o + span > at && o < at + len) return WhereTo(target, 2);
    }
    return 0;
}

// dll!function of an import slot of the game, or "?".
static void ImportName(DWORD slot, char* out, size_t cap) {
    strncpy_s(out, cap, "?", _TRUNCATE);
    IMAGE_NT_HEADERS* nt = (IMAGE_NT_HEADERS*)(g_base + ((IMAGE_DOS_HEADER*)g_base)->e_lfanew);
    IMAGE_DATA_DIRECTORY dir = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
    if (!dir.VirtualAddress) return;
    __try {
        for (IMAGE_IMPORT_DESCRIPTOR* imp = (IMAGE_IMPORT_DESCRIPTOR*)(g_base + dir.VirtualAddress); imp->Name; ++imp) {
            DWORD first = (DWORD)(DWORD_PTR)(g_base + imp->FirstThunk);
            if (slot < first) continue;
            DWORD i = (slot - first) / sizeof(IMAGE_THUNK_DATA);
            IMAGE_THUNK_DATA* thunk = (IMAGE_THUNK_DATA*)(g_base + imp->FirstThunk);
            DWORD k = 0;
            while (k < i && thunk[k].u1.Function) k++;
            if (k < i || !thunk[i].u1.Function) continue;
            const char* dll = (const char*)(g_base + imp->Name);
            if (!imp->OriginalFirstThunk) {
                _snprintf_s(out, cap, _TRUNCATE, "%s!#%lu", dll, i);
                return;
            }
            IMAGE_THUNK_DATA* names = (IMAGE_THUNK_DATA*)(g_base + imp->OriginalFirstThunk);
            if (IMAGE_SNAP_BY_ORDINAL(names[i].u1.Ordinal))
                _snprintf_s(out, cap, _TRUNCATE, "%s!#%lu", dll, (DWORD)IMAGE_ORDINAL(names[i].u1.Ordinal));
            else
                _snprintf_s(out, cap, _TRUNCATE, "%s!%s", dll,
                            (const char*)((IMAGE_IMPORT_BY_NAME*)(g_base + names[i].u1.AddressOfData))->Name);
            return;
        }
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        strncpy_s(out, cap, "?", _TRUNCATE);
    }
}

static void AddConflict(const Record& earlier, const Record& later) {
    DWORD a = earlier.at > later.at ? earlier.at : later.at;
    DWORD b = earlier.at + earlier.len < later.at + later.len ? earlier.at + earlier.len : later.at + later.len;
    if (g_conflictCount >= MAX_CONFLICTS) {
        g_dropped++;
        return;
    }
    Conflict& c = g_conflicts[g_conflictCount++];
    c.at = a;
    c.len = b - a;
    c.first = earlier.owner;
    c.then = later.owner;
    c.imports = later.imports;
    if (later.imports) {
        char name[160];
        ImportName(a, name, sizeof name);
        LogLine(L"ledger   import %S: %s hooked it after %s did (a chain: the game calls %s's first)", name,
                g_owners[later.owner].name, g_owners[earlier.owner].name, g_owners[later.owner].name);
    } else {
        LogLine(L"ledger   CONFLICT at 0x%08lx (%lu bytes): %s changed bytes %s had changed; %s's are what runs", a,
                b - a, g_owners[later.owner].name, g_owners[earlier.owner].name, g_owners[later.owner].name);
    }
}

// One change: len bytes at offset off of region r, from before to after.  owner MAX_OWNERS = the late scan's,
// named by where the change leads.
static void Note(WORD owner, int r, DWORD off, DWORD len, const BYTE* before, const BYTE* after) {
    const Region& g = g_regions[r];
    WORD to = g.imports ? WhereTo(*(const BYTE* const*)after, 2) : LeadsTo(g.at + off, len, g);
    if (owner == MAX_OWNERS) owner = AddOwner("late", to ? g_targets[to] : L"", to ? g_targets[to] : L"unknown");
    DWORD at = (DWORD)(DWORD_PTR)(g.at + off);
    LONG count = g_recordCount;
    if (count && !g.imports) {                    // a change that goes on past the last one's end is the same change
        Record& last = g_records[count - 1];
        if (last.owner == owner && last.region == r && at >= last.at && at <= last.at + last.len + GAP) {
            last.len = at + len - last.at;
            return;
        }
    }
    if (count >= MAX_RECORDS) {
        g_dropped++;
        return;
    }
    Record& rec = g_records[count];
    rec.at = at;
    rec.len = len;
    rec.owner = owner;
    rec.region = (WORD)r;
    rec.to = to;
    rec.imports = (BYTE)g.imports;
    rec.shown = (BYTE)(len < SHOWN ? len : SHOWN);
    memcpy(rec.before, before, rec.shown);
    memcpy(rec.after, after, rec.shown);
    for (LONG k = 0; k < count; k++) {
        const Record& e = g_records[k];
        if (e.imports != rec.imports || e.at >= at + len || at >= e.at + e.len) continue;
        if (_wcsicmp(g_owners[e.owner].module, g_owners[owner].module) == 0 && g_owners[owner].module[0]) continue;
        AddConflict(e, rec);
    }
    MemoryBarrier();
    g_recordCount = count + 1;
}

// What changed since the copy, noted as owner's (MAX_OWNERS: the late scan); the copy moves on.  Changes, bytes.
static void Diff(WORD owner, DWORD* changes, DWORD* bytes) {
    static BYTE live[4096];                     // under g_lock
    *changes = *bytes = 0;
    for (int r = 0; r < g_regionCount; r++) {
        Region& g = g_regions[r];
        BYTE* old = g_copy + g.copyAt;
        for (DWORD i = 0; i < g.size;) {
            DWORD chunk = g.size - i < sizeof live ? g.size - i : (DWORD)sizeof live;
            if (!SafeRead(g.at + i, live, chunk) || memcmp(live, old + i, chunk) == 0) {
                i += chunk;
                continue;
            }
            if (g.imports) {
                for (DWORD k = 0; k + 4 <= chunk; k += 4) {
                    if (memcmp(live + k, old + i + k, 4) == 0) continue;
                    Note(owner, r, i + k, 4, old + i + k, live + k);
                    memcpy(old + i + k, live + k, 4);
                    (*changes)++;
                    *bytes += 4;
                }
                i += chunk;
                continue;
            }
            for (DWORD j = 0; j < chunk;) {
                if (live[j] == old[i + j]) {
                    j++;
                    continue;
                }
                DWORD last = j;
                for (DWORD b = j + 1; b < chunk && b - last <= GAP; b++)
                    if (live[b] != old[i + b]) last = b;
                DWORD n = last - j + 1;
                Note(owner, r, i + j, n, old + i + j, live + j);
                memcpy(old + i + j, live + j, n);
                (*changes)++;
                *bytes += n;
                j = last + 1;
            }
            i += chunk;
        }
    }
}

// ---- patches.txt ------------------------------------------------------------------------------------------------

static void Put(HANDLE f, const wchar_t* fmt, ...) {
    wchar_t w[1400];
    char u[4200];
    va_list ap;
    va_start(ap, fmt);
    _vsnwprintf_s(w, _countof(w), _TRUNCATE, fmt, ap);
    va_end(ap);
    int n = WideCharToMultiByte(CP_UTF8, 0, w, -1, u, sizeof u, NULL, NULL);
    DWORD wrote;
    if (n > 1) WriteFile(f, u, (DWORD)(n - 1), &wrote, NULL);
}

static void Hex(const BYTE* b, int n, wchar_t* out, size_t cap) {
    out[0] = 0;
    for (int i = 0; i < n && (size_t)(i * 2 + 2) < cap; i++) _snwprintf_s(out + i * 2, cap - i * 2, _TRUNCATE, L"%02X", b[i]);
    if (!n) wcsncpy_s(out, cap, L"-", _TRUNCATE);
}

static void Write() {
    wchar_t path[MAX_PATH], tmp[MAX_PATH];
    _snwprintf_s(path, _countof(path), _TRUNCATE, L"%s\\patches.txt", g_logDir);
    _snwprintf_s(tmp, _countof(tmp), _TRUNCATE, L"%s\\patches.txt.tmp", g_logDir);
    HANDLE f = Real_CreateFileW(tmp, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (f == INVALID_HANDLE_VALUE) return;
    Put(f, L"; Riftstone patch ledger: what changed in %s's code and import table since the loader started, and who "
           L"changed it\r\n; riftstone loader %s; docs/runtime.md \"Patch ledger\"; fields are tab-separated\r\n",
        g_exe, RIFTSTONE_LOADER_VERSION);
    Put(f, L"game\t%s\t0x%08lx\r\n", g_exe, (DWORD)(DWORD_PTR)g_base);
    for (int r = 0; r < g_regionCount; r++)
        Put(f, L"region\t%S\t0x%08lx\t%lu\r\n", g_regions[r].name, (DWORD)(DWORD_PTR)g_regions[r].at, g_regions[r].size);
    for (int i = 0; i < g_ownerCount; i++)
        Put(f, L"owner\t%d\t%S\t%s\t%s\r\n", i, g_owners[i].kind, g_owners[i].module[0] ? g_owners[i].module : L"-",
            g_owners[i].name);
    LONG count = g_recordCount;
    DWORD imports = 0;
    for (LONG k = 0; k < count; k++) {
        const Record& rec = g_records[k];
        wchar_t before[SHOWN * 2 + 2], after[SHOWN * 2 + 2];
        Hex(rec.before, rec.shown, before, _countof(before));
        Hex(rec.after, rec.shown, after, _countof(after));
        const wchar_t* to = rec.to ? g_targets[rec.to] : L"-";
        if (rec.imports) {
            char name[160];
            ImportName(rec.at, name, sizeof name);
            Put(f, L"import\t0x%08lx\t%u\t%S\t%s\t%s\t%s\r\n", rec.at, rec.owner, name, to, before, after);
            imports++;
        } else {
            Put(f, L"range\t0x%08lx\t%lu\t%u\t%S\t%s\t%s\t%s\r\n", rec.at, rec.len, rec.owner,
                g_regions[rec.region].name, to, before, after);
        }
    }
    DWORD conflicts = 0, chains = 0;
    for (int i = 0; i < g_conflictCount; i++) {
        const Conflict& c = g_conflicts[i];
        Put(f, L"%s\t0x%08lx\t%lu\t%u\t%u\r\n", c.imports ? L"chain" : L"conflict", c.at, c.len, c.first, c.then);
        if (c.imports) chains++;
        else conflicts++;
    }
    if (g_lateDone) Put(f, L"late\t%d\t%lu\r\n", g_lateSeconds, g_lateChanges);
    Put(f, L"end\t%lu\t%lu\t%lu\t%lu\t%lu\r\n", (DWORD)count - imports, imports, conflicts, chains, g_dropped);
    CloseHandle(f);
    MoveFileExW(tmp, path, MOVEFILE_REPLACE_EXISTING);
}

// ---- the loader's calls -------------------------------------------------------------------------------------------

void LedgerBegin() {
    InitializeCriticalSection(&g_lock);
    g_on = IniInt(L"loader", L"patch_ledger", 1) != 0;
    if (!g_on) {
        LogLine(L"ledger   switched off in riftstone_loader.ini");
        return;
    }
    g_lateSeconds = IniInt(L"loader", L"patch_ledger_seconds", 60);
    if (g_lateSeconds < 2) g_lateSeconds = 2;
    if (g_lateSeconds > 900) g_lateSeconds = 900;
    g_base = (BYTE*)GetModuleHandleW(NULL);
    ModuleName(g_base, g_exe, _countof(g_exe));
    IMAGE_NT_HEADERS* nt = (IMAGE_NT_HEADERS*)(g_base + ((IMAGE_DOS_HEADER*)g_base)->e_lfanew);
    IMAGE_SECTION_HEADER* s = IMAGE_FIRST_SECTION(nt);
    DWORD total = 0;
    for (int i = 0; i < nt->FileHeader.NumberOfSections && g_regionCount < MAX_REGIONS - 1; i++) {
        if (!(s[i].Characteristics & IMAGE_SCN_MEM_EXECUTE) || !s[i].Misc.VirtualSize) continue;
        Region& g = g_regions[g_regionCount++];
        g.at = g_base + s[i].VirtualAddress;
        g.size = s[i].Misc.VirtualSize;
        memcpy(g.name, s[i].Name, 8);
        g.name[8] = 0;
        int same = 1;                             // a PE may name two sections alike: the second is ".text~2"
        for (int k = 0; k < g_regionCount - 1; k++) same += strncmp(g_regions[k].name, g.name, 8) == 0;
        if (same > 1) _snprintf_s(g.name + strlen(g.name), sizeof g.name - strlen(g.name), _TRUNCATE, "~%d", same);
        g.copyAt = total;
        total += (g.size + 15) & ~15u;
    }
    IMAGE_DATA_DIRECTORY iat = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IAT];
    if (iat.VirtualAddress && iat.Size) {
        Region& g = g_regions[g_regionCount++];
        g.at = g_base + iat.VirtualAddress;
        g.size = iat.Size & ~3u;
        strcpy_s(g.name, "imports");
        g.imports = TRUE;
        g.copyAt = total;
        total += (g.size + 15) & ~15u;
    }
    g_copy = total ? (BYTE*)VirtualAlloc(NULL, total, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE) : NULL;
    if (!g_copy) {
        LogLine(L"ledger   no room for a copy of %s's code (%lu KB); the ledger is off this session", g_exe, total >> 10);
        g_on = FALSE;
        return;
    }
    g_copySize = total;
    for (int r = 0; r < g_regionCount; r++)
        if (!SafeRead(g_regions[r].at, g_copy + g_regions[r].copyAt, g_regions[r].size))
            memset(g_copy + g_regions[r].copyAt, 0, g_regions[r].size);
    g_targets[0][0] = 0;
    AddOwner("game", g_exe, L"the game as loaded");
    LogLine(L"ledger   watching %d region(s) of %s (%lu KB): who changes its code and imports, in "
            L"riftstone\\logs\\patches.txt", g_regionCount, g_exe, total >> 10);
}

void LedgerStep(const char* kind, const wchar_t* module, const wchar_t* name) {
    if (!g_on) return;
    EnterCriticalSection(&g_lock);
    if (!g_copy) {
        LogLine(L"ledger   %s came after the late scan; what it changed is not in the ledger", name);
    } else {
        WORD owner = AddOwner(kind, module ? module : L"", name);
        DWORD changes, bytes;
        Diff(owner, &changes, &bytes);
        if (changes) LogLine(L"ledger   %s changed %lu place(s), %lu byte(s), of %s", name, changes, bytes, g_exe);
    }
    LeaveCriticalSection(&g_lock);
}

void LedgerWrite() {
    if (!g_on) return;
    EnterCriticalSection(&g_lock);
    Write();
    LeaveCriticalSection(&g_lock);
}

static DWORD WINAPI LateScan(LPVOID) {
    Sleep((DWORD)g_lateSeconds * 1000);
    EnterCriticalSection(&g_lock);
    if (g_copy) {
        DWORD bytes;
        Diff(MAX_OWNERS, &g_lateChanges, &bytes);
        g_lateDone = TRUE;
        VirtualFree(g_copy, 0, MEM_RELEASE);
        g_copy = NULL;
        int conflicts = 0;
        for (int i = 0; i < g_conflictCount; i++) conflicts += !g_conflicts[i].imports;
        LogLine(L"ledger   late scan at %d s: %lu change(s) since start-up; %ld in all, %d conflict(s); the copy is "
                L"freed (%lu KB)", g_lateSeconds, g_lateChanges, (long)g_recordCount, conflicts, g_copySize >> 10);
        Write();
    }
    LeaveCriticalSection(&g_lock);
    return 0;
}

void LedgerStartLate() {
    if (!g_on || !g_copy) return;
    HANDLE t = CreateThread(NULL, 0, LateScan, NULL, 0, NULL);
    if (t) CloseHandle(t);
}

// The owner of the last change to the game's code holding addr (a crash report's line).  No lock: the records only
// grow, and a crash must not wait.
BOOL LedgerOwnerAt(const void* addr, wchar_t* out, size_t cap) {
    if (!g_on) return FALSE;
    DWORD a = (DWORD)(DWORD_PTR)addr;
    for (LONG k = g_recordCount - 1; k >= 0; k--) {
        const Record& rec = g_records[k];
        if (rec.imports || a < rec.at || a >= rec.at + rec.len) continue;
        _snwprintf_s(out, cap, _TRUNCATE, L"%s (0x%08lx, %lu bytes)", g_owners[rec.owner].name, rec.at, rec.len);
        return TRUE;
    }
    return FALSE;
}

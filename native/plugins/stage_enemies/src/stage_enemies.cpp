// stage_enemies -- Riftstone native plugin: load an enemy's archive when a stage loads, so a mod can
// put an enemy into a stage the game never uses it in (an Archydra in the Tower, say).
//
// What the game does
//   Dark Arisen loads archives by ARCHIVE_TAG: a fixed table (10 lists of {u32 tag; const char* path}
//   at 0x01823544) and, at run time, a request manager ([0x018D9280]) with request slots.  When a stage
//   loads, the stage loader gets a slot (0x00418550 -> [ebp+0xA28]) and queues the stage archive's tag
//   (5 + stage index) into it with 0x00418810 (0x004FFDA1..0x004FFDB7).  It queues nothing else for an
//   ordinary stage -- only the fixed special cases the exe hard-codes (em5800 the Dragon on 501/502,
//   em5801 the Ur-Dragon on 605).  A group whose enemy the stage never queues has no model to spawn, so
//   the enemy simply never appears (no crash).  All 97 vanilla enemies have a tag, so any of them CAN
//   load in any stage; the stage just has to ask.
//
// What this plugin does
//   One jmp in the stage loader at 0x004FFD8E (right after the slot index is put in edx), to a thunk that,
//   for the stage being loaded ([ebp+0x724]), queues each enemy archive named for that stage in
//   stage_enemies.ini -- using the game's own queue call, byte for byte the same as the stage-archive one
//   next to it (eax = the slot record, ecx = the manager, the tag on the stack, 0x00418810, ret 4).  The
//   thunk then replays the instruction the jmp covers (cmp edx, 0x100) and resumes, so the stage archive
//   is still queued exactly as before and nothing runs except at a stage load.  An enemy is emNNNN
//   (resolved to its tag through the exe's own archive table) or a raw tag number.
//
// Safety
//   DDDA.exe build 2364871 only.  The patched bytes and the code around them (the slot fetch, the stage
//   archive queue, the queue function) are compared first; on any difference nothing is patched and
//   riftstone\logs\stage_enemies.log says why.  Queuing an archive only loads its resources; whether a
//   foreign enemy then behaves in a foreign stage is UNKNOWN until played.  Original code; no third-party
//   source.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

namespace {

// ---- build 2364871 --------------------------------------------------------------------------
constexpr uintptr_t IMAGE_BASE = 0x00400000;

constexpr uintptr_t SITE = 0x004FFD8E;   // cmp edx, 0x100  (edx = the request slot index)
constexpr uintptr_t BACK = 0x004FFD94;   // jae 0x004FFDBC  (the slot-valid check the site feeds)
const uint8_t SITE_BYTES[6] = {0x81, 0xFA, 0x00, 0x01, 0x00, 0x00};

constexpr uintptr_t ADD_TAG = 0x00418810;       // add a tag to a request slot (eax slot record, ecx manager, tag on stack)
constexpr uintptr_t REQ_MANAGER_PTR = 0x018D9280;  // [this] = the request manager
constexpr uintptr_t ARC_TABLE = 0x01823544;     // 10 x {const Rec* records; u32 count}

// The frame at the site: [ebp+0x724] = stage number, [ebp+0xA28] = the request slot index.
constexpr uint32_t FRAME_STAGE = 0x724, FRAME_SLOT = 0xA28;

struct Expect {
    const char* what;
    uintptr_t at;
    uint8_t bytes[12];
    uint8_t len;
};
const Expect CONTEXT[] = {
    {"stage loader loads the slot index into edx", 0x004FFD88, {0x8B, 0x95, 0x28, 0x0A, 0x00, 0x00}, 6},
    {"stage loader's slot-valid branch (the site feeds it)", 0x004FFD94, {0x73, 0x26}, 2},
    {"stage loader reads the stage number from ebp+0x724", 0x004FFD96, {0x8B, 0x8D, 0x24, 0x07, 0x00, 0x00}, 6},
    {"stage loader loads the request manager", 0x004FFDA1, {0x8B, 0x0D, 0x80, 0x92, 0x8D, 0x01}, 6},
    {"stage loader computes the stage archive tag (5 + index)", 0x004FFDA7, {0x83, 0xC0, 0x05}, 3},
    {"stage loader queues the stage archive with 0x00418810", 0x004FFDB7, {0xE8, 0x54, 0x8A, 0xF1, 0xFF}, 5},
    {"0x00418810 saves ebx/ebp/esi/edi (the queue function)", 0x00418810, {0x53, 0x55, 0x56, 0x57}, 4},
};

struct ArcRec { uint32_t tag; const char* path; };
struct ArcList { const ArcRec* records; uint32_t count; };

// ---- settings ---------------------------------------------------------------------------------
struct Entry { uint32_t stage; uint32_t tag; };
constexpr int MAX_ENTRIES = 64;
Entry g_entries[MAX_ENTRIES];
int g_count = 0;
bool g_enabled = true;

wchar_t g_logPath[MAX_PATH];
volatile LONG g_loads = 0, g_queued = 0;

// One write per line: the stage loader can run on more than one thread over a session.
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

}  // namespace

// ---- resolving emNNNN to its archive tag through the exe's own table --------------------------
// A raw number is taken as the tag; otherwise "rom\enemy\<name>" is looked up.  Reads only the fixed
// table (0x01823544), which is present from start-up, never the run-time request array.
extern "C" __declspec(dllexport) uint32_t StageEnemies_ResolveTag(const char* emOrTag) {
    if (!emOrTag || !emOrTag[0]) return 0;
    if (emOrTag[0] >= '0' && emOrTag[0] <= '9') {
        long v = strtol(emOrTag, nullptr, 10);
        return (v > 0 && v < 0x8000) ? (uint32_t)v : 0;
    }
    char path[80];
    _snprintf_s(path, sizeof path, _TRUNCATE, "rom\\enemy\\%s", emOrTag);
    if (!Readable(ARC_TABLE, 10 * sizeof(ArcList))) return 0;
    const ArcList* lists = (const ArcList*)ARC_TABLE;
    for (int l = 0; l < 10; l++) {
        const ArcRec* recs = lists[l].records;
        uint32_t count = lists[l].count;
        if (!recs || count > 100000 || !Readable((uintptr_t)recs, (size_t)count * sizeof(ArcRec))) continue;
        for (uint32_t k = 0; k < count; k++) {
            const char* p = recs[k].path;
            if (p && Readable((uintptr_t)p, 1) && _stricmp(p, path) == 0) return recs[k].tag;
        }
    }
    return 0;
}

// ---- the real queue: the game's own call, with our tag ----------------------------------------
// __cdecl(slotIndex, tag).  Mirrors 0x004FFDA1..0x004FFDB7: ecx = the manager, the tag pushed, eax = the
// slot record (manager + (index*5 + 0x1F4) << 5), then 0x00418810 (ret 4).  Never touches ebx/esi/edi.
extern "C" __declspec(naked) void __cdecl StageEnemies_QueueReal(uint32_t /*slotIndex*/, uint32_t /*tag*/) {
    __asm {
        push ebp
        mov  ebp, esp
        mov  edx, dword ptr [ebp + 8]          // slotIndex
        cmp  edx, 0x100
        jae  done
        mov  ecx, dword ptr [0x018D9280]       // the request manager (REQ_MANAGER_PTR)
        test ecx, ecx
        je   done                              // not built yet: nothing to queue into
        mov  eax, dword ptr [ebp + 12]         // tag
        push eax                               // the tag argument (0x00418810 is ret 4)
        lea  eax, [edx + edx*4 + 0x1F4]
        shl  eax, 5
        add  eax, ecx                          // eax = the slot record
        mov  edx, 0x00418810                    // ADD_TAG
        call edx                               // eax slot record, ecx manager, [esp] tag; ret 4
      done:
        mov  esp, ebp
        pop  ebp
        ret
    }
}

namespace {
typedef void(__cdecl* QueueFn)(uint32_t slotIndex, uint32_t tag);
QueueFn g_queue = StageEnemies_QueueReal;
}  // namespace

// For the harness: swap the queue for a recorder so the dispatch can be checked without the game's
// request manager.  Passing null restores the real queue.
extern "C" __declspec(dllexport) void StageEnemies_SetQueueForTest(QueueFn fn) {
    g_queue = fn ? fn : StageEnemies_QueueReal;
}

// ---- the dispatch, called once per stage load from the thunk ----------------------------------
extern "C" __declspec(dllexport) void __cdecl StageEnemies_OnLoad(uint32_t stage, uint32_t slotIndex) {
    InterlockedIncrement(&g_loads);
    for (int i = 0; i < g_count; i++) {
        if (g_entries[i].stage == stage) {
            g_queue(slotIndex, g_entries[i].tag);
            if (InterlockedIncrement(&g_queued) <= 64)
                Log("  stage %u loading: queued enemy archive tag %u (slot %u)", stage, g_entries[i].tag, slotIndex);
        }
    }
}

// ---- thunk ------------------------------------------------------------------------------------
static uintptr_t g_back;

__declspec(naked) static void ThunkStageEnemies() {
    __asm {
        // entry (0x004FFD8E): edx = slot index, ebp = the stage loader's frame, all else the game's
        pushad
        mov  eax, dword ptr [ebp + 0x724]         // stage number (FRAME_STAGE)
        mov  edx, dword ptr [ebp + 0xA28]         // request slot index (FRAME_SLOT)
        push edx
        push eax
        call StageEnemies_OnLoad                  // __cdecl(stage, slotIndex)
        add  esp, 8
        popad
        cmp  edx, 0x100                            // the instruction the jmp covers (edx restored by popad)
        jmp  dword ptr [g_back]                    // 0x004FFD94
    }
}

namespace {

bool Verify() {
    if (!Readable(SITE, sizeof SITE_BYTES) || memcmp((const void*)SITE, SITE_BYTES, sizeof SITE_BYTES) != 0)
        return Log("refused: the stage loader at 0x%08X is not the expected code (another build, or already patched)",
                   (unsigned)SITE),
               false;
    for (const Expect& e : CONTEXT) {
        if (!Readable(e.at, e.len) || memcmp((const void*)e.at, e.bytes, e.len) != 0)
            return Log("refused: %s at 0x%08X is not the expected code", e.what, (unsigned)e.at), false;
    }
    return true;
}

bool Patch() {
    g_back = BACK;
    DWORD old;
    if (!VirtualProtect((void*)SITE, sizeof SITE_BYTES, PAGE_EXECUTE_READWRITE, &old))
        return Log("failed: VirtualProtect at 0x%08X (%lu)", (unsigned)SITE, GetLastError()), false;
    uint8_t* p = (uint8_t*)SITE;
    int32_t rel = (int32_t)((uintptr_t)ThunkStageEnemies - (SITE + 5));
    p[0] = 0xE9;
    memcpy(p + 1, &rel, 4);
    for (size_t k = 5; k < sizeof SITE_BYTES; k++) p[k] = 0x90;
    VirtualProtect((void*)SITE, sizeof SITE_BYTES, old, &old);
    FlushInstructionCache(GetCurrentProcess(), (void*)SITE, sizeof SITE_BYTES);
    return true;
}

// One "stage = enemy[, enemy...]" line: the stage number, then each enemy resolved to its tag.
void ParseLine(const wchar_t* key, const wchar_t* value) {
    long stage = wcstol(key, nullptr, 10);
    if (stage <= 0 || stage > 0xFFFF) return;
    char val[256];
    WideCharToMultiByte(CP_ACP, 0, value, -1, val, sizeof val, nullptr, nullptr);
    char* ctx = nullptr;
    for (char* tok = strtok_s(val, ", \t", &ctx); tok; tok = strtok_s(nullptr, ", \t", &ctx)) {
        if (!*tok || g_count >= MAX_ENTRIES) continue;
        uint32_t tag = StageEnemies_ResolveTag(tok);
        if (!tag) {
            Log("  stage %ld: '%s' is not a known enemy (emNNNN) or tag number; skipped", stage, tok);
            continue;
        }
        g_entries[g_count].stage = (uint32_t)stage;
        g_entries[g_count].tag = tag;
        g_count++;
        Log("  stage %ld will also load '%s' (archive tag %u)", stage, tok, tag);
    }
}

void LoadSettings(HMODULE self) {
    wchar_t ini[MAX_PATH];
    GetModuleFileNameW(self, ini, MAX_PATH);
    wchar_t* dot = wcsrchr(ini, L'.');
    wchar_t* slash = wcsrchr(ini, L'\\');
    if (dot && (!slash || dot > slash)) *dot = 0;
    wcscat_s(ini, L".ini");
    bool haveIni = GetFileAttributesW(ini) != INVALID_FILE_ATTRIBUTES;
    g_enabled = GetPrivateProfileIntW(L"stage_enemies", L"Enabled", 1, ini) != 0;

    // Every "stage = enemies" key in [stage_enemies] except Enabled.
    wchar_t section[4096];
    DWORD got = GetPrivateProfileSectionW(L"stage_enemies", section, _countof(section), ini);
    if (got > 0) {
        for (const wchar_t* p = section; *p; p += wcslen(p) + 1) {
            const wchar_t* eq = wcschr(p, L'=');
            if (!eq) continue;
            wchar_t key[64];
            size_t klen = (size_t)(eq - p);
            if (klen == 0 || klen >= _countof(key)) continue;
            wcsncpy_s(key, p, klen);
            // trim
            wchar_t* k = key;
            while (*k == L' ' || *k == L'\t') k++;
            size_t e = wcslen(k);
            while (e && (k[e - 1] == L' ' || k[e - 1] == L'\t')) k[--e] = 0;
            if (_wcsicmp(k, L"Enabled") == 0) continue;
            ParseLine(k, eq + 1);
        }
    }
    Log("settings: %s; %d enemy load(s) across the stages named%s", haveIni ? "stage_enemies.ini" : "no ini",
        g_count, g_enabled ? "" : " (Enabled=0, nothing patched)");
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
    _snwprintf_s(g_logPath, MAX_PATH, _TRUNCATE, L"%s\\riftstone\\logs\\stage_enemies.log", root);
    wchar_t probe[8];
    if (GetEnvironmentVariableW(L"RIFTSTONE_STAGE_ENEMIES_LOG", probe, 8) == 0) {
        DeleteFileW(g_logPath);
        SetEnvironmentVariableW(L"RIFTSTONE_STAGE_ENEMIES_LOG", L"1");
    }

    bool harness = GetEnvironmentVariableW(L"RIFTSTONE_STAGE_ENEMIES_HARNESS", probe, 8) > 0;
    if (!harness && (uintptr_t)GetModuleHandleW(nullptr) != IMAGE_BASE) {
        Log("refused: the host is not DDDA.exe at 0x00400000");
        return;
    }
    LoadSettings(self);
    if (!g_enabled) return;
    if (g_count == 0) {
        Log("stage_enemies: no stages listed, nothing patched");
        return;
    }
    if (!Verify() || !Patch()) return;
    Log("stage_enemies: stage loader patched at 0x%08X (%s); %d enemy load(s) armed", (unsigned)SITE,
        harness ? "harness" : "game", g_count);
}

}  // namespace

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(module);
        Start(module);
    }
    return TRUE;
}

// compat -- Riftstone native plugin: Dragon's Dogma Online skills run as Dark Arisen actions.
//
// What it does
//   Dark Arisen starts an equipped skill by asking its action manager for the skill's action number
//   (uPlayerBase::checkAction -> setAction / setActionEx).  This plugin wraps uPlayer::checkAction (one
//   vtable slot).  When the game has just asked for a skill that compat.ini maps to a program, the plugin
//   asks for its own action instead, through the engine's own path for actions made from a class
//   descriptor (setActionExDTI): the action manager then runs it like any other -- init, move each
//   frame, final, the damage and death actions still take over as they always do.
//
//   The action is a cPlAction (the game's constructor, init, move, final and destructor run first) with a
//   small extension that executes a skill program from compat.skills: which motion list to bind to the
//   skill bank, which effect provider to bind, which motions to play, which motion-event bits to count
//   (the Online engine's sequence counters: DDO's timing bits are moved to page-1 bits 21-28 when the
//   motions are converted), and which shells to fire from which shell list.  Programs and their files are
//   produced by `riftstone compat pack`; the plugin checks every file a program needs before it lets
//   the game load any of them, because a missing loose file stops Dark Arisen with a fatal error.
//
// Safety
//   DDDA.exe build 2364871 only.  Every function called, both vtables used and the patched slot are
//   compared first; on any difference nothing is patched and riftstone\logs\compat.log says why.  The
//   descriptor for the action class is built only after the game's own cPlAction descriptor exists (the
//   first time the Arisen checks for an action).  Programs that name a file that is not there never run.
//   Original code; no third-party source.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <math.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "engine.h"

namespace {

constexpr uintptr_t IMAGE_BASE = 0x00400000;
constexpr uint32_t EXT_MAGIC = 0x41435352;  // "RSCA"
constexpr int MAX_PROGRAMS = 48, MAX_CODE = 48, MAX_REQUIRE = 512, MAX_SPAWNS = 64, MAX_LEVEL = 10;

wchar_t g_logPath[MAX_PATH], g_root[MAX_PATH], g_selfDir[MAX_PATH], g_ini[MAX_PATH];
bool g_harness = false;
CRITICAL_SECTION g_logLock;

void Log(const char* fmt, ...) {
    char line[768];
    va_list ap;
    va_start(ap, fmt);
    int n = _vsnprintf_s(line, sizeof line, _TRUNCATE, fmt, ap);
    va_end(ap);
    if (n < 0) n = (int)strlen(line);
    EnterCriticalSection(&g_logLock);
    HANDLE h = CreateFileW(g_logPath, FILE_APPEND_DATA, FILE_SHARE_READ, nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h != INVALID_HANDLE_VALUE) {
        DWORD w;
        WriteFile(h, line, (DWORD)n, &w, nullptr);
        WriteFile(h, "\r\n", 2, &w, nullptr);
        CloseHandle(h);
    }
    LeaveCriticalSection(&g_logLock);
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

template <class T> T& At(void* base, uint32_t off) { return *(T*)((uint8_t*)base + off); }

// ---- engine calls ---------------------------------------------------------------------------------
using FnCalcSeq = uint32_t(__thiscall*)(void* layer, uint32_t page, int32_t f0, int32_t f1);
using FnIsEnd = bool(__thiscall*)(void* layer);
using FnSetMotionList = void(__thiscall*)(void* model, void* list, uint32_t bank);
using FnResCreate = void*(__thiscall*)(void* mgr, const void* dti, const char* path, uint32_t flags);
using FnThis = void(__thiscall*)(void* self);
using FnThisU = void(__thiscall*)(void* self, uint32_t v);
using FnDtor = void*(__thiscall*)(void* self, uint32_t flags);
using FnAlloc = void*(__thiscall*)(void* alloc, uint32_t size, uint32_t align, uint32_t tag);
using FnGetAllocator = void*(__cdecl*)(const void* dti);
using FnSetShotCoord = void(__fastcall*)(const float* dir, const float* pos, void* shell);
using FnPlayBase = void(__thiscall*)(void* player, uint32_t motNo, uint32_t forcible, uint32_t attr, float hokan,
                                     float frame, float speed);
using FnSetActionExDti = void(__thiscall*)(void* player, const void* dti, uint32_t u0, uint32_t u1, uint32_t u2,
                                           uint32_t u3, int32_t s0, int32_t s1, int32_t s2, int32_t s3, float f0,
                                           float f1, float f2, float f3, float f4, float f5, float f6, float f7);

// Targets of the register-convention thunks, held in memory (the harness may point them at fakes).
uintptr_t g_fnCreateShl = eng::F_CREATE_SHL, g_fnSetEpvResource = eng::F_SET_EPV_RESOURCE,
          g_fnPlActCtor = eng::F_PLACTION_CTOR, g_fnSkillLevel = eng::F_SKILL_LEVEL;

}  // namespace

// sShlManager::createShlSub: EAX = group; stack (mgr, list, index, owner, parent, line); ret 0x18.
extern "C" __declspec(naked) void* __cdecl CompatCreateShl(uint32_t, void*, void*, uint32_t, void*, void*, uint32_t) {
    __asm {
        push ebp
        mov ebp, esp
        push ebx
        push esi
        push edi
        push dword ptr [ebp + 32]
        push dword ptr [ebp + 28]
        push dword ptr [ebp + 24]
        push dword ptr [ebp + 20]
        push dword ptr [ebp + 16]
        push dword ptr [ebp + 12]
        mov eax, dword ptr [ebp + 8]
        call dword ptr [g_fnCreateShl]
        pop edi
        pop esi
        pop ebx
        pop ebp
        ret
    }
}

// uObjModel::setEpvResource: EAX = path; stack (model, slot); ret 8; AL = loaded.
extern "C" __declspec(naked) uint32_t __cdecl CompatSetEpvResource(const char*, void*, uint32_t) {
    __asm {
        push ebp
        mov ebp, esp
        push ebx
        push esi
        push edi
        push dword ptr [ebp + 16]
        push dword ptr [ebp + 12]
        mov eax, dword ptr [ebp + 8]
        call dword ptr [g_fnSetEpvResource]
        movzx eax, al
        pop edi
        pop esi
        pop ebx
        pop ebp
        ret
    }
}

// cPlAction's constructor: EAX = memory, returns it.
extern "C" __declspec(naked) void* __cdecl CompatPlActionCtor(void*) {
    __asm {
        mov eax, dword ptr [esp + 4]
        push ebx
        push esi
        push edi
        call dword ptr [g_fnPlActCtor]
        pop edi
        pop esi
        pop ebx
        ret
    }
}

// cPlayerInfo::getSkillLevelType: ESI = skill id, EDI = cPlayerInfo + 0x72C; stack bool; ret 4.
extern "C" __declspec(naked) int32_t __cdecl CompatSkillLevel(int32_t, void*) {
    __asm {
        push ebp
        mov ebp, esp
        push ebx
        push esi
        push edi
        mov esi, dword ptr [ebp + 8]
        mov edi, dword ptr [ebp + 12]
        push 0
        call dword ptr [g_fnSkillLevel]
        pop edi
        pop esi
        pop ebx
        pop ebp
        ret
    }
}

namespace {

// ---- programs (compat.skills) ----------------------------------------------------------------------
enum Op : int32_t { OP_PLAY, OP_ARM, OP_WAIT_COUNTER, OP_WAIT_END, OP_WAIT_FRAMES, OP_WAVE, OP_SHOT, OP_END };
const char* const OP_NAMES[] = {"play", "arm", "wait counter", "wait end", "wait frames", "wave", "shot", "end"};

struct Instr {
    int32_t op;
    int32_t a[8];
    float f[4];
};

struct Program {
    char name[32];
    char motions[64];      // rMotionList path (no extension)
    uint32_t bank;         // motion bank the list is bound to (7: the main-skill bank; cPlAction::final restores it)
    char epv[64];          // rEffectProvider bound to the player for motion-synced effects and shells
    uint32_t epvSlot;
    char shells[64];       // rShlParamList
    char collision[64];    // rObjCollision for shells, "%02d" = level
    Instr code[MAX_CODE];
    int n;
    int firstRequire, requireCount;
    bool ready, failed;
    int runs;              // times it began this session (the first ones log every step)
    void* motionRes;
    void* shellRes;
    void* colRes[MAX_LEVEL + 1];
};

Program g_programs[MAX_PROGRAMS];
int g_programCount = 0;
char g_require[MAX_REQUIRE][72];
int g_requireCount = 0;

// compat.ini
bool g_enabled = true;
int g_slotProgram[2][3] = {{-1, -1, -1}, {-1, -1, -1}};  // [main|sub][slot]
int g_levelMap[4] = {5, 5, 8, 10};                       // Dark Arisen level type (0..3) -> Online level
int g_testKey = 0, g_testProgram = -1, g_testLevel = 10;

int FindProgram(const char* name) {
    for (int i = 0; i < g_programCount; i++)
        if (_stricmp(g_programs[i].name, name) == 0) return i;
    return -1;
}

char* Trim(char* s) {
    while (*s == ' ' || *s == '\t') s++;
    char* e = s + strlen(s);
    while (e > s && (e[-1] == ' ' || e[-1] == '\t' || e[-1] == '\r' || e[-1] == '\n')) *--e = 0;
    return s;
}

// Splits a line into words; '#' starts a comment.
int Words(char* s, char** w, int max) {
    int n = 0;
    char* hash = strchr(s, '#');
    if (hash) *hash = 0;
    for (char* p = strtok(s, " \t\r\n"); p && n < max; p = strtok(nullptr, " \t\r\n")) w[n++] = p;
    return n;
}

bool Int(const char* s, int32_t* out) {
    char* end;
    long v = strtol(s, &end, 0);
    if (end == s || *end) return false;
    *out = (int32_t)v;
    return true;
}

bool Flt(const char* s, float* out) {
    char* end;
    double v = strtod(s, &end);
    if (end == s || *end) return false;
    *out = (float)v;
    return true;
}

void CopyPath(char* dst, size_t cap, const char* src, int lineNo, bool* ok) {
    if (strlen(src) >= cap) {
        Log("compat.skills line %d: path too long (%s)", lineNo, src);
        *ok = false;
    }
    strncpy_s(dst, cap, src, _TRUNCATE);
}

// Grammar (one statement per line):
//   skill <name> ... end
//   motions <path>  bank <7|9>  epv <slot> <path>  shells <path>  collision <path with %02d>  require <file>
//   play <motNo> <blend> <frame>         arm <slot> <layer> <page> <bit>
//   wait counter <slot> <timeout>        wait end <timeout>          wait frames <n>
//   wave <group> <first index> <count> <count at level 6+> <step cm> <interval frames>
//   shot <group> <index> <index at level 6+> <forward cm>            end
bool LoadPrograms(const wchar_t* path) {
    FILE* f = nullptr;
    if (_wfopen_s(&f, path, L"rb") != 0 || !f) return Log("no program file %ls", path), false;
    char line[512];
    int lineNo = 0;
    Program* cur = nullptr;
    bool ok = true;
    while (fgets(line, sizeof line, f)) {
        lineNo++;
        char* w[12];
        int n = Words(line, w, 12);
        if (!n) continue;
        auto bad = [&](const char* why) {
            Log("compat.skills line %d: %s", lineNo, why);
            ok = false;
        };
        if (!cur) {
            if (_stricmp(w[0], "skill") != 0 || n != 2) { bad("expected 'skill <name>'"); continue; }
            if (g_programCount >= MAX_PROGRAMS) { bad("too many skills"); break; }
            if (FindProgram(w[1]) >= 0) Log("%ls: skill %s is already defined; the first one counts", path, w[1]);
            cur = &g_programs[g_programCount];
            memset(cur, 0, sizeof *cur);
            strncpy_s(cur->name, w[1], _TRUNCATE);
            cur->bank = 7;
            cur->epvSlot = 9;
            cur->firstRequire = g_requireCount;
            continue;
        }
        int32_t v[8] = {};
        float fv[4] = {};
        if (_stricmp(w[0], "end") == 0 && n == 1) {
            if (cur->n < MAX_CODE) cur->code[cur->n++] = Instr{OP_END, {}, {}};
            cur->requireCount = g_requireCount - cur->firstRequire;
            if (FindProgram(cur->name) < 0) g_programCount++;  // a second definition of a name is dropped
            else g_requireCount = cur->firstRequire;
            cur = nullptr;
        } else if (_stricmp(w[0], "motions") == 0 && n == 2) {
            CopyPath(cur->motions, sizeof cur->motions, w[1], lineNo, &ok);
        } else if (_stricmp(w[0], "bank") == 0 && n == 2 && Int(w[1], v) && (v[0] == 7 || v[0] == 9)) {
            cur->bank = (uint32_t)v[0];  // the two skill banks cPlAction::final gives back to the weapon
        } else if (_stricmp(w[0], "epv") == 0 && n == 3 && Int(w[1], v) && v[0] >= 0 && v[0] < 16) {
            cur->epvSlot = (uint32_t)v[0];
            CopyPath(cur->epv, sizeof cur->epv, w[2], lineNo, &ok);
        } else if (_stricmp(w[0], "shells") == 0 && n == 2) {
            CopyPath(cur->shells, sizeof cur->shells, w[1], lineNo, &ok);
        } else if (_stricmp(w[0], "collision") == 0 && n == 2) {
            CopyPath(cur->collision, sizeof cur->collision, w[1], lineNo, &ok);
        } else if (_stricmp(w[0], "require") == 0 && n == 2) {
            if (g_requireCount >= MAX_REQUIRE) { bad("too many required files"); continue; }
            CopyPath(g_require[g_requireCount], sizeof g_require[0], w[1], lineNo, &ok);
            g_requireCount++;
        } else {
            Instr in = {};
            bool parsed = false;
            if (_stricmp(w[0], "play") == 0 && n == 4 && Int(w[1], v) && Flt(w[2], fv) && Flt(w[3], fv + 1)) {
                in = Instr{OP_PLAY, {v[0]}, {fv[0], fv[1]}};
                parsed = v[0] >= 0 && v[0] <= 0xFFFF;
            } else if (_stricmp(w[0], "arm") == 0 && n == 5 && Int(w[1], v) && Int(w[2], v + 1) && Int(w[3], v + 2) &&
                       Int(w[4], v + 3)) {
                in = Instr{OP_ARM, {v[0], v[1], v[2], v[3]}, {}};
                parsed = v[0] >= 0 && v[0] < 10 && v[1] >= 0 && v[1] < 8 && v[2] >= 0 && v[2] < 4 && v[3] >= 0 && v[3] < 32;
            } else if (_stricmp(w[0], "wait") == 0 && n >= 3) {
                if (_stricmp(w[1], "counter") == 0 && n == 4 && Int(w[2], v) && Flt(w[3], fv)) {
                    in = Instr{OP_WAIT_COUNTER, {v[0]}, {fv[0]}};
                    parsed = v[0] >= 0 && v[0] < 10;
                } else if (_stricmp(w[1], "end") == 0 && n == 3 && Flt(w[2], fv)) {
                    in = Instr{OP_WAIT_END, {}, {fv[0]}};
                    parsed = true;
                } else if (_stricmp(w[1], "frames") == 0 && n == 3 && Flt(w[2], fv)) {
                    in = Instr{OP_WAIT_FRAMES, {}, {fv[0]}};
                    parsed = true;
                }
            } else if (_stricmp(w[0], "wave") == 0 && n == 7 && Int(w[1], v) && Int(w[2], v + 1) && Int(w[3], v + 2) &&
                       Int(w[4], v + 3) && Flt(w[5], fv) && Flt(w[6], fv + 1)) {
                in = Instr{OP_WAVE, {v[0], v[1], v[2], v[3]}, {fv[0], fv[1]}};
                parsed = v[2] > 0 && v[2] <= 16 && v[3] > 0 && v[3] <= 16;
            } else if (_stricmp(w[0], "shot") == 0 && n == 5 && Int(w[1], v) && Int(w[2], v + 1) && Int(w[3], v + 2) &&
                       Flt(w[4], fv)) {
                in = Instr{OP_SHOT, {v[0], v[1], v[2]}, {fv[0]}};
                parsed = true;
            }
            if (!parsed) { bad("not understood"); continue; }
            if (cur->n >= MAX_CODE - 1) { bad("program too long"); continue; }
            cur->code[cur->n++] = in;
        }
    }
    fclose(f);
    if (cur) Log("compat.skills: skill %s has no 'end'", cur->name), ok = false;
    return ok;
}

// ---- files: a resource path the game would open as a loose file ------------------------------------
bool GameFileExists(const char* rel, const char* ext) {
    wchar_t p[MAX_PATH];
    const wchar_t* roots[2] = {L"riftstone\\overlay", L"nativePC"};
    for (auto* r : roots) {
        if (_snwprintf_s(p, MAX_PATH, _TRUNCATE, L"%s\\%s\\%S.%S", g_root, r, rel, ext) < 0) continue;
        DWORD a = GetFileAttributesW(p);
        if (a != INVALID_FILE_ATTRIBUTES && !(a & FILE_ATTRIBUTE_DIRECTORY)) return true;
    }
    return false;
}

bool SplitExt(const char* file, char* base, size_t cap, const char** ext) {
    const char* dot = strrchr(file, '.');
    if (!dot || dot == file) return false;
    size_t n = (size_t)(dot - file);
    if (n >= cap) return false;
    memcpy(base, file, n);
    base[n] = 0;
    *ext = dot + 1;
    return true;
}

// Every file a program names must be present before the game is asked to load any of them.
bool CheckProgramFiles(Program& p) {
    char base[80];
    const char* ext;
    int missing = 0;
    for (int i = p.firstRequire; i < p.firstRequire + p.requireCount; i++) {
        if (!SplitExt(g_require[i], base, sizeof base, &ext) || !GameFileExists(base, ext)) {
            if (missing++ < 8) Log("  %s: missing %s", p.name, g_require[i]);
        }
    }
    struct { const char* path; const char* ext; } own[3] = {{p.motions, "lmt"}, {p.epv, "epv"}, {p.shells, "shl"}};
    for (auto& o : own) {
        if (o.path[0] && !GameFileExists(o.path, o.ext)) {
            if (missing++ < 8) Log("  %s: missing %s.%s", p.name, o.path, o.ext);
        }
    }
    if (p.collision[0]) {
        for (int lv = 1; lv <= MAX_LEVEL; lv++) {
            char c[80];
            _snprintf_s(c, sizeof c, _TRUNCATE, p.collision, lv);
            if (!GameFileExists(c, "ocl") && missing++ < 8) Log("  %s: missing %s.ocl", p.name, c);
        }
    }
    if (missing) Log("skill %s is off: %d file(s) missing", p.name, missing);
    return missing == 0;
}

// ---- resources ---------------------------------------------------------------------------------------
void* LoadResource(uintptr_t dti, const char* path, const char* ext) {
    if (!path[0]) return nullptr;
    if (!GameFileExists(path, ext)) return Log("not loading %s.%s: no such file", path, ext), nullptr;
    void* mgr = *(void**)eng::RESOURCE_MANAGER;
    if (!mgr) return nullptr;
    FnResCreate create = (*(FnResCreate**)mgr)[0x30 / 4];
    void* r = create(mgr, (const void*)dti, path, 1);
    Log("loaded %s.%s -> %p", path, ext, r);
    return r;
}

// Loads what a program keeps for the whole session (one reference each, never released).
bool PrepareProgram(Program& p) {
    if (p.ready) return true;
    if (p.failed) return false;
    if (!CheckProgramFiles(p)) return p.failed = true, false;
    p.motionRes = LoadResource(eng::DTI_MOTION_LIST, p.motions, "lmt");
    if (p.shells[0]) p.shellRes = LoadResource(eng::DTI_SHL_PARAM_LIST, p.shells, "shl");
    if (!p.motionRes || (p.shells[0] && !p.shellRes)) {
        Log("skill %s is off: its motion list or shell list did not load", p.name);
        return p.failed = true, false;
    }
    return p.ready = true;
}

void* CollisionFor(Program& p, int level) {
    if (!p.collision[0]) return nullptr;
    level = level < 1 ? 1 : level > MAX_LEVEL ? MAX_LEVEL : level;
    if (!p.colRes[level]) {
        char c[80];
        _snprintf_s(c, sizeof c, _TRUNCATE, p.collision, level);
        p.colRes[level] = LoadResource(eng::DTI_OBJ_COLLISION, c, "ocl");
    }
    return p.colRes[level];
}

// ---- the action class ------------------------------------------------------------------------------
struct MtDTI {
    const void* const* vtable;
    const char* name;
    MtDTI* next;
    MtDTI* child;
    MtDTI* parent;
    void* link;
    uint32_t bits;
    uint32_t id;
};

struct Seq {
    int32_t active, layer, page, bit, last, count;
};

struct Ext {
    uint32_t magic;
    int32_t prog, pc, level, side, slot, skill;
    int32_t waitPc, waitBase, verbose;
    float waitTime, total;
    uint32_t motion;
    Seq seq[10];
};

constexpr uint32_t ACT_SIZE = (eng::PLACTION_SIZE + sizeof(Ext) + 15) & ~15u;
static_assert(ACT_SIZE < 0x2E0, "the action must stay smaller than the game's largest player action");

MtDTI g_dti;
const void* g_dtiVt[eng::DTI_VT_SLOTS];
const void* g_actVt[eng::PLACTION_SLOTS];
const char DTI_NAME[] = "cPlActCompat";
bool g_dtiReady = false;

Ext* ExtOf(void* act) { return (Ext*)((uint8_t*)act + eng::PLACTION_SIZE); }
bool IsOurs(void* act) { return act && *(const void* const**)act == g_actVt && ExtOf(act)->magic == EXT_MAGIC; }

void* LayerOf(void* player, int layer) {
    void* model = At<void*>(player, eng::P_MODEL);
    if (!model) model = player;
    return (uint8_t*)model + eng::MODEL_LAYERS + layer * eng::LAYER_SIZE;
}

uint32_t JamCrc(const char* s) {
    uint32_t c = 0xFFFFFFFFu;
    for (; *s; s++) {
        c ^= (uint8_t)*s;
        for (int k = 0; k < 8; k++) c = (c >> 1) ^ (0xEDB88320u & (0u - (c & 1)));
    }
    return c;
}

}  // namespace

// MtDTI::newInstance for our class (thiscall; ECX = the descriptor).
extern "C" void* __fastcall CompatNewInstance(MtDTI* self, void*) {
    (void)self;
    void* alloc = ((FnGetAllocator)eng::F_GET_ALLOCATOR)((const void*)eng::PLACTION_DTI);
    if (!alloc) return nullptr;
    FnAlloc fn = (*(FnAlloc**)alloc)[0x1C / 4];
    void* mem = fn(alloc, ACT_SIZE, 16, ((MtDTI*)eng::PLACTION_DTI)->id);
    if (!mem) return nullptr;
    memset(mem, 0, ACT_SIZE);
    CompatPlActionCtor(mem);
    *(const void**)mem = g_actVt;
    Ext* x = ExtOf(mem);
    x->magic = EXT_MAGIC;
    x->prog = -1;
    return mem;
}

namespace {

// ---- shells ----------------------------------------------------------------------------------------
struct Spawn {
    int active;
    float wait;
    int prog, group, index, level;
    float pos[4], dir[4];
};
Spawn g_spawns[MAX_SPAWNS];
void* g_spawnOwner = nullptr;

void* FireShell(void* player, Program& p, int group, int index, int level, const float* pos, const float* dir) {
    void* mgr = *(void**)eng::SHELL_MANAGER;
    if (!mgr || !p.shellRes) return nullptr;
    void* shell = CompatCreateShl((uint32_t)group, mgr, p.shellRes, (uint32_t)index, player, player, 0x12);
    if (!shell) {
        Log("%s: the shell list has no shell %d/%d (or its limit is reached)", p.name, group, index);
        return nullptr;
    }
    void* col = CollisionFor(p, level);
    if (col && !At<void*>(shell, eng::SHL_COLLISION)) {
        At<void*>(shell, eng::SHL_COLLISION) = col;
        ((FnThis)eng::F_RES_ADDREF)(col);
    }
    ((FnSetShotCoord)eng::F_SET_SHOT_COORD)(dir, pos, shell);
    static int logged = 0;
    if (logged < 24) {
        logged++;
        Log("%s: shell %d/%d -> %p at (%.0f, %.0f, %.0f), collision %p", p.name, group, index, shell, pos[0], pos[1],
            pos[2], col);
    }
    return shell;
}

void Facing(void* player, float* dir) {
    const float* z = (const float*)((uint8_t*)player + eng::P_WMAT + 0x20);
    float x = z[0], y = z[2], len = sqrtf(x * x + y * y);
    if (len < 1e-4f) x = 0, y = 1, len = 1;
    dir[0] = x / len;
    dir[1] = 0;
    dir[2] = y / len;
    dir[3] = 0;
}

void QueueWave(void* player, int prog, const Instr& in, int level) {
    Program& p = g_programs[prog];
    bool strong = level >= 6;
    int count = strong ? in.a[3] : in.a[2];
    float step = in.f[0], interval = in.f[1];
    float origin[4], dir[4];
    memcpy(origin, (uint8_t*)player + eng::P_POS, 16);
    Facing(player, dir);
    int queued = 0;
    for (int k = 0; k < count; k++) {
        for (auto& s : g_spawns) {
            if (s.active) continue;
            s = Spawn{1, interval * k, prog, in.a[0], in.a[1] + k, level, {}, {}};
            for (int c = 0; c < 3; c++) s.pos[c] = origin[c] + dir[c] * step * (k + 1);
            memcpy(s.dir, dir, 16);
            queued++;
            break;
        }
    }
    g_spawnOwner = player;
    Log("%s: wave of %d from group %d (level %d, step %.0f, every %.0f frames)", p.name, queued, in.a[0], level, step,
        interval);
}

void TickSpawns(void* player, float dt) {
    if (player != g_spawnOwner) {
        for (auto& s : g_spawns) s.active = 0;
        return;
    }
    for (auto& s : g_spawns) {
        if (!s.active) continue;
        if (s.wait > 0) {  // Online's generators count their wait from the frame they start
            s.wait -= dt;
            continue;
        }
        s.active = 0;
        FireShell(player, g_programs[s.prog], s.group, s.index, s.level, s.pos, s.dir);
    }
}

// ---- running a program -------------------------------------------------------------------------------
void Play(void* player, Ext* x, uint32_t motNo, float blend, float frame) {
    FnPlayBase play = (FnPlayBase)(*(void***)player)[eng::VT_PLAY_BASE_MOTION / 4];
    play(player, motNo, 1, 0, blend, frame, 1.0f);
    x->motion = motNo;
    for (auto& s : x->seq) s.active = 0;  // Online's sequence counters restart with every motion
}

bool OurMotionShows(void* player, Ext* x) {
    return At<uint16_t>(LayerOf(player, 0), eng::LAYER_NO) == (uint16_t)x->motion;
}

void UpdateCounters(void* player, Ext* x) {
    for (auto& s : x->seq) {
        if (!s.active) continue;
        void* layer = LayerOf(player, s.layer);
        if (At<uint16_t>(layer, eng::LAYER_NO) != (uint16_t)x->motion) continue;
        int cur = (int)At<float>(layer, eng::LAYER_FRAME);
        if (s.last == INT32_MIN) s.last = cur - 1;
        for (int f = s.last + 1, guard = 0; f <= cur && guard < 256; f++, guard++) {
            if (((FnCalcSeq)eng::F_CALC_SEQUENCE)(layer, (uint32_t)s.page, f, f) & (1u << s.bit)) s.count++;
        }
        if (cur > s.last) s.last = cur;
    }
}

void EndAction(void* act) { At<uint8_t>(act, eng::ACT_END) = 1; }

void Run(void* act, float dt) {
    Ext* x = ExtOf(act);
    void* player = At<void*>(act, eng::ACT_PLAYER);
    if (!player || x->prog < 0 || x->prog >= g_programCount) return EndAction(act);
    Program& p = g_programs[x->prog];
    x->total += dt;
    UpdateCounters(player, x);
    for (int guard = 0; guard < MAX_CODE; guard++) {
        if (x->pc >= p.n) return EndAction(act);
        const Instr& in = p.code[x->pc];
        if (x->waitPc != x->pc) {  // entering an instruction
            x->waitPc = x->pc;
            x->waitTime = 0;
            x->waitBase = in.op == OP_WAIT_COUNTER ? x->seq[in.a[0]].count : 0;
            if (x->verbose)
                Log("%s: step %d %s (%d %d %d %d) at %.1f frames; layer 0 plays 0x%X at frame %.1f", p.name, x->pc,
                    OP_NAMES[in.op], in.a[0], in.a[1], in.a[2], in.a[3], x->total,
                    At<uint16_t>(LayerOf(player, 0), eng::LAYER_NO), At<float>(LayerOf(player, 0), eng::LAYER_FRAME));
        } else {
            x->waitTime += dt;
        }
        switch (in.op) {
        case OP_PLAY:
            Play(player, x, (uint32_t)in.a[0], in.f[0], in.f[1]);
            break;
        case OP_ARM:
            x->seq[in.a[0]] = Seq{1, in.a[1], in.a[2], in.a[3], INT32_MIN, 0};
            break;
        case OP_WAIT_COUNTER:
            if (x->seq[in.a[0]].count > x->waitBase) break;
            if (x->waitTime > in.f[0]) {
                Log("%s: counter %d never fired (motion 0x%X, %.0f frames); ending", p.name, in.a[0], x->motion, x->waitTime);
                return EndAction(act);
            }
            return;
        case OP_WAIT_END: {
            void* layer = LayerOf(player, 0);
            if (OurMotionShows(player, x) && ((FnIsEnd)eng::F_IS_END)(layer)) break;
            if (x->waitTime > in.f[0]) {
                Log("%s: motion 0x%X did not end in %.0f frames; ending", p.name, x->motion, x->waitTime);
                return EndAction(act);
            }
            return;
        }
        case OP_WAIT_FRAMES:
            if (x->waitTime >= in.f[0]) break;
            return;
        case OP_WAVE:
            QueueWave(player, x->prog, in, x->level);
            break;
        case OP_SHOT: {
            float pos[4], dir[4];
            memcpy(pos, (uint8_t*)player + eng::P_POS, 16);
            Facing(player, dir);
            for (int c = 0; c < 3; c++) pos[c] += dir[c] * in.f[0];
            FireShell(player, p, in.a[0], x->level >= 6 ? in.a[2] : in.a[1], x->level, pos, dir);
            break;
        }
        case OP_END:
        default:
            return EndAction(act);
        }
        x->pc++;
    }
}

void Begin(void* act) {
    Ext* x = ExtOf(act);
    void* player = At<void*>(act, eng::ACT_PLAYER);
    x->prog = (int32_t)At<uint32_t>(act, eng::ACT_U32 + 0);
    x->level = (int32_t)At<uint32_t>(act, eng::ACT_U32 + 4);
    x->side = (int32_t)At<uint32_t>(act, eng::ACT_U32 + 8);
    x->slot = (int32_t)At<uint32_t>(act, eng::ACT_U32 + 12);
    x->skill = At<int32_t>(act, eng::ACT_S32);
    x->pc = 0;
    x->waitPc = -1;
    if (!player || x->prog < 0 || x->prog >= g_programCount || !g_programs[x->prog].ready) {
        Log("action started without a ready program (%d); ending it", x->prog);
        return EndAction(act);
    }
    Program& p = g_programs[x->prog];
    x->verbose = ++p.runs <= 3;
    if (x->verbose) Log("%s: binding motion list %p to bank %u, effect provider %s to slot %u", p.name, p.motionRes, p.bank,
                        p.epv[0] ? p.epv : "(none)", p.epvSlot);
    // As the game's own single-motion skills set up (cPlActWpnSwordCstmKiriAge::init): on the ground, in an
    // attack, moving only by the motion.
    At<uint32_t>(player, eng::P_STAT) |= 1 | 2 | 4 | 0x200 | 0x400;
    memset((uint8_t*)player + 0x2530, 0, 16);
    memset((uint8_t*)player + 0x2540, 0, 16);
    At<uint32_t>(player, 0x35E8) |= 1;
    At<uint32_t>(player, 0x2360) = 0x800;
    At<uint32_t>(player, 0x2364) = 0;
    At<uint32_t>(player, 0x1DF0) = 0xE;
    ((FnSetMotionList)eng::F_SET_MOTION_LIST)(player, p.motionRes, p.bank);
    if (p.epv[0]) CompatSetEpvResource(p.epv, player, p.epvSlot);
    uint32_t old = At<uint32_t>(player, eng::P_OLD_STAT);
    bool interp = !(old & 0x10) && !(old & 0x6000) && (old & 0x200);
    ((FnThisU)eng::F_WEAPON_INTERP)(player, interp ? 1 : 0);
    Log("%s: begins (level %d, %s skill %d, slot %d)", p.name, x->level, x->side == 2 ? "sub" : "main", x->skill,
        x->slot + 1);
    Run(act, 0);
}

}  // namespace

// ---- the action's virtuals (thiscall; __fastcall with an unused EDX has the same contract) ----------
extern "C" void* __fastcall CompatActDtor(void* act, void*, uint32_t flags) {
    if (act && *(const void* const**)act == g_actVt) ExtOf(act)->magic = 0;
    return ((FnDtor)eng::F_PLACTION_DTOR)(act, flags);
}
extern "C" const void* __fastcall CompatActGetDti(void*, void*) { return &g_dti; }
extern "C" void __fastcall CompatActInit(void* act, void*) {
    ((FnThis)eng::F_PLACTION_INIT)(act);
    Begin(act);
}
extern "C" void __fastcall CompatActMove(void* act, void*) {
    ((FnThis)eng::F_PLACTION_MOVE)(act);
    if (At<uint8_t>(act, eng::ACT_END)) return;
    void* player = At<void*>(act, eng::ACT_PLAYER);
    float dt = player ? At<float>(player, eng::P_FRAME_STEP) : 0;
    if (!(dt > 0 && dt < 8)) dt = 1;
    Run(act, dt);
}
extern "C" void __fastcall CompatActFinal(void* act, void*) {
    Ext* x = ExtOf(act);
    if (x->magic == EXT_MAGIC && x->prog >= 0 && x->prog < g_programCount)
        Log("%s: ends after %.0f frames (step %d)", g_programs[x->prog].name, x->total, x->pc);
    ((FnThis)eng::F_PLACTION_FINAL)(act);
}

namespace {

// The game builds cPlAction::DTI before WinMain; ours is a copy with our size, name, newInstance and parent.
bool EnsureDti() {
    if (g_dtiReady) return true;
    MtDTI* base = (MtDTI*)eng::PLACTION_DTI;
    if (!Readable(eng::PLACTION_DTI, sizeof(MtDTI)) || base->vtable != (const void* const*)eng::PLACTION_DTI_VT ||
        (base->bits & 0x7FFFFF) != eng::PLACTION_SIZE)
        return false;
    memcpy(g_dtiVt, (const void*)eng::PLACTION_DTI_VT, sizeof g_dtiVt);
    g_dtiVt[1] = (const void*)&CompatNewInstance;
    g_dti = *base;
    g_dti.vtable = g_dtiVt;
    g_dti.name = DTI_NAME;
    g_dti.next = nullptr;
    g_dti.child = nullptr;
    g_dti.parent = base;
    g_dti.link = nullptr;
    g_dti.bits = (base->bits & ~0x7FFFFFu) | ACT_SIZE;
    g_dti.id = JamCrc(DTI_NAME) & 0x7FFFFFFF;
    g_dtiReady = true;
    Log("action class %s ready: %u bytes, allocator %u", DTI_NAME, ACT_SIZE, (base->bits >> 23) & 0x3F);
    return true;
}

void* Arisen() {
    void* pm = *(void**)eng::PLAYER_MANAGER;
    return pm ? At<void*>(pm, eng::ARISEN) : nullptr;
}

bool Start(void* player, int prog, int level, int side, int slot, int skill) {
    if (prog < 0 || prog >= g_programCount || !EnsureDti() || !PrepareProgram(g_programs[prog])) return false;
    FnSetActionExDti set = (FnSetActionExDti)(*(void***)player)[eng::VT_SET_ACTION_EX_DTI / 4];
    set(player, &g_dti, (uint32_t)prog, (uint32_t)level, (uint32_t)side, (uint32_t)slot, skill, 0, 0, 0, 0, 0, 0, 0, 0,
        0, 0, 0);
    return true;
}

// Which equipped skill (and its slot) the game has just asked for.
bool FindSkill(void* player, int32_t req, int* side, int* slot, int* skill) {
    uint8_t* info = At<uint8_t*>(player, eng::P_PLAYER_INFO);
    if (!info) return false;
    for (int s = 0; s < 2; s++) {
        const int32_t* ids = (const int32_t*)(info + (s ? eng::INFO_SUB_SKILL : eng::INFO_MAIN_SKILL));
        for (int k = 0; k < 3; k++) {
            int32_t id = ids[k];
            if (id < 0 || (uint32_t)id >= eng::SKILL_COUNT) continue;
            if (*(const int32_t*)(eng::SKILL_TABLE + (uint32_t)id * 0x20 + 4) != req) continue;
            *side = s;
            *slot = k;
            *skill = id;
            return true;
        }
    }
    return false;
}

int LevelOf(void* player, int skill) {
    uint8_t* info = At<uint8_t*>(player, eng::P_PLAYER_INFO);
    int type = info ? CompatSkillLevel(skill, info + eng::INFO_LEARNED) : 0;
    if (type < 0 || type > 3) type = 0;
    return g_levelMap[type];
}

bool IdleForTest(void* player) {
    int32_t no = At<int32_t>(player, eng::P_ACTION_NO);
    return !At<uint8_t>(player, eng::P_IS_UPDATE) && (no == 0 || no == 1 || no == 2 || no == 3 || no == 5);
}

using FnCheck = void(__thiscall*)(void* player);
FnCheck g_origCheck = (FnCheck)eng::F_CHECK_ACTION;
bool g_testDown = false, g_trace = false;
int32_t g_lastTrace = -1;

}  // namespace

// uPlayer::checkAction, wrapped (uPlayer's vtable slot 136).
extern "C" void __fastcall CompatCheckAction(void* player, void*) {
    if (!g_enabled || player != Arisen()) return g_origCheck(player);
    float dt = At<float>(player, eng::P_FRAME_STEP);
    if (!(dt > 0 && dt < 8)) dt = 1;
    TickSpawns(player, dt);
    void* cur = At<void*>(player, eng::P_ACTION);
    bool running = IsOurs(cur) && !At<uint8_t>(cur, eng::ACT_END);

    uint8_t flagsBefore[2] = {At<uint8_t>(player, eng::P_IS_UPDATE), At<uint8_t>(player, eng::P_IS_UPDATE + 1)};
    int32_t reqBefore = At<int32_t>(player, eng::P_ACTION_REQ);
    g_origCheck(player);
    if (!At<uint8_t>(player, eng::P_IS_UPDATE)) {
        // Test key: start a program from standing, walking or running.
        if (g_testKey && g_testProgram >= 0) {
            bool down = (GetAsyncKeyState(g_testKey) & 0x8000) != 0;
            if (down && !g_testDown && !running && IdleForTest(player))
                Start(player, g_testProgram, g_testLevel, 0, 0, -1);
            g_testDown = down;
        }
        return;
    }
    int32_t req = At<int32_t>(player, eng::P_ACTION_REQ);
    int side = 0, slot = 0, skill = -1;
    bool isSkill = req >= 0 && FindSkill(player, req, &side, &slot, &skill);
    if (g_trace && req != g_lastTrace) {
        g_lastTrace = req;
        if (isSkill) Log("trace: the game asks for action 0x%08X (%s skill %d in slot %d)%s", req, side ? "sub" : "main", skill,
                         slot + 1, running ? " while a program runs" : "");
        else Log("trace: the game asks for action 0x%08X%s", req, running ? " while a program runs" : "");
    }
    if (!isSkill) return;
    int prog = g_slotProgram[side][slot];
    if (prog < 0) return;
    if (running) {
        // The button is still held: the program keeps running instead of restarting.
        At<uint8_t>(player, eng::P_IS_UPDATE) = flagsBefore[0];
        At<uint8_t>(player, eng::P_IS_UPDATE + 1) = flagsBefore[1];
        At<int32_t>(player, eng::P_ACTION_REQ) = reqBefore;
        return;
    }
    Start(player, prog, LevelOf(player, skill), side + 1, slot, skill);
}

namespace {

struct Site {
    const char* name;
    uintptr_t at;
    uint8_t expect[10];
};

const Site FUNCS[] = {
    {"calcSequence", 0x01057060, {0x8B, 0x44, 0x24, 0x04, 0x56, 0x8B, 0xB4, 0x81, 0x00, 0x01}},
    {"isEnd", 0x01057100, {0xF6, 0x41, 0x06, 0x05, 0x0F, 0x95, 0xC0, 0xC3, 0xCC, 0xCC}},
    {"setMotionList", 0x01057670, {0x53, 0x8B, 0x5C, 0x24, 0x0C, 0x56, 0x8B, 0xF1, 0x8B, 0x8C}},
    {"resCreate", 0x00DBC170, {0x8B, 0x54, 0x24, 0x08, 0x83, 0xEC, 0x44, 0x56, 0x8B, 0xC2}},
    {"resAddRef", 0x00DE0B80, {0x51, 0x8B, 0x0D, 0xA0, 0x0A, 0x8D, 0x01, 0xE8, 0xB4, 0x8B}},
    {"resRelease", 0x00DE0B90, {0xA1, 0xA0, 0x0A, 0x8D, 0x01, 0x8B, 0x10, 0x51, 0x8B, 0xC8}},
    {"createShlSub", 0x004AB970, {0x51, 0x53, 0x8B, 0x5C, 0x24, 0x0C, 0x55, 0x8B, 0x6C, 0x24}},
    {"setShotCoord", 0x00BD6D20, {0x8B, 0x44, 0x24, 0x04, 0x56, 0x8B, 0xB0, 0xDC, 0x29, 0x00}},
    {"setEpvResource", 0x00B572E0, {0x85, 0xC0, 0x75, 0x05, 0x32, 0xC0, 0xC2, 0x08, 0x00, 0x8B}},
    {"getSkillLevelType", 0x00780BA0, {0x83, 0xFE, 0xFF, 0x75, 0x05, 0x33, 0xC0, 0xC2, 0x04, 0x00}},
    {"weaponInterpolate", 0x00B86010, {0x8B, 0x81, 0x68, 0x03, 0x00, 0x00, 0x0F, 0xB6, 0x80, 0x96}},
    {"cPlAction ctor", 0x004CAC70, {0x33, 0xC9, 0x89, 0x48, 0x44, 0x89, 0x48, 0x48, 0x88, 0x48}},
    {"cPlAction::init", 0x00AC4F70, {0x53, 0x56, 0x8B, 0xF1, 0xE8, 0x17, 0xC9, 0xD2, 0xFF, 0x8B}},
    {"cPlAction::move", 0x00AC5100, {0x53, 0x55, 0x8B, 0xE9, 0x8B, 0x45, 0x58, 0x33, 0xDB, 0xF6}},
    {"cPlAction::final", 0x00AC8A10, {0x56, 0x8B, 0xF1, 0x8B, 0x46, 0x44, 0xF6, 0x80, 0x6C, 0x20}},
    {"cPlAction::setup", 0x00AC4F40, {0x8B, 0x44, 0x24, 0x08, 0x56, 0x8B, 0xF1, 0x8B, 0x4C, 0x24}},
    {"cPlAction dtor", 0x004CACF0, {0xF6, 0x44, 0x24, 0x04, 0x01, 0x56, 0x8B, 0xF1, 0xC7, 0x06}},
    {"cPlAction::getDTI", 0x004CAC60, {0xB8, 0x28, 0x1F, 0x9A, 0x01, 0xC3, 0xCC, 0xCC, 0xCC, 0xCC}},
    {"cPlAction newInstance", 0x00AC3320, {0x68, 0x28, 0x1F, 0x9A, 0x01, 0xE8, 0xF6, 0x2A, 0x23, 0x00}},
    {"getAllocator", 0x00CF5E20, {0x8B, 0x44, 0x24, 0x04, 0x8B, 0x48, 0x18, 0xC1, 0xE9, 0x17}},
    {"setActionExDTI", 0x004C73D0, {0xF3, 0x0F, 0x10, 0x44, 0x24, 0x44, 0x53, 0x8B, 0x44, 0x24}},
    {"cActionManager::setEx(dti)", 0x007F2120, {0x8B, 0x4E, 0x14, 0x83, 0xEC, 0x40, 0x85, 0xC9, 0x74, 0x0F}},
    {"uPlayer::checkAction", 0x00B564E0, {0x83, 0xB9, 0xC8, 0x2D, 0x00, 0x00, 0x00, 0x74, 0x05, 0xE9}},
    {"requestSetBaseMotion", 0x00B800C0, {0x0F, 0x57, 0xC9, 0xF3, 0x0F, 0x10, 0x44, 0x24, 0x10, 0x0F}},
    {"setEpv", 0x008844A0, {0x53, 0x8B, 0x5C, 0x24, 0x08, 0x56, 0x8B, 0xF0, 0x85, 0xFF}},
};

const uint32_t PLACTION_VT_WANT[eng::PLACTION_SLOTS] = {
    0x004CACF0, 0x007A1DA0, 0x00401060, 0x007F15A0, 0x004CAC60, 0x00AC4F70, 0x00AC5100,
    0x005F35C0, 0x00AC8A10, 0x004C9E20, 0x005F35C0, 0x00AC51A0, 0x00C342F0, 0x00AC4F40};

bool Verify() {
    for (const Site& s : FUNCS) {
        if (!Readable(s.at, sizeof s.expect) || memcmp((const void*)s.at, s.expect, sizeof s.expect) != 0)
            return Log("refused: %s at 0x%08X is not the expected code (another build, or already patched)", s.name,
                       (unsigned)s.at), false;
    }
    const uint32_t* vt = (const uint32_t*)eng::PLACTION_VT;
    if (!Readable(eng::PLACTION_VT, sizeof PLACTION_VT_WANT) || memcmp(vt, PLACTION_VT_WANT, sizeof PLACTION_VT_WANT) != 0)
        return Log("refused: cPlAction's vtable does not match"), false;
    const uint32_t* dvt = (const uint32_t*)eng::PLACTION_DTI_VT;
    if (!Readable(eng::PLACTION_DTI_VT, 4 * eng::DTI_VT_SLOTS) || dvt[1] != eng::PLACTION_NEW)
        return Log("refused: cPlAction::DTI's vtable does not match"), false;
    const uint32_t* pvt = (const uint32_t*)eng::PLAYER_VT;
    if (!Readable(eng::PLAYER_VT, eng::VT_CHECK_ACTION + 4) || pvt[eng::VT_CHECK_ACTION / 4] != eng::F_CHECK_ACTION ||
        pvt[eng::VT_SET_ACTION_EX_DTI / 4] != eng::F_SET_ACTION_EX_DTI ||
        pvt[eng::VT_PLAY_BASE_MOTION / 4] != eng::F_PLAY_BASE_MOTION)
        return Log("refused: uPlayer's vtable does not match (another build, or another mod changed it)"), false;
    return true;
}

void ReadSettings() {
    g_enabled = GetPrivateProfileIntW(L"compat", L"enabled", 1, g_ini) != 0;
    g_trace = GetPrivateProfileIntW(L"compat", L"trace", 0, g_ini) != 0;
    wchar_t w[128];
    char name[128];
    static const wchar_t* const keys[2][3] = {{L"main1", L"main2", L"main3"}, {L"sub1", L"sub2", L"sub3"}};
    for (int s = 0; s < 2; s++) {
        for (int k = 0; k < 3; k++) {
            GetPrivateProfileStringW(L"slots", keys[s][k], L"", w, 128, g_ini);
            WideCharToMultiByte(CP_UTF8, 0, w, -1, name, sizeof name, nullptr, nullptr);
            char* t = Trim(name);
            g_slotProgram[s][k] = *t ? FindProgram(t) : -1;
            if (*t && g_slotProgram[s][k] < 0) Log("compat.ini: [slots] %ls names no skill '%s'", keys[s][k], t);
            else if (*t) Log("slot %ls -> %s", keys[s][k], t);
        }
    }
    static const wchar_t* const levels[4] = {L"unlearned", L"learned", L"level2", L"level3"};
    for (int i = 0; i < 4; i++) {
        int v = (int)GetPrivateProfileIntW(L"levels", levels[i], g_levelMap[i], g_ini);
        g_levelMap[i] = v < 1 ? 1 : v > MAX_LEVEL ? MAX_LEVEL : v;
    }
    GetPrivateProfileStringW(L"test", L"key", L"0", w, 128, g_ini);
    long key = wcstol(w, nullptr, 0);
    g_testKey = key > 0 && key < 256 ? (int)key : 0;
    g_testLevel = (int)GetPrivateProfileIntW(L"test", L"level", 10, g_ini);
    g_testLevel = g_testLevel < 1 ? 1 : g_testLevel > MAX_LEVEL ? MAX_LEVEL : g_testLevel;
    GetPrivateProfileStringW(L"test", L"skill", L"", w, 128, g_ini);
    WideCharToMultiByte(CP_UTF8, 0, w, -1, name, sizeof name, nullptr, nullptr);
    g_testProgram = *Trim(name) ? FindProgram(Trim(name)) : -1;
    if (g_testKey) Log("test key 0x%02X starts %s", g_testKey, g_testProgram >= 0 ? g_programs[g_testProgram].name : "(none)");
}

bool Patch() {
    uint32_t* slot = (uint32_t*)(eng::PLAYER_VT + eng::VT_CHECK_ACTION);
    DWORD old;
    if (!VirtualProtect(slot, 4, PAGE_READWRITE, &old))
        return Log("failed: VirtualProtect on uPlayer's vtable (%lu)", GetLastError()), false;
    *slot = (uint32_t)(uintptr_t)&CompatCheckAction;
    VirtualProtect(slot, 4, old, &old);
    return true;
}

void Setup(HMODULE self) {
    InitializeCriticalSection(&g_logLock);
    GetModuleFileNameW(nullptr, g_root, MAX_PATH);
    wchar_t* slash = wcsrchr(g_root, L'\\');
    if (slash) *slash = 0;
    GetModuleFileNameW(self, g_selfDir, MAX_PATH);
    wcscpy_s(g_ini, g_selfDir);
    wchar_t* dot = wcsrchr(g_ini, L'.');
    if (dot) wcscpy_s(dot, MAX_PATH - (dot - g_ini), L".ini");
    slash = wcsrchr(g_selfDir, L'\\');
    if (slash) *slash = 0;

    wchar_t probe[8];
    g_harness = GetEnvironmentVariableW(L"RIFTSTONE_COMPAT_HARNESS", probe, 8) > 0;
    if (g_harness && GetEnvironmentVariableW(L"RIFTSTONE_COMPAT_ROOT", g_root, MAX_PATH) == 0) g_root[0] = 0;
    wchar_t dir[MAX_PATH];
    _snwprintf_s(dir, MAX_PATH, _TRUNCATE, L"%s\\riftstone", g_root);
    CreateDirectoryW(dir, nullptr);
    _snwprintf_s(dir, MAX_PATH, _TRUNCATE, L"%s\\riftstone\\logs", g_root);
    CreateDirectoryW(dir, nullptr);
    _snwprintf_s(g_logPath, MAX_PATH, _TRUNCATE, L"%s\\riftstone\\logs\\compat.log", g_root);
    DeleteFileW(g_logPath);

    if (!g_harness && (uintptr_t)GetModuleHandleW(nullptr) != IMAGE_BASE) {
        Log("refused: the host is not DDDA.exe at 0x00400000");
        return;
    }
    if (!Verify()) return;
    memcpy(g_actVt, (const void*)eng::PLACTION_VT, sizeof g_actVt);
    g_actVt[0] = (const void*)&CompatActDtor;
    g_actVt[4] = (const void*)&CompatActGetDti;
    g_actVt[5] = (const void*)&CompatActInit;
    g_actVt[6] = (const void*)&CompatActMove;
    g_actVt[8] = (const void*)&CompatActFinal;
    // Programs: the file compat.ini names next to the plugin (optional), then every *.skills a mod
    // installed into riftstone\overlay\compat\ (in name order; the first definition of a name counts).
    wchar_t skills[MAX_PATH], name[64];
    GetPrivateProfileStringW(L"compat", L"skills", L"compat.skills", name, 64, g_ini);
    _snwprintf_s(skills, MAX_PATH, _TRUNCATE, L"%s\\%s", g_selfDir, name);
    bool parsed = true;
    int files = 0;
    if (GetFileAttributesW(skills) != INVALID_FILE_ATTRIBUTES) parsed &= LoadPrograms(skills), files++;
    wchar_t pattern[MAX_PATH];
    _snwprintf_s(pattern, MAX_PATH, _TRUNCATE, L"%s\\riftstone\\overlay\\compat\\*.skills", g_root);
    WIN32_FIND_DATAW fd;
    HANDLE find = FindFirstFileW(pattern, &fd);
    wchar_t found[16][MAX_PATH];
    int nfound = 0;
    if (find != INVALID_HANDLE_VALUE) {
        do {
            if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) && nfound < 16)
                _snwprintf_s(found[nfound++], MAX_PATH, _TRUNCATE, L"%s\\riftstone\\overlay\\compat\\%s", g_root, fd.cFileName);
        } while (FindNextFileW(find, &fd));
        FindClose(find);
    }
    qsort(found, (size_t)nfound, sizeof found[0], [](const void* a, const void* b) {
        return _wcsicmp((const wchar_t*)a, (const wchar_t*)b);
    });
    for (int i = 0; i < nfound; i++) parsed &= LoadPrograms(found[i]), files++;
    if (!files) Log("no skill programs: neither %ls nor riftstone\\overlay\\compat\\*.skills (install a compat mod)", skills);
    ReadSettings();
    if (!g_enabled) return Log("compat: off in compat.ini ([compat] enabled=0)");
    if (!parsed) Log("compat: some lines of %ls were not understood (see above); those skills may be incomplete", skills);
    int ready = 0;
    for (int i = 0; i < g_programCount; i++) ready += CheckProgramFiles(g_programs[i]) ? 1 : 0;
    if (!Patch()) return;
    Log("compat: uPlayer::checkAction wrapped; %d skill program(s), %d with every file present (%s)", g_programCount,
        ready, g_harness ? "harness" : "game");
}

}  // namespace

// ---- test surface (the harness only) ----------------------------------------------------------------------
extern "C" __declspec(dllexport) void* CompatTest_Dti() { return EnsureDti() ? &g_dti : nullptr; }
extern "C" __declspec(dllexport) const void* const* CompatTest_ActionVtable() { return g_actVt; }
extern "C" __declspec(dllexport) uint32_t CompatTest_ActionSize() { return ACT_SIZE; }
extern "C" __declspec(dllexport) int CompatTest_ProgramCount() { return g_programCount; }
extern "C" __declspec(dllexport) const char* CompatTest_ProgramName(int i) {
    return i >= 0 && i < g_programCount ? g_programs[i].name : nullptr;
}
extern "C" __declspec(dllexport) int CompatTest_ProgramOps(int i) { return i >= 0 && i < g_programCount ? g_programs[i].n : -1; }
extern "C" __declspec(dllexport) void CompatTest_SetTargets(uintptr_t createShl, uintptr_t setEpv, uintptr_t skillLevel) {
    if (!g_harness) return;
    if (createShl) g_fnCreateShl = createShl;
    if (setEpv) g_fnSetEpvResource = setEpv;
    if (skillLevel) g_fnSkillLevel = skillLevel;
}
extern "C" __declspec(dllexport) int CompatTest_Counter(void* act, int slot) {
    return IsOurs(act) && slot >= 0 && slot < 10 ? ExtOf(act)->seq[slot].count : -1;
}
extern "C" __declspec(dllexport) int CompatTest_Pc(void* act) { return IsOurs(act) ? ExtOf(act)->pc : -1; }
extern "C" __declspec(dllexport) int CompatTest_Spawns() {
    int n = 0;
    for (auto& s : g_spawns) n += s.active;
    return n;
}
extern "C" __declspec(dllexport) void* CompatTest_CreateShl(uint32_t g, void* m, void* l, uint32_t i, void* o, void* p, uint32_t line) {
    return CompatCreateShl(g, m, l, i, o, p, line);
}
extern "C" __declspec(dllexport) uint32_t CompatTest_SetEpvResource(const char* path, void* model, uint32_t slot) {
    return CompatSetEpvResource(path, model, slot);
}
extern "C" __declspec(dllexport) int32_t CompatTest_SkillLevel(int32_t id, void* info) { return CompatSkillLevel(id, info); }
extern "C" __declspec(dllexport) void CompatTest_Ready(int i, void* motion, void* shells) {
    if (!g_harness || i < 0 || i >= g_programCount) return;
    g_programs[i].motionRes = motion;
    g_programs[i].shellRes = shells;
    g_programs[i].ready = true;
}

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(module);
        Setup(module);
    }
    return TRUE;
}

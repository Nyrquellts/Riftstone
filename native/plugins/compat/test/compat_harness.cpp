// compat_harness_core -- runs the compat plugin inside the real game code, without the game.
//
//   compat_stub.exe <DDDA.exe> <work folder>   (the stub loads this DLL and calls HarnessMain)
//
// Maps DDDA.exe's image at its fixed base over the stub's image, points the game's globals at fakes
// (resource manager, allocator, player manager, shell manager, cPlAction's class descriptor, which the
// game builds before WinMain), loads <work>\compat.asi (which verifies and patches the mapped code) and:
//   * asks for the program's action through the real uPlayer::setActionExDTI and the real action
//     manager commit (0x007F1EA0), so the real constructor, setup and init of cPlAction run on it;
//   * plays the program frame by frame through the real cPlAction::move, the real calcSequence on a
//     motion whose event track is known, and checks when each counter fires, what is played, which
//     shells are asked for (with which registers) and where the real setShotCoord puts them;
//   * lets the wrapped checkAction fire the queued wave, then finishes and frees the action through the
//     real cPlAction::final and destructor;
//   * checks the slot mapping: a request for an equipped skill becomes the program's action.
// Prints "pass"/"FAIL" lines; exits 0 when everything passed, 1 on a failure, 2 when the image cannot
// be mapped here.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shellapi.h>

#include <math.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include <string>
#include <vector>

namespace {
constexpr uintptr_t BASE = 0x00400000;
constexpr uintptr_t RES_MANAGER = 0x018D0AA0, PLAYER_MANAGER = 0x018FA4EC, SHELL_MANAGER = 0x018FA508;
constexpr uintptr_t ALLOCATORS = 0x01876628, PLACTION_DTI = 0x019A1F28, PLACTION_DTI_VT = 0x015E043C;
constexpr uintptr_t PLAYER_VT = 0x015E90D0, SKILL_TABLE = 0x014F3658, UPDATE_NEXT_ACTION = 0x007F1EA0;
constexpr uintptr_t DTI_MOTION_LIST = 0x018D19DC, DTI_OBJ_COLLISION = 0x019A8BB4, DTI_SHL = 0x019A917C;
constexpr uint32_t ALLOC_INDEX = 5, PLACTION_ID = 0x0BADF00D;

int g_fails = 0;
void Check(bool ok, const std::string& what) {
    printf("  %s  %s\n", ok ? "pass" : "FAIL", what.c_str());
    if (!ok) g_fails++;
}
std::string Fmt(const char* f, ...) {
    char b[512];
    va_list ap;
    va_start(ap, f);
    _vsnprintf_s(b, sizeof b, _TRUNCATE, f, ap);
    va_end(ap);
    return b;
}

// ---- fakes -----------------------------------------------------------------------------------------
struct Obj {
    void** vtable;
    uint8_t pad[0x100];
};

// allocator: vtable +0x1C alloc(size, align, tag), +0x24 free(ptr)
void* g_allocVt[64];
Obj g_allocator;
uint32_t g_allocSize, g_allocAlign, g_allocTag;
void* g_lastAlloc;
void* g_lastFree;
void* __fastcall FakeAlloc(void*, void*, uint32_t size, uint32_t align, uint32_t tag) {
    g_allocSize = size;
    g_allocAlign = align;
    g_allocTag = tag;
    g_lastAlloc = _aligned_malloc(size, 16);
    memset(g_lastAlloc, 0xCD, size);
    return g_lastAlloc;
}
void __fastcall FakeFree(void*, void*, void* p) {
    g_lastFree = p;
    _aligned_free(p);
}

// resource manager: vtable +0x30 create(dti, path, flags), +0x38 release(res); addRef is the game's own
struct Res {
    void* vtable;
    char path[64];
    uint8_t pad[0x100];
};
void* g_resVt[16];
void* g_mgrVt[64];
Obj g_mgr;
struct Loaded {
    uintptr_t dti;
    std::string path;
    Res* res;
};
std::vector<Loaded> g_loaded;
int g_releases = 0;
void* __fastcall FakeCreate(void*, void*, uintptr_t dti, const char* path, uint32_t flags) {
    Res* r = (Res*)calloc(1, sizeof(Res));
    r->vtable = g_resVt;
    strncpy_s(r->path, path, _TRUNCATE);
    *(int32_t*)((uint8_t*)r + 0x48) = 1;
    *(uint32_t*)((uint8_t*)r + 0x50) = 1;
    g_loaded.push_back({dti, path, r});
    (void)flags;
    return r;
}
void __fastcall FakeRelease(void*, void*, void* res) {
    g_releases++;
    if (res) (*(int32_t*)((uint8_t*)res + 0x48))--;
}
Res* Find(const char* path) {
    for (auto& l : g_loaded)
        if (l.path == path) return l.res;
    return nullptr;
}

// createShlSub (EAX = group; mgr, list, index, owner, parent, line; ret 0x18)
struct ShlCall {
    uint32_t group, index, line;
    void *mgr, *list, *owner, *parent, *shell;
};
std::vector<ShlCall> g_shl;
uint32_t g_capGroup;
uint8_t g_shellParam[0x200];
void* __stdcall FakeCreateShlC(void* mgr, void* list, uint32_t index, void* owner, void* parent, uint32_t line) {
    uint8_t* shell = (uint8_t*)calloc(1, 0x3000);
    *(void**)(shell + 0x29DC) = g_shellParam;
    g_shl.push_back({g_capGroup, index, line, mgr, list, owner, parent, shell});
    return shell;
}
__declspec(naked) void FakeCreateShl() {
    __asm {
        mov g_capGroup, eax
        jmp FakeCreateShlC
    }
}

// setEpvResource (EAX = path; model, slot; ret 8)
std::string g_epvPath;
void* g_epvModel;
uint32_t g_epvSlot;
const char* g_capPath;
uint32_t __stdcall FakeSetEpvC(void* model, uint32_t slot) {
    g_epvPath = g_capPath ? g_capPath : "";
    g_epvModel = model;
    g_epvSlot = slot;
    return 1;
}
__declspec(naked) void FakeSetEpv() {
    __asm {
        mov g_capPath, eax
        jmp FakeSetEpvC
    }
}

// getSkillLevelType (ESI = skill, EDI = cPlayerInfo + 0x72C; bool; ret 4)
int32_t g_capSkill;
void* g_capInfo;
int32_t __stdcall FakeSkillLevelC(uint32_t) { return 2; }
__declspec(naked) void FakeSkillLevel() {
    __asm {
        mov g_capSkill, esi
        mov g_capInfo, edi
        jmp FakeSkillLevelC
    }
}

// the player's param table (vtable +0x3C getValue(id) -> f32) and requestSetBaseMotion (vtable +0x90)
void* g_paramVt[32];
Obj g_params;
float __fastcall FakeParam(void*, void*, uint32_t) { return 1.0f; }

struct PlayCall {
    uint32_t motNo, forcible, attr;
    float hokan, frame, speed;
};
std::vector<PlayCall> g_plays;
uint8_t* g_player;
void __fastcall FakePlay(void* player, void*, uint32_t motNo, uint32_t forcible, uint32_t attr, float hokan, float frame,
                         float speed) {
    g_plays.push_back({motNo, forcible, attr, hokan, frame, speed});
    uint8_t* layer = (uint8_t*)player + 0x380;
    *(uint16_t*)(layer + 4) = (uint16_t)motNo;
    *(uint16_t*)(layer + 6) = 0;
    *(float*)(layer + 0x4C) = frame;
}

// the plugin's test surface
struct Plugin {
    HMODULE h;
    void* (*Dti)();
    const void* const* (*ActionVtable)();
    uint32_t (*ActionSize)();
    int (*ProgramCount)();
    const char* (*ProgramName)(int);
    void (*SetTargets)(uintptr_t, uintptr_t, uintptr_t);
    int (*Counter)(void*, int);
    int (*Pc)(void*);
    int (*Spawns)();
} P;

template <class T> T& At(void* b, uint32_t off) { return *(T*)((uint8_t*)b + off); }

using FnSetActionExDti = void(__thiscall*)(void*, const void*, uint32_t, uint32_t, uint32_t, uint32_t, int32_t, int32_t,
                                           int32_t, int32_t, float, float, float, float, float, float, float, float);
using FnThis = void(__thiscall*)(void*);
using FnDtor = void*(__thiscall*)(void*, uint32_t);
using FnCheck = void(__thiscall*)(void*);

// Stands in for the game's uPlayer::checkAction (the plugin calls the original by address): it asks for
// g_fakeReq the way setAction does, or for nothing.
int32_t g_fakeReq = -1;
int g_gameChecks = 0;
void __fastcall FakeGameCheck(uint8_t* player, void*) {
    g_gameChecks++;
    if (g_fakeReq == -1) return;
    At<int32_t>(player, 0x2DD8) = g_fakeReq;
    At<uint8_t>(player, 0x2DD0) = 1;
}

uint32_t g_mgrPtr;
__declspec(naked) void CallUpdateNextAction() {
    __asm {
        push ebx
        push esi
        push edi
        push ebp
        mov edi, g_mgrPtr
        mov eax, UPDATE_NEXT_ACTION
        call eax
        pop ebp
        pop edi
        pop esi
        pop ebx
        ret
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
        return printf("SKIP: run me through compat_stub.exe (host at %p, 0x%X bytes)\n", (void*)host,
                      (unsigned)hostNt->OptionalHeader.SizeOfImage), false;
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

// A motion's event page: {bits, frames} spans after a 64-byte remap table (nMotion::SEQUENCE_INFO).
struct SeqInfo {
    uint16_t remap[32];
    uint32_t count;
    uint32_t* items;
};
uint32_t g_spans[] = {0, 27, 1u << 22, 1, 0, 3, 1u << 21, 1, 0, 21};  // bit 22 at frame 27, bit 21 at 31; 53 frames
SeqInfo g_seq = {{}, 5, g_spans};

uint8_t* NewPlayer(uint8_t* info) {
    uint8_t* p = (uint8_t*)_aligned_malloc(0x6000, 16);
    memset(p, 0, 0x6000);
    static void* vt[320];
    memcpy(vt, (const void*)PLAYER_VT, sizeof vt);
    vt[0x90 / 4] = (void*)&FakePlay;
    At<void*>(p, 0) = vt;
    At<float>(p, 0x14) = 1.0f;                 // frame step
    At<float>(p, 0x40) = 100.0f;               // position
    At<float>(p, 0x44) = 5.0f;
    At<float>(p, 0x48) = 50.0f;
    At<float>(p, 0xB0 + 0x20 + 8) = 1.0f;      // facing +Z
    At<void*>(p, 0xF0C) = p;                   // the model whose layers play is the player itself
    At<uint32_t>(p, 0x374) = 1;                // one motion layer
    At<void*>(p, 0x380 + 0x104) = &g_seq;      // layer 0, event page 1
    static uint8_t joints[0x200];
    memset(joints, 0xFF, sizeof joints);
    At<void*>(p, 0x368) = joints;
    static uint8_t ctl[0x200];
    At<void*>(p, 0x2E74) = ctl;
    At<void*>(p, 0x3348) = &g_params;
    At<void*>(p, 0x2DBC) = p;                  // the action manager's owner
    At<int32_t>(p, 0x2DD4) = 0;
    At<int32_t>(p, 0x2DD8) = -1;
    At<void*>(p, 0x3DEC) = info;
    At<uint8_t>(p, 0x206D) = 2;                // the Arisen
    return p;
}
}  // namespace

static int Run(int argc, wchar_t** argv);

extern "C" __declspec(dllexport) void HarnessMain() {
    int argc = 0;
    wchar_t** argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    int code = Run(argc, argv);
    fflush(stdout);
    ExitProcess((UINT)code);
}

static int Run(int argc, wchar_t** argv) {
    if (argc < 3) {
        printf("usage: compat_stub <DDDA.exe> <work folder>\n");
        return 1;
    }
    if (!MapImage(argv[1])) return 2;
    const wchar_t* work = argv[2];

    // the game's globals
    g_allocVt[0x1C / 4] = (void*)&FakeAlloc;
    g_allocVt[0x24 / 4] = (void*)&FakeFree;
    g_allocator.vtable = g_allocVt;
    At<void*>((void*)ALLOCATORS, ALLOC_INDEX * 4) = &g_allocator;
    g_mgrVt[0x30 / 4] = (void*)&FakeCreate;
    g_mgrVt[0x38 / 4] = (void*)&FakeRelease;
    g_mgr.vtable = g_mgrVt;
    At<void*>((void*)RES_MANAGER, 0) = &g_mgr;
    static uint8_t shellMgr[0x100];
    At<void*>((void*)SHELL_MANAGER, 0) = shellMgr;
    g_paramVt[0x3C / 4] = (void*)&FakeParam;
    g_params.vtable = g_paramVt;
    At<uint32_t>(g_shellParam, 4) = 33;  // axis, as the Alchemist's shells have it

    // the game has not built cPlAction::DTI yet: the plugin must wait for it
    SetEnvironmentVariableW(L"RIFTSTONE_COMPAT_HARNESS", L"1");
    SetEnvironmentVariableW(L"RIFTSTONE_COMPAT_ROOT", work);
    wchar_t asi[MAX_PATH];
    _snwprintf_s(asi, MAX_PATH, _TRUNCATE, L"%s\\compat.asi", work);
    uint32_t slotBefore = At<uint32_t>((void*)PLAYER_VT, 0x220);
    P.h = LoadLibraryW(asi);
    printf("plugin\n");
    Check(P.h != nullptr, "the plugin loads");
    if (!P.h) return 1;
#define GET(n) *(FARPROC*)&P.n = GetProcAddress(P.h, "CompatTest_" #n)
    GET(Dti); GET(ActionVtable); GET(ActionSize); GET(ProgramCount); GET(ProgramName); GET(SetTargets); GET(Counter);
    GET(Pc); GET(Spawns);
    uint32_t slotAfter = At<uint32_t>((void*)PLAYER_VT, 0x220);
    Check(slotBefore == 0x00B564E0 && slotAfter != slotBefore && slotAfter >= (uint32_t)(uintptr_t)P.h,
          "uPlayer::checkAction's vtable slot now points into the plugin");
    Check(P.ProgramCount() == 3 && strcmp(P.ProgramName(0), "test_wave") == 0 && strcmp(P.ProgramName(2), "overlay_only") == 0,
          "programs: 2 from compat.skills, 1 more from the overlay (its second test_wave dropped)");
    Check(P.Dti() == nullptr, "no class descriptor while the game's cPlAction::DTI does not exist yet");
    P.SetTargets((uintptr_t)&FakeCreateShl, (uintptr_t)&FakeSetEpv, (uintptr_t)&FakeSkillLevel);
    uint8_t* orig = (uint8_t*)0x00B564E0;  // the plugin verified these bytes; from here the stand-in answers
    orig[0] = 0xE9;
    At<int32_t>(orig, 1) = (int32_t)((uintptr_t)&FakeGameCheck - (0x00B564E0 + 5));

    // now "the game" builds cPlAction::DTI
    At<void*>((void*)PLACTION_DTI, 0) = (void*)PLACTION_DTI_VT;
    At<const char*>((void*)PLACTION_DTI, 4) = "cPlAction";
    At<uint32_t>((void*)PLACTION_DTI, 0x18) = (ALLOC_INDEX << 23) | 0x74 | (1u << 29);
    At<uint32_t>((void*)PLACTION_DTI, 0x1C) = PLACTION_ID;
    uint8_t* dti = (uint8_t*)P.Dti();
    printf("class descriptor\n");
    Check(dti != nullptr, "built once cPlAction::DTI exists");
    if (!dti) return 1;
    Check(strcmp(At<const char*>(dti, 4), "cPlActCompat") == 0 && At<uintptr_t>(dti, 0x10) == PLACTION_DTI,
          "named cPlActCompat, parent cPlAction");
    Check((At<uint32_t>(dti, 0x18) & 0x7FFFFF) == P.ActionSize() && (At<uint32_t>(dti, 0x18) >> 23) == ((ALLOC_INDEX) | (1u << 6)),
          Fmt("size %u, the same allocator and attributes as cPlAction", P.ActionSize()));
    const void* const* dvt = At<const void* const*>(dti, 0);
    Check(dvt[0] == ((void**)PLACTION_DTI_VT)[0] && dvt[2] == ((void**)PLACTION_DTI_VT)[2] && dvt[1] != ((void**)PLACTION_DTI_VT)[1],
          "its vtable is cPlAction::DTI's with its own newInstance");

    printf("setActionExDTI (the game's) makes the action\n");
    static uint8_t info[0x800];
    At<int32_t>(info, 0x270) = 210;  // main skill 1: Fire Ball's slot
    At<int32_t>(info, 0x274) = -1;
    At<int32_t>(info, 0x278) = -1;
    At<int32_t>(info, 0x27C) = -1;
    At<int32_t>(info, 0x280) = -1;
    At<int32_t>(info, 0x284) = -1;
    uint8_t* pl = NewPlayer(info);
    g_player = pl;
    static uint8_t pm[0xA00];
    At<void*>(pm, 0x99C) = pl;
    At<void*>((void*)PLAYER_MANAGER, 0) = pm;
    FnSetActionExDti setEx = (FnSetActionExDti)At<void**>(pl, 0)[0x21C / 4];
    setEx(pl, dti, 0, 6, 1, 0, 210, 0, 0, 0, 1.5f, 0, 0, 0, 0, 0, 0, 2.5f);
    uint8_t* act = At<uint8_t*>(pl, 0x2DCC);
    Check(act != nullptr && act == g_lastAlloc, "the pending action is the object the plugin allocated");
    Check(g_allocSize == P.ActionSize() && g_allocAlign == 16 && g_allocTag == PLACTION_ID,
          "allocated through cPlAction's allocator with its size, alignment and tag");
    Check(act && At<const void*>(act, 0) == P.ActionVtable(), "it carries the plugin's action vtable");
    Check(act && At<uint32_t>(act, 4) == 0 && At<uint32_t>(act, 8) == 6 && At<uint32_t>(act, 12) == 1 &&
              At<uint32_t>(act, 16) == 0 && At<int32_t>(act, 0x14) == 210,
          "u32 parameters (program, level, side, slot) at +4.. and s32 (skill) at +0x14");
    Check(act && At<float>(act, 0x24) == 1.5f && At<float>(act, 0x40) == 2.5f, "f32 parameters at +0x24..+0x40");
    Check(act && At<void*>(act, 0x44) == pl && At<void*>(act, 0x58) == pl, "cPlAction::setup gave it its owner");
    Check(At<int32_t>(pl, 0x2DD8) == -2 && At<uint8_t>(pl, 0x2DD0) == 1, "the request is 'the pending DTI action'");
    if (act) ((FnDtor)At<void**>(act, 0)[0])(act, 1);
    Check(g_lastFree == act, "a pending action the game drops is freed through the same allocator");
    At<void*>(pl, 0x2DCC) = nullptr;

    printf("a skill request becomes the program\n");
    FnCheck check = (FnCheck)At<void**>(pl, 0)[0x220 / 4];
    At<void*>(pl, 0x2DC8) = nullptr;
    At<uint8_t>(pl, 0x2DD0) = 0;
    At<int32_t>(pl, 0x2DD8) = -1;
    uint32_t fireBall = At<uint32_t>((void*)(SKILL_TABLE + 210 * 0x20), 4);
    g_fakeReq = (int32_t)fireBall;
    check(pl);
    g_fakeReq = -1;
    Check(g_gameChecks == 1, "the game's own checkAction ran first");
    act = At<uint8_t*>(pl, 0x2DCC);
    Check(At<int32_t>(pl, 0x2DD8) == -2 && act && At<const void*>(act, 0) == P.ActionVtable(),
          Fmt("a request for skill 210 (action 0x%08X) in main slot 1 became the program's action", fireBall));
    Check(act && At<uint32_t>(act, 4) == 0 && At<uint32_t>(act, 8) == 6 && At<uint32_t>(act, 12) == 1 &&
              At<uint32_t>(act, 16) == 0 && At<int32_t>(act, 0x14) == 210,
          "program 0, level 6 (level type 2 -> [levels] level2), main, slot 1, skill 210");
    Check(g_capSkill == 210 && g_capInfo == info + 0x72C, "getSkillLevelType got the skill in ESI and cPlayerInfo+0x72C in EDI");
    if (!act) return 1;

    printf("the action manager commits it (0x007F1EA0) and it runs\n");
    g_mgrPtr = (uint32_t)(uintptr_t)(pl + 0x2DB8);
    CallUpdateNextAction();
    Check(At<uint8_t*>(pl, 0x2DC8) == act && At<int32_t>(pl, 0x2DD4) == -2 && At<void*>(pl, 0x2DCC) == nullptr,
          "committed: mpAction is ours, mActionNo -2");
    Res* motions = Find("compat\\test\\m_test");
    Res* shells = Find("compat\\test\\shells");
    Check(motions && shells, "init loaded the program's motion list and shell list (the files exist)");
    Check(At<void*>(pl, 0xDD4 + 7 * 4) == motions && motions && At<int32_t>(motions, 0x48) == 2,
          "the motion list is bound to bank 7 (the game's setMotionList took a reference)");
    Check(g_epvPath == "compat\\test\\p_test" && g_epvModel == pl && g_epvSlot == 9,
          "setEpvResource got the path in EAX, the player and slot 9 on the stack");
    Check(g_plays.size() == 1 && g_plays[0].motNo == 0x764 && g_plays[0].forcible == 1 && g_plays[0].hokan == -1.0f &&
              g_plays[0].speed == 1.0f,
          "played 0x764 (forcible, default blend, speed 1)");
    Check(At<uint32_t>(pl, 0x273C) & 1, "the game's init and the program's setup ran (standing, in an attack)");

    const void* const* avt = At<const void* const*>(act, 0);
    FnThis move = (FnThis)avt[6];
    int fired1 = -1, fired0 = -1, shotFrame = -1;
    uint8_t* layer = pl + 0x380;
    for (int f = 1; f <= 60; f++) {
        At<float>(layer, 0x4C) = (float)f;
        move(act);
        if (fired1 < 0 && P.Counter(act, 1) > 0) fired1 = f;
        if (fired0 < 0 && g_plays.size() >= 2) fired0 = f;
        if (shotFrame < 0 && !g_shl.empty()) shotFrame = f;
        if (g_plays.size() >= 2) break;
    }
    Check(fired1 == 27, Fmt("counter 1 (page 1 bit 22) fired at frame 27 (got %d)", fired1));
    Check(fired0 == 31 && shotFrame == 31, Fmt("at counter 0 (bit 21, frame 31) it fired and played on (got %d/%d)", fired0, shotFrame));
    Check(g_plays.size() >= 2 && g_plays[1].motNo == 0x765 && g_plays[1].hokan == 5.0f, "then played 0x765 with blend 5");
    Check(!g_shl.empty() && g_shl[0].group == 8 && g_shl[0].index == 1 && g_shl[0].line == 0x12 && g_shl[0].owner == pl &&
              g_shl[0].parent == pl && g_shl[0].list == shells && g_shl[0].mgr == At<void*>((void*)SHELL_MANAGER, 0),
          "shot: createShlSub got group 8 in EAX, index 1 (level 6+), owner/parent the player, line 0x12");
    Res* col6 = Find("compat\\test\\col_lv06");
    if (!g_shl.empty()) {
        uint8_t* s = (uint8_t*)g_shl[0].shell;
        Check(col6 && At<void*>(s, 0x1D1C) == col6 && At<int32_t>(col6, 0x48) == 2,
              "the shell's collision is the level-6 file, with a reference of its own");
        Check(fabsf(At<float>(s, 0x40) - 100.0f) < 0.01f && fabsf(At<float>(s, 0x48) - 200.0f) < 0.01f &&
                  fabsf(At<float>(s, 0x25A8) - 1.0f) < 0.001f,
              "setShotCoord (the game's) placed it 150 cm ahead, facing +Z");
    }
    Check(P.Spawns() == 5, Fmt("the wave queued 5 shells at level 6 (got %d)", P.Spawns()));

    // Next frames: the wrapped checkAction lets the wave out.  On the first, the button is still held and
    // the game asks for the skill again: the program keeps running.
    size_t before = g_shl.size();
    std::vector<int> when;
    for (int f = 0; f < 70 && P.Spawns() > 0; f++) {
        size_t n = g_shl.size();
        g_fakeReq = f == 0 ? (int32_t)fireBall : -1;
        check(pl);
        if (f == 0)
            Check(At<uint8_t>(pl, 0x2DD0) == 0 && At<int32_t>(pl, 0x2DD8) == -1 && At<void*>(pl, 0x2DCC) == nullptr &&
                      At<uint8_t*>(pl, 0x2DC8) == act,
                  "asking for the same skill while it runs changes nothing");
        if (g_shl.size() > n) when.push_back(f);
    }
    g_fakeReq = -1;
    bool steps = g_shl.size() == before + 5;
    for (size_t k = 0; steps && k < 5; k++) {
        auto& c = g_shl[before + k];
        float z = At<float>(c.shell, 0x48);
        steps = c.group == 7 && c.index == 2 + k && fabsf(z - (50.0f + 200.0f * (k + 1))) < 0.01f;
    }
    Check(steps, "wave: group 7, shells 2..6, 200 cm apart along the facing");
    Check(when.size() == 5 && when[0] == 0 && when[1] - when[0] == 15 && when[4] - when[3] == 15,
          Fmt("wave: 15 frames apart (%d shells)", (int)when.size()));

    // the second motion runs to its end
    int ended = -1;
    for (int f = 1; f <= 80; f++) {
        At<float>(layer, 0x4C) = (float)f;
        if (f == 59) At<uint16_t>(layer, 6) = 1;  // the motion has ended
        move(act);
        if (At<uint8_t>(act, 0x50)) {
            ended = f;
            break;
        }
    }
    Check(ended == 59, Fmt("the action ends when 0x765 ends (frame %d)", ended));

    printf("the manager replaces it: final and the destructor (the game's)\n");
    int releases = g_releases;
    ((FnThis)avt[8])(act);
    Check(At<void*>(pl, 0xDD4 + 7 * 4) == nullptr && g_releases == releases + 1 && At<int32_t>(motions, 0x48) == 1,
          "cPlAction::final gave bank 7 back and released the list; the plugin keeps its own reference");
    ((FnDtor)avt[0])(act, 1);
    Check(g_lastFree == act, "the destructor freed it through the same allocator");

    printf("other requests\n");
    At<void*>(pl, 0x2DC8) = nullptr;
    At<void*>(pl, 0x2DCC) = nullptr;
    At<uint8_t>(pl, 0x2DD0) = 0;
    At<int32_t>(pl, 0x2DD8) = -1;
    g_fakeReq = 0x0F;  // evasion: not a skill
    check(pl);
    g_fakeReq = -1;
    Check(At<int32_t>(pl, 0x2DD8) == 0x0F && At<void*>(pl, 0x2DCC) == nullptr, "other requests pass through untouched");
    At<void*>(pm, 0x99C) = nullptr;
    At<uint8_t>(pl, 0x2DD0) = 0;
    At<int32_t>(pl, 0x2DD8) = -1;
    g_fakeReq = (int32_t)fireBall;
    check(pl);
    g_fakeReq = -1;
    Check(At<int32_t>(pl, 0x2DD8) == (int32_t)fireBall && At<void*>(pl, 0x2DCC) == nullptr,
          "a player who is not the Arisen keeps the Dark Arisen skill");

    printf(g_fails ? "\n%d check(s) FAILED\n" : "\nall checks passed\n", g_fails);
    return g_fails ? 1 : 0;
}

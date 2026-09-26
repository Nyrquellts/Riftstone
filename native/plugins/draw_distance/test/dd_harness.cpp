// dd_harness_core -- runs the draw_distance plugin inside the real game code, without the game.
//
//   dd_stub.exe <DDDA.exe> <draw_distance.asi> <profile>   (the stub loads this DLL and calls HarnessMain)
//
// Maps DDDA.exe's image at its fixed base (0x00400000) over the stub's image, loads the plugin (which
// verifies and patches the mapped code), compares every byte of the image with the copy taken before,
// and then runs the game's own code on fake objects, at ViewRange NORMAL, FAR and FARTHEST:
//   * sGameSys::setNoMoveDistance (0x0044D770, whole) for five stage rows, and the sGameSys
//     constructor's block (0x004387BD..): the three display radii and the unscaled size limit;
//   * uOmObjBase::after's pick of an object's radius (0x00C52B0F..) and uOmSetBase::after's
//     (0x00C55702..), then the tests that hide and stop an object beyond it on the overworld:
//     uOmObjBase::setRestrictDrawAndMove (0x00C51E60, whole) and uOmSetBase::move's (0x00C55417..);
//   * uEnemy::setup's distance block (0x00AA8A54..) for a 40 m kind, a never-hidden kind and a
//     placement with its own distance;
//   * the uCharacterBase constructor's default distance (0x0084A841..) and
//     uCharacterBase::checkMoveMode (0x00853DC0, whole): the move mode by distance;
//   * aStage::init's grass block (0x005038DB..): the fade band, and xmm0 still 1.0 after it (the
//     rest of aStage::init stores that register).
// A block run from its middle ends at the next game instruction, where the harness has put a jump to
// a capture routine.  Profiles (run_tests.py writes the matching draw_distance.ini):
//   shipped   the shipped ini: Objects 3, Grass 3, the rest the game's
//   people    Objects 0, Grass 0, Enemies 4, HumanEnemies 250
//   all       Objects 8, Grass 2, Enemies 6, HumanEnemies 400, ObjectsNeverHide 1, EnemiesAlwaysActive 1
//   clamp     Objects 500 (-> 20), Grass many (unreadable -> the game's), Enemies 0.5 (-> 1), HumanEnemies 99999 (-> 2000)
//   zero      every setting 0: nothing to patch
//   off       Enabled = 0: nothing patched
//   tampered  the shipped ini, but one byte of verified game code differs: refused, nothing written
// Prints "pass"/"FAIL" lines and exits 0 when everything passed, 1 on a failure, 2 when the image
// cannot be mapped here (reported as a skip by run_tests.py).
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shellapi.h>

#include <math.h>
#include <setjmp.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include <string>
#include <vector>

namespace {
constexpr uintptr_t BASE = 0x00400000;
constexpr uintptr_t CFG_GLOBAL = 0x018D1D20;      // the PC settings object; +0x38 = ViewRange
constexpr uintptr_t GAME_SYS = 0x018FA4BC;        // sGameSys: +0x34 mStageNo, +0xB876C the hour, +0xBE454.. radii
constexpr uintptr_t PLAYER_MANAGER = 0x018FA4EC;  // sPlayerManager: +0x99C the Arisen (position +0xE0)
constexpr uintptr_t SET_NO_MOVE = 0x0044D770, RESTRICT = 0x00C51E60, CHECK_MOVE_MODE = 0x00853DC0;
constexpr uintptr_t TABLE = 0x014F9768;           // uEnemy::mVisibleDistanceTable, 144 floats

struct Block {
    uintptr_t in, out;
};
constexpr Block CTOR = {0x004387BD, 0x00438848}, GRASS = {0x005038DB, 0x0050392C}, ENEMY = {0x00AA8A54, 0x00AA8AF5},
                HUMAN = {0x0084A841, 0x0084A851}, OM_PICK = {0x00C52B0F, 0x00C52B9B}, SET_PICK = {0x00C55702, 0x00C5573F},
                SET_TEST = {0x00C55417, 0x00C554A7};

// Every byte the plugin may write, with the game's own bytes.
struct Site {
    const char* what;
    uintptr_t at;
    uint8_t len;
    uint8_t game[4];
};
const Site OBJ_JNE[2] = {{"setNoMoveDistance jne", 0x0044D888, 2, {0x75, 0x0A}},
                         {"sGameSys constructor jne", 0x004387D0, 2, {0x75, 0x0A}}};
const Site OBJ_OPERAND[2] = {{"setNoMoveDistance 3.0 load", 0x0044D88E, 4, {0x40, 0x01, 0x52, 0x01}},
                             {"sGameSys constructor 3.0 load", 0x004387D6, 4, {0x40, 0x01, 0x52, 0x01}}};
const Site GRASS_JNE = {"aStage::init grass jne", 0x005038F6, 2, {0x75, 0x0A}};
const Site GRASS_OPERAND = {"aStage::init grass 3.0 load", 0x005038FC, 4, {0x40, 0x01, 0x52, 0x01}};
const Site ENEMY_JNE = {"uEnemy::setup jne", 0x00AA8A74, 2, {0x75, 0x0A}};
const Site ENEMY_OPERAND = {"uEnemy::setup 3.0 load", 0x00AA8A7A, 4, {0x40, 0x01, 0x52, 0x01}};
const Site HUMAN_OPERAND = {"uCharacterBase constructor 10000.0 load", 0x0084A845, 4, {0xA8, 0x0B, 0x52, 0x01}};
const Site NEVER_HIDE[2] = {{"setRestrictDrawAndMove jbe", 0x00C51F09, 2, {0x76, 0x07}},
                            {"uOmSetBase::move jbe", 0x00C55499, 2, {0x76, 0x0C}}};
const Site ALWAYS = {"checkMoveMode jbe", 0x00853F25, 2, {0x76, 0x04}};

struct Want {
    const char* name;
    double objects, grass, enemies, humanM;  // 0: the game's own
    bool neverHide, alwaysActive, patched;
};
const Want PROFILES[] = {
    {"shipped", 3, 3, 0, 0, false, false, true},
    {"people", 0, 0, 4, 250, false, false, true},
    {"all", 8, 2, 6, 400, true, true, true},
    {"clamp", 20, 0, 1, 2000, false, false, true},
    {"zero", 0, 0, 0, 0, false, false, false},
    {"off", 0, 0, 0, 0, false, false, false},
    {"tampered", 0, 0, 0, 0, false, false, false},
};
Want W;

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
bool Same(double a, double b) { return a == b || fabs(a - b) <= 1e-6 * fabs(b); }

// ---- the mapped image -------------------------------------------------------------------------
uint32_t g_imageSize = 0;
std::vector<uint8_t> g_snap;

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
        return printf("SKIP: run me through dd_stub.exe (host at %p, 0x%X bytes)\n", (void*)host,
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
    g_imageSize = need;
    return true;
}

void Snapshot() { g_snap.assign((const uint8_t*)BASE, (const uint8_t*)BASE + g_imageSize); }

std::vector<uintptr_t> Changed() {
    std::vector<uintptr_t> out;
    const uint8_t* now = (const uint8_t*)BASE;
    for (uint32_t i = 0; i < g_imageSize; i++)
        if (now[i] != g_snap[i]) out.push_back(BASE + i);
    return out;
}

bool Is(const Site& s, const uint8_t* bytes) { return memcmp((const void*)s.at, bytes, s.len) == 0; }
bool Vanilla(const Site& s) { return Is(s, s.game); }

// The float a patched load reads, if its operand points into the plugin's module.
HMODULE g_plugin = nullptr;
bool InPlugin(uintptr_t p) {
    auto* dos = (const IMAGE_DOS_HEADER*)g_plugin;
    auto* nt = (const IMAGE_NT_HEADERS32*)((const uint8_t*)g_plugin + dos->e_lfanew);
    return p >= (uintptr_t)g_plugin && p + 4 <= (uintptr_t)g_plugin + nt->OptionalHeader.SizeOfImage;
}
bool Reads(const Site& s, double value) {
    uint32_t p = *(const uint32_t*)s.at;
    return InPlugin(p) && *(const float*)(uintptr_t)p == (float)value;
}

// ---- the fake world ---------------------------------------------------------------------------
uint8_t* g_cfg;     // settings object
uint8_t* g_sys;     // sGameSys (0xBE464 bytes and more)
uint8_t* g_pm;      // sPlayerManager
uint8_t* g_arisen;  // the Arisen: position at +0xE0
uint8_t* g_obj;     // a uOmObjBase
uint8_t* g_set;     // a uOmSetBase
uint8_t* g_chr;     // a uCharacterBase (enemy or human enemy)
uint8_t* g_grass;   // sGrass

uint8_t* Alloc(size_t n) { return (uint8_t*)VirtualAlloc(nullptr, n, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE); }
template <class T> T& At(uint8_t* p, uint32_t off) { return *(T*)(p + off); }

void SetupWorld() {
    g_cfg = Alloc(0x1000);
    g_sys = Alloc(0x100000);
    g_pm = Alloc(0x1000);
    g_arisen = Alloc(0x1000);
    g_obj = Alloc(0x4000);
    g_set = Alloc(0x1000);
    g_chr = Alloc(0x8000);
    g_grass = Alloc(0x1000);
    *(uint32_t*)CFG_GLOBAL = (uint32_t)(uintptr_t)g_cfg;
    *(uint32_t*)GAME_SYS = (uint32_t)(uintptr_t)g_sys;
    *(uint32_t*)PLAYER_MANAGER = (uint32_t)(uintptr_t)g_pm;
    At<uint32_t>(g_pm, 0x99C) = (uint32_t)(uintptr_t)g_arisen;
    At<uint32_t>(g_sys, 0xB876C) = 0x20;  // no hour: the object's hour check has nothing to read
}
void SetViewRange(uint32_t vr) { At<uint32_t>(g_cfg, 0x38) = vr; }
void SetStage(int32_t stage) { At<int32_t>(g_sys, 0x34) = stage; }
void PlaceArisen(float x) {  // the thing measured from stands at the origin; the Arisen x cm along X
    At<float>(g_arisen, 0xE0) = x;
    At<float>(g_arisen, 0xE4) = 0;
    At<float>(g_arisen, 0xE8) = 0;
    At<float>(g_arisen, 0xEC) = 1;
}

// ---- calling the game -------------------------------------------------------------------------
uint32_t g_fn, g_a, g_d, g_s;
__declspec(naked) void CallEaxEdxAsm() {  // setNoMoveDistance: eax = the stage, edx = sGameSys
    __asm {
        push ebx
        push esi
        push edi
        push ebp
        mov eax, g_a
        mov edx, g_d
        call dword ptr [g_fn]
        pop ebp
        pop edi
        pop esi
        pop ebx
        ret
    }
}
__declspec(naked) void CallEsiAsm() {  // checkMoveMode: esi = the character; setRestrictDrawAndMove: eax = the object
    __asm {
        push ebx
        push esi
        push edi
        push ebp
        mov eax, g_a
        mov esi, g_s
        call dword ptr [g_fn]
        pop ebp
        pop edi
        pop esi
        pop ebx
        ret
    }
}
void SetNoMove(int32_t stage) {
    g_fn = SET_NO_MOVE;
    g_a = (uint32_t)stage;
    g_d = (uint32_t)(uintptr_t)g_sys;
    CallEaxEdxAsm();
}
void Restrict(uint8_t* obj) {
    g_fn = RESTRICT;
    g_a = (uint32_t)(uintptr_t)obj;
    g_s = 0x5E5E5E5E;
    CallEsiAsm();
}
void CheckMoveMode(uint8_t* chr) {
    g_fn = CHECK_MOVE_MODE;
    g_a = 0x5A5A5A5A;
    g_s = (uint32_t)(uintptr_t)chr;
    CallEsiAsm();
}

// ---- entering a block in the middle ------------------------------------------------------------
jmp_buf g_back;
uint32_t g_in, g_eax, g_ebx, g_ecx, g_esi, g_edi, g_ebp;
uint8_t g_byte0F;
alignas(16) float g_xmm0Out[4];

void __cdecl CaptureC() { longjmp(g_back, 1); }

// Placed by the harness at each block's next game instruction.
__declspec(naked) void Capture() {
    __asm {
        movups xmmword ptr g_xmm0Out, xmm0
        call CaptureC
    }
}

__declspec(naked) void JumpIn() {
    __asm {
        sub esp, 0x400
        mov al, g_byte0F
        mov byte ptr [esp + 0xF], al
        mov eax, g_eax
        mov ebx, g_ebx
        mov ecx, g_ecx
        mov edx, 0x77777777
        mov esi, g_esi
        mov edi, g_edi
        mov ebp, g_ebp
        jmp dword ptr [g_in]
    }
}

#pragma warning(push)
#pragma warning(disable : 4611)
void Enter(uintptr_t in) {
    g_in = (uint32_t)in;
    memset(g_xmm0Out, 0, sizeof g_xmm0Out);
    if (setjmp(g_back) == 0) JumpIn();
}
#pragma warning(pop)

void PlaceCapture(uintptr_t at) {
    uint8_t* p = (uint8_t*)at;
    int32_t rel = (int32_t)((uintptr_t)Capture - (at + 5));
    p[0] = 0xE9;
    memcpy(p + 1, &rel, 4);
}

void Regs(uint32_t eax, uint32_t ebx, uint32_t ecx, uint32_t esi, uint32_t edi, uint32_t ebp, uint8_t byte0F = 0) {
    g_eax = eax, g_ebx = ebx, g_ecx = ecx, g_esi = esi, g_edi = edi, g_ebp = ebp, g_byte0F = byte0F;
}
uint32_t U(void* p) { return (uint32_t)(uintptr_t)p; }

// ---- what the settings should give -----------------------------------------------------------
double Mul(double setting, uint32_t vr) { return setting > 0 ? setting : (double)vr; }
const char* VR[4] = {"", "NORMAL", "FAR", "FARTHEST"};

// ---- the checks -------------------------------------------------------------------------------
void CheckSites(const std::vector<uintptr_t>& changed) {
    // Which bytes may differ, and what each site must hold now.
    std::vector<uintptr_t> allowed;
    auto allow = [&](const Site& s) {
        for (uint8_t i = 0; i < s.len; i++) allowed.push_back(s.at + i);
    };
    const uint8_t nops[2] = {0x90, 0x90};
    bool ok = true;
    for (int i = 0; i < 2; i++) {
        if (W.objects > 0) {
            allow(OBJ_JNE[i]);
            allow(OBJ_OPERAND[i]);
            ok &= Is(OBJ_JNE[i], nops) && Reads(OBJ_OPERAND[i], W.objects);
        } else {
            ok &= Vanilla(OBJ_JNE[i]) && Vanilla(OBJ_OPERAND[i]);
        }
    }
    Check(ok, W.objects > 0 ? F("objects: both jne are nops and both 3.0 loads read the plugin's %g", W.objects)
                            : "objects: both multiplier blocks as shipped");
    if (W.grass > 0) {
        allow(GRASS_JNE);
        allow(GRASS_OPERAND);
        Check(Is(GRASS_JNE, nops) && Reads(GRASS_OPERAND, W.grass), F("grass: the jne is nops, the 3.0 load reads %g", W.grass));
    } else {
        Check(Vanilla(GRASS_JNE) && Vanilla(GRASS_OPERAND), "grass: aStage::init's block as shipped");
    }
    if (W.enemies > 0) {
        allow(ENEMY_JNE);
        allow(ENEMY_OPERAND);
        Check(Is(ENEMY_JNE, nops) && Reads(ENEMY_OPERAND, W.enemies), F("enemies: the jne is nops, the 3.0 load reads %g", W.enemies));
    } else {
        Check(Vanilla(ENEMY_JNE) && Vanilla(ENEMY_OPERAND), "enemies: uEnemy::setup's block as shipped");
    }
    if (W.humanM > 0) {
        allow(HUMAN_OPERAND);
        Check(Reads(HUMAN_OPERAND, W.humanM * 100), F("human enemies: the constructor's 10000.0 load reads %g", W.humanM * 100));
    } else {
        Check(Vanilla(HUMAN_OPERAND), "human enemies: the constructor's 10000.0 load as shipped");
    }
    const uint8_t jmp07[2] = {0xEB, 0x07}, jmp0C[2] = {0xEB, 0x0C};
    if (W.neverHide) {
        allow(NEVER_HIDE[0]);
        allow(NEVER_HIDE[1]);
        Check(Is(NEVER_HIDE[0], jmp07) && Is(NEVER_HIDE[1], jmp0C), "objects never hide: both display-radius jbe are jmp");
    } else {
        Check(Vanilla(NEVER_HIDE[0]) && Vanilla(NEVER_HIDE[1]), "both display-radius tests as shipped");
    }
    if (W.alwaysActive) {
        allow(ALWAYS);
        Check(Is(ALWAYS, nops), "enemies always active: checkMoveMode's jbe is nops");
    } else {
        Check(Vanilla(ALWAYS), "checkMoveMode's distance test as shipped");
    }
    size_t stray = 0;
    for (uintptr_t a : changed) {
        bool mine = false;
        for (uintptr_t x : allowed) mine |= x == a;
        if (!mine) {
            if (stray < 5) printf("       unexpected change at 0x%08X\n", (unsigned)a);
            stray++;
        }
    }
    Check(stray == 0, F("no other byte of the image changed (%zu bytes changed in all)", changed.size()));
    Check(*(const float*)0x01520140 == 3.0f && *(const float*)0x01520BA8 == 10000.0f,
          "the shared 3.0 (234 readers) and 10000.0 are never written");
}

struct Row {
    int32_t stage;
    float r0, r1, r2;
};
const Row ROWS[] = {{100, 1500, 2500, 100000}, {220, 1000, 2000, 10000}, {205, 1500, 2500, 100000},
                    {605, 4500, 5000, 100000}, {300, 2000, 3000, 100000}};

void CheckRadii() {
    printf("sGameSys::setNoMoveDistance (0x0044D770, the whole function) and the constructor's defaults\n");
    for (uint32_t vr = 1; vr <= 3; vr++) {
        SetViewRange(vr);
        double m = Mul(W.objects, vr);
        bool ok = true;
        for (const Row& r : ROWS) {
            memset(g_sys + 0xBE454, 0, 16);
            SetNoMove(r.stage);
            ok &= Same(At<float>(g_sys, 0xBE454), r.r0 * m) && Same(At<float>(g_sys, 0xBE458), r.r1 * m) &&
                  Same(At<float>(g_sys, 0xBE45C), r.r2 * m) && At<float>(g_sys, 0xBE460) == 300.0f;
        }
        Check(ok, F("%s: every stage row x%g (overworld %g / %g / %g m), the size limit 3 m unscaled", VR[vr], m,
                    15 * m, 25 * m, 1000 * m));
        memset(g_sys + 0xBE454, 0, 16);
        Regs(0x11111111, 0x22222222, 0x33333333, 0x44444444, 0x55555555, U(g_sys));
        Enter(CTOR.in);
        Check(Same(At<float>(g_sys, 0xBE454), 1500 * m) && Same(At<float>(g_sys, 0xBE458), 2500 * m) &&
                  Same(At<float>(g_sys, 0xBE45C), 100000 * m) && At<float>(g_sys, 0xBE460) == 300.0f,
              F("%s: the sGameSys constructor's defaults x%g", VR[vr], m));
    }
}

// uOmObjBase::after's pick (0x00C52B0F..0x00C52B9B) for an object; returns mDispRadius (+0x2928).
float OmPick(uint32_t flags, float radius) {
    memset(g_obj, 0, 0x4000);
    At<uint32_t>(g_obj, 0x25AC) = flags;
    At<float>(g_obj, 0x16C) = radius;
    Regs(0x11111111, 0x22222222, 0x33333333, 0x44444444, 0x55555555, U(g_obj));
    Enter(OM_PICK.in);
    return At<float>(g_obj, 0x2928);
}
float SetPick(float radius) {
    memset(g_set, 0, 0x1000);
    At<float>(g_set, 0x16C) = radius;
    Regs(0x11111111, 0x22222222, 0x33333333, U(g_set), 0x55555555, 0x66666666);
    Enter(SET_PICK.in);
    return At<float>(g_set, 0x430);
}

// setRestrictDrawAndMove on an object whose display radius is `radius`, with the Arisen `dist` away:
// true = still drawDistance (drawn and updated).
bool OmShown(float radius, float dist) {
    memset(g_obj, 0, 0x4000);
    At<uint8_t>(g_obj, 0x25F0) = 0x80;   // the radius was picked
    At<uint8_t>(g_obj, 0x29A6) = 0x01;   // mCommand.enableLengthCheck; no zone check
    At<uint8_t>(g_obj, 0x2627) = 0x02;   // mOmRest.drawDistance, as move() sets it each frame
    At<float>(g_obj, 0x2928) = radius;
    PlaceArisen(dist);
    Restrict(g_obj);
    return (At<uint8_t>(g_obj, 0x2627) & 0x02) != 0;
}
// uOmSetBase::move's test (0x00C55417..0x00C554A7).
bool SetShown(float radius, float dist) {
    memset(g_set, 0, 0x1000);
    At<uint8_t>(g_set, 0x448) = 0x02;
    At<uint8_t>(g_set, 0x449) = 0x14;
    At<float>(g_set, 0x430) = radius;
    PlaceArisen(dist);
    Regs(0x14, 1, 0x33333333, U(g_set), U(g_sys), 0x66666666, 0x02);
    Enter(SET_TEST.in);
    return (At<uint8_t>(g_set, 0x448) & 0x02) != 0;
}

void CheckObjects() {
    printf("objects on the overworld: the radius each takes (uOmObjBase::after, uOmSetBase::after), then the\n"
           "hide-and-stop tests (setRestrictDrawAndMove 0x00C51E60 whole, uOmSetBase::move's)\n");
    for (uint32_t vr = 1; vr <= 3; vr++) {
        SetViewRange(vr);
        SetStage(100);
        SetNoMove(100);
        double m = Mul(W.objects, vr);
        float small = OmPick(0, 200.0f), mid = OmPick(0, 290.0f), big = OmPick(0, 500.0f), flagged = OmPick(0x800, 500.0f);
        Check(Same(small, 2500 * m) && Same(mid, 2500 * m) && Same(big, 100000 * m) && Same(flagged, 1500 * m),
              F("%s: a 2 m object takes %g m, 2.9 m %g m, 5 m %g m, a flagged one %g m", VR[vr], small / 100, mid / 100,
                big / 100, flagged / 100));
        Check(Same(SetPick(200.0f), 2500 * m) && Same(SetPick(500.0f), 100000 * m),
              F("%s: set objects (uOmSetBase) take the same radii", VR[vr]));
        // An object 1.2 times the game's own radius away: the game hides it; with the plugin it shows
        // when the new radius reaches that far (or never-hide is on).
        const float dists[3] = {0.8f * small, 1.25f * small, 1.2f * 2500.0f * vr};
        for (int i = 0; i < 3; i++) {
            float d = dists[i];
            bool want = W.neverHide || d <= small;
            Check(OmShown(small, d) == want && SetShown(small, d) == want,
                  F("%s: a small object %.0f m away is %s (radius %g m%s)", VR[vr], d / 100, want ? "shown" : "hidden",
                    small / 100, i == 2 ? F("; the game alone hides it beyond %d m", 25 * (int)vr).c_str() : ""));
        }
    }
    SetStage(200);
    Check(OmShown(2500, 1e6f) && SetShown(2500, 1e6f), "off the overworld (stage 200) the game never hides an object by distance");
    SetStage(100);
}

// uEnemy::setup's block for an enemy of table entry `kind`; placed = the placement's own distance or -1.
void EnemySetup(uint8_t kind, float placed) {
    memset(g_chr, 0, 0x8000);
    At<uint8_t>(g_chr, 0x2D) = kind;
    At<float>(g_chr, 0x2994) = placed;
    Regs(0x11111111, 0x22222222, 0x33333333, 0x44444444, 0x55555555, U(g_chr));
    Enter(ENEMY.in);
}

// checkMoveMode with the Arisen `dist` away from a character whose distances are set.
uint32_t Mode(float draw, float manage, float dist) {
    At<uint32_t>(g_chr, 0x2974) = 1;       // mCheckMoveModeType: by distance (enemies, human enemies)
    At<uint32_t>(g_chr, 0x30) = 0;
    At<float>(g_chr, 0x2984) = draw;
    At<float>(g_chr, 0x2988) = manage;
    At<float>(g_chr, 0xE0) = 0;
    At<float>(g_chr, 0xE4) = 0;
    At<float>(g_chr, 0xE8) = 0;
    At<uint32_t>(g_chr, 0x297C) = 7;
    PlaceArisen(dist);
    CheckMoveMode(g_chr);
    return At<uint32_t>(g_chr, 0x297C);
}

void CheckEnemies() {
    printf("uEnemy::setup's distances (0x00AA8A54..0x00AA8AF5) and uCharacterBase::checkMoveMode (0x00853DC0 whole)\n");
    SetStage(200);  // off the overworld: checkMoveMode skips the loaded-cell test
    const float* table = (const float*)TABLE;
    int k40 = -1, kMax = -1;
    for (int i = 0; i < 144; i++) {
        if (k40 < 0 && table[i] == 4000.0f) k40 = i;
        if (kMax < 0 && table[i] > 1e30f) kMax = i;
    }
    Check(k40 >= 0 && kMax >= 0, F("the kind table holds a 40 m kind (%d) and a never-frozen kind (%d)", k40, kMax));
    if (k40 < 0 || kMax < 0) return;
    for (uint32_t vr = 1; vr <= 3; vr++) {
        SetViewRange(vr);
        double m = Mul(W.enemies, vr);
        EnemySetup((uint8_t)k40, -1.0f);
        float draw = At<float>(g_chr, 0x2984), manage = At<float>(g_chr, 0x2988);
        Check(Same(draw, 4000 * m) && Same(manage, 4000 * m + 20000),
              F("%s: a 40 m kind is fully active to %g m, moving to %g m", VR[vr], draw / 100, manage / 100));
        EnemySetup((uint8_t)kMax, -1.0f);
        Check(At<float>(g_chr, 0x2984) > 1e30f, F("%s: a never-frozen kind stays never frozen", VR[vr]));
        EnemySetup((uint8_t)k40, 1234.0f);
        Check(At<float>(g_chr, 0x2984) == 1234.0f, F("%s: a placement's own distance (12.34 m) is kept", VR[vr]));
        if (vr == 1) {
            float d0 = draw * 0.5f, d1 = draw + 100, d2 = manage + 100;
            uint32_t a = Mode(draw, manage, d0), b = Mode(draw, manage, d1), c = Mode(draw, manage, d2);
            bool ok = W.alwaysActive ? (a == 0 && b == 0 && c == 0) : (a == 0 && b == 1 && c == 2);
            Check(ok, F("NORMAL: at %.0f / %.0f / %.0f m the move mode is %u / %u / %u (%s)", d0 / 100, d1 / 100, d2 / 100, a, b, c,
                        W.alwaysActive ? "always active: 0 everywhere" : "0 active, 1 moving, 2 frozen"));
        }
    }
    printf("the uCharacterBase constructor's default (0x0084A841..0x0084A851): human enemies\n");
    memset(g_chr, 0, 0x8000);
    Regs(0x11111111, U(g_chr), 0x33333333, 0x44444444, 0x55555555, 0x66666666);
    Enter(HUMAN.in);
    float human = At<float>(g_chr, 0x2984), want = (float)(W.humanM > 0 ? W.humanM * 100 : 10000);
    Check(human == want, F("a new character's distance is %g m (human enemies keep it; enemies replace it)", human / 100));
    // uNpc::setup makes the manage distance the draw distance + 200 m.
    float d1 = human + 100, d2 = human + 20000 + 100;
    uint32_t a = Mode(human, human + 20000, human * 0.5f), b = Mode(human, human + 20000, d1),
             c = Mode(human, human + 20000, d2);
    bool ok = W.alwaysActive ? (a == 0 && b == 0 && c == 0) : (a == 0 && b == 1 && c == 2);
    Check(ok, F("a human enemy at %.0f / %.0f / %.0f m: move mode %u / %u / %u (%s)", human * 0.005f, d1 / 100, d2 / 100, a, b,
                c, W.alwaysActive ? "always active: 0 everywhere" : "mode 2 hides and stops it"));
    SetStage(100);
}

void CheckGrass() {
    printf("aStage::init's grass block (0x005038DB..0x0050392C)\n");
    for (uint32_t vr = 1; vr <= 3; vr++) {
        SetViewRange(vr);
        double m = Mul(W.grass, vr);
        memset(g_grass, 0, 0x1000);
        Regs(U(g_grass), 0x22222222, 0x33333333, 0x44444444, 0x55555555, 0x66666666);
        Enter(GRASS.in);
        Check(At<uint8_t>(g_grass, 0x50) == 1 && Same(At<float>(g_grass, 0x54), 1700 * m) && Same(At<float>(g_grass, 0x58), 7000 * m),
              F("%s: grass fades from %g to %g m (forced)", VR[vr], 17 * m, 70 * m));
        Check(g_xmm0Out[0] == 1.0f, F("%s: xmm0 still holds the game's 1.0 for the rest of aStage::init", VR[vr]));
    }
}
}  // namespace

static int Run(int argc, wchar_t** argv);

// Called by dd_stub.exe.  Never returns: the stub's code is gone once DDDA.exe is mapped.
extern "C" __declspec(dllexport) void HarnessMain() {
    int argc = 0;
    wchar_t** argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    int code = Run(argc, argv);
    fflush(stdout);
    ExitProcess((UINT)code);
}

static int Run(int argc, wchar_t** argv) {
    if (argc < 4) {
        printf("usage: dd_stub <DDDA.exe> <draw_distance.asi> <profile>\n");
        return 1;
    }
    char profile[32];
    _snprintf_s(profile, sizeof profile, _TRUNCATE, "%S", argv[3]);
    bool found = false;
    for (const Want& w : PROFILES)
        if (strcmp(w.name, profile) == 0) W = w, found = true;
    if (!found) return printf("unknown profile %s\n", profile), 1;
    if (!MapImage(argv[1])) return 2;

    bool shipped = true;
    for (const Site* s : {&OBJ_JNE[0], &OBJ_JNE[1], &OBJ_OPERAND[0], &OBJ_OPERAND[1], &GRASS_JNE, &GRASS_OPERAND, &ENEMY_JNE,
                          &ENEMY_OPERAND, &HUMAN_OPERAND, &NEVER_HIDE[0], &NEVER_HIDE[1], &ALWAYS})
        shipped &= Vanilla(*s);
    Check(shipped, "the mapped DDDA.exe has all twelve sites as shipped");
    if (strcmp(profile, "tampered") == 0) {
        *(uint8_t*)0x0044D896 = 0x04;  // `cmp ecx, 2` -> `cmp ecx, 4`, inside a verified block, not a site
        printf("tampered: 0x0044D896 02 -> 04 before the plugin loads\n");
    }
    Snapshot();

    SetEnvironmentVariableW(L"RIFTSTONE_DRAW_HARNESS", L"1");
    g_plugin = LoadLibraryW(argv[2]);
    printf("plugin (profile %s)\n", profile);
    Check(g_plugin != nullptr, "the plugin loads");
    if (!g_plugin) return 1;
    std::vector<uintptr_t> changed = Changed();
    if (!W.patched) {
        Check(changed.empty(), "nothing in the image changed");
        if (strcmp(profile, "tampered") == 0 || g_fails) {
            printf(g_fails ? "\n%d check(s) FAILED\n" : "\nall checks passed\n", g_fails);
            return g_fails ? 1 : 0;
        }
        printf("the game's own code, unchanged: every distance as the game sets it\n");
    } else {
        CheckSites(changed);
        // A second copy of the plugin (no ini of its own: the defaults) must see the patched code and
        // leave it alone.
        Snapshot();
        wchar_t copy[MAX_PATH];
        wcscpy_s(copy, argv[2]);
        wchar_t* dot = wcsrchr(copy, L'.');
        if (dot) *dot = 0;
        wcscat_s(copy, L"_copy.asi");
        if (CopyFileW(argv[2], copy, FALSE)) {
            HMODULE second = LoadLibraryW(copy);
            Check(second != nullptr && Changed().empty(), "a second copy refuses the patched code and changes nothing");
        }
        if (g_fails) return 1;
    }

    SetupWorld();
    for (Block b : {CTOR, GRASS, ENEMY, HUMAN, OM_PICK, SET_PICK, SET_TEST}) PlaceCapture(b.out);
    CheckRadii();
    CheckObjects();
    CheckEnemies();
    CheckGrass();
    printf(g_fails ? "\n%d check(s) FAILED\n" : "\nall checks passed\n", g_fails);
    return g_fails ? 1 : 0;
}

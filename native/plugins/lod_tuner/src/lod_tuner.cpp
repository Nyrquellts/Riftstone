// lod_tuner -- Riftstone native plugin: model LOD distances that suit a modern screen.
//
// What the game does
//   Every model file (.mod) carries two switch distances, rModel::MODEL_INFO {s32 middist, lowdist},
//   in engine units (centimetres).  rModel::load (0x00FA8FE0) copies them into the model at +0xE0
//   (0x00FA91E4) and then reads the primitives, each with a LOD mask (1 HIGH, 2 MEDIUM, 4 LOW).  The
//   draw paths (uModel, uBaseModel, uModelSymmetry, uSwingModel, uSwingJointModel::drawModel) pick
//   HIGH up to middist, MEDIUM up to lowdist and LOW beyond, after multiplying both by 1 or 2 for
//   config.ini ViewRange=NORMAL or FAR; FARTHEST forces HIGH.  Instanced vegetation reads the same
//   pair through cInstancingCulling::updateLODParam (0x0113D430) and adds its billboard distance,
//   whatever ViewRange says.  The pairs were tuned for 720p consoles: half of the scenery models
//   switch while they are still about 240 pixels tall on a 720-pixel screen (tools/lod_survey.py).
//
// What this plugin does
//   One jmp in rModel::load right after the primitives are read (0x00FA9479).  The thunk rescales the
//   model's pair once and resumes, so nothing runs per frame and nothing more is loaded (every LOD
//   mesh is already in the .mod).  Scenery (paths scr\... and model\om\...) keeps its detail until it
//   is about PopPixels tall on screen, and gets at least Scale times its vanilla distance.  A model
//   with nothing to draw at HIGH (a far-only stand-in) stays vanilla.  Everything else (characters,
//   enemies, weapons, effects) is multiplied by Characters, 1.0 = vanilla, because their pair also
//   sets cloth-simulation detail (uSimSoftBody::getTargetLODLevel).  A distance is never lowered and
//   never pushed past MaxDistance.  Settings: lod_tuner.ini next to this file.
//
// Safety
//   DDDA.exe build 2364871 only.  The patched bytes, the code around them, rModel's vtable and two
//   of the readers are compared first; on any difference nothing is patched and
//   riftstone\logs\lod_tuner.log says why.  Original code; no third-party source.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <math.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "ninput.h"  // optional Ninput host integration; this plugin still runs standalone

namespace {

// ---- build 2364871 --------------------------------------------------------------------------
constexpr uintptr_t IMAGE_BASE = 0x00400000;

// rModel::load after the primitive loop: mov ecx, [esp+0x2C]; mov edx, [esi+0x78]
constexpr uintptr_t SITE = 0x00FA9479;
constexpr uintptr_t BACK = 0x00FA9480;
const uint8_t SITE_BYTES[7] = {0x8B, 0x4C, 0x24, 0x2C, 0x8B, 0x56, 0x78};

struct Expect {
    const char* what;
    uintptr_t at;
    uint8_t bytes[12];
    uint8_t len;
};
const Expect CONTEXT[] = {
    {"rModel::load header check 'MOD\\0'", 0x00FA902B, {0x81, 0x7C, 0x24, 0x30, 0x4D, 0x4F, 0x44, 0x00}, 8},
    {"rModel::load version check 0xD4", 0x00FA9049, {0xBA, 0xD4, 0x00, 0x00, 0x00, 0x66, 0x39, 0x54, 0x24, 0x34}, 10},
    {"rModel::load stores MODEL_INFO at +0xE0", 0x00FA91E4, {0x66, 0x0F, 0xD6, 0x86, 0xE0, 0x00, 0x00, 0x00}, 8},
    {"rModel::load reads 48-byte primitives to +0x70/+0x74", 0x00FA93F8,
     {0x8B, 0x46, 0x74, 0x8D, 0x14, 0x40, 0x8B, 0x46, 0x70, 0xC1, 0xE2, 0x04}, 12},
    {"rModel::load primitive loop", 0x00FA9471, {0x83, 0xC7, 0x30, 0x3B, 0x4E, 0x74, 0x72, 0xA7}, 8},
    {"rModel::load after the site", 0x00FA9480, {0x51, 0x52, 0x8D, 0x4C, 0x24, 0x1C}, 6},
    {"uModel::drawModel reads lowdist x ViewRange", 0x00FFC525, {0x8B, 0x91, 0xE4, 0x00, 0x00, 0x00, 0x0F, 0xAF, 0xD0}, 9},
    {"cInstancingCulling::updateLODParam", 0x0113D430, {0x8B, 0x44, 0x24, 0x04, 0x0F, 0x57, 0xC0, 0xF3, 0x0F, 0x11, 0x41, 0x08}, 12},
};

constexpr uintptr_t RMODEL_VTABLE = 0x01438618;
constexpr uint32_t SLOT_LOAD = 10;
constexpr uintptr_t RMODEL_LOAD = 0x00FA8FE0;

// rModel (0x230 bytes): cResource::mPath char[64] at +0x08, mJointNum +0x64, mPrimitiveInfo +0x70,
// mPrimitiveNum +0x74, mBoundingSphere +0xB0 (radius +0xBC), mModelInfo +0xE0.  A PC primitive is
// 48 bytes with its LOD mask in byte +7.
constexpr uint32_t OFS_PATH = 0x08, PATH_LEN = 64, OFS_JOINTS = 0x64, OFS_PRIMS = 0x70, OFS_PRIM_NUM = 0x74,
                   OFS_RADIUS = 0xBC, OFS_INFO = 0xE0, PRIM_SIZE = 48, PRIM_LOD = 7, LOD_HIGH = 1;

// ---- settings ---------------------------------------------------------------------------------
struct Settings {
    bool enabled = true;
    double scale = 0;         // 0: auto, screen height / 720
    double popPixels = 24;    // 0: off
    double height = 0;        // 0: auto, from the game's config.ini
    double fov = 0;           // vertical degrees; 0: auto, 40 (uCamera's default) + config CameraFov
    double characters = 1.0;
    double maxMeters = 10000;
};

Settings g_set;
double g_k = 1, g_c = 0, g_cap = 1e6;  // derived: scenery floor, pixels constant, cap (engine units)
wchar_t g_logPath[MAX_PATH];
volatile LONG g_models = 0, g_env = 0, g_fallback = 0, g_farOnly = 0, g_changed = 0, g_detailed = 0;
// Ninput runtime multiplier x1000 (1000 == x1.0). Set atomically via the engine provider; read per
// model load, so it takes effect for models loaded after the change (already-loaded ones keep theirs).
volatile LONG g_extra_milli = 1000;

// One write per line: models load on several threads.
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

// A path the resource manager gave the model: printable, terminated within mPath.
size_t PathLength(const char* p) {
    for (size_t i = 0; i < PATH_LEN; i++) {
        uint8_t c = (uint8_t)p[i];
        if (!c) return i;
        if (c < 0x20 || c > 0x7E) return 0;
    }
    return 0;
}

bool StartsWith(const char* s, const char* prefix) {
    for (; *prefix; s++, prefix++) {
        char a = *s, b = *prefix;
        if (a == '/') a = '\\';
        if (a >= 'A' && a <= 'Z') a = (char)(a - 'A' + 'a');
        if (a != b) return false;
    }
    return true;
}

// Whether any primitive is drawn at HIGH.  One that is not is a stand-in shown only from afar,
// with other models covering the close range; pushing its distances out would hide it.
bool DrawsAtHigh(const uint8_t* model) {
    const uint8_t* prims = *(const uint8_t* const*)(model + OFS_PRIMS);
    uint32_t count = *(const uint32_t*)(model + OFS_PRIM_NUM);
    if (!prims || !count) return true;
    for (uint32_t i = 0; i < count; i++)
        if (prims[i * PRIM_SIZE + PRIM_LOD] & LOD_HIGH) return true;
    return false;
}

// d x s, capped; a vanilla distance is never lowered.  The cap (at most 1e8) keeps the draw paths'
// ViewRange x2 inside an int.
int32_t Scaled(int32_t d, double s) {
    if (d <= 0) return d;
    double v = (double)d * s;
    if (v > g_cap) v = g_cap;
    if (v <= (double)d) return d;
    return (int32_t)floor(v + 0.5);
}

}  // namespace

// ---- the policy, called once per loaded model from the thunk ----------------------------------
extern "C" void __stdcall LodTuner_OnModel(uint8_t* model) {
    LONG seen = InterlockedIncrement(&g_models);
    const char* path = (const char*)(model + OFS_PATH);
    size_t n = PathLength(path);
    bool env;
    if (n) {
        env = StartsWith(path, "scr\\") || StartsWith(path, "model\\om\\");
    } else {
        env = *(const uint32_t*)(model + OFS_JOINTS) == 0;  // no usable path: a model without joints is scenery
        InterlockedIncrement(&g_fallback);
    }
    int32_t* info = (int32_t*)(model + OFS_INFO);
    int32_t mid0 = info[0], low0 = info[1];
    double s;
    if (env) {
        InterlockedIncrement(&g_env);
        s = g_k;
        float r = *(const float*)(model + OFS_RADIUS);
        if (g_c > 0 && mid0 > 0 && r > 0 && r < 1e9f) {
            double px = g_c * (double)r / (double)mid0;  // keeps HIGH until about PopPixels tall
            if (px > s) s = px;
        }
        if (!DrawsAtHigh(model)) {
            InterlockedIncrement(&g_farOnly);
            s = 1.0;
        }
    } else {
        s = g_set.characters;
    }
    s *= (double)g_extra_milli / 1000.0;  // Ninput runtime multiplier; Scaled still never lowers a distance
    int32_t mid = Scaled(mid0, s), low = Scaled(low0, s);
    if (mid != mid0 || low != low0) {
        info[0] = mid;
        info[1] = low;
        InterlockedIncrement(&g_changed);
        if (InterlockedIncrement(&g_detailed) <= 12)
            Log("  %s: HIGH to %d -> %d, MEDIUM to %d -> %d (radius %.0f, x%.2f)", n ? path : "(no path)", mid0, mid,
                low0, low, (double)*(const float*)(model + OFS_RADIUS), s);
    }
    if (seen % 2000 == 0)
        Log("  %ld models loaded: %ld scenery (%ld without a path, %ld far-only kept), %ld others; %ld changed", seen,
            g_env, g_fallback, g_farOnly, seen - g_env, g_changed);
}

// ---- thunk ------------------------------------------------------------------------------------
// At the site esi is the rModel, fully read up to its primitives.  The thunk first replays the two
// instructions the jmp covers (one reads [esp+0x2C], so before anything is pushed), then calls the
// policy with every general register, the flags and every xmm register the C code could touch saved.
static uintptr_t g_back;

__declspec(naked) static void ThunkModel() {
    __asm {
        mov ecx, dword ptr [esp + 0x2C]
        mov edx, dword ptr [esi + 0x78]
        pushfd
        pushad
        sub esp, 128
        movdqu [esp], xmm0
        movdqu [esp + 16], xmm1
        movdqu [esp + 32], xmm2
        movdqu [esp + 48], xmm3
        movdqu [esp + 64], xmm4
        movdqu [esp + 80], xmm5
        movdqu [esp + 96], xmm6
        movdqu [esp + 112], xmm7
        push esi
        call LodTuner_OnModel
        movdqu xmm0, [esp]
        movdqu xmm1, [esp + 16]
        movdqu xmm2, [esp + 32]
        movdqu xmm3, [esp + 48]
        movdqu xmm4, [esp + 64]
        movdqu xmm5, [esp + 80]
        movdqu xmm6, [esp + 96]
        movdqu xmm7, [esp + 112]
        add esp, 128
        popad
        popfd
        jmp dword ptr [g_back]
    }
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
    if (!Readable(SITE, sizeof SITE_BYTES) || memcmp((const void*)SITE, SITE_BYTES, sizeof SITE_BYTES) != 0)
        return Log("refused: rModel::load at 0x%08X is not the expected code (another build, or already patched)",
                   (unsigned)SITE), false;
    for (const Expect& e : CONTEXT) {
        if (!Readable(e.at, e.len) || memcmp((const void*)e.at, e.bytes, e.len) != 0)
            return Log("refused: %s at 0x%08X is not the expected code", e.what, (unsigned)e.at), false;
    }
    if (!Readable(RMODEL_VTABLE, (SLOT_LOAD + 1) * 4) || ((const uint32_t*)RMODEL_VTABLE)[SLOT_LOAD] != RMODEL_LOAD)
        return Log("refused: rModel's vtable does not name rModel::load"), false;
    return true;
}

bool Patch() {
    g_back = BACK;
    DWORD old;
    if (!VirtualProtect((void*)SITE, sizeof SITE_BYTES, PAGE_EXECUTE_READWRITE, &old))
        return Log("failed: VirtualProtect at 0x%08X (%lu)", (unsigned)SITE, GetLastError()), false;
    uint8_t* p = (uint8_t*)SITE;
    int32_t rel = (int32_t)((uintptr_t)ThunkModel - (SITE + 5));
    p[0] = 0xE9;
    memcpy(p + 1, &rel, 4);
    for (size_t k = 5; k < sizeof SITE_BYTES; k++) p[k] = 0x90;
    VirtualProtect((void*)SITE, sizeof SITE_BYTES, old, &old);
    FlushInstructionCache(GetCurrentProcess(), (void*)SITE, sizeof SITE_BYTES);
    return true;
}

// The number in [lod] key; `def` when the key is missing, empty, "auto" or not a number.
double ReadNumber(const wchar_t* ini, const wchar_t* key, double def) {
    wchar_t buf[64];
    GetPrivateProfileStringW(L"lod", key, L"", buf, 64, ini);
    wchar_t* s = buf;
    while (*s == L' ' || *s == L'\t') s++;
    if (!*s || _wcsnicmp(s, L"auto", 4) == 0) return def;
    wchar_t* end = nullptr;
    double v = wcstod(s, &end);
    return (end == s || !isfinite(v)) ? def : v;
}

// The game's own settings: %LOCALAPPDATA%\CAPCOM\DRAGONS DOGMA DARK ARISEN\config.ini.
struct GameConfig {
    double height = 0, cameraFov = 0;
    wchar_t viewRange[32] = L"";
};

GameConfig ReadGameConfig() {
    GameConfig g;
    wchar_t local[MAX_PATH], ini[MAX_PATH];
    if (!GetEnvironmentVariableW(L"LOCALAPPDATA", local, MAX_PATH)) return g;
    _snwprintf_s(ini, MAX_PATH, _TRUNCATE, L"%s\\CAPCOM\\DRAGONS DOGMA DARK ARISEN\\config.ini", local);
    wchar_t res[64];
    GetPrivateProfileStringW(L"DISPLAY", L"Resolution", L"", res, 64, ini);
    const wchar_t* x = wcschr(res, L'x');
    if (x) g.height = wcstod(x + 1, nullptr);
    wchar_t fov[64];
    GetPrivateProfileStringW(L"GRAPHICS", L"CameraFov", L"0", fov, 64, ini);
    g.cameraFov = wcstod(fov, nullptr);
    GetPrivateProfileStringW(L"GRAPHICS", L"ViewRange", L"", g.viewRange, 32, ini);
    return g;
}

double Clamp(double v, double lo, double hi) { return v < lo ? lo : v > hi ? hi : v; }

void LoadSettings(HMODULE self) {
    wchar_t ini[MAX_PATH];
    GetModuleFileNameW(self, ini, MAX_PATH);
    wchar_t* dot = wcsrchr(ini, L'.');
    wchar_t* slash = wcsrchr(ini, L'\\');
    if (dot && (!slash || dot > slash)) *dot = 0;
    wcscat_s(ini, L".ini");
    bool haveIni = GetFileAttributesW(ini) != INVALID_FILE_ATTRIBUTES;

    Settings s;
    s.enabled = GetPrivateProfileIntW(L"lod", L"Enabled", 1, ini) != 0;
    s.scale = ReadNumber(ini, L"Scale", 0);
    s.popPixels = Clamp(ReadNumber(ini, L"PopPixels", 24), 0, 4096);
    s.height = ReadNumber(ini, L"ScreenHeight", 0);
    s.fov = ReadNumber(ini, L"FieldOfView", 0);
    s.characters = Clamp(ReadNumber(ini, L"Characters", 1.0), 1.0, 16.0);  // never lowers a distance
    s.maxMeters = ReadNumber(ini, L"MaxDistance", 10000);
    if (s.maxMeters <= 0) s.maxMeters = 10000;

    GameConfig game = ReadGameConfig();
    if (s.height <= 0) s.height = game.height > 0 ? game.height : 1080;
    if (s.fov <= 0) s.fov = 40.0 + game.cameraFov;
    s.height = Clamp(s.height, 240, 16384);
    s.fov = Clamp(s.fov, 10, 150);
    g_set = s;

    g_k = Clamp(s.scale > 0 ? s.scale : s.height / 720.0, 1.0, 16.0);
    g_c = s.popPixels > 0 ? s.height / (s.popPixels * tan(s.fov * 3.14159265358979323846 / 360.0)) : 0;
    g_cap = Clamp(s.maxMeters * 100.0, 1000.0, 1.0e8);

    char pixels[96];
    if (s.popPixels > 0)
        _snprintf_s(pixels, sizeof pixels, _TRUNCATE, "kept whole until about %.0f px tall (screen %.0f px, %.0f deg)",
                    s.popPixels, s.height, s.fov);
    else
        _snprintf_s(pixels, sizeof pixels, _TRUNCATE, "no pixel rule");
    Log("settings: %s; scenery at least x%.2f%s, %s; others x%.2f; cap %.0f m",
        haveIni ? "lod_tuner.ini" : "no lod_tuner.ini, defaults", g_k, s.scale > 0 ? "" : " (screen height / 720)",
        pixels, s.characters, g_cap / 100.0);
    if (game.viewRange[0])
        Log("config.ini ViewRange=%S%s", game.viewRange,
            _wcsicmp(game.viewRange, L"FARTHEST") == 0
                ? ": the game draws every regular model at full detail at any distance, so this plugin changes only "
                  "instanced vegetation there; FAR (distances x2) or NORMAL let it work everywhere"
                : _wcsicmp(game.viewRange, L"FAR") == 0 ? ": the game doubles these distances again for regular models" : "");
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
    _snwprintf_s(g_logPath, MAX_PATH, _TRUNCATE, L"%s\\riftstone\\logs\\lod_tuner.log", root);
    // A fresh log per game session; a second copy of the plugin in the same process appends to it.
    wchar_t probe[8];
    if (GetEnvironmentVariableW(L"RIFTSTONE_LOD_TUNER_LOG", probe, 8) == 0) {
        DeleteFileW(g_logPath);
        SetEnvironmentVariableW(L"RIFTSTONE_LOD_TUNER_LOG", L"1");
    }

    // The game is a fixed-base image; a test harness that maps DDDA.exe itself sets the variable.
    bool harness = GetEnvironmentVariableW(L"RIFTSTONE_LOD_HARNESS", probe, 8) > 0;
    if (!harness && (uintptr_t)GetModuleHandleW(nullptr) != IMAGE_BASE) {
        Log("refused: the host is not DDDA.exe at 0x00400000");
        return;
    }
    LoadSettings(self);
    if (!g_set.enabled) {
        Log("lod_tuner: Enabled=0, nothing patched");
        return;
    }
    if (!Verify() || !Patch()) return;
    Log("lod_tuner: rModel::load patched at 0x%08X (%s); first changes:", (unsigned)SITE, harness ? "harness" : "game");
}

}  // namespace

// ---- Ninput engine provider (optional) ----------------------------------------------------------
// lod_tuner installs its rModel::load hook at DllMain as usual; this only exposes a runtime
// multiplier through nyr->engine->set_lod_distance_multiplier. Because the hook reads the multiplier
// on every model load, a change takes effect for models loaded afterwards -- genuinely runtime.
extern "C" int NinputProvide_LodMultiplier(float m) {
    if (!(m > 0.0f) || !isfinite((double)m)) return NINPUT_ERR_BADARG;
    double v = (double)m;
    if (v < 0.1) v = 0.1;
    if (v > 64.0) v = 64.0;
    InterlockedExchange(&g_extra_milli, (LONG)(v * 1000.0 + 0.5));
    Log("lod_tuner: Ninput runtime distance multiplier set to x%.2f (affects models loaded next)", v);
    return NINPUT_OK;
}

// For tests / a live view: the multiplier currently in effect.
extern "C" __declspec(dllexport) float LodTuner_Multiplier() { return (float)g_extra_milli / 1000.0f; }

extern "C" __declspec(dllexport) int Ninput_Initialize(const NinputInterface* nyr) {
    if (!nyr || nyr->abi_version != NINPUT_ABI_VERSION || !nyr->engine) return 0;
    nyr->engine->provide_lod_distance_multiplier(NinputProvide_LodMultiplier);
    return 1;
}

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(module);
        Start(module);
    }
    return TRUE;
}

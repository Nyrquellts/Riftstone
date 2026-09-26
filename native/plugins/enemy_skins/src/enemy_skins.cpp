// enemy_skins -- Riftstone native plugin: per-placement texture skins for the chimera (em5200).
//
// How a chimera gets a skin
//   A placement marks its chimera through a stat field the game ignores: the magic-defence
//   multiplier *value* with its enable flag off.  cSetInfoPawn::applyInfo (0x007A4B80) copies both
//   into the unit (flag +0x20BC, value +0x20C0) whatever the flag says, and every reader of the value
//   (uEnemy::callbackDamagePreCalc and friends) tests the flag first.  A value whose bits are
//   0x534B00NN ("SK", skin NN) with the flag off therefore changes nothing in play and names skin NN.
//
//   When the chimera, its goat head (uEm5200_00) or its snake tail (uEm5200_01) picks its
//   full-detail material in setupSecond, and whenever it rebinds its damage textures, this plugin
//   hands the game the skin's copies instead: model\em\e52\e5200\sNN\<same name>.  The skin's mod
//   adds those resources to rom\enemy\em5200.arc, so they are loaded with every chimera.  The parts
//   follow their parent (goat +0x7278, snake +0x72C0).  Unmarked chimeras are untouched, and a
//   marked chimera whose skin is missing keeps its low-detail material rather than crashing.
//
// Safety
//   DDDA.exe build 2364871 only.  Every patched instruction, the three vtables and every table and
//   string the plugin copies are compared first; on any difference nothing is patched and the log
//   (riftstone\logs\enemy_skins.log) says why.  Original code; no third-party source.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

namespace {

// ---- build 2364871 --------------------------------------------------------------------------
constexpr uintptr_t IMAGE_BASE = 0x00400000;

struct Site {
    const char* name;
    uintptr_t at;         // first patched byte
    uintptr_t back;       // where the thunk resumes the game
    uint8_t expect[10];
    uint8_t len;
};

// The six places a chimera names a skinnable resource.  *_MAT: "push <material path>; push
// rMaterial DTI" before rResourceManager::get; *_DMG: "add <reg>, <damage-texture table>".
const Site SITES[] = {
    {"body material", 0x009A1428, 0x009A1432, {0x68, 0xD4, 0xD4, 0x59, 0x01, 0x68, 0x7C, 0x43, 0x8D, 0x01}, 10},
    {"body damage table", 0x009A1553, 0x009A1558, {0x05, 0x48, 0xF5, 0x6F, 0x01}, 5},
    {"goat material", 0x009B17DD, 0x009B17E7, {0x68, 0x54, 0xD6, 0x59, 0x01, 0x68, 0x7C, 0x43, 0x8D, 0x01}, 10},
    {"goat damage table", 0x009B19D0, 0x009B19D5, {0x05, 0x08, 0x9C, 0x6F, 0x01}, 5},
    {"snake material", 0x009B96CD, 0x009B96D7, {0x68, 0x74, 0xD6, 0x59, 0x01, 0x68, 0x7C, 0x43, 0x8D, 0x01}, 10},
    {"snake damage table", 0x009B99C4, 0x009B99CA, {0x81, 0xC3, 0x08, 0x3D, 0x6F, 0x01}, 6},
};
enum { BODY_MAT, BODY_DMG, GOAT_MAT, GOAT_DMG, SNAKE_MAT, SNAKE_DMG, SITE_COUNT };

constexpr uintptr_t VT_BODY = 0x015C4E28, VT_GOAT = 0x015C5FD0, VT_SNAKE = 0x015C6C68;
constexpr uintptr_t SETUP_SECOND_BODY = 0x009A0880, SETUP_SECOND_GOAT = 0x009B0F50, SETUP_SECOND_SNAKE = 0x009B8F10;
constexpr uint32_t SLOT_SETUP_SECOND = 33;
constexpr uint32_t PARENT_GOAT = 0x7278, PARENT_SNAKE = 0x72C0;
constexpr uint32_t FIELD_FLAG = 0x20BC, FIELD_VALUE = 0x20C0;
constexpr uint32_t MARK_MASK = 0xFFFF0000u, MARK_TAG = 0x534B0000u;

constexpr uintptr_t MAT_BODY = 0x0159D4D4, MAT_GOAT = 0x0159D654, MAT_SNAKE = 0x0159D674;
const char* const MAT_NAMES[3] = {"e5200_a", "e5200_00_a", "e5200_01_a"};
const char FOLDER[] = "model\\em\\e52\\e5200\\";

// Damage-texture tables: {s32 material index; char* texture[k]} x entries, one block per variant
// (0 = chimera, 1 = gorechimera).  Only variant 0 is skinned.
struct Table { uintptr_t at; uint32_t entries, words; };
const Table TABLE_BODY = {0x016FF548, 11, 5}, TABLE_GOAT = {0x016F9C08, 4, 4}, TABLE_SNAKE = {0x016F3D08, 5, 4};

// A skin replaces these albedo maps; everything else (normals, masks, damage alphas) stays vanilla.
const char* const SKIN_TEXTURES[4] = {"e5200_skin_BM", "e5200_face_BM", "e5200_hebi_BM", "e5200_eye_BM"};

constexpr int MAX_SKINS = 99;

struct Skin {
    char material[3][64];
    char texture[4][64];
    uint32_t body[11 * 5], goat[4 * 4], snake[5 * 4];
};
Skin* g_skins = nullptr;  // [MAX_SKINS + 1]; index 0 unused
wchar_t g_logPath[MAX_PATH];

void Log(const char* fmt, ...) {
    char line[512];
    va_list ap;
    va_start(ap, fmt);
    int n = _vsnprintf_s(line, sizeof line, _TRUNCATE, fmt, ap);
    va_end(ap);
    if (n < 0) n = (int)strlen(line);
    HANDLE h = CreateFileW(g_logPath, FILE_APPEND_DATA, FILE_SHARE_READ, nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) return;
    DWORD w;
    WriteFile(h, line, (DWORD)n, &w, nullptr);
    WriteFile(h, "\r\n", 2, &w, nullptr);
    CloseHandle(h);
}

// ---- which skin a unit wears ----------------------------------------------------------------
int SkinOfBody(const uint8_t* u) {
    if (!u || *(const uintptr_t*)u != VT_BODY || u[FIELD_FLAG] != 0) return 0;
    uint32_t bits = *(const uint32_t*)(u + FIELD_VALUE);
    if ((bits & MARK_MASK) != MARK_TAG) return 0;
    uint32_t id = bits & ~MARK_MASK;
    return (id >= 1 && id <= (uint32_t)MAX_SKINS) ? (int)id : 0;
}

int SkinOfPart(const uint8_t* u, uintptr_t vtable, uint32_t parent) {
    if (!u || *(const uintptr_t*)u != vtable) return 0;
    return SkinOfBody(*(const uint8_t* const*)(u + parent));
}

}  // namespace

// ---- the choices, called from the thunks (__stdcall: they pop their own arguments) ------------
extern "C" const char* __stdcall EnemySkins_BodyMaterial(const uint8_t* unit) {
    int s = SkinOfBody(unit);
    return s ? g_skins[s].material[0] : (const char*)MAT_BODY;
}
extern "C" const char* __stdcall EnemySkins_GoatMaterial(const uint8_t* unit) {
    int s = SkinOfPart(unit, VT_GOAT, PARENT_GOAT);
    return s ? g_skins[s].material[1] : (const char*)MAT_GOAT;
}
extern "C" const char* __stdcall EnemySkins_SnakeMaterial(const uint8_t* unit) {
    int s = SkinOfPart(unit, VT_SNAKE, PARENT_SNAKE);
    return s ? g_skins[s].material[2] : (const char*)MAT_SNAKE;
}
extern "C" uintptr_t __stdcall EnemySkins_BodyTable(const uint8_t* unit, uint32_t offset) {
    int s = offset == 0 ? SkinOfBody(unit) : 0;
    return s ? (uintptr_t)g_skins[s].body : TABLE_BODY.at + offset;
}
extern "C" uintptr_t __stdcall EnemySkins_GoatTable(const uint8_t* unit, uint32_t offset) {
    int s = offset == 0 ? SkinOfPart(unit, VT_GOAT, PARENT_GOAT) : 0;
    return s ? (uintptr_t)g_skins[s].goat : TABLE_GOAT.at + offset;
}
extern "C" uintptr_t __stdcall EnemySkins_SnakeTable(const uint8_t* unit, uint32_t offset) {
    int s = offset == 0 ? SkinOfPart(unit, VT_SNAKE, PARENT_SNAKE) : 0;
    return s ? (uintptr_t)g_skins[s].snake : TABLE_SNAKE.at + offset;
}

// ---- thunks ---------------------------------------------------------------------------------
// Material sites: the game has pushed the "load" flag (1) and keeps rResourceManager::get in edx
// (body) or eax (parts); the unit is in ebx (body, goat) or ebp (snake).  Each thunk reserves the
// slot for the path, keeps every register, then pushes the rMaterial DTI and resumes at the call.
static uintptr_t g_backBodyMat, g_backBodyDmg, g_backGoatMat, g_backGoatDmg, g_backSnakeMat, g_backSnakeDmg;

__declspec(naked) static void ThunkBodyMaterial() {
    __asm {
        sub esp, 4
        pushad
        push ebx
        call EnemySkins_BodyMaterial
        mov [esp + 32], eax
        popad
        push 0x018D437C
        jmp dword ptr [g_backBodyMat]
    }
}
__declspec(naked) static void ThunkGoatMaterial() {
    __asm {
        sub esp, 4
        pushad
        push ebx
        call EnemySkins_GoatMaterial
        mov [esp + 32], eax
        popad
        push 0x018D437C
        jmp dword ptr [g_backGoatMat]
    }
}
__declspec(naked) static void ThunkSnakeMaterial() {
    __asm {
        sub esp, 4
        pushad
        push ebp
        call EnemySkins_SnakeMaterial
        mov [esp + 32], eax
        popad
        push 0x018D437C
        jmp dword ptr [g_backSnakeMat]
    }
}
// Damage sites: the helper was called with the unit as its only stack argument and has pushed
// ecx, ebx, ebp, esi (body, goat: the unit is at [esp+0x14]) or also edi (snake: [esp+0x18]).
// eax (ebx for the snake) holds variant * block size; the thunk turns it into the table address.
__declspec(naked) static void ThunkBodyTable() {
    __asm {
        push ecx
        push edx
        push eax
        push dword ptr [esp + 0x20]
        call EnemySkins_BodyTable
        pop edx
        pop ecx
        jmp dword ptr [g_backBodyDmg]
    }
}
__declspec(naked) static void ThunkGoatTable() {
    __asm {
        push ecx
        push edx
        push eax
        push dword ptr [esp + 0x20]
        call EnemySkins_GoatTable
        pop edx
        pop ecx
        jmp dword ptr [g_backGoatDmg]
    }
}
__declspec(naked) static void ThunkSnakeTable() {
    __asm {
        push eax
        push ecx
        push edx
        push ebx
        push dword ptr [esp + 0x28]
        call EnemySkins_SnakeTable
        mov ebx, eax
        pop edx
        pop ecx
        pop eax
        jmp dword ptr [g_backSnakeDmg]
    }
}

namespace {

void* const THUNKS[SITE_COUNT] = {(void*)ThunkBodyMaterial, (void*)ThunkBodyTable, (void*)ThunkGoatMaterial,
                                  (void*)ThunkGoatTable, (void*)ThunkSnakeMaterial, (void*)ThunkSnakeTable};

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

const char* BaseName(const char* path) {
    const char* slash = strrchr(path, '\\');
    return slash ? slash + 1 : path;
}

// Every texture a vanilla table names must be one of the chimera's (a readable string in FOLDER).
bool CheckTable(const Table& t, const char* what) {
    const uint32_t* w = (const uint32_t*)t.at;
    if (!Readable(t.at, t.entries * t.words * 4)) return Log("refused: %s table unreadable", what), false;
    for (uint32_t e = 0; e < t.entries; e++) {
        for (uint32_t k = 1; k < t.words; k++) {
            uint32_t p = w[e * t.words + k];
            if (!p) continue;
            if (!Readable(p, sizeof FOLDER) || strncmp((const char*)p, FOLDER, sizeof FOLDER - 1) != 0)
                return Log("refused: %s table entry %u names something else", what, e), false;
        }
    }
    return true;
}

void CopyTable(const Table& t, uint32_t* out, const Skin& s) {
    memcpy(out, (const void*)t.at, t.entries * t.words * 4);
    for (uint32_t e = 0; e < t.entries; e++) {
        for (uint32_t k = 1; k < t.words; k++) {
            uint32_t& p = out[e * t.words + k];
            if (!p) continue;
            for (int i = 0; i < 4; i++)
                if (strcmp(BaseName((const char*)p), SKIN_TEXTURES[i]) == 0) p = (uint32_t)(uintptr_t)s.texture[i];
        }
    }
}

bool Verify() {
    for (const Site& s : SITES) {
        if (!Readable(s.at, s.len) || memcmp((const void*)s.at, s.expect, s.len) != 0)
            return Log("refused: %s at 0x%08X is not the expected code (another build, or already patched)",
                       s.name, (unsigned)s.at), false;
    }
    struct { uintptr_t vt, fn; const char* name; } vts[] = {
        {VT_BODY, SETUP_SECOND_BODY, "uEm5200"}, {VT_GOAT, SETUP_SECOND_GOAT, "uEm5200_00"},
        {VT_SNAKE, SETUP_SECOND_SNAKE, "uEm5200_01"}};
    for (auto& v : vts) {
        if (!Readable(v.vt, (SLOT_SETUP_SECOND + 1) * 4) || ((const uint32_t*)v.vt)[SLOT_SETUP_SECOND] != v.fn)
            return Log("refused: %s vtable does not match", v.name), false;
    }
    const uintptr_t mats[3] = {MAT_BODY, MAT_GOAT, MAT_SNAKE};
    for (int i = 0; i < 3; i++) {
        char want[64];
        _snprintf_s(want, sizeof want, _TRUNCATE, "%s%s", FOLDER, MAT_NAMES[i]);
        if (!Readable(mats[i], strlen(want) + 1) || strcmp((const char*)mats[i], want) != 0)
            return Log("refused: material name %s not found", want), false;
    }
    return CheckTable(TABLE_BODY, "body") && CheckTable(TABLE_GOAT, "goat") && CheckTable(TABLE_SNAKE, "snake");
}

bool BuildSkins() {
    g_skins = (Skin*)VirtualAlloc(nullptr, sizeof(Skin) * (MAX_SKINS + 1), MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    if (!g_skins) return Log("refused: out of memory"), false;
    for (int n = 1; n <= MAX_SKINS; n++) {
        Skin& s = g_skins[n];
        for (int i = 0; i < 3; i++)
            _snprintf_s(s.material[i], sizeof s.material[i], _TRUNCATE, "%ss%02d\\%s", FOLDER, n, MAT_NAMES[i]);
        for (int i = 0; i < 4; i++)
            _snprintf_s(s.texture[i], sizeof s.texture[i], _TRUNCATE, "%ss%02d\\%s", FOLDER, n, SKIN_TEXTURES[i]);
        CopyTable(TABLE_BODY, s.body, s);
        CopyTable(TABLE_GOAT, s.goat, s);
        CopyTable(TABLE_SNAKE, s.snake, s);
    }
    return true;
}

bool Patch() {
    uintptr_t* backs[SITE_COUNT] = {&g_backBodyMat, &g_backBodyDmg, &g_backGoatMat, &g_backGoatDmg, &g_backSnakeMat, &g_backSnakeDmg};
    for (int i = 0; i < SITE_COUNT; i++) *backs[i] = SITES[i].back;
    for (int i = 0; i < SITE_COUNT; i++) {
        const Site& s = SITES[i];
        DWORD old;
        if (!VirtualProtect((void*)s.at, s.len, PAGE_EXECUTE_READWRITE, &old))
            return Log("failed: VirtualProtect at 0x%08X (%lu)", (unsigned)s.at, GetLastError()), false;
        uint8_t* p = (uint8_t*)s.at;
        p[0] = 0xE9;
        *(int32_t*)(p + 1) = (int32_t)((uintptr_t)THUNKS[i] - (s.at + 5));
        for (int k = 5; k < s.len; k++) p[k] = 0x90;
        VirtualProtect((void*)s.at, s.len, old, &old);
        FlushInstructionCache(GetCurrentProcess(), (void*)s.at, s.len);
    }
    return true;
}

void Start() {
    wchar_t root[MAX_PATH];
    GetModuleFileNameW(nullptr, root, MAX_PATH);
    wchar_t* slash = wcsrchr(root, L'\\');
    if (slash) *slash = 0;
    _snwprintf_s(g_logPath, MAX_PATH, _TRUNCATE, L"%s\\riftstone\\logs\\enemy_skins.log", root);
    wchar_t dir[MAX_PATH];
    _snwprintf_s(dir, MAX_PATH, _TRUNCATE, L"%s\\riftstone", root);
    CreateDirectoryW(dir, nullptr);
    _snwprintf_s(dir, MAX_PATH, _TRUNCATE, L"%s\\riftstone\\logs", root);
    CreateDirectoryW(dir, nullptr);
    DeleteFileW(g_logPath);

    // The game is a fixed-base image; a test harness that maps DDDA.exe itself sets the variable.
    wchar_t probe[8];
    bool harness = GetEnvironmentVariableW(L"RIFTSTONE_SKINS_HARNESS", probe, 8) > 0;
    if (!harness && (uintptr_t)GetModuleHandleW(nullptr) != IMAGE_BASE) {
        Log("refused: the host is not DDDA.exe at 0x00400000");
        return;
    }
    if (!Verify() || !BuildSkins() || !Patch()) return;
    Log("enemy_skins: %d sites patched; chimera skins s01..s%02d from rom\\enemy\\em5200.arc (%s)",
        SITE_COUNT, MAX_SKINS, harness ? "harness" : "game");
}

}  // namespace

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(module);
        Start();
    }
    return TRUE;
}

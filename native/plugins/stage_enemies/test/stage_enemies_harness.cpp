// stage_enemies_harness_core -- runs the stage_enemies hook inside the real game code, without the game.
//
//   stage_enemies_stub.exe <DDDA.exe> <stage_enemies.asi> <on|off>
//
// Maps DDDA.exe at its fixed base (0x00400000) over the stub's image, loads the plugin (which reads its
// ini, resolves the enemies to archive tags through the exe's own table, verifies the stage loader and
// patches it), then:
//   * checks the patch: a jmp to the plugin's thunk at 0x004FFD8E, the covered bytes replaced;
//   * checks tag resolution against the real archive table (em5301 -> 173, em5300 -> 172, em5800 -> 162,
//     em5902 -> 167), which is what the plugin read to build its table;
//   * checks the dispatch: with a recorder standing in for the game's queue, a load of stage 370 queues
//     tag 173 and a load of another stage queues nothing;
//   * drives the real thunk: enters the stage loader at 0x004FFD8E with a fake frame (stage 370 at
//     +0x724, slot 5 at +0xA28) and stops at 0x004FFD94, checking the recorder saw (slot 5, tag 173),
//     that edx (the slot index the covered cmp reads) came through, and that esp is balanced.
// Profiles (run_tests.py writes the matching stage_enemies.ini):
//   on   -- 370 = em5301
//   off  -- Enabled = 0: nothing patched
// Prints "pass"/"FAIL" lines, exits 0 when everything passed, 1 on a failure, 2 when the image cannot be
// mapped here (a skip).
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shellapi.h>

#include <setjmp.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include <string>
#include <vector>

namespace {
constexpr uintptr_t BASE = 0x00400000;
constexpr uintptr_t SITE = 0x004FFD8E, NEXT = 0x004FFD94;
const uint8_t SITE_BYTES[6] = {0x81, 0xFA, 0x00, 0x01, 0x00, 0x00};

int g_fails = 0;
void Check(bool ok, const std::string& what) {
    printf("  %s  %s\n", ok ? "pass" : "FAIL", what.c_str());
    if (!ok) g_fails++;
}

// ---- plugin exports ----------------------------------------------------------------------------
typedef uint32_t(*ResolveFn)(const char*);
typedef void(__cdecl* QueueFn)(uint32_t, uint32_t);
typedef void(*SetQueueFn)(QueueFn);
typedef void(__cdecl* OnLoadFn)(uint32_t, uint32_t);
ResolveFn g_resolve = nullptr;
SetQueueFn g_setQueue = nullptr;
OnLoadFn g_onLoad = nullptr;

// ---- recorder standing in for the game's queue -------------------------------------------------
uint32_t g_recSlot = 0, g_recTag = 0;
int g_recCount = 0;
void __cdecl Recorder(uint32_t slot, uint32_t tag) {
    g_recSlot = slot;
    g_recTag = tag;
    g_recCount++;
}
void ResetRec() { g_recSlot = g_recTag = 0; g_recCount = 0; }

// ---- driving the real thunk --------------------------------------------------------------------
alignas(16) uint8_t g_frame[0x1200];
uint32_t g_frameAddr, g_capEdx, g_capEsp, g_entryEsp, g_site = SITE, g_entryEdx = 0;
jmp_buf g_back;

void __cdecl CaptureC() { longjmp(g_back, 1); }

// Placed at 0x004FFD94, the game instruction after the patched cmp.
__declspec(naked) void Capture() {
    __asm {
        mov g_capEdx, edx
        mov g_capEsp, esp
        call CaptureC
    }
}

__declspec(naked) void JumpToSite() {
    __asm {
        mov ebp, g_frameAddr             // fake frame: +0x724 stage, +0xA28 slot index
        mov edx, g_entryEdx               // the slot index the loader has in edx at the site (= [ebp+0xA28])
        mov eax, 0x55555555
        mov ecx, 0x66666666
        mov esi, 0x22222222
        mov edi, 0x33333333
        mov ebx, 0x44444444
        mov g_entryEsp, esp
        jmp dword ptr [g_site]
    }
}

#pragma warning(push)
#pragma warning(disable : 4611)  // setjmp with C++ objects: nothing here needs unwinding
bool EnterSite(uint32_t stage, uint32_t slot) {
    *(uint32_t*)(g_frame + 0x724) = stage;
    *(uint32_t*)(g_frame + 0xA28) = slot;
    g_frameAddr = (uint32_t)(uintptr_t)g_frame;
    g_entryEdx = slot;                    // edx = the slot index at the site, as the game loads it
    ResetRec();
    g_capEsp = 0;
    if (setjmp(g_back) == 0) JumpToSite();
    return g_capEdx == slot && g_capEsp == g_entryEsp;
}
#pragma warning(pop)

// ---- mapping DDDA.exe over the stub -------------------------------------------------------------
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
        return printf("SKIP: run me through stage_enemies_stub.exe (host at %p, 0x%X bytes)\n", (void*)host,
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

void PlaceCapture() {
    uint8_t* p = (uint8_t*)NEXT;
    int32_t rel = (int32_t)((uintptr_t)Capture - (NEXT + 5));
    p[0] = 0xE9;
    memcpy(p + 1, &rel, 4);
}

uintptr_t JmpTarget(uintptr_t at) {
    const uint8_t* p = (const uint8_t*)at;
    return p[0] == 0xE9 ? at + 5 + *(const int32_t*)(p + 1) : 0;
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
    if (argc < 4) {
        printf("usage: stage_enemies_stub <DDDA.exe> <stage_enemies.asi> <on|off>\n");
        return 1;
    }
    const std::wstring profile = argv[3];
    if (!MapImage(argv[1])) return 2;
    Check(memcmp((const void*)SITE, SITE_BYTES, sizeof SITE_BYTES) == 0,
          "the mapped stage loader has its cmp edx,0x100 at 0x004FFD8E");

    SetEnvironmentVariableW(L"RIFTSTONE_STAGE_ENEMIES_HARNESS", L"1");
    HMODULE plugin = LoadLibraryW(argv[2]);
    printf("plugin (profile %S)\n", profile.c_str());
    Check(plugin != nullptr, "the plugin loads");
    if (!plugin) return 1;
    g_resolve = (ResolveFn)GetProcAddress(plugin, "StageEnemies_ResolveTag");
    g_setQueue = (SetQueueFn)GetProcAddress(plugin, "StageEnemies_SetQueueForTest");
    g_onLoad = (OnLoadFn)GetProcAddress(plugin, "StageEnemies_OnLoad");
    Check(g_resolve && g_setQueue && g_onLoad, "the plugin exports resolve, set-queue and on-load");
    if (!g_resolve || !g_setQueue || !g_onLoad) return 1;

    printf("tag resolution against the real archive table\n");
    Check(g_resolve("em5301") == 173, "em5301 (Archydra) resolves to tag 173");
    Check(g_resolve("em5300") == 172, "em5300 (Hydra) resolves to tag 172");
    Check(g_resolve("em5800") == 162, "em5800 (The Dragon) resolves to tag 162");
    Check(g_resolve("em5902") == 167, "em5902 (Wyverns) resolves to tag 167");
    Check(g_resolve("EM5301") == 173, "resolution ignores case");
    Check(g_resolve("173") == 173, "a raw number is taken as the tag");
    Check(g_resolve("em9999") == 0, "an enemy with no archive resolves to 0 (skipped, not queued)");

    if (profile == L"off" || profile == L"shipped") {
        Check(memcmp((const void*)SITE, SITE_BYTES, sizeof SITE_BYTES) == 0,
              profile == L"off" ? "Enabled=0: the stage loader is left as it is"
                                : "the shipped ini lists no stage: the stage loader is left as it is");
        printf(g_fails ? "\n%d check(s) FAILED\n" : "\nall checks passed\n", g_fails);
        return g_fails ? 1 : 0;
    }

    uintptr_t thunk = JmpTarget(SITE);
    Check(thunk != 0 && *(const uint8_t*)(SITE + 5) == 0x90, "the site is a jmp to the plugin's thunk, the 6th byte a nop");

    printf("dispatch through a recorder standing in for the game's queue\n");
    g_setQueue(Recorder);
    ResetRec();
    g_onLoad(370, 5);
    Check(g_recCount == 1 && g_recSlot == 5 && g_recTag == 173, "a load of stage 370 queues tag 173 into slot 5");
    ResetRec();
    g_onLoad(100, 5);
    Check(g_recCount == 0, "a load of stage 100 (not listed) queues nothing");

    printf("the real thunk, entered at the stage loader with a fake frame\n");
    PlaceCapture();
    bool regs = EnterSite(370, 5);
    Check(g_recCount == 1 && g_recSlot == 5 && g_recTag == 173,
          "entering the loader for stage 370 runs the thunk and queues tag 173 (slot 5)");
    Check(regs, "  edx (the slot index the covered cmp reads) is intact and esp is balanced");
    regs = EnterSite(220, 7);
    Check(g_recCount == 0 && regs, "entering the loader for stage 220 queues nothing, edx/esp intact");

    // Restore the real queue so a stray later call would not hit the recorder.
    g_setQueue(nullptr);

    printf(g_fails ? "\n%d check(s) FAILED\n" : "\nall checks passed\n", g_fails);
    return g_fails ? 1 : 0;
}

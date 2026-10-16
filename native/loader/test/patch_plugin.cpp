// A stand-in for another mod's DLL that patches the game's code from its DllMain (an old dinput8 hook,
// HardPlus.dll): it writes jumps into the stand-in game's HarnessPatchArea (harness.cpp), for the loader's patch
// ledger (run_tests.py test_ledger).  Built twice (build.cmd):
//   patch_legacy.dll (PATCH_LEGACY), for riftstone\legacy: E9 <rel32> at +0, and from a thread 0.5 s later at
//                    +64 (a change after start-up, which the ledger's late scan names by where it leads)
//   patch_plugin.asi, for riftstone\plugins: E8 <rel32> at +2, over the legacy one's jump (a conflict), and
//                    0F 0B (ud2) at +128, which harness "ledgercrash" runs (a crash in patched code)
#define WIN32_LEAN_AND_MEAN
#include <Windows.h>
#include <string.h>

extern "C" __declspec(dllexport) __declspec(noinline) int PatchTarget(int x) { return x * 3 + 1; }

static BYTE* Area() { return (BYTE*)GetProcAddress(GetModuleHandleW(NULL), "HarnessPatchArea"); }

static void Write(BYTE* at, const BYTE* bytes, SIZE_T n) {
    DWORD old;
    if (!VirtualProtect(at, n, PAGE_EXECUTE_READWRITE, &old)) return;
    memcpy(at, bytes, n);
    VirtualProtect(at, n, old, &old);
    FlushInstructionCache(GetCurrentProcess(), at, n);
}

static void Jump(BYTE* at, BYTE op) {
    BYTE b[5] = {op};
    LONG rel = (LONG)((BYTE*)PatchTarget - (at + 5));
    memcpy(b + 1, &rel, 4);
    Write(at, b, sizeof b);
}

#ifdef PATCH_LEGACY
static DWORD WINAPI Late(LPVOID) {
    Sleep(500);
    if (BYTE* a = Area()) Jump(a + 64, 0xE9);
    return 0;
}
#endif

BOOL APIENTRY DllMain(HMODULE, DWORD reason, LPVOID) {
    if (reason != DLL_PROCESS_ATTACH) return TRUE;
    BYTE* a = Area();
    if (!a) return TRUE;
#ifdef PATCH_LEGACY
    Jump(a, 0xE9);
    HANDLE t = CreateThread(NULL, 0, Late, NULL, 0, NULL);
    if (t) CloseHandle(t);
#else
    Jump(a + 2, 0xE8);
    static const BYTE ud2[2] = {0x0F, 0x0B};
    Write(a + 128, ud2, sizeof ud2);
#endif
    return TRUE;
}

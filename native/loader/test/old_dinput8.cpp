// Stands in for another mod's dinput8.dll kept beside the Riftstone loader under another name (DDDA Tweak, an ASI
// loader: dinput8_chain.dll, the name `loader install` and the README give it): it answers DirectInput8Create
// through Windows' own dinput8 and counts the calls.  The harness prints whether it was loaded before the game's
// first DirectInput8Create (as it is without Riftstone, where Windows loads it before the game starts) and how many
// calls reached it (run_tests.py: test_chain_other_mod).
#define WIN32_LEAN_AND_MEAN
#include <Windows.h>

static volatile LONG g_calls = 0;

typedef HRESULT(WINAPI* Create_t)(HINSTANCE, DWORD, const GUID&, void**, void*);

extern "C" HRESULT WINAPI DirectInput8Create(HINSTANCE inst, DWORD version, const GUID& riid, void** out, void* outer) {
    InterlockedIncrement(&g_calls);
    wchar_t sys[MAX_PATH];
    GetSystemDirectoryW(sys, MAX_PATH);
    lstrcatW(sys, L"\\dinput8.dll");
    HMODULE lib = LoadLibraryW(sys);
    Create_t real = lib ? (Create_t)GetProcAddress(lib, "DirectInput8Create") : NULL;
    return real ? real(inst, version, riid, out, outer) : E_FAIL;
}
#pragma comment(linker, "/export:DirectInput8Create=_DirectInput8Create@20")

extern "C" __declspec(dllexport) LONG OldDinput8_Calls() { return g_calls; }

BOOL APIENTRY DllMain(HMODULE, DWORD, LPVOID) { return TRUE; }

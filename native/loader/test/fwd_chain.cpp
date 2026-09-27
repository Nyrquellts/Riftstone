// A "chained dinput8" whose DirectInput8Create is only a forwarder to dinput8.DirectInput8Create.  In a
// game folder with the Riftstone proxy, the loaded dinput8.dll is the loader, so following the chain
// leads back into the loader's own export: run_tests.py checks that it is refused.
#define WIN32_LEAN_AND_MEAN
#include <Windows.h>

#pragma comment(linker, "/export:DirectInput8Create=dinput8.DirectInput8Create")

BOOL APIENTRY DllMain(HMODULE, DWORD, LPVOID) { return TRUE; }

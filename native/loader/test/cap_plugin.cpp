// A stand-in for enemy_cap under another file name (run_tests.py copies it in as 01_enemy_cap.asi): it
// exports EnemyCap_Slots, the way the real plugin says how many enemy slots it moved to sSetManager's
// tail, and patches nothing.  The DDDA-layout stand-in game fills those moved slots itself.
#define WIN32_LEAN_AND_MEAN
#include <Windows.h>

extern "C" __declspec(dllexport) int EnemyCap_Slots() { return 30; }

BOOL APIENTRY DllMain(HMODULE, DWORD, LPVOID) { return TRUE; }

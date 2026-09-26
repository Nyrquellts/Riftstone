// A stand-in for a plugin with a bug: harness.exe calls RiftstoneTestBoom, which faults inside this
// module, so run_tests.py can check that the crash report names the plugin and that two start-up
// crashes in it put it in quarantine.
#define WIN32_LEAN_AND_MEAN
#include <Windows.h>

extern "C" __declspec(dllexport) void RiftstoneTestBoom() {
    volatile int* p = (volatile int*)0x20;
    *p = 1;
}

BOOL APIENTRY DllMain(HMODULE, DWORD, LPVOID) { return TRUE; }

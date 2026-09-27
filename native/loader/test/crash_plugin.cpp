// A stand-in for a plugin with a bug: harness.exe calls RiftstoneTestBoom, which faults inside this
// module, or RiftstoneTestDeep, which runs out of stack inside it, so run_tests.py can check that the
// crash report names the plugin and that two start-up crashes in it put it in quarantine.
#define WIN32_LEAN_AND_MEAN
#include <Windows.h>

extern "C" __declspec(dllexport) void RiftstoneTestBoom() {
    volatile int* p = (volatile int*)0x20;
    *p = 1;
}

#pragma warning(push)
#pragma warning(disable : 4717)   // recursive on every path: it is meant to use up the stack
static __declspec(noinline) int Deeper(volatile int* depth) {
    volatile char pad[256];
    pad[0] = (char)++*depth;
    return Deeper(depth) + pad[0];
}
#pragma warning(pop)

extern "C" __declspec(dllexport) void RiftstoneTestDeep() {
    volatile int depth = 0;
    Deeper(&depth);
}

BOOL APIENTRY DllMain(HMODULE, DWORD, LPVOID) { return TRUE; }

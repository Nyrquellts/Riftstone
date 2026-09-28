// stage_enemies_stub -- the host process for stage_enemies_harness_core.dll.
//
// Windows fills the low address space of a new process, so DDDA.exe's fixed range
// (0x00400000 + 0x160C000) cannot be allocated later.  This exe takes that range as its own image
// instead: based at 0x00400000 with a large zero-filled section, no CRT, no TLS.  It never runs again
// once the core DLL maps DDDA.exe over it and leaves through ExitProcess.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#pragma bss_seg(".reserve")
static char g_reserve[0x1700000];
#pragma bss_seg()

extern "C" void __stdcall Start() {
    volatile char* keep = g_reserve;  // keep the section
    keep[0] = 0;
    HMODULE core = LoadLibraryW(L"stage_enemies_harness_core.dll");
    FARPROC main = core ? GetProcAddress(core, "HarnessMain") : nullptr;
    if (!main) ExitProcess(3);
    ((void(__stdcall*)(void))main)();  // does not return
    ExitProcess(4);
}

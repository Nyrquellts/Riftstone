// A minimal Ninput plugin for the offline harness: proves the SDK handshake end to end. It reads
// the ABI version, calls back through the interface (input->get_state), logs a line, and drops a
// marker file next to itself so the test driver can confirm Ninput_Initialize actually ran.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <cstdio>

#include "ninput.h"

static HMODULE g_self;

extern "C" __declspec(dllexport) int Ninput_Initialize(const NinputInterface* nyr) {
    if (!nyr || nyr->abi_version != NINPUT_ABI_VERSION) return 0;

    NinputPad pad{};
    int r = nyr->input->get_state(0, &pad);  // exercise the interface (result is fine either way)
    char msg[128];
    _snprintf_s(msg, sizeof msg, _TRUNCATE,
                "marker_plugin: handshake ok (abi %u); input->get_state(0) => %d", nyr->abi_version, r);
    nyr->log(msg);

    wchar_t path[MAX_PATH];
    GetModuleFileNameW(g_self, path, MAX_PATH);
    if (wchar_t* s = wcsrchr(path, L'\\')) *s = 0;
    wcscat_s(path, L"\\marker.ok");
    HANDLE h = CreateFileW(path, GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h != INVALID_HANDLE_VALUE) {
        DWORD w;
        WriteFile(h, "ok", 2, &w, nullptr);
        CloseHandle(h);
    }
    return 1;
}

BOOL APIENTRY DllMain(HMODULE self, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) g_self = self;
    return TRUE;
}

// Stands in for the game against the xinput1_3 personality (no game launched). Loads the proxy
// from its own folder and fetches XInputGetState by ORDINAL 2 -- exactly how DDDA.exe/DDO.exe
// import it -- then calls it. Loading the proxy runs its DllMain, which boots the Ninput core and
// loads the marker plugin. The Python driver asserts on the printed lines and the marker file.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <cstdio>

typedef DWORD(WINAPI* XInputGetState_t)(DWORD, void*);

int main() {
    wchar_t exe[MAX_PATH];
    GetModuleFileNameW(nullptr, exe, MAX_PATH);
    if (wchar_t* s = wcsrchr(exe, L'\\')) *s = 0;
    SetCurrentDirectoryW(exe);

    HMODULE m = LoadLibraryW(L"xinput1_3.dll");  // resolves to the proxy in this folder
    std::printf("proxy-load %s\n", m ? "ok" : "fail");
    if (!m) return 1;

    auto get = reinterpret_cast<XInputGetState_t>(GetProcAddress(m, MAKEINTRESOURCEA(2)));
    std::printf("ordinal2 %s\n", get ? "resolved" : "missing");
    if (!get) return 1;

    struct {
        DWORD packet;
        struct { WORD buttons; BYTE lt, rt; SHORT lx, ly, rx, ry; } pad;
    } st{};
    DWORD r = get(0, &st);
    // 1167 == ERROR_DEVICE_NOT_CONNECTED: the call reached the real system XInput through the
    // proxy. 0 would mean a controller is actually plugged in. Either proves forwarding works.
    std::printf("getstate %lu\n", r);
    return 0;
}

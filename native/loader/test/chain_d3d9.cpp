// A stand-in for DXVK's d3d9.dll in the loader's tests ([d3d9] chain = riftstone\dxvk\d3d9.dll).
//
// Direct3DCreate9 writes what it saw into chain-called.txt beside the DLL (that it was called, the SDK
// version, and the DXVK_LOG_PATH and DXVK_CONFIG_FILE the loader set up), then hands out Windows' own
// Direct3D 9 object, loaded by its full path from the system folder.  With a file named "fail" beside the
// DLL it returns nothing instead, as DXVK does when it finds no Vulkan device it can use.  Nothing else.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>

struct IDirect3D9;
typedef IDirect3D9*(WINAPI* Direct3DCreate9_t)(UINT);

static HMODULE g_self;

static void Folder(wchar_t* out, size_t cap) {
    GetModuleFileNameW(g_self, out, (DWORD)cap);
    wchar_t* slash = wcsrchr(out, L'\\');
    if (slash) *slash = 0;
}

extern "C" IDirect3D9* WINAPI Direct3DCreate9(UINT sdk) {
    wchar_t dir[MAX_PATH], note[MAX_PATH], fail[MAX_PATH], logPath[MAX_PATH] = L"", conf[MAX_PATH] = L"";
    Folder(dir, _countof(dir));
    GetEnvironmentVariableW(L"DXVK_LOG_PATH", logPath, _countof(logPath));
    GetEnvironmentVariableW(L"DXVK_CONFIG_FILE", conf, _countof(conf));
    _snwprintf_s(note, _countof(note), _TRUNCATE, L"%s\\chain-called.txt", dir);
    FILE* f = NULL;
    if (_wfopen_s(&f, note, L"a, ccs=UTF-8") == 0 && f) {
        fwprintf(f, L"called sdk=%u\nDXVK_LOG_PATH=%s\nDXVK_CONFIG_FILE=%s\n", sdk, logPath, conf);
        fclose(f);
    }
    _snwprintf_s(fail, _countof(fail), _TRUNCATE, L"%s\\fail", dir);
    if (GetFileAttributesW(fail) != INVALID_FILE_ATTRIBUTES) return NULL;
    wchar_t sys[MAX_PATH];
    GetSystemDirectoryW(sys, MAX_PATH);
    wcscat_s(sys, L"\\d3d9.dll");
    HMODULE real = LoadLibraryW(sys);
    Direct3DCreate9_t create = real ? (Direct3DCreate9_t)GetProcAddress(real, "Direct3DCreate9") : NULL;
    return create ? create(sdk) : NULL;
}

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        g_self = module;
        DisableThreadLibraryCalls(module);
    }
    return TRUE;
}

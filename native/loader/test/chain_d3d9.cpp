// A stand-in for DXVK's d3d9.dll in the loader's tests ([d3d9] chain = riftstone\dxvk\d3d9.dll).
//
// Direct3DCreate9 writes what it saw into chain-called.txt beside the DLL (that it was called, the SDK
// version, and the DXVK_LOG_PATH and DXVK_CONFIG_FILE the loader set up), then hands out Windows' own
// Direct3D 9 object, loaded by its full path from the system folder.  With a file named "fail" beside the
// DLL it returns nothing instead, as DXVK does when it finds no Vulkan device it can use.  With a file named
// "lost" beside it, its devices' Present and TestCooperativeLevel say D3DERR_DEVICELOST while the process's
// RIFTSTONE_TEST_DEVICE_LOST is 1 (the harness's hanglost): their slots in the object's and each device's vtable
// are replaced before the loader sees them, so the loader's hooks call these as the real ones.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>

struct IDirect3D9;
typedef IDirect3D9*(WINAPI* Direct3DCreate9_t)(UINT);

static HMODULE g_self;

typedef long(__stdcall* CreateDevice_t)(IDirect3D9*, UINT, int, HWND, DWORD, void*, void**);
typedef long(__stdcall* Present_t)(void*, const RECT*, const RECT*, HWND, const void*);
typedef long(__stdcall* Tcl_t)(void*);
static CreateDevice_t g_createDevice;
static Present_t g_present;
static Tcl_t g_tcl;
static const long DEVICELOST = (long)0x88760868;

static BOOL Patch(void** table, int index, void* hook, void** real) {
    if (table[index] == hook) return TRUE;                   // a table shared by every object of its class
    DWORD old;
    if (!VirtualProtect(&table[index], sizeof(void*), PAGE_READWRITE, &old)) return FALSE;
    *real = table[index];
    table[index] = hook;
    VirtualProtect(&table[index], sizeof(void*), old, &old);
    return TRUE;
}

static BOOL Lost() {
    wchar_t v[4] = L"";
    return GetEnvironmentVariableW(L"RIFTSTONE_TEST_DEVICE_LOST", v, 4) && v[0] == L'1';
}

static long __stdcall LostPresent(void* dev, const RECT* a, const RECT* b, HWND w, const void* r) {
    return Lost() ? DEVICELOST : g_present(dev, a, b, w, r);
}

static long __stdcall LostTcl(void* dev) {
    return Lost() ? DEVICELOST : g_tcl(dev);
}

static long __stdcall LostCreateDevice(IDirect3D9* d3d, UINT adapter, int type, HWND w, DWORD flags, void* pp, void** out) {
    long hr = g_createDevice(d3d, adapter, type, w, flags, pp, out);
    if (hr >= 0 && out && *out) {
        void** table = *(void***)*out;
        Patch(table, 3, (void*)LostTcl, (void**)&g_tcl);         // TestCooperativeLevel
        Patch(table, 17, (void*)LostPresent, (void**)&g_present); // Present
    }
    return hr;
}

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
    IDirect3D9* d3d = create ? create(sdk) : NULL;
    wchar_t lost[MAX_PATH];
    _snwprintf_s(lost, _countof(lost), _TRUNCATE, L"%s\\lost", dir);
    if (d3d && GetFileAttributesW(lost) != INVALID_FILE_ATTRIBUTES)
        Patch(*(void***)d3d, 16, (void*)LostCreateDevice, (void**)&g_createDevice);   // IDirect3D9::CreateDevice
    return d3d;
}

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        g_self = module;
        DisableThreadLibraryCalls(module);
    }
    return TRUE;
}

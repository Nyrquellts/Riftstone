// Offline proof that the display arbiter follows the Riftstone loader's [d3d9] chain (no game).
//
//   ninput_chain_test.exe <root>
//
// <root> holds riftstone_loader.ini naming riftstone\dxvk\d3d9.dll, a stand-in for DXVK's d3d9.dll (the
// loader's test DLL: its Direct3DCreate9 hands out Windows' own Direct3D 9), set up by run_tests.py.  Only
// display_watch_chain runs here, not the hook on Windows' own d3d9.dll, so CreateDevice can only be watched if
// the chained DLL's Direct3DCreate9 -- the call the loader makes for the game -- went through Ninput's hook.
// Then the refusals: a chain outside the game folder, one that is not there, none at all.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <d3d9.h>

#include <cstdio>
#include <cwchar>

#include "display_arbiter.hpp"
#include "hook_registry.hpp"
#include "ninput.h"

static int g_fails = 0;
static void check(bool ok, const char* label) {
    std::printf("  %s  %s\n", ok ? "pass" : "FAIL", label);
    if (!ok) ++g_fails;
}

static void ini(const wchar_t* root, const wchar_t* chain) {
    wchar_t path[MAX_PATH];
    _snwprintf_s(path, _countof(path), _TRUNCATE, L"%ls\\riftstone_loader.ini", root);
    WritePrivateProfileStringW(L"d3d9", L"chain", chain, path);
}

using Create9_t = IDirect3D9*(WINAPI*)(UINT);

int wmain(int argc, wchar_t** argv) {
    setvbuf(stdout, nullptr, _IONBF, 0);
    if (argc < 2) {
        std::puts("usage: ninput_chain_test <root>");
        return 2;
    }
    const wchar_t* root = argv[1];
    ninput::make_interface(NINPUT_GAME_UNKNOWN, 0, [](const char* m) { std::printf("    log: %s\n", m); });

    std::puts("refused: outside the game folder, not there, not set");
    ini(root, L"..\\elsewhere\\d3d9.dll");
    check(!ninput::display_watch_chain(root), "a chain outside the game folder is not watched");
    ini(root, L"riftstone\\dxvk\\none.dll");
    check(!ninput::display_watch_chain(root), "a chain that is not there is not watched");
    ini(root, L"");
    check(!ninput::display_watch_chain(root) && !ninput::display_chain_hooked(), "no chain: nothing watched");

    std::puts("the chain named in riftstone_loader.ini");
    ini(root, L"riftstone\\dxvk\\d3d9.dll   ; DXVK");
    check(ninput::display_watch_chain(root) && ninput::display_chain_hooked(),
          "riftstone\\dxvk\\d3d9.dll (with a trailing comment) is watched");
    wchar_t dll[MAX_PATH];
    _snwprintf_s(dll, _countof(dll), _TRUNCATE, L"%ls\\riftstone\\dxvk\\d3d9.dll", root);
    HMODULE chain = GetModuleHandleW(dll);
    auto create = chain ? reinterpret_cast<Create9_t>(GetProcAddress(chain, "Direct3DCreate9")) : nullptr;
    check(create != nullptr, "the chained DLL is loaded, with its Direct3DCreate9");
    check(!ninput::display_create_device_hooked(), "CreateDevice is not watched before the game creates Direct3D");
    IDirect3D9* d3d = create ? create(D3D_SDK_VERSION) : nullptr;
    check(d3d != nullptr, "the chained Direct3DCreate9 (what the loader calls for the game) returned an object");
    check(ninput::display_create_device_hooked(),
          "it went through Ninput's hook: CreateDevice is watched on the object it returned, so lost/reset "
          "broadcasts reach the chained runtime's device");
    if (d3d) d3d->Release();

    std::printf("\n%s (%d checks failed)\n", g_fails ? "FAILED" : "ALL PASSED", g_fails);
    return g_fails ? 1 : 0;
}

#include "display_arbiter.hpp"

#include <cwchar>
#include <mutex>
#include <vector>

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <d3d9.h>

#include <safetyhook/easy.hpp>
#include <safetyhook/inline_hook.hpp>

#include "ninput_core.hpp"

namespace ninput {
namespace {

// COM vtable indices (stable ABI): IDirect3D9::CreateDevice and IDirect3DDevice9::Reset.
constexpr int VT_CREATE_DEVICE = 16;
constexpr int VT_RESET = 16;

struct DisplayCb {
    int handle;
    NinputDisplayFn on_lost;
    NinputDisplayFn on_reset;
    void* user;
};

std::mutex g_m;
std::vector<DisplayCb> g_cbs;
int g_next = 1;
IDirect3DDevice9* g_device = nullptr;

safetyhook::InlineHook g_hCreate9;
safetyhook::InlineHook g_hCreate9Chain;   // the Riftstone loader's [d3d9] chain (e.g. DXVK's d3d9.dll)
safetyhook::InlineHook g_hCreateDevice;
safetyhook::InlineHook g_hReset;

using Create9_t = IDirect3D9*(WINAPI*)(UINT);
using CreateDevice_t = HRESULT(WINAPI*)(IDirect3D9*, UINT, D3DDEVTYPE, HWND, DWORD,
                                        D3DPRESENT_PARAMETERS*, IDirect3DDevice9**);
using Reset_t = HRESULT(WINAPI*)(IDirect3DDevice9*, D3DPRESENT_PARAMETERS*);

void* vtable_entry(void* iface, int index) {
    return (*reinterpret_cast<void***>(iface))[index];
}

void broadcast(bool lost) {
    std::vector<DisplayCb> local;
    {
        std::scoped_lock lk(g_m);
        local = g_cbs;
    }
    for (const auto& cb : local) {
        NinputDisplayFn fn = lost ? cb.on_lost : cb.on_reset;
        if (fn) fn(g_device, cb.user);
    }
}

void hook_reset(IDirect3DDevice9* dev);

HRESULT WINAPI Reset_detour(IDirect3DDevice9* dev, D3DPRESENT_PARAMETERS* pp) {
    // Before the device is reset every plugin releases its D3DPOOL_DEFAULT resources; after, they
    // recreate them. Doing this in one owned place is what stops the reset-time crashes.
    broadcast(/*lost=*/true);
    HRESULT hr = g_hReset.stdcall<HRESULT>(dev, pp);
    broadcast(/*lost=*/false);
    return hr;
}

void hook_reset(IDirect3DDevice9* dev) {
    if (g_hReset || !dev) return;
    void* target = vtable_entry(dev, VT_RESET);
    auto made = safetyhook::InlineHook::create(target, reinterpret_cast<void*>(&Reset_detour));
    if (made) {
        g_hReset = std::move(*made);
        core_log("display  Reset hooked at 0x%p (device 0x%p)", target, (void*)dev);
    } else {
        core_log("display  could not hook Reset (error %d)", (int)made.error().type);
    }
}

HRESULT WINAPI CreateDevice_detour(IDirect3D9* self, UINT adapter, D3DDEVTYPE type, HWND focus,
                                   DWORD flags, D3DPRESENT_PARAMETERS* pp, IDirect3DDevice9** out) {
    HRESULT hr = g_hCreateDevice.stdcall<HRESULT>(self, adapter, type, focus, flags, pp, out);
    if (SUCCEEDED(hr) && out && *out) {
        g_device = *out;
        hook_reset(*out);
    }
    return hr;
}

void hook_create_device(IDirect3D9* d3d) {
    if (g_hCreateDevice || !d3d) return;
    void* target = vtable_entry(d3d, VT_CREATE_DEVICE);
    auto made = safetyhook::InlineHook::create(target, reinterpret_cast<void*>(&CreateDevice_detour));
    if (made) {
        g_hCreateDevice = std::move(*made);
        core_log("display  CreateDevice hooked at 0x%p", target);
    } else {
        core_log("display  could not hook CreateDevice (error %d)", (int)made.error().type);
    }
}

IDirect3D9* WINAPI Create9_detour(UINT sdk) {
    IDirect3D9* d3d = g_hCreate9.stdcall<IDirect3D9*>(sdk);
    if (d3d) hook_create_device(d3d);
    return d3d;
}

IDirect3D9* WINAPI Create9Chain_detour(UINT sdk) {
    IDirect3D9* d3d = g_hCreate9Chain.stdcall<IDirect3D9*>(sdk);
    if (d3d) hook_create_device(d3d);
    return d3d;
}

}  // namespace

// The Riftstone loader can give the game another Direct3D 9 ([d3d9] chain in riftstone_loader.ini beside the
// game, e.g. DXVK's d3d9.dll in riftstone\dxvk): its Direct3DCreate9 then goes to that DLL and never reaches
// the d3d9.dll hooked above, so that DLL's Direct3DCreate9 is hooked as well.  The same checks as the
// loader's: a .dll inside the game folder.  The loader decides whether it is used (not in safe mode, not a
// 64-bit one, not beside a d3d9.dll in the game folder); a hook on a chain it leaves unused never fires.
bool display_watch_chain(const wchar_t* root) {
    if (g_hCreate9Chain || !root || !root[0]) return static_cast<bool>(g_hCreate9Chain);
    wchar_t ini[MAX_PATH * 2], chain[MAX_PATH] = L"", joined[MAX_PATH * 2], full[MAX_PATH];
    _snwprintf_s(ini, _countof(ini), _TRUNCATE, L"%ls\\riftstone_loader.ini", root);
    GetPrivateProfileStringW(L"d3d9", L"chain", L"", chain, MAX_PATH, ini);
    if (wchar_t* semi = wcschr(chain, L';')) *semi = 0;
    size_t n = wcslen(chain);
    while (n && (chain[n - 1] == L' ' || chain[n - 1] == L'\t')) chain[--n] = 0;
    if (!chain[0]) return false;
    size_t rootLen = wcslen(root);
    DWORD got = 0;
    if (!wcschr(chain, L':') && chain[0] != L'\\' && chain[0] != L'/' &&
        _snwprintf_s(joined, _countof(joined), _TRUNCATE, L"%ls\\%ls", root, chain) > 0)
        got = GetFullPathNameW(joined, MAX_PATH, full, nullptr);
    if (!got || got >= MAX_PATH || _wcsnicmp(full, root, rootLen) != 0 || full[rootLen] != L'\\') {
        core_log("display  [d3d9] chain = %ls is not inside the game folder; not watched", chain);
        return false;
    }
    DWORD attrs = GetFileAttributesW(full);
    if (attrs == INVALID_FILE_ATTRIBUTES || (attrs & FILE_ATTRIBUTE_DIRECTORY)) {
        core_log("display  [d3d9] chain = %ls does not exist; not watched", chain);
        return false;
    }
    // Taken before the chain loads: with two modules named d3d9.dll, the name may find either.
    HMODULE already = GetModuleHandleW(L"d3d9.dll");
    HMODULE lib = LoadLibraryExW(full, nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!lib || lib == already) {
        core_log("display  [d3d9] chain = %ls %s; not watched", chain,
                 lib ? "is the d3d9.dll the game has already" : "could not be loaded");
        return false;
    }
    void* create = reinterpret_cast<void*>(GetProcAddress(lib, "Direct3DCreate9"));
    if (!create) {
        core_log("display  [d3d9] chain = %ls has no Direct3DCreate9; not watched", chain);
        return false;
    }
    auto made = safetyhook::InlineHook::create(create, reinterpret_cast<void*>(&Create9Chain_detour));
    if (!made) {
        core_log("display  could not hook the chained Direct3DCreate9 (error %d)", (int)made.error().type);
        return false;
    }
    g_hCreate9Chain = std::move(*made);
    core_log("display  also watching %ls ([d3d9] chain in riftstone_loader.ini)", full);
    return true;
}

bool display_chain_hooked() { return static_cast<bool>(g_hCreate9Chain); }

void display_arbiter_start() {
    display_watch_chain(core_root());
    HMODULE d3d9 = LoadLibraryW(L"d3d9.dll");
    if (!d3d9) {
        core_log("display  d3d9.dll not present (error %lu); arbiter idle", GetLastError());
        return;
    }
    void* create = reinterpret_cast<void*>(GetProcAddress(d3d9, "Direct3DCreate9"));
    if (!create) {
        core_log("display  Direct3DCreate9 not found; arbiter idle");
        return;
    }
    auto made = safetyhook::InlineHook::create(create, reinterpret_cast<void*>(&Create9_detour));
    if (made) {
        g_hCreate9 = std::move(*made);
        core_log("display  Direct3DCreate9 hooked; waiting for the game to create its device");
    } else {
        core_log("display  could not hook Direct3DCreate9 (error %d)", (int)made.error().type);
    }
}

int display_register(NinputDisplayFn on_lost, NinputDisplayFn on_reset, void* user) {
    if (!on_lost && !on_reset) return NINPUT_ERR_BADARG;
    std::scoped_lock lk(g_m);
    int h = g_next++;
    g_cbs.push_back({h, on_lost, on_reset, user});
    return h;
}

void display_unregister(int handle) {
    std::scoped_lock lk(g_m);
    for (auto it = g_cbs.begin(); it != g_cbs.end(); ++it) {
        if (it->handle == handle) {
            g_cbs.erase(it);
            return;
        }
    }
}

void* display_device() { return g_device; }

bool display_create9_hooked() { return static_cast<bool>(g_hCreate9); }
bool display_create_device_hooked() { return static_cast<bool>(g_hCreateDevice); }
bool display_reset_hooked() { return static_cast<bool>(g_hReset); }
int display_callback_count() {
    std::scoped_lock lk(g_m);
    return (int)g_cbs.size();
}
void display_simulate_reset() {
    broadcast(true);
    broadcast(false);
}

}  // namespace ninput

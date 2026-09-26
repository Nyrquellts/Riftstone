// Offline proof of the D3D9 arbiter (no game). Direct3DCreate9 creates the D3D9 object without a
// GPU, so the create9 -> CreateDevice hook chain and the lost/reset broadcast are all driven for
// real here. A genuine device+Reset on a hidden window is attempted too, and SKIPs (not fails) if
// this machine has no usable D3D9 device.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <d3d9.h>

#include <cstdio>
#include <vector>

#include "display_arbiter.hpp"
#include "hook_registry.hpp"
#include "ninput.h"

static int g_fails = 0;
static void check(bool ok, const char* label) {
    std::printf("  %s  %s\n", ok ? "pass" : "FAIL", label);
    if (!ok) ++g_fails;
}

static int g_lost = 0, g_reset = 0;
static std::vector<int> g_order;
static void on_lost(void*, void*) { g_order.push_back(0); ++g_lost; }
static void on_reset(void*, void*) { g_order.push_back(1); ++g_reset; }

int main() {
    setvbuf(stdout, nullptr, _IONBF, 0);  // unbuffered so a crash cannot swallow the last line
    const NinputInterface* n = ninput::make_interface(NINPUT_GAME_UNKNOWN, 0,
                                                      [](const char* m) { std::printf("    log: %s\n", m); });
    ninput::display_arbiter_start();
    check(ninput::display_create9_hooked(), "Direct3DCreate9 hooked");

    std::puts("create9 -> CreateDevice hook chain (no GPU needed)");
    IDirect3D9* d3d = Direct3DCreate9(D3D_SDK_VERSION);
    check(d3d != nullptr, "Direct3DCreate9 returned an IDirect3D9 through the hook");
    check(ninput::display_create_device_hooked(), "the detour hooked CreateDevice on the returned object");

    std::puts("broadcast ordering (simulated reset)");
    int h1 = n->display->register_callbacks(on_lost, on_reset, nullptr);
    int h2 = n->display->register_callbacks(on_lost, on_reset, nullptr);
    check(h1 > 0 && h2 > 0 && ninput::display_callback_count() == 2, "two plugins registered lost/reset callbacks");
    g_order.clear();
    ninput::display_simulate_reset();
    check((g_order == std::vector<int>{0, 0, 1, 1}), "all 'lost' fired before any 'reset'");
    n->display->unregister(h1);
    n->display->unregister(h2);
    check(ninput::display_callback_count() == 0, "callbacks unregister");

    std::puts("real device Reset (best-effort; SKIP without a D3D9 device)");
    bool device_ok = false;
    if (d3d) {
        WNDCLASSW wc{};
        wc.lpfnWndProc = DefWindowProcW;
        wc.hInstance = GetModuleHandleW(nullptr);
        wc.lpszClassName = L"NinputD3DTest";
        RegisterClassW(&wc);
        HWND hwnd = CreateWindowW(wc.lpszClassName, L"", WS_OVERLAPPEDWINDOW, 0, 0, 320, 240, nullptr, nullptr,
                                  wc.hInstance, nullptr);
        D3DPRESENT_PARAMETERS pp{};
        pp.Windowed = TRUE;
        pp.SwapEffect = D3DSWAPEFFECT_DISCARD;
        pp.BackBufferFormat = D3DFMT_UNKNOWN;
        pp.hDeviceWindow = hwnd;
        IDirect3DDevice9* dev = nullptr;
        HRESULT hr = d3d->CreateDevice(D3DADAPTER_DEFAULT, D3DDEVTYPE_HAL, hwnd,
                                       D3DCREATE_SOFTWARE_VERTEXPROCESSING, &pp, &dev);
        if (SUCCEEDED(hr) && dev) {
            device_ok = true;
            check(ninput::display_reset_hooked(), "Reset hooked once a real device exists");
            check(ninput::display_device() == dev, "arbiter recorded the created device");
            int h = n->display->register_callbacks(on_lost, on_reset, nullptr);
            int before_lost = g_lost, before_reset = g_reset;
            dev->Reset(&pp);
            check(g_lost > before_lost && g_reset > before_reset,
                  "a real device Reset ran the lost/reset broadcast around it");
            n->display->unregister(h);
            dev->Release();
        }
        if (hwnd) DestroyWindow(hwnd);
        d3d->Release();
    }
    if (!device_ok) std::puts("  SKIP  real-device Reset (no usable D3D9 device on this machine)");

    std::printf("\n%s (%d checks failed)\n", g_fails ? "FAILED" : "ALL PASSED", g_fails);
    return g_fails ? 1 : 0;
}

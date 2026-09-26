// Offline proof of the Ninput hook arbiter -- no game involved. Two "plugins" mid-hook the same
// function and both run, in order; one unregisters and drops out; an exclusive detour is granted
// once and refused the second time. Real SafetyHook hooks on real functions in this process.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <cstdio>
#include <vector>

#include "hook_registry.hpp"
#include "ninput.h"

static int g_fails = 0;
static void check(bool ok, const char* label) {
    std::printf("  %s  %s\n", ok ? "pass" : "FAIL", label);
    if (!ok) ++g_fails;
}

// Targets the hooks land on. Optimisation off so they keep a real prologue to relocate.
#pragma optimize("", off)
static volatile int g_target_side = 0;
__declspec(noinline) static void target_mid(int a, int b) {
    g_target_side += a * 3 + b;  // real work, several bytes at the entry
    g_target_side ^= (a << 1);
}
__declspec(noinline) static int target_excl(int x) {
    g_target_side += x;
    return x * 2 + 1;
}
#pragma optimize("", on)

static std::vector<int> g_order;  // which plugin callbacks ran, in the order they ran
static void cb_plugin_a(NinputRegs32*, void*) { g_order.push_back(1); }
static void cb_plugin_b(NinputRegs32*, void*) { g_order.push_back(2); }
static void cb_plugin_c(NinputRegs32*, void*) { g_order.push_back(3); }

static int (__cdecl* g_excl_original)(int) = nullptr;
static int g_detour_calls = 0;
static int __cdecl detour_excl(int x) {
    ++g_detour_calls;
    return g_excl_original(x) + 100;  // call through to the original, then adjust
}

int main() {
    const NinputInterface* n = ninput::make_interface(NINPUT_GAME_UNKNOWN, 0,
                                                      [](const char* m) { std::printf("    log: %s\n", m); });
    check(n && n->abi_version == NINPUT_ABI_VERSION, "interface built, ABI v1");

    const auto addr_mid = reinterpret_cast<uintptr_t>(&target_mid);
    const auto addr_excl = reinterpret_cast<uintptr_t>(&target_excl);

    std::puts("two plugins share one address");
    int ha = n->hooks->register_mid(addr_mid, cb_plugin_a, nullptr, "plugin_a");
    int hb = n->hooks->register_mid(addr_mid, cb_plugin_b, nullptr, "plugin_b");
    check(ha > 0 && hb > 0, "both mid registrations accepted");
    check(ninput::mid_hook_count() == 1, "one real hook installed for the shared address");
    g_order.clear();
    target_mid(2, 5);
    check((g_order == std::vector<int>{1, 2}), "both callbacks ran, in registration order");

    std::puts("a plugin leaves, another joins");
    int hc = n->hooks->register_mid(addr_mid, cb_plugin_c, nullptr, "plugin_c");
    n->hooks->unregister(ha);  // plugin_a leaves
    check(hc > 0 && ninput::mid_hook_count() == 1, "still one shared hook after churn");
    g_order.clear();
    target_mid(1, 1);
    check((g_order == std::vector<int>{2, 3}), "only the remaining callbacks ran (a gone, b then c)");

    n->hooks->unregister(hb);
    n->hooks->unregister(hc);
    check(ninput::mid_hook_count() == 0, "the hook is removed once the last callback leaves");
    g_order.clear();
    target_mid(1, 1);
    check(g_order.empty(), "nothing runs after full unhook (original bytes restored)");

    std::puts("exclusive detours never clobber");
    int hi = n->hooks->register_inline(addr_excl, (void*)&detour_excl, (void**)&g_excl_original, "plugin_a");
    check(hi > 0 && g_excl_original != nullptr, "first exclusive detour granted, original returned");
    int deny = n->hooks->register_inline(addr_excl, (void*)&detour_excl, nullptr, "plugin_b");
    check(deny == NINPUT_ERR_ADDRESS_TAKEN, "second detour at the same address refused, not silently applied");
    g_detour_calls = 0;
    int got = target_excl(10);  // detour -> original(10)=21 -> +100 = 121
    check(g_detour_calls == 1 && got == 121, "detour ran and called through to the original");
    n->hooks->unregister(hi);
    check(ninput::inline_hook_count() == 0 && target_excl(10) == 21, "after unregister the original runs unmodified");

    std::printf("\n%s (%d checks failed)\n", g_fails ? "FAILED" : "ALL PASSED", g_fails);
    return g_fails ? 1 : 0;
}

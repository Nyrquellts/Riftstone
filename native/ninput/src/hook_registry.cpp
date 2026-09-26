#include "hook_registry.hpp"

#include <array>
#include <cstddef>
#include <cstdio>
#include <mutex>
#include <unordered_map>
#include <utility>
#include <vector>

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <safetyhook/context.hpp>
#include <safetyhook/easy.hpp>
#include <safetyhook/inline_hook.hpp>
#include <safetyhook/mid_hook.hpp>

#include "display_arbiter.hpp"
#include "input_engine.hpp"

// The register block the SDK exposes must match SafetyHook's Context32 byte for byte, since a
// mid callback receives &ctx reinterpreted as NinputRegs32*.
static_assert(sizeof(NinputRegs32) == sizeof(safetyhook::Context32), "Context/NinputRegs32 size");
static_assert(offsetof(NinputRegs32, eflags) == offsetof(safetyhook::Context32, eflags), "eflags");
static_assert(offsetof(NinputRegs32, eax) == offsetof(safetyhook::Context32, eax), "eax");
static_assert(offsetof(NinputRegs32, esp) == offsetof(safetyhook::Context32, esp), "esp");
static_assert(offsetof(NinputRegs32, eip) == offsetof(safetyhook::Context32, eip), "eip");

namespace ninput {
namespace {

constexpr int kMaxMidSlots = 128;

struct Callback {
    int handle;
    NinputMidFn fn;
    void* user;
};

// One shared mid-hook: the single SafetyHook hook installed at `address`, plus every plugin
// callback that asked to run there.
struct MidSlot {
    bool used = false;
    std::uintptr_t address = 0;
    std::vector<Callback> callbacks;
    safetyhook::MidHook hook;
};

struct InlineEntry {
    int handle;
    std::uintptr_t address;
    safetyhook::InlineHook hook;
    const char* owner;
};

struct Registry {
    std::recursive_mutex mutex;
    std::array<MidSlot, kMaxMidSlots> slots{};
    std::unordered_map<std::uintptr_t, InlineEntry> inlines;
    int next_handle = 1;
    LogSink sink = nullptr;

    void log(const char* msg) {
        if (sink) sink(msg);
    }
    void logf(const char* fmt, ...) {
        char buf[512];
        va_list ap;
        va_start(ap, fmt);
        _vsnprintf_s(buf, sizeof buf, _TRUNCATE, fmt, ap);
        va_end(ap);
        log(buf);
    }
};

Registry g_reg;

// A fixed pool of dispatchers, one compiled function per slot index, so the registry can route a
// dynamic address to its slot without runtime codegen. dispatch<I> runs slot I's callbacks.
void run_slot(int index, safetyhook::Context& ctx) {
    auto* regs = reinterpret_cast<NinputRegs32*>(&ctx);
    // Copy the callback list under the lock, then run outside it: a callback may register or
    // unregister, and must not deadlock or invalidate the vector we are iterating.
    std::vector<Callback> local;
    {
        std::scoped_lock lk(g_reg.mutex);
        local = g_reg.slots[index].callbacks;
    }
    for (const auto& cb : local) cb.fn(regs, cb.user);
}

template <int I>
void dispatch(safetyhook::Context& ctx) {
    run_slot(I, ctx);
}

template <std::size_t... I>
constexpr std::array<safetyhook::MidHookFn, sizeof...(I)> make_dispatch(std::index_sequence<I...>) {
    return {{&dispatch<static_cast<int>(I)>...}};
}
const auto g_dispatch = make_dispatch(std::make_index_sequence<kMaxMidSlots>{});

// ---- the C tables ----------------------------------------------------------------------

int api_register_mid(std::uintptr_t address, NinputMidFn fn, void* user, const char* owner) {
    if (!address || !fn) return NINPUT_ERR_BADARG;
    std::scoped_lock lk(g_reg.mutex);

    for (auto& slot : g_reg.slots) {
        if (slot.used && slot.address == address) {
            int h = g_reg.next_handle++;
            slot.callbacks.push_back({h, fn, user});
            g_reg.logf("mid    %s joined 0x%08X (now %zu callbacks)", owner ? owner : "?",
                       (unsigned)address, slot.callbacks.size());
            return h;
        }
    }
    for (std::size_t i = 0; i < g_reg.slots.size(); ++i) {
        MidSlot& slot = g_reg.slots[i];
        if (slot.used) continue;
        auto made = safetyhook::MidHook::create(reinterpret_cast<void*>(address), g_dispatch[i]);
        if (!made) {
            g_reg.logf("mid    %s could not hook 0x%08X (trampoline error %d)", owner ? owner : "?",
                       (unsigned)address, (int)made.error().type);
            return NINPUT_ERR_HOOK_FAILED;
        }
        slot.used = true;
        slot.address = address;
        slot.hook = std::move(*made);
        int h = g_reg.next_handle++;
        slot.callbacks.push_back({h, fn, user});
        g_reg.logf("mid    %s hooked 0x%08X (slot %zu)", owner ? owner : "?", (unsigned)address, i);
        return h;
    }
    g_reg.log("mid    refused: the shared mid-hook pool is full");
    return NINPUT_ERR_NO_SLOTS;
}

int api_register_inline(std::uintptr_t address, void* detour, void** original, const char* owner) {
    if (!address || !detour) return NINPUT_ERR_BADARG;
    std::scoped_lock lk(g_reg.mutex);

    auto it = g_reg.inlines.find(address);
    if (it != g_reg.inlines.end()) {
        g_reg.logf("inline refused: 0x%08X already detoured by %s (requested by %s)", (unsigned)address,
                   it->second.owner ? it->second.owner : "?", owner ? owner : "?");
        return NINPUT_ERR_ADDRESS_TAKEN;
    }
    auto made = safetyhook::InlineHook::create(reinterpret_cast<void*>(address), detour);
    if (!made) {
        g_reg.logf("inline %s could not hook 0x%08X (error %d)", owner ? owner : "?", (unsigned)address,
                   (int)made.error().type);
        return NINPUT_ERR_HOOK_FAILED;
    }
    int h = g_reg.next_handle++;
    if (original) *original = made->original<void*>();
    g_reg.inlines.emplace(address, InlineEntry{h, address, std::move(*made), owner});
    g_reg.logf("inline %s detoured 0x%08X", owner ? owner : "?", (unsigned)address);
    return h;
}

void api_unregister(int handle) {
    if (handle <= 0) return;
    std::scoped_lock lk(g_reg.mutex);
    for (auto& slot : g_reg.slots) {
        if (!slot.used) continue;
        for (auto cb = slot.callbacks.begin(); cb != slot.callbacks.end(); ++cb) {
            if (cb->handle != handle) continue;
            slot.callbacks.erase(cb);
            if (slot.callbacks.empty()) {
                slot.hook = {};  // restores the original bytes
                slot.used = false;
                slot.address = 0;
            }
            return;
        }
    }
    for (auto it = g_reg.inlines.begin(); it != g_reg.inlines.end(); ++it) {
        if (it->second.handle == handle) {
            g_reg.inlines.erase(it);  // InlineHook destructor restores the original bytes
            return;
        }
    }
}

// engine: a capability provider registry. Consumer setters route to the provider a verified
// plugin registered; with no provider the answer is honestly UNIMPLEMENTED (core never writes).
std::mutex g_engine_mutex;
NinputEnemyCapFn g_prov_enemy_cap = nullptr;
NinputShadowSizeFn g_prov_shadow_size = nullptr;
NinputShadowDistFn g_prov_shadow_dist = nullptr;
NinputLodMultFn g_prov_lod_mult = nullptr;

int eng_set_enemy_cap(int slots) {
    NinputEnemyCapFn fn;
    { std::scoped_lock lk(g_engine_mutex); fn = g_prov_enemy_cap; }
    return fn ? fn(slots) : NINPUT_ERR_UNIMPLEMENTED;
}
int eng_set_shadow_map_size(std::uint32_t px) {
    NinputShadowSizeFn fn;
    { std::scoped_lock lk(g_engine_mutex); fn = g_prov_shadow_size; }
    return fn ? fn(px) : NINPUT_ERR_UNIMPLEMENTED;
}
int eng_set_shadow_distance(float m) {
    NinputShadowDistFn fn;
    { std::scoped_lock lk(g_engine_mutex); fn = g_prov_shadow_dist; }
    return fn ? fn(m) : NINPUT_ERR_UNIMPLEMENTED;
}
int eng_set_lod_mult(float m) {
    NinputLodMultFn fn;
    { std::scoped_lock lk(g_engine_mutex); fn = g_prov_lod_mult; }
    return fn ? fn(m) : NINPUT_ERR_UNIMPLEMENTED;
}
int eng_provide_enemy_cap(NinputEnemyCapFn fn) { std::scoped_lock lk(g_engine_mutex); g_prov_enemy_cap = fn; return NINPUT_OK; }
int eng_provide_shadow_size(NinputShadowSizeFn fn) { std::scoped_lock lk(g_engine_mutex); g_prov_shadow_size = fn; return NINPUT_OK; }
int eng_provide_shadow_dist(NinputShadowDistFn fn) { std::scoped_lock lk(g_engine_mutex); g_prov_shadow_dist = fn; return NINPUT_OK; }
int eng_provide_lod_mult(NinputLodMultFn fn) { std::scoped_lock lk(g_engine_mutex); g_prov_lod_mult = fn; return NINPUT_OK; }

void* mem_alloc(std::size_t n) { return ::malloc(n); }
void mem_release(void* p) { ::free(p); }

NinputHooks g_hooks{api_register_mid, api_register_inline, api_unregister};
NinputEngine g_engine{};
NinputDisplay g_display{display_register, display_unregister, display_device};
NinputInput g_input{input_get_state, input_register_hotkey, input_register_transform, input_unregister};
NinputMemory g_memory{mem_alloc, mem_release};
void api_log(const char* m) { g_reg.log(m); }
NinputInterface g_interface{};

}  // namespace

const NinputInterface* make_interface(int game, std::uintptr_t image_base, LogSink sink) {
    g_reg.sink = sink;
    g_engine.game = game;
    g_engine.image_base = image_base;
    g_engine.set_enemy_cap = eng_set_enemy_cap;
    g_engine.set_shadow_map_size = eng_set_shadow_map_size;
    g_engine.set_shadow_distance = eng_set_shadow_distance;
    g_engine.set_lod_distance_multiplier = eng_set_lod_mult;
    g_engine.provide_enemy_cap = eng_provide_enemy_cap;
    g_engine.provide_shadow_map_size = eng_provide_shadow_size;
    g_engine.provide_shadow_distance = eng_provide_shadow_dist;
    g_engine.provide_lod_distance_multiplier = eng_provide_lod_mult;
    g_interface.abi_version = NINPUT_ABI_VERSION;
    g_interface.host_version = 0x000100;  // 0.1.0
    g_interface.hooks = &g_hooks;
    g_interface.engine = &g_engine;
    g_interface.display = &g_display;
    g_interface.input = &g_input;
    g_interface.memory = &g_memory;
    g_interface.log = api_log;
    return &g_interface;
}

int mid_hook_count() {
    std::scoped_lock lk(g_reg.mutex);
    int n = 0;
    for (const auto& s : g_reg.slots)
        if (s.used) ++n;
    return n;
}

int inline_hook_count() {
    std::scoped_lock lk(g_reg.mutex);
    return (int)g_reg.inlines.size();
}

}  // namespace ninput

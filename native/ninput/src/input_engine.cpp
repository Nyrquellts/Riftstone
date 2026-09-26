#include "input_engine.hpp"

#include <mutex>
#include <vector>

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "ninput_core.hpp"

namespace ninput {
namespace {

struct Hotkey {
    int handle;
    unsigned short mask;
    NinputHotkeyFn fn;
    void* user;
    bool held[4];  // per-slot edge state
};

struct Transform {
    int handle;
    NinputTransformFn fn;
    void* user;
};

std::mutex g_m;
std::vector<Hotkey> g_hotkeys;
std::vector<Transform> g_transforms;
int g_next = 1;

// The real system XInputGetState, resolved once from the actual system directory (never from the
// proxy, so input_get_state does not re-enter the wrapper).
using XInputGetState_t = DWORD(WINAPI*)(DWORD, RawXInputState*);
XInputGetState_t real_get_state() {
    static XInputGetState_t fn = [] {
        HMODULE m = load_system_dll(L"xinput1_3.dll");
        if (!m) m = load_system_dll(L"xinput1_4.dll");
        if (!m) m = load_system_dll(L"xinput9_1_0.dll");
        return m ? reinterpret_cast<XInputGetState_t>(GetProcAddress(m, "XInputGetState")) : nullptr;
    }();
    return fn;
}

void to_pad(const RawXInputState& s, NinputPad& p) {
    p.packet = s.packet;
    p.buttons = s.pad.buttons;
    p.lt = s.pad.left_trigger;
    p.rt = s.pad.right_trigger;
    p.lx = s.pad.lx;
    p.ly = s.pad.ly;
    p.rx = s.pad.rx;
    p.ry = s.pad.ry;
}

void from_pad(const NinputPad& p, RawXInputState& s) {
    s.pad.buttons = p.buttons;
    s.pad.left_trigger = p.lt;
    s.pad.right_trigger = p.rt;
    s.pad.lx = p.lx;
    s.pad.ly = p.ly;
    s.pad.rx = p.rx;
    s.pad.ry = p.ry;
}

}  // namespace

int input_get_state(int slot, NinputPad* out) {
    if (!out || slot < 0 || slot > 3) return NINPUT_ERR_BADARG;
    XInputGetState_t get = real_get_state();
    if (!get) return NINPUT_ERR_UNIMPLEMENTED;
    RawXInputState s{};
    DWORD r = get((DWORD)slot, &s);
    if (r != 0) return (int)r;  // ERROR_DEVICE_NOT_CONNECTED (1167) etc.
    NinputPad p{};
    to_pad(s, p);
    *out = p;
    return NINPUT_OK;
}

int input_register_hotkey(unsigned short buttons, NinputHotkeyFn fn, void* user) {
    if (!buttons || !fn) return NINPUT_ERR_BADARG;
    std::scoped_lock lk(g_m);
    int h = g_next++;
    g_hotkeys.push_back(Hotkey{h, buttons, fn, user, {false, false, false, false}});
    return h;
}

int input_register_transform(NinputTransformFn fn, void* user) {
    if (!fn) return NINPUT_ERR_BADARG;
    std::scoped_lock lk(g_m);
    int h = g_next++;
    g_transforms.push_back(Transform{h, fn, user});
    return h;
}

void input_unregister(int handle) {
    std::scoped_lock lk(g_m);
    for (auto it = g_hotkeys.begin(); it != g_hotkeys.end(); ++it)
        if (it->handle == handle) { g_hotkeys.erase(it); return; }
    for (auto it = g_transforms.begin(); it != g_transforms.end(); ++it)
        if (it->handle == handle) { g_transforms.erase(it); return; }
}

void input_feed_state(int slot, unsigned long result, RawXInputState* s) {
    if (slot < 0 || slot > 3) return;
    const bool connected = (result == 0) && s != nullptr;
    const unsigned short buttons = connected ? s->pad.buttons : 0;

    struct FireInfo { NinputHotkeyFn fn; void* user; };
    std::vector<FireInfo> fires;
    std::vector<Transform> transforms;
    {
        std::scoped_lock lk(g_m);
        for (auto& hk : g_hotkeys) {
            const bool held = connected && (buttons & hk.mask) == hk.mask;
            if (held && !hk.held[slot]) fires.push_back({hk.fn, hk.user});  // rising edge
            hk.held[slot] = held;
        }
        transforms = g_transforms;
    }

    // Fire callbacks and run transforms outside the lock (a callback may (un)register).
    for (const auto& f : fires) f.fn(slot, f.user);

    if (connected && !transforms.empty()) {
        NinputPad p{};
        to_pad(*s, p);
        for (const auto& t : transforms) t.fn(slot, &p, t.user);
        from_pad(p, *s);  // the game reads the rewritten pad
    }
}

int input_hotkey_count() {
    std::scoped_lock lk(g_m);
    return (int)g_hotkeys.size();
}
int input_transform_count() {
    std::scoped_lock lk(g_m);
    return (int)g_transforms.size();
}

}  // namespace ninput

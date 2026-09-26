// Offline proof of the input layer (pillar 4). Synthetic XInput frames are fed straight into the
// engine (no controller, no game): hotkey chords fire on the rising edge, per slot, and a transform
// rewrites the pad the game would read. This is the same input_feed_state the proxy calls per frame.
#include <cstdio>

#include "hook_registry.hpp"
#include "input_engine.hpp"
#include "ninput.h"

// XINPUT_GAMEPAD_* button bits (from Xinput.h).
enum { BTN_A = 0x1000, BTN_B = 0x2000, BTN_X = 0x4000 };

static int g_fails = 0;
static void check(bool ok, const char* label) {
    std::printf("  %s  %s\n", ok ? "pass" : "FAIL", label);
    if (!ok) ++g_fails;
}

static int g_ab_fires = 0;
static void on_ab(int /*slot*/, void*) { ++g_ab_fires; }
static void add_x(int /*slot*/, NinputPad* p, void*) { p->buttons |= BTN_X; }

static ninput::RawXInputState frame(unsigned short buttons) {
    ninput::RawXInputState s{};
    s.pad.buttons = buttons;
    return s;
}
static void feed(int slot, unsigned long result, ninput::RawXInputState& s) {
    ninput::input_feed_state(slot, result, &s);
}

int main() {
    const NinputInterface* n = ninput::make_interface(NINPUT_GAME_UNKNOWN, 0, [](const char*) {});

    std::puts("hotkey chord edge detection");
    int hk = n->input->register_hotkey(BTN_A | BTN_B, on_ab, nullptr);
    check(hk > 0 && ninput::input_hotkey_count() == 1, "hotkey A+B registered");

    ninput::RawXInputState s = frame(BTN_A);
    feed(0, 0, s);
    check(g_ab_fires == 0, "partial chord (A only) does not fire");
    s = frame(BTN_A | BTN_B);
    feed(0, 0, s);
    check(g_ab_fires == 1, "full chord fires once on the rising edge");
    s = frame(BTN_A | BTN_B);
    feed(0, 0, s);
    check(g_ab_fires == 1, "holding the chord does not refire");

    std::puts("per-slot independence");
    s = frame(BTN_A | BTN_B);
    feed(1, 0, s);
    check(g_ab_fires == 2, "the same chord on another slot is its own edge");

    std::puts("release and re-press");
    s = frame(0);
    feed(0, 0, s);
    s = frame(BTN_A | BTN_B);
    feed(0, 0, s);
    check(g_ab_fires == 3, "releasing then re-pressing fires again");
    s = frame(BTN_A | BTN_B);
    feed(0, 1167, s);  // ERROR_DEVICE_NOT_CONNECTED
    check(g_ab_fires == 3, "a disconnected controller never fires");

    std::puts("transform rewrites the pad the game reads");
    int tf = n->input->register_transform(add_x, nullptr);
    s = frame(BTN_A);
    feed(0, 0, s);
    check((s.pad.buttons & BTN_X) != 0, "the transform's added button is present in the returned state");
    check(g_ab_fires == 3, "hotkeys read raw input, unaffected by a later transform");

    std::puts("unregister");
    n->input->unregister(hk);
    n->input->unregister(tf);
    check(ninput::input_hotkey_count() == 0 && ninput::input_transform_count() == 0, "both unregister");
    s = frame(BTN_A | BTN_B);
    feed(0, 0, s);
    check(g_ab_fires == 3 && (s.pad.buttons & BTN_X) == 0, "nothing fires or rewrites after unregister");

    std::printf("\n%s (%d checks failed)\n", g_fails ? "FAILED" : "ALL PASSED", g_fails);
    return g_fails ? 1 : 0;
}

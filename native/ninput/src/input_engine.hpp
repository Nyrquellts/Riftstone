// The Ninput input layer. Because Ninput is the xinput1_3 proxy, its XInputGetState wrapper feeds
// every frame the game polls through here: hotkey chords fire plugin callbacks, and plugin
// transforms rewrite the pad the game actually receives (remaps, macros, dead-zone tweaks).
#pragma once

#include "ninput.h"

namespace ninput {

// The raw XINPUT_STATE memory layout (kept local so nothing has to include <Xinput.h> or link it).
struct RawXInputState {
    unsigned long packet;
    struct {
        unsigned short buttons;
        unsigned char left_trigger;
        unsigned char right_trigger;
        short lx, ly, rx, ry;
    } pad;
};

// Backs the NinputInput table.
int  input_get_state(int slot, NinputPad* out);
int  input_register_hotkey(unsigned short buttons, NinputHotkeyFn fn, void* user);
int  input_register_transform(NinputTransformFn fn, void* user);
void input_unregister(int handle);

// Called by the xinput proxy right after the real XInputGetState read. `result` is that call's
// return (0 == connected). Runs hotkey edge-detection, then lets transforms modify *s in place so
// the game sees the rewritten pad.
void input_feed_state(int slot, unsigned long result, RawXInputState* s);

// Offline-harness introspection.
int input_hotkey_count();
int input_transform_count();

}  // namespace ninput

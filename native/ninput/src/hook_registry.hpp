// The Ninput hook arbiter: one place that owns every hook, so N plugins can hook the same
// engine address without clobbering each other's trampolines.
//
//   * A "mid" registration is shared. The registry installs one real SafetyHook mid-hook per
//     address and fans out to every plugin's callback in registration order. This is the
//     conflict-free primitive: two plugins reading the same struct never fight.
//   * An "inline" registration is exclusive -- it replaces a function and hands back the
//     original. A second inline request at the same address is refused (ADDRESS_TAKEN) and
//     logged, rather than silently overwriting the first plugin's jump.
//
// Backed by SafetyHook (Boost 1.0, vendored). Thread-safe: all bookkeeping is under one mutex.
#pragma once

#include <cstdint>

#include "ninput.h"

namespace ninput {

// Build the C interface tables that get handed to each plugin. The returned pointer and every
// table it references live for the lifetime of the process. `sink` receives one formatted line
// per log() call (the loader points it at the ninput.log writer; the test points it at stdout).
using LogSink = void (*)(const char* message);
const NinputInterface* make_interface(int game, std::uintptr_t image_base, LogSink sink);

// Exposed for the offline harness so it can assert on registry behaviour without the game.
int   mid_hook_count();      // addresses currently carrying a shared mid-hook
int   inline_hook_count();   // exclusive detours currently installed

}  // namespace ninput

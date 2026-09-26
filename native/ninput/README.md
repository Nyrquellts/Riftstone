# Ninput — universal native plugin host for Dragon's Dogma

Ninput is the master loader you asked for: one clean 32-bit DLL the game loads through a
proxy export, which then hosts many community plugins through **one arbitrated hook
registry** instead of every mod shipping its own `dinput8.dll` and fighting over the same
hooks and the same D3D9 device. It is built on Riftstone's existing loader (`native/loader`,
which already does overlay + crash reports + plugin loading) and on SafetyHook (Boost 1.0)
for the trampolines.

Target today: **Dragon's Dogma: Dark Arisen** (`DDDA.exe`, Steam build 2364871, x86). DDO is
design-only (its own toolkit forbids launching the client or running its binaries; both games
import `XINPUT1_3.dll`/`DINPUT8.dll`/`d3d9.dll`, so the same host maps cleanly when that work
is authorized).

## Why a master loader (the problem it removes)

Both games import `XINPUT1_3.dll` by ordinal, `DINPUT8.dll` (`DirectInput8Create`) and
`d3d9.dll` (`Direct3DCreate9`) — verified from the import tables. So any of those DLL names is a
valid loader entry point. But when two independent mods each install their own proxy and each
hook `IDirect3DDevice9::Reset` or the same engine function, they overwrite each other's jumps —
the fullscreen/alt-tab crashes and hook collisions. Ninput is the single owner of those hooks
and fans out to plugins, so they compose instead of collide.

Measured on the files (2026-09-26): `DDDA.exe` imports `XINPUT1_3.dll` ordinals 5, 4, 2, 3 (IAT
`0x0139D404`..`0x0139D410`); the retail `DDO.exe` (03.04.003, PE timestamp `0x5C4A64F3`) imports
ordinals 5, 4, 3, 2 (IAT `0x022337AC`..`0x022337B8`). The unpacked dumps of `DDO.exe` list the same
slots by name only because their rebuilt import table is written by name. Both games import
`WINMM.dll` (`timeGetTime`, `timeBeginPeriod`, `timeEndPeriod`), `DINPUT8.dll` and `d3d9.dll` by
name; only `DDO.exe` imports `VERSION.dll`. A proxy named `xinput1_3.dll` must therefore export
`XInputGetState`, `XInputSetState`, `XInputGetCapabilities` and `XInputEnable` at ordinals 2..5, as
`def/xinput1_3.def` does. Ultimate ASI Loader does not: its ordinals 2..5 are `XWSACleanup`,
`XCreateSocket`, `XSocketClose` and `XSocketShutdown` (Win32 build, SHA-512 `08B8BCA5…8A175F4`), so
it cannot take the `xinput1_3.dll` name for either game (DDDAFix ships it as `winmm.dll`).

## The six pillars → status

| # | Pillar | Status |
|---|---|---|
| 1 | **Proxy adapter** (xinput1_3 / dinput8 / version, user-selectable) | **Built.** Three DLLs from one core; xinput1_3 is the base (both games import it by ordinal). Exports match the system DLL and forward to it. |
| 2 | **D3D9 / display arbiter** — one `Reset` hook, broadcast lost/reset to plugins | **Built.** Hooks `Direct3DCreate9` → `CreateDevice` → `Reset`; brackets each reset with lost/reset broadcasts. Proven offline against a real D3D9 device. Follows the Riftstone loader's `[d3d9] chain` (DXVK's `d3d9.dll` in `riftstone\dxvk`, `docs/runtime.md`): that DLL's `Direct3DCreate9` is hooked too, so the broadcasts reach the chained device (`test/chain_test.cpp`, with the loader's stand-in DLL). |
| 3 | **Modular plugin loader** — a plugins folder, isolated, deterministic order | **Built.** The core loads `<game>\ninput\plugins\*.dll/*.asi` in name order and gives each the SDK handshake. Separate from the Riftstone loader's own generic `.asi` loading. |
| 4 | **Input multiplexer** — XInput polling, hotkeys, remaps/macros | **Built.** The proxy's `XInputGetState` wrapper feeds every polled frame through the engine: hotkey chords fire plugin callbacks (rising edge, per slot) and transforms rewrite the pad the game reads. Guide button / Elite paddles need `XInputGetStateEx`/HID — future. |
| 5 | **Shared NYR engine API** — `set_enemy_cap`, shadows, LODs… | **Built and wired.** Capability provider registry (core never does a guessed write). `enemy_cap` registers `set_enemy_cap` (apply-once, byte-verified) and `lod_tuner` registers `set_lod_distance_multiplier` (runtime-adjustable). Both plugins live in this worktree and still build standalone; their in-game effect stays UNKNOWN until launched. |
| 6 | **Conflict-free hook registry** (SafetyHook) | **Built and proven** — `src/hook_registry.*`, 13/13 offline checks. |

## The SDK a plugin compiles against

`include/ninput.h` is dependency-free C. A plugin exports one function:

```cpp
#include "ninput.h"
static void on_spawn(NinputRegs32* r, void*) { /* read/steer registers */ }

extern "C" __declspec(dllexport) int Ninput_Initialize(const NinputInterface* nyr) {
    if (nyr->abi_version != NINPUT_ABI_VERSION) return 0;
    // Many plugins may share this address; all run, in order, with no clobbering.
    nyr->hooks->register_mid(0x0041C086, on_spawn, nullptr, "MyPlugin");
    return 1;
}
```

- `register_mid(address, fn, user, owner)` — **shared**. One real hook per address, every
  plugin's callback runs in registration order.
- `register_inline(address, detour, &original, owner)` — **exclusive**. Replaces a function and
  returns the original; a second request at the same address is refused (`ADDRESS_TAKEN`) and
  logged, never silently applied.
- `unregister(handle)` — removing the last mid callback frees the hook; removing a detour
  restores the original bytes.

## Deploying it

One command (auto-detects the Steam install; backs up any existing `xinput1_3.dll`; reversible):

```bat
native\ninput\build_msvc.cmd
python native\ninput\deploy.py install      rem  status | uninstall  also
```

Then launch DDDA and walk through **`docs/in-game-verification.md`** — the checklist that moves each
pillar from harness-verified to in-game verified (and the fullscreen/alt-tab crash test).

To place things by hand instead: copy the personality you want next to `DDDA.exe`, renamed to its
slot name, and drop plugins in `<game>\ninput\plugins\`:

- **`xinput1_3.dll`** — the recommended slot. Leaves `dinput8.dll` free for the Riftstone loader,
  so both run without contending.
- **`dinput8.dll`** / **`version.dll`** — alternates for setups not using the loader.

The core writes `<game>\ninput\ninput.log` (attach line, plugin results) and forwards every real
export to the system DLL in `System32`/`SysWOW64` (never to itself).

## Build & verify (offline, no game)

```bat
native\ninput\build_msvc.cmd
python native\ninput\test\run_tests.py
```

Six harnesses, no game launched (the last two plugins' own byte-patch harnesses, run via their
`build.cmd` + `test/run_tests.py`, additionally verify the real patch against `DDDA.exe`):
- the hook arbiter — real SafetyHook hooks on real functions, asserting the sharing / churn /
  exclusivity rules;
- the D3D9 arbiter — hooks `Direct3DCreate9`, drives the `CreateDevice`→`Reset` chain, and (when
  the machine has a usable D3D9 device) creates a real device on a hidden window and confirms a
  genuine `Reset` runs the lost/reset broadcast; SKIPs that last part on headless machines;
- the engine registry — a mock provider registers a capability; the consumer setter routes to it,
  the provider's verdict propagates, and clearing it returns to `UNIMPLEMENTED`;
- the input layer — synthetic frames prove hotkey chord edges (per slot, re-press, disconnect) and
  a transform rewriting the returned pad;
- the engine providers — the real `enemy_cap.asi` and `lod_tuner.asi` register as providers and the
  consumer setters route to them;
- the xinput1_3 proxy — a stand-in exe loads the proxy and fetches `XInputGetState` **by ordinal 2**
  exactly as the game does, then the marker plugin is initialised through the interface.

Same discipline as the other native plugins: this must pass before any in-game claim, and in-game
behaviour stays **UNKNOWN** until observed in game.

## What's next (in order)

1. ~~Proxy personalities~~ — **done** (xinput1_3 base; dinput8/version alternates).
2. ~~The D3D9 arbiter~~ — **done** (`Direct3DCreate9`→`CreateDevice`→`Reset`, lost/reset broadcast).
3. ~~Engine capability registry~~ — **done**, and `enemy_cap`/`lod_tuner` now register as providers.
4. ~~The input layer~~ — **done** (hotkey chords + pad transforms over the proxy's `XInputGetState`).

All four list items are built and pass offline, and both providers' byte-patch harnesses still pass
against the real `DDDA.exe`. What remains is purely game-dependent: **launch DDDA once** to move the
loader / arbiter / hooks / providers from harness-verified to in-game verified. DDO stays design-only
(its toolkit forbids launching the client).

The `enemy_cap` and `lod_tuner` sources here are copies brought into this worktree for the provider
work; when the native suite is organised in the main line, reconcile them with the originals.

Chaining an existing `dinput8.dll` (e.g. DDDA Tweak) is handled by the Riftstone loader today; if
the dinput8 personality is used standalone, chaining can be added there later.

## Online (DDO.exe 03.04.003): engine sites for a future provider

DDO stays design-only here. These sites were read from the two unpacked dumps of the build in
`<path>` and `..._v3` (identical at every site below). The retail
exe keeps its code encrypted on disk, so a provider must compare the bytes in memory and refuse on
any difference. `<path>` and `<path>` hold the rest of
Buns' perf-patch addresses. Every in-game effect is UNKNOWN.

- **Frame rate.** `sMain` is `[0x0220417C]`, its target at `+0x3C`. The options setter `0x00609C90`
  stores 30.0 (`0x00609CC9`), 10.0 (`0x00609CDD`) or, for "60 FPS", the float at `0x00609CBE`. Both
  dumps hold 100.0 there, which Buns' readme says he edited, so the retail value is UNKNOWN (a check
  that expects 100.0 refuses on an unedited exe). `0x0041314E`, `0x006056A6` and `0x00607BF3` still
  store 60.0.
- **Shadow size.** `sShadow` (`[0x02205C50]`, 0x1170 bytes): when byte `+0x115C` is 1, `0x015BBF40`
  multiplies a requested shadow-map size by the float at `+0x1160`, then clamps it to 32..2048.
- **Depth of field.** The DOF filters are `uDOFFilter` (0xC0 bytes) and its five descendants
  `uDOFFilterExt`, `uDOFFilterAfterlife`, `uDOFFilterEm021004`, `uDOFFilterEvilEye` (0xD0 each) and
  `uBokehFilter` (0x1D0). Their `DTI::newInstance` (slot 1 of each class-DTI vftable, never called
  directly) are `0x014DFFF0`, `0x010BB560`, `0x010BB4D0`, `0x010BB500`, `0x010BB530`, `0x014DFEC0`;
  each starts with `push <its DTI>` (`uDOFFilterExt`'s after `push esi`) and already returns NULL
  when its allocation fails. Returning NULL from all six creates no DOF filter. Buns' patch leaves
  `uDOFFilterAfterlife` out and also turns the `jz` in six scalar-deleting destructors (`0x010B96BF`,
  `0x010B970F`, `0x010B975F`, `0x010B97AF`, `0x014A9B7D`, `0x014A9C8D`) into `jmp`, which only skips
  the free.
- **NULL entries.** `0x0149C140` (5 callers) walks an object array and dereferences each entry at
  `0x0149C154` (`8B 07 8B 10 8B 42 0C`, the only such run in `.text`). A guard there resumes at
  `0x0149C15B` and skips an entry at `0x0149C1AE` (`inc esi`, where the loop's own "no match" branches
  go); `0x0149C1AC`, Buns' target, is the `je` that returns the entry as a match. NULL entries stored
  while the factories return NULL can outlive a restore, so a guard should stay once installed.
- **Direct3D.** `DDO.exe` calls `Direct3DCreate9` through the thunk `0x0199491E`
  (`jmp [0x022337C8]`), so an import-table hook on the game module sees the call.

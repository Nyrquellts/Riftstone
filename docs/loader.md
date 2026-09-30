# The Riftstone loader

`native/loader/` builds a 32-bit DLL (DDDA.exe and DDO.exe are x86) from `loader.cpp`,
`stability.cpp`, `live.cpp`, `fixes.cpp`, `session.cpp`, `overlay.cpp` and `graphics.cpp`. It comes in two builds:

- `dinput8.dll`: a proxy. The game loads it from its own folder and it
  forwards `DirectInput8Create`.
- `riftstone_loader.dll`: an add-on for another loader, e.g. DDDA Tweak's
  `loadLibrary = riftstone_loader.dll`.

This page covers the base: the overlay, plugins, the log and installing. **`docs/runtime.md`** covers
everything the loader does to keep the game running and explain what went wrong: crash, fatal-error
and hang reports with engine class names, why the game closed when it was not a crash, the
missing-texture guard, the archive guard (a resource asked for before its archive was read gets its own bytes), safe mode and plugin quarantine, live stats for Studio, the in-game diagnostics
panel (Insert), save backups, the window fixes, the frame-rate ceiling, the shadow map size, the game's
Direct3D 9 from DXVK (`[d3d9] chain`) with what it holds counted by pool, the memory pressure watch and the
large-address check.

## What it does

- **Overlay.** A read-only open of `<game>\nativePC\<path>` is served from
  `<game>\riftstone\overlay\<path>` when that file exists. Writes are never
  redirected, and neither is anything outside `nativePC`. Paths are
  normalised first (relative, absolute, any case, `..`).
- **Log.** `riftstone\logs\loader.log` records the game and build, the hooks, settings, every
  redirect, and every file the game looked for under `nativePC` and did not find; the previous
  session's log is kept as `loader.prev.log`.
- **Plugins.** Every `.asi`/`.dll` in `<game>\riftstone\plugins\` is loaded at
  startup, in name order, and logged (with the reason when one fails to load). This is how community
  native mods run through Riftstone without a separate ASI loader — including the vendored GPL
  unit-limit expander (a third-party reference kept outside git under `vendor/unit_expander`). Manage them with
  `riftstone loader plugin add|list|remove|release`. Each plugin hooks the game itself; Riftstone only
  brings it into the process, and skips one that crashed the game's start-up twice (`docs/runtime.md`).
- **Direct3D 9 chain.** `[d3d9] chain = riftstone\dxvk\d3d9.dll` gives the game DXVK's 32-bit `d3d9.dll`
  (or another Direct3D 9 runtime) without putting it into the game folder; `riftstone loader d3d9 add <DXVK
  release>` puts it there and sets the key, `riftstone loader d3d9 off` takes the key out. Checked before it
  is loaded, off in safe mode; a `d3d9.dll` in the game folder stays in charge (`docs/runtime.md`).
- **Only in the game.** Another program in the folder that loads dinput8 (DDO's launcher does) gets
  DirectInput passed through and nothing else.

The loader hooks the game executable's import table (`CreateFileA/W`, `GetFileAttributesA/W`,
`SetUnhandledExceptionFilter` unless `[loader] crash_reports = 0`, `MessageBoxA`, `d3d9!Direct3DCreate9`, and the
window functions when the window fixes are on), and the function tables of the Direct3D objects the game creates
(CreateDevice, Present, Reset, the five create calls and each counted object kind's Release). A watchdog re-checks
the hooks for 20 seconds, and again at `DirectInput8Create`. If the DRM start-up restores the import table (a slot
holds again what it held before the loader hooked it, or the export itself), the hooks go back in and the log says
so. Any other value in a slot is another module's hook put over the loader's (a plugin hooking the game's
`CreateFileW` from its `DllMain`): it was handed the loader's hook to call on, so it is left in place and the log
names its module once (since 0.4.1; before, the loader put itself back in front and the two hooks called each
other until the stack ran out). Engine code is patched only by the opt-in, byte-verified fixes for build 2364871,
and by plugins.
To see what closes the game, the loader also puts two message hooks (`WH_CALLWNDPROC`, `WH_GETMESSAGE`) on
the game window's thread; they only look (`docs/runtime.md`, Why the game closed).

## Install

```bat
Riftstone.cmd loader install     rem or Studio > Game > Install loader
```

If a different `dinput8.dll` is already present (DDDA Tweak), it becomes
`dinput8_chain.dll` and the loader forwards DirectInput to it (`[loader] chain`). A chain that is the
loader itself (`chain = dinput8.dll`) or whose `DirectInput8Create` forwards back to it is refused with a
log line, and the system dinput8 answers (since 0.4.1; before, `DirectInput8Create` called itself).
`loader remove` restores it. Installed mods move between direct and overlay
mode automatically. Installing over an older loader keeps every value you set in
`riftstone_loader.ini` and adds the new settings with their defaults. For DDO:
`ddon.cmd runtime install --yes` in `<path>`.

`riftstone_loader.ini` (next to `DDDA.exe`) is commented; `native/loader/riftstone_loader.ini` is the
template, with the sections `[loader]`, `[guard]`, `[live]`, `[memory]`, `[d3d9]`, `[overlay]` (the in-game
panel; not to be confused with `[loader] overlay`, the mod files), `[window]`, `[fps]`, `[render]`, `[saves]`.
Windows reads it in the ANSI code page unless it is UTF-16 with a byte-order mark, never as UTF-8 (measured;
`docs/runtime.md`, "The ini files, and how Windows reads them").

## Verification status

| Check | Result |
|---|---|
| Stand-in games and the real DDDA.exe code, 304 checks, the in-game panel read back from real Direct3D 9 frames (over DXVK too) and the Direct3D chain and pools among them (`docs/runtime.md`, Proof), and the eleven faults 0.4.1 fixes, each reproduced first (`docs/runtime.md`, Loader 0.4.1) | pass (`native/loader/test/run_tests.py`) |
| `DDDA.exe` imports every hooked function (KERNEL32, USER32, d3d9), and its import table sits in `.rdata` while the entry point is in `.bind` (SteamStub) | measured statically |
| Inside the real game: loader 0.1 | **seen working 2026-09-24**: all five hooks installed, archives redirected from the overlay, two plugins loaded (`loader.log`) |
| Inside the real game: the runtime (0.2, 0.2.1's record of why the game closed, 0.3's in-game panel, 0.4's Direct3D chain, pools and pressure watch, 0.4.1's fixes) | **UNKNOWN until the first launch** (what to look for: `docs/runtime.md`) |

## Build

```bat
native\loader\build.cmd          rem Visual Studio 2022 C++ tools, x86; static CRT, /W4, /guard:cf
python native\loader\test\run_tests.py
```

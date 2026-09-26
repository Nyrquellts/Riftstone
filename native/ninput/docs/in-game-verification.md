# Ninput in-game verification (one DDDA launch)

Everything Ninput does is harness-verified but **UNKNOWN in game** until observed. This is the
checklist to move each piece from harness-verified to in-game verified in a single session. You run
the game; nothing here writes to the game's own data (the loader only *reads*, redirecting through
its overlay).

## 0. Prepare

```bat
native\ninput\build_msvc.cmd
python native\ninput\deploy.py status
python native\ninput\deploy.py install
```

`install` backs up any existing `xinput1_3.dll`, drops the proxy next to `DDDA.exe`, and puts
`enemy_cap.asi` + `lod_tuner.asi` in `<game>\ninput\plugins`. If it warns that those `.asi` are also
in `riftstone\plugins`, delete them there first (or the Riftstone loader loads them too and the
second copy refuses). Then launch the game normally from Steam.

Logs to watch afterwards:
- `<game>\ninput\ninput.log` — Ninput core: attach, plugins, the D3D9 arbiter.
- `<game>\riftstone\logs\enemy_cap.log`, `lod_tuner.log` — the two plugins' own logs.
- `<game>\riftstone\logs\loader.log` — the Riftstone loader (dinput8.dll), still running alongside.
- `<game>\riftstone\logs\crash-*.txt` — should **not** appear.

## 1. It loaded at all

**Pass:** the game reaches the title screen. A missing-DLL error box or an instant exit means the
proxy failed to load — check that `xinput1_3.dll` is the built one (it imports only `KERNEL32`).

`ninput.log` exists and starts with:
```
Ninput 0.1.0 attached to Dragon's Dogma: Dark Arisen (build 2364871) at image base 0x00400000
```
If it says **"an unrecognised host"**, your `DDDA.exe` is not build 2364871 — engine providers will
stay `UNIMPLEMENTED` (safe), but report the build so the sites can be checked.

## 2. Plugins handshook (pillars 1, 3)

In `ninput.log`:
```
plugins  2 found in ...\ninput\plugins
plugin   enemy_cap.asi initialised
plugin   lod_tuner.asi initialised
```
**Pass:** both say `initialised` (their `Ninput_Initialize` ran and registered a provider).

## 3. D3D9 arbiter — the fullscreen/alt-tab crash fix (pillar 2)

In `ninput.log`, at startup then once in-world:
```
display  Direct3DCreate9 hooked; waiting for the game to create its device
display  CreateDevice hooked at 0x...
display  Reset hooked at 0x... (device 0x...)
```
Then **exercise device resets** — the exact thing that used to crash:
- Alt-Tab out and back, several times.
- Toggle fullscreen ⇄ windowed (in Options, or Alt-Enter).
- Change resolution in Options and apply.

**Pass:** the game survives every one — no black-screen hang, no crash. No `crash-*.txt` appears in
`riftstone\logs`. (With no drawing plugin registered yet, the arbiter just owns `Reset` cleanly;
the win is that a plugin can no longer fight the game for the device.)

## 4. enemy_cap — more than ten enemies (provider: set_enemy_cap)

`riftstone\logs\enemy_cap.log`:
```
enemy_cap: 30 enemies at once (the game's limit is 10); 164 sites and 4 runs patched (game); slot record on
```
Then go somewhere with a big fight — a goblin/saurian horde, or a stage with respawn hordes.
Watch the slot record accumulate:
```
HH:MM:SS  peak: 14 of 30 slots in use (13 with a unit), stage 100
```
**Pass:** `patched (game)` is present, and the peak climbs **above 10**. Seeing >10 living enemies
at once on screen is the visible confirmation.
**If it refused** (`refused: ...` in the log): note the line. "spawn manager already exists" means it
loaded too late (shouldn't happen from the xinput slot); a byte-mismatch means a different build.

## 5. lod_tuner — distant detail holds (provider: set_lod_distance_multiplier)

`riftstone\logs\lod_tuner.log`:
```
lod_tuner: rModel::load patched at 0x00FA9479 (game); first changes:
  scr\...: HIGH to 3000 -> 6099, MEDIUM to 5000 -> 10166 (radius ..., x2.03)
```
Then look at distant scenery/terrain — buildings and cliffs keep full detail much farther out than
vanilla. Note your `config.ini` `ViewRange`: at **FARTHEST** the engine already forces HIGH for
regular models, so use **FAR** or **NORMAL** to see the plugin's effect there (the log says this).

**Pass:** `patched (game)` present, and distant models visibly hold detail.

## 6. Engine providers reachable (pillar 5)

The providers are registered (step 2). A full end-to-end check needs a consumer plugin that calls
`nyr->engine->set_enemy_cap(...)` / `set_lod_distance_multiplier(...)`; with only enemy_cap/lod_tuner
loaded there is no consumer, so this is verified structurally by the `initialised` lines plus the
cap/LOD actually taking effect (steps 4–5). `set_lod_distance_multiplier` from a future plugin
retunes LOD live (models loaded after the call); `set_enemy_cap` is apply-once (the ini value wins).

## 7. Loader coexistence

`riftstone\logs\loader.log` still shows the Riftstone loader's own `hook ... installed` and any
`overlay ... -> ...` lines. **Pass:** both `dinput8.dll` (loader) and `xinput1_3.dll` (Ninput) ran;
they do not contend.

## If something goes wrong

- **Instant exit / DLL error box:** wrong `xinput1_3.dll`, or a stray one in `System32`. Re-run
  `deploy.py install`; confirm the file imports only `KERNEL32` (`deploy.py status` says built-ready).
- **A `crash-*.txt` in `riftstone\logs`:** the loader's crash report — attach it. It names the
  faulting module+offset and the last files opened.
- **enemy_cap/lod_tuner say `refused`:** report the exact line; a byte-mismatch means the exe isn't
  build 2364871. Nothing was patched (safe).
- **Enemies still capped at 10 / LODs unchanged:** check for a duplicate `.asi` in `riftstone\plugins`
  (double-load → second refuses), and that `ninput.log` shows the plugin `initialised`.

## Roll back

```bat
python native\ninput\deploy.py uninstall
```
Removes `xinput1_3.dll`, restores any backup, and leaves the loader and its plugins untouched.

## Honest scope

Passing this verifies the loader, proxy, D3D9 arbiter, plugin host and the two providers **in the
real process**. It does not verify DDO (design-only), and it does not exercise the
input hotkey/transform path or a consumer of the engine API — those need a plugin that uses them.

# lod_tuner (Riftstone native plugin)

Scenery keeps its full detail until it is a couple of dozen pixels tall on screen, instead of
switching while it is still hundreds of pixels tall as the 720p-tuned vanilla distances make it.
Characters stay vanilla. The distances change once per model at load: no per-frame work, no extra
memory. Instanced vegetation's cross-fade between levels (a step a frame in the game, so 0.11 s at
150 fps, close to a pop) takes `FadeSeconds` (0.75) at any frame rate. The measurements, the design
and the proof are in [`docs/lod.md`](../../../docs/lod.md).

- **Game:** DDDA.exe build 2364871 only. The plugin compares the patched site (right after
  `rModel::load` reads the primitives), the code around it, rModel's vtable, two LOD readers and the
  fade's two sites with the code around them first. A difference at the load site patches nothing, one at the fade's keeps the game's own fade, and it logs why to
  `<game>\riftstone\logs\lod_tuner.log`.
- **Code:** original, no third-party source. Hand-written `jmp` thunks, no hooking library.
- **Settings:** [`lod_tuner.ini`](lod_tuner.ini), next to the `.asi`. `PopPixels` (default 24) is the
  main knob: lower means fewer visible switches and more drawing. `FadeSeconds` (default 0.75) is the
  vegetation's cross-fade; 0 keeps the game's own.
- **Pair it with** config.ini `ViewRange=FAR` or `NORMAL`. Under `FARTHEST` the game already draws
  every regular model at full detail, so the plugin then only changes instanced vegetation, unless
  `Farthest = lod`: that gives FARTHEST levels of detail too, at the plugin's distances x3 (ten
  branches retargeted, each checked first; the default `high` is the game's own).

## Build

Needs Visual Studio 2022 with the C++ x86 tools. No network is needed.

```bat
native\plugins\lod_tuner\build.cmd
```

| File | What |
|---|---|
| `out\lod_tuner.asi` + `out\lod_tuner.ini` | the plugin and its settings |
| `out\lod_stub.exe` + `out\lod_harness_core.dll` | the test harness |

## Test (no game launched)

```bat
python native\plugins\lod_tuner\test\run_tests.py
```

This maps a read-only copy of DDDA.exe into the harness process and lets the plugin patch that copy.
It then runs the real `rModel::load` instructions through the thunk, and the game's own LOD readers
on the result, and times the vegetation's cross-fade in the game's own draw-list builder at 20 to 240 fps.
It runs five profiles (shipped settings, `Farthest = lod`, flat multiplier with the game's own fade, a
5-second fade, disabled): 148 checks. `tools\test_all.cmd` runs it too. It reports a skip when the plugin isn't built or the game isn't
found.

## Install

This writes to the game, so it needs the owner's yes:

```bat
riftstone loader install
riftstone loader plugin add native\plugins\lod_tuner\out\lod_tuner.asi
```

The first `plugin add` also copies `lod_tuner.ini`; a later update keeps the owner's values and adds any
setting new in the build with its default and comment.
Removal: `riftstone loader plugin remove lod_tuner.asi`.

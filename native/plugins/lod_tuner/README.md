# lod_tuner (Riftstone native plugin)

Scenery keeps its full detail until it is a couple of dozen pixels tall on screen, instead of
switching while it is still hundreds of pixels tall as the 720p-tuned vanilla distances make it.
Characters stay vanilla. It runs once per model at load: no per-frame work, no extra memory. The
measurements, the design and the proof are in [`docs/lod.md`](../../../docs/lod.md).

- **Game:** DDDA.exe build 2364871 only. The plugin compares the patched site (right after
  `rModel::load` reads the primitives), the code around it, rModel's vtable and two LOD readers first. On any difference it patches nothing and logs why to
  `<game>\riftstone\logs\lod_tuner.log`.
- **Code:** original, no third-party source. One hand-written `jmp` thunk, no hooking library.
- **Settings:** [`lod_tuner.ini`](lod_tuner.ini), next to the `.asi`. `PopPixels` (default 24) is the
  main knob: lower means fewer visible switches and more drawing.
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
on the result. It runs four profiles (shipped settings, `Farthest = lod`, flat multiplier, disabled), and
`tools\test_all.cmd` runs it too. It reports a skip when the plugin isn't built or the game isn't
found.

## Install

This writes to the game, so it needs the owner's yes:

```bat
riftstone loader install
riftstone loader plugin add native\plugins\lod_tuner\out\lod_tuner.asi
```

The first `plugin add` also copies `lod_tuner.ini`, and later updates keep the owner's edits.
Removal: `riftstone loader plugin remove lod_tuner.asi`.

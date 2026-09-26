# draw_distance (Riftstone native plugin)

How far out the game shows and runs the world around you, independent of the View Range option. The
game multiplies a handful of distances by View Range (NORMAL 1, FAR 2, FARTHEST 3); this plugin puts
its own number in place of that multiplier, at every View Range, so FAR or NORMAL (the pairing
[`lod_tuner`](../lod_tuner/README.md) recommends) can have FARTHEST's distances or more without
FARTHEST's other costs. The mechanism, the evidence and the proof are in
[`docs/draw-distance.md`](../../../docs/draw-distance.md).

- **Game:** DDDA.exe build 2364871 only. The plugin compares every site first, along with the code
  around it, three vtable slots, three calls and the shared constants, whatever the settings. On any
  difference it patches nothing and logs why to `<game>\riftstone\logs\draw_distance.log`.
- **Code:** original, no third-party source. It changes a few bytes of the game's code once at
  start-up (a branch or the address a load reads), so nothing runs per frame.
- **Settings:** [`draw_distance.ini`](draw_distance.ini), next to the `.asi`. 0 leaves a setting to
  the game.

| Key | Default | What it changes |
|---|---|---|
| `Objects` | 3 | Overworld objects (props, doors, chests, gathering spots and the like) are drawn and updated only within their display radius of you: 25 m for small ones (15 m for some), 1 km for large ones, times this. 1 to 20. 3 is what FARTHEST does |
| `Grass` | 3 | Grass fades out between 17 and 70 m, times this. 1 to 3: FARTHEST's own is the most the game ever draws |
| `Enemies` | 0 | Monsters stay fully active (thinking, physics) within their kind's distance, 20 to 300 m times this, keep moving up to 200 m beyond it and stand frozen farther out. The game draws them either way, so this is not a visibility distance. 1 to 20 |
| `HumanEnemies` | 0 | Human enemies (bandits and the like) stay fully active within this many metres (the game: 100 at every View Range), keep moving and showing up to 200 m beyond it and vanish farther out. 100 to 2000 |
| `ObjectsNeverHide` | 0 | 1 = overworld objects are never hidden or stopped by distance, only by what the game has loaded around you |
| `EnemiesAlwaysActive` | 0 | 1 = monsters and human enemies stay fully active (and human enemies shown) at any distance, as long as the game has them loaded |

Everything else keeps the game's own rules: model detail (that is `lod_tuner`), townspeople (the
game's NPC system has its own distances and budget), terrain streaming (the stage's load window) and
characters whose placement sets its own distance. In game: UNKNOWN until played, including the frame
rate cost of each setting.

## Build

Needs Visual Studio 2022 with the C++ x86 tools. No network is needed.

```bat
native\plugins\draw_distance\build.cmd
```

| File | What |
|---|---|
| `out\draw_distance.asi` + `out\draw_distance.ini` | the plugin and its settings |
| `out\dd_stub.exe` + `out\dd_harness_core.dll` | the test harness |

## Test (no game launched)

```bat
python native\plugins\draw_distance\test\run_tests.py
```

This maps a read-only copy of DDDA.exe into the harness process and lets the plugin patch that copy.
It checks that exactly the plugin's bytes changed, then runs the game's own code on fake objects at
View Range NORMAL, FAR and FARTHEST:

- `sGameSys::setNoMoveDistance` and the `sGameSys` constructor: the display radii;
- the radius each object takes (`uOmObjBase::after`, `uOmSetBase::after`), then the tests that hide
  and stop it (`uOmObjBase::setRestrictDrawAndMove`, `uOmSetBase::move`);
- `uEnemy::setup`'s distances and `uCharacterBase::checkMoveMode`, for monsters and human enemies;
- `aStage::init`'s grass fade.

There are seven profiles: the shipped ini, two others, clamped and unreadable values, every setting 0,
`Enabled = 0`, and game code that differs by one byte (refused, nothing written). A second copy of the
plugin refuses the patched code. `tools\test_all.cmd` runs it too. It reports a skip when the plugin
isn't built or the game isn't found.

## Install

This writes to the game, so it needs the owner's yes:

```bat
riftstone loader install
riftstone loader plugin add native\plugins\draw_distance\out\draw_distance.asi
```

The first `plugin add` also copies `draw_distance.ini`, and later updates keep the owner's edits.
Removal: `riftstone loader plugin remove draw_distance.asi`.

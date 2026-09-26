# enemy_skins (Riftstone native plugin)

Chosen chimera placements wear a skin: another set of albedo maps, such as Dragon's Dogma Online's
White Chimera. Every other chimera stays vanilla. The design, the six hook sites and the proof are in
[`docs/enemy-skins.md`](../../../docs/enemy-skins.md).

- **Game:** DDDA.exe build 2364871 only. The plugin compares every site, vtable, string and table
  first; on any difference it patches nothing and logs why to `<game>\riftstone\logs\enemy_skins.log`.
- **Code:** original, no third-party source. The hooks are hand-written `jmp` thunks, with no hooking
  library.
- **Configuration:** none. Skin *NN* is `model\em\e52\e5200\sNN\` inside `rom\enemy\em5200.arc`, which
  `riftstone skin make` writes into a mod. A placement asks for it with its magick-defence multiplier
  off and value bits `0x534B00NN`, which `riftstone encounter ... --skin NN` sets.

## Build

Needs Visual Studio 2022 with the C++ x86 tools. No network is needed.

```bat
native\plugins\enemy_skins\build.cmd
```

It produces:

| File | What |
|---|---|
| `out\enemy_skins.asi` | the plugin |
| `out\skins_stub.exe` + `out\skins_harness_core.dll` | the test harness |

## Test (no game launched)

```bat
python native\plugins\enemy_skins\test\run_tests.py
```

This maps a read-only copy of DDDA.exe into the harness process, lets the plugin patch that copy,
and runs the real game code at every hooked site on fake chimeras. It runs 26 checks, and
`tools\test_all.cmd` runs it too. It reports a skip when the plugin isn't built or the game isn't
found.

## Install

This writes to the game, so it needs the owner's yes:

```bat
riftstone loader install
riftstone loader plugin add native\plugins\enemy_skins\out\enemy_skins.asi
riftstone install "mods\DDO Chimeras"
```

Removal: `riftstone loader plugin remove enemy_skins.asi`. A marked chimera without the plugin is a
plain chimera.

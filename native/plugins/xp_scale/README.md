# xp_scale (Riftstone native plugin)

More or less experience from kills and from quests, and a flatter level curve. The measurements and
the proof are in [`docs/xp-scale.md`](../../../docs/xp-scale.md).

- **Game:** DDDA.exe build 2364871 only.
  - The plugin first compares 14 sites with that build: `sGameSys::addExp`'s entry and both of its
    `raw` tests, the Arisen's slot, both calls to `cPlayerInfo::addExp`, its cap, level-up's compare
    and store, and the five places that give experience.
  - On any difference it patches nothing and logs why to `<game>\riftstone\logs\xp_scale.log`.
- **Code:** original, no third-party source.
  - It replaces the first five bytes of `sGameSys::addExp` (`0x00443470`) with a jump, once at start-up.
  - Each reward then passes through the plugin, which changes only the amount and hands every register,
    the flags and `xmm0`-`xmm7` back as they came. Whole numbers only.
- **Settings:** [`xp_scale.ini`](xp_scale.ini), next to the `.asi`:

| Key | Value | What changes |
|---|---|---|
| `[xp] Kills` | 0.1 to 2.0 (shipped 1.5) | Experience from a kill, times this. |
| `[xp] Quests` | 0.1 to 2.0 (shipped 1.5) | Experience a quest gives, times this. |
| `[curve] LevelCost` | 0, or 1000 to 10,000,000 (shipped 125000) | While your next level costs more than this, rewards grow by that cost / `LevelCost`, so no level takes longer than one of `LevelCost` would. 0 = the game's curve. |

The game's own level costs: 4,640 at level 10, 80,100 at 50, 124,100 at 100, 289,100 at 150 and 504,700
at 199. The main pawn, your vocation's discipline and hired pawns' Rift Crystals follow the same amount.
`Kills = 1`, `Quests = 1` and `LevelCost = 0` patch nothing.

`xp_scale.log` notes once what the first kill and the first quest gave, and once when `LevelCost` first
raised a reward.

In game: UNKNOWN until played.

## Build

Needs Visual Studio 2022 with the C++ x86 tools. No network is needed.

```bat
native\plugins\xp_scale\build.cmd
```

| File | What |
|---|---|
| `out\xp_scale.asi` + `out\xp_scale.ini` | the plugin and its settings |
| `out\xp_stub.exe` + `out\xp_harness_core.dll` | the test harness |

## Test (no game launched)

```bat
python native\plugins\xp_scale\test\run_tests.py
```

It maps `DDDA.exe` read-only into the stub, loads the plugin, and runs the game's own `sGameSys::addExp`
on a fake party, through the patch and unpatched with the plugin's amount, which must leave the same
state and registers; and the game's own `getNextExp` for levels 1 to 199 against the level table read from
your archives. Seven profiles, 392 checks.

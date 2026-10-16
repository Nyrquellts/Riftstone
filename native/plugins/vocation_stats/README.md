# vocation_stats (Riftstone native plugin)

A rest at an inn gives the Arisen and the main pawn the stats their current vocation would have given
them at every level they have, as if they had gained all of them in it. Level and experience stay. The
measurements and the proof are in [`docs/vocation-stats.md`](../../../docs/vocation-stats.md).

- **Game:** DDDA.exe build 2364871 only.
  - The plugin first compares 17 sites with that build: uInn's two rest calls, the game's `setLevel`
    (its start, cap, table, base row, loop, zeroing and return), the level-up (its start, refusals, cap,
    forced path, table, HP cap and no-notice exit), 9999.0 and both party slots.
  - On any difference it patches nothing and logs why to `<game>\riftstone\logs\vocation_stats.log`.
- **Code:** original, no third-party source.
  - It rewrites the two rest calls of uInn's rest state (`0x00736757`, `0x0073675E`), once at start-up.
  - At each rest it runs the game's own `setLevel` at the character's level (level 1 from the current
    vocation's table, then the game's level-up without notice up to the level), puts the experience toward
    the next level back, and hands every register, the flags and `xmm0`-`xmm7` on to the rest as they came.
  - A character the level-up would refuse, one with no level table or a level outside 1..200 is left as
    it is; a fault inside the game's code puts that character back as it was.
- **Settings:** [`vocation_stats.ini`](vocation_stats.ini), next to the `.asi`:

| Key | Value | What changes |
|---|---|---|
| `[vocation_stats] Mode` | `current` (shipped) or `off` | `current`: every rest at an inn rebuilds the stats by the current vocation. `off`: nothing is patched. |
| `[vocation_stats] Who` | `party` (shipped), `arisen` or `pawn` | Who is rebuilt. Hired pawns never are. |

The new stats are saved like any other, and turning the plugin off does not put the old ones back: an
older save comes back with the `save_backup` plugin's copies or `riftstone saves restore`. A Warrior at
level 200 that levelled only as a Warrior has 4,820 HP, 2,580 stamina, 766 attack and 577 defence.

`vocation_stats.log` says for each rest what changed (each stat before and after), or why a character
was left.

In game: UNKNOWN until played.

## Build

Needs Visual Studio 2022 with the C++ x86 tools. No network is needed.

```bat
native\plugins\vocation_stats\build.cmd
```

| File | What |
|---|---|
| `out\vocation_stats.asi` + `out\vocation_stats.ini` | the plugin and its settings |
| `out\vocation_stats_stub.exe` + `out\vocation_stats_harness_core.dll` | the test harness |

## Test (no game launched)

```bat
python native\plugins\vocation_stats\test\run_tests.py
```

It maps `DDDA.exe` read-only into the stub, loads the plugin, and rests through both patched calls on a
fake party, with the game's own `setLevel`, level-up and `getNextExp` and the level tables read from your
archives: every byte of the fake world and every register must come out as expected, and every vocation's
numbers at the levels where its bands change must match the model in `run_tests.py`. Eight profiles,
6,318 checks.

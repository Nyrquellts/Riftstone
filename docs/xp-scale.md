# Experience: more or less from kills and quests, and a flatter curve (`xp_scale` plugin)

Dark Arisen's levels past 100 cost more than twice level 100's, so the last stretch to 200 is a long
grind. The `xp_scale` plugin multiplies the experience a kill gives and the experience a quest gives
(0.1 to 2.0 each), and can flatten the curve: while the Arisen's next level costs more than
`LevelCost`, every reward grows by that cost / `LevelCost`, so no level takes more killing than a level
of `LevelCost` would. Everything below is read statically from `DDDA.exe` (build 2364871) and run in a
harness on the game's own code. **In game: UNKNOWN** until played.

## Where the party's experience goes

- Every reward goes through one function, `sGameSys::addExp` (`0x00443470`), with four arguments on the
  stack: `sGameSys`, the amount, a rate and `raw`; it returns with `ret 0x10` (`C2 10 00` at `0x004437BD`).
  - It starts `83 EC 24 53 55 8B 6C 24 30` at `0x00443470` (`sub esp, 0x24; push ebx; push ebp;
    mov ebp, [esp+0x30]`).
  - It takes `sGameSys`'s lock when `+0x1C` is set or the byte at `0x018D0BB0` is
    (`80 7D 1C 00` at `0x00443479`, `80 3D B0 0B 8D 01 00` at `0x00443481`), through the import at
    `0x0139D160` (`FF 15 60 D1 39 01` at `0x0044348E`).
- With `raw` 0 the amount is first doubled on Hard Mode: bit 4 of `[0x018FA4F8] +0x20E77B`
  (`F6 81 7B E7 20 00 04` at `0x0044349A`), except in stage 605 (`81 7A 34 5D 02 00 00` at `0x004434B2`);
  `raw` decides it (`80 7C 24 44 00 75 3A` at `0x004434BB`).
- Then it walks the four party slots (`83 FB 04` at `0x00443504`): slot 0 is the Arisen at `sGameSys +0xA76D0`
  (`8B 35 BC A4 8F 01 81 C6 D0 76 0A 00` at `0x00443511`), the others `+0xA6860 + 0x1660 x slot`
  (`69 D2 60 16 00 00 8D B4 02 60 68 0A 00` at `0x00443526`), each only while its byte `+4` is set
  (`80 7E 04 00` at `0x0044353B`).
  - With `raw` 0 a slot's amount is multiplied by the rate and the party's level spread
    (`80 7C 24 44 00 75 75` at `0x00443545`, `E8 7B 02 00 00` at `0x00443550`): `getExpRate`
    (`0x004437D0`) gives 0.01 a level of difference, from 0.75 to 1.25.
  - A jump table sends each slot on (`ED 35 44 00 67 36 44 00 4A 37 44 00 4A 37 44 00` at `0x004437C0`).
  - The Arisen: `cPlayerInfo::addExp` (`E8 DC F6 33 00` at `0x0044362F`), then a running total at
    `sGameSys +0xB88B0` (`0x0044B760`, capped at 99,999,999; what reads it is UNKNOWN).
  - The main pawn: `cPlayerInfo::addExp` too (`E8 C8 F5 33 00` at `0x00443743`), plus a catch-up: the
    Arisen's total less its own, x a rate from the object at `sGameSys +0xB8754`, x 0.01
    (`F3 0F 59 05 28 CF 52 01` at `0x004436F8`).
  - The two hired pawns: a tenth of it as Rift Crystals (`D8 0D 5C AE 4E 01` at `0x0044374A`, the 0.1
    `CD CC CC 3D` at `0x014EAE5C`), added at `+0x34C` (`01 96 4C 03 00 00` at `0x00443773`) up to
    99,999,999 (`B8 FF E0 F5 05` at `0x00443762`).
- `cPlayerInfo::addExp` (`0x00782D10`) multiplies by the experience ability (ability `0x5D`, through
  `getAbilityBonus` `0x00783340`), adds the result to `+0x2C4` and caps it at 1,000,000,000
  (`B8 00 CA 9A 3B 39 87 C4 02 00 00 72 06` at `0x00782F0D`); the vocation's discipline grows from the
  same amount (`01 84 8F F4 02 00 00 01 87 44 03 00 00` at `0x00782EE2`).

Five places call `sGameSys::addExp`:

| Call | What | `raw` |
|---|---|---|
| `E8 49 BF BE FF` at `0x00857522` | a kill (the PS3 build's `uCharacterBase::setExperience`) | `6A 00` at `0x00857514` |
| `E8 A9 34 CB FF` at `0x0078FFC2` | a quest's reward (`cQuestCtrl::QuestTblResultProc`) | `6A 01` at `0x0078FFB2` |
| `E8 87 2E CB FF` at `0x007905E4` | a quest's reward after its gold | `6A 01` at `0x007905D5` |
| `E8 44 1C CB FF` at `0x00791827` | a quest's reward by its kind | `6A 01` at `0x00791817` |
| `E8 3E 0B F3 FF` at `0x0051292D` | a reward of the game's mode dispatcher (not in the PS3 build) | `6A 01` at `0x0051291D` |

So `raw` tells a kill (0) from a quest's reward (1), and a kill's amount is the one the level spread and
Hard Mode change.

## What a level costs

- The level table is `param\pl\level\exp.plexp` (`rPlExp`, in `rom\game_main`): rows of `{mLv, mRate}`.
  `sPlayerManager` (`[0x018FA4EC]`) keeps it at `+0x868`, its rows at `+0x70`.
- `getNextExp` (`0x00B5C080`, `A1 EC A4 8F 01 83 EC 0C 53 8B 98 68 08 00 00 8B 4B 70` at `0x00B5C080`):
  level 1 costs row 0's level; each later level costs the one before plus row 1's level x the rate of its
  band, truncated (`DF 7C 24 14` at `0x00B5C12E`); the band starts at row 2 and moves on one row when the
  level reaches the next row's level (`3B 51 08 72 16` at `0x00B5C0E3`).
- `getTotalExp` (`0x00B5C150`) is the sum, 36,636,536 from level 200 on (`B8 78 07 2F 02` at `0x00B5C160`).
- Level-up compares `+0x2C4` with the next level's cost at `+0x2C8`
  (`8B 86 C4 02 00 00 8B 96 C8 02 00 00 3B C2` at `0x00B5BD7D`), below level 200
  (`81 F9 C8 00 00 00` at `0x00B5BD75`), and stores the new cost there
  (`57 E8 F3 00 00 00 56 89 86 C8 02 00 00` at `0x00B5BF87`).
- With the shipped table, the game's own `getNextExp`, run in the harness, gives:

| Level | 1 | 2 | 10 | 50 | 51 | 100 | 101 | 150 | 151 | 199 |
|---|---|---|---|---|---|---|---|---|---|---|
| Next level costs | 200 | 485 | 4,640 | 80,100 | 80,980 | 124,100 | 127,400 | 289,100 | 293,500 | 504,700 |

The harness runs with the x87 at 53-bit precision; the costs above are exact there (the products and sums
fit). Whether the game's x87 runs at single precision once Direct3D is up, which would change some
costs by a point, is UNKNOWN.

## What the plugin does

- The first five bytes of `sGameSys::addExp` become a jump to a thunk. It saves every register, the flags
  and `xmm0` to `xmm7`, changes only the amount argument, restores everything, runs the five bytes and goes
  on at `0x00443475`.
- The new amount, in whole numbers only (the game's x87 and SSE state are never touched):
  - the amount x `Kills` when `raw` is 0, x `Quests` when it is 1 (per mille);
  - when `LevelCost` is set and the Arisen's `+0x2C8` is more: x that cost / `LevelCost`, exactly
    (the quotient and remainder by `LevelCost` keep every product below 2^64);
  - rounded to the nearest point, at least 1 when the game gave at least 1, at most 999,999,999, so the
    game's own sums stay below 2^32 (1,000,000,000 + 999,999,999 x 1.25 x 2 x 1.25 at the most) and its
    caps hold.
- The Arisen's cost is read under SEH; a fault there leaves `LevelCost` out of that reward.
- What follows from the one entry: the main pawn, the vocation's discipline and the hired pawns' Rift
  Crystals all grow by the same factor, since the game derives them from the amount the plugin hands on.
  Hard Mode's double and the level spread still apply to kills, after the plugin.
- `Kills = 1`, `Quests = 1` and `LevelCost = 0` patch nothing.
- Before patching it compares 14 sites with build 2364871 (the entry, both `raw` tests, the Arisen's slot,
  both calls to `cPlayerInfo::addExp`, the cap, level-up's compare and store, and the five callers'
  arguments) and patches nothing on a difference.

| `LevelCost` | What it means with the shipped table |
|---|---|
| 0 | the game's curve |
| 125000 (shipped) | every level past 100 takes about as long as level 100: x2.3 at level 150, x4.0 at 199 |
| 80000 | every level past 50 about as long as level 50 |
| 5000 | about level 10's pace (4,640) all the way up |

## Proof (harness)

`native/plugins/xp_scale/test/run_tests.py` maps `DDDA.exe` into a stub process at its fixed base and
loads the built plugin, which verifies and patches that copy. It reads the game's level table from the
player's archives (never stored in git) and passes it to the harness.

- **Each case runs twice from the same state:** through the patched entry with the game's amount, and
  with the original five bytes put back and the amount the plugin should hand on. Every byte of the fake
  world (`sGameSys`, the Hard Mode object, the item manager) and every register (`eax`, `ecx`, `edx`, the
  four kept, `esp`, `xmm0` to `xmm7`) must come out the same, and the Arisen's experience must grow.
- **Cases:** a kill alone at level 12, a 1-point kill, a whole party, a rate of 0.5, a quest's reward, a
  kill and a quest at level 150 with the experience ability, Hard Mode with `sGameSys` locked, Hard Mode
  with a hired pawn away, a quest on Hard Mode (no double), nothing, and two rewards past the cap.
- **The level costs:** the real `getNextExp` for levels 1 to 199 against the model in `run_tests.py`, and
  the table above.
- **Profiles:** the shipped ini (1.5, 1.5, 125000), kills only (0.1), the most (2.0, 2.0, 1000), the
  game's own (nothing patched), `Kills = 3` and `LevelCost = 500` (refused), and an exe with one byte of
  the kill's call changed (refused). A second copy of the plugin refuses the patched entry.
- **Results:** 392 checks pass. With the shipped ini a goblin's 9 points become 14; a kill of 4,000 at
  level 150 becomes 13,877 (x1.5 x 289,100 / 125,000).

In game, what to look for:

- `riftstone\logs\xp_scale.log` says once what the first kill and the first quest gave, and once when
  `LevelCost` first raised a reward.
- The experience a kill shows on screen is the amount after the plugin (and after the level spread).

Whether every experience the player sees in game goes through these five calls, and how the faster
discipline feels, is UNKNOWN until played.

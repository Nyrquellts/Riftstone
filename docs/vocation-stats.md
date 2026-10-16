# Stats by your current vocation, at an inn (`vocation_stats` plugin)

In Dark Arisen every level-up adds the growth of the vocation you have at that moment, so a character's
stats remember every vocation it ever levelled in: a Warrior that gained its first hundred levels as a
Sorcerer has a Sorcerer's attack for good. The `vocation_stats` plugin changes that: each rest at an inn
gives the Arisen and the main pawn the stats their current vocation would have given them at every level
they have, as if they had gained all of them in it. Level and experience stay. Everything below is read
statically from `DDDA.exe` (build 2364871) and run in a harness on the game's own code. **In game:
UNKNOWN** until played.

## How the game grows the stats

- A character's base stats are six floats of its `cPlayerInfo` (the `uPlayer`'s `+0x3DEC`): HP `+0x2A0`,
  stamina `+0x2B0`, attack `+0x2B4`, defence `+0x2B8`, magick attack `+0x2BC`, magick defence `+0x2C0`;
  its vocation is `+0x10` (1 Fighter .. 9 Sorcerer, `docs/saves.md`), its level `+0x700`.
- The level tables are `param\pl\level\Lv<vocation>` (`rPlLevel`, in `rom\game_main`), one a vocation.
  The loader at `0x0047E2B0` names them in the vocation's order, Fighter twice for number 0
  (`B8 C0 EE 55 01 56 89 44 24 78 89 44 24 7C` at `0x0047E2BD`), keeps the experience table at
  `sPlayerManager +0x868` (`89 87 68 08 00 00` at `0x0047E3CB`) and each level table at `+0x86C + 4 x` the
  vocation (`8D 9F 94 08 00 00` at `0x0047E3D1`, `8B 54 34 78` at `0x0047E3E6`, `89 43 D8` at `0x0047E3FF`).
  - `"param\pl\level\LvFighter"` at `0x0155EEC0`, `"param\pl\level\LvStrider"` at `0x0155EEDC`,
    `"param\pl\level\LvMagicUser"` at `0x0155EEF8`, `"param\pl\level\LvPaladin"` at `0x0155F040`
    (the Mystic Knight), `"param\pl\level\LvAssassin"` at `0x0155F05C`, `"param\pl\level\LvTrickster"` at
    `0x0155F078` (the Magick Archer), `"param\pl\level\LvBattler"` at `0x0155EF14` (the Warrior),
    `"param\pl\level\LvHunter"` at `0x0155EF30` (the Ranger), `"param\pl\level\LvWizard"` at `0x0155EF48`
    (the Sorcerer); `"param\pl\level\exp"` at `0x0155F02C`.
  - A table's rows (`+0x70`, `+0x64` of them) are `{mLevel, mHPMax, mStaminaMax, mAttack, mDefend,
    mMgcAttack, mMgcDefend, mNextExp}`. Row 1 is level 1's stats; every later row is the growth a level
    gets from its `mLevel` on (the shipped tables: levels 2, 11, 101 and 201).
- The level-up (`0x00B5BD20`, the character pushed, then "notice" and "forced"; `ret 0xC`):
  - refuses a character with bit 8 of `+0x206D`, bit 2 with the byte `+0x31EA` set, or bit 4 with bit
    `0x8000` of `+0x273C` (`A8 02 74 09 80 BB EA 31 00 00 00 75 E8 A8 04 74 0C F7 83 3C 27 00 00 00 80 00 00 75 D8`
    at `0x00B5BD4C`), and one at level 200 (`8B B3 EC 3D 00 00 8B 8E 00 07 00 00 81 F9 C8 00 00 00 73 C4` at
    `0x00B5BD69`);
  - when forced, goes on past the experience it lacks (`80 7D 10 00 0F 84 DC 02 00 00 2B C2 41 89 86 C4 02 00 00`
    at `0x00B5BD8D`);
  - takes the current vocation's table (`8B 15 EC A4 8F 01 8B 4E 10 8B 8C 8A 6C 08 00 00` at `0x00B5BE61`)
    and the row of the new level's band, adds its growth, and caps HP at 9999
    (`F3 0F 58 86 A0 02 00 00 0F 2F C1 F3 0F 11 86 A0 02 00 00 72 08 F3 0F 11 8E A0 02 00 00` at `0x00B5BED7`;
    `00 3C 1C 46` at `0x0161CA24` is 9999.0);
  - stores the next level's cost at `+0x2C8` (0 at level 200), refills HP, and without notice stops there
    (`80 7D 0C 00 0F 84 92 00 00 00` at `0x00B5BFD7`), before the level-up's sound, effect and message.
- So the stats are the sum of each level's growth in whichever vocation took it. The game never
  recalculates them; changing vocation changes only what the next level adds.

## The game's own rebuild: setLevel

`setLevel` (`0x00B5CCB0`, the `uPlayer` in `edi`, the level pushed; `ret 4`) builds a character's stats
from nothing:

- it leaves a character with bit 8 of `+0x206D` alone
  (`55 8B EC 83 E4 F8 F6 87 6D 20 00 00 08 53 8B 5D 08 56 0F 85 32 01 00 00` at `0x00B5CCB0`) and caps the
  level at 200 (`81 FB C8 00 00 00 72 05 BB C8 00 00 00` at `0x00B5CCC8`);
- takes the current vocation's table
  (`8B B7 EC 3D 00 00 8B 46 10 8B 0D EC A4 8F 01 8B 94 81 6C 08 00 00` at `0x00B5CCD5`), sets level 1 and
  the six stats from its row 1 (`8B 42 70 8B 40 04 C7 86 00 07 00 00 01 00 00 00 0F B7 50 06` at
  `0x00B5CCEB`);
- runs the level-up without notice, forced, until the level is reached
  (`6A 01 6A 00 57 E8 73 EF FF FF 39 9E 00 07 00 00 73 12` at `0x00B5CDA3`);
- zeroes the experience toward the next level (`C7 86 C4 02 00 00 00 00 00 00` at `0x00B5CDCF`), writes
  the HP into `[[+0x1D18]] +0xC` while `+0x1D0C` is set (`83 BF 0C 1D 00 00 00 76 11` at `0x00B5CDD9`) and calls
  `0x00B807F0` with the character (`8B C7 E8 F6 39 02 00` at `0x00B5CDF3`).

A level-up that refuses would make setLevel's loop run for ever, so the plugin checks the level-up's
refusals itself first.

With the shipped tables, a character that gained every level in one vocation has (HP, stamina, attack,
defence, magick attack, magick defence; the game's own code, run in the harness):

| Vocation | Level 10 | Level 100 | Level 200 |
|---|---|---|---|
| Fighter | 720, 180, 116, 107, 78, 78 | 4,050, 1,530, 476, 467, 258, 168 | 5,550, 2,030, 576, 767, 258, 168 |
| Mage | 608, 180, 78, 87, 116, 107 | | 3,498, 2,080, 258, 177, 676, 667 |
| Strider | | | 3,405, 3,975, 467, 467, 467, 368 |
| Mystic Knight | | | 4,920, 2,480, 396, 477, 448, 448 |
| Assassin | | | 3,135, 4,155, 937, 377, 277, 178 |
| Magick Archer | | | 3,498, 2,980, 358, 357, 486, 667 |
| Warrior | | 4,320, 1,080, 566, 377, 258, 168 | 4,820, 2,580, 766, 577, 258, 168 |
| Ranger | | | 3,045, 4,425, 657, 377, 367, 368 |
| Sorcerer | | | 3,048, 2,530, 258, 177, 866, 657 |

## The rest

- `uInn` (`"uInn"` at `0x0158B464`) is the inn's rest. Its `MtDTI` at `0x01992488` (0xA40 bytes) is built
  at `0x0133E0D6` (`68 40 0A 00 00 68 B8 24 99 01 68 64 B4 58 01 B9 88 24 99 01`), and its vtable's getDTI
  returns it (`B8 88 24 99 01 C3` at `0x00735EF0`).
- Its state function at `+0x38` reaches `0x00736740` from two earlier states
  (`C7 41 38 40 67 73 00` at `0x0073670C` and at `0x00736736`); that state rests: by the byte `+0xA2C` it
  calls `0x00737580` with the inn pushed or `0x007377D0` with it in `esi`
  (`38 9E 2C 0A 00 00 75 08 56 E8 24 0E 00 00 EB 05 E8 6D 10 00 00` at `0x0073674E`). What tells the two
  apart (the kind of rest, or the inn) is UNKNOWN.
- The party: `sPlayerManager` (`[0x018FA4EC]`) slot 0 is the Arisen
  (`8B 0D EC A4 8F 01 8B 81 9C 09 00 00` at `0x007820A0`), slot `n` at `+0x9A0 + 4 x (n - 1)`
  (`8B 15 EC A4 8F 01 8B 84 82 A0 09 00 00` at `0x007820B6`), so the main pawn is `+0x9A0`.

## What the plugin does

- The two rest calls (`0x00736757`, `0x0073675E`) go to thunks. Each saves every register, the flags and
  `xmm0` to `xmm7`, recalculates, restores them and jumps on to the rest routine the game called, so the
  rest's own refill fills the new HP.
- For the Arisen and the main pawn (`Who`): the game's own `setLevel` at the character's level, then its
  experience toward the next level (`+0x2C4`, which setLevel zeroes) put back. The level, the next level's
  cost and everything else come out as the game's own code leaves them.
- Left as it is (and said in the log): no character, a character setLevel or the level-up would refuse, no
  `cPlayerInfo`, a vocation outside 1..9, a level outside 1..200, a missing level table or one with fewer
  than three rows or a row it cannot read, a missing experience table. If the game's code faults inside
  setLevel, that character's level, stats and experience are put back as they were.
- Hired pawns are never touched (their stats are their owners'), nor any other character.
- Before patching it compares 17 sites with build 2364871 (the rest calls, setLevel's start, cap, table,
  base row, loop, zeroing and return, the level-up's start, refusals, cap, forced path, table, HP cap and
  the no-notice exit, 9999.0, and both party slots) and patches nothing on a difference.
- `Mode = off` patches nothing. Turning it off does not put back any stats: they stay what the last rest
  made them until the next level-up adds to them.

What follows from using the game's own setLevel:

- The level-up's level-100 and level-200 marks: for a character with bit 2 of `+0x206D` it sets bit
  `0x800000` (`81 89 74 5A 00 00 00 00 80 00` at `0x00B5BDD6`) and at 200 bit `0x1000000`
  (`81 89 74 5A 00 00 00 00 00 01` at `0x00B5BE02`) of `[0x018D0CC4] +0x5A74` when `+0x5A36` there and
  `sGameSys +0xBE442` are set. A rest at level 100 or more passes through them again; they are only set,
  never cleared, and a character at that level already went through them once. What reads them is UNKNOWN.
- The new stats are the character's own from then on: the save after the rest keeps them, and a level-up
  later adds the vocation's growth to them as it always does. An older save comes back with the
  `save_backup` plugin's copies or `riftstone saves restore` (`docs/saves.md`).
- Equipment, augments and the vocation's skills are not base stats and do not change.

## Proof (harness)

`native/plugins/vocation_stats/test/run_tests.py` maps `DDDA.exe` into a stub process at its fixed base
and loads the built plugin, which verifies and patches that copy. It reads the nine level tables and the
experience table from the player's archives (never stored in git) and passes them to the harness. The rest
routines and three helpers (the level-up's `0x00782040` and `0x00888DA0`, setLevel's `0x00B807F0`) are
stand-ins; setLevel, the level-up and `getNextExp` are the game's.

- **Each case** rests through one of the two calls, with every register, the flags, `xmm0` to `xmm7` and the
  stack seeded. The whole fake world (`sPlayerManager`, the tables, `sGameSys` with the four party
  `cPlayerInfo`s, the marks' object) must come out as expected byte for byte: the stats replaced by the
  model's, the experience, level and the rest left. The rest routine must run once, with the inn and every
  register as the call site left them, after the new HP is in place; the level-up must run once a level,
  without notice, forced; no game code may run for the hired pawns.
- **Cases:** a Warrior at 120 grown as a Sorcerer and a Mage pawn at 118 grown as a Fighter (both rest
  calls), levels 1 and 2, level 200 with the marks armed, each of the level-up's refusals, no
  `cPlayerInfo`, no vocation, vocation 10, levels 0 and 201, no main pawn, a missing and a short level
  table, no player manager; then every vocation at levels 1, 2, 10, 11, 50, 100, 101, 150, 199 and 200,
  whose numbers must be `run_tests.py`'s own model's (the base row, then each level's band, HP capped at
  9999) and the table above.
- **Profiles:** the shipped ini, the Arisen only, the main pawn only, `Mode = off`, two bad values
  (refused), an exe with one byte of the level-up's refusals changed (refused), and tables with eight
  times the HP growth (49 of the 90 numbers reach the 9999 cap). A second copy of the plugin refuses the
  patched calls.
- **Results:** 6,318 checks pass.

In game, what to look for:

- `riftstone\logs\vocation_stats.log` says for each rest what changed: "rest (A): the Arisen, Warrior level
  120: HP 2248 -> 4420, stamina 1730 -> 1380, attack 258 -> 606, ..." (the harness's Warrior grown as a
  Sorcerer), or why a character was left.
- The status screen after a rest shows the base stats of the current vocation's own growth.

Which rests reach `uInn` (the inns' rest until morning and until evening, the encampment's), and how it
plays, is UNKNOWN until played.

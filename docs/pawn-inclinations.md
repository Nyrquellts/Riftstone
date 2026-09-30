# Pawn inclinations: how orders move them, and the `inclination_lock` plugin

Players call it the "Guardian plague": over a long game a main pawn drifts into Guardian and
Nexus, and many blame the pawn orders. This page says what the engine really does, measured on the
PS3 build (its names) and mapped byte for byte to the PC exe (build
2364871, `tools/re_dd.py`). It also covers the plugin that stops the drift. Addresses are PC unless
marked PS3.

## What the game does

A pawn has nine inclinations. The save keeps them as
`cSAVE_DATA_CMC.mInfo[INFO_TYPE_*]`, in this order:

| INFO_TYPE | Inclination | Elixir (item) |
|---|---|---|
| BELLIGERENT | Scather | 1520 |
| PRUDENT | Medicant | 1521 |
| POOR_AIM | Mitigator | 1522 |
| STRATEGY | Challenger | 1523 |
| TACTICS | Utilitarian | 1524 |
| PROTECTION | Guardian | 1525 |
| SAME_SUPPORT | Nexus | 1526 |
| CURIOSITY | Pioneer | 1527 |
| GATHER | Acquisitor | 1528 |

An elixir sets the inclination numbered (item − 1520). PS3 `cAICharacterInfo::changeInfoFromItem`
maps items 1520–1528 to types 0–8, and item 1529 (the Neutralizing Elixir) to a reset. The item
names (`id/message/item/itemName_*`) give the table's names. Each value is at most 1000 (`after()` limits it with the constant
at `0x0182E0D0`), and the two highest are the primary and secondary inclination. The save also holds
two "Ex" values, talk level and skill use (`INFO_TYPE_EX_TALK`, `_SKILL_USE`). They are not
inclinations.

**The drift.** `sAICharacterInfo` is a singleton (vtable `0x01559AE8`; PS3 `sAICharacterInfo`, named).
Its `move` (`0x00410B80`, vtable slot 6) runs every frame while the main pawn is out. It calls eleven
steps in the PS3 build's order: `calcBelligerent` `0x00410EF0`, `calcPrudent` `0x00411030`,
`calcPoorAim` `0x004112E0`, `calcStrategy` `0x00411520`, `calcTactics` `0x004116D0`,
`calcProtection` `0x004117F0`, `calcSameSupport` `0x004119A0`, `calcCuriosity` `0x00411C90`,
`calcGather` `0x00411E40`, `calcMate` `0x00411F10`, `calcSkilllUse` `0x004122A0`. Each step adds up a
change for its inclination at `+0x24 + 4 × type`. Then `after()` (`0x00410C90`) loops over the nine
inclinations. For each it adds the change to the main pawn's value, limits it, and clears the
change. The pawn is party slot 1, `[sPlayerManager+0x9A0]`: PS3 `sPlayerManager::getPlCmc` reads the
slots from there, with slot 0 the Arisen (`+0x99C` on PC) and slot 1 the main pawn. Its
`cAICharacterInfo` is `[pawn+0x2E64]+0x88`, and the value is `+4` of each `cInfo`. On PS3 this add is `cAICharacterInfo::addInfo`,
and `after` is its only caller. On PC it is inlined at `0x00410D66`–`0x00410D96`.

Nothing else drifts the values. On PS3 the other writers are `setInfo` / `returnInfo` /
`resetFromItem`. They are reached from `changeInfoFromItem` (the elixirs) and from
`aStage::move_pawn_school` (the Pawn Guild).

**The orders.** The Arisen's current pawn order is `[sPlayerManager+0x99C]+0x4ABC` (PS3
`uPlayerBase+0x49BC`). 8 means none. `uPlayerBase::receiveMessage` stores an order there (on PC,
`0x00B7A3F1`). `after()` sets it back to 8, and so does the pawn's own order handler
(`cAICheckSituationCmc::ctrlOrder`), which accepts 9 to 11. Three steps read it, each once per
cooldown. The values below are the constructor's constants, the same on PS3 and PC:

| Order | Guardian (`calcProtection`) | Also |
|---|---|---|
| 9 (Go!) | −4.0, cooldown 900 | Pioneer +20, cooldown 900 (`calcCuriosity`; +15 per 1800 once Pioneer ≥ 700) |
| 10 (Help!) | +2.67, cooldown 900 | Medicant +2.0, cooldown 6300 (`calcPrudent`; +1.0 per 10800 in its high band) |
| 11 (Come!) | +4.0, cooldown 900 | — |

Cooldowns count the frame step at `[0x018D0B28]+0x70`. Their unit is UNKNOWN; at 30 steps a second,
900 is 30 s. Pioneer also sinks by 5 every 4500 steps without orders.

So Come! and Help! push Guardian up, and Go! pulls it back down. **Nexus does not follow orders at
all:** `calcSameSupport` never reads the order field. That the three values are Go!, Help!, Come!
follows the HUD's own order list (`id/message/cockpit/cockpit_*`: `pawnorder_go`, `pawnorder_help`,
`pawnorder_come`) and what each value does. It has not been seen in game (UNKNOWN).

**The evidence in DDDA.exe** (build 2364871; `tools/doc_claims.py` re-reads every one of these in
the gate):

| What | Bytes |
|---|---|
| `move` is vtable slot 6 | `80 0B 41 00` at `0x01559B00` |
| `move` calls `after()` | `E8 06 00 00 00` at `0x00410C85` |
| `after()`: the pawn's `cInfo`, `je` over the add when there is none, then value + change | `8B 45 00 85 C0 74 2E F3 0F 10 07 F3 0F 58 40 04` at `0x00410D66` |
| `after()` stores the new value | `F3 0F 11 40 04` at `0x00410D96` |
| `after()` sets the order back to 8 | `C7 80 BC 4A 00 00 08 00 00 00` at `0x00410CB6` |
| `calcProtection` reads the order | `8B 89 BC 4A 00 00` at `0x00411872` |
| `calcProtection`: Come!, Help!, Go! | `83 F9 0B 75 1C` at `0x004118AE`, `83 F9 0A 75 1C` at `0x004118F9`, `83 F9 09 75 F8` at `0x0041194D` |
| `calcPrudent`: Help! | `83 BD BC 4A 00 00 0A 75 F0` at `0x004112A7` |
| `calcCuriosity`: Go! | `83 F9 09 75 5C` at `0x00411D84` |
| the constructor loads the Help! change | `F3 0F 10 3D C4 C7 61 01` at `0x004106E8` |
| Guardian +4.0, +2.67, −4.0 (floats) | `00 00 80 40` at `0x01518668`, `48 E1 2A 40` at `0x0161C7C4`, `00 00 80 C0` at `0x0161C270` |
| the cooldown 900.0 | `00 00 61 44` at `0x014F0840` |
| Pioneer +20.0, Medicant +2.0 | `00 00 A0 41` at `0x015184E0`, `00 00 00 40` at `0x014F9E04` |
| a value's upper limit, 1000.0 | `00 00 7A 44` at `0x0182E0D0` |

## The plugin

`native/plugins/inclination_lock` writes one byte per site, once at start-up. Nothing runs per frame.

| `Mode` | Sites | Effect |
|---|---|---|
| `freeze` (default) | `0x00410D6B` `je` → `jmp` | `after()` skips the add, so no inclination drifts. Elixirs and the Pawn Guild still set them. |
| `commands` | `0x004118B1`, `0x004118FC`, `0x00411950` (Guardian), `0x004112AE` (Medicant), `0x00411D87` (Pioneer): `jne` → `jmp` | Each order check behaves as if no order was given, so no change and no cooldown. Everything else drifts as vanilla. |
| `off` | none | vanilla |

Before patching, the plugin compares every site and the code around it (both modes' sites in either
mode, so a second copy or another patch shows up), `move`'s vtable slot, and `move`'s calls to
`after`, `calcPrudent`, `calcProtection` and `calcCuriosity`. On any difference it patches nothing
and says why in `riftstone\logs\inclination_lock.log`. An unknown `Mode` also patches nothing.

### Proof so far

`python native/plugins/inclination_lock/test/run_tests.py` maps a read-only copy of DDDA.exe into a
harness process and lets the plugin patch it. It then runs the game's own code:

- **After's add.** It enters at `0x00410D66` with a fake pawn value and a pending change, and stops
  before `calcHitInfo`. Vanilla gives 500 + 4 → 504, 500 − 4 → 496, and 999 + 4 → 1000 (the limit).
  Frozen, the value stays put. The loop's registers come through intact.
- **calcProtection, whole, on fake objects.** The Guardian change per order is −4 / +2.67 / +4 / 0,
  and the cooldown blocks a second Come!. In `commands` mode every change is 0 and no cooldown
  starts.
- **calcCuriosity, whole.** Pioneer +20 for Go! only; 0 in `commands` mode.
- **calcPrudent's order step,** entered with its own frame. Medicant +2 for Help! only; 0 in
  `commands` mode.

The constructor's constants are read from the mapped image and checked against the table above. It
runs four profiles: freeze, commands, off, and an unknown mode. A second copy of the plugin refuses
and changes nothing. `tools\test_all.cmd` runs it.

**Seen in game (2026-09-25 22:22, loader 0.3.1):** the plugin patched the real process
(`inclination_lock.log`: "Mode = freeze (game)", `0x00410D6B`). **UNKNOWN until observed in game:**
that inclinations then stay put over a session (not measured yet: the save's `mInfo[...]` before
and after would show it); which D-pad direction sends which order; and how
fast the vanilla drift is in play, since the cooldown unit is not measured. Hired (support) pawns do
not drift here: `after()` updates party slot 1 only.

### Install (writes to the game; the owner's yes first)

```bat
native\plugins\inclination_lock\build.cmd
riftstone loader install
riftstone loader plugin add native\plugins\inclination_lock\out\inclination_lock.asi
```

`plugin add` also copies `inclination_lock.ini` the first time and keeps the owner's copy on later
updates. Removal: `riftstone loader plugin remove inclination_lock.asi`.

## Not covered (leads)

- An in-game switch between modes would need an input hook; Ninput (`native/ninput`, in main) has a
  hotkey layer (`input_register_hotkey`).
- The save's `mInfo[...]` values could be read and set by a save tool (`src/riftstone/saves.py`
  unpacks the XML) instead of by elixirs.
- **Does the lock stop pawns looting?** A player asked (Nexus, 2026-09-27) whether it can. What is measured is the
  drift: `calcGather` (`0x00411E40`) feeds the Acquisitor value (`GATHER`, elixir 1528), and the lock stops that value,
  and every other, from moving. What that value changes in play (which item pick-ups, chest openings or house visits
  belong to Acquisitor, and whether any pawn does them at any value) is **not measured**: the game's text and resource
  names hold nothing about looting (searches for loot, gather, collect, pick up and steal find only stage and room
  names), so the behaviour is in the AI code or its tables, not yet located. Until it is, the honest answer is "reset
  the inclination, freeze it, and tell us what you see", not "it stops looting". The reset and the lock together are
  what exists today; a switch that zeroes the value each frame would need the reader of `GATHER` found first.

# Free sprint out of battle (`free_sprint` plugin)

Sprinting in Dark Arisen drains stamina everywhere, which makes crossing Gransys a chore. The
`free_sprint` plugin makes sprinting free while the game is not in battle, and leaves it as it was in
battle. Everything below is read statically from `DDDA.exe` (build 2364871), mapped through the PS3
build's names (`tools/re_dd.py`). The plugin runs the game's own code in a
harness. **In game: UNKNOWN** until played.

## How the game charges stamina

- Every frame, `uPlayerBase::before` runs `updateStamina` (`0x00B81450`) for the Arisen and each pawn.
  - It asks `calcStaminaRecover` (`0x00B81810`) for the frame's recovery: `0x00B81471` `E8 9A 03 00 00`.
  - It moves the player into `edi` and asks `calcStaminaConsume` (`0x00B81920`) for the drain:
    `0x00B81476` `8B FE F3 0F 11 44 24 0C`, then `E8 9D 04 00 00` at `0x00B8147E`.
  - It adds both, each scaled by the frame's time step, through `addStamina` (`0x00B81E30`).
- `calcStaminaConsume` takes the player in `edi` (a whole-program register convention the PS3 build
  does not have) and returns the drain negated in `xmm0`.
  - It negates with the sign mask at `0x0161C280`: `0x00B81BE0` `0F 28 C1 0F 57 05 80 C2 61 01`.
  - That mask is `00 00 00 80 00 00 00 80` at `0x0161C280`, so an action that costs nothing returns
    `-0.0`.
- The drain depends on the player's current action, the word at `+0x2DD4`. PS3 has it at `+0x2D24`:
  `uPlayerBase` sits 0xB0 later on PC there, as elsewhere.
  - The switch reads it (`8B 8F D4 2D 00 00 49 81 F9 E2 00 00 00` at `0x00B819FC`), then goes through
    a byte index and a jump table: `0x00B81A0F` `0F B6 89 4C 1D B8 00 FF 24 8D 1C 1D B8 00`.
  - Action 5 and action 0x7D have the same index, 2. The index bytes for actions 5 and 6 are
    `02 0B` at `0x00B81D50`, and for 0x7D and 0x7E `02 0B` at `0x00B81DC8`. Entry 2 is the sprint
    branch: `D5 1A B8 00` at `0x00B81D24`.
  - `uPlayerBase`'s action table (0x44 bytes an entry from `0x01866C48`) names them. Entry 5 is
    `cPlActDash` (`A8 1E 9A 01` at `0x01866D9C`, its MtDTI `0x019A1EA8`). Entry 0x7D is
    `cPlActDashBegin` (`48 23 9A 01` at `0x01868D7C`).
- The sprint branch (`6A 0A 8B C7 E8 32 EF FF FF` at `0x00B81AD5`) charges `numerator / denominator`
  from the player's sprint table.
  - The table is at `+0x3504` (PS3 `+0x3440`), or Assassin's own at `+0x353C` when the vocation word
    at `+0x354C` is 5.
  - The sprint branch already charges nothing in four cases: two `sGameSys` flags, one status bit of
    the player, and one flag its virtual at `+0x2F8` returns.
  - The first flag is story flag 483. The PS3 build asks `cGameInfo::getGsfFlag(0x1E3)` there. The PC
    build reads the flag's word inline: bit `0x10000000` of `sGameSys +0xAC720` is flag 483, word 15
    (`F7 81 20 C7 0A 00 00 00 00 10` at `0x00B81B6B`).
  - `cPlActDash::move`, the sprint action itself, tests the same bit
    (`F7 80 20 C7 0A 00 00 00 00 10` at `0x00ACB17A`).
  - What sets flag 483 in play, and what the other three cases mean, is not known.
  - An earlier, unfinished start on this plugin (parked on `wip/free-sprint-0926`) found the same
    mechanism and the flag. It patched the status-bit test at `0x00B81B8C` instead of the call. It
    never had a harness; this plugin supersedes it.
  - The PS3 build has the same branch for the same two actions (PS3 `0x009050F8`).
- Walking (1) and running (2) charge nothing in this function; they only choose the recovery row.
  - Carrying (`cPlActLiftGeneric`, `cPlActLiftWalk`) and climbing a monster (`cPlActEnemyClimb`) have
    branches of their own.
  - A jump from a sprint (`cPlActDashJump`, actions 0xA to 0xC) is charged once, by the jump itself
    (`PlStaminaDashJump`), not here.

## The game's battle state

`sGameSys` (`[0x018FA4BC]`) keeps one byte at `+0xBE2CC` that says the game is in battle (PS3
`+0xA4E04`).

- `calcEnableBattleMode` (`0x00441650`, run by `sGameSys::move`) reads and sets it:
  - it reads the byte: `0x00441659` `80 BF CC E2 0B 00 00`;
  - it sets it: `0x00441688` `66 C7 87 CC E2 0B 00 01 00`;
  - otherwise it decides from the stage's enemies near the Arisen (`0x0044169F`
    `A1 EC A4 8F 01 8B 80 9C 09 00 00`): their state, whether the camera sees them, how far away
    they are.
- 90 instructions of the PC build read the byte, as 90 do in the PS3 build. On PS3 they are
  `sSoundManager::move` (the battle music), the pawns' inclination steps (`sAICharacterInfo::calc*`),
  their talk, and `uPlayerBase::after`.
- `sSave::isManualSaveEnable` calls `disableBattleModeCore` (PS3).
- This byte is the game's own "in battle" state. Dark Arisen has no weapon-sheathe state a player
  controls: weapons come out and go away with the actions themselves. So the plugin keys on this byte,
  not on the weapon.

## What the plugin does

`native/plugins/free_sprint` rewrites the five bytes at `0x00B8147E` into a call to its own thunk.

- The thunk calls the game's `calcStaminaConsume` first.
- Then it lets a sprint go free when all of these hold:
  - the action is 5 or 0x7D;
  - `Mode = out_of_battle` and the battle byte is 0, or `Mode = always`;
  - `Who = party`, or `Who = arisen` and the player is `[0x018FA4EC] +0x99C`.
- A free sprint returns `-0.0`, the value the game itself returns for an action that costs nothing.
- Every other register comes back exactly as the game's function left it:
  - `eax`, `ecx` and `edx`, the stack, the flags, and `xmm1` to `xmm7`;
  - the recovery value the branch stores at `+0xEE4`.
- The check runs under SEH: a fault reading the game's state charges the game's own drain.
- Before patching, the plugin compares these with build 2364871 and patches nothing on a difference:
  - the call site and the code around it;
  - both functions' starts, the switch, the sprint branch and its return;
  - the index and jump tables for both actions;
  - the action table's two classes;
  - `calcEnableBattleMode`'s own use of the byte.

A second copy of the plugin, another patch of the call, or another build is refused, with the reason
in `riftstone\logs\free_sprint.log`. The log also says once when a sprint first went free and once
when one was charged in battle, so a session shows whether it worked.

## Proof (harness)

`native/plugins/free_sprint/test/run_tests.py` maps `DDDA.exe` into a stub process at its fixed base
and loads the built plugin, which verifies and patches that copy. It then runs the real
`calcStaminaConsume` on a fake player twice for every case: directly, and through the call at
`0x00B8147E`.

- **Cases:**
  - sprint and sprint start, in and out of battle, for the Arisen and a pawn, with Assassin's own
    table;
  - walking, running, a sprint jump, climbing a monster and carrying.
- **Profiles:** the shipped `out_of_battle`/`party`, `always`, `arisen`, `off`, an unknown mode, and
  an exe with one byte of the switch changed (refused).
- **Results:** 369 checks.
  - The game charges the table's rate (0.2 and Assassin's 0.15).
  - The plugin returns `-0.0` exactly where the settings say, and the game's own value everywhere else.
  - Every other register matches the unpatched call.
  - A game state whose battle byte cannot be read falls back to the game's own drain.

In game, what to look for:

- Sprint across a field: the stamina bar holds.
- Meet enemies: once the battle music starts, sprinting drains as usual.
- `free_sprint.log` shows both lines.

Whether the battle byte clears everywhere the music does, and how pawns keep up, is UNKNOWN until
played.

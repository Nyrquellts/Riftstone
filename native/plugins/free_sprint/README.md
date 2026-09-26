# free_sprint (Riftstone native plugin)

Sprinting costs no stamina while you are out of battle. In battle it costs what it always did. The
measurements and the proof are in [`docs/free-sprint.md`](../../../docs/free-sprint.md).

- **Game:** DDDA.exe build 2364871 only.
  - The plugin first compares with that build:
    - the call it rewrites, `updateStamina` and `calcStaminaConsume` around it;
    - the switch on the player's action, and the index and jump tables for both sprint actions;
    - the action table's `cPlActDash` and `cPlActDashBegin`;
    - the game's own use of its battle byte.
  - On any difference it patches nothing and logs why to `<game>\riftstone\logs\free_sprint.log`.
- **Code:** original, no third-party source.
  - It rewrites one call (5 bytes at `0x00B8147E`), once at start-up.
  - Each frame the game's own stamina code runs first. The plugin then looks at the player's action
    and the battle byte, and zeroes only the sprint's drain.
  - Every other register comes back as the game left it.
- **Settings:** [`free_sprint.ini`](free_sprint.ini), next to the `.asi`:

| Key | Value | What changes |
|---|---|---|
| `Mode` | `out_of_battle` (default) | Sprinting and starting a sprint cost no stamina while the game is not in battle, the state that starts the battle music. |
| | `always` | Sprinting never costs stamina. |
| | `off` | Nothing. |
| `Who` | `party` (default) | You and your pawns. |
| | `arisen` | Only you; your pawns tire as before. |

A jump from a sprint, climbing a monster and carrying still cost stamina in every mode.

Dark Arisen has no weapon-sheathe state a player controls: weapons come out and go away with the
actions themselves. So "out of battle" is the game's own battle state, not the weapon's.

`free_sprint.log` notes once when a sprint first goes free and once when one is charged in battle.

In game: UNKNOWN until played.

## Build

Needs Visual Studio 2022 with the C++ x86 tools. No network is needed.

```bat
native\plugins\free_sprint\build.cmd
```

| File | What |
|---|---|
| `out\free_sprint.asi` + `out\free_sprint.ini` | the plugin and its settings |
| `out\sprint_stub.exe` + `out\sprint_harness_core.dll` | the test harness |

## Test (no game launched)

```bat
python native\plugins\free_sprint\test\run_tests.py
```

This maps a read-only copy of DDDA.exe into the harness process and lets the plugin patch that copy.
It then runs the game's own `calcStaminaConsume` on a fake player, directly and through the patched
call:

- **Cases:** sprinting and starting a sprint, in and out of battle; the Arisen and a pawn; Assassin's
  own sprint table; walking, running, a sprint jump, climbing and carrying.
- **Profiles:** the shipped settings, `always`, `arisen`, `off`, an unknown mode, and an exe with one
  byte changed, which the plugin refuses.
- **Each case checks:**
  - the drain returned;
  - that every other register matches the unpatched call;
  - that a game state the plugin cannot read falls back to the game's own drain.

369 checks pass. `tools\test_all.cmd` runs it; without a build or without the game it reports a skip.

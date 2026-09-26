# inclination_lock (Riftstone native plugin)

Your main pawn's inclinations stay where you set them. In the unmodded game the pawn orders nudge
them: every **Come!** adds 4 to Guardian and every **Help!** adds 2.67, which over a long game makes a
pawn Guardian whether you wanted one or not. The measurements and the proof are in
[`docs/pawn-inclinations.md`](../../../docs/pawn-inclinations.md).

- **Game:** DDDA.exe build 2364871 only. The plugin compares every site first, along with the code
  around it, `sAICharacterInfo`'s vtable and the calls from its update. On any difference it patches
  nothing and logs why to `<game>\riftstone\logs\inclination_lock.log`.
- **Code:** original, no third-party source. It changes one byte per site, once at start-up, so
  nothing runs per frame.
- **Settings:** [`inclination_lock.ini`](inclination_lock.ini), next to the `.asi`:

| `Mode` | What changes |
|---|---|
| `freeze` (default) | No inclination drifts. Elixirs and the Pawn Guild still set them, so shape the pawn first, then freeze. |
| `commands` | Only the orders stop counting: Go! (Guardian −4, Pioneer +20), Help! (Guardian +2.67, Medicant +2), Come! (Guardian +4). Everything else changes them as in the unmodded game. |
| `off` | Nothing. |

Talk level and skill use are separate values and are left alone in every mode. Which order is 9, 10
and 11 in the code (Go!, Help!, Come!) follows the HUD's order list and what each one does. In game:
UNKNOWN until played.

## Build

Needs Visual Studio 2022 with the C++ x86 tools. No network is needed.

```bat
native\plugins\inclination_lock\build.cmd
```

| File | What |
|---|---|
| `out\inclination_lock.asi` + `out\inclination_lock.ini` | the plugin and its settings |
| `out\incl_stub.exe` + `out\incl_harness_core.dll` | the test harness |

## Test (no game launched)

```bat
python native\plugins\inclination_lock\test\run_tests.py
```

This maps a read-only copy of DDDA.exe into the harness process and lets the plugin patch that copy.
It then runs the game's own inclination code on fake objects:

- the add in `after()`, where the pawn's value changes;
- the whole of `calcProtection` (Guardian) and `calcCuriosity` (Pioneer);
- `calcPrudent`'s order step (Medicant).

Each runs for Go!, Help!, Come! and no order, with the constructor's own numbers. There are four
profiles: freeze, commands, off, and an unknown mode, which patches nothing. `tools\test_all.cmd` runs
it too. It reports a skip when the plugin isn't built or the game isn't found.

## Install

This writes to the game, so it needs the owner's yes:

```bat
riftstone loader install
riftstone loader plugin add native\plugins\inclination_lock\out\inclination_lock.asi
```

The first `plugin add` also copies `inclination_lock.ini`, and later updates keep the owner's edits.
To undo the plugin, remove it; values it kept frozen simply start drifting again.
Removal: `riftstone loader plugin remove inclination_lock.asi`.

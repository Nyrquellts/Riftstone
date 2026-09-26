# six_skill_warrior (Riftstone native plugin)

A Warrior gets six skills the way a Mage's staff has them: the skill menu shows two rows of three for
the longsword and warhammer, the first row fires with the main-weapon skill button and the second with
the secondary-weapon skill button. Your pawns get the same when they are Warriors. The measurements and
the proof are in [`docs/six-skill-warrior.md`](../../../docs/six-skill-warrior.md).

- **Game:** DDDA.exe build 2364871 only. The plugin compares all 24 sites it patches, checkCmdType's jump
  table, the longsword skill table and the other data it relies on first. On any difference it patches
  nothing and logs why to `<game>\riftstone\logs\six_skill_warrior.log`.
- **How:** the game already keeps and saves six skills per weapon; only the staff uses the second three,
  through code that compares weapon and menu category numbers. Each of those compares jumps to a small
  thunk that also accepts the Warrior's numbers and then continues in the game's own code. The weapon
  compares and the skill dispatch accept them only for the party (you and your pawns), so other humans
  with a longsword stay as they were. Nothing runs per frame beyond the compares themselves.
- **Your save:** nothing in it grows. Without the plugin the game ignores the second row again (the
  skills stay written in the save).
- **Code:** original, no third-party source.
- **Settings:** [`six_skill_warrior.ini`](six_skill_warrior.ini), next to the `.asi`:

| `Mode` | What changes |
|---|---|
| `six` (default) | Six skill slots for a Warrior: equip them at the Inn's skill menu, fire the second row with the secondary-weapon skill button and a skill button. |
| `off` | Nothing. |

Which physical button is the secondary-weapon skill button follows your key configuration. In game:
UNKNOWN until played (the menu's second row for a Warrior, the HUD palette, pawns choosing the new skills).

## Build

Needs Visual Studio 2022 with the C++ x86 tools. No network is needed.

```bat
native\plugins\six_skill_warrior\build.cmd
```

| File | What |
|---|---|
| `out\six_skill_warrior.asi` + `out\six_skill_warrior.ini` | the plugin and its settings |
| `out\ssw_stub.exe` + `out\ssw_harness_core.dll` | the test harness |

## Test (no game launched)

```bat
python native\plugins\six_skill_warrior\test\run_tests.py
```

This maps a read-only copy of DDDA.exe into the harness process and lets the plugin patch that copy.
Then:

- every patched compare runs from its first byte with every value, for a party member and for anyone
  else, and must take the path the plugin describes and give every register back unchanged;
- the game's own functions run on fake players: `setSkillFromEquipWeapon`, `removeIllegalCstmSkill`,
  `removeJobMismatchCstmSkill`, `getNextAction` (which action each skill button starts),
  `initMainWpnMotion`/`initSubWpnMotion` (which skill motion lists load and stay), the skill-archive
  loader, and the skill menu's own slot counts.

Four profiles: the shipped ini, `off`, an unknown mode, and an exe with one byte changed (refused). 405
checks. `tools\test_all.cmd` runs it too. It reports a skip when the plugin isn't built or the game isn't
found.

## Install

This writes to the game, so it needs the owner's yes:

```bat
riftstone loader install
riftstone loader plugin add native\plugins\six_skill_warrior\out\six_skill_warrior.asi
```

The first `plugin add` also copies `six_skill_warrior.ini`, and later updates keep the owner's edits.
Removal: `riftstone loader plugin remove six_skill_warrior.asi`.

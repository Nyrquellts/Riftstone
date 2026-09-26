# save_backup (Riftstone native plugin)

Dragon's Dogma: Dark Arisen has one save slot, and every autosave, checkpoint and inn rest
overwrites it. This plugin copies each save the game writes, so an earlier one can be put back
with `riftstone saves restore`. The file format, the folder layout and the proof are in
[`docs/saves.md`](../../../docs/saves.md).

- **What it touches:** it patches no game code. A background thread looks at
  `Steam\userdata\<account>\367500\remote\DDDA.sav` every 2 seconds. When a new save has stopped
  changing and its header and checksum say it is complete, the plugin copies it to
  `%LOCALAPPDATA%\Riftstone\saves\<account>\DDDA_<date>_<time>.sav`.
- **Never next to the save:** it writes nothing in Steam's folder, so Steam Cloud never syncs the
  copies. It never writes the save itself either; it only reads it, for a moment after the game has
  finished writing.
- **What it keeps:** the newest 20 copies, plus the save each of the last 10 game sessions started
  from (`sessions.txt`), so one bad session can always be undone. Each copy is 512 KB.
- **Settings:** [`save_backup.ini`](save_backup.ini), next to the `.asi`: `Keep`, `KeepSessions`,
  `Folder`, `SaveFile`, `CheckSeconds`. `riftstone\logs\save_backup.log` records every copy.
- **Code:** original, no third-party source.
- **Next to the loader's own backup:** the Riftstone loader also copies the whole save folder once
  per game start (`docs/runtime.md`). This plugin adds a copy of every save made during play. The
  two use separate folders and never prune each other's; `riftstone saves` lists both.

## Putting a save back

Close the game first. The command refuses while `DDDA.exe` is running, because the game would write
over the restored save, and it refuses a copy whose save does not check.

```bat
riftstone saves list             :: every copy (this plugin's and the loader's), newest first, numbered
riftstone saves restore 3 --yes  :: copy 3 becomes your save again
```

Before replacing the save, `restore` copies it too, so the restore can itself be undone. If Steam
reports a cloud conflict the next time the game starts, keep the files on this PC.

## Build

Needs Visual Studio 2022 with the C++ x86 tools. No network is needed.

```bat
native\plugins\save_backup\build.cmd
```

| File | What |
|---|---|
| `out\save_backup.asi` + `out\save_backup.ini` | the plugin and its settings |
| `out\backup_host.exe` | a stand-in game process for the test |

## Test (no game, no real save touched)

```bat
python native\plugins\save_backup\test\run_tests.py
```

The test loads the real `.asi` into a 32-bit stand-in game process, points it at a temporary Steam
folder and a temporary Riftstone home, and writes saves the way the game does. It runs three game
sessions and reads every copy back with `riftstone.saves`. It checks that:

- each new save is copied once, and a half-written or unchanged one is not;
- the newest `Keep` copies and the last `KeepSessions` session starts stay;
- nothing is written next to the save, and no `.tmp` file is left behind;
- `Enabled = 0` copies nothing.

`tools\test_all.cmd` runs it too.

## Install

This writes to the game, so it needs the owner's yes:

```bat
riftstone loader install
riftstone loader plugin add native\plugins\save_backup\out\save_backup.asi
```

The first `plugin add` also copies `save_backup.ini`, and later updates keep the owner's edits.
Removal: `riftstone loader plugin remove save_backup.asi` (the copies stay where they are).

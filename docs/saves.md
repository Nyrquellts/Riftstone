# Your save: the file, its copies and `riftstone saves`

Dragon's Dogma: Dark Arisen has one save slot per Steam account. Every autosave, checkpoint and inn
rest overwrites it, so a failed quest or a bad choice can stick. The loader copies the save folder
each time the game starts, the `save_backup` plugin copies each save the game writes while you play,
and `riftstone saves` lists both and puts one back. `riftstone saves knowledge` reads the main pawn's
enemy knowledge from the save and can raise it to the top (below).

| Kind (as the list names it) | Made by | When | Where |
|---|---|---|---|
| save folder | the Riftstone loader (`[saves] backup`, `docs/runtime.md`), or `riftstone saves backup` | once per game start, before the game reads its save; every file of the save folder | `%LOCALAPPDATA%\Riftstone\saves\DDDA\<account>\<YYYYMMDD-HHMMSS>\` |
| while playing | the `save_backup` plugin (below) | every complete new save while you play, plus the save each session started from | `%LOCALAPPDATA%\Riftstone\saves\<account>\DDDA_<date>_<time>.sav` |
| before a restore | `riftstone saves restore` | the save folder as it was, just before a copy of it was put back | beside the loader's copies, `<time>-before-restore` |

The loader's copy undoes a whole session; the plugin's copies undo one save within it. Neither reads
or prunes the other's folder. `riftstone saves` numbers them together per account, newest first.

## The file (measured on a real save, 2026-09-25, read only)

The game writes through Steam's remote storage (the exe imports `"SteamRemoteStorage"` at
`0x0161F63A` and names its save `"DDDA.sav"` at `0x01564F1C`) to
`Steam\userdata\<account>\367500\remote\DDDA.sav`. The file is always 524,288 bytes:

| Offset | What |
|---|---|
| +0 | version, 21 for Dark Arisen (5 was the original game, per `vendor/ddda-save-editor`) |
| +4 | the XML's size (20,441,680 bytes on the measured save) |
| +8 | the compressed size (464,321) |
| +12, +16, +20 | fixed words `0x334D234D`, `0`, `0x334D4044` |
| +24 | the compressed bytes' CRC-32, inverted |
| +28 | fixed word `0x40565235` |
| +32 | the save as zlib data (`78 5E`), unpacking to XML that starts `<class name="dd_savedata"...` |
| after it | zeros to the end of the file |

The game compresses at zlib level 3: compressing the real save's XML at level 3 gives its stored
bytes exactly (463,136 of them on 2026-09-26), where level 6 gives 295,435 and level 2 gives 487,064.

`src/riftstone/saves.py` reads it: `check` validates the size, the fixed words, the version, the
sizes and the checksum, and with `deep` also that the XML unpacks to exactly the stated size.
`unpack` returns the XML and `pack` builds a save from XML at the game's level 3, so packing a save's
own XML gives back the game's file byte for byte. `tests/test_saves.py` runs `check(deep)` and that
rebuild on this machine's save if there is one (read only). A flipped byte anywhere in the
compressed data fails the checksum, which is how a half-written save is told apart from a complete
one. Fuzz target `save` checks that invariant and the XML round trip.

## The plugin's copies

`native/plugins/save_backup` runs a background thread inside the game (`docs/loader.md` covers how
plugins load). Every `CheckSeconds` (default 2) it looks at each account's save. When the save has
changed, has then stayed the same for one more look, and passes the header and checksum check, the
plugin copies it to:

```
%LOCALAPPDATA%\Riftstone\saves\<account>\DDDA_<date>_<time>.sav
```

The name is the save's own time. The copy is written as `.tmp` and renamed when complete, and
carries the save's timestamp. It is skipped when the newest copy already holds the same bytes, as
when Steam touches the file's time. `sessions.txt` records the copy each game session started
from. The plugin keeps the newest `Keep` (20) copies plus the last `KeepSessions` (10) session
starts, and deletes older copies as it goes.

The plugin never writes the save, and it writes nothing in Steam's folder, so Steam Cloud never
syncs the copies. The save is open for reading for about a millisecond, after the game has finished
writing it, with every share mode allowed.

`Folder` in `save_backup.ini` moves the copies; `riftstone saves` reads the installed plugin's ini
to find them. `$RIFTSTONE_HOME` moves Riftstone's home and the copies with it.
`$RIFTSTONE_STEAM_ROOT` points both at another Steam folder (the tests use it).

## Putting one back

```bat
riftstone saves list              :: each account's save, then every copy, newest first, numbered
riftstone saves restore 3 --yes   :: copy 3 goes back (without --yes it says what it would do)
riftstone saves backup            :: copy the save folder now, as the loader does at a game start
```

`riftstone save` is the same command. `restore` refuses while `DDDA.exe` is running, since the game
would write over the save, and refuses any copy whose save does not check all the way through (the
list marks those "NOT complete"). A save-folder copy puts back every file of the folder, and the
folder as it was is kept first as a "before a restore" copy. A copy made while playing puts back the
save, and the save it replaces is kept as a copy of that kind (a damaged one as
`replaced_<time>.sav`, a name the list leaves out). Either way the new file is written next to the
old one and swapped in with one rename. If Steam reports a cloud conflict the next time the game
starts, keep the files on this PC.

Until 2026-09-26 the loader's copies had their own `saves` command and went back without a check;
the plugin's had `save`. One command now lists and checks both.

## The main pawn's knowledge (`riftstone saves knowledge`)

A pawn's knowledge of an enemy family is five levels, and the game works them out from counters it
keeps in the pawn's record (`cSAVE_DATA_CMC` in the save, `mStudyData`):

| Counter | Values | What it counts |
|---|---|---|
| `mStudyData.EncountFrame` | 72 `f32` | frames spent fighting each enemy group |
| `mStudyData.KillCnt` | 72 `u32` | kills in each group |
| `mStudyData.UniqueCnt` | 116 `u8` | how often each special feat was seen or done |

A group's level is the number of its five thresholds the pawn has met, each a time and a kill count
(group 0: 60, 150, 300, 300 and 300 seconds, with 0, 0, 30, 150 and 500 kills). The PS3 build turns
counters into knowledge as the pawn runs (`sAIStudyCtrl::move`, PS3 `0x00020A9C`, through
`updateStudyFlagCommon` and `updateStudyFlagUnique`). The knowledge books go through two PC routines
that read the same thresholds and only ever raise a counter:

| Routine (PC) | Evidence | What it shows |
|---|---|---|
| `setStudyCommonData` | `B9 E8 B6 53 01` at `0x00417040` | the table of 92 {study key, group} entries at `0x0153B6E8` |
| | `3D E0 02 00 00` at `0x00417051` | the loop's end: 0x2E0 bytes, 92 entries of 8 |
| | `83 F9 48` at `0x0041705E` | group 72 is "no group" (group 51 is the only one no entry names) |
| | `83 FF 04` at `0x00417063` | levels 0-4 |
| | `A1 EC A4 8F 01` at `0x00417068` | `sPlayerManager`; with the next test, only while the main pawn (`+0x9A0`) exists |
| | `83 B8 A0 09 00 00 00` at `0x0041706D` | the main pawn slot |
| | `F3 0F 10 84 00 C8 B9 53 01` at `0x00417084` | seconds, `f32 [72][5]` at `0x0153B9C8` |
| | `F3 0F 59 05 60 A1 4E 01` at `0x0041708D` | times frames per second, in 32-bit floats (`mulss`) |
| | `00 00 F0 41` at `0x014EA160` | that rate: 30.0 |
| | `0F 2F 04 8A` at `0x00417097` | compared with the frames counter, written only when larger |
| | `8B 80 68 BF 53 01` at `0x004170A5` | kills, `u32 [72][5]` at `0x0153BF68` |
| | `39 84 8A 20 01 00 00` at `0x004170AB` | the kill counters at `+0x120`, right after the 72 frame counters |
| `setStudyUniqueData` | `B8 08 C5 53 01` at `0x004170D9` | 113 feats {flag, slot, count} at `0x0153C508` |
| | `8A 94 32 40 02 00 00` at `0x004170E7` | the feat counters at `+0x240`, after the kills |
| | `81 F9 4C 05 00 00` at `0x004170F9` | the loop's end: 0x54C bytes, 113 feats of 12 |

The counters sit in the same order in the save's XML as in memory. The thresholds are game data, so
Riftstone never stores them: `saves.knowledge_tables` reads them from the installed `DDDA.exe`, only
after the code above matches byte for byte (build 2364871; any other build is refused).

```bat
riftstone saves knowledge                :: each group's level by the counters, and the feats reached
riftstone saves knowledge --json
riftstone saves knowledge --grant --yes  :: raise every counter to the top (close the game first)
```

`--grant` raises the main pawn's counters to what the top level of every group and every feat asks,
in both copies of the player's data the save holds (`mPlayerDataManual` and `mPlayerDataBase`, each
with a main pawn record, `mPawnType` 1). Counters only go up, as the game's own routines do. Only the
counters' value text changes, written as the game writes it (`%.6f` frames, never below the game's
32-bit threshold; plain digits). Everything else in the XML stays byte for byte. It refuses to run:

- while `DDDA.exe` is running;
- on a save that does not check all the way through;
- on a counter array holding anything besides its values, or a value the game would not write.

The save as it was is kept first, as a copy beside the plugin's (`saves list` shows it and `saves
restore` puts it back), and the new save is swapped in with one rename.

Measured on this machine's save (2026-09-26, in memory, nothing written): the main pawn's counters
reached the top in 6 of 71 groups and 77 of 113 feats. A grant would change 253 values, the result
checks, the counters then reach 71 of 71 and 113 of 113, and a second grant changes nothing.

**UNKNOWN until observed in game:** that the pawn shows the new levels and uses the tactics that go
with them, and whether the pawn other players hire from the Rift carries them.

## Proof so far

- `tests/test_saves.py`: the format against a real save; that damaged or partial saves are refused;
  copy naming and de-duplication; listing and session starts; both kinds in one list and each put
  back (a save-folder copy restores the folder's other files too); a damaged save-folder copy is
  never put back and nothing is set aside for it; restore while running refused; a damaged current
  save kept aside; the plugin's `Folder` setting; and the CLI under both names, `--yes` required.
- `tests/test_plugins.py`: the plugin's settings through Studio's checks (`Keep`, `KeepSessions`,
  `CheckSeconds`, `Enabled`, `Folder`).
- `native/plugins/save_backup/test/run_tests.py` loads the real `.asi` in a stand-in game process
  against a temporary Steam folder and runs three game sessions. It checks that:
  - each new save is copied once, and a half-written or unchanged one is not;
  - two accounts are kept apart;
  - the newest `Keep` and the last `KeepSessions` session starts stay, and older starts go;
  - nothing is written next to the saves, and no `.tmp` is left behind;
  - `Enabled = 0` copies nothing.
- `fuzz/run.py --targets save`: 5.7 million inputs in 120 s, 0 findings (2026-09-25).
- Knowledge (`tests/test_saves.py`, `KnowledgeTest`): levels from the counters; the grant raises only
  the main pawn's counters in both copies, only upward, and leaves every other byte (another pawn
  record included); refusals (no main pawn, a short array, unbalanced XML, values the game would not
  write, anything else inside a counter array, nested pawn records, a program that is not the known
  build); the 32-bit rounding of thresholds; the copy kept before the file changes; the real tables
  from this machine's exe; the CLI, `--yes` required. Fuzz target `save_knowledge` feeds hostile XML
  to the grant with stand-in thresholds. The invariants: only counter values change, each upward and
  as the game writes it, and all inside the main pawn's records; every group and feat reaches the
  top; a second grant changes nothing. Its first run found an element opened inside a counter's value
  that the grant then edited; counter arrays are now strict (regression cases in `test_refusals`).

**Seen in game (2026-09-25):** the loader copied the save folder at each game start (09:52 and 22:22,
`loader.log`: "saves backed up 9 file(s)"). In the 22:22 session (loader 0.3.1) the plugin loaded,
found the save it started from already copied ("unchanged since DDDA_2026-09-25_22-17-16.sav"),
copied a save made in play (22:24:44), and made no copy of the next one (22:27:43), which held the
same bytes: the game rewrites unchanged saves, which is why `saves list` says which copy holds the
save. Steam replaces the file (the `remote` folder's own time changes with each save) and the
plugin read it without a refusal. **UNKNOWN until observed in game:** how Steam Cloud reacts to a
restored save.

# Riftstone Studio, the exe (`native/studio`)

`RiftstoneStudio.exe` is a 32-bit Windows program with no other DLL and no Python. It finds Dark Arisen, switches
Riftstone's features on and off, edits their settings, writes and reads overhaul profiles, turns safe mode off and
starts the game. It sets the game's field of view and view range, ENB's effects, the loader's DXVK chain and how
many enemies come at once; it shows the running game's live stats; and it exports the folder's Riftstone setup as
one zip for another player. Each of these does what the toolkit does, and the tests prove it against the toolkit.
It is the front end of the owner's "Riftstone Executive Framework & Studio" spec (2026-10-13); what the spec asked
of the game itself is in plugins and the loader (the list at the end).

The browser Studio (`riftstone studio`, `src/riftstone/studio.py`) is unchanged. The exe is a second front end on
the same files and the same rules.

Tier: **SIMULATED, gameplay UNOBSERVED.** Every test runs the exe against a stand-in game folder and the toolkit
on a twin of it. Launch was tested only with a stand-in `DDDA.exe` (`test/fake_ddda.cpp`). Nothing here has run
against the owner's game folder or a real game process.

## What it does

| Page | What |
|---|---|
| Home | The game folder, found as `game.py` finds it: the given `--game`, a folder saved in `%LOCALAPPDATA%\Riftstone\studio.ini` (`[game] folder`), the exe's own folder, `RIFTSTONE_GAME`, then every Steam library (the registry's `SteamPath`/`InstallPath`, read only; the default `C:\Program Files (x86)\Steam`; `steamapps\libraryfolders.vdf`). The loader's version and how the last session ended (`runtime-state.ini`). Safe mode with a button to end it. Launch, which starts `DDDA.exe` in its own folder and refuses while one runs. |
| Features | Every plugin in `riftstone\plugins` (on) and `plugins\off` (off), sorted by name, a plugin in both counted where it runs (`plugins.describe`). A switch moves the DLL and its ini together, refuses while the game runs and refuses a move whose target is taken. |
| Settings | A feature's ini (or the loader's, `riftstone_loader.ini`) with the help comments `plugins.read_settings` finds. Sliders for ranges, segments for choices, a field for the rest. Every value is checked by `plugins.rule_for`'s rule before it is written. The file keeps its encoding: UTF-16 behind its mark, else the code page; other lines keep every byte. |
| Profiles | Export the folder's features and checked settings to a `riftstone-profile/1` file, or import one. The import shows the dry run's plan first and applies it only on Apply. |
| Graphics | The game's own `config.ini` (`%LOCALAPPDATA%\CAPCOM\DRAGONS DOGMA DARK ARISEN`, as `graphics.py` finds it): `[GRAPHICS] CameraFov` on a slider 0..15 with a field beside it, `ViewRange` as NORMAL / FAR / FARTHEST. ENB's effects from the start (`enbseries.ini [GLOBAL] UseEffect`), with its in-game key and proxy as `enblocal.ini` names them. ReShade's keys, read only (it switches in the game). DXVK through the loader (`[d3d9] chain = riftstone\dxvk\d3d9.dll`), refused while the folder's own `d3d9.dll` is in charge or DXVK is not in `riftstone\dxvk`. Enemies at once: "64 at once" turns `enemy_cap` on with 64 slots and, past 40, `collision_cap` with it; "Back to 30" puts 30 back. Everything refuses while the game runs (it writes `config.ini` when it closes). |
| Live | The loader's live-stats page (`Local\RiftstoneLive`, read as `runtime.parse_live` reads it), read every second while the page is open: address space used and its peak, frames per second and frame times (the last 256 as bars, spikes past three times the average marked), enemies at once and the stage, and the loader's own lines as `runtime.describe_live` writes them. Take a snapshot asks the loader for one (`Local\RiftstoneSnapshot-<pid>`, as `runtime.request_snapshot`). |
| Mod pack | The folder's Riftstone setup as one zip: the loader (`dinput8.dll`, its ini), Riftstone's own features each where it is (`plugins\` on, `plugins\off\` off) with its ini, the licence, `Riftstone - Start Here.cmd` when the folder has it, a profile of the setup (`riftstone/profile.json`), a README and a manifest (`riftstone/package.json`: every member's SHA-256). No game file, no mod, no save, no log; other authors' DLLs, `riftstone\legacy`, DXVK and ENB stay behind, and the README names each. The exe checks every member as `ipaudit.py` does (the test runs `ipaudit.scan_zip` on the zip too), and the zip is written whole or not at all. Refused when the folder has no loader. |

The look is the Forge design (`AGENTS.md`, `studio.py`): obsidian `#08080c`/`#12131a`, rift cyan only for
live/selected/focus, ruby only as a critical marker, two cut corners, the mark from `studio/mark.svg`. Riftstone
Blade is used only for the wordmark and headings; it comes from the exe's resources (RCDATA 101, the same
`riftstone-blade.woff2`), and Georgia stands in when DirectWrite cannot unpack it. The window is Direct2D and
DirectWrite, per-monitor DPI aware, keyboard-driven (Tab, Space/Enter, arrows, PgUp/PgDn, Ctrl+1..7), with a
dark title bar. It polls the game folder every 2 s, and the live stats every second while Live is open.

## Graphics: what each control writes

`CameraFov` is the game's own setting, not a patch. Its reader pushes the key's name (`68 A8 3B 43 01` at
`0x00EDE29F`, the string `CameraFov` at 0x01433BA8), converts the text and stores it in the settings object at
`+0x24` (`D9 56 24` at `0x00EDE2D2`), then sets a value below 0 to 0 and one above 15.0 to 15.0
(`F3 0F 10 05 80 AC 42 01` at `0x00EDE2ED` loads the 15.0 at 0x0142AC80). The writer puts `+0x24` back
(`F3 0F 10 46 24` at `0x00EDDD64`). So 0..15 is the whole range the game keeps, and Studio refuses anything outside
it. How many degrees one step adds is not measured, and the page does not say. `ViewRange` is what
`docs/draw-distance.md` and `docs/lod.md` measure. The game writes `config.ini` when it closes, so a change made while
it runs would be lost; Studio refuses then.

Both are written the way `graphics.set_ini` writes them: the line's key and spacing kept, the value replaced, other
lines byte for byte, the file's code page kept (a UTF-16 `config.ini` is refused). The DXVK switch
is `loader.d3d9_add`'s line and `loader.d3d9_remove`'s empty value. The crowd buttons go through the same toggles
and checked settings as the Features and Settings pages.

## Overhaul profiles (`riftstone-profile/1`)

`src/riftstone/profiles.py` is the reference; `riftstone profile export|import FILE [--name N] [--dry-run]` is its
command line.

```json
{"schema": "riftstone-profile/1", "name": "Hordes", "made_with": "Riftstone 1.0.5",
 "features": {"enemy_cap": true, "lod_tuner": false},
 "settings": {"enemy_cap": {"enemy_cap": {"slots": "64"}}, "loader": {"overlay": {"key": "Insert"}}}}
```

- A profile carries only values that have a check: a rule in `plugins._RULES` or `_SECTION_RULES`, or a 0/1
  switch (`_BOOL_KEYS`). It never carries a path, a DLL name or other free text, so a profile from someone else
  cannot make the loader or a plugin open a file. It holds no game data.
- Parsing checks every field:
  - UTF-8 with an optional mark, at most 1 MiB, 128 features and 4096 values;
  - a name of at most 80 characters with no control characters;
  - feature names in the plugin manager's alphabet;
  - section and key names in printable ASCII without `;=[]` or surrounding spaces;
  - values of at most 256 characters;
  - no key twice in one object, in any case, and no field the schema does not have.
- Writing back gives two-space JSON in the profile's order, non-ASCII as itself, and a line end at the end.
  `parse(to_json(p)) == p`.
- Import plans everything before it changes anything. A move whose target is taken refuses the whole profile and
  changes nothing.
- Import leaves out, and names, each of these:
  - a feature that is not installed;
  - a key the file does not have;
  - a key without a check;
  - a value its check refuses;
  - a UTF-16 file that does not decode.
- A setting of a feature that is switched in the same import is written to the ini's new place.
- Import refuses while the game runs.
- Exe and toolkit differ in one field: the exe writes `made_with` as `Riftstone Studio X`, the toolkit as
  `Riftstone X`.

## Commands

```
RiftstoneStudio.exe                       the window
RiftstoneStudio.exe [--game DIR] COMMAND
  find-game | list | on NAME|all | off NAME|all | settings NAME | set NAME SECTION KEY VALUE
  export-profile FILE [--name NAME] | import-profile FILE [--dry-run] | launch | safe-mode-off
  graphics | set-config CameraFov|ViewRange VALUE | enb on|off | dxvk on|off | crowd N
  export-pack FILE [--name NAME] | live [--pid N] | snapshot [--pid N]
  selftest [--stand-in EXE] [--keep] | --version
```

`live`, `snapshot` and `set-config` need no game folder. `crowd N` takes 10..64.

The exe is a GUI program, so a command writes to the console it was started from, or to a pipe or file when
redirected. Exit codes: 0 done, 1 failed or refused (the reason on one line), 2 a command it does not know (the usage).

Test-only entry points:
- `batch`: requests on standard input, one per line, answers on standard output. `validate STEM SECTION KEY HEX`,
  `rule STEM SECTION KEY`, `read HEXPATH`, `write HEXPATH SECTION KEY HEX`, `parse HEX`, `live HEX` (a live-stats
  page), `setini TEXT WHAT (SECTION KEY VALUE)*` and `iniv TEXT` (the graphics ini edits). Values travel as hex, so
  any byte survives the pipe.
- `--gui-smoke --game DIR PROFILE`: the window built hidden (never shown, never focused), clicked through with
  window messages, a profile exported from its Profiles page and a pack (`PROFILE.zip`) from its Mod pack page. The
  Graphics steps run on a `config.ini` of the smoke's own (`LOCALAPPDATA` pointed at a temporary folder for the
  process), so the player's is never read or written, and `enemy_cap` and `collision_cap` are put back as they were.
  The Live steps read a stand-in page in a mapping of the smoke's own name.
- `--screenshot FILE PAGE [--size WxH] [--dpi N]`: a page drawn into a PNG (WIC), off screen. PAGE is `home`,
  `features`, `settings[/STEM]`, `profiles`, `import/FILE`, `graphics`, `live[/MAPPING]` or `pack[/ZIP]`.

## Build and tests

- `native\studio\build.cmd`: MSVC x86, `/O2 /MT /W4 /WX /EHsc /GS /guard:cf /std:c++17`, linked
  `/DYNAMICBASE /NXCOMPAT /guard:cf`. Output in `native\studio\out\` (git-ignored): `RiftstoneStudio.exe` and the
  stand-in `fake_ddda.exe`.
- `src/catalog.inc` and `src/version.h` are generated by `tools/gen_studio_catalog.py` from `plugins.py` (the
  features, their titles and summaries, every rule) and `riftstone.__version__`. Never hand-edit them; `--check`
  is in the gate.
- `native\studio\test\run_tests.py`, in `tools\test_all.cmd`, compares the exe with the toolkit. It skips when the
  exe is not built.

| Comparison | Cases |
|---|---|
| Every rule, switch and unchecked key, with values from each rule's edges and hostile text, through `batch validate` against `plugins._valid`. The exe may refuse alone only for a value with non-ASCII characters. | 8,253 |
| 26 ini files (comments above a section and a key, CR-only and CR CR line ends, keys twice in any case, a NUL, control characters, code-page bytes, a UTF-8 mark, empty files, UTF-16 with a broken surrogate, an odd last byte and Unicode line separators), each read and given 19 writes by both. The same file name sits in two folders so messages can match. | 521 |
| Profiles: the corpus plus 4,000 seeded mutations (seed 20261013), each parsed by both, refused by both, or written back to the same text. | 4,095 |
| The command line on a stand-in folder built from the shipped inis, against the toolkit on its twin: `list`, `export-profile`, `import-profile` dry and real, the taken-move refusal, `on all`, `set`, `settings`, `find-game`. | 10 |
| A live-stats page: 700 seeded pages (values a loader writes and values it never would, random flags and frame-ring positions) and six edge pages (short, empty, a broken magic, an old version, a wrong size, bytes past the page), each through `batch live` against `runtime.parse_live` and `describe_live`, word for word; then `live --name` on a page in a real mapping, and a mapping that is not there. | 708 |
| `config.ini`, `enbseries.ini`, `ReShade.ini` edits: 1,500 seeded ini texts (sections and keys twice, spacing, comments, broken section lines, keys with no `=`, every line end), each edited by both (`setini` against `graphics.set_ini`) and read by both (`iniv` against `graphics.ini_values`). | 3,000 |
| The Graphics commands on a stand-in folder with `LOCALAPPDATA` redirected: `graphics`, four `set-config` writes against `graphics.set_ini`, seven refusals, a UTF-16 `config.ini` refused, `enb off`, `dxvk` refused beside a folder `d3d9.dll`, then on and off, `crowd 64` (`collision_cap` on with it), `crowd 30`, and the crowd's refusals. | 24 |
| `export-pack`: refused with no loader; then on a folder with a stand-in loader, other authors' DLLs, DXVK, ENB and legacy DLLs, the zip read by `zipfile`, every member's SHA-256 against the manifest, the member set, what was left behind, the profile through `profiles.parse`, the README and `ipaudit.scan_zip`. | 21 |
| `selftest`, 15 checks: a stand-in folder; game search; reading, switching, setting; the hidden window clicked through; a profile exported, read back, and imported dry then real; safe mode ended; Launch with `fake_ddda.exe`. Then the window smoke (36 steps: every page, the field of view slider to its end writes `CameraFov=15.000000`, View range FAR, ENB's switch, 64 at once and back to 30, the live page, a snapshot asked of a process that takes none, the pack's name and its export), the profile and the pack it exported read by the toolkit, and `enemy_cap` as it was found. | 4 |

`--shots` also writes `out\shots\*.png`, one per page, to look at.

`tests/test_profiles.py` covers `profiles.py`. The fuzz target `profile` (`fuzz/targets.py`) checks its invariant:
- a profile is refused with RiftError, or it reads back from its own text, and that text is stable;
- a dry-run import on a stand-in folder changes no byte;
- every planned write passes its key's check;
- the real import does exactly the plan;
- a second import has nothing left to do.

## Shipping it

The exe is not in the player zip yet. A new unsigned exe from a modding site is what antivirus heuristics flag
first; the loader's stand-in already met a cloud false positive on 2026-10-02. Shipping it is the owner's call.

If it ships, the font notices of `src/riftstone/studio/fonts/` (`README.txt`, the three OFL texts) go beside it.
The exe carries Riftstone Blade in its resources, and the OFL requires the licence to travel with the font.

## The spec, measured

The pasted spec named classes and behaviour. Each claim was checked by counting the names in `DDDA.exe`, build
2364871, on 2026-10-13. Where a claim was wrong, the correction stands here:

- Dark Arisen draws with Direct3D 9: `Direct3DCreate9` 1, `d3d11` 0. An in-game menu hooks D3D9's Present and
  Reset, as the loader's panel does (`overlay.cpp`), not DX11.
- The loader's in-game panel key is Insert (since 1.0.3; F10 before), not F1. `[overlay] key` sets it.
- The combat uncapper is `enemy_cap`: 10 enemies in vanilla, 10..64 in its ini (`docs/re-enemy-cap.md`). The
  default is 30, and Studio's slider spans 10..64. The spec's range of 12..64 was off by its low end.
- No such names in the exe: `cRiftManager`, `cSaveDataManager`, `SaveCheckpoint`, `SyncPawn` (0 each). An
  inn-rest hook, or an online pawn sanitizer keyed to those names, has nothing to attach to. The rest was found
  by its own code instead (`docs/vocation-stats.md`).
- `HardPlus` and `pawnlib` are not part of the game (0 each). They are other authors' DLLs, so isolating them is
  the master proxy's job (below).
- Present, and the leads for the XP and level work: `cPlExp` 1, `mpLvUpTbl` 1, `rEquipLvUp` 2, `IsLevelUpPl` 1,
  `uGUICharacterLevelUp` 1, `uGUIJobLevelUp` 1, `cRomPawnManager` 1.
- "0% online ban risk" is not a claim anyone can make. Riftstone does not hide changes from a service's checks and
  will not, under its rules. An online guard that refuses to run a modded pawn online is buildable.

## Where the rest of the spec went

Each is its own module with its own page and tests, and each is SIMULATED, gameplay UNOBSERVED:

- **XP scalar and a flatter level curve:** the `xp_scale` plugin (`docs/xp-scale.md`).
- **Vocation stats on rest:** the `vocation_stats` plugin (`docs/vocation-stats.md`).
- **Shop and loot rebalancer:** the `economy` plugin: shop prices, gold, Rift Crystals and drop chances, each
  multiplied (`docs/economy.md`).
- **Pawn editor:** `riftstone saves pawns`, the party in the save, a copy of the main pawn in the hired slots
  (`docs/pawn-architect.md`).
- **The master proxy:** the loader's legacy DLLs (`riftstone\legacy`, other authors' DLLs loaded before
  Riftstone's plugins) and its patch ledger (which module changed which bytes; conflicts and chains), in
  `docs/runtime.md`.
- The three plugins are in the exe's catalog, so Features and Settings switch and set them.

Not done:

- An in-game D3D9 menu for these beside the loader's panel (the panel shows diagnostics only).
- Pawns and graphics profiles (`riftstone graphics`) from the exe: the toolkit has them, the exe has no page.
- Mods in the exe's pack: a pack is the setup only; mods travel as `riftstone package` packages.
- Dragon's Dogma Online in the exe: it finds and launches Dark Arisen only (`DDDA.exe`).

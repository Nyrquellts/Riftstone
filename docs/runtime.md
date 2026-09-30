# The Riftstone runtime (Dark Arisen and Online)

The runtime is the Riftstone loader, grown up: one `dinput8.dll` next to `DDDA.exe` (or `DDO.exe`)
that serves mods through the overlay, loads plugins, and makes the game **hard to break and easy to
diagnose**. Everything is switchable in `riftstone_loader.ini`; `Riftstone.cmd loader install`
updates the DLL and keeps your settings. Sources: `native/loader/` (`loader.cpp`, `stability.cpp`,
`live.cpp`, `fixes.cpp`, `session.cpp`, `overlay.cpp`, `graphics.cpp`). Python side: `src/riftstone/runtime.py`
and `pe.py`, the `live`, `crash`, `saves` and `laa` commands, `loader d3d9`, `doctor`, and Studio's Game tab.

**Status, honestly:** every part below runs in a harness: 304 checks on a stand-in game, real
Direct3D 9 frames (the in-game panel read back from them, over Windows' Direct3D 9 and over DXVK), and the
engine parts on the real DDDA.exe code mapped read-only. **In the game itself it is UNKNOWN** until the first launch. The loader's hooks and
overlay *did* run in the game on 2026-09-24 (`riftstone\logs\loader.log`: all five hooks installed,
archives redirected, two plugins loaded), so the base is proven; the new parts are not yet. Loader 0.4.1
fixes eleven faults a review found in 0.3.2, each reproduced in the harness first (below, "Loader 0.4.1").

## What it does

| Part | What you get | Default | Game |
|---|---|---|---|
| Crash reports | `riftstone\logs\crash-<time>.txt` + `.dmp`: the fault, which plugin or module it is in, the **engine class** of each object in the registers and on the stack (`uEm5200`, `sSetManager`...), memory headroom with a verdict when the game ran out of address space, the stage, the last files opened, the files it looked for and did not find. A stack overflow gets one too (written from a helper thread) | on | both |
| Fatal-error reports | the game's own "Failed open file" box becomes `fatal-<time>.txt` (the missing file, what it means) and the box gains one line naming it | on | both |
| Hang reports | no frame for 20 s while the game is in front, or while Windows calls it not responding (since 1.0.2: a frozen fullscreen game is usually behind the desktop by the time its player got out, and one ended at shutdown on 2026-09-27 left no report): `hang-<time>.txt` with where the main thread waits, and with `minidump = 1` every thread's state in `hang-<time>.dmp`, written on a thread of its own that the watch gives 30 s. The game is not touched | on | both |
| **Snapshots** (1.0.2) | `riftstone snapshot` sets `Local\RiftstoneSnapshot-<pid>` (the live thread creates it and checks it every quarter second): the same report as a hang, `snapshot-<time>.txt`, and every thread in `snapshot-<time>.dmp` while the game goes on (its main thread is paused only while its registers and stack are read, and every thread while the dump is written); `riftstone threads` reads any of the loader's dumps and groups the threads by what they are in. Snapshots are not problems: `doctor`, `playtest` and Studio leave them out | with live stats | both |
| **Why the game closed** | a normal exit leaves no report, so the loader watches it happen: Alt+F4 (and whether the key came from the keyboard or a program), the close button or window menu, a close message from another program, Windows ending the session, the game's own exit menu, its fatal error. `loader.log` gets an `exit` line and the exit summary ends `ended by: ...`; `runtime-state.ini` keeps it; `Riftstone.cmd crash`, `doctor` and Studio's Game tab say it, and whether it was a crash | on | both (the exit menu: DDDA 2364871) |
| Report rotation | the newest 10 of each kind; `loader.prev.log` keeps the last session's log | on | both |
| **Missing-texture guard** | a texture the game needs and cannot find gets a neutral 4x4 grey stand-in instead of stopping the game; `loader.log` names it | on | both |
| **Archive guard** (1.0.1) | a resource the game asks for as a loose file before its archive was read (a skipped cutscene, a slow drive) gets its own bytes from that archive instead of stopping the game; `loader.log` names it and the archive | on | DDDA |
| **Ragdoll guard** (1.0.2) | `[guard] ragdoll_bodies`: a ragdoll whose bodies are not set up yet (an enemy just spawned in a big horde) counts as having none, the game's own answer, in four walks that read the count through a null pointer instead (the crash of 2026-09-27: 50 goblins at Gran Soren) | on | DDDA 2364871 |
| **Safe mode** | two crashes in a row during start-up that no plugin explains: the next runs start without plugins and without the overlay (vanilla), with one notice box, until a mod or plugin changes or `Riftstone.cmd loader safe-mode off` | on | both |
| **Plugin quarantine** | a plugin whose code was at fault in two start-up crashes in a row is skipped until its file changes (`Riftstone.cmd loader plugin release <name>`) | on | both |
| Live stats | shared memory `Local\RiftstoneLive`: address space used and the largest free block, peak commit and a memory verdict (headroom/tight/bound), frame times (Present), stutters, overlay/missing/stand-in counters, plugins, the stage, enemy slots in use, the resource table's fill, what Direct3D holds by pool and which Direct3D 9 it is, the pressure watch | on | both (stage, enemies, resources: DDDA) |
| **Direct3D 9 from DXVK** | `[d3d9] chain`: the game's Direct3D 9 from DXVK's 32-bit `d3d9.dll` kept in `riftstone\dxvk` (nothing in the game folder). DXVK keeps Direct3D's copy of the game's textures out of its address space: in the harness 682 MB of managed textures cost +711 MB of address space under Windows' Direct3D 9 and +125 MB under DXVK 3.1.1 (below) | off | both |
| Direct3D pools | the textures and buffers the game holds, in bytes by pool: how much of its address space Windows' Direct3D 9 spends on its copy of the managed ones | on | both |
| **Memory pressure watch** | past 3,400 MB of commit, or with the address space nearly used up: one `loader.log` line saying what holds the memory and what would help. Nothing is flushed: the engine keeps no resource no one uses (below) | on | both |
| Large-address check | `loader.log` says whether the exe is large-address aware (4 GB, not 2 GB); `Riftstone.cmd laa` checks any exe and writes a copy with the flag set | always | both |
| **In-game panel** | Insert shows a small panel over the game (and for 12 s at start a notice says the loader is running): enemy slots in use and the session's peak, address space with the warning level, each plugin's state, frame rate, stage (below) | on, hidden until Insert | both (enemies, stage: DDDA 2364871) |
| Save backups | the save folder copied to `%LOCALAPPDATA%\Riftstone\saves\DDDA\<account>\<time>` before the game reads it, only when it changed; newest 20 kept (only folders named `YYYYMMDD-HHMMSS[-n]` count as backups: anything else there is never counted or removed); `Riftstone.cmd saves list / backup / restore` (the list also holds the `save_backup` plugin's copies, `docs/saves.md`) | on | DDDA |
| Borderless window | windowed mode without a frame, covering the monitor | off | both |
| Keep running when alt-tabbed | windowed/borderless only; the mouse is released while the game is behind | off | both |
| Frame-rate ceiling | the options menu's "Variable" frame rate means `[fps] max_fps` (30..360) instead of 150 | off | DDDA 2364871 |
| Shadow map size | `ShadowQuality=HIGH` draws sun shadows at `[render] shadow_map_size` (e.g. 4096) instead of 2048; spot and point shadows that make their own maps (the player's among them) get half; lights that `sShadow` lends maps to keep its size | off | DDDA 2364871 |
| Pass-through | any other program in the folder that loads dinput8 (DDO's launcher does) gets DirectInput and nothing else | always | both |

The loader pins itself in memory (the import table points into it), and a plugin that fails to load
now logs why (a missing Visual C++ runtime is named as such).

## Using it

```bat
Riftstone.cmd loader install           :: puts the loader in place (keeps your settings on an update)
Riftstone.cmd live [--watch]           :: what the running game is doing right now
Riftstone.cmd crash [--list] [name]    :: how the last session ended, then the newest (or a named) report, explained
Riftstone.cmd saves [list|backup]      :: every copy of the save; restore <number> --yes puts one back (only one that checks; copies the current aside first)
Riftstone.cmd loader safe-mode off     :: end safe mode
Riftstone.cmd loader plugin release <name>
Riftstone.cmd loader d3d9 add <DXVK release>   :: DXVK's x32 d3d9.dll into riftstone\dxvk, named in [d3d9] chain
Riftstone.cmd loader d3d9 [status|off]         :: what [d3d9] chain names; off: Windows' own Direct3D 9 again
Riftstone.cmd laa [exe] [--copy out.exe]       :: large-address aware? --copy writes a copy with the flag set
Riftstone.cmd doctor                   :: also: loader version, safe mode, quarantine, newest report, backups, live
                                       :: stats, the exe's large-address flag, [d3d9] chain
```

Studio's **Game** tab shows the same: a Live card (memory, frame-time graph, enemies, stage), a Last
session tile (what ended it), the reports with their explanation, and a button to end safe mode.

`Riftstone.cmd crash` starts with the last session, for example: *The last session (started 2026-09-25
21:02, ran 43 min 1 s) ended: Alt+F4 was pressed (the keyboard shortcut that closes a window). It was not
a crash.* and *what the loader saw: WM_SYSKEYDOWN F4 with Alt (from the keyboard), then SC_CLOSE
(posted), then WM_CLOSE*. A report older than that session is marked as such.

`Riftstone.cmd crash` reads the report and says it plainly, for example: *the fault is inside the
plugin enemy_cap.asi*; *it had nearly run out of address space (3950 MB of 4096 MB used, largest free
block 42 MB)*; *the game looked for model\em\e52\e5200\s01\e5200_skin_BM.tex; installed mods that
mention it: DDO Chimeras*; *engine objects involved: sSetManager, uEm5200*.

### The ini files, and how Windows reads them

| File | Who reads / writes it | How |
|---|---|---|
| `riftstone_loader.ini` (next to the game) | the loader reads it; `loader install` merges new keys in | `GetPrivateProfileIntW` / `GetPrivateProfileStringW`; the loader itself drops a trailing `; comment` and trailing blanks from a text value (the API keeps them) |
| `riftstone\runtime-state.ini` | the loader reads and writes it; `Riftstone.cmd crash`, `doctor`, Studio read it, `safe-mode off` and `plugin release` write it | the loader: `GetPrivateProfileStringW` / `WritePrivateProfileStringW` (every value it writes is ASCII) |
| `riftstone\plugins\enemy_cap.ini`, `inclination_lock.ini`, `lod_tuner.ini`, `save_backup.ini` | each plugin reads its own | `GetPrivateProfileIntW` / `GetPrivateProfileStringW` |
| the game's `config.ini` | `lod_tuner` reads `Resolution`, `CameraFov`, `ViewRange` | `GetPrivateProfileStringW` |
| `riftstone\logs\last-crash.txt` | not an ini: `key=value` lines the loader writes after a crash and reads at the next start | UTF-8, `CreateFile` / `WriteFile`, parsed line by line by the loader |

No native part reads or writes an ini any other way. What Windows' profile API does with the bytes,
**measured** on this PC (2026-09-26, Windows 11 build 26200, ANSI code page 1252, a ctypes probe of
`GetPrivateProfileStringW` / `WritePrivateProfileStringW`; another lane measured the same):

- A file without a UTF-16 byte-order mark is read in the ANSI code page: `E9` reads as `é`, and UTF-8's
  `C3 A9` as `Ã©`. A UTF-8 file with a byte-order mark is read the same way, and the mark also hides its
  first section (none of that section's keys are found).
- A UTF-16LE file with its byte-order mark is read, and written, as Unicode.
- `WritePrivateProfileStringW` creates a new file in ANSI; a character the code page does not have is
  written as `?` and reads back as `?`. Into an existing file without a mark it writes ANSI bytes, whatever
  the rest of the file is.
- A value in double quotes (`Folder = "D:\My Saves"`) is read without them. A `; comment` after a value is
  part of the value (`c = 5 ; note` reads as `5 ; note`; `GetPrivateProfileIntW` still reads 5).
- A key with `=` in it is written as it is but read back only under the part before the first `=`
  (`crash=plugin.asi` → the key `crash` with the value `plugin.asi=1`), and a second write adds a second
  line. That, and the `?` for characters outside the code page, is why runtime-state.ini keeps a plugin whose
  file name is no plain ini key under `~` and the hex of its name (below, Safe mode); the Python side shows
  that key as it is.

So the inis belong in the ANSI code page (or UTF-16 with its mark), never UTF-8. Riftstone's Python side reads
and writes them in that code page to match (`runtime.ini_text` / `ini_bytes` / `ini_value`, used by
`plugins.py`, `loader.py`, `saves.py`; bug-sweep 4c126bd). **UNKNOWN**: other code pages, and Windows' "Use
Unicode UTF-8 for worldwide language support" (ANSI code page 65001); not measured.

## In-game panel (Insert) and the startup notice

**The startup notice** (1.0.3). For 12 seconds after the first frame the loader draws a small notice in the panel's
corner: `RUNNING  press Insert for the panel` and how many plugins loaded (`safe mode  plugins and mods are off` with a
ruby mark when safe mode is on). It is the answer to "is Riftstone in my game?" for a player: no notice, no loader
(`Riftstone - Start Here.cmd`, choice 1, says why). `[overlay] banner = 0` turns it off, `banner_seconds` (3 to 60)
sets how long; the panel, once opened, replaces it for good. It is drawn by the panel's own code, so it holds a
Direct3D texture and a state block for those seconds and none after.

Insert (while the game is in front) shows the panel in a corner of the game, and Insert again hides it.
Top to bottom:

| Row | What it says |
|---|---|
| `NryQ // Riftstone v1.0.3` ... `Insert` | the loader's version, and the key that hides the panel |
| ACTIVE ENEMY POOL `3 / 10 slots` | enemies in `sSetManager`'s slots now, of the slots there are (10, or `enemy_cap`'s count). The meter fills with the share in use; a cyan tick and `peak 7` mark the most at once this session. DDDA 2364871 only |
| MEMORY GUARD `2.71 / 4.00 GB` | the game's address space used, of all it has (a 32-bit game crashes when it runs out). A white notch marks the loader's warning level, 400 MB before the end; `32.2% headroom` is what is left |
| chips | each plugin in load order: `ACTIVE` (loaded), `FAILED` (did not load), `SKIPPED` (quarantined, or safe mode); `safe mode: ON` first when it is on; `+N more` when two rows are full |
| `60.0 FPS`, `STAGE 100` | the frame rate over the last 64 frames timed at Present, and the stage the player is in (the game's own reader; DDDA 2364871) |

A reading that is not there (another build, a stage still loading) says **UNKNOWN** over an empty track:
nothing unavailable looks healthy. When the address space is nearly used up (the same rule as the
`memory   LOW` line: less than 400 MB left, or no free block of 96 MB), the memory row turns critical: a
ruby droplet, *Address space nearly used up* in white, the meter in ruby and a short ruby mark on the
panel's edge. Readings refresh four times a second; nothing animates.

`[overlay]` in `riftstone_loader.ini`:

| Key | Default | What it does |
|---|---|---|
| `enabled` | 1 | 0: the panel never draws and the key does nothing |
| `key` | Insert | Insert, Delete, Home, End, PageUp, PageDown, Pause, ScrollLock, PrintScreen or F1 to F12. Up to 1.0.2 the default was F10: the game's window treats F10 as a system key, and a player reported that F10 only hid the HUD (Nexus, 2026-09-27). DDDA's own key bindings (`config.ini` `[KEYBOARDMOUSE]`, DirectInput scan codes) use the letters, the digits, Space, Ctrl, Shift, Alt, Caps Lock and F1 to F3, so Insert is free. An update of the loader (`loader install`) moves an old `key = F10` to Insert |
| `banner` | 1 | the startup notice above; 0: none |
| `banner_seconds` | 12 | how long it stays (3 to 60) |
| `show_at_start` | 0 | 1: shown from the first frame (not in safe mode, where only the key shows it) |
| `position` | top-right | top-right, top-left, bottom-right or bottom-left, 16 px from the corner |
| `scale` | auto | auto: the back buffer's height / 1080 (0.75 to 3), or a number such as 1.5 |

**Hidden, it draws nothing and costs one check a frame** (is the game in front, is the key down) and holds
no Direct3D object. Shown, it is one texture and one draw call at Present (`overlay.cpp`): a glyph atlas
made once with GDI (Consolas, Segoe UI Semibold, grayscale antialiasing) plus a few shapes, pre-transformed
vertices, the fixed-function pipeline, its own BeginScene/EndScene. The game's drawing state is kept in a
state block (captured before, applied after); render target 0 goes to the back buffer and the depth-stencil
surface to none while it draws, and both go back. The state block is released before every `Reset` (the
texture is managed and survives one), everything before the game creates another device, and a lost device
draws nothing. No new hook and no D3DX: it rides the Present hook frame timing already has and uses only
plain Direct3D 9 calls that DXVK implements too (state blocks, a managed texture, DrawPrimitiveUP). It needs
`[live] frame_stats = 1` (0 leaves Direct3D alone, and so the panel). An exception while it draws puts the game's state back, turns the panel off for
the session and writes one line to `loader.log` (not a crash report). DDDA does not create a pure device (its
`CreateDevice` text at `0x01429A28`: `D3DCREATE_FPU_PRESERVE | D3DCREATE_HARDWARE_VERTEXPROCESSING |
D3DCREATE_MULTITHREADED`, with a software-vertex-processing fallback), so its state can be saved; on a pure
device the panel says so in the log and stays off.

`loader.log`: `overlay  Insert shows the diagnostics panel (top-right, scale auto)` once the device exists (and
`overlay  a startup banner shows for 12 s ...`), `overlay  startup banner shown on the 2560x1440 back buffer ...` the
first time the notice draws, `overlay  Insert pressed with the game in front: the panel is now shown` for each key
press that reached the loader (the first twelve), `overlay  panel shown on the 1920x1080 back buffer at scale 1.00
(top-right)` the first time the panel draws (and after a resolution change), and `overlay  the diagnostics panel
stopped: ...` if it ever has to stop. Those lines tell a report apart: no `startup banner shown` means nothing is
drawn in that game; a `pressed` line with no `panel shown` after it means the key arrived and the draw did not
(`riftstone playtest` says both). **UNKNOWN until seen in the game:** all of it in DDDA itself, including whether the
key reaches `GetAsyncKeyState` while the game holds the keyboard through DirectInput, and how it sits over DXVK and
the Steam overlay. (The one report so far, a player on 2026-09-27 who pressed F10: the HUD went and no panel came;
the cause is not known, and the log lines above are how the next report will show it.)

## How each part works (and the engine facts it rests on)

All engine facts are for Steam build 2364871 (PE time stamp `0x5A314C31`); the parts that need them
check the bytes first and do nothing on any other build. Class names, reports, the guard, live stats,
safe mode and the window fixes need no engine address at all.

- **Class names.** Every MT Framework object's vtable slot 4 is `getDTI`, compiled as
  `B8 <MtDTI> C3` (`mov eax, imm32; ret`) in all 4,382 DDDA classes that have a vtable, and the
  MtDTI constructor (`0x00CF5780`) stores the class name at `+4`. The crash handler follows that chain
  with reads only (each one checked with `VirtualQuery` and guarded), so it never runs game code. DDO
  shares the layout. The DTIs are built before `WinMain`, so a crash inside the loader's own start-up
  names nothing.
- **The fatal path** (measured): `rMaterial::load` asks `sResource` for a texture; when it is not
  loaded, the synchronous loader calls `MtFileEx::open` -> `MtFile::open` (`0x00D0D0C0`), which opens
  the loose file with **CreateFileA through the import table** (`[0x0139D220]`, re-read on every call),
  tries once, and on any failure (except `.pck` names, which are optional) formats
  `"Failed open file. %s %d"` (`0x01408AD8`), shows `MessageBoxA(..., "Fatal error.")` and calls
  `exit(1)`. So a missing loose file under `nativePC` is always fatal, and the guard's stand-in only
  ever replaces a certain crash. `rTexture::load` (`0x00E6B1C0`) rejects anything but `TEX\0` and revision
  `0x99`, then succeeds when the texture can be created from its header; the stand-in is a valid 4x4 BC1
  texture with 3 mips (DDO's gets revision `0x9D`).
  Intercepting the box alone could not help: `exit(1)` follows it regardless.
- **The ragdoll guard** (1.0.2, `fixes.cpp`, `[guard] ragdoll_bodies = 1`). A ragdoll's bodies live in a container
  (`uRagdollExt` and its kin) whose body data at `+0x38` holds the count (`+0x68 >> 8`) and whose `+0x4C` lists the
  bodies. The game's own count is 0 while that data is not there: `8B 41 38 85 C0 74 07 8B 40 68 C1 E8 08 C3` at
  `0x010805D0`. Four walks read the count inline after checking only the container:
  `8B 48 38 33 F6 F7 41 68 00 FF FF FF` at `0x0079493D` (and at `0x007949ED`, `0x008CF2D8`, `0x00C2BAE0`). The owner's
  session of 2026-09-27 ended there: an access violation at `0x00794942` reading `0x68`, a goblin's `uRagdollExt` in
  `eax` and its data pointer 0, with about 50 enemies at Gran Soren. The first two walks (every body of an enemy's
  ragdoll and its collision set gets a value at `+8` or `+0xC`; `0x00794930`, `0x007949E0`) are replaced by the same
  walk counting the game's way; the two inline reads (a character's ragdoll, `+0x1EFC` and `+0x1EF8`) jump to a
  check that skips the walk when the data or the body list is missing. Every site, the two accessors and the
  inline walks' continuations are byte-verified first; one differing byte patches nothing. The engine harness runs
  the game's own code: unguarded, a ragdoll without its data faults at `0x00794942`; guarded, complete ragdolls get
  the same values and leave the same `eax`, and the one without data walks nothing. Whether a goblin whose
  ragdoll was not set up in time falls right in game: UNKNOWN.
- **The archive guard** (1.0.1, `resources.cpp`, `[guard] from_archives = 1`). The same path stops the game
  for any resource, not only a texture, whenever the game asks for it before the archive that holds it has
  been read. Players report it at the ending's cutscenes: `"Failed open file.
  ...\nativePC\id\credit_02\credit2_01_99.gmd 3"` (Steam community, "The Great Hereafter Cutscene Crash"; what
  helped them was a faster drive). That resource (106 bytes) is in `rom\stage\stage800\stage802.arc` and never
  loose (`riftstone find credit2_01_99`); a skipped cutscene asks for the next scene's resources early in the
  same way. The guard answers such an open with the resource's own bytes. It looks first in the archives the
  game opened most recently (the one it is reading), then in every archive under `nativePC`. Their directories
  are read once, on the first miss, into a table of name hashes that `loader.log` times. Each candidate is
  checked by name and type, and a mod's copy of an archive in `riftstone\overlay` is read instead of the
  game's. The bytes go to `riftstone\standin\nativePC\<path>` and are opened read-only. The rules:
  - the type comes from the path's extension (`restypes.inc`, generated from the type map);
  - `.arc` is never answered: a missing archive is not a resource inside another;
  - a payload whose zlib stream or Adler-32 does not check is not served;
  - a resource no archive holds still fails, except a texture, which gets the grey stand-in.

  Dark Arisen only (Online's archives are encrypted). The loader harness (`archives`) decodes stored, fixed and
  dynamic streams, checks the choice between two copies and the overlay's, the switch, Online and 16 threads
  at once, and reads 37 resources of 36 types from the real `stage802.arc` (the credits text among them) byte
  for byte. **In game: UNKNOWN** until a session shows `was not read in yet` in `loader.log`.
- **Frame-rate ceiling.** The mode switch loads 150.0 / 60.0 / 30.0 (`movss xmm0, [0x01433AA8]` at
  `0x00EDD4AD`, the options' apply; the same at `0x00EDDC86`, the options' save to `MaxFPS`) and calls
  `sMain::setTargetFPS` (`0x00DBE120`, target at `sMain+0x3C`). At start-up the game reads `MaxFPS` from
  config.ini and sets the target from it (`0x0041CE6F`). The 150.0 constant is shared by two unrelated
  readers (`0x00F5BD25`, `0x00FA1971`), so the runtime repoints the two instructions at its own value
  instead of rewriting the constant (which is what DDDAFix does).
- **Shadow map size.** `sRender::getShadowMapSize` (`0x00DA97D0`) returns
  `table[ShadowQuality]` from `0x014292BC` = {512, 1024, 2048}, halved for spot and point shadows; it is
  the table's only reader. The runtime writes the HIGH entry. Shadow maps are made by `createShadowMap`,
  which reads the table again, so scheduler data (`mMapSize`) cannot undo it; spot and point shadows
  that `sShadow` lends maps to use its group size instead (`docs/re-shadows.md`).
- **Stage.** The game's own reader (`0x005BAF40`): `[0x018D099C]` (sArea) `+0x3834`, flag byte `+0x20`,
  stage number at `+0x724`. Read with guards, advisory (an area change can free the object).
- **Resource table.** `sResource` (`[0x018D0AA0]`) registers resources in 2,048 buckets of 8 slots at
  `+0x40D8` (`registTable`, `0x00DB9B70`). A full bucket moves the search to the next shift of the
  resource's 64-bit key; only when all 17 buckets tried (shifts 0 to 16) are full is the resource left
  unregistered, and a later request loads it again. The live view counts the used slots and warns at 90 %.
- **Enemy slots.** `sSetManager` (`[0x018FA504]`): the vanilla ten at `+0x844`, or the `enemy_cap`
  plugin's slots at the manager's tail (it exports `EnemyCap_Slots`), `mUnitNumEnemy` at `+0x1B8D0`.
- **Frame timing.** The game's import of `d3d9!Direct3DCreate9`, then `CreateDevice` (slot 16) of the
  Direct3D object and `Present`/`Reset` (17/16) of the device, patched in their function tables. Works
  the same under DXVK, ReShade or an overlay. `GetAvailableTextureMem` is asked on the render thread
  only (it is Direct3D 9's own rough estimate).
- **Direct3D 9 chain and pools, the pressure watch, the large-address check.** Their own sections below.
- **Crash filter.** Installed first and kept first (re-asserted every 5 s); the game's filter and any
  other module's are called after the report (up to four other modules' filters, newest first). A module
  that sets its own filter over ours got ours as the filter before it and may call it in turn: a guard in
  the filter answers a call on a thread that is already inside it at once, so one crash is one report and
  each filter runs once (before 0.4.1 the two called each other until the stack ran out, with three reports).
  Debugger and anti-tamper probes (breakpoint, single-step, debug-print, thread naming, a read of
  `0xFFFFFFFF`) pass straight through without a report, and a filter further down that recovers keeps the
  game running (up to three reports a session), so a probe cannot use up the report a real crash needs.
  `[loader] crash_reports = 0` leaves `SetUnhandledExceptionFilter` unhooked, so the game's own filter
  reaches Windows as it would without the loader.
- **A crash with little stack left.** Writing a report takes tens of KB of stack; after a stack overflow the
  crashing thread has a few KB. So for a stack overflow, or any crash with less than 64 KB of the thread's
  stack left (Windows 8 and later can tell; on 7 only the overflow counts), the crash note is written first
  with static buffers (a few hundred bytes of stack), then the report and minidump on a helper thread with a
  stack of its own while the crashing thread waits (up to 30 s). When the crashing thread holds the loader
  lock (a crash inside a `DllMain`, e.g. a plugin's while the loader loads it), no new thread can start, so
  only the note is written. Before 0.4.1 the report and the note were left empty and the process ended with a
  second fault (`0xC0000005`), so a stack overflow never counted towards safe mode or quarantine.
- **Safe mode.** `riftstone\runtime-state.ini` remembers how each session ended; the crash handler
  leaves `riftstone\logs\last-crash.txt` (uptime, faulting module). "Start-up" means the first two
  minutes. A clean exit, or a run past two minutes, resets the count. The setup fingerprint covers
  `riftstone\plugins`, `riftstone\overlay`, the ini and the DLL `[d3d9] chain` names. Safe mode turns off the overlay; mods installed
  directly into `nativePC` (without the loader) are not covered. A plugin's strikes and quarantine are kept
  under its file name, or, when the name is no plain ini key (it has `=`, starts with `;` `#` `[` `~` or a
  blank, ends in a blank, or leaves printable ASCII), under `~` and the hex of its lower-case UTF-8: the ini
  reader splits a key at its first `=`, so before 0.4.1 such a plugin's strikes never added up. An older
  loader's entry under the bare name is moved to the new key when it is read, and its split lines removed.
  `Riftstone.cmd loader plugin release` takes the plugin's name or its `~` key (`loader.log` names it).
- **Why the game closed.** Below, with the exit paths it rests on (`session.cpp`, `exit_sites.h`).

### Why the game closed

A crash leaves a report; a normal exit leaves only its teardown (Steam's `gameoverlay_renderer.txt`:
DirectInput devices released, then the Direct3D device, then the overlay detaching). That teardown is
the game's message loop ending, and nothing afterwards says what ended it. So the loader watches it
happen, and the first thing that closes the game is the reason.

**How DDDA 2364871 closes** (static, from the exe; every address below is checked):

- `WinMain` (`0x004079E0`) builds **sApp** on its own stack (constructor `0x00DF3940`, table `0x0142C0D8`,
  instance pointer `[0x018D0F50]`) and runs its loop (`0x00DF0D20`): `PeekMessageA` (`PM_REMOVE`),
  `TranslateMessage`, `DispatchMessageW`, one frame. The loop ends on **WM_QUIT**, or when **sApp+0x266C**
  is set (`or bl, [esi+0x266C]`, `0x00DF0E08`); it sets that byte itself as it ends. Then sApp's
  destructor (`0x00DF1140`: the input manager and its DirectInput devices, every system including the
  Direct3D device, `CoUninitialize`), `SteamAPI_Shutdown`, and `WinMain` returns: exit.
- Only the game's own exit sets sApp+0x266C: **Exit Game on the title screen** (uGUITitle's state
  `0x0072C440`, write `0x0072C4B2`) and the **quit prompt of the start-up save check** (aBbsRpg's flow
  `0x00530AF0`, state 8, write `0x00530D1E`; the flow runs while aBbsRpg boots, `0x004FA6E4`).
- The window procedure (`0x00DF1550`, class registered with `RegisterClassExW`; windowed style
  `0x00CA0000`): Alt+F4 (`WM_SYSKEYDOWN` `VK_F4` with Alt) and `WM_SYSCOMMAND` go to `DefWindowProcW`, which
  makes the `SC_CLOSE` and then the `WM_CLOSE`; Alt+Enter, Alt+Space and F10 are swallowed, and so are
  `SC_SCREENSAVE` / `SC_MONITORPOWER`. The game's own low-level keyboard hook (`0x00DF3C40`) swallows only
  Alt+Space while it is in front. **WM_CLOSE** on the main window (sApp+0x12C) asks sMain
  (`[0x018D0B28]`, table slot `0x34` = `0x00DBD0D0`: yes, unless the "OA" remote tool is connected and says
  no), then runs sMain's **exit request** (`0x00DBD0F0`): sMain+0x34 = 1, then
  `SendMessageA(window, WM_DESTROY)`. **WM_DESTROY** of any window of its class saves the window position
  (config `Window` `MainX`/`MainY`...) and posts WM_QUIT (`0x00DF182B`); it does not check which window.
  WM_QUERYENDSESSION / WM_ENDSESSION go to `DefWindowProcW` untouched, so Windows ends the process.
- The exit request's only other callers are debug paths: the "OA" remote command (`0x00FA071A`) and sMain's
  `Exit` debug property (`0x00DBE460`). sMain+0x34 is cleared by sMain's constructor and set by nothing
  else of sMain's. `PostMessageA` is not imported; `SendMessageA`'s only caller is the exit request, so the
  game never sends or posts itself a close.
- The fatal-error box is not this path: `0x00D0CB00` formats the text, calls `0x00DF0C50`
  (`MessageBoxA "Fatal error."`, shown only while sMain exists), then `exit(1)`, without the game's own
  teardown: only the CRT's exit handlers run before ExitProcess (the fatal report covers it).

**What the loader does.** Once the game's window exists (a top-level window of the process whose class
window procedure is in the game's executable), it puts two thread hooks on that window's thread,
`WH_CALLWNDPROC` (sent messages) and `WH_GETMESSAGE` (what the loop takes from its queue). They only look.
It reads the close in progress from them:

| Recorded as | What the loader saw |
|---|---|
| `alt-f4` | `WM_SYSKEYDOWN` F4 with Alt, then `SC_CLOSE` within 2 s, then `WM_CLOSE`. The key's source comes from Windows (`GetCurrentInputMessageSource`): the keyboard, input a program injected (a macro tool, a controller mapper, remote control), or a message posted to the window |
| `close-button` | `SC_CLOSE` sent on the game's thread with the click's point (the title bar's close button, or Close in its menu), then `WM_CLOSE` |
| `window-menu` | `SC_CLOSE` on the game's thread with no point (the window menu, by keyboard), then `WM_CLOSE` |
| `close-message` | `SC_CLOSE` posted or sent from another thread with no Alt+F4 before it (the taskbar's Close window, Task Manager, a tool), a `WM_CLOSE` posted or sent from another thread, or a `WM_DESTROY` from outside |
| `session-end` | `WM_ENDSESSION`: shutting down, restarting, signing out, or an installer or Windows Update closing programs (`ENDSESSION_CLOSEAPP`). Written at once: Windows may end the process next |
| `exit-menu` | (DDDA 2364871) sApp+0x266C set with no close message and no WM_QUIT before it: Exit Game, or the start-up save check's quit prompt |
| `exit-request` | (DDDA 2364871) `WM_DESTROY` with sMain+0x34 set and no `WM_CLOSE` before it: the debug Exit command |
| `window-destroyed` | `WM_DESTROY` of a game window on its own thread with no close before it (`DestroyWindow` by a plugin or the game) |
| `quit-message` | `WM_QUIT` with no close or destroy before it: posted to a window (the game's own carries none) or `PostQuitMessage` / `PostThreadMessage` by a plugin or another program |
| `fatal-error` | the game's fatal-error box, then `exit(1)` |
| `self-exit` | a normal exit with no message and the quit flag not seen (the exit menu when its flag was missed, or code calling `exit()`) |
| `unknown` | a normal exit with the window never watched |

**Who asked (loader 0.3.3).** Windows does not say which program sent or posted a message. When a close
comes from outside (`close-message`, `quit-message`), the loader notes what Windows does say at that
moment, and `describe_end` (`Riftstone.cmd crash`, `doctor`, Studio) says what most likely sent it:

- the window in front, with its program and class (`explorer.exe [Shell_TrayWnd]`), or `the game`;
- whether the game's own window was in front or minimized;
- the time since the last keyboard or mouse input;
- the window under the pointer.

Windows Explorer in front reads as the taskbar's Close window, Task Manager as its End task, Steam as its
Stop. The game itself in front, with input a moment before, reads as something running in the background,
a plugin, or an overlay drawn in the game. The harness checks that the program named is the one Windows
reports as in front when its messages are sent.

A `WM_CLOSE` counts once the game acts on it (its exit request sends `WM_DESTROY` while the close is
still being handled); a close the game refused is dropped. The quit flag is read every 50 ms by the
watchdog thread, only after the byte check of `exit_sites.h` (the loop, sApp's and sMain's constructors,
both writers of the flag, the exit request, the `WM_CLOSE` handler) passes once the game's window exists,
and only while sApp still carries its table (so not after its destructor). The set flag stays readable
through the loop's last frame and sApp's destructor, which destroys every system (the Direct3D device
among them); how long that takes in the game is not measured. If the flag is ever missed, the exit reads
`self-exit`, which names the exit menu first. Only a change from clear to set counts: in the game
(2026-09-25, loader 0.3.0) the flag already read set 1.7 s after start, while the game was still starting
(before its loop ran), and the session was recorded as `exit-menu` although the game ran for another minute
and was closed with its window's close button. Since 0.3.1 the flag has to read clear once before a set
flag means the game's own exit (the harness's `close startset-button` repeats that start-up).

**Where it goes.** `loader.log`: `exit     watching the game window 0x... (thread ...) ...; the game's own
exit (its quit flag) is verified for this build`, then `exit     Alt+F4, after 43 min 1 s: WM_SYSKEYDOWN
F4 with Alt (from the keyboard), then SC_CLOSE (posted), then WM_CLOSE`, the steps that follow (`exit
WM_DESTROY of the game window ...`, `exit     WM_QUIT reached the game's message loop ...`; Windows asking
to end the session gets its own line), and the `summary` line ends `ended by: Alt+F4`. `runtime-state.ini` `[session]`: `end`, `end_detail`, `end_uptime_ms` (written when it
happens), `clean=1` after the exit itself. At the next start that and the crash note become
`[last_session]` (`end`: one of the codes above, or `crash`, or `not-clean` when the game neither exited
nor left a report: ended from outside, power lost, or died without a report; `closing` when a crash came
while closing; `detail`, `uptime_ms`, `clean`, `report`), and `loader.log` says it in one line
(`last run closed after ... ended by: ...`, `last run was ended by Windows`, `last run did not exit
normally and left no report ...`). `runtime.session_end()` reads both for `crash`, `doctor`,
`loader status` and Studio. `[loader] exit_reason = 0` turns the watch off.

**Measured on Windows 11** (a scratch probe with a hidden window, then the harness): `DefWindowProcW`
turns only a real Alt+F4 key press into `SC_CLOSE`; a posted or sent `WM_SYSKEYDOWN` F4 with Alt does
nothing, even with the thread's key state set, so the stand-in game takes that one step itself (it posts
`SC_CLOSE`, as `DefWindowProc` does for the real key) and nothing is typed. `WH_GETMESSAGE` sees
`WM_QUIT`, and `PostQuitMessage`'s has no window while a posted one carries it. `InSendMessageEx` is 1
for a message sent from another process and 0 on the window's own thread (and stays 1 inside a close sent
from outside); the `WH_CALLWNDPROC` hook's own wParam reads the reverse of its documentation, so it is
not used. `GetCurrentInputMessageSource` reports "unavailable" for a posted key message. **UNKNOWN until
seen in the game:** all of it in DDDA itself; the source Windows reports for a real or injected Alt+F4
(the harness cannot press keys); the `lParam` of the `SC_CLOSE` a real Alt+F4 makes (the reading does not
depend on it).

### The live-stats page

`Local\RiftstoneLive` (or `Local\RiftstoneLive-<pid>` when a second game runs), one 4 KB page, version 1,
written under a sequence lock (odd while writing). `runtime.py` reads it with `OpenFileMapping`
(read-only) and copies it between two equal even sequence numbers.

| Offset | Field | Offset | Field |
|---|---|---|---|
| 0x000 | magic `RSLIVE1\0` | 0x068 | handles |
| 0x008 | version (1), 0x00C size (0x1000) | 0x06C | frames |
| 0x010 | pid, 0x014 game (1 DDDA, 2 DDO) | 0x070..0x07F | frame µs: last, average, 99th percentile, worst |
| 0x018 | exe time stamp, 0x01C flags | 0x080 | stutters (over 2.5x the median, over 20 ms) |
| 0x020 | sequence | 0x084..0x093 | redirects, missing, stand-ins, fatal errors |
| 0x024 | uptime ms, 0x028 update FILETIME | 0x094, 0x098 | plugins loaded / not loaded |
| 0x030..0x057 | address space total, used, largest free, peak used, smallest largest-free | 0x09C..0x0A7 | enemies active, usable, slots (-1 unknown) |
| 0x058, 0x060 | private bytes, working set | 0x0A8..0x0BF | back buffer w/h, windowed, refresh, ring position, VRAM estimate MB |
| 0x0C0 | 256 frame times (µs) | 0x4C0.. | loader version, last file, last stand-in, plugins, notes (UTF-8) |
| 0x9D8 | stage (-1 unknown) | 0x9DC, 0x9E0 | resource table used / slots |
| 0x9E4 | peak commit (private bytes) | 0x9EC | memory verdict (0 unknown, 1 headroom, 2 tight, 3 bound) |
| 0x9F0, 0x9F8 | bytes in D3DPOOL_MANAGED, its peak | 0xA00, 0xA08 | D3DPOOL_DEFAULT; SYSTEMMEM and SCRATCH |
| 0xA10 | textures and buffers held | 0xA14 | Direct3D 9 (0 unknown, 1 Windows' own, 2 chained, 3 a d3d9.dll in the game folder, 4 another module) |
| 0xA18, 0xA1C | pressure now, pressure episodes | 0xA20 | the Direct3D 9 module's path (UTF-8, 160 bytes, its end kept) |

Flags: 1 known build, 2 safe mode, 4 frame timing on, 8 address space low, 16 hang now, 32 exited,
64 large-address aware, 128 memory pressure now, 256 Direct3D pools counted.

The memory verdict answers "is any memory work worth it for this setup?" from the peak commit (the true
4 GB budget), the smallest free block and, since 0.4.0, the least address space left: `headroom` (raising a
pool or texture detail is safe), `tight` (close; trim texture data), `memory-bound` (at the 32-bit ceiling —
only fewer texture bytes in the process help, DXVK among them; a pool bump would not). Less than 400 MB of
address space left is `memory-bound` whatever the commit: under DXVK, Direct3D's texture copies are mapped
views, not commit. `native/loader/live.cpp` `MemVerdict` and `riftstone/runtime.py` `memory_verdict` compute it
from the same thresholds. The fields after it came in 0.4.0, in the page's reserved tail: an older reader
ignores them and a newer one reads an older loader's zeros as unknown.

## Direct3D 9: DXVK through the loader, and what the game holds

**Why.** A 32-bit game has 4 GB of address space, and Dark Arisen spends part of it on a second copy of its
textures. `DDDA.exe` creates every texture in `D3DPOOL_MANAGED` except render targets, dynamic and
system-memory ones: its texture setup switches on the texture's kind (`8B 7E 10 8D 47 FF 83 F8 04 77 1E` at
`0x01105383`); kind 1 is a dynamic texture in the default pool (`33 C9 BA 00 02 00 00` at `0x01105395`), kinds 2
and 5 render targets there (`33 C9 8D 51 01` at `0x0110539E`), kind 3 goes to system memory (`B9 02 00 00 00` at
`0x011053A5`), and kind 4 and every other the managed pool (`B9 01 00 00 00` at `0x011053AC`); then it calls
CreateTexture (`8B 43 5C FF D0` at `0x01105425`). Windows' Direct3D 9 keeps a system-memory copy of every
managed resource inside the process, so each MB of texture costs an MB of address space as well as video
memory. DXVK keeps those copies in memory-mapped files and maps only the ones in use (its
`d3d9.textureMemory`, 100 MB by default). How much of Dark Arisen's address space that copy takes is
**UNKNOWN until measured in the game**; the loader now counts it (the pools, below).

**Measured in the harness** (`run_tests.py --only dxvk`, 2026-09-26: Windows 11, RTX 5070 with driver
32.0.16.1692, DXVK 3.1.1 from its GitHub release, SHA-256 checked): 128 managed DXT5 textures of 2048x2048 with
all their levels (682 MB), filled through LockRect as DDDA fills its own and drawn once, in a stand-in game:

| The game's Direct3D 9 | Address space | Commit |
|---|---|---|
| Windows' own | +711 MB | +709 MB |
| DXVK 3.1.1 through `[d3d9] chain` | +125 MB | +53 MB |

A scratch probe of the same kind (not a gate) showed how it grows: Windows' own took about the textures' size
again at every step (341 MB of textures +352 MB; 1,365 MB +1,398 MB; 2,730 MB +2,805 MB, the largest free block
down to 879 MB), DXVK stayed at 560-600 MB of address space used in all three, its device itself costing about
80 MB more than Windows' own.

**How.** `Riftstone.cmd loader d3d9 add <DXVK release>` takes the x32 `d3d9.dll` out of DXVK's release (its
`.tar.gz` or `.zip`, its folder, or the DLL itself; nothing is extracted but that one file), checks it is a
32-bit DLL that exports Direct3DCreate9, copies it to `riftstone\dxvk\d3d9.dll`, puts a `dxvk.conf` beside it
(every line commented out: DXVK's defaults) and sets `[d3d9] chain = riftstone\dxvk\d3d9.dll`. Nothing goes
into the game folder itself; `loader d3d9 off` empties the key and keeps the DLL; the game must be closed. At
the game's first Direct3DCreate9 the loader (`graphics.cpp`):

- takes the DLL only if it is inside the game folder, a `.dll`, 32-bit (a 64-bit one, from DXVK's x64 folder,
  is named as such in the log) and exports Direct3DCreate9, and only when the `d3d9.dll` the game loaded is
  Windows' own: a `d3d9.dll` in the game folder (DXVK or ReShade installed the classic way) stays in charge;
- sets `DXVK_LOG_PATH` to `riftstone\logs` and `DXVK_CONFIG_FILE` to the `dxvk.conf` beside the DLL, unless
  they are set already, so DXVK writes nothing into the game folder (its log is `DDDA_d3d9.log`);
- calls the DLL's Direct3DCreate9; if that gives nothing (DXVK with no Vulkan device it can use), the game gets
  Windows' own Direct3D 9 for the rest of the session and `loader.log` says so;
- leaves it off in safe mode; a changed DLL is a changed setup (safe mode ends).

Frame timing, the in-game panel and the pool counters work on the chained runtime's objects (the harness draws the
panel over DXVK and reads it back pixel by pixel). `[live] frame_stats = 0` leaves Direct3D alone except for
the chain itself. Ninput's display arbiter (`native/ninput`) hooks the chained DLL's Direct3DCreate9 too, so
its lost/reset broadcasts reach the chained device. **UNKNOWN until seen in the game:** Dark Arisen under DXVK
at all (its shaders, fullscreen and Alt+Tab, frame pacing), how much address space it saves there, the Steam
overlay over a chained DXVK (Steam hooks Windows' own `d3d9.dll`), DDO with it (Themida).

`[d3d9]` in `riftstone_loader.ini`:

| Key | Default | What it does |
|---|---|---|
| `chain` | (empty) | a DLL inside the game folder that gives the game its Direct3D 9, e.g. `riftstone\dxvk\d3d9.dll` |
| `pool_stats` | 1 | count the textures and buffers the game holds, by pool (needs `[live] frame_stats = 1`) |

**The pools.** The loader counts the textures and buffers the game creates, in bytes by pool, until their last
Release: CreateTexture, CreateVolumeTexture, CreateCubeTexture, CreateVertexBuffer and CreateIndexBuffer in the
device's function table, and Release in the table of each kind of object (every level as the object reports
them; block-compressed formats by their 4x4 blocks). What the loader creates itself (the in-game panel) is not
counted. Since 1.0.2 the counters' lock is never held across a call into Direct3D: a Release runs the runtime's
own Release first and takes the lock only when the object is gone, and a creation stamp on each counted object
keeps an address the runtime hands out again meanwhile from being taken for the released one. Before, the lock
was held across the runtime's Release, which takes the device's own lock on DDDA's multithreaded device, so every
texture the game created waited behind every release: in the loader harness (`d3d9race`, four threads releasing
textures and their surfaces while one creates and one draws) creation ran 3.7 times faster after the change, and
the counters stayed exact. The loader's own threads (the live page, the hang watch) never wait for this lock;
a busy lock keeps the last numbers. The live page, `Riftstone.cmd live`, crash reports and the exit summary carry it, for example
*Direct3D 9: Windows' own (C:\WINDOWS\SYSTEM32\d3d9.dll); managed textures and buffers 1,234 MB*. The
runtime is named by where the game's Direct3DCreate9 went: the chained DLL when it answered, else the
`d3d9.dll` the game loaded; not by the import's target, where Windows' compatibility shims (`apphelp.dll`) can
sit (seen with a stand-in named `DDDA.exe`). A texture whose last reference goes through one of its surfaces
rather than the texture's own Release stays counted; DDDA fills its managed textures through the texture's own
LockRect.

## Memory: the pressure watch, and why nothing is flushed

Once a second the loader compares the commit (private bytes) with `[memory] pressure_mb` (3400; 0 = off), and
the address space with the rule behind `memory LOW` and the panel's ruby state (less than 400 MB left, or no
free block of 96 MB). When either is crossed, `loader.log` gets one `memory   PRESSURE:` line with the commit,
the address space, the largest free block, what Direct3D holds in the managed pool and what would help (DXVK
through `[d3d9] chain` when Windows' own Direct3D 9 holds 256 MB or more; otherwise fewer texture mods or a
lower TextureDetail). It eases under `relief_mb` (3200) with 500 MB of address space and a 128 MB block free
(`memory   pressure eased after N s`). The live page carries it (flag 128, the count at 0xA1C), and the exit
summary says `memory pressure N times`. It reads the commit now, not PeakPagefileUsage, which only rises.

**It flushes nothing, because there is nothing safe to flush.** The engine keeps no resource that no one uses:
`sResource::release` (`0x00DBA940`) takes one off the resource's count (`FF 4F 48 75 0C` at `0x00DBA962`)
and, at zero, takes it out of the table (`E8 5F F2 FF FF` at `0x00DBA96C`, `releaseTable` `0x00DB9BD0`) and calls
its deleting destructor at once (`8B 17 8B 02 6A 01 8B CF FF D0` at `0x00DBA990`). The PS3 build's symbols
list `sResource`'s methods (create, load, the table's find/regist/release, addRef, release, the async loader)
and none that collects, and its `sResource::release` does the same. The engine harness runs DDDA.exe's own
`registTable` and `release` on a stand-in resource with two users: the first release leaves it registered, the
second deletes it and empties its slot. So every resource in memory is in use, and a sweep of "cached" monster
assets or "unreferenced" textures has nothing to find: evicting any of them would be a use-after-free. What does
shrink the game's footprint is fewer bytes in the process: DXVK for Direct3D's copy (above), a lower
TextureDetail, fewer or smaller HD texture mods.

`[memory]` in `riftstone_loader.ini`:

| Key | Default | What it does |
|---|---|---|
| `pressure_mb` | 3400 | the commit (MB) past which the watch speaks; 0 turns it off |
| `relief_mb` | 3200 | the commit it eases under (with 500 MB of address space and a 128 MB block free) |

**The large-address flag.** At every start `loader.log` says `memory   DDDA.exe is large-address aware: 4095 MB
of address space` (Steam's build 2364871 has the flag; the engine harness reads it from the real exe's mapped
header), or warns when an exe lacks it: Windows then gives it 2 GB instead of 4 GB. `Riftstone.cmd laa [exe]
[--copy out.exe]` says the same of any exe and writes a copy with the flag set and its header checksum redone
(`src/riftstone/pe.py`, checked against Windows' own `CheckSumMappedFile`); it never changes the file it reads.
`doctor` checks the game's exe, and the loader harness sets the flag on its own stand-in copy for the pressure
test, which commits 3.45 GB in one process.

## Loader 0.4.1: faults fixed

A review of 0.3.2 found these; each was reproduced in the harness first (the check failed on 0.3.2) and
passes on 0.4.1. What they prove is the stand-in game's behaviour; **in the game itself all of it is
UNKNOWN** until a launch.

| Fault in 0.3.2 | Now | Harness (`run_tests.py --only ...`) |
|---|---|---|
| A stack overflow left an empty report and an empty crash note: the report writer faulted again on the few KB of stack left and the process ended with `0xC0000005`; the next start read the empty note as "a crash after 0 s", not during start-up, so safe mode and quarantine never counted it | the note first with static buffers, the report and minidump from a helper thread (above) | `overflow`: the process ends with `0xC00000FD`, the game's filter still runs, one full report and minidump, a complete note; two start-up overflows start safe mode; two in a plugin quarantine it |
| A plugin that hooked one of the game's imports itself (CreateFileW in its `DllMain`) was taken for the DRM restoring the table: the loader put its own hook back in front and called the plugin's, which called the loader's, until the stack ran out, at the first file the game opened | an import slot is hooked again only when it holds what it held before the loader hooked it, or the export itself; any other value is another module's hook, left in place and named once in `loader.log` | `hooks`: the game runs, the overlay still serves through the plugin's hook, `loader.log` says it was left in place |
| A module's own crash filter set over the loader's (it had the loader's as the filter before it) made one crash go round the two filters: three identical reports and minidumps, each filter run tens of thousands of times, the process ending in a stack overflow | the filter answers a call on a thread already inside it at once (one crash, one report), and the module's filter is still called after the report | `filters`: one report, one minidump, the module's filter runs once, the process ends with the access violation |
| A folder name of 64 characters or more in the save-backup folder stopped every start: a fixed 64-character copy fail-fasted inside the loader's start-up (`0xC0000409`), in safe mode too, with no report | only folders named like a backup (`YYYYMMDD-HHMMSS`, or with `-<n>`) are read, into buffers that fit them | `foreignsaves`: a 72-character folder name, the game starts and the save is backed up |
| Pruning removed folders it had not made: every folder in the backup folder counted as a backup, and those sorting before the time stamps (`(`, `!`, `0`...) were deleted file by file; one sorting after them was taken for the newest backup, so an unchanged save was copied again each start | the same: only backup-named folders are counted, compared or pruned (never a junction); a second backup in the same second gets `-1`, `-2`... instead of reusing the folder | `foreignsaves`: with `keep = 1` user folders and their files stay; an unchanged save is not copied again |
| `[loader] crash_reports = 0` also switched off the game's own crash filter: its `SetUnhandledExceptionFilter` call was kept for the loader's filter, which was never installed | with reports off, `SetUnhandledExceptionFilter` is not hooked (and the hook passes the call on) | `crashoff`: the game's filter runs, no report |
| A plugin whose file name holds `=` was never quarantined: its key in runtime-state.ini was split at the `=` (strikes stayed at 1 and a line was added each run), and each of its start-up crashes reset the safe-mode count | such names (and others that are no plain ini key) are kept under `~` and the hex of the name; an older loader's lines for it are removed | `names`: quarantined after two start-up crashes, no split lines; an older loader's quarantine under a non-ASCII bare name is still honoured and moved |
| `[loader] chain = dinput8.dll` loaded the loader itself as the chain, so `DirectInput8Create` called itself (`0xC0000005` at start); so did a chain whose export forwards to `dinput8.DirectInput8Create` | a chain that is the loader, or whose export is the loader's own, is refused (logged); the system dinput8 answers | `dinputchain`: both, the game runs with DirectInput |
| After a session without a crash, the last-session record could carry stack garbage as `report=` (the crash note's parts were left unset when there was no note) | every part is set before the note is read | `state`: with `[loader] test_stack_fill = 1` (the harness's way in: the stack is filled with old data first) no `report` is recorded |
| The texture stand-in was made without a lock: two threads missing a texture at once could collide (the second's exclusive open failed and the game got its fatal error), and a reader could see the path half written | made once (`InitOnceExecuteOnce`), written under a name of its own and moved into place | `guard`: 32 threads at once, all get the stand-in, 5 fresh starts (0.3.2: 6 of 32 and 1 of 32 in two of them) |
| enemy_cap was found only as `enemy_cap.asi`: renamed (`01_enemy_cap.asi`), the live view and the panel read the vanilla ten slots (`7 / 10`) instead of its own | found by its `EnemyCap_Slots` export among the loaded plugins (then by the old name) | `cap`: with a renamed stand-in enemy_cap, 17 of its 30 slots |

The loader's version is written once (`runtime.h`); the panel's header, the log, the reports and the live
page take it from there (a harness check fails if a loader source spells a version out).

## Dragon's Dogma Online

`DDO.exe` imports DirectInput8Create, so the same `dinput8.dll` loads there, and the runtime knows it
by name. DDO.exe is **Themida-packed**, so only the import-table parts run (no engine code is
patched): reports with class names, the guard (DDO textures, revision `0x9D`), live stats without the
DDDA-only fields, safe mode, the window fixes. DDO has its own borderless mode (`ScreenMode`). Install
it with the DDO toolkit: `ddon.cmd runtime install --yes` (the owner's step; `remove --yes` takes it out).
**UNKNOWN in DDO**: nothing of it has run in the client yet, and Themida may object to import-table
hooks; if the client does not start with it, remove it. The DDO toolkit's `Play Solo.cmd` now also
backs up the characters before and after each session and stops the server when the game closes
(`<path>`).

## The "master plugin" plan, checked against the exe

A plan for one DLL that fixes pop-in, shadows, texture thrashing and crashes circulated with specific
engine names and numbers. Each claim was checked statically against DDDA.exe and the PS3 build's
symbols (2026-09-25):

| Claim | Verdict | What is real, and what Riftstone does |
|---|---|---|
| `cStageArea` quadtree of 800 chunks, 3x3 active; `sStageManager::updateStreaming` x4; NOP `cStageArea::evictChunk` | **False** (names do not exist) | Streaming is `uStageSplitCtrl` over a flat grid (stage 100: 40 x 33 cells of 100 m). The window is six integers the `aStage` constructor sets: models and collision **5x5** (`0x004FE16F`), layouts **3x3** (`0x004FE1C6`); `aStage::calcEnableSplitAreaRange` (`0x00507440`). A larger window is possible (the draw-distance work in `native-limits` found the same sites); **never unloading** cells in a 32-bit game would grow memory without bound until it crashes, so it is not done. `ViewRange` does not change this window's size; it multiplies the radius within which its cells load (x1 / x2 / x3, `aStage` `0x0050771C`), so from FAR nearly the whole window loads. |
| `sShadow` caps maps at 512, shadows end 15 ft away; hook `setShadowMapSize` -> 4096 | **False** | `sShadow` is local lights only (128 px maps, 60-80 m). The sun's size comes from `ShadowQuality`: 512 / 1024 / 2048. **Built:** `[render] shadow_map_size` (4096 on HIGH), byte-verified, and the game's own `getShadowMapSize` was run to confirm it. No "15 ft" constant exists (default view distance 8192 cm). |
| hook `cDraw::getLODDistance` to force LOD0 within 500 m | **False** | it only returns the camera distance; the LOD choice is inline in the draw paths. `lod_tuner` does this properly (a `draw_distance` plugin written on 2026-09-24 is parked, unreviewed and unmerged, on the branch `wip/native-limits-0924`). |
| 2,048-slot texture descriptor cache; purge every frame; raise to 16k; dedupe textures | **False** | `sResource` has 16,384 slots (2,048 buckets x 8); a resource whose 17 candidate buckets are all full stays unregistered and is loaded again on the next request; nothing purges the table each frame. **Built:** the live view measures the fill. Textures with one path are already loaded once. |
| route `MtHeapAllocator` into mimalloc | **Not applicable** | the pools are committed up front (`MtHeapAllocator` one `VirtualAlloc`; `MtVirtualAllocator` reserves 512 MiB); swapping the allocator frees nothing. The live view and crash reports measure the real limit instead: address space and the largest free block. |
| DXVK "integration" | **Built, the owner's choice** | `[d3d9] chain` (`loader d3d9 add <DXVK release>`): DXVK's `d3d9.dll` from `riftstone\dxvk`, nothing in the game folder; measured in the harness (682 MB of managed textures: +711 MB of address space under Windows' Direct3D 9, +125 MB under DXVK 3.1.1). Nothing is downloaded or bundled: the owner's own DXVK release goes in. |
| null-resource fallback | **Applicable, built** | the texture guard (above), proven safe by the fatal-path trace; since 1.0.1 any resource an archive holds gets its own bytes instead (the archive guard). A resource no archive holds has no safe stand-in but a texture. |
| clamp `.lot` coordinates to the navmesh | **Not at run time** | positions are checked where they are made (Studio's map, `encounter`); the engine's navmesh query is not mapped. |
| corrupted records skip to the next 4-byte boundary | **Not at run time** | Riftstone's strict, fuzzed parsers refuse damaged data before it reaches the game. |
| VEH crash logger with DTI-resolved call stacks, 5 rotated logs | **Applicable, built** | an unhandled-exception filter (a VEH would see every handled exception too); class names from the DTI; 10 of each kind kept. |
| the current stage in the crash log | **True, built** | the game's own reader, above. |
| live IPC to Studio over `127.0.0.1:8766` | **Built, without a port** | shared memory: no socket, no firewall prompt, nothing leaves the PC. |

The shadow findings are collected in `docs/re-shadows.md` and the streaming ones in
`docs/re-engine-audit.md` (both re-checked 2026-09-26): `ViewRange` does not change the streaming
window's size, but it multiplies the radius within which its cells load (the LOD page's stage-100 rows).

## Proof

- `python native/loader/test/run_tests.py`: 304 checks. On stand-in games (`harness.exe` copied in as
  DDDA.exe, DDO.exe or a launcher): overlay rules, missing files and the stand-in (it parses with
  `tex.py`; also 32 threads missing textures at once), the import table put back after start-up and a
  plugin's own import hook left in place (`chain_plugin.asi`), a chained dinput8 that is the loader or
  forwards back to it refused (`fwd_chain.dll`), plugins, crash/fatal/hang reports (a stack overflow's,
  on the main thread and in a plugin; one crash through a module's chaining filter), crash reports
  switched off, report rotation, probes that are not crashes, plugin quarantine (a name with `=`, and an
  older loader's entry under a non-ASCII name), safe mode on and off (two stack overflows start it too), the
  last session's record after a clean exit, live stats with real Direct3D 9 frames (and a renamed enemy_cap,
  `cap_plugin.asi`), borderless and background running, save backups (next to a 72-character folder name
  and folders of the user's), pass-through, and that no loader source spells out a version. Why the game closed,
  with DDDA's window procedure and loop stood in (`close <how>`): Alt+F4, the close button, the window
  menu, a destroyed window, an exit with no message, a `WM_CLOSE` posted and one sent from another
  process, `SC_CLOSE`, `WM_QUIT` and `WM_DESTROY` from outside, `WM_ENDSESSION` (written before the
  process is killed), a killed game, the watch switched off, and the next start's account of each; in
  DDDA's address layout (`harness_ddda.exe`, given build 2364871's time stamp): the quit flag, Alt+F4
  with the loop's own flag after it, the exit request, and other code refused. On the real DDDA.exe code
  mapped read-only (`engine_stub.exe` + `engine_harness_core.dll`): class names for 18 engine classes
  through their own vtables; the frame-rate sites (and a tampered one refused); the shadow table with the
  game's own `getShadowMapSize`; the game's own stage reader; the resource table; enemy slots; every
  exit site, the quit flag, and DDDA's own `WM_CLOSE` handler and exit request run on a window of the
  harness (sMain+0x34 marked, `WM_DESTROY` sent to sApp's window), a tampered site refused; the exe's
  large-address flag; DDDA.exe's own `sResource::registTable` and `::release` on a stand-in resource with
  two users (the second release deletes it at once and empties its slot).
- Direct3D 9 (42 checks): the pools, on a real device, against sizes worked out in the test on its
  own (a plain texture, a DXT1 chain of 10 levels, part of a DXT5 chain, a cube, a volume, managed, default,
  system-memory and scratch textures and buffers: 677,048 / 81,920 / 20,480 bytes, 11 objects; an AddRef/Release
  pair and a surface change nothing; four released, the peak kept); `[d3d9] chain` with a stand-in DLL (called
  with SDK 32, DXVK's log and settings paths set up, named on the live page, frame timing on its device, nothing
  in the game folder), and refused or let go every other way: a DLL that cannot start (the game draws with
  Windows' own), a 64-bit one, one outside the game folder, an absolute path, a missing file, not a `.dll`, a
  `d3d9.dll` already in the game folder (it stays in charge), safe mode, `[live] frame_stats = 0` (the chain
  still applies). With DXVK when a copy is at hand (`RIFTSTONE_DXVK` or `vendor\dxvk-*\x32\d3d9.dll`): the
  measurement above, DXVK's log in `riftstone\logs`, and the in-game panel drawn over DXVK and read back.
- Memory (9 checks): a stand-in without the large-address flag warns; one given the flag by
  `pe.write_large_address_aware_copy` commits 3.45 GB: `PRESSURE` in the log, the live page's pressure flag and
  count, the verdict `memory-bound`; given back, `pressure eased`; the exit summary says it once.
- The in-game panel (`overlay`, 27 checks), on a real Direct3D 9 device with DDDA's own flags: before every
  Present the stand-in binds its own render target and depth-stencil surface, textures on two stages,
  buffers, a viewport, a scissor rectangle, shader constants, a transform and 41 render, stage and sampler
  states nobody leaves by accident; after every Present all of it is still bound (260 of 260 frames). The
  back buffer is read back after Present (the copy swap effect keeps it) and checked pixel by pixel: every
  pixel outside the panel is exactly the frame's clear colour, the cut corners are cut, most of the panel
  is its surface colour (92 % over the frame), and the meters are found by their colours: in DDDA's layout
  with a stand-in `sSetManager` (7, then 3 of 10 slots in use) and stage, the enemy meter is 30 % cyan with
  the peak ticked at 70 %; the memory meter shows the share in use with its notch at the warning level; with
  the address space reserved down to 250 MB free, the memory meter turns ruby with the droplet and the edge
  mark; after a `Reset` to 2560x1440 it draws again at scale 1.33. Also: unknown readings (another build)
  leave an empty track, bottom-left at scale 1.5, settings it cannot read named in the log with the
  defaults used, and nothing drawn when hidden, switched off, with `[live] frame_stats = 0`, or after an
  exception mid-draw (the game's state put back, no crash report).
  The frames are saved as `native/loader/out/overlay-proof.png` (`-critical`, `-1440`, and `-panels`: the
  three side by side at twice their size).
- `tests/test_runtime.py`: 38 tests (the live page with its Direct3D and pressure fields, the
  verdict's rules, reports and explanations, safe-mode state, how a session ended and how it is said,
  `Riftstone.cmd crash` saying it, save backups, the settings merge, a loader update that keeps settings and
  mods, the version tag). `tests/test_pe.py`: 14 (headers, exports, the checksum against Windows'
  CheckSumMappedFile, the large-address copy). `tests/test_loader_d3d9.py`: 6 (`loader d3d9
  add` from a release archive, a folder or the DLL; every refusal; status and off). `tests/test_studio.py`:
  the Game tab's last session.
- Fuzzing: `live_block` (~72 million inputs) and `report` (~31 million) with invariants, and Studio's new
  routes (~2 million): one finding (an uptime of dots), fixed with a regression test; 0 since. `session`
  (runtime-state.ini and the crash note, ~71 thousand inputs), with `cli`, `studio` and `report` again:
  0 findings. 0.4.0 (120 s each): `live_block` 9.2 million inputs, `pe` 8.3 million, `report` 4.6 million,
  `cli` 4.7 thousand: 0 findings; `d3d9_source` (DXVK release archives) found one: a zip entry needing
  "version 12.7" made `zipfile` raise NotImplementedError (and an encrypted or damaged entry its own errors),
  fixed with a regression test; 170,641 inputs since, 0 findings.

## What to look for on the first launch

`riftstone\logs\loader.log`: `game Dragon's Dogma: Dark Arisen (..., the build Riftstone's engine fixes
were measured on)`, `memory   DDDA.exe is large-address aware: 4095 MB of address space`, `memory   pressure
watch: over 3400 MB of commit ...`, the hooks (`KERNEL32.dll!CreateFileA installed` ... `d3d9.dll!Direct3DCreate9
installed`), `d3d9     Direct3D 9 is Windows' own (...)` (or, with DXVK, `d3d9     chained ...\riftstone\dxvk\
d3d9.dll for the game's Direct3D 9`), `d3d9     counting the game's textures and buffers by pool`, `live
Direct3D device ... (flags 0x46): frame timing on` (0x26 on the software fallback),
`overlay  Insert shows the diagnostics panel`, `saves backed up ...`, `exit     watching the
game window ...; the game's own exit (its quit flag) is verified for this build`, and on exit an `exit`
line with what closed the game and a `summary` line (frames, average fps, stutters, peak address space,
the managed pool's peak, `ended by: ...`). `Riftstone.cmd live` while it runs; its `Direct3D 9:` line says how
many MB of managed textures Windows' Direct3D 9 is keeping a copy of, which is the number that says what DXVK
would save. Any `missing` lines during normal play would mean
the engine probes loose files routinely; the trace says it does not.

Insert in game should show the panel (`overlay  panel shown on the ... back buffer` in the log) with the enemy
count, a stage number and the plugins; Insert again hides it. If the key does nothing, the log still says
whether the panel was available; `show_at_start = 1` shows it without the key.

Closing it once each way checks the close watch in the game itself: Exit Game on the title screen should
read `the game's own exit`, Alt+F4 `Alt+F4 ... (from the keyboard)`. A session that ends unexpectedly
then says in `Riftstone.cmd crash` whether it crashed, was closed (and by what), or was ended from outside.

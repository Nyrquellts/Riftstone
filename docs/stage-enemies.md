# stage_enemies: put an enemy in a stage the game never uses it in

An encounter can add a group for any enemy to any stage's group list, but the enemy only appears if the
stage loads its model. Dark Arisen loads enemy models per stage, and an ordinary stage loads only the
enemies its own groups use. So a mod that drops, say, an Archydra (`em5301`, native to the post-game
Everfall) into the Tower (st370) builds and installs cleanly, and then nothing spawns -- no crash, no log,
the group's slots just never become that enemy. `native/plugins/stage_enemies` lifts that limit the way
the engine already does for the Dragon: it makes the stage ask for the enemy's archive.

## What the game does

Dark Arisen loads archives by `ARCHIVE_TAG`: a fixed table (`docs/archive-tags.md`, ten lists of
`{u32 tag; const char* path}` at `0x01823544`) and, at run time, a request manager (`[0x018D9280]`) whose
slots the loader fills. When a stage loads, the stage loader gets a request slot (into `[ebp+0xA28]`) and
queues the stage archive's tag, `5 + stage index`: `83 C0 05` at `0x004FFDA7` computes the tag and
`E8 54 8A F1 FF` at `0x004FFDB7` (a call to the queue function `0x00418810`) adds it to the slot. For an
ordinary stage it queues nothing else. The only enemies a stage loads beyond its own are the special cases
the exe hard-codes -- `em5800` (The Dragon, tag 162) on stages 501/502, `em5801` (The Ur-Dragon, tag 163)
on 605. All 97 vanilla enemies have a tag, so any of them *can* load in any stage; the stage just has to ask.

## What the plugin does

One `jmp` at `81 FA 00 01 00 00` (`cmp edx, 0x100`, `0x004FFD8E`), right after the stage loader puts the
request slot index in `edx`. The thunk reads the stage being loaded (`[ebp+0x724]`) and, for each enemy
listed for that stage in `stage_enemies.ini`, queues that enemy's archive tag into the same slot -- with the
game's own queue call, byte for byte the same as the stage-archive one beside it (`ecx` the manager, the tag
pushed, `eax` the slot record `manager + (index*5 + 0x1F4) << 5`, `0x00418810`, `ret 4`). It then replays
the instruction the `jmp` covers (`cmp edx, 0x100`) and resumes, so the stage archive is still queued exactly
as before and nothing runs except at a stage load. An enemy is written as `emNNNN` (resolved to its tag
through the exe's own archive table -- `em5301` is 173, `em5300` 172, `em5902` 167) or as a raw tag number.

```ini
[stage_enemies]
Enabled = 1
370 = em5301        ; the Tower Summit also loads the Archydra
```

## Several enemies a stage: an Archydra with a Chimera and Drakes

One line can name several enemies; the plugin queues each once, in the line's order, into the stage's own
request. An Archydra with a Chimera and Drakes in the Tower Summit is

```ini
370 = em5301, em5200, em5900   ; the Archydra (tag 173), Chimeras (122), Drakes (165)
```

with the tags read from the exe's table: `AD 00 00 00` at `0x0153E820` (the record of `rom\enemy\em5301`),
`7A 00 00 00` at `0x0153E6D0` (`em5200`), `A5 00 00 00` at `0x0153E7E0` (`em5900`). Each enemy still needs its
group in the stage (one encounter each). Where all three already belong, no plugin is needed at all: by the
world map only stages 443 (the post-game Everfall) and 604 place Archydras, and both also place Chimeras and
Drakes (`riftstone world enemy archydra`), so encounters there are enough.

How many fit: the queue function keeps 16 tags in the slot record (`83 F8 10 7C 2E` at `0x0041882C`, after the
count at `+0x80` is raised); past 16 it chains a block from `sArchiveManager`'s extend pool
(`E8 5A FE FF FF` at `0x00418841` calls the allocator `0x004186A0`). The pool is 64 blocks of 0x88 bytes
(`69 FF 88 00 00 00` at `0x0041875B`, `8D 84 37 80 1C 00 00` at `0x00418761`) tracked by 8 bitmap bytes at
`+0x1C75` (`80 BC 3E 75 1C 00 00 FF` at `0x004186B0`, `83 FF 08 72 96` at `0x00418715`), shared by every slot,
and an empty pool is the game's fatal error "sArchiveManager: issueExtendPool : Extend Pool overflow."
(`E8 D7 43 8F 00` at `0x00418724`, a call to `0x00D0CB00`). So the plugin takes at most 16 enemies a stage and
64 in all, an enemy listed twice for one stage is queued once, and a line too long to read (255 characters)
is skipped whole; each refusal is a line in `stage_enemies.log`. A stage's own tags and these together past 16
use extend-pool blocks for that load only; how many the rest of the game holds at that moment: UNKNOWN.

## A mod someone else made: install writes the lines

Since 1.0.4 `riftstone install` (Studio's Install too, and so every mod a player makes from a package with
`package install`) works the lines out itself: for every layout the installed mods change it reads the
enemies they place (`stage_enemies.placed`), drops those the stage's own layouts and enemy groups already use
(`native`, from the world map), and keeps one block of `stage_enemies.ini` holding exactly the rest, an
enemy archive each, 16 at most a stage:

```ini
; -- riftstone install: enemies the installed mods place in stages that never load them. Install rewrites
;    these lines each time (restore removes them); put your own lines outside them. --
; stage 370: Tower Trio
370 = em5200, em5301, em5900
; -- end of riftstone install's lines --
```

The block goes at the end of `[stage_enemies]` (or where it was), the mods' names on a comment line, never
on the value line (a plugin before 1.0.4 read every word after `=`). Every other byte of the file stays: the
player's own lines, its code page or UTF-16, its line endings. The next install rewrites the block, an
install with no such mod and `riftstone restore` remove it. The plugin itself is left as the player set it:
the install says when it is off (`plugins on stage_enemies`) or not in the game (`loader plugin add
stage_enemies`), since until then those enemies never appear. An encounter for an enemy the stage does not use
says the same when it is written. `tests/test_stage_enemies.py` makes a mod, packages it, installs the package
as a player and installs that mod; `fuzz` target `stage_enemies_ini` holds the block's rules on any file.

The plugin reads its whole section (32,768 characters; it read 4,096 before 1.0.4, so a long ini lost the
lines at its end, where install puts its own).

## Safety

DDDA.exe build 2364871 only. The patched bytes and the code around them -- the slot fetch (`0x004FFD88`),
the slot-valid branch (`0x004FFD94`), the stage-number read (`0x004FFD96`), the manager load (`0x004FFDA1`),
the stage-archive queue (`0x004FFDA7`, `0x004FFDB7`) and the queue function's prologue (`0x00418810`) -- are
all compared before anything is patched; on any difference nothing is patched and
`riftstone\logs\stage_enemies.log` says why. Queuing an archive only loads its resources into the stage's
request, the same request the stage archive rides in; it changes nothing for a stage with no line in the ini.

Whether a foreign enemy then behaves in a foreign stage -- its AI, its motions, its navigation -- is
**UNKNOWN until played**. Loading the model is the necessary first step, not a guarantee the fight works.

**Seen on the owner's machine (2026-09-28; `riftstone\logs\crash-20260928-124748` and `-134055`):** with
`220 = em5301` in the ini (Gran Soren), the game stopped twice during start-up in stage 220, both times in one
job of `sObjCollision`. Its DTI registration pushes the class size (`68 50 C5 09 00` at `0x0132C466`: 0x9C550
bytes) (`push` of its name right after the size: `sObjCollision` at `0x0155EDE4`): 800 records of 0x320 bytes from `+0x40` and a counter at `+0x9C4F4`. The job (`0x00479C10`)
waits on that counter (`8D 9E F4 C4 09 00` at `0x00479C1B`), then sweeps records up to the count at `+0x34`
(`3B 46 34` at `0x00479C24`; `69 C0 20 03 00 00` at `0x00479C30` and `8D 4C 30 40` at `0x00479C36` make a
record's address) and reads a pointer held in each (`8B 40 04` at `0x00479C44` and at `0x00479C64`: the two
faulting instructions, reading address 5). By the reports' registers the record swept was past the 800th.
**The cause is not this plugin** (`docs/re-collision-cap.md`, 2026-10-06): the count at `+0x34` is the frame's
request counter, which every allocator of an entry node raises *before* checking the bound of 800, so a frame
that asks for more than 800 hit shapes (Gran Soren with the six Archydras the owner's `Gran Soren Arena` mod
places in group 0) leaves it past the table, and the job sweeps records that are not there. Two more crashes
of the same shape came on 2026-10-06 with this plugin off. The `collision_cap` plugin grows the table and
clamps the sweep. The loader's safe mode the first two crashes armed ran the game with no mod or plugin until
2026-10-06; this plugin stays off on the owner's machine only because nothing there needs it.

## Harness

`native/plugins/stage_enemies/test/run_tests.py` maps the real DDDA.exe read-only (no game launched), loads
the plugin so it verifies and patches that copy, then checks: the byte sites; tag resolution against the real
archive table (`em5301` -> 173, `em5300` -> 172, `em5800` -> 162, `em5902` -> 167); the dispatch through a
recorder standing in for the game's queue (stage 370 queues 173, an unlisted stage queues nothing); and the
real thunk entered at the stage loader with a fake frame, checking the tag is queued and `edx`/`esp` come
through. Five profiles: one stage listed; the mix (`370 = em5301, em5200, em5900` with a comment after it and
the Archydra listed again: 173, 122, 165 once each; 17 tags for one stage: the first 16; a line too long:
nothing); the ini as install leaves it, its block after 6,000 characters of comments (122, 173, 165); the
shipped ini; `Enabled = 0`.

## Install

`Riftstone.cmd loader plugin add stage_enemies` (with the game closed) copies `stage_enemies.asi` and its ini
into the loader's plugins folder; an ini already there keeps its values. The enemy's group still has to be in
the stage (an encounter, e.g. `riftstone encounter 370 em5301 --count 6 ...`); installing that mod then writes
the stage's line (above). The player zip carries the plugin switched off (`plugins on stage_enemies` turns it
on). Seen in game (2026-09-28, `stage_enemies.log`): the plugin queued tag 173 when stage 220 loaded (LOADED).
A foreign enemy appearing and fighting in a foreign stage: UNKNOWN until played.

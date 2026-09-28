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

## Safety

DDDA.exe build 2364871 only. The patched bytes and the code around them -- the slot fetch (`0x004FFD88`),
the slot-valid branch (`0x004FFD94`), the stage-number read (`0x004FFD96`), the manager load (`0x004FFDA1`),
the stage-archive queue (`0x004FFDA7`, `0x004FFDB7`) and the queue function's prologue (`0x00418810`) -- are
all compared before anything is patched; on any difference nothing is patched and
`riftstone\logs\stage_enemies.log` says why. Queuing an archive only loads its resources into the stage's
request, the same request the stage archive rides in; it changes nothing for a stage with no line in the ini.

Whether a foreign enemy then behaves in a foreign stage -- its AI, its motions, its navigation -- is
**UNKNOWN until played**. Loading the model is the necessary first step, not a guarantee the fight works.

## Harness

`native/plugins/stage_enemies/test/run_tests.py` maps the real DDDA.exe read-only (no game launched), loads
the plugin so it verifies and patches that copy, then checks: the byte sites; tag resolution against the real
archive table (`em5301` -> 173, `em5300` -> 172, `em5800` -> 162, `em5902` -> 167); the dispatch through a
recorder standing in for the game's queue (stage 370 queues 173, an unlisted stage queues nothing); and the
real thunk entered at the stage loader with a fake frame, checking the tag is queued and `edx`/`esp` come
through. Two profiles: the shipped ini and `Enabled = 0`.

## Install

`Riftstone.cmd loader plugin add stage_enemies` (with the game closed) copies `stage_enemies.asi` and its ini
into the loader's plugins folder; an ini already there keeps its values. The enemy's group still has to be in
the stage (an encounter, e.g. `riftstone encounter 370 em5301 --count 6 ...`). In game: UNKNOWN until played.

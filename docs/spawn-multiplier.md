# Spawn multiplier: every enemy group with N times its placements

A player asked (Nexus, 2026-10-04) for a way to multiply all the spawns by 2 or 3 now that `enemy_cap` lifts the
ten-enemy limit, instead of doubling every placement by hand. `riftstone multiply` does that for the whole game in
one command and writes the result as one mod:

```bat
Riftstone.cmd multiply 2
Riftstone.cmd install "Spawn Multiplier"
Riftstone.cmd plugins on enemy_cap
```

Measured on 2026-10-04 against the installed game (Steam build 2364871) and its data; `tools/multiply_proof.py`
repeats the measurements of this page and fails when a documented count differs. **Nothing here has been played:
every in-game effect is UNKNOWN.** What is proven is what the files hold.

## What "N times" means

A group's placements each spawn their enemy (`mSetCountMax` -1), or the group brings its cap in total from them
(`docs/spawn-system.md`, `docs/world-map.md`). So N times as many is N - 1 copies of every placement, and N times
the cap of a capped group. The rule is `rules/multiply.nyr` (NYR-Lang; `nyrc` compiles it into
`src/riftstone/rules/multiply.py`):

- `copies = min(eligible * (factor - 1), free ids)`, handed out over the group's placements in turn;
- `mSetCountMax = min(cap * factor, 9999)` for a group with a cap (21 groups: hordes and picked sets), which
  keeps the share of spawn points the game gave it.

**The kill record is the limit.** The game keeps one bit per placement id in a 32-bit mask a group
(`B8 01 00 00 00 D3 E0 0F AC FE 1C` at `0x004A653D`, `docs/enemy-waves.md`), and every one of the game's 1,175
enemy groups uses each id once across its layouts, all under 32 (measured: 0 ids past it, 0 used twice). A copy
takes the smallest id free in its whole group (`lot.free_id`), so a group stops at 32 placements: at x2, 648 of
the 5,995 copies asked for do not fit (57 groups full); at x3, 2,336 (118 groups). The command says how many. A
full group already places 32, more than `enemy_cap`'s default pool of 30.

## Which placements

Of the game's 6,606 enemy placements (1,419 layouts, 1,175 groups):

| Left as the game has them | Count | Why |
|---|---|---|
| a placement with an AI script of its own (`mFsmFilePath`, an NPC's `FSMPath`) | 178 | a quest's target or a scripted fight: a second one would follow the same script |
| the Dragon, the Ur-Dragon, Daimon (`em580*`, `em700*`) | 8 | the story's own fights; never copied, with any option |
| big monsters (`mBossFlag`) | 425 | off by default; `--bosses` multiplies them too, their copies three times as far away |

Everything else is copied, in its own layout, with every field of its original:

- **Hostile humans placed as NPCs** (`cSetInfoNpc`, 555 placements) are copied: all of them share `mNpcId` -1, so
  a copy names nobody new. They are never resized (the game builds them from equipment and scales none).
- **Life point groups** (36 values on 552 placements) are shared by whole groups and reused across stages in the
  game's own data; a copy keeps its original's value.
- A copy is always of an enemy its own layout already places: the same enemy, in the same group, in the same
  layout. Nothing new is asked of the stage (no enemy it does not already use, `docs/stage-enemies.md`).

`--stage` (stage numbers, several at once: `--stage 100 424`; `st424` works too) and `--enemy` (names or ids:
`--enemy goblin em0200 "dire wolf"`) narrow it.

## Where a copy stands

A copy never stands on its original: enemies spawned into one another is the first thing a doubled layout would
show. Around the original the command tries rings of 6, 12, 18 and 24 spots, 150 cm apart by default (`--spread`;
the game's own placements have their nearest neighbour within 204 cm for a quarter of them and 374 cm for half),
turned by the placement's own number so two runs give the same layout, and takes the first spot that is free of
every other placement and on ground it can prove:

| Ground | Where | x2 | x3 |
|---|---|---|---|
| the stage's navigation mesh (`nav.py`) | every stage that has one: reached on foot from the original (no wall or drop between), within 250 cm of its height, with the room to the mesh's edge the original has (150 cm at most) | 2,551 | 4,800 |
| the open field's walkable collision (`terrain.Ground`) | stage 100, which has no mesh: on a surface whose normal's Y is at least 0.7 (within about 45 degrees of level), within 250 cm of the original's height, with ground half-way to it | 1,868 | 3,104 |
| close beside the original, at its height | where neither says anything: a flyer, a placement off the ground, a spot with no free ground | 928 | 1,750 |

The field's ground is the cells' merged `e` collision (`st100e_<m>m<n>n_mrg00.sbc`, `docs/terrain.md`). Two
things were measured to read it:

- **A triangle's vertex numbers count from its part's first vertex** (`sbc.triangles`). Read that way the stored
  normal is the corners' own for all 581,218 triangles of the 192 cells that hold an enemy; read as file-wide
  numbers, for 40%.
- Of stage 100's 2,643 enemy placements, 2,552 have that ground under them and 2,537 stand within 100 cm of it
  (half of them within a centimetre). The rest, flyers among them, get the "beside" rule.

Of the stages that place enemies, only 100 (2,643 placements) and 501 (10) have no mesh.

## What makes it look less like a copy

On by default; `--plain` gives exact copies.

- Each copy faces up to 0.45 rad away from its original and is 0.92 to 1.12 times its size, by a number that is
  the placement's own (no randomness between runs). Wolves choose their own size (`mIsRandamScale`) and people
  have none: both are left.
- **Champions** (`--no-champions` turns them off): a group that gets three or more ordinary copies has one of them
  1.3 times its original's size with twice the health, through the placement's own HP multiplier
  (`HP倍率設定の有無` / `HPの倍率`, `docs/enemy-hp.md`; on top of a multiplier the game already gave the original).
  1.3 is the game's own number: its three scaled hobgoblin leaders are 1.3. x2 makes 595 champions, x3 650.

## What it writes

A mod (`Spawn Multiplier` unless `--mod` names another) holding each changed layout and group list as YAML under
`files/`, and `riftstone-multiply.json` (`riftstone-multiply/1`): the factor, the options and the SHA-256 of every
file it wrote. One resource in `files/` is the right form: 896 enemy layouts sit in one archive and 523 in three
(a field cell's `lot`, `split` and `split_sub`), and no layout's archives hold different bytes.

- **A run starts from the game's own layouts, never from the mod's.** Running x3 after x2 gives x3, not x6: the
  files the last run wrote and nobody changed since are replaced, and those the new run no longer needs are
  removed.
- A layout or group list the mod holds from anything else, or one of the command's own files that was edited
  since, is refused before a file is written. Give the multiplier a mod of its own: installed beside other mods,
  layouts merge record by record and group lists group by group (`lotmerge.py`, `gplmerge.py`).
- `--dry-run` plans and reports without writing.

## What it costs installed

Dark Arisen mods install as whole archives rebuilt into the loader's overlay. The changed layouts and group
lists are 1.9 MB, but the archives that hold them are not small:

| | Archives rebuilt | On disk |
|---|---|---|
| x2, the whole game | 576 | 6,516 MB (the field's `split_sub` 2,689 MB and `split` 597 MB, the dungeons' stage archives 3,213 MB) |
| x2 with `--bosses` | 590 | 6,750 MB |

The command prints this before writing. `--stage` keeps it to the stages named.

## Measured

`tools/multiply_proof.py`, 2026-10-04:

| | x2 | x3 | x2 `--bosses` |
|---|---|---|---|
| copies | 5,347 | 9,654 | 5,772 |
| groups that grow | 854 | 854 | 1,102 |
| stages | 43 | 43 | 45 |
| files in the mod | 1,038 | | 1,287 |
| ids past 32 / used twice, every group as multiplied | 0 / 0 | 0 / 0 | 0 / 0 |

Planning the whole game took 6 to 25 seconds on the owner's PC.

## How it is proven

- `tests/test_multiply.py` (33 tests) on a stand-in game (`tests/multiply_fixture.py`): the counts, the ids, the
  cap, what is left alone, the ground on the mesh and on a field cell, the mod written, built and written again.
- `fuzz/targets.py` `multiply`: hostile options; a plan holds to its word (the game's records first and
  unchanged, every copy a copy of an enemy of its layout within the rings, ids once and under 32, a group list
  changed in caps alone), planning writes nothing, a written mod loads back to the plan's bytes, a file somebody
  edited is never overwritten.
- `tools/multiply_proof.py` on the real game: the numbers above, read-only.

## UNKNOWN until played

All of it. In particular:

- whether twice the placements of a group all appear (the pool is ten without `enemy_cap`, 30 by default with
  it, `docs/re-enemy-cap.md`; the save keeps ten enemy records);
- the frame rate and memory with every group doubled;
- how a copy "close beside" its original (928 at x2) settles when the game puts it on the ground;
- a champion in play: its size on screen, its health, whether a pack reads as having a leader;
- hostile humans copied as NPCs: that two placements of `mNpcId` -1 behave apart, as the game's own 74 groups
  that hold several suggest;
- quests that count a group's dead: the kill record has a bit for each copy, so "the group is dead" needs the
  copies dead too.

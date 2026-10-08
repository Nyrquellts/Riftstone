# Arrange: enemies pasted in one spot set out in a shape, and export: a mod as files to copy over the game's

A modder asked (Discord, 2026-10-08) for two things. First, they had placed a bunch of custom enemies by pasting them
all at one spot to try an encounter, and wanted a tool to set them out in an interesting way. Second, they wanted
Riftstone to produce files they could copy over the game's own, since that is how they make their mods.
`riftstone arrange` answers the first and `riftstone export` the second:

```bat
Riftstone.cmd arrange st100_45m55n_e143.lot --shape camp
Riftstone.cmd arrange --mod "My Mod" --shape wedge --toward 54000,-46000
Riftstone.cmd export "My Mod" --out MyModFiles
```

Measured on 2026-10-08 against the installed game (Steam build 2364871) and its data; `tools/facing_proof.py`
repeats the measurements of this page and fails when one comes out otherwise. **Nothing here has been played:
where the enemies stand and which way they face in game is UNKNOWN.** What is proven is what the files hold and
what the game's own layouts show.

## Arrange

### What it moves

A **stack** is two or more enemy placements (any `cSetInfoEnemy*` record, or an NPC record with a position:
hostile humans are placed as NPCs) within 1 m of each other on the ground plan and within 2.5 m in height.
`--records 3,5-7` names the records to set out instead, as `riftstone spawns list` numbers them.

**The game's own placements stay.** The game stands placements together itself: 174 stacks of 501 placements in
117 of its 6,342 layouts, some at 0, 0, 0 (measured 2026-10-08). So a placement that the game's own layout of the
same name holds unchanged (the same id, kind and position) is never moved unless `--records` names it. A copy
pasted on top of one of the game's placements is still a stack: the copies are set out around the original, and
the original stays. That matters most for a whole archive or a whole mod: on a copy of `stage424.arc` with eight
copies of a placement pasted onto it, exactly those eight moved, and the other 53 layouts were left alone.

Only a
placement's position and its heading (`mAngle` y) change: its id, enemy, every other field and every record
nobody named keep their bytes (the tests and the fuzz target check that field by field). The same layout and
options give the same bytes every time; nothing is random.

### The shapes

The biggest enemy (a big monster first, then the higher tier, then the larger) takes the first spot: the middle,
the front or the tip.

| Shape | What |
|---|---|
| `scatter` (default) | a sunflower spiral one spacing apart, each spot nudged a little, each enemy facing the middle give or take 90 degrees: a third face within 30 degrees of it, near the game's own 28% |
| `ring` | a circle, everyone facing out (a guard post) |
| `camp` | a circle facing in, the biggest in the middle when there are five or more |
| `line` | ranks of up to eight facing the heading, every other rank staggered |
| `wedge` | a point facing the heading |
| `flank` | an ambush across the heading's path: half on each side facing the path, the biggest waiting at the far end (three or more) |

The heading is `--toward x,z` (face that point), else `--heading DEG` (the game's own angle), else the way the
stack already faces (the mean of its headings).

### Where each one stands

Each enemy is tried at its spot in the shape, then on rings around it until a spot fits:

- **a stage with a navigation mesh**: the mesh under it, reached on foot from the stack's middle (no longer than
  twice the straight line plus 5 m: not behind a wall or across a drop), with as much room to the mesh's edge as
  the game gives that enemy (`bestiary.room`, at most 3 m) or the middle has. These are the rules
  `riftstone encounter` and `multiply` already stand their enemies by (`docs/spawn-multiplier.md`);
- **the open field** (stage 100, no mesh): the cells' walkable collision (`terrain.Ground`, `docs/terrain.md`) no
  steeper than about 45 degrees, with ground every 2 m along the line from the middle, at most 35 cm over it;
- **elsewhere**, for a stack off the ground, and for enemies the game keeps off it (`bestiary.walks` false:
  Giant Bats, harpies, wall crawlers): the spot at the placement's own height.

Every pair stands at least 0.75 of the two enemies' spacings apart. An enemy whose spot has no room near it takes
the free ground nearest the stack's middle (18 rings around it), then the best spot it can find at half that
distance; when there is none it stays in the pile where it was, and the command says how many did. It is never
put on top of another at a new spot, nor in the air. On the installed game, 8 copies pasted onto a placement in
stages 100, 200, 220 and 424 came out on the ground in every shape, the closest two at least 0.75 of a spacing
apart. Stage 330's pile was Giant Bats, which kept their height.

### Spacing

An enemy's spacing is the game's own: the median distance from one of its placements to the nearest other of the
same enemy in the same layout (pairs closer than 30 cm are pasted pairs and left out), kept within 1.5..15 m. 87
enemies have one: the median of their medians is 630 cm (least 45, most 2,457), the goblins' (`em0100`) 377 cm. A
big monster the game never places two of gets twice its room; any other enemy the median. `--spread` sets one
spacing (0.6..30 m) for all.

### Which way a placement faces

- **A model faces +Z in its own space.** In the rest pose of the Arisen's body
  (`model\pl\m\m_base\m000\m000`), the toes stand ahead of the ankles along +Z (dx 0, dy -7, dz +12 cm for both
  feet).
- **`mAngle` y = atan2(dx, dz).** It turns +Z to (sin y, 0, cos y). The proof: over the game's 6,339 placements
  in 1,002 enemy and NPC layouts of three or more, the difference between `mAngle` y and the way to the layout's
  middle bunches under this reading (mean resultant 0.211, at -3.6 degrees). The mirrored reading, atan2(-dx, dz),
  does not bunch at all (0.008). The two readings that turn +Z a quarter turn are ruled out by the toes; one of
  them bunches as much, but side-on (86 degrees).
- **The game's own groups lean toward their middle**: 28% face within 30 degrees of it, against 17% by chance.

So `--heading` takes the game's own angle in degrees, and `--toward x,z` faces that point.

### What it reads and writes

- a loose `.lot` or `.lot.yaml`, or a folder of them (every one under it). It is written back in place, and the
  first original is kept as `<name>.bak`;
- an archive (`.arc`): its layouts are rewritten, the archive is rebuilt and checked entry by entry
  (`verify_build`), and the original is kept as `<name>.arc.bak`;
- a layout's name (`st424_00m00n_e09`) with `--mod`: the mod's copy, else the game's, saved into the mod;
  `--mod` alone arranges every layout the mod holds. Without a mod, a layout of the game needs `--dry-run`.

`--dry-run` says what it would do and writes nothing. Nothing is ever written inside the game folder, and
Dragon's Dogma Online is refused, since its enemies stand where the server's spawn table puts them (`riftstone ddo`).

## Export

`riftstone export MOD [MOD ...] --out FOLDER` builds the mods the way `install` would (planned together:
the same merges of group lists and layouts, the same conflict report) and writes the result as the game's own
files, under the paths they replace:

- `nativePC\<archive>.arc`: each archive the mods change, rebuilt whole;
- `nativePC\<path>`: a mod's loose files (`loose/`; a later mod's copy wins, and the command says so);
  `compat\` programs under `riftstone\overlay\compat\`;
- `stage_enemies lines.ini`: the `[stage_enemies]` lines the `stage_enemies` plugin needs for an enemy new to a
  stage (`docs/stage-enemies.md`), to paste into `stage_enemies.ini`;
- `riftstone-export.json` (schema `riftstone-export/1`): every file with its size, its SHA-256 and the SHA-256 of
  the archive it replaces;
- `READ ME - Riftstone export.txt`: how to copy them over the game's own (keep the originals first, or let
  Steam's "Verify integrity of game files" put them back). It also says that `riftstone install` is the better
  way, since it keeps the game's files stock and puts everything back with `restore`, and that these files are
  the game's data and must never be shared: a mod is shared with `riftstone package` (`docs/legal.md`).

It refuses an output folder inside the game folder, and one that already holds files unless `--force` is given.
It never writes to the game itself: the player copies the files.

## Proof

- `tests/test_arrange.py` (26 tests): every shape on the corridor mesh of the stand-in game
  (`tests/multiply_fixture.py`: no spot in the hole, each reached on foot, every gap at least 0.75 of a spacing),
  a crowded corridor (16 goblins 30 m apart: the ones with no room stay in the pile, the rest keep half that
  distance), the game's own placements staying while copies pasted on them move, flyers, off the mesh, the open field, only the position and heading changing, the same bytes every
  time; the command on files, folders, YAML, an archive and a mod; never inside the game; export's manifest,
  archives, loose files and README.
- `fuzz/targets.py` `arrange`: hostile stacks and options on the stand-in game. Invariants: refused, or the same
  arranged twice; a byte-exact rebuild; ids, kinds and other fields unchanged; only the stacks (or the records
  named) moving, less the game's own; a placement said to be on the mesh standing on it, one on the field within 35 cm of its ground,
  one kept at its spot; no two set out on one spot.
- `tools/facing_proof.py`: the toes, the four readings and the spacings, on the installed game.

## UNKNOWN

- Whether the game shows each enemy where its record stands and facing its heading: not played.
- How an exported archive behaves when copied over the game's own: not played. The archives are the ones
  `install` serves through the loader's overlay, built by the same code.
- Placing an enemy where the Arisen stands in game (a point taken from a running game) needs the Arisen's
  position in memory: not traced.

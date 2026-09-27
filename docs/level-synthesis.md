# Level synthesis: dungeons laid on the game's own walkable ground

`riftstone dungeon <stage>` writes a whole dungeon's population into a mod: a mission of fights, an ambush, a
horde, a guardian and a boss, with side encounters, each placed where the stage's own AI can walk and every
spawn point checked, after writing, to stand on ground reached on foot from the stage's doors. Of Dark
Arisen's 56 stages, 53 have a navigation mesh (all but the open field, 501 and 703). Over three seeds each,
the director made 99 dungeons from each stage's own enemies and 24 more from the whole game's (`--pool
game`); five stages have no enemy group for an encounter to copy (250, 400, 610, 611, 615), and seven are
too small for the built-in mission (401, 402, 405, 406, 500, 601, 602), where a shorter `--mission` fits
(one fight on all seven, three on three).

What it does not do: new rooms or corridors (a stage's geometry is its models and collision; Riftstone moves
them exactly, `docs/terrain.md`, but does not author them), a new stage (registering one needs a native plugin,
`docs/roadmap.md`), locks and keys (DDDA has no data-side door lock this could set). How a dungeon plays is
**UNKNOWN until played**: the files are valid, and the placements stand where the game's own walkers stand.

```bat
Riftstone.cmd nav 424                              :: the mesh stage 424 loads (stage 420's), its doors
Riftstone.cmd nav 424 --at 2397,0,-359             :: on walkable ground? room? how far on foot from a door?
Riftstone.cmd dungeon 424 --seed 1                 :: plan a dungeon and show it (nothing written)
Riftstone.cmd dungeon 424 --seed 1 --mod "Stage 424 Remix" --plan remix.json
Riftstone.cmd dungeon 704 --pool game --exclude death --mission my-mission.json --mod "Leaper's Revenge"
Riftstone.cmd encounter 424 goblin --count 6 --at 2397,0,-359 --mod M   :: spawn points go on the ground now
```

In Studio: the World tab, a stage, then the **Dungeon** box -- a seed, the enemies to choose from, what to
leave out; Plan draws the numbered places, the doors and every spawn point on the map; Write to mod writes it.

## What it stands on (each measured on build 2364871, `tools/nav_proof.py` repeats it)

**The navigation mesh** (`rNavigationMesh`, `.nav`, `src/riftstone/nav.py`, layout in `docs/formats.md`): the
triangles the AI walks on, each linked to the neighbours across its edges with the distance between their
centroids in metres. Read from `rNavigationMesh::load` (0x01099E00) and its body reader (0x01099100); all 41
distinct Dark Arisen files and all 332 of Online rebuild byte for byte (`check_corpus --only nav`). The game's
walkers stand on it: 2,840 enemy placements, height over the mesh median 0 cm, 95% within 35 cm.

**Which mesh a stage loads**: the engine's stage table (65 stage numbers at 0x01530348, a 22-byte row each at
0x015303D0, the mesh's stage in field 6, read at 0x00503368). Eleven stages borrow: 402 -> 401; 421, 423, 424,
425 -> 420; 431, 435, 436 -> 430; 444, 445 -> 443; 447 -> 446 (`nav.NAV_OF`, checked against the player's exe
by `check_corpus --only nav`). Stage 424's enemies stand on 420's mesh as closely as 420's own.

**The doors**: a stage's start positions (`scr\st<N>\etc\st<N>.stp`, rStartPos: where the player appears
coming in). 277 of the 283 stand on their stage's mesh, 246 on its main region (the rest open onto
separate pieces of their mesh: 33 of them are stage 601's, whose places the game names the Chambers of
Anxiety, Absence, Hesitation, Apprehension and Remorse).

**The bestiary** (`src/riftstone/bestiary.py`, cached in `%LOCALAPPDATA%\Riftstone\bestiary-*.json`):

| Fact | From | Examples |
|---|---|---|
| walks | the share of its placements (in stages with a mesh) on the mesh; half or more = walker | Cyclopes 29/29, Garm 42/42, Goblins 100/121 walk; Giant Bats 14/160, Harpies 5/25, Liches 9/20 do not |
| room | the 10th percentile of the distance, across the ground, from its on-mesh placements to the mesh's edge | Goblins 0.4 m, Gorecyclopes 2.8 m, Chimeras 5.3 m, Dire Wyrms 7.7 m |
| tier | EXP (`経験値` of `charparam\em\<id>_cmn.prp`) and the big-monster flag, through `rules/tiers.nyr` | 1: Wolves 50 .. Hobgoblins 380; 2: Saurians 410 .. Greater Goblins 1,800; 3: Skeleton Brutes 2,300 .. Griffins 9,200; 4: from 10,000, and flagged monsters with no EXP |
| critter | EXP above 0 and below a wolf's 50 (`rules/tiers.nyr`) | rats, spiders, snakes, boars, deer: left out unless named |
| companions | the stages that place both | used when choosing from the whole game |

## How a dungeon is made (`src/riftstone/dungeon.py`)

1. **Space.** The stage's mesh (the mod's own copy when it holds one, as the encounters and the check read it),
   its doors, the region they open into (the triangles the mesh's links join to
   them) and each triangle's depth (metres by the mesh from the nearest door). The **main path** runs from a
   door to the deepest place with room for a boss (`rules/beats.nyr`'s boss room, 4 m). **Places** are laid
   along it every half spacing, then over the rest of the region by farthest-point sampling: each new place
   is the triangle farthest, by the mesh, from every place and door so far, until none is `--spacing` (15 m)
   away. A place is at least 12 m from a door, has 1.5 m of room, and lies within 60 m of an enemy the stage
   already places (a new group copies the nearest group's areas, `encounter.py`). Each knows its depth, room,
   how far off the main path it is (main within 8 m, beside within 25 m, side beyond) and, where the stage
   names its places (`.spn`), its room's name. **Every place is reachable on foot from a door by
   construction**: it lies in the doors' region.
2. **Mission** (`src/riftstone/mission.py`): a sequence of beats -- Fight, Ambush, Horde, Guardian, Boss --
   from a grammar, as J. Dormans' mission grammars generate a mission before mapping it onto space. `?Kind`
   is a side beat: off the main path, after the main beat before it. The built-in grammar:

   ```
   Dungeon  -> Approach Trial Depths Boss
   Approach -> Fight | Fight Fight | Fight ?Ambush          (weights 2 3 2)
   Trial    -> Guardian | Horde | Fight ?Guardian           (2 2 1)
   Depths   -> Fight Fight | Fight Ambush Fight | Horde ?Fight   (3 2 1)
   ```

   Your own is JSON (`riftstone-mission/1`, `--mission`): `{"format": "riftstone-mission/1", "start": "D",
   "rules": {"D": [["Fight", "?Fight", "Boss"]]}, "weights": {...}}`. Expansion is seeded, finite (40 beats,
   2,000 rewrites at most), and a Boss must end the main path.
3. **Places for the beats**: a constraint problem solved by `src/riftstone/wfc.py` -- WaveFunctionCollapse's
   order (collapse the variable of least Shannon entropy to a weighted choice, M. Gumin), arc consistency
   (AC-3) after every choice, and backtracking, which makes it complete: it returns an assignment that
   satisfies every constraint, proves there is none, or says its budget ran out (Karth and Smith describe WFC
   as exactly this kind of constraint solving). Variables are the beats, values the places. Main beats prefer
   the main path, an ambush beside it, side beats off it; the boss the deepest places (75% of the path or
   more). Constraints: all different; at least half the spacing apart; each main beat at least 30% of the
   spacing deeper than the one before; a side beat between the main beats around it; each place with the room
   its beat needs. Weights favour places at the beat's share of the path.
4. **Enemies for the beats**: a second problem over the same solver. A beat takes the tiers
   `rules/beats.nyr` gives it at its depth, from the pool (by default the named walkers the game places in this
   stage and the stages sharing its mesh; `--pool game` the whole game; `--enemies`, `--exclude`), each with
   no more room needed than its place has. Neighbouring main beats differ; with `--pool game` they are also
   companions. Weights: how often the game places each enemy in these stages, so the mix resembles the
   stage's own.
5. **When it does not fit**: the seed's next missions (12), then places closer together (15, 11, 8 m); a beat
   whose tiers the pool lacks takes weaker enemies, or one tier stronger at most (never three hydras for a
   "fight"); every such change is said. A stage whose own enemies are all critters, flyers or too rarely placed
   to measure (an empty pool) needs `--pool game`. A stage with no enemy group at all is refused before
   anything is planned: each encounter copies one of the stage's groups (step 6). A stage with no place at
   all (none 12 m from the doors with room, near its enemies, at any of the three spacings) is refused as too
   small for a dungeon; the end of the main path keeps those rules like every other place.
6. **Which group each encounter copies**: a new group takes its areas and conditions from a vanilla group of
   the stage (`encounter.py`). A group whose `mLoadCondition.mLotFlag` is set loads only while its lot flag
   (`mDataLotFlag.mFlagNo`) is: **every one of the 397 enemy groups of stages 420-447** has one (their base
   lists; the six groups of st443/st444's `_dlc01` lists have none), and 806 of the game's 1,169. The director copies the nearest group with no lot flag within 60 m of the place,
   else the nearest group, and clears a copied lot-flag condition (`encounter --always`; the plan's
   `"always": true`), so the encounter loads whenever the stage does, as the game's groups without a flag do.
   `--keep-lot-flags` keeps them (the encounter then appears only while that flag is set). What sets each of
   the game's lot flags is UNKNOWN here.
7. **Plan, check, write, read back**: the dungeon becomes an encounter plan (`riftstone-encounters/1`, the
   file `riftstone encounters` writes), every encounter of it is planned in a scratch copy of the mod (the
   group numbers and refusals a real run gets) and every spawn point found on the mesh in the doors' region;
   only then, and only when all of that holds, is each encounter written as a new enemy group (`encounter.py`:
   the game's own horde setting for more than the spawn points, `rules/horde.nyr`), and Studio makes a new
   mod. Then every spawn point of every written layout is **read back from the mod's files** and found on the
   mesh, in the doors' region.

`rules/beats.nyr` (NYR-Lang, compiled into `src/riftstone/rules/beats.py`):

| Beat | Tiers | Enemies | At once | Spread | Room |
|---|---|---|---|---|---|
| Fight | 1, deeper 1-2 | 3 near the door .. 6 deepest | same | 2.5 m | 1.8 m |
| Ambush | 1-2 | 4 .. 6 | same | 3.5 m | 1.8 m |
| Horde | 1 | 20 .. 40, the game's horde setting | 8 | 3 m | 3.5 m |
| Guardian | 3 | 1 | 1 | 2.5 m | 3 m |
| Boss | 4 | 1 | 1 | 2.5 m | 4 m |

## Encounters now stand on the ground

`riftstone encounter` (and `encounters`, Studio's World tab, the director) used to lay spawn points on flat
rings at the spot's height. Measured over 79 vanilla enemy groups (two per stage with a mesh), **281 of their
790 points (36%) were not on ground reached on foot from the spot** (inside walls, over drops, on another floor). Now,
in a stage with a mesh, a walker's points go onto the mesh: the first on the ground under the spot (or the
nearest within 10 m; farther is refused), the others on the same rings moved onto ground reached from the
first (at most twice the straight distance plus 5 m by the mesh: not behind a wall), with room and 60% of
the spread apart; fewer fit in a tight spot, and the group gets that many (said). The rings stop after 100
(every spread from 40 cm still reaches the 40 m walk; a spread of a hair in a tight spot once searched for
hours). 719 of 719 such points stand on the mesh. Flyers and stages without a mesh keep the rings; `--no-ground` asks for them.

## The proof and the gates

| Check | Result (2026-09-26) |
|---|---|
| `check_corpus --only nav` (Dark Arisen) | 41 of 41 distinct meshes byte-exact; 305,967 links, every cost within 0.1% of the centroids' distance; 305,966 with their reverse; `NAV_OF` equals DDDA.exe's table |
| `check_corpus --game ddo --only nav` | 332 of 332 byte-exact; 1,613,727 links, all within 1% but 96 of one mesh (rm107) |
| `tools/nav_proof.py` | the stage table; 2,840 walker placements on the mesh (median 0 cm, 95% within 35 cm); 277 of 283 doors on it; flat rings vs ground (509 of 790 vs 719 of 719); the director over the 53 stages x 3 seeds: 99 dungeons from the stages' own enemies, 24 more with `--pool game`, 5 stages refused for want of an enemy group to copy, 7 too small for the built-in mission (one fight fits on each); 3,143 of 3,143 spawn points on the mesh in the doors' region |
| unit tests | `test_nav`, `test_wfc`, `test_mission`, `test_dungeon` (a stand-in stage with a corridor, a hole and an island) |
| fuzz | `nav`, `mission`, `wfc` (answers checked against brute force), `dungeon`, `ground` (an encounter's spawn points, hostile spot and spread: on the mesh, reached on foot, apart and with room as the rules say) |

## What stays UNKNOWN, and the limits

- **In game**: nothing here has been played. The placements stand where the game's walkers stand and the
  groups use only the game's mechanisms (`encounter.py`), but how the AI behaves in a copied group whose
  areas come from the nearest vanilla group is UNKNOWN until played -- one reason places stay within 60 m of
  the stage's own enemies. Whether stages whose own groups all wait on lot flags (420-447) behave with a group
  that loads outside them is UNKNOWN as well (`--keep-lot-flags` keeps the game's conditions).
- **Special monsters**: the pool is the game's own walkers, and an encounter copies an ordinary placement of
  its enemy; one with a role of its own in vanilla (Death, which the game places in stages 413 and 445) comes
  without it. `--exclude` them if they misbehave.
- **The open field** (stage 100) has no navigation mesh: its AI uses waypoint cells (`.way`, 309 files, not
  decoded). Encounters there keep the flat rings.
- **Dragon's Dogma Online** spawns come from its server (`ddo.py`); the director is for Dark Arisen. Its
  meshes are read and rebuilt all the same.
- **Mesh edits**: `nav.build` writes what `nav.parse` read (and refuses anything the loader would trust wrongly);
  generating a new mesh for changed geometry is not built -- the game's own tree layout (depth 7, its bounds)
  is not derived.

## Against the brief it came from

The 2026-09-26 research brief asked for "differentiable WaveFunctionCollapse and graph grammar level synthesis"
with a guarantee that start and boss are connected, written `π1(Start→Boss) ≠ ∅`. What was built, and why:

- **WFC**: the exact form -- constraint solving with WFC's order plus backtracking -- not a differentiable
  relaxation, because only the exact form can return "satisfied" or "impossible" as a fact.
- **The connection guarantee** is a question of path components, not of the fundamental group (π1 is about
  loops at one point). It is answered by the mesh's own links: every place lies in the doors' region, and every
  written spawn point is re-read and located there.
- **Graph grammars**: Dormans' separation of mission and space, with the space fixed (a stage's geometry is
  not generated) and the mission a grammar of beats.
- **Brand-new geometry** (rooms, corridors, collision, new stages) is out of reach of a data-side tool today;
  see "What it does not do" above. `docs/research-audit-0926.md` covers the brief's other items.

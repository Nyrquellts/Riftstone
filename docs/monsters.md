# Monsters between the games: which Online enemy is which Dark Arisen enemy, and what converts

Dragon's Dogma Online (DDO) grew out of Dark Arisen (DDDA): many of its enemies are DDDA's enemies with new
textures, and many are not. This page says which, measured on both installed games (2026-09-25:
DDDA Steam build 2364871, DDO client 03.04.003), and how `riftstone monster` turns one into the other where
the bodies match. Everything here is static: read from the games' files. **How a converted monster looks,
moves and fights in game is UNKNOWN until someone plays it.**

Code: `src/riftstone/monsters.py` (census, verdicts, conversion), `riftstone monster` (`cli.py`),
`tools/monster_census.py` (the full report). The conversion itself is `port.into_mod`
(`docs/ONBOARDING.md`, "Between the games: riftstone port").

## What is measured

| | Dark Arisen | Online |
|---|---|---|
| Enemy archives | `rom/enemy/emNNNN.arc` (97) | `rom/EM/EMxxxxxx.arc` (548) |
| Models | `model\em\eNN\eNNMM\...` | `obj\em\em01NNMM\model\...` and `...\model_org\...` |
| Motion lists | `motion\em\eNN\<body>_co` ... | `obj\em\<folder>\motion\<body>_co` ... |
| Names | `enemy_name_eng` (labels carry the ids; world.enemy_names) | `param\enemy_group.emg` + `enemy_name.gmd` (ddo.client_enemy_names) |

- **Body.** An archive's body is its model with the most joints. The body's folder is its **family**
  (`e0200`, `em015200`), so DDO's variants group under their base: EM015200..EM015204 (Chimera, Gorechimera,
  White, Shadow and Blaze Chimera) and EM070800..070820 all wear `em015200`. Archive ids are not always model
  ids: DDDA's `em2000.arc` holds `e0300` (Skeletons), `em5500C.arc` holds `e5503_00` (Maneaters).
- **Joints**, by joint id (what motion tracks address), with parent and offset (`port.skeleton`).
- **Bound joints.** The joints a model's meshes are bound to: every envelope (the 144-byte bone volumes after
  the mesh records, `numEnvelopes` per mesh) names one. This is what decides whether another game's motions
  can move the model: a bound joint the destination's rig lacks is never moved.
- **Motion lists**: the joint ids the family's own `<body>_<xx>.lmt` drive (read from the track arrays).
- **Vertex formats**: each mesh's format against the set the destination game uses (port's rule; 342 of
  ~113,000 DDO meshes use a format DDDA never does).
- **Texture sheets.** Riftstone does not decode vertex attributes, so whether the UV layouts agree is measured
  on the textures: every normal map (else albedo map) the body's materials bind -- the base material and the
  ones made for the same model, e.g. DDDA's full-detail `e5200_a` -- is paired with the other game's map of
  the same sheet (`em015200_skin_NM` with `e5200_skin_NM`), decoded at 64 px and compared as a mean absolute
  difference (0..255), with the same comparison against the mirrored picture as the baseline. **Line up**:
  at most 16 and at most a third of the mirrored difference; **differ**: more than half of it; otherwise
  inconclusive (little structure). Normal maps carry the geometry, so a different UV layout cannot hide there.
  The chimera: eye 0.0 against 66.2 mirrored, face 6.5 / 49.4, snake-and-goat 9.2 / 43.7, body 4.9 / 50.2.

## The verdict

A pair -- a source body put in place of a destination body -- gets the first verdict that holds:

| Verdict | Rule | What it means |
|---|---|---|
| none | fewer than 4 bound joints (a rigid prop), or fewer than half of them in the destination's rig with the same parent | no counterpart |
| partial | a bound joint missing or on another parent there, or a vertex / texture format the destination never uses | each difference is listed |
| same skeleton | every bound joint there with the same parent; some offset differs by more than 0.5 cm | the model converts; its proportions are not the rig's the motions were made for |
| same body | every bound joint there with the same parent and offset | the model, its rebuilt materials and textures convert; the destination's motions move it as their own |

Each family's **counterpart** in the other game is the family with the best verdict; among equal verdicts the
one with the same name (DDDA names its enemies in the plural, DDO in the singular), then the most bound joints
in place, then the closest joint set. When a family with the same name is not the counterpart it is listed as
the **namesake**. The verdict has a direction; "Back" in the matrix is the other direction, when the
counterpart's own counterpart is this family.

## Results (2026-09-25)

| | Online -> Dark Arisen | Dark Arisen -> Online |
|---|---|---|
| Enemy archives | 548 (79 hold no enemy body) | 97 (8 hold no enemy body) |
| Families | 203 | 65 |
| same body | 72 | 38 |
| same skeleton | 26 | 3 |
| partial | 88 | 22 |
| none | 17 | 2 |
| Texture sheets of the counterpart pairs | 54 line up, 65 differ, 5 inconclusive, 62 not compared | 35 line up, 12 differ, 16 not compared |

The census reads both games in about 6 s, 25-40 s with the texture check (`tools/monster_census.py`).

**Same body both ways** (each is the other's counterpart; 23 pairs): Wolf / Wolves, Direwolf / Direwolves,
Valefar / Garm, Skeleton Knight, Living Armor, Skeleton Brute, Skeleton Mage, Skeleton Sorcerer, Saurian,
Pyre Saurian, both Undead, Stout Undead, Eliminator, Armored Insect / Leapworms, Vile Eye, Wight, Rabbit,
em018100 / Giant Bats, Ox, Giant Rat / Rats, em018700 / Snakes, Spider. All but three have texture sheets that
line up (the Wight's differ; the insect's and em018700's have no pair to compare).

What else the numbers say:

- **Chimera: partial, and a skin instead.** DDO draws the chimera as one model (72 joints, 71 bound); DDDA as
  three units: the body `e5200` and the separate goat head `e5200_00` and snake tail `e5200_01`, each with
  its own motions. 49 of the DDO model's bound joints are DDDA's body joints with the same parent and offset;
  the other 22 are exactly the joints of DDDA's goat (9) and snake (13) part models. The other way, DDDA's
  body binds two joints DDO's model lacks (130, 131). The sheets line up (above), so DDO's variants become
  per-placement skins of DDDA's chimera (`--as-skin`, `docs/enemy-skins.md`). Manticore, Goremanticore and
  Abaddon have more joints still, and other sheets.
- **Many rigs are shared.** DDO's goblins, hobgoblins, grim goblins and redcaps all fit DDDA's goblin rig
  (`e0100`, same body); DDDA's goblin binds five joints DDO's lacks (54, 58, 62, 64, 136), so the way back is
  partial. Pixies add two joints. Humanoid undead and skeletons share a few rigs: DDDA's plain Skeletons fit
  DDO's Mudman rig exactly, while DDO's own Skeleton lacks joints they bind (the namesake, partial).
- **Same rig, other proportions.** Orcs are DDDA's cyclops rig with 35 joints moved (up to 111 cm): same
  skeleton, different creatures; Zuhl, Ifrit and the Daimon share another; Griffin, Buck and the boars match
  their counterparts with a few joints moved.
- **Different bodies.** DDO's Evil Eye and Volt Eye bind 136 joints (DDDA's Evil Eye 38): none. The dragons
  (The White Dragon, Elder Dragon, Golgorran, Ushumgal, Black Dragon) share most joint ids with DDDA's Dragon,
  but 41-54 of their bound joints hang off other parents and 113-114 sit elsewhere (up to 858 cm): partial.
  The Spirit Dragon: none. DDDA's hydras: none.
- **No enemy body.** 76 of DDO's archives without one are human enemies (EM0110xx: the Rogue, Banded, Mergan,
  Pawn, Phindymian and Scarlet vocations, Leo, Iris ...), built from equipment like the player; the others are
  Unspeakable Meat (a one-joint prop) and an invisible aggro enemy. DDDA's Phantoms and Wraiths have no model
  with two or more joints in their archives.

The full matrix is at the end of this page.

## Converting

```bat
Riftstone.cmd monster list [--game ddo|ddda] [--verdict "same body"] [--textures] [--json]
Riftstone.cmd monster show "White Chimera"             :: a family, its counterpart, the pair joint by joint
Riftstone.cmd new "mods\Online Wolf"
Riftstone.cmd monster convert Wolf --into wolves --mod "mods\Online Wolf" [--dry-run]
Riftstone.cmd new "mods\Arisen Wolf" --game ddo
Riftstone.cmd monster convert wolves --into Wolf --mod "mods\Arisen Wolf"
Riftstone.cmd monster convert "White Chimera" --into chimera --as-skin 7 --mod "mods\DDO Chimeras"
```

`<source>` is an enemy of one game by name or id (`White Chimera`, `EM015202`, `0x015202`, `em2000`,
`e0300`); `--into` an enemy of the other game. The mod's game is the destination. `list` reads a cached census
(`%LOCALAPPDATA%\Riftstone\monsters-*.json`, rebuilt when either game's archives change).

`convert` is allowed for **same body**, and for **same skeleton** only when the texture sheets line up;
otherwise it refuses with the census's reasons. It writes, through `port.into_mod`:

- the source's model over the destination's (revision 212 <-> 210, vertex formats checked);
- the destination's material rebuilt for the source model's material names, each texture binding re-pointed
  to the source's map in the same shader slot; slots the source does not fill keep the destination's maps
  (which is why the sheets must line up for a same-skeleton pair);
- every other material made for the same model (one for each of its material names) rebuilt the same way:
  DDDA's full-detail `<model>_a` that the game puts on the unit by path (the chimera's `e5200_a`) and variants'
  own (the Hobgoblin's `e0101_a` over the goblin model);
- the source's textures, converted (0x99 <-> 0x9D, attr1), under `ddo\` or `ddda\` in every archive that holds
  those materials. A DDO variant (the Warg, the White Chimera) brings its own material and maps.

What stays the destination's: its motions, AI, collision, hit shapes, sounds, effects and parameters, and its
separate part models. Every enemy that wears the same model file changes with it (the plan lists them): DDDA's
goblin model sits in `em0100` (Goblins), `em0103` (Greater Goblins) and two event archives, and the
Hobgoblins' and Grimgoblins' own full-detail materials (`e0101_a`, `e0102_a`) are made for it.

`--as-skin N` (chimera only) writes skin `sNN` instead, through `skins.py` and `ddoskins.py` (which reads DDO
through Riftstone's own ARCC reader, the `ddon` toolkit as a fallback). Only placements marked with that skin
change, and it needs the `enemy_skins` plugin. Every conversion records its recipe in the mod
(`riftstone-sources.json`): a package carries the recipe, never the converted files (`docs/legal.md`).

### Converted and built (2026-09-25, scratch mods, not installed)

| Conversion | Verdict | Written | Build |
|---|---|---|---|
| DDO Wolf (EM010200) -> DDDA Wolves (em0200) | same body, sheets line up (body NM 5.6 / 40.3) | model, material, 4 textures | 1 archive from 6 changes, all verified |
| DDDA Wolves -> DDO Wolf (a DDO mod) | same body | model, material, 5 textures | `EM010200.arc` (ARCC) from 7 changes, all verified |
| DDO Goblin -> DDDA Goblins | same body, sheets line up | model, `e0100`, `e0100_a`, `e0101_a`, `e0102_a`, 12 textures | 4 archives from 53 changes, all verified |
| DDO White Chimera -> DDDA chimera skin 7 | partial as a model; sheets line up | 4 albedo maps, 3 materials | 1 archive from 7 changes, all verified |

In each built archive every texture a changed material binds comes before it (the order the game needs,
`AGENTS.md`), and each model, material and texture parses as the destination game's.

## What cannot convert, and why

- **A new enemy class.** Adding a DDO enemy to DDDA as an enemy of its own -- its own archive, loaded by
  tag, with its own AI -- needs an archive tag (a plugin: the tag table is fixed in DDDA.exe,
  `docs/archive-tags.md`) and an enemy class and state machine to drive it. Out of reach here; a conversion
  always takes the place of an existing enemy.
- **Partial and none.** Joints the destination's rig does not have would need rigging in Blender (weights moved
  onto the rig's joints, or motions made for the new joints); a different part split (the chimera) needs the
  model cut into the destination's units. A vertex format the destination never uses has no shader input
  Riftstone can vouch for.
- **Same skeleton with other sheets.** The model converts, but the destination's maps it keeps (damage masks,
  full-detail maps) would land on the wrong parts.

## UNKNOWN until played

- How any converted monster looks and moves: bind pose against the destination's motions (offsets equal is
  measured; the engine's skinning of a foreign model is not observed), the kept destination maps, lighting of
  the other game's material classes rebuilt from a template.
- Whether the game puts a full-detail material on units other than the chimera by path (the `_a` materials are
  rebuilt either way), and what it does with a material name it does not find.
- Hit shapes and attack timing stay the destination's; whether they match the source's silhouette.
- DDO variants whose look comes from shader parameters (curse, embers, dye masks) show their base maps in
  DDDA (the Shadow and Blaze skins bake an approximation, `docs/enemy-skins.md`; equipment's colour masks
  bake exactly by Online's own formula, `docs/ddo-dye.md`).

## Proof

- `tests/test_monsters.py` (23 tests) on stand-in games of both kinds (`tests/monster_fixture.py`: rigged
  models with envelopes, materials, patterned maps, motion lists, both name tables, an ARCC Online): the
  measurements, every verdict, the texture check, the census and its cache, what people type (a refusal for
  an enemy without a body, one name for two families, an archive id that is no enemy id), converting both ways
  and building the mod, a variant's own material, the rebuilt `_a` material, the same-skeleton rule both ways,
  the chimera skin and its refusals, the command line.
- Fuzz target `monster` (`fuzz/targets.py`): a model (facts consistent; a model fits itself), a motion list
  (equal to the parsed list's bones), what people type (an answer is a family of that game; every enemy's own
  id and name find it), two rigs (the verdict's joints split the bound joints; each verdict keeps its rule).
  Two 120 s runs (16.9 M and 10.7 M executions): 0 findings. `port` (two runs), `ddo_names` and `skintex`,
  whose code this touched, 120 s each: 0 findings. The first seed run found one refusal the invariant does
  not allow (an archive id such as `em5500C` did not find its own enemy); fixed, with a regression test.
- `tools/monster_census.py` repeats every number on this page.

## The matrix

Derived facts only: family (its own enemy's name, +N other names), enemy ids, joints (bound), counterpart,
verdict, the verdict back, the texture sheets, what differs (missing / re-parented joint ids; joints moved).

### Dragon's Dogma Online -> Dragon's Dogma: Dark Arisen

| Family | Enemy ids | Joints (bound) | Counterpart | Verdict | Back | Textures | What differs |
|---|---|---|---|---|---|---|---|
| em010100 Goblin +4 | EM010100, EM010101, EM010102 +14 | 25 (22) | e0100 Goblins | same body | partial | line up | - |
| em010110 Hobgoblin +9 | EM010110, EM010111, EM010112 +10 | 25 (22) | e0100 Goblins | same body | - | line up | - |
| em010120 Grim Goblin +6 | EM010120, EM010121, EM010123 +5 | 25 (22) | e0100 Goblins | same body | - | differ | - |
| em010150 Pixie +3 | EM010150, EM010151, EM010152 +1 | 27 (24) | e0100 Goblins | partial | - | differ | missing 100, 105 |
| em010155 Pixie Jabber | EM010155 | 27 (24) | e0100 Goblins | partial | - | differ | missing 100, 105 |
| em010160 Infected Hobgoblin +2 | EM010160, EM010161, EM010162 | 25 (23) | e0100 Goblins | same body | - | line up | - |
| em010170 Severely Infected Pixie | EM010170, EM010171, EM010172 | 27 (24) | e0100 Goblins | partial | - | differ | missing 100, 105 |
| em010190 High Pixie Biff +3 | EM010190, EM010191, EM010192 +1 | 27 (24) | e0100 Goblins | partial | - | differ | missing 100, 105 |
| em010200 Wolf +3 | EM010200, EM010203, EM010206 +1 | 32 (31) | e0200 Wolves | same body | same body | line up | - |
| em010201 Direwolf +1 | EM010201, EM010202 | 32 (31) | e0201 Direwolves | same body | same body | line up | - |
| em010204 Grimwarg +1 | EM010205, EM010211 | 40 (39) | e0204 Garm | same body | - | line up | - |
| em010207 Alchemized Wolf +1 | EM010207, EM010208 | 32 (31) | e0203 Wargs | same body | - | differ | - |
| em010209 Infected Direwolf | EM010209 | 32 (31) | e0203 Wargs | same body | - | differ | - |
| em010210 Green Guardian | EM010210 | 40 (40) | e0204 Garm | same body | - | line up | - |
| em010220 Severely Infected Warg | EM010220 | 32 (31) | e0203 Wargs | same body | - | inconclusive | - |
| em010221 Skeleton Warg | EM010221 | 32 (29) | e0204 Garm | partial | - | differ | missing 210; re-parented 145 |
| em010230 War-Ready Grimwarg | EM010230 | 43 (43) | e0204 Garm | partial | - | line up | missing 210-212 |
| em010240 Valefar | EM010240 | 40 (40) | e0204 Garm | same body | same body | line up | - |
| em010300 Skeleton | EM010300 | 23 (20) | e0300 Skeletons | same body | - | line up | - |
| em010301 Skeleton Knight +1 | EM010301, EM010302 | 25 (24) | e0301 Skeleton Knights | same body | same body | line up | - |
| em010303 Skeleton Lord (Abyss) | EM010303 | 31 (30) | e0801 Skeleton Sorcerers | partial | - | differ | missing 210-215 |
| em010306 Living Armor | EM010306 | 25 (24) | e0305 Living Armor | same body | same body | line up | - |
| em010307 Skeleton Brute | EM010307 | 23 (20) | e0306 Skeleton Brutes | same body | same body | line up | - |
| em010308 Skeleton Mage | EM010308, EM075120, EM075121 | 25 (24) | e0800 Skeleton Mages | same body | same body | line up | - |
| em010309 Skeleton Sorcerer | EM010309, EM075130, EM075131 | 25 (24) | e0801 Skeleton Sorcerers | same body | same body | line up | - |
| em010310 Death Knight | EM010310 | 33 (28) | e0507 Eliminators | partial | - | not compared | missing 73-74; re-parented 68, 70; 1 moved (up to 40 cm) |
| em010311 Ghost Mail | EM010311 | 25 (22) | e0801 Skeleton Sorcerers | same body | - | not compared | - |
| em010312 Alchemized Skeleton | EM010312 | 25 (20) | e0801 Skeleton Sorcerers | same body | - | not compared | - |
| em010313 Skull Lord | EM010313 | 31 (30) | e0801 Skeleton Sorcerers | partial | - | line up | missing 210-215 |
| em010314 Flame Skeleton +4 | EM010314, EM010315, EM010316 +2 | 23 (20) | e0801 Skeleton Sorcerers | same body | - | line up | - |
| em010320 Flame Skeleton Brute +4 | EM010320, EM010321, EM010322 +2 | 23 (20) | e0801 Skeleton Sorcerers | same body | - | line up | - |
| em010400 Saurian +7 | EM010400, EM010401, EM010410 +5 | 40 (37) | e0400 Saurians | same body | same body | line up | - |
| em010440 Pyre Saurian | EM010440 | 40 (33) | e0404 Pyre Saurians | same body | same body | line up | - |
| em010450 Rock Saurian +1 | EM010450, EM010451 | 40 (37) | e0404 Pyre Saurians | same body | - | differ | - |
| em010460 Blue Newt +1 | EM010460, EM010461 | 40 (33) | e0404 Pyre Saurians | same body | - | line up | - |
| em010470 War-Ready Saurian +1 | EM010470, EM010471 | 45 (41) | e0404 Pyre Saurians | partial | - | differ | missing 220-221, 223 |
| em010480 Merman | EM010480 | 40 (33) | e0404 Pyre Saurians | same skeleton | - | differ | 1 moved (up to 10 cm) |
| em010482 Poison Merman | EM010482 | 42 (34) | e0404 Pyre Saurians | partial | - | differ | missing 146; 1 moved (up to 10 cm) |
| em010500 Undead | EM010500 | 27 (24) | e0500 Undead | same body | same body | line up | - |
| em010501 Undead | EM010501 | 25 (24) | e0501 Undead | same body | same body | line up | - |
| em010502 Stout Undead | EM010502 | 25 (24) | e0502 Stout Undead | same body | same body | line up | - |
| em010503 Sword Undead +1 | EM010503, EM010504 | 25 (24) | e0801 Skeleton Sorcerers | same body | - | not compared | - |
| em010508 Eliminator +1 | EM010508, EM010530 | 36 (35) | e0507 Eliminators | same body | same body | line up | - |
| em010509 Mudman +1 | EM010509, EM010510 | 25 (25) | e0801 Skeleton Sorcerers | same body | - | not compared | - |
| em010511 Frost Corpse Punisher +1 | EM010511, EM010512 | 27 (25) | e0801 Skeleton Sorcerers | partial | - | not compared | missing 150 |
| em010513 Flame Corpse Punisher +7 | EM010513, EM010514, EM010515 +5 | 27 (25) | e0801 Skeleton Sorcerers | partial | - | not compared | missing 150 |
| em010600 Harpy +3 | EM010600, EM010601, EM010607 +2 | 40 (38) | e0600 Harpies | same body | partial | line up | - |
| em010603 Gargoyle | EM010603 | 40 (38) | e0604 Strigoi | same body | - | inconclusive | - |
| em010605 Siren | EM010605 | 36 (32) | e0605 Sirens | same body | partial | not compared | - |
| em010606 Alchemized Harpy | EM010606 | 40 (38) | e0600 Harpies | same body | - | line up | - |
| em010610 Strix +1 | EM010610, EM010611 | 45 (43) | e0600 Harpies | partial | - | differ | missing 230-234 |
| em010612 Infected Snow Harpy | EM010612 | 39 (38) | e0600 Harpies | same body | - | inconclusive | - |
| em010614 Severely Infected Stymphalídes | EM010614 | 45 (43) | e0600 Harpies | partial | - | differ | missing 230-234 |
| em010621 Ukobach | EM010621 | 40 (38) | e0604 Strigoi | same body | partial | differ | - |
| em010800 Worm | EM010800 | 8 (7) | e9100 Leapworms | same body | - | line up | - |
| em010810 Leech | EM010810 | 8 (7) | e9100 Leapworms | same body | - | differ | - |
| em010820 Armored Insect | EM010820, EM010821, EM010822 +1 | 8 (7) | e9100 Leapworms | same body | same body | not compared | - |
| em010900 Slime +10 | EM010900, EM010901, EM010902 +8 | 15 (14) | e5502 Gazers | partial | - | not compared | missing 14; re-parented 4-6; 10 moved (up to 169.64 cm); vertex formats 0x64593023; texture formats 14 |
| em010910 Blob +2 | EM010910, EM010911, EM010915 | 15 (14) | e5502 Gazers | partial | - | not compared | missing 14; re-parented 4-6; 10 moved (up to 169.64 cm); vertex formats 0x64593023; texture formats 14 |
| em011100 Forest Goblin +2 | EM011100, EM011101, EM011102 | 23 (22) | e0100 Goblins | same body | - | differ | - |
| em011110 Redcap +2 | EM011110, EM011111, EM011112 | 25 (22) | e0100 Goblins | same body | - | line up | - |
| em011120 Alchemized Goblin +5 | EM011120, EM011121, EM011122 +3 | 25 (22) | e0100 Goblins | same body | - | differ | - |
| em011140 Goblin King | EM011140 | 26 (24) | e0100 Goblins | partial | - | not compared | missing 150; 1 moved (up to 5.5 cm) |
| em011150 Goblin Bomber | EM011150 | 25 (22) | e0100 Goblins | same body | - | line up | - |
| em011200 Killer Bee | EM011200 | 23 (22) | e8300 Oxen | partial | - | not compared | missing 68-71, 100, 105; re-parented 4, 6, 10; 12 moved (up to 77 cm) |
| em011201 Albe | EM011201, EM080801 | 23 (22) | e8300 Oxen | partial | - | not compared | missing 68-71, 100, 105; re-parented 4, 6, 10; 12 moved (up to 77 cm) |
| em011210 Moth | EM011210, EM011211 | 23 (21) | e8300 Oxen | partial | - | not compared | missing 68-71, 100, 105; re-parented 4, 6, 10; 11 moved (up to 120 cm) |
| em011300 Mandragora | EM011300, EM011301 | 16 (14) | e8000 Rabbits | partial | - | not compared | missing 138-139, 210; 9 moved (up to 17.69 cm) |
| em011302 Habanero | EM011302 | 16 (14) | e8000 Rabbits | partial | - | not compared | missing 138-139, 210; 9 moved (up to 17.69 cm) |
| em011400 Maneater | EM011400, EM011401 | 14 (14) | e5503 Maneaters | partial | same body | not compared | missing 12-13 |
| em011410 Tentacle | EM011410, EM011411 | 12 (12) | e5503 Maneaters | same body | - | line up | - |
| em011412 Tentacle | EM011412, EM011413 | 12 (12) | e5503 Maneaters | same body | - | inconclusive | - |
| em011500 Foot-Biter | EM011500 | 40 (37) | e0404 Pyre Saurians | same body | - | differ | - |
| em015000 Cyclops +1 | EM015000, EM015001, EM015002 +7 | 41 (40) | e5000 Cyclopes | same body | partial | line up | - |
| em015010 Gorecyclops | EM015010, EM070050, EM070051 | 39 (38) | e5001 Gorecyclopes | same body | partial | line up | - |
| em015012 Infected Gorecyclops +1 | EM015012, EM015017 | 44 (43) | e5000 Cyclopes | partial | - | differ | missing 68-72; vertex formats 0x64593023, 0xb392101f, 0xd877801b; texture formats 14 |
| em015020 Colossus | EM015020, EM070041, EM070042 | 41 (40) | e5000 Cyclopes | same skeleton | - | differ | 2 moved (up to 217.29 cm) |
| em015030 Grand Ent +1 | EM015030, EM015031, EM070510 +1 | 47 (37) | e5000 Cyclopes | same skeleton | - | not compared | 1 moved (up to 31.99 cm) |
| em015032 Phindymian Ent | EM015032, EM070520 | 47 (37) | e5000 Cyclopes | same skeleton | - | not compared | 1 moved (up to 31.99 cm) |
| em015033 Burned Ent | EM015033 | 47 (37) | e5000 Cyclopes | same body | - | not compared | - |
| em015040 Troll +2 | EM015040, EM015041, EM015042 +1 | 41 (39) | e5000 Cyclopes | partial | - | differ | re-parented 233; 1 moved (up to 247.29 cm) |
| em015050 Skeleton Cyclops | EM015050, EM015051 | 42 (37) | e5000 Cyclopes | partial | - | not compared | missing 240; re-parented 145 |
| em015052 Frost Skeleton Cyclops +2 | EM015052, EM015053, EM015054 | 42 (37) | e5000 Cyclopes | partial | - | not compared | missing 240; re-parented 145 |
| em015060 War-Ready Gorecyclops | EM015060, EM015061 | 45 (42) | e5001 Gorecyclopes | partial | - | line up | re-parented 234, 238; 2 moved (up to 108.2 cm) |
| em015100 Golem +1 | EM015100, EM015104 | 54 (45) | e5100 Golems | same body | partial | line up | - |
| em015102 Goliath | EM015102 | 59 (39) | e5100 Golems | same body | - | not compared | - |
| em015103 Damned Golem | EM015103, EM015105 | 59 (39) | e5100 Golems | same body | - | not compared | - |
| em015200 Chimera +4 | EM015200, EM015201, EM015202 +5 | 72 (71) | e5200 Chimeras | partial | partial | line up | missing 94-101, 146-147, 156-160, 163-164, 212-216 (22 in its part models e5200_00, e5200_01) |
| em015210 Manticore | EM015210, EM070830 | 74 (70) | e5200 Chimeras | partial | - | differ | missing 75, 79, 94-101, 110-119, 210 (8 in its part models e5200_01) |
| em015211 Goremanticore | EM015211 | 74 (70) | e5200 Chimeras | partial | - | differ | missing 75, 79, 94-101, 110-119, 210 (8 in its part models e5200_01) |
| em015220 War-Ready Goremanticore | EM015220 | 82 (76) | e5200 Chimeras | partial | - | differ | missing 75, 79, 94-101, 110-122, 124-126, 210 (8 in its part models e5200_01) |
| em015230 Abaddon | EM015230, EM080800 | 74 (70) | e5200 Chimeras | partial | - | differ | missing 75, 79, 94-101, 110-119, 210 (8 in its part models e5200_01) |
| em015300 Griffin +2 | EM015300, EM015303, EM015320 +3 | 137 (135) | e5400 Griffins | same skeleton | same skeleton | line up | 2 moved (up to 20.86 cm) |
| em015301 Cockatrice +1 | EM015301, EM015321, EM070640 +1 | 140 (134) | e5401 Cockatrices | partial | same body | line up | missing 213, 215 |
| em015302 Sphinx +1 | EM015302, EM015305, EM070700 | 139 (134) | e5401 Cockatrices | partial | - | differ | missing 66, 68, 136-137; 3 moved (up to 24 cm) |
| em015304 Alchemized Griffin | EM015304 | 137 (135) | e5400 Griffins | same skeleton | - | line up | 2 moved (up to 20.86 cm) |
| em015306 Infected Griffin +1 | EM015306, EM015310 | 145 (143) | e5400 Griffins | partial | - | differ | missing 68-71, 74-75, 156, 159; 2 moved (up to 20.86 cm); vertex formats 0x64593023, 0xb392101f; texture formats 14 |
| em015330 War-Ready Nightmare | EM015330 | 152 (142) | e5401 Cockatrices | partial | - | differ | missing 66, 68, 136-137, 234-236, 238-239, 241, 243, 245; 3 moved (up to 24 cm) |
| em015400 Evil Eye | EM015400, EM080700 | 136 (136) | - (closest e5500); namesake e5500 none | none | partial | - | missing 60-67, 70-116, 120-126, 130-136, 150-156, 160-166, 170-176, 180-186, 190-196, 200-206; 25 of 136 bound joints fit |
| em015401 Vile Eye | EM015401 | 13 (13) | e5501 Vile Eyes | same body | same body | line up | - |
| em015406 Volt Eye | EM015406 | 136 (136) | - (closest e5502) | none | same body | - | missing 60-67, 70-116, 120-126, 130-136, 150-156, 160-166, 170-176, 180-186, 190-196, 200-206; 25 of 136 bound joints fit |
| em015410 Crystal Eye +3 | EM015410, EM015411, EM015412 +1 | 13 (13) | e5501 Vile Eyes | same body | - | line up | - |
| em015420 Alchemy Eye | EM015420 | 13 (13) | e5501 Vile Eyes | same body | - | differ | - |
| em015500 Ogre +2 | EM015500, EM015503, EM015505 +1 | 51 (50) | e0900 Ogres | same body | partial | line up | - |
| em015502 Dread Ape +1 | EM015502, EM015504 | 51 (50) | e0901 Elder Ogres | same body | - | differ | - |
| em015506 Spineback +1 | EM015506, EM015507, EM070210 +1 | 51 (48) | e0901 Elder Ogres | same body | - | line up | - |
| em015508 Cragger +1 | EM015508, EM015509 | 51 (50) | e0901 Elder Ogres | same body | partial | differ | - |
| em015510 War-Ready Ogre | EM015510 | 55 (53) | e0901 Elder Ogres | partial | - | line up | missing 234-236 |
| em015600 Wight | EM015600, EM075700 | 25 (23) | e5700 Wights | same body | same body | differ | - |
| em015603 Death | EM015603 | 29 (24) | e5703 Death | partial | same body | line up | missing 156-159 |
| em015604 Witch | EM015604 | 36 (23) | e5703 Death | partial | - | not compared | missing 210-211 |
| em015605 Empress Ghost | EM015605 | 36 (30) | e5703 Death | partial | - | not compared | missing 210-215, 217-219 |
| em015610 Medusa | EM015610 | 55 (42) | e5801 The Ur-Dragon | partial | - | not compared | re-parented 4, 71-72, 74-75, 77-80, 90, 94, 98, 100, 102, 106, 210-211; 23 moved (up to 972.37 cm) |
| em015611 Gorgon | EM015611 | 55 (42) | e5801 The Ur-Dragon | partial | - | not compared | re-parented 4, 71-72, 74-75, 77-80, 90, 94, 98, 100, 102, 106, 210-211; 23 moved (up to 972.37 cm) |
| em015620 Ghost | EM015620 | 25 (17) | e5703 Death | same body | - | not compared | - |
| em015621 Misery Ghost | EM015621 | 25 (17) | e5703 Death | same body | - | not compared | - |
| em015622 Grudge Ghost | EM015622 | 25 (17) | e5703 Death | same body | - | not compared | - |
| em015623 Rage Ghost | EM015623 | 25 (17) | e5703 Death | same body | - | not compared | - |
| em015700 Drake +3 | EM015700, EM015701, EM015710 +3 | 129 (111) | e5900 Drakes | partial | partial | line up | re-parented 35, 51 |
| em015706 Cursed Dragon | EM015706 | 124 (106) | e5906 Cursed Dragons | partial | partial | line up | re-parented 35, 51 |
| em015707 Lindwurm | EM015707, EM070940 | 125 (104) | e5900 Drakes | partial | - | differ | re-parented 35, 51; 26 moved (up to 284.75 cm) |
| em015708 Angules | EM015708, EM070920 | 129 (111) | e5900 Drakes | partial | - | inconclusive | re-parented 35, 51 |
| em015709 Behemoth | EM015709, EM015740, EM070930 +1 | 125 (61) | e5900 Drakes | partial | - | differ | re-parented 35, 51 |
| em015712 Infected Behemoth +1 | EM015712, EM015717 | 134 (67) | e5900 Drakes | partial | - | differ | missing 72-73; re-parented 35, 51, 68, 71, 74, 76; vertex formats 0x64593023, 0xb392101f, 0xd877801b; texture formats 14 |
| em015718 Lotus Fin | EM015718 | 125 (104) | e5900 Drakes | partial | - | differ | re-parented 35, 51; 26 moved (up to 284.75 cm) |
| em015719 Dagon | EM015719 | 125 (104) | e5900 Drakes | partial | - | differ | re-parented 35, 51; 26 moved (up to 284.75 cm) |
| em015720 Tarasque +2 | EM015720, EM015721, EM015723 +2 | 104 (70) | e5900 Drakes | partial | - | differ | re-parented 35, 51, 68-71 |
| em015730 Catoblepas | EM015730 | 85 (43) | e5900 Drakes | partial | - | differ | missing 231-232; re-parented 35, 51, 244-246 |
| em015800 Orc Soldier +3 | EM015800, EM015801, EM015802 +5 | 40 (37) | e5000 Cyclopes | same skeleton | - | not compared | 35 moved (up to 110.8 cm) |
| em015810 Orc Battler +2 | EM015810, EM015811, EM015812 | 40 (38) | e5000 Cyclopes | same skeleton | - | not compared | 36 moved (up to 261 cm) |
| em015813 Infected Orc Soldier +2 | EM015813, EM015814, EM015815 | 40 (37) | e5000 Cyclopes | partial | - | not compared | 35 moved (up to 110.8 cm); vertex formats 0x64593023, 0xb392101f, 0xd877801b; texture formats 14 |
| em015820 Captain Orc | EM015820, EM085004 | 49 (43) | e5000 Cyclopes | partial | - | not compared | missing 216-217; re-parented 210-211, 213-214; 35 moved (up to 110.8 cm) |
| em015821 Sword Soldier Dwarf Orc | EM015821 | 40 (37) | e5000 Cyclopes | same skeleton | - | not compared | 35 moved (up to 110.8 cm) |
| em015822 Blunt Soldier Dwarf Orc | EM015822 | 40 (37) | e5000 Cyclopes | same skeleton | - | not compared | 35 moved (up to 110.8 cm) |
| em015823 Heavy Soldier Dwarf Orc | EM015823 | 40 (37) | e5000 Cyclopes | same skeleton | - | not compared | 35 moved (up to 110.8 cm) |
| em015824 Ranged Soldier Dwarf Orc | EM015824 | 40 (38) | e5000 Cyclopes | same skeleton | - | not compared | 36 moved (up to 261 cm) |
| em015825 Dwarf Orc | EM015825 | 40 (37) | e5000 Cyclopes | same skeleton | - | not compared | 35 moved (up to 110.8 cm) |
| em015826 Squad Leader Dwarf Orc | EM015826 | 46 (42) | e5000 Cyclopes | partial | - | not compared | re-parented 210-214; 35 moved (up to 110.8 cm) |
| em015830 General Orc | EM015830 | 49 (42) | e5000 Cyclopes | partial | - | not compared | missing 216-217; re-parented 211, 213-214; 35 moved (up to 110.8 cm) |
| em015831 Ancestor Orc +1 | EM015831, EM015832, EM015833 +1 | 40 (37) | e5000 Cyclopes | same skeleton | - | not compared | 35 moved (up to 110.8 cm) |
| em015835 Legion +1 | EM015835, EM015836, EM015837 +1 | 43 (39) | e5000 Cyclopes | partial | - | not compared | missing 220-221; 35 moved (up to 110.8 cm) |
| em015840 Mogok | EM015840 | 40 (38) | e5000 Cyclopes | same skeleton | - | not compared | 36 moved (up to 261 cm) |
| em015850 Gigant Machina +3 | EM015850, EM015851, EM071400 +2 | 40 (31) | e0901 Elder Ogres | same skeleton | - | not compared | 29 moved (up to 49.06 cm) |
| em015852 Bolt Machina +1 | EM015852, EM095803 | 40 (31) | e0901 Elder Ogres | same skeleton | - | not compared | 29 moved (up to 49.06 cm) |
| em015860 War Master | EM015860, EM071251 | 49 (43) | e5000 Cyclopes | partial | - | not compared | missing 216-217; re-parented 210-211, 213-214; 35 moved (up to 110.8 cm) |
| em015861 Beast Commander | EM015861, EM071252 | 50 (41) | e5000 Cyclopes | partial | - | not compared | missing 216-217; re-parented 210-211, 213-214; 33 moved (up to 261 cm) |
| em015862 Necro Master | EM015862 | 60 (58) | e5000 Cyclopes | partial | - | not compared | missing 150-151, 156-164, 216-220; re-parented 210-214; 35 moved (up to 110.8 cm) |
| em015863 Shadow Master | EM015863 | 52 (46) | e5000 Cyclopes | partial | - | not compared | missing 150, 216-218, 220-222; re-parented 213-214; 35 moved (up to 110.8 cm) |
| em015870 Ancestor Origin | EM015870 | 50 (42) | e5000 Cyclopes | partial | - | not compared | missing 219, 221, 223, 225, 227; 35 moved (up to 110.8 cm) |
| em015900 Grigori +1 | EM015900, EM015932 | 107 (106) | e7000 Daimon | partial | - | differ | missing 70-73; re-parented 7-8, 11-12, 56-57, 60-61; 5 moved (up to 26.81 cm) |
| em015910 Severely Infected Demon | EM015910 | 111 (110) | e7000 Daimon | partial | - | differ | missing 70-73, 156-159; re-parented 7-8, 11-12, 56-57, 60-61; 5 moved (up to 26.81 cm); vertex formats 0x64593023, 0xb392101f; texture formats 14 |
| em015920 Bearded Grigori | EM015920 | 107 (106) | e7000 Daimon | partial | - | differ | missing 70-73; re-parented 7-8, 11-12, 56-57, 60-61; 5 moved (up to 26.81 cm) |
| em015921 Samyaza | EM015921 | 107 (106) | e7000 Daimon | partial | - | differ | missing 70-73; re-parented 7-8, 11-12, 56-57, 60-61; 5 moved (up to 26.81 cm) |
| em015930 Blaze Grigori | EM015930 | 107 (106) | e7000 Daimon | partial | - | differ | missing 70-73; re-parented 7-8, 11-12, 56-57, 60-61; 5 moved (up to 26.81 cm) |
| em018000 Rabbit | EM018000 | 22 (21) | e8000 Rabbits | same body | same body | line up | - |
| em018100 (no name) | EM018100 | 22 (21) | e8100 Giant Bats | same body | same body | line up | - |
| em018200 Buck +1 | EM018200, EM018201 | 28 (27) | e8200 Stags | same skeleton | same skeleton | line up | 8 moved (up to 29 cm) |
| em018300 Ox | EM018300 | 30 (29) | e8300 Oxen | same body | same body | line up | - |
| em018401 Giant Rat | EM018401 | 23 (22) | e8500 Rats | same body | same body | line up | - |
| em018600 Boar | EM018600 | 23 (22) | e8700 Wild Boars | same skeleton | - | line up | 10 moved (up to 16.5 cm) |
| em018601 Pig +1 | EM018601, EM018602, EM018603 +1 | 23 (22) | e8700 Wild Boars | same skeleton | - | differ | 10 moved (up to 16.5 cm) |
| em018700 (no name) | EM018700 | 15 (14) | e8900 Snakes | same body | same body | not compared | - |
| em018800 Spider | EM018800 | 19 (18) | e9000 Spiders | same body | same body | line up | - |
| em019000 Chicken | EM019000, EM019001 | 19 (18) | e8700 Wild Boars | partial | - | not compared | re-parented 8-9, 11, 13, 15, 18; 11 moved (up to 30.94 cm) |
| em019100 Goat | EM019100 | 26 (25) | e8700 Wild Boars | partial | - | not compared | missing 22-25; 19 moved (up to 25.01 cm) |
| em019200 Frog | EM019200, EM019201 | 18 (17) | e8700 Wild Boars | partial | - | not compared | re-parented 4-5, 7, 9, 15, 145; 9 moved (up to 26.11 cm) |
| em019300 Wild Boar | EM019300, EM019301 | 23 (22) | e8700 Wild Boars | same skeleton | same skeleton | differ | 10 moved (up to 16.5 cm) |
| em020402 Zuhl | EM020402, EM020600, EM071300 +2 | 140 (139) | e7000 Daimon | same skeleton | - | differ | 16 moved (up to 21 cm) |
| em020403 Altered Zuhl | EM020403 | 140 (140) | e7000 Daimon | same skeleton | - | differ | 16 moved (up to 21 cm) |
| em020404 Red Zuhl | EM020404 | 140 (139) | e7000 Daimon | same skeleton | partial | differ | 16 moved (up to 21 cm) |
| em020500 Diamantes | EM020500 | 72 (63) | e0600 Harpies | partial | - | not compared | missing 2, 14-21, 26, 30, 42, 54, 57-58, 61-63, 66-70, 86-89; re-parented 5, 9, 80; 25 moved (up to 148.97 cm) |
| em020601 Scourge +1 | EM020601, EM020606, EM071310 | 137 (137) | e7000 Daimon | partial | - | differ | missing 70-73, 156-159; re-parented 7-8, 11-12, 56-57, 60-61; 5 moved (up to 26.81 cm); vertex formats 0x64593023, 0xb392101f, 0xd877801b; texture formats 14 |
| em020602 Baphomet | EM020602, EM071311, EM080900 +1 | 133 (133) | e7000 Daimon | partial | - | differ | missing 70-73; re-parented 7-8, 11-12, 56-57, 60-61; 5 moved (up to 26.81 cm) |
| em020700 Black Knight +2 | EM020700, EM020701, EM020702 +6 | 66 (66) | e5801 The Ur-Dragon | partial | - | not compared | re-parented 4, 55-57, 230-239; 50 moved (up to 418 cm) |
| em020803 Ifrit | EM020803 | 127 (124) | e7000 Daimon | same skeleton | - | differ | 14 moved (up to 19.5 cm) |
| em020804 Ifrit | EM020804 | 127 (124) | e7000 Daimon | same skeleton | - | differ | 14 moved (up to 19.5 cm) |
| em021000 The White Dragon | EM021000 | 196 (175) | e5800 The Dragon | partial | - | differ | missing 72-73, 87-88, 129, 157, 243; re-parented 32, 35, 48, 51, 68-69, 110-114, 116, 118, 120-124, 126, 128, 148, 156, 180, 183, 196, 199, 209, 217-227, 229-231, 233, 240-242, 250-251; 113 moved (up to 858.24 cm) |
| em021001 Elder Dragon | EM021001, EM071100, EM080000 | 203 (179) | e5800 The Dragon | partial | - | differ | missing 72-73, 78, 87-88, 157, 243-244; re-parented 32, 35, 48, 51, 68-69, 110-114, 116, 118, 120-124, 126, 128, 156, 180, 183, 196, 199, 217-227, 229-231, 233-237, 239-242, 250-251; 114 moved (up to 858.24 cm) |
| em021002 Golgorran | EM021002, EM080100 | 211 (185) | e5800 The Dragon | partial | partial | differ | missing 72-73, 78, 87-88, 119, 129, 157, 243; re-parented 32, 35, 48, 51, 68-69, 110-114, 116, 118, 120-124, 126, 128, 135-136, 138, 148-149, 156, 180, 183, 196, 199, 206-209, 217-227, 229-231, 233, 240-242, 250-251; 114 moved (up to 858.24 cm) |
| em021003 Phantasmic Great Dragon | EM021003 | 196 (176) | e5800 The Dragon | partial | - | differ | missing 72-73, 78, 87-88, 129, 157, 243; re-parented 32, 35, 48, 51, 68-69, 110-114, 116, 118, 120-124, 126, 128, 148, 156, 180, 183, 196, 199, 209, 217-227, 229-231, 233, 240-242, 250-251; 113 moved (up to 858.24 cm) |
| em021004 Ushumgal | EM021004 | 209 (169) | e5800 The Dragon | partial | - | differ | missing 72-73, 78, 87-88, 157, 243-244; re-parented 32, 35, 48, 51, 68-69, 110-114, 116, 118, 120-124, 126, 128, 156, 180, 183, 196, 199, 217-227, 229, 233-234, 250-251; 113 moved (up to 858.24 cm) |
| em022000 Spirit Dragon Willmia | EM022000, EM080400 | 182 (158) | - (closest e5801) | none | - | - | missing 85, 157, 182, 185; re-parented 4-5, 9, 23-28, 32-63, 80, 100, 104-105, 109-114, 120-124, 129-132, 148, 156, 171-176, 178, 180, 184, 186, 188-189, 209, 217-225, 230, 232-233, 235, 240, 242-243; 54 moved (up to 900.2 cm); 63 of 158 bound joints fit |
| em022001 Spirit Dragon Willmia | EM022001, EM100105, EM100106 | 182 (158) | - (closest e5801) | none | - | - | missing 85, 157, 182, 185; re-parented 4-5, 9, 23-28, 32-63, 80, 100, 104-105, 109-114, 120-124, 129-132, 148, 156, 171-176, 178, 180, 184, 186, 188-189, 209, 217-225, 230, 232-233, 235, 240, 242-243; 54 moved (up to 900.2 cm); 63 of 158 bound joints fit |
| em023000 The Evil Dragon | EM023000, EM075400, EM075401 | 150 (141) | e5401 Cockatrices | partial | - | differ | missing 34-35, 50-51, 95-98, 233-234, 243-244; re-parented 104, 109, 175-176, 184, 191-192, 200; 47 moved (up to 398 cm) |
| em024000 Black Dragon +1 | EM024000, EM071101, EM081000 +1 | 197 (171) | e5800 The Dragon | partial | - | differ | missing 72-73, 87-88, 157; re-parented 32, 35, 48, 51, 68-69, 110-114, 116, 118, 120-124, 126, 128, 156, 180, 183, 196, 199, 209, 217-227, 229-231, 240-242, 250-251; 114 moved (up to 858.24 cm) |
| em024001 Power that Destroys Reason +1 | EM024001, EM071102, EM081001 +1 | 205 (177) | e5800 The Dragon | partial | - | differ | missing 72-73, 78, 86-88, 157, 198; re-parented 32, 35, 48, 51, 68-69, 110-114, 116, 118, 120-124, 126, 128, 148-149, 156, 160, 180, 183, 196, 199, 202, 217-227, 229, 231, 233-234, 241-242, 250-251; 114 moved (up to 858.24 cm) |
| em024002 Black Dragon (Event) | EM024002 | 205 (176) | e5800 The Dragon | partial | - | differ | missing 72-73, 78, 86-88, 157, 198; re-parented 32, 35, 48, 51, 68-69, 110-114, 116, 118, 120-124, 126, 128, 148-149, 156, 160, 180, 183, 196, 199, 202, 217-227, 229, 231, 233-234, 242, 250-251; 114 moved (up to 858.24 cm) |
| em030104 Dragon Crystal of Praying | EM030104 | 2 (1) | - (closest e9100) | none | - | - | 1 moved (up to 315.56 cm); rigid (1 bound joint(s)) |
| em030105 Dragon Crystal of Groaning | EM030105 | 2 (1) | - (closest e9100) | none | - | - | rigid (1 bound joint(s)) |
| em030106 Blazing Boulder | EM030106 | 5 (5) | e5502 Gazers | partial | - | not compared | re-parented 4; 3 moved (up to 325 cm) |
| em030107 Evil Dragon Test | EM030107 | 2 (1) | - (closest e9100) | none | - | - | rigid (1 bound joint(s)) |
| em030108 Evil Dragon Test | EM030108 | 2 (1) | - (closest e9100) | none | - | - | 1 moved (up to 111 cm); rigid (1 bound joint(s)) |
| em030109 Evil Dragon Test | EM030109 | 2 (1) | - (closest e9100) | none | - | - | rigid (1 bound joint(s)) |
| em030110 Grudge | EM030110 | 2 (1) | - (closest e9100) | none | - | - | rigid (1 bound joint(s)) |
| em030111 Insect Mound | EM030111, EM077000, EM080802 | 6 (6) | e8100 Giant Bats | partial | - | differ | missing 4-5; re-parented 3 |
| em030112 Dragon Crystal of Darkness | EM030112 | 2 (1) | - (closest e9100) | none | - | - | 1 moved (up to 191.8 cm); rigid (1 bound joint(s)) |
| em030113 Dragon Crystal of Darkness | EM030113 | 2 (1) | - (closest e9100) | none | - | - | 1 moved (up to 191.8 cm); rigid (1 bound joint(s)) |
| em030114 Crystal of Blessing | EM030114 | 2 (1) | - (closest e9100) | none | - | - | 1 moved (up to 91.22 cm); rigid (1 bound joint(s)) |
| em030115 Darkness Emitting Dragon Crystal | EM030115 | 2 (1) | - (closest e9100) | none | - | - | 1 moved (up to 191.8 cm); rigid (1 bound joint(s)) |
| em030116 Dragon Crystal of Destruction | EM030116, EM081004, EM081006 | 2 (1) | - (closest e9100) | none | - | - | 1 moved (up to 111 cm); rigid (1 bound joint(s)) |
| em030117 Dragon Crystal of Binding | EM030117, EM081005, EM081007 | 2 (1) | - (closest e9100) | none | - | - | 1 moved (up to 111 cm); rigid (1 bound joint(s)) |
| em030118 Black Sword | EM030118, EM080505 | 2 (1) | - (closest e9100) | none | - | - | 1 moved (up to 111 cm); rigid (1 bound joint(s)) |
| em071250 Shadow Master | EM071250 | 52 (46) | e5000 Cyclopes | partial | - | not compared | missing 150, 216-218, 220-222; re-parented 213-214; 35 moved (up to 110.8 cm) |

### Dragon's Dogma: Dark Arisen -> Dragon's Dogma Online

| Family | Enemy ids | Joints (bound) | Counterpart | Verdict | Back | Textures | What differs |
|---|---|---|---|---|---|---|---|
| e0100 Goblins +1 | em0100, em0103 | 28 (27) | em010100 Goblin | partial | same body | line up | missing 54, 58, 62, 64, 136 |
| e0200 Wolves | em0200 | 32 (31) | em010200 Wolf | same body | same body | line up | - |
| e0201 Direwolves +1 | em0201, em0202 | 32 (31) | em010201 Direwolf | same body | same body | line up | - |
| e0203 Wargs | em0203 | 32 (31) | em010200 Wolf | same body | - | differ | - |
| e0204 Garm | em0204 | 40 (39) | em010240 Valefar | same body | same body | line up | - |
| e0300 Skeletons | em2000 | 25 (22) | em010509 Mudman; namesake em010300 partial | same body | - | not compared | - |
| e0301 Skeleton Knights +1 | em2001, em2003 | 25 (24) | em010301 Skeleton Knight | same body | same body | line up | - |
| e0302 Skeleton Lords | em2002 | 25 (24) | em010509 Mudman | same body | - | not compared | - |
| e0303 Skeleton Archers +1 | em2005 | 25 (24) | em010509 Mudman | same body | - | not compared | - |
| e0304 Silver Knights | em2006 | 25 (24) | em010509 Mudman | same body | - | not compared | - |
| e0305 Living Armor | em2007 | 25 (24) | em010306 Living Armor | same body | same body | line up | - |
| e0306 Skeleton Brutes | em2004 | 25 (20) | em010307 Skeleton Brute | same body | same body | line up | - |
| e0400 Saurians +7 | em0400, em0401, em0402 +5 | 40 (37) | em010400 Saurian | same body | same body | line up | - |
| e0404 Pyre Saurians | em0404 | 40 (33) | em010440 Pyre Saurian | same body | same body | line up | - |
| e0500 Undead | em0500 | 25 (24) | em010500 Undead | same body | same body | line up | - |
| e0501 Undead | em0501 | 25 (24) | em010501 Undead | same body | same body | line up | - |
| e0502 Stout Undead | em0502 | 25 (24) | em010502 Stout Undead | same body | same body | line up | - |
| e0503 Undead  Warriors | em0503 | 25 (24) | em010509 Mudman | same body | - | not compared | - |
| e0504 Giant Undead | em0504 | 25 (24) | em010509 Mudman | same body | - | not compared | - |
| e0505 Poisoned Undead | em0505 | 25 (24) | em010509 Mudman | same body | - | not compared | - |
| e0506 Banshees | em0506 | 25 (24) | em010501 Undead | same body | - | line up | - |
| e0507 Eliminators | em0507 | 42 (35) | em010508 Eliminator | same body | same body | line up | - |
| e0600 Harpies +1 | em0600, em0601 | 42 (40) | em010600 Harpy | partial | same body | line up | missing 25, 41 |
| e0602 (no name) | em0602 | 38 (34) | em010605 Siren | partial | - | not compared | missing 25, 41 |
| e0603 Succubi | em0603 | 42 (40) | em010621 Ukobach | partial | - | differ | missing 25, 41 |
| e0604 Strigoi | em0604 | 42 (40) | em010621 Ukobach | partial | same body | differ | missing 25, 41 |
| e0605 Sirens | em0605 | 38 (34) | em010605 Siren | partial | same body | not compared | missing 25, 41 |
| e0800 Skeleton Mages | em2100 | 25 (24) | em010308 Skeleton Mage | same body | same body | line up | - |
| e0801 Skeleton Sorcerers | em2101 | 25 (24) | em010309 Skeleton Sorcerer | same body | same body | line up | - |
| e0900 Ogres | em0900 | 56 (55) | em015500 Ogre | partial | same body | line up | missing 130-131, 136, 138-139 |
| e0901 Elder Ogres | em0901 | 56 (55) | em015508 Cragger | partial | same body | differ | missing 130-131, 136, 138-139 |
| e5000 Cyclopes | em5000 | 66 (65) | em015000 Cyclops | partial | same body | line up | missing 35-36, 51-52, 54, 56-58, 60-65, 136, 138, 148-149, 210-215, 233 |
| e5001 Gorecyclopes | em5001 | 73 (72) | em015010 Gorecyclops | partial | same body | line up | missing 35-36, 51-52, 54, 56-58, 60-65, 136, 138, 148-149, 210-215, 234-243 |
| e5100 Golems +1 | em5100, em5101 | 66 (56) | em015100 Golem | partial | same body | line up | missing 55-57, 59-61, 210-212, 214-215 |
| e5200 Chimeras +1 | em5200, em5201 | 53 (49) | em015200 Chimera | partial | partial | line up | missing 130-131 |
| e5300 Hydras | em5300 | 41 (38) | - (closest em010482) | none | - | - | missing 56-66, 130-134, 142-144, 148-149; re-parented 4-5, 9, 13, 145; 12 moved (up to 121 cm); 12 of 38 bound joints fit |
| e5301 Archydras | em5301 | 41 (38) | - (closest em010482) | none | - | - | missing 56-66, 130-134, 142-144, 148-149; re-parented 4-5, 9, 13, 145; 12 moved (up to 121 cm); 12 of 38 bound joints fit |
| e5400 Griffins | em5400 | 137 (133) | em015300 Griffin | same skeleton | same skeleton | line up | 2 moved (up to 20.86 cm) |
| e5401 Cockatrices | em5401, em5402 | 136 (131) | em015301 Cockatrice | same body | partial | line up | - |
| e5500 Evil Eyes | em5500, em5500B | 38 (38) | em015400 Evil Eye | partial | - | line up | missing 7-13, 15-20 |
| e5501 Vile Eyes | em5501 | 13 (13) | em015401 Vile Eye | same body | same body | line up | - |
| e5502 Gazers | em5502 | 38 (25) | em015406 Volt Eye | same body | - | differ | - |
| e5503 Maneaters | em5500C | 12 (12) | em011400 Maneater | same body | partial | not compared | - |
| e5700 Wights | em6000 | 23 (22) | em015600 Wight | same body | same body | differ | - |
| e5701 Liches | em6001 | 23 (22) | em015623 Rage Ghost | same body | - | differ | - |
| e5702 Dark Bishops | em6002 | 23 (22) | em015623 Rage Ghost | same body | - | differ | - |
| e5703 Death | em6003 | 23 (19) | em015603 Death | same body | partial | line up | - |
| e5800 The Dragon | em5800, em5802 | 218 (197) | em021002 Golgorran | partial | partial | differ | missing 31, 47, 54, 140-141, 146-147, 155, 179, 187-188, 195, 235-237, 239, 249; re-parented 32, 35, 48, 51, 68-69, 110-114, 116, 118, 120-124, 126, 128, 134-139, 148-149, 151-152, 156, 180, 183, 196, 199, 217-227, 229-231, 233-234, 240-242; 117 moved (up to 858.24 cm) |
| e5801 The Ur-Dragon | em5801 | 242 (220) | em021002 Golgorran | partial | - | differ | missing 3, 31, 47, 54-55, 59, 66-67, 79, 140-141, 146-147, 155, 168-169, 175, 178-179, 187-188, 195, 235-237, 239, 245-249; re-parented 32, 35, 48, 51, 68-69, 72, 78, 87-88, 110-114, 116, 118-124, 126, 128-129, 131-132, 134-139, 148-149, 151, 153-154, 156, 180, 183, 196, 199, 217-227, 229-234, 240-244; 115 moved (up to 858.24 cm) |
| e5803 (no name) | em7002 | 119 (117) | em015708 Angules | partial | - | not compared | missing 31-34, 47-50, 54, 56, 58, 60, 70-71, 117-118, 127-128, 152, 224, 227-228, 231, 235; re-parented 35, 51; 72 moved (up to 563 cm) |
| e5900 Drakes +5 | em5900, em5901, em5902 +3 | 160 (144) | em015700 Drake | partial | partial | line up | missing 31-34, 47-50, 54, 56, 58, 60, 62-65, 68-71, 76-77, 117-118, 127-128, 136, 138, 235-237; re-parented 35, 51 |
| e5906 Cursed Dragons | em5906 | 146 (137) | em015706 Cursed Dragon | partial | partial | line up | missing 31-34, 47-50, 54, 56-58, 60-65, 68-71, 74-77, 117-118, 127-128, 235; re-parented 35, 51 |
| e7000 Daimon | em7000, em7001 | 168 (167) | em020404 Red Zuhl | partial | same skeleton | differ | missing 81, 130-136, 138-149, 198, 200, 208-209, 232, 236-238; 16 moved (up to 21 cm) |
| e8000 Rabbits | em8000 | 22 (21) | em018000 Rabbit | same body | same body | line up | - |
| e8100 Giant Bats | em8100 | 22 (21) | em018100  | same body | same body | line up | - |
| e8200 Stags +1 | em8200, em8201 | 28 (27) | em018200 Buck | same skeleton | same skeleton | line up | 8 moved (up to 29 cm) |
| e8300 Oxen | em8300 | 30 (29) | em018300 Ox | same body | same body | line up | - |
| e8500 Rats +1 | em8500, em8501 | 23 (22) | em018401 Giant Rat | same body | same body | line up | - |
| e8600 Crows | em8600 | 18 (17) | em015706 Cursed Dragon | partial | - | not compared | missing 3; re-parented 2, 90, 145; 12 moved (up to 377.71 cm) |
| e8601 Birds | em8601 | 12 (11) | em015706 Cursed Dragon | partial | - | not compared | missing 3; re-parented 2; 8 moved (up to 389.59 cm) |
| e8602 Seabirds | em8602 | 18 (17) | em015706 Cursed Dragon | partial | - | not compared | missing 3; re-parented 2, 90, 145; 12 moved (up to 376.59 cm) |
| e8700 Wild Boars | em8700 | 23 (22) | em019300 Wild Boar | same skeleton | same skeleton | differ | 10 moved (up to 16.5 cm) |
| e8900 Snakes | em8900 | 15 (14) | em018700  | same body | same body | not compared | - |
| e9000 Spiders | em9000 | 19 (18) | em018800 Spider | same body | same body | line up | - |
| e9100 Leapworms | em9100 | 8 (7) | em010820 Armored Insect | same body | same body | not compared | - |

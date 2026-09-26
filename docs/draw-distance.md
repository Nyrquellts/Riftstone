# Draw distance: what View Range multiplies, and the `draw_distance` plugin

Measured on DDDA.exe build 2364871 and the PS3 build on 2026-09-26. Everything here is static: the
PC exe read with `tools/re_dd.py`, the PS3 build's names, the
PS3-named symbol map of the PC exe, and the plugin's harness, which runs the game's own code on fake
objects. Nothing on this page was observed in the running game. Every effect in game is UNKNOWN.

Model detail (LOD) is `docs/lod.md` and the `lod_tuner` plugin. This page is about the other distances
config.ini `ViewRange` changes: how far out objects are shown, how far grass reaches, and how far out
enemies stay active.

## The multiplier

Config.ini `ViewRange` is stored at `[0x018D1D20]+0x38` as NORMAL 1, FAR 2, FARTHEST 3
(`docs/re-engine-audit.md`). Where a distance is set, the game picks its multiplier the same way each
time: it loads 1.0, then `cmp ViewRange, 3` and a `jne` over a load of the shared 3.0, else
`cmp ViewRange, 2` and a `jne` over a load of 2.0:

| Where | What the multiplier scales | The FARTHEST test and load |
|---|---|---|
| `sGameSys::setNoMoveDistance` (`0x0044D770`), each stage | objects' display radii | `83 F9 03 75 0A F3 0F 10 05 40 01 52 01` at `0x0044D885` |
| the `sGameSys` constructor | the same radii before the first stage | `83 F8 03 75 0A F3 0F 10 05 40 01 52 01` at `0x004387CD` |
| `aStage::init` | grass fade | `83 F9 03 75 0A F3 0F 10 0D 40 01 52 01` at `0x005038F3` |
| `uEnemy::setup` (`0x00AA8830`) | an enemy's activity distance | `83 F8 03 75 0A F3 0F 10 05 40 01 52 01` at `0x00AA8A71` |

The 3.0 is one constant, `00 00 40 40` at `0x01520140`, with 234 readers in `.text`; 1.0 is
`00 00 80 3F` at `0x015319B8` and 2.0 `00 00 00 40` at `0x014F9E04`. A patch therefore changes the
instruction that reads the constant, never the constant. The PS3 build has no ViewRange: its
`setNoMoveDistance` (PS3 `0x000712A8`) stores the table as it is.

## Objects on the overworld: the display radius

**The radii.** `aStage::init` calls `setNoMoveDistance` with the stage number in `eax` and `sGameSys`
in `edx`: `8B 83 24 07 00 00 E8 A6 C5 F4 FF` at `0x005011BF`. It picks a row of four floats by stage
(stage 100 first: `83 F8 64 75 05` at `0x0044D83A`), multiplies the first three by the multiplier and
stores them at `sGameSys+0xBE454..+0xBE45C` (`F3 0F 11 8A 54 E4 0B 00` at `0x0044D8B2`); the fourth goes to
`+0xBE460` unscaled. The PS3 build names them `mObjNoMoveDistance[3]` and `mObjNoMoveSizeRadius` (PS3
`+0xA4F84`, `+0xA4F90`):

| Stages | `[0]` flagged objects | `[1]` small objects | `[2]` large objects | size limit |
|---|---|---|---|---|
| 100, 200-250 but 220 | 1500 cm | 2500 cm | 100000 cm | 300 cm |
| 220 | 1000 | 2000 | 10000 | 300 |
| 601-609 | 4500 | 5000 | 100000 | 300 |
| any other | 2000 | 3000 | 100000 | 300 |

(1500, 2500, 100000 and 300 are `00 80 BB 44` at `0x017F42F4`, `00 40 1C 45` at `0x0152E228`,
`00 50 C3 47` at `0x0161C640` and `00 00 96 43` at `0x014EA164`.)

**The radius each object takes.** `uOmObjBase::after` (PC `0x00C52A30`, PS3 name) picks once, the first
time it runs, and stores the result as `mDispRadius` (PC `+0x2928`, PS3 `+0x2868`):
`D9 9D 28 29 00 00` at `0x00C52B8F`. An object whose parameters carry bit 0x800 of `cScrOmParam::mStatus`
takes `[0]` (`F7 C1 00 08 00 00` at `0x00C52B33`); otherwise a bounding radius of 3 m or less
(`F3 0F 10 89 60 E4 0B 00` at `0x00C52B72` reads the size limit) takes `[1]` and a larger one `[2]`.
`uOmSetBase::after` (`0x00C556C0`) does the same for set objects (`uOmSetBase` and its five subclasses)
without the flag: `F3 0F 10 81 60 E4 0B 00` at `0x00C55712`, stored at `+0x430`
by `D9 9E 30 04 00 00` at `0x00C55733`.

**What the radius does.** Every frame `uOmObjBase::move` (`0x00C51D90`, vtable `0x0160A498` slot 8) sets
four bits of `mOmRest` (PC `+0x2627`; the PS3 build's `unOmRestriction`: `hide`, `drawDistance`,
`insideOfZone`, `matchDispHour`, `disableBlink`, `drawS10Lot`): `80 8E 27 26 00 00 2E` at `0x00C51D93`.
Then `uOmObjBase::setRestrictDrawAndMove` (`0x00C51E60`, PS3 `0x00A347F0`) clears `drawDistance` when
the Arisen is farther than `mDispRadius` -- only on stage 100 (`83 7F 34 64` at `0x00C51E74` tests
`sGameSys::mStageNo`) and only for objects with `mCommand.enableLengthCheck`:
`0F 2F CB 76 07 80 A6 27 26 00 00 FD` at `0x00C51F06`. Without `drawDistance` the object's unit loses
its Draw attribute (`81 66 04 FF F7 FF FF` at `0x00C51E2A`) and `uObjModel::move` is not called: it is
neither drawn nor updated. Within the radius it is drawn (`81 4E 04 00 08 00 00` at `0x00C51DFC`) and
updated (`E8 AF 3F C3 FF` at `0x00C51E0C`). `uOmSetBase::move` (`0x00C55380`) runs the same test
against its `+0x430`: `0F 2F CB 76 0C` at `0x00C55496`. The objects are the layouts' om placements
(`cSetInfoOmModel` and its kinds: props, doors, chests, gathering spots). `uOmObjBaseInstancing`, the
unit that draws many instanced placements of one model, runs `uOmObjBase::move` first
(`E8 28 C9 02 00` at `0x00C25463`), so the test applies to such a unit as a whole; how its position
relates to its placements was not traced.

So on the overworld, at NORMAL, a small object disappears 25 m away (50 m at FAR, 75 m at
FARTHEST) and a flagged one at 15 m; a large one keeps 1 km. On every other stage no object is hidden by
distance. The harness shows each step with the game's own code (below).

## Grass

`aStage::init` forces `sGrass`'s fade (`[0x018D2004]`: `+0x50` mForcedFade, `C6 40 50 01` at
`0x005038E1`) and sets the band to 1700 .. 7000 cm times the multiplier:
`F3 0F 11 48 58 F3 0F 11 50 54` at `0x00503922` (7000 is `00 C0 DA 45` at `0x0152E204`, 1700
`00 80 D4 44` at `0x0161DA64`; PC offsets are the PS3 build's minus 4). The 1.0 it loads first stays in
`xmm0`, and the rest of `aStage::init` stores that register: `F3 0F 11 84 24 8C 00 00 00` at
`0x005039B3`. That is why the plugin never redirects the 1.0 load.

## Enemies: an activity distance, not a visibility distance

`uEnemy::setup` sets `mDrawDistance` (PC `+0x2984`, PS3 `+0x28D4`) to its kind's entry of
`uEnemy::mVisibleDistanceTable` (144 floats, the first `00 00 7A 45` at `0x014F9768`: 20 to 300 m, and
FLT_MAX for kinds never frozen) times the multiplier: `F3 0F 10 0C 8D 68 97 4F 01 F3 0F 59 C8` at
`0x00AA8A8D`, stored by `F3 0F 11 8D 84 29 00 00` at `0x00AA8AA2`. A placement's own `.lot`
`mDrawDistance` (`mSetInfoDrawDist`, `+0x2994`) replaces it when 0 or more. `mManageDistance` is that plus
200 m: `F3 0F 58 05 1C C2 61 01` at `0x00AA8AC3` (20000 is `00 40 9C 46` at `0x0161C21C`).

`uCharacterBase::checkMoveMode` (`0x00853DC0`, PS3 `0x0058A578`) measures the distance to the Arisen
only for characters whose `mCheckMoveModeType` (`+0x2974`) is 1 (`8B 86 74 29 00 00 48` at
`0x00853E88`). Two constructors set it: `uEnemy`'s (`C7 87 74 29 00 00 01 00 00 00` at `0x00AA8189`)
and `uHumanEnemy`'s (`C7 87 74 29 00 00 01 00 00 00` at `0x00BA7481`); `uCharacterBase`'s constructor
starts it at 0, and no other character code writes it (the PS3 build agrees). Within `mDrawDistance` the move mode is 0,
within `mManageDistance` 1, beyond 2 (`F3 0F 10 8E 84 29 00 00 0F 2F C8` at `0x00853F12`, then
`76 04 33 C0 EB 17` at `0x00853F25`); on stage 100 a character on a cell that is not loaded gets 3.

For an enemy the mode decides activity only. `uEnemy::move` skips `uObjModel::move` in modes 2 and 3:
`83 F8 03 0F 84 DA 00 00 00 83 F8 02 0F 84 D1 00 00 00` at `0x00AA95B7` before
`E8 F0 C7 DD FF` at `0x00AA95CB`; a change between 0 and 1 puts its ragdoll to sleep or wakes it
(`uRagdollExt::setRigidBodySleep` on PS3). `uEnemy`'s draw is `uObjModel::draw` with no test,
`E9 7B 97 F3 FF` at `0x0094D100`, and none of the 20 enemy functions that clear the Draw attribute
reads the move mode or the distances (they follow an enemy's own states and parts). So an enemy is fully
active within its distance (30 to
50 m for most kinds at NORMAL, three times that at FARTHEST), keeps moving 200 m further, and beyond
that stands frozen in its last pose -- drawn.

## Human enemies: 100 m at every View Range

Every `uCharacterBase` starts with `mDrawDistance` 10000 cm: `F3 0F 10 0D A8 0B 52 01 F3 0F 11 8B 84 29 00 00`
at `0x0084A841` (10000.0 is `00 40 1C 46` at `0x01520BA8`, a constant with 116 readers). `uEnemy::setup`
replaces it; `uNpc::setup` keeps it unless the placement sets one, and makes the manage distance 200 m
more (`F3 0F 58 05 1C C2 61 01` at `0x00BAC526`). Of the characters that are not enemies only human
enemies (`uHumanEnemy`, made by `sHumanEnemyManager`) test the distance, and for them the mode is
visibility too: `uHumanEnemy::move` starts with `uNpc::move` (`E8 B1 7C 00 00` at `0x00BA783A`), which
in mode 2 or 3 clears the Draw attribute and returns (`83 F9 02 72 18` at `0x00BAF5C1`,
`81 66 04 FF F7 FF FF` at `0x00BAF5D1`). So a human enemy is fully active within 100 m, moving and shown
to 300 m, and gone beyond, at any View Range.

Townspeople are a different system. A `uNpc` with a `uNpcIntel` stand-in takes its mode from the intel
(`8B 80 F0 04 00 00` at `0x00BB3D1E` in `uNpc::draw`), whose own distance starts at 50 m
(`F3 0F 10 0D 5C E2 52 01` at `0x00BBC1DB`, stored by `F3 0F 11 8E F4 04 00 00` at `0x00BBC1E6`) and
whose count is the `sNpcIntelManager` budget (12 / 24 / 36 by ViewRange, `docs/lod.md`). The plugin does
not touch them.

## The plugin

`native/plugins/draw_distance` writes a few bytes once, at start-up, from `draw_distance.ini`. A setting
of 0 leaves that part to the game.

| Key | Default | The patch |
|---|---|---|
| `Objects` | 3 | in `setNoMoveDistance` and the constructor, the `jne` before the 3.0 load becomes two `nop`s and the load reads the plugin's number: N at every View Range (1 to 20) |
| `Grass` | 3 | the same in `aStage::init`'s grass block (1 to 3: FARTHEST's own is the most the game ever draws) |
| `Enemies` | 0 | the same in `uEnemy::setup` (1 to 20); a placement's own distance still wins |
| `HumanEnemies` | 0 | metres: the constructor's 10000.0 load reads the plugin's number (100 to 2000) |
| `ObjectsNeverHide` | 0 | both display-radius tests always pass: the `jbe`s `76 07` at `0x00C51F09` and `76 0C` at `0x00C55499` become `jmp`s (first byte EB) |
| `EnemiesAlwaysActive` | 0 | `checkMoveMode`'s first test always passes (its `jbe`, `76 04` at `0x00853F25`, becomes two `nop`s): mode 0 at any distance for enemies and human enemies, except off a loaded cell |

Before writing, the plugin compares every site and the code around it (thirteen blocks, 444 bytes), the
four constants, three vtable slots (`uOmObjBase` and `uOmSetBase` move, `uEnemy` setup) and three
calls, whatever the settings; on any difference it writes nothing and says why in
`riftstone\logs\draw_distance.log`. A second copy of the plugin therefore refuses. Once it has
patched, it pins itself in memory, because the game's instructions read its numbers.

**The default** is FARTHEST's own objects and grass at every View Range, and the game's enemies and
human enemies. The intended pairing is **FAR or NORMAL + `lod_tuner` + `draw_distance`**: models get
`lod_tuner`'s screen-aware switch distances, objects and grass FARTHEST's reach, without FARTHEST's triple
NPC budget, its wider streaming radius and its enemies active three times as far out. Under FARTHEST the
default changes nothing; raise the numbers there. Higher `Objects` shows small objects farther but
only where the game has loaded their cells (`docs/re-engine-audit.md`: about 125 m at NORMAL, 250 m at
FAR for the layout cells, plus up to 41 m).

### What was dropped from the 2026-09-24 draft

A lane wrote a first `draw_distance` on 2026-09-24 (parked on `wip/native-limits-0924`, commit
`873f56b`). Checked claim by claim:

- **Its model settings are gone.** `full_detail_models` turned the ten FARTHEST tests of the LOD
  choosers into "always HIGH" (the sites are right, e.g. `83 FA 03 74 33` at `0x00FFC519` in
  `uModel::drawModel`); `full_detail_instanced` handed the instancing culler LOD_HIGH instead of the
  object's own type (`C1 E0 13 51 C1 F8 1C 50` at `0x00C25938`, and `0x00C26112`). Both are model LOD,
  which `lod_tuner` already tunes per model and the game's FARTHEST already forces; both would override
  `lod_tuner` in those paths, the first also forces cloth simulation to full detail
  (`uSimSoftBody::getTargetLODLevel` is one of the ten), and the second would draw nothing for a model
  with no HIGH parts, the far-only stand-ins `lod_tuner` keeps vanilla.
- **Its multipliers only acted at FARTHEST** (they replaced the 3.0 load and nothing else). Now each
  `jne` before it is removed, so the number holds at every View Range; redirecting the 1.0 load instead
  would have broken `aStage::init` (the `xmm0` above).
- **"How far enemies stay drawn" was wrong**: the enemy distance is activity (above), and enemies are
  drawn in every mode. Kept, with that meaning, off by default.
- **"NPCs and human enemies use 10000":** only human enemies test it; townspeople follow their intel.
  Kept as `HumanEnemies`, off by default.
- **Grass above x3 "may overflow the grass buffers"** was a guess; the cap is now x3, what FARTHEST
  itself draws.
- **"Objects never hide" missed half the objects**: `uOmSetBase::move` has its own test (`0x00C55499`).
  Both are patched now. `characters_never_hide` became `EnemiesAlwaysActive`, since only enemies and
  human enemies measure the distance.
- Its message box on refusal and its thread polling ViewRange in memory are gone: the log says it, and
  the plugin reads config.ini's `ViewRange` for the log only.

### Proof so far

`python native/plugins/draw_distance/test/run_tests.py` maps a read-only copy of DDDA.exe into a harness
process and lets the plugin verify and patch it. The harness compares the whole image with its copy from
before (only the plugin's bytes may differ), loads a second copy of the plugin (it must change nothing),
then runs the game's own code on fake objects at NORMAL, FAR and FARTHEST: all of `setNoMoveDistance`
(five stage rows) and the constructor's block; the radius pick of `uOmObjBase::after` and
`uOmSetBase::after`; all of `setRestrictDrawAndMove` and `uOmSetBase::move`'s test with the Arisen
inside, outside and 1.2 times the game's own radius away, and off the overworld; `uEnemy::setup`'s block
for a 40 m kind, a never-frozen kind and a placement's own distance; the constructor's default and all
of `checkMoveMode`; and the grass block, with `xmm0` still 1.0 after it. Seven profiles: the shipped
ini, two others, clamped and unreadable values, every setting 0, `Enabled = 0`, and one byte of game
code changed (refused, nothing written): 299 checks pass. `tools\test_all.cmd` runs it.

**UNKNOWN until observed in game:** the frame rate and CPU cost of each setting; how the extra objects,
grass and active enemies look; whether objects outside the loaded cells matter at high `Objects`; that
the loader brings the plugin in before `sGameSys` is built (the constructor site; `setNoMoveDistance`
runs again at every stage either way).

### Install (writes to the game; the owner's yes first)

```bat
native\plugins\draw_distance\build.cmd
riftstone loader install
riftstone loader plugin add native\plugins\draw_distance\out\draw_distance.asi
```

Removal: `riftstone loader plugin remove draw_distance.asi`.

## Not covered (leads)

- **Townspeople:** the `sNpcIntelManager` budget (12 / 24 / 36, `init` `0x00471CA9`, `moveAfter`
  `0x004724C0`) and the intel's own 50 m are what make people pop in towns.
- **Terrain and far scenery:** the stage's streaming window and its range test (`docs/re-engine-audit.md`)
  and the area LOD archives; not distances this plugin sets.

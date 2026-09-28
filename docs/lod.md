# LOD pop-in: what causes it and the `lod_tuner` plugin

Measured on DDDA.exe build 2364871 and the installed game on 2026-09-24. Nothing here has been
observed in the running game yet. Everything below comes from the exe's code, the PS3 build's
names, and the game's own files.

## What the game does

**Every model file carries its own two switch distances.** A `.mod` (version 212) has
`rModel::MODEL_INFO {s32 middist; s32 lowdist; ...}` at file offset `0x70`, in centimetres.
`rModel::load` (`0x00FA8FE0`) copies the pair into the model at `+0xE0`
(`movq [esi+0xE0], xmm0` at `0x00FA91E4`). Each primitive has a LOD mask: `1` HIGH, `2` MEDIUM,
`4` LOW, `0xFF` every level. So all detail levels are already in the one file. Showing more detail
farther away costs drawing time, not memory.

**Who reads the pair:**

| Reader | What it does with it |
|---|---|
| `uModel`, `uBaseModel`, `uModelSymmetry`, `uSwingModel`, `uSwingJointModel::drawModel` (inline, e.g. `0x00FFC50B`) | HIGH up to `middist x m`, MEDIUM up to `lowdist x m`, LOW beyond. `m` comes from config.ini `ViewRange`: NORMAL 1, FAR 2. FARTHEST skips the test and always draws HIGH |
| `uSimSoftBody::getTargetLODLevel` (`0x0083D220`), baked player/pawn models | the same test, for cloth simulation detail |
| `cInstancingCulling::updateLODParam` (`0x0113D430`), called every frame by the instancing units | instanced vegetation: LOD distances `0 / middist / lowdist / lowdist + mLodBillboardDistance` (billboards beyond), with a fade band between them. **ViewRange does not touch these**, except that `uOmSwingInstancing::move` forces HIGH at FAR and FARTHEST |

**The distances were tuned for 720p consoles.** `python tools/lod_survey.py` over the 3,712 distinct
models: 504 have LOD meshes (458 scenery: `scr\...` stage pieces and `model\om\...` objects). At a
40-degree vertical view (the engine's default camera, `uCamera` constructor), a scenery model is still
this tall on a 720p screen when it switches:

| Switch | Median | 10th percentile | 30th percentile |
|---|---|---|---|
| first change (MEDIUM, or a HIGH-only part vanishing) | 475 px | 30 px | 100 px |
| to LOW | 236 px | 15 px | 48 px |

At 1440p those sizes double. That is the pop-in.

**`ViewRange` is not only a LOD setting.** Config.ini `ViewRange` (NORMAL, FAR, FARTHEST, stored in
the PC settings object `[0x018D1D20]+0x38`) multiplies a dozen other things by 1, 2 or 3:

| System | NORMAL / FAR / FARTHEST | Where |
|---|---|---|
| Model LOD distances | x1 / x2 / always HIGH | the draw paths above |
| Enemy activity distance (`uCharacterBase::mDrawDistance`, PC `+0x2984`) | per enemy type x1 / x2 / x3 | `uEnemy::setup` (`0x00AA8830`, ViewRange read at `0x00AA8A54`) multiplies `uEnemy::mVisibleDistanceTable` (PC `0x014F9768`, 144 floats identical to the PS3 table, indexed by the byte at `+0x2D`): 20 to 300 m (30 to 50 m for most kinds), and 7 entries never frozen (FLT_MAX). Despite the name it is not a visibility distance: an enemy is fully active within it, keeps moving 200 m further (`mManageDistance`) and stands frozen beyond, drawn all the while (`docs/draw-distance.md`). A placement's own `.lot` `mDrawDistance` overrides it |
| NPC budget in `sNpcIntelManager` | 12 / 24 / 36 (20 / 40 / 60 in stage `0xF0`) | `init` `0x00471CA9`, `moveAfter` `0x004724C0` |
| Human enemies (`uHumanEnemy`) | 100 m at every setting | the `uCharacterBase` constructor's default `mDrawDistance` 10000 cm (`0x0084A841`); `uEnemy::setup` replaces it for enemies, and none of the 21 ViewRange readers touches it. Of the other characters only human enemies test the distance: fully active within it, shown and moving to 300 m, hidden beyond. Townspeople follow their `uNpcIntel` stand-in instead (`docs/draw-distance.md`) |
| Object display radii (`sGameSys::mObjNoMoveDistance[3]`, PC `+0xBE454`..`+0xBE45C`) | x1 / x2 / x3 | `sGameSys::setNoMoveDistance` (`0x0044D770`, from `aStage::init`) by stage: 100 and 200-250 1500 / 2500 / 100000 cm, 220 1000 / 2000 / 10000, 601-609 4500 / 5000 / 100000, others 2000 / 3000 / 100000 (a fourth value, 300, the size limit, is not scaled). Each object takes one as its `mDispRadius` (`+0x2928`; flagged objects the first, up to 3 m the second, larger the third), and on stage 100 an object beyond it is neither drawn nor updated (`0x00C51F09`, and `uOmSetBase::move`'s test at `0x00C55499`; traced in `docs/draw-distance.md`) |
| Grass (`sGrass` `[0x018D2004]` `+0x54` / `+0x58`) | 1700 / 7000 cm x1 / x2 / x3 | `aStage::init` `0x005038DB` |
| Stage 100's split cells (models, collision, layouts streamed around the player) | load radius x1 / x2 / x3 inside the fixed 5x5 / 3x3 window: at NORMAL about 200 / 152 / 125 m for models / collision / layouts, so most of the 5x5 outer ring stays unloaded; from FAR nearly the whole window loads | `aStage`'s range test `0x005075D0` reads ViewRange at `0x0050771C`; the window counts never change (`aStage` constructor; `docs/re-engine-audit.md`) |
| Stage 100's area LOD archives | `area_index00..05` `_low` / `_high` / `_high` | `uStageSplitCtrl` (updateStageLowMdl), compare at `0x00C5DAC7` |

The x2 and x3 are shared constants: the 3.0 at `0x01520140` has 234 readers in `.text`, so a plugin
repoints the one instruction that reads it (as the runtime does for the 150.0 frame cap), never the
constant. ViewRange (`[0x018D1D20]+0x38`) has 21 readers. FARTHEST's "always HIGH" is a `cmp ..., 3` in
ten places (`0x0083D24D`, `0x0084596C`, `0x00B898BC`, `0x00C6E336`, `0x00EA5443`, `0x00F1FE42`,
`0x00F61972`, `0x00FA604B`, `0x00FA77B9`, `0x00FFC51C`). Instanced objects (`uOmObjBaseInstancing`,
`uOmSwingBaseInstancing` `vf11`, `0x00C25938`/`0x00C26112`) hand the culler their model's 4-bit LOD type
(bits 9..12 of `+0x108`), not ViewRange; the layouts hold 20,148 `cSetInfoInsModel` placements.

So FARTHEST removes model pop-in at a cost well beyond model detail: triple the NPCs, enemies fully
active three times as far out (120 m for a 40 m kind), more stage loaded (FAR loads that much too:
nearly the whole streaming window and the high-detail area LOD archives). What each of those costs in
frames is UNKNOWN until measured in game.

## The plugin

`native/plugins/lod_tuner` puts one `jmp` in `rModel::load` right after its primitive loop
(`0x00FA9479`). Every successful load passes there once, with the path, joints, bounding sphere,
`MODEL_INFO` and every primitive's LOD mask in place. The thunk replays the two instructions the `jmp`
covers, rescales that model's pair once and resumes. Nothing runs per frame, and nothing more is
loaded. Every reader above sees the new pair.

- **Scenery** (paths `scr\...` and `model\om\...`) keeps its detail until it is about `PopPixels`
  tall on screen: `middist` becomes at least `radius x height / (PopPixels x tan(fov/2))`. It also
  gets at least `Scale x` vanilla (`auto` = screen height / 720, the consoles' resolution). Both
  distances get the same multiplier, so the artist's spacing between levels and instancing's fade
  band keep their shape.
- **Everything else** (characters, enemies, weapons, effects) gets `Characters` (1.0 = vanilla). Their
  pair also sets cloth-simulation detail, so raising it costs CPU in crowds.
- **A far-only stand-in stays vanilla.** A model with no primitive drawn at HIGH is shown only from
  afar, with other models covering the close range, and pushing its distances out would hide it. One
  exists: `scr\st380\model\st380_md12`. FARTHEST hides it too.
- A distance is never lowered and never pushed past `MaxDistance` (10 km). A model without a usable
  path counts as scenery when it has no joints.

**The trick.** A switch still happens, but only once the object is a couple of dozen pixels tall,
far out where the game's distance haze already softens it. Big silhouettes (cliffs, walls, stage
pieces, trees) effectively never switch within view. Small clutter, which is numerous and where extra
drawing costs the most, keeps distances close to vanilla x 2 to 3. With the defaults at 1440p the
median scenery multiplier is x38.8; objects under 1.5 m get a median x3.3 (`tools/lod_survey.py`).

**Against the game's own settings.** Under FARTHEST the game already draws every regular model at
HIGH, so there the plugin only changes instanced vegetation. The intended pairing is **FAR or
NORMAL + lod_tuner**. Regular models then get finite distances that are never costlier than
FARTHEST's "always HIGH", without FARTHEST's triple NPC budget and enemies active three times as far.
Under FAR the draw paths double the plugin's distances again. The other distances ViewRange multiplies
(objects' display radii, grass, enemy activity) are the `draw_distance` plugin's: it gives them a number
of their own at every ViewRange, by default FARTHEST's x3 for objects and grass
(`docs/draw-distance.md`). So **FAR or NORMAL + lod_tuner + draw_distance** has FARTHEST's reach for
models, objects and grass without its other costs. The frame rate of any of these setups is UNKNOWN
until measured.

**FARTHEST with levels of detail (`Farthest = lod`, 2026-09-27).** For players who keep FARTHEST, the
plugin can give it levels of detail instead. Each of the ten ViewRange tests loads ViewRange with the
multiplier at 1; FARTHEST branches to the level store with HIGH, FAR stores 2 as the multiplier. With
`Farthest = lod` the FARTHEST branch lands on that same store, so FARTHEST picks HIGH up to `middist x 3`,
MEDIUM up to `lowdist x 3` and LOW beyond, as NORMAL (x1) and FAR (x2) do; the other two settings run
exactly as before. Three shapes, one branch each:

- `cmp edx, 3; je <level store>; cmp edx, 2; jne; mov eax, edx`, e.g. the cloth's
  `uSimSoftBody::getTargetLODLevel`: `83 FA 03 74 33 83 FA 02 75 02 8B C2` at `0x0083D24A` (also
  `0x00C6E333`, `0x00FA77B6`, and `uModel::drawModel`'s `83 FA 03 74 33` at `0x00FFC519`). The je's
  displacement becomes 5, onto `mov eax, edx`.
- `cmp eax, 3; jne; mov [esp+14h], 1; jmp; cmp eax, 2; jne; mov [esp+m], eax`:
  `83 F8 03 75 0A C7 44 24 14 01` at `0x00845969` (also `0x00B898B9`, `0x00F1FE3F`). The FARTHEST
  block starts with `jmp +0Dh` onto the store.
- `cmp eax, 3; jne; mov [esp+l], ebx; jmp; ...`: `83 F8 03 75 06 89 5C 24 14` at `0x00EA5440` (also
  `0x00F6196F`, `0x00FA6048`). The block starts with `jmp +09h`.

Every run is compared before anything is written, and one difference leaves all ten alone. The
harness (`test/run_tests.py`, profile `farlod`) checks that each branch lands on its store and runs the
cloth's choice at FARTHEST in the game's own code: x3 for scenery and for an enemy. Scenery that
`lod_tuner` rescales still stays whole until it is about `PopPixels` tall; characters switch at three
times their vanilla distances (their pair also sets cloth detail). The default stays `Farthest = high`,
the game's own; what `lod` does to the frame rate and the look in game: UNKNOWN.

### Settings (`lod_tuner.ini`, next to the `.asi`)

| Key | Default | Meaning |
|---|---|---|
| `Enabled` | 1 | 0 = load, change nothing |
| `PopPixels` | 24 | scenery keeps detail until about this tall on screen. Lower = fewer visible switches, more drawing. 0 = off |
| `Scale` | auto | minimum scenery multiplier. auto = screen height / 720 |
| `Characters` | 1.0 | multiplier for everything that is not scenery (1 to 16) |
| `ScreenHeight`, `FieldOfView` | auto | auto = config.ini `Resolution`, and 40 degrees plus config.ini `CameraFov` |
| `MaxDistance` | 10000 | metres. No distance is pushed past it, and vanilla distances beyond it stay |

`riftstone\logs\lod_tuner.log` records the settings used, the ViewRange in effect, the first changes
with before and after values, and running counts every 2,000 models.

### Proof so far

`python native/plugins/lod_tuner/test/run_tests.py` maps a read-only copy of DDDA.exe into a harness
process. The plugin verifies and patches it. The harness then enters `rModel::load` at the site with
fake models (paths, joints, radii, LOD masks) and lets the real instruction stream run through the
thunk to the next game instruction. It checks three things: the distances; that the covered
instructions still load `ecx` from `[esp+0x2C]` and `edx` from `[esi+0x78]`; and that `eax`, `esi`,
`edi`, `ebx`, `ebp`, `esp` and `xmm3` survive. It then hands a scaled model to the game's own
`getTargetLODLevel` (the draw paths' arithmetic, ViewRange NORMAL, FAR and FARTHEST) and to
`cInstancingCulling::updateLODParam`. That is 60 checks over three profiles: the shipped ini against
a stand-in config.ini (2560x1440, FARTHEST), a flat multiplier with a low cap, and `Enabled=0`
(nothing patched, vanilla values reach the readers). A second copy of the plugin sees the patched
site and changes nothing. `tools\test_all.cmd` runs it.

**UNKNOWN until observed in game:** that lod_tuner itself patches the real process (the loader and
the `enemy_cap` plugin have been seen working in game, 2026-09-25); the frame rate at FAR/NORMAL + lod_tuner against FARTHEST;
how visible the remaining switches are at `PopPixels = 24`; and that `cResource::mPath` is already set
when `load` runs. The log's counts show that last one: if "without a path" counts every model, only
the joint rule is classifying.

### Install (writes to the game; the owner's yes first)

```bat
native\plugins\lod_tuner\build.cmd
riftstone loader install
riftstone loader plugin add native\plugins\lod_tuner\out\lod_tuner.asi
```

`plugin add` also copies `lod_tuner.ini` the first time and keeps the owner's copy on later updates.
Then set `ViewRange=FAR` (or `NORMAL`) in `%LOCALAPPDATA%\CAPCOM\DRAGONS DOGMA DARK ARISEN\config.ini`.
Removal: `riftstone loader plugin remove lod_tuner.asi`.

## Not covered (leads)

- **Grass:** `sGrass` (`[0x018D2004]`) has `mFadeBeginDistance`/`mFadeEndDistance` (PC `+0x54`/`+0x58`,
  with `mForcedFade` at `+0x50`; the PS3 build's offsets are 4 higher), `mLODBeginDistance`/`mLODEndDistance`
  and `mMoreSmoothFade`. `aStage::init` (`0x005038DB`) forces the fade and sets the band to 1700 / 7000 cm
  times 1, 2 or 3 by ViewRange; `draw_distance`'s `Grass` sets that multiplier at every ViewRange (at
  most 3, FARTHEST's). A band beyond FARTHEST's, or a softer edge (`mMoreSmoothFade`), is still open.
  Config.ini `GrassQuality` is the game's own knob.
- **Shadows:** the sun's shadow distance is `uSkyStableShadow`'s `mShadowViewDistanceEx` (`+0x190`),
  set per stage by its sky scheduler (6000 cm on stage 100); local lights fade and cut off at `sShadow`
  `+0xED0`/`+0xED4` (`docs/re-shadows.md`).
- **Enemies appearing:** not `uCharacterBase::mDrawDistance`: for an enemy that is the distance of
  full activity, and enemies are drawn at any distance once spawned (`docs/draw-distance.md`;
  `draw_distance`'s `Enemies` scales it). Per placement, the `.lot` `mDrawDistance` field (-1 = the class
  default) sets it too, and `riftstone` edits it today. The game's own layouts rarely do: 19 of 5,524
  enemy records and 73 of 1,850 NPC records in the 6,209 distinct layouts, none of the 20,148
  `cSetInfoInsModel` records. Where an enemy first appears is its spawn: the layouts, the group lists and
  the enemy pool (`docs/world-map.md`, `docs/re-enemy-cap.md`).
- **LOD fade for instancing:** `cInstancing::mUseLodFade` (`+0x26`) and the fade band in
  `cInstancingDynamic`'s culling (`0x011435B6`). Where it is off, turning it on would cross-fade
  vegetation instead of popping it.
